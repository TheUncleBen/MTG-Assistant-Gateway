"""The Collection page's import result in headless Chromium: a CSV imported through the page's own
form names the row kept as English (Phyrexian), the row added to a copy already held in English
and the row Archidekt could not find; a CSV with a bad quantity is refused with its line number.
At 360, 720 and 1280 px, light and dark, nothing scrolls sideways, nothing is clipped or covered
and the notice keeps a 4.5:1 contrast. Set MTG_IMPORT_SHOTS=<dir> to also save screenshots.
Skipped without a Chromium build."""

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

SHOTS = os.environ.get("MTG_IMPORT_SHOTS")

# The notice inside the window, and every line of it inside the notice.
FITS = """
() => {
  const vw = document.documentElement.clientWidth, out = [];
  const box = document.querySelector('.notice');
  if (!box) return ['no notice'];
  const b = box.getBoundingClientRect();
  if (b.left < -1 || b.right > vw + 1) out.push('notice outside the window');
  for (const el of box.querySelectorAll('p, li')) {
    const r = el.getBoundingClientRect();
    if (r.left < b.left - 1 || r.right > b.right + 1) out.push('line wider than the notice');
  }
  return out;
}
"""

FIRST = "Quantity,Name,Language\n1,Sol Ring,EN\n"
MIXED = "Quantity,Name,Language\n2,Sol Ring,JP\n1,Opt,PH\n1,Foo Bar Not A Card,EN\n1,Forest,\n"
BAD = "Quantity,Name\n1,Opt\nx,Sol Ring\n"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _import(page, server: Server, text: str) -> None:
    page.goto(f"{server.base}/collection", wait_until="networkidle")
    fold = page.locator("details.addfold:not(.csvhelp)")
    if not fold.evaluate("d => d.open"):
        fold.locator("summary").click()
    page.fill("#importtext", text)
    with page.expect_navigation(wait_until="networkidle"):
        page.locator("form.importform button").click()


def _check(page, name: str, width: int, theme: str) -> None:
    assert page.evaluate(MEASURE) == []
    assert page.evaluate(FITS) == []
    assert page.evaluate(CONTRAST, ".notice, .notice p, .notice li") == []
    if SHOTS:
        page.locator(".notice").screenshot(path=str(Path(SHOTS) / f"{name}-{width}-{theme}.png"))
        page.screenshot(path=str(Path(SHOTS) / f"{name}-page-{width}-{theme}.png"))


@pytest.mark.parametrize("width,theme", SIZES)
def test_import_result_reads_at_every_width(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        _import(page, server, FIRST)
        assert page.locator(".notice").inner_text() == "Imported 1 card into your Archidekt collection."
        _import(page, server, MIXED)
        assert "&r=" in page.url and "Foo" not in page.url
        lines = page.locator(".notice li").all_inner_texts()
        assert page.locator(".notice p").inner_text() == "Imported 4 cards into your Archidekt collection."
        assert lines == [
            "Kept as English, because Archidekt has no Phyrexian for collection cards: line 3 (Opt).",
            "Added to a copy you already had in another language, so they count as English: line 2 "
            "(Sol Ring, Japanese added to English).",
            "Not found, so not imported: line 4 (Foo Bar Not A Card).",
        ], lines
        _check(page, "import-result", width, theme)
        _import(page, server, BAD)
        assert page.locator(".notice").inner_text() == (
            "Nothing was imported: line 3: quantity 'x' is not a number."
        )
        _check(page, "import-refused", width, theme)
        assert errors == [], errors
        browser.close()
