"""The admin Overview, Users, Activity, Metrics and Settings pages in headless Chromium at 344, 390,
810 and 1280 px, light and dark: nothing scrolls sideways, text reads at 4.5:1 or better, the ⋮
targets are 44 px, a member's row menu opens from the ⋮ and a right-click, Disable… and Delete data…
open their boxes (the red Delete data button stays off until the tick is set), and a Metrics row
opens its details in place. Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_admin_pages import seed
from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_overflow_sweep_browser import MEASURE
from .test_settings_page_browser import _admin, _shot
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200

pytest.importorskip("playwright")

SIZES = [(344, "light"), (390, "dark"), (810, "light"), (810, "dark"), (1280, "light"), (1280, "dark")]
PAGES = [
    "/admin",
    "/admin/users",
    "/admin/users?open=user-3",
    "/admin/activity",
    "/admin/metrics",
    "/activity",
]
TEXT = (
    ".adm h1, .adm .ui-sub, .adm-nav a, .wdg .lab, .wdg .big, .wdg .big small, .wdg .det, .wdg .go .btn, "
    ".ui-chip, .ui-table .t, .ui-table .sub, .ui-table .sm-only, .ui-table .th span, .ui-lbl, .ui-app .nm, "
    ".ui-pager, .ui-drawer dt, .ui-drawer dd, .ui-drawer .btn, .ui-drawer button, .ui-drawer .note, "
    ".act-table .en, .act-table .c-who, .ui-row .lab, .ui-row .ctl, .muted, .nobody, "
    ".ctxmenu [role=menuitem], .mtgbox h2, .mtgbox p, .mtgbox label, .mtgbox .acts button"
)
TAPS = """
() => [...document.querySelectorAll(
    '.kebab, .wdg .go .btn, .ui-drawer .ui-actions .btn, .ui-drawer .ui-actions button, .au-del .btn')]
  .filter(e => e.getBoundingClientRect().width)
  .filter(e => e.getBoundingClientRect().height < 38.5)
  .map(e => (e.getAttribute('aria-label') || e.textContent.trim()).slice(0, 30)
    + ' ' + Math.round(e.getBoundingClientRect().height))
"""


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path, admin_group="mtg-admins")
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("width,theme", SIZES)
def test_every_admin_page_fits_and_reads(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    seed(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        for path in PAGES:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            assert page.evaluate(MEASURE) == [], path
            assert page.evaluate(CONTRAST, TEXT) == [], path
            assert page.evaluate(TAPS) == [], path
            kebabs = page.evaluate(
                "() => [...document.querySelectorAll('.kebab')].map(b => b.getBoundingClientRect().height)"
                ".filter(h => h > 0)"
            )
            assert not kebabs or min(kebabs) >= 43.5, (path, kebabs)
            _shot(page, f"admin-{path.strip('/').replace('/', '-') or 'overview'}-{width}-{theme}.png")
        # the six admin tabs sit in the same place on every tab (the 1200 px width)
        lefts = set()
        for path in ("/admin", "/admin/users", "/admin/settings", "/admin/models"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            lefts.add(
                page.evaluate(
                    "() => Math.round(document.querySelector('.adm-nav').getBoundingClientRect().left)"
                )
            )
            lefts.add(
                page.evaluate(
                    "() => Math.round(document.querySelector('.adm-nav').getBoundingClientRect().right)"
                )
            )
        assert len(lefts) == 2, lefts
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width", [344, 390])
def test_the_admin_pages_fit_a_phone_at_200_percent_text(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    seed(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        for path in PAGES + ["/admin/models", "/admin/settings"]:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            page.evaluate(ROOT_200)
            assert page.evaluate(MEASURE) == [], path
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", [(390, "light"), (1280, "dark")])
def test_a_members_row_menu_and_the_two_boxes(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    seed(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/admin/users", wait_until="networkidle")
        rows = page.locator(".au .ui-table .tr.mem")
        assert rows.count() == 4
        # the ⋮ of a disabled member: Enable, Revoke, Activity, then Delete data… in red
        carol = rows.filter(has_text="carol_long_member_name")
        carol.locator(".kebab").click()
        menu = page.locator(".ctxmenu")
        assert menu.is_visible()
        items = [t.strip() for t in menu.locator("[role=menuitem]").all_inner_texts()]
        assert items == ["Enable", "Revoke tokens and sessions", "Activity", "Delete data…"]
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        page.keyboard.press("Escape")
        assert menu.count() == 0
        # a right-click on a linked, active member: Disable… and Unlink too; Disable… opens its box
        bob = rows.filter(has_text="bob@example.test")
        bob.click(button="right")
        items = [t.strip() for t in page.locator(".ctxmenu [role=menuitem]").all_inner_texts()]
        assert items == [
            "Disable…",
            "Revoke tokens and sessions",
            "Unlink Archidekt",
            "Activity",
            "Delete data…",
        ]
        page.locator(".ctxmenu [role=menuitem]", has_text="Disable…").click()
        page.wait_for_load_state("networkidle")
        box = page.locator("#disable")
        assert box.is_visible() and "Disable “bob”?" in box.inner_text()
        assert page.evaluate(MEASURE) == []
        _shot(page, f"admin-users-disable-{width}-{theme}.png")
        last = box.locator(".acts button").last
        assert last.inner_text().strip() == "Disable account" and "danger" in (
            last.get_attribute("class") or ""
        )
        box.locator(".acts button", has_text="Cancel").click()
        page.wait_for_selector("#disable", state="hidden")
        assert page.url.endswith("/admin/users?open=user-2")
        # the panel is open: Delete data… opens its box in place; the red button waits for the tick
        panel = page.locator(".ui-drawer")
        assert panel.is_visible() and "user-2" in panel.inner_text()
        panel.locator(".au-del a", has_text="Delete data…").click()
        box = page.locator("#delete")
        assert box.is_visible() and "Delete “bob”'s data?" in box.inner_text()
        button = box.locator(".acts button", has_text="Delete data")
        assert button.is_disabled()
        box.locator("input[type=checkbox]").check()
        assert button.is_enabled()
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        _shot(page, f"admin-users-delete-{width}-{theme}.png")
        page.keyboard.press("Escape")
        page.wait_for_selector("#delete", state="hidden")
        assert server.db.get_user("user-2") is not None
        # a click on the row itself opens the member's panel
        page.goto(f"{server.base}/admin/users", wait_until="networkidle")
        rows.filter(has_text="a member with no name").locator(".title").click(position={"x": 5, "y": 5})
        page.wait_for_load_state("networkidle")
        assert page.url.endswith("/admin/users?open=user-9")
        assert "dcr-4f9a" in page.locator(".ui-drawer").inner_text()
        if width >= 1100:
            assert page.locator(".au .ui-table .tr.mem").count() == 4  # the list stays beside the panel
        browser.close()
    assert errors == []


def test_a_metrics_row_opens_in_place_and_an_activity_row_has_a_menu(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    seed(server)
    server.db.audit("approval_refused", sub="user-2", client_id="claude-app", detail={"proposal_id": "p1"})
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 810, "light", exe)
        page.goto(f"{server.base}/admin/metrics", wait_until="networkidle")
        row = page.locator(".am .ui-table .tr.mrow").first
        body = row.locator(".more-body")
        assert not body.is_visible()
        row.locator(".title .t").click()
        assert body.is_visible() and "Recorded as" in body.inner_text()
        assert row.locator(".title .t").get_attribute("aria-expanded") == "true"
        assert page.url.endswith("/admin/metrics")  # no reload
        row.locator(".title .t").click()
        assert not body.is_visible()
        page.goto(f"{server.base}/admin/activity", wait_until="networkidle")
        first = page.locator(".act-table .tr.ev").first
        first.locator(".kebab").click()
        items = [t.strip() for t in page.locator(".ctxmenu [role=menuitem]").all_inner_texts()]
        assert "Copy details" in items and any("only" in t for t in items)
        page.keyboard.press("Escape")
        browser.close()
    assert errors == []
