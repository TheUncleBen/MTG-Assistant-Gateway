"""The guided setup's Copy buttons and icon download in headless Chromium (T-145): each Copy
button puts exactly the value shown on the clipboard, and the connector icon downloads without a
sign-in. Skipped without a Chromium build."""

from __future__ import annotations

from pathlib import Path

import pytest

from mtg_gateway import plugin_page

from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_chatgpt_copy_buttons_and_icon(server: Server) -> None:
    from playwright.sync_api import expect, sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    settings = server.app.state.gateway.settings
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=server.base)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/install?for=chatgpt", wait_until="networkidle")
        expected = {
            "gpt-name": settings.server_name,
            "gpt-desc": plugin_page.SERVER_DESCRIPTION,
            "gpt-url": settings.mcp_url,
        }
        for key, value in expected.items():
            page.click(f"button[data-copy='{key}']")
            expect(page.locator(f"#{key}-status")).to_have_text("Copied")
            assert page.evaluate("navigator.clipboard.readText()") == value, key

        # The icon is a public file: a fresh browser with no session gets the PNG itself.
        anon = browser.new_context()
        r = anon.request.get(f"{server.base}{plugin_page.CONNECTOR_ICON_PATH}")
        assert r.status == 200 and r.headers["content-type"] == "image/png"
        assert len(r.body()) <= plugin_page.CONNECTOR_ICON_MAX_BYTES
        browser.close()
    assert errors == []
