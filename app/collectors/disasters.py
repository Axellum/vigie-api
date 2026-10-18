"""Collecteurs de catastrophes majeures : GDACS (ONU/UE) et NOAA NWS Active Alerts (CAP/GeoJSON).

Couvre :
- Séismes majeurs (`EQ`), Tsunamis (`TS`), Éruptions volcaniques (`VO`),
  Cyclones / Ouragans / Typhons (`TC`), Inondations (`FL`), Feux de forêts (`WF`) via GDACS.
- Alertes immédiates de Tornades (`Tornado Warning`), Ouragans (`Hurricane Warning`),
  Tsunamis (`Tsunami Warning`) et Crues éclair (`Flash Flood Warning`) via NOAA NWS.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector
from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel


_GDACS_CATEGORY_MAP: dict[str, tuple[HazardCategory, str]] = {
    "EQ": (HazardCategory.EARTHQUAKE, "Mw"),
    "TS": (HazardCategory.TSUNAMI, "m"),
    "VO": (HazardCategory.VOLCANO, "VEI"),
    "TC": (HazardCategory.HURRICANE, "km/h"),
    "FL": (HazardCategory.FLOOD, "m"),
    "WF": (HazardCategory.WILDFIRE, "ha"),
}

_GDACS_SEVERITY_MAP: dict[str, SeverityLevel] = {
    "red": SeverityLevel.RED,
    "orange": SeverityLevel.ORANGE,
    "yellow": SeverityLevel.YELLOW,
    "green": SeverityLevel.GREEN,
}


class GDACSCollector(BaseCollector[list[HazardAlert]]):
    """Collecteur asynchrone GDACS (Global Disaster Alert and Coordination System — ONU / UE)."""

    FEED_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/MAP"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="gdacs_global",
            name="GDACS (UN / European Commission JRC)",
            domain="disasters",
            ttl_seconds=180,
            http_client=http_client,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> list[HazardAlert]:
        resp = await client.get(self.FEED_URL)
        resp.raise_for_status()
        payload = resp.json()
        return self.parse_geojson(payload)

    @staticmethod
    def parse_geojson(payload: dict[str, Any]) -> list[HazardAlert]:
        """Parse la FeatureCollection GDACS en liste de `HazardAlert`."""
        alerts: list[HazardAlert] = []
        features = payload.get("features") or []

        for feat in features:
            try:
                props = feat.get("properties") or {}
                geom = feat.get("geometry") or {}
                coords = geom.get("coordinates") or []
                if not coords or not isinstance(coords[0], (int, float)):
                    continue

                lon = float(coords[0])
                lat = float(coords[1])

                ev_type = str(props.get("eventtype") or "").upper()
                if ev_type not in _GDACS_CATEGORY_MAP:
                    continue
                category, default_unit = _GDACS_CATEGORY_MAP[ev_type]

                alert_level_str = str(props.get("alertlevel") or "Green").strip().lower()
                severity = _GDACS_SEVERITY_MAP.get(alert_level_str, SeverityLevel.GREEN)

                sev_data = props.get("severitydata") or {}
                val_raw = sev_data.get("severity") if isinstance(sev_data, dict) else None
                if val_raw is None:
                    val_raw = props.get("severity") or 1.0
                value_primary = round(float(val_raw), 1)
                value_unit = str(
                    (sev_data.get("severityunit") if isinstance(sev_data, dict) else None)
                    or default_unit
                )

                fromdate = props.get("fromdate") or props.get("todate")
                if isinstance(fromdate, str) and fromdate:
                    iso_clean = fromdate.replace("Z", "+00:00")
                    try:
                        ts_utc = int(datetime.fromisoformat(iso_clean).timestamp())
                    except ValueError:
                        ts_utc = int(datetime.now(timezone.utc).timestamp())
                else:
                    ts_utc = int(datetime.now(timezone.utc).timestamp())

                event_id = str(props.get("eventid") or feat.get("id") or f"{ev_type}_{ts_utc}")
                name = str(props.get("name") or props.get("eventname") or props.get("country") or ev_type)
                country = str(props.get("country") or "").strip()
                title = f"{name} ({country})" if country and country not in name else name

                tsunami_flag = category == HazardCategory.TSUNAMI or (
                    category == HazardCategory.EARTHQUAKE and severity == SeverityLevel.RED and value_primary >= 7.0
                )

                alerts.append(
                    HazardAlert(
                        id=f"gdacs_{ev_type.lower()}_{event_id}",
                        category=category,
                        severity=severity,
                        title=title[:96],
                        source="GDACS",
                        sources=["GDACS"],
                        timestamp_utc=ts_utc,
                        lat=round(lat, 4),
                        lon=round(lon, 4),
                        value_primary=value_primary,
                        value_unit=value_unit,
                        tsunami_warning=tsunami_flag,
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue

        return alerts


class NOAANWSCollector(BaseCollector[list[HazardAlert]]):
    """Collecteur des alertes critiques NOAA / National Weather Service (Tornades, Ouragans, Tsunamis, Crues)."""

    FEED_URL = (
        "https://api.weather.gov/alerts/active"
        "?status=actual&severity=Extreme,Severe"
    )

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="noaa_nws",
            name="NOAA National Weather Service (CAP Alerts)",
            domain="disasters",
            ttl_seconds=120,
            http_client=http_client,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> list[HazardAlert]:
        resp = await client.get(self.FEED_URL)
        resp.raise_for_status()
        payload = resp.json()
        return self.parse_alerts(payload)

    @staticmethod
    def _classify_event(event_name: str) -> Optional[ tuple[HazardCategory, float, str, bool] ]:
        ev = event_name.lower()
        if "tsunami" in ev:
            return (HazardCategory.TSUNAMI, 3.0, "m", True)
        if "tornado" in ev:
            return (HazardCategory.TORNADO, 220.0, "km/h", False)
        if "hurricane" in ev or "typhoon" in ev or "tropical storm" in ev:
            return (HazardCategory.HURRICANE, 185.0, "km/h", False)
        if "volcan" in ev or "ashfall" in ev:
            return (HazardCategory.VOLCANO, 3.0, "VEI", False)
        if "flash flood" in ev or "flood warning" in ev:
            return (HazardCategory.FLOOD, 100.0, "mm", False)
        if "red flag" in ev or "fire" in ev:
            return (HazardCategory.WILDFIRE, 500.0, "MW", False)
        return None

    @staticmethod
    def _extract_centroid(geometry: Optional[dict[str, Any]]) -> Optional[tuple[float, float]]:
        """Extrait un centroïde (lat, lon) à partir d'une géométrie Point ou Polygon GeoJSON."""
        if not geometry or not isinstance(geometry, dict):
            return None
        gtype = geometry.get("type")
        coords = geometry.get("coordinates")
        if not coords:
            return None
        if gtype == "Point" and len(coords) >= 2:
            return (float(coords[1]), float(coords[0]))
        if gtype == "Polygon" and coords and coords[0]:
            ring = coords[0]
            lons = [ float(pt[0]) for pt in ring if len(pt) >= 2 ]
            lats = [ float(pt[1]) for pt in ring if len(pt) >= 2 ]
            if lats and lons:
                return (sum(lats) / len(lats), sum(lons) / len(lons))
        return None

    @classmethod
    def parse_alerts(cls, payload: dict[str, Any]) -> list[HazardAlert]:
        """Parse les alertes GeoJSON/CAP de NOAA NWS."""
        alerts: list[HazardAlert] = []
        features = payload.get("features") or []

        for feat in features[:40]:
            try:
                props = feat.get("properties") or {}
                event_name = str(props.get("event") or "")
                classified = cls._classify_event(event_name)
                if classified is None:
                    continue

                category, default_val, unit, tsunami_flag = classified
                centroid = cls._extract_centroid(feat.get("geometry"))
                if centroid is None:
                    continue
                lat, lon = centroid

                sev_raw = str(props.get("severity") or "Severe").lower()
                severity = SeverityLevel.RED if sev_raw == "extreme" else SeverityLevel.ORANGE

                sent_str = props.get("sent") or props.get("effective")
                if isinstance(sent_str, str) and sent_str:
                    ts_utc = int(datetime.fromisoformat(sent_str.replace("Z", "+00:00")).timestamp())
                else:
                    ts_utc = int(datetime.now(timezone.utc).timestamp())

                area_desc = str(props.get("areaDesc") or "Zone NOAA")
                short_area = area_desc.split(";")[0].strip()
                headline = f"{event_name} - {short_area}"
                alert_id = str(props.get("id") or feat.get("id") or f"noaa_{ts_utc}")

                alerts.append(
                    HazardAlert(
                        id=f"noaa_{alert_id[-20:]}",
                        category=category,
                        severity=severity,
                        title=headline[:96],
                        source="NOAA_NWS",
                        sources=["NOAA_NWS"],
                        timestamp_utc=ts_utc,
                        lat=round(lat, 4),
                        lon=round(lon, 4),
                        value_primary=default_val,
                        value_unit=unit,
                        tsunami_warning=tsunami_flag,
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue

        return alerts
