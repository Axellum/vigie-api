"""NASA FIRMS: the active fires (hotspots) that the VIIRS instruments of NOAA-20 and NOAA-21 detect, read once per refresh.

FIRMS (Fire Information for Resource Management System, NASA LANCE) serves the detections as CSV through an "area" API that needs a
free ``MAP_KEY``, given in the URL path::

    https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/world/{DAY_RANGE}/{DATE}

(read on 2026-10-20, https://firms.modaps.eosdis.nasa.gov/api/area/: ``DAY_RANGE`` 1 to 5, ``DATE`` ``YYYY-MM-DD`` optional, the area
``world`` or ``west,south,east,north``). The key is read from the environment variable ``VIGIE_FIRMS_MAP_KEY``; without it there is no
FIRMS collector at all (``load_settings`` says why) and every answer that would use it says the source is off.

What is asked, and why (measurements in ``docs/data-sources.md``):

- **Sources**: ``VIIRS_NOAA20_NRT`` and ``VIIRS_NOAA21_NRT`` (375 m pixels, near real-time: global data within 3 hours of the
  observation, 2.6 to 2.8 hours measured, plus ultra-real-time detections of the United States and Canada within minutes). Suomi NPP
  is left out on purpose: NASA announces that its deliveries cease on 2026-11-02. MODIS (1 km, another CSV layout) is not read.
- **One "world" read per source and day**: the data of today (UTC) is read again at each refresh, the data of yesterday once it has
  settled (4 hours after midnight, UTC), so the rows of the last 24 hours are there at any time of the day. The area API has no
  validator (no ``ETag``, no ``Last-Modified``, no ``Content-Length`` on a ``HEAD``; ``Cache-Control: no-cache``): a read is always a
  full read, so the refresh is the cache lifetime (60 minutes by default, ``VIGIE_FIRMS_REFRESH_MINUTES``).
- **Transactions**: FIRMS allows 5,000 transactions per 10 minutes per key and says a large request counts as several. A world read
  of one day was measured at 36 transactions; the budget counts it so (``WORLD_DAY_TRANSACTIONS``).

The rows are kept compactly (``HotspotTable``: typed arrays, about 37 bytes a row, sorted by latitude through an index) and parsed in a
thread, so the event loop is never held. A value the CSV does not give is ``None``; a row without a readable position or time is skipped
and counted. **The key never leaves this module**: every error that is raised, recorded (``last_error`` of ``GET /v1/providers``) or
logged has it replaced by ``REDACTED`` (``tests/test_firms_secret.py``).
"""

from __future__ import annotations

import csv
import io
import logging
import math
import os
import re
from array import array
from bisect import bisect_left, bisect_right
from calendar import timegm
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app import clock
from app.collectors.base import BaseCollector, CacheEntry, Collected
from app.collectors.budget import BudgetExhaustedError

HOST = "firms.modaps.eosdis.nasa.gov"
AREA_URL = f"https://{HOST}/api/area/csv"
CACHE_KEY = "global"  # the default key of ``fetch``: one entry holds everything
ACCEPT = {"Accept": "text/csv, text/plain, */*"}

# The sources read by default, and the only ones that can be asked (the VIIRS CSV layout). Suomi NPP stops on 2026-11-02.
DEFAULT_SOURCES: tuple[str, ...] = ("VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")
ALLOWED_SOURCES: tuple[str, ...] = DEFAULT_SOURCES

DEFAULT_REFRESH_MINUTES = 60
MIN_REFRESH_MINUTES = 10
MAX_REFRESH_MINUTES = 360
RETRY_S = 300.0  # a source that could not be read is asked again after five minutes, the others are left alone
SHRINK_RATIO = 0.9  # a day's file only grows as the day goes on: an answer with under 90 % of the rows of the last reading is a cut answer
SETTLE_S = 4 * 3600  # the NRT data of a day is complete 3 hours after it ends; one more hour of margin

# Transactions a world read of one day costs on a MAP_KEY: measured on 2026-10-20 with ``/mapserver/mapkey_status/`` (36 for each
# read of one source; 108 after three reads).
WORLD_DAY_TRANSACTIONS = 36.0

# FIRMS answers slowly and, from some networks, loses connections (2026-10-20: handshakes that never finish, a request that took 35 s):
# a short connect timeout and one new attempt on the connect phase, a long read (a world day is about 5 MB).
TIMEOUT = httpx.Timeout(connect=12.0, read=60.0, write=30.0, pool=10.0)

MAX_BODY_BYTES = 200 * 1024 * 1024  # a world day is about 5 MB; this only stops a mistake
KEY_SHAPE = re.compile(r"^[A-Za-z0-9]{8,64}$")

REQUIRED_COLUMNS = ("latitude", "longitude", "acq_date", "acq_time")
CONFIDENCE_CODES: dict[str, int] = {"l": 1, "n": 2, "h": 3}  # VIIRS: low, nominal, high
CONFIDENCE_NAMES: tuple[Optional[str], ...] = (None, "low", "nominal", "high")
DAYNIGHT_NAMES: tuple[Optional[str], ...] = (None, "day", "night")
SCALE = 100_000  # positions are kept in 1e-5 degrees (about 1 m; the CSV has 5 decimals)

NAN = float("nan")


class FIRMSError(RuntimeError):
    """A FIRMS read failed. The message never holds the MAP_KEY."""


# ------------------------------------------------------------------------------------------------ settings


@dataclass(frozen=True)
class Settings:
    """What the environment says about FIRMS: the key (``None`` when the source is off, and then ``off_reason`` says why)."""

    key: Optional[str]
    off_reason: Optional[str]
    sources: tuple[str, ...]
    refresh_s: int


def load_settings(environ: Optional[dict[str, str]] = None) -> Settings:
    """``VIGIE_FIRMS_MAP_KEY`` (the key), ``VIGIE_FIRMS_SOURCES`` (comma-separated, default both) and ``VIGIE_FIRMS_REFRESH_MINUTES``.

    A key that is missing or does not look like a FIRMS key (letters and digits only) turns the source off; the reason never holds the
    value. An unusable list of sources or refresh falls back to the default.
    """
    env = os.environ if environ is None else environ
    raw = (env.get("VIGIE_FIRMS_MAP_KEY") or "").strip()
    key: Optional[str] = None
    reason: Optional[str] = None
    if not raw:
        reason = "VIGIE_FIRMS_MAP_KEY is not set"
    elif not KEY_SHAPE.fullmatch(raw):
        reason = "VIGIE_FIRMS_MAP_KEY is set but is not a FIRMS key (8 to 64 letters and digits)"
    else:
        key = raw

    sources = tuple(dict.fromkeys(part.strip() for part in (env.get("VIGIE_FIRMS_SOURCES") or "").split(",") if part.strip()))
    if not sources or any(source not in ALLOWED_SOURCES for source in sources):
        sources = DEFAULT_SOURCES

    try:
        minutes = int((env.get("VIGIE_FIRMS_REFRESH_MINUTES") or "").strip() or DEFAULT_REFRESH_MINUTES)
    except ValueError:
        minutes = DEFAULT_REFRESH_MINUTES
    if not MIN_REFRESH_MINUTES <= minutes <= MAX_REFRESH_MINUTES:
        minutes = DEFAULT_REFRESH_MINUTES
    return Settings(key=key, off_reason=reason, sources=sources, refresh_s=minutes * 60)


# ------------------------------------------------------------------------------------------------ the rows


class _Palette:
    """The distinct texts of a column (satellite, instrument, version), stored once; a row keeps the index of its text."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self._index: dict[str, int] = {}

    def code(self, text: str) -> int:
        code = self._index.get(text)
        if code is None:
            if len(self.names) >= 255:
                raise ValueError("a column holds more than 255 distinct texts: this is not a FIRMS CSV")
            code = self._index[text] = len(self.names)
            self.names.append(text)
        return code


class HotspotTable:
    """The detections of one source for one UTC day, as typed columns; ``order`` lists the rows by latitude.

    Immutable once built. A number the CSV did not give is ``nan`` (``None`` in the answers); ``confidence`` and ``daynight`` are 0 when
    unknown. ``acq`` is the acquisition time in epoch seconds (UTC).
    """

    __slots__ = ("lat", "lon", "acq", "frp", "ti4", "ti5", "scan", "track", "conf", "dn", "sat", "inst", "ver", "order", "satellites", "instruments", "versions")

    def __init__(self) -> None:
        self.lat = array("i")
        self.lon = array("i")
        self.acq = array("I")
        self.frp = array("f")
        self.ti4 = array("f")
        self.ti5 = array("f")
        self.scan = array("f")
        self.track = array("f")
        self.conf = array("B")
        self.dn = array("B")
        self.sat = array("B")
        self.inst = array("B")
        self.ver = array("B")
        self.order = array("I")
        self.satellites: tuple[str, ...] = ()
        self.instruments: tuple[str, ...] = ()
        self.versions: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.lat)

    def rows_between(self, lat_low: int, lat_high: int) -> range:
        """Positions in ``order`` of the rows whose latitude (1e-5 degrees) is in ``[lat_low, lat_high]``."""
        lat = self.lat
        first = bisect_left(self.order, lat_low, key=lat.__getitem__)
        last = bisect_right(self.order, lat_high, key=lat.__getitem__)
        return range(first, last)

    def number(self, column: array, row: int) -> Optional[float]:
        """A float column's value, ``None`` when the CSV did not give it."""
        value = column[row]
        return None if math.isnan(value) else float(value)

    def satellite(self, row: int) -> Optional[str]:
        return self.satellites[self.sat[row]] if self.satellites else None

    def instrument(self, row: int) -> Optional[str]:
        return self.instruments[self.inst[row]] if self.instruments else None

    def version(self, row: int) -> Optional[str]:
        return self.versions[self.ver[row]] if self.versions else None


@dataclass(frozen=True)
class ParseStats:
    rows: int  # rows kept
    skipped: int  # rows with no readable position or time, or of another day than the one asked
    latest_acq_utc: Optional[int]


def _number(row: list[str], index: Optional[int]) -> float:
    """A float of the row, ``nan`` when the column is absent, empty or not a finite number."""
    if index is None or index >= len(row):
        return NAN
    try:
        value = float(row[index])
    except ValueError:
        return NAN
    return value if math.isfinite(value) else NAN


def parse_csv(content: bytes, date: str) -> tuple[HotspotTable, ParseStats]:
    """The detections of a FIRMS area CSV (VIIRS layout) for the day ``date`` (``YYYY-MM-DD``).

    Raises ``ValueError`` when the answer is not that CSV (an error message, an HTML page, a CSV without position or time columns): the
    caller must not take it for "no fire". A header and no row is a valid empty day.
    """
    text = content.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [column.strip().lower() for column in next(reader)]
    except StopIteration:
        raise ValueError("empty answer") from None
    columns = {name: position for position, name in enumerate(header)}
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        raise ValueError("not a FIRMS CSV (missing columns: " + ", ".join(missing) + ")")
    try:
        midnight = timegm(datetime.strptime(date, "%Y-%m-%d").timetuple())
    except ValueError:
        raise ValueError("bad date asked") from None

    i_lat, i_lon, i_date, i_time = (columns[name] for name in REQUIRED_COLUMNS)
    i_ti4, i_ti5, i_frp = columns.get("bright_ti4"), columns.get("bright_ti5"), columns.get("frp")
    i_scan, i_track = columns.get("scan"), columns.get("track")
    i_conf, i_dn = columns.get("confidence"), columns.get("daynight")
    i_sat, i_inst, i_ver = columns.get("satellite"), columns.get("instrument"), columns.get("version")
    need = max(i_lat, i_lon, i_date, i_time)

    table = HotspotTable()
    satellites, instruments, versions = _Palette(), _Palette(), _Palette()
    skipped = 0
    latest: Optional[int] = None
    sort_keys: list[int] = []
    for row in reader:
        if len(row) <= need:
            skipped += 1
            continue
        try:
            lat, lon = float(row[i_lat]), float(row[i_lon])
            hhmm = int(row[i_time])
        except ValueError:
            skipped += 1
            continue
        hour, minute = divmod(hhmm, 100)
        if row[i_date].strip() != date or not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or not (0 <= hour <= 23 and 0 <= minute <= 59):
            skipped += 1  # also a NaN position, and a row of another day (it belongs to that day's own read)
            continue
        acq = midnight + hour * 3600 + minute * 60
        lat_e5 = round(lat * SCALE)
        table.lat.append(lat_e5)
        table.lon.append(round(lon * SCALE))
        table.acq.append(acq)
        table.frp.append(_number(row, i_frp))
        table.ti4.append(_number(row, i_ti4))
        table.ti5.append(_number(row, i_ti5))
        table.scan.append(_number(row, i_scan))
        table.track.append(_number(row, i_track))
        table.conf.append(CONFIDENCE_CODES.get(row[i_conf].strip().lower(), 0) if i_conf is not None and i_conf < len(row) else 0)
        table.dn.append({"d": 1, "n": 2}.get(row[i_dn].strip().lower(), 0) if i_dn is not None and i_dn < len(row) else 0)
        table.sat.append(satellites.code(row[i_sat].strip()) if i_sat is not None and i_sat < len(row) else 0)
        table.inst.append(instruments.code(row[i_inst].strip()) if i_inst is not None and i_inst < len(row) else 0)
        table.ver.append(versions.code(row[i_ver].strip()) if i_ver is not None and i_ver < len(row) else 0)
        sort_keys.append(lat_e5)
        if latest is None or acq > latest:
            latest = acq
    table.order = array("I", sorted(range(len(sort_keys)), key=sort_keys.__getitem__))
    table.satellites, table.instruments, table.versions = tuple(satellites.names), tuple(instruments.names), tuple(versions.names)
    return table, ParseStats(rows=len(table), skipped=skipped, latest_acq_utc=latest)


@dataclass(frozen=True)
class FireDay:
    """One source for one UTC day, as read at ``read_utc``."""

    source: str
    date: str
    read_utc: int
    bytes_read: int
    skipped: int
    table: HotspotTable

    @property
    def settle_utc(self) -> int:
        """When the data of this day is complete in FIRMS: ``SETTLE_S`` after its end."""
        return day_end_utc(self.date) + SETTLE_S


@dataclass(frozen=True)
class FireSet:
    """Everything read: the tables of today and yesterday (UTC) for each source, and which source could not be read this time."""

    days: tuple[FireDay, ...]
    read_utc: int  # when the last read of this set finished
    failed: tuple[str, ...] = ()  # sources whose last read failed (their previous table, if any, is still in ``days``)
    errors: tuple[str, ...] = ()  # the messages of those failures (the key redacted)

    def sources(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(day.source for day in self.days))

    def newest_read_utc(self, source: str) -> Optional[int]:
        reads = [day.read_utc for day in self.days if day.source == source]
        return max(reads) if reads else None

    def total_rows(self) -> int:
        return sum(len(day.table) for day in self.days)


def day_end_utc(date: str) -> int:
    """Epoch seconds of the end (the next midnight, UTC) of the day ``date``."""
    return timegm(datetime.strptime(date, "%Y-%m-%d").timetuple()) + 86_400


def utc_dates(now: float) -> tuple[str, str]:
    """``(today, yesterday)`` as ``YYYY-MM-DD``, UTC, at the epoch time ``now``."""
    today = datetime.fromtimestamp(now, tz=timezone.utc)
    return today.strftime("%Y-%m-%d"), (today - timedelta(days=1)).strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------------------ the secret


class _RedactingFilter(logging.Filter):
    """Replace the key in the records of ``httpx`` / ``httpcore`` (``httpx`` logs the URL of each request at INFO level)."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self._secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self._secret in message:
            record.msg, record.args = message.replace(self._secret, "REDACTED"), ()
        return True


def _install_log_filter(secret: str) -> None:
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _RedactingFilter) and f._secret == secret for f in logger.filters):
            logger.addFilter(_RedactingFilter(secret))


# ------------------------------------------------------------------------------------------------ the collector


class FIRMSCollector(BaseCollector[FireSet]):
    """Reads the FIRMS detections of the last two UTC days; one cache entry (``CACHE_KEY``) holds everything."""

    def __init__(
        self,
        map_key: str,
        sources: tuple[str, ...] = DEFAULT_SOURCES,
        refresh_s: int = DEFAULT_REFRESH_MINUTES * 60,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            provider_id="nasa_firms",
            name="NASA FIRMS (active fires)",
            domain="fires",
            ttl_seconds=refresh_s,
            failure_threshold=3,
            recovery_timeout_seconds=600,
            timeout_seconds=TIMEOUT,
            http_client=http_client,
        )
        self._key = map_key
        self.secrets = (map_key,)
        self.sources = tuple(sources)
        _install_log_filter(map_key)

    def cached(self) -> tuple[Optional[FireSet], bool]:
        """The last set read and whether it is still within its lifetime; ``(None, False)`` before the first read."""
        entry = self._cache.get(CACHE_KEY)
        if entry is None:
            return None, False
        return entry.value, clock.monotonic() <= entry.fresh_until

    def _needs_read(self, old: Optional[FireDay], date: str, today: str, now: float) -> bool:
        if old is None:
            return True
        age = now - old.read_utc
        if date == today:
            return age >= self.ttl_seconds - 60
        if old.read_utc >= old.settle_utc:
            return False  # yesterday, read after it settled: final
        return now >= old.settle_utc or age >= self.ttl_seconds - 60

    async def _read_day(self, client: httpx.AsyncClient, source: str, date: str, now: float) -> FireDay:
        """One world read of one day. Raises ``FIRMSError`` (the key redacted) or ``BudgetExhaustedError``."""
        url = f"{AREA_URL}/{self._key}/{source}/world/1/{date}"
        problem: Optional[str] = None
        content = b""
        try:
            try:
                response = await self._get(client, url, headers=ACCEPT, weight=WORLD_DAY_TRANSACTIONS)
            except (httpx.ConnectTimeout, httpx.ConnectError):
                response = await self._get(client, url, headers=ACCEPT, weight=WORLD_DAY_TRANSACTIONS)  # another address may answer
        except BudgetExhaustedError:
            raise
        except Exception as exc:  # httpx exceptions can hold the URL, so the message is cleaned before it leaves
            problem = self.redact(f"{type(exc).__name__}: {exc}")
        else:
            content = response.content
            if response.status_code != 200:
                problem = f"HTTP {response.status_code} from {HOST}"
            elif len(content) > MAX_BODY_BYTES:
                problem = f"answer of {len(content)} bytes is too large"
        if problem is not None:
            raise FIRMSError(f"{source} {date}: {problem}")
        try:
            table, stats = await clock.run_blocking(parse_csv, content, date)
        except ValueError as exc:
            snippet = " ".join(content[:100].decode("ascii", errors="replace").split())
            raise FIRMSError(self.redact(f"{source} {date}: {exc} ({snippet!r})")) from None
        return FireDay(source=source, date=date, read_utc=int(now), bytes_read=len(content), skipped=stats.skipped, table=table)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[FireSet]:
        previous: Optional[CacheEntry[FireSet]] = kwargs.get("previous")
        now = clock.wall()
        today, yesterday = utc_dates(now)
        prior = {(day.source, day.date): day for day in previous.value.days} if previous is not None else {}

        days: list[FireDay] = []
        failed: list[str] = []
        errors: list[str] = []
        budget_hit = False
        new_reads = 0
        for source in self.sources:
            for date in (today, yesterday):
                old = prior.get((source, date))
                if not self._needs_read(old, date, today, now):
                    if old is not None:
                        days.append(old)
                    continue
                try:
                    day = await self._read_day(client, source, date, now)
                    if old is not None and len(day.table) < SHRINK_RATIO * len(old.table):
                        # FIRMS loses connections: a cut answer that ends cleanly is a valid CSV with fewer rows. The day's file only grows.
                        raise FIRMSError(f"{source} {date}: {len(day.table)} rows after a reading of {len(old.table)}, the previous one is kept")
                except (FIRMSError, BudgetExhaustedError) as exc:
                    failed.append(source)
                    if isinstance(exc, BudgetExhaustedError):
                        budget_hit = True
                        errors.append(f"{source} {date}: call budget used up")
                    else:
                        errors.append(str(exc))
                    if old is not None:
                        days.append(old)  # the previous reading of the same day is better than none
                    if date == today and (source, yesterday) in prior:
                        days.append(prior[(source, yesterday)])
                    break  # a source that does not answer is not asked again for its other day in the same refresh
                days.append(day)
                new_reads += 1
        if failed and new_reads == 0:
            if budget_hit and all(error.endswith("call budget used up") for error in errors):
                raise BudgetExhaustedError("; ".join(errors))  # the base class counts the refusal and serves the last set
            raise FIRMSError("; ".join(errors))
        if budget_hit:
            self.budget_refusals += 1  # some reads were made: the base class does not see the refusal, so it is counted here
        value = FireSet(days=tuple(days), read_utc=int(now), failed=tuple(failed), errors=tuple(errors))
        return Collected(value=value, fresh_for_s=RETRY_S if failed else float(self.ttl_seconds))

    def _record_success(
        self,
        elapsed_ms: float,
        cache_key: str,
        data: FireSet,
        ttl_seconds: Optional[float] = None,
        validators: Optional[dict[str, str]] = None,
    ) -> None:
        super()._record_success(elapsed_ms, cache_key, data, ttl_seconds, validators)
        if data.errors:  # a source could not be read: the other one did, and the set says so
            self.last_error = self.redact("; ".join(data.errors))[:240]
