"""The map of a solar eclipse, drawn by Vigie from NASA's Besselian elements: the path of totality or annularity, its central
line, and bands of the share of the Sun hidden at the maximum.

* **The path** is the union of the umbra's (or antumbra's) footprints on the ellipsoid every 20 seconds, each pair of successive
  footprints joined by its convex hull. Where the footprint runs off the Earth's disc (the ends of the path, at sunrise and sunset),
  its points are brought back to the disc's edge.
* **The central line** is the point under the shadow axis every 30 seconds.
* **The bands** come from a 0.5-degree grid: at each point, the share of the Sun hidden at the place's maximum, counted only where
  the Sun is above the horizon then; the cells of each band are joined into (multi)polygons.

Near the poles (beyond 85 degrees, where a web map does not go) a footprint whose longitudes spread over more than 90 degrees is
left out of the path. The work takes a fraction of a second to a few seconds: the service runs it in a thread and keeps the result.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
from shapely.affinity import translate
from shapely.geometry import MultiPoint, box, mapping
from shapely.ops import unary_union

from app.services.eclipse_besselian import (
    E2,
    Places,
    SolarEclipse,
    _poly,
    central_line,
    obscuration,
    project,
    shadow,
    time_of_maximum,
)

PATH_STEP_S = 20.0
LINE_STEP_S = 30.0
FOOTPRINT_POINTS = 48
GRID_DEG = 0.5
OBSCURATION_LEVELS = (0.0, 0.2, 0.4, 0.6, 0.8)  # the bands of the map (ours): above 0, then from each level to the next
BAND_SIMPLIFY_DEG = 0.1
POLAR_SPREAD_DEG = 90.0


def _times(eclipse: SolarEclipse, step_s: float) -> np.ndarray:
    step_h = step_s / 3600.0
    return np.arange(eclipse.tmin, eclipse.tmax + step_h / 2, step_h)


def _rounded(geometry: Any, digits: int) -> Any:
    if isinstance(geometry, dict):
        return {key: _rounded(value, digits) for key, value in geometry.items()}
    if isinstance(geometry, (list, tuple)):
        if geometry and all(isinstance(v, (int, float)) for v in geometry):
            return [round(float(v), digits) for v in geometry]
        return [_rounded(v, digits) for v in geometry]
    return geometry


def _split_antimeridian(shape: Any) -> Any:
    """A shape drawn with unwrapped longitudes, cut at +-180 degrees and brought back into [-180, 180]."""
    pieces = []
    for turn in (-1, 0, 1):
        piece = shape.intersection(box(-180.0 + 360.0 * turn, -90.0, 180.0 + 360.0 * turn, 90.0))
        if not piece.is_empty:
            pieces.append(translate(piece, xoff=-360.0 * turn))
    return unary_union(pieces)


def _unwrap(lon: np.ndarray, reference: float) -> np.ndarray:
    return reference + (lon - reference + 180.0) % 360.0 - 180.0


def path_geometry(eclipse: SolarEclipse) -> Optional[dict[str, Any]]:
    """The path of totality or annularity as a GeoJSON geometry; ``None`` for a partial eclipse or one whose umbra misses."""
    if eclipse.kind == "partial":
        return None
    t = _times(eclipse, PATH_STEP_S)
    x, y = _poly(eclipse.x, t), _poly(eclipse.y, t)
    zeta_axis = np.sqrt(np.maximum(0.0, 1.0 - x * x - y * y))
    radius = np.abs(_poly(eclipse.l2, t) - zeta_axis * eclipse.tan_f2)
    theta = np.linspace(0.0, 2.0 * math.pi, FOOTPRINT_POINTS, endpoint=False)
    xi = x[:, None] + radius[:, None] * np.cos(theta)[None, :]
    eta = y[:, None] + radius[:, None] * np.sin(theta)[None, :]
    # Points beyond the Earth's disc are brought back to its edge (the terminator), so the ends of the path are drawn too.
    d = np.radians(_poly(eclipse.d, t))[:, None]
    omega = 1.0 / np.sqrt(1.0 - E2 * np.cos(d) ** 2)
    reach = np.hypot(xi, omega * eta)
    scale = np.where(reach >= 1.0, (1.0 - 1e-9) / reach, 1.0)
    lat, lon, valid = project(eclipse, t[:, None], xi * scale, eta * scale)
    keep = (reach < 1.0).any(axis=1) & valid.all(axis=1)  # the footprint touches the Earth
    hulls = []
    for i in range(len(t) - 1):
        if not (keep[i] and keep[i + 1]):
            continue
        reference = float(lon[i, 0])
        lons = _unwrap(np.concatenate([lon[i], lon[i + 1]]), reference)
        lats = np.concatenate([lat[i], lat[i + 1]])
        if np.ptp(lons) > POLAR_SPREAD_DEG:
            continue
        hulls.append(MultiPoint(np.column_stack([lons, lats])).convex_hull)
    if not hulls:
        return None
    shape = _split_antimeridian(unary_union(hulls)).simplify(0.005, preserve_topology=True)
    return None if shape.is_empty else _rounded(mapping(shape), 3)


def central_line_geometry(eclipse: SolarEclipse) -> Optional[dict[str, Any]]:
    """The central line as a GeoJSON MultiLineString (cut where it leaves the Earth and at the antimeridian); ``None`` when the
    shadow axis misses the Earth."""
    t = _times(eclipse, LINE_STEP_S)
    lat, lon, valid = central_line(eclipse, t)
    lines: list[list[list[float]]] = []
    current: list[list[float]] = []
    for i in range(len(t)):
        if not valid[i]:
            if len(current) > 1:
                lines.append(current)
            current = []
            continue
        point = [float(lon[i]), float(lat[i])]
        if current and abs(point[0] - current[-1][0]) > 180.0:  # crosses the antimeridian: end at the edge, go on from the other
            east = current[-1][0] > 0
            span = (point[0] + (360.0 if east else -360.0)) - current[-1][0]
            edge = 180.0 if east else -180.0
            share = (edge - current[-1][0]) / span if span else 0.0
            edge_lat = current[-1][1] + share * (point[1] - current[-1][1])
            current.append([edge, edge_lat])
            if len(current) > 1:
                lines.append(current)
            current = [[-edge, edge_lat]]
        current.append(point)
    if len(current) > 1:
        lines.append(current)
    if not lines:
        return None
    return _rounded({"type": "MultiLineString", "coordinates": lines}, 3)


def obscuration_grid(eclipse: SolarEclipse, step: float = GRID_DEG) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The share of the Sun hidden at the maximum of each point of a grid (0 where the Sun is down then): ``(lats, lons, values)``,
    ``values[i, j]`` at ``lats[i]``, ``lons[j]`` (the centres of the cells)."""
    lats = np.arange(-90.0 + step / 2, 90.0, step)
    lons = np.arange(-180.0 + step / 2, 180.0, step)
    grid_lat, grid_lon = np.meshgrid(lats, lons, indexing="ij")
    places = Places.at(grid_lat.ravel(), grid_lon.ravel())
    t = time_of_maximum(eclipse, places)
    s = shadow(eclipse, t, places)
    values = np.where(s.sun_altitude > 0.0, obscuration(s), 0.0)
    return lats, lons, values.reshape(grid_lat.shape)


def obscuration_bands(eclipse: SolarEclipse, step: float = GRID_DEG) -> list[tuple[float, Optional[float], dict[str, Any]]]:
    """The bands ``[level, next level)`` of the share hidden (above 0 for the first), as ``(min, max, geometry)``."""
    lats, lons, values = obscuration_grid(eclipse, step)
    bounds = list(OBSCURATION_LEVELS) + [None]
    bands: list[tuple[float, Optional[float], dict[str, Any]]] = []
    for low, high in zip(bounds, bounds[1:], strict=False):
        assert low is not None
        inside = (values > low) if low == 0.0 else (values >= low)
        if high is not None:
            inside &= values < high
        boxes = []
        for i, row in enumerate(inside):
            if not row.any():
                continue
            edges = np.flatnonzero(np.diff(np.concatenate([[0], row.astype(np.int8), [0]])))
            south, north = lats[i] - step / 2, lats[i] + step / 2
            for start, end in zip(edges[::2], edges[1::2], strict=False):
                boxes.append(box(float(lons[start] - step / 2), float(south), float(lons[end - 1] + step / 2), float(north)))
        if not boxes:
            continue
        shape = unary_union(boxes).simplify(BAND_SIMPLIFY_DEG, preserve_topology=True)
        if not shape.is_empty:
            bands.append((low, high, _rounded(mapping(shape), 2)))
    return bands
