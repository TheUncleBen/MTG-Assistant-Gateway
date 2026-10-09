"""Text colours keep WCAG AA contrast (4.5:1 for small text) in both themes: the site's muted text
and danger headings, the account menu's chosen item, and the in-chat cards' own defaults."""

from __future__ import annotations

import re

from mtg_gateway import deckpage, theme
from mtg_gateway.approve import card_html as proposal_html
from mtg_gateway.cards import card_html


def _lum(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    c = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a: str, b: str) -> float:
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):(#[0-9a-fA-F]{6})", block))


def test_site_text_tokens_pass_in_light_and_dark() -> None:
    dark = _tokens(theme.CSS.split("@media (prefers-color-scheme: light)")[0])
    light = {**dark, **_tokens(theme._LIGHT)}
    for t in (light, dark):
        for bg in ("bg", "surface", "surface-2"):
            assert ratio(t["text-muted"], t[bg]) >= 4.5, (t["text-muted"], bg)
            assert ratio(t["danger-text"], t[bg]) >= 4.5, (t["danger-text"], bg)
        assert ratio(t["toolbar-active"], t["surface-2"]) >= 4.5  # the chosen item in the menu
    # The button red is a fill, never text: as text on the dark panel it was 2.55:1.
    assert not re.search(r"[{;]color:var\(--danger-fill\)", deckpage.DECK_CSS + theme.CSS)
    assert ".menu .on{color:var(--toolbar-active)" in theme.CSS  # the shared menu panel class


def test_card_default_text_colours_pass_on_every_card_background() -> None:
    for html in (card_html("deck-card"), proposal_html()):
        assert "--color-text-tertiary" not in html  # muted text takes the host's secondary colour
        blocks = re.findall(r":root(?:\[data-theme=dark\])?\{[^}]*--muted[^}]*\}", html)
        assert len(blocks) >= 2
        for block in blocks:
            d = dict(re.findall(r"--([a-z0-9-]+):var\(--[a-z-]+,(#[0-9a-f]{6})\)", block))
            for fg, bgs in (
                ("muted", ("bg", "bg2", "green-bg", "amber-bg")),
                ("blue", ("bg", "bg2", "blue-bg")),
                ("green", ("bg", "green-bg")),
                ("red", ("bg", "red-bg")),
                ("amber", ("bg", "amber-bg")),
            ):
                for bg in bgs:
                    assert ratio(d[fg], d[bg]) >= 4.5, (fg, d[fg], bg, d[bg])
