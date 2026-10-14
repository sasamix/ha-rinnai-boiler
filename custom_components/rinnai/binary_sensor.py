"""Диагностика связи с котлом Rinnai."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass, BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RinnaiConfigEntry
from .entity import RinnaiEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: RinnaiConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            RinnaiOffline(coordinator),
            RinnaiHotWaterUsing(coordinator),
            RinnaiFault(coordinator),
        ]
    )


class RinnaiOffline(RinnaiEntity, BinarySensorEntity):
    """Горит, когда котёл перестал отвечать."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "offline", "Нет связи")

    @property
    def available(self) -> bool:
        # Датчик обязан оставаться доступным именно тогда, когда связи нет.
        return True

    @property
    def is_on(self) -> bool:
        # Local 2.2.1: связь определяется фактическими пакетами пульта.
        if self.coordinator.local_only:
            return not self.coordinator.local_active
        return not self.coordinator.last_update_success


class RinnaiHotWaterUsing(RinnaiEntity, BinarySensorEntity):
    """Идёт разбор горячей воды прямо сейчас."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "hot_water_using", "Водоразбор")

    @property
    def is_on(self) -> bool:
        return bool(self._data.get("hot_water_using"))


class RinnaiFault(RinnaiEntity, BinarySensorEntity):
    """Котёл сообщает об ошибке."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "fault", "Авария")

    @property
    def is_on(self) -> bool:
        return self._data.get("error_code") is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {"code": self._data.get("error_code")}
