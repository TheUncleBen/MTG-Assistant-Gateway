"""How a deck's Commander bracket is shown on the gateway's pages, in one place.

The deck banner (the bracket set on the deck and the gateway's estimate), the Deck checks row, the
Deck stats panel's estimate box and the deck list's card line all draw their bracket parts here,
so the bracket work (BRACKET-ESTIMATE.md, G8, Q49) changes one module. The callers in
deckpage.py stay where they are and only call these. deck.js swaps the stats panel's ``.bracket``
box after a save, so its class name is part of the page's contract.
"""

from __future__ import annotations

from typing import Any

from .theme import esc

BRACKET_NAMES = {1: "Exhibition (1)", 2: "Core (2)", 3: "Upgraded (3)", 4: "Optimized (4)", 5: "cEDH (5)"}


def banner_bracket_html(deck: Any, stats: dict[str, Any]) -> str:
    """The deck banner's bracket chips: the bracket set on the deck, then the estimate."""
    bracket = stats.get("bracket_estimate") or {}
    est_bracket = (
        f"<span>Est Bracket: {esc(BRACKET_NAMES.get(bracket.get('bracket'), bracket.get('bracket')))}</span>"
        if bracket.get("bracket")
        else ""
    )
    own_bracket = (
        f"<span>Bracket: {esc(BRACKET_NAMES.get(deck.edh_bracket, deck.edh_bracket))}</span>"
        if deck.edh_bracket
        else ""
    )
    return own_bracket + est_bracket


def checks_bracket_row(checks: dict[str, Any]) -> tuple[str, str, bool] | None:
    """The Deck checks panel's Bracket row (label, text, passed), or None when no bracket is set."""
    br = checks.get("bracket") or {}
    if not br.get("set"):
        return None
    return (
        "Bracket",
        f"set on the deck to {br['set']}"
        + (f", cards suggest {br['estimate']} (estimate)" if br.get("estimate") else "")
        + (" · not allowed there: " + esc("; ".join(br["mismatches"])) if br.get("mismatches") else ""),
        br.get("ok", True),
    )


def stats_bracket_html(stats: dict[str, Any]) -> str:
    """The Deck stats panel's bracket estimate box (``.bracket``), empty without an estimate."""
    bracket = stats.get("bracket_estimate") or {}
    basis = bracket.get("basis") or []
    return (
        "<div class='bracket'><h3>Commander bracket estimate</h3>"
        f"<p><b class='orange'>Bracket {esc(bracket.get('bracket'))}</b>"
        + (" · " + "; ".join(esc(b) for b in basis) if basis else "")
        + "</p>"
        f"<p class='small muted'>Game changers: {len(stats.get('game_changers') or [])} · Tutors: "
        f"{stats.get('tutors', 0)} · Extra turns: {stats.get('extra_turns', 0)} · Mass land denial: "
        f"{stats.get('mass_land_denial', 0)}. From Archidekt's card flags only; an estimate, not a "
        "ruling.</p>"
        "</div>"
        if bracket.get("bracket")
        else ""
    )


def deck_card_bracket_text(bracket: Any) -> str:
    """What the deck list's card adds after the format (plain text; the caller escapes the line)."""
    return f" - Bracket: {BRACKET_NAMES.get(bracket, bracket)}" if bracket else ""
