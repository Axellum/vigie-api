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
from .payloads import URL, response, vigilance

DOC = Path(__file__).resolve().parents[1] / "docs" / "home-assistant.md"


def documented_automation() -> dict:
    text = DOC.read_text(encoding="utf-8")
    match = re.search(r"<!-- example:automation -->\s*```yaml\n(.*?)```", text, re.S)
    assert match, "the automation example is not in the documentation"
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
