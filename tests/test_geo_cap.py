"""The shapes a CAP area is written with: ``polygon`` and ``circle`` (synthetic input, checked by distances on the sphere)."""

from __future__ import annotations

import math

import pytest
from shapely.geometry import Point

from app.geo import (
    MAX_CIRCLE_RADIUS_KM,
    Circle,
    circle_to_polygon,
    parse_cap_circle,
    parse_cap_polygon,
    shapes_to_shape,
)


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine), an independent check of the circle."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


@pytest.mark.parametrize(
    ("text", "circle"),
    [
        ("10.0,20.0 50", Circle(10.0, 20.0, 50.0)),
        ("  -17.5,179.9   12.5  ", Circle(-17.5, 179.9, 12.5)),
        ("0,0 0.1", Circle(0.0, 0.0, 0.1)),
    ],
)
def test_a_circle_is_a_centre_and_a_radius_in_kilometres(text: str, circle: Circle) -> None:
    assert parse_cap_circle(text) == circle


@pytest.mark.parametrize(
    "text",
    [None, "", "   ", "10,20", "10,20 5 7", "ten,20 5", "91,0 5", "0,181 5", "0,0 0", "0,0 -3", f"0,0 {MAX_CIRCLE_RADIUS_KM + 1}", "nan,0 5", "0,0 inf", "0;0 5"],
)
def test_what_is_not_a_circle(text: str | None) -> None:
    assert parse_cap_circle(text) is None


@pytest.mark.parametrize("lat", [-60.0, 0.0, 45.0, 66.0])
def test_the_edge_of_a_circle_is_at_its_radius(lat: float) -> None:
    circle = Circle(lat, 30.0, 100.0)
    polygon = circle_to_polygon(circle)
    assert polygon is not None
    for lon, vertex_lat in list(polygon.exterior.coords)[:-1]:
        assert km(lat, 30.0, vertex_lat, lon) == pytest.approx(100.0, abs=0.5)
    assert polygon.contains(Point(30.0, lat))


def test_a_circle_across_the_antimeridian_has_both_sides() -> None:
    polygon = circle_to_polygon(Circle(-17.0, 179.5, 200.0))
    assert polygon is not None and polygon.geom_type == "MultiPolygon"
    assert polygon.contains(Point(179.9, -17.0)) and polygon.contains(Point(-179.9, -17.0))
    assert not polygon.contains(Point(170.0, -17.0))
    west = circle_to_polygon(Circle(5.0, -179.8, 150.0))
    assert west is not None and west.contains(Point(179.9, 5.0)) and west.contains(Point(-179.9, 5.0))


def test_a_circle_that_reaches_a_pole_is_no_polygon() -> None:
    assert circle_to_polygon(Circle(89.0, 0.0, 300.0)) is None
    assert circle_to_polygon(Circle(-90.0, 0.0, 10.0)) is None


def test_a_ring_is_read_in_latitude_longitude_and_closed() -> None:
    ring = parse_cap_polygon("10,20 10,21 11,21 11,20")
    assert ring == [(20.0, 10.0), (21.0, 10.0), (21.0, 11.0), (20.0, 11.0), (20.0, 10.0)]
    assert parse_cap_polygon("10,20\n10,21\n  11,21\t11,20\n10,20") is not None  # Macao puts newlines between the points


@pytest.mark.parametrize("text", [None, "", "10,20 10,21", "a,b c,d e,f g,h", "95,0 95,1 96,1 96,0", "10,200 10,201 11,201 11,200"])
def test_what_is_not_a_polygon(text: str | None) -> None:
    assert parse_cap_polygon(text) is None


def test_a_polygon_and_a_circle_of_one_area_are_one_shape() -> None:
    ring = parse_cap_polygon("10,20 10,21 11,21 11,20")
    assert ring is not None
    shape = shapes_to_shape([ring], [Circle(40.0, 5.0, 30.0)])
    assert shape is not None and shape.contains(Point(20.5, 10.5)) and shape.contains(Point(5.0, 40.0))
    assert shapes_to_shape([], []) is None
