"""Reading a CAP 1.2 alert as the national services of the WMO registry write it, and deriving the API's fields from it.

The alerts come from ~100 services that all follow CAP (OASIS Common Alerting Protocol 1.2) but write it in their own way;
what was seen on 2026-10-20 in the feeds of 88 of them (``tests/fixtures/swic/README.md``) is what this module is built
for, and what it does not understand it leaves unknown, it never guesses:

- one ``info`` per language **or** several in one document (Algeria: French and English, Macao: three languages), the
  languages of one alert are kept together as its ``texts``;
- the area is a ``polygon`` (``lat,lon`` pairs, any amount of white space between them, Macao puts newlines), a ``circle``
  (a centre and a radius in kilometres), or only codes (``geocode``: India's LGD district codes, the Philippines' SAME
  codes, Thailand's ISO 3166-2). An area with codes only is not placed on the map;
- ``msgType`` ``Update`` and ``Cancel`` name the alerts they replace in ``references`` (``sender,identifier,sent``,
  space-separated): the alert they replace is no longer in force (Argentina and Madagascar chain dozens);
- ``status`` other than ``Actual`` (``Test`` is common: Mozambique, Colombia, Ghana) is not a warning, ``scope`` other than
  ``Public`` neither; ``urgency`` ``Past`` (Macao's all-clear) and ``certainty`` ``Unlikely`` say the alert is over or will
  not happen: they are not returned either (the CAP definitions of these words);
- dates are ISO 8601 with an offset (``-00:00`` for UTC at Roshydromet), Thailand's use the Buddhist era (year 2568) in
  some documents.

The XML is parsed without a DTD or entity declaration (a hostile document cannot expand into gigabytes).
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

from app.attribution import merge_credits
from app.collectors.base import parse_iso_utc
from app.geo import Circle, Ring, parse_cap_circle, parse_cap_polygon, shapes_to_shape
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

CAP_NAMESPACE_PREFIX = "urn:oasis:names:tc:emergency:cap:1."
MAX_AREAS_PER_INFO = 300  # a document with more areas is read for its first ones (the rest is counted as skipped)
MAX_ALERT_AGE_WITHOUT_END_S = 3 * 86_400  # an alert with no expiry, sent longer ago than this, is not shown as in force
BUDDHIST_ERA_YEAR = 2400  # no Gregorian alert is dated this far ahead; every Buddhist-era year (Gregorian + 543) is above it
BUDDHIST_ERA_OFFSET = 543

STATUSES: dict[str, VigilanceStatus] = {
    "alert": VigilanceStatus.NEW,
    "update": VigilanceStatus.UPDATE,
    "cancel": VigilanceStatus.CANCEL,
}


# ---------------------------------------------------------------------------------------------------- the tables of the API

# CAP severity -> level. The CAP standard's own words, the scale the NWS table uses too (docs/data-sources.md). ``Minor`` and
# ``Unknown`` give ``None``: the services that publish colours do not agree on what a minor alert is (the DWD calls it yellow,
# the NWS green, the Philippines' tropical cyclone signal 1 is "Minor"), so no level is drawn from it.
SEVERITY_LEVELS: dict[str, SeverityLevel] = {
    "moderate": SeverityLevel.YELLOW,
    "severe": SeverityLevel.ORANGE,
    "extreme": SeverityLevel.RED,
}

# The colour words of the issuers, lower case without accents: English and the Spanish of Latin America and Spain (AEMET writes
# "amarillo", "naranja", "rojo"; Colombia "Alerta Roja"). A colour the issuer writes in its own parameter is its level, and wins over
# the CAP severity (as MetService's ``ColourCode`` does, ADR 0003).
COLOUR_LEVELS: dict[str, SeverityLevel] = {
    "green": SeverityLevel.GREEN,
    "verde": SeverityLevel.GREEN,
    "yellow": SeverityLevel.YELLOW,
    "amarillo": SeverityLevel.YELLOW,
    "amarilla": SeverityLevel.YELLOW,
    "orange": SeverityLevel.ORANGE,
    "naranja": SeverityLevel.ORANGE,
    "red": SeverityLevel.RED,
    "rojo": SeverityLevel.RED,
    "roja": SeverityLevel.RED,
}
# The names of the CAP parameters that carry a colour (lower case): MeteoAlarm's profile, AEMET's, the Colombian IDEAM's.
COLOUR_PARAMETERS = frozenset({"awareness_level", "aemet-meteoalerta nivel", "nivel"})

# Event words -> phenomenon, the first rule whose keyword is in the event wins (so a thunderstorm with heavy rain is a
# thunderstorm, as in the NWS table). Lower case, no accents; a keyword matches the start of a word. English, Spanish,
# Portuguese and French only: the languages of the events seen on 2026-10-20. An event in no rule gives ``None``.
PHENOMENON_RULES: tuple[tuple[VigilancePhenomenon, tuple[str, ...]], ...] = (
    (VigilancePhenomenon.AVALANCHE, ("avalanche", "avalancha")),
    (VigilancePhenomenon.WIND, ("tropical cyclone", "cyclone tropical", "ciclon", "cyclone", "hurricane", "huracan", "typhoon", "tifon",
                                "tropical storm", "tormenta tropical", "tempestade tropical", "tempete tropicale",
                                "tropical depression", "depresion tropical", "tornado", "waterspout")),
    (VigilancePhenomenon.COASTAL, ("storm surge", "stormsurge", "surge", "coastal", "costero", "costera", "cotier", "costeiro", "tsunami", "swell",
                                   "marejada", "oleaje", "high seas", "rough sea", "sea state", "marine", "maritim")),
    (VigilancePhenomenon.FIRE, ("fire", "incendio", "wildfire", "forestfire", "incendie", "queimada", "feux")),
    (VigilancePhenomenon.FLOOD, ("flood", "inundacion", "inondation", "crue", "enchente", "inundacao", "alagamento")),
    (VigilancePhenomenon.OTHER, ("sandstorm", "tormenta de arena")),
    (VigilancePhenomenon.SNOW_ICE, ("snow", "nieve", "nevada", "neige", "neve", "blizzard", "ventisca", "sleet", "freezing rain",
                                    "ice", "hielo", "verglas", "gelo")),
    (VigilancePhenomenon.THUNDERSTORM, ("thunder", "lightning", "tormenta", "orage", "trovoada", "raio", "relampago", "trueno")),
    (VigilancePhenomenon.COLD, ("cold", "frio", "helada", "frost", "freeze", "wind chill", "froid", "gelee", "geada", "friagem",
                                "temperaturas minimas", "low temperature", "minimum temperature")),
    (VigilancePhenomenon.HEAT, ("heat", "calor", "canicule", "hot weather", "onda de calor", "temperaturas maximas",
                                "high temperature", "maximum temperature")),
    (VigilancePhenomenon.FOG, ("fog", "niebla", "neblina", "brouillard", "nevoeiro")),
    (VigilancePhenomenon.RAIN, ("rain", "lluvia", "aguacero", "chubasco", "precipitac", "pluie", "chuva", "shower", "downpour",
                                "torrential", "monsoon", "monzon")),
    (VigilancePhenomenon.WIND, ("wind", "viento", "gale", "gust", "rafaga", "vent", "ventania", "vendaval")),
    (VigilancePhenomenon.OTHER, ("drought", "sequia", "secheresse", "dust", "polvo")),
)


def fold(text: Optional[str]) -> str:
    """Lower case without accents, for comparing words ("Lluvias", "inundación" -> "lluvias", "inundacion")."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def derive_phenomenon(events: list[Optional[str]]) -> Optional[VigilancePhenomenon]:
    """The phenomenon of an alert from the ``event`` words of its languages.

    Every language is read; the alert has a phenomenon when the languages that match a rule agree. Two languages that give
    different phenomena, or none that matches, give ``None``.
    """
    found: set[VigilancePhenomenon] = set()
    for event in events:
        words = fold(event)
        if not words.strip():
            continue
        for phenomenon, keywords in PHENOMENON_RULES:
            if any(re.search(r"(?<![a-z])" + re.escape(keyword), words) for keyword in keywords):
                found.add(phenomenon)
                break
    return next(iter(found)) if len(found) == 1 else None


def colour_of(parameters: list[VigilanceParameter]) -> Optional[tuple[str, SeverityLevel]]:
    """The colour an issuer wrote in one of its level parameters: the parameter's value and the level it says, else ``None``.

    The value must hold exactly one colour word ("2; yellow; Moderate", "Alerta Roja"); two different colours, or none, give ``None``.
    """
    for parameter in parameters:
        if fold(parameter.name).strip() not in COLOUR_PARAMETERS:
            continue
        levels = {COLOUR_LEVELS[word] for word in re.findall(r"[a-z]+", fold(parameter.value)) if word in COLOUR_LEVELS}
        if len(levels) == 1:
            return parameter.value, next(iter(levels))
    return None


def derive_level(severity: Optional[str], parameters: list[VigilanceParameter]) -> tuple[Optional[SeverityLevel], Optional[str]]:
    """The level of an alert and the issuer's own word for it.

    The colour the issuer wrote in a level parameter, else the CAP severity (``SEVERITY_LEVELS``); ``None`` for any other word.
    The second value is that word: the colour's value, else the severity as written.
    """
    colour = colour_of(parameters)
    if colour is not None:
        return colour[1], colour[0]
    word = (severity or "").strip()
    return SEVERITY_LEVELS.get(word.lower()), word or None


# ---------------------------------------------------------------------------------------------------- reading the XML


class CapError(ValueError):
    """The document is not a CAP alert this module can read."""


@dataclass(frozen=True)
class CapArea:
    description: Optional[str]
    rings: tuple[Ring, ...] = ()
    circles: tuple[Circle, ...] = ()
    geocodes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CapInfo:
    language: Optional[str]
    category: Optional[str]
    event: Optional[str]
    urgency: Optional[str]
    severity: Optional[str]
    certainty: Optional[str]
    effective_utc: Optional[int]
    onset_utc: Optional[int]
    expires_utc: Optional[int]
    text: VigilanceText
    areas: tuple[CapArea, ...] = ()
    skipped_areas: int = 0  # areas beyond MAX_AREAS_PER_INFO


@dataclass(frozen=True)
class CapAlert:
    """One CAP document."""

    identifier: str
    sender: str
    sent_utc: Optional[int]
    status: str
    message_type: Optional[str]
    scope: str
    references: tuple[tuple[str, str], ...]  # (sender, identifier) of the alerts this one replaces or cancels
    infos: tuple[CapInfo, ...] = field(default_factory=tuple)
    link: Optional[str] = None  # where the document was read


def local(tag: str) -> str:
    """The name of an element without its namespace."""
    return tag.rsplit("}", 1)[-1]


def child_text(element: ET.Element, name: str) -> Optional[str]:
    """The stripped text of the first child ``name`` (any namespace), ``None`` when missing or empty."""
    for child in element:
        if local(child.tag) == name:
            value = (child.text or "").strip()
            return value or None
    return None


def children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if local(child.tag) == name]


def parse_xml(content: bytes, what: str) -> ET.Element:
    """Parse an XML answer; a DTD or an entity declaration is refused (entity expansion is how an XML parser is attacked)."""
    if b"<!DOCTYPE" in content[:4096].upper() or b"<!ENTITY" in content.upper():
        raise CapError(f"{what} carries a DTD or an entity declaration")
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise CapError(f"{what} is not XML: {exc}") from exc


def instant(value: Optional[str]) -> Optional[int]:
    """A CAP date-time as UTC epoch seconds. A Buddhist-era year (Thailand: 2568) is read as Gregorian; ``None`` if it is no date."""
    if not value:
        return None
    match = re.match(r"^\s*(\d{4})(\D.*)$", value)
    if match and int(match.group(1)) >= BUDDHIST_ERA_YEAR:
        value = f"{int(match.group(1)) - BUDDHIST_ERA_OFFSET:04d}{match.group(2)}"
    return parse_iso_utc(value)


def parse_references(text: Optional[str]) -> tuple[tuple[str, str], ...]:
    """``sender,identifier,sent sender,identifier,sent ...`` -> ``((sender, identifier), ...)``; a malformed entry is skipped."""
    found: list[tuple[str, str]] = []
    for entry in (text or "").split():
        parts = entry.split(",")
        if len(parts) >= 3 and parts[0] and parts[1]:
            found.append((parts[0], ",".join(parts[1:-1])))  # an identifier may itself hold a comma
    return tuple(found)


def _parse_area(area: ET.Element) -> CapArea:
    rings = [ring for ring in (parse_cap_polygon(p.text) for p in children(area, "polygon")) if ring]
    circles = [circle for circle in (parse_cap_circle(c.text) for c in children(area, "circle")) if circle]
    geocodes = []
    for geocode in children(area, "geocode"):
        name, value = child_text(geocode, "valueName"), child_text(geocode, "value")
        if name and value:
            geocodes.append((name, value))
    return CapArea(child_text(area, "areaDesc"), tuple(rings), tuple(circles), tuple(geocodes))


def _parse_info(info: ET.Element) -> CapInfo:
    parameters = [
        VigilanceParameter(name=name, value=value)
        for parameter in children(info, "parameter")
        if (name := child_text(parameter, "valueName")) and (value := child_text(parameter, "value"))
    ]
    areas = children(info, "area")
    text = VigilanceText(
        language=child_text(info, "language"),
        sender_name=child_text(info, "senderName"),
        event=child_text(info, "event"),
        headline=child_text(info, "headline"),
        description=child_text(info, "description"),
        instruction=child_text(info, "instruction"),
        web=child_text(info, "web"),
        parameters=parameters,
    )
    return CapInfo(
        language=text.language,
        category=child_text(info, "category"),
        event=text.event,
        urgency=child_text(info, "urgency"),
        severity=child_text(info, "severity"),
        certainty=child_text(info, "certainty"),
        effective_utc=instant(child_text(info, "effective")),
        onset_utc=instant(child_text(info, "onset")),
        expires_utc=instant(child_text(info, "expires")),
        text=text,
        areas=tuple(_parse_area(area) for area in areas[:MAX_AREAS_PER_INFO]),
        skipped_areas=max(0, len(areas) - MAX_AREAS_PER_INFO),
    )


def parse_alert(content: bytes, link: Optional[str] = None) -> CapAlert:
    """One CAP document as an alert. Raises ``CapError`` when it is not a CAP alert (a feed, an error page, a broken file)."""
    root = parse_xml(content, "the document")
    if local(root.tag) != "alert" or not root.tag.startswith("{" + CAP_NAMESPACE_PREFIX):
        raise CapError(f"the document is not a CAP alert ({root.tag})")
    identifier = child_text(root, "identifier")
    if identifier is None:
        raise CapError("the CAP alert has no identifier")
    return CapAlert(
        identifier=identifier,
        sender=child_text(root, "sender") or "",
        sent_utc=instant(child_text(root, "sent")),
        status=(child_text(root, "status") or "").lower(),
        message_type=child_text(root, "msgType"),
        scope=(child_text(root, "scope") or "public").lower(),
        references=parse_references(child_text(root, "references")),
        infos=tuple(_parse_info(info) for info in children(root, "info")),
        link=link,
    )


# ---------------------------------------------------------------------------------------------------- from alerts to warnings


def is_warning(alert: CapAlert) -> bool:
    """A real, public alert that is not a cancellation: ``Actual`` status, ``Public`` scope, ``Alert`` or ``Update``."""
    return alert.status == "actual" and alert.scope == "public" and (alert.message_type or "").lower() in ("alert", "update")


def superseded(alerts: list[CapAlert]) -> set[tuple[str, str]]:
    """The ``(sender, identifier)`` that an ``Update`` or a ``Cancel`` among ``alerts`` replaces (only real alerts speak)."""
    gone: set[tuple[str, str]] = set()
    for alert in alerts:
        if alert.status == "actual" and (alert.message_type or "").lower() in ("update", "cancel"):
            gone.update(alert.references)
    return gone


def _area_key(info: CapInfo) -> tuple[object, ...]:
    """What the areas of an info are, whatever the language they are named in: their polygons and circles, else their codes.

    The name of a code system is written in the language of the info (Norway's NVE: ``KommuneId`` and ``MunicipalityId``), so
    only the values of the codes count.
    """
    shapes = tuple(sorted((tuple(ring[:3]), len(ring)) for area in info.areas for ring in area.rings))
    circles = tuple(sorted(area_circle for area in info.areas for area_circle in area.circles))
    if shapes or circles:
        return (shapes, circles)
    codes = tuple(sorted(value for area in info.areas for _, value in area.geocodes))
    return (codes,) if codes else (len(info.areas),)


def _structure(info: CapInfo) -> tuple[object, ...]:
    """What two infos of one alert have in common when they are the same warning in two languages."""
    return (info.severity, info.urgency, info.certainty, info.onset_utc, info.expires_utc, _area_key(info))


def group_translations(infos: tuple[CapInfo, ...]) -> list[list[CapInfo]]:
    """The infos of a document grouped by warning: the infos that differ by language only are one warning.

    An info joins the first group of the same structure that does not already have its language; else it starts a group
    (a document may carry two different events).
    """
    groups: list[list[CapInfo]] = []
    for info in infos:
        for group in groups:
            if _structure(group[0]) == _structure(info) and fold(info.language) not in {fold(other.language) for other in group}:
                group.append(info)
                break
        else:
            groups.append([info])
    return groups


def translation_key(alert: CapAlert, group: list[CapInfo]) -> tuple[object, ...]:
    """What identifies a warning across the documents of several language feeds: Russia and Kazakhstan publish one document per language."""
    return (alert.sent_utc, _structure(group[0]))


def is_in_force(alert: CapAlert, group: list[CapInfo], now: float) -> bool:
    """Whether a warning is still one: not over (urgency ``Past``), not called off (certainty ``Unlikely``), not expired.

    An alert with no expiry that was sent more than three days ago is not shown: nothing says it is still in force.
    """
    head = group[0]
    if fold(head.urgency) == "past" or fold(head.certainty) == "unlikely":
        return False
    if head.expires_utc is not None:
        return head.expires_utc >= now
    ref = head.onset_utc or head.effective_utc or alert.sent_utc
    return ref is None or ref >= now - MAX_ALERT_AGE_WITHOUT_END_S


def to_vigilances(
    alert: CapAlert,
    group: list[CapInfo],
    variant: int,
    *,
    source_id: str,
    country: str,
    credit: str,
    extra_infos: tuple[tuple[CapAlert, list[CapInfo]], ...] = (),
) -> list[Vigilance]:
    """The warnings (one per area) of one group of infos of an alert.

    ``extra_infos`` are the same warning read in another language feed (their texts are added, nothing else is taken from them).
    """
    head = group[0]
    level, level_original = derive_level(head.severity, head.text.parameters)
    infos = list(group) + [info for _, other in extra_infos for info in other]
    texts = [info.text for info in infos]
    phenomenon = derive_phenomenon([info.event for info in infos])
    issuer = next((text.sender_name for text in texts if text.sender_name), None)
    attribution = merge_credits(credit, f"Issued by {issuer}" if issuer else None) or credit
    message_type = alert.message_type
    status = STATUSES.get(message_type.lower()) if message_type else None
    derived = [name for name, value in (("status", status), ("level", level), ("phenomenon", phenomenon)) if value is not None]
    out: list[Vigilance] = []
    # An info with no area at all (the element is optional in CAP) is one warning with no place: listed, never matched to a point.
    for index, area in enumerate(head.areas or (CapArea(None),)):
        shape = shapes_to_shape(area.rings, area.circles)
        vigilance = Vigilance(
            id=f"swic:{source_id}:{alert.identifier}:{variant}.{index}",
            source="swic",
            country=country,
            attribution=attribution,
            identifier=alert.identifier,
            sent_utc=alert.sent_utc,
            effective_utc=head.effective_utc,
            onset_utc=head.onset_utc,
            expires_utc=head.expires_utc,
            message_type=message_type,
            status=status,
            level=level,
            level_original=level_original,
            phenomenon=phenomenon,
            phenomenon_original=head.event,
            event=head.event,
            issuer=issuer,
            texts=texts,
            area=VigilanceArea(
                description=area.description,
                geocodes=[VigilanceGeocode(scheme=name, value=value) for name, value in area.geocodes],
                has_polygon=bool(area.rings or area.circles),
            ),
            link=alert.link,
            web=head.text.web,
            derived=derived,
        )
        out.append(vigilance.with_shape(shape))
    return out
