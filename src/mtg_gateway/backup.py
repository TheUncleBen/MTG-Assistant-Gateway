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
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .db import Database

logger = logging.getLogger(__name__)

PREFIX = "mtg-gateway-"
SUFFIX = ".sqlite"


def export_now(db: Database, backup_dir: Path, keep_days: int) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"{PREFIX}{stamp}{SUFFIX}"
    tmp = dest.with_suffix(".tmp")
    # Create the file owner-only first: `docker exec ... backup` doesn't inherit the entrypoint's
    # umask 077, and SQLite keeps an existing file's mode (its journal copies it too).
    tmp.unlink(missing_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    os.chmod(tmp, 0o600)
    db.backup_to(tmp)
    tmp.replace(dest)
    removed = prune(backup_dir, keep_days)
    logger.info("backup written to %s (%d old copies removed)", dest, removed)
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


async def nightly_loop(db: Database, backup_dir: Path, hour_utc: int, keep_days: int) -> None:
    while True:
        await asyncio.sleep(seconds_until(hour_utc))
        try:
            await asyncio.to_thread(export_now, db, backup_dir, keep_days)
            await asyncio.to_thread(db.purge_expired)
        except Exception:
            logger.exception("nightly backup failed")
