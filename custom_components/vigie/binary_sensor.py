"""On when a warning of at least yellow covers the place."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import STATE_NONE
from .coordinator import VigieConfigEntry, VigieCoordinator
from .entity import VigieEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: VigieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add the binary sensor of the entry."""
    async_add_entities([VigieWarningActiveBinarySensor(entry.runtime_data.coordinator)])


class VigieWarningActiveBinarySensor(VigieEntity, BinarySensorEntity):
    """Yellow or above: on. Nothing found where a source answers: off. Anything else: unknown, as the level sensor."""

    _attr_translation_key = "warning_active"

    def __init__(self, coordinator: VigieCoordinator) -> None:
        super().__init__(coordinator, "warning_active")

    @property
    def is_on(self) -> bool | None:
        """Whether a warning of at least yellow is in force or announced; `None` when the level is unknown."""
        level = self.coordinator.data.max_level
        return None if level is None else level != STATE_NONE
