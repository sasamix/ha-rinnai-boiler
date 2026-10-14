"""Разбор пакетов пульта Rinnai.

Формат: <префикс 6 симв.><команда 2><длина 2><данные><7d>
Длина — число hex-символов данных.

Принцип безопасности: данные состояния НЕ пересобираются из разобранных
полей. Мы правим только те байты, которые осознанно меняем, а всё
остальное (включая неизвестные поля) возвращается пульту дословно.
Так исключено повреждение полей, назначение которых нам неизвестно.
"""
from __future__ import annotations

from dataclasses import dataclass

ETX = "7d"


class ProtocolError(ValueError):
    """Пакет не разобран."""


def checksum(data: str) -> int:
    """Контрольная сумма протокола: сумма ASCII-кодов символов данных % 256.

    Используется в обмене с пультом (порт 9105). В облачном HTTP-API это
    поле всегда нулевое — там сумма не проверяется.
    """
    return sum(ord(c) for c in data) % 256


@dataclass
class Packet:
    prefix: str
    command: int
    data: str
    checksum_ok: bool = True

    @classmethod
    def parse(cls, raw: str) -> "Packet":
        raw = raw.strip()
        if len(raw) < 14:
            raise ProtocolError(f"слишком короткий пакет: {raw!r}")
        if not raw.endswith(ETX):
            raise ProtocolError(f"нет завершителя 7d: {raw!r}")
        prefix = raw[0:6]
        try:
            command = int(raw[6:8], 16)
            length = int(raw[8:10], 16)
        except ValueError as err:
            raise ProtocolError(f"нечитаемый заголовок: {raw!r}") from err
        # Local 2.2.1: строгое обрамление, hex и полный байт данных.
        if length % 2 or len(raw) != 10 + length + 4:
            raise ProtocolError("длина пакета не совпадает с заголовком")
        data = raw[10 : 10 + length]
        try:
            bytes.fromhex(data)
        except ValueError as err:
            raise ProtocolError("данные не являются hex") from err
        if len(data) != length:
            raise ProtocolError(
                f"объявлено {length} симв. данных, получено {len(data)}: {raw!r}"
            )
        received = raw[10 + length : -len(ETX)]
        expected = f"{checksum(data):02x}"
        # Облако присылает нули вместо суммы — это не ошибка.
        ok = received in (expected, "00", "")
        return cls(prefix=prefix, command=command, data=data, checksum_ok=ok)

    def build(self) -> str:
        return (
            f"{self.prefix}{self.command:02x}{len(self.data):02x}"
            f"{self.data}{checksum(self.data):02x}{ETX}"
        )


def byte(data: str, index: int) -> int:
    """Значение байта по индексу (данные — hex-строка)."""
    return int(data[index * 2 : index * 2 + 2], 16)


def set_byte(data: str, index: int, value: int) -> str:
    """Заменить один байт, не трогая остальные."""
    pos = index * 2
    if pos + 2 > len(data):
        raise ProtocolError(f"байт {index} за пределами данных длиной {len(data)}")
    return data[:pos] + f"{value & 0xFF:02x}" + data[pos + 2 :]


# Смещения байтов в данных состояния (проверено на реальном котле)
IDX_FLAGS = 0
IDX_ROOM_SET = 1
IDX_CIRCUIT_SET = 2
IDX_HW_SET = 3
IDX_ROOM_CUR = 4
IDX_WATER_CUR = 5
IDX_DRIVE = 6
# В пакете пульта после байта состояния идут два неизвестных байта,
# и только затем признак отъезда (в укороченном облачном пакете он на 7-м).
IDX_AWAY = 9
# Байты 7-8 в пакете пульта совпадают с облачным запросом кода ошибки
# (ffff = ошибок нет) — проверено побайтовым сопоставлением.
IDX_ERROR = 7


def half_degree(value: int) -> float:
    """Старший бит кодирует +0.5 °C."""
    return (value & 0x7F) + 0.5 if value & 0x80 else float(value)


def decode_state(data: str) -> dict:
    """Разобрать известные поля; неизвестные не трогаем."""
    if len(data) < 20:
        raise ProtocolError(f"данные состояния короче ожидаемого: {data!r}")
    flags = byte(data, IDX_FLAGS)
    drive = byte(data, IDX_DRIVE)
    return {
        "flags": flags,
        "power": bool(flags & 0x01),
        "circuit_mode": bool(flags & 0x02),
        "heating": bool(flags & 0x04),
        "hot_water": bool(flags & 0x08),
        "pre_heat": bool(flags & 0x10),
        "quick_heat": bool(flags & 0x20),
        "room_set": byte(data, IDX_ROOM_SET),
        "circuit_set": byte(data, IDX_CIRCUIT_SET),
        "hw_set": half_degree(byte(data, IDX_HW_SET)),
        "room_cur": byte(data, IDX_ROOM_CUR),
        "hw_cur": half_degree(byte(data, IDX_WATER_CUR)),
        "combustion_state": drive & 0x0F,
        "hot_water_using": bool(drive & 0x20),
        "away": byte(data, IDX_AWAY) > 0,
        "error_code": (
            None
            if data[IDX_ERROR * 2 : IDX_ERROR * 2 + 4].lower() == "ffff"
            else data[IDX_ERROR * 2 : IDX_ERROR * 2 + 4]
        ),
        "raw_state": data,
    }
