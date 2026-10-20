"""Weather collectors: Open-Meteo (primary), MET Norway (fallback) and RainViewer (radar).

Multi-provider weather:

1. ``OpenMeteoCollector`` (primary): high-resolution blended models (Meteo-France
   AROME, NOAA HRRR, DWD ICON-D2, ECMWF IFS, JMA...) with the 15-minute precipitation
   of the next hour (``minutely_15``) at the exact coordinates. Open-Meteo documents that
   15-minute data is native only in Central Europe and North America; elsewhere it is
   interpolated from hourly data. This is the only source of the short-term rain of the
   embedded frame (``ZONE`` fields 16 to 18 and ``RAIN5M``).
2. ``MetNorwayCollector`` (fallback): ``api.met.no`` Locationforecast 2.0 (compact),
   used when Open-Meteo is unavailable.
3. ``RainViewerCollector`` (radar): timestamp and tile URL template of the latest observed
   frame of the world precipitation radar. The free RainViewer API has had no nowcast (future
   frames) since 2026-01-01, so nothing about future rain comes from it; the embedded frame
   does not use it at all.

Rule: a variable the provider does not supply stays ``None``. Nothing is guessed,
padded or copied from another field (for example MET Norway has no gust, no UV and
no 15-minute nowcast in its compact product, so those stay unknown).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector, CacheEntry, Collected, optional_float, response_lifetime_s
from app.collectors.grid import (
    DEFAULT_FORECAST_GRID_DEG,
    coord_str,
    grid_step_from_env,
    snap_to_grid,
)
from app.models.schemas import CurrentWeather, DailyForecast

# The longest forecast any route returns (non-compact ``/v1/snapshot`` and ``/v1/point``: 10 days).
# Every forecast is asked for this length and cached once per position; the shorter profiles (the
# compact ones, 5 days) slice the answer, so the number of days is not part of the cache key.
MAX_FORECAST_DAYS = 10

# MET Norway symbol_code (without the _day / _night / _polartwilight suffix) -> WMO code.
# A symbol that is not listed gives ``None`` (unknown), never a default code.
_METNO_SYMBOL_TO_WMO: dict[str, int] = {
    "clearsky": 0,
    "fair": 1,
    "partlycloudy": 2,
    "cloudy": 3,
    "fog": 45,
    "lightrain": 61,
    "rain": 63,
    "heavyrain": 65,
    "lightrainshowers": 80,
    "rainshowers": 81,
    "heavyrainshowers": 82,
    "lightsleet": 68,
    "sleet": 69,
    "heavysleet": 69,
    "lightsleetshowers": 83,
    "sleetshowers": 83,
    "heavysleetshowers": 84,
    "lightsnow": 71,
    "snow": 73,
    "heavysnow": 75,
    "lightsnowshowers": 85,
    "snowshowers": 85,
    "heavysnowshowers": 86,
    "lightrainandthunder": 95,
    "rainandthunder": 95,
    "heavyrainandthunder": 99,
    "lightrainshowersandthunder": 95,
    "rainshowersandthunder": 95,
    "heavyrainshowersandthunder": 99,
    "lightsleetandthunder": 95,
    "sleetandthunder": 95,
    "heavysleetandthunder": 99,
    "lightsnowandthunder": 95,
    "snowandthunder": 95,
    "heavysnowandthunder": 99,
}


def _wmo_from_metno_symbol(symbol: Any) -> Optional[int]:
    """Convert a MET Norway ``symbol_code`` to a WMO code, or ``None`` if unknown."""
    if not isinstance(symbol, str) or not symbol:
        return None
    return _METNO_SYMBOL_TO_WMO.get(symbol.split("_")[0].lower())


def _clamped_int(value: Any, low: int, high: int) -> Optional[int]:
    """Round a provider number to an int within ``[low, high]``, or ``None`` if it is missing."""
    number = optional_float(value)
    if number is None:
        return None
    return min(high, max(low, int(round(number))))


def _non_negative(value: Any, digits: int = 1) -> Optional[float]:
    """Round a provider number to ``digits`` decimals (never below zero), or ``None``."""
    number = optional_float(value)
    if number is None:
        return None
    return round(max(0.0, number), digits)


def _rounded(value: Any, digits: int = 1) -> Optional[float]:
    """Round a provider number that may legitimately be negative (temperatures), or ``None``."""
    number = optional_float(value)
    return None if number is None else round(number, digits)


class OpenMeteoCollector(BaseCollector[dict[str, Any]]):
    """Primary collector: Open-Meteo forecast with the 15-minute rain of the next hour and the daily forecast."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(
        self, http_client: Optional[httpx.AsyncClient] = None, grid_deg: Optional[float] = None
    ) -> None:
        super().__init__(
            provider_id="open_meteo_hd",
            name="Open-Meteo High-Def (AROME / HRRR / IFS / ICON)",
            domain="weather",
            # 15 minutes: the temporal resolution of the ``current`` and ``minutely_15`` data (the models
            # themselves are updated every hour at best). https://open-meteo.com/en/docs
            ttl_seconds=900,
            is_fallback=False,
            http_client=http_client,
        )
        self.grid_deg = (
            grid_deg
            if grid_deg is not None
            else grid_step_from_env("VIGIE_GRID_FORECAST_DEG", DEFAULT_FORECAST_GRID_DEG)
        )

    def cache_key_for(self, lat: float, lon: float) -> str:
        """Cache key of a point forecast: the position snapped to the forecast grid (no number of days)."""
        s_lat, s_lon = snap_to_grid(lat, lon, self.grid_deg)
        return f"om:{coord_str(s_lat)},{coord_str(s_lon)}"

    def get_stale_point(self, lat: float, lon: float) -> Optional[dict[str, Any]]:
        """Return the last cached forecast of a point, even if its TTL has expired."""
        return self._get_stale_cache(self.cache_key_for(lat, lon))

    async def fetch_point(self, lat: float, lon: float, allow_stale_on_error: bool = False) -> dict[str, Any]:
        """Fetch the full forecast (``MAX_FORECAST_DAYS`` days) near the coordinates, snapped to the forecast grid."""
        s_lat, s_lon = snap_to_grid(lat, lon, self.grid_deg)
        return await self.fetch(
            cache_key=self.cache_key_for(lat, lon),
            lat=s_lat,
            lon=s_lon,
            allow_stale_on_error=allow_stale_on_error,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> dict[str, Any]:
        lat = float(kwargs["lat"])
        lon = float(kwargs["lon"])

        params = {
            "latitude": coord_str(lat),
            "longitude": coord_str(lon),
            "current": (
                "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "pressure_msl,wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
                "precipitation,weather_code,uv_index"
            ),
            "minutely_15": "precipitation",
            "forecast_minutely_15": "4",
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_sum,precipitation_probability_max,"
                "wind_speed_10m_max,uv_index_max"
            ),
            "forecast_days": str(MAX_FORECAST_DAYS),
            "timezone": "auto",
        }
        resp = await client.get(self.BASE_URL, params=params)
        resp.raise_for_status()
        return self.parse_response(resp.json())

    @staticmethod
    def parse_response(payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize an Open-Meteo response. Missing variables become ``None``.

        Raises ``ValueError`` when there is no current temperature at all: such a
        response is unusable and the caller must fall back to another provider.
        """
        curr = payload.get("current")
        if not isinstance(curr, dict) or optional_float(curr.get("temperature_2m")) is None:
            raise ValueError("Open-Meteo response has no current temperature")
        daily = payload.get("daily") or {}
        min15 = payload.get("minutely_15") or {}

        wind_deg = optional_float(curr.get("wind_direction_10m"))
        current_weather = CurrentWeather(
            temp_c=_rounded(curr.get("temperature_2m")),
            feels_like_c=_rounded(curr.get("apparent_temperature")),
            humidity_pct=_clamped_int(curr.get("relative_humidity_2m"), 0, 100),
            pressure_hpa=_non_negative(curr.get("pressure_msl")),
            wind_kmh=_non_negative(curr.get("wind_speed_10m")),
            gust_kmh=_non_negative(curr.get("wind_gusts_10m")),
            wind_deg=None if wind_deg is None else int(round(wind_deg)) % 360,
            rain_1h_mm=_non_negative(curr.get("precipitation"), 2),
            weather_code=_clamped_int(curr.get("weather_code"), 0, 99),
            uv_index=_non_negative(curr.get("uv_index")),
            aqi_eu=None,
        )

        # The nowcast is all-or-nothing: four numeric 15-minute steps, else unknown.
        raw_steps = [optional_float(x) for x in (min15.get("precipitation") or [])[:4]]
        rain_next_60m: Optional[list[float]] = None
        if len(raw_steps) == 4 and all(step is not None for step in raw_steps):
            rain_next_60m = [round(max(0.0, step), 2) for step in raw_steps if step is not None]

        times = daily.get("time") or []
        codes = daily.get("weather_code") or []
        tmins = daily.get("temperature_2m_min") or []
        tmaxs = daily.get("temperature_2m_max") or []
        rains = daily.get("precipitation_sum") or []
        probs = daily.get("precipitation_probability_max") or []
        winds = daily.get("wind_speed_10m_max") or []
        uvs = daily.get("uv_index_max") or []

        def at(values: list[Any], idx: int) -> Any:
            return values[idx] if idx < len(values) else None

        daily_forecasts: list[DailyForecast] = []
        for idx, day_str in enumerate(times):
            try:
                day_iso = date.fromisoformat(str(day_str)).isoformat()
            except ValueError:
                continue
            daily_forecasts.append(
                DailyForecast(
                    date=day_iso,
                    code=_clamped_int(at(codes, idx), 0, 99),
                    tmin=_rounded(at(tmins, idx)),
                    tmax=_rounded(at(tmaxs, idx)),
                    rain_mm=_non_negative(at(rains, idx)),
                    rain_probability_pct=_clamped_int(at(probs, idx), 0, 100),
                    wind_max=_non_negative(at(winds, idx)),
                    uv_max=_non_negative(at(uvs, idx)),
                )
            )

        utc_offset = optional_float(payload.get("utc_offset_seconds"))
        return {
            "provider_name": "Open-Meteo",
            "utc_offset_s": None if utc_offset is None else int(utc_offset),
            "current": current_weather,
            "rain_next_60m": rain_next_60m,
            "daily_10d": daily_forecasts,
        }


class MetNorwayCollector(BaseCollector[dict[str, Any]]):
    """Fallback collector: MET Norway Locationforecast 2.0 (compact)."""

    BASE_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

    def __init__(
        self, http_client: Optional[httpx.AsyncClient] = None, grid_deg: Optional[float] = None
    ) -> None:
        super().__init__(
            provider_id="met_norway_fallback",
            name="MET Norway Locationforecast 2.0 (Global Fallback)",
            domain="weather",
            # Only used when the answer carries no ``Expires`` header: the cache keeps the value until the
            # ``Expires`` of each answer (terms: https://api.met.no/doc/TermsOfService). Not verified.
            ttl_seconds=300,
            is_fallback=True,
            http_client=http_client,
        )
        self.grid_deg = (
            grid_deg
            if grid_deg is not None
            else grid_step_from_env("VIGIE_GRID_FORECAST_DEG", DEFAULT_FORECAST_GRID_DEG)
        )

    async def fetch_point(
        self,
        lat: float,
        lon: float,
        allow_stale_on_error: bool = True,
        utc_offset_s: Optional[int] = None,
    ) -> dict[str, Any]:
        """Fetch the fallback forecast near the coordinates (snapped to the forecast grid).

        ``utc_offset_s`` (the zone configuration) is only used to group the hourly series
        into local calendar days; MET Norway does not report the local offset.
        """
        lat_r, lon_r = snap_to_grid(lat, lon, self.grid_deg)
        return await self.fetch(
            cache_key=f"metno:{coord_str(lat_r)},{coord_str(lon_r)}:{utc_offset_s}",
            lat=lat_r,
            lon=lon_r,
            utc_offset_s=utc_offset_s,
            allow_stale_on_error=allow_stale_on_error,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[dict[str, Any]]:
        """Ask MET Norway as its terms require: a conditional request, and the answer valid until ``Expires``.

        When a previous answer is cached, the request carries ``If-Modified-Since`` with exactly its
        ``Last-Modified``. ``304 Not Modified`` keeps the cached value (it is not parsed again); in both
        cases the value stays valid until the ``Expires`` of the new answer.
        """
        lat = float(kwargs["lat"])
        lon = float(kwargs["lon"])
        previous: Optional[CacheEntry[dict[str, Any]]] = kwargs.get("previous")

        headers: dict[str, str] = {}
        if previous is not None and previous.validators and previous.validators.get("Last-Modified"):
            headers["If-Modified-Since"] = previous.validators["Last-Modified"]

        resp = await client.get(
            self.BASE_URL,
            params={"lat": coord_str(lat), "lon": coord_str(lon)},
            headers=headers,
        )
        lifetime = response_lifetime_s(resp)
        if resp.status_code == 304:
            if previous is None:
                raise ValueError("MET Norway answered 304 Not Modified to a request that was not conditional")
            return Collected(previous.value, fresh_for_s=lifetime, validators=previous.validators)
        resp.raise_for_status()

        last_modified = resp.headers.get("Last-Modified")
        return Collected(
            self.parse_response(resp.json(), utc_offset_s=kwargs.get("utc_offset_s")),
            fresh_for_s=lifetime,
            validators={"Last-Modified": last_modified} if last_modified else None,
        )

    @staticmethod
    def parse_response(
        payload: dict[str, Any], days: int = MAX_FORECAST_DAYS, utc_offset_s: Optional[int] = None
    ) -> dict[str, Any]:
        """Convert the MET Norway timeseries to the unified schema. Missing variables are ``None``.

        The compact product has no apparent temperature, no gusts, no UV index and no
        15-minute precipitation, so ``feels_like_c``, ``gust_kmh``, ``uv_index`` and
        ``rain_next_60m`` are always unknown here. Daily values are computed from the
        hourly series over local calendar days (``utc_offset_s``, UTC when unknown); the
        first and the last day may be partial.
        """
        props = payload.get("properties") or {}
        timeseries = props.get("timeseries") or []
        if not timeseries:
            raise ValueError("MET Norway timeseries is empty")

        first = timeseries[0].get("data") or {}
        instant = (first.get("instant") or {}).get("details") or {}
        temp_c = optional_float(instant.get("air_temperature"))
        if temp_c is None:
            raise ValueError("MET Norway response has no current temperature")

        next_1h = first.get("next_1_hours") or {}
        next_6h = first.get("next_6_hours") or {}
        symbol = (next_1h.get("summary") or {}).get("symbol_code") or (next_6h.get("summary") or {}).get(
            "symbol_code"
        )
        wind_ms = optional_float(instant.get("wind_speed"))
        wind_deg = optional_float(instant.get("wind_from_direction"))

        current_weather = CurrentWeather(
            temp_c=round(temp_c, 1),
            feels_like_c=None,
            humidity_pct=_clamped_int(instant.get("relative_humidity"), 0, 100),
            pressure_hpa=_non_negative(instant.get("air_pressure_at_sea_level")),
            wind_kmh=None if wind_ms is None else round(max(0.0, wind_ms) * 3.6, 1),
            gust_kmh=None,
            wind_deg=None if wind_deg is None else int(round(wind_deg)) % 360,
            rain_1h_mm=_non_negative((next_1h.get("details") or {}).get("precipitation_amount"), 2),
            weather_code=_wmo_from_metno_symbol(symbol),
            uv_index=None,
            aqi_eu=None,
        )

        offset = timedelta(seconds=utc_offset_s or 0)
        by_day: dict[str, dict[str, Any]] = {}
        for item in timeseries:
            try:
                stamp = datetime.fromisoformat(str(item.get("time") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            day_key = (stamp + offset).date().isoformat()
            data = item.get("data") or {}
            inst = (data.get("instant") or {}).get("details") or {}
            n1 = data.get("next_1_hours") or {}
            n6 = data.get("next_6_hours") or {}

            entry = by_day.setdefault(
                day_key, {"temps": [], "winds": [], "rain": None, "codes": []}
            )
            t_val = optional_float(inst.get("air_temperature"))
            if t_val is not None:
                entry["temps"].append(t_val)
            w_val = optional_float(inst.get("wind_speed"))
            if w_val is not None:
                entry["winds"].append(w_val * 3.6)
            rain_val = optional_float((n1.get("details") or {}).get("precipitation_amount"))
            if rain_val is None:
                rain_val = optional_float((n6.get("details") or {}).get("precipitation_amount"))
            if rain_val is not None:
                entry["rain"] = (entry["rain"] or 0.0) + rain_val
            code = _wmo_from_metno_symbol(
                (n6.get("summary") or {}).get("symbol_code") or (n1.get("summary") or {}).get("symbol_code")
            )
            if code is not None:
                entry["codes"].append(code)

        daily_forecasts: list[DailyForecast] = []
        for day_key, vals in list(by_day.items())[: max(0, days)]:
            daily_forecasts.append(
                DailyForecast(
                    date=day_key,
                    # The most severe symbol of the day (WMO codes grow with severity).
                    code=max(vals["codes"]) if vals["codes"] else None,
                    tmin=round(min(vals["temps"]), 1) if vals["temps"] else None,
                    tmax=round(max(vals["temps"]), 1) if vals["temps"] else None,
                    rain_mm=None if vals["rain"] is None else round(vals["rain"], 1),
                    rain_probability_pct=None,
                    wind_max=round(max(vals["winds"]), 1) if vals["winds"] else None,
                    uv_max=None,
                )
            )

        return {
            "provider_name": "MET Norway (fallback)",
            "utc_offset_s": None,
            "current": current_weather,
            "rain_next_60m": None,
            "daily_10d": daily_forecasts,
        }


class RainViewerCollector(BaseCollector[dict[str, Any]]):
    """Collector of the RainViewer world radar metadata."""

    MAPS_URL = "https://api.rainviewer.com/public/weather-maps.json"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="rainviewer_radar",
            name="RainViewer Global Weather Radar API",
            domain="weather",
            # 10 minutes: the radar frames are 10 minutes apart. https://www.rainviewer.com/api/weather-maps-api.html
            ttl_seconds=600,
            http_client=http_client,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> dict[str, Any]:
        resp = await client.get(self.MAPS_URL)
        resp.raise_for_status()
        return self.parse_maps(resp.json())

    @staticmethod
    def parse_maps(payload: dict[str, Any]) -> dict[str, Any]:
        """Extract the latest observed radar frame and the tile URL template.

        Only past frames are used: a nowcast (future) frame is a forecast, not a radar image,
        and the free API no longer provides any. Raises ``ValueError`` when the response lists
        no past frame: no timestamp or URL is invented.
        """
        host = str(payload.get("host") or "")
        radar = payload.get("radar") or {}
        past = radar.get("past") or []

        latest = past[-1] if past else None
        if not host or not isinstance(latest, dict) or not latest.get("path") or not latest.get("time"):
            raise ValueError("RainViewer response lists no past radar frame")

        return {
            "host": host,
            "radar_timestamp_utc": int(latest["time"]),
            "radar_tile_url_template": f"{host}{latest['path']}/256/{{z}}/{{x}}/{{y}}/2/1_1.png",
            "past_frames_count": len(past),
        }
