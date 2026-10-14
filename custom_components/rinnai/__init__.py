"""Интеграция газового котла Rinnai (RBK-197 RTU / пульт WF-100W)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import RinnaiCoordinator

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.WATER_HEATER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
]

type RinnaiConfigEntry = ConfigEntry[RinnaiCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: RinnaiConfigEntry) -> bool:
    coordinator = RinnaiCoordinator(hass, entry)
    # Local 2.2.1: при неудачной загрузке не оставляем занятый порт.
    try:
        await coordinator.server.start()
    except Exception:
        await coordinator.server.stop()
        raise
    entry.async_on_unload(coordinator.server.stop)
    # Первый опрос может не удаться (например, пульт уже ушёл на наш сервер,
    # и облако его «не видит») — это не повод не запускаться.
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.server.stop()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RinnaiConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.server.stop()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: RinnaiConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
