"""While another process holds the database's write lock (a backup, an admin command), what only
reads goes ahead at once and only what writes waits (0.7.46 re-audit). Before, every page and
every assistant call waited the whole lock (whoami took 8 s), though in WAL mode a read never
waits for a writer.

A request that should only read (a page or JSON read, an assistant's call of read-only tools) is
let through without waiting. Its answer is held back until it ends: if it turned out to need a
write, that write failed at once without changing anything, the answer is dropped, and the
request runs again, from the start, once the lock is free, as every request did before. Before
anything that changes something outside the gateway (an Archidekt write, the identity
provider's tokens, a notification), such a request goes back to waiting first, so that change is
never made twice and what it gives back is always kept."""

from __future__ import annotations

import asyncio
import re
import time

import httpx
import pytest

from mtg_gateway import dbwait
from mtg_gateway.db import Database, DatabaseBusy, read_through

from .conftest import Stack
from .test_db_lock_wait import _hold
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .test_unanswered_writes import ONE


async def test_reads_answer_at_once_while_another_process_holds_the_lock(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    browser = Browser(h, follow_late=False)
    await browser.login()
    for path in ("/decks", "/account", "/"):  # warm: first reads, caches
        assert (await browser.http.get(path)).status_code == 200
    await call(h, token, "whoami", {})
    holder = _hold(str(h.settings.db_path), 4.0)
    try:
        started = time.monotonic()
        replies = await asyncio.gather(
            call(h, token, "whoami", {}),
            call(h, token, "list_my_proposals", {}),
            browser.http.get("/decks"),
            browser.http.get("/account"),
            browser.http.get("/"),
        )
        took = time.monotonic() - started
        assert h.db.write_free() is False  # the lock really was held all along
    finally:
        holder.wait(timeout=30)
    assert took < 2.0, f"reads took {took:.2f} s while the lock was held"
    who = structured(replies[0])
    assert who.get("ok") is not False and not replies[0].get("isError"), replies[0]
    assert structured(replies[1]).get("ok") is not False, replies[1]
    assert [r.status_code for r in replies[2:]] == [200, 200, 200]
    await browser.aclose()


async def test_a_write_still_waits_for_the_lock_and_then_goes_on(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    holder = _hold(str(h.settings.db_path), 2.0)
    try:
        started = time.monotonic()
        out = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": ONE}))
        took = time.monotonic() - started
    finally:
        holder.wait(timeout=30)
    assert out["ok"] and out["state"] == "pending", out
    assert took >= 1.5, took  # it waited for the lock
    mine = structured(await call(h, token, "list_my_proposals", {}))["proposals"]
    assert [p["id"] for p in mine] == [out["proposal_id"]]  # made once


async def test_a_read_that_needs_a_write_runs_again_once_the_lock_is_free(stack: Stack) -> None:
    """The Proposals page writes when it finds a change that should now be held: it is run again
    after the lock, and the page drawn then is the one the member sees."""
    h = stack.h
    await linked_user(stack)
    browser = Browser(h, follow_late=False)
    await browser.login()
    calls = {"n": 0}
    real = Database.hold_blocked

    def counted(self, sub):
        calls["n"] += 1
        with self.tx() as c:  # a write, as when a change has to be held
            c.execute("UPDATE users SET last_seen_at = last_seen_at WHERE sub = ?", (sub,))
        return real(self, sub)

    holder = _hold(str(h.settings.db_path), 2.0)
    try:
        Database.hold_blocked = counted  # type: ignore[method-assign]
        started = time.monotonic()
        page = await browser.http.get("/proposals")
        took = time.monotonic() - started
    finally:
        Database.hold_blocked = real  # type: ignore[method-assign]
        holder.wait(timeout=30)
    assert page.status_code == 200, page.text[:500]
    assert took >= 1.5, took  # it waited for the lock to run again
    assert calls["n"] == 2, calls  # the first run's write failed at once; the second went through
    await browser.aclose()


def _app(db: Database, steps: list[str], sent: list[str]):
    """A tiny app: each request does ``steps`` in order: "write" (a write, its failure ignored as
    some pages do), "outside" (a change outside the gateway, after before_side_effect)."""

    async def app(scope, receive, send):
        for step in steps:
            if step == "write":
                try:
                    with db.tx() as c:
                        c.execute("UPDATE users SET last_seen_at = 1 WHERE sub = 'nobody'")
                except DatabaseBusy:
                    pass
            else:
                await dbwait.before_side_effect()
                sent.append("outside")
        await send(
            {"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]}
        )
        await send({"type": "http.response.body", "body": b"ok"})

    return app


@pytest.mark.parametrize(
    "steps",
    [["write", "outside"], ["outside", "write"], ["write"], ["outside"]],
    ids=["write-then-outside", "outside-then-write", "write", "outside"],
)
async def test_nothing_outside_is_done_twice(stack: Stack, steps: list[str]) -> None:
    """Whatever the order of a read's write and its change outside the gateway, the outside change
    is made once, after the lock is free, and the request is answered."""
    db = stack.h.db
    sent: list[str] = []
    mw = dbwait.DatabaseWaitMiddleware(_app(db, steps, sent), db=db, limit=10)
    transport = httpx.ASGITransport(app=mw)
    holder = _hold(str(db.path), 1.5)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/page")
    finally:
        holder.wait(timeout=30)
    assert r.status_code == 200 and r.text == "ok"
    assert sent == (["outside"] if "outside" in steps else []), sent
    assert read_through.get() is None


async def test_sign_in_steps_and_writes_never_go_ahead(stack: Stack) -> None:
    db = stack.h.db
    mw = dbwait.DatabaseWaitMiddleware(lambda *a: None, db=db, reads=lambda name: True)
    for method, path in (
        ("GET", "/auth/callback"),
        ("GET", "/authorize"),
        ("GET", "/login"),
        ("POST", "/decks"),
        ("POST", "/api/v1/proposals"),
    ):
        assert not mw._may_read({"type": "http", "method": method, "path": path}), (method, path)
    for method, path in (
        ("GET", "/decks"),
        ("HEAD", "/account"),
        ("GET", "/api/v1/proposals"),
        ("POST", "/mcp"),
    ):
        assert mw._may_read({"type": "http", "method": method, "path": path}), (method, path)
    assert not mw._reads_only({"path": "/mcp"}, b'{"jsonrpc":"2.0","id":1,"method":"initialize"}')


async def test_a_late_deck_page_waits_for_the_lock_off_the_loop(stack: Stack) -> None:
    """A deck page slower than latepage.LATE_AFTER, read while another process holds the lock:
    the frame goes out, the page carries on after the read went ahead, and its write (the waiting
    proposals' hold, db.hold_blocked) found the lock. It used to wait for it on the event loop, up
    to db.BUSY_TIMEOUT_MS, so every other request stalled behind it (4 s measured in the final
    audit of 0.7.46). The late page now waits off the loop like any other write and is then run
    again; other requests are answered meanwhile and the finished page is the real one."""
    from .test_db_lock_wait import _Ticker
    from .test_late_pages import PAGE, _linked, _slow

    h = stack.h
    token = await linked_user(stack)
    b = await _linked(stack)
    try:
        _slow(stack, 2.0)
        holder = _hold(str(h.settings.db_path), 6.0)
        try:
            with _Ticker() as ticker:
                started = time.monotonic()
                frame = await b.http.get("/decks/42", headers=PAGE)
                assert time.monotonic() - started < 1.5
                assert "data-late=" in frame.text, frame.text[:400]
                key = re.search(r"data-late='(/late/[A-Za-z0-9_-]+)'", frame.text).group(1)
                await asyncio.sleep(2.5)  # the page's Archidekt read is over; its write meets the lock
                assert ticker.longest < 0.5, f"the loop stalled {ticker.longest:.2f} s behind a late page"
                t0 = time.monotonic()
                who = await call(h, token, "whoami", {})
                assert time.monotonic() - t0 < 0.5, "a read waited behind the late page"
                assert structured(who).get("ok") is not False, who
                assert h.db.write_free() is False  # the lock really was held all along
                longest = await ticker.done()
        finally:
            holder.wait(timeout=30)
        assert longest < 0.5, f"the event loop stalled {longest:.2f} s"
        ready = await b.http.get(key)
        assert ready.status_code == 200 and ready.json()["ready"] is True, ready.text
        page = await b.http.get("/decks/42", headers=PAGE)
        assert page.status_code == 200 and "data-late" not in page.text, page.text[:400]
    finally:
        await b.aclose()


async def test_a_late_deck_page_during_a_lock_logs_no_error(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    """N8 of the 0.7.46 audit: the late deck page's writes that find the lock (the waiting
    proposals' re-check, the prices read hook) are handled (the page runs again once the lock is
    free), so they log below ERROR. They used to log two ERROR lines with a traceback each."""
    import logging

    from .test_late_pages import PAGE, _linked, _slow

    h = stack.h
    await linked_user(stack)
    b = await _linked(stack)
    try:
        # a waiting proposal on the deck, so the page's re-check has something to look at
        await call(h, await linked_user(stack), "propose_deck_changes", {"deck_id": "42", "changes": ONE})
        _slow(stack, 2.0)
        caplog.set_level(logging.INFO)
        holder = _hold(str(h.settings.db_path), 4.0)
        try:
            frame = await b.http.get("/decks/42", headers=PAGE)
            assert "data-late=" in frame.text, frame.text[:400]
            key = re.search(r"data-late='(/late/[A-Za-z0-9_-]+)'", frame.text).group(1)
            await asyncio.sleep(2.5)  # the page's Archidekt read is over; its writes meet the lock
            assert h.db.write_free() is False  # the lock really was held all along
        finally:
            holder.wait(timeout=30)
        ready = await b.http.get(key)
        assert ready.status_code == 200 and ready.json()["ready"] is True, ready.text
    finally:
        await b.aclose()
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, [f"{r.name}: {r.getMessage()}" for r in errors]
    # one lock event, one line (round 2 of N8): the re-check and the read hook met the same lock
    locked = [r for r in caplog.records if r.levelno == logging.WARNING and "locked" in r.getMessage()]
    assert len(locked) == 1, [f"{r.name}: {r.getMessage()}" for r in locked]


async def test_an_unexpected_failure_of_a_check_or_hook_still_logs_an_error(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    """Only the handled DatabaseBusy is below ERROR: anything else a re-check or a read hook
    raises is still an ERROR with its traceback."""
    import logging

    from mtg_gateway.archidekt import Deck

    decks = stack.gw.decks
    deck = Deck(id="42", name="d", owner="alice", updated_at="", cards=[], categories=[], raw={})

    async def read(*_a, **_k):
        return deck

    real_board, real_read, real_hooks = decks.db.pending_board, decks.get_any_deck, decks.read_hooks
    for raised, level in ((DatabaseBusy(), logging.WARNING), (RuntimeError("boom"), logging.ERROR)):
        caplog.clear()

        def fails(*_a, _e=raised, **_k):
            raise _e

        decks.db.pending_board = fails
        decks.get_any_deck = read
        decks.read_hooks = [fails]
        try:
            await decks._read_page_deck("alice", "42", ("alice", "42"), fresh=False)
        finally:
            decks.db.pending_board, decks.get_any_deck, decks.read_hooks = real_board, real_read, real_hooks
        for words in ("re-check of waiting proposals", "a deck read hook"):
            logged = [r for r in caplog.records if words in r.getMessage()]
            assert [r.levelno for r in logged] == [level], (
                words,
                [(r.levelno, r.getMessage()) for r in logged],
            )
            assert (logged[0].exc_info is not None) is (level == logging.ERROR)
