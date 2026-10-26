"""In headless Chromium: the Collection page's "Importing the CSV on archidekt.com" fold
opens, shows the column table, and at 360, 720 and 1280 px, light and dark, nothing scrolls
sideways, no control is clipped or covered, the table fits the panel and its text keeps a 4.5:1
contrast. Set MTG_CSVHELP_SHOTS=<dir> to also save screenshots. Skipped without a Chromium build."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import SIZES, _chromium, _page
from .test_editor_browser import _link
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_CSVHELP_SHOTS")

# The table and its panel inside the window, and the table no wider than its panel.
FITS = """
() => {
  const vw = document.documentElement.clientWidth, out = [];
  const panel = document.querySelector('details.csvhelp > section.panel');
  const table = document.querySelector('details.csvhelp table');
  if (!panel || !table) return ['no help table'];
  const p = panel.getBoundingClientRect(), t = table.getBoundingClientRect();
  if (p.left < -1 || p.right > vw + 1) out.push('panel outside the window');
  if (t.left < p.left - 1 || t.right > p.right + 1) out.push('table wider than its panel');
  return out;
}
"""


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("width,theme", SIZES)
def test_export_help_fits_and_reads_at_every_width(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/collection", wait_until="networkidle")
        fold = page.locator("details.csvhelp")
        assert fold.count() == 1 and not fold.evaluate("d => d.open")
        assert page.evaluate(MEASURE) == []
        fold.locator("summary").click()
        assert fold.evaluate("d => d.open")
        rows = fold.locator("tbody tr").all_inner_texts()
        assert len(rows) == 10 and rows[1].split("\t") == ["Name", "Card name"], rows
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(FITS) == []
        texts = "details.csvhelp summary, details.csvhelp p, details.csvhelp th, details.csvhelp td"
        assert page.evaluate(CONTRAST, texts) == []
        # the summary's text stays inside the fold
        assert fold.evaluate(
            "d => { const b = d.getBoundingClientRect();"
            " return [...d.querySelectorAll('summary > span')].filter(s => s.offsetParent)"
            ".every(s => s.getBoundingClientRect().right <= b.right - 1); }"
        )
        # the shared disclosure capsule (Ben, 10-25): its words say what the fold holds at every width
        assert "column types" in fold.locator("summary").inner_text()
        assert "fold-btn" in (fold.locator("summary").get_attribute("class") or "")
        if SHOTS:
            fold.scroll_into_view_if_needed()
            box = fold.bounding_box()
            page.screenshot(  # with the page's margins, so the edges show
                path=str(Path(SHOTS) / f"csvhelp-{width}-{theme}.png"),
                clip={"x": 0, "y": max(0, box["y"] - 16), "width": width, "height": box["height"] + 32},
            )
        assert errors == [], errors
        browser.close()
