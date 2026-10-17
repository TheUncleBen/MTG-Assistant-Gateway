"""Schema 14 (0.7.30, Q128, Q136 and card Q138): the simulator's card models, one per card for the
whole gateway. The member who added a model and an admin may remove it, the admin list shows
who added each, and a member's models go with their data. The cap per member and who may replace
a model are Track C's caller's to enforce."""

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
        assert new.schema_version == db_module.SCHEMA_VERSION == 14
        assert new.get_pref("u1", "card.size") == "l"
        columns = [r[1] for r in new._conn.execute("PRAGMA table_info(card_models)")]
        assert columns == [
            "card_key",
            "name",
            "model_json",
            "oracle_hash",
            "added_by",
            "added_at",
            "updated_at",
        ]
        indexes = {r[0] for r in new._conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert "idx_card_models_added_by" in indexes
    finally:
        new.close()


def test_one_model_per_card_and_the_newest_wins(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    try:
        assert db.put_card_model("sol ring", "Sol Ring", MODEL, "u1", 100, oracle_hash="h1") is True
        # the same member again: kept, and it already counted toward them
        assert db.put_card_model("sol ring", "Sol Ring", MODEL.replace("1", "2"), "u1", 150) is False
        assert db.count_card_models("u1") == 1
        # another member can't replace it; an admin's save does, and it now counts toward them
        with pytest.raises(CardModelRefused) as refused:
            db.put_card_model("sol ring", "Sol Ring", MODEL, "u2", 180)
        assert refused.value.why == "another_member"
        assert (
            db.put_card_model("sol ring", "Sol Ring", MODEL, "u2", 200, oracle_hash="h2", admin=True) is True
        )
        assert (db.count_card_models("u1"), db.count_card_models("u2")) == (0, 1)
        (row,) = db.get_card_models(["sol ring", "no such card"])
        assert row == {
            "card_key": "sol ring",
            "name": "Sol Ring",
            "model_json": MODEL,
            "oracle_hash": "h2",
            "added_by": "u2",
            "added_at": 100,  # from the first model
            "updated_at": 200,
        }
        assert db.get_card_models([]) == []
    finally:
        db.close()


def test_many_keys_are_read_in_slices(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    try:
        keys = [f"card {i}" for i in range(db_module._CARD_MODEL_BATCH * 2 + 7)]
        for i, key in enumerate(keys):
            db.put_card_model(key, key.title(), MODEL, "u1", i)
        got = db.get_card_models([*keys, keys[0]])  # a repeated key is read once
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
        assert db.get_card_models(["sol ring", "x"]) == []
    finally:
        db.close()


def test_delete_takes_only_the_members_own_model_for_now(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    try:
        db.put_card_model("sol ring", "Sol Ring", MODEL, "u1", 1)
        assert db.delete_card_model("sol ring", "u2") is False
        assert db.delete_card_model("sol ring", "u1") is True
        assert db.delete_card_model("sol ring", "u1") is False
        assert db.get_card_models(["sol ring"]) == []
    finally:
        db.close()


def test_an_admin_removes_any_members_model_and_it_is_audited(tmp_path: Path) -> None:
    """Q136 item 2: the member who added a model and an admin may remove it."""
    db = Database(tmp_path / "g.sqlite")
    try:
        db.put_card_model("sol ring", "Sol Ring", MODEL, "u1", 1)
        assert db.admin_delete_card_model("sol ring", by="admin-1") is True
        assert db.admin_delete_card_model("sol ring", by="admin-1") is False
        assert db.get_card_models(["sol ring"]) == [] and db.count_card_models("u1") == 0
        (row,) = [e for e in db.audit_recent(5, sub="admin-1") if e["event"] == "card_model_removed"]
        assert row["detail"] == {"card": "Sol Ring", "card_key": "sol ring", "added_by": "u1"}
    finally:
        db.close()


def test_the_admin_list_and_delete_my_data(tmp_path: Path) -> None:
    """Card Q138 items 4 and 5."""
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


def test_two_members_saving_the_same_new_card_at_once_keep_one(tmp_path: Path) -> None:
    """Track C review r1 item 18: the check and the write are one transaction, so of two saves of
    the same new card at the same moment one keeps it and the other is refused (before, both could
    pass a separate check and the second overwrote the first)."""
    import threading

    db = Database(tmp_path / "g.sqlite")
    try:
        for round_ in range(20):
            key = f"card {round_}"
            start = threading.Barrier(2)
            got: dict[str, object] = {}

            def save(sub: str, key: str = key, start: threading.Barrier = start, got: dict = got) -> None:
                start.wait()
                try:
                    got[sub] = db.put_card_model(key, key.title(), MODEL, sub, 1)
                except CardModelRefused as exc:
                    got[sub] = exc.why

            threads = [threading.Thread(target=save, args=(sub,)) for sub in ("u1", "u2")]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert sorted(map(str, got.values())) == ["True", "another_member"], got
            (row,) = db.get_card_models([key])
            assert got[row["added_by"]] is True
    finally:
        db.close()
