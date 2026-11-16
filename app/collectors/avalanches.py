"""Sober avalanche bulletin collectors (SLF Switzerland/Liechtenstein, EAWS Euregio, and Météo-France BRA in ``meteofrance_bra.py``).

The two CAAML feeds are single-document downloads updated once or twice per day during the snow
season and on unscheduled snow events:

* ``slf_avalanche``: ``https://aws.slf.ch/api/bulletin/caaml/en/geojson`` (WSL Institute for
  Snow and Avalanche Research SLF, CC BY 4.0). Returns a GeoJSON ``FeatureCollection`` with
  embedded warning-zone polygons covering Switzerland (``CH-*``) and Liechtenstein
  (``CH-3311``). Supports ``ETag`` / ``If-None-Match`` with ``304 Not Modified``.
* ``euregio_avalanche``: ``https://static.avalanche.report/bulletins/latest/EUREGIO_en_CAAMLv6.json``
  (Avalanche.report — Tirol, Südtirol, Trentino, CC BY 4.0 DEED). Returns CAAML v6 JSON
  keyed by EAWS micro-region IDs (``AT-07-*``, ``IT-32-BZ-*``, ``IT-32-TN-*``), matched
  offline against the bundled CC0 1.0 ``app/data/eaws-euregio-regions.geojson.gz`` index.
  Supports ``ETag`` / ``Last-Modified`` conditional ``304 Not Modified``.

Neither collector performs per-coordinate upstream requests: coverage gating uses the
bundled offline country and micro-region shapefiles, and a 30-minute TTL with conditional
validators keeps upstream traffic minimal.

The third source, ``meteofrance_bra`` (France: Alps, Corsica, Pyrenees), is one small request per massif, cached 30 minutes, and needs
a key; its massifs are matched offline against ``app/data/meteofrance-bra-massifs.geojson.gz``. ``resolve_avalanche_coverage`` tries
SLF (CH/LI), then Euregio, then the BRA massifs.
"""
from __future__ import annotations

import gzip
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence

import httpx
from shapely.geometry import Point, shape
from shapely.prepared import prep
from shapely.strtree import STRtree

from app import clock
from app.collectors.base import BaseCollector, CacheEntry, Collected, Fetched, parse_iso_utc
from app.collectors.meteofrance_bra import BRA_ATTRIBUTION, BRA_WEB_URL, BRAMassifIndex, get_bra_index
from app.models.avalanches import (
    EAWS_DANGER_LABELS,
    EAWS_DANGER_LEVELS,
    AvalancheBulletin,
    AvalancheDangerRating,
    AvalancheElevation,
    AvalancheProblem,
    AvalancheRegion,
    AvalancheSourceId,
    AvalancheStatus,
    AvalancheTendency,
)
from app.services.geoindex import CountryIndex, default_countries

SLF_CAAML_URL = "https://aws.slf.ch/api/bulletin/caaml/en/geojson"
SLF_BULLETIN_WEB_URL = "https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/"
SLF_ATTRIBUTION = "WSL Institute for Snow and Avalanche Research SLF (https://www.slf.ch). CC BY 4.0"
SLF_COUNTRIES = frozenset({"CH", "LI"})

EUREGIO_CAAML_URL = "https://static.avalanche.report/bulletins/latest/EUREGIO_en_CAAMLv6.json"
EUREGIO_BULLETIN_WEB_URL = "https://avalanche.report/bulletin/latest"
EUREGIO_ATTRIBUTION = (
    "Avalanche.report — Avalanche Warning Service Tirol, Südtirol, Trentino "
    "(https://avalanche.report). CC BY 4.0 DEED"
)

# Public page and credit of each source (one place, so a new source is one line here).
SOURCE_LINKS: dict[str, tuple[str, str]] = {
    "slf_avalanche": (SLF_BULLETIN_WEB_URL, SLF_ATTRIBUTION),
    "euregio_avalanche": (EUREGIO_BULLETIN_WEB_URL, EUREGIO_ATTRIBUTION),
    "meteofrance_bra": (BRA_WEB_URL, BRA_ATTRIBUTION),
}

EUREGIO_REGIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "eaws-euregio-regions.geojson.gz"
AVALANCHE_TTL_SECONDS = 1800
_BOUNDARY_TOLERANCE_DEG = 0.005  # ~500 m tolerance around 0.003 deg simplified ridge lines
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def _utc_now() -> datetime:
    return datetime.fromtimestamp(clock.wall(), tz=timezone.utc)


def _clean_html_text(value: Any) -> Optional[str]:
    """Strip CAAML inline HTML tags cleanly while preserving official wording verbatim."""
    if not isinstance(value, str) or not value.strip():
        return None
    stripped = _TAG_RE.sub(" ", value)
    unescaped = html.unescape(stripped)
    collapsed = _SPACE_RE.sub(" ", unescaped).strip()
    return collapsed or None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    ts = parse_iso_utc(value.strip())
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _parse_elevation(raw: Any) -> Optional[AvalancheElevation]:
    if not isinstance(raw, dict):
        return None
    lower = raw.get("lowerBound")
    upper = raw.get("upperBound")
    if lower is None and upper is None:
        return None
    return AvalancheElevation(lower_bound=lower, upper_bound=upper)


def _parse_danger_ratings(
    raw_list: Any,
) -> tuple[list[AvalancheDangerRating], Optional[int], Optional[str], Optional[str]]:
    ratings: list[AvalancheDangerRating] = []
    max_level: Optional[int] = None
    max_label: Optional[str] = None
    max_subdivisions: set[str] = set()

    if not isinstance(raw_list, list):
        return ratings, max_level, max_label, None

    for item in raw_list:
        if not isinstance(item, dict):
            continue
        main_value = item.get("mainValue")
        if not isinstance(main_value, str) or not main_value.strip():
            continue
        main_clean = main_value.strip()
        level = EAWS_DANGER_LEVELS.get(main_clean)
        subdivision = None
        custom = item.get("customData")
        if isinstance(custom, dict) and isinstance(custom.get("CH"), dict):
            sub_val = custom["CH"].get("subdivision")
            if isinstance(sub_val, str) and sub_val.strip():
                subdivision = sub_val.strip()

        ratings.append(
            AvalancheDangerRating(
                main_value=main_clean,
                level=level,
                valid_time_period=item.get("validTimePeriod"),
                elevation=_parse_elevation(item.get("elevation")),
                subdivision=subdivision,
            )
        )
        if level is not None:
            if max_level is None or level > max_level:
                max_level = level
                max_label = EAWS_DANGER_LABELS.get(level, main_clean)
                max_subdivisions = {subdivision} if subdivision else set()
            elif level == max_level and subdivision:
                max_subdivisions.add(subdivision)

    chosen_subdivision = next(iter(max_subdivisions)) if len(max_subdivisions) == 1 else None
    return ratings, max_level, max_label, chosen_subdivision


def _parse_problems(raw_list: Any) -> list[AvalancheProblem]:
    if not isinstance(raw_list, list):
        return []
    problems: list[AvalancheProblem] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        ptype = item.get("problemType")
        if not isinstance(ptype, str) or not ptype.strip():
            continue
        rating_val = item.get("dangerRatingValue")
        rating_level = EAWS_DANGER_LEVELS.get(rating_val) if isinstance(rating_val, str) else None
        custom = item.get("customData") if isinstance(item.get("customData"), dict) else {}
        ch_custom = custom.get("CH") if isinstance(custom.get("CH"), dict) else {}
        albina_custom = custom.get("ALBINA") if isinstance(custom.get("ALBINA"), dict) else {}
        raw_aspects = item.get("aspects")
        aspects = [str(a).strip() for a in raw_aspects if isinstance(a, str) and a.strip()] if isinstance(raw_aspects, list) else []
        raw_size = item.get("avalancheSize")
        size = int(raw_size) if isinstance(raw_size, int) and 1 <= raw_size <= 5 else None
        problems.append(
            AvalancheProblem(
                problem_type=ptype.strip(),
                danger_rating_value=rating_val if isinstance(rating_val, str) else None,
                danger_level=rating_level,
                valid_time_period=item.get("validTimePeriod"),
                elevation=_parse_elevation(item.get("elevation")),
                aspects=aspects,
                snowpack_stability=item.get("snowpackStability") if isinstance(item.get("snowpackStability"), str) else None,
                frequency=item.get("frequency") if isinstance(item.get("frequency"), str) else None,
                avalanche_size=size,
                avalanche_type=albina_custom.get("avalancheType") if isinstance(albina_custom.get("avalancheType"), str) else None,
                core_zone_text=_clean_html_text(ch_custom.get("coreZoneText")),
                comment=_clean_html_text(item.get("comment")),
            )
        )
    return problems


def _parse_tendency(raw: Any) -> list[AvalancheTendency]:
    items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    result: list[AvalancheTendency] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        vt = item.get("validTime") if isinstance(item.get("validTime"), dict) else {}
        entry = AvalancheTendency(
            tendency_type=item.get("tendencyType") if isinstance(item.get("tendencyType"), str) else None,
            valid_from=_parse_dt(vt.get("startTime")),
            valid_until=_parse_dt(vt.get("endTime")),
            highlights=_clean_html_text(item.get("highlights")),
            comment=_clean_html_text(item.get("comment")),
        )
        if any((entry.tendency_type, entry.valid_from, entry.valid_until, entry.highlights, entry.comment)):
            result.append(entry)
    return result


def _parse_bulletin_dict(
    props: dict[str, Any],
    *,
    source: AvalancheSourceId,
    bulletin_url: str,
    attribution: str,
    now_utc: datetime,
    region_name_lookup: Optional[dict[str, str]] = None,
) -> Optional[AvalancheBulletin]:
    bulletin_id = props.get("bulletinID")
    if not isinstance(bulletin_id, str) or not bulletin_id.strip():
        return None
    vt = props.get("validTime") if isinstance(props.get("validTime"), dict) else {}
    valid_from = _parse_dt(vt.get("startTime"))
    valid_until = _parse_dt(vt.get("endTime"))
    is_expired = valid_until is not None and now_utc > valid_until

    ratings, max_level, max_label, _ = _parse_danger_ratings(props.get("dangerRatings"))
    problems = _parse_problems(props.get("avalancheProblems"))

    regions: list[AvalancheRegion] = []
    for raw_reg in props.get("regions") or []:
        if not isinstance(raw_reg, dict):
            continue
        rid = raw_reg.get("regionID")
        if not isinstance(rid, str) or not rid.strip():
            continue
        rid_clean = rid.strip()
        rname = raw_reg.get("name")
        if not isinstance(rname, str) or not rname.strip():
            rname = (region_name_lookup or {}).get(rid_clean)
        regions.append(AvalancheRegion(region_id=rid_clean, name=rname.strip() if isinstance(rname, str) and rname.strip() else None))

    activity = props.get("avalancheActivity") if isinstance(props.get("avalancheActivity"), dict) else {}
    snowpack = props.get("snowpackStructure") if isinstance(props.get("snowpackStructure"), dict) else {}

    return AvalancheBulletin(
        bulletin_id=bulletin_id.strip(),
        source=source,
        lang=props.get("lang") if isinstance(props.get("lang"), str) else None,
        publication_time=_parse_dt(props.get("publicationTime")),
        valid_from=valid_from,
        valid_until=valid_until,
        next_update=_parse_dt(props.get("nextUpdate")),
        unscheduled=bool(props.get("unscheduled")),
        is_expired=is_expired,
        max_danger_level=max_level,
        max_danger_label=max_label,
        regions=regions,
        danger_ratings=ratings,
        avalanche_problems=problems,
        avalanche_activity_highlights=_clean_html_text(activity.get("highlights")),
        avalanche_activity_comment=_clean_html_text(activity.get("comment")),
        snowpack_highlights=_clean_html_text(snowpack.get("highlights")),
        snowpack_comment=_clean_html_text(snowpack.get("comment")),
        tendency=_parse_tendency(props.get("tendency")),
        bulletin_url=bulletin_url,
        attribution=attribution,
    )


def _bulletin_subdivision(bulletin: AvalancheBulletin) -> Optional[str]:
    if bulletin.max_danger_level is None:
        return None
    subs = {r.subdivision for r in bulletin.danger_ratings if r.level == bulletin.max_danger_level and r.subdivision}
    return next(iter(subs)) if len(subs) == 1 else None


def is_bulletin_active(bulletin: AvalancheBulletin, now_utc: datetime) -> bool:
    """Return True only when ``now_utc`` falls inside the bulletin's validity window."""
    if bulletin.valid_until is not None and now_utc > bulletin.valid_until:
        return False
    if bulletin.valid_from is not None and now_utc < bulletin.valid_from:
        return False
    return True


def refresh_expiry_flags(bulletins: Sequence[AvalancheBulletin], now_utc: datetime) -> list[AvalancheBulletin]:
    """Return copies with ``is_expired`` recomputed against the caller's ``now_utc``."""
    updated: list[AvalancheBulletin] = []
    for b in bulletins:
        expired = b.valid_until is not None and now_utc > b.valid_until
        updated.append(b.model_copy(update={"is_expired": expired}))
    return updated


@dataclass(frozen=True)
class _RegionShape:
    region_id: str
    name: Optional[str]
    area: float
    geom: Any
    prep: Any


class EuregioRegionIndex:
    """Offline spatial index for EAWS Euregio micro-regions (AT-07, IT-32-BZ, IT-32-TN)."""

    def __init__(self, path: Path = EUREGIO_REGIONS_PATH) -> None:
        with gzip.open(path, "rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))
        entries: list[_RegionShape] = []
        for feat in payload.get("features") or []:
            props = feat.get("properties") or {}
            rid = props.get("id")
            raw_geom = feat.get("geometry")
            if not rid or not raw_geom:
                continue
            geom = shape(raw_geom)
            if geom.is_empty:
                continue
            name = props.get("name_en") or props.get("name") or rid
            entries.append(
                _RegionShape(
                    region_id=str(rid),
                    name=str(name),
                    area=float(geom.area),
                    geom=geom,
                    prep=prep(geom),
                )
            )
        self._entries = tuple(entries)
        self._by_id = {e.region_id: e.name for e in self._entries if e.name}
        self._tree = STRtree([e.geom for e in self._entries])

    def matches(self, lat: float, lon: float) -> list[AvalancheRegion]:
        """Return matching Euregio micro-regions ordered from smallest area (most specific) to largest."""
        pt = Point(lon, lat)
        hits: list[_RegionShape] = []
        for idx in self._tree.query(pt):
            entry = self._entries[int(idx)]
            if entry.prep.covers(pt):
                hits.append(entry)
        if not hits:
            # Small tolerance along simplified high-alpine ridge boundaries.
            probe = pt.buffer(_BOUNDARY_TOLERANCE_DEG)
            nearest: list[tuple[float, _RegionShape]] = []
            for idx in self._tree.query(probe):
                entry = self._entries[int(idx)]
                dist = float(entry.geom.distance(pt))
                if dist <= _BOUNDARY_TOLERANCE_DEG:
                    nearest.append((dist, entry))
            nearest.sort(key=lambda pair: (pair[0], pair[1].area))
            hits = [pair[1] for pair in nearest]
        else:
            hits.sort(key=lambda entry: entry.area)
        return [AvalancheRegion(region_id=e.region_id, name=e.name) for e in hits]

    def covers(self, lat: float, lon: float) -> bool:
        return bool(self.matches(lat, lon))

    def name_for(self, region_id: str) -> Optional[str]:
        return self._by_id.get(region_id)


@lru_cache(maxsize=1)
def get_euregio_index() -> EuregioRegionIndex:
    return EuregioRegionIndex()


@dataclass(frozen=True)
class _SLFFeature:
    bulletin: AvalancheBulletin
    geom: Any
    prep: Any


@dataclass(frozen=True)
class SLFFeed:
    """Parsed SLF GeoJSON bulletin collection with spatial index."""

    features: tuple[_SLFFeature, ...]
    tree: Optional[STRtree]
    fetched_utc: datetime

    def match_point(self, lat: float, lon: float) -> list[AvalancheBulletin]:
        if self.tree is None or not self.features:
            return []
        pt = Point(lon, lat)
        matched: list[AvalancheBulletin] = []
        for idx in self.tree.query(pt):
            feat = self.features[int(idx)]
            if feat.prep.covers(pt):
                matched.append(feat.bulletin)
        if not matched:
            probe = pt.buffer(_BOUNDARY_TOLERANCE_DEG)
            candidates: list[tuple[float, AvalancheBulletin]] = []
            for idx in self.tree.query(probe):
                feat = self.features[int(idx)]
                dist = float(feat.geom.distance(pt))
                if dist <= _BOUNDARY_TOLERANCE_DEG:
                    candidates.append((dist, feat.bulletin))
            candidates.sort(key=lambda pair: pair[0])
            matched = [pair[1] for pair in candidates]
        return matched


@dataclass(frozen=True)
class EuregioFeed:
    """Parsed Euregio CAAML v6 bulletin collection indexed by micro-region ID."""

    bulletins: tuple[AvalancheBulletin, ...]
    by_region: dict[str, tuple[AvalancheBulletin, ...]]
    fetched_utc: datetime

    def match_regions(self, region_ids: Sequence[str]) -> list[AvalancheBulletin]:
        seen: set[str] = set()
        matched: list[AvalancheBulletin] = []
        for rid in region_ids:
            for bulletin in self.by_region.get(rid, ()):
                if bulletin.bulletin_id not in seen:
                    seen.add(bulletin.bulletin_id)
                    matched.append(bulletin)
        return matched


def parse_slf_geojson(payload: dict[str, Any], now_utc: datetime) -> SLFFeed:
    """Parse an SLF CAAML v6 GeoJSON FeatureCollection."""
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ValueError("SLF CAAML feed did not return a GeoJSON FeatureCollection")
    features: list[_SLFFeature] = []
    for raw_feat in payload.get("features") or []:
        if not isinstance(raw_feat, dict):
            continue
        props = raw_feat.get("properties")
        raw_geom = raw_feat.get("geometry")
        if not isinstance(props, dict) or not raw_geom:
            continue
        bulletin = _parse_bulletin_dict(
            props,
            source="slf_avalanche",
            bulletin_url=SLF_BULLETIN_WEB_URL,
            attribution=SLF_ATTRIBUTION,
            now_utc=now_utc,
        )
        if bulletin is None:
            continue
        geom = shape(raw_geom)
        if geom.is_empty:
            continue
        features.append(_SLFFeature(bulletin=bulletin, geom=geom, prep=prep(geom)))

    tree = STRtree([f.geom for f in features]) if features else None
    return SLFFeed(features=tuple(features), tree=tree, fetched_utc=now_utc)


def parse_euregio_json(
    payload: dict[str, Any],
    now_utc: datetime,
    regions_index: Optional[EuregioRegionIndex] = None,
) -> EuregioFeed:
    """Parse an Avalanche.report Euregio CAAML v6 JSON document."""
    if not isinstance(payload, dict) or not isinstance(payload.get("bulletins"), list):
        raise ValueError("Euregio CAAML feed did not return a `bulletins` list")
    lookup: dict[str, str] = {}
    if regions_index is not None:
        lookup = {rid: name for rid, name in regions_index._by_id.items()}

    bulletins: list[AvalancheBulletin] = []
    by_region_mut: dict[str, list[AvalancheBulletin]] = {}
    for raw_b in payload["bulletins"]:
        if not isinstance(raw_b, dict):
            continue
        bulletin = _parse_bulletin_dict(
            raw_b,
            source="euregio_avalanche",
            bulletin_url=EUREGIO_BULLETIN_WEB_URL,
            attribution=EUREGIO_ATTRIBUTION,
            now_utc=now_utc,
            region_name_lookup=lookup,
        )
        if bulletin is None:
            continue
        bulletins.append(bulletin)
        for region in bulletin.regions:
            by_region_mut.setdefault(region.region_id, []).append(bulletin)

    by_region = {rid: tuple(items) for rid, items in by_region_mut.items()}
    return EuregioFeed(bulletins=tuple(bulletins), by_region=by_region, fetched_utc=now_utc)


class SLFAvalancheCollector(BaseCollector[SLFFeed]):
    """Cached conditional collector for SLF Switzerland & Liechtenstein CAAML v6 bulletins."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="slf_avalanche",
            name="SLF avalanche bulletins (Switzerland & Liechtenstein)",
            domain="avalanches",
            timeout_seconds=15.0,
            ttl_seconds=AVALANCHE_TTL_SECONDS,
            http_client=http_client,
        )

    async def fetch_feed(self, allow_stale_on_error: bool = True) -> Fetched[SLFFeed]:
        return await self.fetch_ex(cache_key="feed", allow_stale_on_error=allow_stale_on_error)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[SLFFeed]:
        previous: Optional[CacheEntry[SLFFeed]] = kwargs.get("previous")
        headers = {"Accept": "application/geo+json, application/json;q=0.9"}
        if previous is not None and previous.validators:
            if previous.validators.get("ETag"):
                headers["If-None-Match"] = previous.validators["ETag"]
            if previous.validators.get("Last-Modified"):
                headers["If-Modified-Since"] = previous.validators["Last-Modified"]
        resp = await self._get(client, SLF_CAAML_URL, headers=headers)
        if resp.status_code == 304:
            if previous is None:
                raise ValueError("SLF answered 304 Not Modified to a non-conditional request")
            return Collected(previous.value, validators=previous.validators)
        resp.raise_for_status()
        payload = resp.json()
        now_utc = _utc_now()
        feed = await clock.run_blocking(parse_slf_geojson, payload, now_utc)
        validators = {name: resp.headers[name] for name in ("ETag", "Last-Modified") if resp.headers.get(name)}
        return Collected(feed, validators=validators or None)


class EuregioAvalancheCollector(BaseCollector[EuregioFeed]):
    """Cached conditional collector for Avalanche.report Euregio CAAML v6 bulletins."""

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
        region_index: Optional[EuregioRegionIndex] = None,
    ) -> None:
        super().__init__(
            provider_id="euregio_avalanche",
            name="Avalanche.report Euregio (Tirol, Südtirol, Trentino)",
            domain="avalanches",
            timeout_seconds=15.0,
            ttl_seconds=AVALANCHE_TTL_SECONDS,
            http_client=http_client,
        )
        self._region_index = region_index

    @property
    def region_index(self) -> EuregioRegionIndex:
        if self._region_index is None:
            self._region_index = get_euregio_index()
        return self._region_index

    async def fetch_feed(self, allow_stale_on_error: bool = True) -> Fetched[EuregioFeed]:
        return await self.fetch_ex(cache_key="feed", allow_stale_on_error=allow_stale_on_error)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[EuregioFeed]:
        previous: Optional[CacheEntry[EuregioFeed]] = kwargs.get("previous")
        headers = {"Accept": "application/json"}
        if previous is not None and previous.validators:
            if previous.validators.get("ETag"):
                headers["If-None-Match"] = previous.validators["ETag"]
            if previous.validators.get("Last-Modified"):
                headers["If-Modified-Since"] = previous.validators["Last-Modified"]
        resp = await self._get(client, EUREGIO_CAAML_URL, headers=headers)
        if resp.status_code == 304:
            if previous is None:
                raise ValueError("Euregio answered 304 Not Modified to a non-conditional request")
            return Collected(previous.value, validators=previous.validators)
        resp.raise_for_status()
        payload = resp.json()
        now_utc = _utc_now()
        idx = self.region_index
        feed = await clock.run_blocking(parse_euregio_json, payload, now_utc, idx)
        validators = {name: resp.headers[name] for name in ("ETag", "Last-Modified") if resp.headers.get(name)}
        return Collected(feed, validators=validators or None)


def resolve_avalanche_coverage(
    lat: float,
    lon: float,
    *,
    euregio_index: Optional[EuregioRegionIndex] = None,
    bra_index: Optional[BRAMassifIndex] = None,
    countries: Optional[CountryIndex] = None,
    preferred_source: Optional[str] = None,
) -> tuple[Optional[AvalancheSourceId], list[AvalancheRegion]]:
    """Determine offline whether ``(lat, lon)`` belongs to SLF, Euregio or Météo-France BRA coverage (in that order).

    Returns ``(source_id, matched_regions)``. Performs zero network requests.
    """
    c_index = countries or default_countries()
    country_codes = c_index.countries_for_point(lat, lon)

    if preferred_source == "slf":
        return ("slf_avalanche", []) if any(c in SLF_COUNTRIES for c in country_codes) else (None, [])

    idx = euregio_index or get_euregio_index()
    eur_regions = idx.matches(lat, lon)

    if preferred_source == "euregio":
        return ("euregio_avalanche", eur_regions) if eur_regions else (None, [])

    bra_regions = (bra_index or get_bra_index()).matches(lat, lon)

    if preferred_source == "meteofrance":
        return ("meteofrance_bra", bra_regions) if bra_regions else (None, [])

    if any(c in SLF_COUNTRIES for c in country_codes):
        return "slf_avalanche", []
    if eur_regions:
        return "euregio_avalanche", eur_regions
    if bra_regions:
        return "meteofrance_bra", bra_regions
    return None, []


def summarize_point_avalanche(
    *,
    source: AvalancheSourceId,
    active_bulletins: Sequence[AvalancheBulletin],
    stale: bool,
    region_id: Optional[str] = None,
    region_name: Optional[str] = None,
) -> AvalancheStatus:
    """Build the compact ``PointResponse.avalanche`` summary from matched active bulletins."""
    bulletin_url, attribution = SOURCE_LINKS[source]
    if not active_bulletins:
        return AvalancheStatus(
            status="confirmed_none",
            covered_by=source,
            region_id=region_id,
            region_name=region_name,
            max_danger_level=None,
            max_danger_label=None,
            subdivision=None,
            problem_types=[],
            valid_from=None,
            valid_until=None,
            unscheduled=False,
            bulletin_url=bulletin_url,
            stale=stale,
            attribution=attribution,
        )

    best = max(active_bulletins, key=lambda b: b.max_danger_level or 0)
    problem_types: list[str] = []
    seen_problems: set[str] = set()
    for b in active_bulletins:
        for p in b.avalanche_problems:
            if p.problem_type not in seen_problems:
                seen_problems.add(p.problem_type)
                problem_types.append(p.problem_type)

    # For SLF, resolve an exact micro-region only when the bulletin covers a single region.
    eff_region_id = region_id
    eff_region_name = region_name
    if eff_region_id is None and len(best.regions) == 1:
        eff_region_id = best.regions[0].region_id
        eff_region_name = best.regions[0].name

    return AvalancheStatus(
        status="active",
        covered_by=source,
        region_id=eff_region_id,
        region_name=eff_region_name,
        max_danger_level=best.max_danger_level,
        max_danger_label=best.max_danger_label,
        subdivision=_bulletin_subdivision(best),
        problem_types=problem_types,
        valid_from=best.valid_from,
        valid_until=best.valid_until,
        unscheduled=any(b.unscheduled for b in active_bulletins),
        bulletin_url=best.bulletin_url or bulletin_url,
        stale=stale,
        attribution=attribution,
    )
