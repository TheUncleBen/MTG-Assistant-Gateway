"""An apply cut off while its write was out (a crash, an out-of-memory kill, a restart, the six-hour
backstop) never says "Nothing further was changed. Create a new proposal": Archidekt may already
have the change, and making it again would make it twice. The apply keeps a note in the database
just before each Archidekt write, so even a hard crash leaves a record that writes had begun; the
proposal then says the change may be on the deck and offers Propose the rest (which reads the deck
and proposes only what is missing). After a restart the gateway reads the deck back and says what
landed. An apply cut off before any write went out still says, truly, that nothing was changed."""

from __future__ import annotations

import asyncio
import re
import time

import httpx

from mtg_gateway.decks import BROWSER_CLIENT, current_client

from .conftest import Stack
from .fake_archidekt import FakeArchidekt
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .test_unanswered_writes import DUPLICATE_INVITES, ONE, _propose, _qty
from .track_a_helpers import press_approve

SUB = "user-1"


def _hang(ark: FakeArchidekt, *, method: str, path: str) -> tuple[asyncio.Event, asyncio.Event]:
    """The first ``method`` request whose path holds ``path`` reaches Archidekt (a write lands),
    and its answer never comes back while the test runs. Returns (out, release)."""
    orig = ark.transport.handler
    out, release = asyncio.Event(), asyncio.Event()

    async def handler(req: httpx.Request) -> httpx.Response:
        resp = orig(req)
        if req.method == method and path in req.url.path and not out.is_set():
            out.set()
            await release.wait()
        return resp

    ark.transport.handler = handler
    return out, release


async def _start_apply(stack: Stack, token: str, pid: str, out: asyncio.Event) -> asyncio.Task:
    task = asyncio.ensure_future(call(stack.h, token, "apply_proposal", {"proposal_id": pid}))
    await asyncio.wait_for(out.wait(), 30)
    return task


def _crash(stack: Stack) -> list[asyncio.Task]:
    """The process that ran the apply is gone: nothing it held in memory is left (the test keeps
    its task only to end it)."""
    decks = stack.gw.decks
    gone = list(decks._running.values())
    decks._running.clear()
    decks._progress.clear()
    decks._deck_locks.clear()
    decks._archidekt_in_flight.clear()
    decks._slot_waiters.clear()
    pacer = decks.client.pacer  # the call left unanswered no longer fills a slot of a new process
    pacer._in_flight = pacer._background_in_flight = 0
    return gone


async def _end(stack: Stack, task: asyncio.Task, release: asyncio.Event, gone: list[asyncio.Task]) -> None:
    release.set()
    task.cancel()
    await asyncio.gather(task, *gone, *stack.gw.decks._running.values(), return_exceptions=True)


async def _review_page(stack: Stack, pid: str) -> str:
    """The member's review page of the change, its words only."""
    b = Browser(stack.h)
    await b.login()
    try:
        page = (await b.http.get(f"/proposals/{pid}", headers={"accept": "text/html"})).text
    finally:
        await b.aclose()
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.split("<main", 1)[-1]))


async def test_a_restart_while_the_write_was_out_never_says_nothing_changed(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    pid = await _propose(h, token, ONE)
    out, release = _hang(ark, method="PATCH", path="/decks/42/modifyCards")
    task = await _start_apply(stack, token, pid, out)
    gone = _crash(stack)
    try:
        assert h.db.fail_interrupted_applies() == 1  # the gateway starts again
        got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
        assert got["state"] == "failed", got
        for bad in DUPLICATE_INVITES:
            assert bad not in got["next_step"], got["next_step"]
        assert got.get("may_have_changed") is True, got
        assert got.get("rest_offered") is True, got
        assert "Propose the rest" in got["next_step"], got["next_step"]
        assert "may" in got["next_step"], got["next_step"]
        # a second apply is refused, and its refusal does not invite the change again either
        again = structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
        assert again["ok"] is False, again
        for bad in DUPLICATE_INVITES:
            assert bad not in again["message"], again["message"]
        page = await _review_page(stack, pid)  # the member's own page says the same
        for bad in (*DUPLICATE_INVITES, "Ask your assistant for a new proposal"):
            assert bad not in page, page
        assert "part or all of it may be on the deck" in page and "Propose the rest" in page, page
    finally:
        await _end(stack, task, release, gone)


async def test_the_six_hour_backstop_never_says_nothing_changed(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    pid = await _propose(h, token, ONE)
    out, release = _hang(ark, method="PATCH", path="/decks/42/modifyCards")
    task = await _start_apply(stack, token, pid, out)
    gone = _crash(stack)
    try:
        with h.db.tx() as c:  # the apply started seven hours ago and never finished
            c.execute("UPDATE proposals SET applied_at = ? WHERE id = ?", (int(time.time()) - 7 * 3600, pid))
        h.db.purge_expired()
        got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
        assert got["state"] == "failed", got
        for bad in DUPLICATE_INVITES:
            assert bad not in got["next_step"], got["next_step"]
        assert got.get("may_have_changed") is True and got.get("rest_offered") is True, got
    finally:
        await _end(stack, task, release, gone)


async def test_after_a_restart_a_change_read_back_whole_is_applied(stack: Stack) -> None:
    """The gateway starts again and reads the deck: the whole change is there, so the change is
    applied, as a finished apply records it: the change that waits on it is no longer held, it
    offers nothing to make again, and Undo puts the deck back to its snapshot."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    pid = await _propose(h, token, ONE)
    after = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [{"action": "set_quantity", "card_name": "Island", "quantity": 2}],
                "separate": True,
                "depends_on": [pid],
            },
        )
    )
    assert after["ok"], after
    out, release = _hang(ark, method="PATCH", path="/decks/42/modifyCards")
    task = asyncio.ensure_future(press_approve(h, token, pid))
    await asyncio.wait_for(out.wait(), 30)
    gone = _crash(stack)
    try:
        assert h.db.fail_interrupted_applies() == 1
        held = structured(await call(h, token, "get_proposal", {"proposal_id": after["proposal_id"]}))
        assert held["status"] == "held", held  # it waits on a change that failed
        assert _qty(ark, "Forest") == 3  # Archidekt took it
        assert await stack.gw.decks.read_back_interrupted() == 1
        got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
        assert got["state"] == "applied", got
        assert got["result"]["read_back"] == "all" and got["snapshot_id"], got
        assert got["next_step"].startswith("Done."), got["next_step"]
        for bad in (*DUPLICATE_INVITES, "Propose the rest"):
            assert bad not in got["next_step"], got["next_step"]
        assert not got.get("rest_offered") and not got.get("may_have_changed"), got
        page = await _review_page(stack, pid)
        for bad in (*DUPLICATE_INVITES, "Ask your assistant for a new proposal", "could not be applied"):
            assert bad not in page, page
        # the hold it caused is lifted, as after an apply that finished
        free = structured(await call(h, token, "get_proposal", {"proposal_id": after["proposal_id"]}))
        assert free["state"] == "pending" and free.get("status") != "held", free
        assert await stack.gw.decks.read_back_interrupted() == 0  # read once
        # Undo puts the deck back to the snapshot taken before the change
        reset = current_client.set(BROWSER_CLIENT)
        try:
            undone = await stack.gw.decks.undo(SUB, got["snapshot_id"], applied=[pid])
        finally:
            current_client.reset(reset)
        assert undone["state"] == "applied", undone
        assert _qty(ark, "Forest") == 15
    finally:
        await _end(stack, task, release, gone)


async def test_after_a_restart_a_change_that_did_not_land_is_said_plainly(stack: Stack) -> None:
    """Writes had begun but the deck read back is as it was: nothing was changed, said truly."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    pid = await _propose(h, token, ONE)
    orig = ark.transport.handler
    out, release = asyncio.Event(), asyncio.Event()

    async def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and "/decks/42/modifyCards" in req.url.path and not out.is_set():
            out.set()  # the write never reaches Archidekt
            await release.wait()
            raise httpx.ConnectError("gone", request=req)
        return orig(req)

    ark.transport.handler = handler
    task = await _start_apply(stack, token, pid, out)
    gone = _crash(stack)
    try:
        assert h.db.fail_interrupted_applies() == 1
        assert await stack.gw.decks.read_back_interrupted() == 1
        got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
        assert got["result"]["read_back"] == "none", got["result"]
        assert "nothing was changed" in got["next_step"].lower(), got["next_step"]
        assert not got.get("may_have_changed"), got
    finally:
        await _end(stack, task, release, gone)


async def test_an_apply_cut_off_before_any_write_says_nothing_was_changed(stack: Stack) -> None:
    """Cut off while it still read the deck: no write had begun, so "nothing was changed" is
    true, and the note that writes had begun is what tells the two apart."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    pid = await _propose(h, token, ONE)
    out, release = _hang(ark, method="GET", path="/decks/42/")
    task = await _start_apply(stack, token, pid, out)
    gone = _crash(stack)
    try:
        assert h.db.fail_interrupted_applies() == 1
        got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
        assert got["state"] == "failed" and not got.get("may_have_changed"), got
        assert "Nothing was changed on Archidekt" in got["next_step"], got["next_step"]
        assert ark.patches == []
    finally:
        await _end(stack, task, release, gone)


async def test_an_apply_left_by_an_older_version_says_it_may_be_on_the_deck(stack: Stack) -> None:
    """A change left "applying" by 0.7.45, which kept no note of its writes: whether one had begun
    is not known, so it must not say "Nothing was changed" (it said so). It says the change may be
    on the deck, and the deck is read back, which then says what landed."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    pid = await _propose(h, token, ONE)
    out, release = _hang(ark, method="PATCH", path="/decks/42/modifyCards")
    task = await _start_apply(stack, token, pid, out)
    gone = _crash(stack)
    try:
        with h.db.tx() as c:  # as 0.7.45 left it: no note of the apply's progress
            c.execute(
                "UPDATE proposals SET result_json = json_remove(result_json, '$.apply_progress') "
                "WHERE id = ?",
                (pid,),
            )
        assert h.db.fail_interrupted_applies() == 1
        got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
        assert got["state"] == "failed", got
        assert "Nothing was changed" not in got["next_step"], got["next_step"]
        assert got.get("may_have_changed") is True, got
        assert "may" in got["next_step"], got["next_step"]
        page = await _review_page(stack, pid)
        assert "Nothing was changed" not in page and "may be on the deck" in page, page
        assert _qty(ark, "Forest") == 3  # Archidekt took it
        assert await stack.gw.decks.read_back_interrupted() == 1
        read = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
        assert read["state"] == "applied" and read["result"]["read_back"] == "all", read
    finally:
        await _end(stack, task, release, gone)
