"""A menu on a phone puts an entry in the history so Back closes it, and closing it from an item
goes back over that entry. A link or a form the item starts must wait for that Back to land:
started at once, it raced the Back, and the Back could win (a tap on Remove… on Card models left
the admin on the list, or took them back to it after the box had opened). Skipped without a
Chromium build."""

from __future__ import annotations

import pytest

from .test_admin_card_models_browser import _models
from .test_cardsearch_browser import _chromium, _page
from .test_settings_page_browser import _admin
from .test_suggest_browser import Server

pytest.importorskip("playwright")


# The order of the menu's Back (popstate) and the page change, kept across the page change.
LOG = """
(() => {
  const put = (s) => {
    try { sessionStorage.setItem('log', (sessionStorage.getItem('log') || '') + s + '|'); } catch (e) {}
  };
  addEventListener('popstate', () => put('pop'));
  if (window.navigation) navigation.addEventListener('navigate', (e) => {
    if (e.navigationType === 'traverse' || e.destination.sameDocument) return;
    put('go ' + new URL(e.destination.url).search);
  });
})();
"""


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path, admin_group="mtg-admins")
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_a_menu_link_on_a_phone_goes_once_the_menus_back_has_landed(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    _models(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        page.add_init_script(LOG)
        page.goto(f"{server.base}/admin/models", wait_until="networkidle")
        assert page.evaluate("() => !!window.navigation")
        page.evaluate("() => sessionStorage.removeItem('log')")
        page.locator(".amo .ui-table .tr.model").filter(has_text="Opt").click(button="right")
        page.locator(".ctxmenu [role=menuitem]", has_text="Remove…").click()
        page.locator("#remove").wait_for(state="visible")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        log = page.evaluate("() => sessionStorage.getItem('log')").strip("|").split("|")
        assert log[0] == "pop" and log[1].startswith("go ?remove=opt"), log
        assert "remove=opt" in page.url
        # one Back from the box's page is the list: the menu's entry is not left behind
        page.go_back(wait_until="networkidle")
        assert page.url.endswith("/admin/models")
        browser.close()
    assert errors == []


def test_a_page_change_an_item_starts_waits_for_the_menus_back(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    _models(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        page.goto(f"{server.base}/admin/models", wait_until="networkidle")
        page.locator(".amo .ui-table .tr.model").filter(has_text="Opt").click(button="right")
        assert page.locator(".ctxmenu").is_visible()
        assert page.evaluate("() => history.state && history.state.mtgmenu") == 1
        seen = page.evaluate(
            """() => new Promise((done) => {
              MtgMenu.close(false);
              const now = MtgMenu.backPending();
              MtgMenu.whenClosed(() => done([now, MtgMenu.backPending(), history.state]));
            })"""
        )
        # the Back was on its way when the item asked; the item went once it had landed
        assert seen == [True, False, None]
        # with no menu open it goes at once
        assert page.evaluate(
            "() => { let ran = false; MtgMenu.whenClosed(() => { ran = true; }); return ran; }"
        )
        browser.close()
    assert errors == []
