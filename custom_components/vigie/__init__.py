"""Vigie: the official weather warnings of a place, from a Vigie API instance.

A personal project, shared in case it helps. It reads an instance of the Vigie API (its `/v1/vigilances` route, and
`/v1/earthquakes` for the earthquakes near the place, a layer of its own, and `/v1/experimental/prealert` only when its
experimental option is ticked) and does not issue any warning: it is not an official warning service, follow the warnings of your national authorities.
"""

from __future__ import annotations

from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import VigieClient
from .const import CONF_EXPERIMENTAL_PREALERT
from .coordinator import (
    VigieConfigEntry,
    VigieCoordinator,
    VigiePrealertCoordinator,
    VigieQuakeCoordinator,
    VigieRuntimeData,
)
from .stream import VigieStream

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT]


async def async_setup_entry(hass: HomeAssistant, entry: VigieConfigEntry) -> bool:
    """Read the instance once (an instance that is down makes the entry retry), then add the entities and the push."""
    client = VigieClient(async_get_clientsession(hass), entry.data[CONF_URL])
    coordinator = VigieCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    # The earthquakes never hold the entry back: an instance that cannot give them leaves their entities unavailable.
    quakes = VigieQuakeCoordinator(hass, entry, client)
    await quakes.async_refresh()
    # The experimental pre-alert only when its option is ticked; it never holds the entry back either.
    prealert = None
    if entry.options.get(CONF_EXPERIMENTAL_PREALERT) is True:
        prealert = VigiePrealertCoordinator(hass, entry, client)
        await prealert.async_refresh()
    stream = VigieStream(client, coordinator.latitude, coordinator.longitude, coordinator.async_request_refresh)
    entry.runtime_data = VigieRuntimeData(client=client, coordinator=coordinator, stream=stream, quakes=quakes, prealert=prealert)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Cancelled with the entry when it is unloaded.
    entry.async_create_background_task(hass, stream.run(), f"vigie push stream {entry.entry_id}")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VigieConfigEntry) -> bool:
    """Unload the platforms (the push stream task is cancelled with the entry)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
