"""The magnetic latitude of a place: the modified apex latitude of IGRF-14, read from a bundled grid.

``app/data/apex-latitudes.json.gz`` holds the modified apex latitude (Richmond 1995) at 110 km of every point of a 2-degree grid,
computed offline by following the field lines of IGRF-14 (``tools/build_magnetic_latitudes.py``). Between the points it is
interpolated linearly in latitude and longitude. The aurora follows this latitude, not the geographic one: a centred dipole would
put Western Europe 5 to 10 degrees too far north.
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

GRID_FILE = Path(__file__).resolve().parents[1] / "data" / "apex-latitudes.json.gz"


@dataclass(frozen=True)
class ApexGrid:
    """The grid of the file: ``rows[i][j]`` is the latitude at ``lat_first + i * step``, ``lon_first + j * step``."""

    epoch: float
    step: float
    lat_first: float
    lon_first: float
    rows: tuple[tuple[float, ...], ...]

    def latitude(self, lat: float, lon: float) -> float:
        """The apex latitude (degrees, negative in the southern magnetic hemisphere) at ``(lat, lon)``, interpolated."""
        if not (math.isfinite(lat) and math.isfinite(lon)):
            raise ValueError("the position is not a number")
        row_f = (max(-90.0, min(90.0, lat)) - self.lat_first) / self.step
        i0 = min(len(self.rows) - 2, max(0, int(math.floor(row_f))))
        di = min(1.0, max(0.0, row_f - i0))
        width = len(self.rows[0])
        col_f = ((lon - self.lon_first) % 360.0) / self.step
        j0 = int(math.floor(col_f)) % width
        j1 = (j0 + 1) % width
        dj = col_f - math.floor(col_f)
        top = self.rows[i0][j0] * (1 - dj) + self.rows[i0][j1] * dj
        bottom = self.rows[i0 + 1][j0] * (1 - dj) + self.rows[i0 + 1][j1] * dj
        return top * (1 - di) + bottom * di


@lru_cache(maxsize=1)
def apex_grid(path: Path = GRID_FILE) -> ApexGrid:
    """The bundled grid, read once."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    return ApexGrid(
        epoch=float(data["epoch"]),
        step=float(data["grid_deg"]),
        lat_first=float(data["lat_first"]),
        lon_first=float(data["lon_first"]),
        rows=tuple(tuple(float(v) for v in row) for row in data["rows"]),
    )


def magnetic_latitude(lat: float, lon: float) -> float:
    """The modified apex latitude of IGRF-14 at ``(lat, lon)`` (degrees, negative in the southern magnetic hemisphere)."""
    return apex_grid().latitude(lat, lon)
