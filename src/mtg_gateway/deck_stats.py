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
            if deck.format == "paupercommander" and deck.is_commander(c) and status != "banned":
                # An uncommon leader is "not_legal" in Pauper Commander's per-card flag (that flag is
                # for the 99); deck_checks judges the commander, so only a banned one is listed here
                # (and, as there, a leader with no flag is not counted as unknown).
                continue
            if status is None:
                unknown += 1
            elif status != "legal":
                problems.append({"name": c.name, "status": status})

    commanders = [c for c in cards if deck.is_commander(c)]
    out: dict[str, Any] = {
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
    # Bracket mismatch, as archidekt.com flags it: the bracket set on the deck sits below what
    # its own cards suggest (the estimate is from Archidekt's card flags; see bracket_estimate).
    estimate = out["bracket_estimate"].get("bracket")
    bracket: dict[str, Any] = {"set": deck.edh_bracket, "estimate": estimate, "ok": True}
    if deck.edh_bracket and estimate and estimate > deck.edh_bracket:
        bracket["ok"] = False
        out["checks"]["problems"].append(
            f"bracket set to {deck.edh_bracket} but the cards suggest at least {estimate} (estimate)"
        )
        out["checks"]["ok"] = False
    out["checks"]["bracket"] = bracket
    return out


# Archidekt's formats (FORMAT_IDS), by family. Singleton formats have a commander zone unless
# listed in _NO_COMMANDER; the exact deck size and other rules per format follow the formats'
# own rules as archidekt.com's deck checks apply them (reported from its client code 2026-10-07).
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
# Singleton formats without a commander zone (Canadian Highlander, Gladiator).
_NO_COMMANDER = {"canlander", "gladiator"}
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
# "custom" (Archidekt's free format) gets the shared checks only (legality has no list for it).
_MIN_SIZE = 60
_MAX_COPIES = 4
_SIDEBOARD_MAX = 15
_ANY_NUMBER = "any number of cards named"
_TINY_LEADERS_MAX_MV = 3
# Two-commander abilities: plain Partner, "Partner with <name>", Friends forever, Choose a
# Background (with a Background enchantment), Doctor's companion (with a Time Lord Doctor).
_PARTNER_RE = re.compile(r"(?<![a-z])partner(?!\s+with)", re.IGNORECASE)
_PARTNER_WITH_RE = re.compile(r"partner with ([^\n(]+?)(?:\s*\(|\n|$)", re.IGNORECASE)


def size_problem(fmt: str, qty: int) -> str | None:
    """The deck-size problem ``deck_checks`` would report for ``qty`` cards in format ``fmt`` (the
    deck proper), in the same words; None when the size fits or the format sets none."""
    fmt = "commander" if fmt == "edh" else fmt
    if fmt in _CONSTRUCTED_FORMATS:
        return None if qty >= _MIN_SIZE else f"deck has {qty} cards; {fmt} wants at least {_MIN_SIZE}"
    expected = _DECK_SIZES.get(fmt)
    return None if expected is None or qty == expected else f"deck has {qty} cards; {fmt} wants {expected}"


def _can_command(card: DeckCard) -> bool:
    legendary = any(t.lower() == "legendary" for t in card.supertypes + card.types)
    creature = any(t.lower() == "creature" for t in card.types)
    return (legendary and creature) or "can be your commander" in card.oracle_text.lower()


def _is_type(card: DeckCard, *names: str) -> bool:
    have = {t.lower() for t in card.types + card.subtypes + card.supertypes}
    return any(n in have for n in names)


def _can_lead(card: DeckCard, fmt: str) -> bool:
    """Whether the card may sit in the format's command zone (the commander-style formats)."""
    if fmt == "oathbreaker":
        return _is_type(card, "planeswalker") or _is_type(card, "instant", "sorcery")
    if fmt == "paupercommander":
        # Pauper Commander: any creature printed at uncommon leads; it need not be legendary (the
        # rarity is checked separately). Not verified against the format's rules page from here.
        return _is_type(card, "creature", "background")
    if fmt in ("brawl", "historicbrawl", "competitivebrawl"):
        legendary = _is_type(card, "legendary")
        return legendary and (_is_type(card, "creature", "planeswalker")) or _can_command(card)
    return _can_command(card)


def _pair_ok(a: DeckCard, b: DeckCard) -> bool:
    """Whether two commanders may be paired: both have Partner, each names the other with
    Partner with, both have Friends forever, a Choose a Background card with a Background, or a
    Doctor's companion with a Time Lord Doctor."""
    ta, tb = a.oracle_text.lower(), b.oracle_text.lower()
    if _PARTNER_RE.search(ta) and _PARTNER_RE.search(tb):
        return True
    ma, mb = _PARTNER_WITH_RE.search(a.oracle_text), _PARTNER_WITH_RE.search(b.oracle_text)
    if ma and mb and ma.group(1).strip().casefold() == b.name.casefold():
        return mb.group(1).strip().casefold() == a.name.casefold()
    if "friends forever" in ta and "friends forever" in tb:
        return True
    for lead, other in ((a, b), (b, a)):
        if "choose a background" in lead.oracle_text.lower() and _is_type(other, "background"):
            return True
        if "doctor's companion" in lead.oracle_text.lower() and _is_type(other, "time lord", "doctor"):
            return True
    return False


def deck_checks(deck: Deck, cards: list[DeckCard], commanders: list[DeckCard], qty: int) -> dict[str, Any]:
    """Structural checks from the deck's own data, for every Archidekt format: deck size (exact
    for the commander-style formats, at least 60 for constructed ones), the command zone (count,
    whether each card may lead, partner pairing, Oathbreaker's planeswalker plus signature spell,
    Tiny Leaders' mana value cap, Pauper Commander's uncommon leader, unverified when the deck's
    printing is not the uncommon one), colour identity against the
    commanders, singleton rule or the four-copies limit, restricted cards (one copy), companion
    rows, sideboard size (constructed), card legality for the format (banned, not legal), the set
    bracket against the estimate, and uncategorised rows. Each entry says what was checked;
    ``problems`` lists the failures in plain words and ``ok`` is false when any check failed,
    legality included."""
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
    has_zone = fmt in _SINGLETON_FORMATS and fmt not in _NO_COMMANDER
    if has_zone:
        if not commanders:
            zone["ok"] = False
            problems.append("no card in the Commander category")
        elif zone["count"] > 2:
            zone["ok"] = False
            problems.append(f"{zone['count']} cards in the Commander category (expected 1 or 2)")
        # A Background (a legendary enchantment) may sit in the zone only beside a "Choose a
        # Background" commander: it is checked as half of that pair, not as a commander itself.
        paired = {c.name for c in commanders} if len(commanders) == 2 and _pair_ok(*commanders) else set()
        not_commanders = [c.name for c in commanders if not _can_lead(c, fmt) and c.name not in paired]
        if not_commanders:
            zone["ok"] = False
            zone["cannot_command"] = not_commanders
            problems.append("not a legal commander: " + ", ".join(not_commanders))
        if len(commanders) == 2 and zone["count"] == 2:
            a, b = commanders
            if fmt == "oathbreaker":
                pw = [c for c in (a, b) if _is_type(c, "planeswalker")]
                spell = [c for c in (a, b) if _is_type(c, "instant", "sorcery")]
                zone["pairing"] = "oathbreaker and signature spell" if pw and spell else "invalid"
                if not (pw and spell):
                    zone["ok"] = False
                    problems.append("Oathbreaker wants one planeswalker and one instant or sorcery")
            elif _pair_ok(a, b):
                zone["pairing"] = "partners"
            else:
                zone["ok"] = False
                zone["pairing"] = "invalid"
                problems.append(f"{a.name} and {b.name} cannot be commanders together (no partner ability)")
        elif fmt == "oathbreaker" and commanders:
            zone["ok"] = False
            problems.append("Oathbreaker wants an oathbreaker and a signature spell in the command zone")
        if fmt == "tlr":
            big = [c.name for c in commanders if (c.cmc or 0) > _TINY_LEADERS_MAX_MV]
            if big:
                zone["ok"] = False
                problems.append("Tiny Leaders commander above mana value 3: " + ", ".join(big))
        if fmt == "paupercommander":
            # Pauper Commander's rule is about the card, not the printing in the deck: any
            # creature ever printed at uncommon may lead, whichever printing is used (format
            # rules, pdhhomebase.com, checked 2026-10-09). The deck's data names only the chosen
            # printing's rarity, so an uncommon printing settles it and any other rarity is
            # reported as unverified rather than as a failure.
            other = [
                f"{c.name} ({c.rarity.lower()} printing)"
                for c in commanders
                if c.rarity and c.rarity.lower() != "uncommon"
            ]
            if other:
                zone["unverified"] = other
                zone["unverified_note"] = (
                    "Pauper Commander lets a creature lead if any printing of it is uncommon; "
                    "this deck uses another printing, so check the card's printings: " + ", ".join(other)
                )
    elif fmt in _NO_COMMANDER and commanders:
        zone["ok"] = False
        problems.append(f"{fmt} has no command zone; {zone['count']} card(s) sit in the Commander category")
    identity: list[dict[str, Any]] = []
    if commanders and has_zone:
        allowed = {x for c in commanders for x in c.color_identity}
        for c in cards:
            outside = sorted(set(c.color_identity) - allowed)
            if outside and c.color_identity:
                identity.append({"name": c.name, "outside": outside})
        if identity:
            problems.append(f"{len(identity)} card(s) outside the commander's colour identity")
    singleton: list[dict[str, Any]] = []
    if fmt in _SINGLETON_FORMATS:
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
    # Card legality for the format, from Archidekt's own legality flags per card: banned and not
    # legal cards are failures; a restricted card is allowed as a single copy.
    legality: dict[str, Any] = {"banned": [], "not_legal": [], "restricted_violations": [], "unknown": 0}
    if fmt and fmt != "custom":
        for c in cards:
            status = c.legalities.get(fmt)
            if fmt == "paupercommander" and c in commanders:
                # The uncommon leader is checked above and the commons rule is for the rest, but a
                # card on the format's banned list cannot lead either.
                if status == "banned":
                    legality["banned"].append(c.name)
                continue
            if status is None:
                legality["unknown"] += c.quantity
            elif status == "banned":
                legality["banned"].append(c.name)
            elif status == "restricted":
                if c.quantity > 1:
                    legality["restricted_violations"].append({"name": c.name, "quantity": c.quantity})
            elif status != "legal":
                legality["not_legal"].append(c.name)
        if legality["banned"]:
            problems.append(f"{len(legality['banned'])} banned card(s): " + ", ".join(legality["banned"][:5]))
        if legality["not_legal"]:
            problems.append(f"{len(legality['not_legal'])} card(s) not legal in {fmt}")
        if legality["restricted_violations"]:
            problems.append(
                f"{len(legality['restricted_violations'])} restricted card(s) with more than one copy"
            )
    legality["ok"] = not (legality["banned"] or legality["not_legal"] or legality["restricted_violations"])
    # Companion: at most one, flagged as such on Archidekt, with the Companion ability.
    companions = [c for c in deck.cards if c.companion]
    companion: dict[str, Any] = {"count": len(companions), "ok": True}
    if len(companions) > 1:
        companion["ok"] = False
        problems.append(f"{len(companions)} companions; at most one")
    not_companions = [c.name for c in companions if "companion" not in c.oracle_text.lower()]
    if not_companions:
        companion["ok"] = False
        companion["not_companions"] = not_companions
        problems.append("marked as companion without the Companion ability: " + ", ".join(not_companions))
    uncategorised = sorted({c.name for c in cards if not c.categories})
    family = (
        "commander"
        if has_zone
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
    }
    if sideboard is not None:
        out["sideboard"] = sideboard
    return out


def compute_from_text(cards: list[ListCard]) -> dict[str, Any]:
    """What can be said about a pasted list from its names and counts alone (no card data):
    sizes, the commander by its Commander category, singleton and uncategorised checks, and
    ``card_data: "unavailable"`` so a reader knows why the rest of ``compute`` is missing."""
    main = [c for c in cards if c.zone == "main"]
    side = [c for c in cards if c.zone != "main"]
    commanders = [c.name for c in main if "Commander" in c.categories]
    qty = sum(c.quantity for c in main)
    problems: list[str] = []
    dupes = [
        {"name": c.name, "quantity": c.quantity}
        for c in main
        if c.quantity > 1 and _match_key(c.name) not in _BASIC_KEYS and "Commander" not in c.categories
    ]
    if commanders and dupes:
        problems.append(f"{len(dupes)} card(s) with more than one copy")
    if commanders and qty != 100:
        problems.append(f"deck has {qty} cards; a Commander deck wants 100")
    if len(commanders) > 2:
        problems.append(f"{len(commanders)} cards under Commander; a deck has one or two")
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
