"""Pages go out compressed (Ben, 25 Oct 2026: "everything seems slow"): page HTML is mostly repeated
markup and shrinks several times over, which matters on mobile data.

Only a browser's page loads are compressed: a GET that asks for ``text/html``, outside the
sign-in's pages. A script's JSON, an assistant's MCP answers and the OAuth endpoints go out as
before. The Accept header is a speed rule, not a security boundary (a cross-site request can send
it). What keeps a compressed page from leaking its form token (BREACH) is that the token is masked
with fresh randomness on every load (masked.py). Static files have their own gzipped copies
(companion._static_payload) and pass through untouched (Starlette's GZipMiddleware leaves a
response that already has a Content-Encoding)."""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# Level 6: nearly the size of 9 for a fraction of the CPU on a Raspberry Pi.
LEVEL = 6
# The sign-in's pages carry one-time values (the consent form's token, the Android app's
# hand-back code) and are small: never compressed. Neither are the Account pages: inside the app
# Account details carries the member's notification topic, a lasting secret that is not masked.
UNCOMPRESSED = ("/login", "/logout", "/auth/", "/authorize", "/account")


class PageGzip:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=1024, compresslevel=LEVEL)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope.get("method") == "GET"
            and not str(scope.get("path", "")).startswith(UNCOMPRESSED)
        ):
            if "text/html" in Headers(scope=scope).get("accept", ""):
                await self.gzip(scope, receive, send)
                return
        await self.app(scope, receive, send)
