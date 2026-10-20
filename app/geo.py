"""Small geometry helpers shared by the collectors and the localization (shapely).

CAP writes a polygon as whitespace-separated ``latitude,longitude`` pairs; GeoJSON (RFC 7946) and shapely use
``longitude, latitude``. The helpers below convert, and build one valid shapely geometry per warning.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, NamedTuple, Optional

from shapely import make_valid
from shapely.affinity import translate
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

Ring = list[tuple[float, float]]  # (lon, lat) pairs

# A polygon of a feed is read only up to this many points: a longer one is not a warning area, it is a mistake.
MAX_RING_POINTS = 20_000


def parse_cap_polygon(text: Optional[str]) -> Optional[Ring]:
    """Read a CAP ``<polygon>`` (``lat,lon lat,lon ...``) as a ring of ``(lon, lat)``; ``None`` if it is not a polygon.

    Needs at least four pairs, finite and within the globe. A ring that is not closed is closed.
    """
    if not text or not text.strip():
        return None
    ring: Ring = []
    for pair in text.split():
        if len(ring) > MAX_RING_POINTS:
            return None
        try:
            lat_text, lon_text = pair.split(",")
            lat, lon = float(lat_text), float(lon_text)
        except ValueError:
            return None
        if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90.0 or abs(lon) > 180.0:
            return None
        ring.append((lon, lat))
    if len(ring) >= 2 and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring if len(ring) >= 4 else None


def rings_to_shape(rings: Iterable[Ring]) -> Optional[BaseGeometry]:
    """One valid geometry for the polygons of a warning (their union); ``None`` when there is none."""
    polygons: list[BaseGeometry] = []
    for ring in rings:
        polygon = Polygon(ring)
        polygons.append(polygon if polygon.is_valid else make_valid(polygon))
    return finalize(polygons)


# A circle of a warning is read up to this radius: a larger one is a mistake (the largest warning areas are a few hundred km).
MAX_CIRCLE_RADIUS_KM = 2_000.0
EARTH_RADIUS_KM = 6371.0088
CIRCLE_POINTS = 48


class Circle(NamedTuple):
    """A CAP ``<circle>``: a centre (WGS84) and a radius in kilometres."""

    lat: float
    lon: float
    radius_km: float


def parse_cap_circle(text: Optional[str]) -> Optional[Circle]:
    """Read a CAP ``<circle>`` (``lat,lon radius``, the radius in kilometres) ; ``None`` if it is not one.

    The radius is positive and at most ``MAX_CIRCLE_RADIUS_KM``; the centre is finite and within the globe.
    """
    if not text or not text.strip():
        return None
    parts = text.split()
    if len(parts) != 2:
        return None
    try:
        lat_text, lon_text = parts[0].split(",")
        lat, lon, radius = float(lat_text), float(lon_text), float(parts[1])
    except ValueError:
        return None
    if not all(math.isfinite(value) for value in (lat, lon, radius)) or abs(lat) > 90.0 or abs(lon) > 180.0:
        return None
    if not 0.0 < radius <= MAX_CIRCLE_RADIUS_KM:
        return None
    return Circle(lat, lon, radius)


def circle_to_polygon(circle: Circle, points: int = CIRCLE_POINTS) -> Optional[BaseGeometry]:
    """The circle as a polygon of ``points`` vertices on the sphere (great-circle distance from the centre).

    A circle that crosses the antimeridian is cut in two so that a point on either side is inside it. ``None`` when the
    circle reaches a pole (its outline is then not a polygon of the plane).
    """
    lat1, lon1 = math.radians(circle.lat), math.radians(circle.lon)
    angular = circle.radius_km / EARTH_RADIUS_KM
    ring: Ring = []
    for step in range(points):
        bearing = 2.0 * math.pi * step / points
        lat2 = math.asin(math.sin(lat1) * math.cos(angular) + math.cos(lat1) * math.sin(angular) * math.cos(bearing))
        lon2 = lon1 + math.atan2(
            math.sin(bearing) * math.sin(angular) * math.cos(lat1),
            math.cos(angular) - math.sin(lat1) * math.sin(lat2),
        )
        ring.append((math.degrees(lon2), math.degrees(lat2)))
    if max(abs(lat) for _, lat in ring) >= 89.9 or abs(circle.lat) + math.degrees(angular) >= 90.0:
        return None
    ring.append(ring[0])
    polygon = Polygon(ring)  # longitudes are continuous around the centre, so they may pass 180 degrees
    west, east = polygon.bounds[0], polygon.bounds[2]
    if west >= -180.0 and east <= 180.0:
        return polygon
    world = box(-180.0, -90.0, 180.0, 90.0)
    parts = [polygon.intersection(world)]
    if east > 180.0:
        parts.append(translate(polygon, xoff=-360.0).intersection(world))
    if west < -180.0:
        parts.append(translate(polygon, xoff=360.0).intersection(world))
    return finalize(parts)


def shapes_to_shape(rings: Iterable[Ring], circles: Iterable[Circle]) -> Optional[BaseGeometry]:
    """One valid geometry for the polygons and the circles of a warning (their union); ``None`` when there is none."""
    geometries: list[BaseGeometry] = []
    for ring in rings:
        polygon = Polygon(ring)
        geometries.append(polygon if polygon.is_valid else make_valid(polygon))
    for circle in circles:
        outline = circle_to_polygon(circle)
        if outline is not None:
            geometries.append(outline)
    return finalize(geometries)


def finalize(geometries: list[BaseGeometry]) -> Optional[BaseGeometry]:
    """The union of ``geometries`` reduced to polygons; ``None`` for nothing."""
    if not geometries:
        return None
    merged = unary_union(geometries)
    if isinstance(merged, GeometryCollection) and not isinstance(merged, MultiPolygon):
        polygons = [part for part in merged.geoms if isinstance(part, (Polygon, MultiPolygon))]
        if not polygons:
            return None
        merged = unary_union(polygons)
    if merged.is_empty or not isinstance(merged, (Polygon, MultiPolygon)):
        return None
    return merged


def geojson_to_shape(geometry: Any) -> Optional[BaseGeometry]:
    """The shapely geometry of a GeoJSON Polygon or MultiPolygon; ``None`` for anything else (or a broken one)."""
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    try:
        parsed = shape(geometry)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return None
    if parsed.is_empty:
        return None
    return parsed if parsed.is_valid else make_valid(parsed)


def shape_to_geojson(geometry: BaseGeometry, decimals: int = 5) -> dict[str, Any]:
    """A GeoJSON geometry (RFC 7946) for a shapely geometry, coordinates rounded."""
    geo = mapping(geometry)
    return {"type": geo["type"], "coordinates": _round(geo["coordinates"], decimals)}


def _round(value: Any, decimals: int) -> Any:
    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (int, float)):
            return [round(float(number), decimals) for number in value]
        return [_round(item, decimals) for item in value]
    return value
