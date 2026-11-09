"""The measure of the forecast swell against the buoys (``tools/measure_surf_error.py``): the parts that are pure.

What is checked is the arithmetic of ADR 0022 (the pairing of the hours, the cross-fit, the criterion), on series worked out by hand or from a seeded
generator; not the sea. Nothing here asks a provider: a test that reached the network would fail.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tools import measure_surf_error as tool

HEADER = ["DateHeure", "H13D", "HM0", "TP", "T02", "THETAP", "THETAM", "QUALITE", "NBSYS", "QUALITE"]  # as CANDHIS has it: QUALITE twice, the second one empty


def td_row(moment: str, h13: str = "2.99", hm0: str = "3.21", quality: str = "1") -> list[str]:
    return [moment, h13, hm0, "10.25", "6.44", "301.83", "300.10", quality, "2", ""]


def td_payload(rows: list[list[str]], header: list[str] = HEADER) -> dict[str, Any]:
    return {"apiVer": "1.00", "success": True, "message": "Données TD directionnel campagne `99999`", "nbLig": len(rows), "entete": header, "results": rows}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a test asked the network")

    monkeypatch.setattr(httpx, "get", refuse)
    monkeypatch.delenv(tool.KEY_ENV, raising=False)


# ------------------------------------------------------------------------------------------------------ reading


def test_the_hours_of_the_window_are_counted_from_the_first_of_december() -> None:
    assert tool.N_HOURS == 335 * 24
    start = tool.START_UNIX
    assert dt.datetime.fromtimestamp(start, dt.timezone.utc) == dt.datetime(2025, 12, 1, tzinfo=dt.timezone.utc)
    assert tool._hour_index(start) == 0 and tool._hour_index(start + 3600) == 1
    assert tool._hour_index(start + 1800) is None  # not on the hour
    assert tool._hour_index(start - 3600) is None and tool._hour_index(start + tool.N_HOURS * 3600) is None  # outside the window
    assert tool._hour_index(start + (tool.N_HOURS - 1) * 3600) == tool.N_HOURS - 1


def test_a_candhis_answer_is_read_by_the_names_of_its_header() -> None:
    measure = tool.parse_td(td_payload([td_row("2025-12-01 00:00:00"), td_row("2025-12-01 00:30:00", hm0="9.99"), td_row("2025-12-01 01:00:00", hm0="3.44")]))
    assert measure.height_kind == "HM0" and measure.records == 3
    assert measure.hs[0] == 3.21 and measure.hs[1] == 3.44  # the record of 00:30 is not on the hour: left out
    assert measure.tp[0] == 10.25 and measure.t02[0] == 6.44 and measure.theta_p[0] == 301.83 and measure.theta_m[0] == 300.10
    assert measure.quality[0] == 1.0  # the first QUALITE column, not the empty second one
    assert int((~np.isnan(measure.hs)).sum()) == 2


def test_a_buoy_without_hm0_gives_h13d_and_says_so() -> None:
    header = ["DateHeure", "H13D", "TH13D", "QUALITE"]
    measure = tool.parse_td(td_payload([["2025-12-01 00:00:00", "2.99", "8.1", "1"]], header))
    assert measure.height_kind == "H13D" and measure.hs[0] == 2.99
    assert math.isnan(measure.tp[0]) and math.isnan(measure.theta_m[0])  # the columns it does not have


def test_a_missing_value_is_not_a_number() -> None:
    measure = tool.parse_td(td_payload([td_row("2025-12-01 00:00:00", hm0="999.9999"), td_row("2025-12-01 01:00:00", hm0="")]))
    assert math.isnan(measure.hs[0]) and math.isnan(measure.hs[1])


def test_only_the_records_of_quality_one_are_scored() -> None:
    rows = [td_row("2025-12-01 00:00:00", hm0="1.0", quality="1"), td_row("2025-12-01 01:00:00", hm0="2.0", quality="4"), td_row("2025-12-01 02:00:00", hm0="3.0", quality="")]
    good = tool.parse_td(td_payload(rows)).hs_good()
    assert good[0] == 1.0 and math.isnan(good[1]) and math.isnan(good[2])


def test_a_record_outside_the_window_or_with_a_bad_date_is_left_out() -> None:
    rows = [td_row("2025-11-30 23:00:00"), td_row("2026-11-01 00:00:00"), td_row("not a date"), td_row("2025-12-01 00:00")]
    measure = tool.parse_td(td_payload(rows))
    assert int((~np.isnan(measure.hs)).sum()) == 1 and measure.hs[0] == 3.21  # a date without seconds is read too


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False, "message": "Pas de données pour la campagne `11266`", "entete": None, "results": None},
        {"success": True, "entete": ["DateHeure", "TP"], "results": []},
        {"success": True, "entete": ["HM0"], "results": []},
        {"success": True, "entete": None, "results": None},
        [],
    ],
)
def test_an_answer_that_is_not_records_raises(payload: Any) -> None:
    with pytest.raises(ValueError):
        tool.parse_td(payload)


def marine_payload(times: list[int], **columns: list[Any]) -> dict[str, Any]:
    return {"latitude": 43.54, "longitude": -1.62, "hourly": {"time": times, **columns}}


def test_the_forecast_is_laid_on_the_grid_by_quantity_and_lead() -> None:
    start = tool.START_UNIX
    payload = marine_payload(
        [start, start + 3600, start + 7200],
        wave_height=[1.0, 2.0, None],
        wave_height_previous_day1=[1.5, 2.5, 3.5],
        wave_period_previous_day3=[10, 11, 12],
    )
    forecast = tool.parse_marine(payload)
    assert (forecast.cell_lat, forecast.cell_lon) == (43.54, -1.62)
    assert list(forecast.series[("height", 0)][:2]) == [1.0, 2.0] and math.isnan(forecast.series[("height", 0)][2])  # a null stays NaN
    assert list(forecast.series[("height", 1)][:3]) == [1.5, 2.5, 3.5] and list(forecast.series[("period", 3)][:3]) == [10, 11, 12]
    assert math.isnan(forecast.series[("direction", 5)][0]) and math.isnan(forecast.series[("height", 5)][0])  # asked and not given
    assert math.isnan(forecast.series[("height", 1)][3])  # beyond the answer


def test_the_variables_asked_of_open_meteo_are_the_twelve_of_the_protocol() -> None:
    names = tool.marine_variables()
    assert len(names) == 12 and "wave_height" in names and "wave_height_previous_day5" in names and "wave_direction_previous_day1" in names
    assert not any("previous_day0" in n for n in names)


def test_a_marine_answer_without_times_raises() -> None:
    with pytest.raises(ValueError):
        tool.parse_marine({"hourly": {"wave_height": [1.0]}})


# ------------------------------------------------------------------------------------------------------ scoring


def test_the_errors_of_a_forecast_are_worked_out_over_the_hours_that_have_both() -> None:
    obs = np.array([1.0, 2.0, 3.0, math.nan, 4.0])
    forecast = np.array([1.5, 2.0, 2.0, 5.0, math.nan])
    s = tool.stats(obs, forecast)
    assert s["n"] == 3
    assert s["bias"] == pytest.approx((0.5 + 0.0 - 1.0) / 3)
    assert s["mae"] == pytest.approx(0.5)
    assert s["rmse"] == pytest.approx(math.sqrt((0.25 + 0.0 + 1.0) / 3))
    assert s["ratio"] == pytest.approx((1.5 + 2.0 + 2.0) / 6.0)
    assert s["within"] == pytest.approx(2 / 3)  # the error of 1 m is the only one over 0.5 m
    assert tool.stats(np.array([math.nan]), np.array([1.0])) == {"n": 0}


def test_the_classes_split_the_hours_by_the_height_the_buoy_read() -> None:
    obs = np.array([0.5, 1.5, 2.5, 3.5, 3.0])
    forecast = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
    classes = tool.class_stats(obs, forecast)
    assert [c["n"] for c in classes] == [1, 1, 1, 2]
    assert classes[3]["to_m"] is None and classes[3]["bias"] == pytest.approx(-2.25)


def test_persistence_is_what_the_buoy_read_n_days_before() -> None:
    obs = np.arange(tool.N_HOURS, dtype=float)
    persisted = tool.persistence(obs, 1)
    assert math.isnan(persisted[23]) and persisted[24] == 0.0 and persisted[100] == 76.0


def test_a_difference_of_direction_goes_the_short_way_round() -> None:
    assert list(tool.circular_difference(np.array([10.0, 350.0, 180.0]), np.array([350.0, 10.0, 0.0]))) == [20.0, -20.0, -180.0]


def test_the_clock_of_a_forecast_that_runs_two_hours_late_is_found() -> None:
    rng = np.random.default_rng(3)
    obs = np.convolve(rng.random(tool.N_HOURS) * 4, np.ones(6) / 6, mode="same")
    late = np.full(tool.N_HOURS, math.nan)
    late[2:] = obs[:-2]  # forecast[i] = obs[i - 2]
    assert tool.best_lag(obs, late) == 2
    assert tool.best_lag(obs, obs) == 0


def test_the_folds_are_the_blocks_of_fourteen_days_one_after_the_other() -> None:
    folds = tool.fold_of_hours()
    assert len(folds) == tool.N_HOURS
    assert set(folds[: 14 * 24]) == {0} and set(folds[14 * 24 : 28 * 24]) == {1} and set(folds[28 * 24 : 42 * 24]) == {0}
    assert abs(int(folds.sum()) - tool.N_HOURS / 2) <= 14 * 24  # about half of the hours each


def synthetic(ratio: Any) -> tuple[np.ndarray, dict[int, np.ndarray]]:
    """A forecast of heights (seeded) and the measure that goes with it: ``ratio`` is a number or a function of the forecast height."""
    rng = np.random.default_rng(1)
    forecast = 0.5 + 3.5 * rng.random(tool.N_HOURS) ** 2
    obs = forecast * (ratio(forecast) if callable(ratio) else ratio)
    return obs, {lead: forecast.copy() for lead in tool.LEADS}


def test_the_factor_is_the_sum_of_the_measures_over_the_sum_of_the_forecasts_on_the_training_hours() -> None:
    obs, forecasts = synthetic(0.8)
    folds = tool.fold_of_hours()
    assert tool.fit_factor(obs, forecasts, folds == 0) == pytest.approx(0.8)
    obs[folds == 0] = math.nan  # nothing to train on
    assert tool.fit_factor(obs, forecasts, folds == 0) is None


def test_a_forecast_that_is_always_too_high_gets_a_factor_that_is_kept() -> None:
    obs, forecasts = synthetic(0.8)
    folds = tool.fold_of_hours()
    directions = {"A_to_B": tool.score_direction(obs, forecasts, 0, folds), "B_to_A": tool.score_direction(obs, forecasts, 1, folds)}
    assert directions["A_to_B"]["factor"] == pytest.approx(0.8) and directions["B_to_A"]["factor"] == pytest.approx(0.8)
    for lead in tool.LEADS:
        score = directions["A_to_B"]["leads"][lead]
        assert score["n"] > tool.MIN_HOURS and score["n_high"] > tool.MIN_HIGH_HOURS
        assert score["rmse_before"] > 0.1 and score["rmse_after"] == pytest.approx(0.0, abs=1e-9) and score["gain"] == pytest.approx(1.0)
    kept, reasons = tool.judge(directions)
    assert kept and reasons == []


def test_a_forecast_that_is_already_right_is_not_corrected() -> None:
    obs, forecasts = synthetic(1.0)
    folds = tool.fold_of_hours()
    directions = {"A_to_B": tool.score_direction(obs, forecasts, 0, folds), "B_to_A": tool.score_direction(obs, forecasts, 1, folds)}
    kept, reasons = tool.judge(directions)
    assert not kept and any("gain" in r and "under 5 %" in r for r in reasons)


def test_a_factor_that_makes_the_big_hours_worse_is_not_kept_even_if_the_whole_improves() -> None:
    obs, forecasts = synthetic(lambda f: np.where(f < 3.3, 0.7, 1.2))  # too high at small heights, too low at the biggest
    folds = tool.fold_of_hours()
    directions = {"A_to_B": tool.score_direction(obs, forecasts, 0, folds), "B_to_A": tool.score_direction(obs, forecasts, 1, folds)}
    score = directions["A_to_B"]["leads"][1]
    assert score["gain"] >= tool.MIN_GAIN  # the whole improves enough (6.8 % on this series)...
    assert score["rmse_high_after"] > score["rmse_high_before"]  # ...and the hours of 2 m and over get worse
    kept, reasons = tool.judge(directions)
    assert not kept and reasons and all("rises" in r for r in reasons)  # that clause alone refuses it


def test_a_pair_with_too_few_hours_is_not_enough_data_and_is_not_corrected() -> None:
    obs, forecasts = synthetic(0.8)
    obs[600:] = math.nan  # a buoy that stopped after 25 days
    folds = tool.fold_of_hours()
    directions = {"A_to_B": tool.score_direction(obs, forecasts, 0, folds), "B_to_A": tool.score_direction(obs, forecasts, 1, folds)}
    kept, reasons = tool.judge(directions)
    assert not kept and any("not enough data" in r for r in reasons)


def test_a_pair_with_no_forecast_has_no_factor_and_is_not_corrected() -> None:
    obs, forecasts = synthetic(0.8)
    nothing = {lead: np.full(tool.N_HOURS, math.nan) for lead in tool.LEADS}
    folds = tool.fold_of_hours()
    directions = {"A_to_B": tool.score_direction(obs, nothing, 0, folds), "B_to_A": tool.score_direction(obs, nothing, 1, folds)}
    kept, reasons = tool.judge(directions)
    assert directions["A_to_B"]["factor"] is None and not kept and any("no factor" in r for r in reasons)


def test_the_gain_needed_is_the_one_written_in_the_adr() -> None:
    adr = (Path(__file__).resolve().parents[1] / "docs" / "decisions" / "0022-forecast-swell-measured-against-buoys.md").read_text(encoding="utf-8")
    assert f"**{tool.MIN_GAIN * 100:g} %**" in adr and f"at least {tool.MIN_HOURS:,} held-out hours" in adr and f"at least {tool.MIN_HIGH_HOURS} with" in adr
    assert f"{tool.HIGH_M:g} m or more" in adr and "blocks of 14 days" in adr and tool.WINDOW_START.isoformat() in adr and tool.WINDOW_END.isoformat() in adr


def test_period_and_direction_are_described_over_the_hours_of_a_real_sea_only() -> None:
    measure = tool.parse_td(td_payload([td_row("2025-12-01 00:00:00", hm0="2.0"), td_row("2025-12-01 01:00:00", hm0="0.4")]))
    start = tool.START_UNIX
    forecast = tool.parse_marine(
        marine_payload([start, start + 3600], wave_period=[7.0, 9.0], wave_direction=[310.0, 100.0], wave_period_previous_day1=[7.0, 9.0], wave_direction_previous_day1=[310.0, 100.0])
    )
    described = tool.describe_period_direction(measure, forecast, 0)
    assert described["hours"] == 1  # the second hour has a buoy under 1 m
    assert described["period_vs_t02"]["bias"] == pytest.approx(7.0 - 6.44) and described["period_vs_tp"]["bias"] == pytest.approx(7.0 - 10.25)
    assert described["direction_vs_theta_m"]["bias"] == pytest.approx(310.0 - 300.10)


# ------------------------------------------------------------------------------------------------------ the pairs and the run


def test_the_pairs_are_the_spots_with_a_real_time_buoy_within_the_comparable_distance() -> None:
    pairs = tool.pairs_of_the_service()
    assert pairs and all(p.distance_km <= 30.0 for p in pairs)
    assert len({p.spot_id for p in pairs}) == len(pairs)
    anglet = [p for p in pairs if p.buoy_id == "06402"]
    assert anglet and all(len(p.buoy_id) == 5 for p in pairs)


def cache_names(buoy_id: str, lat: float, lon: float) -> tuple[str, str]:
    end = tool.WINDOW_END + dt.timedelta(days=1)
    return f"candhis-td-{buoy_id}-{tool.WINDOW_START}-{end}.json.gz", f"marine-{tool.coord_str(lat)}-{tool.coord_str(lon)}-{tool.WINDOW_START}-{tool.WINDOW_END}.json.gz"


def test_the_cache_is_read_and_nothing_is_asked(tmp_path: Path) -> None:
    pair = tool.pairs_of_the_service()[0]
    td_name, marine_name = cache_names(pair.buoy_id, pair.lat, pair.lon)
    (tmp_path / td_name).write_bytes(gzip.compress(json.dumps(td_payload([td_row("2025-12-01 00:00:00")])).encode()))
    (tmp_path / marine_name).write_bytes(gzip.compress(json.dumps(marine_payload([tool.START_UNIX], wave_height=[1.0])).encode()))
    assert tool.fetch_buoy(pair.buoy_id, tmp_path, offline=False).hs[0] == 3.21  # no key and no network needed: it is in the cache
    assert tool.fetch_forecast(pair, tmp_path, offline=False).series[("height", 0)][0] == 1.0


def test_without_the_cache_and_without_a_key_the_run_stops_and_says_which_variable(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as stop:
        tool.fetch_buoy("06402", tmp_path, offline=False)
    assert tool.KEY_ENV in str(stop.value)
    with pytest.raises(SystemExit) as offline:
        tool.fetch_forecast(tool.pairs_of_the_service()[0], tmp_path, offline=True)
    assert "--offline" in str(offline.value)


def test_a_whole_run_from_the_cache_gives_a_report_and_results_that_can_be_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pair = tool.pairs_of_the_service()[0]
    obs, forecasts = synthetic(0.8)
    start = tool.START_UNIX
    rows = [td_row(dt.datetime.fromtimestamp(start + 3600 * i, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), hm0=f"{obs[i]:.4f}") for i in range(tool.N_HOURS)]
    times = [start + 3600 * i for i in range(tool.N_HOURS)]
    columns: dict[str, list[float]] = {}
    for lead in tool.ALL_LEADS:
        for quantity in tool.QUANTITIES:
            values = forecasts[1] if quantity == "height" else np.full(tool.N_HOURS, 8.0)
            columns[tool.variable_name(quantity, lead)] = [float(v) for v in values]
    td_name, marine_name = cache_names(pair.buoy_id, pair.lat, pair.lon)
    (tmp_path / td_name).write_bytes(gzip.compress(json.dumps(td_payload(rows)).encode()))
    (tmp_path / marine_name).write_bytes(gzip.compress(json.dumps(marine_payload(times, **columns)).encode()))
    monkeypatch.setattr(tool, "pairs_of_the_service", lambda: [pair])
    results = tool._clean(tool.measure(tmp_path, offline=True))
    assert results["kept"] == [pair.spot_id] and results["pairs"][0]["factor_whole_window"] == pytest.approx(0.8)
    assert results["pairs"][0]["lag_hours"] == 0 and results["pairs"][0]["by_lead"]["3"]["ratio"] == pytest.approx(1.25)
    assert results["overall"]["by_lead"]["1"]["n"] == tool.N_HOURS
    text = tool.report(results)
    for heading in ("## Pairs", "## All pairs together", "### By month, lead 1 day", "## By buoy", "## Quality filter", "## Verdict per pair"):
        assert heading in text
    assert "Kept: 1 of 1 pairs." in text
    assert json.loads(json.dumps(results))["window"] == ["2025-12-01", "2026-10-31"]  # plain JSON: no NaN, no numpy
