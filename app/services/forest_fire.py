"""The forest fire danger of the department of a position (``GET /v1/forest-fire``), from Météo-France's "Météo des forêts" (ADR 0035).

The danger is given **per department** (four levels, for the two days after the publication), and only for metropolitan France (Corsica included).
The department of a point is found offline from the bundled NUTS3 shapes of the French departments (the same ones that place a vigilance), within about
2 km of the coast. The flux is seasonal, June to September: out of season ``status`` is ``out_of_season`` and no level is invented.
"""

from __future__ import annotations

from typing import Optional

from shapely.geometry import Point

from app.attribution import credit_text
from app.collectors.meteofrance import DEPARTMENTS
from app.collectors.meteofrance_forests import MeteoFranceForestsCollector
from app.models.forest_fire import ForestFireDepartment, ForestFireLevel, ForestFireResponse
from app.services.geoindex import GeometryIndex, default_geometries

CREDIT = ("meteofrance_forests",)
LABELS = {1: "low", 2: "moderate", 3: "high", 4: "very high"}
NUTS3_TO_DEPARTMENT = {nuts3: code for code, (nuts3, _name) in DEPARTMENTS.items()}
NOTE_SEASON = (
    "Météo-France publishes the forest fire danger from June to September (about 17:00 local time); out of this season there is no map and no level is given. "
    "It is a danger of the department as a whole, in four levels, for the two days after the publication: not a warning, not a fire detected (see "
    "`fires_nearby` of `GET /v1/point`), and the local conditions of a forest can differ."
)


def department_of(lat: float, lon: float, geometries: Optional[GeometryIndex] = None) -> Optional[str]:
    """The INSEE code of the metropolitan department that holds the point (the nearest shape when it lies in the coastal margin), or ``None``."""
    index = geometries or default_geometries()
    found = [code for scheme, code in index.codes_at(lat, lon) if scheme == "NUTS3" and code in NUTS3_TO_DEPARTMENT]
    if not found:
        return None
    if len(found) > 1:
        point = Point(lon, lat)
        found.sort(key=lambda code: point.distance(index.get("NUTS3", code)))  # type: ignore[arg-type]
    return NUTS3_TO_DEPARTMENT[found[0]]


def _level(value: Optional[int]) -> ForestFireLevel:
    return ForestFireLevel(level=value, label=LABELS.get(value) if value is not None else None)


async def query_forest_fire(
    collector: Optional[MeteoFranceForestsCollector], *, lat: float, lon: float, geometries: Optional[GeometryIndex] = None
) -> ForestFireResponse:
    """The danger of the department of a point, or why there is none."""
    base = {"lat": lat, "lon": lon, "attribution": credit_text(*CREDIT)}
    if collector is None:
        return ForestFireResponse(
            status="not_configured",
            note="The forest fire danger of Météo-France is read through a vigie-meteo gateway: set VIGIE_METEO_GATEWAY_URL.",
            **base,
        )
    code = department_of(lat, lon, geometries)
    if code is None:
        return ForestFireResponse(
            status="outside_coverage",
            note="The Météo des forêts covers the departments of metropolitan France (Corsica included): nothing is estimated elsewhere.",
            **base,
        )
    department = ForestFireDepartment(code=code, name=DEPARTMENTS[code][1])
    try:
        result = await collector.fetch_department(code)
    except Exception:
        return ForestFireResponse(
            status="unavailable",
            department=department,
            note="The forest fire danger of Météo-France cannot be read right now; try again later.",
            **base,
        )
    if result.status == "out_of_season":
        return ForestFireResponse(
            status="out_of_season", department=department, checked_at_utc=result.checked_at_utc, note=NOTE_SEASON, **base
        )
    return ForestFireResponse(
        status="available",
        department=department,
        reference_time=result.reference_time,
        day1=_level(result.level_d1),
        day2=_level(result.level_d2),
        checked_at_utc=result.checked_at_utc,
        note=NOTE_SEASON,
        **base,
    )
