"""The Archidekt link disclosure's fold (static/disclosure.js) in a real browser: the full detail
starts folded and the tick box disabled until the member opens it; with scripts off the detail
shows open and the box works; after a failed link it comes back open and unlocked."""

from __future__ import annotations

import html
from pathlib import Path

import pytest

from mtg_gateway import link_disclosure
from mtg_gateway.theme import DEFAULT_CSP

from .test_consent_browser import _chromium_or_skip, _launch

pytest.importorskip("playwright")

STATIC = Path(__file__).resolve().parent.parent / "src" / "mtg_gateway" / "static"
ORIGIN = "https://gw.test"


def _page(read: bool = False) -> str:
    # The same markup pages._account_body renders around the disclosure (the tick box and hint).
    return (
        "<!doctype html><html><body>"
        f"{link_disclosure.form_html(True, read=read)}"
        "<form method='post'><input name='archidekt_login'>"
        "<label class='check'><input type='checkbox' name='accept_risk' value='1' required "
        "aria-describedby='accept-risk-hint'> "
        f"<span>{html.escape(link_disclosure.ACKNOWLEDGE)}</span></label>"
        f"<p id='accept-risk-hint' hidden>{html.escape(link_disclosure.TICK_HINT)}</p>"
        "<button>Link account</button></form></body></html>"
    )


def _open(browser, body: str, *, scripts: bool = True):
    ctx = browser.new_context(java_script_enabled=scripts)
    page = ctx.new_page()

    def handle(route):
        url = route.request.url
        if url.endswith(link_disclosure.SCRIPT):
            route.fulfill(
                body=(STATIC / "disclosure.js").read_text(encoding="utf-8"),
                content_type="text/javascript",
            )
        else:
            # The gateway's own policy: scripts from its origin only, nothing inline.
            headers = {"Content-Security-Policy": DEFAULT_CSP}
            route.fulfill(body=body, content_type="text/html", headers=headers)

    page.route(f"{ORIGIN}/**", handle)
    page.goto(f"{ORIGIN}/account")
    page.wait_for_load_state("load")
    return ctx, page


def test_tick_box_unlocks_only_once_the_detail_is_opened() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            ctx, page = _open(browser, _page())
            detail = page.locator(f"#{link_disclosure.DETAIL_ID}")
            box = page.locator("input[name=accept_risk]")
            # folded, box locked, hint shown; the summary stays visible
            assert detail.evaluate("d => d.open") is False
            assert box.is_disabled()
            assert page.locator("#accept-risk-hint").is_visible()
            assert page.get_by_text(link_disclosure.SUMMARY[1]).is_visible()
            assert not page.get_by_text(link_disclosure.SECTIONS[3][1][0]).is_visible()
            # pressing Link before opening it sends nothing and points at the detail
            posts: list[str] = []
            page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
            page.click("text=Link account")
            page.wait_for_timeout(300)
            assert posts == []
            assert page.evaluate("() => document.activeElement.tagName") == "SUMMARY"
            # open it from the keyboard: focus the summary, press Enter
            page.locator(f"#{link_disclosure.DETAIL_ID} > summary").focus()
            page.keyboard.press("Enter")
            page.wait_for_function("() => !document.querySelector('input[name=accept_risk]').disabled")
            assert page.get_by_text(link_disclosure.SECTIONS[3][1][0]).is_visible()
            assert not page.locator("#accept-risk-hint").is_visible()
            # folding it again does not lock the box again
            page.locator(f"#{link_disclosure.DETAIL_ID} > summary").click()
            assert detail.evaluate("d => d.open") is False
            box.check()
            assert box.is_checked()
            ctx.close()
        finally:
            browser.close()


def test_without_scripts_the_detail_is_open_and_the_box_works() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            ctx, page = _open(browser, _page(), scripts=False)
            assert page.locator(f"#{link_disclosure.DETAIL_ID}").evaluate("d => d.open") is True
            assert page.get_by_text(link_disclosure.SECTIONS[3][1][0]).is_visible()
            box = page.locator("input[name=accept_risk]")
            assert box.is_enabled()
            box.check()
            assert box.is_checked()
            assert not page.locator("#accept-risk-hint").is_visible()
            ctx.close()
        finally:
            browser.close()


def test_after_a_failed_link_the_detail_comes_back_open_and_unlocked() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            ctx, page = _open(browser, _page(read=True))
            assert page.locator(f"#{link_disclosure.DETAIL_ID}").evaluate("d => d.open") is True
            assert page.locator("input[name=accept_risk]").is_enabled()
            ctx.close()
        finally:
            browser.close()
