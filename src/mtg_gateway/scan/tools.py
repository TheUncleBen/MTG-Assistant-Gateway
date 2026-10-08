"""MCP tools for the assistant path: resolve_cards and scan-session access."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from pydantic import Field

from ..schemas import change_for_assistant
from .service import ScanError, ScanService, parse_cards, parse_text

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer


def _sub() -> str:
    token = get_access_token()
    if token is None or not token.subject:
        raise RuntimeError("no authenticated user on this request")
    return token.subject


def _err(exc: ScanError) -> dict[str, Any]:
    return {"ok": False, "error": exc.kind, "message": str(exc)}


# Card fields the Scan page draws with (image links, the Scryfall page) and the assistant has no
# use for: left out of what the tools return, which keeps a card's printings list small.
_PAGE_ONLY = ("image_small", "image_normal", "image_art", "image_large", "scryfall_uri")


def _slim(card: Any) -> Any:
    if isinstance(card, dict):
        return {k: v for k, v in card.items() if k not in _PAGE_ONLY}
    return card


def _for_assistant(out: dict[str, Any]) -> dict[str, Any]:
    """A resolve or scan-session result in the assistant's shape: cards without the page-only
    fields, and ``changes`` in the documented spelling (name, set_code, finish)."""
    out = dict(out)
    for key in ("cards", "items"):
        if isinstance(out.get(key), list):
            out[key] = [
                {**it, "card": _slim(it.get("card"))} if isinstance(it, dict) and "card" in it else _slim(it)
                for it in out[key]
            ]
    if isinstance(out.get("changes"), list):
        out["changes"] = [change_for_assistant(c) for c in out["changes"]]
    if isinstance(out.get("change_batches"), list):
        out["change_batches"] = [[change_for_assistant(c) for c in b] for b in out["change_batches"]]
    return out


def add_scan_tools(server: MCPServer, service: ScanService) -> None:
    @server.tool(
        name="resolve_cards",
        title="Resolve card names to exact cards",
        description=(
            "Turn card names read from photos, a typed list or a decklist into exact Magic cards via "
            "Scryfall. Give either `cards` (list of {name, set?, collector_number?, quantity?}) or `text` "
            "(one card per line, e.g. '2 Sol Ring (CMR) 472'). Each result has a status: exact, printing "
            "(matched by set and number), fuzzy (name corrected; confirm with the user), ambiguous or "
            "not_found (with suggestions). Returns a decklist and `changes` ready for propose_deck_changes. "
            "Does not touch Archidekt."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def resolve_cards(
        cards: list[dict[str, Any] | str] | None = None, text: str | None = None
    ) -> dict[str, Any]:
        try:
            inputs = parse_cards(cards) if cards else parse_text(text or "")
            if not inputs:
                raise ScanError("invalid", "give `cards` or `text` with at least one card")
            results = await service.resolve(inputs, owner=_sub())
        except ScanError as exc:
            return _err(exc)
        return {"ok": True, **_for_assistant(service.describe(results))}

    @server.tool(
        name="card_printings",
        title="List the printings of a card",
        description=(
            "The printings of one Magic card (set, set name, collector number, release date, rarity, "
            "finishes), newest first, from Scryfall. Give `oracle_id` from a resolve_cards result, or "
            "`name`. Narrow it with `set_code` (e.g. cmr) and `finish` (foil or etched: printings that "
            "come in it); `limit` caps the rows (default 25; `matching` says how many matched). Use it to "
            "pick an exact printing or to tell foil-only printings apart. Does not touch Archidekt."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def card_printings(
        oracle_id: Annotated[str | None, Field(description="The card's Scryfall oracle id.")] = None,
        name: Annotated[str | None, Field(description="Or the card's name.")] = None,
        set_code: Annotated[str | None, Field(description="Only printings from this set, e.g. cmr.")] = None,
        finish: Annotated[
            Literal["nonfoil", "foil", "etched"] | None, Field(description="Only printings that come in it.")
        ] = None,
        limit: Annotated[int, Field(description="Rows to return, 1 to 175.")] = 25,
    ) -> dict[str, Any]:
        try:
            if not oracle_id:
                if not name:
                    raise ScanError("invalid", "give oracle_id or name")
                results = await service.resolve(parse_cards([{"name": name}]), owner=_sub())
                card = results[0].card if results else None
                if not card or not card.get("oracle_id"):
                    raise ScanError("not_found", f"no card found for '{name}'")
                oracle_id = str(card["oracle_id"])
            out = await service.printings(oracle_id, owner=_sub())
        except ScanError as exc:
            return _err(exc)
        cards = [c for c in out.get("cards", []) if isinstance(c, dict)]
        if set_code:
            want = set_code.strip().lower()
            cards = [c for c in cards if str(c.get("set") or "").lower() == want]
        if finish:
            cards = [c for c in cards if finish in (c.get("finishes") or [])]
        shown = max(1, min(int(limit or 25), 175))
        return {
            "ok": True,
            "oracle_id": oracle_id,
            "matching": len(cards),
            "cards": [_slim(c) for c in cards[:shown]],
            "truncated": len(cards) > shown,
            # Scryfall shows 175 printings a page; past that, filters only see the newest 175
            "has_more": bool(out.get("has_more")),
            "total_cards": out.get("total_cards"),
        }

    @server.tool(
        name="save_scan_session",
        title="Save a scan session",
        description=(
            "Resolve cards (same input as resolve_cards) and store them as a named scan session for the "
            "signed-in user, so they can be reviewed at the gateway's /scan page or fetched later with "
            "get_scan_session. Nothing is sent to Archidekt."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
    )
    async def save_scan_session(
        name: str, cards: list[dict[str, Any] | str] | None = None, text: str | None = None
    ) -> dict[str, Any]:
        try:
            inputs = parse_cards(cards) if cards else parse_text(text or "")
            if not inputs:
                raise ScanError("invalid", "give `cards` or `text` with at least one card")
            results = await service.resolve(inputs, owner=_sub())
            items = [
                {
                    "quantity": r.input.quantity,
                    "name": (r.card or {}).get("name") or r.input.name,
                    "status": r.status,
                    "note": r.note,
                    "card": r.card,
                }
                for r in results
            ]
            session = service.new_session(_sub(), name, items, source="assistant")
        except ScanError as exc:
            return _err(exc)
        return {"ok": True, **_for_assistant(session)}

    @server.tool(
        name="list_scan_sessions",
        title="List my scan sessions",
        description=(
            "List the signed-in user's card scan sessions (from the gateway's /scan page or "
            "save_scan_session), newest first, with card counts and how many cards still need review."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def list_scan_sessions() -> dict[str, Any]:
        return {"ok": True, "sessions": service.list_sessions(_sub())}

    @server.tool(
        name="get_scan_session",
        title="Get a scan session",
        description=(
            "Fetch one of the signed-in user's scan sessions by id (scan_…) or by name. Returns the "
            "resolved cards, a decklist text and `changes` (add actions) that can be passed to "
            "propose_deck_changes for a deck the user owns. Items without a `card` were not resolved; "
            "ask the user about them."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def get_scan_session(session: str) -> dict[str, Any]:
        try:
            return {"ok": True, **_for_assistant(service.find_session(_sub(), session))}
        except ScanError as exc:
            return _err(exc)
