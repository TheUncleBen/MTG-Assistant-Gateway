"""Tolerant reader for pasted plain-text decklists.

Accepts the common shapes people paste from Archidekt, Moxfield, MTGO and
Arena exports:

    1 Sol Ring
    1x Sol Ring
    1 Sol Ring (cmr) 436
    1 Sol Ring (CMR) 436 *F* [Ramp]
    1 Sol Ring [Ramp{top}]
    Sol Ring                      (quantity defaults to 1)
    Commander:  /  // Lands  /  Sideboard   (section headers become the category)
    SB: 1 Negate                  (MTGO sideboard prefix)
    # comments and blank lines are ignored

Nothing here contacts any service.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

LINE = re.compile(
    r"^\s*(?:(?P<sb>SB:)\s*)?(?:(?P<qty>\d{1,3})\s*[xX]?\s+)?(?P<name>[^(\[\^*]+?)"
    r"(?:\s+\((?P<set>[A-Za-z0-9]{2,6})\)(?:\s+(?P<num>[A-Za-z0-9★-]+))?)?"
    r"(?:\s+\*(?P<foil>[FfEe])\*)?"
    r"(?:\s+\[(?P<cats>[^\]]*)\])?"
    r"(?:\s+\^(?P<caret>[^^]*)\^)?(?:\s+#CustomCard)?\s*$"
)
HEADER_WORDS = {
    "commander",
    "commanders",
    "deck",
    "main",
    "mainboard",
    "maindeck",
    "sideboard",
    "maybeboard",
    "companion",
    "lands",
    "creatures",
    "spells",
    "instants",
    "sorceries",
    "artifacts",
    "enchantments",
    "planeswalkers",
    "battles",
    "tokens",
    "considering",
}
SIDE_ZONES = {"sideboard", "maybeboard", "considering"}
SIDE_CATEGORY = {"sideboard": "Sideboard", "maybeboard": "Maybeboard", "considering": "Maybeboard"}
FINISH_MARKS = {"F": "Foil", "E": "Etched"}
MAX_LINE_CHARS = 300  # no card line is anywhere near this; it also bounds the regex work per line
MAX_LINES = 5000


class DecklistError(Exception):
    pass


@dataclass
class ListCard:
    quantity: int
    name: str
    set_code: str = ""
    collector_number: str = ""
    categories: list[str] = field(default_factory=list)
    foil: bool = False
    zone: str = "main"
    finish: str = ""  # "Foil", "Etched" or "" (normal); ``foil`` is True for either premium finish
    label: str = ""  # Archidekt's ^label^ (a note with a colour), kept apart from the categories
    board: str = ""  # for zone "side": the Archidekt category it came from, "Sideboard" or "Maybeboard"


def _header(line: str) -> str | None:
    s = line.strip().strip(":").strip()
    s = re.sub(r"^(?://|#)\s*", "", s)  # "// Lands" and Archidekt's "# Sideboard" headers
    s = re.sub(r"\s*\(\d+\)$", "", s)
    if s.lower() in HEADER_WORDS:
        return s
    if line.strip().endswith(":") and " " not in s and s:
        return s
    return None


def parse_decklist(text: str) -> list[ListCard]:
    cards: list[ListCard] = []
    section: str | None = None
    lines = text.replace("\r", "").split("\n")
    if len(lines) > MAX_LINES:
        raise DecklistError(f"decklist has more than {MAX_LINES} lines")
    for raw in lines:
        if len(raw) > MAX_LINE_CHARS:
            raise DecklistError(f"line longer than {MAX_LINE_CHARS} characters: {raw[:80]}")
        # Collapse whitespace runs: they are the input that made the line pattern backtrack
        # quadratically, and they carry no meaning in a card line.
        line = " ".join(raw.split())
        if not line:
            continue
        header = _header(line)  # "Sideboard", "// Lands", Archidekt's "# Sideboard"
        if header is not None:
            section = header
            continue
        if line.startswith("#") or line.startswith("//"):
            continue  # a comment
        m = LINE.match(line)
        if not m or not m.group("name").strip():
            raise DecklistError(f"could not read line: {line[:80]}")
        name = m.group("name").strip().rstrip(",")
        qty = int(m.group("qty")) if m.group("qty") else 1
        if qty <= 0 or qty > 999:
            raise DecklistError(f"bad quantity on line: {line[:80]}")
        cats: list[str] = []
        if m.group("cats"):
            cats = [re.sub(r"\{.*?\}", "", c).strip() for c in m.group("cats").split(",")]
            cats = [c for c in cats if c]
        label = (m.group("caret") or "").strip()
        board = ""
        finish = FINISH_MARKS.get((m.group("foil") or "").upper(), "")
        zone = "main"
        if m.group("sb") or (section and section.lower() in SIDE_ZONES):
            zone = "side"
            board = SIDE_CATEGORY.get((section or "").lower(), "Sideboard")
        elif section and section.lower() not in ("deck", "main", "mainboard", "maindeck") and not cats:
            cats = [section]
            if section.lower() in ("commander", "commanders"):
                cats = ["Commander"]
        cards.append(
            ListCard(
                quantity=qty,
                name=name,
                set_code=(m.group("set") or "").lower(),
                collector_number=m.group("num") or "",
                categories=cats,
                foil=bool(finish),
                zone=zone,
                finish=finish,
                label=label,
                board=board,
            )
        )
    if not cards:
        raise DecklistError("no cards found in the text")
    return merge(cards)


def merge(cards: list[ListCard]) -> list[ListCard]:
    """Combine repeated lines for the same name, printing and zone."""
    out: dict[tuple[str, str, str, str], ListCard] = {}
    # Per merged card, its categories as an ordered set: a membership test on the list made a
    # crafted list with thousands of distinct categories on one card quadratic.
    seen: dict[tuple[str, str, str, str], dict[str, None]] = {}
    for c in cards:
        key = (c.name.lower(), c.set_code, c.collector_number, c.zone)
        if key in out:
            out[key].quantity += c.quantity
            cats = seen[key]
            for cat in c.categories:
                if cat not in cats:
                    cats[cat] = None
                    out[key].categories.append(cat)
        else:
            out[key] = ListCard(**{**c.__dict__, "categories": list(c.categories)})
            seen[key] = dict.fromkeys(c.categories)
    return list(out.values())


def to_text(cards: list[ListCard], *, with_categories: bool = True, zone: str = "main") -> str:
    """Render one zone as a plain list the research and simulation tools accept.

    No section headers: Mystic Forge's parser has none and would read "Commander"
    or "Sideboard" as card names. Commanders come first because goldfish_run
    treats the first line as the commander. Returns "" when the zone is empty.
    """
    chosen = [c for c in cards if c.zone == zone]
    commanders = [c for c in chosen if "Commander" in c.categories]
    rest = [c for c in chosen if c not in commanders]
    lines = [_line(c, with_categories=False) for c in commanders]
    lines += [_line(c, with_categories=with_categories) for c in rest]
    return "\n".join(lines) + ("\n" if lines else "")


def clean_text(value: Any) -> str:
    """One line of plain text: control and format characters (newlines, bidi overrides) and
    line or paragraph separators become spaces and whitespace runs collapse. Names and categories
    end up in the diff the user reviews line by line, so one value can never forge another row."""
    s = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in str(value))
    return " ".join(s.split())


def clean_category(value: Any) -> str:
    """A category as it may appear inside one list line's ``[a,b]``: one line, and without the
    brackets and commas that delimit the list (a category read from a public deck must never
    close the brackets and start a card line of its own)."""
    return clean_text(re.sub(r"[\[\],^]", " ", clean_text(value)))


def _line(c: ListCard, *, with_categories: bool) -> str:
    s = f"{c.quantity} {clean_text(c.name)}"
    if c.set_code:
        s += f" ({clean_text(c.set_code)})"
        if c.collector_number:
            s += f" {clean_text(c.collector_number)}"
    if c.finish == "Etched":
        s += " *E*"
    elif c.foil:
        s += " *F*"
    cats = [x for x in (clean_category(cat) for cat in c.categories) if x] if with_categories else []
    if cats:
        s += " [" + ",".join(cats) + "]"
    return s
