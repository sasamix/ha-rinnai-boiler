"""Replay the user's capture against the real aiohttp server; HA uses small stubs."""
import asyncio
import importlib
import json
import pathlib
import struct
import sys
import time
import types
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
pkg = types.ModuleType('rinnai_test')
pkg.__path__ = [str(ROOT / 'custom_components/rinnai')]
sys.modules[pkg.__name__] = pkg

def module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m

class HAError(Exception): pass
class UpdateFailed(Exception): pass
class Coordinator:
    def __class_getitem__(cls, _): return cls
    def __init__(self, hass, logger, **kwargs):
        self.hass = hass
        self.data = None
    def async_set_updated_data(self, data): self.data = data

class ConfigFlow:
    def __init_subclass__(cls, **kwargs): pass
    async def async_set_unique_id(self, value): self.unique_id = value
    def _abort_if_unique_id_configured(self): pass
    def async_create_entry(self, **kwargs): return {'type': 'create_entry', **kwargs}
    def async_show_form(self, **kwargs): return {'type': 'form', **kwargs}

module('homeassistant')
module('homeassistant.config_entries', ConfigEntry=object, ConfigFlow=ConfigFlow, ConfigFlowResult=dict)
module('homeassistant.core', HomeAssistant=object)
module('homeassistant.exceptions', HomeAssistantError=HAError)
module('homeassistant.helpers')
module('homeassistant.helpers.update_coordinator', DataUpdateCoordinator=Coordinator, UpdateFailed=UpdateFailed)

protocol = importlib.import_module('rinnai_test.protocol')
server_mod = importlib.import_module('rinnai_test.server')
coord_mod = importlib.import_module('rinnai_test.coordinator')
flow_mod = importlib.import_module('rinnai_test.config_flow')

def capture_frames(path):
    b = pathlib.Path(path).read_bytes()
    pos = 0
    result = []
    while pos < len(b):
        kind, length = struct.unpack_from('<II', b, pos)
        if kind == 6:
            cap = struct.unpack_from('<I', b, pos + 20)[0]
            packet = b[pos + 28:pos + 28 + cap]
            if packet[12:14] == b'\x08\x00':
                ip = packet[14:]
                if ip[9] == 6:
                    ih = (ip[0] & 15) * 4
                    tcp = ip[ih:]
                    payload = tcp[(tcp[12] >> 4) * 4:struct.unpack_from('!H', ip, 2)[0] - ih]
                    if b'\r\n\r\n' in payload:
                        header, body = payload.split(b'\r\n\r\n', 1)
                        result.append((header.startswith(b'POST'), body.decode().strip()))
        pos += length
    return result

async def verify(path):
    from aiohttp import ClientSession
    from aiohttp.test_utils import TestClient, TestServer
    if path:
        frames = capture_frames(path)
    else:
        # Synthetic status: no user MAC, session token or original capture.
        data = "0d18183217a902ffff00" + "00" * 66
        frames = [(True, protocol.Packet("re0101", 1, data).build()),
                  (False, protocol.Packet("re0101", 1, data).build())]
    assert frames
    # Real voluptuous schema and local flow: MAC only, no API instantiation.
    flow = flow_mod.RinnaiConfigFlow()
    form = await flow.async_step_user()
    inputs = form['data_schema']({'room_control_id': 'AA:BB:CC:DD:EE:FF'})
    assert inputs['local_only'] and inputs['local_port'] == 9105
    with patch.object(flow_mod, 'RinnaiApi', side_effect=AssertionError('Local flow touched cloud')):
        result = await flow.async_step_user(inputs)
    assert result['type'] == 'create_entry'
    assert result['data']['device_id'] == '' and result['data']['local_only']
    assert result['data']['room_control_id'] == 'aabbccddeeff'
    assert result['options']['local_port'] == 9105
    result = await flow.async_step_user({'room_control_id': 'bad', 'local_only': True})
    assert result['errors']['room_control_id'] == 'invalid_id'
    for _, raw in frames:
        packet = protocol.Packet.parse(raw)
        assert packet.checksum_ok and packet.build() == raw
        protocol.decode_state(packet.data)
    hass = types.SimpleNamespace(loop=asyncio.get_running_loop())
    entry = types.SimpleNamespace(data={'room_control_id': 'aabbccddeeff', 'local_only': True}, options={})
    coord = coord_mod.RinnaiCoordinator(hass, entry)
    assert coord.api is None
    try:
        await coord._async_update_data()
        assert False, 'No-state update must fail'
    except UpdateFailed: pass
    try:
        await coord.async_set_hot_water_temp(47)
        assert False, 'No-state write must fail'
    except HAError: pass

    app = server_mod.web.Application()
    app.router.add_post('/state', coord.server._handle_state)
    app.router.add_post('/register', coord.server._handle_register)
    app.router.add_get('/health', coord.server._handle_health)
    client = TestClient(TestServer(app))
    await client.start_server()
    headers = {'Referer': 'aabbccddeeff'}
    initial = next(raw for request, raw in frames if request)
    async def post(raw, hdr=headers):
        response = await client.post('/state', data=raw.encode(), headers=hdr)
        return response, await response.text()
    try:
        response = await client.get('/health')
        health = await response.json()
        assert health['room_control_id'] == headers['Referer'] and not health['online']
        # Every captured request is accepted and echoed byte for byte when idle.
        for request, raw in frames:
            if request:
                response, body = await post(raw)
                assert response.status == 200 and body == raw
                assert int(response.headers['Content-Length']) == len(raw)
        await asyncio.sleep(0)
        assert coord.local_active and coord.data['source'] == 'Локально'
        # Return to the captured initial state before producing commands.
        await post(initial)
        await asyncio.sleep(0)
        await coord.async_set_room_temp(25)
        response, body = await post(initial)
        packet = protocol.Packet.parse(body)
        source = protocol.Packet.parse(initial)
        expected = protocol.set_byte(protocol.set_byte(source.data, 1, 25), 2, 25)
        assert packet.prefix == 'sm0101' and packet.data == expected
        assert body == protocol.Packet('sm0101', 1, expected).build()
        # HA continues showing the measured state until the pult confirms it.
        await asyncio.sleep(0)
        assert coord.data['room_set'] == 24
        confirmation = protocol.Packet('re0101', 1, expected).build()
        await post(confirmation)
        await asyncio.sleep(0)
        assert coord.data['room_set'] == 25

        await coord.async_set_hot_water_temp(47)
        _, body = await post(initial)
        actual = protocol.Packet.parse(body)
        assert actual.data == protocol.set_byte(source.data, 3, 47)
        # Multiple changes preserve all bits that are not explicitly targeted.
        flags_data = protocol.set_byte(source.data, 0, 0xFD)
        flags_frame = protocol.Packet('re0101', 1, flags_data).build()
        await coord.async_update_flags(clear_bits=4)
        await coord.async_update_flags(set_bits=2)
        _, body = await post(flags_frame)
        assert protocol.byte(protocol.Packet.parse(body).data, 0) == 0xFB

        last_seen = coord.server.last_seen
        response, _ = await post(initial, {'Referer': '000000000000'})
        assert response.status == 403 and coord.server.last_seen == last_seen
        corrupted = initial[:-4] + '00' + initial[-2:]
        response, _ = await post(corrupted)
        assert response.status == 400 and coord.server.last_seen == last_seen
        # Expired commands never get applied on reconnection.
        await coord.async_set_hot_water_temp(47)
        future = time.monotonic() + 31
        with patch.object(server_mod.time, 'monotonic', return_value=future):
            _, body = await post(initial)
            assert body == initial
        coord.server.last_seen = time.time() - 31
        try:
            await coord.async_set_room_temp(25)
            assert False, 'Offline write must fail'
        except HAError: pass
        try:
            await coord._async_update_data()
            assert False, 'Offline update must fail without cloud'
        except UpdateFailed: pass
        register = protocol.Packet('re0000', 0, headers['Referer'] + '0001').build()
        response = await client.post('/register', data=register)
        assert response.status == 200
        reply = protocol.Packet.parse(await response.text())
        assert reply.prefix == 're0100' and len(reply.data) == 32 and reply.checksum_ok
        response = await client.post('/register', data=protocol.Packet('re0000', 0, '0' * 16).build())
        assert response.status == 403
        for raw in [initial + 'x', initial[:-2], initial[:10] + 'xx' + initial[12:]]:
            try:
                protocol.Packet.parse(raw)
                assert False, 'Malformed frame must fail'
            except protocol.ProtocolError: pass
        print(f'PASS: {len(frames)} captured frames; real HTTP replay; room/DHW commands; confirmation; identity; checksum; expiry; offline; registration.')
    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(verify(sys.argv[1] if len(sys.argv) > 1 else None))
