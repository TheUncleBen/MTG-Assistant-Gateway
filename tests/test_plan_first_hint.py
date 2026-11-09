"""A "plan first" hint when one app sends many changes to one deck in a short time (assistant
tool review C-1, 2026-10-28: nine edits in about 90 seconds took a deck from 122 to 100 cards,
and it sat illegal in between). From the third deck edit an app sends to the same deck within
PLAN_WINDOW_SECONDS, the result says to finish the plan and send it as one proposal."""

from __future__ import annotations

from mtg_gateway import decks as decks_module

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, structured


async def _edit(stack: Stack, token: str, card: str, deck: str = "42") -> dict:
    args = {"deck_id": deck, "changes": [{"action": "add", "name": card}], "separate": True}
    out = structured(await call(stack.h, token, "propose_deck_changes", args))
    assert out["ok"], out
    return out


async def test_the_third_quick_edit_of_one_deck_says_plan_first(stack: Stack) -> None:
    token = await linked_user(stack)
    first = await _edit(stack, token, "Sol Ring")
    second = await _edit(stack, token, "Arcane Signet")
    third = await _edit(stack, token, "Command Tower")
    assert "plan_first" not in first and "plan_first" not in second, (first, second)
    hint = third["plan_first"]
    assert "3 changes to this deck" in hint and "one proposal" in hint, hint
    assert "check_deck_changes" in hint and "amend_proposal" in hint, hint


async def test_edits_spread_out_say_nothing(stack: Stack, monkeypatch) -> None:
    token = await linked_user(stack)
    clock = [1000.0]
    monkeypatch.setattr(decks_module, "_plan_clock", lambda: clock[0])
    for card in ("Sol Ring", "Arcane Signet", "Command Tower"):
        out = await _edit(stack, token, card)
        clock[0] += decks_module.PLAN_WINDOW_SECONDS + 1
    assert "plan_first" not in out, out


async def _new_deck(stack: Stack, token: str, name: str) -> dict:
    args = {"name": name, "cards": [{"name": "Sol Ring", "quantity": 1}]}
    out = structured(await call(stack.h, token, "propose_new_deck", args))
    assert out["ok"], out
    return out


async def test_the_third_quick_new_deck_says_check_first_and_amend(stack: Stack) -> None:
    """0.7.47 (Ben's v39 feedback, card spam): an assistant that withdrew its new deck and
    proposed it again under another name, five times in one turn, drew five cards in the chat.
    From the third new deck one app proposes within PLAN_WINDOW_SECONDS, the result says each
    one drew a card and how to change a waiting one instead."""
    token = await linked_user(stack)
    first = await _new_deck(stack, token, "Darksteel")
    await call(stack.h, token, "withdraw_proposal", {"proposal_id": first["proposal_id"], "reason": "redo"})
    second = await _new_deck(stack, token, "Darksteel v2")
    third = await _new_deck(stack, token, "Darksteel Optimized")
    assert "plan_first" not in first and "plan_first" not in second, (first, second)
    hint = third["plan_first"]
    assert "3 new decks" in hint and "card in the user's chat" in hint, hint
    assert "check_new_deck" in hint and "amend_proposal" in hint and "replaces" in hint, hint


async def test_new_decks_spread_out_say_nothing(stack: Stack, monkeypatch) -> None:
    token = await linked_user(stack)
    clock = [1000.0]
    monkeypatch.setattr(decks_module, "_plan_clock", lambda: clock[0])
    for name in ("One", "Two", "Three"):
        out = await _new_deck(stack, token, name)
        clock[0] += decks_module.PLAN_WINDOW_SECONDS + 1
    assert "plan_first" not in out, out
