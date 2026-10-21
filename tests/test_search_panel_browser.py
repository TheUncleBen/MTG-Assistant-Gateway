"""The search panel the card search, the deck search and My decks share (0.7.36), in headless
Chromium at 344, 390, 810, 1024 and 1280 px: nothing scrolls sideways, a control group never
splits apart, the results bar sits at the panel's foot, the colour match drop-down sits beside the
colour buttons where the group is wide and under them where it is not (two rows of three buttons
on a phone), the rarity drop-down shows its picks, "Load more decks" appends the next page and says
when Archidekt's cap is reached, an Archidekt link in the deck name box shows "Open this deck",
"Exactly these" colours keep only those decks, My decks filters in the browser as you type and
pick, the deck page's toolbar shows the card size beside the view with the drag hint, and the
navigation rail reaches 1279 px on a touch screen. Skipped without Chromium."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from .test_cardsearch_browser import CATALOG, _chromium
from .test_deck_menu_browser import PNG
from .test_editor_browser import _link
from .test_suggest_browser import Server

pytest.importorskip("playwright")

WIDTHS = [344, 390, 810, 1024, 1280]


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True)
    s.sf.catalog = CATALOG + [f"Filler Card {i}" for i in range(30)]
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    ark = s.ark
    for did, name in ((46, "Fresh brew"), (47, "Another sample deck")):
        ark.decks[did] = dict(ark.decks[42], id=did, name=name, updatedAt="2026-09-20T10:00:00Z")
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _page(p, server: Server, sid: str, width: int, exe: str | None, *, touch: bool | None = None):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    touch = width < 900 if touch is None else touch
    ctx = browser.new_context(viewport={"width": width, "height": 860}, has_touch=touch, is_mobile=touch)
    ctx.route("https://cards.scryfall.io/**", lambda r: r.fulfill(content_type="image/png", body=PNG))
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


# The panel's layout: the window, the panel and its results bar, every filter group with its
# visible children, the colour buttons and their match drop-down.
PANEL = """
() => {
  const r = e => { const b = e.getBoundingClientRect(); return {t: Math.round(b.top), l: Math.round(b.left),
    r: Math.round(b.right), b: Math.round(b.bottom), w: Math.round(b.width)}; };
  const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const panel = document.querySelector('.spanel'), rbar = panel.querySelector('.rbar');
  const groups = [...panel.querySelectorAll('.fgrid > *')].filter(shown).map(g => ({
    box: r(g), kids: [...g.children].filter(shown).map(r)}));
  const colors = panel.querySelector('.colors');
  const pills = [...colors.querySelectorAll('.tog')].map(r);
  const mode = r(colors.querySelector('.msel-btn') || colors.querySelector('select'));
  const vw = document.documentElement.clientWidth;
  const outside = [...panel.querySelectorAll('button, input:not([type=hidden]), .msel-btn, a.btn, summary')]
    .filter(shown).map(r).filter(b => b.l < -1 || b.r > vw + 1).length;
  return {sw: document.documentElement.scrollWidth, vw, panel: r(panel),
    rbar: rbar && shown(rbar) ? r(rbar) : null, groups, pills, mode, colorsW: r(colors).w, outside};
}
"""


def _check_panel(got: dict, width: int) -> None:
    assert got["sw"] <= got["vw"], got
    assert got["outside"] == 0, got
    for g in got["groups"]:  # a group's label and control stay together, inside the group
        for k in g["kids"]:
            assert k["l"] >= g["box"]["l"] - 1 and k["r"] <= g["box"]["r"] + 1, (g, width)
        if len(g["kids"]) >= 2:
            assert g["kids"][0]["b"] <= g["kids"][1]["t"] + 1, (g, width)  # the label above its control
    pills, mode = got["pills"], got["mode"]
    assert len(pills) == 6, pills
    if width >= 600:
        assert len({p["t"] for p in pills}) == 1, pills  # the six colours share one row
        if got["colorsW"] >= 690:
            assert mode["t"] < pills[5]["b"] and mode["l"] > pills[5]["r"], (pills, mode, got["colorsW"])
        else:
            assert mode["t"] >= pills[5]["b"], (pills, mode, got["colorsW"])
        assert got["rbar"] and got["rbar"]["b"] <= got["panel"]["b"] + 1, got  # the bar is the panel's foot
        assert got["rbar"]["t"] > got["panel"]["t"], got
    else:
        assert len({p["l"] for p in pills}) == 3 and len({p["t"] for p in pills}) == 2, pills
        assert mode["t"] >= pills[5]["b"], (pills, mode)


@pytest.mark.parametrize("width", WIDTHS)
def test_the_deck_search_panel_fits_every_width(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, exe)
        page.goto(f"{server.base}/search?format=3", wait_until="networkidle")
        assert page.locator("#s-count").inner_text() == "3 decks"
        if width < 600:
            assert (
                page.locator(".sp-open").is_visible()
                and not page.locator("#f-format, #s-format").is_visible()
            )
            assert (
                page.locator(".rbar").bounding_box()["y"]
                < page.locator(".spanel").bounding_box()["y"]
                + (page.locator(".spanel").bounding_box()["height"])
            )
            page.locator(".sp-open").click()
            page.locator("#sp-filters.open").wait_for(timeout=3000)
            assert page.locator("#sp-filters .sheet-size .cardsize").is_visible()
        got = page.evaluate(PANEL)
        _check_panel(got, width)
        if width < 600:
            # Show results submits the search: wait for the new page to finish loading, or the
            # tiles can be measured on a page whose styles are not applied yet
            with page.expect_navigation(wait_until="networkidle"):
                page.locator(".sp-show").click()
            assert not page.locator("#sp-filters.open").count()
        # the tiles: as many as fit, each at least 220 px wide (M)
        cols = page.evaluate(
            "() => { const ul = document.querySelector('ul.decklist.grid');"
            " return {w: ul.getBoundingClientRect().width,"
            " cols: getComputedStyle(ul).gridTemplateColumns.split(' ').map(parseFloat)}; }"
        )
        assert all(c >= 219 for c in cols["cols"]), cols
        assert len(cols["cols"]) == max(1, int((cols["w"] + 25) // 245)), (cols, width)
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_the_card_search_panel_fits_every_width_and_rarity_shows_its_picks(
    server: Server, width: int
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, exe)
        page.goto(f"{server.base}/cards?q=s", wait_until="networkidle")
        if width < 600:
            page.locator(".sp-open").click()
            page.locator("#sp-filters.open").wait_for(timeout=3000)
        _check_panel(page.evaluate(PANEL), width)
        # the rarity drop-down: a checkbox list that names what is picked
        pick = page.locator(".pickm")
        assert page.locator(".pickm .v").inner_text() == "Any rarity"
        page.locator(".pickm summary").click()
        page.locator(".pickm[open]").wait_for(timeout=3000)
        page.locator(".pickm input[value=r]").check()
        assert page.locator(".pickm .v").inner_text() == "Rare"
        page.locator(".pickm input[value=m]").check()
        assert page.locator(".pickm .v").inner_text() == "Rare, Mythic rare"
        page.wait_for_function("() => location.search.includes('r=m')", timeout=5000)
        page.keyboard.press("Escape")
        assert pick.get_attribute("open") is None
        assert page.evaluate("() => document.activeElement.closest('.pickm') !== null")
        if width < 600:
            assert page.locator("#sp-filters.open").count() == 1  # Escape closed the list, not the sheet
        assert errors == [], errors
        browser.close()


def test_load_more_decks_appends_the_next_page_and_says_when_the_cap_is_reached(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    server.ark.search_page_size = 2
    exe = _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, exe)
        page.goto(f"{server.base}/search?format=3", wait_until="networkidle")
        assert page.locator("ul.decklist li.deck").count() == 2
        assert page.locator("#s-count").inner_text() == "3 decks"
        more = page.locator("#s-more [data-more]")
        assert more.is_visible() and more.inner_text() == "Load more decks"
        more.click()
        page.wait_for_function(
            "() => document.querySelectorAll('ul.decklist li.deck').length === 3", timeout=5000
        )
        assert more.is_hidden() and page.locator("#s-more p").is_hidden()
        assert server.ark.search_params[-1]["page"] == "2"
        # at Archidekt's cap (page 17 with more behind it) the line says so and there is no button
        for did in range(100, 140):
            server.ark.decks[did] = dict(server.ark.decks[42], id=did, name=f"Deck {did}")
        page.goto(f"{server.base}/search?format=3&page=17", wait_until="networkidle")
        server.ark.search_page_size = 0
        assert page.locator("#s-more p").is_visible() and more.is_hidden()
        assert "at most 1,000 decks" in page.locator("#s-more p").inner_text()
        assert errors == [], errors
        browser.close()


def test_a_deck_link_in_the_name_box_offers_to_open_it(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, exe)
        page.goto(f"{server.base}/search", wait_until="networkidle")
        assert page.locator(".openhint").count() == 0
        page.locator("#s-name").fill("https://archidekt.com/decks/42")
        hint = page.locator(".openhint")
        hint.wait_for(timeout=3000)
        assert hint.inner_text().startswith("Archidekt deck 42")
        link = hint.locator("a")
        assert link.inner_text().strip() == "Open this deck"
        assert link.get_attribute("href") == "/decks/open?ref=42"
        page.locator("#s-name").fill("sample")
        page.wait_for_function("() => !document.querySelector('.openhint')", timeout=3000)
        # the page itself shows it for a link in the address, without asking Archidekt
        searches = len(server.ark.search_params)
        page.goto(f"{server.base}/search?name=https://archidekt.com/decks/42", wait_until="networkidle")
        assert page.locator(".openhint a").get_attribute("href") == "/decks/open?ref=42"
        assert len(server.ark.search_params) == searches
        with page.expect_navigation():
            page.locator(".openhint a").click()
        assert urlparse(page.url).path == "/decks/42"
        assert errors == [], errors
        browser.close()


def test_exactly_these_colours_keep_only_decks_of_those_colours(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, exe)
        page.goto(f"{server.base}/search?format=3&c=U&c=G&cm=exact", wait_until="networkidle")
        assert page.locator("ul.decklist li.deck").count() == 3  # every fake deck is blue and green
        assert server.ark.search_params[-1]["colors"] == "U,G"
        assert page.locator("select[name=cm]").input_value() == "exact"
        page.goto(f"{server.base}/search?format=3&c=U&cm=exact", wait_until="networkidle")
        assert page.locator("ul.decklist li.deck").count() == 0
        assert "No public decks match" in page.locator("main").inner_text()
        assert page.locator("#s-count").inner_text() == "0 decks"
        # a pick from the drop-down applies at once (the form sends itself)
        page.goto(f"{server.base}/search?format=3&c=U", wait_until="networkidle")
        with page.expect_navigation():
            page.locator("select[name=cm]").select_option("exact")
        assert parse_qs(urlparse(page.url).query)["cm"] == ["exact"]
        assert errors == [], errors
        browser.close()


def _names(page) -> list[str]:
    return page.locator("ul.decklist .deck .name").all_inner_texts()


@pytest.mark.parametrize("width", [390, 1280])
def test_my_decks_filters_in_the_browser_as_you_type_and_pick(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, exe)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        page.wait_for_selector("ul.decklist li.deck")
        assert page.locator("#decks-total").inner_text() == "3 decks"
        page.evaluate("() => { window.__same = true; }")
        page.locator("#q").fill("fresh")
        page.wait_for_function("() => location.search.includes('q=fresh')", timeout=5000)
        page.wait_for_function("() => !document.querySelector('#decks-list[aria-busy]')", timeout=5000)
        assert _names(page) == ["Fresh brew"] and page.evaluate("() => window.__same")  # no reload
        assert page.locator("#decks-total").inner_text() == "1 deck"
        page.locator("#q").fill("")
        page.wait_for_function("() => !location.search.includes('q=')", timeout=5000)
        page.wait_for_function(
            "() => document.querySelectorAll('ul.decklist li.deck').length === 3", timeout=5000
        )
        if width < 600:
            page.locator(".sp-open").click()
            page.locator("#sp-filters.open").wait_for(timeout=3000)
        # a colour the decks do not have
        page.locator(".colors .tog input[value=W]").check()
        page.wait_for_function("() => location.search.includes('c=W')", timeout=5000)
        page.wait_for_function("() => !document.querySelector('#decks-list[aria-busy]')", timeout=5000)
        assert "No decks match these filters in any folder." in page.locator("#decks-list").inner_text()
        assert page.locator("#decks-total").inner_text() == "0 decks"
        page.locator(".colors .tog input[value=W]").uncheck()
        page.locator(".colors .tog input[value=U]").check()
        page.wait_for_function("() => location.search.includes('c=U') && !location.search.includes('c=W')")
        page.wait_for_function(
            "() => document.querySelectorAll('ul.decklist li.deck').length === 3", timeout=5000
        )
        if width < 600:
            page.locator(".sp-show").click()
            page.wait_for_function("() => !document.getElementById('sp-filters').classList.contains('open')")
        else:
            # the sort and its direction
            page.locator("#f-order").select_option("name")
            page.wait_for_function("() => location.search.includes('order=name')", timeout=5000)
            page.wait_for_function("() => !document.querySelector('#decks-list[aria-busy]')", timeout=5000)
            assert _names(page) == ["Another sample deck", "Fresh brew", "Sample Commander Deck"]
            page.locator("select[name=dir]").select_option("desc")
            page.wait_for_function("() => location.search.includes('dir=desc')", timeout=5000)
            page.wait_for_function("() => !document.querySelector('#decks-list[aria-busy]')", timeout=5000)
            assert _names(page) == ["Sample Commander Deck", "Fresh brew", "Another sample deck"]
            page.locator("#f-format").select_option("Modern")
            page.wait_for_function("() => location.search.includes('format=modern')", timeout=5000)
            page.wait_for_function("() => !document.querySelector('#decks-list[aria-busy]')", timeout=5000)
            assert page.locator("#decks-total").inner_text() == "0 decks"
        # Clear filters is a link back to the plain list
        if width < 600:
            page.locator(".sp-open").click()
            page.locator("#sp-filters.open").wait_for(timeout=3000)
        with page.expect_navigation():
            page.locator(".sp-clear:visible").first.click()
        assert urlparse(page.url).path == "/decks" and not urlparse(page.url).query
        assert page.evaluate(PANEL)["sw"] <= width
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_the_deck_page_toolbar_shows_the_size_beside_the_view_with_the_drag_hint(
    server: Server, width: int
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, exe)
        page.goto(f"{server.base}/decks/42?view=stacks", wait_until="networkidle")
        hint = page.locator(".toolbar .hint")
        assert hint.inner_text() == "Drag a card onto another category to move it; on touch, hold it first."
        view = page.locator(".toolbar #f-view-btn").bounding_box()
        size = page.locator(".toolbar .cardsize .sizeseg").bounding_box()
        assert view and size and abs(view["y"] - size["y"]) <= 2, (view, size)
        assert page.get_by_role("button", name="Select cards").is_visible()
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        # the stack columns follow the card size: at least S 140 / M 170 / L 240 px wide
        if width >= 600:
            for size_key, least in (("s", 140), ("m", 170), ("l", 240)):
                page.evaluate(f"() => document.documentElement.setAttribute('data-card-size', '{size_key}')")
                cols = page.evaluate(
                    "() => getComputedStyle(document.querySelector('.deckview.stacks')).gridTemplateColumns"
                    ".split(' ').map(parseFloat)"
                )
                assert len(cols) >= 2 and all(c >= least for c in cols), (size_key, cols)
        assert errors == [], errors
        browser.close()


RAIL = """
() => {
  const shown = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).display !== 'none';
  const bar = document.querySelector('.tabbar');
  return {tabbar: shown(bar), rail: shown(bar) && getComputedStyle(bar).flexDirection === 'column',
    more: shown(document.querySelector('.tabbar details.more')),
    links: [...document.querySelectorAll('.topbar nav.site > a')].filter(shown).length,
    search: shown(document.querySelector('.topbar .topsearch'))};
}
"""


@pytest.mark.parametrize(
    "width,touch,expect",
    [
        (599, True, "phone"),
        (600, True, "rail"),
        (1024, True, "rail"),
        (1279, True, "rail"),
        (1280, True, "top"),
        (810, False, "top"),
        (1024, False, "top"),
    ],
)
def test_the_navigation_rail_reaches_1279_px_on_a_touch_screen(
    server: Server, width: int, touch: bool, expect: str
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, exe, touch=touch)
        page.goto(f"{server.base}/search", wait_until="networkidle")
        got = page.evaluate(RAIL)
        if expect == "phone":
            assert got["tabbar"] and got["more"] and not got["rail"], got
        elif expect == "rail":
            assert got["rail"] and not got["more"] and got["links"] == 0 and got["search"], got
        else:
            assert not got["tabbar"] and got["links"] > 0, got
            assert got["search"] == (width >= 900), got
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        assert errors == [], errors
        browser.close()
