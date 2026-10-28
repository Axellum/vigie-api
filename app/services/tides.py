"""The tide at a coastal point, computed from the EOT20 ocean tide model (ADR 0013).

No provider is asked: the harmonic constants of the model cells near a coast and a table of the astronomical arguments are
bundled in ``app/data`` (built by ``tools/build_tides.py``, which checks this module against pyTMD). The height at a time is

    h(t) = sum over the constituents k of f_k(t) * (Re(H_k) * cos(theta_k(t)) - Im(H_k) * sin(theta_k(t)))

where ``H`` are the complex constants of the cell (the 17 of EOT20, then 17 minor ones inferred from them by a fixed matrix),
``f`` the nodal factors and ``theta = omega * (t - origin) + phase(t)`` the equilibrium argument plus the nodal angle; ``f`` and
``phase`` vary slowly and are interpolated in the table. This is pyTMD's prediction with the FES nodal corrections.

A point gets the tide of the nearest model cell within ``MAX_DISTANCE_KM``: no cell that near, no tide (``NoTideHere``). The
heights are relative to the mean sea level of the model, not to the chart datum of a port. Not for navigation.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import numpy as np

from app.attribution import TIDES, credit_text
from app.models.tides import (
    CoastalTideFeature,
    CoastalTideProperties,
    CoastalTides,
    CoastalTidesMetadata,
    TideCell,
    TideCoefficient,
    TideExtreme,
    TideHeight,
    TideNow,
    TidesQuery,
    TidesResponse,
)
from app.services.geodesy import haversine_km

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CONSTANTS_FILE = DATA_DIR / "tides-eot20.npz"
ARGUMENTS_FILE = DATA_DIR / "tides-arguments.npz"

# A point further than this from every model cell kept by the build has no tide (inland, or a coast the model does not resolve).
MAX_DISTANCE_KM = 30.0
# Seconds between the Unix epoch and the Modified Julian Date 0 is not needed: MJD = Unix days + this.
UNIX_EPOCH_MJD = 40587.0
# The heights are computed every minute to find the high and low waters.
EXTREMES_STEP_MINUTES = 1
# Two neighbouring extremes closer than this in height are a ripple of the curve, not a tide: the dip between them is dropped.
MIN_EXTREME_DIFFERENCE_M = 0.02

# The estimated French coefficient (ours, unofficial; ADR 0013): the range of a semi-diurnal tide at Brest divided by this unit, times 100.
# 6.10 m is the unit that secondary sources give for Brest (the mean range of equinoctial spring tides); SHOM's own text was not read.
COEFFICIENT_UNIT_M = 6.10
# The tide gauge of Brest (REFMAR 3, the SHOM's position): the model cell nearest to it gives the coefficient.
BREST = (48.3829, -4.4950)


class NoTideHere(LookupError):
    """No model cell within the radius of the point: the model gives no tide there."""


@dataclass(frozen=True)
class Cell:
    """A cell of the model grid, with its constants (metres, complex, in the order of the model's constituents)."""

    lat: float
    lon: float
    distance_km: float
    constants: np.ndarray


@dataclass(frozen=True)
class Extreme:
    """A high or a low water: its time (MJD) and height (m)."""

    mjd: float
    kind: Literal["high", "low"]
    height_m: float


class TideModel:
    """The bundled model: the cells near a coast, and the table of the arguments."""

    def __init__(self, constants_file: Path = CONSTANTS_FILE, arguments_file: Path = ARGUMENTS_FILE) -> None:
        with np.load(constants_file, allow_pickle=False) as data:
            self.model = str(data["model"][0])
            self.step_deg = float(data["step_deg"][0])
            self.lat_origin = float(data["lat_origin"][0])
            self.columns = int(data["columns"][0])
            # The cells are stored as differences from the previous cell (it compresses three times better): add them up again.
            # The constants wrap around in int16 when they are differenced; summing in int16 wraps back to the exact values.
            self.keys = np.cumsum(data["cell_delta"].astype(np.int64))
            self.raw_constants = np.cumsum(data["constants_delta"], axis=-1, dtype=np.int16)
            # 1 for a cell next to the model's land, up to the width of the band kept by the build.
            self.rings = data["ring"]
            self.constituents = [str(name) for name in data["constituents"]]
        with np.load(arguments_file, allow_pickle=False) as data:
            self.all_constituents = [str(name) for name in data["constituents"]]
            self.omega = data["omega"]
            self.origin_mjd = float(data["origin_mjd"][0])
            self.table_mjd = data["mjd"]
            self.table_phase = data["phase"]
            self.table_factor = data["factor"]
            self.inference = data["inference"]
        if self.all_constituents[: len(self.constituents)] != self.constituents:
            raise ValueError("the two tide files do not list the constituents in the same order")

    @property
    def first_mjd(self) -> float:
        return float(self.table_mjd[0])

    @property
    def last_mjd(self) -> float:
        return float(self.table_mjd[-1])

    def cell_position(self, key: int) -> tuple[float, float]:
        """Latitude and longitude (-180 to 180) of the centre of a cell."""
        row, column = divmod(int(key), self.columns)
        lon = column * self.step_deg
        return self.lat_origin + row * self.step_deg, lon - 360.0 if lon >= 180.0 else lon

    def nearest_cell(self, lat: float, lon: float, max_km: float = MAX_DISTANCE_KM) -> Cell:
        """The model cell nearest to a point, within ``max_km``; ``NoTideHere`` when there is none."""
        row0 = round((lat - self.lat_origin) / self.step_deg)
        column0 = round((lon % 360.0) / self.step_deg) % self.columns
        rows_out = math.ceil(max_km / (111.0 * self.step_deg)) + 1
        cos_lat = max(math.cos(math.radians(min(abs(lat), 89.0))), 0.01)
        columns_out = min(math.ceil(max_km / (111.0 * self.step_deg * cos_lat)) + 1, self.columns // 2)
        rows = np.arange(row0 - rows_out, row0 + rows_out + 1)
        rows = rows[(rows >= 0) & (rows * self.step_deg + self.lat_origin <= 90.0)]
        columns = np.arange(column0 - columns_out, column0 + columns_out + 1) % self.columns
        candidates = (rows[:, None] * self.columns + columns[None, :]).ravel()
        positions = np.searchsorted(self.keys, candidates)
        inside = positions < len(self.keys)
        positions, candidates = positions[inside], candidates[inside]
        found = positions[self.keys[positions] == candidates]
        best: Optional[tuple[float, int]] = None
        for position in found:
            cell_lat, cell_lon = self.cell_position(int(self.keys[position]))
            distance = haversine_km(lat, lon, cell_lat, cell_lon)
            if distance <= max_km and (best is None or distance < best[0]):
                best = (distance, int(position))
        if best is None:
            raise NoTideHere(f"no cell of the {self.model} model within {max_km:g} km of this point")
        distance, position = best
        cell_lat, cell_lon = self.cell_position(int(self.keys[position]))
        return Cell(lat=cell_lat, lon=cell_lon, distance_km=distance, constants=self.constants_at(position))

    def constants_at(self, positions: np.ndarray | int) -> np.ndarray:
        """The complex constants (metres) of the cells at ``positions`` (one cell: shape (17,); several: (cells, 17))."""
        raw = self.raw_constants[:, :, positions].astype(np.float64) / 1000.0  # millimetres -> metres
        return np.moveaxis(raw[:, 0] + 1j * raw[:, 1], 0, -1)

    def oscillations(self, mjd: np.ndarray) -> np.ndarray:
        """``f * exp(i * theta)`` of every constituent at the times ``mjd`` (Modified Julian Dates, UTC): shape (times, 34)."""
        mjd = np.asarray(mjd, dtype=np.float64)
        if mjd.size and (mjd.min() < self.first_mjd or mjd.max() > self.last_mjd):
            raise ValueError("time outside the table of the tide arguments")
        index = np.clip(np.searchsorted(self.table_mjd, mjd, side="right") - 1, 0, len(self.table_mjd) - 2)
        weight = ((mjd - self.table_mjd[index]) / (self.table_mjd[index + 1] - self.table_mjd[index]))[:, None]
        phase = self.table_phase[index] * (1.0 - weight) + self.table_phase[index + 1] * weight
        factor = self.table_factor[index] * (1.0 - weight) + self.table_factor[index + 1] * weight
        return factor * np.exp(1j * (self.omega[None, :] * (mjd - self.origin_mjd)[:, None] + phase))

    def heights(self, constants: np.ndarray, mjd: np.ndarray) -> np.ndarray:
        """The heights (m, relative to the model's mean sea level) at the times ``mjd`` of a cell (constants of shape (17,):
        heights of shape (times,)) or of several (shape (cells, 17): heights of shape (cells, times))."""
        every = np.concatenate([constants, constants @ self.inference.T], axis=-1)
        return np.real(every @ self.oscillations(mjd).T)


def find_extremes(mjd: np.ndarray, heights: np.ndarray, min_difference_m: float = MIN_EXTREME_DIFFERENCE_M) -> list[Extreme]:
    """The high and low waters of a regularly sampled curve, alternating; a dip smaller than ``min_difference_m`` is dropped.

    Each extreme is placed on the parabola through its sample and the two around it (the curve of a tide is smooth). An extreme at
    the first or last sample is not one (the curve may go on beyond): sample the curve wider than the window.
    """
    if len(heights) < 3:
        return []
    rising = np.diff(heights) > 0
    turns = np.nonzero(rising[:-1] != rising[1:])[0] + 1
    step = float(mjd[1] - mjd[0])
    extremes = []
    for i in turns:
        before, here, after = float(heights[i - 1]), float(heights[i]), float(heights[i + 1])
        curvature = before - 2.0 * here + after
        offset = 0.0 if curvature == 0.0 else max(-0.5, min(0.5, (before - after) / (2.0 * curvature)))
        height = here - (before - after) * offset / 4.0
        extremes.append(Extreme(float(mjd[i]) + offset * step, "high" if rising[i - 1] else "low", height))
    # Merge the ripples: while two neighbours differ by less than the threshold, drop the smaller dip and the less extreme of
    # the two extremes around it, so that what stays alternates and keeps the highest high and the lowest low.
    while len(extremes) >= 2:
        gaps = np.abs(np.diff([extreme.height_m for extreme in extremes]))
        i = int(np.argmin(gaps))
        if gaps[i] >= min_difference_m:
            break
        if len(extremes) == 2:
            extremes.clear()
            break
        # Extremes i and i + 1 are close: the dip is the one that sits between two extremes of the other kind.
        dip = i if i >= 1 else 1
        left, right = dip - 1, dip + 1
        keep_left = (extremes[left].height_m >= extremes[right].height_m) == (extremes[left].kind == "high")
        drop = right if keep_left else left
        for index in sorted((dip, drop), reverse=True):
            del extremes[index]
    return extremes


def mjd_from_unix(seconds: float) -> float:
    """The Modified Julian Date of a Unix time (UTC)."""
    return seconds / 86400.0 + UNIX_EPOCH_MJD


def unix_from_mjd(mjd: float) -> float:
    """The Unix time (UTC) of a Modified Julian Date."""
    return (mjd - UNIX_EPOCH_MJD) * 86400.0


def estimated_coefficients(extremes: list[Extreme]) -> list[tuple[Extreme, int]]:
    """The estimated French coefficient of each high water of Brest that has a low water on either side (ours, ADR 0013)."""
    result: list[tuple[Extreme, int]] = []
    for i in range(1, len(extremes) - 1):
        high = extremes[i]
        if high.kind != "high" or extremes[i - 1].kind != "low" or extremes[i + 1].kind != "low":
            continue
        tide_range = high.height_m - (extremes[i - 1].height_m + extremes[i + 1].height_m) / 2.0
        result.append((high, round(100.0 * tide_range / COEFFICIENT_UNIT_M)))
    return result


_model: Optional[TideModel] = None
_model_lock = threading.Lock()


def model() -> TideModel:
    """The bundled model, read once (about 9 MB of arrays in memory)."""
    global _model
    with _model_lock:
        if _model is None:
            _model = TideModel()
        return _model


# ------------------------------------------------------------------------------------------------------------ the answers

MINUTE = 1.0 / 1440.0
# The window of a curve is read one day wider on each side, so that its first and last extremes are known with their ranges.
PADDING_DAYS = 1.0
# The coast of a box: at most one point per square of the first of these sides (degrees) that keeps the count under the limit.
COAST_SPACINGS = (0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
# The tide now along a coast is read every 5 minutes over the next 26 hours (a diurnal tide has its next high water within 25 h).
COAST_STEP_MINUTES = 5
COAST_HOURS = 26
# The longitude ranges of a box are in 1e-5 degrees (``app/services/fires.py``, ``parse_bbox``).
LON_SCALE = 100_000


def _extreme(extreme: Extreme, previous: Optional[Extreme]) -> TideExtreme:
    return TideExtreme(
        time_utc=round(unix_from_mjd(extreme.mjd) / 60.0) * 60,
        kind=extreme.kind,
        height_m=round(extreme.height_m, 2),
        range_m=None if previous is None else round(abs(extreme.height_m - previous.height_m), 2),
    )


def _now(mjd: float, first_two: np.ndarray, extremes: list[Extreme]) -> tuple[float, Literal["rising", "falling"], Optional[TideExtreme], Optional[TideExtreme]]:
    """The height at ``mjd`` (``first_two``: at ``mjd`` and a little after), whether it rises, and the next high and low waters."""
    following: dict[str, Optional[TideExtreme]] = {"high": None, "low": None}
    for i, extreme in enumerate(extremes):
        if extreme.mjd > mjd and following[extreme.kind] is None:
            following[extreme.kind] = _extreme(extreme, extremes[i - 1] if i else None)
    trend: Literal["rising", "falling"] = "rising" if first_two[1] > first_two[0] else "falling"
    return float(first_two[0]), trend, following["high"], following["low"]


def tides_at(lat: float, lon: float, start_unix: int, days: float, interval_minutes: int, now_unix: float) -> TidesResponse:
    """The tide at a point over ``days`` from ``start_unix``. Raises ``NoTideHere`` (no model cell near) or ``ValueError`` (a time
    outside the table of the arguments)."""
    tide_model = model()
    cell = tide_model.nearest_cell(lat, lon)
    start = mjd_from_unix(start_unix)
    end = start + days
    now = mjd_from_unix(now_unix)
    if min(start, now) - PADDING_DAYS < tide_model.first_mjd or max(end, now + 1.5) + PADDING_DAYS > tide_model.last_mjd:
        first, last = _date(tide_model.first_mjd + PADDING_DAYS), _date(tide_model.last_mjd - PADDING_DAYS - 7.0)
        raise ValueError(f"the tide can be computed from {first} to {last}")
    grid = start + np.arange(int(round(days * 1440 / interval_minutes)) + 1) * interval_minutes * MINUTE
    curve = np.arange(start - PADDING_DAYS, end + PADDING_DAYS, MINUTE)
    extremes = find_extremes(curve, tide_model.heights(cell.constants, curve))
    around_now = now + np.arange(-12 * 60, 36 * 60 + 1) * MINUTE
    now_curve = tide_model.heights(cell.constants, around_now)
    first_two = tide_model.heights(cell.constants, np.array([now, now + MINUTE]))
    height, trend, next_high, next_low = _now(now, first_two, find_extremes(around_now, now_curve))
    return TidesResponse(
        generated_utc=int(now_unix),
        query=TidesQuery(lat=lat, lon=lon, start_utc=start_unix, days=days, interval_minutes=interval_minutes),
        model=tide_model.model,
        cell=TideCell(lat=cell.lat, lon=cell.lon, distance_km=round(cell.distance_km, 1)),
        end_utc=int(round(unix_from_mjd(end))),
        heights=[
            TideHeight(time_utc=int(round(unix_from_mjd(moment))), height_m=round(float(value), 2))
            for moment, value in zip(grid, tide_model.heights(cell.constants, grid), strict=True)
        ],
        extremes=[_extreme(extreme, extremes[i - 1] if i else None) for i, extreme in enumerate(extremes) if start <= extreme.mjd <= end],
        now=TideNow(time_utc=int(now_unix), height_m=round(height, 2), trend=trend, next_high=next_high, next_low=next_low),
        coefficients_estimated=_coefficients(tide_model, start, end),
        attribution=credit_text(*TIDES),
    )


def _coefficients(tide_model: TideModel, start: float, end: float) -> list[TideCoefficient]:
    brest = tide_model.nearest_cell(*BREST)
    curve = np.arange(start - PADDING_DAYS, end + PADDING_DAYS, MINUTE)
    return [
        TideCoefficient(brest_high_water_utc=round(unix_from_mjd(high.mjd) / 60.0) * 60, value=value)
        for high, value in estimated_coefficients(find_extremes(curve, tide_model.heights(brest.constants, curve)))
        if start <= high.mjd <= end
    ]


def _date(mjd: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(unix_from_mjd(mjd)))


def coastal_tides(
    south: float, north: float, lon_ranges: tuple[tuple[int, int], ...], asked: tuple[float, float, float, float], now_unix: float, limit: int
) -> CoastalTides:
    """The tide now at the model cells next to the coast inside a box (``lon_ranges`` in 1e-5 degrees, as ``parse_bbox`` gives
    them), at most ``limit`` of them, thinned on a grid. The answer is the same for five minutes."""
    bucket = int(now_unix // (COAST_STEP_MINUTES * 60)) * COAST_STEP_MINUTES * 60
    return _coastal_tides(south, north, lon_ranges, asked, bucket, limit)


@lru_cache(maxsize=64)
def _coastal_tides(
    south: float, north: float, lon_ranges: tuple[tuple[int, int], ...], asked: tuple[float, float, float, float], now_unix: int, limit: int
) -> CoastalTides:
    tide_model = model()
    positions = np.flatnonzero(tide_model.rings == 1)
    rows, columns = np.divmod(tide_model.keys[positions], tide_model.columns)
    lats = tide_model.lat_origin + rows * tide_model.step_deg
    lons = columns * tide_model.step_deg
    lons = np.where(lons >= 180.0, lons - 360.0, lons)
    lon_e5 = np.round(lons * LON_SCALE).astype(np.int64)
    in_lon = np.zeros(len(lons), dtype=bool)
    for low, high in lon_ranges:
        in_lon |= (lon_e5 >= low) & (lon_e5 <= high)
    inside = (lats >= south) & (lats <= north) & in_lon
    positions, lats, lons = positions[inside], lats[inside], lons[inside]
    spacing, chosen = COAST_SPACINGS[0], np.arange(len(positions))
    for spacing in COAST_SPACINGS:
        blocks = np.floor(lats / spacing).astype(np.int64) * 100_000 + np.floor((lons + 180.0) / spacing).astype(np.int64)
        _, chosen = np.unique(blocks, return_index=True)
        if len(chosen) <= limit:
            break
    chosen = np.sort(chosen)[:limit]
    now = mjd_from_unix(now_unix)
    # From 13 hours before now, so that the next high and low waters have the extreme before them (their range).
    back = 13 * 60 // COAST_STEP_MINUTES
    times = now + np.arange(-back * COAST_STEP_MINUTES, COAST_HOURS * 60 + 1, COAST_STEP_MINUTES) * MINUTE
    curves = tide_model.heights(tide_model.constants_at(positions[chosen]), times) if len(chosen) else np.zeros((0, len(times)))
    features = []
    for index, curve in zip(chosen, curves, strict=True):
        height, trend, next_high, next_low = _now(now, curve[back : back + 2], find_extremes(times, curve))
        lat, lon = float(lats[index]), float(lons[index])
        properties = CoastalTideProperties(lat=lat, lon=lon, height_m=round(height, 2), trend=trend, next_high=next_high, next_low=next_low)
        features.append(CoastalTideFeature(geometry={"type": "Point", "coordinates": [lon, lat]}, properties=properties))
    metadata = CoastalTidesMetadata(
        generated_utc=now_unix, model=tide_model.model, bbox=list(asked), spacing_deg=spacing, count=len(features),
        attribution=credit_text(*TIDES),
    )  # fmt: skip
    return CoastalTides(features=features, metadata=metadata)
