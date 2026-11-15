"""Measure Météo-France / SHOM HYCOM2D storm surge against live SHOM REFMAR tide gauges (ADR 0025).

    python tools/measure_mf_surge.py --cache-dir DIR               # download what is missing, print report
    python tools/measure_mf_surge.py --cache-dir DIR --write       # and write docs/measurements/mf-surge-2026-11.md + JSON.gz

Compares four tide / storm-surge predictors against REFMAR's 61-minute smoothed sea level across
12 French gauges (11 Atlantic/Channel from ADR 0017 plus Marseille in the Mediterranean), strictly
on held-out origins after fitting each station's chart datum offset on the first half of the window:

1. ``eot20``: EOT20 alone (with ADR 0019 Brest calibration), ``GET /v1/tides`` baseline.
2. ``adr0017``: EOT20 + trailing REFMAR gauge persistence (745 min mean, 20 min lag, 12 h decay).
3. ``arpege``: EOT20 + HYCOM2D-WARP (Atlantic/Channel) or HYCOM2D-MARP (Med) storm surge.
4. ``arome``: EOT20 + HYCOM2D-WARO (Atlantic/Channel) or HYCOM2D-MARO (Med) storm surge.
5. ``hybrid``: EOT20 + HYCOM2D + decayed initial error ``(obs_residual(t0 - 20m) - hycom(t0 - 20m))`` using ADR 0017's 12 h decay.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import measure_tide_correction as mtc  # noqa: E402
import measure_tides as mt  # noqa: E402

from app.prealert.hycom2d import decode_surge, grib_url  # noqa: E402
from app.services import tide_correction as tc  # noqa: E402
from app.services.tides import MINUTE, TideModel, calibrated_heights, calibration_for, mjd_from_unix  # noqa: E402

USER_AGENT = "vigie-api measure_mf_surge (personal project)"
LEADS_H = (1, 3, 6, 12, 24, 36)
CYCLE_HOURS = (0, 6, 12, 18)

# 14 full days present on meteofrance-pnt S3 bucket as of 2026-11-15: 7 days train, 7 days held-out test.
WINDOW_START = dt.datetime(2026, 11, 1, 0, 0, tzinfo=dt.UTC)
SPLIT = dt.datetime(2026, 11, 8, 0, 0, tzinfo=dt.UTC)
TEST_LAST_ORIGIN = dt.datetime(2026, 11, 14, 18, 0, tzinfo=dt.UTC)
OBS_START = dt.datetime(2026, 10, 31, 0, 0, tzinfo=dt.UTC)
OBS_END = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.UTC)
MAX_SEA_DISTANCE_KM = 15.0


@dataclass(frozen=True)
class BasinStation:
    station: mt.Station
    basin: str  # "ATL" or "MED"

    def model_for(self, forcing: str) -> str:
        suffix = "P" if forcing == "arpege" else "O"
        prefix = "HYCOM2D-WAR" if self.basin == "ATL" else "HYCOM2D-MAR"
        return f"{prefix}{suffix}"


STATIONS = [BasinStation(s, "ATL") for s in mtc.STATIONS] + [
    BasinStation(mt.Station("refmar", "524", "Marseille", 43.278814, 5.353758), "MED")
]


def _fetch_refmar_window(station: mt.Station, cache_dir: Path) -> list[tuple[float, float]]:
    cache = cache_dir / f"refmar-surge-{station.station_id}-nov.json.gz"
    params = {"sources": "1", "dtStart": f"{OBS_START:%Y-%m-%dT%H:%M:%SZ}", "dtEnd": f"{OBS_END:%Y-%m-%dT%H:%M:%SZ}"}
    payload = mt._get_json(mt.REFMAR_URL.format(station=station.station_id), params, cache)
    return sorted(set(mt.parse_refmar(payload)))


def _run_cycles(start: dt.datetime, end_inclusive: dt.datetime) -> list[dt.datetime]:
    out: list[dt.datetime] = []
    cur = start.replace(minute=0, second=0, microsecond=0)
    while cur <= end_inclusive:
        if cur.hour in CYCLE_HOURS:
            out.append(cur)
        cur += dt.timedelta(hours=6)
    return out


def _sample_grib_cached(
    client: httpx.Client,
    model: str,
    run_utc: dt.datetime,
    lead_h: int,
    basin_stations: list[BasinStation],
    cache_dir: Path,
) -> dict[str, Optional[float]]:
    run_tag = run_utc.strftime("%Y%m%dT%H%MZ")
    sample_cache = cache_dir / "hycom_samples" / f"{model}__{run_tag}__{lead_h:03d}.json"
    if sample_cache.exists():
        return json.loads(sample_cache.read_text(encoding="utf-8"))

    grib_path = cache_dir / "hycom_grib2" / f"{model}__{run_tag}__{lead_h:03d}.grib2"
    raw: Optional[bytes] = None
    if grib_path.exists():
        raw = grib_path.read_bytes()
    else:
        url = grib_url(model, run_utc, lead_h)
        resp = client.get(url, timeout=30.0)
        if resp.status_code == 200 and resp.content:
            grib_path.parent.mkdir(parents=True, exist_ok=True)
            grib_path.write_bytes(resp.content)
            raw = resp.content
        elif resp.status_code == 404:
            raw = None
        else:
            resp.raise_for_status()

    result: dict[str, Optional[float]] = {bs.station.station_id: None for bs in basin_stations}
    if raw is not None:
        grid = decode_surge(raw, model=model)
        for bs in basin_stations:
            hit = grid.sample(bs.station.lat, bs.station.lon, max_distance_km=MAX_SEA_DISTANCE_KM)
            result[bs.station.station_id] = hit.surge_m if hit is not None else None

    sample_cache.parent.mkdir(parents=True, exist_ok=True)
    sample_cache.write_text(json.dumps(result), encoding="utf-8")
    return result


def prefetch_hycom_series(cache_dir: Path) -> dict[tuple[str, dt.datetime, int], dict[str, Optional[float]]]:
    """Download/decode all (model, run_utc, lead_h) surge samples needed for train + test."""
    runs = _run_cycles(WINDOW_START, TEST_LAST_ORIGIN)
    atl_stations = [bs for bs in STATIONS if bs.basin == "ATL"]
    med_stations = [bs for bs in STATIONS if bs.basin == "MED"]

    jobs: list[tuple[str, dt.datetime, int, list[BasinStation]]] = []
    # Also fetch lead_h=0 when available so the initial hybrid error can read hycom at run time;
    # at origin t0 we actually need the surge prediction valid near t0 - 20m, approximated cleanly from run 000H or prior cycle!
    leads_needed = sorted(set(LEADS_H) | {0})
    for run in runs:
        for lead_h in leads_needed:
            if run + dt.timedelta(hours=lead_h) > OBS_END:
                continue
            jobs.append(("HYCOM2D-WARP", run, lead_h, atl_stations))
            jobs.append(("HYCOM2D-WARO", run, lead_h, atl_stations))
            jobs.append(("HYCOM2D-MARP", run, lead_h, med_stations))
            jobs.append(("HYCOM2D-MARO", run, lead_h, med_stations))

    out: dict[tuple[str, dt.datetime, int], dict[str, Optional[float]]] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, limits=httpx.Limits(max_connections=6)) as client:
        def _worker(job: tuple[str, dt.datetime, int, list[BasinStation]]) -> tuple[tuple[str, dt.datetime, int], dict[str, Optional[float]]]:
            model, run, lh, stations = job
            return (model, run, lh), _sample_grib_cached(client, model, run, lh, stations, cache_dir)

        with ThreadPoolExecutor(max_workers=4) as pool:
            for key, res in pool.map(_worker, jobs):
                out[key] = res
    return out


@dataclass
class GaugeData:
    bs: BasinStation
    mjd: np.ndarray
    raw: np.ndarray
    smooth: np.ndarray
    eot20: np.ndarray
    offset_eot20: float


def build_gauges(tide_model: TideModel, cache_dir: Path) -> list[GaugeData]:
    split_mjd = mjd_from_unix(SPLIT.timestamp())
    start_mjd = mjd_from_unix(WINDOW_START.timestamp())
    gauges: list[GaugeData] = []
    for bs in STATIONS:
        values = _fetch_refmar_window(bs.station, cache_dir)
        minutes = np.round(np.array([m for m, _ in values]) * 1440.0).astype(np.int64)
        first = int(minutes.min())
        raw = np.full(int(minutes.max()) - first + 1, np.nan)
        raw[minutes - first] = [v for _, v in values]
        mjd, smooth = mt.smooth_minutes(values)
        cell = tide_model.nearest_cell(bs.station.lat, bs.station.lon)
        cal = calibration_for(bs.station.lat, bs.station.lon)
        eot20 = calibrated_heights(tide_model, cell.constants, mjd, cal)
        train_mask = (mjd >= start_mjd) & (mjd < split_mjd)
        offset_eot20 = float(np.nanmean((smooth - eot20)[train_mask]))
        gauges.append(GaugeData(bs, mjd, raw, smooth, eot20, offset_eot20))
    return gauges


def _metrics(err: np.ndarray, base_err: np.ndarray) -> dict[str, float]:
    return {
        "rms_cm": float(np.sqrt(np.mean(err**2)) * 100.0),
        "mae_cm": float(np.mean(np.abs(err)) * 100.0),
        "bias_cm": float(np.mean(err) * 100.0),
        "better_pct": float(np.mean(np.abs(err) < np.abs(base_err)) * 100.0),
    }


def _corr(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    if len(x) < 3 or np.std(x) < 1e-9 or np.std(y) < 1e-9:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def score_gauge(
    gd: GaugeData,
    hycom: dict[tuple[str, dt.datetime, int], dict[str, Optional[float]]],
) -> dict[str, Any]:
    raw_res = gd.raw - gd.eot20 - gd.offset_eot20
    runs = _run_cycles(WINDOW_START, TEST_LAST_ORIGIN)
    train_runs = [r for r in runs if r < SPLIT]
    test_runs = [r for r in runs if r >= SPLIT]

    model_p = gd.bs.model_for("arpege")
    model_o = gd.bs.model_for("arome")
    sid = gd.bs.station.station_id

    def _obs_at(ts: dt.datetime) -> Optional[tuple[float, float]]:
        mjd_t = mjd_from_unix(ts.timestamp())
        idx = int(round((mjd_t - gd.mjd[0]) / MINUTE))
        if not 0 <= idx < len(gd.mjd):
            return None
        val = gd.smooth[idx]
        if not np.isfinite(val):
            return None
        return float(val - gd.offset_eot20), float(gd.eot20[idx])

    # Estimate HYCOM train bias per forcing strictly on train runs before SPLIT.
    train_errs_p: list[float] = []
    train_errs_o: list[float] = []
    for run in train_runs:
        for lh in LEADS_H:
            target = run + dt.timedelta(hours=lh)
            if target >= SPLIT:
                continue
            obs = _obs_at(target)
            sp = hycom.get((model_p, run, lh), {}).get(sid)
            so = hycom.get((model_o, run, lh), {}).get(sid)
            if obs is None or sp is None or so is None:
                continue
            truth_surge = obs[0] - obs[1]
            train_errs_p.append(sp - truth_surge)
            train_errs_o.append(so - truth_surge)

    bias_p = float(np.mean(train_errs_p)) if train_errs_p else 0.0
    bias_o = float(np.mean(train_errs_o)) if train_errs_o else 0.0

    per_lead: dict[int, dict[str, Any]] = {}
    for lh in LEADS_H:
        arrays: dict[str, list[float]] = {k: [] for k in ("obs_surge", "eot20", "adr0017", "arpege_raw", "arpege_deb", "arome_raw", "arome_deb", "hybrid_deb")}
        pred_surge_p: list[float] = []
        pred_surge_o: list[float] = []
        pred_surge_adr: list[float] = []
        pred_surge_hyb: list[float] = []

        for run in test_runs:
            target = run + dt.timedelta(hours=lh)
            if target > OBS_END:
                continue
            obs = _obs_at(target)
            sp = hycom.get((model_p, run, lh), {}).get(sid)
            so = hycom.get((model_o, run, lh), {}).get(sid)
            sp0 = hycom.get((model_p, run, 0), {}).get(sid)
            origin_mjd = mjd_from_unix(run.timestamp())
            target_mjd = mjd_from_unix(target.timestamp())
            found = tc.trailing_anomaly(gd.mjd, raw_res, origin_mjd - mtc.LAG_MINUTES * MINUTE)
            if obs is None or sp is None or so is None or sp0 is None or found is None:
                continue

            truth_height, eot_target = obs
            obs_surge = truth_height - eot_target
            adr_corr = float(mtc.correction_at(np.array([target_mjd]), found[0], found[1])[0])
            # Hybrid decays initial model residual at t0 over TAU_HOURS (12 h):
            # residual_at_t0 = found[0] - (sp0 - bias_p)
            hyb_extra = (found[0] - (sp0 - bias_p)) * math.exp(-max(0.0, (target_mjd - found[1]) * 24.0) / tc.TAU_HOURS)
            hyb_surge = (sp - bias_p) + hyb_extra

            arrays["obs_surge"].append(obs_surge)
            arrays["eot20"].append(eot_target - truth_height)
            arrays["adr0017"].append((eot_target + adr_corr) - truth_height)
            arrays["arpege_raw"].append((eot_target + sp) - truth_height)
            arrays["arpege_deb"].append((eot_target + sp - bias_p) - truth_height)
            arrays["arome_raw"].append((eot_target + so) - truth_height)
            arrays["arome_deb"].append((eot_target + so - bias_o) - truth_height)
            arrays["hybrid_deb"].append((eot_target + hyb_surge) - truth_height)

            pred_surge_p.append(sp)
            pred_surge_o.append(so)
            pred_surge_adr.append(adr_corr)
            pred_surge_hyb.append(hyb_surge)

        if not arrays["obs_surge"]:
            continue
        obs_s = np.array(arrays["obs_surge"])
        base = np.array(arrays["eot20"])
        top20 = np.abs(obs_s) >= np.quantile(np.abs(obs_s), 0.80) if len(obs_s) >= 5 else np.ones(len(obs_s), dtype=bool)
        entry: dict[str, Any] = {
            "n": len(obs_s),
            "r_arpege": _corr(np.array(pred_surge_p), obs_s),
            "r_arome": _corr(np.array(pred_surge_o), obs_s),
            "r_adr0017": _corr(np.array(pred_surge_adr), obs_s),
            "r_hybrid": _corr(np.array(pred_surge_hyb), obs_s),
        }
        for key in ("eot20", "adr0017", "arpege_raw", "arpege_deb", "arome_raw", "arome_deb", "hybrid_deb"):
            err = np.array(arrays[key])
            m = _metrics(err, base)
            m["top20_rms_cm"] = float(np.sqrt(np.mean(err[top20] ** 2)) * 100.0)
            entry[key] = m
        per_lead[lh] = entry

    return {
        "station": gd.bs.station.name,
        "id": sid,
        "basin": gd.bs.basin,
        "offset_eot20_m": gd.offset_eot20,
        "train_bias_arpege_cm": bias_p * 100.0,
        "train_bias_arome_cm": bias_o * 100.0,
        "leads": per_lead,
    }


def _pooled_summary(station_results: list[dict[str, Any]], basin_filter: Optional[str] = None) -> dict[int, dict[str, Any]]:
    subset = [r for r in station_results if basin_filter is None or r["basin"] == basin_filter]
    summary: dict[int, dict[str, Any]] = {}
    for lh in LEADS_H:
        entries = [r["leads"][lh] for r in subset if lh in r["leads"]]
        if not entries:
            continue
        row: dict[str, Any] = {
            "gauges": len(entries),
            "n": sum(e["n"] for e in entries),
        }
        for rk in ("r_arpege", "r_arome", "r_adr0017", "r_hybrid"):
            vals = [e[rk] for e in entries if e[rk] is not None]
            row[rk] = float(np.mean(vals)) if vals else None
        for key in ("eot20", "adr0017", "arpege_raw", "arpege_deb", "arome_raw", "arome_deb", "hybrid_deb"):
            row[key] = {
                mk: float(np.mean([e[key][mk] for e in entries]))
                for mk in ("rms_cm", "mae_cm", "bias_cm", "better_pct", "top20_rms_cm")
            }
        summary[lh] = row
    return summary


def format_markdown(results: dict[str, Any]) -> str:
    lines = [
        "# Measure: Météo-France HYCOM2D storm surge vs SHOM REFMAR gauges (2026-11)",
        "",
        "Measured with [`tools/measure_mf_surge.py`](../../tools/measure_mf_surge.py) (raw stats: `mf-surge-2026-11-results.json.gz`), for [ADR 0025](../decisions/0025-hycom2d-surge-measured-and-ml-archive.md).",
        "",
        "**Setup.** 14 days of live OVH public S3 HYCOM2D GRIB2 runs (6-hourly cycles `00/06/12/18Z`, `HYCOM2D-WARP`/`WARO` in Atlantic-Channel, `HYCOM2D-MARP`/`MARO` in Mediterranean) scored against SHOM REFMAR 61-minute centered-mean sea level across 12 gauges (11 Atlantic/Channel gauges of ADR 0017 + Marseille).",
        f"- **Calibration window (chart datum + mean surge offset)**: `{WINDOW_START:%Y-%m-%d %HZ}` to `{SPLIT:%Y-%m-%d %HZ}`.",
        f"- **Held-out test window**: `{SPLIT:%Y-%m-%d %HZ}` to `{OBS_END:%Y-%m-%d %HZ}` (`<= {TEST_LAST_ORIGIN:%Y-%m-%d %HZ}` origins; REFMAR lag = 20 min for ADR 0017 and hybrid).",
        "",
        "## Atlantic & Channel (11 gauges, mean across stations)",
        "",
        "| Lead | n (total) | EOT20 RMS | ADR0017 RMS | HYCOM ARPEGE (deb) RMS | HYCOM AROME (deb) RMS | Hybrid RMS | HYCOM ARPEGE (raw) RMS | corr r(ARPEGE, obs) | corr r(AROME, obs) | corr r(ADR0017, obs) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for lh, row in sorted(results["pooled_atl"].items()):
        lines.append(
            f"| +{lh} h | {row['n']} | {row['eot20']['rms_cm']:.1f} | {row['adr0017']['rms_cm']:.1f} | "
            f"{row['arpege_deb']['rms_cm']:.1f} | {row['arome_deb']['rms_cm']:.1f} | {row['hybrid_deb']['rms_cm']:.1f} | "
            f"{row['arpege_raw']['rms_cm']:.1f} | {row['r_arpege'] or 0.0:.2f} | {row['r_arome'] or 0.0:.2f} | {row['r_adr0017'] or 0.0:.2f} |"
        )

    lines += [
        "",
        "## Highest-surge hours (top 20% |non-tidal anomaly|, Atlantic & Channel RMS cm)",
        "",
        "| Lead | EOT20 | ADR0017 | HYCOM ARPEGE (deb) | HYCOM AROME (deb) | Hybrid |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for lh, row in sorted(results["pooled_atl"].items()):
        lines.append(
            f"| +{lh} h | {row['eot20']['top20_rms_cm']:.1f} | {row['adr0017']['top20_rms_cm']:.1f} | "
            f"{row['arpege_deb']['top20_rms_cm']:.1f} | {row['arome_deb']['top20_rms_cm']:.1f} | {row['hybrid_deb']['top20_rms_cm']:.1f} |"
        )

    lines += [
        "",
        "## Mediterranean — Marseille (REFMAR 524, HYCOM2D-MARP/MARO, RMS cm)",
        "",
        "| Lead | n | EOT20 | ADR0017 | HYCOM MARP (deb) | HYCOM MARO (deb) | Hybrid | r(MARP) | r(ADR0017) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for lh, row in sorted(results["pooled_med"].items()):
        lines.append(
            f"| +{lh} h | {row['n']} | {row['eot20']['rms_cm']:.1f} | {row['adr0017']['rms_cm']:.1f} | "
            f"{row['arpege_deb']['rms_cm']:.1f} | {row['arome_deb']['rms_cm']:.1f} | {row['hybrid_deb']['rms_cm']:.1f} | "
            f"{row['r_arpege'] or 0.0:.2f} | {row['r_adr0017'] or 0.0:.2f} |"
        )

    lines += [
        "",
        "## Per-station breakdown at +24 h lead (RMS cm, r(ARPEGE, obs))",
        "",
        "| Station | Basin | Train ARPEGE bias (cm) | EOT20 | ADR0017 | HYCOM ARPEGE (deb) | HYCOM AROME (deb) | Hybrid | r(ARPEGE) |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sr in results["stations"]:
        e24 = sr["leads"].get(24)
        if not e24:
            continue
        lines.append(
            f"| {sr['station']} | {sr['basin']} | {sr['train_bias_arpege_cm']:+.1f} | "
            f"{e24['eot20']['rms_cm']:.1f} | {e24['adr0017']['rms_cm']:.1f} | "
            f"{e24['arpege_deb']['rms_cm']:.1f} | {e24['arome_deb']['rms_cm']:.1f} | {e24['hybrid_deb']['rms_cm']:.1f} | "
            f"{e24['r_arpege'] or 0.0:.2f} |"
        )

    lines += [
        "",
        "## Attribution",
        "",
        "- REFMAR 1-minute tide gauge observations: SHOM (Licence Ouverte Etalab 2.0).",
        "- HYCOM2D storm surge fields (`HYCOM2D-WARP`, `HYCOM2D-WARO`, `HYCOM2D-MARP`, `HYCOM2D-MARO`): Météo-France / SHOM HOMONIM (Licence Ouverte Etalab 2.0, `meteofrance-pnt` OVH S3).",
        "- Astronomical tide baseline: EOT20 (Hart-Davis et al. 2021, CC BY 4.0) with ADR 0019 Brest calibration.",
        "",
    ]
    return "\n".join(lines)


def measure(cache_dir: Path) -> dict[str, Any]:
    tide_model = TideModel()
    hycom = prefetch_hycom_series(cache_dir)
    gauges = build_gauges(tide_model, cache_dir)
    station_results = [score_gauge(gd, hycom) for gd in gauges]
    return {
        "window_start": WINDOW_START.isoformat(),
        "split": SPLIT.isoformat(),
        "obs_end": OBS_END.isoformat(),
        "stations": station_results,
        "pooled_atl": _pooled_summary(station_results, "ATL"),
        "pooled_med": _pooled_summary(station_results, "MED"),
        "pooled_all": _pooled_summary(station_results, None),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True, help="Local cache directory outside the repo.")
    parser.add_argument("--write", action="store_true", help="Write docs/measurements/mf-surge-2026-11.md and JSON.gz.")
    args = parser.parse_args()

    results = measure(args.cache_dir)
    report = format_markdown(results)
    print(report)
    if args.write:
        out_dir = REPO_ROOT / "docs" / "measurements"
        (out_dir / "mf-surge-2026-11.md").write_text(report, encoding="utf-8")
        (out_dir / "mf-surge-2026-11-results.json.gz").write_bytes(gzip.compress(json.dumps(results, indent=2).encode("utf-8")))
        print(f"Wrote {out_dir / 'mf-surge-2026-11.md'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
