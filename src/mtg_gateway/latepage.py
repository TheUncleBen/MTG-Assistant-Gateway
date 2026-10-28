"""Pages that read Archidekt draw at once (Standard 1 of the 0.7.46 audit: "cache first, never
blank").

A page such as a deck, Notifications, the forum, a profile, My lists or the Binder is drawn from
what Archidekt answers. When that answer is slow (the shared pacer is busy, or Archidekt itself
is slow), the browser used to show nothing but a thin bar until it came. :class:`LatePageMiddleware`
lets such a page run as usual; if it has not answered within LATE_AFTER, the member gets the
site's own frame at once (top bar, title, a skeleton and a line saying the page is being read
from Archidekt), and the page carries on in the background. static/feedback.js then waits on
``/late/{key}`` for it to finish and loads the page's address again, which is answered with the
finished page, kept for that member's session for LATE_KEEP. Nothing is shown that the page
itself would not show, and nothing is read twice: the finished answer is served once.

The finished answer is kept in memory under a fingerprint of the member's session cookie and
the page's address, so it is only ever served to the browser session that asked for it.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import html
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .timing import (
    PageTime,
    archidekt_failures,
    archidekt_time,
    logged_path,
    pacer_time,
    speed_log,
)

LATE_AFTER = 0.8  # seconds a page may take before the frame is sent instead
LATE_KEEP = 60.0  # a finished page is kept this long for the browser to fetch
LATE_WAIT = 120.0  # the longest /late/{key} waits for a page to finish
LATE_MAX = 300  # pages running or kept at most (the oldest kept ones go first)

# The pages that read Archidekt before they draw: (address, title, the nav place to mark).
_PAGES = (
    (re.compile(r"^/decks/\d{1,12}$"), "Deck", "/decks"),
    (re.compile(r"^/notifications$"), "Notifications", ""),
    (re.compile(r"^/forum(/[^/]+){0,3}$"), "Forum", ""),
    (re.compile(r"^/users/[^/]{1,80}$"), "Profile", ""),
    (re.compile(r"^/lists/[a-z_-]{1,20}$"), "My lists", ""),
    (re.compile(r"^/collection$"), "Binder", "/collection"),
)
LOADING_TEXT = "Reading this page from Archidekt…"
FAILED_TEXT = "This page could not be loaded."


def late_page_of(path: str) -> tuple[str, str] | None:
    for pattern, title, current in _PAGES:
        if pattern.match(path):
            return title, current
    return None


@dataclass
class _Late:
    owner: str  # fingerprint of the session cookie
    url: str
    task: asyncio.Task[list[Message] | None]
    started: float = field(default_factory=time.monotonic)
    done_at: float | None = None


class LateStore:
    """The pages under way or finished, by key, and the finished ones by (session, address)."""

    def __init__(self) -> None:
        self.by_key: dict[str, _Late] = {}

    def prune(self, now: float) -> None:
        for key, late in list(self.by_key.items()):
            if late.done_at is not None and now - late.done_at > LATE_KEEP:
                del self.by_key[key]
        while len(self.by_key) > LATE_MAX:
            oldest = min(self.by_key, key=lambda k: self.by_key[k].started)
            self.by_key.pop(oldest)

    def finished(self, owner: str, url: str) -> list[Message] | None:
        """The finished page for this session and address, served once; None when there is none."""
        for key, late in list(self.by_key.items()):
            if late.owner == owner and late.url == url and late.task.done():
                del self.by_key[key]
                if late.task.cancelled() or late.task.exception() is not None:
                    return None
                return late.task.result()
        return None


def _owner(scope: Scope, cookie: str) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            for part in v.decode("latin-1").split(";"):
                name, _, val = part.strip().partition("=")
                if name == cookie and val:
                    return hashlib.sha256(val.strip().encode()).hexdigest()[:32]
    return None


def _wants_html(scope: Scope) -> bool:
    """A browser loading the page (not a script fetching it)."""
    headers = dict(scope.get("headers", []))
    return b"text/html" in headers.get(b"accept", b"") and b"x-requested-with" not in headers


def _url(scope: Scope) -> str:
    query = scope.get("query_string", b"").decode("latin-1")
    return str(scope.get("path", "")) + (f"?{query}" if query else "")


class LatePageMiddleware:
    """See the module docstring. Inside the membership check and the theme's context, so the
    member is known and the frame is drawn in their theme."""

    def __init__(self, app: ASGIApp, state: Any, *, after: float | None = None):
        self.app = app
        self.state = state
        self.after = LATE_AFTER if after is None else after
        self.store: LateStore = state.late_pages

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "GET":
            await self.app(scope, receive, send)
            return
        page = late_page_of(str(scope.get("path", "")))
        from .auth_provider import cookie_name
        from .pages import SESSION_COOKIE

        owner = _owner(scope, cookie_name(SESSION_COOKIE, self.state.settings)) if page else None
        if page is None or owner is None or not _wants_html(scope):
            await self.app(scope, receive, send)
            return
        url = _url(scope)
        now = time.monotonic()
        self.store.prune(now)
        kept = self.store.finished(owner, url)
        if kept is not None:
            for message in kept:
                await send(message)
            return
        messages: list[Message] = []

        async def capture(message: Message) -> None:
            messages.append(message)

        async def quiet_receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        started = time.perf_counter()

        async def run() -> list[Message] | None:
            await self.app(scope, quiet_receive, capture)
            return messages

        # its own copy of the request's context, so its Archidekt time is its own to count
        task = asyncio.get_running_loop().create_task(run(), context=contextvars.copy_context())
        done, _pending = await asyncio.wait({task}, timeout=self.after)
        if done:
            for message in task.result() or []:
                await send(message)
            return
        key = secrets.token_urlsafe(16)
        late = _Late(owner, url, task)
        self.store.by_key[key] = late
        scope["mtg_late"] = True  # timing.py counts this view when its content is there

        def finished(t: asyncio.Task[list[Message] | None]) -> None:
            late.done_at = time.monotonic()
            status = 0
            if not t.cancelled() and t.exception() is None:
                status = next(
                    (int(m.get("status", 0)) for m in t.result() or [] if m["type"] == "http.response.start"),
                    0,
                )
            ctx = t.get_context()
            speed_log.add(
                PageTime(
                    time.time(),
                    logged_path(scope),
                    time.perf_counter() - started,
                    ctx.get(archidekt_time, 0.0),
                    ctx.get(pacer_time, 0.0),
                    int(ctx.get(archidekt_failures, 0.0)),
                    status or 500,
                )
            )

        task.add_done_callback(finished)
        response = self.frame(scope, page, key)
        await response(scope, receive, send)

    def frame(self, scope: Scope, page: tuple[str, str], key: str) -> Response:
        """The site's frame with a skeleton, while the page is read."""
        from .pages import _csrf, browser_session
        from .theme import render

        request = Request(scope)
        sub, sid = browser_session(self.state, request)
        s = self.state.settings
        user = self.state.db.get_user(sub) if sub else None
        admin = bool(sub and s.admin_group and s.admin_group in ((user or {}).get("groups") or []))
        title, current = page
        url = html.escape(_url(scope))
        body = (
            f"<div class='late' data-late='/late/{html.escape(key)}' data-href='{url}'>"
            f"<p class='late-note' role='status'>{html.escape(LOADING_TEXT)}</p>"
            "<div class='late-skel skeleton' aria-hidden='true'>" + "<span></span>" * 6 + "</div>"
            f"<noscript><p><a class='btn' href='{url}'>Show the page</a></p></noscript></div>"
        )
        resp = render(
            title,
            body,
            site=s.server_name,
            signed_in=sub is not None,
            csrf=_csrf(s, sid),
            admin=admin,
            sub=sub,
            current=current or None,
        )
        resp.headers["Cache-Control"] = "no-store"  # never kept: Back must not land on the frame
        return resp


def add_late_routes(server: Any, state: Any) -> None:
    @server.custom_route("/late/{key}", methods=["GET"], include_in_schema=False)
    async def late_wait(request: Request) -> Response:
        """Wait for a late page to finish: 200 when the page's address can be loaded again,
        404 when there is no such page for this session (load the address anyway)."""
        from .auth_provider import cookie_name
        from .pages import SESSION_COOKIE

        store: LateStore = state.late_pages
        late = store.by_key.get(request.path_params["key"])
        owner = _owner(request.scope, cookie_name(SESSION_COOKIE, state.settings))
        headers = {"Cache-Control": "no-store"}
        if late is None or owner is None or late.owner != owner:
            return JSONResponse({"ready": False, "gone": True}, 404, headers=headers)
        try:
            await asyncio.wait_for(asyncio.shield(late.task), LATE_WAIT)
        except TimeoutError:
            return JSONResponse({"ready": False}, 202, headers=headers)
        except Exception:  # noqa: BLE001, S110 - the page failed: loading its address shows why
            pass
        return JSONResponse({"ready": True}, headers=headers)
