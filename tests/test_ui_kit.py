"""The UI standard's shared pieces (ui.py, theme.UI_CSS): §4 labels from one table, the app chip,
the §3 date rule and its 7-day boundary, widgets, chips, the toolbar, the table, paging, the
selection bar and the side panel."""

from __future__ import annotations

import calendar
import re

from mtg_gateway import theme

NOW = calendar.timegm((2026, 10, 19, 15, 36, 0))
MIN, HOUR, DAY = 60, 3600, 86400

# UI-STANDARD.md v1.3 §4, word for word: label, colour, icon
STATUS_TABLE = [
    ("waiting", "Waiting", "orange", "clock"),
    ("needs_look", "Needs a look", "red", "alert"),
    ("held", "Held", "orange", "pause"),
    ("updated", "Updated", "blue", "refresh"),
    ("applying", "Applying", "blue", "spinner"),
    ("applied", "Applied", "green", "check"),
    ("rejected", "Rejected", "grey", "x"),
    ("withdrawn", "Withdrawn", "grey", "back"),
    ("replaced", "Replaced", "grey", "arrows"),
    ("expired", "Expired", "grey", "hourglass"),
    ("failed", "Failed", "red", "alert"),
]
RISK_TABLE = [
    ("low", "Low", "green", "shield"),
    ("high", "High", "orange", "triangle"),
    ("your_press", "Your press", "blue", "hand"),
    ("delete", "Delete", "red", "trash"),
]
NEW_ICONS = (
    "clock",
    "alert",
    "pause",
    "hourglass",
    "shield",
    "triangle",
    "hand",
    "spinner",
    "arrows",
    "back",
    "bell",
    "send",
)


def test_new_icons_are_drawn_in_the_theme_style() -> None:
    for name in NEW_ICONS:
        body = theme.ICONS[name]
        assert body.startswith("<") and "fill=" not in body, name
        # arc flags spaced like the theme's own paths ("0 0 1"), never packed ("013")
        assert not re.search(r"a[\d.]+ [\d.]+ \d+ \d\d", body), name
        assert theme.icon(name).startswith("<svg class='i' viewBox='0 0 24 24' aria-hidden='true'>")


def test_ui_css_is_in_the_site_stylesheet_without_clashing_names() -> None:
    assert theme.UI_CSS in theme.CSS
    # names the theme or a page already uses for something else are not restyled: no selector
    # starts with one (inside .ui-table, say, .meta is the table's own)
    for clash in ("lbl", "app", "when", "chip", "chips", "chg", "locked", "sr", "meta", "pill"):
        start = rf"(?m)(^\s*|[,}}]\s*)(\w+)?{re.escape('.' + clash)}[\s{{,:.\[]"
        assert not re.search(start, theme.UI_CSS), clash
    for cls in (
        "ui-wide",
        "ui-head",
        "ui-sub",
        "ui-lbl",
        "ui-app",
        "ui-when",
        "ui-tabs",
        "ui-chips",
        "ui-chip",
        "ui-tools",
        "ui-table",
        "ui-bar",
        "ui-pager",
        "ui-split",
        "ui-drawer",
        "ui-meta",
        "ui-chg",
        "ui-next",
        "ui-widgets",
        "wdg",
        "ui-card",
        "ui-row",
        "ui-checks",
        "btn-outline-danger",
        "btn-plain",
        "linkbtn",
        "adm-nav",
    ):
        assert f".{cls}" in theme.UI_CSS, cls


def test_render_main_class() -> None:
    plain = theme.render("T", "<p>x</p>", site="S").body.decode()
    assert "<main class='wrap' id='main' tabindex='-1'>" in plain
    wide = theme.render("T", "<p>x</p>", site="S", main_class="ui-wide").body.decode()
    assert "<main class='wrap ui-wide' id='main' tabindex='-1'>" in wide
