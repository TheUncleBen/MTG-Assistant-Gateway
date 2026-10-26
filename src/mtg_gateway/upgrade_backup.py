"""The copy of the database the gateway saves before it upgrades it, and going back to it.

When a new version starts on a database an older version wrote, it first saves a full copy of that
database in the before-upgrade folder (``before-upgrade`` inside ``MTG_BACKUP_DIR``, or inside the
data folder when backups are off), checks the copy, and only then upgrades. No copy, no upgrade:
the gateway stops with a message saying why, and the database is left as it was.

The copy is the database exactly as it was, members' Archidekt links and identity-provider tokens
included (still encrypted with the Fernet key, which is never in the file), so going back needs
nobody to relink. That is why it stays in its own owner-only folder and is never copied to
``MTG_BACKUP_COPY_DIR``. The newest ``KEEP`` copies are kept, and none longer than the nightly
backups (backup_keep_days, :func:`age_out`). What a member ends on the live database is
ended in every copy too (:func:`scrub_member`): an unlink, a disable, "Delete my data".

Going back: when an older version (this one or newer) starts on a database a newer version
upgraded, it looks in the same folder for its own before-upgrade copy. With one, it first saves
the newer database there as a set-aside copy (nothing is lost), puts its copy back, ends every
sign-in so everyone signs in once more, and starts. Without one, it stops and says what to do.
The set-aside copy is marked (``SET_ASIDE_EVENT``): put back by hand to go forward again, the
newer version ends every sign-in at its first start on it (:func:`went_forward`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

FOLDER = "before-upgrade"
BEFORE_PREFIX = "mtg-gateway-before-upgrade-"
SET_ASIDE_PREFIX = "mtg-gateway-set-aside-"
SUFFIX = ".sqlite"
WORK_PREFIX = ".work-"
# A work folder older than this was left by a start that was killed mid-copy; a younger one may
# belong to another process copying right now.
STALE_WORK_SECONDS = 3600
# Copies kept of each kind (before-upgrade, set-aside); older ones are deleted after a new one.
KEEP = 3
# Free space wanted beyond the database's own size, for SQLite's journal while the copy is checked.
SPACE_MARGIN_BYTES = 16 * 1024 * 1024
# The audit row a set-aside copy carries (going forward again with it ends every sign-in), and
# the one that start writes instead.
SET_ASIDE_EVENT = "database_set_aside"
WENT_FORWARD_EVENT = "database_went_forward"
_NAME = re.compile(r"^mtg-gateway-(?:before-upgrade|set-aside)-(\d{8}T\d{6}Z)-schema(\d+)\.sqlite$")

# What this process did at start, for the admin page: {"kind": "upgraded" | "went-back", ...}.
last_event: dict[str, object] = {}
# One clean-up of the copies at a time (a member's delete, the hourly age-out).
_COPIES_LOCK = threading.Lock()


class DatabaseUpgradeError(RuntimeError):
    """The gateway can't open its database safely and must not start; the message says why and
    what to do."""


def folder_for(backup_dir: Path | None, db_path: Path) -> Path:
    """Where before-upgrade copies live: inside the backup folder, else next to the database."""
    return (backup_dir if backup_dir is not None else db_path.parent) / FOLDER


def tighten(folder: Path) -> None:
    """Make the folder owner-only: one made by hand, or by an older version, may be looser. One
    the gateway can't change (another user's) is logged and left as it is."""
    try:
        if folder.is_dir() and not folder.is_symlink() and folder.stat().st_mode & 0o077:
            folder.chmod(0o700)
    except OSError as exc:
        logger.warning("could not make %s owner-only: %s", folder, type(exc).__name__)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def copies(folder: Path, prefix: str = BEFORE_PREFIX) -> list[tuple[Path, int]]:
    """The copies of one kind in ``folder``, newest first, with the schema version in each name."""
    found: list[tuple[str, Path, int]] = []
    try:
        entries = list(folder.glob(f"{prefix}*{SUFFIX}"))
    except OSError:
        return []
    for f in entries:
        m = _NAME.match(f.name)
        if m and f.is_file() and not f.is_symlink():
            found.append((m.group(1), f, int(m.group(2))))
    found.sort(key=lambda t: t[0], reverse=True)
    return [(f, schema) for _, f, schema in found]


def _schema_of(path: Path) -> int | None:
    """The schema version inside a database file, or None when it can't be read or fails its
    quick check."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            return None
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _mb(n: float) -> str:
    return f"{max(1, round(n / (1024 * 1024)))} MB"


def _db_bytes(db_path: Path) -> int:
    total = 0
    for suffix in ("", "-wal"):
        try:
            total += Path(f"{db_path}{suffix}").stat().st_size
        except OSError:
            pass
    return total


def _write_copy(
    source: sqlite3.Connection,
    folder: Path,
    name: str,
    expect_schema: int,
    db_path: Path,
    prepare: Callable[[sqlite3.Connection], object] | None = None,
) -> Path:
    """Copy the database behind ``source`` into ``folder`` as ``name`` with SQLite's online backup
    API, run ``prepare`` on the copy, check it, and only then give it its name. Raises OSError or
    sqlite3.Error on failure."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    tighten(folder)
    need = _db_bytes(db_path) + SPACE_MARGIN_BYTES
    free = shutil.disk_usage(folder).free
    if free < need:
        raise OSError(f"not enough free space in {folder}: it needs about {_mb(need)} and has {_mb(free)}")
    cutoff = time.time() - STALE_WORK_SECONDS
    for stale in folder.glob(f"{WORK_PREFIX}*"):
        try:
            if stale.is_dir() and not stale.is_symlink() and stale.lstat().st_mtime < cutoff:
                shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            continue
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=folder))  # mode 0700
    try:
        tmp = work / name
        # owner-only from the start: SQLite keeps an existing file's mode
        os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600))
        target = sqlite3.connect(str(tmp))
        try:
            source.backup(target)
            # a standalone file with no WAL beside it
            target.execute("PRAGMA journal_mode=DELETE")
            if prepare is not None:
                prepare(target)
                target.commit()
        finally:
            target.close()
        got = _schema_of(tmp)
        if got != expect_schema:
            raise sqlite3.DatabaseError(
                "the copy failed its check"
                if got is None
                else f"the copy holds schema {got}, not {expect_schema}"
            )
        dest = folder / name
        tmp.replace(dest)
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _prune(folder: Path, prefix: str) -> int:
    removed = 0
    for f, _ in copies(folder, prefix)[KEEP:]:
        try:
            f.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("could not delete the old copy %s: %s", f.name, type(exc).__name__)
    return removed


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _drop_if_same_as_older(new: Path, schema: int, folder: Path) -> Path:
    """``new``, after deleting the newest older copy of ``schema`` when it holds exactly the same
    bytes (SQLite's backup copies page by page, so an unchanged database gives the same file). The
    new one is kept: its date is the one the age-out goes by."""
    older = next((f for f, s in copies(folder) if s == schema and f != new), None)
    if older is None:
        return new
    try:
        if _sha256(older) == _sha256(new):
            older.unlink()
    except OSError:
        pass
    return new


def has_data(conn: sqlite3.Connection) -> bool:
    """Whether the database already holds tables (a new, empty file needs no copy)."""
    return conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone() is not None


def save_before_upgrade(
    conn: sqlite3.Connection, db_path: Path, folder: Path, schema: int, target: int
) -> Path:
    """Save the copy taken before upgrading from ``schema`` to ``target``. Raises
    DatabaseUpgradeError, with nothing changed, when it can't be made."""
    name = f"{BEFORE_PREFIX}{_stamp()}-schema{schema}{SUFFIX}"
    try:
        dest = _write_copy(conn, folder, name, schema, db_path)
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseUpgradeError(
            f"the database was not upgraded (schema {schema} to {target}) because the gateway could not "
            f"save a copy of it first in {folder}: {exc}. Nothing was changed. Fix that and start the "
            "gateway again, or go back to the version you ran before."
        ) from exc
    # A start that keeps failing saves the same database again each time: keep only the first, so
    # the copies an older version may need aren't pushed out.
    dest = _drop_if_same_as_older(dest, schema, folder)
    removed = _prune(folder, BEFORE_PREFIX)
    logger.warning(
        "database upgrade from schema %d to %d: saved a copy of the database first, %s "
        "(%d older copies removed)",
        schema,
        target,
        dest,
        removed,
    )
    last_event.clear()
    last_event.update(kind="upgraded", at=int(time.time()), file=dest.name, schema=schema, target=target)
    return dest


# Every sign-in ends when an older copy is put back: a sign-in or token revoked after the copy was
# taken would otherwise work again.
_END_SIGN_INS = (
    "UPDATE tokens SET revoked = 1",
    "DELETE FROM browser_sessions",
    "DELETE FROM auth_codes",
    "DELETE FROM login_sessions",
)


def _carry_forward(newer: sqlite3.Connection, old: sqlite3.Connection, since: int, forget: Callable) -> None:
    """Bring into ``old`` (the copy about to be put back) what must not come undone: members
    disabled, Archidekt sessions deleted (an unlink, the removed-member clean-up) and members'
    "Delete my data" since the copy was taken (``since``), read from ``newer``. A read that the
    newer schema no longer answers is logged and skipped."""
    try:
        for sub, at in newer.execute("SELECT sub, disabled_at FROM users WHERE disabled_at IS NOT NULL"):
            old.execute("UPDATE users SET disabled_at = ? WHERE sub = ? AND disabled_at IS NULL", (at, sub))
    except sqlite3.Error as exc:
        logger.warning("going back: could not read who was disabled since the upgrade: %s", exc)
    _carry_links(newer, old)
    try:
        # the phone keys: one replaced since (Disconnect my phones) must not work again
        topics = {r[0]: r[1] for r in newer.execute("SELECT sub, ntfy_topic FROM notify_prefs")}
        subs = [r[0] for r in old.execute("SELECT sub FROM notify_prefs").fetchall()]
        old.execute("UPDATE notify_prefs SET ntfy_topic = NULL")
        for sub in subs:
            if topics.get(sub):
                old.execute("UPDATE notify_prefs SET ntfy_topic = ? WHERE sub = ?", (topics[sub], sub))
    except sqlite3.Error as exc:
        logger.warning(
            "going back: could not read the members' phone keys, so every phone connects again: %s", exc
        )
        try:
            old.execute("UPDATE notify_prefs SET ntfy_topic = NULL")
        except sqlite3.Error:
            pass
    try:
        deleted = [
            r[0]
            for r in newer.execute(
                "SELECT DISTINCT sub FROM audit_log WHERE event = 'member_data_deleted' AND at >= ?", (since,)
            )
            if r[0]
        ]
    except sqlite3.Error as exc:
        logger.warning("going back: could not read whose data was deleted since the upgrade: %s", exc)
        deleted = []
    for sub in deleted:
        forget(old, sub)


_LINK_COLUMNS = (
    "archidekt_username",
    "archidekt_user_id",
    "secret_enc",
    "status",
    "created_at",
    "refreshed_at",
)


def _carry_links(newer: sqlite3.Connection, old: sqlite3.Connection) -> None:
    """Each member's Archidekt link in ``old`` becomes the one the newer database holds now: a
    session deleted since (an unlink) stays deleted, and one replaced since (a relink, perhaps to
    another Archidekt account) is not brought back; the member's current link is kept, so nobody
    relinks. When the newer rows can't be read or written, every link in ``old`` is blanked."""
    cols = ", ".join(_LINK_COLUMNS)
    try:
        now = {
            r[0]: r[1:]
            for r in newer.execute(f"SELECT sub, {cols} FROM archidekt_links WHERE secret_enc != ''")
        }
        for (sub,) in old.execute("SELECT sub FROM archidekt_links WHERE secret_enc != ''").fetchall():
            if sub not in now:
                old.execute(
                    "UPDATE archidekt_links SET secret_enc = '', status = 'revoked' WHERE sub = ?", (sub,)
                )
        members = {r[0] for r in old.execute("SELECT sub FROM users")}
        sets = ", ".join(f"{c} = excluded.{c}" for c in _LINK_COLUMNS)
        for sub, values in now.items():
            if sub in members:  # someone who joined after the upgrade isn't in the older copy
                old.execute(
                    f"INSERT INTO archidekt_links (sub, {cols}) VALUES (?, {', '.join('?' * len(values))})"
                    f" ON CONFLICT(sub) DO UPDATE SET {sets}",
                    (sub, *values),
                )
    except sqlite3.Error as exc:
        logger.warning(
            "going back: could not carry the members' Archidekt links over, so everyone relinks: %s", exc
        )
        old.execute("UPDATE archidekt_links SET secret_enc = '', status = 'revoked' WHERE secret_enc != ''")


def _stamp_of(path: Path) -> int:
    m = _NAME.match(path.name)
    if not m:
        return 0
    return int(datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).timestamp())


def go_back(
    conn: sqlite3.Connection,
    db_path: Path,
    folder: Path,
    found: int,
    supported: int,
    forget: Callable[[sqlite3.Connection, str], object],
) -> bool:
    """Put back this version's before-upgrade copy into the open database ``conn`` (schema
    ``found``, newer than ``supported``), after saving the newer database as a set-aside copy.
    The copy is made ready in a work file first (what must not come undone carried forward, every
    sign-in ended) and goes in with one backup, so a crash leaves either database whole.
    ``forget(conn, sub)`` deletes one member's data. Returns False when there is no copy to put
    back; raises DatabaseUpgradeError when going back fails, with the database left as it was."""
    candidate = next(
        (f for f, schema in copies(folder) if schema == supported and _schema_of(f) == supported), None
    )
    if candidate is None:
        return False
    aside_name = f"{SET_ASIDE_PREFIX}{_stamp()}-schema{found}{SUFFIX}"

    def mark(copy: sqlite3.Connection) -> None:
        # the newer schema's audit_log may differ: unmarked, going forward needs the docs' command
        try:
            copy.execute(
                "INSERT INTO audit_log (at, sub, client_id, event, detail_json) VALUES (?, NULL, NULL, ?, ?)",
                (int(time.time()), SET_ASIDE_EVENT, json.dumps({"back_to_schema": supported})),
            )
        except sqlite3.Error as exc:
            logger.warning("going back: the set-aside copy could not be marked: %s", exc)

    try:
        aside = _write_copy(conn, folder, aside_name, found, db_path, prepare=mark)
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseUpgradeError(
            f"the gateway did not go back to its before-upgrade copy {candidate.name} because it could "
            f"not first save the newer database in {folder}: {exc}. Nothing was changed."
        ) from exc
    _prune(folder, SET_ASIDE_PREFIX)
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=folder))  # mode 0700
    try:
        ready = work / candidate.name
        os.close(os.open(ready, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600))
        src = sqlite3.connect(f"file:{candidate}?mode=ro", uri=True)
        old = sqlite3.connect(str(ready), isolation_level=None)
        try:
            # as the gateway's own connection: deleted rows are overwritten, and deletes cascade
            old.execute("PRAGMA secure_delete=ON")
            old.execute("PRAGMA foreign_keys=ON")
            src.backup(old)
            old.execute("PRAGMA journal_mode=DELETE")
            old.execute("BEGIN IMMEDIATE")
            _carry_forward(conn, old, _stamp_of(candidate), forget)
            for sql in _END_SIGN_INS:
                old.execute(sql)
            # a mark the copy carried (a set-aside copy that was put back and upgraded since)
            old.execute("DELETE FROM audit_log WHERE event = ?", (SET_ASIDE_EVENT,))
            old.execute(
                "INSERT INTO audit_log (at, sub, client_id, event, detail_json) VALUES (?, NULL, NULL, ?, ?)",
                (
                    int(time.time()),
                    "database_went_back",
                    json.dumps({"copy": candidate.name, "set_aside": aside.name, "from_schema": found}),
                ),
            )
            old.execute("COMMIT")
            if old.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("the prepared copy failed its check")
            # one backup step: the database in use changes whole, or not at all
            old.backup(conn)
        finally:
            old.close()
            src.close()
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseUpgradeError(
            f"going back to the before-upgrade copy {candidate.name} failed ({exc}). Nothing was "
            f"changed, and the newer database is also saved as {aside}. docs/UPGRADING.md, “Going "
            "back to an older version”, says how to put a copy back by hand."
        ) from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    logger.warning(
        "going back: this version reads schema %d and the database was at %d. The newer database is "
        "saved as the set-aside copy %s, and the before-upgrade copy %s is back in use. Every sign-in "
        "has ended, so everyone signs in once more; members disabled, Archidekt sessions deleted "
        "and members' data deleted since the upgrade stay so. Other changes made since the upgrade "
        "are only in the set-aside copy.",
        supported,
        found,
        aside,
        candidate.name,
    )
    last_event.clear()
    last_event.update(
        kind="went-back",
        at=int(time.time()),
        file=candidate.name,
        aside=aside.name,
        schema=found,
        target=supported,
    )
    return True


def went_forward(conn: sqlite3.Connection) -> bool:
    """At start: when the database is a set-aside copy put back by hand (going forward again after
    going back), end every sign-in, as going back does: sign-ins revoked while the older version
    ran would otherwise work again. The mark is replaced by a record of this, so it happens once.
    True when it did."""
    try:
        marked = conn.execute(
            "SELECT 1 FROM audit_log WHERE event = ? LIMIT 1", (SET_ASIDE_EVENT,)
        ).fetchone()
    except sqlite3.Error:
        return False
    if marked is None:
        return False
    conn.execute("BEGIN IMMEDIATE")
    try:
        for sql in _END_SIGN_INS:
            conn.execute(sql)
        conn.execute("DELETE FROM audit_log WHERE event = ?", (SET_ASIDE_EVENT,))
        conn.execute(
            "INSERT INTO audit_log (at, sub, client_id, event, detail_json) VALUES (?, NULL, NULL, ?, '{}')",
            (int(time.time()), WENT_FORWARD_EVENT),
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    logger.warning(
        "going forward again: this database was set aside when an older version went back, and has "
        "been put back. Every sign-in has ended, so everyone signs in once more. Members disabled, "
        "unlinked or deleted while the older version ran are not; check the admin Users page."
    )
    last_event.clear()
    last_event.update(kind="went-forward", at=int(time.time()))
    return True


def age_out(folder: Path, keep_days: int, now: float | None = None) -> int:
    """Delete the before-upgrade and set-aside copies older than ``keep_days`` (the nightly backups'
    backup_keep_days), by the time in their names: they hold what members asked to be
    deleted later, so they are kept no longer than the nightly backups. Returns how many went."""
    cutoff = (time.time() if now is None else now) - keep_days * 86400
    removed = 0
    with _COPIES_LOCK:
        tighten(folder)
        for prefix in (BEFORE_PREFIX, SET_ASIDE_PREFIX):
            for f, _ in copies(folder, prefix):
                # a journal beside a copy is a change cut short: it holds the rows before it
                hot = Path(f"{f}-journal")
                if _stamp_of(f) >= cutoff and not hot.exists():
                    continue
                try:
                    f.unlink()
                    removed += 1
                except OSError as exc:
                    logger.warning("could not delete the old copy %s: %s", f.name, type(exc).__name__)
        _sweep_leftovers(folder)
    if removed:
        logger.info("deleted %d copies saved at database upgrades, older than %d days", removed, keep_days)
    return removed


def _sweep_leftovers(folder: Path) -> None:
    """Remove what a crash left in the folder: work folders older than STALE_WORK_SECONDS (a
    half-made copy inside) and journals whose copy is gone."""
    cutoff = time.time() - STALE_WORK_SECONDS
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    for e in entries:
        try:
            if e.name.startswith(WORK_PREFIX) and e.is_dir() and not e.is_symlink():
                if e.lstat().st_mtime < cutoff:
                    shutil.rmtree(e, ignore_errors=True)
            elif e.name.endswith(f"{SUFFIX}-journal") and not e.is_symlink():
                if not Path(str(e)[: -len("-journal")]).exists():
                    e.unlink()
        except OSError:
            continue


# What :func:`scrub_member` does to one member's rows in a copy, by what ended on the live database.
_SCRUB = {
    # an unlink (also an admin's): the Archidekt session
    "link": ("UPDATE archidekt_links SET secret_enc = '', status = 'revoked' WHERE sub = ?",),
    # a disable or a removal from the group: the Archidekt session and the identity-provider tokens
    "account": (
        "UPDATE archidekt_links SET secret_enc = '', status = 'revoked' WHERE sub = ?",
        "DELETE FROM idp_grants WHERE sub = ?",
    ),
    # "Disconnect my phones": the phone notification key
    "phone": ("UPDATE notify_prefs SET ntfy_topic = NULL WHERE sub = ?",),
}


def scrub_member(
    folder: Path,
    sub: str,
    what: str,
    forget: Callable[[sqlite3.Connection, str], object],
    supported: int | None = None,
) -> int:
    """End in every before-upgrade and set-aside copy what just ended for ``sub`` on the live
    database: ``what`` is "link", "account", "phone" (see ``_SCRUB``) or "data" (``forget(conn, sub)``
    deletes their rows, as "Delete my data" does). Deleted rows are overwritten (secure_delete).
    A copy that can't be changed is deleted, so it never keeps what the member was told is gone;
    so is, for "data", a copy of a schema newer than ``supported`` (a set-aside copy), whose
    tables this version doesn't all know. Returns how many copies were changed."""
    if what != "data" and what not in _SCRUB:
        raise ValueError(what)
    changed = 0
    with _COPIES_LOCK:
        for prefix in (BEFORE_PREFIX, SET_ASIDE_PREFIX):
            for f, schema in copies(folder, prefix):
                try:
                    if what == "data" and supported is not None and schema > supported:
                        raise sqlite3.DatabaseError(f"schema {schema} is newer than this version reads")
                    _scrub_one(f, sub, what, forget)
                    changed += 1
                except (OSError, sqlite3.Error) as exc:
                    logger.warning(
                        "could not remove a member's data from the copy %s (%s), so it is deleted",
                        f.name,
                        exc,
                    )
                    try:
                        f.unlink()
                    except OSError as err:
                        logger.error("could not delete the copy %s: %s", f.name, type(err).__name__)
    return changed


def reconcile(
    folder: Path,
    live: sqlite3.Connection,
    forget: Callable[[sqlite3.Connection, str], object],
    supported: int,
) -> int:
    """At start: bring every copy in line with what has ended on the live database ``live`` since
    it was made, also by a version from before this check, or by a change cut short: members who
    are gone (their data deleted) are forgotten; members who are gone or disabled keep no
    identity-provider tokens; an Archidekt session or a phone key the live database doesn't hold
    (unlinked, replaced or disconnected; going back carries the live ones over) is blanked. A
    set-aside copy keeps the rows of members who joined after the upgrade, though not their
    Archidekt session, phone key or sign-in tokens (going forward with it, they sign in, relink and
    reconnect); a member it holds who was already there at the upgrade (signed in before the
    before-upgrade copy that going back put in its place was saved, as the live database's
    "database_went_back" record names them) and is gone from the live database is gone. A
    set-aside copy holding a member who is gone, or who can't be told apart from one (no such
    record left), is deleted when its schema is newer than ``supported``, as this version doesn't
    know all its tables. A copy that can't be brought in line is deleted. Returns how many copies
    were changed."""
    try:
        members = {r[0] for r in live.execute("SELECT sub FROM users")}
        disabled = {r[0] for r in live.execute("SELECT sub FROM users WHERE disabled_at IS NOT NULL")}
        sessions = {
            (r[0], r[1])
            for r in live.execute("SELECT sub, secret_enc FROM archidekt_links WHERE secret_enc != ''")
        }
        phones = {
            (r[0], r[1])
            for r in live.execute("SELECT sub, ntfy_topic FROM notify_prefs WHERE ntfy_topic IS NOT NULL")
        }
        upgraded = _upgrade_times(live)
    except sqlite3.Error as exc:
        logger.warning("could not read the live database to check the copies: %s", exc)
        return 0
    changed = 0
    with _COPIES_LOCK:
        for prefix in (BEFORE_PREFIX, SET_ASIDE_PREFIX):
            for f, schema in copies(folder, prefix):
                try:
                    known = LiveState(members, disabled, sessions, phones)
                    before = prefix == BEFORE_PREFIX
                    since = None if before else upgraded.get(f.name)
                    if _reconcile_one(f, known, forget, before, schema <= supported, since):
                        changed += 1
                except (OSError, sqlite3.Error) as exc:
                    logger.warning("could not bring the copy %s in line (%s), so it is deleted", f.name, exc)
                    try:
                        f.unlink()
                    except OSError as err:
                        logger.error("could not delete the copy %s: %s", f.name, type(err).__name__)
    if changed:
        logger.info("brought %d copies saved at database upgrades in line with the database", changed)
    return changed


class LiveState(NamedTuple):
    """What the live database holds, for ``reconcile``."""

    members: set[str]
    disabled: set[str]
    sessions: set[tuple[str, str]]
    phones: set[tuple[str, str]]


def _upgrade_times(live: sqlite3.Connection) -> dict[str, int]:
    """For each set-aside copy going back recorded on ``live``, by its file name: when the
    before-upgrade copy put back in its place was saved, which is when that upgrade began."""
    out: dict[str, int] = {}
    for (detail,) in live.execute("SELECT detail_json FROM audit_log WHERE event = 'database_went_back'"):
        try:
            d = json.loads(detail or "")
            aside, copy = str(d["set_aside"]), Path(str(d["copy"]))
        except (ValueError, KeyError, TypeError):
            continue
        if _stamp_of(copy):
            out[aside] = _stamp_of(copy)
    return out


def _reconcile_one(
    path: Path,
    live: LiveState,
    forget: Callable[[sqlite3.Connection, str], object],
    before: bool,
    readable: bool,
    upgraded_at: int | None,
) -> bool:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        held = conn.execute("SELECT sub, first_login_at FROM users").fetchall()
        if before:
            gone = [sub for sub, _ in held if sub not in live.members]
        else:
            # a set-aside copy also holds those who joined after the upgrade: only those who were
            # already there at it are gone; with no record to date the upgrade by, a member it
            # holds who isn't here can't be told apart from one who is gone, and the copy goes
            absent = [(sub, int(first or 0)) for sub, first in held if sub not in live.members]
            if absent and upgraded_at is None:
                raise sqlite3.DatabaseError("it holds members who may be gone; the upgrade can't be dated")
            gone = [sub for sub, first in absent if upgraded_at is not None and first < upgraded_at]
        stale = [
            r[0]
            for r in conn.execute("SELECT sub, secret_enc FROM archidekt_links WHERE secret_enc != ''")
            if (r[0], r[1]) not in live.sessions
        ]
        phones = [
            r[0]
            for r in conn.execute("SELECT sub, ntfy_topic FROM notify_prefs WHERE ntfy_topic IS NOT NULL")
            if (r[0], r[1]) not in live.phones
        ]
        grants = [
            r[0]
            for r in conn.execute("SELECT sub FROM idp_grants")
            if r[0] in live.disabled or r[0] not in live.members
        ]
    finally:
        conn.close()
    if not (gone or stale or phones or grants):
        return False
    if gone and not readable:
        raise sqlite3.DatabaseError("it holds a member who is gone, in a schema newer than this reads")
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for sub in gone:
                forget(conn, sub)
            for sub in stale:
                conn.execute(_SCRUB["link"][0], (sub,))
            for sub in phones:
                conn.execute(_SCRUB["phone"][0], (sub,))
            for sub in grants:
                conn.execute("DELETE FROM idp_grants WHERE sub = ?", (sub,))
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("the copy failed its check")
    finally:
        conn.close()
    return True


def _scrub_one(path: Path, sub: str, what: str, forget: Callable[[sqlite3.Connection, str], object]) -> None:
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if what == "data":
                forget(conn, sub)
            else:
                for sql in _SCRUB[what]:
                    conn.execute(sql, (sub,))
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        # no whole-file check here: it reads the whole copy, and this runs inside a member's
        # request; the change commits whole or not at all, and the copies are checked at start
        conn.execute("COMMIT")
    finally:
        conn.close()


def summary(folder: Path) -> dict[str, object]:
    """The before-upgrade folder for the admin page: where it is, its newest copy, and what this
    start did."""
    newest = copies(folder)
    out: dict[str, object] = {"folder": str(folder), "copies": len(newest)}
    if newest:
        f = newest[0][0]
        try:
            out["newest"] = f.name
            out["newest_at"] = int(f.stat().st_mtime)
        except OSError:
            pass
    if last_event:
        out["event"] = dict(last_event)
    return out
