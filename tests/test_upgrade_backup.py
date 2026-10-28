"""The copy saved before a database upgrade, the upgrade happening whole or not at all, and going
back to an older version (upgrade_backup.py)."""

from __future__ import annotations

import json
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
    assert _version(path) == 17
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
    _old_database(path, 17)
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        newer = Database(path, upgrade_dir=folder)
        assert newer.schema_version == 18
        newer._conn.execute(
            "INSERT INTO users (sub, groups_json, first_login_at, last_login_at) VALUES ('u2', '[]', 3, 3)"
        )
        newer.close()
    # back on this version: the copy saved before the upgrade is put back
    db = Database(path, upgrade_dir=folder)
    try:
        assert db.schema_version == 17
        assert [r[0] for r in db._conn.execute("SELECT sub FROM users")] == ["u1"]
        assert db._conn.execute("SELECT revoked FROM tokens").fetchone()[0] == 1  # everyone signs in again
        assert db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        db.close()
    ((aside, schema),) = upgrade_backup.copies(folder, upgrade_backup.SET_ASIDE_PREFIX)
    assert schema == 18 and stat.S_IMODE(aside.stat().st_mode) == 0o600
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
    _old_database(path, 17)
    folder.mkdir()
    # a copy of another schema is no use, nor is a damaged one
    (folder / "mtg-gateway-before-upgrade-20260101T000000Z-schema14.sqlite").write_bytes(b"")
    (folder / "mtg-gateway-before-upgrade-20260102T000000Z-schema17.sqlite").write_bytes(b"junk" * 1000)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 18")
    conn.close()
    with pytest.raises(DatabaseUpgradeError, match="newer than this gateway supports .*has no copy"):
        Database(path, upgrade_dir=folder)
    assert _version(path) == 18
    assert not upgrade_backup.copies(folder, upgrade_backup.SET_ASIDE_PREFIX)


# -- the stack files need no edit: the Authentik secret is always attached ------------------------
ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["deploy/portainer-stack.yml", "deploy/compose/docker-compose.yml"])
def test_the_stack_files_attach_the_authentik_secret_with_no_line_to_uncomment(name: str) -> None:
    text = (ROOT / name).read_text()
    assert "# mtg_authentik_api_token" not in text and "# - mtg_authentik_api_token" not in text
    assert "\n  mtg_authentik_api_token:\n" in text and "\n      - mtg_authentik_api_token\n" in text
    assert "      MTG_AUTHENTIK_API_TOKEN_FILE: /run/secrets/mtg_authentik_api_token\n" in text


def test_make_secrets_writes_off_and_keeps_a_token_already_there(tmp_path: Path) -> None:
    import shutil
    import subprocess

    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    script = ROOT / "deploy" / "compose" / "make-secrets.sh"
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "mtg_oidc_client_secret.txt").write_text("x")  # so the script asks nothing
    env = {"PATH": os.environ["PATH"]}
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
    forward = _went_back_notice({"folder": "f", "event": {"kind": "went-forward"}})
    assert "notice warn" in forward and "ended every sign-in" in forward and "Users page" in forward


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
    _old_database(path, 17)
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
        assert db.schema_version == 17
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
    _old_database(path, 17)
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        Database(path, upgrade_dir=folder).close()

    def crash(*a: object) -> None:
        raise sqlite3.OperationalError("killed")

    with monkeypatch.context() as m:
        m.setattr(upgrade_backup, "_carry_forward", crash)
        with pytest.raises(DatabaseUpgradeError, match="Nothing was changed"):
            Database(path, upgrade_dir=folder)
    assert _version(path) == 18
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT revoked FROM tokens").fetchone()[0] == 0
    finally:
        conn.close()
    assert not list(folder.glob(".work-*"))
    # the next start goes back, signed out
    db = Database(path, upgrade_dir=folder)
    try:
        assert db.schema_version == 17 and db._conn.execute("SELECT revoked FROM tokens").fetchone()[0] == 1
    finally:
        db.close()


def test_a_start_that_keeps_failing_saves_one_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 17)

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


# -- what the copies keep, and for how long ---------------------------------------------------


def _members(path: Path, *subs: str) -> None:
    """More members in the database at ``path``, each with an Archidekt link, identity-provider
    tokens and some data of their own."""
    conn = sqlite3.connect(path)
    for sub in subs:
        conn.execute(
            "INSERT INTO users (sub, groups_json, first_login_at, last_login_at) VALUES (?, '[]', 1, 1)",
            (sub,),
        )
        conn.execute(
            "INSERT INTO archidekt_links (sub, archidekt_username, archidekt_user_id, secret_enc, created_at,"
            " refreshed_at) VALUES (?, ?, '7', 'sealed', 1, 1)",
            (sub, f"name-{sub}"),
        )
        conn.execute(
            "INSERT INTO idp_grants (sub, refresh_enc, access_enc, access_expires_at, updated_at)"
            " VALUES (?, 'r', 'a', 1, 1)",
            (sub,),
        )
        conn.execute(
            "INSERT INTO proposals (id, owner_sub, deck_id, baseline_fingerprint, changes_json, diff_text,"
            " created_at, expires_at) VALUES (?, ?, 'd1', 'fp', '[]', 'diff', 1, 4102444800)",
            (f"p-{sub}", sub),
        )
    conn.commit()
    conn.close()


def _read(path: Path, sql: str, *args) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_an_unlink_a_disable_and_delete_my_data_reach_every_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Members are told an unlink, a disable and "Delete my data" end their Archidekt session and
    data at once: the copies saved at upgrades (before-upgrade and set-aside) are no exception."""
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 17)
    _members(path, "u2", "u3", "u4", "u5")
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        newer = Database(path, upgrade_dir=folder)
        try:
            ((before, _),) = upgrade_backup.copies(folder)
            aside = folder / "mtg-gateway-set-aside-20260101T000000Z-schema17.sqlite"
            aside.write_bytes(before.read_bytes())
            aside.chmod(0o600)
            assert newer.revoke_link("u2")  # Unlink (the member's or an admin's)
            assert newer.set_user_disabled("u3", True)  # an admin's Disable
            newer.delete_member_data("u4")  # Delete my data
            newer.drop_member("u5", [])  # removed from the gateway's group
        finally:
            newer.close()
    for copy in (before, aside):
        links = dict(_read(copy, "SELECT sub, secret_enc FROM archidekt_links"))
        assert links == {"u2": "", "u3": "", "u5": ""}, copy.name
        assert sorted(r[0] for r in _read(copy, "SELECT sub FROM idp_grants")) == ["u2"]
        assert ("u4",) not in _read(copy, "SELECT sub FROM users")
        assert _read(copy, "SELECT count(*) FROM proposals WHERE owner_sub = 'u4'") == [(0,)]
        assert stat.S_IMODE(copy.stat().st_mode) == 0o600
        assert _read(copy, "PRAGMA user_version") == [(17,)]
    # a refresh that fails (the session expired) is not an unlink, and changes no copy
    db = Database(tmp_path / "other.sqlite", upgrade_dir=folder)
    try:
        calls: list = []
        monkeypatch.setattr(upgrade_backup, "scrub_member", lambda *a: calls.append(a) or 0)
        db.revoke_link("u1", only_secret="sealed")
        assert calls == []
    finally:
        db.close()


def test_a_copy_that_can_t_be_changed_is_deleted_rather_than_kept(tmp_path: Path) -> None:
    folder = tmp_path / "before-upgrade"
    folder.mkdir()
    junk = folder / "mtg-gateway-before-upgrade-20260101T000000Z-schema17.sqlite"
    junk.write_bytes(b"not a database" * 100)
    db = Database(tmp_path / "g.sqlite", upgrade_dir=folder)
    try:
        db.delete_member_data("u1")
    finally:
        db.close()
    assert not junk.exists()


def test_the_copies_are_kept_no_longer_than_the_nightly_backups(tmp_path: Path) -> None:
    folder = tmp_path / "before-upgrade"
    folder.mkdir()
    names = [
        "mtg-gateway-before-upgrade-20260101T000000Z-schema14.sqlite",
        "mtg-gateway-before-upgrade-20260110T000000Z-schema14.sqlite",
        "mtg-gateway-set-aside-20260102T000000Z-schema18.sqlite",
        "mtg-gateway-set-aside-20260120T000000Z-schema18.sqlite",
    ]
    for name in names:
        (folder / name).write_bytes(b"")
    now = 1768867200  # 2026-01-20 00:00 UTC
    # the newest copy goes too once it is older than the nightly backups kept
    assert upgrade_backup.age_out(folder, 14, now=now) == 2
    assert sorted(f.name for f in folder.iterdir()) == [names[1], names[3]]
    assert upgrade_backup.age_out(folder, 5, now=now) == 1
    assert [f.name for f in folder.iterdir()] == [names[3]]
    # the hourly clean-up does it with the days the app wires in (MTG_BACKUP_KEEP_DAYS)
    db = Database(tmp_path / "g.sqlite", upgrade_dir=folder)
    try:
        db.purge_expired()
        assert (folder / names[3]).exists()  # nothing wired in: kept
        db.upgrade_keep_days = 14
        db.purge_expired()
        assert not (folder / names[3]).exists()
    finally:
        db.close()


def test_the_app_wires_in_the_backup_days_and_ages_copies_out_at_start(tmp_path: Path) -> None:
    from mtg_gateway.app import create_app

    from .conftest import make_settings

    settings = make_settings(tmp_path)
    folder = upgrade_backup.folder_for(settings.backup_dir, settings.db_path)
    folder.mkdir(parents=True)
    old = folder / "mtg-gateway-before-upgrade-20200101T000000Z-schema14.sqlite"
    old.write_bytes(b"")
    app = create_app(settings)
    try:
        assert app.state.gateway.db.upgrade_keep_days == settings.backup_keep_days
        assert not old.exists()
    finally:
        app.state.gateway.db.close()


def test_a_before_upgrade_folder_made_with_looser_permissions_is_made_owner_only(tmp_path: Path) -> None:
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)
    _old_database(path, 14)
    Database(path, upgrade_dir=folder).close()
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_going_back_keeps_the_member_s_current_archidekt_link_and_phone_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the upgrade the member unlinked Archidekt account A, linked B and pressed Disconnect
    my phones: going back must not bring back A's session or the old phone key."""
    from mtg_gateway import notify

    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 17)
    _members(path, "u2", "u3")
    d = Database(path, upgrade_dir=folder)
    try:
        d._conn.execute(
            "INSERT INTO archidekt_links (sub, archidekt_username, archidekt_user_id, secret_enc,"
            " created_at, refreshed_at) VALUES ('u1', 'accountA', '11', 'sealed-A', 1, 1)"
        )
        old_topic = notify.new_topic(d, "u1")
        notify.save_prefs(d, "u1", ntfy_topic=old_topic, ntfy_on=True)
        other_topic = notify.new_topic(d, "u2")
        notify.save_prefs(d, "u2", ntfy_topic=other_topic, ntfy_on=True)
    finally:
        d.close()
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        newer = Database(path, upgrade_dir=folder)
        try:
            newer.revoke_link("u1")
            assert newer.save_link("u1", username="accountB", user_id="22", secret_enc="sealed-B")
            new_topic = notify.new_topic(newer, "u1")
            notify.save_prefs(newer, "u1", ntfy_topic=new_topic)
            newer.revoke_link("u3")  # u3 unlinked and did not link again
        finally:
            newer.close()
    db = Database(path, upgrade_dir=folder)
    try:
        assert db.schema_version == 17
        link = db.get_link("u1")
        assert link and (link["archidekt_username"], link["archidekt_user_id"], link["secret_enc"]) == (
            "accountB",
            "22",
            "sealed-B",
        )
        assert db.get_link("u2")["secret_enc"] == "sealed"  # nothing changed: kept, nobody relinks
        assert db.get_link("u3") is None
        topics = dict(db._conn.execute("SELECT sub, ntfy_topic FROM notify_prefs").fetchall())
        assert topics == {"u1": new_topic, "u2": other_topic}
        assert old_topic not in topics.values()
    finally:
        db.close()


def test_going_forward_again_with_the_set_aside_copy_ends_every_sign_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The set-aside copy put back by hand (docs/UPGRADING.md) holds the sign-ins from before
    going back: the newer version ends them all at its first start on it, once."""
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 17)
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        newer = Database(path, upgrade_dir=folder)
        newer.create_browser_session("sid-1", "u1", 3600)
        newer.close()
    Database(path, upgrade_dir=folder).close()  # this version goes back
    ((aside, _),) = upgrade_backup.copies(folder, upgrade_backup.SET_ASIDE_PREFIX)
    assert _read(aside, "SELECT count(*) FROM audit_log WHERE event = ?", upgrade_backup.SET_ASIDE_EVENT) == [
        (1,)
    ]
    # put back by hand, as the docs say, and the newer version started on it
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    path.write_bytes(aside.read_bytes())
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        newer = Database(path, upgrade_dir=folder)
        try:
            assert newer.schema_version == 18
            assert newer._conn.execute("SELECT revoked FROM tokens").fetchone()[0] == 1
            assert newer.get_browser_session("sid-1") is None
            assert upgrade_backup.last_event["kind"] == "went-forward"
            events = [r[0] for r in newer._conn.execute("SELECT event FROM audit_log")]
            assert (
                upgrade_backup.SET_ASIDE_EVENT not in events and upgrade_backup.WENT_FORWARD_EVENT in events
            )
            newer.create_browser_session("sid-2", "u1", 3600)
        finally:
            newer.close()
        # once: a later start keeps the sign-ins made since
        again = Database(path, upgrade_dir=folder)
        try:
            assert again.get_browser_session("sid-2") == "u1"
        finally:
            again.close()


# -- found in review of the fixes ----------------------------------------------------------------


def test_a_repeated_copy_keeps_the_newest_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A start that failed long ago left a copy; the start that finally upgrades saves the same
    bytes again. The new one is kept (and the old one goes), so the age-out right after counts
    from now and going back stays possible."""
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 17)

    def broken(conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("step failed")

    with monkeypatch.context() as m:
        _newer(m, broken)
        with pytest.raises(sqlite3.OperationalError):
            Database(path, upgrade_dir=folder)
    ((first, _),) = upgrade_backup.copies(folder)
    old = folder / "mtg-gateway-before-upgrade-20200101T000000Z-schema17.sqlite"
    first.rename(old)
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        db = Database(path, upgrade_dir=folder)
        try:
            db.upgrade_keep_days = 14
            db.age_out_upgrade_copies()
        finally:
            db.close()
    ((kept, schema),) = upgrade_backup.copies(folder)
    assert schema == 17 and kept != old and not old.exists()


def test_the_age_out_removes_what_a_crash_left(tmp_path: Path) -> None:
    folder = tmp_path / "before-upgrade"
    folder.mkdir()
    now = upgrade_backup._stamp()
    hot = folder / f"mtg-gateway-before-upgrade-{now}-schema17.sqlite"
    hot.write_bytes(b"")
    Path(f"{hot}-journal").write_bytes(b"rows from before a change")  # a change cut short
    fresh = folder / f"mtg-gateway-set-aside-{now}-schema18.sqlite"
    fresh.write_bytes(b"")
    orphan = folder / "mtg-gateway-before-upgrade-20200101T000000Z-schema14.sqlite-journal"
    orphan.write_bytes(b"x")
    stale = folder / ".work-abc"
    stale.mkdir()
    (stale / "half.sqlite").write_bytes(b"x")
    os.utime(stale, (1, 1))
    young = folder / ".work-young"
    young.mkdir()
    upgrade_backup.age_out(folder, 14)
    assert sorted(f.name for f in folder.iterdir()) == [".work-young", fresh.name]


def test_a_set_aside_copy_put_back_and_upgraded_doesn_t_bring_its_mark_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 17)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO audit_log (at, sub, client_id, event, detail_json) VALUES (1, NULL, NULL, ?, '{}')",
        (upgrade_backup.SET_ASIDE_EVENT,),
    )
    conn.commit()
    conn.close()
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        Database(path, upgrade_dir=folder).close()  # the mark is dealt with before the copy is saved
    ((before, _),) = upgrade_backup.copies(folder)
    assert _read(
        before, "SELECT count(*) FROM audit_log WHERE event = ?", upgrade_backup.SET_ASIDE_EVENT
    ) == [(0,)]
    Database(path, upgrade_dir=folder).close()  # going back
    assert upgrade_backup.last_event["kind"] == "went-back"


def test_going_back_works_when_the_newer_audit_log_can_t_take_the_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def other_log(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE audit_log RENAME TO audit_old")
        conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, at INTEGER, must TEXT NOT NULL)")

    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 17)
    with monkeypatch.context() as m:
        _newer(m, other_log)
        Database(path, upgrade_dir=folder).close()
    db = Database(path, upgrade_dir=folder)
    try:
        assert db.schema_version == 17
    finally:
        db.close()
    assert len(upgrade_backup.copies(folder, upgrade_backup.SET_ASIDE_PREFIX)) == 1


def test_delete_my_data_deletes_a_set_aside_copy_of_a_newer_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A newer version's tables may hold more of the member than this version knows to delete."""
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 17)
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        Database(path, upgrade_dir=folder).close()
    db = Database(path, upgrade_dir=folder)  # back on this version: a set-aside copy of schema 18
    try:
        ((aside, schema),) = upgrade_backup.copies(folder, upgrade_backup.SET_ASIDE_PREFIX)
        assert schema == 18
        db.revoke_link("u1")  # an unlink only blanks one column: the copy is changed, not deleted
        assert aside.exists()
        db.delete_member_data("u1")
        assert not aside.exists()
        assert upgrade_backup.copies(folder)  # the before-upgrade copy (schema 17) is changed
    finally:
        db.close()


def test_the_copies_are_brought_in_line_at_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What ended under 0.7.37 (no scrub then), or in a change cut short, is ended in the copies at
    the next start: deleted members forgotten, unlinked or replaced sessions blanked, disabled
    members' identity-provider tokens deleted. A relink now also blanks the replaced session."""
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 17)
    _members(path, "u2", "u3", "u4", "u5")
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        newer = Database(path, upgrade_dir=folder)
        ((before, _),) = upgrade_backup.copies(folder)
        c = newer._conn  # as 0.7.37 did it: the live database only
        dbmod.delete_member_rows(c, "u2")
        c.execute("UPDATE archidekt_links SET secret_enc = '' WHERE sub = 'u3'")
        c.execute("UPDATE archidekt_links SET secret_enc = 'sealed-new' WHERE sub = 'u4'")
        c.execute("UPDATE users SET disabled_at = 1 WHERE sub = 'u5'")
        assert newer.reconcile_upgrade_copies() == 1
        assert newer.reconcile_upgrade_copies() == 0  # nothing more to do
        assert ("u2",) not in _read(before, "SELECT sub FROM users")
        links = dict(_read(before, "SELECT sub, secret_enc FROM archidekt_links"))
        assert links == {"u3": "", "u4": "", "u5": "sealed"}
        assert sorted(r[0] for r in _read(before, "SELECT sub FROM idp_grants")) == ["u3", "u4"]
        assert newer.save_link("u5", username="n", user_id="8", secret_enc="sealed-5b")
        assert dict(_read(before, "SELECT sub, secret_enc FROM archidekt_links"))["u5"] == ""
        newer.close()


def test_a_folder_the_gateway_can_t_tighten_doesn_t_stop_the_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    folder.mkdir()
    folder.chmod(0o755)
    _old_database(path, 14)

    def refuse(self: Path, mode: int) -> None:
        raise PermissionError("not the owner")

    monkeypatch.setattr(Path, "chmod", refuse)
    db = Database(path, upgrade_dir=folder)
    try:
        assert db.schema_version == dbmod.SCHEMA_VERSION
    finally:
        db.close()
    assert len(upgrade_backup.copies(folder)) == 1


def _phone(path: Path, sub: str, topic: str | None) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT OR REPLACE INTO notify_prefs (sub, ntfy_on, ntfy_topic, updated_at) VALUES (?, 1, ?, 1)",
        (sub, topic),
    )
    conn.commit()
    conn.close()


async def test_disconnect_my_phones_reaches_every_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Members are told Disconnect my phones ends the old key: a copy saved at an upgrade can't
    bring it back."""
    from mtg_gateway.notify import account_action

    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 17)
    _members(path, "u2")
    _phone(path, "u1", "old-key-1")
    _phone(path, "u2", "key-2")
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        newer = Database(path, upgrade_dir=folder)
        try:
            ((before, _),) = upgrade_backup.copies(folder)
            aside = folder / "mtg-gateway-set-aside-20260101T000000Z-schema17.sqlite"
            aside.write_bytes(before.read_bytes())
            aside.chmod(0o600)
            state = SimpleNamespace(db=newer, notifier=None)
            assert await account_action(state, "u1", "notify_new_topic", {})
            live = dict(newer._conn.execute("SELECT sub, ntfy_topic FROM notify_prefs").fetchall())
            assert live["u1"] not in (None, "old-key-1")
        finally:
            newer.close()
    for copy in (before, aside):
        assert dict(_read(copy, "SELECT sub, ntfy_topic FROM notify_prefs")) == {"u1": None, "u2": "key-2"}


def test_at_start_a_phone_key_the_database_no_longer_holds_is_blanked_in_the_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disconnected under 0.7.37, or in a change cut short: the next start blanks it."""
    path, folder = tmp_path / "g.sqlite", tmp_path / "before-upgrade"
    _old_database(path, 17)
    _members(path, "u2")
    _phone(path, "u1", "old-key-1")
    _phone(path, "u2", "key-2")
    with monkeypatch.context() as m:
        _newer(m, _add_table)
        newer = Database(path, upgrade_dir=folder)
        try:
            ((before, _),) = upgrade_backup.copies(folder)
            newer._conn.execute("UPDATE notify_prefs SET ntfy_topic = 'new-key-1' WHERE sub = 'u1'")
            assert newer.reconcile_upgrade_copies() == 1
            assert newer.reconcile_upgrade_copies() == 0
        finally:
            newer.close()
    assert dict(_read(before, "SELECT sub, ntfy_topic FROM notify_prefs")) == {"u1": None, "u2": "key-2"}


UPGRADED = "mtg-gateway-before-upgrade-20260301T000000Z-schema17.sqlite"  # 2026-03-01: the upgrade
AFTER_UPGRADE = 1775000000  # 2026-04-01, a sign-in after it


def _set_aside(tmp_path: Path, name: str, *, record: bool = True) -> tuple[Database, Path]:
    """A live database (schema 17, member u1 only) that went back from a newer one, and the
    set-aside copy named ``name``: u1, u2 (there at the upgrade, deleted since) and u9 (joined
    after the upgrade), each with an Archidekt session and identity-provider tokens."""
    folder = tmp_path / "before-upgrade"
    folder.mkdir()
    src = tmp_path / "aside.sqlite"
    _old_database(src, 17)
    _members(src, "u2", "u9")
    conn = sqlite3.connect(src)
    conn.execute("UPDATE users SET first_login_at = ? WHERE sub = 'u9'", (AFTER_UPGRADE,))
    conn.execute(
        "INSERT INTO idp_grants (sub, refresh_enc, access_enc, access_expires_at, updated_at)"
        " VALUES ('u1', 'r', 'a', 1, 1)"
    )
    conn.commit()
    conn.close()
    aside = folder / name
    aside.write_bytes(src.read_bytes())
    aside.chmod(0o600)
    live_path = tmp_path / "g.sqlite"
    _old_database(live_path, 17)
    db = Database(live_path, upgrade_dir=folder)
    if record:
        db._conn.execute(
            "INSERT INTO audit_log (at, sub, client_id, event, detail_json) VALUES (1, NULL, NULL, ?, ?)",
            ("database_went_back", json.dumps({"copy": UPGRADED, "set_aside": name, "from_schema": 18})),
        )
    return db, aside


def test_at_start_a_member_deleted_since_is_forgotten_in_the_set_aside_copy(tmp_path: Path) -> None:
    """A set-aside copy also holds those who joined after the upgrade: they stay (going forward
    with it, they sign in and relink), but a member who was there at the upgrade and is gone now
    is forgotten, and no member who is gone or new keeps identity-provider tokens."""
    name = "mtg-gateway-set-aside-20260501T000000Z-schema17.sqlite"
    db, aside = _set_aside(tmp_path, name)
    try:
        assert db.reconcile_upgrade_copies() == 1
        assert db.reconcile_upgrade_copies() == 0
    finally:
        db.close()
    assert sorted(r[0] for r in _read(aside, "SELECT sub FROM users")) == ["u1", "u9"]
    assert _read(aside, "SELECT count(*) FROM proposals WHERE owner_sub = 'u2'") == [(0,)]
    assert _read(aside, "SELECT sub FROM idp_grants") == [("u1",)]
    assert dict(_read(aside, "SELECT sub, secret_enc FROM archidekt_links")) == {"u9": ""}


@pytest.mark.parametrize("record", [True, False])
def test_a_set_aside_copy_that_can_t_be_brought_in_line_is_deleted(tmp_path: Path, record: bool) -> None:
    """Holding a member who is gone, in a newer schema this version doesn't know every table of;
    or with no record left of when the upgrade was, so the members it holds who aren't here can't
    be told apart from those who are gone."""
    schema = 18 if record else 17
    name = f"mtg-gateway-set-aside-20260501T000000Z-schema{schema}.sqlite"
    db, aside = _set_aside(tmp_path, name, record=record)
    try:
        db.reconcile_upgrade_copies()
    finally:
        db.close()
    assert not aside.exists()
