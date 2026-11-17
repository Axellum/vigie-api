"""Offline lookup of the climate normals and records of Météo-France (the "Fiches climatologiques"), ADR 0027.

The bundle (``app/data/meteofrance-climate-normals.json.gz``, built by ``tools/build_climate_normals.py``) holds, for each
station that has a sheet, the monthly normals and records the sheet gives. Nothing is fetched at run time.

What Vigie adds, and names in ``derived``: the choice of the station (the nearest that still measures, within
``max_distance_km``), its distance, and the comparison of a value the caller gives with the normal and the record of the
month. A normal is a mean of the month: it is not the normal of one day, so a day of rain is only set against the record.
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app import clock
from app.attribution import METEOFRANCE_CLIMATE, credit_text
from app.models.climate import (
    ClimateComparison,
    ClimateComparisons,
    ClimateNormal,
    ClimateNormals,
    ClimateNormalsResponse,
    ClimateRecord,
    ClimateRecords,
    ClimateStation,
)
from app.services.geodesy import haversine_km

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "meteofrance-climate-normals.json.gz"
DEFAULT_MAX_DISTANCE_KM = 50.0
# A station whose records end more than this before the latest edition of the sheets no longer measures.
ACTIVE_WITHIN_DAYS = 45
YEAR_COLUMN = 13


@dataclass(frozen=True, slots=True)
class _Station:
    id: str
    name: str
    lat: float
    lon: float
    alt: int
    edited: Optional[str]
    period: Optional[str]
    blocks: dict[str, Any]
    records_until: Optional[str]
    active: bool


@dataclass(frozen=True, slots=True)
class ClimateIndex:
    dataset_updated: Optional[str]
    stations: tuple[_Station, ...]


def _records_until(blocks: dict[str, Any]) -> Optional[str]:
    ends = [block["p"][1] for key, block in blocks.items() if key.endswith("_record") and block.get("p")]
    return max(ends) if ends else None


def load_index(raw: dict[str, Any]) -> ClimateIndex:
    dataset_updated = raw.get("dataset_updated")
    cutoff = (date.fromisoformat(dataset_updated) - timedelta(days=ACTIVE_WITHIN_DAYS)).isoformat() if dataset_updated else None
    stations = []
    for row in raw["stations"]:
        blocks = row["blocks"]
        if "tmean" not in blocks and "tmax_mean" not in blocks:
            continue  # no temperature normals: nothing this route can say is normal
        until = _records_until(blocks)
        period = "-".join(row["period"]) if row.get("period") else None
        stations.append(
            _Station(
                id=row["id"],
                name=row["name"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                alt=int(row["alt"]),
                edited=row.get("edited"),
                period=period,
                blocks=blocks,
                records_until=until,
                active=bool(until and cutoff and until >= cutoff),
            )
        )
    return ClimateIndex(dataset_updated=dataset_updated, stations=tuple(stations))


@lru_cache(maxsize=1)
def default_index() -> ClimateIndex:
    """Load and cache the bundled sheets."""
    with gzip.open(DATA_FILE, "rt", encoding="utf-8") as fh:
        return load_index(json.load(fh))


def _nearest(lat: float, lon: float, index: ClimateIndex) -> list[tuple[float, _Station]]:
    """Every station with its distance, nearest first (a squared flat pass, then the exact distance on the closest)."""
    cos_lat = math.cos(math.radians(lat))
    rough = sorted(
        index.stations,
        key=lambda s: (s.lat - lat) ** 2 + (((s.lon - lon + 180.0) % 360.0 - 180.0) * cos_lat) ** 2,
    )
    exact = [(haversine_km(lat, lon, s.lat, s.lon), s) for s in rough[:40]]
    exact.sort(key=lambda item: item[0])
    return exact


def choose_station(lat: float, lon: float, max_distance_km: float, index: ClimateIndex) -> tuple[Optional[_Station], float]:
    """The nearest station that still measures within ``max_distance_km``, else the nearest one there; and the distance."""
    ranked = _nearest(lat, lon, index)
    if not ranked:
        return None, math.inf
    within = [(d, s) for d, s in ranked if d <= max_distance_km]
    if not within:
        return None, ranked[0][0]
    active = [(d, s) for d, s in within if s.active]
    distance, station = (active or within)[0]
    return station, distance


def _normal(station: _Station, key: str, month: int) -> ClimateNormal:
    block = station.blocks.get(key)
    if block is None:
        return ClimateNormal()
    period = "-".join(block["p"]) if block.get("p") else station.period
    return ClimateNormal(value=block["v"][month - 1], period=period)


def _record(station: _Station, key: str, column: int) -> ClimateRecord:
    block = station.blocks.get(key)
    if block is None:
        return ClimateRecord()
    since, until = (block.get("p") or [None, None])[:2]
    dates = block.get("d") or [None] * YEAR_COLUMN
    return ClimateRecord(value=block["v"][column - 1], date=dates[column - 1], since=since, until=until)


def _records(station: _Station, column: int) -> ClimateRecords:
    return ClimateRecords(
        tmax_c=_record(station, "tmax_record", column),
        tmin_c=_record(station, "tmin_record", column),
        rain_day_mm=_record(station, "rain_day_record", column),
        gust_ms=_record(station, "gust_record", column),
    )


def _compare(value: float, normal: Optional[float], record: ClimateRecord, *, lower: bool, active: bool) -> ClimateComparison:
    beats: Optional[bool] = None
    if record.value is not None and active:
        beats = value < record.value if lower else value > record.value
    return ClimateComparison(
        value=value,
        normal=normal,
        difference=round(value - normal, 1) if normal is not None else None,
        record=record.value,
        record_date=record.date,
        beats_record=beats,
    )


def current_month() -> int:
    return datetime.fromtimestamp(clock.wall(), tz=timezone.utc).month


def query_climate_normals(
    *,
    lat: float,
    lon: float,
    month: Optional[int] = None,
    max_distance_km: float = DEFAULT_MAX_DISTANCE_KM,
    tmax: Optional[float] = None,
    tmin: Optional[float] = None,
    rain_mm: Optional[float] = None,
    index: Optional[ClimateIndex] = None,
) -> ClimateNormalsResponse:
    """The normals and records of the month at the station that answers for a position, and the comparison asked for."""
    idx = index or default_index()
    month = month or current_month()
    attribution = credit_text(*METEOFRANCE_CLIMATE)
    station, distance = choose_station(lat, lon, max_distance_km, idx)
    if station is None:
        nearest = None if math.isinf(distance) else round(distance, 1)
        return ClimateNormalsResponse(
            status="outside_coverage",
            lat=lat,
            lon=lon,
            month=month,
            nearest_distance_km=nearest,
            dataset_updated=idx.dataset_updated,
            derived=["nearest_distance_km"] if nearest is not None else [],
            note=(
                f"No Météo-France climate sheet within {max_distance_km:g} km"
                + (f" (the nearest is {nearest} km away)" if nearest is not None else "")
                + ". The sheets cover France and its overseas territories; nothing is estimated elsewhere."
            ),
            attribution=attribution,
        )

    normals = ClimateNormals(
        tmax_mean_c=_normal(station, "tmax_mean", month),
        tmin_mean_c=_normal(station, "tmin_mean", month),
        tmean_c=_normal(station, "tmean", month),
        rain_mm=_normal(station, "rain_mean", month),
        rain_days=_normal(station, "rain_days", month),
        frost_days=_normal(station, "frost_days", month),
        hot_days=_normal(station, "hot_days", month),
        sunshine_h=_normal(station, "sunshine_mean", month),
    )
    records = _records(station, month)

    comparison: Optional[ClimateComparisons] = None
    if tmax is not None or tmin is not None or rain_mm is not None:
        comparison = ClimateComparisons(
            tmax_c=_compare(tmax, normals.tmax_mean_c.value, records.tmax_c, lower=False, active=station.active) if tmax is not None else None,
            tmin_c=_compare(tmin, normals.tmin_mean_c.value, records.tmin_c, lower=True, active=station.active) if tmin is not None else None,
            rain_mm=(
                _compare(rain_mm, None, records.rain_day_mm, lower=False, active=station.active) if rain_mm is not None else None
            ),
        )

    derived = ["station", "station.distance_km", "station.active"]
    if comparison is not None:
        derived.append("comparison")
    note = (
        f"Normals and records of the Météo-France station {station.name} (altitude {station.alt} m), "
        f"{distance:.1f} km from the position, as its climate sheet gives them (edited {station.edited or 'on an unknown date'}). "
        "A normal is the mean of the whole month, not of one day; a station at another altitude than the position can differ by "
        "several degrees."
    )
    if not station.active:
        note += f" This station no longer measures (records until {station.records_until}): a record is not set against a value."
    return ClimateNormalsResponse(
        status="available",
        lat=lat,
        lon=lon,
        month=month,
        station=ClimateStation(
            id=station.id,
            name=station.name,
            lat=station.lat,
            lon=station.lon,
            altitude_m=station.alt,
            distance_km=round(distance, 1),
            sheet_edited=station.edited,
            records_until=station.records_until,
            active=station.active,
        ),
        dataset_updated=idx.dataset_updated,
        normals=normals,
        records=records,
        records_all_year=_records(station, YEAR_COLUMN),
        comparison=comparison,
        derived=derived,
        note=note,
        attribution=attribution,
    )
