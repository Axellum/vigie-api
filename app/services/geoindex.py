"""Where a point is: its country, and whether it lies inside the area of a warning (shapely).

Two small sets of shapes are read from ``app/data/`` (built by ``tools/build_geometries.py``, whose docstring
says where each comes from and under which licence). Nothing is fetched at run time.

- ``countries.geojson.gz``: the borders of the countries with a warning source, to find the country of a point
  **offline**, without asking any service. A point that lies in a country, or within 0.02 degree (about 2 km) of
  its border, belongs to it; a point at sea belongs to the nearest country within 0.25 degree (about 25 km).
- ``geocodes-*.geojson.gz``: the shapes of the codes a warning can name its area with (NUTS3 for France, NUTS2 for
  Belgium). They are simplified. A point that lies in a shape belongs to that shape only (the exact answer); a point
  that lies in none of the shapes of a code system (the coast, a gap of the simplification) belongs to those within
  ``GEOCODE_MARGIN_DEG`` (0.02 degree, about 2 km). The MeteoAlarm ``EMMA_ID`` shapes are NOT bundled (no licence, see ``SOURCES.md``);
  an operator can build them for his own instance (``tools/build_geometries.py --emma``), and the file is read
  when it is there: ``VIGIE_EMMA_GEOMETRIES``, else ``app/data/local/emma_id.geojson.gz``.

A code without a shape here cannot be matched to a point: the answer says so (``coverage``), it never guesses.

A river section (Vigicrues) is a **line**, not an area: a point is matched when it lies within ``river_radius_km`` of
it (``DEFAULT_RIVER_RADIUS_KM`` unless the query says otherwise), and the distance is returned with the warning.
"""

from __future__ import annotations

import gzip
import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from shapely import STRtree
from shapely.affinity import scale
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from app.geo import finalize
from app.models.vigilance import Vigilance
from app.services.geodesy import haversine_km

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

BORDER_MARGIN_DEG = 0.02  # a point this close to a border counts as inside the country (simplified borders)
COAST_MARGIN_DEG = 0.25  # a point at sea belongs to the nearest country this close
GEOCODE_MARGIN_DEG = 0.02  # a point in no shape of a code system belongs to the shapes this close

# How far from a point a river section (a line) still counts. 5 km: the lines of Vigicrues are simplified, the flood plain
# of a large river is several kilometres wide, and a flood vigilance is a heads-up; the distance is returned with the
# warning, so a client that wants less can filter (or ask for a smaller radius). See ``docs/data-sources.md``.
DEFAULT_RIVER_RADIUS_KM = 5.0
MAX_RIVER_RADIUS_KM = 50.0
LINE_TYPES = ("LineString", "MultiLineString")


def _load_features(path: Path) -> list[dict[str, Any]]:
    """The features of a gzipped GeoJSON FeatureCollection."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise ValueError(f"{path.name} is not a FeatureCollection")
    return features


class CountryIndex:
    """The borders of the countries with a warning source, to tell where a point is without any request."""

    def __init__(self, borders: dict[str, BaseGeometry]) -> None:
        self._borders = borders
        self._bounds = {country: geometry.bounds for country, geometry in borders.items()}

    @classmethod
    def from_file(cls, path: Path = DATA_DIR / "countries.geojson.gz") -> "CountryIndex":
        borders: dict[str, BaseGeometry] = {}
        for item in _load_features(path):
            country = (item.get("properties") or {}).get("country")
            if isinstance(country, str) and item.get("geometry"):
                borders[country] = shape(item["geometry"])
        return cls(borders)

    def countries(self) -> list[str]:
        return sorted(self._borders)

    def countries_for_point(self, lat: float, lon: float) -> list[str]:
        """The countries a point belongs to: its own (and a neighbour within 2 km of the border), else the nearest at sea."""
        point = Point(lon, lat)
        near: list[tuple[float, str]] = []
        for country, border in self._borders.items():
            min_x, min_y, max_x, max_y = self._bounds[country]
            if not (min_x - COAST_MARGIN_DEG <= lon <= max_x + COAST_MARGIN_DEG and min_y - COAST_MARGIN_DEG <= lat <= max_y + COAST_MARGIN_DEG):
                continue
            near.append((border.distance(point), country))
        near.sort()
        inside = [country for distance, country in near if distance <= BORDER_MARGIN_DEG]
        if inside:
            return inside
        if near and near[0][0] <= COAST_MARGIN_DEG:
            return [near[0][1]]
        return []


class GeometryIndex:
    """The shapes of the codes a warning names its area with, by ``(scheme, code)``."""

    def __init__(self, shapes: dict[tuple[str, str], BaseGeometry]) -> None:
        self._shapes = shapes
        self._keys = list(shapes)
        self._tree = STRtree([shapes[key] for key in self._keys]) if shapes else None

    @classmethod
    def from_features(cls, features: list[dict[str, Any]]) -> "GeometryIndex":
        shapes: dict[tuple[str, str], BaseGeometry] = {}
        for item in features:
            props = item.get("properties") or {}
            scheme, code = props.get("scheme"), props.get("code")
            if isinstance(scheme, str) and isinstance(code, str) and item.get("geometry"):
                shapes[(scheme, code)] = shape(item["geometry"])
        return cls(shapes)

    @classmethod
    def from_disk(cls, data_dir: Path = DATA_DIR) -> "GeometryIndex":
        """The bundled shapes, plus the operator's own EMMA_ID file when there is one."""
        paths = sorted(data_dir.glob("geocodes-*.geojson.gz"))
        local = os.environ.get("VIGIE_EMMA_GEOMETRIES", "").strip()
        emma = Path(local) if local else data_dir / "local" / "emma_id.geojson.gz"
        if emma.is_file():
            paths.append(emma)
        features: list[dict[str, Any]] = []
        for path in paths:
            features.extend(_load_features(path))
        return cls.from_features(features)

    def has(self, scheme: str, code: str) -> bool:
        return (scheme, code) in self._shapes

    def get(self, scheme: str, code: str) -> Optional[BaseGeometry]:
        return self._shapes.get((scheme, code))

    def codes_at(self, lat: float, lon: float) -> set[tuple[str, str]]:
        """The ``(scheme, code)`` a position belongs to: for each code system the shapes it lies in, else the ones within the margin."""
        if self._tree is None:
            return set()
        point = Point(lon, lat)
        exact = {self._keys[i] for i in self._tree.query(point, predicate="covered_by")}
        found = set(exact)
        in_scheme = {scheme for scheme, _ in exact}
        for i in self._tree.query(point.buffer(GEOCODE_MARGIN_DEG), predicate="intersects"):
            scheme, _ = self._keys[i]
            if scheme not in in_scheme:
                found.add(self._keys[i])
        return found

    def schemes(self) -> dict[str, int]:
        """How many codes of each scheme have a shape."""
        counts: dict[str, int] = {}
        for scheme, _ in self._shapes:
            counts[scheme] = counts.get(scheme, 0) + 1
        return counts


@lru_cache(maxsize=1)
def default_countries() -> CountryIndex:
    return CountryIndex.from_file()


@lru_cache(maxsize=1)
def default_geometries() -> GeometryIndex:
    return GeometryIndex.from_disk()


Localization = Literal["polygon", "geocode", "line", "none"]


def is_line(shape_: Optional[BaseGeometry]) -> bool:
    """True for the line of a river section."""
    return shape_ is not None and shape_.geom_type in LINE_TYPES


def line_distance_km(lat: float, lon: float, line: BaseGeometry) -> float:
    """Distance in kilometres from a position to the nearest point of a line (WGS84).

    The nearest point is found on the plane where longitudes are scaled by the cosine of the latitude (right at the
    scale of a few tens of kilometres), then the distance to it is the great-circle one.
    """
    k = max(math.cos(math.radians(lat)), 0.01)
    flat = scale(line, xfact=k, yfact=1.0, origin=(0.0, 0.0))
    near = nearest_points(flat, Point(lon * k, lat))[0]
    return haversine_km(lat, lon, near.y, near.x / k)


def localization_of(vigilance: Vigilance, geometries: GeometryIndex) -> Localization:
    """How a point can be matched to the area of this warning on this instance."""
    if vigilance.shape is not None:
        return "line" if is_line(vigilance.shape) else "polygon"
    if any(geometries.has(code.scheme, code.value) for code in vigilance.area.geocodes):
        return "geocode"
    return "none"


class PointLocator:
    """One position, ready to be tested against many warnings (the codes it belongs to are worked out once)."""

    def __init__(
        self, lat: float, lon: float, geometries: GeometryIndex, river_radius_km: float = DEFAULT_RIVER_RADIUS_KM
    ) -> None:
        self.lat = lat
        self.lon = lon
        self.point = Point(lon, lat)
        self.river_radius_km = river_radius_km
        self._codes = geometries.codes_at(lat, lon)
        self._distances: dict[str, float] = {}

    def distance_km(self, vigilance: Vigilance) -> Optional[float]:
        """The distance to the line of a river section (``None`` for any other warning), worked out once."""
        if not is_line(vigilance.shape):
            return None
        if vigilance.id not in self._distances:
            assert vigilance.shape is not None
            self._distances[vigilance.id] = line_distance_km(self.lat, self.lon, vigilance.shape)
        return self._distances[vigilance.id]

    def covering(self, vigilance: Vigilance) -> Optional[Localization]:
        """``polygon``, ``line`` or ``geocode`` when the position lies in the area of the warning, else ``None``.

        The polygon of the source is exact (its edge counts as inside). A river section counts when the position lies
        within ``river_radius_km`` of its line. A code counts when the position belongs to its shape (see
        ``GeometryIndex.codes_at``).
        """
        if is_line(vigilance.shape):
            distance = self.distance_km(vigilance)
            return "line" if distance is not None and distance <= self.river_radius_km else None
        if vigilance.shape is not None and vigilance.shape.covers(self.point):
            return "polygon"
        if any((code.scheme, code.value) in self._codes for code in vigilance.area.geocodes):
            return "geocode"
        return None


def geometry_of(vigilance: Vigilance, geometries: GeometryIndex) -> tuple[Optional[BaseGeometry], Optional[str]]:
    """The geometry to draw for a warning and where it comes from: its own polygon or line, else the shapes of its codes."""
    if vigilance.shape is not None:
        return vigilance.shape, "line" if is_line(vigilance.shape) else "polygon"
    shapes = [(code, geometries.get(code.scheme, code.value)) for code in vigilance.area.geocodes]
    known = [(code, area) for code, area in shapes if area is not None]
    if not known:
        return None, None
    merged = finalize([area for _, area in known])
    return merged, "geocode:" + ",".join(f"{code.scheme}:{code.value}" for code, _ in known)
