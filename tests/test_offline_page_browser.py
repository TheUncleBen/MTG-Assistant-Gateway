"""The service worker's "You're offline" page in headless Chromium: it answers a page load
that cannot reach the gateway (the browser offline, or the gateway's connections reset), and once
the connection is back one reload, or its Try again link, loads the page again, also when the
browser stopped the idle worker meanwhile. The worker keeps trying a failed load for about
5 s before it shows that page, and the page loads the address again by itself when the browser
is back online or the gateway answers its check every few seconds. The gateway is reached through
a small relay the test can cut, as a dropped network or a restarting gateway would. Skipped
without a Chromium build."""

from __future__ import annotations

import os
import signal
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from .test_scan_browser import _chromium_path, _free_port, _held_socket
from .test_suggest_browser import Server

pytest.importorskip("playwright")


class Relay:
    """A TCP relay in front of the gateway. ``cut()`` resets every open connection and each new
    one (the browser sees the network fail); ``restore()`` relays again. ``refuse`` set to a
    request line's start (b"GET /history ") resets the connection of each such request only, so
    that one page keeps failing at the network level while the rest of the gateway answers."""

    def __init__(self, target: int) -> None:
        self.target = target
        self.port = _free_port()
        self.up = True
        self.refuse: bytes | None = None
        self.live: set[socket.socket] = set()
        self._stop = False
        self._sock = _held_socket(self.port)  # bound already, so no other test can take the port
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
                    if a is c and self.refuse and self.refuse in data:
                        a.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
                        break  # closed with a reset below, as a dropped connection would be
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


class Steps:
    """The step a test is on, for the watchdog's message."""

    def __init__(self) -> None:
        self.now = "starting Chromium"


def _drive(served, run: Callable[[Any, Any, str, Relay, Steps], None]) -> None:
    """Start Chromium with a signed-in context whose page is controlled by the service worker, then
    ``run(page, ctx, base, relay, steps)``. A watchdog kills the browser after STUCK_AFTER seconds,
    so a call that never returns fails the test, naming its step, instead of hanging the run."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    server, relay, base = served
    sid = server.sign_in()
    steps = Steps()
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
            steps.now = "first load and the service worker getting ready"
            page.goto(f"{base}/decks")
            # bounded: a worker that never gets ready fails the test instead of hanging it
            ready = (
                "Promise.race([navigator.serviceWorker.ready.then(() => true),"
                " new Promise(r => setTimeout(() => r(false), 15000))])"
            )
            assert page.evaluate(ready), "the service worker never became ready"
            page.goto(f"{base}/decks")  # this load goes through the worker
            assert page.evaluate("!!navigator.serviceWorker.controller")
            run(page, ctx, base, relay, steps)
            steps.now = "closing Chromium"
            browser.close()
    except Exception:
        if stuck.is_set():
            pytest.fail(f"stuck for {STUCK_AFTER} s during: {steps.now}")
        raise
    finally:
        watchdog.cancel()
    assert not stuck.is_set(), f"stuck for {STUCK_AFTER} s during: {steps.now}"


def _wait_for_title(page, start: str, timeout_ms: int) -> None:
    """Wait (bounded) until a page with this title has loaded; survives the page's own reloads."""
    page.wait_for_function(
        "t => document.readyState !== 'loading' && document.title.startsWith(t)",
        arg=start,
        timeout=timeout_ms,
    )


def test_one_reload_after_the_connection_returns_loads_the_page(served) -> None:
    def run(page, ctx, base: str, relay: Relay, steps: Steps) -> None:
        # The offline page loads the address again by itself once the connection is back,
        # so each reload here follows the cut at once, well before its first check.
        steps.now = "the gateway cut off, then back"
        relay.cut()
        page.goto(f"{base}/history")
        assert _offline(page)
        relay.restore()
        page.reload()
        assert page.title().startswith("History"), page.title()

        steps.now = "the Try again link"
        relay.cut()
        page.goto(f"{base}/history")
        assert _offline(page)
        relay.restore()
        with page.expect_navigation():
            page.get_by_role("link", name="Try again").click()
        assert page.title().startswith("History"), page.title()

        # The gateway is cut off here rather than the browser set offline: on newer Chromium,
        # Playwright's set_offline never returns once the worker has been stopped.
        steps.now = "the browser stopping the idle worker while offline"
        cdp = ctx.new_cdp_session(page)
        cdp.send("ServiceWorker.enable")
        relay.cut()
        page.goto(f"{base}/decks")
        assert _offline(page)
        cdp.send("ServiceWorker.stopAllWorkers")
        relay.restore()
        page.reload()
        assert page.title().startswith("My decks"), page.title()

    _drive(served, run)


CHECK_EVERY = 5.0  # seconds between the offline page's checks (static/offline.js)


def test_the_offline_page_reloads_itself_once_the_connection_is_back(served) -> None:
    """Back online, the page loads again at once (the browser's online event, well before
    the first check); with the gateway cut off, it asks /healthz every few seconds, never in a
    tight loop, and loads the page again once the gateway answers."""

    def run(page, ctx, base: str, relay: Relay, steps: Steps) -> None:
        steps.now = "the browser offline, then online again"
        ctx.set_offline(True)
        page.goto(f"{base}/history")
        assert _offline(page)
        ctx.set_offline(False)
        _wait_for_title(page, "History", int((CHECK_EVERY - 1) * 1000))

        steps.now = "the gateway checked every few seconds while cut off"
        checks: list[float] = []
        page.on("request", lambda r: checks.append(time.monotonic()) if r.url.endswith("/healthz") else None)
        relay.cut()
        page.goto(f"{base}/history")
        assert _offline(page)
        shown = time.monotonic()
        page.wait_for_timeout(2 * CHECK_EVERY * 1000 + 1500)
        assert _offline(page), page.title()  # nothing answered: still the offline page
        assert 2 <= len(checks) <= 3, [round(t - shown, 1) for t in checks]
        assert all(b - a >= CHECK_EVERY - 1 for a, b in zip(checks, checks[1:], strict=False)), checks

        steps.now = "the gateway back: the page loads by itself"
        relay.restore()
        _wait_for_title(page, "History", int((CHECK_EVERY + 6) * 1000))
        after = len(checks)
        page.wait_for_timeout(CHECK_EVERY * 1000 + 1000)
        assert len(checks) == after, "the checks went on after the page had loaded"

    _drive(served, run)


def test_the_worker_keeps_trying_a_failed_load_for_about_five_seconds(served) -> None:
    """A gateway restart or a short drop (here 2.5 s) never shows the offline page; a
    longer one shows it after about 5 s of tries."""

    def run(page, ctx, base: str, relay: Relay, steps: Steps) -> None:
        steps.now = "a 2.5 s drop during a page load"
        relay.cut()
        back = threading.Timer(2.5, relay.restore)
        back.start()
        try:
            page.goto(f"{base}/history")
        finally:
            back.cancel()
            relay.restore()
        assert page.title().startswith("History"), page.title()

        steps.now = "a longer cut shows the offline page after the tries"
        relay.cut()
        started = time.monotonic()
        page.goto(f"{base}/history")
        took = time.monotonic() - started
        assert _offline(page)
        assert 4.0 <= took <= 12.0, took
        relay.restore()

    _drive(served, run)


def test_a_browser_that_knows_it_is_offline_gets_the_offline_page_at_once(served) -> None:
    """When the browser already says it is offline, the worker
    shows the offline page at once instead of trying for about 5 s; it still reloads by itself
    once the browser is back online. With the browser online, a failed load is still tried for
    about 5 s (the test above)."""

    def run(page, ctx, base: str, relay: Relay, steps: Steps) -> None:
        steps.now = "the browser offline"
        ctx.set_offline(True)
        started = time.monotonic()
        page.goto(f"{base}/history")
        took = time.monotonic() - started
        assert _offline(page)
        assert took < 2.5, took  # it used to take about 5 s of tries
        steps.now = "back online: the page loads by itself"
        ctx.set_offline(False)
        _wait_for_title(page, "History", int((CHECK_EVERY - 1) * 1000))

    _drive(served, run)


def test_a_page_that_keeps_failing_is_reloaded_three_times_at_most(served) -> None:
    """A page that keeps failing at the network level while the gateway's /healthz
    answers is loaded again by itself 3 times at most, then the offline page stays, with its Try
    again link, which still works and starts the tries afresh."""

    def run(page, ctx, base: str, relay: Relay, steps: Steps) -> None:
        loads: list[float] = []
        page.on("load", lambda: loads.append(time.monotonic()))
        steps.now = "a page that keeps failing while /healthz answers"
        relay.refuse = b"GET /history "
        page.goto(f"{base}/history")
        assert _offline(page)
        # each try: about 5 s of the worker's tries, then the next check 5 s later finds the
        # gateway up and loads the page again; 3 tries take about 30 s
        deadline = time.monotonic() + 4 * 2 * (CHECK_EVERY + 1) + 10
        while len(loads) < 4 and time.monotonic() < deadline:
            page.wait_for_timeout(500)
        assert len(loads) == 4, len(loads)  # the first offline page and 3 reloads
        page.wait_for_function("() => document.readyState === 'complete'")
        page.wait_for_timeout(2 * (CHECK_EVERY + 1) * 1000 + 2000)  # long enough for another try
        assert len(loads) == 4, len(loads)
        assert _offline(page), page.title()
        steps.now = "Try again once the page loads"
        relay.refuse = None
        with page.expect_navigation():
            page.get_by_role("link", name="Try again").click()
        assert page.title().startswith("History"), page.title()

    _drive(served, run)
