"""Проверки режима Smart control, подогрева ГВС и часов (без HA и aiohttp): режим Smart control (байт 10) и часы (байты 49–55).

Эталон — пара из записи обмена пульта WF-100W-RU с облаком (2026-11-17 18:41:57): пакет пульта и ответ облака
на него без команды (re0101). Облако меняет только часы: год (54) и секунды (55), минуты/часы/день недели/число/месяц — те же.
"""
import datetime as dt
import importlib.util
import pathlib
import sys

path = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "rinnai" / "protocol.py"
spec = importlib.util.spec_from_file_location("rinnai_protocol", path)
protocol = importlib.util.module_from_spec(spec)
sys.modules["rinnai_protocol"] = protocol
spec.loader.exec_module(protocol)

ZEROS = "00"
def state(**over):
    b = ["0f", "28", "28", "2a", "18", "a9", "02", "ff", "ff"] + [ZEROS] * 67
    b[14], b[73] = "17", "05"
    b[49:54] = ["41", "18", "02", "17", "11"]  # 18:41, вторник, 17.11 — так шлёт пульт (54–55 = 00)
    for k, v in over.items():
        b[int(k[1:])] = v
    return "".join(b)

pult = state()
cloud = state(b49="41", b54="26", b55="57")  # ответ облака в 18:41:57
assert protocol.set_clock(pult, dt.datetime(2026, 11, 17, 18, 41, 57)) == cloud, "часы не совпали с облаком"
assert protocol.set_clock("0f28", dt.datetime(2026, 11, 17)) == "0f28", "короткий пакет должен остаться как есть"

for raw, mode in (("00", "normal"), ("03", "economy"), ("04", "sleep"), ("80", "auto")):
    s = protocol.decode_state(state(b10=raw))
    assert protocol.SMART_MODES[s["smart_mode"]] == mode, (raw, s["smart_mode"])
assert protocol.decode_state(state(b0="1f"))["pre_heat"] is True
assert protocol.decode_state(state(b9="80"))["away"] is True
print("verify_fork: OK")
