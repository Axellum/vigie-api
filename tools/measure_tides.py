"""Measure the tide the API computes (EOT20, ``app/services/tides.py``) against references, by the criterion of ADR 0012.

    python tools/measure_tides.py --cache-dir DIR                       # download what is missing into DIR, print the report
    python tools/measure_tides.py --cache-dir DIR --write               # and write docs/measurements/tides-2026-10.md and its results

The criterion, the window and the stations were fixed in ADR 0012 before any figure was seen (commit acb9417); this tool applies
them and changes none:

- **France**: the observations of REFMAR (SHOM, Licence Ouverte 2.0), raw high-frequency data (``sources=1``, one value a minute, UTC,
  metres above the chart datum), from ``services.data.shom.fr/maregraphie/observation/json/{id}``, smoothed by a centred 61-minute mean
  (a minute counts when at least 31 of the 61 values exist).
- **United States**: the predictions of NOAA CO-OPS (``api.tidesandcurrents.noaa.gov``, ``datum=MSL``, ``time_zone=gmt``,
  ``units=metric``): the series every 10 minutes and the high and low waters (``interval=hilo``).

The downloads are kept in the cache folder (a REFMAR request covers 16 days, under its limit of 31): a second run reads them and asks
nothing. The User-Agent names the tool and carries no address.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.tides import (  # noqa: E402
    BREST,
    COEFFICIENT_UNIT_M,
    Extreme,
    NoTideHere,
    TideModel,
    estimated_coefficients,
    find_extremes,
    mjd_from_unix,
)

USER_AGENT = "vigie-api measure_tides (personal project)"
REFMAR_URL = "https://services.data.shom.fr/maregraphie/observation/json/{station}"
NOAA_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"

# ADR 0012: the window, the thresholds and the stations.
WINDOW_START = dt.datetime(2026, 9, 27, tzinfo=dt.UTC)
WINDOW_END = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)
MARGIN = dt.timedelta(days=1)
STEP_MINUTES = 10
SMOOTHING_MINUTES = 61
MAX_TIME_ERROR_MIN = 20.0
MAX_HEIGHT_ERROR_SHARE = 0.10
MAX_RMS_SHARE = 0.10
MAX_UNMATCHED_SHARE = 0.05
SIGNIFICANT_SHARE = 0.25
MATCH_WINDOW_H = 3.0
MIN_COVERAGE = 0.70
PASSES_NEEDED = 8
MIN_MEASURED = 7


@dataclass(frozen=True)
class Station:
    reference: str  # "refmar" or "noaa"
    station_id: str
    name: str
    lat: float
    lon: float
    in_criterion: bool = True


# Positions: REFMAR's list of tide gauges (services.data.shom.fr/maregraphie/service/tidegauges) and NOAA's metadata API (mdapi),
# read on 2026-10-28.
STATIONS = [
    Station("refmar", "55", "Calais", 50.9694, 1.8677),
    Station("refmar", "4", "Le Havre", 49.481892, 0.10598),
    Station("refmar", "13", "Cherbourg", 49.651447, -1.635508),
    Station("refmar", "410", "Saint-Malo", 48.640812, -2.028103),
    Station("refmar", "54", "Roscoff", 48.718426, -3.965679),
    Station("refmar", "3", "Brest", 48.3829, -4.49504),
    Station("refmar", "160", "Concarneau", 47.873549, -3.907207),
    Station("refmar", "62", "Les Sables-d'Olonne", 46.497358, -1.793528),
    Station("refmar", "34", "La Rochelle-La Pallice", 46.1585, -1.22065),
    Station("refmar", "95", "Saint-Jean-de-Luz Socoa", 43.39524, -1.68162),
    Station("refmar", "190", "Arcachon Eyrac", 44.665, -1.16355, in_criterion=False),
    Station("refmar", "94", "Bayonne Boucau", 43.52732, -1.51483, in_criterion=False),
    Station("refmar", "524", "Marseille", 43.278814, 5.353758, in_criterion=False),
    Station("noaa", "8443970", "Boston", 42.35389, -71.05028),
    Station("noaa", "8518750", "The Battery, New York", 40.700554, -74.01417),
    Station("noaa", "8665530", "Charleston", 32.780834, -79.923615),
    Station("noaa", "8723214", "Virginia Key", 25.7314, -80.1618),
    Station("noaa", "8729840", "Pensacola", 30.4044, -87.2112),
    Station("noaa", "8775870", "Bob Hall Pier, Corpus Christi", 27.58, -97.2167),
    Station("noaa", "9410170", "San Diego", 32.715557, -117.17667),
    Station("noaa", "9414290", "San Francisco", 37.806305, -122.46589),
    Station("noaa", "9447130", "Seattle", 47.60264, -122.3393),
    Station("noaa", "1612340", "Honolulu", 21.303333, -157.86453),
    Station("noaa", "9455920", "Anchorage", 61.2375, -149.8904, in_criterion=False),
]


@dataclass
class Reference:
    """A reference: its 10-minute series on the window (NaN where unknown) and its extremes around the window."""

    mjd: np.ndarray
    heights: np.ndarray
    extremes: list[Extreme]


@dataclass
class Score:
    station: str
    reference: str
    station_id: str
    in_criterion: bool
    cell_lat: Optional[float] = None
    cell_lon: Optional[float] = None
    cell_distance_km: Optional[float] = None
    coverage: Optional[float] = None
    mean_range_m: Optional[float] = None
    significant: int = 0
    matched: int = 0
    time_error_median_min: Optional[float] = None
    time_bias_median_min: Optional[float] = None
    height_error_median_m: Optional[float] = None
    rms_m: Optional[float] = None
    status: str = "not measured"  # "pass", "fail", "not measured"
    reasons: list[str] = field(default_factory=list)

    @property
    def unmatched_share(self) -> Optional[float]:
        return None if not self.significant else 1.0 - self.matched / self.significant


# ---------------------------------------------------------------------------------------------------------------- downloads


def _get_json(url: str, params: dict[str, str], cache: Path) -> Any:
    if cache.exists():
        return json.loads(gzip.decompress(cache.read_bytes()))
    import httpx

    response = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=120)
    response.raise_for_status()
    payload = response.json()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))
    return payload


def fetch_refmar(station: Station, cache_dir: Path) -> list[tuple[float, float]]:
    """The raw observations (MJD, metres above the chart datum) from a day before the window to a day after, in two requests."""
    start, end = WINDOW_START - MARGIN, WINDOW_END + MARGIN
    middle = start + (end - start) / 2
    values: list[tuple[float, float]] = []
    for part, (first, last) in enumerate([(start, middle), (middle, end)]):
        params = {"sources": "1", "dtStart": f"{first:%Y-%m-%dT%H:%M:%SZ}", "dtEnd": f"{last:%Y-%m-%dT%H:%M:%SZ}"}
        payload = _get_json(REFMAR_URL.format(station=station.station_id), params, cache_dir / f"refmar-{station.station_id}-{part}.json.gz")
        values.extend(parse_refmar(payload))
    return sorted(set(values))


def parse_refmar(payload: dict[str, Any]) -> list[tuple[float, float]]:
    """REFMAR's JSON (``data``: ``timestamp`` "YYYY/MM/DD HH:MM:SS" in UTC, ``value`` in metres) as (MJD, metres)."""
    values = []
    for item in payload.get("data") or []:
        value = item.get("value")
        if value is None:
            continue
        moment = dt.datetime.strptime(item["timestamp"], "%Y/%m/%d %H:%M:%S").replace(tzinfo=dt.UTC)
        values.append((mjd_from_unix(moment.timestamp()), float(value)))
    return values


def fetch_noaa(station: Station, cache_dir: Path) -> tuple[list[tuple[float, float]], list[Extreme]]:
    """NOAA's predictions (MSL, metres, UTC): the series every 10 minutes and the high and low waters, a day around the window."""
    start, end = WINDOW_START - MARGIN, WINDOW_END + MARGIN
    common = {"product": "predictions", "application": "vigie-api_measure_tides", "begin_date": f"{start:%Y%m%d}",
              "end_date": f"{end:%Y%m%d}", "datum": "MSL", "station": station.station_id, "time_zone": "gmt",
              "units": "metric", "format": "json"}  # fmt: skip
    series = _get_json(NOAA_URL, {**common, "interval": "10"}, cache_dir / f"noaa-{station.station_id}-10.json.gz")
    hilo = _get_json(NOAA_URL, {**common, "interval": "hilo"}, cache_dir / f"noaa-{station.station_id}-hilo.json.gz")
    return parse_noaa_series(series), parse_noaa_extremes(hilo)


def _noaa_mjd(text: str) -> float:
    return mjd_from_unix(dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=dt.UTC).timestamp())


def parse_noaa_series(payload: dict[str, Any]) -> list[tuple[float, float]]:
    if "predictions" not in payload:
        raise ValueError(f"NOAA answered no predictions: {payload.get('error')}")
    return [(_noaa_mjd(item["t"]), float(item["v"])) for item in payload["predictions"]]


def parse_noaa_extremes(payload: dict[str, Any]) -> list[Extreme]:
    if "predictions" not in payload:
        raise ValueError(f"NOAA answered no predictions: {payload.get('error')}")
    kinds = {"H": "high", "L": "low"}
    return [Extreme(_noaa_mjd(item["t"]), kinds[item["type"]], float(item["v"])) for item in payload["predictions"]]  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------------------------- references


def window_grid() -> np.ndarray:
    """The MJD of every 10 minutes of the window."""
    start = mjd_from_unix(WINDOW_START.timestamp())
    count = int((WINDOW_END - WINDOW_START).total_seconds() // (STEP_MINUTES * 60))
    return start + np.arange(count) * STEP_MINUTES / 1440.0


def smooth_minutes(values: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """The observations on a one-minute grid, smoothed by a centred mean of SMOOTHING_MINUTES (NaN where fewer than half exist)."""
    if not values:
        return np.zeros(0), np.zeros(0)
    minutes = np.round(np.array([mjd for mjd, _ in values]) * 1440.0).astype(np.int64)
    first = int(minutes.min())
    grid = np.full(int(minutes.max()) - first + 1, np.nan)
    grid[minutes - first] = [value for _, value in values]
    known = np.isfinite(grid)
    filled = np.where(known, grid, 0.0)
    kernel = np.ones(SMOOTHING_MINUTES)
    sums = np.convolve(filled, kernel, mode="same")
    counts = np.convolve(known.astype(float), kernel, mode="same")
    smoothed = np.where(counts >= (SMOOTHING_MINUTES + 1) // 2, sums / np.maximum(counts, 1.0), np.nan)
    return (first + np.arange(len(grid))) / 1440.0, smoothed


def extremes_of_gappy(mjd: np.ndarray, heights: np.ndarray) -> list[Extreme]:
    """The extremes of a curve with gaps: each run without a gap on its own (an extreme at the edge of a run is not one)."""
    extremes: list[Extreme] = []
    known = np.isfinite(heights)
    edges = np.flatnonzero(np.diff(np.concatenate([[0], known.astype(np.int8), [0]])))
    for start, stop in zip(edges[::2], edges[1::2], strict=True):
        extremes.extend(find_extremes(mjd[start:stop], heights[start:stop]))
    return extremes


def refmar_reference(values: list[tuple[float, float]]) -> Reference:
    minute_mjd, smoothed = smooth_minutes(values)
    grid = window_grid()
    heights = np.full(len(grid), np.nan)
    if len(minute_mjd):
        index = np.round((grid - minute_mjd[0]) * 1440.0).astype(np.int64)
        inside = (index >= 0) & (index < len(minute_mjd))
        heights[inside] = smoothed[index[inside]]
    return Reference(grid, heights, extremes_of_gappy(minute_mjd, smoothed))


def noaa_reference(series: list[tuple[float, float]], extremes: list[Extreme]) -> Reference:
    lookup = {round(mjd * 1440.0): value for mjd, value in series}
    grid = window_grid()
    heights = np.array([lookup.get(round(mjd * 1440.0), np.nan) for mjd in grid])
    return Reference(grid, heights, extremes)


# ------------------------------------------------------------------------------------------------------------------ scoring


def model_curve(model: TideModel, constants: np.ndarray) -> tuple[np.ndarray, list[Extreme]]:
    """The model on the 10-minute grid, and its extremes from a one-minute curve a day around the window."""
    grid = window_grid()
    start, end = mjd_from_unix((WINDOW_START - MARGIN).timestamp()), mjd_from_unix((WINDOW_END + MARGIN).timestamp())
    minutes = np.arange(start, end, 1.0 / 1440.0)
    return model.heights(constants, grid), find_extremes(minutes, model.heights(constants, minutes))


def significant_extremes(extremes: list[Extreme], mean_range: float, first: float, last: float) -> list[Extreme]:
    """The extremes inside [first, last) that differ by at least SIGNIFICANT_SHARE of the mean range from both neighbours."""
    kept = []
    for i in range(1, len(extremes) - 1):
        here = extremes[i]
        if not first <= here.mjd < last:
            continue
        if min(abs(here.height_m - extremes[i - 1].height_m), abs(here.height_m - extremes[i + 1].height_m)) >= SIGNIFICANT_SHARE * mean_range:
            kept.append(here)
    return kept


def mean_range(extremes: list[Extreme], first: float, last: float) -> Optional[float]:
    """The mean of the differences between consecutive extremes that are both inside [first, last)."""
    inside = [extreme for extreme in extremes if first <= extreme.mjd < last]
    ranges = [abs(a.height_m - b.height_m) for a, b in zip(inside, inside[1:], strict=False) if a.kind != b.kind]
    return float(np.mean(ranges)) if ranges else None


def match(reference: list[Extreme], model: list[Extreme]) -> list[tuple[Extreme, Extreme]]:
    """Each reference extreme with the model extreme of the same kind nearest in time, within MATCH_WINDOW_H."""
    pairs = []
    for wanted in reference:
        same = [candidate for candidate in model if candidate.kind == wanted.kind]
        if not same:
            continue
        nearest = min(same, key=lambda candidate: abs(candidate.mjd - wanted.mjd))
        if abs(nearest.mjd - wanted.mjd) * 24.0 <= MATCH_WINDOW_H:
            pairs.append((wanted, nearest))
    return pairs


def score_station(station: Station, reference: Reference, model_heights: np.ndarray, model_extremes: list[Extreme]) -> Score:
    score = Score(station.name, station.reference, station.station_id, station.in_criterion)
    known = np.isfinite(reference.heights)
    score.coverage = float(known.mean())
    if score.coverage < MIN_COVERAGE:
        score.reasons.append(f"only {score.coverage:.0%} of the 10-minute values")
        return score
    first, last = float(reference.mjd[0]), float(reference.mjd[-1]) + STEP_MINUTES / 1440.0
    reference_mean = float(reference.heights[known].mean())
    model_mean = float(model_heights[known].mean())
    span = mean_range(reference.extremes, first, last)
    if span is None:
        score.reasons.append("no high and low waters in the reference")
        return score
    score.mean_range_m = span
    significant = significant_extremes(reference.extremes, span, first, last)
    pairs = match(significant, model_extremes)
    score.significant, score.matched = len(significant), len(pairs)
    if pairs:
        time_errors = np.array([(found.mjd - wanted.mjd) * 1440.0 for wanted, found in pairs])
        height_errors = np.array([(found.height_m - model_mean) - (wanted.height_m - reference_mean) for wanted, found in pairs])
        score.time_error_median_min = float(np.median(np.abs(time_errors)))
        score.time_bias_median_min = float(np.median(time_errors))
        score.height_error_median_m = float(np.median(np.abs(height_errors)))
    difference = (model_heights[known] - model_mean) - (reference.heights[known] - reference_mean)
    score.rms_m = float(np.sqrt(np.mean(difference**2)))
    checks = [
        (score.time_error_median_min is not None and score.time_error_median_min <= MAX_TIME_ERROR_MIN, "time of high and low waters"),
        (score.height_error_median_m is not None and score.height_error_median_m <= MAX_HEIGHT_ERROR_SHARE * span, "height of high and low waters"),
        (score.rms_m <= MAX_RMS_SHARE * span, "series"),
        (score.unmatched_share is not None and score.unmatched_share <= MAX_UNMATCHED_SHARE, "unmatched extremes"),
    ]  # fmt: skip
    score.reasons = [name for ok, name in checks if not ok]
    score.status = "pass" if not score.reasons else "fail"
    return score


def verdict(scores: list[Score], reference: str) -> dict[str, Any]:
    """Whether a set meets the criterion: 8 of 10 pass; with stations not measured, 80 % of those measured and at least 7 measured."""
    counted = [score for score in scores if score.reference == reference and score.in_criterion]
    measured = [score for score in counted if score.status != "not measured"]
    passed = [score for score in measured if score.status == "pass"]
    if len(measured) == len(counted):
        holds = len(passed) >= PASSES_NEEDED
    else:
        holds = len(measured) >= MIN_MEASURED and len(passed) >= math.ceil(0.8 * len(measured))
    return {"stations": len(counted), "measured": len(measured), "passed": len(passed), "holds": holds}


def coefficients_at_brest(model: TideModel, observed: Optional[Reference]) -> dict[str, Any]:
    """Outside the criterion: the estimated coefficient against the same formula on the observed tide of Brest, and its span 2026-2044."""
    cell = model.nearest_cell(*BREST)
    result: dict[str, Any] = {"cell": [cell.lat, cell.lon], "unit_m": COEFFICIENT_UNIT_M}
    start = mjd_from_unix(dt.datetime(2026, 1, 1, tzinfo=dt.UTC).timestamp())
    end = mjd_from_unix(dt.datetime(2045, 1, 1, tzinfo=dt.UTC).timestamp())
    values: list[int] = []
    for chunk_start in np.arange(start, end, 200.0):
        times = np.arange(chunk_start - 1.0, min(chunk_start + 200.0, end) + 1.0, 5.0 / 1440.0)
        for high, value in estimated_coefficients(find_extremes(times, model.heights(cell.constants, times))):
            if chunk_start <= high.mjd < chunk_start + 200.0:
                values.append(value)
    result["span_2026_2044"] = {"min": min(values), "max": max(values), "count": len(values)}
    if observed is not None:
        first, last = float(observed.mjd[0]), float(observed.mjd[-1])
        times = np.arange(first - 1.0, last + 1.0, 1.0 / 1440.0)
        modelled = estimated_coefficients(find_extremes(times, model.heights(cell.constants, times)))
        seen = [(high, value) for high, value in estimated_coefficients(observed.extremes) if first <= high.mjd < last]
        differences = []
        for high, value in seen:
            nearest = min(modelled, key=lambda item: abs(item[0].mjd - high.mjd))
            if abs(nearest[0].mjd - high.mjd) * 24.0 <= MATCH_WINDOW_H:
                differences.append(nearest[1] - value)
        result["window"] = {
            "high_waters": len(seen),
            "matched": len(differences),
            "median_abs_difference": float(np.median(np.abs(differences))) if differences else None,
            "mean_difference": float(np.mean(differences)) if differences else None,
            "max_abs_difference": int(np.max(np.abs(differences))) if differences else None,
        }
    return result


def measure(cache_dir: Path) -> dict[str, Any]:
    model = TideModel()
    scores: list[Score] = []
    brest: Optional[Reference] = None
    for station in STATIONS:
        if station.reference == "refmar":
            reference = refmar_reference(fetch_refmar(station, cache_dir))
            if station.station_id == "3":
                brest = reference
        else:
            reference = noaa_reference(*fetch_noaa(station, cache_dir))
        try:
            cell = model.nearest_cell(station.lat, station.lon)
        except NoTideHere:
            score = Score(station.name, station.reference, station.station_id, station.in_criterion, status="fail")
            score.reasons.append("no model cell within the radius of the API")
            scores.append(score)
            continue
        heights, extremes = model_curve(model, cell.constants)
        score = score_station(station, reference, heights, extremes)
        score.cell_lat, score.cell_lon, score.cell_distance_km = cell.lat, cell.lon, round(cell.distance_km, 1)
        scores.append(score)
        print(f"{station.name:32} {score.status:13} {', '.join(score.reasons)}", file=sys.stderr)
    return {
        "window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        "model": model.model,
        "scores": [asdict(score) | {"unmatched_share": score.unmatched_share} for score in scores],
        "verdict": {"france": verdict(scores, "refmar"), "united_states": verdict(scores, "noaa")},
        "coefficient": coefficients_at_brest(model, brest),
    }


# ------------------------------------------------------------------------------------------------------------------- report


def _cm(value: Optional[float]) -> str:
    return "–" if value is None else f"{value * 100:.0f}"


def _share(value: Optional[float], span: Optional[float]) -> str:
    return "–" if value is None or not span else f"{value / span:.1%}"


def markdown_table(results: dict[str, Any], reference: str) -> str:
    lines = [
        "| Station | Cell (km away) | Mean range (cm) | Time error, median (min) | Model early (-) or late (+), median (min) "
        "| Height error, median (cm, % of range) | RMS of the series (cm, % of range) | Unmatched | Result |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for score in results["scores"]:
        if score["reference"] != reference:
            continue
        span = score["mean_range_m"]
        name = score["station"] + ("" if score["in_criterion"] else " *(outside the criterion)*")
        cell = "–" if score["cell_lat"] is None else f"{score['cell_lat']:.3f}, {score['cell_lon']:.3f} ({score['cell_distance_km']:.1f})"
        unmatched = "–" if score["unmatched_share"] is None else f"{score['significant'] - score['matched']} of {score['significant']}"
        time_error = "–" if score["time_error_median_min"] is None else f"{score['time_error_median_min']:.0f}"
        bias = "–" if score["time_bias_median_min"] is None else f"{score['time_bias_median_min']:+.0f}"
        result = score["status"] + (f" ({', '.join(score['reasons'])})" if score["reasons"] else "")
        lines.append(
            f"| {name} | {cell} | {_cm(span)} | {time_error} | {bias} | {_cm(score['height_error_median_m'])} "
            f"({_share(score['height_error_median_m'], span)}) | {_cm(score['rms_m'])} ({_share(score['rms_m'], span)}) | {unmatched} | {result} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--cache-dir", type=Path, required=True, help="where the downloads are kept")
    parser.add_argument("--write", action="store_true", help="write the results file of docs/measurements")
    args = parser.parse_args()
    results = measure(args.cache_dir)
    print(json.dumps({"verdict": results["verdict"], "coefficient": results["coefficient"]}, indent=2))
    print(markdown_table(results, "refmar"))
    print(markdown_table(results, "noaa"))
    if args.write:
        target = REPO_ROOT / "docs" / "measurements" / "tides-2026-10-results.json.gz"
        target.write_bytes(gzip.compress(json.dumps(results, indent=1).encode("utf-8"), mtime=0))
        print(f"wrote {target.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
