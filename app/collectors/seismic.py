"""Collecteurs sismologiques temps réel : USGS (GeoJSON) et EMSC / SeismicPortal (REST JSON).

Les deux sources sont complémentaires :
- `USGSCollector` : Référence mondiale pour les magnitudes Mw, le niveau d'alerte PAGER
  et le flag `tsunami` océanique.
- `EMSCCollector` : Centre Sismologique Euro-Méditerranéen (SeismicPortal), particulièrement
  rapide en Europe, Méditerranée, Moyen-Orient et Asie.
Les doublons inter-providers sont ensuite fusionnés par `deduplicate_hazards()` dans `geodesy.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector
from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel


def _severity_from_magnitude_and_pager(
    mag: float, pager_alert: Optional[str], tsunami_flag: bool
) -> SeverityLevel:
    """Calcule la sévérité unifiée à partir de la magnitude, du niveau PAGER USGS et du flag tsunami."""
    pager = (pager_alert or "").strip().lower()
    if pager == "red" or mag >= 7.0 or (tsunami_flag and mag >= 6.5):
        return SeverityLevel.RED
    if pager == "orange" or mag >= 6.0 or tsunami_flag:
        return SeverityLevel.ORANGE
    if pager == "yellow" or mag >= 5.0:
        return SeverityLevel.YELLOW
    return SeverityLevel.GREEN


class USGSCollector(BaseCollector[list[HazardAlert]]):
    """Collecteur asynchrone des séismes mondiaux USGS (GeoJSON M4.5+ 24h)."""

    FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="usgs_eq",
            name="USGS Earthquake Hazards Program",
            domain="seismic",
            ttl_seconds=60,
            http_client=http_client,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> list[HazardAlert]:
        resp = await client.get(self.FEED_URL)
        resp.raise_for_status()
        payload = resp.json()
        return self.parse_geojson(payload)

    @staticmethod
    def parse_geojson(payload: dict[str, Any]) -> list[HazardAlert]:
        """Transforme un flux GeoJSON USGS en liste de `HazardAlert` normalisées."""
        alerts: list[HazardAlert] = []
        features = payload.get("features") or []

        for feat in features:
            try:
                props = feat.get("properties") or {}
                geom = feat.get("geometry") or {}
                coords = geom.get("coordinates") or []
                if len(coords) < 2:
                    continue

                lon = float(coords[0])
                lat = float(coords[1])
                depth_km = round(float(coords[2]), 1) if len(coords) >= 3 and coords[2] is not None else 10.0

                mag_raw = props.get("mag")
                if mag_raw is None:
                    continue
                mag = round(float(mag_raw), 1)

                ts_ms = props.get("time") or 0
                ts_utc = int(float(ts_ms) / 1000.0) if ts_ms else int(datetime.now(timezone.utc).timestamp())

                tsunami_flag = bool(int(props.get("tsunami") or 0) == 1)
                pager_alert = props.get("alert")
                severity = _severity_from_magnitude_and_pager(mag, pager_alert, tsunami_flag)

                place = str(props.get("place") or "Épicentre mondial")
                feat_id = str(feat.get("id") or f"{lat:.2f}_{lon:.2f}_{ts_utc}")

                alerts.append(
                    HazardAlert(
                        id=f"usgs_{feat_id}",
                        category=HazardCategory.EARTHQUAKE,
                        severity=severity,
                        title=f"M {mag:.1f} - {place}",
                        source="USGS",
                        sources=["USGS"],
                        timestamp_utc=ts_utc,
                        lat=round(lat, 4),
                        lon=round(lon, 4),
                        value_primary=mag,
                        value_unit="Mw",
                        depth_km=depth_km,
                        tsunami_warning=tsunami_flag,
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue

        return alerts


class EMSCCollector(BaseCollector[list[HazardAlert]]):
    """Collecteur asynchrone EMSC / SeismicPortal (Centre Sismologique Euro-Méditerranéen)."""

    FEED_URL = (
        "https://www.seismicportal.eu/fdsnws/event/1/query"
        "?limit=40&minmag=4.5&format=json"
    )

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="emsc_seismic",
            name="EMSC / SeismicPortal (Euro-Med & Global)",
            domain="seismic",
            ttl_seconds=60,
            http_client=http_client,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> list[HazardAlert]:
        resp = await client.get(self.FEED_URL)
        resp.raise_for_status()
        payload = resp.json()
        return self.parse_geojson(payload)

    @staticmethod
    def parse_geojson(payload: dict[str, Any]) -> list[HazardAlert]:
        """Transforme un flux GeoJSON EMSC / SeismicPortal en liste de `HazardAlert`."""
        alerts: list[HazardAlert] = []
        features = payload.get("features") or []

        for feat in features:
            try:
                props = feat.get("properties") or {}
                geom = feat.get("geometry") or {}
                coords = geom.get("coordinates") or []
                if len(coords) < 2:
                    continue

                lon = float(coords[0])
                lat = float(coords[1])
                raw_depth = coords[2] if len(coords) >= 3 and coords[2] is not None else props.get("depth", 10.0)
                depth_km = round(abs(float(raw_depth)), 1)

                mag_raw = props.get("mag")
                if mag_raw is None:
                    continue
                mag = round(float(mag_raw), 1)

                time_str = props.get("time")
                if isinstance(time_str, (int, float)):
                    ts_utc = int(time_str)
                elif isinstance(time_str, str) and time_str:
                    iso_clean = time_str.replace("Z", "+00:00")
                    ts_utc = int(datetime.fromisoformat(iso_clean).timestamp())
                else:
                    ts_utc = int(datetime.now(timezone.utc).timestamp())

                region = str(props.get("flynn_region") or props.get("place") or "Zone Euro-Med / Monde")
                unid = str(props.get("unid") or feat.get("id") or f"{lat:.2f}_{lon:.2f}_{ts_utc}")
                severity = _severity_from_magnitude_and_pager(mag, None, False)

                alerts.append(
                    HazardAlert(
                        id=f"emsc_{unid}",
                        category=HazardCategory.EARTHQUAKE,
                        severity=severity,
                        title=f"M {mag:.1f} - {region}",
                        source="EMSC",
                        sources=["EMSC"],
                        timestamp_utc=ts_utc,
                        lat=round(lat, 4),
                        lon=round(lon, 4),
                        value_primary=mag,
                        value_unit=str(props.get("magtype") or "Mw"),
                        depth_km=depth_km,
                        tsunami_warning=False,
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue

        return alerts
