"""The deck page's own save: ``POST /api/v1/decks/{id}/edit`` runs the proposals path (propose,
hand-edit confirmation, apply with a snapshot as the member's own press), re-reads the deck and
answers with the touched rows, fresh checks and the re-rendered Legality chip and Deck checks
panel. Browser session with the CSRF header only; a deck the linked account does not own is
refused; a big removal asks first and the follow-up names the proposal."""

from __future__ import annotations

import pytest

from mtg_gateway import api as api_module

from .test_browse_collection import Stack, api, linked, stack  # noqa: F401 - fixture
from .test_round4_data import _app_token


def _deck_card(st: Stack, name: str, *, zone: str | None = None) -> list[dict]:
    rows = [c for c in st.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == name]
    if zone == "side":
        return [c for c in rows if c["categories"] == ["Maybeboard"]]
    if zone == "main":
        return [c for c in rows if c["categories"] != ["Maybeboard"]]
    return rows


def _legal_commander_deck(st: Stack) -> None:
    """Deck 42 as a legal 100-card Commander deck: the CSV fixture has no supertypes, so the
    commander gets Legendary and the basic lands Basic, as Archidekt's deck JSON carries them."""
    st.ark.decks[42]["deckFormat"] = 3
    for name, sup in (("Aesi, Tyrant of Gyre Strait", "Legendary"), ("Forest", "Basic"), ("Island", "Basic")):
        for row in _deck_card(st, name):
            row["card"]["oracleCard"]["superTypes"] = [sup]


async def test_quantity_remove_and_category_edits_answer_with_fresh_rows_and_checks(stack: Stack) -> None:  # noqa: F811
    stack.ark.decks[42]["deckFormat"] = 3  # Commander: a 100-card deck with legality to report
    b = await linked(stack)
    try:
        patches = len(stack.ark.patches)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["ok"] and d["applied"] is True and d["snapshot_id"] and d["stale"] is False
        # the quick edit keeps the gateway snapshot but makes no extra copy on Archidekt (D-02)
        assert d["result"]["result"]["archidekt_backup"] == "skipped", d["result"]
        assert len(stack.ark.patches) > patches
        assert d["rows"] == [
            {
                "name": "Cultivate",
                "qty": 2,
                "zone": "main",
                "categories": d["rows"][0]["categories"],
                "relation_id": d["rows"][0]["relation_id"],
                "finish": "Normal",
                "label": "",
                "mana_value": None,
            }
        ]
        assert _deck_card(stack, "Cultivate")[0]["quantity"] == 2
        # the checks are computed from the re-read deck: 101 cards now, so the deck size check fails
        st = d["stats"]
        assert st["card_count"] == 101 and st["checks"]["deck_size"]["ok"] is False
        # every card is legal, but a 101-card Commander deck is not: the chip says so, as on Archidekt
        assert st["legal"] is False and st["problems"] == [] and st["format"] == "commander"
        assert "deck has 101 cards" in " ".join(st["checks"]["problems"])
        assert "class='legal bad'" in d["banner_html"] and "deck has 101 cards" in d["banner_html"]
        assert d["checks_html"].startswith("<div class='checks'><h3>Deck checks</h3>")
        assert "101 of 100" in d["checks_html"] and "class='bad'" in d["checks_html"]
        assert "Not legal in Commander" in d["legality_html"]
        # the proposal is in History as applied, with its snapshot
        listed = (await b.http.get("/api/v1/proposals")).json()["proposals"]
        assert listed[0]["id"] == d["proposal_id"] and listed[0]["state"] == "applied"

        # remove: the card leaves the deck, so no row comes back for it and the size is right again
        r = await api(
            b, "POST", "/api/v1/decks/42/edit", {"changes": [{"action": "remove", "card_name": "Cultivate"}]}
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True and d["rows"] == []
        assert _deck_card(stack, "Cultivate") == []
        assert d["stats"]["card_count"] == 99 and d["stats"]["checks"]["deck_size"]["ok"] is False

        # set_category: the row's categories change and the row says so
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_category", "card_name": "Acidic Slime", "category": "Removal"}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True
        assert d["rows"] == [
            {
                "name": "Acidic Slime",
                "qty": 1,
                "zone": "main",
                "categories": ["Removal"],
                "relation_id": d["rows"][0]["relation_id"],
                "finish": "Normal",
                "label": "",
                "mana_value": None,
            }
        ]
        assert _deck_card(stack, "Acidic Slime")[0]["categories"] == ["Removal"]
    finally:
        await b.aclose()


async def test_side_zone_edits_target_the_maybeboard_row(stack: Stack) -> None:  # noqa: F811
    stack.ark.add_side_row(42, "Sol Ring", 1)
    b = await linked(stack)
    try:
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Sol Ring", "quantity": 3, "zone": "side"}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True and d["stale"] is False
        rows = {(x["zone"], x["qty"]) for x in d["rows"]}
        assert ("side", 3) in rows, d["rows"]
        assert ("main", 1) in rows, d["rows"]  # the deck's own Sol Ring row is untouched
        assert d["stats"]["side_count"] == 3 and d["stats"]["card_count"] == 100
        assert _deck_card(stack, "Sol Ring", zone="side")[0]["quantity"] == 3
        assert _deck_card(stack, "Sol Ring", zone="main")[0]["quantity"] == 1
        # a maybeboard card moved into a counted category joins the deck (zone side names the row)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {
                "changes": [
                    {"action": "set_category", "card_name": "Sol Ring", "category": "Ramp", "zone": "side"}
                ]
            },
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True
        assert [x["zone"] for x in d["rows"]] == ["main", "main"] and ["Ramp"] in [
            x["categories"] for x in d["rows"]
        ]
        assert d["stats"]["side_count"] == 0 and d["stats"]["card_count"] == 103
    finally:
        await b.aclose()


async def test_a_deck_changed_on_archidekt_is_judged_as_it_is_now(stack: Stack) -> None:  # noqa: F811
    """T-040: the deck is legal, then a card is added on Archidekt (outside the gateway) to make
    101: the deck page and the API both say it is not legal, though every card still is."""
    _legal_commander_deck(stack)
    b = await linked(stack)
    try:
        page = await b.http.get("/decks/42", headers={"Accept": "text/html"})
        assert page.status_code == 200 and "Size: 100" in page.text
        assert "class='legal ok'" in page.text and "Legal in Commander" in page.text
        before = (await b.http.get("/api/v1/decks/42/stats")).json()["stats"]
        assert before["card_count"] == 100 and before["checks"]["legal"] is True

        _deck_card(stack, "Forest")[0]["quantity"] += 1  # the owner's edit on archidekt.com
        stack.ark.decks[42]["updatedAt"] = "2026-10-10T12:00:00Z"

        page = await b.http.get("/decks/42", headers={"Accept": "text/html"})
        assert "Size: 101" in page.text and "class='legal ok'" not in page.text
        assert "class='legal bad'" in page.text and "deck has 101 cards; commander wants 100" in page.text
        assert "Not legal in Commander" in page.text and "101 of 100" in page.text
        after = (await b.http.get("/api/v1/decks/42/stats")).json()["stats"]
        assert after["card_count"] == 101 and after["legality_problems"] == []
        assert after["checks"]["legal"] is False and after["checks"]["ok"] is False
        assert after["checks"]["legal_problems"] == ["deck has 101 cards; commander wants 100"]
    finally:
        await b.aclose()


async def test_big_removal_asks_first_then_the_proposal_id_applies_it(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        deck = stack.ark.decks[42]
        names = [c["card"]["oracleCard"]["name"] for c in deck["cards"] if c["categories"] != ["Commander"]][
            :9
        ]
        changes = [{"action": "remove", "card_name": n} for n in names]
        before = len(stack.ark.patches)
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": changes})
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is False and d["needs_confirm"] is True and "cards" in d["why"]
        assert "rows" not in d and len(stack.ark.patches) == before  # nothing sent before the answer
        # the follow-up names the proposal (the page's "Save anyway") and gets the full answer
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": d["proposal_id"]})
        assert r.status_code == 201, r.text
        a = r.json()
        assert a["applied"] is True and a["proposal_id"] == d["proposal_id"] and a["rows"] == []
        assert a["stats"]["card_count"] == 91 and len(stack.ark.patches) > before
        # a proposal of another kind or deck is refused by the follow-up
        other = await api(b, "POST", "/api/v1/proposals", {"kind": "clone", "deck_id": "42"})
        assert other.status_code == 201
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": other.json()["proposal_id"]})
        assert r.status_code == 400 and r.json()["error"] == "invalid"
        # confirmed: true skips the question when the page already asked
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {
                "changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}],
                "confirmed": True,
            },
        )
        assert r.status_code == 201 and r.json()["applied"] is True, r.text
    finally:
        await b.aclose()


async def test_edit_needs_the_browser_session_and_the_csrf_header(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        body = {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}]}
        r = await b.http.post("/api/v1/decks/42/edit", json=body)
        assert r.status_code == 403 and r.json()["error"] == "csrf"
        r = await api(b, "POST", "/api/v1/decks/42/edit", body, csrf="0" * 64)
        assert r.status_code == 403 and r.json()["error"] == "csrf"
        # an app's bearer token proposes through /api/v1/proposals instead
        token, _cid = await _app_token(stack.h, "deck-page-test")
        r = await stack.h.http.post(
            "/api/v1/decks/42/edit", json=body, headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403 and r.json()["error"] == "browser_required", r.text
        assert _deck_card(stack, "Cultivate")[0]["quantity"] == 1
        # the body is checked like a proposal's
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": []})
        assert r.status_code == 400 and r.json()["error"] == "invalid"
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": 7})
        assert r.status_code == 400 and r.json()["error"] == "invalid"
        r = await api(b, "POST", "/api/v1/decks/42/edit", {})
        assert r.status_code == 400 and r.json()["error"] == "invalid"
    finally:
        await b.aclose()


async def test_someone_elses_deck_is_refused(stack: Stack) -> None:  # noqa: F811
    stack.ark.private.discard(43)  # amy's deck can be read, but alice does not own it
    b = await linked(stack)
    try:
        before = len(stack.ark.patches)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/43/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Acidic Slime", "quantity": 2}]},
        )
        assert r.status_code == 403 and r.json()["error"] == "forbidden", r.text
        assert len(stack.ark.patches) == before
    finally:
        await b.aclose()


async def test_a_lagging_re_read_is_read_again_and_marked_stale(
    stack: Stack,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Archidekt's read after the write still shows the old rows, the endpoint waits and reads
    once more; if it still lags, the answer says ``stale`` so the page can refetch."""
    monkeypatch.setattr(api_module, "EDIT_REREAD_DELAY", 0.01)
    b = await linked(stack)
    try:
        decks = stack.h.app.state.gateway.decks
        real = decks.get_own_deck
        reads: list[str] = []

        async def lagging(sub: str, deck_id: str):
            deck = await real(sub, deck_id)
            reads.append(deck_id)
            # the propose and the apply read the deck three times; the endpoint's own re-reads
            # (the fourth and fifth) see the old count
            if len(reads) >= 4:
                for c in deck.cards:
                    if c.name == "Cultivate":
                        c.quantity = 1
            return deck

        monkeypatch.setattr(decks, "get_own_deck", lagging)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True and d["stale"] is True, d
        assert d["rows"][0]["qty"] == 1 and len(reads) == 5, reads
        assert _deck_card(stack, "Cultivate")[0]["quantity"] == 2  # the write itself landed
    finally:
        await b.aclose()


async def test_an_assistants_pending_proposal_is_not_applied_by_the_page(stack: Stack) -> None:  # noqa: F811
    """0.7.9 gate L1: the follow-up with a proposal_id answers only the question this endpoint
    asked. A proposal an app made (pending, with its Approve card and its backup copy) is refused
    here and stays pending; nothing reaches Archidekt."""
    b = await linked(stack)
    try:
        token, _cid = await _app_token(stack.h, "assistant-test")
        before = len(stack.ark.patches)
        r = await stack.h.http.post(
            "/api/v1/proposals",
            json={
                "kind": "edit",
                "deck_id": "42",
                "changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 3}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        pid = r.json()["proposal_id"]
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": pid})
        assert r.status_code == 400 and r.json()["error"] == "invalid", r.text
        assert "assistant" in r.json()["message"] and r.json()["review_url"] == f"/proposals/{pid}"
        assert len(stack.ark.patches) == before and _deck_card(stack, "Cultivate")[0]["quantity"] == 1
        r = await api(b, "GET", f"/api/v1/proposals/{pid}")
        assert r.status_code == 200 and r.json()["state"] == "pending", r.text
        # the page's own question, once answered, cannot be answered twice
        names = [
            c["card"]["oracleCard"]["name"]
            for c in stack.ark.decks[42]["cards"]
            if c["categories"] != ["Commander"]
        ][:9]
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "remove", "card_name": n} for n in names]},
        )
        assert r.status_code == 201 and r.json()["needs_confirm"] is True, r.text
        own = r.json()["proposal_id"]
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": own})
        assert r.status_code == 201 and r.json()["applied"] is True, r.text
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": own})
        assert r.status_code == 400 and "applied" in r.json()["message"], r.text
    finally:
        await b.aclose()
