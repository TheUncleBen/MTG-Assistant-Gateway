"""Approval modes (modes.py): manual by default, semi and auto chosen per member on the Account
page only, risk tiers from the review rows, and strict per-member isolation."""

from __future__ import annotations

import pytest

from mtg_gateway import modes
from mtg_gateway.approve import APPROVAL_META_KEY

from .conftest import FakeIdP, Harness, make_settings, running, sse_json
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, Stack, _client, call, linked_user, structured

AMY = {"sub": "user-2", "email": "amy@example.com", "name": "Amy", "preferred_username": "amy"}
ALICE = {"sub": "user-1", "email": "alice@example.com", "name": "Alice", "preferred_username": "alice"}
NAMES = ["Arcane Signet", "Rampant Growth", "Sol Ring", "Forest", "Island", "Opt", "Swamp"]


def _stack_with(tmp_path, idp: FakeIdP, **over):
    ark = FakeArchidekt()
    settings = make_settings(tmp_path, writes_enabled=True, archidekt_base="https://ark.test/api", **over)
    return Harness(settings, idp, archidekt=_client(settings, ark)), ark


@pytest.fixture
async def stack(tmp_path, idp: FakeIdP):
    """The shipped defaults: manual for everyone who has not chosen, no cap."""
    h, ark = _stack_with(tmp_path, idp)
    async with running(h):
        yield Stack(h, ark)


@pytest.fixture
async def capped(tmp_path, idp: FakeIdP):
    h, ark = _stack_with(tmp_path, idp, approval_mode_default="auto", approval_mode_max="semi")
    async with running(h):
        yield Stack(h, ark)


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
    assert not {n for n in names if "mode" in n or "approval" in n and n != "confirm_proposal"}
    for path in ("/api/v1/me", "/api/v1/account", "/api/v1/me/approval_mode"):
        r = await h.http.post(
            path, json={"approval_mode": "auto"}, headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code in (404, 405), (path, r.status_code)
    assert h.db.get_user("user-1")["approval_mode"] is None


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
    assert p["risk"] == "high" and p["assistant_may_apply"] is True and "every change" in p["next_step"]
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    sent = len(ark.patches)
    assert a["ok"] and a["state"] == "applied" and a["snapshot_id"] and sent >= 1
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
