"""Météo-France AROME-OM weather over the overseas territories, read through a vigie-meteo gateway (optional).

AROME-OM is Météo-France's high-resolution model of the overseas territories (0.025 degree, about 2.8 km; one model for the Antilles, French
Guiana, the Indian Ocean, New Caledonia and French Polynesia; four runs a day, hourly to +48 hours). Its files are large maps, so this API does
not read them: the gateway (``Axellum/vigie-meteo``, ``GET /v1/overseas?lat=&lon=``, read on 2026-11-18) keeps the hourly series of windows
drawn around the islands and answers a point. A point outside the windows (a boat at sea, the Marquesas, Wallis-et-Futuna) is a 404 and keeps
Open-Meteo's public weather.

What the answer holds, per hour: temperature, humidity, wind, gusts, direction, pressure, cloud cover and the rain of the hour. What it does not:
a weather code, a UV index, a precipitation probability, an apparent temperature. They stay with Open-Meteo's public API (probability, UV of the
days, apparent temperature) or are **worked out here** (the weather code, from the rain and the cloud cover, see :func:`weather_code`: it is not a
Météo-France product). A local day is given by the model only when its 24 hourly steps are all there (two runs are stitched by the gateway, so
usually today and tomorrow are); the other days are Open-Meteo's, as in :func:`~app.collectors.meteofrance_weather.merge_france_bundle`.

Without ``VIGIE_METEO_GATEWAY_URL``, or with ``VIGIE_METEO_GATEWAY_OVERSEAS`` at ``0``, or when the gateway fails, Open-Meteo's public API gives
the weather, as before.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.attribution import OPEN_METEO_WEATHER, credit_text, merge_credits
from app.collectors.base import BaseCollector
from app.collectors.grid import DEFAULT_FORECAST_GRID_DEG, coord_str, snap_to_grid
from app.models.schemas import CurrentWeather, DailyForecast, HourlyWeatherPoint

PROVIDER_ID = "meteofrance_overseas"
TOGGLE_ENV = "VIGIE_METEO_GATEWAY_OVERSEAS"
SOURCE_MODEL = "meteofrance-arome-om"
PROVIDER_NAME = "Météo-France (AROME Outre-mer)"
TTL_SECONDS = 900  # the hourly values are read again every quarter of an hour at most (a run only changes every six hours)

# Boxes (lat min, lat max, lon min, lon max) around the territories the gateway has windows in; a coarse filter so that a point elsewhere in the
# world does not cost a request. The gateway has the exact windows and answers 404 outside them (kept as "no overseas weather").
OVERSEAS_BOXES: tuple[tuple[float, float, float, float], ...] = (
    (14.0, 18.5, -63.5, -60.0),  # Guadeloupe, Martinique, Saint-Martin, Saint-Barthélemy
    (1.5, 6.5, -55.0, -51.0),  # French Guiana
    (-22.0, -12.0, 44.5, 56.0),  # Réunion, Mayotte
    (-23.5, -19.0, 163.0, 169.5),  # New Caledonia
    (-24.5, -14.0, -152.5, -145.0),  # Society, western Tuamotu, Austral islands
)


def meteo_overseas_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the weather of the overseas territories is read from the gateway: on unless ``VIGIE_METEO_GATEWAY_OVERSEAS`` is ``0`` / ``false`` / ``no`` / ``off``."""
    raw = (env if env is not None else os.environ).get(TOGGLE_ENV, "1")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def in_overseas_box(lat: float, lon: float) -> bool:
    """Whether the point is inside one of the boxes where the gateway is asked for the overseas weather."""
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in OVERSEAS_BOXES)


def weather_code(precip_mm: Optional[float], cloud_pct: Optional[float]) -> Optional[int]:
    """A WMO weather code worked out from the rain of the hour and the cloud cover: **ours**, not Météo-France's.

    The model gives neither a weather code nor the type of precipitation, so there is no snow, hail, fog or thunder here: a tropical rain is
    ``rain``. Rain of the hour: 0.1 mm drizzle (51), 0.5 mm slight rain (61), 2.5 mm moderate (63), 8 mm heavy (65); no rain: clear (0) under
    20 % of cloud, mainly clear (1) under 45 %, partly cloudy (2) under 75 %, overcast (3) beyond. ``None`` without both values for a dry hour.
    """
    if precip_mm is not None and precip_mm >= 0.1:
        return 65 if precip_mm >= 8.0 else 63 if precip_mm >= 2.5 else 61 if precip_mm >= 0.5 else 51
    if cloud_pct is None or precip_mm is None:
        return None
    return 0 if cloud_pct < 20 else 1 if cloud_pct < 45 else 2 if cloud_pct < 75 else 3


def _number(value: Any, low: float, high: float, what: str, required: bool = False) -> Optional[float]:
    if value is None:
        if required:
            raise ValueError(f"{what} is missing")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{what} is out of range")
    return float(value)


def _epoch(value: Any) -> int:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError("time without a time zone")
    return int(moment.timestamp())


class OverseasHour:
    """One valid hour: the values are ``None`` when the model had none (the gust and the rain of the first hour of a run)."""

    __slots__ = ("time_utc", "temp_c", "humidity_pct", "wind_kmh", "gust_kmh", "wind_deg", "pressure_hpa", "cloud_pct", "rain_mm")

    def __init__(self, time_utc: int, temp_c: float, humidity_pct, wind_kmh, gust_kmh, wind_deg, pressure_hpa, cloud_pct, rain_mm) -> None:  # noqa: ANN001
        self.time_utc = time_utc
        self.temp_c = temp_c
        self.humidity_pct = humidity_pct
        self.wind_kmh = wind_kmh
        self.gust_kmh = gust_kmh
        self.wind_deg = wind_deg
        self.pressure_hpa = pressure_hpa
        self.cloud_pct = cloud_pct
        self.rain_mm = rain_mm


class OverseasRaw:
    """The checked answer of the gateway: the hours, the time zone offset of the territory and the run."""

    def __init__(self, run_utc: int, utc_offset_s: int, hours: tuple[OverseasHour, ...]) -> None:
        self.run_utc = run_utc
        self.utc_offset_s = utc_offset_s
        self.hours = hours


EMPTY = OverseasRaw(0, 0, ())  # a point outside the windows of the gateway: no overseas weather, not a failure of the source


def parse_gateway_overseas(payload: Any) -> OverseasRaw:
    """The hours of a gateway answer. ``ValueError`` when it is not AROME-OM's or a value cannot be read or is impossible."""
    if not isinstance(payload, dict):
        raise ValueError("gateway answer is not an object")
    model = payload.get("source_model")
    if model != SOURCE_MODEL:
        raise ValueError(f"gateway answer is not AROME-OM (source_model={str(model)[:40]!r})")
    offset = payload.get("utc_offset_s")
    if isinstance(offset, bool) or not isinstance(offset, int) or not -14 * 3600 <= offset <= 14 * 3600:
        raise ValueError("gateway answer without a valid time zone offset")
    items = payload.get("steps")
    if not isinstance(items, list) or not items:
        raise ValueError("gateway answer without steps")
    by_time: dict[int, OverseasHour] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("gateway step is not an object")
        when = _epoch(item.get("time"))
        humidity = _number(item.get("humidity_pct"), 0.0, 100.0, "humidity")
        wind_deg = _number(item.get("wind_direction_deg"), 0.0, 360.0, "wind direction")
        by_time[when] = OverseasHour(
            when,
            _number(item.get("temperature_c"), -60.0, 60.0, "temperature", required=True),  # type: ignore[arg-type]
            humidity,
            _number(item.get("wind_kmh"), 0.0, 500.0, "wind"),
            _number(item.get("gust_kmh"), 0.0, 600.0, "gust"),
            wind_deg,
            _number(item.get("pressure_hpa"), 800.0, 1100.0, "pressure"),
            _number(item.get("cloud_cover_pct"), 0.0, 100.0, "cloud cover"),
            _number(item.get("precip_mm"), 0.0, 1000.0, "rain"),
        )
    return OverseasRaw(_epoch(payload.get("run")), offset, tuple(by_time[t] for t in sorted(by_time)))


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    return None if value is None else round(value, digits)


def current_weather(raw: OverseasRaw, now_s: float) -> Optional[CurrentWeather]:
    """The conditions of the hour nearest to ``now_s`` (within 90 minutes), or ``None``."""
    if not raw.hours:
        return None
    nearest = min(raw.hours, key=lambda h: abs(h.time_utc - now_s))
    if abs(nearest.time_utc - now_s) > 5400:
        return None
    return CurrentWeather(
        temp_c=_round(nearest.temp_c),
        humidity_pct=None if nearest.humidity_pct is None else int(round(nearest.humidity_pct)),
        pressure_hpa=_round(nearest.pressure_hpa),
        wind_kmh=_round(nearest.wind_kmh),
        gust_kmh=_round(nearest.gust_kmh),
        wind_deg=None if nearest.wind_deg is None else int(round(nearest.wind_deg)) % 360,
        rain_1h_mm=_round(nearest.rain_mm, 2),
        weather_code=weather_code(nearest.rain_mm, nearest.cloud_pct),
    )


def daily_forecasts(raw: OverseasRaw) -> list[DailyForecast]:
    """The local days whose 24 hours are all in the series: minimum and maximum temperature, rain total, maximum wind and the most severe
    weather code of the hours. The rain probability and the UV maximum are not the model's (``None``: the caller completes them)."""
    # A step is the hour that ENDS at its time (the rain and the gust are those of that hour): it belongs to the local day of its middle, so a
    # day is the steps from 01:00 to 24:00 (00:00 of the next day).
    shift = timedelta(seconds=raw.utc_offset_s - 1800)
    days: dict[str, list[OverseasHour]] = {}
    for hour in raw.hours:
        local = datetime.fromtimestamp(hour.time_utc, tz=timezone.utc) + shift
        days.setdefault(local.date().isoformat(), []).append(hour)
    out: list[DailyForecast] = []
    for date, hours in sorted(days.items()):
        if len(hours) != 24:  # the steps are one per hour, de-duplicated by time: 24 of them are the 24 hours of the day
            continue
        rains = [h.rain_mm for h in hours if h.rain_mm is not None]
        winds = [h.wind_kmh for h in hours if h.wind_kmh is not None]
        codes = [c for c in (weather_code(h.rain_mm, h.cloud_pct) for h in hours) if c is not None]
        out.append(
            DailyForecast(
                date=date,
                code=max(codes) if codes else None,
                tmin=_round(min(h.temp_c for h in hours)),
                tmax=_round(max(h.temp_c for h in hours)),
                rain_mm=_round(sum(rains), 1) if rains else None,
                wind_max=_round(max(winds)) if winds else None,
            )
        )
    return out


def merge_overseas_bundle(raw: OverseasRaw, public_daily: Optional[Mapping[str, Any]], now_s: float) -> Optional[dict[str, Any]]:
    """The weather bundle of a point of the overseas territories: AROME-OM's current conditions and complete days, completed by Open-Meteo.

    - ``current``: the model's (``current_weather``); ``None`` as a whole when the series has no hour near now (the caller falls back).
    - ``daily_10d``: for a day the model covers completely, its values; the rain probability and the UV maximum of that day, and every
      other day, from ``public_daily`` (Open-Meteo's public API). Without ``public_daily`` only the model's days.
    - ``hourly_cloud_cover``: ``public_daily``'s. ``rain_next_60m``: ``None`` (the model's rain is hourly, no quarter-hour amounts).
    - credits: Météo-France's, plus Open-Meteo's only when ``public_daily`` contributed.
    """
    current = current_weather(raw, now_s)
    if current is None:
        return None
    public_days: dict[str, DailyForecast] = {}
    if public_daily is not None:
        public_days = {day.date: day for day in public_daily.get("daily_10d", [])}
    days: dict[str, DailyForecast] = {}
    for day in daily_forecasts(raw):
        pub = public_days.get(day.date)
        days[day.date] = day.model_copy(update={"rain_probability_pct": pub.rain_probability_pct if pub else None, "uv_max": pub.uv_max if pub else None})
    first_date = min(days, default="")
    for date, pub in public_days.items():
        if date >= first_date:  # a public entry older than the first day of the model is yesterday's (an expired cache)
            days.setdefault(date, pub)
    credits = [credit_text(PROVIDER_ID)]
    if public_daily is not None:
        credits.append(credit_text(*OPEN_METEO_WEATHER))
    hourly_all = [
        HourlyWeatherPoint(
            time_utc=h.time_utc,
            temp_c=_round(h.temp_c),
            precipitation_mm=_round(h.rain_mm, 2),
            wind_kmh=_round(h.wind_kmh),
            wind_gust_kmh=_round(h.gust_kmh),
            wind_deg=None if h.wind_deg is None else int(round(h.wind_deg)) % 360,
            cloud_cover_pct=None if h.cloud_pct is None else int(round(h.cloud_pct)),
            pressure_hpa=_round(h.pressure_hpa),
            weather_code=weather_code(h.rain_mm, h.cloud_pct),
        )
        for h in raw.hours
    ]
    return {
        "provider_name": PROVIDER_NAME,
        "attribution": merge_credits(*credits),
        "utc_offset_s": raw.utc_offset_s,
        "current": current,
        "rain_next_60m": None,
        "daily_10d": [days[date] for date in sorted(days)],
        "hourly_cloud_cover": list((public_daily or {}).get("hourly_cloud_cover", [])),
        "hourly_all": hourly_all,
    }


class MeteoFranceOverseasCollector(BaseCollector[OverseasRaw]):
    """The AROME-OM hours of a point through the gateway, cached per cell of the forecast grid."""

    def __init__(
        self,
        base_url: str,
        grid_deg: float = DEFAULT_FORECAST_GRID_DEG,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            provider_id=PROVIDER_ID,
            name="Météo-France AROME-OM weather of the overseas territories (vigie-meteo gateway)",
            domain="weather",
            ttl_seconds=TTL_SECONDS,
            failure_threshold=3,
            recovery_timeout_seconds=120,
            timeout_seconds=8.0,
            http_client=http_client,
        )
        self.base_url = base_url.rstrip("/")
        self.grid_deg = grid_deg

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> OverseasRaw:
        kwargs.pop("previous", None)
        lat, lon = float(kwargs["lat"]), float(kwargs["lon"])
        response = await self._get(client, f"{self.base_url}/v1/overseas", params={"lat": coord_str(lat), "lon": coord_str(lon)})
        if response.status_code == 404:
            return EMPTY
        response.raise_for_status()
        return parse_gateway_overseas(response.json())

    async def fetch_point(self, lat: float, lon: float) -> OverseasRaw:
        """The hours of a point (the grid cell's): raises when the gateway cannot give them, or the point is outside its windows (``EMPTY``)."""
        s_lat, s_lon = snap_to_grid(lat, lon, self.grid_deg)
        raw = await self.fetch(f"mf-om:{coord_str(s_lat)},{coord_str(s_lon)}", lat=s_lat, lon=s_lon, allow_stale_on_error=False)
        if not raw.hours:
            raise ValueError("the point is outside the overseas windows of the gateway")
        return raw
