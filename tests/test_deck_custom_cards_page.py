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
        # marked Custom once a row, the mark under the name (after the name cell, as the Companion
        # word), with the one line where custom cards live; the Set cell is blank, not a second word
        assert "class='custom-badge'" in page and "A custom card, made on Archidekt" in page
        assert page.count("class='custom-badge'") == 2
        full = re.search(r"<li class='row custom'[^>]*data-card='Grizzled Mentor'.*?</li>", page, re.S)
        full = full.group(0)
        assert full.count("class='custom-badge'") == 1 and "</span><span class='custom-badge'" in full
        assert "<span class='set'></span>" in full and full.count(">Custom<") == 1
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


async def test_the_page_saves_a_color_tag_and_a_mana_value_of_a_custom_card(stack: Stack) -> None:
    """The custom card menu's Color tag… and Mana value… boxes save through the page's own endpoint,
    as the normal card menu's do, and the answer's rows carry both for the page to redraw."""
    ark = stack.ark
    _seed(ark)
    _in_deck(ark, (3100, 41, "Grizzled Mentor", ["Commander"]), (3101, 44, "Old Ranger", None))
    b = await linked(stack)
    try:
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/custom_cards",
            {"changes": [{"action": "set_quantity", "custom_card_id": 44, "label": "Have,#37d67a"}]},
        )
        assert r.status_code == 201, r.text
        assert _rows(ark)[44]["label"] == "Have,#37d67a" and _rows(ark)[44]["quantity"] == 1
        rows = {x["custom_card_id"]: x for x in r.json()["custom_rows"]}
        assert rows[44]["label"] == "Have,#37d67a" and rows[44]["mana_value"] is None
        assert rows[41]["label"] == "" and rows[41]["mana_value"] is None
        # the page draws the tag as it draws a normal card's
        page = (await b.http.get("/decks/42")).text
        ranger = re.search(r"<li class='row custom'[^>]*data-card='Old Ranger'[^>]*>", page).group(0)
        assert "data-label='Have,#37d67a'" in ranger
        # the mana value, then its own again
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/custom_cards",
            {"changes": [{"action": "set_quantity", "custom_card_id": 44, "mana_value": 3}]},
        )
        assert r.status_code == 201, r.text
        assert _rows(ark)[44]["customCmc"] == 3 and _rows(ark)[44]["label"] == "Have,#37d67a"
        rows = {x["custom_card_id"]: x for x in r.json()["custom_rows"]}
        assert rows[44]["mana_value"] == 3 and rows[44]["label"] == "Have,#37d67a"
        page = (await b.http.get("/decks/42")).text
        ranger = re.search(r"<li class='row custom'[^>]*data-card='Old Ranger'[^>]*>", page).group(0)
        assert "data-mv='3'" in ranger
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/custom_cards",
            {"changes": [{"action": "set_quantity", "custom_card_id": 44, "mana_value": None, "label": ""}]},
        )
        assert r.status_code == 201, r.text
        assert _rows(ark)[44]["customCmc"] is None and _rows(ark)[44]["label"] == ",#656565"  # no name
        rows = {x["custom_card_id"]: x for x in r.json()["custom_rows"]}
        assert rows[44]["mana_value"] is None and rows[44]["label"] == ""
    finally:
        await b.aclose()


async def test_a_custom_cards_picture_is_drawn_from_the_gateways_own_address(stack: Stack) -> None:
    """Wherever a normal card shows its picture the custom card shows its own, through the
    gateway's picture route (same origin: the page's picture sources stay as they are); its name
    and type stay drawn underneath for when there is no picture."""
    ark = stack.ark
    _seed(ark)
    ark.account.customs["alice"][0]["hasBack"] = True
    ark.account.customs["alice"][0]["backImageUrl"] = "https://cdn.example/41b.png"
    _in_deck(ark, (3100, 41, "Grizzled Mentor", ["Commander"]), (3101, 44, "Old Ranger", None))
    ark.decks[42]["customCards"][0]["card"] = ark.account.customs["alice"][0]
    b = await linked(stack)
    try:
        front = "/decks/api/custom_picture/42/41?face=front"
        # the list views: the viewer's picture and the hover picture
        page = (await b.http.get("/decks/42")).text
        mentor = re.search(
            r"<li class='row custom'[^>]*data-card='Grizzled Mentor'[^>]*>.*?</li>", page, re.S
        )
        assert mentor, page
        assert f"data-img='{front}'" in mentor.group(0)
        assert "data-back='/decks/api/custom_picture/42/41?face=back'" in mentor.group(0)
        assert f"<span class='hover'><img src='{front}' alt='' loading='lazy'></span>" in mentor.group(0)
        ranger = re.search(r"<li class='row custom'[^>]*data-card='Old Ranger'[^>]*>", page).group(0)
        assert "data-img='/decks/api/custom_picture/42/44?face=front'" in ranger
        assert "data-back=" not in ranger  # one face
        assert "https://cdn.example" not in page  # never Archidekt's address itself
        # the picture views: the picture over the name-and-type placeholder
        page_s = (await b.http.get("/decks/42?view=stacks")).text
        card = re.search(
            r"<div class='c custom'[^>]*data-card='Grizzled Mentor'[^>]*>.*?</div>", page_s, re.S
        )
        assert card, page_s
        assert "<span class='ph'>" in card.group(0) and "Creature — Human Advisor" in card.group(0)
        assert f"<img class='pic' src='{front}' alt='Grizzled Mentor' loading='lazy'>" in card.group(0)
        # the type line stops short of the Custom mark (it ran under it)
        css = (await b.http.get(re.search(r"href='(/static/deck\.css[^']*)'", page_s).group(1))).text
        assert ".deckview .c.custom .ph .ty{padding-right:4.2rem}" in css
        assert ".deckview .c.custom img.pic{position:absolute;inset:0;z-index:0}" in css
        # the brewer view's art
        page_b = (await b.http.get("/decks/42?view=brewer")).text
        assert f"<img class='art' src='{front}' alt='' loading='lazy'>" in page_b
    finally:
        await b.aclose()
