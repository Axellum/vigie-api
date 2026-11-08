"""Calibrate the EOT20 tide of a port against months of REFMAR observations (ADR 0019), offline.

    python tools/calibrate_tides.py --cache-dir DIR                 # fetch what is missing (16 days a request), print the report
    python tools/calibrate_tides.py --cache-dir DIR --write         # and write app/data/tides-calibration.json
    python tools/calibrate_tides.py --cache-dir DIR --station 3 --station 6349

REFMAR (SHOM, Licence Ouverte 2.0), ``services.data.shom.fr/maregraphie/observation/json/{id}``, raw minute data (``sources=1``),
smoothed by the centred 61-minute mean of ``tools/measure_tides.py``. Requests cover 16 days (the service allows 31), one after the other
with a pause, and are kept in the cache folder: a second run asks nothing. The criterion, the periods and the thresholds are in
``docs/decisions/0019-tide-calibration-per-port.md`` and were written before any figure was seen.

The correction is ``corrected(t) = amplitude * model(t - delay)``; ``delay`` > 0 means the real tide is later than the model's.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.tides import Extreme, TideModel, find_extremes, mjd_from_unix  # noqa: E402
from tools import measure_tides as mt  # noqa: E402

OUTPUT = REPO_ROOT / "app" / "data" / "tides-calibration.json"
USER_AGENT = "vigie-api calibrate_tides (personal project)"
TIDEGAUGES_URL = "https://services.data.shom.fr/maregraphie/service/tidegauges"
SOURCE = "SHOM, REFMAR, Licence Ouverte 2.0"
PAUSE_SECONDS = 1.5
CHUNK_DAYS = 16

# ADR 0019.
PERIOD_START = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
FIT_END = dt.datetime(2026, 9, 27, tzinfo=dt.UTC)  # exclusive: the final test starts here (the window of ADR 0015)
TEST_END = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)
FOLDS = 4
DELAY_RANGE_MIN = 45
MIN_RMS_GAIN = 0.10
MIN_FOLDS_BETTER = 3
AMPLITUDE_RANGE = (0.85, 1.15)
MAX_DELAY_MIN = 30
MIN_COVERAGE = 0.70
# The stations the file is built for: Brest, and the REFMAR station nearest to Lorient in the service's own list (read on 2026-11-08).
DEFAULT_STATIONS = ("3", "6349")


@dataclass
class Segment:
    """A part of the period: the indices of the 10-minute grid and the bounds (MJD)."""

    name: str
    first: float
    last: float
    mask: np.ndarray


# ---------------------------------------------------------------------------------------------------------------- downloads


def fetch_stations(cache_dir: Path) -> list[dict[str, Any]]:
    import gzip

    cache = cache_dir / "tidegauges.json.gz"
    if cache.exists():
        return json.loads(gzip.decompress(cache.read_bytes()))
    import httpx

    response = httpx.get(TIDEGAUGES_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=60)
    response.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(gzip.compress(response.content))
    return response.json()


def fetch_observations(station_id: str, cache_dir: Path) -> list[tuple[float, float]]:
    """Every raw observation (MJD, metres) of the period, a day on each side, in requests of CHUNK_DAYS."""
    import gzip

    import httpx

    start, end = PERIOD_START - dt.timedelta(days=1), TEST_END + dt.timedelta(days=1)
    values: list[tuple[float, float]] = []
    first = start
    while first < end:
        last = min(first + dt.timedelta(days=CHUNK_DAYS), end)
        cache = cache_dir / f"calib-{station_id}-{first:%Y%m%d}.json.gz"
        if cache.exists():
            payload = json.loads(gzip.decompress(cache.read_bytes()))
        else:
            params = {"sources": "1", "dtStart": f"{first:%Y-%m-%dT%H:%M:%SZ}", "dtEnd": f"{last:%Y-%m-%dT%H:%M:%SZ}"}
            response = httpx.get(
                mt.REFMAR_URL.format(station=station_id), params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=120
            )
            response.raise_for_status()
            payload = response.json()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))
            time.sleep(PAUSE_SECONDS)
        values.extend(mt.parse_refmar(payload))
        first = last
    return sorted(set(values))


# --------------------------------------------------------------------------------------------------------------------- maths


def grid_of_period() -> np.ndarray:
    start = mjd_from_unix(PERIOD_START.timestamp())
    count = int((TEST_END - PERIOD_START).total_seconds() // (mt.STEP_MINUTES * 60))
    return start + np.arange(count) * mt.STEP_MINUTES / 1440.0


def observed_series(values: list[tuple[float, float]], grid: np.ndarray) -> tuple[np.ndarray, list[Extreme]]:
    """The smoothed observations on the grid (NaN where unknown) and the extremes of the whole smoothed series."""
    minute_mjd, smoothed = mt.smooth_minutes(values)
    heights = np.full(len(grid), np.nan)
    if len(minute_mjd):
        index = np.round((grid - minute_mjd[0]) * 1440.0).astype(np.int64)
        inside = (index >= 0) & (index < len(minute_mjd))
        heights[inside] = smoothed[index[inside]]
    return heights, mt.extremes_of_gappy(minute_mjd, smoothed)


def model_at(model: TideModel, constants: np.ndarray, mjd: np.ndarray) -> np.ndarray:
    out = np.empty(len(mjd))
    for i in range(0, len(mjd), 20000):
        out[i : i + 20000] = model.heights(constants, mjd[i : i + 20000])
    return out


def fit(grid: np.ndarray, observed: np.ndarray, model: TideModel, constants: np.ndarray, train: np.ndarray) -> Optional[tuple[float, float]]:
    """(amplitude, delay in minutes) minimising the squared error of ``observed ~ offset + amplitude * model(t - delay)`` on ``train``."""
    use = train & np.isfinite(observed)
    if use.sum() < 500:
        return None
    best: Optional[tuple[float, float, float]] = None
    target = observed[use] - observed[use].mean()
    for delay in range(-DELAY_RANGE_MIN, DELAY_RANGE_MIN + 1):
        shifted = model_at(model, constants, grid[use] - delay / 1440.0)
        centred = shifted - shifted.mean()
        amplitude = float(centred @ target / (centred @ centred))
        error = float(np.sum((target - amplitude * centred) ** 2))
        if best is None or error < best[0]:
            best = (error, amplitude, float(delay))
    assert best is not None
    return best[1], best[2]


def correct_extremes(extremes: list[Extreme], amplitude: float, delay: float) -> list[Extreme]:
    """The extremes of ``amplitude * model(t - delay)``: each later by ``delay`` minutes and ``amplitude`` times as high."""
    return [Extreme(e.mjd + delay / 1440.0, e.kind, e.height_m * amplitude) for e in extremes]


def segments(grid: np.ndarray) -> tuple[list[Segment], Segment]:
    fit_last = mjd_from_unix(FIT_END.timestamp())
    fit_indices = np.flatnonzero(grid < fit_last)
    folds = []
    for i, block in enumerate(np.array_split(fit_indices, FOLDS)):
        mask = np.zeros(len(grid), dtype=bool)
        mask[block] = True
        folds.append(Segment(f"fold {i + 1}", float(grid[block[0]]), float(grid[block[-1]]) + mt.STEP_MINUTES / 1440.0, mask))
    mask = grid >= fit_last
    test = Segment("final test", float(grid[mask][0]), float(grid[mask][-1]) + mt.STEP_MINUTES / 1440.0, mask)
    return folds, test


def corrected_heights(model: TideModel, constants: np.ndarray, grid: np.ndarray, mask: np.ndarray, correction: tuple[float, float]) -> np.ndarray:
    amplitude, delay = correction
    heights = np.full(len(grid), np.nan)
    heights[mask] = amplitude * model_at(model, constants, grid[mask] - delay / 1440.0)
    return heights


def score(
    segment: Segment, grid: np.ndarray, observed: np.ndarray, observed_extremes: list[Extreme], model: TideModel, constants: np.ndarray,
    plain_extremes: list[Extreme], correction: Optional[tuple[float, float]],
) -> Optional[dict[str, Any]]:  # fmt: skip
    """The four figures of ADR 0019 on a segment, for the model as it is (``correction`` None) or corrected. None: too little data."""
    known = segment.mask & np.isfinite(observed)
    if known.sum() < MIN_COVERAGE * segment.mask.sum():
        return None
    heights = corrected_heights(model, constants, grid, segment.mask, correction or (1.0, 0.0))
    amplitude, delay = correction or (1.0, 0.0)
    span = mt.mean_range(observed_extremes, segment.first, segment.last)
    if span is None:
        return None
    model_mean, reference_mean = float(heights[known].mean()), float(observed[known].mean())
    significant = mt.significant_extremes(observed_extremes, span, segment.first, segment.last)
    pairs = mt.match(significant, correct_extremes(plain_extremes, amplitude, delay))
    time_errors = np.array([(found.mjd - wanted.mjd) * 1440.0 for wanted, found in pairs])
    height_errors = np.array([(found.height_m - model_mean) - (wanted.height_m - reference_mean) for wanted, found in pairs])
    difference = (heights[known] - model_mean) - (observed[known] - reference_mean)
    return {
        "mean_range_m": round(span, 3),
        "extremes": len(significant),
        "matched": len(pairs),
        "rms_m": round(float(np.sqrt(np.mean(difference**2))), 4),
        "time_error_median_min": round(float(np.median(np.abs(time_errors))), 1) if len(pairs) else None,
        "time_bias_median_min": round(float(np.median(time_errors)), 1) if len(pairs) else None,
        "height_error_median_m": round(float(np.median(np.abs(height_errors))), 4) if len(pairs) else None,
    }


def pooled(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The figures of several segments together: RMS over all their points, medians weighted by the matched extremes."""
    weights = np.array([row["matched"] for row in rows], dtype=float)
    result: dict[str, Any] = {"extremes": sum(row["extremes"] for row in rows), "matched": int(weights.sum())}
    result["rms_m"] = round(float(np.sqrt(np.average([row["rms_m"] ** 2 for row in rows]))), 4)
    for key in ("time_error_median_min", "height_error_median_m"):
        result[key] = round(float(np.average([row[key] for row in rows], weights=weights)), 4) if weights.sum() else None
    return result


def decide(cross: dict[str, Any], test: dict[str, Any], fold_gains: list[bool], amplitude: float, delay: float) -> list[str]:
    """The reasons not to apply the correction (ADR 0019, decision 5); empty: apply."""
    reasons = []
    for name, before_after in (("cross-validation", cross), ("final test", test)):
        before, after = before_after["before"], before_after["after"]
        if after["rms_m"] > (1.0 - MIN_RMS_GAIN) * before["rms_m"]:
            reasons.append(f"{name}: RMS falls by less than {MIN_RMS_GAIN:.0%}")
        for key, label in (("time_error_median_min", "time error"), ("height_error_median_m", "height error")):
            if before[key] is None or after[key] is None or after[key] > before[key]:
                reasons.append(f"{name}: the median {label} rises")
    if sum(fold_gains) < MIN_FOLDS_BETTER:
        reasons.append(f"the RMS falls in only {sum(fold_gains)} of {len(fold_gains)} blocks")
    if not AMPLITUDE_RANGE[0] <= amplitude <= AMPLITUDE_RANGE[1]:
        reasons.append(f"amplitude {amplitude:.3f} outside {AMPLITUDE_RANGE[0]} to {AMPLITUDE_RANGE[1]}")
    if abs(delay) > MAX_DELAY_MIN:
        reasons.append(f"delay {delay:+.0f} min beyond {MAX_DELAY_MIN}")
    return reasons


def calibrate_station(station: dict[str, Any], model: TideModel, cache_dir: Path) -> dict[str, Any]:
    grid = grid_of_period()
    observed, observed_extremes = observed_series(fetch_observations(str(station["shom_id"]), cache_dir), grid)
    cell = model.nearest_cell(station["latitude"], station["longitude"])
    folds, test = segments(grid)
    fit_mask = ~test.mask
    minutes = np.arange(grid[0] - 1.0, grid[-1] + 1.0, 1.0 / 1440.0)
    plain_extremes = find_extremes(minutes, model_at(model, cell.constants, minutes))
    known_share = float(np.isfinite(observed)[fit_mask].mean())
    record: dict[str, Any] = {
        "station_id": str(station["shom_id"]), "name": station["name"].title().replace("_", " "),
        "lat": station["latitude"], "lon": station["longitude"],
        "cell": {"lat": cell.lat, "lon": cell.lon, "distance_km": round(cell.distance_km, 1)},
        "observed_share": round(known_share, 3),
    }  # fmt: skip
    if known_share < MIN_COVERAGE:
        return record | {"applied": False, "reasons": [f"only {known_share:.0%} of the 10-minute values in the fit period"]}
    correction = fit(grid, observed, model, cell.constants, fit_mask)
    if correction is None:
        return record | {"applied": False, "reasons": ["too few observations to fit"]}
    amplitude, delay = correction
    before_rows, after_rows, fold_gains, fold_report = [], [], [], []
    for fold in folds:
        others = fit_mask & ~fold.mask
        fold_fit = fit(grid, observed, model, cell.constants, others)
        if fold_fit is None:
            continue
        before = score(fold, grid, observed, observed_extremes, model, cell.constants, plain_extremes, None)
        after = score(fold, grid, observed, observed_extremes, model, cell.constants, plain_extremes, fold_fit)
        if before is None or after is None:
            continue
        before_rows.append(before)
        after_rows.append(after)
        fold_gains.append(after["rms_m"] < before["rms_m"])
        fold_report.append({"name": fold.name, "amplitude": round(fold_fit[0], 4), "delay_minutes": fold_fit[1], "before": before, "after": after})
    test_before = score(test, grid, observed, observed_extremes, model, cell.constants, plain_extremes, None)
    test_after = score(test, grid, observed, observed_extremes, model, cell.constants, plain_extremes, correction)
    if not before_rows or test_before is None or test_after is None:
        return record | {"applied": False, "reasons": ["too few observations to validate"]}
    cross = {"before": pooled(before_rows), "after": pooled(after_rows)}
    final = {"before": test_before, "after": test_after}
    reasons = decide(cross, final, fold_gains, amplitude, delay)
    return record | {
        "amplitude": round(amplitude, 4), "delay_minutes": delay, "applied": not reasons, "reasons": reasons,
        "cross_validation": cross | {"blocks": fold_report}, "final_test": final,
    }  # fmt: skip


def calibrate(cache_dir: Path, station_ids: tuple[str, ...]) -> dict[str, Any]:
    model = TideModel()
    listing = {str(item["shom_id"]): item for item in fetch_stations(cache_dir)}
    stations = []
    for station_id in station_ids:
        if station_id not in listing:
            raise SystemExit(f"station {station_id} is not in the list of the service")
        stations.append(calibrate_station(listing[station_id], model, cache_dir))
        print(f"{stations[-1]['name']:20} applied={stations[-1]['applied']} {stations[-1].get('reasons') or ''}", file=sys.stderr)
    return {
        "source": SOURCE,
        "method": (
            "corrected(t) = amplitude * model(t - delay_minutes); least squares on the 10-minute series smoothed by a 61-minute mean, "
            "delay searched from -45 to +45 minutes; applied only if it reduces the error on held-out data (ADR 0019)"
        ),
        "model": model.model,
        "fit_period": [PERIOD_START.date().isoformat(), (FIT_END - dt.timedelta(days=1)).date().isoformat()],
        "test_period": [FIT_END.date().isoformat(), (TEST_END - dt.timedelta(days=1)).date().isoformat()],
        "radius_km": 15.0,
        "stations": stations,
    }


def report(result: dict[str, Any]) -> str:
    lines = ["| Station | Amplitude | Delay (min) | Applied | Set | RMS cm before > after | Time err. min before > after | Height err. cm before > after |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]  # fmt: skip
    for station in result["stations"]:
        if "final_test" not in station:
            lines.append(f"| {station['name']} | – | – | no: {'; '.join(station['reasons'])} | | | | |")
            continue
        for label, data in (("cross-validation", station["cross_validation"]), ("final test", station["final_test"])):
            b, a = data["before"], data["after"]
            lines.append(
                f"| {station['name']} | {station['amplitude']:.3f} | {station['delay_minutes']:+.0f} | "
                f"{'yes' if station['applied'] else 'no'} | {label} | {b['rms_m'] * 100:.1f} > {a['rms_m'] * 100:.1f} | "
                f"{b['time_error_median_min']:.1f} > {a['time_error_median_min']:.1f} | "
                f"{b['height_error_median_m'] * 100:.1f} > {a['height_error_median_m'] * 100:.1f} |"
            )
        if station["reasons"]:
            lines.append(f"| | | | | reasons: {'; '.join(station['reasons'])} | | | |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--station", action="append", help="a REFMAR station id (default: Brest and Lorient)")
    parser.add_argument("--write", action="store_true", help=f"write {OUTPUT.relative_to(REPO_ROOT)}")
    args = parser.parse_args()
    result = calibrate(args.cache_dir, tuple(args.station or DEFAULT_STATIONS))
    print(report(result))
    if args.write:
        OUTPUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
