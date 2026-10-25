"""The config flow (address of the instance and place), its options, and the change of address or place."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.vigie.const import (
    CONF_EXPERIMENTAL_PREALERT,
    CONF_LOCATION,
    CONF_QUAKE_MIN_MAGNITUDE,
    CONF_QUAKE_RADIUS_KM,
    CONF_RIVER_RADIUS_KM,
    CONF_SCAN_INTERVAL_MIN,
    DOMAIN,
    PUBLIC_INSTANCE_URL,
)

from .conftest import HOME, setup_entry
from .payloads import URL, response, vigilance


def suggested(result: dict[str, Any], field: str) -> Any:
    """The value the form suggests for a field."""
    for key in result["data_schema"].schema:
        if key == field:
            return (key.description or {}).get("suggested_value")
    raise AssertionError(f"no field {field}")


async def start(hass: HomeAssistant) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_the_form_suggests_the_public_instance_the_home_zone_and_a_name(hass: HomeAssistant) -> None:
    hass.states.async_set("zone.home", "0", {CONF_LATITUDE: 43.1234, CONF_LONGITUDE: 5.4321})
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    assert suggested(result, CONF_LOCATION) == {CONF_LATITUDE: 43.1234, CONF_LONGITUDE: 5.4321}
    assert suggested(result, CONF_NAME).startswith("Vigie ")
    assert suggested(result, CONF_URL) == PUBLIC_INSTANCE_URL == "https://vigie.auxfilsdesidees.fr"  # another house needs no instance of its own
    assert result["description_placeholders"] == {"example": "http://vigie.local:8080", "public": PUBLIC_INSTANCE_URL}  # the help of the address field


async def test_without_a_home_zone_the_location_of_home_assistant_is_suggested(hass: HomeAssistant) -> None:
    hass.config.latitude, hass.config.longitude = 48.5, 2.25
    result = await start(hass)
    assert suggested(result, CONF_LOCATION) == {CONF_LATITUDE: 48.5, CONF_LONGITUDE: 2.25}


async def test_a_valid_instance_creates_the_entry(hass: HomeAssistant, mock_instance: Callable[..., None], aioclient_mock: AiohttpClientMocker) -> None:
    mock_instance()
    result = await start(hass)
    with patch("custom_components.vigie.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NAME: "Vigie Home", CONF_URL: "vigie.test:8080/", CONF_LOCATION: {CONF_LATITUDE: 45.0, CONF_LONGITUDE: 5.0}}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Vigie Home"
    assert result["data"] == {CONF_URL: URL, CONF_LATITUDE: 45.0, CONF_LONGITUDE: 5.0}
    assert result["result"].unique_id == f"{URL}|45.0000|5.0000"
    assert [str(call[1]) for call in aioclient_mock.mock_calls] == [f"{URL}/v1/health"]  # validated by /v1/health only


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        ({"exc": aiohttp.ClientConnectionError("refused")}, "cannot_connect"),
        ({"exc": TimeoutError()}, "cannot_connect"),
        ({"status": 500}, "cannot_connect"),
        ({"status": 404}, "invalid_response"),
        ({"json": {"hello": "world"}}, "invalid_response"),
        ({"json": ["not", "an", "object"]}, "invalid_response"),
        ({"text": "<html>router login</html>"}, "invalid_response"),
    ],
)
async def test_an_address_that_is_not_a_vigie_instance_is_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, reply: dict[str, Any], error: str
) -> None:
    aioclient_mock.get(f"{URL}/v1/health", **reply)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Vigie", CONF_URL: URL, CONF_LOCATION: {CONF_LATITUDE: 45.0, CONF_LONGITUDE: 5.0}}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_an_address_that_is_not_http_is_refused_without_a_request(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Vigie", CONF_URL: "ftp://vigie.test", CONF_LOCATION: {CONF_LATITUDE: 45.0, CONF_LONGITUDE: 5.0}}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}
    assert aioclient_mock.call_count == 0


async def test_the_same_place_on_the_same_instance_is_not_added_twice(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry
) -> None:
    mock_instance()
    mock_entry.add_to_hass(hass)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Again", CONF_URL: URL, CONF_LOCATION: {CONF_LATITUDE: 45.0, CONF_LONGITUDE: 5.0}}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_another_place_on_the_same_instance_is_a_second_entry(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry
) -> None:
    mock_instance()
    mock_entry.add_to_hass(hass)
    result = await start(hass)
    with patch("custom_components.vigie.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NAME: "Holiday", CONF_URL: URL, CONF_LOCATION: {CONF_LATITUDE: 42.7, CONF_LONGITUDE: 9.45}}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_the_options_set_the_poll_the_river_radius_and_the_earthquakes_and_reload_the_entry(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, no_stream: None
) -> None:
    mock_instance()
    await setup_entry(hass, mock_entry)
    assert mock_entry.runtime_data.coordinator.update_interval.total_seconds() == 300  # the default: five minutes
    assert mock_entry.runtime_data.coordinator.river_radius_km == 5

    form = await hass.config_entries.options.async_init(mock_entry.entry_id)
    assert form["type"] is FlowResultType.FORM and form["step_id"] == "init"
    assert suggested(form, CONF_SCAN_INTERVAL_MIN) == 5 and suggested(form, CONF_RIVER_RADIUS_KM) == 5
    assert suggested(form, CONF_QUAKE_RADIUS_KM) == 300 and suggested(form, CONF_QUAKE_MIN_MAGNITUDE) == 4.5
    assert suggested(form, CONF_EXPERIMENTAL_PREALERT) is False
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_SCAN_INTERVAL_MIN: 2, CONF_RIVER_RADIUS_KM: 12, CONF_QUAKE_RADIUS_KM: 800, CONF_QUAKE_MIN_MAGNITUDE: 5.04}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options == {
        CONF_SCAN_INTERVAL_MIN: 2,
        CONF_RIVER_RADIUS_KM: 12,
        CONF_QUAKE_RADIUS_KM: 800,
        CONF_QUAKE_MIN_MAGNITUDE: 5.0,
        CONF_EXPERIMENTAL_PREALERT: False,  # off unless ticked
    }
    assert mock_entry.state is ConfigEntryState.LOADED
    assert mock_entry.runtime_data.coordinator.update_interval.total_seconds() == 120
    last = [call for call in aioclient_mock.mock_calls if str(call[1]).startswith(f"{URL}/v1/vigilances")][-1][1]
    assert last.query["river_radius_km"] == "12"
    quakes = [call for call in aioclient_mock.mock_calls if str(call[1]).startswith(f"{URL}/v1/earthquakes")][-1][1]
    assert quakes.query["radius_km"] == "800" and quakes.query["min_magnitude"] == "5" and quakes.query["hours"] == "24"
    assert mock_entry.runtime_data.quakes.update_interval.total_seconds() == 120


@pytest.mark.parametrize(
    ("stored", "minutes", "radius", "quake_radius", "magnitude"),
    [(0, 1, 1, 10, 4.5), (999, 60, 50, 999, 9.0), (99_999, 60, 50, 3000, 9.0), ("x", 5, 5, 300, 4.5), (True, 5, 5, 300, 4.5)],
)
async def test_options_out_of_range_are_held_in_range(
    hass: HomeAssistant,
    mock_instance: Callable[..., None],
    mock_entry: MockConfigEntry,
    no_stream: None,
    stored: Any,
    minutes: int,
    radius: int,
    quake_radius: float,
    magnitude: float,
) -> None:
    mock_instance()
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        options={CONF_SCAN_INTERVAL_MIN: stored, CONF_RIVER_RADIUS_KM: stored, CONF_QUAKE_RADIUS_KM: stored, CONF_QUAKE_MIN_MAGNITUDE: stored},
    )
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_entry.runtime_data.coordinator
    assert coordinator.update_interval.total_seconds() == minutes * 60
    assert coordinator.river_radius_km == radius
    assert mock_entry.runtime_data.quakes.radius_km == quake_radius and mock_entry.runtime_data.quakes.min_magnitude == magnitude


async def test_reconfigure_changes_the_place_and_the_address_and_keeps_the_entities(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None
) -> None:
    mock_instance(response([vigilance(level="ORANGE")]))
    await setup_entry(hass, mock_entry)
    registry_ids = {entity.entity_id for entity in hass.states.async_all()}
    assert registry_ids

    result = await mock_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    assert suggested(result, CONF_URL) == URL and suggested(result, CONF_NAME) == "Vigie Test"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Vigie Boat", CONF_URL: URL, CONF_LOCATION: {CONF_LATITUDE: 42.7, CONF_LONGITUDE: 9.45}}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert mock_entry.title == "Vigie Boat"
    assert mock_entry.data[CONF_LATITUDE] == 42.7 and mock_entry.unique_id == f"{URL}|42.7000|9.4500"
    assert mock_entry.state is ConfigEntryState.LOADED
    assert mock_entry.runtime_data.coordinator.latitude == 42.7
    assert registry_ids <= {entity.entity_id for entity in hass.states.async_all()}  # the same entity ids: the identity is the entry's


async def test_reconfigure_cannot_take_the_place_of_another_entry(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry
) -> None:
    mock_instance()
    other = MockConfigEntry(domain=DOMAIN, title="Other", unique_id=f"{URL}|42.7000|9.4500", data={CONF_URL: URL, CONF_LATITUDE: 42.7, CONF_LONGITUDE: 9.45})
    other.add_to_hass(hass)
    mock_entry.add_to_hass(hass)
    result = await mock_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Vigie Test", CONF_URL: URL, CONF_LOCATION: {CONF_LATITUDE: 42.7, CONF_LONGITUDE: 9.45}}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    assert mock_entry.unique_id == f"{URL}|45.0000|5.0000"


async def test_reconfigure_with_nothing_changed_is_fine(hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry) -> None:
    mock_instance()
    mock_entry.add_to_hass(hass)
    result = await mock_entry.start_reconfigure_flow(hass)
    with patch("custom_components.vigie.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NAME: "Vigie Test", CONF_URL: URL, CONF_LOCATION: dict(HOME)}
        )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
