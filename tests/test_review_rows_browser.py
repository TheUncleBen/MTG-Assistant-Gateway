"""The review page's change list keeps a row's name readable when what follows it is long text
(Ben, 2026-10-28: a new card package's name stood one letter a line beside its keywords, budget
and formats). The settings wrap in their own column; the name keeps its width. At 375, 840 and 1280
px, light and dark. Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from mtg_gateway import pages, theme

from .test_scan_browser import _chromium_path

pytest.importorskip("playwright")

ROWS = [
    {
        "kind": "new_deck",
        "name": "Fetches and Shocks",
        "cards": 0,
        "private": True,
        "package": "keywords “fetchlands, shocklands, dual lands”, budget $$$, for Commander, Modern, Legacy",
    },
    {"kind": "add", "name": "Sol Ring", "quantity": 1},
]

MEASURE = """
() => [...document.querySelectorAll('ul.changes li')].map(li => {
  const name = li.querySelector('.name').getBoundingClientRect();
  const lh = parseFloat(getComputedStyle(li.querySelector('.name')).lineHeight) || 24;
  return {name: li.querySelector('.name').innerText, w: name.width, lines: Math.round(name.height / lh),
          over: li.scrollWidth - li.clientWidth};
})
"""


@pytest.mark.parametrize("width", [375, 840, 1280])
def test_a_new_packages_name_reads_across_beside_its_settings(width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    items, _counts, _net = pages._change_rows(ROWS, "")
    assert "New card package" in items
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for mode in ("light", "dark"):
            page = browser.new_page(viewport={"width": width, "height": 800})
            page.set_content(
                f"<!doctype html><html lang='en' data-theme='{mode}'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<style>{theme.CSS}</style></head><body><main><section class='panel'>"
                f"<ul class='changes'>{items}</ul></section></main></body></html>"
            )
            rows = page.evaluate(MEASURE)
            package = rows[0]
            assert package["name"] == "Fetches and Shocks"
            assert package["w"] >= 100 and package["lines"] <= 2, (mode, package)
            assert all(r["over"] <= 0 for r in rows), rows
            page.close()
        browser.close()
