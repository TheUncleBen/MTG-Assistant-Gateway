"""A place's switch (Binder: Cards, Wants, Watchlist; Assistant: Proposals, History, Reports) at
200 % text on a phone (independent review of 0.7.46: Binder's switch showed "Wa…" with nothing to
say it scrolls). When its parts are wider than the screen it scrolls inside, the page it names is
scrolled into full view, and a faded edge shows there is more on that side. Never a sideways page."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")

PAGES = ["/collection", "/collection/wants", "/watchlist", "/proposals", "/history", "/reports"]
STATE = """() => {
  const n = document.querySelector('.hubseg'); const cur = n.querySelector('[aria-current=page]');
  const r = n.getBoundingClientRect(), c = cur.getBoundingClientRect();
  return {over: n.scrollWidth > n.clientWidth + 1, inView: c.left >= r.left - 1 && c.right <= r.right + 1,
          start: n.classList.contains('more-start'), end: n.classList.contains('more-end'),
          page: document.documentElement.scrollWidth - document.documentElement.clientWidth};
}"""


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_switch_shows_its_page_and_that_it_scrolls(server: Server, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(
                viewport={"width": 390, "height": 844}, color_scheme=scheme, has_touch=True
            )
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            # 200 % text before any script runs, as the phone's own setting gives it
            page.add_init_script(
                "document.addEventListener('DOMContentLoaded', () => {"
                " const s = document.createElement('style');"
                " s.textContent = 'html{font-size:200% !important}';"
                " document.head.appendChild(s); });"
            )
            for path in PAGES:
                page.goto(f"{server.base}{path}", wait_until="networkidle")
                page.wait_for_timeout(100)
                st = page.evaluate(STATE)
                assert st["page"] <= 0, (path, st)
                assert st["inView"], (path, st)
                if st["over"]:
                    assert st["start"] or st["end"], (path, st)
        finally:
            browser.close()
