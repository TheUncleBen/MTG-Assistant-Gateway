"""How a deck's Commander bracket is shown on the gateway's pages, in one place.

The deck banner (the bracket set on the deck, else Archidekt's estimate from the cards), the Deck
checks row, the Deck stats panel's bracket box and the deck list's card line all draw their bracket
parts here, so the bracket work (BRACKET-ESTIMATE.md, G8, Q49, O14) changes one module. The chat
stats card (static/cards/stats-card.html) follows the same rule. The callers in
deckpage.py stay where they are and only call these. deck.js swaps the stats panel's ``.bracket``
box after a save, so its class name is part of the page's contract.
"""

from __future__ import annotations

from typing import Any

from .theme import esc

BRACKET_NAMES = {1: "Exhibition (1)", 2: "Core (2)", 3: "Upgraded (3)", 4: "Optimized (4)", 5: "cEDH (5)"}


# What every page calls the estimate (O14 "Archidekt label"): Archidekt's own rules from the deck's
# cards, never a second bracket beside the one set on the deck (Q49).
ESTIMATE_LABEL = "Archidekt's estimate from cards"


def bracket_name(value: Any) -> str:
    """A bracket number as Archidekt names it ("Optimized (4)"); anything else as given."""
    return str(BRACKET_NAMES.get(value, value))


def banner_bracket_html(deck: Any, stats: dict[str, Any]) -> str:
    """The deck banner's one bracket chip: the bracket set on the deck, which is the deck's bracket,
    or, only when none is set, Archidekt's estimate from the cards (Q49, O14, G8)."""
    if deck.edh_bracket:
        return f"<span class='bracket-set'>Bracket: {esc(bracket_name(deck.edh_bracket))}</span>"
    bracket = stats.get("bracket_estimate") or {}
    if not bracket.get("bracket"):
        return ""
    return f"<span class='bracket-est'>{ESTIMATE_LABEL}: {esc(bracket_name(bracket.get('bracket')))}</span>"


def checks_bracket_row(checks: dict[str, Any]) -> tuple[str, str, bool] | None:
    """The Deck checks panel's Bracket row (label, text, passed), or None when no bracket is set.
    The estimate is named only on a real mismatch, as archidekt.com's "Bracket Mismatch" (Q49)."""
    br = checks.get("bracket") or {}
    if not br.get("set"):
        return None
    mismatches = br.get("mismatches") or []
    text = f"set on the deck to {esc(bracket_name(br['set']))}"
    if mismatches:
        text += " · not allowed there: " + esc("; ".join(mismatches))
        if br.get("estimate"):
            text += f" · {ESTIMATE_LABEL}: {esc(bracket_name(br['estimate']))}"
    return ("Bracket", text, br.get("ok", True))


def stats_bracket_html(stats: dict[str, Any]) -> str:
    """The Deck stats panel's bracket box (``.bracket``, which deck.js swaps after a save): the
    bracket set on the deck when there is one, then Archidekt's estimate from the cards with
    Archidekt's own notes and what it rests on. Empty without either."""
    bracket = stats.get("bracket_estimate") or {}
    own = stats.get("archidekt_bracket")
    if not bracket.get("bracket") and not own:
        return ""
    parts = ["<div class='bracket'><h3>Commander bracket</h3>"]
    if own:
        parts.append(
            f"<p><b class='orange'>Bracket: {esc(bracket_name(own))}</b> · set on the deck. This is the "
            "deck's bracket.</p>"
        )
    if bracket.get("bracket"):
        parts.append(f"<p>{ESTIMATE_LABEL}: <b>{esc(bracket_name(bracket.get('bracket')))}</b></p>")
        notes = bracket.get("notes") or []
        if notes:
            parts.append(
                "<ul class='bracket-notes'>" + "".join(f"<li>{esc(n)}</li>" for n in notes) + "</ul>"
            )
        basis = bracket.get("basis") or []
        parts.append(
            "<p class='small muted'>"
            + (esc("; ".join(basis)) + ". " if basis else "")
            + "Archidekt works this out from its own card flags (game changers, mass land denial, "
            "extra turns and two-card combos), so it says what the cards suggest, not how strong the "
            "deck plays.</p>"
        )
    parts.append("</div>")
    return "".join(parts)


def deck_card_bracket_text(bracket: Any) -> str:
    """What the deck list's card adds after the format (plain text; the caller escapes the line)."""
    return f" - Bracket: {BRACKET_NAMES.get(bracket, bracket)}" if bracket else ""
