"""Состояние котла: локальный сервер, если пульт подключился, иначе облако."""
from __future__ import annotations

from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RinnaiApi, RinnaiError, RinnaiPultOfflineError
from .const import (
    CONF_DEVICE_ID, CONF_LOCAL_PORT, CONF_ROOM_CONTROL_ID, DEFAULT_LOCAL_PORT,
    CONF_LOCAL_ONLY, FLAG_POWER, FLAG_HEATING, FLAG_HOT_WATER, FLAG_CIRCUIT_MODE,
    DOMAIN, LOCAL_GRACE_SECONDS, SCAN_INTERVAL_SECONDS,
)
from . import protocol
from .server import RinnaiLocalServer

_LOGGER = logging.getLogger(__name__)


class RinnaiCoordinator(DataUpdateCoordinator[dict]):
    """Держит актуальное состояние и отправляет команды."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=10 if entry.data.get(CONF_LOCAL_ONLY) else SCAN_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.room_control_id: str = entry.data[CONF_ROOM_CONTROL_ID]
        # Local 2.2.1: никакой сетевой связи с облаком в local_only.
        self.local_only = entry.data.get(CONF_LOCAL_ONLY, False)
        self.api = None if self.local_only else RinnaiApi(self.room_control_id, entry.data[CONF_DEVICE_ID])
        port = entry.options.get(CONF_LOCAL_PORT, DEFAULT_LOCAL_PORT)
        self.server = RinnaiLocalServer(port, self._on_local_state, self.room_control_id)
        self._local_state: dict | None = None
        self._cloud_extras: dict = {}
        self._started = time.time()

    # ---------- источник данных ----------

    @property
    def local_active(self) -> bool:
        """Пульт общается напрямую с нами, облако не нужно."""
        return self.server.online and self._local_state is not None

    @property
    def source(self) -> str:
        return "Локально" if self.local_only or self.local_active else "Облако"

    def _on_local_state(self, state: dict, raw_data: str) -> None:
        """Вызывается сервером при каждом опросе от пульта."""
        first = self._local_state is None
        self._local_state = state
        if first:
            _LOGGER.info("Пульт перешёл на локальный сервер, облако больше не нужно")
        self.hass.loop.call_soon_threadsafe(
            lambda: self.async_set_updated_data(self._merged())
        )

    def _merged(self) -> dict:
        data = dict(self._local_state or {})
        # Код ошибки приходит и от пульта. Расписание пока только из облака —
        # показываем последнее известное значение.
        for key in ("schedule",):
            data.setdefault(key, self._cloud_extras.get(key))
        if "error_code" not in data:
            data["error_code"] = self._cloud_extras.get("error_code")
        data["source"] = self.source
        return data

    async def _async_update_data(self) -> dict:
        if self.local_active:
            return self._merged()
        if self.local_only:
            raise UpdateFailed("Нет свежих данных от локального пульта Rinnai")
        try:
            data = await self.hass.async_add_executor_job(self.api.get_status)
        except RinnaiPultOfflineError as err:
            # «Пульт отключён от облака» — норма, когда он ушёл на наш сервер:
            # после старта HA он стучится к нам через несколько секунд. Даём
            # ему льготное окно и отдаём последнее известное состояние, а не
            # роняем интеграцию в ERROR. Молчит дольше окна — это уже потеря связи.
            last_contact = max(self._started, self.server.last_seen or 0)
            if time.time() - last_contact < LOCAL_GRACE_SECONDS:
                _LOGGER.debug("Ждём пульт на локальном сервере: %s", err)
                # Пока состояния нет вовсе (первый опрос) — сущности остаются
                # недоступными, а не показывают «выключено» с пустыми данными.
                return self.data
            raise UpdateFailed(str(err)) from err
        except RinnaiError as err:
            raise UpdateFailed(str(err)) from err
        self._cloud_extras = {
            "schedule": data.get("schedule"),
            "error_code": data.get("error_code"),
        }
        data["source"] = self.source
        return data

    # ---------- команды ----------

    async def _send_cloud(self, func, *args) -> None:
        if self.local_only or self.api is None:
            raise HomeAssistantError("Облачное управление отключено")
        try:
            await self.hass.async_add_executor_job(func, *args)
        except RinnaiError as err:
            raise HomeAssistantError(f"Команда котлу Rinnai не прошла: {err}") from err
        await self.async_request_refresh()

    def _patch_local(self, index: int, value: int) -> None:
        self._require_local()
        self.server.queue(lambda data: protocol.set_byte(data, index, value))

    def _require_local(self) -> None:
        if not self.local_active:
            raise HomeAssistantError("Нет свежих данных от пульта; команда не отправлена")

    def _patch_setpoint(self, index: int, value: int) -> None:
        self._require_local()
        # Дамп WF-100W-RU: активная уставка дублируется в байтах 1 и 2.
        # Второй байт меняем только при совпадении уставок и активном режиме.
        def patch(data: str) -> str:
            active = protocol.IDX_CIRCUIT_SET if protocol.byte(data, 0) & FLAG_CIRCUIT_MODE else protocol.IDX_ROOM_SET
            mirrored = protocol.byte(data, 1) == protocol.byte(data, 2)
            result = protocol.set_byte(data, index, value)
            if index == active and mirrored:
                result = protocol.set_byte(result, 3 - index, value)
            return result
        self.server.queue(patch)

    async def async_update_flags(self, set_bits: int = 0, clear_bits: int = 0) -> None:
        # Local 2.2.1: применяем маску к свежему пакету, сохраняя прочие биты.
        if self.local_only or self.local_active:
            self._require_local()
            self.server.queue(lambda data: protocol.set_byte(
                data, 0, (protocol.byte(data, 0) | set_bits) & ~clear_bits & 0xff))
            return
        flags = int((self.data or {}).get("flags", 0))
        await self._send_cloud(self.api.set_flags, (flags | set_bits) & ~clear_bits & 0xff)

    async def async_set_room_temp(self, temp: int) -> None:
        if self.local_only or self.local_active:
            self._patch_setpoint(protocol.IDX_ROOM_SET, temp)
            return
        await self._send_cloud(self.api.set_room_temp, temp)

    async def async_set_circuit_temp(self, temp: int) -> None:
        if self.local_only or self.local_active:
            self._patch_setpoint(protocol.IDX_CIRCUIT_SET, temp)
            return
        await self._send_cloud(self.api.set_circuit_temp, temp)

    async def async_set_hot_water_temp(self, temp: int) -> None:
        if self.local_only or self.local_active:
            self._patch_local(protocol.IDX_HW_SET, temp)
            return
        await self._send_cloud(self.api.set_hot_water_temp, temp)

    async def async_set_flags(self, flags: int) -> None:
        if self.local_only or self.local_active:
            self._patch_local(protocol.IDX_FLAGS, flags)
            return
        await self._send_cloud(self.api.set_flags, flags)

    async def async_set_away(self, enable: bool) -> None:
        if self.local_only or self.local_active:
            self._patch_local(protocol.IDX_AWAY, 0x80 if enable else 0x00)
            return
        await self._send_cloud(self.api.set_away, enable)

    async def async_set_economy(self, enable: bool) -> None:
        if self.local_only:
            raise HomeAssistantError("Экономичный режим не расшифрован для локального управления")
        await self._send_mode_only_via_cloud(self.api.set_economy, enable, "экономичный")

    async def async_set_sleep(self, enable: bool) -> None:
        if self.local_only:
            raise HomeAssistantError("Ночной режим не расшифрован для локального управления")
        await self._send_mode_only_via_cloud(self.api.set_sleep, enable, "ночной")

    async def _send_mode_only_via_cloud(self, func, enable: bool, name: str) -> None:
        """Байт этих режимов в пакете пульта не найден — только через облако."""
        if self.local_only or self.local_active:
            raise HomeAssistantError(
                f"Режим «{name}» пока доступен только через облако Rinnai, "
                "а пульт сейчас работает локально."
            )
        await self._send_cloud(func, enable)
