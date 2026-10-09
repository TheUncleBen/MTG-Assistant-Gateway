"""Mana and rules-text symbols drawn by the gateway (mana.py): own glyphs on coloured discs,
hybrid and Phyrexian forms, numbers, and rules text with its symbols replaced."""

from __future__ import annotations

from mtg_gateway.mana import SPRITE, describe, mana_html, pip_html, rules_html


def test_sprite_defines_every_glyph_once() -> None:
    for k in "WUBRGCTQSEP":
        assert SPRITE.count(f"id='ms-{k}'") == 1
    assert "<symbol" in SPRITE and "viewBox='0 0 24 24'" in SPRITE


def test_colour_pips_use_the_glyphs() -> None:
    assert "class='pip pip-G'" in pip_html("G") and "href='#ms-G'" in pip_html("G")
    assert "aria-label='green'" in pip_html("g")
    hybrid = pip_html("W/U")
    assert "pip-W hy" in hybrid and "data-b='U'" in hybrid and "aria-label='white or blue'" in hybrid
    phy = pip_html("G/P")
    assert "pip-G" in phy and "href='#ms-P'" in phy
    twobrid = pip_html("2/W")
    assert "pip-W" in twobrid and "<b>2</b>" in twobrid


def test_generic_and_special_pips() -> None:
    assert "<b>2</b>" in pip_html("2") and "pip-g" in pip_html("2")
    assert "big" in pip_html("10") and "<b>10</b>" in pip_html("10")
    assert "href='#ms-T'" in pip_html("T") and "aria-label='tap'" in pip_html("T")
    assert "href='#ms-C'" in pip_html("C")
    assert "href='#ms-S'" in pip_html("S") and "href='#ms-E'" in pip_html("E")
    assert "<b>X</b>" in pip_html("X")
    assert "<b>&#x221e;</b>" in pip_html("∞") or "<b>∞</b>" in pip_html("∞")


def test_mana_html_and_rules_html() -> None:
    cost = mana_html("{2}{G}{U}")
    assert cost.startswith("<span class='mana'>") and cost.count("<i class='pip") == 3
    assert mana_html("") == ""
    rules = rules_html("({T}: Add {B}.)\nPay {W/P}: <draw>")
    assert rules.count("class='pip sm") == 3 and "{" not in rules
    assert "&lt;draw&gt;" in rules and "\n" in rules


def test_describe() -> None:
    assert describe("W/U") == "white or blue"
    assert describe("2") == "2"
    assert describe("Q") == "untap"
