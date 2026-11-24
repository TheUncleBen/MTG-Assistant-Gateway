"""The loop round's shared pieces (docs/UI-STANDARD.md): what ui.py's helpers write and what the
shared sheet says, without a browser. The layout itself is measured in
tests/test_loop_round_shared_browser.py."""

from __future__ import annotations

import re
from pathlib import Path

from mtg_gateway import theme, ui

SRC = Path(theme.__file__).parent
STANDARD = (SRC.parents[1] / "docs" / "UI-STANDARD.md").read_text(encoding="utf-8")
CSS = theme.CSS + theme.UI_CSS


def _rule(selector: str, css: str = CSS) -> str:
    """The declarations of the first rule whose selector list is exactly ``selector``."""
    m = re.search(r"(?:^|[}\s])" + re.escape(selector) + r"\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


# --- #2 the purpose line -------------------------------------------------------------------------


def test_a_head_with_actions_writes_its_purpose_line_right_after_the_title() -> None:
    head = ui.page_head("Cards", "What the page holds.", actions_html="<a class='btn'>Add</a>")
    assert re.search(r"<h1>Cards</h1><p class='ui-sub'>What the page holds\.</p><span class='acts'>", head)
    assert head.endswith("</span></div>")
    # without actions nothing changes: the line follows the head
    assert ui.page_head("Cards", "What the page holds.") == (
        "<div class='ui-head'><h1>Cards</h1></div><p class='ui-sub'>What the page holds.</p>"
    )
    # from 600 px the actions sit beside the title and the line goes under both
    assert "@media (min-width:600px){ .ui-head > .ui-sub{order:1" in CSS


# --- #10 chip rows -------------------------------------------------------------------------------


def test_a_chip_row_scrolls_only_below_600px() -> None:
    assert "flex-wrap:nowrap" in _rule(".ui-chips")
    assert "@media (min-width:600px){ .ui-chips{flex-wrap:wrap;overflow-x:visible} }" in CSS


# --- #11 selects ---------------------------------------------------------------------------------


def test_a_pick_lists_text_never_wraps() -> None:
    txt = _rule(".msel-btn .msel-txt")
    assert "white-space:nowrap" in txt and "text-overflow:ellipsis" in txt and "overflow:hidden" in txt
    # a filter is sized by its value, 9rem at least, so the row wraps whole controls
    f = _rule(".ui-tools .f")
    assert "flex:1 1 auto" in f and "min-width:min(100%,9rem)" in f
    assert ".ui-tools.n3 .f{flex:1 1 calc(33.333%" not in CSS


# --- #12 tags ------------------------------------------------------------------------------------


def test_a_tag_is_one_line_and_the_helper_gives_it_its_title() -> None:
    tag = _rule(".ui-tag,.pill")
    for decl in ("max-width:100%", "white-space:nowrap", "overflow:hidden", "text-overflow:ellipsis"):
        assert decl in tag, decl
    got = ui.tag("landfall & ramp", icon_name="tag")
    assert got.startswith("<span class='ui-tag' title='landfall &amp; ramp'>") and "<svg" in got
    assert got.endswith("landfall &amp; ramp</span>")
    # no page writes a bare tag without its title
    for path in sorted(SRC.glob("*.py")):
        # (pages.py: a review's summary sentences, which wrap: .summary-pills)
        if path.name in ("deckpage.py", "companion_tools.py", "pages.py"):
            continue
        for m in re.finditer(
            r"<span class='(?:ui-tag|pill)[^']*'([^>]*)>\{", path.read_text(encoding="utf-8")
        ):
            assert "title=" in m.group(1), (path.name, m.group(0))


# --- #13 a tile's label --------------------------------------------------------------------------


def test_a_tiles_label_wraps_beside_its_icon() -> None:
    tile = ui.widget("Played with their effect", "stats", 3)
    assert re.search(
        r"<span class='lab'><svg[^>]*>.*?</svg><span class='t'>Played with their effect</span></span>", tile
    )
    assert "flex:none" in _rule(".wdg .lab svg.i")
    words = _rule(".wdg .lab > .t")
    assert "flex:1 1 0" in words and "min-width:min-content" in words


# --- #18 a list's heading ------------------------------------------------------------------------


def test_a_list_heads_side_is_a_centred_row() -> None:
    side = _rule(".list-head > .ui-sub")
    assert "display:flex" in side and "align-items:center" in side and "gap:.5rem" in side
    assert "align-items:center" in _rule(".list-head")


# --- #22 a switch's parts on a phone -------------------------------------------------------------


def test_a_one_row_switch_has_equal_parts_on_a_phone() -> None:
    phone = r"@media \(max-width:599\.98px\)\{\s*"
    assert re.search(phone + r"\.hubseg\.seg-row > \*\{flex:1 1 0!important;min-width:min-content;", CSS)


# --- #33 Alerts ----------------------------------------------------------------------------------


def test_alerts_asks_for_the_narrow_column() -> None:
    src = (SRC / "alerts_page.py").read_text(encoding="utf-8")
    assert 'current="/notifications",\n            heading=False,\n            main_class="ui-narrow",' in src
