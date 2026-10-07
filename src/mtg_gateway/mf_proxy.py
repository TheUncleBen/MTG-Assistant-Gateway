"""Allowlisted proxy for Mystic Forge tools.

The gateway lists an explicit set of Mystic Forge tools as its own and forwards
calls to the internal Mystic Forge MCP endpoint. Stateful or passphrase-keyed
tools (watchlists, stored goldfish reports, interactive games, price history)
are not exposed until the gateway can record ownership for them.
"""

from __future__ import annotations

import json
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
        "format_archidekt",
        "validate_decklist",
        "spellbook_combos",
        "spellbook_card_combos",
        "rules_get",
        "rules_search",
        "precon_search",
        "precon_decklist",
        "precon_export",
        "goldfish_odds",
        "goldfish_annotate",
    }
)

# One owner per capability. These Mystic Forge tools do what a gateway tool already does, so they
# are neither listed nor callable by an assistant: the gateway tool named here owns the job, and
# an assistant that asks for one of these is told which tool to call instead. The gateway itself
# still runs goldfish_run inside run_deck_report (``call(..., internal=True)``).
OWNED_ELSEWHERE: dict[str, str] = {
    "goldfish_run": "run_deck_report",
    "goldfish_ab": "run_deck_report",
    "archidekt_deck": "get_deck",
    "archidekt_export": "get_deck",
    "archidekt_user_decks": "list_my_decks",
    "validate_archidekt_deck": "deck_stats",
    "precon_diff": "compare_decks",
}
# What each owner does, for the refusal an assistant gets when it calls the hidden duplicate.
OWNER_NOTES: dict[str, str] = {
    "run_deck_report": "the gateway's simulation: it reads the deck, validates it, runs the goldfish games "
    "and stores the report; run it twice to compare two versions",
    "get_deck": "the gateway's deck reader: any Archidekt deck by id or link, with decklist_text to export",
    "list_my_decks": "the signed-in member's decks; archidekt_user lists another user's public decks",
    "deck_stats": "legality, bracket, curve, colours and price from one read",
    "compare_decks": "the exact adds and cuts between two decks, snapshots or lists",
}

BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        *OWNED_ELSEWHERE,
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


# Bounds on the arguments of proxied calls, checked before anything is forwarded. Numbers of the
# simulation tools (goldfish_*): games as run_deck_report allows (reports.MAX_GAMES), turns, and
# the sizes of goldfish_odds. Any text argument (a decklist) up to the gateway's own decklist
# limit, and the arguments as a whole up to MAX_ARGUMENT_BYTES.
GOLDFISH_LIMITS: dict[str, int] = {
    "n": 2000,
    "until_turn": 30,
    "deck_size": 1000,
    "draws": 1000,
    "copies": 1000,
    "min_successes": 1000,
}
MAX_TEXT_ARGUMENT = 200_000
MAX_ARGUMENT_BYTES = 1_000_000
# Proxied tools that read from Archidekt (through Mystic Forge, outside the gateway's pacer): they
# draw on the member's Archidekt budget (decks.DeckService.budget_refusal). A goldfish or precon
# tool given an Archidekt deck id or link reads Archidekt as well.
ARCHIDEKT_TOOLS = frozenset(
    {"archidekt_deck", "archidekt_user_decks", "archidekt_export", "validate_archidekt_deck"}
)
DECK_ARGUMENTS = ("deck", "deck_a", "deck_b")

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


def _refusal(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)


def argument_problem(name: str, arguments: dict[str, Any] | None) -> str | None:
    """Why ``arguments`` are out of bounds for proxied tool ``name`` (see GOLDFISH_LIMITS), or None."""
    try:
        size = len(json.dumps(arguments or {}))
    except (TypeError, ValueError):
        return "the arguments could not be read"
    if size > MAX_ARGUMENT_BYTES:
        return f"the arguments may be at most {MAX_ARGUMENT_BYTES // 1000} kB"

    def walk(value: Any, depth: int = 0) -> str | None:
        if depth > 4:
            return None
        if isinstance(value, str) and len(value) > MAX_TEXT_ARGUMENT:
            return f"a text argument may be at most {MAX_TEXT_ARGUMENT // 1000} kB"
        if isinstance(value, list):
            for item in value:
                if problem := walk(item, depth + 1):
                    return problem
        if isinstance(value, dict):
            for key, item in value.items():
                limit = GOLDFISH_LIMITS.get(key) if name.startswith("goldfish_") else None
                if limit is not None and not isinstance(item, bool):
                    number: float | None = None
                    if isinstance(item, int | float):
                        number = item
                    elif isinstance(item, str):
                        try:
                            number = float(item.strip())
                        except ValueError:
                            number = None
                    if number is not None and number > limit:
                        return f"{key} may be at most {limit}"
                if problem := walk(item, depth + 1):
                    return problem
        return None

    return walk(arguments or {})


def reads_archidekt(name: str, arguments: dict[str, Any] | None) -> bool:
    """Whether proxied tool ``name`` reaches Archidekt with these arguments."""
    if name in ARCHIDEKT_TOOLS:
        return True
    for key in DECK_ARGUMENTS:
        value = (arguments or {}).get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return True
        if isinstance(value, str) and (value.strip().isdigit() or "archidekt.com" in value.lower()):
            return True
    return False


def owned_elsewhere_text(name: str) -> str | None:
    """The refusal for a hidden duplicate: which gateway tool to call instead, or None."""
    owner = OWNED_ELSEWHERE.get(name)
    if owner is None:
        return None
    return (
        f"{name} is not offered here: {owner} owns this on the gateway ({OWNER_NOTES.get(owner, '')}). "
        f"Call {owner} instead."
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
        # Set by the app: spends one unit of a member's Archidekt budget, returning the refusal
        # text when it is used up (None to go ahead).
        self.archidekt_budget: Any = None

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
        """Calls the proxy answers itself: the allowed tools, and the hidden duplicates (answered
        with the name of the gateway tool that owns the job)."""
        return name in ALLOWED_TOOLS or name in OWNED_ELSEWHERE

    async def call(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        *,
        owner: str | None = None,
        internal: bool = False,
    ) -> types.CallToolResult:
        """Forward one call. With ``owner`` set, at most ``max_calls_per_user`` run at once for
        that account; every call is stopped after ``deadline`` seconds. ``internal`` is for the
        gateway's own tools (run_deck_report), which may use a Mystic Forge tool that assistants
        reach only through them (OWNED_ELSEWHERE)."""
        if name not in ALLOWED_TOOLS and not (internal and name in OWNED_ELSEWHERE):
            return _refusal(owned_elsewhere_text(name) or f"tool {name} is not available")
        problem = argument_problem(name, arguments)
        if problem:
            return _refusal(f"{name} refused: {problem}; nothing was sent to the research service")
        if owner is not None and self.archidekt_budget is not None and reads_archidekt(name, arguments):
            refused = self.archidekt_budget(owner)
            if refused:
                return _refusal(refused)
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


__all__ = ["ALLOWED_TOOLS", "BLOCKED_TOOLS", "OWNED_ELSEWHERE", "MysticForgeProxy", "is_busy"]
