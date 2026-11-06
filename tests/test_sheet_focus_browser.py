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
from .waits import frame, redrawn

pytest.importorskip("playwright")

# the seven sheets: the Binder's View (no tags to filter by) and Filters (with tags), card search,
# My decks and its Packages view (deckpage.deck_list_controls_html, the one sheet in two states),
# deck search and the precons
SHEETS = [
    ("binder-view", "/collection", False),
    ("binder-filters", "/collection", True),
    ("cards", "/cards", False),
    ("decks", "/decks", False),
    ("decks-packages", "/decks?show=packages", False),
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
            page.wait_for_function(f"() => ({WHERE})() === 'sheet'")
            frame(page)
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


# the page once the sheet has gone: what is left inert, the groups' dialog role and backdrop,
# the scroll lock, and whether a control on the page takes the focus (given back afterwards)
FREE = """() => {
  const s = document.getElementById('sp-filters');
  const c = [...document.querySelectorAll('header a[href], main a[href], main button, main input')]
    .find(e => !s.contains(e) && e.getClientRects().length && !e.closest('.sp-scrim'));
  const was = document.activeElement;
  c.focus();
  const took = document.activeElement === c;
  if (was && was !== document.body) was.focus(); else c.blur();
  return {inert: document.querySelectorAll('[inert]').length, role: s.getAttribute('role'),
    modal: s.getAttribute('aria-modal'), open: s.classList.contains('open'),
    scrim: document.querySelectorAll('.sp-scrim').length,
    lock: document.documentElement.classList.contains('sp-sheet-open'),
    overflow: getComputedStyle(document.documentElement).overflow, took};
}"""


def _open_sheet(page) -> None:  # noqa: ANN001
    page.locator(".spanel .sp-open").click()
    page.locator("#sp-filters.open").wait_for()
    assert page.evaluate(WHERE) == "sheet"


@pytest.mark.parametrize("start", [360, 1280])
@pytest.mark.parametrize("name,path,tags", SHEETS)
def test_a_sheet_open_in_a_window_that_grows_past_phone_width_lets_the_page_go(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    tags: bool,
    start: int,
) -> None:
    """Audit D1 on 099f600a: a sheet opened under 600 px and the window widened past the sheet's
    media query (a Fold unfolding, a desktop window resized) left the page inert and scroll-locked
    behind a sheet that no longer showed, on five of the six sheets. Now the sheet closes with the
    width: nothing stays inert, the groups lose their dialog role and backdrop, the page scrolls
    and takes the focus, the focus lands on a control of the panel, and narrowing the window again
    does not bring the sheet back. On a phone (touch) and in a mouse window shrunk to 360 px."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    server.ark.collection_tag_lists["alice"] = (
        {21: {"id": 21, "name": "trade binder page 12", "color": "#d33"}} if tags else {}
    )
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, start, "light", exe)
        page.set_viewport_size({"width": 360, "height": 860})
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        _open_sheet(page)
        page.set_viewport_size({"width": 1280, "height": 860})
        page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
        got = page.evaluate(FREE)
        assert got == {
            "inert": 0,
            "role": None,
            "modal": None,
            "open": False,
            "scrim": 0,
            "lock": False,
            "overflow": "visible",
            "took": True,
        }, (name, start, got)
        where = page.evaluate(WHERE)
        assert where != "body" and not where.startswith("page: a"), (name, start, where)
        # a click reaches the page: the name box takes it
        page.locator(".spanel .sp-name input").first.click()
        assert page.evaluate(WHERE).startswith("page: input"), (name, start, page.evaluate(WHERE))
        page.set_viewport_size({"width": 360, "height": 860})
        redrawn(page)
        got = page.evaluate(FREE)
        assert got["open"] is False and got["inert"] == 0 and got["lock"] is False, (name, got)
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()


@pytest.mark.parametrize("name,path,tags", SHEETS)
def test_back_closes_an_open_sheet_and_no_way_of_closing_it_leaves_an_entry_behind(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    tags: bool,
) -> None:
    """Audit D4 on 099f600a: the sheet pushed no history entry, so Android's Back key left the
    page with the sheet open. Now, as the boxes and menus do, an open sheet holds one entry: Back
    closes it (the page stays, the focus goes back to the button), and closing it by ×, Escape or
    its backdrop gives the entry back, so the next Back leaves the page. A filter set inside the
    sheet: tests/test_sheet_history_browser.py."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    server.ark.collection_tag_lists["alice"] = (
        {21: {"id": 21, "name": "trade binder page 12", "color": "#d33"}} if tags else {}
    )
    prev = f"{server.base}/users/alice"
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        for way in ("back", "escape", "x", "scrim"):
            page.goto(prev, wait_until="networkidle")
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            here = page.url
            _open_sheet(page)
            if way == "back":
                page.go_back()
            elif way == "escape":
                page.keyboard.press("Escape")
            elif way == "x":
                page.locator("#sp-filters .sp-sheet-head .close").click()
            else:
                page.locator(".sp-scrim").dispatch_event("click")
            page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
            assert page.url == here, (name, way, page.url)
            got = page.evaluate(FREE)
            assert got["inert"] == 0 and got["role"] is None and got["scrim"] == 0, (name, way, got)
            opener = page.locator(".spanel .sp-open")
            assert opener.evaluate("e => e === document.activeElement"), (name, way, page.evaluate(WHERE))
            # the sheet's entry is given back: this page's own entry is the one left
            page.wait_for_function("() => history.state === null")
            page.go_back()
            page.wait_for_url(prev)
        assert errors == []
        browser.close()
