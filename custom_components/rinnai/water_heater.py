"""Горячая вода котла Rinnai."""
from __future__ import annotations

from homeassistant.components.water_heater import (
    STATE_OFF, STATE_ON, WaterHeaterEntity, WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RinnaiConfigEntry
from .const import (
    FLAG_CIRCUIT_MODE, FLAG_HEATING, FLAG_HOT_WATER, FLAG_POWER, HW_MAX, HW_MIN,
)
from .entity import RinnaiEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: RinnaiConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([RinnaiWaterHeater(entry.runtime_data)])


class RinnaiWaterHeater(RinnaiEntity, WaterHeaterEntity):
    """Контур ГВС."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
    )
    _attr_operation_list = [STATE_ON, STATE_OFF]
    _attr_min_temp = HW_MIN
    _attr_max_temp = HW_MAX

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "water_heater", "ГВС")

    @property
    def current_temperature(self) -> float | None:
        return self._data.get("hw_cur")

    @property
    def target_temperature(self) -> float | None:
        return self._data.get("hw_set")

    @property
    def current_operation(self) -> str:
        data = self._data
        return STATE_ON if data.get("power") and data.get("hot_water") else STATE_OFF

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        value = max(HW_MIN, min(HW_MAX, int(round(temperature))))
        await self.coordinator.async_set_hot_water_temp(value)

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        # Local 2.2.1: не сбрасываем отопление, подогрев и неизвестные флаги.
        await self.coordinator.async_update_flags(
            set_bits=FLAG_POWER | (FLAG_HOT_WATER if operation_mode == STATE_ON else 0),
            clear_bits=FLAG_HOT_WATER if operation_mode == STATE_OFF else 0)
