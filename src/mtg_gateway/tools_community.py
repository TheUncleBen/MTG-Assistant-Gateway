"""MCP tools for Archidekt's community actions of parity release 2 (0.7.43): a deck help request
and a report to Archidekt's moderators (actions.py; each waits for the member's own press), and the
tournament registration sheet (registration.py, a read)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated

from mcp_types import CallToolResult
from pydantic import Field

from .app import AppState, _tool_error
from .decks import DeckError


def add_community_tools(state: AppState, h: SimpleNamespace) -> None:
    """Register propose_deck_help and propose_report."""
    _proposal = h._proposal
    _sub = h._sub
    server = h.server

    @server.tool(
        name="propose_deck_help",
        title="Propose a deck help request (public, two-step)",
        description=(
            "Step 1 of asking Archidekt's community for help with one of the user's own decks, on "
            "Archidekt's deck help board, under their name. help_action: post (goals and issues, 10 to "
            "200 words each; Archidekt asks for at least one deck tag and a deck of 80 to 120 cards in a "
            "Commander-style format, else 50 to 85; listed for 7 days, and a delisted or expired "
            "request is opened again), edit (new goals and issues) or delist (takes it down). Returns "
            "the proposal (kind 'action'); only the user's own press applies it, in every approval "
            "mode. Changes nothing on Archidekt by itself."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
        meta=h.card_meta,
    )
    async def propose_deck_help(
        deck_id: Annotated[str, Field(description="The user's own deck: its id or URL.")],
        help_action: str = "post",
        goals: str = "",
        issues: str = "",
    ) -> CallToolResult:
        try:
            out = await state.decks.propose_action(
                _sub(), "deck_help", deck_id=deck_id, help_action=help_action, goals=goals, issues=issues
            )
            return _proposal({"ok": True, **out})
        except DeckError as exc:
            return _proposal(_tool_error(exc))

    @server.tool(
        name="propose_report",
        title="Propose a report to Archidekt's moderators (public, two-step)",
        description=(
            "Step 1 of reporting something on Archidekt to its moderators, under the user's name: a "
            "deck (report_kind deck), a card or custom card in a deck (card: its name in that deck), or "
            "a comment on a deck (comment_id and the page get_deck_comments read it from). message says "
            "what is wrong, in the user's words. Returns the proposal (kind 'action'); only the user's "
            "own press sends it, in every approval mode, and a report can't be taken back. Changes "
            "nothing on Archidekt by itself."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
        meta=h.card_meta,
    )
    async def propose_report(
        deck_id: Annotated[
            str, Field(description="The deck the report is about, or the deck the card or comment is on.")
        ],
        report_kind: str,
        message: str,
        card: str | None = None,
        comment_id: int | None = None,
        page: int | None = None,
    ) -> CallToolResult:
        try:
            out = await state.decks.propose_action(
                _sub(),
                "report",
                deck_id=deck_id,
                report_kind=report_kind,
                message=message,
                card=card,
                comment_id=comment_id,
                page=page,
            )
            return _proposal({"ok": True, **out})
        except DeckError as exc:
            return _proposal(_tool_error(exc))
