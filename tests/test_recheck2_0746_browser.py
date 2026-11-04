"""The last re-check of the 0.7.46 polish (findings batch 3 on a6d09a1), measured in headless
Chromium on the overflow sweep's hostile data: the Report's Deck tile facts never break inside a
fact, no dot floats before a chip in a facts line, a price nobody knows reads "unknown" the same
way everywhere (muted, normal weight, never louder than a real price), the editor's empty Add says
"Type a card name." as the deck page does, and a change's label is sentence case at every width.
Phone 360 and 412, the Fold 720 and desktop 1280, light and dark."""

from __future__ import annotations

import asyncio

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_overflow_sweep_browser import MEASURE, _seed, server  # noqa: F401
from .test_recheck_0746_browser import TOOL_ROWS, _balanced_facts
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

# a span's words, and whether it holds "unknown" in muted text at normal weight, no larger than the
# text around it (a price nobody knows: the one treatment, polish batch 3 N3)
UNPRICED = """sel => [...document.querySelectorAll(sel)].map(e => {
  const cs = getComputedStyle(e), ps = getComputedStyle(e.parentElement);
  return {where: e.parentElement.className, text: e.textContent.trim(), weight: cs.fontWeight,
    size: parseFloat(cs.fontSize), around: parseFloat(ps.fontSize), color: cs.color};
})"""


def _one_treatment(page, sel: str, *, at_most: float | None = None) -> list[dict]:  # noqa: ANN001
    got = page.evaluate(UNPRICED, sel)
    assert got, sel
    for g in got:
        assert g["text"] == "unknown", (sel, g)
        assert g["weight"] == "400", (sel, g)
        assert g["size"] <= (at_most or g["around"]) + 0.01, (sel, g)
    assert page.evaluate(CONTRAST, sel) == [], sel
    return got


@pytest.mark.parametrize("width,theme", [(360, "light"), (360, "dark"), (412, "dark"), (720, "light")])
def test_the_reports_deck_tile_facts_never_break_inside_a_fact(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """N1: "34 lands · Avg MV 3.2 · 0 game changers" was running text, so the phone's half-width
    tile broke it between "0" and "game changers". One nowrap fact a span, balanced (ui-dots)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/history/reports/r1", wait_until="networkidle")
        fold = page.locator("label.glance-lbl").first  # the tiles fold on a phone
        if fold.count() and fold.is_visible():
            fold.click()
        tile = page.locator(".ui-widgets .wdg", has=page.locator(".lab", has_text="Deck"))
        tile.scroll_into_view_if_needed()
        facts = tile.locator(".det .ui-dots.balanced")
        assert facts.count() == 1
        words = [t.strip() for t in facts.locator("> *").all_text_contents()]
        assert words[:3] == ["34 lands", "Avg MV 3.2", "0 game changers"], words
        _balanced_facts(page, ".ui-widgets .wdg .det .ui-dots.balanced")
        page.evaluate(TEXT_200)
        page.wait_for_timeout(150)
        _balanced_facts(page, ".ui-widgets .wdg .det .ui-dots.balanced")
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()


DOTS = """() => [...document.querySelectorAll('.ui-dots')].flatMap(p =>
  [...p.children].slice(1).map(c => {
    const b = getComputedStyle(c, '::before'), r = c.getBoundingClientRect();
    const line = parseFloat(getComputedStyle(c).lineHeight) || parseFloat(getComputedStyle(c).fontSize) * 1.4;
    const CHIP = '.ui-cap, .ui-chip, .ui-lbl, .btn, .btn-plain';
    return {text: c.textContent.trim().slice(0, 30),
      chip: c.matches(CHIP) || [...c.children].some(k => k.matches(CHIP)),
      dot: b.content.includes('·'), taller: r.height > line + 4};
  }))"""


@pytest.mark.parametrize("width,theme", [(720, "light"), (720, "dark"), (1280, "light"), (1280, "dark")])
def test_no_dot_floats_before_a_chip_in_a_facts_line(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """N2: the dot before the Watchlist's "Price alert defaults" chip sat at the chip's top edge,
    12 px above the words' baseline, because the dot is drawn at the top of the item it precedes
    and a chip is taller than a line. No separator before a chip, in the ui-dots rule itself."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        got = page.evaluate(DOTS)
        chips = [g for g in got if g["chip"]]
        assert any("Price alert defaults" in g["text"] for g in chips), got
        for g in got:
            # a chip takes no dot; a fact that is plain words keeps its own
            assert g["dot"] == (not g["chip"]), (g, got)
            assert not (g["dot"] and g["taller"]), (g, got)
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(360, "dark"), (720, "light"), (1280, "light"), (1280, "dark")])
def test_a_price_nobody_knows_reads_the_same_way_everywhere(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """N3 (and the audit's N7): the Binder said "unknown" in bold, the loudest text on its row,
    the Watchlist's change cells a bare "–", and the banner's word took its <b>'s weight. One
    treatment: the word, muted, normal weight, never larger than the text around it, 4.5:1."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        # the Binder: the Value tile and the list's Price column
        page.goto(f"{server.base}/collection?view=list", wait_until="networkidle")
        page.wait_for_timeout(2500)  # the Value tile follows the binder read (collection.js)
        fold = page.locator("label.glance-lbl").first
        if fold.count() and fold.is_visible():
            fold.click()
        big = page.locator(".wdg", has_text="Value").locator(".big")
        big_size = big.evaluate("e => parseFloat(getComputedStyle(e).fontSize)")
        _one_treatment(page, ".wdg .big .unpriced", at_most=big_size - 1)  # quieter than a real price
        if width >= 900:
            _one_treatment(page, ".ui-table .prc .unpriced")
        assert page.evaluate(MEASURE) == []
        # the Watchlist: the 7-day and 30-day change cells on a card with too little history
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        dashes = page.evaluate(
            "() => [...document.querySelectorAll('.w-t .chgs *, .w-t .px, .w-t .sub .px')]"
            ".filter(e => !e.children.length && e.textContent.trim() === '–').length"
        )
        assert dashes == 0
        if width >= 900:
            _one_treatment(page, ".w-t .chgs .unpriced")
        else:
            page.locator(".w-t .tr .more").first.click()
            page.locator(".w-t .wx:not([hidden]) .wx-chg").first.wait_for()
            _one_treatment(page, ".w-t .wx:not([hidden]) .wx-chg .unpriced")
        assert page.evaluate(MEASURE) == []
        # the deck banner keeps its own colour on the banner, with the same word and weight
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        banner = page.evaluate(UNPRICED, ".banner .unpriced")
        assert banner and banner[0]["text"] == "unknown" and banner[0]["weight"] == "400", banner
        assert banner[0]["color"] == "rgba(255, 255, 255, 0.72)", banner
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(360, "light"), (720, "dark"), (1280, "light")])
def test_the_editors_empty_add_says_type_a_card_name(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """N4: the editor's Add with nothing typed showed the browser's "Please fill out this
    field."; the deck page's quick add says "Type a card name." (A5). One shared note."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        box = page.locator("#addname")
        box.wait_for()
        page.locator("form.addcard .go button").click()
        assert box.evaluate("e => e.validationMessage") == "Type a card name."
        assert box.get_attribute("aria-invalid") == "true"
        assert box.evaluate("e => document.activeElement === e")
        assert page.url.endswith("/decks/42/edit")  # the form never left the page
        box.fill("Sol")
        assert box.evaluate("e => e.validationMessage") == "" and box.get_attribute("aria-invalid") is None
        # the deck page's own quick add says the same, from the same code
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        quick = page.locator("#quick")
        quick.focus()
        quick.press("Enter")
        assert quick.evaluate("e => e.validationMessage") == "Type a card name."
        assert errors == []
        browser.close()


def _edit_proposal(srv: Server) -> str:
    decks = srv.app.state.gateway.decks

    async def make() -> str:
        made = await decks.propose(
            "user-1",
            "42",
            [
                {"action": "add", "card_name": "Arcane Signet"},
                {"action": "remove", "card_name": "Counterspell"},
                {"action": "set_quantity", "card_name": "Forest", "quantity": 13},
            ],
        )
        return str(made["proposal_id"])

    return asyncio.run(make())


@pytest.mark.parametrize("width,theme", [(360, "light"), (720, "dark"), (1280, "light"), (1280, "dark")])
def test_a_changes_label_is_sentence_case_at_every_width(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """N5: the review page's row labels read "Change / Add / Remove" on a phone but "CHANGE /
    ADD / REMOVE" from 600 px (an uppercase transform the phone's rule undid). Sentence case,
    everywhere, on the review page and in the editor's box of changes."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    pid = _edit_proposal(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        for path in (f"/proposals/{pid}", "/decks/42/edit"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            if path.endswith("/edit"):
                page.locator("button[aria-label='One fewer Sol Ring']").first.click()
                page.locator("details.pendingbox summary").click()  # the box of changes is folded
                page.locator(".pending .changes .act").first.wait_for()
            labels = page.locator("ul.changes .act, .pending .changes .act")
            assert labels.count() >= 1, path
            got = labels.evaluate_all(
                "els => els.map(e => ({text: e.innerText.trim(), tr: getComputedStyle(e).textTransform,"
                " spacing: getComputedStyle(e).letterSpacing}))"
            )
            # innerText carries the transform: "Add", never "ADD"
            assert all(g["text"][:1].isupper() and g["text"][1:] == g["text"][1:].lower() for g in got), (
                path,
                got,
            )
            assert all(g["tr"] == "none" for g in got), (path, got)
            assert page.evaluate(CONTRAST, "ul.changes .act") == [], path
            assert page.evaluate(MEASURE) == [], path
        assert errors == []
        browser.close()


# -- the audit's rows on a6d09a1 (AUDIT-a6d09a1, section 3) ---------------------------------------
SHEET_PAGES = ["/collection", "/search", "/precons"]


@pytest.mark.parametrize("width,theme", [(360, "light"), (360, "dark"), (412, "light")])
def test_the_filters_sheets_clear_filters_hides_until_a_filter_is_set(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """C4's phone half (audit N11): the Filters sheet's foot showed Clear filters with nothing set
    on the Binder, Search and Precons, because the foot's button rule (display:inline-flex) beat
    .sp-clear[hidden]. Hidden on a fresh page; shown once a name is typed."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        for path in SHEET_PAGES:
            for query, shown in (("", False), ("?q=Sol", True)):
                page.goto(f"{server.base}{path}{query}", wait_until="networkidle")
                page.locator(".spanel .sp-open").click()
                page.locator("#sp-filters.open").wait_for()
                clear = page.locator("#sp-filters .sp-sheet-foot .sp-clear")
                assert clear.count() == 1, path
                assert clear.is_visible() == shown, (path, query)
                display = clear.evaluate("e => getComputedStyle(e).display")
                assert (display != "none") == shown, (path, query, display)
                page.locator("#sp-filters .sp-sheet-head .close").click()
                page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
            assert page.evaluate(MEASURE) == [], path
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(360, "dark"), (720, "light"), (1280, "light")])
def test_the_binders_grid_and_phone_lines_and_the_picker_say_unknown_too(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """Audit N7/N12 with polish N3: the Binder grid and the list under 900 px showed nothing for a
    price nobody knows, and the printing picker said "No price". The one word everywhere."""
    from playwright.sync_api import sync_playwright

    from .test_printing_picker_browser import _open_picker

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/collection?view=grid", wait_until="networkidle")
        assert page.locator("#cards .cc .pr .unpriced").count() >= 1
        _one_treatment(page, "#cards .cc .pr .unpriced")
        if width < 900:
            page.goto(f"{server.base}/collection?view=list", wait_until="networkidle")
            assert page.locator("#cards .tr .sub .pr .unpriced").count() >= 1
            _one_treatment(page, "#cards .tr .sub .pr .unpriced")
        assert page.evaluate(MEASURE) == []
        if width >= 720:
            page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
            _open_picker(page, page.locator(".deckview .row[data-card='Sol Ring'][data-zone='main']"))
            tiles = page.locator(".printpick .pptile .ppprice")
            assert tiles.count() >= 1
            assert "No price" not in page.locator(".printpick").inner_text()
            if page.locator(".printpick .pptile .ppprice.none").count():
                _one_treatment(page, ".printpick .pptile .ppprice.none")
        assert errors == []
        browser.close()


BALANCED = "no row holds one filter alone while another holds more"


def _rows_balanced(rows: list[list[str]]) -> bool:
    counts = [r.count("filter") for r in rows if "filter" in r]
    return not counts or not (min(counts) == 1 and max(counts) > 1)


@pytest.mark.parametrize("width,zoom", [(1280, "root"), (720, "root"), (1280, "none"), (360, "none")])
def test_the_activity_toolbars_wrap_as_every_list_does(
    server: Server,  # noqa: F811
    width: int,
    zoom: str,
) -> None:
    """Audit N9: the activity pages' own toolbar grid (10rem filter columns) left the search 0 px
    wide and the page 1395 px wide at 1280 px with 200% text. They take the shared ui-tools rules:
    nothing scrolls sideways, the search keeps its room, and the filters share rows evenly."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        for path in ("/admin/activity", "/activity"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            if zoom == "root":
                page.evaluate(ROOT_200)
                page.wait_for_timeout(150)
            assert page.evaluate(MEASURE) == [], (path, width, zoom)
            tools = page.evaluate(TOOL_ROWS)
            search = page.locator(".ui-tools .search input").bounding_box()
            assert search and search["width"] >= 200, (path, search)
            assert _rows_balanced(tools["rows"]), (path, BALANCED, tools)
            if width == 360 and path == "/activity":
                # a phone keeps My activity's three filters on one row (re-audit D31)
                assert tools["rows"][-1] == ["filter"] * 3, tools
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,zoom", [(717, "root"), (720, "none"), (1280, "root"), (412, "none")])
def test_historys_three_filters_never_leave_one_stretched_alone(
    server: Server,  # noqa: F811
    width: int,
    zoom: str,
) -> None:
    """Audit N10: /history at 717 px with 200% text wrapped its three filters two and one, the last
    stretched alone across the row. Three that do not fit one row take a row each."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "dark", exe)
        page.goto(f"{server.base}/history", wait_until="networkidle")
        if zoom == "root":
            page.evaluate(ROOT_200)
            page.wait_for_timeout(150)
        tools = page.evaluate(TOOL_ROWS)
        assert _rows_balanced(tools["rows"]), (BALANCED, tools)
        if zoom == "none":
            assert tools["rows"][-1] == ["filter"] * 3, tools  # all three share a row at 100%
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()
