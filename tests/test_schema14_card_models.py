"""Schema 14 (0.7.30, Q128 and Q136): the simulator's card models, one per card for the whole
gateway. Storage only: the cap per member, what happens to a deleted member's models, who may
replace and who may delete a model are still to be decided, and nothing here enforces them."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mtg_gateway import db as db_module
from mtg_gateway.db import MAX_CARD_MODEL_BYTES, Database

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
        # another member's model replaces it and now counts toward them, not the first
        assert db.put_card_model("sol ring", "Sol Ring", MODEL, "u2", 200, oracle_hash="h2") is True
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
