"""MCP tools for Archidekt card packages (0.7.43, packages.py): list the member's own packages and
add one to a deck. Making a package is propose_new_deck with package; its settings are
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
    """Register list_my_packages and propose_add_package."""
    _proposal = h._proposal
    _sub = h._sub
    server = h.server

    @server.tool(
        name="list_my_packages",
        title="List my Archidekt card packages",
        description=(
            "The user's own Archidekt card packages, most imported first, 50 a page: id, name, "
            "keywords, budget, formats and how many times each was added to a deck. Changes nothing."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def list_my_packages(page: int = 1) -> dict[str, object]:
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 100:
            return {"ok": False, "error": "invalid", "message": "page must be a number from 1 to 100"}
        decks = state.decks
        try:
            _token, link = decks._token(_sub())
            body = await decks._call(
                _sub(), lambda token, *_: decks.client.my_packages(token, link["archidekt_username"], page)
            )
        except DeckError as exc:
            return _tool_error(exc)
        rows = []
        for r in body["results"]:
            if not isinstance(r, dict) or not str(r.get("id") or "").isdigit():
                continue
            block = r.get("cardPackage") if isinstance(r.get("cardPackage"), dict) else {}
            rows.append(
                {
                    "id": str(r["id"]),
                    "name": str(r.get("name") or "Unnamed deck"),
                    "url": f"https://archidekt.com/decks/{r['id']}",
                    "private": bool(r.get("private")),
                    "settings": packages.text(block) if block else None,
                    "imports": block.get("importCount"),
                }
            )
        return {"ok": True, "packages": rows, "page": page, "more": bool(body.get("next"))}

    @server.tool(
        name="propose_add_package",
        title="Propose adding a card package to a deck (write, two-step)",
        description=(
            "Step 1 of adding a card package's cards to one of the user's own decks, as Archidekt's Add "
            "package does: any package the user can open (list_my_packages, or its id or URL). "
            "categories: package (each card keeps its package category, the default), default "
            "(Archidekt files them) or maybeboard. skip_existing (default true) leaves out cards the "
            "deck has; skip_outside_identity (default true) leaves out cards outside the commander's "
            "colors; singleton (default: the deck's format decides) adds one copy of each. Returns a "
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
            )
            return _proposal({"ok": True, **out})
        except DeckError as exc:
            return _proposal(_tool_error(exc))
