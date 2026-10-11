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
from .decklist import DecklistError, ListCard, parse_decklist

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


# A card's one type, in the order Archidekt picks a card's auto category, so an Artifact
# Creature counts once, as a Creature. The one owner of that order: the deck page's Type grouping
# and sorting, the split curve, the auto category and get_deck's ``shown_under`` all use it.
PRIMARY_TYPES = (
    "Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land",
)  # fmt: skip
CURVE_BUCKETS = ("0", "1", "2", "3", "4", "5", "6", "7+")
_TYPE_WORD = re.compile(r"[\s,/]+")


def primary_type(card: DeckCard) -> str:
    """The card's first type in PRIMARY_TYPES order ('Other' when it has none of them). A type is
    matched as a word in any case, so "Kindred Instant" or "artifact" count too."""
    have = {w.casefold() for t in card.types for w in _TYPE_WORD.split(t) if w}
    return next((t for t in PRIMARY_TYPES if t.casefold() in have), "Other")


def auto_category(card: DeckCard) -> str:
    """The category Archidekt shows an uncategorised card under: its oracle ``defaultCategory``
    when the deck JSON carried one, else the card's primary type, else 'Other'."""
    return card.default_category or primary_type(card)


def primary_category(card: DeckCard) -> str:
    """The row's first category (Archidekt's primary category); an uncategorised card counts
    under its auto category."""
    return card.categories[0] if card.categories else auto_category(card)


def mana_value(card: DeckCard) -> float | None:
    """The mana value the deck counts a card at: the deck's own override (Archidekt's custom mana
    value, ``customCmc``) when it has one, else the card's printed mana value. Used wherever a
    figure is worked out from mana value (curve, average, grouping, sorting, draw odds); the
    format rules (legality, companions, brackets) keep the printed one."""
    custom = getattr(card, "custom_cmc", None)
    return card.cmc if custom is None else custom


def curve_bucket(card: DeckCard) -> str | None:
    """The mana curve column of a nonland card ('0' to '6', '7+'); None for lands and cards
    without a mana value."""
    mv = mana_value(card)
    if is_land(card) or mv is None:
        return None
    return _curve_bucket(mv)


def curve_by(cards: list[DeckCard], key: Any) -> dict[str, dict[str, int]]:
    """The mana curve split by ``key(card)`` (a type or a category): per column, cards per group,
    biggest group first. The groups in a column add up to that column of ``mana_curve``."""
    out: dict[str, Counter[str]] = {b: Counter() for b in CURVE_BUCKETS}
    for c in cards:
        bucket = curve_bucket(c)
        if bucket is not None:
            out[bucket][key(c)] += c.quantity
    return {b: dict(n.most_common()) for b, n in out.items()}


def outside_pips(card: DeckCard, identity: set[str]) -> dict[str, float]:
    """The coloured pips of the card's mana cost whose colour is outside ``identity`` (a hybrid
    pip's half for an outside colour counts as half)."""
    return {k: v for k, v in mana_pips(card.mana_cost).items() if k not in identity}


def out_of_identity(cards: list[DeckCard], commanders: list[DeckCard]) -> dict[str, Any] | None:
    """Mana-cost pips outside the commanders' colour identity: ``identity`` (WUBRG letters),
    ``pips`` per outside colour (times the copies), ``cards`` with the outside ``colours`` per
    card. None when the deck has no commander."""
    if not commanders:
        return None
    identity = {colour_letter(x) for c in commanders for x in c.color_identity} - {None}
    pips: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    for c in cards:
        out = outside_pips(c, identity)  # type: ignore[arg-type]
        if not out:
            continue
        rows.append({"name": c.name, "colours": [k for k in WUBRG if k in out]})
        for k, v in out.items():
            pips[k] = pips.get(k, 0.0) + v * c.quantity
    return {
        "identity": [k for k in WUBRG if k in identity],
        "pips": {k: _round(pips[k]) for k in WUBRG if k in pips},
        "cards": rows,
    }


def counts_of(cards: list[DeckCard], field_name: str) -> dict[str, int]:
    """Cards (copies) per value of a list field such as ``subtypes`` or ``keywords``, most first
    (then by name)."""
    n: Counter[str] = Counter()
    for c in cards:
        for v in dict.fromkeys(getattr(c, field_name)):
            n[v] += c.quantity
    return dict(sorted(n.items(), key=lambda kv: (-kv[1], kv[0].casefold())))


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

    valued = [(c, mv) for c in nonlands if (mv := mana_value(c)) is not None]
    mv_total = sum(mv * c.quantity for c, mv in valued)
    mv_count = sum(c.quantity for c, _mv in valued)
    curve: Counter[str] = Counter({**{str(i): 0 for i in range(7)}, "7+": 0})
    for c, mv in valued:
        curve[_curve_bucket(mv)] += c.quantity

    pips: dict[str, float] = {}
    for c in cards:
        for colour, n in mana_pips(c.mana_cost).items():
            pips[colour] = pips.get(colour, 0.0) + n * c.quantity
    sources: dict[str, int] = {}
    land_sources: dict[str, int] = {}
    commanders = [c for c in cards if deck.is_commander(c)]
    # In the commander formats a source of any color (Command Tower, Lotus Field...) can only make
    # the commanders' colors, so sources count inside the color identity only (colorless kept).
    makes = None
    if deck.format in _COMMANDER_FORMATS and commanders:
        makes = {colour_letter(x) for c in commanders for x in c.color_identity} | {"C"}
    for c in cards:
        for colour, n in (c.mana_production or {}).items():
            if makes is not None and colour not in makes:
                continue
            sources[colour] = sources.get(colour, 0) + n * c.quantity
            if is_land(c):
                land_sources[colour] = land_sources.get(colour, 0) + n * c.quantity
    order = lambda kv: (WUBRG + "C").find(kv[0])  # noqa: E731

    types: Counter[str] = Counter()
    rarities: Counter[str] = Counter()
    for c in cards:
        for t in c.types:
            types[t] += c.quantity
        if c.rarity:
            rarities[c.rarity] += c.quantity

    priced = [c for c in cards if c.price is not None]
    price_total = sum(c.price * c.quantity for c in priced if c.price is not None)

    # Card legality comes from deck_checks, the one legality engine (archidekt.com's rules: the
    # sideboard where the format counts it, restricted cards judged by their copies).
    checks = deck_checks(deck, cards, commanders, qty)

    out: dict[str, Any] = {
        "card_count": qty,
        "distinct": len({c.name for c in cards}),
        "land_count": sum(c.quantity for c in lands),
        "nonland_count": sum(c.quantity for c in nonlands),
        "average_mana_value": _round(mv_total / mv_count) if mv_count else None,
        "mana_curve": dict(curve),
        "colour_pips": {k: _round(v) for k, v in pips.items() if k in WUBRG and v},
        "mana_sources": dict(sorted(sources.items(), key=order)),
        "mana_sources_lands": dict(sorted(land_sources.items(), key=order)),
        "mana_curve_by_type": curve_by(cards, primary_type),
        "mana_curve_by_category": curve_by(cards, primary_category),
        "type_counts": dict(types.most_common()),
        "rarity_counts": dict(rarities.most_common()),
        "subtype_counts": counts_of(cards, "subtypes"),
        "keyword_counts": counts_of(cards, "keywords"),
        "price_total": _round(price_total),
        "priced_cards": len(priced),
        "format": deck.format,
        "legality_problems": card_legality_problems(checks),
        "legality_unknown": int((checks.get("legality") or {}).get("unknown") or 0),
        "game_changers": sorted({c.name for c in cards if c.game_changer}),
        "tutors": sum(c.quantity for c in cards if c.tutor),
        "extra_turns": sum(c.quantity for c in cards if c.extra_turns),
        "mass_land_denial": sum(c.quantity for c in cards if c.mass_land_denial),
        "salt_total": _round(sum(c.salt * c.quantity for c in cards if c.salt is not None)),
        "commanders": [c.name for c in commanders],
        "colour_identity": colour_identity(cards),
        "out_of_identity": out_of_identity(cards, commanders),
        "bracket_estimate": bracket_estimate(cards, deck.format),
        "archidekt_bracket": deck.edh_bracket,
        "checks": checks,
    }
    # The bracket set on the deck is checked in deck_checks as archidekt.com checks it; the
    # estimate is shown beside it.
    out["checks"]["bracket"]["estimate"] = out["bracket_estimate"].get("bracket")
    return out


# The deck legality rules per format, as archidekt.com's own deck legality check applies them
# (its deck page's checker, read from the site's code on 2026-10-11): what is checked, on which
# cards, and the deck sizes. The commander-style formats (Archidekt's list) have a command zone.
_COMMANDER_FORMATS = {
    "commander",
    "1v1",
    "duel",
    "brawl",
    "oathbreaker",
    "paupercommander",
    "historicbrawl",
    "predh",
    "competitivebrawl",
    "tlr",
}
# Singleton formats without a command zone (Canadian Highlander, Gladiator).
_NO_COMMANDER = {"canlander", "gladiator"}
_SINGLETON_FORMATS = _COMMANDER_FORMATS | _NO_COMMANDER
_DECK_SIZES = {
    "commander": 100,
    "1v1": 100,
    "duel": 100,
    "paupercommander": 100,
    "predh": 100,
    "historicbrawl": 100,
    "competitivebrawl": 100,
    "gladiator": 100,
    "brawl": 60,
    "oathbreaker": 60,
    "tlr": 50,
}
# Canadian Highlander wants at least 100 cards (not exactly 100), and at most 10 points.
_MIN_SIZES = {"canlander": 100}
_CANLANDER_POINTS = 10
# Constructed formats: at least 60 cards, at most 4 copies of a card (sideboard copies count), a
# sideboard of up to 15. Restricted cards (one copy) exist in Vintage and Timeless only.
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
    "explorer",
}
_RESTRICTED_FORMATS = {"vintage", "timeless"}
# "custom" (Archidekt's free format) is not checked at all, as on archidekt.com.
_MIN_SIZE = 60
_MAX_COPIES = 4
_SIDEBOARD_MAX = 15
_TLR_SIDEBOARD_MAX = 10
# Cards a deck may hold more copies of than its format allows (Archidekt's list): any number of
# the basic lands and of these, up to seven Seven Dwarves and up to nine Nazgûl. A card whose
# rules text says "any number of cards named" is let through too, for printings newer than the list.
_ANY_NUMBER = "any number of cards named"
_ANY_NUMBER_NAMES = {
    n.casefold()
    for n in (
        "Relentless Rats", "Rat Colony", "Shadowborn Apostle", "Persistent Petitioners",
        "Dragon's Approach", "Slime Against Humanity", "Templar Knight", "Hare Apparent",
        "Tempest Hawk", "Cid, Timeless Artificer", "Sphinx's Approach",
    )
}  # fmt: skip
_UP_TO = {"seven dwarves": 7, "nazgûl": 9}
# Cards that may be a commander although they are not creatures (Archidekt's list).
_EXTRA_COMMANDERS = {"Grist, the Hunger Tide"}
# The ten companions and what each asks of the deck (the rest of the deck and the commanders).
_COMPANIONS = {
    "Gyruda, Doom of Depths", "Jegantha, the Wellspring", "Kaheera, the Orphanguard",
    "Keruga, the Macrosage", "Lurrus of the Dream-Den", "Lutri, the Spellchaser",
    "Obosh, the Preypiercer", "Umori, the Collector", "Yorion, Sky Nomad", "Zirda, the Dawnwaker",
}  # fmt: skip
_PERMANENT_TYPES = ("Artifact", "Creature", "Enchantment", "Land", "Planeswalker")
_KAHEERA_TYPES = {"Cat", "Elemental", "Nightmare", "Dinosaur", "Beast"}
# Zirda: words that mark an activated ability (besides a colon outside quotes).
_ZIRDA_WORDS = (
    "Cycling", "Basic landcycling", "Plainscycling", "Islandcycling", "Swampcycling", "Mountaincycling",
    "Forestcycling", "Transfigure", "Unearth", "Level up", "Outlast", "Crew", "Ninjutsu",
    "Commander ninjutsu", "Transmute", "Forecast", "Aura swap", "Reinforce", "Scavenge", "Embalm",
    "Eternalize", "Fortify", "Reconfigure", "Encore", "Craft", "Saddle", "Station",
)  # fmt: skip
_BASIC_LAND_TYPES = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}
# Commanders that let other cards in whatever their colour identity, or one more colour.
_ONE_MORE_COLOUR = {"The Prismatic Piper", "Faceless One", "Clara Oswald"}


def _is_basic(card: DeckCard) -> bool:
    return any(t.lower() == "basic" for t in card.supertypes)


def _identity_exempt(commanders: set[str], card: DeckCard) -> bool:
    """Whether a commander's own rule lets ``card`` in whatever its colour identity (Archidekt's
    per-commander exceptions)."""
    text = card.oracle_text
    if "Seluma, Light of Aysen" in commanders:
        return (
            _is_basic(card)
            or "Angel" in card.subtypes
            or "Changeling" in text
            or card.name == "Mistform Ultimus"
        )
    if "Valko Indorian" in commanders:
        return (
            _is_basic(card)
            or "Phyrexian" in card.subtypes
            or "Changeling" in text
            or card.name == "Mistform Ultimus"
        )
    if "The Unluckiest Planeswalker" in commanders:
        return _is_basic(card) or "Aura" in card.subtypes
    if "Maular, the Next Evolution" in commanders:
        return _is_basic(card) or ("Creature" in card.types and (card.cmc or 0) >= 7)
    if "Grizzlegom, Hurloon Hero" in commanders:
        return "Land" in card.types
    if "The Everforger" in commanders:
        return (
            _is_basic(card)
            or ("Creature" in card.types and "Artifact" in card.types)
            or "Equipment" in card.subtypes
        )
    return False


def size_problem(fmt: str, qty: int) -> str | None:
    """The deck-size problem ``deck_checks`` would report for ``qty`` cards in format ``fmt`` (the
    deck proper), in the same words; None when the size fits or the format sets none."""
    fmt = "commander" if fmt == "edh" else fmt
    minimum = _MIN_SIZES.get(fmt) or (_MIN_SIZE if fmt in _CONSTRUCTED_FORMATS else None)
    if minimum is not None:
        return None if qty >= minimum else f"deck has {qty} cards; {fmt} wants at least {minimum}"
    expected = _DECK_SIZES.get(fmt)
    return None if expected is None or qty == expected else f"deck has {qty} cards; {fmt} wants {expected}"


def _power_toughness(card: DeckCard) -> bool:
    front = card.faces[0] if card.faces else {}
    return bool(card.power or front.get("power")) and bool(card.toughness or front.get("toughness"))


def _is_type(card: DeckCard, *names: str) -> bool:
    have = {t.lower() for t in card.types + card.subtypes + card.supertypes}
    return any(n in have for n in names)


def _can_lead(card: DeckCard, *, brawl: bool = False, paired: bool = False) -> bool:
    """Whether the card may be a commander, as archidekt.com decides it: a creature (or a Vehicle
    or Spacecraft) with power and toughness, a planeswalker in the Brawl formats and Tiny Leaders,
    a card that says it can be your commander, a Background beside a second commander, or a card
    on Archidekt's own list. Being legendary is checked apart."""
    if _is_type(card, "creature") and (_power_toughness(card) or not (card.power or card.faces)):
        return True  # (a creature read without its power and toughness is given the benefit)
    if _power_toughness(card) and ("Vehicle" in card.subtypes or "Spacecraft" in card.subtypes):
        return True
    if brawl and _is_type(card, "planeswalker"):
        return True
    return (
        "can be your commander" in card.oracle_text
        or (paired and "Background" in card.subtypes)
        or card.name in _EXTRA_COMMANDERS
    )


_PARTNER_KINDS = ("Partner—Survivors", "Partner—Father & son", "Partner—Character select", "Friends forever")


def _pair_problem(a: DeckCard, b: DeckCard) -> str | None:
    """Why two commanders may not be paired, or None when they may: both have Partner (or the
    same kind of partner: Friends forever, Partner—Survivors...), each names the other with
    Partner with, a Choose a Background commander with a Background, or a Doctor's companion with
    a Time Lord Doctor (archidekt.com's pairing rules)."""
    ta, tb = a.oracle_text, b.oracle_text
    if any(k in ta and k in tb for k in _PARTNER_KINDS):
        return None
    if ("Choose a Background" in ta or "Choose a Background" in tb) and (
        "Background" in a.subtypes or "Background" in b.subtypes
    ):
        return None
    doctor = any("Doctor" in c.subtypes and "Time Lord" in c.subtypes for c in (a, b))
    if doctor and ("Doctor's companion" in ta or "Doctor's companion" in tb):
        return None
    plain = all("Partner" in t and "Partner—" not in t for t in (ta, tb))
    if not plain:
        return f"{a.name} and {b.name} cannot be commanders together (only partner commanders can be paired)"
    wa, wb = _PARTNER_WITH_RE.search(ta), _PARTNER_WITH_RE.search(tb)
    if (wa or wb) and not (wa and wb and wa.group(1).strip() == b.name and wb.group(1).strip() == a.name):
        return (
            f"{a.name} and {b.name} cannot be commanders together (Partner with pairs only the card it names)"
        )
    return None


_PARTNER_WITH_RE = re.compile(r"Partner with ([^\n(]+?)(?:\s*\(|\n|$)")


def _names(rows: list[Any], limit: int = 10) -> str:
    """'A, B ×2, C' from names or {name, quantity} rows, at most ``limit`` then 'and N more'."""
    shown = [
        f"{r['name']} ×{r['quantity']}"
        if isinstance(r, dict) and "quantity" in r
        else str(r["name"] if isinstance(r, dict) else r)
        for r in rows[:limit]
    ]
    more = f" and {len(rows) - limit} more" if len(rows) > limit else ""
    return ", ".join(shown) + more


def _companion_misses(name: str, pool: list[DeckCard], fmt: str, size: dict[str, Any]) -> list[str]:
    """The cards (or the reason) that break a companion's deck condition, as archidekt.com checks
    it over the deck and its commanders."""
    nonland = [c for c in pool if "Land" not in c.types]
    if name == "Gyruda, Doom of Depths":
        return [c.name for c in pool if int(c.cmc or 0) % 2]
    if name == "Obosh, the Preypiercer":
        return [c.name for c in nonland if int(c.cmc or 0) % 2 == 0]
    if name == "Keruga, the Macrosage":
        return [c.name for c in nonland if (c.cmc or 0) < 3]
    if name == "Lurrus of the Dream-Den":
        return [c.name for c in pool if (c.cmc or 0) > 2 and any(t in _PERMANENT_TYPES for t in c.types)]
    if name == "Jegantha, the Wellspring":
        out = []
        for c in pool:
            symbols = _SYMBOL_RE.findall(c.mana_cost.replace(" // ", ""))
            if len(set(symbols)) != len(symbols):
                out.append(c.name)
        return out
    if name == "Kaheera, the Orphanguard":
        return [
            c.name
            for c in pool
            if "Creature" in c.types
            and not _KAHEERA_TYPES & set(c.subtypes)
            and "Changeling" not in c.oracle_text
            and c.name != "Mistform Ultimus"
        ]
    if name == "Lutri, the Spellchaser":
        return [c.name for c in nonland if c.quantity > 1]
    if name == "Umori, the Collector":
        counts: Counter[str] = Counter()
        total = 0
        for c in nonland:
            total += c.quantity
            types = set(c.types) | ({"Creature"} if c.name == "Grist, the Hunger Tide" else set())
            for t in types:
                counts[t] += c.quantity
        if not counts or max(counts.values()) == total:
            return []
        shared = counts.most_common(1)[0][0]
        return [
            c.name
            for c in nonland
            if shared not in c.types and not (shared == "Creature" and c.name == "Grist, the Hunger Tide")
        ]
    if name == "Yorion, Sky Nomad":
        if size.get("expected"):
            return ["it cannot be used in a format with an exact deck size"]
        minimum = size.get("minimum")
        if minimum and size["actual"] < minimum + 20:
            return [f"the deck needs at least {minimum + 20} cards"]
        return []
    if name == "Zirda, the Dawnwaker":
        out = []
        for c in pool:
            if c.name == "Fluctuator":
                out.append(c.name)
                continue
            if (
                _is_basic(c)
                or _BASIC_LAND_TYPES & set(c.subtypes)
                or not any(t in _PERMANENT_TYPES for t in c.types)
            ):
                continue
            text = re.sub(r'"[^"]+"', "", re.sub(r" *\([^)]*\) *", "", c.oracle_text))
            bare = text.replace("Equipment", "").replace("Equipped", "")
            if not (any(w in text for w in _ZIRDA_WORDS) or "Equip" in bare or ":" in text):
                out.append(c.name)
        return out
    return []


def _bracket_mismatches(bracket: int | None, pool: list[DeckCard]) -> list[str]:
    """What the deck holds that the bracket set on it (the owner's setting) does not allow, as
    archidekt.com flags a "Commander Bracket mis-match": bracket 1 allows no game changers, mass
    land denial, extra-turn cards or two-card infinite combos; bracket 2 none of those but extra
    turns; bracket 3 up to three game changers and no mass land denial. Nothing for 4, 5 or unset."""
    if bracket not in (1, 2, 3):
        return []
    ids = {c.oracle_id for c in pool if c.oracle_id}
    changers = sorted({c.name for c in pool if c.game_changer})
    mld = sorted({c.name for c in pool if c.mass_land_denial})
    turns = sorted({c.name for c in pool if c.extra_turns})
    combos = sorted({c.name for c in pool if any(i in ids for i in c.atomic_combos)})
    out = []
    if changers and (bracket < 3 or len(changers) > 3):
        out.append(
            f"game changers ({_names(changers)})"
            if bracket < 3
            else f"more than three game changers ({_names(changers)})"
        )
    if mld:
        out.append(f"mass land denial ({_names(mld)})")
    if turns and bracket == 1:
        out.append(f"extra-turn cards ({_names(turns)})")
    if combos and bracket < 3:
        out.append(f"two-card infinite combos ({_names(combos)})")
    return out


def deck_checks(deck: Deck, cards: list[DeckCard], commanders: list[DeckCard], qty: int) -> dict[str, Any]:
    """The deck's legality for its format, check for check as archidekt.com's own legality check
    makes it (read from the site's code, 2026-10-11): deck size (exact for most commander-style
    formats, a minimum elsewhere), the command zone (a commander present, whether it may lead and
    is legendary, partner pairing, Brawl's single commander, Oathbreaker's planeswalker plus
    signature spell, Pauper EDH's leader, Duel Commander's banned-as-commander list), colour
    identity against the commanders (with the commanders that bend it), the singleton rule or
    the four-copies limit (copies counted by name across rows and the sideboard, with the cards
    allowed in any number), card legality for the format (banned, not legal; restricted cards in
    Vintage and Timeless), sideboard size, Canadian Highlander points, the companion and its
    condition, and the bracket set on the deck. ``problems`` lists every failure in plain words
    with the cards concerned; ``legal`` is the deck's verdict (every rule met, the bracket set on
    the deck aside, which archidekt.com reports as a mismatch rather than as illegal)."""
    problems: list[str] = []
    fmt = deck.format or ""
    commander_fmt = fmt in _COMMANDER_FORMATS
    zone_cards = commanders if commander_fmt else []
    in_zone = {id(c) for c in zone_cards}
    rest = [c for c in cards if id(c) not in in_zone]
    # The sideboard: the rows filed under "Sideboard" first.
    side = [c for c in deck.cards if c.categories and c.categories[0] == "Sideboard"]
    names = {c.name for c in zone_cards}

    # Deck size: exact, or a minimum (Whtz, the Bibliophile turns Commander's 100 into a minimum).
    expected = _DECK_SIZES.get(fmt)
    minimum = _MIN_SIZES.get(fmt) or (_MIN_SIZE if fmt in _CONSTRUCTED_FORMATS else None)
    if expected and "Whtz, the Bibliophile" in names:
        expected, minimum = None, expected
    if minimum:
        size: dict[str, Any] = {"actual": qty, "minimum": minimum, "ok": qty >= minimum}
        if not size["ok"]:
            problems.append(f"deck has {qty} cards; {fmt} wants at least {minimum}")
    else:
        size = {"actual": qty, "expected": expected, "ok": expected is None or qty == expected}
        if not size["ok"]:
            problems.append(f"deck has {qty} cards; {fmt} wants {expected}")

    # The command zone.
    zone: dict[str, Any] = {"count": sum(c.quantity for c in commanders), "ok": True}
    zone_problems: list[str] = []
    allowed_ident = {x for c in zone_cards for x in c.color_identity}
    if commander_fmt and fmt == "oathbreaker":
        breakers = [
            c for c in zone_cards if _is_type(c, "planeswalker") or "can be your commander" in c.oracle_text
        ]
        spells = [c for c in zone_cards if _is_type(c, "instant", "sorcery")]
        allowed_ident = {x for c in breakers for x in c.color_identity}
        if not breakers:
            zone_problems.append(
                "Oathbreaker wants an oathbreaker (a planeswalker) in the Commander category"
            )
        elif len(breakers) > 2:
            zone_problems.append("more than one oathbreaker: " + _names([c.name for c in breakers]))
        elif len(breakers) == 2 and (why := _pair_problem(*breakers)):
            zone_problems.append(why)
        if not spells:
            zone_problems.append(
                "Oathbreaker wants a signature spell (an instant or sorcery) in the Commander category"
            )
        elif breakers and len(spells) != len(breakers):
            zone_problems.append(
                f"{len(spells)} signature spells for {len(breakers)} oathbreaker(s); each oathbreaker has one"
            )
        zone["pairing"] = "oathbreaker and signature spell" if breakers and spells else "invalid"
    elif commander_fmt:
        brawl = fmt in ("brawl", "historicbrawl", "competitivebrawl", "tlr")
        if not commanders:
            zone_problems.append(
                "no card in the Commander category"
                + (" (Brawl wants a creature or planeswalker there)" if brawl and fmt != "tlr" else "")
            )
        elif too_many := _zone_count_problem(zone["count"]):
            zone_problems.append(too_many)
        elif fmt == "brawl" and len(commanders) > 1:
            zone_problems.append("Standard Brawl allows one commander")
        if fmt == "competitivebrawl":
            cannot = [c.name for c in commanders if not _is_type(c, "creature", "planeswalker")]
        else:
            cannot = [
                c.name for c in commanders if not _can_lead(c, brawl=brawl, paired=len(commanders) == 2)
            ]
        if cannot:
            zone["cannot_command"] = cannot
            zone_problems.append("not a legal commander: " + ", ".join(cannot))
        if fmt != "paupercommander":
            plain = [
                c.name
                for c in commanders
                if c.name not in cannot and not any(t.lower() == "legendary" for t in c.supertypes)
            ]
            if plain:
                zone_problems.append("a commander must be legendary: " + ", ".join(plain))
        if len(commanders) == 2:
            why = _pair_problem(*commanders)
            zone["pairing"] = "invalid" if why else "partners"
            if why:
                zone_problems.append(why)
        if fmt == "duel":
            banned = [c.name for c in commanders if c.legalities.get(fmt) == "restricted"]
            if banned:
                zone_problems.append("banned as a commander in Duel Commander: " + ", ".join(banned))
        if fmt == "paupercommander":
            # Archidekt's own flag says whether a card may lead (a creature printed at uncommon,
            # in any printing). Without it, the printing in the deck is all there is: an uncommon
            # printing settles it and any other is reported as unverified, never as a failure.
            invalid = [c.name for c in commanders if c.is_pdh_commander is False and c.name not in cannot]
            if invalid:
                zone_problems.append(
                    "not a valid Pauper EDH commander (it must be a creature printed at uncommon): "
                    + ", ".join(invalid)
                )
            other = [
                f"{c.name} ({c.rarity.lower()} printing)"
                for c in commanders
                if c.is_pdh_commander is None and c.rarity and c.rarity.lower() != "uncommon"
            ]
            if other:
                zone["unverified"] = other
                zone["unverified_note"] = (
                    "Pauper Commander lets a creature lead if any printing of it is uncommon; "
                    "this deck uses another printing, so check the card's printings: " + ", ".join(other)
                )
    if zone_problems:
        zone["ok"] = False
        problems.extend(zone_problems)

    # Colour identity, against the commanders (with the exceptions some commanders make).
    identity: list[dict[str, Any]] = []
    if commander_fmt and zone_cards:
        extra: set[str] = set()
        if names & _ONE_MORE_COLOUR or "Tolabow, Loch Rascal" in names:
            # One colour outside the commanders' identity is allowed: the first one met.
            for c in [*cards, *side]:
                if _is_basic(c):
                    continue
                outside = set(c.color_identity) - allowed_ident
                if len(outside) == 1 and not extra:
                    extra = outside
                elif outside - extra and extra:
                    problems.append(
                        "the commander lets in one extra color only; more than one is used (" + c.name + ")"
                    )
                    break
        tolabow = "Tolabow, Loch Rascal" in names
        for c in cards:  # the commanders too: an Oathbreaker's signature spell is held to its colors
            if _identity_exempt(names, c) or (tolabow and _is_basic(c)):
                continue
            ident = allowed_ident | (extra if not tolabow or _is_type(c, "instant", "sorcery") else set())
            outside = sorted(set(c.color_identity) - ident)
            if outside:
                identity.append({"name": c.name, "outside": outside})
        if identity:
            problems.append(
                "outside the commander's color identity: "
                + _names([f"{r['name']} ({', '.join(r['outside'])})" for r in identity])
            )

    # Copies, counted by name across the deck's rows and its sideboard.
    copies_by_name: Counter[str] = Counter()
    for c in [*cards, *side]:
        copies_by_name[c.name] += c.quantity

    def over_limit(pool: list[DeckCard], limit: int) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for c in pool:
            if _over_copy_limit(c.name, copies_by_name[c.name], limit, c.oracle_text):
                out.setdefault(c.name, {"name": c.name, "quantity": copies_by_name[c.name]})
        return list(out.values())

    singleton: list[dict[str, Any]] = []
    copies: list[dict[str, Any]] = []
    if fmt in _SINGLETON_FORMATS:
        singleton = over_limit(rest if fmt in _NO_COMMANDER else cards, 1)
        if singleton:
            problems.append("more than one copy in a singleton format: " + _names(singleton))
    elif fmt in _CONSTRUCTED_FORMATS:
        copies = over_limit([*cards, *side], _MAX_COPIES)
        if copies:
            problems.append(f"more than {_MAX_COPIES} copies: " + _names(copies))

    # Card legality for the format, from the cards' own legality flags (a card without one for
    # the format is counted as unknown and not judged, as on archidekt.com).
    legality: dict[str, Any] = {"banned": [], "not_legal": [], "restricted_violations": [], "unknown": 0}
    if fmt and fmt != "custom":
        pool = rest if fmt in _NO_COMMANDER else [*cards, *side] if fmt in _CONSTRUCTED_FORMATS else cards
        seen: set[str] = set()
        for c in pool:
            status = c.legalities.get(fmt)
            if fmt == "paupercommander" and id(c) in in_zone:
                # The leader is judged above (an uncommon creature is "not legal" among the 99), but
                # a card on the format's banned list cannot lead either.
                if status == "banned":
                    legality["banned"].append(c.name)
                continue
            if status is None:
                legality["unknown"] += c.quantity
            elif c.name in seen:
                continue
            elif status == "banned":
                legality["banned"].append(c.name)
            elif status == "not_legal":
                legality["not_legal"].append(c.name)
            elif status == "restricted" and fmt in _RESTRICTED_FORMATS and copies_by_name[c.name] > 1:
                legality["restricted_violations"].append({"name": c.name, "quantity": copies_by_name[c.name]})
            seen.add(c.name)
        if legality["banned"]:
            problems.append("banned: " + _names(legality["banned"]))
        if legality["not_legal"]:
            problems.append(f"not legal in {fmt}: " + _names(legality["not_legal"]))
        if legality["restricted_violations"]:
            problems.append("restricted, more than one copy: " + _names(legality["restricted_violations"]))
    legality["ok"] = not (legality["banned"] or legality["not_legal"] or legality["restricted_violations"])

    # The sideboard's size and Canadian Highlander's points.
    sideboard: dict[str, Any] | None = None
    side_max = _SIDEBOARD_MAX if fmt in _CONSTRUCTED_FORMATS else _TLR_SIDEBOARD_MAX if fmt == "tlr" else None
    if side_max is not None:
        side_qty = sum(c.quantity for c in side)
        sideboard = {"count": side_qty, "maximum": side_max, "ok": side_qty <= side_max}
        if not sideboard["ok"]:
            problems.append(f"sideboard has {side_qty} cards; at most {side_max}")
    points: dict[str, Any] | None = None
    if fmt == "canlander":
        total = sum(c.canlander_points or 0 for c in rest)
        points = {"total": total, "maximum": _CANLANDER_POINTS, "ok": total <= _CANLANDER_POINTS}
        if not points["ok"]:
            problems.append(f"{total} Canadian Highlander points; at most {_CANLANDER_POINTS}")

    # The companion: flagged as such on Archidekt; one of the ten companions; inside the
    # commander's colors and a single copy in the commander formats; its condition met.
    companions = [c for c in deck.cards if c.companion]
    companion: dict[str, Any] = {"count": len(companions), "ok": True}
    if fmt and fmt != "custom" and fmt != "gladiator":
        not_companions = [c.name for c in companions if c.name not in _COMPANIONS]
        if not_companions:
            companion["not_companions"] = not_companions
            problems.append("marked as companion but not a companion: " + ", ".join(not_companions))
        for c in companions:
            if c.name not in _COMPANIONS:
                continue
            why: list[str] = []
            if commander_fmt:
                if set(c.color_identity) - allowed_ident:
                    why.append("outside the commander's color identity")
                if copies_by_name[c.name] > 1:
                    why.append("more than one copy")
                if c.name == "Lutri, the Spellchaser":
                    why.append("banned as a companion")
            misses = _companion_misses(c.name, [*rest, *zone_cards], fmt, size)
            if misses:
                why.append("its condition is not met: " + _names(sorted(set(misses))))
            if why:
                companion.setdefault("problems", []).append({"name": c.name, "reasons": why})
                problems.append(f"companion {c.name}: " + "; ".join(why))
        companion["ok"] = not not_companions and not companion.get("problems")

    uncategorised = sorted({c.name for c in cards if not c.categories})
    family = (
        "commander"
        if commander_fmt
        else "highlander"
        if fmt in _NO_COMMANDER
        else "constructed"
        if fmt in _CONSTRUCTED_FORMATS
        else "custom"
    )
    out = {
        "format_family": family,
        "deck_size": size,
        "commander_zone": zone,
        "colour_identity_violations": identity,
        "singleton_violations": singleton,
        "copy_limit_violations": copies,
        "legality": legality,
        "companion": companion,
        "uncategorised": uncategorised,
        "problems": problems,
        "ok": not problems,
        # The deck's verdict as archidekt.com's Legality indicator gives it: every format rule
        # above met, the deck size included, not just every card legal. The bracket set on the
        # deck is checked below and listed in ``problems`` but, as on archidekt.com, a bracket
        # mismatch alone does not make the deck illegal.
        "legal": bool(fmt) and not problems,
        "legal_problems": list(problems),
    }
    if sideboard is not None:
        out["sideboard"] = sideboard
    if points is not None:
        out["canlander_points"] = points
    mismatches = (
        _bracket_mismatches(deck.edh_bracket, [*commanders, *rest, *side]) if fmt == "commander" else []
    )
    out["bracket"] = {"set": deck.edh_bracket, "ok": not mismatches}
    if mismatches:
        out["bracket"]["mismatches"] = mismatches
        problems.append(f"bracket set to {deck.edh_bracket} but the deck has " + "; ".join(mismatches))
        out["ok"] = False
    return out


def _over_copy_limit(name: str, copies: int, limit: int, oracle_text: str = "") -> bool:
    """Whether ``copies`` of a card break a ``limit``-copies rule, as deck_checks counts it: the
    basic lands and the cards a deck may hold any number of are exempt, Seven Dwarves and Nazgûl
    may have up to seven and nine. Shared with the pasted-list check, which has names only."""
    key = _match_key(name)
    if key in _BASIC_KEYS or key in _ANY_NUMBER_NAMES or _ANY_NUMBER in (oracle_text or "").lower():
        return False
    return copies > max(limit, _UP_TO.get(key, 0))


def _zone_count_problem(count: int) -> str | None:
    """deck_checks' words for a Commander category holding more than two cards."""
    return f"{count} cards in the Commander category (expected 1 or 2)" if count > 2 else None


def card_legality_problems(checks: dict[str, Any] | None) -> list[dict[str, str]]:
    """The cards deck_checks finds not legal in the format, as ``{name, status}`` rows (status
    "banned", "not_legal", or "restricted" for a restricted card held in more than one copy).
    This is what ``compute``'s ``legality_problems`` holds, so every reader of it (the report's
    stats strip, the deck page's edit answers) gives the deck page's verdict."""
    legality = (checks or {}).get("legality") or {}
    return (
        [{"name": str(n), "status": "banned"} for n in legality.get("banned") or []]
        + [{"name": str(n), "status": "not_legal"} for n in legality.get("not_legal") or []]
        + [
            {"name": str(r.get("name")), "status": "restricted"}
            for r in legality.get("restricted_violations") or []
            if isinstance(r, dict)
        ]
    )


def legality_problems_of(stats: dict[str, Any] | None) -> list[Any]:
    """``legality_problems`` of stored statistics, from their deck_checks block when they have
    one (statistics stored before the two engines were merged kept an older list that flagged
    restricted cards and left the sideboard out); else the stored list as it is."""
    stats = stats or {}
    checks = stats.get("checks")
    if isinstance(checks, dict) and isinstance(checks.get("legality"), dict):
        return card_legality_problems(checks)
    return list(stats.get("legality_problems") or [])


def compute_from_text(cards: list[ListCard]) -> dict[str, Any]:
    """What can be said about a pasted list from its names and counts alone (no card data):
    sizes, the commander by its Commander category, singleton and uncategorised checks, and
    ``card_data: "unavailable"`` so a reader knows why the rest of ``compute`` is missing. A list
    with a commander is held to deck_checks' Commander rules, in its words (size, the Commander
    category, the singleton rule over the deck proper)."""
    main = [c for c in cards if c.zone == "main"]
    side = [c for c in cards if c.zone != "main"]
    commanders = [c.name for c in main if "Commander" in c.categories]
    qty = sum(c.quantity for c in main)
    problems: list[str] = []
    dupes = singleton_from_counts(counts_from_text(cards))
    if commanders and dupes:
        problems.append("more than one copy in a singleton format: " + _names(dupes))
    if commanders and (size := size_problem("commander", qty)):
        problems.append(size)
    if too_many := _zone_count_problem(len(commanders)):
        problems.append(too_many)
    uncategorised = sorted({c.name for c in main if not c.categories})
    return {
        "card_count": qty,
        "distinct": len({c.name for c in main}),
        "sideboard_count": sum(c.quantity for c in side),
        "commanders": commanders,
        "card_data": "unavailable",
        "checks": {
            "deck_size": {
                "actual": qty,
                "expected": 100 if commanders else None,
                "ok": not commanders or qty == 100,
            },
            "commander_zone": {"count": len(commanders), "ok": len(commanders) <= 2},
            "singleton_violations": dupes if commanders else [],
            "uncategorised": uncategorised,
            "problems": problems,
            "ok": not problems,
        },
    }


def counts_from_text(cards: list[ListCard]) -> dict[str, int]:
    """Card counts by name for the main zone of a parsed decklist."""
    out: dict[str, int] = {}
    for c in cards:
        if c.zone == "main":
            out[c.name] = out.get(c.name, 0) + c.quantity
    return out


def singleton_from_counts(counts: dict[str, int]) -> list[dict[str, Any]]:
    """The names a singleton deck holds more than one copy of, from names and counts alone (a
    pasted list): copies summed by name over rows, the basic lands and the cards a deck may hold
    any number of (or up to seven or nine) left out, by deck_checks' own rule."""
    return [{"name": name, "quantity": n} for name, n in counts.items() if _over_copy_limit(name, n, 1)]


def validation_gateway_checks(decklist: str) -> tuple[list[dict[str, Any]], str] | None:
    """The gateway's own checks of a list sent to validate_decklist (which checks names, size and
    color identity only): the singleton rule, copies counted by name. Returns the violations and
    the section the result gets; None when the list cannot be read."""
    try:
        cards = parse_decklist(decklist)
    except (DecklistError, ValueError):
        return None
    dupes = singleton_from_counts(counts_from_text(cards))
    body = (
        "**more than one copy (singleton):** " + _names(dupes, 30)
        if dupes
        else "Singleton: no card has more than one copy."
    )
    return dupes, (
        "## Gateway checks\n"
        + body
        + "\nCopies are counted by name over every line; basic lands and the cards a deck may hold "
        "any number of are exempt. Banned cards and the other format rules: deck_stats on the deck."
    )


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


def is_basic_land(name: str) -> bool:
    """True for the basic lands (snow-covered and Wastes included), matched by front face."""
    return _match_key(name) in _BASIC_KEYS


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
    before_size, after_size = sum(a.values()), sum(b.values())
    cut = [r for r in removed if not is_basic_land(r["name"])]
    add = [r for r in added if not is_basic_land(r["name"])]
    # Cards (not rows), basics left out: what was taken out as a share of the deck it came from
    # and what was put in as a share of the deck it went into, so a 5-card list upgraded to a
    # 67-card deck reads "65 put in (97%)", not "1300%".
    cut_cards, add_cards = sum(r["quantity"] for r in cut), sum(r["quantity"] for r in add)
    pct = lambda n, size: round(n / size * 100) if size else 0  # noqa: E731
    out["summary"] = {
        "before_size": before_size,
        "after_size": after_size,
        "cut": cut_cards,
        "added": add_cards,
        "kept": sum(b[kb[k]] for k in ka if k in kb and k not in _BASIC_KEYS),  # cards too
        "cut_pct": pct(cut_cards, before_size),
        "added_pct": pct(add_cards, after_size),
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
