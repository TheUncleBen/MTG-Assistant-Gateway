"""The deck editor's add-a-card flow in headless Chromium (R-131): typed names show rich
suggestions (picture, mana cost, type line) from the gateway's catalog, Enter adds the card and
keeps the box focused for the next one, the add form's controls share one row on desktop and
fold cleanly on a phone, and the printing picker opens as a dialog. Skipped without Chromium."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAUAAAAHCAIAAAAbd2raAAAAFElEQVR4nGNgYGD4z8DAwMDAwMAAAAwAA/8BpYQAAAAASUVORK5CYII="
)


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    # the fixture cards' names are suggestable, with their summaries behind /scan/api/peek
    s.sf.catalog = sorted({c["name"] for c in s.sf.cards}) + [f"Filler Card {i}" for i in range(1200)]
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _link(server: Server) -> str:
    import httpx

    sid = server.sign_in()
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/account").text).group(1)
    r = h.post(
        "/account",
        data={
            "csrf": csrf,
            "action": "link",
            "archidekt_login": "alice",
            "archidekt_password": "pw-alice",
            "accept_risk": "1",
        },
    )
    assert r.status_code == 303, r.text
    return sid


@pytest.mark.parametrize("width", [390, 1366])
def test_add_a_card_with_rich_suggestions(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        assert page.locator("datalist").count() == 0
        # the add form: one row of equal-height controls on desktop; nothing wider than the page
        tops = page.evaluate(
            "() => [...document.querySelectorAll('form.addcard .field')].map(f => {"
            " const c = f.querySelector('input,select,button'); const r = c.getBoundingClientRect();"
            " return [Math.round(r.top), Math.round(r.height)]; })"
        )
        if width >= 1200:
            assert len({t[0] for t in tops}) == 1 and len({t[1] for t in tops}) == 1, tops
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        box = page.locator("#addname")
        box.click()
        box.press_sequentially("sol", delay=30)
        page.locator("[role=listbox]:visible").wait_for(timeout=3000)
        first = page.locator("[role=option]").first
        assert first.get_attribute("data-name") == "Sol Ring"
        # the row is decorated once the summaries arrive: type line and a mana disc
        page.locator("[role=option] .rich .ty").first.wait_for(timeout=3000)
        assert page.locator("[role=option] .rich .ty").first.inner_text() == "Artifact"
        assert page.locator("[role=option] .rich .pip").count() >= 1
        box.press("ArrowDown")
        box.press("Enter")
        page.wait_for_timeout(300)
        assert "Added 1 × Sol Ring" in page.locator(".addstatus").inner_text()
        assert page.locator(".pendingbox .n").inner_text() == "1"
        assert page.evaluate("() => document.activeElement.id") == "addname"
        assert box.input_value() == ""
        # a card the deck does not hold yet becomes a new row with its picture and mana cost
        box.press_sequentially("oathsw", delay=30)
        page.locator("[role=option]").first.wait_for(timeout=3000)
        box.press("Enter")
        page.wait_for_timeout(300)
        row = page.locator(".erow.new").first
        assert "Oathsworn Giant" in row.inner_text()
        assert row.locator(".name .pip").count() >= 1
        # the printing picker is a dialog, closed by Escape
        page.locator(".erow:not(.new) details.rowmenu summary").first.click()
        page.locator(".erow:not(.new) .menu button", has_text="Change printing").first.click()
        picker = page.locator(".picker[role=dialog]")
        picker.wait_for(state="visible", timeout=3000)
        page.locator(".picker .print").first.wait_for(timeout=5000)
        assert page.evaluate("() => document.activeElement.className").startswith("close")
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        assert page.locator(".picker:visible").count() == 0
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        assert errors == []
        browser.close()
