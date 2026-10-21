"""The copy saved before a database upgrade, the upgrade happening whole or not at all, and going
back to an older version (upgrade_backup.py)."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from mtg_gateway import db as dbmod
from mtg_gateway import upgrade_backup
from mtg_gateway.db import Database
from mtg_gateway.upgrade_backup import DatabaseUpgradeError


def _old_database(path: Path, schema: int) -> None:
    """A database written by a gateway at ``schema``, with one member and one live token."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for i, step in enumerate(dbmod.MIGRATIONS[:schema], 1):
        step(conn)
        conn.execute(f"PRAGMA user_version = {i}")
    conn.execute(
        "INSERT INTO users (sub, email, name, preferred_username, groups_json, first_login_at, last_login_at)"
        " VALUES ('u1', 'a@b', 'A', 'a', '[]', 1, 2)"
    )
    conn.execute(
        "INSERT INTO tokens (token_hash, kind, client_id, sub, scopes_json, family, expires_at, created_at)"
        " VALUES ('h', 'refresh', 'c', 'u1', '[]', 'f', 4102444800, 1)"
    )
    conn.commit()
    conn.close()


def _version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _newer(monkeypatch: pytest.MonkeyPatch, *steps) -> None:
    monkeypatch.setattr(dbmod, "MIGRATIONS", [*dbmod.MIGRATIONS, *steps])


def _add_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE newer_thing (id INTEGER PRIMARY KEY)")


def test_an_upgrade_saves_a_checked_owner_only_copy_first(tmp_path: Path) -> None:
    path, folder = tmp_path / "data" / "g.sqlite", tmp_path / "backups" / "before-upgrade"
    path.parent.mkdir()
    _old_database(path, 14)
    db = Database(path, upgrade_dir=folder)
    try:
        assert db.schema_version == dbmod.SCHEMA_VERSION
    finally:
        db.close()
    (copy, schema), *rest = upgrade_backup.copies(folder)
    assert not rest and schema == 14 and copy.name.endswith("-schema14.sqlite")
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600 and stat.S_IMODE(folder.stat().st_mode) == 0o700
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
        assert conn.execute("SELECT sub FROM users").fetchall() == [("u1",)]
        # the copy is the database as it was: nothing blanked, no WAL needed beside it
        assert conn.execute("SELECT revoked FROM tokens").fetchone()[0] == 0
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()
    assert not list(folder.glob(".work-*"))
    assert upgrade_backup.last_event["kind"] == "upgraded"
    # opening again upgrades nothing and saves nothing
    Database(path, upgrade_dir=folder).close()
    assert len(upgrade_backup.copies(folder)) == 1


def test_a_new_database_and_one_already_current_get_no_copy(tmp_path: Path) -> None:
    folder = tmp_path / "before-upgrade"
    Database(tmp_path / "new.sqlite", upgrade_dir=folder).close()
    Database(tmp_path / "new.sqlite", upgrade_dir=folder).close()
    assert not folder.exists()


def test_without_a_folder_the_copy_goes_next_to_the_database(tmp_path: Path) -> None:
    path = tmp_path / "g.sqlite"
    _old_database(path, 14)
    Database(path).close()
    assert len(upgrade_backup.copies(tmp_path / "before-upgrade")) == 1


def test_no_copy_no_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "g.sqlite"
    _old_database(path, 14)
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")  # the folder can't be made under a file
    with pytest.raises(DatabaseUpgradeError, match="was not upgraded .*Nothing was changed"):
        Database(path, upgrade_dir=blocker / "before-upgrade")
    assert _version(path) == 14


def test_too_little_free_space_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "g.sqlite"
    _old_database(path, 14)
    monkeypatch.setattr(upgrade_backup.shutil, "disk_usage", lambda p: SimpleNamespace(free=1024 * 1024))
    with pytest.raises(
        DatabaseUpgradeError, match="not enough free space .* needs about \\d+ MB and has 1 MB"
    ):
        Database(path, upgrade_dir=tmp_path / "before-upgrade")
    assert _version(path) == 14


def test_an_upgrade_happens_whole_or_not_at_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "g.sqlite"
    Database(path).close()

    def broken(conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("step failed")

    _newer(monkeypatch, _add_table, broken)
    with pytest.raises(sqlite3.OperationalError):
        Database(path)
    assert _version(path) == 15
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'newer_thing'").fetchone() is None
    finally:
        conn.close()


def test_the_newest_three_copies_are_kept(tmp_path: Path) -> None:
    folder = tmp_path / "before-upgrade"
    folder.mkdir()
    for day in range(1, 6):
        (folder / f"mtg-gateway-before-upgrade-2026010{day}T000000Z-schema14.sqlite").write_bytes(b"")
    path = tmp_path / "g.sqlite"
    _old_database(path, 14)
    Database(path, upgrade_dir=folder).close()
    names = [f.name for f, _ in upgrade_backup.copies(folder)]
    assert len(names) == upgrade_backup.KEEP == 3
    assert names[1:] == [
        "mtg-gateway-before-upgrade-20260105T000000Z-schema14.sqlite",
        "mtg-gateway-before-upgrade-20260104T000000Z-schema14.sqlite",
    ]


def test_an_older_version_goes_back_to_its_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 15)
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        newer = Database(path, upgrade_dir=folder)
        assert newer.schema_version == 16
        newer._conn.execute(
            "INSERT INTO users (sub, groups_json, first_login_at, last_login_at) VALUES ('u2', '[]', 3, 3)"
        )
        newer.close()
    # back on this version: the copy saved before the upgrade is put back
    db = Database(path, upgrade_dir=folder)
    try:
        assert db.schema_version == 15
        assert [r[0] for r in db._conn.execute("SELECT sub FROM users")] == ["u1"]
        assert db._conn.execute("SELECT revoked FROM tokens").fetchone()[0] == 1  # everyone signs in again
        assert db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        db.close()
    ((aside, schema),) = upgrade_backup.copies(folder, upgrade_backup.SET_ASIDE_PREFIX)
    assert schema == 16 and stat.S_IMODE(aside.stat().st_mode) == 0o600
    conn = sqlite3.connect(aside)
    try:
        # nothing is lost: the newer database, with the change made since the upgrade
        assert {r[0] for r in conn.execute("SELECT sub FROM users")} == {"u1", "u2"}
    finally:
        conn.close()
    assert upgrade_backup.last_event["kind"] == "went-back"
    # the next start is an ordinary one
    Database(path, upgrade_dir=folder).close()
    assert len(upgrade_backup.copies(folder, upgrade_backup.SET_ASIDE_PREFIX)) == 1


def test_without_its_copy_an_older_version_refuses(tmp_path: Path) -> None:
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 15)
    folder.mkdir()
    # a copy of another schema is no use, nor is a damaged one
    (folder / "mtg-gateway-before-upgrade-20260101T000000Z-schema14.sqlite").write_bytes(b"")
    (folder / "mtg-gateway-before-upgrade-20260102T000000Z-schema15.sqlite").write_bytes(b"junk" * 1000)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 16")
    conn.close()
    with pytest.raises(DatabaseUpgradeError, match="newer than this gateway supports .*has no copy"):
        Database(path, upgrade_dir=folder)
    assert _version(path) == 16
    assert not upgrade_backup.copies(folder, upgrade_backup.SET_ASIDE_PREFIX)


# -- the stack files need no edit: the Authentik secret is always attached ------------------------
ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["deploy/portainer-stack.yml", "deploy/compose/docker-compose.yml"])
def test_the_stack_files_attach_the_authentik_secret_with_no_line_to_uncomment(name: str) -> None:
    text = (ROOT / name).read_text()
    assert "# mtg_authentik_api_token" not in text and "# - mtg_authentik_api_token" not in text
    assert "\n  mtg_authentik_api_token:\n" in text and "\n      - mtg_authentik_api_token\n" in text
    assert (
        "MTG_AUTHENTIK_API_TOKEN_FILE: ${MTG_AUTHENTIK_API_TOKEN_FILE:-/run/secrets/mtg_authentik_api_token}"
        in text
    )


def test_make_secrets_writes_off_and_keeps_a_token_already_there(tmp_path: Path) -> None:
    import shutil
    import subprocess

    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    script = ROOT / "deploy" / "compose" / "make-secrets.sh"
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "mtg_oidc_client_secret.txt").write_text("x")  # so the script asks nothing
    env = {"PATH": os.environ["PATH"], "MTG_SECRETS_DIR": str(secrets)}
    subprocess.run(["sh", str(script)], cwd=tmp_path, env=env, check=True, capture_output=True)
    assert (secrets / "mtg_authentik_api_token.txt").read_text() == "off"
    (secrets / "mtg_authentik_api_token.txt").write_text("a-real-token")
    subprocess.run(["sh", str(script)], cwd=tmp_path, env=env, check=True, capture_output=True)
    assert (secrets / "mtg_authentik_api_token.txt").read_text() == "a-real-token"


# -- the admin page says where the copy is, and when this start went back ------------------------
def test_the_admin_system_lines() -> None:
    from mtg_gateway.admin import _before_upgrade_text, _went_back_notice

    assert "none yet" in str(_before_upgrade_text({"folder": "/backups/before-upgrade", "copies": 0}))
    line = str(
        _before_upgrade_text(
            {
                "folder": "/backups/before-upgrade",
                "copies": 1,
                "newest": "mtg-gateway-before-upgrade-x.sqlite",
            }
        )
    )
    assert "mtg-gateway-before-upgrade-x.sqlite" in line and "/backups/before-upgrade" in line
    assert _went_back_notice({"folder": "f", "event": {"kind": "upgraded"}}) == ""
    notice = _went_back_notice(
        {
            "folder": "/backups/before-upgrade",
            "event": {"kind": "went-back", "file": "a.sqlite", "aside": "b.sqlite"},
        }
    )
    assert "notice warn" in notice and "a.sqlite" in notice and "b.sqlite" in notice


async def test_the_admin_page_shows_the_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_gateway.admin import system_data

    path = tmp_path / "g.sqlite"
    _old_database(path, 14)
    db = Database(path, upgrade_dir=tmp_path / "b" / "before-upgrade")
    try:
        state = SimpleNamespace(db=db, settings=SimpleNamespace(backup_dir=tmp_path / "b"))
        got = system_data(state)["before_upgrade"]
        assert got["copies"] == 1 and got["newest"].endswith("-schema14.sqlite")
        assert got["folder"] == str(tmp_path / "b" / "before-upgrade")
    finally:
        db.close()


# -- found in review: what must not come undone, crash loops, another process's work folder ------
def test_going_back_keeps_members_disabled_unlinked_and_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 15)
    conn = sqlite3.connect(path)
    for sub in ("u2", "u3"):
        conn.execute(
            "INSERT INTO users (sub, groups_json, first_login_at, last_login_at) VALUES (?, '[]', 1, 1)",
            (sub,),
        )
    conn.execute(
        "INSERT INTO proposals (id, owner_sub, deck_id, baseline_fingerprint, changes_json, diff_text,"
        " created_at, expires_at) VALUES ('p1', 'u1', 'd1', 'fp', '[]', 'diff', 1, 4102444800)"
    )
    conn.execute(
        "INSERT INTO proposal_versions (proposal_id, version, changes_json, diff_text, made_at)"
        " VALUES ('p1', 1, '[]', 'the diff', 1)"
    )
    for sub in ("u1", "u2"):
        conn.execute(
            "INSERT INTO archidekt_links (sub, archidekt_username, secret_enc, created_at, refreshed_at)"
            " VALUES (?, 'name', 'sealed', 1, 1)",
            (sub,),
        )
    conn.commit()
    conn.close()
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        newer = Database(path, upgrade_dir=folder)
        assert newer.set_user_disabled("u3", True)  # an admin disabled u3 after the upgrade
        newer._conn.execute("UPDATE archidekt_links SET secret_enc = '', status = 'revoked' WHERE sub = 'u2'")
        newer.delete_member_data("u1")  # u1 asked for their data to go
        newer.close()
    db = Database(path, upgrade_dir=folder)
    try:
        assert db.schema_version == 15
        assert db.get_user("u1") is None
        # their proposal's versions go with it (deletes cascade on the prepared copy too)
        assert db._conn.execute("SELECT count(*) FROM proposal_versions").fetchone()[0] == 0
        assert db._conn.execute("SELECT count(*) FROM archidekt_links WHERE sub = 'u1'").fetchone()[0] == 0
        assert db._conn.execute("SELECT secret_enc FROM archidekt_links WHERE sub = 'u2'").fetchone()[0] == ""
        assert db.get_user("u3")["disabled_at"] and db.get_user("u2")["disabled_at"] is None
        event = db._conn.execute(
            "SELECT detail_json FROM audit_log WHERE event = 'database_went_back'"
        ).fetchone()
        assert event and "set_aside" in event[0]
    finally:
        db.close()


def test_going_back_changes_the_database_in_one_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The copy is made ready (sign-ins ended) in a work file before it goes in with one backup, so
    a failure while it is made ready leaves the newer database in use exactly as it was."""
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 15)
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        Database(path, upgrade_dir=folder).close()

    def crash(*a: object) -> None:
        raise sqlite3.OperationalError("killed")

    with monkeypatch.context() as m:
        m.setattr(upgrade_backup, "_carry_forward", crash)
        with pytest.raises(DatabaseUpgradeError, match="Nothing was changed"):
            Database(path, upgrade_dir=folder)
    assert _version(path) == 16
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT revoked FROM tokens").fetchone()[0] == 0
    finally:
        conn.close()
    assert not list(folder.glob(".work-*"))
    # the next start goes back, signed out
    db = Database(path, upgrade_dir=folder)
    try:
        assert db.schema_version == 15 and db._conn.execute("SELECT revoked FROM tokens").fetchone()[0] == 1
    finally:
        db.close()


def test_a_start_that_keeps_failing_saves_one_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 15)

    def broken(conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("step failed")

    _newer(monkeypatch, broken)
    for _ in range(4):
        with pytest.raises(sqlite3.OperationalError):
            Database(path, upgrade_dir=folder)
    assert len(upgrade_backup.copies(folder)) == 1


def test_another_process_s_work_folder_is_left_alone(tmp_path: Path) -> None:
    folder = tmp_path / "before-upgrade"
    folder.mkdir()
    young, old = folder / ".work-young", folder / ".work-old"
    young.mkdir()
    old.mkdir()
    os.utime(old, (1, 1))
    path = tmp_path / "g.sqlite"
    _old_database(path, 14)
    Database(path, upgrade_dir=folder).close()
    assert young.exists() and not old.exists()
