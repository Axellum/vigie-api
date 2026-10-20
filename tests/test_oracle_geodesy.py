"""The independent oracle (``tests/oracle_geodesy.py``) is itself checked against published distances before it checks anything."""

from __future__ import annotations

import pytest

from app.services.geodesy import haversine_km
from tests.oracle_geodesy import densify, distance_to_line_km, vincenty_m


def dms(degrees: int, minutes: int, seconds: float) -> float:
    sign = -1 if degrees < 0 else 1
    return sign * (abs(degrees) + minutes / 60 + seconds / 3600)


def test_vincenty_gives_the_geoscience_australia_example() -> None:
    """Flinders Peak to Buninyong: 54 972.271 m, the worked example of Geoscience Australia (Vincenty's inverse problem)."""
    got = vincenty_m(dms(-37, 57, 3.72030), dms(144, 25, 29.52440), dms(-37, 39, 10.15610), dms(143, 55, 35.38390))
    assert got == pytest.approx(54972.271, abs=0.001)


def test_vincenty_gives_the_length_of_a_degree_at_the_equator() -> None:
    """One degree of latitude and of longitude on the equator of WGS84: 110 574.4 m and 111 319.5 m."""
    assert vincenty_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(110574.4, abs=0.1)
    assert vincenty_m(0.0, 0.0, 0.0, 1.0) == pytest.approx(111319.5, abs=0.1)


def test_the_ellipsoid_and_the_sphere_differ_by_a_fraction_of_a_percent() -> None:
    ellipsoid = vincenty_m(8.0, -80.8, 8.2, -80.6) / 1000.0
    assert haversine_km(8.0, -80.8, 8.2, -80.6) == pytest.approx(ellipsoid, rel=0.005)


def test_a_line_is_sampled_no_more_than_a_step_apart() -> None:
    points = densify([[-80.0, 8.0], [-80.0, 8.1]], step_m=100.0)
    assert len(points) >= 111  # 11.1 km at 100 m
    assert all(vincenty_m(b[1], b[0], a[1], a[0]) <= 100.5 for a, b in zip(points, points[1:], strict=False))


def test_the_distance_to_a_line_is_the_distance_to_its_nearest_point() -> None:
    line = [[-80.0, 8.0], [-80.0, 8.2]]  # a meridian
    km = distance_to_line_km(8.1, -79.95, line)  # 0.05 degree of longitude east of its middle, at latitude 8.1
    assert km == pytest.approx(0.05 * 111.319 * 0.990, rel=0.01)  # 5.51 km: a degree of longitude is 111.32 km * cos(8.1 deg)
