"""MCP tools for Archidekt's community actions of parity release 2 (0.7.43): a deck help request
and a report to Archidekt's moderators (actions.py; each waits for the member's own press), and the
tournament registration sheet (registration.py, a read)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, Literal

from mcp_types import CallToolResult
from pydantic import Field

from . import registration
from .app import AppState, _tool_error
from .decks import DeckError


def add_community_tools(state: AppState, h: SimpleNamespace) -> None:
    """Register propose_deck_help, propose_report and get_registration_pdf."""
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

    @server.tool(
        name="get_registration_pdf",
        title="Make a deck registration sheet (PDF)",
        description=(
            "Archidekt's Deck Registration PDF for a tournament, for any deck the user can open: "
            "Archidekt fills the sheet and answers with the PDF's address (pdf_url; give it to the user "
            "as a link; how long the address keeps working is not known). The main deck is every card "
            "whose first category counts in the deck; the sideboard is every card whose first category "
            "is Sideboard; other categories left out of the deck (a Maybeboard) are left off the sheet. "
            "sort: alpha (the default, by name), manaValue, color or type. The details are optional, "
            "60 characters each (date 20, free text such as 10/24/2026; dci_number digits only, at "
            "most 10) and go to Archidekt only: only pass what the user gave you for this sheet. "
            "Changes nothing in the deck or the account."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
    )
    async def get_registration_pdf(
        deck_id: Annotated[str, Field(description="The deck: its id or URL.")],
        sort: str = "alpha",
        first_name: str | None = None,
        last_name: str | None = None,
        dci_number: str | None = None,
        event: str | None = None,
        location: str | None = None,
        date: str | None = None,
        designer: str | None = None,
        deck_name: Annotated[
            str | None, Field(description="The name printed on the sheet; the deck's own name if left out.")
        ] = None,
    ) -> dict[str, object]:
        details = {
            "first_name": first_name,
            "last_name": last_name,
            "dci_number": dci_number,
            "event": event,
            "location": location,
            "date": date,
            "designer": designer,
            "deck_name": deck_name,
        }
        try:
            out = await registration.make(state.decks, _sub(), deck_id, details, sort)
        except DeckError as exc:
            return _tool_error(exc)
        return {"ok": True, **out}

    @server.tool(
        name="get_my_profile",
        title="Read my Archidekt profile and settings",
        description=(
            "The user's own Archidekt profile and account settings as they are now: username, bio "
            "(its words), whether their collection is public, their email and notification switches "
            "and their New Device Default Settings. Changes nothing."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def get_my_profile() -> dict[str, object]:
        from . import profile

        try:
            now = await profile.read(state.decks, _sub())
        except DeckError as exc:
            return _tool_error(exc)
        return {"ok": True, **profile.summary(now)}

    @server.tool(
        name="get_my_notifications",
        title="Read my Archidekt notifications",
        description=(
            "The user's Archidekt notifications (the bell): kind, text, whether it was seen, and the "
            "page it points to. Archidekt has no way to mark them read from here. Changes nothing."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def get_my_notifications() -> dict[str, object]:
        from . import profile

        try:
            out = await profile.notifications(state.decks, _sub())
        except DeckError as exc:
            return _tool_error(exc)
        return {"ok": True, **out}

    @server.tool(
        name="propose_profile_change",
        title="Propose a change to my Archidekt profile or settings (two-step)",
        description=(
            "Step 1 of changing one thing on the user's own Archidekt account, as its settings page "
            "does, one change a proposal. field: bio (bio, plain text, 1000 characters at most here; "
            "empty clears it), username (username), email (switch: contactable, newsletter, "
            "comment_emails or site_notifications; on: true or false), preference (preference: "
            "deck_view card/text/grid/scroll/table/brewer, search_game paper/mtgo/arena, "
            "unique_search on/off, use_default_categories on/off, show_custom_uploads on/off; value) "
            "or collection_public (on: true or false). A profile picture is changed only on the "
            "gateway's Account page, by the user. Returns the proposal (kind 'action'); only the user's "
            "own press applies it, in every approval mode. Changes nothing on Archidekt by itself."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
        meta=h.card_meta,
    )
    async def propose_profile_change(
        field: Literal["bio", "username", "email", "preference", "collection_public"],
        bio: str | None = None,
        username: str | None = None,
        switch: str | None = None,
        on: bool | None = None,
        preference: str | None = None,
        value: str | None = None,
    ) -> CallToolResult:
        params = {
            "field": field,
            "bio": bio,
            "username": username,
            "switch": switch,
            "on": on,
            "preference": preference,
            "value": value,
        }
        try:
            out = await state.decks.propose_action(_sub(), "profile", **params)
            return _proposal({"ok": True, **out})
        except DeckError as exc:
            return _proposal(_tool_error(exc))

    @server.tool(
        name="propose_contest_entry",
        title="Propose entering a deck in an Archidekt contest (public, two-step)",
        description=(
            "Step 1 of entering one of the user's own decks in an Archidekt deck-building contest, or "
            "withdrawing it, under their name. contest: the contest's id or its page address. enter "
            "needs a summary (plain text, 5000 characters at most here): as on Archidekt, entering "
            "replaces the deck's description with the summary, and the gateway keeps a snapshot "
            "first. Returns the proposal (kind 'action'); only the user's own press applies it, in "
            "every approval mode. Changes nothing on Archidekt by itself."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
        meta=h.card_meta,
    )
    async def propose_contest_entry(
        deck_id: Annotated[str, Field(description="The user's own deck: its id or URL.")],
        contest: Annotated[str, Field(description="The contest's id or its page address.")],
        entry_action: Literal["enter", "withdraw"] = "enter",
        summary: str | None = None,
    ) -> CallToolResult:
        try:
            out = await state.decks.propose_action(
                _sub(), f"contest_{entry_action}", deck_id=deck_id, contest=contest, summary=summary
            )
            return _proposal({"ok": True, **out})
        except DeckError as exc:
            return _proposal(_tool_error(exc))
