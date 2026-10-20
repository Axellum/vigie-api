"""A small reader of ESRI shapefiles: the ``.shp`` geometry and its ``.dbf`` attribute table.

The National Hurricane Center publishes the forecast of each storm as a ZIP of shapefiles (the cone, the track line, the
forecast points and the coastal watches and warnings). Only the three geometry types those files use are read here, by
the published format ("ESRI Shapefile Technical Description", July 1998): points, polylines and polygons (and their Z and M
variants, whose extra values are ignored). The reader is strict about the structure and bounded in size, since the bytes
come from the network: a file that is not what it says raises ``ShapefileError`` and the caller falls back.

No coordinate is changed here: longitudes are returned as written (the NHC writes some beyond 180 degrees).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Optional

POINT = 1
POLYLINE = 3
POLYGON = 5

# The Z and M variants of a type carry the same X and Y first.
_BASE_TYPE = {1: POINT, 11: POINT, 21: POINT, 3: POLYLINE, 13: POLYLINE, 23: POLYLINE, 5: POLYGON, 15: POLYGON, 25: POLYGON}

FILE_CODE = 9994
VERSION = 1000
HEADER_BYTES = 100

# A forecast bundle is some tens of kilobytes and a few hundred points; these limits only stop a mistake.
MAX_RECORDS = 10_000
MAX_PARTS = 10_000
MAX_POINTS = 200_000


class ShapefileError(ValueError):
    """The bytes are not a shapefile this reader understands."""


@dataclass(frozen=True)
class Shape:
    """One geometry: its type (``POINT``, ``POLYLINE`` or ``POLYGON``) and its parts, each a sequence of ``(lon, lat)``.

    A point has one part of one position. A polygon part is a ring. The format says an outer ring is clockwise and a hole
    counter-clockwise, but the NHC draws its cones counter-clockwise: the direction is not relied on, the nesting is.
    """

    kind: int
    parts: tuple[tuple[tuple[float, float], ...], ...]


def read_shapes(data: bytes) -> list[Optional[Shape]]:
    """The geometries of a ``.shp`` file in record order (``None`` for a null shape, which the format allows)."""
    if len(data) < HEADER_BYTES:
        raise ShapefileError("shapefile shorter than its header")
    code, = struct.unpack_from(">i", data, 0)
    length_words, = struct.unpack_from(">i", data, 24)
    version, file_type = struct.unpack_from("<ii", data, 28)
    if code != FILE_CODE or version != VERSION:
        raise ShapefileError("not an ESRI shapefile (file code or version)")
    if length_words * 2 != len(data):
        raise ShapefileError(f"shapefile length {len(data)} differs from the {length_words * 2} its header gives")
    if file_type != 0 and file_type not in _BASE_TYPE:
        raise ShapefileError(f"unsupported shape type {file_type}")

    shapes: list[Optional[Shape]] = []
    offset = HEADER_BYTES
    while offset < len(data):
        if len(shapes) >= MAX_RECORDS:
            raise ShapefileError("more records than any forecast bundle has")
        if offset + 8 > len(data):
            raise ShapefileError("truncated record header")
        content_words, = struct.unpack_from(">i", data, offset + 4)
        start, end = offset + 8, offset + 8 + content_words * 2
        if content_words < 2 or end > len(data):
            raise ShapefileError("record runs past the end of the file")
        shapes.append(_read_shape(data, start, end))
        offset = end
    return shapes


def _read_shape(data: bytes, start: int, end: int) -> Optional[Shape]:
    shape_type, = struct.unpack_from("<i", data, start)
    if shape_type == 0:
        return None
    kind = _BASE_TYPE.get(shape_type)
    if kind is None:
        raise ShapefileError(f"unsupported shape type {shape_type}")
    if kind == POINT:
        if start + 20 > end:
            raise ShapefileError("truncated point")
        x, y = struct.unpack_from("<2d", data, start + 4)
        return Shape(POINT, (((x, y),),))
    if start + 44 > end:
        raise ShapefileError("truncated polyline or polygon")
    parts_count, points_count = struct.unpack_from("<2i", data, start + 36)
    if not 0 < parts_count <= MAX_PARTS or not 0 < points_count <= MAX_POINTS or parts_count > points_count:
        raise ShapefileError("part or point count out of range")
    parts_at = start + 44
    points_at = parts_at + 4 * parts_count
    if points_at + 16 * points_count > end:
        raise ShapefileError("points run past the end of the record")
    firsts = struct.unpack_from(f"<{parts_count}i", data, parts_at)
    bounds = [*firsts, points_count]
    if firsts[0] != 0 or any(a >= b for a, b in zip(bounds, bounds[1:], strict=False)):
        raise ShapefileError("parts are not an increasing run of indexes")
    flat = struct.unpack_from(f"<{2 * points_count}d", data, points_at)
    if not all(math.isfinite(value) for value in flat):
        raise ShapefileError("a coordinate is not a finite number")
    points = list(zip(flat[0::2], flat[1::2], strict=True))
    parts = tuple(tuple(points[a:b]) for a, b in zip(bounds, bounds[1:], strict=False))
    return Shape(kind, parts)


def read_table(data: bytes) -> list[Optional[dict[str, str]]]:
    """The records of a ``.dbf`` file, each as ``{field: text}`` (``None`` for a record marked deleted).

    Values are the text of the field with its padding removed. Numeric fields are not converted: the caller reads
    what it needs and says what an unreadable value means.
    """
    if len(data) < 32:
        raise ShapefileError("dBASE table shorter than its header")
    records_count, header_length, record_length = struct.unpack_from("<IHH", data, 4)
    if records_count > MAX_RECORDS or header_length < 33 or record_length < 2:
        raise ShapefileError("dBASE header out of range")
    fields: list[tuple[str, int]] = []
    position = 32
    while True:
        if position >= header_length or position >= len(data):
            raise ShapefileError("dBASE field list is not terminated")
        if data[position] == 0x0D:
            break
        if position + 32 > header_length:
            raise ShapefileError("dBASE field descriptor runs past the header")
        name = data[position : position + 11].split(b"\0", 1)[0].decode("latin-1")
        fields.append((name, data[position + 16]))
        position += 32
    if 1 + sum(width for _, width in fields) > record_length:
        raise ShapefileError("dBASE fields are wider than their record")
    if header_length + records_count * record_length > len(data):
        raise ShapefileError("dBASE records run past the end of the file")

    table: list[Optional[dict[str, str]]] = []
    for index in range(records_count):
        record = data[header_length + index * record_length : header_length + (index + 1) * record_length]
        if record[0:1] == b"*":
            table.append(None)
            continue
        row: dict[str, str] = {}
        at = 1
        for name, width in fields:
            row[name] = record[at : at + width].decode("latin-1").replace("\0", " ").strip()
            at += width
        table.append(row)
    return table


def read_shapefile(shp: bytes, dbf: bytes) -> list[tuple[Shape, dict[str, str]]]:
    """The geometries of a ``.shp`` paired with the records of its ``.dbf``; a null shape or a deleted record is left out."""
    shapes, table = read_shapes(shp), read_table(dbf)
    if len(shapes) != len(table):
        raise ShapefileError(f"{len(shapes)} shapes but {len(table)} attribute records")
    return [(shape, row) for shape, row in zip(shapes, table, strict=True) if shape is not None and row is not None]
