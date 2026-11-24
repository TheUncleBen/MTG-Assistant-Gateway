"""The 0.7.48 review's browser findings (review-rest.md): the bell's box said "You're all caught
up." while older alerts were kept, a browser whose zone became UTC kept the old days for one view,
and feedback.js now also tells the server the zone's name, so a past entry is grouped by the
offset its own day had (localday.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mtg_gateway import notify, theme

from .test_alerts_box_browser import _open
from .test_alerts_center_browser import _alerts, _exe
from .test_consent_browser import _chromium_or_skip, _launch
from .test_suggest_browser import Server

pytest.importorskip("playwright")

FEEDBACK_JS = (Path(notify.__file__).parent / "static" / "feedback.js").read_text()


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_dismissing_the_shown_rows_brings_the_older_ones_in(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in()
    _alerts(server, 12)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, "light", False, False, exe)
        try:
            page.goto(f"{server.base}/decks")
            page.wait_for_load_state("load")
            page.locator("[data-bell]:visible").first.click()
            box = page.locator(".mtgbox.al-box")
            box.locator(".al-row").first.wait_for()
            for left in range(11, 3, -1):
                aid = box.locator(".al-row[data-alert]").first.get_attribute("data-alert")
                box.locator(f".al-row[data-alert='{aid}'] .al-kebab").click()
                page.get_by_role("menuitem", name="Dismiss").click()
                page.wait_for_function(
                    "([aid, n]) => { const b = document.querySelector('.mtgbox.al-box');"
                    " return !b.querySelector(`.al-row[data-alert='${aid}']`)"
                    " && b.querySelectorAll('.al-row[data-alert]').length === n; }",
                    arg=[aid, min(8, left)],
                    timeout=5000,
                )
            assert box.get_by_text("You're all caught up.").count() == 0
            assert box.locator(".al-row[data-alert]").count() == 4
            assert errors == []
        finally:
            browser.close()


def _days_page(p, exe: str | None, zone: str, cookies: dict[str, str]):  # noqa: ANN202
    browser = _launch(p, exe)
    ctx = browser.new_context(timezone_id=zone)
    ctx.add_cookies([{"name": k, "value": v, "url": "https://mtg.test"} for k, v in cookies.items()])
    ctx.add_init_script(
        "sessionStorage.setItem('loads', String(+(sessionStorage.getItem('loads') || 0) + 1))"
    )
    page = ctx.new_page()
    html = (
        f"<!doctype html><style>{theme.CSS}</style><main><ul data-local-days></ul></main>"
        f"<script>{FEEDBACK_JS}</script>"
    )
    page.route("https://mtg.test/**", lambda route: route.fulfill(content_type="text/html", body=html))
    page.goto("https://mtg.test/history")
    page.wait_for_load_state("load")
    page.wait_for_timeout(300)  # fixed wait: a reload that may not come has no event to wait on
    page.wait_for_load_state("load")
    return browser, page


def _cookies(page) -> dict[str, str]:  # noqa: ANN001
    return {c["name"]: c["value"] for c in page.context.cookies()}


def test_a_browser_whose_offset_becomes_zero_loads_the_days_again() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        # a UK member's summer cookie, read after the clocks went back (Abidjan: always UTC+0)
        browser, page = _days_page(p, exe, "Africa/Abidjan", {"mtg_tzo": "60", "mtg_tz": "Africa/Abidjan"})
        try:
            assert _cookies(page)["mtg_tzo"] == "0"
            assert page.evaluate("sessionStorage.getItem('loads')") == "2"
        finally:
            browser.close()


def test_a_new_browser_at_utc_plus_zero_all_year_is_not_loaded_again() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser, page = _days_page(p, exe, "Africa/Abidjan", {})
        try:
            assert _cookies(page)["mtg_tzo"] == "0"
            assert page.evaluate("sessionStorage.getItem('loads')") == "1"
        finally:
            browser.close()


def test_the_page_tells_the_server_its_zone_name() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser, page = _days_page(p, exe, "America/New_York", {})
        try:
            cookies = _cookies(page)
            assert cookies["mtg_tz"] == "America/New_York"
            assert cookies["mtg_tzo"] in ("-300", "-240")
            assert page.evaluate("sessionStorage.getItem('loads')") == "2"  # the server knew neither
        finally:
            browser.close()
