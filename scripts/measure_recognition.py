"""How much of a deck the solo test knows what to do with (K9 in Q136; R-134, T-152): the share of a
deck's cards Mystic Forge models by itself, files as out of scope on purpose, or plays without
their effect until an assistant writes a model ("needs annotation"). Numbers only; it never gates.

    python3 -I scripts/measure_recognition.py --mf <mystic-forge checkout> DECK [DECK ...]

Each DECK is a decklist text file (one "N Card Name" per line; a "*CMDR*" or "[Commander]" mark
is fine) or a precons JSON file as docker/forge/precons.py writes it. Card data comes from
Scryfall's /cards/collection, at most 75 names a request and 2 requests a second
(https://scryfall.com/docs/api/cards/collection, read 2026-10-16); this script waits 0.6 s between
requests. It never reads Archidekt: Archidekt's terms forbid scripted queries, so the decks are
precons and the repository's own test decks, never members' decks. The classifier is Mystic
Forge's own (mystic_forge/goldfish/autoderive.py at the pinned commit), so the figure matches what
the report's "Played without their effect" list would show for the same cards.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

SCRYFALL = "https://api.scryfall.com/cards/collection"
HEADERS = {
    "User-Agent": "MTG-Assistant-Gateway-measure/1.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
BATCH, PAUSE = 75, 0.6
_LINE = re.compile(r"^\s*(\d+)x?\s+(.+?)\s*$")
_MARKS = re.compile(r"\s*(\*CMDR\*|\[Commander\]|\([A-Za-z0-9]{2,6}\)\s*[\w\-★]*|\^[^^]*\^|#.*)\s*")


def front(name: str) -> str:
    return name.split(" // ")[0].strip()


def read_decks(path: Path) -> list[tuple[str, dict[str, int]]]:
    """(deck name, {card name: copies}) for every deck in the file."""
    if path.suffix == ".csv":  # Archidekt's CSV export: Quantity and Name columns
        cards = {}
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("Name") and (row.get("Quantity") or "").isdigit():
                    cards[row["Name"]] = cards.get(row["Name"], 0) + int(row["Quantity"])
        return [(path.stem, cards)]
    data = json.loads(path.read_text("utf-8")) if path.suffix == ".json" else None
    if isinstance(data, dict) and isinstance(data.get("cards"), list):  # an Archidekt deck's JSON
        cards = {}
        for row in data["cards"]:
            name = ((row.get("card") or {}).get("oracleCard") or {}).get("name")
            cats = row.get("categories") or []
            if name and not ({"Maybeboard", "Sideboard"} & set(cats)):
                cards[name] = cards.get(name, 0) + int(row.get("quantity") or 0)
        return [(str(data.get("name") or path.stem), cards)]
    if path.suffix == ".json":
        out = []
        for p in json.loads(path.read_text("utf-8")):
            cards: dict[str, int] = {}
            for name in p.get("commander") or []:
                cards[name] = cards.get(name, 0) + 1
            for count, name in p.get("main") or []:
                cards[name] = cards.get(name, 0) + int(count)
            out.append((str(p.get("name")), cards))
        return out
    cards = {}
    for line in path.read_text("utf-8").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        name = _MARKS.sub(" ", m.group(2)).strip()
        if name:
            cards[name] = cards.get(name, 0) + int(m.group(1))
    return [(path.stem, cards)]


def fetch(names: list[str], cache: dict[str, dict]) -> list[str]:
    """Scryfall's card for each front face, into ``cache``; returns the names it does not know."""
    todo = sorted({front(n) for n in names} - set(cache))
    missing: list[str] = []
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        body = json.dumps({"identifiers": [{"name": n} for n in batch]}).encode()
        req = urllib.request.Request(SCRYFALL, data=body, headers=HEADERS, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        gone = {str(x.get("name", "")) for x in data.get("not_found", [])}
        missing += sorted(gone)
        for name, card in zip([n for n in batch if n not in gone], data.get("data", []), strict=False):
            cache[name] = card
        time.sleep(PAUSE)
    return missing


def measure(decks: list[tuple[str, dict[str, int]]], derive) -> list[dict]:  # noqa: ANN001
    cache: dict[str, dict] = {}
    missing = set(fetch([n for _, cards in decks for n in cards], cache))
    derived = {front(name): d for name, d in derive(list(cache.values())).items()}
    by_front = {key: derived.get(front(card.get("name", key))) for key, card in cache.items()}
    rows = []
    for name, cards in decks:
        tally = {"modeled": 0, "out_of_scope": 0, "needs_model": 0, "not_found": 0}
        needs = []
        for card, copies in cards.items():
            d = by_front.get(front(card))
            if front(card) in missing or d is None:
                tally["not_found"] += copies
            elif d.auto_annotated:
                tally["modeled"] += copies
            elif d.needs_annotation:
                tally["needs_model"] += copies
                needs.append(card)
            else:
                tally["out_of_scope"] += copies
        total = sum(tally.values()) or 1
        rows.append(
            {
                "deck": name,
                "cards": total,
                **tally,
                "known_share": round(100 * (tally["modeled"] + tally["out_of_scope"]) / total, 1),
                "modeled_share": round(100 * tally["modeled"] / total, 1),
                "needs_model_cards": sorted(needs),
            }
        )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mf", required=True, type=Path, help="a Mystic Forge checkout (the pinned commit)")
    ap.add_argument("--json", type=Path, help="also write the rows here")
    ap.add_argument("decks", nargs="+", type=Path)
    args = ap.parse_args()
    sys.path.insert(0, str(args.mf.resolve()))
    from mystic_forge.goldfish.autoderive import derive  # the pinned classifier

    decks = [d for p in args.decks for d in read_decks(p)]
    rows = measure(decks, derive)
    print("| Deck | Cards | Modeled | Out of scope | Needs a model | Not on Scryfall | Known |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        print(
            f"| {r['deck']} | {r['cards']} | {r['modeled']} | {r['out_of_scope']} | {r['needs_model']} "
            f"| {r['not_found']} | {r['known_share']}% |"
        )
    total = sum(r["cards"] for r in rows) or 1
    known = sum(r["modeled"] + r["out_of_scope"] for r in rows)
    print(f"\nAll decks: {round(100 * known / total, 1)}% of {total} cards known to the solo test.")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
