"""Signed out and no connection read the same on every page script, not only on the ones that send
through MtgFetch (0.7.46 re-audit: 12 of 57 scripts did; the deck editor said "Network error" and
offered no Sign in). The deck editor now sends through MtgFetch; every other script's own fetch
goes through fetchjson.js's window.fetch, which puts up one note: "You're signed out, so nothing
was changed." with Sign in back to the same page (path and query), or "No connection; nothing was
changed." for a change that got no answer. Driven in Chromium: the editor's save, the card size
switch (prefs.js) and the Decks list's search (decks.js)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import pytest

from .test_deck_menu_browser import Server, _chromium, _page

pytest.importorskip("playwright")

RAW = re.compile(r"Failed to fetch|Network error|network error|\bAPI\b|TypeError|NetworkError")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _sign_in_href(path_and_query: str) -> str:
    return "/login?next=" + quote(path_and_query, safe="")


def _note(page, cls: str):
    note = page.locator(f".toast.{cls}[role=alert]")
    note.wait_for(timeout=8000)
    return note


@pytest.mark.parametrize("width", [412, 1280])
def test_the_editor_save_signed_out_offers_sign_in_and_offline_says_no_connection(
    server: Server, width: int
) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        try:
            url = f"{server.base}/decks/42/edit?from=deck"
            page.goto(url, wait_until="networkidle")
            page.locator("details.pastebox summary").click()
            page.locator("#pastetext").fill("1 Cultivate")
            page.locator("form.pastelist button").click()
            page.wait_for_function(
                "() => /added|not recognized|could not/"
                ".test(document.querySelector('.pastestatus').textContent)"
            )
            added = page.locator(".pastestatus").inner_text()
            assert added.startswith("1 card added"), added
            # the session ends while the editor is open, then Save is pressed
            page.context.clear_cookies()
            page.locator(".editbar button.review").click()
            status = page.locator(".editbar .status.notice.error")
            status.wait_for(timeout=8000)
            text = status.inner_text()
            assert "You're signed out, so nothing was changed." in text, text
            assert not RAW.search(text), text
            sign_in = status.locator("a", has_text="Sign in")
            assert sign_in.count() == 1, text
            assert sign_in.get_attribute("href") == _sign_in_href("/decks/42/edit?from=deck")
            # said once: the page-wide note stays away from MtgFetch's own requests
            assert page.locator(".session-toast").count() == 0
            assert page.locator(".editbar button.review").is_enabled()  # the edit is kept to save again

            # signed in again, then the network goes
            page.context.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page.context.set_offline(True)
            page.locator(".editbar button.review").click()
            page.locator(".editbar .status", has_text="No connection").wait_for(timeout=8000)
            text = page.locator(".editbar .status").inner_text()
            assert "No connection; nothing was changed." in text, text
            assert not RAW.search(text), text
            assert page.locator(".editbar .status a", has_text="Sign in").count() == 0
            page.context.set_offline(False)
            assert errors == [], errors
        finally:
            browser.close()


@pytest.mark.parametrize("width", [840, 1280])
def test_a_scripts_own_save_signed_out_or_offline_gets_the_one_note(server: Server, width: int) -> None:
    """prefs.js saves the card size with its own fetch and says nothing when it fails: the page's
    note says it now, with Sign in back to this page."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        try:
            page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
            small = page.locator("[data-card-size-set=s]:visible").first
            small.wait_for()
            page.context.clear_cookies()
            small.click()
            note = _note(page, "session-toast")
            text = note.inner_text()
            assert "You're signed out, so nothing was changed." in text, text
            assert not RAW.search(text), text
            sign_in = note.locator("a", has_text="Sign in")
            assert sign_in.count() == 1
            assert sign_in.get_attribute("href") == _sign_in_href("/decks/42?view=grid")
            assert page.locator("[role=alert]").count() == 1  # one note, however many requests
            note.locator("button.close").click()
            assert page.locator(".session-toast").count() == 0

            page.context.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page.reload(wait_until="networkidle")
            page.context.set_offline(True)
            page.locator("[data-card-size-set=l]:visible").first.click()
            note = _note(page, "offline-toast")
            text = note.inner_text()
            assert "No connection; nothing was changed." in text, text
            assert note.locator("a", has_text="Sign in").count() == 0
            page.context.set_offline(False)
            # back online: the note goes by itself
            page.locator(".offline-toast").wait_for(state="detached", timeout=8000)
            assert errors == [], errors
        finally:
            browser.close()


def test_a_scripts_own_read_signed_out_gets_the_note_too(server: Server) -> None:
    """decks.js reads the Decks list with its own fetch: signed out, the search can't read it and
    the page says why, with Sign in back to the same address."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        try:
            page.goto(f"{server.base}/decks?sort=name", wait_until="networkidle")
            box = page.locator("#q")
            box.wait_for()
            page.context.clear_cookies()
            box.fill("zzz")
            box.press("Enter")
            note = _note(page, "session-toast")
            text = note.inner_text()
            assert "You're signed out" in text and not RAW.search(text), text
            href = note.locator("a", has_text="Sign in").get_attribute("href")
            assert href is not None and href.startswith("/login?next=%2Fdecks"), href
            assert errors == [], errors
        finally:
            browser.close()
