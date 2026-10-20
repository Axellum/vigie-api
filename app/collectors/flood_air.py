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

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector, optional_float
from app.collectors.grid import (
    DEFAULT_AIR_QUALITY_GRID_DEG,
    DEFAULT_FLOOD_GRID_DEG,
    coord_str,
    grid_step_from_env,
    snap_to_grid,
)
from app.collectors.weights import open_meteo_weight
from app.models.schemas import (
    AirQualityStatus,
    FloodRiskStatus,
    HazardAlert,
    HazardCategory,
    SeverityLevel,
)


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
        return await self.fetch(cache_key=cache_key, lat=lat_r, lon=lon_r)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> FloodRiskStatus:
        lat = float(kwargs["lat"])
        lon = float(kwargs["lon"])
        params = {
            "latitude": coord_str(lat),
            "longitude": coord_str(lon),
            "daily": "river_discharge,river_discharge_mean,river_discharge_max",
            "forecast_days": "7",
        }
        resp = await client.get(self.BASE_URL, params=params)
        resp.raise_for_status()
        return self.parse_response(resp.json())

    @staticmethod
    def parse_response(payload: dict[str, Any]) -> FloodRiskStatus:
        """Compute the discharge ratio and the heuristic flood level.

        The ratio is the maximum forecast discharge over the mean forecast discharge (both
        GloFAS ensemble statistics). The level is a heuristic, not an official warning.
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

        ratio: Optional[float] = None
        level: Optional[SeverityLevel] = None
        if mean_q is not None:
            ratio = round(max_q / max(mean_q, 1.0), 2)
            level = SeverityLevel.GREEN
            if max_q >= 50.0 and ratio >= 5.0:
                level = SeverityLevel.RED
            elif max_q >= 25.0 and ratio >= 3.0:
                level = SeverityLevel.ORANGE
            elif max_q >= 10.0 and ratio >= 2.0:
                level = SeverityLevel.YELLOW

        return FloodRiskStatus(
            river_discharge_m3s=max(0.0, curr_q),
            river_discharge_mean_m3s=None if mean_q is None else max(0.0, mean_q),
            river_discharge_max_m3s=max(0.0, max_q),
            anomaly_ratio=ratio,
            flood_alert_level=level,
        )


class OpenMeteoAirQualityCollector(BaseCollector[AirQualityStatus]):
    """Air quality, UV and dust / ash collector (Open-Meteo Air Quality API)."""

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
        self.grid_deg = (
            grid_deg
            if grid_deg is not None
            else grid_step_from_env("VIGIE_GRID_AIR_QUALITY_DEG", DEFAULT_AIR_QUALITY_GRID_DEG)
        )

    def call_weight(self, params: Mapping[str, str]) -> float:
        """Weight of one request under the Open-Meteo rule (6 current variables: 1.0)."""
        return open_meteo_weight(params, default_forecast_days=5)

    async def fetch_air_quality(self, lat: float, lon: float) -> AirQualityStatus:
        """Fetch the AQI, fine particles, UV and dust / ash near ``lat, lon`` (snapped to the air quality grid)."""
        lat_r, lon_r = snap_to_grid(lat, lon, self.grid_deg)
        cache_key = f"aqi:{coord_str(lat_r)},{coord_str(lon_r)}"
        return await self.fetch(cache_key=cache_key, lat=lat_r, lon=lon_r)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> AirQualityStatus:
        lat = float(kwargs["lat"])
        lon = float(kwargs["lon"])
        params = {
            "latitude": coord_str(lat),
            "longitude": coord_str(lon),
            "current": "european_aqi,us_aqi,pm10,pm2_5,uv_index,dust",
        }
        resp = await client.get(self.BASE_URL, params=params)
        resp.raise_for_status()
        return self.parse_response(resp.json())

    @staticmethod
    def parse_response(payload: dict[str, Any]) -> AirQualityStatus:
        """Normalize an Open-Meteo Air Quality response. Missing variables become ``None``.

        Raises ``ValueError`` when the response carries none of the air quality values.
        """
        curr = payload.get("current") or {}
        eu_raw = optional_float(curr.get("european_aqi"))
        us_raw = optional_float(curr.get("us_aqi"))
        pm2_5 = optional_float(curr.get("pm2_5"))
        pm10 = optional_float(curr.get("pm10"))
        uv = optional_float(curr.get("uv_index"))
        dust = optional_float(curr.get("dust"))
        if all(v is None for v in (eu_raw, us_raw, pm2_5, pm10, dust)):
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
        )


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
        resp = await client.get(self.FEED_URL)
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
                name = str(_first_present(props, "vName", "v_name", "volcano_name", "name") or "Unnamed volcano")
                v_id = str(_first_present(props, "vnum", "id") or name.lower().replace(" ", "_"))
                title = f"{name} volcano (aviation color code {color_code})"

                alerts.append(
                    HazardAlert(
                        id=f"volcano_{v_id}",
                        category=HazardCategory.VOLCANO,
                        severity=severity,
                        title=title[:96],
                        source="USGS_VOLCANO",
                        sources=["USGS_VOLCANO"],
                        timestamp_utc=ts_utc,
                        lat=round(lat, 4),
                        lon=round(lon, 4),
                        value_primary=None if vei is None else round(vei, 1),
                        value_unit=None if vei is None else "VEI",
                        tsunami_warning=False,
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue

        return alerts
