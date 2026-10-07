"""Production readiness (0.6.0): snapshot and counter retention, purge schedule, checked backups,
transactional migrations and the indexes the purge and lists rely on."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from mtg_gateway import backup as backup_module
from mtg_gateway import db as db_module
from mtg_gateway.db import Database

from .conftest import FakeIdP, Harness, gw, idp, make_settings, running  # noqa: F401
from .test_decks_and_proxy import Browser


def _snap(db: Database, sid: str, *, owner: str = "u", deck: str = "1", age: int = 0) -> None:
    db.save_snapshot(
        sid, owner_sub=owner, deck_id=deck, proposal_id=None, fingerprint="f", deck={"cards": []}
    )
    with db.tx() as c:
        c.execute("UPDATE snapshots SET taken_at = ? WHERE id = ?", (int(time.time()) - age, sid))


def _ids(db: Database, table: str, col: str = "id") -> set[str]:
    with db.tx() as c:
        return {r[0] for r in c.execute(f"SELECT {col} FROM {table}")}


def test_snapshots_keep_newest_per_deck(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_module, "SNAPSHOTS_KEEP_PER_DECK", 2)
    db = Database(tmp_path / "g.sqlite")
    for i in range(4):
        _snap(db, f"a{i}", age=100 - i)  # a3 newest
    _snap(db, "b0", deck="2", age=1000)  # another deck: its only snapshot stays
    _snap(db, "other", owner="v", age=1000)  # another member's deck 1: counted separately
    db.purge_expired()
    assert _ids(db, "snapshots") == {"a2", "a3", "b0", "other"}
    db.close()


def test_snapshot_a_pending_restore_needs_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_module, "SNAPSHOTS_KEEP_PER_DECK", 1)
    db = Database(tmp_path / "g.sqlite")
    _snap(db, "old", age=500)
    _snap(db, "new", age=1)
    db.save_proposal(
        {
            "id": "p1",
            "owner_sub": "u",
            "kind": "restore",
            "deck_id": "1",
            "baseline_fingerprint": "f",
            "changes": {"snapshot_id": "old"},
            "diff_text": "",
            "expires_at": int(time.time()) + 3600,
        }
    )
    db.purge_expired()
    assert _ids(db, "snapshots") == {"old", "new"}
    db.reject_proposal("p1", "u")
    db.purge_expired()
    assert _ids(db, "snapshots") == {"new"}
    db.close()


def test_old_metrics_and_deck_covers_are_purged(tmp_path: Path):
    db = Database(tmp_path / "g.sqlite")
    db.metrics_increment("2000-01-01", "u", "tool", "old")
    db.metrics_increment(time.strftime("%Y-%m-%d", time.gmtime()), "u", "tool", "new")
    db.save_deck_cover("1", "uid-old")
    db.save_deck_cover("2", "uid-new")
    with db.tx() as c:
        c.execute(
            "UPDATE deck_covers SET updated_at = ? WHERE deck_id = '1'",
            (int(time.time()) - db_module.DECK_COVER_RETENTION_SECONDS - 60,),
        )
    db.purge_expired()
    assert _ids(db, "metrics", "name") == {"new"}
    assert _ids(db, "deck_covers", "deck_id") == {"2"}
    db.close()


def test_purge_indexes_exist_and_are_used(tmp_path: Path):
    db = Database(tmp_path / "g.sqlite")
    assert db.schema_version == db_module.SCHEMA_VERSION == 10
    with db.tx() as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {
            "audit_log_event",
            "audit_log_at",
            "browser_sessions_sub",
            "browser_sessions_expires",
            "tokens_expires",
            "snapshots_owner_taken",
            "snapshots_owner_deck",
        } <= names
        plan = " ".join(
            str(r[3]) for r in c.execute("EXPLAIN QUERY PLAN DELETE FROM tokens WHERE expires_at < 5")
        )
        assert "tokens_expires" in plan
        sql = "EXPLAIN QUERY PLAN SELECT id_hash FROM browser_sessions WHERE sub = 'x'"
        plan = " ".join(str(r[3]) for r in c.execute(sql))
        assert "browser_sessions_sub" in plan
    db.close()


def test_busy_timeout_is_set(tmp_path: Path):
    db = Database(tmp_path / "g.sqlite")
    with db.tx() as c:
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == db_module.BUSY_TIMEOUT_MS
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    db.close()


def test_failed_migration_step_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "g.sqlite"
    Database(path).close()

    def broken(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("boom")

    monkeypatch.setattr(db_module, "MIGRATIONS", [*db_module.MIGRATIONS, broken])
    with pytest.raises(RuntimeError, match="boom"):
        Database(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_module.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'half_done'").fetchone()[0] == 0
    conn.close()


def test_backup_is_a_checked_standalone_copy(tmp_path: Path):
    db = Database(tmp_path / "data" / "g.sqlite")
    db.audit("hello", sub="u")
    dest = backup_module.export_now(db, tmp_path / "backups", keep_days=14)
    conn = sqlite3.connect(dest)
    # A plain file (no -wal needed beside it) that passes its own check and holds the data.
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT event FROM audit_log").fetchone()[0] == "hello"
    conn.close()
    assert backup_module.newest_backup(tmp_path / "backups")[0] == dest
    db.close()


def test_backup_runs_while_the_gateway_holds_its_lock(tmp_path: Path):
    """The copy reads through its own connection, so it doesn't wait on (or stall) requests."""
    db = Database(tmp_path / "g.sqlite")
    db.audit("hello", sub="u")
    with db._lock:  # a request in the middle of using the shared connection
        done: list[bool] = []

        def run() -> None:
            db.backup_to(tmp_path / "copy.sqlite")
            done.append(True)

        import threading

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=10)
        assert done == [True]
    db.close()


def test_purge_loop_runs_without_backups_and_survives_errors(tmp_path: Path):
    calls: list[int] = []

    class FlakyDb:
        def purge_expired(self) -> int:
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            return 0

    async def run() -> None:
        task = asyncio.create_task(backup_module.purge_loop(FlakyDb(), interval=0.01))  # type: ignore[arg-type]
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(asyncio.wait_for(run(), timeout=5))
    assert len(calls) >= 3


def test_hung_mystic_forge_listing_gives_up_and_backs_off(monkeypatch: pytest.MonkeyPatch):
    from mtg_gateway import mf_proxy

    monkeypatch.setattr(mf_proxy, "LIST_DEADLINE_SECONDS", 0.05)
    opened: list[int] = []

    class Hung:
        async def __aenter__(self) -> Hung:
            opened.append(1)
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def list_tools(self) -> None:
            await asyncio.sleep(3600)

    proxy = mf_proxy.MysticForgeProxy("http://mf.test/mcp", client_factory=Hung)  # type: ignore[arg-type]

    async def run() -> None:
        assert await proxy.tools() == []
        assert await proxy.tools() == []  # within the back-off: no second wait

    started = time.monotonic()
    asyncio.run(run())
    assert time.monotonic() - started < 2
    assert opened == [1]


def test_startup_fails_interrupted_applies_at_once(tmp_path: Path):
    db = Database(tmp_path / "g.sqlite")
    db.save_proposal(
        {
            "id": "p1",
            "owner_sub": "u",
            "deck_id": "1",
            "baseline_fingerprint": "f",
            "changes": {},
            "diff_text": "",
            "expires_at": int(time.time()) + 3600,
        }
    )
    assert db.claim_proposal("p1", "u")
    db.finish_proposal("p1", state="applying", snapshot_id="s1")
    assert db.fail_interrupted_applies() == 1
    row = db.get_proposal("p1", "u")
    assert row["state"] == "failed"
    assert row["result"]["error"] == "interrupted" and row["result"]["snapshot_id"] == "s1"
    db.close()


def test_scan_changes_fit_propose_limits():
    from mtg_gateway.decks import MAX_CHANGES
    from mtg_gateway.scan.service import change_fields

    items = [{"card": {"name": "Island"}, "quantity": 150}]
    items += [{"card": {"name": f"Card {i}"}, "quantity": 1} for i in range(MAX_CHANGES)]
    out = change_fields(items)
    assert all(1 <= c["quantity"] <= 99 for c in out["changes"])
    assert sum(c["quantity"] for c in out["changes"] if c["card_name"] == "Island") == 150
    assert len(out["changes"]) == MAX_CHANGES + 2
    assert [len(b) for b in out["change_batches"]] == [MAX_CHANGES, 2]
    assert "change_batches" not in change_fields(items[:3])


# -- what people see when something goes wrong -------------------------------------------------

NAVIGATE = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}


async def test_unknown_address_gets_a_page_or_json_not_bare_text(gw: Harness) -> None:  # noqa: F811
    page = await gw.http.get("/no-such-page", headers=NAVIGATE)
    assert page.status_code == 404 and page.headers["content-type"].startswith("text/html")
    assert "Page not found" in page.text and "href='/decks'" in page.text
    assert "name='viewport'" in page.text
    api = await gw.http.get("/api/v1/no-such-thing", headers={"Accept": "text/html"})
    assert api.status_code == 404 and api.json()["ok"] is False


async def test_unexpected_error_is_a_plain_page_logged_and_counted(
    tmp_path: Path,
    idp: FakeIdP,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    h = Harness(make_settings(tmp_path), idp)
    async with running(h):
        b = Browser(h)
        await b.login()
        # Like a real server: the app answers, then the exception goes on to be logged.
        signed_in = b.http
        b.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app, raise_app_exceptions=False),
            base_url=signed_in.base_url,
            cookies=signed_in.cookies,
        )
        await signed_in.aclose()

        def boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("secret internal detail")

        monkeypatch.setattr(h.db, "list_proposals", boom)
        page = await b.http.get("/proposals", headers=NAVIGATE)
        assert page.status_code == 500 and "Something went wrong" in page.text
        assert "secret internal detail" not in page.text and "Traceback" not in page.text
        api = await b.http.get("/api/v1/proposals")
        assert api.status_code == 500 and api.json()["error"] == "server_error"
        assert "secret internal detail" not in api.text
        assert any(t["name"] == "server_error" for t in h.db.metrics_totals(1))
        await b.aclose()


async def test_every_page_loads_the_feedback_script_and_offline_worker(gw: Harness) -> None:  # noqa: F811
    b = Browser(gw)
    try:
        await b.login()
        page = await b.http.get("/account", headers=NAVIGATE)
        csp = page.headers["content-security-policy"]
        script_src = csp.split("script-src")[1].split(";")[0]
        assert script_src.strip() == "'self'"
        assert "<script src='/static/feedback.js' defer></script>" in page.text
        js = await b.http.get("/static/feedback.js")
        assert js.status_code == 200 and "aria-busy" in js.text and "/sw.js" in js.text
        sw = await b.http.get("/sw.js")
        assert "navigate" in sw.text and "You're offline" in sw.text and "caches" not in sw.text
    finally:
        await b.aclose()


def test_button_text_contrast_meets_wcag_aa():
    """Bold 16px labels need 4.5:1 (WCAG 2.x SC 1.4.3)."""
    from mtg_gateway import theme

    def lum(hexcolor: str) -> float:
        c = [int(hexcolor[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def ratio(a: str, b: str) -> float:
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    css = theme.CSS
    assert "color:var(--on-orange)" in css and "background:var(--danger-fill)" in css
    assert ratio("#fa890d", "#111111") >= 4.5  # primary buttons, warn badges
    assert ratio("#1ebb6c", "#111111") >= 4.5  # success buttons, ok badges
    assert ratio("#c0182b", "#ffffff") >= 4.5  # danger buttons and badges
    assert ratio("#2a66c9", "#ffffff") >= 4.5  # info badges


async def test_member_can_delete_their_data_from_the_account_page(gw: Harness) -> None:  # noqa: F811
    b = Browser(gw)
    try:
        await b.login()
        page = await b.http.get("/account", headers=NAVIGATE)
        assert "Delete my data" in page.text and "action' value='delete_data'" in page.text
        _snap(gw.db, "s1", owner="user-1")
        gw.db.metrics_increment("2026-10-06", "user-1", "tool", "whoami")
        csrf = await b.csrf("/account")
        # Without the confirmation box nothing happens.
        r = await b.http.post("/account", data={"csrf": csrf, "action": "delete_data"})
        assert r.status_code == 303 and r.headers["location"] == "/account?err=confirm_delete"
        assert gw.db.get_user("user-1") is not None
        r = await b.http.post("/account", data={"csrf": csrf, "action": "delete_data", "confirm": "yes"})
        assert r.status_code == 303 and r.headers["location"] == "/data-deleted"
        assert gw.db.get_user("user-1") is None and gw.db.list_snapshots("user-1") == []
        assert gw.db.metrics_totals(3650) == []
        assert any(e["event"] == "member_data_deleted" for e in gw.db.audit_recent(5))
        # Signed out: the session is gone with the data.
        again = await b.http.get("/account", headers=NAVIGATE)
        assert again.status_code == 302 and again.headers["location"].startswith("/login")
        done = await b.http.get("/data-deleted", headers=NAVIGATE)
        assert done.status_code == 200 and "Your data was deleted" in done.text
    finally:
        await b.aclose()
