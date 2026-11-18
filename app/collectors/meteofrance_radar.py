"""Météo-France radar mosaic of metropolitan France (rain actually measured), relayed from a vigie-meteo gateway (optional).

Météo-France's ``DPRadar`` publishes one mosaic of the radars of metropolitan France every 5 minutes (the ``LAME_D_EAU`` product:
the rain accumulated over the 5 minutes before the date of the image, 500 m pixels, a file of HDF5 in a polar stereographic grid).
This API does not read it itself: the gateway (``Axellum/vigie-meteo``, ``GET /v1/radar/frames`` and
``GET /v1/radar/tiles/{epoch}/{z}/{x}/{y}.png``, read on 2026-11-18) downloads each image when it is published, keeps the last three
hours and draws Web Mercator PNG tiles on demand. This collector reads the list of its images (a few hundred bytes, cached two
minutes) and relays its tiles: a tile of an image the gateway no longer lists is refused, so a client cannot make this API ask the
gateway for anything else.

Nothing is drawn or invented here: the tiles are the gateway's, the credit is Météo-France's. The image that ends at a given time is
published about two minutes after it; a tile is immutable (its address holds the date), so it is cached for a day by the client and
for a few minutes in memory here, where a crowd asking for the same new tile at the same moment makes one request to the gateway.

Without ``VIGIE_METEO_GATEWAY_URL`` there is no collector and the radar fields stay unknown (or RainViewer's, on a private instance).
"""

from __future__ import annotations

import asyncio
import math
import struct
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector, build_user_agent

PROVIDER_ID = "meteofrance_radar"
SOURCE_MODEL = "meteofrance-lame-d-eau"
TTL_SECONDS = 120  # an image every 5 minutes, published about 2 minutes late
TILE_TIMEOUT_S = 8.0
TILE_CACHE_ENTRIES = 600  # about 5 kB each at most
MAX_TILE_BYTES = 200_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# What the gateway tiles show: a Web Mercator slippy map of 256 px tiles, zoom 3 to 10 (read from the gateway's answer).
TILE_PATH = "/v1/radar/france/tiles/{epoch}/{z}/{x}/{y}.png"
MAX_LEGEND_CLASSES = 20



def _transparent_png() -> bytes:
    """A 1x1 fully transparent PNG: the answer for a tile that does not touch the radar's area (no need to ask the gateway)."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(5)))  # one row: the filter byte and one RGBA pixel, all zero
        + chunk(b"IEND", b"")
    )


TRANSPARENT_PNG = _transparent_png()


@dataclass(frozen=True)
class LegendClass:
    """One intensity class of the tiles: it starts at ``min_mm_h`` and runs to the start of the next one."""

    min_mm_h: float
    color: str
    alpha: float


@dataclass(frozen=True)
class RadarFrames:
    """The images the gateway keeps, oldest first (``epochs`` = the end of the 5 minutes of rain, UTC seconds)."""

    epochs: tuple[int, ...]
    bounds: tuple[float, float, float, float]  # west, south, east, north (degrees)
    min_zoom: int
    max_zoom: int
    legend: tuple[LegendClass, ...]


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"radar frames: {what} is not a number")
    return float(value)


def parse_frames(payload: Any) -> RadarFrames:
    """The images of a gateway answer. ``ValueError`` when the answer is not the radar of Météo-France or cannot be read."""
    if not isinstance(payload, dict):
        raise ValueError("radar frames: the answer is not an object")
    if payload.get("source_model") != SOURCE_MODEL:
        raise ValueError(f"radar frames: unexpected source_model {str(payload.get('source_model'))[:40]!r}")
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise ValueError("radar frames: no frame")
    epochs: list[int] = []
    for item in raw_frames:
        if not isinstance(item, dict):
            raise ValueError("radar frames: a frame is not an object")
        epoch = item.get("epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
            raise ValueError("radar frames: a frame without a valid epoch")
        epochs.append(epoch)
    bounds = payload.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError("radar frames: the bounds are not four numbers")
    west, south, east, north = (_number(v, "a bound") for v in bounds)
    if not (-180.0 <= west < east <= 180.0 and -90.0 <= south < north <= 90.0):
        raise ValueError("radar frames: the bounds are not a box")
    min_zoom, max_zoom = payload.get("min_zoom"), payload.get("max_zoom")
    if (
        not isinstance(min_zoom, int)
        or not isinstance(max_zoom, int)
        or isinstance(min_zoom, bool)
        or isinstance(max_zoom, bool)
        or not 0 <= min_zoom <= max_zoom <= 22
    ):
        raise ValueError("radar frames: the zoom range is not valid")
    legend: list[LegendClass] = []
    raw_legend = payload.get("legend")
    if isinstance(raw_legend, list):
        for entry in raw_legend[:MAX_LEGEND_CLASSES]:
            if not isinstance(entry, dict) or not isinstance(entry.get("color"), str) or len(entry["color"]) != 7:
                raise ValueError("radar frames: a legend class cannot be read")
            legend.append(
                LegendClass(_number(entry.get("min_mm_h"), "a legend threshold"), entry["color"], _number(entry.get("alpha"), "an alpha"))
            )
    return RadarFrames(
        epochs=tuple(sorted(set(epochs))),
        bounds=(west, south, east, north),
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        legend=tuple(legend),
    )


def tile_touches(frames: RadarFrames, z: int, x: int, y: int) -> bool:
    """Whether the Web Mercator tile ``z/x/y`` overlaps the box of the radar (else its tile is transparent, no request needed)."""
    n = 2**z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0

    def lat(row: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * row / n))))

    north, south = lat(y), lat(y + 1)
    return west < frames.bounds[2] and east > frames.bounds[0] and south < frames.bounds[3] and north > frames.bounds[1]


class MeteoFranceRadarCollector(BaseCollector[RadarFrames]):
    """The list of the radar images of the gateway, and the relay of their tiles."""

    def __init__(self, base_url: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id=PROVIDER_ID,
            name="Météo-France radar mosaic (vigie-meteo gateway)",
            domain="weather",
            ttl_seconds=TTL_SECONDS,
            failure_threshold=3,
            recovery_timeout_seconds=120,
            timeout_seconds=8.0,
            http_client=http_client,
        )
        self.base_url = base_url.rstrip("/")
        self._tiles: OrderedDict[tuple[int, int, int, int], bytes] = OrderedDict()
        self._tiles_inflight: dict[tuple[int, int, int, int], asyncio.Task[Optional[bytes]]] = {}
        self._tile_client: Optional[httpx.AsyncClient] = None

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> RadarFrames:
        kwargs.pop("previous", None)
        response = await self._get(client, f"{self.base_url}/v1/radar/frames")
        response.raise_for_status()
        return parse_frames(response.json())

    async def frames(self) -> Optional[RadarFrames]:
        """The images the gateway lists, or ``None`` when it does not answer or lists none (an expired list is not served: a tile
        of an image the gateway has purged would be a 404)."""
        try:
            return await self.fetch("global", allow_stale_on_error=False)
        except Exception:
            return None

    async def tile(self, epoch: int, z: int, x: int, y: int) -> Optional[bytes]:
        """The PNG of a tile of the image ending at ``epoch``: ``None`` when the gateway has no such image; raises when it fails."""
        key = (epoch, z, x, y)
        cached = self._tiles.get(key)
        if cached is not None:
            self._tiles.move_to_end(key)
            return cached
        task = self._tiles_inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch_tile(key))
            self._tiles_inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._tiles_inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _fetch_tile(self, key: tuple[int, int, int, int]) -> Optional[bytes]:
        epoch, z, x, y = key
        client = self._external_client
        if client is None:
            if self._tile_client is None:
                self._tile_client = httpx.AsyncClient(
                    timeout=TILE_TIMEOUT_S, headers={"User-Agent": build_user_agent(), "Accept": "image/png"}, follow_redirects=False
                )
            client = self._tile_client
        response = await self._get(client, f"{self.base_url}/v1/radar/tiles/{epoch}/{z}/{x}/{y}.png")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.content
        if not body.startswith(PNG_SIGNATURE) or len(body) > MAX_TILE_BYTES:
            raise ValueError("radar tile: the gateway did not answer a PNG of a reasonable size")
        self._tiles[key] = body
        while len(self._tiles) > TILE_CACHE_ENTRIES:
            self._tiles.popitem(last=False)
        return body

    async def aclose(self) -> None:
        if self._tile_client is not None:
            await self._tile_client.aclose()
            self._tile_client = None
