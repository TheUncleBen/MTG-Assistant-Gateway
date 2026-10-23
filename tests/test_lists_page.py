"""The My lists page (/lists, lists_page.py): five tabs over the member's account-wide Archidekt
lists, each change the member's own press through a plain form (works without scripts), a delete
asking first with a box, Archidekt's refusal shown in the box, the custom card's picture through
its own route, and the links to the page from the More menu and the Account page."""

from __future__ import annotations

import html
import re

import pytest

from mtg_gateway import account_lists

from .conftest import Stack, stack_with
from .test_account_lists import _picture
from .test_approval_modes import SHIPPED
from .test_decks_and_proxy import Browser, linked_user

stack_options = stack_with(SHIPPED)
WHO = "alice"


def _seed(ark) -> None:
    a = ark.account
    a.templates[WHO] = [
        {"id": 11, "name": "Ramp", "includedInDeck": True, "includedInPrice": True, "isPremier": False},
        {"id": 12, "name": "Commander", "includedInDeck": True, "includedInPrice": True, "isPremier": True},
    ]
    a.defaults[WHO] = {9003: ["Ramp"]}  # Sol Ring
    a.tags[WHO] = [{"id": 21, "name": "Cut", "color": "#ff4d4f"}]
    a.pins[WHO] = [
        {"id": 31, "card": a._card(9003), "notes": "for Meren", "modifier": "Normal", "quantity": 1},
    ]
    a.customs[WHO] = [
        {
            "id": 41,
            "owner": ark.users[WHO]["id"],
            "frontName": "Grizzled Mentor",
            "frontTypes": "Creature",
            "frontSubTypes": "Human Advisor",
            "frontPower": "2",
            "frontToughness": "3",
            "hasBack": False,
            "setCode": "CST",
            "collectorNumber": "001",
        }
    ]


async def _member(stack: Stack) -> Browser:
    """A linked member's browser, signed in."""
    await linked_user(stack)
    b = Browser(stack.h)
    await b.login("/lists")
    return b


@pytest.fixture
async def member(stack: Stack):
    _seed(stack.ark)
    b = await _member(stack)
    try:
        yield b
    finally:
        await b.aclose()


async def _post(b: Browser, tab: str, **data):
    data.setdefault("csrf", await b.csrf(f"/lists/{tab}"))
    return await b.http.post(f"/lists/{tab}", data=data)


def _notice(page: str) -> str:
    m = re.search(r"<div class='notice ok' role='status'>([^<]*)</div>", page)
    return m.group(1) if m else ""


# -- the tabs ------------------------------------------------------------------------------------------


async def test_lists_opens_on_the_first_tab(member: Browser) -> None:
    r = await member.http.get("/lists")
    assert r.status_code == 302 and r.headers["location"] == "/lists/templates"
    r = await member.http.get("/lists/nothing")
    assert r.status_code == 302 and r.headers["location"] == "/lists/templates"


async def test_every_tab_renders_its_rows_and_menus(member: Browser) -> None:
    want = {
        "templates": ("Category templates", ["Ramp", "Commander"], "New template"),
        "defaults": ("Default categories", ["Sol Ring"], "Search by card name"),
        "tags": ("Color tags", ["Cut"], "New color tag"),
        "pins": ("Pinned cards", ["Sol Ring"], "Remove all…"),
        "custom": ("Custom cards", ["Grizzled Mentor"], "New custom card"),
    }
    for slug, (label, names, extra) in want.items():
        page = (await member.http.get(f"/lists/{slug}")).text
        assert f"<a href='/lists/{slug}' aria-current='page'>{label}" in page, slug
        assert "<title>My lists" in page and extra in page, slug
        for n in names:
            assert f"data-name='{n}'" in page and f"aria-label='Menu for {n}'" in page, (slug, n)
        # every row's menu is a plain drop-down of links without scripts, its delete in red
        assert "<details class='dd rowmenu lp-dd'>" in page, slug
        assert "static/lists.js" in page or "lists.js" in page, slug
        assert "kept under History" in page, slug
    page = (await member.http.get("/lists/pins")).text
    assert "1 / 150" in page and "for Meren" in page and "9 / 256" in page
    assert "class='danger' data-box='pin-rm'" in page
    page = (await member.http.get("/lists/templates")).text
    assert "class='danger' data-box='tpl-del'" in page
    # the tabs carry the counts read so far (one Archidekt read per tab view)
    assert "Category templates<span class='n'>2</span>" in page
    assert "Pinned cards<span class='n'>1</span>" in page


async def test_an_unlinked_member_is_sent_to_link(stack: Stack) -> None:
    b = Browser(stack.h)
    try:
        await b.login("/lists")
        r = await b.http.get("/lists/templates")
        assert r.status_code == 200 and "Link your Archidekt account" in r.text
        assert "href='/account'" in r.text
    finally:
        await b.aclose()


async def test_a_signed_out_browser_is_sent_to_login(stack: Stack) -> None:
    b = Browser(stack.h)
    try:
        r = await b.http.get("/lists/tags")
        assert r.status_code == 302 and "/login" in r.headers["location"]
    finally:
        await b.aclose()


# -- Category templates ------------------------------------------------------------------------------


async def test_templates_create_edit_and_delete_through_the_forms(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    r = await _post(member, "templates", action="create", name="Draw", in_deck="1", in_price="1")
    assert r.status_code == 303 and r.headers["location"] == "/lists/templates?ok=template_created", r.text
    assert [t["name"] for t in ark.account.templates[WHO]] == ["Ramp", "Commander", "Draw"]
    page = (await member.http.get(r.headers["location"])).text
    assert _notice(page) == "Template created on Archidekt and read back."

    r = await _post(member, "templates", action="edit", was_name="Draw", name="Card draw", in_deck="1")
    assert r.status_code == 303 and r.headers["location"] == "/lists/templates?ok=template_saved"
    t = ark.account.templates[WHO][2]
    assert t["name"] == "Card draw" and t["includedInPrice"] is False

    # a delete asks first: the plain press is sent to the box, drawn open on the page
    r = await _post(member, "templates", action="delete", was_name="Card draw")
    assert r.status_code == 303 and r.headers["location"] == f"/lists/templates?delete={t['id']}"
    page = (await member.http.get(r.headers["location"])).text
    assert "Delete the template “Card draw”?" in page and "data-open" in page
    assert "is also removed from every card's default categories" in page
    assert "<button type='submit' class='danger' form='tpl-del-f'>" in page
    assert len(ark.account.templates[WHO]) == 3
    # the box's own form carries confirmed=1: applied by the member's press (a destructive change)
    r = await _post(member, "templates", action="delete", was_name="Card draw", confirmed="1")
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/lists/templates?ok=template_deleted&n=Card+draw"
    assert [t["name"] for t in ark.account.templates[WHO]] == ["Ramp", "Commander"]
    page = (await member.http.get(r.headers["location"])).text
    assert _notice(page) == "Deleted “Card draw” on Archidekt."
    # the change is on the proposals list as applied, with the copy from just before
    hist = (await member.http.get("/history")).text
    assert "Your Archidekt lists" in hist


async def test_archidekts_refusal_is_shown_in_the_box(member: Browser, stack: Stack) -> None:
    r = await _post(member, "templates", action="create", name="ramp", in_deck="1")
    assert r.status_code == 400
    assert "<div data-t-new='New category template'" in r.text and "data-open" in r.text
    assert "class='notice error boxnote' role='alert'" in r.text
    assert "value='ramp'" in r.text  # what was typed stays
    assert len(stack.ark.account.templates[WHO]) == 2


async def test_a_form_without_its_token_is_refused(member: Browser, stack: Stack) -> None:
    r = await member.http.post("/lists/templates", data={"action": "create", "name": "X", "csrf": "nope"})
    assert r.status_code == 403
    assert len(stack.ark.account.templates[WHO]) == 2


# -- Default categories ------------------------------------------------------------------------------


async def test_default_categories_set_and_clear(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    page = (await member.http.get("/lists/defaults?edit=9003")).text
    assert "Default categories for “Sol Ring”" in page and "data-open" in page
    assert "<input type='checkbox' name='cat' value='Ramp' checked" in page
    csrf = await member.csrf("/lists/defaults")
    r = await member.http.post(
        "/lists/defaults",
        data={
            "csrf": csrf,
            "action": "set",
            "card": "Sol Ring",
            "cat": ["Ramp", "Commander"],
            "main": "Commander",
        },
    )
    assert r.status_code == 303 and r.headers["location"] == "/lists/defaults?ok=defaults_saved&n=Sol+Ring"
    assert ark.account.defaults[WHO][9003] == ["Commander", "Ramp"]  # the main one first
    page = (await member.http.get(r.headers["location"])).text
    assert _notice(page) == "Default categories for “Sol Ring” saved on Archidekt and read back."
    assert "<span class='lp-chip main'>" in page

    r = await member.http.post("/lists/defaults", data={"csrf": csrf, "action": "set", "card": "Sol Ring"})
    assert r.status_code == 400 and "Tick at least one category" in r.text

    r = await _post(member, "defaults", action="clear", card="Sol Ring")
    assert r.status_code == 303 and r.headers["location"] == "/lists/defaults?clear=9003"
    page = (await member.http.get(r.headers["location"])).text
    assert "Clear the default categories of “Sol Ring”?" in page
    r = await _post(member, "defaults", action="clear", card="Sol Ring", confirmed="1")
    assert r.status_code == 303 and r.headers["location"] == "/lists/defaults?ok=defaults_cleared"
    assert ark.account.defaults[WHO][9003] == []
    page = (await member.http.get("/lists/defaults")).text
    assert "No cards with default categories yet." in page


async def test_default_categories_load_more_answers_rows(member: Browser, stack: Stack) -> None:
    stack.ark.account.defaults[WHO] = {9000 + i: ["Ramp"] for i in range(3, 106)}
    page = (await member.http.get("/lists/defaults")).text
    assert "Load more (100 of 103 shown)" in page and "href='/lists/defaults?page=2'" in page
    r = await member.http.get("/lists/defaults?page=2", headers={"Accept": "application/json"})
    d = r.json()
    assert d["ok"] and d["has_more"] is False and d["html"].count("class='tr'") == 3
    assert d["done"] == "All 103 shown."
    page = (await member.http.get("/lists/defaults?q=sol")).text
    assert "Sol Ring" in page and "Card 9007" not in page


# -- Color tags ----------------------------------------------------------------------------------------


async def test_color_tags_create_recolor_and_delete(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    r = await _post(member, "tags", action="create", name="Keep", color="#1ebb6c")
    assert r.status_code == 303 and r.headers["location"] == "/lists/tags?ok=tag_created"
    assert ark.account.tags[WHO][1] == {
        "id": ark.account.tags[WHO][1]["id"],
        "name": "Keep",
        "color": "#1ebb6c",
    }
    page = (await member.http.get("/lists/tags?edit=21")).text
    assert "Rename or recolor “Cut”" in page and "value='#ff4d4f'" in page
    r = await _post(member, "tags", action="edit", was_name="Cut", name="Cut it", color="#aa0000")
    assert r.status_code == 303 and r.headers["location"] == "/lists/tags?ok=tag_saved"
    assert ark.account.tags[WHO][0]["name"] == "Cut it" and ark.account.tags[WHO][0]["color"] == "#aa0000"
    r = await _post(member, "tags", action="delete", was_name="Cut it")
    assert r.headers["location"] == "/lists/tags?delete=21"
    page = (await member.http.get(r.headers["location"])).text
    assert "Delete the tag “Cut it”?" in page
    r = await _post(member, "tags", action="delete", was_name="Cut it", confirmed="1")
    assert r.headers["location"] == "/lists/tags?ok=tag_deleted&n=Cut+it"
    assert [t["name"] for t in ark.account.tags[WHO]] == ["Keep"]


# -- Pinned cards ------------------------------------------------------------------------------------


async def test_pins_copies_notes_and_removal(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    r = await _post(member, "pins", action="edit", pin_id="31", quantity="2")
    assert r.status_code == 303 and r.headers["location"] == "/lists/pins?ok=pin_saved"
    assert ark.account.pins[WHO][0]["quantity"] == 2
    page = (await member.http.get(r.headers["location"])).text
    assert _notice(page) == "Saved on Archidekt." and "<b class='n' aria-live='polite'>2</b>" in page

    # the note, saved on leaving the field with scripts: the same form, answered as JSON
    csrf = await member.csrf("/lists/pins")
    r = await member.http.post(
        "/lists/pins",
        data={"csrf": csrf, "action": "edit", "pin_id": "31", "notes": "Trade for it"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["notice"] == "Saved on Archidekt."
    assert ark.account.pins[WHO][0]["notes"] == "Trade for it"
    r = await member.http.post(
        "/lists/pins",
        data={"csrf": csrf, "action": "edit", "pin_id": "31", "notes": "x" * 300},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 400 and "256" in r.json()["message"]

    # below one copy: the stepper's press asks first, as Remove pin does
    r = await _post(member, "pins", action="edit", pin_id="31", quantity="0")
    assert r.status_code == 303 and r.headers["location"] == "/lists/pins?remove=31"
    page = (await member.http.get(r.headers["location"])).text
    assert "Remove the pin on “Sol Ring”?" in page and "data-open" in page
    assert ark.account.pins[WHO]
    r = await _post(member, "pins", action="unpin", pin_id="31", confirmed="1")
    assert r.headers["location"] == "/lists/pins?ok=pin_removed"
    assert ark.account.pins[WHO] == []
    page = (await member.http.get("/lists/pins")).text
    assert "No pinned cards yet." in page and "Remove all…" not in page


async def test_remove_all_pins_asks_then_removes_every_pin(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    ark.account.pins[WHO].append(
        {"id": 32, "card": ark.account._card(9007), "notes": "", "modifier": "Foil", "quantity": 1}
    )
    page = (await member.http.get("/lists/pins")).text
    assert "2 / 150" in page and "Foil" in page
    assert (
        "<a class='btn btn-outline-danger' href='/lists/pins?remove_all=1' data-box-open='pin-all'>" in page
    )
    r = await _post(member, "pins", action="remove_all")
    assert r.headers["location"] == "/lists/pins?remove_all=1"
    page = (await member.http.get(r.headers["location"])).text
    assert "Remove all 2 pinned cards?" in page and ">Remove all 2</button>" in page
    assert "class='danger' form='pin-all-f'" in page
    r = await _post(member, "pins", action="remove_all", confirmed="1")
    assert r.headers["location"] == "/lists/pins?ok=pins_removed"
    assert ark.account.pins[WHO] == []


# -- Custom cards ------------------------------------------------------------------------------------


async def test_custom_cards_create_edit_and_a_delete_that_needs_the_tick(
    member: Browser, stack: Stack
) -> None:
    ark = stack.ark
    r = await _post(
        member,
        "custom",
        action="create",
        f_n="Rift of Echoes",
        f_m="{1}{U}",
        f_t="Instant",
        f_x="Draw a card.",
        set_code="CST",
        collector_number="002",
    )
    assert r.status_code == 303 and r.headers["location"] == "/lists/custom?ok=card_created", r.text
    made = ark.account.customs[WHO][1]
    assert made["frontName"] == "Rift of Echoes" and made["frontManaCost"] == "{1}{U}" and made["cmc"] == 2
    assert made["hasBack"] is False and made["backName"] is None
    page = (await member.http.get(r.headers["location"])).text
    assert _notice(page) == "Custom card created on Archidekt and read back."
    assert "Rift of Echoes" in page and "Instant" in page and "CST · 002" in page

    page = (await member.http.get("/lists/custom?edit=41")).text
    assert "Edit “Grizzled Mentor”" in page and "value='Human Advisor'" in page
    assert "Choose a picture…" in page and "form='cc-pic-f'" in page
    r = await _post(
        member,
        "custom",
        action="edit",
        was_name="Grizzled Mentor",
        f_n="Grizzled Mentor",
        f_t="Creature",
        f_st="Human Advisor",
        f_p="3",
        f_tg="3",
        has_back="1",
        b_n="Mentor Unbound",
        b_t="Creature",
    )
    assert r.status_code == 303 and r.headers["location"] == "/lists/custom?ok=card_saved", r.text
    c = ark.account.customs[WHO][0]
    assert c["frontPower"] == "3" and c["hasBack"] is True and c["backName"] == "Mentor Unbound"
    page = (await member.http.get("/lists/custom")).text
    assert "Grizzled Mentor // Mentor Unbound" in page

    r = await _post(member, "custom", action="delete", was_name="Rift of Echoes")
    assert r.headers["location"] == f"/lists/custom?delete={made['id']}"
    page = (await member.http.get(r.headers["location"])).text
    assert "Delete “Rift of Echoes”?" in page and "the card becomes a blank" in page
    assert "name='understood'" in page and ">Delete this custom card</button>" in page
    r = await _post(member, "custom", action="delete", was_name="Rift of Echoes", confirmed="1")
    assert r.status_code == 400 and "Tick the box first" in r.text
    assert len(ark.account.customs[WHO]) == 2
    r = await _post(
        member, "custom", action="delete", was_name="Rift of Echoes", confirmed="1", understood="1"
    )
    assert r.headers["location"] == "/lists/custom?ok=card_deleted&n=Rift+of+Echoes"
    assert [c["frontName"] for c in ark.account.customs[WHO]] == ["Grizzled Mentor"]


async def test_custom_cards_search_answers_rows_as_json(member: Browser) -> None:
    r = await member.http.get("/lists/custom?q=grizz", headers={"Accept": "application/json"})
    d = r.json()
    assert d["ok"] and "Grizzled Mentor" in d["html"] and d["total"] == 1 and d["has_more"] is False
    r = await member.http.get("/lists/custom?q=zzz", headers={"Accept": "application/json"})
    assert r.json()["html"] == "" and r.json()["total"] == 0
    page = (await member.http.get("/lists/custom?q=zzz")).text
    assert "No custom card matches that name." in page


async def test_the_picture_goes_through_its_own_route(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    csrf = await member.csrf("/lists/custom")
    r = await member.http.post(
        "/lists/custom/picture",
        data={"csrf": csrf, "name": "Grizzled Mentor", "face": "front", "crop": "100,0,600,840"},
        files={"picture": ("mentor.jpg", _picture("JPEG"), "image/jpeg")},
    )
    assert r.status_code == 303 and r.headers["location"] == "/lists/custom?ok=picture_saved", r.text
    who, body, _d = ark.account.uploads[-1]
    assert who == WHO and body.startswith(b"\x89PNG")
    assert ark.account.customs[WHO][0]["frontImageUrl"].startswith("https://")
    page = (await member.http.get(r.headers["location"])).text
    assert _notice(page) == "Picture saved on Archidekt and read back."
    page = (await member.http.get("/lists/custom?edit=41")).text
    assert "Picture set" in page

    # not a picture: refused in the box, nothing sent
    n = len(ark.account.uploads)
    r = await member.http.post(
        "/lists/custom/picture",
        data={"csrf": csrf, "name": "Grizzled Mentor", "face": "front"},
        files={"picture": ("x.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400 and "Edit “Grizzled Mentor”" in r.text
    assert "class='notice error boxnote' role='alert'" in r.text
    assert len(ark.account.uploads) == n
    # the back face needs the card to have one
    r = await member.http.post(
        "/lists/custom/picture",
        data={"csrf": csrf, "name": "Grizzled Mentor", "face": "back"},
        files={"picture": ("mentor.png", _picture("PNG"), "image/png")},
    )
    assert r.status_code == 400 and "Has a back face" in r.text
    # without the token
    r = await member.http.post(
        "/lists/custom/picture",
        data={"csrf": "nope", "name": "Grizzled Mentor", "face": "front"},
        files={"picture": ("mentor.png", _picture("PNG"), "image/png")},
    )
    assert r.status_code == 403


# -- the links to the page ---------------------------------------------------------------------------


async def test_the_more_menu_and_the_account_page_link_to_my_lists(member: Browser, stack: Stack) -> None:
    page = (await member.http.get("/reports")).text
    nav = page.split("<nav class='site' aria-label='Site'>")[1].split("</nav>")[0]
    menu = nav.split("<div class='menu'>")[1]
    labels = [x.split("</svg>")[1] for x in menu.split("</a>")[:-1]]
    # next to Proposals and History (Watchlist sits between when watchlists are on)
    assert labels == ["Proposals", "History", "Reports", "My lists", "Forum", "Guide", "Account"]
    assert "<a href='/lists'>" in menu
    # the phone's More sheet and the rail list the same pages
    assert page.count("href='/lists'") >= 2

    # the Account page's panel, with the counts of the tabs read so far
    account_lists.forget_counts("user-1")  # as before any tab was opened
    acc = (await member.http.get("/account")).text
    assert "<h2>Your Archidekt lists</h2>" in acc
    assert "Lists that follow you into every deck, kept on Archidekt:" in acc
    assert "<a href='/lists/templates'>category templates</a>" in acc  # not read yet: no count
    assert "<a class='btn' href='/lists'>" in acc and "Open My lists</a>" in acc
    for slug in ("templates", "defaults", "tags", "pins", "custom"):
        await member.http.get(f"/lists/{slug}")
    acc = (await member.http.get("/account")).text
    assert "<a href='/lists/templates'>2 category templates</a>" in acc
    assert "<a href='/lists/defaults'>1 card with default categories</a>" in acc
    assert "<a href='/lists/tags'>1 color tag</a>" in acc
    assert "<a href='/lists/pins'>1 pinned card</a>" in acc
    assert "<a href='/lists/custom'>1 custom card</a>" in acc
    assert account_lists.counts("user-1")["templates"] == 2


async def test_a_custom_card_is_added_to_a_deck_from_its_row(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    page = html.unescape((await member.http.get("/lists/custom")).text)
    # the row's menu offers Add to deck…; the box is on the page, hidden, and reads the decks
    assert "Add to deck…" in page and "href='/lists/custom?add=41'" in page and "data-box='cc-add'" in page
    assert "id='cc-add'" in page and "id='cc-add-f'" in page and "data-src='/api/decks/mine'" in page
    assert "data-src='/api/v1/decks/{id}?cards=0'" in page and "The deck's default" in page
    # the old address draws it open with the deck list in the page (no scripts)
    page = html.unescape((await member.http.get("/lists/custom?add=41")).text)
    assert "Add “Grizzled Mentor” to a deck" in page and "data-open" in page
    assert "<option value='42'>Sample Commander Deck</option>" in page
    assert ">Add to deck</button>" in page
    # the checks
    r = await _post(
        member, "custom", action="add_to_deck", custom_id="41", was_name="Grizzled Mentor", deck=""
    )
    assert r.status_code == 400 and "Pick a deck first." in r.text
    r = await _post(
        member,
        "custom",
        action="add_to_deck",
        custom_id="41",
        was_name="Grizzled Mentor",
        deck="42",
        quantity="0",
    )
    assert r.status_code == 400 and "Copies must be a whole number from 1 to 99." in r.text
    assert not ark.decks[42].get("customCards")
    # the add: proposed and applied through this press, the deck read back
    r = await _post(
        member,
        "custom",
        action="add_to_deck",
        custom_id="41",
        was_name="Grizzled Mentor",
        deck="42",
        quantity="2",
        category="Ramp",
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/lists/custom?ok=card_added&n=Grizzled+Mentor"
    rows = ark.decks[42]["customCards"]
    assert len(rows) == 1 and rows[0]["quantity"] == 2 and rows[0]["categories"] == ["Ramp"]
    assert rows[0]["card"]["id"] == 41
    page = (await member.http.get(r.headers["location"])).text
    assert _notice(page) == "Added “Grizzled Mentor” to the deck on Archidekt and read back."
    # the proposal is the member's own, applied
    listed = (await member.http.get("/api/v1/proposals")).json()["proposals"]
    assert listed[0]["kind"] == "custom_cards" and listed[0]["state"] == "applied"
    # a second add of the same card is refused in the box, with what was picked kept
    r = await _post(
        member, "custom", action="add_to_deck", custom_id="41", was_name="Grizzled Mentor", deck="42"
    )
    assert r.status_code == 400 and "already in this deck" in r.text
    assert "data-open" in r.text and "<option value='42' selected>" in r.text
    # with writes off the add waits on its review page
    object.__setattr__(stack.h.app.state.gateway.settings, "writes_enabled", False)
    ark.decks[42]["customCards"] = []
    r = await _post(
        member, "custom", action="add_to_deck", custom_id="41", was_name="Grizzled Mentor", deck="42"
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/proposals/"), r.text
    assert not ark.decks[42]["customCards"]


async def test_custom_cards_rows_and_the_edit_box_show_the_cards_picture(
    member: Browser, stack: Stack
) -> None:
    """A thumbnail on each row and the current picture in the Edit box (front and back), through
    the gateway's own route (custom_pictures); a card without a picture keeps the empty slot and
    the frame's words."""
    ark = stack.ark
    card = ark.account.customs[WHO][0]
    card["frontImageUrl"] = "https://cdn.example/41.png"
    ark.account.customs[WHO].append(
        {
            "id": 44,
            "owner": card["owner"],
            "frontName": "Old Ranger",
            "frontTypes": "Creature",
            "hasBack": False,
        }
    )
    page = (await member.http.get("/lists/custom")).text
    assert (
        "<img class='lp-thumb custom' src='/lists/api/custom_picture/41?face=front' alt='' loading='lazy'"
        in page
    )
    ranger = re.search(r"<div class='tr'[^>]*data-name='Old Ranger'[^>]*>.*?</div>", page, re.S).group(0)
    assert "<span class='lp-thumb' aria-hidden='true'></span>" in ranger and "custom_picture" not in ranger
    assert "src='https://cdn.example" not in page  # never drawn from Archidekt's address itself
    # the Edit box: the front face's picture; no back face, no back picture
    page = (await member.http.get("/lists/custom?edit=41")).text
    front = "<img class='lp-cur' src='/lists/api/custom_picture/41?face=front' alt=''>"
    assert f"data-crop='f'>Picture set{front}</div>" in page
    assert "data-crop='b'>No picture yet</div>" in page
    card["hasBack"] = True
    card["backImageUrl"] = "https://cdn.example/41b.png"
    page = (await member.http.get("/lists/custom?edit=41")).text
    assert "<img class='lp-cur' src='/lists/api/custom_picture/41?face=back' alt=''>" in page
    page = (await member.http.get("/lists/custom?edit=44")).text
    assert "data-crop='f'>No picture yet</div>" in page and "class='lp-cur'" not in page
    # a new card's box has none either
    page = (await member.http.get("/lists/custom?new=1")).text
    assert "class='lp-cur'" not in page
