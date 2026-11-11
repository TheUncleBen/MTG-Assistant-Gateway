"""The Alerts page and one-time notices in Chromium (0.7.47, Ben's notifications redesign): a
saved form's notice is gone after a reload (the app's pull to refresh is one) and its × takes it
away; each alert's ⋮ marks it read or dismisses it in place, with Undo; Mark all read and Clear
all act on every row; on a phone, an open Fold and a laptop, light and dark, nothing runs off the
screen and every control is a 44 px target."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

SIZES = [(360, "light"), (673, "dark"), (1280, "light"), (1280, "dark")]


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _exe() -> str:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _browser(p, server: Server, sid: str, width: int, theme: str, exe: str):  # noqa: ANN001, ANN202
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": width, "height": 860}, has_touch=width < 700)
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": sid, "url": server.base},
            {"name": "mtg_theme", "value": theme, "url": server.base},
        ]
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


def _alerts(server: Server, n: int = 3) -> list[int]:
    titles = [
        "Price drop · Sol Ring",
        "Report ready · Atraxa, the Very Long Commander Name of Doom",
        "Summary · Today",
    ]
    kinds = ["price", "report", "summary"]
    ids = []
    for i in range(n):
        aid = server.db.add_gateway_alert(
            "user-1",
            kinds[i % 3],
            titles[i % 3],
            "$12.00 to $9.50: below your alert.",
            "/watchlist",
            kind=kinds[i % 3],
        )
        assert aid is not None
        ids.append(aid)
    return ids


@pytest.mark.parametrize("width,theme", [(390, "light"), (1280, "dark")])
def test_a_saved_notice_is_gone_after_a_reload_and_its_x_takes_it_away(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in()
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _browser(p, server, sid, width, theme, exe)
        try:
            url = f"{server.base}/account/details/notifications?ok=notify_saved"
            page.goto(url)
            note = page.locator(".notice.ok", has_text="Push notifications saved.")
            note.wait_for()
            assert page.url == url  # the first showing changes nothing
            assert note.inner_text().strip() == "Push notifications saved."
            x = note.locator("> .close")
            box = x.bounding_box()
            assert box and box["width"] >= 44 and box["height"] >= 44
            x.click()
            assert page.locator(".notice.ok", has_text="Push notifications saved.").count() == 0
            # shown again, then reloaded: gone, and so is that part of the address
            page.goto(url)
            note.wait_for()
            page.reload()
            page.wait_for_url(f"{server.base}/account/details/notifications")
            page.wait_for_load_state("load")
            assert page.locator(".notice.ok", has_text="Push notifications saved.").count() == 0
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_alerts_page_reads_and_works_at_every_size(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in()
    a1, a2, a3 = _alerts(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _browser(p, server, sid, width, theme, exe)
        try:
            page.goto(f"{server.base}/notifications")
            rows = page.locator(".al-row")
            assert rows.count() == 3
            # nothing runs off the screen, the long title wraps inside its row
            over = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert over <= 0, over
            small = page.evaluate(
                "[...document.querySelectorAll('.al-row summary, .al-acts button, .al-acts a')]"
                ".filter(e => e.offsetParent && Math.min(e.getBoundingClientRect().width,"
                " e.getBoundingClientRect().height) < 43.5).map(e => e.textContent.trim())"
            )
            assert small == [], small
            # the newest alert's ⋮: Mark read, in place
            row = page.locator(f".al-row[data-alert='{a3}']")
            assert "unread" in (row.get_attribute("class") or "")
            row.locator(".al-menu > summary").click()
            page.get_by_role("menuitem", name="Mark read").click()
            page.wait_for_selector(f".al-row.unread[data-alert='{a3}']", state="detached")
            assert server.db.gateway_alert("user-1", a3)["read_at"] is not None
            # Dismiss, then Undo in the toast
            page.locator(f".al-row[data-alert='{a2}'] .al-menu > summary").click()
            page.get_by_role("menuitem", name="Dismiss").click()
            page.wait_for_selector(f".al-row[data-alert='{a2}']", state="detached")
            assert server.db.gateway_alert("user-1", a2)["dismissed_at"] is not None
            page.get_by_role("button", name="Undo").click()
            page.wait_for_selector(f".al-row[data-alert='{a2}']")
            assert server.db.gateway_alert("user-1", a2)["dismissed_at"] is None
            # Mark all read, then Clear all: the page says it is all caught up
            page.locator("[data-al-all='read_all']").click()
            page.wait_for_selector(".al-row.unread", state="detached")
            page.locator("[data-al-all='clear_all']").click()
            page.get_by_text("You're all caught up.").wait_for()
            assert server.db.gateway_alerts("user-1") == []
            # a reload shows the same: what was cleared stays cleared
            page.reload()
            page.get_by_text("You're all caught up.").wait_for()
            assert errors == []
        finally:
            browser.close()
