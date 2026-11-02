"""What a member sees when another connection takes the database's write lock just after a request
passed the check before it (dbwait.py), and holds it past the busy timeout (0.7.46 re-audit).

Before: an apply whose change Archidekt had already taken ended as a raw "Error executing tool
apply_proposal" and stayed "applying" until a restart, or as "failed ... the deck is partly
changed" though the whole change had landed; a refresh caught between retiring the old token and
saving the new one signed the member out. Now the apply waits off the event loop and records the
change as applied, a tool never answers with a raw error, "partly changed" is said only when it
is, and a refresh writes all or nothing, answering "busy, try again" with the old token still
good. The waits here are shortened (the busy timeout, the jobs' wait) so the tests run in
seconds; the order of events is the same as with the real ones."""

from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from mtg_gateway import db as dbmod
from mtg_gateway import dbwait
from mtg_gateway.decks import _partial_note

from .conftest import Harness, Stack
from .test_approval_modes import _set_mode
from .test_db_lock_wait import _hold
from .test_decks_and_proxy import call, linked_user, structured
from .test_unanswered_writes import ONE, _propose, _qty

BUSY_MS = 300  # the busy timeout, shortened from db.BUSY_TIMEOUT_MS


@pytest.fixture
def short_busy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dbmod, "BUSY_TIMEOUT_MS", BUSY_MS)

    def apply(db: dbmod.Database) -> None:
        with db._lock:
            db._conn.execute(f"PRAGMA busy_timeout={BUSY_MS}")

    return apply


def _lock_after_patch(stack: Stack, seconds: float) -> dict[str, subprocess.Popen[str]]:
    """Archidekt takes the change's write; at that moment another process takes the database's
    write lock and keeps it ``seconds``."""
    ark = stack.ark
    orig = ark.transport.handler
    held: dict[str, subprocess.Popen[str]] = {}

    def handler(req):
        resp = orig(req)
        if req.method == "PATCH" and "/decks/42/modifyCards" in req.url.path and "p" not in held:
            held["p"] = _hold(str(stack.h.settings.db_path), seconds)
        return resp

    ark.transport.handler = handler
    return held


@pytest.mark.parametrize("past_the_jobs_wait", [False, True], ids=["lock-13s", "lock-25s"])
async def test_an_apply_caught_by_the_lock_is_recorded_as_applied(
    stack: Stack, short_busy, monkeypatch: pytest.MonkeyPatch, past_the_jobs_wait: bool
) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    pid = await _propose(h, token, ONE)
    short_busy(h.db)
    if past_the_jobs_wait:  # the lock outlasts even a background job's wait for it
        monkeypatch.setattr(dbwait, "JOB_WAIT_SECONDS", 0.2)
    held = _lock_after_patch(stack, 1.5)
    try:
        out = await call(h, token, "apply_proposal", {"proposal_id": pid})
    finally:
        if "p" in held:
            held["p"].wait(timeout=30)
    assert "p" in held, "the lock was never taken"
    text = str(out)
    assert "Error executing tool" not in text and "database is locked" not in text, out
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["state"] == "applied", got
    assert _qty(ark, "Forest") == 3 and len(ark.patches) == 1
    assert "partly changed" not in text and "partly changed" not in str(got), got


def test_partly_changed_is_said_only_when_part_was_sent() -> None:
    every = _partial_note({"sent_entries": 1, "of_entries": 1, "snapshot_id": "s"})
    assert "partly changed" not in every, every
    assert "All 1 deck row changes had already been sent" in every, every
    some = _partial_note({"sent_entries": 1, "of_entries": 3, "snapshot_id": "s"})
    assert "1 of 3 deck row changes had already been sent, so the deck is partly changed." in some


async def test_a_tool_that_meets_the_lock_answers_busy_never_a_raw_error(
    stack: Stack, short_busy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock taken after the request passed the check: the tool's write fails, and the member's
    assistant is told the gateway is busy, never "Error executing tool"."""
    h = stack.h
    token = await linked_user(stack)
    short_busy(h.db)
    monkeypatch.setattr(dbmod.Database, "write_free", lambda self: True)  # passed the check
    holder = _hold(str(h.settings.db_path), 2.0)
    try:
        out = await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": ONE})
    finally:
        holder.wait(timeout=30)
    text = str(out)
    assert "Error executing tool" not in text, out
    s = structured(out)
    assert s.get("error") == "busy" and s.get("retryable") is True, s
    assert "The gateway is busy" in s["message"], s


async def _refresh(gw: Harness, client: dict, refresh_token: str):
    return await gw.token(client, grant_type="refresh_token", refresh_token=refresh_token)


async def test_a_refresh_caught_by_the_lock_keeps_the_member_signed_in(
    gw: Harness, short_busy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another process takes the lock at the first moment it can after the old refresh token is
    retired. Before, that was before the new pair was saved: the refresh failed, and the app's
    retry with the old token was taken for reuse and signed the member out."""
    short_busy(gw.db)
    client = await gw.register()
    t1 = await gw.tokens_for(client)
    db = gw.db
    orig = dbmod.Database.revoke_generation
    holders: list[subprocess.Popen[str]] = []

    def revoke_then_lock(self, family: str, created_at: int) -> None:
        orig(self, family, created_at)
        ready = threading.Event()

        def take() -> None:
            p = subprocess.Popen(
                [sys.executable, "-c", _HOLD_WAITING, str(gw.settings.db_path), "1.5"],
                stdout=subprocess.PIPE,
                text=True,
            )
            holders.append(p)
            assert p.stdout is not None
            p.stdout.readline()
            ready.set()

        threading.Thread(target=take, daemon=True).start()
        if not self._conn.in_transaction:  # the lock can be taken now: wait until it is
            assert ready.wait(30)

    monkeypatch.setattr(dbmod.Database, "revoke_generation", revoke_then_lock)
    first = await _refresh(gw, client, t1["refresh_token"])
    monkeypatch.setattr(dbmod.Database, "revoke_generation", orig)
    for p in holders:
        p.wait(timeout=30)
    if first.status_code == 200:
        t2 = first.json()
    else:  # told to try again: the app sends the same refresh token once the gateway is free
        assert first.status_code == 400 and first.json()["error"] == "invalid_request", first.text
        again = await _refresh(gw, client, t1["refresh_token"])
        assert again.status_code == 200, again.text
        t2 = again.json()
    assert (await gw.mcp(t2["access_token"], "tools/list")).status_code == 200
    t3 = await _refresh(gw, client, t2["refresh_token"])
    assert t3.status_code == 200, t3.text
    assert db.write_free()


async def test_a_refresh_while_the_lock_is_held_is_told_busy_and_the_token_still_works(
    gw: Harness, short_busy, monkeypatch: pytest.MonkeyPatch
) -> None:
    short_busy(gw.db)
    client = await gw.register()
    t1 = await gw.tokens_for(client)
    monkeypatch.setattr(dbmod.Database, "write_free", lambda self: True)  # passed the check
    holder = _hold(str(gw.settings.db_path), 1.5)
    try:
        busy = await _refresh(gw, client, t1["refresh_token"])
    finally:
        holder.wait(timeout=30)
    monkeypatch.undo()
    assert busy.status_code == 400, busy.text
    assert busy.json()["error"] == "invalid_request" and "busy" in busy.json()["error_description"], busy.text
    again = await _refresh(gw, client, t1["refresh_token"])
    assert again.status_code == 200, again.text
    assert (await gw.mcp(again.json()["access_token"], "tools/list")).status_code == 200


# Takes the write lock as soon as it can (waiting up to 30 s for it), says so, holds it.
_HOLD_WAITING = """
import sqlite3, sys, time
c = sqlite3.connect(sys.argv[1], isolation_level=None, timeout=30)
c.execute("BEGIN IMMEDIATE")
print("held", flush=True)
time.sleep(float(sys.argv[2]))
c.execute("COMMIT")
"""
