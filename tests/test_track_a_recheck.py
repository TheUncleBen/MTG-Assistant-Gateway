"""Release 0.7.29, approvals and proposals: a waiting change checked again against the deck as it
is now (Q27g, K1, K2), what auto mode still asks about (O7, O8, G5, Q27j), and the card's
approval code (Q8, G3, G16, T-151)."""

from __future__ import annotations

import time

from mtg_gateway import recheck
from mtg_gateway.approve import APPROVAL_META_KEY

from .conftest import Stack
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import call, linked_user, structured
from .test_round4_data import _app_token
from .track_a_helpers import card_code, press_approve

SUB = "user-1"
FOREST = [{"action": "set_quantity", "card_name": "Forest", "quantity": 14}]


async def _propose(h, token: str, changes: list | None = None, deck: str = "42") -> dict:
    p = structured(
        await call(h, token, "propose_deck_changes", {"deck_id": deck, "changes": changes or FOREST})
    )
    assert p["ok"] and p["state"] == "pending", p
    return p


def _card(ark, name: str) -> dict:
    return next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == name)


def _edit_elsewhere(ark, name: str = "Acidic Slime", by: int = 1) -> None:
    """A change made on archidekt.com: another count, and Archidekt's updated time moves."""
    _card(ark, name)["quantity"] += by
    ark.decks[42]["updatedAt"] = "2026-10-05T00:00:00Z"


async def _get(h, token: str, pid: str) -> dict:
    return structured(await call(h, token, "get_proposal", {"proposal_id": pid}))


# -- K1: worked out again on the deck as it is now ---------------------------------------------------


async def test_a_change_elsewhere_that_leaves_the_rows_alone_still_applies(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = await _propose(h, token)
    _edit_elsewhere(ark)  # another card: the Forest row reads exactly as reviewed
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] and out["state"] == "applied", out
    assert _card(ark, "Forest")["quantity"] == 14 and _card(ark, "Acidic Slime")["quantity"] == 2


async def test_a_change_elsewhere_to_the_same_card_needs_a_look_and_sends_nothing(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = await _propose(h, token)
    _edit_elsewhere(ark, "Forest", 2)  # 15 -> 17 on Archidekt: "15 -> 14" is no longer what happens
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "stale", out
    assert out["state"] == "pending" and out["status"] == "needs_look"
    assert "Needs a look" in out["message"] and "Nothing was sent" in out["message"]
    assert ark.patches == []
    got = await _get(h, token, p["proposal_id"])
    assert got["state"] == "pending" and got["status"] == "needs_look" and got["out_of_date"] is True
    assert got["status_reason"] == (
        "The deck changed. Reviewed: 15 -> 14 Forest. Now it would be: 17 -> 14 Forest."
    ), got["status_reason"]
    assert got["next_step"].startswith("Needs a look:") and got["assistant_may_apply"] is False
    # The deck goes back to how it was: the next check finds it fits again, and it applies.
    _card(ark, "Forest")["quantity"] = 15
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] and out["state"] == "applied", out


async def test_an_apply_checks_the_decks_other_waiting_changes_at_no_cost(stack: Stack) -> None:
    """K2: the verify read of an apply re-checks the deck's other waiting changes."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    first = await _propose(h, token)
    fits = await _propose(h, token, [{"action": "add", "card_name": "Arcane Signet"}])
    clash = await _propose(h, token, [{"action": "set_quantity", "card_name": "Forest", "quantity": 10}])
    reads = len(ark.calls)
    assert structured(await call(h, token, "apply_proposal", {"proposal_id": first["proposal_id"]}))["ok"]
    assert len(ark.calls) > reads
    a, b = await _get(h, token, fits["proposal_id"]), await _get(h, token, clash["proposal_id"])
    assert a["status"] == "updated" and "still does exactly what was reviewed" in a["status_reason"]
    assert b["status"] == "needs_look" and "14 -> 10 Forest" in b["status_reason"], b
    # "Updated" moved its baseline: it applies without another look
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": fits["proposal_id"]}))
    assert out["ok"] and out["state"] == "applied", out


async def test_a_deck_page_view_checks_the_waiting_changes(stack: Stack) -> None:
    from .test_decks_and_proxy import Browser

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = await _propose(h, token)
    _edit_elsewhere(ark, "Forest")
    b = Browser(h)
    try:
        await b.login()
        r = await b.http.get("/decks/42", headers={"Sec-Fetch-Mode": "navigate", "Accept": "text/html"})
        assert r.status_code == 200
    finally:
        await b.aclose()
    assert (await _get(h, token, p["proposal_id"]))["status"] == "needs_look"


async def test_a_deck_deletion_and_a_copy_stay_strict(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    gw = h.app.state.gateway
    clone = structured(await call(h, token, "propose_clone_deck", {"deck_id": "42"}))
    assert clone["ok"], clone
    _edit_elsewhere(ark)  # unrelated to anything the copy "reviews", but a copy copies it all
    deck = await gw.decks.get_deck(SUB, "42")
    row = gw.db.get_proposal(clone["proposal_id"], SUB)
    assert recheck.check(gw.decks, SUB, row, deck) == ("needs_look", recheck.REASON_CHANGED)
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": clone["proposal_id"]}))
    assert out["error"] == "stale" and out["state"] == "pending", out
    assert (await _get(h, token, clone["proposal_id"]))["status_reason"] == (
        "The deck changed on Archidekt since this was made."
    )


async def test_a_details_change_needs_a_look_when_the_deck_already_has_it(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"name": "New name"}})
    )
    _edit_elsewhere(ark)  # cards only: the name row is as reviewed
    gw = h.app.state.gateway
    deck = await gw.decks.get_deck(SUB, "42")
    assert gw.decks.recheck_waiting(SUB, deck) == {p["proposal_id"]: "updated"}
    ark.decks[42]["name"] = "New name"  # renamed by hand meanwhile
    ark.decks[42]["updatedAt"] = "2026-10-06T00:00:00Z"
    deck = await gw.decks.get_deck(SUB, "42")
    assert gw.decks.recheck_waiting(SUB, deck) == {p["proposal_id"]: "needs_look"}
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["error"] == "stale" and out["state"] == "pending" and ark.updates == [], out


async def test_a_details_change_records_where_folder_tags_and_cover_were(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"add_tags": ["budget"]}})
    )
    assert p["ok"], p
    was = p["changes"]["organise"]["was"]
    assert set(was) == {"folder", "tags", "cover"}


async def test_the_deck_sync_records_the_status_with_the_proposal(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    sync = h.app.state.gateway.sync
    sync.opened = lambda sub: None
    token = await linked_user(stack)
    p = await _propose(h, token)
    await sync.check(SUB)
    _edit_elsewhere(ark, "Forest")
    h.app.state.gateway.decks.deck_lists.clear()
    out = await sync.check(SUB)
    assert out["flagged"] == [p["proposal_id"]], out
    # stored, so a restart (a new sync with no memory) still shows it
    row = h.db.get_proposal(p["proposal_id"], SUB)
    assert row["status"] == "needs_look" and row["status_reason"].startswith("rows:")


# -- O7, O8, G5, Q27j: what an assistant may apply itself ----------------------------------------------


async def test_auto_mode_still_asks_about_restores_new_decks_commanders_and_big_edits(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    small = await _propose(h, token)
    assert small["approval_mode"] == "auto" and small["assistant_may_apply"] is True
    big = await _propose(
        h, token, [{"action": "add", "card_name": n} for n in [f"Card {i}" for i in range(21)]]
    )
    assert big["assistant_may_apply"] is False and "21 rows, more than the 20" in big["risk_reason"]
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": big["proposal_id"]}))
    assert (
        out["error"] == "browser_required" and "even in the user's automatic approval mode" in out["message"]
    )
    new = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": "Fresh", "deck_format": "commander", "decklist_text": "1 Sol Ring"},
        )
    )
    assert new["assistant_may_apply"] is False and new["risk_reason"] == "creates a new deck"
    details = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"name": "Auto name"}})
    )
    assert details["risk"] == "high" and details["assistant_may_apply"] is True  # O7: applies in auto
    clone = structured(await call(h, token, "propose_clone_deck", {"deck_id": "42"}))
    assert clone["assistant_may_apply"] is True
    # the big-edit limit is the admin setting (O8)
    h.app.state.gateway.decks.live.set("auto_big_edit_rows", 30, by="admin")
    again = await _get(h, token, big["proposal_id"])
    assert again["assistant_may_apply"] is True


async def test_a_note_changed_by_another_app_is_never_applied_by_an_assistant(stack: Stack) -> None:
    """G5, K7: whatever its rows, in every mode."""
    h = stack.h
    token = await linked_user(stack)
    p = await _propose(h, token)
    assert h.db.amend_proposal(
        p["proposal_id"],
        SUB,
        expect_version=1,
        changes=p["changes"],
        rows=p["rows"],
        diff_text=p["diff"],
        by_client="another-app",
    )
    got = await _get(h, token, p["proposal_id"])
    assert got["assistant_may_apply"] is False and got["risk"] == "high"
    assert got["risk_reason"] == "was changed by another app after it was made" and got["version"] == 2
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["error"] == "browser_required"


async def test_a_held_note_waits_for_the_member_who_may_still_apply_it(stack: Stack) -> None:
    """Q27j, A8 (rec): an assistant never applies a held note; the member's press does."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    base = await _propose(h, token, [{"action": "add", "card_name": "Arcane Signet"}])
    top = await _propose(h, token)
    assert h.db.link_proposals(top["proposal_id"], SUB, "depends_on", base["proposal_id"])
    assert structured(await call(h, token, "reject_proposal", {"proposal_id": base["proposal_id"]}))["ok"]
    assert h.db.set_hold(top["proposal_id"], SUB, f"depends:{base['proposal_id']}:rejected")
    got = await _get(h, token, top["proposal_id"])
    assert got["status"] == "held" and got["assistant_may_apply"] is False
    assert got["status_reason"] == "It depends on #1, which you rejected." and got["next_step"].startswith(
        "Held:"
    )
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": top["proposal_id"]}))
    assert out["error"] == "browser_required" and ark.patches == []
    done = await press_approve(h, token, top["proposal_id"])
    assert done["ok"] and done["state"] == "applied", done


# -- the card's approval code ---------------------------------------------------------------------------


async def test_the_card_draws_buttons_only_in_the_app_that_made_the_note(stack: Stack) -> None:
    """Coordinator check 3: get_proposal in another app drew buttons that always failed with
    invalid_approval. Now that card has no code and sends the member to the review page."""
    h = stack.h
    await linked_user(stack)
    chatgpt, _ = await _app_token(h, "ChatGPT")
    claude, _ = await _app_token(h, "Claude")
    big = [{"action": "add", "card_name": f"Card {i}"} for i in range(21)]  # waits even in auto
    p = structured(await call(h, chatgpt, "propose_deck_changes", {"deck_id": "42", "changes": big}))
    mine = await call(h, chatgpt, "get_proposal", {"proposal_id": p["proposal_id"]})
    assert (mine.get("_meta") or {}).get(APPROVAL_META_KEY) and not structured(mine).get("card_other_app")
    theirs = await call(h, claude, "get_proposal", {"proposal_id": p["proposal_id"]})
    assert not (theirs.get("_meta") or {}).get(APPROVAL_META_KEY)
    assert structured(theirs)["card_other_app"] is True


async def test_a_card_of_an_earlier_version_is_refused_as_replaced(stack: Stack) -> None:
    """G16, K4: an amend makes the old card's code stop working, with the design's words."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    p = await _propose(h, token)
    old = await card_code(h, token, p["proposal_id"])
    assert h.db.amend_proposal(
        p["proposal_id"],
        SUB,
        expect_version=1,
        changes=p["changes"],
        rows=p["rows"],
        diff_text=p["diff"],
        by_client=h.db.get_proposal(p["proposal_id"], SUB)["created_by_client"],
    )
    out = structured(
        await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": old})
    )
    assert (
        out["ok"] is False
        and out["error"] == "changed"
        and out["message"].startswith("Replaced by a newer version.")
    ), out
    assert ark.patches == []
    assert (await press_approve(h, token, p["proposal_id"]))["state"] == "applied"


async def test_asking_the_assistant_to_apply_a_note_that_needs_a_press_answers_with_the_card(
    stack: Stack,
) -> None:
    """T-151 (coordinator check 2): apply_proposal of a change the member must approve used to
    answer only with a link. Now its answer is the proposal, with the card's code."""
    h = stack.h
    token = await linked_user(stack)
    big = [{"action": "add", "card_name": f"Card {i}"} for i in range(21)]
    p = await _propose(h, token, big)
    res = await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    out = structured(res)
    assert out["ok"] is False and out["error"] == "browser_required"
    assert out["proposal_id"] == p["proposal_id"] and out["state"] == "pending" and out["rows"]
    assert (res.get("_meta") or {}).get(APPROVAL_META_KEY)


async def test_social_actions_are_approved_on_the_card_and_never_by_the_assistant(stack: Stack) -> None:
    """Q8, Q9, G3, K13: a like gets Approve on the card; the assistant's apply is refused; a deck
    deletion keeps the review page only."""
    h, ark = stack.h, stack.ark
    ark.private.discard(43)  # amy's deck is public: someone else's deck to like
    token = await linked_user(stack)
    like = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "like"}))
    assert like["ok"], like
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": like["proposal_id"]}))
    assert out["error"] == "browser_required" and "proposal card" in out["message"] and ark.votes == {}
    done = await press_approve(h, token, like["proposal_id"])
    assert done["ok"] and done["state"] == "applied", done
    gone = structured(await call(h, token, "propose_delete_deck", {"deck_id": "42"}))
    res = await call(h, token, "get_proposal", {"proposal_id": gone["proposal_id"]})
    assert not (res.get("_meta") or {}).get(APPROVAL_META_KEY)


# -- Q130, A12: the expiry ------------------------------------------------------------------------------


async def test_a_new_note_waits_seven_days_by_default(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    p = await _propose(h, token)
    assert abs(p["expires_at"] - (int(time.time()) + 7 * 86400)) < 60
