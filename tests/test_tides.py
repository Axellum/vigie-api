"""The tide computed from EOT20 (``app/services/tides.py``, ``GET /v1/tides``, ``GET /v1/tides/coast``; ADR 0015).

The prediction is checked against pyTMD's own (``tests/fixtures/tides/pytmd-reference.json``); the rest is arithmetic on curves worked
out by hand. Nothing here reads the network: the tide is computed from the files of ``app/data``.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.attribution import CREDITS
from app.main import app
from app.services import tides
from app.services.tides import Extreme, NoTideHere, estimated_coefficients, find_extremes, mjd_from_unix

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tides"
# 2026-10-28 12:00 UTC
NOW = 1793188800


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(clock, "_wall", lambda: float(NOW))
    return TestClient(app)


# ------------------------------------------------------------------------------------------------------------- the model


def test_the_prediction_is_pytmds_within_five_millimetres() -> None:
    reference = json.loads((FIXTURES / "pytmd-reference.json").read_text(encoding="utf-8"))
    model = tides.model()
    for series in reference["series"]:
        cell = model.nearest_cell(series["cell_lat"], series["cell_lon"])
        assert (cell.lat, cell.lon) == (series["cell_lat"], series["cell_lon"])
        times = mjd_from_unix(series["start_utc"]) + np.arange(len(series["heights_mm"])) * reference["step_s"] / 86400.0
        ours = model.heights(cell.constants, times) * 1000.0
        assert np.abs(ours - np.array(series["heights_mm"])).max() < 5.0, series["name"]


def test_several_cells_at_once_give_the_same_heights_as_one_by_one() -> None:
    model = tides.model()
    times = mjd_from_unix(NOW) + np.arange(0, 1, 1 / 48)
    cells = [model.nearest_cell(48.38, -4.5), model.nearest_cell(43.48, -1.56)]
    together = model.heights(np.stack([cell.constants for cell in cells]), times)
    for row, cell in zip(together, cells, strict=True):
        assert np.allclose(row, model.heights(cell.constants, times))


def test_the_nearest_cell_is_within_the_radius_or_there_is_none() -> None:
    model = tides.model()
    brest = model.nearest_cell(48.3829, -4.4950)
    assert brest.distance_km < 2 and abs(brest.constants[model.constituents.index("m2")]) > 1.5  # Brest: M2 about 2 m
    with pytest.raises(NoTideHere):
        model.nearest_cell(48.85, 2.35)  # Paris
    with pytest.raises(NoTideHere):
        model.nearest_cell(69.65, 18.96)  # Tromso: beyond 66 N, EOT20 is FES2014b's fill and is not kept


def test_a_cell_across_the_antimeridian_is_found() -> None:
    model = tides.model()
    east, west = model.nearest_cell(-16.8, 179.99), model.nearest_cell(-16.8, -179.99)  # Vanua Levu, Fiji
    assert (east.lat, east.lon) == (west.lat, west.lon)


def test_no_cell_keeps_an_artefact_of_the_model() -> None:
    model = tides.model()
    constants = model.constants_at(np.arange(len(model.keys)))
    for name in ("s1", "t2"):
        assert np.abs(constants[:, model.constituents.index(name)]).max() <= 0.20  # ARTEFACT_CM of tools/build_tides.py


# ------------------------------------------------------------------------------------------------------- extremes, coefficient


def _curve(step_minutes: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    times = np.arange(0.0, 2.0, step_minutes / 1440.0)
    return times, 2.0 * np.cos(2 * np.pi * (times - 0.25) / 0.5)  # high waters at 0.25, 0.75, 1.25 and 1.75 days


def test_the_extremes_of_a_sinusoid_alternate_at_the_right_times() -> None:
    times, heights = _curve(10.0)  # coarse on purpose: the parabola puts each extreme back on the curve
    extremes = find_extremes(times, heights)
    assert [extreme.kind for extreme in extremes] == ["high", "low"] * 3 + ["high"]
    highs = [extreme for extreme in extremes if extreme.kind == "high"]
    assert [round(extreme.mjd * 1440) for extreme in highs] == [360, 1080, 1800, 2520]
    assert all(abs(extreme.height_m - 2.0) < 1e-3 for extreme in highs)


def test_a_ripple_is_not_a_tide() -> None:
    times, heights = _curve()
    ripple = 0.006 * np.sin(2 * np.pi * times / (40 / 1440.0)) * (np.abs(times - 0.25) < 0.04)
    extremes = find_extremes(times, heights + ripple)
    assert [extreme.kind for extreme in extremes] == ["high", "low"] * 3 + ["high"]
    assert abs(extremes[0].height_m - 2.0) < 0.01


def test_the_estimated_coefficient_is_the_range_over_six_metres_ten() -> None:
    extremes = [Extreme(0.0, "low", -3.0), Extreme(0.26, "high", 3.1), Extreme(0.52, "low", -2.9)]
    [(high, value)] = estimated_coefficients(extremes)
    assert high.kind == "high" and value == round(100 * (3.1 + 2.95) / 6.10)  # 99


# ---------------------------------------------------------------------------------------------------------------- the routes


def test_the_tide_at_biarritz(client: TestClient) -> None:
    response = client.get("/v1/tides", params={"lat": 43.4832, "lon": -1.5586, "days": 2, "interval_minutes": 60})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    body = response.json()
    assert body["model"] == "EOT20" and body["cell"]["distance_km"] <= 30
    assert body["query"]["start_utc"] == NOW // 3600 * 3600
    assert len(body["heights"]) == 2 * 24 + 1
    kinds = [extreme["kind"] for extreme in body["extremes"]]
    assert len(kinds) >= 6 and all(a != b for a, b in zip(kinds, kinds[1:], strict=False))
    assert all(extreme["range_m"] is not None for extreme in body["extremes"])
    first, second = body["extremes"][:2]
    assert second["range_m"] == pytest.approx(abs(second["height_m"] - first["height_m"]), abs=0.011)
    now = body["now"]
    assert now["trend"] in ("rising", "falling") and now["next_high"]["time_utc"] > NOW and now["next_low"]["time_utc"] > NOW
    assert all(20 <= item["value"] <= 120 for item in body["coefficients_estimated"]) and body["coefficients_estimated"]
    assert body["attribution"] == CREDITS["eot20"].text
    assert "not for navigation" in body["disclaimer"].lower() and "bay" in body["limits"]
    assert set(body["derived"]) == {"heights", "extremes", "now", "coefficients_estimated"}


def test_the_heights_rise_towards_the_next_high_water(client: TestClient) -> None:
    body = client.get("/v1/tides", params={"lat": 48.38, "lon": -4.5, "days": 1, "interval_minutes": 10}).json()
    heights = {item["time_utc"]: item["height_m"] for item in body["heights"]}
    for extreme in body["extremes"]:
        nearest = min(heights, key=lambda moment: abs(moment - extreme["time_utc"]))
        assert abs(heights[nearest] - extreme["height_m"]) < 0.1


def test_a_start_and_its_errors(client: TestClient) -> None:
    expected = int(datetime(2030, 6, 1, 6, 30, tzinfo=timezone.utc).timestamp())
    body = client.get("/v1/tides", params={"lat": 48.38, "lon": -4.5, "start": "2030-06-01T06:30", "days": 1}).json()
    assert body["query"]["start_utc"] == expected
    with_offset = client.get("/v1/tides", params={"lat": 48.38, "lon": -4.5, "start": "2030-06-01T08:30+02:00"}).json()
    assert with_offset["query"]["start_utc"] == expected
    assert client.get("/v1/tides", params={"lat": 48.38, "lon": -4.5, "start": "June 1st"}).status_code == 422
    assert client.get("/v1/tides", params={"lat": 48.38, "lon": -4.5, "start": "2050-01-01"}).status_code == 422
    assert client.get("/v1/tides", params={"lat": 48.38, "lon": -4.5, "days": 8}).status_code == 422
    assert client.get("/v1/tides", params={"lat": 48.38, "lon": -4.5, "interval_minutes": 7}).status_code == 422


def test_no_tide_inland(client: TestClient) -> None:
    response = client.get("/v1/tides", params={"lat": 48.85, "lon": 2.35})
    assert response.status_code == 404 and "within 30 km" in response.json()["detail"]


def test_the_tide_along_a_coast(client: TestClient) -> None:
    response = client.get("/v1/tides/coast", params={"bbox": "-5,43,0,48.5", "limit": 50})
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "FeatureCollection" and 0 < body["metadata"]["count"] <= 50
    for feature in body["features"]:
        lon, lat = feature["geometry"]["coordinates"]
        assert -5 <= lon <= 0 and 43 <= lat <= 48.5
        properties = feature["properties"]
        assert properties["trend"] in ("rising", "falling")
        assert properties["next_high"]["time_utc"] > NOW and properties["next_high"]["range_m"] is not None
    assert body["metadata"]["attribution"] == CREDITS["eot20"].text


def test_the_coast_across_the_antimeridian_and_a_bad_box(client: TestClient) -> None:
    body = client.get("/v1/tides/coast", params={"bbox": "178,-20,-178,-15"}).json()
    assert body["features"] and all(abs(feature["geometry"]["coordinates"][0]) >= 178 for feature in body["features"])
    assert client.get("/v1/tides/coast", params={"bbox": "1,2,3"}).status_code == 422


def test_the_coast_is_thinned_to_the_limit(client: TestClient) -> None:
    body = client.get("/v1/tides/coast", params={"bbox": "-180,-66,180,66", "limit": 200}).json()
    assert body["metadata"]["count"] <= 200 and body["metadata"]["spacing_deg"] > 1


def test_the_tide_files_are_small_enough_for_the_image() -> None:
    sizes = sum(path.stat().st_size for path in (tides.CONSTANTS_FILE, tides.ARGUMENTS_FILE))
    assert sizes < 3_000_000
    assert math.isclose(tides.model().step_deg, 0.125)
