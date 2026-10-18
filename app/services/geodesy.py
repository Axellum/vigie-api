"""Moteur géodésique (Haversine, Relèvement/Cap, ETA Tsunami) et déduplication multi-providers.

Ce module fournit :
1. `haversine_km` : distance orthodromique sur sphère terrestre moyenne (R = 6371.0088 km).
2. `initial_bearing_deg` & `bearing_to_cardinal` : azimut exact (0-360°) et point cardinal
   (N, NE, E, SE, S, SW, W, NW) depuis la position de l'utilisateur/bateau vers la menace.
3. `estimate_tsunami_eta_minutes` : estimation physique du temps de propagation d'un tsunami
   en fonction de la profondeur océanique moyenne (v = sqrt(g * d)).
4. `deduplicate_hazards` : fusion spatiotemporelle inter-agences (ex: un même séisme
   remonté simultanément par USGS, EMSC et GDACS) en conservant le meilleur score et la
   liste complète des sources confirmantes.
"""

from __future__ import annotations

import math
from typing import Iterable

from app.models.schemas import HazardAlert, HazardCategory, SEVERITY_RANK, SeverityLevel

# Rayon terrestre moyen IUGG (en kilomètres)
EARTH_RADIUS_KM: float = 6371.0088
# Accélération de la pesanteur standard (m/s²)
GRAVITY_M_S2: float = 9.80665

CARDINAL_8: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcule la distance orthodromique (grand cercle) en kilomètres entre deux points WGS84."""
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
    """Calcule le relèvement initial (azimut vrai en degrés [0.0, 360.0[) du point 1 vers le point 2.

    Permet à un bateau ou une station fixe de savoir immédiatement dans quelle direction
    se situe l'épicentre d'un séisme, d'un cyclone ou d'un grain violent.
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
    """Convertit un azimut en degrés [0, 360[ en direction cardinale à 8 secteurs (N, NE, E...)."""
    normalized = (bearing_deg + 360.0) % 360.0
    index = int((normalized + 22.5) // 45.0) % 8
    return CARDINAL_8[index]


def estimate_tsunami_eta_minutes(distance_km: float, avg_depth_m: float = 4000.0) -> int:
    """Estime le temps d'arrivée d'une onde de tsunami (en minutes) selon la loi d'onde longue.

    En océan profond (d = 4000 m), la célérité de l'onde est c = sqrt(g * d) ≈ 198.05 m/s ≈ 713 km/h.
    Retourne toujours au minimum 1 minute si la distance est non nulle.
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
    """Calcule et injecte distance, relèvement (cap + cardinal) et ETA tsunami vers un point de référence."""
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
    """Détermine si deux alertes provenant de sources différentes décrivent le même phénomène physique."""
    if a.category != b.category:
        return False

    time_diff = abs(a.timestamp_utc - b.timestamp_utc)
    dist_km = haversine_km(a.lat, a.lon, b.lat, b.lon)

    if a.category == HazardCategory.EARTHQUAKE:
        return time_diff <= max_time_diff_s and dist_km <= max_dist_km

    # Pour les phénomènes étendus (cyclones, volcans, tsunamis, inondations), fenêtre temporelle de 6h et 150 km
    return time_diff <= 21600 and dist_km <= 150.0


def _merge_two_hazards(primary: HazardAlert, secondary: HazardAlert) -> HazardAlert:
    """Fusionne deux alertes du même événement en conservant le meilleur score et la liste des sources."""
    # Conserver la sévérité la plus haute
    best_severity: SeverityLevel = (
        primary.severity
        if SEVERITY_RANK[primary.severity] >= SEVERITY_RANK[secondary.severity]
        else secondary.severity
    )

    # Ordre canonique des sources sans doublon
    merged_sources: list[str] = []
    for src in (primary.sources or [primary.source]) + (secondary.sources or [secondary.source]):
        for token in src.split("+"):
            clean = token.strip()
            if clean and clean not in merged_sources:
                merged_sources.append(clean)

    # Choisir l'alerte ayant la valeur primaire la plus élevée ou le titre le plus descriptif
    best_value = max(primary.value_primary, secondary.value_primary)
    best_depth = primary.depth_km if primary.depth_km is not None else secondary.depth_km
    tsunami_flag = bool(primary.tsunami_warning or secondary.tsunami_warning)

    # Préférer le titre et les coordonnées de USGS s'il est présent, sinon le premier
    preferred = primary if "USGS" in (primary.sources or [primary.source]) else (
        secondary if "USGS" in (secondary.sources or [secondary.source]) else primary
    )

    return preferred.model_copy(
        update={
            "severity": best_severity,
            "source": "+".join(merged_sources),
            "sources": merged_sources,
            "value_primary": best_value,
            "depth_km": best_depth,
            "tsunami_warning": tsunami_flag,
        }
    )


def deduplicate_hazards(
    hazards: Iterable[HazardAlert],
    max_time_diff_s: int = 120,
    max_dist_km: float = 100.0,
) -> list[HazardAlert]:
    """Déduplique et fusionne une liste d'alertes multi-providers (ex: USGS + EMSC + GDACS).

    Trie le résultat par sévérité décroissante puis par horodatage décroissant (plus récent en tête).
    """
    merged_list: list[HazardAlert] = []

    for candidate in hazards:
        # Normaliser la liste `sources` si elle est vide
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
        key=lambda h: (SEVERITY_RANK[h.severity], h.value_primary, h.timestamp_utc),
        reverse=True,
    )
    return merged_list
