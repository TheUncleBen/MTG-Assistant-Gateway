"""Approval modes (modes.py): manual by default, semi and auto chosen per member on the Account
page only, risk tiers from the review rows, and strict per-member isolation."""

from __future__ import annotations

import html
import json
import re

import pytest

from mtg_gateway import modes
from mtg_gateway.approve import APPROVAL_META_KEY

from .conftest import FakeIdP, Harness, Stack, StackOptions, open_stack, sse_json, stack_with
from .test_decks_and_proxy import Browser, call, linked_user, structured

AMY = {"sub": "user-2", "email": "amy@example.com", "name": "Amy", "preferred_username": "amy"}
ALICE = {"sub": "user-1", "email": "alice@example.com", "name": "Alice", "preferred_username": "alice"}
NAMES = ["Arcane Signet", "Rampant Growth", "Sol Ring", "Forest", "Island", "Opt", "Swamp"]


# The shipped defaults: manual for everyone who has not chosen, no cap.
SHIPPED = StackOptions(settings={"writes_enabled": True})
stack_options = stack_with(SHIPPED)


@pytest.fixture
async def capped(tmp_path, idp: FakeIdP):
    settings = {"writes_enabled": True, "approval_mode_default": "auto", "approval_mode_max": "semi"}
    async with open_stack(tmp_path, idp, SHIPPED.but(settings=settings)) as st:
        yield st


async def _set_mode(h: Harness, mode: str, *, user: dict | None = None) -> str:
    """Choose a mode the way a member does: the Account page form in their own browser session.
    Returns the redirect target (``?ok=`` or ``?err=``)."""
    if user:
        h.idp.user = user
    b = Browser(h)
    await b.login()
    r = await b.http.post("/account", data={"csrf": await b.csrf(), "action": "approval_mode", "mode": mode})
    assert r.status_code == 303, r.text
    await b.aclose()
    return r.headers["location"]


async def _adds(h, token: str, n: int) -> dict:
    changes = [{"action": "add", "card_name": name} for name in NAMES[:n]]
    return await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes})


# -- the tiers and the mode arithmetic (pure) ------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "rows", "risk"),
    [
        ("edit", [{"kind": "add", "name": "Sol Ring", "qty": 1}], "low"),
        ("edit", [{"kind": k} for k in ("add", "remove", "change", "category", "finish")], "low"),
        ("edit", [{"kind": "add"}] * 6, "high"),
        ("edit", [{"kind": "add", "name": "Forest", "qty": 4}], "low"),
        ("edit", [{"kind": "add", "name": "Forest", "qty": 5}], "high"),
        ("edit", [{"kind": "remove", "name": "Forest", "qty": 12}], "high"),
        ("edit", [{"kind": "change", "name": "Forest", "before": 14, "after": 10}], "low"),
        ("edit", [{"kind": "change", "name": "Forest", "before": 14, "after": 0}], "high"),
        ("edit", [{"kind": "add", "name": "Forest", "qty": "lots"}], "high"),
        ("edit", [{"kind": "commander", "before": "A", "after": "B"}], "high"),
        ("edit", [{"kind": "add"}, {"kind": "commander"}], "high"),
        ("edit", [{"kind": "description"}], "high"),
        ("edit", None, "high"),
        ("clone", [{"kind": "clone"}], "low"),
        ("create_deck", [{"kind": "new_deck"}], "high"),
        ("restore", [{"kind": "restore"}], "high"),
        ("details", [{"kind": "detail"}], "high"),
        ("collection", [{"kind": "add", "name": "Sol Ring", "qty": 1}], "low"),
        ("collection", [{"kind": "remove", "name": "Forest", "qty": 12}], "high"),
        ("something_new", [], "high"),
    ],
)
def test_risk_tiers(kind: str, rows, risk: str) -> None:
    got, why = modes.risk_of(kind, rows, max_rows=5)
    assert got == risk and why


def test_risk_row_limit_is_a_setting() -> None:
    rows = [{"kind": "add"}] * 6
    assert modes.risk_of("edit", rows, max_rows=5)[0] == "high"
    assert modes.risk_of("edit", rows, max_rows=6)[0] == "low"


def test_effective_mode_honours_choice_default_and_cap() -> None:
    assert modes.effective_mode(None, default="manual", cap="auto") == "manual"
    assert modes.effective_mode(None, default="semi", cap="auto") == "semi"
    assert modes.effective_mode("auto", default="manual", cap="auto") == "auto"
    assert modes.effective_mode("auto", default="manual", cap="semi") == "semi"
    assert modes.effective_mode("semi", default="auto", cap="manual") == "manual"
    assert modes.effective_mode("bogus", default="semi", cap="auto") == "semi"
    assert modes.effective_mode("auto", default="bogus", cap="bogus") == "auto"
    assert modes.assistant_may_apply("manual", "low") is False
    assert modes.assistant_may_apply("semi", "low") is True
    assert modes.assistant_may_apply("semi", "high") is False
    assert modes.assistant_may_apply("auto", "high") is True


# -- choosing a mode: the Account page only --------------------------------------------------
async def test_default_is_manual_and_the_account_page_sets_the_mode(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    st = structured(await call(h, token, "account_status"))
    assert st["approval_mode"] == "manual" and "Ask me every time" in st["approval_mode_label"]

    b = Browser(h)
    await b.login()
    page = (await b.http.get("/account")).text
    assert "Approval mode" in page and page.count("name='mode'") == 3 and "value='manual' checked" in page
    assert "tricked by text it reads" in page  # the cost of the auto modes, said plainly
    await b.aclose()

    assert await _set_mode(h, "auto") == "/account?ok=mode_auto"
    assert structured(await call(h, token, "account_status"))["approval_mode"] == "auto"
    assert h.db.get_user("user-1")["approval_mode"] == "auto"
    events = [a for a in h.db.audit_for_user("user-1", 50) if a["event"] == "approval_mode_set"]
    assert events and events[0]["detail"] == {"from": "manual", "to": "auto", "by": "user"}

    assert await _set_mode(h, "bogus") == "/account?err=mode_invalid"
    assert await _set_mode(h, "") == "/account?err=mode_invalid"
    assert structured(await call(h, token, "account_status"))["approval_mode"] == "auto"
    assert await _set_mode(h, "manual") == "/account?ok=mode_manual"
    assert structured(await call(h, token, "account_status"))["approval_mode"] == "manual"


async def test_account_page_needs_a_valid_form(stack: Stack) -> None:
    """No CSRF token, no change; a bearer token (an assistant) cannot post the form at all."""
    h = stack.h
    token = await linked_user(stack)
    b = Browser(h)
    await b.login()
    r = await b.http.post("/account", data={"csrf": "0" * 32, "action": "approval_mode", "mode": "auto"})
    assert r.status_code == 403
    await b.aclose()
    r = await h.http.post(
        "/account",
        data={"action": "approval_mode", "mode": "auto"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (302, 303) and "/login" in r.headers.get("location", "")
    assert h.db.get_user("user-1")["approval_mode"] is None


async def test_no_tool_or_api_route_sets_the_mode(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    r = await h.mcp(token, "tools/list", {}, rid=3)
    names = {t["name"] for t in sse_json(r)["result"]["tools"]}
    # "mode" as a word, so remove_card_model (a card model, not the approval mode) is not caught
    assert not {n for n in names if "mode" in n.split("_") or "approval" in n and n != "confirm_proposal"}
    for path in ("/api/v1/me", "/api/v1/account", "/api/v1/me/approval_mode"):
        r = await h.http.post(
            path, json={"approval_mode": "auto"}, headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code in (404, 405), (path, r.status_code)
    assert h.db.get_user("user-1")["approval_mode"] is None


async def test_a_cap_changed_on_the_settings_page_applies_at_once(stack: Stack) -> None:
    """An admin lowers the cap on the Settings page: the Account page refuses a mode above it
    from then on, with no restart."""
    h = stack.h
    await linked_user(stack)
    h.app.state.gateway.live.set("approval_mode_max", "semi", by="admin-1")
    assert await _set_mode(h, "auto") == "/account?err=mode_invalid"
    assert h.db.get_user("user-1")["approval_mode"] is None
    assert await _set_mode(h, "semi") == "/account?ok=mode_semi"


async def test_a_lowered_cap_and_row_limit_apply_at_once_to_a_member_who_chose_more(stack: Stack) -> None:
    """The join of the live mode reading and the Settings page: a member who already
    chose Full auto is read as Semi-auto as soon as an admin lowers the cap, with no restart, and a
    lowered row limit makes a 3-row edit high risk at once."""
    h = stack.h
    token = await linked_user(stack)
    assert await _set_mode(h, "auto") == "/account?ok=mode_auto"
    assert structured(await call(h, token, "account_status"))["approval_mode"] == "auto"
    h.app.state.gateway.live.set("approval_mode_max", "semi", by="admin-1")
    assert h.db.get_user("user-1")["approval_mode"] == "auto"  # the stored choice is kept
    assert structured(await call(h, token, "account_status"))["approval_mode"] == "semi"
    p = structured(await _adds(h, token, 3))
    assert p["approval_mode"] == "semi" and p["risk"] == "low" and p["assistant_may_apply"] is True
    h.app.state.gateway.live.set("auto_apply_max_rows", 2, by="admin-1")
    p = structured(await _adds(h, token, 3))
    assert p["approval_mode"] == "semi" and p["risk"] == "high" and p["assistant_may_apply"] is False


async def test_the_cap_limits_what_members_may_choose(capped: Stack) -> None:
    """MTG_APPROVAL_MODE_MAX=semi: the operator's auto default is read as semi, auto cannot be
    chosen, and the Account page shows the auto choice disabled."""
    h = capped.h
    token = await linked_user(capped)
    assert structured(await call(h, token, "account_status"))["approval_mode"] == "semi"
    assert await _set_mode(h, "auto") == "/account?err=mode_invalid"
    assert h.db.get_user("user-1")["approval_mode"] is None
    b = Browser(h)
    await b.login()
    page = (await b.http.get("/account")).text
    assert "value='auto' disabled" in page and "not allowed on this gateway" in page
    await b.aclose()
    assert await _set_mode(h, "manual") == "/account?ok=mode_manual"
    assert structured(await call(h, token, "account_status"))["approval_mode"] == "manual"


# -- what each mode lets the assistant do ------------------------------------------------------
async def test_semi_mode_applies_low_risk_and_asks_for_high_risk(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")

    low = await _adds(h, token, 1)
    p = structured(low)
    assert p["approval_mode"] == "semi" and p["risk"] == "low" and p["assistant_may_apply"] is True
    assert "call apply_proposal now" in p["next_step"]
    assert APPROVAL_META_KEY not in (low.get("_meta") or {})  # nothing for a person to press
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    sent = len(ark.patches)
    assert a["ok"] and a["state"] == "applied" and a["snapshot_id"] and sent >= 1
    applied = [x for x in h.db.audit_for_user("user-1", 50) if x["event"] == "proposal_applied"]
    assert applied and applied[0]["detail"]["via"] == "mcp"

    high = await _adds(h, token, 6)
    p = structured(high)
    assert p["risk"] == "high" and "6 rows" in p["risk_reason"] and p["assistant_may_apply"] is False
    assert "high risk" in p["next_step"] and "Do not call apply_proposal" in p["next_step"]
    code = high["_meta"][APPROVAL_META_KEY]  # the card gets its buttons
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required" and out["risk"] == "high"
    assert "semi-automatic" in out["message"] and len(ark.patches) == sent
    refused = [x for x in h.db.audit_for_user("user-1", 50) if x["event"] == "apply_needs_user"]
    assert refused and refused[0]["detail"]["mode"] == "semi" and refused[0]["detail"]["risk"] == "high"
    # The member's press on the card still applies it.
    done = structured(
        await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": code})
    )
    assert done["ok"] and done["state"] == "applied" and len(ark.patches) > sent


@pytest.mark.parametrize(
    ("tool", "args", "risk"),
    [
        (
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "set_commander", "card_name": "Sol Ring"}]},
            "high",
        ),
        ("propose_deck_details", {"deck_id": "42", "details": {"name": "Renamed"}}, "high"),
        ("propose_clone_deck", {"deck_id": "42", "name": "Copy"}, "low"),
        (
            "propose_new_deck",
            {"name": "Fresh", "deck_format": "commander", "cards": [{"name": "Sol Ring", "quantity": 1}]},
            "high",
        ),
        ("propose_collection_changes", {"add": [{"name": "Sol Ring", "quantity": 1}]}, "low"),
        ("propose_collection_changes", {"add": [{"name": "Forest", "quantity": 9}]}, "high"),
    ],
)
async def test_every_proposal_kind_has_a_tier(stack: Stack, tool: str, args: dict, risk: str) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    p = structured(await call(h, token, tool, args))
    assert p["ok"], p
    assert p["risk"] == risk and p["assistant_may_apply"] is (risk == "low"), p
    if risk == "high":
        out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
        assert out["ok"] is False and out["error"] == "browser_required"


async def test_semi_mode_note_says_what_it_applies(stack: Stack) -> None:
    """account_status's note matches the tiers: clones and small deck or collection edits apply
    without the member's press in semi mode (a clone is low risk by design); a new deck from a list
    waits."""
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    note = structured(await call(h, token, "account_status"))["approval_mode_note"]
    assert "at most 5 card rows" in note and f"{modes.MAX_COPIES_PER_ROW} copies each" in note
    assert "a private copy of a deck (a clone" in note and "collection" in note
    assert "A new deck built from a list" in note and "New decks" not in note
    assert "{rows}" not in modes.mode_help("semi", max_rows=3) and "at most 3" in modes.mode_help(
        "semi", max_rows=3
    )


async def test_restore_is_high_risk(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    p = structured(await _adds(h, token, 1))
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["snapshot_id"]
    await _set_mode(h, "semi")
    r = structured(await call(h, token, "propose_restore_snapshot", {"snapshot_id": a["snapshot_id"]}))
    assert r["ok"] and r["risk"] == "high" and r["assistant_may_apply"] is False


async def test_auto_mode_applies_everything_with_a_snapshot(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    p = structured(await _adds(h, token, 6))
    assert p["risk"] == "high" and p["assistant_may_apply"] is True
    assert "deck and collection changes" in p["next_step"]
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    sent = len(ark.patches)
    assert a["ok"] and a["state"] == "applied" and a["snapshot_id"] and sent >= 1
    # "May apply now": false once the proposal is closed; the policy fields stay as they were.
    assert a["assistant_may_apply"] is False and a["approval_mode"] == "auto" and a["risk"] == "high"
    r = structured(await _adds(h, token, 1))
    assert r["assistant_may_apply"] is True
    r = structured(await call(h, token, "reject_proposal", {"proposal_id": r["proposal_id"]}))
    assert r["state"] == "rejected" and r["assistant_may_apply"] is False and r["risk"] == "low"
    snaps = structured(await call(h, token, "list_snapshots"))["snapshots"]
    assert [s["snapshot_id"] for s in snaps] == [a["snapshot_id"]]
    # The REST API applies under the same mode (same service path).
    p2 = structured(await _adds(h, token, 1))
    r = await h.http.post(
        f"/api/v1/proposals/{p2['proposal_id']}/apply", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200 and r.json()["state"] == "applied" and len(ark.patches) > sent


async def test_manual_mode_over_the_api_is_browser_required(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await _adds(h, token, 1))
    r = await h.http.post(
        f"/api/v1/proposals/{p['proposal_id']}/apply", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403 and r.json()["error"] == "browser_required" and ark.patches == []


async def test_changing_the_mode_later_governs_pending_proposals(stack: Stack) -> None:
    """The mode is read when the apply is attempted, never frozen into the proposal."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    p = structured(await _adds(h, token, 1))
    assert p["assistant_may_apply"] is True
    await _set_mode(h, "manual")
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required" and ark.patches == []
    assert (
        structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))[
            "assistant_may_apply"
        ]
        is False
    )


async def test_a_change_between_the_assistants_apply_and_its_claim_still_stops_it(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The apply runs as its own task, so the member's mode can change (or a change can be held)
    after apply_proposal's own check: the policy is decided again just before the claim."""
    from contextlib import asynccontextmanager

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    p = structured(await _adds(h, token, 1))
    assert p["assistant_may_apply"] is True
    decks = h.app.state.gateway.decks
    real = decks.archidekt_slot

    @asynccontextmanager
    async def slot(sub):
        h.db.set_approval_mode(sub, "manual")  # the member's press on the Account page lands now
        async with real(sub):
            yield

    monkeypatch.setattr(decks, "archidekt_slot", slot)
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required" and ark.patches == []
    assert h.db.get_proposal(p["proposal_id"], "user-1")["state"] == "pending"


# -- isolation: one member's mode never reaches another ------------------------------------------
async def test_one_members_mode_never_applies_to_another(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    alice = await linked_user(stack, ALICE, "alice", "pw-alice")
    amy = await linked_user(stack, AMY, "amy", "pw-amy")
    assert await _set_mode(h, "auto", user=ALICE) == "/account?ok=mode_auto"
    assert h.db.get_user("user-1")["approval_mode"] == "auto"
    assert h.db.get_user("user-2")["approval_mode"] is None

    # Amy (manual by default): her assistant cannot apply, whatever Alice chose.
    pa = structured(
        await call(
            h,
            amy,
            "propose_deck_changes",
            {"deck_id": "43", "changes": [{"action": "add", "card_name": "Opt"}]},
        )
    )
    assert pa["approval_mode"] == "manual" and pa["assistant_may_apply"] is False
    out = structured(await call(h, amy, "apply_proposal", {"proposal_id": pa["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required" and ark.patches == []
    assert structured(await call(h, amy, "account_status"))["approval_mode"] == "manual"

    # Alice (auto): her assistant applies her own proposal, and only hers.
    pb = structured(await _adds(h, alice, 1))
    assert pb["approval_mode"] == "auto" and pb["assistant_may_apply"] is True
    a = structured(await call(h, alice, "apply_proposal", {"proposal_id": pb["proposal_id"]}))
    sent = len(ark.patches)
    assert a["ok"] and a["state"] == "applied" and sent >= 1
    # Alice's auto mode does not let her assistant touch Amy's proposal (it cannot even see it).
    out = structured(await call(h, alice, "apply_proposal", {"proposal_id": pa["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "not_found" and len(ark.patches) == sent
    assert (
        structured(await call(h, amy, "get_proposal", {"proposal_id": pa["proposal_id"]}))["state"]
        == "pending"
    )

    # Amy choosing semi changes Amy only.
    assert await _set_mode(h, "semi", user=AMY) == "/account?ok=mode_semi"
    assert h.db.get_user("user-1")["approval_mode"] == "auto"
    assert h.db.get_user("user-2")["approval_mode"] == "semi"
    assert structured(await call(h, amy, "account_status"))["approval_mode"] == "semi"
    assert structured(await call(h, alice, "account_status"))["approval_mode"] == "auto"
    # The audit rows name the member who chose, nobody else.
    assert [
        x["detail"]["to"] for x in h.db.audit_for_user("user-2", 50) if x["event"] == "approval_mode_set"
    ] == ["semi"]
    assert [
        x["detail"]["to"] for x in h.db.audit_for_user("user-1", 50) if x["event"] == "approval_mode_set"
    ] == ["auto"]


async def test_deleting_my_data_forgets_the_mode(stack: Stack) -> None:
    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "auto")
    b = Browser(h)
    await b.login()
    r = await b.http.post(
        "/account", data={"csrf": await b.csrf(), "action": "delete_data", "confirm": "yes"}
    )
    assert r.status_code == 303
    await b.aclose()
    user = h.db.get_user("user-1")
    assert user is None or user.get("approval_mode") is None


# -- 0.7.24: one source for the approval-mode wording (bug 6) ---------------------------------


def _flat(text: str) -> str:
    import html

    return re.sub(r"\s+", " ", html.unescape(text))


def test_the_low_risk_wording_is_the_list_risk_of_checks() -> None:
    """Every row kind risk_of accepts as low risk has its word in the wording, and only those."""
    assert set(modes.LOW_RISK_ROW_WORDS) == modes.LOW_RISK_ROW_KINDS
    for word in modes.LOW_RISK_ROW_WORDS.values():
        assert word in modes.LOW_RISK_CHANGES, word
    for kind in ("label", "mana_value", "change", "category", "finish", "printing"):
        rows = [{"kind": kind, "name": "Sol Ring", "before": 1, "after": 1}]
        assert modes.risk_of("edit", rows)[0] == "low", kind
        assert modes.risk_of("collection", rows)[0] == "low", kind
    for text in (modes.low_risk_text(), modes.mode_help("semi")):
        assert "color tag" in text and "custom mana value" in text and "collection" in text


def test_the_account_actions_wording_names_every_account_action() -> None:
    """Each action of actions.py is named in the member-only wording (collaborators included), and
    every mode's help says these wait, matching MEMBER_ONLY."""
    from mtg_gateway.actions import LABELS

    words = {
        "deck_vote": "likes",
        "deck_bookmark": "bookmarks",
        "follow": "follows",
        "comment_post": "comments",
        "comment_edit": "comments",
        "comment_delete": "comments",
        "comment_vote": "votes",
        "create_folder": "new folders",
        "collaborator_add": "collaborator changes",
        "collaborator_remove": "collaborator changes",
        "delete_deck": "deleting a deck",
    }
    assert set(words) == set(LABELS)
    for action, word in words.items():
        assert word in modes.MEMBER_ONLY_TEXT, action
        assert modes.risk_of("action", [{"action": action}])[0] in modes.MEMBER_ONLY
    for mode in modes.MODES:
        assert "Archidekt account" in modes.mode_help(mode) and "deleting a deck" in modes.mode_help(mode)


async def test_the_account_page_and_guide_use_the_one_wording(stack: Stack) -> None:
    from mtg_gateway.guide import guide_body

    h = stack.h
    await linked_user(stack)
    b = Browser(h)
    try:
        await b.login()
        page = _flat((await b.http.get("/account")).text)
    finally:
        await b.aclose()
    guide = _flat(guide_body(site="Gateway", writes_enabled=True, has_collection=True, max_rows=3))
    for text in (page, guide):
        assert modes.HIGH_RISK_TEXT in text and modes.MEMBER_ONLY_TEXT in text
    assert modes.low_risk_text(5) in page and modes.low_risk_text(3) in guide
    assert "Account action or deck deletion" in guide  # the mode table has the column (bug 6)


@pytest.mark.parametrize(
    ("doc", "low", "member_only"),
    [
        ("CAPABILITIES.md", True, True),
        ("OPERATIONS.md", True, True),
        ("CONNECT.md", False, True),
        ("ONBOARDING.md", False, True),
    ],
)
def test_docs_quote_the_one_wording(doc: str, low: bool, member_only: bool) -> None:
    from pathlib import Path

    text = _flat((Path(__file__).resolve().parents[1] / "docs" / doc).read_text(encoding="utf-8"))
    if low:
        assert modes.low_risk_text(modes.DEFAULT_MAX_ROWS) in text and modes.HIGH_RISK_TEXT in text
    if member_only:
        assert modes.MEMBER_ONLY_TEXT in text
    assert modes.LOW_RISK_CHANGES in text


def test_the_low_risk_texts_say_which_commander_changes_wait() -> None:
    """A copy more or less of a commander with one row is low risk, so the texts don't say
    "never the commander"; they name what waits: a change to which cards are the commanders, and
    fewer copies of a commander that has another row in the deck (both tested above)."""
    for text in (modes.low_risk_text(), modes.mode_help("semi")):
        assert "never the commander" not in text
        assert "never a change to which cards are the commanders" in text
        assert "fewer copies of a commander that has another row in the deck" in text


def test_the_semi_auto_notice_names_what_semi_auto_applies() -> None:
    """The Account page's notice after choosing Semi-auto names both kinds of change Semi-auto applies
    by itself (a clone is low risk too)."""
    from mtg_gateway.pages import OK_MESSAGES

    assert OK_MESSAGES["mode_semi"] == (
        "Saved: your assistant applies low-risk changes without asking (a small deck or collection edit, "
        "or a private copy of a deck) and asks for everything else."
    )
    assert "a private copy of a deck" in modes.mode_help("semi")


AESI = "Aesi, Tyrant of Gyre Strait"  # deck 42's commander


def commander_moves(zone: str) -> dict[str, list[dict]]:
    return {
        "card into the zone": [{"action": "set_category", "card_name": "Sol Ring", "category": zone}],
        "commander out of the zone": [{"action": "set_category", "card_name": AESI, "category": "Creatures"}],
        "commander removed": [{"action": "remove", "card_name": AESI}],
        "card added in the zone": [{"action": "add", "card_name": "Opt", "category": zone}],
    }


COMMANDER_MOVES = commander_moves("Commander")


def _rename_zone(ark, zone: str) -> None:
    """Give deck 42's commander zone (its premier category) another name, as a deck may."""
    deck = ark.decks[42]
    for c in deck["categories"]:
        if c["name"] == "Commander":
            c["name"] = zone
    for c in deck["cards"]:
        c["categories"] = [zone if x == "Commander" else x for x in (c.get("categories") or [])] or None


def _commanders(ark) -> list[str]:
    deck = ark.decks[42]
    zone = {c["name"] for c in deck["categories"] if c.get("isPremier")} | {"Commander"}
    return [
        c["card"]["oracleCard"]["name"] for c in deck["cards"] if zone.intersection(c.get("categories") or [])
    ]


@pytest.mark.parametrize("zone", ["Commander", "Oathbreaker"])
@pytest.mark.parametrize("shape", list(COMMANDER_MOVES))
@pytest.mark.parametrize("mode", ["auto", "semi"])
async def test_every_commander_change_waits_for_the_members_press(
    stack: Stack, mode: str, shape: str, zone: str
) -> None:
    """Not only set_commander: moving a card into or out of the commander zone, removing the
    commander and adding a card in the zone change the commanders too, so they wait in Full auto
    and Semi-auto on every assistant path (the tool and the API), and the member can still apply
    them. The zone is every category the deck marks premier, whatever its name."""
    h, ark = stack.h, stack.ark
    if zone != "Commander":
        _rename_zone(ark, zone)
    token = await linked_user(stack)
    await _set_mode(h, mode)
    before = _commanders(ark)
    assert before == [AESI]
    p = structured(
        await call(
            h, token, "propose_deck_changes", {"deck_id": "42", "changes": commander_moves(zone)[shape]}
        )
    )
    assert p["ok"] and p["state"] == "pending", p
    assert "commander" in [r.get("kind") for r in p["rows"]]
    assert p["risk"] == "high" and p["assistant_may_apply"] is False
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required"
    r = await h.http.post(
        f"/api/v1/proposals/{p['proposal_id']}/apply", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403 and r.json()["error"] == "browser_required"
    assert ark.patches == [] and _commanders(ark) == before
    # the member's own press applies it
    b = Browser(h)
    try:
        await b.login()
        r = await b.http.post(
            f"/api/v1/proposals/{p['proposal_id']}/apply",
            headers={"X-CSRF-Token": await b.csrf()},
            json={"version": p["version"]},
        )
        assert r.status_code == 200 and r.json()["state"] == "applied", r.text
    finally:
        await b.aclose()
    assert _commanders(ark) != before


def _second_commander_row_first(ark) -> None:
    """Give deck 42's commander a second, main-deck row listed before its commander-zone row."""
    deck = ark.decks[42]
    zone_row = next(c for c in deck["cards"] if c["card"]["oracleCard"]["name"] == AESI)
    ark.next_rel_id += 1
    other = {**json.loads(json.dumps(zone_row)), "id": ark.next_rel_id, "categories": ["Creatures"]}
    deck["cards"].insert(deck["cards"].index(zone_row), other)


@pytest.mark.parametrize(
    "change",
    [
        {"action": "set_quantity", "card_name": AESI, "quantity": 1},
        {"action": "remove", "card_name": AESI, "quantity": 1},
    ],
)
@pytest.mark.parametrize("mode", ["auto", "semi"])
async def test_a_lowered_count_on_a_commander_with_two_rows_waits_and_keeps_the_commander(
    stack: Stack, mode: str, change: dict
) -> None:
    """The commander has a main-deck row listed before its commander-zone row. Taking one copy
    away waits for the member's press with a commander row, and once applied the commander-zone
    row is the one kept."""
    h, ark = stack.h, stack.ark
    _second_commander_row_first(ark)
    token = await linked_user(stack)
    await _set_mode(h, mode)
    assert _commanders(ark) == [AESI]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    assert p["ok"] and p["state"] == "pending", p
    assert "commander" in [r.get("kind") for r in p["rows"]]
    assert p["risk"] == "high" and p["assistant_may_apply"] is False
    # the commander stays, and every text says what changes rather than "changes the commander"
    stays = f"{AESI} stays in the commander zone, now with 1 copy. Its other rows in the deck are removed."
    assert p["diff"].splitlines() == [f"2 -> 1 {AESI}", stays], p["diff"]
    assert p["risk_reason"] == "changes the commander's copies", p
    assert modes.hand_edit_confirm("edit", p["rows"]) == "This changes the copies of the deck's commander."
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required"
    assert ark.patches == []
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
        text = html.unescape(page)
        assert stays in text
        row = text[text.rindex("<li", 0, text.index(stays)) : text.index("</li>", text.index(stays))]
        assert "→" not in row and "Commander" in row
        r = await b.http.post(
            f"/api/v1/proposals/{p['proposal_id']}/apply",
            headers={"X-CSRF-Token": await b.csrf()},
            json={"version": p["version"]},
        )
        assert r.status_code == 200 and r.json()["state"] == "applied", r.text
    finally:
        await b.aclose()
    rows = [c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == AESI]
    assert [(c["quantity"], c["categories"]) for c in rows] == [(1, ["Commander"])]


async def test_the_commander_zone_keeps_the_new_count_and_the_maybeboard_row_stays(stack: Stack) -> None:
    """The commander-stays text is what the write does: the card's other rows in the deck go, its
    commander-zone row takes the new count (more copies than before when other rows held them),
    and its Maybeboard row is left alone."""
    h, ark = stack.h, stack.ark
    _second_commander_row_first(ark)
    deck = ark.decks[42]
    next(c for c in deck["cards"] if c["categories"] == ["Creatures"])["quantity"] = 2  # 3 in all
    ark.add_side_row(42, AESI)
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    change = {"action": "set_quantity", "card_name": AESI, "quantity": 2}
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    stays = f"{AESI} stays in the commander zone, now with 2 copies. Its other rows in the deck are removed."
    assert stays in p["diff"].splitlines(), p["diff"]
    b = Browser(h)
    try:
        await b.login()
        r = await b.http.post(
            f"/api/v1/proposals/{p['proposal_id']}/apply",
            headers={"X-CSRF-Token": await b.csrf()},
            json={"version": p["version"]},
        )
        assert r.status_code == 200 and r.json()["state"] == "applied", r.text
    finally:
        await b.aclose()
    rows = sorted(
        (c["quantity"], c["categories"])
        for c in ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == AESI
    )
    assert rows == [(1, ["Maybeboard"]), (2, ["Commander"])], rows


async def test_a_commander_zone_row_in_the_maybeboard_still_waits_and_says_what_goes(stack: Stack) -> None:
    """A Maybeboard row that also carries the commander zone's category, with another Maybeboard
    row of the same card: taking a copy away still waits with a commander row, which says the
    maybeboard or sideboard rows go (the deck's own rows are left alone)."""
    h, ark = stack.h, stack.ark
    ark.add_side_row(42, "Sol Ring")
    ark.add_side_row(42, "Sol Ring")
    side = [c for c in ark.decks[42]["cards"] if c["categories"] == ["Maybeboard"]]
    side[1]["categories"] = ["Maybeboard", "Commander"]
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    change = {"action": "set_quantity", "card_name": "Sol Ring", "quantity": 1, "zone": "side"}
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    assert p["ok"] and p["state"] == "pending", p
    assert "commander" in [r.get("kind") for r in p["rows"]]
    assert p["risk"] == "high" and p["assistant_may_apply"] is False
    stays = (
        "Sol Ring stays in the commander zone, now with 1 copy. "
        "Its other rows in the maybeboard or sideboard are removed."
    )
    assert stays in p["diff"].splitlines(), p["diff"]
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and ark.patches == []


@pytest.mark.parametrize("name", ["commander", "COMMANDER"])
async def test_a_move_into_the_commander_zone_under_another_case_waits(stack: Stack, name: str) -> None:
    """A category that differs from the commander zone's name only in case counts as that zone
    for approvals, so the move waits rather than applying."""
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    changes = [{"action": "set_category", "card_name": "Sol Ring", "category": name}]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert "commander" in [r.get("kind") for r in p["rows"]], p
    assert p["assistant_may_apply"] is False


async def test_a_category_move_that_leaves_the_commanders_alone_still_applies_in_auto(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    changes = [
        {"action": "set_category", "card_name": "Sol Ring", "category": "Ramp"},
        {"action": "add", "card_name": AESI, "category": "Commander"},  # one more copy, still the commander
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert "commander" not in [r.get("kind") for r in p["rows"]]
    assert p["assistant_may_apply"] is True


async def _assert_waits_as_a_commander_change(stack: Stack, token: str, changes: list[dict]) -> dict:
    """The proposal is high risk with a commander row, the assistant may not apply it, and
    applying it as the assistant writes nothing."""
    h, ark = stack.h, stack.ark
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert p["ok"], p
    assert "commander" in [r.get("kind") for r in p["rows"]], p["rows"]
    assert p["risk"] == "high" and p["assistant_may_apply"] is False, p
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and ark.patches == [], out
    return p


@pytest.mark.parametrize("mode", ["auto", "semi"])
@pytest.mark.parametrize(
    "categories",
    [["Maybeboard", "Commander"], ["Sideboard", "Commander"], ["Commander", "Maybeboard"]],
)
async def test_any_category_change_on_the_commanders_row_waits(
    stack: Stack, mode: str, categories: list[str]
) -> None:
    """Moving the only commander's row to new categories that keep "Commander" waits for the
    member's press in both modes: with an excluded primary category it takes the commander out
    of the deck, and any category change on a commander-zone row counts as a commander change."""
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, mode)
    change = {
        "action": "set_category",
        "card_name": AESI,
        "category": categories[0],
        "categories": categories,
    }
    p = await _assert_waits_as_a_commander_change(stack, token, [change])
    commander = next(r for r in p["rows"] if r["kind"] == "commander")
    if categories[0] == "Commander":  # still in the deck and still the commander
        assert commander["before"] == commander["after"] == AESI
        moved = f"{AESI}'s categories change from Commander to {', '.join(categories)}."
        assert any(line.startswith(moved) for line in p["diff"].splitlines()), p["diff"]
        assert p["risk_reason"] == "changes the categories of a row in the commander zone"
    else:  # out of the deck: no commander is left
        assert (commander["before"], commander["after"]) == (AESI, "(none)")
        assert p["risk_reason"] == "changes the commander"


@pytest.mark.parametrize("mode", ["auto", "semi"])
@pytest.mark.parametrize("new", [["Command Zone", "Ramp"], ["command zone"]])
async def test_a_category_change_in_a_renamed_commander_zone_waits(
    stack: Stack, mode: str, new: list[str]
) -> None:
    """A deck whose commander zone has another name (a premier category): any category change on
    a row in it waits, a second category or only a change of case included."""
    h, ark = stack.h, stack.ark
    deck = ark.decks[42]
    deck["categories"].append({"name": "Command Zone", "isPremier": True, "includedInDeck": True})
    next(c for c in deck["cards"] if c["card"]["oracleCard"]["name"] == AESI)["categories"] = ["Command Zone"]
    token = await linked_user(stack)
    await _set_mode(h, mode)
    change = {"action": "set_category", "card_name": AESI, "category": new[0], "categories": new}
    await _assert_waits_as_a_commander_change(stack, token, [change])


@pytest.mark.parametrize("mode", ["auto", "semi"])
async def test_a_maybeboard_commander_moved_into_the_deck_waits(stack: Stack, mode: str) -> None:
    """The reverse: a maybeboard row carrying "Commander" is no commander, and moving it into the
    deck makes it one, so it waits in both modes."""
    h, ark = stack.h, stack.ark
    ark.add_side_row(42, "Sol Ring")
    side = next(c for c in ark.decks[42]["cards"] if c["categories"] == ["Maybeboard"])
    side["categories"] = ["Maybeboard", "Commander"]
    token = await linked_user(stack)
    await _set_mode(h, mode)
    change = {
        "action": "set_category",
        "card_name": "Sol Ring",
        "category": "Commander",
        "categories": ["Commander"],
        "zone": "side",
    }
    p = await _assert_waits_as_a_commander_change(stack, token, [change])
    commander = next(r for r in p["rows"] if r["kind"] == "commander")
    assert (commander["before"], commander["after"]) == (AESI, f"{AESI}, Sol Ring")


@pytest.mark.parametrize("mode", ["auto", "semi"])
async def test_an_add_with_the_commander_zone_outside_the_deck_waits(stack: Stack, mode: str) -> None:
    """An add whose primary category is the maybeboard and which also carries "Commander" makes
    no commander, but it still counts as a commander change and waits."""
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, mode)
    change = {
        "action": "add",
        "card_name": "Sol Ring",
        "category": "Maybeboard",
        "categories": ["Maybeboard", "Commander"],
    }
    p = await _assert_waits_as_a_commander_change(stack, token, [change])
    added = "This adds Sol Ring with a commander-zone category (Maybeboard, Commander), so it counts"
    assert any(line.startswith(added) for line in p["diff"].splitlines()), p["diff"]


async def test_a_raised_commander_with_another_row_says_the_rows_fold(stack: Stack) -> None:
    """Raising a commander that has a second row in the deck stays low risk, and the review says
    what the write does: the commander-zone row takes the new count and the other row goes."""
    h, ark = stack.h, stack.ark
    _second_commander_row_first(ark)
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    change = {"action": "set_quantity", "card_name": AESI, "quantity": 3}
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    assert p["risk"] == "low" and "commander" not in [r.get("kind") for r in p["rows"]], p
    fold = f"{AESI} stays in the commander zone, now with 3 copies. Its other rows in the deck are removed."
    assert any(line.startswith("2 -> 3") and line.endswith(fold) for line in p["diff"].splitlines()), p[
        "diff"
    ]
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
    finally:
        await b.aclose()
    assert html.escape(fold) in page
