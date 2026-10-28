"""Request timing: one log line per request and a ``Server-Timing`` header.

The gateway runs with uvicorn's access log off, so until now nothing said how long a page took
or where the time went. :class:`TimingMiddleware` is the outermost ASGI layer: it measures the
whole request, logs ``method route status ms`` at INFO and adds a ``Server-Timing`` header a
browser's network panel shows next to the request, with
``total``, ``archidekt`` (time this request spent awaiting Archidekt, pacer waits included) and
``idp`` (time spent asking the identity provider whether the member is still allowed in).

The log line names the route, not the request: ``/decks/{deck_id}``, ``/users/{username}``,
``/cards/data/{token}``, so a deck number, an Archidekt username, a signed link's token or a
member's user ID never reaches the log (nor does the query string or the member). A request no
route answers (a 404) is logged as its first path segment only. This is what the Archidekt link
disclosure and OPERATIONS.md promise about the request log.

The two partial times are context variables that :mod:`archidekt` and :mod:`membership` add to;
a background task started from a request copies the context, so what it awaits later is not
counted against the request that started it.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("mtg_gateway.requests")

archidekt_time: ContextVar[float] = ContextVar("archidekt_time", default=0.0)
idp_time: ContextVar[float] = ContextVar("idp_time", default=0.0)
# Of archidekt_time: the time spent waiting for a turn in the shared Pacer (archidekt.Pacer.enter),
# and the Archidekt calls that failed (an error answer, a timeout, a pause that refused it).
pacer_time: ContextVar[float] = ContextVar("pacer_time", default=0.0)
archidekt_failures: ContextVar[float] = ContextVar("archidekt_failures", default=0.0)

# A request that takes longer than this is logged again at WARNING with its time split, so a slow
# page stands out in a log kept at WARNING.
SLOW_REQUEST_SECONDS = 3.0
# Noise the log line would add nothing to; the header is still set.
_QUIET_PREFIXES = ("/static/", "/scan/static/", "/healthz")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def logged_path(scope: Scope) -> str:
    """What the log line says for a request: the matched route's template, never the raw path.

    After routing the scope carries the ``Route`` that answered (its ``path`` is the template with
    ``{param}`` placeholders) and ``root_path`` (a mount's prefix). Without a match, the first
    path segment and ``/…`` stand in, so an unrouted request cannot write a secret into the log.
    """
    root = str(scope.get("root_path") or "")
    template = getattr(scope.get("route"), "path", None)
    if isinstance(template, str) and template:
        if root and template == root:
            return _CONTROL.sub("?", root)[:300] + "/…"  # a Mount answered: its prefix, no more
        return _CONTROL.sub("?", root + template)[:300]
    raw = _CONTROL.sub("?", str(scope.get("path", "")))
    first, _sep, rest = raw.lstrip("/").partition("/")
    return "/" + first[:80] + ("/…" if rest else "")


def add_time(var: ContextVar[float], seconds: float) -> None:
    """Add ``seconds`` to a request's running total for ``var`` (a no-op outside a request)."""
    var.set(var.get() + seconds)


# -- Admin's "Speed, last 24 h" card (owner decision, 0.7.46) -------------------------------------
SPEED_WINDOW = 24 * 3600.0
SPEED_MAX = 20000  # page views kept at most, the oldest go first
# Requests that are not a page a member waits for: files, the JSON and programs' paths, the
# late page's own wait (latepage.py), and the small counts every page asks for.
_NOT_PAGES = (
    "/static/",
    "/scan/static/",
    "/healthz",
    "/api/",
    "/mcp",
    "/oauth",
    "/.well-known",
    "/late/",
    "/notifications/count",
    "/watchlist/api/",
    "/sw.js",
    "/favicon",
)


@dataclass
class PageTime:
    """One page view's time, without the member, the query or any id (the route template)."""

    at: float  # wall clock, for the 24 h window
    route: str
    seconds: float  # until the page's content was there (a late page: its fill)
    archidekt: float  # awaiting Archidekt in all, pacer waits included
    pacer: float  # of it, waiting for a turn in the shared pacer
    failures: int  # Archidekt calls that failed
    status: int


class SpeedLog:
    """The page views of the last SPEED_WINDOW, in memory (a restart starts it again)."""

    def __init__(self) -> None:
        self._rows: deque[PageTime] = deque(maxlen=SPEED_MAX)
        self.started = time.time()

    def add(self, row: PageTime) -> None:
        self._rows.append(row)

    def rows(self, now: float | None = None) -> list[PageTime]:
        cut = (time.time() if now is None else now) - SPEED_WINDOW
        while self._rows and self._rows[0].at < cut:
            self._rows.popleft()
        return list(self._rows)

    def summary(self, now: float | None = None, *, slow: float = SLOW_REQUEST_SECONDS) -> dict[str, Any]:
        """Totals and the routes, those with the most slow views first, with where their time went."""
        rows = self.rows(now)
        by_route: dict[str, list[PageTime]] = {}
        for r in rows:
            by_route.setdefault(r.route, []).append(r)
        routes = []
        for route, rs in by_route.items():
            times = sorted(r.seconds for r in rs)
            p95 = times[min(len(times) - 1, int(round(0.95 * (len(times) - 1))))]
            routes.append(
                {
                    "route": route,
                    "views": len(rs),
                    "slow": sum(1 for r in rs if r.seconds > slow),
                    "p95": p95,
                    "max": times[-1],
                    "pacer": sum(r.pacer for r in rs) / len(rs),
                    "archidekt": sum(max(0.0, r.archidekt - r.pacer) for r in rs) / len(rs),
                    "failures": sum(r.failures for r in rs),
                }
            )
        routes.sort(key=lambda r: (r["slow"], r["max"]), reverse=True)  # the most slow views first
        return {
            "views": len(rows),
            "slow": sum(1 for r in rows if r.seconds > slow),
            "pacer": sum(r.pacer for r in rows),
            "archidekt": sum(max(0.0, r.archidekt - r.pacer) for r in rows),
            "failures": sum(r.failures for r in rows),
            "routes": routes,
            "since": max(self.started, (time.time() if now is None else now) - SPEED_WINDOW),
        }


speed_log = SpeedLog()


def is_page(scope: Scope) -> bool:
    """Whether a request is a page view a member waits for (a GET of HTML, not a file or an API)."""
    raw = str(scope.get("path", ""))
    return scope.get("method") == "GET" and not raw.startswith(_NOT_PAGES)


class TimingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        archidekt_time.set(0.0)
        idp_time.set(0.0)
        pacer_time.set(0.0)
        archidekt_failures.set(0.0)
        status = {"code": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = int(message.get("status", 0))
                headers = list(message.get("headers", []))
                total = (time.perf_counter() - started) * 1000
                value = (
                    f"total;dur={total:.1f}, archidekt;dur={archidekt_time.get() * 1000:.1f}, "
                    f"idp;dur={idp_time.get() * 1000:.1f}"
                )
                headers.append((b"server-timing", value.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            raw = str(scope.get("path", ""))
            # a late page (latepage.py) is counted once its content is there, by the page's fill
            if is_page(scope) and status["code"] and not scope.get("mtg_late"):
                speed_log.add(
                    PageTime(
                        time.time(),
                        logged_path(scope),
                        time.perf_counter() - started,
                        archidekt_time.get(),
                        pacer_time.get(),
                        int(archidekt_failures.get()),
                        status["code"],
                    )
                )
            if not raw.startswith(_QUIET_PREFIXES):
                path = logged_path(scope)
                total = time.perf_counter() - started
                log.info(
                    "%s %s %d %.0fms archidekt=%.0fms idp=%.0fms",
                    scope.get("method", "-"),
                    path,
                    status["code"],
                    total * 1000,
                    archidekt_time.get() * 1000,
                    idp_time.get() * 1000,
                )
                if total > SLOW_REQUEST_SECONDS:
                    log.warning(
                        "slow request: %s %s %d total=%.0fms archidekt=%.0fms idp=%.0fms",
                        scope.get("method", "-"),
                        path,
                        status["code"],
                        total * 1000,
                        archidekt_time.get() * 1000,
                        idp_time.get() * 1000,
                    )
