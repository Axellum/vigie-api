"""What the API does with the FIRMS detections: the hotspots of a box, their summary per cell, and the summary around a point.

The detections are read by ``app/collectors/firms.py`` (a world read per source and day, every 60 minutes by default) and kept as typed
columns sorted by latitude. This module answers the three questions asked of them:

- ``features``: the detections of a box and of the last hours, as GeoJSON points, capped (the strongest, by fire radiative power, are kept);
- ``grid``: the same detections counted per cell of ``cell_deg`` degrees (count, highest FRP, most recent time), for small zooms;
- ``nearby``: the count, the distance of the nearest and the highest FRP within a radius of a point, for ``GET /v1/point``.

**The request never waits for FIRMS once a reading exists.** FIRMS is slow and loses connections (measured 2026-10-20), a world day weighs
about 5 MB and the answers of ``GET /v1/point`` feed displays: when the last reading is older than its lifetime, the previous one is served at
once and a refresh starts in the background (one at a time, shared with the collector's own coalescing). Only the very first request, with
nothing read yet, waits (``GET /v1/fires``) or answers without the block (``GET /v1/point``). ``stale`` says that the last attempt failed.

Nothing here is an alert and nothing raises a level: the numbers are our own counts of the satellites' pixels (``derived`` says which).
"""

from __future__ import annotations

import asyncio
import heapq
import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

from app import clock
from app.attribution import FIRES, FIRMS_DISCLAIMER, credit_text
from app.collectors.base import CollectorUnavailableError
from app.collectors.firms import CACHE_KEY, CONFIDENCE_NAMES, DAYNIGHT_NAMES, SCALE, FireSet, FIRMSCollector
from app.models.schemas import FiresNearby
from app.services.geodesy import haversine_km

WINDOW_DEFAULT_H = 24.0
WINDOW_MAX_H = 48.0  # two UTC days are kept
FEATURES_DEFAULT = 1000
FEATURES_MAX = 5000
CELLS_DEFAULT = 5000
CELLS_MAX = 20000
CELL_SIZES: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0)
CONFIDENCE_LEVELS: dict[str, int] = {"low": 0, "nominal": 2, "high": 3}  # "low" lets everything through, the unknown too
NEARBY_RADIUS_DEFAULT_KM = 50.0
NEARBY_RADIUS_MAX_KM = 500.0
KM_PER_DEGREE = 111.195  # the great-circle degree on the mean Earth radius of ``geodesy``
SINCE_STEP_S = 300  # the start of the window is rounded down to 5 minutes, so that answers can be shared
GRID_CACHE_SIZE = 64


class FiresOff(Exception):
    """The source is off on this instance (no key); ``reason`` says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FiresUnavailable(Exception):
    """FIRMS has never been read successfully since the start, and could not be read now."""


# ------------------------------------------------------------------------------------------------ the box


@dataclass(frozen=True)
class Box:
    """A search box: latitudes, and one or two longitude ranges (two when it crosses the antimeridian), in 1e-5 degrees."""

    south: float
    north: float
    lon_ranges: tuple[tuple[int, int], ...]
    asked: tuple[float, float, float, float]

    @property
    def lat_low(self) -> int:
        return round(self.south * SCALE)

    @property
    def lat_high(self) -> int:
        return round(self.north * SCALE)

    def as_list(self) -> list[float]:
        return list(self.asked)


def lon_ranges(west: float, east: float) -> tuple[tuple[int, int], ...]:
    """The longitude ranges (1e-5 degrees, within -180 / 180) of the span from ``west`` eastwards to ``east``.

    ``west`` greater than ``east`` crosses the antimeridian (RFC 7946, 5.2); a span of 360 degrees or more is the whole globe; longitudes
    beyond +-180 (a map that repeats the world) are brought back into range.
    """
    span = east - west if east >= west else east - west + 360.0
    if span >= 360.0:
        return ((-180 * SCALE, 180 * SCALE),)
    start = (west + 180.0) % 360.0 - 180.0
    end = start + span
    if end <= 180.0:
        return ((round(start * SCALE), round(end * SCALE)),)
    return ((round(start * SCALE), 180 * SCALE), (-180 * SCALE, round((end - 360.0) * SCALE)))


def parse_bbox(text: str) -> Box:
    """``west,south,east,north`` in degrees. Raises ``ValueError`` with the reason when it is not a box."""
    parts = [part.strip() for part in (text or "").split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be four numbers: west,south,east,north")
    try:
        west, south, east, north = (float(part) for part in parts)
    except ValueError:
        raise ValueError("bbox must be four numbers: west,south,east,north") from None
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        raise ValueError("bbox must be four finite numbers")
    if not (-90.0 <= south < north <= 90.0):
        raise ValueError("bbox latitudes must satisfy -90 <= south < north <= 90")
    if abs(west) > 540.0 or abs(east) > 540.0:
        raise ValueError("bbox longitudes must be within -540 and 540")
    return Box(south=south, north=north, lon_ranges=lon_ranges(west, east), asked=(west, south, east, north))


def box_around(lat: float, lon: float, radius_km: float) -> Box:
    """The smallest box that holds the disc of ``radius_km`` around a point (a little larger: the discs are cut by distance afterwards)."""
    dlat = min(90.0, radius_km / KM_PER_DEGREE * 1.02 + 1e-6)
    south, north = max(-90.0, lat - dlat), min(90.0, lat + dlat)
    cos_edge = math.cos(math.radians(min(89.9, max(abs(south), abs(north)))))
    dlon = 180.0 if abs(lat) + dlat >= 89.0 else min(180.0, dlat / max(cos_edge, 1e-6))
    return Box(south=south, north=north, lon_ranges=lon_ranges(lon - dlon, lon + dlon), asked=(lon - dlon, south, lon + dlon, north))


# ------------------------------------------------------------------------------------------------ the scans (synchronous, run in a thread)


def _in_ranges(ranges: tuple[tuple[int, int], ...]) -> tuple[int, int, int, int]:
    first = ranges[0]
    second = ranges[1] if len(ranges) > 1 else first
    return first[0], first[1], second[0], second[1]


def select(fs: FireSet, box: Box, since: int, min_confidence: int, min_frp: float, limit: int) -> tuple[int, list[tuple[float, int, int, int]]]:
    """``(total, kept)``: how many detections pass, and the ``limit`` strongest as ``(frp, acq, table, row)``, strongest first."""
    a1, b1, a2, b2 = _in_ranges(box.lon_ranges)
    heap: list[tuple[float, int, int, int]] = []
    total = 0
    for table_index, day in enumerate(fs.days):
        table = day.table
        order, lon, acq, conf, frp = table.order, table.lon, table.acq, table.conf, table.frp
        for k in table.rows_between(box.lat_low, box.lat_high):
            i = order[k]
            when = acq[i]
            if when < since:
                continue
            x = lon[i]
            if not (a1 <= x <= b1 or a2 <= x <= b2) or conf[i] < min_confidence:
                continue
            power = frp[i]
            if min_frp > 0.0 and not power >= min_frp:  # a missing power (nan) does not pass a minimum
                continue
            total += 1
            key = (power if power == power else -1.0, when, table_index, i)
            if len(heap) < limit:
                heapq.heappush(heap, key)
            elif key > heap[0]:
                heapq.heapreplace(heap, key)
    return total, sorted(heap, reverse=True)


def feature_of(fs: FireSet, table_index: int, row: int) -> dict[str, Any]:
    """One detection as a GeoJSON feature: the CSV's own fields, rounded to the precision the file has."""
    table = fs.days[table_index].table
    lon, lat = table.lon[row] / SCALE, table.lat[row] / SCALE
    satellite = table.satellite(row)
    acq = int(table.acq[row])

    def rounded(column: Any) -> Optional[float]:
        value = table.number(column, row)
        return None if value is None else round(value, 2)

    return {
        "type": "Feature",
        "id": f"{satellite or 'x'}_{lat:.5f}_{lon:.5f}_{acq}",
        "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
        "properties": {
            "acq_utc": acq,
            "satellite": satellite,
            "instrument": table.instrument(row),
            "confidence": CONFIDENCE_NAMES[table.conf[row]],
            "frp_mw": rounded(table.frp),
            "bright_ti4_k": rounded(table.ti4),
            "bright_ti5_k": rounded(table.ti5),
            "scan_km": rounded(table.scan),
            "track_km": rounded(table.track),
            "daynight": DAYNIGHT_NAMES[table.dn[row]],
            "version": table.version(row),
        },
    }


@dataclass(frozen=True)
class GridResult:
    total_cells: int
    total_detections: int
    features: tuple[dict[str, Any], ...]


def grid_cells(fs: FireSet, box: Box, since: int, cell_deg: float, min_confidence: int, min_frp: float, limit: int) -> GridResult:
    """The detections counted per cell of ``cell_deg`` degrees (aligned on multiples of it from -90 and -180), the busiest cells kept."""
    cell = round(cell_deg * SCALE)
    rows, columns = round(180.0 / cell_deg), round(360.0 / cell_deg)
    a1, b1, a2, b2 = _in_ranges(box.lon_ranges)
    cells: dict[tuple[int, int], list[Any]] = {}
    total = 0
    for day in fs.days:
        table = day.table
        order, lat, lon, acq, conf, frp = table.order, table.lat, table.lon, table.acq, table.conf, table.frp
        for k in table.rows_between(box.lat_low, box.lat_high):
            i = order[k]
            when = acq[i]
            if when < since:
                continue
            x = lon[i]
            if not (a1 <= x <= b1 or a2 <= x <= b2) or conf[i] < min_confidence:
                continue
            power = frp[i]
            if min_frp > 0.0 and not power >= min_frp:
                continue
            total += 1
            key = (min(rows - 1, (lat[i] + 90 * SCALE) // cell), min(columns - 1, (x + 180 * SCALE) // cell))
            entry = cells.get(key)
            if entry is None:
                cells[key] = [1, power if power == power else None, when]
            else:
                entry[0] += 1
                if power == power and (entry[1] is None or power > entry[1]):
                    entry[1] = power
                if when > entry[2]:
                    entry[2] = when
    ranked = sorted(cells.items(), key=lambda item: (-item[1][0], -(item[1][1] if item[1][1] is not None else -1.0), item[0]))[:limit]
    features = tuple(
        {
            "type": "Feature",
            "id": f"{cell_deg:g}_{row}_{column}",
            "geometry": {"type": "Point", "coordinates": [round(-180.0 + (column + 0.5) * cell_deg, 5), round(-90.0 + (row + 0.5) * cell_deg, 5)]},
            "properties": {"count": count, "max_frp_mw": None if power is None else round(float(power), 2), "latest_utc": int(latest)},
        }
        for (row, column), (count, power, latest) in ranked
    )
    return GridResult(total_cells=len(cells), total_detections=total, features=features)


def near(fs: FireSet, lat: float, lon: float, radius_km: float, since: int) -> tuple[int, Optional[float], Optional[float], Optional[int]]:
    """``(count, nearest_km, max_frp_mw, latest_utc)`` of the detections within ``radius_km`` of a point, acquired since ``since``."""
    box = box_around(lat, lon, radius_km)
    a1, b1, a2, b2 = _in_ranges(box.lon_ranges)
    count = 0
    nearest: Optional[float] = None
    strongest: Optional[float] = None
    latest: Optional[int] = None
    for day in fs.days:
        table = day.table
        order, tlat, tlon, acq, frp = table.order, table.lat, table.lon, table.acq, table.frp
        for k in table.rows_between(box.lat_low, box.lat_high):
            i = order[k]
            when = acq[i]
            if when < since:
                continue
            x = tlon[i]
            if not (a1 <= x <= b1 or a2 <= x <= b2):
                continue
            distance = haversine_km(lat, lon, tlat[i] / SCALE, x / SCALE)
            if distance > radius_km:
                continue
            count += 1
            if nearest is None or distance < nearest:
                nearest = distance
            power = frp[i]
            if power == power and (strongest is None or power > strongest):
                strongest = float(power)
            if latest is None or when > latest:
                latest = int(when)
    return count, nearest, strongest, latest


# ------------------------------------------------------------------------------------------------ the service


class FireService:
    """The fire detections for the routes and for ``GET /v1/point``; ``collector`` is ``None`` when the source is off."""

    def __init__(self, collector: Optional[FIRMSCollector], off_reason: Optional[str]) -> None:
        self.collector = collector
        self.off_reason = off_reason or "NASA FIRMS is off on this instance"
        self._task: Optional["asyncio.Task[None]"] = None
        self._grids: OrderedDict[tuple[Any, ...], tuple[FireSet, GridResult]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self.collector is not None

    # -- reading ---------------------------------------------------------------------------------

    def _kick(self) -> None:
        """Start a refresh in the background unless one is running. It never raises: the collector records the failure."""
        if self.collector is None:
            return
        task = self._task
        if task is not None and not task.done() and task.get_loop() is asyncio.get_running_loop():
            return  # one is running (a task of another, closed event loop is a leftover: never wait for it)
        collector = self.collector

        async def refresh() -> None:
            try:
                await collector.fetch_ex(CACHE_KEY)
            except Exception:
                pass  # recorded by the collector (circuit breaker, last_error); the previous reading stays in use

        self._task = asyncio.get_running_loop().create_task(refresh())

    def warm_up(self) -> None:
        """Start the first read when the application starts, so that the first visitor does not wait for it."""
        if self.collector is not None:
            self._kick()

    async def drain(self) -> None:
        """Wait for the refresh that is running, if any (tests and the simulation)."""
        task = self._task
        if task is not None and not task.done() and task.get_loop() is asyncio.get_running_loop():
            await task

    async def _current(self, wait: bool) -> Optional[tuple[FireSet, bool]]:
        """``(set, stale)``: the last reading, whatever its age. ``None`` before the first reading (then ``wait`` decides whether to wait for it)."""
        collector = self.collector
        if collector is None:
            return None
        value, fresh = collector.cached()
        if value is None:
            if not wait:
                self._kick()
                return None
            try:
                value = await collector.fetch(CACHE_KEY)
            except CollectorUnavailableError as exc:
                raise FiresUnavailable(str(exc)) from None
            return value, bool(value.failed)
        if not fresh:
            self._kick()
        return value, collector.consecutive_failures > 0 or bool(value.failed)

    def _sources(self, fs: FireSet) -> list[dict[str, Any]]:
        """The products the instance reads and how each stands: one that was never read (it failed every time so far) is listed too, with no time."""
        read = fs.sources()
        configured = self.collector.sources if self.collector is not None else ()
        return [
            {"source": source, "read_utc": fs.newest_read_utc(source), "ok": source in read and source not in fs.failed}
            for source in dict.fromkeys((*configured, *read))
        ]

    # -- the routes ------------------------------------------------------------------------------

    @staticmethod
    def _since(now: float, hours: float) -> int:
        return int(now - hours * 3600.0) // SINCE_STEP_S * SINCE_STEP_S

    async def features(self, box: Box, hours: float, min_confidence: str, min_frp: float, limit: int) -> dict[str, Any]:
        """The ``GET /v1/fires`` answer. Raises ``FiresOff`` or ``FiresUnavailable``."""
        if self.collector is None:
            raise FiresOff(self.off_reason)
        current = await self._current(wait=True)
        assert current is not None
        fs, stale = current
        now = clock.wall()
        since = self._since(now, hours)
        total, kept = await clock.run_blocking(select, fs, box, since, CONFIDENCE_LEVELS[min_confidence], min_frp, limit)
        return {
            "type": "FeatureCollection",
            "metadata": {
                "standard": "IETF RFC 7946 GeoJSON",
                "generated_utc": int(now),
                "bbox": box.as_list(),
                "window_hours": hours,
                "since_utc": since,
                "min_confidence": min_confidence,
                "min_frp_mw": min_frp,
                "limit": limit,
                "count": len(kept),
                "total_matching": total,
                "truncated": total > len(kept),
                "order": "frp_mw descending, then most recent first",
                "data_read_utc": fs.read_utc,
                "stale": stale,
                "sources": self._sources(fs),
                "skipped_rows": sum(day.skipped for day in fs.days),
                "attribution": credit_text(*FIRES),
                "disclaimer": FIRMS_DISCLAIMER,
            },
            "features": [feature_of(fs, table_index, row) for _, _, table_index, row in kept],
        }

    async def grid(self, box: Box, hours: float, cell_deg: float, min_confidence: str, min_frp: float, limit: int) -> dict[str, Any]:
        """The ``GET /v1/fires/grid`` answer. Raises ``FiresOff`` or ``FiresUnavailable``."""
        if self.collector is None:
            raise FiresOff(self.off_reason)
        current = await self._current(wait=True)
        assert current is not None
        fs, stale = current
        now = clock.wall()
        since = self._since(now, hours)
        key = (id(fs), box.asked, since, cell_deg, min_confidence, min_frp, limit)
        cached = self._grids.get(key)
        if cached is not None and cached[0] is fs:
            self._grids.move_to_end(key)
            result = cached[1]
        else:
            result = await clock.run_blocking(grid_cells, fs, box, since, cell_deg, CONFIDENCE_LEVELS[min_confidence], min_frp, limit)
            self._grids[key] = (fs, result)
            while len(self._grids) > GRID_CACHE_SIZE:
                self._grids.popitem(last=False)
        return {
            "type": "FeatureCollection",
            "metadata": {
                "standard": "IETF RFC 7946 GeoJSON",
                "generated_utc": int(now),
                "cell_deg": cell_deg,
                "bbox": box.as_list(),
                "window_hours": hours,
                "since_utc": since,
                "min_confidence": min_confidence,
                "min_frp_mw": min_frp,
                "limit": limit,
                "count": len(result.features),
                "total_cells": result.total_cells,
                "total_detections": result.total_detections,
                "truncated": result.total_cells > len(result.features),
                "order": "count descending, then max_frp_mw descending",
                "data_read_utc": fs.read_utc,
                "stale": stale,
                "sources": self._sources(fs),
                "skipped_rows": sum(day.skipped for day in fs.days),
                "attribution": credit_text(*FIRES),
                "disclaimer": FIRMS_DISCLAIMER,
            },
            "features": list(result.features),
        }

    async def nearby(self, lat: float, lon: float, radius_km: float = NEARBY_RADIUS_DEFAULT_KM) -> Optional[FiresNearby]:
        """The summary of ``GET /v1/point``; ``None`` when the source is off or nothing has been read yet (it never waits)."""
        current = await self._current(wait=False)
        if current is None:
            return None
        fs, stale = current
        since = int(clock.wall() - 24 * 3600)
        count, nearest, strongest, latest = near(fs, lat, lon, radius_km, since)
        return FiresNearby(
            radius_km=radius_km,
            count_24h=count,
            nearest_km=None if nearest is None else round(nearest, 1),
            max_frp_mw=None if strongest is None else round(strongest, 2),
            latest_utc=latest,
            data_read_utc=fs.read_utc,
            stale=stale,
            sources=list(fs.sources()),
            attribution=credit_text(*FIRES),
            disclaimer=FIRMS_DISCLAIMER,
        )
