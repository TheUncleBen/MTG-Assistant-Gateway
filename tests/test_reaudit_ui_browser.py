"""The 0.7.46 re-audit's UI findings, each measured in headless Chromium on the overflow sweep's
hostile data (test_overflow_sweep_browser): the Binder list on phones gives the card's name the
row (the name column was 32 px at 360 px and the row about 310 px tall), the deck page's tool row
keeps its ⋮ on the one row at 360 px, the sort drop-downs show their choice on one line, the
Activity filters share one row, and the deck page comes back to where it was after Back, every
time. Skipped without a Chromium build."""

from __future__ import annotations

import re

import pytest

from .test_editor_browser import PNG
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

APP_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Mobile Safari/537.36 MTGAssistant/0.7.46"
)
PHONES = [344, 360, 390, 412]
FOLDS = [717, 840]


def _ctx(
    browser,
    server: Server,  # noqa: F811
    sid: str,
    width: int,
    *,
    touch: bool = True,
    app: bool = False,
    height=860,  # noqa: F811
):
    kw = {"has_touch": True, "is_mobile": True} if touch else {}
    if app:
        kw["user_agent"] = APP_UA
    ctx = browser.new_context(viewport={"width": width, "height": height}, **kw)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    return ctx


# The Binder list's first rows: the row's width, the name's width and the row's height.
LIST_ROWS = """
() => [...document.querySelectorAll('ul.colllist .tr.row')].slice(0, 4).map(li => {
  const r = li.getBoundingClientRect(), n = li.querySelector('.name').getBoundingClientRect();
  return {row: Math.round(r.width), name: Math.round(n.width), height: Math.round(r.height),
          text: li.querySelector('.name').textContent.trim().slice(0, 24)};
})
"""


@pytest.mark.parametrize("app", [False, True], ids=["web", "app"])
def test_the_binder_list_gives_the_name_the_row_on_phones_and_folds(server: Server, app: bool) -> None:  # noqa: F811
    """At every phone and Fold width the name column of the Binder's list view is at least two
    fifths of the row (it was 32 px at 360 px, one word every few letters) and no row is taller
    than 240 px (it was about 310 px), with the long tagged names of the sweep's data, among them
    one unbroken 31-letter name that takes three lines whatever the layout."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _ = _seed(server)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for w in PHONES + FOLDS:
            ctx = _ctx(browser, server, sid, w, app=app)
            page = ctx.new_page()
            r = page.goto(f"{server.base}/collection?view=list", wait_until="networkidle")
            assert r is not None and r.status == 200
            rows = page.evaluate(LIST_ROWS)
            assert len(rows) == 4
            for row in rows:
                if row["name"] < row["row"] * 0.4:
                    problems.append(f"{w}px: name {row['name']} of {row['row']} px for “{row['text']}”")
                if row["height"] > 240:
                    problems.append(f"{w}px: row {row['height']} px tall for “{row['text']}”")
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# A finger's touch on an element, as test_deck_menu_browser sends it: the touch never ends until
# the caller says so, as a hold.
TOUCH_START = """(c) => {
  const r = c.getBoundingClientRect();
  const t = new Touch({identifier: 7, target: c, clientX: r.left + r.width / 2, clientY: r.top + 20});
  c.dispatchEvent(new TouchEvent("touchstart",
    {bubbles: true, cancelable: true, touches: [t], targetTouches: [t], changedTouches: [t]}));
}"""
TOUCH_END = """(c) => {
  const r = c.getBoundingClientRect();
  const t = new Touch({identifier: 7, target: c, clientX: r.left + r.width / 2, clientY: r.top + 20});
  c.dispatchEvent(new TouchEvent("touchend",
    {bubbles: true, cancelable: true, touches: [], targetTouches: [], changedTouches: [t]}));
}"""


def test_a_hold_on_a_deck_page_row_opens_its_menu_during_the_hold(server: Server) -> None:  # noqa: F811
    """On a deck page in the app a press and hold on a card row opens the card's menu while the
    finger is still down, as every other list does (re-audit D09: it opened only when the finger
    lifted), and the lift leaves it open."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _ = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, 412, app=True, height=915)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        row.scroll_into_view_if_needed()
        row.evaluate(TOUCH_START)
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(state="visible", timeout=1500)  # during the hold: no touchend was sent
        assert menu.locator(".head").inner_text() == "Cultivate"
        row.evaluate(TOUCH_END)
        # fixed wait: a negative check: the lift after the hold does not close the menu within 100 ms
        page.wait_for_timeout(100)
        assert menu.is_visible()
        browser.close()
    assert errors == []


def test_the_deck_page_comes_back_to_where_it_was_after_back_every_time(server: Server) -> None:  # noqa: F811
    """On a phone, a deck page scrolled to a row, then Settings, then Back lands on the same row
    ten times out of ten (re-audit N05: five of eight runs landed 680 px further down)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _ = _seed(server)
    landed: list[int] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, 412, app=True, height=915)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for _ in range(10):
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            page.evaluate("() => scrollTo(0, 2200)")
            page.wait_for_function("() => Math.abs(scrollY - 2200) < 2")
            page.evaluate("() => location.assign('/decks/42/settings')")
            page.wait_for_url(re.compile(r"/decks/42/settings$"))
            page.wait_for_load_state("networkidle")
            page.go_back()
            page.wait_for_url(re.compile(r"/decks/42$"))
            page.wait_for_load_state("networkidle")
            # once the page has settled (no scroll change over a frame)
            page.wait_for_function(
                "() => new Promise(r => { const y = scrollY; requestAnimationFrame(() =>"
                " requestAnimationFrame(() => r(scrollY === y))); })"
            )
            landed.append(page.evaluate("() => Math.round(scrollY)"))
        browser.close()
    assert errors == []
    assert all(abs(y - 2200) <= 2 for y in landed), landed


# Where things sit: the boxes of the elements a selector finds, by index.
BOXES = """(sel) => [...document.querySelectorAll(sel)].filter(e => e.getClientRects().length).map(e => {
  const r = e.getBoundingClientRect();
  return {top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left),
          right: Math.round(r.right), width: Math.round(r.width), height: Math.round(r.height)};
})"""


def test_home_puts_what_needs_you_beside_the_decks_on_a_desktop(server: Server) -> None:  # noqa: F811
    """At 1280 and 1440 px with a mouse the Home page's deck covers take the page's width with
    Needs you and the connection details in a column beside them, and a cover is at least 240 px
    wide (re-audit D28: 144 px covers in one row and the lower half of the screen empty). Below
    1280 px the sections follow each other as before."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _ = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for w in (1280, 1440, 1024):
            ctx = _ctx(browser, server, sid, w, touch=False)
            page = ctx.new_page()
            r = page.goto(f"{server.base}/", wait_until="networkidle")
            assert r is not None and r.status == 200
            (needs,) = page.evaluate(BOXES, ".home .needs")
            (panel,) = page.evaluate(BOXES, ".home .home-grid > .panel:not(.connect)")
            (connect,) = page.evaluate(BOXES, ".home details.connect")
            covers = page.evaluate(BOXES, ".home .recent a")
            assert page.evaluate("() => document.documentElement.scrollWidth") == w
            if w >= 1280:
                assert needs["left"] >= panel["right"] and connect["left"] >= panel["right"], (
                    w,
                    needs,
                    panel,
                )
                assert abs(needs["top"] - panel["top"]) <= 2 and connect["top"] > needs["bottom"], (w, needs)
                assert all(c["width"] >= 240 for c in covers), (w, covers)
            else:
                assert needs["bottom"] <= panel["top"] <= connect["top"], (w, needs, panel, connect)
            ctx.close()
        browser.close()


@pytest.mark.parametrize("width", [360, 412])
def test_the_deck_page_tool_row_keeps_its_menu_on_the_one_row(server: Server, width: int) -> None:  # noqa: F811
    """On a phone the deck page's tool row (the quick-add field, View and ⋮) is one row at 360
    and 412 px (re-audit D04: at 360 px the ⋮ wrapped alone under the row)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _ = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, width, app=True)
        page = ctx.new_page()
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        page.locator(".toolbar.compact .toolmore").wait_for(state="visible")
        boxes = page.evaluate(BOXES, ".toolbar.compact .controls > *")
        tops = {b["top"] for b in boxes}
        assert len(boxes) >= 3 and len(tops) == 1, boxes
        browser.close()


def test_the_sort_drop_downs_show_their_choice_on_one_line_in_the_app(server: Server) -> None:  # noqa: F811
    """At 412 px in the app the closed Sort by drop-downs on Precons and the Binder are one line
    tall (re-audit D12: "Set release / ↓" on two lines, 51 px; "Price, highest first" on two)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _ = _seed(server)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, 412, app=True)
        page = ctx.new_page()
        for path, sel in (("/precons", ".g.sort select"), ("/collection", ".g.sort select")):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            for sel_box in page.evaluate(BOXES, sel):
                line = page.evaluate("() => parseFloat(getComputedStyle(document.body).lineHeight) || 24")
                if sel_box["height"] > 48 or sel_box["height"] > line * 2:
                    problems.append(f"{path}: {sel} is {sel_box['height']} px tall")
        browser.close()
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("width", [360, 412])
def test_the_activity_filters_share_one_row_on_phones(server: Server, width: int) -> None:  # noqa: F811
    """On My activity the three filters (App, Event, When) share one row at phone widths
    (re-audit D31: two on the first row and the third full width under them)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _ = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, width, app=True)
        page = ctx.new_page()
        page.goto(f"{server.base}/activity", wait_until="networkidle")
        boxes = page.evaluate(BOXES, ".ui-tools .f")
        assert len(boxes) == 3, boxes
        assert len({b["top"] for b in boxes}) == 1, boxes
        assert all(b["width"] >= 90 for b in boxes), boxes
        browser.close()


def test_the_apply_buttons_are_buttons_while_the_click_guard_is_locked(server: Server) -> None:  # noqa: F811
    """On a proposal's page in the app, Apply and Reject are drawn as real buttons from the
    start, with the lock's note in their bar (re-audit: they were dashed outlines until the
    guard unlocked, which read as disabled for good); the guard still holds them."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, 412, app=True, height=915)
        page = ctx.new_page()
        page.goto(f"{server.base}/proposals/{pending}", wait_until="domcontentloaded")
        apply = page.locator("form.guarded button[data-guard][value=apply]").first
        apply.wait_for(state="visible")
        assert apply.is_disabled()  # the guard holds it at first
        style = apply.evaluate(
            "b => { const c = getComputedStyle(b); return {border: c.borderStyle, opacity: c.opacity,"
            " bg: c.backgroundColor, color: c.color}; }"
        )
        assert style["border"] != "dashed" and style["opacity"] == "1", style
        assert style["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), style
        hint = page.locator("form.guarded .choice .guard-hint")
        assert hint.is_visible() and "tap once to unlock" in hint.inner_text()
        hint_box, apply_box = (
            page.evaluate(BOXES, "form.guarded .choice .guard-hint")[0],
            page.evaluate(BOXES, "form.guarded button[data-guard][value=apply]")[0],
        )
        assert hint_box["bottom"] <= apply_box["top"], (hint_box, apply_box)
        browser.close()


def test_the_glance_tiles_fold_behind_one_line_on_phones(server: Server) -> None:  # noqa: F811
    """On a phone the Proposals and Binder pages lead with their list: the stat tiles sit behind
    one 44 px line that says the gist and opens them on a tap (re-audit: four tiles before the
    list on a phone). At 1280 px the tiles show whole and the line is not there."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _ = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, 412, app=True, height=915)
        page = ctx.new_page()
        for path, list_sel in (("/proposals", ".ui-table"), ("/collection", ".ui-table, ul.collgrid")):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            label = page.locator("label.glance-lbl").first
            assert label.is_visible(), path
            (lbox,) = page.evaluate(BOXES, "label.glance-lbl")
            assert lbox["height"] >= 44, (path, lbox)
            assert not page.locator("section.ui-widgets").first.is_visible(), path
            (lst,) = page.evaluate(BOXES, list_sel)[:1]
            assert lst["top"] < 915, (path, lst)  # the list starts on the first screen
            label.click()
            page.locator("section.ui-widgets").first.wait_for(state="visible")
        ctx.close()
        ctx = _ctx(browser, server, sid, 1280, touch=False)
        page = ctx.new_page()
        page.goto(f"{server.base}/proposals", wait_until="networkidle")
        assert not page.locator("label.glance-lbl").first.is_visible()
        assert page.locator("section.ui-widgets").first.is_visible()
        browser.close()
