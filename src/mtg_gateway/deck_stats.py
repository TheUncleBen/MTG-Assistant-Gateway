"""Pure deck statistics over the richer Archidekt deck model. No I/O.

Everything here is computed from the fields Archidekt sends with a deck (``DeckCard``'s oracle
details). Where a figure depends on Archidekt's own judgement (prices, salt, game-changer and
bracket flags) it is reported as Archidekt reports it, not verified by us.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .archidekt import Deck, DeckCard
from .decklist import ListCard

WUBRG = "WUBRG"
COLOR_LETTERS = {
    "w": "W",
    "white": "W",
    "u": "U",
    "blue": "U",
    "b": "B",
    "black": "B",
    "r": "R",
    "red": "R",
    "g": "G",
    "green": "G",
}
COMMANDER_FORMATS = {"commander", "edh"}
_SYMBOL_RE = re.compile(r"\{([^{}]+)\}")
_NUMERIC_STATS = (
    "card_count",
    "distinct",
    "land_count",
    "nonland_count",
    "average_mana_value",
    "price_total",
    "priced_cards",
    "tutors",
    "extra_turns",
    "mass_land_denial",
    "salt_total",
)


def colour_letter(value: str) -> str | None:
    """'Blue' or 'U' -> 'U'; anything else -> None."""
    return COLOR_LETTERS.get(value.strip().casefold())


def colour_identity(cards: list[DeckCard]) -> list[str]:
    """Union of the cards' colour identities, in WUBRG order."""
    letters = {colour_letter(c) for card in cards for c in card.color_identity}
    return [c for c in WUBRG if c in letters]


def mana_pips(mana_cost: str) -> dict[str, float]:
    """Coloured pips in a mana cost. A plain pip counts 1 for its colour. A hybrid pip such as
    {W/U} counts half for each of its colours (so it still weighs one pip in total); {2/W} and
    Phyrexian {W/P} count one full pip for their single colour. Generic, X, colourless and snow
    symbols add nothing."""
    pips: dict[str, float] = {}
    for sym in _SYMBOL_RE.findall(mana_cost or ""):
        colours = [c for c in (colour_letter(p) for p in sym.split("/")) if c]
        if not colours:
            continue
        share = 1.0 / len(colours)
        for c in colours:
            pips[c] = pips.get(c, 0.0) + share
    return pips


def is_land(card: DeckCard) -> bool:
    return any(t.casefold() == "land" for t in card.types)


def _curve_bucket(cmc: float) -> str:
    n = int(cmc)
    return "7+" if n >= 7 else str(max(n, 0))


def _round(value: float) -> float:
    return round(value + 0.0, 2)


def bracket_estimate(cards: list[DeckCard], deck_format: str | None = None) -> dict[str, Any]:
    """A Commander bracket estimate from Archidekt's own card flags only (game changers, mass
    land denial, extra turns, tutors, two-card combos). Rules: any mass land denial, or two or
    more extra-turn cards (chaining), means at least bracket 4; more than three game changers
    means 4; one to three game changers means 3; a two-card combo shared by two cards in the
    deck means at least 3; none of these means 2. Bracket 5 (cEDH) is a matter of intent, so it
    is never estimated. ``basis`` lists what pushed the number; this is an estimate, not a
    ruling."""
    if deck_format is not None and deck_format not in COMMANDER_FORMATS:
        return {"bracket": None, "kind": "estimate", "basis": [f"not a Commander deck ({deck_format})"]}
    if not cards:
        return {"bracket": None, "kind": "estimate", "basis": ["no cards"]}
    changers = sorted({c.name for c in cards if c.game_changer})
    mld = sorted({c.name for c in cards if c.mass_land_denial})
    turns = sorted({c.name for c in cards if c.extra_turns})
    tutors = sorted({c.name for c in cards if c.tutor})
    combo_cards: dict[str, set[str]] = {}
    for c in cards:
        for combo in c.two_card_combo_ids:
            combo_cards.setdefault(combo, set()).add(c.name)
    combos = sorted(names for names in combo_cards.values() if len(names) >= 2)

    bracket = 2
    basis: list[str] = []
    if changers:
        bracket = 4 if len(changers) > 3 else 3
        basis.append(f"{len(changers)} game changer(s): {', '.join(changers)}")
    else:
        basis.append("no game changers")
    if mld:
        bracket = max(bracket, 4)
        basis.append(f"mass land denial: {', '.join(mld)}")
    if len(turns) >= 2:
        bracket = max(bracket, 4)
        basis.append(f"extra-turn chaining possible: {', '.join(turns)}")
    elif turns:
        basis.append(f"one extra-turn card: {turns[0]}")
    if combos:
        bracket = max(bracket, 3)
        basis.append(
            f"{len(combos)} two-card combo(s) in the deck: "
            + "; ".join(" + ".join(sorted(c)) for c in combos)
        )
    if tutors:
        basis.append(f"{len(tutors)} tutor(s)")
    return {"bracket": bracket, "kind": "estimate", "basis": basis}


def compute(deck: Deck) -> dict[str, Any]:
    """Statistics for the deck proper (maybeboard and sideboard left out)."""
    cards = deck.main_cards
    lands = [c for c in cards if is_land(c)]
    nonlands = [c for c in cards if not is_land(c)]
    qty = sum(c.quantity for c in cards)

    mv_total = sum(c.cmc * c.quantity for c in nonlands if c.cmc is not None)
    mv_count = sum(c.quantity for c in nonlands if c.cmc is not None)
    curve: Counter[str] = Counter({**{str(i): 0 for i in range(7)}, "7+": 0})
    for c in nonlands:
        if c.cmc is not None:
            curve[_curve_bucket(c.cmc)] += c.quantity

    pips: dict[str, float] = {}
    for c in cards:
        for colour, n in mana_pips(c.mana_cost).items():
            pips[colour] = pips.get(colour, 0.0) + n * c.quantity
    sources: dict[str, int] = {}
    for c in cards:
        for colour, n in (c.mana_production or {}).items():
            sources[colour] = sources.get(colour, 0) + n * c.quantity

    types: Counter[str] = Counter()
    rarities: Counter[str] = Counter()
    for c in cards:
        for t in c.types:
            types[t] += c.quantity
        if c.rarity:
            rarities[c.rarity] += c.quantity

    priced = [c for c in cards if c.price is not None]
    price_total = sum(c.price * c.quantity for c in priced if c.price is not None)

    problems: list[dict[str, str]] = []
    unknown = 0
    if deck.format:
        for c in cards:
            status = c.legalities.get(deck.format)
            if status is None:
                unknown += 1
            elif status != "legal":
                problems.append({"name": c.name, "status": status})

    return {
        "card_count": qty,
        "distinct": len({c.name for c in cards}),
        "land_count": sum(c.quantity for c in lands),
        "nonland_count": sum(c.quantity for c in nonlands),
        "average_mana_value": _round(mv_total / mv_count) if mv_count else None,
        "mana_curve": dict(curve),
        "colour_pips": {k: _round(v) for k, v in pips.items() if k in WUBRG and v},
        "mana_sources": dict(sorted(sources.items(), key=lambda kv: (WUBRG + "C").find(kv[0]))),
        "type_counts": dict(types.most_common()),
        "rarity_counts": dict(rarities.most_common()),
        "price_total": _round(price_total),
        "priced_cards": len(priced),
        "format": deck.format,
        "legality_problems": problems,
        "legality_unknown": unknown,
        "game_changers": sorted({c.name for c in cards if c.game_changer}),
        "tutors": sum(c.quantity for c in cards if c.tutor),
        "extra_turns": sum(c.quantity for c in cards if c.extra_turns),
        "mass_land_denial": sum(c.quantity for c in cards if c.mass_land_denial),
        "salt_total": _round(sum(c.salt * c.quantity for c in cards if c.salt is not None)),
        "commanders": [c.name for c in cards if "Commander" in c.categories],
        "colour_identity": colour_identity(cards),
        "bracket_estimate": bracket_estimate(cards, deck.format),
        "archidekt_bracket": deck.edh_bracket,
    }


def counts_from_text(cards: list[ListCard]) -> dict[str, int]:
    """Card counts by name for the main zone of a parsed decklist."""
    out: dict[str, int] = {}
    for c in cards:
        if c.zone == "main":
            out[c.name] = out.get(c.name, 0) + c.quantity
    return out


def _counts(deck: Deck | dict[str, int]) -> dict[str, int]:
    return deck.counts_by_name() if isinstance(deck, Deck) else dict(deck)


def compare(before: Deck | dict[str, int], after: Deck | dict[str, int]) -> dict[str, Any]:
    """Card-level differences between two decks (or name -> count maps): ``added``, ``removed``
    and ``changed`` rows, and when both sides are decks, ``stats_delta`` (after minus before)
    for the numeric statistics."""
    a, b = _counts(before), _counts(after)
    added = [{"name": n, "quantity": b[n]} for n in sorted(b) if n not in a]
    removed = [{"name": n, "quantity": a[n]} for n in sorted(a) if n not in b]
    changed = [{"name": n, "before": a[n], "after": b[n]} for n in sorted(a) if n in b and a[n] != b[n]]
    out: dict[str, Any] = {"added": added, "removed": removed, "changed": changed}
    if isinstance(before, Deck) and isinstance(after, Deck):
        sb, sa = compute(before), compute(after)
        delta: dict[str, float | int | None] = {}
        for key in _NUMERIC_STATS:
            x, y = sb.get(key), sa.get(key)
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                d = y - x
                delta[key] = d if isinstance(d, int) else _round(d)
            else:
                delta[key] = None
        out["stats_delta"] = delta
    return out
