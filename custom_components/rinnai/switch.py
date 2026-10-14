"""Выключатели котла Rinnai."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RinnaiConfigEntry
from .const import FLAG_CIRCUIT_MODE, FLAG_HEATING, FLAG_HOT_WATER, FLAG_POWER
from .entity import RinnaiEntity

FLAG_SWITCHES = (
    ("power", "Питание", FLAG_POWER, None),
    ("hot_water", "ГВС вкл", FLAG_HOT_WATER, None),
    ("heating", "Отопление вкл", FLAG_HEATING, None),
    ("circuit_mode", "По теплоносителю", FLAG_CIRCUIT_MODE, EntityCategory.CONFIG),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: RinnaiConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [
        RinnaiFlagSwitch(coordinator, *args) for args in FLAG_SWITCHES
    ]
    entities.append(RinnaiAwaySwitch(coordinator))
    # Local 2.2.1: облачные режимы не показываем в локальной записи.
    if not coordinator.local_only:
        entities.append(
            RinnaiBlindSwitch(coordinator, "economy", "Эконом", coordinator.async_set_economy)
        )
        entities.append(
            RinnaiBlindSwitch(coordinator, "sleep", "Ночной", coordinator.async_set_sleep)
        )
    async_add_entities(entities)


class RinnaiFlagSwitch(RinnaiEntity, SwitchEntity):
    """Переключатель одного бита в поле флагов (CMD 01)."""

    def __init__(self, coordinator, key, name, bit, category) -> None:
        super().__init__(coordinator, f"switch_{key}", name)
        self._key = key
        self._bit = bit
        self._attr_entity_category = category

    @property
    def is_on(self) -> bool:
        return bool(self._data.get(self._key))

    async def _apply(self, turn_on: bool) -> None:
        set_bits = self._bit if turn_on else 0
        if turn_on and self._bit != FLAG_POWER:
            set_bits |= FLAG_POWER
        await self.coordinator.async_update_flags(
            set_bits=set_bits, clear_bits=0 if turn_on else self._bit)

    async def async_turn_on(self, **kwargs) -> None:
        await self._apply(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._apply(False)


class RinnaiAwaySwitch(RinnaiEntity, SwitchEntity):
    """Режим отъезда — состояние приходит в статусе котла."""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "switch_away", "Отъезд")

    @property
    def is_on(self) -> bool:
        return bool(self._data.get("away"))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_away(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_away(False)


class RinnaiBlindSwitch(RinnaiEntity, SwitchEntity):
    """Режим, состояние которого котёл не сообщает.

    HA показывает две независимые кнопки и не делает вид, будто знает состояние.
    """

    _attr_assumed_state = True

    def __init__(self, coordinator, key, name, setter) -> None:
        super().__init__(coordinator, f"switch_{key}", name)
        self._setter = setter
        self._state = False

    @property
    def is_on(self) -> bool:
        return self._state

    async def async_turn_on(self, **kwargs) -> None:
        await self._setter(True)
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self._setter(False)
        self._state = False
        self.async_write_ha_state()
