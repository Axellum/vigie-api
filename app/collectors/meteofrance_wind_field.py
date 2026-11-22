"""The forecast wind for a map (``GET /v1/wind/field``): AROME and ARPEGE of Météo-France, as small images a browser animates.

**What is read.** The wind 10 m above the ground, east (``U``) and north (``V``) components, of the hour nearest to now, from the
WCS of Météo-France's API portal (``/public/arome/1.0`` and ``/public/arpege/1.0``, the key of the application in the ``apikey``
header, never in an address). Three grids, from the finest to the widest, each read whole (one GRIB2 message per component and
per grid, ``grid_simple`` packing, measured on 2026-11-22 for the run of 2026-11-21 18 UTC at +6 h):

=========  =========================================  ==============================  =====================  ===========
level      service                                    area, step                      file (one component)   image
=========  =========================================  ==============================  =====================  ===========
``france`` ``MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS``   12 W-16 E, 37.5-55.4 N, 0.025   1.1 MB (1121 x 717)    every 2nd node: 0.05
``europe`` ``MF-NWP-GLOBAL-ARPEGE-01-EUROPE-WCS``     32 W-42 E, 20-72 N, 0.1         0.6 MB (741 x 521)     every 2nd node: 0.2
``world``  ``MF-NWP-GLOBAL-ARPEGE-025-GLOBE-WCS``     the globe, 0.25                 1.6 MB (1440 x 721)    every 2nd node: 0.5
=========  =========================================  ==============================  =====================  ===========

AROME (1.3 km) is the model of France and the best there; ARPEGE is the global model AROME is run inside, so the two meet without
a jump at the edge of AROME. A request answered in about half a second (2026-11-22): 6 requests an hour, about 6.5 MB, out of the
50 a minute of the portal's free plan, shared with the other readers of the key.

**Which run.** AROME runs every 3 hours, ARPEGE every 6 (UTC). For a grid, the runs tried are the newest one old enough to be
published (``Level.delay_h`` after its time; AROME of 21 UTC was not yet listed at 00 UTC on 2026-11-22) and the two before it;
a run that has not the hour yet answers 400 or 404, is remembered as missing for ``MISSING_RETRY_S`` and the one before is asked.

**What is served.** Each grid becomes a PNG image of every second node after a light smoothing (a 3 x 3 binomial mean, which keeps
the mean wind and avoids a stairs effect), with the east component in red and the north component in green, both on one byte
(``value = (byte - 127.5) / 127.5 * scale_ms``, ``scale_ms`` the strongest component of the grid rounded up: a step of about
0.2 m/s), and blue at 255 where the model has a value (AROME has none outside its own area, in the corners of its grid). An image
weighs about 130 to 300 KB; the three are made again once an hour, in about a second. The browser draws moving trails from them:
the speed and the direction are the model's; nothing is drawn where it has no value.

A grid that fails keeps the image of an earlier hour (at most ``KEEP_OLD_S`` old, its own hour said); with none, the grid is left
out and the others are served. A forecast, never a measure (``GET /v1/stations/wind`` gives the wind measured).

Licence: Licence Ouverte 2.0 (Etalab), as the licence documents of the AROME and ARPEGE APIs on the portal say (read on
2026-11-22): reuse, commercial included, with the source and the date of the run named and the data not altered; the reduced
resolution is said in the credit (see ADR 0038).
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

KEY_ENV = "VIGIE_METEOFRANCE_WIND_API_KEY"
FALLBACK_KEY_ENV = "VIGIE_METEOFRANCE_API_KEY"
SWITCH_ENV = "VIGIE_WIND_FIELD"
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9._\-+/=]{16,}$")

WCS_HOST = "https://public-api.meteofrance.fr/public"
COVERAGE = "{component}_COMPONENT_OF_WIND__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND___{run}"
FORMAT = "application/wmo-grib"
MAX_BYTES = 8_000_000  # one component of the world grid is 1.6 MB
RUNS_TRIED = 3
MISSING_RETRY_S = 15 * 60  # a run that had not the hour is asked again after this long
KEEP_OLD_S = 3 * 3600  # the image of an earlier hour is served this long when its grid fails
RETRY_INCOMPLETE_S = 600  # an answer that lacks a grid of the hour is made again after this long
IMAGES_KEPT = 12  # images kept for their address (this hour and the ones before, 3 grids each)
SCALE_MIN_MS = 10.0
SCALE_MAX_MS = 90.0
CREDIT = (
    "Source: Météo-France, forecast models AROME (France) and ARPEGE (Europe, world), wind at 10 m "
    "(https://portail-api.meteofrance.fr, Licence Ouverte 2.0), run dated in the answer; drawn by Vigie at a reduced resolution"
)
NOTE = "A forecast for the hour, not a measure: the wind measured by the stations is GET /v1/stations/wind. Not for safety."


@dataclass(frozen=True)
class Level:
    """One grid of the map: where it is read, how often its model runs, from what zoom the map wants it."""

    id: str
    model: str
    service: str  # ``<api>/1.0/wcs/<service id>`` under ``WCS_HOST``
    cycle_h: int
    delay_h: float  # a run is first asked this long after its time
    max_lead_h: int
    thin: int  # one node out of ``thin`` in each direction
    min_zoom: float


LEVELS: tuple[Level, ...] = (
    Level("world", "ARPEGE", "arpege/1.0/wcs/MF-NWP-GLOBAL-ARPEGE-025-GLOBE-WCS", 6, 3.5, 96, 2, 0.0),
    Level("europe", "ARPEGE", "arpege/1.0/wcs/MF-NWP-GLOBAL-ARPEGE-01-EUROPE-WCS", 6, 3.5, 96, 2, 3.0),
    Level("france", "AROME", "arome/1.0/wcs/MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS", 3, 2.0, 48, 2, 5.0),
)
LEVEL_IDS = tuple(level.id for level in LEVELS)


def wind_field_key_from_env(env: Optional[Mapping[str, str]] = None) -> tuple[Optional[str], Optional[str]]:
    """``(key, problem)``: ``VIGIE_METEOFRANCE_WIND_API_KEY``, else ``VIGIE_METEOFRANCE_API_KEY``; ``(None, None)`` when switched off."""
    source = env if env is not None else os.environ
    if (source.get(SWITCH_ENV) or "").strip().lower() in ("0", "false", "no", "off"):
        return None, None
    for var_name in (KEY_ENV, FALLBACK_KEY_ENV):
        raw = (source.get(var_name) or "").strip()
        if not raw:
            continue
        if not _KEY_SHAPE.match(raw):
            return None, f"{var_name} is set but is not shaped as a Météo-France API token: the forecast wind map is off"
        return raw, None
    return None, None


# ============================================================================
# Time
# ============================================================================


def nearest_hour(now_unix: float) -> datetime:
    """The whole UTC hour nearest to ``now_unix`` (01:29 gives 01:00, 01:30 gives 02:00)."""
    return datetime.fromtimestamp(math.floor((now_unix + 1800) / 3600) * 3600, tz=timezone.utc)


def seconds_to_next_hour_change(now_unix: float) -> float:
    """Seconds until ``nearest_hour`` changes (the next half past), at least a minute."""
    return max(60.0, 3600 - ((now_unix - 1800) % 3600))


def candidate_runs(level: Level, valid: datetime, now_unix: float) -> list[datetime]:
    """The runs that may hold ``valid``, newest first: published by now (``delay_h``), at most ``RUNS_TRIED``, lead 0 to ``max_lead_h``."""
    cycle_s = level.cycle_h * 3600
    newest = math.floor((now_unix - level.delay_h * 3600) / cycle_s) * cycle_s
    runs = []
    for k in range(RUNS_TRIED):
        run = datetime.fromtimestamp(newest - k * cycle_s, tz=timezone.utc)
        lead_h = (valid - run).total_seconds() / 3600
        if 0 <= lead_h <= level.max_lead_h:
            runs.append(run)
    return runs


def run_stamp(run: datetime) -> str:
    """A run as the WCS names it in a coverage, e.g. ``2026-11-21T18.00.00Z``."""
    return run.strftime("%Y-%m-%dT%H.%M.%SZ")


def image_stamp(valid: datetime, run: datetime) -> str:
    """The name of an image: its hour and its run, so that its address never serves two images."""
    return f"{valid:%Y%m%d%H}-{run:%Y%m%d%H}"


# ============================================================================
# Grids
# ============================================================================


@dataclass(frozen=True)
class Grid:
    """One component on a regular grid: rows from north to south, columns from west to east (longitudes in -180..180 or beyond 180
    only for a grid that crosses it), ``nan`` where the model has no value."""

    run: datetime
    valid: datetime
    short_name: str
    north: float
    west: float
    step_lat: float
    step_lon: float
    values: "np.ndarray[Any, Any]"  # float32, (rows, columns)
    whole_world: bool

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.values.shape[0]), int(self.values.shape[1]))


def decode_component(data: bytes) -> Grid:
    """The one 10 m wind component of a GRIB2 file of the WCS; ``ValueError`` when the file is not that."""
    import eccodes  # imported only when a file is read

    messages = split_messages(data)
    if len(messages) != 1:
        raise ValueError(f"{len(messages)} messages where one component of the wind was expected")
    with DECODE_LOCK:
        handle = eccodes.codes_new_from_message(messages[0])
        try:
            short_name = str(eccodes.codes_get(handle, "shortName"))
            if short_name not in ("10u", "10v"):
                raise ValueError(f"{short_name} is not a component of the wind at 10 m")
            if eccodes.codes_get(handle, "gridType") != "regular_ll":
                raise ValueError(f"grid {eccodes.codes_get(handle, 'gridType')} is not read")
            ni, nj = int(eccodes.codes_get(handle, "Ni")), int(eccodes.codes_get(handle, "Nj"))
            lat1 = float(eccodes.codes_get(handle, "latitudeOfFirstGridPointInDegrees"))
            lat2 = float(eccodes.codes_get(handle, "latitudeOfLastGridPointInDegrees"))
            lon1 = float(eccodes.codes_get(handle, "longitudeOfFirstGridPointInDegrees"))
            lon2 = float(eccodes.codes_get(handle, "longitudeOfLastGridPointInDegrees"))
            if int(eccodes.codes_get(handle, "iScansNegatively")) != 0 or int(eccodes.codes_get(handle, "jPointsAreConsecutive")) != 0:
                raise ValueError("the grid is not scanned along the parallels from west to east")
            values = np.asarray(eccodes.codes_get_values(handle), dtype=np.float64).reshape(nj, ni)
            if bool(eccodes.codes_get(handle, "bitmapPresent")):
                values = np.where(values == float(eccodes.codes_get(handle, "missingValue")), np.nan, values)
            data_date, data_time = int(eccodes.codes_get(handle, "dataDate")), int(eccodes.codes_get(handle, "dataTime"))
            step_units = str(eccodes.codes_get(handle, "stepUnits"))
            end_step = int(eccodes.codes_get(handle, "endStep"))
        finally:
            eccodes.codes_release(handle)
    if step_units not in ("1", "h"):
        raise ValueError(f"steps in unit {step_units} are not read")
    run = datetime(data_date // 10000, data_date // 100 % 100, data_date % 100, data_time // 100, data_time % 100, tzinfo=timezone.utc)
    if lat1 < lat2:  # scanned from south to north: turned so that the first row is the northern one
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
        # 0..359.75: turned to begin at 180 W, so that the columns run from west to east as the map does.
        values, lon1 = _roll_to_dateline(values, lon1, step_lon)
    elif lon1 >= 180.0:
        lon1 -= 360.0
    return Grid(
        run=run,
        valid=run + timedelta(hours=end_step),
        short_name=short_name,
        north=round(lat1, 6),
        west=round(lon1, 6),
        step_lat=round(step_lat, 6),
        step_lon=round(step_lon, 6),
        values=values.astype(np.float32),
        whole_world=whole_world,
    )


def _roll_to_dateline(values: "np.ndarray[Any, Any]", lon1: float, step: float) -> tuple["np.ndarray[Any, Any]", float]:
    """The columns of a whole-world grid turned so that the first one is at or just east of 180 W."""
    first = ((lon1 + 180.0) % 360.0) - 180.0  # the first column, in -180..180
    shift = int(round(((first - (-180.0)) % 360.0) / step))  # columns from 180 W to the first one
    count = values.shape[1]
    start = (count - shift) % count  # the column now at 180 W (or the first one after it)
    rolled = np.roll(values, -start, axis=1)
    west = first + start * step
    west = ((west + 180.0) % 360.0) - 180.0
    return rolled, west


def thin_grid(values: "np.ndarray[Any, Any]", every: int, whole_world: bool) -> "np.ndarray[Any, Any]":
    """One node out of ``every`` in each direction, after a 3 x 3 binomial mean of the nodes that have a value (columns wrap around a
    whole-world grid). ``every`` 1 keeps the grid as it is. A node with no value stays without one."""
    if every <= 1:
        return values.astype(np.float32)
    have = np.isfinite(values)
    filled = np.where(have, values, 0.0).astype(np.float64)
    weight = have.astype(np.float64)

    def smooth(a: "np.ndarray[Any, Any]") -> "np.ndarray[Any, Any]":
        # rows: [1, 2, 1] with the edge row repeated; columns: the same, wrapped around the world when it is whole
        up = np.vstack([a[:1], a[:-1]])
        down = np.vstack([a[1:], a[-1:]])
        a = up + 2 * a + down
        if whole_world:
            left, right = np.roll(a, 1, axis=1), np.roll(a, -1, axis=1)
        else:
            left = np.hstack([a[:, :1], a[:, :-1]])
            right = np.hstack([a[:, 1:], a[:, -1:]])
        return left + 2 * a + right

    total, count = smooth(filled), smooth(weight)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(count > 0, total / np.where(count > 0, count, 1.0), np.nan)
    mean = np.where(have, mean, np.nan)
    return mean[::every, ::every].astype(np.float32)


# ============================================================================
# Images
# ============================================================================


def wind_scale(u: "np.ndarray[Any, Any]", v: "np.ndarray[Any, Any]") -> float:
    """The strongest component of the grid in m/s, rounded up, within ``SCALE_MIN_MS`` and ``SCALE_MAX_MS``."""
    both = np.concatenate([np.abs(u[np.isfinite(u)]), np.abs(v[np.isfinite(v)])])
    strongest = float(both.max()) if both.size else 0.0
    return float(min(SCALE_MAX_MS, max(SCALE_MIN_MS, math.ceil(strongest))))


def to_bytes(values: "np.ndarray[Any, Any]", scale: float) -> "np.ndarray[Any, Any]":
    """``value = (byte - 127.5) / 127.5 * scale``, rounded to the nearest byte (0 where there is no value)."""
    clean = np.where(np.isfinite(values), values, 0.0)
    return np.clip(np.round(clean / scale * 127.5 + 127.5), 0, 255).astype(np.uint8)


def png_rgb(rgb: "np.ndarray[Any, Any]") -> bytes:
    """A PNG (8-bit RGB, no alpha so that a browser gives the bytes back as they are) of ``rgb`` (rows, columns, 3), each row with the
    ``Sub`` filter (the difference with the pixel on the left: a smooth field compresses about a third better)."""
    rows, columns, channels = rgb.shape
    if channels != 3:
        raise ValueError("an image of three channels was expected")
    raw = rgb.astype(np.int16)
    diff = raw.copy()
    diff[:, 1:, :] = raw[:, 1:, :] - raw[:, :-1, :]
    body = np.concatenate([np.ones((rows, 1), np.uint8), (diff % 256).astype(np.uint8).reshape(rows, columns * 3)], axis=1)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", columns, rows, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(body.tobytes(), 9)) + chunk(b"IEND", b"")


@dataclass(frozen=True)
class LevelImage:
    """One grid made into an image, with what a map needs to place and read it."""

    level: Level
    run: datetime
    valid: datetime
    north: float
    west: float
    step_lat: float
    step_lon: float
    width: int
    height: int
    scale_ms: float
    whole_world: bool
    png: bytes
    stamp: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stamp", image_stamp(self.valid, self.run))

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """West, south, east, north of the nodes (the centres of the image's pixels)."""
        south = self.north - (self.height - 1) * self.step_lat
        east = self.west + (self.width - 1) * self.step_lon
        return (round(self.west, 4), round(south, 4), round(east, 4), round(self.north, 4))


def make_image(level: Level, u: Grid, v: Grid) -> LevelImage:
    """The image of a grid from its two components (``ValueError`` when they do not match)."""
    if (u.short_name, v.short_name) != ("10u", "10v"):
        raise ValueError("the two components are not the east and north wind")
    if (u.run, u.valid, u.shape, u.north, u.west, u.step_lat, u.step_lon) != (v.run, v.valid, v.shape, v.north, v.west, v.step_lat, v.step_lon):
        raise ValueError("the two components are not on the same grid or not of the same hour")
    tu = thin_grid(u.values, level.thin, u.whole_world)
    tv = thin_grid(v.values, level.thin, u.whole_world)
    scale = wind_scale(tu, tv)
    have = np.isfinite(tu) & np.isfinite(tv)
    rgb = np.stack([to_bytes(tu, scale), to_bytes(tv, scale), np.where(have, 255, 0).astype(np.uint8)], axis=-1)
    return LevelImage(
        level=level,
        run=u.run,
        valid=u.valid,
        north=u.north,
        west=u.west,
        step_lat=round(u.step_lat * level.thin, 6),
        step_lon=round(u.step_lon * level.thin, 6),
        width=int(tu.shape[1]),
        height=int(tu.shape[0]),
        scale_ms=scale,
        whole_world=u.whole_world,
        png=png_rgb(rgb),
    )


@dataclass(frozen=True)
class WindField:
    """The images of the hour: the grids that could be read, from the widest to the finest."""

    valid: datetime
    images: tuple[LevelImage, ...]


# ============================================================================
# The collector
# ============================================================================


class RunMissingError(LookupError):
    """The run has not this hour (yet): the one before is asked."""


class MeteoFranceWindFieldCollector(BaseCollector[WindField]):
    """The wind of the hour from AROME and ARPEGE, as images (see the module)."""

    def __init__(self, api_key: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="meteofrance_wind_field",
            name="Météo-France AROME / ARPEGE wind at 10 m (forecast map)",
            domain="weather",
            ttl_seconds=1800,
            failure_threshold=3,
            recovery_timeout_seconds=300,
            timeout_seconds=40.0,
            http_client=http_client,
        )
        self._key = api_key
        self.secrets = (api_key,)
        self._held: dict[str, LevelImage] = {}  # by level: the newest image made
        self._missing: dict[tuple[str, datetime], float] = {}  # (level, run) -> when it lacked the hour (monotonic)
        self._images: dict[tuple[str, str], bytes] = {}  # (level, stamp) -> PNG, the newest ``IMAGES_KEPT``

    async def field(self) -> Optional[Fetched[WindField]]:
        """The images of the hour (or an earlier one, flagged stale, when the portal fails); ``None`` when none could ever be made."""
        try:
            return await self.fetch_ex(cache_key="field", ttl_seconds=seconds_to_next_hour_change(clock.wall()))
        except Exception:
            return None

    def image(self, level_id: str, stamp: str) -> Optional[bytes]:
        """The PNG of an image still kept, by the address an answer gave."""
        return self._images.get((level_id, stamp))

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[WindField]:
        kwargs.pop("previous", None)
        now = clock.wall()
        valid = nearest_hour(now)
        results = await asyncio.gather(*(self._level(client, level, valid, now) for level in LEVELS), return_exceptions=True)
        images: list[LevelImage] = []
        problems: list[str] = []
        for level, result in zip(LEVELS, results, strict=True):
            if isinstance(result, LevelImage):
                images.append(result)
                continue
            problems.append(f"{level.id}: {self.redact(str(result))}")
            old = self._held.get(level.id)
            if old is not None and abs((old.valid - valid).total_seconds()) <= KEEP_OLD_S:
                images.append(old)  # an earlier hour of this grid, its own hour said
        if not images:
            raise ValueError("no grid of the wind could be read: " + "; ".join(problems))
        whole = len(images) == len(LEVELS) and all(image.valid == valid for image in images)
        # Until the next hour when every grid is of this hour; otherwise asked again a little later.
        lifetime = seconds_to_next_hour_change(now) if whole else min(RETRY_INCOMPLETE_S, seconds_to_next_hour_change(now))
        return Collected(WindField(valid=valid, images=tuple(images)), fresh_for_s=lifetime)

    async def _level(self, client: httpx.AsyncClient, level: Level, valid: datetime, now: float) -> LevelImage:
        held = self._held.get(level.id)
        if held is not None and held.valid == valid:
            return held
        runs = candidate_runs(level, valid, now)
        if not runs:
            raise ValueError(f"no run of {level.model} can hold {valid:%Y-%m-%d %H} UTC")
        last: Optional[Exception] = None
        for run in runs:
            missing_at = self._missing.get((level.id, run))
            if missing_at is not None and clock.monotonic() - missing_at < MISSING_RETRY_S:
                continue
            try:
                u = await self._component(client, level, run, valid, "U")
                v = await self._component(client, level, run, valid, "V")
            except RunMissingError as exc:
                self._missing[(level.id, run)] = clock.monotonic()
                last = exc
                continue
            image = await clock.run_blocking(make_image, level, u, v)
            if image.run != run or image.valid != valid:
                raise ValueError(f"{level.id}: the file is of the run {image.run:%Y-%m-%d %H} UTC at {image.valid:%H} UTC, not the one asked")
            self._keep(image)
            return image
        raise last if last is not None else ValueError(f"{level.id}: every run was missing the hour a short while ago")

    async def _component(self, client: httpx.AsyncClient, level: Level, run: datetime, valid: datetime, component: str) -> Grid:
        query = [
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("coverageid", COVERAGE.format(component=component, run=run_stamp(run))),
            ("format", FORMAT),
            ("subset", f"time({valid:%Y-%m-%dT%H:%M:%SZ})"),
            ("subset", "height(10)"),
        ]
        url = f"{WCS_HOST}/{level.service}/GetCoverage?{urlencode(query)}"
        response = await self._get_capped(client, url, max_bytes=MAX_BYTES, headers={"apikey": self._key, "accept": "*/*"})
        if response.status_code in (400, 404):
            raise RunMissingError(f"{level.id}: the run {run_stamp(run)} has not {valid:%H} UTC (HTTP {response.status_code})")
        response.raise_for_status()
        return await clock.run_blocking(decode_component, response.content)

    def _keep(self, image: LevelImage) -> None:
        self._held[image.level.id] = image
        self._images[(image.level.id, image.stamp)] = image.png
        while len(self._images) > IMAGES_KEPT:
            del self._images[next(iter(self._images))]
        horizon = clock.monotonic() - MISSING_RETRY_S
        self._missing = {key: at for key, at in self._missing.items() if at >= horizon}
