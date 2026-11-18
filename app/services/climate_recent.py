"""The last days a Météo-France station near a position measured (``GET /v1/climate/recent``), the complement of the normals of :mod:`app.services.climate`.

The stations are the ones of the climate sheets (``app/data/meteofrance-climate-normals.json.gz``): the nearest that **still measures** within
``max_distance_km`` answers, and if it has nothing for the days asked (a station can be closed to the daily data, or silent for a while) the next
two nearest are tried. Vigie works out the choice of the station and its distance (``derived``); the values are Météo-France's, left out when it marks
them doubtful. The days come from the gateway (one order per station and per day at most, ADR 0034).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.attribution import credit_text
from app.collectors.meteofrance_station_daily import MeteoFranceStationDailyCollector, StationDaily
from app.models.climate import ClimateStation
from app.models.climate_recent import ClimateDay, ClimateRecentNormals, ClimateRecentResponse
from app.services.climate import ClimateIndex, _nearest, _normal, _Station, default_index

CREDIT = ("meteofrance_station_daily", "meteofrance_climate")
MAX_CANDIDATES = 3
NOTE_MEASURES = (
    "Measures of the station named in `station` (altitude {alt} m, {distance:.1f} km from the position), as Météo-France's daily climatological data give "
    "them, validated or only filtered at the time they were read (a measure under verification is left out and named in `doubtful`). The rain of a day is "
    "the one of 06:00 UTC that day to 06:00 UTC the next. Yesterday is published from 11:30 UTC. A station at another altitude than the position can differ "
    "by several degrees. `normals_month` is the mean of the whole month, not the normal of a day."
)


def _station(candidate: _Station, distance: float) -> ClimateStation:
    return ClimateStation(
        id=candidate.id,
        name=candidate.name,
        lat=candidate.lat,
        lon=candidate.lon,
        altitude_m=candidate.alt,
        distance_km=round(distance, 1),
        sheet_edited=candidate.edited,
        records_until=candidate.records_until,
        active=candidate.active,
    )


def _candidates(lat: float, lon: float, max_distance_km: float, index: ClimateIndex) -> list[tuple[float, _Station]]:
    """The nearest stations that still measure, within the distance, nearest first."""
    return [(d, s) for d, s in _nearest(lat, lon, index) if d <= max_distance_km and s.active][:MAX_CANDIDATES]


def _normals(candidate: _Station, last_day: str) -> ClimateRecentNormals:
    month = date.fromisoformat(last_day).month
    tmax = _normal(candidate, "tmax_mean", month)
    tmin = _normal(candidate, "tmin_mean", month)
    return ClimateRecentNormals(month=month, tmax_mean_c=tmax.value, tmin_mean_c=tmin.value, period=tmax.period or tmin.period)


async def query_climate_recent(
    collector: Optional[MeteoFranceStationDailyCollector],
    *,
    lat: float,
    lon: float,
    days: int,
    max_distance_km: float,
    index: Optional[ClimateIndex] = None,
) -> ClimateRecentResponse:
    """The last ``days`` days at the nearest station that has them."""
    attribution = credit_text(*CREDIT)
    base = {"lat": lat, "lon": lon, "attribution": attribution}
    if collector is None:
        return ClimateRecentResponse(
            status="not_configured",
            note="The daily climatological data of Météo-France are read through a vigie-meteo gateway: set VIGIE_METEO_GATEWAY_URL.",
            **base,
        )
    idx = index or default_index()
    ranked = _candidates(lat, lon, max_distance_km, idx)
    if not ranked:
        nearest = _nearest(lat, lon, idx)
        far = f" (the nearest measuring station is {nearest[0][0]:.1f} km away)" if nearest else ""
        return ClimateRecentResponse(
            status="outside_coverage",
            note=f"No Météo-France station that still measures within {max_distance_km:g} km{far}; nothing is estimated.",
            **base,
        )
    gateway_failed = False
    for distance, candidate in ranked:
        try:
            result: StationDaily = await collector.fetch_station(candidate.id)
        except Exception:
            gateway_failed = True
            break  # the gateway (or the portal) does not answer: another station would not change that
        rows: list[ClimateDay] = list(result.days)[-days:]
        if not rows:
            continue
        return ClimateRecentResponse(
            status="available",
            station=_station(candidate, distance),
            last_day=result.last_day,
            fetched_at_utc=result.fetched_at_utc,
            days=rows,
            normals_month=_normals(candidate, rows[-1].date),
            derived=["station", "station.distance_km"],
            note=NOTE_MEASURES.format(alt=candidate.alt, distance=distance),
            **base,
        )
    if gateway_failed:
        return ClimateRecentResponse(
            status="unavailable", note="The daily climatological data of Météo-France cannot be read right now; try again later.", **base
        )
    nearest_station = _station(ranked[0][1], ranked[0][0])
    return ClimateRecentResponse(
        status="no_recent_data",
        station=nearest_station,
        note=f"The {len(ranked)} nearest stations that still measure have no daily data for the last days: none is shown.",
        **base,
    )
