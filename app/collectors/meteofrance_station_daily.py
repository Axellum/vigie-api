"""The days a Météo-France station measured, read through a vigie-meteo gateway (optional): "yesterday and the days before at the nearest station".

Météo-France's climatological data API (``DPClim/v1``, daily data of every station, "Licence Ouverte 2.0" in the licence document of the API, read on
2026-11-18) is **asynchronous**: an order for a station and a period, then a CSV file some seconds later; yesterday is only published from 11:30 UTC, and
the portal's quota (50 requests a minute for a key) is shared by everything the gateway reads. So this API does not call it: the gateway
(``Axellum/vigie-meteo``, ``GET /v1/station-daily?station=&days=``) makes one order per station and per day at most, keeps the last 14 days of each
station, spreads its orders out and refuses them past a ceiling (503). This collector asks the gateway for a station (cached one hour here: the gateway's
own cache changes once a day), and refuses an answer that is not DPClim's, for another station, or with a value no weather station could measure.

A measure Météo-France marks *doubtful* (quality code 2, "under verification") is left out by the gateway and named in ``doubtful``: it is never
shown as a number.

Without ``VIGIE_METEO_GATEWAY_URL`` there is no collector and ``GET /v1/climate/recent`` says ``not_configured``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import httpx

from app.collectors.base import SELF_HOSTED_CONNECTION, BaseCollector, optional_float
from app.models.climate_recent import ClimateDay

PROVIDER_ID = "meteofrance_station_daily"
SOURCE_MODEL = "meteofrance-dpclim"
TTL_SECONDS = 3600
WINDOW_DAYS = 14  # what the gateway keeps per station
STATION_RE = re.compile(r"^\d{8}$")

# Limits of what a station can measure in a day: a value outside is a broken answer, not a record.
LIMITS: dict[str, tuple[float, float]] = {
    "rain_mm": (0.0, 2000.0),
    "tmin_c": (-90.0, 70.0),
    "tmax_c": (-90.0, 70.0),
    "tmean_c": (-90.0, 70.0),
    "wind_mean_ms": (0.0, 120.0),
    "gust_ms": (0.0, 120.0),
}


@dataclass(frozen=True, slots=True)
class StationDaily:
    """The days of a station, as the gateway gave them."""

    station: str
    last_day: Optional[str]
    fetched_at_utc: Optional[int]
    days: tuple[ClimateDay, ...]


def _iso_day(value: Any) -> str:
    return date.fromisoformat(str(value)).isoformat()


def parse_gateway_station_daily(payload: Any, station: str) -> StationDaily:
    """Check and read the gateway's answer; ``ValueError`` when it is not what the source says it is."""
    if not isinstance(payload, dict) or payload.get("source_model") != SOURCE_MODEL:
        raise ValueError("not an answer of the climatological data of Météo-France")
    if payload.get("station") != station:
        raise ValueError("the answer is for another station")
    rows = payload.get("days")
    if not isinstance(rows, list):
        raise ValueError("days is not a list")
    days: list[ClimateDay] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("a day is not an object")
        values: dict[str, Optional[float]] = {}
        for key, (low, high) in LIMITS.items():
            value = optional_float(row.get(key))
            if value is not None and not low <= value <= high:
                raise ValueError(f"{key} is out of {low:g}..{high:g}")
            values[key] = value
        doubtful = [str(item) for item in row.get("doubtful", []) if str(item) in LIMITS]
        days.append(ClimateDay(date=_iso_day(row.get("date")), doubtful=doubtful, **values))
    if [d.date for d in days] != sorted({d.date for d in days}):
        raise ValueError("the days are not in order, or repeated")
    last = payload.get("last_day")
    fetched = payload.get("fetched_at")
    return StationDaily(
        station=station,
        last_day=_iso_day(last) if last else None,
        fetched_at_utc=int(datetime.fromisoformat(str(fetched).replace("Z", "+00:00")).timestamp()) if fetched else None,
        days=tuple(days),
    )


class MeteoFranceStationDailyCollector(BaseCollector[StationDaily]):
    """The days of one station through the gateway, cached per station."""

    def __init__(self, base_url: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id=PROVIDER_ID,
            name="Météo-France daily climatological data of the stations (vigie-meteo gateway)",
            domain="weather",
            ttl_seconds=TTL_SECONDS,
            failure_threshold=3,
            recovery_timeout_seconds=120,
            timeout_seconds=30.0,  # a first order of the day for a station waits for the portal's file (seconds)
            http_client=http_client,
            connection=SELF_HOSTED_CONNECTION,
        )
        self.base_url = base_url.rstrip("/")

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> StationDaily:
        kwargs.pop("previous", None)
        station = str(kwargs["station"])
        response = await self._get(client, f"{self.base_url}/v1/station-daily", params={"station": station, "days": str(WINDOW_DAYS)})
        response.raise_for_status()
        return parse_gateway_station_daily(response.json(), station)

    async def fetch_station(self, station: str) -> StationDaily:
        """The last days of a station; raises when the gateway cannot give them (the caller says ``unavailable``)."""
        if not STATION_RE.match(station):
            raise ValueError("a Météo-France station number has 8 digits")
        return await self.fetch(f"mf-clim:{station}", station=station)
