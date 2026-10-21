"""The copy of the database the gateway saves before it upgrades it, and going back to it.

When a new version starts on a database an older version wrote, it first saves a full copy of that
database in the before-upgrade folder (``before-upgrade`` inside ``MTG_BACKUP_DIR``, or inside the
data folder when backups are off), checks the copy, and only then upgrades. No copy, no upgrade:
the gateway stops with a message saying why, and the database is left as it was.

The copy is the database exactly as it was, members' Archidekt links and identity-provider tokens
included (still encrypted with the Fernet key, which is never in the file), so going back needs
nobody to relink. That is why it stays in its own owner-only folder and is never copied to
``MTG_BACKUP_COPY_DIR``. The newest ``KEEP`` copies are kept.

Going back: when an older version (this one or newer) starts on a database a newer version
upgraded, it looks in the same folder for its own before-upgrade copy. With one, it first saves
the newer database there as a set-aside copy (nothing is lost), puts its copy back, ends every
sign-in so everyone signs in once more, and starts. Without one, it stops and says what to do.
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
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

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
_NAME = re.compile(r"^mtg-gateway-(?:before-upgrade|set-aside)-(\d{8}T\d{6}Z)-schema(\d+)\.sqlite$")

# What this process did at start, for the admin page: {"kind": "upgraded" | "went-back", ...}.
last_event: dict[str, object] = {}


class DatabaseUpgradeError(RuntimeError):
    """The gateway can't open its database safely and must not start; the message says why and
    what to do."""


def folder_for(backup_dir: Path | None, db_path: Path) -> Path:
    """Where before-upgrade copies live: inside the backup folder, else next to the database."""
    return (backup_dir if backup_dir is not None else db_path.parent) / FOLDER


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
    source: sqlite3.Connection, folder: Path, name: str, expect_schema: int, db_path: Path
) -> Path:
    """Copy the database behind ``source`` into ``folder`` as ``name`` with SQLite's online backup
    API, check it, and only then give it its name. Raises OSError or sqlite3.Error on failure."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
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
    """The newest older copy of ``schema`` when it holds exactly the same bytes as ``new`` (which is
    then deleted), else ``new``. SQLite's backup copies page by page, so an unchanged database
    gives the same file."""
    older = next((f for f, s in copies(folder) if s == schema and f != new), None)
    if older is None:
        return new
    try:
        if _sha256(older) != _sha256(new):
            return new
        new.unlink()
    except OSError:
        return new
    return older


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
    try:
        linked = {r[0] for r in newer.execute("SELECT sub FROM archidekt_links WHERE secret_enc != ''")}
        for (sub,) in old.execute("SELECT sub FROM archidekt_links WHERE secret_enc != ''").fetchall():
            if sub not in linked:
                old.execute(
                    "UPDATE archidekt_links SET secret_enc = '', status = 'revoked' WHERE sub = ?", (sub,)
                )
    except sqlite3.Error as exc:
        logger.warning("going back: could not read which Archidekt sessions were deleted: %s", exc)
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
    try:
        aside = _write_copy(conn, folder, aside_name, found, db_path)
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
