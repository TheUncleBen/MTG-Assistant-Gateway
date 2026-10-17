"""R-094 / T-058: large text. With the text at twice its size (the root font size at 200%, and
every element's own font size doubled, as a phone's font scale or a browser's text-only zoom
does it) the main pages at phone width (360 px) and on a Fold's inner screen (673 px) must not
scroll sideways, clip a control or let two controls overlap, and every dropdown menu still opens
inside the window. The same checks as the overflow sweep (test_overflow_sweep_browser), on
the same hostile data. Skipped without a Chromium build."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from .test_editor_browser import PNG
from .test_overflow_sweep_browser import FOLDERS, MEASURE, MENUS, _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

WIDTHS = [360, 673, 717]

# The pages a member uses most, plus the ones with forms and tables.
PAGES = [
    "/",
    "/decks",
    "/decks/42",
    "/decks/42?view=grid",
    "/decks/42?view=table",
    "/decks/50",
    "/decks/42/edit",
    "/decks/42/settings",
    "/decks/new",
    "/decks/42/export",
    "/decks/42/compare",
    f"/decks?folder={FOLDERS[0]}",
    "/folders",
    "/collection?view=grid",
    "/collection?view=list",
    "/search",
    "/precons",
    "/users/alice",
    "/history",
    "/history/reports/r1",
    "/activity",
    "/proposals",
    "/account",
    "/guide",
    "/scan",
    "/app",
    "/install",
    "/admin",
]

# Root font size at 200% (the pages' own 14px doubled): everything sized in rem doubles.
ROOT_200 = "() => { document.documentElement.style.setProperty('font-size', '28px', 'important'); }"

# Text-only zoom: every element's computed font size doubled (read all first, then set), the way
# Android's font scale (WebView textZoom) and a browser's text-only zoom enlarge text in px too.
TEXT_200 = """
() => {
  const els = [document.documentElement, ...document.querySelectorAll('body, body *')];
  const sizes = els.map(el => parseFloat(getComputedStyle(el).fontSize));
  els.forEach((el, i) => {
    if (sizes[i]) el.style.setProperty('font-size', (sizes[i] * 2) + 'px', 'important');
  });
}
"""


@pytest.mark.parametrize("zoom", ["root", "text"])
def test_main_pages_hold_together_at_200_percent_text(server: Server, zoom: str) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 360, "height": 800}, has_touch=True, is_mobile=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for path in [*PAGES, f"/proposals/{pending}"]:
            for w in WIDTHS:
                ctx.clear_cookies(name=re.compile(r"^mtg_view_"))
                server.db.delete_prefs("user-1", prefixes=("view.",))  # on the account from 0.7.29
                page.set_viewport_size({"width": w, "height": 800})
                r = page.goto(f"{server.base}{path}", wait_until="networkidle")
                assert r is not None and r.status == 200, (path, r and r.status)
                page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
                page.wait_for_timeout(30)
                found = page.evaluate(MEASURE) + page.evaluate(MENUS)
                problems += [f"{path} @{w}px {zoom}: {f}" for f in found]
        browser.close()
    if os.environ.get("SWEEP_OUT"):
        Path(os.environ["SWEEP_OUT"]).write_text("\n".join(problems))
    assert errors == []
    more = f"\n… {len(problems)} in all" if len(problems) > 80 else ""
    assert problems == [], "\n".join(problems[:80]) + more
