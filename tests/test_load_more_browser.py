"""Load more on History, Proposals and My activity in headless Chromium (Standard 13): pressing it
adds the next rows in place, without leaving the page, and never draws a day heading twice; the
button is 44 px tall on a phone, at 390 and 1280 px, light and dark."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from .test_history_page import seed_history
from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1280, "light")])
def test_load_more_adds_rows_in_place(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    seed_history(server.db, "user-1", now=int(time.time()))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(
                viewport={"width": width, "height": 900}, color_scheme=scheme, has_touch=width < 900
            )
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for path, table in (("/history", ".hy-table"), ("/proposals?show=all", ".pr-table")):
                page.goto(f"{server.base}{path}", wait_until="networkidle")
                rows = page.locator(f"{table} > .tr:not(.th):not(.daygroup):not(.group)")
                before = rows.count()
                assert before == 25, (path, before)
                btn = page.locator(".loadmore [data-load-more]")
                box = btn.bounding_box()
                assert box is not None and box["height"] >= (44 if width < 900 else 36)
                url = page.url
                btn.click()
                for _ in range(100):
                    if rows.count() > before:
                        break
                    page.wait_for_timeout(50)
                assert rows.count() > before and page.url == url, path
                days = page.locator(f"{table} > .daygroup").all_inner_texts()
                plain = [d.split("·")[0].strip() for d in days]
                assert len(plain) == len(set(plain)), plain
                assert page.locator(".ui-pager").count() == 0
            assert errors == []
        finally:
            browser.close()
