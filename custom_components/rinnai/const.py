"""Константы интеграции газового котла Rinnai."""

DOMAIN = "rinnai"

CONF_ROOM_CONTROL_ID = "room_control_id"
CONF_DEVICE_ID = "device_id"

CLOUD_HOSTNAME = "wifiboilers1.rinnai.co.kr"
CLOUD_PORT = 11443
# Адрес облака фиксируется намеренно: так интеграция не зависит от подмены
# DNS в локальной сети (её используем, чтобы увести пульт на свой сервер).
CLOUD_IP = "58.72.180.69"  # wifiboilers1.rinnai.co.kr
USER_AGENT = "RinnaiSmartApp/1.2.6.1 CFNetwork/3860.700.1 Darwin/25.6.0"
ETX = "7d"

# Биты поля флагов (CMD 01)
FLAG_POWER = 0x01
FLAG_CIRCUIT_MODE = 0x02
FLAG_HEATING = 0x04
FLAG_HOT_WATER = 0x08
FLAG_PRE_HEAT = 0x10
FLAG_QUICK_HEAT = 0x20

# Команды управления (sm0003)
CMD_FLAGS = "01"
CMD_ROOM_TEMP = "02"
CMD_CIRCUIT_TEMP = "03"
CMD_HW_TEMP = "04"
CMD_AWAY = "05"
CMD_ECONOMY = "07"
CMD_SLEEP = "08"

# Запросы (sm0002)
QUERY_STATUS = "01"
QUERY_ERROR = "02"
QUERY_SCHEDULE = "03"

# Локальный сервер (замена облака)
CONF_LOCAL_PORT = "local_port"
DEFAULT_LOCAL_PORT = 9105

ROOM_MIN, ROOM_MAX = 10, 40
# Пульт WF-100W регулирует теплоноситель от 40 °C; котёл RBK-197 — до 80 °C.
CIRCUIT_MIN, CIRCUIT_MAX = 40, 80
HW_MIN, HW_MAX = 35, 60

SCAN_INTERVAL_SECONDS = 60
# Сколько ждать пульт на локальном сервере (после старта HA или после его
# последнего визита), прежде чем «пульт отключён от облака» считать потерей связи.
LOCAL_GRACE_SECONDS = 300
