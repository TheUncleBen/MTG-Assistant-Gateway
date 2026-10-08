"""Tolerant reader for Archidekt's CSV deck export.

Real exports (the project fixture ``tests/fixtures/sample_deck.csv``) have
quirks the reader must survive rather than assume away:

* the header has one fewer name than the rows have fields: ``Edition date`` and
  ``Category`` are joined as ``Edition dateCategory``;
* the mana cost follows the quoted card text with no comma between them;
* the second ``Collector Number`` column holds ownership (``not owned``);
* missing prices are ``----``;
* multi-value fields (colours, hybrid costs) are comma-joined inside quotes.

The reader positions columns by the header names it recognises, splits the
joined header, and keeps unknown columns in ``extra``.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

KNOWN = {
    "quantity": "quantity",
    "name": "name",
    "edition name": "edition_name",
    "edition code": "set_code",
    "edition date": "edition_date",
    "category": "category",
    "secondary categories": "secondary_categories",
    "label": "label",
    "finish": "finish",
    "collector number": "collector_number",
    "salt": "salt",
    "colors": "colors",
    "mana value": "mana_value",
    "rarity": "rarity",
    "scryfall id": "scryfall_id",
    "types": "types",
    "price": "price",
    "card text": "card_text",
    "mana cost": "mana_cost",
}


MAX_COLUMNS = 100
MAX_ROWS = 5_000
MAX_PHYSICAL_ROWS = 2 * MAX_ROWS  # blank lines included
# The header's columns plus the split-off pieces of a hybrid mana cost; a real row is far narrower.
MAX_ROW_FIELDS = 2 * MAX_COLUMNS


class CsvError(Exception):
    pass


@dataclass
class ExportCard:
    quantity: int
    name: str
    set_code: str = ""
    collector_number: str = ""
    category: str = ""
    secondary_categories: list[str] = field(default_factory=list)
    finish: str = ""
    colors: list[str] = field(default_factory=list)
    mana_value: float | None = None
    mana_cost: str = ""
    rarity: str = ""
    types: str = ""
    scryfall_id: str = ""
    price: float | None = None
    owned: bool | None = None
    card_text: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def categories(self) -> list[str]:
        return [c for c in [self.category, *self.secondary_categories] if c]


def _split_header(raw: list[str]) -> list[str]:
    out: list[str] = []
    for h in raw:
        key = h.strip()
        low = key.lower()
        # Archidekt joins "Edition date" and "Category" into one header cell.
        if low.endswith("category") and low != "category" and low[: -len("category")].strip() in KNOWN:
            out.append(key[: -len("category")].strip())
            out.append("Category")
        else:
            out.append(key)
    return out


def _num(value: str) -> float | None:
    v = value.strip()
    if not v or v.startswith("--"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def parse_export(text: str) -> list[ExportCard]:
    """Parse an Archidekt CSV export into cards. Raises CsvError when the text is not one."""
    text = text.lstrip("﻿")
    # Rows are read one at a time and the caps checked as they come, so an oversized paste is
    # refused once it passes a cap rather than after every row has been built in memory.
    rows: list[list[str]] = []
    try:
        for physical, row in enumerate(csv.reader(io.StringIO(text)), start=1):
            if physical > MAX_PHYSICAL_ROWS:
                raise CsvError(f"the export has more than {MAX_PHYSICAL_ROWS} lines")
            if len(row) > MAX_ROW_FIELDS:
                raise CsvError(f"line {physical} has more than {MAX_ROW_FIELDS} columns")
            if not any(cell.strip() for cell in row):
                continue
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise CsvError(f"the export has more than {MAX_ROWS} rows")
    except csv.Error as exc:
        raise CsvError(f"not a CSV export: {exc}") from exc
    if len(rows) < 2:
        raise CsvError("the export has no card rows")
    header = _split_header(rows[0])
    if len(header) > MAX_COLUMNS:
        # Work per row grows with the header width; a real export has a few dozen columns.
        raise CsvError(f"the header has more than {MAX_COLUMNS} columns")
    lowered = [h.lower() for h in header]
    if "quantity" not in lowered or "name" not in lowered:
        raise CsvError("the header must contain Quantity and Name columns")
    seen_collector = 0
    keys: list[str] = []
    for h in lowered:
        if h == "collector number":
            seen_collector += 1
            keys.append("collector_number" if seen_collector == 1 else "owned")
        else:
            keys.append(KNOWN.get(h, f"extra:{h}"))
    cards: list[ExportCard] = []
    for lineno, row in enumerate(rows[1:], start=2):
        data = {k: (row[i] if i < len(row) else "") for i, k in enumerate(keys)}
        # A hybrid cost glued to the text ("...step."2G,UG,UG,U) is split by its commas into
        # trailing unlabelled fields; they are rejoined below.
        trailing = [cell.strip() for cell in row[len(keys) :]]
        try:
            qty = int(str(data.get("quantity", "")).strip() or "0")
        except ValueError as exc:
            raise CsvError(f"line {lineno}: quantity is not a number") from exc
        name = _unquote(str(data.get("name", "")))
        if not name or qty <= 0:
            raise CsvError(f"line {lineno}: missing name or quantity")
        text_cell, glued = _split_text_and_cost(data.get("card_text", ""))
        mana_cost = ",".join(p for p in [glued, data.get("mana_cost", "").strip(), *trailing] if p)
        owned_raw = str(data.get("owned", "")).strip().lower()
        cards.append(
            ExportCard(
                quantity=qty,
                name=name,
                set_code=str(data.get("set_code", "")).strip(),
                collector_number=str(data.get("collector_number", "")).strip(),
                category=_unquote(str(data.get("category", ""))),
                secondary_categories=_list(data.get("secondary_categories", "")),
                finish=str(data.get("finish", "")).strip(),
                colors=_list(data.get("colors", "")),
                mana_value=_num(str(data.get("mana_value", ""))),
                mana_cost=mana_cost.strip(),
                rarity=str(data.get("rarity", "")).strip(),
                types=str(data.get("types", "")).strip(),
                scryfall_id=str(data.get("scryfall_id", "")).strip(),
                price=_num(str(data.get("price", ""))),
                owned=None if not owned_raw else owned_raw != "not owned",
                card_text=text_cell.strip(),
                extra={k[6:]: v for k, v in data.items() if k.startswith("extra:") and v},
            )
        )
    return cards


def _list(value: Any) -> list[str]:
    return [_unquote(p) for p in _unquote(str(value)).split(",") if _unquote(p)]


def _unquote(value: str) -> str:
    """Drop the leading apostrophe the gateway's own export puts before a cell that would
    otherwise read as a spreadsheet formula."""
    s = value.strip()
    return s[1:] if s[:2] and s[0] == "'" and s[1] in ("=", "+", "-", "@") else s


def _split_text_and_cost(cell: str) -> tuple[str, str]:
    """Archidekt writes ``"<text>"3GG``: csv keeps the glued cost in the same cell.

    The cost is whatever follows the last closing quote the writer produced; in
    the parsed cell that boundary is lost, so take the trailing run of mana
    symbols (digits, WUBRGC, X, slashes, braces, commas) after the last sentence
    end. Cards with no cost (lands) end with punctuation and keep an empty cost.
    """
    s = cell.rstrip()
    i = len(s)
    allowed = set("0123456789WUBRGCXYZSP/{},")
    while i > 0 and s[i - 1] in allowed:
        i -= 1
    cost = s[i:]
    text = s[:i]
    if cost and text and text[-1] not in ".)\"'" and not text.endswith("—"):
        # The run bled into a word (for example a number inside the text); keep everything as text.
        return s, ""
    return text, cost


def to_deck_json(
    cards: list[ExportCard],
    *,
    deck_id: int = 1,
    name: str = "Imported deck",
    owner: str = "owner",
    updated_at: str = "2026-01-01T00:00:00Z",
    keep_categories: bool = False,
) -> dict[str, Any]:
    """Build an Archidekt-API-shaped deck object from exported cards (used by tests as a fixture).

    The CSV's Category column is Archidekt's suggested category, not one set on the
    deck; a live deck sends ``"categories": null`` for such cards (verified on the
    project's live fixture). So by default only Commander is kept and the rest are
    null, and the modifier is spelled the way the API spells it ("Normal").
    """
    kept = {c for card in cards for c in card.categories if keep_categories or c == "Commander"}
    categories = sorted(kept)
    return {
        "id": deck_id,
        "name": name,
        "owner": {"username": owner},
        "updatedAt": updated_at,
        "categories": [
            {"name": c, "isPremier": c == "Commander", "includedInDeck": c not in ("Maybeboard", "Sideboard")}
            for c in categories
        ],
        "cards": [
            {
                "id": 1000 + i,
                "quantity": card.quantity,
                "categories": [c for c in card.categories if c in kept] or None,
                "modifier": card.finish or "Normal",
                "card": {
                    "id": 5000 + i,
                    "collectorNumber": card.collector_number,
                    "edition": {"editioncode": card.set_code, "editionname": card.set_code},
                    "rarity": card.rarity,
                    "oracleCard": {
                        "name": card.name,
                        "manaCost": card.mana_cost,
                        "types": [card.types],
                        "text": card.card_text,
                        "cmc": card.mana_value,
                    },
                },
            }
            for i, card in enumerate(cards)
        ],
    }
