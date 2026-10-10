"""Text colours keep WCAG AA contrast (4.5:1 for small text) in both themes: the site's muted text
and danger headings, the account menu's chosen item, and the in-chat cards' own defaults."""

from __future__ import annotations

import re
from pathlib import Path

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


def _blend(fg: str, alpha: float, bg: str) -> str:
    """The colour a translucent tint shows over its background (the notice backgrounds)."""
    f = [int(fg.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    b = [int(bg.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(alpha * f[i] + (1 - alpha) * b[i]):02x}" for i in range(3))


def _theme_tokens() -> tuple[dict[str, str], dict[str, str]]:
    dark = _tokens(theme.CSS.split("@media (prefers-color-scheme: light)")[0])
    light = {**dark, **_tokens(theme._LIGHT)}
    return light, dark


# Every text token the pages use on the page background, a panel and the menu / tile surface
# (4.5:1), the coloured text on its own tinted notice, the dark bars' text, and the non-text
# edges: the focus ring and the control borders (3:1, WCAG 1.4.11).
TEXT_TOKENS = (
    "text",
    "text-muted",
    "link",
    "orange-text",
    "green-text",
    "red-text",
    "blue-text",
    "danger-text",
)
TINTS = {"orange-text": "#fa890d", "green-text": "#1ebb6c", "red-text": "#ff555b", "blue-text": "#4286f4"}


def test_site_text_tokens_pass_in_light_and_dark() -> None:
    light, dark = _theme_tokens()
    for name, t in (("light", light), ("dark", dark)):
        tint_alpha = 0.16 if name == "light" else 0.14
        for token in TEXT_TOKENS:
            for bg in ("bg", "surface", "surface-2"):
                assert ratio(t[token], t[bg]) >= 4.5, (name, token, t[token], bg, t[bg])
            if token in TINTS:  # the notice: coloured text on its own tint over a panel
                tinted = _blend(TINTS[token], tint_alpha, t["surface"])
                assert ratio(t[token], tinted) >= 4.5, (name, token, tinted)
        assert ratio(t["toolbar-active"], t["surface-2"]) >= 4.5  # the chosen item in the menu
        assert ratio(t["menu-head"], t["surface-2"]) >= 4.5
        assert ratio(t["text"], t["surface-3"]) >= 4.5  # avatar initials
        assert ratio(t["toolbar-text"], t["toolbar-bg"]) >= 4.5  # the tab bar
        assert ratio(t["toolbar-active"], t["toolbar-bg"]) >= 4.5
        assert ratio(t["navbar-text"], t["navbar-bg"]) >= 4.5  # the top bar
        assert ratio("#fa890d", t["navbar-bg"]) >= 4.5  # its current link
        assert ratio(t["on-orange"], "#fa890d") >= 4.5  # text on the orange fills (badges, Liked, buttons)
        assert ratio(t["on-green"], "#1ebb6c") >= 4.5
        assert ratio("#ffffff", t["danger-fill"]) >= 4.5
        # non-text: the focus ring and the input / select edges against what they sit on
        for bg in ("bg", "surface", "surface-2"):
            assert ratio(t["focus"], t[bg]) >= 3, (name, "focus", t["focus"], bg)
            assert ratio(t["control-border"], t[bg]) >= 3, (name, "control-border", t["control-border"], bg)
        assert ratio("#fa890d", t["navbar-bg"]) >= 3  # the ring the dark bars switch back to
        for bg in ("bg", "surface", "surface-2"):  # a checked box or radio against its page
            assert ratio(t["orange-ctl"], t[bg]) >= 3, (name, "orange-ctl", t["orange-ctl"], bg)
        assert ratio(t["on-orange"], t["orange-ctl"]) >= 3  # the tick on the checked box (a state mark)
    # The button red is a fill, never text: as text on the dark panel it was 2.55:1.
    assert not re.search(r"[{;]color:var\(--danger-fill\)", deckpage.DECK_CSS + theme.CSS)
    assert ".menu .on{color:var(--toolbar-active)" in theme.CSS  # the shared menu panel class
    # the dark bars keep the orange ring; everything on an orange fill uses the dark text token
    assert ".topbar,footer.site,.banner{--focus:#fa890d}" in theme.CSS
    assert "a:hover{color:var(--orange-text)}" in theme.CSS
    assert "border:1px solid var(--control-border)" in theme.CSS
    assert "input[type=radio]:checked{border:.35rem solid var(--orange-ctl)" in theme.CSS
    assert "input[type=checkbox]:checked{background-color:var(--orange-ctl)" in theme.CSS
    for css, where in ((deckpage.DECK_CSS, "deck"), (theme.CSS, "theme")):
        for rule in re.findall(r"\{[^}]*background:var\(--orange\)[^}]*\}", css):
            assert "color:#fff" not in rule, (where, rule)


def test_no_white_text_on_orange_anywhere() -> None:
    from mtg_gateway.scan import routes

    scan_css = (Path(routes.__file__).parent / "static" / "scan.css").read_text(encoding="utf-8")
    for rule in re.findall(r"\{[^}]*background:var\(--s-accent\)[^}]*\}", scan_css):
        assert "color:#fff" not in rule, rule
    assert "--s-border:var(--control-border,#868686)" in scan_css  # the Set input's edge, 3:1
    from mtg_gateway import browse

    browse_src = Path(browse.__file__).read_text(encoding="utf-8")
    for rule in re.findall(
        r"\{[^}]*background:var\(--orange\)[^}]*\}", browse_src + deckpage.DECK_CSS + theme.CSS
    ):
        assert "#fff" not in rule, rule
    for src in (theme.CSS, deckpage.DECK_CSS):  # white text never sits on a light surface token
        assert not re.search(r"background:var\(--surface(-[23])?\);color:#fff", src)
    assert "color:var(--on-orange)" in deckpage.DECK_CSS.split(".soc.on{")[1].split("}")[0]
    assert "color:var(--on-orange)" in deckpage.DECK_CSS.split(".pendingbox summary b{")[1].split("}")[0]


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
