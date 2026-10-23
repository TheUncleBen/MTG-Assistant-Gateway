"""Contest entries (parity release 2, REQUESTS.md section 8): entering replaces the deck's
description with the summary, as Archidekt's own form does, after a snapshot; withdrawing deletes
the entry. Both only on the member's own press."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

from mtg_gateway.archidekt import rich_text

from .conftest import Stack
from .test_account_actions import _browser_apply
from .test_decks_and_proxy import call, linked_user, structured


async def _propose(stack: Stack, token: str, **args: object) -> dict:
    return structured(await call(stack.h, token, "propose_contest_entry", {"deck_id": "42", **args}))


async def test_entering_rewrites_the_description_after_a_snapshot_and_withdrawing_removes_it(
    stack: Stack,
) -> None:
    token = await linked_user(stack)
    stack.ark.decks[42]["description"] = "My old words"
    p = await _propose(
        stack,
        token,
        contest="https://archidekt.com/contest/discussion/880",
        summary="Simic ramp,\nbig sea monsters",
    )
    assert p["ok"] is True, p
    assert "in the contest (thread 880)" in p["diff"]
    assert stack.ark.account.entries == {}
    refused = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert refused["ok"] is False
    done = await _browser_apply(stack.h, p["proposal_id"])
    assert done["state"] == "applied" and done["result"]["verified"] is True, done
    assert [e["deck"]["id"] for e in stack.ark.account.entries[880]] == [42]
    assert rich_text(stack.ark.decks[42]["description"]).strip() == "Simic ramp,\nbig sea monsters"
    snap = stack.h.app.state.gateway.db._conn.execute(
        "SELECT deck_json FROM snapshots WHERE id = ?", (done["result"]["snapshot_id"],)
    ).fetchone()
    assert snap is not None and "My old words" in snap[0]
    again = await _propose(stack, token, contest="880", summary="x")
    assert again["ok"] is False and "entered in that contest already" in again["message"]
    w = await _propose(stack, token, contest="880", entry_action="withdraw")
    assert w["ok"] is True, w
    out = await _browser_apply(stack.h, w["proposal_id"])
    assert out["state"] == "applied" and out["result"]["verified"] is True, out
    assert stack.ark.account.entries[880] == []
    none = await _propose(stack, token, contest="880", entry_action="withdraw")
    assert none["ok"] is False and "no entry" in none["message"]


async def test_an_entry_needs_a_summary_a_contest_and_the_members_own_deck(stack: Stack) -> None:
    token = await linked_user(stack)
    assert "needs a summary" in (await _propose(stack, token, contest="880"))["message"]
    assert "contest's id" in (await _propose(stack, token, contest="soon", summary="x"))["message"]
    other = structured(
        await call(
            stack.h, token, "propose_contest_entry", {"deck_id": "43", "contest": "880", "summary": "x"}
        )
    )
    assert other["ok"] is False
