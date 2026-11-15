"""NOAA National Data Buoy Center (NDBC): latest marine buoy wave observations.

Two unauthenticated public endpoints, both under the NWS public-domain disclaimer
(``https://www.weather.gov/disclaimer``, linked from ``https://www.ndbc.noaa.gov/``):

- ``https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt``: space-delimited table of the latest observation per
  station, asked every 10 minutes (``Cache-Control: max-age=600, must-revalidate``) with conditional ``ETag`` /
  ``Last-Modified`` headers so an unchanged table answers ``304 Not Modified``.
- ``https://www.ndbc.noaa.gov/activestations.xml``: station names, types and owners, cached once a day with conditional
  ``ETag`` / ``Last-Modified``. Coordinates and observation time come directly from ``latest_obs.txt``; when
  ``activestations.xml`` cannot be read, each station keeps its identifier as its name (nothing is invented).

Field definitions from NDBC "Measurement Descriptions and Units" (``https://www.ndbc.noaa.gov/faq/measdes.shtml``):

- ``WVHT`` -> ``height_m`` (``height_kind="H1/3"``: average of highest one-third of wave heights during the 20-minute period);
- ``DPD``  -> ``peak_period_s`` (dominant wave period, seconds; ``<= 0`` is ``None``, seen on KMA buoys as ``0``);
- ``APD``  -> ``period_s`` with ``period_kind="APD"`` (average wave period, seconds; never mixed with ``TH1/3`` or ``T02``);
- ``MWD``  -> ``direction_deg`` (direction the waves at DPD are coming from, degrees clockwise from true North);
- ``WTMP`` -> ``sea_temp_c`` (sea surface temperature, deg C, kept only within ``[-3, 45]``);
- ``hmax_m`` and ``spread_deg`` are not in ``latest_obs.txt`` and stay ``None``.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app import clock
from app.collectors.base import BaseCollector, CacheEntry, Collected, optional_float
from app.collectors.candhis import BuoyReading

log = logging.getLogger(__name__)

LATEST_OBS_URL = "https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt"
ACTIVE_STATIONS_URL = "https://www.ndbc.noaa.gov/activestations.xml"

# ``latest_obs.txt`` carries ``Cache-Control: max-age=600, must-revalidate`` (checked on 2026-11-15): 144 calls/day max.
TTL_SECONDS = 600
STATIONS_TTL_SECONDS = 86_400

MAX_OBS_BYTES = 4 * 1024 * 1024
MAX_STATIONS_BYTES = 8 * 1024 * 1024
MISSING = "MM"
_REQUIRED_COLUMNS = ("STN", "LAT", "LON", "YYYY", "MM", "DD", "hh", "mm", "WVHT")


@dataclass(frozen=True)
class NdbcStation:
    """One NDBC station with a current wave observation."""

    id: str
    name: str
    lat: float
    lon: float
    station_type: Optional[str] = None
    owner: Optional[str] = None


@dataclass(frozen=True)
class NdbcStationMeta:
    """Metadata of an NDBC station from ``activestations.xml``."""

    id: str
    name: str
    station_type: Optional[str] = None
    owner: Optional[str] = None


@dataclass(frozen=True)
class NdbcLatest:
    """Latest wave buoy observations parsed from NDBC ``latest_obs.txt``."""

    stations: dict[str, NdbcStation]
    readings: dict[str, BuoyReading]
    total_rows: int = 0
    wave_rows: int = 0
    skipped_rows: int = 0
    stale: bool = False


def _ndbc_float(raw: str) -> Optional[float]:
    if not raw or raw == MISSING:
        return None
    return optional_float(raw)


def _ndbc_int(raw: str) -> Optional[int]:
    if not raw or raw == MISSING:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_utc(parts: list[str], index: dict[str, int]) -> Optional[int]:
    year = _ndbc_int(parts[index["YYYY"]])
    month = _ndbc_int(parts[index["MM"]])
    day = _ndbc_int(parts[index["DD"]])
    hour = _ndbc_int(parts[index["hh"]])
    minute = _ndbc_int(parts[index["mm"]])
    if None in (year, month, day, hour, minute):
        return None
    assert year is not None and month is not None and day is not None and hour is not None and minute is not None
    try:
        dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp())


def parse_active_stations(content: bytes) -> dict[str, NdbcStationMeta]:
    """Parse ``activestations.xml`` into ``{station_id_lower: NdbcStationMeta}``.

    Raises ``ValueError`` if the payload exceeds ``MAX_STATIONS_BYTES`` or is not a ``<stations>`` XML document.
    """
    if len(content) > MAX_STATIONS_BYTES:
        raise ValueError(f"NDBC activestations.xml exceeds {MAX_STATIONS_BYTES} bytes")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"NDBC activestations.xml is not valid XML: {exc}") from exc
    if root.tag != "stations":
        raise ValueError(f"NDBC activestations.xml unexpected root tag: {root.tag!r}")
    out: dict[str, NdbcStationMeta] = {}
    for elem in root.findall("station"):
        sid = (elem.attrib.get("id") or "").strip()
        if not sid:
            continue
        name = (elem.attrib.get("name") or "").strip() or sid
        station_type = (elem.attrib.get("type") or "").strip() or None
        owner = (elem.attrib.get("owner") or "").strip() or None
        out[sid.lower()] = NdbcStationMeta(id=sid, name=name, station_type=station_type, owner=owner)
    return out


def parse_latest_obs(
    text: str,
    station_meta: Optional[dict[str, NdbcStationMeta]] = None,
) -> NdbcLatest:
    """Parse ``latest_obs.txt`` into ``NdbcLatest``.

    Rows without ``WVHT`` (e.g. meteorological-only land/C-MAN stations) are ignored cleanly; malformed rows or out-of-range
    required fields are skipped and counted in ``skipped_rows``. Unknown optional measurements stay ``None``.
    """
    if not text.strip():
        raise ValueError("NDBC latest_obs.txt is empty")
    lines = text.splitlines()
    header_index: Optional[dict[str, int]] = None
    data_start = 0
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#STN") or stripped.lstrip("#").startswith("STN "):
            cols = stripped.lstrip("#").split()
            header_index = {name: pos for pos, name in enumerate(cols)}
            data_start = idx + 1
            break
    if header_index is None:
        raise ValueError("NDBC latest_obs.txt missing #STN header")
    missing_cols = [col for col in _REQUIRED_COLUMNS if col not in header_index]
    if missing_cols:
        raise ValueError(f"NDBC latest_obs.txt missing required columns: {', '.join(missing_cols)}")

    col_count = len(header_index)
    meta_map = station_meta or {}
    stations: dict[str, NdbcStation] = {}
    readings: dict[str, BuoyReading] = {}
    total_rows = 0
    wave_rows = 0
    skipped_rows = 0

    def get_field(parts: list[str], name: str) -> str:
        pos = header_index.get(name)
        if pos is None or pos >= len(parts):
            return MISSING
        return parts[pos]

    for raw_line in lines[data_start:]:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        total_rows += 1
        parts = stripped.split()
        if len(parts) < col_count:
            skipped_rows += 1
            continue

        wvht_raw = get_field(parts, "WVHT")
        if wvht_raw == MISSING:
            continue  # non-wave station in the mixed observation table

        station_id = get_field(parts, "STN").strip()
        lat = _ndbc_float(get_field(parts, "LAT"))
        lon = _ndbc_float(get_field(parts, "LON"))
        measured_utc = _parse_utc(parts, header_index)
        wvht = _ndbc_float(wvht_raw)
        if (
            not station_id
            or station_id == MISSING
            or lat is None
            or lon is None
            or measured_utc is None
            or wvht is None
            or not (-90.0 <= lat <= 90.0)
            or not (-180.0 <= lon <= 180.0)
            or wvht < 0.0
        ):
            skipped_rows += 1
            continue

        dpd = _ndbc_float(get_field(parts, "DPD"))
        peak_period_s = dpd if (dpd is not None and dpd > 0.0) else None

        apd = _ndbc_float(get_field(parts, "APD"))
        period_s = apd if (apd is not None and apd > 0.0) else None
        period_kind = "APD" if period_s is not None else None

        mwd = _ndbc_float(get_field(parts, "MWD"))
        direction_deg = mwd if (mwd is not None and 0.0 <= mwd <= 360.0) else None

        wtmp = _ndbc_float(get_field(parts, "WTMP"))
        sea_temp_c = wtmp if (wtmp is not None and -3.0 <= wtmp <= 45.0) else None

        meta = meta_map.get(station_id.lower())
        stations[station_id] = NdbcStation(
            id=station_id,
            name=meta.name if meta is not None else station_id,
            lat=lat,
            lon=lon,
            station_type=meta.station_type if meta is not None else None,
            owner=meta.owner if meta is not None else None,
        )
        readings[station_id] = BuoyReading(
            station_id=station_id,
            measured_utc=measured_utc,
            height_m=wvht,
            height_kind="H1/3",
            hmax_m=None,
            period_s=period_s,
            period_kind=period_kind,
            peak_period_s=peak_period_s,
            direction_deg=direction_deg,
            spread_deg=None,
            sea_temp_c=sea_temp_c,
        )
        wave_rows += 1

    return NdbcLatest(
        stations=stations,
        readings=readings,
        total_rows=total_rows,
        wave_rows=wave_rows,
        skipped_rows=skipped_rows,
        stale=False,
    )


def _conditional_headers(validators: Optional[dict[str, str]], accept: str) -> dict[str, str]:
    headers = {"Accept": accept}
    if validators:
        for header_name, req_name in (("ETag", "If-None-Match"), ("Last-Modified", "If-Modified-Since")):
            if validators.get(header_name):
                headers[req_name] = validators[header_name]
    return headers


def _extract_validators(response: httpx.Response) -> Optional[dict[str, str]]:
    found = {name: response.headers[name] for name in ("ETag", "Last-Modified") if response.headers.get(name)}
    return found or None


class NdbcCollector(BaseCollector[NdbcLatest]):
    """Collector for NOAA NDBC latest marine buoy wave observations (keyless, conditional GET)."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="noaa_ndbc",
            name="NOAA NDBC marine buoys",
            domain="marine",
            ttl_seconds=TTL_SECONDS,
            http_client=http_client,
        )
        self._station_meta: dict[str, NdbcStationMeta] = {}
        self._stations_validators: Optional[dict[str, str]] = None
        self._stations_checked_at: Optional[float] = None

    async def _refresh_stations(self, client: httpx.AsyncClient) -> None:
        """Fetch or conditionally revalidate ``activestations.xml`` at most once per day.

        Errors are logged and ignored so wave observations from ``latest_obs.txt`` remain available with station IDs
        as names when the station metadata endpoint is unreachable.
        """
        now = clock.monotonic()
        if self._stations_checked_at is not None and (now - self._stations_checked_at) < STATIONS_TTL_SECONDS:
            return
        headers = _conditional_headers(self._stations_validators, "application/xml, text/xml, */*;q=0.5")
        try:
            response = await self._get(client, ACTIVE_STATIONS_URL, headers=headers)
            if response.status_code == 304:
                self._stations_checked_at = now
                return
            response.raise_for_status()
            parsed = parse_active_stations(response.content)
        except Exception as exc:
            log.warning("NDBC activestations.xml refresh failed (%s); keeping %d cached stations", exc, len(self._station_meta))
            # Back off station-metadata retries for the observation TTL window to remain call-frugal.
            self._stations_checked_at = now - STATIONS_TTL_SECONDS + TTL_SECONDS
            return
        self._station_meta = parsed
        self._stations_validators = _extract_validators(response)
        self._stations_checked_at = now

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[NdbcLatest]:
        previous: Optional[CacheEntry[NdbcLatest]] = kwargs.get("previous")
        headers = _conditional_headers(previous.validators if previous is not None else None, "text/plain, */*;q=0.5")
        response = await self._get(client, LATEST_OBS_URL, headers=headers)
        if response.status_code == 304:
            if previous is None:
                raise ValueError("NDBC answered 304 Not Modified to a request that was not conditional")
            return Collected(previous.value, validators=previous.validators)
        response.raise_for_status()
        if len(response.content) > MAX_OBS_BYTES:
            raise ValueError(f"NDBC latest_obs.txt exceeds {MAX_OBS_BYTES} bytes")
        await self._refresh_stations(client)
        text = response.content.decode("latin-1")
        latest = parse_latest_obs(text, station_meta=self._station_meta)
        return Collected(latest, validators=_extract_validators(response))

    async def fetch_latest(self) -> NdbcLatest:
        """Return the latest NDBC wave observations, preserving ``stale=True`` on cache fallback."""
        fetched = await self.fetch_ex(cache_key="latest")
        latest = fetched.value
        if fetched.stale and not latest.stale:
            return replace(latest, stale=True)
        return latest
