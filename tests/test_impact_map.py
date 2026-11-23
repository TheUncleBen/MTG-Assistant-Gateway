"""The impact map's check (tests/impact_map.py): a change must do every place its map lists and
nothing the map does not, and every surface must have been considered. And the dependency finder:
a change reaches the pages that use what it changed, through calls, CSS classes and scripts, and
not the pages that do not."""

from __future__ import annotations

import re

from . import impact_map as IM

MAP = """# Impact map: rename Torch to Flash
## Change
- [x] src/mtg_gateway/scan/static/scan.js: the button's word
- [x] docs/SCANNING.md: the guide's word
## Checked, no change
- [x] android/app/src/main/res/values/strings.xml: the app has no such button
## Surfaces
- [x] Web pages: /scan
- [x] Android app: none, the app shows the website's page
- [x] Claude and ChatGPT cards: none
- [x] Assistant tools: none
- [x] Plugin and skill: none
- [x] Docs: SCANNING.md
## Pages expected to look different
- /scan
"""
DIFF = ["src/mtg_gateway/scan/static/scan.js", "docs/SCANNING.md"]


def test_a_change_that_does_its_map_passes() -> None:
    assert IM.check(MAP, DIFF) == []
    assert IM.parse_map(MAP).expected_pages == {"/scan"}


def test_a_place_the_map_lists_and_the_change_left_is_a_miss() -> None:
    problems = IM.check(MAP, DIFF[:1])
    assert problems == ["missed: the map says to change docs/SCANNING.md, the diff does not"], problems


def test_a_file_the_map_does_not_list_is_scope_creep() -> None:
    problems = IM.check(MAP, [*DIFF, "src/mtg_gateway/theme.py"])
    assert problems == [
        "scope creep: the diff changes src/mtg_gateway/theme.py, the map does not list it under ## Change"
    ], problems


def test_every_surface_is_considered_and_every_box_ticked() -> None:
    text = MAP.replace("- [x] Android app: none, the app shows the website's page\n", "")
    text = text.replace("- [x] docs/SCANNING.md", "- [ ] docs/SCANNING.md")
    text = text.replace("- [x] Assistant tools: none", "- [x] Assistant tools:")
    text = text.replace(": the app has no such button", "")
    problems = IM.check(text, DIFF)
    assert "not ticked: docs/SCANNING.md" in problems
    assert any(p.startswith("surface not considered: Android app") for p in problems)
    assert any(p.startswith("surface Assistant tools has no words") for p in problems)
    assert any(p.startswith("checked, no change, but no reason given") for p in problems)


def test_the_finder_lists_every_surface() -> None:
    out = IM.find(["Flash"])
    for name, _ in IM.SURFACES:
        assert re.search(rf"^## {re.escape(name)}: ", out, re.M), name


def test_a_changed_helper_reaches_its_callers_pages_and_no_others() -> None:
    """ui.back_link is drawn by the Binder and the Account pages, never by Home: a change to it
    reaches them, through the call graph read from the code (not words in strings)."""
    found = IM.propagate({"mtg_gateway.ui": {"back_link"}}, set())
    assert "back_link" in found["mtg_gateway.ui"]
    assert "mtg_gateway.binder_hub" in found and "mtg_gateway.account_hub" in found
    rt = IM.routes()
    templates = {
        t
        for m, hit in found.items()
        for handler, t in rt.get(m, ())
        if "*" in hit or handler in hit or any(u.startswith("add_") for u in hit)
    }
    pages = IM.pages_for(templates)
    assert "/binder" in pages and "/account/details" in pages
    assert "/" not in pages, sorted(pages)


def test_a_dict_get_is_not_a_call_to_every_get() -> None:
    """A common method name (get, read, apply) is followed only where its class is named, so a
    change to one service's get() does not reach every page that reads a dict."""
    found = IM.propagate({"mtg_gateway.reports": {"ReportService.get"}}, set())
    assert "mtg_gateway.ui" not in found, sorted(found)


def test_the_words_a_member_reads_are_found_in_a_removed_line() -> None:
    """The left-behind check reads labels and messages out of removed lines: a quoted phrase of two
    or more words, or one capitalised word of five or more letters; not code names."""
    line = """+ form("read_all", "Mark all read", "check") + "<h2>Alerts</h2>" + 'x' + "set_quantity" """
    assert IM.PHRASE_RE.findall(line) == ["Mark all read", "Alerts"]
