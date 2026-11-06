"""Categories edited on the deck page (0.7.46 full review F8, the owner's T-191 note, mockup screen 4)
in headless Chromium, on the phone in the Android app (375 px, a touch screen, the app's user
agent), the open Fold (840 px, touch) and the desktop (1280 px, a mouse), light and dark:

- each stack's ⋮ opens the category's menu (Rename…, Add category…, Add from template… | Premier,
  In deck, In price as tick rows | Edit cards in editor | Delete… in red), as does a right-click,
  a press and hold or Shift+F10 on its head; the no-script drop-down is gone; every row is
  finger-sized on touch and reads at 4.5:1;
- a rename saves at once and redraws the stacks, with Undo in its toast;
- Premier asks first in the shared box with the gateway's own words; Keep it sends nothing;
- Delete… is the red box: the biggest category the cards can go to comes picked, "Keep it" and the
  solid red "Delete category"; the cards move first, the stack goes, and the toast's Undo opens the
  restore of the delete's own snapshot;
- "+ Add category" after the last stack adds one (its empty stack is drawn), Undo takes it off;
- nothing scrolls sideways at 200% text.

Set MTG_CATS_SHOTS=<dir> to save screenshots. Skipped without a Chromium build."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_deck_categories import _give_categories
from .test_deck_menu_browser import _EXE, PNG, Server, _chromium
from .test_overflow_sweep_browser import MEASURE
from .test_text_zoom_browser import TEXT_200
from .waits import expect, quiet, settled

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_CATS_SHOTS")
APP_UA = "Mozilla/5.0 (Linux; Android 16; wv) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.7.46"
MENU_TEXT = ".ctxmenu [role=menuitem], .ctxmenu [role=menuitemcheckbox], .ctxmenu .head, .stackhead .title"
BOX = ".mtgbox-scrim .mtgbox"
ITEMS = [
    "Rename…",
    "Add category…",
    "Add from template…",
    "Premier",
    "In deck",
    "In price",
    "Edit cards in editor",
    "Delete…",
]
SURFACES = [(375, "dark", True, True), (840, "light", True, False), (1280, "dark", False, False)]
STACK = "(name) => !!document.querySelector(`.stack[data-group='${name}']`)"
NO_STACK = "(name) => !document.querySelector(`.stack[data-group='${name}']`)"
BOX_TITLE = "(t) => (document.querySelector('.mtgbox-scrim .mtgbox h2') || {}).textContent === t"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    _give_categories(s.ark)
    s.ark.decks[42]["categories"].append(
        {"name": "Removal", "isPremier": False, "includedInDeck": True, "includedInPrice": True}
    )
    for row in s.ark.decks[42]["cards"]:
        if row["card"]["oracleCard"]["name"] in ("Arcane Denial", "Acidic Slime", "Beast Within"):
            row["categories"] = ["Removal"]
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _open(p, server: Server, sid: str, width: int, theme: str, touch: bool, app: bool):
    import re

    browser = p.chromium.launch(executable_path=_EXE["path"], args=["--no-sandbox"])
    kw: dict = {"has_touch": True, "is_mobile": True} if touch else {}
    if app:
        kw["user_agent"] = APP_UA
    ctx = browser.new_context(viewport={"width": width, "height": 812 if width < 500 else 900}, **kw)
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": sid, "url": server.base},
            {"name": "mtg_theme", "value": theme, "url": server.base},
        ]
    )
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
    page.locator(".stackhead .kebab").first.wait_for(timeout=5000)
    return browser, page, errors


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def _items(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('.ctxmenu [role=menuitem], .ctxmenu [role=menuitemcheckbox]')]"
        ".map(e => e.textContent.trim())"
    )


def _checked(page, text: str) -> str:
    return page.locator(".ctxmenu [role=menuitemcheckbox]", has_text=text).get_attribute("aria-checked")


def _kebab(page, name: str):
    k = page.locator(f".stack[data-group='{name}'] .stackhead .kebab")
    k.scroll_into_view_if_needed()
    return k


def _stacks(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('.deckview .stack')].map(s => s.getAttribute('data-group'))"
    )


def _toast(page) -> str:
    page.locator(".deck-toast .msg").wait_for(timeout=5000)
    return page.locator(".deck-toast .msg").inner_text()


@pytest.mark.parametrize("width,theme,touch,app", SURFACES)
def test_the_category_menu_opens_every_way_and_reads_everywhere(
    server: Server, width: int, theme: str, touch: bool, app: bool
) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width, theme, touch, app)
        # the no-script drop-down made way for the ⋮ (one per stack), nothing of it left
        assert page.locator("details.catdd").count() == 0
        assert page.locator(".stackhead .kebab").count() == len(_stacks(page))
        k = _kebab(page, "Ramp")
        assert k.get_attribute("aria-label") == "Options for Ramp"
        k.click()
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        settled(page)
        assert menu.locator(".head").inner_text() == "Ramp"
        assert _items(page) == ITEMS, _items(page)
        assert _checked(page, "Premier") == "false" and _checked(page, "In deck") == "true"
        assert _checked(page, "In price") == "true"
        last = menu.locator("[role=menuitem]").last
        assert "danger" in (last.get_attribute("class") or "") and last.inner_text().strip() == "Delete…"
        if width < 600:
            assert "cs-menu" in menu.get_attribute("class")  # the phone's bottom sheet
        if touch:
            short = page.evaluate(
                "() => [...document.querySelectorAll("
                "'.ctxmenu [role=menuitem], .ctxmenu [role=menuitemcheckbox], .stackhead .kebab')]"
                ".filter(e => e.getClientRects().length)"
                ".map(e => ({ t: e.textContent.trim().slice(0, 20), h: e.getBoundingClientRect().height,"
                " w: e.getBoundingClientRect().width })).filter(r => r.h < 44 || r.w < 44)"
            )
            assert short == [], short
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, MENU_TEXT) == []
        _shot(page, f"catmenu-{width}-{theme}")
        page.keyboard.press("Escape")
        expect(page.locator(".ctxmenu")).to_have_count(0)
        quiet(page)
        assert page.locator(".ctxmenu").count() == 0
        # a right-click (mouse) or a press and hold (touch) on the head opens the same menu
        head = page.locator(".stack[data-group='Ramp'] .stackhead .title")
        head.scroll_into_view_if_needed()
        box = head.bounding_box()
        if touch:
            cdp = page.context.new_cdp_session(page)
            x, y = box["x"] + 20, box["y"] + box["height"] / 2
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
            menu.wait_for(timeout=3000)  # the hold opened the menu, the finger still down
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        else:
            head.click(button="right")
        menu.wait_for(timeout=3000)
        settled(page)
        assert _items(page) == ITEMS
        # the lift of the finger that held picked nothing in the sheet that rose under it
        assert page.locator(BOX).count() == 0 and menu.count() == 1
        # the Maybeboard (not in the deck) shows it in its ticks; the Commander category is Premier
        page.keyboard.press("Escape")
        expect(page.locator(".ctxmenu")).to_have_count(0)
        quiet(page)
        _kebab(page, "Maybeboard").click()
        menu.wait_for(timeout=3000)
        assert _checked(page, "In deck") == "false" and _checked(page, "In price") == "false"
        page.keyboard.press("Escape")
        expect(page.locator(".ctxmenu")).to_have_count(0)
        quiet(page)
        _kebab(page, "Commander").click()
        menu.wait_for(timeout=3000)
        assert _checked(page, "Premier") == "true"
        page.keyboard.press("Escape")
        expect(page.locator(".ctxmenu")).to_have_count(0)
        quiet(page)
        # 200% text: the page and an open menu never scroll sideways
        page.evaluate(TEXT_200)
        settled(page)
        _kebab(page, "Ramp").click()
        menu.wait_for(timeout=3000)
        settled(page)
        assert page.evaluate(MEASURE) == [], page.evaluate(MEASURE)
        _shot(page, f"catmenu-200-{width}-{theme}")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme,touch,app", SURFACES)
def test_rename_premier_delete_and_add_from_the_deck_page(
    server: Server, width: int, theme: str, touch: bool, app: bool
) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width, theme, touch, app)
        menu = page.locator(".ctxmenu[role=menu]")
        box = page.locator(BOX)
        # -- Rename…: saved at once, the stacks drawn again, Undo in the toast
        _kebab(page, "Ramp").click()
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitem]", has_text="Rename…").click()
        box.wait_for(timeout=3000)
        assert box.locator("h2").inner_text() == "Rename “Ramp”"
        assert box.locator("input#catname").input_value() == "Ramp"
        _shot(page, f"rename-{width}-{theme}")
        box.locator("input#catname").fill("Mana")
        box.locator("input#catname").press("Enter")
        page.wait_for_function(STACK, arg="Mana", timeout=6000)
        assert "Ramp" not in _stacks(page)
        assert _toast(page) == "Renamed “Ramp” to “Mana”. Snapshot kept under History."
        patches = [p_["name"] for p_ in server.ark.category_patches]
        assert patches == ["Mana"]
        _shot(page, f"renamed-{width}-{theme}")
        page.locator(".deck-toast .acts button", has_text="Undo").click()
        page.wait_for_function(STACK, arg="Ramp", timeout=6000)
        assert _toast(page) == "Renamed “Mana” back to “Ramp”. Snapshot kept under History."
        assert [p_["name"] for p_ in server.ark.category_patches] == ["Mana", "Ramp"]
        # the card menu's Move to list knows the names as they are now
        cats = page.evaluate("() => JSON.parse(document.getElementById('cards').getAttribute('data-cats'))")
        assert cats.count("Ramp") == 1
        # -- Premier: asks first with the gateway's words; Cancel sends nothing; OK saves
        _kebab(page, "Removal").click()
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitemcheckbox]", has_text="Premier").click()
        box.wait_for(timeout=3000)
        expect(box.locator("h2")).to_have_text("Change the commander category?")
        assert box.locator("h2").inner_text() == "Change the commander category?"
        assert "Cards in a Premier category are the deck's commanders." in box.locator(".body").inner_text()
        _shot(page, f"premier-ask-{width}-{theme}")
        box.locator(".acts button[data-cancel]").click()
        expect(page.locator(BOX)).to_have_count(0)
        assert page.locator(BOX).count() == 0
        assert [p_["name"] for p_ in server.ark.category_patches] == ["Mana", "Ramp"]  # nothing sent
        _kebab(page, "Removal").click()
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitemcheckbox]", has_text="Premier").click()
        box.wait_for(timeout=3000)
        box.locator(".acts button.primary").click()
        page.wait_for_function(
            "() => /Premier category/.test((document.querySelector('.deck-toast .msg') || {}).textContent"
            " || '')",
            timeout=6000,
        )
        assert _toast(page) == "“Removal” is now a Premier category. Snapshot kept under History."
        assert server.ark.category_patches[-1]["isPremier"] is True
        _kebab(page, "Removal").click()
        menu.wait_for(timeout=3000)
        assert _checked(page, "Premier") == "true"
        page.keyboard.press("Escape")
        expect(page.locator(".ctxmenu")).to_have_count(0)
        quiet(page)
        # -- Delete…: the red box names where the cards go and what each choice does (review 2,
        # M6), Keep it, then the solid red Delete category; Undo is the restore
        _kebab(page, "Ramp").click()
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitem]", has_text="Delete…").click()
        box.wait_for(timeout=3000)
        expect(box.locator("h2")).to_have_text("Delete “Ramp”?")
        assert box.locator("h2").inner_text() == "Delete “Ramp”?"
        options = box.locator("select#catmove option").all_inner_texts()
        assert "Ramp" not in options and "No category" in options
        assert "Commander (makes them commanders)" in options and "Removal (makes them commanders)" in options
        assert "Maybeboard (leaves the deck)" in options
        # Removal (4 cards) is Premier now and the other categories are out of the deck, so no
        # plain category is left to pick: "No category" comes picked, never Commander
        assert box.locator("select#catmove").input_value() == ""
        assert (
            "Its 2 cards stay in the deck with no category, listed by type."
            in box.locator(".body").inner_text()
        )
        box.locator("select#catmove").select_option("Removal")
        assert "Its 2 cards move to “Removal” and become commanders." in box.locator(".body").inner_text()
        assert box.locator(".acts button[data-cancel]").inner_text().strip() == "Keep it"
        final = box.locator(".acts button.danger")
        assert final.inner_text().strip() == "Delete category"
        _shot(page, f"delete-{width}-{theme}")
        box.locator(".acts button[data-cancel]").click()
        expect(page.locator(BOX)).to_have_count(0)
        assert server.ark.category_deletes == []
        _kebab(page, "Ramp").click()
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitem]", has_text="Delete…").click()
        box.wait_for(timeout=3000)
        box.locator("select#catmove").select_option("Removal")
        box.locator(".acts button.danger").click()
        # moving the cards into a Premier category asks once more, with the commander sentence
        page.wait_for_function(BOX_TITLE, arg="Delete the category “Ramp”?", timeout=6000)
        assert "Moving these 2 cards there makes them commanders" in box.locator(".body").inner_text()
        assert box.locator(".acts button[data-cancel]").inner_text().strip() == "Keep it"
        box.locator(".acts button.danger").click()
        page.wait_for_function(NO_STACK, arg="Ramp", timeout=6000)
        assert _toast(page) == "Deleted “Ramp”. Its 2 cards are in “Removal”. Snapshot kept under History."
        # the question's box took over the delete box's Back entry: the page stays (box.js)
        assert page.url.endswith("/decks/42?view=text")
        assert server.ark.category_deletes_with_cards == [] and len(server.ark.category_deletes) == 1
        assert page.locator(".stack[data-group='Removal'] .row").count() == 5
        _shot(page, f"deleted-{width}-{theme}")
        page.locator(".deck-toast form[data-undo-box] button").click()
        page.wait_for_function(BOX_TITLE, arg="Undo this change?", timeout=6000)
        assert box.locator(".acts button.primary").inner_text().strip() == "Undo: put the deck back"
        _shot(page, f"undo-delete-{width}-{theme}")
        page.keyboard.press("Escape")
        expect(page.locator(BOX)).to_have_count(0)
        quiet(page)
        # -- + Add category: a name and the switches; its empty stack is drawn; Undo takes it off
        add = page.locator(".deckview .addcat")
        add.scroll_into_view_if_needed()
        assert add.inner_text().strip() == "Add category"
        add.click()
        box.wait_for(timeout=3000)
        assert box.locator("h2").inner_text() == "Add a category"
        switches = box.locator(".ds-sw label")
        assert [t.strip() for t in switches.all_inner_texts()] == ["Premier", "In deck", "In price"]
        assert [c.is_checked() for c in box.locator(".ds-sw input").all()] == [False, True, True]
        _shot(page, f"add-{width}-{theme}")
        # a name the deck has (in any case) is refused in the box, nothing sent
        box.locator("input#catname").fill("removal")
        box.locator(".acts button.primary").click()
        expect(box.locator(".boxnote")).to_contain_text("already has a category named “removal”")
        assert page.locator(BOX).count() == 1 and server.ark.category_creates == []
        assert "already has a category named “removal”" in box.locator(".boxnote").inner_text()
        box.locator("input#catname").fill("Wincons")
        box.locator(".acts button.primary").click()
        page.wait_for_function(STACK, arg="Wincons", timeout=6000)
        assert _toast(page).startswith("Added “Wincons”.")
        assert [c["name"] for c in server.ark.category_creates] == ["Wincons"]
        page.locator(".deck-toast .acts button", has_text="Undo").click()
        page.wait_for_function(NO_STACK, arg="Wincons", timeout=6000)
        assert _toast(page) == "Removed “Wincons” again. Snapshot kept under History."
        assert server.ark.category_deletes_with_cards == []
        assert page.evaluate(MEASURE) == []
        assert errors == []
        browser.close()


def test_the_delete_box_never_picks_commander_and_offers_no_category(server: Server) -> None:
    """Review 2, M6: the delete box's pick is the biggest plain category (never Commander, a
    Premier category or a board out of the deck), each choice is named for what it does, the
    sentence follows the pick, and "No category" leaves the cards in the deck with none (an
    Archidekt category delete after the cards are cleared); a Premier pick still asks once more."""
    from playwright.sync_api import sync_playwright

    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 375, "dark", True, True)
        menu = page.locator(".ctxmenu[role=menu]")
        box = page.locator(BOX)
        rows_before = page.locator(".deckview li.row").count()
        # Removal (3 cards): Ramp (2, plain) comes picked, not Commander (Premier) or Maybeboard
        _kebab(page, "Removal").click()
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitem]", has_text="Delete…").click()
        box.wait_for(timeout=3000)
        expect(box.locator("select#catmove")).to_have_value("Ramp")
        select = box.locator("select#catmove")
        assert select.input_value() == "Ramp"
        assert "Its 3 cards stay in the deck, in “Ramp”." in box.locator(".body").inner_text()
        labels = select.locator("option").all_inner_texts()
        assert labels[0] == "No category" and "Commander (makes them commanders)" in labels
        assert "Maybeboard (leaves the deck)" in labels and "Removal" not in labels
        select.select_option("Maybeboard")
        assert "Its 3 cards move to “Maybeboard”, out of the deck." in box.locator(".body").inner_text()
        select.select_option("")
        assert (
            "Its 3 cards stay in the deck with no category, listed by type."
            in box.locator(".body").inner_text()
        )
        _shot(page, "delete-no-category-375")
        box.locator(".acts button.danger").click()
        page.wait_for_function(NO_STACK, arg="Removal", timeout=6000)
        assert (
            _toast(page)
            == "Deleted “Removal”. Its 3 cards have no category now. Snapshot kept under History."
        )
        assert page.locator(".deckview li.row").count() == rows_before  # every card still in the deck
        assert server.ark.category_deletes_with_cards == [] and len(server.ark.category_deletes) == 1
        assert all("Removal" not in (r.get("categories") or []) for r in server.ark.decks[42]["cards"])
        # with no plain category left, Ramp's box picks "No category"; Commander asks once more
        _kebab(page, "Ramp").click()
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitem]", has_text="Delete…").click()
        box.wait_for(timeout=3000)
        expect(box.locator("select#catmove")).to_have_value("")
        assert select.input_value() == ""
        select.select_option("Commander")
        assert "Its 2 cards move to “Commander” and become commanders." in box.locator(".body").inner_text()
        box.locator(".acts button.danger").click()
        page.wait_for_function(BOX_TITLE, arg="Delete the category “Ramp”?", timeout=6000)
        assert "makes them commanders" in box.locator(".body").inner_text()
        box.locator(".acts button[data-cancel]").click()
        quiet(page)
        assert len(server.ark.category_deletes) == 1  # kept
        assert errors == [], errors
        browser.close()


def test_the_delete_box_says_what_happens_to_cards_out_of_the_deck_and_to_commanders(server: Server) -> None:
    """Confirmation pass, N1: deleting the Maybeboard said its card "stays in the deck with no
    category" and offered "Sideboard (leaves the deck)" for a card that was never in the deck.
    Each choice now says what happens from where the cards are: out of the deck they join it
    (No category, a plain category, Commander) or move to another board and stay out, which
    comes picked; from Commander they stop being commanders unless they go to another Premier
    category. The sentence the server shows in its box says they join the deck too."""
    from playwright.sync_api import sync_playwright

    server.ark.add_side_row(42, "Sol Ring")
    server.ark.decks[42]["categories"].append(
        {"name": "Sideboard", "isPremier": False, "includedInDeck": False, "includedInPrice": False}
    )
    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, "light", False, False)
        menu = page.locator(".ctxmenu[role=menu]")
        box = page.locator(BOX)
        _kebab(page, "Maybeboard").click()
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitem]", has_text="Delete…").click()
        box.wait_for(timeout=3000)
        expect(box.locator("select#catmove")).to_have_value("Sideboard")
        select = box.locator("select#catmove")
        body = box.locator(".body")
        m = re.match(r"Its (\d+) cards? ", body.inner_text())
        assert m, body.inner_text()
        n = int(m.group(1))  # the fixture deck may hold a maybeboard row of its own
        one = n == 1
        its = f"Its {n} card{'' if one else 's'} "
        assert select.input_value() == "Sideboard"  # the other board: the cards stay as they are
        assert its + f"move{'s' if one else ''} to “Sideboard”, still out of the deck." in body.inner_text()
        labels = select.locator("option").all_inner_texts()
        assert "Sideboard (still out of the deck)" in labels and "leaves the deck" not in " ".join(labels)
        assert labels[0] == "No category (joins the deck)" and "Ramp (joins the deck)" in labels
        assert f"Commander (makes {'it a commander' if one else 'them commanders'})" in labels
        select.select_option("")
        joins = its + f"join{'s' if one else ''} the deck"
        assert joins + " with no category, listed by type." in body.inner_text()
        select.select_option("Ramp")
        assert joins + ", in “Ramp”." in body.inner_text()
        select.select_option("Commander")
        becomes = f"become{'s' if one else ''} {'a commander' if one else 'commanders'}"
        assert joins + f" in “Commander” and {becomes}." in body.inner_text()
        assert "stay" not in body.inner_text()
        # the server's own box (the move changes what counts) says the cards join the deck
        select.select_option("")
        box.locator(".acts button.danger").click()
        page.wait_for_function(BOX_TITLE, arg="Delete the category “Maybeboard”?", timeout=6000)
        words = box.locator(".body").inner_text()
        assert f"its {n} card{'' if one else 's'} join{'s' if one else ''} the deck with no category" in words
        assert "stay in the deck" not in words and "stays in the deck" not in words
        box.locator(".acts button[data-cancel]").click()
        quiet(page)
        assert server.ark.category_deletes == []
        # Commander (Premier, holding the commander): its cards stop being commanders
        _kebab(page, "Commander").click()
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitem]", has_text="Delete…").click()
        box.wait_for(timeout=3000)
        expect(box.locator("select#catmove")).to_be_visible()
        n = int(page.locator(".stack[data-group='Commander'] li.row").count())
        assert n >= 1
        select.select_option("")
        text = body.inner_text()
        assert "with no category, listed by type and " in text and "no longer" in text and "commander" in text
        select.select_option("Ramp")
        assert "in “Ramp” and " in body.inner_text() and "no longer" in body.inner_text()
        select.select_option("Maybeboard")
        text = body.inner_text()
        assert "to “Maybeboard”, out of the deck and " in text and "no longer" in text
        box.locator(".acts button[data-cancel]").click()
        assert errors == [], errors
        browser.close()


def test_the_owners_name_in_the_banner_is_a_44px_target_on_a_phone(server: Server) -> None:
    """The phone banner's owner link (review 2, L8): a 44 px tap target without a taller line."""
    from playwright.sync_api import sync_playwright

    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 375, "light", True, True)
        owner = page.locator(".banner .rows .owner-sm")
        assert owner.is_visible()
        b = owner.bounding_box()
        assert b and b["height"] >= 44, b
        line = owner.evaluate("el => el.parentElement.getBoundingClientRect().height")
        assert line < 44 + 24, line  # the line itself stays short: the padding makes the target
        assert errors == [], errors
        browser.close()
