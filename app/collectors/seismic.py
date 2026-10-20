"""Real-time seismology collectors: USGS (GeoJSON) and EMSC / SeismicPortal (REST JSON).

The two sources complement each other:

- ``USGSCollector``: world reference for moment magnitudes, the PAGER alert level and the
  oceanic ``tsunami`` flag.
- ``EMSCCollector``: Euro-Mediterranean Seismological Centre (SeismicPortal), particularly
  fast in Europe, the Mediterranean, the Middle East and Asia.

Duplicates between providers are merged afterwards by ``deduplicate_hazards()`` in ``geodesy.py``.
An event without a magnitude or without an event time is skipped: nothing is dated "now" or
given a default depth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector, optional_float
from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel


def _severity_from_magnitude_and_pager(
    mag: float, pager_alert: Optional[str], tsunami_flag: bool
) -> SeverityLevel:
    """Compute the unified severity from the magnitude, the USGS PAGER level and the tsunami flag."""
    pager = (pager_alert or "").strip().lower()
    if pager == "red" or mag >= 7.0 or (tsunami_flag and mag >= 6.5):
        return SeverityLevel.RED
    if pager == "orange" or mag >= 6.0 or tsunami_flag:
        return SeverityLevel.ORANGE
    if pager == "yellow" or mag >= 5.0:
        return SeverityLevel.YELLOW
    return SeverityLevel.GREEN


class USGSCollector(BaseCollector[list[HazardAlert]]):
    """Asynchronous collector of world earthquakes from USGS (GeoJSON, M4.5+, last 24 hours)."""

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
        """Turn a USGS GeoJSON feed into normalized ``HazardAlert`` objects."""
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
                depth = optional_float(coords[2]) if len(coords) >= 3 else None
                depth_km = None if depth is None else round(depth, 1)

                mag_raw = props.get("mag")
                if mag_raw is None:
                    continue
                mag = round(float(mag_raw), 1)

                ts_ms = optional_float(props.get("time"))
                if ts_ms is None or ts_ms <= 0:
                    continue
                ts_utc = int(ts_ms / 1000.0)

                tsunami_flag = bool(int(props.get("tsunami") or 0) == 1)
                pager_alert = props.get("alert")
                severity = _severity_from_magnitude_and_pager(mag, pager_alert, tsunami_flag)

                place = str(props.get("place") or "Unknown location")
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
                        value_unit=str(props.get("magType") or "M"),
                        depth_km=depth_km,
                        tsunami_warning=tsunami_flag,
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue

        return alerts


class EMSCCollector(BaseCollector[list[HazardAlert]]):
    """Asynchronous collector of EMSC / SeismicPortal (Euro-Mediterranean Seismological Centre)."""

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
        """Turn an EMSC / SeismicPortal GeoJSON feed into ``HazardAlert`` objects."""
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
                raw_depth = optional_float(coords[2]) if len(coords) >= 3 else None
                if raw_depth is None:
                    raw_depth = optional_float(props.get("depth"))
                depth_km = None if raw_depth is None else round(abs(raw_depth), 1)

                mag_raw = props.get("mag")
                if mag_raw is None:
                    continue
                mag = round(float(mag_raw), 1)

                time_val = props.get("time")
                if isinstance(time_val, (int, float)) and not isinstance(time_val, bool):
                    ts_utc = int(time_val)
                elif isinstance(time_val, str) and time_val:
                    parsed = datetime.fromisoformat(time_val.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    ts_utc = int(parsed.timestamp())
                else:
                    continue

                region = str(props.get("flynn_region") or props.get("place") or "Unknown region")
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
                        value_unit=str(props.get("magtype") or "M"),
                        depth_km=depth_km,
                        tsunami_warning=False,
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue

        return alerts
