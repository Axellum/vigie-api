"""Diagnostics: what an issue report needs, without the place.

The coordinates are redacted (the entry's, and the `query` the API echoes back), and so is the address of the instance.
The warnings are kept as the integration reads them (their area is the name of a region, not a position).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_URL
from homeassistant.core import HomeAssistant

from .const import CONF_LOCATION
from .coordinator import VigieConfigEntry

TO_REDACT = {CONF_LATITUDE, CONF_LONGITUDE, "lat", "lon", CONF_LOCATION, CONF_URL, "base_url", "configuration_url"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: VigieConfigEntry) -> dict[str, Any]:
    """The entry, the last read, the sources and the state of the push stream."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    data = coordinator.data
    language = hass.config.language
    read: dict[str, Any] = {
        "last_update_success": coordinator.last_update_success,
        "update_interval_s": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
        "river_radius_km": coordinator.river_radius_km,
        "query": {"lat": coordinator.latitude, "lon": coordinator.longitude, "river_radius_km": coordinator.river_radius_km},
    }
    if data is not None:
        read |= {
            "server_utc": data.server_utc,
            "max_level": data.max_level,
            "has_data": data.has_data,
            "not_matchable": data.not_matchable,
            "unreadable_items": data.skipped,
            "note": data.note,
            "coverage": [
                {"source": item.source, "country": item.country, "status": item.status, "warnings_not_matchable": item.warnings_not_matchable}
                for item in data.coverage
            ],
            "vigilances": [vigilance.summary(language) for vigilance in data.vigilances],
        }
    return async_redact_data(
        {
            "entry": {"title": entry.title, "data": dict(entry.data), "options": dict(entry.options)},
            "read": read,
            "stream": runtime.stream.diagnostics(),
        },
        TO_REDACT,
    )
