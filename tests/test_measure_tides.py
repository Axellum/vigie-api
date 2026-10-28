"""The measure of the tide (``tools/measure_tides.py``): the parts that are pure.

The answers of REFMAR and NOAA are read from real samples (``tests/fixtures/tides``); the scoring is checked on curves worked out by
hand. What is checked is the arithmetic of the criterion of ADR 0012, not the sea.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from app.services.tides import Extreme, find_extremes, mjd_from_unix
from tools import measure_tides as tool

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tides"


def _sample(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_refmar_answers_are_read_in_utc_metres() -> None:
    values = tool.parse_refmar(_sample("refmar-3-sample.json"))
    assert len(values) == 70
    first_mjd, first_value = values[0]
    assert first_mjd == mjd_from_unix(datetime(2026, 9, 27, tzinfo=timezone.utc).timestamp())
    assert 0.0 < first_value < 9.0  # metres above the chart datum of Brest


def test_noaa_answers_are_read() -> None:
    series = tool.parse_noaa_series(_sample("noaa-9414290-10-sample.json"))
    assert len(series) == 12 and series[1][0] - series[0][0] == pytest.approx(10 / 1440)
    extremes = tool.parse_noaa_extremes(_sample("noaa-9414290-hilo-sample.json"))
    assert [extreme.kind for extreme in extremes][:2] == ["low", "high"]


def test_the_smoothing_leaves_a_gap_where_too_few_values_exist() -> None:
    start = mjd_from_unix(tool.WINDOW_START.timestamp())
    values = [(start + minute / 1440.0, 1.0) for minute in range(300) if not 100 <= minute < 200]
    minutes, smoothed = tool.smooth_minutes(values)
    assert len(minutes) == 300
    assert np.isnan(smoothed[150]) and smoothed[50] == 1.0 and smoothed[250] == 1.0


def _tide(shift_minutes: float = 0.0, scale: float = 1.0) -> tuple[np.ndarray, np.ndarray, list[Extreme]]:
    grid = tool.window_grid()
    start, end = grid[0] - 1.0, grid[-1] + 1.0
    minutes = np.arange(start, end, 1 / 1440)

    def curve(times: np.ndarray) -> np.ndarray:
        return scale * 2.0 * np.cos(2 * np.pi * (times - shift_minutes / 1440) / 0.5175)

    return curve(grid), curve(minutes), find_extremes(minutes, curve(minutes))


def test_a_model_equal_to_the_reference_passes() -> None:
    grid_heights, _, extremes = _tide()
    reference = tool.Reference(tool.window_grid(), grid_heights, extremes)
    score = tool.score_station(tool.STATIONS[0], reference, grid_heights, extremes)
    assert score.status == "pass" and score.matched == score.significant > 100
    assert abs(score.mean_range_m - 4.0) < 0.01 and score.time_error_median_min < 1


def test_half_an_hour_late_fails_on_the_times_and_ten_percent_too_high_on_the_heights() -> None:
    grid_heights, _, extremes = _tide()
    reference = tool.Reference(tool.window_grid(), grid_heights, extremes)
    late_heights, _, late_extremes = _tide(shift_minutes=30)
    late = tool.score_station(tool.STATIONS[0], reference, late_heights, late_extremes)
    assert late.status == "fail" and "time of high and low waters" in late.reasons
    assert abs(late.time_bias_median_min - 30) < 1
    high_heights, _, high_extremes = _tide(scale=1.25)
    high = tool.score_station(tool.STATIONS[0], reference, high_heights, high_extremes)
    assert "height of high and low waters" in high.reasons and "time of high and low waters" not in high.reasons


def test_a_reference_with_too_few_values_is_not_measured() -> None:
    grid_heights, _, extremes = _tide()
    holed = grid_heights.copy()
    holed[: len(holed) // 2] = np.nan
    score = tool.score_station(tool.STATIONS[0], tool.Reference(tool.window_grid(), holed, extremes), grid_heights, extremes)
    assert score.status == "not measured"


def test_the_small_extremes_of_a_mixed_tide_are_not_significant() -> None:
    # A higher high water, then a small low and a small high (0.2 m apart), then the lower low water.
    extremes = [Extreme(0.0, "low", -1.0), Extreme(0.25, "high", 1.0), Extreme(0.5, "low", 0.3), Extreme(0.75, "high", 0.5),
                Extreme(1.0, "low", -1.0), Extreme(1.25, "high", 1.0)]  # fmt: skip
    span = tool.mean_range(extremes, -1, 2)
    kept = tool.significant_extremes(extremes, span, -1, 2)
    assert [extreme.mjd for extreme in kept] == [0.25, 1.0]


def _score(reference: str, status: str, in_criterion: bool = True) -> tool.Score:
    return tool.Score("x", reference, "1", in_criterion, status=status)


def test_the_verdict_needs_eight_of_ten_or_eighty_percent_of_those_measured() -> None:
    passing = [_score("noaa", "pass")] * 8 + [_score("noaa", "fail")] * 2 + [_score("noaa", "fail", in_criterion=False)]
    assert tool.verdict(passing, "noaa") == {"stations": 10, "measured": 10, "passed": 8, "holds": True}
    failing = [_score("noaa", "pass")] * 7 + [_score("noaa", "fail")] * 3
    assert not tool.verdict(failing, "noaa")["holds"]
    unmeasured = [_score("refmar", "pass")] * 7 + [_score("refmar", "fail")] + [_score("refmar", "not measured")] * 2
    assert tool.verdict(unmeasured, "refmar")["holds"]  # 8 measured: 80 % of them is 7 (rounded up), and 7 pass
    too_few = [_score("refmar", "pass")] * 6 + [_score("refmar", "not measured")] * 4
    assert not tool.verdict(too_few, "refmar")["holds"]  # fewer than 7 measured
