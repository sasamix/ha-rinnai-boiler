"""Отопление котла Rinnai (термостат, виден в Apple Home).

Котёл греет в одном из двух режимов, и у них разные уставки:
  * по температуре в доме (датчик в пульте) — 10–40 °C;
  * по температуре теплоносителя — 40–80 °C.
Режим выбирается пресетом; диапазон и уставка термостата следуют за ним.
"""
from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RinnaiConfigEntry
from .const import (
    CIRCUIT_MAX, CIRCUIT_MIN, FLAG_CIRCUIT_MODE, FLAG_HEATING, FLAG_HOT_WATER,
    FLAG_POWER, ROOM_MAX, ROOM_MIN,
)
from .entity import RinnaiEntity

PRESET_ROOM = "room"
PRESET_CIRCUIT = "circuit"


async def async_setup_entry(
    hass: HomeAssistant, entry: RinnaiConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([RinnaiClimate(entry.runtime_data)])


class RinnaiClimate(RinnaiEntity, ClimateEntity):
    """Отопление: по воздуху в доме или по теплоносителю."""

    _attr_translation_key = "heating"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_ROOM, PRESET_CIRCUIT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_target_temperature_step = 1

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "climate", "Отопление")

    # ---------- режим ----------

    @property
    def _circuit_mode(self) -> bool:
        return bool(self._data.get("circuit_mode"))

    @property
    def preset_mode(self) -> str:
        return PRESET_CIRCUIT if self._circuit_mode else PRESET_ROOM

    @property
    def min_temp(self) -> float:
        return CIRCUIT_MIN if self._circuit_mode else ROOM_MIN

    @property
    def max_temp(self) -> float:
        return CIRCUIT_MAX if self._circuit_mode else ROOM_MAX

    # ---------- температуры ----------

    @property
    def current_temperature(self) -> float | None:
        # Температуру теплоносителя котёл не сообщает, так что в обоих
        # режимах показываем воздух в доме — единственный живой датчик.
        return self._data.get("room_cur")

    @property
    def target_temperature(self) -> float | None:
        key = "circuit_set" if self._circuit_mode else "room_set"
        value = self._data.get(key)
        if value is None:
            return None
        # В режиме «по теплоносителю» пульт дублирует уставку контура и в байт
        # комнатной уставки, поэтому после переключения режима там может лежать
        # число вне диапазона термостата. Отдаём значение в границах сущности,
        # иначе Home Assistant и HomeKit получают состояние вне своих пределов.
        return min(max(float(value), self.min_temp), self.max_temp)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "raw_room_setpoint": self._data.get("room_set"),
            "raw_circuit_setpoint": self._data.get("circuit_set"),
        }

    @property
    def hvac_mode(self) -> HVACMode:
        data = self._data
        return HVACMode.HEAT if data.get("power") and data.get("heating") else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        data = self._data
        if not data.get("power") or not data.get("heating"):
            return HVACAction.OFF
        # Горит ли горелка на отопление: по состоянию горелки, а не по
        # сравнению температур — в режиме по теплоносителю сравнивать не с чем.
        if data.get("combustion_state") and not data.get("hot_water_using"):
            return HVACAction.HEATING
        return HVACAction.IDLE

    # ---------- команды ----------

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        value = int(round(temperature))
        if self._circuit_mode:
            value = max(CIRCUIT_MIN, min(CIRCUIT_MAX, value))
            await self.coordinator.async_set_circuit_temp(value)
        else:
            value = max(ROOM_MIN, min(ROOM_MAX, value))
            await self.coordinator.async_set_room_temp(value)

    def _flags(self, *, heating: bool, circuit: bool) -> int:
        flags = FLAG_POWER
        if circuit:
            flags |= FLAG_CIRCUIT_MODE
        if self._data.get("hot_water"):
            flags |= FLAG_HOT_WATER
        if heating:
            flags |= FLAG_HEATING
        return flags

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.coordinator.async_update_flags(
            set_bits=FLAG_POWER | (FLAG_HEATING if hvac_mode == HVACMode.HEAT else 0),
            clear_bits=FLAG_HEATING if hvac_mode == HVACMode.OFF else 0)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.coordinator.async_update_flags(
            set_bits=FLAG_POWER | (FLAG_CIRCUIT_MODE if preset_mode == PRESET_CIRCUIT else 0),
            clear_bits=FLAG_CIRCUIT_MODE if preset_mode == PRESET_ROOM else 0)

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
