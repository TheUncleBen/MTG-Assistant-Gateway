"""The Proposals, History and Reports rows after the 0.7.46 redesign (mockup screen 6): one line
per entry at every width, with a tick box where the list has one, the title and its facts, the
one main action as a capsule and the ⋮ beside it, never on a line of its own; four widgets on
Proposals; the Fold's filter row balanced; History's purpose line as plain words and its Details
buttons in one row."""

from __future__ import annotations

import re
import time
from pathlib import Path

from mtg_gateway import history_view, proposals_view, reports_page, theme, ui
from mtg_gateway.theme import esc

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_history_page import seed_history, store_report

stack_options = stack_with(COMPANION)
# a row's main action: the word from 600 px, a 44 px chevron on a phone
OPEN = (
    "<a class='ui-cap' href='{href}' aria-label='{word}'><span class='sm-hide'>{word}</span>"
    "<span class='sm-only chev' aria-hidden='true'>›</span></a>"
)


def _rows(table: str) -> list[str]:
    rows = table.split("<div class='tr")[1:]
    return [r for r in rows if not r.startswith(" th'") and " group" not in r[:12]]


async def test_proposals_waiting_rows_are_one_line_with_review_and_the_menu(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        seed_history(gw.db, sub, now=int(time.time()))
        body = (await b.http.get("/proposals", headers=NAV)).text.split("<main")[1]
        table = body.split("class='ui-table pr-table ui-rows'")[1].split("</form>")[0]
        rows = _rows(table)
        assert len(rows) == 15
        for r in rows:
            pid = re.search(r"name='pick' value='(p\d\d)'", r).group(1)
            # the whole row ticks its box; Review and ⋮ sit together at the row's end
            assert f"<label class='title' for='pk-{pid}'>" in r
            # Review: the word from 600 px, a chevron on a phone (the title keeps its line)
            assert (
                f"<span class='act'>{OPEN.format(href=f'/proposals/{pid}', word='Review')}"
                f"<a class='kebab' href='/proposals/{pid}' aria-label='Menu for " in r
            ), r
            assert "aria-haspopup='menu'" in r and "a.chev" not in r and "md-hide" not in r
            # the facts line: risk, the app, when (the Waiting label is the chip's own word)
            assert "<span class='sub'>" in r and ">Waiting</span>" not in r
        # the deck's group row: its tick box, the name with its count line, Open deck as a capsule
        group = table.split("<div class='tr group'>")[1].split("<div class='tr'")[0]
        assert "data-all='43'" in group and "<a id='deck-43' href='/decks/43'>Reap the Tides</a>" in group
        assert "<small>15 waiting · oldest " in group
        assert "<span class='act'><a class='ui-cap' href='/decks/43'>Open deck</a></span>" in group
        # the header keeps the check-every box and nothing else
        assert "aria-label='Check every waiting change'" in table and ">Risk<" not in table
        assert ">Status<" not in table and ">From<" not in table and ">Made<" not in table
    finally:
        await b.aclose()


async def test_proposals_has_four_widgets_with_the_apps_folded_into_waiting(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        seed_history(gw.db, sub, now=int(time.time()))
        body = (await b.http.get("/proposals", headers=NAV)).text.split("<main")[1]
        widgets = body.split("<section class='ui-widgets'")[1].split("</section>")[0]
        assert widgets.count("<div class='wdg") == 4
        assert "Waiting by app" not in widgets and "Connected apps" not in widgets
        assert "bar-split" not in widgets and "wide-sm" not in widgets
        first = widgets.split("<div class='wdg")[1]
        assert "15 on 1 deck · 10 from an unnamed assistant, 5 from you · oldest " in first
        # the third tile keeps the approved words and says what it counts (C-FINDINGS F13)
        third = widgets.split("<div class='wdg")[3]
        assert "Only you can apply" in third and "in every approval mode" not in third
        assert proposals_view.PRESS_DETAIL in third
    finally:
        await b.aclose()


async def test_the_all_list_rows_carry_their_status_and_the_order_switch(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        seed_history(gw.db, sub, now=int(time.time()))
        gw.db.finish_proposal(
            "p02", state="rejected", result={"rejected_by": "card", "reject_reason": "Keep it, please."}
        )
        body = (await b.http.get("/proposals?show=all", headers=NAV)).text.split("<main")[1]
        table = body.split("class='ui-table pr-table ui-rows'")[1].split("<div class='loadmore'")[0]
        rows = _rows(table)
        assert len(rows) == 25 and "<div class='tr th'>" not in table
        for r in rows:
            pid = re.search(r"href='/proposals/(p\d\d)\?show=all'", r).group(1)
            assert f"<a class='t' href='/proposals/{pid}?show=all'>" in r
            assert (
                f"<span class='act'>{OPEN.format(href=f'/proposals/{pid}?show=all', word='Review')}"
                f"<a class='kebab' href='/proposals/{pid}?show=all'" in r
            ), r
            assert "md-hide" not in r
            # the status label is on the facts line, its closing line under it
            states = "(Applied|Waiting|Rejected|Failed)"
            label = rf"<span class='ui-lbl \w+'>(<svg.*?</svg>)?{states}</span>"
            assert re.search(rf"<span class='sub'>.*{label}", r), r
        rejected = next(r for r in rows if "/proposals/p02?" in r)
        assert "<span class='flag'>Rejected by you: “Keep it, please.”</span>" in rejected
        failed = next(r for r in rows if "/proposals/p03?" in r)
        why = "<a class='ui-cap' href='/proposals/p03'>Why</a>"
        assert f"<span class='flag'>Could not be applied · {why}</span>" in failed
        # the Made sort left the header for the head line, as a capsule
        head = body.split("<div class='list-head'>")[1].split("</div>")[0]
        older = "<a class='ui-cap' href='/proposals?show=all&amp;order=old'>Show oldest first</a>"
        assert f"Newest first · {older}" in head
        assert "pr-sort" not in body
    finally:
        await b.aclose()


async def test_history_rows_open_their_details_and_keep_restore_in_the_menu(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        now = int(time.time())
        seed_history(gw.db, sub, now=now)
        store_report(gw.db, sub, "rep_a", taken_at=now - 100)
        page = (await b.http.get("/history", headers=NAV)).text
        body = page.split("<main")[1]
        # the purpose line is plain words: no capsule inside the sentence (F13)
        assert f"<p class='ui-sub'>{esc(history_view.PAGE_SUB_TEXT)}</p>" in body
        assert "waiting for you are on <a" not in body and "under Proposals." in history_view.PAGE_SUB_TEXT
        table = body.split("class='ui-table hy-table ui-rows'")[1].split("<div class='loadmore'")[0]
        rows = _rows(table)
        assert rows and "<div class='tr th'>" not in table and "hy-sort" not in body
        snap = next(r for r in rows if "open=s-s00'" in r)
        # the deck's name is the row's main action, Details (F14), on a row that knows its deck
        # (the snapshot's ⋮ offers the change it was taken before from data-review, L9)
        attrs = " data-deck='42' data-deck-href='/decks/42' data-review='/proposals/p00'>"
        assert snap.startswith("'" + attrs) or snap.startswith(" open'" + attrs)
        assert "<a class='t' href='/history?open=s-s00'>Liesa Test Deck</a>" in snap
        restore = "<form method='post' action='/history/restore' class='hy-restore'>"
        assert f"<span class='act'><span class='acts' hidden>{restore}" in snap
        details_cap = OPEN.format(href="/history?open=s-s00", word="Details")
        assert f"</span>{details_cap}<a class='kebab' href='/history?open=s-s00'" in snap
        assert " href='/decks/42'" not in snap  # no deck link in the row (the menu reads data-deck-href)
        rep = next(r for r in rows if "/history/reports/rep_a'" in r)
        assert "<a class='t' href='/history/reports/rep_a'>Liesa Test Deck</a>" in rep
        open_cap = OPEN.format(href="/history/reports/rep_a", word="Open report")
        assert f"{open_cap}<a class='kebab' href='/history/reports/rep_a'" in rep
        # the details: the deck's page, one explanation, one row of buttons
        opened = (await b.http.get("/history?open=s-s00", headers=NAV)).text
        details = opened.split("<aside class='ui-drawer'")[1]
        assert "<dt>Deck</dt><dd><a href='/decks/42'>Liesa Test Deck</a></dd>" in details
        acts = details.split("<div class='ui-actions'>")
        assert len(acts) == 2, details
        row = acts[1].split("</div>")[0]
        assert row.index("Restore (review first)") < row.index("Test this version") < row.index("Delete…")
        assert "<p>" + history_view.SNAPSHOT_WORDS + "</p>" in details
    finally:
        await b.aclose()


async def test_reports_rows_have_open_and_the_menu_together(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        now = int(time.time())
        store_report(gw.db, sub, "rep_a", taken_at=now - 100)
        store_report(gw.db, sub, "rep_b", deck_id="43", deck_name="Reap the Tides", taken_at=now - 86400)
        body = (await b.http.get("/reports", headers=NAV)).text.split("<main")[1]
        table = body.split("class='ui-table rp-t ui-rows'")[1].split("<div class='loadmore'")[0]
        rows = _rows(table)
        assert len(rows) == 2 and "<div class='tr th'>" not in table
        for r in rows:
            rid = re.search(r"href='/history/reports/(rep_\w)'", r).group(1)
            assert (
                f"<span class='act'>{OPEN.format(href=f'/history/reports/{rid}', word='Open')}"
                f"<a class='kebab' href='/history/reports/{rid}'" in r
            ), r
            assert "md-hide" not in r
            assert "<span class='sub'><span class='kv'>Won <b>" in r
        head = body.split("<div class='list-head'>")[1].split("</div>")[0]
        assert "Newest first · <a class='ui-cap' href='/reports?order=oldest'>Show oldest first</a>" in head
        assert "rp-sort" not in body
        oldest = (await b.http.get("/reports?order=oldest", headers=NAV)).text
        assert "Oldest first · <a class='ui-cap' href='/reports'>Show newest first</a>" in oldest
    finally:
        await b.aclose()


def test_the_row_menu_button_and_the_balanced_toolbar() -> None:
    k = ui.row_menu("/proposals/p1", "+1 Sol Ring")
    assert k.startswith("<a class='kebab' href='/proposals/p1' aria-label='Menu for +1 Sol Ring' ")
    assert "aria-haspopup='menu'" in k
    assert "title='Menu'" in k and "<svg" in k
    # a toolbar too narrow for the search and all its filters (the Fold, F13; the list beside an
    # open side panel, polish R5): the search on its own line, the filters sharing the next. The
    # rule follows the toolbar's own width and its filter count (a window-width rule missed the
    # panel), so it is in the shared styles, not appended by each page.
    from mtg_gateway.theme import UI_CSS

    assert not hasattr(theme, "BALANCED_TOOLS_CSS")
    assert "@container (max-width:60em){ .ui-tools.n4 .search{flex-basis:100%} }" in UI_CSS
    assert "@container (max-width:50em){ .ui-tools.n3 .search{flex-basis:100%} }" in UI_CSS
    assert "@container (max-width:39.5em){ .ui-tools.n4 .f{flex-basis:calc(50% - .25rem)} }" in UI_CSS
    assert "@media (min-width:600px) and (max-width:999.98px)" not in (
        proposals_view.PROPOSALS_CSS + history_view.HISTORY_CSS + reports_page.REPORTS_PAGE_CSS
    )
    four = ui.toolbar("/proposals", search_label="s", filters=["<span class='f'></span>"] * 4)
    assert four.startswith("<form class='ui-tools n4' ")
    assert ui.toolbar("/x", search_label="s").startswith("<form class='ui-tools' ")
    assert ".ui-table.ui-rows" in UI_CSS


def test_a_history_line_is_words_only_and_its_links_are_in_the_menu() -> None:
    """A History line holds no capsule (on a phone the Proposal capsule wrapped onto a line of
    its own; review 2 L9, confirmation L9): a change's or a restore's review page and a
    snapshot's backup copy are the row's ``data-review`` and ``data-backup``, which the ⋮
    (static/menus.js) offers, and the Details keep them. The line keeps to one line, the rest in
    the Details."""
    e = history_view._change_event({"id": "p1", "summary": "+1 Sol Ring", "kind": "deck_changes"})
    assert e["line"] == "Applied a change: +1 Sol Ring" and e["review"] == "/proposals/p1"
    row = history_view._row_html({**e, "deck_id": "42", "deck_name": "Deck"}, {}, now=0)
    assert " data-review='/proposals/p1'" in row and "data-backup" not in row
    assert "<span class='deck'>Applied a change: +1 Sol Ring</span>" in row
    events = history_view.build_events(
        [{"id": "p2", "kind": "restore", "taken": "2026-10-11 16:10 UTC", "deck_id": "42"}],
        [
            {
                "snapshot_id": "s1",
                "deck_id": "42",
                "deck_name": "Deck",
                "taken_at": 10,
                "proposal_id": "p3",
                "backup_url": "https://archidekt.com/decks/9",
                "card_count": 99,
            },
            {"snapshot_id": "s2", "deck_id": "42", "deck_name": "Deck", "taken_at": 9, "backup_gone": True},
        ],
        [],
        csrf_input="",
    )
    by_key = {e["key"]: e for e in events}
    restore, snap, gone = by_key["p-p2"], by_key["s-s1"], by_key["s-s2"]
    assert restore["review"] == "/proposals/p2" and "<a" not in restore["line"]
    assert snap["review"] == "/proposals/p3" and snap["backup"] == "https://archidekt.com/decks/9"
    assert "<a" not in snap["line"] and "<a" not in gone["line"]
    assert gone.get("review") is None and gone.get("backup") is None
    assert "its backup copy is no longer on Archidekt" in gone["line"]
    snap_row = history_view._row_html(snap, {}, now=0)
    assert " data-review='/proposals/p3' data-backup='https://archidekt.com/decks/9'" in snap_row
    assert "Backup copy on Archidekt</a>" in history_view.details_panel(snap, {})
    js = (Path(history_view.__file__).parent / "static" / "menus.js").read_text()
    assert 'tr.getAttribute("data-review")' in js and 'tr.getAttribute("data-backup")' in js
    assert '"Open the review page"' in js and '"The change it was taken before"' in js
    assert '"Backup copy on Archidekt"' in js
    assert ".hy-table .title .deck{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" in (
        history_view.HISTORY_CSS
    )
