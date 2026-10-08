"""SQLite backup: an export-now command and a nightly loop.

A backup is a consistent copy of the gateway database made with SQLite's
online backup API, written to ``MTG_BACKUP_DIR`` as
``mtg-gateway-<UTC timestamp>.sqlite``. Copies older than
``MTG_BACKUP_KEEP_DAYS`` are deleted after each run. The database holds user
records, registered OAuth clients, hashed tokens and the audit log. It never
holds the Fernet key, which lives in a Docker secret and must be kept
separately: a restored database without that key cannot decrypt anything that
later phases encrypt with it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .db import Database

logger = logging.getLogger(__name__)

PREFIX = "mtg-gateway-"
SUFFIX = ".sqlite"
WORK_PREFIX = ".backup-"  # the private folder a backup is written in before it is moved into place
STALE_WORK_SECONDS = 3600  # a work folder older than this was left by a crash and is removed


def _remove_work_dir(work: Path) -> None:
    try:
        shutil.rmtree(work)
    except OSError as exc:
        logger.warning("could not remove backup work folder %s: %s", work, type(exc).__name__)


def sweep_work_dirs(backup_dir: Path, older_than: float = STALE_WORK_SECONDS) -> int:
    """Remove ``.backup-*`` work folders a crashed or killed backup left behind, with the partial
    copy inside them. A folder younger than ``older_than`` seconds may belong to a backup still
    running in another process (``docker exec ... backup``) and is left alone."""
    cutoff = time.time() - older_than
    removed = 0
    for d in backup_dir.glob(f"{WORK_PREFIX}*"):
        try:
            if d.is_dir() and not d.is_symlink() and d.lstat().st_mtime < cutoff:
                _remove_work_dir(d)
                removed += 1
                logger.info("removed stale backup work folder %s", d)
        except OSError as exc:
            logger.warning("could not inspect %s: %s", d, type(exc).__name__)
    return removed


def export_now(db: Database, backup_dir: Path, keep_days: int, copy_dir: Path | None = None) -> Path:
    """Write a backup to ``backup_dir`` and, when ``copy_dir`` is set (``MTG_BACKUP_COPY_DIR``,
    say a network share), a second copy there, pruned the same way. A failed copy is logged and
    recorded in ``last_run["copy_error"]``; the main backup still counts as written."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"{PREFIX}{stamp}{SUFFIX}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    # SQLite opens the file by name, so it is written inside a fresh owner-only folder nobody else
    # can put a link in, then moved into place. The file is created owner-only first: `docker exec
    # ... backup` doesn't inherit the entrypoint's umask 077, and SQLite keeps an existing file's
    # mode (its journal copies it too).
    sweep_work_dirs(backup_dir)
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=backup_dir))  # mode 0700
    try:
        tmp = work / dest.name
        os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600))
        db.backup_to(tmp)
        tmp.replace(dest)
    finally:
        _remove_work_dir(work)
    removed = prune(backup_dir, keep_days)
    logger.info("backup written to %s (%d old copies removed)", dest, removed)
    last_run["copy_error"] = None
    if copy_dir is not None:
        try:
            copy_backup(dest, copy_dir, keep_days)
        except OSError as exc:
            last_run["copy_error"] = type(exc).__name__
            logger.error("backup copy to %s failed: %s", copy_dir, type(exc).__name__)
    return dest


def copy_backup(src: Path, copy_dir: Path, keep_days: int) -> Path:
    """Copy one backup file into ``copy_dir`` owner-only (via a temporary name, so a half-written
    copy never looks like a backup) and prune old copies there."""
    copy_dir.mkdir(parents=True, exist_ok=True)
    dest = copy_dir / src.name
    tmp = dest.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    # The copy folder may be shared storage others can write to: the temporary file is opened once,
    # never through a symlink, and written through that handle, so nothing swapped in at its name
    # can redirect the write.
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as out, open(src, "rb") as data:
        shutil.copyfileobj(data, out)
        os.fchmod(out.fileno(), 0o600)
        if not os.path.samestat(os.fstat(out.fileno()), os.lstat(tmp)):
            raise OSError("the temporary backup copy was replaced while it was written")
    tmp.replace(dest)
    removed = prune(copy_dir, keep_days)
    logger.info("backup copied to %s (%d old copies removed)", dest, removed)
    return dest


def prune(backup_dir: Path, keep_days: int) -> int:
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for f in backup_dir.glob(f"{PREFIX}*{SUFFIX}"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("could not prune %s: %s", f, exc)
    return removed


def seconds_until(hour_utc: int, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


# The outcome of the last backup this process attempted, for the admin page. Survives nothing:
# after a restart the admin page falls back to the newest file in MTG_BACKUP_DIR.
last_run: dict[str, object] = {}


def newest_backup(backup_dir: Path) -> tuple[Path, float] | None:
    best: tuple[Path, float] | None = None
    for f in backup_dir.glob(f"{PREFIX}*{SUFFIX}"):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[1]:
            best = (f, mtime)
    return best


async def nightly_loop(
    db: Database, backup_dir: Path, hour_utc: int, keep_days: int, copy_dir: Path | None = None
) -> None:
    while True:
        await asyncio.sleep(seconds_until(hour_utc))
        try:
            dest = await asyncio.to_thread(export_now, db, backup_dir, keep_days, copy_dir)
            last_run.update(ok=True, at=int(time.time()), file=dest.name, error=None)
        except Exception as exc:
            last_run.update(ok=False, at=int(time.time()), error=type(exc).__name__)
            logger.exception("nightly backup failed")


PURGE_INTERVAL_SECONDS = 3600


async def purge_loop(
    db: Database, interval: float = PURGE_INTERVAL_SECONDS, also: Callable[[], object] | None = None
) -> None:
    """Expire tokens, sessions and proposals and apply the retention caps every hour, whether or
    not backups are configured; ``also`` runs after each round (the expired Archidekt sessions).
    A failure is logged and retried on the next round."""
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(db.purge_expired)
        except Exception:
            logger.exception("database purge failed")
        if also is not None:
            try:
                await asyncio.to_thread(also)
            except Exception:
                logger.exception("purge of expired Archidekt sessions failed")
