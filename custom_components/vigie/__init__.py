"""Vigie: the official weather warnings of a place, from a Vigie API instance, and (option "Weather and card") its weather.

A personal project, shared in case it helps. It reads an instance of the Vigie API (its `/v1/vigilances` route, and
`/v1/earthquakes` for the earthquakes near the place, a layer of its own, and `/v1/experimental/prealert` only when its
experimental option is ticked) and does not issue any warning: it is not an official warning service, follow the warnings of your national authorities.
With the option "Weather and card" (on by default), it also reads the weather of the place (`/v1/point`), its tide, the normals of
the month and the wind measured nearby, gives a weather entity and three sensors, and serves all of it to the dashboard card.
"""

from __future__ import annotations

from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import VigieClient
from .const import CONF_EXPERIMENTAL_PREALERT, CONF_WEATHER_AND_CARD, DEFAULT_WEATHER_AND_CARD, DOMAIN
from .coordinator import (
    VigieConfigEntry,
    VigieCoordinator,
    VigiePrealertCoordinator,
    VigieQuakeCoordinator,
    VigieRuntimeData,
)
from .stream import VigieStream
from .weather_data import VigieWeatherCoordinator
from .websocket import async_register_commands

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT, Platform.WEATHER]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Once per Home Assistant: the commands the dashboard card asks its data with."""
    async_register_commands(hass)
    return True


def weather_and_card(entry: VigieConfigEntry) -> bool:
    """The option "Weather and card": on unless it was unticked."""
    value = entry.options.get(CONF_WEATHER_AND_CARD, DEFAULT_WEATHER_AND_CARD)
    return value is not False


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
    # The weather of the place and the data of the card, unless the option is unticked; it never holds the warnings back.
    weather = None
    if weather_and_card(entry):
        weather = VigieWeatherCoordinator(hass, entry, client)
        await weather.async_refresh()
    stream = VigieStream(client, coordinator.latitude, coordinator.longitude, coordinator.async_request_refresh)
    entry.runtime_data = VigieRuntimeData(client=client, coordinator=coordinator, stream=stream, quakes=quakes, prealert=prealert, weather=weather)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Cancelled with the entry when it is unloaded.
    entry.async_create_background_task(hass, stream.run(), f"vigie push stream {entry.entry_id}")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VigieConfigEntry) -> bool:
    """Unload the platforms (the push stream task is cancelled with the entry)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
