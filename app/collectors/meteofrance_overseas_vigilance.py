"""Météo-France official weather warnings of the French overseas territories: the other entry points of ``DPVigilance/v1`` (ADR 0036).

``/cartevigilance/encours`` (``meteofrance.py``) holds the metropolitan departments only. The overseas territories are published apart,
in three feeds read on 2026-11-21 with the project's key (Licence Ouverte 2.0, as the metropolitan map):

- ``/vigilanceom/flux/dernier``: **a ZIP** (about 1 MB, mostly PDF bulletins) holding, one file per territory, the JSON maps of the
  Antilles and Guyane (``CDPV85_TFFR_.txt`` Guadeloupe, ``CDPV85_TFFF_.txt`` Martinique, ``CDPV85_TFFJ_.txt`` Saint-Martin and
  Saint-Barthélemy, ``CDPV85_SOCA_.txt`` Guyane), of La Réunion (``CDPV95_FMEE_.txt``) and Mayotte (``CDPV84_FMEE_.txt``), the
  forecasters' bulletins of La Réunion and Mayotte (``CDPV96_FMEE_.txt``, ``CDPV87_FMEE_.txt``) and the level of Saint-Pierre-et-Miquelon
  (``NXFR33_LFVP_.xml``).
- ``/polynesie/cartevigilance/encours`` and ``/ncaledonie/cartevigilance/encours``: JSON maps of French Polynesia and of New Caledonia.

All the maps share the shape of the metropolitan one (``timelaps.domain_ids`` of ``phenomenon_items`` with a ``color_id`` and slots) with
their own ids. A territory has **one domain of its own** (``VIGI971``: the highest level of each phenomenon over the territory) and
sub-zones (``VIGI971-01``, ``-51`` ...): this module serves one warning per territory, phenomenon and level, from the territory's own
domain, and names the sub-zones that carry the level in the ``zones`` parameter. The shapes of the sub-zones are not published, so a
point is matched to the territory (the shape of its code ``MF_OM`` is the outline of the territory, ``app/data/geocodes-meteofrance-om``).

What the ids mean is **not documented by Météo-France in anything read here**; the tables below follow the open-source integration
of the Home Assistant community (``hacf-fr/meteo_france_vigilance``, reverse-engineered) and the titles of the bulletins of La Réunion
and Mayotte, and are marked ``not_verified`` in ADR 0036. A colour ``0`` or ``-1`` is "no level" (never a warning); ``1`` is green;
``2`` to ``4`` are yellow, orange and red as in the metropolitan map; the higher ids the sources describe as the phases of a cyclone
(purple, grey, pre-alert...) are served with ``level`` null and the id in ``level_original``, never mapped to a level by guess.

Requires ``VIGIE_METEOFRANCE_API_KEY`` (the same key and header as ``meteofrance.py``).
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.attribution import credit_text
from app.collectors.base import BaseCollector
from app.collectors.meteofrance import COLORS, ISSUER, WEB_URL, MeteoFranceVigilanceStatus, _iso_to_utc
from app.models.vigilance import (
    Vigilance,
    VigilanceArea,
    VigilanceGeocode,
    VigilanceParameter,
    VigilancePhenomenon,
    VigilanceStatus,
    VigilanceText,
)

BASE_URL = "https://public-api.meteofrance.fr/public/DPVigilance/v1"
FEED_URLS: dict[str, str] = {
    "zip": f"{BASE_URL}/vigilanceom/flux/dernier",
    "polynesia": f"{BASE_URL}/polynesie/cartevigilance/encours",
    "new-caledonia": f"{BASE_URL}/ncaledonie/cartevigilance/encours",
}
TOGGLE_ENV = "VIGIE_METEOFRANCE_VIGILANCE_OVERSEAS"
TTL_SECONDS = (
    300  # the ZIP is about 1 MB: not asked more than every 5 minutes, and only for a country somebody asks for
)
MAX_ZIP_BYTES = 8_000_000
MAX_MEMBER_BYTES = 2_000_000
MAX_JSON_BYTES = 2_000_000
GEOCODE_SCHEME = "MF_OM"
MAP_FILE = re.compile(r"^CDPV(?:84|85|95)_[A-Z]{4}_\.txt$")
TEXT_FILE = re.compile(r"^CDPV(?:87|96)_[A-Z]{4}_\.txt$")
SPM_FILE = re.compile(r"^NXFR33_[A-Z]{4}_\.xml$")
SPM_LEVEL = re.compile(rb'<DV\b[^>]*\bdep="975"[^>]*\bcoul="(\d+)"')
SPM_STAMP = re.compile(rb'<EV\b[^>]*\bdateinsert="(\d{14})"')

# Phenomenon id -> (French label as published or as the bulletins title it, derived phenomenon), by table.
PHENOMENA: dict[str, dict[str, tuple[str, VigilancePhenomenon]]] = {
    "antilles": {
        "1": ("Vent", VigilancePhenomenon.WIND),
        "2": ("Pluie-orages", VigilancePhenomenon.RAIN),
        "9": ("Vagues-submersion", VigilancePhenomenon.COASTAL),
        "10": ("Cyclone", VigilancePhenomenon.WIND),
    },
    "indian-ocean": {
        "1": ("Vent", VigilancePhenomenon.WIND),
        "12": ("Pluie-orages", VigilancePhenomenon.RAIN),
        "9": ("Vagues-submersion", VigilancePhenomenon.COASTAL),
        "10": ("Cyclone", VigilancePhenomenon.WIND),
    },
    # The ids of French Polynesia are those of the metropolitan table (1 wind, 2 rain, 3 thunderstorms, 9 waves): not verified.
    "polynesia": {
        "1": ("Vent", VigilancePhenomenon.WIND),
        "2": ("Pluie-inondation", VigilancePhenomenon.RAIN),
        "3": ("Orages", VigilancePhenomenon.THUNDERSTORM),
        "9": ("Vagues-submersion", VigilancePhenomenon.COASTAL),
    },
    "other": {},
}
# The titles of the bulletins of La Réunion and Mayotte -> the phenomenon id they speak about.
BULLETIN_TITLES = {"vagues-submersion": "9", "vents forts": "1", "fortes pluies/orages": "12"}
NO_WARNING_TEXT = "pas de vigilance particulière"


@dataclass(frozen=True)
class Territory:
    """A territory with its own vigilance: where it is read, what the API calls it, and how its ids are read."""

    domain: str  # the territory's own domain in the feeds
    code: str  # the value of its ``MF_OM`` geocode
    country: str  # ISO 3166-1 alpha-2 (the API's country)
    name: str
    feed: str
    table: str


TERRITORIES: tuple[Territory, ...] = (
    Territory("VIGI971", "971", "GP", "Guadeloupe", "zip", "antilles"),
    Territory("VIGI972", "972", "MQ", "Martinique", "zip", "antilles"),
    Territory("VIGI973", "973", "GF", "Guyane", "zip", "antilles"),
    Territory("VIGI974", "974", "RE", "La Réunion", "zip", "indian-ocean"),
    Territory("VIGI976", "976", "YT", "Mayotte", "zip", "indian-ocean"),
    # One domain for the two collectivities: each gets its own warning (and its own shape).
    Territory("VIGI978-977", "978", "MF", "Saint-Martin", "zip", "antilles"),
    Territory("VIGI978-977", "977", "BL", "Saint-Barthélemy", "zip", "antilles"),
    Territory("975", "975", "PM", "Saint-Pierre-et-Miquelon", "zip", "other"),
    Territory("VIGI987", "987", "PF", "Polynésie française", "polynesia", "polynesia"),
    Territory("VIGI988", "988", "NC", "Nouvelle-Calédonie", "new-caledonia", "indian-ocean"),
)
COUNTRY_FEEDS: dict[str, str] = {territory.country: territory.feed for territory in TERRITORIES}
COUNTRIES: tuple[str, ...] = tuple(sorted(COUNTRY_FEEDS))
NAME_BY_CODE = {territory.code: territory.name for territory in TERRITORIES}


def overseas_vigilance_enabled_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the overseas warnings are read: on (with the Météo-France key) unless ``VIGIE_METEOFRANCE_VIGILANCE_OVERSEAS`` is ``0`` / ``false`` / ``no`` / ``off``."""
    raw = (env if env is not None else os.environ).get(TOGGLE_ENV, "1")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def color_of(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def label_and_level(color: int) -> Optional[tuple[str, Optional[Any]]]:
    """``(label, level)`` of a colour that is a warning: yellow, orange, red; any higher id is a warning without a derived level."""
    if color in COLORS:
        label, level = COLORS[color]
        return label, level
    if color >= 5:
        return f"Couleur {color}", None
    return None  # 1 green, 0 and -1 no level


def _timestamp(value: Any) -> Optional[int]:
    return _iso_to_utc(value)


def _domains(payload: Any) -> dict[str, Mapping[str, Any]]:
    """The domains of a map, by id; ``ValueError`` when the answer is not a map of the shape of the metropolitan one."""
    if not isinstance(payload, Mapping):
        raise ValueError("Météo-France overseas vigilance map is not an object")
    timelaps = payload.get("timelaps")
    items = timelaps.get("domain_ids") if isinstance(timelaps, Mapping) else None
    if not isinstance(items, list):
        raise ValueError("Météo-France overseas vigilance map has no `timelaps.domain_ids` list")
    return {
        str(item["domain_id"]).strip(): item for item in items if isinstance(item, Mapping) and item.get("domain_id")
    }


def _bulletin(payload: Any) -> dict[str, str]:
    """``{phenomenon id: text}`` of the follow-up bulletin of La Réunion or Mayotte (``CDPV96`` / ``CDPV87``), keeping the words intact."""
    out: dict[str, str] = {}
    if not isinstance(payload, Mapping):
        return out
    for bloc in payload.get("text_bloc_items") or []:
        for item in bloc.get("text_items") or [] if isinstance(bloc, Mapping) else []:
            if not isinstance(item, Mapping):
                continue
            phenomenon = BULLETIN_TITLES.get(str(item.get("title") or "").strip().lower())
            lines = [str(line).strip() for line in item.get("text") or [] if str(line).strip()]
            if phenomenon and lines:
                out[phenomenon] = "\n".join(lines)
    return out


def _comment(payload: Any) -> Optional[tuple[str, str]]:
    """The commentary of a map (``text_items`` of the New Caledonia map): ``(title, text)``."""
    items = payload.get("text_items") if isinstance(payload, Mapping) else None
    if isinstance(items, Mapping):
        items = [items]
    lines: list[str] = []
    title = ""
    for item in items if isinstance(items, list) else []:
        if isinstance(item, Mapping):
            title = title or str(item.get("title") or "").strip()
            lines.extend(str(line).strip() for line in item.get("text") or [] if str(line).strip())
    return (title, "\n".join(lines)) if lines else None


def territory_vigilances(
    territory: Territory,
    payload: Any,
    bulletin: Optional[Mapping[str, str]] = None,
) -> tuple[Optional[int], list[Vigilance]]:
    """``(update time, warnings)`` of one territory from a map; a map that does not hold the territory's domain gives no warning."""
    domains = _domains(payload)
    update_raw = str(payload.get("update_time") or "").strip() or None
    updated_utc = _timestamp(update_raw)
    own = domains.get(territory.domain)
    if own is None:
        return updated_utc, []
    comment = _comment(payload)
    base_credit = credit_text("meteofrance_vigilance")
    attribution = f"{base_credit}. Last update: {update_raw}" if update_raw else base_credit
    table = PHENOMENA[territory.table]
    found: list[Vigilance] = []
    for item in own.get("phenomenon_items") or []:
        if not isinstance(item, Mapping):
            continue
        color = color_of(item.get("phenomenon_max_color_id"))
        described = label_and_level(color) if color is not None else None
        if described is None:
            continue
        color_label, level = described
        phen_id = str(item.get("phenomenon_id") or "").strip()
        phen_label, phenomenon = table.get(phen_id, (f"Phénomène {phen_id}", VigilancePhenomenon.OTHER))
        starts: list[int] = []
        ends: list[int] = []
        slots: list[str] = []
        for slot in item.get("timelaps_items") or []:
            slot_color = color_of(slot.get("color_id")) if isinstance(slot, Mapping) else None
            slot_described = label_and_level(slot_color) if slot_color is not None else None
            if slot_described is None:
                continue
            begin, end = _timestamp(slot.get("begin_time")), _timestamp(slot.get("end_time"))
            if begin is not None:
                starts.append(begin)
            if end is not None:
                ends.append(end)
            slots.append(f"{slot.get('begin_time') or ''}/{slot.get('end_time') or ''}:{slot_described[0]}")
        zones = sorted(
            domain_id
            for domain_id, domain in domains.items()
            if domain_id.startswith(territory.domain + "-")
            and any(
                isinstance(other, Mapping)
                and str(other.get("phenomenon_id") or "").strip() == phen_id
                and (color_of(other.get("phenomenon_max_color_id")) or 0) >= 2
                for other in domain.get("phenomenon_items") or []
            )
        )
        event_text = f"Vigilance {color_label.lower()} {phen_label.lower()}"
        parameters = [
            VigilanceParameter(name="phenomenon_id", value=phen_id),
            VigilanceParameter(name="color_id", value=str(color)),
        ]
        if slots:
            parameters.append(VigilanceParameter(name="timelaps", value=", ".join(slots)))
        if zones:
            parameters.append(VigilanceParameter(name="zones", value=", ".join(zones)))
        description = (bulletin or {}).get(phen_id)
        headline = f"{territory.name} : {event_text}"
        if description is None and comment is not None:
            headline, description = comment[0] or headline, comment[1]
        stamp = update_raw or str(updated_utc or "current")
        found.append(
            Vigilance(
                id=f"meteofrance:{territory.code}:{phen_id}:{stamp}",
                source="meteofrance",
                country=territory.country,
                attribution=attribution,
                identifier=f"MF-VIGILANCE-{territory.code}-{phen_id}-{stamp}",
                sent_utc=updated_utc,
                effective_utc=updated_utc,
                onset_utc=min(starts) if starts else updated_utc,
                expires_utc=max(ends) if ends else _timestamp(payload.get("end_validity_time")),
                message_type="Alert",
                status=VigilanceStatus.NEW,
                level=level,
                level_original=color_label,
                phenomenon=phenomenon,
                phenomenon_original=phen_label,
                event=event_text,
                issuer=ISSUER,
                texts=[
                    VigilanceText(
                        language="fr-FR",
                        sender_name=ISSUER,
                        event=event_text,
                        headline=headline,
                        description=description,
                        web=WEB_URL,
                        parameters=parameters,
                    )
                ],
                area=VigilanceArea(
                    description=territory.name,
                    geocodes=[VigilanceGeocode(scheme=GEOCODE_SCHEME, value=territory.code)],
                ),
                web=WEB_URL,
            )
        )
    return updated_utc, found


def parse_map_feed(territories: tuple[Territory, ...], payload: Any) -> MeteoFranceVigilanceStatus:
    """The warnings of the territories of one JSON map (French Polynesia, New Caledonia)."""
    updated: list[int] = []
    found: list[Vigilance] = []
    for territory in territories:
        when, vigilances = territory_vigilances(territory, payload)
        if when is not None:
            updated.append(when)
        found.extend(vigilances)
    return MeteoFranceVigilanceStatus(updated_utc=max(updated) if updated else None, vigilances=tuple(found))


def _spm_vigilances(content: bytes) -> tuple[Optional[int], list[Vigilance]]:
    """Saint-Pierre-et-Miquelon: the file gives a level for the archipelago and no phenomenon (``coul`` is read on the metropolitan scale:
    not verified). A warning is served only when the level is above green; its words (``WXFP01``) are not attached: the bulletin that
    comes with the file was found dated a month before the level, and nothing says it is the current one."""
    level_match, stamp_match = SPM_LEVEL.search(content), SPM_STAMP.search(content)
    updated = None
    if stamp_match is not None:
        try:  # read as UTC: not verified (the file does not say)
            updated = int(
                datetime.strptime(stamp_match.group(1).decode(), "%Y%m%d%H%M%S")
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
        except ValueError:
            updated = None
    if level_match is None:
        raise ValueError("Saint-Pierre-et-Miquelon level not found in the file")
    color = int(level_match.group(1))
    described = label_and_level(color)
    if described is None:
        return updated, []
    color_label, level = described
    territory = next(t for t in TERRITORIES if t.domain == "975")
    event_text = f"Vigilance {color_label.lower()}"
    stamp = stamp_match.group(1).decode() if stamp_match else "current"
    return updated, [
        Vigilance(
            id=f"meteofrance:{territory.code}:all:{stamp}",
            source="meteofrance",
            country=territory.country,
            attribution=credit_text("meteofrance_vigilance"),
            identifier=f"MF-VIGILANCE-{territory.code}-{stamp}",
            sent_utc=updated,
            effective_utc=updated,
            onset_utc=updated,
            message_type="Alert",
            status=VigilanceStatus.NEW,
            level=level,
            level_original=color_label,
            phenomenon=None,
            phenomenon_original=None,
            event=event_text,
            issuer=ISSUER,
            texts=[
                VigilanceText(
                    language="fr-FR",
                    sender_name=ISSUER,
                    event=event_text,
                    headline=f"{territory.name} : {event_text}",
                    web=WEB_URL,
                    parameters=[VigilanceParameter(name="color_id", value=str(color))],
                )
            ],
            area=VigilanceArea(
                description=territory.name, geocodes=[VigilanceGeocode(scheme=GEOCODE_SCHEME, value=territory.code)]
            ),
            web=WEB_URL,
        )
    ]


def _json_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> Any:
    if info.file_size > MAX_MEMBER_BYTES:
        raise ValueError(f"{info.filename} is too large")
    return json.loads(archive.read(info).decode("utf-8"))


def parse_zip(content: bytes) -> MeteoFranceVigilanceStatus:
    """The warnings of the ZIP of ``/vigilanceom/flux/dernier``. A file that cannot be read leaves its territory out (it is
    counted: when no map of the archive can be read at all, ``ValueError``)."""
    if len(content) > MAX_ZIP_BYTES:
        raise ValueError("Météo-France overseas vigilance archive is too large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("Météo-France overseas vigilance answer is not a ZIP archive") from exc
    maps: list[Any] = []
    bulletins: dict[str, str] = {}
    spm: Optional[bytes] = None
    for info in archive.infolist():
        name = info.filename.rsplit("/", 1)[-1]
        try:
            if MAP_FILE.match(name):
                maps.append(_json_member(archive, info))
            elif TEXT_FILE.match(name):
                payload = _json_member(archive, info)
                domain = str(payload.get("domain_id") or "") if isinstance(payload, Mapping) else ""
                for phenomenon, text in _bulletin(payload).items():
                    bulletins[f"{domain}:{phenomenon}"] = text
            elif SPM_FILE.match(name) and info.file_size <= 64_000:
                spm = archive.read(info)
        except (ValueError, KeyError):
            continue  # a file of the archive that cannot be read leaves its territory out; the others stay
    readable = 0
    updated: list[int] = []
    found: list[Vigilance] = []
    for payload in maps:
        try:
            domains = _domains(payload)
        except ValueError:
            continue
        readable += 1
        for territory in TERRITORIES:
            if territory.feed != "zip" or territory.domain not in domains:
                continue
            own_bulletin = {
                key.split(":", 1)[1]: text for key, text in bulletins.items() if key.startswith(territory.domain + ":")
            }
            when, vigilances = territory_vigilances(territory, payload, own_bulletin)
            if when is not None:
                updated.append(when)
            found.extend(vigilances)
    if spm is not None:
        try:
            when, vigilances = _spm_vigilances(spm)
            readable += 1
            if when is not None:
                updated.append(when)
            found.extend(vigilances)
        except ValueError:
            pass
    if readable == 0:
        raise ValueError("Météo-France overseas vigilance archive holds no readable map")
    return MeteoFranceVigilanceStatus(updated_utc=max(updated) if updated else None, vigilances=tuple(found))


def parse_feed(feed: str, content: bytes) -> MeteoFranceVigilanceStatus:
    """The warnings of one feed (``zip``, ``polynesia``, ``new-caledonia``)."""
    if feed == "zip":
        return parse_zip(content)
    if len(content) > MAX_JSON_BYTES:
        raise ValueError("Météo-France overseas vigilance map is too large")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Météo-France overseas vigilance map is not JSON") from exc
    return parse_map_feed(tuple(t for t in TERRITORIES if t.feed == feed), payload)


class MeteoFranceOverseasVigilanceCollector(BaseCollector[MeteoFranceVigilanceStatus]):
    """Official Météo-France warnings of the overseas territories, one cached read per feed (``zip``, ``polynesia``, ``new-caledonia``)."""

    def __init__(self, api_key: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="meteofrance_vigilance_overseas",
            name="Météo-France Vigilance outre-mer (DPVigilance v1)",
            domain="vigilance",
            ttl_seconds=TTL_SECONDS,
            timeout_seconds=20.0,
            http_client=http_client,
        )
        self._key = api_key
        self.secrets = (api_key,)

    async def fetch_feed(self, feed: str):  # type: ignore[no-untyped-def]
        """The warnings of one feed (cached ``TTL_SECONDS``); ``feed`` is ``zip``, ``polynesia`` or ``new-caledonia``."""
        if feed not in FEED_URLS:
            raise ValueError(f"unknown feed {feed!r}")
        return await self.fetch_ex(cache_key=feed, feed=feed)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> MeteoFranceVigilanceStatus:
        feed = kwargs["feed"]
        response = await self._get(client, FEED_URLS[feed], headers={"apikey": self._key, "Accept": "*/*"})
        response.raise_for_status()
        return parse_feed(feed, response.content)
