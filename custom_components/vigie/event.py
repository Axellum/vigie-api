"""The event entities: a warning appeared, went up, or ended; an earthquake happened near the place (a layer of its own)."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_NEW_EARTHQUAKE, EVENT_TYPES
from .coordinator import VigieConfigEntry, VigieCoordinator, VigieQuakeCoordinator
from .entity import VigieEntity, VigieQuakeEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: VigieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add the event entities of the entry."""
    async_add_entities([VigieVigilanceEvent(entry.runtime_data.coordinator), VigieQuakeEvent(entry.runtime_data.quakes)])


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


class VigieQuakeEvent(VigieQuakeEntity, EventEntity):
    """Fires `new_earthquake` once per earthquake within the radius and of at least the minimum magnitude.

    Each read is compared with what was already announced (`QuakeTracker`): an earthquake read again, or reported later by a
    second source, fires nothing more. What had already happened when Home Assistant started is the baseline and fires nothing.
    """

    _attr_translation_key = "earthquake"
    _attr_event_types = [EVENT_NEW_EARTHQUAKE]

    def __init__(self, coordinator: VigieQuakeCoordinator) -> None:
        super().__init__(coordinator, "earthquake")
        self._seen = coordinator.data.sequence if coordinator.data is not None else 0

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        if data is not None and data.sequence != self._seen:
            self._seen = data.sequence
            for quake in data.new:
                self._trigger_event(EVENT_NEW_EARTHQUAKE, quake.summary())
                self.async_write_ha_state()  # one state change per event: an automation sees each of them
            if data.new:
                return
        self.async_write_ha_state()
