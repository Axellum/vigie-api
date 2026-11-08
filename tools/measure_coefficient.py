"""Measure the estimated coefficient (``app/services/tides.py``) against the observed tide of Brest over several months (ADR 0018).

    python tools/measure_coefficient.py --cache-dir DIR [--start 2026-04-26 --end 2026-10-28]

Two forms of the coefficient are worked out on the model's tide and on the observed one (REFMAR station 3, SHOM, Licence Ouverte 2.0, raw data
smoothed as ``tools/measure_tides.py`` does), at each high water, and the difference model - observed is reported for each:

- ``range``: what the API does, 100 x (high water - mean of the two low waters around it) / 6.10 m;
- ``shom``: SHOM's form, 100 x (high water - mid-tide level N0) / U, with U = 3.05 m (half of 6.10 m; the data sheet gives no value) and N0 the
  model's mean level (0) or, on the observations, the mean of the series (``shom_mean``) or of the 24 h 50 min around the high water (``shom_local``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.tides import BREST, COEFFICIENT_UNIT_M, Extreme, TideModel, find_extremes  # noqa: E402
from tools.measure_tides import REFMAR_URL, _get_json, extremes_of_gappy, parse_refmar, smooth_minutes  # noqa: E402

UNIT_HALF_M = COEFFICIENT_UNIT_M / 2.0
MATCH_HOURS = 3.0
LOCAL_MEAN_MINUTES = int(24.84 * 60)


def observations(cache_dir: Path, start: dt.datetime, end: dt.datetime) -> list[tuple[float, float]]:
    values: list[tuple[float, float]] = []
    moment, part = start, 0
    while moment < end:
        stop = min(moment + dt.timedelta(days=15), end)
        params = {"sources": "1", "dtStart": f"{moment:%Y-%m-%dT%H:%M:%SZ}", "dtEnd": f"{stop:%Y-%m-%dT%H:%M:%SZ}"}
        values.extend(parse_refmar(_get_json(REFMAR_URL.format(station="3"), params, cache_dir / f"coef-refmar-3-{part}.json.gz")))
        moment, part = stop, part + 1
    return sorted(set(values))


def high_waters(extremes: list[Extreme], first: float, last: float) -> list[tuple[Extreme, float]]:
    """Each high water in [first, last) with the mean of the two low waters around it."""
    result = []
    for i in range(1, len(extremes) - 1):
        high = extremes[i]
        if high.kind == "high" and extremes[i - 1].kind == "low" and extremes[i + 1].kind == "low" and first <= high.mjd < last:
            result.append((high, (extremes[i - 1].height_m + extremes[i + 1].height_m) / 2.0))
    return result


def summary(differences: list[float]) -> dict[str, float | int]:
    d = np.array(differences)
    return {"n": len(d), "mean": round(float(d.mean()), 2), "mean_abs": round(float(np.abs(d).mean()), 2),
            "max_abs": round(float(np.abs(d).max()), 1), "rms": round(float(np.sqrt((d**2).mean())), 2)}  # fmt: skip


def measure(cache_dir: Path, start: dt.datetime, end: dt.datetime) -> dict[str, object]:
    minute_mjd, smoothed = smooth_minutes(observations(cache_dir, start, end))
    first, last = float(minute_mjd[0]) + 1.0, float(minute_mjd[-1]) - 1.0
    observed = high_waters(extremes_of_gappy(minute_mjd, smoothed), first, last)
    mean_level = float(np.nanmean(smoothed))
    model = TideModel()
    cell = model.nearest_cell(*BREST)
    times = np.arange(first - 1.0, last + 1.0, 1.0 / 1440.0)
    modelled = high_waters(find_extremes(times, model.heights(cell.constants, times)), first, last)
    half = LOCAL_MEAN_MINUTES // 2
    columns: dict[str, list[float]] = {"range": [], "shom_mean": [], "shom_local": []}
    for high, lows in observed:
        close = min(modelled, key=lambda item: abs(item[0].mjd - high.mjd))
        if abs(close[0].mjd - high.mjd) * 24.0 > MATCH_HOURS:
            continue
        i = int(round((high.mjd - float(minute_mjd[0])) * 1440.0))
        around = smoothed[max(0, i - half) : i + half + 1]
        if np.isfinite(around).sum() < 0.9 * len(around):
            continue
        model_high, model_lows = close
        columns["range"].append(100.0 * ((model_high.height_m - model_lows) - (high.height_m - lows)) / COEFFICIENT_UNIT_M)
        columns["shom_mean"].append(100.0 * (model_high.height_m - (high.height_m - mean_level)) / UNIT_HALF_M)
        columns["shom_local"].append(100.0 * (model_high.height_m - (high.height_m - float(np.nanmean(around)))) / UNIT_HALF_M)
    return {"window": [start.isoformat(), end.isoformat()], "observed_mean_level_m": round(mean_level, 3),
            "difference_model_minus_observed": {name: summary(values) for name, values in columns.items()}}  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--start", default="2026-04-26")
    parser.add_argument("--end", default="2026-10-28")
    args = parser.parse_args()
    day = dt.date.fromisoformat
    start = dt.datetime.combine(day(args.start), dt.time(), tzinfo=dt.UTC)
    end = dt.datetime.combine(day(args.end), dt.time(), tzinfo=dt.UTC)
    print(json.dumps(measure(args.cache_dir, start, end), indent=2))


if __name__ == "__main__":
    main()
