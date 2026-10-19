"""0.7.34, smarter approval cards: check_deck_changes checks a planned deck edit before it is
proposed, with no card in the chat, nothing saved and no call to Archidekt, and says whether to
fix it, leave it (already waiting) or amend the assistant's own waiting proposal instead."""

from __future__ import annotations

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, mcp_token, structured
from .test_in_chat_approve import _tools
from .test_track_a_board import _change, _mode, _propose


async def _check(h, token: str, changes: list, deck: str = "42") -> dict:
    out = structured(await call(h, token, "check_deck_changes", {"deck_id": deck, "changes": changes}))
    assert out["ok"], out
    return out


async def _pending(h, token: str) -> list[dict]:
    return structured(await call(h, token, "list_my_proposals", {"pending_only": True}))["proposals"]


async def test_the_check_draws_no_card_and_saves_and_asks_nothing(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    tools = await _tools(h, token)
    assert "ui" not in (tools["check_deck_changes"].get("_meta") or {})  # never a card
    assert tools["check_deck_changes"]["annotations"]["readOnlyHint"] is True
    before = len(stack.ark.calls)
    out = await _check(h, token, _change("Opt"))
    assert out["suggest"] == "propose" and out["problems"] == [] and out["waiting_on_deck"] == []
    assert out["changes"] == 1
    assert len(stack.ark.calls) == before  # Archidekt was not asked
    assert await _pending(h, token) == []  # nothing saved


async def test_a_plan_touching_the_apps_own_waiting_proposal_is_told_to_amend_it(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = await _propose(h, token, _change("Opt"))
    out = await _check(h, token, [*_change("Opt"), *_change("Ponder")])
    assert out["suggest"] == "amend"
    assert out["amend"] == {"proposal_id": first["proposal_id"], "version": 1}
    assert out["waiting_on_deck"][0]["shares_cards"] == ["Opt"]
    assert out["overlaps"] == [f"Opt: #{first['board_no']} changes it too."]
    assert "amend_proposal" in out["next_step"]
    # an exact repeat: already waiting, nothing to do
    same = await _check(h, token, _change("Opt"))
    assert same["suggest"] == "already_waiting" and same["duplicate_of"] == first["proposal_id"]
    # another app's waiting proposal is listed, never offered to amend
    other = await mcp_token(h)
    theirs = await _check(h, other, [*_change("Opt"), *_change("Brainstorm")])
    assert theirs["suggest"] == "propose" and "amend" not in theirs
    assert theirs["waiting_on_deck"][0]["own"] is False


async def test_a_plan_a_proposal_would_refuse_comes_back_as_problems(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    out = await _check(h, token, _change("Opt", action="set_quantity"))  # no quantity
    assert out["suggest"] == "fix" and out["problems"][0]["error"] == "invalid"
    assert await _pending(h, token) == []


async def test_the_cards_read_after_an_amend_carries_the_new_versions_code(stack: Stack) -> None:
    """What an older card relies on to follow an amend: get_proposal (the card's own read) carries
    the new version's code, which confirm_proposal takes, while the old one is refused as changed."""
    from mtg_gateway.approve import APPROVAL_META_KEY

    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    res = await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": _change("Opt")})
    pid, old = structured(res)["proposal_id"], res["_meta"][APPROVAL_META_KEY]
    amend = {"proposal_id": pid, "version": 1, "changes": _change("Ponder")}
    amended = structured(await call(h, token, "amend_proposal", amend))
    assert amended["ok"] and amended["version"] == 2
    read = await call(h, token, "get_proposal", {"proposal_id": pid})
    assert structured(read)["version"] == 2
    new = read["_meta"][APPROVAL_META_KEY]
    assert new != old
    stale = structured(
        await call(h, token, "confirm_proposal", {"proposal_id": pid, "approval": old, "decision": "reject"})
    )
    assert stale["ok"] is False and stale["error"] == "changed"
    done = structured(
        await call(h, token, "confirm_proposal", {"proposal_id": pid, "approval": new, "decision": "reject"})
    )
    assert done["ok"] and done["state"] == "rejected"


def test_every_guide_says_each_call_draws_a_card_and_to_check_first() -> None:
    from mtg_gateway.app import server_instructions

    from .test_skill_docs import CHATGPT, SKILL_DIR

    skill = " ".join((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split())
    assert "Every propose, amend and combine call puts an approval card in the chat" in skill
    assert "check a deck edit with `check_deck_changes` first" in skill
    block = CHATGPT.read_text(encoding="utf-8").split("```text\n", 1)[1]
    assert "amend_proposal. check_deck_changes first." in block
    told = server_instructions("https://gateway.example.com")
    assert "Each propose, amend or combine call puts an approval card in the chat" in told
    assert "check_deck_changes first" in told
