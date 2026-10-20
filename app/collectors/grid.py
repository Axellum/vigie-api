"""Point grids: the position sent upstream and used in cache keys is snapped to a grid.

The weather models are coarser than the 0.01 degree (about 1 km) the collectors used to round to:
AROME 1.5 km, ICON-D2 2 km, HRRR 3 km, IFS 9 km and more. Two clients a few kilometres apart get
the same model values, so they share one upstream call and one cache entry.

Only the upstream request and the cache key use the snapped point. The exact position of the client
is kept by the callers: it is what the responses report and what the distances to the hazards use.

The step of each grid (in degrees) comes from an environment variable:

=================================  =======  =========================================
Variable                           Default  Used for
=================================  =======  =========================================
``VIGIE_GRID_FORECAST_DEG``        0.05     Open-Meteo forecast and MET Norway
``VIGIE_GRID_AIR_QUALITY_DEG``     0.1      Open-Meteo Air Quality
``VIGIE_GRID_FLOOD_DEG``           0.1      Open-Meteo Flood (GloFAS)
``VIGIE_GRID_MARINE_DEG``          0.1      Open-Meteo Marine
=================================  =======  =========================================

A value that is not a number between 0.0001 and 10 is ignored (the default is used).
"""

from __future__ import annotations

import math
import os

DEFAULT_FORECAST_GRID_DEG = 0.05
DEFAULT_AIR_QUALITY_GRID_DEG = 0.1
DEFAULT_FLOOD_GRID_DEG = 0.1
DEFAULT_MARINE_GRID_DEG = 0.1

# 4 decimals is also the most MET Norway accepts (a request with more returns 403).
MIN_GRID_DEG = 0.0001
MAX_GRID_DEG = 10.0


def grid_step_from_env(name: str, default: float) -> float:
    """Read a grid step in degrees from the environment variable ``name``; ``default`` when unusable."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or not (MIN_GRID_DEG <= value <= MAX_GRID_DEG):
        return default
    return value


def snap_to_grid(lat: float, lon: float, step_deg: float) -> tuple[float, float]:
    """Return the grid point nearest to ``(lat, lon)`` for a grid of ``step_deg`` degrees.

    The grid is made of the multiples of ``step_deg``. The result has at most 4 decimals, the latitude
    stays within [-90, 90] and the longitude within [-180, 180].
    """
    snapped_lat = round(float(lat) / step_deg) * step_deg
    snapped_lon = round(float(lon) / step_deg) * step_deg
    snapped_lat = min(90.0, max(-90.0, snapped_lat))
    if snapped_lon > 180.0:
        snapped_lon -= 360.0
    elif snapped_lon < -180.0:
        snapped_lon += 360.0
    # ``+ 0.0`` turns a negative zero into 0.0 so that the cache key never reads "-0.0000".
    return round(snapped_lat, 4) + 0.0, round(snapped_lon, 4) + 0.0


def coord_str(value: float) -> str:
    """Format a coordinate for a URL: at most 4 decimals, no trailing zeros."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text
