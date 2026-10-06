"""purge_expired ages out the audit log and caps the tables anonymous requests can grow."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mtg_gateway import db as db_module
from mtg_gateway.db import Database


def _count(db: Database, table: str) -> int:
    with db.tx() as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_audit_log_older_than_retention_is_pruned(tmp_path: Path):
    db = Database(tmp_path / "g.sqlite")
    db.audit("old_event", sub="u")
    db.audit("new_event", sub="u")
    with db.tx() as c:
        c.execute(
            "UPDATE audit_log SET at = ? WHERE event = 'old_event'",
            (int(time.time()) - db_module.AUDIT_RETENTION_SECONDS - 60,),
        )
    db.purge_expired()
    with db.tx() as c:
        events = [r[0] for r in c.execute("SELECT event FROM audit_log")]
    assert events == ["new_event"]
    db.close()


def test_login_sessions_capped_oldest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_module, "MAX_LOGIN_SESSIONS", 3)
    db = Database(tmp_path / "g.sqlite")
    db.save_client("c", {"client_name": "x"})
    for i in range(5):
        db.create_login_session(
            f"s{i}", client_id="c", params={}, oidc_nonce="n", oidc_code_verifier="v", ttl=600 + i
        )
    db.purge_expired()
    assert _count(db, "login_sessions") == 3
    assert not db.get_login_session_exists("s0") and not db.get_login_session_exists("s1")
    assert all(db.get_login_session_exists(f"s{i}") for i in (2, 3, 4))
    db.close()


def test_unused_clients_capped_oldest_first_and_used_ones_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(db_module, "MAX_UNUSED_CLIENTS", 2)
    db = Database(tmp_path / "g.sqlite")
    now = int(time.time())
    for i in range(5):
        db.save_client(f"c{i}", {"client_name": f"x{i}"})
    db.save_client("used", {"client_name": "used"})
    with db.tx() as c:
        for i in range(5):
            c.execute(
                "UPDATE oauth_clients SET created_at = ? WHERE client_id = ?", (now - 1000 + i, f"c{i}")
            )
        c.execute("UPDATE oauth_clients SET created_at = ? WHERE client_id = 'used'", (now - 5000,))
    db.save_token(
        "tok",
        kind="access",
        client_id="used",
        sub="u",
        scopes=["mtg"],
        resource=None,
        family="f",
        expires_at=now + 3600,
    )
    db.purge_expired()
    assert db.get_client("used") is not None  # has a live token, never counted
    assert [db.get_client(f"c{i}") is not None for i in range(5)] == [False, False, False, True, True]
    db.close()
