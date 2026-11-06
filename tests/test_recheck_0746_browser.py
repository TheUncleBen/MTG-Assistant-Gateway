"""The re-check of the 0.7.46 polish (findings batch 2 and the audit's N rows), measured in headless
Chromium on the overflow sweep's hostile data: the Like count's ink on the heart's ink centre, the
goldfish status line wrapping without a dangling dot or a lone fact, the Binder's Clear filters
hidden until a filter is set, the lock note one sentence on a phone, the banner's "unknown" price
at 4.5:1 in both themes, the Watchlist's Price alert defaults chip one line, the group row's Open
deck as far in as its tick box, the Proposals filters sharing rows beside the open panel, Stop
watching with an icon of its own, and the Binder's unknown prices in words. Phone 360 and 412,
the Fold 720 and desktop 1280, light and dark."""

from __future__ import annotations

import io
import re

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import SIZES, _chromium, _page
from .test_editor_browser import PNG
from .test_overflow_sweep_browser import MEASURE, _seed, server  # noqa: F401
from .test_polish_geometry_browser import RECT, _muted
from .test_suggest_browser import Server
from .test_text_zoom_browser import TEXT_200
from .waits import expect, redrawn

pytest.importorskip("playwright")

PHONES = [(360, "light"), (360, "dark"), (412, "light"), (412, "dark")]


def _hires(p, srv: Server, sid: str, width: int, theme: str, exe: str | None, scale: int):  # noqa: ANN001
    """As _page (the theme cookie, the image route), at ``scale`` device pixels per CSS pixel, so
    a screenshot can measure glyph ink."""
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(
        viewport={"width": width, "height": 860}, has_touch=True, is_mobile=True, device_scale_factor=scale
    )
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": sid, "url": srv.base},
            {"name": "mtg_theme", "value": theme, "url": srv.base},
        ]
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


def _ink_centre(png: bytes, box: list[float], scale: int, *, light_ink: bool) -> float:
    """The vertical centre, in CSS px, of the glyph ink inside ``box`` (CSS px): the rows that hold
    a pixel brighter (``light_ink``) or darker than the box's ground."""
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("L")
    left, top, right, bottom = (int(round(v * scale)) for v in box)
    rows = [
        y
        for y in range(top, bottom)
        if any((im.getpixel((x, y)) > 160) == light_ink for x in range(left, right))
    ]
    assert rows, box
    return (rows[0] + rows[-1] + 1) / 2 / scale


@pytest.mark.parametrize("width,theme", PHONES)
def test_the_like_counts_ink_sits_on_the_hearts_centre_on_a_phone(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """B1: the boxes were centred, but a digit's ink sits above its line box's centre (cap height,
    no descender), so the "0" read 2.5 px high beside the heart. Measured on the ink at 3×."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _hires(p, server, sid, width, theme, exe, 3)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        soc = page.locator(".banner .social .soc[data-social=vote]")
        assert soc.get_attribute("aria-pressed") == "false"  # white ink on the dark chip
        heart, digit, chip = (
            soc.locator("svg").first.evaluate(RECT),
            soc.locator("b.n").evaluate(RECT),
            soc.evaluate(RECT),
        )
        png = page.screenshot(clip={"x": 0, "y": 0, "width": width, "height": 860})
        heart_ink = _ink_centre(png, heart[:4], 3, light_ink=True)
        digit_ink = _ink_centre(png, digit[:4], 3, light_ink=True)
        assert abs(heart_ink - digit_ink) <= 1, (heart_ink, digit_ink, heart, digit)
        assert abs(digit_ink - (chip[1] + chip[3]) / 2) <= 1.5, (digit_ink, chip)
        assert chip[4] >= 44 and errors == []
        browser.close()


# each fact of a dotted line: its box, the line's content edge, and where its dot is drawn
FACTS = """e => {
  const p = e.getBoundingClientRect(), cs = getComputedStyle(e);
  const clip = (cs.clipPath.match(/inset\\(.*?([\\d.]+)(px|rem)?\\)/) || [])[1];
  const edge = p.left + (parseFloat(cs.paddingLeft) || 0) + (clip ? parseFloat(clip) : 0);
  return [...e.children].map(c => {
    const r = c.getBoundingClientRect(), b = getComputedStyle(c, '::before');
    const dl = r.left + (parseFloat(b.left) || 0);
    return {text: c.textContent.trim(), left: Math.round(r.left), right: Math.round(r.right),
      top: Math.round(r.top), width: Math.round(r.width), edge: Math.round(edge),
      dot: b.content.includes('·'),
      dotLeft: Math.round(dl), dotRight: Math.round(dl + (parseFloat(b.width) || 0)),
      lines: c.getClientRects().length};
  });
}"""


def _lines(facts: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for f in facts:
        if lines and abs(lines[-1][0]["top"] - f["top"]) < 4:
            lines[-1].append(f)
        else:
            lines.append([f])
    return lines


def _balanced_facts(page, sel: str) -> list[list[dict]]:  # noqa: ANN001
    """A facts line whose dots sit between its facts, never at a line's end or start, with no
    fact alone on a line while another line holds three or more."""
    facts = page.locator(sel).evaluate(FACTS)
    assert len(facts) >= 2 and all(f["lines"] == 1 for f in facts), facts
    assert not facts[0]["dot"] and all(f["dot"] for f in facts[1:]), facts
    lines = _lines(facts)
    for line in lines:
        first = line[0]
        # a line's first fact sits on the line's edge and its dot is outside, where the line clips it
        assert abs(first["left"] - first["edge"]) <= 1, (first, facts)
        if first["dot"]:
            assert first["dotRight"] <= first["edge"], (first, facts)
        for prev, f in zip(line, line[1:], strict=False):
            assert prev["right"] <= f["dotLeft"] and f["dotRight"] <= f["left"], (prev, f)
    if len(lines) > 1:
        assert not (any(len(x) == 1 for x in lines) and any(len(x) >= 3 for x in lines)), [
            [f["text"] for f in x] for x in lines
        ]
    return lines


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_goldfish_status_wraps_without_a_dangling_dot_or_a_lone_fact(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """C1: "Opening hand · Library 92 · Hand 7 · Graveyard 0 ·" ended the line and "Exile 0" sat
    alone under it. The facts wrap as balanced lines with the dots in the gaps, at 200% too."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/42/goldfish", wait_until="networkidle")
        status = page.locator("#gf-status")
        assert "Opening hand" in status.inner_text() and "Exile 0" in status.inner_text()
        assert page.evaluate(MEASURE) == []
        lines = _balanced_facts(page, "#gf-status")
        if width == 360:
            assert len(lines) >= 2, lines  # the phone does wrap this line: the check is real
        page.evaluate(TEXT_200)
        redrawn(page)
        _balanced_facts(page, "#gf-status")
        assert page.evaluate(MEASURE) == []
        # the game keeps the line up to date through the same spans
        page.locator(".gf-controls [data-act=keep]").click()
        expect(status).to_contain_text("Turn 1")
        assert "Turn 1" in status.inner_text()
        _balanced_facts(page, "#gf-status")
        assert errors == []
        browser.close()


CLEARS = """() => [...document.querySelectorAll('a, button')]
  .filter(e => /clear filters/i.test(e.textContent) && e.getClientRects().length && e.offsetParent)
  .map(e => e.className)"""


@pytest.mark.parametrize("width,theme", [(360, "light"), (720, "dark"), (1280, "light")])
def test_the_binders_clear_filters_hides_until_a_filter_is_set(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """C4: the results bar's Clear filters on the Binder showed at 720 and 1280 with nothing
    filtered, sorted or viewed (the bar's link was not one the panel's script hides). A name,
    the only filter outside the sheet, counts as a filter too."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        for path in ("/collection", "/collection?view=list", "/collection?sort=name"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            assert page.evaluate(CLEARS) == [], path
            assert all(c.evaluate("e => e.hidden") for c in page.locator(".spanel .sp-clear").all()), path
        page.goto(f"{server.base}/collection?q=Sol", wait_until="networkidle")
        if width >= 600:
            assert page.evaluate(CLEARS) != [], "a name filter is set: Clear filters shows"
        assert not any(c.evaluate("e => e.hidden") for c in page.locator(".spanel .sp-clear").all())
        # typing a name shows it, clearing the box hides it again
        page.goto(f"{server.base}/collection", wait_until="networkidle")
        page.locator("#col-q").fill("Sol")
        page.wait_for_function(
            "() => [...document.querySelectorAll('.spanel .sp-clear')].every(c => !c.hidden)"
        )
        assert not any(c.evaluate("e => e.hidden") for c in page.locator(".spanel .sp-clear").all())
        page.locator("#col-q").fill("")
        page.wait_for_function(
            "() => [...document.querySelectorAll('.spanel .sp-clear')].every(c => c.hidden)"
        )
        assert all(c.evaluate("e => e.hidden") for c in page.locator(".spanel .sp-clear").all())
        assert errors == []
        browser.close()


# every text fragment of the lock note, line by line, with the icon's box and the font size
NOTE = """e => {
  const out = [], w = document.createTreeWalker(e, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    if (!n.textContent.trim() || !n.parentElement.getClientRects().length
        || getComputedStyle(n.parentElement).display === 'none') continue;
    const r = document.createRange(); r.selectNodeContents(n);
    for (const q of r.getClientRects()) if (q.width > 0)
      out.push({left: Math.round(q.left), right: Math.round(q.right), top: Math.round(q.top),
                bottom: Math.round(q.bottom), text: n.textContent.trim().slice(0, 12)});
  }
  out.sort((a, b) => a.top - b.top || a.left - b.left);
  const svg = e.querySelector('svg'), s = svg && svg.getBoundingClientRect();
  return {frags: out, icon: s && [s.left, s.top, s.right, s.bottom].map(Math.round),
          em: parseFloat(getComputedStyle(e).fontSize)};
}"""


@pytest.mark.parametrize("width,theme", [(360, "light"), (412, "dark"), (720, "light")])
def test_the_lock_note_reads_as_one_sentence(server: Server, width: int, theme: str) -> None:  # noqa: F811
    """R1: p.guard-hint is a flex row, so "Locked for a moment:" and the touch sentence became two
    columns with a gap between them on every waiting proposal. The sentence flows as one text
    with the lock icon on its first line."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/proposals/{pending}", wait_until="domcontentloaded")
        note = page.locator("form.guarded .choice .guard-hint")
        note.wait_for(state="visible")
        assert "Locked for a moment: tap once to unlock" in re.sub(r"\s+", " ", note.inner_text())
        got = note.evaluate(NOTE)
        frags, em = got["frags"], got["em"]
        assert len(frags) >= 2, got
        for prev, f in zip(frags, frags[1:], strict=False):
            if abs(f["top"] - prev["top"]) < 4:
                assert 0 <= f["left"] - prev["right"] <= 0.6 * em, (prev, f, em)  # one space apart
            else:
                assert f["left"] == frags[0]["left"], (frags[0], f)  # a new line starts under the first
        # the icon sits on the first line, before its first word
        first = frags[0]
        icon = got["icon"]
        assert (
            icon and icon[2] <= first["left"] and first["top"] <= (icon[1] + icon[3]) / 2 <= first["bottom"]
        ), (
            icon,
            first,
        )
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()


def _lum(c: tuple[float, float, float]) -> float:
    v = []
    for x in c:
        x /= 255
        v.append(x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4)
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]


def _ratio(fg: tuple[float, float, float], bg: tuple[float, float, float]) -> float:
    a, b = _lum(fg), _lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _over(color: str, bg: tuple[int, int, int]) -> tuple[float, float, float]:
    """A computed colour (rgb or rgba) as drawn over ``bg``."""
    parts = [float(x) for x in re.findall(r"[\d.]+", color)]
    alpha = parts[3] if len(parts) > 3 else 1.0
    return tuple(parts[i] * alpha + bg[i] * (1 - alpha) for i in range(3))  # type: ignore[return-value]


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_banners_unknown_price_reads_at_4_5_to_1_in_both_themes(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """R2 (the audit's N1): "unknown" used the page's muted grey, 1.5:1 on the banner in the light
    theme. It takes a colour of the banner's own, measured against the pixels drawn beside the
    word and at the shade's lightest end."""
    from PIL import Image
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        est = page.locator(".banner .row .unpriced")
        assert est.inner_text() == "unknown"
        color = est.evaluate("e => getComputedStyle(e).color")
        word, row = (
            est.evaluate(RECT),
            est.locator("xpath=ancestor::*[contains(@class, 'row')][1]").evaluate(RECT),
        )
        banner = page.locator(".banner").evaluate(RECT)
        im = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        # the shade on the word's line, in the banner's side gutters (left of the row's words and
        # at its far end, where the shade is lightest): no glyph's edge can be sampled there
        y = (word[1] + word[3]) // 2
        left, right = max(banner[0], row[0] - 6), min(banner[2], row[2] + 6) - 1
        samples = {x: im.getpixel((x, y)) for x in (left, right)}
        for x, bg in samples.items():
            ratio = _ratio(_over(color, bg), bg)
            assert ratio >= 4.5, (x, y, bg, color, ratio)
        # the label beside it keeps reading at 4.5:1 too
        label = page.locator(".banner .row").first.evaluate("e => getComputedStyle(e).color")
        assert all(_ratio(_over(label, bg), bg) >= 4.5 for bg in samples.values()), samples
        assert color != _muted(page), color  # the banner's own colour, not the page's muted grey
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(360, "light"), (412, "dark"), (720, "light")])
def test_the_watchlists_price_alert_defaults_chip_is_one_line(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """R3 (the audit's N5): the chip's cell took the chip's width less its 4 px side margins, and
    the chip's max-width (100%) then squeezed its words onto two lines."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        chip = page.locator(".w-status a.ui-cap", has_text="Price alert defaults")
        assert chip.count() == 1
        got = chip.evaluate(
            "a => { const tops = new Set(), w = document.createTreeWalker(a, NodeFilter.SHOW_TEXT); let n;"
            " while ((n = w.nextNode())) { if (!n.textContent.trim()) continue;"
            "   const r = document.createRange(); r.selectNodeContents(n);"
            "   for (const q of r.getClientRects()) if (q.width) tops.add(Math.round(q.top)); }"
            " return {lines: tops.size, fits: a.scrollWidth <= a.clientWidth + 1,"
            " height: a.getBoundingClientRect().height, cell: a.parentElement.getBoundingClientRect().width,"
            " chip: a.getBoundingClientRect().width}; }"
        )
        assert got["lines"] == 1 and got["fits"] and got["height"] <= 46, got
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()


GROUP_ROW = """() => {
  const row = document.querySelector('.ui-table.ui-rows .tr.group');
  const cap = row.querySelector('.act .ui-cap'), cs = getComputedStyle(cap);
  const r = cap.getBoundingClientRect(), rr = row.getBoundingClientRect();
  const tick = row.querySelector('input[type=checkbox]').getBoundingClientRect();
  const keb = document.querySelector('.ui-table.ui-rows .tr:not(.group) .act .kebab svg')
    .getBoundingClientRect();
  return {pillRight: rr.right - (r.right - parseFloat(cs.borderRightWidth)),
          tickLeft: tick.left - rr.left, kebabRight: rr.right - keb.right,
          pillHeight: r.height, top: r.top - rr.top, bottom: rr.bottom - r.bottom};
}"""


@pytest.mark.parametrize("width,theme", [(412, "light"), (720, "dark"), (1280, "light")])
def test_the_groups_open_deck_sits_as_far_in_as_the_tick_box(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """R4: the actions cell pulls 8 px out of the row's padding so a ⋮'s icon lands 14 px from the
    edge; the group's Open deck capsule got the same pull and touched the border. Its drawn edge
    now sits where the ⋮ icon and the tick box do."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/proposals", wait_until="networkidle")
        g = page.evaluate(GROUP_ROW)
        assert abs(g["pillRight"] - g["tickLeft"]) <= 2.5, g
        assert abs(g["pillRight"] - g["kebabRight"]) <= 1, g
        assert g["pillHeight"] >= 36 and g["top"] >= 0 and g["bottom"] >= 0, g
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()


TOOL_ROWS = """() => {
  const f = document.querySelector('.ui-tools'), rows = [];
  for (const c of [...f.children].filter(e => e.getClientRects().length)) {
    const r = c.getBoundingClientRect(), kind = c.classList.contains('search') ? 'search' : 'filter';
    const last = rows[rows.length - 1];
    if (last && Math.abs(last.top - r.top) < 4) last.items.push(kind);
    else rows.push({top: r.top, items: [kind]});
  }
  return {width: Math.round(f.getBoundingClientRect().width), rows: rows.map(r => r.items)};
}"""


@pytest.mark.parametrize("width", [1100, 1280, 1440])
def test_the_proposals_filters_share_rows_beside_the_open_panel(
    server: Server,  # noqa: F811
    width: int,
) -> None:
    """R5: a rule on the window's width put the search on its own line only on a Fold; beside an
    open side panel the list is as narrow, so four of the five boxes shared a row and "Any time"
    sat alone on the next. The rule follows the toolbar's own width."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        for path in (f"/proposals/{pending}", "/proposals"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            if path != "/proposals":
                assert page.locator(".ui-split.with-panel").count() == 1
            got = page.evaluate(TOOL_ROWS)
            counts = [row.count("filter") for row in got["rows"] if "filter" in row]
            assert 1 not in counts, (path, got)  # never one filter alone on a row
            assert max(counts) - min(counts) <= 1, (path, got)  # the rows of filters are balanced
            assert page.evaluate(MEASURE) == []
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(360, "light"), (412, "dark")])
def test_the_watchlist_bars_stop_watching_has_an_icon_of_its_own(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """The audit's N4: with its word folded away on a narrow bar, Stop watching was a red × beside
    Cancel's grey ×: a destructive action easy to take for Cancel."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        page.locator("button.w-select").click()
        bar = page.locator(".ui-bar[aria-label='Selected cards']")
        bar.wait_for(state="visible")
        cancel, stop = bar.locator("button.w-clear"), bar.locator("a[data-box-open='w-stop']")
        glyph = "e => e.querySelector('svg').innerHTML"
        assert cancel.evaluate(glyph) != stop.evaluate(glyph)
        assert stop.evaluate("e => getComputedStyle(e.querySelector('.lbl')).display") == "none"
        assert (
            stop.get_attribute("aria-label") == "Stop watching"
            and stop.get_attribute("title") == "Stop watching"
        )
        assert stop.bounding_box()["height"] >= 44 and stop.bounding_box()["width"] >= 44
        # the row's menu says it with the same icon
        page.keyboard.press("Escape")
        page.locator(".w-t .tr .kebab").first.click()
        item = page.locator(".ctxmenu[role=menu] [role=menuitem]", has_text="Stop watching")
        item.wait_for(state="visible")
        assert item.evaluate(glyph) == stop.evaluate(glyph)
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(360, "dark"), (1280, "light")])
def test_the_binders_unknown_prices_say_so_in_words(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
) -> None:
    """The audit's N7: the Value tile and the list's price column showed a bare "–" for a price
    nobody knows, while the deck banner says "unknown". The same word, muted, at 4.5:1."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/collection?view=list", wait_until="networkidle")
        expect(page.locator("[data-value-reading]")).to_have_count(0)  # the Value tile followed the read
        dashes = page.evaluate(
            "() => [...document.querySelectorAll('main *')]"
            ".filter(e => !e.children.length && e.textContent.trim() === '–' && e.getClientRects().length)"
            ".map(e => e.className + ' in ' + e.parentElement.className)"
        )
        assert dashes == [], dashes
        value = page.locator(".wdg", has_text="Value").locator(".big .unpriced")
        assert value.inner_text() == "unknown"
        prices = page.locator(".ui-table .prc .unpriced")
        assert prices.count() >= 1 and prices.first.evaluate("e => e.textContent.trim()") == "unknown"
        assert page.evaluate(CONTRAST, ".wdg .big .unpriced, .ui-table .prc .unpriced") == []
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()
