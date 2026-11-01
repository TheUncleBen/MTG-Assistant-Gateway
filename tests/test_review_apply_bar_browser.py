"""Apply a proposal without a scroll or a lecture (0.7.46 audit QG-13, QG-23; QOL-19, QOL-25):
the review page says in one line what Apply does, with a How it works link to the Guide, the
click guard's note is one short line, and on a phone Apply and Reject sit in a bar at the foot
of the screen, so they are on the first screen whatever the proposal's length."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_proposal_review_browser import _proposals
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("width,height", [(412, 860), (360, 740)])
def test_apply_is_on_the_first_screen_of_a_phone(server: Server, width: int, height: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, edit, _details = _proposals(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        try:
            ctx = browser.new_context(
                viewport={"width": width, "height": height}, is_mobile=True, has_touch=True
            )
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            page.goto(f"{server.base}/proposals/{edit}", wait_until="networkidle")
            # one line, then the way to the longer story
            apply_text = page.locator(".apply > p").first.inner_text()
            assert apply_text.count(".") <= 2 and "How it works" in apply_text, apply_text
            link = page.locator(".apply a", has_text="How it works")
            assert link.get_attribute("href") == "/guide#how-approve"
            assert "reads the result back" not in apply_text
            # the guard's note is one short line
            hint = page.locator("form.guarded .guard-hint")
            assert len(hint.inner_text()) <= 80, hint.inner_text()
            # Apply and Reject are on screen without a scroll, in the bar at the foot
            apply = page.locator("form.guarded button[value=apply]")
            reject = page.locator("form.guarded button[value=reject]")
            for b in (apply, reject):
                box = b.bounding_box()
                assert box and box["y"] + box["height"] <= height and box["y"] >= 0, (box, height)
                assert box["height"] >= 44
            assert page.evaluate("() => scrollY") == 0
            # the bar stays at the foot as the page scrolls
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(200)
            box = apply.bounding_box()
            assert box["y"] + box["height"] <= height and box["y"] >= 0, box
            # nothing hides behind it: the page's last line can be scrolled above the bar
            hint = page.locator("form.guarded .guard-hint")
            if hint.is_visible():
                assert hint.bounding_box()["y"] + hint.bounding_box()["height"] <= box["y"] + 1
            last = page.locator(".apply").bounding_box()
            assert last["y"] + last["height"] <= box["y"] + 1, (last, box)
        finally:
            browser.close()
