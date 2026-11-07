"""Two rules on the stylesheets themselves, read from the source (no browser): no heading is
hyphenated by the browser, and every piece of text grows with the text-size setting.

- ``hyphens:auto`` is never set: Chromium's line breaker is greedy and hyphenates a word that
  would have fitted whole on the next line ("snap-shots", "ad-mins" at 100% on a 320 px phone,
  final polish P-1). A word that cannot fit wraps whole or the heading shrinks to the viewport.
- A font size in px does not grow with the root (ROOT_200, BROWSER_200): every ``font-size`` is
  in rem, em or a share of the viewport (final polish AC1), except the few named below with the
  reason each keeps its px.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "mtg_gateway"
SUFFIXES = {".py", ".css", ".js", ".html"}

# the px font sizes that stay, each with its reason
KEEP_PX = {
    # the root: the one px size everything else is measured from (14 px, as Archidekt)
    ("theme.py", "html{font-size:14px"),
    # a report exported as one file: its own root, the site's 14 px
    ("report_view.py", "html{font-size:14px"),
    # the tab bar's count: it grows with the text up to 12 px, so it stays on its icon's corner and
    # inside its tab (confirmation NA5)
    ("theme.py", "font-size:min(.72rem, 12px)"),
    # the mana symbols: a glyph drawn inside a 16 px circle, part of the icon
    ("theme.py", ".pip b{font-size:10px"),
    ("theme.py", ".pip.big b{font-size:8px"),
    ("theme.py", ".pip.sm b{font-size:9px"),
    # the tick on a picked card: a mark drawn inside its 44 px circle, part of the icon
    ("deckpage.py", "font-size:26px"),
    # the scanner's viewfinder overlays: capped so they stay inside a 280 px camera view at any text
    # size (the count must end above the name box, the caption inside the view)
    ("scan.css", "font-size:min(1rem,20px)"),
    ("scan.css", "font-size:min(.9rem,18px)"),
}


def _sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*") if p.suffix in SUFFIXES and "node_modules" not in p.parts)


def _rules(text: str) -> str:
    """The text without its comments (a comment may name the rule it forbids)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", "//")))


def test_no_stylesheet_hyphenates() -> None:
    bad = []
    for p in _sources():
        for line in _rules(p.read_text(encoding="utf-8")).splitlines():
            if re.search(r"hyphens\s*:\s*auto", line):
                bad.append(f"{p.name}: {line.strip()[:100]}")
    assert bad == [], "\n".join(bad)


def test_every_font_size_grows_with_the_text() -> None:
    found = []
    for p in _sources():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"font-size\s*:\s*([^;}\"']*\d(?:\.\d+)?px[^;}\"']*)", line):
                start = max(0, m.start() - 90)
                context = line[start : m.end()]
                if any(p.name == name and text in context for name, text in KEEP_PX):
                    continue
                found.append(f"{p.name}:{i}: font-size:{m.group(1)}")
    assert found == [], "\n".join(found)
