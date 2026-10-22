"""The setup, Account and small pages in headless Chromium (the B4 overhaul): the guided setup's
two columns on a computer and its "Steps for" drop-down on a phone, the Account page's
Disconnect box opening from its red-outline button, the Delete my data box's solid red button
staying disabled until the tick (static/pages.js), and a Back button going back one page.
Skipped without a Chromium build."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

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


def _connect_app(server: Server, cid: str, name: str) -> None:
    db = server.db
    db.save_client(cid, {"client_name": name, "redirect_uris": ["https://x.test/cb"]})
    db.save_token(
        f"tok-{cid}",
        kind="access",
        client_id=cid,
        sub="user-1",
        scopes=["mtg"],
        resource=None,
        family=f"f-{cid}",
        expires_at=int(time.time()) + 86400,
    )


def test_setup_columns_account_boxes_and_back(server: Server) -> None:
    from playwright.sync_api import expect, sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    _connect_app(server, "claude-7f3c", "Claude")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        errors: list[str] = []

        # A computer: the app list stands left of the steps; the phone drop-down is not drawn.
        desk = browser.new_context(viewport={"width": 1280, "height": 800})
        desk.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = desk.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/install?for=claude", wait_until="networkidle")
        nav = page.locator("nav.pick").bounding_box()
        steps = page.locator(".inst .cols > div:nth-child(2)").bounding_box()
        assert nav and steps and nav["x"] + nav["width"] <= steps["x"] and abs(nav["y"] - steps["y"]) < 4
        expect(page.locator("details.drop")).to_be_hidden()
        expect(page.locator("nav.pick a[aria-current='true']")).to_contain_text("Claude: web")
        assert page.evaluate("document.documentElement.scrollWidth") <= 1280
        desk.close()

        phone = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        phone.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = phone.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/install?for=claude", wait_until="networkidle")
        expect(page.locator("nav.pick")).to_be_hidden()
        drop = page.locator("details.drop")
        expect(drop).to_be_visible()
        expect(drop.locator("summary .v")).to_have_text("Claude: web, desktop, iPhone and Android")
        drop.locator("summary").click()
        expect(drop.locator(".list a[href='/install?for=chatgpt']")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= 390

        # Account: Disconnect opens its box (no page load); the box's solid red button is a submit.
        page.goto(f"{server.base}/account", wait_until="networkidle")
        box = page.locator("#disc1")
        expect(box).to_be_hidden()
        page.locator("a[data-box-open='disc1']").click()
        expect(box).to_be_visible()
        expect(box.locator("h2")).to_have_text("Disconnect “Claude”?")
        expect(box.locator("button.danger")).to_have_text("Disconnect")
        assert page.url.endswith("/account")
        box.locator("[data-cancel]").click()
        expect(box).to_be_hidden()

        # Delete my data: the final button stays disabled until the tick, then submits the form.
        page.locator("a[data-box-open='delete']").click()
        dbox = page.locator("#delete")
        expect(dbox).to_be_visible()
        final = dbox.locator("button.danger")
        expect(final).to_be_disabled()
        dbox.locator("#delete-tick").check()
        expect(final).to_be_enabled()
        dbox.locator("#delete-tick").uncheck()
        expect(final).to_be_disabled()
        assert page.evaluate("document.documentElement.scrollWidth") <= 390

        # A Back button goes back one page where there is one.
        page.goto(f"{server.base}/account", wait_until="networkidle")
        page.evaluate(
            "() => { const f = document.createElement('form'); f.method = 'post'; f.action = '/account';"
            " const i = document.createElement('input'); i.name = 'x'; i.value = 'a'.repeat(20000);"
            " f.appendChild(i); document.body.appendChild(f); f.submit(); }"
        )
        page.wait_for_load_state("networkidle")
        expect(page.locator("h1")).to_have_text("Too large")
        page.locator("a[data-history-back]").click()
        page.wait_for_load_state("networkidle")
        assert page.url.endswith("/account") and page.locator("#delete-data").count() == 1
        phone.close()
        browser.close()
    assert errors == []


def test_undo_opens_a_box_and_puts_the_deck_back(tmp_path: Path) -> None:
    """The review page's Undo: the box with the deck, the list of what goes back and the one Undo
    button, locked for a moment; the press puts the deck back. The form still posts without it."""
    import asyncio

    from playwright.sync_api import expect, sync_playwright

    from mtg_gateway.decks import REVIEW_PAGE, current_client
    from mtg_gateway.pages import BROWSER_CLIENT_ID

    from .test_editor_browser import _link

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    server = Server(tmp_path, writes_enabled=True)
    server.start()
    try:
        sid = _link(server)
        decks = server.app.state.gateway.decks

        async def applied() -> str:
            current_client.set(BROWSER_CLIENT_ID)
            p = await decks.propose("user-1", "42", [{"action": "add", "card_name": "Opt"}])
            await decks.apply("user-1", p["proposal_id"], via=REVIEW_PAGE, version=1)
            return p["proposal_id"]

        pid = asyncio.run(applied())
        assert any(c["card"]["oracleCard"]["name"] == "Opt" for c in server.ark.decks[42]["cards"])
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/proposals/{pid}", wait_until="networkidle")
            page.locator("form[data-undo-box] button").click()
            box = page.locator(".mtgbox-scrim .mtgbox")
            expect(box).to_be_visible()
            expect(box.locator("h2").first).to_have_text("Undo this change?")
            expect(box.locator(".body")).to_contain_text("Undo puts the deck back")
            expect(box.locator(".body li").first).to_contain_text("Opt")
            assert box.locator(".body button").count() == 0  # the page's own buttons are not in the box
            ok = box.locator(".acts button.primary")
            expect(ok).to_have_text("Undo: put the deck back")
            expect(ok).to_be_disabled()
            expect(ok).to_be_enabled(timeout=4000)
            assert page.url.endswith(f"/proposals/{pid}")  # still the review page under the box
            assert page.evaluate("document.documentElement.scrollWidth") <= 390
            ok.click()
            page.wait_for_url("**/proposals/*?ok=undone")  # the undo's own review page
            expect(page.locator(".notice.ok")).to_contain_text("Undone")
            browser.close()
        assert errors == []
        assert not any(c["card"]["oracleCard"]["name"] == "Opt" for c in server.ark.decks[42]["cards"])
    finally:
        server.stop()
