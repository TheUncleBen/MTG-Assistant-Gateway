"""0.7.34, smarter approval cards: check_deck_changes checks a planned deck edit before it is
proposed, with no card in the chat, nothing saved and no call to Archidekt, and says whether to
fix it, leave it (already waiting) or amend the assistant's own waiting proposal instead."""

from __future__ import annotations

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, mcp_token, structured
from .test_in_chat_approve import _tools
from .test_track_a_board import SUB, _change, _mode, _propose


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
    assert out["amend"]["proposal_id"] == first["proposal_id"] and out["amend"]["version"] == 1
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
    assert "every propose, amend and combine call puts a card in the chat, a refused one too" in skill
    assert "check a deck edit with `check_deck_changes` first" in skill
    block = CHATGPT.read_text(encoding="utf-8").split("```text\n", 1)[1]
    assert (
        "amend_proposal. Each propose/amend makes a card: check_deck_changes first, follow its suggest."
        in block
    )
    told = server_instructions("https://gateway.example.com")
    assert "each propose, amend or combine call puts a card in the chat, a refused one too" in told
    assert "check_deck_changes first" in told


def test_a_cards_code_is_for_the_version_the_result_shows() -> None:
    """Hardening: the code drawn with a result is for the version that result shows, never a later
    one read from the database between the two (which its rows do not show)."""
    from types import SimpleNamespace

    from mtg_gateway.approve import card_approval
    from mtg_gateway.decks import current_client

    asked: list[int | None] = []
    row = {"id": "p-1", "created_by_client": "c1", "version": 2, "created_at": 1}
    decks = SimpleNamespace(
        db=SimpleNamespace(get_proposal=lambda pid, sub: row),
        card_may_approve=lambda r: True,
        approval_for=lambda sub, r, version=None: asked.append(version) or f"code-{version}",
    )
    shown = {"ok": True, "state": "pending", "proposal_id": "p-1", "version": 1}
    token = current_client.set("c1")
    try:
        assert card_approval(shown, decks=decks, sub="u", in_chat=True) == ("code-1", False)
    finally:
        current_client.reset(token)
    assert asked == [1]


async def test_the_amend_suggestion_carries_the_whole_list_and_prefers_the_one_sharing_cards(
    stack: Stack,
) -> None:
    """amend_proposal replaces the whole list, so the suggestion hands back the waiting one's own
    changes (in the spelling the tool takes), and it picks the own waiting edit that shares cards."""
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    await _propose(h, token, _change("Brainstorm"))
    second = await _propose(h, token, [*_change("Opt"), *_change("Ponder")])
    out = await _check(h, token, _change("Opt", action="remove"))
    assert out["suggest"] == "amend" and out["amend"]["proposal_id"] == second["proposal_id"]
    assert [c["name"] for c in out["amend"]["changes"]] == ["Opt", "Ponder"]
    assert "card_name" not in out["amend"]["changes"][0]
    # following it: the list and the plan together, in one amend
    plan = [*out["amend"]["changes"], {"action": "add", "name": "Preordain"}]
    amend = {"proposal_id": second["proposal_id"], "version": out["amend"]["version"], "changes": plan}
    done = structured(await call(h, token, "amend_proposal", amend))
    assert done["ok"] and {r["name"] for r in done["rows"]} == {"Opt", "Ponder", "Preordain"}


async def test_a_held_proposal_is_never_offered_to_amend(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    mine = await _propose(h, token, _change("Opt"))
    assert h.db.set_hold(mine["proposal_id"], SUB, "depends:x")
    out = await _check(h, token, [*_change("Opt"), *_change("Ponder")])
    assert out["suggest"] == "propose" and "amend" not in out


async def test_at_the_waiting_limit_an_amend_or_a_repeat_is_still_suggested(
    stack: Stack, monkeypatch
) -> None:
    """The limit stops a new proposal only: an amend and an exact repeat are not refused by it."""
    from mtg_gateway import decks as decks_mod

    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    await _propose(h, token, _change("Opt"))
    monkeypatch.setattr(decks_mod, "MAX_PENDING_PROPOSALS", 1)
    out = await _check(h, token, [*_change("Opt"), *_change("Ponder")])
    assert out["suggest"] == "amend" and out["problems"] == []
    assert (await _check(h, token, _change("Opt")))["suggest"] == "already_waiting"
    other = await mcp_token(h)  # another app: only a new proposal would do, and it has no room
    full = await _check(h, other, _change("Ponder"))
    assert full["suggest"] == "fix" and full["problems"][0]["error"] == "rate_limited"


async def test_a_name_no_magic_card_has_is_a_problem(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    h.app.state.gateway.scan.names.set_names(["Island", "Swamp"])
    out = await _check(h, token, [*_change("Islend"), *_change("Swamp")])
    assert out["suggest"] == "fix" and out["problems"][0]["unknown_cards"] == ["Islend"]


async def test_another_apps_change_on_the_same_cards_is_named_in_the_next_step(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    await _propose(h, token, _change("Opt"))
    other = await mcp_token(h)
    out = await _check(h, other, [*_change("Opt"), *_change("Ponder")])
    assert out["suggest"] == "propose" and "another app's waiting change shares cards" in out["next_step"]
    url = await _check(h, other, _change("Ponder"), deck="https://archidekt.com/decks/42/sample")
    assert url["deck_id"] == "42" and len(url["waiting_on_deck"]) == 1
