"""The measure of the forecast models (``tools/measure_forecast_models.py``): the parts that are pure.

Nothing here touches the network: the requests are played against ``httpx.MockTransport`` and the statistics against
small tables worked out by hand. What is checked is the *arithmetic* the report rests on (the weight of a request, the
windows of a day, the pairing of the days, the interval, the reading of a difference), not the weather.
"""

from __future__ import annotations

import gzip
import json
import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import numpy as np
import pytest

from tools import measure_forecast_models as tool

UTC = timezone.utc


def has_time_zones() -> bool:
    try:
        ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        return False
    return True


needs_zones = pytest.mark.skipif(not has_time_zones(), reason="no time zone database (pip install tzdata)")

PARIS = next(s for s in tool.STATIONS if s.key == "75114001")
JFK = next(s for s in tool.STATIONS if s.key == "JFK")


# --- the weight of a request ---------------------------------------------------------------------------------------


def test_the_models_multiply_the_variables_in_the_weight():
    params = tool.forecast_params(48.8217, 2.3378, date(2026, 8, 20), date(2026, 10, 21), (tool.BEST_MATCH, *tool.MODELS))
    # 15 hourly variables x 9 models = 135 columns = 13.5 calls, over 63 days = 4.5 times 14 days.
    assert tool.request_weight(params) == pytest.approx(13.5 * 4.5)
    one_model = tool.forecast_params(48.8217, 2.3378, date(2026, 8, 20), date(2026, 10, 21), None)
    assert tool.request_weight(one_model) == pytest.approx(1.5 * 4.5)


def test_a_short_request_weighs_its_variables_only():
    params = {
        "latitude": "1",
        "longitude": "2",
        "start_date": "2026-07-21",
        "end_date": "2026-07-23",
        "hourly": "temperature_2m_previous_day1,temperature_2m_previous_day5",
        "models": ",".join(["best_match"] + [f"m{i}" for i in range(12)]),
    }
    assert tool.request_weight(params) == pytest.approx(2.6)  # 13 models x 2 variables, under 14 days
    assert tool.request_weight({**params, "models": "best_match", "hourly": "temperature_2m_previous_day1"}) == 1.0


def test_the_request_names_what_it_asks_and_sends_no_address():
    params = tool.forecast_params(48.82168, 2.33783, date(2026, 8, 20), date(2026, 10, 21), None)
    assert params["timezone"] == "GMT" and params["wind_speed_unit"] == "ms"
    assert params["latitude"] == "48.8217" and params["longitude"] == "2.3378"
    assert "models" not in params
    names = params["hourly"].split(",")
    assert len(names) == 15 and "wind_speed_10m_previous_day5" in names
    assert "@" not in tool.USER_AGENT and "mailto" not in tool.USER_AGENT.lower()


def test_the_url_does_not_depend_on_the_order_of_the_parameters():
    one = tool.canonical_url("https://x/y", {"b": "2", "a": "1"})
    two = tool.canonical_url("https://x/y", {"a": "1", "b": "2"})
    assert one == two == "https://x/y?a=1&b=2"


# --- the ledger and the ceiling --------------------------------------------------------------------------------------


def payload(hours: int = 72):
    times = [(datetime(2026, 10, 19, tzinfo=UTC) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    return {"latitude": 48.8, "longitude": 2.3, "elevation": 71.0, "hourly": {"time": times}}


def mock_source(tmp_path, max_weight, sent, monkeypatch):
    monkeypatch.setattr(tool.time, "sleep", lambda seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=payload())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return tool.Source(cache_dir=tmp_path, max_weight=max_weight, _client=client)


SHORT = {"latitude": "1", "longitude": "2", "start_date": "2026-10-19", "end_date": "2026-10-21", "hourly": "temperature_2m_previous_day1"}


def test_a_request_is_booked_once_and_answered_from_the_cache_after(tmp_path, monkeypatch):
    sent: list[httpx.Request] = []
    source = mock_source(tmp_path, 10, sent, monkeypatch)
    source.open_meteo(SHORT, "first")
    source.open_meteo(SHORT, "again")
    assert len(sent) == 1
    assert source.spent() == 1.0
    assert (source.om_calls, source.om_hits) == (1, 1)
    # a new run reads the same ledger and the same cache
    again = mock_source(tmp_path, 10, sent, monkeypatch)
    again.open_meteo(SHORT, "third run")
    assert len(sent) == 1 and again.spent() == 1.0


def test_the_ceiling_stops_a_request_before_it_leaves(tmp_path, monkeypatch):
    sent: list[httpx.Request] = []
    source = mock_source(tmp_path, 1.5, sent, monkeypatch)
    source.open_meteo(SHORT, "first")
    with pytest.raises(tool.BudgetExceeded):
        source.open_meteo({**SHORT, "latitude": "3"}, "second")
    assert len(sent) == 1 and source.spent() == 1.0


def test_a_request_that_fails_is_booked_anyway(tmp_path, monkeypatch):
    monkeypatch.setattr(tool.time, "sleep", lambda seconds: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500, text="down")))
    source = tool.Source(cache_dir=tmp_path, max_weight=10, _client=client)
    with pytest.raises(RuntimeError):
        source.open_meteo(SHORT, "failing")
    assert source.spent() == 1.0
    events = [entry["event"] for entry in source.ledger()]
    assert events == ["sent", "answered"]


def test_a_dry_run_sends_nothing(tmp_path):
    source = tool.Source(cache_dir=tmp_path, max_weight=10, dry_run=True)
    with pytest.raises(tool.BudgetExceeded):
        source.open_meteo(SHORT, "never")
    assert source.spent() == 0.0


# --- the windows of a day ---------------------------------------------------------------------------------------------


def test_france_follows_the_windows_of_meteo_france():
    day = date(2026, 9, 10)
    assert tool.window_bounds(PARIS, "tx", day) == (datetime(2026, 9, 10, 6, tzinfo=UTC), datetime(2026, 9, 11, 6, tzinfo=UTC))
    assert tool.window_bounds(PARIS, "tn", day) == (datetime(2026, 9, 9, 18, tzinfo=UTC), datetime(2026, 9, 10, 18, tzinfo=UTC))
    assert tool.window_bounds(PARIS, "rr", day) == tool.window_bounds(PARIS, "tx", day)
    assert tool.window_bounds(PARIS, "ws", day) == (datetime(2026, 9, 10, tzinfo=UTC), datetime(2026, 9, 11, tzinfo=UTC))


@needs_zones
def test_the_world_uses_the_local_day_and_its_length_on_the_days_the_clock_changes():
    start, end = tool.window_bounds(JFK, "tx", date(2026, 9, 10))
    assert (start, end) == (datetime(2026, 9, 10, 4, tzinfo=UTC), datetime(2026, 9, 11, 4, tzinfo=UTC))
    # 1 November 2026: the clocks of New York go back, the local day lasts 25 hours
    start, end = tool.window_bounds(JFK, "tx", date(2026, 11, 1))
    assert (end - start) == timedelta(hours=25)
    auckland = next(s for s in tool.STATIONS if s.key == "NZAA")
    start, end = tool.window_bounds(auckland, "tx", date(2026, 9, 27))  # the clocks go forward: 23 hours
    assert (end - start) == timedelta(hours=23)


def test_a_sum_is_labelled_with_the_end_of_its_hour():
    start, end = datetime(2026, 9, 10, 6, tzinfo=UTC), datetime(2026, 9, 11, 6, tzinfo=UTC)
    instant = tool.window_hours(start, end, accumulated=False)
    summed = tool.window_hours(start, end, accumulated=True)
    assert len(instant) == len(summed) == 24
    assert instant[0] == start and instant[-1] == end - timedelta(hours=1)
    assert summed[0] == start + timedelta(hours=1) and summed[-1] == end
    with pytest.raises(ValueError):
        tool.window_hours(start + timedelta(minutes=30), end, accumulated=False)


def test_the_hourly_values_fold_into_the_daily_quantity():
    hours = np.arange(24, dtype=float)
    assert tool.fold_values(hours, "tx") == 23.0
    assert tool.fold_values(hours, "tn") == 0.0
    assert tool.fold_values(np.ones(24) * 0.5, "rr") == pytest.approx(12.0)
    holes = hours.copy()
    holes[:5] = np.nan
    assert math.isnan(tool.fold_values(holes, "tx", min_coverage=1.0))
    assert tool.fold_values(holes, "tx", min_coverage=0.75) == 23.0


def test_a_series_folds_the_window_of_the_station():
    hours = 96
    times = [(datetime(2026, 9, 9, tzinfo=UTC) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    hourly = {"time": times}
    for variable in tool.HOURLY_VARIABLES:
        for lead in tool.LEADS:
            hourly[f"{variable}_previous_day{lead}_best_match"] = [float(h) for h in range(hours)]
    series = tool.HourlySeries({"hourly": hourly, "elevation": 71.0}, ("best_match",))
    # TX of 10 September: the hours 06 UTC of the 10th to 05 UTC of the 11th -> indices 30 .. 53
    assert series.fold("best_match", "tx", 1, PARIS, date(2026, 9, 10)) == 53.0
    # TN: 18 UTC of the 9th to 17 UTC of the 10th -> indices 18 .. 41
    assert series.fold("best_match", "tn", 1, PARIS, date(2026, 9, 10)) == 18.0
    # rain: the sums labelled 07 UTC of the 10th to 06 UTC of the 11th -> indices 31 .. 54
    assert series.fold("best_match", "rr", 1, PARIS, date(2026, 9, 10)) == pytest.approx(sum(range(31, 55)))
    # a window that leaves the answer is missing, not guessed
    assert math.isnan(series.fold("best_match", "tx", 1, PARIS, date(2026, 9, 12)))


# --- the observations ------------------------------------------------------------------------------------------------


def test_the_daily_file_of_meteo_france_is_read_and_a_doubtful_value_is_dropped():
    header = "NUM_POSTE;NOM_USUEL;LAT;LON;ALTI;AAAAMMJJ;RR;QRR;TN;QTN;HTN;QHTN;TX;QTX;HTX;QHTX;FXY;QFXY\n"
    rows = (
        "75114001;PARIS-MONTSOURIS;48.821667;2.337833;75;20260910;0.4;1;14.2;1;0600;1;24.1;1;1500;1;6.3;1\n"
        "75114001;PARIS-MONTSOURIS;48.821667;2.337833;75;20260911;;;13.0;2;0600;1;;;;;5.0;9\n"
        "99999999;OTHER;1;1;1;20260910;9.9;1;9.9;1;0600;1;9.9;1;1500;1;9.9;1\n"
    )
    parsed, info = tool.parse_mf_daily(gzip.compress((header + rows).encode("utf-8")), "75114001")
    assert set(parsed) == {date(2026, 9, 10), date(2026, 9, 11)}
    assert parsed[date(2026, 9, 10)] == {"tx": 24.1, "tn": 14.2, "rr": 0.4, "ws": 6.3}
    second = parsed[date(2026, 9, 11)]
    assert math.isnan(second["tx"]) and math.isnan(second["rr"]) and math.isnan(second["tn"])  # TN has quality code 2
    assert second["ws"] == 5.0
    assert info["name"] == "PARIS-MONTSOURIS" and info["dropped_doubtful"] == 1


def test_the_maximum_of_the_reports_needs_most_of_the_hours():
    day = date(2026, 9, 10)
    start = datetime(2026, 9, 10, 4, tzinfo=UTC)
    reports = [tool.Report(start + timedelta(hours=h, minutes=51), 10.0 + h, 3.0) for h in range(24)]
    if has_time_zones():
        assert tool.obs_from_reports(JFK, "tx", day, reports) == 33.0
        assert tool.obs_from_reports(JFK, "tn", day, reports) == 10.0
        assert math.isnan(tool.obs_from_reports(JFK, "tx", day, reports[:10]))
    assert math.isnan(tool.obs_from_reports(PARIS, "tx", day, reports[:5]))


@needs_zones
def test_the_rain_of_a_day_is_the_sum_of_the_routine_reports_and_needs_the_hours():
    day = date(2026, 9, 10)
    start = datetime(2026, 9, 10, 4, tzinfo=UTC)
    rain = [(start + timedelta(hours=h, minutes=-9), 0.5) for h in range(1, 25)]  # reports at H:51 of the hour before
    assert tool.obs_rain(JFK, day, rain) == pytest.approx(12.0)
    assert math.isnan(tool.obs_rain(JFK, day, rain[:10]))
    doubled = rain + [(rain[0][0] + timedelta(minutes=1), 0.7)]  # a correction in the same hour keeps the larger value
    assert tool.obs_rain(JFK, day, doubled) == pytest.approx(12.2)


# --- the statistics ---------------------------------------------------------------------------------------------------


def test_a_missing_model_leaves_the_mean_of_the_models_present():
    values = np.array([[10.0, np.nan], [12.0, 20.0], [14.0, np.nan]])
    mean, count = tool.blend_members(values, "mean")
    median, _ = tool.blend_members(values, "median")
    assert mean.tolist() == [12.0, 20.0] and count.tolist() == [3, 1]
    assert median.tolist() == [12.0, 20.0]
    none, zero = tool.blend_members(np.full((3, 2), np.nan), "mean")
    assert np.isnan(none).all() and zero.tolist() == [0, 0]


def test_the_weights_follow_the_errors_of_the_learning_half_and_sum_to_one():
    errors = np.array([[1.0, 1.0], [2.0, 2.0], [np.nan, np.nan]])
    weights = tool.learn_weights(errors)
    assert weights.sum() == pytest.approx(1.0)
    assert weights[0] > weights[1] and weights[2] == 0.0
    blended = tool.weighted_blend(np.array([[10.0, np.nan], [20.0, 20.0], [99.0, 99.0]]), weights)
    assert blended[0] == pytest.approx(10.0 * weights[0] / (weights[0] + weights[1]) + 20.0 * weights[1] / (weights[0] + weights[1]))
    assert blended[1] == 20.0  # the model that is missing that day does not count; the weights are renormalised


def test_only_the_days_where_both_forecasts_and_the_observation_exist_are_paired():
    forecast = np.array([[10.0, 12.0, np.nan], [20.0, 20.0, 20.0]])
    reference = np.array([[11.0, np.nan, 11.0], [21.0, 21.0, 21.0]])
    observed = np.array([[10.0, 10.0, 10.0], [18.0, 18.0, np.nan]])
    comps = tool.abs_error_components(forecast, reference, observed, np.array([True, True]))
    assert comps["n"].tolist() == [2.0, 1.0, 0.0]
    assert comps["abs_f"].tolist() == [0.0 + 2.0, 2.0, 0.0]
    assert comps["abs_r"].tolist() == [1.0 + 3.0, 3.0, 0.0]
    only_first = tool.abs_error_components(forecast, reference, observed, np.array([True, False]))
    assert only_first["n"].tolist() == [1.0, 0.0, 0.0]


def test_the_resamples_are_whole_days_and_repeatable():
    first = tool.make_resamples(20, 100, seed=1)
    assert first.shape == (100, 20) and first.min() >= 0 and first.max() < 20
    assert (first == tool.make_resamples(20, 100, seed=1)).all()
    assert not (first == tool.make_resamples(20, 100, seed=2)).all()


def test_the_interval_holds_the_estimate_and_shrinks_with_the_days():
    rng = np.random.default_rng(3)
    few = {"d": rng.normal(-0.3, 0.5, 20), "n": np.ones(20)}
    many = {"d": rng.normal(-0.3, 0.5, 400), "n": np.ones(400)}

    def mean(t):
        return t["d"] / t["n"]

    est_few, low_few, high_few = tool.interval(few, mean, tool.make_resamples(20, 2000))
    est_many, low_many, high_many = tool.interval(many, mean, tool.make_resamples(400, 2000))
    assert low_few < est_few < high_few and low_many < est_many < high_many
    assert (high_many - low_many) < (high_few - low_few)
    assert est_many == pytest.approx(many["d"].sum() / 400)
    constant = tool.interval({"d": np.full(10, 2.0), "n": np.ones(10)}, mean, tool.make_resamples(10, 500))
    assert constant == (2.0, 2.0, 2.0)


def test_the_detection_scores_of_a_table_worked_by_hand():
    observed = np.array([[0.0, 2.0, 3.0, 0.5, 5.0]])
    forecast = np.array([[1.5, 2.5, 0.2, 0.0, 6.0]])
    valid = np.ones_like(observed, dtype=bool)
    counts = tool.event_components(forecast, observed, valid)
    totals = {k: float(v.sum()) for k, v in counts.items()}
    assert totals == {"hit": 2.0, "miss": 1.0, "fa": 1.0, "cn": 1.0}
    assert tool.pod(totals) == pytest.approx(2 / 3)
    assert tool.far(totals) == pytest.approx(1 / 3)
    assert tool.csi(totals) == pytest.approx(2 / 4)
    nothing = tool.event_components(forecast, observed, np.zeros_like(valid))
    assert all(v.sum() == 0 for v in nothing.values())


def test_a_gain_needs_an_interval_that_excludes_zero_and_a_useful_size():
    assert tool.verdict(-0.4, -0.6, -0.2, 0.2) == "better"
    assert tool.verdict(-0.1, -0.15, -0.05, 0.2) == "better (below threshold)"
    assert tool.verdict(-0.4, -0.9, 0.1, 0.2) == "no difference shown"
    assert tool.verdict(0.3, 0.1, 0.5, 0.2) == "worse"
    assert tool.verdict(0.05, 0.01, 0.1, 0.2) == "worse (below threshold)"
    assert tool.verdict(0.05, math.nan, 0.1, 0.2) == "n/a"
    # a score that should go up: the detection rate
    assert tool.verdict(0.05, 0.03, 0.08, 0.02, lower_is_better=False) == "better"
    assert tool.verdict(-0.05, -0.08, -0.03, 0.02, lower_is_better=False) == "worse"


# --- the whole chain on a small made-up data set ------------------------------------------------------------------


def synthetic_dataset(days: int = 30) -> tool.Dataset:
    """Two stations; the models err in opposite directions, so the mean is better than best_match and than any model."""
    rng = np.random.default_rng(11)
    stations = [PARIS, next(s for s in tool.STATIONS if s.key == "EDDF")]
    day_list = tool.date_range(date(2026, 9, 1), date(2026, 9, 1) + timedelta(days=days - 1))
    fc, obs = {}, {}
    for quantity in tool.QUANTITIES:
        truth = rng.normal(20.0, 5.0, size=(2, days)) if quantity != "rr" else np.abs(rng.normal(2.0, 3.0, size=(2, days)))
        if quantity == "rr":
            truth[1] = np.nan  # no rain observation at the airport
        obs[quantity] = truth
        array = np.full((2, len(tool.ENTRIES), len(tool.LEADS), days), np.nan)
        for e_index, entry in enumerate(tool.ENTRIES):
            for l_index in range(len(tool.LEADS)):
                if entry in tool.MODELS:
                    sign = 1.0 if tool.MODELS.index(entry) % 2 == 0 else -1.0
                    array[:, e_index, l_index, :] = np.where(np.isnan(truth), 5.0, truth) + sign * 3.0 + rng.normal(0, 0.2, (2, days))
                else:  # best_match and its grid twin: wrong in the same direction
                    array[:, e_index, l_index, :] = np.where(np.isnan(truth), 5.0, truth) + 3.0 + rng.normal(0, 0.2, (2, days))
        fc[quantity] = array
    return tool.Dataset(stations, day_list, fc, obs, {})


def test_the_mean_of_models_that_err_in_opposite_ways_beats_the_reference():
    results = tool.score_dataset(synthetic_dataset())
    mean_tx = [r for r in results["mae"] if r["quantity"] == "tx" and r["subset"] == "all" and r["period"] == "all_days" and r["entry"] == "mean"]
    assert [r["lead"] for r in mean_tx] == list(tool.LEADS)
    for row in mean_tx:
        assert row["mae"] < 0.5 < row["best_match_mae"]
        assert row["diff_high"] < 0 and row["verdict"] == "better"
        assert row["n_pairs"] == 2 * 30
    # the reference never appears against itself, the grid twin is in
    entries = {r["entry"] for r in results["mae"]}
    assert "best_match" not in entries and tool.GRID_ENTRY in entries and tool.BLEND_WEIGHTED in entries
    # rain: one station only has an observation
    rain = [r for r in results["mae"] if r["quantity"] == "rr" and r["subset"] == "all" and r["entry"] == "mean" and r["period"] == "all_days"]
    assert all(r["n_pairs"] == 30 for r in rain)
    # the weighted mean is scored on the second half only
    weighted = [r for r in results["mae"] if r["entry"] == tool.BLEND_WEIGHTED]
    assert weighted and all(r["period"] == "second_half" for r in weighted)
    assert results["split"]["learning_days"][0] == "2026-09-01"
    events = [r for r in results["rain_events"] if r["entry"] == "mean" and r["subset"] == "all" and r["period"] == "all_days"]
    assert events and all(0.0 <= r["pod"] <= 1.0 for r in events)
    assert all(r["n_pairs"] > 0 for r in results["rain_events"])


def test_the_models_that_do_not_reach_a_lead_are_counted_not_filled_in():
    dataset = synthetic_dataset(10)
    for quantity in tool.QUANTITIES:
        dataset.fc[quantity][:, tool.ENTRIES.index("meteofrance_seamless"), 3:, :] = np.nan  # J+4 and J+5
    members = {row["lead"]: row for row in tool.member_rows(dataset)}
    assert members[1]["mean_members"] == len(tool.MODELS) and members[1]["station_days_with_fewer_than_all_models"] == 0
    assert members[4]["mean_members"] == len(tool.MODELS) - 1
    assert members[5]["station_days_with_fewer_than_all_models"] == members[5]["station_days"]


def test_the_results_are_written_as_json_without_nan():
    cleaned = tool.clean({"a": math.nan, "b": 0.123456, "c": [np.float64(1.0), np.int64(3)], "d": "x"})
    assert cleaned == {"a": None, "b": 0.1235, "c": [1.0, 3], "d": "x"}
    json.dumps(cleaned)


def test_one_station_that_wins_everything_shows_in_the_leave_one_out_range():
    dataset = synthetic_dataset(20)
    robustness = tool.score_dataset(dataset)["robustness"]
    assert len(robustness) == len(tool.QUANTITIES) * len(tool.LEADS)
    tx = next(r for r in robustness if r["quantity"] == "tx" and r["lead"] == 1)
    assert tx["diff"] < 0
    assert tx["leave_one_out_max"] < 0  # whichever station is left out, the mean still wins
    assert tx["stations_better"] == tx["stations_total"] == 2
    # rain is observed at one station only
    rr = next(r for r in robustness if r["quantity"] == "rr" and r["lead"] == 1)
    assert rr["stations_total"] == 1


def test_the_tables_of_the_report_are_written_from_the_results():
    results = tool.score_dataset(synthetic_dataset(20))
    text = tool.render_markdown(results)
    assert "### daily maximum temperature (degC): best_match against the mean of the 8 models, all stations" in text
    assert "| J+1 | 40 |" in text  # two stations, twenty days
    assert text.count("\n|---") >= 8
    summary = tool.render_summary(results)
    assert "rain events" in summary and "leave-one-out" in summary


def test_the_hourly_sampling_bias_is_the_gap_between_the_true_extreme_and_the_hourly_ones():
    day = date(2026, 9, 10)
    start = datetime(2026, 9, 10, 6, tzinfo=UTC)
    header = "NUM_POSTE;AAAAMMJJHH;T;QT\n"
    rows = []
    for h in range(-12, 48):  # the hours around the two windows
        moment = start + timedelta(hours=h)
        temperature = 20.0 + (5.0 if moment.hour == 15 else 0.0) - (4.0 if moment.hour == 5 else 0.0)
        rows.append(f"75114001;{moment.strftime('%Y%m%d%H')};{temperature};1\n")
    hourly = tool.parse_mf_hourly_temperature(gzip.compress((header + "".join(rows)).encode("utf-8")), "75114001")
    result = tool.sampling_bias(hourly, PARIS, [day], np.array([26.0]), np.array([15.5]))
    # hourly maximum 25.0 against a true 26.0; hourly minimum 16.0 against a true 15.5
    assert result["true_tx_minus_hourly_max_mean"] == pytest.approx(1.0)
    assert result["hourly_min_minus_true_tn_mean"] == pytest.approx(0.5)
    assert result["n_days"] == 1


def test_the_probe_asks_the_models_the_plan_names_and_keeps_to_a_few_calls():
    requests = tool.probe_requests()
    assert len(requests) == 3
    assert sum(tool.request_weight(params) for _, params in requests) == pytest.approx(6.2)
    models = requests[0][1]["models"].split(",")
    assert set(tool.MODELS) <= set(models) and tool.BEST_MATCH in models


def test_the_cost_of_the_models_in_production_is_worked_out_with_the_rule_of_the_repository():
    cost = tool.production_cost()
    weights = cost["weighted_calls_per_refresh"]
    assert weights["daily_request_today"] == 1.0
    assert weights["eight_models_for_the_two_temperatures_only"] == pytest.approx(1.6)  # 2 variables x 8 models over 10 days
    assert weights["today_plus_two_temperatures_of_eight_models"] == pytest.approx(2.6)
    assert weights["eight_models_for_the_four_measured_variables_only"] == pytest.approx(3.2)
    assert weights["eight_models_for_all_seven_daily_variables"] == pytest.approx(5.6)
    assert cost["refreshes_a_day_per_cell"] == 24


def test_the_mean_is_read_at_the_threshold_where_it_announces_as_many_rainy_days_as_the_reference():
    reference = np.array([0.0, 2.0, 3.0, 0.5, 0.0, 5.0])  # three days of 1 mm or more
    values = np.array([0.4, 1.2, 0.9, 1.1, 0.8, 2.5])  # a smoothed forecast: five days over 0.8, four over 1.0
    threshold = tool.matching_threshold(values, reference)
    assert (values >= threshold).sum() == 3
    assert threshold == 1.1
    # nothing to match: the usual threshold stays
    assert tool.matching_threshold(values, np.zeros(6)) == tool.RAIN_EVENT_MM
    observed = np.array([[0.0, 2.0, 3.0, 0.5, 0.0, 5.0]])
    counts = tool.event_components(values[None, :], observed, np.ones((1, 6), dtype=bool), forecast_threshold=threshold)
    assert sum(float(v.sum()) for v in counts.values()) == 6.0
    assert float(counts["hit"].sum() + counts["fa"].sum()) == 3.0  # three days announced, as asked


def test_the_rain_table_has_the_same_frequency_reading_of_the_mean():
    results = tool.score_dataset(synthetic_dataset(20))
    rows = [r for r in results["rain_events"] if r["entry"] == tool.MEAN_SAME_FREQUENCY and r["subset"] == "all" and r["period"] == "all_days"]
    assert [r["lead"] for r in rows] == list(tool.LEADS)
    assert all(r["forecast_threshold_mm"] > 0 for r in rows)


def test_the_daily_file_is_rebuilt_from_the_hourly_values_over_the_window_and_not_over_the_calendar_day():
    day = date(2026, 9, 10)
    hourly = {}
    for h in range(-30, 60):  # hours around the 10th, labelled by the END of the hour they describe
        moment = datetime(2026, 9, 10, tzinfo=UTC) + timedelta(hours=h)
        # a warm spell that peaks at 03 UTC of the 11th (inside the TX window 06 UTC D to 06 UTC D+1, outside the UTC day)
        warm = 30.0 if moment == datetime(2026, 9, 11, 3, tzinfo=UTC) else 20.0
        hourly[moment] = {"t": warm, "tx": warm, "tn": 12.0, "rr": 1.0, "ws": 5.0}
    obs = {q: np.array([[value]]) for q, value in {"tx": 30.0, "tn": 12.0, "rr": 24.0, "ws": 5.0}.items()}
    result = tool.windows_check(hourly, PARIS, [day], obs, 0)
    assert result["tx"]["official_mean_abs_difference"] == 0.0
    assert result["tx"]["utc_day_mean_abs_difference"] == pytest.approx(10.0)
    assert result["ws"]["official_mean_abs_difference"] == result["ws"]["utc_day_mean_abs_difference"] == 0.0
