"""The purge runs in short transactions and batches (0.7.29): it gives the same
result as one big transaction, but never holds the lock for its whole run."""

from __future__ import annotations

import contextlib
import time
from pathlib import Path

import pytest

from mtg_gateway import db as db_module
from mtg_gateway.db import Database


def _old_audit_rows(db: Database, n: int) -> None:
    old = int(time.time()) - db_module.AUDIT_RETENTION_SECONDS - 60
    with db.tx() as c:
        c.executemany("INSERT INTO audit_log (at, event, sub) VALUES (?, 'old_event', 'u')", [(old,)] * n)


def _count(db: Database, sql: str) -> int:
    with db.tx() as c:
        return c.execute(sql).fetchone()[0]


def test_a_large_purge_deletes_everything_in_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_module, "PURGE_BATCH", 7)
    db = Database(tmp_path / "g.sqlite")
    _old_audit_rows(db, 50)
    db.audit("new_event", sub="u")
    old_day = db._since_day(db_module.METRICS_RETENTION_SECONDS // 86400 + 5)
    with db.tx() as c:
        c.executemany(
            "INSERT INTO metrics (day, kind, name, sub, n) VALUES (?, 'tool', ?, 'u', 1)",
            [(old_day, f"t{i}") for i in range(30)],
        )

    seen: list[int] = []
    real_tx = db.tx

    @contextlib.contextmanager
    def watched_tx():
        # The old audit rows left at the start of every transaction the purge opens.
        with real_tx() as c:
            seen.append(c.execute("SELECT COUNT(*) FROM audit_log WHERE event = 'old_event'").fetchone()[0])
            yield c

    monkeypatch.setattr(db, "tx", watched_tx)
    assert db.purge_expired() >= 80
    monkeypatch.setattr(db, "tx", real_tx)

    assert _count(db, "SELECT COUNT(*) FROM audit_log WHERE event = 'old_event'") == 0
    assert _count(db, "SELECT COUNT(*) FROM audit_log WHERE event = 'new_event'") == 1
    assert _count(db, "SELECT COUNT(*) FROM metrics") == 0
    # No transaction removed more than one batch of the old rows: the lock was given up
    # between batches (50 rows at 7 a batch is 8 transactions).
    drops = [a - b for a, b in zip(seen, seen[1:], strict=False) if a != b]
    assert drops and max(drops) <= 7
    assert len(drops) == 8
    db.close()


def test_another_request_gets_in_between_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_module, "PURGE_BATCH", 5)
    db = Database(tmp_path / "g.sqlite")
    _old_audit_rows(db, 20)
    real_tx = db.tx
    wrote: list[int] = []

    @contextlib.contextmanager
    def interleaved_tx():
        with real_tx() as c:
            yield c
        # Between two of the purge's transactions the lock is free: a member's write lands.
        if len(wrote) < 3:
            wrote.append(1)
            db.audit("member_write", sub="u")

    monkeypatch.setattr(db, "tx", interleaved_tx)
    db.purge_expired()
    monkeypatch.setattr(db, "tx", real_tx)
    assert _count(db, "SELECT COUNT(*) FROM audit_log WHERE event = 'member_write'") == 3
    assert _count(db, "SELECT COUNT(*) FROM audit_log WHERE event = 'old_event'") == 0
    db.close()


def test_a_purge_cut_short_is_finished_by_the_next_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_module, "PURGE_BATCH", 4)
    db = Database(tmp_path / "g.sqlite")
    _old_audit_rows(db, 10)
    real = db._delete_batched

    def failing(table, where, args):
        if table == "metrics":
            raise RuntimeError("disk hiccup")
        return real(table, where, args)

    monkeypatch.setattr(db, "_delete_batched", failing)
    with pytest.raises(RuntimeError):
        db.purge_expired()
    # The steps before the failure are kept (each committed on its own).
    assert _count(db, "SELECT COUNT(*) FROM audit_log WHERE event = 'old_event'") == 0
    monkeypatch.setattr(db, "_delete_batched", real)
    assert db.purge_expired() == 0
    db.close()


def test_snapshots_kept_per_deck_follow_the_setting(tmp_path: Path):
    db = Database(tmp_path / "g.sqlite")
    assert db.snapshots_keep() == db_module.SNAPSHOTS_KEEP_PER_DECK
    db.snapshots_per_deck = 7
    assert db.snapshots_keep() == 7
    db.close()
