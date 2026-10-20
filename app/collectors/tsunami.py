"""NOAA U.S. Tsunami Warning Centers (tsunami.gov): the National (NTWC, Palmer, Alaska) and the Pacific (PTWC, Honolulu) centre.

What is read, all without a key, all checked on 2026-10-20 (the bulletins of the Panama earthquake of 2026-10-09 are the real
messages the tests replay):

- ``/events/js/previous.js`` is the list of the latest bulletins of both centres, every product (the same list the site's
  "Previous messages" page draws). It is asked conditionally (``Last-Modified``, gzip): a refresh that finds nothing new is a
  ``304``. It is the starting point, not the per-centre file ``/events/xml/PHEBCAP.xml`` that the site also offers: that file
  holds **only the latest bulletin of all products**, so the information statement the centre issues for Guam a minute after a
  threat message would hide the threat. The list keeps every bulletin, with its address.
- for each event, the CAP document (OASIS CAP 1.2, profile CAP-TSU) of the **latest bulletin of each alert or threat product**
  (a bulletin replaces the previous one of its product), fetched once: it never changes.
- for a PTWC international threat message, its text bulletin, once, for the table of estimated arrival times (it is the only
  place where those come with coordinates).

Which products are read is decided by the centres' own product list (https://www.tsunami.gov/?page=product_list, effective
April 3, 2017). Warnings, watches and advisories (``WEPA41``, ``WEXX20``, ``WECA60``, ``WEHW40``, ``WEZS40``, ``WEGM40``; the
bulleted layouts ``WEAK51``, ``WEXX30`` and ``WECA40`` say the same thing and are not read when their segmented twin exists) and the
international threat messages (``WEPA40``, ``WECA41``) are read. **An information statement is not an alert** and is not read: it
says there is no threat, or that the hazard is not determined. Nor are the Spanish layouts (``WEAK61``...) and the tests (``NTXX98``).

An event is a hazard (category ``TSUNAMI``, at the epicentre) and its alert infos are vigilances (``source`` ``tsunami``). The words of the
centres are kept as they wrote them; the levels are ours (tables below, ``docs/data-sources.md``).
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app import clock
from app.attribution import credit_text
from app.collectors.base import (
    BaseCollector,
    CacheEntry,
    Collected,
    Fetched,
    http_date_to_epoch,
    optional_float,
    parse_iso_utc,
)
from app.models.schemas import SEVERITY_RANK, HazardAlert, HazardCategory, SeverityLevel
from app.models.vigilance import (
    Vigilance,
    VigilanceArea,
    VigilanceGeocode,
    VigilanceParameter,
    VigilancePhenomenon,
    VigilanceStatus,
    VigilanceText,
)

CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"
BASE_URL = "https://www.tsunami.gov"
LISTING_URL = f"{BASE_URL}/events/js/previous.js"

CENTRES = {"PHEB": "PTWC", "PAAQ": "NTWC"}  # the centre code in the addresses -> the name the hazard carries as its source

# The products the centres list. Warnings, watches and advisories: the segmented layouts (UGC and VTEC codes) where there is
# one, the others alone.
ALERT_PRODUCTS = ("WEPA41", "WEXX20", "WECA60", "WEHW40", "WEZS40", "WEGM40")
BULLETED_TWINS = {"WEAK51": "WEPA41", "WEXX30": "WEXX20", "WECA40": "WECA60"}  # the same message, in a bulleted layout
THREAT_PRODUCTS = ("WEPA40", "WECA41")  # the international threat messages of the PTWC
# The information statements (no threat, or a hazard not determined): listed, counted, never read.
INFORMATION_PRODUCTS = ("WEAK53", "WEXX32", "WEHW42", "WEZS42", "WEGM42", "WECA42", "WEPA42", "WECA43")

# A bulletin with no end of its own (the PTWC threat messages carry no ``expires``) is kept this long after its issue. The PTWC
# issues its threat messages "in one hour or sooner" (the text of a bulletin says so), and an event lasted 26 hours with 23 messages
# on 2025-07-29: three hours without a new message is three missed updates. This number is ours.
ACTIVE_FOR_S = 3 * 3600

# The level of an alert info. The words are the centres' own; the level is ours (documented in docs/data-sources.md).
# Domestic messages: the alert level the centre gives (``TsunamiSystemCategory``). The CAP severity is Extreme, Moderate and Moderate
# for Warning, Advisory and Watch (seen in the recorded messages), which would put the last two at one level.
DOMESTIC_LEVELS: dict[str, SeverityLevel] = {
    "warning": SeverityLevel.RED,
    "advisory": SeverityLevel.ORANGE,
    "watch": SeverityLevel.YELLOW,
}
# International threat messages: the CAP gives no severity (``Unknown``); the event text is the forecast wave height class. The words
# seen on 2026-10-20 in the centre's messages are in the table; any other word ("Tsunami Information", a class not seen yet) is no alert here.
THREAT_LEVELS: dict[str, SeverityLevel] = {
    "tsunami >3.0 meters": SeverityLevel.RED,
    "tsunami 1-3 meters": SeverityLevel.ORANGE,
    "tsunami potential threat": SeverityLevel.ORANGE,  # a threat is possible and no height is forecast yet
    "tsunami 0.3-1 meters": SeverityLevel.YELLOW,
}

MAX_LISTING_ENTRIES = 2_000
MAX_DOCUMENT_BYTES = 1_000_000
MAX_AREAS = 400
MAX_ETAS = 400
CONCURRENCY = 4

_PATH = re.compile(r"^/events/(PHEB|PAAQ)/\d{4}/\d{2}/\d{2}/[A-Za-z0-9]+/\d{1,3}$")
_WMO = re.compile(r"^[A-Z]{4}\d{2}$")
_ENTRY = re.compile(r"\{[^{}]*\}")
_FIELD = re.compile(r"'(\w+)'\s*:\s*(?:'((?:[^'\\]|\\.)*)'|(-?\d+(?:\.\d+)?))")


@dataclass(frozen=True)
class Bulletin:
    """One line of the list of latest bulletins."""

    twc: str  # PHEB or PAAQ
    event_id: str
    wmo_id: str  # the product (WEPA40...)
    number: int
    issued_utc: int
    path: str  # /events/PHEB/2026/10/09/26282000/4

    @property
    def incident(self) -> tuple[str, str]:
        return self.twc, self.event_id

    @property
    def cap_url(self) -> str:
        return f"{BASE_URL}{self.path}/{self.wmo_id}/{self.twc}CAP.xml"

    @property
    def text_url(self) -> str:
        return f"{BASE_URL}{self.path}/{self.wmo_id}/{self.wmo_id}.txt"


@dataclass(frozen=True)
class Listing:
    """The list of latest bulletins, and what in it was not read."""

    bulletins: tuple[Bulletin, ...]
    unreadable: int = 0


@dataclass(frozen=True)
class CapArea:
    description: Optional[str]
    circle: Optional[tuple[float, float]]  # (lat, lon) of the centre of the circle, radius 0 is a point
    geocodes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CapInfo:
    """One ``info`` of a CAP message, the words as given."""

    event: Optional[str]
    category: Optional[str]  # eventCode TsunamiSystemCategory (Warning, Advisory, Watch, Information, Cancellation)
    severity: Optional[str]
    urgency: Optional[str]
    certainty: Optional[str]
    response_type: Optional[str]
    onset_utc: Optional[int]
    expires_utc: Optional[int]
    sender_name: Optional[str]
    headline: Optional[str]
    description: Optional[str]
    instruction: Optional[str]
    web: Optional[str]
    parameters: tuple[tuple[str, str], ...]
    areas: tuple[CapArea, ...]
    event_codes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Eta:
    """The estimated arrival time of the first wave at a place."""

    place: str
    eta_utc: int
    region: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    source: str = "cap"  # where the time was read: the CAP parameter or the text bulletin


@dataclass(frozen=True)
class Message:
    """One CAP message (one bulletin of one product) with the arrival times read from it."""

    bulletin: Bulletin
    identifier: str
    sent_utc: Optional[int]
    status: Optional[str]
    message_type: Optional[str]
    source: Optional[str]
    infos: tuple[CapInfo, ...]
    etas: tuple[Eta, ...] = ()


@dataclass(frozen=True)
class Alert:
    """An info of a message that is an alert or a threat, with our level and the instant until which it counts."""

    message: Message
    index: int  # position of the info in the message
    info: CapInfo
    level: SeverityLevel
    level_original: str
    until_utc: int


@dataclass(frozen=True)
class Origin:
    """The earthquake behind the message, as the message gives it."""

    lat: float
    lon: float
    time_utc: Optional[int]
    magnitude: Optional[float]
    magnitude_type: Optional[str]
    depth_km: Optional[float]
    location: Optional[str]


@dataclass(frozen=True)
class TsunamiEvent:
    """An event with at least one alert in force: the hazard and its vigilances are made from it."""

    twc: str
    event_id: str
    origin: Origin
    alerts: tuple[Alert, ...]
    etas: tuple[Eta, ...]

    @property
    def hazard_id(self) -> str:
        return f"tsunami_{self.twc.lower()}_{self.event_id}"

    @property
    def level(self) -> SeverityLevel:
        return max((alert.level for alert in self.alerts), key=lambda level: SEVERITY_RANK[level])

    @property
    def strongest(self) -> Alert:
        return max(self.alerts, key=lambda alert: (SEVERITY_RANK[alert.level], alert.message.sent_utc or 0))


@dataclass(frozen=True)
class TsunamiState:
    """What the list says now: the events with an alert, and what was left out."""

    events: tuple[TsunamiEvent, ...] = ()
    information_statements: int = 0  # bulletins of information products in the list (no threat: not alerts)
    unreadable: int = 0  # entries of the list, or documents of an event, that could not be read
    updated_utc: Optional[int] = None


# ------------------------------------------------------------------------------------------------------ the list of bulletins


def parse_listing(text: str) -> Listing:
    """The bulletins of ``previous.js`` (a JavaScript object literal: ``var prevEvents = {'event' : [{...}, ...]}``)."""
    if "prevEvents" not in text or "'event'" not in text:
        raise ValueError("tsunami.gov list is not the previous.js file (no prevEvents)")
    body = text[text.index("'event'") :]
    bulletins: list[Bulletin] = []
    unreadable = 0
    entries = _ENTRY.findall(body)
    if len(entries) > MAX_LISTING_ENTRIES:
        raise ValueError("tsunami.gov list is longer than it ever was")
    for entry in entries:
        values: dict[str, str] = {}
        for found in _FIELD.finditer(entry):
            values[found.group(1)] = found.group(2).replace("\\'", "'") if found.group(2) is not None else found.group(3)
        bulletin = _bulletin(values)
        if bulletin is None:
            unreadable += 1
        else:
            bulletins.append(bulletin)
    if entries and not bulletins:
        raise ValueError("tsunami.gov list: none of its entries could be read")
    return Listing(bulletins=tuple(bulletins), unreadable=unreadable)


def _bulletin(values: dict[str, str]) -> Optional[Bulletin]:
    twc, wmo, path = values.get("TWCID"), values.get("WMOID"), values.get("URL")
    event_id = values.get("id")
    issued = parse_iso_utc((values.get("issueTime") or "").split(".")[0].replace(" ", "T"))
    try:
        number = int(float(values.get("bulletinNumber", "")))
    except ValueError:
        return None
    if twc not in CENTRES or not wmo or not _WMO.match(wmo) or not event_id or not re.fullmatch(r"[A-Za-z0-9]+", event_id):
        return None
    if issued is None or path is None or not _PATH.match(path) or f"/{event_id}/" not in f"{path}/" or not path.startswith(f"/events/{twc}/"):
        return None
    return Bulletin(twc=twc, event_id=event_id, wmo_id=wmo, number=number, issued_utc=issued, path=path)


def wanted_bulletins(listing: Listing, now_utc: float) -> tuple[list[Bulletin], int]:
    """The bulletins worth reading now, and how many bulletins of the list are information statements.

    The latest bulletin of each alert or threat product of each event, issued within ``ACTIVE_FOR_S``. A bulleted layout is left
    out when the segmented product of the same event is in the list (the same message twice).
    """
    latest: dict[tuple[str, str, str], Bulletin] = {}
    present: set[tuple[str, str, str]] = set()
    information = 0
    for bulletin in listing.bulletins:
        if bulletin.issued_utc > now_utc:
            continue  # a bulletin from the future is a clock that is wrong somewhere: not trusted
        key = (bulletin.twc, bulletin.event_id, bulletin.wmo_id)
        present.add(key)
        if bulletin.wmo_id in ALERT_PRODUCTS or bulletin.wmo_id in THREAT_PRODUCTS or bulletin.wmo_id in BULLETED_TWINS:
            if key not in latest or bulletin.number > latest[key].number:
                latest[key] = bulletin
        elif bulletin.wmo_id in INFORMATION_PRODUCTS:
            information += 1
    chosen = []
    for (twc, event_id, wmo), bulletin in latest.items():
        twin = BULLETED_TWINS.get(wmo)
        if twin is not None and (twc, event_id, twin) in present:
            continue
        if now_utc - bulletin.issued_utc <= ACTIVE_FOR_S:
            chosen.append(bulletin)
    chosen.sort(key=lambda b: (b.twc, b.event_id, b.wmo_id))
    return chosen, information


# ------------------------------------------------------------------------------------------------------ a CAP message


def _text(element: ET.Element, tag: str) -> Optional[str]:
    value = element.findtext(CAP + tag)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _named(parent: ET.Element, tag: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in parent.findall(CAP + tag):
        name, value = _text(item, "valueName"), item.findtext(CAP + "value")
        if name and value is not None and value.strip():
            pairs.append((name.strip(), value.strip()))
    return pairs


def _circle(text: Optional[str]) -> Optional[tuple[float, float]]:
    """``lat,lon radius`` -> ``(lat, lon)``; ``None`` for anything else (the radius is 0 in every message seen)."""
    found = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\s+\d+(?:\.\d+)?\s*", text or "")
    if found is None:
        return None
    lat, lon = float(found.group(1)), float(found.group(2))
    return (lat, lon) if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 else None


def _info(info: ET.Element) -> CapInfo:
    codes = _named(info, "eventCode")
    areas = tuple(
        CapArea(
            description=_text(area, "areaDesc"),
            circle=_circle(_text(area, "circle")),
            geocodes=tuple(_named(area, "geocode")),
        )
        for area in info.findall(CAP + "area")[:MAX_AREAS]
    )
    return CapInfo(
        event=_text(info, "event"),
        category=next((value for name, value in codes if name == "TsunamiSystemCategory"), None),
        severity=_text(info, "severity"),
        urgency=_text(info, "urgency"),
        certainty=_text(info, "certainty"),
        response_type=_text(info, "responseType"),
        onset_utc=parse_iso_utc(_text(info, "onset")),
        expires_utc=parse_iso_utc(_text(info, "expires")),
        sender_name=_text(info, "senderName"),
        headline=_text(info, "headline"),
        description=_text(info, "description"),
        instruction=_text(info, "instruction"),
        web=_text(info, "web"),
        parameters=tuple(_named(info, "parameter")),
        areas=areas,
        event_codes=tuple(codes),
    )


def _reject_dtd(content: bytes) -> None:
    """A CAP document never has a DTD: refuse one (entity expansion is how an XML parser is attacked)."""
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("tsunami.gov document carries a DTD or an entity declaration")


def parse_message(content: bytes, bulletin: Bulletin) -> Message:
    """One CAP document of the centres. A document that is not a CAP alert raises ``ValueError``."""
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("tsunami.gov document is larger than any message ever was")
    _reject_dtd(content)
    root = ET.fromstring(content)
    if root.tag != CAP + "alert":
        raise ValueError(f"tsunami.gov document is not a CAP alert ({root.tag})")
    identifier = _text(root, "identifier")
    if identifier is None:
        raise ValueError("tsunami.gov CAP document has no identifier")
    infos = tuple(_info(info) for info in root.findall(CAP + "info"))
    return Message(
        bulletin=bulletin,
        identifier=identifier,
        sent_utc=parse_iso_utc(_text(root, "sent")),
        status=_text(root, "status"),
        message_type=_text(root, "msgType"),
        source=_text(root, "source"),
        infos=infos,
        etas=tuple(_cap_etas(infos)),
    )


def _cap_etas(infos: tuple[CapInfo, ...]) -> list[Eta]:
    """The arrival times of the CAP parameters ``predictedArrivalTime: "Place"``, placed by the area of the same name when it has one."""
    etas: list[Eta] = []
    for info in infos:
        circles = {area.description: area.circle for area in info.areas if area.description and area.circle}
        for name, value in info.parameters:
            found = re.fullmatch(r'predictedArrivalTime:\s*"(.*)"', name)
            at = parse_iso_utc(value)
            if found is None or at is None or not found.group(1).strip():
                continue
            place = found.group(1).strip()
            circle = circles.get(place)
            etas.append(Eta(place=place, eta_utc=at, lat=None if circle is None else circle[0], lon=None if circle is None else circle[1]))
            if len(etas) >= MAX_ETAS:
                return etas
    return etas


_ETA_HEADER = re.compile(r"^\s*LOCATION\s+REGION\s+COORDINATES\s+ETA", re.IGNORECASE)
_ETA_LINE = re.compile(
    r"^\s+(?P<place>\S.*?)\s{2,}(?P<region>\S.*?)\s{2,}"
    r"(?P<lat>\d+(?:\.\d+)?)(?P<ns>[NS])\s+(?P<lon>\d+(?:\.\d+)?)(?P<ew>[EW])\s+"
    r"(?P<hh>\d{2})(?P<mi>\d{2})\s+(?P<mo>\d{2})/(?P<dd>\d{2})\s*$"
)


def parse_text_etas(text: str, sent_utc: Optional[int]) -> list[Eta]:
    """The table of estimated arrival times of an international threat message (``LOCATION REGION COORDINATES ETA(UTC)``).

    ``hhmm mm/dd`` carries no year: it is the one that puts the date closest to the issue of the message. A line that does not
    read as a row ends the table. No table (a message before the first forecast) gives none.
    """
    if sent_utc is None:
        return []
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if _ETA_HEADER.match(line)), None)
    if start is None:
        return []
    reference = datetime.fromtimestamp(sent_utc, tz=timezone.utc)
    etas: list[Eta] = []
    for line in lines[start + 1 :]:
        if not line.strip() or set(line.strip()) == {"-"}:
            if etas:
                break
            continue
        found = _ETA_LINE.match(line)
        if found is None:
            break
        lat = float(found["lat"]) * (1.0 if found["ns"] == "N" else -1.0)
        lon = float(found["lon"]) * (1.0 if found["ew"] == "E" else -1.0)
        at = _month_day_time(reference, int(found["mo"]), int(found["dd"]), int(found["hh"]), int(found["mi"]))
        if at is None or not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
            continue
        etas.append(Eta(place=found["place"].strip(), region=found["region"].strip(), eta_utc=at, lat=lat, lon=lon, source="text"))
        if len(etas) >= MAX_ETAS:
            break
    return etas


def _month_day_time(reference: datetime, month: int, day: int, hour: int, minute: int) -> Optional[int]:
    """The instant ``month/day hh:mm`` (UTC) in the year that puts it closest to ``reference``."""
    best: Optional[datetime] = None
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidate = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            continue
        if best is None or abs(candidate - reference) < abs(best - reference):
            best = candidate
    return None if best is None or abs(best - reference) > timedelta(days=60) else int(best.timestamp())


# ------------------------------------------------------------------------------------------------------ alerts, events, hazards


def alerts_of(message: Message, now_utc: float) -> list[Alert]:
    """The infos of a message that are an alert or a threat in force now, with their level.

    A message with no ``info`` (the PTWC "final" bulletin), a cancellation, an information statement, a threat class that is in no
    table, and an info whose end is past are none.
    """
    alerts: list[Alert] = []
    if (message.status or "").lower() != "actual":
        return alerts
    for index, info in enumerate(message.infos):
        level, original = _level(message, info)
        if level is None or original is None:
            continue
        until = info.expires_utc if info.expires_utc is not None else (message.sent_utc or 0) + ACTIVE_FOR_S
        if message.sent_utc is None or until < now_utc:
            continue
        alerts.append(Alert(message=message, index=index, info=info, level=level, level_original=original, until_utc=until))
    return alerts


def _level(message: Message, info: CapInfo) -> tuple[Optional[SeverityLevel], Optional[str]]:
    if message.bulletin.wmo_id in THREAT_PRODUCTS:
        word = " ".join((info.event or "").split())
        return THREAT_LEVELS.get(word.lower()), word or None
    category = info.category
    if category is None:
        return None, None
    return DOMESTIC_LEVELS.get(category.lower()), category


def _parameter(info: CapInfo, name: str) -> Optional[str]:
    return next((value for key, value in info.parameters if key == name), None)


def origin_of(alerts: tuple[Alert, ...]) -> Optional[Origin]:
    """The earthquake of an event, from the parameters of its most recent alert (``EventLatLon``, ``EventOriginTime``...)."""
    for alert in sorted(alerts, key=lambda a: a.message.sent_utc or 0, reverse=True):
        found = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)(?:\s+\S+)?\s*", _parameter(alert.info, "EventLatLon") or "")
        if found is None:
            continue
        lat, lon = float(found.group(1)), float(found.group(2))
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        depth = re.match(r"\s*(\d+(?:\.\d+)?)\s*(kilometers|km)", _parameter(alert.info, "EventDepth") or "", re.IGNORECASE)
        return Origin(
            lat=lat,
            lon=lon,
            time_utc=parse_iso_utc(_parameter(alert.info, "EventOriginTime")),
            magnitude=optional_float(_parameter(alert.info, "EventPreliminaryMagnitude")),
            magnitude_type=_parameter(alert.info, "EventPreliminaryMagnitudeType"),
            depth_km=None if depth is None else float(depth.group(1)),
            location=_parameter(alert.info, "EventLocationName"),
        )
    return None


def events_of(messages: list[Message], now_utc: float) -> tuple[list[TsunamiEvent], int]:
    """The events that have an alert in force, and how many events had alerts but no epicentre (they are left out and counted)."""
    by_event: dict[tuple[str, str], list[Message]] = {}
    for message in messages:
        by_event.setdefault(message.bulletin.incident, []).append(message)
    events: list[TsunamiEvent] = []
    unplaced = 0
    for (twc, event_id), group in sorted(by_event.items()):
        alerts = tuple(alert for message in group for alert in alerts_of(message, now_utc))
        if not alerts:
            continue
        origin = origin_of(alerts)
        if origin is None:
            unplaced += 1
            continue
        # The messages here are the latest of each product, so their arrival times are the current ones; a place two products give once.
        etas: dict[str, Eta] = {}
        for message in sorted(group, key=lambda m: m.sent_utc or 0):
            for eta in message.etas:
                etas[f"{eta.place}|{eta.region}"] = eta
        events.append(TsunamiEvent(twc=twc, event_id=event_id, origin=origin, alerts=alerts, etas=tuple(etas.values())))
    return events, unplaced


VTEC = re.compile(r"/[A-Z]\.[A-Z]{3}\.([A-Z]{4})\.([A-Z]{2})\.[A-Z]\.(\d{4})\.")


def vtec_event_key(value: Optional[str]) -> Optional[str]:
    """The key an NWS product shares with the centre's message it relays: the office, the phenomenon and the number of its VTEC code.

    ``/O.NEW.PAAQ.TS.W.0024.121028T0307Z-000000T0000Z/`` is ``vtec:PAAQ.TS.0024``. The significance (W, A, Y) is left out: a watch
    that becomes a warning is the same event.
    """
    found = VTEC.search(value or "")
    return None if found is None else f"vtec:{found.group(1)}.{found.group(2)}.{found.group(3)}"


def vtec_key(event: TsunamiEvent) -> Optional[str]:
    """The key of an event whose messages carry a VTEC code (the domestic ones do): the NWS carries the same code in its alert for the
    same message, so the hazard of the centre and the NWS one are told to be the same event without comparing positions (the NWS
    gives a zone, the centre an epicentre)."""
    for alert in event.alerts:
        key = vtec_event_key(_parameter(alert.info, "VTEC"))
        if key is not None:
            return key
    return None


def _clean(text: str) -> str:
    return " ".join(text.split())


def hazard_of(event: TsunamiEvent) -> HazardAlert:
    """The hazard of an event: at the epicentre, the level of its strongest alert, the earthquake time as its time."""
    strongest = event.strongest
    # A threat message names its class in the event ("Tsunami 1-3 meters"); a domestic one has the alert level ("Warning").
    what = strongest.level_original if strongest.message.bulletin.wmo_id in THREAT_PRODUCTS else f"Tsunami {strongest.level_original}"
    where = event.origin.location
    title = _clean(f"{what} - {where}" if where else what)
    source = CENTRES[event.twc]
    return HazardAlert(
        id=event.hazard_id,
        category=HazardCategory.TSUNAMI,
        severity=event.level,
        title=title[:96],
        source=source,
        sources=[source],
        timestamp_utc=event.origin.time_utc or strongest.message.sent_utc or 0,
        lat=round(event.origin.lat, 4),
        lon=round(event.origin.lon, 4),
        value_primary=None,
        value_unit=None,
        tsunami_warning=True,
    ).with_event_key(vtec_key(event))


def country_of_codes(codes: tuple[tuple[str, str], ...]) -> Optional[str]:
    """The country of a set of UGC zone codes: ``CA`` for the British Columbia ones (``BCZ220``), ``US`` for any other zone, else none."""
    ugc = [value for name, value in codes if name == "UGC"]
    if not ugc:
        return None
    return "CA" if all(value.startswith("BC") for value in ugc) else "US"


def vigilances_of(event: TsunamiEvent) -> list[Vigilance]:
    """The alerts of an event as vigilances: one per info and country, the words of the centre untouched."""
    vigilances: list[Vigilance] = []
    for alert in event.alerts:
        by_country: dict[Optional[str], list[CapArea]] = {}
        for area in alert.info.areas:
            by_country.setdefault(country_of_codes(area.geocodes), []).append(area)
        if not by_country:
            by_country[None] = []
        for country, areas in by_country.items():
            vigilances.append(_vigilance(event, alert, country, areas, len(by_country) > 1))
    return vigilances


def _vigilance(event: TsunamiEvent, alert: Alert, country: Optional[str], areas: list[CapArea], split: bool) -> Vigilance:
    info, message = alert.info, alert.message
    descriptions = [area.description for area in areas if area.description]
    codes: list[VigilanceGeocode] = []
    for area in areas:
        for scheme, value in area.geocodes:
            code = VigilanceGeocode(scheme=scheme, value=value)
            if code not in codes:
                codes.append(code)
    parameters = [VigilanceParameter(name=f"eventCode:{name}", value=value) for name, value in info.event_codes]
    parameters += [VigilanceParameter(name=name, value=value) for name, value in info.parameters]
    sender = info.sender_name
    credit = credit_text("noaa_tsunami")
    message_type = message.message_type
    suffix = f":{country or 'xx'}" if split else ""
    return Vigilance(
        id=f"tsunami:{message.identifier}:{alert.index}{suffix}",
        source="tsunami",
        country=country,
        attribution=f"{credit} | Issued by {sender}" if sender else credit,
        identifier=message.identifier,
        sent_utc=message.sent_utc,
        effective_utc=None,
        onset_utc=info.onset_utc,
        expires_utc=info.expires_utc,  # a PTWC threat message has none: our three hours are not the centre's word
        message_type=message_type,
        status={"alert": VigilanceStatus.NEW, "update": VigilanceStatus.UPDATE, "cancel": VigilanceStatus.CANCEL}.get((message_type or "").lower()),
        level=alert.level,
        level_original=alert.level_original,
        phenomenon=VigilancePhenomenon.COASTAL,
        phenomenon_original=info.event,
        event=info.event,
        issuer=sender,
        texts=[
            VigilanceText(
                language=None,
                sender_name=sender,
                event=info.event,
                headline=info.headline,
                description=info.description,
                instruction=info.instruction,
                web=info.web,
                parameters=parameters,
            )
        ],
        area=VigilanceArea(description="; ".join(descriptions) or None, geocodes=codes, has_polygon=False),
        link=message.bulletin.cap_url,
        web=info.web,
    )


# ------------------------------------------------------------------------------------------------------ the collector


class TsunamiCollector(BaseCollector[list[HazardAlert]]):
    """The alerts of the U.S. Tsunami Warning Centers: one conditional request a minute, a document per new bulletin."""

    # 60 s: the centres' first message comes minutes after an earthquake and the list is updated within about 25 seconds of an
    # issue (the list of 2026-10-20 was modified at 10:45:26 for a bulletin sent at 10:45:01 and stored at 10:45:23). Nothing is
    # published about a rhythm or a limit (**not verified**); a refresh that finds nothing new is a ``304`` of a few hundred bytes.
    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="tsunami_warning_centers",
            name="NOAA U.S. Tsunami Warning Centers (NTWC, PTWC)",
            domain="disasters",
            ttl_seconds=60,
            http_client=http_client,
        )
        self.last_state: TsunamiState = TsunamiState()
        self._listing: Optional[Listing] = None
        self._messages: dict[str, Message] = {}
        self._failed: dict[str, float] = {}  # a document that could not be read -> when to ask for it again

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[list[HazardAlert]]:
        previous: Optional[CacheEntry[list[HazardAlert]]] = kwargs.get("previous")
        headers = {"Accept": "*/*"}
        if previous is not None and previous.validators and self._listing is not None:
            for name, header in (("ETag", "If-None-Match"), ("Last-Modified", "If-Modified-Since")):
                if previous.validators.get(name):
                    headers[header] = previous.validators[name]
        response = await self._get(client, LISTING_URL, headers=headers)
        updated: Optional[int]
        if response.status_code == 304 and self._listing is not None and previous is not None:
            listing, validators = self._listing, previous.validators
            updated = self.last_state.updated_utc
        else:
            response.raise_for_status()
            listing = parse_listing(response.text)
            self._listing = listing
            validators = {name: response.headers[name] for name in ("ETag", "Last-Modified") if response.headers.get(name)} or None
            modified = response.headers.get("Last-Modified")
            stamp = http_date_to_epoch(modified)
            updated = None if stamp is None else int(stamp)
        now = clock.wall()
        state = await self._state(client, listing, now, updated)
        self.last_state = state
        return Collected([hazard_of(event) for event in state.events], validators=validators)

    async def _state(self, client: httpx.AsyncClient, listing: Listing, now: float, updated: Optional[int]) -> TsunamiState:
        chosen, information = wanted_bulletins(listing, now)
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def load(bulletin: Bulletin) -> Optional[Message]:
            url = bulletin.cap_url
            held = self._messages.get(url)
            if held is not None:
                return held
            if self._failed.get(url, 0.0) > clock.monotonic():
                return None
            async with semaphore:
                try:
                    response = await self._get(client, url, headers={"Accept": "application/xml, text/xml;q=0.9, */*;q=0.5"})
                    response.raise_for_status()
                    message = parse_message(response.content, bulletin)
                    if bulletin.wmo_id in THREAT_PRODUCTS and message.infos:
                        message = await self._with_text_etas(client, message)
                except Exception:  # the product is left out this time; the next refresh asks again, not before a minute has passed
                    self._failed[url] = clock.monotonic() + 60.0
                    return None
            self._messages[url] = message
            return message

        loaded = await asyncio.gather(*(load(bulletin) for bulletin in chosen))
        messages = [message for message in loaded if message is not None]
        wanted_urls = {bulletin.cap_url for bulletin in chosen}
        for url in [url for url in self._messages if url not in wanted_urls]:
            del self._messages[url]
        for url in [url for url, until in self._failed.items() if until <= clock.monotonic()]:
            del self._failed[url]
        events, unplaced = events_of(messages, now)
        return TsunamiState(
            events=tuple(events),
            information_statements=information,
            unreadable=listing.unreadable + (len(chosen) - len(messages)) + unplaced,
            updated_utc=updated,
        )

    async def _with_text_etas(self, client: httpx.AsyncClient, message: Message) -> Message:
        """The arrival times of a threat message come in its text bulletin; a bulletin that cannot be read just has none."""
        try:
            response = await self._get(client, message.bulletin.text_url, headers={"Accept": "text/plain, */*;q=0.5"})
            response.raise_for_status()
            etas = parse_text_etas(response.content[:MAX_DOCUMENT_BYTES].decode("latin-1"), message.sent_utc)
        except Exception:
            return message
        return replace(message, etas=tuple(etas))

    def event_for(self, hazard_id: str) -> Optional[TsunamiEvent]:
        """The event of a hazard id (``tsunami_pheb_26282000``) in the last answer read."""
        return next((event for event in self.last_state.events if event.hazard_id == hazard_id), None)

    def vigilances(self) -> list[Vigilance]:
        """Every alert of the last answer read, as vigilances."""
        return [vigilance for event in self.last_state.events for vigilance in vigilances_of(event)]

    async def fetch_vigilances(self) -> Fetched[list[Vigilance]]:
        """The alerts of the cached answer as vigilances: no request of their own, they come with the hazards'."""
        fetched = await self.fetch_ex()
        return Fetched(self.vigilances(), stale=fetched.stale)

