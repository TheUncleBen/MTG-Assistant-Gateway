"""Export-only deck formats: Arena import text, an MTGO ``.dek`` file and a printable PDF.

None of these round-trip through the gateway (Archidekt's text, CSV and the gateway's JSON do);
they exist so a deck can be taken to another program. Arena and MTGO only know the cards they
sell, so a paper-only card is simply not found on import there. Nothing here contacts a service.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from xml.sax.saxutils import quoteattr

from .archidekt import Deck, DeckCard


def _sorted(cards: list[DeckCard]) -> list[DeckCard]:
    return sorted(cards, key=lambda c: (c.name.lower(), c.set_code, c.collector_number))


def _arena_line(c: DeckCard) -> str:
    line = f"{c.quantity} {c.name}"
    if c.set_code:
        line += f" ({c.set_code.upper()})"
        if c.collector_number:
            line += f" {c.collector_number}"
    return line


def to_arena(deck: Deck) -> str:
    """Arena's import text: ``1 Name (SET) 123`` under ``Commander``, ``Companion``, ``Deck`` and
    ``Sideboard`` headings (the headings Arena's own export writes). Cards with the Archidekt
    category ``Companion`` go under Companion; other rows kept outside the deck are the sideboard,
    except the Maybeboard, which is left out (Arena has no maybeboard)."""
    commanders = [c for c in deck.main_cards if deck.is_commander(c)]
    companions = [c for c in deck.cards if "Companion" in c.categories and not deck.is_commander(c)]
    main = [c for c in deck.main_cards if c not in commanders and c not in companions]
    side = [c for c in deck.side_cards if c not in companions and "Maybeboard" not in c.categories]
    blocks: list[str] = []
    for title, rows in (
        ("Commander", commanders),
        ("Companion", companions),
        ("Deck", main),
        ("Sideboard", side),
    ):
        if rows:
            blocks.append(title + "\n" + "\n".join(_arena_line(c) for c in _sorted(rows)))
    return "\n\n".join(blocks) + "\n"


EDHPLAY_URL = "https://edhplay.com/"


# EDHPlay's Bulk Import reads "(SET) NUMBER" only when both match these (its parser, below).
_EDH_SET = re.compile(r"[A-Za-z0-9]+")
_EDH_NUMBER = re.compile(r"[A-Za-z0-9-]+")
# Archidekt categories EDHPlay's import cannot take: it reads a Tokens section but drops it
# (verified live 2026-10-11: the import request carries no token rows), so tokens are left out.
_EDH_TOKEN_CATEGORIES = {"token", "tokens"}
# The most cards EDHPlay's Sideboard holds. An import that would put more there is refused whole
# ("Sideboard would have 17 cards, max is 15", verified live 2026-10-11) and nothing is imported.
EDHPLAY_SIDEBOARD_MAX = 15


@dataclass
class EdhplayList:
    """What "Play on EDHPlay" copies, and what it had to leave out (for the note)."""

    text: str
    commanders: list[str]
    left_out: list[str]  # "2 Cultivate" lines that did not fit EDHPlay's 15-card sideboard
    maybeboard: int  # maybeboard cards, never sent (Ben, 23:21Z 2026-10-11)
    tokens: int  # token cards left out (EDHPlay's import drops them)


def _edh_line(c: DeckCard, *, commander: bool = False, quantity: int | None = None) -> str:
    """``1 Name (SET) 123``, with ``[Commander{top}]`` on a commander: the marker EDHPlay's parser
    looks for (it takes Archidekt's own export syntax for the commander)."""
    name = " ".join(c.name.replace("[", "(").replace("]", ")").split())
    line = f"{c.quantity if quantity is None else quantity} {name}"
    if c.set_code and c.collector_number and _EDH_SET.fullmatch(c.set_code):
        if _EDH_NUMBER.fullmatch(c.collector_number):
            line += f" ({c.set_code.upper()}) {c.collector_number}"
    return line + (" [Commander{top}]" if commander else "")


def to_edhplay(deck: Deck) -> EdhplayList:
    """The whole deck as EDHPlay's Bulk Import reads it, the commander names and what was left out.

    Verified 2026-10-11 against EDHPlay itself: its help page ('Click "Create Deck", choose your
    format and commander. Add cards by searching or use "Bulk Import" to paste your decklist (e.g.,
    "1 Sol Ring", "1 Command Tower")'), the import dialog's own text ("1 Sol Ring (C18) 222": "Use
    set code and collector number to specify exact printing"), the parser the dialog runs in the
    browser (edhplay.com/assets/DeckEditorSkeleton-*.js, functions ``tc``/``Vs``/``bn``) and live
    imports with a test account, read back through EDHPlay's own Export:

    - each line is ``N Name``, an optional ``x`` after N, then optional ``(SET) NUMBER``;
    - a line carrying ``[...Commander{top}...]`` is the commander, a second one the partner, and
      it wins over a commander already picked under Create Deck (with Import & Replace);
    - lines before the first ``Sideboard``, ``Tokens``, ``Attractions`` or ``Stickers`` heading are
      the main deck; each heading's section runs to the next blank line;
    - double-faced cards are found by their full ``A // B`` name;
    - EDHPlay has no maybeboard (a ``Maybeboard`` heading is skipped and its cards would count as
      main deck), its import never sets a companion, it drops a Tokens section, and its Sideboard
      holds at most 15 cards: more and the whole import is refused.

    So: commander(s) first, marked; then the main deck; then, after a blank line, ``Sideboard``
    with the deck's companion and sideboard, up to 15 copies (the companion first); anything past
    that is left out and named in the note. The maybeboard (and any other category kept outside
    the deck) is never sent (Ben's choice, 23:21Z 2026-10-11), and neither are tokens; the note
    says so. Printings keep their set and number when EDHPlay's parser can read them. A deck with
    no commander starts with a line of several copies when it has one, because EDHPlay's parser
    takes a first line of quantity 1 as the commander. Unknown: whether the 15 counts copies or
    lines; copies is the safe reading."""
    commanders: list[str] = []
    head: list[str] = []
    main: list[str] = []
    first: list[DeckCard] = []  # the sideboard and the companion
    maybeboard = tokens = 0
    for c in sorted(deck.cards, key=lambda c: (c.name.casefold(), c.set_code, c.collector_number)):
        if not c.name.strip() or c.quantity <= 0:
            continue
        if deck.in_deck(c) and deck.is_commander(c):
            head.append(_edh_line(c, commander=True))
            if c.name not in commanders:
                commanders.append(c.name)
        elif deck.in_deck(c):
            main.append(_edh_line(c))
        elif any(cat.strip().casefold() in _EDH_TOKEN_CATEGORIES for cat in c.categories):
            tokens += c.quantity
        elif c.companion or (c.categories and c.categories[0].casefold() in ("sideboard", "companion")):
            first.append(c)
        else:  # the maybeboard, or another category kept outside the deck
            maybeboard += c.quantity
    side: list[str] = []
    left_out: list[str] = []
    room = EDHPLAY_SIDEBOARD_MAX
    # the companion first, so a cut never drops it
    first.sort(key=lambda c: not (c.companion or c.categories[:1] == ["Companion"]))
    for c in first:
        fits = min(c.quantity, room)
        if fits:
            side.append(_edh_line(c, quantity=fits))
            room -= fits
        if fits < c.quantity:
            left_out.append(_edh_line(c, quantity=c.quantity - fits))
    if not head:
        # With no marked commander EDHPlay's parser takes a first line of quantity 1 as the
        # commander; a line of several copies first (a basic land, usually) keeps it from that.
        several = next((i for i, line in enumerate(main) if not line.startswith("1 ")), None)
        if several:
            main.insert(0, main.pop(several))
    text = "\n".join(head + main) + "\n"
    if side:
        text += "\nSideboard\n" + "\n".join(side) + "\n"
    return EdhplayList(text, commanders, left_out, maybeboard, tokens)


def to_mtgo_dek(deck: Deck) -> str:
    """An MTGO ``.dek`` file: the ``Deck``/``Cards`` XML the MTGO client exports, one ``Cards``
    element per row with ``Quantity``, ``Sideboard`` and ``Name``. MTGO's own catalogue ids
    (``CatID``) are not known to the gateway and are left out; the client matches by name."""
    rows = []
    for c in _sorted(deck.cards):
        side = "false" if deck.in_deck(c) else "true"
        rows.append(
            f"  <Cards Quantity={quoteattr(str(c.quantity))} Sideboard={quoteattr(side)} "
            f'Name={quoteattr(c.name)} Annotation="0" />'
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Deck xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n'
        "  <NetDeckID>0</NetDeckID>\n"
        "  <PreconstructedDeckID>0</PreconstructedDeckID>\n" + "\n".join(rows) + "\n</Deck>\n"
    )


# -- PDF ---------------------------------------------------------------------------------------
_PAGE_W, _PAGE_H = 612, 792  # US Letter in points; A4 readers scale it
_MARGIN = 54
_LINE = 14
_TITLE_SIZE, _BODY_SIZE = 16, 11


def _pdf_text(s: str) -> str:
    """A PDF literal string in WinAnsi (Helvetica's base encoding); other characters become ``?``."""
    raw = s.encode("cp1252", errors="replace").decode("cp1252")
    return "(" + raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def pdf_lines(deck: Deck) -> list[tuple[str, str]]:
    """The printable rows: ``(kind, text)`` with kind ``title``, ``head``, ``row`` or ``blank``."""
    out: list[tuple[str, str]] = [("title", deck.name)]
    meta = []
    if deck.format:
        meta.append(deck.format)
    meta.append(f"{sum(c.quantity for c in deck.main_cards)} cards")
    meta.append(f"archidekt.com/decks/{deck.id}")
    out.append(("row", " · ".join(meta)))
    groups: dict[str, list[DeckCard]] = {}
    for c in deck.cards:
        key = "Commander" if deck.is_commander(c) else (c.categories[0] if c.categories else "Uncategorized")
        if not deck.in_deck(c):
            key = c.categories[0] if c.categories else "Sideboard"
        groups.setdefault(key, []).append(c)
    order = sorted(groups, key=lambda k: (k != "Commander", k.lower()))
    for key in order:
        rows = groups[key]
        out.append(("blank", ""))
        out.append(("head", f"{key} ({sum(c.quantity for c in rows)})"))
        for c in _sorted(rows):
            text = f"{c.quantity}  {c.name}"
            if c.set_code:
                text += f"  ({c.set_code.upper()}{' ' + c.collector_number if c.collector_number else ''})"
            if c.modifier.lower() in ("foil", "etched"):
                text += f"  {c.modifier.capitalize()}"
            out.append(("row", text))
    return out


def to_pdf(deck: Deck) -> bytes:
    """A small PDF (Helvetica, one column, several pages when needed) written without a library,
    so the gateway needs no PDF dependency. Every reader opens PDF 1.4 text pages like this."""
    lines = pdf_lines(deck)
    per_page = (_PAGE_H - 2 * _MARGIN) // _LINE
    pages: list[list[tuple[str, str]]] = [lines[i : i + per_page] for i in range(0, len(lines), per_page)]
    objects: list[bytes] = []  # 1-based object numbers follow the list index + 1

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    pages_obj = add(b"")  # filled in once the page ids are known
    page_ids: list[int] = []
    for n, chunk in enumerate(pages):
        ops = ["BT"]
        y = _PAGE_H - _MARGIN
        for kind, text in chunk:
            size = _TITLE_SIZE if kind == "title" else _BODY_SIZE
            name = "/F2" if kind in ("title", "head") else "/F1"
            if kind != "blank":
                ops.append(f"{name} {size} Tf 1 0 0 1 {_MARGIN} {y - size} Tm {_pdf_text(text)} Tj")
            y -= _LINE if kind != "title" else _LINE + 6
        footer = f"{deck.name} · page {n + 1} of {len(pages)}"
        ops.append(f"/F1 8 Tf 1 0 0 1 {_MARGIN} {_MARGIN // 2} Tm {_pdf_text(footer)} Tj")
        ops.append("ET")
        stream = zlib.compress("\n".join(ops).encode("cp1252", errors="replace"))
        content = add(
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream"
        )
        page_ids.append(
            add(
                (
                    f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}] "
                    f"/Resources << /Font << /F1 {font} 0 R /F2 {bold} 0 R >> >> /Contents {content} 0 R >>"
                ).encode()
            )
        )
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_obj - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    catalog = add(f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode())
    title = _pdf_text(deck.name)
    info = add(f"<< /Title {title} /Producer (MTG Assistant Gateway) >>".encode("cp1252", errors="replace"))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)
