"""Météo-France moored buoys: ``DPObs/v2/bouees`` on ``portail-api.meteofrance.fr``.

``https://public-api.meteofrance.fr/public/DPObs/v2/bouees?format=json&date_debut=...`` answers, in one call, the hourly
records of every buoy of Météo-France since ``date_debut`` (one row per buoy and per hour). Read on 2026-11-16: 10 buoys (the
Gulf of Lion, Provence, PACA, Azur, Ajaccio, Calvi, Porto-Vecchio and Sardinia in the Mediterranean, Gascogne in the Bay of
Biscay, one off the Antilles), records dated on the hour and published about 8 minutes later. Without ``date_debut`` the API
answers 5 days (375 kB); with the last 6 hours it answers about 15 kB.

The API is in the "Données publiques" family of the portal (catalogue tags ``données publiques``, ``open data``, ``PAYANT: Non``);
its own page names no licence that could be read (rendered by script): see ``app/commercial.py``.

Fields of a row and how Vigie converts them (units as in ``DPPaquetObs``, the same family; the API documents no field):

- ``haut_vag`` (m) -> ``wave_height_m``. Which significant height (H1/3 or Hm0) is not stated: ``height_kind`` stays ``"Hs"``.
- ``per_moy_vag`` (s) -> ``wave_period_s`` (a mean period, as the name says; ``<= 0`` is ``None``).
- ``dir_vag`` (degrees) -> ``wave_from_deg``; the convention is not stated by the API (WMO practice: where the waves come from).
- ``tmer`` and ``t`` (Kelvin) -> ``sea_temp_c`` and ``air_temp_c`` (``- 273.15``; a sea outside ``[-3, 45]`` °C is ``None``).
- ``ff`` and ``rafper`` (m/s) -> ``wind_speed_kmh`` and ``wind_gust_kmh`` (``* 3.6``); ``dd`` (degrees) -> ``wind_from_deg``.
- ``pmer`` (Pa) -> ``pressure_hpa`` (``/ 100``).
- ``per`` (``-10`` on every row read) and ``td``, ``u`` are not used.

Key: ``VIGIE_METEOFRANCE_API_KEY`` (the one key of the portal application, subscribed to ``DonneesPubliquesObservation``), sent in
the ``apikey`` header, never in a URL, redacted in every error and log line.
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

BUOYS_URL = "https://public-api.meteofrance.fr/public/DPObs/v2/bouees"
KEY_ENV = "VIGIE_METEOFRANCE_API_KEY"
# Records are hourly and published about 8 minutes past the hour: half an hour keeps a record at most ~40 minutes late, 48 calls a day.
TTL_SECONDS = 1800
WINDOW_HOURS = 6
MAX_BYTES = 2 * 1024 * 1024
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9._\-+/=]{16,}$")
_ACRONYMS = {"PACA"}  # kept in capitals by ``display_name`` (Provence-Alpes-Côte d'Azur)


@dataclass(frozen=True)
class BuoyRecord:
    """The latest hourly record of one Météo-France buoy, converted (``None``: the buoy did not give it)."""

    id: str
    name: str
    lat: float
    lon: float
    measured_utc: int
    wave_height_m: Optional[float]
    wave_period_s: Optional[float]
    wave_from_deg: Optional[int]
    sea_temp_c: Optional[float]
    air_temp_c: Optional[float]
    wind_speed_kmh: Optional[float]
    wind_gust_kmh: Optional[float]
    wind_from_deg: Optional[int]
    pressure_hpa: Optional[float]


Records = dict[str, BuoyRecord]


@dataclass(frozen=True)
class LatestBuoys:
    records: Records
    stale: bool


def meteofrance_buoys_key_from_env(env: Optional[Mapping[str, str]] = None) -> tuple[Optional[str], Optional[str]]:
    """``(key, problem)`` read from ``VIGIE_METEOFRANCE_API_KEY``; a key of the wrong shape is refused without being quoted."""
    raw = ((env if env is not None else os.environ).get(KEY_ENV) or "").strip()
    if not raw:
        return None, None
    if not _KEY_SHAPE.match(raw):
        return None, f"{KEY_ENV} is set but is not shaped as a Météo-France API key: it is not used"
    return raw, None


def _parse_iso_utc(raw: Any) -> Optional[int]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def display_name(raw: str) -> str:
    """``BOUEE_GASCOGNE`` -> ``Gascogne``; a name of another shape is kept as given."""
    text = raw.strip()
    if text.upper().startswith("BOUEE_"):
        text = text[6:]
    if text in _ACRONYMS:
        return text
    return text.replace("_", " ").title() if text.isupper() else text


def _kelvin(value: Any) -> Optional[float]:
    k = optional_float(value)
    return round(k - 273.15, 1) if k is not None and k > 150.0 else None


def _direction(value: Any) -> Optional[int]:
    d = optional_float(value)
    return int(round(d)) % 360 if d is not None and 0.0 <= d <= 360.0 else None


def _kmh(value: Any) -> Optional[float]:
    v = optional_float(value)
    return round(v * 3.6, 1) if v is not None and v >= 0.0 else None


def parse_buoys(payload: Any) -> Records:
    """The latest record of each buoy (by WMO id) in a ``bouees?format=json`` answer. Raises ``ValueError`` on another shape."""
    if not isinstance(payload, list):
        raise ValueError("Météo-France DPObs bouees answer is not a JSON list")
    out: Records = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        buoy_id = str(row.get("geo_id_wmo") or "").strip()
        lat = optional_float(row.get("lat"))
        lon = optional_float(row.get("lon"))
        measured = _parse_iso_utc(row.get("validity_time"))
        if not buoy_id or lat is None or lon is None or measured is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        previous = out.get(buoy_id)
        if previous is not None and previous.measured_utc >= measured:
            continue
        height = optional_float(row.get("haut_vag"))
        period = optional_float(row.get("per_moy_vag"))
        sea = _kelvin(row.get("tmer"))
        pmer = optional_float(row.get("pmer"))
        ff = optional_float(row.get("ff"))
        out[buoy_id] = BuoyRecord(
            id=buoy_id,
            name=display_name(str(row.get("name") or buoy_id)),
            lat=lat,
            lon=lon,
            measured_utc=measured,
            wave_height_m=round(height, 2) if height is not None and height >= 0.0 else None,
            wave_period_s=round(period, 1) if period is not None and period > 0.0 else None,
            wave_from_deg=_direction(row.get("dir_vag")),
            sea_temp_c=sea if sea is not None and -3.0 <= sea <= 45.0 else None,
            air_temp_c=_kelvin(row.get("t")),
            wind_speed_kmh=_kmh(ff),
            wind_gust_kmh=_kmh(row.get("rafper")),
            wind_from_deg=None if ff == 0 else _direction(row.get("dd")),
            pressure_hpa=round(pmer / 100.0, 1) if pmer is not None and pmer > 80000.0 else None,
        )
    return out


class MeteoFranceBuoysCollector(BaseCollector[Records]):
    """Latest hourly records of Météo-France's moored buoys (``DPObs/v2/bouees``)."""

    def __init__(self, api_key: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="meteofrance_buoys",
            name="Météo-France moored buoys (DPObs v2)",
            domain="marine",
            ttl_seconds=TTL_SECONDS,
            timeout_seconds=20.0,
            http_client=http_client,
        )
        self._key = api_key
        self.secrets = (api_key,)

    async def fetch_latest(self) -> LatestBuoys:
        res = await self.fetch_ex(cache_key="bouees")
        return LatestBuoys(records=res.value, stale=res.stale)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Records:
        start = datetime.fromtimestamp(clock.wall(), tz=timezone.utc) - timedelta(hours=WINDOW_HOURS)
        params = {"format": "json", "date_debut": start.strftime("%Y-%m-%dT%H:00:00Z")}
        resp = await self._get(client, BUOYS_URL, params=params, headers={"apikey": self._key, "Accept": "application/json"})
        resp.raise_for_status()
        if len(resp.content) > MAX_BYTES:
            raise ValueError(f"Météo-France DPObs bouees answer exceeds {MAX_BYTES} bytes")
        return parse_buoys(resp.json())
