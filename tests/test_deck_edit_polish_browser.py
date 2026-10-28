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


# -- the deck page's saves when Archidekt is slow or fails (0.7.46 audit D-04, D-05, D-06, D-07) --
from mtg_gateway import decks as decks_mod  # noqa: E402

from .test_deck_views_browser import _error, _held, _menu_page, _open_menu  # noqa: E402


def test_a_timed_out_save_shows_what_the_deck_has_now(server: Server) -> None:
    """D-04: Archidekt took the write but never answered. The page never says "not saved" and
    never puts the old count back: it draws the count Archidekt has, and says so."""
    from playwright.sync_api import sync_playwright

    server.ark.timeout_card_writes = True
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _menu_page(p, server, 390, 844, "dark")
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        n = server.quantity("Cultivate")
        menu = _open_menu(page, row)
        menu.locator(".qtyrow .step").nth(1).click()
        note = page.locator(".deck-toast.warn .msg")
        note.wait_for(timeout=15_000)
        assert note.inner_text() == "Archidekt didn't answer; this is what the deck shows now."
        assert page.locator(".deck-toast.error").count() == 0
        assert row.get_attribute("data-qty") == str(n + 1)
        assert menu.locator(".qtyrow .n").inner_text() == str(n + 1)
        assert server.quantity("Cultivate") == n + 1
        assert errors == []
        browser.close()


def test_try_again_sends_a_refused_change_once_more(server: Server) -> None:
    """D-05: the red note offers Try again, which sends the same change again; saved, the row
    shows it with Undo. Refused again, the note says so once more, with Try again."""
    from playwright.sync_api import sync_playwright

    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _menu_page(p, server, 1280, 800, "light")
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        n = server.quantity("Cultivate")
        was = f"Cultivate is back to {n} {'copy' if n == 1 else 'copies'}."
        server.ark.fail_card_writes = True
        menu = _open_menu(page, row)
        menu.locator(".qtyrow .step").nth(1).click()
        text = _error(page)
        assert text.startswith("Quantity not saved:") and text.endswith(was), text
        assert "503" not in text and "HTTP" not in text
        again = page.locator(".deck-toast.error button", has_text="Try again")
        assert again.count() == 1 and row.get_attribute("data-qty") == str(n)
        server.ark.fail_card_writes = False
        again.click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert page.locator(".deck-toast button", has_text="Undo").count() == 1
        assert row.get_attribute("data-qty") == str(n + 1) and server.quantity("Cultivate") == n + 1
        # refused twice: the second note has nothing to put back, and Try again once more
        server.ark.fail_card_writes = True
        page.keyboard.press("Escape")
        menu = _open_menu(page, row)
        menu.locator(".qtyrow .step").nth(1).click()
        assert _error(page).endswith(f"Cultivate is back to {n + 1} copies.")
        page.locator(".deck-toast.error button", has_text="Try again").click()
        text = _error(page)
        assert text.startswith("Quantity not saved:") and "is back" not in text, text
        assert page.locator(".deck-toast.error button", has_text="Try again").count() == 1
        assert row.get_attribute("data-qty") == str(n + 1) and server.quantity("Cultivate") == n + 1
        assert errors == []
        browser.close()


def test_a_slow_save_ends_on_the_deck_page_with_undo(server: Server, monkeypatch) -> None:
    """D-07: Archidekt takes longer than the gateway waits. The page keeps its Saving note (no
    "Review page" link), and once the save ends the row is drawn, Saved with Undo."""
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr(decks_mod, "APPLY_WAIT_SECONDS", 0.5)
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _menu_page(p, server, 390, 844, "dark")
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        n = server.quantity("Cultivate")
        held = _held(server)
        try:
            menu = _open_menu(page, row)
            menu.locator(".qtyrow .step").nth(1).click()
            saying = page.locator(".deck-toast .msg", has_text=f"Saving Cultivate: {n + 1} copies…")
            saying.wait_for(timeout=5000)
            page.wait_for_timeout(2500)
            assert page.locator(".deck-toast a", has_text="Review page").count() == 0
            assert "Saving Cultivate" in page.locator(".deck-toast .msg").inner_text()
            assert row.get_attribute("data-qty") == str(n + 1)
        finally:
            held.set()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=10_000)
        assert row.get_attribute("data-qty") == str(n + 1) and server.quantity("Cultivate") == n + 1
        page.locator(".deck-toast button", has_text="Undo").click()
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=10_000)
        assert row.get_attribute("data-qty") == str(n) and server.quantity("Cultivate") == n
        assert errors == []
        browser.close()


def test_a_reload_during_a_slow_save_shows_the_strip_then_the_result(server: Server, monkeypatch) -> None:
    """D-06: a reload while a save still runs shows "Saving 1 change…"; once it ends the page
    loads again by itself and the row shows the saved count."""
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr(decks_mod, "APPLY_WAIT_SECONDS", 0.5)
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _menu_page(p, server, 390, 844, "dark")
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        n = server.quantity("Cultivate")
        held = _held(server)
        try:
            menu = _open_menu(page, row)
            menu.locator(".qtyrow .step").nth(1).click()
            page.locator(".deck-toast .msg", has_text="Saving Cultivate").wait_for(timeout=5000)
            page.wait_for_timeout(1500)
            page.reload(wait_until="networkidle")
            strip = page.locator("p.notice.saving[data-busy]")
            assert strip.is_visible() and "Saving 1 change…" in strip.inner_text()
            assert strip.get_attribute("role") == "status"
            fresh = page.locator(".deckview .row[data-card='Cultivate']").first
            assert fresh.get_attribute("data-qty") == str(n)
        finally:
            held.set()
        page.wait_for_function(
            "() => !document.querySelector('p.notice.saving') && "
            f"document.querySelector(\".deckview .row[data-card='Cultivate']\").dataset.qty === '{n + 1}'",
            timeout=15_000,
        )
        assert server.quantity("Cultivate") == n + 1
        assert errors == []
        browser.close()
