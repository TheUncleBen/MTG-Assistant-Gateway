"""The report's two downloads to the gateway's standard (0.7.46 review, finding F7): the HTML file
draws its links in the text's colour, never browser blue; the Markdown file lists one card per
line with the commander marked, uses real plurals, and names the research service's output for
what it is."""

from __future__ import annotations

import re

import pytest

from mtg_gateway import report_view as rv
from mtg_gateway import theme
from mtg_gateway.companion import stats_strip

from .test_report_export_outline import URL, _report


def _html(r: dict) -> str:
    return rv.report_export_html(
        r, stats_html=stats_strip(r.get("stats")), theme_css=theme.VIZ_CSS, report_url=URL
    )


def test_markdown_lists_one_card_per_line_and_marks_the_commander() -> None:
    md = rv.report_markdown(_report(), report_url=URL)
    body = md.split(f"## {rv.MODEL_HEADING}", 1)[1].split("\n## ", 1)[0]
    # the gateway's own groups: a bullet per card, so "Ajani, Caller of the Pride" is one card
    assert "- Ajani, Caller of the Pride\n" in body and "Aegis of the Gods, Ajani" not in body
    assert "- Liesa, Forgotten Archangel (commander)\n" in body
    assert "\n- Removal and other interaction:\n  - Avatar of Woe\n" in body
    assert "\n- Black Dragon Gate\n- Charcoal Diamond\n" in body
    # no line in these groups joins several names: 32 cards played without their effect are 32 lines
    unmodeled = body.split(f"**{rv.UNMODELED_HEADING}", 1)[1].split("\n**", 1)[0]
    assert sum(1 for ln in unmodeled.splitlines() if ln.startswith("- ")) == 32
    # Forge's leftovers too, one per line
    solo = md.split(f"## {rv.FORGE_SOLO_HEADING}", 1)[1].split("\n## ", 1)[0]
    assert "- Cards Forge could not play:\n  - Mirror of the Forebears\n  - Grist, the Hunger Tide\n" in solo
    # the HTML file: a capsule per card, not one comma-joined line
    doc = _html(_report())
    assert "Cards Forge could not play:<ul class='names'><li>Mirror of the Forebears</li>" in doc
    assert "Mirror of the Forebears, Grist" not in doc


def test_no_bracketed_plural_in_either_download() -> None:
    md = rv.report_markdown(_report(), report_url=URL)
    doc = _html(_report())
    assert "card(s)" not in md and "(s)" not in md
    assert "card(s)" not in doc and "(s) " not in doc
    assert "- 2 cards not found on Scryfall:\n  - Enduring Angel" in md
    assert rv.plain_words("1 card(s) not found") == "1 card not found"


def test_the_research_output_section_is_named_for_what_it_is() -> None:
    md = rv.report_markdown(_report(), report_url=URL)
    doc = _html(_report())
    assert "Raw output" not in md and "Raw output" not in doc
    assert f"## {rv.RAW_HEADING}\n\nThe research service's own output is kept with the report: {URL}" in md
    assert f"<summary class='fold-btn'>{rv.RAW_HEADING}</summary>" in doc
    # no heading without a body under it
    lines = md.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("## "):
            assert i + 2 < len(lines) and lines[i + 2].strip(), ln


def test_the_html_files_links_take_the_text_colour() -> None:
    """The deck name and Open the report, drawn by a browser in light and dark: the text's colour,
    never the browser's or the theme's link blue."""
    from .test_consent_browser import _chromium_or_skip, _launch

    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    doc = _html(_report())
    assert re.search(r"(?m)^a\{color:var\(--text\)", doc.split("<style>", 1)[1]), "the export's own link rule"
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            for scheme in ("light", "dark"):
                ctx = browser.new_context(viewport={"width": 375, "height": 800}, color_scheme=scheme)
                page = ctx.new_page()
                page.set_content(doc)
                got = page.evaluate(
                    """() => {
                      const body = getComputedStyle(document.body).color;
                      return [...document.querySelectorAll('.meta a')]
                        .map(a => [a.textContent, getComputedStyle(a).color, body]);
                    }"""
                )
                assert len(got) == 2, got
                for text, colour, body in got:
                    assert colour == body, (scheme, text, colour, body)
                ctx.close()
        finally:
            browser.close()


# Each shown fold summary of the file whose drawn chevron (its ::after) is under .3 of its words'
# size (the site's crawl check (20), for the saved file, which carries its own CSS)
SMALL_CHEVRONS = """() => [...document.querySelectorAll('details > summary')]
  .filter(s => s.getClientRects().length)
  .map(s => [s.textContent.trim().slice(0, 30), parseFloat(getComputedStyle(s, '::after').width),
    parseFloat(getComputedStyle(s).fontSize), getComputedStyle(s, '::after').borderRightStyle])
  .filter(([, w, fs, line]) => line === 'solid' && w > 0 && w < 0.3 * fs - 0.5)
  .map(([t, w, fs]) => t + ': ' + w.toFixed(1) + 'px chevron beside ' + fs + 'px words')"""


def test_the_html_files_fold_chevrons_grow_with_the_text() -> None:
    """Polish AG1/AH1: the saved file's folds drew a 6 px chevron with a 2 px line at every text
    size, about a fifth of their words at 200% text. It grows with the words, as the site's do,
    and stays 6 px at 100%."""
    from .test_consent_browser import _chromium_or_skip, _launch

    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    doc = _html(_report())
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            ctx = browser.new_context(viewport={"width": 320, "height": 800})
            page = ctx.new_page()
            page.set_content(doc)
            assert page.locator("details > summary").count(), "the file has folds"
            sizes = page.evaluate(
                "() => [...document.querySelectorAll('details > summary')]"
                ".map(s => parseFloat(getComputedStyle(s, '::after').width))"
            )
            assert sizes and all(abs(w - 6) < 0.6 for w in sizes), sizes
            page.evaluate(
                "() => document.documentElement.style.setProperty('font-size', '200%', 'important')"
            )
            assert page.evaluate(SMALL_CHEVRONS) == []
            ctx.close()
        finally:
            browser.close()


# a word of letters (and an apostrophe) that the browser split across two lines
BROKEN_WORDS = """() => {
  const out = [];
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = tw.nextNode(); n; n = tw.nextNode()) {
    const el = n.parentElement;
    if (!el || !el.getClientRects().length || el.closest('pre, code')) continue;
    for (const m of n.textContent.matchAll(/[A-Za-z\u00c0-\u024f']{2,}/g)) {
      const r = document.createRange();
      r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
      const tops = new Set([...r.getClientRects()].filter(x => x.width > 0).map(x => Math.round(x.top)));
      if (tops.size < 2) continue;
      // name the cell's table and its label too (CI found "commander" in a td, nothing more)
      const t = el.closest('table'), c = el.closest('td, th');
      const lbl = c && c.dataset.label ? ' [' + c.dataset.label + ']' : '';
      const where = t ? ' (table.' + t.className.replace(/ /g, '.') + lbl + ')' : '';
      out.push('"' + m[0] + '" in ' + el.tagName.toLowerCase() + '.' + el.className + where);
    }
  }
  return out;
}"""


def test_the_html_files_words_stay_whole_on_a_small_phone_at_large_text() -> None:
    """Polish AI2: at 320 px with 200% text the page's, the card's and a list's indents left a
    list's words about 150 px, and "Commander" broke inside the word ("Comman / der cast"). The
    indents are capped by the window's width, so every word keeps its line."""
    from .test_consent_browser import _chromium_or_skip, _launch

    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    doc = _html(_report())
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            ctx = browser.new_context(viewport={"width": 320, "height": 800})
            page = ctx.new_page()
            # the page's own fonts, then two wider ones: CI's runner found a break ("commander" in a
            # stacked table's cell) that this machine's narrower font did not
            for font in ("", "DejaVu Sans", "Liberation Sans"):
                page.set_content(doc)
                if font:
                    page.add_style_tag(content=f"html, body, body * {{font-family: '{font}' !important}}")
                page.evaluate(
                    "() => { document.documentElement.style.setProperty('font-size', '200%', 'important');"
                    " document.querySelectorAll('details').forEach(d => { d.open = true; }); }"
                )
                assert page.evaluate("() => /Commander cast/.test(document.body.textContent)")
                assert page.evaluate(BROKEN_WORDS) == [], font or "the page's fonts"
            ctx.close()
        finally:
            browser.close()


# the file's markup hooks that carry no style of their own, in the file or on the site: section
# names (report-head, forge, forge-tiles, unmodeled, coverage), the results tables' (qty, seats,
# rate; styled through .forge-results table) and the chart's text parts (v, x, y)
HOOKS = {
    "coverage",
    "forge",
    "forge-tiles",
    "qty",
    "rate",
    "report-head",
    "seats",
    "unmodeled",
    "v",
    "x",
    "y",
}


def test_the_html_file_defines_every_class_it_uses() -> None:
    """Polish AI3: the saved file used .sr-only for the results table's caption but had no rule for
    it, so the caption showed as text, one word a line at 320 px. Every class the file uses is in
    its own CSS, or is a hook that no global rule on the site styles either."""
    doc = _html(_report())
    css = "".join(re.findall(r"<style[^>]*>(.*?)</style>", doc, re.S))
    body = re.sub(r"<style[^>]*>.*?</style>", "", doc, flags=re.S)
    used: set[str] = set()
    for value in re.findall(r"class=['\"]([^'\"]*)['\"]", body):
        used |= set(value.split())
    defined = set(re.findall(r"\.([A-Za-z_][\w-]*)", css))
    assert used - defined - HOOKS == set()
    # no site rule starts at a hook: not ".hook", "tag.hook", ".other.hook", nor ".hook h3" (polish
    # on c0b79f8d: ".disclosure h3" and "details.disclosure" were missed); ".changes .qty", another
    # part's own rule for a hook inside it, is not about the file's markup
    site = theme.CSS + theme.UI_CSS
    for hook in HOOKS:
        starts = rf"(?:^|[,}}]\s*)[a-z]*(?:\.[\w-]+)*\.{re.escape(hook)}(?![\w-])"
        assert not re.search(starts, site, re.M), hook
    # the site's rules for a disclosure's content are in the file, the same
    for rule in re.findall(r"(?m)^(?:details)?\.disclosure(?: [^{]*)?\{[^}]*\}", theme.CSS):
        assert rule in css, rule


STACK_SPILL = """() => {
  const out = [];
  for (const td of document.querySelectorAll('table.stack tbody td, table.stack tbody th')) {
    if (td.getClientRects().length && td.scrollWidth > td.clientWidth + 1)
      out.push((td.dataset.label || td.textContent.trim().slice(0, 20)) + ': ' + td.scrollWidth + ' of ' +
        td.clientWidth + 'px in table.' + td.closest('table').className.replace(/ /g, '.'));
  }
  for (const s of document.querySelectorAll('.scroll'))
    if (s.scrollWidth > s.clientWidth + 1)
      out.push('a results table scrolls ' + (s.scrollWidth - s.clientWidth) + 'px');
  return out;
}"""


def test_the_html_files_stacked_results_keep_their_values_in_the_card() -> None:
    """Polish AI4: at 320 px with 200% text a stacked results card pushed its values right, past
    its edge ("17% to 69%" lost "to", "life total 3, commander damage 2" was cut). A value goes under
    its label when there is no room beside it, from the left, as the site's cards do."""
    from .test_consent_browser import _chromium_or_skip, _launch

    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    doc = _html(_report())
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            ctx = browser.new_context(viewport={"width": 320, "height": 800})
            page = ctx.new_page()
            for font in ("", "DejaVu Sans", "Liberation Sans"):
                page.set_content(doc)
                if font:
                    page.add_style_tag(content=f"html, body, body * {{font-family: '{font}' !important}}")
                page.evaluate(
                    "() => document.documentElement.style.setProperty('font-size', '200%', 'important')"
                )
                assert page.locator("table.stack td").count()
                assert page.evaluate(STACK_SPILL) == [], font or "the page's fonts"
                caption = page.locator("table.seats caption")
                assert caption.count() and caption.evaluate("e => e.getBoundingClientRect().height") <= 1
            ctx.close()
        finally:
            browser.close()


HEADING_SIZES = """() => ['h1', 'h2', 'h3'].map(t => [...document.querySelectorAll(t)]
  .filter(e => e.getClientRects().length).map(e => parseFloat(getComputedStyle(e).fontSize)))"""


@pytest.mark.parametrize("width", [320, 412, 600, 900, 1280])
def test_the_html_files_headings_keep_their_order_at_every_size(width: int) -> None:
    """Polish AK1: at 320 px with 200% text the file's h1 and h2 came down to 32 px each and h3 was
    35 px. Each level stays larger than the next at every width and text size."""
    from .test_consent_browser import _chromium_or_skip, _launch

    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    doc = _html(_report())
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            ctx = browser.new_context(viewport={"width": width, "height": 800})
            page = ctx.new_page()
            for size in ("100%", "150%", "200%"):
                page.set_content(doc)
                page.evaluate(
                    "(z) => { document.documentElement.style.setProperty('font-size', z, 'important');"
                    " document.querySelectorAll('details').forEach(d => { d.open = true; }); }",
                    size,
                )
                h1, h2, h3 = page.evaluate(HEADING_SIZES)
                assert h1 and h2 and h3, (size, h1, h2, h3)
                assert min(h1) > max(h2) and min(h2) > max(h3), (size, h1, h2, h3)
            ctx.close()
        finally:
            browser.close()


FOLD_RULES = """() => {
  const css = e => getComputedStyle(e), size = e => parseFloat(css(e).fontSize);
  const body = size(document.body);
  const h = document.querySelector('.disclosure h3'), li = document.querySelector('.disclosure li');
  const cards = [...document.querySelectorAll('details.card.disclosure')].map(css);
  return [h && size(h) / body, li && parseFloat(css(li).lineHeight) / size(li), cards.length,
    cards.every(s => s.borderTopColor === s.borderLeftColor)];
}"""


def test_the_html_files_folds_follow_the_sites_rules_and_cards_keep_their_frame() -> None:
    """Polish on c0b79f8d: the file had none of the site's rules for a disclosure's content, so its
    heading was 1.1 of the text (the site's 1) and its lines 1.35 high (the site's 1.45). They are
    the site's; a fold that is a card keeps the card's frame, its top edge the colour of the rest."""
    from .test_consent_browser import _chromium_or_skip, _launch

    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    doc = _html(_report())
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 412, "height": 900})
            page.set_content(doc)
            page.evaluate("() => document.querySelectorAll('details').forEach(d => { d.open = true; })")
            got = page.evaluate(FOLD_RULES)
        finally:
            browser.close()
    h3, line, cards, framed = got
    assert h3 is not None and abs(h3 - 1) < 0.01, got
    assert line is not None and abs(line - 1.45) < 0.01, got
    assert cards and framed, got
