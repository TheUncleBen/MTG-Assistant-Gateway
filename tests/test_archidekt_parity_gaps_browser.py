"""0.7.46's Archidekt parity pages in headless Chromium: Archidekt's change log page with its
removed card rows, a profile with no public decks (its empty state and Follow), Account details ›
Backup folder, and the My decks delete box that moves the backup folder out of the way and asks
again. On a phone (360, 412), the Fold (717, 840) and a desktop (1280), light and dark, in the
Android app's shell and at 200% text: nothing scrolls sideways, text keeps 4.5:1 and touch targets
are 44 px. Set MTG_PARITY_SHOTS=<dir> to also save screenshots. Skipped without a Chromium build."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_editor_browser import _link
from .test_notify_app import APP_UA
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server
from .test_text_zoom_browser import TEXT_200

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_PARITY_SHOTS")
PAGES = ("/history/archidekt?deck_id=42", "/users/bo", "/account/details/backups")
TEXT = "main h1, main h2, main p, main li, main label, main button, main .btn, main a, .notice"
TARGETS = "main button, main .btn, main input[type=text], .yback"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True)
    ark = s.ark
    ark.users["bo"] = {"password": "pw-bo", "id": 79, "decks": []}
    ark.deck_logs[42] = [
        {
            "id": 1,
            "createdAt": "2026-10-21T08:00:00Z",
            "user": {"username": "alice"},
            "description": "Added 1 Sol Ring to Ramp, and a long line of words that has to wrap on a phone",
        }
    ]
    ark.add_removed_row(42, ark.decks[42]["cards"][0]["card"]["oracleCard"]["name"], 2)
    ark.folders["alice"] = [
        {"id": 504, "name": "Archive", "private": False},
        {"id": 505, "name": "MTG Gateway Backups", "private": True, "parent": 504},
    ]
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _short(page) -> list[str]:
    return page.evaluate(
        "(sel) => [...document.querySelectorAll(sel)]"
        ".filter(e => e.getBoundingClientRect().width && getComputedStyle(e).visibility !== 'hidden')"
        ".filter(e => e.getBoundingClientRect().height < 43.5)"
        ".map(e => e.textContent.trim().slice(0, 30) + ' ' + Math.round(e.getBoundingClientRect().height))",
        TARGETS,
    )


@pytest.mark.parametrize(
    "width,theme,app,zoom",
    [
        (360, "light", False, False),
        (412, "dark", False, False),
        (717, "light", False, False),
        (840, "dark", False, False),
        (1280, "light", False, False),
        (1280, "dark", False, False),
        (412, "dark", True, False),
        (360, "light", False, True),
    ],
)
def test_the_parity_pages_fit_each_surface(
    server: Server, width: int, theme: str, app: bool, zoom: bool
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        if app:
            ctx = browser.new_context(
                viewport={"width": width, "height": 860},
                has_touch=True,
                user_agent=APP_UA.replace("0.7.30", "0.7.46"),
            )
            ctx.add_cookies(
                [
                    {"name": "mtg_session", "value": sid, "url": server.base},
                    {"name": "mtg_theme", "value": theme, "url": server.base},
                ]
            )
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
        for path in PAGES:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            if zoom:
                page.evaluate(TEXT_200)
                page.wait_for_timeout(300)
            assert page.locator("main h1").count() == 1, path
            assert page.evaluate(MEASURE) == [], path
            assert page.evaluate(CONTRAST, TEXT) == [], path
            if width < 900:
                assert _short(page) == [], path
            if SHOTS:
                name = path.strip("/").split("?")[0].replace("/", "-")
                tag = f"{width}-{theme}{'-app' if app else ''}{'-200' if zoom else ''}"
                page.screenshot(path=str(Path(SHOTS) / f"{name}-{tag}.png"), full_page=True)
        browser.close()
    assert errors == []


def test_the_delete_box_moves_the_backup_folder_out_and_asks_again(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 412, "dark", exe)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        page.locator("li.folder[data-folder-id='504'] button.kebab").click()
        page.locator(".ctxmenu[role=menu] [role=menuitem]", has_text="Delete").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        move = box.locator(".acts button", has_text="Move the backup folder to your top level")
        move.wait_for(timeout=5000)
        assert "can't be deleted yet" in box.locator("h2").inner_text()
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, ".mtgbox h2, .mtgbox p, .mtgbox .acts button") == []
        if SHOTS:
            page.screenshot(path=str(Path(SHOTS) / "decks-delete-box-412-dark.png"))
        move.click()
        # the box asks about the folder again, now with what deleting it deletes
        page.locator(".mtgbox[role=dialog]:not([hidden]) input#mb-typed").wait_for(timeout=5000)
        assert server.ark.folders["alice"][1]["parent"] == 1000 + server.ark.users["alice"]["id"]
        if SHOTS:
            page.screenshot(path=str(Path(SHOTS) / "decks-delete-box-after-412-dark.png"))
        browser.close()
    assert errors == []


def test_follow_works_on_a_profile_with_no_public_decks(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "light", exe)
        page.goto(f"{server.base}/users/bo", wait_until="networkidle")
        btn = page.locator("[data-social=follow]")
        assert btn.get_attribute("data-state") == "0"  # read from Archidekt: not followed yet
        btn.click()
        page.locator(".confirm button", has_text="Follow").click()
        page.wait_for_function("() => document.querySelector('[data-social=follow]').dataset.state === '1'")
        assert btn.inner_text().strip() == "Following bo"
        assert 79 in server.ark.follows["alice"]
        browser.close()
    assert errors == []
