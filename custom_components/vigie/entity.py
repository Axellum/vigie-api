"""The base of the entities of one entry: one device, the credit of the sources."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import VigieConfigEntry, VigieCoordinator, VigieQuakeCoordinator


def device_info(entry: VigieConfigEntry, base_url: str) -> DeviceInfo:
    """The one device of a watched place: the warnings and the earthquakes are entities of it."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=NAME,
        model="Vigie API",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=f"{base_url}/dashboard",
    )


class VigieEntity(CoordinatorEntity[VigieCoordinator]):
    """An entity of the device of one watched place."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VigieCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry, coordinator.client.base_url)

    @property
    def attribution(self) -> str | None:
        """The credit of the sources that answered, as the API words it (`None` until there is an answer)."""
        data = self.coordinator.data
        return data.attribution if data is not None else None


class VigieQuakeEntity(CoordinatorEntity[VigieQuakeCoordinator]):
    """An earthquake entity of the device of one watched place (a layer of its own, not a warning)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VigieQuakeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry, coordinator.client.base_url)

    @property
    def attribution(self) -> str | None:
        """The credit of the seismic sources (USGS, EMSC), as the API words it (`None` until there is an answer)."""
        data = self.coordinator.data
        return data.attribution if data is not None else None
