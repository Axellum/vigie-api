"""Météo-France daily UV index over France: ``DPPaquetENVIRONNEMENT`` (model PHEALTH) on ``portail-api.meteofrance.fr``.

PHEALTH is Météo-France's health-quality model. Its package ``UVQ`` ("Paramètres UV quotidiens", grid ``EURW1S40``, 0.025 degrees,
37.5 to 55.4 N and 12 W to 16 E) is one GRIB2 file per run, a run every 3 hours, published about 3 hours after its time. Read on
2026-11-18, one file of 1.9 MB holds four messages (compressed with CCSDS, so decoded with ecCodes):

- ``UV index`` (category 4, number 51, ``uvi``): the daily **maximum** of the UV index under the real sky, for two UTC days;
- ``UV index (under clear sky)`` (number 50, ``uviucs``): the same maximum with no cloud, for the same two days.

A message covers 24 hours aligned on 00:00 UTC (a run of 03:00 UTC gives ``stepRange`` ``-3-21`` and ``21-45``). Missing points are
``9999`` under a bitmap.

The model gives a maximum per day, no hourly curve: the UV index *now* is calculated by this module from the maximum of the day and
the height of the Sun (``uv_now``, an approximation, see there). Only today and tomorrow are covered: the other days keep their value
from the weather provider.

One request lists the runs, one gets the description of the newest run, one downloads its file: three requests an hour out of the
50 a minute of the free plan, and a file only when the run is new. The grid is cut to the box where this API asks for it
(metropolitan France and Corsica), so a few MB stay in memory.

Key: ``VIGIE_METEOFRANCE_UV_API_KEY``, else ``VIGIE_METEOFRANCE_API_KEY`` (one key of the portal application, subscribed to
``PaquetENVIRONNEMENT``), sent in the ``apikey`` header, never in a URL, redacted in every error and log line.
"""

from __future__ import annotations

import math
import os
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import numpy as np

from app import clock
from app.collectors.base import BaseCollector
from app.collectors.grib import DECODE_LOCK

BASE_URL = "https://public-api.meteofrance.fr/previnum/DPPaquetENVIRONNEMENT/v1"
PACKAGE_URL = f"{BASE_URL}/models/PHEALTH/grids/EURW1S40/packages/UVQ"
KEY_ENV = "VIGIE_METEOFRANCE_UV_API_KEY"
FALLBACK_KEY_ENV = "VIGIE_METEOFRANCE_API_KEY"
# A run every 3 hours: the list of the runs is read again every hour, the file only when the newest run changed.
TTL_SECONDS = 3600
MAX_BYTES = 8 * 1024 * 1024
RUNS_TRIED = 3  # the newest run listed may not have its file yet
# The box kept in memory (the one this API asks for the weather of France): metropolitan France and Corsica.
LAT_RANGE = (41.0, 51.5)
LON_RANGE = (-6.0, 10.5)
UV_NUMBER = 51
UV_CLEAR_NUMBER = 50
UV_CATEGORY = 4
MISSING = 9000.0  # the file marks a missing point 9999
# UV index of the Sun at height ``h`` goes about as ``sin(h) ** UV_EXPONENT``; 1.2 is the usual order of magnitude of the
# empirical fits of the erythemal UV to the cosine of the zenith angle. It is an approximation chosen here, not read from Météo-France.
UV_EXPONENT = 1.2
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9._\-+/=]{16,}$")
_LEAD_SHAPE = re.compile(r"^\d{3}H\d{3}H$")  # the range of lead times of a file, e.g. 001H051H

# ecCodes crashed when two threads decoded at once (see ``app/collectors/grib.py``): one decode at a time, shared by every reader.
_DECODE_LOCK = DECODE_LOCK


def meteofrance_uv_key_from_env(env: Optional[Mapping[str, str]] = None) -> tuple[Optional[str], Optional[str]]:
    """``(key, problem)`` read from ``VIGIE_METEOFRANCE_UV_API_KEY``, else ``VIGIE_METEOFRANCE_API_KEY``."""
    source = env if env is not None else os.environ
    for var_name in (KEY_ENV, FALLBACK_KEY_ENV):
        raw = (source.get(var_name) or "").strip()
        if not raw:
            continue
        if not _KEY_SHAPE.match(raw):
            return None, f"{var_name} is set but is not shaped as a Météo-France API token: the Météo-France UV index is off"
        return raw, None
    return None, None


@dataclass(frozen=True)
class UvDay:
    """The UV maxima of one UTC day on the grid: ``sky`` (real sky) and ``clear`` (no cloud), ``nan`` where missing."""

    sky: "np.ndarray[Any, Any]"
    clear: "np.ndarray[Any, Any]"


@dataclass(frozen=True)
class UvGrids:
    """The daily UV maxima of one PHEALTH run, cut to the box of this API."""

    run_utc: int
    lats: "np.ndarray[Any, Any]"  # ascending
    lons: "np.ndarray[Any, Any]"  # ascending, -180..180
    days: dict[str, UvDay]  # UTC date (YYYY-MM-DD) -> maxima

    def sample(self, lat: float, lon: float, date: str) -> Optional[tuple[float, float]]:
        """``(sky, clear)`` UV maxima of ``date`` at the grid point nearest to ``(lat, lon)``, or ``None``.

        A missing point looks at the 8 points around it (0.025 degrees away), then gives up."""
        day = self.days.get(date)
        if day is None or not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
            return None
        i = int(np.abs(self.lats - lat).argmin())
        j = int(np.abs(self.lons - lon).argmin())
        if abs(self.lats[i] - lat) > 0.05 or abs(self.lons[j] - lon) > 0.05:
            return None
        best: Optional[tuple[float, float, float]] = None
        for di in (0, -1, 1):
            for dj in (0, -1, 1):
                ii, jj = i + di, j + dj
                if not (0 <= ii < len(self.lats) and 0 <= jj < len(self.lons)):
                    continue
                sky, clear = float(day.sky[ii, jj]), float(day.clear[ii, jj])
                if math.isnan(sky) or math.isnan(clear):
                    continue
                distance = abs(di) + abs(dj)
                if best is None or distance < best[0]:
                    best = (distance, sky, clear)
            if best is not None and best[0] == 0:
                break
        return None if best is None else (round(best[1], 2), round(best[2], 2))


def _messages(data: bytes) -> list[bytes]:
    """Split a GRIB2 buffer into its messages (section 0 holds the length of each)."""
    out: list[bytes] = []
    i = 0
    while i + 16 <= len(data):
        if data[i : i + 4] != b"GRIB":
            raise ValueError("not a GRIB2 buffer")
        length = struct.unpack(">Q", data[i + 8 : i + 16])[0]
        if length < 16 or i + length > len(data):
            raise ValueError("truncated GRIB2 message")
        out.append(data[i : i + length])
        i += length
    return out


def decode_uvq(data: bytes) -> UvGrids:
    """The daily UV maxima of one PHEALTH ``UVQ`` GRIB2 file, cut to the box of this API. ``ValueError`` when there are none."""
    import eccodes  # imported only when a file is read

    days: dict[str, dict[int, Any]] = {}
    lats = lons = None
    keep: Optional[tuple[Any, Any]] = None
    shape: Optional[tuple[int, int]] = None
    run: Optional[datetime] = None
    with _DECODE_LOCK:
        for raw in _messages(data):
            handle = eccodes.codes_new_from_message(raw)
            try:
                if int(eccodes.codes_get(handle, "parameterCategory")) != UV_CATEGORY:
                    continue
                number = int(eccodes.codes_get(handle, "parameterNumber"))
                if number not in (UV_NUMBER, UV_CLEAR_NUMBER):
                    continue
                if str(eccodes.codes_get(handle, "stepType")) != "max" or int(eccodes.codes_get(handle, "lengthOfTimeRange")) != 24:
                    continue
                if eccodes.codes_get(handle, "gridType") != "regular_ll":
                    raise ValueError(f"UVQ grid {eccodes.codes_get(handle, 'gridType')} is not regular_ll")
                ni, nj = int(eccodes.codes_get(handle, "Ni")), int(eccodes.codes_get(handle, "Nj"))
                lat1 = float(eccodes.codes_get(handle, "latitudeOfFirstGridPointInDegrees"))
                lat2 = float(eccodes.codes_get(handle, "latitudeOfLastGridPointInDegrees"))
                lon1 = float(eccodes.codes_get(handle, "longitudeOfFirstGridPointInDegrees"))
                lon2 = float(eccodes.codes_get(handle, "longitudeOfLastGridPointInDegrees"))
                if lon2 < lon1:
                    lon2 += 360.0
                all_lats = np.linspace(lat1, lat2, nj)
                all_lons = np.linspace(lon1, lon2, ni)
                all_lons = np.where(all_lons > 180.0, all_lons - 360.0, all_lons)
                values = np.asarray(eccodes.codes_get_values(handle), dtype=np.float64).reshape(nj, ni)
                values = np.where(values >= MISSING, np.nan, values)
                data_date, data_time = int(eccodes.codes_get(handle, "dataDate")), int(eccodes.codes_get(handle, "dataTime"))
                this_run = datetime(
                    data_date // 10000, data_date // 100 % 100, data_date % 100, data_time // 100, data_time % 100, tzinfo=timezone.utc
                )
                start = this_run + timedelta(hours=int(eccodes.codes_get(handle, "startStep")))
            finally:
                eccodes.codes_release(handle)
            if start.hour != 0 or start.minute != 0:
                continue  # a window that is not a UTC day: not used
            run = this_run
            if shape is not None and shape != (nj, ni):
                raise ValueError("the UVQ messages are not on the same grid")
            if lats is None or keep is None:
                shape = (nj, ni)
                lat_order = np.argsort(all_lats)
                lon_order = np.argsort(all_lons)
                lat_keep = [k for k in lat_order if LAT_RANGE[0] - 0.1 <= all_lats[k] <= LAT_RANGE[1] + 0.1]
                lon_keep = [k for k in lon_order if LON_RANGE[0] - 0.1 <= all_lons[k] <= LON_RANGE[1] + 0.1]
                lats, lons = all_lats[lat_keep], all_lons[lon_keep]
                keep = (np.array(lat_keep), np.array(lon_keep))
            assert keep is not None
            cut = values[np.ix_(*keep)].astype(np.float32)
            days.setdefault(start.date().isoformat(), {})[number] = cut
    complete = {
        date: UvDay(sky=parts[UV_NUMBER], clear=parts[UV_CLEAR_NUMBER])
        for date, parts in days.items()
        if UV_NUMBER in parts and UV_CLEAR_NUMBER in parts
    }
    if not complete or lats is None or lons is None or run is None:
        raise ValueError("the UVQ file holds no daily UV index")
    return UvGrids(run_utc=int(run.timestamp()), lats=lats, lons=lons, days=complete)


def _sun_height_deg(lat: float, lon: float, moment: datetime) -> float:
    """Geometric height of the Sun above the horizon (degrees, no refraction), by astronomy-engine."""
    import astronomy

    when = moment.astimezone(timezone.utc)
    time = astronomy.Time.Make(when.year, when.month, when.day, when.hour, when.minute, when.second)
    observer = astronomy.Observer(lat, lon, 0.0)
    equator = astronomy.Equator(astronomy.Body.Sun, time, observer, True, True)
    return float(astronomy.Horizon(time, observer, equator.ra, equator.dec, astronomy.Refraction.Airless).altitude)


def _noon_height_deg(lat: float, lon: float, moment: datetime) -> float:
    """Height of the Sun at its highest on the day of ``moment`` (degrees): ``90 - |latitude - declination|`` at local solar noon."""
    import astronomy

    day = moment.astimezone(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    noon = day - timedelta(hours=lon / 15.0)
    time = astronomy.Time.Make(noon.year, noon.month, noon.day, noon.hour, noon.minute, 0)
    observer = astronomy.Observer(lat, lon, 0.0)
    declination = float(astronomy.Equator(astronomy.Body.Sun, time, observer, True, True).dec)
    return 90.0 - abs(lat - declination)


def uv_now(sky_max: float, lat: float, lon: float, moment: datetime) -> float:
    """The UV index at ``moment``, calculated from the maximum of the day under the real sky (an approximation).

    ``sky_max`` is Météo-France's maximum of the day; the index of another hour is that maximum scaled by the height of the Sun:
    ``sky_max * (sin(height now) / sin(height at noon)) ** UV_EXPONENT``, zero when the Sun is below the horizon. It follows the Sun
    only: a cloud that is not the one of the hour of the maximum is not seen. Compared with Open-Meteo's index at several towns
    (the measurement of the ADR 0028), not with a UV sensor."""
    height = _sun_height_deg(lat, lon, moment)
    if height <= 0.0:
        return 0.0
    noon = _noon_height_deg(lat, lon, moment)
    if noon <= 0.0:
        return 0.0
    ratio = min(1.0, math.sin(math.radians(height)) / math.sin(math.radians(noon)))
    return round(max(0.0, sky_max) * ratio**UV_EXPONENT, 1)


def _run_of(link: dict[str, Any]) -> Optional[str]:
    match = re.search(r"referencetime=([0-9T:\-]+Z)", str(link.get("href") or ""))
    return match.group(1) if match else None


class MeteoFranceUvCollector(BaseCollector[UvGrids]):
    """The daily UV maxima of the newest published PHEALTH run (``DPPaquetENVIRONNEMENT``)."""

    def __init__(self, api_key: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="meteofrance_uv",
            name="Météo-France daily UV index (PHEALTH, DPPaquetENVIRONNEMENT)",
            domain="weather",
            ttl_seconds=TTL_SECONDS,
            failure_threshold=3,
            recovery_timeout_seconds=300,
            timeout_seconds=30.0,
            http_client=http_client,
        )
        self._key = api_key
        self.secrets = (api_key,)
        self._held: Optional[tuple[str, UvGrids]] = None  # (reference time of the run, its grids)

    async def uv_grids(self) -> Optional[UvGrids]:
        """The grids of the newest run, or ``None`` (a failure with nothing held, circuit open): the UV index stays unknown."""
        try:
            return (await self.fetch_ex(cache_key="uvq")).value
        except Exception:
            return None

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> UvGrids:
        kwargs.pop("previous", None)
        headers = {"apikey": self._key, "accept": "application/json"}
        listing = await self._get(client, PACKAGE_URL, headers=headers)
        listing.raise_for_status()
        runs = sorted({run for link in listing.json().get("links", []) if (run := _run_of(link))}, reverse=True)
        if not runs:
            raise ValueError("no PHEALTH run listed")
        if self._held is not None and runs[0] == self._held[0]:
            return self._held[1]
        last: Optional[Exception] = None
        for run in runs[:RUNS_TRIED]:
            if self._held is not None and run == self._held[0]:
                return self._held[1]
            try:
                grids = await self._read_run(client, headers, run)
            except Exception as exc:  # this run has no file yet, or a bad one: try the one before
                last = exc
                continue
            self._held = (run, grids)
            return grids
        raise ValueError(f"no readable PHEALTH run among the newest {RUNS_TRIED}: {last}")

    async def _read_run(self, client: httpx.AsyncClient, headers: Mapping[str, str], run: str) -> UvGrids:
        description = await self._get(client, PACKAGE_URL, params={"referencetime": run}, headers=headers)
        description.raise_for_status()
        # The links of the description name the product by its path and its query; the file is asked from this API's own base address,
        # whatever host the answer names (the links omit the ``/v1`` of the base address).
        times = []
        for link in description.json().get("links", []):
            href = str(link.get("href") or "")
            parts = urlsplit(href)
            query = parse_qs(parts.query)
            if parts.path.endswith("/productPHEALTH") and query.get("format") == ["grib2"] and query.get("referencetime") == [run]:
                times.extend(t for t in query.get("time", []) if _LEAD_SHAPE.match(t))
        if not times:
            raise ValueError(f"run {run} has no file yet")
        url = f"{PACKAGE_URL}/productPHEALTH?{urlencode({'referencetime': run, 'time': times[0], 'format': 'grib2'})}"
        response = await self._get_capped(client, url, max_bytes=MAX_BYTES, headers={"apikey": self._key, "accept": "*/*"})
        response.raise_for_status()
        return await clock.run_blocking(decode_uvq, response.content)
