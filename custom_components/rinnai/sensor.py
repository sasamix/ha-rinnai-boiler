"""Датчики котла Rinnai."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RinnaiConfigEntry
from .entity import RinnaiEntity


@dataclass(frozen=True, kw_only=True)
class RinnaiSensorDescription(SensorEntityDescription):
    """Описание датчика с функцией извлечения значения."""

    value_fn: Callable[[dict], object]


def _mode(data: dict) -> str:
    if not data.get("power"):
        return "Выключен"
    if data.get("heating"):
        return "Отопление"
    if data.get("hot_water"):
        return "Горячая вода"
    return "Ожидание"


def _burner_activity(data: dict) -> str | None:
    """Показывать фактическую работу, а не разрешённый режим котла."""
    code = data.get("combustion_state")
    if data.get("power") is False:
        return "Выключена"
    if code == 4:
        return "Греет ГВС"
    if data.get("hot_water_using"):
        return "Водоразбор"
    if code == 2:
        return "Греет отопление"
    if code == 1:
        return "Ожидание"
    return "Неизвестно" if code is not None else None


def _schedule(data: dict) -> str:
    value = data.get("schedule")
    if value is None:
        return "Неизвестно"
    return "Включено" if value else "Выключено"


SENSORS: tuple[RinnaiSensorDescription, ...] = (
    RinnaiSensorDescription(
        key="room_cur",
        name="В доме",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("room_cur"),
    ),
    RinnaiSensorDescription(
        key="hw_cur",
        name="Вода",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("hw_cur"),
    ),
    RinnaiSensorDescription(
        key="circuit_set",
        name="Контур",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("circuit_set"),
    ),
    RinnaiSensorDescription(
        key="combustion_state",
        name="Горелка",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_burner_activity,
    ),
    RinnaiSensorDescription(
        key="error_code",
        name="Ошибка",
        value_fn=lambda d: d.get("error_code") or "Нет",
    ),
    RinnaiSensorDescription(
        key="source",
        name="Источник",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("source", "Облако"),
    ),
    RinnaiSensorDescription(
        key="mode",
        name="Режим",
        value_fn=_mode,
    ),
    RinnaiSensorDescription(
        key="schedule",
        name="Расписание",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_schedule,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: RinnaiConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(RinnaiSensor(coordinator, d) for d in SENSORS)


class RinnaiSensor(RinnaiEntity, SensorEntity):
    """Числовой или текстовый показатель котла."""

    entity_description: RinnaiSensorDescription

    def __init__(self, coordinator, description: RinnaiSensorDescription) -> None:
        super().__init__(coordinator, description.key, description.name)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value_fn(self._data)

    @property
    def extra_state_attributes(self) -> dict[str, int | None] | None:
        if self.entity_description.key == "combustion_state":
            return {"burner_code": self._data.get("combustion_state")}
        return None
