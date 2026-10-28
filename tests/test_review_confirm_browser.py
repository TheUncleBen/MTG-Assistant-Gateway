"""The confirm box of a destructive approval on the review page, in headless Chromium (0.7.46):
the red outline button opens the site's shared box; Keep it, Escape, the × and the phone's Back
close it with nothing sent; the one solid red button inside it applies through the guarded form.
Phone, Fold and desktop (where the proposal sits in the side panel), light and dark, the Android
app's WebView as the website. Skipped without a Chromium build."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from .test_deck_custom_cards import _seed as seed_customs
from .test_editor_browser import _link
from .test_lists_page import _seed as seed_lists
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_track_a_pages_browser import _no_sideways, _readable

pytest.importorskip("playwright")

APP_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-F946B) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Mobile Safari/537.36 MTGAssistant/0.7.46"
)
SIZES = (
    ("phone", 390, 844, True, "dark"),
    ("phone", 390, 844, True, "light"),
    ("fold", 840, 1000, True, "dark"),
    ("desktop", 1280, 900, False, "dark"),
    ("desktop", 1280, 900, False, "light"),
)
BOX = ".mtgbox-scrim.in #confirm-delete"
# every hidden button is hidden, whatever display rule the theme gives buttons
HIDDEN_BUTTONS_HIDDEN = (
    "() => [...document.querySelectorAll('button[hidden]')].every(b => b.offsetParent === null)"
)
VISIBLE_RED = (
    "() => [...document.querySelectorAll('button.danger')].filter(b => b.offsetParent !== null).length"
)
FOCUSED = (
    "() => (document.activeElement.getAttribute('aria-label') || document.activeElement.textContent).trim()"
)


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _proposals(server: Server) -> tuple[str, dict[str, str]]:
    """A deck delete, a custom card taken out of deck 42 and a list delete, all waiting."""
    sid = _link(server)
    seed_lists(server.ark)
    seed_customs(server.ark)
    decks = server.app.state.gateway.decks

    async def make() -> dict[str, str]:
        add = await decks.propose_custom_cards(
            "user-1", "42", [{"action": "add", "custom_card_id": 41, "quantity": 3}]
        )
        await decks.apply("user-1", add["proposal_id"], via="browser")
        out = await decks.propose_custom_cards(
            "user-1", "42", [{"action": "set_quantity", "custom_card_id": 41, "quantity": 2}]
        )
        lists = await decks.propose_lists("user-1", "templates", [{"op": "delete", "name": "Ramp"}])
        # last: the delete claims the deck as it is now (a custom card added above changed it)
        gone = await decks.propose_action("user-1", "delete_deck", deck_id="42")
        return {"delete": gone["proposal_id"], "custom": out["proposal_id"], "lists": lists["proposal_id"]}

    return sid, asyncio.run(make())


def _context(
    browser, server: Server, sid: str, vw: int, vh: int, phone: bool, scheme: str, app: bool = False
):
    extra = {"user_agent": APP_UA} if app else {}
    ctx = browser.new_context(
        viewport={"width": vw, "height": vh}, color_scheme=scheme, is_mobile=phone, has_touch=phone, **extra
    )
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": sid, "url": server.base},
            {"name": "mtg_theme", "value": scheme, "url": server.base},
        ]
    )
    return ctx


def _unlock(page) -> None:
    """The click guard: the pointer moves, then the dwell and settle times pass."""
    page.mouse.move(100, 100)
    page.mouse.move(130, 150)
    page.wait_for_timeout(1300)


def _open(page):
    page.locator("[data-box-open='confirm-delete']").click()
    box = page.locator(BOX)
    box.wait_for(timeout=5000)
    # measured once it has slid or scaled into place: mid-way it is lower and 2% smaller
    page.wait_for_function(
        "() => document.getAnimations().every(a => a.playState !== 'running')", timeout=5000
    )
    return box


def _gone(page) -> None:
    page.locator(".mtgbox-scrim").wait_for(state="detached", timeout=5000)


def _state(server: Server, pid: str) -> str:
    return str(server.app.state.gateway.db.get_proposal(pid, "user-1")["state"])


def _wait_state(server: Server, pid: str, want: str) -> None:
    for _ in range(100):
        if _state(server, pid) == want:
            return
        time.sleep(0.1)
    assert _state(server, pid) == want


def test_deleting_a_deck_asks_in_the_box_and_only_its_red_button_deletes(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, ids = _proposals(server)
    pid = ids["delete"]
    url = f"{server.base}/proposals/{pid}"
    shots = os.environ.get("MTG_SHOTS_DIR")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        try:
            for name, vw, vh, phone, scheme in SIZES:
                where = f"{name}-{scheme}"
                ctx = _context(browser, server, sid, vw, vh, phone, scheme)
                page = ctx.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.goto(url, wait_until="networkidle")
                # one Delete control before the press, outlined; no solid red button anywhere
                delete = page.locator("button", has_text="Delete this deck")
                assert [b.is_visible() for b in delete.all()].count(True) == 1, where
                assert page.evaluate(VISIBLE_RED) == 0, where
                assert page.evaluate(HIDDEN_BUTTONS_HIDDEN), where
                opener = page.locator("[data-box-open='confirm-delete']")
                assert opener.inner_text() == "Delete this deck on Archidekt…", where
                if shots:
                    page.locator(".apply").scroll_into_view_if_needed()
                    page.screenshot(path=f"{shots}/confirm-{where}-1-before.png")
                _unlock(page)
                assert opener.is_enabled(), where
                box = _open(page)
                assert page.url == url, where  # a box, not a page
                assert box.locator(".bh h2").inner_text() == "Delete “Sample Commander Deck”?", where
                body = box.locator(".body").inner_text()
                assert (
                    "“Sample Commander Deck” (100 cards)" in body and "Archidekt itself has no undo." in body
                )
                assert box.get_attribute("role") == "dialog" and box.get_attribute("aria-modal") == "true"
                assert box.get_attribute("aria-labelledby") == "confirm-delete-title", where
                # the one solid red button is inside the box; the outline one is under the scrim
                assert page.evaluate(VISIBLE_RED) == 1, where
                red = box.locator("button.danger")
                assert red.inner_text().strip() == "Delete this deck", where
                assert red.get_attribute("form") == "applyf" and red.get_attribute("value") == "apply", where
                bb = red.bounding_box()  # a 44 px target on a touch screen, on screen everywhere
                assert bb is not None and bb["height"] >= (44 if phone else 36), (where, bb)
                assert bb["y"] + bb["height"] <= vh, (where, bb)
                _readable(page, ".mtgbox .bh h2, .mtgbox .body p, .mtgbox .acts button", where)
                _no_sideways(page, vw, where)
                if shots:
                    page.screenshot(path=f"{shots}/confirm-{where}-2-box.png")
                # focus starts on Keep it: Enter keeps the deck
                assert page.evaluate(FOCUSED) == "Keep it", where
                page.keyboard.press("Enter")
                _gone(page)
                assert page.url == url and _state(server, pid) == "pending", where
                assert page.evaluate(FOCUSED) == "Delete this deck on Archidekt…", where
                # Tab stays inside: Keep it, Delete this deck, the ×, Keep it again
                _open(page)
                seen = []
                for _ in range(3):
                    page.keyboard.press("Tab")
                    seen.append(page.evaluate(FOCUSED))
                assert seen == ["Delete this deck", "Close", "Keep it"], (where, seen)
                page.keyboard.press("Shift+Tab")
                assert page.evaluate(FOCUSED) == "Close", where
                # Escape closes the box only: focus back on the opener, the side panel still open
                page.keyboard.press("Escape")
                _gone(page)
                assert page.evaluate(FOCUSED) == "Delete this deck on Archidekt…", where
                assert page.url == url and _state(server, pid) == "pending", where
                if name == "desktop":
                    assert page.locator(".ui-split.with-panel .ui-drawer").is_visible(), where
                # the × and the scrim close it too
                _open(page).locator(".bx").click()
                _gone(page)
                _open(page)
                page.mouse.click(5, 300)
                _gone(page)
                assert _state(server, pid) == "pending" and errors == [], (where, errors)
                ctx.close()

            # the Android app's WebView: Back closes the box instead of leaving the page; then the
            # red button deletes the deck on Archidekt through the same guarded form
            ctx = _context(browser, server, sid, 390, 844, True, "dark", app=True)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            page.goto(url, wait_until="networkidle")
            assert page.evaluate("() => document.body.classList.contains('app')")
            _unlock(page)
            _open(page)
            page.go_back()
            _gone(page)
            assert page.url == url and _state(server, pid) == "pending"
            box = _open(page)
            assert server.ark.deleted == []
            with page.expect_navigation(wait_until="networkidle"):
                box.locator("button.danger").click()
            assert "err=" not in page.url, (page.url, page.locator(".notice").all_inner_texts())
            _wait_state(server, pid, "applied")
            assert server.ark.deleted == [42]
            assert errors == [], errors
            ctx.close()
        finally:
            browser.close()


def test_custom_cards_out_and_list_deletes_ask_in_the_same_box(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, ids = _proposals(server)
    shots = os.environ.get("MTG_SHOTS_DIR")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        try:
            ctx = _context(browser, server, sid, 390, 844, True, "dark")
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            for which, title, keep, final in (
                (
                    "custom",
                    "Take 1 custom card out of “Sample Commander Deck”?",
                    "Keep them",
                    "Take them out",
                ),
                ("lists", "Delete from your Archidekt lists?", "Keep them", "Delete 1 entry"),
            ):
                url = f"{server.base}/proposals/{ids[which]}"
                page.goto(url, wait_until="networkidle")
                assert page.evaluate(VISIBLE_RED) == 0, which
                _unlock(page)
                box = _open(page)
                assert box.locator(".bh h2").inner_text() == title, which
                assert box.locator("[data-cancel]").inner_text() == keep, which
                assert box.locator("button.danger").inner_text().strip() == final, which
                assert page.evaluate(VISIBLE_RED) == 1 and page.evaluate(FOCUSED) == keep, which
                _readable(
                    page, ".mtgbox .bh h2, .mtgbox .body p, .mtgbox .body li, .mtgbox .acts button", which
                )
                if shots:
                    page.screenshot(path=f"{shots}/confirm-{which}-phone-dark-2-box.png")
                page.keyboard.press("Escape")
                _gone(page)
                assert _state(server, ids[which]) == "pending", which
            # the red button applies the list delete: the template is gone on Archidekt
            page.goto(f"{server.base}/proposals/{ids['lists']}", wait_until="networkidle")
            _unlock(page)
            box = _open(page)
            with page.expect_navigation(wait_until="networkidle"):
                box.locator("button.danger").click()
            _wait_state(server, ids["lists"], "applied")
            assert [t["name"] for t in server.ark.account.templates["alice"]] == ["Commander"]
            assert errors == [], errors
            ctx.close()
        finally:
            browser.close()
