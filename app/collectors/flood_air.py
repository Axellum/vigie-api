"""Complementary collectors: river discharge (GloFAS), air quality / ash, USGS volcano alerts.

1. ``OpenMeteoFloodCollector``: Open-Meteo Flood API (Copernicus GloFAS hydrological model)
   for ``river_discharge``, its ensemble mean and maximum, and a heuristic flood indicator.
2. ``OpenMeteoAirQualityCollector``: Open-Meteo Air Quality API for ``european_aqi``,
   ``us_aqi``, ``pm2_5``, ``pm10``, ``uv_index`` and ``dust`` (fine volcanic ash and Saharan
   dust plumes).
3. ``USGSVolcanoCollector``: USGS Volcano Hazards Program, elevated-alert volcanoes, with
   the aviation color code mapped to the unified severity.

A variable a provider does not supply stays ``None``. A response that carries no usable
value at all raises ``ValueError``, so the caller reports "unknown" instead of a default.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.attribution import AIR_QUALITY, FLOOD, credit_text
from app.collectors.base import BaseCollector, optional_float, short_title
from app.collectors.grid import (
    DEFAULT_AIR_QUALITY_GRID_DEG,
    DEFAULT_FLOOD_GRID_DEG,
    coord_str,
    grid_step_from_env,
    snap_to_grid,
)
from app.collectors.open_meteo_hosts import get_open_meteo
from app.collectors.weights import open_meteo_weight
from app.models.pollens import (
    POLLEN_API_VARIABLES,
    POLLEN_SPECIES,
    PollenDailyForecast,
    PollenSpecies,
    PollenSpeciesCounts,
    PollenStatus,
)
from app.models.schemas import (
    AirHourlyPoint,
    AirQualityStatus,
    FloodRiskStatus,
    HazardAlert,
    HazardCategory,
    SeverityLevel,
)

DEFAULT_OPEN_METEO_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


def open_meteo_air_quality_url(env: Optional[Mapping[str, str]] = None) -> str:
    """The Open-Meteo Air Quality endpoint: ``VIGIE_OPEN_METEO_AIR_QUALITY_URL`` when set (e.g. a self-hosted
    Open-Meteo, asked first with the public API as a fallback, see ``open_meteo_hosts.py``), else the public one."""
    raw = ((env if env is not None else os.environ).get("VIGIE_OPEN_METEO_AIR_QUALITY_URL") or "").strip()
    return raw or DEFAULT_OPEN_METEO_AIR_QUALITY_URL


def _numbers(values: Any) -> list[float]:
    """Keep the finite numbers of a provider list."""
    return [x for x in (optional_float(v) for v in (values or [])) if x is not None]


class OpenMeteoFloodCollector(BaseCollector[FloodRiskStatus]):
    """Hydrological collector: Copernicus GloFAS through the Open-Meteo Flood API."""

    BASE_URL = "https://flood-api.open-meteo.com/v1/flood"

    def __init__(
        self, http_client: Optional[httpx.AsyncClient] = None, grid_deg: Optional[float] = None
    ) -> None:
        super().__init__(
            provider_id="open_meteo_flood",
            name="Open-Meteo Flood API (Copernicus GloFAS)",
            domain="disasters",
            # 6 hours: the GloFAS forecast is updated once a day. https://open-meteo.com/en/docs/flood-api
            ttl_seconds=21600,
            http_client=http_client,
        )
        self.grid_deg = (
            grid_deg if grid_deg is not None else grid_step_from_env("VIGIE_GRID_FLOOD_DEG", DEFAULT_FLOOD_GRID_DEG)
        )

    def call_weight(self, params: Mapping[str, str]) -> float:
        """Weight of one request under the Open-Meteo rule (3 variables, 7 days: 1.0)."""
        return open_meteo_weight(params, default_forecast_days=92)

    async def fetch_flood_risk(self, lat: float, lon: float) -> FloodRiskStatus:
        """Fetch river discharge and flood risk near ``lat, lon`` (snapped to the flood grid)."""
        lat_r, lon_r = snap_to_grid(lat, lon, self.grid_deg)
        cache_key = f"flood:{coord_str(lat_r)},{coord_str(lon_r)}"
        fetched = await self.fetch_ex(cache_key=cache_key, lat=lat_r, lon=lon_r)
        return fetched.value.model_copy(update={"stale": True}) if fetched.stale else fetched.value

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> FloodRiskStatus:
        lat = float(kwargs["lat"])
        lon = float(kwargs["lon"])
        params = {
            "latitude": coord_str(lat),
            "longitude": coord_str(lon),
            "daily": "river_discharge,river_discharge_mean,river_discharge_max",
            "forecast_days": "7",
        }
        resp = await self._get(client, self.BASE_URL, params=params)
        resp.raise_for_status()
        return self.parse_response(resp.json())

    @staticmethod
    def parse_response(payload: dict[str, Any]) -> FloodRiskStatus:
        """Read the river discharge; no flood level is drawn from it.

        ``river_discharge_max`` is the highest of the ensemble members and ``river_discharge_mean`` their
        mean ("statistical analysis from ensemble members", https://open-meteo.com/en/docs/flood-api), so the
        ratio of the two measures how far the forecast scenarios spread, not how high the river is. A level
        drawn from it said "yellow flood" for the Seine in Paris on 2026-10-20 while the day's discharge
        (32.65 m3/s) was below the mean forecast (55.63 m3/s): the members only disagreed (maximum 146.57).
        The API gives no flood threshold or return period, so ``flood_alert_level`` stays null (unknown);
        official flood warnings come from ``/v1/vigilances`` (Vigicrues, MeteoAlarm, NWS...).
        Raises ``ValueError`` when the response has no river discharge value.
        """
        daily = payload.get("daily") or {}
        discharges = _numbers(daily.get("river_discharge"))
        if not discharges:
            raise ValueError("Open-Meteo Flood response has no river discharge")
        means = _numbers(daily.get("river_discharge_mean"))
        maxes = _numbers(daily.get("river_discharge_max")) or discharges

        curr_q = round(discharges[0], 2)
        max_q = round(max(maxes), 2)
        mean_q = round(sum(means) / len(means), 2) if means else None

        ratio = None if mean_q is None else round(max_q / max(mean_q, 1.0), 2)

        return FloodRiskStatus(
            river_discharge_m3s=max(0.0, curr_q),
            river_discharge_mean_m3s=None if mean_q is None else max(0.0, mean_q),
            river_discharge_max_m3s=max(0.0, max_q),
            anomaly_ratio=ratio,
            flood_alert_level=None,
            attribution=credit_text(*FLOOD),
        )


# CAMS European Air Quality domain (Open-Meteo ``cams_europe``: 30 N..72 N, 25 W..45 E).
# Outside this box Open-Meteo returns ``null`` for all six pollen variables.
CAMS_EUROPE_LAT_MIN = 30.0
CAMS_EUROPE_LAT_MAX = 72.0
CAMS_EUROPE_LON_MIN = -25.0
CAMS_EUROPE_LON_MAX = 45.0
POLLEN_FORECAST_DAYS = 4
# ``european_aqi``, ``pm10``, ``pm2_5`` and ``dust`` are already in ``hourly`` at CAMS's 1-hour resolution, so asking
# for only ``us_aqi,uv_index`` in ``current`` avoids double-counting them in the Open-Meteo call weight (8 variables
# outside Europe -> weight 1.0; 14 with the 6 European pollens -> weight 1.4).
AIR_CURRENT_VARIABLES = "us_aqi,uv_index"
AIR_HOURLY_VARIABLES = ("pm2_5", "pm10", "ozone", "nitrogen_dioxide", "dust", "european_aqi")
AIR_HOURLY_FORECAST_DAYS = 4
AIR_HOURLY_MAX_HOURS = 72


def is_in_cams_europe(lat: float, lon: float) -> bool:
    """Return ``True`` when ``(lat, lon)`` lies inside the CAMS European Air Quality domain."""
    return CAMS_EUROPE_LAT_MIN <= lat < CAMS_EUROPE_LAT_MAX and CAMS_EUROPE_LON_MIN <= lon <= CAMS_EUROPE_LON_MAX


class OpenMeteoAirQualityCollector(BaseCollector[AirQualityStatus]):
    """Air quality, UV, dust / ash and CAMS European pollen collector (Open-Meteo Air Quality API)."""

    BASE_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

    def __init__(
        self, http_client: Optional[httpx.AsyncClient] = None, grid_deg: Optional[float] = None
    ) -> None:
        super().__init__(
            provider_id="open_meteo_aqi",
            name="Open-Meteo Air Quality, Dust & UV API",
            domain="weather",
            # 1 hour: the CAMS forecast has hourly values (Europe) or 3-hourly ones (global) and is updated
            # every 12 to 24 hours. https://open-meteo.com/en/docs/air-quality-api
            ttl_seconds=3600,
            http_client=http_client,
        )
        self.BASE_URL = open_meteo_air_quality_url()
        self.grid_deg = (
            grid_deg
            if grid_deg is not None
            else grid_step_from_env("VIGIE_GRID_AIR_QUALITY_DEG", DEFAULT_AIR_QUALITY_GRID_DEG)
        )

    def call_weight(self, params: Mapping[str, str]) -> float:
        """Weight of one request under the Open-Meteo rule (1.0 outside Europe, 1.4 in Europe with 6 pollens)."""
        return open_meteo_weight(params, default_forecast_days=5)

    @staticmethod
    def request_params(lat: float, lon: float) -> dict[str, str]:
        """Build Open-Meteo Air Quality query parameters (pollen hourly series requested only inside CAMS Europe)."""
        hourly_vars = list(AIR_HOURLY_VARIABLES)
        if is_in_cams_europe(lat, lon):
            hourly_vars.extend(POLLEN_API_VARIABLES.values())
        return {
            "latitude": coord_str(lat),
            "longitude": coord_str(lon),
            "current": AIR_CURRENT_VARIABLES,
            "hourly": ",".join(hourly_vars),
            "forecast_days": str(AIR_HOURLY_FORECAST_DAYS),
        }

    async def fetch_air_quality(self, lat: float, lon: float) -> AirQualityStatus:
        """Fetch the AQI, fine particles, UV, dust / ash, hourly air series and (in Europe) pollens near ``lat, lon``."""
        lat_r, lon_r = snap_to_grid(lat, lon, self.grid_deg)
        cache_key = f"aqi:{coord_str(lat_r)},{coord_str(lon_r)}"
        fetched = await self.fetch_ex(cache_key=cache_key, lat=lat_r, lon=lon_r)
        if not fetched.stale:
            return fetched.value
        updates: dict[str, Any] = {"stale": True}
        if fetched.value.pollens is not None:
            updates["pollens"] = fetched.value.pollens.model_copy(update={"stale": True})
        return fetched.value.model_copy(update=updates).with_hourly(fetched.value.hourly_series)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> AirQualityStatus:
        lat = float(kwargs["lat"])
        lon = float(kwargs["lon"])
        params = self.request_params(lat, lon)
        resp = await get_open_meteo(self, client, self.BASE_URL, DEFAULT_OPEN_METEO_AIR_QUALITY_URL, params)
        resp.raise_for_status()
        return self.parse_response(resp.json())

    @staticmethod
    def parse_pollens(payload: dict[str, Any]) -> Optional[PollenStatus]:
        """Extract CAMS European pollen observations and up-to-4-day daily peaks.

        Returns ``None`` when the provider supplied no non-null pollen values (e.g. outside Europe
        or when no pollen variables were requested). Never fabricates missing values.
        """
        curr = payload.get("current") or {}
        hourly = payload.get("hourly") or {}
        raw_times = hourly.get("time")
        times: list[str] = [t for t in raw_times if isinstance(t, str)] if isinstance(raw_times, list) else []

        observed_raw = curr.get("time")
        observed_time = observed_raw.strip() if isinstance(observed_raw, str) and observed_raw.strip() else None
        obs_idx: Optional[int] = None
        if observed_time is not None and times:
            try:
                obs_idx = times.index(observed_time)
            except ValueError:
                obs_idx = None

        current_fields: dict[str, Optional[float]] = {}
        current_by_species: dict[PollenSpecies, Optional[float]] = {}
        day_buckets: dict[str, dict[PollenSpecies, list[float]]] = {}

        for species in POLLEN_SPECIES:
            api_var = POLLEN_API_VARIABLES[species]
            arr = hourly.get(api_var)
            series = arr if isinstance(arr, list) else []

            cur_val = optional_float(curr.get(api_var))
            if cur_val is None and obs_idx is not None and obs_idx < len(series):
                cur_val = optional_float(series[obs_idx])
            if cur_val is not None and cur_val < 0.0:
                cur_val = None
            rounded_cur = None if cur_val is None else round(cur_val, 1)
            current_fields[f"{species}_grains_m3"] = rounded_cur
            current_by_species[species] = rounded_cur

            for idx, ts in enumerate(times):
                if idx >= len(series) or len(ts) < 10:
                    continue
                val = optional_float(series[idx])
                if val is None or val < 0.0:
                    continue
                day = ts[:10]
                day_buckets.setdefault(day, {}).setdefault(species, []).append(val)

        has_current = any(v is not None for v in current_fields.values())
        current_obj = PollenSpeciesCounts(**current_fields) if has_current else None

        daily_list: list[PollenDailyForecast] = []
        for day, per_species in list(day_buckets.items())[:POLLEN_FORECAST_DAYS]:
            day_fields = {
                f"{species}_max_grains_m3": (
                    round(max(per_species[species]), 1) if per_species.get(species) else None
                )
                for species in POLLEN_SPECIES
            }
            if any(v is not None for v in day_fields.values()):
                daily_list.append(PollenDailyForecast(date=day, **day_fields))

        if current_obj is None and not daily_list:
            return None

        dominant_species: Optional[PollenSpecies] = None
        if current_obj is not None:
            best_val = 0.0
            for species in POLLEN_SPECIES:
                val = current_by_species[species]
                if val is not None and val > best_val:
                    best_val = val
                    dominant_species = species

        derived: list[str] = []
        if daily_list:
            derived.append("daily")
        if dominant_species is not None:
            derived.append("dominant_species")

        return PollenStatus(
            observed_time=observed_time if current_obj is not None else None,
            current=current_obj,
            daily=daily_list,
            dominant_species=dominant_species,
            derived=derived,
            attribution=credit_text(*AIR_QUALITY),
        )

    @staticmethod
    def _observed_hour_index(curr: Mapping[str, Any], times: list[str]) -> Optional[int]:
        """Index of ``curr['time']`` inside ``hourly['time']``, or ``None`` when absent / unmatched."""
        observed_raw = curr.get("time")
        observed_time = observed_raw.strip() if isinstance(observed_raw, str) and observed_raw.strip() else None
        if observed_time is None or not times:
            return None
        try:
            return times.index(observed_time)
        except ValueError:
            return None

    @staticmethod
    def parse_hourly(payload: dict[str, Any]) -> Optional[list[AirHourlyPoint]]:
        """Extract up to 72 hours of hourly air quality starting from ``current.time`` (or the first hour).

        Returns ``None`` when the provider supplied no non-null air quality values in ``hourly``.
        """
        curr = payload.get("current") or {}
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict):
            return None
        raw_times = hourly.get("time")
        if not isinstance(raw_times, list) or not raw_times:
            return None
        times = [t for t in raw_times if isinstance(t, str)]
        if not times:
            return None

        offset = optional_float(payload.get("utc_offset_seconds")) or 0.0
        start_idx = OpenMeteoAirQualityCollector._observed_hour_index(curr, times)
        if start_idx is None:
            start_idx = 0

        def col(name: str) -> list[Any]:
            arr = hourly.get(name)
            return arr if isinstance(arr, list) else []

        def at(values: list[Any], idx: int) -> Any:
            return values[idx] if idx < len(values) else None

        def non_neg(value: Any) -> Optional[float]:
            num = optional_float(value)
            return None if num is None else round(max(0.0, num), 1)

        pm25_arr = col("pm2_5")
        pm10_arr = col("pm10")
        o3_arr = col("ozone")
        no2_arr = col("nitrogen_dioxide")
        dust_arr = col("dust")
        eaqi_arr = col("european_aqi")

        points: list[AirHourlyPoint] = []
        for idx in range(start_idx, len(raw_times)):
            if len(points) >= AIR_HOURLY_MAX_HOURS:
                break
            stamp = raw_times[idx]
            if not isinstance(stamp, str) or not stamp.strip():
                continue
            text = stamp.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                continue
            epoch = int(dt.timestamp()) if dt.tzinfo is not None else int(dt.replace(tzinfo=timezone.utc).timestamp() - offset)

            pm2_5 = non_neg(at(pm25_arr, idx))
            pm10 = non_neg(at(pm10_arr, idx))
            ozone = non_neg(at(o3_arr, idx))
            no2 = non_neg(at(no2_arr, idx))
            dust = non_neg(at(dust_arr, idx))
            eaqi_raw = optional_float(at(eaqi_arr, idx))
            eaqi = None if eaqi_raw is None else max(0, int(round(eaqi_raw)))
            if all(v is None for v in (pm2_5, pm10, ozone, no2, dust, eaqi)):
                continue
            points.append(
                AirHourlyPoint(
                    time_utc=epoch,
                    pm2_5=pm2_5,
                    pm10=pm10,
                    ozone=ozone,
                    nitrogen_dioxide=no2,
                    dust=dust,
                    european_aqi=eaqi,
                )
            )

        return points or None

    @staticmethod
    def parse_response(payload: dict[str, Any]) -> AirQualityStatus:
        """Normalize an Open-Meteo Air Quality response. Missing variables become ``None``.

        ``european_aqi``, ``pm2_5``, ``pm10`` and ``dust`` are read from ``current`` when present, else from the
        matching ``current.time`` hour of ``hourly`` (avoiding double-counting them in the Open-Meteo call weight).
        Raises ``ValueError`` when the response carries neither air quality nor pollen values.
        """
        curr = payload.get("current") or {}
        hourly = payload.get("hourly") or {}
        raw_times = hourly.get("time") if isinstance(hourly, dict) else None
        times = [t for t in raw_times if isinstance(t, str)] if isinstance(raw_times, list) else []
        obs_idx = OpenMeteoAirQualityCollector._observed_hour_index(curr, times)
        if obs_idx is None and times and not curr.get("time"):
            obs_idx = 0

        def current_or_hourly(key: str) -> Optional[float]:
            val = optional_float(curr.get(key))
            if val is not None:
                return val
            if obs_idx is not None and isinstance(hourly, dict):
                arr = hourly.get(key)
                if isinstance(arr, list) and obs_idx < len(arr):
                    return optional_float(arr[obs_idx])
            return None

        eu_raw = current_or_hourly("european_aqi")
        us_raw = optional_float(curr.get("us_aqi"))
        pm2_5 = current_or_hourly("pm2_5")
        pm10 = current_or_hourly("pm10")
        uv = optional_float(curr.get("uv_index"))
        dust = current_or_hourly("dust")
        pollens = OpenMeteoAirQualityCollector.parse_pollens(payload)
        air_hourly = OpenMeteoAirQualityCollector.parse_hourly(payload)
        if all(v is None for v in (eu_raw, us_raw, pm2_5, pm10, dust)) and pollens is None and air_hourly is None:
            raise ValueError("Open-Meteo Air Quality response has no air quality value")

        eu_aqi = None if eu_raw is None else max(0, int(round(eu_raw)))
        warning = bool(
            (dust is not None and dust >= 100.0)
            or (pm10 is not None and pm10 >= 120.0)
            or (eu_aqi is not None and eu_aqi >= 100)
        )
        return AirQualityStatus(
            european_aqi=eu_aqi,
            us_aqi=None if us_raw is None else max(0, int(round(us_raw))),
            pm2_5_ugm3=None if pm2_5 is None else round(max(0.0, pm2_5), 1),
            pm10_ugm3=None if pm10 is None else round(max(0.0, pm10), 1),
            uv_index=None if uv is None else round(max(0.0, uv), 1),
            dust_ugm3=None if dust is None else round(max(0.0, dust), 1),
            ash_or_dust_warning=warning,
            pollens=pollens,
            attribution=credit_text(*AIR_QUALITY),
        ).with_hourly(air_hourly)


_AVIATION_COLOR_TO_SEVERITY: dict[str, SeverityLevel] = {
    "RED": SeverityLevel.RED,
    "WARNING": SeverityLevel.RED,
    "ORANGE": SeverityLevel.ORANGE,
    "WATCH": SeverityLevel.ORANGE,
    "YELLOW": SeverityLevel.YELLOW,
    "ADVISORY": SeverityLevel.YELLOW,
    "GREEN": SeverityLevel.GREEN,
    "NORMAL": SeverityLevel.GREEN,
}


def _first_present(props: dict[str, Any], *keys: str) -> Any:
    """Return the first non-empty value among ``keys`` in ``props``."""
    for key in keys:
        value = props.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_utc_timestamp(value: Any) -> Optional[int]:
    """Parse a UTC timestamp given as epoch seconds or as ``YYYY-MM-DD HH:MM:SS`` / ISO 8601."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


class USGSVolcanoCollector(BaseCollector[list[HazardAlert]]):
    """Collector of volcanoes under an elevated alert (USGS Volcano Hazards Program).

    Supports the aviation color codes (``GREEN``, ``YELLOW``, ``ORANGE``, ``RED``) and the
    volcano alert levels (``NORMAL``, ``ADVISORY``, ``WATCH``, ``WARNING``).
    """

    FEED_URL = "https://volcanoes.usgs.gov/vsc/api/volcanoApi/elevated"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="usgs_volcano_gvp",
            name="USGS Volcano Hazards Program (elevated alerts)",
            domain="disasters",
            ttl_seconds=600,  # update rhythm not verified (the endpoint documents none and sends no cache header)
            http_client=http_client,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> list[HazardAlert]:
        resp = await self._get(client, self.FEED_URL)
        resp.raise_for_status()
        return self.parse_volcanoes(resp.json())

    @staticmethod
    def parse_volcanoes(payload: Any) -> list[HazardAlert]:
        """Convert the USGS elevated-volcano list to ``HazardAlert`` objects.

        A volcano without a position, an alert level or a notice time is skipped. The main
        value is the VEI only when the feed gives one; it is never derived from the alert level.
        """
        items: list[dict[str, Any]] = []
        if isinstance(payload, list):
            items = [x for x in payload if isinstance(x, dict)]
        elif isinstance(payload, dict):
            raw_list = payload.get("volcanoes") or payload.get("features") or []
            items = [x for x in raw_list if isinstance(x, dict)]

        alerts: list[HazardAlert] = []
        for item in items:
            try:
                props = item.get("properties") if "properties" in item else item
                if not isinstance(props, dict):
                    continue

                lat_raw = _first_present(props, "lat", "latitude")
                lon_raw = _first_present(props, "long", "lon", "longitude")
                if lat_raw is None or lon_raw is None:
                    coords = (item.get("geometry") or {}).get("coordinates") or []
                    if len(coords) >= 2:
                        lon_raw, lat_raw = coords[0], coords[1]
                if lat_raw is None or lon_raw is None:
                    continue
                lat = float(lat_raw)
                lon = float(lon_raw)

                level = _first_present(props, "colorCode", "color_code", "alertLevel", "alert_level")
                if level is None:
                    continue
                color_code = str(level).strip().upper()
                severity = _AVIATION_COLOR_TO_SEVERITY.get(color_code)
                if severity is None:
                    continue

                ts_utc = _parse_utc_timestamp(_first_present(props, "sentUtc", "alertDate", "sent_utc"))
                if ts_utc is None:
                    continue

                vei = optional_float(props.get("vei"))
                given_name = _first_present(props, "vName", "v_name", "volcano_name", "name")
                name = str(given_name or "Unnamed volcano")
                v_id = str(_first_present(props, "vnum", "id") or name.lower().replace(" ", "_"))
                title = f"{name} volcano (aviation color code {color_code})"

                alerts.append(
                    HazardAlert(
                        id=f"volcano_{v_id}",
                        category=HazardCategory.VOLCANO,
                        severity=severity,
                        title=short_title(title),
                        source="USGS_VOLCANO",
                        sources=["USGS_VOLCANO"],
                        timestamp_utc=ts_utc,
                        lat=round(lat, 4),
                        lon=round(lon, 4),
                        value_primary=None if vei is None else round(vei, 1),
                        value_unit=None if vei is None else "VEI",
                        tsunami_warning=False,
                    ).with_place_name(str(given_name) if given_name else None)  # the volcano's own name, for the earthquakes near it
                )
            except (ValueError, TypeError, KeyError):
                continue

        return alerts
