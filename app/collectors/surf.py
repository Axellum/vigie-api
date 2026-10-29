"""The hourly swell and wind of a surf spot: Météo-France's wave model through Open-Meteo Marine, and Open-Meteo's wind.

Two requests per spot, each refreshed at the pace of what it carries (``docs/data-sources.md``, "Surf"):

- ``swell``: the waves, the primary and secondary swell and the wind waves of **MFWAM** (Météo-France, Copernicus Marine;
  ``models=meteofrance_wave``), every hour for 7 days (10 variables, weight 1.0). MFWAM is the model of Open-Meteo Marine that
  gives a secondary swell on these coasts; its data is 3-hourly and updated every 12 hours
  (https://open-meteo.com/en/docs/marine-weather-api), so the entry is kept 3 hours.
- ``wind``: the wind at 10 m, its direction and gusts, every hour, and the sunrise and sunset of each day (5 variables, weight
  1.0), from Open-Meteo's forecast; the models are updated every hour at best, so the entry is kept 1 hour.

A spot is a fixed point of ``app/data/surf-spots.json``: the cache key is the spot, never the number of days asked (7 are
always fetched, the service cuts them). A variable the provider leaves out stays ``None``; an answer with no swell at all
raises (the spot then has no forecast).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector, optional_float
from app.collectors.grid import coord_str
from app.collectors.weights import open_meteo_weight

SWELL_VARIABLES = (
    "wave_height",
    "wave_direction",
    "wave_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
    "secondary_swell_wave_height",
    "secondary_swell_wave_direction",
    "secondary_swell_wave_period",
    "wind_wave_height",
)
WIND_VARIABLES = ("wind_speed_10m", "wind_direction_10m", "wind_gusts_10m")
DAYLIGHT_VARIABLES = ("sunrise", "sunset")
MARINE_MODEL = "meteofrance_wave"
FORECAST_DAYS = 7


def _series(block: Mapping[str, Any], name: str, length: int) -> list[Optional[float]]:
    """The values of one variable, as numbers or ``None``, padded with ``None`` to ``length``."""
    values = [optional_float(v) for v in (block.get(name) or [])][:length]
    return values + [None] * (length - len(values))


def _times(block: Mapping[str, Any]) -> list[int]:
    times = block.get("time") or []
    if not isinstance(times, list) or not all(isinstance(t, (int, float)) for t in times):
        raise ValueError("Open-Meteo answer without unix times")
    return [int(t) for t in times]


def parse_swell(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The hourly swell of an Open-Meteo Marine answer (``timeformat=unixtime``). Raises when it holds no swell height."""
    hourly = payload.get("hourly") or {}
    times = _times(hourly)
    series = {name: _series(hourly, name, len(times)) for name in SWELL_VARIABLES}
    if not any(h is not None for h in series["swell_wave_height"]) and not any(h is not None for h in series["wave_height"]):
        raise ValueError("Open-Meteo Marine answer has no wave or swell height")
    return {
        "times": times,
        "model_lat": optional_float(payload.get("latitude")),
        "model_lon": optional_float(payload.get("longitude")),
        **series,
    }


def parse_wind(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The hourly wind and the daylight of an Open-Meteo forecast answer (``timeformat=unixtime``)."""
    hourly = payload.get("hourly") or {}
    daily = payload.get("daily") or {}
    times = _times(hourly)
    days = len(daily.get("time") or [])
    series = {name: _series(hourly, name, len(times)) for name in WIND_VARIABLES}
    if not any(v is not None for v in series["wind_speed_10m"]):
        raise ValueError("Open-Meteo answer has no wind")
    return {
        "times": times,
        **series,
        "sunrise": [int(v) if v is not None else None for v in _series(daily, "sunrise", days)],
        "sunset": [int(v) if v is not None else None for v in _series(daily, "sunset", days)],
    }


class SurfForecastCollector(BaseCollector[dict[str, Any]]):
    """The swell (MFWAM) and the wind of the surf spots, from Open-Meteo's free APIs (one quota with the other Open-Meteo ones)."""

    MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
    FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
    SWELL_TTL_SECONDS = 3 * 3600
    WIND_TTL_SECONDS = 3600

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="open_meteo_surf",
            name="Open-Meteo surf (MFWAM swell, wind)",
            domain="marine",
            ttl_seconds=self.WIND_TTL_SECONDS,
            http_client=http_client,
        )

    def call_weight(self, params: Mapping[str, str]) -> float:
        """Weight under the Open-Meteo rule: 10 or 5 variables over 7 days, one model, weigh 1.0."""
        return open_meteo_weight(params, default_forecast_days=FORECAST_DAYS)

    async def fetch_swell(self, spot_id: str, lat: float, lon: float) -> tuple[dict[str, Any], bool]:
        """The hourly swell of a spot, and whether it is a stale copy."""
        fetched = await self.fetch_ex(
            cache_key=f"surf-swell:{spot_id}", kind="swell", lat=lat, lon=lon, ttl_seconds=self.SWELL_TTL_SECONDS
        )
        return fetched.value, fetched.stale

    async def fetch_wind(self, spot_id: str, lat: float, lon: float) -> tuple[dict[str, Any], bool]:
        """The hourly wind and the daylight of a spot, and whether it is a stale copy."""
        fetched = await self.fetch_ex(
            cache_key=f"surf-wind:{spot_id}", kind="wind", lat=lat, lon=lon, ttl_seconds=self.WIND_TTL_SECONDS
        )
        return fetched.value, fetched.stale

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> dict[str, Any]:
        lat, lon = float(kwargs["lat"]), float(kwargs["lon"])
        common = {
            "latitude": coord_str(lat),
            "longitude": coord_str(lon),
            "forecast_days": str(FORECAST_DAYS),
            "timeformat": "unixtime",
            "timezone": "GMT",
        }
        if kwargs["kind"] == "swell":
            params = {**common, "hourly": ",".join(SWELL_VARIABLES), "models": MARINE_MODEL}
            response = await self._get(client, self.MARINE_URL, params=params)
            response.raise_for_status()
            return parse_swell(response.json())
        params = {**common, "hourly": ",".join(WIND_VARIABLES), "daily": ",".join(DAYLIGHT_VARIABLES)}
        response = await self._get(client, self.FORECAST_URL, params=params)
        response.raise_for_status()
        return parse_wind(response.json())
