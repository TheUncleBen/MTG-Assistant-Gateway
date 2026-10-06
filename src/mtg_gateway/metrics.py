"""Per-day usage counters: tool calls, errors, API calls, proposals, applies, scans.

One row per (UTC day, user, kind, name) in the ``metrics`` table, bumped with a
single UPSERT. The admin pages read the totals and 30-day series; nothing else
depends on these counts, so a failure to record one is logged and swallowed.
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
from collections.abc import Callable
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.context import CallNext, ServerRequestContext

from .db import Database

logger = logging.getLogger(__name__)


class Metrics:
    def __init__(self, db: Database, known_tool: Callable[[str], bool] | None = None):
        self.db = db
        # Names a client made up must not each become a row: they collapse to "(unknown)".
        self.known_tool = known_tool

    @staticmethod
    def today() -> str:
        return _dt.datetime.now(_dt.UTC).date().isoformat()

    def record(self, kind: str, name: str, sub: str | None = None, n: int = 1) -> None:
        """Add ``n`` to today's counter for (kind, name) of ``sub`` (None: not tied to a user)."""
        try:
            self.db.metrics_increment(self.today(), sub, kind, name, n)
        except sqlite3.Error as exc:  # a counter is never worth failing the request over
            logger.warning("metrics: could not record %s/%s: %s", kind, name, exc)

    def mcp_middleware(self):
        """MCP server middleware: one ``tool`` count per tools/call for the calling user, and an
        ``error`` count when the tool reports isError or raises. Append it to ``server.middleware``
        before any middleware that answers tools/call itself (the Mystic Forge proxy), so proxied
        calls are counted too."""

        async def _mw(ctx: ServerRequestContext[Any, Any], call_next: CallNext):
            if ctx.method != "tools/call" or not isinstance(ctx.params, dict):
                return await call_next(ctx)
            raw = ctx.params.get("name")
            name = raw if isinstance(raw, str) and raw else "(unnamed)"
            if self.known_tool is not None and not self.known_tool(name):
                name = "(unknown)"
            token = get_access_token()
            sub = token.subject if token is not None else None
            self.record("tool", name, sub)
            try:
                result = await call_next(ctx)
            except Exception:
                self.record("error", name, sub)
                raise
            if isinstance(result, dict) and result.get("isError"):
                self.record("error", name, sub)
            return result

        return _mw


__all__ = ["Metrics"]
