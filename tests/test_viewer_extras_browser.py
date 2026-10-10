"""0.7.16 in headless Chromium: the card viewer's Rulings, EDHREC and TCGplayer links and the
back face of a double-faced card; voting on someone else's comment (asked first, like the deck's
Like); Redo in the deck editor (buttons and Ctrl+Z / Ctrl+Shift+Z). Screenshots go to
MTG_SHOTS when it is set. Skipped without a Chromium build."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import pytest

from .test_editor_browser import _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAUAAAAHCAIAAAAbd2raAAAAFElEQVR4nGNgYGD4z8DAwMDAwMAAAAwAA/8BpYQAAAAASUVORK5CYII="
)
SHOTS = os.environ.get("MTG_SHOTS")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.ark.private.discard(43)  # amy's deck is public: someone else's comments to vote on
    s.ark.comments[300043] = [
        {
            "id": 555001,
            "text": "Try Cultivate here.",
            "owner": {"id": 78, "username": "amy", "avatar": None, "frame": None},
            "parent": 300043,
            "originalPost": 300043,
            "createdAt": "2026-10-10T12:00:00Z",
            "editedAt": None,
            "points": 2,
            "userInput": 0,
            "childrenCount": 0,
            "children": {"count": 0, "results": []},
            "archived": False,
            "locked": False,
            "type": 4,
        }
    ]
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=False)


def _context(p, exe: str, server: Server, sid: str, width: int, scheme: str = "light"):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(
        viewport={"width": width, "height": 900 if width > 500 else 800}, color_scheme=scheme
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    return browser, ctx


@pytest.mark.parametrize("width,scheme", [(390, "dark"), (1366, "light")])
def test_viewer_rulings_links_and_back_face(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    rulings = [
        {"object": "ruling", "source": "wotc", "published_at": "2004-10-04", "comment": "It adds {C}{C}."},
        {"object": "ruling", "source": "scryfall", "published_at": "2021-03-19", "comment": "x " * 120},
    ]
    for c in server.sf.cards:
        server.sf.rulings[c["id"]] = rulings
    with sync_playwright() as p:
        browser, ctx = _context(p, exe, server, sid, width, scheme)

        # the fixture cards have one picture each; the text answer is given a back face here
        def text(route):
            resp = route.fetch()
            body = resp.json()
            if body.get("card"):
                body["card"]["back_img"] = "https://cards.scryfall.io/normal/back/x.jpg"
            route.fulfill(response=resp, body=json.dumps(body))

        ctx.route(re.compile(r".*/cards/api/text\?.*"), text)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        # the fake deck's rows carry no picture; give Sol Ring one so the viewer shows an image
        sol = page.locator(".deckview .c[data-card='Sol Ring']").first
        sol.evaluate("e => e.setAttribute('data-img', 'https://cards.scryfall.io/small/front/s.jpg')")
        sol.click()
        page.wait_for_selector(".cardview.open", timeout=3000)
        edh = page.locator(".cardview .more a", has_text="EDHREC")
        assert edh.get_attribute("href") == "https://edhrec.com/route/?cc=Sol+Ring"
        assert edh.get_attribute("rel") == "noopener noreferrer"
        flip = page.locator(".cardview .flip")
        flip.wait_for(state="visible", timeout=3000)
        front = page.locator(".cardview .pane img").get_attribute("src")
        flip.click()
        assert page.locator(".cardview .pane img").get_attribute("src").endswith("/back/x.jpg")
        assert flip.get_attribute("aria-pressed") == "true" and flip.inner_text() == "Show front"
        flip.click()
        assert page.locator(".cardview .pane img").get_attribute("src") == front
        rb = page.locator(".cardview .more button", has_text="Rulings")
        rb.click()
        page.locator(".cardview .rulings li").first.wait_for(timeout=3000)
        assert page.locator(".cardview .rulings li").count() == 2
        assert rb.get_attribute("aria-expanded") == "true"
        assert page.locator(".cardview .rulings li time").first.inner_text() == "2004-10-04"
        assert page.locator(".cardview .rulings .pip").count() >= 1  # {C} drawn as a symbol
        over = page.evaluate(
            "() => { const b = document.querySelector('.cardview .box');"
            " return [b.scrollWidth - b.clientWidth, document.documentElement.scrollWidth - innerWidth]; }"
        )
        assert over == [0, 0], over
        _shot(page, f"viewer-rulings-{width}-{scheme}")
        rb.click()
        assert page.locator(".cardview .rulings").is_hidden()
        assert errors == []
        browser.close()


def test_vote_on_someone_elses_comment(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    with sync_playwright() as p:
        browser, ctx = _context(p, exe, server, sid, 390)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/decks/43", wait_until="networkidle")
        page.locator("#comments").scroll_into_view_if_needed()
        up = page.locator(".cmt[data-id='555001'] button.vote", has_text="Up")
        up.wait_for(timeout=5000)
        assert "2 points" in page.locator(".cmt[data-id='555001'] .who").inner_text()
        up.click()
        # asked first; nothing sent yet
        assert server.ark.votes == {}
        page.locator(".cmt .confirm button", has_text="Vote").click()
        page.wait_for_function(
            "() => document.querySelector(\".cmt[data-id='555001'] .who\").textContent.includes('3 points')"
        )
        assert server.ark.votes == {("alice", 555001): 1}
        assert up.get_attribute("aria-pressed") == "true"
        _shot(page, "comment-voted-390")
        up.click()
        page.locator(".cmt .confirm button", has_text="Take back").click()
        page.wait_for_function(
            "() => document.querySelector(\".cmt[data-id='555001'] .who\").textContent.includes('2 points')"
        )
        assert server.ark.votes == {}
        assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") == 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [390, 1366])
def test_editor_redo(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    with sync_playwright() as p:
        browser, ctx = _context(p, exe, server, sid, width)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        n = page.locator(".pendingbox .n")
        undo, redo = page.locator("button.undo"), page.locator("button.redo")
        assert undo.is_disabled() and redo.is_disabled()
        page.locator("button[aria-label^='One more']").first.click()
        page.locator("button[aria-label^='One more']").nth(1).click()
        assert n.inner_text() == "2"
        undo.click()
        assert n.inner_text() == "1" and redo.is_enabled()
        undo.click()
        assert n.inner_text() == "0" and undo.is_disabled()
        redo.click()
        redo.click()
        assert n.inner_text() == "2" and redo.is_disabled()
        # keyboard, outside a text field
        page.locator("h1").first.click()
        page.keyboard.press("Control+z")
        assert n.inner_text() == "1"
        page.keyboard.press("Control+Shift+z")
        assert n.inner_text() == "2"
        # a new change after an undo clears what Redo would bring back
        undo.click()
        page.locator("button[aria-label^='One more']").nth(2).click()
        assert redo.is_disabled() and n.inner_text() == "2"
        bar = page.evaluate(
            "() => { const b = document.querySelector('.editbar').getBoundingClientRect();"
            " return [document.documentElement.scrollWidth - innerWidth, b.right <= innerWidth]; }"
        )
        assert bar == [0, True], bar
        _shot(page, f"editor-redo-{width}")
        assert errors == []
        browser.close()
