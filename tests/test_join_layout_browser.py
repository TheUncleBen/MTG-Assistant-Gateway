"""Layout checks, in headless Chromium: a control that Tab reaches is never
hidden under what stays at the foot of the window (the phone's tab bar, the waiting changes' Apply
bar, the Guide's Back to top), the deck page's View as, Card size, Group by and Sort by boxes never
run over each other at desktop width, and Reports is in the account menu at desktop width. Each at
the normal text size and at 200%. Skipped without a Chromium build."""

from __future__ import annotations

import re

import pytest

from .test_editor_browser import PNG
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

ZOOM = {"none": None, "root": ROOT_200, "text": TEXT_200}

# The focused control against what is pinned to the foot of the window: it must show in full above
# all of it (WCAG 2.4.11). A control inside one of those bars is that bar's own.
FOCUS_AT_FOOT = """() => {
  const el = document.activeElement;
  if (!el || el === document.body || el.closest('.topbar')) return null;
  const r = el.getBoundingClientRect();
  if (!r.width && !r.height) return null;
  const feet = [...document.querySelectorAll('.tabbar, .wbar:not(.inline), .gtop')].filter(f => {
    const s = getComputedStyle(f), b = f.getBoundingClientRect();
    // pinned to the foot (a sticky bar only while it is stuck there), and across from the control
    const pinned = s.position === 'fixed'
      || (s.position === 'sticky' && b.bottom >= innerHeight - (parseFloat(s.bottom) || 0) - 2);
    return s.display !== 'none' && b.height && pinned && b.top > innerHeight / 2 && !f.contains(el)
      && b.left < r.right - 1 && r.left < b.right - 1;
  });
  if (!feet.length) return null;
  const top = Math.min(...feet.map(f => f.getBoundingClientRect().top));
  return {what: (el.outerHTML || '').slice(0, 80), top: r.top, bottom: r.bottom, foot: top,
          win: innerHeight};
}"""


def _context(browser, srv: Server, sid: str, width: int, height: int, touch: bool, script: bool = True):
    ctx = browser.new_context(
        viewport={"width": width, "height": height},
        has_touch=touch,
        is_mobile=touch,
        java_script_enabled=script,
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": srv.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    return ctx


def _exe() -> str:
    """The Chromium build, read before Playwright starts (finding it starts Playwright itself)."""
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _settle(page, script: bool) -> None:
    """topbar.js measures again in a ResizeObserver callback after the zoom: wait two frames."""
    if script:
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    page.wait_for_timeout(50)


@pytest.mark.parametrize("script", [True, False], ids=["script", "noscript"])
@pytest.mark.parametrize("zoom", ["none", "root", "text"])
def test_tab_never_hides_a_control_under_the_bars_at_the_foot(
    server: Server,  # noqa: F811
    zoom: str,
    script: bool,
) -> None:
    """The Proposals page's Apply bar on a phone at 200% text and the Guide's Back to top on a
    folded Fold: the room kept below a focused control follows those bars'
    real height (static/topbar.js), and is 9rem without scripts."""
    from playwright.sync_api import sync_playwright

    exe, (sid, _pending) = _exe(), _seed(server)
    hidden: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        sizes = ((390, 844, True), (344, 882, True), (673, 841, True), (1366, 768, False))
        for width, height, touch in sizes:
            ctx = _context(browser, server, sid, width, height, touch, script)
            page = ctx.new_page()
            for path in ("/proposals", "/guide"):
                page.goto(f"{server.base}{path}", wait_until="load")
                if ZOOM[zoom]:
                    page.evaluate(ZOOM[zoom])
                _settle(page, script)
                seen = 0
                for _ in range(45):
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(20)
                    g = page.evaluate(FOCUS_AT_FOOT)
                    if g is None:
                        continue
                    seen += 1
                    # in full above the bars (a control taller than the room there may run under them)
                    fits = g["bottom"] - g["top"] < g["foot"] - 80
                    if g["bottom"] > g["foot"] + 1 and (fits or g["top"] >= g["foot"] - 1):
                        hidden.append(f"{path} @{width}px {zoom} {'script' if script else 'noscript'}: {g}")
            ctx.close()
        browser.close()
    assert hidden == [], "\n".join(hidden[:20]) + f"\n… {len(hidden)} in all"


# The deck page's view boxes: no two of them (nor their buttons) run over each other, and none
# leaves the toolbar.
VIEW_BOXES = """() => {
  const form = document.querySelector('.toolbar form.views'), f = form.getBoundingClientRect();
  const parts = [...form.querySelectorAll(':scope > .field, :scope > .field select,'
    + ' :scope > .field .msel-btn, :scope > .field .sizeseg')].filter(e => e.getClientRects().length
      && getComputedStyle(e).visibility !== 'hidden')
    .map(e => ({what: (e.id || e.className || e.tagName) + ' ' + (e.textContent || '').trim().slice(0, 16),
                box: e.getBoundingClientRect(), field: e.closest('.field')}));
  const hit = (a, b) => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1
    && b.top < a.bottom - 1;
  const out = [];
  parts.forEach((p, i) => parts.slice(i + 1).forEach(q => {
    if (p.field !== q.field && hit(p.box, q.box)) out.push(`${p.what} / ${q.what}`); }));
  parts.forEach(p => {
    if (p.box.left < f.left - 1 || p.box.right > f.right + 1) out.push(`${p.what} outside`); });
  return out;
}"""


@pytest.mark.parametrize("zoom", ["none", "root", "text"])
def test_the_deck_pages_view_boxes_never_overlap_at_desktop_width(server: Server, zoom: str) -> None:  # noqa: F811
    """With the card size switch in the row, Group by and Sort by ran over each
    other at 1366 px and 200% text."""
    from playwright.sync_api import sync_playwright

    exe, (sid, _pending) = _exe(), _seed(server)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width in (1201, 1280, 1366, 1440, 1600, 1920):
            ctx = _context(browser, server, sid, width, 900, touch=False)
            page = ctx.new_page()
            for view in ("grid", "stacks", "text"):
                page.goto(f"{server.base}/decks/42?view={view}", wait_until="networkidle")
                if ZOOM[zoom]:
                    page.evaluate(ZOOM[zoom])
                page.wait_for_timeout(30)
                problems += [f"{view} @{width}px {zoom}: {x}" for x in page.evaluate(VIEW_BOXES)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems[:30])


def test_reports_is_in_the_account_menu_at_desktop_width(server: Server) -> None:  # noqa: F811
    """With a mouse at desktop width the account menu has Reports (the top bar
    also shows it from 1200 px, under its More below that)."""
    from playwright.sync_api import sync_playwright

    exe, (sid, _pending) = _exe(), _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width in (1000, 1366, 1920):
            ctx = _context(browser, server, sid, width, 800, touch=False)
            page = ctx.new_page()
            page.goto(f"{server.base}/history", wait_until="load")
            page.click("details.dd:not(.navmore) > summary")
            link = page.locator("details.dd:not(.navmore)[open] .menu a[href='/reports']")
            assert link.is_visible(), width
            assert link.inner_text().strip() == "Reports"
            link.click()
            page.wait_for_url(f"{server.base}/reports")
            ctx.close()
        browser.close()
