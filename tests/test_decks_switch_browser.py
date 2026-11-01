"""The My Decks / Packages switch at the top of the deck list draws the other side in place
(static/decks.js): no page load, the address and the browser's Back follow it, the heading, the
filter box's words and the page's orange button change with it (0.7.46 audit: the switch was a
whole page load of 384-528 ms). Without scripts its links are the same two pages. Skipped
without a Chromium build."""

from __future__ import annotations

import pytest

from .test_deck_extras_browser import server  # noqa: F401
from .test_editor_browser import _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

STATE = """
() => ({
  url: location.pathname + location.search,
  stayed: window.__stayed === 1,
  h1: document.querySelector('.hubhead h1').textContent,
  title: document.title.split(' · ')[0].split(' - ')[0],
  current: [...document.querySelectorAll('.hubhead .hubseg a[aria-current=page]')]
    .map(a => a.textContent.trim()),
  deck42: !document.querySelector("li.deck[data-deck-id='42']").classList.contains('sh-off'),
  pkg50: !document.querySelector("li.deck[data-deck-id='50']").classList.contains('sh-off'),
  orange: [...document.querySelectorAll('.hubhead .acts a.btn-primary')].map(a => a.textContent.trim()),
  placeholder: document.querySelector('#q').placeholder,
  label: document.querySelector("label[for=q]").textContent,
  show: (document.querySelector('#listform input[name=show]') || {}).value || '',
  heading: (document.querySelector('#fv-decks') || {}).textContent || '',
})
"""


def test_the_switch_draws_the_other_side_in_place_and_back_returns(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        page.wait_for_function("() => document.querySelector(\"li.deck[data-deck-id='50']\")")
        page.wait_for_function("() => window.MtgPackages")  # the packages read landed
        page.evaluate("() => { window.__stayed = 1; }")
        before = page.evaluate(STATE)
        assert before["h1"] == "My Decks" and before["current"] == ["My Decks"], before
        assert before["deck42"] and not before["pkg50"] and before["orange"] == ["New deck"], before
        assert before["placeholder"] == "Deck name" and before["show"] == "", before
        page.get_by_role("link", name="Packages", exact=True).first.click()
        page.wait_for_function("() => location.search.includes('show=packages')")
        after = page.evaluate(STATE)
        assert after["stayed"], "the page was loaded again"
        assert after["url"] == "/decks?show=packages", after
        assert after["h1"] == "Packages" and after["title"] == "Packages", after
        assert after["current"] == ["Packages"], after
        assert after["pkg50"] and not after["deck42"], after
        assert after["orange"] == ["New package"], after
        assert after["placeholder"] == "Package name" and after["label"] == "Filter package name", after
        assert after["show"] == "packages" and after["heading"] == "Packages", after
        # a filter typed now keeps the side in the address (the panel's own refresh)
        page.fill("#q", "Gol")
        page.wait_for_function(
            "() => location.search.includes('q=Gol') && location.search.includes('show=packages')"
        )
        page.fill("#q", "")
        page.wait_for_function("() => !location.search.includes('q=')")
        page.go_back()
        page.wait_for_function("() => !location.search.includes('show=packages')")
        back = page.evaluate(STATE)
        assert back["stayed"] and back["url"] == "/decks", back
        assert back["h1"] == "My Decks" and back["current"] == ["My Decks"], back
        assert back["deck42"] and not back["pkg50"] and back["orange"] == ["New deck"], back
        assert back["placeholder"] == "Deck name" and back["show"] == "", back
        # the link itself is still the whole page: a reload shows the side the address names
        page.goto(f"{server.base}/decks?show=packages", wait_until="networkidle")
        page.wait_for_function("() => window.MtgPackages")
        fresh = page.evaluate(STATE)
        assert fresh["h1"] == "Packages" and fresh["pkg50"] and not fresh["deck42"], fresh
        browser.close()
