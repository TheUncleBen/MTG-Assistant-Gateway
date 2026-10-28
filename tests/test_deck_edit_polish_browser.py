"""The deck page, editor and My decks after the 0.7.46 audit, in headless Chromium: the deck
menu of a deck still being made offers no edits; the editor's row menu names the category a
side row leaves; a folder tile's ⋮ never sits on its name; the Packages tab shows one empty
state, under its own heading. Skipped without Chromium."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_deck_being_made_browser import _making
from .test_deck_menu_browser import Server, _chromium, _file_under, _page

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _items(page) -> list[str]:
    menu = page.locator(".ctxmenu[role=menu]")
    menu.wait_for(timeout=3000)
    return [" ".join(t.split()) for t in menu.locator("[role=menuitem]").all_inner_texts()]


@pytest.mark.parametrize("width", [390, 1280])
def test_the_deck_menu_offers_no_edits_while_the_deck_is_made(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _making(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        page.locator(".banner button.deckmenu").click()
        items = _items(page)
        assert "Deck stats" in items and "Share…" in items and "Export…" in items
        for edit in ("Rename…", "Move to folder…", "Add a package…", "Deck settings", "Delete deck…"):
            assert edit not in items, items
        page.keyboard.press("Escape")
        # once the deck is done the page reloads by itself, and the menu has them back
        server.app.state.gateway.decks._being_made.pop("42", None)
        page.locator("a.btn-primary[href='/decks/42/edit']").wait_for(timeout=15_000)
        page.locator(".banner button.deckmenu").click()
        items = _items(page)
        assert "Rename…" in items and "Delete deck…" in items and "Deck settings" in items
        assert errors == []
        browser.close()


def test_the_editors_row_menu_names_the_category_a_side_row_leaves(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _file_under(server, "Rampant Growth", ["Sideboard"])
    side = {"name": "Sideboard", "includedInDeck": False, "isPremier": False}
    server.ark.decks[42]["categories"].append(side)
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        side = page.locator("li.erow.side", has_text="Rampant Growth").first
        assert side.locator("button.remove").get_attribute("title") == "Remove from “Sideboard”"
        side.click(button="right")
        items = _items(page)
        assert "Remove from “Sideboard”" in items and "Remove from deck" not in items, items
        page.keyboard.press("Escape")
        main = page.locator("li.erow:not(.side)", has_text="Cultivate").first
        main.click(button="right")
        assert "Remove from deck" in _items(page)
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_a_folder_tiles_menu_button_never_sits_on_its_name(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    long_name = "Commander decks in progress and testing builds for Friday Night Magic"
    server.ark.folders["alice"] = [{"id": 501, "name": long_name, "private": False}]
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        tile = page.locator("ul.folderlist li.folder").first
        name = tile.locator(".name").bounding_box()
        kebab = tile.locator("button.kebab").bounding_box()
        assert name["x"] + name["width"] <= kebab["x"], (name, kebab)
        assert errors == []
        browser.close()


def test_the_packages_tab_shows_one_empty_state_under_its_heading(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    server.ark.folders["alice"] = [{"id": 501, "name": "Packages here", "private": False}]
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390)
        page.goto(f"{server.base}/decks?show=packages", wait_until="networkidle")
        empty = page.locator("#pk-empty")
        empty.wait_for(timeout=5000)
        assert page.locator("p.empty:visible").count() == 1
        assert page.locator("a.btn-primary", has_text="New package").count() == 1
        assert page.locator("a.btn-primary", has_text="New deck").count() == 0
        # under the Packages heading, after the folders, not above them
        head = page.locator("#fv-decks").bounding_box()
        folders = page.locator("#fv-folders").bounding_box()
        at = empty.bounding_box()
        assert folders["y"] < head["y"] < at["y"], (folders, head, at)
        page.goto(f"{server.base}/decks?show=packages&folder=501", wait_until="networkidle")
        page.locator("#pk-empty").wait_for(timeout=5000)
        shown = page.locator("p.empty:visible")
        assert shown.count() == 1 and "No packages here yet" in shown.inner_text()
        assert errors == []
        browser.close()
