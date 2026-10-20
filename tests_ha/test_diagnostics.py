"""Diagnostics: useful for an issue report, without the place."""

from __future__ import annotations

import json
from collections.abc import Callable

from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_URL
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vigie.const import DOMAIN
from custom_components.vigie.diagnostics import async_get_config_entry_diagnostics

from .conftest import setup_entry
from .payloads import URL, response, vigilance


async def test_the_coordinates_and_the_address_are_redacted(hass: HomeAssistant, mock_instance: Callable[..., None], no_stream: None) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Vigie Test",
        unique_id=f"{URL}|43.2109|5.4321",
        data={CONF_URL: URL, CONF_LATITUDE: 43.2109, CONF_LONGITUDE: 5.4321},
    )
    answer = response([vigilance(level="ORANGE")])
    answer["query"] = {"lat": 43.2109, "lon": 5.4321, "country": None, "river_radius_km": 5.0}
    mock_instance(answer)
    await setup_entry(hass, entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    dumped = json.dumps(diagnostics)
    for secret in ("43.2109", "5.4321", "vigie.test"):
        assert secret not in dumped, secret
    assert diagnostics["entry"]["data"][CONF_LATITUDE] == "**REDACTED**"
    assert diagnostics["entry"]["data"][CONF_URL] == "**REDACTED**"
    assert diagnostics["read"]["query"]["lat"] == "**REDACTED**"

    read = diagnostics["read"]
    assert read["max_level"] == "orange" and read["last_update_success"] is True and read["has_data"] is True
    assert read["coverage"] == [{"source": "meteoalarm", "country": "FR", "status": "ok", "warnings_not_matchable": 0}]
    assert read["vigilances"][0]["level"] == "orange"
    assert diagnostics["stream"]["state"] in {"idle", "not_offered", "connecting", "reconnecting"}


async def test_diagnostics_of_an_entry_whose_last_read_failed(hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, no_stream: None) -> None:
    mock_instance(response([]))
    await setup_entry(hass, mock_entry)
    mock_instance(status=500)
    await mock_entry.runtime_data.coordinator.async_refresh()
    diagnostics = await async_get_config_entry_diagnostics(hass, mock_entry)
    assert diagnostics["read"]["last_update_success"] is False  # the last good read is still shown, flagged
    assert diagnostics["read"]["max_level"] == "none"
