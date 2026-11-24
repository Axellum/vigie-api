"""Météo-France weather over France (AROME, ARPEGE), read through a vigie-meteo gateway (optional).

The gateway (``Axellum/vigie-meteo``) sits next to an Open-Meteo server that synchronises only Météo-France models, and passes
Open-Meteo's ``/v1/forecast`` through (``GET /v1/forecast?latitude=&longitude=&models=...``, answers in Open-Meteo's own format).
This collector asks it for ``models=meteofrance_seamless``: Open-Meteo's blend of the Météo-France models, AROME France HD
(1.5 km, to about +51 hours) then ARPEGE Europe (11 km, to about +4 days), variable by variable. Read on 2026-11-18, measured
in ADR 0028:

- AROME HD alone has no pressure, no weather code and no cloud cover; the blend fills them from ARPEGE (the weather code is the
  one Open-Meteo calculates from ARPEGE's fields, not a Météo-France product).
- There is **no UV index** and **no precipitation probability** in these models: the UV index comes from ``meteofrance_uv`` (PHEALTH),
  the probability stays with Open-Meteo's public API (see ``merge_france_bundle``).
- The gateway says in ``X-Cache-Status`` whether it answered from an expired cache (its Open-Meteo is down): such an answer is refused.
- A day is complete only when the models cover its 24 hours: Open-Meteo answers ``null`` for the others (4 days, today included). The
  later days (and the hourly cloud cover, which feeds the aurora estimate) come from Open-Meteo's public API.

A position costs two requests, refreshed at the pace of what they carry, like ``OpenMeteoCollector``: ``now`` (the current conditions
and four 15-minute rain amounts, every 15 minutes) and ``daily`` (every hour). The answers are read with the parsers of
``OpenMeteoCollector``: the format is the same, and so are the times of the rain amounts (the first is a quarter-hour already
over, see ``RainNowcast``); the gateway keeps an answer up to ``METEO_FORECAST_TTL_SECONDS`` (15 minutes by default) on top of
this collector's own 15 minutes.

The collector is used only for a point in the box around metropolitan France and Corsica (``in_france_box``); a neighbour inside the
box is served from the same models, which cover it. Without ``VIGIE_METEO_GATEWAY_URL``, or with ``VIGIE_METEO_GATEWAY_WEATHER`` at
``0``, or when the gateway fails, Open-Meteo's public API gives the weather, as before.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.attribution import OPEN_METEO_WEATHER, credit_text, merge_credits
from app.collectors.base import BaseCollector
from app.collectors.grid import DEFAULT_FORECAST_GRID_DEG, coord_str, snap_to_grid
from app.collectors.meteo_gateway import in_piaf_box
from app.collectors.meteofrance_uv import UvGrids, uv_now
from app.collectors.weather import MAX_FORECAST_DAYS, OpenMeteoCollector
from app.models.schemas import CurrentWeather, DailyForecast

TOGGLE_ENV = "VIGIE_METEO_GATEWAY_WEATHER"
MODELS = "meteofrance_seamless"
PROVIDER_NAME = "Météo-France (AROME, ARPEGE)"
NOW_TTL_SECONDS = 900  # the resolution of the ``current`` and ``minutely_15`` data
DAILY_TTL_SECONDS = 3600  # the models are updated every hour at best
# Without the UV index and the precipitation probability: the models have neither (the gateway answers ``null``).
CURRENT_VARIABLES = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,"
    "pressure_msl,wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
    "precipitation,weather_code"
)
DAILY_VARIABLES = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"
# Hourly variables synced in AROME HD / ARPEGE Europe behind the gateway (no uv_index or precipitation_probability).
HOURLY_VARIABLES = (
    "temperature_2m,apparent_temperature,dew_point_2m,"
    "precipitation,"
    "wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
    "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,"
    "pressure_msl,shortwave_radiation,weather_code,is_day"
)
HOURLY_FORECAST_HOURS = 72
HOURLY_PAST_HOURS = 24


def meteo_weather_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the weather of France is read from the gateway: on unless ``VIGIE_METEO_GATEWAY_WEATHER`` is ``0`` / ``false`` / ``no`` / ``off``."""
    raw = (env if env is not None else os.environ).get(TOGGLE_ENV, "1")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def in_france_box(lat: float, lon: float) -> bool:
    """Whether the point is inside the box where the weather is asked from the gateway (metropolitan France and Corsica)."""
    return in_piaf_box(lat, lon)


class MeteoFranceWeatherCollector(BaseCollector[dict[str, Any]]):
    """The current conditions, four 15-minute rain amounts and the daily forecast of a point, from the Météo-France models."""

    def __init__(
        self,
        base_url: str,
        grid_deg: float = DEFAULT_FORECAST_GRID_DEG,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            provider_id="meteofrance_weather",
            name="Météo-France AROME / ARPEGE weather (vigie-meteo gateway)",
            domain="weather",
            ttl_seconds=NOW_TTL_SECONDS,
            failure_threshold=3,
            recovery_timeout_seconds=120,
            timeout_seconds=8.0,
            http_client=http_client,
        )
        self.base_url = base_url.rstrip("/")
        self.grid_deg = grid_deg

    @staticmethod
    def request_params(part: str, lat: float, lon: float) -> dict[str, str]:
        """Query parameters of the ``now`` or the ``daily`` request at ``lat, lon`` (Open-Meteo's)."""
        params = {"latitude": coord_str(lat), "longitude": coord_str(lon), "timezone": "auto", "models": MODELS}
        if part == "daily":
            params["daily"] = DAILY_VARIABLES
            params["forecast_days"] = str(MAX_FORECAST_DAYS)
            params["hourly"] = HOURLY_VARIABLES
            params["forecast_hours"] = str(HOURLY_FORECAST_HOURS)
            params["past_hours"] = str(HOURLY_PAST_HOURS)
        else:
            params["current"] = CURRENT_VARIABLES
            params["minutely_15"] = "precipitation"
            params["forecast_minutely_15"] = "4"
        return params

    def cache_keys_for(self, lat: float, lon: float) -> tuple[str, str]:
        s_lat, s_lon = snap_to_grid(lat, lon, self.grid_deg)
        cell = f"{coord_str(s_lat)},{coord_str(s_lon)}"
        return f"mf-now:{cell}", f"mf-daily:{cell}"

    async def fetch_point(self, lat: float, lon: float) -> dict[str, Any]:
        """The weather of a point: ``utc_offset_s``, ``current``, ``rain_next_60m`` and ``daily`` (the complete days only).

        Raises when the gateway cannot give the current conditions or the days: the caller falls back to Open-Meteo's public API."""
        s_lat, s_lon = snap_to_grid(lat, lon, self.grid_deg)
        now_key, daily_key = self.cache_keys_for(lat, lon)
        now, daily = await asyncio.gather(
            self.fetch(now_key, lat=s_lat, lon=s_lon, part="now", allow_stale_on_error=False),
            self.fetch(daily_key, lat=s_lat, lon=s_lon, part="daily", ttl_seconds=DAILY_TTL_SECONDS, allow_stale_on_error=False),
            return_exceptions=True,
        )
        for part in (now, daily):
            if isinstance(part, BaseException):
                raise part
        assert isinstance(now, dict) and isinstance(daily, dict)
        offset = now.get("utc_offset_s")
        return {
            "utc_offset_s": offset if offset is not None else daily.get("utc_offset_s"),
            "current": now["current"],
            "rain_next_60m": now["rain_next_60m"],
            "daily": daily["daily_10d"],
            "hourly_all": daily.get("hourly_all", []),
        }

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> dict[str, Any]:
        kwargs.pop("previous", None)
        part = str(kwargs.get("part", "now"))
        params = self.request_params(part, float(kwargs["lat"]), float(kwargs["lon"]))
        response = await self._get(client, f"{self.base_url}/v1/forecast", params=params)
        response.raise_for_status()
        if response.headers.get("X-Cache-Status") == "stale":
            # The gateway serves its cached answer, up to a day old, when its Open-Meteo is down: weather that old is not shown as
            # current, Open-Meteo's public API gives it. A gateway that sends no such header is read as before.
            raise ValueError("the gateway answered from an expired cache")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("gateway answer is not an object")
        if part == "daily":
            parsed = OpenMeteoCollector.parse_daily(payload)
            days = [day for day in parsed["daily_10d"] if day.tmax is not None and day.tmin is not None]
            if not days:
                raise ValueError("gateway answer has no complete day")
            hourly_parsed = OpenMeteoCollector.parse_hourly(payload)
            return {
                "utc_offset_s": parsed["utc_offset_s"],
                "daily_10d": days,
                "hourly_all": hourly_parsed["hourly_all"],
            }
        return OpenMeteoCollector.parse_now(payload)  # ValueError without a current temperature


def merge_france_bundle(
    france: Mapping[str, Any],
    public_daily: Optional[Mapping[str, Any]],
    uv: Optional[UvGrids],
    lat: float,
    lon: float,
    now_s: float,
) -> dict[str, Any]:
    """The weather bundle of a point in France: Météo-France's models, completed field by field.

    - ``current``: the models' (see the module doc), plus the UV index calculated from Météo-France's UV maximum of the day
      (``uv_now``) when that grid answers; ``None`` otherwise.
    - ``daily``: for a day the models cover, their weather code, minimum and maximum temperature, rain total and maximum wind; the
      UV maximum from Météo-France's UV grid when it covers the day (today and tomorrow), else from ``public_daily``; the rain
      probability and every later day from ``public_daily`` (Open-Meteo's public API). Without ``public_daily`` only the days the
      models cover are returned, without probability.
    - ``hourly_cloud_cover``: ``public_daily``'s.
    - credits: Météo-France's, plus Open-Meteo's only when ``public_daily`` contributed.
    """
    current: CurrentWeather = france["current"]
    offset = france.get("utc_offset_s")
    local_now = datetime.fromtimestamp(now_s, tz=timezone.utc) + timedelta(seconds=offset or 0)
    uv_used = False

    if uv is not None:
        sample = uv.sample(lat, lon, local_now.date().isoformat())
        if sample is not None:
            current = current.model_copy(update={"uv_index": uv_now(sample[0], lat, lon, datetime.fromtimestamp(now_s, tz=timezone.utc))})
            uv_used = True

    public_days: dict[str, DailyForecast] = {}
    if public_daily is not None:
        public_days = {day.date: day for day in public_daily.get("daily_10d", [])}

    def with_uv(day: DailyForecast) -> DailyForecast:
        nonlocal uv_used
        sample = uv.sample(lat, lon, day.date) if uv is not None else None
        if sample is not None:
            uv_used = True
            return day.model_copy(update={"uv_max": round(sample[0], 1)})
        pub = public_days.get(day.date)
        return day.model_copy(update={"uv_max": pub.uv_max}) if pub is not None else day

    days: dict[str, DailyForecast] = {}
    for day in france["daily"]:
        pub = public_days.get(day.date)
        days[day.date] = with_uv(day.model_copy(update={"rain_probability_pct": pub.rain_probability_pct if pub else None}))
    first_date = min(days, default="")
    for date, pub in public_days.items():
        if date >= first_date:  # a public entry older than the first day of the models is yesterday's (an expired cache)
            days.setdefault(date, pub)

    credits = [credit_text("meteofrance_weather")]
    if uv_used:
        credits.append(credit_text("meteofrance_uv"))
    if public_daily is not None:
        credits.append(credit_text(*OPEN_METEO_WEATHER))
    return {
        "provider_name": PROVIDER_NAME,
        "attribution": merge_credits(*credits),
        "utc_offset_s": offset if offset is not None else (public_daily or {}).get("utc_offset_s"),
        "current": current,
        "rain_next_60m": france["rain_next_60m"],
        "daily_10d": [days[date] for date in sorted(days)],
        "hourly_cloud_cover": list((public_daily or {}).get("hourly_cloud_cover", [])),
        "hourly_all": list(france.get("hourly_all") or []),
    }

