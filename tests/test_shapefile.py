"""The shapefile reader (``app/shapefile.py``) and the GeoJSON made of its shapes (``app/storm_geo.py``).

The real files are the ones of the National Hurricane Center's advisory 13A of Hurricane Isaias (2026-10-09) and of the
advisory 64 of a storm on the date line (``tests/fixtures/nhc``). The reader is strict: whatever is not a shapefile it
understands is an error, never a guess.
"""

from __future__ import annotations

import io
import struct
import zipfile
from typing import Optional

import pytest
from shapely.geometry import LineString, Point, shape

from app.shapefile import POINT, POLYGON, POLYLINE, Shape, ShapefileError, read_shapefile, read_shapes, read_table
from app.storm_geo import geometry_of_shape, wrap_longitude
from tests import storm_samples as S

ISAIAS = "al092026-013A"


def signed_area(ring: tuple[tuple[float, float], ...]) -> float:
    """Shoelace: positive for a ring that goes counter-clockwise."""
    return 0.5 * sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True))


def member(bundle: str, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(S.nhc_fixture(bundle))) as archive:
        return archive.read(name)


def layer(layer_name: str) -> list[tuple[Shape, dict[str, str]]]:
    return read_shapefile(member("al092026_5day_013A.zip", f"{ISAIAS}_{layer_name}.shp"), member("al092026_5day_013A.zip", f"{ISAIAS}_{layer_name}.dbf"))


def shp(shape_type: int, records: list[bytes]) -> bytes:
    """A minimal ``.shp`` file: the 100-byte header, then each record (its content already packed) behind its 8-byte header."""
    body = b"".join(struct.pack(">ii", number + 1, len(content) // 2) + content for number, content in enumerate(records))
    header = struct.pack(">i", 9994) + bytes(20) + struct.pack(">i", (100 + len(body)) // 2) + struct.pack("<ii", 1000, shape_type) + bytes(64)
    return header + body


def point_record(x: float, y: float) -> bytes:
    return struct.pack("<i2d", 1, x, y)


def polygon_record(rings: list[list[tuple[float, float]]], shape_type: int = 5) -> bytes:
    points = [p for ring in rings for p in ring]
    starts, at = [], 0
    for ring in rings:
        starts.append(at)
        at += len(ring)
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    head = struct.pack("<i4d2i", shape_type, min(xs), min(ys), max(xs), max(ys), len(rings), len(points))
    return head + struct.pack(f"<{len(starts)}i", *starts) + b"".join(struct.pack("<2d", *p) for p in points)


# ------------------------------------------------------------------------------------------------ the reader


def test_the_real_layers_of_the_advisory_are_read() -> None:
    cone, track, points, warnings = layer("5day_pgn"), layer("5day_lin"), layer("5day_pts"), layer("ww_wwlin")
    assert [len(cone), len(track), len(points), len(warnings)] == [1, 1, 5, 3]
    assert cone[0][0].kind == POLYGON and track[0][0].kind == POLYLINE and points[0][0].kind == POINT
    assert [row["TAU"] for _, row in points] == ["0", "12", "24", "36", "48"]
    assert {row["TCWW"] for _, row in warnings} == {"TWR", "HWR"}


def test_the_exterior_of_a_cone_is_counter_clockwise_whichever_way_the_file_draws_it() -> None:
    """RFC 7946 wants the exterior ring counter-clockwise (the NHC's cone is, a clockwise ring is turned round too)."""
    cone = layer("5day_pgn")[0][0]
    exterior = geometry_of_shape(cone)["coordinates"][0]  # type: ignore[index]
    assert signed_area(tuple((x, y) for x, y in exterior[:-1])) > 0
    turned = Shape(POLYGON, (tuple(reversed(cone.parts[0])),))
    exterior = geometry_of_shape(turned)["coordinates"][0]  # type: ignore[index]
    assert signed_area(tuple((x, y) for x, y in exterior[:-1])) > 0


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00" * 99,
        b"\x00" * 100,  # no file code
    ],
)
def test_what_is_not_a_shapefile_is_an_error(data: bytes) -> None:
    with pytest.raises(ShapefileError):
        read_shapes(data)


def test_a_file_that_lies_about_its_length_is_refused() -> None:
    good = shp(1, [point_record(1.0, 2.0)])
    assert read_shapes(good)[0] == Shape(POINT, (((1.0, 2.0),),))
    with pytest.raises(ShapefileError, match="length"):
        read_shapes(good + b"\x00\x00")
    with pytest.raises(ShapefileError):
        read_shapes(good[:-4])


def test_a_record_that_runs_past_the_file_is_refused() -> None:
    good = bytearray(shp(1, [point_record(1.0, 2.0)]))
    struct.pack_into(">i", good, 104, 500)  # the record says it is 1,000 bytes long
    with pytest.raises(ShapefileError, match="past the end"):
        read_shapes(bytes(good))


def test_a_polygon_with_more_parts_than_points_or_parts_out_of_order_is_refused() -> None:
    ring = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (0.0, 0.0)]
    assert read_shapes(shp(5, [polygon_record([ring])]))[0] is not None
    mangled = bytearray(polygon_record([ring, ring]))
    struct.pack_into("<i", mangled, 44, 9)  # the first ring starts at point 9, not 0
    with pytest.raises(ShapefileError, match="increasing"):
        read_shapes(shp(5, [bytes(mangled)]))
    many = bytearray(polygon_record([ring]))
    struct.pack_into("<i", many, 36, 900)  # 900 parts for 4 points
    with pytest.raises(ShapefileError):
        read_shapes(shp(5, [bytes(many)]))


def test_a_coordinate_that_is_not_a_number_is_refused() -> None:
    with pytest.raises(ShapefileError, match="finite"):
        read_shapes(shp(3, [polygon_record([[(0.0, 0.0), (float("nan"), 1.0)]], shape_type=3)]))


def test_a_null_shape_is_none_and_z_and_m_shapes_are_read_as_flat() -> None:
    null = struct.pack("<i", 0) + bytes(4)
    assert read_shapes(shp(1, [null]))[0] is None
    z_point = struct.pack("<i4d", 11, 3.0, 4.0, 0.0, 0.0)
    assert read_shapes(shp(11, [z_point]))[0] == Shape(POINT, (((3.0, 4.0),),))


def test_a_shape_type_the_reader_does_not_know_is_refused() -> None:
    with pytest.raises(ShapefileError, match="unsupported"):
        read_shapes(shp(8, [bytes(40)]))  # a multipoint


def test_the_table_is_read_as_text_and_a_deleted_record_is_none() -> None:
    data = member("al092026_5day_013A.zip", f"{ISAIAS}_5day_pts.dbf")
    rows = read_table(data)
    assert rows[0] is not None and rows[0]["TAU"] == "0" and rows[0]["MAXWIND"] == "95.0"
    assert rows[1]["MSLP"] == "9999.0"  # type: ignore[index]  # the NHC's "no value", left for the caller to read
    header_length = struct.unpack_from("<H", data, 8)[0]
    deleted = bytearray(data)
    deleted[header_length] = ord("*")  # dBASE marks a deleted record with an asterisk
    assert read_table(bytes(deleted))[0] is None and read_table(bytes(deleted))[1] == rows[1]


def test_a_table_that_is_cut_or_not_terminated_is_refused() -> None:
    data = member("al092026_5day_013A.zip", f"{ISAIAS}_5day_pts.dbf")
    with pytest.raises(ShapefileError):
        read_table(data[:20])
    with pytest.raises(ShapefileError):
        read_table(data[:-10])
    broken = bytearray(data)
    header_length = struct.unpack_from("<H", broken, 8)[0]
    broken[header_length - 1] = 0x20  # the byte that ends the field list
    with pytest.raises(ShapefileError):
        read_table(bytes(broken))


def test_shapes_and_records_must_come_in_equal_number() -> None:
    pts_shp = member("al092026_5day_013A.zip", f"{ISAIAS}_5day_pts.shp")
    lin_dbf = member("al092026_5day_013A.zip", f"{ISAIAS}_5day_lin.dbf")
    with pytest.raises(ShapefileError, match="attribute records"):
        read_shapefile(pts_shp, lin_dbf)


# ------------------------------------------------------------------------------------------------ the GeoJSON


def test_longitudes_are_brought_within_the_range() -> None:
    assert [wrap_longitude(x) for x in (-180.0, 180.0, 181.0, -181.0, 360.0, 0.0)] == [-180.0, 180.0, -179.0, 179.0, 0.0, 0.0]


def test_the_coordinates_are_rounded_to_three_decimals() -> None:
    geometry = geometry_of_shape(Shape(POINT, (((-86.6000004, 30.0999999),),)))
    assert geometry == {"type": "Point", "coordinates": [-86.6, 30.1]}
    line = geometry_of_shape(Shape(POLYLINE, (((0.0, 0.00012345), (1.23456789, 2.0)),)))
    assert line == {"type": "LineString", "coordinates": [[0.0, 0.0], [1.235, 2.0]]}


def geojson_lons(geometry: Optional[dict]) -> list[float]:
    assert geometry is not None

    def walk(value: object) -> list[float]:
        if isinstance(value, list) and value and isinstance(value[0], (int, float)):
            return [value[0]]
        return [lon for item in value for lon in walk(item)]  # type: ignore[union-attr]

    return walk(geometry["coordinates"])


def test_a_line_across_the_date_line_is_cut_in_two_as_rfc_7946_asks() -> None:
    geometry = geometry_of_shape(Shape(POLYLINE, (((178.0, 10.0), (182.0, 12.0)),)))  # 182 is 178 West
    assert geometry is not None and geometry["type"] == "MultiLineString"
    east, west = geometry["coordinates"]
    assert east[0] == [178.0, 10.0] and east[-1][0] == 180.0
    assert west[0][0] == -180.0 and west[-1] == [-178.0, 12.0]
    assert all(-180.0 <= lon <= 180.0 for lon in geojson_lons(geometry))
    assert east[-1][1] == west[0][1] == 11.0  # the two halves meet at the same latitude


def test_the_cone_of_a_storm_on_the_date_line_is_a_multipolygon_within_the_range() -> None:
    """Advisory 64 of a storm on 180 degrees: the NHC draws its cone with longitudes on both sides of the date line."""
    from app.collectors.nhc import parse_forecast_bundle

    forecast = parse_forecast_bundle(S.nhc_fixture("cp012026_5day_064.zip"), 1791590400, "064")
    cone, track = forecast.cones[0], forecast.tracks[0]
    assert cone["type"] == "MultiPolygon" and track["type"] == "MultiLineString"
    for geometry in (cone, track):
        lons = geojson_lons(geometry)
        assert min(lons) >= -180.0 and max(lons) <= 180.0
    east, west = (shape({"type": "Polygon", "coordinates": rings}) for rings in cone["coordinates"])
    assert east.bounds[2] == 180.0 and west.bounds[0] == -180.0
    assert east.is_valid and west.is_valid
    # The positions of the forecast are on the cone, whichever side they are written on.
    for point in forecast.points:
        lon, lat = point.geometry["coordinates"]
        assert east.buffer(0.001).contains(Point(lon, lat)) or west.buffer(0.001).contains(Point(lon, lat))
    # ...and the track joins them without the long way round: no segment is wider than the whole cone.
    for line in track["coordinates"]:
        assert LineString(line).length < 20.0


def test_a_ring_inside_a_ring_is_a_hole_and_a_second_outer_ring_a_second_polygon() -> None:
    outer = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0), (0.0, 0.0)]  # clockwise
    hole = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0), (4.0, 4.0)]  # counter-clockwise
    apart = [(20.0, 0.0), (20.0, 5.0), (25.0, 5.0), (25.0, 0.0), (20.0, 0.0)]
    one = geometry_of_shape(Shape(POLYGON, (tuple(outer), tuple(hole))))
    assert one is not None and one["type"] == "Polygon" and len(one["coordinates"]) == 2
    assert shape(one).area == pytest.approx(100.0 - 4.0)
    two = geometry_of_shape(Shape(POLYGON, (tuple(outer), tuple(apart))))
    assert two is not None and two["type"] == "MultiPolygon" and len(two["coordinates"]) == 2


def test_a_shape_with_nothing_left_is_none() -> None:
    flat = ((0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (0.0, 0.0))  # a ring without area
    assert geometry_of_shape(Shape(POLYGON, (flat,))) is None
