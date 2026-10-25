"""Diagnostics: what an issue report needs, without the place.

The coordinates are redacted (the entry's, and the `query` the API echoes back), and so is the address of the instance.
The warnings are kept as the integration reads them (their area is the name of a region, not a position). The earthquakes keep
their magnitude, time and sources, not their distance or position, which would tell where the place is.
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
    quakes = runtime.quakes
    quake_read: dict[str, Any] = {
        "last_update_success": quakes.last_update_success,
        "radius_km": quakes.radius_km,
        "min_magnitude": quakes.min_magnitude,
        "hours": quakes.hours,
    }
    if quakes.data is not None:
        quake_read |= {
            "server_utc": quakes.data.generated_utc,
            "count": quakes.data.count,
            "unreadable_items": quakes.data.skipped,
            "sources": [{"source": s.provider_id, "answered": s.answered} for s in quakes.data.sources],
            "earthquakes": [
                {"id": q.id, "magnitude": q.magnitude, "time_utc": q.time_utc, "sources": list(q.sources)} for q in quakes.data.quakes
            ],
        }
    # The experimental pre-alert: the province is kept (a province of Turkey is wide), not the point.
    prealert = runtime.prealert
    prealert_read: dict[str, Any] | None = None
    if prealert is not None:
        prealert_read = {"last_update_success": prealert.last_update_success}
        if prealert.data is not None:
            prealert_read |= {
                "province_code": prealert.data.province_code,
                "run_utc": prealert.data.run_utc,
                "days": [{"day": d.day, "max_level": d.max_level} for d in prealert.data.days],
            }
    return async_redact_data(
        {
            "entry": {"title": entry.title, "data": dict(entry.data), "options": dict(entry.options)},
            "read": read,
            "earthquakes": quake_read,
            "experimental_prealert": prealert_read,
            "stream": runtime.stream.diagnostics(),
        },
        TO_REDACT,
    )
