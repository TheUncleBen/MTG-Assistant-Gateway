"""The member's own custom cards on the Deck page (the UI pass over custom cards): drawn in the
stacks by category or type and marked Custom, counted in the stack's total, left out of the deck's
own card rows; the page's own save of a count, a move or a removal through
``POST /api/v1/decks/{id}/custom_cards`` (proposed, applied as the member's press, the deck read
back) and the answer the page draws from; a big removal asks first; an assistant's token is
refused. Fakes only."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import re

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED
from .test_browse_collection import api, linked
from .test_deck_custom_cards import _custom, _seed
from .test_round4_data import _app_token

stack_options = stack_with(SHIPPED)


def _in_deck(ark, *rows: tuple[int, int, str, list[str] | None]) -> None:  # noqa: ANN001
    """Custom rows in deck 42: (relation id, custom card id, name, categories)."""
    ark.decks[42]["customCards"] = [
        {"id": rel, "quantity": 1, "categories": cats, "card": _custom(cid, name)}
        for rel, cid, name, cats in rows
    ]


def _rows(ark) -> dict[int, dict]:  # noqa: ANN001
    return {r["card"]["id"]: r for r in ark.decks[42].get("customCards") or []}


async def test_custom_cards_are_drawn_in_their_stacks_marked_custom(stack: Stack) -> None:
    ark = stack.ark
    _seed(ark)
    _in_deck(ark, (3100, 41, "Grizzled Mentor", ["Commander"]), (3101, 44, "Old Ranger", None))
    ark.decks[42]["customCards"][1]["quantity"] = 3
    b = await linked(stack)
    try:
        page = (await b.http.get("/decks/42")).text
        # one row each, with what the page's script needs: the custom card id, the row, the count
        mentor = re.search(r"<li class='row custom'[^>]*data-card='Grizzled Mentor'[^>]*>", page)
        assert mentor, page
        row = mentor.group(0)
        assert "data-custom='1'" in row and "data-custom-id='41'" in row and "data-rel='3100'" in row
        assert "data-qty='1'" in row and "data-cat='Commander'" in row and "data-zone='main'" in row
        # marked Custom, with the one line where custom cards live
        assert "class='custom-badge'" in page and "One of your custom cards (My lists › Custom cards)" in page
        assert page.count("class='custom-badge'") == 2
        # filed by category: Grizzled Mentor under Commander, Old Ranger (no category) under its type
        commander = re.search(r"<section class='stack'[^>]*data-group='Commander'.*?</section>", page, re.S)
        assert commander and "Grizzled Mentor" in commander.group(0)
        ranger = re.search(r"<li class='row custom'[^>]*data-card='Old Ranger'[^>]*>", page).group(0)
        assert "data-qty='3'" in ranger and "data-custom-id='44'" in ranger
        # the type grouping files both by their primary type
        page_t = (await b.http.get("/decks/42?group=type")).text
        creature = re.search(r"<section class='stack'[^>]*data-group='Creature'.*?</section>", page_t, re.S)
        assert creature and "Grizzled Mentor" in creature.group(0) and "Old Ranger" in creature.group(0)
        # the picture views draw them too
        page_s = (await b.http.get("/decks/42?view=stacks")).text
        assert page_s.count("class='c custom'") == 2
    finally:
        await b.aclose()


async def test_the_page_saves_a_count_a_move_and_a_removal_of_a_custom_card(stack: Stack) -> None:
    ark = stack.ark
    _seed(ark)
    _in_deck(ark, (3100, 41, "Grizzled Mentor", ["Commander"]), (3101, 44, "Old Ranger", None))
    b = await linked(stack)
    try:
        # the count
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/custom_cards",
            {"changes": [{"action": "set_quantity", "custom_card_id": 44, "quantity": 4}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["ok"] is True and d["applied"] is True
        assert _rows(ark)[44]["quantity"] == 4
        rows = {x["custom_card_id"]: x for x in d["custom_rows"]}
        assert rows[44]["qty"] == 4 and rows[44]["relation_id"] == 3101 and rows[44]["name"] == "Old Ranger"
        assert rows[41]["categories"] == ["Commander"]
        assert "banner_html" in d and "checks_html" in d and "legality_html" in d and "stats" in d
        # the proposal is the member's own, in History as applied
        listed = (await b.http.get("/api/v1/proposals")).json()["proposals"]
        assert listed[0]["id"] == d["proposal_id"] and listed[0]["state"] == "applied"
        assert listed[0]["kind"] == "custom_cards"
        # a move: the count as it is, the new category
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/custom_cards",
            {
                "changes": [
                    {"action": "set_quantity", "custom_card_id": 44, "quantity": 4, "category": "Ramp"}
                ]
            },
        )
        assert r.status_code == 201, r.text
        assert _rows(ark)[44]["categories"] == ["Ramp"]
        rows = {x["custom_card_id"]: x for x in r.json()["custom_rows"]}
        assert rows[44]["categories"] == ["Ramp"]
        # the removal: the row is gone from the answer and from Archidekt
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/custom_cards",
            {"changes": [{"action": "remove", "custom_card_id": 44}]},
        )
        assert r.status_code == 201, r.text
        assert [x["custom_card_id"] for x in r.json()["custom_rows"]] == [41]
        assert 44 not in _rows(ark)
        # and the Undo: added back with its count and category
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/custom_cards",
            {"changes": [{"action": "add", "custom_card_id": 44, "quantity": 4, "category": "Ramp"}]},
        )
        assert r.status_code == 201, r.text
        assert _rows(ark)[44]["quantity"] == 4 and _rows(ark)[44]["categories"] == ["Ramp"]
        # nothing to change is refused plainly
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/custom_cards",
            {"changes": [{"action": "set_quantity", "custom_card_id": 44, "quantity": 4}]},
        )
        assert r.status_code == 400 and "nothing would change" in r.text
    finally:
        await b.aclose()


async def test_a_big_removal_asks_first_and_the_follow_up_names_the_proposal(stack: Stack) -> None:
    ark = stack.ark
    _seed(ark)
    _in_deck(ark, (3101, 44, "Old Ranger", None))
    ark.decks[42]["customCards"][0]["quantity"] = 20
    b = await linked(stack)
    try:
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/custom_cards",
            {"changes": [{"action": "remove", "custom_card_id": 44}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is False and d["needs_confirm"] is True
        assert d["why"] == "This removes 20 cards from the deck."
        assert _rows(ark)[44]["quantity"] == 20  # nothing sent
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/custom_cards",
            {"proposal_id": d["proposal_id"], "version": d["version"]},
        )
        assert r.status_code == 201, r.text
        assert r.json()["applied"] is True and 44 not in _rows(ark)
        # a proposal of another kind is not taken here
        r = await api(b, "POST", "/api/v1/decks/42/custom_cards", {"proposal_id": "nope", "version": 1})
        assert r.status_code in (400, 404)
    finally:
        await b.aclose()


async def test_the_save_is_the_browsers_own_and_waits_when_writes_are_off(stack: Stack) -> None:
    ark = stack.ark
    _seed(ark)
    _in_deck(ark, (3101, 44, "Old Ranger", None))
    b = await linked(stack)
    try:
        token, _cid = await _app_token(stack.h, "custom-cards-test")
        r = await stack.h.http.post(
            "/api/v1/decks/42/custom_cards",
            json={"changes": [{"action": "remove", "custom_card_id": 44}]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403 and r.json()["error"] == "browser_required", r.text
        object.__setattr__(stack.h.app.state.gateway.settings, "writes_enabled", False)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/custom_cards",
            {"changes": [{"action": "remove", "custom_card_id": 44}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is False and d["review_url"] == f"/proposals/{d['proposal_id']}"
        assert 44 in _rows(ark)
    finally:
        await b.aclose()
