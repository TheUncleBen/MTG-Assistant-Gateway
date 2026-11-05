"""The history behind a phone's filter sheet (static/searchpanel.js), in headless Chromium at
360 px with touch: every sheet (tests/test_sheet_focus_browser.py SHEETS) × every way of closing it
(Back, Escape, ×, the backdrop, the window widened past phone width, Show results, Clear filters)
× Forward and a reload.

Audit N1-N3 on e0c02add: the sheet's history entry (D4) was wiped by the live filters of /cards,
/decks and Packages, so Back closed the sheet onto the address without the filter; a filter that
reloads the page from the sheet left the old page's entry behind. Audit H1, L1, L2 and the
drop-down row on 71911eba: deck search's Show results threw the filters away (the search went
while the sheet's own back() was on its way, and that back's popstate redrew the page from the old
address); Back after Clear filters brought the sheet back and leaving took three presses; Forward
after ×, Escape or the backdrop reopened the sheet; and Back after a drop-down sent the form
showed the sent choice over the unfiltered list.

The rule: every way of closing a sheet leaves the history as it was before the sheet opened, with
the current filter address; what Show or Clear filters does from the sheet is one entry over
that, as it is without a sheet; Forward brings the sheet back only after a Back closed it. A page
reloaded with the sheet open shows the page, not the sheet, on a plain entry: reopening it on load
and closing it with Back would be a fresh page load in Safari (the iPhone) and Firefox, which lose
a filter set in the reopened sheet; the price is one Back that stays on the page."""

from __future__ import annotations

import pytest

from .test_cardsearch_browser import _chromium, _page
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_sheet_focus_browser import FREE, SHEETS, WHERE, _open_sheet
from .test_suggest_browser import Server

pytest.importorskip("playwright")

PREV = "/users/alice"
WAYS = ["back", "escape", "x", "scrim", "widen"]

# the sheets whose filters apply as the list is read in place, the address following: a box
# (a colour) on card search and My decks, a drop-down on deck search (its boxes wait for Search)
LIVE = [
    ("cards", "/cards?q=sol", "box"),
    ("decks", "/decks", "box"),
    ("decks-packages", "/decks?show=packages", "box"),
    ("search", "/search", "select"),
]
LIVE_KIND = {name: kind for name, _path, kind in LIVE}
# the sheets with a filter that sends the form (a new page): the Binder's tag and a precons set
RELOADING = [
    ("binder-filters", "/collection", "#sp-filters select"),
    ("precons", "/precons", "#sp-filters select"),
]
# Show in every sheet, with each kind of filter the sheet holds (None: a sheet whose only filter
# sends the form by itself, or none), and where one Back lands afterwards: "prev" where the
# filter went into the page's own address, "here" where Show itself asked for the page or the
# search, which is its own entry over the page's, as a Search press is without a sheet
SHOWS = [
    ("binder-view", "/collection", False, None, "here"),
    ("binder-filters", "/collection", True, None, "here"),
    ("cards", "/cards?q=sol", False, "box", "prev"),
    ("cards-text", "/cards?q=sol", False, "text:o", "prev"),
    ("decks", "/decks", False, "box", "prev"),
    ("decks-packages", "/decks?show=packages", False, "box", "prev"),
    ("search-box", "/search", False, "box", "here"),
    ("search-text", "/search", False, "text:commander", "here"),
    ("search-select", "/search", False, "select", "prev"),
    ("precons", "/precons", False, None, "here"),
]
# Clear filters as a link in the sheet, after a filter in the address: a box (My decks), a
# drop-down (deck search: its boxes wait for Search), a set (precons: the set drop-down sends the
# form by itself)
CLEARS = [
    ("decks", "/decks", "box"),
    ("decks-packages", "/decks?show=packages", "box"),
    ("search", "/search", "select"),
    ("precons", "/precons", "set"),
]

# what the page shows for its filters: the boxes ticked, the drop-downs' choices, the text typed,
# the rows listed and the button's count
SIG = """() => [...document.querySelectorAll(
    '.spanel input:checked, .spanel select, .spanel input[type=text]')]
  .filter(i => i.name && !i.closest('.sortcw')).map(i => i.name + '=' + i.value).sort().join('&') + ' | ' +
  document.querySelectorAll('main li').length + ' | ' +
  ((document.querySelector('.spanel .sp-open .n') || {}).textContent || '').trim()"""
# the form's drop-downs alone
FORM = """() => [...document.querySelectorAll('.spanel select')].filter(s => s.name && !s.closest('.sortcw'))
  .map(s => s.name + '=' + s.value).sort().join('&')"""
PLAIN = "() => !(history.state && history.state.spsheet)"


def _settle(page) -> None:  # noqa: ANN001
    page.wait_for_load_state("networkidle")
    page.wait_for_function("() => !document.querySelector('[aria-busy=true]')")


def _arrive(page, base: str, path: str) -> str:  # noqa: ANN001
    """The page one entry after PREV, settled; its address."""
    page.goto(f"{base}{PREV}", wait_until="networkidle")
    page.goto(f"{base}{path}", wait_until="networkidle")
    _settle(page)
    return page.url


def _tick(page, kind: str) -> None:  # noqa: ANN001
    """Set a filter in the open sheet: its first shown box, its first drop-down's second choice,
    or a word in a text box (``text:<name>``)."""
    if kind == "select":
        # a drop-down that means something on its own (the colour mode and the direction qualify
        # another box, and the page draws them from that box on a reload)
        page.locator("#sp-filters select:not([name=cm]):not([name=dir])").first.select_option(index=1)
        return
    if kind.startswith("text:"):
        box = page.locator(f"#sp-filters input[name={kind[5:]}]")
        # a box behind More filters: its fold opens first
        box.evaluate(
            "e => { for (let d = e.closest('details'); d; d = d.parentElement.closest('details')) "
            "d.open = true; }"
        )
        box.fill("sol")
        return
    boxes = page.evaluate(
        "() => [...document.querySelectorAll('#sp-filters input[type=checkbox]')]"
        ".map((e, i) => e.getClientRects().length && !e.closest('details:not([open])') ? i : -1)"
        ".filter(i => i >= 0)"
    )
    page.locator("#sp-filters input[type=checkbox]").nth(boxes[0]).check()


def _close(page, way: str) -> None:  # noqa: ANN001
    if way == "back":
        page.go_back()
    elif way == "escape":
        page.keyboard.press("Escape")
    elif way == "x":
        page.locator("#sp-filters .sp-sheet-head .close").click()
    elif way == "scrim":
        page.locator(".sp-scrim").dispatch_event("click")
    else:
        page.set_viewport_size({"width": 1280, "height": 860})


def _closed_on(page, here: str, name: str, way: str) -> None:  # noqa: ANN001
    page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
    assert page.url == here, (name, way, page.url)
    got = page.evaluate(FREE)
    assert got["inert"] == 0 and got["role"] is None and got["scrim"] == 0, (name, way, got)
    page.wait_for_function(PLAIN)


@pytest.mark.parametrize("way", WAYS)
@pytest.mark.parametrize("name,path,kind", LIVE)
def test_a_filter_set_in_the_sheet_keeps_its_address_whichever_way_the_sheet_closes(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    kind: str,
    way: str,
) -> None:
    """N1: a box ticked in the open sheet changes the address in place; closing the sheet by
    Back, Escape, ×, the backdrop or the window widening keeps that address (a reload shows the
    same list, boxes and count), leaves the page on its own entry, and one more Back leaves the
    page."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        here = _arrive(page, server.base, path)
        _open_sheet(page)
        _tick(page, kind)
        page.wait_for_url(lambda u: u != here)
        # the list follows (the address is set as the ask goes out): settled before it is read
        _settle(page)
        filtered = page.url
        assert page.locator("#sp-filters.open").count() == 1, (name, "the sheet stays while filtering")
        sig = page.evaluate(SIG)
        _close(page, way)
        _closed_on(page, filtered, name, way)
        page.reload(wait_until="networkidle")
        _settle(page)
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
        here = _arrive(page, server.base, path)
        _open_sheet(page)
        page.locator(select).first.select_option(index=1)
        page.wait_for_url(lambda u: u != here)
        page.wait_for_load_state("networkidle")
        assert page.locator("#sp-filters.open").count() == 0, (name, "a new page")
        assert page.evaluate(PLAIN)
        page.go_back()
        page.wait_for_url(here)
        page.wait_for_load_state("networkidle")
        got = page.evaluate(FREE)
        assert got["open"] is False and got["inert"] == 0, (name, got)
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [360, 1280])
@pytest.mark.parametrize("name,path,select", RELOADING)
def test_back_after_a_drop_down_sent_the_form_shows_the_choice_the_list_was_drawn_with(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    select: str,
    width: int,
) -> None:
    """The drop-down row: Back from the page a drop-down sent the form for showed the sent choice
    over the unfiltered list (the browser restored the control, the page was drawn from the
    address). The controls match the list: on a phone (from the sheet) and on a desktop."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    server.ark.collection_tag_lists["alice"] = {
        21: {"id": 21, "name": "trade binder page 12", "color": "#d33"}
    }
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        here = _arrive(page, server.base, path)
        before = page.evaluate(SIG)
        if width < 600:
            _open_sheet(page)
        page.locator(select).first.select_option(index=1)
        page.wait_for_url(lambda u: u != here)
        _settle(page)
        assert page.evaluate(FORM) != before.split(" | ")[0], (name, "the choice did not change the page")
        page.go_back()
        page.wait_for_url(here)
        _settle(page)
        assert page.evaluate(SIG) == before, (name, width, page.evaluate(SIG), before)
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("way", WAYS)
@pytest.mark.parametrize("name,path,tags", SHEETS)
def test_forward_brings_the_sheet_back_only_after_a_back_closed_it(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    tags: bool,
    way: str,
) -> None:
    """N3 and L2: Forward after a Back closed the sheet shows it again (its entry is what Back
    took), so the next Back closes it and the one after leaves the page. Forward after the sheet
    closed by ×, Escape, the backdrop or the window widening lands on a plain entry of the page,
    without the sheet (L2: the sheet's entry was given back as it was, and Forward reopened the
    sheet), and Back from there is one press to the page and one more to leave."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    server.ark.collection_tag_lists["alice"] = (
        {21: {"id": 21, "name": "trade binder page 12", "color": "#d33"}} if tags else {}
    )
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        here = _arrive(page, server.base, path)
        _open_sheet(page)
        _close(page, way)
        _closed_on(page, here, name, way)
        if way == "widen":
            page.set_viewport_size({"width": 360, "height": 860})
            page.wait_for_timeout(100)
        page.go_forward()
        if way == "back":
            page.locator("#sp-filters.open").wait_for()
            assert page.evaluate(WHERE) == "sheet", (name, page.evaluate(WHERE))
            assert not page.evaluate(PLAIN)
            page.go_back()
            _closed_on(page, here, name, "back again")
        else:
            page.wait_for_url(here)
            page.wait_for_timeout(150)
            assert page.locator("#sp-filters.open").count() == 0, (name, way, "Forward reopened the sheet")
            got = page.evaluate(FREE)
            assert got["inert"] == 0 and got["role"] is None and got["scrim"] == 0, (name, way, got)
            assert page.evaluate(PLAIN), (name, way)
            page.go_back()
            page.wait_for_url(here)
            assert page.locator("#sp-filters.open").count() == 0, (name, way)
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("name,path,tags", SHEETS)
def test_a_page_reloaded_with_the_sheet_open_shows_the_page_on_a_plain_entry(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    tags: bool,
) -> None:
    """A reload with the sheet open (the app's pull to refresh) shows the page without the sheet,
    on a plain entry with the page's address, nothing inert. The sheet opened again works as
    ever: a filter set in it stays in the address when Back closes it (a reload shows the same
    list); then one Back stays on the page (the entry the reload doubled) and one leaves."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    server.ark.collection_tag_lists["alice"] = (
        {21: {"id": 21, "name": "trade binder page 12", "color": "#d33"}} if tags else {}
    )
    kind = LIVE_KIND.get(name)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        here = _arrive(page, server.base, path)
        _open_sheet(page)
        page.reload(wait_until="networkidle")
        _settle(page)
        assert page.url == here, (name, page.url)
        assert page.locator("#sp-filters.open").count() == 0, (name, "the sheet came back with the reload")
        got = page.evaluate(FREE)
        assert got["inert"] == 0 and got["role"] is None and got["scrim"] == 0, (name, got)
        assert got["lock"] is False, (name, got)
        page.wait_for_function("() => history.state === null")
        _open_sheet(page)
        filtered, sig = here, None
        if kind:
            _tick(page, kind)
            page.wait_for_url(lambda u: u != here)
            _settle(page)
            filtered = page.url
            sig = page.evaluate(SIG)
        page.go_back()
        _closed_on(page, filtered, name, "back")
        if kind:
            page.reload(wait_until="networkidle")
            _settle(page)
            assert page.url == filtered, (name, page.url)
            assert page.evaluate(SIG) == sig, (name, page.evaluate(SIG), sig)
        page.go_back()
        page.wait_for_url(here)
        _settle(page)
        assert page.locator("#sp-filters.open").count() == 0, name
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("name,path,tags,kind,back", SHOWS)
def test_show_results_from_the_sheet_keeps_the_filters_and_leaves_one_entry_at_most(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    tags: bool,
    kind: str | None,
    back: str,
) -> None:
    """H1: Show pressed in the sheet closes it and the page shows the filters set in it, in the
    list and in the address (a reload shows the same); deck search's Show threw them away (the
    search was sent while the sheet's own back() was on its way, and that back's popstate redrew
    the page from the old address). One Back from there is as without a sheet: to the page the
    sheet opened on where Show asked for a search (deck search's boxes and text wait for it), else
    to the page before (the filter went into the page's own address in place, or nothing
    changed)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    server.ark.collection_tag_lists["alice"] = (
        {21: {"id": 21, "name": "trade binder page 12", "color": "#d33"}} if tags else {}
    )
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        here = _arrive(page, server.base, path)
        before = page.evaluate(SIG)
        _open_sheet(page)
        if kind:
            _tick(page, kind)
            _settle(page)
        page.locator("#sp-filters .sp-show").click()
        page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
        _settle(page)
        if kind:
            page.wait_for_url(lambda u: u != here)
        shown = page.url
        sig = page.evaluate(SIG)
        assert (sig != before) == bool(kind), (name, sig, before)
        got = page.evaluate(FREE)
        assert got["inert"] == 0 and got["role"] is None and got["scrim"] == 0, (name, got)
        assert page.evaluate(PLAIN), name
        page.reload(wait_until="networkidle")
        _settle(page)
        assert page.url == shown and page.evaluate(SIG) == sig, (name, page.url, page.evaluate(SIG), sig)
        page.go_back()
        if back == "here":
            page.wait_for_url(here)
            _settle(page)
            assert page.locator("#sp-filters.open").count() == 0, name
            assert page.evaluate(SIG) == before, (name, page.evaluate(SIG), before)
            page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("name,path,kind", CLEARS)
def test_clear_filters_from_the_sheet_is_one_entry_over_the_filtered_page(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    kind: str,
) -> None:
    """L1: Clear filters in the sheet (a link to the page without filters) leaves the sheet's
    entry behind it, so the cleared page is one Back from the filtered page, shown without the
    sheet, and that page one Back from the one before: two presses to leave, as without a sheet
    (before: Back brought the sheet back, and leaving took three presses)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        if kind == "set":
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            value = page.locator("#sp-filters select option").nth(1).get_attribute("value")
            path = f"{path}?set={value}"
        here = _arrive(page, server.base, path)
        _open_sheet(page)
        if kind != "set":
            _tick(page, kind)
            page.wait_for_url(lambda u: u != here)
            _settle(page)
        filtered = page.url
        clear = page.locator("#sp-filters a.sp-clear")
        clear.wait_for(state="visible")
        target = clear.get_attribute("href")
        clear.click()
        page.wait_for_url(f"{server.base}{target}")
        _settle(page)
        assert page.locator("#sp-filters.open").count() == 0, name
        assert page.evaluate(PLAIN), name
        assert page.locator("#sp-filters a.sp-clear").is_hidden(), (name, "a filter is still set")
        page.go_back()
        page.wait_for_url(filtered)
        _settle(page)
        assert page.locator("#sp-filters.open").count() == 0, (name, "the sheet came back")
        got = page.evaluate(FREE)
        assert got["inert"] == 0 and got["role"] is None and got["scrim"] == 0, (name, got)
        assert page.evaluate(PLAIN), name
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == []
        browser.close()
