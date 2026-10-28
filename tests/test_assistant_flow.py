"""0.7.46, how an assistant's changes flow (owner decision C3) and what its tool results say
(audit work package WP7): one waiting draft per deck per app, propose and apply in one call where
the member's mode allows it, a failed apply that changed nothing waits again with Try again,
"Propose the rest" after a partly sent apply, one account-action tool, and results the assistant
reads without guessing."""

from __future__ import annotations

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, structured
from .test_track_a_board import _mode


def _add(name: str, **more: object) -> dict:
    return {"action": "add", "name": name, **more}


async def _propose(h, token: str, changes: list, **more: object) -> dict:
    return structured(
        await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes, **more})
    )


# -- combine: the board note is worked out after the combined ones are replaced (A12) ----------


async def test_combine_lists_only_what_still_waits_beside_it(stack: Stack) -> None:
    """The combine result listed the proposals it had just replaced under waiting_on_deck, with
    overlaps against them, and told the assistant to tidy them up."""
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    one = await _propose(h, token, [_add("Arcane Signet"), {"action": "remove", "name": "Acidic Slime"}])
    two = await _propose(h, token, [_add("Sol Ring")], separate=True)
    three = await _propose(h, token, [_add("Opt")], separate=True)
    out = structured(
        await call(h, token, "combine_proposals", {"proposal_ids": [one["proposal_id"], two["proposal_id"]]})
    )
    assert out["ok"] and sorted(out["replaced"]) == sorted([one["proposal_id"], two["proposal_id"]]), out
    assert [w["proposal_id"] for w in out["waiting_on_deck"]] == [three["proposal_id"]], out
    assert "overlaps" not in out, out
    assert "overlap" not in out["next_step"].lower(), out["next_step"]


# -- one waiting draft per deck per app (C3, question 1) ---------------------------------------


async def test_a_later_request_from_the_same_app_is_added_to_its_waiting_draft(stack: Stack) -> None:
    """A second propose for the same deck from the same app made a second proposal and a second
    card. It now adds to the app's waiting one: one proposal, a new version, both changes."""
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = await _propose(h, token, [_add("Arcane Signet", category="Ramp")])
    second = await _propose(h, token, [_add("Mind Stone")])
    assert second["ok"] and second["proposal_id"] == first["proposal_id"], second
    assert second["added_to"] == first["proposal_id"] and second["version"] == 2
    assert second["note"].startswith("Added to #1, this app's waiting change for this deck"), second
    assert [r["name"] for r in second["rows"]] == ["Arcane Signet", "Mind Stone"]
    assert "now +1 Mind Stone" in second["what_changed"]
    assert h.db.count_pending_proposals("user-1") == 1
    # its card is the one live card: the result that drew the newer card names the higher number
    assert second["card_seq"] == 2
    # a third request for a card the draft already changes takes that card's change's place
    third = await _propose(h, token, [_add("Arcane Signet", quantity=2, category="Ramp")])
    assert third["version"] == 3 and [r["qty"] for r in third["rows"] if r["name"] == "Arcane Signet"] == [2]
    assert [c["name"] for c in third["changes"]] == ["Arcane Signet", "Mind Stone"], third["changes"]


async def test_the_same_request_again_adds_nothing(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = await _propose(h, token, [_add("Opt"), _add("Mind Stone")])
    again = await _propose(h, token, [_add("Mind Stone")])
    assert again["duplicate_of"] == first["proposal_id"] and again["version"] == 1, again
    assert again["note"] == "Already in #1: it holds these changes, so nothing was added."


async def test_a_draft_is_per_app_and_separate_opts_out(stack: Stack) -> None:
    from .test_round4_data import _app_token

    h = stack.h
    await linked_user(stack)
    await _mode(h, "manual")
    claude, _ = await _app_token(h, "Claude")
    chatgpt, _ = await _app_token(h, "ChatGPT")
    a = await _propose(h, claude, [_add("Opt")])
    b = await _propose(h, chatgpt, [_add("Mind Stone")])
    assert b["proposal_id"] != a["proposal_id"] and "added_to" not in b
    c = await _propose(h, claude, [_add("Island")], separate=True)
    assert c["proposal_id"] != a["proposal_id"] and c["version"] == 1 and "added_to" not in c
    # with two drafts of its own, a later request goes to the newest
    d = await _propose(h, claude, [_add("Swamp")])
    assert d["added_to"] == c["proposal_id"], d


async def test_another_kind_of_change_to_the_same_card_is_proposed_on_its_own(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = await _propose(h, token, [{"action": "set_quantity", "name": "Forest", "quantity": 14}])
    moved = await _propose(h, token, [{"action": "set_category", "name": "Forest", "category": "Lands"}])
    assert moved["proposal_id"] != first["proposal_id"] and "added_to" not in moved, moved
    assert "#1 already makes another kind of change to “Forest”" in moved["note"], moved["note"]


async def test_a_draft_the_deck_changed_under_is_left_alone(stack: Stack) -> None:
    """A draft that would now do something else on the deck (changed on Archidekt) is Needs a
    look; a later request is not mixed into it."""
    from .test_track_a_recheck import _edit_elsewhere

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = await _propose(h, token, [{"action": "set_quantity", "name": "Forest", "quantity": 14}])
    _edit_elsewhere(ark, "Forest", 2)
    later = await _propose(h, token, [_add("Opt")])
    assert later["proposal_id"] != first["proposal_id"] and "added_to" not in later
    old = structured(await call(h, token, "proposal_status", {"proposal_id": first["proposal_id"]}))
    assert old["status"] == "needs_look" and old["version"] == 1


async def test_a_full_draft_takes_no_more(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = await _propose(h, token, [_add(f"Card {i}") for i in range(39)])
    more = await _propose(h, token, [_add("Opt"), _add("Island")])
    assert more["proposal_id"] != first["proposal_id"], more
    assert "would pass 40 changes" in more["note"]
    # one that fits still goes in
    fits = await _propose(h, token, [_add("Swamp")])
    assert fits["added_to"] in (first["proposal_id"], more["proposal_id"])


# -- result keys and short answers (A23) -------------------------------------------------------


async def test_results_name_cards_as_the_inputs_do_and_reject_and_withdraw_answer_short(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = await _propose(h, token, [_add("Opt", finish="foil"), {"action": "remove", "name": "Acidic Slime"}])
    full = structured(await call(h, token, "proposal_status", {"proposal_id": p["proposal_id"], "full": True}))
    assert full["changes"] == [
        {"action": "add", "name": "Opt", "quantity": 1, "finish": "foil"},
        {"action": "remove", "name": "Acidic Slime"},
    ], full["changes"]
    assert all("card_name" not in c for c in p["changes"])
    out = structured(await call(h, token, "reject_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] and out["state"] == "rejected" and out["proposal_id"] == p["proposal_id"], out
    assert not {"card_seq", "rows", "changes", "diff", "next_step", "result"} & set(out), sorted(out)
    assert out["closed_by"] == "assistant" and out["closed_at"]
    q = await _propose(h, token, [_add("Island")])
    out = structured(
        await call(h, token, "withdraw_proposal", {"proposal_id": q["proposal_id"], "reason": "not needed"})
    )
    assert out["ok"] and out["state"] == "withdrawn", out
    assert not {"card_seq", "rows", "changes", "diff", "result"} & set(out), sorted(out)
