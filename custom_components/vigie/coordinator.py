"""The coordinator: reads the instance and tells what changed since the read before."""

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
    CONF_RIVER_RADIUS_KM,
    CONF_SCAN_INTERVAL_MIN,
    DEFAULT_RIVER_RADIUS_KM,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    MAX_RIVER_RADIUS_KM,
    MAX_SCAN_INTERVAL_MIN,
    MIN_RIVER_RADIUS_KM,
    MIN_SCAN_INTERVAL_MIN,
)
from .models import ChangeTracker, VigieData, parse_response
from .stream import VigieStream

_LOGGER = logging.getLogger(__name__)

type VigieConfigEntry = ConfigEntry[VigieRuntimeData]


def _bounded(value: object, default: float, low: float, high: float) -> float:
    """A number from the options, held between ``low`` and ``high`` (``default`` when it is not one)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return min(max(float(value), low), high)


class VigieCoordinator(DataUpdateCoordinator[VigieData]):
    """Polls ``/v1/vigilances`` for the place of the entry."""

    config_entry: VigieConfigEntry

    def __init__(self, hass: HomeAssistant, entry: VigieConfigEntry, client: VigieClient) -> None:
        minutes = _bounded(
            entry.options.get(CONF_SCAN_INTERVAL_MIN), DEFAULT_SCAN_INTERVAL_MIN, MIN_SCAN_INTERVAL_MIN, MAX_SCAN_INTERVAL_MIN
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(minutes=minutes),
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


@dataclass
class VigieRuntimeData:
    """What a loaded entry holds."""

    client: VigieClient
    coordinator: VigieCoordinator
    stream: VigieStream
