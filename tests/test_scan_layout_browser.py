"""The Scan page's control bar in headless Chromium, in a browser and as inside the Android app
(whose window.MtgNative adds a "Phone camera" button): the capture button stays a circle. Needs no
OCR assets, unlike test_scan_browser.py. Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_cardsearch_browser import _chromium
from .test_suggest_browser import Server, server  # noqa: F401

pytest.importorskip("playwright")


@pytest.mark.parametrize("width", [344, 390, 884, 1280])
def test_the_capture_button_is_a_circle_in_the_browser_and_in_the_app(server: Server, width: int) -> None:  # noqa: F811
    """Inside the Android app the fourth button put the capture button in a wide grid column,
    where it stretched into an oval."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        errors: list[str] = []
        for app in (False, True):
            ctx = browser.new_context(viewport={"width": width, "height": 860}, has_touch=width < 900)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            if app:
                ctx.add_init_script("window.MtgNative = {openCamera: function () {}};")
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/scan")
            page.wait_for_selector("#shutter", timeout=10_000)
            assert page.locator("#phone-camera").count() == (1 if app else 0)
            box = page.locator("#shutter").bounding_box()
            assert abs(box["width"] - box["height"]) < 1, (app, box)
            assert errors == [], errors
            ctx.close()
        browser.close()
