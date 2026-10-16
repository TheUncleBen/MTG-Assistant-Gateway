"""The long-ChatGPT-name warnings in headless Chromium: on the ChatGPT install page's plugin-file
step and on the admin overview, at 360, 720 (Fold) and 1280 px, light and dark, the warning shows,
reads at a 4.5:1 contrast or better, and nothing scrolls sideways. Set MTG_CHATGPT_NAME_SHOTS=<dir>
to also save screenshots. Skipped without a Chromium build."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import SIZES, _chromium, _page
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_CHATGPT_NAME_SHOTS")
# 85 characters, no MTG_CHATGPT_NAME
LONG = "Ben's Thursday Commander Night Deck Research and Goldfish Testing Gateway for the Pod"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, server_name=LONG, admin_group="mtg-admins")
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _admin_sign_in(server: Server) -> str:
    sid = server.sign_in()
    server.db.upsert_user(
        "user-1", email="alice@example.test", name="Alice", preferred_username="alice", groups=["mtg-admins"]
    )
    return sid


def _shot(page, locator, name: str, pad: int = 24) -> None:
    """The element and a margin round it, scrolled to the middle of the window (clear of the
    fixed top bar)."""
    if not SHOTS:
        return
    Path(SHOTS).mkdir(parents=True, exist_ok=True)
    locator.evaluate("el => el.scrollIntoView({block: 'center'})")
    box = locator.bounding_box()
    vw = page.evaluate("document.documentElement.clientWidth")
    x = max(0, box["x"] - pad)
    clip = {
        "x": x,
        "y": max(0, box["y"] - pad),
        "width": min(vw, box["x"] + box["width"] + pad) - x,
        "height": box["height"] + 2 * pad,
    }
    page.screenshot(path=str(Path(SHOTS) / name), clip=clip)


def _bounds(page, sel: str) -> dict:
    return page.evaluate(
        "(sel) => { const r = document.querySelector(sel).getBoundingClientRect();"
        " return {left: r.left, right: r.right, vw: document.documentElement.clientWidth}; }",
        sel,
    )


@pytest.mark.parametrize("width,theme", SIZES)
def test_install_warning_reads_everywhere(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/install?for=chatgpt", wait_until="networkidle")
        note = page.locator(".notice.warn.stepnote")
        assert note.count() == 1 and note.is_visible()
        assert note.inner_text() == (
            f"This gateway's ChatGPT connector name, “{LONG}”, is 85 characters, and ChatGPT refuses "
            "a plugin file whose name is over 80 characters. Add the connector by hand with the steps "
            "above instead; only the gateway's owner can change the name."
        )
        # in the plugin-file card, not under the hand-add Name box
        card = note.locator("xpath=ancestor::div[contains(@class,'card')][1]")
        assert "ChatGPT plugin file" in card.locator("h2").inner_text()
        b = _bounds(page, ".notice.warn.stepnote")
        assert b["left"] >= 0 and b["right"] <= b["vw"], b
        assert page.evaluate(CONTRAST, ".notice.warn.stepnote") == []
        assert page.evaluate(MEASURE) == []
        _shot(page, card, f"install-{width}-{theme}.png")
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", SIZES)
def test_admin_warning_reads_everywhere(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _admin_sign_in(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/admin", wait_until="networkidle")
        note = page.locator(".notice.warn", has_text="MTG_CHATGPT_NAME")
        assert note.count() == 1 and note.is_visible()
        assert note.inner_text() == (
            f"The ChatGPT connector name, “{LONG}”, is 85 characters; ChatGPT refuses a plugin file "
            "whose name is over 80 characters. Set a shorter MTG_CHATGPT_NAME (see docs/DEPLOY.md)."
        )
        assert page.evaluate(CONTRAST, ".notice.warn") == []
        assert page.evaluate(MEASURE) == []
        _shot(page, note, f"admin-{width}-{theme}.png", pad=80)
        browser.close()
    assert errors == []
