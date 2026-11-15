"""The nearest wave buoy of each surf spot, and how far it is.

The stations are ``app/data/candhis-stations.json`` (CANDHIS, Cerema, Licence Ouverte 2.0; ``tools/build_buoys.py``). The
distance is calculated by Vigie (``derived``). A buoy is `comparable` with a spot only when it is real time **and** at most
``MAX_COMPARABLE_KM`` away: that radius is a setting of ours, not a fact. How exposed the spot is compared with the buoy (a
sheltered spot behind a headland) is **not** judged: only the distance is.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.attribution import credit_text
from app.collectors.base import CollectorUnavailableError
from app.collectors.candhis import BuoyReading, CandhisCollector
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
READING_NOTE = (
    "The buoy is the nearest located buoy whose latest record is at most `max_age_hours` old: a nearer buoy that has stopped is passed "
    "over. Only the buoys of the CANDHIS list of 2024-12-03 have a position here; another one is not used. The distance, `comparable` and "
    "`age_minutes` are calculated by Vigie. A buoy measures the sea where it floats: how sheltered the spot is compared with it is not judged."
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


async def spot_reading(
    collector: Optional[CandhisCollector],
    spot_id: str,
    now_unix: float,
    surf_collector: Optional[SurfForecastCollector] = None,
    compare: bool = False,
) -> SpotBuoyReading:
    """The latest record of the nearest buoy that has a current one, for a surf spot (``surf.UnknownSpot`` for an unknown id).
    Never raises for a provider that fails: ``status`` says why there is no record."""
    spot = surf.find_spot(spot_id)
    base_derived = ["buoy", "distance_km", "comparable", "age_minutes", "measured_at", "max_age_hours", "max_comparable_km"]

    def answer(status: str, comparison: Optional[BuoyForecastComparison] = None, **more: Any) -> SpotBuoyReading:
        credits = ("candhis_tr", "copernicus_mfwam", "open_meteo") if comparison and comparison.status == "ok" else ("candhis_tr",)
        return SpotBuoyReading(
            spot_id=spot.id,
            spot_name=spot.name,
            status=status,  # type: ignore[arg-type]
            comparison=comparison,
            max_age_hours=MAX_AGE_S / 3600,
            max_comparable_km=MAX_COMPARABLE_KM,
            note=READING_NOTE,
            derived=[*base_derived, "comparison"] if comparison is not None else base_derived,
            licence="Licence Ouverte 2.0",
            attribution=credit_text(*credits),
            **more,
        )

    if collector is None:
        return answer("not_configured")
    try:
        latest = await collector.fetch_latest()
    except CollectorUnavailableError:
        return answer("unavailable")
    located = {b.id: b for b in stations()}
    candidates = [
        (haversine_km(spot.lat, spot.lon, located[sid].lat, located[sid].lon), located[sid], reading)
        for sid, reading in latest.readings.items()
        if sid in located and -MAX_AHEAD_S <= now_unix - reading.measured_utc <= MAX_AGE_S
    ]
    complete = not latest.failed_kinds  # a kind that could not be read hides buoys: the nearest one read may not be the nearest
    if not candidates:
        return answer("no_current_reading", stale=latest.stale, complete=complete)
    distance, buoy, reading = min(candidates, key=lambda c: c[0])
    comparison = await _compare_with_forecast(surf_collector, spot, reading) if compare and surf_collector is not None else None
    return answer(
        "ok",
        comparison=comparison,
        buoy=buoy,
        distance_km=round(distance, 1),
        comparable=distance <= MAX_COMPARABLE_KM,
        measure=_measure(reading, now_unix),
        stale=latest.stale,
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
