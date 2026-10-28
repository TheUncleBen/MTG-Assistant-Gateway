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
        "Which list: templates (category templates), default_categories (each card's preset "
        "categories), color_tags, pins (pinned cards) or custom_cards."
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
            "Read one of the user's account-wide Archidekt lists as it is now: their category "
            "templates (name and the three switches: in deck, in price, Premier), their default "
            "categories (the categories a card goes into when added to a deck, 100 cards a page; the "
            "first is the main one), their color tags (name and hex color), their pinned cards "
            "(printing, copies, finish and note; Archidekt keeps at most 150) or their custom cards "
            "(search by name, a page at a time). Changes nothing."
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
            "Step 1 of changing one of the user's account-wide Archidekt lists. changes is a list of "
            "changes to that one list, each an object with op and its fields:\n"
            "- templates: create {name, in_deck, in_price, premier} (in deck and price default on, "
            "Premier off); edit {name, new_name?, in_deck?, in_price?, premier?}; delete {name}.\n"
            "- default_categories: set {card, categories: [template names, the main one first]}; "
            "clear {card}. Only the user's template names can be default categories.\n"
            "- color_tags: create {name (32 characters at most), color (hex, default #fa890d)}; edit "
            "{name, new_name?, color?}; delete {name}. Cards already tagged keep their old name and "
            "color.\n"
            "- pins: pin {card, set_code?, collector_number?, finish? (Normal, Foil or Etched), "
            "quantity?, notes?}; edit {card or pin_id, quantity?, notes?, finish?, set_code? and "
            "collector_number? for another printing}; unpin {card or pin_id}; remove_all {}.\n"
            "- custom_cards: create {fields}; edit {name, fields}; delete {name}. fields uses "
            "Archidekt's own names (frontName, frontManaCost, frontTypes, frontSubTypes, "
            "frontSuperTypes, frontText, frontPower, frontToughness, frontLoyalty, frontArtistName, "
            "cmc, hasBack and the back* fields, setCode, collectorNumber); a name and at least one "
            "type are required. A picture is never set from here: the user uploads it on the My "
            "lists page.\n"
            "Each change is checked against the list as it is now. Returns the proposal (kind "
            "'lists') and its review URL; it is applied with apply_proposal like any other. A small "
            "change is low risk (assistant_may_apply says when you may apply it yourself); a delete "
            "(a template, a color tag, a custom card, or all pins) always waits for the user's own "
            "press. Changes nothing on Archidekt by itself."
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
        try:
            out = await state.decks.propose_lists(_sub(), which_list, changes)
            return h._proposal({"ok": True, **out})
        except DeckError as exc:
            return _proposal(_tool_error(exc))
