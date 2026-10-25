"""Backtest of the unofficial pre-alert for Turkey on past GFS runs (ADR 0011). A measure, not part of the application.

    python tools/backtest_prealert_tr.py values  --cache-dir DIR           # read the runs, write the province values
    python tools/backtest_prealert_tr.py choose  --values FILE             # the threshold table the learning set suggests
    python tools/backtest_prealert_tr.py score   --values FILE --set test  # score the frozen THRESHOLDS on a set, ONCE

What it reads: the GFS 0.25 degree runs of 00 UTC kept on AWS (``noaa-gfs-bdp-pds``, "can be used as desired",
https://registry.opendata.aws/noaa-gfs-bdp-pds/), each field fetched alone by a byte range found in the file's ``.idx``. A field is
the whole world (about 0.5 to 1 MB); it is cut to Turkey at once and only the cut is kept on disk (``--cache-dir``), so a second run
of the tool asks nothing. The code that turns the fields into days and provinces is the application's (``app/prealert``): the live
pilot and this measure compute the same way.

Which days:

- **Episodes** (``docs/measurements/prealert-tr-episodes.csv``): every day of every episode, from the run of 00 UTC of that day
  (lead J0) and of the day before (J1). A pair (episode, province) is a hit at a lead when the province reaches level 2 or more for
  one of the episode's phenomena on one of its days. ``revision_of`` lines are not counted again.
- **Ordinary days** (``--sample-every``, 7 by default): one day in seven of the learning years, at J0, every province: how often each
  level would have been given. This bounds over-alerting on the learning set; it does not measure false alarms (no list of the days
  without a warning exists).

``choose`` looks at the learning set only (2024 and 2025). ``score`` refuses the test set unless ``--i-have-frozen-the-thresholds`` is
given: the test set is scored once, with the table that is in ``app/prealert/levels.py``.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import tempfile
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.prealert.gfs import Box, Message, decode, run_days, run_time  # noqa: E402
from app.prealert.levels import PHENOMENA, THRESHOLDS, Threshold, province_quantities  # noqa: E402
from app.prealert.provinces import provinces_on_grid  # noqa: E402

BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
# ECMWF IFS open data kept on AWS (CC BY 4.0 and the ECMWF Terms of Use, https://registry.opendata.aws/ecmwf-forecasts/, read
# 2026-10-25): the 0.25 degree files begin on 2024-02-29 (before, a 0.4 degree beta without gusts nor temperature extremes).
ECMWF_BUCKET = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
ECMWF_FIRST_DAY = date(2024, 2, 29)
ECMWF_PARAMS = ("tp", "10fg", "mx2t3", "mn2t3", "ptype")
EPISODES = REPO_ROOT / "docs" / "measurements" / "prealert-tr-episodes.csv"
TURKEY = Box(south=35.5, north=42.5, west=25.5, east=45.0)
USER_AGENT = "vigie-api backtest_prealert_tr (https://github.com/Axellum/vigie-api)"
LEARNING_YEARS = (2024, 2025)
TEST_YEARS = (2026,)

# Which messages of a file are read: the .idx line's variable, level, and a test on its time description.
WANTED = (
    ("APCP", "surface", lambda desc: desc.startswith("0-") and desc.endswith("acc fcst")),
    ("GUST", "surface", lambda desc: desc.endswith("hour fcst") and "-" not in desc),
    ("TMAX", "2 m above ground", lambda desc: desc.endswith("max fcst")),
    ("TMIN", "2 m above ground", lambda desc: desc.endswith("min fcst")),
    ("TMP", "2 m above ground", lambda desc: desc.endswith("hour fcst") and "-" not in desc),
    ("CSNOW", "surface", lambda desc: desc.endswith("ave fcst")),
)


# ---------------------------------------------------------------- reading the archive


class Archive:
    """The GFS files on AWS, read field by field and kept on disk cut to Turkey."""

    def __init__(self, cache: Path) -> None:
        self.cache = cache
        self.client = httpx.Client(timeout=120.0, headers={"User-Agent": USER_AGENT})
        self.bytes_read = 0

    def _url(self, run: datetime, lead: int) -> str:
        return f"{BUCKET}/gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f{lead:03d}"

    def _get(self, url: str, headers: Optional[dict[str, str]] = None) -> bytes:
        for attempt in range(4):
            try:
                response = self.client.get(url, headers=headers or {})
                response.raise_for_status()
                self.bytes_read += len(response.content)
                return response.content
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(2.0 * (attempt + 1))
        raise AssertionError("unreachable")

    def lead(self, run: datetime, lead: int) -> list[Message]:
        """The wanted messages of one file, cut to Turkey (from the disk when they were read before)."""
        path = self.cache / f"{run:%Y%m%d%H}" / f"f{lead:03d}.npz"
        if path.exists():
            return _load(path)
        base = self._url(run, lead)
        rows = [line.split(":") for line in self._get(base + ".idx").decode("ascii").splitlines() if line]
        messages: list[Message] = []
        seen: set[tuple[str, str]] = set()
        for k, row in enumerate(rows):
            variable, level, desc = row[3], row[4], row[5]
            for name, wanted_level, test in WANTED:
                if variable != name or level != wanted_level or not test(desc) or (name, desc) in seen:
                    continue
                seen.add((name, desc))
                start = int(row[1])
                end = f"{int(rows[k + 1][1]) - 1}" if k + 1 < len(rows) else ""
                messages.extend(decode(self._get(base, {"Range": f"bytes={start}-{end}"}), TURKEY))
        _save(path, messages)
        return messages


class EcmwfArchive(Archive):
    """The ECMWF IFS 0.25 degree files on AWS: the same, with the JSON ``.index`` of ECMWF (one line per field, offset and length)."""

    def _url(self, run: datetime, lead: int) -> str:
        return f"{ECMWF_BUCKET}/{run:%Y%m%d}/{run:%H}z/ifs/0p25/oper/{run:%Y%m%d%H}0000-{lead}h-oper-fc"

    def lead(self, run: datetime, lead: int) -> list[Message]:
        path = self.cache / f"{run:%Y%m%d%H}" / f"f{lead:03d}.npz"
        if path.exists():
            return _load(path)
        base = self._url(run, lead)
        messages: list[Message] = []
        for line in self._get(base + ".index").decode("ascii").splitlines():
            if not line.strip():
                continue
            field = json.loads(line)
            if field.get("levtype") != "sfc" or field.get("param") not in ECMWF_PARAMS:
                continue
            start, length = int(field["_offset"]), int(field["_length"])
            messages.extend(decode(self._get(base + ".grib2", {"Range": f"bytes={start}-{start + length - 1}"}), TURKEY))
        _save(path, messages)
        return messages


def _save(path: Path, messages: list[Message]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, Any] = {}
    meta = []
    for k, m in enumerate(messages):
        arrays[f"v{k}"] = m.values.astype(np.float32)
        meta.append([m.short_name, m.step_type, m.start_h, m.end_h])
    if messages:
        arrays["lats"], arrays["lons"] = messages[0].lats, messages[0].lons
    arrays["meta"] = np.array(json.dumps(meta))
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, **arrays)
    tmp.replace(path)


def _load(path: Path) -> list[Message]:
    with np.load(path) as data:
        meta = json.loads(str(data["meta"]))
        return [
            Message(name, step_type, int(start), int(end), data["lats"], data["lons"], data[f"v{k}"].astype(np.float64))
            for k, (name, step_type, start, end) in enumerate(meta)
        ]


def run_province_days(archive: Archive, run: datetime, last_lead: int) -> dict[date, dict[str, dict[str, Optional[float]]]]:
    """``local day -> province code -> phenomenon -> value`` for one run, read up to ``last_lead`` hours."""
    leads = list(range(3, last_lead + 1, 3))
    with ThreadPoolExecutor(max_workers=6) as pool:
        by_lead = dict(zip(leads, pool.map(lambda lead: archive.lead(run, lead), leads), strict=True))
    days = run_days(run, by_lead)
    first = by_lead[3][0]
    provinces = provinces_on_grid(first.lats, first.lons)
    return {
        day: {p.code: province_quantities(values, p) for p in provinces}
        for day, values in days.items()
    }


# ---------------------------------------------------------------- the data sets


def read_episodes(path: Path = EPISODES) -> list[dict[str, Any]]:
    episodes = []
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            start, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
            days = [start + timedelta(days=k) for k in range((end - start).days + 1)]
            episodes.append({**row, "days": days, "provinces": row["provinces"].split(";"), "phenomena": row["phenomena"].split(";")})
    return episodes


def values(args: argparse.Namespace) -> int:
    archive = EcmwfArchive(args.cache_dir / "ecmwf") if args.model == "ecmwf" else Archive(args.cache_dir)
    first_day = ECMWF_FIRST_DAY if args.model == "ecmwf" else date(LEARNING_YEARS[0], 1, 1)
    episodes = read_episodes()
    runs: dict[datetime, int] = {}  # run -> last lead needed

    def need(run_day: date, target: date) -> None:
        run = run_time(run_day, 0)
        lead = int(((datetime(target.year, target.month, target.day, 21, tzinfo=timezone.utc)) - run).total_seconds() // 3600)
        runs[run] = max(runs.get(run, 0), lead)

    for episode in episodes:
        for day in episode["days"]:
            if day - timedelta(days=1) >= first_day:
                need(day, day)
                need(day - timedelta(days=1), day)
    sample: list[date] = []
    day = first_day
    while day.year in LEARNING_YEARS:
        sample.append(day)
        need(day, day)
        day += timedelta(days=args.sample_every)

    out: dict[str, Any] = {
        "model": args.model,
        "runs": {},
        "sample_days": [d.isoformat() for d in sample],
        "made": datetime.now(timezone.utc).isoformat(),
    }
    for k, (run, last_lead) in enumerate(sorted(runs.items()), start=1):
        try:
            result = run_province_days(archive, run, last_lead)
        except (httpx.HTTPError, ValueError) as error:
            print(f"[{k}/{len(runs)}] {run:%Y-%m-%d %H} UTC: not read ({error})", file=sys.stderr)
            continue
        out["runs"][f"{run:%Y%m%d%H}"] = {d.isoformat(): v for d, v in result.items()}
        print(f"[{k}/{len(runs)}] {run:%Y-%m-%d %H} UTC up to f{last_lead:03d}: {archive.bytes_read / 1e6:.0f} MB read so far", file=sys.stderr)
    with gzip.open(args.out, "wt", encoding="utf-8") as handle:
        json.dump(out, handle)
    print(f"wrote {args.out} ({len(out['runs'])} runs of {len(runs)})")
    return 0


# ---------------------------------------------------------------- scoring


def _value(data: dict[str, Any], run_day: date, day: date, code: str, phenomenon: str) -> Optional[float]:
    run = data["runs"].get(f"{run_day:%Y%m%d}00")
    if run is None:
        return None
    return (run.get(day.isoformat()) or {}).get(code, {}).get(phenomenon)


def pairs(data: dict[str, Any], years: Iterable[int], lead_days: int, include_disputed: bool = True) -> list[dict[str, Any]]:
    """One row per (episode, province): the strongest value of each of its phenomena over its days, at the given lead."""
    rows = []
    for episode in read_episodes():
        if episode["flag"].startswith("revision_of") or episode["days"][0].year not in years:
            continue
        if not include_disputed and episode["flag"] == "disputed":
            continue
        for code in episode["provinces"]:
            best: dict[str, Optional[float]] = {}
            for phenomenon in episode["phenomena"]:
                found = [
                    _value(data, day - timedelta(days=lead_days), day, code, phenomenon) for day in episode["days"]
                ]
                found = [v for v in found if v is not None]
                if found:
                    best[phenomenon] = min(found) if phenomenon == "cold" else max(found)
            rows.append({"episode": episode["id"], "province": code, "values": best})
    return rows


def _level(phenomenon: str, value: float, table: dict[str, Any]) -> int:
    base = THRESHOLDS[phenomenon]
    return Threshold(base.quantity, tuple(table[phenomenon]), base.direction).level_of(value) or 0


def hit_rate(rows: list[dict[str, Any]], table: dict[str, Any], level: int = 2) -> tuple[int, int]:
    """(hits, pairs with a value) for a threshold table ``phenomenon -> (l1, l2, l3)``."""
    hits = scored = 0
    for row in rows:
        if not row["values"]:
            continue
        scored += 1
        if any(_level(name, value, table) >= level for name, value in row["values"].items()):
            hits += 1
    return hits, scored


def ordinary_values(data: dict[str, Any], phenomenon: str) -> np.ndarray:
    found = []
    for day in data["sample_days"]:
        d = date.fromisoformat(day)
        for code in (data["runs"].get(f"{d:%Y%m%d}00") or {}).get(day, {}):
            value = _value(data, d, d, code, phenomenon)
            if value is not None:
                found.append(value)
    return np.array(found)


def choose(args: argparse.Namespace) -> int:
    with gzip.open(args.values, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    learning_j0 = pairs(data, LEARNING_YEARS, 0)
    learning_j1 = pairs(data, LEARNING_YEARS, 1)
    print(f"learning set: {len(learning_j0)} pairs; ordinary days: {len(data['sample_days'])}")
    for phenomenon in PHENOMENA:
        ordinary = ordinary_values(data, phenomenon)
        at_pairs = [r["values"][phenomenon] for r in learning_j0 if phenomenon in r["values"]]
        at_pairs_j1 = [r["values"][phenomenon] for r in learning_j1 if phenomenon in r["values"]]
        print(f"\n== {phenomenon} ({THRESHOLDS[phenomenon].quantity}): {len(at_pairs)} learning pairs, {ordinary.size} ordinary province-days")
        if ordinary.size == 0:
            continue
        sign = -1.0 if THRESHOLDS[phenomenon].direction == "below" else 1.0
        if at_pairs:
            print("   episode pairs J0 quantiles 10/25/50/75/90:", np.round(np.quantile(at_pairs, [0.1, 0.25, 0.5, 0.75, 0.9]), 1).tolist())
        if at_pairs_j1:
            print("   episode pairs J1 quantiles 10/25/50/75/90:", np.round(np.quantile(at_pairs_j1, [0.1, 0.25, 0.5, 0.75, 0.9]), 1).tolist())
        print("   ordinary days: value exceeded by 10 / 3 / 1 / 0.3 / 0.1 % of province-days:",
              np.round(sign * np.quantile(sign * ordinary, [0.9, 0.97, 0.99, 0.997, 0.999]), 1).tolist())
        print("   candidate level-2 threshold -> share of learning pairs reached (J0, J1), share of ordinary province-days reached:")
        for q in (0.9, 0.95, 0.97, 0.98, 0.99, 0.995, 0.997):
            limit = float(sign * np.quantile(sign * ordinary, q))
            share_j0 = np.mean([sign * v >= sign * limit for v in at_pairs]) if at_pairs else float("nan")
            share_j1 = np.mean([sign * v >= sign * limit for v in at_pairs_j1]) if at_pairs_j1 else float("nan")
            print(f"     {limit:8.1f}: pairs J0 {share_j0:5.0%}  J1 {share_j1:5.0%}  ordinary {1 - q:6.1%}")
    return 0


def score(args: argparse.Namespace) -> int:
    if args.set == "test" and not args.i_have_frozen_the_thresholds:
        print("the test set is scored once, with frozen thresholds: add --i-have-frozen-the-thresholds", file=sys.stderr)
        return 2
    with gzip.open(args.values, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    years = LEARNING_YEARS if args.set == "learning" else TEST_YEARS
    table = {name: THRESHOLDS[name].levels for name in PHENOMENA}
    for include_disputed in (True, False):
        print(f"\n{args.set} set, {'with' if include_disputed else 'without'} the disputed episodes")
        for lead in (0, 1):
            rows = pairs(data, years, lead, include_disputed)
            hits, scored = hit_rate(rows, table)
            print(f"  J{lead}: {hits}/{scored} pairs at level 2 or more = {hits / scored:.0%}" if scored else f"  J{lead}: no pair")
            for phenomenon in PHENOMENA:
                sub = [r for r in rows if phenomenon in r["values"]]
                if sub:
                    h, s = hit_rate([{**r, "values": {phenomenon: r["values"][phenomenon]}} for r in sub], table)
                    print(f"     {phenomenon:5s}: {h}/{s} = {h / s:.0%}{'' if s >= 10 else ' (under 10 pairs: reported, not judged)'}")
    if args.set == "learning":
        print("\nordinary days of the learning years (J0), share of province-days at each level:")
        for phenomenon in PHENOMENA:
            ordinary = ordinary_values(data, phenomenon)
            if ordinary.size:
                levels = [THRESHOLDS[phenomenon].level_of(v) or 0 for v in ordinary]
                print(f"  {phenomenon:5s}: " + "  ".join(f"L{k} {np.mean([x >= k for x in levels]):.2%}" for k in (1, 2, 3)))
    return 0


# ---------------------------------------------------------------- thresholds per province


# Ours, not the MGM's: the least a province's percentile may be, so that a dry province does not warn for a shower (levels 1, 2, 3).
FLOORS: dict[str, tuple[float, float, float]] = {"rain": (10.0, 20.0, 50.0), "snow": (2.0, 5.0, 15.0), "wind": (40.0, 55.0, 80.0)}
# Heat and cold have no episode to learn from: one national threshold, rare, from the ordinary days (share of province-days).
RARE_SHARE = (0.01, 0.003, 0.0005)


def _percentiles_for(p2: float) -> tuple[float, float, float]:
    """The percentiles of levels 1, 2 and 3 for a level-2 percentile ``p2``: level 1 three times as frequent, level 3 five times rarer."""
    return (100.0 - 3.0 * (100.0 - p2), p2, 100.0 - (100.0 - p2) / 5.0)


def daily_values(data: dict[str, Any], years: Iterable[int]) -> dict[str, dict[str, list[float]]]:
    """``province -> phenomenon -> values`` of the ordinary days of ``years`` (J0)."""
    out: dict[str, dict[str, list[float]]] = {}
    wanted = set(years)
    for day in data["sample_days"]:
        d = date.fromisoformat(day)
        if d.year not in wanted:
            continue
        for code, quantities in ((data["runs"].get(f"{d:%Y%m%d}00") or {}).get(day) or {}).items():
            for name, value in quantities.items():
                if value is not None:
                    out.setdefault(code, {}).setdefault(name, []).append(value)
    return out


def province_table(data: dict[str, Any], years: Iterable[int], p2: float) -> dict[str, dict[str, tuple[float, float, float]]]:
    """``province -> phenomenon -> (level 1, 2, 3)``: percentiles of the province's own days with the floors (rain, snow, wind), one
    rare national value for heat and cold."""
    values = daily_values(data, years)
    table: dict[str, dict[str, tuple[float, float, float]]] = {}
    pooled = {name: np.array([v for per in values.values() for v in per.get(name, [])]) for name in ("heat", "cold")}
    national = {
        "heat": tuple(float(np.quantile(pooled["heat"], 1.0 - share)) for share in RARE_SHARE),
        "cold": tuple(float(np.quantile(pooled["cold"], share)) for share in RARE_SHARE),
    }
    for code, per in values.items():
        row: dict[str, tuple[float, float, float]] = {}
        for name, floors in FLOORS.items():
            found = per.get(name)
            if not found:
                continue
            row[name] = tuple(  # type: ignore[assignment]
                max(float(np.percentile(found, q)), floor) for q, floor in zip(_percentiles_for(p2), floors, strict=True)
            )
        row.update(national)  # type: ignore[arg-type]
        table[code] = row
    return table


def _level_with(table: dict[str, dict[str, tuple[float, float, float]]], code: str, name: str, value: float) -> int:
    limits = table.get(code, {}).get(name)
    if limits is None:
        return 0
    return Threshold("", limits, "below" if name == "cold" else "above").level_of(value) or 0


def provinces(args: argparse.Namespace) -> int:
    """Cross-validation by year on the learning set: thresholds of one year judged on the other (detection and over-alerting)."""
    with gzip.open(args.values, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    years = list(LEARNING_YEARS)
    print(f"ordinary days: {len(data['sample_days'])}; learning pairs: {len(pairs(data, years, 0))}")
    print("p2      J0 pairs   J1 pairs   province-days L>=2   L3     (thresholds of one year, judged on the other)")
    for p2 in args.percentiles:
        hits = {0: [0, 0], 1: [0, 0]}
        over = [0, 0, 0]  # level >= 2, level 3, province-days
        by_name: dict[str, int] = {}
        for year in years:
            other = [y for y in years if y != year]
            table = province_table(data, other, p2)
            for lead in (0, 1):
                for row in pairs(data, [year], lead):
                    if not row["values"]:
                        continue
                    hits[lead][1] += 1
                    if any(_level_with(table, row["province"], n, v) >= 2 for n, v in row["values"].items()):
                        hits[lead][0] += 1
            for code, per in daily_values(data, [year]).items():
                count = min(len(v) for v in per.values())
                for k in range(count):
                    levels = [_level_with(table, code, n, per[n][k]) for n in per]
                    for n, level in zip(per, levels, strict=True):
                        by_name[n] = by_name.get(n, 0) + (level >= 2)
                    over[2] += 1
                    over[0] += max(levels) >= 2
                    over[1] += max(levels) >= 3
        print(f"{p2:5.1f}   {hits[0][0]:3d}/{hits[0][1]} {hits[0][0] / hits[0][1]:4.0%}   {hits[1][0]:3d}/{hits[1][1]} {hits[1][0] / hits[1][1]:4.0%}"
              f"      {over[0] / over[2]:6.2%}          {over[1] / over[2]:6.2%}   "
              + " ".join(f"{n} {c / over[2]:.2%}" for n, c in sorted(by_name.items())))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest of the unofficial pre-alert for Turkey on past GFS runs (ADR 0011).")
    sub = parser.add_subparsers(dest="command", required=True)
    p_values = sub.add_parser("values", help="read the runs and write the province values")
    p_values.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "vigie-prealert-tr")
    p_values.add_argument("--out", type=Path, default=Path("prealert-tr-values.json.gz"))
    p_values.add_argument("--sample-every", type=int, default=7)
    p_values.add_argument("--model", choices=("gfs", "ecmwf"), default="gfs")
    p_choose = sub.add_parser("choose", help="what the learning set says of the thresholds")
    p_choose.add_argument("--values", type=Path, required=True)
    p_prov = sub.add_parser("provinces", help="thresholds per province: cross-validation by year on the learning set")
    p_prov.add_argument("--values", type=Path, required=True)
    p_prov.add_argument("--percentiles", type=float, nargs="+", default=[95.0, 97.0, 98.0, 98.5, 99.0, 99.5])
    p_score = sub.add_parser("score", help="score the thresholds of app/prealert/levels.py")
    p_score.add_argument("--values", type=Path, required=True)
    p_score.add_argument("--set", choices=("learning", "test"), default="learning")
    p_score.add_argument("--i-have-frozen-the-thresholds", action="store_true")
    args = parser.parse_args(argv)
    return {"values": values, "choose": choose, "provinces": provinces, "score": score}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
