"""Release 0.7.30, approvals: a press on the chat card sends the status the card showed. A change
held after the card was drawn is refused with nothing sent, and the reply carries the hold, so the
member sees it even when the chat app refused the card's own read first. A press made after the
card showed the hold applies the change, and a card that sends no status works as before."""

from __future__ import annotations

from .conftest import Stack
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import call, linked_user, structured
from .test_round4_data import _app_token
from .track_a_helpers import card_code

SUB = "user-1"


def _add(name: str) -> list[dict]:
    return [{"action": "add", "card_name": name}]


async def _propose(h, token: str, changes: list, **more: object) -> dict:
    p = structured(
        await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes, **more})
    )
    assert p["ok"], p
    return p


def _qty(ark, name: str) -> int:
    cards = ark.decks[42]["cards"]
    return sum(c["quantity"] for c in cards if c["card"]["oracleCard"]["name"].split(" // ")[0] == name)


async def _press(h, token: str, pid: str, code: str, **more: object) -> dict:
    args = {"proposal_id": pid, "approval": code, "decision": "approve", **more}
    return structured(await call(h, token, "confirm_proposal", args))


async def _held_replacement(stack: Stack) -> tuple:
    """Claude's change replaces ChatGPT's; the card code is taken, then the member applies
    ChatGPT's change, so Claude's is held (it would change the deck twice)."""
    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    theirs = await _propose(h, b, _add("Arcane Signet"))
    new = await _propose(h, a, _add("Delver of Secrets"), replaces=[theirs["proposal_id"]])
    code = await card_code(h, a, new["proposal_id"])
    shown = structured(await call(h, a, "get_proposal", {"proposal_id": new["proposal_id"]}))
    assert shown.get("status") in (None, "")
    tb = await card_code(h, b, theirs["proposal_id"])
    assert (await _press(h, b, theirs["proposal_id"], tb))["state"] == "applied"
    assert h.db.get_proposal(new["proposal_id"], SUB)["status"] == "held"
    return h, a, new["proposal_id"], code


async def test_a_hold_set_after_the_card_was_drawn_is_refused_and_shown(stack: Stack) -> None:
    h, a, pid, code = await _held_replacement(stack)
    before = _qty(stack.ark, "Delver of Secrets")
    got = await _press(h, a, pid, code, shown_status="")
    assert got["ok"] is False and got["error"] == "now_held", got
    assert got["state"] == "pending" and got["status"] == "held"
    assert got["status_reason"] == "It would replace #1, which was applied."
    assert got["message"] == (
        "This change is now held, so nothing was sent to Archidekt. It would replace #1, which was "
        "applied. The user can read why on the card and still apply it there."
    )
    # the card's note is drawn again from these: as they are now, not as when it was drawn
    now = structured(await call(h, a, "get_proposal", {"proposal_id": pid}))
    print("replace_note now:", repr(now.get("replace_note")), "press_reason:", repr(now.get("press_reason")))
    assert got["press_reason"] == (now.get("press_reason") or "")
    assert got["replace_note"] == (now.get("replace_note") or "")
    assert got["assistant_may_apply"] is False
    assert _qty(stack.ark, "Delver of Secrets") == before
    assert h.db.get_proposal(pid, SUB)["state"] == "pending"
    # the card now shows the hold: the member's press with that status applies it
    done = await _press(h, a, pid, code, shown_status="held")
    assert done["ok"] and done["state"] == "applied", done
    assert _qty(stack.ark, "Delver of Secrets") == before + 1


async def test_a_card_that_sends_no_status_applies_as_before(stack: Stack) -> None:
    h, a, pid, code = await _held_replacement(stack)
    done = await _press(h, a, pid, code)
    assert done["ok"] and done["state"] == "applied", done


async def test_a_hold_from_a_rejected_first_step_is_refused_too(stack: Stack) -> None:
    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    first = await _propose(h, a, _add("Opt"))
    second = await _propose(h, a, _add("Arcane Signet"), depends_on=[first["proposal_id"]])
    code = await card_code(h, a, second["proposal_id"])
    structured(await call(h, a, "reject_proposal", {"proposal_id": first["proposal_id"]}))
    got = await _press(h, a, second["proposal_id"], code, shown_status="")
    assert got["error"] == "now_held" and got["status_reason"] == "It depends on #1, which you rejected.", got
    assert h.db.get_proposal(second["proposal_id"], SUB)["state"] == "pending"


async def test_an_unchanged_status_applies(stack: Stack) -> None:
    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    p = await _propose(h, a, _add("Opt"))
    code = await card_code(h, a, p["proposal_id"])
    done = await _press(h, a, p["proposal_id"], code, shown_status="")
    assert done["ok"] and done["state"] == "applied", done
