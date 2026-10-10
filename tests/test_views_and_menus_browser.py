"""View as is remembered, and short menu items keep one line, in headless Chromium.

Owner test round 0.7.10: a deck switched to Stacks went back to Text when it was opened again,
and the deck list went back from List to Grid. The choice is now kept per browser (a cookie, as
the theme is), so the page renders the chosen view straight away, with no flash of the default.

Owner backlog: short menu items wrapped (the panel hanging from a small button shrank to its
min-width). Every item that fits on one line within the panel's cap keeps one line, every
panel stays inside the window, and a long item wraps instead of widening the panel past it.

The deck's name is hostile (long, unbroken, right-to-left text and emoji). Skipped without
Chromium."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mtg_gateway.archidekt_csv import parse_export, to_deck_json

from .test_editor_browser import PNG, _link
from .test_editor_leave_browser import HOSTILE
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

DECK = 51


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_deck.csv"
    cards = parse_export(fixture.read_text(encoding="utf-8"))
    s.ark.decks[DECK] = to_deck_json(cards[:14], deck_id=DECK, name=HOSTILE, owner="alice")
    s.ark.users["alice"]["decks"].append(DECK)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _start(server: Server, width: int, *, touch: bool = False):
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    p = sync_playwright().start()
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=touch, is_mobile=touch)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return p, browser, page, errors


def _pick(page, select_id: str, label: str) -> None:
    """Choose ``label`` in the themed View as dropdown (the list opens from its button)."""
    page.locator(f".field:has(#{select_id}) .msel-btn").click()
    with page.expect_navigation(wait_until="networkidle"):
        page.locator("[role=listbox] [role=option]", has_text=label).first.click()


def _nav(page, href: str) -> None:
    """Follow the site's own link to ``href`` (tab bar on a phone, top bar on a desktop)."""
    link = page.locator(f".tabbar > a[href='{href}']:visible, .topbar nav.site a[href='{href}']:visible")
    link = link.first
    with page.expect_navigation(wait_until="networkidle"):
        link.click()


@pytest.mark.parametrize("width,touch", [(360, True), (1366, False)])
def test_view_as_is_remembered_after_leaving_and_coming_back(server: Server, width: int, touch: bool) -> None:
    p, browser, page, errors = _start(server, width, touch=touch)
    try:
        # the deck page: Text by default, Stacks picked, then away to the deck list and back in
        page.goto(f"{server.base}/decks/{DECK}", wait_until="networkidle")
        assert page.locator("#cards.deckview.text").count() == 1
        _pick(page, "f-view", "Stacks")
        assert "view=stacks" in page.url and page.locator("#cards.deckview.stacks").count() == 1
        _nav(page, "/decks")
        with page.expect_navigation(wait_until="networkidle"):
            page.locator(f"a[href='/decks/{DECK}']").first.click()
        assert page.url == f"{server.base}/decks/{DECK}", page.url
        assert page.locator("#cards.deckview.stacks").count() == 1, "the deck went back to Text"
        assert page.locator(".field:has(#f-view) .msel-btn").inner_text().strip() == "Stacks"
        # rendered so by the gateway: the first paint already has it (no flash of Text)
        first = page.request.get(f"{server.base}/decks/{DECK}").text()
        assert "class='deckview stacks'" in first and "<option value='stacks' selected>" in first

        # the deck list: Grid by default, List picked, then away (Collection) and back (Decks)
        _nav(page, "/decks")
        assert page.locator("ul.decklist.grid").count() == 1
        _pick(page, "f-view", "List")
        assert page.locator("ul.decklist.list").count() == 1
        _nav(page, "/collection")
        _nav(page, "/decks")
        assert page.url == f"{server.base}/decks", page.url
        assert page.locator("ul.decklist.list").count() == 1, "the deck list went back to Grid"
        assert "<option value='list' selected>" in page.request.get(f"{server.base}/decks").text()
        # the deck page keeps its own choice: the two do not share one setting
        page.goto(f"{server.base}/decks/{DECK}", wait_until="networkidle")
        assert page.locator("#cards.deckview.stacks").count() == 1

        # the collection: List picked, away and back
        _nav(page, "/collection")
        _pick(page, "f-view", "List")
        _nav(page, "/decks")
        _nav(page, "/collection")
        assert page.url == f"{server.base}/collection" and page.locator("#f-view").input_value() == "list"
        assert "<option value='list' selected>" in page.request.get(f"{server.base}/collection").text()
        # a view named in the address still wins, and becomes the remembered one
        page.goto(f"{server.base}/decks/{DECK}?view=grid", wait_until="networkidle")
        page.goto(f"{server.base}/decks/{DECK}", wait_until="networkidle")
        assert page.locator("#cards.deckview.grid").count() == 1
        # an unknown view in the address is ignored, the remembered one kept
        page.goto(f"{server.base}/decks/{DECK}?view=bogus", wait_until="networkidle")
        assert page.locator("#cards.deckview.grid").count() == 1
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        assert errors == []
    finally:
        browser.close()
        p.stop()


# Every open menu's panel against the window, and each item in it: one line when it fits on one
# line within the panel's cap. Returns the problems found.
MENU_ITEMS = """
() => {
  const out = [];
  const vw = document.documentElement.clientWidth;
  for (const d of document.querySelectorAll('details.dd[open]')) {
    const m = d.querySelector(':scope > .menu');
    if (!m) continue;
    const r = m.getBoundingClientRect();
    if (r.left < 0 || r.right > vw + .5)
      out.push('panel outside the window: ' + Math.round(r.left) + '..' + Math.round(r.right));
    if (m.scrollWidth > m.clientWidth + 1) out.push('panel scrolls sideways');
    const cap = parseFloat(getComputedStyle(m).maxWidth);
    const items = m.querySelectorAll(':scope > a, :scope > button, :scope > form > button, :scope > .item');
    for (const it of items) {
      const h = it.getBoundingClientRect().height;
      if (!h) continue;
      const ws = it.style.whiteSpace;
      it.style.whiteSpace = 'nowrap';
      const oneLine = it.getBoundingClientRect().height, natural = it.scrollWidth;
      it.style.whiteSpace = ws;
      const pad = m.offsetWidth - m.clientWidth;
      if (natural + pad <= cap && h > oneLine + .5)
        out.push('"' + it.textContent.trim().slice(0, 40) + '" wraps (' + Math.round(h) + ' > '
          + Math.round(oneLine) + 'px)');
    }
  }
  return out;
}
"""


@pytest.mark.parametrize("width,touch", [(360, True), (720, True), (1366, False)])
def test_short_menu_items_keep_one_line(server: Server, width: int, touch: bool) -> None:
    p, browser, page, errors = _start(server, width, touch=touch)
    try:
        problems: list[str] = []
        for path in (f"/decks/{DECK}", "/decks", "/collection?view=list"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            summaries = page.locator("details.dd > summary")
            for i in range(summaries.count()):
                s = summaries.nth(i)
                if not s.is_visible():
                    continue
                s.scroll_into_view_if_needed()
                s.click()
                page.wait_for_timeout(50)
                label = s.get_attribute("aria-label") or s.inner_text().strip()[:30]
                problems += [f"{path} [{label}]: {m}" for m in page.evaluate(MENU_ITEMS)]
                page.keyboard.press("Escape")
                # closed, and on a phone the menu's history entry given back (feedback.js)
                page.wait_for_function(
                    "() => !document.querySelector('details.dd[open]')"
                    " && !(history.state && history.state.menu)"
                )
        assert problems == [], "\n".join(problems)
        assert errors == []
    finally:
        browser.close()
        p.stop()
