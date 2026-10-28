"""No plain blue text links for actions or navigation on the assistant pages (0.7.46): a standalone
link that does something or goes somewhere (Why, Group by deck, Waiting, Open as a full page, Undo,
Open, the change, Only this deck) is the theme's capsule, and a row's or a group's title, a deck
named in a panel or a widget, reads in the text colour, bold, underlined on hover. The report
page's crumb trail keeps its links: it is the page's way back, drawn like every crumb trail."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mtg_gateway import ui

from .test_history_page import seed_history, store_report
from .test_scan_browser import _chromium_path
from .test_social_browser import Server


def test_the_capsule_link_and_the_rows_open() -> None:
    assert ui.cap_link("Why", "/proposals/p1") == "<a class='ui-cap' href='/proposals/p1'>Why</a>"
    assert ui.open_link("/history/reports/r1", "Open").startswith("<a class='act ui-cap' href=")


pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


# every visible link or link-like button in the page's main column drawn in the link colour
BLUE = """() => {
  const probe = document.createElement('a'); probe.href = '#'; document.body.appendChild(probe);
  const link = getComputedStyle(probe).color; probe.remove();
  return Array.from(document.querySelectorAll('main a, main button')).filter(a => {
    if (!a.offsetParent || a.closest('nav.crumbs')) return false;
    return getComputedStyle(a).color === link;
  }).map(a => (a.className || a.parentElement.className) + ': ' + a.innerText.trim().slice(0, 40));
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_assistant_pages_have_no_blue_text_links(server: Server, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    now = int(time.time())
    seed_history(server.db, "user-1", now=now)
    store_report(server.db, "user-1", "rep_a", taken_at=now - 200)
    pids = [r["id"] for r in server.db._conn.execute("SELECT id, state FROM proposals ORDER BY created_at")]
    failed = server.db._one("SELECT id FROM proposals WHERE state='failed'")["id"]
    snap = server.db._one("SELECT id FROM snapshots WHERE proposal_id IS NOT NULL")["id"]
    pages = [
        "/reports",
        "/proposals",
        "/proposals?show=all",
        "/proposals?show=all&group=deck",
        f"/proposals/{pids[1]}",
        f"/proposals/{failed}",
        "/history",
        "/history?group=deck",
        f"/history?open=s-{snap}",
        "/history/reports/rep_a",
        "/activity?q=nothing-matches-this",
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=scheme)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            found: dict[str, list[str]] = {}
            for path in pages:
                page.goto(f"{server.base}{path}", wait_until="networkidle")
                blue = page.evaluate(BLUE)
                if blue:
                    found[path] = sorted(set(blue))
            assert not found, found
            # the capsules are 44 px tall tap targets
            page.goto(f"{server.base}/proposals?show=all", wait_until="networkidle")
            heights = page.evaluate(
                "() => Array.from(document.querySelectorAll('main .ui-cap'))"
                ".filter(a => a.offsetParent).map(a => a.getBoundingClientRect().height)"
            )
            assert heights and min(heights) >= 44, heights
        finally:
            browser.close()
