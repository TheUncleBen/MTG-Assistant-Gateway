"""An apply that Archidekt's waits stopped before it changed anything: the proposal is
pending again. The unused backup copy whose delete Archidekt refused for a while is deleted
later by the gateway itself: queued in the database, one delete per pass, never while the
client is paused, with a backoff and a limit, and only after the same safety checks as at once."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import time

import httpx
import pytest

from mtg_gateway import archidekt as ark_mod
from mtg_gateway import decks as decks_mod
from mtg_gateway.archidekt import ArchidektClient
from mtg_gateway.history_view import build_events
from mtg_gateway.sync import DeckSync

from .conftest import (
    AUTO,
    COLLECTION,
    FakeIdP,
    Harness,
    Stack,
    StackOptions,
    make_settings,
    open_stack,
    running,
)
from .fake_archidekt import FakeArchidekt
from .test_background_apply import LATER, NOTHING_CHANGED, THREE, _finished
from .test_browse_collection import linked
from .test_decks_and_proxy import _client, call, linked_user, mcp_token, structured

SUB = "user-1"


@pytest.fixture
def stack_options(monkeypatch) -> StackOptions:
    """The shared AUTO stack, with the assistant's apply answering "applying" after 0.2 s."""
    monkeypatch.setattr(decks_mod, "APPLY_WAIT_SECONDS", 0.2)
    return AUTO


@pytest.fixture
def quick_waits(monkeypatch) -> list[float]:
    """The apply's 429 waits, recorded instead of slept."""
    waits: list[float] = []

    async def wait_out(self, seconds: float) -> None:
        waits.append(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(ArchidektClient, "_wait_out", wait_out)
    return waits


def _patches_429(ark: FakeArchidekt, *, retry_after: str = "30") -> dict[str, int]:
    """Every card write meets a 429 asking for ``retry_after`` seconds; DELETEs are counted."""
    orig = ark.transport.handler
    seen = {"deletes": 0, "patches": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and "modifyCards" in req.url.path:
            seen["patches"] += 1
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": retry_after})
        if req.method == "DELETE":
            seen["deletes"] += 1
        return orig(req)

    ark.transport.handler = handler
    seen["orig"] = orig  # type: ignore[assignment]
    return seen


async def _stop(h, ark: FakeArchidekt, token: str) -> tuple[str, dict, dict]:
    """Propose an edit of deck 42 and apply it while Archidekt answers every card write with a 429
    asking for 30 s: the apply stops with nothing changed and the backup's delete is queued.
    Returns the proposal id, its stored result and the request counts. The pause stays running."""
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    seen = _patches_429(ark)
    await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    row = await _finished(h, p["proposal_id"])
    assert row["state"] == "pending" and row["result"]["backup_note"] == decks_mod.BACKUP_LATER, row
    return p["proposal_id"], row["result"], seen


def _calm(h, ark: FakeArchidekt, seen: dict) -> None:
    """Archidekt's pause is over and it answers normally again."""
    h.app.state.gateway.decks.client.pacer._retry_until = 0
    ark.transport.handler = seen["orig"]


def _due_now(h) -> None:
    with h.db.tx() as c:
        c.execute("UPDATE backup_deletes SET due_at = 0")


def _events(h, event: str) -> list[dict]:
    return [e for e in h.db.audit_recent(200, sub=SUB) if e["event"] == event]


@pytest.mark.usefixtures("quick_waits")
async def test_a_queued_delete_waits_for_the_pause_then_runs_once_it_ends(stack: Stack) -> None:
    """Queued for when the pause ends plus a margin; nothing is sent while the client is
    paused or before it is due; then the copy is deleted, the snapshot forgets it, the audit says
    it was deleted later, and the proposal's message no longer promises it."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    before = int(time.time())
    pid, result, seen = await _stop(h, ark, token)
    copy, snap_id = str(result["backup_deck_id"]), result["snapshot_id"]
    [job] = h.db.queued_backup_deletes()
    # Archidekt asked for 30 s; the first try is due when that ends, plus the margin
    assert before + 30 + decks_mod.BACKUP_DELETE_MARGIN - 2 <= job["due"] <= int(time.time()) + 61, job
    assert _events(h, "backup_delete_queued")[0]["detail"]["error"] == "paused"
    _due_now(h)
    assert svc.client.pacer.paused()
    assert await svc.delete_one_queued_backup() is None  # paused: nothing is sent
    assert seen["deletes"] == 0 and ark.deleted == []
    _calm(h, ark, seen)
    with h.db.tx() as c:
        c.execute("UPDATE backup_deletes SET due_at = ?", (int(time.time()) + 3600,))
    assert await svc.delete_one_queued_backup() is None  # not due yet
    assert ark.deleted == []
    _due_now(h)
    assert await svc.delete_one_queued_backup() == {"deleted": snap_id}
    assert ark.deleted == [int(copy)] and 42 in ark.decks
    assert h.db.queued_backup_deletes() == []
    assert h.db.get_snapshot(snap_id, SUB)["backup_deck_id"] is None
    [done] = _events(h, "backup_deleted")
    assert done["detail"]["later"] is True and done["detail"]["backup_deck_id"] == copy, done
    row = h.db.get_proposal(pid, SUB)
    assert row["state"] == "pending"
    assert row["result"]["detail"] == f"{ark_mod.PATIENCE_SPENT} {NOTHING_CHANGED}", row["result"]
    assert row["result"]["backup_deleted"] is True and "backup_deck_id" not in row["result"]
    assert "backup_note" not in row["result"]
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["next_step"] == f"{ark_mod.PATIENCE_SPENT} {NOTHING_CHANGED}", got["next_step"]
    assert await svc.delete_one_queued_backup() is None  # nothing left


@pytest.mark.usefixtures("quick_waits")
async def test_each_pass_deletes_at_most_one_copy(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    _pid1, r1, seen = await _stop(h, ark, token)
    _calm(h, ark, seen)
    _pid2, r2, seen = await _stop(h, ark, token)
    _calm(h, ark, seen)
    assert len(h.db.queued_backup_deletes()) == 2
    _due_now(h)
    assert "deleted" in await svc.delete_one_queued_backup()
    assert len(ark.deleted) == 1 and len(h.db.queued_backup_deletes()) == 1
    assert "deleted" in await svc.delete_one_queued_backup()
    assert sorted(ark.deleted) == sorted([int(r1["backup_deck_id"]), int(r2["backup_deck_id"])])


@pytest.mark.usefixtures("quick_waits")
async def test_failed_later_deletes_back_off_then_give_up(stack: Stack) -> None:
    """Archidekt answers 503: the next try waits 10, 20, then 40 minutes; the fourth failure
    gives up. The copy stays, the audit says so, and the proposal's message goes back to the
    copy staying in the backup folder."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    pid, result, seen = await _stop(h, ark, token)
    copy = int(result["backup_deck_id"])
    _calm(h, ark, seen)
    orig = ark.transport.handler
    tries = {"n": 0}

    def down(req: httpx.Request) -> httpx.Response:
        if req.method == "DELETE":
            tries["n"] += 1
            return httpx.Response(503, json={"detail": "down"})
        return orig(req)

    ark.transport.handler = down
    for attempt, minutes in ((1, 10), (2, 20), (3, 40)):
        _due_now(h)
        now = int(time.time())
        out = await svc.delete_one_queued_backup()
        assert out is not None and out["attempts"] == attempt, out
        [job] = h.db.queued_backup_deletes()
        assert job["attempts"] == attempt and now + minutes * 60 - 1 <= job["due"] <= now + minutes * 60 + 2
        assert await svc.delete_one_queued_backup() is None  # not due again yet
    _due_now(h)
    out = await svc.delete_one_queued_backup()
    assert out == {"given_up": result["snapshot_id"], "error": "unavailable"}, out
    assert tries["n"] == 4 and h.db.queued_backup_deletes() == [] and copy in ark.decks
    [gave] = _events(h, "backup_delete_given_up")
    assert gave["detail"]["attempts"] == 4
    row = h.db.get_proposal(pid, SUB)
    kept = (
        "Nothing was changed on Archidekt. The unused backup copy could not be deleted; it stays in the "
        "backup folder. The proposal is still pending, so it can be approved again later."
    )
    assert row["result"]["detail"] == f"{ark_mod.PATIENCE_SPENT} {kept}", row["result"]["detail"]
    assert row["result"]["backup_note"] == decks_mod.BACKUP_KEPT
    assert h.db.get_snapshot(result["snapshot_id"], SUB)["backup_deck_id"] == str(copy)
    assert await svc.delete_one_queued_backup() is None  # given up: never tried again


@pytest.mark.usefixtures("quick_waits")
async def test_a_429_on_a_later_delete_waits_out_the_pause_and_counts_for_the_breaker(
    stack: Stack, monkeypatch
) -> None:
    """Outside an apply the later delete is a normal call: its 429 counts towards the
    breaker, and the next try waits at least the pause Archidekt asked for (plus the margin)."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    monkeypatch.setattr(decks_mod, "BACKUP_DELETE_BACKOFF", 10)
    token = await linked_user(stack)
    _pid, _result, seen = await _stop(h, ark, token)
    _calm(h, ark, seen)
    orig = ark.transport.handler

    def busy(req: httpx.Request) -> httpx.Response:
        if req.method == "DELETE":
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "100"})
        return orig(req)

    ark.transport.handler = busy
    failures = svc.client.pacer._failures
    _due_now(h)
    now = int(time.time())
    out = await svc.delete_one_queued_backup()
    assert out is not None and out["attempts"] == 1, out
    assert svc.client.pacer._failures == failures + 1  # counted, as it is outside an apply
    [job] = h.db.queued_backup_deletes()
    assert now + 100 + decks_mod.BACKUP_DELETE_MARGIN - 2 <= job["due"] <= now + 100 + 32, job
    assert await svc.delete_one_queued_backup() is None  # paused now: nothing is tried
    svc.client.pacer._retry_until = 0


@pytest.mark.usefixtures("quick_waits")
@pytest.mark.parametrize("answer", ["gone", "forbidden", "not_linked"])
async def test_not_found_is_done_and_forbidden_or_unlinked_give_up_at_once(stack: Stack, answer: str) -> None:
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    pid, result, seen = await _stop(h, ark, token)
    copy, snap_id = int(result["backup_deck_id"]), result["snapshot_id"]
    _calm(h, ark, seen)
    if answer == "gone":
        del ark.decks[copy]  # deleted on Archidekt meanwhile
    elif answer == "forbidden":
        orig = ark.transport.handler

        def refuse(req: httpx.Request) -> httpx.Response:
            if req.method == "DELETE":
                return httpx.Response(403, json={"detail": "You do not have permission."})
            return orig(req)

        ark.transport.handler = refuse
    else:
        svc.unlink(SUB)
    _due_now(h)
    out = await svc.delete_one_queued_backup()
    assert h.db.queued_backup_deletes() == []
    row = h.db.get_proposal(pid, SUB)
    if answer == "gone":
        assert out == {"deleted": snap_id, "already_gone": True}, out
        assert h.db.get_snapshot(snap_id, SUB)["backup_deck_id"] is None
        assert row["result"]["detail"] == f"{ark_mod.PATIENCE_SPENT} {NOTHING_CHANGED}"
    else:
        assert out == {"given_up": snap_id, "error": answer}, out
        assert copy in ark.decks and ark.deleted == []
        assert row["result"]["backup_note"] == decks_mod.BACKUP_KEPT
        assert _events(h, "backup_delete_given_up")[0]["detail"]["attempts"] == 1


@pytest.mark.usefixtures("quick_waits")
async def test_the_safety_checks_refuse_a_mismatched_snapshot(stack: Stack) -> None:
    """Before it deletes, the pass checks the copy as the at-once delete does: the snapshot
    must still record exactly that copy, and the id must never be the deck itself."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    _pid, result, seen = await _stop(h, ark, token)
    copy, snap_id = int(result["backup_deck_id"]), result["snapshot_id"]
    _calm(h, ark, seen)
    h.db.set_snapshot_backup(snap_id, backup_deck_id="777", backup_url="")
    _due_now(h)
    assert await svc.delete_one_queued_backup() == {"skipped": snap_id}
    assert ark.deleted == [] and copy in ark.decks and h.db.queued_backup_deletes() == []
    assert _events(h, "backup_delete_skipped")[0]["detail"]["later"] is True
    # a queued id that is the deck itself (recorded so on the snapshot too) is never deleted
    h.db.set_snapshot_backup(snap_id, backup_deck_id="42", backup_url="")
    h.db.queue_backup_delete(snap_id, sub=SUB, backup_deck_id="42", deck_id="42", proposal_id=_pid, due=0)
    assert await svc.delete_one_queued_backup() == {"skipped": snap_id}
    assert ark.deleted == [] and 42 in ark.decks


@pytest.mark.usefixtures("quick_waits")
async def test_the_queue_outlives_a_re_apply_of_the_proposal(stack: Stack) -> None:
    """The stopped proposal is approved again later. The re-apply is re-checked, takes a
    new snapshot and a new backup and applies; the old copy's queued delete is untouched by the
    rewritten result and still runs, and the new copy is kept. History tells the two apart."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    pid, result, seen = await _stop(h, ark, token)
    old_copy, old_snap = int(result["backup_deck_id"]), result["snapshot_id"]
    _calm(h, ark, seen)
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
    row = await _finished(h, pid)
    assert row["state"] == "applied", (out, row)
    new_snap, new_copy = row["result"]["snapshot_id"], int(row["result"]["backup_deck_id"])
    assert new_snap != old_snap and new_copy != old_copy and len(ark.patches) == 3
    assert h.db.get_snapshot(old_snap, SUB) is not None  # the stopped apply's snapshot stays
    [job] = h.db.queued_backup_deletes()
    assert job["snapshot_id"] == old_snap
    _due_now(h)
    assert await svc.delete_one_queued_backup() == {"deleted": old_snap}
    assert ark.deleted == [old_copy] and new_copy in ark.decks
    after = h.db.get_proposal(pid, SUB)
    assert after["state"] == "applied" and after["result"] == row["result"]
    snaps = {s["snapshot_id"]: s for s in svc.list_snapshots(SUB)}
    assert snaps[old_snap].get("stopped_attempt") is True and not snaps[new_snap].get("stopped_attempt")
    events = build_events([], list(snaps.values()), [], csrf_input="")
    text = {e["line"] for e in events}  # the row's second line
    assert any(" just before an apply that stopped before it changed anything" in t for t in text)
    assert any(" just before a change was applied" in t for t in text)
    applied = [e for e in h.db.audit_recent(200, sub=SUB) if e["event"] == "proposal_applied"]
    stopped = [e for e in h.db.audit_recent(200, sub=SUB) if e["event"] == "proposal_apply_stopped"]
    assert len(applied) == 1 and len(stopped) == 1 and stopped[0]["detail"]["state"] == "pending"


@pytest.mark.usefixtures("quick_waits")
async def test_a_stopped_proposal_is_never_applied_again_by_itself(stack: Stack) -> None:
    """Pending again, but nothing re-applies it: not the deck sync, not the later deletes,
    and next_step never tells the assistant to apply it now (the stack's mode is automatic). It
    still expires as any pending proposal does."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    pid, _result, seen = await _stop(h, ark, token)
    patches = seen["patches"]
    _calm(h, ark, seen)
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["state"] == "pending" and got["assistant_may_apply"] is True
    assert "apply_proposal" not in got["next_step"], got["next_step"]
    await DeckSync(svc, 1).run_round()
    _due_now(h)
    await svc.delete_one_queued_backup()
    assert h.db.get_proposal(pid, SUB)["state"] == "pending" and seen["patches"] == patches
    assert ark.patches == []
    with h.db.tx() as c:
        c.execute("UPDATE proposals SET expires_at = ? WHERE id = ?", (int(time.time()) - 1, pid))
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
    assert out["ok"] is False and ark.patches == [], out
    assert structured(await call(h, token, "get_proposal", {"proposal_id": pid}))["state"] == "expired"


@pytest.mark.usefixtures("quick_waits")
async def test_the_queue_outlives_a_restart(tmp_path, idp: FakeIdP, monkeypatch) -> None:
    """The queued delete is kept in the database, so a new gateway on the same database
    (a restart) still deletes the copy."""
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, approval_mode_default="auto", archidekt_base="https://ark.test/api"
    )
    monkeypatch.setattr(decks_mod, "APPLY_WAIT_SECONDS", 0.2)
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h:
        token = await linked_user(Stack(h, ark))
        _pid, result, seen = await _stop(h, ark, token)
    ark.transport.handler = seen["orig"]
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h2:
        svc = h2.app.state.gateway.decks
        [job] = h2.db.queued_backup_deletes()
        assert job["snapshot_id"] == result["snapshot_id"]
        _due_now(h2)
        assert await svc.delete_one_queued_backup() == {"deleted": result["snapshot_id"]}
        assert ark.deleted == [int(result["backup_deck_id"])]


@pytest.mark.usefixtures("quick_waits")
async def test_member_data_deletion_drops_their_queued_deletes(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _stop(h, ark, token)
    assert len(h.db.queued_backup_deletes()) == 1
    out = h.db.delete_member_data(SUB)
    assert out["backup_deletes"] == 1 and h.db.queued_backup_deletes() == []
    h.app.state.gateway.decks.client.pacer._retry_until = 0


@pytest.mark.usefixtures("quick_waits")
async def test_an_immediate_delete_that_meets_a_5xx_is_queued_and_a_403_keeps_the_copy(stack: Stack) -> None:
    """A failed apply (here Archidekt refuses the card write outright) deletes its unused copy
    at once; a transient refusal of that delete queues it, any other keeps today's sentence."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    orig = ark.transport.handler
    answer = {"delete": 503}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and "modifyCards" in req.url.path:
            return httpx.Response(403, json={"detail": "You do not have permission."})
        if req.method == "DELETE":
            return httpx.Response(answer["delete"], json={"detail": "no"})
        return orig(req)

    ark.transport.handler = handler
    for code, note, queued in ((503, decks_mod.BACKUP_LATER, 1), (403, decks_mod.BACKUP_KEPT, 1)):
        answer["delete"] = code
        p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
        await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
        row = await _finished(h, p["proposal_id"])
        assert row["state"] == "failed" and row["result"]["error"] == "forbidden", row
        assert row["result"]["backup_note"] == note, row["result"]
        assert len(h.db.queued_backup_deletes()) == queued


@pytest.mark.usefixtures("quick_waits")
async def test_a_collection_apply_stopped_after_a_record_was_added_stays_failed(
    tmp_path, idp: FakeIdP
) -> None:
    """A collection proposal whose first card went in before Archidekt's waits ran out
    changed something, so it is not pending again (applying it again would add the card twice),
    and the message says your collection is partly changed."""
    async with open_stack(tmp_path, idp, COLLECTION) as cs:
        h, ark = cs.h, cs.ark
        b = await linked(cs)
        try:
            token = await mcp_token(h)
            p = structured(
                await call(h, token, "propose_collection_changes", {"add": ["Sol Ring", "Arcane Signet"]})
            )
            orig = ark.transport.handler

            def second_add_429(req: httpx.Request) -> httpx.Response:
                if req.method == "POST" and req.url.path == "/api/collection/v2/" and ark.collection_posts:
                    return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "0"})
                return orig(req)

            ark.transport.handler = second_add_429
            csrf = await b.csrf("/collection")
            await b.http.post(
                f"/api/v1/proposals/{p['proposal_id']}/apply",
                headers={"X-CSRF-Token": csrf},
                json={"version": p["version"]},
            )
            row = await _finished(h, p["proposal_id"])
        finally:
            await b.aclose()
    result = row["result"]
    assert len(ark.collection_posts) == 1 and row["state"] == "failed", row
    assert result["sent_entries"] == 1 and result["of_entries"] == 2, result
    assert result["detail"] == (
        f"{ark_mod.PATIENCE_SPENT} 1 of 2 binder changes had already been sent, so your binder "
        "is partly changed. Try the rest again later with a new proposal."
    ), result["detail"]


def test_the_wording_is_the_approved_text() -> None:
    assert decks_mod.BACKUP_LATER == (
        "The unused backup copy will be deleted from the backup folder by itself once Archidekt allows it."
    )
    assert LATER.startswith("Nothing was changed on Archidekt. " + decks_mod.BACKUP_LATER)


async def test_the_delete_at_once_never_waits_inside_the_apply(
    stack: Stack, quick_waits: list[float]
) -> None:
    """No extra waiting in the apply: when the delete just after a failed apply meets a
    429, the apply does not wait it out; the delete is queued for later at once. As an apply's
    429 it still does not count towards the gateway-wide breaker."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    orig = ark.transport.handler
    deletes = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and "modifyCards" in req.url.path:
            return httpx.Response(403, json={"detail": "You do not have permission."})
        if req.method == "DELETE":
            deletes["n"] += 1
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "5"})
        return orig(req)

    ark.transport.handler = handler
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    row = await _finished(h, p["proposal_id"])
    assert row["state"] == "failed" and row["result"]["error"] == "forbidden", row
    assert quick_waits == [] and deletes["n"] == 1, (quick_waits, deletes)
    assert row["result"]["backup_note"] == decks_mod.BACKUP_LATER, row["result"]
    assert len(h.db.queued_backup_deletes()) == 1
    assert h.app.state.gateway.decks.client.pacer._failures == 0


def _kept(h, pid: str, snap_id: str, copy: int, ark: FakeArchidekt, error: str) -> None:
    """The queued delete was given up: the copy stays on Archidekt, the snapshot still records
    it, the proposal's message is back to the copy staying in the backup folder, and the audit
    says why."""
    assert h.db.queued_backup_deletes() == []
    assert copy in ark.decks and ark.deleted == []
    assert h.db.get_snapshot(snap_id, SUB)["backup_deck_id"] == str(copy)
    row = h.db.get_proposal(pid, SUB)
    assert row["result"]["backup_note"] == decks_mod.BACKUP_KEPT, row["result"]
    assert decks_mod.BACKUP_KEPT in row["result"]["detail"]
    assert decks_mod.BACKUP_LATER not in row["result"]["detail"]
    [gave] = _events(h, "backup_delete_given_up")
    assert gave["detail"]["error"] == error and gave["detail"]["backup_deck_id"] == str(copy), gave


@pytest.mark.usefixtures("quick_waits")
@pytest.mark.parametrize("who", ["amy", "alice"])
async def test_a_relink_drops_the_members_queued_deletes_and_keeps_the_copies(stack: Stack, who: str) -> None:
    """A queued delete does not record which Archidekt account made the copy, so after a
    relink (to another account, or the same one: any relink, the conservative choice) a 404 could
    no longer be read as "already gone". The relink drops the member's queued deletes; the copies
    stay in the backup folder, with the old sentence and an audit row."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    pid, result, seen = await _stop(h, ark, token)
    copy, snap_id = int(result["backup_deck_id"]), result["snapshot_id"]
    _calm(h, ark, seen)
    from .test_decks_and_proxy import Browser

    b = Browser(h)
    try:
        await b.login()
        r = await b.link(who, f"pw-{who}")
        assert r.status_code == 303, r.text
    finally:
        await b.aclose()
    assert svc.status(SUB)["archidekt_username"] == who
    _kept(h, pid, snap_id, copy, ark, "relinked")
    _due_now(h)
    assert await svc.delete_one_queued_backup() is None
    assert copy in ark.decks and ark.deleted == []


@pytest.mark.usefixtures("quick_waits")
@pytest.mark.parametrize("change", ["renamed", "described", "moved", "edited"])
async def test_a_copy_changed_since_it_was_made_is_kept(stack: Stack, change: str) -> None:
    """The later delete reads the copy once first and deletes it only while it is still
    in the backup folder with the name and description the gateway gave it, and Archidekt's
    updated time is no later than shortly after its delete was queued. Otherwise it gives up and
    keeps the copy."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    pid, result, seen = await _stop(h, ark, token)
    copy, snap_id = int(result["backup_deck_id"]), result["snapshot_id"]
    _calm(h, ark, seen)
    if change == "renamed":
        ark.decks[copy]["name"] = "My own brew now"
    elif change == "described":
        ark.decks[copy]["description"] = "Kept on purpose."
    elif change == "moved":
        ark.deck_folder[copy] = 1000 + 77  # the account's root folder
    else:
        later = time.gmtime(time.time() + decks_mod.BACKUP_UNCHANGED_SLACK + 600)
        ark.decks[copy]["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", later)
    _due_now(h)
    assert await svc.delete_one_queued_backup() == {"given_up": snap_id, "error": "copy_changed"}
    _kept(h, pid, snap_id, copy, ark, "copy_changed")


@pytest.mark.usefixtures("quick_waits")
async def test_an_unchanged_copy_is_read_once_then_deleted(stack: Stack) -> None:
    """One read of the copy (an ordinary call), then the delete."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    _pid, result, seen = await _stop(h, ark, token)
    copy, snap_id = int(result["backup_deck_id"]), result["snapshot_id"]
    _calm(h, ark, seen)
    orig = ark.transport.handler
    sent: list[tuple[str, str]] = []

    def count(req: httpx.Request) -> httpx.Response:
        sent.append((req.method, req.url.path))
        return orig(req)

    ark.transport.handler = count
    _due_now(h)
    assert await svc.delete_one_queued_backup() == {"deleted": snap_id}
    assert sent == [("GET", f"/api/decks/{copy}/"), ("DELETE", f"/api/decks/{copy}/")], sent


@pytest.mark.usefixtures("quick_waits")
async def test_an_unexpected_error_is_retried_on_the_backoff_then_given_up(stack: Stack, monkeypatch) -> None:
    """A queued delete whose try raises something unexpected is not left due (where it
    would hold up every other queued delete, one try a minute): it waits 10, 20, then 40 minutes
    like any failed try, and the fourth failure gives up and keeps the copy."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    pid, result, seen = await _stop(h, ark, token)
    copy, snap_id = int(result["backup_deck_id"]), result["snapshot_id"]
    _calm(h, ark, seen)

    async def broken(token: str, deck_id: str) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(svc.client, "delete_deck", broken)
    for attempt, minutes in ((1, 10), (2, 20), (3, 40)):
        _due_now(h)
        now = int(time.time())
        out = await svc.delete_one_queued_backup()
        assert out is not None and out["retry"] == snap_id and out["attempts"] == attempt, out
        [job] = h.db.queued_backup_deletes()
        assert job["attempts"] == attempt and now + minutes * 60 - 1 <= job["due"] <= now + minutes * 60 + 2
        assert await svc.delete_one_queued_backup() is None  # not due again: nothing is held up
    _due_now(h)
    assert await svc.delete_one_queued_backup() == {"given_up": snap_id, "error": "internal"}
    _kept(h, pid, snap_id, copy, ark, "internal")


# -- a deck delete makes no backup copy on Archidekt ---------------------------------------------
@pytest.mark.usefixtures("quick_waits")
async def test_a_deck_delete_stopped_on_a_long_429_makes_no_copy_and_leaves_none_behind(
    stack: Stack,
) -> None:
    """A delete-deck action keeps only the gateway's snapshot: no backup copy is made on
    Archidekt. A stop on Archidekt's waits before the deck was deleted is back to pending with
    the approved sentence and nothing queued; a second approval deletes the deck, and no new
    deck is left behind on Archidekt."""
    from .test_account_actions import _browser_apply

    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_delete_deck", {"deck_id": "42"}))
    pid = p["proposal_id"]
    orig = ark.transport.handler
    seen = {"copies": 0, "busy": True}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path.endswith("/decks/copy/"):
            seen["copies"] += 1
        if seen["busy"] and req.method == "DELETE" and req.url.path.endswith("/decks/42/"):
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "30"})
        return orig(req)

    ark.transport.handler = handler
    before = set(ark.decks)
    await _browser_apply(h, pid)
    row = await _finished(h, pid)
    result = row["result"]
    assert row["state"] == "pending" and 42 in ark.decks and seen["copies"] == 0, row
    detail = result["detail"]
    assert detail.startswith(ark_mod.PATIENCE_SPENT) and "backup copy" not in detail, result
    assert "backup_deck_id" not in result and h.db.queued_backup_deletes() == []
    seen["busy"] = False
    svc.client.pacer._retry_until = 0
    await _browser_apply(h, pid)
    row = await _finished(h, pid)
    assert row["state"] == "applied" and 42 not in ark.decks and seen["copies"] == 0, row
    assert row["result"]["snapshot_id"] and "backup_deck_id" not in row["result"]
    assert set(ark.decks) == before - {42} and h.db.queued_backup_deletes() == []


@pytest.mark.usefixtures("quick_waits")
async def test_a_deck_delete_never_waits_on_the_copy_route(stack: Stack) -> None:
    """The copy route is never asked during a deck delete, so a copy POST that would time out
    cannot hold the delete up or leave an unnamed copy behind."""
    from .test_account_actions import _browser_apply

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_delete_deck", {"deck_id": "42"}))
    pid = p["proposal_id"]
    orig = ark.transport.handler

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path.endswith("/decks/copy/"):
            orig(req)
            raise httpx.ReadTimeout("timed out", request=req)
        return orig(req)

    ark.transport.handler = handler
    before = set(ark.decks)
    await _browser_apply(h, pid)
    row = await _finished(h, pid)
    assert row["state"] == "applied", row
    assert set(ark.decks) == before - {42} and h.db.queued_backup_deletes() == []


# -- the copy's own clock -----------------------------------------------------------------------
def _at(value: str, seconds: float) -> str:
    when = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (when + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.mark.usefixtures("quick_waits")
@pytest.mark.parametrize(
    "case",
    [
        "edited_a_minute_later",
        "created_missing",
        "created_unreadable",
        "created_without_zone",
        "updated_without_zone",
    ],
)
async def test_the_later_delete_reads_the_copys_own_clock(stack: Stack, case: str) -> None:
    """The copy is unchanged only while Archidekt's updatedAt is no later than its own
    createdAt plus the time the gateway's own writes to it took (measured as it made the copy)
    plus BACKUP_UNCHANGED_SLACK: one clock, Archidekt's. An edit a minute after the copy was
    made is kept, though that is within two minutes of the delete being queued. A missing or
    unreadable createdAt keeps the copy. A time with no zone is unreadable."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    pid, result, seen = await _stop(h, ark, token)
    copy, snap_id = int(result["backup_deck_id"]), result["snapshot_id"]
    _calm(h, ark, seen)
    made = ark.decks[copy]
    if case == "edited_a_minute_later":
        made["updatedAt"] = _at(made["createdAt"], 60)
    elif case == "created_missing":
        del made["createdAt"]
    elif case == "created_unreadable":
        made["createdAt"] = "yesterday"
    elif case == "created_without_zone":
        made["createdAt"] = made["createdAt"].removesuffix("Z")
        made["updatedAt"] = made["updatedAt"].removesuffix("Z")
    else:
        made["updatedAt"] = made["updatedAt"].removesuffix("Z")
    _due_now(h)
    assert await svc.delete_one_queued_backup() == {"given_up": snap_id, "error": "copy_changed"}
    _kept(h, pid, snap_id, copy, ark, "copy_changed")


def test_backup_unchanged_uses_the_copys_created_time_and_the_measured_writes() -> None:
    """The bound is createdAt + the gateway's measured writes + the slack, inclusive;
    a second later is a change. Without the measured writes the copy is kept."""
    from mtg_gateway.archidekt import Deck

    expect = {"name": "B", "description": "d", "folder_id": "7", "written_within": 3}
    created = "2026-10-14T10:00:00.000000Z"
    bound = 3 + decks_mod.BACKUP_UNCHANGED_SLACK

    def copy(updated: str, created_at: str = created) -> Deck:
        return Deck(
            id="9",
            name="B",
            owner="alice",
            updated_at=updated,
            cards=[],
            categories=[],
            raw={},
            description="d",
            parent_folder=7,
            created_at=created_at,
        )

    job = {"expect": expect, "queued_at": 0}
    assert decks_mod._backup_as_made(copy(_at(created, bound)), job)
    assert not decks_mod._backup_as_made(copy(_at(created, bound + 1)), job)
    assert decks_mod._backup_as_made(copy("2026-10-14T12:00:01+02:00", "2026-10-14T10:00:00Z"), job)
    old = {"expect": {k: v for k, v in expect.items() if k != "written_within"}, "queued_at": 0}
    assert not decks_mod._backup_as_made(copy(created), old)


def test_a_time_without_a_zone_is_unreadable() -> None:
    """A time with no zone is unreadable."""
    assert decks_mod._iso_time("2026-10-14T10:00:00") is None
    assert decks_mod._iso_time("2026-10-14T10:00:00.123456") is None
    assert decks_mod._iso_time("2026-10-14T10:00:00Z") == decks_mod._iso_time("2026-10-14T12:00:00+02:00")
    assert decks_mod._iso_time("2026-10-14T10:00:00.5Z") is not None


# -- the copy's owner, and a relink inside the same second --------------------------------------
@pytest.mark.usefixtures("quick_waits")
@pytest.mark.parametrize("case", ["other_account", "link_without_id", "copy_without_owner_id"])
async def test_a_copy_not_owned_by_the_linked_account_is_kept(stack: Stack, monkeypatch, case: str) -> None:
    """The later delete deletes the copy only when its owner id is the linked account's
    Archidekt user id as stored on the link. Missing or different: it gives up and keeps it."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    assert h.db.get_link(SUB)["archidekt_user_id"]
    pid, result, seen = await _stop(h, ark, token)
    copy, snap_id = int(result["backup_deck_id"]), result["snapshot_id"]
    _calm(h, ark, seen)
    if case == "copy_without_owner_id":
        read = svc.client.get_deck

        async def no_owner_id(*a, **k):
            return dataclasses.replace(await read(*a, **k), owner_id=None)

        monkeypatch.setattr(svc.client, "get_deck", no_owner_id)
    else:
        with h.db.tx() as c:
            c.execute(
                "UPDATE archidekt_links SET archidekt_user_id = ? WHERE sub = ?",
                ("999999" if case == "other_account" else None, SUB),
            )
    _due_now(h)
    assert await svc.delete_one_queued_backup() == {"given_up": snap_id, "error": "not_owner"}
    _kept(h, pid, snap_id, copy, ark, "not_owner")


@pytest.mark.usefixtures("quick_waits")
async def test_a_relink_in_the_same_second_as_the_read_still_stops_the_delete(
    stack: Stack, monkeypatch
) -> None:
    """A relink while the later delete reads the copy stops it, even when the new link's
    created_at (whole seconds) equals the old one's: the relink dropped the queued delete, and
    the pass checks that it is still queued before it deletes."""
    h, ark = stack.h, stack.ark
    svc = h.app.state.gateway.decks
    token = await linked_user(stack)
    pid, result, seen = await _stop(h, ark, token)
    copy, snap_id = int(result["backup_deck_id"]), result["snapshot_id"]
    _calm(h, ark, seen)
    read = svc.client.get_deck

    async def read_then_relink(*a, **k):
        out = await read(*a, **k)
        link = h.db.get_link(SUB)
        h.db.save_link(
            SUB,
            username=link["archidekt_username"],
            user_id=link["archidekt_user_id"],
            secret_enc=link["secret_enc"],
        )
        with h.db.tx() as c:  # in the same second as the link it replaced
            c.execute("UPDATE archidekt_links SET created_at = ? WHERE sub = ?", (link["created_at"], SUB))
        svc._drop_backup_deletes_on_relink(SUB)
        return out

    monkeypatch.setattr(svc.client, "get_deck", read_then_relink)
    _due_now(h)
    assert await svc.delete_one_queued_backup() == {"relinked": snap_id}
    _kept(h, pid, snap_id, copy, ark, "relinked")
