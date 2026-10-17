"""0.7.25 (R-153): the automatic Archidekt -> gateway deck sync (sync.py). Archidekt is the master
copy; the gateway checks it when the app opens and on a timer, read-only, behind every member's
own work, and flags a waiting proposal out of date as soon as it sees the deck change. It must
never break or overwrite what a member or an assistant does from the gateway (R1-R6)."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from mtg_gateway import decks as decks_mod
from mtg_gateway import sync as sync_mod
from mtg_gateway.sync import DeckSync

from .conftest import AUTO, FakeIdP, Harness, Stack, StackOptions, make_settings, running
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, _client, call, linked_user, structured

SUB = "user-1"
CHANGE = [{"action": "set_quantity", "card_name": "Forest", "quantity": 3}]


@pytest.fixture
def stack_options(monkeypatch) -> StackOptions:
    """The shared AUTO stack, with the assistant's apply answering "applying" after 0.2 s. The sync's
    idle wait is 0.3 s, looked at every 10 ms."""
    monkeypatch.setattr(decks_mod, "APPLY_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(sync_mod, "IDLE_WAIT", 0.3)
    monkeypatch.setattr(sync_mod, "IDLE_POLL", 0.01)
    return AUTO


def _sync(stack: Stack, *, quiet: bool = True) -> DeckSync:
    sync = stack.h.app.state.gateway.sync
    assert isinstance(sync, DeckSync) and sync.interval == 30 * 60  # on by default, every 30 minutes
    if quiet:
        sync.opened = lambda sub: None  # this test drives the checks itself
    return sync


def _reads(ark: FakeArchidekt, deck_id: int = 42) -> int:
    return sum(1 for m, p in ark.calls if m == "GET" and p == f"/api/decks/{deck_id}/")


def _edit_elsewhere(ark: FakeArchidekt, when: str = "2026-10-05T00:00:00Z") -> None:
    """A change made on archidekt.com (or in another app), as Archidekt would report it: to the
    Forest row the waiting proposals change, so they no longer do what was reviewed (0.7.29, K1:
    a change to another card leaves them "updated", test_track_a_recheck)."""
    forest = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Forest")
    forest["quantity"] += 1
    ark.decks[42]["updatedAt"] = when


def _later(stack: Stack) -> None:
    """Time passes: the member's cached deck list (fresh for 90 s) has aged out."""
    stack.h.app.state.gateway.decks.deck_lists.clear()


async def _propose(h, token: str) -> str:
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": CHANGE}))
    assert p["ok"] and p["state"] == "pending", p
    return p["proposal_id"]


def _hold(ark: FakeArchidekt, when) -> asyncio.Event:
    """Hold Archidekt's answer to the first request ``when`` matches until the event is set."""
    orig = ark.transport.handler
    release, held = asyncio.Event(), {"x": False}

    async def slow(req: httpx.Request) -> httpx.Response:
        if not held["x"] and when(req):
            held["x"] = True
            await release.wait()
        return orig(req)

    ark.transport.handler = slow
    return release


async def _until(pred, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.01)


async def test_an_outside_change_flags_the_waiting_proposal_at_once(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    pid = await _propose(h, token)
    first = await sync.check(SUB)
    assert first["flagged"] == [] and first["reads"] == 1, first
    assert "out_of_date" not in structured(await call(h, token, "get_proposal", {"proposal_id": pid}))

    _edit_elsewhere(ark)
    _later(stack)
    out = await sync.check(SUB)
    assert out["flagged"] == [pid], out
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["state"] == "pending" and got["out_of_date"] is True and got["out_of_date_since"]
    assert got["next_step"].startswith("Needs a look: the deck changed after this proposal was made")
    listed = structured(await call(h, token, "list_my_proposals"))["proposals"]
    assert next(p for p in listed if p["id"] == pid)["out_of_date"] is True

    b = Browser(h)
    try:
        await b.login()
        page = await b.http.get(f"/proposals/{pid}")
        assert "Needs a look: the deck changed after this change was made" in page.text
        assert "Needs a look</span>" in (await b.http.get("/proposals")).text
    finally:
        await b.aclose()

    # Apply decides exactly as before 0.7.25: it re-reads the deck, refuses, and sends nothing.
    patches = len(ark.patches)
    res = structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
    assert res["ok"] is False and res["error"] == "stale", res
    assert len(ark.patches) == patches


async def test_an_unchanged_deck_costs_one_list_request_and_no_deck_read(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    await _propose(h, token)
    await sync.check(SUB)
    reads = _reads(ark)
    h.app.state.gateway.decks.deck_lists.clear()  # the cached list aged out: one listing request
    lists = sum(1 for m, p in ark.calls if p == "/api/decks/v3/")
    _later(stack)
    out = await sync.check(SUB)
    assert out["reads"] == 0 and out["flagged"] == [], out
    assert _reads(ark) == reads
    assert sum(1 for m, p in ark.calls if p == "/api/decks/v3/") == lists + 1


async def test_the_check_is_read_only_and_never_touches_proposals_or_snapshots(stack: Stack) -> None:
    """R1: only GETs go out; proposals (but for the status of a waiting one, 0.7.29), snapshots
    and the undo history stay exactly as they were."""
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    applied = structured(await call(h, token, "apply_proposal", {"proposal_id": await _propose(h, token)}))
    assert applied["state"] == "applied", applied
    waiting = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "set_quantity", "card_name": "Forest", "quantity": 2}]},
        )
    )["proposal_id"]
    before_props = {p["id"]: p["state"] for p in h.db.list_proposals(SUB)}
    before_snaps = [s["snapshot_id"] for s in h.db.list_snapshots(SUB)]
    calls = len(ark.calls)
    _edit_elsewhere(ark)
    _later(stack)
    out = await sync.check(SUB)
    assert out["flagged"] == [waiting], out
    assert all(m == "GET" for m, _p in ark.calls[calls:]), ark.calls[calls:]
    assert {p["id"]: p["state"] for p in h.db.list_proposals(SUB)} == before_props
    assert [s["snapshot_id"] for s in h.db.list_snapshots(SUB)] == before_snaps
    link = h.db.get_link(SUB)
    assert link is not None and link["status"] == "active"


async def test_the_check_waits_behind_a_members_request_then_skips(stack: Stack) -> None:
    """R2: while a member's Archidekt call is under way the check sends nothing and gives up for
    this round; the member's call finishes as it would have."""
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    await _propose(h, token)
    h.app.state.gateway.decks.deck_lists.clear()
    release = _hold(ark, lambda r: r.url.path == "/api/decks/42/")
    member = asyncio.create_task(call(h, token, "get_deck", {"deck_ref": "42"}))
    await _until(lambda: h.app.state.gateway.decks.archidekt_busy())
    calls = len(ark.calls)
    _later(stack)
    out = await sync.check(SUB)
    assert out == {"skipped": "busy"}, out
    assert len(ark.calls) == calls  # the check sent nothing
    release.set()
    got = structured(await member)
    assert got["ok"] and got["name"] == "Sample Commander Deck", got


async def test_a_check_beside_an_apply_leaves_it_to_finish_exactly_as_today(stack: Stack) -> None:
    """R3/R6: an apply under way on the deck is never read halfway; it ends applied, and its own
    write does not flag it."""
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    pid = await _propose(h, token)
    release = _hold(ark, lambda r: r.method == "PATCH")
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
    assert out["state"] == "applying", out
    calls = len(ark.calls)
    assert await sync.check(SUB) == {"skipped": "busy"}
    assert len(ark.calls) == calls
    # even with the pacer idle, a deck being applied is left alone
    decks = h.app.state.gateway.decks
    assert decks.deck_busy(SUB, "42")
    release.set()
    await _until(lambda: h.db.get_proposal(pid, SUB)["state"] == "applied")
    assert sync.out_of_date(pid) is None
    done = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert done["state"] == "applied" and "out_of_date" not in done


async def test_a_busy_deck_is_skipped_and_compared_on_the_next_check(stack: Stack, monkeypatch) -> None:
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    pid = await _propose(h, token)
    _edit_elsewhere(ark)
    decks = h.app.state.gateway.decks
    monkeypatch.setattr(decks, "deck_busy", lambda sub, deck_id: True)
    _later(stack)
    out = await sync.check(SUB)
    assert out["reads"] == 0 and out["flagged"] == [], out
    monkeypatch.undo()
    monkeypatch.setattr(sync_mod, "IDLE_WAIT", 0.3)
    _later(stack)
    assert (await sync.check(SUB))["flagged"] == [pid]


async def test_restore_and_details_proposals_are_flagged_too(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    applied = structured(await call(h, token, "apply_proposal", {"proposal_id": await _propose(h, token)}))
    restore = structured(
        await call(h, token, "propose_restore_snapshot", {"snapshot_id": applied["snapshot_id"]})
    )
    details = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"name": "Renamed"}})
    )
    assert restore["ok"] and details["ok"], (restore, details)
    _edit_elsewhere(ark)
    _later(stack)
    out = await sync.check(SUB)
    # 0.7.29 (K1): the restore would now put Forest back from 4, not 3; the rename still fits
    assert out["flagged"] == [restore["proposal_id"]] and out["updated"] == [details["proposal_id"]], out


async def test_the_gateways_own_apply_checks_another_waiting_proposal(stack: Stack) -> None:
    """Two proposals for one deck: applying the first changes the deck. Its verify read checks
    the second at once (0.7.29, K2): it still does what was reviewed, so it is "updated", and the
    next check has nothing to say. The applied one is not flagged."""
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    first = await _propose(h, token)
    second = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "set_quantity", "card_name": "Island", "quantity": 2}]},
        )
    )["proposal_id"]
    await sync.check(SUB)
    ark.decks[42]["updatedAt"] = "2026-10-06T00:00:00Z"  # Archidekt moves updatedAt on the write
    orig = ark.apply_patch

    def patch_and_bump(deck, entries):
        orig(deck, entries)
        deck["updatedAt"] = "2026-10-06T00:00:01Z"

    ark.apply_patch = patch_and_bump  # type: ignore[method-assign]
    ark.decks[42]["updatedAt"] = "2026-10-01T10:00:00Z"
    assert structured(await call(h, token, "apply_proposal", {"proposal_id": first}))["state"] == "applied"
    assert h.db.get_proposal(second, SUB)["status"] == "updated"
    _later(stack)
    out = await sync.check(SUB)
    assert out["flagged"] == [] and out["updated"] == [], out
    assert sync.out_of_date(first) is None and sync.out_of_date(second) is None


async def test_a_failed_check_is_quiet_and_the_member_carries_on(stack: Stack) -> None:
    """R5: Archidekt failing ends the check with nothing changed; the member's next request works."""
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    pid = await _propose(h, token)
    h.app.state.gateway.decks.deck_lists.clear()
    ark.inject = [(503, None)]  # the listing fails (this test client does not retry)
    _later(stack)
    out = await sync.check(SUB)
    assert out == {"error": "unavailable"}, out
    assert sync.out_of_date(pid) is None and h.db.get_proposal(pid, SUB)["state"] == "pending"
    got = structured(await call(h, token, "list_my_decks"))
    assert got["ok"] and [d["id"] for d in got["decks"]] == ["42"], got


async def test_a_failing_archidekt_costs_the_breaker_one_failure_with_production_retries(
    stack: Stack,
) -> None:
    """R5 with the shipped retry setting (two retries for a member's read): the sync sends each
    request once, so a check adds one failure to the shared breaker, and while the breaker or a
    Retry-After pause is on it sends nothing at all."""
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    client = h.app.state.gateway.decks.client
    client.retries, client.backoff_base = 2, 0.0
    token = await linked_user(stack)
    await _propose(h, token)
    _later(stack)
    sent = len(ark.calls)
    ark.inject = [(503, None)] * 3
    assert await sync.check(SUB) == {"error": "unavailable"}
    assert len(ark.calls) == sent + 1 and client.pacer._failures == 1
    ark.inject = []
    client.pacer._open_until = time.monotonic() + 60  # the breaker is open
    sent = len(ark.calls)
    assert await sync.check(SUB) == {"skipped": "busy"} and len(ark.calls) == sent
    client.pacer._open_until = 0.0
    # a member's own read still gets the retries
    ark.inject = [(503, None)]
    got = structured(await call(h, token, "list_my_decks"))
    assert got["ok"], got


async def test_a_failed_refresh_of_an_older_list_still_ends_the_check(stack: Stack) -> None:
    """A deck list cached a few minutes ago (no longer fresh) is not used to hide a failed read:
    the check reports the error, so a round stops there and adds one failure to the breaker."""
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    client = h.app.state.gateway.decks.client
    client.retries, client.backoff_base = 2, 0.0
    token = await linked_user(stack)
    await _propose(h, token)
    lists = h.app.state.gateway.decks.deck_lists
    await sync.check(SUB)  # fills the member's list
    for sub, (at, wall, value) in list(lists._entries.items()):
        lists._entries[sub] = (at - 200, wall, value)  # 200 s old: past fresh, not yet dropped
    assert lists.peek(SUB)[1] == "stale"
    sent, failures = len(ark.calls), client.pacer._failures
    ark.inject = [(503, None)] * 3
    assert await sync.check(SUB) == {"error": "unavailable"}
    assert len(ark.calls) == sent + 1 and client.pacer._failures == failures + 1
    ark.inject = []


async def test_a_rejected_session_ends_the_check_but_never_unlinks(stack: Stack) -> None:
    """The sync is nobody using the link: a 401 (and a rejected refresh) ends the check and leaves
    the link for the member's own next request to judge."""
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    pid = await _propose(h, token)
    _later(stack)
    ark.inject = [(401, None), (401, None)]  # the listing, then the refresh
    assert await sync.check(SUB) == {"error": "not_linked"}
    assert h.db.get_link(SUB) is not None and h.db.get_proposal(pid, SUB)["state"] == "pending"
    assert not h.db.audit_seen("archidekt_link_expired")


async def test_a_new_proposal_on_an_unchanged_deck_is_not_read_again(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    await _propose(h, token)
    await sync.check(SUB)
    later = await _propose(h, token)
    reads = _reads(ark)
    _later(stack)
    out = await sync.check(SUB)
    assert out["reads"] == 0 and _reads(ark) == reads and sync.out_of_date(later) is None


async def test_opening_the_app_starts_one_check(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    sync = _sync(stack, quiet=False)
    token = await linked_user(stack)
    pid = await _propose(h, token)
    sync._checked.clear()
    _edit_elsewhere(ark)
    b = Browser(h)
    try:
        await b.login()
        await b.http.get("/decks")
        await _until(lambda: sync.out_of_date(pid) is not None)
        started = sync._checked[SUB]
        await b.http.get("/proposals")  # within OPEN_GAP: no second check
        await asyncio.sleep(0.05)
        assert sync._checked[SUB] == started and SUB not in sync._tasks
    finally:
        await b.aclose()


async def test_the_timed_round_checks_only_members_with_waiting_proposals(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    await linked_user(stack, {"sub": "user-2", "email": "amy@example.com", "name": "Amy"}, "amy", "pw-amy")
    assert await sync.run_round() == 0  # nobody waits on anything: no request at all
    pid = await _propose(h, token)
    _edit_elsewhere(ark)
    _later(stack)
    assert await sync.run_round() == 1
    assert sync.out_of_date(pid) is not None


async def test_a_closed_proposal_is_never_out_of_date(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    sync = _sync(stack)
    token = await linked_user(stack)
    pid = await _propose(h, token)
    _edit_elsewhere(ark)
    await sync.check(SUB)
    assert sync.out_of_date(pid)
    structured(await call(h, token, "reject_proposal", {"proposal_id": pid}))
    await sync.check(SUB)
    assert sync.out_of_date(pid) is None


async def test_sync_off_with_zero_minutes(tmp_path, idp: FakeIdP) -> None:
    ark = FakeArchidekt()
    settings = make_settings(tmp_path, deck_sync_minutes=0, archidekt_base="https://ark.test/api")
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h:
        assert h.app.state.gateway.sync is None
        assert h.app.state.gateway.decks.out_of_date is None


def test_deck_sync_minutes_setting(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_gateway.config import ConfigError, load_settings

    from .test_backup_and_config import _write_secrets

    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.delenv("MTG_DECK_SYNC_MINUTES", raising=False)
    assert load_settings().deck_sync_minutes == 30
    monkeypatch.setenv("MTG_DECK_SYNC_MINUTES", "0")
    assert load_settings().deck_sync_minutes == 0
    monkeypatch.setenv("MTG_DECK_SYNC_MINUTES", "-5")
    with pytest.raises(ConfigError):
        load_settings()
