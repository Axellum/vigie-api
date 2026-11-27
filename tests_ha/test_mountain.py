"""The stations of the card's Mountain page (``vigie/card/mountain``): the weather at the top and at the base of a station, each
at its altitude (``GET /v1/point?elevation=``), and its avalanche bulletin (``GET /v1/avalanches``), trimmed to what the page
draws and kept for 10 minutes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.vigie.const import MAX_STATIONS
from custom_components.vigie.weather_data import mountain_avalanches, mountain_point

from .conftest import recorded, setup_entry
from .payloads import URL, station_point

CHAMONIX = recorded("point_chamonix_2026-11-22.json")
AVALANCHES = recorded("avalanches_chamonix_2026-11-27.json")
STATION = {"lat": 45.92, "lon": 6.87, "base_m": 1350, "top_m": 2400}


@pytest.fixture(autouse=True)
def _frozen(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-11-22T20:20:00+00:00")


async def ask(hass_ws_client: WebSocketGenerator, hass: HomeAssistant, message: dict[str, Any]) -> dict[str, Any]:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(message)
    return await client.receive_json()


def mountain(aioclient_mock: AiohttpClientMocker, *, top: Any = None, base: Any = None, avalanches: Any = None, avalanches_status: int = 200) -> None:
    """The instance as a station reads it: the top at 2 400 m, the base at 1 350 m (or at the cell, without an altitude), the
    bulletin of Chamonix recorded on 2026-11-27."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/v1/point", params={"elevation": "2400"}, json=station_point(CHAMONIX, 2400, -8.0) if top is None else top)
    aioclient_mock.get(f"{URL}/v1/point", params={"elevation": "1350"}, json=station_point(CHAMONIX, 1350, -1.5) if base is None else base)
    aioclient_mock.get(f"{URL}/v1/point", json=CHAMONIX)  # no altitude asked: the cell's
    aioclient_mock.get(f"{URL}/v1/avalanches", json=AVALANCHES if avalanches is None else avalanches, status=avalanches_status)


def calls(aioclient_mock: AiohttpClientMocker, path: str) -> list[Any]:
    return [call[1] for call in aioclient_mock.mock_calls if call[1].path == path]


async def test_a_station_is_read_at_its_top_and_its_base_with_its_bulletin_then_kept_10_minutes(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    mock_instance()
    await setup_entry(hass, biarritz_entry)
    mountain(aioclient_mock)
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/mountain", **STATION})
    assert answer["success"], answer
    data = answer["result"]
    assert data["elevation_ignored"] is False and data["language"] == hass.config.language
    top, base = data["top"], data["base"]
    assert top["coordinates"]["elevation_m"] == 2400 and base["coordinates"]["elevation_m"] == 1350
    # Two reads of the point, at the two altitudes, without the sea; one of the bulletin, at the station.
    points = calls(aioclient_mock, "/v1/point")
    assert sorted(url.query["elevation"] for url in points) == ["1350", "2400"]
    assert all(url.query["marine"] == "false" and url.query["compact"] == "false" for url in points)
    assert [(url.query["lat"], url.query["lon"]) for url in calls(aioclient_mock, "/v1/avalanches")] == [("45.92000", "6.87000")]
    # Trimmed to what the page draws: no sea, no air, no nowcast; the hours with their freezing level.
    assert set(top) == {"coordinates", "utc_offset_s", "server_utc", "weather_provider", "weather_attribution", "current", "hourly", "daily"}
    assert top["hourly"][0]["temp_c"] == round(CHAMONIX["hourly"][0]["temp_c"] - 8.0, 1) and top["hourly"][0]["freezing_level_m"] == 1500
    assert "cloud_low_pct" not in top["hourly"][0] and top["daily"][0]["snowfall_cm"] == 15.0
    assert base["hourly"][0]["temp_c"] == round(CHAMONIX["hourly"][0]["temp_c"] - 1.5, 1)
    avalanches = data["avalanches"]
    assert avalanches["status"] == "active" and avalanches["region_name"] == "Mont-Blanc" and avalanches["max_danger_level"] == 3
    bulletin = avalanches["bulletins"][0]
    assert bulletin["danger_ratings"] == AVALANCHES["bulletins"][0]["danger_ratings"]
    assert [p["problem_type"] for p in bulletin["avalanche_problems"]] == ["wind_slab", "gliding_snow"]
    assert "snowpack_comment" not in bulletin and "comment" not in bulletin["tendency"][0]
    # A second ask, from another screen, within 10 minutes: nothing read again; 10 minutes later, the three again.
    await ask(hass_ws_client, hass, {"type": "vigie/card/mountain", **STATION})
    assert len(calls(aioclient_mock, "/v1/point")) == 2 and len(calls(aioclient_mock, "/v1/avalanches")) == 1
    station = next(iter(biarritz_entry.runtime_data.weather._stations.values()))
    for part in (station.top, station.base, station.avalanches):
        part.read_at -= 601
    await ask(hass_ws_client, hass, {"type": "vigie/card/mountain", **STATION})
    assert len(calls(aioclient_mock, "/v1/point")) == 4 and len(calls(aioclient_mock, "/v1/avalanches")) == 2


async def test_without_the_altitude_of_the_base_the_base_is_the_cell(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    mock_instance()
    await setup_entry(hass, biarritz_entry)
    mountain(aioclient_mock)
    data = (await ask(hass_ws_client, hass, {"type": "vigie/card/mountain", "lat": 45.92, "lon": 6.87, "top_m": 2400}))["result"]
    assert data["top"]["coordinates"]["elevation_m"] == 2400 and "elevation_m" not in data["base"]["coordinates"]
    assert sorted(url.query.get("elevation", "cell") for url in calls(aioclient_mock, "/v1/point")) == ["2400", "cell"]
    assert data["elevation_ignored"] is False


async def test_an_instance_older_than_the_altitude_is_said_and_never_drawn_as_top_and_base(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    """An instance older than ``elevation`` ignores it and answers at its cell's altitude: the top and the base would be alike."""
    mock_instance()
    await setup_entry(hass, biarritz_entry)
    mountain(aioclient_mock, top=CHAMONIX, base=CHAMONIX)
    data = (await ask(hass_ws_client, hass, {"type": "vigie/card/mountain", **STATION}))["result"]
    assert data["top"] is None and data["base"] is None and data["elevation_ignored"] is True
    assert data["avalanches"]["status"] == "active"  # the bulletin does not depend on it


async def test_an_instance_without_the_avalanche_route_gives_the_weather_alone(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    mock_instance()
    await setup_entry(hass, biarritz_entry)
    mountain(aioclient_mock, avalanches={"detail": "Not Found"}, avalanches_status=404)
    data = (await ask(hass_ws_client, hass, {"type": "vigie/card/mountain", **STATION}))["result"]
    assert data["avalanches"] is None and data["top"]["coordinates"]["elevation_m"] == 2400


@pytest.mark.parametrize(
    "station",
    [
        {"lat": 45.92, "lon": 6.87, "top_m": 9001},
        {"lat": 45.92, "lon": 6.87, "top_m": 2400, "base_m": -501},
        {"lat": 91, "lon": 6.87, "top_m": 2400},
        {"lat": 45.92, "lon": 181, "top_m": 2400},
        {"lat": 45.92, "lon": 6.87},
        {"lat": 45.92, "lon": 6.87, "top_m": "haut"},
    ],
)
async def test_an_impossible_station_is_refused_without_asking_the_instance(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
    station: dict[str, Any],
) -> None:
    mock_instance()
    await setup_entry(hass, biarritz_entry)
    mountain(aioclient_mock)
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/mountain", **station})
    assert not answer["success"] and answer["error"]["code"] == "invalid_format"
    assert not calls(aioclient_mock, "/v1/point") and not calls(aioclient_mock, "/v1/avalanches")


async def test_at_most_six_stations_are_kept_the_oldest_asked_goes_first(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    mock_instance()
    await setup_entry(hass, biarritz_entry)
    mountain(aioclient_mock)
    for k in range(MAX_STATIONS + 1):
        await ask(hass_ws_client, hass, {"type": "vigie/card/mountain", **STATION, "lat": 45.0 + k / 10})
    kept = biarritz_entry.runtime_data.weather._stations
    assert len(kept) == MAX_STATIONS and (45.0, 6.87, 1350, 2400) not in kept and (45.6, 6.87, 1350, 2400) in kept


def test_what_the_page_does_not_draw_is_left_out() -> None:
    assert mountain_point({"detail": "Not Found"}, 2400) is None and mountain_point(None, None) is None
    assert mountain_point(station_point(CHAMONIX, 2400, -8.0), 1350) is None  # answered at another altitude than asked
    assert mountain_avalanches("not an answer") is None
    expired = {**AVALANCHES, "bulletins": [{**AVALANCHES["bulletins"][0], "is_expired": True}]}
    assert mountain_avalanches(expired)["bulletins"] == []
    outside = {"status": "outside_coverage", "note": "…", "bulletins": [], "attribution": None}
    assert mountain_avalanches(outside) == {"status": "outside_coverage", "attribution": None, "bulletins": []}
