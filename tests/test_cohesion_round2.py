"""Cohesion round 2, the parts that need no browser (docs/UI-STANDARD.md): the way back a level is
one helper with one look, a side panel takes its page title as a parameter, a list row's main
action is the standard button, the tag rule keeps to tags, days are said as the member's own, and
the workarounds the shared pieces replaced are gone."""

from __future__ import annotations

from pathlib import Path

from mtg_gateway import account_hub, admin, history_view, reports_page, theme, ui

SRC = Path(theme.__file__).parent


def test_the_way_back_is_one_helper_with_one_look() -> None:
    link = ui.back_link("Binder", "/binder")
    assert link.startswith("<p class='ui-back-line'><a class='ui-cap ui-back' href='/binder'><svg")
    assert link.endswith("<span>Binder</span></a></p>")
    # in a row beside other ways to go: the capsule alone
    assert ui.back_link("Back to the deck", "/decks/1", line=False).startswith("<a class='ui-cap ui-back' ")
    # the Account pages', the Binder pages' and a side panel's way back are that helper
    assert account_hub.back_head("/account", "Account", "Account details").startswith(
        ui.back_link("Account", "/account")
    )
    panel = ui.side_panel("H", "<p>b</p>", label="L", close_href="/p", back_text="Back to Proposals")
    assert "<a class='ui-cap ui-back' href='/p'>" in panel and "← " not in panel
    # no page draws a way back of its own: the old looks are gone from the code
    for path in list(SRC.glob("*.py")) + list((SRC / "static").glob("*.js")):
        text = path.read_text()
        assert "bn-back" not in text and "yback" not in text, path.name
        assert "← Back" not in text, path.name


def test_a_side_panel_takes_its_page_title_and_can_merge_its_heading() -> None:
    plain = ui.side_panel("<b>H</b>", "", label="L", close_href="/p", back_text="Back")
    assert "<h1" not in plain and "<div class='dh'><h2><b>H</b></h2>" in plain
    titled = ui.side_panel("H", "", label="L", close_href="/p", back_text="Back", page_title="A <b>")
    # the title is the item's own page's (not drawn beside the list), between the way back and the heading
    assert "</a></p><h1 class='page-only'>A &lt;b&gt;</h1><div class='dh'><h2>H</h2>" in titled
    merged = ui.side_panel(
        "H", "", label="L", close_href="/p", back_text="Back", page_title="H", merge_title=True
    )
    # the title says what the heading says: as its own page the heading is not drawn again
    assert "<h1 class='page-only'>H</h1><div class='dh panel-only'><h2>H</h2>" in merged
    assert ".ui-drawer h1.page-only{margin:0}" in theme.UI_CSS
    # neither caller patches the panel's markup any more
    for name in ("pages.py", "history_view.py"):
        assert "<div class='dh'>" not in (SRC / name).read_text(), name
    assert "hy-title" not in history_view.HISTORY_CSS


def test_a_rows_main_action_is_the_standard_button() -> None:
    assert ui.row_open("/x", "Open").startswith("<a class='btn' href='/x' aria-label='Open'>")
    assert ui.open_link("/x", "Review").startswith("<a class='act btn' href='/x'")
    # Reports: Test a pasted list opens a form, so it is a button too
    assert "<summary class='btn'>Test a pasted list</summary>" in reports_page.paste_test_html("t")


def test_the_tag_rule_keeps_to_tags_and_dead_rules_are_gone() -> None:
    sheet = (SRC / "theme.py").read_text()
    assert ".ui-tag,.pill{" in sheet and "span.chip" not in sheet.split(".ui-tag,.pill{")[0][-400:]
    assert "span.chip," not in sheet and ".ds .ui-card" not in sheet
    # the admin tab bar has no layout of its own: the switch's script lays seven parts four over three
    assert "seg-cols" not in admin.ADMIN_CSS
    assert "seg-tight" in (SRC / "static" / "topbar.js").read_text()


def test_days_are_said_as_the_members_own() -> None:
    assert "UTC days" not in (SRC / "history_view.py").read_text()
    text = (SRC / "guide.py").read_text()
    assert "UTC days" not in text and "counted in your own local days" in text
