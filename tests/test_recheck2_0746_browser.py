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


# every cell of the activity tables' rows: its right edge against the page's and its row's
ACT_CELLS = """() => {
  const page = document.documentElement.clientWidth;
  return [...document.querySelectorAll('.act-table .tr.ev')].flatMap(row => {
    const r = row.getBoundingClientRect();
    return [...row.children].filter(c => !c.hidden && c.getClientRects().length).map(c => {
      const b = c.getBoundingClientRect();
      return {cell: c.className, right: Math.round(b.right), page, row: Math.round(r.right),
        inside: b.right <= page + 1 && b.right <= r.right + 1 && b.left >= r.left - 1};
    });
  });
}"""


@pytest.mark.parametrize("width", [900, 1024, 1280])
def test_the_activity_tables_fold_their_columns_at_200_percent_text(
    server: Server,  # noqa: F811
    width: int,
) -> None:
    """Audit N9/P2: at 200% text (a 28 px root) /admin/activity was 1200 px wide at 900 and
    1024 px viewports and /activity 972 px at 900: the When and Actions columns spilled past
    the page. The rows now follow the table's own width in its text: nothing scrolls sideways
    and every cell stays inside its row and the page."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        for path in ("/admin/activity", "/activity"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            page.evaluate(ROOT_200)
            page.wait_for_timeout(150)
            assert page.locator(".act-table .tr.ev").count() >= 1, path
            assert page.evaluate(MEASURE) == [], (path, width)
            width_now = page.evaluate("() => document.documentElement.scrollWidth")
            assert width_now <= width, (path, width_now)
            cells = page.evaluate(ACT_CELLS)
            spilt = [c for c in cells if not c["inside"]]
            assert cells and not spilt, (path, width, spilt)
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(360, "light"), (360, "dark"), (1280, "light"), (1280, "dark")])
def test_try_again_in_the_held_strip_is_a_button_not_an_underlined_link(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """Reviewer's P1 on 5625c3b: the held strip's Try again (busy.held_html, an a.btn in a
    .notice) was underlined in the notice's orange, because .notice a{color:inherit;
    text-decoration:underline} beat the button's own look. A button in a notice keeps its colour
    and no underline, at 44 px, at a phone and a desktop in both themes."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/collection", wait_until="networkidle")  # read and kept
        server.ark.fail_collection_reads = True
        try:
            page.goto(f"{server.base}/collection", wait_until="networkidle")
            again = page.locator(".notice.held a.btn", has_text="Try again")
            assert again.count() == 1
            got = again.evaluate(
                "e => { const cs = getComputedStyle(e); const probe = document.createElement('a');"
                " probe.className = 'btn'; probe.textContent = 'x'; document.body.appendChild(probe);"
                " const own = getComputedStyle(probe); const out = {deco: cs.textDecorationLine,"
                " color: cs.color, own: own.color, strip: getComputedStyle(e.parentElement).color,"
                " height: e.getBoundingClientRect().height}; probe.remove(); return out; }"
            )
            assert got["deco"] == "none", got
            assert got["color"] == got["own"] and got["color"] != got["strip"], got
            assert got["height"] >= 44, got
            assert page.evaluate(CONTRAST, ".notice.held, .notice.held a.btn") == []
            assert page.evaluate(MEASURE) == []
            # a plain link in a notice keeps its underline and the notice's colour
            plain = page.evaluate(
                "() => { const n = document.querySelector('.notice.held');"
                " const a = document.createElement('a'); a.href = '/'; a.textContent = 'x'; n.appendChild(a);"
                " const cs = getComputedStyle(a); const out = {deco: cs.textDecorationLine, color: cs.color,"
                " strip: getComputedStyle(n).color}; a.remove(); return out; }"
            )
            assert plain["deco"] == "underline" and plain["color"] == plain["strip"], plain
        finally:
            server.ark.fail_collection_reads = False
        assert errors == []
        browser.close()


def _icon_path(name: str) -> str:
    """The first path of a theme icon, as Chromium reads its ``d`` back."""
    import re

    from mtg_gateway.theme import ICONS

    return re.search(r"d='([^']+)'", ICONS[name]).group(1)


@pytest.mark.parametrize("width,theme", [(360, "light"), (360, "dark")])
def test_the_binders_phone_sheet_is_titled_by_what_it_holds(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """Audit: the Binder's phone sheet was titled "Filters" when it held only the card size.
    With no filter groups (no tags) the button and the sheet say "View"; with a tag list to
    filter by they say "Filters", as the Search and Precons sheets do."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        for tags, word in ((False, "View"), (True, "Filters")):
            server.ark.collection_tag_lists["alice"] = (
                {21: {"id": 21, "name": "trade binder page 12", "color": "#d33"}} if tags else {}
            )
            page.goto(f"{server.base}/collection", wait_until="networkidle")
            opener = page.locator(".spanel .sp-open")
            assert opener.inner_text().strip() == word, (tags, opener.inner_text())
            # the View button is no filter: it carries the eye, not the funnel (re-check S4)
            drawn = opener.locator("svg path").first.get_attribute("d")
            assert drawn == _icon_path("eye" if word == "View" else "filter"), (word, drawn)
            opener.click()
            page.locator("#sp-filters.open").wait_for()
            assert page.locator("#sp-filters .sp-sheet-head h2").inner_text() == word
            close = page.locator("#sp-filters .sp-sheet-head .close").bounding_box()
            assert close["width"] >= 44 and close["height"] >= 44, close  # a 44 px tap target (B5)
            assert page.locator("#sp-filters .sp-sheet-head .close").get_attribute("aria-label") == (
                f"Close {word.lower()}"
            )
            assert (page.locator("#sp-filters select[name=tag]").count() == 1) == tags
            assert page.locator("#sp-filters .sheet-size").count() == 1  # the card size is in it either way
            # with only the card size in it, a bottom sheet the height of its content, its foot
            # inside it; with groups the full screen (polish re-check Q5)
            sheet, tall = page.locator("#sp-filters").bounding_box(), page.evaluate("innerHeight")
            foot = page.locator("#sp-filters .sp-sheet-foot").bounding_box()
            show = page.locator("#sp-filters .sp-sheet-foot .sp-show")
            assert show.is_visible() and show.bounding_box()["height"] >= 44
            assert abs(sheet["y"] + sheet["height"] - tall) <= 1, (sheet, tall)
            if tags:
                assert sheet["y"] <= 1 and sheet["height"] >= tall - 1, (sheet, tall)
            else:
                assert sheet["height"] <= tall * 0.5, (sheet, tall)
                assert foot["y"] + foot["height"] <= sheet["y"] + sheet["height"] + 1, (foot, sheet)
                assert page.locator("#sp-filters").evaluate("e => e.scrollHeight <= e.clientHeight + 1")
            assert page.evaluate(CONTRAST, "#sp-filters .sp-sheet-head h2") == []
            assert page.evaluate(MEASURE) == []
            page.locator("#sp-filters .sp-sheet-head .close").click()
            page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(360, "light"), (360, "dark"), (1280, "light"), (1280, "dark")])
def test_a_price_nobody_knows_reads_unknown_on_the_report_and_in_the_card_viewer(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """Audit N7/N12 (the reviewer's P2 on 5625c3b): a deck with no priced cards still read "No
    price" on the report's Deck tile and on the companion's deck card (the report's statistics
    tiles, companion.stats_strip), and the card viewer's price line said "no price". All three say
    the shared muted "unknown": normal weight, no larger than the text around it, 4.5:1."""
    from playwright.sync_api import sync_playwright

    from .test_history_page import store_report

    exe = _chromium()
    sid, _pending = _seed(server)
    store_report(
        server.db, "user-1", "r2", deck_name="Nothing priced", stats={"price_total": 0.0, "priced_cards": 0}
    )
    with server.db.tx() as c:  # a watch of Sol Ring with no price recorded yet
        c.execute(
            "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, scryfall_id, "
            "alert_pct, created_at) VALUES ('w9', 'user-1', 'Sol Ring', 'cmm', '1234', 'nonfoil', 'id-9', "
            "12.5, 0)"
        )
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/history/reports/r2", wait_until="networkidle")
        deck = page.locator(".rv-page .wdg", has_text="Deck").first
        assert "Price unknown" in deck.inner_text() and "$0" not in deck.inner_text(), deck.inner_text()
        _one_treatment(page, ".rv-page .wdg .ui-dots .unpriced")
        tile = page.locator(".rv-dstats .tile", has_text="Price")
        # the label keeps its capitals (CSS); the word never takes them
        assert tile.inner_text().split() == ["unknown", "PRICE"], tile.inner_text()
        _one_treatment(page, ".rv-dstats .tile .unpriced")
        assert "No price" not in page.content() and "$0.00" not in page.content()
        assert page.evaluate(MEASURE) == []
        # the card viewer's Price history: a watch with no price yet
        page.goto(f"{server.base}/cards?q=Sol Ring", wait_until="networkidle")
        page.locator(".cardgrid .pic[data-card='Sol Ring']").first.click()
        page.wait_for_selector(".cardview.open", timeout=3000)
        page.get_by_role("button", name="Price history").click()
        box = page.locator("#cardview-prices")
        line = box.locator("p", has_text="Nonfoil: unknown")
        line.wait_for(timeout=5000)
        # one muted line, the word in the shared treatment, and no second line repeating it (A1)
        assert line.inner_text() == "CMM #1234 \u00b7 Nonfoil: unknown \u00b7 comes with the next daily check"
        assert "muted" in (line.get_attribute("class") or "").split()
        assert line.evaluate("e => getComputedStyle(e).color") == line.evaluate(
            "e => getComputedStyle(e.querySelector('.unpriced')).color"
        )
        _one_treatment(page, "#cardview-prices .unpriced")
        assert page.evaluate(CONTRAST, "#cardview-prices p") == []
        said = box.inner_text()
        assert "No price yet" not in said and "no price" not in said.lower(), said
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()


# a button in a notice: its words on one line, the box inside the notice, never over the notice's
# own words (it goes under them when they leave it no room)
NOTICE_BUTTONS = """sel => [...document.querySelectorAll(sel)].map(b => {
  const r = document.createRange(); r.selectNodeContents(b);
  const n = b.closest('.notice'), nb = n.getBoundingClientRect(), bb = b.getBoundingClientRect();
  const words = [...n.childNodes].filter(x => x.nodeType === 3 && x.textContent.trim()).flatMap(t => {
    const q = document.createRange(); q.selectNodeContents(t); return [...q.getClientRects()]; });
  const meets = words.some(t => Math.min(t.right, bb.right) - Math.max(t.left, bb.left) > 0
    && Math.min(t.bottom, bb.bottom) - Math.max(t.top, bb.top) > 0);
  return {text: b.textContent.trim(), lines: r.getClientRects().length, height: bb.height, meets,
    inside: bb.left >= nb.left - 1 && bb.right <= nb.right + 1};
})"""


@pytest.mark.parametrize("width,zoom", [(360, "none"), (412, "none"), (360, "text"), (412, "text")])
def test_a_button_in_a_notice_stays_whole_and_goes_under_the_words(
    server: Server,  # noqa: F811
    width: int,
    zoom: str,
) -> None:
    """Polish re-check Q1: on the held Binder at 412 px "Try again" broke mid-word into
    "Try / agai / n" (the notice's overflow-wrap:anywhere reached into the button). A button in a
    notice keeps its words on one line, inside the notice and never over its words, at 360 and
    412 px and with 200% text, on the held Binder and the held deck page."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    decks = server.app.state.gateway.decks
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        page.goto(f"{server.base}/collection", wait_until="networkidle")  # read and kept
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        server.ark.fail_collection_reads = True
        server.ark.fail_deck_reads = True
        # the deck's kept copy, too old to show: the read fails and the last read is held
        decks._page_decks = {k: (v[0] - 7 * 3600, v[1]) for k, v in decks._page_decks.items()}
        try:
            for path in ("/collection", "/decks/42"):
                page.goto(f"{server.base}{path}", wait_until="networkidle")
                if zoom == "text":
                    page.evaluate(TEXT_200)
                    page.wait_for_timeout(150)
                got = page.evaluate(NOTICE_BUTTONS, ".notice.held .btn")
                assert [g["text"] for g in got] == ["Try again"], (path, got)
                for g in got:
                    assert g["lines"] == 1 and g["inside"] and not g["meets"], (path, width, zoom, g)
                    assert g["height"] >= 44, (path, g)
                assert page.evaluate(MEASURE) == [], (path, width, zoom)
        finally:
            server.ark.fail_collection_reads = False
            server.ark.fail_deck_reads = False
        assert errors == []
        browser.close()


# a notice with a button: its content width, its words' line boxes and the button's box
NOTICE_ROOM = """sel => [...document.querySelectorAll(sel)].map(n => {
  const cs = getComputedStyle(n), nb = n.getBoundingClientRect();
  const content = n.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const words = [...n.childNodes].filter(x => x.nodeType === 3 && x.textContent.trim()).flatMap(t => {
    const q = document.createRange(); q.selectNodeContents(t);
    return [...q.getClientRects()].map(r => [Math.round(r.left), Math.round(r.right), Math.round(r.top),
      Math.round(r.bottom)]); });
  const b = n.querySelector('.btn'), bb = b.getBoundingClientRect();
  const r = document.createRange(); r.selectNodeContents(b);
  return {content: Math.round(content), words, height: Math.round(nb.height),
    btn: {left: Math.round(bb.left), right: Math.round(bb.right), top: Math.round(bb.top),
      bottom: Math.round(bb.bottom), lines: r.getClientRects().length}};
})"""


def _room_kept(got: list[dict]) -> None:
    """The notice's words keep at least 60% of its width as a block, never a column (at most one
    line but the last narrower than 40%), the button's words stay on one line, and the button
    sits after the words on their last line or under them, never over them."""
    for n in got:
        assert n["words"] and n["btn"]["lines"] == 1, n
        block = max(w[1] for w in n["words"]) - min(w[0] for w in n["words"])
        assert block >= 0.6 * n["content"], n
        narrow = [w for w in n["words"][:-1] if w[1] - w[0] < 0.4 * n["content"]]
        assert len(narrow) <= 1, n
        beside = [w for w in n["words"] if min(w[3], n["btn"]["bottom"]) - max(w[2], n["btn"]["top"]) > 0]
        for w in beside:
            assert w[1] <= n["btn"]["left"] + 1, n  # on its line, after the words
        if not beside:
            assert n["btn"]["top"] >= max(w[3] for w in n["words"]) - 1, n


@pytest.mark.parametrize(
    "width,zoom", [(320, "none"), (360, "none"), (412, "none"), (320, "root"), (360, "root"), (412, "root")]
)
def test_a_notices_words_keep_their_room_beside_or_above_its_button(
    server: Server,  # noqa: F811
    width: int,
    zoom: str,
) -> None:
    """Audit B1: at 200% text (a 28 px root) the held Binder's strip, a flex row, squeezed its
    words into a 73 px column at 360 px (34 px at 320) beside a button that would not give. The
    words keep their row (at least 60% of the notice, the whole of it when the button drops
    under them) and the button stays whole: the busy strip of a page never read, the held Binder
    and the held deck page, at 320, 360 and 412 px, at 100% and 200% text."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    decks = server.app.state.gateway.decks
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)

        def strips(path: str) -> list[dict]:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            if zoom == "root":
                page.evaluate(ROOT_200)
                page.wait_for_timeout(150)
            got = page.evaluate(NOTICE_ROOM, ".notice.held")
            assert len(got) == 1, (path, got)
            assert page.evaluate(MEASURE) == [], (path, width, zoom)
            return got

        try:
            server.ark.fail_collection_reads = True
            busy = strips("/collection")  # never read: the busy strip (busy.busy_body)
            server.ark.fail_collection_reads = False
            page.goto(f"{server.base}/collection", wait_until="networkidle")  # read and kept
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            server.ark.fail_collection_reads = True
            server.ark.fail_deck_reads = True
            decks._page_decks = {k: (v[0] - 7 * 3600, v[1]) for k, v in decks._page_decks.items()}
            held = strips("/collection") + strips("/decks/42")
        finally:
            server.ark.fail_collection_reads = False
            server.ark.fail_deck_reads = False
        _room_kept(busy + held)
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(360, "light"), (360, "dark")])
def test_a_tap_on_the_sheets_backdrop_closes_it_and_reaches_nothing_under_it(
    server: Server,  # noqa: F711, F811
    width: int,
    theme: str,
) -> None:
    """Audit B2: the View sheet's dim backdrop was a box-shadow, so a tap on it went through to the
    page (it opened Add cards) and left the sheet open. A real backdrop takes the tap, closes the
    sheet and gives the focus back to the View button; Escape still closes it."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    server.ark.collection_tag_lists["alice"] = {}
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/collection", wait_until="networkidle")
        url = page.url
        add = page.locator("a.btn-primary, .btn-primary", has_text="Add cards").first
        target = add.bounding_box()
        opener = page.locator(".spanel .sp-open")
        opener.click()
        page.locator("#sp-filters.open").wait_for()
        scrim = page.locator(".sp-scrim")
        assert scrim.count() == 1 and scrim.is_visible()
        assert (
            page.evaluate(CONTRAST, "#sp-filters .sp-sheet-head h2, #sp-filters .sp-sheet-foot .sp-show")
            == []
        )
        # a tap over Add cards lands on the backdrop
        page.mouse.click(target["x"] + target["width"] / 2, target["y"] + target["height"] / 2)
        page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
        page.wait_for_timeout(300)
        assert page.url == url, page.url  # Add cards (a link to ?add=1) was not followed...
        assert page.locator(".mtgbox[role=dialog]:not([hidden])").count() == 0  # ...nor its box opened
        assert page.locator(".sp-scrim").count() == 0
        assert page.evaluate(
            "() => document.activeElement && document.activeElement.className"
        ) and opener.evaluate("e => e === document.activeElement")
        # Escape closes it too, with the focus back on the button
        opener.click()
        page.locator("#sp-filters.open").wait_for()
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
        assert page.locator(".sp-scrim").count() == 0 and opener.evaluate("e => e === document.activeElement")
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()


# every tile detail line on the page: its text nodes' own words (a dot drawn as text would be
# here) and, when it is a facts line, each fact's rectangle against the line's box
TILE_FACTS = """() => [...document.querySelectorAll('.wdg .det')].map((d) => {
  const own = [...d.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('');
  const line = d.querySelector('.tx.ui-dots.balanced');
  const box = (line || d).getBoundingClientRect();
  return {tile: d.parentElement.querySelector('.lab').textContent.trim(), own, text: d.textContent.trim(),
    facts: line ? [...line.children].map((c) => {
      const r = c.getBoundingClientRect(), b = getComputedStyle(c, '::before');
      return {text: c.textContent.trim(), left: Math.round(r.left), right: Math.round(r.right),
        lines: c.getClientRects().length, dot: b.content.includes('·')};
    }) : null,
    left: Math.round(box.left), right: Math.round(box.right)};
})"""

# pages with a tile whose detail is facts set apart by dots, and a tile each that has them
DOTTED_TILES = [
    ("/admin/activity", "Last 24 hours"),
    ("/activity", "Last 7 days"),
    ("/proposals", "Waiting for you"),
    ("/proposals", "Last 7 days"),
    ("/history", "Snapshots kept"),
    ("/admin", "Members"),
]


@pytest.mark.parametrize("width,zoom", [(1280, "none"), (360, "none"), (360, "root")])
def test_a_tiles_facts_never_leave_a_dot_at_the_end_of_a_line(
    server: Server,  # noqa: F811
    width: int,
    zoom: str,
) -> None:
    """Re-check S5: /admin/activity's "Last 24 hours" tile at 1280 broke "From 1 member · Website
    1 · / Other 2" with the dot left at the line's end. A tile's facts are one span each (ui.facts)
    and the dots are drawn by the style before a fact, so a dot goes with its fact to the next
    line; no tile's detail holds a dot as text. At 200% text on a phone a fact can be wider than
    the tile: it wraps inside its span rather than being cut off."""
    from playwright.sync_api import sync_playwright

    from .test_recheck_0746_browser import _balanced_facts

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        for path, tile in DOTTED_TILES:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            fold = page.locator("label.glance-lbl").first  # the tiles fold on a phone
            if fold.count() and fold.is_visible():
                fold.click()
            if zoom == "root":
                page.evaluate(ROOT_200)
                page.wait_for_timeout(200)
            dets = page.evaluate(TILE_FACTS)
            assert all(d["right"] > d["left"] for d in dets), (path, dets)  # shown, with a size
            assert all("·" not in d["own"] for d in dets), (path, [d for d in dets if "·" in d["own"]])
            mine = [d for d in dets if d["tile"] == tile]
            assert len(mine) == 1 and mine[0]["facts"] and len(mine[0]["facts"]) >= 2, (path, tile, dets)
            got = mine[0]
            assert not got["facts"][0]["dot"] and all(f["dot"] for f in got["facts"][1:]), got
            for f in got["facts"]:  # whole where it fits, never cut off at the tile's edge
                assert f["left"] >= got["left"] - 1 and f["right"] <= got["right"] + 1, (path, f, got)
                if f["lines"] > 1:  # wrapped inside only because it is wider than the line
                    assert f["right"] - f["left"] >= got["right"] - got["left"] - 2, (path, f, got)
            if width == 1280:  # every fact fits: the dots sit between the facts, the lines balanced
                i = [d["tile"] for d in dets].index(tile)
                _balanced_facts(page, f".wdg .det >> nth={i} >> .tx.ui-dots.balanced")
        assert errors == []
        browser.close()
