"""
Shared fixtures: the case files, and a fake RB-01 the handlers can talk to.

The fake serves tests/fixtures/rb01.json, which holds trimmed responses captured from a real
RB-01 (roastbeef test/display/fixtures/rb01-device.json) and a few airfields around Perugia.
It records every request and keeps the state the handlers write: radio, timers, autopilot plan.
"""
import copy
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import assistant  # noqa: E402

REFERENCE = "it"
LANGUAGES = sorted(p.name.split(".")[1] for p in ROOT.glob("rbvoice-cases.*.json"))
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "rb01.json").read_text(encoding="utf-8"))
RULES = ROOT / "rbvoice-aircraft-rules.json"

# The plan the autopilot holds before a test changes it: from here (passed) to Perugia (target)
PLAN = [
    {"Lat": 43.104656, "Lon": 12.2511215, "Ele": 546, "Status": 2, "Cmt": "Current Position"},
    {"Lat": 43.095901, "Lon": 12.5132, "Ele": 212, "Status": 1, "Cmt": "Perugia San Francesco d'Assisi – Umbria International Airport, PG10, LIRZ"},
]


def load_cases(lang):
    with open(ROOT / f"rbvoice-cases.{lang}.json", encoding="utf-8") as f:
        return json.load(f)


class FakeRB:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.reset()

    def reset(self):
        self.data = copy.deepcopy(FIXTURE)
        self.data["/autopilot"] = copy.deepcopy(PLAN)
        self.requests = []

    def calls(self, method, path):
        """Bodies of the requests made with method to path, in order."""
        return [body for m, p, body in self.requests if (m, p) == (method, path)]

    def handler(self):
        rb = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body=None):
                payload = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def handle_any(self):
                # Go's ServeMux answers repeated slashes ("//timers") with a 307 to the clean path,
                # which keeps the method and the body. Several handlers build such URLs.
                if "//" in self.path:
                    self.send_response(307)
                    self.send_header("Location", re.sub(r"/+", "/", self.path))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                path = self.path.split("?")[0]
                rb.requests.append((self.command, path, body))

                if self.command == "GET" and path in rb.data:
                    return self.reply(200, rb.data[path])
                if (self.command, path) == ("POST", "/radio/0"):
                    rb.data["/radio"][0] = body
                    return self.reply(200, body)
                if (self.command, path) == ("POST", "/timers"):
                    for index, timer in body.items():
                        rb.data["/timers"][int(index)] = timer
                    return self.reply(200, rb.data["/timers"])
                if (self.command, path) == ("POST", "/autopilot"):
                    rb.data["/autopilot"] = body
                    return self.reply(200, [])
                if (self.command, path) in (("PUT", "/autopilot"), ("DELETE", "/autopilot"), ("PUT", "/remote/voice")):
                    return self.reply(200, {})
                self.reply(404)

            do_GET = do_POST = do_PUT = do_DELETE = handle_any

        return Handler


@pytest.fixture(scope="session")
def fake_rb_server():
    rb = FakeRB()
    yield rb
    rb.server.shutdown()


@pytest.fixture
def rb(fake_rb_server, monkeypatch):
    """The fake RB-01, reset for each test; ValueHelper reads the repo's aircraft rules."""
    fake_rb_server.reset()
    # helper_EMS_EngineStatus hard-codes /boot/firmware/rb/rbvoice-aircraft-rules.json
    monkeypatch.setattr(assistant.RuleEvaluator, "from_file",
                        classmethod(lambda cls, path: cls(json.loads(RULES.read_text()))))
    return fake_rb_server


def config_for(lang, rb):
    config = load_cases(lang)
    config["base_url"] = rb.url
    return config
