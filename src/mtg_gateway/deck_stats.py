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

    commanders = [c for c in cards if deck.is_commander(c)]
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
        "commanders": [c.name for c in commanders],
        "colour_identity": colour_identity(cards),
        "bracket_estimate": bracket_estimate(cards, deck.format),
        "archidekt_bracket": deck.edh_bracket,
        "checks": deck_checks(deck, cards, commanders, qty),
    }


# Formats with one copy per card and an exact deck size (commander-style), by Archidekt slug.
_SINGLETON_FORMATS = {
    "commander",
    "1v1",
    "duel",
    "paupercommander",
    "predh",
    "historicbrawl",
    "canlander",
    "gladiator",
    "brawl",
    "oathbreaker",
    "tlr",
    "competitivebrawl",
}
_DECK_SIZES = {
    "commander": 100,
    "1v1": 100,
    "duel": 100,
    "paupercommander": 100,
    "predh": 100,
    "historicbrawl": 100,
    "canlander": 100,
    "gladiator": 100,
    "brawl": 60,
    "oathbreaker": 60,
    "tlr": 50,
    "competitivebrawl": 60,
}
# Constructed formats: at least 60 cards, at most 4 copies of a card, a sideboard of up to 15.
_CONSTRUCTED_FORMATS = {
    "standard",
    "modern",
    "legacy",
    "vintage",
    "pauper",
    "pioneer",
    "historic",
    "alchemy",
    "timeless",
    "premodern",
    "future",
    "frontier",
    "penny",
}
_MIN_SIZE = 60
_MAX_COPIES = 4
_SIDEBOARD_MAX = 15
_ANY_NUMBER = "any number of cards named"


def _can_command(card: DeckCard) -> bool:
    legendary = any(t.lower() == "legendary" for t in card.supertypes + card.types)
    creature = any(t.lower() == "creature" for t in card.types)
    return (legendary and creature) or "can be your commander" in card.oracle_text.lower()


def deck_checks(deck: Deck, cards: list[DeckCard], commanders: list[DeckCard], qty: int) -> dict[str, Any]:
    """Structural checks from the deck's own data: deck size for the format (exact for the
    commander-style formats, at least 60 for constructed ones), commander zone (count and
    whether each card may command), colour identity against the commanders, singleton rule or
    the four-copies limit, sideboard size (constructed), uncategorised rows. Each entry says what
    was checked; ``problems`` lists the failures in plain words. Card legality by format is
    ``legality_problems``."""
    problems: list[str] = []
    fmt = deck.format or ""
    expected = _DECK_SIZES.get(fmt)
    size: dict[str, Any] = {"actual": qty, "expected": expected, "ok": expected is None or qty == expected}
    if fmt in _CONSTRUCTED_FORMATS:
        size = {"actual": qty, "minimum": _MIN_SIZE, "ok": qty >= _MIN_SIZE}
        if not size["ok"]:
            problems.append(f"deck has {qty} cards; {fmt} wants at least {_MIN_SIZE}")
    elif not size["ok"]:
        problems.append(f"deck has {qty} cards; {fmt} wants {expected}")
    zone: dict[str, Any] = {"count": sum(c.quantity for c in commanders), "ok": True}
    if deck.format in _SINGLETON_FORMATS:
        if not commanders:
            zone["ok"] = False
            problems.append("no card in the Commander category")
        elif zone["count"] > 2:
            zone["ok"] = False
            problems.append(f"{zone['count']} cards in the Commander category (expected 1 or 2)")
        not_commanders = [c.name for c in commanders if not _can_command(c)]
        if not_commanders:
            zone["ok"] = False
            zone["cannot_command"] = not_commanders
            problems.append("not a legal commander: " + ", ".join(not_commanders))
    identity: list[dict[str, Any]] = []
    if commanders and deck.format in _SINGLETON_FORMATS:
        allowed = {x for c in commanders for x in c.color_identity}
        for c in cards:
            outside = sorted(set(c.color_identity) - allowed)
            if outside and c.color_identity:
                identity.append({"name": c.name, "outside": outside})
        if identity:
            problems.append(f"{len(identity)} card(s) outside the commander's colour identity")
    singleton: list[dict[str, Any]] = []
    if deck.format in _SINGLETON_FORMATS:
        for c in cards:
            if c.quantity > 1 and not is_land(c) and _ANY_NUMBER not in c.oracle_text.lower():
                singleton.append({"name": c.name, "quantity": c.quantity})
            elif c.quantity > 1 and is_land(c) and "basic" not in [t.lower() for t in c.supertypes]:
                singleton.append({"name": c.name, "quantity": c.quantity})
        if singleton:
            problems.append(f"{len(singleton)} card(s) with more than one copy")
    copies: list[dict[str, Any]] = []
    sideboard: dict[str, Any] | None = None
    if fmt in _CONSTRUCTED_FORMATS:
        for c in cards:
            basic = is_land(c) and "basic" in [t.lower() for t in c.supertypes]
            if c.quantity > _MAX_COPIES and not basic and _ANY_NUMBER not in c.oracle_text.lower():
                copies.append({"name": c.name, "quantity": c.quantity})
        if copies:
            problems.append(f"{len(copies)} card(s) with more than {_MAX_COPIES} copies")
        side_qty = sum(c.quantity for c in deck.side_cards if "Sideboard" in c.categories)
        sideboard = {"count": side_qty, "maximum": _SIDEBOARD_MAX, "ok": side_qty <= _SIDEBOARD_MAX}
        if not sideboard["ok"]:
            problems.append(f"sideboard has {side_qty} cards; at most {_SIDEBOARD_MAX}")
    uncategorised = sorted({c.name for c in cards if not c.categories})
    out = {
        "deck_size": size,
        "commander_zone": zone,
        "colour_identity_violations": identity,
        "singleton_violations": singleton,
        "copy_limit_violations": copies,
        "uncategorised": uncategorised,
        "problems": problems,
        "ok": not problems,
    }
    if sideboard is not None:
        out["sideboard"] = sideboard
    return out


def counts_from_text(cards: list[ListCard]) -> dict[str, int]:
    """Card counts by name for the main zone of a parsed decklist."""
    out: dict[str, int] = {}
    for c in cards:
        if c.zone == "main":
            out[c.name] = out.get(c.name, 0) + c.quantity
    return out


def _counts(deck: Deck | dict[str, int]) -> dict[str, int]:
    return deck.counts_by_name() if isinstance(deck, Deck) else dict(deck)


_BASIC_KEYS = {
    n.casefold()
    for n in (
        "Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes",
        "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
        "Snow-Covered Mountain", "Snow-Covered Forest", "Snow-Covered Wastes",
    )
}  # fmt: skip


def _match_key(name: str) -> str:
    return name.split(" // ", 1)[0].strip().casefold()


def compare(before: Deck | dict[str, int], after: Deck | dict[str, int]) -> dict[str, Any]:
    """Card-level differences between two decks (or name -> count maps): ``added``, ``removed``
    and ``changed`` rows, and when both sides are decks, ``stats_delta`` (after minus before)
    for the numeric statistics."""
    a, b = _counts(before), _counts(after)
    # Names are matched case-insensitively and by front face, so "Delver of Secrets" in one list
    # equals "Delver of Secrets // Insectile Aberration" in the other; rows keep each side's spelling.
    ka = {_match_key(n): n for n in a}
    kb = {_match_key(n): n for n in b}
    added = [{"name": kb[k], "quantity": b[kb[k]]} for k in sorted(kb) if k not in ka]
    removed = [{"name": ka[k], "quantity": a[ka[k]]} for k in sorted(ka) if k not in kb]
    changed = [
        {"name": kb[k], "before": a[ka[k]], "after": b[kb[k]]}
        for k in sorted(ka)
        if k in kb and a[ka[k]] != b[kb[k]]
    ]
    out: dict[str, Any] = {"added": added, "removed": removed, "changed": changed}
    basic = lambda row: _match_key(row["name"]) in _BASIC_KEYS  # noqa: E731
    before_size, after_size = sum(a.values()), sum(b.values())
    cut, add = [r for r in removed if not basic(r)], [r for r in added if not basic(r)]
    pct = lambda n: round(n / before_size * 100) if before_size else 0  # noqa: E731
    out["summary"] = {
        "before_size": before_size,
        "after_size": after_size,
        "cut": len(cut),
        "added": len(add),
        "kept": len([k for k in ka if k in kb and k not in _BASIC_KEYS]),
        "cut_pct": pct(len(cut)),
        "added_pct": pct(len(add)),
        "basic_land_changes": [
            {
                "name": (kb.get(k) or ka[k]),
                "before": a.get(ka.get(k, ""), 0),
                "after": b.get(kb.get(k, ""), 0),
            }
            for k in sorted(set(ka) | set(kb))
            if k in _BASIC_KEYS and a.get(ka.get(k, ""), 0) != b.get(kb.get(k, ""), 0)
        ],
    }
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
