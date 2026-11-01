"""Nothing typed is lost (0.7.46 audit QG-01, QG-02, QG-03, QG-04; QOL-03): the New deck box, the
Binder's Import a list and a deck's comment keep what was typed through a reload, and the New
deck form comes back filled in after a sign-in that ended while it was being filled in. A deck
that was created leaves no draft behind. Driven in Chromium at phone and desktop widths."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_deck_menu_browser import Server, _chromium, _page

pytest.importorskip("playwright")

LIST = "1 Sol Ring\n1 Arcane Signet\n1 Cultivate"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _open_paste(page) -> None:
    box = page.locator("#newdeck")
    box.wait_for()
    box.locator(".nd-tabs [data-src=paste]").click()


@pytest.mark.parametrize("width", [412, 1280])
def test_new_deck_box_keeps_its_draft_through_a_reload_and_an_ended_sign_in(
    server: Server, width: int
) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        try:
            page.goto(f"{server.base}/decks/new", wait_until="networkidle")
            _open_paste(page)
            page.locator("#newdeck #name").fill("Kept draft")
            page.locator("#newdeck #source").fill(LIST)
            page.locator("#newdeck input[name=private]").check()
            # a reload: the name, the list, the switch and the Paste tab are back
            page.reload(wait_until="networkidle")
            box = page.locator("#newdeck")
            box.wait_for()
            assert box.locator("#name").input_value() == "Kept draft"
            assert box.locator("#source").input_value() == LIST
            assert box.locator("input[name=private]").is_checked()
            assert box.locator(".nd-tabs [data-src=paste]").get_attribute("aria-selected") == "true"
            assert "3 cards" in box.locator(".nd-pn").inner_text()

            # the sign-in ends while the form is filled in: Create deck goes to the sign-in page
            # with ?draft=1 in the address it comes back to (the POST's body is dropped)
            page.route("**/login?*", lambda r: r.fulfill(status=200, content_type="text/html", body="<p>sign in</p>"))
            page.context.clear_cookies()
            box.locator(".acts button.primary", has_text="Create deck").click()
            page.wait_for_url("**/login?next=/decks/new?draft=1", timeout=8000)
            # signed in again, the sign-in lands on that address: the form is filled in again
            page.context.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page.goto(f"{server.base}/decks/new?draft=1", wait_until="networkidle")
            box = page.locator("#newdeck")
            box.wait_for()
            assert box.locator("#name").input_value() == "Kept draft"
            assert box.locator("#source").input_value() == LIST

            # created: the deck exists, and the next New deck box starts empty
            box.locator(".acts button.primary", has_text="Create deck").click()
            page.wait_for_url("**/decks/*?ok=created", timeout=15000)
            page.goto(f"{server.base}/decks/new", wait_until="networkidle")
            box = page.locator("#newdeck")
            box.wait_for()
            assert box.locator("#name").input_value() == ""
            assert box.locator("#source").input_value() == ""
            assert errors == [], errors
        finally:
            browser.close()


def test_binder_import_and_deck_comment_keep_their_drafts(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        try:
            page.goto(f"{server.base}/collection?add=import", wait_until="networkidle")
            area = page.locator("#importtext")
            area.wait_for()
            area.fill("4 Lightning Bolt\n2 Counterspell")
            page.reload(wait_until="networkidle")
            page.goto(f"{server.base}/collection?add=import", wait_until="networkidle")
            assert page.locator("#importtext").input_value() == "4 Lightning Bolt\n2 Counterspell"

            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            ctext = page.locator("#ctext")
            ctext.scroll_into_view_if_needed()
            ctext.fill("A long comment typed on a phone")
            page.reload(wait_until="networkidle")
            assert page.locator("#ctext").input_value() == "A long comment typed on a phone"
            # another member on this browser never sees it: the draft is keyed by the member
            page.evaluate("() => document.querySelector('header.topbar [data-who]').setAttribute('data-who', 'other')")
            assert page.evaluate(
                "() => Object.keys(localStorage).filter(k => k.startsWith('mtg_draft:')).every(k => k.indexOf(':other:') < 0)"
            )
            assert errors == [], errors
        finally:
            browser.close()
