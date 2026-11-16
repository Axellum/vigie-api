"""Météo-France avalanche-risk bulletins (BRA): ``DPBRA/v1`` on ``public-api.meteofrance.fr``.

One bulletin per massif (35 massifs: the French Alps, Corsica and the Pyrenees), written every afternoon in the snow season and
published under **Licence Ouverte 2.0 (Etalab)** (https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesBRA).

* ``GET /public/DPBRA/v1/massif/BRA?id-massif=<code>&format=xml`` answers the bulletin of one massif (about 13 KB of XML). It is the
  only upstream call this module makes; the massif of a point is found **offline** with ``app/data/meteofrance-bra-massifs.geojson.gz``
  (the list of the massifs, ``tools/build_bra_massifs.py``), so there are at most 35 distinct requests, each cached 30 minutes.
* Needs ``VIGIE_METEOFRANCE_API_KEY`` (sent in the ``apikey`` header, never in a URL, redacted in every error and log line). Without it
  there is no collector: the French massifs are still recognised offline, and ``/v1/avalanches`` says the source is off.

The bulletin is normalised to the same ``AvalancheBulletin`` model as the CAAML sources. What Météo-France writes is kept in its own
words (French); what the API adds (the EAWS label of a level, the problem type of a typical avalanche situation) is derived and
said so in the field descriptions.

Typical avalanche situation codes (``STABILITE/SitAvalTyp`` ``SAT1`` and ``SAT2``). Read on the 35 live bulletins of 2026-11-16 and on the
printed bulletin of the same day: ``1`` goes with "neige récente / poudreuse" texts, ``2`` with the "neige ventée" label of the PDF
(Mont-Blanc), ``4`` with "neige humide" (PDF and texts), ``6`` with "pas de situation prédominante" (PDF of Haute-Bigorre). ``3`` and
``5`` were not in any bulletin of that day: they follow the order of the five EAWS avalanche problems (persistent weak layers, gliding
snow) and are **not confirmed**. Any other code is ignored.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import httpx
from shapely.geometry import Point, shape
from shapely.prepared import prep
from shapely.strtree import STRtree

from app import clock
from app.collectors.base import BaseCollector
from app.models.avalanches import (
    EAWS_DANGER_LABELS,
    AvalancheBulletin,
    AvalancheDangerRating,
    AvalancheElevation,
    AvalancheProblem,
    AvalancheRegion,
    AvalancheTendency,
)

BRA_URL = "https://public-api.meteofrance.fr/public/DPBRA/v1/massif/BRA"
BRA_WEB_URL = "https://meteofrance.com/meteo-montagne"
BRA_ATTRIBUTION = (
    "Météo-France, bulletin d'estimation du risque d'avalanche (BRA, https://meteofrance.com/meteo-montagne). "
    "Licence Ouverte 2.0 (Etalab)"
)
KEY_ENV = "VIGIE_METEOFRANCE_API_KEY"
BRA_MASSIFS_PATH = Path(__file__).resolve().parent.parent / "data" / "meteofrance-bra-massifs.geojson.gz"
BRA_TTL_SECONDS = 1800
REGION_PREFIX = "FR-BRA-"
_BOUNDARY_TOLERANCE_DEG = 0.005
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9._\-+/=]{16,}$")
_ASPECTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
_TAG_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKS_RE = re.compile(r"\n{3,}")

# SAT code -> EAWS problem type (see the module docstring: 1, 2, 4 and 6 read on live bulletins, 3 and 5 not confirmed).
SAT_PROBLEMS: dict[str, str] = {
    "1": "new_snow",
    "2": "wind_slab",
    "3": "persistent_weak_layers",
    "4": "wet_snow",
    "5": "gliding_snow",
}


def meteofrance_bra_key_from_env(env: Optional[Mapping[str, str]] = None) -> tuple[Optional[str], Optional[str]]:
    """``(key, problem)`` read from ``VIGIE_METEOFRANCE_API_KEY``. A key with whitespace or fewer than 16 characters is refused
    without being quoted."""
    raw = ((env if env is not None else os.environ).get(KEY_ENV) or "").strip()
    if not raw:
        return None, None
    if not _KEY_SHAPE.match(raw):
        return None, f"{KEY_ENV} is set but is not shaped as a Météo-France API key: it is not used"
    return raw, None


# ----------------------------------------------------------------------------------------------------------------- massifs


@dataclass(frozen=True)
class _MassifShape:
    code: int
    name: Optional[str]
    area: float
    geom: Any
    prep: Any


class BRAMassifIndex:
    """Offline spatial index of the 35 Météo-France BRA massifs."""

    def __init__(self, path: Path = BRA_MASSIFS_PATH) -> None:
        with gzip.open(path, "rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))
        entries: list[_MassifShape] = []
        for feat in payload.get("features") or []:
            props = feat.get("properties") or {}
            code = props.get("code")
            raw_geom = feat.get("geometry")
            if not isinstance(code, int) or not raw_geom:
                continue
            geom = shape(raw_geom)
            if geom.is_empty:
                continue
            entries.append(_MassifShape(code, props.get("name"), float(geom.area), geom, prep(geom)))
        self._entries = tuple(entries)
        self._tree = STRtree([e.geom for e in self._entries])

    def matches(self, lat: float, lon: float) -> list[AvalancheRegion]:
        """The massif(s) holding the point, smallest first; a point within ``_BOUNDARY_TOLERANCE_DEG`` of a simplified edge falls back
        to the nearest massif."""
        pt = Point(lon, lat)
        hits = [self._entries[int(i)] for i in self._tree.query(pt) if self._entries[int(i)].prep.covers(pt)]
        if hits:
            hits.sort(key=lambda entry: entry.area)
        else:
            near: list[tuple[float, _MassifShape]] = []
            for i in self._tree.query(pt.buffer(_BOUNDARY_TOLERANCE_DEG)):
                entry = self._entries[int(i)]
                dist = float(entry.geom.distance(pt))
                if dist <= _BOUNDARY_TOLERANCE_DEG:
                    near.append((dist, entry))
            near.sort(key=lambda pair: (pair[0], pair[1].area))
            hits = [pair[1] for pair in near]
        return [AvalancheRegion(region_id=f"{REGION_PREFIX}{entry.code}", name=entry.name) for entry in hits]

    def codes(self) -> list[int]:
        return sorted(entry.code for entry in self._entries)


@lru_cache(maxsize=1)
def get_bra_index() -> BRAMassifIndex:
    return BRAMassifIndex()


def massif_code(region: AvalancheRegion) -> Optional[int]:
    """The massif code of a ``FR-BRA-<code>`` region, ``None`` for any other region."""
    if not region.region_id.startswith(REGION_PREFIX):
        return None
    tail = region.region_id[len(REGION_PREFIX):]
    return int(tail) if tail.isdigit() else None


# ------------------------------------------------------------------------------------------------------------------ parsing


def _last_sunday(year: int, month: int) -> date:
    first_of_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = first_of_next - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def paris_to_utc(naive: datetime) -> datetime:
    """A wall-clock time of Paris (the BRA dates carry no zone) as UTC. The rule is the European one (summer time from the last Sunday
    of March 02:00 to the last Sunday of October 03:00, the repeated hour read as summer time), written here so that no time-zone
    database is needed."""
    start = datetime.combine(_last_sunday(naive.year, 3), datetime.min.time()) + timedelta(hours=2)
    end = datetime.combine(_last_sunday(naive.year, 10), datetime.min.time()) + timedelta(hours=3)
    offset = timedelta(hours=2) if start <= naive < end else timedelta(hours=1)
    return (naive - offset).replace(tzinfo=timezone.utc)


def _parse_paris(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return paris_to_utc(parsed) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def clean_text(value: Any) -> Optional[str]:
    """Plain text with the provider's line breaks kept (the site renders them): tags stripped, spaces collapsed inside a line,
    at most one blank line in a row."""
    if not isinstance(value, str):
        return None
    text = _TAG_RE.sub(" ", value).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(_SPACES_RE.sub(" ", line).strip() for line in text.split("\n"))
    text = _BLANKS_RE.sub("\n\n", text).strip()
    return text or None


def _text(root: ET.Element, path: str) -> Optional[str]:
    node = root.find(path)
    return clean_text(node.text) if node is not None and node.text else None


def _level(raw: Any) -> Optional[int]:
    return int(raw) if isinstance(raw, str) and raw.strip() in {"1", "2", "3", "4", "5"} else None


def _rating(level: int, elevation: Optional[AvalancheElevation]) -> AvalancheDangerRating:
    return AvalancheDangerRating(
        main_value=EAWS_DANGER_LABELS[level], level=level, valid_time_period="all_day", elevation=elevation
    )


def _ratings(risque: ET.Element) -> list[AvalancheDangerRating]:
    low, high = _level(risque.get("RISQUE1")), _level(risque.get("RISQUE2"))
    altitude_raw = (risque.get("ALTITUDE") or "").strip()
    altitude = int(altitude_raw) if altitude_raw.isdigit() else None
    ratings: list[AvalancheDangerRating] = []
    if altitude is not None:
        if low is not None:
            ratings.append(_rating(low, AvalancheElevation(upper_bound=altitude)))
        if high is not None:
            ratings.append(_rating(high, AvalancheElevation(lower_bound=altitude)))
        return ratings
    # No elevation band: the single level of the massif (RISQUE1, or RISQUEMAXI when RISQUE1 is empty).
    single = low if low is not None else _level(risque.get("RISQUEMAXI"))
    return [_rating(single, None)] if single is not None else []


def _problems(root: ET.Element, aspects: list[str]) -> list[AvalancheProblem]:
    sat = root.find("STABILITE/SitAvalTyp")
    if sat is None:
        return []
    problems: list[AvalancheProblem] = []
    seen: set[str] = set()
    for attr in ("SAT1", "SAT2"):
        ptype = SAT_PROBLEMS.get((sat.get(attr) or "").strip())
        if ptype is None or ptype in seen:
            continue
        seen.add(ptype)
        problems.append(AvalancheProblem(problem_type=ptype, aspects=list(aspects)))
    return problems


def _join(*parts: Optional[str]) -> Optional[str]:
    kept = [p for p in parts if p]
    return "\n\n".join(kept) if kept else None


def parse_bra_xml(data: bytes, now_utc: datetime, *, region_name: Optional[str] = None) -> Optional[AvalancheBulletin]:
    """Normalise one ``BULLETINS_NEIGE_AVALANCHE`` document. ``None`` when there is no usable bulletin (empty answer, no massif
    code or no date); raises ``ValueError`` when the document is not a BRA at all."""
    if not data or not data.strip():
        return None
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("BRA answer carries a DTD: refused")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"BRA answer is not XML: {exc}") from exc
    if root.tag != "BULLETINS_NEIGE_AVALANCHE":
        raise ValueError(f"BRA answer has an unexpected root element <{root.tag}>")

    code = (root.get("ID") or "").strip()
    written_raw = (root.get("DATEBULLETIN") or "").strip()
    if not code.isdigit() or not written_raw:
        return None
    valid_from = _parse_paris(written_raw)
    valid_until = _parse_paris(root.get("DATEVALIDITE"))
    if valid_from is None:
        return None

    risque = root.find("CARTOUCHERISQUE/RISQUE")
    max_level = _level(risque.get("RISQUEMAXI")) if risque is not None else None
    pente = root.find("CARTOUCHERISQUE/PENTE")
    aspects = [a for a in _ASPECTS if pente is not None and (pente.get(a) or "").strip().lower() == "true"]

    summary = _text(root, "CARTOUCHERISQUE/RESUME")
    if summary is None:
        summary = _join(_text(root, "CARTOUCHERISQUE/ACCIDENTEL"), _text(root, "CARTOUCHERISQUE/NATUREL"))
    stability_body = _text(root, "STABILITE/TEXTESANSTITRE") or _text(root, "STABILITE/TEXTE")

    tendency: list[AvalancheTendency] = []
    day2 = AvalancheTendency(
        valid_from=_parse_paris(risque.get("DATE_RISQUE_J2")) if risque is not None else None,
        highlights=_text(root, "CARTOUCHERISQUE/RisqueJ2"),
        comment=_text(root, "CARTOUCHERISQUE/CommentaireRisqueJ2"),
    )
    if any((day2.valid_from, day2.highlights, day2.comment)):
        tendency.append(day2)

    return AvalancheBulletin(
        bulletin_id=f"FR-BRA-{code}-{written_raw}",
        source="meteofrance_bra",
        lang="fr",
        publication_time=_parse_paris(root.get("DATEDIFFUSION")),
        valid_from=valid_from,
        valid_until=valid_until,
        unscheduled=(root.get("AMENDEMENT") or "").strip().lower() == "true",
        is_expired=valid_until is not None and now_utc > valid_until,
        max_danger_level=max_level,
        max_danger_label=EAWS_DANGER_LABELS.get(max_level) if max_level is not None else None,
        regions=[AvalancheRegion(region_id=f"{REGION_PREFIX}{int(code)}", name=(root.get("MASSIF") or "").strip() or region_name)],
        danger_ratings=_ratings(risque) if risque is not None else [],
        avalanche_problems=_problems(root, aspects),
        avalanche_activity_highlights=clean_text(risque.get("COMMENTAIRE")) if risque is not None else None,
        avalanche_activity_comment=summary,
        snowpack_highlights=_text(root, "STABILITE/TITRE"),
        snowpack_comment=_join(stability_body, _text(root, "QUALITE/TEXTE")),
        tendency=tendency,
        bulletin_url=BRA_WEB_URL,
        attribution=BRA_ATTRIBUTION,
    )


# ---------------------------------------------------------------------------------------------------------------- collector


@dataclass(frozen=True)
class BRAMassifBulletin:
    """What the API answered for one massif: the bulletin, or ``None`` when it publishes none (off season)."""

    code: int
    bulletin: Optional[AvalancheBulletin]
    fetched_utc: datetime


class MeteoFranceBRACollector(BaseCollector[BRAMassifBulletin]):
    """Cached collector of the Météo-France BRA of one massif (one cache entry and one circuit per massif)."""

    def __init__(self, api_key: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="meteofrance_bra",
            name="Météo-France BRA avalanche bulletins (DPBRA)",
            domain="avalanches",
            ttl_seconds=BRA_TTL_SECONDS,
            timeout_seconds=15.0,
            http_client=http_client,
        )
        self._key = api_key
        self.secrets = (api_key,)

    async def fetch_massif(self, code: int, allow_stale_on_error: bool = True):  # type: ignore[no-untyped-def]
        """The bulletin of massif ``code`` (cached ``BRA_TTL_SECONDS``)."""
        return await self.fetch_ex(cache_key=f"massif:{code}", code=code, allow_stale_on_error=allow_stale_on_error)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> BRAMassifBulletin:
        code = int(kwargs["code"])
        now_utc = datetime.fromtimestamp(clock.wall(), tz=timezone.utc)
        resp = await self._get(
            client,
            BRA_URL,
            params={"id-massif": str(code), "format": "xml"},
            headers={"apikey": self._key, "Accept": "application/xml, text/xml"},
        )
        if resp.status_code == 404:
            return BRAMassifBulletin(code, None, now_utc)
        resp.raise_for_status()
        bulletin = await clock.run_blocking(parse_bra_xml, resp.content, now_utc)
        return BRAMassifBulletin(code, bulletin, now_utc)
