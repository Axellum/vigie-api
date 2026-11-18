"""The measure of MFWAM 0.025 against the buoys (``tools/measure_mfwam_france.py``): the parts that are pure.

What is checked is the arithmetic (pairing of the hours, nearest water cell, statistics, the criterion of ADR 0029) on series worked out by hand;
not the sea. Nothing here asks a provider.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from tools import measure_mfwam_france as tool


def test_valid_time_adds_the_lead_to_the_run():
    run = dt.datetime(2026, 11, 18, 0, tzinfo=dt.timezone.utc)
    assert tool.valid_time(run, 24) == int(dt.datetime(2026, 11, 19, 0, tzinfo=dt.timezone.utc).timestamp())


def test_circular_difference_goes_the_short_way_round():
    d = tool.circular_difference(np.array([350.0, 10.0, 180.0]), np.array([10.0, 350.0, 0.0]))
    assert d.tolist() == [-20.0, 20.0, -180.0]


def test_buoy_positions_take_the_median_of_a_drifting_mooring():
    rows = [{"geo_id_wmo": "1", "name": "B", "lat": 45.0 + e, "lon": -5.0 + e} for e in (0.0, 0.01, 0.5)]
    assert tool.buoy_positions(rows)["1"] == {"name": "B", "lat": pytest.approx(45.01), "lon": pytest.approx(-4.99)}


def test_hourly_waves_keeps_the_records_on_the_hour_with_a_height():
    rows = [
        {"geo_id_wmo": "1", "validity_time": "2026-11-18T07:00:00Z", "haut_vag": 2.5, "per_moy_vag": 5.0, "dir_vag": 230.0},
        {"geo_id_wmo": "1", "validity_time": "2026-11-18T07:30:00Z", "haut_vag": 9.9, "per_moy_vag": 5.0, "dir_vag": 230.0},
        {"geo_id_wmo": "1", "validity_time": "2026-11-18T08:00:00Z", "haut_vag": None, "per_moy_vag": 5.0, "dir_vag": 230.0},
        {"geo_id_wmo": "1", "validity_time": "2026-11-18T09:00:00Z", "haut_vag": 1.0, "per_moy_vag": -10, "dir_vag": None},
    ]
    waves = tool.hourly_waves(rows)["1"]
    assert sorted(waves.values()) == [(1.0, None, None), (2.5, 5.0, 230.0)]


def test_nearest_water_cell_skips_land_and_gives_up_far_out():
    field = np.full((tool.NJ, tool.NI), np.nan, dtype=np.float32)
    # The cell of (45.0 N, 5 W): row (53 - 45) / 0.025 = 320, column (355 - 352) / 0.025 = 120.
    assert tool.nearest_water_cell(field, 45.0, -5.0) is None
    field[322, 120] = 1.5
    assert tool.nearest_water_cell(field, 45.0, -5.0) == (322, 120, 2.0)
    field[320, 121] = 1.0
    assert tool.nearest_water_cell(field, 45.0, -5.0) == (320, 121, 1.0)


def test_stats_of_a_biased_forecast():
    obs = np.array([1.0, 2.0, 3.0, np.nan])
    s = tool.stats(obs + 0.5, obs)
    assert s["n"] == 3 and s["bias"] == pytest.approx(0.5) and s["mae"] == pytest.approx(0.5) and s["rmse"] == pytest.approx(0.5)
    assert s["r"] == pytest.approx(1.0)
    assert tool.stats(obs, np.full(4, np.nan)) == {"n": 0}
    assert tool.stats(obs + 1.0, obs, obs >= 2.0)["n"] == 2


def stat(rmse: float, n: int = 50) -> dict:
    return {"n": n, "rmse": rmse}


def test_the_candidate_must_be_five_percent_better_and_not_worse_in_storms():
    assert tool.verdict(stat(0.94), stat(1.0), stat(1.0), stat(1.0))
    assert not tool.verdict(stat(0.96), stat(1.0), stat(1.0), stat(1.0))  # less than 5 % better
    assert not tool.verdict(stat(0.5), stat(1.0), stat(1.2), stat(1.0))  # better overall, worse in the storm hours
    assert tool.verdict(stat(0.5), stat(1.0), {"n": 0}, {"n": 0})  # no storm hour to judge
    assert not tool.verdict(stat(0.1, n=10), stat(1.0), stat(0.1), stat(1.0))  # not enough hours: described, not judged
