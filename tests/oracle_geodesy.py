"""An independent way to measure the distance from a point to a line, to check ``app/services/places.py`` against.

The API works out the distance on the sphere (``haversine_km`` to the nearest point of a simplified trace, found on a flat map
scaled to the latitude). This module does it another way, on purpose: **Vincenty's inverse formula on the WGS84 ellipsoid**
(the standard geodesic distance, accurate to a fraction of a millimetre), measured from the point to a **dense sampling of the
original, unsimplified line**. Two methods that share nothing but the data: when they agree, neither the sphere, nor the flat
approximation, nor the simplification of the bundled file is hiding a mistake.

``tests/test_oracle_geodesy.py`` checks this module against published distances before anything is compared with it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

A = 6378137.0  # WGS84 semi-major axis (m)
F = 1 / 298.257223563  # WGS84 flattening
B = (1 - F) * A


def vincenty_m(lat1: float, lon1: float, lat2: float, lon2: float, tol: float = 1e-12, max_iter: int = 200) -> float:
    """Geodesic distance in metres between two WGS84 positions (Vincenty, inverse problem)."""
    if lat1 == lat2 and lon1 == lon2:
        return 0.0
    u1 = math.atan((1 - F) * math.tan(math.radians(lat1)))
    u2 = math.atan((1 - F) * math.tan(math.radians(lat2)))
    big_l = math.radians(lon2 - lon1)
    lam = big_l
    sin_u1, cos_u1, sin_u2, cos_u2 = math.sin(u1), math.cos(u1), math.sin(u2), math.cos(u2)
    for _ in range(max_iter):
        sin_lam, cos_lam = math.sin(lam), math.cos(lam)
        sin_sigma = math.hypot(cos_u2 * sin_lam, cos_u1 * sin_u2 - sin_u1 * cos_u2 * cos_lam)
        if sin_sigma == 0:
            return 0.0
        cos_sigma = sin_u1 * sin_u2 + cos_u1 * cos_u2 * cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cos_u1 * cos_u2 * sin_lam / sin_sigma
        cos2_alpha = 1 - sin_alpha**2
        cos_2sm = cos_sigma - 2 * sin_u1 * sin_u2 / cos2_alpha if cos2_alpha != 0 else 0.0
        c = F / 16 * cos2_alpha * (4 + F * (4 - 3 * cos2_alpha))
        previous = lam
        lam = big_l + (1 - c) * F * sin_alpha * (
            sigma + c * sin_sigma * (cos_2sm + c * cos_sigma * (-1 + 2 * cos_2sm**2))
        )
        if abs(lam - previous) < tol:
            break
    else:
        raise ArithmeticError("Vincenty did not converge (nearly antipodal points)")
    u_sq = cos2_alpha * (A**2 - B**2) / B**2
    k1 = (math.sqrt(1 + u_sq) - 1) / (math.sqrt(1 + u_sq) + 1)
    big_a = (1 + k1**2 / 4) / (1 - k1)
    big_b = k1 * (1 - 3 * k1**2 / 8)
    d_sigma = big_b * sin_sigma * (
        cos_2sm
        + big_b
        / 4
        * (
            cos_sigma * (-1 + 2 * cos_2sm**2)
            - big_b / 6 * cos_2sm * (-3 + 4 * sin_sigma**2) * (-3 + 4 * cos_2sm**2)
        )
    )
    return B * big_a * (sigma - d_sigma)


def densify(coordinates: Sequence[Sequence[float]], step_m: float = 100.0) -> list[tuple[float, float]]:
    """Positions along a polyline ``[[lon, lat], ...]``, at most ``step_m`` apart (straight in lon/lat between two vertices)."""
    points = [(float(coordinates[0][0]), float(coordinates[0][1]))]
    for start, end in zip(coordinates, coordinates[1:], strict=False):
        x0, y0, x1, y1 = start[0], start[1], end[0], end[1]
        steps = max(1, math.ceil(vincenty_m(y0, x0, y1, x1) / step_m))
        for k in range(1, steps + 1):
            t = k / steps
            points.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return points


def distance_to_line_km(lat: float, lon: float, coordinates: Sequence[Sequence[float]], step_m: float = 100.0) -> float:
    """Distance in km from a position to a polyline ``[[lon, lat], ...]``: the nearest of its sampled points (error: under ``step_m`` / 2)."""
    return min(vincenty_m(lat, lon, y, x) for x, y in densify(coordinates, step_m)) / 1000.0
