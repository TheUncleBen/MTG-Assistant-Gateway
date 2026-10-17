"""Release 0.7.29, approvals and proposals: the pin board the assistant reads and tidies (BOARD
design; Q27j-Q27m, O9, G6, K5, K10, A1, A4, A5, A12)."""

from __future__ import annotations

import dataclasses
import time

from mtg_gateway import board

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, structured
from .test_round4_data import _app_token

SUB = "user-1"


def _change(card: str, action: str = "add", **more: object) -> list[dict]:
    return [{"action": action, "card_name": card, **more}]


async def _propose(h, token: str, changes: list, **more: object) -> dict:
    p = structured(
        await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes, **more})
    )
    assert p["ok"], p
    return p


async def _mode(h, mode: str) -> None:
    from .test_approval_modes import _set_mode

    await _set_mode(h, mode)


async def test_the_board_lists_every_waiting_note_oldest_first_with_its_number(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    made = [await _propose(h, token, _change(f"Card {i}")) for i in range(22)]
    assert made[-1]["board_no"] == 22
    out = structured(await call(h, token, "list_my_proposals", {"pending_only": True, "oldest_first": True}))
    assert [p["board_no"] for p in out["proposals"]] == list(range(1, 23))  # K10: no cap of 20
    first = out["proposals"][0]
    assert first["own"] is True and first["version"] == 1 and first["cards"] == ["Card 0"]
    assert first["status"] is None and first["links"] == []
    newest = structured(await call(h, token, "list_my_proposals", {"pending_only": True}))["proposals"]
    assert newest[0]["board_no"] == 22
    # G6: from 20 on the deck, a hint to tidy up, never a refusal
    assert "hint" in made[-1] and "22 changes waiting" in made[-1]["hint"]
    assert "hint" not in made[17]
    other_deck = structured(
        await call(h, token, "list_my_proposals", {"pending_only": True, "deck_id": "43"})
    )
    assert other_deck["proposals"] == []


async def test_a_new_request_is_told_where_it_overlaps_a_waiting_note(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    art = await _propose(h, token, _change("Sol Ring", "set_finish", finish="Foil"))
    gone = await _propose(h, token, _change("Sol Ring", "remove"))
    assert gone["overlaps"] == ["Sol Ring: #1 changes its finish; this request removes it."], gone
    assert [w["board_no"] for w in gone["waiting_on_deck"]] == [1]
    assert art["proposal_id"] == gone["waiting_on_deck"][0]["proposal_id"]


async def test_an_exact_repeat_from_the_same_app_returns_the_waiting_note(stack: Stack) -> None:
    """Q27k, A5 (rec): only exact duplicates, and only against the same app's notes."""
    h = stack.h
    await linked_user(stack)
    await _mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    first = await _propose(h, a, _change("Opt"))
    again = await _propose(h, a, _change("Opt"))
    assert again["proposal_id"] == first["proposal_id"] and again["duplicate_of"] == first["proposal_id"]
    assert again["note"].startswith("Already waiting as #1")
    other = await _propose(h, b, _change("Opt"))
    assert other["proposal_id"] != first["proposal_id"] and "duplicate_of" not in other
    assert other["overlaps"] == ["Opt: #1 adds 1; this request adds 1."]
    assert h.db.count_pending_proposals(SUB) == 2
    # one that needs a look is not a duplicate: asking again makes it fresh from the deck as it is
    h.db.rebase_proposal(
        first["proposal_id"],
        SUB,
        was=h.db.get_proposal(first["proposal_id"], SUB)["baseline_fingerprint"],
        fingerprint="x",
        status="needs_look",
        reason="changed",
    )
    fresh = await _propose(h, a, _change("Opt"))
    assert fresh["proposal_id"] != first["proposal_id"] and "duplicate_of" not in fresh


async def test_amend_keeps_the_number_writes_what_changed_and_restarts_the_expiry(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = await _propose(h, token, _change("Sol Ring", "set_finish", finish="Foil"))
    with h.db.tx() as c:  # an older expiry, to see it start again
        c.execute(
            "UPDATE proposals SET expires_at = ? WHERE id = ?", (int(time.time()) + 60, p["proposal_id"])
        )
    out = structured(
        await call(
            h,
            token,
            "amend_proposal",
            {"proposal_id": p["proposal_id"], "version": 1, "changes": _change("Sol Ring", "remove")},
        )
    )
    assert out["ok"] and out["version"] == 2 and out["board_no"] == 1, out
    assert out["diff"] == "-1 Sol Ring" and out["expires_at"] > time.time() + 6 * 86400
    note = h.db.get_proposal(p["proposal_id"], SUB)["version_note"]
    assert note.startswith("now -1 Sol Ring; no longer"), note
    line = structured(await call(h, token, "list_my_proposals", {"pending_only": True}))["proposals"][0]
    assert line["what_changed"] == note and line["changed_by"]
    # an amend of a version already replaced is refused with the design's words
    stale = structured(
        await call(
            h,
            token,
            "amend_proposal",
            {"proposal_id": p["proposal_id"], "version": 1, "changes": _change("Opt")},
        )
    )
    assert stale["error"] == "changed"
    assert stale["message"].startswith("There is a newer version of this change")


async def test_amend_at_the_version_cap_tells_the_assistant_to_replace(stack: Stack, monkeypatch) -> None:
    """K5, C12: the cap refuses with versions_full; the assistant makes a new linked note."""
    from mtg_gateway import db as db_module

    monkeypatch.setattr(db_module, "MAX_PROPOSAL_VERSIONS", 1)
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = await _propose(h, token, _change("Opt"))
    args = {"proposal_id": p["proposal_id"], "changes": _change("Ponder")}
    assert structured(await call(h, token, "amend_proposal", {**args, "version": 1}))["ok"]
    full = structured(await call(h, token, "amend_proposal", {**args, "version": 2}))
    assert full["error"] == "versions_full" and "replaces" in full["message"], full
    new = await _propose(h, token, _change("Brainstorm"), replaces=[p["proposal_id"]])
    assert new["replaced"] == [p["proposal_id"]]
    assert h.db.get_proposal(p["proposal_id"], SUB)["state"] == "replaced"


async def test_a_replaced_note_comes_back_when_its_replacement_is_rejected(stack: Stack) -> None:
    """Q27l "Keep linked"."""
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    old = await _propose(h, token, _change("Opt"))
    new = await _propose(h, token, _change("Ponder"), replaces=[old["proposal_id"]])
    gone = h.db.get_proposal(old["proposal_id"], SUB)
    assert gone["state"] == "replaced" and gone["status_reason"] == "replaced by #2"
    links = structured(await call(h, token, "get_proposal", {"proposal_id": new["proposal_id"]}))
    assert links["state"] == "pending"
    assert structured(await call(h, token, "reject_proposal", {"proposal_id": new["proposal_id"]}))["ok"]
    back = h.db.get_proposal(old["proposal_id"], SUB)
    assert back["state"] == "pending" and back["board_no"] == 1


async def test_withdraw_only_the_apps_own_note_and_its_dependents_are_held(stack: Stack) -> None:
    """O9 (only its own), Q27j and K6 (a hold, lifted when the note comes back)."""
    h = stack.h
    await linked_user(stack)
    await _mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    base = await _propose(h, a, _change("Opt"))
    top = await _propose(h, a, _change("Ponder"), depends_on=[base["proposal_id"]])
    refused = structured(
        await call(h, b, "withdraw_proposal", {"proposal_id": base["proposal_id"], "reason": "not mine"})
    )
    assert refused["error"] == "other_client"
    out = structured(
        await call(
            h, a, "withdraw_proposal", {"proposal_id": base["proposal_id"], "reason": "card removed in #5"}
        )
    )
    assert out["ok"] and out["state"] == "withdrawn", out
    held = structured(await call(h, a, "get_proposal", {"proposal_id": top["proposal_id"]}))
    assert held["status"] == "held" and held["status_reason"] == "It depends on #1, which was withdrawn."
    h.app.state.gateway.decks.bring_back(SUB, base["proposal_id"])
    assert h.db.get_proposal(top["proposal_id"], SUB)["status"] is None


async def test_combine_makes_one_note_in_order_and_marks_the_rest_replaced(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    ids = [(await _propose(h, token, _change(n)))["proposal_id"] for n in ("Opt", "Ponder", "Brainstorm")]
    out = structured(await call(h, token, "combine_proposals", {"proposal_ids": ids[::-1]}))
    assert out["ok"] and [c["card_name"] for c in out["changes"]] == ["Opt", "Ponder", "Brainstorm"], out
    assert sorted(out["diff"].splitlines()) == ["+1 Brainstorm", "+1 Opt", "+1 Ponder"]
    assert sorted(out["replaced"]) == sorted(ids)
    assert all(h.db.get_proposal(i, SUB)["state"] == "replaced" for i in ids)
    one = structured(await call(h, token, "combine_proposals", {"proposal_ids": [out["proposal_id"]]}))
    assert one["error"] == "invalid"


def test_overlap_lines_name_both_sides() -> None:
    waiting = [{"id": "a", "rows": [{"kind": "change", "name": "Forest", "before": 15, "after": 14}]}]
    rows = [{"kind": "remove", "name": "forest", "qty": 15}]
    assert board.overlaps(waiting, rows, {"a": 3}) == [
        "forest: #3 changes its count from 15 to 14; this request removes it."
    ]
    assert board.what_changed(
        [{"kind": "add", "name": "Opt", "qty": 1}], [{"kind": "add", "name": "Opt", "qty": 1}]
    ) == ("the same changes")


async def test_every_tool_that_changes_something_is_refused_to_a_read_only_token(stack: Stack) -> None:
    """Review of 0.7.29's own tools: amend, withdraw, combine and the card's Apply all change what
    waits (or Archidekt), so a read-only token (mtg.read) is refused them like every other tool
    whose readOnlyHint is false."""
    import json

    from mtg_gateway.app import WRITE_TOOLS

    from .test_decks_and_proxy import sse_json

    h = stack.h
    token = await linked_user(stack)
    r = await h.mcp(token, "tools/list", {}, rid=7)
    tools = sse_json(r)["result"]["tools"]
    writes = {t["name"] for t in tools if (t.get("annotations") or {}).get("readOnlyHint") is False}
    assert {"amend_proposal", "withdraw_proposal", "combine_proposals"} <= writes
    assert writes <= WRITE_TOOLS, writes - WRITE_TOOLS
    assert "confirm_waiting" in WRITE_TOOLS
    await _mode(h, "manual")
    p = await _propose(h, token, _change("Opt"))
    reader, _ = await _app_token(h, "reader", scope="mtg.read")
    for tool, args in (
        ("amend_proposal", {"proposal_id": p["proposal_id"], "version": 1, "changes": _change("Island")}),
        ("withdraw_proposal", {"proposal_id": p["proposal_id"], "reason": "x"}),
        ("combine_proposals", {"proposal_ids": [p["proposal_id"], p["proposal_id"]]}),
    ):
        res = await call(h, reader, tool, args)
        assert res.get("isError") and "insufficient_scope" in json.dumps(res), (tool, res)
    assert h.db.get_proposal(p["proposal_id"], SUB)["version"] == 1


async def test_approval_settings_changed_on_the_settings_page_apply_at_once(stack: Stack) -> None:
    """Step 0's live settings: the approval values an admin stores on the Settings page reach the
    next proposal, the account page and the mode without a restart."""
    from .test_approval_modes import _set_mode

    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    decks = h.app.state.gateway.decks
    # the stack's own values count as the deployment's (R-208 lock); this test stands for a stack
    # that leaves the approval settings to the Settings page
    decks.live.settings = dataclasses.replace(decks.live.settings, env_set=frozenset())
    two = [{"action": "add", "card_name": "Opt"}, {"action": "add", "card_name": "Swamp"}]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": two}))
    assert p["assistant_may_apply"] is True, p
    decks.live.set("auto_big_edit_rows", 1, by="admin-test")  # O8: more than 1 row waits in auto
    p = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert p["assistant_may_apply"] is False and "changes 2 rows" in p["risk_reason"], p
    decks.live.set("approval_mode_max", "semi", by="admin-test")  # the cap, read now
    assert decks.mode_of(SUB) == "semi"
    decks.live.set("auto_apply_max_rows", 1, by="admin-test")
    p = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert p["approval_mode"] == "semi" and p["risk"] == "high" and p["assistant_may_apply"] is False
    # a member who has not chosen follows the default as it is now
    assert h.db.set_approval_mode(SUB, None)
    decks.live.set("approval_mode_default", "manual", by="admin-test")
    assert decks.mode_of(SUB) == "manual"
    decks.live.set("approval_mode_default", "semi", by="admin-test")
    assert decks.mode_of(SUB) == "semi"
