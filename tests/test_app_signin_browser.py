"""The Android app sign-in's last page in headless Chromium (app_signin.return_body,
static/app-signin.js): the whole page is drawn, small print and footer included, before anything
leaves it; the top bar has no Sign in under the page's "Signed in"; and the page opens the app by
itself only when the gateway marked the code as made after a sign-in during the leg. Skipped
without a Chromium build."""

from __future__ import annotations

from pathlib import Path

import pytest

from mtg_gateway.app_signin import RETURN_PATH, challenge_of

from .test_consent_browser import _chromium_or_skip, _launch
from .test_guide_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _open(p, exe, server: Server, *, fresh: bool, width: int = 390):
    code = server.app.state.gateway.app_signins.issue("user-1", challenge_of("v" * 43), "/decks", fresh=fresh)
    browser = _launch(p, exe)
    page = browser.new_page(viewport={"width": width, "height": 800})
    tried: list[str] = []
    page.on("request", lambda r: tried.append(r.url) if r.url.startswith("intent:") else None)
    # (headless Chromium has no app for the intent: link, so a page that follows it never ends
    # loading: the parsed page is what is looked at)
    page.goto(f"{server.base}{RETURN_PATH}?code={code}", wait_until="commit")
    page.locator("footer.site").wait_for(state="attached", timeout=10_000)
    page.wait_for_timeout(400)
    return browser, page, tried


@pytest.mark.parametrize("fresh", [False, True], ids=["passed through", "signed in during the leg"])
def test_the_return_page_is_drawn_whole_and_opens_the_app_only_when_marked(
    server: Server, fresh: bool
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser, page, tried = _open(p, exe, server, fresh=fresh)
        try:
            assert page.locator("h1").inner_text() == "Signed in"
            assert page.get_by_text("The link works once, for two minutes.").is_visible()
            assert page.locator("footer.site").count() == 1
            # no Sign in in the top bar of a page that says the person is signed in
            assert page.locator("header .btn.signin").count() == 0
            assert page.locator("#app-return").is_visible()
            assert len(tried) == (1 if fresh else 0), tried
            # the button's own link (the browser reports the try without its #Intent part)
            href = page.locator("#app-return").get_attribute("href") or ""
            assert href.endswith("package=local.mtgassistantgateway.app;end")
            assert all(href.startswith(u.split("#")[0]) for u in tried), (tried, href)
            # a reload never tries again
            page.reload(wait_until="commit")
            page.locator("footer.site").wait_for(state="attached", timeout=10_000)
            page.wait_for_timeout(300)
            assert len(tried) == (1 if fresh else 0), tried
        finally:
            browser.close()
