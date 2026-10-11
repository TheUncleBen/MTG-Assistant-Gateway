"""0.7.23 in headless Chromium: the shared stylesheets load as files under every page's policy
(the page looks as before, no policy complaint), and a tap on a link to another page shows the
navigation bar at once. Skipped without a Chromium build."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_deck_menu_browser import Server, _chromium, _page

pytest.importorskip("playwright")

PAGES = (
    "/",
    "/decks",
    "/decks/42?view=grid",
    "/cards?q=sol",
    "/collection",
    "/scan",
    "/proposals",
    "/account",
)


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_stylesheets_apply_and_a_tap_shows_the_navigation_bar(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390)
        refused: list[str] = []
        page.on("console", lambda m: refused.append(m.text) if "Content Security Policy" in m.text else None)
        for path in PAGES:
            page.goto(server.base + path, wait_until="load")
            # site.css: the dark top bar; deck.css on the deck pages: the card grid
            bar = page.evaluate(
                "() => getComputedStyle(document.querySelector('header.topbar')).backgroundColor"
            )
            assert bar not in ("rgba(0, 0, 0, 0)", "transparent"), path
            if path.startswith("/decks/42"):
                grid = "() => getComputedStyle(document.querySelector('.deckview.grid .cards')).display"
                assert page.evaluate(grid) == "grid"
        assert refused == [] and errors == []

        page.goto(server.base + "/proposals", wait_until="load")
        assert not page.evaluate("() => document.documentElement.classList.contains('navigating')")
        # a tap on a link to another page: the bar shows before that page has answered
        shown = page.evaluate(
            """() => new Promise(done => {
                 document.querySelector("nav.tabbar a[href='/decks']").click();
                 setTimeout(() => done(document.documentElement.classList.contains('navigating')
                   && getComputedStyle(document.querySelector('.navprogress')).opacity === '1'), 0);
               })"""
        )
        assert shown
        page.wait_for_url(server.base + "/decks")
        assert not page.evaluate("() => document.documentElement.classList.contains('navigating')")
        # a link a script takes over, or an anchor on the same page, shows nothing
        stays = page.evaluate(
            """() => new Promise(done => {
                 const a = document.createElement('a'); a.href = '/history'; a.textContent = 'x';
                 a.addEventListener('click', e => e.preventDefault()); document.body.appendChild(a);
                 const b = document.createElement('a'); b.href = '#main'; b.textContent = 'y';
                 document.body.appendChild(b);
                 a.click(); b.click();
                 setTimeout(() => done(document.documentElement.classList.contains('navigating')), 0);
               })"""
        )
        assert stays is False
        browser.close()
