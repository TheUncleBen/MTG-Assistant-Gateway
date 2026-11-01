"""MCP tools for the member's account-wide Archidekt lists (0.7.43, account_lists.py): one read
and one write for category templates, default categories, color tags, pinned cards and custom
cards."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, Any

from mcp_types import CallToolResult
from pydantic import Field

from . import account_lists
from .app import AppState, _tool_error
from .decks import DeckError

LIST_FIELD = Field(
    description=(
        "templates (category templates), default_categories, color_tags, pins, custom_cards or "
        "collection_tags (binder tags)."
    )
)


def add_list_tools(state: AppState, h: SimpleNamespace) -> None:
    """Register get_my_lists and propose_list_change."""
    _proposal = h._proposal
    _sub = h._sub
    server = h.server

    @server.tool(
        name="get_my_lists",
        title="Read one of my Archidekt lists",
        description=(
            "Read one of the user's account-wide Archidekt lists: category templates (name and the in deck, "
            "in price and Premier switches), default categories (a card's categories when added to a deck, "
            "the main one first; 100 cards a page), color tags (name and hex color), pinned cards (printing, "
            "copies, finish, note) or custom cards (search by name, a page at a time). Changes nothing."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def get_my_lists(
        which_list: Annotated[str, LIST_FIELD],
        page: int = 1,
        search: str = "",
    ) -> dict[str, object]:
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 1000:
            return {"ok": False, "error": "invalid", "message": "page must be a number from 1 to 1000"}
        try:
            got = await account_lists.read(state.decks, _sub(), which_list, page=page, search=search[:100])
        except DeckError as exc:
            return _tool_error(exc)
        return {"ok": True, **got}

    @server.tool(
        name="propose_list_change",
        title="Propose a change to one of my Archidekt lists (write, two-step)",
        description=(
            "Step 1 of changing one of the user's account-wide Archidekt lists. changes: changes to that one "
            "list, each {op, ...} (action is taken for op too):\n"
            "- templates: create {name, in_deck, in_price, premier} (in deck and in price default on); edit "
            "{name, new_name?, in_deck?, in_price?, premier?}; delete {name}.\n"
            "- default_categories: set {card, categories: [template names, the main one first]}; clear "
            "{card}.\n"
            "- color_tags: create {name (at most 32 characters), color (hex, default #fa890d)}; edit {name, "
            "new_name?, color?}; delete {name}.\n"
            "- collection_tags (binder tags): as color_tags.\n"
            "- pins: pin {card, set_code?, collector_number?, finish? (Normal, Foil or Etched), quantity?, "
            "notes?}; edit {card or pin_id, quantity?, notes?, finish?, set_code?, collector_number?}; unpin "
            "{card or pin_id}; remove_all {}.\n"
            "- custom_cards: create {fields}; edit {name, fields}; delete {name}. fields uses Archidekt's "
            "names (frontName, frontManaCost, frontTypes, frontSubTypes, frontSuperTypes, frontText, "
            "frontPower, frontToughness, frontLoyalty, frontArtistName, cmc, hasBack and the back* fields, "
            "setCode, collectorNumber); a name and a type are required. Pictures are uploaded by the user "
            "on Binder › Lists.\n"
            "Returns the proposal (kind 'lists'), applied with apply_proposal; a delete (or remove_all "
            "pins) always waits for the user's own press. Changes nothing on Archidekt by itself."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
        meta=h.card_meta,
    )
    async def propose_list_change(
        which_list: Annotated[str, LIST_FIELD],
        changes: Annotated[
            list[dict[str, Any]],
            Field(description="The changes to that list, each with op (see the description)."),
        ],
    ) -> CallToolResult:
        # every other propose tool names a change's kind "action": taken here for "op" too
        changes = [
            {"op": c["action"], **{k: v for k, v in c.items() if k != "action"}}
            if isinstance(c, dict) and "op" not in c and "action" in c
            else c
            for c in changes
        ]
        try:
            out = await state.decks.propose_lists(_sub(), which_list, changes)
            return h._proposal({"ok": True, **out})
        except DeckError as exc:
            return _proposal(_tool_error(exc))
