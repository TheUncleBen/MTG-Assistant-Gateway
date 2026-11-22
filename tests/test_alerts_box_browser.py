"""The bell's box and the Alerts list in Chromium (0.7.48, the owner's notes on 0.7.47): a row's ⋮
opens its menu on top of the box (it opened hidden behind it), so a row is marked read or
dismissed right there; the box has Mark all read and Clear all, and Undo puts the rows back in
the box without leaving it. The ⋮ is a button on the page as everywhere else; the page's actions
keep their words on one line. Phone, open Fold, laptop; light and dark; the app's own browser."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_alerts_center_browser import _alerts, _exe
from .test_suggest_browser import Server

APP_UA = (
    "Mozilla/5.0 (Linux; Android 15; SM-F966U; wv) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/142.0.0.0 Mobile Safari/537.36 MTGAssistant/0.7.48"
)
NATIVE = (
    "window.MtgNative={layer:function(){},backKey:function(){return true},pullAllowed:function(){},"
    "version:function(){return '0.7.48'},notificationsState:function(){return ''}}"
)
# width, theme, touch, the app
SIZES = [
    (360, "light", True, False),
    (884, "dark", True, True),
    (673, "dark", True, False),
    (1280, "light", False, False),
]
ON_TOP = """
(sel) => { const m = document.querySelector(sel); if (!m) return 'no menu';
  const r = m.getBoundingClientRect();
  const e = document.elementFromPoint(r.left + r.width / 2, r.bottom - 12);
  return e && m.contains(e) ? 'top' : (e ? e.className : 'nothing'); }
"""


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _open(p, server: Server, sid: str, width: int, theme: str, touch: bool, app: bool, exe: str):  # noqa: ANN001, ANN202
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    extra = {"user_agent": APP_UA} if app else {}
    ctx = browser.new_context(
        viewport={"width": width, "height": 860}, has_touch=touch, is_mobile=touch, **extra
    )
    if app:
        ctx.add_init_script(NATIVE)
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": sid, "url": server.base},
            {"name": "mtg_theme", "value": theme, "url": server.base},
        ]
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


def _press(loc, touch: bool) -> None:  # noqa: ANN001
    if touch:
        loc.tap()
    else:
        loc.click()


@pytest.mark.parametrize("width,theme,touch,app", SIZES)
def test_the_bell_box_reads_marks_and_clears_with_undo(
    server: Server, width: int, theme: str, touch: bool, app: bool
) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in()
    a1, a2, a3 = _alerts(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width, theme, touch, app, exe)
        try:
            page.goto(f"{server.base}/decks")
            page.wait_for_load_state("load")
            _press(page.locator("[data-bell]:visible").first, touch)
            box = page.locator(".mtgbox.al-box")
            box.locator(".al-row").first.wait_for()
            # nothing in the box runs off its side (rows were cut at the left)
            over = page.evaluate(
                "(() => { const b = document.querySelector('.mtgbox .body');"
                " return b.scrollWidth - b.clientWidth; })()"
            )
            assert over <= 0, over
            small = page.evaluate(
                "[...document.querySelectorAll('.mtgbox .al-kebab, .mtgbox [data-al-all],"
                " .mtgbox .acts .btn')]"
                ".filter(e => e.offsetParent && Math.min(e.getBoundingClientRect().width,"
                " e.getBoundingClientRect().height) < 43.5).map(e => e.textContent.trim())"
            )
            assert small == [], small
            # a row's ⋮ opens its menu over the box, and Dismiss works there
            _press(box.locator(f".al-row[data-alert='{a2}'] .al-kebab"), touch)
            page.locator(".ctxmenu").wait_for()
            assert page.evaluate(ON_TOP, ".ctxmenu") == "top"
            _press(page.get_by_role("menuitem", name="Dismiss"), touch)
            page.wait_for_selector(f".mtgbox .al-row[data-alert='{a2}']", state="detached")
            assert server.db.gateway_alert("user-1", a2)["dismissed_at"] is not None
            # the toast's Undo is over the box too, and puts the row back in the box
            undo = page.locator(".toast").get_by_role("button", name="Undo")
            undo.wait_for()
            assert page.evaluate(ON_TOP, ".toast") == "top"
            _press(undo, touch)
            page.wait_for_selector(f".mtgbox .al-row[data-alert='{a2}']")
            assert server.db.gateway_alert("user-1", a2)["dismissed_at"] is None
            # Mark all read and Clear all, in the box
            _press(box.locator("[data-al-all='read_all']"), touch)
            page.wait_for_selector(".mtgbox .al-row.unread", state="detached")
            assert all(a["read_at"] is not None for a in server.db.gateway_alerts("user-1"))
            _press(box.locator("[data-al-all='clear_all']"), touch)
            box.get_by_text("You're all caught up.").wait_for()
            assert server.db.gateway_alerts("user-1") == []
            assert box.locator("[data-al-all='clear_all']").is_disabled()
            _press(page.locator(".toast").get_by_role("button", name="Undo"), touch)
            page.wait_for_selector(".mtgbox .al-row[data-alert]")
            assert len(server.db.gateway_alerts("user-1")) == 3
            assert box.is_visible()  # Undo never left the box
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize("width,theme,touch,app", SIZES)
def test_the_pages_menu_is_a_button_that_opens(
    server: Server, width: int, theme: str, touch: bool, app: bool
) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in()
    a1, _a2, _a3 = _alerts(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width, theme, touch, app, exe)
        try:
            page.goto(f"{server.base}/notifications")
            page.wait_for_load_state("load")
            kebab = page.locator(f".al-row[data-alert='{a1}'] .al-kebab")
            kebab.wait_for()
            assert kebab.evaluate("e => e.tagName") == "BUTTON"
            assert kebab.get_attribute("aria-haspopup") == "menu"
            assert page.locator("details.al-menu").count() == 0  # the no-script menu gave way
            _press(kebab, touch)
            page.locator(".ctxmenu").wait_for()
            assert page.evaluate(ON_TOP, ".ctxmenu") == "top"
            _press(page.get_by_role("menuitem", name="Mark read"), touch)
            page.wait_for_selector(f".al-row.unread[data-alert='{a1}']", state="detached")
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize(
    "width,scale", [(884, 1), (884, 2), (1280, 1), (1280, 2), (673, 1), (360, 1), (360, 2)]
)
def test_the_page_actions_keep_their_words_on_one_line(server: Server, width: int, scale: int) -> None:
    from playwright.sync_api import sync_playwright

    from .ui_matrix import TEXT_200

    sid = server.sign_in()
    _alerts(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width, "dark", width < 1000, False, exe)
        try:
            page.goto(f"{server.base}/notifications")
            page.wait_for_load_state("load")
            if scale == 2:
                page.evaluate(TEXT_200)
            lines = page.evaluate(
                "[...document.querySelectorAll('.al-acts button, .al-acts a')].map(b => {"
                " const t = [...b.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim()).pop();"
                " const r = document.createRange(); r.selectNodeContents(t);"
                " const tops = new Set([...r.getClientRects()].filter(x => x.width > 1)"
                ".map(x => Math.round(x.top)));"
                " return [b.textContent.trim(), tops.size]; })"
            )
            assert [t for t, n in lines if n != 1] == [], lines
            over = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert over <= 0, over
            assert errors == []
        finally:
            browser.close()
