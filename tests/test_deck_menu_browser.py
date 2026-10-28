"""The deck page's direct edits in headless Chromium (static/deck.js): a right-click on a
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
from .test_scan_browser import _chromium_path, _free_port, _serve

pytest.importorskip("playwright")

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAUAAAAHCAIAAAAbd2raAAAAFElEQVR4nGNgYGD4z8DAwMDAwMAAAAwAA/8BpYQAAAAASUVORK5CYII="
)
EDIT_URL = re.compile(r"/api/v1/decks/42/edit$")


class Server:
    """The gateway with writes on, over a real port, with alice's Commander deck 42 (a Sol Ring on
    its maybeboard too, so a side row is on the page)."""

    def __init__(self, tmp_path: Path, **over: object):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        settings = make_settings(
            tmp_path,
            public_url=self.base,
            allowed_hosts=["127.0.0.1", f"127.0.0.1:{self.port}"],
            archidekt_base="https://ark.test/api",
            writes_enabled=True,
            **over,
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
        self.thread = threading.Thread(target=_serve(self.server, self.port), daemon=True)

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
        csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/account/details").text).group(1)
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
        # until the save answers the toast says "Saving…" (a slow CI runner took longer than 200 ms)
        page.wait_for_function(
            "() => /Saved\\. Snapshot kept under History/.test("
            "(document.querySelector('.deck-toast .msg') || {}).textContent || '')",
            timeout=8000,
        )
        assert page.url == url
        # the menu stays open for more steps (0.7.27), showing the saved count
        assert page.locator(".ctxmenu").count() == 1 and menu.locator(".qtyrow .n").inner_text() == "2"
        page.keyboard.press("Escape")
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


def test_undo_of_a_remove_puts_back_printing_finish_tag_and_mana_value(server: Server) -> None:
    """Undo of a removal used to add the card back by name and category only, so a foil Cultivate
    with a color tag and a custom mana value came back as a plain one. The Undo now sends the
    printing and finish with the add, and the tag and mana value after it; the companion and
    "flipped by default" flags go with the add too."""
    from playwright.sync_api import sync_playwright

    row = next(c for c in server.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Cultivate")
    row.update(modifier="Foil", label="Have,#37d67a", customCmc=5, companion=True, flippedDefault=True)
    printing = (row["card"]["edition"]["editioncode"], row["card"]["collectorNumber"])
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Cultivate']").first
        card.scroll_into_view_if_needed()
        card.click()
        page.wait_for_selector(".cardview.open", timeout=3000)
        page.locator(".cardview .acts button", has_text="Remove from deck").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert server.quantity("Cultivate") == 0
        sent: list[list[dict]] = []
        page.on(
            "request",
            lambda r: sent.append(json.loads(r.post_data)["changes"]) if EDIT_URL.search(r.url) else None,
        )
        page.locator(".deck-toast button", has_text="Undo").click()
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        # the add first; the gateway tags only a card in the deck, one tag change per card per save
        assert sent == [
            [
                {
                    "action": "add",
                    "card_name": "Cultivate",
                    "quantity": 1,
                    "set_code": printing[0],
                    "collector_number": printing[1],
                    "finish": "foil",
                    "companion": True,
                    "flipped_default": True,
                }
            ],
            [{"action": "set_label", "card_name": "Cultivate", "label": "Have", "color": "#37d67a"}],
            [{"action": "set_mana_value", "card_name": "Cultivate", "mana_value": 5}],
        ], sent
        rows = [c for c in server.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Cultivate"]
        assert len(rows) == 1, rows
        got = rows[0]
        assert (got["card"]["edition"]["editioncode"], got["card"]["collectorNumber"]) == printing
        assert got["modifier"] == "Foil" and got["label"] == "Have,#37d67a" and got["customCmc"] == 5, got
        assert got["quantity"] == 1 and got["companion"] is True and got["flippedDefault"] is True, got
        # and the page shows it as it was
        again = page.locator(".deckview .c[data-card='Cultivate']").first
        assert again.get_attribute("data-finish") == "Foil" and again.get_attribute("data-mv") == "5"
        assert again.get_attribute("data-label") == "Have,#37d67a"
        assert errors == []
        browser.close()


def test_undo_of_a_maybeboard_remove_puts_back_its_printing(server: Server) -> None:
    """A maybeboard row's Undo of a removal used to leave its printing out (a maybeboard add took
    none), so the row came back in a printing Archidekt picked. Since 0.7.33 it sends the printing."""
    from playwright.sync_api import sync_playwright

    row = next(
        c
        for c in server.ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == "Sol Ring" and "Maybeboard" in (c["categories"] or [])
    )
    row["card"] = {**row["card"], **server.ark.printing(124026)}  # SLD 1074, not the one picked by name
    printing = (row["card"]["edition"]["editioncode"], row["card"]["collectorNumber"])
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Sol Ring'][data-zone='side']").first
        card.scroll_into_view_if_needed()
        card.click()
        page.wait_for_selector(".cardview.open", timeout=3000)
        # (a side row's viewer says which category it leaves)
        page.locator(".cardview .acts button", has_text="Remove from “Maybeboard”").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert server.quantity("Sol Ring", side=True) == 0
        sent: list[list[dict]] = []
        page.on(
            "request",
            lambda r: sent.append(json.loads(r.post_data)["changes"]) if EDIT_URL.search(r.url) else None,
        )
        page.locator(".deck-toast button", has_text="Undo").click()
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        (first,) = sent[0]
        assert first["zone"] == "side" and (first["set_code"], first["collector_number"]) == printing, sent
        rows = [
            c
            for c in server.ark.decks[42]["cards"]
            if c["card"]["oracleCard"]["name"] == "Sol Ring" and "Maybeboard" in (c["categories"] or [])
        ]
        assert [(r["card"]["edition"]["editioncode"], r["card"]["collectorNumber"]) for r in rows] == [
            printing
        ]
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
    and drop moves the row and saves it at once (no "Save moves" bar any more): one
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
        # the drop saves at once: set_category with category (not categories) and the side zone
        with page.expect_request(EDIT_URL) as req:
            page.drag_and_drop(source, ".stack[data-group='Commander'] .stackhead")
        body = json.loads(req.value.post_data)
        assert body["changes"] == [
            {"action": "set_category", "card_name": "Sol Ring", "category": "Commander", "zone": "side"}
        ]
        # filed under Commander, Sol Ring becomes a second commander: a commander change asks first
        dialog = page.locator(".deck-toast[role='alertdialog']")
        dialog.wait_for(timeout=5000)
        assert "This changes the deck's commander. Save anyway?" in dialog.inner_text()
        dialog.locator("button", has_text="Save anyway").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        page.wait_for_timeout(200)
        assert page.url == url and page.locator(".movebar").count() == 0  # no staging bar
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
        assert page.locator(".histtools button", has_text="Undo").is_disabled()  # nor in the history
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


def test_a_hold_that_also_starts_the_browsers_drag_leaves_no_card_stuck(server: Server) -> None:
    """Chrome on Android starts its own drag and drop from a press and hold on a draggable card:
    the touch is cancelled, dragstart fires, the menu opens where the finger lets go and dragend
    never comes. That sequence, replayed here, used to leave the card faded (.dragging) and every
    later tap on a card ignored. Now the card is not draggable under a finger, the drag is
    refused, the menu still opens, and the next tap on another card opens it."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 900, has_touch=True, is_mobile=True)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Cultivate']").first
        card.scroll_into_view_if_needed()
        box = card.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + 30
        cdp = page.context.new_cdp_session(page)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        page.wait_for_timeout(450)
        assert card.get_attribute("draggable") == "false"  # no browser drag under a finger
        # what Android does next: its drag starts (cancelling the touch) and, the finger letting
        # go without moving, its context menu event; no dragend follows
        refused = card.evaluate(
            """(c) => {
                const dt = new DataTransfer();
                const ev = new DragEvent("dragstart", {bubbles: true, cancelable: true, dataTransfer: dt});
                c.dispatchEvent(ev);
                return ev.defaultPrevented;
            }"""
        )
        assert refused
        cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
        card.evaluate(
            """(c, xy) => c.dispatchEvent(new MouseEvent("contextmenu",
                {bubbles: true, cancelable: true, clientX: xy[0], clientY: xy[1]}))""",
            [x, y],
        )
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        assert menu.locator(".head").inner_text() == "Cultivate"
        assert "dragging" not in (card.get_attribute("class") or "")
        assert "carddrag" not in (page.locator(".deckview").get_attribute("class") or "")
        # a tap outside closes the menu, and a tap on another card opens that card
        other = page.locator(".deckview .c:not([data-card='Cultivate'])").first
        name = other.get_attribute("data-card")
        page.mouse.click(5, 5)
        page.wait_for_timeout(500)
        assert page.locator(".ctxmenu").count() == 0
        other.scroll_into_view_if_needed()
        ob = other.bounding_box()
        ox, oy = ob["x"] + ob["width"] / 2, ob["y"] + 20
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": ox, "y": oy}]})
        page.wait_for_timeout(80)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_selector(".cardview.open", timeout=3000)
        assert name in page.locator(".cardview.open").inner_text()
        assert errors == []
        browser.close()


def test_a_pen_never_starts_the_browsers_drag_and_a_hold_left_over_ends_at_the_next_touch(
    server: Server,
) -> None:
    """A pen (the Fold's S Pen) is treated like a finger: the card is not draggable under it. And a
    hold whose touch never ended (no touchend or touchcancel came) does not stay armed: the next
    touch on another card takes its outline away, so a slide there cannot carry the old card."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 900, has_touch=True, is_mobile=True)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Cultivate']").first
        other = page.locator(".deckview .c:not([data-card='Cultivate'])").first
        card.scroll_into_view_if_needed()
        card.dispatch_event("pointerdown", {"pointerType": "pen", "bubbles": True})
        assert card.get_attribute("draggable") == "false"
        touch = """(c) => {
            const r = c.getBoundingClientRect();
            const t = new Touch({identifier: Date.now(), target: c,
                clientX: r.left + r.width / 2, clientY: r.top + 20});
            c.dispatchEvent(new TouchEvent("touchstart",
                {bubbles: true, cancelable: true, touches: [t], targetTouches: [t], changedTouches: [t]}));
        }"""
        card.evaluate(touch)
        page.wait_for_timeout(450)
        assert "held" in (card.get_attribute("class") or "")
        other.evaluate(touch)  # the first touch never ended
        assert "held" not in (card.get_attribute("class") or "")
        assert errors == []
        browser.close()


def test_a_mouse_drag_the_browser_never_ended_is_cleared_by_the_next_press(server: Server) -> None:
    """A mouse still drags (the card stays draggable for a mouse), and a drag the
    browser started but never ended (no dragend) is cleared by the next press, so the card does
    not stay faded and the next click on a card opens it."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Cultivate']").first
        card.scroll_into_view_if_needed()
        assert card.get_attribute("draggable") == "true"
        started = card.evaluate(
            """(c) => {
                const dt = new DataTransfer();
                const ev = new DragEvent("dragstart", {bubbles: true, cancelable: true, dataTransfer: dt});
                c.dispatchEvent(ev);
                return !ev.defaultPrevented && c.classList.contains("dragging");
            }"""
        )
        assert started  # a mouse drag still starts
        other = page.locator(".deckview .c:not([data-card='Cultivate'])").first
        name = other.get_attribute("data-card")
        other.scroll_into_view_if_needed()
        other.click()
        page.wait_for_selector(".cardview.open", timeout=3000)
        assert name in page.locator(".cardview.open").inner_text()
        assert "dragging" not in (card.get_attribute("class") or "")
        assert errors == []
        browser.close()


def test_a_declined_confirmation_rejects_the_proposal_it_made(server: Server) -> None:
    """The edit endpoint makes the proposal before it asks; a removal asks in the shared box (0.7.44
    M3), and saying no (Keep them or the box's ×) rejects that proposal, so it never waits pending
    until it expires."""
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
            dialog = page.locator(".mtgbox-scrim .mtgbox")
            dialog.wait_for(timeout=5000)
            assert dialog.locator("h2").inner_text() == "Remove 9 cards?"
            assert dialog.locator(".acts button.danger").inner_text().strip() == "Remove 9 cards"
            if close == "Dismiss":
                dialog.locator("button[aria-label='Close']").click()
            else:
                dialog.locator("button", has_text="Keep them").click(timeout=3000)
            gone = "() => !document.querySelector('.mtgbox-scrim')"
            page.wait_for_function(gone, timeout=3000)
            page.wait_for_timeout(200)
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        assert rejected == ["POST"] * 3  # one reject per declined question, never two
        assert page.locator(".deckview .c[data-card='Cultivate']").count() == 1  # nothing changed
        assert server.quantity("Cultivate") == 1
        assert errors == []
        browser.close()


def _file_under(server: Server, name: str, cats: list[str], **fields) -> None:
    """Give the deck's ``name`` row these categories (made deck categories, counted in the deck)."""
    deck = server.ark.decks[42]
    for cat in cats:
        if not any(c["name"] == cat for c in deck["categories"]):
            deck["categories"].append({"name": cat, "isPremier": False, "includedInDeck": True})
    row = next(c for c in deck["cards"] if c["card"]["oracleCard"]["name"] == name)
    row.update(categories=list(cats), **fields)


def _cats(server: Server, name: str) -> list[str]:
    row = next(c for c in server.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == name)
    return row["categories"]


def _edits(page) -> list[list[dict]]:
    """The changes of every request the page sends to its edit endpoint, in order."""
    sent: list[list[dict]] = []
    page.on(
        "request",
        lambda r: sent.append(json.loads(r.post_data)["changes"]) if EDIT_URL.search(r.url) else None,
    )
    return sent


def test_drag_saves_at_once_with_undo_and_redo_buttons_and_keys(server: Server) -> None:
    """A drag between categories saves at once, as the card menu's Move to does, and the
    page's one history undoes and redoes it with the Undo and Redo buttons, the toast's Undo and
    the editor's keys (Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y); the keys do nothing while typing in a field."""
    from playwright.sync_api import sync_playwright

    # Lands sits above Ramp (headless Chromium's drag finds a stack above the card reliably)
    _file_under(server, "Rampant Growth", ["Lands"])
    _file_under(server, "Cultivate", ["Ramp"])
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        sent = _edits(page)
        undo = page.locator(".histtools button", has_text="Undo")
        redo = page.locator(".histtools button", has_text="Redo")
        assert undo.is_disabled() and redo.is_disabled()
        page.drag_and_drop(
            ".stack[data-group='Ramp'] .row[data-card='Cultivate']", ".stack[data-group='Lands'] .stackhead"
        )
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert sent == [[{"action": "set_category", "card_name": "Cultivate", "category": "Lands"}]]
        assert _cats(server, "Cultivate") == ["Lands"] and page.locator(".movebar").count() == 0
        assert page.locator(".stack[data-group='Lands'] .row[data-card='Cultivate']").count() == 1
        assert not undo.is_disabled() and redo.is_disabled()

        def wait_for(text: str) -> None:
            page.locator(".deck-toast .msg", has_text=text).wait_for(timeout=8000)
            page.wait_for_timeout(150)

        # Ctrl+Z with the focus on the page (not in a field) undoes the drop
        page.locator("h1.deckname").click()
        page.keyboard.press("Control+z")
        wait_for("Undone")
        assert sent[-1] == [{"action": "set_category", "card_name": "Cultivate", "category": "Ramp"}]
        assert _cats(server, "Cultivate") == ["Ramp"]
        assert page.locator(".stack[data-group='Ramp'] .row[data-card='Cultivate']").count() == 1
        assert undo.is_disabled() and not redo.is_disabled()
        # Ctrl+Shift+Z redoes it
        page.keyboard.press("Control+Shift+z")
        wait_for("Redone")
        assert sent[-1] == [{"action": "set_category", "card_name": "Cultivate", "category": "Lands"}]
        assert _cats(server, "Cultivate") == ["Lands"]
        # the Undo button, then Ctrl+Y
        undo.click()
        wait_for("Undone")
        assert _cats(server, "Cultivate") == ["Ramp"]
        page.locator("h1.deckname").click()
        page.keyboard.press("Control+y")
        wait_for("Redone")
        assert _cats(server, "Cultivate") == ["Lands"]
        # the toast's Undo is the same history's Undo; then the Redo button
        page.locator(".deck-toast button", has_text="Undo").click()
        wait_for("Undone")
        assert _cats(server, "Cultivate") == ["Ramp"]
        redo.click()
        wait_for("Redone")
        assert _cats(server, "Cultivate") == ["Lands"]
        assert len(sent) == 7
        # typing in a field: Ctrl+Z belongs to the field, nothing is sent
        page.locator("#q").click()
        page.keyboard.press("Control+z")
        page.wait_for_timeout(600)
        assert len(sent) == 7 and _cats(server, "Cultivate") == ["Lands"]
        assert errors == []
        browser.close()


def test_undo_of_a_remove_restores_every_category_and_a_note_is_warned_about(server: Server) -> None:
    """Bug 7: the Undo of a removal put the card back under its first category only; it now sends
    every category. Archidekt keeps a card's notes on its row and the gateway cannot write them,
    so removing a card with a note asks first, and Cancel sends nothing."""
    from playwright.sync_api import sync_playwright

    _file_under(server, "Cultivate", ["Ramp", "Value"], notes="Keep: fetches both basics")
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        sent = _edits(page)
        card = page.locator(".deckview .row[data-card='Cultivate']").first
        assert json.loads(card.get_attribute("data-allcats")) == ["Ramp", "Value"]
        dialog = page.locator(".deck-toast[role='alertdialog']")
        menu = page.locator(".ctxmenu[role=menu]")
        # Cancel: nothing is sent and the card stays
        card.click(button="right")
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitem]", has_text="Remove from deck").click()
        dialog.wait_for(timeout=5000)
        assert "has a card note" in dialog.inner_text() and "cannot write notes" in dialog.inner_text()
        dialog.locator("button", has_text="Cancel").click()
        page.wait_for_timeout(400)
        assert sent == [] and server.quantity("Cultivate") == 1
        page.keyboard.press("Escape")
        # Remove anyway: the removal goes out
        card.click(button="right")
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitem]", has_text="Remove from deck").click()
        dialog.wait_for(timeout=5000)
        dialog.locator("button", has_text="Remove anyway").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert sent == [[{"action": "remove", "card_name": "Cultivate"}]]
        assert server.quantity("Cultivate") == 0
        # Undo: the add names both categories, the primary one first
        page.locator(".deck-toast button", has_text="Undo").click()
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        add = sent[1][0]
        assert add["action"] == "add" and add["category"] == "Ramp", add
        assert add["categories"] == ["Ramp", "Value"], add
        assert _cats(server, "Cultivate") == ["Ramp", "Value"]
        back = page.locator(".deckview .row[data-card='Cultivate']").first
        assert json.loads(back.get_attribute("data-allcats")) == ["Ramp", "Value"]
        assert back.get_attribute("data-notes") is None  # the note did not come back
        # Redo removes it again without asking (it has no note any more)
        page.locator("h1.deckname").click()
        page.keyboard.press("Control+Shift+z")
        page.locator(".deck-toast .msg", has_text="Redone").wait_for(timeout=8000)
        assert sent[-1] == [{"action": "remove", "card_name": "Cultivate"}]
        assert server.quantity("Cultivate") == 0
        assert errors == []
        browser.close()


def test_undo_of_a_move_files_a_card_back_under_every_category(server: Server) -> None:
    """Move to of a card filed under several categories has an Undo, as a single-category
    move does; the Undo's set_category names every category it had, the primary first. Redo
    moves it again."""
    from playwright.sync_api import sync_playwright

    _file_under(server, "Cultivate", ["Ramp", "Value"])
    _file_under(server, "Rampant Growth", ["Lands"])
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        sent = _edits(page)
        card = page.locator(".deckview .row[data-card='Cultivate']").first
        assert json.loads(card.get_attribute("data-allcats")) == ["Ramp", "Value"]
        menu = page.locator(".ctxmenu[role=menu]")
        card.evaluate("e => e.scrollIntoView({block: 'center'})")  # room below it for the whole menu
        card.click(button="right")
        menu.wait_for(timeout=3000)
        menu.locator(".more").click()
        menu.locator(".sub button", has_text="Lands").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert sent == [[{"action": "set_category", "card_name": "Cultivate", "category": "Lands"}]]
        assert _cats(server, "Cultivate") == ["Lands"]
        undo = page.locator(".histtools button", has_text="Undo")
        assert not undo.is_disabled()
        page.locator(".deck-toast button", has_text="Undo").click()
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert sent[-1] == [
            {
                "action": "set_category",
                "card_name": "Cultivate",
                "category": "Ramp",
                "categories": ["Ramp", "Value"],
            }
        ]
        assert _cats(server, "Cultivate") == ["Ramp", "Value"]
        back = page.locator(".stack[data-group='Ramp'] .row[data-card='Cultivate']")
        assert back.count() == 1 and json.loads(back.get_attribute("data-allcats")) == ["Ramp", "Value"]
        page.locator("h1.deckname").click()
        page.keyboard.press("Control+Shift+z")
        page.locator(".deck-toast .msg", has_text="Redone").wait_for(timeout=8000)
        assert sent[-1] == [{"action": "set_category", "card_name": "Cultivate", "category": "Lands"}]
        assert _cats(server, "Cultivate") == ["Lands"]
        assert errors == []
        browser.close()


def test_drag_onto_the_stack_directly_below_in_the_stacks_view(server: Server) -> None:
    """In the stacks view the cards after the one under the pointer fan out downwards, past
    the bottom of their stack and over the top of the stack directly below. A native drag kept
    that fan open, so a drop on the lower stack's head landed on the dragged card's own stack and
    did nothing. While a card is dragged the fan stays closed: the drop reaches the lower stack."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        # Fold width: three columns, Creature (the tallest) with Land directly below it
        browser, page, errors = _page(p, server, sid, 673)
        page.goto(f"{server.base}/decks/42?view=stacks", wait_until="networkidle")
        sent = _edits(page)
        creature = page.locator(".stack[data-group='Creature']")
        land = page.locator(".stack[data-group='Land']")
        c, ld = creature.bounding_box(), land.bounding_box()
        assert abs(c["x"] - ld["x"]) < 2 and c["y"] + c["height"] < ld["y"]  # the same column, below
        page.evaluate("y => window.scrollBy(0, y)", ld["y"] - 650)
        # the third card from the end: the two after it fan out over Land's head
        source = creature.locator(".c").nth(-3)
        name = source.get_attribute("data-card")
        s = source.bounding_box()
        head = land.locator(".stackhead").bounding_box()
        x0, y0 = s["x"] + s["width"] / 2, s["y"] + 12
        page.mouse.move(x0, y0)
        page.wait_for_timeout(300)  # the fan opens under the pointer
        page.mouse.down()
        page.mouse.move(x0, y0 + 8, steps=2)  # the drag starts on this card
        page.mouse.move(head["x"] + head["width"] / 2, head["y"] + head["height"] / 2, steps=8)
        page.mouse.up()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert sent == [[{"action": "set_category", "card_name": name, "category": "Land"}]]
        assert land.locator(f".c[data-card={json.dumps(name)}]").count() == 1
        assert errors == []
        browser.close()


def test_a_late_refresh_that_takes_a_card_off_closes_its_menu(server: Server) -> None:
    """A save whose re-read was stale reads the deck once more a moment later; when
    that read no longer has the card the menu is open on, the menu closes with it (it would
    otherwise act on a card that is gone from the page)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()

    def answer(route) -> None:
        got = route.fetch().json()
        if json.loads(route.request.post_data or "{}").get("refresh"):
            got["rows"] = [r for r in got.get("rows") or [] if r["name"] != "Cultivate"]
        else:
            got["stale"] = True
        route.fulfill(status=201, json=got)

    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        page.route(EDIT_URL, answer)
        card = page.locator(".deckview .c[data-card='Cultivate']").first
        card.scroll_into_view_if_needed()
        card.click(button="right")
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        with page.expect_request(lambda r: EDIT_URL.search(r.url) and "refresh" in (r.post_data or "")):
            menu.locator(".qtyrow .step").nth(1).click()
            # the menu stays open for more steps while the page waits to read the deck again
            page.locator(".deck-toast .msg", has_text="Refreshing").wait_for(timeout=8000)
            assert page.locator(".ctxmenu").count() == 1
        page.locator(".deck-toast .msg", has_text="Saved. Snapshot kept").wait_for(timeout=8000)
        assert page.locator(".deckview .c[data-card='Cultivate']").count() == 0
        assert page.locator(".ctxmenu").count() == 0
        assert errors == []
        browser.close()


def test_the_card_menu_switches_companion_and_back_face(server: Server) -> None:
    """Archidekt's card menu switches (parity release 1): Companion on any card, Show back face on a
    two-faced card only; each saves in the background and the card shows it at once."""
    from playwright.sync_api import sync_playwright

    server.ark.add_side_row(42, "Delver of Secrets // Insectile Aberration", category="Sideboard")
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Cultivate']").first
        card.scroll_into_view_if_needed()
        card.click(button="right")
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        comp = menu.locator("[role=menuitemcheckbox]", has_text="Companion")
        assert comp.get_attribute("aria-checked") == "false"
        assert menu.locator("[role=menuitemcheckbox]", has_text="Show back face").count() == 0
        with page.expect_request(EDIT_URL) as req:
            comp.click()
        assert json.loads(req.value.post_data)["changes"] == [
            {"action": "set_companion", "card_name": "Cultivate", "zone": "main", "companion": True}
        ]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        row = next(c for c in server.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Cultivate")
        assert row["companion"] is True and card.get_attribute("data-companion") == "1"
        delver = page.locator(".deckview .c[data-card^='Delver of Secrets']").first
        delver.scroll_into_view_if_needed()
        delver.click(button="right")
        menu.wait_for(timeout=3000)
        with page.expect_request(EDIT_URL) as req:
            menu.locator("[role=menuitemcheckbox]", has_text="Show back face").click()
        assert json.loads(req.value.post_data)["changes"][0]["back_face"] is True
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert errors == []
        browser.close()


MENU_ROWS = """() => [...document.querySelectorAll(
    '.ctxmenu[role=menu] [role=menuitem], .ctxmenu[role=menu] [role=menuitemcheckbox], ' +
    '.ctxmenu[role=menu] .qtyrow')]
  .filter(e => e.getClientRects().length)
  .map(e => { const r = e.getBoundingClientRect(); return { text: e.textContent.trim().slice(0, 24),
    cls: e.className, h: r.height, w: r.width, x: r.left }; })"""

MENU_TEXT_X = """() => [...document.querySelectorAll(
    '.ctxmenu[role=menu] button[role=menuitem], .ctxmenu[role=menu] a[role=menuitem], ' +
    '.ctxmenu[role=menu] [role=menuitemcheckbox]')]
  .filter(e => e.getClientRects().length && !e.closest('.sub') && !e.closest('.qtyrow'))
  .map(e => { const t = [...e.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
    if (!t) return null; const r = document.createRange(); r.selectNodeContents(t);
    return { text: t.textContent.trim(), x: Math.round(r.getBoundingClientRect().left) }; })
  .filter(Boolean)"""


def test_every_card_menu_row_and_step_is_finger_sized_on_a_phone(server: Server) -> None:
    """UI review 0.7.42 (H1): with a coarse pointer every row of the card menu, the Companion and
    Show back face switches among them, the − and + steps and the Move to list's rows are at least
    44 px tall (the steps 44 wide too), with the menu inside the window."""
    from playwright.sync_api import sync_playwright

    server.ark.add_side_row(42, "Delver of Secrets // Insectile Aberration", category="Sideboard")
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, has_touch=True, is_mobile=True)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        card = page.locator(".deckview .row[data-card^='Delver of Secrets']").first
        card.scroll_into_view_if_needed()
        box = card.bounding_box()
        cdp = page.context.new_cdp_session(page)
        x, y = box["x"] + 60, box["y"] + box["height"] / 2
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        page.wait_for_timeout(500)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        menu.locator("button.more", has_text="Move to").click()
        page.wait_for_timeout(100)
        rows = page.evaluate(MENU_ROWS)
        texts = [r["text"] for r in rows]
        assert "Companion" in texts and "Show back face" in texts and "−" in texts and "+" in texts, texts
        assert any("Maybeboard" in t or "Sideboard" in t for t in texts), texts  # the Move to list is open
        short = [r for r in rows if r["h"] < 44]
        assert not short, short
        steps = [r for r in rows if "step" in r["cls"]]
        assert len(steps) == 2 and all(r["w"] >= 44 for r in steps), steps
        assert _overflow(page) == 0
        assert errors == []
        browser.close()


def test_the_companion_corner_follows_the_switch_in_the_grid_and_the_list(server: Server) -> None:
    """UI review 0.7.42 (M1): the pink corner (role img, labelled Companion) appears on the tile as
    soon as Companion is switched on and goes when it is switched off, without a reload; a list row
    shows and drops the word; a refused save (Undo) puts the mark back as it was."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Cultivate']").first
        corner = card.locator(".corner.comp")
        assert corner.count() == 0
        menu = page.locator(".ctxmenu[role=menu]")

        def switch() -> None:
            card.scroll_into_view_if_needed()
            card.click(button="right")
            menu.wait_for(timeout=3000)
            with page.expect_request(EDIT_URL):
                menu.locator("[role=menuitemcheckbox]", has_text="Companion").click()
            page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)

        switch()
        assert corner.count() == 1 and card.get_attribute("data-companion") == "1"
        assert corner.get_attribute("role") == "img" and corner.get_attribute("aria-label") == "Companion"
        assert corner.bounding_box()["width"] >= 20
        page.reload(wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Cultivate']").first
        corner = card.locator(".corner.comp")
        assert corner.count() == 1
        switch()
        assert corner.count() == 0 and card.get_attribute("data-companion") is None
        # the list view shows the word, and drops it, the same way
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        card = page.locator(".deckview .row[data-card='Cultivate']").first
        mark = card.locator(".compmark")
        assert mark.count() == 0
        switch()
        assert mark.count() == 1 and mark.inner_text() == "Companion"
        switch()
        assert mark.count() == 0
        assert errors == []
        browser.close()


def test_card_menu_rows_without_an_icon_keep_the_slot_so_text_lines_up(server: Server) -> None:
    """UI review 0.7.42 (L5): a switch row that is off has no check, but keeps an empty slot the
    icon's width, so the text of every row starts at the same place."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Cultivate']").first
        card.scroll_into_view_if_needed()
        card.click(button="right")
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        comp = menu.locator("[role=menuitemcheckbox]", has_text="Companion")
        assert comp.get_attribute("aria-checked") == "false"
        assert comp.locator(".ic-slot[aria-hidden=true]").count() == 1 and comp.locator("svg").count() == 0
        starts = page.evaluate(MENU_TEXT_X)
        assert {s["text"] for s in starts} >= {"Open card", "Companion", "Add to collection", "Copy name"}, (
            starts
        )
        xs = {s["x"] for s in starts}
        assert len(xs) == 1, starts
        assert errors == []
        browser.close()


def test_show_back_face_swaps_the_picture_at_once_and_the_viewer_opens_on_it(server: Server) -> None:
    """L6 (Ben, 23 Oct): the card menu's Show back face swaps the tile's picture to the back face
    without a reload, Undo (a refused save) swaps it back, the card view opens on the back with a
    Show front button, and switching it off brings the front back. The fake Scryfall pictures are
    a different PNG per face (tests/fake_faces.py), so the picture the browser drew, not only the
    address, says which face shows."""
    from playwright.sync_api import sync_playwright

    from .fake_faces import FACES, serve_faces

    server.ark.add_side_row(42, "Delver of Secrets // Insectile Aberration", category="Sideboard")
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.route(re.compile(r"https://cards\.scryfall\.io/.*"), serve_faces)  # before the context's PNG
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card^='Delver of Secrets']").first
        img = card.locator("img")
        front, back = card.get_attribute("data-img"), card.get_attribute("data-back")
        assert front and "/front/" in front and back and "/back/" in back
        assert img.get_attribute("src") == front
        menu = page.locator(".ctxmenu[role=menu]")

        def drawn(face: str, where=img) -> None:
            """The picture on screen is that face's PNG (by its size: the back is 260 px wide)."""
            where.scroll_into_view_if_needed()
            page.wait_for_function(
                "([e, w]) => e.complete && e.naturalWidth === w",
                arg=[where.element_handle(), FACES[face][0][0]],
            )

        drawn("front")

        def switch() -> None:
            card.scroll_into_view_if_needed()
            card.click(button="right")
            menu.wait_for(timeout=3000)
            with page.expect_request(EDIT_URL):
                menu.locator("[role=menuitemcheckbox]", has_text="Show back face").click()
            page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)

        switch()
        assert img.get_attribute("src") == back and card.get_attribute("data-flipped") == "1"
        drawn("back")
        # the viewer opens on the back face, with the way to the front
        card.click()
        viewer = page.locator(".cardview.open")
        viewer.wait_for(timeout=3000)
        assert viewer.locator(".pane img").get_attribute("src") == back
        drawn("back", viewer.locator(".pane img"))
        flip = viewer.locator("button.flip")
        assert flip.inner_text() == "Show front" and flip.get_attribute("aria-pressed") == "true"
        flip.click()
        assert viewer.locator(".pane img").get_attribute("src") == front
        page.keyboard.press("Escape")
        viewer.wait_for(state="hidden", timeout=3000)
        page.reload(wait_until="networkidle")
        card = page.locator(".deckview .c[data-card^='Delver of Secrets']").first
        img = card.locator("img")
        assert img.get_attribute("src") == back
        drawn("back")
        switch()
        assert img.get_attribute("src") == front and card.get_attribute("data-flipped") is None
        drawn("front")
        # the list row's hover preview follows too
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        row = page.locator(".deckview .row[data-card^='Delver of Secrets']").first
        row.scroll_into_view_if_needed()
        row.click(button="right")
        menu.wait_for(timeout=3000)
        with page.expect_request(EDIT_URL):
            menu.locator("[role=menuitemcheckbox]", has_text="Show back face").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert row.locator(".hover img").get_attribute("src") == back
        assert errors == []
        browser.close()


def test_a_refused_back_face_save_puts_the_front_picture_back(server: Server) -> None:
    """L6: a save Archidekt refuses is undone on the page: the front picture comes back with the
    switch, and the toast says so."""
    from playwright.sync_api import sync_playwright

    server.ark.add_side_row(42, "Delver of Secrets // Insectile Aberration", category="Sideboard")
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        server.ark.fail_card_writes = True  # Archidekt refuses the save
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card^='Delver of Secrets']").first
        front = card.get_attribute("data-img")
        card.scroll_into_view_if_needed()
        card.click(button="right")
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        with page.expect_request(EDIT_URL):
            menu.locator("[role=menuitemcheckbox]", has_text="Show back face").click()
        err = page.locator(".deck-toast.error .msg")
        err.wait_for(timeout=8000)
        assert err.inner_text().startswith("Show back face not saved:"), err.inner_text()
        assert (
            card.locator("img").get_attribute("src") == front and card.get_attribute("data-flipped") is None
        )
        assert errors == []
        browser.close()
