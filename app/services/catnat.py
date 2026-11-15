"""Offline lookup service for French natural disaster decrees (GASPAR CatNat)."""

from __future__ import annotations

import gzip
import json
import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.attribution import GASPAR_CATNAT, credit_text
from app.models.catnat import (
    CatNatCommuneMatch,
    CatNatDecree,
    CatNatHazardType,
    CatNatHistoryResponse,
    CatNatTypeCount,
    CatNatYearCount,
)
from app.services.geodesy import haversine_km
from app.services.geoindex import default_countries

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "gaspar-catnat.json.gz"

OVERSEAS_DEPT_TO_ISO: dict[str, str] = {
    "971": "GP",
    "972": "MQ",
    "973": "GF",
    "974": "RE",
    "976": "YT",
}
ISO_TO_OVERSEAS_DEPT: dict[str, str] = {iso: dept for dept, iso in OVERSEAS_DEPT_TO_ISO.items()}
COVERED_TERRITORIES: frozenset[str] = frozenset({"FR", *OVERSEAS_DEPT_TO_ISO.values()})
UNCOVERED_OVERSEAS_PREFIXES: frozenset[str] = frozenset({"975", "977", "978", "984", "986", "987", "988", "989"})


class UnknownCommuneError(LookupError):
    """Raised when a syntactically valid French INSEE code is absent from the dataset."""


@dataclass(frozen=True, slots=True)
class _CommuneEntry:
    code: str
    name: str
    dept: str
    territory: str
    lat: float
    lon: float
    events_packed: str


@dataclass(frozen=True, slots=True)
class CatNatIndex:
    dataset_updated: str
    period_start: Optional[str]
    period_end: Optional[str]
    type_by_idx: tuple[tuple[CatNatHazardType, str], ...]
    idx_by_slug: dict[str, int]
    by_code: dict[str, _CommuneEntry]
    by_territory: dict[str, tuple[_CommuneEntry, ...]]


def _territory_for_dept(dept: str) -> str:
    return OVERSEAS_DEPT_TO_ISO.get(dept, "FR")


def _load_index_from_path(path: Path) -> CatNatIndex:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        raw = json.load(fh)

    hazard_types = sorted(raw["hazard_types"], key=lambda item: int(item["idx"]))
    type_by_idx = tuple((item["slug"], item["label"]) for item in hazard_types)
    idx_by_slug = {item["slug"]: int(item["idx"]) for item in hazard_types}

    by_code: dict[str, _CommuneEntry] = {}
    grouped: dict[str, list[_CommuneEntry]] = {territory: [] for territory in COVERED_TERRITORIES}

    for row in raw["communes"]:
        dept = str(row["d"])
        territory = _territory_for_dept(dept)
        entry = _CommuneEntry(
            code=str(row["c"]),
            name=str(row["n"]),
            dept=dept,
            territory=territory,
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            events_packed=str(row.get("e", "")),
        )
        by_code[entry.code] = entry
        grouped.setdefault(territory, []).append(entry)

    return CatNatIndex(
        dataset_updated=str(raw["dataset_updated"]),
        period_start=raw.get("earliest_start_date"),
        period_end=raw.get("latest_decree_date") or raw.get("latest_start_date"),
        type_by_idx=type_by_idx,
        idx_by_slug=idx_by_slug,
        by_code=by_code,
        by_territory={k: tuple(v) for k, v in grouped.items()},
    )


@lru_cache(maxsize=1)
def default_catnat_index() -> CatNatIndex:
    """Load and cache the bundled GASPAR CatNat dataset."""
    return _load_index_from_path(DATA_FILE)


def _unpack_events(events_packed: str) -> list[tuple[int, str, str, str]]:
    if not events_packed:
        return []
    out: list[tuple[int, str, str, str]] = []
    for chunk in events_packed.split(";"):
        if not chunk:
            continue
        type_s, start_d, end_d, decree_d = chunk.split(",")
        out.append((int(type_s), start_d, end_d, decree_d))
    return out


def _event_year(start_d: str, end_d: str, decree_d: str) -> Optional[int]:
    for candidate in (start_d, decree_d, end_d):
        if len(candidate) >= 4 and candidate[:4].isdigit():
            return int(candidate[:4])
    return None


def _nearest_commune(
    lat: float,
    lon: float,
    territories: list[str],
    index: CatNatIndex,
) -> tuple[_CommuneEntry, float]:
    """Find nearest commune centroid inside the candidate French territories."""
    cos_lat = math.cos(math.radians(lat))
    candidates: list[_CommuneEntry] = []
    for territory in territories:
        candidates.extend(index.by_territory.get(territory, ()))
    if not candidates:
        raise LookupError(f"No communes indexed for territories {territories}")

    # Fast squared equirectangular pass, then exact Haversine on top 5 to avoid 35k trig calls.
    scored: list[tuple[float, _CommuneEntry]] = []
    for entry in candidates:
        dlat = entry.lat - lat
        dlon = (entry.lon - lon) * cos_lat
        scored.append((dlat * dlat + dlon * dlon, entry))
    scored.sort(key=lambda item: item[0])

    best_entry = scored[0][1]
    best_dist = haversine_km(lat, lon, best_entry.lat, best_entry.lon)
    for _, entry in scored[1:5]:
        dist = haversine_km(lat, lon, entry.lat, entry.lon)
        if dist < best_dist:
            best_dist = dist
            best_entry = entry
    return best_entry, round(best_dist, 2)


def _outside_coverage_response(
    index: CatNatIndex,
    *,
    lat: Optional[float],
    lon: Optional[float],
    country: Optional[str],
    limit: int,
    offset: int,
    note: str,
) -> CatNatHistoryResponse:
    return CatNatHistoryResponse(
        status="outside_coverage",
        lat=lat,
        lon=lon,
        country=country,
        commune=None,
        dataset_updated=index.dataset_updated,
        period_start=None,
        period_end=None,
        total_decrees=None,
        filtered_decrees=None,
        first_decree_date=None,
        last_decree_date=None,
        by_type=[],
        by_year=[],
        limit=limit,
        offset=offset,
        returned=0,
        decrees=[],
        derived=[],
        note=note,
        attribution=credit_text(*GASPAR_CATNAT),
    )


def query_catnat_history(
    *,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    commune: Optional[str] = None,
    hazard_type: Optional[CatNatHazardType] = None,
    since_year: Optional[int] = None,
    until_year: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    index: Optional[CatNatIndex] = None,
) -> CatNatHistoryResponse:
    """Query historical GASPAR CatNat decrees for a French coordinate or INSEE commune code."""
    if since_year is not None and until_year is not None and since_year > until_year:
        raise ValueError("since_year must be less than or equal to until_year")

    idx = index or default_catnat_index()
    centroid_distance_km: Optional[float] = None
    matched_by_coords = False

    if commune is not None:
        code = commune.strip().upper()
        if code[:3] in UNCOVERED_OVERSEAS_PREFIXES:
            return _outside_coverage_response(
                idx,
                lat=None,
                lon=None,
                country=None,
                limit=limit,
                offset=offset,
                note=(
                    f"INSEE code '{code}' belongs to a French overseas territory outside the 101 departments "
                    "covered by the GASPAR CatNat dataset; counts are omitted rather than estimated."
                ),
            )
        entry = idx.by_code.get(code)
        if entry is None:
            raise UnknownCommuneError(f"Unknown French INSEE commune code '{code}' in covered departments")
        territory = entry.territory
    else:
        assert lat is not None and lon is not None
        matched_by_coords = True
        countries = default_countries().countries_for_point(lat, lon)
        covered = [code for code in countries if code in COVERED_TERRITORIES]
        if not covered:
            return _outside_coverage_response(
                idx,
                lat=lat,
                lon=lon,
                country=countries[0] if countries else None,
                limit=limit,
                offset=offset,
                note=(
                    "Coordinates lie outside French territories covered by GASPAR CatNat "
                    "(metropolitan France FR and overseas departments GP, MQ, GF, RE, YT)."
                ),
            )
        entry, centroid_distance_km = _nearest_commune(lat, lon, covered, idx)
        territory = entry.territory

    all_events = _unpack_events(entry.events_packed)
    target_type_idx = idx.idx_by_slug[hazard_type] if hazard_type is not None else None

    filtered: list[tuple[int, str, str, str]] = []
    for ev in all_events:
        type_idx, start_d, end_d, decree_d = ev
        if target_type_idx is not None and type_idx != target_type_idx:
            continue
        ev_year = _event_year(start_d, end_d, decree_d)
        if since_year is not None and (ev_year is None or ev_year < since_year):
            continue
        if until_year is not None and (ev_year is None or ev_year > until_year):
            continue
        filtered.append(ev)

    type_counts: Counter[int] = Counter()
    year_counts: Counter[int] = Counter()
    first_decree_date: Optional[str] = None
    last_decree_date: Optional[str] = None

    for type_idx, start_d, end_d, decree_d in filtered:
        type_counts[type_idx] += 1
        ev_year = _event_year(start_d, end_d, decree_d)
        if ev_year is not None:
            year_counts[ev_year] += 1

        start_anchor = start_d or decree_d or end_d
        if start_anchor and (first_decree_date is None or start_anchor < first_decree_date):
            first_decree_date = start_anchor

        latest_anchor = decree_d or start_d or end_d
        if latest_anchor and (last_decree_date is None or latest_anchor > last_decree_date):
            last_decree_date = latest_anchor

    by_type = [
        CatNatTypeCount(
            category=idx.type_by_idx[type_idx][0],
            type_label=idx.type_by_idx[type_idx][1],
            count=count,
        )
        for type_idx, count in sorted(type_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    by_year = [
        CatNatYearCount(year=year, count=count)
        for year, count in sorted(year_counts.items(), key=lambda item: -item[0])
    ]

    page = filtered[offset : offset + limit] if limit > 0 else []
    decrees = [
        CatNatDecree(
            category=idx.type_by_idx[type_idx][0],
            type_label=idx.type_by_idx[type_idx][1],
            start_date=start_d or None,
            end_date=end_d or None,
            decree_date=decree_d or None,
        )
        for type_idx, start_d, end_d, decree_d in page
    ]

    derived = ["category", "summary"]
    if matched_by_coords:
        derived = ["commune_match", "centroid_distance_km", *derived]

    status = "available" if all_events else "confirmed_none"
    note_parts = [
        f"Offline TACCT extraction of GASPAR CatNat decrees (data.gouv.fr vintage {idx.dataset_updated}, decrees through {idx.period_end}).",
    ]
    if matched_by_coords:
        note_parts.append(
            f"Matched by nearest commune centroid ({centroid_distance_km} km from point); "
            "commune polygons are not bundled so border points may snap to a neighbouring centroid."
        )
    note_parts.append("Official decrees published in the Journal officiel and Géorisques remain authoritative.")

    return CatNatHistoryResponse(
        status=status,
        lat=lat,
        lon=lon,
        country=territory,
        commune=CatNatCommuneMatch(
            insee_code=entry.code,
            name=entry.name,
            department=entry.dept,
            centroid_lat=entry.lat,
            centroid_lon=entry.lon,
            centroid_distance_km=centroid_distance_km,
        ),
        dataset_updated=idx.dataset_updated,
        period_start=idx.period_start,
        period_end=idx.period_end,
        total_decrees=len(all_events),
        filtered_decrees=len(filtered),
        first_decree_date=first_decree_date,
        last_decree_date=last_decree_date,
        by_type=by_type,
        by_year=by_year,
        limit=limit,
        offset=offset,
        returned=len(decrees),
        decrees=decrees,
        derived=derived,
        note=" ".join(note_parts),
        attribution=credit_text(*GASPAR_CATNAT),
    )
