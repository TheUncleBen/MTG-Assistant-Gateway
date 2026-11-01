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

What it does not change: a lock taken by another connection after a request has passed this check
still makes that request's write wait on the loop (up to BUSY_TIMEOUT_MS, as before), and
background jobs don't pass through here. Writes keep their wait rather than failing at once, so a
change Archidekt already took is always recorded. Moving the database work off the loop entirely
is the fuller fix, kept for a later release.
"""

from __future__ import annotations

import asyncio
import logging
import time

from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

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
        self._waiting: asyncio.Future[bool] | None = None

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
        """Wait, off the loop, at most ``limit`` seconds until a write could start. Requests that
        arrive while one wait is running share it rather than starting their own thread."""
        deadline = time.monotonic() + limit
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            task = self._waiting
            if task is None or task.done():
                task = self._waiting = asyncio.ensure_future(asyncio.to_thread(self.db.wait_write_free, left))
            try:
                await asyncio.wait_for(asyncio.shield(task), left)
            except TimeoutError:
                return False
            if self.db.write_free():
                return True


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
