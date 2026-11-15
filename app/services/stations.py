"""The nearest coastal weather station of each surf spot, and what its anemometer measures now.

The stations are ``app/data/meteofrance-coastal-stations.json`` (Météo-France ``POSTES_MF.csv``, Licence Ouverte 2.0;
``tools/build_coastal_stations.py``). The distance and the relative wind regime (``offshore``, ``cross-shore``, ``onshore``) are
calculated by Vigie (``derived``). A station is ``comparable`` with a spot when it is at most ``MAX_COMPARABLE_KM`` away: that
radius is a setting of ours, not a fact. Local topography between the station and the beach (a cliff, a dune or an estuary) is
**not** judged: only the distance is.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.attribution import credit_text
from app.collectors.base import CollectorUnavailableError
from app.collectors.meteofrance_obs import MeteoFranceObsCollector, StationReading
from app.collectors.surf import SurfForecastCollector
from app.models.stations import (
    CoastalStation,
    SpotStation,
    SpotStationReading,
    SpotStations,
    StationMeasure,
    StationWindComparison,
)
from app.services import surf
from app.services.geodesy import haversine_km

STATIONS_FILE = Path(__file__).resolve().parents[1] / "data" / "meteofrance-coastal-stations.json"
MAX_COMPARABLE_KM = 25.0
# DPPaquetObs publishes a new observation every 6 minutes; one older than an hour is no longer "the wind now".
MAX_AGE_S = 3600
MAX_AHEAD_S = 15 * 60
COMPARE_MAX_DIFF_S = 30 * 60
WIND_MODEL = "Open-Meteo hourly wind forecast"
READING_NOTE = (
    "The station is the nearest coastal Météo-France station whose latest 6-minute wind observation is at most `max_age_hours` old: "
    "a nearer station without a wind reading is passed over. The distance, `comparable`, `age_minutes` and `wind_relative` are "
    "calculated by Vigie. Local terrain between the station and the beach is not judged."
)
NOTE = (
    "The distance is calculated by Vigie from the open real-time stations (`TYPE_POSTE_ACTUEL` 0, 1 or 2) of Météo-France's "
    "`POSTES_MF.csv` file within 35 km of at least one surf spot. `comparable` rests on the distance alone (`<= max_comparable_km`), "
    "not on local shelter or headland effects."
)


@lru_cache(maxsize=1)
def stations() -> list[CoastalStation]:
    data: dict[str, Any] = json.loads(STATIONS_FILE.read_text(encoding="utf-8"))
    return [CoastalStation(**s) for s in data["stations"]]


def _nearest(lat: float, lon: float, pool: list[CoastalStation]) -> tuple[CoastalStation, float] | None:
    best = min(((haversine_km(lat, lon, s.lat, s.lon), s) for s in pool), key=lambda t: t[0], default=None)
    return None if best is None else (best[1], best[0])


def _measure(reading: StationReading, spot: surf.SurfSpot, now_unix: float) -> StationMeasure:
    return StationMeasure(
        measured_utc=reading.measured_utc,
        measured_at=datetime.fromtimestamp(reading.measured_utc, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        age_minutes=max(0, int((now_unix - reading.measured_utc) // 60)),
        wind_speed_kmh=reading.wind_speed_kmh,
        wind_gust_kmh=reading.wind_gust_kmh,
        wind_from_deg=reading.wind_from_deg,
        wind_relative=surf.wind_relative(reading.wind_from_deg, spot.facing_deg),
        air_temp_c=reading.air_temp_c,
        humidity_pct=reading.humidity_pct,
        pressure_hpa=reading.pressure_hpa,
        precip_6m_mm=reading.precip_6m_mm,
    )


async def _compare_with_forecast(
    surf_collector: SurfForecastCollector, spot: surf.SurfSpot, measure: StationMeasure
) -> StationWindComparison:
    """Compare ``measure`` with the spot's hourly wind forecast at the nearest hour within ``COMPARE_MAX_DIFF_S``."""
    try:
        wind, wind_stale = await surf_collector.fetch_wind(spot.id, spot.lat, spot.lon)
    except Exception:  # noqa: BLE001 - a failing wind forecast never fails the station observation
        return StationWindComparison(status="unavailable", wind_model=WIND_MODEL)
    times: list[int] = wind.get("times") or []
    matching = [(abs(t - measure.measured_utc), i, t) for i, t in enumerate(times) if abs(t - measure.measured_utc) <= COMPARE_MAX_DIFF_S]
    if not matching:
        return StationWindComparison(status="no_matching_hour", wind_model=WIND_MODEL, stale=wind_stale)
    _, index, forecast_time = min(matching, key=lambda item: item[0])
    model_speed = surf._round(surf._at(wind, "wind_speed_10m", index), 1)
    model_gust = surf._round(surf._at(wind, "wind_gusts_10m", index), 1)
    model_dir = surf._direction(surf._at(wind, "wind_direction_10m", index))
    model_rel = surf.wind_relative(model_dir, spot.facing_deg)

    speed_diff = None if model_speed is None else round(measure.wind_speed_kmh - model_speed, 1)
    gust_diff = (
        None
        if measure.wind_gust_kmh is None or model_gust is None
        else round(measure.wind_gust_kmh - model_gust, 1)
    )
    dir_diff = (
        None
        if measure.wind_from_deg is None or model_dir is None
        else int(round(surf.angle_between(measure.wind_from_deg, model_dir)))
    )
    regime_match = (
        None
        if measure.wind_relative is None or model_rel is None
        else (measure.wind_relative == model_rel)
    )
    return StationWindComparison(
        status="ok",
        wind_model=WIND_MODEL,
        forecast_time_utc=forecast_time,
        model_wind_speed_kmh=model_speed,
        speed_diff_kmh=speed_diff,
        model_wind_gust_kmh=model_gust,
        gust_diff_kmh=gust_diff,
        model_wind_from_deg=model_dir,
        model_wind_relative=model_rel,
        direction_diff_deg=dir_diff,
        regime_match=regime_match,
        stale=wind_stale,
    )


async def spot_reading(
    collector: Optional[MeteoFranceObsCollector],
    spot_id: str,
    now_unix: float,
    surf_collector: Optional[SurfForecastCollector] = None,
    compare: bool = False,
) -> SpotStationReading:
    """The latest 6-minute observation of the nearest coastal station that has a current wind reading, for a surf spot."""
    spot = surf.find_spot(spot_id)
    base_derived = [
        "station",
        "distance_km",
        "comparable",
        "age_minutes",
        "measured_at",
        "wind_relative",
        "max_age_hours",
        "max_comparable_km",
    ]

    def answer(status: str, comparison: Optional[StationWindComparison] = None, **more: Any) -> SpotStationReading:
        credits = ("meteofrance_obs", "open_meteo") if comparison and comparison.status == "ok" else ("meteofrance_obs",)
        return SpotStationReading(
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
    located = {s.id: s for s in stations()}
    candidates = [
        (haversine_km(spot.lat, spot.lon, located[sid].lat, located[sid].lon), located[sid], reading)
        for sid, reading in latest.readings.items()
        if sid in located and -MAX_AHEAD_S <= now_unix - reading.measured_utc <= MAX_AGE_S
    ]
    if not candidates:
        return answer("no_current_reading", stale=latest.stale)
    distance, station, reading = min(candidates, key=lambda c: c[0])
    measure = _measure(reading, spot, now_unix)
    comparison = await _compare_with_forecast(surf_collector, spot, measure) if compare and surf_collector is not None else None
    return answer(
        "ok",
        comparison=comparison,
        station=station,
        distance_km=round(distance, 1),
        comparable=distance <= MAX_COMPARABLE_KM,
        measure=measure,
        stale=latest.stale,
    )


def spot_stations() -> SpotStations:
    all_stations = stations()
    rows: list[SpotStation] = []
    for spot in surf.spots().spots:
        near = _nearest(spot.lat, spot.lon, all_stations)
        assert near is not None
        rows.append(
            SpotStation(
                spot_id=spot.id,
                spot_name=spot.name,
                station=near[0],
                distance_km=near[1],
                comparable=near[1] <= MAX_COMPARABLE_KM,
            )
        )
    return SpotStations(
        count=len(rows),
        max_comparable_km=MAX_COMPARABLE_KM,
        within_15_km=sum(r.distance_km <= 15 for r in rows),
        within_25_km=sum(r.distance_km <= 25 for r in rows),
        spots=rows,
        note=NOTE,
        derived=["distance_km", "comparable", "max_comparable_km", "within_15_km", "within_25_km"],
        licence="Licence Ouverte 2.0",
        attribution=credit_text("meteofrance_stations"),
    )
