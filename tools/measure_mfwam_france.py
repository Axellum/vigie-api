"""Measure the Météo-France wave model of France (MFWAM 0.025 degrees, coupled with AROME) against buoys, next to the source Vigie serves today.

    python tools/measure_mfwam_france.py --cache-dir DIR                 # print the report (asks what the cache lacks)
    python tools/measure_mfwam_france.py --cache-dir DIR --offline       # the cache only, nothing is asked
    python tools/measure_mfwam_france.py --cache-dir DIR --write results.json

The protocol and the criterion are those of ``docs/decisions/0029-mfwam-france-measured-against-buoys.md``. In short:

- **offshore**: the ten moored buoys of Météo-France (``DPObs/v2/bouees``, hourly, the last 5 days), the significant height, the mean period and
  the direction of the waves, against MFWAM 0.025 (``DPPaquetWAVESMODELS``, package ``SP1``, the runs still published, steps 6, 24 and 48 hours)
  and against what Vigie serves today (Open-Meteo Marine, ``models=meteofrance_wave``: the past values for the 6 hours, ``*_previous_day1`` and
  ``*_previous_day2`` for 24 and 48 hours);
- **nearshore**: one snapshot of the CANDHIS real-time buoys (``getCampListeTR.php``, the latest hourly record of every buoy) against the newest
  MFWAM 0.025 run, interpolated to the minute of the record, and against Open-Meteo at the same time.

The MFWAM files are GRIB2 compressed with CCSDS (ecCodes decodes them); each is 5 to 10 MB and the runs go away after about three days, so only the
values at the buoys are kept in the cache folder. Keys: ``VIGIE_METEOFRANCE_API_KEY`` (header ``apikey``) and ``VIGIE_CANDHIS_API_KEY`` (header
``Authorization``), read from the environment, never printed. Nothing here runs inside the API.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

USER_AGENT = "vigie-api measure_mfwam_france (personal project)"
PACKAGE_URL = "https://public-api.meteofrance.fr/previnum/DPPaquetWAVESMODELS/v1/models/MFWAM/grids/0.025/packages/SP1"
BUOYS_URL = "https://public-api.meteofrance.fr/public/DPObs/v2/bouees?format=json"
CANDHIS_URL = "https://candhis.cerema.fr/API/v1/getCampListeTR.php"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
LEADS = (6, 24, 48)  # hours; Open-Meteo has no past forecast under a day, so 6 h is compared with its past values
OM_SUFFIX = {6: "", 24: "_previous_day1", 48: "_previous_day2"}
# Grid of the files (read on 2026-11-18): 38 to 53 N, 8 W to 12 E, 0.025 degrees, 601 rows from the north, 801 columns from 352 E.
NJ, NI, LAT0, LON0, STEP = 601, 801, 53.0, 352.0, 0.025
# GRIB2 local parameter numbers of discipline 10, category 0 (what ecCodes does not name), by the position in the package description.
PARAMS = {14: "mwd", 15: "mwp", 3: "swh"}
GRID_BOX = (38.0, 53.0, -8.0, 12.0)
MIN_HOURS_PER_LEAD = 30  # a lead with fewer paired hours than this is described, not judged
STORM_M = 2.0


def key(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"{name} is not set")
    return value


# ------------------------------------------------------------------------------------------------------------------ pure parts


def valid_time(run: dt.datetime, lead_hours: int) -> int:
    """The unix time a run's step is valid for."""
    return int((run + dt.timedelta(hours=lead_hours)).timestamp())


def circular_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """``a - b`` on the circle of directions, in ``[-180, 180)`` degrees."""
    return (a - b + 180.0) % 360.0 - 180.0


def buoy_positions(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One fixed position per buoy (the median of the positions it reported: a mooring drifts by a few hundred metres)."""
    seen: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        seen[str(row["geo_id_wmo"])].append(row)
    return {
        buoy: {
            "name": str(items[0]["name"]),
            "lat": float(np.median([r["lat"] for r in items])),
            "lon": float(np.median([r["lon"] for r in items])),
        }
        for buoy, items in seen.items()
    }


def hourly_waves(rows: list[dict[str, Any]]) -> dict[str, dict[int, tuple[float, Optional[float], Optional[float]]]]:
    """Per buoy, the records dated exactly on the hour that carry a wave height: ``{unix: (height m, period s, direction deg)}``."""
    out: dict[str, dict[int, tuple[float, Optional[float], Optional[float]]]] = defaultdict(dict)
    for row in rows:
        if row.get("haut_vag") is None:
            continue
        moment = dt.datetime.strptime(row["validity_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        if moment.minute or moment.second:
            continue
        period, direction = row.get("per_moy_vag"), row.get("dir_vag")
        out[str(row["geo_id_wmo"])][int(moment.timestamp())] = (
            float(row["haut_vag"]),
            None if period is None or period <= 0 else float(period),
            None if direction is None else float(direction),
        )
    return out


def nearest_water_cell(field: np.ndarray, lat: float, lon: float, max_cells: int = 4) -> Optional[tuple[int, int, float]]:
    """``(row, column, distance in cells)`` of the nearest cell of ``field`` that holds a value, within ``max_cells``, or ``None``.

    The rows run from the north (``LAT0``) and the columns from ``LON0`` east (GRIB2 counts longitudes from 0 to 360)."""
    j0 = round((LAT0 - lat) / STEP)
    i0 = round(((lon % 360.0) - LON0) % 360.0 / STEP)
    best: Optional[tuple[float, int, int]] = None
    for dj in range(-max_cells, max_cells + 1):
        for di in range(-max_cells, max_cells + 1):
            j, i = j0 + dj, i0 + di
            if 0 <= j < field.shape[0] and 0 <= i < field.shape[1] and not math.isnan(float(field[j, i])):
                distance = math.hypot(dj, di)
                if best is None or distance < best[0]:
                    best = (distance, j, i)
    return None if best is None else (best[1], best[2], best[0])


def stats(forecast: np.ndarray, observed: np.ndarray, mask: Optional[np.ndarray] = None) -> dict[str, Any]:
    """Bias (forecast minus buoy), mean absolute error, root mean square error and correlation over the hours with both values."""
    ok = ~np.isnan(forecast) & ~np.isnan(observed)
    if mask is not None:
        ok &= mask
    f, o = forecast[ok], observed[ok]
    if len(f) == 0:
        return {"n": 0}
    d = f - o
    return {
        "n": int(len(f)),
        "bias": float(d.mean()),
        "mae": float(np.abs(d).mean()),
        "rmse": float(math.sqrt(float((d**2).mean()))),
        "r": float(np.corrcoef(f, o)[0, 1]) if len(f) > 2 and f.std() > 0 and o.std() > 0 else None,
    }


def verdict(candidate: dict[str, Any], reference: dict[str, Any], candidate_storm: dict[str, Any], reference_storm: dict[str, Any]) -> bool:
    """ADR 0029: the candidate beats the reference if its RMSE is at least 5 % lower and does not rise over the hours the buoys read 2 m or more."""
    if not candidate.get("n") or candidate["n"] < MIN_HOURS_PER_LEAD or reference.get("rmse") in (None, 0):
        return False
    if candidate["rmse"] > 0.95 * reference["rmse"]:
        return False
    return not candidate_storm.get("n") or candidate_storm["rmse"] <= reference_storm["rmse"]


# ------------------------------------------------------------------------------------------------------------------ downloads


def _get(client: Any, url: str, headers: dict[str, str], params: Optional[dict[str, str]] = None) -> Any:
    for _attempt in range(3):
        response = client.get(url, params=params, headers={"User-Agent": USER_AGENT, **headers}, timeout=180)
        if response.status_code == 429:
            time.sleep(60)
            continue
        response.raise_for_status()
        time.sleep(1.5)
        return response
    raise SystemExit(f"{url.split('?')[0]}: still 429 after 3 attempts")


def decode_waves(data: bytes) -> dict[str, np.ndarray]:
    """The significant height, the mean period and the mean direction of the waves of one MFWAM file (``9999`` = missing -> NaN)."""
    import eccodes  # imported only when a file is read

    out: dict[str, np.ndarray] = {}
    position = 0
    while position + 16 <= len(data):  # section 0 of a GRIB2 message holds its length: the file is the messages one after the other
        if data[position : position + 4] != b"GRIB":
            raise ValueError("not a GRIB2 buffer")
        length = struct.unpack(">Q", data[position + 8 : position + 16])[0]
        message = eccodes.codes_new_from_message(data[position : position + length])
        position += length
        try:
            if int(eccodes.codes_get(message, "discipline")) == 10 and int(eccodes.codes_get(message, "parameterCategory")) == 0:
                number = int(eccodes.codes_get(message, "parameterNumber"))
                if number in PARAMS:
                    field = eccodes.codes_get_values(message).reshape(NJ, NI).astype(np.float32)
                    field[field >= 9000.0] = np.nan
                    out[PARAMS[number]] = field
        finally:
            eccodes.codes_release(message)
    if set(out) != set(PARAMS.values()):
        raise ValueError(f"MFWAM file without {set(PARAMS.values()) - set(out)}")
    return out


def sample(fields: dict[str, np.ndarray], lat: float, lon: float) -> Optional[dict[str, float]]:
    cell = nearest_water_cell(fields["swh"], lat, lon)
    if cell is None:
        return None
    j, i, distance = cell
    return {"cells": distance, **{name: (None if math.isnan(float(f[j, i])) else float(f[j, i])) for name, f in fields.items()}}


def run_step_values(client: Any, run: dt.datetime, step: int, points: dict[str, tuple[float, float]]) -> dict[str, Optional[dict[str, float]]]:
    url = f"{PACKAGE_URL}/productMFWAM?referencetime={run:%Y-%m-%dT%H}:00:00Z&time={step:03d}H&format=grib2"
    response = _get(client, url, {"apikey": key("VIGIE_METEOFRANCE_API_KEY")})
    fields = decode_waves(response.content)
    return {name: sample(fields, lat, lon) for name, (lat, lon) in points.items()}


def list_runs(client: Any) -> list[dt.datetime]:
    payload = _get(client, PACKAGE_URL, {"apikey": key("VIGIE_METEOFRANCE_API_KEY")}).json()
    runs = []
    for link in payload.get("links", []):
        href = link.get("href", "")
        if "referencetime=" in href:
            runs.append(dt.datetime.strptime(href.split("referencetime=")[1][:20], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc))
    return sorted(runs)


def open_meteo(client: Any, lats: list[float], lons: list[float], hourly: str, past_days: int) -> list[dict[str, Any]]:
    params = {
        "latitude": ",".join(f"{v:.4f}" for v in lats),
        "longitude": ",".join(f"{v:.4f}" for v in lons),
        "hourly": hourly,
        "models": "meteofrance_wave",
        "past_days": str(past_days),
        "forecast_days": "2",
        "timeformat": "unixtime",
    }
    payload = _get(client, MARINE_URL, {}, params).json()
    return payload if isinstance(payload, list) else [payload]


def om_value(item: dict[str, Any], variable: str, unix: int) -> Optional[float]:
    hourly = item["hourly"]
    try:
        value = hourly[variable][hourly["time"].index(unix)]
    except (KeyError, ValueError):
        return None
    return None if value is None else float(value)


# ------------------------------------------------------------------------------------------------------------------ the two measures


def offshore(client: Any, cache: Path, offline: bool) -> dict[str, Any]:
    """The rows (buoy, lead, hour, buoy value, MFWAM 0.025, Open-Meteo) of the 5 days the Météo-France buoys keep."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    if offline:
        raise SystemExit(f"--offline: {cache.name} is not in the cache")
    rows = _get(client, BUOYS_URL, {"apikey": key("VIGIE_METEOFRANCE_API_KEY")}).json()
    positions = buoy_positions(rows)
    observed = hourly_waves(rows)
    points = {b: (p["lat"], p["lon"]) for b, p in positions.items() if GRID_BOX[0] <= p["lat"] <= GRID_BOX[1] and GRID_BOX[2] <= p["lon"] <= GRID_BOX[3]}
    variables = ["wave_height", "wave_period", "wave_direction"]
    hourly = ",".join(variables + [v + OM_SUFFIX[lead] for v in variables for lead in LEADS if OM_SUFFIX[lead]])
    ids = list(points)
    reference = dict(zip(ids, open_meteo(client, [points[b][0] for b in ids], [points[b][1] for b in ids], hourly, past_days=6), strict=True))
    out_rows: list[dict[str, Any]] = []
    for run in list_runs(client):
        for lead in LEADS:
            try:
                values = run_step_values(client, run, lead, points)
            except Exception as error:  # a step not yet published, or a run that was withdrawn
                print(f"  run {run:%d %H}Z +{lead} h: {type(error).__name__}", file=sys.stderr)
                continue
            unix = valid_time(run, lead)
            for buoy, got in values.items():
                if got is None or unix not in observed.get(buoy, {}):
                    continue
                height, period, direction = observed[buoy][unix]
                suffix = OM_SUFFIX[lead]
                out_rows.append(
                    {
                        "buoy": positions[buoy]["name"], "lead": lead, "unix": unix, "obs": [height, period, direction],
                        "mfwam": [got["swh"], got["mwp"], got["mwd"]],
                        "open_meteo": [om_value(reference[buoy], v + suffix, unix) for v in variables],
                    }
                )
    if not out_rows:
        raise SystemExit("no paired hour: nothing is cached")
    result = {"rows": out_rows, "buoys": sorted(positions[b]["name"] for b in points)}
    cache.write_text(json.dumps(result), encoding="utf-8")
    return result


def nearshore(client: Any, cache: Path, offline: bool, stations: Path) -> dict[str, Any]:
    """The latest record of every CANDHIS real-time buoy inside the grid, against MFWAM 0.025 and Open-Meteo at the minute of the record."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    if offline:
        raise SystemExit(f"--offline: {cache.name} is not in the cache")
    known = {s["id"]: s for s in json.loads(stations.read_text(encoding="utf-8"))["stations"]}
    records = []
    for kind in (0, 1, 2):
        answer = _get(client, CANDHIS_URL, {"Authorization": key("VIGIE_CANDHIS_API_KEY")}, {"type": str(kind)}).json()
        for row in answer.get("results") or []:
            station = known.get(row[0])
            height = float(row[2])
            if station is None or height >= 900 or not (GRID_BOX[0] <= station["lat"] <= GRID_BOX[1] and GRID_BOX[2] <= station["lon"] <= GRID_BOX[3]):
                continue
            moment = dt.datetime.strptime(row[1], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)  # the guide: UTC
            records.append((station, moment, height))
    run = max(r for r in list_runs(client) if r <= min(m for _, m, _ in records) - dt.timedelta(hours=1))
    steps = sorted({int((m - run).total_seconds() // 3600) + extra for _, m, _ in records for extra in (0, 1 if m.minute else 0)})
    points = {s["id"]: (s["lat"], s["lon"]) for s, _, _ in records}
    at = {step: run_step_values(client, run, step, points) for step in steps}
    reference = dict(zip(points, open_meteo(client, [p[0] for p in points.values()], [p[1] for p in points.values()], "wave_height", past_days=1), strict=True))
    rows = []
    for station, moment, height in records:
        floor = moment.replace(minute=0)
        weight = moment.minute / 60.0
        step = int((floor - run).total_seconds() // 3600)
        first, second = at[step][station["id"]], at.get(step + 1, {}).get(station["id"])
        if first is None or first["swh"] is None:
            continue
        mfwam = first["swh"] if not weight else first["swh"] * (1 - weight) + (second or first)["swh"] * weight
        a = om_value(reference[station["id"]], "wave_height", int(floor.timestamp()))
        b = om_value(reference[station["id"]], "wave_height", int((floor + dt.timedelta(hours=1)).timestamp())) if weight else a
        if a is None or b is None:
            continue
        rows.append({"buoy": station["name"], "obs": height, "mfwam": mfwam, "open_meteo": a * (1 - weight) + b * weight, "cells": first["cells"]})
    result = {"run": f"{run:%Y-%m-%dT%H}Z", "rows": rows}
    cache.write_text(json.dumps(result), encoding="utf-8")
    return result


# ------------------------------------------------------------------------------------------------------------------ the report


def _col(rows: list[dict[str, Any]], name: str, index: Optional[int] = None) -> np.ndarray:
    values = [r[name] if index is None else r[name][index] for r in rows]
    return np.array([math.nan if v is None else v for v in values], dtype=float)


def _line(name: str, s: dict[str, Any]) -> str:
    if not s.get("n"):
        return f"| {name} | 0 | | | | |"
    r = "" if s["r"] is None else f"{s['r']:.2f}"
    return f"| {name} | {s['n']} | {s['bias']:+.2f} | {s['mae']:.2f} | {s['rmse']:.2f} | {r} |"


def report(far: dict[str, Any], near: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    out: list[str] = []
    summary: dict[str, Any] = {"offshore": {}, "nearshore": {}}
    head = "| source | hours | bias | MAE | RMSE | r |\n|---|---|---|---|---|---|"
    out.append(f"Offshore: {len(far['buoys'])} Météo-France buoys in the grid ({', '.join(far['buoys'])}).\n")
    for lead in LEADS:
        rows = [r for r in far["rows"] if r["lead"] == lead]
        obs = _col(rows, "obs", 0)
        mf, om = _col(rows, "mfwam", 0), _col(rows, "open_meteo", 0)
        storm = obs >= STORM_M
        entry = {
            "mfwam": stats(mf, obs), "open_meteo": stats(om, obs),
            "mfwam_storm": stats(mf, obs, storm), "open_meteo_storm": stats(om, obs, storm),
        }
        entry["mfwam_better"] = verdict(entry["mfwam"], entry["open_meteo"], entry["mfwam_storm"], entry["open_meteo_storm"])
        summary["offshore"][str(lead)] = entry
        out += [f"Height, +{lead} h ({len(rows)} paired hours):\n", head, _line("MFWAM 0.025", entry["mfwam"]), _line("Open-Meteo", entry["open_meteo"]),
                _line(f"MFWAM 0.025, buoy >= {STORM_M:g} m", entry["mfwam_storm"]), _line(f"Open-Meteo, buoy >= {STORM_M:g} m", entry["open_meteo_storm"]), ""]
        for label, index in (("period (s)", 1), ("direction (deg)", 2)):
            o = _col(rows, "obs", index)
            for name, source in (("MFWAM 0.025", "mfwam"), ("Open-Meteo", "open_meteo")):
                f = _col(rows, source, index)
                if index == 2:
                    d = np.abs(circular_difference(f, o))
                    d = d[~np.isnan(d)]
                    out.append(f"- {label}, {name}: mean absolute error {d.mean():.1f} over {len(d)} hours")
                else:
                    s = stats(f, o)
                    out.append(f"- {label}, {name}: bias {s['bias']:+.2f}, mean absolute error {s['mae']:.2f} over {s['n']} hours")
        out.append("")
    rows = near["rows"]
    obs, mf, om = _col(rows, "obs"), _col(rows, "mfwam"), _col(rows, "open_meteo")
    storm = obs >= STORM_M
    entry = {"mfwam": stats(mf, obs), "open_meteo": stats(om, obs), "mfwam_storm": stats(mf, obs, storm), "open_meteo_storm": stats(om, obs, storm)}
    summary["nearshore"] = entry
    out += [f"Nearshore: {len(rows)} CANDHIS buoys, one record each, run {near['run']}:\n", head.replace("hours", "buoys"), _line("MFWAM 0.025", entry["mfwam"]),
            _line("Open-Meteo", entry["open_meteo"]), _line(f"MFWAM 0.025, buoy >= {STORM_M:g} m", entry["mfwam_storm"]),
            _line(f"Open-Meteo, buoy >= {STORM_M:g} m", entry["open_meteo_storm"]), ""]
    return "\n".join(out), summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="read the cache only; ask nothing")
    parser.add_argument("--write", type=Path, help="also write the statistics as JSON")
    parser.add_argument("--stations", type=Path, default=Path(__file__).resolve().parents[1] / "app" / "data" / "candhis-stations.json")
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    import httpx

    with httpx.Client() as client:
        far = offshore(client, args.cache_dir / "offshore.json", args.offline)
        near = nearshore(client, args.cache_dir / "nearshore.json", args.offline, args.stations)
    text, summary = report(far, near)
    print(text)
    if args.write:
        args.write.write_text(json.dumps(summary, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
