"""The coordinators: read the instance and tell what changed since the read before.

The warnings (`/v1/vigilances`) and the earthquakes (`/v1/earthquakes`) are two coordinators of one entry: a layer each, read
and failing on their own (an instance too old for the earthquakes leaves the warnings untouched).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VigieClient, VigieError
from .const import (
    CONF_QUAKE_MIN_MAGNITUDE,
    CONF_QUAKE_RADIUS_KM,
    CONF_RIVER_RADIUS_KM,
    CONF_SCAN_INTERVAL_MIN,
    DEFAULT_QUAKE_MIN_MAGNITUDE,
    DEFAULT_QUAKE_RADIUS_KM,
    DEFAULT_RIVER_RADIUS_KM,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    MAX_QUAKE_MIN_MAGNITUDE,
    MAX_QUAKE_RADIUS_KM,
    MAX_RIVER_RADIUS_KM,
    MAX_SCAN_INTERVAL_MIN,
    MIN_QUAKE_MIN_MAGNITUDE,
    MIN_QUAKE_RADIUS_KM,
    MIN_RIVER_RADIUS_KM,
    MIN_SCAN_INTERVAL_MIN,
    QUAKE_WINDOW_HOURS,
)
from .models import ChangeTracker, VigieData, parse_response
from .quakes import QuakeData, QuakeTracker, parse_earthquakes
from .stream import VigieStream

_LOGGER = logging.getLogger(__name__)

type VigieConfigEntry = ConfigEntry[VigieRuntimeData]


def _bounded(value: object, default: float, low: float, high: float) -> float:
    """A number from the options, held between ``low`` and ``high`` (``default`` when it is not one)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return min(max(float(value), low), high)


def _update_interval(entry: VigieConfigEntry) -> timedelta:
    minutes = _bounded(entry.options.get(CONF_SCAN_INTERVAL_MIN), DEFAULT_SCAN_INTERVAL_MIN, MIN_SCAN_INTERVAL_MIN, MAX_SCAN_INTERVAL_MIN)
    return timedelta(minutes=minutes)


class VigieCoordinator(DataUpdateCoordinator[VigieData]):
    """Polls ``/v1/vigilances`` for the place of the entry."""

    config_entry: VigieConfigEntry

    def __init__(self, hass: HomeAssistant, entry: VigieConfigEntry, client: VigieClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=_update_interval(entry),
        )
        self.client = client
        self.latitude: float = entry.data[CONF_LATITUDE]
        self.longitude: float = entry.data[CONF_LONGITUDE]
        self.river_radius_km = _bounded(
            entry.options.get(CONF_RIVER_RADIUS_KM), DEFAULT_RIVER_RADIUS_KM, MIN_RIVER_RADIUS_KM, MAX_RIVER_RADIUS_KM
        )
        self._tracker = ChangeTracker()
        self._sequence = 0

    async def _async_update_data(self) -> VigieData:
        try:
            payload = await self.client.vigilances(self.latitude, self.longitude, self.river_radius_km)
        except VigieError as err:
            raise UpdateFailed(str(err)) from err
        try:
            data = parse_response(payload)
        except ValueError as err:
            raise UpdateFailed(f"Unreadable answer: {err}") from err
        if data.skipped:
            _LOGGER.warning("%d warning(s) of the answer could not be read and were left out", data.skipped)
        self._sequence += 1
        return replace(data, changes=self._tracker.update(data), sequence=self._sequence)


class VigieQuakeCoordinator(DataUpdateCoordinator[QuakeData]):
    """Polls ``/v1/earthquakes`` around the place of the entry, at the same interval as the warnings."""

    config_entry: VigieConfigEntry

    def __init__(self, hass: HomeAssistant, entry: VigieConfigEntry, client: VigieClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title} earthquakes",
            update_interval=_update_interval(entry),
        )
        self.client = client
        self.latitude: float = entry.data[CONF_LATITUDE]
        self.longitude: float = entry.data[CONF_LONGITUDE]
        self.radius_km = _bounded(entry.options.get(CONF_QUAKE_RADIUS_KM), DEFAULT_QUAKE_RADIUS_KM, MIN_QUAKE_RADIUS_KM, MAX_QUAKE_RADIUS_KM)
        self.min_magnitude = _bounded(
            entry.options.get(CONF_QUAKE_MIN_MAGNITUDE), DEFAULT_QUAKE_MIN_MAGNITUDE, MIN_QUAKE_MIN_MAGNITUDE, MAX_QUAKE_MIN_MAGNITUDE
        )
        self.hours = float(QUAKE_WINDOW_HOURS)
        self._tracker = QuakeTracker(window_s=QUAKE_WINDOW_HOURS * 3600)
        self._sequence = 0

    async def _async_update_data(self) -> QuakeData:
        try:
            payload = await self.client.earthquakes(self.latitude, self.longitude, self.radius_km, self.min_magnitude, self.hours)
        except VigieError as err:
            raise UpdateFailed(str(err)) from err
        try:
            data = parse_earthquakes(payload, self.min_magnitude)
        except ValueError as err:
            raise UpdateFailed(f"Unreadable answer: {err}") from err
        self._sequence += 1
        return replace(data, new=self._tracker.update(data), sequence=self._sequence)


@dataclass
class VigieRuntimeData:
    """What a loaded entry holds."""

    client: VigieClient
    coordinator: VigieCoordinator
    stream: VigieStream
    quakes: VigieQuakeCoordinator
