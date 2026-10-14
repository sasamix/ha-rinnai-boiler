# Rinnai Boiler — local-first fork

Home Assistant integration for Rinnai WF-100W / WF-S100 Wi-Fi controllers.
This fork adds explicit local setup without a registered phone DeviceId.
Based on [abaskalov/ha-rinnai-boiler](https://github.com/abaskalov/ha-rinnai-boiler).
Original MIT license and protocol credits are retained.

## Installation through HACS

1. If the original repository is downloaded in HACS, remove that download first.
   Both projects use the same `rinnai` domain and must not be installed together.
   Preserve any existing integration configuration; entity unique IDs are unchanged.
2. HACS → Custom repositories → add
   `https://github.com/sasamix/ha-rinnai-boiler`, category **Integration**.
3. Download the integration from the `main` branch and restart Home Assistant.
4. Settings → Devices & services → Add integration → **Rinnai Boiler**.

## Local setup / Локальная настройка

Enable **Local only / Только локально**, enter the controller's Wi-Fi MAC
(12 hexadecimal characters, colons accepted), leave **DeviceId empty**,
and use TCP port **9105**.

The entry is created without any cloud request. Entities remain unavailable
until valid state packets arrive from the configured controller.

Verify the listener first: `http://HOME_ASSISTANT_IP:9105/health`.
It reports the configured controller MAC, version, and connection state.
`online: false` before redirection is normal.

The controller normally sends plain HTTP to `wifiboilers1.rinnai.co.kr:9105`.
Redirect that connection to Home Assistant using a controller-specific router
dstnat rule, or DNS when appropriate for your topology. Ensure replies travel
through the translating router. If the networks use different gateways, a narrow
srcnat rule may be needed. RouterOS NAT changes require removing the old
controller connection or reconnecting that controller to Wi-Fi.

The server must be reachable on TCP 9105. A Docker installation needs host
networking or an explicitly published port. Do not expose it to the internet.

When `/health` reports `online: true`, check HA temperatures against the physical
controller. Change the room setpoint by one degree and back, then test hot water.
HA reports the next measured state packet, rather than assuming a command worked.

**Пока пульт направлен на HA, штатное приложение Rinnai не видит котёл.**
Для возврата облака выключите перенаправление и переподключите пульт к Wi-Fi.

## Changes in 2.2.1

- Local configuration needs only the controller MAC and listener port.
- Local-only entries never poll the cloud, including after disconnects.
- Room/hot-water commands patch the latest complete state payload.
- Mirrored active setpoints are updated together when both fields already agree.
- Flag switches preserve other bits and apply to the next fresh state packet.
- Controller MAC, frame length, hex encoding and checksum are checked.
- Commands require fresh state and expire after 30 seconds.
- Connection diagnosis updates every 10 seconds; packets expire after 30 seconds.
- Health endpoint allows testing the server before redirecting the controller.
- Economy/night controls are omitted for local-only entries: their local fields
  have not been confirmed.
- Cloud configuration accepts either a UUID or the 16-character Android ID used
  by some Android Rinnai versions. It still needs a registered identifier.

## Validation and limits

The implementation was replay-tested against 26 captured HTTP frames from a
WF-100W-RU, including room and DHW changes and subsequent state confirmation.
No private capture, controller session token or personal network configuration
is included in this repository.

Run the reproducible synthetic HTTP/configuration tests:

```sh
python -m pip install aiohttp voluptuous
python tests/verify_local.py
python -m compileall -q custom_components/rinnai
```

Tests use a real aiohttp server and small Home Assistant stubs. Physical appliance
operation and an actual Home Assistant runtime are still pending verification.
Registration tests use synthetic packets. Other operating modes are inherited
from upstream and have not been verified on every controller/firmware.

Unknown payload fields are echoed unchanged. Long-term schedule/clock behavior
needs observation; this fork does not synthesize the cloud's clock fields.
MAC filtering identifies the intended controller but is not cryptographic
authentication. Use a trusted local network.

## Credits and license

- Original integration: [abaskalov](https://github.com/abaskalov/ha-rinnai-boiler).
- Protocol groundwork: [mog422/open-rinnai-server](https://github.com/mog422/open-rinnai-server)
  and [zobithecat/rinnai-ha](https://github.com/zobithecat/rinnai-ha).
- MIT, see [LICENSE](LICENSE). Not affiliated with Rinnai.
