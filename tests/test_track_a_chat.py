"""Release 0.7.29, approvals and proposals in the chat: the waiting changes card
(show_waiting_changes, Q134 A6), its Apply all (confirm_waiting, get_apply_result) and the
proposal card's line about the other changes waiting on its deck (Q134 A3)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mtg_gateway.app import WRITE_TOOLS
from mtg_gateway.approve import APPROVAL_SET_META_KEY, APPROVALS_META_KEY
from mtg_gateway.cards import WAITING_CARD_URI

from .conftest import FakeIdP, Stack, StackOptions, open_stack, sse_json, stack_with
from .test_decks_and_proxy import call, linked_user, mcp_token, structured
from .test_in_chat_approve import _tools, _writes

MANUAL = StackOptions(settings={"writes_enabled": True, "approval_mode_default": "manual"})
stack_options = stack_with(MANUAL)


@pytest.fixture
async def no_card(tmp_path, idp: FakeIdP):
    async with open_stack(
        tmp_path, idp, MANUAL.but(settings={**MANUAL.settings, "apply_in_chat": False})
    ) as st:
        yield st


def _add(name: str) -> list[dict]:
    return [{"action": "add", "card_name": name}]


async def _propose(h, token: str, name: str) -> dict:
    res = await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": _add(name)})
    assert structured(res)["ok"], structured(res)
    return res


def _qty(ark, name: str) -> int:
    return sum(c["quantity"] for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == name)


async def test_the_waiting_card_and_its_buttons_tools(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    tools = await _tools(h, token)
    meta = tools["show_waiting_changes"]["_meta"]
    assert meta["ui"]["resourceUri"] == WAITING_CARD_URI
    for name in ("confirm_waiting", "get_apply_result"):
        assert tools[name]["_meta"]["ui"] == {"visibility": ["app"]}, name
        assert tools[name]["_meta"]["openai/visibility"] == "private", name
    assert "confirm_waiting" in WRITE_TOOLS and "show_waiting_changes" not in WRITE_TOOLS
    r = await h.mcp(token, "resources/read", {"uri": WAITING_CARD_URI}, rid=4)
    content = sse_json(r)["result"]["contents"][0]
    page = content["text"]
    assert page.startswith("<!DOCTYPE html>") and "ui/initialize" in page
    assert "confirm_waiting" in page and "get_apply_result" in page and "confirm_proposal" in page
    assert "/*@BASE@*/" not in page and "/*@BRIDGE@*/" not in page
    assert "<script src=" not in page


async def test_own_changes_get_codes_and_another_apps_only_a_link(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    other = await mcp_token(h)  # a second app of the same member
    a = structured(await _propose(h, token, "Opt"))["proposal_id"]
    b = structured(await _propose(h, token, "Delver of Secrets"))["proposal_id"]
    c = structured(await _propose(h, other, "Swamp"))["proposal_id"]
    res = await call(h, token, "show_waiting_changes", {"deck_id": "42"})
    sc = structured(res)
    assert sc["ok"] and sc["count"] == 3 and sc["own_count"] == 2
    assert [x["proposal_id"] for x in sc["waiting"]] == [a, b, c]  # oldest first
    assert sc["review_all_url"].endswith("/proposals#deck-42")
    assert len(sc["other_apps"]) == 1 and sc["other_apps"][0]["count"] == 1
    codes = res["_meta"][APPROVALS_META_KEY]
    assert set(codes) == {a, b}  # never another app's (O9)
    the_set = res["_meta"][APPROVAL_SET_META_KEY]
    assert sorted(the_set["ids"]) == sorted([a, b])
    text = json.dumps(sc) + res["content"][0]["text"]
    assert the_set["code"] not in text and all(v not in text for v in codes.values())
    # the per-row code is the proposal card's own: Approve on one row applies that one only
    got = structured(
        await call(
            h, token, "confirm_proposal", {"proposal_id": a, "approval": codes[a], "decision": "approve"}
        )
    )
    assert got["state"] == "applied", got
    assert _qty(stack.ark, "Opt") == 1 and _qty(stack.ark, "Delver of Secrets") == 0
    # the other app sees its own with a code, and ours as a link only
    res2 = await call(h, other, "show_waiting_changes", {})
    assert set(res2["_meta"][APPROVALS_META_KEY]) == {c} and APPROVAL_SET_META_KEY not in res2["_meta"]


async def test_apply_all_applies_the_set_and_refuses_a_wrong_or_stale_code(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    a = structured(await _propose(h, token, "Opt"))["proposal_id"]
    b = structured(await _propose(h, token, "Delver of Secrets"))["proposal_id"]
    res = await call(h, token, "show_waiting_changes", {"deck_id": "42"})
    the_set = res["_meta"][APPROVAL_SET_META_KEY]
    before = _writes(ark)
    bad = structured(await call(h, token, "confirm_waiting", {"proposal_ids": [a, b], "approval": "x" * 32}))
    assert bad["ok"] is False and bad["error"] == "invalid_approval"
    # a code for the set does not stretch to a subset
    part = structured(
        await call(h, token, "confirm_waiting", {"proposal_ids": [a], "approval": the_set["code"]})
    )
    assert part["ok"] is False and part["error"] == "invalid_approval"
    # another app cannot use it
    other = await mcp_token(h)
    stolen = structured(
        await call(h, other, "confirm_waiting", {"proposal_ids": the_set["ids"], "approval": the_set["code"]})
    )
    assert stolen["ok"] is False and stolen["error"] == "invalid_approval"
    assert _writes(ark) == before
    # amended since the card was drawn: refused as replaced
    amended = structured(
        await call(h, token, "amend_proposal", {"proposal_id": b, "version": 1, "changes": _add("Swamp")})
    )
    assert amended["ok"], amended
    stale = structured(
        await call(h, token, "confirm_waiting", {"proposal_ids": the_set["ids"], "approval": the_set["code"]})
    )
    assert stale["ok"] is False and stale["error"] == "changed"
    assert stale["message"].startswith("There is a newer version of one of these changes")
    assert _writes(ark) == before
    # drawn again, the new code applies both, oldest first
    the_set = (await call(h, token, "show_waiting_changes", {"deck_id": "42"}))["_meta"][
        APPROVAL_SET_META_KEY
    ]
    out = structured(
        await call(h, token, "confirm_waiting", {"proposal_ids": the_set["ids"], "approval": the_set["code"]})
    )
    # under a heavy load the apply can outlast the call's wait and answer "applying"; then it is read
    # back as an assistant would, with get_apply_result
    for _ in range(60):
        if not (out["ok"] and out["state"] == "applying"):
            break
        await asyncio.sleep(0.5)
        out = structured(await call(h, token, "get_apply_result", {"batch_id": out["batch_id"]}))
    assert out["ok"] and out["state"] == "done", out
    assert [x["proposal_id"] for x in out["applied"]] == [a, b] and out["summary"].startswith("Applied 2.")
    assert out["result_url"].endswith(f"/proposals/applied/{out['batch_id']}")
    assert _qty(ark, "Opt") == 1 and _qty(ark, "Swamp") == 1
    again = structured(await call(h, token, "get_apply_result", {"batch_id": out["batch_id"]}))
    assert again["state"] == "done" and len(again["applied"]) == 2
    # another member's app cannot read it
    missing = structured(await call(h, token, "get_apply_result", {"batch_id": "nope"}))
    assert missing["ok"] is False and missing["error"] == "not_found"


async def test_the_proposal_card_names_the_other_waiting_changes(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    other = await mcp_token(h)
    first = structured(await _propose(h, token, "Opt"))["proposal_id"]
    await _propose(h, other, "Swamp")
    res = await _propose(h, token, "Delver of Secrets")
    info = structured(res)["deck_waiting"]
    assert info["earlier"] == 2 and info["same_app"] == 2
    assert len(info["other_apps"]) == 1 and info["other_apps"][0]["count"] == 1
    assert info["review_all_url"].endswith("/proposals#deck-42")
    the_set = res["_meta"][APPROVAL_SET_META_KEY]
    assert first in the_set["ids"] and len(the_set["ids"]) == 2
    assert the_set["code"] not in json.dumps(structured(res))


async def test_a_read_only_app_cannot_apply_all(stack: Stack) -> None:
    from .test_round4_data import _app_token

    h = stack.h
    await linked_user(stack)
    token, _cid = await _app_token(h, "reader", scope="mtg.read")
    res = await call(h, token, "confirm_waiting", {"proposal_ids": ["p"], "approval": "c"})
    assert res.get("isError") and "insufficient_scope" in json.dumps(res), res
    # reading what waits is allowed
    assert structured(await call(h, token, "show_waiting_changes", {}))["ok"]


async def test_in_chat_off_means_no_codes_and_no_apply_all(no_card: Stack) -> None:
    h = no_card.h
    token = await linked_user(no_card)
    a = structured(await _propose(h, token, "Opt"))["proposal_id"]
    b = structured(await _propose(h, token, "Delver of Secrets"))["proposal_id"]
    res = await call(h, token, "show_waiting_changes", {})
    assert structured(res)["count"] == 2 and not (res.get("_meta") or {}).get(APPROVALS_META_KEY)
    got = structured(await call(h, token, "confirm_waiting", {"proposal_ids": [a, b], "approval": "x" * 32}))
    assert got["ok"] is False and got["error"] == "in_chat_disabled"
    assert _writes(no_card.ark) == 0


def test_the_proposal_card_uses_the_shared_card_template() -> None:
    """A10: the proposal card is built like every other card: shared bridge and base inlined, with its
    own words when it cannot reach the chat."""
    from mtg_gateway import approve

    html = approve.card_html()
    assert "/*@BASE@*/" not in html and "/*@BRIDGE@*/" not in html
    assert "var Bridge" in html or "Bridge =" in html
    assert "Use the review page in your browser." in html
    assert html.index("button:disabled{opacity:.55") < html.index("border:1px dashed currentColor")


def test_the_history_row_shows_the_bracket_set_on_the_deck_over_the_estimate() -> None:
    """Q49/O14: the bracket set on the deck is THE bracket; the estimate shows only when none is set."""
    from mtg_gateway.history_view import build_events
    from mtg_gateway.reports import ReportService

    def row(stats: dict) -> dict:
        return ReportService._summary(
            {
                "id": "r1",
                "deck_id": 42,
                "deck_name": "Deck",
                "taken_at": 1,
                "fingerprint": "f",
                "stats_json": json.dumps(stats),
            }
        )

    est = {"bracket_estimate": {"bracket": 3}, "card_count": 100}
    # `bracket_set` (the bracket set on the deck) is Track C's summary field (reports.py, C's
    # branch); until the join the summary is given it here
    set_one = build_events([], [], [{**row(est), "bracket_set": 2}], csrf_input="")[0]["summary"]
    assert "bracket 2" in set_one and "about" not in set_one
    none_set = build_events([], [], [{**row(est), "bracket_set": None}], csrf_input="")[0]["summary"]
    assert "bracket about 3" in none_set


async def test_a_page_that_shared_a_failed_sync_read_reads_again_on_its_own() -> None:
    """A6: a page arriving while the deck sync's read is under way shares it; when that read fails
    (the sync reads with no retry), the page reads once more on its own terms instead of showing
    the error. A page's own failed read is not repeated."""
    import asyncio

    from mtg_gateway.decks import MemberCache

    cache = MemberCache(fresh=60, stale=600)
    gate = asyncio.Event()
    calls: list[str] = []

    async def sync_fetch() -> list[str]:
        calls.append("sync")
        await gate.wait()
        raise RuntimeError("Archidekt busy")

    async def page_fetch() -> list[str]:
        calls.append("page")
        return ["deck"]

    sync = asyncio.create_task(cache.refreshed("u", sync_fetch, wait=5))
    await asyncio.sleep(0)
    page = asyncio.create_task(cache.get("u", page_fetch, wait=5))
    await asyncio.sleep(0)
    gate.set()
    assert await page == ["deck"]
    with pytest.raises(RuntimeError):
        await sync
    assert calls == ["sync", "page"]

    async def failing() -> list[str]:
        calls.append("own")
        raise RuntimeError("down")

    cache.drop("u")
    with pytest.raises(RuntimeError):
        await cache.get("u", failing, wait=5)
    assert calls.count("own") == 1


async def test_the_waiting_card_rows_carry_why_one_needs_a_look(stack: Stack) -> None:
    """The waiting card shows the why of a "needs a look" row from the board line's status_reason."""
    from mtg_gateway import recheck

    h = stack.h
    token = await linked_user(stack)
    pid = structured(await _propose(h, token, "Opt"))["proposal_id"]
    with h.db.tx() as c:
        c.execute(
            "UPDATE proposals SET status = 'needs_look', status_reason = ? WHERE id = ?",
            (recheck.REASON_CHANGED, pid),
        )
    sc = structured(await call(h, token, "show_waiting_changes", {"deck_id": "42"}))
    row = sc["waiting"][0]
    assert row["status"] == "needs_look" and "changed on Archidekt" in row["status_reason"], row
    card = (
        Path(__file__).resolve().parents[1] / "src/mtg_gateway/static/cards/waiting-card.html"
    ).read_text()
    assert "x.status_reason" in card


def test_a_refusal_without_a_stored_reason_still_reads_as_a_sentence() -> None:
    from mtg_gateway.decks import needs_look_error

    msg = str(needs_look_error(None))
    assert msg.startswith("Not applied: The deck changed on Archidekt since this was made. Nothing")
    assert "  " not in msg
