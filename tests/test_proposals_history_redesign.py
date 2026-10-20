"""The Proposals and History screens of the 2026-10 UI standard (P1, P2, P3, H1): a closed
proposal's risk is stored when it closes (with a one-time backfill), the waiting dashboard with
its widgets, chips, filters and selection bar, the All view paged newest first, one proposal as
a side panel or its own page, and History without proposals."""

from __future__ import annotations

import re
import time
from pathlib import Path

from mtg_gateway.db import Database
from mtg_gateway.modes import UNJUDGED

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_history_page import seed_history, store_report

stack_options = stack_with(COMPANION)

LOW_ROWS = [{"kind": "add", "name": "Sol Ring", "qty": 1, "before": 0, "after": 1}]


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "r.sqlite")
    db.upsert_user("u", email="u@example.test", name="U", preferred_username="u", groups=[])
    return db


def _save(db: Database, pid: str, *, rows: list | None = None, deck: str = "42", **extra: object) -> None:
    db.save_proposal(
        {
            "id": pid,
            "owner_sub": "u",
            "kind": "edit",
            "deck_id": deck,
            "deck_name": "Immortal Reckoning",
            "baseline_fingerprint": "f",
            "changes": [],
            "diff_text": "+1 Sol Ring",
            "expires_at": int(time.time()) + 3600,
            "created_by_client": "__browser__",
            **({"rows": rows} if rows is not None else {}),
            **extra,
        }
    )


def _risk(db: Database, pid: str) -> str | None:
    return db._one("SELECT risk FROM proposals WHERE id = ?", (pid,))["risk"]


# -- the stored risk ---------------------------------------------------------------
def test_risk_is_stored_when_a_proposal_closes_and_cleared_when_it_reopens(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.risk_source = lambda row: "low" if row.get("rows") else "high"
    _save(db, "a", rows=LOW_ROWS)
    _save(db, "b")
    assert _risk(db, "a") is None  # a waiting one is worked out on every read
    db.finish_proposal("a", state="applied", result={})
    db.close_proposal("b", "u", "withdrawn")
    assert _risk(db, "a") == "low" and _risk(db, "b") == "high"
    # once stored it no longer follows the settings
    db.risk_source = lambda row: "destructive"
    db.settle_risks()
    assert _risk(db, "a") == "low"
    # a reopened proposal waits again, so its risk is worked out on read once more
    assert db.reopen_proposal("b", "u")
    assert _risk(db, "b") is None


def test_backfill_stores_the_risk_of_closed_proposals_once(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _save(db, "a", rows=LOW_ROWS)
    _save(db, "b")
    _save(db, "c")
    db.finish_proposal("a", state="applied", result={})  # no risk_source yet: as before the upgrade
    db.finish_proposal("b", state="failed", result={})
    assert _risk(db, "a") is None and _risk(db, "b") is None

    def odd(row: dict) -> str:
        if row["id"] == "b":
            raise ValueError("cannot judge")
        return "low"

    db.risk_source = odd
    assert db.settle_risks() == 1  # one odd row does not stop the rest
    # the odd row keeps the sentinel, so no later scan judges it (and logs) again
    assert _risk(db, "a") == "low" and _risk(db, "b") == UNJUDGED and _risk(db, "c") is None
    asked: list[str] = []

    def counting(row: dict) -> str:
        asked.append(row["id"])
        return "low"

    db.risk_source = counting
    assert db.settle_risks() == 0 and asked == []
    db.risk_source = lambda row: "odd"  # no tier at all: the same sentinel
    db.close_proposal("c", "u", "withdrawn")
    assert _risk(db, "c") == UNJUDGED


def test_the_closers_settle_only_the_rows_they_closed(tmp_path: Path) -> None:
    """purge_expired and fail_interrupted_applies judge the proposals they closed just now, never
    every closed row with no tier (that is the one-time backfill, DeckService at startup)."""
    db = _db(tmp_path)
    _save(db, "a", rows=LOW_ROWS)
    _save(db, "b", rows=LOW_ROWS)
    _save(db, "c", rows=LOW_ROWS)
    db.finish_proposal("a", state="applied", result={})  # closed before the upgrade: no tier
    asked: list[str] = []

    def counting(row: dict) -> str:
        asked.append(row["id"])
        return "low"

    db.risk_source = counting
    with db.tx() as c:
        c.execute("UPDATE proposals SET expires_at = ? WHERE id = 'b'", (int(time.time()) - 10,))
        c.execute("UPDATE proposals SET state = 'applying' WHERE id = 'c'")
    db.purge_expired()
    assert asked == ["b"] and _risk(db, "b") == "low" and _risk(db, "a") is None
    assert db.fail_interrupted_applies() == 1
    assert asked == ["b", "c"] and _risk(db, "c") == "low" and _risk(db, "a") is None
    db.purge_expired()
    assert asked == ["b", "c"]  # a tick with nothing closed judges nothing
    assert db.settle_risks() == 1 and asked == ["b", "c", "a"]  # the backfill takes the rest


async def test_a_stored_tier_keeps_no_live_reason_and_an_unjudged_one_is_a_dash(stack: Stack) -> None:
    """A closed proposal shows the tier stored when it closed, with no reason worked out under
    today's settings (which may contradict it). One with nothing to judge it by (no review rows)
    shows a dash, not High, and the Risk filter never counts it as High."""
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        two = LOW_ROWS + [{"kind": "add", "name": "Arcane Signet", "qty": 1, "before": 0, "after": 1}]
        for pid, rows in (("lo", two), ("nr", None)):
            gw.db.save_proposal(
                {
                    "id": pid,
                    "owner_sub": sub,
                    "kind": "edit",
                    "deck_id": "42",
                    "deck_name": "Immortal Reckoning",
                    "baseline_fingerprint": "f",
                    "changes": [],
                    "diff_text": "+1 Sol Ring",
                    "expires_at": int(time.time()) + 3600,
                    "created_by_client": "__browser__",
                    **({"rows": rows} if rows is not None else {}),
                }
            )
            gw.db.finish_proposal(pid, state="applied", result={})
        assert _risk(gw.db, "lo") == "low" and _risk(gw.db, "nr") == UNJUDGED
        gw.live.set("auto_apply_max_rows", 1, by="admin-1")  # today it would read High
        assert gw.decks.proposal_risk(sub, gw.db.get_proposal("lo", sub))[0] == "high"
        shown = gw.decks.describe(sub, "lo")
        assert (shown["risk"], shown["risk_reason"]) == ("low", "judged when it closed")
        unjudged = gw.decks.describe(sub, "nr")
        assert unjudged["risk"] is None and unjudged["risk_reason"] == ""
        body = (await b.http.get("/proposals?show=all", headers=NAV)).text.split("<main")[1]
        rows = {m[0]: m[1] for m in re.findall(r"href='/proposals/(lo|nr)\?show=all'(.*?)</div>", body)}
        assert "Low</span>" in rows["lo"] and "High" not in rows["lo"]
        assert "Risk not judged" in rows["nr"] and "High" not in rows["nr"]
        high = (await b.http.get("/proposals?show=all&risk=high", headers=NAV)).text.split("<main")[1]
        assert "/proposals/nr?" not in high and "/proposals/lo?" not in high
        low = (await b.http.get("/proposals?show=all&risk=low", headers=NAV)).text.split("<main")[1]
        assert "/proposals/lo?" in low and "/proposals/nr?" not in low
        page = (await b.http.get("/proposals/nr", headers=NAV)).text.split("<aside")[1]
        assert "Risk not judged" in page and "High" not in page
    finally:
        await b.aclose()


def test_proposal_index_and_history_counts(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    seed_history(db, "u", now=now)
    index = db.proposal_index("u")
    assert len(index) == 60 and index[0]["id"] == "p00"  # every one, newest first
    assert {"risk", "board_no", "snapshot_id", "result"} <= set(index[0])
    counts = db.history_counts("u", deck_id=None, search="", since=None)
    assert counts["snapshots"] == 15 and counts["restores"] == 1  # p04, an applied restore
    # the search counts as the rows match: a restore by its deck's name or its change summary
    for q in ("immortal", "sol ring", "card 4", "reap"):
        rows = db.list_proposals("u", limit=100, kinds=["restore"], states=["applied"], search=q)
        assert db.history_counts("u", search=q)["restores"] == len(rows), q
    assert db.history_counts("u", search="sol ring")["restores"] == 1
    assert db.history_counts("u", search="sol ring")["snapshots"] == 0  # a snapshot has no summary
    assert db.history_counts("u", deck_id="43", search="", since=None)["snapshots"] == 0
    stats = db.snapshot_stats("u")
    assert stats["count"] == 15 and stats["decks"] == 1


# -- P1, P2, P3 --------------------------------------------------------------------
async def test_waiting_dashboard(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        seed_history(gw.db, sub, now=int(time.time()))
        r = await b.http.get("/proposals", headers=NAV)
        body = r.text.split("<main")[1]
        for lab in (
            "Waiting for you",
            "Needs a look",
            "Always need your press",
            "Last 7 days",
            "Waiting by app",
        ):
            assert re.search(rf"<span class='lab'>(<svg.*?</svg>)?{lab}</span>", body)
        assert "Undo last apply" not in body or "action='/proposals/undo'" in body
        # chips with counts; the filters, "The administrator" among From
        assert (
            "<a class='ui-chip' href='/proposals' aria-current='true'>Waiting <span class='n'>15</span>"
            in body
        )
        assert "All <span class='n'>60</span>" in body
        assert "placeholder='Search deck or card'" in body
        assert "<option value='admin'>The administrator</option>" in body
        assert (
            "<select name='risk' aria-label='Risk'>" in body
            and "<select name='when' aria-label='When'>" in body
        )
        # a grouped table with tick boxes and the selection bar
        assert "class='tr group'" in body and "id='deck-43'" in body
        assert body.count("name='pick'") == 15
        assert "class='ui-bar" in body and "data-long" in body
        assert "(deck 4" not in body
        # the deck filter narrows the table
        one = (await b.http.get("/proposals?deck_id=42", headers=NAV)).text.split("<main")[1]
        assert one.count("name='pick'") == 0
    finally:
        await b.aclose()


async def test_check_all_low_risk_only_ticks_rows(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        for pid, rows in (("lo", LOW_ROWS), ("hi", None)):
            gw.db.save_proposal(
                {
                    "id": pid,
                    "owner_sub": sub,
                    "kind": "edit",
                    "deck_id": "42",
                    "deck_name": "Sample Commander Deck",
                    "baseline_fingerprint": "f",
                    "changes": [],
                    "diff_text": "+1 Sol Ring",
                    "expires_at": int(time.time()) + 3600,
                    "created_by_client": "__browser__",
                    **({"rows": rows} if rows else {}),
                }
            )
        page = (await b.http.get("/proposals", headers=NAV)).text
        assert "Check all low-risk (1)" in page and "check=low" in page
        ticked = (await b.http.get("/proposals?check=low", headers=NAV)).text
        boxes = {
            m[0]: " checked" in m[1]
            for m in re.findall(r"<input type='checkbox' id='pk-(\w+)'([^>]*)>", ticked)
        }
        assert boxes == {"lo": True, "hi": False}
        assert gw.db.get_proposal("lo", sub)["state"] == "pending"  # nothing applied
    finally:
        await b.aclose()


async def test_all_view_pages_newest_first(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        seed_history(gw.db, sub, now=int(time.time()))
        body = (await b.http.get("/proposals?show=all", headers=NAV)).text.split("<main")[1]
        assert "To apply or reject several at once, open <a href='/proposals'>Waiting</a>." in body
        assert "name='pick'" not in body  # no tick boxes on All
        ids = re.findall(r"href='/proposals/(p\d\d)\?show=all'", body)
        assert ids[:3] == ["p00", "p01", "p02"] and len(set(ids)) == 25
        assert "1–25 of 60" in body and "offset=25" in body and "Earlier" not in body
        older = (await b.http.get("/proposals?show=all&offset=25", headers=NAV)).text
        assert "26–50 of 60" in older and "/proposals/p25?show=all" in older
        assert re.search(r"<span class='ui-lbl grey'>(<svg.*?</svg>)?Rejected</span>", body)
        # a failed one says why; the seeded ones store no error, so Archidekt did not refuse them
        assert "Could not be applied · <a href='/proposals/p03'>Why</a>" in body
        assert "Archidekt refused it" not in body
        assert "(deck 4" not in body
    finally:
        await b.aclose()


async def test_one_proposal_is_a_side_panel_or_a_page(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        seed_history(gw.db, sub, now=int(time.time()))
        gw.db.finish_proposal("p00", state="applied", result={"snapshot_id": "s00", "ok": True})
        panel = (await b.http.get("/proposals/p01", headers=NAV)).text.split("<main")[1]
        # the list stays beside the panel, which a narrow screen shows as the page itself
        assert "class='ui-split with-panel'" in panel and "<aside class='ui-drawer'" in panel
        assert "← Back to Proposals" in panel and "href='/proposals/p01?full=1'>Open as a full page" in panel
        assert "name='pick'" in panel.split("<aside")[0]
        assert "(deck 4" not in panel
        assert "14 other changes also wait on this deck." in panel and "Review all 15" in panel
        full = (await b.http.get("/proposals/p01?full=1", headers=NAV)).text.split("<main")[1]
        assert "class='pr-full'" in full and "name='pick'" not in full
        # a closed one: back to All, no expiry, its result data folded under Technical details
        closed = (await b.http.get("/proposals/p00", headers=NAV)).text.split("<main")[1]
        assert "href='/proposals?show=all'>← Back to Proposals" in closed
        assert "<dt>Expires</dt>" not in closed
        assert "<summary>Technical details</summary>" in closed
    finally:
        await b.aclose()


def test_a_failed_row_says_refused_only_when_archidekt_refused() -> None:
    """Archidekt's own no (a 4xx, a contract it did not keep) reads "Archidekt refused it"; the
    gateway restarting, a network error or a timeout and the backup copy read "Could not be
    applied", each with the Why link to the proposal."""
    from mtg_gateway.proposals_view import _closing_html

    def line(error: str | None) -> str:
        it = {
            "id": "f1",
            "state": "failed",
            "result": {"error": error} if error else {},
            "app": None,
            "applied_at": 0,
            "status_at": 0,
            "created_at": 0,
            "expires_at": 0,
        }
        return _closing_html(it, csrf=None, replaced_by=None, now=0.0)

    for refused in ("forbidden", "not_found", "auth", "rate_limited", "contract", "verify_mismatch"):
        assert line(refused) == "Archidekt refused it · <a href='/proposals/f1'>Why</a>", refused
    for other in ("interrupted", "unavailable", "internal", "backup_failed", None):
        assert line(other) == "Could not be applied · <a href='/proposals/f1'>Why</a>", other


# -- H1 ------------------------------------------------------------------------------
async def test_history_has_no_proposals(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        now = int(time.time())
        seed_history(gw.db, sub, now=now)
        store_report(gw.db, sub, "rep_a", taken_at=now - 100)
        store_report(gw.db, sub, "rep_z", deck_id="99", deck_name="", taken_at=now - 10)
        body = (await b.http.get("/history", headers=NAV)).text.split("<main")[1]
        assert "placeholder='Deck name'" in body
        # the Reports widget: the newest report's stored name, never its deck id; "All reports"
        assert "Latest: an unnamed deck, just now" in body and ">All reports</a>" in body
        assert "Deck 99" not in body and "Open Reports" not in body
        assert "Everything <span class='n'>18</span>" in body  # 15 snapshots, 1 restore, 2 reports
        assert "Snapshots <span class='n'>15</span>" in body and "Restores <span class='n'>1</span>" in body
        # no edit proposal is listed; a snapshot links the change it was taken before
        assert "+1 Counterspell" not in body and "Rejected" not in body
        rows = body.count("action='/history/restore'")
        assert rows == 15 and body.count("href='/history?open=s-") >= 30  # Details and the phone chevron
        assert "title='Snapshots do not record who made them'>—" in body
        assert "Snapshots kept</span>" in body and "Backup copies</span>" in body
        details = (await b.http.get("/history?open=s-s00", headers=NAV)).text.split("<main")[1]
        drawer = details.split("<aside class='ui-drawer'")[1]
        assert "← Back to History" in drawer and "Restore (review first)" in drawer and "<dd>—</dd>" in drawer
    finally:
        await b.aclose()


async def test_an_empty_waiting_widget_says_nothing_is_waiting(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        body = (await b.http.get("/proposals", headers=NAV)).text.split("<main")[1]
        assert "Nothing is waiting." in body and "Every change is reviewed." not in body
    finally:
        await b.aclose()


async def test_deck_menu_links_its_proposals(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/decks/42", headers=NAV)).text
        assert "<a href='/proposals?deck_id=42'>" in page and "Proposals for this deck</a>" in page
    finally:
        await b.aclose()


def test_cards_use_the_standard_risk_words() -> None:
    cards = Path(__file__).parents[1] / "src" / "mtg_gateway" / "static" / "cards"
    proposal = (cards / "proposal-card.html").read_text()
    assert 'consent: "Your press", destructive: "Delete"' in proposal
    assert "Needs your approval" not in proposal and "Destructive: deletes a deck" not in proposal
    assert "No press needed" in proposal
    assert 'pending: "Waiting"' not in proposal and '"Waiting"' in proposal
    waiting = (cards / "waiting-card.html").read_text()
    assert 'low: "Low", high: "High", consent: "Your press", destructive: "Delete"' in waiting


def test_applied_rows_close_with_the_date_and_undo() -> None:
    from mtg_gateway import proposals_view, ui

    now = 1_800_000_000
    it = {
        "id": "p1",
        "state": "applied",
        "kind": "edit",
        "applied_at": now - 3 * 86400,
        "status_at": 0,
        "created_at": now - 5 * 86400,
        "snapshot_id": "s1",
    }
    when = ui.when(now - 3 * 86400, now=now)
    out = proposals_view._closing_html(it, csrf="tok", replaced_by=None, now=now)
    assert out.startswith(f"Applied {when} · <form method='post' action='/proposals/undo'")
    assert ">3 days ago</time>" in when and ">Undo</button></form>" in out
    # no snapshot: the date alone; an old row without applied_at falls back to when it closed
    plain = proposals_view._closing_html({**it, "snapshot_id": None}, csrf="tok", replaced_by=None, now=now)
    assert plain == f"Applied {when}"
    old = {**it, "applied_at": 0, "status_at": now - 9 * 86400, "snapshot_id": None}
    assert proposals_view._closing_html(old, csrf="tok", replaced_by=None, now=now) == (
        f"Applied {ui.when(now - 9 * 86400, now=now)}"
    )
