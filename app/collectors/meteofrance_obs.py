"""Météo-France 6-minute ground observations: ``DPPaquetObs/v2`` on ``portail-api.meteofrance.fr``.

``https://public-api.meteofrance.fr/public/DPPaquetObs/v2/paquet/stations/infrahoraire-6m?date=...&format=json`` answers, in one
call, the latest 6-minute observation of every open real-time ground station of Météo-France's Radome network, published under
**Licence Ouverte 2.0 (Etalab)** (https://portail-api.meteofrance.fr/web/fr/api/PaquetObs).

Why we read the whole national package at once:

- One request every ``TTL_SECONDS`` (360 s = 6 minutes, the cadence of the network) serves every surf spot and coastal query,
  using at most 10 requests per hour out of the 50 requests per minute allowed on the free plan.
- Only records that carry a 10 m wind speed (``ff``) are kept, so a closer station that has no anemometer (or whose anemometer
  is down) never hides a nearby coastal wind station.

Units in the upstream JSON package and how Vigie converts them:

- ``ff`` (m/s, 10-minute mean wind speed at 10 m) -> ``wind_speed_kmh`` (``round(ff * 3.6, 1)``).
- ``raf10`` (m/s, 10-minute max wind gust at 10 m in ``DPPaquetObs/v2``; ``fxi10`` or ``fxy`` in ``v1``) -> ``wind_gust_kmh``
  (``round(v * 3.6, 1)``).
- ``dd`` (degrees, 10-minute wind direction at 10 m; ``0`` when ``ff == 0`` means calm wind) -> ``wind_from_deg`` (``0..359``,
  ``None`` when calm or absent).
- ``t`` (Kelvin, air temperature at 2 m) -> ``air_temp_c`` (``round(t - 273.15, 1)``).
- ``u`` (percent, relative humidity at 2 m) -> ``humidity_pct`` (``int(round(u))``).
- ``pmer`` (Pascal, sea-level pressure) -> ``pressure_hpa`` (``round(pmer / 100.0, 1)``).
- ``rr_per`` (mm, precipitation over the last 6 minutes) -> ``precip_6m_mm`` (``round(rr_per, 1)``).

Key configuration: reads ``VIGIE_METEOFRANCE_OBS_API_KEY`` first and falls back to ``VIGIE_METEOFRANCE_API_KEY`` when unset, so a
single Météo-France portal token subscribed to both ``DPVigilance`` and ``DPPaquetObs`` works out of the box.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app import clock
from app.collectors.base import BaseCollector, optional_float

PAQUET_URL = "https://public-api.meteofrance.fr/public/DPPaquetObs/v2/paquet/stations/infrahoraire-6m"
KEY_ENV = "VIGIE_METEOFRANCE_OBS_API_KEY"
FALLBACK_KEY_ENV = "VIGIE_METEOFRANCE_API_KEY"
TTL_SECONDS = 360
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9._\-+/=]{16,}$")


def latest_6min_utc(now_unix: Optional[float] = None) -> datetime:
    """Latest publishable 6-minute observation timestamp (UTC, 6-minute safety margin)."""
    now_s = clock.wall() if now_unix is None else now_unix
    ref = datetime.fromtimestamp(now_s, tz=timezone.utc) - timedelta(minutes=6)
    return ref.replace(minute=(ref.minute // 6) * 6, second=0, microsecond=0)


@dataclass(frozen=True)
class StationReading:
    """The latest 6-minute observation of one Météo-France ground station."""

    station_id: str
    measured_utc: int
    wind_speed_kmh: float
    wind_gust_kmh: Optional[float]
    wind_from_deg: Optional[int]
    air_temp_c: Optional[float]
    humidity_pct: Optional[int]
    pressure_hpa: Optional[float]
    precip_6m_mm: Optional[float]


Readings = dict[str, StationReading]


@dataclass(frozen=True)
class LatestStations:
    """All current station observations from the latest 6-minute package."""

    readings: Readings
    stale: bool


def meteofrance_obs_key_from_env(env: Optional[Mapping[str, str]] = None) -> tuple[Optional[str], Optional[str]]:
    """``(key, problem)`` read from ``VIGIE_METEOFRANCE_OBS_API_KEY`` (or ``VIGIE_METEOFRANCE_API_KEY`` when unset)."""
    source = env if env is not None else os.environ
    for var_name in (KEY_ENV, FALLBACK_KEY_ENV):
        raw = (source.get(var_name) or "").strip()
        if not raw:
            continue
        if not _KEY_SHAPE.match(raw):
            return None, f"{var_name} is set but is not shaped as a Météo-France API token: it is not used"
        return raw, None
    return None, None


def _parse_iso_utc(raw: Any) -> Optional[int]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    """Accept a JSON list (``v1`` or ``v2`` ``format=json``) or a GeoJSON ``FeatureCollection``."""
    raw_items: Any = payload.get("features") if isinstance(payload, dict) and isinstance(payload.get("features"), list) else payload
    if not isinstance(raw_items, list):
        raise ValueError("Météo-France DPPaquetObs answer is not a JSON list or GeoJSON FeatureCollection")
    rows: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        props = item.get("properties")
        rows.append(props if isinstance(props, dict) else item)
    return rows


def parse_latest(payload: Any) -> Readings:
    """Parse the JSON list or GeoJSON features returned by ``DPPaquetObs`` keyed by 8-digit ``station_id``."""
    readings: Readings = {}
    for row in _extract_rows(payload):
        raw_id = str(row.get("geo_id_insee") or "").strip()
        if not raw_id:
            continue
        station_id = raw_id.zfill(8)
        measured_utc = _parse_iso_utc(row.get("validity_time"))
        ff_ms = optional_float(row.get("ff"))
        if measured_utc is None or ff_ms is None or ff_ms < 0:
            continue
        wind_speed_kmh = round(ff_ms * 3.6, 1)
        gust_ms = optional_float(row.get("raf10"))
        if gust_ms is None:
            gust_ms = optional_float(row.get("fxi10"))
        if gust_ms is None:
            gust_ms = optional_float(row.get("fxy"))
        wind_gust_kmh = round(gust_ms * 3.6, 1) if gust_ms is not None and gust_ms >= 0 else None

        dd_raw = optional_float(row.get("dd"))
        if dd_raw is None or (ff_ms == 0 and dd_raw == 0):
            wind_from_deg: Optional[int] = None
        else:
            wind_from_deg = int(round(dd_raw)) % 360

        temp_k = optional_float(row.get("t"))
        air_temp_c = round(temp_k - 273.15, 1) if temp_k is not None and temp_k > 150.0 else None

        hum_raw = optional_float(row.get("u"))
        humidity_pct = int(round(hum_raw)) if hum_raw is not None and 0.0 <= hum_raw <= 100.0 else None

        pmer_pa = optional_float(row.get("pmer"))
        pressure_hpa = round(pmer_pa / 100.0, 1) if pmer_pa is not None and pmer_pa > 80000.0 else None

        rr_mm = optional_float(row.get("rr_per"))
        precip_6m_mm = round(rr_mm, 1) if rr_mm is not None and rr_mm >= 0.0 else None

        prev = readings.get(station_id)
        if prev is not None and prev.measured_utc >= measured_utc:
            continue
        readings[station_id] = StationReading(
            station_id=station_id,
            measured_utc=measured_utc,
            wind_speed_kmh=wind_speed_kmh,
            wind_gust_kmh=wind_gust_kmh,
            wind_from_deg=wind_from_deg,
            air_temp_c=air_temp_c,
            humidity_pct=humidity_pct,
            pressure_hpa=pressure_hpa,
            precip_6m_mm=precip_6m_mm,
        )
    return readings


class MeteoFranceObsCollector(BaseCollector[Readings]):
    """Latest 6-minute observations of Météo-France ground stations (``DPPaquetObs/v2``)."""

    def __init__(self, api_key: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="meteofrance_obs",
            name="Météo-France 6-minute observations (DPPaquetObs)",
            domain="weather",
            ttl_seconds=TTL_SECONDS,
            timeout_seconds=20.0,
            http_client=http_client,
        )
        self._key = api_key
        self.secrets = (api_key,)

    async def fetch_latest(self) -> LatestStations:
        res = await self.fetch_ex(cache_key="infrahoraire_6m")
        return LatestStations(readings=res.value, stale=res.stale)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Readings:
        headers = {"apikey": self._key, "Accept": "application/json"}
        slot = latest_6min_utc()
        for attempt in range(2):
            target = slot - timedelta(minutes=6 * attempt)
            params = {"date": target.strftime("%Y-%m-%dT%H:%M:%SZ"), "format": "json"}
            resp = await self._get(client, PAQUET_URL, params=params, headers=headers)
            if resp.status_code == 404 and attempt == 0:
                continue
            resp.raise_for_status()
            return parse_latest(resp.json())
        raise RuntimeError("unreachable")
