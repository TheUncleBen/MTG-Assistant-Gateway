"""An apply that Archidekt's waits stopped before it changed anything (Q78): the proposal is
pending again. The unused backup copy whose delete Archidekt refused for a while is deleted
later by the gateway itself (Q77): queued in the database, one delete per pass, never while the
client is paused, with a backoff and a limit, and only after the same safety checks as at once."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from mcp import Client

from mtg_gateway import archidekt as ark_mod
from mtg_gateway import decks as decks_mod
from mtg_gateway.archidekt import ArchidektClient, Pacer
from mtg_gateway.history_view import build_events
from mtg_gateway.mf_proxy import MysticForgeProxy
from mtg_gateway.scan.scryfall import ScryfallClient
from mtg_gateway.sync import DeckSync

from .conftest import FakeIdP, Harness, make_settings, running
from .fake_archidekt import FakeArchidekt
from .fake_scryfall import FakeScryfall
from .test_background_apply import LATER, NOTHING_CHANGED, THREE, _finished
from .test_browse_collection import Stack as CollectionStack
from .test_browse_collection import linked
from .test_decks_and_proxy import Stack, _client, call, fake_mystic_forge, linked_user, mcp_token, structured

SUB = "user-1"


@pytest.fixture
async def stack(tmp_path, idp: FakeIdP, monkeypatch):
    monkeypatch.setattr(decks_mod, "APPLY_WAIT_SECONDS", 0.2)
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, approval_mode_default="auto", archidekt_base="https://ark.test/api"
    )
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h:
        yield Stack(h, ark)


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
        c.execute("UPDATE job_runs SET at = 0 WHERE name LIKE 'backup\\_delete:%' ESCAPE '\\'")


def _events(h, event: str) -> list[dict]:
    return [e for e in h.db.audit_recent(200, sub=SUB) if e["event"] == event]


@pytest.mark.usefixtures("quick_waits")
async def test_a_queued_delete_waits_for_the_pause_then_runs_once_it_ends(stack: Stack) -> None:
    """Q77: queued for when the pause ends plus a margin; nothing is sent while the client is
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
        c.execute("UPDATE job_runs SET at = ?", (int(time.time()) + 3600,))
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
    """Q77: Archidekt answers 503: the next try waits 10, 20, then 40 minutes; the fourth failure
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
        "backup folder. The proposal is still pending, so it can be approved again in a few minutes."
    )
    assert row["result"]["detail"] == f"{ark_mod.PATIENCE_SPENT} {kept}", row["result"]["detail"]
    assert row["result"]["backup_note"] == decks_mod.BACKUP_KEPT
    assert h.db.get_snapshot(result["snapshot_id"], SUB)["backup_deck_id"] == str(copy)
    assert await svc.delete_one_queued_backup() is None  # given up: never tried again


@pytest.mark.usefixtures("quick_waits")
async def test_a_429_on_a_later_delete_waits_out_the_pause_and_counts_for_the_breaker(
    stack: Stack, monkeypatch
) -> None:
    """Q62, Q77: outside an apply the later delete is a normal call: its 429 counts towards the
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
    assert svc.client.pacer._failures == failures + 1  # counted, as Q62 wants outside an apply
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
    """Q77: before it deletes, the pass checks the copy as the at-once delete does: the snapshot
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
    """Q77, Q78: the stopped proposal is approved again later. The re-apply is re-checked, takes a
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
    text = {e["summary"] for e in events}
    assert any(t.endswith(" just before an apply that stopped before it changed anything.") for t in text)
    assert any(t.endswith(" just before a change was applied.") for t in text)
    applied = [e for e in h.db.audit_recent(200, sub=SUB) if e["event"] == "proposal_applied"]
    stopped = [e for e in h.db.audit_recent(200, sub=SUB) if e["event"] == "proposal_apply_stopped"]
    assert len(applied) == 1 and len(stopped) == 1 and stopped[0]["detail"]["state"] == "pending"


@pytest.mark.usefixtures("quick_waits")
async def test_a_stopped_proposal_is_never_applied_again_by_itself(stack: Stack) -> None:
    """Q78: pending again, but nothing re-applies it: not the deck sync, not the later deletes,
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
    """Q77: the queued delete is kept in the database, so a new gateway on the same database
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
    """Q77: a failed apply (here Archidekt refuses the card write outright) deletes its unused copy
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
    """Q78: a collection proposal whose first card went in before Archidekt's waits ran out
    changed something, so it is not pending again (applying it again would add the card twice),
    and the message says your collection is partly changed."""
    ark, sf = FakeArchidekt(), FakeScryfall()
    settings = make_settings(tmp_path, writes_enabled=True, archidekt_base="https://ark.test/api")
    client = ArchidektClient(settings.archidekt_base, "test-agent", Pacer(0.0), http=ark.client())
    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(fake_mystic_forge()))
    async with running(Harness(settings, idp, archidekt=client, mf_proxy=proxy)) as h:
        h.app.state.gateway.scan.scryfall = ScryfallClient(
            "https://scryfall.test", min_interval=0.0, http=sf.client()
        )
        cs = CollectionStack(h, ark, sf)
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
            await b.http.post(f"/api/v1/proposals/{p['proposal_id']}/apply", headers={"X-CSRF-Token": csrf})
            row = await _finished(h, p["proposal_id"])
        finally:
            await b.aclose()
    result = row["result"]
    assert len(ark.collection_posts) == 1 and row["state"] == "failed", row
    assert result["sent_entries"] == 1 and result["of_entries"] == 2, result
    assert result["detail"] == (
        f"{ark_mod.PATIENCE_SPENT} 1 of 2 collection changes had already been sent, so your collection "
        "is partly changed. Try the rest again in a few minutes with a new proposal."
    ), result["detail"]


def test_the_wording_is_the_approved_text() -> None:
    assert decks_mod.BACKUP_LATER == (
        "The unused backup copy will be deleted from the backup folder by itself once Archidekt allows it."
    )
    assert LATER.startswith("Nothing was changed on Archidekt. " + decks_mod.BACKUP_LATER)


async def test_the_delete_at_once_never_waits_inside_the_apply(
    stack: Stack, quick_waits: list[float]
) -> None:
    """Q77 ("no extra waiting in the apply"): when the delete just after a failed apply meets a
    429, the apply does not wait it out; the delete is queued for later at once. As an apply's
    429 it still does not count towards the gateway-wide breaker (Q62)."""
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
