"""Météo-France radar mosaics (rain actually measured: metropolitan France, the Antilles, La Réunion and New Caledonia), relayed from a vigie-meteo gateway (optional).

Météo-France's ``DPRadar`` publishes one mosaic of the radars of metropolitan France every 5 minutes (the ``LAME_D_EAU`` product:
the rain accumulated over the 5 minutes before the date of the image, 500 m pixels, a file of HDF5 in a polar stereographic grid).
This API does not read it itself: the gateway (``Axellum/vigie-meteo``, ``GET /v1/radar/frames`` and
``GET /v1/radar/tiles/{epoch}/{z}/{x}/{y}.png``, read on 2026-11-18) downloads each image when it is published, keeps the last three
hours and draws Web Mercator PNG tiles on demand. This collector reads the list of its images (a few hundred bytes, cached two
minutes) and relays its tiles: a tile of an image the gateway no longer lists is refused, so a client cannot make this API ask the
gateway for anything else.

The three overseas mosaics (``ANTILLES``, ``REUNION``, ``NOUVELLE-CALEDONIE``, ADR 0037) are the same product on their own grids; the gateway
serves each under ``/v1/radar/{area}/...`` (the metropolitan one keeps its first routes, ``/v1/radar/frames`` and ``/v1/radar/tiles/...``)
and this collector asks for each area on its own, with its own list of images and its own tiles in memory.

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

from app import clock
from app.collectors.base import SELF_HOSTED_CONNECTION, BaseCollector, build_user_agent, parse_iso_utc

PROVIDER_ID = "meteofrance_radar"
SOURCE_MODEL = "meteofrance-lame-d-eau"
NOWCAST_SOURCE_MODEL = "meteofrance-piaf"
TTL_SECONDS = 120  # an image every 5 minutes, published about 2 minutes late
TILE_TIMEOUT_S = 8.0
# A new connection to the gateway answers in about 0.1 s; one left without an answer (2 in 128 from the container of this API,
# 0 from its host, measured on 2026-11-24) is given up after 2 s and tried once more, rather than waiting the 8 s of a tile.
TILE_CONNECT_TIMEOUT_S = 2.0
TILE_CONNECT_RETRIES = 1
# The gateway keeps an idle connection 75 s (vigie-meteo#21): kept 30 s here, fewer new connections, and always closed by this side first.
TILE_KEEPALIVE_S = 30.0
TILE_CACHE_ENTRIES = 600  # about 5 kB each at most
MAX_TILE_BYTES = 200_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# What the gateway tiles show: a Web Mercator slippy map of 256 px tiles, zoom 3 to 10 (read from the gateway's answer).
TILE_PATH = "/v1/radar/france/tiles/{epoch}/{z}/{x}/{y}.png"
NOWCAST_TILE_PATH = "/v1/radar/france/nowcast/tiles/{epoch}/{z}/{x}/{y}.png"
NOWCAST_KEY = "nowcast"
MAX_LEGEND_CLASSES = 20

FRANCE = "france"
# The areas of the radar, by the name this API and the gateway give them in an address, with the box of their grid (west, south, east,
# north; the boxes of the files of 2026-11-21, widened by at most 0.02 degree): a point inside one is given that radar. The box is not
# the range of the radars (a point of the box may be beyond them: its tiles are transparent there, as in France).
AREA_BOXES: dict[str, tuple[float, float, float, float]] = {
    "antilles": (-66.0, 10.59, -57.0, 21.0),
    "reunion": (51.0, -25.02, 62.02, -16.0),
    "new-caledonia": (159.7, -27.27, 171.58, -16.2),
}
AREAS: tuple[str, ...] = (FRANCE, *AREA_BOXES)


def tile_path(area: str = FRANCE) -> str:
    """The path of the tiles of an area, relative to the root of this API."""
    return f"/v1/radar/{area}/tiles/{{epoch}}/{{z}}/{{x}}/{{y}}.png"


def overseas_area_of(lat: float, lon: float) -> Optional[str]:
    """The overseas radar area whose grid holds a point (``antilles``, ``reunion``, ``new-caledonia``), else ``None``."""
    for area, (west, south, east, north) in AREA_BOXES.items():
        if west <= lon <= east and south <= lat <= north:
            return area
    return None



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


@dataclass(frozen=True)
class NowcastFrame:
    """One 5-minute forecast frame of a PIAF nowcast run."""

    epoch: int
    lead_minutes: int


@dataclass(frozen=True)
class NowcastFrames:
    """The PIAF +1h forecast frames the gateway serves, earliest lead first."""

    run_utc: int
    frames: tuple[NowcastFrame, ...]
    bounds: tuple[float, float, float, float]  # west, south, east, north (degrees)
    min_zoom: int
    max_zoom: int
    step_seconds: int
    legend: tuple[LegendClass, ...]

    @property
    def epochs(self) -> tuple[int, ...]:
        return tuple(f.epoch for f in self.frames)


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"radar frames: {what} is not a number")
    return float(value)


def _parse_bounds_and_zoom(payload: dict[str, Any], label: str) -> tuple[tuple[float, float, float, float], int, int, tuple[LegendClass, ...]]:
    bounds = payload.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError(f"{label}: the bounds are not four numbers")
    west, south, east, north = (_number(v, "a bound") for v in bounds)
    if not (-180.0 <= west < east <= 180.0 and -90.0 <= south < north <= 90.0):
        raise ValueError(f"{label}: the bounds are not a box")
    min_zoom, max_zoom = payload.get("min_zoom"), payload.get("max_zoom")
    if (
        not isinstance(min_zoom, int)
        or not isinstance(max_zoom, int)
        or isinstance(min_zoom, bool)
        or isinstance(max_zoom, bool)
        or not 0 <= min_zoom <= max_zoom <= 22
    ):
        raise ValueError(f"{label}: the zoom range is not valid")
    legend: list[LegendClass] = []
    raw_legend = payload.get("legend")
    if isinstance(raw_legend, list):
        for entry in raw_legend[:MAX_LEGEND_CLASSES]:
            if not isinstance(entry, dict) or not isinstance(entry.get("color"), str) or len(entry["color"]) != 7:
                raise ValueError(f"{label}: a legend class cannot be read")
            legend.append(
                LegendClass(_number(entry.get("min_mm_h"), "a legend threshold"), entry["color"], _number(entry.get("alpha"), "an alpha"))
            )
    return (west, south, east, north), min_zoom, max_zoom, tuple(legend)


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
    bounds, min_zoom, max_zoom, legend = _parse_bounds_and_zoom(payload, "radar frames")
    return RadarFrames(
        epochs=tuple(sorted(set(epochs))),
        bounds=bounds,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        legend=legend,
    )


def parse_nowcast_frames(payload: Any) -> NowcastFrames:
    """Parse ``GET /v1/rain-nowcast/frames`` from the vigie-meteo gateway; ``ValueError`` when invalid."""
    if not isinstance(payload, dict):
        raise ValueError("nowcast frames: the answer is not an object")
    if payload.get("source_model") != NOWCAST_SOURCE_MODEL:
        raise ValueError(f"nowcast frames: unexpected source_model {str(payload.get('source_model'))[:40]!r}")
    raw_run = payload.get("run_utc")
    if isinstance(raw_run, int) and not isinstance(raw_run, bool) and raw_run > 0:
        run_utc = raw_run
    else:
        parsed_run = parse_iso_utc(raw_run)
        if parsed_run is None or parsed_run <= 0:
            raise ValueError("nowcast frames: missing or invalid run_utc")
        run_utc = parsed_run
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise ValueError("nowcast frames: no frame")
    by_epoch: dict[int, NowcastFrame] = {}
    for item in raw_frames:
        if not isinstance(item, dict):
            raise ValueError("nowcast frames: a frame is not an object")
        epoch = item.get("epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
            raise ValueError("nowcast frames: a frame without a valid epoch")
        lead = item.get("lead_minutes")
        if isinstance(lead, bool) or not isinstance(lead, int) or lead <= 0:
            raise ValueError("nowcast frames: a frame without a valid lead_minutes")
        by_epoch[epoch] = NowcastFrame(epoch=epoch, lead_minutes=lead)
    raw_step = payload.get("step_seconds", 300)
    if isinstance(raw_step, bool) or not isinstance(raw_step, int) or raw_step <= 0:
        raise ValueError("nowcast frames: step_seconds is not a positive integer")
    bounds, min_zoom, max_zoom, legend = _parse_bounds_and_zoom(payload, "nowcast frames")
    ordered = tuple(by_epoch[e] for e in sorted(by_epoch))
    return NowcastFrames(
        run_utc=run_utc,
        frames=ordered,
        bounds=bounds,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        step_seconds=raw_step,
        legend=legend,
    )


def tile_touches(frames: RadarFrames | NowcastFrames, z: int, x: int, y: int) -> bool:
    """Whether the Web Mercator tile ``z/x/y`` overlaps the box of the radar/nowcast (else its tile is transparent, no request needed)."""
    n = 2**z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0

    def lat(row: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * row / n))))

    north, south = lat(y), lat(y + 1)
    return west < frames.bounds[2] and east > frames.bounds[0] and south < frames.bounds[3] and north > frames.bounds[1]


class MeteoFranceRadarCollector(BaseCollector[RadarFrames]):
    """The list of the radar images (and PIAF +1h nowcast frames) of the gateway, and the relay of their tiles."""

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
            connection=SELF_HOSTED_CONNECTION,
        )
        self.base_url = base_url.rstrip("/")
        self._tiles: OrderedDict[tuple[str, int, int, int, int], bytes] = OrderedDict()
        self._tiles_inflight: dict[tuple[str, int, int, int, int], asyncio.Task[tuple[Optional[bytes], bool]]] = {}
        self._tile_client: Optional[httpx.AsyncClient] = None
        self._nowcast_cache: Optional[tuple[float, NowcastFrames]] = None
        self._nowcast_inflight: Optional[asyncio.Task[Optional[NowcastFrames]]] = None

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> RadarFrames:
        kwargs.pop("previous", None)
        area = kwargs.pop("area", FRANCE)
        path = "/v1/radar/frames" if area == FRANCE else f"/v1/radar/{area}/frames"
        response = await self._get(client, f"{self.base_url}{path}")
        response.raise_for_status()
        return parse_frames(response.json())

    async def frames(self, area: str = FRANCE) -> Optional[RadarFrames]:
        """The images the gateway lists for an area, or ``None`` when it does not answer or lists none (an expired list is not served:
        a tile of an image the gateway has purged would be a 404)."""
        if area not in AREAS:
            return None
        try:
            return await self.fetch("global" if area == FRANCE else f"area:{area}", allow_stale_on_error=False, area=area)
        except Exception:
            return None

    async def nowcast_frames(self) -> Optional[NowcastFrames]:
        """The PIAF +1h forecast frames the gateway lists, or ``None`` when unavailable (expired lists are not served)."""
        now = clock.monotonic()
        if self._nowcast_cache is not None and now < self._nowcast_cache[0]:
            return self._nowcast_cache[1]
        task = self._nowcast_inflight
        if task is None:
            task = asyncio.ensure_future(self._fetch_nowcast_frames())
            self._nowcast_inflight = task

            def _clear(_t: asyncio.Task[Optional[NowcastFrames]]) -> None:
                if self._nowcast_inflight is _t:
                    self._nowcast_inflight = None

            task.add_done_callback(_clear)
        return await asyncio.shield(task)

    async def _fetch_nowcast_frames(self) -> Optional[NowcastFrames]:
        try:
            response = await self._get(self._http_client(), f"{self.base_url}/v1/rain-nowcast/frames", follow_redirects=False)
            response.raise_for_status()
            parsed = parse_nowcast_frames(response.json())
        except Exception:
            return None
        self._nowcast_cache = (clock.monotonic() + TTL_SECONDS, parsed)
        return parsed

    async def tile(self, epoch: int, z: int, x: int, y: int, area: str = FRANCE) -> Optional[bytes]:
        """The PNG of a tile of the image of an area ending at ``epoch``: ``None`` when the gateway has no such image; raises when it fails."""
        body, _ = await self._tile((area, epoch, z, x, y))
        return body

    async def nowcast_tile(self, epoch: int, z: int, x: int, y: int, run: int) -> tuple[Optional[bytes], bool]:
        """A PIAF +1h forecast tile ending at ``epoch``, asked of the gateway for the run ``run`` (the run of the frames this API lists),
        and whether the gateway drew it with that run.

        A new run every 5 minutes draws the same times again, so a tile is kept here under its run, and only when the gateway drew it with
        that run; a tile of another run (the gateway has the next one already, or its other process is a run behind) is relayed and not
        kept. ``None`` when the gateway has no such frame; raises on error.
        """
        return await self._tile((f"{NOWCAST_KEY}:{run}", epoch, z, x, y))

    async def _tile(self, key: tuple[str, int, int, int, int]) -> tuple[Optional[bytes], bool]:
        cached = self._tiles.get(key)
        if cached is not None:
            self._tiles.move_to_end(key)
            return cached, True
        task = self._tiles_inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch_tile(key))
            self._tiles_inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._tiles_inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _fetch_tile(self, key: tuple[str, int, int, int, int]) -> tuple[Optional[bytes], bool]:
        """The tile of ``key`` from the gateway, and whether it is the one asked for (always, except a PIAF tile of another run)."""
        area, epoch, z, x, y = key
        client = self._tile_http_client()
        run: Optional[str] = None
        if area.startswith(NOWCAST_KEY + ":"):
            run = area.split(":", 1)[1]
            path = f"/v1/rain-nowcast/tiles/{epoch}/{z}/{x}/{y}.png"
        elif area == FRANCE:
            path = f"/v1/radar/tiles/{epoch}/{z}/{x}/{y}.png"
        else:
            path = f"/v1/radar/{area}/tiles/{epoch}/{z}/{x}/{y}.png"
        url = f"{self.base_url}{path}"
        params = None if run is None else {"run": run}
        # A kept connection the gateway closed just as the request left is asked once more by ``_get``.
        response = await self._get(client, url, params=params)
        if response.status_code == 404:
            return None, False
        response.raise_for_status()
        body = response.content
        if not body.startswith(PNG_SIGNATURE) or len(body) > MAX_TILE_BYTES:
            raise ValueError("radar tile: the gateway did not answer a PNG of a reasonable size")
        # The gateway names the run that drew a PIAF tile (`X-Piaf-Run`); an older gateway names none: not that run's for sure.
        exact = run is None or response.headers.get("x-piaf-run") == run
        if exact:
            self._tiles[key] = body
            while len(self._tiles) > TILE_CACHE_ENTRIES:
                self._tiles.popitem(last=False)
        return body, exact

    def _tile_http_client(self) -> httpx.AsyncClient:
        """The client of the tiles (one for the life of the collector, so its connections to the gateway are kept and reused)."""
        if self._external_client is not None:
            return self._external_client
        if self._tile_client is None:
            self._tile_client = httpx.AsyncClient(
                timeout=httpx.Timeout(TILE_TIMEOUT_S, connect=TILE_CONNECT_TIMEOUT_S),
                # The limits go to the transport: a client given a transport ignores its own. Default numbers, longer keep-alive.
                transport=httpx.AsyncHTTPTransport(
                    retries=TILE_CONNECT_RETRIES,
                    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=TILE_KEEPALIVE_S),
                ),
                headers={"User-Agent": build_user_agent(), "Accept": "image/png"},
                follow_redirects=False,
            )
        return self._tile_client

    async def aclose(self) -> None:
        if self._tile_client is not None:
            await self._tile_client.aclose()
            self._tile_client = None
        await super().aclose()
