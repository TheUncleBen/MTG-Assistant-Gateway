"""The service worker's "You're offline" page in headless Chromium (T-125): it answers a page load
that cannot reach the gateway (the browser offline, or the gateway's connections reset), and once
the connection is back one reload, or its Try again link, loads the page again, also when the
browser stopped the idle worker meanwhile. The gateway is reached through a small relay the test
can cut, as a dropped network or a restarting gateway would. Skipped without a Chromium build."""

from __future__ import annotations

import os
import signal
import socket
import threading
from pathlib import Path

import pytest

from .test_scan_browser import _chromium_path, _free_port
from .test_suggest_browser import Server

pytest.importorskip("playwright")


class Relay:
    """A TCP relay in front of the gateway. ``cut()`` resets every open connection and each new
    one (the browser sees the network fail); ``restore()`` relays again."""

    def __init__(self, target: int) -> None:
        self.target = target
        self.port = _free_port()
        self.up = True
        self.live: set[socket.socket] = set()
        self._stop = False
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", self.port))
        self._sock.listen(64)
        self._sock.settimeout(0.1)
        threading.Thread(target=self._loop, daemon=True).start()

    def cut(self) -> None:
        self.up = False
        for c in list(self.live):
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def restore(self) -> None:
        self.up = True

    def close(self) -> None:
        self._stop = True
        self.cut()

    def _loop(self) -> None:
        while not self._stop:
            try:
                c, _ = self._sock.accept()
            except (TimeoutError, OSError):
                continue
            if not self.up:
                c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
                c.close()  # a reset, as from a dropped network or a gateway going down
                continue
            threading.Thread(target=self._pipe, args=(c,), daemon=True).start()
        self._sock.close()

    def _pipe(self, c: socket.socket) -> None:
        try:
            u = socket.create_connection(("127.0.0.1", self.target))
        except OSError:
            c.close()
            return
        self.live.update((c, u))

        def pump(a: socket.socket, b: socket.socket) -> None:
            try:
                while data := a.recv(65536):
                    b.sendall(data)
            except OSError:
                pass
            finally:
                for x in (a, b):
                    try:
                        x.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.live.discard(x)

        threading.Thread(target=pump, args=(c, u), daemon=True).start()
        pump(u, c)


@pytest.fixture
def served(tmp_path: Path):
    s = Server(tmp_path)
    relay = Relay(s.port)
    s.app.state.gateway.settings.allowed_hosts.append(f"127.0.0.1:{relay.port}")
    s.start()
    try:
        yield s, relay, f"http://127.0.0.1:{relay.port}"
    finally:
        relay.close()
        s.stop()


def _offline(page) -> bool:
    return page.title().startswith("You're offline")


STUCK_AFTER = 180  # seconds; the whole test takes a few seconds when it works


def _kill_children() -> None:
    """Kill this process's child processes (the Playwright driver and its browser), so a call
    that never returns raises instead of hanging the test run."""
    me = str(os.getpid())
    for d in Path("/proc").glob("[0-9]*"):
        try:
            ppid = (d / "stat").read_text().rsplit(")", 1)[1].split()[1]
        except (OSError, IndexError):
            continue
        if ppid == me:
            try:
                os.kill(int(d.name), signal.SIGKILL)
            except OSError:
                pass


def test_one_reload_after_the_connection_returns_loads_the_page(served) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    server, relay, base = served
    sid = server.sign_in()
    step = "starting Chromium"
    stuck = threading.Event()

    def give_up() -> None:
        stuck.set()
        _kill_children()

    watchdog = threading.Timer(STUCK_AFTER, give_up)
    watchdog.daemon = True
    watchdog.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            ctx = browser.new_context()
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": base}])
            page = ctx.new_page()
            page.set_default_timeout(15_000)
            step = "first load and the service worker getting ready"
            page.goto(f"{base}/decks")
            # bounded: a worker that never gets ready fails the test instead of hanging it
            ready = (
                "Promise.race([navigator.serviceWorker.ready.then(() => true),"
                " new Promise(r => setTimeout(() => r(false), 15000))])"
            )
            assert page.evaluate(ready), "the service worker never became ready"
            page.goto(f"{base}/decks")  # this load goes through the worker
            assert page.evaluate("!!navigator.serviceWorker.controller")

            step = "the browser offline, then online again"
            ctx.set_offline(True)
            page.goto(f"{base}/history")
            assert _offline(page)
            ctx.set_offline(False)
            page.reload()
            assert page.title().startswith("History"), page.title()

            step = "the gateway cut off, then back"
            relay.cut()
            page.goto(f"{base}/history")
            assert _offline(page)
            relay.restore()
            page.reload()
            assert page.title().startswith("History"), page.title()

            step = "the Try again link"
            relay.cut()
            page.goto(f"{base}/history")
            assert _offline(page)
            relay.restore()
            with page.expect_navigation():
                page.get_by_role("link", name="Try again").click()
            assert page.title().startswith("History"), page.title()

            # The gateway is cut off here rather than the browser set offline: on newer Chromium,
            # Playwright's set_offline never returns once the worker has been stopped.
            step = "the browser stopping the idle worker while offline"
            cdp = ctx.new_cdp_session(page)
            cdp.send("ServiceWorker.enable")
            relay.cut()
            page.goto(f"{base}/decks")
            assert _offline(page)
            cdp.send("ServiceWorker.stopAllWorkers")
            relay.restore()
            page.reload()
            assert page.title().startswith("My decks"), page.title()
            step = "closing Chromium"
            browser.close()
    except Exception:
        if stuck.is_set():
            pytest.fail(f"stuck for {STUCK_AFTER} s during: {step}")
        raise
    finally:
        watchdog.cancel()
    assert not stuck.is_set(), f"stuck for {STUCK_AFTER} s during: {step}"
