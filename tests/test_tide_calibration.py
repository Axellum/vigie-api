"""The calibration of the tide against REFMAR (ADR 0019): the file, how it is applied, and the arithmetic of the tool that makes it.

Nothing here reads the network: the observations are a curve worked out from the model itself with a known error.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.main import app
from app.services import tides
from app.services.tides import find_extremes, mjd_from_unix
from tools import calibrate_tides as tool

NOW = 1793188800  # 2026-10-28 12:00 UTC
BREST = {"lat": 48.38, "lon": -4.5, "days": 1, "interval_minutes": 10}
LORIENT = {"lat": 47.7375, "lon": -3.3545, "days": 1, "interval_minutes": 10}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(clock, "_wall", lambda: float(NOW))
    return TestClient(app)


def _file() -> dict:
    return json.loads(tides.CALIBRATION_FILE.read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------------------- the file


def test_the_file_says_where_it_comes_from_and_how() -> None:
    data = _file()
    assert "Licence Ouverte 2.0" in data["source"] and "SHOM" in data["source"]
    assert data["fit_period"][1] < data["test_period"][0], "the test period must come after the fit period"
    assert data["stations"], "a station that was measured is listed, applied or not"


def test_an_applied_station_met_the_criterion_of_the_adr() -> None:
    for station in _file()["stations"]:
        if not station["applied"]:
            assert station["reasons"], "a station that is not applied says why"
            continue
        assert station["reasons"] == []
        assert tool.AMPLITUDE_RANGE[0] <= station["amplitude"] <= tool.AMPLITUDE_RANGE[1]
        assert abs(station["delay_minutes"]) <= tool.MAX_DELAY_MIN
        for data in (station["cross_validation"], station["final_test"]):
            before, after = data["before"], data["after"]
            assert after["rms_m"] <= (1 - tool.MIN_RMS_GAIN) * before["rms_m"]
            assert after["time_error_median_min"] <= before["time_error_median_min"]
            assert after["height_error_median_m"] <= before["height_error_median_m"]


def test_only_applied_stations_are_served() -> None:
    applied = {s["station_id"] for s in _file()["stations"] if s["applied"]}
    assert {c.station_id for c in tides.calibrations()} == applied


# ----------------------------------------------------------------------------------------------------- how it is applied


def test_a_calibrated_point_is_the_model_with_the_amplitude_and_the_delay(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calibrated = client.get("/v1/tides", params=BREST).json()
    station = calibrated["calibration"]
    assert station["applied"] is True and station["station_id"] == "3"
    assert "calibration" in calibrated["derived"] and "REFMAR" in calibrated["attribution"]
    monkeypatch.setattr(tides, "calibrations", lambda *a, **k: ())
    plain = client.get("/v1/tides", params=BREST).json()
    assert plain["calibration"] == {"applied": False} | {k: None for k in station if k != "applied"}
    assert "calibration" not in plain["derived"] and "REFMAR" not in plain["attribution"]
    # The same high and low waters, moved by the delay and scaled by the amplitude (the answer is rounded to a minute and a centimetre).
    assert len(calibrated["extremes"]) == len(plain["extremes"])
    for with_, without in zip(calibrated["extremes"], plain["extremes"], strict=True):
        assert abs((with_["time_utc"] - without["time_utc"]) - station["delay_minutes"] * 60) <= 120
        assert abs(with_["height_m"] - station["amplitude"] * without["height_m"]) <= 0.02


def test_a_point_far_from_every_applied_station_gets_the_plain_model(client: TestClient) -> None:
    body = client.get("/v1/tides", params=LORIENT).json()
    assert body["calibration"]["applied"] is False
    assert body["calibration"]["station_id"] is None
    assert "calibration" not in body["derived"]


def test_the_radius_is_kept() -> None:
    assert tides.calibration_for(48.3829, -4.495) is not None
    assert tides.calibration_for(48.3829 + 0.2, -4.495) is None  # about 22 km north


def test_the_curve_is_amplitude_times_the_model_a_delay_earlier() -> None:
    model = tides.model()
    cell = model.nearest_cell(48.3829, -4.495)
    calibration = tides.calibration_for(48.3829, -4.495)
    assert calibration is not None
    times = mjd_from_unix(NOW) + np.arange(0, 1, 1 / 144)
    expected = calibration.amplitude * model.heights(cell.constants, times - calibration.delay_minutes / 1440.0)
    assert np.allclose(tides.calibrated_heights(model, cell.constants, times, calibration), expected)
    assert np.allclose(tides.calibrated_heights(model, cell.constants, times, None), model.heights(cell.constants, times))


# ------------------------------------------------------------------------------------------------------------ the tool


def test_the_fit_finds_a_known_amplitude_and_delay() -> None:
    model = tides.model()
    cell = model.nearest_cell(48.3829, -4.495)
    grid = mjd_from_unix(NOW) + np.arange(0, 60 * 144) / 144.0
    observed = 1.04 * tool.model_at(model, cell.constants, grid - 12 / 1440.0) + 3.2  # a datum difference of 3.2 m changes nothing
    found = tool.fit(grid, observed, model, cell.constants, np.ones(len(grid), dtype=bool))
    assert found is not None
    assert found[0] == pytest.approx(1.04, abs=0.002)
    assert found[1] == 12


def test_corrected_extremes_follow_the_corrected_curve() -> None:
    model = tides.model()
    cell = model.nearest_cell(48.3829, -4.495)
    minutes = mjd_from_unix(NOW) + np.arange(0, 3 * 1440) / 1440.0
    plain = find_extremes(minutes, model.heights(cell.constants, minutes))
    shifted = find_extremes(minutes, 1.05 * model.heights(cell.constants, minutes - 9 / 1440.0))
    moved = tool.correct_extremes(plain, 1.05, 9.0)
    for a, b in zip(shifted[1:-1], moved[1:-1], strict=False):
        assert a.kind == b.kind and abs(a.mjd - b.mjd) * 1440 < 0.5 and abs(a.height_m - b.height_m) < 0.005


def _figures(rms: float, time: float, height: float) -> dict:
    return {"rms_m": rms, "time_error_median_min": time, "height_error_median_m": height}


def test_the_decision_wants_a_gain_on_both_held_out_sets() -> None:
    good = {"before": _figures(0.20, 8.0, 0.10), "after": _figures(0.17, 6.0, 0.08)}
    assert tool.decide(good, good, [True, True, True, False], 1.02, 5.0) == []
    flat = {"before": _figures(0.20, 8.0, 0.10), "after": _figures(0.195, 6.0, 0.08)}
    assert any("RMS falls by less" in r for r in tool.decide(good, flat, [True] * 4, 1.02, 5.0))
    worse = {"before": _figures(0.20, 8.0, 0.10), "after": _figures(0.15, 9.0, 0.08)}
    assert any("time error rises" in r for r in tool.decide(worse, good, [True] * 4, 1.02, 5.0))
    assert any("only 2 of 4" in r for r in tool.decide(good, good, [True, True, False, False], 1.02, 5.0))
    assert tool.decide(good, good, [True] * 4, 1.3, 5.0) and tool.decide(good, good, [True] * 4, 1.0, 40.0)


def test_the_tool_asks_for_requests_of_16_days_and_pauses() -> None:
    assert tool.CHUNK_DAYS == 16 < 31 and tool.PAUSE_SECONDS >= 1.0
    assert tool.OUTPUT == Path(tides.CALIBRATION_FILE)
