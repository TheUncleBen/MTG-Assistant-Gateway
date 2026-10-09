"""Mana and rules-text symbols drawn by the gateway itself: an inline SVG sprite of our own
glyphs (sun, drop, skull, flame, tree, diamond, tap, untap, snow, energy, Phyrexian) on
Archidekt-coloured discs. No fonts or images are loaded (the pages' CSP forbids them), nothing
is copied from Wizards' or Scryfall's symbol art, and the same markup serves server-rendered
pips (``mana_html``, ``rules_html``) and the card viewer's script (static/cardview.js builds the
same elements from ``data-mana`` and ``data-text``).

Symbols follow Scryfall's notation: ``{2}{G}{U}``, hybrid ``{W/U}``, Phyrexian ``{W/P}``,
``{2/W}``, ``{C}``, ``{X}``, ``{T}``, ``{Q}``, ``{S}``, ``{E}``, ``{∞}``, ``{½}``.
"""

from __future__ import annotations

import html
import re

_SYMBOL = re.compile(r"\{([^}]+)\}")
COLOURS = ("W", "U", "B", "R", "G")

# Own glyphs, 24x24, filled with currentColor (the disc's ink colour).
_GLYPHS = {
    "W": "<circle cx='12' cy='12' r='4.3'/><path d='M12 2.6v3.2M12 18.2v3.2M2.6 12h3.2M18.2 12h3.2"
    "M5.4 5.4l2.2 2.2M16.4 16.4l2.2 2.2M5.4 18.6l2.2-2.2M16.4 7.6l2.2-2.2' fill='none' "
    "stroke='currentColor' stroke-width='2.3' stroke-linecap='round'/>",
    "U": "<path d='M12 2.6c-2.9 4.6-6.3 8.1-6.3 12a6.3 6.3 0 0 0 12.6 0c0-3.9-3.4-7.4-6.3-12z'/>",
    "B": "<path fill-rule='evenodd' d='M12 2.8a7.4 7.4 0 0 0-7.4 7.4c0 2.7 1.3 4.7 3.1 5.9v2.6"
    "a1.5 1.5 0 0 0 1.5 1.5h5.6a1.5 1.5 0 0 0 1.5-1.5v-2.6c1.8-1.2 3.1-3.2 3.1-5.9A7.4 7.4 0 0 0 12 2.8z"
    "M8.9 12.9a2 2 0 1 1 0-4 2 2 0 0 1 0 4zm6.2 0a2 2 0 1 1 0-4 2 2 0 0 1 0 4zm-3.1 1.1 1.3 2.4h-2.6z'/>",
    "R": "<path d='M12.6 2.2c.7 3.3-1.1 5.2-2.6 7.1-1.3 1.7-1.6 3.4-.9 5 .4-1.4 1.2-2.4 2.3-3.1"
    "-.2 2.4 1.9 3.2 2 5.5 1.5-.9 2.4-2.4 2.4-4.1 0-3.9-2.6-6.7-3.2-10.4zM8.2 8.9C6 10.7 4.6 12.9 4.6 15.4"
    "A7.4 7.4 0 0 0 19.4 15c-.3 2.1-1.7 3.5-3.7 3.9.2-.5.3-1 .3-1.6 0-2.9-2.8-3.9-2.6-7.3-1.6.8-2.6 2-3 3.6"
    "-1.3-1-1.6-2.8-.9-4.7z'/>",
    "G": "<path d='M12 2.2 6.3 9.6h2.9L4.8 15.2h4.7L7 19.4h3.8v2.4h2.4v-2.4H17l-2.5-4.2h4.7l-4.4-5.6h2.9z'/>",
    "C": "<path d='M12 2.8 20.2 12 12 21.2 3.8 12z'/>",
    "T": "<path d='M5.2 14.2A7 7 0 0 1 15.4 5.8' fill='none' stroke='currentColor' stroke-width='2.6' "
    "stroke-linecap='round'/><path d='M19.2 3.2v7.4h-7.4z'/>"
    "<path d='M9 15.2h8.6l-1.6 6.2H10.6z'/>",
    "Q": "<path d='M18.8 14.2A7 7 0 0 0 8.6 5.8' fill='none' stroke='currentColor' stroke-width='2.6' "
    "stroke-linecap='round'/><path d='M4.8 3.2v7.4h7.4z'/>"
    "<path d='M6.4 15.2H15l-1.6 6.2H8z'/>",
    "S": "<path d='M12 2.8v18.4M4 7.4l16 9.2M4 16.6l16-9.2M12 2.8l-2.6 2.2M12 2.8l2.6 2.2M12 21.2l-2.6-2.2"
    "M12 21.2l2.6-2.2' fill='none' stroke='currentColor' stroke-width='2.1' stroke-linecap='round'/>",
    "E": "<path d='M13.6 2 4.8 13.6h5.8L9.4 22l9.8-12.2h-6.2z'/>",
    "P": "<circle cx='12' cy='12' r='8.2' fill='none' stroke='currentColor' stroke-width='2.2'/>"
    "<path d='M12 5.2v13.6M7.6 9.4c0 4 8.8 4 8.8 0' fill='none' stroke='currentColor' stroke-width='2.2' "
    "stroke-linecap='round'/>",
}

SPRITE = (
    "<svg xmlns='http://www.w3.org/2000/svg' style='position:absolute;width:0;height:0;overflow:hidden' "
    "aria-hidden='true' focusable='false'>"
    + "".join(f"<symbol id='ms-{k}' viewBox='0 0 24 24'>{v}</symbol>" for k, v in _GLYPHS.items())
    + "</svg>"
)

_WORDS = {
    "W": "white",
    "U": "blue",
    "B": "black",
    "R": "red",
    "G": "green",
    "C": "colorless",
    "T": "tap",
    "Q": "untap",
    "S": "snow",
    "E": "energy",
    "P": "Phyrexian",
    "X": "X",
    "Y": "Y",
    "Z": "Z",
}


def describe(sym: str) -> str:
    """Words for a screen reader: ``{W/U}`` -> ``white or blue``, ``{2}`` -> ``2``."""
    parts = sym.upper().split("/")
    return " or ".join(_WORDS.get(p, p) for p in parts)


def pip_html(sym: str, *, cls: str = "pip") -> str:
    """One symbol as a disc. Colours fill the disc; hybrid discs are split; the glyph or the
    number sits on top. Unknown symbols show their text."""
    raw = sym.strip()
    parts = raw.upper().split("/")
    colours = [p for p in parts if p in COLOURS]
    phy = "P" in parts
    label = html.escape(describe(raw))
    inner = ""
    classes = cls
    if colours:
        classes += f" pip-{colours[0]}"
        if len(colours) >= 2:
            classes += " hy"
            extra = f" data-b='{colours[1]}'"
        else:
            extra = ""
        glyph = "P" if phy else colours[0]
        if len(colours) == 1 and parts[0] not in COLOURS and not phy:
            # {2/W}: the number on a coloured disc
            inner = f"<b>{html.escape(parts[0])}</b>"
        else:
            inner = f"<svg viewBox='0 0 24 24'><use href='#ms-{glyph}'/></svg>"
        return f"<i class='{classes}'{extra} role='img' aria-label='{label}'>{inner}</i>"
    key = parts[0]
    if key in ("C", "T", "Q", "S", "E"):
        return (
            f"<i class='{classes} pip-{key}' role='img' aria-label='{label}'>"
            f"<svg viewBox='0 0 24 24'><use href='#ms-{key}'/></svg></i>"
        )
    if key == "P":
        return (
            f"<i class='{classes} pip-P' role='img' aria-label='{label}'>"
            "<svg viewBox='0 0 24 24'><use href='#ms-P'/></svg></i>"
        )
    text = raw if len(raw) <= 3 else raw[:3]
    size = " big" if len(text) > 1 else ""
    return f"<i class='{classes} pip-g{size}' role='img' aria-label='{label}'><b>{html.escape(text)}</b></i>"


def mana_html(cost: str, *, cls: str = "mana") -> str:
    """A mana cost such as ``{2}{G}{U}`` as discs."""
    if not cost:
        return ""
    pips = "".join(pip_html(sym) for sym in _SYMBOL.findall(cost))
    return f"<span class='{cls}'>{pips}</span>" if pips else ""


def rules_html(text: str) -> str:
    """Rules text with every ``{...}`` symbol drawn as a disc; the rest escaped, newlines kept
    for ``white-space: pre-line``."""
    out: list[str] = []
    pos = 0
    for m in _SYMBOL.finditer(text):
        out.append(html.escape(text[pos : m.start()]))
        out.append(pip_html(m.group(1), cls="pip sm"))
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)
