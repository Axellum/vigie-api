"""DWD collector: the official warnings of the Deutscher Wetterdienst (Germany) as ``Vigilance`` objects.

The DWD publishes its warnings as CAP 1.2 documents on its open data server, without a key
(https://opendata.dwd.de/weather/alerts/cap/, described in the "Product overview DWD Alerts" and in the
"Documentation for the CAP DWD profile 2.1.14" linked from https://www.dwd.de/DE/leistungen/opendata/hilfe.html).
One request reads all of Germany:

- ``DISTRICT_DWD_STAT/Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMDWD_DISTRICT_MUL.zip`` is the **status** (the whole
  situation, every warning in force at the moment of the last production) by **district** (Landkreis: the polygon of
  every area is the geometry of the district, with its holes), in the **multilingual** variant (one document per
  warning, one ``info`` per language: German, English, French, Spanish, Arabic, Russian, Turkish, Polish).
  ``LATEST`` is a name that always points to the newest production. A cancelled warning is simply no longer there
  (cancellations and all-clear updates are only in the ``DIFFERENCE`` products, which are not read).
- The server sends ``ETag`` and ``Last-Modified``; a conditional request is answered ``304`` (checked on 2026-10-20),
  so asking again costs a few hundred bytes. The DWD says it produces the status up to once a minute, and at least
  every ten minutes even when nothing changed; it promises no real-time delivery.

One CAP document holds the same warning for several areas; the model is one warning per area, so a document gives as
many ``Vigilance`` objects as it has areas. The words of the issuer are kept untouched in every language. Nothing here
guesses: a ``severity`` or an event number that is not in the tables gives ``None``.
"""

from __future__ import annotations

import asyncio
import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from shapely.geometry.base import BaseGeometry

from app.attribution import credit_text
from app.collectors.base import BaseCollector, CacheEntry, Collected, Fetched, http_date_to_epoch, parse_iso_utc
from app.geo import finalize, parse_cap_polygon, rings_to_shape
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

STATUS_URL = (
    "https://opendata.dwd.de/weather/alerts/cap/DISTRICT_DWD_STAT/"
    "Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMDWD_DISTRICT_MUL.zip"
)

# A status of a stormy day is some hundred documents of some ten kilobytes each; these limits only stop a mistake
# (or an archive that lies about its size) from using all the memory.
MAX_MEMBERS = 5_000
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024

# CAP ``severity`` -> our level. The DWD profile (2.1.14, section 2.2.2.6) names the four levels: Minor is the
# "Wetterwarnung" (yellow), Moderate the "markante Wetterwarnung" (orange), Severe the "Unwetterwarnung" (red) and
# Extreme the "extreme Unwetterwarnung" (violet). Our scale stops at red: Extreme is RED here, and the original word
# stays in ``level_original``. NB: unlike the NWS, Minor is NOT the lowest "green" level here, it is a yellow warning.
SEVERITIES: dict[str, SeverityLevel] = {
    "minor": SeverityLevel.YELLOW,
    "moderate": SeverityLevel.ORANGE,
    "severe": SeverityLevel.RED,
    "extreme": SeverityLevel.RED,
}

# DWD event number (``eventCode`` ``II``, profile section 3.1) -> our phenomenon. The profile says an ``ii`` is not
# necessarily a number and that the DWD may change the encoding: an unknown one gives ``None``.
_PHENOMENA_BY_II: dict[VigilancePhenomenon, tuple[str, ...]] = {
    # 11-13 coastal gusts, wind and storm; 14-16 open sea; 51-56 gusts; 57 strong wind; 58 storm
    VigilancePhenomenon.WIND: ("11", "12", "13", "14", "15", "16", "51", "52", "53", "54", "55", "56", "57", "58"),
    # 31-49 thunderstorms (some with gusts, rain, hail), 90-96 the same family
    VigilancePhenomenon.THUNDERSTORM: (
        "31", "33", "34", "36", "38", "40", "41", "42", "44", "45", "46", "48", "49", "90", "91", "92", "93", "95", "96",
    ),
    VigilancePhenomenon.RAIN: ("61", "62", "63", "64", "65", "66"),  # heavy and persistent rain
    # snowfall (70-73), snow drifts (74-76), slippery roads and black ice (84-87)
    VigilancePhenomenon.SNOW_ICE: ("70", "71", "72", "73", "74", "75", "76", "84", "85", "86", "87"),
    VigilancePhenomenon.COLD: ("22", "82", "83"),  # frost
    VigilancePhenomenon.HEAT: ("247", "248"),  # strong and extreme heat
    VigilancePhenomenon.FOG: ("59",),
    # 79 power-line vibrations, 88-89 thaw, 246 UV index: no group of ours
    VigilancePhenomenon.OTHER: ("79", "88", "89", "246"),
}
PHENOMENA: dict[str, VigilancePhenomenon] = {ii: group for group, numbers in _PHENOMENA_BY_II.items() for ii in numbers}

STATUSES: dict[str, VigilanceStatus] = {
    "alert": VigilanceStatus.NEW,
    "update": VigilanceStatus.UPDATE,
    "cancel": VigilanceStatus.CANCEL,
}

_ID_PREFIX = "id:"


@dataclass(frozen=True)
class DWDStatus:
    """The warnings in force in Germany as the status lists them (per area), and when the DWD produced the file."""

    updated_utc: Optional[int]
    vigilances: tuple[Vigilance, ...]
    skipped: int = 0  # documents that were not a real warning (a test, an all-clear) or could not be read


def derive_level(severity: Optional[str]) -> Optional[SeverityLevel]:
    """Our level for a CAP ``severity`` of the DWD (``None`` if it is not one of the four)."""
    return SEVERITIES.get(severity.strip().lower()) if severity else None


def derive_phenomenon(ii: Optional[str]) -> Optional[VigilancePhenomenon]:
    """Our phenomenon for a DWD event number (``None`` if the number is not in the table)."""
    return PHENOMENA.get(ii.strip()) if ii else None


def _text(element: ET.Element, tag: str) -> Optional[str]:
    """The stripped text of a child, ``None`` when missing or empty."""
    value = element.findtext(tag)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _reject_dtd(content: bytes) -> None:
    """A CAP document never has a DTD: refuse one (entity expansion is how an XML parser is attacked)."""
    if b"<!DOCTYPE" in content[:4096].upper() or b"<!ENTITY" in content.upper():
        raise ValueError("DWD document carries a DTD or an entity declaration")


def _named_values(parent: ET.Element, tag: str) -> list[tuple[str, str]]:
    """The ``(valueName, value)`` pairs of the children ``tag`` (``eventCode``, ``parameter``, ``geocode``), as given."""
    pairs: list[tuple[str, str]] = []
    for item in parent.findall(CAP + tag):
        name, value = _text(item, CAP + "valueName"), item.findtext(CAP + "value")
        if name and value is not None and value.strip():
            pairs.append((name, value.strip()))
    return pairs


def _area_shape(area: ET.Element) -> tuple[Optional[BaseGeometry], int]:
    """The polygons of an area minus its holes (profile 2.2.3.3: every ``EXCLUDE_POLYGON`` is cut out) and how many there are."""
    rings = [ring for ring in (parse_cap_polygon(p.text) for p in area.findall(CAP + "polygon")) if ring]
    shape = rings_to_shape(rings)
    if shape is None:
        return None, 0
    holes = [
        ring
        for name, value in _named_values(area, "geocode")
        if name == "EXCLUDE_POLYGON" and (ring := parse_cap_polygon(value))
    ]
    hole_shape = rings_to_shape(holes)
    if hole_shape is not None:
        cut = finalize([shape.difference(hole_shape)])
        shape = cut if cut is not None else shape
    return shape, len(rings)


def _info_text(info: ET.Element) -> VigilanceText:
    """The words of the issuer in one language, untouched; the event codes of the profile ride along as parameters."""
    parameters = [VigilanceParameter(name=f"eventCode:{name}", value=value) for name, value in _named_values(info, "eventCode")]
    parameters += [VigilanceParameter(name=name, value=value) for name, value in _named_values(info, "parameter")]
    return VigilanceText(
        language=_text(info, CAP + "language"),
        sender_name=_text(info, CAP + "senderName"),
        event=_text(info, CAP + "event"),
        headline=_text(info, CAP + "headline"),
        description=_text(info, CAP + "description"),
        instruction=_text(info, CAP + "instruction"),
        web=_text(info, CAP + "web"),
        parameters=parameters,
    )


def _reference_info(infos: list[ET.Element]) -> ET.Element:
    """The ``info`` the areas are read from: the German one (the original text) when there is one, else the first."""
    for info in infos:
        if (_text(info, CAP + "language") or "").lower().startswith("de"):
            return info
    return infos[0]


def parse_alert(root: ET.Element) -> list[Vigilance]:
    """The warnings (one per area) of one CAP document; ``[]`` when it is not a real warning (a test, an all-clear)."""
    if root.tag != CAP + "alert":
        raise ValueError(f"DWD document is not a CAP alert ({root.tag})")
    if (_text(root, CAP + "status") or "").lower() != "actual":
        return []
    identifier = _text(root, CAP + "identifier")
    infos = root.findall(CAP + "info")
    if identifier is None or not infos:
        return []
    message_type = _text(root, CAP + "msgType")
    if any((_text(info, CAP + "responseType") or "").lower() == "allclear" for info in infos):
        return []  # an update that ends the warning: it is over, the status does not list it any more
    handling = [c.text.strip() for c in root.findall(CAP + "code") if c.text]
    code = next((value[len(_ID_PREFIX) :] for value in handling if value.startswith(_ID_PREFIX)), None)

    texts = [_info_text(info) for info in infos]
    reference = _reference_info(infos)
    reference_text = texts[infos.index(reference)]
    event_codes = dict(_named_values(reference, "eventCode"))
    licence = event_codes.get("LICENSE")  # "© GeoBasis-DE / BKG 2021 (Daten modifiziert)": the credit of the area shapes
    issuer = reference_text.sender_name
    severity = _text(reference, CAP + "severity")

    credit = credit_text("dwd_warnings")
    attribution = " | ".join(part for part in (credit, licence, f"Issued by {issuer}" if issuer else None) if part)
    document = code or identifier

    vigilances: list[Vigilance] = []
    for index, area in enumerate(reference.findall(CAP + "area")):
        geocodes = [VigilanceGeocode(scheme=name, value=value) for name, value in _named_values(area, "geocode") if name != "EXCLUDE_POLYGON"]
        shape, polygons = _area_shape(area)
        cell = next((g.value for g in geocodes if g.scheme == "WARNCELLID"), str(index))
        vigilance = Vigilance(
            id=f"dwd:{document}:{cell}",
            source="dwd",
            country="DE",
            attribution=attribution,
            identifier=identifier,
            sent_utc=parse_iso_utc(_text(root, CAP + "sent")),
            effective_utc=parse_iso_utc(_text(reference, CAP + "effective")),
            onset_utc=parse_iso_utc(_text(reference, CAP + "onset")),
            expires_utc=parse_iso_utc(_text(reference, CAP + "expires")),
            message_type=message_type,
            status=STATUSES.get(message_type.lower()) if message_type else None,
            level=derive_level(severity),
            level_original=severity,
            phenomenon=derive_phenomenon(event_codes.get("II")),
            phenomenon_original=reference_text.event,
            event=reference_text.event,
            issuer=issuer,
            texts=texts,
            area=VigilanceArea(
                description=_text(area, CAP + "areaDesc"),
                geocodes=geocodes,
                has_polygon=polygons > 0,
            ),
            web=reference_text.web,
        )
        vigilances.append(vigilance.with_shape(shape))
    return vigilances


def parse_status(content: bytes, updated_utc: Optional[int] = None) -> DWDStatus:
    """Parse the ZIP of a status: one CAP document per warning. An empty ZIP is a valid answer (nothing in force).

    A ZIP that is not one, or whose documents all fail, is an error (the caller falls back on its last answer).
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"DWD answer is not a ZIP archive: {exc}") from exc
    members = [info for info in archive.infolist() if not info.is_dir()]
    if len(members) > MAX_MEMBERS or sum(info.file_size for info in members) > MAX_TOTAL_BYTES:
        raise ValueError("DWD archive is larger than any status ever was")
    vigilances: list[Vigilance] = []
    skipped = 0
    broken = 0
    for info in members:
        if not info.filename.lower().endswith(".xml") or info.file_size > MAX_MEMBER_BYTES:
            skipped += 1
            continue
        try:
            with archive.open(info) as handle:
                data = handle.read(MAX_MEMBER_BYTES + 1)
            if len(data) > MAX_MEMBER_BYTES:
                raise ValueError("document larger than its declared size")
            _reject_dtd(data)
            parsed = parse_alert(ET.fromstring(data))
        except (ValueError, TypeError, KeyError, AttributeError, ET.ParseError, zipfile.BadZipFile):
            broken += 1
            continue
        if parsed:
            vigilances.extend(parsed)
        else:
            skipped += 1
    if members and not vigilances and broken:
        raise ValueError(f"DWD status: {broken} documents, none could be read")
    return DWDStatus(updated_utc=updated_utc, vigilances=tuple(vigilances), skipped=skipped + broken)


class DWDCollector(BaseCollector[DWDStatus]):
    """The DWD status of Germany: one conditional request per refresh, cached 120 seconds."""

    # 120 s: the DWD produces the status up to once a minute and at least every ten minutes (implementation notes, 2.2).
    # It publishes no delay or cache lifetime for redistributors (not verified): two minutes keeps what is served
    # within two productions, and a refresh that finds nothing new is a ``304`` with no body.
    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="dwd_cap",
            name="DWD CAP warnings (Deutscher Wetterdienst)",
            domain="warnings",
            ttl_seconds=120,
            http_client=http_client,
        )

    async def fetch_status(self, allow_stale_on_error: bool = True) -> Fetched[DWDStatus]:
        """The status of Germany, from the cache while it is fresh, else one (conditional) request."""
        return await self.fetch_ex(cache_key="status", allow_stale_on_error=allow_stale_on_error)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[DWDStatus]:
        previous: Optional[CacheEntry[DWDStatus]] = kwargs.get("previous")
        headers = {"Accept": "application/zip, */*;q=0.5"}
        if previous is not None and previous.validators:
            if previous.validators.get("ETag"):
                headers["If-None-Match"] = previous.validators["ETag"]
            if previous.validators.get("Last-Modified"):
                headers["If-Modified-Since"] = previous.validators["Last-Modified"]
        resp = await self._get(client, STATUS_URL, headers=headers)
        if resp.status_code == 304:
            if previous is None:
                raise ValueError("DWD answered 304 Not Modified to a request that was not conditional")
            return Collected(previous.value, validators=previous.validators)
        resp.raise_for_status()
        modified = http_date_to_epoch(resp.headers.get("Last-Modified"))
        status = await asyncio.to_thread(parse_status, resp.content, None if modified is None else int(modified))
        validators = {name: resp.headers[name] for name in ("ETag", "Last-Modified") if resp.headers.get(name)}
        return Collected(status, validators=validators or None)

