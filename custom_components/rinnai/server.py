"""Локальный сервер, заменяющий облако Rinnai для пульта WF-100W.

Пульт сам опрашивает сервер по HTTP на порту 9105:
  POST /register — представляется, ждёт токен;
  POST /state    — присылает состояние и получает его же обратно.

Команды доставляются пульту в ответе на его собственный опрос: мы берём
присланные им данные и правим ТОЛЬКО нужные байты. Неизвестные поля
возвращаются дословно — это главное отличие от известных реализаций,
которые пересобирают пакет целиком и рискуют исказить чужие поля.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
import time

from aiohttp import web

from .protocol import Packet, ProtocolError, decode_state, checksum, set_clock
from .const import LOCAL_TIMEOUT_SECONDS, COMMAND_TTL_SECONDS

_LOGGER = logging.getLogger(__name__)

REGISTER_PREFIX = "re0000"
REGISTER_REPLY_PREFIX = "re0100"
STATE_PREFIX = "re0101"
COMMAND_REPLY_PREFIX = "sm0101"
TOKEN_LENGTH = 32


class RinnaiLocalServer:
    """HTTP-сервер, к которому подключается пульт вместо облака."""

    def __init__(self, port: int, on_state: Callable[[dict, str], None], room_control_id: str,
                 clock: Callable[[], datetime] | None = None) -> None:
        self._port = port
        # Часы для ответа пульту (как у облака): функция, возвращающая местное время; None — не подставлять.
        self._clock = clock
        self._clock_warned = False
        self.room_control_id = room_control_id.lower()
        self._on_state = on_state
        self._runner: web.AppRunner | None = None
        self._pending: list[tuple[float, Callable[[str], str]]] = []
        self.serial: str | None = None
        self.last_seen: float | None = None
        self.last_raw: str | None = None

    # ---------- жизненный цикл ----------

    async def start(self) -> None:
        app = web.Application(client_max_size=16384)
        app.router.add_get("/health", self._handle_health)
        app.router.add_post("/register", self._handle_register)
        app.router.add_post("/state", self._handle_state)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        _LOGGER.info("Локальный сервер Rinnai слушает порт %s", self._port)

    async def stop(self) -> None:
        self._pending.clear()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            _LOGGER.info("Локальный сервер Rinnai остановлен")

    # ---------- очередь команд ----------

    def queue(self, patch: Callable[[str], str]) -> None:
        """Поставить правку данных состояния в очередь.

        Правка применится при следующем опросе пульта (обычно в течение
        нескольких секунд) и уйдёт ему как команда.
        """
        # Local 2.2.1: команды не сохраняются при потере связи и не живут бесконечно.
        if not self.online:
            raise ProtocolError("пульт недоступен")
        self._pending.append((time.monotonic() + COMMAND_TTL_SECONDS, patch))

    @property
    def online(self) -> bool:
        return self.last_seen is not None and (time.monotonic() - self.last_seen) < LOCAL_TIMEOUT_SECONDS

    @property
    def pending_commands(self) -> int:
        return len(self._pending)

    # ---------- обработчики ----------

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Local 2.2.1: проверка сервера до изменения маршрута пульта."""
        # MAC пульта — единственная проверка в /state (Referer), поэтому целиком его не показываем.
        return web.json_response({"service": "rinnai", "version": "2.2.3",
                                  "room_control_id": "*" * 8 + self.room_control_id[-4:],
                                  "online": self.online})

    async def _handle_register(self, request: web.Request) -> web.Response:
        body = (await request.read()).decode(errors="replace").strip()
        # Local 2.2.1: принимаем регистрацию только ожидаемого MAC.
        _LOGGER.debug("Получена регистрация локального пульта")
        try:
            packet = Packet.parse(body)
        except ProtocolError as err:
            _LOGGER.warning("нераспознанный /register (%s): %.200s", err, body)
            return web.Response(text="", content_type="text/plain")

        if (packet.prefix != REGISTER_PREFIX
                or not packet.data.lower().startswith(self.room_control_id)
                or body[-4:-2].lower() != f"{checksum(packet.data):02x}"):
            return web.Response(status=403, text="Controller rejected")

        self.serial = packet.data
        _LOGGER.info("Пульт зарегистрирован, серийный номер %s", packet.data)

        reply = Packet(
            prefix=REGISTER_REPLY_PREFIX,
            command=packet.command,
            data="1" * TOKEN_LENGTH,
        )
        return self._reply(reply)

    async def _handle_state(self, request: web.Request) -> web.Response:
        # Local 2.2.1: MAC из Referer подтверждён в эталонном PCAP.
        referer = request.headers.get("Referer", "").strip().lower().replace(":", "").replace("-", "")
        if referer != self.room_control_id:
            return web.Response(status=403, text="Controller rejected")
        body = (await request.read()).decode(errors="replace").strip()
        _LOGGER.debug("Получено состояние локального пульта")
        try:
            packet = Packet.parse(body)
        except ProtocolError as err:
            _LOGGER.warning("нераспознанный /state (%s): %.200s", err, body)
            return web.Response(text="", content_type="text/plain")

        if (packet.prefix != STATE_PREFIX or packet.command != 1
                or body[-4:-2].lower() != f"{checksum(packet.data):02x}"):
            return web.Response(status=400, text="Invalid state packet")

        try:
            state = decode_state(packet.data)
        except ProtocolError as err:
            _LOGGER.warning("Не удалось разобрать состояние: %s", err)
            return web.Response(status=400, text="Invalid state data")
        else:
            self.last_seen = time.monotonic()  # монотонное: скачок системных часов не путает «на связи»
            self.last_raw = body
            self._on_state(state, packet.data)

        data = packet.data
        prefix = packet.prefix
        if self._pending:
            # Команда держится в очереди, пока пакет пульта её не подтвердит (правка больше ничего
            # не меняет) или не истечёт срок: ответ мог не дойти (обрыв связи, перезапуск HA), а все
            # правки можно безопасно применить повторно (установка/сброс битов, фиксированные значения).
            pending, keep = self._pending, []
            for deadline, patch in pending:
                if time.monotonic() > deadline:
                    _LOGGER.warning("Просроченная команда пульту отброшена")
                    continue
                try:
                    patched = patch(data)
                except ProtocolError as err:
                    _LOGGER.error("команда пропущена, правка невозможна: %s", err)
                    continue
                if patched != data:
                    data = patched
                    keep.append((deadline, patch))
            self._pending = keep
            if data != packet.data:
                # Пульт применяет изменения только у пакета с этим префиксом.
                prefix = COMMAND_REPLY_PREFIX
                _LOGGER.info("Команда отправлена пульту: %s -> %s", packet.data, data)

        # Время в каждом ответе, как у облака (префикс не меняет). Неправдоподобное время
        # (часы хоста ещё не синхронизированы) или ошибка часов — байты 49–55 не трогаем.
        if self._clock is not None:
            try:
                now = self._clock()
                if now.year >= 2025:
                    data = set_clock(data, now)
                elif not self._clock_warned:
                    self._clock_warned = True
                    _LOGGER.warning("Часы HA показывают %s — время пульту не отправляем", now)
            except Exception:  # noqa: BLE001 — ответ пульту важнее часов
                if not self._clock_warned:
                    self._clock_warned = True
                    _LOGGER.exception("Не удалось получить время для пульта")
        reply = Packet(prefix=prefix, command=packet.command, data=data)
        return self._reply(reply)

    @staticmethod
    def _reply(packet: Packet) -> web.Response:
        """Дамп облака: Content-Length равен длине пакета, без CRLF."""
        body = packet.build()
        _LOGGER.debug("сервер -> пульт: %s", packet.build())
        return web.Response(body=body.encode(), content_type="text/plain")
