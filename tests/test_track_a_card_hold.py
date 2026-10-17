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


async def test_a_held_change_held_for_a_new_cause_records_it_shows_it_and_refuses_an_old_card(
    stack: Stack,
) -> None:
    """Claude's #3 depends on its #1 and replaces ChatGPT's #2. The member rejects #1 (held), then
    applies #2: #3 is now also held because applying it would change the deck twice. That cause is
    recorded beside the first, shown on the card and the review page, and a card that showed only
    the first cause is refused."""
    from .test_decks_and_proxy import Browser

    h, ark = stack.h, stack.ark
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    first = await _propose(h, a, _add("Opt"))
    theirs = await _propose(h, b, _add("Arcane Signet"))
    new = await _propose(
        h,
        a,
        _add("Delver of Secrets"),
        depends_on=[first["proposal_id"]],
        replaces=[theirs["proposal_id"]],
    )
    pid = new["proposal_id"]
    code = await card_code(h, a, pid)
    structured(await call(h, a, "reject_proposal", {"proposal_id": first["proposal_id"]}))
    shown = structured(await call(h, a, "get_proposal", {"proposal_id": pid}))
    assert shown["status"] == "held" and shown["status_reason"] == "It depends on #1, which you rejected."
    tb = await card_code(h, b, theirs["proposal_id"])
    assert (await _press(h, b, theirs["proposal_id"], tb))["state"] == "applied"
    now = structured(await call(h, a, "get_proposal", {"proposal_id": pid}))
    assert now["status_reason"] == (
        "It depends on #1, which you rejected. It would replace #2, which was applied."
    ), now
    assert now["press_reason"] == "would replace #2, which was applied"
    assert now["status_at"] >= shown["status_at"]
    before = _qty(ark, "Delver of Secrets")
    got = await _press(h, a, pid, code, shown_status="held", shown_status_at=shown["status_at"] - 1)
    assert got["error"] == "now_held" and got["status_reason"] == now["status_reason"], got
    assert _qty(ark, "Delver of Secrets") == before
    # the review page says both causes
    br = Browser(h)
    try:
        await br.login()
        page = (await br.http.get(f"/proposals/{pid}")).text
    finally:
        await br.aclose()
    assert (
        "<dt>Held</dt><dd>It depends on #1, which you rejected. It would replace #2, which was applied.</dd>"
    ) in page
    # a card showing both causes applies it (the member's own press)
    done = await _press(h, a, pid, code, shown_status="held", shown_status_at=now["status_at"])
    assert done["ok"] and done["state"] == "applied", done


async def test_a_held_change_records_each_cause_once(stack: Stack) -> None:
    """Each cause is recorded once, newest last; a new state of the same first step takes the
    place of its earlier one."""
    from mtg_gateway.board import CAUSES_SEP, add_hold

    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    p = await _propose(h, a, _add("Opt"))
    add_hold(h.db, SUB, p["proposal_id"], "depends:x:failed")
    add_hold(h.db, SUB, p["proposal_id"], "replaces:y:applied")
    add_hold(h.db, SUB, p["proposal_id"], "depends:x:rejected")  # the same first step, a new state
    row = h.db.get_proposal(p["proposal_id"], SUB)
    assert row["status_reason"].split(CAUSES_SEP) == ["replaces:y:applied", "depends:x:rejected"]


async def test_a_new_cause_in_the_same_second_still_moves_the_hold_time(stack: Stack, monkeypatch) -> None:
    """Hold times are whole seconds: a cause added in the same second as the one a card showed
    still moves the time forward, so that card's press is refused (both ways a cause is added)."""
    import time as _time

    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    first = await _propose(h, a, _add("Opt"))
    theirs = await _propose(h, b, _add("Arcane Signet"))
    new = await _propose(
        h, a, _add("Delver of Secrets"), depends_on=[first["proposal_id"]], replaces=[theirs["proposal_id"]]
    )
    pid = new["proposal_id"]
    code = await card_code(h, a, pid)
    frozen = float(int(_time.time()))
    monkeypatch.setattr(_time, "time", lambda: frozen)
    structured(await call(h, a, "reject_proposal", {"proposal_id": first["proposal_id"]}))
    shown = h.db.get_proposal(pid, SUB)
    assert shown["status"] == "held"
    # the replaced change applied without its own links being settled: the lazy hold adds it
    with h.db.tx() as c:
        c.execute("UPDATE proposals SET state = 'applied' WHERE id = ?", (theirs["proposal_id"],))
    h.db.hold_blocked(SUB)
    now = h.db.get_proposal(pid, SUB)
    assert "replaces:" in now["status_reason"] and now["status_at"] > shown["status_at"], now
    monkeypatch.undo()
    before = _qty(stack.ark, "Delver of Secrets")
    got = await _press(h, a, pid, code, shown_status="held", shown_status_at=shown["status_at"])
    assert got["error"] == "now_held", got
    assert _qty(stack.ark, "Delver of Secrets") == before


async def test_a_cause_added_in_the_same_second_moves_the_hold_time(stack: Stack, monkeypatch) -> None:
    import time as _time

    from mtg_gateway.board import add_hold

    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    p = await _propose(h, a, _add("Opt"))
    frozen = float(int(_time.time()))
    monkeypatch.setattr(_time, "time", lambda: frozen)
    add_hold(h.db, SUB, p["proposal_id"], "depends:x:failed")
    one = h.db.get_proposal(p["proposal_id"], SUB)["status_at"]
    add_hold(h.db, SUB, p["proposal_id"], "replaces:y:applied")
    assert h.db.get_proposal(p["proposal_id"], SUB)["status_at"] > one
