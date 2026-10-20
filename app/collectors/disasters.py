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

import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app import clock
from app.attribution import credit_text
from app.collectors.base import BaseCollector, Fetched, optional_float, parse_iso_utc
from app.collectors.nhc import cyclone_key
from app.collectors.tsunami import vtec_event_key
from app.geo import geojson_to_shape
from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel
from app.models.vigilance import (
    Vigilance,
    VigilanceArea,
    VigilanceGeocode,
    VigilanceParameter,
    VigilancePhenomenon,
    VigilanceStatus,
    VigilanceText,
)

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


_parse_iso_utc = parse_iso_utc


def _gdacs_cyclone_key(props: dict[str, Any]) -> Optional[str]:
    """The key of a GDACS cyclone: its name and the year of its season, from the event name (``ISAIAS-26``: Isaias, 2026).

    The National Hurricane Center's storms carry the same key (``app/collectors/nhc.py``), so a storm that both report is one hazard.
    GDACS writes the event name of a storm of any basin this way (``CHOI-WAN-26``, ``TWENTYEIGHT-26``); a name that does not end with
    a two-digit year gives no key.
    """
    found = re.fullmatch(r"(.+)-(\d{2})", str(props.get("eventname") or "").strip())
    return None if found is None else cyclone_key(found.group(1), 2000 + int(found.group(2)))


def _clean(value: Any) -> Optional[str]:
    """A provider text as it is, ``None`` when it is missing or blank (the NWS writes the string "None" for no note)."""
    if not isinstance(value, str):
        return None
    return value if value.strip() and value.strip() != "None" else None


MAX_NWS_FEATURES = 2000  # the answer lists the active alerts; a hundred is a lot, two thousand is a mistake


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

                hazard = HazardAlert(
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
                alerts.append(hazard.with_event_key(_gdacs_cyclone_key(props) if ev_type == "TC" else None))
            except (ValueError, TypeError, KeyError):
                continue

        return alerts


# NWS CAP severity -> our level (CAP profile of MeteoAlarm, section 3.4: Minor is the lowest level, "green").
# `Unknown` is not in the table: the level is then unknown.
NWS_LEVELS: dict[str, SeverityLevel] = {
    "minor": SeverityLevel.GREEN,
    "moderate": SeverityLevel.YELLOW,
    "severe": SeverityLevel.ORANGE,
    "extreme": SeverityLevel.RED,
}

# NWS event name -> our phenomenon: the first rule whose words are all in the lower-case event wins, so the order
# matters (a "flash flood" is a flood, a "coastal flood" is coastal). An event no rule covers is "other".
NWS_PHENOMENA: tuple[tuple[tuple[str, ...], VigilancePhenomenon], ...] = (
    (("tornado",), VigilancePhenomenon.WIND),
    (("hurricane",), VigilancePhenomenon.WIND),
    (("typhoon",), VigilancePhenomenon.WIND),
    (("tropical storm",), VigilancePhenomenon.WIND),
    (("storm surge",), VigilancePhenomenon.COASTAL),
    (("coastal flood",), VigilancePhenomenon.COASTAL),
    (("lakeshore flood",), VigilancePhenomenon.COASTAL),
    (("tsunami",), VigilancePhenomenon.COASTAL),
    (("high surf",), VigilancePhenomenon.COASTAL),
    (("rip current",), VigilancePhenomenon.COASTAL),
    (("beach hazards",), VigilancePhenomenon.COASTAL),
    (("flood",), VigilancePhenomenon.FLOOD),
    (("thunderstorm",), VigilancePhenomenon.THUNDERSTORM),
    (("blizzard",), VigilancePhenomenon.SNOW_ICE),
    (("winter storm",), VigilancePhenomenon.SNOW_ICE),
    (("winter weather",), VigilancePhenomenon.SNOW_ICE),
    (("ice storm",), VigilancePhenomenon.SNOW_ICE),
    (("snow",), VigilancePhenomenon.SNOW_ICE),
    (("freezing rain",), VigilancePhenomenon.SNOW_ICE),
    (("sleet",), VigilancePhenomenon.SNOW_ICE),
    (("freezing fog",), VigilancePhenomenon.FOG),
    (("fog",), VigilancePhenomenon.FOG),
    (("wind chill",), VigilancePhenomenon.COLD),
    (("extreme cold",), VigilancePhenomenon.COLD),
    (("freeze",), VigilancePhenomenon.COLD),
    (("frost",), VigilancePhenomenon.COLD),
    (("heat",), VigilancePhenomenon.HEAT),
    (("dust",), VigilancePhenomenon.OTHER),  # before the "storm warning" of the sea forecasts
    (("high wind",), VigilancePhenomenon.WIND),
    (("extreme wind",), VigilancePhenomenon.WIND),
    (("wind",), VigilancePhenomenon.WIND),
    (("gale",), VigilancePhenomenon.WIND),
    (("storm warning",), VigilancePhenomenon.WIND),
    (("red flag",), VigilancePhenomenon.FIRE),
    (("fire weather",), VigilancePhenomenon.FIRE),
    (("fire",), VigilancePhenomenon.FIRE),
    (("avalanche",), VigilancePhenomenon.AVALANCHE),
    (("rain",), VigilancePhenomenon.RAIN),
)

NWS_STATUSES: dict[str, VigilanceStatus] = {
    "alert": VigilanceStatus.NEW,
    "update": VigilanceStatus.UPDATE,
    "cancel": VigilanceStatus.CANCEL,
}


def derive_nws_phenomenon(event: Optional[str]) -> Optional[VigilancePhenomenon]:
    """Our phenomenon for an NWS event name; ``None`` when there is no event, "other" when no rule covers it."""
    if not event or not event.strip():
        return None
    lowered = event.lower()
    for words, phenomenon in NWS_PHENOMENA:
        if all(word in lowered for word in words):
            return phenomenon
    return VigilancePhenomenon.OTHER


class NOAANWSCollector(BaseCollector[list[HazardAlert]]):
    """Collector of the most severe NOAA / National Weather Service alerts (tornado, hurricane, tsunami, flood).

    The same request also feeds the vigilances (``GET /v1/vigilances``): every alert of the answer, whatever its
    event, is read once as a ``Vigilance`` (``last_vigilances``) at the moment the answer is parsed. Asking for the
    vigilances (``fetch_vigilances``) goes through the same cache entry, so it never sends a second request.
    """

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
        # The alerts of the last answer read, as vigilances (see ``fetch_vigilances``).
        self.last_vigilances: list[Vigilance] = []

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> list[HazardAlert]:
        resp = await self._get(client, self.FEED_URL)
        resp.raise_for_status()
        payload = resp.json()
        hazards = self.parse_alerts(payload)
        # The vigilances come from the same answer and are replaced only when an answer was read here, never by a
        # cached one: the hazards and the vigilances always describe the same moment.
        self.last_vigilances = self.parse_vigilances(payload)
        return hazards

    async def fetch_vigilances(self) -> Fetched[list[Vigilance]]:
        """The vigilances of the cached answer: one request at most per cache lifetime, shared with the hazards."""
        fetched = await self.fetch_ex()
        return Fetched(list(self.last_vigilances), stale=fetched.stale)

    @classmethod
    def parse_vigilances(cls, payload: dict[str, Any]) -> list[Vigilance]:
        """Every alert of the answer as a ``Vigilance``: the CAP fields as given, the polygon when there is one.

        Most alerts have no polygon (they name zones): their area keeps the UGC and SAME codes as given.
        An alert with no identifier is skipped; an unknown value stays ``None``.
        """
        vigilances: list[Vigilance] = []
        for feat in (payload.get("features") or [])[:MAX_NWS_FEATURES]:
            try:
                parsed = cls._parse_vigilance(feat)
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            if parsed is not None:
                vigilances.append(parsed)
        return vigilances

    @staticmethod
    def _parse_vigilance(feat: dict[str, Any]) -> Optional[Vigilance]:
        props = feat.get("properties") or {}
        identifier = props.get("id") or feat.get("id")
        if not isinstance(identifier, str) or not identifier or str(props.get("status") or "").lower() != "actual":
            return None
        severity = str(props.get("severity") or "").strip()
        event = _clean(props.get("event"))
        message_type = _clean(props.get("messageType"))
        geocode = props.get("geocode") if isinstance(props.get("geocode"), dict) else {}
        geocodes = [
            VigilanceGeocode(scheme=scheme, value=str(value))
            for scheme in ("UGC", "SAME")
            for value in (geocode.get(scheme) or [])
            if value
        ]
        parameters = [
            VigilanceParameter(name=str(name), value=str(value))
            for name, values in (props.get("parameters") or {}).items()
            for value in (values if isinstance(values, list) else [values])
            if value is not None
        ]
        shape = geojson_to_shape(feat.get("geometry"))
        sender = _clean(props.get("senderName"))
        credit = credit_text("noaa_nws")
        vigilance = Vigilance(
            id=f"noaa_nws:{identifier}",
            source="noaa_nws",
            country="US",
            attribution=f"{credit} | Issued by {sender}" if sender else credit,
            identifier=identifier,
            sent_utc=parse_iso_utc(props.get("sent")),
            effective_utc=parse_iso_utc(props.get("effective")),
            onset_utc=parse_iso_utc(props.get("onset")),
            expires_utc=parse_iso_utc(props.get("expires")),
            ends_utc=parse_iso_utc(props.get("ends")),
            message_type=message_type,
            status=NWS_STATUSES.get(message_type.lower()) if message_type else None,
            level=NWS_LEVELS.get(severity.lower()),
            level_original=severity or None,
            phenomenon=derive_nws_phenomenon(event),
            phenomenon_original=event,
            event=event,
            issuer=sender,
            texts=[
                VigilanceText(
                    language=_clean(props.get("language")),
                    sender_name=sender,
                    event=event,
                    headline=_clean(props.get("headline")),
                    description=_clean(props.get("description")),
                    instruction=_clean(props.get("instruction")),
                    web=_clean(props.get("web")),
                    parameters=parameters,
                )
            ],
            area=VigilanceArea(
                description=_clean(props.get("areaDesc")),
                geocodes=geocodes,
                has_polygon=shape is not None,
            ),
            link=_clean(props.get("@id")) or (feat.get("id") if isinstance(feat.get("id"), str) else None),
        )
        return vigilance.with_shape(shape)

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

                hazard = HazardAlert(
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
                # A tsunami warning of the NWS carries the VTEC code of the message of the warning centre it relays: the key of the same event.
                vtec = (props.get("parameters") or {}).get("VTEC") if isinstance(props.get("parameters"), dict) else None
                key = next((k for k in (vtec_event_key(v) for v in (vtec if isinstance(vtec, list) else [])) if k), None)
                alerts.append(hazard.with_event_key(key if category == HazardCategory.TSUNAMI else None))
            except (ValueError, TypeError, KeyError):
                continue

        return alerts
