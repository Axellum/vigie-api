"""Small geometry helpers shared by the collectors and the localization (shapely).

CAP writes a polygon as whitespace-separated ``latitude,longitude`` pairs; GeoJSON (RFC 7946) and shapely use
``longitude, latitude``. The helpers below convert, and build one valid shapely geometry per warning.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, Optional

from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, mapping, shape
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
