"""Request timing: one log line per request and a ``Server-Timing`` header.

The gateway runs with uvicorn's access log off, so until now nothing said how long a page took
or where the time went. :class:`TimingMiddleware` is the outermost ASGI layer: it measures the
whole request, logs ``method path status ms`` at INFO (no query string, no user id) and adds a
``Server-Timing`` header a browser's network panel shows next to the request, with
``total``, ``archidekt`` (time this request spent awaiting Archidekt, pacer waits included) and
``idp`` (time spent asking the identity provider whether the member is still allowed in).

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
            # the decoded path is a client's string: one line per request, whatever it holds
            path = re.sub(r"[\x00-\x1f\x7f]", "?", str(scope.get("path", "")))[:300]
            if not path.startswith(_QUIET_PREFIXES):
                log.info(
                    "%s %s %d %.0fms archidekt=%.0fms idp=%.0fms",
                    scope.get("method", "-"),
                    path,
                    status["code"],
                    (time.perf_counter() - started) * 1000,
                    archidekt_time.get() * 1000,
                    idp_time.get() * 1000,
                )
