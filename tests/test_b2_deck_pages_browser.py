"""Package B2 in a browser: the deck menu (⋮ More, right-click), the Export, Delete, Clone and
Rename boxes, the New deck box's tabs and preview, the Goldfish card menu, and the settings rows
at phone and desktop widths without overflow."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_deck_menu_browser import Server, _chromium, _overflow, _page

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.ark.folders["alice"] = [{"id": 501, "name": "Brews", "private": False}]
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _menu(page):
    menu = page.locator(".ctxmenu[role=menu]")
    menu.wait_for(timeout=3000)
    return menu


def _box(page, box_id: str):
    box = page.locator(f"#{box_id}.mtgbox[role=dialog]")
    box.wait_for(state="visible", timeout=3000)
    return box


@pytest.mark.parametrize("width", [390, 1280])
def test_the_deck_menu_opens_from_more_and_a_right_click(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        banner = page.locator(".banner[data-deck-id='42']")
        assert banner.get_attribute("data-menu-ready") is not None
        assert banner.locator("details.deckmore").is_hidden()
        more = banner.locator("button.deckmenu")
        assert more.get_attribute("aria-haspopup") == "menu" and more.bounding_box()["height"] >= 36
        more.click()
        menu = _menu(page)
        labels = [t.strip() for t in menu.locator("[role=menuitem]").all_inner_texts()]
        assert menu.locator(".head").inner_text() == "Sample Commander Deck"
        # navigation round 2 (Ben, 2026-10-25): Edit, Test and play and Proposals sit on the
        # banner, Copy link is in Share…, and the deck page opens in this tab
        for want in (
            "Rename…",
            "Move to folder…",
            "Add a package…",
            "Share…",
            "Export…",
            "Compare with…",
            "Deck settings",
            "Deck stats",
            "Deck reports",
            "History and snapshots",
            "Open on Archidekt",
            "Delete deck…",
        ):
            assert any(x == want for x in labels), (want, labels)
        for gone in ("Open in a new tab", "Edit deck", "Goldfish", "Copy link", "Proposals for this deck"):
            assert gone not in labels, (gone, labels)
        assert "More" not in labels and "Solo test only" not in labels  # Forge is off here
        assert menu.locator("[role=menuitem].danger", has_text="Delete deck…").count() == 1
        assert _overflow(page) == 0
        # Export… adopts the page's box: tabs, the texts, the files
        menu.locator("[role=menuitem]", has_text="Export…").click()
        box = _box(page, "export")
        assert box.locator("h2").inner_text() == "Export “Sample Commander Deck”"
        tabs = [t.strip() for t in box.locator("[role=tab]").all_inner_texts()]
        assert tabs == ["Archidekt text", "Plain list", "Side and maybe", "Files"]
        assert "Sol Ring" in box.locator("#exp-arch").input_value()
        box.locator("[role=tab]", has_text="Files").click()
        assert box.locator(".files a[download]").count() == 7 and box.locator("#exp-arch").is_hidden()
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.getElementById('export').hidden", timeout=3000)
        page.wait_for_function("() => location.pathname === '/decks/42'", timeout=3000)
        # Delete deck…: the solid red button waits for the typed name
        page.locator(".banner").click(button="right")
        _menu(page).locator("[role=menuitem]", has_text="Delete deck…").click()
        box = _box(page, "delete")
        red = box.locator(".acts button.danger", has_text="Delete this deck")
        assert red.is_disabled()
        box.locator("#typed").fill("sample commander deck")
        assert red.is_enabled()
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        # Rename…: the JSON route, then the page again with the notice
        more.click()
        _menu(page).locator("[role=menuitem]", has_text="Rename…").click()
        rename = page.locator(".mtgbox[role=dialog]:not([hidden])")
        rename.wait_for(timeout=3000)
        rename.locator("#dk-name").fill("Renamed deck")
        rename.locator("button.primary", has_text="Rename").click()
        page.wait_for_url("**/decks/42?ok=saved", timeout=5000)
        assert page.locator(".banner h1").inner_text().strip() == "Renamed deck"
        assert server.ark.decks[42]["name"] == "Renamed deck"
        assert not errors, errors
        browser.close()


def test_the_clone_box_and_a_cards_own_menu(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        page.locator(".banner button[data-box-open='clone']").click()
        box = _box(page, "clone")
        assert box.locator("#clone-name").input_value() == "Copy of - Sample Commander Deck"
        options = [t.strip() for t in box.locator("#clone-folder option").all_inner_texts()]
        assert options[0] == "Top level (no folder)" and any("Brews" in o for o in options)
        box.locator("#clone-folder").select_option("501")
        box.locator(".acts button.primary", has_text="Clone").click()
        page.wait_for_url("**?ok=created", timeout=8000)
        new_id = int(page.url.split("/decks/")[1].split("?")[0])
        assert server.ark.deck_folder.get(new_id) == 501
        # your own deck's card menu carries Add to deck… and Open on Scryfall too (F6)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        page.locator(".deckview .row[data-card='Cultivate']").first.click(button="right")
        labels = [t.strip() for t in _menu(page).locator("[role=menuitem]").all_inner_texts()]
        assert any(x == "Add to deck…" for x in labels) and any(x == "Open on Scryfall" for x in labels), (
            labels
        )
        assert any(x == "Remove from deck" for x in labels)
        assert not errors, errors
        browser.close()


@pytest.mark.parametrize("width", [344, 1280])
def test_the_new_deck_box_tabs_preview_and_name_check(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        page.locator("a[data-box-open='newdeck']").first.click()
        box = _box(page, "newdeck")
        assert box.locator("#source").is_hidden() and box.locator(".filepick").is_hidden()
        assert box.locator(".nd-pt").inner_text() == "Unnamed deck"
        box.locator("#name").fill("Simic value")
        assert (
            box.locator(".nd-pt").inner_text() == "Simic value"
            and box.locator(".nd-cnt").inner_text() == "11/120"
        )
        box.locator("[role=tab][data-src='paste']").click()
        assert box.locator("#source").is_visible() and box.locator(".filepick").is_hidden()
        box.locator("#source").fill("1 Sol Ring\n2 Opt\n")
        assert box.locator(".nd-pn").inner_text() == "3 cards"
        box.locator("[role=tab][data-src='file']").click()
        assert box.locator(".filepick").is_visible()
        # an empty name is said in the box, nothing is sent
        box.locator("#name").fill("")
        box.locator(".acts button.primary", has_text="Create deck").click()
        assert box.locator("#nd-ne").is_visible() and page.url.endswith("/decks")
        assert _overflow(page) == 0
        # the old address opens the page with the box; closing it puts /decks in the bar
        page.goto(f"{server.base}/decks/new", wait_until="networkidle")
        assert _box(page, "newdeck").is_visible()
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.getElementById('newdeck').hidden", timeout=3000)
        if width >= 600:  # (a phone's sheet closes through the history, box.js's own test)
            page.wait_for_function("() => location.pathname === '/decks'", timeout=3000)
        assert not errors, errors
        browser.close()


def test_goldfish_cards_have_the_shared_menu_with_the_games_moves(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.goto(f"{server.base}/decks/42/goldfish", wait_until="networkidle")
        assert page.locator("#gf-hint").inner_text().strip() == ""
        page.locator("[data-act=keep]").click()
        card = page.locator("#gf-hand .gf-card").first
        card.click(button="right")
        menu = _menu(page)
        labels = [t.strip() for t in menu.locator("[role=menuitem]").all_inner_texts()]
        assert labels[0] == "Open card" and "Play to the battlefield" in labels and "Copy name" in labels
        assert "Open on Scryfall" in labels and "Add to collection" in labels
        menu.locator("[role=menuitem]", has_text="Play to the battlefield").click()
        page.wait_for_timeout(100)
        assert page.locator("#gf-battlefield .gf-card").count() == 1
        assert not errors, errors
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(344, "dark"), (810, "light"), (1280, "dark")])
def test_deck_settings_rows_fit_every_width(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        touch = width < 600  # a phone: finger-sized controls (44 px); a mouse keeps the theme's --ctl
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme, has_touch=touch)
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        assert _overflow(page) == 0
        assert page.locator(".ds-nav").is_visible() == (width >= 1100)
        assert page.locator("#ds-delete a[data-box-open='delete']").bounding_box()["height"] >= (
            44 if touch else 36
        )
        assert page.locator("#collaborators form.addtag button").bounding_box()["height"] >= (
            44 if touch else 36
        )
        page.locator("#ds-delete a[data-box-open='delete']").click()
        assert _box(page, "delete").locator("h2").inner_text() == "Delete “Sample Commander Deck”?"
        assert not errors, errors
        browser.close()


# -- the editable Categories panel (F7 a, Ben 21 Oct) ----------------------------------------------

OPEN_BOXES = "() => document.querySelectorAll('.mtgbox[role=dialog]:not([hidden])').length"
CAT_ROWS = """() => [...document.querySelectorAll('#categories li[data-cat]')].map(li => {
  const vw = document.documentElement.clientWidth;
  const inside = [...li.querySelectorAll('input[type=text], label.ds-flag')]
    .every(e => e.getBoundingClientRect().right <= vw + 0.5 && e.getBoundingClientRect().left >= -0.5);
  return { name: li.dataset.name, inside,
    nameH: li.querySelector('input[type=text]').getBoundingClientRect().height,
    flags: [...li.querySelectorAll('label.ds-flag')].map(l =>
      [l.innerText.trim(), l.getBoundingClientRect().height, l.querySelector('input').checked]) };
})"""


@pytest.mark.parametrize(
    ("width", "scheme"),
    [(344, "light"), (344, "dark"), (810, "light"), (810, "dark"), (1280, "light"), (1280, "dark")],
)
def test_the_categories_panel_fits_reads_and_saves_everywhere(
    server: Server, width: int, scheme: str
) -> None:
    """Each category: its name in a box and the three switches with their words, all inside the
    window, 44 px on a phone, text at 4.5:1; Undo changes puts the boxes back; Save categories is
    plain and saves at once; touching the commander opens the confirm box first (the backend's
    words), whose Save sends the form with ``confirmed``."""
    from playwright.sync_api import sync_playwright

    from .test_card_search_filters_browser import CONTRAST
    from .test_deck_categories import _give_categories

    _give_categories(server.ark)
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        touch = width < 600
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme, has_touch=touch)
        page.goto(f"{server.base}{'/decks/42/settings'}", wait_until="networkidle")
        assert _overflow(page) == 0
        rows = page.evaluate(CAT_ROWS)
        assert {r["name"] for r in rows} >= {"Commander", "Ramp", "Maybeboard"}, rows
        for r in rows:
            assert r["inside"], r
            assert r["nameH"] >= 44, r
            assert [f[0] for f in r["flags"]] == ["Premier", "Not in deck", "Not in price"], r
            assert all(f[1] >= (44 if touch else 36) for f in r["flags"]), r
        assert page.evaluate(CONTRAST, "#categories .nm input, #categories .ds-flag, #categories .foot") == []
        assert page.locator("#categories button.btn-primary").count() == 0
        panel = page.locator("#categories")
        # Undo changes puts a typed name and a switched flag back
        ramp = panel.locator("li[data-name='Ramp']")
        ramp.locator("input[type=text]").fill("Mana")
        ramp.locator("label.ds-flag", has_text="Not in price").click()
        assert ramp.locator("input[name^=not_in_price_]").is_checked()
        panel.locator("button[type=reset]").click()
        assert ramp.locator("input[type=text]").input_value() == "Ramp"
        assert not ramp.locator("input[name^=not_in_price_]").is_checked()
        # a plain change saves at once, no box
        ramp.locator("input[type=text]").fill("Mana")
        panel.locator("button[name=action][value=save]").click()
        page.wait_for_url(lambda u: "ok=categories" in u, timeout=5000)
        page.wait_for_load_state("networkidle")
        assert page.evaluate(OPEN_BOXES) == 0
        assert "Categories saved to Archidekt." in page.locator("#categories .notice").inner_text()
        assert page.locator("#categories li[data-name='Mana']").count() == 1
        assert [b["name"] for b in server.ark.category_patches] == ["Mana"]
        # touching the commander asks first, in the box, with the backend's sentence
        cmd = page.locator("#categories li[data-name='Commander']")
        cmd.locator("label.ds-flag", has_text="Premier").click()
        panel.locator("button[name=action][value=save]").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        assert box.locator("h2").inner_text() == "Change the commander category?"
        assert "Cards in a Premier category are the deck's commanders." in box.inner_text()
        assert box.locator(".acts button.primary").inner_text().strip() == "Save categories"
        assert len(server.ark.category_patches) == 1  # nothing sent yet
        box.locator("button", has_text="Cancel").click()
        box.wait_for(state="hidden", timeout=3000)
        assert page.evaluate(OPEN_BOXES) == 0
        assert len(server.ark.category_patches) == 1
        panel.locator("button[name=action][value=save]").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        box.locator(".acts button.primary").click()
        page.wait_for_url(lambda u: "ok=categories" in u and "settings" in u, timeout=5000)
        page.wait_for_load_state("networkidle")
        assert [b["isPremier"] for b in server.ark.category_patches][-1] is False
        # a Not in deck switch asks too, in the box, with its own title and sentence
        sent = len(server.ark.category_patches)
        panel = page.locator("#categories")
        panel.locator("li[data-name='Mana'] label.ds-flag:has(input[name^=not_in_deck_])").click()
        panel.locator("button[name=action][value=save]").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        assert box.locator("h2").inner_text() == "Change which cards count in the deck?"
        assert "don't count in the deck" in box.inner_text()
        box.locator("button", has_text="Cancel").click()
        box.wait_for(state="hidden", timeout=3000)
        assert len(server.ark.category_patches) == sent
        assert not errors, errors
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(344, "light"), (1280, "dark")])
def test_create_and_delete_categories_ask_in_the_box(server: Server, width: int, scheme: str) -> None:
    """Create and delete (Ben 22 Oct, one pane of glass): a plain new category is made at once; a
    row's Delete always asks in the box, and one with cards asks where they go first (Archidekt
    only deletes an empty category), the cards moving before the delete; every button inside the
    window, 44 px on a phone."""
    from playwright.sync_api import sync_playwright

    from .test_deck_categories import RAMP, _give_categories

    _give_categories(server.ark)
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        touch = width < 600
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme, has_touch=touch)
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        assert _overflow(page) == 0
        panel = page.locator("#categories")
        for b in panel.locator("button.del").all() + [panel.locator("#catnew button")]:
            box_ = b.bounding_box()
            assert box_ and box_["x"] >= 0 and box_["x"] + box_["width"] <= width, box_
            assert box_["height"] >= (44 if touch else 32), box_
        # a plain new category: no box, made at once
        panel.locator("#newcat").fill("Removal")
        panel.locator("#catnew button").click()
        page.wait_for_url(lambda u: "ok=category_created" in u, timeout=5000)
        page.wait_for_load_state("networkidle")
        assert page.evaluate(OPEN_BOXES) == 0
        assert [c["name"] for c in server.ark.category_creates] == ["Removal"]
        # Delete on a category with cards: the box asks where they go
        panel = page.locator("#categories")
        panel.locator("li[data-name='Ramp'] button.del").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        # the box names its category, stacks its paragraphs and offers Keep it (M1, M3, L1 of the
        # 0.7.42 UI review)
        assert box.locator("h2").inner_text() == "Delete the category “Ramp”?"
        assert box.locator(".body > .stack").count() == 1 and box.locator(".body > .stack > *").count() >= 2
        assert box.locator(".acts button[data-cancel]").inner_text().strip() == "Keep it"
        assert "“Ramp” still has 2 cards." in box.inner_text()
        assert "Ramp" not in box.locator("select option").all_inner_texts()
        box.locator("select").select_option("Removal")
        assert server.ark.category_deletes == []
        box.locator(".acts button.danger").click()
        page.wait_for_url(lambda u: "ok=category_deleted" in u, timeout=5000)
        page.wait_for_load_state("networkidle")
        assert len(server.ark.category_deletes) == 1 and server.ark.category_deletes_with_cards == []
        rows = [r for r in server.ark.decks[42]["cards"] if r["card"]["oracleCard"]["name"] in RAMP]
        assert rows and all(r["categories"] == ["Removal"] for r in rows), rows
        assert page.locator("#categories li[data-name='Ramp']").count() == 0
        assert not errors, errors
        browser.close()


def test_the_delete_box_says_when_the_picked_category_makes_the_cards_commanders(server: Server) -> None:
    """Fact-check 0.7.42 (S-01's box): moving a deleted category's cards into Commander (or another
    Premier category) makes them commanders, and into Maybeboard takes them out of the deck; the box
    says so as soon as that category is picked, not only on the page that asks again after."""
    from playwright.sync_api import sync_playwright

    from mtg_gateway import modes

    from .test_deck_categories import _give_categories

    _give_categories(server.ark)
    server.ark.decks[42]["categories"].append({"name": "Removal", "isPremier": False, "includedInDeck": True})
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        page.locator("#categories li[data-name='Ramp'] button.del").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        warn = box.locator("[data-move-warn]")
        # nothing is picked when the box opens (the first category is often Commander), and
        # Delete waits for a pick
        assert box.locator("select").input_value() == ""
        assert box.locator(".acts button.danger").is_disabled() and not warn.is_visible()
        for target, says in (
            ("Commander", modes.category_words("move_commander", n=2)),
            ("Maybeboard", modes.category_words("move_counts", n=2)),
            ("Removal", ""),
        ):
            box.locator("select").select_option(target)
            assert warn.inner_text().strip() == says, (target, warn.inner_text())
            assert (warn.get_attribute("role") == "status") and warn.is_visible() == bool(says)
            assert box.locator(".acts button.danger").is_enabled()
        assert server.ark.category_deletes == []
        assert not errors, errors
        browser.close()


def test_the_delete_unused_and_new_category_boxes_say_what_they_do(server: Server) -> None:
    """UI review 0.7.42 (M2): Delete unused counts and names the categories, and a guarded new
    category is asked as a create, Premier winning when Not in deck is on too."""
    from playwright.sync_api import sync_playwright

    from mtg_gateway import modes

    from .test_deck_categories import _give_categories

    _give_categories(server.ark)
    server.ark.decks[42]["categories"].append({"name": "Spare", "isPremier": False, "includedInDeck": True})
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        panel = page.locator("#categories")
        unused = panel.locator("button[value=unused]")
        # its own line, right-aligned, under Save categories and Undo changes
        save = panel.locator("button[name=action][value=save]").bounding_box()
        ub = unused.bounding_box()
        assert ub["y"] >= save["y"] + save["height"] - 1, (save, ub)
        assert ub["x"] + ub["width"] >= panel.bounding_box()["x"] + panel.bounding_box()["width"] - 40, ub
        unused.click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        # (the Maybeboard holds a Sol Ring here, so Spare is the one unused category: the singular)
        assert box.locator("h2").inner_text() == "Delete the unused category “Spare”?"
        assert "Deletes “Spare”." in box.inner_text()
        assert box.locator(".acts button.danger").inner_text().strip() == "Delete “Spare”"
        assert box.locator(".acts button[data-cancel]").inner_text().strip() == "Keep it"
        assert box.locator(".body > .stack").count() == 1
        page.keyboard.press("Escape")
        box.wait_for(state="hidden", timeout=3000)
        # a new Premier category: created, not changed
        panel.locator("#newcat").fill("Big")
        panel.locator("#catnew label.ds-flag", has_text="Premier").click()
        panel.locator("#catnew button").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        assert box.locator("h2").inner_text() == "Create “Big” as a Premier category?"
        assert (
            box.locator(".body > .stack > p").first.inner_text() == modes.CATEGORY_WORDS["create_commander"]
        )
        assert "Changing it" not in box.inner_text()
        assert box.locator(".acts button.primary").inner_text().strip() == "Create category"
        assert box.locator(".acts button[data-cancel]").inner_text().strip() == "Cancel"
        box.locator("button[data-cancel]").click()
        box.wait_for(state="hidden", timeout=3000)
        # Not in deck on too: Premier still wins; Not in deck alone, or a Sideboard, is the counts box
        panel.locator("#catnew label.ds-flag", has_text="Not in deck").click()
        panel.locator("#catnew button").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        assert box.locator("h2").inner_text() == "Create “Big” as a Premier category?"
        box.locator("button[data-cancel]").click()
        box.wait_for(state="hidden", timeout=3000)
        panel.locator("#catnew label.ds-flag", has_text="Premier").click()
        panel.locator("#catnew button").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        assert box.locator("h2").inner_text() == "Create “Big” as a category that doesn't count?"
        assert box.locator(".body > .stack > p").first.inner_text() == modes.CATEGORY_WORDS["create_counts"]
        box.locator("button[data-cancel]").click()
        box.wait_for(state="hidden", timeout=3000)
        panel.locator("#catnew label.ds-flag", has_text="Not in deck").click()
        panel.locator("#newcat").fill("Sideboard")
        panel.locator("#catnew button").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        assert box.locator("h2").inner_text() == "Create “Sideboard” as a category that doesn't count?"
        assert server.ark.category_creates == [] and server.ark.category_deletes == []
        assert not errors, errors
        browser.close()


CAT_GRID = """() => [...document.querySelectorAll('#categories li[data-cat]')].map(li => {
  const r = s => { const e = li.querySelector(s); return e ? e.getBoundingClientRect() : null; };
  const nm = r('.nm input'), cnt = r('.cnt'), del = r('.del'), sw = r('.ds-sw');
  const row = li.getBoundingClientRect();
  return { name: li.dataset.name, nm, cnt, del, sw, row, lines: Math.round(row.height / 44) };
})"""


@pytest.mark.parametrize("width", [344, 390, 810, 1366])
def test_category_rows_are_one_grid_with_the_delete_buttons_lined_up(server: Server, width: int) -> None:
    """UI review 0.7.42 (M5): at every width each row has its name, count and Delete on the first
    line and the switches under them, every Delete button at the same x, no row wider than the
    window and no near-empty line."""
    from playwright.sync_api import sync_playwright

    from .test_deck_categories import _give_categories

    _give_categories(server.ark)
    server.ark.decks[42]["categories"].append(
        {"name": "A much longer category name", "isPremier": False, "includedInDeck": True}
    )
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        touch = width < 600
        browser, page, errors = _page(p, server, sid, width, has_touch=touch)
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        assert _overflow(page) == 0
        rows = [r for r in page.evaluate(CAT_GRID) if r["del"]]
        assert len(rows) >= 3, rows
        for r in rows:
            top = r["nm"]["y"]
            # name, count and Delete share the first line; the switches sit below it
            assert abs(r["cnt"]["y"] + r["cnt"]["height"] / 2 - (top + r["nm"]["height"] / 2)) <= 8, r
            assert abs(r["del"]["y"] + r["del"]["height"] / 2 - (top + r["nm"]["height"] / 2)) <= 8, r
            assert r["sw"]["y"] >= top + r["nm"]["height"] - 1, r
            assert r["del"]["x"] + r["del"]["width"] <= width + 0.5 and r["nm"]["x"] >= 0, r
            # two lines (three when the switches wrap on a narrow phone): no four-line rows
            assert r["row"]["height"] <= 3 * 52, r
        xs = {round(r["del"]["x"]) for r in rows}
        assert len(xs) == 1, rows
        assert not errors, errors
        browser.close()


def test_the_new_deck_preview_tile_shows_theorycrafted_and_the_game_type(server: Server) -> None:
    """UI review 0.7.42 (L4): the tile's details line follows the Theorycrafted switch and the
    game type (nothing for Any)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        page.locator("a[data-box-open='newdeck']").first.click()
        box = _box(page, "newdeck")
        fmt = box.locator(".nd-pf")
        assert fmt.inner_text() == "Commander · Private"  # a new deck starts private
        box.locator("label.switch", has_text="Theorycrafted").click()
        assert fmt.inner_text() == "Commander · Private · Theorycrafted"
        box.locator("#game").select_option("mtgo")
        assert fmt.inner_text() == "Commander · Private · Theorycrafted · MTGO"
        box.locator("label.switch", has_text="Private").click()
        assert fmt.inner_text() == "Commander · Public · Theorycrafted · MTGO"
        box.locator("label.switch", has_text="Theorycrafted").click()
        assert fmt.inner_text() == "Commander · Public · MTGO"
        box.locator("#game").select_option("any")
        assert fmt.inner_text() == "Commander · Public"
        assert not errors, errors
        browser.close()


# the switch tracks of one category row, with its name box and the row itself
SWITCH_TRACKS = """name => { const li = document.querySelector(`#categories li[data-name='${name}']`);
  const r = e => e.getBoundingClientRect();
  return { input: r(li.querySelector('.nm input')), row: r(li),
    labels: [...li.querySelectorAll('.ds-sw label')].map(l => r(l).height),
    tracks: [...li.querySelectorAll('.ds-sw .track')].map(t => { const b = r(t); return [b.top, b.bottom]; })
  }; }"""


@pytest.mark.parametrize("width", [344, 390, 810, 1366])
def test_the_switches_sit_as_close_under_the_name_as_beside_each_other(server: Server, width: int) -> None:
    """0.7.43: on a phone the third switch wraps under the other two, 12 px below their tracks (the
    gap between the switches beside each other), not on a 44 px line of its own, and the switch
    block starts and ends close to the name box and the row's edge; the finger boxes stay 44 px."""
    from playwright.sync_api import sync_playwright

    from .test_deck_categories import _give_categories

    _give_categories(server.ark)
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        touch = width < 600
        browser, page, errors = _page(p, server, sid, width, has_touch=touch)
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        assert _overflow(page) == 0
        m = page.evaluate(SWITCH_TRACKS, "Ramp")
        assert len(m["tracks"]) == 3 and all(h >= (44 if touch else 36) for h in m["labels"]), m
        lines = sorted({round(t[0]) for t in m["tracks"]})
        assert len(lines) == (2 if touch else 1), (width, lines)
        first_top = min(t[0] for t in m["tracks"])
        last_bottom = max(t[1] for t in m["tracks"])
        # name box to the first track, and the last track to the row's end: 10 and 12 px on a phone
        # (the overlap), 16 and 18 px (the 44 px line, the row's padding) with a mouse
        near = 14 if touch else 18
        assert first_top - (m["input"]["y"] + m["input"]["height"]) <= near, (width, m)
        assert m["row"]["y"] + m["row"]["height"] - last_bottom <= near, (width, m)
        if touch:
            # the wrapped switch's track sits 12 px under the first line's tracks (the column gap)
            below = min(t[0] for t in m["tracks"] if round(t[0]) == lines[1])
            above = max(t[1] for t in m["tracks"] if round(t[0]) == lines[0])
            assert 8 <= below - above <= 14, (width, below - above, m)
        assert not errors, errors
        browser.close()


NEW_DECK_TOPS = """() => { const q = s => document.querySelector(`.mtgbox ${s}`).getBoundingClientRect();
  return { name: q('#name').y, nameLabel: q("label[for=name]").y, formatLabel: q("label[for=format]").y,
    format: q('.nd-top .msel-btn, .nd-top #format').y, column: q('.nd-prev').y, tile: q('.ndtile').y }; }"""


@pytest.mark.parametrize("width", [810, 1280])
def test_the_new_deck_fields_line_up_and_the_preview_tile_sits_at_the_top(server: Server, width: int) -> None:
    """0.7.43: Deck name and Format share one top edge (the box's own field spacing set Format
    12 px lower), and the preview tile starts at the top of its column, level with the fields."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        page.locator("a[data-box-open='newdeck']").first.click()
        _box(page, "newdeck")
        page.wait_for_timeout(250)
        m = page.evaluate(NEW_DECK_TOPS)
        assert abs(m["name"] - m["format"]) <= 1 and abs(m["nameLabel"] - m["formatLabel"]) <= 1, m
        assert abs(m["tile"] - m["column"]) <= 1 and abs(m["tile"] - m["nameLabel"]) <= 2, m
        assert not errors, errors
        browser.close()


# A mark over a tile's text: the dots and the finish badge are boxes; the quantity corner (top
# left) and the companion corner (top right) are triangles, so only the text box's corner nearest
# each counts (the corner's square reaches further than its drawn triangle).
TILE_MARKS = """() => { const out = [];
  const ov = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  document.querySelectorAll('.deckview .c').forEach(c => { const ph = c.querySelector('.ph'); if (!ph) return;
    const texts = [...c.querySelectorAll('.ph .nm, .ph .ty, .ph .mana')]
      .filter(t => t.getBoundingClientRect().width);
    const say = (m, t) => out.push([c.dataset.card, m.className, t.className]);
    c.querySelectorAll('.owned, .tagdot, .finish').forEach(m => texts.forEach(t => {
      if (ov(m.getBoundingClientRect(), t.getBoundingClientRect())) say(m, t); }));
    c.querySelectorAll('.qty, .corner').forEach(m => { const a = m.getBoundingClientRect();
      texts.forEach(t => {
      const b = t.getBoundingClientRect(); if (!ov(a, b)) return;
      const inside = m.classList.contains('qty')
        ? (b.left - a.left) / a.width + (b.top - a.top) / a.height < 1
        : (b.right - a.left) / a.width > (b.top - a.top) / a.height;
      if (inside) say(m, t); }); }); });
  return out; }"""


def _own(server: Server, sid: str, names: list[str]) -> None:
    """Put the cards in the member's collection, so their tiles carry the owned dot."""
    import re

    import httpx

    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/collection").text).group(1)
    r = h.post(
        "/collection/api/add",
        headers={"X-CSRF-Token": csrf},
        json={"items": [{"name": n, "quantity": 1} for n in names], "source": "scan"},
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("width", [344, 390, 810, 1366])
def test_a_tiles_marks_never_sit_on_its_text(server: Server, width: int) -> None:
    """0.7.43: on a tile without a picture the owned dot and the finish badge sat on the type line
    ("Artifact") and the companion corner could reach the name's line. At S, M and L, in the grid
    and the stacks, no mark covers a word."""
    from playwright.sync_api import sync_playwright

    server.ark.add_side_row(42, "Delver of Secrets // Insectile Aberration", category="Sideboard")
    for row in server.ark.decks[42]["cards"]:
        if row["card"]["oracleCard"]["name"].startswith("Delver"):
            row["companion"] = True
    _chromium()
    sid = server.sign_in_and_link()
    _own(
        server, sid, ["Sol Ring", "Delver of Secrets // Insectile Aberration", "Aesi, Tyrant of Gyre Strait"]
    )
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, has_touch=width < 600)
        for size in ("s", "m", "l"):
            server.db.set_pref("user-1", "card.size", size)
            for view in ("grid", "stacks"):
                page.goto(f"{server.base}/decks/42?view={view}", wait_until="networkidle")
                assert page.locator(".deckview .c .corner.comp").count() == 1, (size, view)
                assert page.locator(".deckview .c .owned").count() >= 2, (size, view)
                assert page.evaluate(TILE_MARKS) == [], (width, size, view)
        assert not errors, errors
        browser.close()


LIST_NAME = """card => { const row = document.querySelector(`.deckview .row[data-card^='${card}']`);
  const r = e => e.getBoundingClientRect(); const n = row.querySelector('.n');
  const name = row.querySelector('.name'), mark = row.querySelector('.compmark');
  return { cell: r(n).width, clipped: name.scrollWidth > name.clientWidth, nameBottom: r(name).bottom,
    row: r(row).height, mark: mark ? r(mark) : null, cellLeft: r(n).left }; }"""


@pytest.mark.parametrize("width", [390, 810, 1366])
def test_the_companion_word_sits_under_the_name_which_keeps_its_room(server: Server, width: int) -> None:
    """0.7.43: the Companion word on a list row sits on its own line under the name, in the name's
    column, so the name keeps the width every other row's name has (beside the name it cut
    "Delver of Se…" at 390 px, where the whole name fits), in the Text and the Table views."""
    from playwright.sync_api import sync_playwright

    delver = "Delver of Secrets // Insectile Aberration"
    server.ark.add_side_row(42, delver, category="Sideboard")
    for row in server.ark.decks[42]["cards"]:
        if row["card"]["oracleCard"]["name"] == delver:
            row["companion"] = True
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, has_touch=width < 600)
        for view in ("text", "table"):
            page.goto(f"{server.base}/decks/42?view={view}", wait_until="networkidle")
            assert _overflow(page) == 0
            m, other = page.evaluate(LIST_NAME, "Delver"), page.evaluate(LIST_NAME, "Cultivate")
            assert m["mark"] and not other["mark"], (view, m, other)
            assert abs(m["cell"] - other["cell"]) <= 1, (view, m, other)  # the name's room, unchanged
            if width == 390:
                assert not m["clipped"], (view, m)
            assert m["mark"]["y"] >= m["nameBottom"] - 1 and abs(m["mark"]["x"] - m["cellLeft"]) <= 1, (
                view,
                m,
            )
            assert m["row"] >= other["row"] + m["mark"]["height"] and other["row"] >= (
                44 if width < 600 else 30
            )
        assert not errors, errors
        browser.close()


def test_box_bodies_stack_their_paragraphs_with_the_standard_gap(server: Server) -> None:
    """UI review 0.7.42 (L1): a category box's body is the box kit's .stack, so its sentences sit
    .75rem apart instead of running together."""
    from playwright.sync_api import sync_playwright

    from .test_deck_categories import _give_categories

    _give_categories(server.ark)
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        page.locator("#categories li[data-name='Ramp'] button.del").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        gaps = page.evaluate(
            "() => { const s = document.querySelector('.mtgbox .body > .stack');"
            " const ps = [...s.querySelectorAll('p')].slice(0, 2);"  # the note, the cards sentence
            " const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);"
            " return ps.slice(1).map((p, i) =>"
            " (p.getBoundingClientRect().top - ps[i].getBoundingClientRect().bottom) / rem); }"
        )
        assert len(gaps) >= 1 and all(abs(g - 0.75) < 0.05 for g in gaps), gaps
        assert not errors, errors
        browser.close()


TOAST_CLOSE = """() => {
  const t = document.querySelector('.deck-toast'), x = t.querySelector('.close'),
    m = t.querySelector('.msg'), tr = t.getBoundingClientRect(), xr = x.getBoundingClientRect(),
    mr = m.getBoundingClientRect(), line = parseFloat(getComputedStyle(m).lineHeight) || 24;
  const acts = [...t.querySelectorAll('.acts button, .acts a')].map(a => a.getBoundingClientRect());
  return { toastRight: tr.right, toastTop: tr.top, close: { top: xr.top, right: xr.right, bottom: xr.bottom },
    msgTop: mr.top, msgRight: mr.right, line, actsRight: Math.max(...acts.map(a => a.right)),
    overlap: acts.some(a => a.right > xr.left && a.left < xr.right && a.bottom > xr.top && a.top < xr.bottom)
      || mr.right > xr.left && mr.bottom > xr.top && mr.top < xr.bottom };
}"""


@pytest.mark.parametrize("width", [344, 390, 810, 1366])
def test_the_toasts_cross_stays_on_its_first_line(server: Server, width: int) -> None:
    """0.7.43: the × of a toast keeps the top right corner at every width (theme.py: out of the
    flow), so "Saved. Snapshot kept under History." with Undo and History wraps the buttons under
    the text and never pushes the × onto a line of its own (it did at 810 px), and nothing sits
    under the ×."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        # a phone and a folded tablet are touch screens (44 px buttons, which is what wrapped the ×)
        browser, page, errors = _page(p, server, sid, width, has_touch=width < 1366)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Cultivate']").first
        card.scroll_into_view_if_needed()
        card.click(button="right")
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitemcheckbox]", has_text="Companion").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        page.wait_for_timeout(300)
        m = page.evaluate(TOAST_CLOSE)
        assert _overflow(page) == 0
        # the × sits on the message's first line, in the corner, with nothing under it
        assert m["close"]["top"] < m["msgTop"] + m["line"], m
        assert m["close"]["right"] <= m["toastRight"] and m["close"]["top"] >= m["toastTop"], m
        assert not m["overlap"], m
        assert not errors, errors
        browser.close()
