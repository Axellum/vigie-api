"""The base of the entities of one entry: one device, the credit of the sources."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import VigieCoordinator


class VigieEntity(CoordinatorEntity[VigieCoordinator]):
    """An entity of the device of one watched place."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VigieCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=NAME,
            model="Vigie API",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"{coordinator.client.base_url}/dashboard",
        )

    @property
    def attribution(self) -> str | None:
        """The credit of the sources that answered, as the API words it (`None` until there is an answer)."""
        data = self.coordinator.data
        return data.attribution if data is not None else None
