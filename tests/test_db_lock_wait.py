"""Another connection holding the database's write lock no longer stalls the whole gateway
(0.7.46 audit, row 9). Before, every request wrote something on the event loop (the usage
counters, a sign-in's use, a waiting proposal's hold), so a backup or an admin command holding the
lock for four seconds stalled every page, every assistant call and every background job for those
four seconds, measured at a 4.5 s longest gap in the loop. Requests now wait for the lock off the
loop (dbwait.py), so the loop keeps turning, and they all go on once it is free."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
import time

import pytest

from mtg_gateway import dbwait

from .conftest import Stack
from .test_decks_and_proxy import Browser, call, linked_user, structured

HOLD = textwrap.dedent(
    """
    import sqlite3, sys, time
    c = sqlite3.connect(sys.argv[1], isolation_level=None)
    c.execute("BEGIN IMMEDIATE")
    print("held", flush=True)
    time.sleep(float(sys.argv[2]))
    c.execute("COMMIT")
    """
)


def _hold(path: str, seconds: float) -> subprocess.Popen[str]:
    """Another process takes the database's write lock and keeps it ``seconds``."""
    p = subprocess.Popen([sys.executable, "-c", HOLD, path, str(seconds)], stdout=subprocess.PIPE, text=True)
    assert p.stdout is not None and p.stdout.readline().strip() == "held"
    return p


class _Ticker:
    """The longest the event loop went without turning, while it ran."""

    def __init__(self) -> None:
        self.longest = 0.0
        self._stop = False
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        last = time.monotonic()
        while not self._stop:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            self.longest = max(self.longest, now - last)
            last = now

    def __enter__(self) -> _Ticker:
        self._task = asyncio.ensure_future(self._run())
        return self

    async def done(self) -> float:
        self._stop = True
        assert self._task is not None
        await self._task
        return self.longest

    def __exit__(self, *exc: object) -> None:
        self._stop = True


async def test_a_lock_held_by_another_process_does_not_stall_the_loop(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    browser = Browser(h)
    await browser.login()
    for path in ("/proposals", "/decks", "/account"):  # warm: first reads, caches
        assert (await browser.http.get(path)).status_code == 200
    holder = _hold(str(h.settings.db_path), 3.0)
    try:
        with _Ticker() as ticker:
            started = time.monotonic()
            replies = await asyncio.gather(
                call(h, token, "whoami", {}),
                call(h, token, "list_my_proposals", {}),
                browser.http.get("/proposals"),
                browser.http.get("/account"),
            )
            took = time.monotonic() - started
            longest = await ticker.done()
    finally:
        holder.wait(timeout=30)
    # the same four requests with no lock held already keep the loop busy for 0.1 to 0.2 s at a
    # time in this test client (drawing the pages), so a loaded machine gets headroom; before the
    # change it stalled the whole 3 s
    assert longest < 1.0, f"the loop stalled {longest:.2f} s while another process held the lock"
    # every request went on once the lock was free, and none was turned away
    assert took >= 2.0, took
    assert all(structured(r).get("ok") is not False and not r.get("isError") for r in replies[:2]), replies[
        :2
    ]
    assert [r.status_code for r in replies[2:]] == [200, 200]


async def test_a_lock_held_past_the_limit_is_answered_busy(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rather than hold a request forever, one that waited the limit is told to try again. The
    Proposals page writes (it holds a change whose first step failed), so it waits for the lock
    even though a page only reading would go ahead (test_db_lock_reads.py)."""
    h = stack.h
    browser = Browser(h)
    await browser.login()
    wait = next(m for m in h.app.user_middleware if m.cls is dbwait.DatabaseWaitMiddleware)
    monkeypatch.setitem(wait.kwargs, "limit", 0.5)
    h.app.middleware_stack = None  # built again with the shorter limit
    holder = _hold(str(h.settings.db_path), 2.0)
    try:
        page = await browser.http.get("/proposals", headers={"accept": "text/html"})
        api = await browser.http.get("/proposals", headers={"accept": "application/json"})
    finally:
        holder.wait(timeout=30)
    assert page.status_code == 503 and "The gateway is busy" in page.text, page.text
    assert page.headers["retry-after"] == "5"
    assert api.status_code == 503 and api.json()["error"] == "busy", api.text
    assert (await browser.http.get("/proposals")).status_code == 200  # and served once it is free


async def test_static_files_and_the_health_check_never_wait(stack: Stack) -> None:
    h = stack.h
    http = Browser(h).http
    holder = _hold(str(h.settings.db_path), 2.0)
    try:
        started = time.monotonic()
        assert (await http.get("/healthz")).status_code in (200, 503)
        assert (await http.get("/static/bell.js")).status_code == 200
        assert time.monotonic() - started < 1.0
    finally:
        holder.wait(timeout=30)


def test_the_check_before_each_request_is_cheap(stack: Stack) -> None:
    """Asked before nearly every request: well under a millisecond when nothing holds the lock."""
    db = stack.h.db
    assert db.write_free()
    started = time.perf_counter()
    for _ in range(200):
        assert db.write_free()
    each = (time.perf_counter() - started) / 200
    assert each < 0.002, f"{each * 1000:.3f} ms per check"


def test_the_check_says_busy_without_waiting_and_keeps_the_writes_wait(stack: Stack) -> None:
    db = stack.h.db
    holder = _hold(str(db.path), 1.5)
    try:
        started = time.monotonic()
        assert db.write_free() is False
        assert time.monotonic() - started < 0.2
        # a write still waits its full busy timeout (nothing Archidekt took is ever left unrecorded)
        assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert db.wait_write_free(5.0) is True
    finally:
        holder.wait(timeout=30)
    assert db.write_free() is True


def test_a_read_never_waits_for_another_process_s_write_lock(stack: Stack) -> None:
    """In WAL mode a read does not wait for a writer: while another process holds the write lock,
    the gateway's reads answer at once. So it is only a write, taken after the check before a
    request (dbwait.py), that can still wait for the lock on the loop."""
    db = stack.h.db
    holder = _hold(str(db.path), 1.5)
    try:
        started = time.monotonic()
        for _ in range(20):
            db.get_user("user-1")
            db.list_proposals("user-1", 10)
        assert time.monotonic() - started < 0.15
        assert db.write_free() is False  # the write lock really is held meanwhile
    finally:
        holder.wait(timeout=30)
