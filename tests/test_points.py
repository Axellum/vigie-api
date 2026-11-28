"""The weather of several places in one request (``GET /v1/points``, ADR 0047), for the weather icons of a map.

Zero network calls: every provider is simulated with ``httpx.MockTransport`` or mocked.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.collectors.weather import OpenMeteoCollector
from app.models.schemas import POINTS_MAX, DeviceType
from app.services import aggregator as aggregator_module
from tests.conftest import World
from tests.helpers import SimClock, make_service
from tests.test_point_elevation import CHAMONIX, open_meteo_router

ANNECY = (45.9, 6.13)  # another cell than Chamonix (0.05 deg grid)
CHAMONIX_NEXT_DOOR = (45.921, 6.871)  # the same forecast cell as Chamonix


async def test_each_place_has_the_weather_of_v1_point_there(sim_clock: SimClock, tmp_path: Path) -> None:
    provider = open_meteo_router()
    service = make_service(tmp_path / "points.db", open_meteo=OpenMeteoCollector(http_client=provider.client()))

    answer = await service.build_points_response([CHAMONIX, ANNECY], hours=6, days=3)
    point = await service.build_point_response(*CHAMONIX, device_type=DeviceType.GENERIC, compact=False)

    assert answer.hours == 6 and answer.days == 3
    assert [(p.lat, p.lon) for p in answer.places] == [CHAMONIX, ANNECY]  # the order asked
    chamonix = answer.places[0]
    assert point.current is not None and chamonix.current is not None and chamonix.current.temp_c is not None
    assert chamonix.current.model_dump(exclude={"aqi_eu"}) == point.current.model_dump(exclude={"aqi_eu"})  # air: /v1/point only
    assert chamonix.weather_provider == point.weather_provider and chamonix.utc_offset_s == point.utc_offset_s
    assert point.hourly is not None and len(chamonix.hourly) == 6
    # The hours from the current one, field by field those of /v1/point.
    for mine, theirs in zip(chamonix.hourly, point.hourly[:6], strict=True):
        assert mine.model_dump() == theirs.model_dump(include=set(mine.model_dump()))
    assert chamonix.daily == point.daily[:3]
    assert answer.attribution and point.weather_attribution and "Open-Meteo" in answer.attribution


async def test_two_places_of_one_cell_cost_one_reading(sim_clock: SimClock, tmp_path: Path) -> None:
    provider = open_meteo_router()
    service = make_service(tmp_path / "cell.db", open_meteo=OpenMeteoCollector(http_client=provider.client()))

    answer = await service.build_points_response([CHAMONIX, CHAMONIX_NEXT_DOOR])

    assert len(provider.requests) == 3  # now, daily, hourly, once for the two places
    first, second = answer.places
    assert first.current == second.current and first.hourly == second.hourly
    assert (second.lat, second.lon) == CHAMONIX_NEXT_DOOR  # each place keeps the position asked

    await service.build_points_response([CHAMONIX])
    assert len(provider.requests) == 3  # and a place asked again comes from the cache


async def test_a_place_that_fails_or_is_too_slow_has_no_weather_and_the_others_keep_theirs(
    sim_clock: SimClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service(tmp_path / "fail.db", open_meteo=OpenMeteoCollector(http_client=open_meteo_router().client()))
    real = service.fetch_weather_with_fallback

    async def reading(lat: float, lon: float, **kw: Any) -> Any:
        if (lat, lon) == ANNECY:
            raise RuntimeError("down")
        if lat < 0:
            await asyncio.sleep(1)  # longer than the bound set below
        return await real(lat, lon, **kw)

    monkeypatch.setattr(service, "fetch_weather_with_fallback", reading)
    monkeypatch.setattr(aggregator_module, "POINTS_TIMEOUT_S", 0.05)

    answer = await service.build_points_response([CHAMONIX, ANNECY, (-33.9, 18.4)])

    good, failed, slow = answer.places
    assert good.current is not None and good.hourly
    for place in (failed, slow):
        assert place.current is None and place.hourly == [] and place.daily == [] and place.weather_provider is None


# ------------------------------------------------------------------------------------------------ the route


def test_the_route_answers_each_place_in_the_order_asked(world: World) -> None:
    response = world.client.get("/v1/points", params=[("at", "40.4168,-3.7038"), ("at", "52.52,13.405"), ("days", "2")])
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "1.0" and body["days"] == 2 and body["hours"] == 36
    assert [(p["lat"], p["lon"]) for p in body["places"]] == [(40.4168, -3.7038), (52.52, 13.405)]
    for place in body["places"]:
        assert place["current"] is not None and place["weather_provider"]
        assert len(place["daily"]) <= 2
    assert "nearby_hazards" not in body["places"][0]  # the weather only: hazards stay in /v1/point


def test_the_route_documents_itself_in_openapi(world: World) -> None:
    spec = world.client.get("/openapi.json").json()
    route = spec["paths"]["/v1/points"]["get"]
    assert "Embedded & GPS" in route["tags"]
    assert {p["name"] for p in route["parameters"]} == {"at", "hours", "days"}


@pytest.mark.parametrize(
    "params",
    [
        [],
        [("at", "abc")],
        [("at", "48.8")],
        [("at", "48.8,2.3,1")],
        [("at", "91,0")],
        [("at", "0,181")],
        [("at", "nan,nan")],
        [("at", "inf,0")],
        [("at", "48.8,2.3"), ("hours", "0")],
        [("at", "48.8,2.3"), ("hours", "73")],
        [("at", "48.8,2.3"), ("days", "15")],
        [("at", f"{45 + k / 100},6") for k in range(POINTS_MAX + 1)],
    ],
)
def test_the_route_refuses_what_is_not_1_to_40_positions(world: World, params: list[tuple[str, str]]) -> None:
    assert world.client.get("/v1/points", params=params).status_code == 422
