"""The entities: states (unknown is not none), attributes, attribution, availability, the poll."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.vigie.const import DOMAIN

from .conftest import setup_entry
from .payloads import URL, coverage, response, vigilance

LEVEL = "sensor.vigie_test_maximum_level"
ACTIVE = "binary_sensor.vigie_test_warning_active"
EVENT = "event.vigie_test_vigilance"


async def test_the_real_answer_gives_a_red_sensor_with_its_attributes(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, haute_corse: dict, no_stream: None
) -> None:
    mock_instance(haute_corse)
    await setup_entry(hass, mock_entry)

    state = hass.states.get(LEVEL)
    assert state.state == "red"
    assert state.attributes["device_class"] == "enum" and state.attributes["options"] == ["none", "yellow", "orange", "red"]
    assert state.attributes["count"] == 7 and state.attributes["omitted"] == 0 and len(state.attributes["vigilances"]) == 7
    assert state.attributes["by_phenomenon"] == {"flood": "red", "thunderstorm": "yellow", "wind": "yellow"}
    assert state.attributes["sources"] == [
        {"source": "meteoalarm", "country": "FR", "status": "ok"},
        {"source": "vigicrues", "country": "FR", "status": "ok"},
    ]
    first = state.attributes["vigilances"][0]
    assert first["level"] == "red" and first["headline"] == "Extreme rain-flood warning" and first["source"] == "meteoalarm"
    assert state.attributes["attribution"].startswith("Source: EUMETNET – MeteoAlarm")
    assert "Time delays" in state.attributes["disclaimer"]
    assert len(json.dumps(dict(state.attributes))) < 16384

    assert hass.states.get(ACTIVE).state == STATE_ON
    assert hass.states.get(EVENT).state == STATE_UNKNOWN  # nothing happened yet: what was in force at the start is the baseline


async def test_the_headlines_follow_the_language_of_home_assistant(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, haute_corse: dict, no_stream: None
) -> None:
    hass.config.language = "fr"
    mock_instance(haute_corse)
    await setup_entry(hass, mock_entry)
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{mock_entry.entry_id}_max_level")
    assert entity_id == "sensor.vigie_test_niveau_maximal"  # the name of the entity is translated too
    assert hass.states.get(entity_id).attributes["vigilances"][0]["headline"] == "Vigilance rouge pluie-inondation"


async def test_nothing_found_where_a_source_answers_is_none_and_off(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(response([]))
    await setup_entry(hass, mock_entry)
    assert hass.states.get(LEVEL).state == "none"
    assert hass.states.get(LEVEL).attributes["count"] == 0
    assert hass.states.get(ACTIVE).state == STATE_OFF


async def test_no_source_covering_the_place_is_unknown_not_none(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(response([], sources=[], note="No official warning source covers this point.", attribution=None))
    await setup_entry(hass, mock_entry)
    state = hass.states.get(LEVEL)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["note"].startswith("No official warning source")
    assert "attribution" not in state.attributes  # nothing answered, nothing is credited
    assert hass.states.get(ACTIVE).state == STATE_UNKNOWN


async def test_every_covering_source_unavailable_is_unknown_not_none(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(response([], sources=[coverage(status="unavailable")]))
    await setup_entry(hass, mock_entry)
    assert hass.states.get(LEVEL).state == STATE_UNKNOWN
    assert hass.states.get(ACTIVE).state == STATE_UNKNOWN


async def test_warnings_that_cannot_be_placed_are_unknown_not_none(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(response([], sources=[coverage("meteoalarm", "IT", not_matchable=4)]))
    await setup_entry(hass, mock_entry)
    state = hass.states.get(LEVEL)
    assert state.state == STATE_UNKNOWN and state.attributes["not_matchable"] == 4


async def test_the_list_of_warnings_is_bounded(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    many = [vigilance(f"w{i}", area=f"Area {i}", codes=(("NUTS3", f"X{i}"),)) for i in range(60)]
    mock_instance(response(many))
    await setup_entry(hass, mock_entry)
    attributes = hass.states.get(LEVEL).attributes
    assert attributes["count"] == 60 and len(attributes["vigilances"]) == 20 and attributes["omitted"] == 40
    assert len(json.dumps(dict(attributes))) < 16384


async def test_the_device_and_the_unique_ids(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance()
    await setup_entry(hass, mock_entry)
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, mock_entry.entry_id), mock_entry.entry_id)
    assert device is not None and device.name == "Vigie Test" and device.manufacturer == "Vigie"
    registry = er.async_get(hass)
    unique_ids = {entry.unique_id for entry in er.async_entries_for_config_entry(registry, mock_entry.entry_id)}
    assert unique_ids == {f"{mock_entry.entry_id}_max_level", f"{mock_entry.entry_id}_warning_active", f"{mock_entry.entry_id}_vigilance"}


async def test_two_places_are_two_devices(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance()
    second = MockConfigEntry(
        domain=DOMAIN, title="Vigie Holiday", unique_id=f"{URL}|42.7000|9.4500", data={"url": URL, "latitude": 42.7, "longitude": 9.45}
    )
    await setup_entry(hass, mock_entry)
    await setup_entry(hass, second)
    assert hass.states.get(LEVEL) is not None and hass.states.get("sensor.vigie_holiday_maximum_level") is not None


async def test_the_entities_are_unavailable_when_the_instance_stops_answering_and_come_back(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, no_stream: None
) -> None:
    mock_instance(response([vigilance(level="ORANGE")]))
    await setup_entry(hass, mock_entry)
    assert hass.states.get(LEVEL).state == "orange"

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/v1/vigilances", exc=aiohttp.ClientConnectionError("down"))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()
    assert hass.states.get(LEVEL).state == STATE_UNAVAILABLE  # not unknown, not none: we do not know and say so differently
    assert hass.states.get(ACTIVE).state == STATE_UNAVAILABLE
    assert hass.states.get(EVENT).state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/v1/vigilances", json=response([vigilance(level="RED")]))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=12))
    await hass.async_block_till_done()
    assert hass.states.get(LEVEL).state == "red"


async def test_an_instance_that_is_down_at_start_makes_the_entry_retry(
    hass: HomeAssistant, mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, no_stream: None
) -> None:
    aioclient_mock.get(f"{URL}/v1/vigilances", exc=aiohttp.ClientConnectionError("down"))
    await setup_entry(hass, mock_entry)
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.states.get(LEVEL) is None  # no entity with an invented state


async def test_an_old_instance_without_the_route_makes_the_entry_retry(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(status=404)
    await setup_entry(hass, mock_entry)
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_an_answer_that_is_not_one_makes_the_entry_retry(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance({"unexpected": True})
    await setup_entry(hass, mock_entry)
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_the_instance_is_read_at_its_interval_for_the_place(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, no_stream: None
) -> None:
    mock_instance()
    await setup_entry(hass, mock_entry)
    reads = lambda: [call[1] for call in aioclient_mock.mock_calls if call[1].path == "/v1/vigilances"]  # noqa: E731
    assert len(reads()) == 1
    assert reads()[0].query == {"lat": "45.00000", "lon": "5.00000", "river_radius_km": "5"}

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=4))
    await hass.async_block_till_done()
    assert len(reads()) == 1  # not yet
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5, seconds=5))
    await hass.async_block_till_done()
    assert len(reads()) == 2


async def test_unloading_stops_the_entry(hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry) -> None:
    mock_instance()
    await setup_entry(hass, mock_entry)
    stream_state = mock_entry.runtime_data.stream.state
    assert stream_state == "not_offered"  # this instance has no scheduler: nothing would be pushed, the entry polls
    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(LEVEL).state == STATE_UNAVAILABLE
