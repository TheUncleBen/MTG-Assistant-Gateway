"""Schema 14 (0.7.30): the simulator's card models. Each member keeps their own, with the
simulator version each was checked with. The member and an admin may remove one, the admin list
shows who added each, a member's models go with their data, and the cap per member is checked in
the write's own transaction."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mtg_gateway import db as db_module
from mtg_gateway.db import MAX_CARD_MODEL_BYTES, CardModelRefused, Database

MODEL = '{"effects":[{"kind":"draw","count":1}]}'


def test_a_schema_13_database_gets_the_table_and_keeps_its_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "old.sqlite"
    monkeypatch.setattr(db_module, "MIGRATIONS", db_module.MIGRATIONS[:13])
    old = Database(path)
    assert old.schema_version == 13
    old.upsert_user("u1", email=None, name=None, preferred_username=None, groups=[])
    old.set_pref("u1", "card.size", "l")
    old.close()
    monkeypatch.undo()
    new = Database(path)
    try:
        assert new.schema_version == db_module.SCHEMA_VERSION == 19
        assert new.get_pref("u1", "card.size") == "l"
        columns = [r[1] for r in new._conn.execute("PRAGMA table_info(card_models)")]
        assert columns == [
            "card_key",
            "name",
            "model_json",
            "sim_version",
            "oracle_hash",
            "added_by",
            "added_at",
            "updated_at",
        ]
        # one model per card per member
        pk = sorted((r[5], r[1]) for r in new._conn.execute("PRAGMA table_info(card_models)") if r[5])
        assert pk == [(1, "card_key"), (2, "added_by")]
        indexes = {r[0] for r in new._conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert "idx_card_models_added_by" in indexes
    finally:
        new.close()


def test_each_member_keeps_their_own_model_of_a_card(tmp_path: Path) -> None:
    """One member's model never replaces or changes another's."""
    db = Database(tmp_path / "g.sqlite")
    try:
        assert (
            db.put_card_model("sol ring", "Sol Ring", MODEL, "u1", 100, sim_version="1.0", oracle_hash="h1")
            is True
        )
        # the same member again: their model is replaced, and it already counted toward them
        two = MODEL.replace("1", "2")
        assert db.put_card_model("sol ring", "Sol Ring", two, "u1", 150, sim_version="1.1") is False
        # another member saves their own model of the same card
        assert db.put_card_model("sol ring", "Sol Ring", MODEL, "u2", 200, sim_version="1.0") is True
        assert (db.count_card_models("u1"), db.count_card_models("u2")) == (1, 1)
        (mine,) = db.get_card_models(["sol ring", "no such card"], "u1")
        assert mine == {
            "card_key": "sol ring",
            "name": "Sol Ring",
            "model_json": two,
            "sim_version": "1.1",
            "oracle_hash": None,  # a save without it clears it
            "added_by": "u1",
            "added_at": 100,  # from their first model
            "updated_at": 150,
        }
        (theirs,) = db.get_card_models(["sol ring"], "u2")
        assert theirs["model_json"] == MODEL and theirs["added_by"] == "u2"
        assert db.get_card_models(["sol ring"], "u3") == []
        assert db.get_card_models([], "u1") == []
    finally:
        db.close()


def test_a_member_replacing_their_model_leaves_an_earlier_members_alone(tmp_path: Path) -> None:
    """The other member saves first, so a save or a replacement that lost
    its member condition would find, or overwrite, their row."""
    db = Database(tmp_path / "g.sqlite")
    try:
        assert db.put_card_model(
            "sol ring", "Sol Ring", MODEL, "u2", 100, sim_version="1.0", oracle_hash="h2"
        )
        before = db.get_card_models(["sol ring"], "u2")
        one, two = MODEL.replace("1", "3"), MODEL.replace("1", "4")
        assert db.put_card_model("sol ring", "Sol Ring", one, "u1", 200, sim_version="1.1") is True
        assert db.put_card_model("sol ring", "Sol Ring", two, "u1", 300, sim_version="1.2") is False
        assert db.get_card_models(["sol ring"], "u2") == before
        assert before[0]["model_json"] == MODEL and before[0]["updated_at"] == 100
        (mine,) = db.get_card_models(["sol ring"], "u1")
        assert (mine["model_json"], mine["added_at"], mine["updated_at"]) == (two, 200, 300)
        assert (db.count_card_models("u1"), db.count_card_models("u2")) == (1, 1)
    finally:
        db.close()


# card_models as builds before ccb56f6 made it (2870a24 and earlier): one model per card for the
# whole gateway and no simulator version. Copied from that build's _step_14.
EARLIER_14 = """CREATE TABLE IF NOT EXISTS card_models (
            card_key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            model_json TEXT NOT NULL CHECK (length(CAST(model_json AS BLOB)) <= 4096),
            oracle_hash TEXT,
            added_by TEXT NOT NULL,
            added_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )"""


def test_a_database_from_an_earlier_schema_14_build_is_brought_to_the_new_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 14 changed in place, so a database an earlier build already
    took to 14 is repaired at start, without a new schema number, and keeps its models."""
    path = tmp_path / "earlier14.sqlite"
    monkeypatch.setattr(db_module, "MIGRATIONS", db_module.MIGRATIONS[:13])
    Database(path).close()
    monkeypatch.undo()
    conn = sqlite3.connect(path)
    conn.execute(EARLIER_14)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_card_models_added_by ON card_models(added_by)")
    conn.execute("INSERT INTO card_models VALUES ('sol ring', 'Sol Ring', ?, 'h1', 'u1', 100, 150)", (MODEL,))
    conn.execute("PRAGMA user_version = 14")
    conn.commit()
    conn.close()
    for _ in range(2):  # the second start finds nothing to do
        db = Database(path)
        try:
            assert db.schema_version == 19
            (kept,) = db.get_card_models(["sol ring"], "u1")
            assert kept == {
                "card_key": "sol ring",
                "name": "Sol Ring",
                "model_json": MODEL,
                "sim_version": None,
                "oracle_hash": "h1",
                "added_by": "u1",
                "added_at": 100,
                "updated_at": 150,
            }
            pk = sorted((r[5], r[1]) for r in db._conn.execute("PRAGMA table_info(card_models)") if r[5])
            assert pk == [(1, "card_key"), (2, "added_by")]
            names = {r[0] for r in db._conn.execute("SELECT name FROM sqlite_master")}
            assert "idx_card_models_added_by" in names and "card_models_old" not in names
        finally:
            db.close()
    db = Database(path)
    try:  # and the new shape works: another member keeps their own model of the same card
        assert db.put_card_model("sol ring", "Sol Ring", MODEL, "u2", 200, sim_version="1.0") is True
        assert (db.count_card_models("u1"), db.count_card_models("u2")) == (1, 1)
    finally:
        db.close()


def test_many_keys_are_read_in_slices(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    try:
        keys = [f"card {i}" for i in range(db_module._CARD_MODEL_BATCH * 2 + 7)]
        for i, key in enumerate(keys):
            db.put_card_model(key, key.title(), MODEL, "u1", i)
        got = db.get_card_models([*keys, keys[0]], "u1")  # a repeated key is read once
        assert sorted(r["card_key"] for r in got) == sorted(keys)
        assert db.count_card_models("u1") == len(keys)
    finally:
        db.close()


def test_a_model_over_the_size_limit_or_without_a_key_is_refused(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    try:
        big = '{"x":"' + "a" * MAX_CARD_MODEL_BYTES + '"}'
        with pytest.raises(ValueError):
            db.put_card_model("sol ring", "Sol Ring", big, "u1", 1)
        with pytest.raises(ValueError):
            db.put_card_model("", "Sol Ring", MODEL, "u1", 1)
        # the table refuses it too, whatever writes to it
        with pytest.raises(sqlite3.IntegrityError), db.tx() as c:
            c.execute(
                "INSERT INTO card_models (card_key, name, model_json, added_by, added_at, updated_at) "
                "VALUES ('x', 'X', ?, 'u1', 1, 1)",
                (big,),
            )
        assert db.get_card_models(["sol ring", "x"], "u1") == []
    finally:
        db.close()


def test_a_member_deletes_only_their_own_model(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    try:
        db.put_card_model("sol ring", "Sol Ring", MODEL, "u1", 1)
        db.put_card_model("sol ring", "Sol Ring", MODEL, "u2", 1)
        assert db.delete_card_model("sol ring", "u3") is False
        assert db.delete_card_model("sol ring", "u1") is True
        assert db.delete_card_model("sol ring", "u1") is False
        assert db.get_card_models(["sol ring"], "u1") == []
        assert len(db.get_card_models(["sol ring"], "u2")) == 1
    finally:
        db.close()


def test_an_admin_removes_any_members_model_and_it_is_audited(tmp_path: Path) -> None:
    """The member who added a model and an admin may remove it."""
    db = Database(tmp_path / "g.sqlite")
    try:
        db.put_card_model("sol ring", "Sol Ring", MODEL, "u1", 1)
        db.put_card_model("sol ring", "Sol Ring", MODEL, "u2", 1)
        assert db.admin_delete_card_model("sol ring", "u1", by="admin-1") is True
        assert db.admin_delete_card_model("sol ring", "u1", by="admin-1") is False
        assert db.get_card_models(["sol ring"], "u1") == [] and db.count_card_models("u1") == 0
        assert db.count_card_models("u2") == 1  # only the one model named
        (row,) = [e for e in db.audit_recent(5, sub="admin-1") if e["event"] == "card_model_removed"]
        assert row["detail"] == {"card": "Sol Ring", "card_key": "sol ring", "added_by": "u1"}
    finally:
        db.close()


def test_the_admin_list_and_delete_my_data(tmp_path: Path) -> None:
    """The admin list shows who added each model, and deleting a member's data removes theirs."""
    db = Database(tmp_path / "g.sqlite")
    try:
        for sub in ("u1", "u2"):
            db.upsert_user(sub, email=None, name=None, preferred_username=sub, groups=[])
        db.put_card_model("sol ring", "Sol Ring", MODEL, "u1", 100)
        db.put_card_model("opt", "Opt", MODEL, "u2", 200)
        db.put_card_model("island", "Island", MODEL, "u1", 300)
        assert db.count_all_card_models() == 3
        listed = db.list_card_models()
        assert [m["card_key"] for m in listed] == ["island", "opt", "sol ring"]
        assert listed[0] == {
            "card_key": "island",
            "name": "Island",
            "added_by": "u1",
            "added_at": 300,
            "updated_at": 300,
            "size": len(MODEL.encode()),
        }
        assert [m["card_key"] for m in db.list_card_models(limit=1, offset=1)] == ["opt"]
        out = db.delete_member_data("u1")
        assert out["card_models"] == 2
        assert [m["card_key"] for m in db.list_card_models()] == ["opt"]
    finally:
        db.close()


def test_the_cap_counts_only_a_save_that_newly_counts(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    try:
        assert db.put_card_model("opt", "Opt", MODEL, "u1", 1, most=2) is True
        assert db.put_card_model("ponder", "Ponder", MODEL, "u1", 2, most=2) is True
        with pytest.raises(CardModelRefused) as refused:
            db.put_card_model("brainstorm", "Brainstorm", MODEL, "u1", 3, most=2)
        assert refused.value.why == "most"
        # replacing one they already keep is never refused
        assert db.put_card_model("opt", "Opt", MODEL.replace("1", "2"), "u1", 4, most=2) is False
        assert db.count_card_models("u1") == 2
    finally:
        db.close()


def test_two_saves_at_once_never_pass_the_cap_together(tmp_path: Path) -> None:
    """The cap check and the write are one transaction, so of two new
    cards a member saves at the same moment with one place left, one is kept and the other is
    refused (before, both could pass a separate count and the member went over the cap)."""
    import threading

    db = Database(tmp_path / "g.sqlite")
    try:
        for round_ in range(20):
            sub = f"u{round_}"
            start = threading.Barrier(2)
            got: dict[str, object] = {}

            def save(key: str, sub: str = sub, start: threading.Barrier = start, got: dict = got) -> None:
                start.wait()
                try:
                    got[key] = db.put_card_model(key, key.title(), MODEL, sub, 1, most=1)
                except CardModelRefused as exc:
                    got[key] = exc.why

            threads = [threading.Thread(target=save, args=(key,)) for key in ("opt", "ponder")]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert sorted(map(str, got.values())) == ["True", "most"], got
            assert db.count_card_models(sub) == 1
    finally:
        db.close()
