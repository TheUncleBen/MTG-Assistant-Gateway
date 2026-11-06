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
that, as it is without a sheet, and nothing when nothing changed (audit L4); Forward brings the
sheet back only after a Back closed it. After any Back or Forward the address, the list, the
boxes and the badge agree (SIG below, against a reload): a page landing on an entry with another
address redraws from it (audit R1, where the Back after a reload with the sheet open left the
filtered list under the unfiltered address). A page reloaded with the sheet open shows the page,
not the sheet, on a plain entry: reopening it on load and closing it with Back would be a fresh
page load in Safari (the iPhone) and Firefox, which lose a filter set in the reopened sheet; the
price is the entry the reload leaves under the page's (no API removes one), so one Back shows the
page as before the sheet's filters (a fresh load in Safari, a redraw in Chromium: the same)."""

from __future__ import annotations

import pytest

from .test_cardsearch_browser import _chromium, _page
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_sheet_focus_browser import FREE, SHEETS, _open_sheet
from .test_suggest_browser import Server
from .waits import history_step, quiet, redrawn

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
# Show in every sheet, with each kind of filter the sheet holds (None: nothing changed, on a sheet
# whose only filter sends the form by itself or on deck search), and where one Back lands
# afterwards: "prev" where the filter went into the page's own address or nothing changed (Show
# only closes the sheet: audit L4), "here" where Show itself asked for the search, which is its
# own entry over the page's, as a Search press is without a sheet
SHOWS = [
    ("binder-view", "/collection", False, None, "prev"),
    ("binder-filters", "/collection", True, None, "prev"),
    ("cards", "/cards?q=sol", False, "box", "prev"),
    ("cards-text", "/cards?q=sol", False, "text:o", "prev"),
    ("decks", "/decks", False, "box", "prev"),
    ("decks-packages", "/decks?show=packages", False, "box", "prev"),
    ("search-box", "/search", False, "box", "here"),
    ("search-text", "/search", False, "text:commander", "here"),
    ("search-select", "/search", False, "select", "prev"),
    ("search-none", "/search", False, None, "prev"),
    ("precons", "/precons", False, None, "prev"),
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
PLAIN = "() => !(history.state && history.state.mtglayer)"
# nothing holds the page: no inert part, no sheet role, no sheet or pick-list backdrop (FREE's
# checks, on a page with or without a filter sheet)
CLEAR = """() => { const s = document.getElementById('sp-filters');
  return {inert: document.querySelectorAll('[inert]').length, role: s ? s.getAttribute('role') : null,
    scrim: document.querySelectorAll('.sp-scrim, .pickm-scrim').length}; }"""
# nothing open over the page: no layer of static/layers.js, and none of their markup shown
WHAT_OPEN = """() => (window.MtgLayers.top() || {}).name + ' ' + [...document.querySelectorAll(
  '#sp-filters.open, details.pickm[open], .mtgbox[role=dialog], .ctxmenu[role=menu], details.dd[open],'
  + ' .cardview.open, .printpick.open, .msel-list, body.search-open, .addsheet.open, .edhplay-dialog')]
  .filter(e => e.getClientRects().length).map(e => e.className).join(',')"""
NO_LAYER = """() => !(window.MtgLayers && window.MtgLayers.top()) && ![...document.querySelectorAll(
  '#sp-filters.open, details.pickm[open], .mtgbox[role=dialog], .ctxmenu[role=menu], details.dd[open],'
  + ' .cardview.open, .printpick.open, .msel-list, body.search-open, .addsheet.open, .edhplay-dialog')]
  .some(e => e.getClientRects().length)"""


def _settle(page) -> None:  # noqa: ANN001
    page.wait_for_load_state("networkidle")
    page.wait_for_function("() => !document.querySelector('[aria-busy=true]')")


def _shows(page, sig: str, where: str) -> None:  # noqa: ANN001
    """The page shows ``sig`` (its list, boxes, badge and text), once its redraw from the address
    Back or Forward landed on is done: in place, or as a fresh load of that address (the
    evaluation fails while the page is on its way)."""
    from playwright.sync_api import Error

    for _ in range(100):
        try:
            if page.evaluate(SIG) == sig:
                return
        except Error:
            pass
        page.wait_for_timeout(100)  # fixed wait: poll interval of a loop with a deadline
    assert page.evaluate(SIG) == sig, (where, page.evaluate(SIG), sig)


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


def _forward_shows_nothing(page, here: str, where: object) -> None:  # noqa: ANN001
    """Forward after a layer closed: the page as it was, no layer, on a plain entry with the
    same address (an entry a layer gave back is stepped back over at once, so Playwright's own
    go_forward, which waits for a landing, is not used)."""
    history_step(page)
    page.wait_for_function(PLAIN)
    assert page.url == here, (where, page.url)
    got = page.evaluate(CLEAR)
    assert got["inert"] == 0 and got["role"] is None and got["scrim"] == 0, (where, got)
    assert page.evaluate(NO_LAYER), (where, "Forward brought a layer back")


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
def test_forward_after_the_sheet_closed_shows_nothing_new_whichever_way_it_closed(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    tags: bool,
    way: str,
) -> None:
    """Audit F-1 on eccfa3d9 (N3, L2 and L3 before it): Forward after the sheet closed, by Back
    or by ×, Escape, the backdrop or the window widening, shows nothing new: the page as it was,
    without the sheet, on its own plain entry (the one entry that stands for "a layer is open",
    static/layers.js, steps straight back when Forward lands on it, as there is nothing to show),
    and Back from there is one press to leave. Before: Forward after a Back brought the sheet
    back, and after the other ways landed on a dead duplicate of the page."""
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
        _close(page, way)
        _closed_on(page, here, name, way)
        if way == "widen":
            page.set_viewport_size({"width": 360, "height": 860})
            redrawn(page)
        _forward_shows_nothing(page, here, (name, way))
        assert page.evaluate(SIG) == before, (name, way, page.evaluate(SIG), before)
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
    """A reload with the sheet open (the app's menu Reload) shows the page without the sheet, on
    a plain entry with the page's address, nothing inert. The sheet opened again works as ever:
    a filter set in it stays in the address when Back closes it (a reload shows the same list);
    then one Back lands on the page's own entry under the one the reload left, with the address
    as before the sheet's filters, and the page shows that address (audit R1: on /cards, /decks
    and Packages the list, the boxes and the badge kept the filter under the unfiltered address),
    and one more leaves."""
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
        before = page.evaluate(SIG)
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
        _shows(page, before, (name, "the page's own entry"))
        _settle(page)
        assert page.url == here and page.locator("#sp-filters.open").count() == 0, (name, page.url)
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
        asked: list[str] = []
        page.on("request", lambda r: asked.append(r.url) if r.resource_type in ("fetch", "xhr") else None)
        entries = page.evaluate("() => history.length")
        page.evaluate("() => { window.sameDocument = true; }")
        page.locator("#sp-filters .sp-show").click()
        page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
        _settle(page)
        if kind:
            page.wait_for_url(lambda u: u != here)
        else:
            # nothing changed: the sheet closes, nothing is asked for, no entry, no page load
            assert page.url == here and page.evaluate("() => window.sameDocument === true"), (name, page.url)
            assert page.evaluate("() => history.length") == entries and asked == [], (name, asked)
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


# the sheet's own back() held for a while, and every fetch slowed, so a search sent from the
# sheet lands in the gap between the back() and its popstate
SLOW = """() => {
  const back = history.back.bind(history);
  history.back = () => setTimeout(back, 700);
  const f = window.fetch.bind(window);
  window.fetch = (u, o) => new Promise(r => setTimeout(() => r(f(u, o)), 250));
}"""


def test_a_search_landing_while_the_sheets_back_is_on_its_way_keeps_its_address(
    server: Server,  # noqa: F811
) -> None:
    """L5: a drop-down picked in deck search's sheet sends its search; Show pressed at once
    closes the sheet, whose back() is on its way when the search lands. The landed search's
    address goes onto the entry the back lands on (before: pushed over the sheet's entry, it was
    lost under the traversal, and the results showed under the old address). The address names
    the filter, a reload shows the same results, and one Back leaves the page."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        here = _arrive(page, server.base, "/search")
        _open_sheet(page)
        page.evaluate(SLOW)
        _tick(page, "select")
        page.locator("#sp-filters .sp-show").click()
        page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
        page.wait_for_url(lambda u: u != here)
        quiet(page)  # the held back() and its popstate
        _settle(page)
        filtered = page.url
        assert "format=" in filtered and page.evaluate(PLAIN), filtered
        sig = page.evaluate(SIG)
        assert sig.split(" | ")[0] != "", sig
        page.reload(wait_until="networkidle")
        _settle(page)
        assert page.url == filtered and page.evaluate(SIG) == sig, (page.url, page.evaluate(SIG), sig)
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == []
        browser.close()


# -- every layer over a page (audit F-1 and F-2 on eccfa3d9): the one pre-push check ----------------
# Each kind of layer static/layers.js knows, on a page of the sweep server: how it opens, what
# shows it open, the layer it sits in (closed after it), and whether Escape closes it (the top
# search folds on Escape only while its box is empty; a drop-down menu closes on Escape; the
# shortcuts box as every box).
LAYERS: list[tuple[str, str, str, str, str, str]] = [
    # name, page, how it opens, shown by, its name as a layer, the layer under it ("" when none)
    ("filter sheet", "/cards?q=sol", "sheet", "#sp-filters.open", "sheet", ""),
    ("pick-several list", "/cards?q=sol", "pickm", "details.pickm[open]", "pickm", "#sp-filters.open"),
    ("pop-up box", "/decks/42", "key:?", ".mtgbox[role=dialog]:not([hidden])", "box", ""),
    ("card menu", "/cards?q=sol", "click:.cardgrid .nm .kebab", ".ctxmenu[role=menu]", "menu", ""),
    ("deck card menu", "/decks/42", "contextmenu:[data-card]", ".ctxmenu[role=menu]", "menu", ""),
    ("drop-down menu", "/decks/42", "click:details.dd.more > summary", "details.dd[open]", "dropdown", ""),
    ("card viewer", "/decks/42", "click:[data-card]", ".cardview.open", "cardview", ""),
    ("printing picker", "/decks/42", "printpick", ".printpick.open", "printpick", ""),
    ("themed drop-down", "/cards?q=sol", "select", ".msel-list", "select", "#sp-filters.open"),
    ("top search", "/decks/42", "click:.topbar .searchbtn", "body.search-open", "topsearch", ""),
    ("Add sheet", "/cards?q=sol", "addsheet", ".addsheet.open", "addsheet", ""),
    ("EDHPlay box", "/decks/42", "edhplay", ".edhplay-dialog", "box", ""),
]
LAYER_WAYS = ["back", "escape"]


def _open_layer(page, how: str) -> None:  # noqa: ANN001
    kind, _, sel = how.partition(":")
    if kind == "sheet":
        _open_sheet(page)
    elif kind == "pickm":
        _open_sheet(page)
        page.locator("#sp-filters details.pickm > summary").first.click()
    elif kind == "select":
        _open_sheet(page)
        page.locator("#sp-filters .msel-btn").first.click()
    elif kind == "key":
        page.locator("body").click(position={"x": 5, "y": 300})
        page.keyboard.press(sel)
    elif kind == "click":
        page.locator(sel).first.click()
    elif kind == "contextmenu":
        card = page.locator(sel).first
        card.scroll_into_view_if_needed()
        card.evaluate(
            "c => c.dispatchEvent(new MouseEvent('contextmenu',"
            " {bubbles: true, cancelable: true, clientX: 40, clientY: 40}))"
        )
    elif kind == "printpick":
        _open_layer(page, "contextmenu:[data-card]")
        page.locator(".ctxmenu[role=menu]").wait_for()
        page.locator(".ctxmenu [role=menuitem]", has_text="Printings…").click()
    elif kind == "edhplay":
        page.locator(".banner .decktestbtn").click()
        page.locator(".ctxmenu[role=menu]").wait_for()
        page.locator(".ctxmenu [role=menuitem]", has_text="Play on EDHPlay").click()
    elif kind == "addsheet":
        page.locator(".cc .pic").nth(1).click()
        page.locator(".cardview.open").wait_for()
        page.locator(".cardview.open button", has_text="Add to deck").click()
    else:
        raise AssertionError(how)


def _shown(page, sel: str) -> bool:  # noqa: ANN001
    return page.evaluate(
        "(sel) => [...document.querySelectorAll(sel)]"
        ".some(e => e === document.body || e.getClientRects().length)",
        sel,
    )


def _wait_shown(page, sel: str, shown: bool, where: object, layer: str = "") -> None:  # noqa: ANN001
    """The layer's markup shown (or gone), and, for an opening, the layer on the stack under its
    name (a details' toggle event is queued, so its layer registers a moment after it shows)."""
    for _ in range(50):
        if _shown(page, sel) == shown and (
            not shown
            or not layer
            or page.evaluate("() => window.MtgLayers.top() && window.MtgLayers.top().name") == layer
        ):
            return
        page.wait_for_timeout(100)  # fixed wait: poll interval of a loop with a deadline
    raise AssertionError((where, sel, "shown" if shown else "closed"))


def _close_layer(page, way: str) -> None:  # noqa: ANN001
    if way == "back":
        page.go_back()
    else:
        page.keyboard.press("Escape")


def _nothing_open_on(page, here: str, where: object) -> None:  # noqa: ANN001
    for _ in range(50):
        if page.evaluate(NO_LAYER) and page.evaluate(PLAIN):
            break
        page.wait_for_timeout(100)  # fixed wait: poll interval of a loop with a deadline
    assert page.evaluate(NO_LAYER), (where, "a layer is still open", page.evaluate(WHAT_OPEN))
    assert page.evaluate(PLAIN), (
        where,
        "the layer's entry is still there",
        page.evaluate("() => history.state"),
    )
    assert page.url == here, (where, page.url)
    got = page.evaluate(CLEAR)
    assert got["inert"] == 0 and got["role"] is None and got["scrim"] == 0, (where, got)


def test_every_layer_leaves_the_history_as_it_was_for_back_forward_reload_show_and_resize(
    server: Server,  # noqa: F811
) -> None:
    """Audit F-1 and F-2 on eccfa3d9, the pre-push check for every layer over a page (the filter
    sheet, its pick-several list and themed drop-down, a pop-up box, the card and deck card
    menus, a drop-down menu, the card viewer, the printing picker, the top search, the card
    search's Add sheet and the EDHPlay box), on a phone with touch, where one history entry
    stands for "a layer is open" (static/layers.js):
    - the address never changes for a layer;
    - Back closes the layer (one under it stays, and closes on the next Back), never the page;
    - closing it by Escape leaves the history as before the layer opened, and Forward after
      either way of closing shows nothing new;
    - a reload with the layer open shows the page on a plain entry, with one Back to the page's
      own entry and one more to leave;
    - the sheet's Show with nothing changed only closes it (no entry, no request);
    - the window widening past phone width lets the page go: whatever closed, Back leaves."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        for name, path, how, shown, layer, under in LAYERS:
            here = _arrive(page, server.base, path)
            for way in LAYER_WAYS:
                _open_layer(page, how)
                _wait_shown(page, shown, True, (name, way), layer)
                assert page.url == here, (name, way, page.url)
                _close_layer(page, way)
                _wait_shown(page, shown, False, (name, way))
                if under:
                    # the layer it sat in is still open, and closes the same way
                    quiet(page)  # the inner layer's Back has landed
                    assert _shown(page, under), (name, way, "the layer under it closed too")
                    assert page.url == here, (name, way, page.url)
                    _close_layer(page, way)
                    _wait_shown(page, under, False, (name, way))
                _nothing_open_on(page, here, (name, way))
                _forward_shows_nothing(page, here, (name, way))
            # a reload with the layer open
            _open_layer(page, how)
            _wait_shown(page, shown, True, (name, "reload"), layer)
            page.reload(wait_until="networkidle")
            _settle(page)
            page.wait_for_function("() => history.state === null")
            _nothing_open_on(page, here, (name, "reload"))
            page.go_back()
            page.wait_for_url(here)
            _nothing_open_on(page, here, (name, "back after the reload"))
            page.go_back()
            page.wait_for_url(f"{server.base}{PREV}")
            # the window widening past phone width, with the layer open
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            _settle(page)
            _open_layer(page, how)
            _wait_shown(page, shown, True, (name, "widen"), layer)
            page.set_viewport_size({"width": 1280, "height": 860})
            redrawn(page)
            quiet(page)
            if _shown(page, shown):
                page.keyboard.press("Escape")
                _wait_shown(page, shown, False, (name, "widen"))
            if under and _shown(page, under):
                page.keyboard.press("Escape")
                _wait_shown(page, under, False, (name, "widen"))
            _nothing_open_on(page, here, (name, "widen"))
            page.go_back()
            page.wait_for_url(f"{server.base}{PREV}")
            page.set_viewport_size({"width": 360, "height": 860})
        # the sheet's Show with nothing changed: the sheet closes, nothing else happens
        here = _arrive(page, server.base, "/cards?q=sol")
        _open_sheet(page)
        asked: list[str] = []
        page.on("request", lambda r: asked.append(r.url) if r.resource_type in ("fetch", "xhr") else None)
        page.locator("#sp-filters .sp-show").click()
        _nothing_open_on(page, here, ("sheet", "show"))
        assert asked == [], asked
        page.go_back()
        page.wait_for_url(f"{server.base}{PREV}")
        assert errors == [], errors
        browser.close()


# the Android app (its Back key asks the page first: MainActivity.kt) and Chromium on Android
# (a CloseWatcher takes the Back gesture): no history entry for a layer at all
APP_UA = (
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36 MTGAssistant/1.0"
)
ANDROID_UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36"
NATIVE = (
    "window.MtgNative = { backKey: function () { return true; },"
    " layer: function (open) { window.__layer = open; } };"
)


@pytest.mark.parametrize("mode", ["key", "watcher"])
def test_the_app_and_chromium_on_android_keep_no_history_entry_for_a_layer(
    server: Server,  # noqa: F811
    mode: str,
) -> None:
    """F-1 and F-2, the two ways without an entry: in the app (an app that answers MtgNative.backKey)
    the Back key asks the page (window.MtgBackKey closes the top layer and says so; nothing open,
    it says no and the app goes back), and the app is told when a layer is open; in Chromium on
    Android a CloseWatcher takes the Back gesture (its close request closes the layer). In both,
    opening and closing every layer leaves the history exactly as it was: the same length, a
    plain entry, the same address."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(
            viewport={"width": 360, "height": 860},
            has_touch=True,
            user_agent=APP_UA if mode == "key" else ANDROID_UA,
        )
        if mode == "key":
            ctx.add_init_script(NATIVE)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for name, path, how, shown, layer, under in LAYERS:
            here = _arrive(page, server.base, path)
            assert page.evaluate("() => window.MtgLayers.mode") == mode, name
            length = page.evaluate("() => history.length")
            _open_layer(page, how)
            _wait_shown(page, shown, True, (name, mode), layer)
            assert page.evaluate("() => history.length") == length and page.url == here, (name, mode)
            assert page.evaluate(PLAIN), (name, mode)
            if mode == "key":
                assert page.evaluate("() => window.__layer === true"), (name, "the app was not told")
                assert page.evaluate("() => window.MtgBackKey()") is True, name
            else:
                page.evaluate("() => window.MtgLayers.top().watcher.requestClose()")
            _wait_shown(page, shown, False, (name, mode))
            if under:
                assert _shown(page, under), (name, mode, "the layer under it closed too")
                if mode == "key":
                    assert page.evaluate("() => window.MtgBackKey()") is True, name
                else:
                    page.evaluate("() => window.MtgLayers.top().watcher.requestClose()")
                _wait_shown(page, under, False, (name, mode))
            page.wait_for_function(NO_LAYER)
            assert page.evaluate("() => history.length") == length and page.url == here, (name, mode)
            assert page.evaluate(PLAIN), (name, mode)
            if mode == "key":
                assert page.evaluate("() => window.__layer === false"), (name, "the app was not told")
                assert page.evaluate("() => window.MtgBackKey()") is False, name
            page.go_back()
            page.wait_for_url(f"{server.base}{PREV}")
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize("name,path,kind", [LIVE[0], LIVE[3]], ids=["cards", "search"])
def test_a_slow_back_over_the_sheet_still_lands_on_the_filtered_address(
    server: Server,  # noqa: F811
    name: str,
    path: str,
    kind: str,
) -> None:
    """Under load the sheet's own history.back() can take longer than the layers' 1.5 s wait
    (static/layers.js settled): the filter's address was then set on the sheet's entry, and the
    back landed on the page's entry with the address from before the filter (the full suite's
    "search-select-widen" run). The address waits for the landing however long it takes."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        _arrive(page, server.base, path)
        _open_sheet(page)
        here = page.url
        _tick(page, kind)
        page.wait_for_url(lambda u: u != here)
        _settle(page)
        filtered = page.url
        # a busy machine: the browser takes 2.5 s to step back
        page.evaluate(
            "() => { const back = history.back.bind(history); history.back = () => setTimeout(back, 2500); }"
        )
        page.set_viewport_size({"width": 1280, "height": 860})  # the sheet closes as the window widens
        page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
        page.wait_for_function(PLAIN, timeout=10_000)  # the sheet's entry is gone: the back landed
        quiet(page)
        assert page.url == filtered, (name, page.url)
        assert errors == [], errors
        browser.close()
