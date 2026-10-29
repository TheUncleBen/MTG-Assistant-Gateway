"""History's "Make a new deck from this" on a deleted deck's snapshot, in a real browser: at 390,
840 and 1280 px, light and dark, at 200 % text and in the Android app, the button shows (in the
row, or under Details on a phone) without pushing the page sideways, and pressing it opens the
new deck's review page with nothing made yet. Skipped without Chromium."""

from __future__ import annotations

import copy
import time
from pathlib import Path

import pytest

from .test_notify_app import APP_UA
from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")

SNAP = "snapdeleted01"
WIDE = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _seed(server: Server) -> None:
    """A snapshot of a deck (id 999) that is not on the member's Archidekt account any more."""
    sub = server.db._one("SELECT sub FROM users", ())["sub"]
    deck = copy.deepcopy(server.ark.decks[42])
    deck["id"] = 999
    deck["name"] = "Gone But Kept"
    deck["owner"] = {**deck.get("owner", {}), "id": 77}  # the linked account's own deck, as read
    assert server.db.save_snapshot(
        SNAP, owner_sub=sub, deck_id="999", proposal_id=None, fingerprint="f", deck=deck
    )
    with server.db.tx() as c:
        c.execute("UPDATE snapshots SET taken_at = ? WHERE id = ?", (int(time.time()) - 60, SNAP))


@pytest.mark.parametrize(
    ("width", "scheme", "big", "ua"),
    [
        (390, "light", False, APP_UA),
        (390, "dark", True, None),
        (840, "dark", False, None),
        (1280, "light", True, None),
    ],
)
def test_make_a_new_deck_shows_and_opens_its_review(server: Server, width, scheme, big, ua) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            opts = {"viewport": {"width": width, "height": 900}, "color_scheme": scheme}
            if width == 390:
                opts.update(has_touch=True, is_mobile=True)
            if ua:
                opts["user_agent"] = ua
            ctx = browser.new_context(**opts)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            if big:
                page.add_init_script(
                    "document.addEventListener('DOMContentLoaded', () => {"
                    " const s = document.createElement('style');"
                    " s.textContent = 'html{font-size:200% !important}'; document.head.appendChild(s); });"
                )
            page.goto(f"{server.base}/history?open=s-{SNAP}", wait_until="networkidle")
            button = page.locator("button:visible", has_text="Make a new deck from this").first
            assert button.is_visible()
            assert page.locator("text=Restore (review first)").locator("visible=true").count() == 0
            assert page.evaluate(WIDE) <= 0
            box = button.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
            button.click()
            page.wait_for_load_state("networkidle")
            assert "/proposals/" in page.url, page.url
            assert "Gone But Kept" in page.content()
            assert page.evaluate(WIDE) <= 0
            assert not [d for d in server.ark.decks.values() if d.get("name") == "Gone But Kept"]
        finally:
            browser.close()
