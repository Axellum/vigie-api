"""The recorded FIRMS answers of ``tests/fixtures/firms`` and two simulated FIRMS servers, as the tests replay them.

``FirmsServer`` answers like the static files of FIRMS (``/data/active_fire/{folder}/csv/{prefix}_Global_{24|48}h.csv``): the recorded rows of
the real files of 2026-10-20, with the real ``ETag`` and ``Last-Modified`` they had, ``Accept-Ranges: bytes`` and no ``304`` (the real server
sends the whole file back to a conditional request). It can also cut or stall a download, which the real one does from time to time.
``AreaServer`` answers like the area API (``/api/area/csv/{key}/{source}/world/1/{date}``) for the fallback that needs a key: the key below is
made up and is not in any recording.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import math
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from tests.helpers import Provider

FIRMS = Path(__file__).resolve().parent / "fixtures" / "firms"
KEY = "FIRMSTESTKEYFIRMSTESTKEY"  # made up (low entropy on purpose: the secret scan reads a key-like literal as a leak); the real key is never in the repository
N20, N21 = "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"
FIXTURE_DAY = "2026-10-20"
AREA_HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"
STATIC_HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight"
HEADER = AREA_HEADER
AREA_FILES = {N20: "viirs_noaa20_nrt_world_2026-10-20.csv", N21: "viirs_noaa21_nrt_world_2026-10-20.csv"}
STATIC_FILES = {(N20, 24): "static_noaa20_24h.csv", (N21, 24): "static_noaa21_24h.csv", (N21, 48): "static_noaa21_48h.csv"}
# folder and file of each source on the real server
STATIC_PATHS = {
    (N20, 24): "/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv",
    (N20, 48): "/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_48h.csv",
    (N21, 24): "/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_24h.csv",
    (N21, 48): "/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_48h.csv",
}
# The validators the real server gave on 2026-10-20 (HEAD of 20:17 UTC): opaque labels here, the recorded files are not the whole ones.
REAL_VALIDATORS = {
    N20: ('"09d95d7d68cb89d9808ed41417a05674"', "Tue, 20 Oct 2026 19:40:30 GMT"),
    N21: ('"644399737c597320b1e075faab41d530"', "Tue, 20 Oct 2026 19:43:17 GMT"),
}


MIDNIGHT = int(datetime.fromisoformat("2026-10-20T00:00:00+00:00").timestamp())
SINCE_ALL = 0


def at(day: str, clock: str) -> float:
    """``at('2026-10-20', '18:12')``: a UTC instant as an epoch time."""
    return datetime.fromisoformat(f"{day}T{clock}:00+00:00").timestamp()


NOW = at("2026-10-20", "20:30")  # the recorded files were read at 20:25 UTC: written at 19:40 and 19:43, their latest detection acquired at 16:20 and 17:15
PUBLISHED = {N20: at("2026-10-20", "19:40") + 30, N21: at("2026-10-20", "19:43") + 17}  # the Last-Modified of REAL_VALIDATORS


def static_text(source: str, window: int = 24) -> str:
    return (FIRMS / STATIC_FILES[(source, window)]).read_text(encoding="utf-8")


def static_rows(source: str, window: int = 24) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(static_text(source, window))))


def area_text(source: str) -> str:
    return (FIRMS / AREA_FILES[source]).read_text(encoding="utf-8")


def area_rows(source: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(area_text(source))))


def csv_for(source: str, date: str) -> bytes:
    """The recorded rows of the area API for ``source`` as the file of ``date`` (only the date of each row changes)."""
    return area_text(source).replace(FIXTURE_DAY, date).encode("utf-8")


def static_csv(source: str, window: int = 24) -> bytes:
    return static_text(source, window).encode("utf-8")


def csv_rows(rows: list[tuple[float, float, int, float, str]], date: str = FIXTURE_DAY, satellite: str = "N20") -> bytes:
    """A made-up file of ``(lat, lon, hhmm, frp, confidence)`` rows in the layout of the area API, to test what a recording does not hold."""
    lines = [AREA_HEADER]
    for lat, lon, hhmm, frp, confidence in rows:
        lines.append(f"{lat},{lon},330.5,0.4,0.4,{date},{hhmm},{satellite},VIIRS,{confidence},2.0NRT,295.1,{frp},D")
    return ("\n".join(lines) + "\n").encode("utf-8")


def static_csv_rows(rows: list[tuple[float, float, str, str, float, str]], satellite: str = "N20") -> bytes:
    """A made-up static file of ``(lat, lon, date, hhmm, frp, confidence)`` rows (``acq_time`` with its leading zero, the class in full)."""
    lines = [STATIC_HEADER]
    for lat, lon, date, hhmm, frp, confidence in rows:
        lines.append(f"{lat},{lon},330.5,0.4,0.4,{date},{hhmm},{satellite},{confidence},2.0NRT,295.1,{frp},D")
    return ("\n".join(lines) + "\n").encode("utf-8")


def md5_etag(data: bytes) -> str:
    return '"' + hashlib.md5(data).hexdigest() + '"'  # noqa: S324 (the real server's ETag is an MD5; nothing is protected here)


class FirmsServer(Provider):
    """A simulated FIRMS static file server: the recorded rows for each source and window, with validators, ranges and the failures of the real one.

    - ``failing``: sources whose connection is refused; ``status``: a source gets this ``(code, body)`` instead of its file;
    - ``answers``: replaces the file of a ``(source, window)`` (its ``ETag`` follows); ``publish`` makes a source's file a new one;
    - ``honour_304``: answer ``304`` to a matching conditional request (the real server does not); ``ranges``: honour ``Range`` (the real one does);
    - ``cut``: ``{source: n}``, the next download of that source ends in a connection error after ``n`` bytes; ``stall``: ``{source: n}``, it
      stops sending after ``n`` bytes for good (the client's own deadline ends it); ``stall_timeout``: raise ``ReadTimeout`` there instead, as httpx does
      when a socket stays silent;
    - ``body_bytes``: the bytes of body the client really pulled, per source (a client that closes after the headers pulls none).
    """

    def __init__(self, latency_ticks: int = 2) -> None:
        super().__init__(answer=self._respond, latency_ticks=latency_ticks)
        self.failing: set[str] = set()
        self.status: dict[str, tuple[int, bytes]] = {}
        self.answers: dict[tuple[str, int], bytes] = {}
        self.modified: dict[tuple[str, int], str] = {}
        self.etags: dict[tuple[str, int], str] = {}
        self.honour_304 = False
        self.ranges = True
        self.cut: dict[str, int] = {}
        self.stall: dict[str, int] = {}
        self.stall_timeout = False
        self.body_bytes: dict[str, int] = {}
        self.on_request: Optional[Callable[[httpx.Request], None]] = None

    # -- the files ----------------------------------------------------------------------------

    def file(self, source: str, window: int = 24) -> bytes:
        if (source, window) in self.answers:
            return self.answers[(source, window)]
        key = (source, window if (source, window) in STATIC_FILES else 24)
        return static_csv(*key)

    def validators(self, source: str, window: int = 24) -> tuple[str, str]:
        """``(ETag, Last-Modified)`` of the file now: the real ones for the recorded file, the MD5 of the body for a replaced one."""
        etag = self.etags.get((source, window)) or (md5_etag(self.answers[(source, window)]) if (source, window) in self.answers else REAL_VALIDATORS[source][0])
        return etag, self.modified.get((source, window), REAL_VALIDATORS[source][1])

    def publish(self, source: str, data: Optional[bytes] = None, window: int = 24, modified: str = "Tue, 20 Oct 2026 21:50:00 GMT") -> None:
        """FIRMS writes a new file for ``source``: ``data``, whose ``ETag`` is its MD5 as the real one is, or the same rows under another ``ETag``."""
        body = data if data is not None else self.file(source, window)
        self.answers[(source, window)] = body
        self.etags[(source, window)] = md5_etag(body) if data is not None else '"rewritten-' + md5_etag(body + modified.encode())[1:9] + '"'
        self.modified[(source, window)] = modified

    def reads(self, source: Optional[str] = None) -> list[tuple[str, int]]:
        """``(source, window)`` of each request received, in order."""
        found = []
        wanted = {path: key for key, path in STATIC_PATHS.items()}
        for request in self.requests:
            key = wanted.get(request.url.path)
            if key is not None and (source is None or key[0] == source):
                found.append(key)
        return found

    # -- the answers --------------------------------------------------------------------------

    async def _body(self, source: str, data: bytes, cut_after: Optional[int], stall_after: Optional[int]) -> AsyncIterator[bytes]:
        limit = len(data) if cut_after is None and stall_after is None else (cut_after if cut_after is not None else stall_after or 0)
        position = 0
        while position < min(limit, len(data)):
            end = min(position + 2048, limit, len(data))
            await asyncio.sleep(0)
            self.body_bytes[source] = self.body_bytes.get(source, 0) + (end - position)
            yield data[position:end]
            position = end
        if cut_after is not None and cut_after < len(data):
            raise httpx.ReadError("connection reset by peer")
        if stall_after is not None and stall_after < len(data):
            if self.stall_timeout:
                raise httpx.ReadTimeout("")
            await asyncio.Event().wait()  # never answers: only the deadline of the client ends it

    def _respond(self, request: httpx.Request) -> httpx.Response:
        if self.on_request is not None:
            self.on_request(request)
        found = {path: key for key, path in STATIC_PATHS.items()}.get(request.url.path)
        if found is None or request.method != "GET":
            return httpx.Response(404, text="Not found")
        source, window = found
        if source in self.failing:
            raise httpx.ConnectError("[Errno 111] Connection refused", request=request)
        if source in self.status:
            code, body = self.status[source]
            return httpx.Response(code, content=body)
        data = self.file(source, window)
        etag, modified = self.validators(source, window)
        headers = {"Content-Type": "text/csv", "ETag": etag, "Last-Modified": modified, "Accept-Ranges": "bytes", "Content-Length": str(len(data))}
        if self.honour_304 and (request.headers.get("If-None-Match") == etag or request.headers.get("If-Modified-Since") == modified):
            return httpx.Response(304, headers={"ETag": etag, "Last-Modified": modified})
        start = 0
        code = 200
        asked = request.headers.get("Range")
        if asked and self.ranges and asked.startswith("bytes=") and asked.endswith("-") and request.headers.get("If-Range", etag) == etag:
            start = int(asked[len("bytes=") : -1])
            code = 206
            headers["Content-Range"] = f"bytes {start}-{len(data) - 1}/{len(data)}"
            headers["Content-Length"] = str(len(data) - start)
        body = self._body(source, data[start:], self.cut.pop(source, None), self.stall.pop(source, None))
        return httpx.Response(code, headers=headers, content=body)


class AreaServer(Provider):
    """A simulated FIRMS area API: serves the recorded rows for any source and day; ``failing`` makes a source answer an error.

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
        if source not in AREA_FILES:
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


class FirmsAnywhere(Provider):
    """Both servers behind one client: a collector with a key tries the file first and the area API after it."""

    def __init__(self, static: FirmsServer, area: AreaServer) -> None:
        super().__init__(answer=self._respond, latency_ticks=0)
        self.static, self.area = static, area

    def _respond(self, request: httpx.Request) -> httpx.Response:
        server = self.area if request.url.path.startswith("/api/") else self.static
        server.requests.append(request)
        return server._respond(request)


# ------------------------------------------------------------------------------------------------ the oracle
# Independent computations over the recorded rows (floats, plain loops, the great-circle formula written out): what the tests compare
# the code under test with. They share nothing with ``app/``.


def acq_of(row: dict[str, str]) -> int:
    """The acquisition time of a row of a file, as epoch seconds: its own date and its ``HHMM`` time."""
    midnight = int(datetime.fromisoformat(f"{row['acq_date']}T00:00:00+00:00").timestamp())
    hhmm = int(row["acq_time"])
    return midnight + (hhmm // 100) * 3600 + (hhmm % 100) * 60


def rows() -> list[dict[str, Any]]:
    """The recorded detections of both satellites (the files of 24 hours) as plain floats."""
    found = []
    for source in (N20, N21):
        for row in static_rows(source):
            found.append(
                {
                    "lat": float(row["latitude"]),
                    "lon": float(row["longitude"]),
                    "acq": acq_of(row),
                    "frp": float(row["frp"]) if row["frp"] else math.nan,
                    "conf": {"low": 0, "nominal": 2, "high": 3}[row["confidence"]],
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
