#!/usr/bin/env python3
"""A stand-in gateway for the app's notification tests on an emulator (push-test.sh).

It serves the gateway's notification stream (/app/notifications/stream) over https, as the real
gateway does: one JSON object per line, an ``open`` line first, a ``keepalive`` every few seconds,
then the messages the test sends. The test drives it on the same port:

  POST /control/send     {"id", "title", "message", "sequence_id"?}  a message to every connection
  POST /control/delete   {"sequence_id"}                             withdraw a deck's message
  POST /control/refuse   {"status", "error"}                         refuse from now on, end streams
  POST /control/drop                                                 end streams (the app reconnects)
  POST /control/press    {"name"}                     the page clicks its button for that app method
  GET  /control/state    {"connections", "connects", "last_since", "pages": {path: requests}, "seen"}

"seen" is what the page last reported: whether the app's bridge was there and which buttons showed.

Only the key in PUSH_TEST_KEY is accepted. Every other path answers a small page with the account
menu's App buttons and the gateway's own feedback.js (from PUSH_TEST_STATIC, the gateway's static
folder), so the test can press each one inside the app. Standard library only.
"""

from __future__ import annotations

import json
import os
import queue
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = os.environ.get("PUSH_TEST_KEY", "")
KEEPALIVE = float(os.environ.get("PUSH_TEST_KEEPALIVE", "5"))
STATIC = os.environ.get("PUSH_TEST_STATIC", "")
# The App section of the gateway's account menu (theme.py), as the app's buttons
PAGE = (
    "<!doctype html><meta name='viewport' content='width=device-width'><title>Test gateway</title>"
    "<style>button{display:block;font-size:20px;margin:16px;padding:12px}[hidden]{display:none}</style>"
    "<p>Test gateway.</p>"
    "<button type='button' data-native='openCamera'>Scan with the app's camera</button>"
    "<button type='button' data-native='reload'>Reload</button>"
    "<button type='button' data-native='openInBrowser'>Open in browser</button>"
    "<button type='button' data-native='changeGateway'>Change gateway</button>"
    "<script src='/static/feedback.js'></script>"
    # Reports what it shows, and clicks a button when the test asks (POST /control/press)
    "<script>(function(){"
    "function shown(){return Array.prototype.filter.call(document.querySelectorAll('[data-native]'),"
    "function(b){return !b.hidden;}).map(function(b){return b.getAttribute('data-native');}).join(',');}"
    "setTimeout(function(){fetch('/control/seen?bridge='+(window.MtgNative?1:0)+'&shown='+shown());},500);"
    "setInterval(function(){fetch('/control/press').then(function(r){return r.json();}).then(function(j){"
    "var b=j.name&&document.querySelector('[data-native=\"'+j.name+'\"]');if(b)b.click();})"
    ".catch(function(){});},500);"
    "})();</script>"
).encode()

lock = threading.Lock()
streams: list[queue.Queue] = []
state = {"connects": 0, "last_since": None, "refuse": None, "press": None, "seen": None}
pages: dict[str, int] = {}
history: list[dict] = []  # sent messages, for since=


def _publish(event: dict) -> None:
    with lock:
        if event.get("event") == "message":
            history.append(event)
        for q in streams:
            q.put(event)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # a stream ends when the connection closes, as the app expects

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("gateway: " + fmt % args + "\n")

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        if path == "/control/state":
            with lock:
                self._json(
                    200,
                    {
                        "connections": len(streams),
                        "connects": state["connects"],
                        "last_since": state["last_since"],
                        "pages": dict(pages),
                        "seen": state["seen"],
                    },
                )
            return
        if path == "/control/seen":
            with lock:
                state["seen"] = query
            self._json(200, {"ok": True})
            return
        if path == "/control/press":
            with lock:
                name, state["press"] = state["press"], None
            self._json(200, {"name": name})
            return
        if path == "/static/feedback.js" and STATIC:
            with open(os.path.join(STATIC, "feedback.js"), "rb") as f:
                raw = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if path != "/app/notifications/stream":
            with lock:
                pages[path] = pages.get(path, 0) + 1
            raw = PAGE
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            self._json(401, {"ok": False, "error": "unknown_key", "message": "unknown key"})
            return
        with lock:
            refuse = state["refuse"]
        if refuse:
            self._json(refuse["status"], {"ok": False, "error": refuse["error"], "message": "refused"})
            return
        since = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("since")
        q: queue.Queue = queue.Queue()
        with lock:
            state["connects"] += 1
            state["last_since"] = since
            missed = []
            if since:
                ids = [m["id"] for m in history]
                missed = history[ids.index(since) + 1 :] if since in ids else []
            streams.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self._line({"id": "open0001", "time": int(time.time()), "event": "open"})
            for m in missed:
                self._line(m)
            while True:
                try:
                    ev = q.get(timeout=KEEPALIVE)
                except queue.Empty:
                    ev = {"event": "keepalive", "time": int(time.time())}
                if ev.get("event") == "end":
                    return
                self._line(ev)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
            return
        finally:
            with lock:
                if q in streams:
                    streams.remove(q)

    def _line(self, ev: dict) -> None:
        self.wfile.write((json.dumps(ev) + "\n").encode())
        self.wfile.flush()

    def do_POST(self) -> None:
        body = self._body()
        if self.path == "/control/send":
            ev = {
                "event": "message",
                "time": int(time.time()),
                "id": body["id"],
                "title": body.get("title", ""),
                "message": body.get("message", ""),
                "click": body.get("click", ""),
            }
            if body.get("sequence_id"):
                ev["sequence_id"] = body["sequence_id"]
            _publish(ev)
        elif self.path == "/control/delete":
            _publish(
                {
                    "event": "message_delete",
                    "time": int(time.time()),
                    "id": "del" + str(int(time.time() * 1000)),
                    "sequence_id": body["sequence_id"],
                }
            )
        elif self.path == "/control/press":
            with lock:
                state["press"] = body.get("name")
        elif self.path == "/control/drop":
            _publish({"event": "end"})
        elif self.path == "/control/refuse":
            with lock:
                state["refuse"] = body
            _publish({"event": "end"})
        else:
            self._json(404, {"ok": False})
            return
        self._json(200, {"ok": True})


def main() -> None:
    cert, key, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
    if not KEY:
        sys.exit("PUSH_TEST_KEY is not set")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    print(f"test gateway on https://localhost:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
