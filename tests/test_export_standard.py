"""Deck exports follow the import of the app they are for (0.7.46). One rule for rows outside the
deck: the sideboard goes along, the Maybeboard never does (Arena, MTGO, the plain list). A file
for a game client names each card once per zone. The plain list carries no Archidekt marks, and
the CSV's Label column holds the tag's name only. Golden files for deck 42 hold the whole text."""

from __future__ import annotations

import csv
import io
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from .conftest import Stack, stack_with
from .test_companion import COMPANION, linked_browser

stack_options = stack_with(COMPANION)

GOLDEN = Path(__file__).parent / "fixtures" / "exports"


def _golden(name: str, text: str) -> None:
    """``text`` must equal the golden file; MTG_WRITE_GOLDEN=1 writes it (looked at by hand)."""
    path = GOLDEN / name
    if os.environ.get("MTG_WRITE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    assert text == path.read_bytes().decode("utf-8"), name


def _setup(stack: Stack) -> None:
    """Deck 42 with a second Sol Ring row in the deck (another category), Opt in the sideboard
    and in the Maybeboard, and a colour tag on one row."""
    deck = stack.ark.decks[42]
    ring = next(c for c in deck["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    twin = {**ring, "id": 99_001, "categories": ["Ramp"], "modifier": "Foil"}
    deck["cards"].append(twin)
    ring["label"] = "Have,#37d67a"
    stack.ark.add_side_row(42, "Opt", 2, category="Sideboard")
    stack.ark.add_side_row(42, "Opt", 1)  # Maybeboard
    stack.ark.add_side_row(42, "Delver of Secrets // Insectile Aberration", 1)  # Maybeboard


async def test_mtgo_dek_has_one_element_per_name_and_zone_and_no_maybeboard(stack: Stack) -> None:
    _setup(stack)
    b = await linked_browser(stack)
    try:
        dek = (await b.http.get("/decks/42/export.dek")).text
        cards = ET.fromstring(dek).iter("Cards")
        rows = [(c.get("Name"), c.get("Quantity"), c.get("Sideboard")) for c in cards]
        keys = [(n, side) for n, _q, side in rows]
        assert len(keys) == len(set(keys)), sorted(k for k in keys if keys.count(k) > 1)
        assert ("Sol Ring", "2", "false") in rows  # two rows of the deck, one element
        assert ("Opt", "2", "true") in rows  # the sideboard only: the Maybeboard copy is not sent
        assert not any(n.startswith("Delver of Secrets") for n, _q, _s in rows)
        _golden("deck42.dek", dek)
    finally:
        await b.aclose()


async def test_plain_list_is_names_under_three_headings(stack: Stack) -> None:
    _setup(stack)
    b = await linked_browser(stack)
    try:
        plain = (await b.http.get("/decks/42/export.txt")).text
        for mark in ("*F*", "*E*", "[", "]", "^", "//", "{", "(CMR)"):
            assert mark not in plain, (mark, plain[:400])
        blocks = plain.strip().split("\n\n")
        assert [blk.splitlines()[0] for blk in blocks] == ["Commander", "Deck", "Sideboard"]
        assert blocks[0].splitlines()[1:] == ["1 Aesi, Tyrant of Gyre Strait"]
        assert "2 Sol Ring" in blocks[1].splitlines() and blocks[2].splitlines()[1:] == ["2 Opt"]
        assert "Delver of Secrets" not in plain
        _golden("deck42.plain.txt", plain)
        # the deck page's Plain list tab shows the same text
        page = (await b.http.get("/decks/42?box=export", headers={"Accept": "text/html"})).text
        pane = page.split("id='exp-main'")[1].split("</textarea>")[0]
        assert "1 Aesi, Tyrant of Gyre Strait\n" in pane and "[Commander]" not in pane
        # and so does My decks' Export box
        assert (await b.http.get("/decks/api/decks/42/export")).json()["plain"] == plain
    finally:
        await b.aclose()


async def test_csv_label_column_holds_the_tags_name(stack: Stack) -> None:
    _setup(stack)
    b = await linked_browser(stack)
    try:
        text = (await b.http.get("/decks/42/export.csv")).text
        rows = list(csv.DictReader(io.StringIO(text)))
        labels = {r["Label"] for r in rows}
        assert labels == {"", "Have"}, labels
        _golden("deck42.csv", text)
    finally:
        await b.aclose()
