"""The statistics of ``tools/measure_aurora.py`` (ADR 0013): pure functions, no network, no download."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import measure_aurora as tool  # noqa: E402


def report(seen: bool, magnetic_lat: float, kp: float, sun: float = -30.0, moon: float = -10.0, when: int = 1_700_000_000) -> tool.Report:
    return tool.Report("x", when, 50.0, 0.0, seen, False, kp, magnetic_lat, sun, moon, 0.5)


def test_auc_counts_pairs_and_ties() -> None:
    assert tool.auc([0.1, 0.9], [False, True]) == 1.0
    assert tool.auc([0.9, 0.1], [False, True]) == 0.0
    assert tool.auc([0.5, 0.5], [False, True]) == 0.5
    assert tool.auc([0.3, 0.3], [True, True]) is None


def test_log_likelihood_keeps_certainty_finite() -> None:
    value = tool.log_likelihood([0.0, 1.0], [True, False])
    assert math.isfinite(value) and value == pytest.approx(math.log(0.02))


def test_calibration_must_grow_over_the_bins_with_enough_reports() -> None:
    rows = [{"reports": 40, "seen_share": 0.2}, {"reports": 40, "seen_share": 0.5}, {"reports": 5, "seen_share": 0.1}, {"reports": 40, "seen_share": 0.9}]
    assert tool.calibration_increases(rows)
    rows[1]["seen_share"] = 0.1
    assert not tool.calibration_increases(rows)


def test_the_estimate_of_a_report_is_the_method_of_the_api() -> None:
    under = report(True, 60.0, 5.0)  # the edge is at 62.5 - 1.9 * 5 = 53 degrees: under the aurora
    far = report(False, 35.0, 1.0)
    daylight = report(True, 60.0, 5.0, sun=10.0)
    score = tool.vigie_score()
    assert score(under) == 1.0 and score(far) == 0.0 and score(daylight) == 0.0


def test_the_fit_finds_the_edge_that_separates_the_reports() -> None:
    # Seen from 60 degrees up, not seen from 58 down, all at Kp 2: the edge at Kp 2 lands just poleward of 59 (the chance
    # falls over the 9 degrees of the reach, so the fit puts the edge where the seen reports are mostly above one half).
    reports = [report(lat >= 60, float(lat), 2.0) for lat in range(40, 72, 2) for _ in range(3)]
    best = tool.fit(reports)
    assert 60.0 <= best["edge_at_kp0"] - 2.0 * best["edge_per_kp"] <= 66.0


def test_the_nights_and_the_split() -> None:
    may = report(True, 50.0, 9.0, when=int(tool.datetime.fromisoformat("2024-05-10T22:00:00+00:00").timestamp()))
    assert tool.in_night(may, "2024-05-10/11") and not tool.in_night(may, "2024-10-10/11")
    assert may.when_utc >= tool.SPLIT_UTC
