"""The review page after the independent review of 0.7.46, in headless Chromium at 390, 840 and
1280 px, light and dark, and at 200 % text: a deck delete's summary ("1 Archidekt action; no cards
change") wraps instead of widening the page; a Needs a look delete shows Reject and no Delete; a
waiting edit's "Once applied" price line fits and reads at 4.5:1."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_REVIEW_SHOTS")
SIZES = [(390, "dark", False), (390, "light", True), (840, "light", False), (1280, "dark", False)]
FP = "f"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _save(server: Server, pid: str, **row) -> None:
    server.db.save_proposal(
        {
            "id": pid,
            "owner_sub": "user-1",
            "deck_id": "42",
            "deck_name": "Sample Commander Deck",
            "baseline_fingerprint": FP,
            "expires_at": int(time.time()) + 3600,
            "created_by_client": "__browser__",
            **row,
        }
    )


@pytest.mark.parametrize(("width", "scheme", "big"), SIZES)
def test_review_pages_fit_and_offer_only_what_works(
    server: Server, width: int, scheme: str, big: bool
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    text = "Delete your deck “Sample Commander Deck” (100 cards) from your Archidekt account"
    delete = {
        "kind": "action",
        "changes": {"action": "delete_deck", "deck_id": "42", "name": "Sample Commander Deck", "cards": 100},
        "rows": [
            {
                "kind": "action",
                "action": "delete_deck",
                "label": "Delete deck",
                "text": text,
                "note": "Archidekt itself has no undo for a deleted deck.",
                "cannot_undo": True,
                "destructive": True,
            }
        ],
        "diff_text": text,
    }
    _save(server, "del1", **delete)
    _save(server, "del2", **delete)
    server.db.rebase_proposal("del2", "user-1", was=FP, fingerprint=FP, status="needs_look", reason="changed")
    _save(
        server,
        "ed1",
        kind="edit",
        changes=[{"action": "add", "card_name": "Opt", "quantity": 1}],
        rows=[{"kind": "add", "name": "Opt", "qty": 1}],
        diff_text="+1 Opt",
        deck_after={
            "card_count": 101,
            "legal": False,
            "problems": ["the deck has 101 cards, not 100"],
            "price_total": 412.5,
            "price_change": 0.25,
            "unpriced_added": [],
        },
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for pid in ("del1", "del2", "ed1"):
                page.goto(f"{server.base}/proposals/{pid}", wait_until="networkidle")
                if big:
                    page.add_style_tag(content="html{font-size:200% !important}")
                over = page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                assert over <= 0, (pid, over)
                if pid == "del1":
                    assert page.locator("button", has_text="Delete this deck on Archidekt").count() == 1
                if pid == "del2":
                    assert page.locator("button", has_text="Delete this deck on Archidekt").count() == 0
                    assert page.locator("button", has_text="Reject this proposal").is_visible()
                if pid == "ed1":
                    line = page.locator(".pr-after")
                    assert "Once applied: 101 cards, not legal, $412.50 (up $0.25)." in line.inner_text()
                    assert page.evaluate(CONTRAST, ".pr-after, .summary-pills .ui-lbl") == []
                if SHOTS:
                    page.screenshot(
                        path=str(Path(SHOTS) / f"review-{pid}-{width}-{scheme}{'-z200' if big else ''}.png"),
                        full_page=True,
                    )
            assert errors == []
        finally:
            browser.close()
