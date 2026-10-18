"""Release 0.7.29: an amend and an apply of the same waiting note racing each other. Whichever
claims the note first wins; the other is refused and nothing it would have done reaches
Archidekt, so the deck gets exactly the version that was approved."""

from __future__ import annotations

import asyncio

import httpx

from .conftest import Stack
from .fake_archidekt import FakeArchidekt
from .test_account_actions import _browser_apply
from .test_decks_and_proxy import call, linked_user, structured
from .test_track_a_board import _change, _mode, _propose

SUB = "user-1"


def _hold_next_deck_read(ark: FakeArchidekt) -> tuple[asyncio.Event, asyncio.Event]:
    """Hold Archidekt's answer to the next read of deck 42: (reached, release)."""
    orig = ark.transport.handler
    reached, release, done = asyncio.Event(), asyncio.Event(), {"x": False}

    async def slow(req: httpx.Request) -> httpx.Response:
        if not done["x"] and req.method == "GET" and req.url.path == "/api/decks/42/":
            done["x"] = True
            reached.set()
            await release.wait()
        return orig(req)

    ark.transport.handler = slow
    return reached, release


def _names(ark: FakeArchidekt) -> set[str]:
    return {c["card"]["oracleCard"]["name"] for c in ark.decks[42]["cards"]}


async def _settled(h, pid: str) -> dict:
    for _ in range(200):
        row = h.db.get_proposal(pid, SUB)
        if row["state"] not in ("pending", "applying"):
            return row
        await asyncio.sleep(0.02)
    raise AssertionError("the apply did not finish")


async def test_an_apply_that_claims_first_beats_an_amend_in_flight(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    pid = (await _propose(h, token, _change("Opt")))["proposal_id"]
    reached, release = _hold_next_deck_read(ark)
    amend = asyncio.create_task(
        call(h, token, "amend_proposal", {"proposal_id": pid, "version": 1, "changes": _change("Ponder")})
    )
    await asyncio.wait_for(reached.wait(), 5)  # the amend is reading the deck
    out = await _browser_apply(h, pid)
    # the press claimed the note (it may still be running, behind the amend's Archidekt read)
    assert out.get("state") in ("applying", "applied"), out
    release.set()
    lost = structured(await amend)
    assert lost["ok"] is False, lost
    row = await _settled(h, pid)
    assert row["state"] == "applied" and row["version"] == 1
    assert "Opt" in _names(ark) and "Ponder" not in _names(ark)


async def test_an_amend_during_an_apply_is_refused_and_the_approved_version_lands(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    pid = (await _propose(h, token, _change("Opt")))["proposal_id"]
    reached, release = _hold_next_deck_read(ark)
    apply = asyncio.create_task(_browser_apply(h, pid))
    await asyncio.wait_for(reached.wait(), 5)  # the apply claimed the note and is reading the deck
    assert h.db.get_proposal(pid, SUB)["state"] == "applying"
    lost = structured(
        await call(
            h, token, "amend_proposal", {"proposal_id": pid, "version": 1, "changes": _change("Ponder")}
        )
    )
    assert lost["ok"] is False, lost
    release.set()
    await apply
    row = await _settled(h, pid)
    assert row["state"] == "applied" and row["version"] == 1
    assert "Opt" in _names(ark) and "Ponder" not in _names(ark)


async def test_an_amend_that_lands_first_makes_the_older_press_refused(stack: Stack) -> None:
    """The member's page showed version 1; the note was amended to version 2 before the press."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    pid = (await _propose(h, token, _change("Opt")))["proposal_id"]
    from .test_decks_and_proxy import Browser

    b = Browser(h)
    try:
        await b.login()
        url = f"/proposals/{pid}"
        form = {**await b.review_form(url), "version": "1"}  # the page as it was: version 1
        done = structured(
            await call(
                h, token, "amend_proposal", {"proposal_id": pid, "version": 1, "changes": _change("Ponder")}
            )
        )
        assert done["ok"] and done["version"] == 2, done
        r = await b.http.post(url, data={**form, "action": "apply"})
        assert r.status_code == 303 and "?err=" in r.headers["location"], r.headers
    finally:
        await b.aclose()
    row = h.db.get_proposal(pid, SUB)
    assert row["state"] == "pending" and row["version"] == 2
    assert "Opt" not in _names(ark) and "Ponder" not in _names(ark)
