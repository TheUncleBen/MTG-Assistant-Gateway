"""The deck page's statistics controls in headless Chromium: Mana curve by None / Type / Category,
colours as Bar or Pie, Lands only, the out-of-identity line, quantities by subtype and keyword, and
a click on a chart part showing just those cards in the list. At a 320px phone, an open Fold
(884px) and a desktop width, with no sideways scroll. Screenshots go to MTG_SHOTS when it is set.
Skipped without Chromium."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .test_deck_menu_browser import Server, _chromium, _overflow, _page

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_SHOTS")

# Archidekt's own shapes for the fields the fake deck's CSV source leaves out.
ORACLE = {
    "Aesi, Tyrant of Gyre Strait": {"manaCost": "{4}{G}{U}", "colorIdentity": ["Green", "Blue"]},
    "Coiling Oracle": {"manaCost": "{G}{U}", "subTypes": ["Snake", "Elf", "Druid"]},
    "Eternal Witness": {"manaCost": "{1}{G}{G}", "subTypes": ["Human", "Shaman"]},
    "Counterspell": {"manaCost": "{U}{U}"},
    "Cultivate": {"manaCost": "{2}{G}"},
    "Meloku the Clouded Mirror": {
        "manaCost": "{4}{U}",
        "subTypes": ["Moonfolk", "Wizard"],
        "keywords": ["Flying"],
    },
    "Mulldrifter": {"manaCost": "{4}{U}", "subTypes": ["Elemental"], "keywords": ["Flying", "Evoke"]},
    # a red pip in a Simic deck: the out-of-identity line names it
    "Beast Within": {"manaCost": "{2}{R}"},
    "Forest": {"manaProduction": {"G": 1}, "subTypes": ["Forest"]},
    "Island": {"manaProduction": {"U": 1}, "subTypes": ["Island"]},
    "Command Tower": {"manaProduction": {"G": 1, "U": 1}},
    "Sol Ring": {"manaProduction": {"C": 2}},
}


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    for row in s.ark.decks[42]["cards"]:
        oracle = row["card"]["oracleCard"]
        oracle.update(ORACLE.get(oracle["name"], {}))
        if oracle["name"] == "Beast Within":
            row["categories"] = ["Removal"]  # a category of its own for the curve by category
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.locator("#stats").screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def _visible(page, sel: str) -> int:
    return page.evaluate(
        "s => [...document.querySelectorAll(s)].filter(e => e.offsetParent !== null).length", sel
    )


@pytest.mark.parametrize(("width", "scheme"), [(320, "dark"), (884, "dark"), (1366, "light")])
def test_stats_toggles_and_focus(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        stats = page.locator("#stats")
        stats.scroll_into_view_if_needed()
        assert _overflow(page) == 0
        # the controls show once the script runs; the plain curve is the default
        curve_by = stats.locator(".statctl[aria-label='Mana curve by']")
        assert curve_by.is_visible()
        assert curve_by.locator("[data-value=none]").get_attribute("aria-pressed") == "true"
        assert stats.locator("[data-curve=none]").is_visible()
        assert stats.locator("[data-curve=type]").is_hidden()
        _shot(page, f"stats-default-{width}-{scheme}")

        # Mana curve by Type, from the keyboard: stacked bars and a legend with counts
        btn = curve_by.locator("[data-value=type]")
        btn.focus()
        page.keyboard.press("Enter")
        assert btn.get_attribute("aria-pressed") == "true"
        assert curve_by.locator("[data-value=none]").get_attribute("aria-pressed") == "false"
        typed = stats.locator("[data-curve=type]")
        assert typed.is_visible() and stats.locator("[data-curve=none]").is_hidden()
        assert "by type" in typed.locator(".curve").get_attribute("aria-label")
        assert typed.locator(".slegend button", has_text="Creature").count() == 1
        assert typed.locator(".curve .sg.k1").count() >= 1
        assert _overflow(page) == 0
        _shot(page, f"stats-type-{width}-{scheme}")
        curve_by.locator("[data-value=category]").click()
        cat = stats.locator("[data-curve=category]")
        assert cat.is_visible()
        assert cat.locator(".slegend button", has_text="Removal").count() == 1

        # Colours: Pie, then Lands only (production from lands alone)
        colours = stats.locator(".statctl[aria-label=Colors]")
        colours.locator("[data-value=pie]").click()
        assert colours.locator("[data-value=pie]").get_attribute("aria-pressed") == "true"
        assert _visible(page, "#stats [data-src=all] .pie svg[role=img]") == 2
        assert _visible(page, "#stats [data-src=all] [data-chart=bar]") == 0
        lands = colours.locator("[data-set=lands]")
        lands.click()
        assert lands.get_attribute("aria-pressed") == "true"
        assert stats.locator("[data-src=lands]").is_visible()
        assert stats.locator("[data-src=all]").is_hidden()
        label = stats.locator("[data-src=lands] .pie svg").nth(1).get_attribute("aria-label")
        assert label.startswith("Production (lands only) by color") and "Colorless" not in label
        assert _overflow(page) == 0
        _shot(page, f"stats-pie-lands-{width}-{scheme}")

        # out of identity: Beast Within's red pip in a green-blue deck
        ooi = stats.locator(".ooi.bad")
        assert "1 pip outside the commander's color identity" in ooi.inner_text()

        # quantities by subtype and keyword
        assert stats.locator("h3", has_text="Quantity by subtype").count() == 1
        assert stats.locator("table.qty button", has_text="Flying").count() == 1

        # a click on a chart part shows just those cards; Show all cards brings the rest back
        ooi.locator("button").click()
        note = page.locator(".statfocus")
        assert note.is_visible() and "Showing 1 card" in note.inner_text()
        assert _visible(page, "#cards [data-name]") == 1
        assert page.locator("#cards [data-name='beast within']").is_visible()
        stats.locator("table.qty button", has_text="Flying").click()
        assert _visible(page, "#cards [data-name]") == 2
        assert stats.locator("table.qty button", has_text="Flying").get_attribute("aria-pressed") == "true"
        note.locator("button").click()
        assert page.locator(".statfocus").count() == 0
        assert _visible(page, "#cards [data-name]") > 40

        # the choices are kept for the next visit
        page.reload(wait_until="networkidle")
        assert page.locator("#stats [data-curve=category]").is_visible()
        assert page.locator("#stats [data-src=lands] [data-chart=pie]").is_visible()
        assert _overflow(page) == 0
        assert errors == []
        browser.close()


def test_stats_page_without_storage_still_works(server: Server) -> None:
    """localStorage that throws (a locked-down browser) leaves the toggles working."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1024)
        page.add_init_script(
            "Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }})"
        )
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        page.locator("#stats [data-set=curve][data-value=type]").click()
        assert page.locator("#stats [data-curve=type]").is_visible()
        page.locator("#stats [data-curve=type] .curveaxis .mv", has_text="3").click()
        assert page.locator(".statfocus").is_visible()
        assert errors == []
        browser.close()
