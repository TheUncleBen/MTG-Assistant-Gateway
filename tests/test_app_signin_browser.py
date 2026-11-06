"""The Android app sign-in's last page in headless Chromium (app_signin.return_body,
static/app-signin.js): the whole page is drawn, small print and footer included, before anything
leaves it; the top bar has no Sign in under the page's "Signed in"; and the page opens the app by
itself only when the gateway marked the code as made after a sign-in during the leg. Skipped
without a Chromium build."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from mtg_gateway.app_signin import RETURN_PATH, challenge_of

from .test_consent_browser import _chromium_or_skip, _launch
from .test_guide_browser import Server
from .waits import poll

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
    page.wait_for_load_state("domcontentloaded")  # the page's script has run
    if fresh:
        poll(page, lambda: len(tried) >= 1)  # its try of the app's link
    else:
        # fixed wait: a negative check: an unmarked page tries no link within 400 ms
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
            page.wait_for_load_state("domcontentloaded")  # the page's script has run
            # fixed wait: a negative check: the reload tries no link within 300 ms
            page.wait_for_timeout(300)
            assert len(tried) == (1 if fresh else 0), tried
        finally:
            browser.close()


# -- 0.7.46 security review A-04: the page before the identity provider on the app's leg ---------
APP_UA = "Mozilla/5.0 (Linux; Android 16) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.7.46"

CONTRAST = """() => {
  function rgb(s) { return (s.match(/[\\d.]+/g) || []).map(Number); }
  function lum(c) {
    var a = c.slice(0, 3).map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
  }
  function bg(e) {
    for (; e; e = e.parentElement) {
      var c = rgb(getComputedStyle(e).backgroundColor);
      if (c.length < 4 || c[3] > 0) return c;
    }
    return [255, 255, 255];
  }
  var worst = 99, what = "";
  document.querySelectorAll(".sio-p h1, .sio-p p, .sio-p b, .sio-p button").forEach(function (e) {
    if (!e.offsetParent || !e.textContent.trim()) return;
    var a = lum(rgb(getComputedStyle(e).color)), b = lum(bg(e));
    var r = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (r < worst) { worst = r; what = e.tagName + ":" + e.textContent.trim().slice(0, 30); }
  });
  return [worst, what];
}"""

# every element's font size doubled, as a phone's font scale or a text-only zoom does it
TEXT_200 = """() => {
  const els = [document.documentElement, ...document.querySelectorAll('body, body *')];
  const sizes = els.map(el => parseFloat(getComputedStyle(el).fontSize));
  els.forEach((el, i) => {
    if (sizes[i]) el.style.setProperty('font-size', (sizes[i] * 2) + 'px', 'important');
  });
}"""


@pytest.fixture
def idp_server(tmp_path: Path):
    from .test_consent_browser import Server as ConsentServer

    s = ConsentServer(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _leg_url(base: str) -> str:
    return f"{base}/login?next=/decks&app_challenge={challenge_of('w' * 43)}&app_return=scheme"


def _buttons_fit(page, touch: bool) -> None:
    # 44 px for a finger; with a mouse on desktop the site's own button size (owner's call)
    least = 44 if touch else 30
    for name in ("Continue", "Cancel"):
        box = page.get_by_role("button", name=name).bounding_box()
        assert box is not None and box["height"] >= least and box["width"] >= least, (name, box)
    sideways = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    assert sideways <= 0, sideways
    # nothing in the panel sticks out of it
    out = page.evaluate(
        """() => { const p = document.querySelector('.sio-p').getBoundingClientRect();
        return [...document.querySelectorAll('.sio-p *')].filter(e => {
          const r = e.getBoundingClientRect();
          return r.width && (r.right > p.right + 1 || r.left < p.left - 1);
        }).map(e => e.tagName + ':' + e.textContent.trim().slice(0, 20)); }"""
    )
    assert not out, out


@pytest.mark.parametrize("width", [360, 412, 717, 840, 1280])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_page_before_the_provider_reads_and_fits_everywhere(idp_server, width: int, scheme: str) -> None:
    """Phone, Fold and desktop widths, light and dark, normal and 200% text, in a plain browser and
    with the app's user agent: the page names the gateway, its two buttons are 44 px or more, the
    text reads at 4.5:1 or better, and nothing scrolls sideways or leaves the panel."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            errors: list[str] = []
            for ua in (None, APP_UA):
                ctx = browser.new_context(
                    viewport={"width": width, "height": 800},
                    color_scheme=scheme,
                    has_touch=width < 1000,
                    **({"user_agent": ua} if ua else {}),
                )
                page = ctx.new_page()
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.goto(_leg_url(idp_server.base))
                assert page.locator("h1").inner_text() == "Sign in to the app?"
                assert page.get_by_text("An app asked to sign in to").is_visible()
                assert page.locator("header .btn.signin").count() == 0
                _buttons_fit(page, width < 1000)
                worst, what = page.evaluate(CONTRAST)
                assert worst >= 4.5, (worst, what)
                page.evaluate(TEXT_200)
                _buttons_fit(page, width < 1000)
                assert not errors, errors
                ctx.close()
        finally:
            browser.close()


def test_continue_goes_to_the_provider_and_cancel_ends_the_leg(idp_server) -> None:
    """A real press: Continue's post is answered with the identity provider's address, which the
    page's form-action allows (Chromium applies it to that redirect); Cancel stays on the gateway
    with the canceled page. Opening the address alone never leaves the gateway."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 412, "height": 800})
            console: list[str] = []
            page.on("console", lambda m: console.append(m.text))
            left: list[str] = []
            page.on("request", lambda r: left.append(r.url) if r.url.startswith(idp_server.outside) else None)
            page.goto(_leg_url(idp_server.base), wait_until="networkidle")
            assert not left, left
            page.get_by_role("button", name="Continue").click()
            page.wait_for_url(f"{idp_server.outside}/application/o/authorize/**", timeout=10_000)
            q = parse_qs(urlparse(page.url).query)
            assert q.get("prompt") == ["login"] and q.get("max_age") == ["0"]  # the scheme leg's rule
            assert not [m for m in console if "form-action" in m or "Refused" in m], console

            page.goto(_leg_url(idp_server.base))
            page.get_by_role("button", name="Cancel").click()
            page.locator("h1", has_text="Sign-in canceled").wait_for(timeout=10_000)
            assert page.url.startswith(idp_server.base) and not left[1:], left
        finally:
            browser.close()


def test_the_return_page_keeps_no_code_in_storage_and_tries_once_per_code(server: Server) -> None:
    """Security review A-05: the page remembered its one try under a key made of its whole link, so
    the one-time code sat in the browser's storage. Now one fixed key holds a hash of the code:
    a reload of the same page never tries again, a new code in the same tab tries once."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 800})
            tried: list[str] = []
            page.on("request", lambda r: tried.append(r.url) if r.url.startswith("intent:") else None)
            codes = []
            for n in (1, 2):
                code = server.app.state.gateway.app_signins.issue(
                    "user-1", challenge_of("v" * 43), "/decks", fresh=True
                )
                codes.append(code)
                for _ in range(2):  # the page, then a reload of it
                    page.goto(f"{server.base}{RETURN_PATH}?code={code}", wait_until="commit")
                    page.locator("footer.site").wait_for(state="attached", timeout=10_000)
                    page.wait_for_function(
                        "() => { try { return sessionStorage.getItem('mtg-app-return') !== null; }"
                        " catch (e) { return false; } }",
                        timeout=10_000,
                    )
                stored = page.evaluate(
                    "() => Object.keys(sessionStorage).map(k => k + '=' + sessionStorage.getItem(k))"
                )
                assert len(stored) == 1 and stored[0].startswith("mtg-app-return="), stored
                assert not [c for c in codes if any(c in s for s in stored)], stored
                assert len(tried) == n, tried  # once for this code, reload or not
        finally:
            browser.close()
