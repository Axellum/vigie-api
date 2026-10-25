"""Read the GFS 0.25 degree files and turn one run into daily quantities per grid point.

NOAA's Global Forecast System (GFS) is published every 6 hours as GRIB2 files, one per lead time ("can be used as desired", with
credit to NOAA and without implying its endorsement: https://registry.opendata.aws/noaa-gfs-bdp-pds/, read 2026-10-25). The same
files are read two ways: cut to an area and to a few fields by the NOMADS grib filter (the live pilot), or as whole-world fields
fetched by byte range from the archive on AWS (the backtest). Both give GRIB2 messages; this module only decodes them, so the two
paths share every computation.

What is read, per file of lead ``f`` hours (the inventory of a 2024-01-15 run, read on 2026-10-25, every 3 hours to 72):

- ``tp`` (``APCP``), the precipitation **accumulated from the start of the run** (``startStep`` 0): the rain of any interval is
  the difference of two of them, so a day is exact whatever the run. The file also holds the accumulation of the last 3 or 6 hours,
  which is not used.
- ``gust`` (``GUST``), the wind gust at the surface **at** the lead time (instantaneous), in m/s. A day's value is the strongest
  of its 3-hourly samples: it can miss a peak between two of them.
- ``tmax`` / ``tmin`` (``TMAX`` / ``TMIN`` at 2 m), the extreme of the last 3 or 6 hours, and ``2t`` (``TMP`` at 2 m) at the
  lead time, in K.
- ``csnow`` (``CSNOW``), the categorical snow (1 when the precipitation falls as snow), **averaged** over the last 3 or 6 hours.

**Days are local days of Turkey** (Europe/Istanbul, UTC+3 all year since 2016): a day runs from 21:00 UTC to 21:00 UTC. Every
lead time of a run falls on a multiple of 3 hours UTC, so the 3-hour intervals never straddle the boundary. The intervals of 6
hours (``tmax``, ``tmin``, ``csnow`` at leads 6, 12, 18 and 24 of a cycle) do: for ``csnow`` the last 3 hours are recovered
exactly from the two averages (``2 * avg(0-6) - avg(0-3)``); a ``tmax`` or ``tmin`` of 6 hours that straddles 21:00 UTC is not
used, and the day keeps the 3-hour intervals and the instantaneous ``2t`` it has.

**Snow** is ours: the precipitation of each 3 hours times the share of it that fell as snow (``csnow``), in mm of water. GFS
gives no snowfall depth in these files; 1 mm of water is often written as about 1 cm of fresh snow, but that ratio is not used.

A value that is not in a file is not invented: a day misses a quantity when no file gave it, and says how many hours it covers.
"""

from __future__ import annotations

import struct
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np

# Turkey keeps UTC+3 all year (permanent summer time since 2016-09-07).
LOCAL_OFFSET = timedelta(hours=3)
STEP_H = 3

# The fields the pre-alert reads, by their ecCodes short name.
FIELDS = ("tp", "gust", "tmax", "tmin", "2t", "csnow")

# The NOMADS grib filter asks for variables and levels by their GRIB names (https://nomads.ncep.noaa.gov/gribfilter.php?ds=gfs_0p25).
NOMADS_VARIABLES = ("APCP", "GUST", "TMAX", "TMIN", "TMP", "CSNOW")
NOMADS_LEVELS = ("surface", "2_m_above_ground")

# ecCodes crashed (no definitions found, then a segmentation fault) when two threads decoded at once on 2026-10-25 (eccodes 2.50,
# Windows): one decoding at a time, whatever thread asks.
_DECODE_LOCK = threading.Lock()


@dataclass(frozen=True)
class Box:
    """An area in degrees: south, north, west, east (east longitudes, 0 to 360 or -180 to 180)."""

    south: float
    north: float
    west: float
    east: float


@dataclass(frozen=True)
class Message:
    """One decoded GRIB2 message, cut to the area: ``values[j, i]`` at ``lats[j]``, ``lons[i]``."""

    short_name: str
    step_type: str  # "accum", "instant", "max", "min", "avg"
    start_h: int
    end_h: int
    lats: np.ndarray
    lons: np.ndarray
    values: np.ndarray


def split_messages(data: bytes) -> list[bytes]:
    """Split concatenated GRIB2 messages (the answer of the NOMADS filter) on their stated lengths."""
    messages: list[bytes] = []
    offset = 0
    while True:
        start = data.find(b"GRIB", offset)
        if start < 0:
            break
        if len(data) < start + 16:
            raise ValueError("truncated GRIB message header")
        edition = data[start + 7]
        if edition != 2:
            raise ValueError(f"GRIB edition {edition} is not read (only GRIB2)")
        (length,) = struct.unpack(">Q", data[start + 8 : start + 16])
        end = start + length
        if end > len(data) or data[end - 4 : end] != b"7777":
            raise ValueError("truncated GRIB message")
        messages.append(data[start:end])
        offset = end
    return messages


def _axis(first: float, last: float, count: int) -> np.ndarray:
    return np.linspace(first, last, count) if count > 1 else np.array([first])


def decode(data: bytes, box: Optional[Box] = None) -> list[Message]:
    """Decode every GRIB2 message of ``data`` that is one of ``FIELDS`` on a regular latitude-longitude grid, cut to ``box``."""
    import eccodes  # optional dependency: imported only when a file is read

    with _DECODE_LOCK:
        return _decode_locked(eccodes, data, box)


def _decode_locked(eccodes: Any, data: bytes, box: Optional[Box]) -> list[Message]:
    out: list[Message] = []
    for raw in split_messages(data):
        handle = eccodes.codes_new_from_message(raw)
        try:
            short_name = str(eccodes.codes_get(handle, "shortName"))
            if short_name not in FIELDS:
                continue
            if eccodes.codes_get(handle, "gridType") != "regular_ll":
                raise ValueError(f"{short_name}: grid {eccodes.codes_get(handle, 'gridType')} is not read")
            ni = int(eccodes.codes_get(handle, "Ni"))
            nj = int(eccodes.codes_get(handle, "Nj"))
            lats = _axis(
                float(eccodes.codes_get(handle, "latitudeOfFirstGridPointInDegrees")),
                float(eccodes.codes_get(handle, "latitudeOfLastGridPointInDegrees")),
                nj,
            )
            lon_first = float(eccodes.codes_get(handle, "longitudeOfFirstGridPointInDegrees"))
            lon_last = float(eccodes.codes_get(handle, "longitudeOfLastGridPointInDegrees"))
            if lon_last < lon_first:
                lon_last += 360.0
            lons = _axis(lon_first, lon_last, ni)
            lons = np.where(lons > 180.0, lons - 360.0, lons)
            values = np.asarray(eccodes.codes_get_values(handle), dtype=np.float64).reshape(nj, ni)
            if bool(eccodes.codes_get(handle, "bitmapPresent")):
                missing = float(eccodes.codes_get(handle, "missingValue"))
                values = np.where(values == missing, np.nan, values)
            step_type = str(eccodes.codes_get(handle, "stepType"))
            start_h = int(eccodes.codes_get(handle, "startStep"))
            end_h = int(eccodes.codes_get(handle, "endStep"))
        finally:
            eccodes.codes_release(handle)
        if box is not None:
            rows = np.where((lats >= box.south) & (lats <= box.north))[0]
            cols = np.where((lons >= box.west) & (lons <= box.east))[0]
            if rows.size == 0 or cols.size == 0:
                raise ValueError(f"{short_name}: the message does not cover the area")
            lats, lons, values = lats[rows], lons[cols], values[np.ix_(rows, cols)]
        out.append(Message(short_name, step_type, start_h, end_h, lats, lons, values))
    return out


@dataclass
class DayValues:
    """The quantities of one local day at every grid point. ``None``: no file gave it."""

    day: date
    hours: int = 0  # how many of the 24 hours the run covers for this day (rain and snow)
    rain_mm: Optional[np.ndarray] = None
    snow_mm: Optional[np.ndarray] = None  # water that fell as snow (ours, see the module docstring)
    gust_ms: Optional[np.ndarray] = None
    tmax_k: Optional[np.ndarray] = None
    tmin_k: Optional[np.ndarray] = None
    leads_h: list[int] = field(default_factory=list)


def local_day_of_interval_end(run: datetime, end_h: int) -> date:
    """The local day an interval ending ``end_h`` hours after ``run`` belongs to (its last second is in that day)."""
    end = run + timedelta(hours=end_h)
    return (end + LOCAL_OFFSET - timedelta(seconds=1)).date()


def _maximum(current: Optional[np.ndarray], new: np.ndarray) -> np.ndarray:
    return new.copy() if current is None else np.fmax(current, new)


def _minimum(current: Optional[np.ndarray], new: np.ndarray) -> np.ndarray:
    return new.copy() if current is None else np.fmin(current, new)


def _add(current: Optional[np.ndarray], new: np.ndarray) -> np.ndarray:
    return new.copy() if current is None else current + new


def _pick(messages: Iterable[Message], short_name: str, **match: object) -> Optional[Message]:
    for message in messages:
        if message.short_name != short_name:
            continue
        if all(getattr(message, key) == value for key, value in match.items()):
            return message
    return None


def run_days(run: datetime, by_lead: Mapping[int, list[Message]]) -> dict[date, DayValues]:
    """Turn the messages of one run, keyed by lead time in hours (3, 6, ... with no gap), into local days.

    ``run`` is the run's time (UTC, aware). A lead without its accumulated precipitation ends the reading there: an interval
    is never guessed. The first and last days of a run are partial: ``hours`` says how much of them was covered.
    """
    if run.tzinfo is None or run.utcoffset() != timedelta(0):
        raise ValueError("the run time must be an aware UTC datetime")
    days: dict[date, DayValues] = {}
    previous_tp: Optional[np.ndarray] = None
    previous_lead = 0
    previous_csnow_avg: dict[int, np.ndarray] = {}  # interval start -> average over (start, lead) of the previous lead
    for lead in sorted(by_lead):
        if lead != previous_lead + STEP_H:
            break
        messages = by_lead[lead]
        tp = _pick(messages, "tp", start_h=0, end_h=lead)
        if tp is None:
            break
        rain3 = tp.values if previous_tp is None else tp.values - previous_tp
        rain3 = np.where(rain3 < 0, 0.0, rain3)  # packing noise: an accumulation does not go down
        day = days.setdefault(local_day_of_interval_end(run, lead), DayValues(local_day_of_interval_end(run, lead)))
        day.hours += STEP_H
        day.leads_h.append(lead)
        day.rain_mm = _add(day.rain_mm, rain3)

        csnow = _pick(messages, "csnow", step_type="avg", end_h=lead)
        if csnow is not None:
            span = csnow.end_h - csnow.start_h
            if span == STEP_H:
                share = csnow.values
            elif span == 2 * STEP_H and csnow.start_h in previous_csnow_avg:
                share = 2.0 * csnow.values - previous_csnow_avg[csnow.start_h]
            else:
                share = None
            if share is not None:
                day.snow_mm = _add(day.snow_mm, rain3 * np.clip(share, 0.0, 1.0))
            previous_csnow_avg = {csnow.start_h: csnow.values} if span == STEP_H else {}

        gust = _pick(messages, "gust", end_h=lead)
        if gust is not None:
            day.gust_ms = _maximum(day.gust_ms, gust.values)
        for name, attr, combine in (("tmax", "tmax_k", _maximum), ("tmin", "tmin_k", _minimum)):
            extreme = _pick(messages, name, end_h=lead)
            if extreme is not None and local_day_of_interval_end(run, extreme.start_h + 1) == day.day:
                setattr(day, attr, combine(getattr(day, attr), extreme.values))
        t2 = _pick(messages, "2t", end_h=lead)
        if t2 is not None:
            day.tmax_k = _maximum(day.tmax_k, t2.values)
            day.tmin_k = _minimum(day.tmin_k, t2.values)

        previous_tp = tp.values
        previous_lead = lead
    return days


def run_time(day: date, cycle: int) -> datetime:
    """The time of the GFS run of ``day`` at ``cycle`` (0, 6, 12 or 18 UTC)."""
    if cycle not in (0, 6, 12, 18):
        raise ValueError("a GFS cycle is 0, 6, 12 or 18")
    return datetime(day.year, day.month, day.day, cycle, tzinfo=timezone.utc)
