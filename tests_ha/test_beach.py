"""The beaches of the card's Sea page (``vigie/card/beach``): the forecast of a spot hour by hour (``GET /v1/surf/spots/{spot}``),
its verdicts (``GET /v1/activities/{spot}``), the water at its cell (``GET /v1/point`` with the sea) and its monitored bathing
site (``GET /v1/sea/places``), trimmed to what the page draws and kept for 30 minutes (the bathing site for a day).

The forecast, the verdicts and the bathing sites are real answers for the Plage des Corsaires, recorded on 2026-11-27 at
17:11 UTC; the clock is frozen just after."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.vigie.const import BEACH_HOURS, MAX_BEACHES
from custom_components.vigie.weather_data import bathing_site, beach_activities, beach_forecast

from .conftest import SPOT, recorded, setup_entry
from .payloads import URL

READ_UTC = 1_795_799_503  # 2026-11-27 17:11:43 UTC
FORECAST = recorded(f"surfspot_{SPOT}_2026-11-27.json")
ACTIVITIES = recorded(f"activities_{SPOT}_2026-11-27.json")
BATHING = recorded(f"seaplaces_{SPOT}_2026-11-27.json")
POINT = recorded("point_biarritz_2026-11-22.json")


@pytest.fixture(autouse=True)
def _frozen(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-11-27T17:12:00+00:00")


async def ask(hass_ws_client: WebSocketGenerator, hass: HomeAssistant, message: dict[str, Any]) -> dict[str, Any]:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(message)
    return await client.receive_json()


def beach(aioclient_mock: AiohttpClientMocker, *, bathing: Any = None, bathing_status: int = 200, forecast_status: int = 200) -> None:
    """The routes of a beach, as the instance answered them for the Plage des Corsaires (after ``mock_instance``)."""
    aioclient_mock.get(f"{URL}/v1/surf/spots/{SPOT}", json=FORECAST, status=forecast_status)
    aioclient_mock.get(f"{URL}/v1/sea/places", json=BATHING if bathing is None else bathing, status=bathing_status)


def calls(aioclient_mock: AiohttpClientMocker, path: str) -> list[Any]:
    return [call[1] for call in aioclient_mock.mock_calls if call[1].path == path]


async def test_a_beach_is_its_forecast_its_verdicts_its_water_and_its_bathing_site_then_kept_30_minutes(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    await setup_entry_with(hass, mock_instance, biarritz_entry, aioclient_mock)
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/beach", "spot": SPOT})
    assert answer["success"], answer
    data = answer["result"]
    assert data["language"] == hass.config.language
    # The spot: its name and distance (the list of the editor), its facing, bottom and lifeguards (the forecast).
    spot = data["spot"]
    assert spot["id"] == SPOT and spot["name"] == "Plage des Corsaires" and spot["distance_km"] > 0
    assert spot["facing_deg"] == 296 and spot["bottom"] == "sand" and spot["lifeguarded"] is True
    # Its hours: from the one under way to 27 hours ahead, without the ratings of the surf page.
    hours = data["forecast"]["hours"]
    assert hours[0]["time_utc"] <= READ_UTC < hours[0]["time_utc"] + 3600
    assert hours[-1]["time_utc"] <= READ_UTC + BEACH_HOURS * 3600 and len(hours) == BEACH_HOURS + 1
    assert set(hours[0]) == {"time_utc", "daylight", "waves", "swell", "wind", "tide"}
    assert hours[0]["wind"]["relative"] in {"onshore", "offshore", "cross_shore"} and "rating" not in hours[0]
    assert data["forecast"]["attribution"] == FORECAST["attribution"]
    # Its verdicts over the same hours, the tide of a day and a half, the sun.
    acts = data["activities"]
    assert [a["id"] for a in acts["activities"]] == ["surf", "kite", "boat", "shore_fishing"]
    assert all(len(a["hours"]) == len(hours) for a in acts["activities"])
    assert acts["tide"]["extremes"] and acts["tide"]["coefficients_estimated"] and acts["sun"]
    # The water of the ocean model at its cell, and the bathing site of the beach with its class.
    assert data["water"]["sea_surface_temperature_c"] == POINT["marine"]["sea_surface_temperature_c"]
    assert set(data["water"]) <= {"sea_surface_temperature_c", "sea_state_label", "wave_height_m", "stale", "attribution"}
    site = data["bathing"]
    assert site["name"] == "Les Corsaires" and site["quality"] == "excellent" and site["distance_km"] == 0.2
    assert "Qualité des sites de baignade" in site["attribution"]
    # What was asked: the forecast of two days, the point at the spot with the sea, the bathing sites within 2 km.
    assert [url.query["days"] for url in calls(aioclient_mock, f"/v1/surf/spots/{SPOT}")] == ["2"]
    assert [(url.query["lat"], url.query["lon"], url.query["marine"]) for url in calls(aioclient_mock, "/v1/point")][-1] == ("43.50740", "-1.53750", "true")
    sea = calls(aioclient_mock, "/v1/sea/places")
    assert [(url.query["type"], url.query["near_lat"], url.query["radius_km"]) for url in sea] == [("bathing", "43.50740", "2")]
    # Another screen within 30 minutes: nothing read again. Later: the forecast, the water and the verdicts, not the site.
    points = len(calls(aioclient_mock, "/v1/point"))
    await ask(hass_ws_client, hass, {"type": "vigie/card/beach", "spot": SPOT})
    assert len(calls(aioclient_mock, f"/v1/surf/spots/{SPOT}")) == 1 and len(calls(aioclient_mock, "/v1/point")) == points
    weather = biarritz_entry.runtime_data.weather
    kept = weather._beaches[SPOT]
    for part in (kept.forecast, kept.water, weather._activities[SPOT]):
        part.read_at -= 1801
    await ask(hass_ws_client, hass, {"type": "vigie/card/beach", "spot": SPOT})
    assert len(calls(aioclient_mock, f"/v1/surf/spots/{SPOT}")) == 2 and len(calls(aioclient_mock, "/v1/point")) == points + 1
    assert len(calls(aioclient_mock, f"/v1/activities/{SPOT}")) == 2 and len(calls(aioclient_mock, "/v1/sea/places")) == 1


async def setup_entry_with(hass: HomeAssistant, mock_instance: Callable[..., None], entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, **kw: Any) -> None:
    mock_instance(activities=ACTIVITIES)  # the verdicts read with the forecast, not those of the 22nd
    beach(aioclient_mock, **kw)
    await setup_entry(hass, entry)


async def test_a_spot_that_is_not_near_the_place_is_refused_and_not_read(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    await setup_entry_with(hass, mock_instance, biarritz_entry, aioclient_mock)
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/beach", "spot": "trevou-treguignec-plage-de-trestel"})
    assert not answer["success"] and answer["error"]["code"] == "not_found"
    assert not [call for call in aioclient_mock.mock_calls if call[1].path.startswith("/v1/surf/spots/")]


async def test_without_the_bathing_sites_the_rest_of_the_beach_is_there(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    """An instance older than ``/v1/sea/places`` answers 404: no site, never a guess; the forecast and the verdicts stay."""
    await setup_entry_with(hass, mock_instance, biarritz_entry, aioclient_mock, bathing={"detail": "Not Found"}, bathing_status=404)
    data = (await ask(hass_ws_client, hass, {"type": "vigie/card/beach", "spot": SPOT}))["result"]
    assert data["bathing"] is None and data["forecast"]["hours"] and data["activities"]["activities"]


async def test_a_forecast_that_does_not_answer_leaves_the_verdicts(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    await setup_entry_with(hass, mock_instance, biarritz_entry, aioclient_mock, forecast_status=502)
    data = (await ask(hass_ws_client, hass, {"type": "vigie/card/beach", "spot": SPOT}))["result"]
    assert data["forecast"] is None and data["water"] is None and data["bathing"] is None
    assert data["spot"]["name"] == "Plage des Corsaires" and data["activities"]["activities"]


async def test_the_beaches_kept_are_the_last_ones_asked(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    await setup_entry_with(hass, mock_instance, biarritz_entry, aioclient_mock)
    spots = [spot["id"] for spot in (await ask(hass_ws_client, hass, {"type": "vigie/card/spots"}))["result"]][: MAX_BEACHES + 1]
    for spot in spots:
        aioclient_mock.get(f"{URL}/v1/surf/spots/{spot}", json=FORECAST)
        aioclient_mock.get(f"{URL}/v1/activities/{spot}", json=ACTIVITIES)
    for spot in spots:
        assert (await ask(hass_ws_client, hass, {"type": "vigie/card/beach", "spot": spot}))["success"]
    assert list(biarritz_entry.runtime_data.weather._beaches) == spots[1:]


def test_the_bathing_site_is_the_beach_s_own_not_a_lake_behind_it_nor_another_beach() -> None:
    spot = FORECAST["spot"]
    assert bathing_site(BATHING, spot)["name"] == "Les Corsaires"
    # A pond 500 m straight inland (the beach faces 296°): left out; the same pond counts when the facing is unknown.
    pond = {**BATHING["places"][0], "id": "bathing-pond", "name": "Étang", "lat": spot["lat"] - 0.002, "lon": spot["lon"] + 0.005}
    assert bathing_site({**BATHING, "places": [pond]}, spot) is None
    assert bathing_site({**BATHING, "places": [pond]}, {**spot, "facing_deg": None})["name"] == "Étang"
    # Farther than 1 km: another beach. Another kind of place is never one.
    far = {**BATHING["places"][0], "lat": spot["lat"] + 0.012}
    assert bathing_site({**BATHING, "places": [far]}, spot) is None
    assert bathing_site({**BATHING, "places": [{**BATHING["places"][0], "type": "port"}]}, spot) is None
    assert bathing_site(None, spot) is None and bathing_site(BATHING, {}) is None


def test_the_trimmed_answers_keep_only_the_window() -> None:
    hours = beach_forecast(FORECAST, READ_UTC)["hours"]
    assert len(hours) == BEACH_HOURS + 1 and beach_forecast({"spot": {}}, READ_UTC) is None
    acts = beach_activities(ACTIVITIES, READ_UTC)
    assert all(READ_UTC - 12 * 3600 <= e["time_utc"] <= READ_UTC + 36 * 3600 for e in acts["tide"]["extremes"])
    assert set(acts["activities"][0]) == {"id", "available", "verdict", "reason", "best_slot", "hours"}
