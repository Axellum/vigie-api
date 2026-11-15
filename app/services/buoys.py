"""The nearest wave buoy of each surf spot, and how far it is.

The stations are ``app/data/candhis-stations.json`` (CANDHIS, Cerema, Licence Ouverte 2.0; ``tools/build_buoys.py``). The
distance is calculated by Vigie (``derived``). A buoy is `comparable` with a spot only when it is real time **and** at most
``MAX_COMPARABLE_KM`` away: that radius is a setting of ours, not a fact. How exposed the spot is compared with the buoy (a
sheltered spot behind a headland) is **not** judged: only the distance is.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.attribution import credit_text
from app.collectors.base import CollectorUnavailableError
from app.collectors.candhis import BuoyReading, CandhisCollector
from app.collectors.ndbc import NdbcCollector
from app.collectors.surf import SurfForecastCollector
from app.models.buoys import Buoy, BuoyForecastComparison, BuoyMeasure, SpotBuoy, SpotBuoyReading, SpotBuoys
from app.services import surf
from app.services.geodesy import haversine_km

STATIONS_FILE = Path(__file__).resolve().parents[1] / "data" / "candhis-stations.json"
MAX_COMPARABLE_KM = 30.0
# A record older than this is not "the sea now"; one dated further ahead of our clock is not believed (a buoy's clock is wrong).
# Both are settings of ours. CANDHIS gives one record an hour (30 minutes for some buoys): three hours is a missed transmission or two.
MAX_AGE_S = 3 * 3600
MAX_AHEAD_S = 30 * 60
COMPARE_MAX_DIFF_S = 30 * 60  # a buoy record is compared with the hourly forecast step within 30 minutes of `measured_utc`
CANDHIS_LICENCE = "Licence Ouverte 2.0"
NDBC_LICENCE = "Public domain (U.S. NWS disclaimer)"
READING_NOTE = (
    "The buoy is the nearest located buoy whose latest record is at most `max_age_hours` old: a nearer buoy that has stopped is passed "
    "over. CANDHIS buoys use the CANDHIS list of 2024-12-03 for coordinates; NOAA NDBC buoys use coordinates from `latest_obs.txt` and "
    "names from `activestations.xml`. The distance, `comparable` and `age_minutes` are calculated by Vigie. A buoy measures the sea "
    "where it floats: how sheltered the spot is compared with it is not judged."
)
NOTE = (
    "The distance is calculated by Vigie. The nearest station of the CANDHIS file may no longer measure: `real_time` is true only for "
    "stations CANDHIS marked real time when the list was read, null for the others. `comparable` rests on the distance alone, not on "
    "how sheltered the spot is."
)


@lru_cache(maxsize=1)
def stations() -> list[Buoy]:
    data: dict[str, Any] = json.loads(STATIONS_FILE.read_text(encoding="utf-8"))
    return [Buoy(**s) for s in data["stations"]]


def _nearest(lat: float, lon: float, pool: list[Buoy]) -> tuple[Buoy, float] | None:
    best = min(((haversine_km(lat, lon, b.lat, b.lon), b) for b in pool), key=lambda t: t[0], default=None)
    return None if best is None else (best[1], best[0])


def _measure(reading: BuoyReading, now_unix: float) -> BuoyMeasure:
    return BuoyMeasure(
        measured_utc=reading.measured_utc,
        measured_at=datetime.fromtimestamp(reading.measured_utc, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        age_minutes=max(0, int((now_unix - reading.measured_utc) // 60)),
        height_m=reading.height_m,
        height_kind=reading.height_kind,
        hmax_m=reading.hmax_m,
        period_s=reading.period_s,
        period_kind=reading.period_kind,
        peak_period_s=reading.peak_period_s,
        direction_deg=reading.direction_deg,
        spread_deg=reading.spread_deg,
        sea_temp_c=reading.sea_temp_c,
    )


async def _compare_with_forecast(
    surf_collector: SurfForecastCollector, spot: surf.SurfSpot, reading: BuoyReading
) -> BuoyForecastComparison:
    """Compare ``reading`` with the spot's MFWAM wave forecast at the nearest hour within ``COMPARE_MAX_DIFF_S``."""
    try:
        swell, swell_stale = await surf_collector.fetch_swell(spot.id, spot.lat, spot.lon)
    except Exception:  # noqa: BLE001 - a failing forecast never fails the buoy reading
        return BuoyForecastComparison(status="unavailable", swell_model=surf.SWELL_MODEL)
    cell = None
    if swell.get("model_lat") is not None and swell.get("model_lon") is not None:
        cell = {"lat": float(swell["model_lat"]), "lon": float(swell["model_lon"])}
    times: list[int] = swell.get("times") or []
    matching = [(abs(t - reading.measured_utc), i, t) for i, t in enumerate(times) if abs(t - reading.measured_utc) <= COMPARE_MAX_DIFF_S]
    if not matching:
        return BuoyForecastComparison(status="no_matching_hour", swell_model=surf.SWELL_MODEL, swell_cell=cell, stale=swell_stale)
    _, index, forecast_time = min(matching, key=lambda item: item[0])
    wave_h = surf._round(surf._at(swell, "wave_height", index), 2)
    wave_p = surf._round(surf._at(swell, "wave_period", index), 1)
    swell_h = surf._round(surf._at(swell, "swell_wave_height", index), 2)
    swell_p = surf._round(surf._at(swell, "swell_wave_period", index), 1)
    swell_dir = surf._direction(surf._at(swell, "swell_wave_direction", index))
    height_diff = None if wave_h is None else round(reading.height_m - wave_h, 2)
    height_ratio = None if wave_h is None or wave_h <= 0 else round(reading.height_m / wave_h, 2)
    dir_diff = (
        None
        if reading.direction_deg is None or swell_dir is None
        else int(round(surf.angle_between(reading.direction_deg, swell_dir)))
    )
    return BuoyForecastComparison(
        status="ok",
        swell_model=surf.SWELL_MODEL,
        swell_cell=cell,
        forecast_time_utc=forecast_time,
        model_wave_height_m=wave_h,
        height_diff_m=height_diff,
        height_ratio=height_ratio,
        model_wave_period_s=wave_p,
        model_swell_height_m=swell_h,
        model_swell_period_s=swell_p,
        model_swell_from_deg=swell_dir,
        direction_diff_deg=dir_diff,
        stale=swell_stale,
    )


def _is_current(measured_utc: int, now_unix: float) -> bool:
    return -MAX_AHEAD_S <= now_unix - measured_utc <= MAX_AGE_S


async def _fetch_candhis_candidates(
    collector: CandhisCollector, lat: float, lon: float, now_unix: float
) -> tuple[list[tuple[float, Buoy, BuoyReading, str, str, bool]], bool, bool]:
    """Return ``(candidates, stale, complete)`` from CANDHIS, or raise ``CollectorUnavailableError``."""
    latest = await collector.fetch_latest()
    located = {b.id: b for b in stations()}
    rows = [
        (haversine_km(lat, lon, located[sid].lat, located[sid].lon), located[sid], reading, "candhis_tr", CANDHIS_LICENCE, latest.stale)
        for sid, reading in latest.readings.items()
        if sid in located and _is_current(reading.measured_utc, now_unix)
    ]
    return rows, latest.stale, not latest.failed_kinds


async def _fetch_ndbc_candidates(
    collector: NdbcCollector, lat: float, lon: float, now_unix: float
) -> tuple[list[tuple[float, Buoy, BuoyReading, str, str, bool]], bool, bool]:
    """Return ``(candidates, stale, complete)`` from NOAA NDBC, or raise ``CollectorUnavailableError``."""
    latest = await collector.fetch_latest()
    rows: list[tuple[float, Buoy, BuoyReading, str, str, bool]] = []
    for sid, reading in latest.readings.items():
        station = latest.stations.get(sid)
        if station is None or not _is_current(reading.measured_utc, now_unix):
            continue
        buoy = Buoy(id=station.id, name=station.name, lat=station.lat, lon=station.lon, source="ndbc", real_time=True)
        rows.append((haversine_km(lat, lon, station.lat, station.lon), buoy, reading, "noaa_ndbc", NDBC_LICENCE, latest.stale))
    return rows, latest.stale, True


async def spot_reading(
    collector: Optional[CandhisCollector],
    spot_id: str,
    now_unix: float,
    surf_collector: Optional[SurfForecastCollector] = None,
    compare: bool = False,
    ndbc: Optional[NdbcCollector] = None,
) -> SpotBuoyReading:
    """The latest record of the nearest buoy (CANDHIS or NOAA NDBC) that has a current one, for a surf spot.

    Raises ``surf.UnknownSpot`` for an unknown id. Never raises for a provider that fails: ``status`` says why there is no record.
    """
    spot = surf.find_spot(spot_id)
    base_derived = ["buoy", "distance_km", "comparable", "age_minutes", "measured_at", "max_age_hours", "max_comparable_km"]

    def answer(
        status: str,
        credit_key: str = "candhis_tr",
        licence: str = CANDHIS_LICENCE,
        comparison: Optional[BuoyForecastComparison] = None,
        **more: Any,
    ) -> SpotBuoyReading:
        credits = (credit_key, "copernicus_mfwam", "open_meteo") if comparison and comparison.status == "ok" else (credit_key,)
        return SpotBuoyReading(
            spot_id=spot.id,
            spot_name=spot.name,
            status=status,  # type: ignore[arg-type]
            comparison=comparison,
            max_age_hours=MAX_AGE_S / 3600,
            max_comparable_km=MAX_COMPARABLE_KM,
            note=READING_NOTE,
            derived=[*base_derived, "comparison"] if comparison is not None else base_derived,
            licence=licence,
            attribution=credit_text(*credits),
            **more,
        )

    tasks: list[tuple[str, str, Any]] = []
    if collector is not None:
        tasks.append(("candhis_tr", CANDHIS_LICENCE, _fetch_candhis_candidates(collector, spot.lat, spot.lon, now_unix)))
    if ndbc is not None:
        tasks.append(("noaa_ndbc", NDBC_LICENCE, _fetch_ndbc_candidates(ndbc, spot.lat, spot.lon, now_unix)))
    if not tasks:
        return answer("not_configured")

    results = await asyncio.gather(*(coro for _, _, coro in tasks), return_exceptions=True)
    candidates: list[tuple[float, Buoy, BuoyReading, str, str, bool]] = []
    succeeded = 0
    any_stale = False
    complete = True
    last_credit, last_licence = tasks[0][0], tasks[0][1]
    for (credit_key, licence, _), outcome in zip(tasks, results, strict=True):
        last_credit, last_licence = credit_key, licence
        if isinstance(outcome, CollectorUnavailableError):
            complete = False
            continue
        if isinstance(outcome, BaseException):
            raise outcome
        rows, src_stale, src_complete = outcome
        succeeded += 1
        candidates.extend(rows)
        any_stale = any_stale or src_stale
        complete = complete and src_complete

    if succeeded == 0:
        return answer("unavailable", credit_key=last_credit, licence=last_licence)
    if not candidates:
        return answer("no_current_reading", credit_key=last_credit, licence=last_licence, stale=any_stale, complete=complete)

    distance, buoy, reading, credit_key, licence, source_stale = min(candidates, key=lambda c: c[0])
    comparison = await _compare_with_forecast(surf_collector, spot, reading) if compare and surf_collector is not None else None
    return answer(
        "ok",
        credit_key=credit_key,
        licence=licence,
        comparison=comparison,
        buoy=buoy,
        distance_km=round(distance, 1),
        comparable=distance <= MAX_COMPARABLE_KM,
        measure=_measure(reading, now_unix),
        stale=source_stale,
        complete=complete,
    )


def spot_buoys() -> SpotBuoys:
    all_stations = stations()
    live = [b for b in all_stations if b.real_time]
    rows: list[SpotBuoy] = []
    for spot in surf.spots().spots:
        near = _nearest(spot.lat, spot.lon, all_stations)
        near_live = _nearest(spot.lat, spot.lon, live)
        assert near is not None
        rows.append(
            SpotBuoy(
                spot_id=spot.id,
                spot_name=spot.name,
                buoy=near[0],
                distance_km=near[1],
                real_time_buoy=near_live[0] if near_live else None,
                real_time_distance_km=near_live[1] if near_live else None,
                comparable=bool(near_live and near_live[1] <= MAX_COMPARABLE_KM),
            )
        )
    return SpotBuoys(
        count=len(rows),
        max_comparable_km=MAX_COMPARABLE_KM,
        within_30_km=sum(r.distance_km <= 30 for r in rows),
        within_50_km=sum(r.distance_km <= 50 for r in rows),
        real_time_within_30_km=sum((r.real_time_distance_km or 1e9) <= 30 for r in rows),
        real_time_within_50_km=sum((r.real_time_distance_km or 1e9) <= 50 for r in rows),
        spots=rows,
        note=NOTE,
        derived=["distance_km", "real_time_distance_km", "comparable", "max_comparable_km", "within_30_km", "within_50_km"],
        licence="Licence Ouverte 2.0",
        attribution=credit_text("candhis_stations"),
    )
