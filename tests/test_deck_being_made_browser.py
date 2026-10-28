"""A new deck whose cards are still going in (decks.being_made), on its deck page in headless
Chromium: the notice has the theme's progress ring and says changes wait; Edit deck, Quick add,
Add a package and the rows' edits are off meanwhile; and the page asks now and then whether the
deck is done, reloading itself once it is. Skipped without a Chromium build."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_printing_picker_browser import Server, _chromium, _page

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _making(server: Server) -> None:
    entries = [{"modifications": {"quantity": 1}} for _ in range(125)]
    server.app.state.gateway.decks._being_made["42"] = ({"sent_entries": 50}, entries)


@pytest.mark.parametrize("width", [375, 1280])
def test_the_page_waits_for_the_deck_with_its_edits_off_then_reloads(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _making(server)
    with sync_playwright() as p:
        for theme in ("light", "dark"):
            _making(server)
            browser, page, errors = _page(p, server, sid, width, theme=theme, touch=width == 375)
            try:
                page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
                notice = page.locator("p.notice.making")
                assert notice.is_visible()
                assert notice.get_attribute("role") == "status"
                assert notice.locator(".ui-spin").count() == 1
                assert " ".join(notice.inner_text().split()) == (
                    "This deck is still being made: 50 of 125 cards added so far. "
                    "Changes wait until it is done."
                )
                edit = page.locator("button.btn-primary", has_text="Edit deck")
                assert edit.is_disabled()
                assert page.locator("a[href='/decks/42/edit']").count() == 0
                assert page.locator("#quick").is_disabled()
                assert page.locator("a.addpkg[aria-disabled=true]").count() == 1
                # the rows' menus offer no edits while it is made
                assert page.locator("#cards[data-own]").count() == 0
                assert errors == []
                # once the deck is done the page reloads by itself, with its edits back
                server.app.state.gateway.decks._being_made.pop("42", None)
                page.locator("a.btn-primary[href='/decks/42/edit']").wait_for(timeout=15_000)
                assert page.locator("p.notice.making").count() == 0
                assert errors == []
            finally:
                browser.close()
