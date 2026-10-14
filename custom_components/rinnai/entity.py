"""Базовый класс сущностей Rinnai."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RinnaiCoordinator


class RinnaiEntity(CoordinatorEntity[RinnaiCoordinator]):
    """Общее устройство, unique_id и доступность."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RinnaiCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.room_control_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.room_control_id)},
            manufacturer="Rinnai",
            model="WF-100W-RU",
            name="Котёл",
            configuration_url="https://www.rinnai.ru",
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}
