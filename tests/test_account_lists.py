"""0.7.43: the member's account-wide Archidekt lists (account_lists.py) through the assistant's
two tools: each change is a proposal of kind ``lists``, checked against the list as it is now,
low risk when small, and a delete waits for the member's own press in every mode. An apply
reads the list again first, keeps the entries it changes from just before, and reads it back."""

from __future__ import annotations

import pytest

from mtg_gateway import account_lists, modes
from mtg_gateway.approve import APPROVAL_META_KEY

from .conftest import Stack, stack_with
from .test_account_actions import _browser_apply
from .test_approval_modes import SHIPPED, _set_mode
from .test_decks_and_proxy import Browser, call, linked_user, structured

stack_options = stack_with(SHIPPED)


async def _propose(h, token: str, kind: str, changes: list[dict]) -> dict:
    return structured(await call(h, token, "propose_list_change", {"list_name": kind, "changes": changes}))


async def _apply(h, token: str, p: dict) -> dict:
    return structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))


def test_tiers() -> None:
    row = {"kind": "list", "list": "pins", "op": "pin"}
    assert modes.risk_of("lists", [row])[0] == "low"
    assert modes.risk_of("lists", [row] * 6)[0] == "high"  # more than the low-risk row cap
    assert modes.risk_of("lists", [row, {**row, "destructive": True}])[0] == "destructive"
    assert modes.assistant_may_apply("semi", "low") is True
    assert all(modes.assistant_may_apply(m, "destructive") is False for m in modes.MODES)


async def test_templates_create_edit_and_a_delete_that_waits(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    p = await _propose(h, token, "templates", [{"op": "create", "name": "Ramp"}])
    assert p["ok"] and p["kind"] == "lists" and p["risk"] == "low" and p["assistant_may_apply"], p
    assert ark.account.templates.get("alice", []) == []  # a proposal changes nothing by itself
    out = await _apply(h, token, p)
    assert out["state"] == "applied" and out["result"]["verified"] is True, out
    assert ark.account.templates["alice"][0]["name"] == "Ramp"
    assert ark.account.templates["alice"][0]["includedInPrice"] is True

    p = await _propose(h, token, "templates", [{"op": "edit", "name": "ramp", "in_price": False}])
    assert "Counts in the price off" in p["rows"][0]["text"], p
    assert (await _apply(h, token, p))["state"] == "applied"
    assert ark.account.templates["alice"][0]["includedInPrice"] is False

    # refused before anything is stored: a duplicate, an unknown template, a change to nothing
    for bad in (
        [{"op": "create", "name": "RAMP"}],
        [{"op": "delete", "name": "Draw"}],
        [{"op": "edit", "name": "Ramp", "in_price": False}],
        [{"op": "rename", "name": "Ramp"}],
    ):
        r = await _propose(h, token, "templates", bad)
        assert r["ok"] is False, (bad, r)

    p = await _propose(h, token, "templates", [{"op": "delete", "name": "Ramp"}])
    assert p["risk"] == "destructive" and p["assistant_may_apply"] is False, p
    refused = await _apply(h, token, p)
    assert refused["ok"] is False and refused["error"] == "browser_required", refused
    assert ark.account.templates["alice"]  # still there
    done = await _browser_apply(h, p["proposal_id"])
    assert done["state"] == "applied"
    assert ark.account.templates["alice"] == []
    assert done["result"]["before"][0]["name"] == "Ramp"  # the copy from just before


async def test_a_delete_can_be_approved_on_the_chat_card(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.account.tags["alice"] = [{"id": 1, "name": "Cut", "color": "#ff0000"}]
    res = await call(
        h,
        token,
        "propose_list_change",
        {"list_name": "color_tags", "changes": [{"op": "delete", "name": "Cut"}]},
    )
    code = (res.get("_meta") or {}).get(APPROVAL_META_KEY)
    assert code, res.get("_meta")
    out = structured(
        await call(
            h,
            token,
            "confirm_proposal",
            {"proposal_id": structured(res)["proposal_id"], "approval": code, "decision": "approve"},
        )
    )
    assert out["state"] == "applied" and ark.account.tags["alice"] == [], out


async def test_color_tags(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    for bad in ({"op": "create", "name": "x" * 33}, {"op": "create", "name": "Cut", "color": "red"}):
        assert (await _propose(h, token, "color_tags", [bad]))["ok"] is False
    p = await _propose(h, token, "color_tags", [{"op": "create", "name": "Cut"}])
    assert p["rows"][0]["text"] == f"New color tag “Cut” ({account_lists.DEFAULT_TAG_COLOR})"
    await _apply(h, token, p)
    p = await _propose(
        h, token, "color_tags", [{"op": "edit", "name": "Cut", "new_name": "Cuts", "color": "#00FF00"}]
    )
    out = await _apply(h, token, p)
    assert out["state"] == "applied" and out["result"]["verified"], out
    assert ark.account.tags["alice"][0] | {"id": 0} == {"id": 0, "name": "Cuts", "color": "#00ff00"}


async def test_default_categories_take_template_names_only(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    ark.account.templates["alice"] = [
        {"id": 1, "name": "Ramp", "includedInDeck": True, "includedInPrice": True, "isPremier": False},
        {"id": 2, "name": "Draw", "includedInDeck": True, "includedInPrice": True, "isPremier": False},
    ]
    bad = await _propose(
        h, token, "default_categories", [{"op": "set", "card": "Sol Ring", "categories": ["Nope"]}]
    )
    assert bad["ok"] is False and "not one of your category templates" in bad["message"]
    p = await _propose(
        h, token, "default_categories", [{"op": "set", "card": "Sol Ring", "categories": ["draw", "Ramp"]}]
    )
    assert "(main: Draw)" in p["rows"][0]["text"], p
    out = await _apply(h, token, p)
    assert out["result"]["verified"] is True, out
    assert ark.account.defaults["alice"][9003] == ["Draw", "Ramp"]
    got = structured(await call(h, token, "get_my_lists", {"list_name": "default_categories"}))
    assert got["items"] == [{"oracle_id": 9003, "name": "Sol Ring", "categories": ["Draw", "Ramp"]}]
    p = await _propose(h, token, "default_categories", [{"op": "clear", "card": "Sol Ring"}])
    await _apply(h, token, p)
    assert ark.account.defaults["alice"][9003] == []


async def test_pins(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    p = await _propose(h, token, "pins", [{"op": "pin", "card": "Sol Ring", "notes": "for Meren"}])
    assert (await _apply(h, token, p))["state"] == "applied"
    pins = structured(await call(h, token, "get_my_lists", {"list_name": "pins"}))
    assert pins["items"][0]["name"] == "Sol Ring" and pins["items"][0]["notes"] == "for Meren"
    assert pins["limit"] == account_lists.MAX_PINS
    p = await _propose(
        h, token, "pins", [{"op": "edit", "card": "Sol Ring", "quantity": 2, "finish": "Foil"}]
    )
    assert p["rows"][0]["text"] == "Pin “Sol Ring”: copies 1 -> 2, finish Normal -> Foil", p
    assert (await _apply(h, token, p))["result"]["verified"] is True
    assert (await _propose(h, token, "pins", [{"op": "edit", "card": "Sol Ring", "quantity": 0}]))[
        "ok"
    ] is False
    p = await _propose(h, token, "pins", [{"op": "remove_all"}])
    assert p["risk"] == "destructive"
    await _browser_apply(h, p["proposal_id"])
    assert ark.account.pins["alice"] == []


async def test_custom_cards_never_take_a_picture_from_the_assistant(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    pic = await _propose(
        h,
        token,
        "custom_cards",
        [
            {
                "op": "create",
                "fields": {
                    "frontName": "X",
                    "frontTypes": "Creature",
                    "frontImageUrl": "https://e.example/a.png",
                },
            }
        ],
    )
    assert pic["ok"] is False and "My lists page" in pic["message"]
    p = await _propose(
        h,
        token,
        "custom_cards",
        [{"op": "create", "fields": {"frontName": "Grizzled Mentor", "frontTypes": "Creature"}}],
    )
    assert (await _apply(h, token, p))["state"] == "applied"
    made = ark.account.customs["alice"][0]
    made["frontImageUrl"] = "https://cdn.example/pic.png"  # uploaded on the page
    p = await _propose(
        h,
        token,
        "custom_cards",
        [{"op": "edit", "name": "Grizzled Mentor", "fields": {"frontText": "Vigilance"}}],
    )
    assert p["rows"][0]["text"] == "Custom card “Grizzled Mentor”: rules text", p
    out = await _apply(h, token, p)
    assert out["result"]["verified"] is True, out
    assert ark.account.customs["alice"][0]["frontImageUrl"] == "https://cdn.example/pic.png"  # kept


async def test_a_change_made_on_archidekt_since_stops_the_apply(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    ark.account.tags["alice"] = [{"id": 1, "name": "Cut", "color": "#ff0000"}]
    p = await _propose(h, token, "color_tags", [{"op": "edit", "name": "Cut", "color": "#00ff00"}])
    ark.account.tags["alice"][0]["color"] = "#0000ff"  # changed on archidekt.com meanwhile
    sent = len([c for c in ark.calls if c[0] != "GET"])
    out = await _apply(h, token, p)
    assert out["ok"] is False and out["error"] == "stale", out
    assert len([c for c in ark.calls if c[0] != "GET"]) == sent  # nothing was sent
    again = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert again["state"] == "pending" and again["status"] == "needs_look", again


@pytest.mark.parametrize("kind", account_lists.KINDS)
async def test_every_list_reads(stack: Stack, kind: str) -> None:
    token = await linked_user(stack)
    got = structured(await call(stack.h, token, "get_my_lists", {"list_name": kind}))
    assert got["ok"] and got["list"] == kind and got["items"] == [], got


async def test_the_review_page_and_history_show_a_lists_change(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    ark.account.tags["alice"] = [{"id": 1, "name": "Cut", "color": "#ff0000"}]
    p = await _propose(h, token, "color_tags", [{"op": "edit", "name": "Cut", "color": "#00ff00"}])
    await _apply(h, token, p)
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
        assert "Your Archidekt lists" in page and "Color tag “Cut”: color #ff0000 -&gt; #00ff00" in page
        assert "Before: your list as it was just before" in page and "Cut: #ff0000" in page
        assert "Your color tags on Archidekt have these changes now." in page
        history = (await b.http.get("/history")).text
        assert "Your Archidekt lists" in history
    finally:
        await b.aclose()
