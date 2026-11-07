"""A write made on an event loop takes the event loop's path in db.tx(), whichever thread runs that
loop. The check used to be "not the main thread", so the browser tests (uvicorn's loop runs in a
thread of its own there) took the worker-thread path, which production never does: a request let
through during a lock (dbwait.ReadThrough) waited up to BUSY_TIMEOUT_MS for it instead of being
refused at once with DatabaseBusy, as it is in production."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

from mtg_gateway.db import BUSY_TIMEOUT_MS, Database, DatabaseBusy, ReadThrough, read_through


def _write_on_a_loop_in_a_thread(db: Database) -> tuple[BaseException | None, float]:
    out: dict[str, object] = {}

    async def write() -> None:
        read_through.set(ReadThrough(db, time.monotonic() + 30))
        started = time.monotonic()
        try:
            db.touch_user("user-1")
        except BaseException as exc:  # noqa: BLE001 - the outcome is what is measured
            out["error"] = exc
        out["took"] = time.monotonic() - started

    thread = threading.Thread(target=lambda: asyncio.run(write()))
    thread.start()
    thread.join(BUSY_TIMEOUT_MS / 1000 + 5)
    assert not thread.is_alive()
    return out.get("error"), out["took"]  # type: ignore[return-value]


def test_a_write_on_a_loop_outside_the_main_thread_is_refused_at_once_during_a_lock(tmp_path: Path) -> None:
    db = Database(tmp_path / "gw.db")
    holder = sqlite3.connect(str(tmp_path / "gw.db"), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        error, took = _write_on_a_loop_in_a_thread(db)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert isinstance(error, DatabaseBusy), repr(error)
    assert took < 1.0, took


def test_a_worker_thread_without_a_loop_still_waits_for_the_lock_off_the_loop(tmp_path: Path) -> None:
    """The worker-thread path stays for a thread with no loop (a job run with asyncio.to_thread):
    it waits for the lock and writes once it is free."""
    db = Database(tmp_path / "gw.db")
    db.upsert_user("user-1", email=None, name=None, preferred_username="alice", groups=[])
    holder = sqlite3.connect(str(tmp_path / "gw.db"), isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    threading.Timer(0.5, lambda: holder.execute("ROLLBACK")).start()
    out: dict[str, object] = {}

    def job() -> None:
        started = time.monotonic()
        db.touch_user("user-1")
        out["took"] = time.monotonic() - started

    thread = threading.Thread(target=job)
    thread.start()
    thread.join(BUSY_TIMEOUT_MS / 1000 + 5)
    holder.close()
    assert 0.4 < out["took"] < BUSY_TIMEOUT_MS / 1000, out
