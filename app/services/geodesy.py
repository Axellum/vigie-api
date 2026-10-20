"""Geodesy (haversine, bearing, tsunami ETA) and multi-provider de-duplication.

This module provides:

1. ``haversine_km``: great-circle distance on a mean Earth sphere (R = 6371.0088 km).
2. ``initial_bearing_deg`` and ``bearing_to_cardinal``: true azimuth (0-360 degrees) and
   cardinal point (N, NE, E, SE, S, SW, W, NW) from the user / vessel towards the threat.
3. ``estimate_tsunami_eta_minutes``: long-wave travel time of a tsunami from the mean ocean
   depth (v = sqrt(g * d)). It is an order-of-magnitude estimate, not a forecast.
4. ``deduplicate_hazards``: space-time merge across agencies (for example one earthquake
   reported by USGS, EMSC and GDACS), keeping the best score and the full source list.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

from app.models.schemas import SEVERITY_RANK, HazardAlert, HazardCategory, SeverityLevel

# IUGG mean Earth radius (kilometres)
EARTH_RADIUS_KM: float = 6371.0088
# Standard gravity (m/s^2)
GRAVITY_M_S2: float = 9.80665

CARDINAL_8: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# The sources that observe the event themselves, in the order their title and position are preferred when several report it.
FIRST_HAND_SOURCES: tuple[str, ...] = ("USGS", "NHC", "PTWC", "NTWC")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in kilometres between two WGS84 points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return round(EARTH_RADIUS_KM * c, 2)


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the initial bearing (true azimuth in degrees, [0.0, 360.0[) from point 1 to point 2.

    Lets a boat or a fixed station know at once in which direction an earthquake epicentre,
    a cyclone or a violent squall lies.
    """
    if math.isclose(lat1, lat2, abs_tol=1e-9) and math.isclose(lon1, lon2, abs_tol=1e-9):
        return 0.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)

    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    theta = math.degrees(math.atan2(y, x))
    return round((theta + 360.0) % 360.0, 1)


def bearing_to_cardinal(bearing_deg: float) -> str:
    """Convert an azimuth in [0, 360[ degrees to one of 8 cardinal directions (N, NE, E...)."""
    normalized = (bearing_deg + 360.0) % 360.0
    index = int((normalized + 22.5) // 45.0) % 8
    return CARDINAL_8[index]


def estimate_tsunami_eta_minutes(distance_km: float, avg_depth_m: float = 4000.0) -> int:
    """Estimate a tsunami arrival time (minutes) with the long-wave law.

    In deep ocean (d = 4000 m) the wave speed is c = sqrt(g * d), about 198 m/s or 713 km/h.
    Returns at least 1 minute when the distance is not zero.
    """
    if distance_km <= 0.0:
        return 0
    effective_depth = max(50.0, float(avg_depth_m))
    speed_m_s = math.sqrt(GRAVITY_M_S2 * effective_depth)
    speed_km_h = speed_m_s * 3.6
    eta_hours = distance_km / speed_km_h
    return max(1, int(round(eta_hours * 60.0)))


def enrich_hazard_for_point(
    hazard: HazardAlert,
    ref_lat: float,
    ref_lon: float,
    zone_id: str | None = None,
    zone_name: str | None = None,
) -> HazardAlert:
    """Add the distance, the bearing (degrees and cardinal) and the tsunami ETA towards a reference point."""
    dist_km = haversine_km(ref_lat, ref_lon, hazard.lat, hazard.lon)
    bearing = initial_bearing_deg(ref_lat, ref_lon, hazard.lat, hazard.lon)
    cardinal = bearing_to_cardinal(bearing)
    tsunami_eta = (
        estimate_tsunami_eta_minutes(dist_km)
        if (hazard.tsunami_warning or hazard.category == HazardCategory.TSUNAMI)
        else None
    )

    return hazard.model_copy(
        update={
            "nearest_zone_id": zone_id,
            "nearest_zone_name": zone_name,
            "distance_to_zone_km": dist_km,
            "bearing_deg": bearing,
            "bearing_cardinal": cardinal,
            "tsunami_eta_minutes": tsunami_eta,
        }
    )


def _are_duplicate_hazards(
    a: HazardAlert,
    b: HazardAlert,
    max_time_diff_s: int = 120,
    max_dist_km: float = 100.0,
) -> bool:
    """Tell whether two alerts from different sources describe the same physical phenomenon."""
    if a.category != b.category:
        return False

    # Two reports that carry the key of an event (a storm's name and season, the VTEC code of a tsunami message) are the same event
    # when the keys are equal and two events when they are not, whatever their times and positions: GDACS dates a storm by its first
    # day, the NHC by its latest advisory, and the NWS places a tsunami warning in a zone, not at the epicentre.
    if a.event_key is not None and b.event_key is not None:
        return a.event_key == b.event_key

    time_diff = abs(a.timestamp_utc - b.timestamp_utc)
    dist_km = haversine_km(a.lat, a.lon, b.lat, b.lon)

    if a.category == HazardCategory.EARTHQUAKE:
        return time_diff <= max_time_diff_s and dist_km <= max_dist_km

    # Extended phenomena (cyclones, volcanoes, tsunamis, floods): 6 hour window and 150 km
    return time_diff <= 21600 and dist_km <= 150.0


def _max_known(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """Return the larger of two optional values, ignoring unknown ones."""
    known = [v for v in (a, b) if v is not None]
    return max(known) if known else None


def _merge_two_hazards(primary: HazardAlert, secondary: HazardAlert) -> HazardAlert:
    """Merge two alerts of the same event, keeping the best score and the list of sources."""
    # Keep the highest severity
    best_severity: SeverityLevel = (
        primary.severity
        if SEVERITY_RANK[primary.severity] >= SEVERITY_RANK[secondary.severity]
        else secondary.severity
    )

    # Canonical source order without duplicates
    merged_sources: list[str] = []
    for src in (primary.sources or [primary.source]) + (secondary.sources or [secondary.source]):
        for token in src.split("+"):
            clean = token.strip()
            if clean and clean not in merged_sources:
                merged_sources.append(clean)

    best_value = _max_known(primary.value_primary, secondary.value_primary)
    best_depth = primary.depth_km if primary.depth_km is not None else secondary.depth_km
    tsunami_flag = bool(primary.tsunami_warning or secondary.tsunami_warning)

    # Prefer the title and the coordinates of the first-hand source when there is one (USGS for an earthquake, the National Hurricane
    # Center for a storm it follows, the tsunami warning centres for their events), else the first alert
    preferred = primary
    for source in FIRST_HAND_SOURCES:
        if source in (primary.sources or [primary.source]):
            break
        if source in (secondary.sources or [secondary.source]):
            preferred = secondary
            break

    other = secondary if preferred is primary else primary
    value_unit = preferred.value_unit or primary.value_unit or secondary.value_unit
    # One storm, two reports: the National Hurricane Center says what the storm is now, GDACS what it has been at its strongest
    # (its alert level and its wind are those of the whole event). The hazard is the storm of now: the first-hand level and wind,
    # not the highest of the two. An earthquake keeps the highest (the sources estimate the same instant).
    same_event = preferred.event_key is not None and preferred.event_key == other.event_key
    if same_event and any(source in FIRST_HAND_SOURCES for source in (preferred.sources or [preferred.source])):
        best_severity, best_value, value_unit = preferred.severity, preferred.value_primary, preferred.value_unit
    return preferred.model_copy(
        update={
            "severity": best_severity,
            "source": "+".join(merged_sources),
            "sources": merged_sources,
            "value_primary": best_value,
            "value_unit": value_unit,
            "depth_km": best_depth,
            "tsunami_warning": tsunami_flag,
        }
    ).with_event_key(preferred.event_key or other.event_key).with_place_name(preferred.place_name or other.place_name)


def deduplicate_hazards(
    hazards: Iterable[HazardAlert],
    max_time_diff_s: int = 120,
    max_dist_km: float = 100.0,
) -> list[HazardAlert]:
    """De-duplicate and merge a multi-provider alert list (for example USGS + EMSC + GDACS).

    The result is sorted by decreasing severity, then decreasing known value, then by
    decreasing time (most recent first). An unknown value sorts after every known one.
    """
    merged_list: list[HazardAlert] = []

    for candidate in hazards:
        # Normalize the ``sources`` list when it is empty
        normalized_sources = candidate.sources or [
            s.strip() for s in candidate.source.split("+") if s.strip()
        ]
        cand = candidate.model_copy(
            update={
                "sources": normalized_sources,
                "source": "+".join(normalized_sources),
            }
        )

        matched_idx: int | None = None
        for idx, existing in enumerate(merged_list):
            if _are_duplicate_hazards(existing, cand, max_time_diff_s=max_time_diff_s, max_dist_km=max_dist_km):
                matched_idx = idx
                break

        if matched_idx is None:
            merged_list.append(cand)
        else:
            merged_list[matched_idx] = _merge_two_hazards(merged_list[matched_idx], cand)

    merged_list.sort(
        key=lambda h: (
            SEVERITY_RANK[h.severity],
            h.value_primary is not None,
            h.value_primary if h.value_primary is not None else 0.0,
            h.timestamp_utc,
        ),
        reverse=True,
    )
    return merged_list
