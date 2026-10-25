"""The example of `docs/home-assistant.md` is played, so it cannot rot: its entity id, its fields, its template."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import yaml
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.vigie.const import DOMAIN

from .conftest import setup_entry
from .payloads import URL, earthquake, earthquakes_response, response, vigilance

DOC = Path(__file__).resolve().parents[1] / "docs" / "home-assistant.md"


def documented_automation(marker: str = "automation") -> dict:
    text = DOC.read_text(encoding="utf-8")
    match = re.search(rf"<!-- example:{marker} -->\s*```yaml\n(.*?)```", text, re.S)
    assert match, f"the {marker} example is not in the documentation"
    return yaml.safe_load(match.group(1))


async def test_the_documented_automation_notifies_for_orange_and_red_only(
    hass: HomeAssistant, mock_instance: Callable[..., None], no_stream: None
) -> None:
    calls = async_mock_service(hass, "notify", "notify")
    assert await async_setup_component(hass, "automation", {"automation": documented_automation()})

    entry = MockConfigEntry(
        domain=DOMAIN, title="Vigie Home", unique_id=f"{URL}|45.0000|5.0000", data={CONF_URL: URL, CONF_LATITUDE: 45.0, CONF_LONGITUDE: 5.0}
    )
    mock_instance(response([]))
    await setup_entry(hass, entry)
    assert hass.states.get("event.vigie_home_vigilance") is not None  # the entity id the documentation uses

    async def read(*warnings: dict) -> None:
        mock_instance(response(list(warnings)))
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()

    await read(vigilance("a", level="YELLOW"))  # new, but yellow: no notification
    assert calls == []
    await read(vigilance("b", level="ORANGE"))  # upgraded to orange
    assert len(calls) == 1
    assert calls[0].data["title"] == "Vigie: orange"
    assert calls[0].data["message"] == "Orange wind warning (Haute-Corse)"
    await read()  # ended: no notification
    assert len(calls) == 1
    await read(vigilance("c", phenomenon="rain", original="Rain", level="RED"))  # new, red
    assert len(calls) == 2 and calls[1].data["title"] == "Vigie: red"


async def test_the_documented_earthquake_automation_notifies_from_magnitude_5_5(
    hass: HomeAssistant, mock_instance: Callable[..., None], no_stream: None
) -> None:
    calls = async_mock_service(hass, "notify", "notify")
    assert await async_setup_component(hass, "automation", {"automation": documented_automation("earthquake-automation")})

    entry = MockConfigEntry(
        domain=DOMAIN, title="Vigie Home", unique_id=f"{URL}|45.0000|5.0000", data={CONF_URL: URL, CONF_LATITUDE: 45.0, CONF_LONGITUDE: 5.0}
    )
    mock_instance(response([]))
    await setup_entry(hass, entry)
    assert hass.states.get("event.vigie_home_earthquake") is not None  # the entity id the documentation uses

    async def read(*quakes: dict) -> None:
        mock_instance(response([]), quakes=earthquakes_response(list(quakes)))
        await entry.runtime_data.quakes.async_refresh()
        await hass.async_block_till_done()

    small = earthquake("usgs_small", magnitude=4.8, lat=40.0)
    await read(small)  # new, but under 5.5
    assert calls == []
    big = earthquake("usgs_big", magnitude=6.1, distance_km=120.5, title="M 6.1 - 40 km S of Testville, France")
    await read(small, big)
    assert len(calls) == 1
    assert calls[0].data["title"] == "Vigie: earthquake"
    assert calls[0].data["message"] == "M 6.1 - 40 km S of Testville, France (120.5 km)"
    await read(small, big)  # read again: no second notification
    assert len(calls) == 1
