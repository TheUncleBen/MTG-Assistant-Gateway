"""Assistant pages in a real browser on a phone (0.7.46 review, findings F6, F8, F12 and F14): a
test still going shows on its deck's page and on Home with the animated spinner and the way to its
report; the Goldfish page's way back is the grey capsule, not blue text; every card name on a
report reads the same on a touch screen; the deck names on History and Reports are finger-sized."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from mtg_gateway import report_page

from .test_history_page import store_report
from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")

SPIN = "span.ui-spin"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _phone(browser, server: Server, sid: str, scheme: str = "light"):
    ctx = browser.new_context(
        viewport={"width": 375, "height": 800}, is_mobile=True, has_touch=True, color_scheme=scheme
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    return ctx


def _set(server: Server, rid: str, **cols) -> None:
    sets = ", ".join(f"{k} = ?" for k in cols)
    with server.db.tx() as c:
        c.execute(f"UPDATE reports SET {sets} WHERE id = ?", (*cols.values(), rid))


def _animated(page, selector: str) -> bool:
    return page.locator(selector).first.evaluate("e => getComputedStyle(e).animationName.includes('ui-spin')")


def test_a_running_test_shows_on_its_deck_page_and_on_home_until_it_is_done(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    # Forge playing its games: 2 of 5 done
    store_report(server.db, "user-1", "rep_run", goldfish=None)
    forge = {"state": "running", "games_requested": 5, "result": {"games": 2}}
    _set(server, "rep_run", forge_state="running", forge_json=json.dumps(forge))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = _phone(browser, server, sid)
            page = ctx.new_page()
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            mark = page.locator("p.notice.testing")
            assert mark.count() == 1
            assert "Test running: Forge has played 2 of 5 games." in mark.inner_text()
            assert _animated(page, f"p.notice.testing {SPIN}")
            link = mark.locator("a.ui-cap", has_text="Open the report")
            assert link.get_attribute("href") == "/history/reports/rep_run"
            box = link.bounding_box()
            assert box and box["height"] >= 44, box
            page.goto(f"{server.base}/", wait_until="networkidle")
            row = page.locator("section.needs li", has_text="test running")
            assert row.count() == 1
            assert "1 test running: Forge has played 2 of 5 games." in row.inner_text()
            assert _animated(page, f"section.needs li {SPIN}")
            assert row.locator("a[href='/history/reports/rep_run']").count() == 1
            assert "Forge test" not in page.locator("section.needs").inner_text()
            # the research service's part still out: its own words
            _set(server, "rep_run", research_state="running")
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            assert (
                "the solo test is running on the research service"
                in page.locator("p.notice.testing").inner_text()
            )
            # every part done: the mark is gone from both pages
            _set(server, "rep_run", research_state=None, forge_state="done")
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            assert page.locator("p.notice.testing").count() == 0
            page.goto(f"{server.base}/", wait_until="networkidle")
            assert "test running" not in page.locator("body").inner_text()
            ctx.close()
        finally:
            browser.close()


def test_the_goldfish_way_back_is_the_grey_capsule(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            for scheme in ("light", "dark"):
                ctx = _phone(browser, server, sid, scheme)
                page = ctx.new_page()
                page.goto(f"{server.base}/decks/42/goldfish", wait_until="networkidle")
                assert page.locator(".gf-back").count() == 0
                back = page.locator(".gf-crumbs a.ui-cap")
                assert back.count() == 1 and back.get_attribute("href") == "/decks/42"
                assert "Back to the deck" in back.inner_text()
                box = back.bounding_box()
                assert box and box["height"] >= 44, box
                colours = page.evaluate(
                    "() => [getComputedStyle(document.querySelector('.gf-crumbs a.ui-cap')).color,"
                    " getComputedStyle(document.body).color,"
                    " getComputedStyle(document.documentElement).getPropertyValue('--link').trim()]"
                )
                assert colours[0] == colours[1], (scheme, colours)
                # the deck is still named, beside the page's title
                assert "Sample Commander Deck" in page.locator(".gf-head h1 .gf-deck").inner_text()
                ctx.close()
        finally:
            browser.close()


def test_every_card_name_on_a_report_reads_the_same_on_a_touch_screen(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    # the orange underline is for a pointer that can hover, never left on the last tapped name
    assert re.search(r"@media \(hover:hover\)\{\.rv-page \.cardlink:hover", report_page.REPORT_PAGE_CSS)
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    store_report(server.db, "user-1", "rep_done")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = _phone(browser, server, sid)
            page = ctx.new_page()
            page.goto(f"{server.base}/history/reports/rep_done", wait_until="networkidle")
            assert not page.evaluate("() => matchMedia('(hover: hover)').matches")  # a phone
            names = page.locator("button.cardlink[data-card]")
            assert names.count() >= 2
            names.first.hover()
            style = "e => [getComputedStyle(e).color, getComputedStyle(e).textDecorationLine]"
            assert names.first.evaluate(style) == names.nth(1).evaluate(style)
            ctx.close()
        finally:
            browser.close()


def test_deck_names_on_history_and_reports_are_finger_sized(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    now = int(time.time())
    for i in range(3):
        store_report(server.db, "user-1", f"rep_{i}", taken_at=now - 600 * (i + 1))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = _phone(browser, server, sid)
            page = ctx.new_page()
            for path, selector in (
                ("/history", ".hy-table .title a.t"),
                ("/reports", ".reports-page .rp-t .title .t a"),
            ):
                page.goto(f"{server.base}{path}", wait_until="networkidle")
                links = page.locator(selector)
                assert links.count() >= 3, path
                for i in range(links.count()):
                    box = links.nth(i).bounding_box()
                    assert box and box["height"] >= 44, (path, i, box)
                assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
            ctx.close()
        finally:
            browser.close()
