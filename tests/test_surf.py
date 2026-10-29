"""The surf forecast (``app/collectors/surf.py``, ``app/services/surf.py``, ``GET /v1/surf/spots``): real answers of Open-Meteo for
Hossegor (``tests/fixtures/surf``), the rating of ours and its rules, the routes. No network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.attribution import CREDITS
from app.collectors.surf import FORECAST_DAYS, MARINE_MODEL, SurfForecastCollector, parse_swell, parse_wind
from app.main import aggregator, app
from app.models.surf import SurfTide, SurfWind, Swell
from app.services import surf
from tests.helpers import Provider

FIXTURES = Path(__file__).parent / "fixtures" / "surf"
SWELL = json.loads((FIXTURES / "mfwam-hossegor.json").read_text(encoding="utf-8"))
WIND = json.loads((FIXTURES / "wind-hossegor.json").read_text(encoding="utf-8"))
FIRST_HOUR = 1793145600  # 2026-10-28 00:00 UTC, the first hour of both answers
NOW = FIRST_HOUR + 10 * 3600 + 600  # 10:10 UTC

SPOT_FILE: dict[str, Any] = {
    "built_utc": "2026-10-28T22:00:00Z",
    "source": "test",
    "licence": "ODbL-1.0",
    "attribution": "© OpenStreetMap contributors",
    "derived": ["facing_deg", "place", "id"],
    "spots": [
        {
            "id": "hossegor-plage-centrale",
            "name": "Plage Centrale",
            "place": "Hossegor",
            "lat": 43.6644,
            "lon": -1.4432,
            "facing_deg": 280,
            "beach_surface": "sand",
            "bottom": None,
            "best_tide": None,
            "best_swell_from_deg": None,
            "osm": "way/1",
            "evidence": [{"osm": "node/2", "name": "Hossegor Surf Club", "kind": "school"}],
        }
    ],
}


@pytest.fixture
def spot_file(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(surf, "spot_file", lambda: SPOT_FILE)
    return SPOT_FILE


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, spot_file: dict[str, Any]) -> TestClient:
    monkeypatch.setattr(clock, "_wall", lambda: float(NOW))
    return TestClient(app)


# ------------------------------------------------------------------------------------------------------------- the answers


def test_the_swell_and_the_wind_are_read_as_given() -> None:
    swell = parse_swell(SWELL)
    assert len(swell["times"]) == 168 and swell["times"][0] == FIRST_HOUR
    assert swell["swell_wave_height"][0] == 2.2 and swell["swell_wave_direction"][0] == 294
    assert swell["secondary_swell_wave_height"][0] == 0.0  # what the model gave: no invented secondary swell, none removed
    assert (swell["model_lat"], swell["model_lon"]) == (43.625, pytest.approx(-1.4583282))
    wind = parse_wind(WIND)
    assert len(wind["times"]) == 168 and wind["wind_speed_10m"][0] == 6.4 and wind["wind_direction_10m"][0] == 43
    assert len(wind["sunrise"]) == len(wind["sunset"]) == 7 and wind["sunrise"][0] < wind["sunset"][0]


def test_an_answer_without_swell_or_wind_raises_and_a_gap_stays_unknown() -> None:
    with pytest.raises(ValueError):
        parse_swell({"hourly": {"time": [1, 2], "swell_wave_height": [None, None], "wave_height": [None, None]}})
    with pytest.raises(ValueError):
        parse_swell({"hourly": {"time": ["2026-10-28T00:00"]}})
    with pytest.raises(ValueError):
        parse_wind({"hourly": {"time": [1], "wind_speed_10m": [None]}})
    swell = parse_swell({"hourly": {"time": [1, 2], "swell_wave_height": [1.0]}})
    assert swell["swell_wave_height"] == [1.0, None] and swell["swell_wave_period"] == [None, None]


@pytest.mark.asyncio
async def test_the_collector_asks_mfwam_for_seven_days_once_per_lifetime_whatever_the_days() -> None:
    provider = Provider(answer=lambda request: httpx.Response(200, json=SWELL if "marine" in request.url.host else WIND))
    collector = SurfForecastCollector(http_client=provider.client())
    for _ in range(3):
        await collector.fetch_swell("hossegor", 43.6644, -1.4432)
        await collector.fetch_wind("hossegor", 43.6644, -1.4432)
    assert provider.count == 2  # one swell, one wind: the cache holds them, whatever was asked after
    swell_query = next(dict(r.url.params) for r in provider.requests if "marine" in r.url.host)
    wind_query = next(dict(r.url.params) for r in provider.requests if "marine" not in r.url.host)
    assert swell_query["models"] == MARINE_MODEL and swell_query["forecast_days"] == str(FORECAST_DAYS)
    assert swell_query["timeformat"] == "unixtime" and "secondary_swell_wave_height" in swell_query["hourly"]
    assert wind_query["daily"] == "sunrise,sunset" and "models" not in wind_query
    # Under the Open-Meteo rule each request weighs one call (10 and 5 variables, 7 days, one model).
    assert collector.call_weight(swell_query) == 1.0 and collector.call_weight(wind_query) == 1.0
    assert collector.SWELL_TTL_SECONDS == 3 * 3600 and collector.WIND_TTL_SECONDS == 3600


# ------------------------------------------------------------------------------------------------------------- the rules


def test_the_wind_relative_to_the_coast() -> None:
    facing = 280  # a beach of the Landes, facing west-north-west
    assert surf.wind_relative(100, facing) == "offshore"  # from the land
    assert surf.wind_relative(56, facing) == "offshore"  # 44 degrees off
    assert surf.wind_relative(280, facing) == "onshore"
    assert surf.wind_relative(10, facing) == "cross-shore"
    assert surf.wind_relative(None, facing) is None
    assert surf.angle_between(350, 10) == 20 and surf.angle_between(10, 350) == 20 and surf.angle_between(0, 180) == 180


def test_the_power_of_the_swell() -> None:
    assert surf.swell_power_kw_m(2.0, 10.0) == pytest.approx(19.6, abs=0.05)  # 0.49 x 4 x 10
    assert surf.swell_power_kw_m(None, 10.0) is None and surf.swell_power_kw_m(1.0, None) is None


def test_the_rating_gives_each_criterion_its_points_and_leaves_out_the_unknown() -> None:
    swell = Swell(height_m=1.6, period_s=12.0, from_deg=290)
    offshore = SurfWind(speed_kmh=15.0, gust_kmh=25.0, from_deg=100, relative="offshore")
    tide = SurfTide(height_m=0.4, trend="rising", stage="mid")
    rating = surf.rate(swell, 10.0, offshore, tide, None)
    assert rating is not None
    points = {c.name: c.points for c in rating.criteria}
    assert points == {"swell_height": 2, "swell_period": 2, "swell_direction": 2, "wind": 2, "tide": None}
    assert (rating.points, rating.out_of, rating.unknown) == (8, 8, ["tide"])
    # The spot's best tide, when someone gives it, is scored.
    assert {c.name: c.points for c in surf.rate(swell, 10.0, offshore, tide, "low").criteria}["tide"] == 0  # type: ignore[union-attr]
    assert {c.name: c.points for c in surf.rate(swell, 10.0, offshore, tide, "mid").criteria}["tide"] == 2  # type: ignore[union-attr]
    # Onshore and strong, small and short, sideways.
    poor = surf.rate(Swell(height_m=0.4, period_s=6.0), 75.0, SurfWind(speed_kmh=30.0, relative="onshore"), None, None)
    assert poor is not None and (poor.points, poor.out_of) == (0, 8)
    # The wind unknown: left out, said so; a light wind needs no direction.
    no_wind = surf.rate(swell, 10.0, SurfWind(), None, None)
    assert no_wind is not None and no_wind.out_of == 6 and no_wind.unknown == ["wind", "tide"]
    light = surf.rate(swell, 10.0, SurfWind(speed_kmh=5.0), None, None)
    assert light is not None and {c.name: c.points for c in light.criteria}["wind"] == 2
    # Without the height of the swell there is no rating at all.
    assert surf.rate(Swell(period_s=12.0), 10.0, offshore, None, None) is None
    # The bands, at their edges.
    assert surf._band(0.5, surf.SWELL_HEIGHT_BANDS) == 1 and surf._band(4.0, surf.SWELL_HEIGHT_BANDS) == 0
    assert surf._band(11.0, surf.SWELL_PERIOD_BANDS) == 2 and surf._band(60.0, surf.SWELL_ANGLE_BANDS) == 0
    assert set(surf.RULES) == {"swell_height", "swell_period", "swell_direction", "wind", "tide", "best_slot"}


# ------------------------------------------------------------------------------------------------------------- the forecast


def test_the_forecast_of_hossegor(spot_file: dict[str, Any]) -> None:
    spot = surf.find_spot("hossegor-plage-centrale")
    forecast = surf.build_forecast(spot, parse_swell(SWELL), parse_wind(WIND), 3, NOW)
    assert len(forecast.hours) == 72
    first = forecast.hours[0]
    assert first.time_utc == FIRST_HOUR + 10 * 3600
    index = 10
    assert first.swell.height_m == SWELL["hourly"]["swell_wave_height"][index]
    assert first.swell.from_deg == SWELL["hourly"]["swell_wave_direction"][index]
    assert first.wind.speed_kmh == WIND["hourly"]["wind_speed_10m"][index]
    assert first.swell_angle_deg == round(surf.angle_between(first.swell.from_deg or 0, 280))
    assert first.wind.relative == surf.wind_relative(first.wind.from_deg, 280)
    # Daylight: between the sunrise and the sunset of the day, as Open-Meteo gave them.
    sunrise, sunset = WIND["daily"]["sunrise"][0], WIND["daily"]["sunset"][0]
    for hour in forecast.hours[:14]:
        assert hour.daylight is (sunrise <= hour.time_utc < sunset)
    # The tide is computed at the spot (EOT20): a range of a few metres on the Basque coast, rising then falling.
    tides_here = [h.tide for h in forecast.hours if h.tide is not None]
    assert len(tides_here) == 72
    heights = [t.height_m for t in tides_here]
    assert 1.0 < max(heights) - min(heights) < 5.0
    assert {t.trend for t in tides_here} == {"rising", "falling"} and {t.stage for t in tides_here} == {"low", "mid", "high"}
    # The best slot is a daylight hour of the next 72 with no better one before it.
    slot = forecast.best_slot
    assert slot is not None
    chosen = next(h for h in forecast.hours if h.time_utc == slot.time_utc)
    assert chosen.daylight is True and chosen.rating is not None
    share = chosen.rating.points / chosen.rating.out_of
    for hour in forecast.hours:
        if hour.daylight and hour.rating and hour.rating.out_of >= 6:
            assert hour.rating.points / hour.rating.out_of <= share
            if hour.time_utc < slot.time_utc:
                assert hour.rating.points / hour.rating.out_of < share
    # Every block is credited, and what is ours is named.
    assert CREDITS["copernicus_mfwam"].text in forecast.attribution and CREDITS["osm_spots"].text in forecast.attribution
    assert CREDITS["eot20"].text in forecast.attribution and CREDITS["open_meteo"].text in forecast.attribution
    assert "hours.rating" in forecast.derived and "best_slot" in forecast.derived
    assert forecast.swell_cell == {"lat": 43.625, "lon": pytest.approx(-1.4583282)}
    assert "Not for safety" in forecast.disclaimer


def test_without_the_wind_the_forecast_says_it_does_not_know(spot_file: dict[str, Any]) -> None:
    spot = surf.find_spot("hossegor-plage-centrale")
    forecast = surf.build_forecast(spot, parse_swell(SWELL), None, 1, NOW)
    assert len(forecast.hours) == 24
    assert all(h.wind.speed_kmh is None and h.wind.relative is None and h.daylight is None for h in forecast.hours)
    assert all(h.rating is not None and "wind" in h.rating.unknown for h in forecast.hours)
    assert forecast.best_slot is None  # daylight unknown: no slot is offered


# ------------------------------------------------------------------------------------------------------------- the routes


def test_the_spots_route(client: TestClient) -> None:
    body = client.get("/v1/surf/spots").json()
    assert body["count"] == 1 and body["licence"] == "ODbL-1.0"
    assert body["spots"][0]["facing_deg"] == 280 and body["spots"][0]["bottom"] is None
    assert body["attribution"] == CREDITS["osm_spots"].text


def test_the_forecast_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aggregator.surf, "fetch_swell", AsyncMock(return_value=(parse_swell(SWELL), False)))
    monkeypatch.setattr(aggregator.surf, "fetch_wind", AsyncMock(return_value=(parse_wind(WIND), True)))
    response = client.get("/v1/surf/spots/hossegor-plage-centrale", params={"days": 2})
    assert response.status_code == 200
    body = response.json()
    assert len(body["hours"]) == 48 and body["stale"] is True and body["days"] == 2
    assert body["rules"]["wind"].startswith("under 10 km/h: 2")
    assert response.headers["cache-control"] == "public, max-age=600"
    assert client.get("/v1/surf/spots/nowhere").status_code == 404
    assert client.get("/v1/surf/spots/hossegor-plage-centrale", params={"days": 8}).status_code == 422
    monkeypatch.setattr(aggregator.surf, "fetch_swell", AsyncMock(side_effect=RuntimeError("down")))
    answer = client.get("/v1/surf/spots/hossegor-plage-centrale")
    assert answer.status_code == 503 and "no answer" in answer.json()["detail"]


def test_the_bundled_spot_file_is_sound() -> None:
    data = json.loads(surf.SPOTS_FILE.read_text(encoding="utf-8"))
    assert data["licence"] == "ODbL-1.0" and data["attribution"] == "© OpenStreetMap contributors"
    ids = [s["id"] for s in data["spots"]]
    assert len(ids) == len(set(ids)) >= 40
    for spot in data["spots"]:
        assert 43.3 <= spot["lat"] <= 51.2 and -5.3 <= spot["lon"] <= 2.7, spot["id"]
        assert 0 <= spot["facing_deg"] < 360
        assert spot["evidence"] and all(e["kind"] in ("spot", "school") for e in spot["evidence"])
        # Nothing no open source gives: unknown until someone gives it with a source.
        assert spot["bottom"] is None and spot["best_tide"] is None and spot["best_swell_from_deg"] is None
