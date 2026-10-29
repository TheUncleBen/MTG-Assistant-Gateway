"""Dict shapes shared by the MCP tools, the JSON API and the browser pages.

One place decides what a deck, a card or a proposal looks like to every front door, so the
assistant, the companion web app and the Android app see the same fields.
"""

from __future__ import annotations

from typing import Any

from . import deck_stats
from .archidekt import Deck, DeckCard, rich_text
from .decks import deck_to_archidekt_text, deck_to_text

# Archidekt's finish words and colour words, as the tools write them everywhere else.
_FINISHES = {"normal": "nonfoil", "nonfoil": "nonfoil", "foil": "foil", "etched": "etched"}
_COLOR_LETTERS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G", "colorless": "C"}


def finish_of(modifier: Any) -> str:
    """nonfoil, foil or etched for Archidekt's modifier (Normal, Foil, Etched)."""
    word = str(modifier or "").strip().lower()
    return _FINISHES.get(word, word or "nonfoil")


def color_letters(colors: list[Any]) -> list[str]:
    """W, U, B, R, G (and C) for Archidekt's colour words; a letter already given stays."""
    return [_COLOR_LETTERS.get(str(c).strip().lower(), str(c)) for c in colors]


def card_out(deck: Deck, c: DeckCard) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": c.name,
        "quantity": c.quantity,
        "categories": c.categories,
        "set": c.set_code,
        "collector_number": c.collector_number,
        # the write tools' finish values (nonfoil, foil, etched), not Archidekt's Normal, Foil, Etched
        "finish": finish_of(c.modifier),
        "in_deck": deck.in_deck(c),
    }
    if not c.categories:
        # No category of the member's own: archidekt.com shows the card under its auto category
        # (else its type), and deck_stats counts it there, so the row says which.
        out["shown_under"] = deck_stats.primary_category(c)
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
        if key in ("colors", "color_identity") and isinstance(value, list):
            value = color_letters(value)  # letters, as deck_stats and resolve_cards give them
        if value not in (None, [], "", False):
            out[key] = value
    if getattr(c, "custom_cmc", None) is not None:  # 0 is a real override
        out["custom_mana_value"] = c.custom_cmc
    return out


# What each get_deck ``view`` returns besides the deck's header (name, owner, format, counts...).
DECK_VIEWS = ("summary", "text", "cards", "export", "full")
# Card fields an assistant rarely needs (images, ids, the label colour, ranking numbers): left out
# of the compact "cards" view, kept in "full" for the web pages and the API.
_FULL_ONLY_CARD_FIELDS = (
    "image_hash",
    "scryfall_uid",
    "label",
    "edhrec_rank",
    "salt",
    "colors",
    "types",
    "subtypes",
)


def deck_out(
    deck: Deck,
    *,
    with_cards: bool = True,
    with_stats: bool = True,
    include_text: bool = False,
    view: str = "full",
) -> dict[str, Any]:
    """The deck as the tools and API return it. ``view`` picks the body: ``summary`` (statistics
    only), ``text`` (the headline statistics, stats_brief, plus the plain decklist_text and
    sideboard_text), ``cards`` (the headline statistics plus one compact row per card),
    ``export`` (archidekt_text, Archidekt's own import syntax) or ``full`` (all of them, the whole
    statistics and every card field). ``include_text`` adds each card's rules text
    (``oracle_text``) and so needs card rows: with ``summary``, ``text`` or ``export`` it
    switches to ``cards``."""
    if view not in DECK_VIEWS:
        view = "full"
    if include_text and view in ("summary", "text", "export"):
        view = "cards"
    out: dict[str, Any] = {
        "ok": True,
        "id": deck.id,
        "name": deck.name,
        "owner": deck.owner,
        "updated_at": deck.updated_at,
        "url": f"https://archidekt.com/decks/{deck.id}",
        "format": getattr(deck, "format", None),
        "description": rich_text(getattr(deck, "description", None)),  # its text, not the Quill JSON
        "edh_bracket": getattr(deck, "edh_bracket", None),
        "private": getattr(deck, "private", None),
        "tags": getattr(deck, "tags", None) or [],
        "card_count": sum(c.quantity for c in deck.main_cards),
        **({"link_notice": deck.link_notice} if getattr(deck, "link_notice", "") else {}),
        "side_count": sum(c.quantity for c in deck.side_cards),
        "commanders": [c.name for c in deck.cards if "Commander" in c.categories],
        "categories": [c.get("name") for c in deck.categories if isinstance(c.get("name"), str)],
        # the deck's categories with no card filed under them (Archidekt keeps them until deleted)
        "empty_categories": [
            c["name"]
            for c in deck.categories
            if isinstance(c.get("name"), str) and not any(c["name"] in card.categories for card in deck.cards)
        ],
        # the member's custom cards in the deck (Archidekt's customCards, not in cards or the counts);
        # id is the custom card's id, as propose_custom_card_changes takes it
        "custom_cards": [
            {"id": c.custom_card_id, "name": c.name, "quantity": c.quantity, "categories": list(c.categories)}
            for c in getattr(deck, "custom_cards", None) or []
        ],
    }
    if view != "full":
        out["view"] = view
    if with_cards and view in ("cards", "full"):
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
                # flavor text and the artist only in the full view: an assistant rarely needs them
                if c.flavor and view == "full":
                    row["flavor_text"] = c.flavor
                if c.artist and view == "full":
                    row["artist"] = c.artist
        if view == "cards":
            cards = [{k: v for k, v in row.items() if k not in _FULL_ONLY_CARD_FIELDS} for row in cards]
        out["cards"] = cards
    if with_cards and view in ("text", "full"):
        out["decklist_text"] = deck_to_text(deck)
        out["sideboard_text"] = deck_to_text(deck, zone="side")
    if with_cards and view in ("export", "full"):
        out["archidekt_text"] = deck_to_archidekt_text(deck)
    if with_stats and view != "export":
        try:
            stats = deck_stats.compute(deck)
        except Exception:  # pragma: no cover - statistics must never break a deck read
            stats = None
        # the text and cards views carry the headline numbers only; deck_stats (and the summary
        # and full views) give the whole statistics (AT-31)
        if view in ("text", "cards"):
            out["stats_brief"] = deck_stats.brief(stats)
        else:
            out["stats"] = stats
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


# The category Archidekt shows an uncategorised card under (deck_stats owns the rule and the
# type order; the deck page, the stats and get_deck's shown_under all use that one).
auto_category = deck_stats.auto_category


def cards_by_category(deck: Deck) -> list[tuple[str, list[DeckCard]]]:
    """Cards grouped the way Archidekt shows them: Commander first, then the deck's own category
    order, then anything else alphabetically; uncategorised cards under their auto category. The
    categories not in the deck (Maybeboard, Sideboard and any other the deck marks "Not in deck")
    come last, in the deck's order, after every stack that counts (0.7.46 audit D-14)."""
    order = [c.get("name") for c in deck.categories if isinstance(c.get("name"), str)]
    excluded = deck.excluded_categories()
    groups: dict[str, list[DeckCard]] = {}
    for card in deck.cards:
        cats = card.categories or [auto_category(card)]
        groups.setdefault(cats[0], []).append(card)
    rank = {name: i for i, name in enumerate(order)}
    rank.setdefault("Commander", -1)
    after = len(order) + 2  # past every deck category, "Other" and the automatic stacks

    def key(name: str) -> tuple[int, str]:
        if name in excluded:
            return (after + rank.get(name, len(order)), name.lower())
        return (rank.get(name, len(order) + (1 if name == "Other" else 0)), name.lower())

    return [(name, sorted(groups[name], key=lambda c: c.name.lower())) for name in sorted(groups, key=key)]


__all__ = ["DECK_VIEWS", "auto_category", "card_out", "cards_by_category", "deck_brief", "deck_out"]
