"""Forecast precipitation map overlays (``GET /v1/rain/field``): AROME and ARPEGE 1-hour totals from Météo-France WCS.

**What is read.** Total 1-hour accumulated precipitation at the ground or water surface (``TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___<run>_PT1H``,
GRIB2 ``shortName="tp"``, ``units="kg m**-2"`` = mm over 1 hour = mm/h, ``stepType="accum"`` with ``endStep - startStep == 1``)
from the WCS of Météo-France's API portal (``/public/arome/1.0`` and ``/public/arpege/1.0``, key in the ``apikey`` header only).
Because ``_PT1H`` already provides exact 1-hour accumulations on all three services (measured on 2026-11-22), no step subtraction
is needed: one request yields one forecast hour.

Three grids, from widest to finest (measured on 2026-11-22):

=========  =========================================  ==============================  ================  =================
level      service                                    area, step                      GRIB2 (1 hour)    RGBA PNG (thin=2)
=========  =========================================  ==============================  ================  =================
``world``  ``MF-NWP-GLOBAL-ARPEGE-025-GLOBE-WCS``     the globe, 0.25 (1440 x 721)    2.0 MB, ~0.3 s    ~23 KB (721 x 360)
``europe`` ``MF-NWP-GLOBAL-ARPEGE-01-EUROPE-WCS``     32 W-42 E, 20-72 N, 0.1         0.75 MB, ~0.15 s  ~9 KB  (371 x 260)
``france`` ``MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS``   12 W-16 E, 37.5-55.4 N, 0.025   1.4 MB, ~0.2 s    ~15 KB (561 x 358)
=========  =========================================  ==============================  ================  =================

**Lead steps and sobriety.** Six forecast lead times (``+1h, +2h, +3h, +6h, +12h, +24h`` from the nearest UTC hour) are fetched
once an hour across the three levels (at most 18 requests per hour, fewer when unchanged `(level, valid, run)` frames are reused
from memory; well below the portal's 50 req/min limit). All PNGs live in memory (zero extra disk).

**Projection and rendering.** Each grid is rendered to a lightweight 8-bit RGBA PNG whose rows are linearly spaced in Web Mercator
Y (clamped to ±85° latitude for the global grid, wrapping longitude cleanly from -180° to +180°), so a Web Mercator map client
(MapLibre ``type: "image"`` source) places the overlay without latitude distortion. Pixels below 0.1 mm/h or outside the model
domain (AROME bitmap corners) are fully transparent; wet pixels use the exact same 9-class intensity palette as Vigie's radar
mosaics.

Licence: Licence Ouverte 2.0 (Etalab), as the licence documents of the AROME and ARPEGE APIs on the portal say (read on
2026-11-22): reuse, commercial included, with the source and the date of the run named and the data not altered (see ADR 0041).
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import struct
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import numpy as np

from app import clock
from app.collectors.base import BaseCollector, Collected, Fetched
from app.collectors.grib import DECODE_LOCK, split_messages
from app.collectors.meteofrance_wind_field import (
    FORMAT,
    WCS_HOST,
    _roll_to_dateline,
    image_stamp,
    nearest_hour,
    run_stamp,
    seconds_to_next_hour_change,
)

KEY_ENV = "VIGIE_METEOFRANCE_RAIN_API_KEY"
FALLBACK_KEY_ENV = "VIGIE_METEOFRANCE_API_KEY"
SWITCH_ENV = "VIGIE_RAIN_FIELD"
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9._\-+/=]{16,}$")

COVERAGE = "TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___{run}_PT1H"
MAX_BYTES = 8_000_000  # one world GRIB2 is ~2.0 MB
RUNS_TRIED = 3
MISSING_RETRY_S = 15 * 60
KEEP_OLD_S = 3 * 3600
RETRY_INCOMPLETE_S = 600
IMAGES_KEPT = 72  # up to 4 hours x 3 levels x 6 frames
MAX_CONCURRENT_REQUESTS = 4
MERCATOR_MAX_LAT = 85.0
LEAD_HOURS: tuple[int, ...] = (1, 2, 3, 6, 12, 24)

NOTE = "A forecast, not a radar measure. One-hour model accumulation (mm/h) from AROME and ARPEGE. Not for safety."
LICENCE = "Licence Ouverte 2.0 (Etalab): name Météo-France and the run, do not alter the data"


@dataclass(frozen=True)
class RainLegendClass:
    """One precipitation intensity class (shared palette with Météo-France radar & PIAF)."""

    min_mm_h: float
    color: str
    alpha: float

    @property
    def rgba(self) -> tuple[int, int, int, int]:
        hex_code = self.color.lstrip("#")
        return (
            int(hex_code[0:2], 16),
            int(hex_code[2:4], 16),
            int(hex_code[4:6], 16),
            round(self.alpha * 255),
        )


RAIN_LEGEND: tuple[RainLegendClass, ...] = (
    RainLegendClass(0.1, "#a6cee3", 0.67),
    RainLegendClass(0.5, "#60a5fa", 0.78),
    RainLegendClass(1.0, "#2563eb", 0.84),
    RainLegendClass(2.0, "#22c55e", 0.88),
    RainLegendClass(4.0, "#facc15", 0.92),
    RainLegendClass(8.0, "#f97316", 0.94),
    RainLegendClass(16.0, "#dc2626", 0.96),
    RainLegendClass(32.0, "#a21caf", 0.98),
    RainLegendClass(64.0, "#581c87", 1.0),
)


@dataclass(frozen=True)
class RainLevel:
    """One precipitation grid: WCS service, run cycle, publication delay, max _PT1H lead, thinning and map zoom."""

    id: str
    model: str
    service: str
    cycle_h: int
    delay_h: float
    max_lead_h: int
    thin: int
    min_zoom: float


LEVELS: tuple[RainLevel, ...] = (
    RainLevel("world", "ARPEGE", "arpege/1.0/wcs/MF-NWP-GLOBAL-ARPEGE-025-GLOBE-WCS", 6, 3.5, 48, 2, 0.0),
    RainLevel("europe", "ARPEGE", "arpege/1.0/wcs/MF-NWP-GLOBAL-ARPEGE-01-EUROPE-WCS", 6, 3.5, 48, 2, 3.0),
    RainLevel("france", "AROME", "arome/1.0/wcs/MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS", 3, 2.0, 38, 2, 5.0),
)
LEVEL_IDS = tuple(level.id for level in LEVELS)


def rain_field_key_from_env(env: Optional[Mapping[str, str]] = None) -> tuple[Optional[str], Optional[str]]:
    """``(key, problem)``: ``VIGIE_METEOFRANCE_RAIN_API_KEY``, else ``VIGIE_METEOFRANCE_API_KEY``; ``(None, None)`` when off."""
    source = env if env is not None else os.environ
    if (source.get(SWITCH_ENV) or "").strip().lower() in ("0", "false", "no", "off"):
        return None, None
    for var_name in (KEY_ENV, FALLBACK_KEY_ENV):
        raw = (source.get(var_name) or "").strip()
        if not raw:
            continue
        if not _KEY_SHAPE.match(raw):
            return None, f"{var_name} is set but is not shaped as a Météo-France API token: the forecast rain map is off"
        return raw, None
    return None, None


def candidate_runs(level: RainLevel, valid: datetime, now_unix: float) -> list[datetime]:
    """The runs that may hold ``valid`` for ``_PT1H``, newest first (lead between 1 h and ``max_lead_h``)."""
    cycle_s = level.cycle_h * 3600
    newest = math.floor((now_unix - level.delay_h * 3600) / cycle_s) * cycle_s
    runs: list[datetime] = []
    for k in range(RUNS_TRIED):
        run = datetime.fromtimestamp(newest - k * cycle_s, tz=timezone.utc)
        lead_h = (valid - run).total_seconds() / 3600
        if 1 <= lead_h <= level.max_lead_h:
            runs.append(run)
    return runs


# ============================================================================
# GRIB2 decoding
# ============================================================================


@dataclass(frozen=True)
class RainGrid:
    """One 1-hour total precipitation grid: rows north to south, columns west to east, ``nan`` outside model domain."""

    run: datetime
    valid: datetime
    short_name: str
    north: float
    south: float
    west: float
    east: float
    step_lat: float
    step_lon: float
    values: "np.ndarray[Any, Any]"  # float32, (rows, columns), mm/h
    whole_world: bool

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.values.shape[0]), int(self.values.shape[1]))


def decode_precipitation(data: bytes) -> RainGrid:
    """Decode one 1-hour total precipitation GRIB2 message from Météo-France WCS; raises ``ValueError`` otherwise."""
    import eccodes  # imported lazily when a file is decoded

    messages = split_messages(data)
    if len(messages) != 1:
        raise ValueError(f"{len(messages)} messages where one precipitation grid was expected")
    with DECODE_LOCK:
        handle = eccodes.codes_new_from_message(messages[0])
        try:
            short_name = str(eccodes.codes_get(handle, "shortName"))
            if short_name not in ("tp", "apcp"):
                raise ValueError(f"{short_name} is not total precipitation")
            if eccodes.codes_get(handle, "gridType") != "regular_ll":
                raise ValueError(f"grid {eccodes.codes_get(handle, 'gridType')} is not read")
            ni, nj = int(eccodes.codes_get(handle, "Ni")), int(eccodes.codes_get(handle, "Nj"))
            lat1 = float(eccodes.codes_get(handle, "latitudeOfFirstGridPointInDegrees"))
            lat2 = float(eccodes.codes_get(handle, "latitudeOfLastGridPointInDegrees"))
            lon1 = float(eccodes.codes_get(handle, "longitudeOfFirstGridPointInDegrees"))
            lon2 = float(eccodes.codes_get(handle, "longitudeOfLastGridPointInDegrees"))
            if int(eccodes.codes_get(handle, "iScansNegatively")) != 0 or int(eccodes.codes_get(handle, "jPointsAreConsecutive")) != 0:
                raise ValueError("the grid is not scanned along parallels from west to east")
            values = np.asarray(eccodes.codes_get_values(handle), dtype=np.float64).reshape(nj, ni)
            if bool(eccodes.codes_get(handle, "bitmapPresent")):
                values = np.where(values == float(eccodes.codes_get(handle, "missingValue")), np.nan, values)
            data_date, data_time = int(eccodes.codes_get(handle, "dataDate")), int(eccodes.codes_get(handle, "dataTime"))
            step_units = str(eccodes.codes_get(handle, "stepUnits"))
            start_step = int(eccodes.codes_get(handle, "startStep"))
            end_step = int(eccodes.codes_get(handle, "endStep"))
        finally:
            eccodes.codes_release(handle)
    if step_units not in ("1", "h"):
        raise ValueError(f"steps in unit {step_units} are not read")
    if end_step - start_step != 1:
        raise ValueError(f"expected a 1-hour accumulation, got {start_step}-{end_step} h")
    run = datetime(data_date // 10000, data_date // 100 % 100, data_date % 100, data_time // 100, data_time % 100, tzinfo=timezone.utc)
    if lat1 < lat2:
        values = values[::-1]
        lat1, lat2 = lat2, lat1
    if lon2 < lon1:
        lon2 += 360.0
    step_lat = (lat1 - lat2) / (nj - 1) if nj > 1 else 0.0
    step_lon = (lon2 - lon1) / (ni - 1) if ni > 1 else 0.0
    if step_lat <= 0 or step_lon <= 0:
        raise ValueError("a grid of one row or one column is not read")
    whole_world = abs((lon2 - lon1) + step_lon - 360.0) < step_lon / 2
    if whole_world:
        values, lon1 = _roll_to_dateline(values, lon1, step_lon)
        lon2 = lon1 + (ni - 1) * step_lon
    elif lon1 >= 180.0:
        lon1 -= 360.0
        lon2 -= 360.0
    return RainGrid(
        run=run,
        valid=run + timedelta(hours=end_step),
        short_name=short_name,
        north=round(lat1, 6),
        south=round(lat2, 6),
        west=round(lon1, 6),
        east=round(lon2, 6),
        step_lat=round(step_lat, 6),
        step_lon=round(step_lon, 6),
        values=values.astype(np.float32),
        whole_world=whole_world,
    )


# ============================================================================
# Web Mercator RGBA PNG rendering
# ============================================================================


def _mercator_y(lat_deg: "np.ndarray[Any, Any] | float") -> Any:
    rad = np.radians(lat_deg)
    return np.log(np.tan(np.pi / 4.0 + rad / 2.0))


def _inv_mercator_y(y: "np.ndarray[Any, Any]") -> "np.ndarray[Any, Any]":
    return np.degrees(2.0 * np.arctan(np.exp(y)) - np.pi / 2.0)


def png_rgba(rgba: "np.ndarray[Any, Any]") -> bytes:
    """Encode an 8-bit RGBA array ``(rows, columns, 4)`` as a PNG (zero Pillow dependency)."""
    rows, columns, channels = rgba.shape
    if channels != 4:
        raise ValueError("an image of four RGBA channels was expected")
    body = np.concatenate([np.zeros((rows, 1), np.uint8), rgba.astype(np.uint8).reshape(rows, columns * 4)], axis=1)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", columns, rows, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(body.tobytes(), 6)) + chunk(b"IEND", b"")


def colorize_rain(values: "np.ndarray[Any, Any]") -> "np.ndarray[Any, Any]":
    """Map mm/h values ``(rows, columns)`` to ``(rows, columns, 4)`` uint8 RGBA; dry (<0.1) and NaN are transparent."""
    rgba = np.zeros((values.shape[0], values.shape[1], 4), dtype=np.uint8)
    valid = np.isfinite(values)
    for entry in RAIN_LEGEND:
        mask = valid & (values >= entry.min_mm_h)
        rgba[mask] = entry.rgba
    return rgba


@dataclass(frozen=True)
class RainFrameImage:
    """One rendered RGBA forecast precipitation overlay."""

    level: RainLevel
    run: datetime
    valid: datetime
    lead_hours: int
    bounds: tuple[float, float, float, float]  # west, south, east, north
    width: int
    height: int
    png: bytes
    stamp: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stamp", image_stamp(self.valid, self.run))


@dataclass(frozen=True)
class RainLevelFrames:
    """All rendered forecast frames of one level (world, europe or france), earliest lead first."""

    level: RainLevel
    run: datetime
    bounds: tuple[float, float, float, float]
    width: int
    height: int
    frames: tuple[RainFrameImage, ...]


@dataclass(frozen=True)
class RainField:
    """The forecast rain overlays across the levels that could be read, widest to finest."""

    base: datetime
    levels: tuple[RainLevelFrames, ...]


def make_rain_image(level: RainLevel, grid: RainGrid, lead_hours: int) -> RainFrameImage:
    """Project a decoded 1-hour precipitation grid onto Web Mercator rows and render a lightweight RGBA PNG."""
    nj, ni = grid.shape
    thin = max(1, level.thin)
    m_north = min(grid.north, MERCATOR_MAX_LAT)
    m_south = max(grid.south, -MERCATOR_MAX_LAT)
    if m_north <= m_south:
        raise ValueError("grid lies outside Web Mercator latitude bounds")

    cols = grid.values[:, ::thin]
    if grid.whole_world:
        # Append column 0 at the right edge so the global overlay wraps seamlessly from -180 to +180.
        cols = np.concatenate([cols, cols[:, :1]], axis=1)
        west, east = -180.0, 180.0
    else:
        west = round(grid.west, 4)
        east = round(grid.west + (cols.shape[1] - 1) * grid.step_lon * thin, 4)

    out_h = max(2, int(round(nj / thin)))
    ys = np.linspace(float(_mercator_y(m_north)), float(_mercator_y(m_south)), out_h)
    lats = _inv_mercator_y(ys)
    row_i = np.clip(np.round((grid.north - lats) / grid.step_lat).astype(np.int32), 0, nj - 1)
    sampled = cols[row_i, :]
    rgba = colorize_rain(sampled)
    return RainFrameImage(
        level=level,
        run=grid.run,
        valid=grid.valid,
        lead_hours=lead_hours,
        bounds=(west, round(m_south, 4), east, round(m_north, 4)),
        width=int(sampled.shape[1]),
        height=int(sampled.shape[0]),
        png=png_rgba(rgba),
    )


# ============================================================================
# Collector
# ============================================================================


class RunMissingError(LookupError):
    """The run does not have this valid hour yet; fall back to an earlier run."""


class MeteoFranceRainFieldCollector(BaseCollector[RainField]):
    """Global, Europe and France forecast rain overlays from Météo-France AROME/ARPEGE WCS."""

    def __init__(self, api_key: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="meteofrance_rain_field",
            name="Météo-France AROME / ARPEGE precipitation (forecast map)",
            domain="weather",
            ttl_seconds=1800,
            failure_threshold=3,
            recovery_timeout_seconds=300,
            timeout_seconds=45.0,
            http_client=http_client,
        )
        self._key = api_key
        self.secrets = (api_key,)
        self._held: dict[str, RainLevelFrames] = {}
        self._missing: dict[tuple[str, datetime], float] = {}
        self._images: dict[tuple[str, str], bytes] = {}
        self._frames_by_key: dict[tuple[str, datetime, datetime], RainFrameImage] = {}

    async def field(self) -> Optional[Fetched[RainField]]:
        """The forecast rain overlays for the current hour (or stale fallback on portal failure); ``None`` when nothing is held."""
        try:
            return await self.fetch_ex(cache_key="field", ttl_seconds=seconds_to_next_hour_change(clock.wall()))
        except Exception:
            return None

    def image(self, level_id: str, stamp: str) -> Optional[bytes]:
        """The cached RGBA PNG for ``(level_id, stamp)``, or ``None`` if unknown or evicted."""
        return self._images.get((level_id, stamp))

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[RainField]:
        kwargs.pop("previous", None)
        now = clock.wall()
        base = nearest_hour(now)
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        results = await asyncio.gather(*(self._level(client, sem, level, base, now) for level in LEVELS), return_exceptions=True)
        level_frames: list[RainLevelFrames] = []
        problems: list[str] = []
        for level, result in zip(LEVELS, results, strict=True):
            if isinstance(result, RainLevelFrames):
                level_frames.append(result)
                continue
            problems.append(f"{level.id}: {self.redact(str(result))}")
            old = self._held.get(level.id)
            if old is not None and old.frames and abs((old.frames[0].valid - base).total_seconds()) <= KEEP_OLD_S + 3600:
                level_frames.append(old)
        if not level_frames:
            raise ValueError("no forecast rain grid could be read: " + "; ".join(problems))
        whole = len(level_frames) == len(LEVELS) and all(
            lf.frames and lf.frames[0].valid == base + timedelta(hours=LEAD_HOURS[0]) for lf in level_frames
        )
        lifetime = seconds_to_next_hour_change(now) if whole else min(RETRY_INCOMPLETE_S, seconds_to_next_hour_change(now))
        return Collected(RainField(base=base, levels=tuple(level_frames)), fresh_for_s=lifetime)

    async def _level(
        self, client: httpx.AsyncClient, sem: asyncio.Semaphore, level: RainLevel, base: datetime, now: float
    ) -> RainLevelFrames:
        targets = [(lead, base + timedelta(hours=lead)) for lead in LEAD_HOURS]
        first_lead, first_valid = targets[0]
        runs = candidate_runs(level, first_valid, now)
        if not runs:
            raise ValueError(f"no run of {level.model} can hold {first_valid:%Y-%m-%d %H} UTC")

        chosen_run: Optional[datetime] = None
        first_frame: Optional[RainFrameImage] = None
        last_exc: Optional[Exception] = None

        for run in runs:
            missing_at = self._missing.get((level.id, run))
            if missing_at is not None and clock.monotonic() - missing_at < MISSING_RETRY_S:
                continue
            try:
                first_frame = await self._frame(client, sem, level, run, first_valid, first_lead)
            except RunMissingError as exc:
                self._missing[(level.id, run)] = clock.monotonic()
                last_exc = exc
                continue
            chosen_run = run
            break

        if chosen_run is None or first_frame is None:
            raise last_exc if last_exc is not None else ValueError(f"{level.id}: every candidate run lacked {first_valid:%H} UTC recently")

        frames: list[RainFrameImage] = [first_frame]
        rest_tasks = [
            self._frame_optional(client, sem, level, chosen_run, valid, lead)
            for lead, valid in targets[1:]
            if 1 <= (valid - chosen_run).total_seconds() / 3600 <= level.max_lead_h
        ]
        if rest_tasks:
            rest = await asyncio.gather(*rest_tasks)
            for item in rest:
                if item is not None:
                    frames.append(item)

        frames.sort(key=lambda f: f.lead_hours)
        bundle = RainLevelFrames(
            level=level,
            run=chosen_run,
            bounds=first_frame.bounds,
            width=first_frame.width,
            height=first_frame.height,
            frames=tuple(frames),
        )
        self._keep(bundle)
        return bundle

    async def _frame_optional(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        level: RainLevel,
        run: datetime,
        valid: datetime,
        lead_hours: int,
    ) -> Optional[RainFrameImage]:
        try:
            return await self._frame(client, sem, level, run, valid, lead_hours)
        except Exception:
            return None

    async def _frame(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        level: RainLevel,
        run: datetime,
        valid: datetime,
        lead_hours: int,
    ) -> RainFrameImage:
        cached = self._frames_by_key.get((level.id, valid, run))
        if cached is not None:
            if cached.lead_hours == lead_hours:
                return cached
            rebound = RainFrameImage(
                level=cached.level,
                run=cached.run,
                valid=cached.valid,
                lead_hours=lead_hours,
                bounds=cached.bounds,
                width=cached.width,
                height=cached.height,
                png=cached.png,
            )
            self._frames_by_key[(level.id, valid, run)] = rebound
            return rebound

        query = [
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("coverageid", COVERAGE.format(run=run_stamp(run))),
            ("format", FORMAT),
            ("subset", f"time({valid:%Y-%m-%dT%H:%M:%SZ})"),
        ]
        url = f"{WCS_HOST}/{level.service}/GetCoverage?{urlencode(query)}"
        async with sem:
            response = await self._get_capped(client, url, max_bytes=MAX_BYTES, headers={"apikey": self._key, "accept": "*/*"})
        if response.status_code in (400, 404):
            raise RunMissingError(f"{level.id}: run {run_stamp(run)} lacks {valid:%Y-%m-%d %H} UTC (HTTP {response.status_code})")
        response.raise_for_status()
        grid = await clock.run_blocking(decode_precipitation, response.content)
        if grid.run != run or grid.valid != valid:
            raise ValueError(
                f"{level.id}: the file is of run {grid.run:%Y-%m-%d %H} UTC valid {grid.valid:%Y-%m-%d %H} UTC, not the one asked"
            )
        frame = await clock.run_blocking(make_rain_image, level, grid, lead_hours)
        self._frames_by_key[(level.id, valid, run)] = frame
        while len(self._frames_by_key) > IMAGES_KEPT:
            del self._frames_by_key[next(iter(self._frames_by_key))]
        return frame

    def _keep(self, bundle: RainLevelFrames) -> None:
        self._held[bundle.level.id] = bundle
        for frame in bundle.frames:
            self._images[(bundle.level.id, frame.stamp)] = frame.png
            while len(self._images) > IMAGES_KEPT:
                del self._images[next(iter(self._images))]
        horizon = clock.monotonic() - MISSING_RETRY_S
        self._missing = {key: at for key, at in self._missing.items() if at >= horizon}
