"""The 81 provinces (il) of Turkey, and which grid points of a model fall in each.

The shapes are ``app/data/provinces-tr.geojson.gz``, built by ``tools/build_geometries.py --provinces-tr`` from the first-level
areas of Natural Earth (public domain) with the Turkish names of Wikidata (CC0), keyed by ISO 3166-2 code (``TR-01`` to ``TR-81``,
the numbers of the licence plates).

A province's value is computed on the grid points inside it. The smallest provinces hold one or two points of a 0.25 degree grid
(about 28 km); a province that holds none takes the point nearest to a point inside it (``representative_point``), and says so
(``nearest_only``).
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

PROVINCES_PATH = Path(__file__).resolve().parents[1] / "data" / "provinces-tr.geojson.gz"


@dataclass(frozen=True)
class Province:
    code: str  # ISO 3166-2, e.g. "TR-34"
    name: str  # Turkish, e.g. "İstanbul"
    rows: tuple[int, ...]  # grid indices of the points inside, row (latitude) and column (longitude)
    cols: tuple[int, ...]
    nearest_only: bool  # no grid point inside: the nearest one stands for it


@lru_cache(maxsize=1)
def _shapes(path: Path = PROVINCES_PATH) -> tuple[tuple[str, str, BaseGeometry], ...]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        collection = json.load(handle)
    return tuple(
        (feature["properties"]["code"], feature["properties"]["name"], shape(feature["geometry"]))
        for feature in collection["features"]
    )


def provinces_on_grid(lats: np.ndarray, lons: np.ndarray, path: Path = PROVINCES_PATH) -> list[Province]:
    """The provinces, each with the indices of the grid points (``lats`` x ``lons``) that fall inside it."""
    out: list[Province] = []
    for code, name, geometry in _shapes(path):
        test = prep(geometry)
        min_lon, min_lat, max_lon, max_lat = geometry.bounds
        rows: list[int] = []
        cols: list[int] = []
        for j in np.where((lats >= min_lat) & (lats <= max_lat))[0]:
            for i in np.where((lons >= min_lon) & (lons <= max_lon))[0]:
                if test.contains(Point(float(lons[i]), float(lats[j]))):
                    rows.append(int(j))
                    cols.append(int(i))
        nearest_only = not rows
        if nearest_only:
            inside = geometry.representative_point()
            rows = [int(np.argmin(np.abs(lats - inside.y)))]
            cols = [int(np.argmin(np.abs(lons - inside.x)))]
        out.append(Province(code, name, tuple(rows), tuple(cols), nearest_only))
    return out


@lru_cache(maxsize=1)
def _prepared(path: Path = PROVINCES_PATH) -> tuple[tuple[str, str, BaseGeometry, object], ...]:
    return tuple((code, name, geometry, prep(geometry)) for code, name, geometry in _shapes(path))


def province_at(lat: float, lon: float, margin_deg: float = 0.05) -> Optional[tuple[str, str]]:
    """``(code, name)`` of the province a point is in; a point within ``margin_deg`` of one (a coast, the simplification) belongs to
    the nearest. ``None`` outside Turkey."""
    point = Point(lon, lat)
    nearest: Optional[tuple[float, str, str]] = None
    for code, name, geometry, prepared in _prepared():
        if prepared.contains(point):  # type: ignore[attr-defined]
            return code, name
        distance = geometry.distance(point)
        if distance <= margin_deg and (nearest is None or distance < nearest[0]):
            nearest = (distance, code, name)
    return None if nearest is None else (nearest[1], nearest[2])

