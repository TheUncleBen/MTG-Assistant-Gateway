"""Requests wait off the event loop while the database is locked (0.7.46 audit, row 9).

The gateway's database work runs on the event loop, and a write that finds the database locked
by another connection (``mtg-gateway backup``, an admin command run in the container, a worker
thread's purge) waits up to db.BUSY_TIMEOUT_MS for it. Nearly every request writes something
(the usage counters, a sign-in's last use, a waiting proposal's hold), so one such lock held for
a few seconds stalled every request and every background job together, pages, assistants and the
health check alike, for as long as it was held.

Before a request goes on, this asks the database, without waiting, whether a write could start
(Database.write_free). Nearly always it can, and the request goes on as before. When it can't,
the request waits on a worker thread (Database.wait_write_free, one wait shared by every request
that finds it locked), so the loop keeps serving what doesn't need the database, then goes on;
after WAIT_LIMIT_SECONDS it is answered 503 with Retry-After, saying to try again.

Background jobs don't pass through here; they wait the same way where they already wait on the
network (``job`` and ``settle`` below), and a job run in a worker thread waits for the lock
without holding up the loop (Database._begin_off_loop).

What it does not change: a lock taken by another connection after a request has passed this check
still makes that request's next write wait on the loop (up to BUSY_TIMEOUT_MS, as before). Writes
keep their wait rather than failing at once, so a change Archidekt already took is always
recorded; a request can't be stopped and run again once it may have changed something.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterator
from contextvars import ContextVar
from typing import TYPE_CHECKING

from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from .db import Database

logger = logging.getLogger(__name__)

# The longest a request waits for the database before it is answered 503.
WAIT_LIMIT_SECONDS = 20.0
# Static files and the health check never need a write, so they never wait.
SKIP_PREFIXES = ("/static/", "/scan/static/", "/healthz")
# Paths answered for programs, which get JSON (as app._MACHINE_PREFIXES).
MACHINE_PREFIXES = ("/api/", "/mcp", "/.well-known/", "/token", "/register", "/revoke", "/authorize")
BUSY_MESSAGE = "The gateway is busy: its database is being backed up or changed. Try again."


class DatabaseWaitMiddleware:
    def __init__(self, app: ASGIApp, *, db: Database, limit: float = WAIT_LIMIT_SECONDS):
        self.app = app
        self.db = db
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or str(scope.get("path", "")).startswith(SKIP_PREFIXES)
            or self.db.write_free()
        ):
            await self.app(scope, receive, send)
            return
        if await self.until_write_free(self.limit):
            await self.app(scope, receive, send)
            return
        logger.warning("a request was turned away: the database stayed locked for %.0f s", self.limit)
        await _busy(scope)(scope, receive, send)

    async def until_write_free(self, limit: float) -> bool:
        return await until_write_free(self.db, limit)


async def until_write_free(db: Database, limit: float) -> bool:
    """Wait, off the loop, at most ``limit`` seconds until a write could start. Everything that
    finds the lock held while one wait is running (requests, jobs) shares it rather than starting
    a thread of its own."""
    deadline = time.monotonic() + limit
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        task: asyncio.Future[bool] | None = getattr(db, "_async_wait", None)
        if task is None or task.done():
            task = asyncio.ensure_future(asyncio.to_thread(db.wait_write_free, left))
            db._async_wait = task  # type: ignore[attr-defined]
        try:
            await asyncio.wait_for(asyncio.shield(task), left)
        except TimeoutError:
            return False
        if db.write_free():
            return True


# -- background jobs -------------------------------------------------------------------------------
# The database a background job works with, set for the length of one job step (``job``). Where the
# job already waits on the network (an Archidekt or Scryfall answer, a notification sent), the wait
# goes on, off the loop, until the write lock is free too (``settle``), so the database work that
# follows never waits for it on the loop. Nothing new can run in between: the job was already
# waiting at that point, so the order of its own reads, decisions and writes is unchanged.
_job_db: ContextVar[Database | None] = ContextVar("dbwait_job_db", default=None)
# The longest a job waits so; after it, its write waits on the loop as it always did.
JOB_WAIT_SECONDS = 60.0


@contextlib.contextmanager
def job(db: Database | None) -> Iterator[None]:
    """One background job step: the network answers it awaits settle (see above)."""
    token = _job_db.set(db)
    try:
        yield
    finally:
        _job_db.reset(token)


async def settle() -> None:
    """In a background job step, wait off the loop until the database's write lock is free (at
    most JOB_WAIT_SECONDS). Anywhere else, nothing: a request's own waits are not stretched."""
    db = _job_db.get()
    if db is None:
        return
    try:
        if not db.write_free():
            await until_write_free(db, JOB_WAIT_SECONDS)
    except Exception:  # only ever a wait: the job's own work then says what is wrong
        logger.debug("waiting for the database's write lock failed", exc_info=True)


def _busy(scope: Scope) -> Response:
    headers = {"Retry-After": "5"}
    path = str(scope.get("path", ""))
    if path.startswith(MACHINE_PREFIXES) or "text/html" not in Headers(scope=scope).get("accept", ""):
        return JSONResponse({"ok": False, "error": "busy", "message": BUSY_MESSAGE}, 503, headers=headers)
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>The gateway is busy</title>"
        "</head><body style='font-family:system-ui,sans-serif;max-width:32rem;"
        "margin:3rem auto;padding:0 1rem'>"
        f"<h1>The gateway is busy</h1><p>{BUSY_MESSAGE}</p><p><a href=''>Try again</a></p></body></html>"
    )
    return HTMLResponse(page, 503, headers=headers)
