"""Categories edited on the deck page itself (0.7.46 full review F8, Ben's T-191 note, mockup
screen 4): the member's own deck, grouped by category, carries what each category is
(``data-catinfo``) for the stacks' ⋮ menus, and "+ Add category" after the last stack. Every change
the page's script sends (static/deck.js) goes through Deck settings' own route,
``POST /decks/{id}/categories``, with ``json=1&page=deck``: the same baseline check, the same
questions (the commander, which cards count, a delete) and the same one proposal applied at once
with its snapshot. The answer carries the stacks drawn again and, after a delete, its Undo."""

from __future__ import annotations

import json
import re

from mtg_gateway import modes
from mtg_gateway.decks import OPEN_TABS_NOTE

from .conftest import Stack
from .test_companion import NAV, linked_browser
from .test_deck_categories import RAMP, _give_categories

DECK = "/decks/42"
POST = "/decks/42/categories"
JSON = {"Accept": "application/json"}


async def _page(b, path: str = DECK) -> tuple[str, str]:  # noqa: ANN001
    page = (await b.http.get(path, headers=NAV)).text
    late = re.search(r"data-late='(/late/[A-Za-z0-9_-]+)'", page)
    if late:  # the first read took over the late frame's mark (a loaded machine): the finished page
        assert (await b.http.get(late.group(1))).json()["ready"] is True
        page = (await b.http.get(path, headers=NAV)).text
    return page, re.search(r"name='csrf' value='([0-9a-f]+)'", page).group(1)


def _catinfo(page: str) -> list[dict]:
    m = re.search(r"<div class='deckview [^']*' id='cards'[^>]* data-catinfo='([^']*)'", page)
    assert m, "the cards carry no data-catinfo"
    return json.loads(m.group(1).replace("&#x27;", "'").replace("&quot;", '"'))


def _info(page: str, name: str) -> dict:
    return next(c for c in _catinfo(page) if c["name"] == name)


def _baseline(c: dict) -> dict[str, str]:
    """What deck.js sends for one category: its ``was_`` fields, its name and the switches that
    are on, as Deck settings' form does for every category."""
    k = c["key"]
    out = {f"was_name_{k}": c["name"], f"name_{k}": c["name"]}
    for flag in ("premier", "not_in_deck", "not_in_price"):
        out[f"was_{flag}_{k}"] = "1" if c[flag] else "0"
        if c[flag]:
            out[f"{flag}_{k}"] = "1"
    return out


def _stacks(html: str) -> list[str]:
    return re.findall(r"<section class='stack' data-group='([^']*)'", html)


async def test_the_members_own_deck_page_carries_its_categories_and_an_add_button(stack: Stack) -> None:
    """Grouped by category, each stack keeps its no-script ⋮ (the editor and Deck settings) and the
    cards carry every category with its form key, record id, switches, copies and whether it
    touches the commander; "+ Add category" follows the stacks. Grouped any other way (the stacks
    are not categories) there is none of it, and nothing of it on a deck the member doesn't own."""
    _give_categories(stack.ark)
    # a name with a quote cannot break the data the page's script reads
    stack.ark.decks[42]["categories"].append(
        {"name": "Bob's picks", "isPremier": False, "includedInDeck": True, "includedInPrice": True}
    )
    b = await linked_browser(stack)
    try:
        page, _csrf = await _page(b)
        info = _catinfo(page)
        names = [c["name"] for c in info]
        assert names[0] == "Commander" and "Ramp" in names and "Maybeboard" in names, names
        ramp = _info(page, "Ramp")
        assert ramp["cards"] == 2 and ramp["record"] and isinstance(ramp["id"], int)
        assert ramp["key"] == str(ramp["id"]) and not ramp["premier"] and not ramp["commander"]
        cmd = _info(page, "Commander")
        assert cmd["premier"] and cmd["commander"]
        maybe = _info(page, "Maybeboard")
        assert maybe["not_in_deck"] and maybe["not_in_price"]
        assert "<details class='dd catdd'>" in page
        assert "aria-label='Options for Ramp'" in page
        assert (
            "<a class='btn addcat' href='/decks/42/settings#categories' data-addcat>" in page
            and "Add category</a></div>" in page
        )
        assert _info(page, "Bob's picks")["cards"] == 0
        # grouped by type: no category menus, no Add category
        page, _csrf = await _page(b, f"{DECK}?group=type")
        assert "data-catinfo" not in page and "data-addcat" not in page and "catdd" not in page
    finally:
        await b.aclose()


def test_someone_elses_deck_has_no_category_menus() -> None:
    from mtg_gateway.archidekt import parse_deck
    from mtg_gateway.deckpage import cards_html

    from .fake_archidekt import FakeArchidekt

    deck = parse_deck(FakeArchidekt().decks[42])
    page = cards_html(deck, view="text", group="category", sort="name", q="", own=False)
    assert "data-catinfo" not in page and "data-addcat" not in page and "catdd" not in page
    mine = cards_html(deck, view="text", group="category", sort="name", q="", own=True)
    assert "data-catinfo" in mine and "data-addcat" in mine


async def test_a_rename_from_the_deck_page_redraws_the_stacks(stack: Stack) -> None:
    """The page sends the category's baseline and its new name (as Deck settings' form does for
    one row); Archidekt gets the editor's PATCH; the answer is the stacks drawn again for the
    page's view and sort, with the renamed stack and a fresh data-catinfo. The read, the write and
    the read back are the only Archidekt calls (review F1)."""
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, csrf = await _page(b)
        ramp = _info(page, "Ramp")
        stack.ark.calls.clear()
        data = {
            **_baseline(ramp),
            f"name_{ramp['key']}": "Mana",
            "csrf": csrf,
            "action": "save",
            "json": "1",
            "page": "deck",
            "view": "table",
            "sort": "name",
        }
        r = await b.http.post(POST, data=data, headers=JSON)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True and "undo_html" not in d
        assert [m + " " + p for m, p in stack.ark.calls] == [
            "GET /api/decks/42/",
            f"PATCH /api/decks/category/{ramp['id']}/",
            "GET /api/decks/42/",
        ], stack.ark.calls
        html = d["cards_html"]
        assert html.startswith("<div class='deckview table' id='cards' data-deck='42'")
        assert "Mana" in _stacks(html) and "Ramp" not in _stacks(html)
        assert _info(html, "Mana")["id"] == ramp["id"] and "Ramp" not in [c["name"] for c in _catinfo(html)]
        assert "data-addcat" in html
        rows = stack.ark.decks[42]["cards"]
        assert [c["card"]["oracleCard"]["name"] for c in rows if c["categories"] == ["Mana"]]
        # the page's own history row says what changed
        history = (await b.http.get("/api/v1/decks/42/history")).json()
        assert [p["state"] for p in history["proposals"]] == ["applied"]
        # a stale baseline (the page showed a name the deck no longer has) is refused in words,
        # nothing sent
        stack.ark.calls.clear()
        r = await b.http.post(POST, data={**data, f"name_{ramp['key']}": "Rocks"}, headers=JSON)
        assert r.status_code == 400 and r.json()["fresh"] is True and "Not saved" in r.json()["message"]
        assert not any(m == "PATCH" for m, _p in stack.ark.calls)
    finally:
        await b.aclose()


async def test_a_switch_that_touches_the_commander_asks_first_in_every_mode(stack: Stack) -> None:
    """Premier on Ramp makes its cards commanders: the page's post is answered with the question
    (the box's own words) and nothing is sent, in the stack's Full auto mode too; the same post with
    confirmed=1 saves it. The same for In deck off (which cards count)."""
    assert stack.gw.decks.live.get("approval_mode_default") == "auto"
    _give_categories(stack.ark)
    stack.ark.decks[42]["categories"].append(
        {"name": "Removal", "isPremier": False, "includedInDeck": True, "includedInPrice": True}
    )
    b = await linked_browser(stack)
    try:
        page, csrf = await _page(b)
        ramp = _info(page, "Ramp")
        base = {"csrf": csrf, "action": "save", "json": "1", "page": "deck"}
        data = {**_baseline(ramp), f"premier_{ramp['key']}": "1", **base}
        r = await b.http.post(POST, data=data, headers=JSON)
        assert r.status_code == 200, r.text
        assert r.json() == {
            "ok": False,
            "needs_confirm": True,
            "title": "Change the commander category?",
            "text": modes.CATEGORY_COMMANDER_CONFIRM,
        }
        assert stack.ark.category_patches == []
        r = await b.http.post(POST, data={**data, "confirmed": "1"}, headers=JSON)
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert [p["isPremier"] for p in stack.ark.category_patches] == [True]
        assert _info(r.json()["cards_html"], "Ramp")["premier"] is True
        # In deck off: which cards count
        page, csrf = await _page(b)
        draw = _info(page, "Removal")
        data = {**_baseline(draw), f"not_in_deck_{draw['key']}": "1", **base, "csrf": csrf}
        r = await b.http.post(POST, data=data, headers=JSON)
        assert r.status_code == 200 and r.json()["needs_confirm"] is True
        assert r.json()["title"] == "Change which cards count in the deck?"
        r = await b.http.post(POST, data={**data, "confirmed": "1"}, headers=JSON)
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert _info(r.json()["cards_html"], draw["name"])["not_in_deck"] is True
    finally:
        await b.aclose()


async def test_add_category_from_the_deck_page(stack: Stack) -> None:
    """A plain create is applied at once; one made Premier or Not in deck asks first with the
    create box's own title and sentence, as Deck settings does."""
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        _page_, csrf = await _page(b)
        base = {"csrf": csrf, "action": "create", "json": "1", "page": "deck"}
        r = await b.http.post(POST, data={**base, "category": "Draw spells"}, headers=JSON)
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert [c["name"] for c in stack.ark.category_creates] == ["Draw spells"]
        made = _info(r.json()["cards_html"], "Draw spells")
        assert made["cards"] == 0 and made["record"]
        r = await b.http.post(POST, data={**base, "category": "Partner", "premier": "1"}, headers=JSON)
        assert r.status_code == 200, r.text
        assert r.json() == {
            "ok": False,
            "needs_confirm": True,
            "title": "Create “Partner” as a Premier category?",
            "text": modes.CATEGORY_WORDS["create_commander"],
        }
        assert len(stack.ark.category_creates) == 1
        yes = {**base, "category": "Partner", "premier": "1", "confirmed": "1"}
        r = await b.http.post(POST, data=yes, headers=JSON)
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert _info(r.json()["cards_html"], "Partner")["premier"] is True
        # a name the deck has is refused in words
        r = await b.http.post(POST, data={**base, "category": "ramp"}, headers=JSON)
        assert r.status_code == 400 and "already has a category named" in r.json()["message"]
    finally:
        await b.aclose()


async def test_delete_from_the_deck_page_moves_the_cards_first_and_offers_undo(stack: Stack) -> None:
    """The page's delete box names where the cards go: the cards are moved, then the empty category
    is deleted (Archidekt never gets a DELETE of a category with cards). The answer's stacks no
    longer have it, and its Undo form (the delete's own snapshot, review F10) comes with them. A
    delete without a pick is refused in words; moving the cards into the Commander category asks
    first with the commander sentence."""
    _give_categories(stack.ark)
    stack.ark.decks[42]["categories"].append(
        {"name": "Removal", "isPremier": False, "includedInDeck": True, "includedInPrice": True}
    )
    b = await linked_browser(stack)
    try:
        page, csrf = await _page(b)
        ramp = _info(page, "Ramp")
        base = {"csrf": csrf, "action": "delete", "delete": str(ramp["id"]), "json": "1", "page": "deck"}
        r = await b.http.post(POST, data={**base, "move_ok": "1"}, headers=JSON)
        assert r.status_code == 400 and r.json()["message"] == modes.CATEGORY_WORDS["pick_target"], r.text
        assert stack.ark.category_deletes == []
        r = await b.http.post(POST, data={**base, "move_cards_to": "Commander", "move_ok": "1"}, headers=JSON)
        assert r.status_code == 200 and r.json()["needs_confirm"] is True, r.text
        assert r.json()["title"] == "Delete the category “Ramp”?"
        assert modes.category_words("move_commander", n=2) in r.json()["text"]
        assert stack.ark.category_deletes == []
        r = await b.http.post(POST, data={**base, "move_cards_to": "Removal", "move_ok": "1"}, headers=JSON)
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        d = r.json()
        assert stack.ark.category_deletes == [ramp["id"]] and stack.ark.category_deletes_with_cards == []
        assert "Ramp" not in _stacks(d["cards_html"])
        assert "Ramp" not in [c["name"] for c in _catinfo(d["cards_html"])]
        rows = stack.ark.decks[42]["cards"]
        filed = {c["card"]["oracleCard"]["name"] for c in rows if c["categories"] == ["Removal"]}
        assert set(RAMP) <= filed
        m = re.search(
            r"<form method='post' action='/proposals/undo' class='undo' data-undo-box>(.*?)</form>",
            d["undo_html"],
        )
        assert m and ">Undo</button>" in m.group(1), d["undo_html"]
        history = (await b.http.get("/api/v1/decks/42/history")).json()
        row = stack.gw.decks.db.get_proposal(history["proposals"][0]["id"], "user-1")
        fields = dict(re.findall(r"name='([a-z_]+)' value='([^']*)'", m.group(1)))
        assert fields["snapshot"] == row["snapshot_id"] and fields["applied"] == row["id"]
        assert OPEN_TABS_NOTE  # the notice the history row carries, as every category change
    finally:
        await b.aclose()


async def test_add_from_template_on_the_deck_page(stack: Stack) -> None:
    _give_categories(stack.ark)
    stack.ark.account.templates["alice"] = [
        {"id": 901, "name": "Wincons", "includedInDeck": True, "includedInPrice": True, "isPremier": False},
        {"id": 902, "name": "Ramp", "includedInDeck": True, "includedInPrice": True, "isPremier": False},
    ]
    b = await linked_browser(stack)
    try:
        _page_, csrf = await _page(b)
        r = await b.http.post(
            POST,
            data={"csrf": csrf, "action": "template", "template": "Wincons", "json": "1", "page": "deck"},
            headers=JSON,
        )
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert [c["name"] for c in stack.ark.category_creates] == ["Wincons"]
        assert _info(r.json()["cards_html"], "Wincons")["cards"] == 0
    finally:
        await b.aclose()
