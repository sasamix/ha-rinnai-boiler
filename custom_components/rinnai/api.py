"""Клиент облака Rinnai для котла RBK-197 (пульт WF-100W).

Протокол восстановлен по трафику официального приложения:
  POST /query   тело sm0002<cmd><len><data>7d
  POST /control тело sm0003<cmd><len><data>7d   (данные дополняются нулями до 4 симв.)
Авторизация — заголовки RoomControlId (MAC пульта) и DeviceId.
"""
from __future__ import annotations

import http.client
import logging
import socket
import ssl

from .const import (
    CMD_AWAY, CMD_CIRCUIT_TEMP, CMD_ECONOMY, CMD_FLAGS, CMD_HW_TEMP,
    CMD_ROOM_TEMP, CMD_SLEEP, ETX, FLAG_CIRCUIT_MODE, FLAG_HEATING,
    FLAG_HOT_WATER, FLAG_POWER, FLAG_PRE_HEAT, FLAG_QUICK_HEAT,
    CLOUD_HOSTNAME, CLOUD_IP, CLOUD_PORT, QUERY_ERROR, QUERY_SCHEDULE,
    QUERY_STATUS, USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

_ERROR_SUBCODES = {
    "08": "пульт отключён от облака",
    "10": "устройство удалено из аккаунта",
    "11": "котёл сообщает об ошибке",
    "12": "ошибка котла, устройство удалено",
}


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS-соединение с проверкой сертификата по имени, но с подключением
    по заданному IP. Нужно, чтобы локальная подмена DNS не уводила запросы
    самого Home Assistant на наш же сервер."""

    def __init__(self, hostname: str, port: int, context: ssl.SSLContext,
                 ip: str | None) -> None:
        super().__init__(hostname, port=port, timeout=20, context=context)
        self._ip = ip

    def connect(self) -> None:
        target = self._ip or self.host
        sock = socket.create_connection((target, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class RinnaiError(Exception):
    """Ошибка обмена с облаком Rinnai."""


class RinnaiAuthError(RinnaiError):
    """Пульт недоступен по указанному RoomControlId."""


class RinnaiPultOfflineError(RinnaiError):
    """Облако отвечает, что пульт к нему не подключён (код 08).

    Штатно, когда пульт ушёл на наш локальный сервер."""


class RinnaiApi:
    """Синхронный клиент. Вызывать только из executor-потока."""

    def __init__(self, room_control_id: str, device_id: str) -> None:
        self._room_control_id = room_control_id
        self._device_id = device_id
        self._ctx: ssl.SSLContext | None = None

    def _ssl_context(self) -> ssl.SSLContext:
        """Контекст создаётся лениво: ssl.create_default_context() читает
        хранилище корневых сертификатов с диска и блокирует цикл событий,
        поэтому вызывается только внутри executor-потока."""
        if self._ctx is None:
            # Сертификат сервера валиден (TuringSign, *.rinnai.co.kr) — проверяем строго.
            self._ctx = ssl.create_default_context()
        return self._ctx

    def _post(self, path: str, body: str) -> str:
        headers = {
            "Content-Type": "text/plain",
            "Accept": "text/plain",
            "DeviceId": self._device_id,
            "RoomControlId": self._room_control_id,
            "User-Agent": USER_AGENT,
        }
        # Сначала по закреплённому адресу, при неудаче — обычным способом,
        # чтобы смена адреса облака когда-нибудь не сломала интеграцию.
        errors: list[str] = []
        for target in (CLOUD_IP, None):
            try:
                conn = _PinnedHTTPSConnection(
                    CLOUD_HOSTNAME, CLOUD_PORT, self._ssl_context(), target
                )
                try:
                    conn.request("POST", path, body=body.encode(), headers=headers)
                    response = conn.getresponse()
                    if response.status != 200:
                        raise RinnaiError(f"HTTP {response.status} от облака Rinnai")
                    return response.read().decode().strip()
                finally:
                    conn.close()
            except RinnaiError:
                raise
            except Exception as err:  # noqa: BLE001
                errors.append(f"{target or 'DNS'}: {err}")
        raise RinnaiError("нет связи с облаком Rinnai (" + "; ".join(errors) + ")")

    @staticmethod
    def _check_error(raw: str) -> None:
        if raw and len(raw) >= 12 and raw[8:10] == "ff":
            sub = raw[10:12]
            message = _ERROR_SUBCODES.get(sub, f"код {sub}")
            # Ни один из этих кодов не считаем ошибкой авторизации: «пульт
            # отключён от облака» — штатная ситуация, когда он ушёл на наш
            # локальный сервер, и останавливать интеграцию из-за неё нельзя.
            if sub == "08":
                raise RinnaiPultOfflineError(message)
            raise RinnaiError(message)

    # ---------- чтение ----------

    def _query(self, cmd: str) -> str:
        raw = self._post("/query", f"sm0002{cmd}0000{ETX}")
        self._check_error(raw)
        return raw

    def get_status(self) -> dict:
        data = self._parse_status(self._query(QUERY_STATUS))
        try:
            raw = self._query(QUERY_SCHEDULE)
            length = int(raw[8:10], 16)
            data["schedule"] = int(raw[10 : 10 + length][0:2], 16) > 0
        except (RinnaiError, ValueError, IndexError):
            data["schedule"] = None
        try:
            raw = self._query(QUERY_ERROR)
            length = int(raw[8:10], 16)
            code = raw[10 : 10 + length]
            # ffff — штатное состояние «ошибок нет».
            data["error_code"] = None if code.lower() in ("ffff", "") else code
        except (RinnaiError, ValueError, IndexError):
            data["error_code"] = None
        return data

    @staticmethod
    def _parse_status(raw: str) -> dict:
        if not raw or len(raw) < 22:
            raise RinnaiError(f"слишком короткий ответ: {raw!r}")
        try:
            length = int(raw[8:10], 16)
        except ValueError as err:
            raise RinnaiError(f"нечитаемый ответ: {raw!r}") from err
        payload = raw[10 : 10 + length]
        if len(payload) < 12:
            raise RinnaiError(f"неполные данные: {raw!r}")
        flags = int(payload[0:2], 16)

        def half(value: int) -> float:
            """Полуградусы: старший бит кодирует +0.5 °C."""
            return (value & 0x7F) + 0.5 if value & 0x80 else float(value)

        # Байт состояния горелки/водоразбора (см. open-rinnai-server)
        drive = int(payload[12:14], 16) if len(payload) >= 14 else 0

        return {
            "flags": flags,
            "power": bool(flags & FLAG_POWER),
            "circuit_mode": bool(flags & FLAG_CIRCUIT_MODE),
            "heating": bool(flags & FLAG_HEATING),
            "hot_water": bool(flags & FLAG_HOT_WATER),
            "pre_heat": bool(flags & FLAG_PRE_HEAT),
            "quick_heat": bool(flags & FLAG_QUICK_HEAT),
            "room_set": int(payload[2:4], 16),
            "circuit_set": int(payload[4:6], 16),
            "hw_set": half(int(payload[6:8], 16)),
            "room_cur": int(payload[8:10], 16),
            "hw_cur": half(int(payload[10:12], 16)),
            "combustion_state": drive & 0x0F,
            "hot_water_using": bool(drive & 0x20),
            # Проверено экспериментом: байт 7 == 0x80 при включённом режиме отъезда.
            "away": int(payload[14:16], 16) > 0 if len(payload) >= 16 else False,
            "raw": raw,
        }

    # ---------- управление ----------

    def _control(self, cmd: str, data: str) -> None:
        # Длина — число hex-символов данных; поле данных дополняется нулями
        # до 4 символов, итоговый пакет всегда 16 символов.
        packet = f"sm0003{cmd}{len(data):02x}{data.ljust(4, '0')}{ETX}"
        raw = self._post("/control", packet)
        self._check_error(raw)
        if len(raw) < 12 or raw[6:8] != cmd or raw[10:12] != "01":
            raise RinnaiError(f"команда {cmd} отклонена котлом: {raw!r}")

    def set_flags(self, flags: int) -> None:
        """CMD 01 — питание, режим, отопление, ГВС одним битовым полем."""
        self._control(CMD_FLAGS, f"{flags & 0xFF:02x}00")

    def set_room_temp(self, temp: int) -> None:
        self._control(CMD_ROOM_TEMP, f"{int(temp):02x}")

    def set_circuit_temp(self, temp: int) -> None:
        self._control(CMD_CIRCUIT_TEMP, f"{int(temp):02x}")

    def set_hot_water_temp(self, temp: int) -> None:
        self._control(CMD_HW_TEMP, f"{int(temp):02x}")

    def set_away(self, enable: bool) -> None:
        self._control(CMD_AWAY, "80" if enable else "00")

    def set_economy(self, enable: bool) -> None:
        self._control(CMD_ECONOMY, "80" if enable else "00")

    def set_sleep(self, enable: bool) -> None:
        self._control(CMD_SLEEP, "80" if enable else "00")
