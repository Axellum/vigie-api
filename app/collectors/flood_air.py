"""Collecteurs mondiaux complémentaires : Crues GloFAS, Qualité de l'Air/Cendres & Volcans USGS/Smithsonian.

1. `OpenMeteoFloodCollector` : Interroge `https://flood-api.open-meteo.com/v1/flood`
   (modèle hydrologique mondial GloFAS Copernicus) pour extraire `river_discharge`,
   `river_discharge_mean` et `river_discharge_max` et détecter les crues majeures.
2. `OpenMeteoAirQualityCollector` : Interroge `https://air-quality-api.open-meteo.com/v1/air-quality`
   pour extraire `european_aqi`, `us_aqi`, `pm2_5`, `pm10`, `uv_index` et `dust`
   (indispensable pour détecter les panaches de cendres volcaniques fines ou poussières sahariennes).
3. `USGSVolcanoCollector` : Interroge l'API Volcano Hazards Program de l'USGS / Smithsonian GVP
   avec normalisation des codes d'alerte aviation (`GREEN`, `YELLOW`, `ORANGE`, `RED`) et du VEI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector
from app.models.schemas import (
    AirQualityStatus,
    FloodRiskStatus,
    HazardAlert,
    HazardCategory,
    SeverityLevel,
)


class OpenMeteoFloodCollector(BaseCollector[FloodRiskStatus]):
    """Collecteur hydrologique GloFAS Copernicus via Open-Meteo Flood API."""

    BASE_URL = "https://flood-api.open-meteo.com/v1/flood"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="open_meteo_flood",
            name="Open-Meteo Flood API (Copernicus GloFAS)",
            domain="disasters",
            ttl_seconds=600,
            http_client=http_client,
        )

    async def fetch_flood_risk(self, lat: float, lon: float) -> FloodRiskStatus:
        """Récupère les débits fluviaux et le risque de crue aux coordonnées (`lat, lon`)."""
        lat_r = round(float(lat), 2)
        lon_r = round(float(lon), 2)
        cache_key = f"flood:{lat_r:.2f},{lon_r:.2f}"
        return await self.fetch(cache_key=cache_key, lat=lat_r, lon=lon_r)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> FloodRiskStatus:
        lat = float(kwargs["lat"])
        lon = float(kwargs["lon"])
        params = {
            "latitude": f"{lat:.2f}",
            "longitude": f"{lon:.2f}",
            "daily": "river_discharge,river_discharge_mean,river_discharge_max",
            "forecast_days": "7",
        }
        resp = await client.get(self.BASE_URL, params=params)
        resp.raise_for_status()
        return self.parse_response(resp.json())

    @staticmethod
    def parse_response(payload: dict[str, Any]) -> FloodRiskStatus:
        """Analyse les séries temporelles de débit fluvial et calcule le ratio d'anomalie de crue."""
        daily = payload.get("daily") or {}
        discharges = [float(x) for x in (daily.get("river_discharge") or [0.0]) if x is not None]
        means = [float(x) for x in (daily.get("river_discharge_mean") or [0.0]) if x is not None]
        maxes = [float(x) for x in (daily.get("river_discharge_max") or discharges or [0.0]) if x is not None]

        curr_q = round(discharges[0], 2) if discharges else 0.0
        mean_q = round(sum(means) / len(means), 2) if means else max(curr_q, 1.0)
        max_q = round(max(maxes), 2) if maxes else curr_q

        denom = max(mean_q, 1.0)
        ratio = round(max_q / denom, 2) if max_q > 0.0 else 1.0

        # Seuils hydrologiques d'alerte (combinaison ratio d'anomalie et débit absolu significatif)
        level = SeverityLevel.GREEN
        if max_q >= 50.0 and ratio >= 5.0:
            level = SeverityLevel.RED
        elif max_q >= 25.0 and ratio >= 3.0:
            level = SeverityLevel.ORANGE
        elif max_q >= 10.0 and ratio >= 2.0:
            level = SeverityLevel.YELLOW

        return FloodRiskStatus(
            river_discharge_m3s=curr_q,
            river_discharge_mean_m3s=mean_q,
            river_discharge_max_m3s=max_q,
            anomaly_ratio=ratio,
            flood_alert_level=level,
        )


class OpenMeteoAirQualityCollector(BaseCollector[AirQualityStatus]):
    """Collecteur de qualité de l'air, UV et poussières/cendres (Open-Meteo Air Quality API)."""

    BASE_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="open_meteo_aqi",
            name="Open-Meteo Air Quality, Dust & UV API",
            domain="weather",
            ttl_seconds=600,
            http_client=http_client,
        )

    async def fetch_air_quality(self, lat: float, lon: float) -> AirQualityStatus:
        """Récupère l'indice AQI, les particules fines, l'UV et les poussières/cendres en (`lat, lon`)."""
        lat_r = round(float(lat), 2)
        lon_r = round(float(lon), 2)
        cache_key = f"aqi:{lat_r:.2f},{lon_r:.2f}"
        return await self.fetch(cache_key=cache_key, lat=lat_r, lon=lon_r)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> AirQualityStatus:
        lat = float(kwargs["lat"])
        lon = float(kwargs["lon"])
        params = {
            "latitude": f"{lat:.2f}",
            "longitude": f"{lon:.2f}",
            "current": "european_aqi,us_aqi,pm10,pm2_5,uv_index,dust",
        }
        resp = await client.get(self.BASE_URL, params=params)
        resp.raise_for_status()
        return self.parse_response(resp.json())

    @staticmethod
    def parse_response(payload: dict[str, Any]) -> AirQualityStatus:
        """Normalise la réponse JSON de Open-Meteo Air Quality."""
        curr = payload.get("current") or {}
        eu_aqi = int(round(float(curr.get("european_aqi") or 20)))
        us_aqi = int(round(float(curr.get("us_aqi") or 25)))
        pm2_5 = round(float(curr.get("pm2_5") or 5.0), 1)
        pm10 = round(float(curr.get("pm10") or 12.0), 1)
        uv = round(float(curr.get("uv_index") or 3.0), 1)
        dust = round(float(curr.get("dust") or 0.0), 1)

        warning = bool(dust >= 100.0 or pm10 >= 120.0 or eu_aqi >= 100)
        return AirQualityStatus(
            european_aqi=max(0, eu_aqi),
            us_aqi=max(0, us_aqi),
            pm2_5_ugm3=max(0.0, pm2_5),
            pm10_ugm3=max(0.0, pm10),
            uv_index=max(0.0, uv),
            dust_ugm3=max(0.0, dust),
            ash_or_dust_warning=warning,
        )


_AVIATION_COLOR_TO_SEVERITY: dict[str, tuple[SeverityLevel, float]] = {
    "RED": (SeverityLevel.RED, 4.0),
    "WARNING": (SeverityLevel.RED, 4.0),
    "ORANGE": (SeverityLevel.ORANGE, 3.0),
    "WATCH": (SeverityLevel.ORANGE, 3.0),
    "YELLOW": (SeverityLevel.YELLOW, 2.0),
    "ADVISORY": (SeverityLevel.YELLOW, 2.0),
    "GREEN": (SeverityLevel.GREEN, 1.0),
    "NORMAL": (SeverityLevel.GREEN, 1.0),
}


class USGSVolcanoCollector(BaseCollector[list[HazardAlert]]):
    """Collecteur spécialisé des volcans actifs (USGS Volcano Hazards / Smithsonian GVP).

    Supporte les codes couleur d'aviation de l'OACI (`GREEN`, `YELLOW`, `ORANGE`, `RED`)
    et les niveaux d'alerte volcanique (`NORMAL`, `ADVISORY`, `WATCH`, `WARNING`).
    """

    FEED_URL = "https://volcanoes.usgs.gov/vsc/api/volcanoApi/elevated"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="usgs_volcano_gvp",
            name="USGS / Smithsonian GVP Volcano Aviation Alerts",
            domain="disasters",
            ttl_seconds=600,
            http_client=http_client,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> list[HazardAlert]:
        resp = await client.get(self.FEED_URL)
        resp.raise_for_status()
        return self.parse_volcanoes(resp.json())

    @staticmethod
    def parse_volcanoes(payload: Any) -> list[HazardAlert]:
        """Convertit la liste des volcans sous vigilance USGS/Smithsonian en `HazardAlert`."""
        items: list[dict[str, Any]] = []
        if isinstance(payload, list):
            items = [x for x in payload if isinstance(x, dict)]
        elif isinstance(payload, dict):
            raw_list = payload.get("volcanoes") or payload.get("features") or []
            items = [x for x in raw_list if isinstance(x, dict)]

        alerts: list[HazardAlert] = []
        now_utc = int(datetime.now(timezone.utc).timestamp())

        for item in items:
            try:
                props = item.get("properties") if "properties" in item else item
                if not isinstance(props, dict):
                    continue

                lat_raw = props.get("latitude", props.get("lat"))
                lon_raw = props.get("longitude", props.get("lon"))
                if lat_raw is None or lon_raw is None:
                    geom = item.get("geometry") or {}
                    coords = geom.get("coordinates") or []
                    if len(coords) >= 2:
                        lon_raw, lat_raw = coords[0], coords[1]
                if lat_raw is None or lon_raw is None:
                    continue

                lat = float(lat_raw)
                lon = float(lon_raw)

                color_code = str(
                    props.get("color_code")
                    or props.get("colorCode")
                    or props.get("alert_level")
                    or props.get("alertLevel")
                    or "YELLOW"
                ).strip().upper()

                severity, default_vei = _AVIATION_COLOR_TO_SEVERITY.get(
                    color_code, (SeverityLevel.YELLOW, 2.0)
                )
                vei_val = float(props.get("vei") or default_vei)

                v_name = str(props.get("v_name") or props.get("volcano_name") or props.get("name") or "Volcan actif")
                v_id = str(props.get("vnum") or props.get("id") or v_name.lower().replace(" ", "_"))
                title = f"Volcan {v_name} (Code Aviation {color_code})"

                alerts.append(
                    HazardAlert(
                        id=f"volcano_{v_id}",
                        category=HazardCategory.VOLCANO,
                        severity=severity,
                        title=title[:96],
                        source="USGS_VOLCANO",
                        sources=["USGS_VOLCANO"],
                        timestamp_utc=now_utc,
                        lat=round(lat, 4),
                        lon=round(lon, 4),
                        value_primary=round(vei_val, 1),
                        value_unit="VEI",
                        tsunami_warning=False,
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue

        return alerts
