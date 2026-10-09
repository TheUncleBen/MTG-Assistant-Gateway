"""The report page and the history page in headless Chromium: no sideways scroll at phone and
laptop widths, the tiles and charts are styled (the CSS the report page once lacked), the day
headings stick, and the Copy button has its script. Screenshots land in .scratch/ when it
exists (never committed). Skipped without a Chromium build."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from .test_history_page import seed_history, store_report
from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")

SCRATCH = Path(__file__).resolve().parents[1] / ".scratch"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _shot(page, name: str) -> None:
    if SCRATCH.is_dir():
        page.screenshot(path=str(SCRATCH / f"{name}.png"), full_page=True)


@pytest.mark.parametrize("width", [360, 1366])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_report_and_history_pages_fit(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    now = int(time.time())
    seed_history(server.db, "user-1", now=now)
    store_report(server.db, "user-1", "rep_browser", taken_at=now - 200)
    store_report(server.db, "user-1", "rep_older", taken_at=now - 3 * 86400)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{server.base}/history/reports/rep_browser", wait_until="networkidle")
        state = page.evaluate(
            """() => {
              const tile = document.querySelector('.tiles.wide .tile');
              const cs = tile ? getComputedStyle(tile) : null;
              const svg = document.querySelector('.chart svg');
              const line = document.querySelector('.chart polyline.s1');
              return {
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth,
                tiles: document.querySelectorAll('.tiles.wide .tile').length,
                tileBg: cs ? cs.backgroundColor : null,
                tileDisplay: cs ? cs.display : null,
                charts: document.querySelectorAll('.chart svg').length,
                svgWidth: svg ? svg.getBoundingClientRect().width : 0,
                lineStroke: line ? getComputedStyle(line).stroke : null,
                copy: !!document.querySelector('.copybtn[data-copy=rep-md]'),
                actionsRow: getComputedStyle(document.querySelector('.report .form-actions')).display,
                text: document.body.innerText,
                bodyBg: getComputedStyle(document.body).backgroundColor,
                wide: Array.from(document.querySelectorAll('body *'))
                  .filter(e => e.getBoundingClientRect().right > window.innerWidth)
                  .map(e => e.tagName + '.' + e.className).slice(0, 12),
              };
            }"""
        )
        _shot(page, f"report-{width}-{scheme}")
        assert state["scrollWidth"] <= state["innerWidth"], state["wide"]
        assert state["tiles"] >= 5 and state["tileDisplay"] == "flex"
        assert state["tileBg"] not in (None, "rgba(0, 0, 0, 0)", "transparent"), "tiles are styled"
        assert state["charts"] == 4 and 0 < state["svgWidth"] <= state["innerWidth"]
        assert state["lineStroke"] == "rgb(250, 137, 13)", state["lineStroke"]  # the theme's orange
        assert state["copy"] and state["actionsRow"] == "flex"
        assert "goldfish_" not in state["text"] and "What the simulation could not model" in state["text"]
        assert state["bodyBg"] == ("rgb(24, 24, 24)" if scheme == "dark" else "rgb(249, 250, 251)")

        page.goto(f"{server.base}/history?deck_id=42", wait_until="networkidle")
        hist = page.evaluate(
            """() => {
              const day = document.querySelector('.history .day');
              const sel = document.querySelector('.filterbar select');
              const spark = document.querySelector('.trend .tile');
              return {
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth,
                rows: document.querySelectorAll('.hrow').length,
                dayPosition: day ? getComputedStyle(day).position : null,
                selectArrow: sel ? getComputedStyle(sel).appearance : null,
                selectWidth: sel ? sel.getBoundingClientRect().width : 0,
                trendBg: spark ? getComputedStyle(spark).backgroundColor : null,
              };
            }"""
        )
        _shot(page, f"history-{width}-{scheme}")
        assert hist["scrollWidth"] <= hist["innerWidth"], hist
        assert hist["rows"] == 25 and hist["dayPosition"] == "sticky"
        assert hist["selectArrow"] == "none" and 0 < hist["selectWidth"] <= hist["innerWidth"]
        assert hist["trendBg"] not in (None, "rgba(0, 0, 0, 0)", "transparent"), "trend tiles are styled"
        # a row's details open in place and the Restore button sits in a form-actions row
        page.locator(".hrow.k-snapshot details summary").first.click()
        restore = page.locator(".hrow.k-snapshot details[open] .form-actions button").first
        assert restore.is_visible() and "Restore (review first)" in restore.inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors, errors
        browser.close()
