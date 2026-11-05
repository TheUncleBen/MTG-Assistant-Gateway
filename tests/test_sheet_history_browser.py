"""The history behind a phone's filter sheet (static/searchpanel.js), in headless Chromium at
360 px with touch. Audit N1-N3 on e0c02add: the sheet's history entry (D4) was wiped by the live
filters of /cards, /decks and Packages (``history.replaceState(null, …)``), so Back closed the
sheet onto the address without the filter while the list kept it, and ×, Escape and the backdrop
left a dead entry; a filter that reloads the page from the sheet (the Binder's tag, a precons set)
left the old page's entry behind, three Backs to leave; and a page reloaded with the sheet open,
or brought Forward onto the sheet's entry, sat on that entry with the sheet closed. The rule now:
every way of closing a sheet leaves the history as it was before the sheet opened, with the
current filter address, and a page on the sheet's entry shows the sheet."""

from __future__ import annotations

import pytest

from .test_cardsearch_browser import _chromium, _page
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_sheet_focus_browser import FREE, SHEETS, WHERE, _open_sheet
from .test_suggest_browser import Server

pytest.importorskip("playwright")

PREV = "/users/alice"

# the sheets whose filters apply as the list is read in place, the address following: a box
# (a colour) on card search and My decks, a drop-down on deck search (its boxes wait for Search)
LIVE = [
    ("cards", "/cards?q=sol", "box"),
    ("decks", "/decks", "box"),
    ("decks-packages", "/decks?show=packages", "box"),
    ("search", "/search", "select"),
]
# the sheets with a filter that sends the form (a new page): the Binder's tag and a precons set
RELOADING = [
    ("binder-filters", "/collection", "#sp-filters select"),
    ("precons", "/precons", "#sp-filters select"),
]

# what the page shows for its filters: the boxes ticked, the drop-downs' choices, the rows listed
# and the button's count
SIG = """() => [...document.querySelectorAll('.spanel input:checked, .spanel select')]
  .map(i => i.name + '=' + i.value).sort().join('&') + ' | ' +
  document.querySelectorAll('main li').length + ' | ' +
  ((document.querySelector('.spanel .sp-open .n') || {}).textContent || '').trim()"""


def _tick(page, kind: str) -> None:  # noqa: ANN001
    """Set a filter in the open sheet: its first shown box, or its first drop-down's second
    choice."""
    if kind == "select":
        # a drop-down that means something on its own (the colour mode and the direction qualify
        # another box, and the page draws them from that box on a reload)
        page.locator("#sp-filters select:not([name=cm]):not([name=dir])").first.select_option(index=1)
        return
    boxes = page.evaluate(
        "() => [...document.querySelectorAll('#sp-filters input[type=checkbox]')]"
        ".map((e, i) => e.getClientRects().length && !e.closest('details:not([open])') ? i : -1)"
        ".filter(i => i >= 0)"
    )
    page.locator("#sp-filters input[type=checkbox]").nth(boxes[0]).check()


def _closed_on(page, here: str, name: str, way: str) -> None:  # noqa: ANN001
    page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
    assert page.url == here, (name, way, page.url)
    got = page.evaluate(FREE)
    assert got["inert"] == 0 and got["role"] is None and got["scrim"] == 0, (name, way, got)
    page.wait_for_function("() => !(history.state && history.state.spsheet)")


@pytest.mark.parametrize("way", ["back", "escape", "x", "scrim"])
@pytest.mark.parametrize("name,path,kind", LIVE)
def test_a_filter_set_in_the_sheet_keeps_its_address_whichever_way_the_sheet_closes(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    kind: str,
    way: str,
) -> None:
    """N1: a box ticked in the open sheet changes the address in place; closing the sheet by
    Back, Escape, × or the backdrop keeps that address (a reload shows the same list, boxes and
    count), leaves the page on its own entry, and one more Back leaves the page."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        page.goto(f"{server.base}{PREV}", wait_until="networkidle")
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        here = page.url
        _open_sheet(page)
        _tick(page, kind)
        page.wait_for_url(lambda u: u != here)
        # the list follows (the address is set as the ask goes out): settled before it is read
        page.wait_for_load_state("networkidle")
        page.wait_for_function("() => !document.querySelector('[aria-busy=true]')")
        filtered = page.url
        assert page.locator("#sp-filters.open").count() == 1, (name, "the sheet stays while filtering")
        sig = page.evaluate(SIG)
        if way == "back":
            page.go_back()
        elif way == "escape":
            page.keyboard.press("Escape")
        elif way == "x":
            page.locator("#sp-filters .sp-sheet-head .close").click()
        else:
            page.locator(".sp-scrim").dispatch_event("click")
        _closed_on(page, filtered, name, way)
        page.reload(wait_until="networkidle")
        page.wait_for_function("() => !document.querySelector('[aria-busy=true]')")
        assert page.url == filtered, (name, way, page.url)
        assert page.locator("#sp-filters.open").count() == 0
        assert page.evaluate(SIG) == sig, (name, way, page.evaluate(SIG), sig)
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("name,path,select", RELOADING)
def test_a_filter_that_reloads_the_page_from_the_sheet_leaves_no_entry_behind(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    select: str,
) -> None:
    """N2: a drop-down picked in the open sheet sends the form; the new page is one Back from
    the page the sheet was opened on, and that page one Back from the one before it: two
    presses to leave, as without a sheet."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    server.ark.collection_tag_lists["alice"] = {
        21: {"id": 21, "name": "trade binder page 12", "color": "#d33"}
    }
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        page.goto(f"{server.base}{PREV}", wait_until="networkidle")
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        here = page.url
        _open_sheet(page)
        page.locator(select).first.select_option(index=1)
        page.wait_for_url(lambda u: u != here)
        page.wait_for_load_state("networkidle")
        assert page.locator("#sp-filters.open").count() == 0, (name, "a new page")
        assert page.evaluate("() => !(history.state && history.state.spsheet)")
        page.go_back()
        page.wait_for_url(here)
        page.wait_for_load_state("networkidle")
        got = page.evaluate(FREE)
        assert got["open"] is False and got["inert"] == 0, (name, got)
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("name,path,tags", SHEETS)
def test_a_page_on_the_sheets_entry_shows_the_sheet(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    tags: bool,
) -> None:
    """N3: Forward onto the sheet's entry after a Back, and a reload with the sheet open (the
    app's pull to refresh), show the sheet again, so the next Back closes it (on the page, in the
    same document) and the one after leaves the page; no entry is ever a dead press."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    server.ark.collection_tag_lists["alice"] = (
        {21: {"id": 21, "name": "trade binder page 12", "color": "#d33"}} if tags else {}
    )
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        # Back, then Forward
        page.goto(f"{server.base}{PREV}", wait_until="networkidle")
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        here = page.url
        _open_sheet(page)
        page.go_back()
        _closed_on(page, here, name, "back")
        page.go_forward()
        page.locator("#sp-filters.open").wait_for()
        assert page.evaluate(WHERE) == "sheet", (name, page.evaluate(WHERE))
        assert page.evaluate("() => !!(history.state && history.state.spsheet)")
        page.go_back()
        _closed_on(page, here, name, "back again")
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        # a reload with the sheet open
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        _open_sheet(page)
        page.reload(wait_until="networkidle")
        page.locator("#sp-filters.open").wait_for()
        assert page.evaluate(WHERE) == "sheet", (name, page.evaluate(WHERE))
        page.go_back()
        _closed_on(page, here, name, "back after a reload")
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("name,path,kind", LIVE)
def test_a_filter_set_after_a_reload_with_the_sheet_open_stays_in_the_address(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    kind: str,
) -> None:
    """N3, the reload case as the normal one: a filter set in the sheet after a reload with it
    open is kept by Back (the sheet closes onto the filtered address, a reload shows the same
    list) and one more Back leaves the page. Chromium (and so the app's WebView) goes back from
    the reloaded entry to the page's own entry under it within the same document, which is what
    carries the address: the page's script must still be the one that was running."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        page.goto(f"{server.base}{PREV}", wait_until="networkidle")
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        here = page.url
        _open_sheet(page)
        page.reload(wait_until="networkidle")
        page.locator("#sp-filters.open").wait_for()
        page.wait_for_load_state("networkidle")
        assert page.url == here, (name, page.url)
        _tick(page, kind)
        page.wait_for_url(lambda u: u != here)
        page.wait_for_load_state("networkidle")
        page.wait_for_function("() => !document.querySelector('[aria-busy=true]')")
        filtered = page.url
        sig = page.evaluate(SIG)
        page.evaluate("() => { window.sameDocument = true; }")
        page.go_back()
        _closed_on(page, filtered, name, "back")
        assert page.evaluate("() => window.sameDocument === true"), (name, "Back loaded the page anew")
        page.reload(wait_until="networkidle")
        page.wait_for_function("() => !document.querySelector('[aria-busy=true]')")
        assert page.url == filtered and page.evaluate(SIG) == sig, (name, page.url, page.evaluate(SIG), sig)
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == []
        browser.close()
