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
        for want in (
            "Open in a new tab",
            "Edit deck",
            "Goldfish",
            "Run full test",
            "Clone…",
            "Rename…",
            "Move to folder…",
            "Export…",
            "Compare with…",
            "Deck settings",
            "Deck stats",
            "History and snapshots",
            "Proposals for this deck",
            "Copy link",
            "Open on Archidekt",
            "Delete deck…",
        ):
            assert any(x == want for x in labels), (want, labels)
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
        panel.locator("button[name=action]").click()
        page.wait_for_url(lambda u: "ok=categories" in u, timeout=5000)
        page.wait_for_load_state("networkidle")
        assert page.evaluate(OPEN_BOXES) == 0
        assert "Categories saved to Archidekt." in page.locator("#categories .notice").inner_text()
        assert page.locator("#categories li[data-name='Mana']").count() == 1
        assert [b["name"] for b in server.ark.category_patches] == ["Mana"]
        # touching the commander asks first, in the box, with the backend's sentence
        cmd = page.locator("#categories li[data-name='Commander']")
        cmd.locator("label.ds-flag", has_text="Premier").click()
        panel.locator("button[name=action]").click()
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
        panel.locator("button[name=action]").click()
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
        panel.locator("button[name=action]").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        assert box.locator("h2").inner_text() == "Change which cards count in the deck?"
        assert "don't count in the deck" in box.inner_text()
        box.locator("button", has_text="Cancel").click()
        box.wait_for(state="hidden", timeout=3000)
        assert len(server.ark.category_patches) == sent
        assert not errors, errors
        browser.close()
