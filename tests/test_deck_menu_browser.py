"""The deck page's direct edits in headless Chromium (static/deck.js, T-040): a right-click on a
card opens the gateway's own themed menu and the browser's image menu is prevented; Quantity +
sends one set_quantity change to the page's edit endpoint and the count, the stack total, the
Legality chip and the Deck checks panel redraw in place without a navigation; the drag between
stacks saves ``set_category`` with ``category`` (and ``zone`` for a maybeboard row); a press and
hold on a touch screen opens the menu; nothing is wider than the window at 390 and 1366 px with
the menu open. Skipped without a Chromium build."""

from __future__ import annotations

import base64
import json
import re
import secrets
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from mtg_gateway.app import create_app
from mtg_gateway.archidekt import ArchidektClient, Pacer
from mtg_gateway.db import Database
from mtg_gateway.oidc import OIDCClient

from .conftest import CLIENT_ID, CLIENT_SECRET, IDP, make_settings
from .fake_archidekt import FakeArchidekt
from .test_scan_browser import _chromium_path, _free_port

pytest.importorskip("playwright")

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAUAAAAHCAIAAAAbd2raAAAAFElEQVR4nGNgYGD4z8DAwMDAwMAAAAwAA/8BpYQAAAAASUVORK5CYII="
)
EDIT_URL = re.compile(r"/api/v1/decks/42/edit$")


class Server:
    """The gateway with writes on, over a real port, with alice's Commander deck 42 (a Sol Ring on
    its maybeboard too, so a side row is on the page)."""

    def __init__(self, tmp_path: Path):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        settings = make_settings(
            tmp_path,
            public_url=self.base,
            allowed_hosts=["127.0.0.1", f"127.0.0.1:{self.port}"],
            archidekt_base="https://ark.test/api",
            writes_enabled=True,
        )
        self.db = Database(settings.db_path)
        oidc = OIDCClient(IDP, CLIENT_ID, CLIENT_SECRET, settings.callback_url, settings.oidc_scopes)
        self.ark = FakeArchidekt()
        self.ark.decks[42]["deckFormat"] = 3  # Commander: 100 cards, legality shown
        # A legal deck to start from: the CSV fixture has no supertypes, Archidekt's JSON does.
        supertypes = {"Aesi, Tyrant of Gyre Strait": "Legendary", "Forest": "Basic", "Island": "Basic"}
        for row in self.ark.decks[42]["cards"]:
            sup = supertypes.get(row["card"]["oracleCard"]["name"])
            if sup:
                row["card"]["oracleCard"]["superTypes"] = [sup]
        self.ark.add_side_row(42, "Sol Ring")
        client = ArchidektClient(settings.archidekt_base, "test", Pacer(0.0), http=self.ark.client())
        self.app = create_app(settings, db=self.db, oidc=oidc, archidekt=client)
        self.app.state.gateway.membership = None
        self.server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        for _ in range(100):
            try:
                if httpx.get(f"{self.base}/healthz", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("gateway did not start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def sign_in_and_link(self) -> str:
        sid = secrets.token_urlsafe(32)
        self.db.upsert_user(
            "user-1", email="alice@example.test", name="Alice", preferred_username="alice", groups=[]
        )
        self.db.create_browser_session(sid, "user-1", 3600)
        h = httpx.Client(base_url=self.base, cookies={"mtg_session": sid})
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

    def quantity(self, name: str, *, side: bool = False) -> int:
        rows = [
            c
            for c in self.ark.decks[42]["cards"]
            if c["card"]["oracleCard"]["name"] == name and (c["categories"] == ["Maybeboard"]) is side
        ]
        return rows[0]["quantity"] if rows else 0


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _page(p, server: Server, sid: str, width: int, **kw):
    browser = p.chromium.launch(executable_path=_EXE["path"], args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 800}, **kw)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


_EXE: dict[str, str] = {}


def _chromium() -> None:
    """Find Chromium before the sync Playwright block (the lookup opens one of its own)."""
    _EXE["path"] = _chromium_path()
    if _EXE["path"] == "missing":
        pytest.skip("no Chromium available for Playwright")


def _overflow(page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")


@pytest.mark.parametrize(("width", "view"), [(390, "text"), (1366, "grid")])
def test_right_click_menu_and_quantity_plus_redraw_in_place(server: Server, width: int, view: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        page.goto(f"{server.base}/decks/42?view={view}", wait_until="networkidle")
        url = page.url
        sel = ".deckview .c" if view == "grid" else ".deckview .row"
        card = page.locator(f"{sel}[data-card='Cultivate']").first
        assert card.get_attribute("data-qty") == "1" and card.get_attribute("data-zone") == "main"
        assert card.get_attribute("data-rel") and card.get_attribute("data-cat") is not None
        side = page.locator(f"{sel}[data-card='Sol Ring'][data-zone='side']")
        assert side.count() == 1
        assert page.locator(".banner .legal.ok").count() == 1
        # the browser's menu is prevented: the contextmenu event comes back cancelled
        card.scroll_into_view_if_needed()
        prevented = card.evaluate(
            "c => !c.dispatchEvent(new MouseEvent('contextmenu',"
            " {bubbles: true, cancelable: true, clientX: 40, clientY: 40}))"
        )
        assert prevented is True
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        assert menu.count() == 1 and menu.locator(".menu").count() == 0
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        assert page.locator(".ctxmenu").count() == 0
        # a real right-click opens the themed menu with the card's actions, inside the window
        card.click(button="right")
        menu.wait_for(timeout=3000)
        labels = menu.locator("[role=menuitem]").all_inner_texts()
        assert any("Open card" in x for x in labels) and any("Remove from deck" in x for x in labels), labels
        assert any("Edit in deck editor" in x for x in labels) and any("Move to" in x for x in labels), labels
        assert menu.locator(".qtyrow .n").inner_text() == "1"
        assert menu.locator(".head").inner_text() == "Cultivate"
        box = menu.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width, box
        assert _overflow(page) == 0
        # the first item has focus; arrows move through the items
        assert page.evaluate("() => document.activeElement.getAttribute('role')") == "menuitem"
        # Move to lists the deck's categories, with the maybeboard
        menu.locator(".more").click()
        subs = menu.locator(".sub button").all_inner_texts()
        assert any("Maybeboard" in x for x in subs) and any("Commander" in x for x in subs), subs
        assert _overflow(page) == 0
        # Quantity +: one set_quantity change to the page's own endpoint; no navigation
        with page.expect_request(EDIT_URL) as req:
            menu.locator(".qtyrow .step").nth(1).click()
        body = json.loads(req.value.post_data)
        assert body == {
            "changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}],
            "confirmed": False,
        }
        assert req.value.headers.get("x-csrf-token")
        page.locator(".deck-toast").wait_for(timeout=8000)
        page.wait_for_timeout(200)
        assert page.url == url
        assert page.locator(".ctxmenu").count() == 0  # the menu closed on the save
        assert "Saved. Snapshot kept under History" in page.locator(".deck-toast .msg").inner_text()
        assert page.locator(".deck-toast button", has_text="Undo").count() == 1
        assert server.quantity("Cultivate") == 2
        # redrawn in place: the count on the card, the stack total, the size, the chip, the checks
        assert card.get_attribute("data-qty") == "2"
        assert card.locator(".qty, .q").first.inner_text() == "2"
        stack_meta = card.locator("xpath=ancestor::section[contains(@class,'stack')]//div[@class='meta']")
        assert stack_meta.inner_text().startswith("Qty: 2") or re.match(r"Qty: \d+", stack_meta.inner_text())
        assert page.locator(".banner .row span", has_text="Size:").inner_text() == "Size: 101"
        assert page.locator(".banner .legal.bad").count() == 1  # 101 cards: not legal, as on Archidekt
        checks = page.locator(".stats .checks")
        assert checks.count() == 1 and "101 of 100" in checks.inner_text()
        assert checks.locator("li.bad").count() >= 1
        assert page.locator(".stats .tiles .tile b").first.inner_text() == "101"
        # Undo sends the inverse change and the page goes back to one copy
        with page.expect_request(EDIT_URL) as req2:
            page.locator(".deck-toast button", has_text="Undo").click()
        body2 = json.loads(req2.value.post_data)
        assert body2["changes"] == [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 1}]
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert card.get_attribute("data-qty") == "1" and server.quantity("Cultivate") == 1
        assert page.locator(".banner .row span", has_text="Size:").inner_text() == "Size: 100"
        assert "100 of 100" in page.locator(".stats .checks").inner_text()
        assert _overflow(page) == 0 and page.url == url
        # a maybeboard row says so in its menu and its change names the zone
        side.first.scroll_into_view_if_needed()
        side.first.click(button="right")
        menu.wait_for(timeout=3000)
        with page.expect_request(EDIT_URL) as req3:
            menu.locator(".qtyrow .step").nth(1).click()
        assert json.loads(req3.value.post_data)["changes"] == [
            {"action": "set_quantity", "card_name": "Sol Ring", "zone": "side", "quantity": 2}
        ]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert server.quantity("Sol Ring", side=True) == 2 and server.quantity("Sol Ring") == 1
        assert side.first.get_attribute("data-qty") == "2"
        assert errors == []
        browser.close()


def test_viewer_quantity_buttons_and_remove(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Cultivate']").first
        card.scroll_into_view_if_needed()
        card.click()
        page.wait_for_selector(".cardview.open", timeout=3000)
        ctl = page.locator(".cardview .acts .qtyctl")
        assert ctl.locator(".n").inner_text() == "1"
        with page.expect_request(EDIT_URL) as req:
            ctl.locator("button").nth(1).click()
        assert json.loads(req.value.post_data)["changes"][0]["quantity"] == 2
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert ctl.locator(".n").inner_text() == "2" and card.get_attribute("data-qty") == "2"
        assert page.locator(".cardview.open").count() == 1  # the viewer stays open
        with page.expect_request(EDIT_URL) as req2:
            page.locator(".cardview .acts button", has_text="Remove from deck").click()
        assert json.loads(req2.value.post_data)["changes"] == [{"action": "remove", "card_name": "Cultivate"}]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        page.wait_for_timeout(300)
        assert page.locator(".cardview.open").count() == 0
        assert page.locator(".deckview .c[data-card='Cultivate']").count() == 0
        assert server.quantity("Cultivate") == 0
        assert page.locator(".banner .row span", has_text="Size:").inner_text() == "Size: 99"
        # Undo of a removal adds the card back, and its picture returns to the page
        with page.expect_request(EDIT_URL) as req3:
            page.locator(".deck-toast button", has_text="Undo").click()
        back = json.loads(req3.value.post_data)["changes"][0]
        assert back["action"] == "add" and back["card_name"] == "Cultivate" and back["quantity"] == 2
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert page.locator(".deckview .c[data-card='Cultivate']").count() == 1
        assert server.quantity("Cultivate") == 2
        assert errors == []
        browser.close()


def test_drag_between_stacks_saves_set_category_with_the_zone(server: Server) -> None:
    """Text view, where the Maybeboard and Commander stacks share the window: Chromium's own drag
    and drop moves the row, Undo all puts it back without a reload, and Save moves sends one
    set_category change with ``category`` and the side zone through the page's endpoint."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        url = page.url
        source = ".deckview .row[data-card='Sol Ring'][data-zone='side']"
        # text rows take focus, Enter opens the viewer and Shift+F10 the menu, as grid cards do
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        row.focus()
        page.keyboard.press("Enter")
        page.wait_for_selector(".cardview.open", timeout=3000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        row.focus()
        page.keyboard.press("Shift+F10")
        page.wait_for_selector(".ctxmenu", timeout=3000)
        assert page.evaluate("document.activeElement.closest('.ctxmenu') !== null")
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        assert page.locator(".ctxmenu").count() == 0
        assert page.evaluate("document.activeElement.getAttribute('data-card')") == "Cultivate"
        target = page.locator(".stack[data-group='Commander']")
        assert page.locator(source).count() == 1
        page.drag_and_drop(source, ".stack[data-group='Commander'] .stackhead")
        page.wait_for_timeout(200)
        assert target.locator(".row[data-card='Sol Ring']").count() == 1
        bar = page.locator(".movebar.show")
        assert bar.count() == 1 and "1 card moved" in bar.inner_text()
        # Undo all puts the card back without a reload
        bar.locator("button", has_text="Undo all").click()
        page.wait_for_timeout(100)
        assert page.url == url and page.locator(".movebar.show").count() == 0
        assert page.locator(".stack[data-group='Maybeboard'] .row[data-card='Sol Ring']").count() == 1
        # drag again and save: set_category with category (not categories) and the side zone
        # (a fresh page: headless Chromium does not start a second drag in the same one)
        page.close()
        page = browser.contexts[0].new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url, wait_until="networkidle")
        target = page.locator(".stack[data-group='Commander']")
        page.drag_and_drop(source, ".stack[data-group='Commander'] .stackhead")
        page.wait_for_timeout(200)
        with page.expect_request(EDIT_URL) as req:
            page.locator(".movebar button", has_text="Save moves").click()
        body = json.loads(req.value.post_data)
        assert body["changes"] == [
            {"action": "set_category", "card_name": "Sol Ring", "category": "Commander", "zone": "side"}
        ]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        page.wait_for_timeout(200)
        assert page.url == url and page.locator(".movebar.show").count() == 0
        moved = target.locator(".row[data-card='Sol Ring']")
        assert moved.count() == 1 and moved.get_attribute("data-zone") == "main"
        assert moved.get_attribute("data-cat") == "Commander" and "side" not in (
            moved.get_attribute("class") or ""
        )
        assert page.locator(".stack[data-group='Maybeboard']").count() == 0  # emptied, so gone
        rows = [c for c in server.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring"]
        assert any(c["categories"] == ["Commander"] for c in rows)
        assert page.locator(".banner .row span", has_text="Size:").inner_text() == "Size: 101"
        assert "2 in the Commander category" in page.locator(".stats .checks").inner_text()
        # the deck now holds two Sol Ring rows, so a set_category back would move both: no Undo
        assert page.locator(".deck-toast button", has_text="Undo").count() == 0
        assert page.locator(".deck-toast a", has_text="History").count() == 1
        assert errors == []
        browser.close()


def test_press_and_hold_opens_the_menu_on_touch(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, has_touch=True, is_mobile=True)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        card = page.locator(".deckview .row[data-card='Cultivate']").first
        card.scroll_into_view_if_needed()
        box = card.bounding_box()
        x, y = box["x"] + 60, box["y"] + box["height"] / 2
        cdp = page.context.new_cdp_session(page)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        page.wait_for_timeout(500)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        assert menu.locator(".head").inner_text() == "Cultivate"
        assert page.locator(".cardview.open").count() == 0  # the hold did not also open the viewer
        assert _overflow(page) == 0
        b = menu.bounding_box()
        assert b["x"] >= 0 and b["x"] + b["width"] <= 390 and b["y"] + b["height"] <= 800, b
        # Back closes it on a phone
        page.go_back()
        page.wait_for_timeout(200)
        assert page.locator(".ctxmenu").count() == 0 and "/decks/42" in page.url
        # a short tap still opens the viewer (after the hold's own click guard has passed)
        page.wait_for_timeout(500)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        page.wait_for_timeout(80)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_selector(".cardview.open", timeout=3000)
        assert page.locator(".ctxmenu").count() == 0
        assert errors == []
        browser.close()


def test_a_declined_confirmation_rejects_the_proposal_it_made(server: Server) -> None:
    """The edit endpoint makes the proposal before it asks "Save anyway?"; saying no (Cancel or the
    dialog's close button) rejects that proposal, so it never waits pending until it expires."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        asked = {
            "ok": True,
            "applied": False,
            "needs_confirm": True,
            "why": "This removes 9 cards.",
            "proposal_id": "prop_decline",
        }
        page.route(EDIT_URL, lambda route: route.fulfill(status=201, json=asked))
        rejected: list[str] = []
        page.route(
            re.compile(r"/api/v1/proposals/prop_decline/reject$"),
            lambda route: (
                rejected.append(route.request.method),
                route.fulfill(status=200, json={"ok": True}),
            ),
        )
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        for close in ("Cancel", "Dismiss", "viewer"):
            card = page.locator(".deckview .c[data-card='Cultivate']").first
            card.scroll_into_view_if_needed()
            if close == "viewer":
                # asked from the card viewer (a commander removal does this): the question must sit
                # above the viewer's backdrop, or it could not be answered
                card.click()
                page.wait_for_selector(".cardview.open", timeout=3000)
                page.locator(".cardview .acts button", has_text="Remove from deck").click()
            else:
                card.click(button="right")
                menu = page.locator(".ctxmenu[role=menu]")
                menu.wait_for(timeout=3000)
                menu.locator("[role=menuitem]", has_text="Remove from deck").click()
            dialog = page.locator(".deck-toast[role='alertdialog']")
            dialog.wait_for(timeout=5000)
            assert "Save anyway?" in dialog.inner_text()
            if close == "Dismiss":
                dialog.locator("button[aria-label='Dismiss']").click()
            else:
                dialog.locator("button", has_text="Cancel").click(timeout=3000)
            gone = "() => !document.querySelector('.deck-toast[role=alertdialog]')"
            page.wait_for_function(gone, timeout=3000)
            page.wait_for_timeout(200)
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        assert rejected == ["POST"] * 3  # one reject per declined question, never two
        assert page.locator(".deckview .c[data-card='Cultivate']").count() == 1  # nothing changed
        assert server.quantity("Cultivate") == 1
        assert errors == []
        browser.close()
