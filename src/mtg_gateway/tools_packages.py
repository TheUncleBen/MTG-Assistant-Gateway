"""MCP tools for Archidekt card packages (packages.py): list packages (the member's own, followed or
everyone's) and add one to a deck. Making a package is propose_new_deck with package; its settings are
propose_deck_details with package."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, Literal

from mcp_types import CallToolResult
from pydantic import Field

from . import packages
from .app import AppState, _tool_error
from .decks import DeckError


def add_package_tools(state: AppState, h: SimpleNamespace) -> None:
    """Register list_card_packages and propose_add_package."""
    _proposal = h._proposal
    _sub = h._sub
    server = h.server

    @server.tool(
        name="list_card_packages",
        title="List Archidekt card packages",
        description=(
            "Archidekt card packages, most imported first, 50 a page, as Archidekt's Packages page "
            "lists them. whose: mine (default, the user's own), followed (ones the user bookmarked) "
            "or all (everyone's public packages). Filters: name_contains (the package's name only), "
            "intended_format (a format "
            "name such as commander) and budget (1 to 4, $ to $$$$). Each package: id, name, owner, "
            "keywords, budget, formats and how many times it was added to a deck. Changes nothing."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def list_card_packages(
        whose: Literal["mine", "followed", "all"] = "mine",
        name_contains: str | None = None,
        intended_format: str | None = None,
        budget: int | None = None,
        page: int = 1,
    ) -> dict[str, object]:
        try:
            got = await packages.listing(
                state.decks,
                _sub(),
                whose=whose,
                name_contains=name_contains,
                intended_format=intended_format,
                budget=budget,
                page=page,
            )
        except DeckError as exc:
            return _tool_error(exc)
        rows = [{k: v for k, v in r.items() if k not in ("block", "size")} for r in got["rows"]]
        return {"ok": True, "packages": rows, "page": page, "more": got["more"]}

    @server.tool(
        name="propose_add_package",
        title="Propose adding a card package to a deck (write, two-step)",
        description=(
            "Step 1 of adding a card package's cards to one of the user's own decks, as Archidekt's Add "
            "package does: any package the user can open (list_card_packages, or its id or URL). "
            "categories: package (each card keeps its package category, the default), default "
            "(Archidekt files them) or maybeboard. skip_existing (default true) leaves out cards the "
            "deck has; skip_outside_identity (default true) leaves out cards outside the commander's "
            "colors; singleton (default: the deck's format decides) adds one copy of each; color_tag "
            "puts a color tag on every card it adds (one the deck uses or one of the user's color tags "
            "by name, or any name with color as #rrggbb). Returns a "
            "proposal like propose_deck_changes (kind 'edit': the adds, each reviewed, under a line "
            "naming the package and what was left out), applied with apply_proposal. When it is "
            "applied, Archidekt counts one import of the package, as on the site. Changes nothing on "
            "Archidekt by itself."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
        meta=h.card_meta,
    )
    async def propose_add_package(
        deck_id: Annotated[str, Field(description="The user's own deck: its id or URL.")],
        package_id: Annotated[str, Field(description="The card package: its id or URL.")],
        categories: Literal["package", "default", "maybeboard"] = "package",
        skip_existing: bool = True,
        skip_outside_identity: bool = True,
        singleton: bool | None = None,
        color_tag: str | None = None,
        color: str | None = None,
    ) -> CallToolResult:
        try:
            out = await state.decks.propose_package(
                _sub(),
                deck_id,
                package_id,
                categories=categories,
                skip_existing=skip_existing,
                skip_outside_identity=skip_outside_identity,
                singleton=singleton,
                color_tag=color_tag,
                color=color,
            )
            return _proposal({"ok": True, **out})
        except DeckError as exc:
            return _proposal(_tool_error(exc))
