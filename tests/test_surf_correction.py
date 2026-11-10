"""The correction of the sea height at eight surf spots, only if asked (ADR 0023, ``app/services/surf_correction.py``): the bundled factors are the
ones of the published measure, the corrected field is the only one that changes, every case where it is not applied says why. No network."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import clock
from app.attribution import CREDITS
from app.collectors.surf import parse_swell, parse_wind
from app.main import aggregator, app
from app.services import buoys, surf, surf_correction
from tools import measure_surf_error

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "surf"
SWELL = json.loads((FIXTURES / "mfwam-hossegor.json").read_text(encoding="utf-8"))
WIND = json.loads((FIXTURES / "wind-hossegor.json").read_text(encoding="utf-8"))
NOW = 1793145600 + 10 * 3600 + 600  # 2026-10-28 10:10 UTC, inside the fixtures

# The eight spots of ADR 0023 and the factor each one gets (the table of the ADR; ``app/data/surf-calibration.json`` is the source).
EIGHT = {
    "locquirec-plage-du-moulin-de-la-rive": 1.210,
    "locmaria-plouzane-plage-du-minou": 1.267,
    "camaret-sur-mer-plage-de-pen-hat": 1.267,
    "le-pouliguen-plage-de-la-govelle": 1.190,
    "ondres-plage-ondres-ocean": 1.159,
    "ondres-plage-du-metro": 1.159,
    "biarritz-plage-du-port-vieux": 1.234,
    "biarritz-plage-de-la-marbella": 1.234,
}
SPOT = "ondres-plage-ondres-ocean"
SPOT_CELL = (43.541664, -1.4583282)


def swell_answer(lat: float = SPOT_CELL[0], lon: float = SPOT_CELL[1]) -> dict[str, Any]:
    """The Hossegor answer of Open-Meteo, as if it came from the cell ``(lat, lon)``."""
    return parse_swell({**SWELL, "latitude": lat, "longitude": lon})


def forecast_of(spot_id: str = SPOT, correct: bool = False, swell: dict[str, Any] | None = None) -> Any:
    return surf.build_forecast(surf.find_spot(spot_id), swell or swell_answer(), parse_wind(WIND), 2, NOW, False, correct)


# --------------------------------------------------------------------------------------------------------------- the data


def test_the_bundled_factors_are_the_ones_of_the_published_measure() -> None:
    published = json.loads(gzip.decompress((ROOT / "docs" / "measurements" / "surf-error-2026-11-results.json.gz").read_bytes()))
    bundled = json.loads(surf_correction.CALIBRATION_FILE.read_text(encoding="utf-8"))
    assert bundled == measure_surf_error.calibration_of(published)


def test_the_spots_that_get_a_factor_are_the_eight_of_the_adr() -> None:
    assert {spot_id: found.factor for spot_id, found in surf_correction.factors().items()} == EIGHT


def test_every_pair_of_the_file_is_a_spot_and_the_buoy_the_service_names() -> None:
    bundled = json.loads(surf_correction.CALIBRATION_FILE.read_text(encoding="utf-8"))
    rows = {row.spot_id: row for row in buoys.spot_buoys().spots}
    assert len(bundled["pairs"]) == 20
    for entry in bundled["pairs"]:
        surf.find_spot(entry["spot_id"])  # raises for an id that is not a spot
        row = rows[entry["spot_id"]]
        assert row.comparable and row.real_time_buoy is not None
        assert entry["buoy_id"] == row.real_time_buoy.id and entry["distance_km"] == row.real_time_distance_km
        if entry["applied"]:
            assert 1.0 < entry["factor"] < 1.5 and entry["held_out_gain"] and all(g >= 0.05 for g in entry["held_out_gain"].values())
            assert entry["reasons"] == []
        else:
            assert entry["reasons"], f"{entry['spot_id']} is not applied and does not say why"


# --------------------------------------------------------------------------------------------------------------- the forecast


def test_without_asking_the_model_height_is_served_and_the_answer_says_it() -> None:
    forecast = forecast_of(correct=False)
    assert forecast.correction.applied is False and forecast.correction.reason == "not_asked"
    assert forecast.correction.factor is None and forecast.correction.left_as_modelled == []
    assert all(h.waves_model_height_m is None for h in forecast.hours)
    first = forecast.hours[0]
    assert first.waves.height_m == SWELL["hourly"]["wave_height"][10]
    assert surf_correction.CORRECTED_FIELD not in forecast.derived


def test_asked_the_height_of_the_whole_sea_is_multiplied_and_the_model_value_is_kept() -> None:
    forecast = forecast_of(correct=True)
    correction = forecast.correction
    assert correction.applied is True and correction.reason is None and correction.factor == 1.159
    assert correction.field == "hours.waves.height_m" == surf_correction.CORRECTED_FIELD
    assert correction.buoy is not None and (correction.buoy.id, correction.buoy.name) == ("06402", "Anglet")
    assert correction.buoy.distance_km == pytest.approx(11.37)
    assert correction.window == ["2025-12-01", "2026-10-31"]
    assert correction.held_out_gain is not None and set(correction.held_out_gain) == {"1", "3", "5"}
    assert correction.attribution == CREDITS["surf_height_factors"].text
    assert "closer to that buoy" in (correction.note or "") and correction.measure == "docs/measurements/surf-error-2026-11.md"
    assert surf_correction.CORRECTED_FIELD in forecast.derived
    for hour in forecast.hours:
        index = (hour.time_utc - 1793145600) // 3600
        raw = SWELL["hourly"]["wave_height"][index]
        assert hour.waves_model_height_m == round(raw, 2)
        assert hour.waves.height_m == round(raw * 1.159, 2)
        assert hour.waves.height_m > hour.waves_model_height_m  # the model reads low: a factor over 1 raises a sea that is not nil


def test_nothing_but_the_height_of_the_sea_changes() -> None:
    plain, corrected = forecast_of(correct=False), forecast_of(correct=True)
    a, b = plain.model_dump(), corrected.model_dump()
    for answer in (a, b):
        answer.pop("correction")
        answer.pop("derived")
        for hour in answer["hours"]:
            hour["waves"].pop("height_m")
            hour.pop("waves_model_height_m")
    assert a == b  # the swell, the periods, the power, the wind, the tide, the rating and the best slot are the model's


def test_a_gap_in_the_model_stays_a_gap() -> None:
    swell = swell_answer()
    swell["wave_height"] = [None if i == 10 else v for i, v in enumerate(swell["wave_height"])]
    first = forecast_of(correct=True, swell=swell).hours[0]
    assert first.waves.height_m is None and first.waves_model_height_m is None


@pytest.mark.parametrize("spot_id", ["soorts-hossegor-plage-sud-point-d-or", "guethary-uhabia", "royan-plage-de-pontaillac", "hendaye-ondarraitz"])
def test_a_spot_that_did_not_meet_the_criterion_is_not_corrected(spot_id: str) -> None:
    forecast = forecast_of(spot_id=spot_id, correct=True)
    assert forecast.correction.applied is False and forecast.correction.reason == "not_calibrated"
    assert all(h.waves_model_height_m is None for h in forecast.hours)
    assert forecast.hours[0].waves.height_m == SWELL["hourly"]["wave_height"][10]


def test_a_spot_with_no_buoy_near_is_not_corrected() -> None:
    other = next(s for s in surf.spots().spots if s.id not in {e.spot_id for e in buoys.spot_buoys().spots if e.comparable})
    assert forecast_of(spot_id=other.id, correct=True).correction.reason == "not_calibrated"


def test_the_factor_is_applied_only_on_the_cell_it_was_found_on() -> None:
    moved = forecast_of(correct=True, swell=swell_answer(lat=43.625))
    assert moved.correction.applied is False and moved.correction.reason == "other_cell"
    assert moved.hours[0].waves.height_m == SWELL["hourly"]["wave_height"][10] and moved.hours[0].waves_model_height_m is None
    nearby = forecast_of(correct=True, swell=swell_answer(lat=SPOT_CELL[0] + 0.000002))
    assert nearby.correction.applied is True  # the same cell, written with another rounding
    unknown = forecast_of(correct=True, swell={**swell_answer(), "model_lat": None, "model_lon": None})
    assert unknown.correction.applied is False and unknown.correction.reason == "cell_unknown"


# --------------------------------------------------------------------------------------------------------------- the route


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(clock, "_wall", lambda: float(NOW))
    monkeypatch.setattr(aggregator.surf, "fetch_swell", AsyncMock(return_value=(swell_answer(), False)))
    monkeypatch.setattr(aggregator.surf, "fetch_wind", AsyncMock(return_value=(parse_wind(WIND), False)))
    monkeypatch.delenv(surf_correction.DEFAULT_ON_BY, raising=False)
    return TestClient(app)


def test_the_route_does_not_correct_unless_asked(client: TestClient) -> None:
    body = client.get(f"/v1/surf/spots/{SPOT}", params={"days": 1}).json()
    assert body["correction"]["applied"] is False and body["correction"]["reason"] == "not_asked"
    assert body["hours"][0]["waves"]["height_m"] == SWELL["hourly"]["wave_height"][10] and body["hours"][0]["waves_model_height_m"] is None


def test_the_route_corrects_when_asked(client: TestClient) -> None:
    body = client.get(f"/v1/surf/spots/{SPOT}", params={"days": 1, "correct": "true"}).json()
    assert body["correction"]["applied"] is True and body["correction"]["factor"] == 1.159
    assert body["hours"][0]["waves_model_height_m"] == SWELL["hourly"]["wave_height"][10]
    assert body["hours"][0]["waves"]["height_m"] == round(SWELL["hourly"]["wave_height"][10] * 1.159, 2)
    assert body["correction"]["buoy"] == {"id": "06402", "name": "Anglet", "distance_km": 11.37}
    assert client.get(f"/v1/surf/spots/{SPOT}", params={"correct": "maybe"}).status_code == 422


def test_an_instance_can_apply_it_by_default_and_a_request_can_still_ask_for_the_model_alone(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(surf_correction.DEFAULT_ON_BY, "on")
    assert client.get(f"/v1/surf/spots/{SPOT}", params={"days": 1}).json()["correction"]["applied"] is True
    alone = client.get(f"/v1/surf/spots/{SPOT}", params={"days": 1, "correct": "false"}).json()
    assert alone["correction"]["applied"] is False and alone["correction"]["reason"] == "not_asked"
    monkeypatch.setenv(surf_correction.DEFAULT_ON_BY, "off")
    assert client.get(f"/v1/surf/spots/{SPOT}", params={"days": 1}).json()["correction"]["applied"] is False


def test_the_switch_of_the_instance_is_read_by_the_route_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the overview and the activity verdicts call (``build_forecast``) corrects nothing, whatever the instance says: the verdicts read the sea height of the same hours."""
    monkeypatch.setenv(surf_correction.DEFAULT_ON_BY, "on")
    forecast = surf.build_forecast(surf.find_spot(SPOT), swell_answer(), parse_wind(WIND), 1, NOW)
    assert forecast.correction.reason == "not_asked" and forecast.hours[0].waves.height_m == SWELL["hourly"]["wave_height"][10]
