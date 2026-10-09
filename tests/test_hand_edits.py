"""A member's own edit in the app is their approval: ``apply: true`` on POST /api/v1/proposals from
the browser session applies the proposal at once (snapshot first); a big removal comes back as
``needs_confirm`` and is applied by the follow-up Apply; bearer tokens cannot use the flag."""

from __future__ import annotations

from mtg_gateway import modes

from .test_browse_collection import Stack, api, linked, stack  # noqa: F401 - fixture
from .test_round4_data import _app_token


def test_hand_edit_confirmation_rule() -> None:
    assert modes.hand_edit_confirm("edit", [{"kind": "category", "name": "Sol Ring"}]) is None
    assert modes.hand_edit_confirm("edit", [{"kind": "remove", "name": "Forest", "qty": 10}]) is None
    assert "11 cards" in (
        modes.hand_edit_confirm("edit", [{"kind": "remove", "name": "Forest", "qty": 11}]) or ""
    )
    many = [{"kind": "remove", "name": f"Card {i}", "qty": 1} for i in range(8)]
    assert "8 different cards" in (modes.hand_edit_confirm("edit", many) or "")
    assert "commander" in (modes.hand_edit_confirm("edit", [{"kind": "commander"}]) or "")
    assert modes.hand_edit_confirm("restore", [{"kind": "restore"}])
    assert modes.hand_edit_confirm("clone", [{"kind": "clone"}]) is None


async def test_small_hand_edit_applies_at_once_with_a_snapshot(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        before = len(stack.ark.patches)
        r = await api(
            b,
            "POST",
            "/api/v1/proposals",
            {
                "kind": "edit",
                "deck_id": "42",
                "changes": [{"action": "add", "card_name": "Cultivate", "quantity": 1}],
                "apply": True,
            },
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True and d["result"]["state"] == "applied" and d["result"]["snapshot_id"]
        assert len(stack.ark.patches) > before
        listed = (await b.http.get("/api/v1/proposals")).json()["proposals"]
        assert listed[0]["id"] == d["proposal_id"] and listed[0]["state"] == "applied"
    finally:
        await b.aclose()


async def test_big_removal_asks_first_and_applies_on_confirm(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        deck = stack.ark.decks[42]
        names = [c["card"]["oracleCard"]["name"] for c in deck["cards"]][:9]
        changes = [{"action": "remove", "card_name": n} for n in names]
        before = len(stack.ark.patches)
        r = await api(
            b,
            "POST",
            "/api/v1/proposals",
            {"kind": "edit", "deck_id": "42", "changes": changes, "apply": True},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is False and d["needs_confirm"] is True and "cards" in d["why"], d
        assert len(stack.ark.patches) == before  # nothing sent before the confirmation
        a = await api(b, "POST", f"/api/v1/proposals/{d['proposal_id']}/apply", {})
        assert a.status_code == 200 and a.json()["state"] == "applied", a.text
        assert len(stack.ark.patches) > before
        # confirmed: true skips the question (the page already asked)
        r2 = await api(
            b,
            "POST",
            "/api/v1/proposals",
            {"kind": "edit", "deck_id": "42", "changes": changes[:1], "apply": True, "confirmed": True},
        )
        assert r2.status_code in (201, 400, 409), r2.text  # the card may already be gone
    finally:
        await b.aclose()


async def test_bearer_tokens_cannot_apply_in_one_step(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        token, _cid = await _app_token(stack.h, "writer")
        before = len(stack.ark.patches)
        r = await b.http.post(
            "/api/v1/proposals",
            json={
                "kind": "edit",
                "deck_id": "42",
                "changes": [{"action": "add", "card_name": "Cultivate", "quantity": 1}],
                "apply": True,
                "confirmed": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert "applied" not in d and d["state"] == "pending"
        assert len(stack.ark.patches) == before
    finally:
        await b.aclose()


async def test_the_save_bar_tick_box_decides_the_extra_copy_on_archidekt(stack: Stack) -> None:  # noqa: F811
    """D-02: the member chooses per save whether the extra backup copy goes to Archidekt; the
    gateway's own snapshot is taken either way, and the assistant's applies never skip the copy."""
    b = await linked(stack)
    db = stack.h.app.state.gateway.db
    try:
        body = {
            "kind": "edit",
            "deck_id": "42",
            "changes": [{"action": "add", "card_name": "Cultivate", "quantity": 1}],
            "apply": True,
        }
        r = await api(b, "POST", "/api/v1/proposals", {**body, "archidekt_backup": False})
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["result"]["state"] == "applied", d
        assert d["result"]["result"]["archidekt_backup"] == "skipped", d["result"]
        snap = db.get_snapshot(d["result"]["snapshot_id"], "user-1")
        assert snap and not snap.get("backup_deck_id")
        before = len(stack.ark.decks)
        r = await api(b, "POST", "/api/v1/proposals", body)  # box ticked (the default)
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["result"]["state"] == "applied", d
        assert d["result"]["result"].get("archidekt_backup") != "skipped", d["result"]
        snap = db.get_snapshot(d["result"]["snapshot_id"], "user-1")
        assert snap and snap.get("backup_deck_id") and len(stack.ark.decks) == before + 1
    finally:
        await b.aclose()
