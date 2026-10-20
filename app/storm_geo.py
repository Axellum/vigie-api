"""From the shapes of a forecast bundle to RFC 7946 GeoJSON geometries.

The National Hurricane Center writes longitudes however its drawing needs them: in the bundle of a storm that crosses the
date line (measured on 2026-10-20, ``cp012026`` advisory 64) the cone runs from 174.35 to 180.94 while the track and the
points of the same file run from -184.6 to -179.4. GeoJSON wants every longitude within -180 and 180 and a geometry that
crosses the antimeridian cut in two (RFC 7946, section 3.1.9). The steps, for each shape:

1. the parts are made continuous (a longitude never jumps by more than 180 degrees from one point to the next);
2. the geometry is cut at the antimeridian, each piece shifted back into -180 to 180;
3. the coordinates are snapped to a grid (3 decimals is about 110 m: far finer than a forecast cone) and the
   exterior rings are made counter-clockwise, as the RFC asks.

A ring is an outer ring or a hole by how it is nested, not by its direction: the NHC draws its cone counter-clockwise, the
opposite of the shapefile description.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import shapely
from shapely import make_valid
from shapely.affinity import translate
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from app.shapefile import POINT, POLYGON, POLYLINE, Shape

Position = tuple[float, float]
DEFAULT_DECIMALS = 3


def wrap_longitude(lon: float) -> float:
    """The same meridian within -180 and 180 (180 itself stays 180)."""
    if -180.0 <= lon <= 180.0:
        return lon
    return lon - 360.0 * math.floor((lon + 180.0) / 360.0)


def _continuous(points: tuple[Position, ...]) -> list[Position]:
    """The points with 360 degrees added or removed where needed so that no step is longer than 180 degrees."""
    out: list[Position] = [points[0]]
    for lon, lat in points[1:]:
        previous = out[-1][0]
        out.append((lon + 360.0 * round((previous - lon) / 360.0), lat))
    return out


def _continuous_parts(parts: tuple[tuple[Position, ...], ...]) -> list[list[Position]]:
    """Every part continuous, and every part on the same side of the date line as the first."""
    out: list[list[Position]] = []
    for part in parts:
        points = _continuous(part)
        if out:
            points = [(lon + 360.0 * round((out[0][0][0] - points[0][0]) / 360.0), lat) for lon, lat in points]
        out.append(points)
    return out


def _depth(index: int, polygons: list[Polygon]) -> int:
    """How many other (larger) rings contain this one whole."""
    inner = polygons[index]
    return sum(1 for other, polygon in enumerate(polygons) if other != index and polygon.area > inner.area and inner.within(polygon))


def _polygons(parts: list[list[Position]]) -> Optional[BaseGeometry]:
    """The polygons of the rings of a shape: a ring at an even depth is an outer ring, one at an odd depth a hole."""
    rings = [Polygon(part) for part in parts if len(part) >= 4]
    rings = [ring if ring.is_valid else make_valid(ring) for ring in rings]
    rings = [ring for ring in rings if not ring.is_empty and ring.area > 0.0]
    if not rings:
        return None
    depths = [_depth(i, rings) for i in range(len(rings))]
    shells = [(i, rings[i]) for i, d in enumerate(depths) if d % 2 == 0]
    result: list[BaseGeometry] = []
    for index, shell in shells:
        holes = [rings[j] for j, d in enumerate(depths) if d == depths[index] + 1 and rings[j].within(shell)]
        shape = shell
        for hole in holes:
            shape = shape.difference(hole)
        result.append(shape)
    union = shapely.union_all(result)
    return union if not union.is_empty else None


def _cut_at_antimeridian(geometry: BaseGeometry, dimension: int) -> Optional[BaseGeometry]:
    """The geometry in pieces that each lie within -180 and 180, as one geometry of the same dimension (1 line, 2 area)."""
    min_x, _, max_x, _ = geometry.bounds
    first, last = math.floor((min_x + 180.0) / 360.0), math.floor((max_x + 180.0) / 360.0)
    pieces: list[BaseGeometry] = []
    for strip in range(first, last + 1):
        clipped = geometry.intersection(box(-180.0 + 360.0 * strip, -90.0, 180.0 + 360.0 * strip, 90.0))
        shifted = translate(clipped, xoff=-360.0 * strip) if strip else clipped
        pieces.extend(_flatten(shifted, dimension))
    if not pieces:
        return None
    if dimension == 2:
        polygons = [orient(piece, 1.0) for piece in pieces]
        return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    return pieces[0] if len(pieces) == 1 else MultiLineString(pieces)


def _flatten(geometry: BaseGeometry, dimension: int) -> list[BaseGeometry]:
    """The polygons (dimension 2) or lines (dimension 1) of a geometry, without the points an edge cut leaves behind."""
    if geometry.is_empty:
        return []
    if hasattr(geometry, "geoms"):
        return [part for member in geometry.geoms for part in _flatten(member, dimension)]
    if dimension == 2 and isinstance(geometry, Polygon) and geometry.area > 0.0:
        return [geometry]
    if dimension == 1 and isinstance(geometry, LineString) and geometry.length > 0.0:
        return [geometry]
    return []


def geometry_of_shape(shape: Shape, decimals: int = DEFAULT_DECIMALS) -> Optional[dict[str, Any]]:
    """The GeoJSON geometry of a shapefile shape, or ``None`` when nothing is left of it.

    A point gives a ``Point``; a polyline a ``LineString`` or a ``MultiLineString``; a polygon a ``Polygon`` or a
    ``MultiPolygon``. Longitudes are within -180 and 180, the coordinates are rounded to ``decimals``.
    """
    if shape.kind == POINT:
        lon, lat = shape.parts[0][0]
        return {"type": "Point", "coordinates": [round(wrap_longitude(lon), decimals), round(lat, decimals)]}
    parts = _continuous_parts(shape.parts)
    geometry: Optional[BaseGeometry]
    if shape.kind == POLYLINE:
        lines = [LineString(part) for part in parts if len(part) >= 2]
        geometry = MultiLineString(lines) if len(lines) > 1 else (lines[0] if lines else None)
        dimension = 1
    elif shape.kind == POLYGON:
        geometry, dimension = _polygons(parts), 2
    else:
        return None
    if geometry is None:
        return None
    cut = _cut_at_antimeridian(geometry, dimension)
    if cut is None:
        return None
    grid = 10.0 ** -decimals
    snapped = shapely.set_precision(cut, grid)
    flat = _flatten(snapped, dimension)
    if not flat:
        return None
    if dimension == 2:  # snapping to the grid gives the rings back the way GEOS likes them (exterior clockwise): RFC 7946 wants it counter-clockwise
        flat = [orient(piece, 1.0) for piece in flat]
    result = flat[0] if len(flat) == 1 else (MultiPolygon(flat) if dimension == 2 else MultiLineString(flat))  # type: ignore[arg-type]
    mapped = mapping(result)
    return {"type": mapped["type"], "coordinates": _rounded(mapped["coordinates"], decimals)}


def _rounded(value: Any, decimals: int) -> Any:
    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (int, float)):
            return [round(float(number), decimals) for number in value[:2]]
        return [_rounded(item, decimals) for item in value]
    return value
