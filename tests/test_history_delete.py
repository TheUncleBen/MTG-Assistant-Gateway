"""Deleting one report or one snapshot (0.7.46): a Delete on each History row's menu, its Details and
the report's page opens the shared confirm box with its solid red button; the delete is the member's
own, CSRF-checked and recorded in their activity. A snapshot a waiting restore puts back can't be
deleted, and says why. The assistant's tools never delete: they hand the member the box to press."""

from __future__ import annotations

import html
import re
import time
from pathlib import Path

from mtg_gateway.db import SNAPSHOT_HELD_BACKUP, SNAPSHOT_HELD_RESTORE, Database

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_decks_and_proxy import call, mcp_token, structured
from .test_history_page import store_report

stack_options = stack_with(COMPANION)


def _snapshot(db, sub: str, sid: str) -> None:  # noqa: ANN001
    db.save_snapshot(
        sid,
        owner_sub=sub,
        deck_id="42",
        proposal_id=None,
        fingerprint="f",
        deck={"id": "42", "name": "Immortal Reckoning", "cards": []},
    )


def _csrf(text: str) -> str:
    m = re.search(r"name='csrf' value='([0-9a-f]+)'", text)
    assert m, text
    return m.group(1)


def _events(db, sub: str, event: str) -> list[dict]:  # noqa: ANN001
    return [r for r in db.audit_for_user(sub, 200) if r["event"] == event]


async def test_a_report_is_deleted_from_its_history_details_after_the_confirm_box(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        store_report(gw.db, sub, "rep_del", taken_at=int(time.time()) - 60)
        details = (await b.http.get("/history?open=r-rep_del", headers=NAV)).text
        assert "href='/history?open=r-rep_del&amp;box=delete' data-box-open='hy-del'>Delete…</a>" in details
        assert "class='btn btn-outline-danger'" in details.split("data-box-open='hy-del'")[0][-200:]
        assert (
            "id='hy-del' role='dialog'" in details and "data-open" not in details.split("id='hy-del'")[1][:90]
        )
        opened = (await b.http.get("/history?open=r-rep_del&box=delete", headers=NAV)).text
        box = (
            opened.split("id='hy-del'", 1)[1].split("</form>", 1)[0]
            + opened.split("id='hy-del'", 1)[1][:3000]
        )
        assert "Delete this report?" in opened and "data-open" in opened.split("id='hy-del'")[1][:120]
        assert "action='/history/reports/rep_del/delete'" in box
        assert "<button type='submit' class='danger' form='hy-delf'>Delete report</button>" in box
        # no CSRF token: refused, nothing deleted
        r = await b.http.post("/history/reports/rep_del/delete", data={})
        assert r.status_code == 403 and gw.reports.list(sub, None)
        r = await b.http.post("/history/reports/rep_del/delete", data={"csrf": _csrf(opened)})
        assert r.status_code == 303 and r.headers["location"] == "/history?deleted=report", r.text
        assert gw.reports.list(sub, None) == []
        assert [e["detail"]["report_id"] for e in _events(gw.db, sub, "report_deleted")] == ["rep_del"]
        after = (await b.http.get("/history?deleted=report", headers=NAV)).text
        assert "The report was deleted." in after
        # again: it is gone, and says so
        r = await b.http.post("/history/reports/rep_del/delete", data={"csrf": _csrf(opened)})
        assert r.status_code == 404
    finally:
        await b.aclose()


async def test_the_report_page_has_delete_and_the_reports_menu_opens_it(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        store_report(gw.db, sub, "rep_pg")
        page = (await b.http.get("/history/reports/rep_pg", headers=NAV)).text
        assert "href='/history/reports/rep_pg?box=delete' data-box-open='rep-del'>" in page
        opened = (await b.http.get("/history/reports/rep_pg?box=delete", headers=NAV)).text
        assert "data-open" in opened.split("id='rep-del'")[1][:120]
        assert "action='/history/reports/rep_pg/delete'" in opened
        js = (await b.http.get("/static/menus.js")).text
        assert 'item("Delete…", "trash"' in js or 'link("Delete…", "trash"' in js
    finally:
        await b.aclose()


async def test_a_snapshot_is_deleted_unless_a_waiting_restore_puts_it_back(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        _snapshot(gw.db, sub, "snapfree")
        _snapshot(gw.db, sub, "snapheld")
        gw.db.save_proposal(
            {
                "id": "prest",
                "owner_sub": sub,
                "kind": "restore",
                "deck_id": "42",
                "deck_name": "Immortal Reckoning",
                "baseline_fingerprint": "f",
                "changes": {"snapshot_id": "snapheld", "details": {}},
                "diff_text": "",
                "expires_at": int(time.time()) + 3600,
                "created_by_client": "__browser__",
            }
        )
        held = (await b.http.get("/history?open=s-snapheld&box=delete", headers=NAV)).text
        assert html.escape(SNAPSHOT_HELD_RESTORE) in held
        assert "action='/history/snapshots/snapheld/delete'" not in held
        csrf = _csrf(held)
        r = await b.http.post("/history/snapshots/snapheld/delete", data={"csrf": csrf})
        assert r.status_code == 409 and html.escape(SNAPSHOT_HELD_RESTORE) in r.text
        assert gw.db.get_snapshot("snapheld", sub) is not None
        free = (await b.http.get("/history?open=s-snapfree&box=delete", headers=NAV)).text
        assert "Delete this snapshot?" in free and "action='/history/snapshots/snapfree/delete'" in free
        r = await b.http.post("/history/snapshots/snapfree/delete", data={"csrf": csrf})
        assert r.status_code == 303 and r.headers["location"] == "/history?deleted=snapshot"
        assert gw.db.get_snapshot("snapfree", sub) is None
        assert [e["detail"]["snapshot_id"] for e in _events(gw.db, sub, "snapshot_deleted")] == ["snapfree"]
    finally:
        await b.aclose()


def test_the_delete_itself_checks_the_backup_hold_again(tmp_path: Path) -> None:
    """The route checks ``snapshot_held`` first, but a backup delete queued after that check still
    keeps the snapshot: the delete checks both holds in its own transaction."""
    db = Database(tmp_path / "g.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    _snapshot(db, "alice", "snapbackup")
    assert db.snapshot_held("snapbackup", "alice") is None
    db.queue_backup_delete(
        "snapbackup", sub="alice", backup_deck_id="900", deck_id="42", proposal_id=None, due=0
    )
    assert db.snapshot_held("snapbackup", "alice") == SNAPSHOT_HELD_BACKUP
    assert SNAPSHOT_HELD_BACKUP == (
        "The gateway still needs this snapshot to tidy up its backup copy on Archidekt, "
        "so it can't be deleted yet."
    )
    assert db.delete_snapshot("snapbackup", "alice") is False
    assert db.get_snapshot("snapbackup", "alice") is not None


async def test_another_members_report_or_snapshot_is_never_deleted(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        gw.db.upsert_user("other", email=None, name=None, preferred_username=None, groups=[])
        store_report(gw.db, "other", "rep_theirs")
        _snapshot(gw.db, "other", "snaptheirs")
        csrf = _csrf((await b.http.get("/history", headers=NAV)).text)
        r = await b.http.post("/history/reports/rep_theirs/delete", data={"csrf": csrf})
        assert r.status_code == 404
        r = await b.http.post("/history/snapshots/snaptheirs/delete", data={"csrf": csrf})
        assert r.status_code == 404
        assert gw.reports.list("other", None) and gw.db.get_snapshot("snaptheirs", "other")
    finally:
        await b.aclose()


async def test_the_assistant_proposes_a_delete_and_never_deletes(stack: Stack) -> None:
    b = await linked_browser(stack)
    await b.aclose()
    h = stack.h
    gw = h.app.state.gateway
    sub = gw.db._one("SELECT sub FROM users", ())["sub"]
    store_report(gw.db, sub, "rep_ai")
    _snapshot(gw.db, sub, "snapai")
    token = await mcp_token(h)
    out = structured(await call(h, token, "propose_delete_report", {"report_id": "rep_ai"}))
    assert out["ok"] and out["deleted"] is False, out
    assert out["delete_url"].endswith("/history/reports/rep_ai?box=delete"), out
    assert "press" in out["message"] and gw.reports.list(sub, None)
    out = structured(await call(h, token, "propose_delete_snapshot", {"snapshot_id": "snapai"}))
    assert out["ok"] and out["deleted"] is False and out["delete_url"].endswith("open=s-snapai&box=delete")
    assert gw.db.get_snapshot("snapai", sub) is not None
    nope = structured(await call(h, token, "propose_delete_report", {"report_id": "rep_nope"}))
    assert nope["ok"] is False and nope["error"] == "not_found", nope
