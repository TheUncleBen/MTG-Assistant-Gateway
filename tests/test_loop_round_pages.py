"""The 0.7.48 loop round's page fixes, the parts that need no browser (docs/UI-STANDARD.md): the
Alerts actions that cannot act are not drawn, Home's sections have their headings outside their
panels and Connect is a widget, the Scan page has a purpose line and no panel around its steps, a
folder's name is its section's heading, a list page has one way back, a Watchlist row's main
action is the row helper's, a report has one way back and names its deck under its title, a
profile's Follow is one word, and the admin Users row captions what it shows."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from mtg_gateway import (
    admin,
    alerts_page,
    binder_hub,
    folderview,
    home,
    settings_page,
    setup_hub,
    theme,
    ui,
    watchlist_page,
)
from mtg_gateway import report_page as rp

from .test_overflow_sweep_browser import LONGUSER, _seed, server  # noqa: F401
from .test_report_view import report
from .test_suggest_browser import Server

SRC = Path(theme.__file__).parent


def _main(server: Server, sid: str, path: str) -> str:  # noqa: F811
    r = httpx.get(f"{server.base}{path}", cookies={"mtg_session": sid}, timeout=30)
    assert r.status_code == 200, (path, r.status_code)
    return r.text.split("<main", 1)[1].split("</main>", 1)[0]


# -- Alerts (#2, #3) ----------------------------------------------------------------------------


def test_alerts_actions_that_cannot_act_are_not_drawn() -> None:
    none = alerts_page.tools_html("", any_unread=False, any_rows=False)
    # still in the page, off, so the script can turn one on after an Undo; the sheet hides it
    assert none.count(" disabled>") == 2 and ">Settings</a>" in none
    assert ".al-acts form:has(> button:disabled){display:none}" in theme.CSS
    both = alerts_page.tools_html("", any_unread=True, any_rows=True)
    assert " disabled" not in both
    # a phone's short words; the button keeps its whole name for a screen reader
    assert "aria-label='Mark all read'" in both
    assert "<span class='al-long'>Mark all read</span><span class='al-short'>Read all</span>" in both


def test_alerts_page_with_only_a_waiting_row_offers_no_bulk_action(server: Server) -> None:  # noqa: F811
    sid, _pending = _seed(server)
    body = _main(server, sid, "/notifications")
    # the row that waits is unread (the badge counts it, as the bell's box does), and nothing
    # kept can be marked or cleared: neither button can act
    assert "1 unread" in body and "al-row unread live" in body
    acts = body.split("<div class='al-acts'>", 1)[1].split("</div>", 1)[0]
    assert len(re.findall(r"data-al-all='\w+' aria-label='[^']+' disabled>", acts)) == 2, acts


# -- Binder (#4, #21) ---------------------------------------------------------------------------


def test_binder_split_view_gives_the_title_row_its_own_height() -> None:
    wide = binder_hub.CSS.split("@container bn (min-width:56em){", 1)[1].split("@container", 1)[0]
    assert ".bn.picked{grid-template-rows:auto 1fr}" in wide
    assert ".bn.picked > .bn-main{grid-column:2;grid-row:2;align-self:start}" in wide


def test_main_actions_are_full_width_on_a_phone_and_sentence_case() -> None:
    assert "@media (max-width:599.98px){ .bn-save .primary{flex:1 1 100%} }" in binder_hub.CSS
    assert ".inst .actions .step-main{flex:1 1 100%" in setup_hub.INSTALL_CSS
    assert settings_page.save_label("Approvals") == "Save approvals"
    assert settings_page.save_label("Deck sync") == "Save deck sync"
    assert settings_page.save_label("Sign-in") == "Save sign-in"
    # a name keeps its capital
    assert settings_page.save_label("Archidekt and Scryfall pacing") == "Save Archidekt and Scryfall pacing"
    assert "Save {html.escape(title)}" not in (SRC / "settings_page.py").read_text()


def test_admin_settings_page_says_save_approvals(server: Server) -> None:  # noqa: F811
    sid, _pending = _seed(server)
    body = _main(server, sid, "/admin/settings")
    assert ">Save approvals</button>" in body and "Save Approvals" not in body


# -- Home (#7) ----------------------------------------------------------------------------------


def test_home_connect_is_a_widget_with_the_guided_setup_button() -> None:
    box = home.connect_html("https://gw.test/mcp")
    assert box.startswith("<section class='connect'") and "<div class='wdg'>" in box
    assert "<a class='btn btn-plain' href='/install'>Guided setup</a>" in box
    # the connector address is one press away, for a member who needs only that
    assert "data-copy-text='https://gw.test/mcp'" in box and ">Copy address</button>" in box
    assert "<details" not in box


def test_home_section_headings_sit_outside_their_panels() -> None:
    decks = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    inner = home.recent_panel_inner(decks, {})
    head, panel = inner.split("<div class='panel'>", 1)
    assert head.startswith("<div class='list-head'><h2>My decks <span class='ui-sub'>2</span></h2>")
    assert "href='/decks'>All decks</a>" in head and "<h2" not in panel
    for empty in (home.recent_panel_inner([], {}), home.recent_panel_inner(None, {}), home.recent_skeleton()):
        assert empty.startswith("<div class='list-head'><h2>My decks"), empty[:80]
        assert "<h2" not in empty.split("<div class='panel'>", 1)[1]
    assert "panel-head" not in (SRC / "home.py").read_text()


def test_home_title_keeps_its_size_on_a_phone() -> None:
    hero = theme.CSS.split(".home .hero{", 1)[1].split(".home .tiles", 1)[0]
    assert "align-items:flex-start" in hero.split("}", 1)[0]
    assert "@media (max-width:599.98px){ .home .hero > div{flex-basis:100%} }" in hero


# -- Scan (#8) ----------------------------------------------------------------------------------


def test_scan_page_has_a_purpose_line_and_no_panel_around_its_steps(server: Server) -> None:  # noqa: F811
    sid, _pending = _seed(server)
    body = _main(server, sid, "/scan")
    assert re.search(r"<h1>Scan cards</h1></div><p class='ui-sub'>[^<]+</p>", body), body[:400]
    js = (SRC / "scan" / "static" / "scan.js").read_text()
    css = (SRC / "scan" / "static" / "scan.css").read_text()
    assert "class: 'card intro'" not in js and "class: 'intro'" in js
    assert "class: 'notice once'" in js  # the first visit's steps: a strip with its ×
    assert ".scan .scan-opts .switch{flex:0 1 auto;min-width:0}" in css
    # the words fit a laptop's camera too
    assert "Hold the phone" not in js and "hold the phone" not in js


# -- A folder (#15) -----------------------------------------------------------------------------


def _level(*path: tuple[int, str]) -> folderview.Level:
    last = path[-1]
    return folderview.Level(
        crumbs=[(None, "Decks"), *path],
        current={"id": last[0], "name": last[1], "parent": None},
        decks=[],
        total=0,
    )


def test_a_folders_name_is_its_heading_and_the_trail_is_the_way_back() -> None:
    one = folderview.level_html(
        _level((5, "Precons & more")), covers=None, q="", view="grid", order="updated"
    )
    # one level down: the standard way back, then the folder as the section's heading
    assert one.startswith(ui.back_link("Decks", "/decks"))
    assert "<h2 id='fv-here'>Precons &amp; more <span class='ui-sub'>Empty</span></h2>" in one
    assert "aria-current='page'" not in one and "class='crumbs'" not in one
    deep = folderview.breadcrumb_html(_level((5, "Commander"), (6, "Precons")), order="name")
    # deeper: capsules, each "›" drawn by the item of the name it leads to
    assert deep.count("<li>") == 2 and "Precons" not in deep
    assert "<a class='ui-cap' href='/decks?folder=5&amp;order=name'>Commander</a>" in deep
    assert ".crumbs li + li::before{content:'›'" in folderview.FOLDER_CSS
    # inside a folder the areas are its sub-headings; with no folders in it, Decks is not said again
    assert "<h3 id='fv-decks' class='sr-only'>Decks</h3>" in one
    top = folderview.level_html(
        folderview.Level(crumbs=[(None, "Decks")]), covers=None, q="", view="grid", order=""
    )
    assert "<h2 id='fv-decks'>Decks</h2>" in top and "fv-here" not in top


def test_a_folder_page_counts_its_decks_beside_its_name(server: Server) -> None:  # noqa: F811
    sid, _pending = _seed(server)
    body = _main(server, sid, "/decks?folder=501")
    assert re.search(
        r"<h2 id='fv-here'>Commander decks in progress[^<]+ <span class='ui-sub'>1 deck</span></h2>", body
    )


# -- List pages (#16) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "title", "purpose"),
    [
        ("/lists/custom", "Custom cards", "Cards you made on Archidekt."),
        ("/collection/wants", "Pinned cards", "The cards you pinned on Archidekt"),
        ("/lists/templates", "Category templates", "Categories you can add to any deck in one press."),
    ],
)
def test_a_list_page_has_one_way_back_one_title_and_its_own_purpose(
    server: Server,  # noqa: F811
    path: str,
    title: str,
    purpose: str,
) -> None:
    sid, _pending = _seed(server)
    body = _main(server, sid, path)
    assert (
        body.count("ui-cap ui-back") == 1
        and "href='/collection/lists'><svg" in body.split("ui-back", 1)[1][:200]
    )
    assert "<span>Lists</span></a></p>" in body and "lp-crumbs" not in body
    # the title once, with the list's count; no heading under it saying the same
    assert re.search(rf"<h1>{title} <span class='cn'>[^<]+</span></h1>", body), body[:600]
    assert f"<h2>{title}" not in body
    sub = re.search(r"</div><p class='ui-sub'>([^<]+)</p>", body)
    assert sub and sub.group(1).startswith(purpose), sub
    assert "Your Archidekt lists that shape every deck" not in body


# -- Watchlist (#20) ----------------------------------------------------------------------------


def test_a_watchlist_rows_main_action_is_the_row_helper(server: Server) -> None:  # noqa: F811
    assert ui.row_open("/x", "Details", cls="more", attrs="role='button'").startswith(
        "<a class='btn more' href='/x' aria-label='Details' role='button'>"
    )
    sid, _pending = _seed(server)
    body = _main(server, sid, "/watchlist")
    row = body.split("class='act'>", 1)[1].split("</span></div>", 1)[0]
    assert (
        "<span class='sm-hide'>Details</span><span class='sm-only chev' aria-hidden='true'>›</span></a>"
        in row
    )
    assert "aria-expanded='false'" in row
    # the script changes the word, never the whole button (the chevron stays)
    js = (SRC / "static" / "watchlist.js").read_text()
    assert 'link.textContent = opening ? "Close" : "Details"' not in js
    assert ".watch-page .w-t .act .more{padding:0;border:0;background:none" in watchlist_page.WATCH_PAGE_CSS


# -- A report (#23, #24) ------------------------------------------------------------------------


def test_a_report_has_one_way_back_and_its_deck_under_its_title() -> None:
    html = rp.report_page_html(report(status="complete"), stats_html="")
    head = html.split("<header", 1)[0]
    assert head.endswith(ui.back_link("All reports", "/reports")) and html.count("ui-cap ui-back") == 1
    assert "rv-crumbs" not in html
    assert "<h1>Deck test report</h1>" in html
    # the deck's name whole, as words: the way to the deck is the button
    assert "<p class='ui-sub rv-deck'>Liesa Test Deck</p>" in html
    # the deck and its history: standard buttons in the page's action row
    acts = html.split("<div class='rv-acts'>", 1)[1].split("</header>", 1)[0]
    assert "<a class='btn btn-plain' href='/decks/42'>" in acts and ">Open the deck</a>" in acts
    assert "<a class='btn btn-plain' href='/history?deck_id=42'>" in acts
    assert ">Deck history</a>" in acts
    # a pasted list has no deck to open
    pasted = rp.report_page_html(report(status="complete", deck_id="pasted"), stats_html="")
    assert "Open the deck" not in pasted and "<p class='ui-sub rv-deck'>" in pasted


# -- A profile (#25) ----------------------------------------------------------------------------


def test_a_profiles_follow_is_one_word_with_the_name_for_a_screen_reader(server: Server) -> None:  # noqa: F811
    sid, _pending = _seed(server)
    body = _main(server, sid, f"/users/{LONGUSER}")
    btn = body.split("data-social='follow'", 1)[1].split("</button>", 1)[0]
    assert btn.endswith("<span>Follow</span>") and "data-short" in btn
    assert f"aria-label='Follow {LONGUSER}'" in btn
    js = (SRC / "static" / "social.js").read_text()
    assert 'btn.hasAttribute("data-short")' in js


# -- Admin (#26) ----------------------------------------------------------------------------------


def test_admin_users_row_captions_what_follows_its_labels(server: Server) -> None:  # noqa: F811
    sid, _pending = _seed(server)
    body = _main(server, sid, "/admin/users")
    sub = body.split("<span class='sub'>", 1)[1].split("<span class='sm-only'>", 1)[0]
    assert "<span class='k'>Archidekt</span> alice</span>" in sub and "No apps" in sub
    assert ">none<" not in sub
    assert admin._apps_html({"clients": []}, caption=True) == "<span class='muted'>No apps</span>"
