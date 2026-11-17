"""Режим «Smart control» пульта W-100 (байт 10 пакета состояния)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RinnaiConfigEntry
from .entity import RinnaiEntity
from .protocol import SMART_MODES

LABELS = {"normal": "Обычный", "economy": "Эконом", "sleep": "Ночной", "auto": "Авто"}
VALUES = {LABELS[k]: v for v, k in SMART_MODES.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: RinnaiConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    # Байт 10 известен только в пакете пульта — в облачной записи выбор режима не создаём.
    if coordinator.local_only:
        async_add_entities([RinnaiSmartModeSelect(coordinator)])


class RinnaiSmartModeSelect(RinnaiEntity, SelectEntity):
    """Обычный / Эконом / Ночной / Авто — как Save / Sleep / Auto в приложении.

    «Авто» пульт пересчитывает уставки сам (на проверенном котле поставил теплоноситель 60 °C, ГВС 40 °C).
    """

    _attr_options = list(VALUES)
    _attr_icon = "mdi:tune-variant"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "smart_mode", "Режим Smart")

    @property
    def current_option(self) -> str | None:
        key = SMART_MODES.get(self._data.get("smart_mode"))
        return LABELS.get(key) if key else None

    @property
    def extra_state_attributes(self) -> dict:
        return {"raw": self._data.get("smart_mode")}

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_smart_mode(VALUES[option])
