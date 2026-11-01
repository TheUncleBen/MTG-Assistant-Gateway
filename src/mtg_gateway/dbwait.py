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
after WAIT_LIMIT_SECONDS it is answered 503 with Retry-After, saying to try again: JSON for a
program or a page's script (static/fetchjson.js holds the request and shows one note with Try
again), a page in the site's look for a browser's own navigation, whose Try again sends the same
request again, a posted form with it (``busy_page``). Both are marked X-MTG-Busy, which the
Android app reads to leave the page in view instead of its own error box.

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
import html
import logging
import time
from collections.abc import Iterator
from contextvars import ContextVar
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl

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
BUSY_MESSAGE = (
    "The gateway is busy (its database is being backed up or changed). Nothing was changed; try again."
)
# The busy page says the same in two lines: the reason, then what to do.
BUSY_REASON = "Its database is being backed up or changed."


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
        await (await _busy(scope, receive))(scope, receive, send)

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


# A posted form is kept on the busy page, so Try again can post it once more; a body past this
# is not copied into the page (the member goes back to the form instead).
KEEP_BODY_LIMIT = 256 * 1024
BUSY_HEADER = "X-MTG-Busy"
# Nothing on the page can run or load from elsewhere; only its own small script (/static/busy.js,
# served without waiting for the database: SKIP_PREFIXES) and its own inline style.
_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; img-src data:; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
_FORM_TYPE = "application/x-www-form-urlencoded"


async def _busy(scope: Scope, receive: Receive) -> Response:
    """The answer for a request that waited the limit: JSON for a program or a page's script,
    marked X-MTG-Busy so the page's scripts (static/fetchjson.js) and the Android app tell it from
    a reverse proxy's own 503; for a browser's own navigation a page in the site's look whose Try
    again really sends the same request again. Nothing of the request reached the app, so nothing
    was changed."""
    headers = {"Retry-After": "5", "Cache-Control": "no-store", BUSY_HEADER: "1"}
    path = str(scope.get("path", ""))
    request_headers = Headers(scope=scope)
    if path.startswith(MACHINE_PREFIXES) or "text/html" not in request_headers.get("accept", ""):
        return JSONResponse({"ok": False, "error": "busy", "message": BUSY_MESSAGE}, 503, headers=headers)
    query = scope.get("query_string", b"").decode("latin-1")
    same_url = path + (f"?{query}" if query else "")
    method = str(scope.get("method", "GET")).upper()
    fields: list[tuple[str, str]] | None = None
    if method == "POST":
        fields = await _posted_fields(receive, request_headers)
    page = busy_page(same_url, method, fields, theme=_theme_cookie(request_headers.get("cookie", "")))
    return HTMLResponse(page, 503, headers={**headers, "Content-Security-Policy": _CSP})


async def _posted_fields(receive: Receive, headers: Headers) -> list[tuple[str, str]] | None:
    """A posted form's fields, in their order, when the body is a plain form within the limit;
    None otherwise (a file upload, JSON, or too long). The body is read either way, so the
    connection is left clean for the next request."""
    keep = headers.get("content-type", "").split(";")[0].strip().lower() == _FORM_TYPE
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return None
        if keep and len(body) <= KEEP_BODY_LIMIT:
            body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break
    if not keep or len(body) > KEEP_BODY_LIMIT:
        return None
    return parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)


def _theme_cookie(cookie: str) -> str:
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "mtg_theme" and value in ("light", "dark"):
            return value
    return ""


# The site's own colours (theme.py), as the offline page has them: the page is sent by this
# middleware, outside the app, so it carries its own small style and no stylesheet.
_STYLE = (
    ":root{--bg:#181818;--surface:#232323;--border:#5f5f5f;--text:#e3e3e3;--muted:#a8a8a8;--focus:#fa890d}"
    "@media (prefers-color-scheme: light){:root:not([data-theme=dark]){--bg:#f9fafb;--surface:#fafafa;"
    "--border:#bababa;--text:#383838;--muted:#5e5e5e;--focus:#944b00}}"
    ":root[data-theme=light]{--bg:#f9fafb;--surface:#fafafa;--border:#bababa;--text:#383838;"
    "--muted:#5e5e5e;--focus:#944b00}"
    "body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
    "font:16px/1.4 Lato,'Helvetica Neue',Arial,sans-serif;background:var(--bg);color:var(--text);"
    "padding:1rem;box-sizing:border-box}"
    "main{max-width:28rem;width:100%;text-align:center}"
    "h1{font-size:1.4rem;margin:.75rem 0 .5rem;overflow-wrap:anywhere}"
    "p{margin:.5rem 0;overflow-wrap:anywhere}"
    ".site{color:var(--muted);font-weight:700;font-size:.9rem;margin:0}"
    "img{width:64px;height:64px;border-radius:14px}form{margin:0}"
    ".btn{display:inline-flex;align-items:center;justify-content:center;min-height:44px;min-width:44px;"
    "margin-top:1rem;padding:0 1.2rem;border:0;border-radius:5px;background:#fa890d;color:#111;"
    "font:inherit;font-weight:700;text-decoration:none;cursor:pointer;max-width:100%;box-sizing:border-box}"
    ".btn:focus-visible{outline:2px solid var(--focus);outline-offset:2px}"
)


def busy_page(same_url: str, method: str, fields: list[tuple[str, str]] | None, *, theme: str = "") -> str:
    """The busy page for a browser's own navigation to ``same_url``. Try again: for a GET, the
    same address; for a posted form, the same form (its fields kept as hidden inputs) posted
    again; for a post that cannot be copied (a file, too long), Go back to the form the member
    filled in (static/busy.js keeps its typed draft for it)."""
    from .theme import ICON_DATA_URL  # a data: URL, so the icon shows with the database busy

    url = html.escape(same_url, quote=True)
    theme_attr = f" data-theme='{theme}'" if theme else ""
    script = ""
    if method != "POST":
        what = "Nothing was changed."
        action = f"<a class='btn btn-primary' href='{url}'>Try again</a>"
    elif fields is not None:
        what = "Nothing was sent, so nothing was changed. What you sent is kept here."
        hidden = "".join(
            f"<input type='hidden' name='{html.escape(name, quote=True)}' "
            f"value='{html.escape(value, quote=True)}'>"
            for name, value in fields
        )
        action = (
            f"<form method='post' action='{url}'>{hidden}"
            "<button type='submit' class='btn btn-primary'>Try again</button></form>"
        )
    else:
        what = "Nothing was sent, so nothing was changed. Go back to what you typed, then send it again."
        action = "<button type='button' class='btn btn-primary' data-busy-back>Go back</button>"
        script = "<script src='/static/busy.js' defer></script>"
    return (
        f"<!doctype html><html lang='en'{theme_attr}>"
        "<head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<meta name='color-scheme' content='{theme or 'dark light'}'>"
        "<meta name='robots' content='noindex, nofollow'>"
        "<title>The gateway is busy</title>"
        f"<link rel='icon' href='{ICON_DATA_URL}' type='image/svg+xml'>"
        f"<style>{_STYLE}</style></head><body><main>"
        f"<img src='{ICON_DATA_URL}' alt=''>"
        f"<h1>The gateway is busy</h1><p>{html.escape(BUSY_REASON)}</p><p>{html.escape(what)}</p>"
        f"{action}</main>{script}</body></html>"
    )
