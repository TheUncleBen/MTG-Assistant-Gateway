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
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("mtg_gateway.requests")

archidekt_time: ContextVar[float] = ContextVar("archidekt_time", default=0.0)
idp_time: ContextVar[float] = ContextVar("idp_time", default=0.0)

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
            if not raw.startswith(_QUIET_PREFIXES):
                path = logged_path(scope)
                log.info(
                    "%s %s %d %.0fms archidekt=%.0fms idp=%.0fms",
                    scope.get("method", "-"),
                    path,
                    status["code"],
                    (time.perf_counter() - started) * 1000,
                    archidekt_time.get() * 1000,
                    idp_time.get() * 1000,
                )
