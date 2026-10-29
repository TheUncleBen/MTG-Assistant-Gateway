"""Release 0.7.29, the shared first step: the schema 13 upgrade (settings, remembered choices, the
approvals' new columns and tables, snapshot summaries, the queued backup deletes' own table and the
Archidekt account on each), the settings registry with its live reads (a deployment value
wins), and the seams the next steps build on (social.js, the bracket display, the CSS hooks, the
waiting changes data and the folder helpers)."""

from __future__ import annotations

import dataclasses
import inspect
import json
import sqlite3
import time
from pathlib import Path

import pytest

from mtg_gateway import config, waiting_strip
from mtg_gateway import db as db_module
from mtg_gateway import decks as decks_module
from mtg_gateway.config import MANAGED_SETTINGS, ConfigError, LiveSettings, SettingError, load_settings
from mtg_gateway.db import Database

from .conftest import Stack, make_settings
from .test_decks_and_proxy import Browser


async def linked_browser(stack: Stack, who: str = "alice") -> Browser:
    b = Browser(stack.h)
    await b.login("/account")
    r = await b.link(who, f"pw-{who}")
    assert r.status_code == 303, r.text
    return b


NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
WEEK = 7 * 86400


# -- the upgrade from schema 12 ---------------------------------------------------------------------


def _deck(i: int, *, owner: int | None, cards: int = 60, name: object = "deck") -> dict:
    deck: dict = {
        "id": 1000 + i % 7,
        "name": name if name != "deck" else f"Deck {i}",
        "cards": [{"quantity": 1, "card": {"oracleCard": {"name": f"Card {n}"}}} for n in range(cards)],
    }
    if owner is not None:
        deck["owner"] = {"id": owner, "username": f"u{owner}"}
    return deck


def _schema_12(path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    monkeypatch.setattr(db_module, "MIGRATIONS", db_module.MIGRATIONS[:12])
    old = Database(path)
    assert old.schema_version == 12
    return old


def _fill_12(old: Database, *, snapshots: int) -> None:
    """A made-up 0.7.28 database: members, their links, snapshots, proposals and the queued
    backup deletes as 0.7.28 kept them (job_runs rows, ``backup_delete:<snapshot id>``)."""
    for n in range(1, 4):
        old.upsert_user(f"m{n}", email=None, name=None, preferred_username=f"m{n}", groups=[])
    old.save_link("m1", username="alice", user_id="78", secret_enc="x")
    old.save_link("m2", username="bob", user_id=None, secret_enc="x")  # a link that never got its id
    with old.tx() as c:
        for i in range(snapshots):
            sub = f"m{i % 3 + 1}"
            name: object = "deck"
            if i == 5:
                name = None  # a deck stored with no name
            deck = _deck(i, owner=None if i % 10 == 0 else 78, cards=60 + i % 40, name=name)
            c.execute(
                "INSERT INTO snapshots (id, owner_sub, deck_id, proposal_id, taken_at, fingerprint, "
                "deck_json) VALUES (?, ?, ?, NULL, ?, 'fp', ?)",
                (f"s{i:05d}", sub, str(deck["id"]), 1_700_000_000 + i, json.dumps(deck)),
            )
        c.execute(
            "INSERT INTO snapshots (id, owner_sub, deck_id, proposal_id, taken_at, fingerprint, deck_json) "
            "VALUES ('broken', 'm1', '1', NULL, 1, 'fp', 'not json')"
        )
        now = int(time.time())
        for pid, sub in (("p1", "m1"), ("p2", "m2")):
            c.execute(
                "INSERT INTO proposals (id, owner_sub, kind, deck_id, deck_name, baseline_fingerprint, "
                "changes_json, diff_text, state, created_at, expires_at) "
                "VALUES (?, ?, 'edit', '1001', 'D', 'fp', '[]', '+1 x', 'pending', ?, ?)",
                (pid, sub, now, now + 3600),
            )
        rows = [
            ("s00001", "m1", {"expect": {"name": "Deck 1 (backup)", "folder_id": 9}}),
            ("s00002", "m2", {}),
            ("s00004", "m1", {"attempts": 2}),
        ]
        for snap, sub, extra in rows:
            detail = {
                "sub": sub,
                "snapshot_id": snap,
                "backup_deck_id": "555" + snap[-1],
                "deck_id": "1001",
                "proposal_id": "p1",
                "attempts": 0,
                "queued_at": 1_700_000_100,
                **extra,
            }
            c.execute(
                "INSERT INTO job_runs (name, day, at, detail_json) VALUES (?, '2026-10-16', ?, ?)",
                ("backup_delete:" + snap, 1_700_000_500, json.dumps(detail)),
            )
        # a row the old code could never have run (no copy id), and an unrelated daily job
        c.execute(
            "INSERT INTO job_runs (name, day, at, detail_json) VALUES ('backup_delete:x', 'd', 1, '{}')"
        )
        c.execute("INSERT INTO job_runs (name, day, at, detail_json) VALUES ('prices', 'd', 1, NULL)")


def test_a_large_0_7_28_database_is_upgraded_to_13_and_keeps_every_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "old.sqlite"
    old = _schema_12(path, monkeypatch)
    # more than several slices of the step's batch, so the slice loop is exercised end to end
    _fill_12(old, snapshots=db_module._STEP_13_BATCH * 5 + 37)
    old.close()
    monkeypatch.undo()
    new = Database(path)
    try:
        assert new.schema_version == db_module.SCHEMA_VERSION == 19
        c = new._conn
        (count,) = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()
        assert count == db_module._STEP_13_BATCH * 5 + 38
        (missing,) = c.execute("SELECT COUNT(*) FROM snapshots WHERE card_count IS NULL").fetchone()
        assert missing == 1  # only the snapshot whose stored deck is not JSON
        row = c.execute("SELECT * FROM snapshots WHERE id = 's00007'").fetchone()
        assert (row["deck_name"], row["card_count"], row["deck_owner_id"]) == ("Deck 7", 67, "78")
        # Nothing recorded which account took an old snapshot, so none is made up
        assert (
            c.execute("SELECT COUNT(*) FROM snapshots WHERE archidekt_user_id IS NOT NULL").fetchone()[0] == 0
        )
        assert c.execute("SELECT deck_owner_id FROM snapshots WHERE id = 's00010'").fetchone()[0] is None
        assert c.execute("SELECT deck_name FROM snapshots WHERE id = 's00005'").fetchone()[0] == ""
        # proposals keep their rows: version 1, no account recorded, and a waiting one is numbered
        p1 = c.execute("SELECT * FROM proposals WHERE id = 'p1'").fetchone()
        assert p1["version"] == 1 and p1["board_no"] == 1 and p1["archidekt_user_id"] is None
        # the queued deletes moved into their own table with the account on the member's link
        moved = {r["snapshot_id"]: dict(r) for r in c.execute("SELECT * FROM backup_deletes")}
        assert set(moved) == {"s00001", "s00002", "s00004"}
        assert moved["s00001"]["archidekt_user_id"] == "78"
        assert moved["s00002"]["archidekt_user_id"] is None  # that link never recorded an id
        assert moved["s00004"]["attempts"] == 2 and moved["s00001"]["due_at"] == 1_700_000_500
        assert json.loads(moved["s00001"]["expect_json"]) == {"name": "Deck 1 (backup)", "folder_id": 9}
        assert moved["s00002"]["expect_json"] is None and moved["s00001"]["queued_at"] == 1_700_000_100
        names = [r[0] for r in c.execute("SELECT name FROM job_runs ORDER BY name")]
        assert names == ["prices"]  # every backup_delete row left job_runs, nothing else did
        # what the backup code reads back has the 0.7.28 keys
        [first, *_] = new.queued_backup_deletes()
        for key in ("sub", "snapshot_id", "backup_deck_id", "deck_id", "proposal_id", "attempts", "due"):
            assert key in first
        assert first["expect"] == {"name": "Deck 1 (backup)", "folder_id": 9}
        # lists read the new columns and still show the old values
        listed = {s["snapshot_id"]: s for s in new.list_snapshots("m2", limit=5000)}
        assert listed["s00007"]["deck_name"] == "Deck 7" and listed["s00007"]["card_count"] == 67
        assert listed["s00007"]["account_id"] == "78" and listed["s00007"]["linked_account_id"] is None
        found = {x["snapshot_id"] for x in new.list_snapshots("m3", search="Deck 8", limit=500)}
        assert "s00008" in found and all(
            new.get_snapshot(i, "m3")["deck"]["name"].startswith("Deck 8") for i in found
        )
        assert new.history_decks("m3")["1001"].startswith("Deck ")  # m3 has no proposals
    finally:
        new.close()


def test_an_interrupted_13_upgrade_leaves_12_and_runs_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "old.sqlite"
    old = _schema_12(path, monkeypatch)
    _fill_12(old, snapshots=450)
    old.close()
    monkeypatch.undo()
    real = db_module.snapshot_summary
    calls = {"n": 0}

    def dies_midway(deck):
        calls["n"] += 1
        if calls["n"] == 300:  # the second slice, after the first was already written
            raise KeyboardInterrupt("power cut")
        return real(deck)

    monkeypatch.setattr(db_module, "snapshot_summary", dies_midway)
    with pytest.raises(KeyboardInterrupt):
        Database(path)
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 12
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "backup_deletes" not in tables and "settings" not in tables
        columns = {r[1] for r in raw.execute("PRAGMA table_info(snapshots)")}
        assert "deck_name" not in columns  # the half-done step left nothing behind
        assert (
            raw.execute("SELECT COUNT(*) FROM job_runs WHERE name LIKE 'backup_delete:%'").fetchone()[0] == 4
        )
    finally:
        raw.close()
    monkeypatch.setattr(db_module, "snapshot_summary", real)
    again = Database(path)
    try:
        assert again.schema_version == db_module.SCHEMA_VERSION
        assert again._conn.execute("SELECT COUNT(*) FROM backup_deletes").fetchone()[0] == 3
        assert (
            again._conn.execute("SELECT COUNT(*) FROM snapshots WHERE deck_name IS NULL").fetchone()[0] == 0
        )
    finally:
        again.close()


def test_a_0_7_28_image_refuses_a_13_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "new.sqlite"
    Database(path).close()
    monkeypatch.setattr(db_module, "MIGRATIONS", db_module.MIGRATIONS[:12])
    with pytest.raises(RuntimeError, match="newer than this gateway supports"):
        Database(path)


def test_the_pending_queries_use_the_state_and_expiry_index(tmp_path: Path) -> None:
    """The expiry sweep no longer scans every proposal."""
    db = Database(tmp_path / "x.sqlite")
    try:
        plan = " ".join(
            str(r[-1])
            for r in db._conn.execute(
                "EXPLAIN QUERY PLAN UPDATE proposals SET state = 'expired' WHERE state = 'pending' "
                "AND expires_at < ?",
                (1,),
            )
        )
        assert "proposals_state_expires" in plan, plan
    finally:
        db.close()


# -- what new rows record --------------------------------------------------------------------------


def _member(db: Database, sub: str = "m1", account: str | None = "78") -> None:
    db.upsert_user(sub, email=None, name=None, preferred_username=sub, groups=[])
    db.save_link(sub, username=sub, user_id=account, secret_enc="x")


def _proposal(db: Database, pid: str, *, sub: str = "m1", deck: str = "1", **more: object) -> bool:
    row = {
        "id": pid,
        "owner_sub": sub,
        "deck_id": deck,
        "deck_name": "D",
        "baseline_fingerprint": "fp",
        "changes": [{"op": "add", "name": pid}],
        "diff_text": f"+1 {pid}",
        "expires_at": int(time.time()) + WEEK,
        "created_by_client": "app-1",
        **more,
    }
    return db.save_proposal(row)


def test_new_snapshots_and_proposals_record_the_linked_archidekt_account(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite")
    try:
        _member(db)
        assert db.save_snapshot(
            "s1", owner_sub="m1", deck_id="1", proposal_id=None, fingerprint="f", deck=_deck(1, owner=78)
        )
        [snap] = db.list_snapshots("m1")
        assert snap["linked_account_id"] == "78" and snap["account_id"] == "78" and snap["card_count"] == 60
        assert db.save_snapshot(
            "s2", owner_sub="m1", deck_id="1", proposal_id=None, fingerprint="f", deck={"name": None}
        )
        assert db.list_snapshots("m1")[0]["deck_name"] == ""  # was "None" before 0.7.29
        assert _proposal(db, "p1")
        assert db.get_proposal("p1", "m1")["archidekt_user_id"] == "78"
        assert _proposal(db, "p2", archidekt_user_id="99")  # the caller's own value wins
        assert db.get_proposal("p2", "m1")["archidekt_user_id"] == "99"
        db.queue_backup_delete("s1", sub="m1", backup_deck_id="5", deck_id="1", proposal_id="p1", due=1)
        assert db.queued_backup_deletes()[0]["archidekt_user_id"] == "78"
        # a relink to another account: new rows record the new one, old rows keep theirs
        db.save_link("m1", username="m1", user_id="91", secret_enc="y")
        assert _proposal(db, "p3")
        assert db.get_proposal("p3", "m1")["archidekt_user_id"] == "91"
        assert db.get_proposal("p1", "m1")["archidekt_user_id"] == "78"
        # no active link: nothing is recorded
        _member(db, "m2", account=None)
        assert _proposal(db, "q1", sub="m2")
        assert db.get_proposal("q1", "m2")["archidekt_user_id"] is None
    finally:
        db.close()


def test_board_numbers_count_up_per_deck_and_start_again_on_an_empty_board(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite")
    try:
        _member(db)
        for pid in ("a", "b", "c"):
            assert _proposal(db, pid)
        assert _proposal(db, "other", deck="2")
        assert [r["board_no"] for r in db.pending_board("m1", "1")] == [1, 2, 3]
        assert db.pending_board("m1", "2")[0]["board_no"] == 1
        assert db.pending_count_by_deck("m1") == {"1": 3, "2": 1}
        assert len(db.pending_board("m1")) == 4
        for pid in ("a", "b", "c"):
            assert db.close_proposal(pid, "m1", "withdrawn", "not needed", by="member")
        assert db.pending_board("m1", "1") == []
        assert _proposal(db, "d")
        assert db.pending_board("m1", "1")[0]["board_no"] == 1
    finally:
        db.close()


def test_an_amend_keeps_the_id_number_and_every_earlier_version(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite")
    try:
        _member(db)
        assert _proposal(db, "a")
        assert db.set_proposal_status("a", "m1", "needs_look", "the deck changed")
        v2 = db.amend_proposal(
            "a",
            "m1",
            expect_version=1,
            changes=[{"op": "add", "name": "b"}],
            rows=[{"name": "b"}],
            diff_text="+1 b",
            by_client="app-2",
            what_changed="swapped a for b",
        )
        assert v2 == 2
        p = db.get_proposal("a", "m1")
        assert p["version"] == 2 and p["board_no"] == 1 and p["diff_text"] == "+1 b"
        assert p["amended_by_client"] == "app-2" and p["version_note"] == "swapped a for b"
        assert p["status"] is None and p["status_reason"] is None  # a new version is looked at afresh
        # a second amend from an app that read version 1 loses; nothing changes
        assert (
            db.amend_proposal(
                "a", "m1", expect_version=1, changes=[], rows=None, diff_text="x", by_client="app-1"
            )
            is None
        )
        assert (
            db.amend_proposal(
                "a",
                "m1",
                expect_version=2,
                changes=[{"op": "add", "name": "c"}],
                rows=None,
                diff_text="+1 c",
                by_client="app-1",
                what_changed="c instead",
            )
            == 3
        )
        versions = db.proposal_versions("a", "m1")
        assert [v["version"] for v in versions] == [1, 2]
        assert versions[0]["made_by_client"] == "app-1" and versions[0]["what_changed"] is None
        assert versions[1]["made_by_client"] == "app-2" and versions[1]["what_changed"] == "swapped a for b"
        assert versions[1]["changes"] == [{"op": "add", "name": "b"}] and versions[1]["rows"] == [
            {"name": "b"}
        ]
        assert db.proposal_versions("a", "someone-else") == []
        # only a waiting proposal is amended
        assert db.claim_proposal("a", "m1")
        assert (
            db.amend_proposal(
                "a", "m1", expect_version=3, changes=[], rows=None, diff_text="x", by_client=None
            )
            is None
        )
    finally:
        db.close()


def test_status_close_reopen_and_links(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite")
    try:
        _member(db)
        _member(db, "m2")
        for pid in ("a", "b", "c"):
            assert _proposal(db, pid)
        assert _proposal(db, "theirs", sub="m2")
        with pytest.raises(ValueError):
            db.set_proposal_status("a", "m1", "nonsense")
        assert not db.set_proposal_status("a", "m2", "held")  # not theirs
        assert db.link_proposals("c", "m1", "replaces", "a")
        assert db.link_proposals("c", "m1", "replaces", "b")  # one note can replace several
        assert db.link_proposals("c", "m1", "replaces", "b")  # a repeat is harmless
        assert db.link_proposals("b", "m1", "depends_on", "a")
        assert not db.link_proposals("a", "m1", "depends_on", "a")
        assert not db.link_proposals("a", "m1", "depends_on", "theirs")  # another member's
        with pytest.raises(ValueError):
            db.link_proposals("a", "m1", "blocks", "b")
        links = db.proposal_links("m1", ["a"])
        assert {(r["proposal_id"], r["kind"], r["other_id"]) for r in links} == {
            ("c", "replaces", "a"),
            ("b", "depends_on", "a"),
        }
        assert db.proposal_links("m2", ["a"]) == []
        assert db.close_proposal("a", "m1", "replaced", "replaced by c", by={"client": "app-1"})
        assert not db.close_proposal("a", "m1", "replaced")  # no longer waiting
        with pytest.raises(ValueError):
            db.close_proposal("b", "m1", "applied")
        p = db.get_proposal("a", "m1")
        assert p["state"] == "replaced" and p["status_reason"] == "replaced by c"
        assert db.reopen_proposal("a", "m1")  # c rejected: a comes back
        p = db.get_proposal("a", "m1")
        assert p["state"] == "pending" and p["status_reason"] is None
        assert not db.reopen_proposal("a", "m1")  # already waiting
        # a withdrawn note that expired stays closed
        assert db.close_proposal("b", "m1", "withdrawn")
        with db.tx() as c:
            c.execute("UPDATE proposals SET expires_at = 1 WHERE id = 'b'")
        assert not db.reopen_proposal("b", "m1")
        # links and versions go with their proposal
        with db.tx() as c:
            c.execute("DELETE FROM proposals WHERE id = 'c'")
        assert {r["proposal_id"] for r in db.proposal_links("m1", ["a"])} == {"b"}
    finally:
        db.close()


# -- remembered choices and "Delete my data" -------------------------------------------------------


def test_remembered_choices_are_capped_per_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(tmp_path / "x.sqlite")
    try:
        _member(db)
        assert db.set_pref("m1", "deck.view", "grid") and db.get_pref("m1", "deck.view") == "grid"
        assert db.set_pref("m1", "deck.view", "stacks")  # replaced, not added
        assert db.get_prefs("m1") == {"deck.view": "stacks"}
        assert not db.set_pref("nobody", "deck.view", "grid")  # not a known user
        assert not db.set_pref("m1", "", 1) and not db.set_pref("m1", "x" * (db_module.MAX_PREF_NAME + 1), 1)
        assert not db.set_pref("m1", "big", "x" * db_module.MAX_PREF_BYTES)
        assert db.set_pref("m1", "big", "x" * (db_module.MAX_PREF_BYTES - 2))  # 4 KB as JSON, quotes too
        monkeypatch.setattr(db_module, "MAX_PREFS_PER_ACCOUNT", 3)
        assert db.set_pref("m1", "third", 3)
        assert not db.set_pref("m1", "fourth", 4)
        assert db.set_pref("m1", "third", 33)  # a known name can still change at the cap
        assert db.delete_pref("m1", "third") and not db.delete_pref("m1", "third")
        assert db.get_pref("m1", "third", "default") == "default"
    finally:
        db.close()


def test_delete_my_data_removes_the_new_tables_too(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite")
    try:
        _member(db)
        _member(db, "m2")
        assert db.set_pref("m1", "deck.view", "grid") and db.set_pref("m2", "deck.view", "grid")
        assert _proposal(db, "a") and _proposal(db, "b")
        assert db.link_proposals("b", "m1", "depends_on", "a")
        assert db.amend_proposal(
            "a", "m1", expect_version=1, changes=[], rows=None, diff_text="x", by_client=None
        )
        db.queue_backup_delete("s1", sub="m1", backup_deck_id="5", deck_id="1", proposal_id="a", due=1)
        db.queue_backup_delete("s2", sub="m2", backup_deck_id="6", deck_id="1", proposal_id=None, due=1)
        out = db.delete_member_data("m1")
        assert out["account_prefs"] == 1 and out["backup_deletes"] == 1 and out["proposals"] == 2
        c = db._conn
        assert c.execute("SELECT COUNT(*) FROM proposal_versions").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM proposal_links").fetchone()[0] == 0
        assert [r[0] for r in c.execute("SELECT snapshot_id FROM backup_deletes")] == ["s2"]
        assert db.get_prefs("m2") == {"deck.view": "grid"}
    finally:
        db.close()


# -- the settings registry and live reads ----------------------------------------------------------


def test_every_registered_setting_is_a_deployment_setting_with_sane_bounds() -> None:
    fields = {f.name: f for f in dataclasses.fields(config.Settings)}
    for key, spec in MANAGED_SETTINGS.items():
        assert key in fields, key
        assert spec.env == "" or spec.env.startswith("MTG_"), key
        # the code default is the page's default, so a value the page never stored reads the same
        # (a sign-in time counts seconds in Settings and days or hours on the page: ``scale``)
        assert fields[key].default == spec.default * spec.scale, key
        assert spec.parse(spec.default) == spec.default, key
        if spec.lo is not None:
            assert spec.lo <= spec.default <= spec.hi, key
    # What stays in the deployment only
    for key in ("membership_check_ttl", "writes_enabled", "public_url"):
        assert key not in MANAGED_SETTINGS


def test_pacing_can_only_be_made_gentler_from_the_page() -> None:
    """Be gentle on Archidekt: the page can't make the gateway call Archidekt or
    Scryfall faster than today."""
    faster_when_higher = (
        "archidekt_calls_per_10_min",
        "archidekt_max_per_minute",
        "archidekt_retries",
        "scan_art_images_per_hour",
        "watch_max_cards",  # each watched card is priced through Scryfall daily
        "watch_max_decks",  # each watched deck is read from Archidekt daily
    )
    faster_when_lower = (
        "archidekt_min_interval",
        "archidekt_backoff_base",
        "archidekt_cache_seconds",
        "archidekt_card_cache_seconds",
        "scryfall_lookup_interval",
        "scan_art_image_interval",
        "deck_sync_minutes",  # the background deck sync reads Archidekt every this many minutes
    )
    # 0.7.33 lowered the shipped pace from 40 to 30 requests a minute; the page may still go back
    # to 40, the pace before, and never past it.
    top = {"archidekt_max_per_minute": 40}
    assert MANAGED_SETTINGS["archidekt_max_per_minute"].default == 30
    for key in faster_when_higher:
        spec = MANAGED_SETTINGS[key]
        assert spec.hi == top.get(key, spec.default), key
        with pytest.raises(SettingError):
            spec.parse(spec.hi + 1)
    for key in faster_when_lower:
        spec = MANAGED_SETTINGS[key]
        assert spec.lo == spec.default, key
    assert MANAGED_SETTINGS["archidekt_calls_per_10_min"].default == 120
    # deck sync: 0 turns it off; anything else is every 30 minutes or less often
    sync = MANAGED_SETTINGS["deck_sync_minutes"]
    assert sync.parse("0") == 0 and sync.parse(30) == 30 and sync.parse(1440) == 1440
    for faster in (1, 29):
        with pytest.raises(
            SettingError, match="^deck_sync_minutes must be 0 \\(off\\) or between 30 and 1,440 minutes$"
        ):
            sync.parse(faster)


def test_live_settings_default_stored_and_deployment_values(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite")
    try:
        settings = make_settings(tmp_path, deck_sync_minutes=60, env_set=frozenset({"deck_sync_minutes"}))
        live = LiveSettings(settings, db)
        heard: list[str] = []
        live.on_change.append(heard.append)
        # code default, nothing stored
        assert live.get("forge_parallel_games") == 4 and live.source("forge_parallel_games") == "default"
        assert live.set("forge_parallel_games", "3", by="admin-1") == 3
        assert live.get("forge_parallel_games") == 3 and live.source("forge_parallel_games") == "dashboard"
        assert heard == ["forge_parallel_games"]
        # a value outside the bounds, or of the wrong kind, is refused and nothing changes
        for bad in ("0", "17", "two", True, None):
            with pytest.raises(SettingError):
                live.set("forge_parallel_games", bad, by="admin-1")
        assert live.get("forge_parallel_games") == 3
        # a deployment value wins and locks the setting
        assert live.get("deck_sync_minutes") == 60 and live.locked("deck_sync_minutes")
        assert live.source("deck_sync_minutes") == "deployment"
        with pytest.raises(
            SettingError,
            match=r"^deck_sync_minutes is set in your stack \(MTG_DECK_SYNC_MINUTES\), so it can't be "
            r"changed here\. Change or remove it in your stack, then redeploy\.$",
        ):
            live.set("deck_sync_minutes", 90, by="admin-1")
        with pytest.raises(SettingError):
            live.reset("deck_sync_minutes", by="admin-1")
        # a setting with no env variable is never locked
        assert not live.locked("forge_games")
        # another instance (a restart) reads what was stored
        again = LiveSettings(settings, db)
        assert again.get("forge_parallel_games") == 3
        live.reset("forge_parallel_games", by="admin-1")
        assert live.get("forge_parallel_games") == 4 and db.stored_settings() == {}
        assert heard == ["forge_parallel_games", "forge_parallel_games"]
        # each change is in the audit log, with who made it
        audit = [a for a in db.audit_recent(10) if a["event"] == "setting_changed"]
        details = [a["detail"] for a in reversed(audit)]
        assert details == [
            {"key": "forge_parallel_games", "value": 3, "previous": 4},
            {"key": "forge_parallel_games", "value": 4, "previous": 3},
        ]
        assert all(a["sub"] == "admin-1" for a in audit)
        with pytest.raises(SettingError):
            live.get("membership_check_ttl")  # deployment-only, not a managed key
        # booleans, choices and text
        assert live.set("archidekt_backup_box_default", "on", by=None) is True
        assert live.set("approval_mode_default", "semi", by=None) == "semi"
        with pytest.raises(SettingError):
            live.set("approval_mode_default", "yolo", by=None)
        assert live.set("server_name", "  My   gateway ", by=None) == "My gateway"
        assert live.set("archidekt_backup_folder", " Old  decks ", by=None) == "Old decks"
        with pytest.raises(SettingError):
            live.set("archidekt_backup_folder", "x" * 101, by=None)
        with pytest.raises(SettingError):
            live.set("server_name", "", by=None)
        described = {d["key"]: d for d in live.describe()}
        assert described["deck_sync_minutes"]["source"] == "deployment"
        assert described["deck_sync_minutes"]["value"] == 60
        assert described["approval_mode_default"]["changed"]["updated_by"] is None
        assert set(described) == set(MANAGED_SETTINGS)
    finally:
        db.close()


def test_a_stored_value_outside_newer_bounds_falls_back_to_the_default(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite")
    try:
        db.save_setting("archidekt_calls_per_10_min", 5000, by="admin")  # allowed by an older build
        db.save_setting("not_a_setting_any_more", 1, by="admin")
        live = LiveSettings(make_settings(tmp_path), db)
        assert live.get("archidekt_calls_per_10_min") == 120
        assert live.source("archidekt_calls_per_10_min") == "default"
        # an unknown key is a programming error; its message reads right in a background job's log
        with pytest.raises(
            SettingError, match="^not_a_setting_any_more isn't one of the Settings page's settings$"
        ):
            live.get("not_a_setting_any_more")
    finally:
        db.close()


def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    (tmp_path / "fernet").write_text(Fernet.generate_key().decode())
    (tmp_path / "session").write_text("s" * 40)
    (tmp_path / "oidc").write_text("secret")
    monkeypatch.setenv("MTG_FERNET_KEY_FILE", str(tmp_path / "fernet"))
    monkeypatch.setenv("MTG_SESSION_SECRET_FILE", str(tmp_path / "session"))
    monkeypatch.setenv("MTG_OIDC_CLIENT_SECRET_FILE", str(tmp_path / "oidc"))
    monkeypatch.setenv("MTG_PUBLIC_URL", "https://gateway.example.com")
    monkeypatch.setenv("MTG_OIDC_ISSUER", "https://auth.example.test/application/o/mtg/")
    monkeypatch.setenv("MTG_OIDC_CLIENT_ID", "abc")
    monkeypatch.setenv("MTG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MTG_REQUIRED_GROUP", "mtg-users")
    for spec in MANAGED_SETTINGS.values():
        monkeypatch.delenv(spec.env, raising=False)


def test_forge_games_at_once_go_up_to_16(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Settings page's Forge games at once go up to 16."""
    spec = MANAGED_SETTINGS["forge_parallel_games"]
    assert (spec.lo, spec.hi, spec.default) == (1, 16, 4)
    assert spec.parse("16") == 16 and spec.parse(5) == 5
    with pytest.raises(SettingError, match="^forge_parallel_games must be between 1 and 16$"):
        spec.parse(17)


def test_the_deployment_marks_what_it_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(tmp_path, monkeypatch)
    s = load_settings()
    assert s.env_set == frozenset()
    assert (s.proposal_expiry_days, s.auto_big_edit_rows, s.forge_parallel_games) == (7, 20, 4)
    assert (s.archidekt_backup_copies, s.snapshots_per_deck, s.archidekt_backup_box_default) == (
        10,
        25,
        False,
    )
    monkeypatch.setenv("MTG_WATCHLISTS_ENABLED", "false")
    monkeypatch.setenv("MTG_DECK_SYNC_MINUTES", "0")
    monkeypatch.setenv("MTG_UPDATE_CHECK", "")  # blank: left to the page
    monkeypatch.setenv("MTG_APPLY_IN_CHAT", "   ")  # only spaces: blank too
    s = load_settings()
    assert s.env_set == {"watchlists_enabled", "deck_sync_minutes"}
    db = Database(tmp_path / "x.sqlite")
    try:
        live = LiveSettings(s, db)
        assert live.get("watchlists_enabled") is False and live.get("deck_sync_minutes") == 0
        assert not live.locked("update_check") and not live.locked("apply_in_chat")
    finally:
        db.close()


def test_a_setting_of_only_spaces_is_unset_at_start_up_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before, only spaces counted as unset for the page's lock but not at start-up, so
    every number setting stopped the gateway, four on/off settings read Off and a log level of
    spaces broke logging. Now start-up reads it as unset, and spaces around a value are dropped."""
    _env(tmp_path, monkeypatch)
    defaults = load_settings()
    for spec in MANAGED_SETTINGS.values():
        if spec.env:
            monkeypatch.setenv(spec.env, "   ")
    s = load_settings()
    assert s.env_set == frozenset()
    for key in MANAGED_SETTINGS:
        assert getattr(s, key) == getattr(defaults, key), key
    assert s.archidekt_backups is True and s.apply_in_chat is True
    for env, value in (("MTG_DECK_SYNC_MINUTES", " 60 "), ("MTG_APPLY_IN_CHAT", " false ")):
        monkeypatch.setenv(env, value)
    s = load_settings()
    assert (s.deck_sync_minutes, s.apply_in_chat) == (60, False)


async def test_the_gateway_shares_one_live_settings(stack: Stack) -> None:
    gw = stack.h.app.state.gateway
    assert gw.live is gw.decks.live
    gw.live.set("forge_parallel_games", 2, by="admin-1")
    assert gw.decks.live.get("forge_parallel_games") == 2


# -- the seams for the next steps -------------------------------------------------------------------


async def test_social_actions_load_on_the_deck_page_and_the_user_page_only(stack: Stack) -> None:
    """The social actions moved to static/social.js; it loads after deck.js where social
    controls are drawn (the deck page and an Archidekt user page), not on Search or Precons."""
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/decks/42", headers=NAV)).text
        assert page.index("/static/deck.js") < page.index("/static/social.js")
        # Follow shows on another member's page (alice, linked here as amy)
        await b.aclose()
        b = await linked_browser(stack, "amy")
        user = (await b.http.get("/users/alice", headers=NAV)).text
        assert "data-social='follow'" in user
        assert user.index("/static/deck.js") < user.index("/static/social.js")
        for path in ("/search", "/precons"):
            assert "/static/social.js" not in (await b.http.get(path, headers=NAV)).text, path
        js = (await b.http.get("/static/social.js")).text
        assert "function socialRequest(" in js and "/social/api" in js
        deck_js = (await b.http.get("/static/deck.js")).text
        assert "function socialRequest(" not in deck_js
    finally:
        await b.aclose()


async def test_the_deck_page_asks_for_its_waiting_changes(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deck route hands the strip's data to the page; empty until the strip is built."""
    asked: list[tuple] = []

    def spy(db, sub, deck_id, *, own, **_kw):
        asked.append((sub, str(deck_id), own))
        return []

    from mtg_gateway import companion_decks

    monkeypatch.setattr(companion_decks, "waiting_changes", spy)
    b = await linked_browser(stack)
    try:
        r = await b.http.get("/decks/42", headers=NAV)
        assert r.status_code == 200 and asked == [("user-1", "42", True)]
    finally:
        await b.aclose()
    assert waiting_strip.waiting_changes(None, "x", 1, own=True) == []


def test_the_folder_helpers_keep_their_frozen_interfaces() -> None:
    """The folder and deck actions build on these three as they are."""
    assert str(inspect.signature(decks_module.DeckService._not_the_backup_name)) == (
        "(self, name: 'str', depth: 'int') -> 'None'"
    )
    assert str(inspect.signature(decks_module._shown_folders)) == (
        "(info: 'dict[str, Any]') -> 'list[dict[str, Any]]'"
    )
    assert str(inspect.signature(decks_module._no_such_folder)) == (
        "(info: 'dict[str, Any]', name: 'Any', *, several: 'bool' = False) -> 'DeckError'"
    )
    info = {
        "folders": [
            {"name": "Top", "depth": 0},
            {"name": "Mine", "depth": 1},
            {"name": "Backups", "depth": 1, "backup": True},
        ]
    }
    assert [f["name"] for f in decks_module._shown_folders(info)] == ["Mine"]
    err = decks_module._no_such_folder(info, "Backups")
    assert err.kind == "invalid" and "Your folders: Mine" in str(err)


# -- waiting notes: board numbers, limits and versions ---------------------------------------------


def test_waiting_changes_at_the_upgrade_get_their_board_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proposals waiting (or being applied) when schema 13 arrives are numbered per
    member and deck, oldest first, and a new one follows them."""
    path = tmp_path / "old.sqlite"
    old = _schema_12(path, monkeypatch)
    old.upsert_user("m1", email=None, name=None, preferred_username="m1", groups=[])
    old.upsert_user("m2", email=None, name=None, preferred_username="m2", groups=[])
    now = int(time.time())
    rows = [  # id, owner, deck, state, created_at
        ("c", "m1", "1", "pending", now - 10),
        ("a", "m1", "1", "pending", now - 30),
        ("b", "m1", "1", "applying", now - 20),
        ("done", "m1", "1", "applied", now - 40),
        ("x", "m1", "2", "pending", now - 5),
        ("y", "m2", "1", "pending", now - 50),
    ]
    with old.tx() as c:
        for pid, sub, deck, state, at in rows:
            c.execute(
                "INSERT INTO proposals (id, owner_sub, kind, deck_id, baseline_fingerprint, "
                "changes_json, diff_text, state, created_at, expires_at) "
                "VALUES (?, ?, 'edit', ?, 'fp', '[]', 'd', ?, ?, ?)",
                (pid, sub, deck, state, at, now + WEEK),
            )
    old.close()
    monkeypatch.undo()
    db = Database(path)
    try:
        number = {r["id"]: r["board_no"] for r in db._conn.execute("SELECT id, board_no FROM proposals")}
        assert number == {"a": 1, "b": 2, "c": 3, "done": None, "x": 1, "y": 1}
        assert _proposal(db, "new")
        assert db.get_proposal("new", "m1")["board_no"] == 4
    finally:
        db.close()


def test_a_note_being_applied_keeps_its_board_number(tmp_path: Path) -> None:
    """A note saved while another is being applied doesn't take its number, so the
    board never shows two #1s when that apply goes back to waiting."""
    db = Database(tmp_path / "x.sqlite")
    try:
        _member(db)
        assert _proposal(db, "a")
        assert db.claim_proposal("a", "m1")
        assert _proposal(db, "b")
        db.finish_proposal("a", state="pending")  # nothing changed: back to waiting
        assert [(r["id"], r["board_no"]) for r in db.pending_board("m1", "1")] == [("a", 1), ("b", 2)]
    finally:
        db.close()


def test_restoring_a_note_keeps_the_per_account_limit(tmp_path: Path) -> None:
    """An account keeps at most 100 waiting; restoring a withdrawn note counts too."""
    db = Database(tmp_path / "x.sqlite")
    try:
        _member(db)
        for pid in ("a", "b"):
            assert _proposal(db, pid)
        assert db.close_proposal("a", "m1", "withdrawn")
        assert _proposal(db, "c")  # two waiting again
        assert not db.reopen_proposal("a", "m1", max_pending=2)
        assert db.get_proposal("a", "m1")["state"] == "withdrawn"
        assert db.reopen_proposal("a", "m1", max_pending=3)
        assert db.count_pending_proposals("m1") == 3
    finally:
        db.close()


def test_a_waiting_note_keeps_a_limited_number_of_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An app that keeps amending one note can't grow the database without bound."""
    assert db_module.MAX_PROPOSAL_VERSIONS == 50
    monkeypatch.setattr(db_module, "MAX_PROPOSAL_VERSIONS", 3)
    db = Database(tmp_path / "x.sqlite")
    try:
        _member(db)
        assert _proposal(db, "a")
        for version in (1, 2, 3):
            assert (
                db.amend_proposal(
                    "a",
                    "m1",
                    expect_version=version,
                    changes=[],
                    rows=None,
                    diff_text=f"v{version + 1}",
                    by_client="app",
                )
                == version + 1
            )
        # the cap is told apart from a version conflict (the 3 kept versions are full here)
        with pytest.raises(db_module.ProposalVersionsFull):
            db.amend_proposal(
                "a", "m1", expect_version=4, changes=[], rows=None, diff_text="v5", by_client="app"
            )
        assert (
            db.amend_proposal(
                "a", "m1", expect_version=3, changes=[], rows=None, diff_text="v5", by_client="app"
            )
            is None
        )  # a stale version is still None, checked before the cap
        assert db.get_proposal("a", "m1")["diff_text"] == "v4"
        assert len(db.proposal_versions("a", "m1")) == 3
    finally:
        db.close()


# -- an amend keeps its hold; settings refusals -----------------------------------------------------


def test_an_amend_keeps_the_hold_of_a_rejected_dependency(tmp_path: Path) -> None:
    """A note held because a note it depends on was rejected stays held when an app amends
    it, so the new version can't apply by itself in semi or auto. Any other status clears."""
    db = Database(tmp_path / "x.sqlite")
    try:
        _member(db)
        for pid in ("base", "top", "other"):
            assert _proposal(db, pid)
        assert db.link_proposals("top", "m1", "depends_on", "base")
        with db.tx() as c:
            c.execute("UPDATE proposals SET state = 'rejected' WHERE id = 'base'")
        assert db.set_proposal_status("top", "m1", "held", "#1 was rejected")
        assert db.amend_proposal(
            "top", "m1", expect_version=1, changes=[], rows=None, diff_text="v2", by_client="a"
        )
        p = db.get_proposal("top", "m1")
        assert (p["status"], p["status_reason"], p["version"]) == ("held", "#1 was rejected", 2)
        # held with no rejected dependency (the member's own hold): a new version clears it
        assert db.set_proposal_status("other", "m1", "held", "later")
        assert db.amend_proposal(
            "other", "m1", expect_version=1, changes=[], rows=None, diff_text="v2", by_client="a"
        )
        assert db.get_proposal("other", "m1")["status"] is None
        # needs a look with a rejected dependency: cleared too (only a hold is kept)
        assert db.set_proposal_status("top", "m1", "needs_look", "deck changed")
        assert db.amend_proposal(
            "top", "m1", expect_version=2, changes=[], rows=None, diff_text="v3", by_client="a"
        )
        assert db.get_proposal("top", "m1")["status"] is None
    finally:
        db.close()


def test_settings_refusals_read_as_the_pages_do() -> None:
    """Units, thousands commas, the Account page's mode names and the stack wording."""
    with pytest.raises(SettingError, match="^backup_keep_days must be between 1 and 3,650 days$"):
        MANAGED_SETTINGS["backup_keep_days"].parse(5000)
    with pytest.raises(SettingError, match="^archidekt_min_interval must be between 1 and 10 seconds$"):
        MANAGED_SETTINGS["archidekt_min_interval"].parse(0.5)
    with pytest.raises(SettingError, match="^watch_max_cards must be between 0 and 5,000$"):
        MANAGED_SETTINGS["watch_max_cards"].parse(5001)
    with pytest.raises(SettingError) as refused:
        MANAGED_SETTINGS["approval_mode_default"].parse("yolo")
    assert refused.value.message == (
        'must be one of "Ask me every time", "Apply low-risk changes without asking" or '
        '"Apply changes without asking"'
    )


def test_the_stack_keeps_the_deck_checks_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MTG_DECK_SYNC_MINUTES takes what the page takes: 0 (off), or 30 to 1440, so the stack can't
    make the background check ask Archidekt more often than the page allows."""
    _env(tmp_path, monkeypatch)
    for ok in ("0", "30", "1440"):
        monkeypatch.setenv("MTG_DECK_SYNC_MINUTES", ok)
        assert load_settings().deck_sync_minutes == int(ok)
    for bad in ("1", "29", "1441"):
        monkeypatch.setenv("MTG_DECK_SYNC_MINUTES", bad)
        with pytest.raises(ConfigError, match=r"MTG_DECK_SYNC_MINUTES must be 0 \(off\), or between 30"):
            load_settings()


def test_mtg_server_name_locks_the_gateway_name_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ben, 2026-10-28: MTG_SERVER_NAME is back. Empty leaves the Gateway name to the Settings page;
    a name locks that row with the stack's value."""
    _env(tmp_path, monkeypatch)
    assert MANAGED_SETTINGS["server_name"].env == "MTG_SERVER_NAME"
    monkeypatch.setenv("MTG_SERVER_NAME", "")
    s = load_settings()
    assert s.server_name == "MTG Assistant Gateway" and "server_name" not in s.env_set
    monkeypatch.setenv("MTG_SERVER_NAME", "  Kitchen Table Magic ")
    s = load_settings()
    assert s.server_name == "Kitchen Table Magic" and s.env_set == {"server_name"}
    db = Database(tmp_path / "x.sqlite")
    try:
        live = LiveSettings(s, db)
        assert live.locked("server_name") and live.get("server_name") == "Kitchen Table Magic"
        with pytest.raises(SettingError, match=r"\(MTG_SERVER_NAME\)"):
            live.set("server_name", "Other", by="admin-1")
    finally:
        db.close()


def test_a_report_plays_five_forge_games_by_default() -> None:
    from mtg_gateway.config import Settings

    assert MANAGED_SETTINGS["forge_games"].default == 5
    assert Settings.__dataclass_fields__["forge_games"].default == 5
