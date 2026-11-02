"""Background jobs no longer stall the gateway while another process holds the database's write
lock (0.7.46 audit, row 9, the jobs left after dbwait.py).

A job's database work after an Archidekt answer (the deck sync's re-check, an alert round, a
queued backup delete) waited for the lock on the event loop, so every page and assistant call
stalled with it. A job's work run in a worker thread (the hourly purge, the nightly copy) waited
while holding the connection's in-process lock, so the next database read on the loop stalled
behind it. Now a job waits off the loop where it already waits (after an Archidekt answer, a
Scryfall answer, a notification send, its own sleep), and a worker thread waits for the write
lock without holding the in-process one."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
import time

import httpx
import pytest

from mtg_gateway import sync as sync_mod

from .conftest import AUTO, Stack, StackOptions
from .test_db_lock_wait import _hold as _hold_lock
from .test_db_lock_wait import _Ticker
from .test_deck_sync import _edit_elsewhere, _later, _propose, _sync
from .test_decks_and_proxy import call, linked_user, structured

SUB = "user-1"
# The longest the loop may go without turning while a job meets a held lock. Drawing a page in this
# test client alone takes 0.1 to 0.2 s, so these tests draw none while they measure.
GAP = 0.15
HOLD = 3.0
# A loop that waited for the lock on the loop would stand still for the whole hold (the gateway did,
# measured at 4.5 s for a 4 s hold); a loop that waits off the loop turns every 10 ms, 300 times in
# the hold. A loaded runner (four workers, browsers) slows the ticks, never to a third of them.
TICKS_IN_HOLD = 100

# The holder, started before the measured window so starting an interpreter (20 to 50 ms, more on
# a loaded runner) never counts as a gap in the loop: it takes the lock when told, says "held",
# keeps it HOLD seconds and lets it go.
HOLD_WHEN_TOLD = textwrap.dedent(
    """
    import sqlite3, sys, time
    c = sqlite3.connect(sys.argv[1], isolation_level=None)
    sys.stdin.readline()
    c.execute("BEGIN IMMEDIATE")
    print("held", flush=True)
    time.sleep(float(sys.argv[2]))
    c.execute("COMMIT")
    """
)


class _Holder:
    """Another process ready to take the database's write lock the moment ``take`` is called."""

    def __init__(self, path: str, seconds: float) -> None:
        self.p = subprocess.Popen(
            [sys.executable, "-c", HOLD_WHEN_TOLD, path, str(seconds)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.taken_at: float | None = None

    def take(self) -> None:
        assert self.p.stdin is not None and self.p.stdout is not None
        self.p.stdin.write("go\n")
        self.p.stdin.flush()
        assert self.p.stdout.readline().strip() == "held"
        self.taken_at = time.monotonic()

    def wait(self) -> None:
        if self.taken_at is None:  # never told: let it go
            assert self.p.stdin is not None
            self.p.stdin.close()
        self.p.wait(timeout=30)


class _TickCounter(_Ticker):
    """The loop's turns, each stamped, so the turns inside the hold can be counted."""

    def __init__(self) -> None:
        super().__init__()
        self.ticks: list[float] = []

    async def _run(self) -> None:
        last = time.monotonic()
        while not self._stop:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            self.longest = max(self.longest, now - last)
            self.ticks.append(now)
            last = now


@pytest.fixture
def stack_options(monkeypatch) -> StackOptions:
    monkeypatch.setattr(sync_mod, "IDLE_WAIT", 0.3)
    monkeypatch.setattr(sync_mod, "IDLE_POLL", 0.01)
    return AUTO


async def test_the_deck_sync_waits_off_the_loop_for_a_lock_taken_while_it_reads(stack: Stack) -> None:
    """Another process takes the lock for 3 s while the sync reads the deck from Archidekt; the
    sync's re-check (which marks the waiting proposal Needs a look) then waits for it off the loop:
    the loop keeps turning through the hold (TICKS_IN_HOLD), where before the fix it stood still
    until the lock was let go."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    pid = await _propose(h, token)
    sync = _sync(stack)
    _edit_elsewhere(ark)
    _later(stack)
    orig = ark.transport.handler
    holder = _Holder(str(h.settings.db_path), HOLD)

    def lock_while_reading(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/decks/42/" and holder.taken_at is None:
            holder.take()
        return orig(req)

    ark.transport.handler = lock_while_reading
    try:
        with _TickCounter() as ticker:
            started = time.monotonic()
            out = await sync.check(SUB)
            ended = time.monotonic()
            await ticker.done()
    finally:
        holder.wait()
    assert holder.taken_at is not None, "the deck was never read"
    assert out.get("flagged") == [pid], out
    # the sync's re-check could only be written once the lock was let go: the check lasted the hold
    assert ended - holder.taken_at >= HOLD - 0.1, (started, holder.taken_at, ended)
    in_hold = [t for t in ticker.ticks if holder.taken_at <= t <= holder.taken_at + HOLD]
    assert len(in_hold) >= TICKS_IN_HOLD, f"the loop turned {len(in_hold)} times in the {HOLD:.0f} s hold"
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["status"] == "needs_look", got


async def test_a_job_in_a_worker_thread_waits_without_holding_up_the_loops_reads(stack: Stack) -> None:
    """The hourly purge runs in a worker thread. While another process holds the lock, the purge
    waits for it; the loop's own reads (every page reads the database) carry on meanwhile."""
    h = stack.h
    await linked_user(stack)
    holder = _hold_lock(str(h.settings.db_path), 3.0)
    reads = 0
    try:
        with _Ticker() as ticker:
            purge = asyncio.ensure_future(asyncio.to_thread(h.db.purge_expired))
            await asyncio.sleep(0.2)  # the purge is waiting for the lock by now
            while not purge.done():
                assert h.db.get_user(SUB) is not None  # a read, as a page makes
                reads += 1
                await asyncio.sleep(0.02)
            await purge
            longest = await ticker.done()
    finally:
        holder.wait(timeout=30)
    assert reads > 50, reads  # the loop kept reading through the 3 s
    assert longest < GAP, f"the loop stalled {longest:.2f} s behind the purge's wait"
