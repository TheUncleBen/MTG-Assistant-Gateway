"""Account details as a settings list (0.7.46, mockup screen 7) in a browser: the list's rows and
their values, each row opening its own page, the folds that open in place, the boxes behind the
actions in Your data, and the old #part links landing on the right page. Every surface the review
names: a phone, a tablet and a desktop in both themes, the Android app's shell, and 200% text; no
sideways scroll, 4.5:1 text, 44 px targets on touch."""

from __future__ import annotations

import re

import httpx
import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_editor_browser import _link
from .test_notify_app import APP_UA
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server
from .test_text_zoom_browser import TEXT_200

TEXT = (
    ".ygrp h2, .ygrp .lbl, .ygrp .val, .yme b, .yme .sub, .yback, h1, .yfold p, .yfold dt, .yfold dd, "
    ".acc h2, .acc p, .acc label, .acc .btn, .acc button, .acc .ui-row .lab, .acc .ui-row .help, "
    ".mtgbox h2, .mtgbox p, .mtgbox .acts button, .mtgbox label, .notice"
)
TARGETS = (
    ".ygrp a.yrow, .ygrp button.yrow, .ygrp details.yrow > summary, .yback, .acc .btn, "
    ".acc button:not(.inline), .acc .switch, .acc .msel-btn, .yfold .ui-cap, .yfold .btn"
)
PAGES = (
    "/account/details",
    "/account/details/archidekt",
    "/account/details/profile",
    "/account/details/email",
    "/account/details/defaults",
    "/account/details/notifications",
    "/account/details/choices",
)


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path, ntfy_url="https://ntfy.test", ntfy_token="tok")
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _short(page, sel: str) -> list[str]:
    """The targets under 44 px, named."""
    return page.evaluate(
        "(sel) => [...document.querySelectorAll(sel)]"
        ".filter(e => e.getBoundingClientRect().width && getComputedStyle(e).visibility !== 'hidden')"
        ".filter(e => Math.min(e.getBoundingClientRect().height, e.getBoundingClientRect().width) < 43.5)"
        ".map(e => e.textContent.trim().slice(0, 30) + ' ' + Math.round(e.getBoundingClientRect().height))",
        sel,
    )


def _open(p, server: Server, sid: str, width: int, theme: str, exe, *, app: bool = False):
    browser, page, errors = _page(p, server, sid, width, theme, exe)
    if app:
        # the app's shell: the same pages under the Android app's user agent
        ctx = browser.new_context(
            viewport={"width": width, "height": 860},
            has_touch=True,
            user_agent=APP_UA.replace("0.7.30", "0.7.46"),
        )
        ctx.add_cookies(
            [
                {"name": "mtg_session", "value": sid, "url": server.base},
                {"name": "mtg_theme", "value": theme, "url": server.base},
            ]
        )
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


@pytest.mark.parametrize(
    "width,theme,app,zoom",
    [
        (375, "light", False, False),
        (375, "dark", False, False),
        (840, "light", False, False),
        (840, "dark", False, False),
        (1280, "light", False, False),
        (1280, "dark", False, False),
        (375, "light", True, False),
        (375, "dark", True, False),
        (375, "dark", False, True),
        (375, "light", True, True),
    ],
)
def test_the_list_and_every_page_fit_each_surface(
    server: Server, width: int, theme: str, app: bool, zoom: bool
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width, theme, exe, app=app)
        for path in PAGES:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            if zoom:
                page.evaluate(TEXT_200)
                page.wait_for_timeout(300)
            if path == "/account/details":
                # the list: four groups in the mockup's order, rows with values, no form on it
                # (textContent: the style sets the headings in capitals), top to bottom on a phone;
                # on a wide screen Archidekt over This gateway, Assistant over Your data
                heads = page.evaluate(
                    "() => [...document.querySelectorAll('.ygrp')].map(g => [g.querySelector('h2')"
                    ".textContent.trim(), g.getBoundingClientRect().y, g.getBoundingClientRect().x])"
                )
                by_y = [h for h, _y, _x in sorted(heads, key=lambda t: (round(t[1]), t[2]))]
                if width < 700:
                    assert by_y == ["Archidekt", "Assistant", "This gateway", "Your data"]
                else:
                    xs = sorted({round(x) for _h, _y, x in heads})
                    assert len(xs) == 2, heads
                    left = sorted((h for h in heads if round(h[2]) == xs[0]), key=lambda t: t[1])
                    right = sorted((h for h in heads if round(h[2]) == xs[1]), key=lambda t: t[1])
                    assert [h for h, _y, _x in left] == ["Archidekt", "This gateway"]
                    assert [h for h, _y, _x in right] == ["Assistant", "Your data"]
                    assert heads[0][2] + 300 < xs[1], heads  # columns as wide as the Account page's
                # every group as wide as the list (one under the other on a phone)
                widths = page.evaluate(
                    "() => [...document.querySelectorAll('.ygrp')].map(g => g.offsetWidth)"
                )
                assert max(widths) - min(widths) <= 1, widths
                assert page.locator(".ygrp .yrow").count() >= 10
                assert page.locator(".acc .card form").count() == 0
                assert page.locator(".yrow .val").filter(has_text="alice · linked").count() == 1
                # the folds open in place, their bodies drawn
                page.evaluate("() => document.querySelectorAll('details.yrow').forEach(d => d.open = true)")
                page.wait_for_timeout(100)
                assert page.locator("#identity .yfold dt").filter(has_text="Email").count() == 1
                assert page.locator("#writes .yfold .ui-lbl").inner_text().strip() == "On"
            else:
                # a section's page: the way back to the list, one heading, the form drawn
                assert page.locator(".yback").get_attribute("href") == "/account/details"
                assert page.locator("main h1").count() == 1
                assert page.locator(".acc .card > h2").count() <= 1, path  # no repeat of the h1
            assert page.evaluate(MEASURE) == [], path
            assert page.evaluate(CONTRAST, TEXT) == [], path
            if width < 900:
                assert _short(page, TARGETS) == [], path
        browser.close()
    assert errors == []


def test_the_rows_open_their_pages_and_the_boxes_ask_first(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 375, "dark", exe)
        # an old link to a part of the page (a summary email, an older app) lands on that part's
        # page (pages.js); each is a fresh load, as such a link opens
        page.goto(f"{server.base}/account/details#summary", wait_until="networkidle")
        page.wait_for_url("**/account/details/notifications#summary")
        page.goto(f"{server.base}/account/details#archidekt-disclosure", wait_until="networkidle")
        page.wait_for_url("**/account/details/archidekt#archidekt-disclosure")
        page.goto(f"{server.base}/account/details", wait_until="networkidle")
        page.locator(".yrow", has_text="Push notifications").click()
        page.wait_for_url("**/account/details/notifications")
        assert page.locator("main h1").inner_text() == "Push notifications"
        page.locator(".yback").click()
        page.wait_for_url("**/account/details")
        # Sign out on all my devices: the box, then the one red button posts the logout form
        page.locator(".yrow", has_text="Sign out on all my devices").click()
        box = page.locator("#signout")
        box.wait_for(state="visible")
        assert "this one too" in box.inner_text()
        assert page.evaluate(MEASURE) == []
        box.locator("button", has_text="Cancel").click()
        box.wait_for(state="hidden")
        # Delete my data…: red on the list, the box holds the list of what goes and the gated button
        row = page.locator("#delete-data")
        assert "ydanger" in (row.get_attribute("class") or "")
        row.click()
        box = page.locator("#delete")
        box.wait_for(state="visible")
        final = box.locator("button[form='deletef']")
        assert final.is_disabled() and "can't be undone" in box.inner_text()
        box.locator("#delete-tick").check()
        assert final.is_enabled()
        box.locator("button", has_text="Cancel").click()
        box.wait_for(state="hidden")
        # the sign-out form posts to /logout with everywhere=1 (a browser's post, as the box's button)
        h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
        csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/account/details").text).group(1)
        r = h.post("/logout", data={"csrf": csrf, "everywhere": "1"})
        assert r.status_code in (302, 303)
        assert h.get("/account/details").status_code in (302, 303)  # signed out
        browser.close()
    assert errors == []
