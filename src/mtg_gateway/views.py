"""Dict shapes shared by the MCP tools, the JSON API and the browser pages.

One place decides what a deck, a card or a proposal looks like to every front door, so the
assistant, the companion web app and the Android app see the same fields.
"""

from __future__ import annotations

import re
from typing import Any

from . import deck_stats
from .archidekt import Deck, DeckCard
from .decks import deck_to_archidekt_text, deck_to_text


def card_out(deck: Deck, c: DeckCard) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": c.name,
        "quantity": c.quantity,
        "categories": c.categories,
        "set": c.set_code,
        "collector_number": c.collector_number,
        "finish": c.modifier,
        "in_deck": deck.in_deck(c),
    }
    # Oracle fields are optional on the model (older fixtures and the fake server may lack them).
    for key in (
        "cmc",
        "mana_cost",
        "colors",
        "color_identity",
        "types",
        "subtypes",
        "rarity",
        "price",
        "edhrec_rank",
        "salt",
        "game_changer",
        "notes",
        "label",
        "companion",
        "image_hash",
        "scryfall_uid",
    ):
        value = getattr(c, key, None)
        if value not in (None, [], "", False):
            out[key] = value
    return out


def deck_out(
    deck: Deck, *, with_cards: bool = True, with_stats: bool = True, include_text: bool = False
) -> dict[str, Any]:
    """The full deck as the tools and API return it. ``include_text`` adds each card's rules
    text (``oracle_text``), which is left out by default to keep a deck read small."""
    out: dict[str, Any] = {
        "ok": True,
        "id": deck.id,
        "name": deck.name,
        "owner": deck.owner,
        "updated_at": deck.updated_at,
        "url": f"https://archidekt.com/decks/{deck.id}",
        "format": getattr(deck, "format", None),
        "description": getattr(deck, "description", None) or "",
        "edh_bracket": getattr(deck, "edh_bracket", None),
        "private": getattr(deck, "private", None),
        "tags": getattr(deck, "tags", None) or [],
        "card_count": sum(c.quantity for c in deck.main_cards),
        "side_count": sum(c.quantity for c in deck.side_cards),
        "commanders": [c.name for c in deck.cards if "Commander" in c.categories],
        "categories": [c.get("name") for c in deck.categories if isinstance(c.get("name"), str)],
    }
    if with_cards:
        cards = [card_out(deck, c) for c in deck.cards]
        for row, c in zip(cards, deck.cards, strict=True):
            row["type_line"] = c.type_line
            if c.power or c.toughness:
                row["power"], row["toughness"] = c.power, c.toughness
            if c.loyalty:
                row["loyalty"] = c.loyalty
            if include_text:
                row["oracle_text"] = c.oracle_text
                if c.faces:
                    row["faces"] = c.faces
                if c.flavor:
                    row["flavor_text"] = c.flavor
                if c.artist:
                    row["artist"] = c.artist
        out["cards"] = cards
        out["decklist_text"] = deck_to_text(deck)
        out["sideboard_text"] = deck_to_text(deck, zone="side")
        out["archidekt_text"] = deck_to_archidekt_text(deck)
    if with_stats:
        try:
            out["stats"] = deck_stats.compute(deck)
        except Exception:  # pragma: no cover - statistics must never break a deck read
            out["stats"] = None
    return out


def deck_brief(deck: Deck) -> dict[str, Any]:
    """The short block for lists and history rows."""
    return {
        "id": deck.id,
        "name": deck.name,
        "owner": deck.owner,
        "updated_at": deck.updated_at,
        "url": f"https://archidekt.com/decks/{deck.id}",
        "format": getattr(deck, "format", None),
        "card_count": sum(c.quantity for c in deck.main_cards),
        "commanders": [c.name for c in deck.cards if "Commander" in c.categories],
    }


_AUTO_TYPES = ("Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land")


def auto_category(card: DeckCard) -> str:
    """The category Archidekt shows an uncategorised card under: its oracle ``defaultCategory``
    when the deck JSON carried one, else the card's primary type, else 'Other'."""
    if card.default_category:
        return card.default_category
    words = {w for t in card.types for w in re.split(r"[\s,/]+", t) if w}
    for t in _AUTO_TYPES:
        if t in words:
            return t
    return "Other"


def cards_by_category(deck: Deck) -> list[tuple[str, list[DeckCard]]]:
    """Cards grouped the way Archidekt shows them: Commander first, then the deck's own category
    order, then anything else alphabetically; uncategorised cards under their auto category."""
    order = [c.get("name") for c in deck.categories if isinstance(c.get("name"), str)]
    groups: dict[str, list[DeckCard]] = {}
    for card in deck.cards:
        cats = card.categories or [auto_category(card)]
        groups.setdefault(cats[0], []).append(card)
    rank = {name: i for i, name in enumerate(order)}
    rank.setdefault("Commander", -1)

    def key(name: str) -> tuple[int, str]:
        return (rank.get(name, len(order) + (1 if name == "Other" else 0)), name.lower())

    return [(name, sorted(groups[name], key=lambda c: c.name.lower())) for name in sorted(groups, key=key)]


__all__ = ["auto_category", "card_out", "cards_by_category", "deck_brief", "deck_out"]
