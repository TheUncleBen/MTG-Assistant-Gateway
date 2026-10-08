"""MCP tools for the assistant path: resolve_cards and scan-session access."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import Context
from mcp_types import CallToolResult
from pydantic import Field

from ..cards import PICKER_CARD_URI, PRINTINGS_CARD_URI, ask_choices, tool_meta, with_card
from ..schemas import change_for_assistant
from .service import ScanError, ScanService, parse_cards, parse_text

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from ..cards import CardLinks

CARD_FIELDS = (
    "name",
    "set",
    "set_name",
    "collector_number",
    "scryfall_id",
    "image_small",
    "finishes",
    "scryfall_uri",
)


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


def _picker_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the picker card draws per row: the input, the status and the card's identity and
    small picture; nothing the assistant's copy does not already say."""
    rows = []
    for it in items:
        card = it.get("card") or None
        rows.append(
            {
                "input_name": (it.get("input") or {}).get("name"),
                "quantity": it.get("quantity"),
                "status": it.get("status"),
                "note": it.get("note"),
                "suggestions": list(it.get("suggestions") or [])[:6],
                "foil": it.get("foil"),
                "card": {k: card.get(k) for k in CARD_FIELDS if card.get(k) is not None} if card else None,
            }
        )
    return rows


def summarize_sets(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per set a card was printed in (newest first): what the assistant names when the
    printings card carries the pictures, so it never has to list every printing."""
    sets: dict[str, dict[str, Any]] = {}
    for c in cards:
        code = str(c.get("set") or "").lower()
        row = sets.setdefault(
            code,
            {
                "set": code,
                "set_name": c.get("set_name"),
                "printings": 0,
                "newest": c.get("released_at"),
                "finishes": [],
            },
        )
        row["printings"] += 1
        for f in c.get("finishes") or []:
            if f not in row["finishes"]:
                row["finishes"].append(f)
    return sorted(sets.values(), key=lambda r: str(r.get("newest") or ""), reverse=True)


def add_scan_tools(server: MCPServer, service: ScanService, *, links: CardLinks | None = None) -> None:
    @server.tool(
        name="resolve_cards",
        title="Resolve card names to exact cards",
        description=(
            "Turn card names read from photos, a typed list or a decklist into exact Magic cards via "
            "Scryfall. Give either `cards` (list of {name, set?, collector_number?, quantity?}) or `text` "
            "(one card per line, e.g. '2 Sol Ring (CMR) 472'). Each result has a status: exact, printing "
            "(matched by set and number), fuzzy (name corrected; confirm with the user), ambiguous or "
            "not_found (with suggestions). Returns a decklist and `changes` ready for propose_deck_changes. "
            "In an app that shows cards, the user sees the recognised cards with pictures, can drop some, "
            "pick among suggestions and confirm; wait for that before proposing. "
            "Does not touch Archidekt."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
        meta=tool_meta(PICKER_CARD_URI) if links is not None else None,
    )
    async def resolve_cards(
        ctx: Context,
        cards: list[dict[str, Any] | str] | None = None,
        text: str | None = None,
    ) -> CallToolResult:
        try:
            inputs = parse_cards(cards) if cards else parse_text(text or "")
            if not inputs:
                raise ScanError("invalid", "give `cards` or `text` with at least one card")
            results = await service.resolve(inputs, owner=_sub())
            results = await _ask_about_ambiguous(ctx, results)
        except ScanError as exc:
            return with_card(_err(exc), None)
        described = service.describe(results)
        out = {"ok": True, **_for_assistant(described)}
        return with_card(out, {"rows": _picker_rows(described["cards"])} if links is not None else None)

    async def _ask_about_ambiguous(ctx: Context, results: list[Any]) -> list[Any]:
        """Where the client can show a form (Claude Code), ask which of the suggestions an
        ambiguous or unknown name meant, then resolve those names again. Elsewhere the result's
        suggestions go to the assistant (and the picker card) as before."""
        asks = [
            (i, r) for i, r in enumerate(results) if r.status in ("ambiguous", "not_found") and r.suggestions
        ]
        if not asks:
            return results
        questions = [
            {"key": f"q{i}", "title": f"Which card is '{r.input.name}'?", "options": list(r.suggestions)[:12]}
            for i, r in asks
        ]
        answers = await ask_choices(
            ctx, "Some names matched several cards. Pick the ones you meant.", questions
        )
        if not answers:
            return results
        redo: list[tuple[int, Any]] = []
        for i, r in asks:
            picked = answers.get(f"q{i}")
            if picked:
                redo.append((i, replace(r.input, name=picked, art_hash="")))
        if not redo:
            return results
        try:
            fresh = await service.resolve([inp for _, inp in redo], owner=_sub())
        except ScanError:  # the first pass stands; its suggestions still reach the assistant
            return results
        out = list(results)
        for (i, _inp), res in zip(redo, fresh, strict=True):
            original = results[i].input
            picked_note = f"picked from the suggestions for '{original.name}'"
            res = replace(res, input=original, note=f"{res.note}; {picked_note}" if res.note else picked_note)
            out[i] = res
        return out

    @server.tool(
        name="card_printings",
        title="List the printings of a card",
        description=(
            "The printings of one Magic card (set, set name, collector number, release date, rarity, "
            "finishes), newest first, from Scryfall, with `sets` (one row per set). Give `oracle_id` from a "
            "resolve_cards result, or `name`. Narrow it with `set_code` (e.g. cmr) and `finish` (foil or "
            "etched: printings that come in it); `limit` caps the rows (default 25; `matching` says how "
            "many matched). In an app that shows cards, the user sees every printing's picture and taps "
            "the one they mean; wait for that pick. Does not touch Archidekt."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
        meta=tool_meta(PRINTINGS_CARD_URI) if links is not None else None,
    )
    async def card_printings(
        oracle_id: Annotated[str | None, Field(description="The card's Scryfall oracle id.")] = None,
        name: Annotated[str | None, Field(description="Or the card's name.")] = None,
        set_code: Annotated[str | None, Field(description="Only printings from this set, e.g. cmr.")] = None,
        finish: Annotated[
            Literal["nonfoil", "foil", "etched"] | None, Field(description="Only printings that come in it.")
        ] = None,
        limit: Annotated[int, Field(description="Rows to return, 1 to 175.")] = 25,
    ) -> CallToolResult:
        try:
            if not oracle_id:
                if not name:
                    raise ScanError("invalid", "give oracle_id or name")
                results = await service.resolve(parse_cards([{"name": name}]), owner=_sub())
                card = results[0].card if results else None
                if not card or not card.get("oracle_id"):
                    raise ScanError("not_found", f"no card found for '{name}'")
                oracle_id = str(card["oracle_id"])
                name = str(card.get("name") or name)
            out = await service.printings(oracle_id, owner=_sub())
        except ScanError as exc:
            return with_card(_err(exc), None)
        cards = [c for c in out.get("cards", []) if isinstance(c, dict)]
        sets = summarize_sets(cards)
        if set_code:
            want = set_code.strip().lower()
            cards = [c for c in cards if str(c.get("set") or "").lower() == want]
        if finish:
            cards = [c for c in cards if finish in (c.get("finishes") or [])]
        shown = max(1, min(int(limit or 25), 175))
        data = {
            "ok": True,
            "oracle_id": oracle_id,
            "name": name or (cards[0].get("name") if cards else None),
            "matching": len(cards),
            "cards": [_slim(c) for c in cards[:shown]],
            "truncated": len(cards) > shown,
            # Scryfall shows 175 printings a page; past that, filters only see the newest 175
            "has_more": bool(out.get("has_more")),
            "total_cards": out.get("total_cards"),
            "sets": sets,
            "note": (
                "Every printing with its picture is on the card in the chat, where the user can tap the "
                "one they mean. Without the card, ask which set, then call again with set_code."
            ),
        }
        card = None
        if links is not None:
            card = {
                "link": links.issue(_sub(), "printings", oracle_id),
                "name": data["name"],
                "oracle_id": oracle_id,
                "total": data["total_cards"],
            }
        return with_card(data, card)

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
