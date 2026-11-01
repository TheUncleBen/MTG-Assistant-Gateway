"""Account › Assistants in headless Chromium, as the approved mockups show it: Account details'
Assistants row, the Assistants list, one assistant's page, the confirm box and Home's notice, on a
phone (375 px), an open Fold (840 px) and a desktop (1280 px), light and dark, at 200% text and in
the Android app: nothing scrolls sideways, text reads at 4.5:1 or better, every row and button is a
44 px target, and the right mark shows in each theme. Skipped without a Chromium build.
MTG_PERMODE_SHOTS=<folder> keeps a screenshot of each."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from mtg_gateway import modes

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_notify_app import APP_UA
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_PERMODE_SHOTS")
SIZES = [(375, "dark"), (375, "light"), (840, "dark"), (1280, "dark"), (1280, "light")]
PAGES = [
    ("details", "/account/details"),
    ("assistants", "/account/assistants"),
    ("chatgpt", "/account/assistants/chatgpt"),
    ("confirm", "/account/assistants/chatgpt?confirm=auto"),
    ("default", "/account/assistants/default"),
    ("home", "/"),
]
TEXT = (
    ".ygrp h2, .ygrp .lbl, .ygrp .val, .ygrp .sb, .ygrp .yfoot, .ygrp .lbl small, .yme b, .yme .sub, "
    ".ydisc .btn, .justin .t, .justin .btn, .mtgbox h2, .mtgbox p, .mtgbox .acts button, .yback, h1"
)
# rows and buttons on these pages under 44 px tall (a box's buttons on a touch screen: with a mouse
# they are the site's 39 px box buttons, as every other box)
TAPS = """
(touch) => [...document.querySelectorAll('.ygrp a.yrow, .ygrp button.yrow, .ydisc .btn, .justin .btn, .yback'
    + (touch ? ', .mtgbox .acts button' : ''))]
  .filter(e => e.getBoundingClientRect().width && getComputedStyle(e).visibility !== 'hidden')
  .filter(e => e.getBoundingClientRect().height < 43.5)
  .map(e => e.textContent.trim().slice(0, 30) + ' ' + Math.round(e.getBoundingClientRect().height))
"""
# the OpenAI mark that shows: black on light, white on dark
OPENAI_SHOWN = """
() => [...document.querySelectorAll('.alogo.oa img')]
  .filter(i => getComputedStyle(i).display !== 'none').map(i => i.getAttribute('src'))
"""


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _app(server: Server, cid: str, name: str, redirect: str, *, opened: bool) -> None:
    db = server.db
    db.save_client(cid, {"client_name": name, "redirect_uris": [redirect]})
    key = "claude" if "claude.ai" in redirect else "openai" if "chatgpt" in redirect else f"app:{cid}"
    db.note_assistant("user-1", key, cid)
    if opened:
        db.mark_assistant_opened("user-1", key)
    db.save_token(
        f"tok-{cid}",
        kind="access",
        client_id=cid,
        sub="user-1",
        scopes=["mtg"],
        resource=None,
        family=f"f-{cid}",
        expires_at=int(time.time()) + 3600,
    )


def _seed(server: Server) -> str:
    sid = server.sign_in()
    _app(server, "c-claude", "Claude", "https://claude.ai/api/mcp/auth_callback", opened=True)
    _app(server, "c-code", "Claude Code", "http://localhost:33418/callback", opened=True)
    _app(server, "c-gpt", "ChatGPT", "https://chatgpt.com/connector_platform_oauth_redirect", opened=False)
    server.db.set_assistant_mode("user-1", "claude", "semi")
    return sid


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / name), full_page=True)


def _check(page, where: str, *, touch: bool = True) -> None:
    assert page.evaluate(MEASURE) == [], where
    assert page.evaluate(CONTRAST, TEXT) == [], where
    assert page.evaluate(TAPS, touch) == [], where


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_assistant_pages_fit_and_read_everywhere(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        # Home first, while ChatGPT is still new (its page, opened below, ends the notice)
        for name, path in [PAGES[-1], *PAGES[:-1]]:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            _check(page, f"{path} {width} {theme}", touch=width < 900)
            _shot(page, f"{name}-{width}-{theme}.png")
            if name == "home":
                assert page.locator(".justin").is_visible()
            if name == "assistants":
                shown = page.evaluate(OPENAI_SHOWN)
                want = "openai-black" if theme == "light" else "openai-white"
                assert len(shown) == 1 and want in shown[0], shown
                assert page.locator(".yrow.app").count() == 3
                vals = page.locator(".yrow.app .val").all_inner_texts()
                assert modes.MODE_LABELS["semi"] in vals
            if name == "confirm":
                box = page.locator("#pmode")
                assert box.is_visible()
                assert "Allow for “ChatGPT”" in box.inner_text()
        assert errors == []
        browser.close()


@pytest.mark.parametrize("zoom", ["root", "text"])
def test_the_assistant_pages_hold_at_200_percent_text(server: Server, zoom: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _seed(server)
    with sync_playwright() as p:
        browser, page, _errors = _page(p, server, sid, 375, "dark", exe)
        for name, path in [PAGES[-1], *PAGES[:-1]]:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
            page.wait_for_timeout(100)
            assert page.evaluate(MEASURE) == [], (path, zoom)
            assert page.evaluate(TAPS, True) == [], (path, zoom)
            _shot(page, f"{name}-375-200pct-{zoom}.png")
        browser.close()


def test_the_assistant_pages_in_the_android_app(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(
            viewport={"width": 412, "height": 900}, has_touch=True, is_mobile=True, user_agent=APP_UA
        )
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for name, path in [PAGES[-1], *PAGES[:-1]]:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            _check(page, f"app {path}")
            _shot(page, f"{name}-app-412.png")
        # the confirm box's Allow saves the mode, as the box says
        page.goto(f"{server.base}/account/assistants/chatgpt?confirm=auto", wait_until="networkidle")
        page.locator("#pmode .acts button.primary").click()
        page.wait_for_load_state("networkidle")
        assert "Saved: “ChatGPT” applies changes without asking." in page.inner_text("main")
        assert server.db.assistant_mode("user-1", "openai") == "auto"
        assert errors == []
        browser.close()


def test_a_loosening_press_opens_the_box_and_cancel_changes_nothing(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 375, "dark", exe)
        page.goto(f"{server.base}/account/assistants/chatgpt", wait_until="networkidle")
        page.locator("button.ychoice[value='semi']").click()
        page.wait_for_load_state("networkidle")
        box = page.locator("#pmode")
        assert box.is_visible() and "low-risk changes" in box.inner_text()
        box.locator("button[data-cancel]").click()
        box.wait_for(state="hidden", timeout=3000)
        assert server.db.assistant_mode("user-1", "openai") is None
        # tightening Claude is one press, no box
        page.goto(f"{server.base}/account/assistants/claude", wait_until="networkidle")
        page.locator("button.ychoice[value='manual']").click()
        page.wait_for_load_state("networkidle")
        assert server.db.assistant_mode("user-1", "claude") == "manual"
        assert page.locator("#pmode").count() == 0
        assert errors == []
        browser.close()
