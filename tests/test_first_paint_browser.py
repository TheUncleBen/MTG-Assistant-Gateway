"""A page never fades in from the browser's own look. A browser can work out an element's style
before the site's stylesheet has arrived (a script that measures, an extension, a busy machine);
the buttons then have the browser's grey and the body its own canvas, and when the stylesheet
lands the site's colour transitions (button, field and body background fades) ran from those
colours: in dark mode Cancel on the app's sign-in page faded from grey (4.15:1 behind its text).
The page starts marked ``preload`` (no transitions) and static/topbar.js lifts the mark once the
stylesheet is in, so hovers still fade. Skipped without a Chromium build."""

from __future__ import annotations

import time

import pytest

from .test_app_signin_browser import _leg_url, idp_server  # noqa: F401
from .test_consent_browser import _chromium_or_skip, _launch
from .waits import poll

pytest.importorskip("playwright")

# works out each button's style the moment the parser adds it, before the stylesheet is in
EARLY_STYLE = """new MutationObserver(function (list) {
  list.forEach(function (m) { m.addedNodes.forEach(function (n) {
    if (n.nodeType === 1 && (n.matches('button, .btn, body') || n.querySelector('button, .btn')))
      document.querySelectorAll('body, button, .btn').forEach(function (e) {
        getComputedStyle(e).backgroundColor; });
  }); });
}).observe(document, {childList: true, subtree: true});"""

RUNNING = """() => document.getAnimations().filter(a => a.transitionProperty)
  .map(a => a.effect.target.tagName + '.' + a.effect.target.className + ' ' + a.transitionProperty)"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_nothing_fades_in_from_the_browsers_own_colours(idp_server, scheme: str) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            ctx = browser.new_context(viewport={"width": 412, "height": 800}, color_scheme=scheme)
            ctx.add_init_script(EARLY_STYLE)
            page = ctx.new_page()

            def late(route, _request=None) -> None:
                time.sleep(0.3)  # the stylesheet arrives after the page's first style
                route.continue_()

            page.route("**/static/site.css*", late)
            page.goto(_leg_url(idp_server.base))
            assert page.evaluate(RUNNING) == []
            cancel = page.get_by_role("button", name="Cancel")
            assert cancel.evaluate("e => getComputedStyle(e).backgroundColor") == "rgba(0, 0, 0, 0)"
            # once the page is in, a hover still fades
            poll(page, lambda: page.evaluate("() => !document.documentElement.classList.contains('preload')"))
            page.get_by_role("button", name="Continue").hover()
            running = page.evaluate(RUNNING)
            assert any(a.startswith("BUTTON.primary ") for a in running), running
            ctx.close()
        finally:
            browser.close()
