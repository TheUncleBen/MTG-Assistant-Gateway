"""The report's two downloads (0.7.46). The HTML file is drawn in the current theme: the site's own
light and dark colours (following the reader's system setting), its labels, capsules, panels and
table headings, all inside the one file: no scripts, nothing fetched, no links into the gateway,
and a still spinner, since a saved file never updates. The Markdown file is clean Markdown: one
title, the research service's own output inside fences so its headings never mix with the
report's, each group of cards as its own paragraph, no HTML, and a pasted list or a comparison
named for what it is instead of a deck link that leads nowhere."""

from __future__ import annotations

import json
import re

import pytest

from mtg_gateway import report_view as rv
from mtg_gateway import theme
from mtg_gateway.companion import stats_strip

from .test_report_view import VALIDATION_PASSED, report
from .test_reports_browser import FORGE_DONE


def _doc(**over) -> str:
    r = report(validation=VALIDATION_PASSED, forge=json.loads(json.dumps(FORGE_DONE)), status="complete")
    r.update(over)
    return rv.report_export_html(r, stats_html=stats_strip(r.get("stats")), theme_css=theme.VIZ_CSS)


def test_the_html_file_carries_the_sites_colours_for_both_schemes() -> None:
    doc = _doc()
    css = doc.split("<style>", 1)[1].split("</style>", 1)[0]
    # the light set (the system's light setting) and the dark set, word for word from the theme
    assert "@media (prefers-color-scheme: light)" in css and theme._LIGHT.strip() in css
    for token in ("--link:#73a8dc", "--blue-text:#7fb0ff", "--card-border:#525252", "--radius-panel:3px"):
        assert token in css, token
    # the old export palette is gone
    assert "#2a66c9" not in css and "#a65400" not in css and "#146b3f" not in css
    # the labels are the site's own rules
    for rule in re.findall(r"(?m)^\.ui-lbl[^{]*\{[^}]*\}", theme.UI_CSS):
        assert rule in css, rule
    # a fold is the site's grey capsule, not a bare blue line
    assert re.search(r"details > summary\{[^}]*border-radius:22px", css), css
    assert "<meta name='color-scheme' content='light dark'>" in doc


def test_the_html_file_stays_one_file() -> None:
    doc = _doc()
    assert "<script" not in doc and "href='/" not in doc and " src=" not in doc and "@import" not in doc
    assert not re.search(r"url\((?!data:)", doc)
    # a run that was still going when it was saved keeps a still spinner: the file never updates
    running = _doc(forge={**json.loads(json.dumps(FORGE_DONE)), "state": "running"}, status="running")
    assert "class='ui-spin'" in running and "@keyframes" not in running
    css = running.split("<style>", 1)[1].split("</style>", 1)[0]
    spin = re.search(r"\.ui-spin\{[^}]*\}", css)
    assert spin and "animation" not in spin.group(0)


def _fences(md: str) -> list[str]:
    """The lines outside fenced blocks."""
    out, fence = [], ""
    for line in md.splitlines():
        m = re.match(r"^(`{3,})", line)
        if m and (not fence or m.group(1) == fence):
            fence = "" if fence else m.group(1)
            continue
        if not fence:
            out.append(line)
    return out


def test_the_markdown_is_clean() -> None:
    r = report(validation=VALIDATION_PASSED, forge=json.loads(json.dumps(FORGE_DONE)), status="complete")
    md = rv.report_markdown(r)
    outside = _fences(md)
    # one title; the research service's own output (# Goldfish run, ## Metrics) is not copied in:
    # the file says where it is kept (test_report_export_outline)
    assert [ln for ln in outside if ln.startswith("# ")] == ["# Deck report: Immortal Reckoning"]
    assert "## Metrics" not in md and "# Goldfish run" not in md and "## Raw output" in outside
    # no HTML outside the fences
    assert not [ln for ln in outside if re.search(r"<[a-z/][^>]*>", ln)], outside
    # each group of cards is its own paragraph, its count in bold
    group = "\n**Played without their effect: 32 of 98 cards (33% of the deck).**\n\n"
    assert group + rv.UNMODELED_TEXT in md
    assert f"\n**{rv.APPROX_HEADING}: 6.**\n\n{rv.APPROX_TEXT}\n\n- " in md
    # every heading has a blank line around it
    lines = md.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("#") and ln in outside and i:
            assert lines[i - 1] == "" and lines[i + 1] == "", (i, ln)
    # one win is a win
    assert "Precon 2: 1 win, 10%" in md and "1 wins" not in md
    assert "| Status | Complete |" in md


def test_a_pasted_list_or_a_comparison_says_so_in_markdown() -> None:
    r = report(status="complete")
    r.update(deck_id="", deck_name="Pasted list (Liesa)", source={"kind": "list", "label": "Pasted list"})
    md = rv.report_markdown(r)
    assert "archidekt.com/decks/" not in md and "| Source | Pasted list |" in md
    deck = report(status="complete")
    assert "| Deck | [Immortal Reckoning](https://archidekt.com/decks/42) |" in rv.report_markdown(deck)
    cmp = report(status="complete")
    cmp.update(
        deck_name="Immortal Reckoning vs Pasted list",
        source={"kind": "compare", "label": "Comparison"},
        comparison={"tool": "goldfish_ab", "ok": True, "text": "# A/B\n\n## Deltas\n- kill turn -1"},
    )
    md = rv.report_markdown(cmp)
    assert "| Source | Comparison |" in md and "## Comparison\n\n````text\n# A/B\n\n## Deltas" in md
    assert [ln for ln in _fences(md) if ln.startswith("# ")] == [
        "# Deck report: Immortal Reckoning vs Pasted list"
    ]


pytest.importorskip("playwright")

from .test_scan_browser import _chromium_path  # noqa: E402
from .test_ui_kit_browser import CONTRAST_JS  # noqa: E402


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [375, 840, 1280])
def test_the_html_file_reads_in_both_schemes_at_every_width(scheme: str, width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    doc = _doc()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
            page.set_content(doc)
            bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
            assert bg == ("rgb(249, 250, 251)" if scheme == "light" else "rgb(24, 24, 24)"), bg
            labels = page.evaluate(CONTRAST_JS)
            assert len(labels) >= 3, labels
            for name, ratio in labels:
                assert ratio >= 4.5, (name, round(ratio, 2))
            for text in ("100%", "200%"):
                page.evaluate(f"document.documentElement.style.fontSize = '{text}'")
                wide = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                assert wide <= 0, (text, wide)
        finally:
            browser.close()
