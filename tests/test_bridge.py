"""
Wake word and trigger (push to talk) through the RB-01 bridge: bridge.py and the
require_wake_word switch of handle_utterance().

The WebSocket tests run the client against a small server in this file that writes frames the way
golang.org/x/net/websocket does: unmasked, one message per frame.
"""
import base64
import hashlib
import json
import socket
import struct
import threading

import pytest

import assistant
import bridge
from bridge import VOICE_TRIGGER_KEY, VOICE_WAKEWORD_KEY, BridgeFloat, MiniWebSocket, VoiceControl
from conftest import LANGUAGES, config_for, load_cases


class Posts:
    """Stands in for BridgeFloat: records the writes."""

    def __init__(self):
        self.values = []

    def post(self, values):
        self.values.append(values)

    post_now = post


def test_case_files_carry_the_default():
    for lang in LANGUAGES:
        assert isinstance(load_cases(lang).get("wake_word_enabled"), bool), lang


@pytest.mark.parametrize("enabled", [True, False])
def test_publish_sends_default_and_clears_trigger(enabled):
    posts = Posts()
    VoiceControl(enabled, bridge=posts).publish()
    assert posts.values == [{VOICE_WAKEWORD_KEY: int(enabled), VOICE_TRIGGER_KEY: 0}]


def test_bridge_switches_wake_word():
    control = VoiceControl(True)
    control.on_bridge({VOICE_WAKEWORD_KEY: 0, "qnh": 1013})
    assert not control.wake_word_enabled
    control.on_bridge({"qnh": 1014})  # a map without the key leaves it as it is
    assert not control.wake_word_enabled
    control.on_bridge({VOICE_WAKEWORD_KEY: 1})
    assert control.wake_word_enabled


def test_without_wake_word_audio_is_heard_only_after_trigger():
    control = VoiceControl(False, trigger_window=8)
    assert not control.poll(False, now=0)
    control.on_bridge({VOICE_TRIGGER_KEY: 1})
    assert control.poll(False, now=1) and control.triggered
    assert control.poll(False, now=8.9)
    assert not control.poll(False, now=9.1) and not control.triggered


def test_window_closing_writes_trigger_back():
    posts = Posts()
    control = VoiceControl(False, trigger_window=8, bridge=posts)
    control.on_bridge({VOICE_TRIGGER_KEY: 1})
    control.poll(False, now=0)
    control.poll(False, now=9)
    assert posts.values == [{VOICE_TRIGGER_KEY: 0}]
    control.end_trigger()  # already closed: nothing more to write
    assert posts.values == [{VOICE_TRIGGER_KEY: 0}]


def test_window_stays_open_while_speaking():
    control = VoiceControl(False, trigger_window=8)
    control.trigger()
    control.poll(False, now=0)
    assert control.poll(True, now=30) and control.triggered
    control.end_trigger()
    assert not control.poll(False, now=31)


def test_only_a_rising_edge_is_a_press():
    """The whole map comes again on every change of any key, the trigger still at 1."""
    control = VoiceControl(False, trigger_window=8)
    control.on_bridge({VOICE_TRIGGER_KEY: 1})
    control.poll(False, now=0)
    control.end_trigger()
    control.on_bridge({VOICE_TRIGGER_KEY: 1, "rpm": 2400})  # stale, before our 0 reached the server
    assert not control.poll(False, now=1)
    control.on_bridge({VOICE_TRIGGER_KEY: 0})
    control.on_bridge({VOICE_TRIGGER_KEY: 1})
    assert control.poll(False, now=2)


def test_wake_word_mode_still_takes_a_trigger():
    control = VoiceControl(True, trigger_window=8)
    assert control.poll(False, now=0) and not control.triggered
    control.trigger()
    assert control.poll(False, now=1) and control.triggered


def test_triggered_utterance_needs_no_wake_word(rb):
    config, ctx = config_for("it", rb), assistant.SessionContext()
    assert assistant.handle_utterance("dimmi la quota", ctx, config) == ""
    reply = assistant.handle_utterance("dimmi la quota", ctx, config, require_wake_word=False)
    assert reply and ctx.Context["last_parameter"] == "GPSAltitudeMSL"
    assert ctx.Context["last_target"] == ""  # no display named: every display


def test_triggered_utterance_keeps_a_wake_word(rb):
    config, ctx = config_for("it", rb), assistant.SessionContext()
    reply = assistant.handle_utterance("secondario dimmi la quota", ctx, config, require_wake_word=False)
    assert reply and ctx.Context["last_target"] == "secondario"


class FakeBridgeWS:
    """One WebSocket connection at a time, on a local port; sends what the test queues."""

    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.conn = None
        self.request = b""
        self.accepted = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        conn, _ = self.listener.accept()
        while b"\r\n\r\n" not in self.request:
            self.request += conn.recv(4096)
        key = [line.split(b":", 1)[1].strip() for line in self.request.split(b"\r\n")
               if line.lower().startswith(b"sec-websocket-key")][0]
        accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        self.conn = conn
        self.accepted.set()

    def send(self, opcode, payload: bytes):
        if len(payload) < 126:
            header = bytes([0x80 | opcode, len(payload)])
        else:
            header = bytes([0x80 | opcode, 126]) + struct.pack(">H", len(payload))
        self.conn.sendall(header + payload)

    def read_frame(self):
        b0, b1 = self.conn.recv(2)
        mask = self.conn.recv(4)
        payload = self.conn.recv(b1 & 0x7F)
        return b0 & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    def close(self):
        if self.conn:
            self.conn.close()
        self.listener.close()


def test_websocket_reads_messages_and_answers_ping():
    server = FakeBridgeWS()
    try:
        ws = MiniWebSocket(f"ws://127.0.0.1:{server.port}/bridge/float/ws")
        assert server.accepted.wait(5)
        assert b"GET /bridge/float/ws HTTP/1.1" in server.request
        server.send(0x9, b"hi")
        big = json.dumps({f"CHECKLIST_0_{i}": i for i in range(20)}).encode()  # over 125 bytes
        server.send(0x1, big)
        assert json.loads(ws.recv()) == json.loads(big)
        assert server.read_frame() == (0xA, b"hi")
        server.send(0x1, b'{"voice_trigger":1}')
        assert ws.recv() == '{"voice_trigger":1}'
        server.send(0x8, b"")
        with pytest.raises(bridge.WebSocketClosed):
            ws.recv()
    finally:
        server.close()


def test_bridge_float_delivers_get_then_websocket(monkeypatch):
    server = FakeBridgeWS()
    received, done = [], threading.Event()

    def on_update(data):
        received.append(data)
        if len(received) == 2:
            done.set()

    client = BridgeFloat(f"http://127.0.0.1:{server.port}", on_update)
    # The socket says nothing on connect: the client reads the map with a GET first
    monkeypatch.setattr(client, "get", lambda: {VOICE_WAKEWORD_KEY: 1})
    try:
        client.start()
        assert server.accepted.wait(5)
        server.send(0x1, json.dumps({VOICE_WAKEWORD_KEY: 0}).encode())
        assert done.wait(5)
        assert received == [{VOICE_WAKEWORD_KEY: 1}, {VOICE_WAKEWORD_KEY: 0}]
    finally:
        client.stop()
        server.close()
