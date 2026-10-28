"""Measure the aurora estimate of Vigie (ADR 0012) on past nights, against what people reported they saw or did not see.

The estimate (``app/services/aurora_method.py``) is ours: before a route serves it, it is measured here. The reference is the
citizen-science database of Aurorasaurus, "Aurorasaurus Web Observations (2014-2025)" (MacDonald et al. 2026, Zenodo,
https://doi.org/10.5281/zenodo.16783265, CC BY-NC 4.0): 22,280 reports, each a place, a time span and ``see_aurora`` true or
false. The planetary Kp index of each report's 3-hour interval comes from GFZ Potsdam (Matzka et al. 2021,
https://doi.org/10.5880/Kp.0001, CC BY 4.0). Neither file is copied into the repository: they are downloaded into the cache
folder (the temporary folder by default) and only figures computed from them are written.

Each step can be restarted and none asks twice for what it already has::

    python tools/measure_aurora.py fetch      # the two files (8 MB and 17 MB), once
    python tools/measure_aurora.py prepare    # per report: magnetic latitude, Sun, Moon, Kp (a few minutes, cached)
    python tools/measure_aurora.py fit        # the two settings of the edge, on the reports of 2014 to 2022 ONLY
    python tools/measure_aurora.py score      # the criterion of ADR 0012 on the reports of 2023 to 2025 (--markdown, --json)

**What is measured.** The chance without the cloud factor: a report says whether its author saw an aurora, not how cloudy it was,
and the clouds forecast at that hour are not part of this measure. The moon and darkness factors are in. The Kp is the one GFZ
published afterwards, so this is the estimate with a perfect Kp forecast: what SWPC's own forecast adds as error is not measured.

**The two sets never mix.** ``fit`` reads the reports made before 2023-01-01 and nothing else; ``score`` reads the reports from
2023-01-01 (the storms of May and October 2024 are there). The criterion was written in the ADR before ``score`` was first run.
The statistics are pure functions, run with no network by ``tests/test_measure_aurora.py``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services import aurora_method as method  # noqa: E402

AURORASAURUS_URL = (
    "https://zenodo.org/api/records/16783265/files/web_observations_2014-08-01_to_2025-08-02_cleaned.csv/content"
)
GFZ_KP_URL = "https://kp.gfz.de/app/files/Kp_ap_since_1932.txt"
USER_AGENT = "vigie-api measure_aurora (personal project)"

SPLIT_UTC = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp())  # fit before, score from
POSSIBLE = 0.2  # the floor of "possible" (method.LEVELS)
DARK_FOR_NIGHTS = -12.0  # the reports counted on the named nights: Sun at -12 degrees or lower
CALIBRATION_BINS = (0.0, 0.05, 0.2, 0.5, 1.0000001)
MIN_REPORTS_PER_BIN = 30
EUROPE = (-25.0, 34.0, 45.0, 72.0)  # west, south, east, north

# The named nights of ADR 0012, as UTC windows from noon to noon (the night of Europe, then of the Americas).
NIGHTS: dict[str, tuple[str, str]] = {
    "2024-05-10/11": ("2024-05-10T12:00:00+00:00", "2024-05-11T12:00:00+00:00"),
    "2024-05-11/12": ("2024-05-11T12:00:00+00:00", "2024-05-12T12:00:00+00:00"),
    "2024-10-10/11": ("2024-10-10T12:00:00+00:00", "2024-10-11T12:00:00+00:00"),
    "2025-01-01/02": ("2025-01-01T12:00:00+00:00", "2025-01-02T12:00:00+00:00"),
    "2025-06-01/02": ("2025-06-01T12:00:00+00:00", "2025-06-02T12:00:00+00:00"),
}
CRITERION_NIGHTS = ("2024-05-10/11", "2024-10-10/11")


@dataclass(frozen=True)
class Report:
    """One report, prepared: everything the estimate needs, computed once."""

    obs_id: str
    when_utc: int  # the middle of the time span of the report
    lat: float
    lon: float
    seen: bool
    clear_sky: bool  # the author ticked "clear": the form asks it only of those who saw no aurora
    kp: float
    magnetic_lat: float
    sun_altitude: float
    moon_altitude: float
    moon_fraction: float


# ---------------------------------------------------------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------------------------------------------------------


def cache_dir(arg: Optional[str]) -> Path:
    folder = Path(arg) if arg else Path(tempfile.gettempdir()) / "vigie-measure-aurora"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def fetch(folder: Path) -> None:
    import httpx

    for url, name in ((AURORASAURUS_URL, "aurorasaurus.csv"), (GFZ_KP_URL, "kp_gfz.txt")):
        target = folder / name
        if target.exists() and target.stat().st_size > 0:
            print(f"{name}: already here ({target.stat().st_size} bytes)")
            continue
        with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=120.0) as client:
            response = client.get(url)
            response.raise_for_status()
            target.write_bytes(response.content)
        print(f"{name}: {target.stat().st_size} bytes from {url}")


def read_kp(lines: Iterable[str]) -> dict[int, float]:
    """The GFZ file: the start of each 3-hour interval (UTC epoch) -> Kp. Missing values (-1) are left out."""
    table: dict[int, float] = {}
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        kp = float(parts[7])
        if kp < 0:
            continue
        start = datetime(int(parts[0]), int(parts[1]), int(parts[2]), int(float(parts[3])), tzinfo=timezone.utc)
        table[int(start.timestamp())] = kp
    return table


def kp_at(table: dict[int, float], when_utc: float) -> Optional[float]:
    return table.get(int(when_utc // 10800 * 10800))


def _when(row: dict[str, str]) -> Optional[int]:
    try:
        start = datetime.fromisoformat(row["time_start"]).timestamp()
        end = datetime.fromisoformat(row["time_end"]).timestamp() if row.get("time_end") else start
    except (KeyError, ValueError):
        return None
    if end < start or end - start > 6 * 3600:  # a span longer than two Kp intervals says nothing about one hour
        return None
    return int((start + end) / 2)


def prepare(folder: Path) -> list[Report]:
    """Read the reports and compute, once, what the estimate needs; cached in ``prepared.csv``."""
    cached = folder / "prepared-apex.csv"
    if cached.exists():
        return load_prepared(cached)
    kp_table = read_kp((folder / "kp_gfz.txt").read_text(encoding="utf-8").splitlines())
    reports: list[Report] = []
    skipped = 0
    with (folder / "aurorasaurus.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            when = _when(row)
            try:
                lat, lon = float(row["st_y"]), float(row["st_x"])
            except (KeyError, ValueError):
                lat = lon = math.nan
            kp = None if when is None else kp_at(kp_table, when)
            if when is None or kp is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or row.get("see_aurora") not in ("True", "False"):
                skipped += 1
                continue
            sun, moon, fraction = method.sun_and_moon(lat, lon, when)
            reports.append(
                Report(
                    obs_id=row["obs_id"],
                    when_utc=when,
                    lat=lat,
                    lon=lon,
                    seen=row["see_aurora"] == "True",
                    clear_sky=row.get("sky_id") == "clea",
                    kp=kp,
                    magnetic_lat=method.magnetic_latitude(lat, lon),
                    sun_altitude=sun,
                    moon_altitude=moon,
                    moon_fraction=fraction,
                )
            )
    print(f"prepared {len(reports)} reports, skipped {skipped} (no time, no Kp, no position or no answer)")
    with cached.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Report.__dataclass_fields__))
        writer.writeheader()
        for report in reports:
            writer.writerow(asdict(report))
    return reports


def load_prepared(path: Path) -> list[Report]:
    reports: list[Report] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            reports.append(
                Report(
                    obs_id=row["obs_id"],
                    when_utc=int(row["when_utc"]),
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    seen=row["seen"] == "True",
                    clear_sky=row["clear_sky"] == "True",
                    kp=float(row["kp"]),
                    magnetic_lat=float(row["magnetic_lat"]),
                    sun_altitude=float(row["sun_altitude"]),
                    moon_altitude=float(row["moon_altitude"]),
                    moon_fraction=float(row["moon_fraction"]),
                )
            )
    return reports


# ---------------------------------------------------------------------------------------------------------------------------
# The estimate and its statistics (pure functions)
# ---------------------------------------------------------------------------------------------------------------------------

Scorer = Callable[[Report], float]


def vigie_score(edge_at_kp0: float = method.EDGE_AT_KP0, edge_per_kp: float = method.EDGE_PER_KP, moon: bool = True) -> Scorer:
    """The chance of ADR 0012 without the clouds, for these settings of the edge."""

    def score(report: Report) -> float:
        value = method.activity(report.magnetic_lat, report.kp, edge_at_kp0, edge_per_kp) * method.darkness(report.sun_altitude)
        return value * method.moon_factor(report.moon_altitude, report.moon_fraction) if moon else value

    return score


def swpc_scale_score(report: Report) -> float:
    """The simplest sourced alternative: the latitudes of the NOAA G scale ("aurora has been seen as low as ... typically 55, 50,
    45, 40 degrees geomagnetic latitude" at Kp 6, 7, 8, 9), the line extended by 5 degrees a Kp unit, reached 5 degrees poleward of it."""
    lowest = 85.0 - 5.0 * min(9.0, max(0.0, report.kp))
    value = max(0.0, min(1.0, (abs(report.magnetic_lat) - lowest) / 5.0)) * method.darkness(report.sun_altitude)
    return value * method.moon_factor(report.moon_altitude, report.moon_fraction)


def kp_only_score(report: Report) -> float:
    """Kp alone (no place, no hour): what any ranking must beat."""
    return report.kp / 9.0


def auc(scores: Sequence[float], labels: Sequence[bool]) -> Optional[float]:
    """Area under the ROC curve (the Mann-Whitney statistic, ties counted half); ``None`` without both classes."""
    pairs = sorted(zip(scores, labels, strict=True), key=lambda pair: pair[0])
    positives = sum(1 for _, label in pairs if label)
    negatives = len(pairs) - positives
    if positives == 0 or negatives == 0:
        return None
    rank_sum = 0.0
    index = 0
    while index < len(pairs):
        end = index
        while end + 1 < len(pairs) and pairs[end + 1][0] == pairs[index][0]:
            end += 1
        mean_rank = (index + end) / 2 + 1
        rank_sum += mean_rank * sum(1 for k in range(index, end + 1) if pairs[k][1])
        index = end + 1
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def log_likelihood(scores: Sequence[float], labels: Sequence[bool], floor: float = 0.02) -> float:
    """Mean Bernoulli log-likelihood, each chance kept within [floor, 1 - floor] (the estimate says 0 and 1)."""
    total = 0.0
    for score, label in zip(scores, labels, strict=True):
        p = min(1.0 - floor, max(floor, score))
        total += math.log(p if label else 1.0 - p)
    return total / len(scores)


def brier(scores: Sequence[float], labels: Sequence[bool]) -> float:
    return sum((score - (1.0 if label else 0.0)) ** 2 for score, label in zip(scores, labels, strict=True)) / len(scores)


def calibration(scores: Sequence[float], labels: Sequence[bool]) -> list[dict[str, Any]]:
    """Per bin of chance: the reports, the share seen and the mean chance."""
    rows: list[dict[str, Any]] = []
    for low, high in zip(CALIBRATION_BINS, CALIBRATION_BINS[1:], strict=False):
        inside = [(s, label) for s, label in zip(scores, labels, strict=True) if low <= s < high]
        rows.append(
            {
                "from": low,
                "to": min(1.0, high),
                "reports": len(inside),
                "seen_share": (sum(1 for _, label in inside if label) / len(inside)) if inside else None,
                "mean_chance": (sum(s for s, _ in inside) / len(inside)) if inside else None,
            }
        )
    return rows


def calibration_increases(rows: Sequence[dict[str, Any]]) -> bool:
    """The share seen grows from bin to bin (bins with fewer than ``MIN_REPORTS_PER_BIN`` reports are left out)."""
    shares = [row["seen_share"] for row in rows if row["reports"] >= MIN_REPORTS_PER_BIN]
    return all(b > a for a, b in zip(shares, shares[1:], strict=False))


def summary(reports: Sequence[Report], scorer: Scorer) -> dict[str, Any]:
    scores = [scorer(report) for report in reports]
    labels = [report.seen for report in reports]
    seen = sum(labels)
    return {
        "reports": len(reports),
        "seen": seen,
        "not_seen": len(reports) - seen,
        "auc": auc(scores, labels),
        "log_likelihood": log_likelihood(scores, labels) if reports else None,
        "brier": brier(scores, labels) if reports else None,
        "seen_possible_share": (sum(1 for s, label in zip(scores, labels, strict=True) if label and s >= POSSIBLE) / seen) if seen else None,
        "not_seen_possible_share": (
            sum(1 for s, label in zip(scores, labels, strict=True) if not label and s >= POSSIBLE) / (len(reports) - seen)
            if len(reports) > seen
            else None
        ),
        "calibration": calibration(scores, labels),
    }


def in_europe(report: Report) -> bool:
    west, south, east, north = EUROPE
    return west <= report.lon <= east and south <= report.lat <= north


def in_night(report: Report, night: str) -> bool:
    start, end = (datetime.fromisoformat(value).timestamp() for value in NIGHTS[night])
    return start <= report.when_utc < end


def fit(reports: Sequence[Report]) -> dict[str, Any]:
    """The edge settings with the best mean log-likelihood on ``reports`` (a grid: 55 to 75 degrees, 0.5 to 5 degrees a Kp unit)."""
    labels = [report.seen for report in reports]
    best: Optional[tuple[float, float, float]] = None
    for e10 in range(550, 751, 5):
        for s10 in range(5, 51):
            edge_at_kp0, edge_per_kp = e10 / 10, s10 / 10
            scorer = vigie_score(edge_at_kp0, edge_per_kp)
            value = log_likelihood([scorer(report) for report in reports], labels)
            if best is None or value > best[0]:
                best = (value, edge_at_kp0, edge_per_kp)
    assert best is not None
    return {"log_likelihood": best[0], "edge_at_kp0": best[1], "edge_per_kp": best[2], "reports": len(reports)}


def criterion(test: Sequence[Report], scorer: Scorer) -> dict[str, Any]:
    """The four points of ADR 0012, each with its figure and whether it holds."""
    overall = summary(test, scorer)
    baseline = summary(test, swpc_scale_score)
    nights: dict[str, Any] = {}
    for night in CRITERION_NIGHTS:
        for region, keep in (("world", lambda r: True), ("europe", in_europe)):
            dark_seen = [r for r in test if in_night(r, night) and keep(r) and r.seen and r.sun_altitude <= DARK_FOR_NIGHTS]
            share = (sum(1 for r in dark_seen if scorer(r) >= POSSIBLE) / len(dark_seen)) if dark_seen else None
            nights[f"{night} {region}"] = {"dark_seen_reports": len(dark_seen), "possible_share": share, "holds": share is not None and share >= 0.7}
    points = {
        "1 auc >= 0.75": {"value": overall["auc"], "holds": overall["auc"] is not None and overall["auc"] >= 0.75},
        "2 calibration increases": {"value": [row["seen_share"] for row in overall["calibration"]], "holds": calibration_increases(overall["calibration"])},
        "3 named nights, seen in the dark, >= 70 % possible": {"value": nights, "holds": all(n["holds"] for n in nights.values())},
        "4 better than the G-scale line": {
            "value": {"vigie": overall["log_likelihood"], "g_scale": baseline["log_likelihood"]},
            "holds": overall["log_likelihood"] > baseline["log_likelihood"],
        },
    }
    return {"points": points, "holds": all(point["holds"] for point in points.values())}


# ---------------------------------------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------------------------------------


def score_all(reports: Sequence[Report]) -> dict[str, Any]:
    train = [r for r in reports if r.when_utc < SPLIT_UTC]
    test = [r for r in reports if r.when_utc >= SPLIT_UTC]
    current = vigie_score()
    result: dict[str, Any] = {
        "settings": {"edge_at_kp0": method.EDGE_AT_KP0, "edge_per_kp": method.EDGE_PER_KP, "reach_km": method.REACH_KM, "moon_weight": method.MOON_WEIGHT},
        "train": summary(train, current),
        "test": summary(test, current),
        "test_europe": summary([r for r in test if in_europe(r)], current),
        "test_clear_sky": summary([r for r in test if r.clear_sky], current),
        "test_without_moon": summary(test, vigie_score(moon=False)),
        "test_g_scale": summary(test, swpc_scale_score),
        "test_kp_only": summary(test, kp_only_score),
        "nights": {
            night: {
                "world": summary([r for r in test if in_night(r, night)], current),
                "europe": summary([r for r in test if in_night(r, night) and in_europe(r)], current),
            }
            for night in NIGHTS
        },
        "criterion": criterion(test, current),
    }
    return result


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{100 * value:.0f} %"


def _num(value: Optional[float], digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    settings = result["settings"]
    lines.append(
        f"Settings: edge at Kp 0 = {settings['edge_at_kp0']} degrees, {settings['edge_per_kp']} degrees a Kp unit, reach "
        f"{settings['reach_km']:.0f} km, moon weight {settings['moon_weight']}."
    )
    lines.append("")
    lines.append("| Set | Reports | Seen | Not seen | AUC | Log-likelihood | Brier | Seen with >= 20 % | Not seen with >= 20 % |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for key, label in (
        ("train", "2014-2022 (fit)"),
        ("test", "2023-2025 (test)"),
        ("test_europe", "2023-2025, Europe"),
        ("test_clear_sky", "2023-2025, not seen under a clear sky (the only ones that say)"),
        ("test_without_moon", "2023-2025, without the Moon factor"),
        ("test_g_scale", "2023-2025, G-scale line instead"),
        ("test_kp_only", "2023-2025, Kp alone"),
    ):
        s = result[key]
        lines.append(
            f"| {label} | {s['reports']} | {s['seen']} | {s['not_seen']} | {_num(s['auc'])} | {_num(s['log_likelihood'])} | "
            f"{_num(s['brier'])} | {_pct(s['seen_possible_share'])} | {_pct(s['not_seen_possible_share'])} |"
        )
    lines.append("")
    lines.append("Calibration on 2023-2025 (share of the reports that saw an aurora, by chance):")
    lines.append("")
    lines.append("| Chance | Reports | Mean chance | Share seen |")
    lines.append("| --- | ---: | ---: | ---: |")
    for row in result["test"]["calibration"]:
        lines.append(f"| {row['from']:.2f} to {row['to']:.2f} | {row['reports']} | {_pct(row['mean_chance'])} | {_pct(row['seen_share'])} |")
    lines.append("")
    lines.append("Named nights (UTC noon to noon):")
    lines.append("")
    lines.append("| Night | Region | Reports | Seen | AUC | Seen with >= 20 % | Not seen with >= 20 % |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for night, regions in result["nights"].items():
        for region, s in regions.items():
            lines.append(
                f"| {night} | {region} | {s['reports']} | {s['seen']} | {_num(s['auc'])} | {_pct(s['seen_possible_share'])} | "
                f"{_pct(s['not_seen_possible_share'])} |"
            )
    lines.append("")
    lines.append("Criterion of ADR 0012:")
    lines.append("")
    for name, point in result["criterion"]["points"].items():
        lines.append(f"- {name}: **{'holds' if point['holds'] else 'FAILS'}** ({json.dumps(point['value'], default=str)})")
    lines.append(f"- Overall: **{'holds' if result['criterion']['holds'] else 'FAILS'}**")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("step", choices=["fetch", "prepare", "fit", "score"])
    parser.add_argument("--cache", help="folder of the downloaded files and the prepared reports (default: the temporary folder)")
    parser.add_argument("--markdown", action="store_true", help="score: print the tables of the report")
    parser.add_argument("--json", help="score: write the figures to this file")
    args = parser.parse_args(argv)
    folder = cache_dir(args.cache)
    if args.step == "fetch":
        fetch(folder)
        return 0
    reports = prepare(folder)
    if args.step == "prepare":
        return 0
    if args.step == "fit":
        train = [r for r in reports if r.when_utc < SPLIT_UTC]
        print(json.dumps(fit(train), indent=2))
        return 0
    result = score_all(reports)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    print(markdown(result) if args.markdown else json.dumps(result["criterion"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
