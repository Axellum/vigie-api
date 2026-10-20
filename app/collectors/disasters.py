"""Major disaster collectors: GDACS (UN / EU) and NOAA NWS active alerts (CAP / GeoJSON).

Coverage:

- Major earthquakes (``EQ``), tsunamis (``TS``), volcanic eruptions (``VO``), cyclones /
  hurricanes / typhoons (``TC``), floods (``FL``) and wildfires (``WF``) through GDACS: the events
  still current at the orange or red alert level (green means a low humanitarian impact).
- Immediate Tornado, Hurricane, Tsunami and Flash Flood warnings through NOAA NWS.

An alert needs a position, a severity and an event time to be kept. The main value
(``value_primary``) is the one the source reports; when the source gives none it stays
``None``: NWS alerts carry no magnitude, so none is made up.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app import clock
from app.collectors.base import BaseCollector, optional_float
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


def _parse_iso_utc(value: Any) -> Optional[int]:
    """Parse an ISO 8601 date-time to UTC epoch seconds (naive values are taken as UTC)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


class GDACSCollector(BaseCollector[list[HazardAlert]]):
    """Asynchronous GDACS collector (Global Disaster Alert and Coordination System, UN / EU).

    One request per refresh to the documented search endpoint
    (https://www.gdacs.org/gdacsapi/swagger/index.html, ``/api/Events/geteventlist/search``) for every
    event type at once. The ``MAP`` endpoint used before now needs one request per event type: called
    without ``eventtype`` it answers HTTP 400 "Eventtype is required." (seen on 2026-10-20), so this
    collector received nothing. The search answers 204 with an empty body when nothing matches.
    """

    FEED_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
    EVENT_TYPES = "EQ;TC;FL;VO;WF"  # the types _GDACS_CATEGORY_MAP knows (GDACS has no separate TS type)
    # Orange and red only: GDACS green means a low humanitarian impact, and there are hundreds of green
    # events at any time (mostly small wildfires), more than one page of the search.
    ALERT_LEVELS = "Orange;Red"
    WINDOW_DAYS = 30  # an event still current (a long flood, a cyclone) can have started weeks ago
    PAGE_SIZE = 100  # the documented maximum; orange and red events of 30 days fit in one page

    @classmethod
    def search_params(cls, now_utc: float) -> dict[str, str]:
        """Query of the search endpoint for the events that started within ``WINDOW_DAYS`` of ``now_utc``."""
        start = datetime.fromtimestamp(now_utc - cls.WINDOW_DAYS * 86400, tz=timezone.utc).date()
        return {
            "eventlist": cls.EVENT_TYPES,
            "alertlevel": cls.ALERT_LEVELS,
            "fromDate": start.isoformat(),
            "pageSize": str(cls.PAGE_SIZE),
            "pageNumber": "1",
        }

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="gdacs_global",
            name="GDACS (UN / European Commission JRC)",
            domain="disasters",
            ttl_seconds=180,  # update rhythm not verified (https://data.gdacs.org/About/termofuse.aspx documents none)
            http_client=http_client,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> list[HazardAlert]:
        resp = await self._get(client, self.FEED_URL, params=self.search_params(clock.wall()))
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content.strip():
            return []  # GDACS answered: no event matches (not a failure)
        return self.parse_geojson(resp.json())

    @staticmethod
    def parse_geojson(payload: dict[str, Any]) -> list[HazardAlert]:
        """Parse the GDACS FeatureCollection into ``HazardAlert`` objects (current events only)."""
        alerts: list[HazardAlert] = []
        features = payload.get("features") or []

        for feat in features:
            try:
                props = feat.get("properties") or {}
                # "iscurrent" is the string "true" / "false" in the search answer: an event GDACS no
                # longer follows is not an active hazard. Absent: kept (older answers had no flag).
                if str(props.get("iscurrent", "true")).strip().lower() == "false":
                    continue
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

                severity = _GDACS_SEVERITY_MAP.get(str(props.get("alertlevel") or "").strip().lower())
                if severity is None:
                    continue

                sev_data = props.get("severitydata")
                sev_data = sev_data if isinstance(sev_data, dict) else {}
                val_raw = optional_float(sev_data.get("severity"))
                if val_raw is None:
                    val_raw = optional_float(props.get("severity"))
                value_primary = None if val_raw is None else round(val_raw, 1)
                value_unit = str(sev_data.get("severityunit") or default_unit)

                ts_utc = _parse_iso_utc(props.get("fromdate")) or _parse_iso_utc(props.get("todate"))
                if ts_utc is None:
                    continue

                event_id = str(props.get("eventid") or feat.get("id") or f"{ev_type}_{ts_utc}")
                name = str(props.get("name") or props.get("eventname") or props.get("country") or ev_type)
                country = str(props.get("country") or "").strip()
                title = f"{name} ({country})" if country and country not in name else name

                tsunami_flag = category == HazardCategory.TSUNAMI or (
                    category == HazardCategory.EARTHQUAKE
                    and severity == SeverityLevel.RED
                    and value_primary is not None
                    and value_primary >= 7.0
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
    """Collector of the most severe NOAA / National Weather Service alerts (tornado, hurricane, tsunami, flood)."""

    FEED_URL = (
        "https://api.weather.gov/alerts/active"
        "?status=actual&severity=Extreme,Severe"
    )

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="noaa_nws",
            name="NOAA National Weather Service (CAP Alerts)",
            domain="disasters",
            # The API sends Cache-Control: max-age=5 but documents no update rhythm
            # (https://www.weather.gov/documentation/services-web-api): kept at 120 s, not verified.
            ttl_seconds=120,
            http_client=http_client,
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> list[HazardAlert]:
        resp = await self._get(client, self.FEED_URL)
        resp.raise_for_status()
        payload = resp.json()
        return self.parse_alerts(payload)

    @staticmethod
    def _classify_event(event_name: str) -> Optional[tuple[HazardCategory, str, bool]]:
        """Map an NWS event name to (category, unit of the main value, tsunami flag)."""
        ev = event_name.lower()
        if "tsunami" in ev:
            return (HazardCategory.TSUNAMI, "m", True)
        if "tornado" in ev:
            return (HazardCategory.TORNADO, "km/h", False)
        if "hurricane" in ev or "typhoon" in ev or "tropical storm" in ev:
            return (HazardCategory.HURRICANE, "km/h", False)
        if "volcan" in ev or "ashfall" in ev:
            return (HazardCategory.VOLCANO, "VEI", False)
        if "flash flood" in ev or "flood warning" in ev:
            return (HazardCategory.FLOOD, "mm", False)
        if "red flag" in ev or "fire" in ev:
            return (HazardCategory.WILDFIRE, "MW", False)
        return None

    @staticmethod
    def _extract_centroid(geometry: Optional[dict[str, Any]]) -> Optional[tuple[float, float]]:
        """Extract a centroid (lat, lon) from a GeoJSON Point or Polygon geometry."""
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
            lons = [float(pt[0]) for pt in ring if len(pt) >= 2]
            lats = [float(pt[1]) for pt in ring if len(pt) >= 2]
            if lats and lons:
                return (sum(lats) / len(lats), sum(lons) / len(lons))
        return None

    @classmethod
    def parse_alerts(cls, payload: dict[str, Any]) -> list[HazardAlert]:
        """Parse NOAA NWS GeoJSON / CAP alerts. NWS alerts carry no physical value: it stays unknown."""
        alerts: list[HazardAlert] = []
        features = payload.get("features") or []

        for feat in features[:40]:
            try:
                props = feat.get("properties") or {}
                event_name = str(props.get("event") or "")
                classified = cls._classify_event(event_name)
                if classified is None:
                    continue

                category, unit, tsunami_flag = classified
                centroid = cls._extract_centroid(feat.get("geometry"))
                if centroid is None:
                    continue
                lat, lon = centroid

                sev_raw = str(props.get("severity") or "").lower()
                if sev_raw == "extreme":
                    severity = SeverityLevel.RED
                elif sev_raw == "severe":
                    severity = SeverityLevel.ORANGE
                else:
                    continue

                ts_utc = _parse_iso_utc(props.get("sent")) or _parse_iso_utc(props.get("effective"))
                if ts_utc is None:
                    continue

                area_desc = str(props.get("areaDesc") or "Unspecified area")
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
                        value_primary=None,
                        value_unit=unit,
                        tsunami_warning=tsunami_flag,
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue

        return alerts
