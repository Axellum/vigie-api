"""MeteoAlarm collectors: the official warnings of the European national weather services (``Vigilance``).

MeteoAlarm (EUMETNET, run by GeoSphere Austria) publishes one Atom feed per country, without a key
(https://feeds.meteoalarm.org/). Each entry is one warning for one area (a code, or a polygon): the
level and the phenomenon are in its title, the times and the identifier in its CAP elements. The words of the
issuer (headline, description, instruction, in each language) are only in the CAP document the entry links
to. So there are two collectors, counted separately in ``GET /v1/providers``:

- ``MeteoAlarmCollector`` (``meteoalarm_atom``): the feed of ONE country per request, cached 180 seconds.
  The terms of MeteoAlarm (https://meteoalarm.org/en/live/page/terms-and-conditions, section 5) want the
  warnings on the redistributor's side with a delay of five minutes on average and ten at most; a cache that
  lives 3 minutes keeps the age of what is served under 3 minutes (4.5 with the scheduler's 90 seconds).
  The server sends no ``ETag`` or ``Last-Modified`` (checked on 2026-10-20), so a conditional request is
  not possible; the code sends one if the server starts to give validators.
- ``MeteoAlarmCapCollector`` (``meteoalarm_cap``): one CAP document per request, only for the warnings that
  an answer returns, kept for 6 hours (a document never changes: an update is a new document).

Nothing here guesses. A warning whose level or phenomenon is not in the tables has ``None`` there; a CAP
document the server no longer has (HTTP 404, seen for Italy on 2026-10-20) leaves the texts empty.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

import httpx

from app.attribution import credit_text
from app.collectors.base import BaseCollector, CacheEntry, Collected, Fetched, parse_iso_utc
from app.geo import Ring, parse_cap_polygon, rings_to_shape
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

ATOM = "{http://www.w3.org/2005/Atom}"
CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"

FEED_HOST = "https://feeds.meteoalarm.org"
FEED_URL = FEED_HOST + "/feeds/meteoalarm-legacy-atom-{slug}"
CAP_URL_PREFIX = FEED_HOST + "/api/v1/warnings/"

# The server answers HTTP 406 to an Accept header that names only the type it serves (checked on 2026-10-20:
# "application/atom+xml" alone is refused, "*/*" and no header are accepted), so the wildcard stays.
ATOM_ACCEPT = "application/atom+xml, application/xml;q=0.9, */*;q=0.5"
CAP_ACCEPT = "application/cap+xml, application/xml;q=0.9, */*;q=0.5"

# ISO 3166-1 alpha-2 -> the name in the address of the feed (the index https://feeds.meteoalarm.org/ lists the 39).
COUNTRY_FEEDS: dict[str, str] = {
    "AD": "andorra",
    "AT": "austria",
    "BE": "belgium",
    "BA": "bosnia-herzegovina",
    "BG": "bulgaria",
    "HR": "croatia",
    "CY": "cyprus",
    "CZ": "czechia",
    "DK": "denmark",
    "EE": "estonia",
    "FI": "finland",
    "FR": "france",
    "DE": "germany",
    "GR": "greece",
    "HU": "hungary",
    "IS": "iceland",
    "IE": "ireland",
    "IL": "israel",
    "IT": "italy",
    "LV": "latvia",
    "LT": "lithuania",
    "LU": "luxembourg",
    "MT": "malta",
    "MD": "moldova",
    "ME": "montenegro",
    "NL": "netherlands",
    "NO": "norway",
    "PL": "poland",
    "PT": "portugal",
    "MK": "republic-of-north-macedonia",
    "RO": "romania",
    "RS": "serbia",
    "SK": "slovakia",
    "SI": "slovenia",
    "ES": "spain",
    "SE": "sweden",
    "CH": "switzerland",
    "UA": "ukraine",
    "GB": "united-kingdom",
}

# MeteoAlarm awareness levels (CAP profile v2.0, section 2.2.16): the colour word of the warning -> our level.
# "Minor"/green is not published in the feeds (footnote 31) but can come in an update that ends a warning.
LEVELS: dict[str, SeverityLevel] = {
    "green": SeverityLevel.GREEN,
    "yellow": SeverityLevel.YELLOW,
    "orange": SeverityLevel.ORANGE,
    "red": SeverityLevel.RED,
}

# MeteoAlarm awareness types (CAP profile v2.0, section 2.2.17): the name of the type -> our phenomenon.
# The names are the ones of the profile, lower-case; "rain-flood" (pluvial flooding) counts as a flood, "marine-hazard"
# (storm surge, high seas) as coastal, and the drought, which has no group of its own, as "other".
PHENOMENA: dict[str, VigilancePhenomenon] = {
    "wind": VigilancePhenomenon.WIND,
    "snow-ice": VigilancePhenomenon.SNOW_ICE,
    "thunderstorm": VigilancePhenomenon.THUNDERSTORM,
    "fog": VigilancePhenomenon.FOG,
    "high-temperature": VigilancePhenomenon.HEAT,
    "low-temperature": VigilancePhenomenon.COLD,
    "coastalevent": VigilancePhenomenon.COASTAL,
    "forest-fire": VigilancePhenomenon.FIRE,
    "avalanches": VigilancePhenomenon.AVALANCHE,
    "rain": VigilancePhenomenon.RAIN,
    "flooding": VigilancePhenomenon.FLOOD,
    "rain-flood": VigilancePhenomenon.FLOOD,
    "marine-hazard": VigilancePhenomenon.COASTAL,
    "drought": VigilancePhenomenon.OTHER,
}

STATUSES: dict[str, VigilanceStatus] = {
    "alert": VigilanceStatus.NEW,
    "update": VigilanceStatus.UPDATE,
    "cancel": VigilanceStatus.CANCEL,
}

# "Yellow Flooding Warning issued for France - Drôme": the title the feed writes from the awareness level and type.
_TITLE = re.compile(r"^\s*(\S+)\s+(\S+)\s+Warning issued for\s")
_EMMA_LINK = re.compile(r"[?&]geocode=([A-Za-z0-9_]+):([^&]+)")


@dataclass(frozen=True)
class MeteoAlarmFeed:
    """The warnings of one country as the feed lists them (per area), and when the feed says it was updated."""

    country: str
    updated_utc: Optional[int]
    vigilances: tuple[Vigilance, ...]
    skipped: int = 0  # entries that could not be read (a test, an exercise, or a broken one)


@dataclass(frozen=True)
class CapDocument:
    """The words of the issuer in one CAP document. ``available`` is False when the server no longer has it."""

    available: bool
    issuer: Optional[str] = None
    texts: tuple[VigilanceText, ...] = field(default_factory=tuple)


def _reject_dtd(content: bytes) -> None:
    """A feed never has a DTD: refuse one (entity expansion is how an XML parser is attacked)."""
    if b"<!DOCTYPE" in content[:4096].upper() or b"<!ENTITY" in content.upper():
        raise ValueError("MeteoAlarm answer carries a DTD or an entity declaration")


def _parse_xml(content: bytes) -> ET.Element:
    _reject_dtd(content)
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"MeteoAlarm answer is not XML: {exc}") from exc


def _text(element: ET.Element, tag: str) -> Optional[str]:
    """The stripped text of a child, ``None`` when missing or empty."""
    value = element.findtext(tag)
    if value is None:
        return None
    value = value.strip()
    return value or None


def derive_level(word: Optional[str]) -> Optional[SeverityLevel]:
    """Our level for the colour word of a MeteoAlarm warning (``None`` if it is not a known colour)."""
    return LEVELS.get(word.strip().lower()) if word else None


def derive_phenomenon(name: Optional[str]) -> Optional[VigilancePhenomenon]:
    """Our phenomenon for the awareness type name (``None`` if the type is not in the table)."""
    return PHENOMENA.get(name.strip().lower()) if name else None


def _warning_id(entry: ET.Element, cap_link: Optional[str], identifier: str, area_key: str) -> str:
    """The id of a warning for an area: the CAP document and the position of the area in it (from the entry's own id)."""
    entry_id = _text(entry, ATOM + "id")
    if entry_id:
        parts = urlsplit(entry_id)
        document = parts.path.rstrip("/").rsplit("/", 1)[-1]
        query = parse_qs(parts.query)
        position = "-".join(query[key][0] for key in sorted(query) if key.startswith("index_") and query[key])
        if document and position:
            return f"meteoalarm:{document}:{position}"
    digest = hashlib.sha1(f"{cap_link}|{identifier}|{area_key}".encode()).hexdigest()[:12]
    return f"meteoalarm:{digest}"


def parse_entry(entry: ET.Element, country: str) -> Optional[Vigilance]:
    """One Atom entry as a warning; ``None`` when it is not a real warning (a test, an exercise) or has no identifier."""
    if (_text(entry, CAP + "status") or "").lower() != "actual":
        return None
    identifier = _text(entry, CAP + "identifier")
    if identifier is None:
        return None

    title = _text(entry, ATOM + "title") or ""
    matched = _TITLE.match(title)
    level_word, type_word = (matched.group(1), matched.group(2)) if matched else (None, None)

    cap_link: Optional[str] = None
    web: Optional[str] = None
    geocodes: list[VigilanceGeocode] = []
    for link in entry.findall(ATOM + "link"):
        href = link.get("href")
        if not href:
            continue
        if link.get("type") == "application/cap+xml":
            cap_link = href
        elif link.get("rel") in (None, "alternate") and href.startswith("https://meteoalarm.org"):
            web = href
            emma = _EMMA_LINK.search(href)
            if emma:  # the entry's only EMMA_ID is in this link (France gives only NUTS3 in the CAP elements)
                geocodes.append(VigilanceGeocode(scheme=emma.group(1), value=emma.group(2)))
    for geocode in entry.findall(CAP + "geocode"):
        scheme, value = _text(geocode, ATOM + "valueName"), _text(geocode, ATOM + "value")
        if scheme and value and not any(g.scheme == scheme and g.value == value for g in geocodes):
            geocodes.append(VigilanceGeocode(scheme=scheme, value=value))

    rings: list[Ring] = [ring for ring in (parse_cap_polygon(p.text) for p in entry.findall(CAP + "polygon")) if ring]
    area_key = "|".join(f"{g.scheme}:{g.value}" for g in geocodes) or f"polygon:{len(rings)}"
    message_type = _text(entry, CAP + "message_type")

    vigilance = Vigilance(
        id=_warning_id(entry, cap_link, identifier, area_key),
        source="meteoalarm",
        country=country,
        attribution=credit_text("meteoalarm"),
        identifier=identifier,
        sent_utc=parse_iso_utc(_text(entry, CAP + "sent")),
        effective_utc=parse_iso_utc(_text(entry, CAP + "effective")),
        onset_utc=parse_iso_utc(_text(entry, CAP + "onset")),
        expires_utc=parse_iso_utc(_text(entry, CAP + "expires")),
        message_type=message_type,
        status=STATUSES.get(message_type.lower()) if message_type else None,
        level=derive_level(level_word),
        level_original=level_word,
        phenomenon=derive_phenomenon(type_word),
        phenomenon_original=type_word,
        event=_text(entry, CAP + "event"),
        area=VigilanceArea(
            description=_text(entry, CAP + "areaDesc"),
            geocodes=geocodes,
            has_polygon=bool(rings),
        ),
        link=cap_link,
        web=web,
    )
    return vigilance.with_shape(rings_to_shape(rings))


def parse_feed(content: bytes, country: str) -> MeteoAlarmFeed:
    """Parse the Atom feed of a country. An empty feed is a valid answer; a feed whose entries all fail is an error."""
    root = _parse_xml(content)
    if root.tag != ATOM + "feed":
        raise ValueError(f"MeteoAlarm answer is not an Atom feed ({root.tag})")
    entries = root.findall(ATOM + "entry")
    vigilances: list[Vigilance] = []
    skipped = 0
    broken = 0
    for entry in entries:
        try:
            parsed = parse_entry(entry, country)
        except (ValueError, TypeError, KeyError, AttributeError):
            broken += 1
            continue
        if parsed is None:
            skipped += 1
        else:
            vigilances.append(parsed)
    if entries and not vigilances and broken:
        raise ValueError(f"MeteoAlarm feed of {country}: {broken} entries, none could be read")
    return MeteoAlarmFeed(
        country=country,
        updated_utc=parse_iso_utc(_text(root, ATOM + "updated")),
        vigilances=tuple(vigilances),
        skipped=skipped + broken,
    )


def parse_cap(content: bytes) -> CapDocument:
    """The texts of a CAP document, untouched, one per ``info`` (language). The first sender name is the issuer."""
    root = _parse_xml(content)
    if root.tag != CAP + "alert":
        raise ValueError(f"MeteoAlarm document is not a CAP alert ({root.tag})")
    texts: list[VigilanceText] = []
    for info in root.findall(CAP + "info"):
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
    issuer = next((text.sender_name for text in texts if text.sender_name), None)
    return CapDocument(available=True, issuer=issuer, texts=tuple(texts))


def with_cap(vigilance: Vigilance, document: Optional[CapDocument], languages: Optional[tuple[str, ...]] = None) -> Vigilance:
    """The warning with the words of its CAP document and the issuer's name in its credit.

    ``languages`` keeps only the languages that start with one of these tags (``fr``, ``en``...); all by default.
    A document the server does not have changes nothing.
    """
    if document is None or not document.available:
        return vigilance
    texts = [
        text
        for text in document.texts
        if not languages or (text.language or "").lower().startswith(languages)
    ]
    credit = credit_text("meteoalarm")
    update: dict[str, Any] = {"texts": texts, "issuer": document.issuer}
    if document.issuer:
        update["attribution"] = f"{credit} | Issued by {document.issuer}"
    return vigilance.model_copy(update=update)


class MeteoAlarmCollector(BaseCollector[MeteoAlarmFeed]):
    """The Atom feed of one MeteoAlarm country, cached 180 seconds (see the module docstring)."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="meteoalarm_atom",
            name="MeteoAlarm Atom feeds (EUMETNET)",
            domain="warnings",
            ttl_seconds=180,
            http_client=http_client,
        )

    async def fetch_country(self, country: str, allow_stale_on_error: bool = True) -> Fetched[MeteoAlarmFeed]:
        """The feed of ``country`` (ISO alpha-2), from the cache while it is fresh, else one request."""
        if country not in COUNTRY_FEEDS:
            raise ValueError(f"MeteoAlarm has no feed for {country!r}")
        return await self.fetch_ex(
            cache_key=f"atom:{country}", country=country, allow_stale_on_error=allow_stale_on_error
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[MeteoAlarmFeed]:
        country = str(kwargs["country"])
        previous: Optional[CacheEntry[MeteoAlarmFeed]] = kwargs.get("previous")
        headers = {"Accept": ATOM_ACCEPT}
        # MeteoAlarm sends neither ETag nor Last-Modified today; if it ever does, ask conditionally.
        if previous is not None and previous.validators:
            if previous.validators.get("ETag"):
                headers["If-None-Match"] = previous.validators["ETag"]
            if previous.validators.get("Last-Modified"):
                headers["If-Modified-Since"] = previous.validators["Last-Modified"]
        resp = await self._get(client, FEED_URL.format(slug=COUNTRY_FEEDS[country]), headers=headers)
        if resp.status_code == 304:
            if previous is None:
                raise ValueError("MeteoAlarm answered 304 Not Modified to a request that was not conditional")
            return Collected(previous.value, validators=previous.validators)
        resp.raise_for_status()
        validators = {name: resp.headers[name] for name in ("ETag", "Last-Modified") if resp.headers.get(name)}
        return Collected(parse_feed(resp.content, country), validators=validators or None)


class MeteoAlarmCapCollector(BaseCollector[CapDocument]):
    """The CAP documents the entries link to, one request each, kept 6 hours (a document never changes)."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="meteoalarm_cap",
            name="MeteoAlarm CAP documents (EUMETNET)",
            domain="warnings",
            ttl_seconds=6 * 3600,
            http_client=http_client,
        )

    async def fetch_document(self, url: str) -> Fetched[CapDocument]:
        """The CAP document at ``url``. Only the documents of MeteoAlarm's own address are fetched: a feed cannot send us elsewhere."""
        if not url.startswith(CAP_URL_PREFIX):
            raise ValueError("not a MeteoAlarm CAP document address")
        return await self.fetch_ex(cache_key=url, url=url)

    def prune(self, keep: set[str]) -> int:
        """Forget the documents that no cached feed links to any more; returns how many."""
        gone = [key for key in self._cache if key not in keep]
        for key in gone:
            del self._cache[key]
        return len(gone)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[CapDocument]:
        url = str(kwargs["url"])
        resp = await self._get(client, url, headers={"Accept": CAP_ACCEPT})
        if resp.status_code in (404, 410):
            # The feed links to a document the server does not have (every Italian one on 2026-10-20). That is an
            # answer, not a failure of the provider: remember it for a few minutes and leave the texts empty.
            return Collected(CapDocument(available=False), fresh_for_s=900.0)
        resp.raise_for_status()
        return Collected(parse_cap(resp.content))
