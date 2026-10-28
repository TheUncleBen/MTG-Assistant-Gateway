"""The shared dashboard and list pieces (ui.py, theme.UI_CSS): status labels from one table, the app chip,
the list date rule and its 7-day boundary, widgets, chips, the toolbar, the table, paging, the
selection bar and the side panel."""

from __future__ import annotations

import calendar
import re
from pathlib import Path

import pytest

from mtg_gateway import theme, ui

SRC = Path(__file__).resolve().parent.parent / "src" / "mtg_gateway"
# a mockup or builder-group id, or a section mark of the internal standard, in a comment
INTERNAL_MARK = re.compile(r"\((?:K|k)\d\)|\bK\d:|mapping k\d|group [A-Z]:|§\d")


def test_served_comments_carry_no_internal_ids() -> None:
    """What a member's browser downloads (the static scripts, the pages' CSS) names no mockup id,
    builder group or section of the internal standard in its comments."""
    found = []
    for path in [*SRC.rglob("*.js"), *SRC.rglob("*.css"), *SRC.rglob("*.html")]:
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if INTERNAL_MARK.search(line):
                found.append(f"{path.name}:{i}: {line.strip()}")
    for path in SRC.rglob("*.py"):  # the CSS strings in the page modules (their comments are /* */)
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "/*" in line and INTERNAL_MARK.search(line):
                found.append(f"{path.name}:{i}: {line.strip()}")
    assert not found, found


NOW = calendar.timegm((2026, 10, 19, 15, 36, 0))
MIN, HOUR, DAY = 60, 3600, 86400

# The status table, word for word: label, colour, icon
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


@pytest.mark.parametrize("key,word,colour,icon", STATUS_TABLE)
def test_status_labels_match_the_standard(key: str, word: str, colour: str, icon: str) -> None:
    out = ui.status_label(key)
    # Applying draws the theme's one animated spinner (0.7.46), the others their icon
    mark = theme.SPINNER if icon == "spinner" else theme.icon(icon)
    assert out == f"<span class='ui-lbl {colour}'>{mark}{word}</span>"


@pytest.mark.parametrize("key,word,colour,icon", RISK_TABLE)
def test_risk_labels_match_the_standard(key: str, word: str, colour: str, icon: str) -> None:
    assert ui.risk_label(key) == f"<span class='ui-lbl {colour}'>{theme.icon(icon)}{word}</span>"


def test_stored_codes_read_as_the_standard_words() -> None:
    assert ui.status_label("pending") == ui.status_label("waiting")
    assert ui.status_label("superseded") == ui.status_label("replaced")
    assert ui.risk_label("consent") == ui.risk_label("your_press")
    assert ui.risk_label("destructive") == ui.risk_label("delete")
    with pytest.raises(KeyError):
        ui.status_label("low")  # a risk is not a status
    with pytest.raises(KeyError):
        ui.risk_label("waiting")
    with pytest.raises(KeyError):
        ui.label_for("nonsense")


def test_refused_is_red_with_the_ban_icon_for_activity_pages() -> None:
    # the ban sign, so Refused differs from Failed (the alert sign) by icon as well as by word
    assert ui.label_for("refused") == f"<span class='ui-lbl red'>{theme.icon('ban')}Refused</span>"
    assert theme.icon("ban") != theme.icon("alert")
    assert "refused" not in ui.STATUS_KEYS and "refused" not in ui.RISK_KEYS


def test_label_options_and_escaping() -> None:
    out = ui.label_for("delete", word="Delete 1", title="Deletes <a> deck", cls="stin")
    assert out.startswith("<span class='ui-lbl red stin' title='Deletes &lt;a&gt; deck'>")
    assert out.endswith("Delete 1</span>")
    assert ui.label("Running", "orange", "spinner").endswith("Running</span>")
    assert "&lt;b&gt;" in ui.label("<b>", "grey", "x")
    with pytest.raises(ValueError):
        ui.label("Odd", "purple", "x")


def test_app_chip_names_the_app_and_never_guesses() -> None:
    # 2026-10-21 (Ben): the app's full name beside its mark (Claude, ChatGPT: the official files,
    # tests/test_app_logos.py) or the one neutral icon for any other app; never initials
    plug = theme.icon("app")
    assert ui.app_chip("Claude", client_id="https://claude.ai/oauth/c.json").endswith(
        "alt='' width='16' height='16'><span class='nm'>Claude</span></span>"
    )
    assert ui.app_chip("ChatGPT", client_id="https://chatgpt.com/oauth/c.json").endswith(
        "alt='' width='16' height='16'><span class='nm'>ChatGPT</span></span>"
    )
    assert ui.app_chip("Forge") == f"<span class='ui-app'>{plug}<span class='nm'>Forge</span></span>"
    assert "&lt;script&gt;" in ui.app_chip("<script>")
    for none in (None, "", "  "):
        out = ui.app_chip(none)
        assert "class='ui-app none' title='No app'" in out and "—" in out
        assert "<span class='sr-only'>No app</span>" in out


@pytest.mark.parametrize(
    "age,text",
    [
        (-5 * MIN, "just now"),  # a clock a little ahead
        (0, "just now"),
        (59, "just now"),
        (MIN, "1 min ago"),
        (40 * MIN, "40 min ago"),
        (HOUR - 1, "59 min ago"),
        (HOUR, "1 h ago"),
        (2 * HOUR + 59 * MIN, "2 h ago"),
        (DAY - 1, "23 h ago"),
        (DAY, "1 day ago"),
        (2 * DAY - 1, "1 day ago"),
        (2 * DAY, "2 days ago"),
        (6 * DAY + 23 * HOUR, "6 days ago"),
        (7 * DAY, "7 days ago"),  # the boundary is in: up to and including 7 days
        (7 * DAY + 1, "Oct 12"),  # then the date
        (7 * DAY + 90 * MIN, "Oct 12"),  # 7 days and 1.5 hours reads Oct 12
        (8 * DAY, "Oct 11"),
        (280 * DAY, "Jan 12"),
    ],
)
def test_when_text_follows_the_date_rule(age: int, text: str) -> None:
    assert ui.when_text(NOW - age, now=NOW) == text


def test_when_text_adds_the_year_only_when_it_is_not_this_one() -> None:
    last_year = calendar.timegm((2025, 12, 30, 23, 0, 0))
    assert ui.when_text(last_year, now=NOW) == "Dec 30, 2025"
    new_year = calendar.timegm((2027, 1, 3, 0, 0, 0))
    assert ui.when_text(last_year, now=new_year) == "Dec 30, 2025"
    assert ui.when_text(None) == "" and ui.when_text(0) == "" and ui.when_text("x") == ""


def test_when_markup_has_one_form_with_a_full_tooltip() -> None:
    ts = NOW - 2 * HOUR
    out = ui.when(ts, now=NOW)
    assert out == (
        "<time class='ui-when' datetime='2026-10-19T13:36:00Z' data-fmt='rel' "
        "title='Oct 19, 2026, 13:36 UTC'>2 h ago</time>"
    )
    assert ui.full_text(calendar.timegm((2026, 3, 5, 7, 4, 0))) == "Mar 5, 2026, 07:04 UTC"
    assert ui.when(None) == "" and ui.when(ts, now=NOW, cls="md-hide").startswith(
        "<time class='ui-when md-hide'"
    )


def test_feedback_script_has_the_same_rule() -> None:
    js = (theme.STATIC_DIR / "feedback.js").read_text()
    for needle in (
        "rel: function (value, now)",
        '"just now"',
        '" min ago"',
        '" h ago"',
        "age <= 7 * 86400",
        '" day ago"',
        '" days ago"',
        "time[datetime][data-fmt=rel]",
    ):
        assert needle in js, needle


def test_widget() -> None:
    out = ui.widget(
        "Waiting for you",
        "clock",
        1234,
        unit="waiting",
        detail="6 on 4 decks <b>",
        actions=[ui.button_link("Show them", "/p?f=w")],
        tone="accent",
        wide_sm=True,
    )
    assert out.startswith("<div class='wdg accent wide-sm'><span class='lab'>")
    assert "Waiting for you</span><span class='big'>1,234<small>waiting</small></span>" in out
    assert "<span class='det'>6 on 4 decks &lt;b&gt;</span>" in out
    assert "<span class='go'><a class='btn btn-plain' href='/p?f=w'>Show them</a></span>" in out
    assert "class='det'" not in ui.widget("Applied", "check", 0)
    with pytest.raises(ValueError):
        ui.widget("x", "x", 1, actions=["a", "b", "c"])
    with pytest.raises(ValueError):
        ui.widget("x", "x", 1, tone="green")
    assert ui.widgets(["<i>"]) == "<section class='ui-widgets' aria-label='At a glance'><i></section>"


def test_chips_count_and_mark_the_current_one() -> None:
    out = ui.chips(
        [
            ui.Chip("Waiting", 8, "?show=waiting", True),
            ui.Chip("All", 1051, "?show=all"),
            ui.Chip("X", None, "#"),
        ]
    )
    assert out.startswith("<nav class='ui-chips' aria-label='Show'>")
    assert (
        "<a class='ui-chip' href='?show=waiting' aria-current='true'>Waiting <span class='n'>8</span></a>"
        in out
    )
    assert "All <span class='n'>1,051</span>" in out
    assert "<a class='ui-chip' href='#'>X</a>" in out


def test_toolbar_is_a_get_form_with_a_search_button_without_scripts() -> None:
    f = ui.filter_select("risk", "Risk", [("", "Any risk"), ("low", "Low")], "low")
    assert f == (
        "<span class='f'><select name='risk' aria-label='Risk'><option value=''>Any risk</option>"
        "<option value='low' selected>Low</option></select></span>"
    )
    out = ui.toolbar(
        "/proposals",
        search_label="Search proposals",
        placeholder="Search deck or card",
        q="sol 'ring",
        filters=[f],
        keep={"show": "waiting", "empty": ""},
    )
    assert out.startswith("<form class='ui-tools' role='search' method='get' action='/proposals'>")
    assert "<input type='hidden' name='show' value='waiting'>" in out and "empty" not in out
    assert "<span class='sr-only'>Search proposals</span>" in out
    assert "value='sol &#x27;ring'" in out
    assert "<noscript><button type='submit' class='btn-plain'>Search</button></noscript>" in out


def test_table_pieces() -> None:
    assert ui.table_open("32px 1fr", "32px 1fr", "1fr", label="Proposals") == (
        "<div class='ui-table' role='region' aria-label='Proposals' "
        "style='--cols:32px 1fr;--cols-md:32px 1fr;--cols-sm:1fr'>"
    )
    head = ui.table_header(
        [ui.Column("", html="<b>x</b>"), ui.Column("Change"), ui.Column("Risk", "md-hide")]
    )
    assert head == (
        "<div class='tr th'><span><b>x</b></span><span>Change</span><span class='md-hide'>Risk</span></div>"
    )
    chk = ui.check_cell("id", "p1", "#1 Sol Ring", checked=True)
    assert chk.startswith("<label class='chk'><input type='checkbox' name='id' value='p1'")
    assert "aria-label='#1 Sol Ring' checked>" in chk
    assert ui.group_row("<a href='/d'>Deck</a>", "1 waiting").startswith(
        "<div class='tr group'><span></span>"
    )
    assert ui.day_row("Today") == "<div class='tr daygroup'>Today</div>"
    link = ui.open_link("/proposals/1", "Review")
    assert "class='act linkbtn'" in link and "aria-label='Review'" in link and "sm-only chev" in link


def test_pager_says_where_you_are_and_never_caps_silently() -> None:
    first = ui.pager(0, 51, newer_href=None, older_href="?page=2", note="past proposals are kept 30 days")
    assert "<span class='info'>1–25 of 51 · past proposals are kept 30 days</span>" in first
    assert "<button class='btn-plain' type='button' disabled>← Newer</button>" in first
    assert "<a class='btn btn-plain' href='?page=2' rel='next'>Older →</a>" in first
    last = ui.pager(50, 51, newer_href="?page=2", older_href=None)
    assert "51–51 of 51" in last and "type='button' disabled>Older →</button>" in last
    assert "rel='prev'>← Newer</a>" in last
    assert "1–5 of 5" in ui.pager(0, 5, newer_href=None, older_href=None)
    assert "1,001–1,025 of 2,000" in ui.pager(1000, 2000, newer_href="a", older_href="b")
    assert ui.pager(0, 0, newer_href=None, older_href=None) == ""


def test_selection_bar_and_side_panel() -> None:
    bar = ui.selection_bar("2 checked", ["<button>Apply 2 changes</button>"], label="Checked changes")
    assert bar.startswith("<div class='ui-bar' role='group' aria-label='Checked changes'>")
    assert "<span class='count' aria-live='polite'>2 checked</span><button>" in bar
    assert " hidden>" in ui.selection_bar("", [], label="x", hidden=True)
    panel = ui.side_panel(
        "#1 Sol Ring", "<p>body</p>", label="Proposal #1", close_href="/p", back_text="Back"
    )
    assert panel.startswith("<aside class='ui-drawer' aria-label='Proposal #1'>")
    assert "<p class='page-only'><a href='/p'>← Back</a></p>" in panel
    assert "class='close btn panel-only' href='/p' aria-label='Close' title='Close'>" in panel
    assert ui.split("L") == "<div class='ui-split'>L</div>"
    assert (
        ui.split("L", panel)
        == f"<div class='ui-split with-panel'><div class='list-only'>L</div>{panel}</div>"
    )
    assert ui.meta([("Deck", "<a>x</a>")]) == "<dl class='ui-meta'><dt>Deck</dt><dd><a>x</a></dd></dl>"


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
