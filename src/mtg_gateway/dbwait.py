"""While another connection holds the database's write lock, reads go ahead and writes wait off
the event loop (0.7.46 audit, row 9; re-audit).

The gateway's database work runs on the event loop, and a write that finds the database locked
by another connection (``mtg-gateway backup``, an admin command run in the container, a worker
thread's purge) waits up to db.BUSY_TIMEOUT_MS for it. In WAL mode a read never waits for a
writer, so only writes have to.

Before a request goes on, this asks the database, without waiting, whether a write could start
(Database.write_free). Nearly always it can, and the request goes on as before. When it can't:

- A request that should only read goes ahead at once (``_read_through``): a page or JSON read
  (GET or HEAD; never a sign-in step, NEVER_READ_THROUGH) or an assistant's call of tools that
  only read (``reads``; never a proposal or an apply). Its writes don't wait (db.ReadThrough): the
  usage counters are skipped, and any other write that finds the lock held fails at once, having
  changed nothing. Its answer is held back until it ends; when a write of it failed so, the
  answer is dropped and the request waits as below and then runs again from the start. Before
  anything that changes something outside the gateway (an Archidekt write or sign-in, the
  identity provider's tokens, a push notification: ``before_side_effect``) it goes back to
  waiting, so that change is made once and what it gives back is kept.
- Any other request waits on a worker thread (Database.wait_write_free, one wait shared by every
  request that finds it locked), so the loop keeps serving, then goes on; after
  WAIT_LIMIT_SECONDS it is answered 503 with Retry-After, saying to try again: JSON for a program
  or a page's script (static/fetchjson.js holds the request and shows one note with Try again), a
  page in the site's look for a browser's own navigation, whose Try again sends the same request
  again, a posted form with it (``busy_page``). Both are marked X-MTG-Busy, which the Android app
  reads to leave the page in view instead of its own error box.

Background jobs don't pass through here; they wait the same way where they already wait on the
network (``job`` and ``settle`` below; a member's apply is one), and a job run in a worker thread
waits for the lock without holding up the loop (Database._begin_off_loop).

What it does not change: a lock taken by another connection after a request has passed this check
still makes that request's next write wait on the loop (up to BUSY_TIMEOUT_MS, as before). Writes
keep their wait rather than failing at once, so a change Archidekt already took is always
recorded; a request can't be stopped and run again once it may have changed something. A tool
call whose write still finds the lock is answered busy, never with a raw error
(``tool_busy_middleware``); an apply records how it ended once the lock is free (decks); a token
request writes all or nothing (auth_provider)."""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl

from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    from .db import Database

logger = logging.getLogger(__name__)

# The longest a request waits for the database before it is answered 503.
WAIT_LIMIT_SECONDS = 20.0
# Static files and the health check never need a write, so they never wait.
SKIP_PREFIXES = ("/static/", "/scan/static/", "/healthz")
# Sign-in steps: each one changes something at the identity provider or keeps a one-time code,
# so it always waits for the lock rather than going ahead (DatabaseWaitMiddleware._may_read).
NEVER_READ_THROUGH = ("/auth/", "/authorize", "/login", "/logout", "/token", "/register", "/revoke")
# An assistant's request body is read ahead (to see which tools it calls) up to this size.
READ_AHEAD_LIMIT = 1024 * 1024
# Paths answered for programs, which get JSON (as app._MACHINE_PREFIXES).
MACHINE_PREFIXES = ("/api/", "/mcp", "/.well-known/", "/token", "/register", "/revoke", "/authorize")
BUSY_MESSAGE = (
    "The gateway is busy (its database is being backed up or changed). Nothing was changed; try again."
)
# The busy page says the same in two lines: the reason, then what to do.
BUSY_REASON = "Its database is being backed up or changed."
# A tool call whose own write met the lock after it passed the check: whether anything of it was
# kept is not known for a change, so a changing tool's answer says to look before sending again.
TOOL_BUSY = (
    "The gateway is busy (its database is being backed up or changed), so this call could not "
    "finish. Try again."
)
TOOL_BUSY_CHANGE = (
    " If it was a change, check where it stands first (list_my_proposals or get_proposal), so "
    "nothing is made twice."
)


class DatabaseWaitMiddleware:
    """See the module's docstring. ``reads(name)``: whether the assistant tool ``name`` only reads
    (a call of nothing else goes ahead while the lock is held); None: every assistant call waits."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        db: Database,
        limit: float = WAIT_LIMIT_SECONDS,
        reads: Callable[[str], bool] | None = None,
    ):
        self.app = app
        self.db = db
        self.limit = limit
        self.reads = reads

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or str(scope.get("path", "")).startswith(SKIP_PREFIXES)
            or self.db.write_free()
        ):
            await self.app(scope, receive, send)
            return
        deadline = time.monotonic() + self.limit
        body: bytes | None = b""
        if self._may_read(scope):
            if str(scope.get("method", "")).upper() == "POST":
                body, receive = await _read_body(receive)
            if body is not None and self._reads_only(scope, body):
                if await self._read_through(scope, receive, send, deadline):
                    return
                receive = _again(body, receive)  # it needed a write: run again once the lock is free
        if await self.until_write_free(max(0.0, deadline - time.monotonic())):
            await self.app(scope, receive, send)
            return
        logger.warning("a request was turned away: the database stayed locked for %.0f s", self.limit)
        await (await _busy(scope, receive))(scope, receive, send)

    async def until_write_free(self, limit: float) -> bool:
        return await until_write_free(self.db, limit)

    def _may_read(self, scope: Scope) -> bool:
        """A page or JSON read (GET or HEAD, not a sign-in step), or an assistant's call."""
        path = str(scope.get("path", ""))
        method = str(scope.get("method", "")).upper()
        if path == "/mcp" or path.startswith("/mcp/"):
            return method == "POST" and self.reads is not None
        return method in ("GET", "HEAD") and not path.startswith(NEVER_READ_THROUGH)

    def _reads_only(self, scope: Scope, body: bytes) -> bool:
        path = str(scope.get("path", ""))
        if not (path == "/mcp" or path.startswith("/mcp/")):
            return True
        from .app import mcp_only_reads

        return self.reads is not None and mcp_only_reads(body, self.reads)

    async def _read_through(self, scope: Scope, receive: Receive, send: Send, deadline: float) -> bool:
        """Run a request that should only read while another connection holds the write lock,
        without waiting for it (db.ReadThrough). Its answer is held back until it ends: when a write
        of it found the lock held, the answer is dropped and False returned, so the request is run
        again, from the start, once the lock is free. Nothing of it was written then, and nothing
        left the gateway that changes anything (before_side_effect), so running it again is safe.
        A streamed answer (an event stream) is passed on as it comes; its writes wait as before."""
        from .db import DatabaseBusy, ReadThrough, read_through

        through = ReadThrough(self.db, deadline)
        held: list[Message] = []
        passing = False

        async def keep(message: Message) -> None:
            nonlocal passing
            if not passing and message["type"] == "http.response.start" and _streamed(message):
                passing = True
                through.active = False  # can't be run again: its writes wait from here
                for m in held:
                    await send(m)
                held.clear()
            if passing:
                await send(message)
            else:
                held.append(message)

        token = read_through.set(through)
        try:
            await self.app(scope, receive, keep)
        except Exception as exc:
            if passing or not (through.busy or isinstance(exc, DatabaseBusy)):
                raise
            through.busy = True
        finally:
            through.active = False
            read_through.reset(token)
        if passing:
            return True
        if through.busy:
            if not through.committed:
                return False
            # part of it was written before the lock was taken again: not run twice
            logger.warning("a read went ahead during a database lock and met it again; answered busy")
            await (await _busy(scope, _again(b"", receive)))(scope, receive, send)
            return True
        for m in held:
            await send(m)
        return True


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


def tool_busy_middleware(only_reads: Callable[[str], bool]):
    """MCP middleware: a tool call whose write found the database locked past the busy timeout
    (another connection took the lock after the call passed the check above) is answered with
    the plain busy refusal (error "busy", retryable), never the framework's raw "Error executing
    tool". ``only_reads(name)``: the tool changes nothing, so its answer says nothing was changed.
    Sits inside schema_error_middleware, which adds the error code."""
    from mcp import types

    from .db import lock_failures

    async def _mw(ctx: Any, call_next: Any) -> Any:
        if ctx.method != "tools/call" or not isinstance(ctx.params, dict):
            return await call_next(ctx)
        name = str(ctx.params.get("name") or "")
        seen: dict[str, bool] = {}
        token = lock_failures.set(seen)
        try:
            result = await call_next(ctx)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc):
                raise
            seen["locked"] = True
            result = None
        finally:
            lock_failures.reset(token)
        failed = result is None or (
            result.get("isError") if isinstance(result, dict) else getattr(result, "isError", False)
        )
        if not (seen.get("locked") and failed):
            return result
        logger.warning("tool %s met the database's lock and was answered busy", name)
        message = BUSY_MESSAGE if only_reads(name) else TOOL_BUSY + TOOL_BUSY_CHANGE
        body = {"ok": False, "error": "busy", "message": message, "retry_after": 5}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(body))],
            structuredContent=body,
            isError=True,
        ).model_dump(by_alias=True, exclude_none=True, mode="json")

    return _mw


async def before_side_effect() -> None:
    """Awaited just before anything that changes something outside the gateway (an Archidekt
    write or sign-in, the identity provider's tokens, a push notification). In a request let
    through while the lock is held (db.ReadThrough) it turns the request back to the old way:
    it waits, off the loop, until the lock is free, and its writes from here on wait for it, so
    what the outside change gives back is always kept and the request is never run twice. When
    one of its writes already found the lock held (it will be run again), or the lock stays held
    past the request's limit, DatabaseBusy is raised before anything is sent."""
    from .db import DatabaseBusy, read_through

    through = read_through.get()
    if through is None or not through.active:
        return
    if through.busy:
        raise DatabaseBusy()
    through.active = False
    if not through.db.write_free() and not await until_write_free(
        through.db, max(0.0, through.deadline - time.monotonic())
    ):
        through.busy = True
        raise DatabaseBusy()


def _streamed(start: Message) -> bool:
    """Whether a response is a stream (server-sent events, a line-by-line relay), sent as it comes."""
    for name, value in start.get("headers") or []:
        if name.lower() == b"content-type":
            kind = value.decode("latin-1").split(";")[0].strip().lower()
            return kind in ("text/event-stream", "application/x-ndjson")
    return False


async def _read_body(receive: Receive) -> tuple[bytes | None, Receive]:
    """A request's body read ahead (None when longer than READ_AHEAD_LIMIT or the client left),
    and a ``receive`` that hands the app the same messages, then carries on with the real one."""
    held: list[Message] = []
    size = 0
    complete = False
    while True:
        message = await receive()
        held.append(message)
        if message["type"] != "http.request":
            break
        size += len(message.get("body", b""))
        if size > READ_AHEAD_LIMIT:
            break
        if not message.get("more_body", False):
            complete = True
            break
    body = b"".join(m.get("body", b"") for m in held) if complete else None

    async def replay() -> Message:
        if held:
            return held.pop(0)
        return await receive()

    return body, replay


def _again(body: bytes | None, receive: Receive) -> Receive:
    """A ``receive`` for running a request again: its body once more, then the real one (which
    only says when the client leaves)."""
    sent = False

    async def again() -> Message:
        nonlocal sent
        if not sent and body is not None:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return again


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
# A posted field whose name says it holds a secret is never written back into a page. The forms'
# own names: archidekt_password (the Archidekt link form, pages.py); token (an undo form's
# one-time token, collection.py, lists_page.py, watchlist_page.py); the OAuth fields code,
# code_verifier, client_secret, refresh_token reach /token, a machine path, but are listed in
# case a form ever carries one. Kept as plain names: csrf (the page's own form token, which the
# posted form needs again), set_code, card_key, pin_id, keywords, archidekt_login.
SECRET_WORDS = frozenset(
    {"password", "passwd", "pwd", "pass", "secret", "token", "verifier", "otp", "credential", "auth"}
)
SECRET_NAMES = frozenset({"code", "key", "pin"})


def looks_secret(name: str) -> bool:
    """Whether a posted field's name says it holds a secret (see SECRET_WORDS)."""
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower().replace("-", "_")
    if snake in SECRET_NAMES or "api_key" in snake or "apikey" in snake:
        return True
    return any(w in SECRET_WORDS for w in re.split(r"[^a-z0-9]+", snake) if w)


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
        if fields is not None and any(looks_secret(name) for name, _ in fields):
            fields = None  # a password or a token goes into no page: Go back to the form instead
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
