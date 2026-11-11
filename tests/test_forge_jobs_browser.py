"""A Forge test in the background, followed in a real browser (owner decision 11-01): the report page's
progress panel, the deck page's running mark and Home's row move on by themselves as Forge plays (the
step, the games played, the time so far), say when the test has ended, and the report page stops a
test from its panel. Phone, Fold and desktop widths, light and dark, and twice the text size."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from .test_history_page import store_report
from .test_scan_browser import _chromium_path
from .test_social_browser import Server
from .waits import sideways

pytest.importorskip("playwright")

WIDTHS = (360, 412, 717, 840, 1280)


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _playing(server: Server, rid: str, done: int, *, state: str = "running") -> None:
    forge = {
        "state": state,
        "job_id": "job-1",
        "games_requested": 5,
        "queued_at": int(time.time()) - 300,
        "seats": ["Liesa Test Deck", "Precon 1", "Precon 2", "Precon 3"],
        "result": {"games": done, "games_requested": 5},
    }
    with server.db.tx() as c:
        c.execute(
            "UPDATE reports SET forge_state = ?, forge_json = ? WHERE id = ?", (state, json.dumps(forge), rid)
        )


def _context(browser, server: Server, sid: str, width: int, scheme: str = "light", scale: float = 1.0):
    ctx = browser.new_context(
        viewport={"width": width, "height": 900},
        is_mobile=width < 900,
        has_touch=width < 900,
        color_scheme=scheme,
        device_scale_factor=1,
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    if scale != 1.0:  # the member's text size at twice the default
        ctx.add_init_script(
            f"document.addEventListener('DOMContentLoaded', () => "
            f"document.documentElement.style.fontSize = '{int(scale * 100)}%')"
        )
    return ctx


def _no_sideways_scroll(page) -> None:
    over = sideways(page)
    assert over <= 0, over


def test_the_deck_page_and_home_follow_a_test_until_it_ends(server: Server, tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    store_report(server.db, "user-1", "rep_run", goldfish=None)
    _playing(server, "rep_run", 2)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = _context(browser, server, sid, 360)
            page = ctx.new_page()
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            mark = page.locator("p.notice.testing")
            assert "Test running: Forge has played 2 of 5 games." in mark.inner_text()
            assert "5 min so far." in mark.inner_text()
            # the live words are one region that stays in place, so a finished game is announced
            page.evaluate("() => { document.querySelector('p.notice.testing [role=status]').kept = '1'; }")
            _playing(server, "rep_run", 3)
            page.wait_for_function(
                "() => document.querySelector('p.notice.testing').innerText.includes('3 of 5 games')",
                timeout=15000,
            )
            assert page.evaluate("() => document.querySelector('p.notice.testing [role=status]').kept") == "1"
            _no_sideways_scroll(page)
            # Home, at the same time
            home = ctx.new_page()
            home.goto(f"{server.base}/", wait_until="networkidle")
            row = home.locator("section.needs li", has_text="test running")
            assert "1 test running: Forge has played 3 of 5 games." in row.inner_text()
            # the deck and the time are two facts, the dot between them drawn (ui.facts)
            assert "<span>Liesa Test Deck</span><span>5 min so far</span>" in row.inner_html()
            # the test ends: both say so, with the way to the report, and stop asking
            _playing(server, "rep_run", 5, state="done")
            page.wait_for_function("() => document.body.innerText.includes('Test finished.')", timeout=15000)
            done = page.locator("p.notice.testing.done")
            assert done.locator("a.ui-cap[href='/history/reports/rep_run']").count() == 1
            assert page.locator("[data-running-live]").count() == 0
            home.wait_for_function("() => document.body.innerText.includes('Test finished')", timeout=15000)
            assert home.locator("section.needs li a[href='/history/reports/rep_run']").count() == 1
            assert "test running" not in home.locator("section.needs").inner_text()
            ctx.close()
        finally:
            browser.close()


def test_the_report_page_follows_a_test_and_stops_it(server: Server, tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    store_report(server.db, "user-1", "rep_run", goldfish=None)
    _playing(server, "rep_run", 2)
    # FORGE_JOBS_SHOTS keeps the screenshots for a look; otherwise they go with the test's folder
    shots = Path(os.environ.get("FORGE_JOBS_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            # every width and both themes, and twice the text size on a phone: the panel fits, its Stop
            # button is finger-sized and its words read clearly
            for width, scheme, scale in [
                *((w, "light", 1.0) for w in WIDTHS),
                (360, "dark", 1.0),
                (1280, "dark", 1.0),
                (360, "light", 2.0),
            ]:
                ctx = _context(browser, server, sid, width, scheme, scale)
                page = ctx.new_page()
                page.goto(f"{server.base}/history/reports/rep_run", wait_until="networkidle")
                panel = page.locator("#rv-progress")
                assert panel.is_visible()
                assert "Forge has played 2 of 5 games." in panel.inner_text()
                assert page.locator("#rv-progress progress.rv-bar").get_attribute("value") == "2"
                stop = panel.locator("a.btn", has_text="Stop the test")
                box = stop.bounding_box()
                assert box and box["height"] >= 44, (width, box)
                _no_sideways_scroll(page)
                colours = page.evaluate(
                    "() => { const q = (s) => getComputedStyle(document.querySelector(s));"
                    " return [q('#rv-progress .rv-time').color, q('#rv-progress').backgroundColor]; }"
                )
                assert colours[0] != colours[1]
                page.screenshot(path=str(shots / f"progress-{width}-{scheme}-{scale}.png"), full_page=False)
                ctx.close()
            ctx = _context(browser, server, sid, 412)
            page = ctx.new_page()
            page.goto(f"{server.base}/history/reports/rep_run", wait_until="networkidle")
            page.evaluate("() => { document.querySelector('#rv-progress [aria-live]').kept = '1'; }")
            _playing(server, "rep_run", 3)
            page.wait_for_function(
                "() => document.querySelector('#rv-progress').innerText.includes('3 of 5 games')",
                timeout=15000,
            )
            assert page.locator("#rv-progress progress.rv-bar").get_attribute("value") == "3"
            assert page.evaluate("() => document.querySelector('#rv-progress [aria-live]').kept") == "1"
            # Stop asks first, then stops: the games played stay, the panel goes
            page.locator("#rv-progress a.btn", has_text="Stop the test").click()
            dialog = page.locator("#rv-stop")
            dialog.wait_for(state="visible")
            assert "Stop this test?" in dialog.inner_text() and "Keep it going" in dialog.inner_text()
            page.screenshot(path=str(shots / "stop-box-412.png"))
            with page.expect_navigation():
                dialog.locator("button", has_text="Stop the test").click()
            assert page.url.endswith("/history/reports/rep_run?stopped=1")
            assert "You stopped this test." in page.locator("body").inner_text()
            assert page.locator("#rv-progress").is_hidden()
            forge = page.locator("#rv-forge").inner_text()
            assert "Forge stopped after 3 of 5 games, because you stopped the test." in forge
            page.screenshot(path=str(shots / "stopped-412.png"), full_page=True)
            ctx.close()
        finally:
            browser.close()
