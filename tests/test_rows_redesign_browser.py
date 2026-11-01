"""The Proposals, History and Reports rows after the 0.7.46 redesign, measured in headless
Chromium: each row's ⋮ sits on its title's line (never on a line of its own) on a phone, a Fold
and a desktop; on a phone the title, the main action and the ⋮ are 44 px targets; the Fold's
filter row puts the search on its own line with the filters sharing the next; the ⋮ opens the
row's menu from a tap and a right-click, and History's Restore in it still sends its form; at
200% text nothing scrolls sideways and the ⋮ keeps its line. Skipped without a Chromium build."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_cardsearch_browser import _page
from .test_menus_browser import _exe, _items, _menu, _seed_rows, _server
from .test_text_zoom_browser import TEXT_200

pytest.importorskip("playwright")

NO_SIDEWAYS = "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
ROWS = {
    "/proposals": ".pr-table .tr:not(.th):not(.group)",
    "/history": ".hy-table .tr:not(.th):not(.daygroup):not(.group)",
    "/reports": ".rp-t .tr:not(.th):not(.group)",
}

# the rows' boxes: the title's line, the main action and the ⋮ (height, width and their lines)
BOXES = """
(sel) => [...document.querySelectorAll(sel)].slice(0, 6).map(tr => {
  const r = (el) => {
    const b = el.getBoundingClientRect(); return {x: b.x, y: b.y, w: b.width, h: b.height}; };
  const title = tr.querySelector('.title .t, .title .t a');
  const tap = title.closest('a, label') || title.querySelector('a') || title;
  const kebab = tr.querySelector('.kebab');
  const main = tr.querySelector('.act > .ui-cap');
  return {title: r(title), tap: r(tap), kebab: r(kebab), main: r(main), row: r(tr),
          text: title.textContent.trim()};
})
"""


def _on_the_title_line(b: dict) -> bool:
    t, k = b["title"], b["kebab"]
    return k["y"] < t["y"] + t["h"] and k["y"] + k["h"] > t["y"]


@pytest.mark.parametrize("width", [375, 840, 1280])
def test_the_menu_button_shares_the_title_line_and_a_phone_gets_44px_targets(
    tmp_path: Path, width: int
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _seed_rows(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, width, "dark", exe)
            for path, sel in ROWS.items():
                page.goto(f"{server.base}{path}", wait_until="networkidle")
                boxes = page.evaluate(BOXES, sel)
                assert boxes, path
                for b in boxes:
                    where = f"{path} @{width}px {b['text']!r}"
                    assert _on_the_title_line(b), (where, b)
                    assert b["kebab"]["x"] + b["kebab"]["w"] <= b["row"]["x"] + b["row"]["w"] + 1, (where, b)
                    assert b["kebab"]["w"] >= 44 and b["kebab"]["h"] >= 44, (where, b["kebab"])
                    assert b["main"]["w"] >= 44 and b["main"]["h"] >= 44, (where, b["main"])
                    if width < 600:
                        # the title (History's deck name, F14) is a 44 px tap target on a phone
                        assert b["tap"]["h"] >= 44, (where, b["tap"])
                assert page.evaluate(NO_SIDEWAYS), f"{path} @{width}px"
            if width == 840:
                # the Fold (F13): the search takes its own line, every filter shares the next
                for path in ROWS:
                    page.goto(f"{server.base}{path}", wait_until="networkidle")
                    tools = page.evaluate(
                        """() => { const f = document.querySelector('.ui-tools'); if (!f) return null;
                        const r = (el) => el.getBoundingClientRect();
                        return {w: r(f).width, search: r(f.querySelector('.search')).width,
                          tops: [...f.children].filter(el => !el.classList.contains('search') &&
                            el.tagName !== 'INPUT' && r(el).height > 0)
                            .map(el => Math.round(r(el).top))}; }"""
                    )
                    assert tools, path
                    assert tools["search"] >= tools["w"] - 2, (path, tools)
                    assert len(set(tools["tops"])) == 1, (path, tools)
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


def test_the_rows_menus_open_from_a_tap_and_a_right_click_and_restore_sends_its_form(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _seed_rows(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, 375, "light", exe)
            page.goto(f"{server.base}/proposals", wait_until="networkidle")
            row = page.locator(ROWS["/proposals"], has_text="Arcane Signet").first
            row.locator("a.kebab").tap()
            _menu(page)
            assert _items(page) == ["Review", "Apply", "Reject", "Open the deck"]
            page.keyboard.press("Escape")
            assert page.url.endswith("/proposals")  # the ⋮'s own link never followed
            row.locator(".title").click(button="right")
            _menu(page)
            assert _items(page) == ["Review", "Apply", "Reject", "Open the deck"]
            page.keyboard.press("Escape")
            # History: Restore from the ⋮ sends the row's hidden form, to the proposal to review
            page.goto(f"{server.base}/history", wait_until="networkidle")
            rows = page.locator(ROWS["/history"])
            snap = None
            for i in range(rows.count()):
                r = rows.nth(i)
                if r.locator("form.hy-restore").count():
                    snap = r
                    break
            assert snap is not None
            snap.locator("a.kebab").tap()
            _menu(page)
            assert "Restore (review first)" in _items(page) and "Test this version" in _items(page)
            with page.expect_navigation():
                page.locator(".ctxmenu [role=menuitem]", has_text="Restore (review first)").click()
            assert "/proposals/" in page.url, page.url
            assert page.locator("main", has_text="Immortal Reckoning").count() == 1
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


def test_at_200_percent_text_the_rows_hold_their_line(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _seed_rows(server)
        with sync_playwright() as p:
            for width in (375, 840):
                browser, page, errors = _page(p, server, sid, width, "dark", exe)
                for path, sel in ROWS.items():
                    page.goto(f"{server.base}{path}", wait_until="networkidle")
                    page.evaluate(TEXT_200)
                    page.wait_for_timeout(50)
                    assert page.evaluate(NO_SIDEWAYS), f"{path} @{width}px 200%"
                    for b in page.evaluate(BOXES, sel):
                        assert _on_the_title_line(b), (path, width, b)
                        assert b["kebab"]["x"] + b["kebab"]["w"] <= width + 1, (path, width, b)
                assert errors == [], errors
                browser.close()
    finally:
        server.stop()
