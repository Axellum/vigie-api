"""The earthquakes near a position (``GET /v1/earthquakes``): the hazards the seismic sources listed, and the history the API kept.

The feeds keep a short window (USGS the last day, EMSC its last 40 events), so an earthquake of the last days comes from the
history of hazards the API records at each reading; ``listed_now`` tells the two apart. Two sources that report the same event
are one earthquake (``deduplicate_hazards``), whether they are in the feeds now or only in the history. Nothing here asks a provider.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.attribution import SIMULATED_ID_PREFIX, hazard_credit, merge_credits
from app.models.earthquakes import EarthquakesQuery, EarthquakesResponse, NearbyEarthquake
from app.models.schemas import HazardAlert, HazardCategory
from app.models.watch import WatchedSource
from app.services.geodesy import _are_duplicate_hazards, deduplicate_hazards, enrich_hazard_for_point, haversine_km

# The feeds the collectors read list nothing smaller (``app/collectors/seismic.py``: USGS `4.5_day`, EMSC `minmag=4.5`).
FEEDS_MIN_MAGNITUDE = 4.5

DEFAULT_RADIUS_KM = 300.0
MAX_RADIUS_KM = 3000.0
DEFAULT_HOURS = 24.0
MAX_HOURS = 168.0  # a week of history
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _is_quake(hazard: HazardAlert) -> bool:
    return hazard.category == HazardCategory.EARTHQUAKE and not hazard.id.startswith(SIMULATED_ID_PREFIX)


def ids_within(positions: Iterable[tuple[str, float, float]], lat: float, lon: float, radius_km: float) -> list[str]:
    """The ids of the history whose position is within ``radius_km`` of the point."""
    return [hazard_id for hazard_id, h_lat, h_lon in positions if haversine_km(lat, lon, h_lat, h_lon) <= radius_km]


def build_earthquakes(
    query: EarthquakesQuery,
    active: Iterable[HazardAlert],
    history: Iterable[HazardAlert],
    sources: list[WatchedSource],
    now_utc: int,
    limit: int,
    sources_credit: str | None,
) -> EarthquakesResponse:
    """The earthquakes of ``active`` (listed by a feed now) and ``history`` (kept by the API) that match the query."""
    start_utc = now_utc - int(query.hours * 3600)
    listed = [h for h in active if _is_quake(h)]
    listed_ids = {h.id for h in listed}
    kept = deduplicate_hazards(h for h in history if _is_quake(h) and h.id not in listed_ids)
    kept = [h for h in kept if not any(_are_duplicate_hazards(a, h) for a in listed)]

    found: list[NearbyEarthquake] = []
    for hazard, listed_now in [(h, True) for h in listed] + [(h, False) for h in kept]:
        if hazard.value_primary is None or hazard.value_primary < query.min_magnitude or hazard.timestamp_utc < start_utc:
            continue
        reporters = list(hazard.sources or [hazard.source])
        near = enrich_hazard_for_point(hazard, ref_lat=query.lat, ref_lon=query.lon)
        if near.distance_to_zone_km is None or near.distance_to_zone_km > query.radius_km:
            continue
        found.append(
            NearbyEarthquake(
                id=hazard.id,
                title=hazard.title,
                magnitude=hazard.value_primary,
                magnitude_type=hazard.value_unit,
                time_utc=hazard.timestamp_utc,
                lat=hazard.lat,
                lon=hazard.lon,
                depth_km=hazard.depth_km,
                tsunami_warning=hazard.tsunami_warning,
                sources=reporters,
                distance_km=round(near.distance_to_zone_km, 1),
                bearing_deg=None if near.bearing_deg is None else round(near.bearing_deg, 1),
                bearing_cardinal=near.bearing_cardinal,
                listed_now=listed_now,
                attribution=hazard_credit(hazard.id, reporters),  # the event's sources only: no `near_places` here, so not their credit
            )
        )
    found.sort(key=lambda q: (q.magnitude, q.time_utc), reverse=True)
    shown = found[:limit]
    return EarthquakesResponse(
        generated_utc=now_utc,
        query=query,
        feeds_min_magnitude=FEEDS_MIN_MAGNITUDE,
        sources=sources,
        count=len(found),
        earthquakes=shown,
        attribution=merge_credits(sources_credit, *(q.attribution for q in shown)),
    )
