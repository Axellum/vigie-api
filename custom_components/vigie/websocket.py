"""The commands the dashboard card asks its data with (ADR 0042 of the API).

The card never calls the instance itself: one Home Assistant reads it once for every screen open on it, and the card asks Home
Assistant over its own WebSocket. The data are those the entry has already read; only the verdicts of a spot are read on the
card's demand, and kept for 30 minutes.

- ``vigie/card/entries``: the loaded entries, for the card's editor.
- ``vigie/card/data``: what the card shows for one entry (``entry_id``; the first entry with the option when it is left out),
  with the verdicts of ``spot`` (an identifier, or ``auto`` for the nearest spot within ``AUTO_SPOT_KM``).
- ``vigie/card/spots``: the spots near the place of an entry, for the card's editor.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import CONF_WEATHER_AND_CARD, DEFAULT_WEATHER_AND_CARD, DOMAIN
from .coordinator import VigieConfigEntry

# "auto": the nearest spot, only when it is this close to the place (a spot farther away is not "the sea of the place").
AUTO_SPOT_KM = 20.0


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register the card's commands (once per Home Assistant)."""
    websocket_api.async_register_command(hass, ws_entries)
    websocket_api.async_register_command(hass, ws_card_data)
    websocket_api.async_register_command(hass, ws_spots)


def _loaded(hass: HomeAssistant) -> list[VigieConfigEntry]:
    return [entry for entry in hass.config_entries.async_entries(DOMAIN) if entry.state is ConfigEntryState.LOADED]


def _with_weather(entry: VigieConfigEntry) -> bool:
    return entry.options.get(CONF_WEATHER_AND_CARD, DEFAULT_WEATHER_AND_CARD) is not False and entry.runtime_data.weather is not None


def _entry(hass: HomeAssistant, entry_id: str | None) -> VigieConfigEntry | None:
    entries = _loaded(hass)
    if entry_id:
        return next((entry for entry in entries if entry.entry_id == entry_id), None)
    return next((entry for entry in entries if _with_weather(entry)), None)


@websocket_api.websocket_command({vol.Required("type"): "vigie/card/entries"})
@callback
def ws_entries(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The loaded entries: identifier, name, and whether their option "Weather and card" is ticked."""
    connection.send_result(
        msg["id"],
        [{"entry_id": entry.entry_id, "title": entry.title, "weather_and_card": _with_weather(entry)} for entry in _loaded(hass)],
    )


@websocket_api.websocket_command({vol.Required("type"): "vigie/card/spots", vol.Optional("entry_id"): str})
@websocket_api.async_response
async def ws_spots(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The spots near the place of the entry, nearest first."""
    entry = _entry(hass, msg.get("entry_id"))
    if entry is None or not _with_weather(entry):
        connection.send_error(msg["id"], "not_found", "No Vigie entry with the option Weather and card")
        return
    connection.send_result(msg["id"], await entry.runtime_data.weather.async_spots())  # type: ignore[union-attr]


def _quakes(entry: VigieConfigEntry) -> dict[str, Any] | None:
    data = entry.runtime_data.quakes.data
    if data is None:
        return None
    return {
        "count": data.count,
        "attribution": data.attribution,
        "earthquakes": [
            {
                "title": quake.title,
                "magnitude": quake.magnitude,
                "time_utc": quake.time_utc,
                "distance_km": quake.distance_km,
                "bearing_cardinal": quake.bearing_cardinal,
                "depth_km": quake.depth_km,
                "tsunami_warning": quake.tsunami_warning,
            }
            for quake in data.quakes[:5]
        ],
    }


@websocket_api.websocket_command(
    {vol.Required("type"): "vigie/card/data", vol.Optional("entry_id"): str, vol.Optional("spot"): vol.Any(str, None)}
)
@websocket_api.async_response
async def ws_card_data(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """What the card shows for one entry. A part the instance did not give is ``null``, never filled in."""
    entry = _entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(msg["id"], "not_found", "No loaded Vigie entry")
        return
    runtime = entry.runtime_data
    weather = runtime.weather
    if weather is None:
        connection.send_error(msg["id"], "weather_off", "The option Weather and card of this entry is unticked")
        return
    language = hass.config.language
    warnings = runtime.coordinator.data
    data = weather.data

    spot_id = msg.get("spot")
    spot: dict[str, Any] | None = None
    activities = None
    if spot_id:
        spots = await weather.async_spots()
        if spot_id == "auto":
            spot = spots[0] if spots and spots[0]["distance_km"] <= AUTO_SPOT_KM else None
        else:
            spot = next((item for item in spots if item["id"] == spot_id), None)
        if spot is not None:
            activities = await weather.async_activities(spot["id"])

    connection.send_result(
        msg["id"],
        {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "language": language,
            "read_utc": data.read_utc if data is not None else None,
            "point": data.point if data is not None else None,
            "tides": data.tides if data is not None else None,
            "normals": data.normals if data is not None else None,
            "station_wind": data.station_wind if data is not None else None,
            "warnings": None
            if warnings is None
            else {
                "max_level": warnings.max_level,
                "server_utc": warnings.server_utc,
                "note": warnings.note,
                "attribution": warnings.attribution,
                "vigilances": [vigilance.summary(language) for vigilance in warnings.vigilances],
            },
            "earthquakes": _quakes(entry),
            "spot": spot,
            "activities": activities,
        },
    )
