"""The recipe of `docs/home-assistant.md` ("Try it without waiting for a warning"), over real HTTP.

The fake instance (`tools/fake_instance.py`) is served on the loopback by a thread, the integration is added through its
config flow with that address, and the warnings are changed with the same POST requests the documentation gives.
"""

from __future__ import annotations

import asyncio
import threading
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from typing import Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME, CONF_URL, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType

from custom_components.vigie.const import CONF_LOCATION, DOMAIN
from tools import fake_instance

EVENT = "event.vigie_fake_vigilance"
LEVEL = "sensor.vigie_fake_maximum_level"


@pytest.fixture
def fake_url(socket_enabled: None) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake_instance.make_handler(fake_instance.Scenario()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


async def post(hass: HomeAssistant, url: str) -> None:
    def call() -> None:
        with urllib.request.urlopen(urllib.request.Request(url, method="POST", data=b""), timeout=5) as reply:
            reply.read()

    await hass.async_add_executor_job(call)


async def test_the_documented_recipe_gives_new_upgraded_and_ended(hass: HomeAssistant, fake_url: str, no_stream: None) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Vigie Fake", CONF_URL: fake_url, CONF_LOCATION: {CONF_LATITUDE: 45.0, CONF_LONGITUDE: 5.0}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY  # `/v1/health` of the fake instance passed the check
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert hass.states.get(LEVEL).state == "none"  # a source answers, nothing is in force

    fired: list[dict[str, Any]] = []

    @callback
    def on_change(event: Event) -> None:
        new = event.data["new_state"]
        if event.data["entity_id"] == EVENT and new and new.attributes.get("event_type"):
            fired.append(dict(new.attributes))

    hass.bus.async_listen(EVENT_STATE_CHANGED, on_change)

    async def read() -> None:
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()

    await post(hass, f"{fake_url}/fake/set?phenomenon=wind&level=YELLOW")
    await read()
    assert hass.states.get(LEVEL).state == "yellow"
    await post(hass, f"{fake_url}/fake/set?phenomenon=wind&level=ORANGE")
    await read()
    assert hass.states.get(LEVEL).state == "orange"
    await post(hass, f"{fake_url}/fake/remove?phenomenon=wind")
    await read()
    assert hass.states.get(LEVEL).state == "none"
    await asyncio.sleep(0)

    assert [(e["event_type"], e["level"]) for e in fired] == [("new", "yellow"), ("upgraded", "orange"), ("ended", "orange")]
    assert fired[1]["previous_level"] == "yellow"
    assert all("SIMULATION" in e["headline"] for e in fired)  # labelled as what it is, on the entity too
    assert "SIMULATION" in hass.states.get(LEVEL).attributes["attribution"]
