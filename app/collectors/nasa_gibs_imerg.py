"""Global observed satellite precipitation tiles (``GET /v1/rain/observed``): NASA GIBS GPM IMERG 30-minute average (ADR 0043).

**What is read.** NASA's Global Imagery Browse Services (GIBS) serves pre-generated 256 px Web Mercator PNG tiles of
GPM IMERG 30-minute precipitation rate (``IMERG_Precipitation_Rate_30min``, ``GoogleMapsCompatible_Level6``, zooms 0–6)
without authentication or API key:

* Frame discovery: a single ~380-byte OGC WMTS ``DescribeDomains`` XML request over the recent 30-hour window, cached
  for 10 minutes (``TTL_SECONDS = 600``).
* Tile relay: ``https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/IMERG_Precipitation_Rate_30min/default/{time}/GoogleMapsCompatible_Level6/{z}/{y}/{x}.png``
  (note WMTS ``TileRow/TileCol`` order is ``{y}/{x}``, mapped from standard XYZ ``/{z}/{x}/{y}.png`` exposed by this API).
  Tiles are 8-bit indexed PNGs with ``tRNS`` transparency (``No Data`` and ``< 0.1 mm/h`` are transparent; ~3.5–6.7 KB each).

**Safeguards & sobriety.**
* Strict stamp allowlist: only timestamps present in the current ``DescribeDomains`` frame list can be requested, preventing
  arbitrary historical proxying.
* Bounded in-memory LRU cache (``TILE_CACHE_ENTRIES = 600``, ~3–4 MB max RAM, zero disk) with in-flight request coalescing
  so concurrent clients viewing the same tile trigger only one upstream request.
* All upstream requests pass through ``BaseCollector._get`` (counted in ``calls_24h`` and bounded by a self-imposed ceiling
  in ``app/collectors/budget.py``).
* Disabled when ``VIGIE_OBSERVED_RAIN=0`` (default ON, keyless).

**Honest limitations.** GPM IMERG is a satellite precipitation rate estimate (~0.1° ≈ 10 km native resolution, updated every
30 minutes, typically 4 to 6 hours behind real time). It is not a ground weather radar and must not be used for safety.
"""

from __future__ import annotations

import asyncio
import os
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app import clock
from app.collectors.base import BaseCollector, Fetched, build_user_agent

PROVIDER_ID = "nasa_gibs_imerg"
SOURCE_MODEL = "nasa-gibs-imerg-30min"
SWITCH_ENV = "VIGIE_OBSERVED_RAIN"
_OFF_VALUES = frozenset({"0", "false", "no", "off"})

GIBS_BASE = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
LAYER = "IMERG_Precipitation_Rate_30min"
TILE_MATRIX_SET = "GoogleMapsCompatible_Level6"
DOMAINS_URL_TEMPLATE = f"{GIBS_BASE}/1.0.0/{LAYER}/default/{TILE_MATRIX_SET}/all/{{start}}--{{end}}.xml"
TILE_URL_TEMPLATE = f"{GIBS_BASE}/{LAYER}/default/{{time_iso}}/{TILE_MATRIX_SET}/{{z}}/{{y}}/{{x}}.png"
API_TILE_PATH = "/v1/rain/observed/tiles/{stamp}/{z}/{x}/{y}.png"

# Web Mercator bounds from GIBS WMTSCapabilities (WGS84BoundingBox of IMERG_Precipitation_Rate_30min, verified 2026-11-22).
BOUNDS: tuple[float, float, float, float] = (-180.0, -85.051129, 180.0, 85.051129)
MIN_ZOOM = 0
MAX_ZOOM = 6
INTERVAL_MINUTES = 30
RESOLUTION_KM = 10
TYPICAL_LATENCY_MINUTES = 300  # ~4 to 6 hours (GPM Early Run ~4h + GIBS ingestion; 6h11m observed on 2026-11-22)
FRAME_COUNT = 12  # up to 6 hours of 30-minute observed windows
DOMAINS_LOOKBACK_HOURS = 30
TTL_SECONDS = 600  # 10 min; IMERG updates every 30 min (~6 tiny XML calls/hour)
TILE_TIMEOUT_S = 10.0
TILE_CACHE_ENTRIES = 600  # ~3.5-6.7 KB each -> ~3-4 MB max RAM, 0 disk
MAX_TILE_BYTES = 200_000
MAX_DOMAINS_BYTES = 256_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

LICENCE = (
    "CC0 / U.S. Government public domain (NASA ESDIS Data Use Guidance: NASA-led mission data are CC0 "
    "unless noted otherwise; no implied endorsement; see SOURCES.md)"
)
NOTE = (
    "Global satellite precipitation rate estimate (NASA GPM IMERG 30-minute average, ~10 km native resolution, "
    "typically 4 to 6 hours behind real time). Green-to-red tones show rain rate and cyan-to-purple tones show "
    "snow rate (transparent below 0.1 mm/h or outside satellite coverage). Not a ground weather radar. Not for safety."
)

_DURATION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


@dataclass(frozen=True)
class ObservedLegendTick:
    """One labeled tick of NASA GIBS ``GPM_Precipitation_Rate.xml`` (v1.3, verified 2026-11-22)."""

    min_mm_h: float
    color: str
    alpha: float = 1.0


# Exact RGB ticks from https://gibs.earthdata.nasa.gov/colormaps/v1.3/GPM_Precipitation_Rate.xml (read 2026-11-22).
RAIN_LEGEND: tuple[ObservedLegendTick, ...] = (
    ObservedLegendTick(0.1, "#00764e", 1.0),
    ObservedLegendTick(0.2, "#009424", 1.0),
    ObservedLegendTick(0.5, "#45c000", 1.0),
    ObservedLegendTick(1.0, "#c3e400", 1.0),
    ObservedLegendTick(2.0, "#ffb006", 1.0),
    ObservedLegendTick(5.0, "#ff4730", 1.0),
    ObservedLegendTick(10.0, "#e70000", 1.0),
    ObservedLegendTick(20.0, "#9c0000", 1.0),
    ObservedLegendTick(53.0, "#330000", 1.0),
)

SNOW_LEGEND: tuple[ObservedLegendTick, ...] = (
    ObservedLegendTick(0.1, "#b1faee", 1.0),
    ObservedLegendTick(0.2, "#8febf1", 1.0),
    ObservedLegendTick(0.5, "#66abdc", 1.0),
    ObservedLegendTick(1.0, "#4f73c4", 1.0),
    ObservedLegendTick(2.0, "#4048d2", 1.0),
    ObservedLegendTick(5.0, "#5f2fe4", 1.0),
    ObservedLegendTick(10.0, "#770bd1", 1.0),
    ObservedLegendTick(20.0, "#770792", 1.0),
    ObservedLegendTick(53.0, "#3a0330", 1.0),
)


@dataclass(frozen=True)
class ObservedFrame:
    """One available 30-minute IMERG frame."""

    stamp: str  # YYYYMMDDHHMM (UTC)
    time_utc: int  # UTC epoch seconds
    time_at: str  # ISO 8601 UTC (YYYY-MM-DDTHH:MM:SSZ)


@dataclass(frozen=True)
class ObservedRainFrames:
    """Active observed frames returned by NASA GIBS ``DescribeDomains``, oldest first."""

    frames: tuple[ObservedFrame, ...]
    by_stamp: dict[str, ObservedFrame]

    @property
    def latest(self) -> ObservedFrame:
        return self.frames[-1]


def observed_rain_enabled() -> bool:
    """Whether ``GET /v1/rain/observed`` is enabled (default ON; ``VIGIE_OBSERVED_RAIN=0`` disables)."""
    raw = os.environ.get(SWITCH_ENV, "1").strip().lower()
    return raw not in _OFF_VALUES


def _parse_iso_instant(raw: str) -> datetime:
    """Parse a GIBS time instant (``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM:SSZ``) into a UTC datetime."""
    text = raw.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_iso_step(raw: str) -> timedelta:
    """Parse an ISO-8601 time step such as ``PT30M`` or ``PT1H``."""
    match = _DURATION_RE.match(raw.strip())
    if match is None:
        raise ValueError(f"nasa_gibs_imerg: unsupported time step {raw[:32]!r}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    step = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    if step <= timedelta(0):
        raise ValueError(f"nasa_gibs_imerg: non-positive time step {raw[:32]!r}")
    return step


def _expand_period(spec: str, max_steps: int = 200) -> list[datetime]:
    """Expand one GIBS ``<Domain>`` interval (``start/end/step`` or single instant) into UTC datetimes (newest-capped)."""
    token = spec.strip()
    if not token:
        return []
    parts = token.split("/")
    if len(parts) == 1:
        return [_parse_iso_instant(parts[0])]
    if len(parts) == 2:
        start, end = _parse_iso_instant(parts[0]), _parse_iso_instant(parts[1])
        step = timedelta(minutes=INTERVAL_MINUTES)
    elif len(parts) == 3:
        start, end, step = _parse_iso_instant(parts[0]), _parse_iso_instant(parts[1]), _parse_iso_step(parts[2])
    else:
        raise ValueError(f"nasa_gibs_imerg: invalid domain period {token[:60]!r}")
    if end < start:
        return []
    # Walk backwards from end so even a wide multi-day period never allocates thousands of timestamps.
    out: list[datetime] = []
    current = end
    while current >= start and len(out) < max_steps:
        out.append(current)
        current -= step
    out.reverse()
    return out


def parse_domains_xml(payload: bytes, max_frames: int = FRAME_COUNT) -> ObservedRainFrames:
    """Parse a NASA GIBS WMTS ``DescribeDomains`` XML response into the latest ``max_frames`` observed frames."""
    if not payload or len(payload) > MAX_DOMAINS_BYTES:
        raise ValueError("nasa_gibs_imerg: Domains XML is empty or too large")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"nasa_gibs_imerg: malformed Domains XML: {exc}") from exc

    domain_texts: list[str] = []
    for dim in root.iter():
        if not dim.tag.endswith("DimensionDomain"):
            continue
        ident: Optional[str] = None
        domain_val: Optional[str] = None
        for child in dim:
            if child.tag.endswith("Identifier") and child.text:
                ident = child.text.strip().lower()
            elif child.tag.endswith("Domain") and child.text:
                domain_val = child.text.strip()
        if ident == "time" and domain_val:
            domain_texts.append(domain_val)

    instants: set[datetime] = set()
    for text in domain_texts:
        for part in text.split(","):
            instants.update(_expand_period(part, max_steps=max(max_frames * 2, 48)))

    if not instants:
        raise ValueError("nasa_gibs_imerg: no observed frames in Domains response")

    ordered = sorted(instants)[-max_frames:]
    frames = tuple(
        ObservedFrame(
            stamp=dt.strftime("%Y%m%d%H%M"),
            time_utc=int(dt.timestamp()),
            time_at=dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        for dt in ordered
    )
    return ObservedRainFrames(frames=frames, by_stamp={f.stamp: f for f in frames})


class NasaGibsImergCollector(BaseCollector[ObservedRainFrames]):
    """Discovers recent NASA GIBS IMERG 30-minute frames and relays their Web Mercator PNG tiles with bounded LRU caching."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id=PROVIDER_ID,
            name="NASA GIBS GPM IMERG (observed rain)",
            domain="weather",
            ttl_seconds=TTL_SECONDS,
            failure_threshold=3,
            recovery_timeout_seconds=120,
            timeout_seconds=10.0,
            http_client=http_client,
        )
        self._tiles: OrderedDict[tuple[str, int, int, int], bytes] = OrderedDict()
        self._tiles_inflight: dict[tuple[str, int, int, int], asyncio.Task[Optional[bytes]]] = {}
        self._tile_client: Optional[httpx.AsyncClient] = None

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> ObservedRainFrames:
        kwargs.pop("previous", None)
        now_dt = datetime.fromtimestamp(clock.wall(), tz=timezone.utc)
        start = (now_dt - timedelta(hours=DOMAINS_LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:00:00Z")
        end = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        url = DOMAINS_URL_TEMPLATE.format(start=start, end=end)
        response = await self._get(client, url, headers={"Accept": "application/xml, text/xml"})
        response.raise_for_status()
        return parse_domains_xml(response.content)

    async def frames(self) -> Optional[Fetched[ObservedRainFrames]]:
        """Return the active observed frames (stale cache allowed on temporary GIBS error), or ``None`` if unavailable."""
        try:
            return await self.fetch_ex("global", allow_stale_on_error=True)
        except Exception:
            return None

    async def tile(self, frame: ObservedFrame, z: int, x: int, y: int) -> Optional[bytes]:
        """Relay one 256 px PNG tile for an allowlisted ``frame`` (standard XYZ ``z/x/y`` mapped to GIBS WMTS ``z/y/x``).

        Returns ``None`` if NASA GIBS answers 404; raises on network/budget/format error.
        """
        key = (frame.stamp, z, x, y)
        cached = self._tiles.get(key)
        if cached is not None:
            self._tiles.move_to_end(key)
            return cached
        task = self._tiles_inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch_tile(frame, key))
            self._tiles_inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._tiles_inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _fetch_tile(self, frame: ObservedFrame, key: tuple[str, int, int, int]) -> Optional[bytes]:
        _, z, x, y = key
        client = self._external_client
        if client is None:
            if self._tile_client is None:
                self._tile_client = httpx.AsyncClient(
                    timeout=TILE_TIMEOUT_S,
                    headers={"User-Agent": build_user_agent(), "Accept": "image/png"},
                    follow_redirects=False,
                )
            client = self._tile_client
        # WMTS REST template order is {TileMatrix}/{TileRow}/{TileCol}.png == {z}/{y}/{x}.png
        url = TILE_URL_TEMPLATE.format(time_iso=frame.time_at, z=z, y=y, x=x)
        response = await self._get(client, url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.content
        if not body.startswith(PNG_SIGNATURE) or len(body) > MAX_TILE_BYTES:
            raise ValueError("nasa_gibs_imerg: upstream did not return a valid PNG tile")
        self._tiles[key] = body
        while len(self._tiles) > TILE_CACHE_ENTRIES:
            self._tiles.popitem(last=False)
        return body

    async def aclose(self) -> None:
        if self._tile_client is not None:
            await self._tile_client.aclose()
            self._tile_client = None
