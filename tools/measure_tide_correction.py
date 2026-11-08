"""Measure the correction of the modelled tide by the nearest REFMAR tide gauge (ADR 0017), before it is served.

    python tools/measure_tide_correction.py --cache-dir DIR                      # print the report
    python tools/measure_tide_correction.py --cache-dir DIR --write              # and write docs/measurements/tide-correction-2026-11.md's results
    python tools/measure_tide_correction.py --cache-dir DIR --write-stations     # and write app/data/refmar-stations.json (the datum offsets)

It reads the downloads of ``tools/measure_tides.py`` (the same cache folder, the same window 2026-09-27 to 2026-10-27, the raw REFMAR measures
of one a minute) and asks REFMAR for the stations that are missing. Nothing here asks a provider while the API runs.

The test is a forecast made at a time ``t`` and compared with what the gauge measured at ``t + 1 h``, ``t + 3 h`` and ``t + 6 h``:

- the forecast uses only the measures up to ``t`` minus ``LAG_MINUTES`` (REFMAR posted its latest value 3 to 20 minutes late on 2026-11-08);
- the datum offset of a station and the choice of the window and of the decay come from the **first half** of the window (up to
  ``SPLIT``); the figures of the report are those of the **second half**, which the choice never saw;
- the reference is the 61-minute mean of the measures, as ADR 0015's criterion (the surge is kept, the waves are not);
- the model is what the API serves (EOT20, the cell nearest to the gauge, calibrated where ADR 0019 applies a calibration: Brest), the correction is ``app/services/tide_correction.py`` itself.

Heights: the error of the series at the three leads. High and low waters: every significant extreme of the reference (ADR 0015's rule) is
forecast from ``t = extreme - lead`` with and without the correction, and matched with the extreme of the same kind nearest in time
(within 3 hours): the error of the time and of the height.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import measure_tides as mt  # noqa: E402

from app.services import tide_correction as tc  # noqa: E402
from app.services.tides import (  # noqa: E402
    MINUTE,
    Extreme,
    TideModel,
    calibrated_heights,
    calibration_for,
    find_extremes,
    mjd_from_unix,
)

# The gauges the correction can use: the ten French ports of ADR 0015's criterion, and Lorient (REFMAR 6349, read in the list of the gauges
# `services.data.shom.fr/maregraphie/service/tidegauges` on 2026-11-08: the nearest gauge to Lorient).
STATIONS = [s for s in mt.STATIONS if s.reference == "refmar" and s.in_criterion] + [
    mt.Station("refmar", "6349", "Lorient", 47.7375, -3.3545)
]
FOCUS = ("3", "6349")  # Brest, Lorient: the stations the end criterion names

SPLIT = dt.datetime(2026, 10, 12, tzinfo=dt.UTC)
TEST_END = dt.datetime(2026, 10, 26, tzinfo=dt.UTC)  # the last forecast needs 6 hours of measures after it
LAG_MINUTES = 20
LEADS_MIN = (60, 180, 360)
ORIGIN_STEP_MIN = 30
TUNE_WINDOWS = (60, 180, 745, 1490)
TUNE_TAUS = (1.0, 3.0, 6.0, 12.0, 24.0, 48.0)


@dataclass
class Series:
    """A gauge on a one-minute grid: the measures, their 61-minute mean, and the model at the cell nearest to the gauge."""

    station: mt.Station
    mjd: np.ndarray
    raw: np.ndarray
    smooth: np.ndarray
    model: np.ndarray
    cell_constants: np.ndarray
    calibration: Any
    offset_train: float
    offset_full: float


def build_series(model: TideModel, station: mt.Station, cache_dir: Path) -> Series:
    values = mt.fetch_refmar(station, cache_dir)
    minutes = np.round(np.array([m for m, _ in values]) * 1440.0).astype(np.int64)
    first = int(minutes.min())
    raw = np.full(int(minutes.max()) - first + 1, np.nan)
    raw[minutes - first] = [v for _, v in values]
    mjd, smooth = mt.smooth_minutes(values)
    cell = model.nearest_cell(station.lat, station.lon)
    calibration = calibration_for(station.lat, station.lon)  # ADR 0019: the model as the API serves it (Brest only)
    modelled = calibrated_heights(model, cell.constants, mjd, calibration)
    start = mjd_from_unix(mt.WINDOW_START.timestamp())
    split = mjd_from_unix(SPLIT.timestamp())
    end = mjd_from_unix(mt.WINDOW_END.timestamp())
    gap = smooth - modelled
    train = (mjd >= start) & (mjd < split)
    full = (mjd >= start) & (mjd < end)
    return Series(station, mjd, raw, smooth, modelled, cell.constants, calibration, float(np.nanmean(gap[train])), float(np.nanmean(gap[full])))


def residual(series: Series, offset: float) -> np.ndarray:
    return series.raw - series.model - offset


def origins() -> np.ndarray:
    start = mjd_from_unix(SPLIT.timestamp()) + 0.5
    end = mjd_from_unix(TEST_END.timestamp())
    return np.arange(start, end, ORIGIN_STEP_MIN * MINUTE)


# ------------------------------------------------------------------------------------------------------------------- tuning


def trailing_means(series: Series, window: int, offset: float) -> np.ndarray:
    """The mean residual over the ``window`` minutes up to each minute (NaN where fewer than half exist)."""
    res = residual(series, offset)
    known = np.isfinite(res)
    sums = np.convolve(np.where(known, res, 0.0), np.ones(window))[: len(res)]
    counts = np.convolve(known.astype(float), np.ones(window))[: len(res)]
    return np.where(counts >= tc.MIN_COVERAGE * window, sums / np.maximum(counts, 1.0), np.nan)


def tune(all_series: list[Series]) -> list[dict[str, Any]]:
    """The RMS error of the series at the three leads for each window and decay, on the first half only."""
    start = mjd_from_unix(mt.WINDOW_START.timestamp()) + 1.0
    split = mjd_from_unix(SPLIT.timestamp())
    rows = []
    for window in TUNE_WINDOWS:
        for tau in TUNE_TAUS:
            per_lead = {lead: [] for lead in LEADS_MIN}
            for series in all_series:
                means = trailing_means(series, window, series.offset_train)
                index = np.flatnonzero((series.mjd >= start + window / 1440.0) & (series.mjd < split))[::ORIGIN_STEP_MIN]
                for lead in LEADS_MIN:
                    target = index + lead
                    ahead = index - LAG_MINUTES
                    ok = target < len(series.mjd)
                    target_ok, ahead_ok = target[ok], ahead[ok]
                    error = series.smooth[target_ok] - series.offset_train - series.model[target_ok]
                    error = error - means[ahead_ok] * np.exp(-(lead + LAG_MINUTES) / 60.0 / tau)
                    error = error[np.isfinite(error)]
                    per_lead[lead].append(float(np.sqrt(np.mean(error**2))))
            rows.append({"window_min": window, "tau_h": tau, **{f"rms_{lead}": float(np.mean(per_lead[lead])) for lead in LEADS_MIN}})
    return rows


# ------------------------------------------------------------------------------------------------------------------- scoring


def anomaly_at(series: Series, residuals: np.ndarray, origin: float) -> Optional[tuple[float, float]]:
    """The gap and the time of the last measure of a forecast made at ``origin`` (the function the API runs)."""
    found = tc.trailing_anomaly(series.mjd, residuals, origin - LAG_MINUTES * MINUTE)
    return None if found is None else (found[0], found[1])


def correction_at(moments: np.ndarray, anomaly: float, measured: float) -> np.ndarray:
    hours = (moments - measured) * 24.0
    return np.where(hours >= 0.0, anomaly * np.exp(-np.maximum(hours, 0.0) / tc.TAU_HOURS), 0.0)


def score_heights(series: Series) -> dict[str, Any]:
    res = residual(series, series.offset_train)
    result: dict[str, Any] = {}
    for lead in LEADS_MIN:
        before, after = [], []
        for origin in origins():
            found = anomaly_at(series, res, origin)
            target = origin + lead * MINUTE
            index = int(round((target - series.mjd[0]) / MINUTE))
            if found is None or not 0 <= index < len(series.mjd) or not np.isfinite(series.smooth[index]):
                continue
            truth = series.smooth[index] - series.offset_train
            before.append(truth - series.model[index])
            after.append(truth - series.model[index] - float(correction_at(np.array([target]), *found)[0]))
        before_a, after_a = np.array(before), np.array(after)
        result[lead] = {
            "n": len(before),
            "rms_before_m": float(np.sqrt(np.mean(before_a**2))),
            "rms_after_m": float(np.sqrt(np.mean(after_a**2))),
            "mae_before_m": float(np.mean(np.abs(before_a))),
            "mae_after_m": float(np.mean(np.abs(after_a))),
            "bias_before_m": float(np.mean(before_a)),
            "bias_after_m": float(np.mean(after_a)),
            "better_share": float(np.mean(np.abs(after_a) < np.abs(before_a))),
        }
    return result


def score_extremes(series: Series, model: TideModel) -> dict[str, Any]:
    res = residual(series, series.offset_train)
    first = mjd_from_unix(SPLIT.timestamp()) + 0.5
    last = mjd_from_unix(TEST_END.timestamp())
    reference = mt.extremes_of_gappy(series.mjd, series.smooth)
    span = mt.mean_range(reference, float(series.mjd[0]), float(series.mjd[-1]))
    significant = [e for e in mt.significant_extremes(reference, span or 0.0, float(series.mjd[0]), float(series.mjd[-1])) if first <= e.mjd < last]
    result: dict[str, Any] = {"significant": len(significant), "mean_range_m": span}
    for lead in LEADS_MIN:
        time_before, time_after, height_before, height_after = [], [], [], []
        missed_before = missed_after = 0
        for wanted in significant:
            origin = wanted.mjd - lead * MINUTE
            found = anomaly_at(series, res, origin)
            if found is None:
                continue
            moments = np.arange(wanted.mjd - 9 / 24.0, wanted.mjd + 5 / 24.0, MINUTE)
            curve = calibrated_heights(model, series.cell_constants, moments, series.calibration)
            corrected = curve + np.where(moments >= origin, correction_at(moments, *found), 0.0)
            truth_height = wanted.height_m - series.offset_train
            for label, values in (("before", curve), ("after", corrected)):
                same = [e for e in find_extremes(moments, values) if e.kind == wanted.kind and abs(e.mjd - wanted.mjd) * 24.0 <= mt.MATCH_WINDOW_H]
                if not same:
                    if label == "before":
                        missed_before += 1
                    else:
                        missed_after += 1
                    continue
                nearest: Extreme = min(same, key=lambda e: abs(e.mjd - wanted.mjd))
                (time_before if label == "before" else time_after).append((nearest.mjd - wanted.mjd) * 1440.0)
                (height_before if label == "before" else height_after).append(nearest.height_m - truth_height)
        result[lead] = {
            "n": len(time_before),
            "missed_before": missed_before,
            "missed_after": missed_after,
            "time_median_abs_before_min": float(np.median(np.abs(time_before))),
            "time_median_abs_after_min": float(np.median(np.abs(time_after))),
            "time_mean_abs_before_min": float(np.mean(np.abs(time_before))),
            "time_mean_abs_after_min": float(np.mean(np.abs(time_after))),
            "height_median_abs_before_m": float(np.median(np.abs(height_before))),
            "height_median_abs_after_m": float(np.median(np.abs(height_after))),
            "height_mean_abs_before_m": float(np.mean(np.abs(height_before))),
            "height_mean_abs_after_m": float(np.mean(np.abs(height_after))),
        }
    return result


def transfer(all_series: list[Series]) -> list[dict[str, Any]]:
    """The gap of one gauge applied to another one (the evidence of ``MAX_DISTANCE_KM``): RMS error at +3 h, before and after."""
    from app.services.geodesy import haversine_km

    rows = []
    for a in all_series:
        res = residual(a, a.offset_train)
        for b in all_series:
            if a is b:
                continue
            km = haversine_km(a.station.lat, a.station.lon, b.station.lat, b.station.lon)
            if km > 150.0:
                continue
            before, after = [], []
            for origin in origins():
                found = anomaly_at(a, res, origin)
                target = origin + 180 * MINUTE
                index = int(round((target - b.mjd[0]) / MINUTE))
                if found is None or not 0 <= index < len(b.mjd) or not np.isfinite(b.smooth[index]):
                    continue
                truth = b.smooth[index] - b.offset_train - b.model[index]
                before.append(truth)
                after.append(truth - float(correction_at(np.array([target]), *found)[0]))
            rows.append({"from": a.station.name, "to": b.station.name, "km": km, "rms_before_m": float(np.sqrt(np.mean(np.square(before)))),
                         "rms_after_m": float(np.sqrt(np.mean(np.square(after))))})  # fmt: skip
    return sorted(rows, key=lambda row: row["km"])


def measure(cache_dir: Path) -> dict[str, Any]:
    model = TideModel()
    all_series = [build_series(model, station, cache_dir) for station in STATIONS]
    scores = []
    for series in all_series:
        scores.append({"station": series.station.name, "id": series.station.station_id, "offset_train_m": series.offset_train,
                       "offset_full_m": series.offset_full, "heights": score_heights(series), "extremes": score_extremes(series, model)})  # fmt: skip
        print(f"scored {series.station.name}", file=sys.stderr)
    return {
        "split": SPLIT.isoformat(),
        "lag_minutes": LAG_MINUTES,
        "chosen": {"window_min": tc.WINDOW_MINUTES, "tau_h": tc.TAU_HOURS, "max_km": tc.MAX_DISTANCE_KM},
        "tuning_first_half": tune(all_series),
        "scores": scores,
        "transfer": transfer(all_series),
        "series": {s.station.station_id: s for s in all_series},
    }


# ------------------------------------------------------------------------------------------------------------------- report


def _cm(value: float) -> str:
    return f"{value * 100:.1f}"


def tables(results: dict[str, Any]) -> str:
    lines = ["### Heights: RMS error of the series (cm), forecast at t, compared with the measure at t + lead", "",
             "| Station | +1 h before | +1 h after | +3 h before | +3 h after | +6 h before | +6 h after |", "| --- | --- | --- | --- | --- | --- | --- |"]  # fmt: skip
    pooled = {lead: ([], []) for lead in LEADS_MIN}
    for score in results["scores"]:
        cells = []
        for lead in LEADS_MIN:
            item = score["heights"][lead]
            cells += [_cm(item["rms_before_m"]), _cm(item["rms_after_m"])]
            pooled[lead][0].append(item["rms_before_m"])
            pooled[lead][1].append(item["rms_after_m"])
        lines.append(f"| {score['station']} | " + " | ".join(cells) + " |")
    lines.append("| **mean of the stations** | " + " | ".join(_cm(float(np.mean(pooled[lead][i]))) for lead in LEADS_MIN for i in (0, 1)) + " |")
    lines += ["", "### High and low waters: median absolute error of the time (min) and of the height (cm), forecast at extreme - lead", "",
              "| Station | Extremes | Lead | Time before | Time after | Height before | Height after | Not found before / after |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]  # fmt: skip
    for score in results["scores"]:
        for lead in LEADS_MIN:
            item = score["extremes"][lead]
            lines.append(
                f"| {score['station']} | {item['n']} of {score['extremes']['significant']} | +{lead // 60} h | {item['time_median_abs_before_min']:.1f} "
                f"| {item['time_median_abs_after_min']:.1f} | {_cm(item['height_median_abs_before_m'])} | {_cm(item['height_median_abs_after_m'])} "
                f"| {item['missed_before']} / {item['missed_after']} |"
            )
    return "\n".join(lines)


def tuning_table(results: dict[str, Any]) -> str:
    lines = ["| Window (min) | Decay (h) | RMS +1 h (cm) | RMS +3 h (cm) | RMS +6 h (cm) | Mean of the three |", "| --- | --- | --- | --- | --- | --- |"]
    for row in results["tuning_first_half"]:
        mean = np.mean([row[f"rms_{lead}"] for lead in LEADS_MIN])
        lines.append(f"| {row['window_min']} | {row['tau_h']:g} | {_cm(row['rms_60'])} | {_cm(row['rms_180'])} | {_cm(row['rms_360'])} | {_cm(float(mean))} |")
    return "\n".join(lines)


def transfer_table(results: dict[str, Any]) -> str:
    lines = ["| Gap measured at | Applied at | Distance (km) | RMS +3 h before (cm) | after (cm) |", "| --- | --- | --- | --- | --- |"]
    for row in results["transfer"]:
        lines.append(f"| {row['from']} | {row['to']} | {row['km']:.0f} | {_cm(row['rms_before_m'])} | {_cm(row['rms_after_m'])} |")
    return "\n".join(lines)


def write_stations(results: dict[str, Any]) -> Path:
    target = REPO_ROOT / "app" / "data" / "refmar-stations.json"
    stations = [
        {"id": s.station.station_id, "name": s.station.name, "lat": s.station.lat, "lon": s.station.lon, "offset_m": round(s.offset_full, 3)}
        for s in results["series"].values()
    ]
    document = {
        "note": "REFMAR tide gauges (SHOM, Licence Ouverte 2.0) that correct the modelled tide (ADR 0017). offset_m: the mean of (smoothed measure "
        "- EOT20 at the cell nearest to the gauge) over 2026-09-27 to 2026-10-27, i.e. the chart datum of the gauge against the model's mean sea "
        "level; built by tools/measure_tide_correction.py.",
        "stations": stations,
    }
    target.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--write", action="store_true", help="write docs/measurements/tide-correction-2026-11-results.json.gz")
    parser.add_argument("--write-stations", action="store_true", help="write app/data/refmar-stations.json")
    args = parser.parse_args()
    results = measure(args.cache_dir)
    print("## Tuning, first half\n" + tuning_table(results) + "\n\n## Transfer\n" + transfer_table(results) + "\n\n## Test, second half\n" + tables(results))
    if args.write_stations:
        print(f"wrote {write_stations(results).relative_to(REPO_ROOT)}")
    if args.write:
        target = REPO_ROOT / "docs" / "measurements" / "tide-correction-2026-11-results.json.gz"
        slim = {k: v for k, v in results.items() if k != "series"}
        target.write_bytes(gzip.compress(json.dumps(slim, indent=1).encode("utf-8"), mtime=0))
        print(f"wrote {target.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
