"""The commands the dashboard card asks its data with (``vigie/card/entries``, ``vigie/card/data``, ``vigie/card/spots``)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.vigie.const import CONF_WEATHER_AND_CARD, DOMAIN

from .conftest import SPOT, recorded, setup_entry
from .payloads import URL, response, vigilance


@pytest.fixture(autouse=True)
def _frozen(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-11-22T13:12:00+00:00")


async def ask(hass_ws_client: WebSocketGenerator, hass: HomeAssistant, message: dict[str, Any]) -> dict[str, Any]:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(message)
    return await client.receive_json()


async def test_the_entries_for_the_editor(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, mock_instance: Callable[..., None], biarritz_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance()
    await setup_entry(hass, biarritz_entry)
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/entries"})
    assert answer["success"] and answer["result"] == [{"entry_id": biarritz_entry.entry_id, "title": "Vigie Biarritz", "weather_and_card": True}]


async def test_the_data_of_the_card(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    mock_instance(response([vigilance("meteofrance:64:wind", source="meteofrance", phenomenon="wind", level="YELLOW", area="Pyrénées-Atlantiques")]))
    await setup_entry(hass, biarritz_entry)
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/data", "spot": SPOT})
    assert answer["success"]
    data = answer["result"]
    assert data["entry_id"] == biarritz_entry.entry_id and data["title"] == "Vigie Biarritz"
    assert data["point"]["current"]["temp_c"] == 15.1 and data["tides"]["model"] == "EOT20"
    assert data["normals"]["status"] == "available" and data["station_wind"]["name"] == "BIARRITZ-PAYS-BASQUE"
    warnings = data["warnings"]
    assert warnings["max_level"] == "yellow" and [v["phenomenon"] for v in warnings["vigilances"]] == ["wind"]
    assert warnings["vigilances"][0]["area"] == "Pyrénées-Atlantiques" and "onset" in warnings["vigilances"][0]
    assert data["earthquakes"]["count"] == 0 and data["earthquakes"]["earthquakes"] == []
    assert data["spot"]["id"] == SPOT and data["activities"]["spot"]["id"] == SPOT
    assert data["activities"] == recorded(f"activities_{SPOT}_2026-11-22.json")
    # a second ask within 30 minutes does not read the verdicts again
    await ask(hass_ws_client, hass, {"type": "vigie/card/data", "spot": SPOT})
    assert len([call for call in aioclient_mock.mock_calls if call[1].path == f"/v1/activities/{SPOT}"]) == 1


async def test_auto_takes_the_nearest_spot_and_a_far_spot_is_refused(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_instance: Callable[..., None],
    biarritz_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_stream: None,
) -> None:
    mock_instance()
    aioclient_mock.get(f"{URL}/v1/activities/biarritz-grande-plage", status=404, json={"detail": "not found"})
    await setup_entry(hass, biarritz_entry)
    auto = (await ask(hass_ws_client, hass, {"type": "vigie/card/data", "spot": "auto"}))["result"]
    assert auto["spot"]["id"] == "biarritz-grande-plage" and auto["activities"] is None  # the instance had nothing for it
    far = (await ask(hass_ws_client, hass, {"type": "vigie/card/data", "spot": "trevou-treguignec-plage-de-trestel"}))["result"]
    assert far["spot"] is None and far["activities"] is None  # in Brittany: not a spot of this place, not read
    assert not [call for call in aioclient_mock.mock_calls if "trestel" in call[1].path]


async def test_no_spot_inland(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance()
    await setup_entry(hass, mock_entry)
    assert (await ask(hass_ws_client, hass, {"type": "vigie/card/spots", "entry_id": mock_entry.entry_id}))["result"] == []
    data = (await ask(hass_ws_client, hass, {"type": "vigie/card/data", "spot": "auto"}))["result"]
    assert data["spot"] is None and data["activities"] is None


async def test_the_spots_for_the_editor(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, mock_instance: Callable[..., None], biarritz_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance()
    await setup_entry(hass, biarritz_entry)
    spots = (await ask(hass_ws_client, hass, {"type": "vigie/card/spots"}))["result"]
    assert spots[0] == {"id": "biarritz-grande-plage", "name": spots[0]["name"], "place": "Biarritz", "distance_km": 0.5}
    assert SPOT in [spot["id"] for spot in spots]


async def test_an_entry_without_the_option_says_so(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, mock_instance: Callable[..., None], no_stream: None
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Vigie Test",
        unique_id=f"{URL}|45.0000|5.0000",
        data={"url": URL, "latitude": 45.0, "longitude": 5.0},
        options={CONF_WEATHER_AND_CARD: False},
    )
    mock_instance()
    await setup_entry(hass, entry)
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/data", "entry_id": entry.entry_id})
    assert not answer["success"] and answer["error"]["code"] == "weather_off"
    answer = await ask(hass_ws_client, hass, {"type": "vigie/card/data", "entry_id": "nope"})
    assert not answer["success"] and answer["error"]["code"] == "not_found"
    entries = (await ask(hass_ws_client, hass, {"type": "vigie/card/entries"}))["result"]
    assert entries == [{"entry_id": entry.entry_id, "title": "Vigie Test", "weather_and_card": False}]
