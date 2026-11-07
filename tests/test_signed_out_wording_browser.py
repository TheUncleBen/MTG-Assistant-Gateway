"""One wording for a change that could not be sent, on every page script (0.7.46 audit QG-06,
QG-07, QG-08, AUDIT-REPORT row 12): signed out says "You're signed out, so nothing was changed."
with a Sign in button that comes back to the same page; no connection says "No connection;
nothing was changed." Never the browser's own "Failed to fetch", never "API", never "That did
not work" twice. Driven in Chromium: the deck page's quick add and the Binder's stepper, with
the session cookie cleared and with the network off."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from .test_deck_menu_browser import Server, _chromium, _page

pytest.importorskip("playwright")

RAW = re.compile(r"Failed to fetch|\bAPI\b|TypeError|NetworkError", re.I)


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _binder(server: Server, sid: str) -> None:
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/collection").text).group(1)
    r = h.post(
        "/collection/api/add",
        headers={"X-CSRF-Token": csrf},
        json={"items": [{"name": "Sol Ring", "quantity": 3}], "source": "manual"},
    )
    assert r.status_code == 200, r.text


def _alerts(page) -> list[str]:
    return [t.strip() for t in page.locator("[role=alert]").all_inner_texts() if t.strip()]


@pytest.mark.parametrize("width", [412, 1280])
def test_deck_quick_add_signed_out_offers_sign_in_and_offline_says_no_connection(
    server: Server, width: int
) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        try:
            url = f"{server.base}/decks/42?view=text"
            page.goto(url)
            page.locator("#quick").wait_for()
            # the session ends while the page is open (cleared cookie), then a card is added
            page.context.clear_cookies()
            page.locator("#quick").fill("Arcane Signet")
            page.locator("#quick").press("Enter")
            toast = page.locator(".deck-toast.error")
            toast.wait_for(timeout=8000)
            text = toast.inner_text()
            assert "signed out" in text, text
            assert not RAW.search(text), text
            sign_in = toast.locator("a", has_text="Sign in")
            assert sign_in.count() == 1, text
            # Sign in comes back to this very page, its query too
            assert sign_in.get_attribute("href") == "/login?next=" + "%2Fdecks%2F42%3Fview%3Dtext"
            assert toast.locator("button", has_text="Try again").count() == 0  # cannot help here
            assert server.quantity("Arcane Signet") == 0

            # signed in again, but the network is gone
            page.context.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page.goto(url)
            page.locator("#quick").wait_for()
            page.context.set_offline(True)
            page.locator("#quick").fill("Arcane Signet")
            page.locator("#quick").press("Enter")
            toast = page.locator(".deck-toast.error")
            toast.wait_for(timeout=8000)
            text = toast.inner_text()
            assert "no connection" in text.lower() and "nothing was changed" in text, text
            assert not RAW.search(text), text
            assert toast.locator("button", has_text="Try again").count() == 1
            assert toast.locator("a", has_text="Sign in").count() == 0
            page.context.set_offline(False)
            assert errors == [], errors
        finally:
            browser.close()


@pytest.mark.parametrize("width", [412, 1280])
def test_binder_stepper_offline_and_signed_out_say_it_once(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _binder(server, sid)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        try:
            page.goto(f"{server.base}/collection?view=list")
            inc = page.locator("li[data-id] form.qty button[value=inc]").first
            inc.wait_for()
            page.context.set_offline(True)
            inc.click()
            strip = page.locator("#col-notes .notice.error")
            strip.wait_for(timeout=8000)
            text = strip.inner_text()
            assert "No connection" in text and "was not changed" in text, text
            assert "That did not work" not in text and not RAW.search(text), text
            # said once: one alert on the page, the live region does not repeat it
            alerts = _alerts(page)
            assert len(alerts) == 1, alerts
            assert page.locator("#live").inner_text().strip() == ""
            page.context.set_offline(False)
            # a second failure replaces the strip, it does not stack a second one
            page.context.clear_cookies()
            inc.click()
            page.locator("#col-notes .notice.error", has_text="signed out").wait_for(timeout=8000)
            strips = page.locator("#col-notes .notice.error")
            assert strips.count() == 1
            text = strips.first.inner_text()
            assert "You're signed out, so Sol Ring was not changed." in text, text
            sign_in = strips.first.locator("a", has_text="Sign in")
            assert sign_in.count() == 1
            assert sign_in.get_attribute("href") == "/login?next=%2Fcollection%3Fview%3Dlist"
            assert errors == [], errors
        finally:
            browser.close()
