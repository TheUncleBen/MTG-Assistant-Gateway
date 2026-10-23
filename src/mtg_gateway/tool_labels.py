"""Plain words for the gateway's tool names, used wherever a member or an admin reads about them.

Member text never shows a tool name (the Guide's closed "Advanced: tool names" fold and the admin
rows' "Recorded as {raw name}" detail are the only places). Each registered MCP tool has three
words here: its raw name, a readable name for admin lists ("Read a deck") and the plain ask a
member would type to their assistant ("Read my deck"). The admin Metrics page also records errors
(``server_error`` and a tool's failure), the JSON API's routes ("GET /decks"), the admin actions
and the chat card loads (``printings``); :func:`metric_label` gives each of those its words too.

tests/test_tool_labels.py fails when a registered tool, an API route, an admin action or a card
kind has no entry here, so a new tool cannot ship nameless.
"""

from __future__ import annotations

from typing import NamedTuple


class ToolWords(NamedTuple):
    name: str  # readable name for admin lists ("Read a deck")
    ask: str  # the plain ask a member would type ("Read my deck")


# Every tool the gateway registers (tools_*.py, app.py, browse.py, collection.py, summary.py,
# watchlist.py) and the Mystic Forge tools it passes through (mf_proxy.ALLOWED_TOOLS).
TOOLS: dict[str, ToolWords] = {
    # app.py
    "whoami": ToolWords("Who am I", "Who am I on the MTG gateway?"),
    # tools_decks.py
    "account_status": ToolWords("Account status", "Is my Archidekt account linked?"),
    "list_my_decks": ToolWords("List my decks", "List my decks"),
    "get_deck": ToolWords("Read a deck", "Read my deck"),
    "parse_decklist": ToolWords("Read a decklist", "Read this decklist"),
    "propose_new_deck": ToolWords("Propose a new deck", "Make me a new deck"),
    "propose_deck_changes": ToolWords("Propose deck changes", "Change my deck"),
    "check_deck_changes": ToolWords("Check deck changes", "Check these changes"),
    "list_my_proposals": ToolWords("List my changes", "List my changes"),
    "amend_proposal": ToolWords("Amend a change", "Amend that change"),
    "withdraw_proposal": ToolWords("Withdraw a change", "Take that change back"),
    "combine_proposals": ToolWords("Combine changes", "Combine those proposals"),
    "show_waiting_changes": ToolWords("Show waiting changes", "What is waiting for my approval?"),
    "confirm_waiting": ToolWords("Apply all from a chat card", "Apply all (a chat card's button)"),
    "get_apply_result": ToolWords("Follow an apply from a chat card", "Is the apply done? (a chat card)"),
    "get_proposal": ToolWords("Read a change", "Show me that change"),
    "list_snapshots": ToolWords("List snapshots", "List my deck snapshots"),
    "propose_restore_snapshot": ToolWords("Propose a restore", "Put my deck back to that snapshot"),
    "propose_deck_categories": ToolWords("Propose category changes", "Add, delete or rename a deck category"),
    "apply_proposal": ToolWords("Apply a change", "Apply that change"),
    "confirm_proposal": ToolWords("Approve or reject from a chat card", "Approve (a chat card's button)"),
    "reject_proposal": ToolWords("Reject a change", "Reject that change"),
    "get_snapshot": ToolWords("Read a snapshot", "Show me that snapshot"),
    # tools_account.py
    "propose_deck_details": ToolWords("Propose deck settings", "Change my deck's settings"),
    "propose_clone_deck": ToolWords("Propose a copy of a deck", "Copy this deck into my decks"),
    "get_deck_comments": ToolWords("Read deck comments", "Read the comments on this deck"),
    "propose_deck_social": ToolWords("Propose a like, bookmark or follow", "Bookmark this deck"),
    "propose_comment": ToolWords("Propose a comment", "Comment on this deck"),
    "propose_delete_deck": ToolWords("Propose deleting a deck", "Delete my deck"),
    "propose_create_folder": ToolWords("Propose a new folder", "Make me a folder"),
    "get_deck_collaborators": ToolWords("Read deck collaborators", "Who can edit my deck?"),
    # tools_community.py
    "propose_deck_help": ToolWords("Propose a deck help request", "Ask for help with this deck"),
    "propose_report": ToolWords("Propose a report to Archidekt", "Report this deck to Archidekt"),
    "get_my_profile": ToolWords("Read my Archidekt profile", "Show my Archidekt profile"),
    "get_my_notifications": ToolWords("Read my Archidekt notifications", "Any new notifications?"),
    "propose_profile_change": ToolWords("Propose a profile or settings change", "Change my Archidekt bio"),
    "propose_contest_entry": ToolWords("Propose a contest entry", "Enter this deck in the contest"),
    "get_registration_pdf": ToolWords("Make a deck registration sheet", "Make a registration PDF"),
    # tools_packages.py
    "list_card_packages": ToolWords("List card packages", "List card packages"),
    "propose_add_package": ToolWords("Propose adding a card package", "Add my Fetches package to this deck"),
    # tools_lists.py
    "get_my_lists": ToolWords("Read my Archidekt lists", "List my category templates"),
    "propose_list_change": ToolWords("Propose a change to my lists", "Add a color tag called Cut, red"),
    "propose_collaborator": ToolWords("Propose a collaborator change", "Let someone edit my deck"),
    "parse_deck_export": ToolWords("Read an Archidekt export", "Read this Archidekt export"),
    # tools_reports.py
    "deck_stats": ToolWords("Deck statistics", "Show my deck's statistics"),
    "compare_decks": ToolWords("Compare two decks", "Compare two decks"),
    "run_deck_report": ToolWords("Run a test report", "Run a test report"),
    "list_deck_reports": ToolWords("List test reports", "List my test reports"),
    "remove_card_model": ToolWords("Remove a saved description", "Remove my saved description for this card"),
    "get_deck_report": ToolWords("Read a test report", "Show me that test report"),
    "play_more_games": ToolWords("Play more games", "Play more games on that report"),
    # scan/ (the card scanner's tools)
    "resolve_cards": ToolWords("Find exact printings", "Which printing is this card?"),
    "card_printings": ToolWords("List a card's printings", "Show me this card's printings"),
    "save_scan_session": ToolWords("Save a scan", "Save this scan"),
    "list_scan_sessions": ToolWords("List my scans", "List my scans"),
    "get_scan_session": ToolWords("Read a scan", "Show me that scan"),
    # browse.py, collection.py, summary.py
    "search_decks": ToolWords("Search public decks", "Find public decks"),
    "list_collection": ToolWords("Read my collection", "What is in my collection?"),
    "propose_collection_changes": ToolWords("Propose collection changes", "Add these cards to my collection"),
    "get_summary": ToolWords("Daily or weekly summary", "What happened this week?"),
    # watchlist.py (with watchlists on)
    "watch_card": ToolWords("Watch a card", "Watch this card's price"),
    "watch_deck": ToolWords("Watch a deck", "Watch this deck's price"),
    "unwatch": ToolWords("Stop watching", "Stop watching that"),
    "list_watchlist": ToolWords("Read my watchlist", "What is on my watchlist?"),
    "card_price_history": ToolWords("Card price history", "How has this card's price moved?"),
    # Mystic Forge, passed through (mf_proxy.ALLOWED_TOOLS)
    "scryfall_search": ToolWords("Search cards", "Find cards that…"),
    "scryfall_named": ToolWords("Look up a card", "Look up this card"),
    "scryfall_random": ToolWords("A random card", "Show me a random card"),
    "scryfall_price": ToolWords("A card's price", "What does this card cost?"),
    "scryfall_price_list": ToolWords("Prices of a list", "Price this list of cards"),
    "scryfall_card_text": ToolWords("A card's rules text", "What does this card do?"),
    "scryfall_rulings": ToolWords("A card's rulings", "Any rulings on this card?"),
    "edhrec_commander": ToolWords("EDHREC: a commander's page", "What goes in a deck for this commander?"),
    "edhrec_average_deck": ToolWords("EDHREC: the average deck", "Show the average deck for this commander"),
    "edhrec_combos": ToolWords("EDHREC: combos", "Which combos fit this commander?"),
    "edhrec_top_cards": ToolWords("EDHREC: top cards", "What are the top cards for this commander?"),
    "edhrec_recommendations": ToolWords("EDHREC: recommendations", "What should I add to my deck?"),
    "edhrec_salt": ToolWords("Most disliked cards (EDHREC)", "Which cards do people dislike most?"),
    "edhrec_precon_upgrade": ToolWords("EDHREC: precon upgrades", "How do I upgrade this precon?"),
    "format_archidekt": ToolWords("Format for Archidekt", "Put this list in Archidekt's format"),
    "validate_decklist": ToolWords("Check a decklist", "Is this decklist legal?"),
    "spellbook_combos": ToolWords("Combos in a deck", "Which combos are in my deck?"),
    "spellbook_card_combos": ToolWords("Combos with a card", "Which combos use this card?"),
    "rules_get": ToolWords("Read a rule", "What does rule 903 say?"),
    "rules_search": ToolWords("Search the rules", "Find the rule about…"),
    "precon_search": ToolWords("Find a precon", "Find the precon called…"),
    "precon_decklist": ToolWords("A precon's decklist", "What is in this precon?"),
    "precon_export": ToolWords("Export a precon", "Export this precon's list"),
    "goldfish_odds": ToolWords("Draw odds", "What are the odds I draw this by turn 3?"),
    "goldfish_annotate": ToolWords("Annotate a deck for the simulator", "Annotate my deck for the simulator"),
}

# The JSON API's routes (api.py), recorded as "METHOD /path".
API_ROUTES: dict[str, str] = {
    "GET /me": "Who am I (API)",
    "GET /decks": "List my decks (API)",
    "GET /decks/{deck_id}": "Read a deck (API)",
    "GET /decks/{deck_id}/stats": "Deck statistics (API)",
    "GET /decks/{deck_id}/history": "Deck history (API)",
    "GET /compare": "Compare two decks (API)",
    "POST /decks/{deck_id}/edit": "Propose deck changes (API)",
    "GET /proposals": "List my changes (API)",
    "POST /proposals": "Propose a change (API)",
    "GET /proposals/{pid}": "Read a change (API)",
    "POST /proposals/{pid}/apply": "Apply a change (API)",
    "POST /proposals/{pid}/reject": "Reject a change (API)",
    "GET /snapshots": "List snapshots (API)",
    "GET /snapshots/{sid}": "Read a snapshot (API)",
    "GET /reports": "List test reports (API)",
    "POST /reports": "Run a test report (API)",
    "GET /reports/{rid}": "Read a test report (API)",
    "GET /reports/{rid}/forge": "Read a report's Forge games (API)",
    "POST /reports/{rid}/forge/more": "Play more games (API)",
    "DELETE /reports/{rid}": "Delete a test report (API)",
    "GET /activity": "My activity (API)",
    # the admin pages' own JSON (admin.py; not counted as API calls today)
    "GET /admin/overview": "Admin overview (API)",
    "GET /admin/users": "Admin users (API)",
    "GET /admin/metrics": "Admin metrics (API)",
    "POST /admin/users/{sub:path}": "Admin action on a member (API)",
}

# Admin actions (admin.ACTIONS), in the Activity page's own words (NEW-WORDING).
ADMIN_ACTIONS: dict[str, str] = {
    "disable": "Account disabled",
    "enable": "Account turned back on",
    "revoke": "Assistants' access and sign-ins revoked",
    "unlink": "Archidekt unlinked by an admin",
    "delete_data": "Former member's data deleted",
}

# Chat card loads (cards.LINK_KINDS): the pictures a chat card reads through a signed link.
CARD_KINDS: dict[str, str] = {"printings": "Card printings"}

# Errors that are not a tool's failure (app.py).
ERRORS: dict[str, str] = {"server_error": "Server error"}

# The metric kinds (admin.METRIC_KINDS and the card kind), as the Metrics page names them.
METRIC_KINDS: dict[str, str] = {
    "tool": "Tool calls",
    "error": "Errors",
    "api": "API calls",
    "admin": "Admin actions",
    "card": "Chat card loads",
}

UNKNOWN = "(unknown)"
UNNAMED = "(unnamed)"


def tool_name(raw: str) -> str:
    """The readable name of a tool for admin lists ("Read a deck"); an unregistered or made-up
    name reads "Unknown tool", never the raw text."""
    if raw == UNKNOWN or raw == UNNAMED:
        return "Unknown tool"
    words = TOOLS.get(raw)
    return words.name if words else "Unknown tool"


def tool_ask(raw: str) -> str:
    """The plain ask a member would type for a tool ("Read my deck"), for the Guide's list of
    abilities and the setup pages."""
    words = TOOLS.get(raw)
    return words.ask if words else ""


def metric_label(kind: str, name: str) -> str:
    """The words for one Metrics row: a tool's readable name, "{Tool name} failed" or "Server
    error" for an error, the API route's name, the admin action's words or the card kind's.
    Anything unknown reads as "Unknown tool", "Unknown error" or the like; the raw name belongs in
    the row's detail ("Recorded as {raw name}"), never in the label."""
    if kind == "tool":
        return tool_name(name)
    if kind == "error":
        if name in ERRORS:
            return ERRORS[name]
        return f"{tool_name(name)} failed" if name in TOOLS else "Unknown error"
    if kind == "api":
        return API_ROUTES.get(name, "Unknown API call")
    if kind == "admin":
        return ADMIN_ACTIONS.get(name, "Unknown admin action")
    if kind == "card":
        return CARD_KINDS.get(name, "Unknown card load")
    return METRIC_KINDS.get(kind, "Unknown")


def recorded_as(name: str) -> str:
    """The row detail that carries the raw name (the one admin place a tool name shows)."""
    return f"Recorded as {name}"


__all__ = [
    "ADMIN_ACTIONS",
    "API_ROUTES",
    "CARD_KINDS",
    "ERRORS",
    "METRIC_KINDS",
    "TOOLS",
    "ToolWords",
    "metric_label",
    "recorded_as",
    "tool_ask",
    "tool_name",
]
