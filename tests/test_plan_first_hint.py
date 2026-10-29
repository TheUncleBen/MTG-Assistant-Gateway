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
