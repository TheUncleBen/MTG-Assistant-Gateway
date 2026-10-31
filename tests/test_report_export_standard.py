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
