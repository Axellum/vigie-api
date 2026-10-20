"""MetService collector: the public CAP alerts of the Meteorological Service of New Zealand as ``Vigilance`` objects.

MetService publishes "Current Watches, Warnings and Advisories for New Zealand" as an RSS feed
(``https://alerts.metservice.com/cap/rss``, also ``/cap/atom``) whose items link to CAP 1.2 documents, without a key.
The format is **documented by the issuer**: the MetService CAP Feed User Guide, version 1.2 of 20 April 2026
(https://www.metservice.com/files/documents/cap/MetService-CAP-Feed-User-Guide-v1.2.pdf, linked from
https://www.metservice.com/about/common-alerting-protocol; read on 2026-10-20). The feed says of itself (its
``<copyright>`` element): "Copyright Meteorological Service of New Zealand Limited, 2026. Licensed under Creative
Commons BY 4.0." It sends ``Cache-Control: max-age=300`` and a ``Last-Modified`` that answers ``If-Modified-Since``
with a ``304``; the guide asks for a poll at least every 5 minutes and not more often than every 2 minutes.

What the guide says and this module relies on (section numbers are the guide's):

- 2.3: an item of the feed is ONE area under a Watch or a Warning in force; the feed holds nothing lifted, cancelled,
  replaced or expired, and no item at all when nothing is in force. The item's ``link`` is the address of the CAP
  document; the CAP documents carry the ``<area>`` polygons ("lat,lon" pairs) and are always signed (XML signature, not
  checked here).
- 2.2: only Severe Weather Watches and Orange / Red Warnings (heavy rain, heavy snow, strong wind), Severe Thunderstorm
  Watches and Warnings, and Road Snowfall Warnings. Marine warnings and outlooks are NOT in the feed.
- 3.2.14.2: the ``ColourCode`` parameter is always present and is Yellow (Watches and the Severe Thunderstorm Watch),
  Orange (Orange Warnings and the Road Snowfall Warning) or Red (Red Warnings and the Severe Thunderstorm Warning). It is
  the level, in the issuer's own words; the CAP ``severity`` is not (a Severe Thunderstorm Watch is Moderate and yellow).
- 3.2.2: ``<event>`` is one of ``rain``, ``snow``, ``wind``, ``thunderstorm`` and ``localStorm`` (a localised severe
  event that is not a thunderstorm, used for a Severe Thunderstorm Watch). The guide warns the word may later move to an
  ``eventCode``: any other word falls back on the words of the headline.

**What was and was not verified.** On 2026-10-20 no warning was in force in New Zealand: the feed was empty, and the only
things recorded are the empty RSS and Atom answers (``tests/fixtures/metservice/``). **No real item and no real CAP
document has been seen here.** The tests that need an item use a feed and CAP documents written after the guide's own
examples (its Appendices A and B) and labelled **synthetic**. An item whose address is not under
``https://alerts.metservice.com/``, or a document that does not read as a CAP alert, is counted as unreadable and said so
in the answer; the weekly check of the live feeds fails if an item cannot be read, has a colour or an event the guide
does not list, or a colour that contradicts its headline.

The level is the ``ColourCode`` (Yellow, Orange, Red); without it, the words of the headline, following the guide's
table of headlines ("Heavy Rain Warning - Orange", "Heavy Rain Watch", "Road Snowfall Warning", "Severe Thunderstorm
Watch", "Severe Thunderstorm Warning"). A colour that contradicts the headline, or that is not one of the three, gives
``None``: nothing is guessed. The texts, the parameters (``NextUpdate``, ``ChanceOfUpgrade``, the colour) and the
instruction stay untouched, as the Code of Practice that goes with the feed asks (pass the information on without
modification, say MetService is its source).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.attribution import credit_text
from app.collectors.base import BaseCollector, CacheEntry, Collected, Fetched, parse_iso_utc
from app.geo import parse_cap_polygon, rings_to_shape
from app.models.schemas import SeverityLevel
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
FEED_URL = "https://alerts.metservice.com/cap/rss"
CAP_URL_PREFIX = "https://alerts.metservice.com/"
MAX_ITEMS = 100  # per refresh: a feed with more is a mistake

STATUSES: dict[str, VigilanceStatus] = {
    "alert": VigilanceStatus.NEW,
    "update": VigilanceStatus.UPDATE,
    "cancel": VigilanceStatus.CANCEL,
}

_COLOURS = {"red": SeverityLevel.RED, "orange": SeverityLevel.ORANGE, "yellow": SeverityLevel.YELLOW}
# The values of <event> the guide lists (3.2.2) and the phenomenon each one is. ``localstorm`` is a severe local event
# that is NOT a thunderstorm: "other", not a guess.
EVENTS = {
    "rain": VigilancePhenomenon.RAIN,
    "snow": VigilancePhenomenon.SNOW_ICE,
    "wind": VigilancePhenomenon.WIND,
    "thunderstorm": VigilancePhenomenon.THUNDERSTORM,
    "localstorm": VigilancePhenomenon.OTHER,
}
COLOUR_PARAMETER = "ColourCode"
_WORD = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class MetServiceFeed:
    """The warnings in force in New Zealand as the feed and its CAP documents give them, and how the reading went."""

    vigilances: tuple[Vigilance, ...]
    items: int = 0  # items the feed lists
    unreadable: int = 0  # items whose address is not MetService's, or whose document does not read as a CAP alert
    copyright: Optional[str] = None  # the licence line of the feed, as given


def _level_from_words(event: Optional[str], headline: Optional[str]) -> Optional[SeverityLevel]:
    """The level the words of a warning say, after the guide's table of headlines; ``None`` when they say nothing."""
    for text in (headline, event):
        words = set(_WORD.findall((text or "").lower()))
        found = {_COLOURS[word] for word in words if word in _COLOURS}
        if len(found) == 1:
            return next(iter(found))
        if len(found) > 1:
            return None
    words = set(_WORD.findall(f"{headline or ''} {event or ''}".lower()))
    if "thunderstorm" in words and "warning" in words:
        return SeverityLevel.RED  # Severe Thunderstorm Warning: Red (guide 3.2.14.2)
    if "road" in words and "snowfall" in words:
        return SeverityLevel.ORANGE  # Road Snowfall Warning: Orange (guide 3.2.14.2)
    if "watch" in words and "warning" not in words:
        return SeverityLevel.YELLOW  # every Watch: Yellow (guide 3.2.14.2)
    return None


def derive_level(event: Optional[str], headline: Optional[str], colour: Optional[str] = None) -> Optional[SeverityLevel]:
    """Our level for a MetService warning: its ``ColourCode``, else the words of its headline; ``None`` when unsure.

    The ``ColourCode`` parameter (always present, guide 3.2.14.2) is the issuer's own level: Yellow, Orange or Red. A
    value that is none of these gives ``None``. A colour that the words of the headline contradict also gives ``None``.
    Without a ``ColourCode`` the words decide (:func:`_level_from_words`).
    """
    words = _level_from_words(event, headline)
    if colour is None or not colour.strip():
        return words
    level = _COLOURS.get(colour.strip().lower())
    if level is None or (words is not None and words != level):
        return None
    return level


def derive_phenomenon(event: Optional[str], headline: Optional[str]) -> Optional[VigilancePhenomenon]:
    """Our phenomenon from ``<event>`` (rain, snow, wind, thunderstorm, localStorm), else from the words of a warning."""
    if not (event or headline):
        return None
    known = EVENTS.get((event or "").strip().lower())
    if known is not None:
        return known
    words = set(_WORD.findall(f"{headline or ''} {event or ''}".lower()))
    for word, phenomenon in (
        ("thunderstorm", VigilancePhenomenon.THUNDERSTORM),
        ("tornado", VigilancePhenomenon.WIND),
        ("snow", VigilancePhenomenon.SNOW_ICE),
        ("rain", VigilancePhenomenon.RAIN),
        ("wind", VigilancePhenomenon.WIND),
    ):
        if word in words:
            return phenomenon
    return VigilancePhenomenon.OTHER


def _text(element: ET.Element, tag: str) -> Optional[str]:
    value = element.findtext(tag)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _reject_dtd(content: bytes) -> None:
    if b"<!DOCTYPE" in content[:4096].upper() or b"<!ENTITY" in content.upper():
        raise ValueError("MetService answer carries a DTD or an entity declaration")


def parse_cap(content: bytes, copyright_line: Optional[str] = None, link: Optional[str] = None) -> list[Vigilance]:
    """The warnings (one per area) of a CAP 1.2 document; ``[]`` for a test or an exercise. Raises if it is not a CAP alert.

    ``link`` is the address the document was read at (the item's link in the feed).
    """
    _reject_dtd(content)
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"MetService document is not XML: {exc}") from exc
    if root.tag != CAP + "alert":
        raise ValueError(f"MetService document is not a CAP alert ({root.tag})")
    if (_text(root, CAP + "status") or "").lower() != "actual":
        return []
    identifier = _text(root, CAP + "identifier")
    infos = root.findall(CAP + "info")
    if identifier is None or not infos:
        return []
    message_type = _text(root, CAP + "msgType")

    texts: list[VigilanceText] = []
    for info in infos:
        parameters = [
            VigilanceParameter(name=name, value=value)
            for parameter in info.findall(CAP + "parameter")
            if (name := _text(parameter, CAP + "valueName")) and (value := _text(parameter, CAP + "value"))
        ]
        texts.append(
            VigilanceText(
                language=_text(info, CAP + "language"),
                sender_name=_text(info, CAP + "senderName"),
                event=_text(info, CAP + "event"),
                headline=_text(info, CAP + "headline"),
                description=_text(info, CAP + "description"),
                instruction=_text(info, CAP + "instruction"),
                web=_text(info, CAP + "web"),
                parameters=parameters,
            )
        )
    # The areas and the times are the first language's (the other languages describe the same warning).
    info = infos[0]
    text = texts[0]
    severity = _text(info, CAP + "severity")
    colour = next((p.value for p in text.parameters if p.name == COLOUR_PARAMETER), None)
    issuer = text.sender_name
    credit = credit_text("metservice_alerts")
    attribution = " | ".join(part for part in (credit, copyright_line, f"Issued by {issuer}" if issuer else None) if part)

    vigilances: list[Vigilance] = []
    for index, area in enumerate(info.findall(CAP + "area")):
        rings = [ring for ring in (parse_cap_polygon(p.text) for p in area.findall(CAP + "polygon")) if ring]
        geocodes = [
            VigilanceGeocode(scheme=name, value=value)
            for geocode in area.findall(CAP + "geocode")
            if (name := _text(geocode, CAP + "valueName")) and (value := _text(geocode, CAP + "value"))
        ]
        description = _text(area, CAP + "areaDesc")
        vigilance = Vigilance(
            id=f"metservice:{identifier}:{index}",
            source="metservice",
            country="NZ",
            attribution=attribution,
            identifier=identifier,
            sent_utc=parse_iso_utc(_text(root, CAP + "sent")),
            effective_utc=parse_iso_utc(_text(info, CAP + "effective")),
            onset_utc=parse_iso_utc(_text(info, CAP + "onset")),
            expires_utc=parse_iso_utc(_text(info, CAP + "expires")),
            message_type=message_type,
            status=STATUSES.get(message_type.lower()) if message_type else None,
            level=derive_level(text.event, text.headline, colour),
            level_original=colour or severity,  # the issuer's colour; the CAP severity only when it gave none
            phenomenon=derive_phenomenon(text.event, text.headline),
            phenomenon_original=text.event,
            event=text.event,
            issuer=issuer,
            texts=texts,
            area=VigilanceArea(description=description, geocodes=geocodes, has_polygon=bool(rings)),
            link=link,
            web=text.web,
        )
        vigilances.append(vigilance.with_shape(rings_to_shape(rings)))
    return vigilances


def parse_feed(content: bytes) -> tuple[list[str], int, Optional[str]]:
    """The addresses of the CAP documents an RSS feed lists, how many items it has, and its copyright line (as given)."""
    _reject_dtd(content)
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"MetService answer is not XML: {exc}") from exc
    if root.tag != "rss":
        raise ValueError(f"MetService answer is not an RSS feed ({root.tag})")
    channel = root.find("channel")
    if channel is None:
        raise ValueError("MetService RSS feed has no channel")
    copyright_line = _text(channel, "copyright")
    addresses: list[str] = []
    items = channel.findall("item")
    for item in items[:MAX_ITEMS]:
        for candidate in (_text(item, "link"), _text(item, "guid")):
            if candidate and candidate.startswith(CAP_URL_PREFIX):
                addresses.append(candidate)
                break
        else:
            addresses.append("")  # an item with no address of MetService's: counted as unreadable
    return addresses, len(items), copyright_line


class MetServiceCollector(BaseCollector[MetServiceFeed]):
    """The RSS feed of New Zealand, cached 300 seconds, then the CAP documents of its items (each fetched once)."""

    # 300 s: the feed itself sends Cache-Control: max-age=300. It answers If-Modified-Since with a 304.
    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="metservice_cap",
            name="MetService public CAP alerts (New Zealand)",
            domain="warnings",
            ttl_seconds=300,
            http_client=http_client,
        )
        self._documents: dict[str, tuple[Vigilance, ...]] = {}

    async def fetch_feed(self, allow_stale_on_error: bool = True) -> Fetched[MetServiceFeed]:
        """The warnings of New Zealand, from the cache while it is fresh, else the feed (conditional) and new documents."""
        return await self.fetch_ex(cache_key="feed", allow_stale_on_error=allow_stale_on_error)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[MetServiceFeed]:
        previous: Optional[CacheEntry[MetServiceFeed]] = kwargs.get("previous")
        headers = {"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5"}
        if previous is not None and previous.validators and previous.validators.get("Last-Modified"):
            headers["If-Modified-Since"] = previous.validators["Last-Modified"]
        resp = await self._get(client, FEED_URL, headers=headers)
        if resp.status_code == 304:
            if previous is None:
                raise ValueError("MetService answered 304 Not Modified to a request that was not conditional")
            return Collected(previous.value, validators=previous.validators)
        resp.raise_for_status()
        addresses, items, copyright_line = parse_feed(resp.content)
        found: dict[str, tuple[Vigilance, ...]] = {}
        unreadable = items - len(addresses)  # items beyond the cap
        for address in dict.fromkeys(addresses):
            if not address:
                unreadable += 1
                continue
            if address not in self._documents:
                doc = await self._get(client, address, headers={"Accept": "application/cap+xml, application/xml;q=0.9, */*;q=0.5"})
                if doc.status_code in (404, 410):  # the feed lists a document the server no longer has
                    unreadable += 1
                    continue
                doc.raise_for_status()
                try:
                    self._documents[address] = tuple(parse_cap(doc.content, copyright_line, address))
                except ValueError:
                    unreadable += 1
                    continue
            found[address] = self._documents[address]
        for stale in [a for a in self._documents if a not in addresses]:
            del self._documents[stale]  # a warning that left the feed
        vigilances = tuple(v for address in dict.fromkeys(addresses) if address in found for v in found[address])
        validators = {"Last-Modified": resp.headers["Last-Modified"]} if resp.headers.get("Last-Modified") else None
        return Collected(
            MetServiceFeed(vigilances=vigilances, items=items, unreadable=unreadable, copyright=copyright_line),
            validators=validators,
        )
