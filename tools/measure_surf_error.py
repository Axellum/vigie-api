"""Measure the forecast swell of the surf spots against the CANDHIS buoys, and the one correction ADR 0022 allows itself to try.

    python tools/measure_surf_error.py --cache-dir DIR                # print the report (asks CANDHIS and Open-Meteo for what the cache lacks)
    python tools/measure_surf_error.py --cache-dir DIR --offline      # the cache only, nothing is asked
    python tools/measure_surf_error.py --cache-dir DIR --write        # and write docs/measurements/surf-error-2026-11-results.json.gz
    python tools/measure_surf_error.py --cache-dir DIR --only 06402   # one buoy only (a trial run; the cache is shared)

CANDHIS needs the key in ``VIGIE_CANDHIS_API_KEY`` (only for what the cache lacks: 1 request per buoy, 7 in all). Open-Meteo asks nothing but one request per
spot (about 29 calls of its free quota under the rule of ``app/collectors/weights.py``). A second run asks nothing. Nothing here runs inside the API.

The protocol and the criterion are those of ``docs/decisions/0022-forecast-swell-measured-against-buoys.md``, written before any figure was computed:

- pairs: every surf spot whose nearest located real-time buoy is within 30 km (``GET /v1/surf/buoys``, ``comparable``);
- forecast: ``wave_height`` of MFWAM at the spot (``models=meteofrance_wave``), the value predicted 24 h x N before for N = 1, 3, 5
  (``wave_height_previous_dayN``), and, for information, the unsuffixed past values ("lead 0");
- measure: ``HM0`` of the CANDHIS delayed-time records (``getCampTD.php``), the records dated on the hour, with ``QUALITE`` 1;
- window 2025-12-01 to 2026-10-31 (UTC), in blocks of 14 days: even blocks fold A, odd blocks fold B; a factor is fitted on one fold and scored on the other;
- the factor of a pair is kept only if, in both directions and at each of the three leads, the held-out RMSE falls by 5 % or more and does not rise on the hours
  the buoy read 2 m or more, with 1,000 held-out hours (100 of them at 2 m or more) at each lead.

Period and direction are described only (a model's mean period and mean direction are not a buoy's), over the hours the buoy read 1 m or more.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.collectors.grid import coord_str  # noqa: E402
from app.collectors.surf import MARINE_MODEL  # noqa: E402
from app.services import buoys as buoy_service  # noqa: E402
from app.services import surf  # noqa: E402

USER_AGENT = "vigie-api measure_surf_error (personal project)"
CANDHIS_URL = "https://candhis.cerema.fr/API/v1/getCampTD.php"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
KEY_ENV = "VIGIE_CANDHIS_API_KEY"

WINDOW_START = dt.date(2025, 12, 1)
WINDOW_END = dt.date(2026, 10, 31)  # inclusive
START_UNIX = int(dt.datetime(WINDOW_START.year, WINDOW_START.month, WINDOW_START.day, tzinfo=dt.timezone.utc).timestamp())
N_HOURS = ((WINDOW_END - WINDOW_START).days + 1) * 24
LEADS = (1, 3, 5)  # days; forecasts made 24 h x N before
ALL_LEADS = (0, *LEADS)  # 0 = the unsuffixed past values, for information only
BLOCK_DAYS = 14
MIN_GAIN = 0.05  # the smallest fall of the RMSE worth a constant per spot (ADR 0022, a judgement of ours)
MIN_HOURS = 1000
MIN_HIGH_HOURS = 100
HIGH_M = 2.0
MIN_SEA_M = 1.0  # period and direction are described only when the buoy read at least this
CLASSES = ((0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, math.inf))
WITHIN_M = 0.5
LAGS = range(-3, 4)
# The change to summer time in France (2026-03-29): a clock read in local time would shift the buoys by one more hour after it (added after the first run, to read it).
SUMMER_TIME_HOUR = int((dt.datetime(2026, 3, 29, 1, tzinfo=dt.timezone.utc).timestamp() - START_UNIX) // 3600)

MONTHS = np.array([dt.datetime.fromtimestamp(START_UNIX + 3600 * hour, dt.timezone.utc).strftime("%Y-%m") for hour in range(N_HOURS)])  # the month of each hour of the window

QUANTITIES = {"height": "wave_height", "period": "wave_period", "direction": "wave_direction"}


# ---------------------------------------------------------------------------------------------------------------- reading


def _number(value: Any) -> float:
    """A number of a CANDHIS cell; blank, unreadable and the 999.9999-style missing values are NaN."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return math.nan if not math.isfinite(number) or abs(number) >= 900.0 else number


def _unix_of(text: Any) -> Optional[float]:
    """The UTC time of a CANDHIS ``DateHeure`` cell (``2026-01-09 00:30:00``, seconds optional), or ``None``."""
    text = str(text).strip()
    for form in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, form).replace(tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _hour_index(unix: float) -> Optional[int]:
    """The place of a time on the hourly grid of the window, or ``None`` when it is not exactly on the hour inside it."""
    offset = unix - START_UNIX
    if offset < 0 or offset % 3600:
        return None
    index = int(offset // 3600)
    return index if index < N_HOURS else None


@dataclass
class Measure:
    """What a buoy read, on the hourly grid of the window (NaN where it read nothing on the hour)."""

    height_kind: str  # "HM0", or "H13D" for a buoy that has no HM0
    hs: np.ndarray
    tp: np.ndarray
    t02: np.ndarray
    theta_p: np.ndarray
    theta_m: np.ndarray
    quality: np.ndarray
    records: int  # the records of the answer, whatever their time
    codes: dict[str, int]  # how many records carry each QUALITE code (added after the first run, to read it)
    first: Optional[str]  # the date of the first and of the last record of the answer
    last: Optional[str]

    def hs_good(self) -> np.ndarray:
        """The heights of the records whose ``QUALITE`` is 1 (the others are left out, ADR 0022)."""
        return np.where(self.quality == 1.0, self.hs, math.nan)


def parse_td(payload: Any) -> Measure:
    """The delayed-time records of a CANDHIS answer, by the names of the header (``QUALITE`` is there twice: the first one is the quality)."""
    if not isinstance(payload, dict) or payload.get("success") not in (True, "True", "true"):
        message = payload.get("message") if isinstance(payload, dict) else None
        raise ValueError(f"CANDHIS answer is not a success: {message}")
    header, rows = payload.get("entete"), payload.get("results")
    if not isinstance(header, list) or not isinstance(rows, list):
        raise ValueError("CANDHIS answer has no header or no rows")
    index: dict[str, int] = {}
    for position, name in enumerate(header):
        index.setdefault(str(name).strip().upper(), position)
    if "DATEHEURE" not in index:
        raise ValueError("CANDHIS answer has no DateHeure column")
    kind = "HM0" if "HM0" in index else "H13D" if "H13D" in index else None
    if kind is None:
        raise ValueError("CANDHIS answer has neither HM0 nor H13D")

    def grid() -> np.ndarray:
        return np.full(N_HOURS, math.nan)

    out = {name: grid() for name in ("hs", "tp", "t02", "theta_p", "theta_m", "quality")}
    quality_at = index.get("QUALITE")
    codes: dict[str, int] = {}
    for row in rows:
        if isinstance(row, list) and quality_at is not None and quality_at < len(row):
            code = str(row[quality_at]).strip() or "(blank)"
            codes[code] = codes.get(code, 0) + 1
    dates = [str(row[index["DATEHEURE"]]).strip() for row in rows if isinstance(row, list) and index["DATEHEURE"] < len(row)]
    columns = {"hs": kind, "tp": "TP", "t02": "T02", "theta_p": "THETAP", "theta_m": "THETAM", "quality": "QUALITE"}
    for row in rows:
        unix = _unix_of(row[index["DATEHEURE"]]) if isinstance(row, list) and index["DATEHEURE"] < len(row) else None
        place = None if unix is None else _hour_index(unix)
        if place is None:
            continue
        for field, column in columns.items():
            position = index.get(column)
            if position is not None and position < len(row):
                out[field][place] = _number(row[position])
    return Measure(
        kind, out["hs"], out["tp"], out["t02"], out["theta_p"], out["theta_m"], out["quality"], len(rows), codes, min(dates, default=None), max(dates, default=None)
    )


@dataclass
class Forecast:
    """What the model gave at one point, on the hourly grid of the window, by quantity and lead."""

    cell_lat: Optional[float]
    cell_lon: Optional[float]
    series: dict[tuple[str, int], np.ndarray]


def variable_name(quantity: str, lead: int) -> str:
    return QUANTITIES[quantity] + ("" if lead == 0 else f"_previous_day{lead}")


def marine_variables() -> list[str]:
    return [variable_name(q, lead) for q in QUANTITIES for lead in ALL_LEADS]


def parse_marine(payload: Any) -> Forecast:
    """The hourly series of an Open-Meteo Marine answer (``timeformat=unixtime``) laid on the grid of the window; a value it leaves out is NaN."""
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise ValueError("Open-Meteo Marine answer has no hourly times")
    places = [_hour_index(t) if isinstance(t, (int, float)) else None for t in hourly["time"]]
    series: dict[tuple[str, int], np.ndarray] = {}
    for quantity in QUANTITIES:
        for lead in ALL_LEADS:
            values = hourly.get(variable_name(quantity, lead)) or []
            column = np.full(N_HOURS, math.nan)
            for place, value in zip(places, values, strict=False):  # a column shorter than the times leaves the rest NaN
                if place is not None and value is not None:
                    column[place] = _number(value)
            series[(quantity, lead)] = column
    return Forecast(
        payload.get("latitude") if isinstance(payload.get("latitude"), (int, float)) else None,
        payload.get("longitude") if isinstance(payload.get("longitude"), (int, float)) else None,
        series,
    )


# ---------------------------------------------------------------------------------------------------------------- scoring


def stats(obs: np.ndarray, forecast: np.ndarray) -> dict[str, Any]:
    """The error of a forecast against a measure over the hours that have both. Bias = forecast minus measure; ratio = mean forecast / mean measure."""
    keep = ~np.isnan(obs) & ~np.isnan(forecast)
    n = int(keep.sum())
    if n == 0:
        return {"n": 0}
    o, f = obs[keep], forecast[keep]
    d = f - o
    r = float(np.corrcoef(o, f)[0, 1]) if n >= 3 and o.std() > 0 and f.std() > 0 else None
    return {
        "n": n,
        "bias": float(d.mean()),
        "mae": float(np.abs(d).mean()),
        "rmse": float(math.sqrt((d * d).mean())),
        "r": r,
        "ratio": float(f.mean() / o.mean()) if o.mean() > 0 else None,
        "within": float((np.abs(d) <= WITHIN_M).mean()),
    }


def class_stats(obs: np.ndarray, forecast: np.ndarray) -> list[dict[str, Any]]:
    """``stats`` by class of the height the buoy read."""
    out = []
    for low, high in CLASSES:
        with np.errstate(invalid="ignore"):
            inside = (obs >= low) & (obs < high)
        out.append({"from_m": low, "to_m": None if math.isinf(high) else high, **stats(np.where(inside, obs, math.nan), forecast)})
    return out


def persistence(obs: np.ndarray, lead: int) -> np.ndarray:
    """What the buoy read ``lead`` days before each hour (what a forecast that knew nothing but the last measure would say)."""
    shift = 24 * lead
    out = np.full(len(obs), math.nan)
    out[shift:] = obs[:-shift]
    return out


def circular_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a - b in degrees, in -180 to 180."""
    return (a - b + 180.0) % 360.0 - 180.0


def fold_of_hours() -> np.ndarray:
    """0 for the hours of the even blocks of 14 days (fold A), 1 for the odd ones (fold B)."""
    return ((np.arange(N_HOURS) // 24 // BLOCK_DAYS) % 2).astype(int)


def fit_factor(obs: np.ndarray, forecasts: dict[int, np.ndarray], train: np.ndarray) -> Optional[float]:
    """The factor of ADR 0022: the sum of the measured heights over the sum of the forecast ones, on the training hours, the three leads together."""
    total_obs = total_forecast = 0.0
    for lead in LEADS:
        keep = train & ~np.isnan(obs) & ~np.isnan(forecasts[lead])
        total_obs += float(obs[keep].sum())
        total_forecast += float(forecasts[lead][keep].sum())
    return total_obs / total_forecast if total_forecast > 0 else None


def rmse(obs: np.ndarray, forecast: np.ndarray) -> Optional[float]:
    s = stats(obs, forecast)
    return s.get("rmse") if s["n"] else None


def score_direction(obs: np.ndarray, forecasts: dict[int, np.ndarray], train_fold: int, folds: np.ndarray) -> dict[str, Any]:
    """Fit on one fold, score on the other: the factor and, per lead, the held-out RMSE before and after, over all hours and over the hours of 2 m or more."""
    train, test = folds == train_fold, folds != train_fold
    factor = fit_factor(obs, forecasts, train)
    leads: dict[int, dict[str, Any]] = {}
    for lead in LEADS:
        forecast = forecasts[lead]
        corrected = forecast * factor if factor is not None else forecast
        held = np.where(test, obs, math.nan)
        high = np.where(test & (obs >= HIGH_M), obs, math.nan)
        before, after = rmse(held, forecast), rmse(held, corrected)
        high_before, high_after = rmse(high, forecast), rmse(high, corrected)
        leads[lead] = {
            "n": stats(held, forecast)["n"],
            "n_high": stats(high, forecast)["n"],
            "rmse_before": before,
            "rmse_after": after,
            "gain": (before - after) / before if before and after is not None else None,
            "rmse_high_before": high_before,
            "rmse_high_after": high_after,
        }
    return {"factor": factor, "leads": leads}


def judge(directions: dict[str, dict[str, Any]]) -> tuple[bool, list[str]]:
    """The criterion of ADR 0022, applied to both directions of the cross-fit, at each of the leads."""
    reasons: list[str] = []
    for name, direction in directions.items():
        if direction["factor"] is None:
            reasons.append(f"{name}: no factor (the training hours have no forecast height)")
            continue
        for lead, score in direction["leads"].items():
            where = f"{name}, {lead} d"
            if score["n"] < MIN_HOURS or score["n_high"] < MIN_HIGH_HOURS:
                reasons.append(f"{where}: not enough data ({score['n']} hours, {score['n_high']} at {HIGH_M:g} m or more)")
                continue
            if score["gain"] is None or score["gain"] < MIN_GAIN:
                gain = "none" if score["gain"] is None else f"{score['gain'] * 100:.1f} %"
                reasons.append(f"{where}: gain {gain}, under {MIN_GAIN * 100:g} %")
            if score["rmse_high_after"] is None or score["rmse_high_before"] is None or score["rmse_high_after"] > score["rmse_high_before"]:
                reasons.append(f"{where}: the error on the hours of {HIGH_M:g} m or more rises")
    return not reasons, reasons


def best_lag(obs: np.ndarray, forecast: np.ndarray) -> Optional[int]:
    """The shift, in hours, of the forecast against the measure that makes them agree best (0 when the clocks are the same)."""
    best: Optional[tuple[float, int]] = None
    for lag in LAGS:
        shifted = np.full(len(forecast), math.nan)
        if lag >= 0:
            shifted[: len(forecast) - lag] = forecast[lag:]
        else:
            shifted[-lag:] = forecast[: len(forecast) + lag]
        s = stats(obs, shifted)
        if s["n"] and s["r"] is not None and (best is None or s["r"] > best[0]):
            best = (s["r"], lag)
    return None if best is None else best[1]


def describe_period_direction(measure: Measure, forecast: Forecast, lead: int) -> dict[str, Any]:
    """Period and direction against the buoy's, over the hours the buoy read ``MIN_SEA_M`` or more. Described only: no correction is decided on them."""
    sea = np.where(measure.hs_good() >= MIN_SEA_M, 1.0, math.nan)
    period, direction = forecast.series[("period", lead)], forecast.series[("direction", lead)]
    out: dict[str, Any] = {"hours": int(np.nansum(sea))}
    for name, reference in (("t02", measure.t02), ("tp", measure.tp)):
        out[f"period_vs_{name}"] = stats(reference * sea, period)
    for name, reference in (("theta_m", measure.theta_m), ("theta_p", measure.theta_p)):
        diff = circular_difference(direction, reference) * sea
        keep = ~np.isnan(diff)
        out[f"direction_vs_{name}"] = {"n": int(keep.sum()), "bias": float(diff[keep].mean()), "mae": float(np.abs(diff[keep]).mean())} if keep.any() else {"n": 0}
    return out


@dataclass
class Pair:
    spot_id: str
    spot_name: str
    lat: float
    lon: float
    buoy_id: str
    buoy_name: str
    distance_km: float


def pairs_of_the_service() -> list[Pair]:
    """The (spot, buoy) pairs of ADR 0022: the spots whose nearest located real-time buoy is within the comparable distance."""
    out = []
    for row in buoy_service.spot_buoys().spots:
        if row.comparable and row.real_time_buoy is not None and row.real_time_distance_km is not None:
            spot = surf.find_spot(row.spot_id)
            out.append(Pair(row.spot_id, row.spot_name, spot.lat, spot.lon, row.real_time_buoy.id, row.real_time_buoy.name, row.real_time_distance_km))
    return out


def score_pair(measure: Measure, forecast: Forecast, folds: np.ndarray) -> dict[str, Any]:
    """Everything the report says about one pair."""
    obs = measure.hs_good()
    heights = {lead: forecast.series[("height", lead)] for lead in ALL_LEADS}
    directions = {"A_to_B": score_direction(obs, heights, 0, folds), "B_to_A": score_direction(obs, heights, 1, folds)}
    kept, reasons = judge(directions)
    return {
        "height_kind": measure.height_kind,
        "records": measure.records,
        "quality_ones": int((measure.quality == 1.0).sum()),
        "on_the_hour": int((~np.isnan(measure.hs)).sum()),
        "cell": [forecast.cell_lat, forecast.cell_lon],
        "by_lead": {str(lead): stats(obs, heights[lead]) for lead in ALL_LEADS},
        "without_quality_filter": {str(lead): stats(measure.hs, heights[lead]) for lead in ALL_LEADS},
        "persistence": {str(lead): stats(obs, persistence(obs, lead)) for lead in LEADS},
        "by_class": {str(lead): class_stats(obs, heights[lead]) for lead in ALL_LEADS},
        "lag_hours": best_lag(obs, heights[0]),
        "lag_hours_winter": best_lag(np.where(np.arange(N_HOURS) < SUMMER_TIME_HOUR, obs, math.nan), heights[0]),
        "lag_hours_summer": best_lag(np.where(np.arange(N_HOURS) >= SUMMER_TIME_HOUR, obs, math.nan), heights[0]),
        "first_record": measure.first,
        "last_record": measure.last,
        "quality_codes": measure.codes,
        "period_direction": {str(lead): describe_period_direction(measure, forecast, lead) for lead in ALL_LEADS},
        "cross_fit": directions,
        "factor_whole_window": fit_factor(obs, heights, np.ones(N_HOURS, dtype=bool)),
        "kept": kept,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------------------------------------------- downloads


def _get_json(url: str, params: dict[str, str], headers: dict[str, str], cache: Path, offline: bool, pause_s: float) -> Any:
    """The JSON of a request, from the cache folder or asked once and kept (then ``pause_s`` seconds of rest for the provider).
    A connection that is reset is tried again a few times."""
    if cache.exists():
        return json.loads(gzip.decompress(cache.read_bytes()))
    if offline:
        raise SystemExit(f"--offline: {cache.name} is not in the cache")
    import httpx

    last = "no answer"
    for attempt in range(1, 5):
        try:
            response = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **headers}, timeout=180)
        except httpx.TransportError as error:
            last = type(error).__name__
            print(f"  {cache.name}: attempt {attempt} failed ({last}), waiting", file=sys.stderr)
            time.sleep(10 * attempt)
            continue
        if response.status_code == 429:
            last = "429"
            print(f"  {cache.name}: 429, waiting a minute", file=sys.stderr)
            time.sleep(60)
            continue
        response.raise_for_status()
        payload = response.json()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))
        time.sleep(pause_s)
        return payload
    raise SystemExit(f"{cache.name}: no answer after 4 attempts ({last})")


def fetch_buoy(buoy_id: str, cache_dir: Path, offline: bool) -> Measure:
    """The delayed-time records of a buoy over the window, in one request (12 months at most; the end date is exclusive)."""
    end = WINDOW_END + dt.timedelta(days=1)
    key = os.environ.get(KEY_ENV, "")
    cache = cache_dir / f"candhis-td-{buoy_id}-{WINDOW_START}-{end}.json.gz"
    if not cache.exists() and not offline and not key:
        raise SystemExit(f"{KEY_ENV} is not set, and {cache.name} is not in the cache")
    params = {"camp": buoy_id, "dateDeb": WINDOW_START.isoformat(), "dateFin": end.isoformat()}
    return parse_td(_get_json(CANDHIS_URL, params, {"Authorization": key}, cache, offline, pause_s=5.0))


def fetch_forecast(pair: Pair, cache_dir: Path, offline: bool) -> Forecast:
    """The hourly forecasts of the spot over the window, in one request."""
    lat, lon = coord_str(pair.lat), coord_str(pair.lon)
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": WINDOW_START.isoformat(),
        "end_date": WINDOW_END.isoformat(),
        "hourly": ",".join(marine_variables()),
        "models": MARINE_MODEL,
        "timeformat": "unixtime",
        "timezone": "GMT",
    }
    cache = cache_dir / f"marine-{lat}-{lon}-{WINDOW_START}-{WINDOW_END}.json.gz"
    return parse_marine(_get_json(MARINE_URL, params, {}, cache, offline, pause_s=2.0))


# ---------------------------------------------------------------------------------------------------------------- the run


def measure(cache_dir: Path, offline: bool = False, only: Optional[str] = None) -> dict[str, Any]:
    folds = fold_of_hours()
    pairs = [p for p in pairs_of_the_service() if only is None or p.buoy_id == only]
    if not pairs:
        raise SystemExit("no pair to measure")
    measures: dict[str, Measure] = {}
    unavailable: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    held: dict[str, list[tuple[Measure, Forecast]]] = {}
    for pair in pairs:
        if pair.buoy_id in unavailable:
            continue
        if pair.buoy_id not in measures:
            print(f"buoy {pair.buoy_id} {pair.buoy_name}", file=sys.stderr)
            try:
                measures[pair.buoy_id] = fetch_buoy(pair.buoy_id, cache_dir, offline)
            except ValueError as error:
                unavailable[pair.buoy_id] = str(error)
                continue
        print(f"  spot {pair.spot_id}", file=sys.stderr)
        forecast = fetch_forecast(pair, cache_dir, offline)
        held.setdefault(pair.buoy_id, []).append((measures[pair.buoy_id], forecast))
        results.append({"pair": pair.__dict__, **score_pair(measures[pair.buoy_id], forecast, folds)})
    by_buoy: dict[str, Any] = {}
    for buoy_id, items in held.items():
        obs = np.concatenate([m.hs_good() for m, _ in items])
        by_buoy[buoy_id] = {
            "pairs": len(items),
            "by_lead": {str(lead): stats(obs, np.concatenate([f.series[("height", lead)] for _, f in items])) for lead in ALL_LEADS},
            "persistence": {str(lead): stats(obs, np.concatenate([persistence(m.hs_good(), lead) for m, _ in items])) for lead in LEADS},
        }
    everything_obs = np.concatenate([m.hs_good() for items in held.values() for m, _ in items])
    months = np.concatenate([MONTHS for items in held.values() for _ in items])
    overall = {
        "pairs": len(results),
        "by_lead": {str(lead): stats(everything_obs, np.concatenate([f.series[("height", lead)] for items in held.values() for _, f in items])) for lead in ALL_LEADS},
        "persistence": {str(lead): stats(everything_obs, np.concatenate([persistence(m.hs_good(), lead) for items in held.values() for m, _ in items])) for lead in LEADS},
        "by_class": {str(lead): class_stats(everything_obs, np.concatenate([f.series[("height", lead)] for items in held.values() for _, f in items])) for lead in ALL_LEADS},
        "by_month_lead_1": _by_month(everything_obs, np.concatenate([f.series[("height", 1)] for items in held.values() for _, f in items]), months),
    }
    return {
        "window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        "pairs": results,
        "by_buoy": by_buoy,
        "overall": overall,
        "unavailable_buoys": unavailable,
        "kept": [r["pair"]["spot_id"] for r in results if r["kept"]],
    }


def _by_month(obs: np.ndarray, forecast: np.ndarray, months: np.ndarray) -> dict[str, Any]:
    return {month: stats(np.where(months == month, obs, math.nan), forecast) for month in sorted(set(months.tolist()))}


# ---------------------------------------------------------------------------------------------------------------- report


def _f(value: Any, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _pct(value: Any) -> str:
    return "-" if value is None else f"{value * 100:.1f} %"


def report(results: dict[str, Any]) -> str:
    """The tables of the measure, in Markdown."""
    lines: list[str] = []
    out = lines.append
    out(f"Window {results['window'][0]} to {results['window'][1]}; {results['overall']['pairs']} pairs (spot, buoy); criterion of ADR 0022.\n")
    out("## Pairs\n")
    out("| Spot | Buoy | km | Height | Cell | Hours scored (1 d) | Hours on the hour | QUALITE 1 | Shift (h): all / winter / summer | Records of the answer | QUALITE codes |")
    out("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in results["pairs"]:
        p = r["pair"]
        cell = f"{_f(r['cell'][0], 3)}, {_f(r['cell'][1], 3)}"
        codes = ", ".join(f"{code}: {count}" for code, count in sorted(r["quality_codes"].items()))
        out(f"| {p['spot_name']} | {p['buoy_id']} {p['buoy_name']} | {p['distance_km']:.1f} | {r['height_kind']} | {cell} | {r['by_lead']['1']['n']} | {r['on_the_hour']} | {r['quality_ones']} | {r['lag_hours']} / {r['lag_hours_winter']} / {r['lag_hours_summer']} | {r['records']} ({r['first_record']} to {r['last_record']}) | {codes} |")
    for buoy_id, why in results["unavailable_buoys"].items():
        out(f"\nBuoy {buoy_id} has no usable record: {why}")

    def table(title: str, block: dict[str, Any], persistence_block: Optional[dict[str, Any]] = None) -> None:
        out(f"\n{title}\n")
        out("| Lead | Hours | Bias (m) | MAE (m) | RMSE (m) | r | Forecast / buoy | Within 0.5 m | Persistence RMSE (m) |")
        out("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for lead in ALL_LEADS:
            s = block["by_lead"][str(lead)]
            ref = (persistence_block or {}).get(str(lead), {})
            label = "0 (past values)" if lead == 0 else f"{lead} d"
            out(f"| {label} | {s['n']} | {_f(s.get('bias'))} | {_f(s.get('mae'))} | {_f(s.get('rmse'))} | {_f(s.get('r'))} | {_f(s.get('ratio'))} | {_pct(s.get('within'))} | {_f(ref.get('rmse'))} |")

    out("\n## All pairs together")
    table("Height of the sea (the forecast of the spot against the buoy)", results["overall"], results["overall"]["persistence"])
    out("\n### By class of the height the buoy read\n")
    out("| Buoy read | Lead | Hours | Bias (m) | RMSE (m) | Forecast / buoy |")
    out("| --- | --- | --- | --- | --- | --- |")
    for lead in LEADS:
        for c in results["overall"]["by_class"][str(lead)]:
            label = f"{c['from_m']:g} to {c['to_m']:g} m" if c["to_m"] is not None else f"{c['from_m']:g} m and over"
            out(f"| {label} | {lead} d | {c['n']} | {_f(c.get('bias'))} | {_f(c.get('rmse'))} | {_f(c.get('ratio'))} |")
    out("\n### By month, lead 1 day\n")
    out("| Month | Hours | Bias (m) | RMSE (m) | Forecast / buoy |")
    out("| --- | --- | --- | --- | --- |")
    for month, s in results["overall"]["by_month_lead_1"].items():
        out(f"| {month} | {s['n']} | {_f(s.get('bias'))} | {_f(s.get('rmse'))} | {_f(s.get('ratio'))} |")

    out("\n## By buoy (its spots together)")
    for buoy_id, block in results["by_buoy"].items():
        name = next(r["pair"]["buoy_name"] for r in results["pairs"] if r["pair"]["buoy_id"] == buoy_id)
        table(f"{buoy_id} {name} ({block['pairs']} spots)", block, block["persistence"])

    out("\n## Quality filter\n")
    out("| Spot | RMSE 1 d, QUALITE 1 only | RMSE 1 d, every record | Hours, QUALITE 1 only | Hours, every record |")
    out("| --- | --- | --- | --- | --- |")
    for r in results["pairs"]:
        a, b = r["by_lead"]["1"], r["without_quality_filter"]["1"]
        out(f"| {r['pair']['spot_name']} | {_f(a.get('rmse'))} | {_f(b.get('rmse'))} | {a['n']} | {b['n']} |")

    out("\n## Period and direction (described only; hours the buoy read 1 m or more)\n")
    out("| Spot | Lead | Hours | Period bias vs T02 (s) | vs TP (s) | Direction bias vs mean (deg) | MAE (deg) | vs peak (deg) | MAE (deg) |")
    out("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in results["pairs"]:
        for lead in (0, 3):
            d = r["period_direction"][str(lead)]
            out(
                f"| {r['pair']['spot_name']} | {lead} d | {d['hours']} | {_f(d['period_vs_t02'].get('bias'))} | {_f(d['period_vs_tp'].get('bias'))} | "
                f"{_f(d['direction_vs_theta_m'].get('bias'), 1)} | {_f(d['direction_vs_theta_m'].get('mae'), 1)} | {_f(d['direction_vs_theta_p'].get('bias'), 1)} | {_f(d['direction_vs_theta_p'].get('mae'), 1)} |"
            )

    out("\n## The factor and its held-out test\n")
    out("Held-out RMSE of the height (m) before -> after the factor, and the gain; the same at the hours the buoy read 2 m or more.\n")
    out("| Spot | Direction | Factor | Lead | Hours | RMSE before -> after | Gain | Hours 2 m+ | RMSE 2 m+ before -> after |")
    out("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in results["pairs"]:
        for name, direction in r["cross_fit"].items():
            for lead, s in direction["leads"].items():
                out(
                    f"| {r['pair']['spot_name']} | {name} | {_f(direction['factor'], 3)} | {lead} d | {s['n']} | {_f(s['rmse_before'])} -> {_f(s['rmse_after'])} | {_pct(s['gain'])} | "
                    f"{s['n_high']} | {_f(s['rmse_high_before'])} -> {_f(s['rmse_high_after'])} |"
                )
    out("\n## Verdict per pair (the criterion of ADR 0022)\n")
    out("| Spot | Kept | Factor on the whole window | Why not |")
    out("| --- | --- | --- | --- |")
    for r in results["pairs"]:
        out(f"| {r['pair']['spot_name']} | {'yes' if r['kept'] else 'no'} | {_f(r['factor_whole_window'], 3)} | {'; '.join(r['reasons']) or '-'} |")
    out(f"\nKept: {len(results['kept'])} of {results['overall']['pairs']} pairs.")
    return "\n".join(lines)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--cache-dir", type=Path, required=True, help="Folder of the downloads (kept between runs; not part of the repository)")
    parser.add_argument("--offline", action="store_true", help="Ask nothing: the cache only")
    parser.add_argument("--only", help="One buoy only (its CANDHIS code, for example 06402)")
    parser.add_argument("--write", action="store_true", help="Write docs/measurements/surf-error-2026-11-results.json.gz")
    args = parser.parse_args()
    results = _clean(measure(args.cache_dir, args.offline, args.only))
    print(report(results))
    if args.write:
        target = REPO_ROOT / "docs" / "measurements" / "surf-error-2026-11-results.json.gz"
        target.write_bytes(gzip.compress(json.dumps(results, separators=(",", ":")).encode("utf-8")))
        print(f"\nwritten: {target.relative_to(REPO_ROOT)} ({target.stat().st_size} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
