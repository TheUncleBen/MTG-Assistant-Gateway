"""The deck registration PDF (parity release 2): Archidekt's Deck Registration form, filled from a
deck the member can read and the details they type, sent as the site sends it (POST
/decks/exportPdf/, REQUESTS.md section 12) and answered with the PDF's address.

What the site does is known from its public script; what it does upstream is not. The gateway's
own choices, said in the tool's description: the main deck is every card whose first category
counts in the deck; the sideboard is every card whose first category is "Sideboard"; any other
category left out of the deck (a Maybeboard, for example) is left off the sheet. Copies of one
card in several printings are one line. The details are sent to Archidekt only: the gateway keeps
none of them (its audit line records that a sheet was made, for which deck, nothing typed)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .archidekt import Deck, DeckCard

if TYPE_CHECKING:
    from .decks import DeckService

SORTS = ("alpha", "manaValue", "color", "type")
SORT_WORDS = {"alpha": "name", "manaValue": "mana value", "color": "color", "type": "card type"}
TEXT_MAX = 60
DATE_MAX = 20
DCI_MAX = 10
_COLOR_ORDER = "WUBRG"
_TYPE_ORDER = ("Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land")


def _err(text: str) -> Exception:
    from .decks import DeckError

    return DeckError("invalid", text)


def _first(card: DeckCard) -> str:
    return card.categories[0] if card.categories else ""


def _in_deck(deck: Deck) -> dict[str, bool]:
    return {
        str(c.get("name")): c.get("includedInDeck") is not False
        for c in deck.categories
        if isinstance(c, dict) and c.get("name")
    }


def split(deck: Deck) -> tuple[list[DeckCard], list[DeckCard]]:
    """(main deck, sideboard) as the sheet lists them, before sorting."""
    counts = _in_deck(deck)
    main: list[DeckCard] = []
    side: list[DeckCard] = []
    for c in deck.cards:
        first = _first(c)
        if first.casefold() == "sideboard":
            side.append(c)
        elif counts.get(first, True):  # no category, or one that counts in the deck
            main.append(c)
    return main, side


def _color_key(c: DeckCard) -> tuple[int, str]:
    colors = [x for x in c.colors if x in _COLOR_ORDER]
    if not colors:
        return (6 if "Land" in c.types else 5, "")
    if len(colors) > 1:
        return (7, "".join(sorted(colors, key=_COLOR_ORDER.index)))
    return (_COLOR_ORDER.index(colors[0]), "")


def _type_key(c: DeckCard) -> int:
    return next((i for i, t in enumerate(_TYPE_ORDER) if t in c.types), len(_TYPE_ORDER))


def lines(cards: list[DeckCard], sort: str) -> list[tuple[str, int]]:
    """(name, copies), one line per card name, in the chosen order (name breaks every tie)."""
    total: dict[str, int] = {}
    first: dict[str, DeckCard] = {}
    for c in cards:
        name = c.oracle_name or c.name
        total[name] = total.get(name, 0) + c.quantity
        first.setdefault(name, c)

    def key(name: str) -> tuple[Any, ...]:
        c = first[name]
        if sort == "manaValue":
            return (c.cmc if c.cmc is not None else 99, name.casefold())
        if sort == "color":
            return (*_color_key(c), name.casefold())
        if sort == "type":
            return (_type_key(c), name.casefold())
        return (name.casefold(),)

    return [(n, total[n]) for n in sorted(total, key=key)]


def _text(value: Any, label: str, most: int = TEXT_MAX) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _err(f"{label} must be text")
    value = " ".join(value.split())
    if len(value) > most:
        raise _err(f"{label} is at most {most} characters")
    return value


def fields(deck: Deck, details: dict[str, Any], sort: str) -> dict[str, Any]:
    """The flat form the site sends: card_i / card_i_qty, sideboard_i / sideboard_i_qty, the
    sizes, the deck name and the member's details, the DCI number one character a field."""
    if sort not in SORTS:
        raise _err(f"sort must be one of: {', '.join(SORTS)}")
    dci = _text(details.get("dci_number"), "The DCI number", DCI_MAX).replace(" ", "")
    if dci and not dci.isdigit():
        raise _err("The DCI number is digits only")
    last = _text(details.get("last_name"), "Last name")
    main, side = split(deck)
    out: dict[str, Any] = {
        "deckName": _text(details.get("deck_name") or deck.name[:TEXT_MAX], "Deck name") or "Unnamed deck",
        "date": _text(details.get("date"), "Date", DATE_MAX),
        "firstName": _text(details.get("first_name"), "First name"),
        "lastName": last,
        "firstOfLastName": last[:1],
        "designer": _text(details.get("designer"), "Designer"),
        "location": _text(details.get("location"), "Location"),
        "event": _text(details.get("event"), "Event"),
    }
    main_lines = lines(main, sort)
    side_lines = lines(side, sort)
    for i, (name, qty) in enumerate(main_lines):
        out[f"card_{i}"] = name
        out[f"card_{i}_qty"] = qty
    for i, (name, qty) in enumerate(side_lines):
        out[f"sideboard_{i}"] = name
        out[f"sideboard_{i}_qty"] = qty
    out["deckSize"] = sum(q for _, q in main_lines)
    out["sideboardSize"] = sum(q for _, q in side_lines)
    for i, ch in enumerate(dci):
        out[f"dci_{i}"] = ch
    return out


async def make(
    decks: DeckService, sub: str, deck_ref: str, details: dict[str, Any], sort: str = "alpha"
) -> dict[str, Any]:
    """Fill the form from the deck as the member sees it now and ask Archidekt for the PDF."""
    deck = await decks.get_any_deck(sub, deck_ref, fresh=True)
    form = fields(deck, details, sort)
    url = await decks._call(sub, lambda token, *_: decks.client.registration_pdf(token, form))
    decks._audit("registration_pdf", sub=sub, detail={"deck_id": deck.id})
    out = {
        "deck_id": deck.id,
        "deck_name": form["deckName"],
        "pdf_url": url,
        "main_deck": form["deckSize"],
        "sideboard": form["sideboardSize"],
        "sorted_by": SORT_WORDS[sort],
    }
    broken = format_problems(deck)
    if broken:
        # the sheet is made either way; a tournament would not accept the deck as it is
        out["warnings"] = [f"The deck breaks its format's rules: {p}." for p in broken]
    return out


def format_problems(deck: Any) -> list[str]:
    """The format rules the deck breaks as it is now (deck_stats.deck_checks), in words; none
    when the deck has no format."""
    from . import deck_stats
    from .decks import _new_problems

    if not deck.format:
        return []
    checks = deck_stats.compute(deck).get("checks") or {}
    return [p["problem"] for p in _new_problems({}, checks, now=True)]
