"""Allowlisted proxy for Mystic Forge tools.

The gateway lists an explicit set of Mystic Forge tools as its own and forwards
calls to the internal Mystic Forge MCP endpoint. Stateful or passphrase-keyed
tools (watchlists, stored goldfish reports, interactive games, price history)
are not exposed until the gateway can record ownership for them.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import anyio
import mcp_types as types
from mcp import Client
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.context import CallNext, ServerRequestContext

logger = logging.getLogger(__name__)

ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "scryfall_search",
        "scryfall_named",
        "scryfall_random",
        "scryfall_price",
        "scryfall_price_list",
        "scryfall_card_text",
        "scryfall_rulings",
        "edhrec_commander",
        "edhrec_average_deck",
        "edhrec_combos",
        "edhrec_top_cards",
        "edhrec_recommendations",
        "edhrec_salt",
        "edhrec_precon_upgrade",
        "archidekt_deck",
        "archidekt_user_decks",
        "archidekt_export",
        "format_archidekt",
        "validate_decklist",
        "validate_archidekt_deck",
        "spellbook_combos",
        "spellbook_card_combos",
        "rules_get",
        "rules_search",
        "precon_search",
        "precon_decklist",
        "precon_export",
        "precon_diff",
        "goldfish_odds",
        "goldfish_annotate",
        "goldfish_run",
        "goldfish_ab",
    }
)

BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        "goldfish_report",
        "goldfish_start",
        "goldfish_step",
        "goldfish_state",
        "watchlist_create",
        "watchlist_add",
        "watchlist_bulk_add",
        "watchlist_remove",
        "watchlist_list",
        "watchlist_report",
        "watchlist_view",
        "watchlist_history",
        "watchlist_clone",
        "price_history",
    }
)


# One member may run this many proxied calls at once; another is refused at once ("busy")
# rather than queued, so one account cannot hold Mystic Forge's simulation slots for everyone.
MAX_CALLS_PER_USER = 2
# Every proxied call is cut off after this long (a goldfish run included), so no call holds a
# slot for hours. Mystic Forge itself may keep working on a cancelled call until it finishes.
CALL_DEADLINE_SECONDS = 120.0
# Listing Mystic Forge's tools is quick when it is up. When it hangs, give up after this long and
# serve the gateway's own tools (plus any cached research tools), then wait before asking again.
LIST_DEADLINE_SECONDS = 10.0
LIST_RETRY_SECONDS = 60.0


BUSY_PREFIX = "busy: "


def _busy() -> types.CallToolResult:
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=f"{BUSY_PREFIX}{MAX_CALLS_PER_USER} research calls for your account are still running; "
                "wait for one to finish and try again",
            )
        ],
        isError=True,
    )


def is_busy(result: types.CallToolResult) -> bool:
    """True for the refusal ``call`` gives when the owner already runs their share of calls."""
    if not result.is_error or not result.content:
        return False
    first = result.content[0]
    return getattr(first, "type", "") == "text" and str(getattr(first, "text", "")).startswith(BUSY_PREFIX)


class MysticForgeProxy:
    def __init__(
        self,
        url: str,
        *,
        cache_ttl: float = 300.0,
        client_factory: Any | None = None,
        max_calls_per_user: int = MAX_CALLS_PER_USER,
        deadline: float = CALL_DEADLINE_SECONDS,
    ):
        self.url = url
        self.cache_ttl = cache_ttl
        self.max_calls_per_user = max_calls_per_user
        self.deadline = deadline
        self._in_flight: dict[str, int] = {}
        self._client_factory = client_factory or (lambda: Client(url))
        self._tools: list[dict[str, Any]] = []
        self._tools_at = 0.0
        self._known: set[str] = set()
        self._retry_at = 0.0

    async def tools(self, *, force: bool = False) -> list[dict[str, Any]]:
        if self._tools and not force and time.time() - self._tools_at < self.cache_ttl:
            return self._tools
        if not force and time.time() < self._retry_at:
            return self._tools  # it failed moments ago; don't make every tools/list wait again
        try:
            with anyio.fail_after(LIST_DEADLINE_SECONDS):
                async with self._client_factory() as client:
                    listing = await client.list_tools()
        except Exception as exc:  # includes TimeoutError: a hung Mystic Forge must not stall tools/list
            logger.warning("Mystic Forge tool listing failed: %s", str(exc) or type(exc).__name__)
            self._retry_at = time.time() + LIST_RETRY_SECONDS
            return self._tools  # stale list, or empty if never reached
        tools: list[dict[str, Any]] = []
        for t in listing.tools:
            if t.name in ALLOWED_TOOLS:
                d = t.model_dump(by_alias=True, exclude_none=True, mode="json")
                # Advertise as read-only research tools; none of them writes to Archidekt.
                d.setdefault("annotations", {}).update({"readOnlyHint": True, "openWorldHint": True})
                tools.append(d)
        skipped = {t.name for t in listing.tools} - ALLOWED_TOOLS
        if skipped - BLOCKED_TOOLS:
            logger.info(
                "Mystic Forge exposes tools not in either list (left hidden): %s",
                sorted(skipped - BLOCKED_TOOLS),
            )
        self._tools, self._tools_at = tools, time.time()
        self._known = {t["name"] for t in tools}
        return tools

    def is_proxied(self, name: str) -> bool:
        return name in ALLOWED_TOOLS

    async def call(
        self, name: str, arguments: dict[str, Any] | None, *, owner: str | None = None
    ) -> types.CallToolResult:
        """Forward one call. With ``owner`` set, at most ``max_calls_per_user`` run at once for
        that account; every call is stopped after ``deadline`` seconds."""
        if name not in ALLOWED_TOOLS:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"tool {name} is not available")], isError=True
            )
        if owner is not None:
            if self._in_flight.get(owner, 0) >= self.max_calls_per_user:
                return _busy()
            self._in_flight[owner] = self._in_flight.get(owner, 0) + 1
        try:
            return await self._forward(name, arguments)
        finally:
            if owner is not None:
                left = self._in_flight.get(owner, 1) - 1
                if left > 0:
                    self._in_flight[owner] = left
                else:
                    self._in_flight.pop(owner, None)

    async def _forward(self, name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        try:
            with anyio.fail_after(self.deadline):
                async with self._client_factory() as client:
                    return await client.call_tool(name, arguments or {})
        except TimeoutError:
            logger.warning("Mystic Forge call %s passed the %.0f s deadline", name, self.deadline)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"the research call took longer than {self.deadline:.0f} s and was stopped; "
                        "try a smaller request",
                    )
                ],
                isError=True,
            )
        except Exception as exc:
            logger.warning("Mystic Forge call %s failed: %s", name, exc)
            return types.CallToolResult(
                content=[
                    types.TextContent(type="text", text="the research service is unavailable right now")
                ],
                isError=True,
            )

    def middleware(self):
        """MCP server middleware that merges proxied tools into tools/list and routes tools/call."""

        async def _mw(ctx: ServerRequestContext[Any, Any], call_next: CallNext):
            if ctx.method == "tools/list":
                result = await call_next(ctx)
                tools = await self.tools()
                if tools and isinstance(result, dict):
                    local = {t.get("name") for t in result.get("tools", [])}
                    result = dict(result)
                    result["tools"] = list(result.get("tools", [])) + [
                        t for t in tools if t["name"] not in local
                    ]
                return result
            if ctx.method == "tools/call" and isinstance(ctx.params, dict):
                name = ctx.params.get("name")
                if isinstance(name, str) and self.is_proxied(name):
                    token = get_access_token()
                    owner = token.subject if token is not None and token.subject else None
                    out = await self.call(name, ctx.params.get("arguments"), owner=owner)
                    return out.model_dump(by_alias=True, exclude_none=True, mode="json")
            return await call_next(ctx)

        return _mw


__all__ = ["ALLOWED_TOOLS", "BLOCKED_TOOLS", "MysticForgeProxy", "is_busy"]
