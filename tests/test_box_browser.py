"""The shared box (dialog) in headless Chromium (static/box.js, theme.BOX_CSS, ui.box): a box the
page holds opens from a button that is a real link to the old address, with a calm transition
(none under reduced motion); the header has the title and a 44 px ×; Escape, the scrim, the × and
Cancel close it and focus goes back to the opener; Tab stays inside; long content scrolls inside;
on a phone it is a sheet from the bottom; the old address draws the page with the box open and
closing it puts the page's address back; without scripts the link goes to the old address, where
the box is a plain panel. The menus' own boxes (Rename) wear the same header. Light and dark, at
200% text, with no sideways scroll. Skipped without a Chromium build."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.routing import Route

from mtg_gateway import theme, ui

from .test_card_search_filters_browser import CONTRAST
from .test_overflow_sweep_browser import MEASURE
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

SCROLLS = (
    "() => { const b = document.querySelector('.mtgbox .body'); return b.scrollHeight > b.clientHeight; }"
)
# the sheet sits on the bottom edge of the window, as wide as it
SHEET = (
    "() => { const r = document.querySelector('.mtgbox-scrim .mtgbox').getBoundingClientRect();"
    " return Math.abs(r.bottom - innerHeight) < 1 && r.left === 0 && r.width === innerWidth; }"
)
BORDERS = (
    "s => { const c = getComputedStyle(document.querySelector(s)); return [c.borderTopWidth,"
    " c.borderRightWidth, c.borderBottomWidth, c.borderLeftWidth, c.borderTopLeftRadius,"
    " c.borderTopRightRadius]; }"
)
STYLE = "([s, p]) => getComputedStyle(document.querySelector(s))[p]"
LONG = "".join(f"<p>Line {i} of a long body that must scroll inside the box.</p>" for i in range(60))


def _page_html(opened: bool) -> str:
    body_form = (
        "<form id='exf' method='get' action='/box-kit/sent'>"
        "<div class='field'><label for='nm'>Name</label><input id='nm' name='name' value='Meren'></div>"
        f"{LONG}</form>"
    )
    return (
        ui.page_head(
            "Box kit",
            "The shared box, on a small page.",
            actions_html=ui.box_opener("Export…", "/box-kit/open", "export", icon_name="download"),
        )
        + ui.notice("Deck renamed on Archidekt.", "ok", role="status")
        + ui.notice("Archidekt is not answering right now.", "error")
        + "<p><a id='after' href='/box-kit'>A link after the opener</a></p>"
        + ui.box(
            "export",
            "Export “Meren”",
            body_form,
            actions=[ui.box_button("Cancel", cancel=True), ui.box_button("Save", kind="primary", form="exf")],
            open=opened,
            close_href="/box-kit",
        )
    )


class BoxServer(Server):
    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path)

        async def kit(request: Request):
            opened = request.url.path.endswith("/open")
            return theme.render(
                "Box kit", _page_html(opened), site="MTG Gateway", signed_in=True, csrf="x", heading=False
            )

        async def sent(request: Request):
            name = request.query_params.get("name", "")
            return theme.render(
                "Sent", f"<p id='sent'>Saved {name}</p>", site="MTG Gateway", signed_in=True, csrf="x"
            )

        self.app.router.routes.insert(0, Route("/box-kit", kit))
        self.app.router.routes.insert(0, Route("/box-kit/open", kit))
        self.app.router.routes.insert(0, Route("/box-kit/sent", sent))


@pytest.fixture
def kit(tmp_path: Path):
    s = BoxServer(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _exe() -> str | None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _ctx(p, server: Server, sid: str, width: int, *, theme_name: str = "dark", exe=None, **kw):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": width, "height": 800}, **kw)
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": sid, "url": server.base},
            {"name": "mtg_theme", "value": theme_name, "url": server.base},
        ]
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


SETTLED = (
    "() => { const b = document.querySelector('.mtgbox-scrim .mtgbox');"
    " return b && getComputedStyle(b).transform === 'none'; }"
)


def _box(page):
    """The open box, once its opening transition has run to its end state."""
    box = page.locator(".mtgbox-scrim .mtgbox[role=dialog]")
    box.wait_for(timeout=3000)
    page.wait_for_function(SETTLED, timeout=3000)
    return box


def _gone(page) -> None:
    page.locator(".mtgbox-scrim").first.wait_for(state="detached", timeout=3000)


@pytest.mark.parametrize("theme_name", ["dark", "light"])
def test_the_box_opens_from_its_button_and_closes_with_escape_focus_back_on_the_opener(
    kit: BoxServer, theme_name: str
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _ctx(p, kit, sid, 1280, theme_name=theme_name, exe=exe)
        page.goto(f"{kit.base}/box-kit", wait_until="networkidle")
        opener = page.locator("[data-box-open=export]")
        assert opener.get_attribute("href") == "/box-kit/open"  # the old address, for no scripts
        assert page.locator("#export").is_hidden()
        opener.click()
        box = _box(page)
        assert page.locator(".mtgbox-scrim.in").count() == 1  # the transition ran to its end state
        assert page.url.endswith("/box-kit")  # a box, not a page
        assert box.locator(".bh h2").inner_text() == "Export “Meren”"
        assert (
            box.get_attribute("aria-modal") == "true"
            and box.get_attribute("aria-labelledby") == "export-title"
        )
        x = box.locator(".bh .bx")
        bb = x.bounding_box()
        assert bb["width"] >= 44 and bb["height"] >= 44
        # the look: rounder and softer than the panels, in both themes
        radius = page.evaluate(STYLE, [".mtgbox-scrim .mtgbox", "borderRadius"])
        assert radius == "14px"
        shadow = page.evaluate(STYLE, [".mtgbox-scrim .mtgbox", "boxShadow"])
        assert "48px" in shadow
        assert page.evaluate(CONTRAST, ".mtgbox .bh h2, .mtgbox .body p, .mtgbox .acts button") == []
        # focus went into the box: the first field
        assert page.evaluate("() => document.activeElement.id") == "nm"
        # Tab stays inside the box: from the last button back to the first thing
        for _ in range(12):
            page.keyboard.press("Tab")
            assert page.evaluate("() => !!document.activeElement.closest('.mtgbox')")
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("() => !!document.activeElement.closest('.mtgbox')")
        # long content scrolls inside the box, not the page
        assert page.evaluate(SCROLLS)
        assert page.evaluate("() => getComputedStyle(document.documentElement).overflow") == "hidden"
        assert page.evaluate(MEASURE) == []
        page.keyboard.press("Escape")
        _gone(page)
        assert (
            page.locator("#export").is_hidden() and page.locator("#export").count() == 1
        )  # back in the page
        assert page.evaluate("() => document.activeElement.getAttribute('data-box-open')") == "export"
        assert page.evaluate("() => getComputedStyle(document.documentElement).overflow") != "hidden"
        # the scrim and Cancel close it too; the × as well
        opener.click()
        _box(page)
        page.mouse.click(5, 400)
        _gone(page)
        opener.click()
        _box(page).locator("[data-cancel]").click()
        _gone(page)
        opener.click()
        _box(page).locator(".bx").click()
        _gone(page)
        assert page.url.endswith("/box-kit")
        # the form inside the box submits as it would have on the old page
        opener.click()
        _box(page).locator("#nm").fill("Meren 2")
        with page.expect_navigation(wait_until="networkidle"):
            _box(page).locator(".acts button", has_text="Save").click()
        assert page.locator("#sent").inner_text() == "Saved Meren 2"
        browser.close()
    assert errors == [], errors


def test_the_old_address_opens_the_page_with_the_box_open_and_closing_puts_the_address_back(
    kit: BoxServer,
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _ctx(p, kit, sid, 1280, exe=exe)
        page.goto(f"{kit.base}/box-kit/open", wait_until="networkidle")
        box = _box(page)
        assert box.locator(".bh h2").inner_text() == "Export “Meren”"
        assert box.locator(".bx").get_attribute("href") == "/box-kit"
        box.locator(".bx").click()
        _gone(page)
        assert page.url == f"{kit.base}/box-kit"  # the page without the box
        assert page.locator("#export").is_hidden()
        browser.close()
    assert errors == [], errors


def test_without_scripts_the_button_is_a_link_and_the_old_address_shows_the_box_as_a_panel(
    kit: BoxServer,
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _ctx(p, kit, sid, 1280, exe=exe, java_script_enabled=False)
        page.goto(f"{kit.base}/box-kit", wait_until="networkidle")
        assert page.locator("#export").is_hidden()
        page.locator("[data-box-open=export]").click()
        page.wait_for_url(f"{kit.base}/box-kit/open")
        panel = page.locator("#export")
        assert panel.is_visible() and page.locator(".mtgbox-scrim").count() == 0
        assert panel.locator(".bh h2").is_visible() and panel.locator("[data-cancel]").is_hidden()
        assert panel.locator(".acts button", has_text="Save").is_visible()
        # a plain panel, with the panels' corners, not a floating card
        assert page.evaluate("() => getComputedStyle(document.querySelector('#export')).position") == "static"
        assert page.evaluate(MEASURE) == []
        # the × is the link back to the page without the box
        panel.locator(".bx").click()
        page.wait_for_url(f"{kit.base}/box-kit")
        browser.close()
    assert errors == [], errors


def test_on_a_phone_the_box_is_a_sheet_from_the_bottom_and_back_closes_it(kit: BoxServer) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _ctx(p, kit, sid, 390, exe=exe, has_touch=True, is_mobile=True)
        page.goto(f"{kit.base}/box-kit", wait_until="networkidle")
        page.locator("[data-box-open=export]").click()
        _box(page)
        page.wait_for_function(SHEET)
        radius = page.evaluate(STYLE, [".mtgbox-scrim .mtgbox", "borderTopLeftRadius"])
        assert radius == "14px"
        assert page.evaluate(STYLE, [".mtgbox-scrim .mtgbox", "borderBottomLeftRadius"]) == "0px"
        assert page.evaluate(MEASURE) == []
        # the phone's Back closes the sheet instead of leaving the page
        page.go_back()
        _gone(page)
        assert page.url.endswith("/box-kit")
        # 200% text: the sheet still fits, nothing scrolls sideways, the × is still 44 px
        page.evaluate("() => { document.documentElement.style.fontSize = '28px'; }")
        page.locator("[data-box-open=export]").click()
        box = _box(page)
        bb = box.locator(".bx").bounding_box()
        assert bb["width"] >= 44 and bb["height"] >= 44
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(SCROLLS)
        browser.close()
    assert errors == [], errors


def test_under_reduced_motion_the_box_comes_and_goes_at_once(kit: BoxServer) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _ctx(p, kit, sid, 1280, exe=exe, reduced_motion="reduce")
        page.goto(f"{kit.base}/box-kit", wait_until="networkidle")
        page.locator("[data-box-open=export]").click()
        # no frame of waiting: the open state is there as soon as the click is handled
        assert page.locator(".mtgbox-scrim.in").count() == 1
        assert (
            page.evaluate(
                "() => getComputedStyle(document.querySelector('.mtgbox-scrim')).transitionDuration"
            )
            == "0s"
        )
        page.keyboard.press("Escape")
        assert page.locator(".mtgbox-scrim").count() == 0  # gone at once, no fade
        browser.close()
    assert errors == [], errors


def test_the_notice_strips_have_no_border_of_their_own_and_read_at_4_5_to_1(kit: BoxServer) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        for theme_name in ("dark", "light"):
            browser, page, errors = _ctx(p, kit, sid, 1280, theme_name=theme_name, exe=exe)
            page.goto(f"{kit.base}/box-kit", wait_until="networkidle")
            for sel in (".notice.ok", ".notice.error"):
                st = page.evaluate(BORDERS, sel)
                assert st[:3] == ["0px", "0px", "0px"] and st[3] == "4px", (sel, st)
                assert st[4] == "0px" and st[5] == "5px", st
            assert page.evaluate(CONTRAST, ".notice.ok, .notice.error") == []
            browser.close()
            assert errors == [], errors


def test_the_menus_rename_box_wears_the_same_header(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    from .test_menus_browser import _link, _menu, _pick, _server

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _link(server)
        with sync_playwright() as p:
            browser, page, errors = _ctx(p, server, sid, 1280, exe=exe)
            page.goto(f"{server.base}/decks?folder=503", wait_until="networkidle")
            page.locator("li.deck[data-deck-id='42'] button.kebab").click()
            _menu(page)
            _pick(page, "Rename…")
            box = _box(page)
            assert box.locator(".bh h2").inner_text() == "Rename deck"
            bb = box.locator(".bh .bx").bounding_box()
            assert bb["width"] >= 44 and bb["height"] >= 44
            assert page.evaluate(STYLE, [".mtgbox-scrim .mtgbox", "borderRadius"]) == "14px"
            assert page.evaluate("() => document.activeElement.id") == "mb-name"
            box.locator(".bx").click()
            _gone(page)
            assert page.evaluate("() => document.activeElement.closest('li.deck') !== null")
            browser.close()
        assert errors == [], errors
    finally:
        server.stop()


def test_a_box_opened_from_a_menu_on_a_touch_screen_stays_until_back_is_pressed(tmp_path: Path) -> None:
    """The menu closes over its own history entry when an item is picked; the popstate that
    follows is not a Back press on the box the item opened. Back then closes the box, not the page."""
    from playwright.sync_api import sync_playwright

    from .test_menus_browser import _link, _menu, _pick, _server

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _link(server)
        with sync_playwright() as p:
            browser, page, errors = _ctx(p, server, sid, 720, exe=exe, has_touch=True, is_mobile=True)
            page.goto(f"{server.base}/decks", wait_until="networkidle")
            page.locator("li.folder[data-folder-id='504'] button.kebab").click()
            _menu(page)
            _pick(page, "Rename…")
            box = _box(page)
            page.wait_for_timeout(600)  # the menu's Back has landed by now
            assert page.locator(".mtgbox-scrim .mtgbox[role=dialog]").count() == 1
            assert box.locator("input#mb-fname").input_value() == "Archive"
            page.go_back()
            _gone(page)
            assert page.url.endswith("/decks")
            browser.close()
        assert errors == [], errors
    finally:
        server.stop()
