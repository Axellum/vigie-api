"""The event entity: a warning appeared, went up, or ended."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_TYPES
from .coordinator import VigieConfigEntry, VigieCoordinator
from .entity import VigieEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: VigieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add the event entity of the entry."""
    async_add_entities([VigieVigilanceEvent(entry.runtime_data.coordinator)])


class VigieVigilanceEvent(VigieEntity, EventEntity):
    """Fires `new`, `upgraded` or `ended` with the warning's summary as data.

    The changes come from comparing each read of the instance with the one before (`ChangeTracker`); what was already
    in force when Home Assistant started is the baseline and fires nothing.
    """

    _attr_translation_key = "vigilance"
    _attr_event_types = list(EVENT_TYPES)

    def __init__(self, coordinator: VigieCoordinator) -> None:
        super().__init__(coordinator, "vigilance")
        self._seen = coordinator.data.sequence if coordinator.data is not None else 0

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        if data is not None and data.sequence != self._seen:
            self._seen = data.sequence
            language = self.hass.config.language
            for change in data.changes:
                self._trigger_event(change.kind, change.event_data(language))
                self.async_write_ha_state()  # one state change per event: an automation sees each of them
            if data.changes:
                return
        self.async_write_ha_state()
