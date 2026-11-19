"""
RB-01 addons bridge (bridgeFloat): the voice settings the RB-01 can read and change.

The RB-01 daemon (roastbeef main/bridge.go) keeps a map of float values:
- POST /bridge/float with {"key": value, ...} stores the values;
- GET /bridge/float returns the whole map;
- /bridge/float/ws sends the whole map to every client, but only after a value changed:
  it says nothing on connect, so the map is read with a GET first.

Voice owns two keys:
- VOICE_WAKEWORD_KEY: 1 = listen all the time and answer only after a wake word,
  0 = listen only after a trigger. rbvoice publishes the case file default at startup.
- VOICE_TRIGGER_KEY: 1 = listen now, without a wake word (push to talk). rbvoice writes 0 back
  when the listening window closes, so the value also tells the HMI whether voice is listening.

The WebSocket client uses the standard library only, so the venv needs no new package. It reads the
unmasked frames the Go server (golang.org/x/net/websocket) writes, and answers pings.
"""
import base64
import json
import os
import queue
import socket
import ssl
import struct
import threading
import time
import traceback
from urllib.parse import urlparse

import requests

VOICE_WAKEWORD_KEY = "voice_wakeword"
VOICE_TRIGGER_KEY = "voice_trigger"

BRIDGE_PATH = "/bridge/float"
BRIDGE_WS_PATH = "/bridge/float/ws"
RECONNECT_SECONDS = 2.0


def flag(value) -> bool:
    """A bridge float as a switch: 1 is on, 0 is off."""
    try:
        return float(value) >= 0.5
    except (TypeError, ValueError):
        return False


class WebSocketClosed(Exception):
    pass


class MiniWebSocket:
    """The client side of RFC 6455, enough to receive text frames."""

    def __init__(self, url: str, timeout: float = 10.0):
        u = urlparse(url)
        secure = u.scheme in ("wss", "https")
        port = u.port or (443 if secure else 80)
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        sock = socket.create_connection((u.hostname, port), timeout=timeout)
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
        self.sock = sock
        self.buffer = b""
        key = base64.b64encode(os.urandom(16)).decode()
        origin = f"{'https' if secure else 'http'}://{u.netloc}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {u.netloc}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Origin: {origin}\r\n"
            "\r\n"
        )
        sock.sendall(request.encode())
        while b"\r\n\r\n" not in self.buffer:
            self._fill()
        head, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0]
        if b" 101" not in status:
            sock.close()
            raise ConnectionError(f"WebSocket handshake refused: {status.decode(errors='replace')}")
        # The server only writes after a change: wait for it as long as it takes
        sock.settimeout(None)

    def _fill(self):
        chunk = self.sock.recv(4096)
        if not chunk:
            raise WebSocketClosed()
        self.buffer += chunk

    def _read(self, n: int) -> bytes:
        while len(self.buffer) < n:
            self._fill()
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def _send(self, opcode: int, payload: bytes = b""):
        # A client masks every frame it sends
        mask = os.urandom(4)
        header = bytes([0x80 | opcode])
        if len(payload) < 126:
            header += bytes([0x80 | len(payload)])
        elif len(payload) < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", len(payload))
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", len(payload))
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def recv(self) -> str:
        """The next text or binary message, as text."""
        message = b""
        while True:
            b0, b1 = self._read(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            payload = self._read(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise WebSocketClosed()
            if opcode == 0x9:
                self._send(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            message += payload
            if fin:
                return message.decode("utf-8", errors="replace")

    def close(self):
        try:
            self._send(0x8)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class BridgeFloat:
    """Reads the bridge map from the WebSocket, and writes values without blocking the caller."""

    def __init__(self, base_url: str, on_update):
        self.base_url = base_url.rstrip("/")
        self.on_update = on_update  # called with the whole map, from the bridge thread
        self.posts = queue.Queue(maxsize=100)
        self.connected = False
        self._stop = threading.Event()
        self._ws = None

    def ws_url(self) -> str:
        u = urlparse(self.base_url)
        scheme = "wss" if u.scheme == "https" else "ws"
        return f"{scheme}://{u.netloc}{BRIDGE_WS_PATH}"

    def get(self) -> dict:
        resp = requests.get(self.base_url + BRIDGE_PATH, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def post_now(self, values: dict):
        resp = requests.post(self.base_url + BRIDGE_PATH, data=json.dumps(values), timeout=10.0)
        resp.raise_for_status()

    def post(self, values: dict):
        """Queues a write: the audio loop must not wait for the server."""
        try:
            self.posts.put_nowait(dict(values))
        except queue.Full:
            print(f"Bridge: write queue full, dropped {values}")

    def start(self):
        threading.Thread(target=self._post_worker, daemon=True, name="bridge-post").start()
        threading.Thread(target=self._ws_worker, daemon=True, name="bridge-ws").start()

    def stop(self):
        self._stop.set()
        if self._ws:
            self._ws.close()

    def _post_worker(self):
        while not self._stop.is_set():
            values = self.posts.get()
            try:
                self.post_now(values)
            except Exception as e:
                print(f"Bridge: POST {values} failed: {e}")

    def _deliver(self, data):
        if isinstance(data, dict):
            try:
                self.on_update(data)
            except Exception:
                print(traceback.format_exc())

    def _ws_worker(self):
        while not self._stop.is_set():
            try:
                self._ws = MiniWebSocket(self.ws_url())
                self.connected = True
                print(f"Bridge: connected to {self.ws_url()}")
                # The socket says nothing on connect, and a value may have changed while it was down
                self._deliver(self.get())
                while not self._stop.is_set():
                    message = self._ws.recv()
                    try:
                        self._deliver(json.loads(message))
                    except ValueError:
                        print(f"Bridge: not JSON: {message[:80]}")
            except Exception as e:
                if not self._stop.is_set():
                    print(f"Bridge: {self.ws_url()} unavailable ({e}), retrying in {RECONNECT_SECONDS:.0f} s")
            finally:
                self.connected = False
                if self._ws:
                    self._ws.close()
                    self._ws = None
            self._stop.wait(RECONNECT_SECONDS)


class VoiceControl:
    """
    How voice listens: with a wake word, after a trigger, or both.

    - wake_word_enabled: audio is heard all the time, and an utterance is answered only when it
      opens with a wake word. When off, audio is ignored unless a trigger opened a window.
    - A trigger (VOICE_TRIGGER_KEY going from 0 to 1) opens a window of trigger_window seconds for
      the pilot to start speaking. The utterance heard in it needs no wake word. A window that saw
      speech stays open until the utterance ends.
    """

    def __init__(self, wake_word_enabled: bool, trigger_window: float = 8.0, bridge: "BridgeFloat" = None):
        self.wake_word_enabled = bool(wake_word_enabled)
        self.trigger_window = trigger_window
        self.bridge = bridge
        self.triggered = False
        self.window_until = 0.0
        self._trigger = threading.Event()
        self._last_trigger_value = None

    def on_bridge(self, data: dict):
        """Called with the whole bridge map, on connect and on every change of any key."""
        if VOICE_WAKEWORD_KEY in data:
            enabled = flag(data[VOICE_WAKEWORD_KEY])
            if enabled != self.wake_word_enabled:
                print(f"Bridge: wake word {'enabled' if enabled else 'disabled'}")
            self.wake_word_enabled = enabled
        if VOICE_TRIGGER_KEY in data:
            pressed = flag(data[VOICE_TRIGGER_KEY])
            # The map comes again on every change of any key: only a 0 -> 1 edge is a press
            if pressed and self._last_trigger_value is not True:
                print("Bridge: trigger")
                self._trigger.set()
            self._last_trigger_value = pressed

    def publish(self):
        """Startup: the case file default for the wake word, and no trigger pending."""
        if self.bridge is None:
            return
        values = {VOICE_WAKEWORD_KEY: 1 if self.wake_word_enabled else 0, VOICE_TRIGGER_KEY: 0}
        try:
            self.bridge.post_now(values)
            print(f"Bridge: published {values}")
        except Exception as e:
            print(f"Bridge: cannot publish {values}: {e}")

    def trigger(self):
        """A trigger from inside the process, as if the bridge had sent it."""
        self._trigger.set()

    def poll(self, speaking: bool, now: float = None) -> bool:
        """Called for every audio chunk: True when the chunk may be heard."""
        now = time.monotonic() if now is None else now
        if self._trigger.is_set():
            self._trigger.clear()
            self.triggered = True
            self.window_until = now + self.trigger_window
            print(f"Listening for {self.trigger_window:.0f} s, no wake word needed")
        if self.triggered and not speaking and now > self.window_until:
            print("Trigger window closed without an utterance")
            self.end_trigger()
        return self.wake_word_enabled or self.triggered

    def end_trigger(self):
        """Closes the trigger window, and tells the HMI it is closed."""
        if not self.triggered:
            return
        self.triggered = False
        if self.bridge is not None:
            self.bridge.post({VOICE_TRIGGER_KEY: 0})
