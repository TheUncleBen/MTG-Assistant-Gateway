"""The in-chat Approve card (approve.py): proposal tools carry the card, only the card's one-time
code applies or rejects, and the assistant's own paths stay as they were."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from mtg_gateway import approve
from mtg_gateway.app import WRITE_TOOLS
from mtg_gateway.approve import (
    APPROVAL_META_KEY,
    CARD_URI,
    apply_on_review_page,
    approval_code,
    approval_matches,
)

from .conftest import FakeIdP, Harness, make_settings, running, sse_json
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, Stack, _client, call, linked_user, mcp_token, structured

CARD_TOOLS = (
    "propose_deck_changes",
    "propose_new_deck",
    "propose_restore_snapshot",
    "propose_deck_details",
    "propose_clone_deck",
    "get_proposal",
)


def _stack_with(tmp_path, idp: FakeIdP, **over):
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, apply_via_mcp=False, archidekt_base="https://ark.test/api", **over
    )
    return Harness(settings, idp, archidekt=_client(settings, ark)), ark


@pytest.fixture
async def stack(tmp_path, idp: FakeIdP):
    """Writes on, the assistant's own apply off (the shipped default): only the card or the
    review page applies."""
    h, ark = _stack_with(tmp_path, idp)
    async with running(h):
        yield Stack(h, ark)


@pytest.fixture
async def no_card(tmp_path, idp: FakeIdP):
    h, ark = _stack_with(tmp_path, idp, apply_in_chat=False)
    async with running(h):
        yield Stack(h, ark)


async def _propose(h, token: str) -> dict:
    return await call(
        h,
        token,
        "propose_deck_changes",
        {"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]},
    )


async def _tools(h, token: str) -> dict[str, dict]:
    r = await h.mcp(token, "tools/list", {}, rid=3)
    assert r.status_code == 200, r.text
    return {t["name"]: t for t in sse_json(r)["result"]["tools"]}


def _writes(ark: FakeArchidekt) -> int:
    """Deck writes the fake Archidekt saw (linking an account is a POST too, so look at decks)."""
    return len([c for c in ark.calls if c[0] in ("POST", "PUT", "PATCH", "DELETE") and "/decks" in c[1]])


# -- what the client sees -----------------------------------------------------------------------


async def test_proposal_tools_carry_the_card_and_confirm_is_app_only(stack: Stack) -> None:
    tools = await _tools(stack.h, await mcp_token(stack.h))
    for name in CARD_TOOLS:
        meta = tools[name]["_meta"]
        assert meta["ui"]["resourceUri"] == CARD_URI and meta["ui/resourceUri"] == CARD_URI, name
    confirm = tools["confirm_proposal"]["_meta"]
    assert confirm["ui"] == {"visibility": ["app"]}
    assert confirm["openai/visibility"] == "private" and confirm["openai/widgetAccessible"] is True
    assert "resourceUri" not in confirm["ui"]
    assert tools["apply_proposal"]["_meta"]["anthropic/requiresUserInteraction"] is True
    assert "confirm_proposal" in WRITE_TOOLS
    # The tools the assistant uses for a reject keep their plain metadata.
    assert "_meta" not in tools["reject_proposal"] or "ui" not in tools["reject_proposal"]["_meta"]


async def test_card_resource_is_served_as_an_mcp_app(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    r = await h.mcp(token, "resources/read", {"uri": CARD_URI}, rid=4)
    assert r.status_code == 200, r.text
    content = sse_json(r)["result"]["contents"][0]
    assert content["mimeType"] == "text/html;profile=mcp-app"
    html = content["text"]
    assert html.startswith("<!DOCTYPE html>") and "ui/initialize" in html and "confirm_proposal" in html
    assert "<script src=" not in html and "import " not in html  # self-contained: no scripts from elsewhere
    assert content["_meta"]["ui"]["csp"]["resourceDomains"] == approve.CARD_IMAGE_HOSTS
    assert content["_meta"]["ui"]["prefersBorder"] is True
    # resources/list does not need to show it, but the server advertises the extension.
    # The SDK registers the extension (io.modelcontextprotocol/ui), but the wire revisions this
    # server speaks have no capabilities.extensions map, so hosts go by the tool and resource
    # metadata alone, as the MCP Apps server helpers do; nothing here depends on the capability.
    tools = await _tools(h, token)
    assert tools["propose_deck_changes"]["_meta"]["ui"]["resourceUri"] == CARD_URI


async def test_approval_code_travels_only_in_meta(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    res = await _propose(h, token)
    code = res["_meta"][APPROVAL_META_KEY]
    assert len(code) >= 30
    text = res["content"][0]["text"]
    assert code not in text and code not in json.dumps(res["structuredContent"])
    assert "approval" not in res["structuredContent"]
    # get_proposal shows the card again with the same code while the proposal is pending.
    again = await call(h, token, "get_proposal", {"proposal_id": structured(res)["proposal_id"]})
    assert again["_meta"][APPROVAL_META_KEY] == code
    # An error result carries no code (there is nothing to approve).
    bad = await call(h, token, "get_proposal", {"proposal_id": "nope"})
    assert structured(bad)["ok"] is False and "_meta" not in bad


# -- the card's decisions ---------------------------------------------------------------------


async def test_card_approve_applies_and_the_code_is_spent(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    res = await _propose(h, token)
    p, code = structured(res), res["_meta"][APPROVAL_META_KEY]
    # The assistant's own apply is refused on this gateway (MTG_APPLY_VIA_MCP=false)...
    own = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert own["ok"] is False and own["error"] == "browser_required"
    assert _writes(stack.ark) == 0
    # ...the card's Approve applies at once, with no "too soon" hold.
    done = await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": code})
    got = structured(done)
    assert got["ok"] is True and got["state"] == "applied", got
    assert "_meta" not in done  # nothing left to approve
    assert _writes(stack.ark) > 0
    sent = _writes(stack.ark)
    # Spent: the same code cannot apply again.
    twice = structured(
        await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": code})
    )
    assert twice["ok"] is False and twice["error"] == "already_applied"
    assert _writes(stack.ark) == sent
    audit = [r for r in h.db.audit_for_user("user-1", 50) if r["event"] == "proposal_applied"]
    assert audit and audit[0]["detail"]["via"] == "app"


async def test_card_reject_sends_nothing(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    res = await _propose(h, token)
    p, code = structured(res), res["_meta"][APPROVAL_META_KEY]
    got = structured(
        await call(
            h,
            token,
            "confirm_proposal",
            {"proposal_id": p["proposal_id"], "approval": code, "decision": "reject"},
        )
    )
    assert got["ok"] is True and got["state"] == "rejected"
    assert _writes(stack.ark) == 0
    bad = structured(
        await call(
            h,
            token,
            "confirm_proposal",
            {"proposal_id": p["proposal_id"], "approval": code, "decision": "maybe"},
        )
    )
    assert bad["ok"] is False and bad["error"] == "invalid"


@pytest.mark.parametrize("given", ["", "x", None, 1, "A" * 65])
async def test_wrong_or_missing_code_is_refused_and_logged(stack: Stack, given) -> None:
    h = stack.h
    token = await linked_user(stack)
    p = structured(await _propose(h, token))
    args = {"proposal_id": p["proposal_id"]}
    if given is not None:
        args["approval"] = given
    r = await h.mcp(token, "tools/call", {"name": "confirm_proposal", "arguments": args}, rid=8)
    body = sse_json(r)
    if "error" in body or body["result"].get("isError"):
        pass  # the SDK refused the arguments before the tool ran (a missing or non-string code)
    else:
        got = body["result"]["structuredContent"]
        assert got["ok"] is False and got["error"] == "invalid_approval", got
        assert got["review_url"].endswith(p["proposal_id"])
        assert any(a["event"] == "approval_refused" for a in h.db.audit_for_user("user-1", 20))
    assert _writes(stack.ark) == 0
    assert (
        structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))["state"]
        == "pending"
    )


async def test_code_is_bound_to_the_proposal_the_member_and_the_app(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    a = await _propose(h, token)
    b = await _propose(h, token)
    pa, ca = structured(a)["proposal_id"], a["_meta"][APPROVAL_META_KEY]
    pb, cb = structured(b)["proposal_id"], b["_meta"][APPROVAL_META_KEY]
    assert ca != cb
    # Another proposal's code.
    got = structured(await call(h, token, "confirm_proposal", {"proposal_id": pa, "approval": cb}))
    assert got["ok"] is False and got["error"] == "invalid_approval"
    # The right code from a different connected app (a second OAuth client of the same member).
    other = await mcp_token(h)
    got = structured(await call(h, other, "confirm_proposal", {"proposal_id": pa, "approval": ca}))
    assert got["ok"] is False and got["error"] == "invalid_approval"
    # A code minted for a different member (the HMAC input differs).
    assert approval_code("s" * 48, proposal_id=pa, sub="someone-else", client_id="c", created_at=1) != ca
    assert _writes(stack.ark) == 0
    # The real one still works afterwards: refusals do not spend it.
    got = structured(await call(h, token, "confirm_proposal", {"proposal_id": pa, "approval": ca}))
    assert got["ok"] is True and got["state"] == "applied"
    assert pb and structured(await call(h, token, "get_proposal", {"proposal_id": pb}))["state"] == "pending"


async def test_card_cannot_apply_what_the_browser_already_rejected(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    res = await _propose(h, token)
    p, code = structured(res), res["_meta"][APPROVAL_META_KEY]
    b = Browser(h)
    await b.login()
    page = await b.http.get(f"/proposals/{p['proposal_id']}")
    assert "Reject this proposal" in page.text
    assert h.db.reject_proposal(p["proposal_id"], "user-1")
    await b.aclose()
    got = structured(
        await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": code})
    )
    assert got["ok"] is False and got["error"] == "not_pending"
    assert _writes(stack.ark) == 0


async def test_read_only_token_cannot_confirm(stack: Stack) -> None:
    from .test_round4_data import _app_token

    h = stack.h
    await linked_user(stack)
    token, _cid = await _app_token(h, "reader", scope="mtg.read")
    res = await call(h, token, "confirm_proposal", {"proposal_id": "p", "approval": "c"})
    assert res.get("isError") and "insufficient_scope" in json.dumps(res), res


# -- the switch -------------------------------------------------------------------------------


async def test_in_chat_off_means_no_card_no_code_no_confirm(no_card: Stack) -> None:
    h = no_card.h
    token = await linked_user(no_card)
    tools = await _tools(h, token)
    for name in CARD_TOOLS:
        assert "ui" not in tools[name].get("_meta", {}), name
    res = await _propose(h, token)
    assert "_meta" not in res
    p = structured(res)
    assert "card" not in p["next_step"] and "review page" in p["next_step"]
    code = approval_code(
        "s" * 48, proposal_id=p["proposal_id"], sub="user-1", client_id="", created_at=p["created_at"]
    )
    got = structured(
        await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": code})
    )
    assert got["ok"] is False and got["error"] == "in_chat_disabled"
    assert _writes(no_card.ark) == 0
    r = await h.mcp(token, "resources/read", {"uri": CARD_URI}, rid=4)
    body = sse_json(r)
    assert "error" in body or body["result"].get("isError"), body


def test_approval_code_shape() -> None:
    code = approval_code("secret", proposal_id="p", sub="u", client_id="c", created_at=1)
    assert code == approval_code("secret", proposal_id="p", sub="u", client_id="c", created_at=1)
    assert code != approval_code("secret", proposal_id="p", sub="u", client_id="c", created_at=2)
    assert code != approval_code("other", proposal_id="p", sub="u", client_id="c", created_at=1)
    assert approval_matches(code, code) and not approval_matches(code, code[:-1])
    assert not approval_matches(code, None) and not approval_matches(code, "")


# -- apply_proposal on a client with URL elicitation (Claude Code) ---------------------------


def _ctx(action: str | None, *, url: bool = True):
    calls: list[tuple] = []

    async def elicit_url(message, url_, elicitation_id):
        calls.append(("elicit", message, url_, elicitation_id))
        if action is None:
            raise RuntimeError("client refused")
        return SimpleNamespace(action=action)

    async def send_elicit_complete(elicitation_id):
        calls.append(("complete", elicitation_id))

    caps = SimpleNamespace(elicitation=SimpleNamespace(url=SimpleNamespace() if url else None, form=None))
    return SimpleNamespace(
        client_capabilities=caps,
        elicit_url=elicit_url,
        session=SimpleNamespace(send_elicit_complete=send_elicit_complete),
    ), calls


async def test_apply_waits_for_the_review_page_after_a_url_elicitation() -> None:
    state = {"state": "pending", "review_url": "https://mtg.test/proposals/p1", "proposal_id": "p1"}
    ctx, calls = _ctx("accept")
    ticks = []

    async def sleep(n):
        ticks.append(n)
        if len(ticks) == 2:
            state["state"] = "applied"

    out = await apply_on_review_page(
        ctx, lambda: dict(state), wait_seconds=30, poll_seconds=0.01, sleep=sleep
    )
    assert out["ok"] is True and out["state"] == "applied"
    assert calls[0][0] == "elicit" and calls[0][2] == state["review_url"]
    assert calls[-1] == ("complete", calls[0][3])
    assert len(ticks) == 2


async def test_apply_reports_a_still_pending_proposal_and_declines() -> None:
    state = {"state": "pending", "review_url": "u", "proposal_id": "p1"}
    ctx, _ = _ctx("accept")

    async def sleep(_n):
        return None

    out = await apply_on_review_page(ctx, lambda: dict(state), wait_seconds=0, poll_seconds=0, sleep=sleep)
    assert out["ok"] is False and out["error"] == "browser_pending" and out["state"] == "pending"
    ctx, _ = _ctx("decline")
    out = await apply_on_review_page(ctx, lambda: dict(state), wait_seconds=0, poll_seconds=0, sleep=sleep)
    assert out["ok"] is False and out["error"] == "browser_required"
    # No URL elicitation, or a client that refuses the request: the caller's plain text path.
    ctx, _ = _ctx("accept", url=False)
    assert await apply_on_review_page(ctx, lambda: dict(state)) is None
    ctx, _ = _ctx(None)
    assert await apply_on_review_page(ctx, lambda: dict(state)) is None
    assert await apply_on_review_page(SimpleNamespace(client_capabilities=None), lambda: dict(state)) is None


async def test_apply_proposal_without_elicitation_is_unchanged(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    p = structured(await _propose(h, token))
    got = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["ok"] is False and got["error"] == "browser_required" and got["review_url"] == p["review_url"]
    assert _writes(stack.ark) == 0
    assert time.time() > 0 and asyncio.iscoroutinefunction(apply_on_review_page)
