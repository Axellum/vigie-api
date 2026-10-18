"""Collecteurs météo hyper-locale GPS (`lat, lon`), Fallback MET Norway et Radar Pluie RainViewer.

Architecture Multi-Providers Météo :
1. `OpenMeteoCollector` (Primaire) : Combine automatiquement les modèles haute définition
   locaux (Météo-France AROME 1.5 km, NOAA HRRR 3 km, DWD ICON-D2, ECMWF IFS, JMA)
   et fournit la prévision de pluie aux 15 minutes (`minutely_15`) aux coordonnées exactes.
2. `MetNorwayCollector` (Filet de sécurité / Fallback 100 % gratuit mondial) :
   Interroge `api.met.no/weatherapi/locationforecast/2.0/compact` pour assurer une continuité
   totale terrestre et maritime si Open-Meteo est indisponible.
3. `RainViewerCollector` (Radar Pluie) : Récupère les horodatages et modèles d'URL de tuiles
   radar mondiales et nowcast (`https://api.rainviewer.com/public/weather-maps.json`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector
from app.models.schemas import CurrentWeather, DailyForecast

DAYS_FR = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")

# Correspondance entre les codes symbol_code de MET Norway et les codes numériques WMO
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
    "lightsnow": 71,
    "snow": 73,
    "heavysnow": 75,
    "rainandthunder": 95,
    "heavyrainandthunder": 99,
}


class WeatherPointBundle(dict):
    """Dictionnaire typé contenant `current`, `rain_next_60m`, `daily_10d`, `utc_offset_s`, `provider_name`."""


class OpenMeteoCollector(BaseCollector[dict[str, Any]]):
    """Collecteur primaire Open-Meteo (Forecast + Minutely-15 + Daily 10j)."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="open_meteo_hd",
            name="Open-Meteo High-Def (AROME / HRRR / IFS / ICON)",
            domain="weather",
            ttl_seconds=300,
            is_fallback=False,
            http_client=http_client,
        )

    async def fetch_point(
        self, lat: float, lon: float, days: int = 7, allow_stale_on_error: bool = False
    ) -> dict[str, Any]:
        """Récupère la météo complète au centième de degré près (`lat, lon`)."""
        lat_r = round(float(lat), 2)
        lon_r = round(float(lon), 2)
        cache_key = f"om:{lat_r:.2f},{lon_r:.2f}:{days}"
        return await self.fetch(
            cache_key=cache_key,
            lat=lat_r,
            lon=lon_r,
            days=days,
            allow_stale_on_error=allow_stale_on_error,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> dict[str, Any]:
        lat = float(kwargs["lat"])
        lon = float(kwargs["lon"])
        days = int(kwargs.get("days", 7))

        params = {
            "latitude": f"{lat:.2f}",
            "longitude": f"{lon:.2f}",
            "current": (
                "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "surface_pressure,wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
                "precipitation,weather_code"
            ),
            "minutely_15": "precipitation",
            "forecast_minutely_15": "4",
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_sum,wind_speed_10m_max,uv_index_max"
            ),
            "forecast_days": str(min(10, max(1, days))),
            "timezone": "auto",
        }
        resp = await client.get(self.BASE_URL, params=params)
        resp.raise_for_status()
        return self.parse_response(resp.json())

    @staticmethod
    def parse_response(payload: dict[str, Any]) -> dict[str, Any]:
        """Normalise la réponse JSON d'Open-Meteo."""
        curr = payload.get("current") or {}
        daily = payload.get("daily") or {}
        min15 = payload.get("minutely_15") or {}

        uv_list = daily.get("uv_index_max") or [3.0]
        uv_val = round(float(uv_list[0] if uv_list and uv_list[0] is not None else 3.0), 1)

        current_weather = CurrentWeather(
            temp_c=round(float(curr.get("temperature_2m", 15.0)), 1),
            feels_like_c=round(float(curr.get("apparent_temperature", curr.get("temperature_2m", 15.0))), 1),
            humidity_pct=int(round(float(curr.get("relative_humidity_2m", 70)))),
            pressure_hpa=round(float(curr.get("surface_pressure", 1013.2)), 1),
            wind_kmh=round(float(curr.get("wind_speed_10m", 10.0)), 1),
            gust_kmh=round(float(curr.get("wind_gusts_10m", curr.get("wind_speed_10m", 15.0))), 1),
            wind_deg=int(round(float(curr.get("wind_direction_10m", 0)))) % 361,
            rain_1h_mm=round(float(curr.get("precipitation", 0.0)), 2),
            weather_code=int(curr.get("weather_code", 1)),
            uv_index=uv_val,
            aqi_eu=22,
        )

        raw_min15 = min15.get("precipitation") or []
        rain_next_60m = [
            round(float(x if x is not None else 0.0), 2) for x in raw_min15[:4]
        ]
        while len(rain_next_60m) < 4:
            rain_next_60m.append(round(current_weather.rain_1h_mm / 4.0, 2))

        daily_forecasts: list[DailyForecast] = []
        times = daily.get("time") or []
        codes = daily.get("weather_code") or []
        tmins = daily.get("temperature_2m_min") or []
        tmaxs = daily.get("temperature_2m_max") or []
        rains = daily.get("precipitation_sum") or []
        winds = daily.get("wind_speed_10m_max") or []

        for idx, dt_str in enumerate(times):
            try:
                dt_obj = datetime.strptime(str(dt_str), "%Y-%m-%d")
                day_label = DAYS_FR[dt_obj.weekday()]
            except ValueError:
                day_label = f"J+{idx}"

            daily_forecasts.append(
                DailyForecast(
                    d=day_label,
                    code=int(codes[idx]) if idx < len(codes) and codes[idx] is not None else 1,
                    tmin=round(float(tmins[idx]), 1) if idx < len(tmins) and tmins[idx] is not None else 10.0,
                    tmax=round(float(tmaxs[idx]), 1) if idx < len(tmaxs) and tmaxs[idx] is not None else 18.0,
                    rain_mm=round(float(rains[idx]), 1) if idx < len(rains) and rains[idx] is not None else 0.0,
                    wind_max=round(float(winds[idx]), 1) if idx < len(winds) and winds[idx] is not None else 15.0,
                )
            )

        return {
            "provider_name": "Open-Meteo (AROME/HRRR/IFS HD)",
            "utc_offset_s": int(payload.get("utc_offset_seconds", 0)),
            "current": current_weather,
            "rain_next_60m": rain_next_60m,
            "daily_10d": daily_forecasts,
        }


class MetNorwayCollector(BaseCollector[dict[str, Any]]):
    """Collecteur secondaire (Filet de sécurité 100 % gratuit mondial) : MET Norway Locationforecast 2.0."""

    BASE_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="met_norway_fallback",
            name="MET Norway Locationforecast 2.0 (Global Fallback)",
            domain="weather",
            ttl_seconds=300,
            is_fallback=True,
            http_client=http_client,
        )

    async def fetch_point(
        self, lat: float, lon: float, days: int = 7, allow_stale_on_error: bool = True
    ) -> dict[str, Any]:
        """Récupère la météo de secours depuis MET Norway aux coordonnées (`lat, lon`)."""
        lat_r = round(float(lat), 2)
        lon_r = round(float(lon), 2)
        cache_key = f"metno:{lat_r:.2f},{lon_r:.2f}:{days}"
        return await self.fetch(
            cache_key=cache_key,
            lat=lat_r,
            lon=lon_r,
            days=days,
            allow_stale_on_error=allow_stale_on_error,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> dict[str, Any]:
        lat = float(kwargs["lat"])
        lon = float(kwargs["lon"])
        days = int(kwargs.get("days", 7))

        resp = await client.get(
            self.BASE_URL,
            params={"lat": f"{lat:.2f}", "lon": f"{lon:.2f}"},
        )
        resp.raise_for_status()
        return self.parse_response(resp.json(), days=days)

    @staticmethod
    def parse_response(payload: dict[str, Any], days: int = 7) -> dict[str, Any]:
        """Convertit le format GeoJSON timeseries de MET Norway vers le schéma unifié Vigie Mondiale."""
        props = payload.get("properties") or {}
        timeseries = props.get("timeseries") or []
        if not timeseries:
            raise ValueError("MET Norway timeseries vide")

        first = timeseries[0].get("data") or {}
        instant = (first.get("instant") or {}).get("details") or {}
        next_1h = first.get("next_1_hours") or first.get("next_6_hours") or {}
        summary = (next_1h.get("summary") or {}).get("symbol_code", "partlycloudy_day")
        n1_details = next_1h.get("details") or {}

        base_symbol = str(summary).split("_")[0].lower()
        wmo_code = _METNO_SYMBOL_TO_WMO.get(base_symbol, 2)

        temp_c = round(float(instant.get("air_temperature", 15.0)), 1)
        wind_ms = float(instant.get("wind_speed", 3.0))
        wind_kmh = round(wind_ms * 3.6, 1)
        gust_ms = float(instant.get("wind_speed_of_gust", wind_ms * 1.35))
        gust_kmh = round(gust_ms * 3.6, 1)
        rain_1h = round(float(n1_details.get("precipitation_amount", 0.0)), 2)

        current_weather = CurrentWeather(
            temp_c=temp_c,
            feels_like_c=temp_c,
            humidity_pct=int(round(float(instant.get("relative_humidity", 75)))),
            pressure_hpa=round(float(instant.get("air_pressure_at_sea_level", 1013.2)), 1),
            wind_kmh=wind_kmh,
            gust_kmh=gust_kmh,
            wind_deg=int(round(float(instant.get("wind_from_direction", 0)))) % 361,
            rain_1h_mm=rain_1h,
            weather_code=wmo_code,
            uv_index=round(float(instant.get("ultraviolet_index_clear_sky", 2.5)), 1),
            aqi_eu=20,
        )

        step_mm = round(rain_1h / 4.0, 2)
        rain_next_60m = [step_mm, step_mm, step_mm, step_mm]

        # Agrégation journalière à partir des pas horaires disponibles
        by_day: dict[str, dict[str, Any]] = {}
        for item in timeseries:
            t_str = str(item.get("time") or "")
            if len(t_str) < 10:
                continue
            day_key = t_str[:10]
            data = item.get("data") or {}
            inst = (data.get("instant") or {}).get("details") or {}
            n1 = (data.get("next_1_hours") or data.get("next_6_hours") or {}).get("details") or {}

            t_val = float(inst.get("air_temperature", temp_c))
            w_val = float(inst.get("wind_speed", wind_ms)) * 3.6
            r_val = float(n1.get("precipitation_amount", 0.0))

            if day_key not in by_day:
                by_day[day_key] = {"tmin": t_val, "tmax": t_val, "rain": r_val, "wind": w_val, "code": wmo_code}
            else:
                entry = by_day[day_key]
                entry["tmin"] = min(entry["tmin"], t_val)
                entry["tmax"] = max(entry["tmax"], t_val)
                entry["rain"] += r_val
                entry["wind"] = max(entry["wind"], w_val)

        daily_forecasts: list[DailyForecast] = []
        for idx, (day_key, vals) in enumerate(list(by_day.items())[:days]):
            try:
                dt_obj = datetime.strptime(day_key, "%Y-%m-%d")
                day_label = DAYS_FR[dt_obj.weekday()]
            except ValueError:
                day_label = f"J+{idx}"
            daily_forecasts.append(
                DailyForecast(
                    d=day_label,
                    code=int(vals["code"]),
                    tmin=round(vals["tmin"], 1),
                    tmax=round(vals["tmax"], 1),
                    rain_mm=round(vals["rain"], 1),
                    wind_max=round(vals["wind"], 1),
                )
            )

        return {
            "provider_name": "MET Norway Locationforecast 2.0 (Fallback)",
            "utc_offset_s": 0,
            "current": current_weather,
            "rain_next_60m": rain_next_60m,
            "daily_10d": daily_forecasts,
        }


class RainViewerCollector(BaseCollector[dict[str, Any]]):
    """Collecteur des métadonnées radar de pluie mondiales RainViewer API."""

    MAPS_URL = "https://api.rainviewer.com/public/weather-maps.json"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="rainviewer_radar",
            name="RainViewer Global Weather Radar API",
            domain="weather",
            ttl_seconds=180,
            http_client=http_client,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> dict[str, Any]:
        resp = await client.get(self.MAPS_URL)
        resp.raise_for_status()
        return self.parse_maps(resp.json())

    @staticmethod
    def parse_maps(payload: dict[str, Any]) -> dict[str, Any]:
        """Extrait le dernier frame radar et le template d'URL de tuiles."""
        host = str(payload.get("host") or "https://tilecache.rainviewer.com")
        radar = payload.get("radar") or {}
        past = radar.get("past") or []
        nowcast = radar.get("nowcast") or []

        latest = nowcast[0] if nowcast else (past[-1] if past else None)
        if isinstance(latest, dict) and latest.get("path"):
            path = str(latest["path"])
            ts_utc = int(latest.get("time") or 0)
            tile_template = f"{host}{path}/256/{{z}}/{{x}}/{{y}}/2/1_1.png"
        else:
            ts_utc = int(datetime.now().timestamp())
            tile_template = f"{host}/v2/radar/{ts_utc}/256/{{z}}/{{x}}/{{y}}/2/1_1.png"

        return {
            "host": host,
            "radar_timestamp_utc": ts_utc,
            "radar_tile_url_template": tile_template,
            "past_frames_count": len(past),
            "nowcast_frames_count": len(nowcast),
        }
