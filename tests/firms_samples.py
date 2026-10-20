"""The recorded FIRMS answers of ``tests/fixtures/firms`` and a simulated FIRMS server, as the tests replay them.

The CSV files are real rows of the FIRMS area API (see the README of that folder); the key below is made up and
is not in any recording. ``FirmsServer`` answers like the real service: ``/api/area/csv/{key}/{source}/world/1/{date}``,
the file of the day with its ``acq_date`` set to the day asked.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from tests.helpers import Provider

FIRMS = Path(__file__).resolve().parent / "fixtures" / "firms"
KEY = "FIRMSTESTKEYFIRMSTESTKEY"  # made up (low entropy on purpose: the secret scan reads a key-like literal as a leak); the real key is never in the repository
N20, N21 = "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"
FIXTURE_DAY = "2026-10-20"
HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"
FILES = {N20: "viirs_noaa20_nrt_world_2026-10-20.csv", N21: "viirs_noaa21_nrt_world_2026-10-20.csv"}


MIDNIGHT = int(datetime.fromisoformat("2026-10-20T00:00:00+00:00").timestamp())
SINCE_ALL = 0


def at(day: str, clock: str) -> float:
    """``at('2026-10-20', '18:12')``: a UTC instant as an epoch time."""
    return datetime.fromisoformat(f"{day}T{clock}:00+00:00").timestamp()


NOON = at("2026-10-20", "12:00")  # the fixture's acquisition times (00:01 to 18:09) are all within 24 hours before 18:12 and after 2026-10-19 18:12
EVENING = at("2026-10-20", "18:12")


def fixture_text(source: str) -> str:
    return (FIRMS / FILES[source]).read_text(encoding="utf-8")


def fixture_rows(source: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(fixture_text(source))))


def csv_for(source: str, date: str) -> bytes:
    """The recorded rows of ``source`` as the file of ``date`` (only the date of each row changes)."""
    return fixture_text(source).replace(FIXTURE_DAY, date).encode("utf-8")


def csv_rows(rows: list[tuple[float, float, int, float, str]], date: str = FIXTURE_DAY, satellite: str = "N20") -> bytes:
    """A made-up file of ``(lat, lon, hhmm, frp, confidence)`` rows, to test what a recording does not hold."""
    lines = [HEADER]
    for lat, lon, hhmm, frp, confidence in rows:
        lines.append(f"{lat},{lon},330.5,0.4,0.4,{date},{hhmm},{satellite},VIIRS,{confidence},2.0NRT,295.1,{frp},D")
    return ("\n".join(lines) + "\n").encode("utf-8")


class FirmsServer(Provider):
    """A simulated FIRMS: serves the recorded rows for any source and day; ``failing`` makes a source answer an error.

    ``answers`` replaces the file of a ``(source, date)``; ``status`` is what a source gets instead of a file (a code, or a
    body that is not a CSV).
    """

    def __init__(self, latency_ticks: int = 2) -> None:
        super().__init__(answer=self._respond, latency_ticks=latency_ticks)
        self.failing: set[str] = set()
        self.answers: dict[tuple[str, str], bytes] = {}
        self.status: dict[str, tuple[int, bytes]] = {}
        self.on_request: Optional[Callable[[httpx.Request], None]] = None

    def _respond(self, request: httpx.Request) -> httpx.Response:
        if self.on_request is not None:
            self.on_request(request)
        parts = request.url.path.split("/")  # '', 'api', 'area', 'csv', key, source, 'world', '1', date
        if len(parts) != 9 or parts[1:4] != ["api", "area", "csv"] or parts[6] != "world" or parts[7] != "1":
            return httpx.Response(404, text="Not found")
        _, _, _, _, key, source, _, _, date = parts
        if key != KEY:
            return httpx.Response(200, text="Invalid MAP_KEY.")
        if source in self.failing:
            raise httpx.ConnectError("[Errno 111] Connection refused", request=request)
        if source in self.status:
            code, body = self.status[source]
            return httpx.Response(code, content=body)
        if (source, date) in self.answers:
            return httpx.Response(200, content=self.answers[(source, date)], headers={"Content-Type": "text/plain;charset=UTF-8"})
        if source not in FILES:
            return httpx.Response(200, text="Invalid source.")
        return httpx.Response(200, content=csv_for(source, date), headers={"Content-Type": "text/plain;charset=UTF-8"})

    def dates(self, source: Optional[str] = None) -> list[tuple[str, str]]:
        """``(source, date)`` of each request received, in order."""
        found = []
        for request in self.requests:
            parts = request.url.path.split("/")
            if len(parts) == 9 and (source is None or parts[5] == source):
                found.append((parts[5], parts[8]))
        return found


# ------------------------------------------------------------------------------------------------ the oracle
# Independent computations over the recorded rows (floats, plain loops, the great-circle formula written out): what the tests compare
# the code under test with. They share nothing with ``app/``.


def rows() -> list[dict[str, Any]]:
    """The recorded detections of both satellites as plain floats."""
    found = []
    for source in (N20, N21):
        for row in fixture_rows(source):
            hhmm = int(row["acq_time"])
            found.append(
                {
                    "lat": float(row["latitude"]),
                    "lon": float(row["longitude"]),
                    "acq": MIDNIGHT + (hhmm // 100) * 3600 + (hhmm % 100) * 60,
                    "frp": float(row["frp"]) if row["frp"] else math.nan,
                    "conf": {"l": 0, "n": 2, "h": 3}[row["confidence"]],
                    "sat": row["satellite"],
                    "row": row,
                }
            )
    return found


def in_lon(lon: float, west: float, east: float) -> bool:
    """Is ``lon`` on the way from ``west`` eastwards to ``east`` (a span of 360 degrees or more is everything)?"""
    span = east - west if east >= west else east - west + 360.0
    if span >= 360.0:
        return True
    return (lon - west) % 360.0 <= span + 1e-9


def oracle_ids(box: str, since: int = SINCE_ALL, min_conf: int = 0, min_frp: float = 0.0) -> list[dict[str, Any]]:
    west, south, east, north = (float(part) for part in box.split(","))
    return [
        r
        for r in rows()
        if south <= r["lat"] <= north and in_lon(r["lon"], west, east) and r["acq"] >= since and r["conf"] >= min_conf and (min_frp <= 0 or r["frp"] >= min_frp)
    ]


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def oracle_grid(cell: float, box: str = "-180,-90,180,90", since: int = SINCE_ALL, min_conf: int = 0, min_frp: float = 0.0) -> dict[tuple[int, int], tuple[int, float, int]]:
    """``{(row, column): (count, highest frp or -1, latest acq)}`` of the cells of ``cell`` degrees from -90 / -180."""
    cells: dict[tuple[int, int], tuple[int, float, int]] = {}
    ny, nx = round(180 / cell), round(360 / cell)
    for r in oracle_ids(box, since, min_conf, min_frp):
        key = (min(ny - 1, math.floor((r["lat"] + 90.0) / cell + 1e-9)), min(nx - 1, math.floor((r["lon"] + 180.0) / cell + 1e-9)))
        count, power, latest = cells.get(key, (0, -1.0, 0))
        cells[key] = (count + 1, max(power, r["frp"] if not math.isnan(r["frp"]) else -1.0), max(latest, r["acq"]))
    return cells


def oracle_near(lat: float, lon: float, radius_km: float, since: int = SINCE_ALL) -> tuple[int, Optional[float], Optional[float], Optional[int]]:
    """``(count, nearest km, highest frp, latest acq)`` of the detections within ``radius_km`` of a point."""
    inside = [(haversine(lat, lon, r["lat"], r["lon"]), r) for r in rows() if r["acq"] >= since]
    inside = [(d, r) for d, r in inside if d <= radius_km]
    if not inside:
        return 0, None, None, None
    powers = [r["frp"] for _, r in inside if not math.isnan(r["frp"])]
    return len(inside), min(d for d, _ in inside), max(powers) if powers else None, max(r["acq"] for _, r in inside)
