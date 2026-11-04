"""The phone's filter and View sheets (searchpanel.filters_sheet, static/searchpanel.js) hold the
keyboard, in headless Chromium. Audit C3 on a62955eb: Tab walked out of every open sheet onto the
page under the backdrop (15 to 26 of 30 presses), there was no dialog role, aria-modal or inert,
and on /cards Escape stopped closing the sheet once a picker's list had been left open behind the
focus."""

from __future__ import annotations

import pytest

from .test_cardsearch_browser import _chromium, _page
from .test_overflow_sweep_browser import MEASURE, _seed, server  # noqa: F401
from .test_suggest_browser import Server

pytest.importorskip("playwright")

# the six sheets: the Binder's View (no tags to filter by) and Filters (with tags), card search,
# My decks, deck search and the precons
SHEETS = [
    ("binder-view", "/collection", False),
    ("binder-filters", "/collection", True),
    ("cards", "/cards", False),
    ("decks", "/decks", False),
    ("search", "/search", False),
    ("precons", "/precons", False),
]

# where the focus is: inside the open sheet, on the page's body (nowhere) or on the page behind
WHERE = """() => {
  const a = document.activeElement, s = document.getElementById('sp-filters');
  if (!a || a === document.body) return 'body';
  return s && s.contains(a) ? 'sheet' : 'page: ' + a.tagName.toLowerCase() + ' "' +
    (a.getAttribute('aria-label') || a.textContent || a.value || '').trim().slice(0, 30) + '"';
}"""

# the sheet's dialog semantics, and its accessible name as a screen reader would read it
DIALOG = """() => {
  const s = document.getElementById('sp-filters');
  const by = s.getAttribute('aria-labelledby');
  const name = s.getAttribute('aria-label') || (by && [...by.split(/\\s+/)]
    .map(id => (document.getElementById(id) || {}).textContent || '').join(' ')) || '';
  return {role: s.getAttribute('role'), modal: s.getAttribute('aria-modal'), name: name.trim()};
}"""

# a control on the page behind the sheet refuses the focus (the page is inert)
BEHIND = """() => {
  const s = document.getElementById('sp-filters');
  const c = [...document.querySelectorAll('a[href], button, input, select')]
    .find(e => !s.contains(e) && e.getClientRects().length && !e.closest('.sp-scrim'));
  if (!c) return 'no control behind the sheet';
  c.focus();
  return document.activeElement === c ? 'focused ' + c.tagName.toLowerCase() + ' "' +
    (c.getAttribute('aria-label') || c.textContent || '').trim().slice(0, 30) + '"' : 'refused';
}"""


def _escape_closes(page, opener) -> None:  # noqa: ANN001
    """Escape closes the sheet from wherever the focus is in it: a picker's open list takes the
    first press for itself (it closes), and the next closes the sheet."""
    expanded = page.evaluate("() => document.activeElement.getAttribute('aria-expanded') === 'true'")
    page.keyboard.press("Escape")
    if expanded:
        page.wait_for_function("() => document.activeElement.getAttribute('aria-expanded') !== 'true'")
        page.keyboard.press("Escape")
    page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
    assert page.locator(".sp-scrim").count() == 0
    assert opener.evaluate("e => e === document.activeElement"), page.evaluate(WHERE)


@pytest.mark.parametrize("width,theme", [(360, "light"), (412, "dark")])
@pytest.mark.parametrize("name,path,tags", SHEETS)
def test_an_open_sheet_keeps_the_keyboard_and_escape_closes_it_from_anywhere(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    tags: bool,
    width: int,
    theme: str,
) -> None:
    """Audit C3: an open sheet is a modal dialog with the sheet's title as its name; 30 Tab and 30
    Shift+Tab presses keep the focus inside it, the page behind it refuses the focus, Escape
    closes it from wherever the focus is, and the focus goes back to the button that opened it.
    On /cards a picker's list left open behind the focus no longer swallows Escape."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    server.ark.collection_tag_lists["alice"] = (
        {21: {"id": 21, "name": "trade binder page 12", "color": "#d33"}} if tags else {}
    )
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        opener = page.locator(".spanel .sp-open")
        title = opener.inner_text().strip().split("\n")[0].strip()
        assert title in ("View", "Filters"), (name, title)

        def open_sheet() -> None:
            opener.click()
            page.locator("#sp-filters.open").wait_for()
            assert page.evaluate(WHERE) == "sheet"

        open_sheet()
        got = page.evaluate(DIALOG)
        assert got["role"] == "dialog" and got["modal"] == "true", (name, got)
        assert got["name"] == title, (name, got)
        assert page.evaluate(BEHIND) == "refused", (name, page.evaluate(BEHIND))
        page.locator("#sp-filters .sp-sheet-head .close").focus()
        stops: list[str] = []
        for key in ("Tab",) * 30 + ("Shift+Tab",) * 30:
            page.keyboard.press(key)
            stops.append(page.evaluate(WHERE))
        outside = [(i, s) for i, s in enumerate(stops) if s != "sheet"]
        assert outside == [], (name, outside)
        assert len(set(stops)) == 1, stops
        _escape_closes(page, opener)
        # from the last control, past the sheet's end and back to its first, then Escape
        open_sheet()
        page.evaluate(
            "() => { const f = [...document.querySelectorAll('#sp-filters button, #sp-filters input, "
            "#sp-filters select, #sp-filters a[href]')].filter(e => e.getClientRects().length"
            " && !e.closest('details:not([open])')); f[f.length - 1].focus(); }"
        )
        page.keyboard.press("Tab")
        assert page.evaluate(WHERE) == "sheet"
        _escape_closes(page, opener)
        # a picker's list opened by the focus and left behind it (card search): Escape still closes
        # the sheet
        pickers = page.locator("#sp-filters [data-picker] input[type=text]")
        open_sheet()
        shown = page.evaluate(  # the pickers in the sheet's groups, not those folded under More filters
            "() => [...document.querySelectorAll('#sp-filters [data-picker] input[type=text]')]"
            ".map((e, i) => e.getClientRects().length && !e.closest('details:not([open])') ? i : -1)"
            ".filter(i => i >= 0)"
        )
        assert (len(shown) >= 1) == (path == "/cards"), (name, shown)
        _escape_closes(page, opener)
        for i in shown:
            open_sheet()
            pickers.nth(i).focus()
            page.wait_for_function(
                "i => document.querySelectorAll('#sp-filters [data-picker] input[type=text]')[i]"
                ".getAttribute('aria-expanded') === 'true'",
                arg=i,
            )
            page.keyboard.press("Tab")
            page.wait_for_timeout(250)
            assert page.evaluate(WHERE) == "sheet"
            _escape_closes(page, opener)
        # closed: the page has its controls back, no dialog role is left on the groups
        assert (
            page.evaluate(
                "() => [...document.querySelectorAll('[inert]')].length + "
                "(document.getElementById('sp-filters').getAttribute('role') ? 1 : 0)"
            )
            == 0
        )
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()
