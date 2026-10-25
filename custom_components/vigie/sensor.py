"""The maximum level of the official warnings that cover the place, and the strongest earthquake near it (a layer of its own)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_LEVEL_STATES
from .coordinator import VigieConfigEntry, VigieCoordinator, VigieQuakeCoordinator
from .entity import VigieEntity, VigieQuakeEntity
from .models import build_attributes
from .quakes import build_quake_attributes

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: VigieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add the sensors of the entry."""
    async_add_entities([VigieMaxLevelSensor(entry.runtime_data.coordinator), VigieStrongestQuakeSensor(entry.runtime_data.quakes)])


class VigieMaxLevelSensor(VigieEntity, SensorEntity):
    """The highest level among the warnings in force or announced for the place.

    `none` means a source answered for the place and nothing was found. The state is unknown (not `none`) when no
    source covers the place, when every covering source is unavailable, when a warning cannot be graded, and when the
    sources list warnings that cannot be placed on a map (`not_matchable`).
    """

    _attr_translation_key = "max_level"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MAX_LEVEL_STATES)

    def __init__(self, coordinator: VigieCoordinator) -> None:
        super().__init__(coordinator, "max_level")

    @property
    def native_value(self) -> str | None:
        """`none`, `yellow`, `orange` or `red`; `None` (unknown) when it cannot be told."""
        return self.coordinator.data.max_level

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The count, the level of each phenomenon, the state of the sources and a bounded list of the warnings."""
        return build_attributes(self.coordinator.data, self.hass.config.language)


class VigieStrongestQuakeSensor(VigieQuakeEntity, SensorEntity):
    """The magnitude of the strongest earthquake within the radius in the last day, as its sources reported it.

    Unknown when no earthquake matches: the attribute `count` then says 0 when a seismic source answered, and is null when
    none did. It is not an official warning, and it never changes the warning entities.
    """

    _attr_translation_key = "strongest_earthquake"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: VigieQuakeCoordinator) -> None:
        super().__init__(coordinator, "strongest_earthquake")

    @property
    def native_value(self) -> float | None:
        """The magnitude, or `None` (unknown) when no earthquake matches."""
        data = self.coordinator.data
        strongest = data.strongest if data is not None else None
        return strongest.magnitude if strongest is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Where and when the strongest one was, the settings of the search, the sources, and a bounded list."""
        coordinator = self.coordinator
        if coordinator.data is None:
            return {}
        return build_quake_attributes(
            coordinator.data, radius_km=coordinator.radius_km, min_magnitude=coordinator.min_magnitude, hours=coordinator.hours
        )
