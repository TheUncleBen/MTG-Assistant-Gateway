"""The printing picker (static/printpick.js) in headless Chromium: the deck page's card menu has
"Change printing…" (the deck proper's rows only); the picker searches by set name or code,
filters by finish, frame and border, sorts newest, oldest or cheapest, hides digital-only and
preview printings until the switch shows them (labelled), never lists another language, and
pages past Scryfall's page with Load more (filtered on Scryfall's side then). Hovering a tile
shows the whole card; "Use this printing" saves at once through the deck edit endpoint, with
Undo putting the old printing and finish back, and a printing not made in the card's finish
moves the finish and the Saved message says so (P6). With writes off the change is kept as a
proposal. The deck editor's row menu and its add box use the same picker and queue the change.
At 360, 673 and 1280 px in both themes nothing scrolls sideways, no control of the dialog is
covered, text keeps 4.5:1 and touch controls are 44 px. The card menu opened near the bottom of
the window keeps its "Move to" list usable. Screenshots go to MTG_PICKER_SHOTS (a folder) when
set. Skipped without a Chromium build."""

from __future__ import annotations

import json
import os
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
from mtg_gateway.scan.scryfall import ScryfallClient

from .conftest import CLIENT_ID, CLIENT_SECRET, IDP, make_settings
from .fake_archidekt import FakeArchidekt
from .fake_printings import sol_ring_printings
from .fake_scryfall import FakeScryfall
from .test_card_search_filters_browser import CONTRAST
from .test_deck_menu_browser import PNG
from .test_overflow_sweep_browser import MEASURE
from .test_scan_browser import _chromium_path, _free_port

pytest.importorskip("playwright")

EDIT_URL = re.compile(r"/api/v1/decks/42/edit$")
SHOTS = os.environ.get("MTG_PICKER_SHOTS", "")


class Server:
    """The gateway over a real port with alice's deck 42 (a Sol Ring in the deck, CMR 472, and one
    on the maybeboard) and a Scryfall that knows Sol Ring's made-up printings (fake_printings)."""

    def __init__(self, tmp_path: Path, *, writes: bool = True, fillers: int = 0, page_size: int = 175):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        settings = make_settings(
            tmp_path,
            public_url=self.base,
            allowed_hosts=["127.0.0.1", f"127.0.0.1:{self.port}"],
            archidekt_base="https://ark.test/api",
            writes_enabled=writes,
        )
        self.db = Database(settings.db_path)
        oidc = OIDCClient(IDP, CLIENT_ID, CLIENT_SECRET, settings.callback_url, settings.oidc_scopes)
        self.ark = FakeArchidekt()
        self.ark.add_side_row(42, "Sol Ring")
        client = ArchidektClient(settings.archidekt_base, "test", Pacer(0.0), http=self.ark.client())
        self.app = create_app(settings, db=self.db, oidc=oidc, archidekt=client)
        self.app.state.gateway.membership = None
        self.sf = FakeScryfall()
        self.sf.extra_cards = sol_ring_printings(fillers)
        self.sf.page_size = page_size
        self.app.state.gateway.scan.scryfall = ScryfallClient(
            "https://scryfall.test", min_interval=0.0, http=self.sf.client()
        )
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

    def sol_ring(self) -> list[tuple[str, str, str]]:
        """The deck proper's Sol Ring rows as (set, number, finish)."""
        return [
            (c["card"]["edition"]["editioncode"], c["card"]["collectorNumber"], c.get("modifier") or "Normal")
            for c in self.ark.decks[42]["cards"]
            if c["card"]["oracleCard"]["name"] == "Sol Ring" and c["categories"] != ["Maybeboard"]
        ]


def _server(tmp_path: Path, **kw):
    s = Server(tmp_path, **kw)
    s.start()
    return s


@pytest.fixture
def server(tmp_path: Path):
    s = _server(tmp_path)
    try:
        yield s
    finally:
        s.stop()


_EXE: dict[str, str] = {}


def _chromium() -> None:
    """Find Chromium before the sync Playwright block (the lookup opens one of its own)."""
    _EXE["path"] = _chromium_path()
    if _EXE["path"] == "missing":
        pytest.skip("no Chromium available for Playwright")


def _page(
    p, server: Server, sid: str, width: int, *, theme: str = "light", height: int = 900, touch: bool = False
):
    browser = p.chromium.launch(executable_path=_EXE["path"], args=["--no-sandbox"])
    kw = {"has_touch": True, "is_mobile": True} if touch else {}
    ctx = browser.new_context(viewport={"width": width, "height": height}, **kw)
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": sid, "url": server.base},
            {"name": "mtg_theme", "value": theme, "url": server.base},
        ]
    )
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


def _open_menu(page, card) -> None:
    """The card's own menu, as a right-click (or a press and hold) opens it."""
    card.scroll_into_view_if_needed()
    box = card.bounding_box()
    card.evaluate(
        "(c, xy) => c.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true,"
        " clientX: xy[0], clientY: xy[1]}))",
        [box["x"] + min(60, box["width"] / 2), box["y"] + box["height"] / 2],
    )
    page.locator(".ctxmenu[role=menu]").wait_for(timeout=3000)


def _open_picker(page, card) -> None:
    _open_menu(page, card)
    page.locator(".ctxmenu [role=menuitem]", has_text="Change printing…").click()
    page.locator(".printpick.open[role=dialog]").wait_for(timeout=3000)
    page.locator(".printpick .pptile").first.wait_for(timeout=5000)


def _tiles(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('.printpick .pptile .ppcode')].map(e => e.innerText)"
    )


def _choose(page, label: str, value: str) -> None:
    """A filter's value, set through its themed list as a person picks it."""
    btn = page.locator(f".printpick .msel-btn[aria-label='{label}']")
    if not btn.is_visible():
        page.locator(".printpick .ppfiltbtn").click()
    btn.click()
    page.locator(".msel-list [role=option]", has_text=value).first.click()
    page.wait_for_timeout(150)


# every control of the open dialog: on screen, inside its scroller, and the one under its middle
COVERED = """
() => {
  const dlg = document.querySelector('.printpick.open');
  if (!dlg) return ['no picker is open'];
  const out = [], vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const sel = 'button, input:not([type=hidden]):not([type=checkbox]), .msel-btn, a[href], label.switch';
  for (const el of dlg.querySelectorAll(sel)) {
    if (el.matches('select.msel-native') || el.closest('[hidden]') || el.closest('[inert]')) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) continue;
    let top = Math.max(r.top, 0), bottom = Math.min(r.bottom, vh);
    const sc = el.parentElement.closest('.ppscroll, .pppreview');
    if (sc) {
      const s = sc.getBoundingClientRect();
      top = Math.max(top, s.top);
      bottom = Math.min(bottom, s.bottom);
    }
    if (bottom - top < 4) continue;  // scrolled out of its list: not on screen now
    const name = (el.getAttribute('aria-label') || el.innerText || '').trim().slice(0, 30);
    const label = el.tagName.toLowerCase() + ' "' + name + '"';
    if (r.left < -1 || r.right > vw + 1) out.push(label + ' outside the window');
    const x = r.left + r.width / 2, y = (top + bottom) / 2;
    const hit = document.elementFromPoint(x, y);
    if (!hit || !(hit === el || el.contains(hit)))
      out.push(label + ' covered by ' + (hit ? hit.tagName + '.' + hit.className : 'nothing'));
  }
  return out;
}
"""

# touch screens: every control of the dialog at least 44 px tall (and wide)
SMALL = """
() => {
  const out = [];
  const dlg = document.querySelector('.printpick.open');
  for (const el of dlg.querySelectorAll('button, input[type=search], .msel-btn, label.switch')) {
    if (el.closest('[hidden]') || el.closest('[inert]')) continue;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) continue;
    if (r.height < 43.5 || r.width < 43.5)
      out.push((el.getAttribute('aria-label') || el.innerText).trim().slice(0, 30) + ' '
        + Math.round(r.width) + 'x' + Math.round(r.height));
  }
  return out;
}
"""

TEXT = (
    ".printpick h2, .printpick .ppstatus, .printpick .ppsetname, .printpick .ppcode, .printpick .ppprice,"
    " .printpick .ppchip, .printpick .ppmark, .printpick label.switch, .printpick .msel-btn,"
    " .printpick .ppfiltbtn, .printpick .ppmore .btn, .printpick .pppreview h3, .printpick .ppline,"
    " .printpick .ppprices dt, .printpick .ppprices dd, .printpick .ppnote, .printpick .ppacts button,"
    " .printpick .pphint"
)


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def _shots_both_themes(page, name: str) -> None:
    if SHOTS:
        for theme in ("light", "dark"):
            page.evaluate("t => document.documentElement.setAttribute('data-theme', t)", theme)
            page.wait_for_timeout(100)
            _shot(page, f"{name}-{theme}")
        page.evaluate("() => document.documentElement.setAttribute('data-theme', 'light')")


def test_change_printing_from_the_card_menu_saves_with_undo(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    assert server.sol_ring() == [("cmr", "472", "Normal")]
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        url = page.url
        card = page.locator(".deckview .row[data-card='Sol Ring'][data-zone='main']")
        side = page.locator(".deckview .row[data-card='Sol Ring'][data-zone='side']")
        # the maybeboard row's menu has no printing item (Archidekt's printing change is for the deck)
        _open_menu(page, side)
        labels = page.locator(".ctxmenu [role=menuitem]").all_inner_texts()
        assert not any("Change printing" in x for x in labels), labels
        page.keyboard.press("Escape")
        _open_menu(page, card)
        labels = page.locator(".ctxmenu [role=menuitem]").all_inner_texts()
        assert any("Change printing…" in x for x in labels), labels
        page.locator(".ctxmenu [role=menuitem]", has_text="Change printing…").click()
        dlg = page.locator(".printpick.open[role=dialog]")
        dlg.wait_for(timeout=3000)
        assert page.locator(".ctxmenu").count() == 0
        assert dlg.get_attribute("aria-modal") == "true"
        assert page.locator("#pp-title").inner_text() == "Printings of Sol Ring"
        assert page.evaluate("() => document.activeElement.getAttribute('aria-label')") == "Close"
        page.locator(".printpick .pptile").first.wait_for(timeout=5000)
        # the default list: released paper printings in English, newest first
        codes = _tiles(page)
        assert codes[0].startswith("FRC #21") and "CMR #472 · 2020" in codes, codes
        assert not any(c.startswith(("YMID", "FUT", "STA")) for c in codes), codes
        assert "2 digital-only or preview printings hidden." in page.locator(".ppstatus").inner_text()
        # every tile: set name, code and number, year, finishes, price; the current one is marked
        cur = page.locator(".printpick .pptile.current")
        assert cur.count() == 1 and "CMR #472" in cur.inner_text() and "Current" in cur.inner_text()
        assert (
            "Commander Legends" in cur.inner_text()
            and "Non-foil" in cur.inner_text()
            and "$1.46" in cur.inner_text()
        )
        sld = page.locator(".printpick .pptile", has_text="SLD #1074")
        assert "Secret Lair Drop" in sld.inner_text() and "Etched" in sld.inner_text()
        assert "$4.50" in sld.inner_text()  # the price of the finish the card would be in
        # the wide preview starts on the deck's own printing (the pointer, left where the menu item
        # was, may sit over a tile: move it off the list first)
        page.mouse.move(640, 4)
        assert "Commander Legends" in page.locator(".pppreview h3").inner_text()
        assert page.locator(".pppreview .ppuse").is_disabled()
        # search: set name or code; the card's current printing (CMR #472) always shows (N7), and
        # the count line counts only what matches and says why it is there
        status = page.locator(".ppstatus")
        own = "The card's current printing is shown too."
        page.locator("#pp-q").fill("secret")
        assert sorted(_tiles(page)[:2]) == ["SLD #1074 · 2022", "SLD #1075 · 2022"]
        assert _tiles(page)[2:] == ["CMR #472 · 2020"]
        assert status.inner_text().startswith("2 of 8 printings match. " + own)
        assert page.locator(".printpick .pptile.current", has_text="CMR #472").count() == 1
        page.locator("#pp-q").fill("LEA")
        assert _tiles(page) == ["CMR #472 · 2020", "LEA #270 · 1993"]
        assert status.inner_text().startswith("1 of 8 printings matches. " + own)
        page.locator("#pp-q").fill("CMR")
        assert _tiles(page) == ["CMR #472 · 2020"]
        assert own not in status.inner_text()  # it matches: nothing to explain
        page.locator("#pp-q").fill("")
        # filters (every printing is here already, so they apply at once) and the sorts
        _choose(page, "Finish", "Etched")
        assert sorted(_tiles(page)[:2]) == ["SLD #1074 · 2022", "SLD #1075 · 2022"]
        assert _tiles(page)[2:] == ["CMR #472 · 2020"]
        assert status.inner_text().startswith("2 of 8 printings match. " + own)
        _choose(page, "Finish", "Any finish")
        _choose(page, "Frame", "1993 frame")
        assert _tiles(page) == ["CMR #472 · 2020", "4ED #325 · 1995", "LEA #270 · 1993"]
        _choose(page, "Border", "White border")
        assert _tiles(page) == ["CMR #472 · 2020", "4ED #325 · 1995"]
        assert status.inner_text().startswith("1 of 8 printings matches. " + own)
        _choose(page, "Border", "Borderless")
        assert _tiles(page) == ["CMR #472 · 2020"]
        assert status.inner_text().startswith("0 of 8 printings match. " + own)
        assert page.locator(".ppempty", has_text="No printing matches these filters.").is_visible()
        page.locator(".ppempty button", has_text="Clear filters").click()
        assert len(_tiles(page)) == 8
        _choose(page, "Sort", "Oldest first")
        assert _tiles(page)[0] == "LEA #270 · 1993"
        assert "$3,500.00" in page.locator(".printpick .pptile", has_text="LEA #270").inner_text()
        _choose(page, "Sort", "Cheapest (USD)")
        cheap = _tiles(page)
        assert cheap[:3] == ["4ED #325 · 1995", "FRC #21 · 2026", "CMR #472 · 2020"], cheap
        _choose(page, "Sort", "Newest first")
        # the switch shows the digital-only and preview printings, labelled; never another language
        page.locator(".printpick label.switch").click()
        codes = _tiles(page)
        assert any(c.startswith("YMID") for c in codes) and any(c.startswith("FUT") for c in codes)
        assert not any(c.startswith("STA") for c in codes)
        assert "Digital only" in page.locator(".pptile", has_text="YMID #12").inner_text()
        assert "Preview" in page.locator(".pptile", has_text="FUT #99").inner_text()
        page.locator(".printpick label.switch").click()
        # hover: the whole card in the preview, with what a pick of it would do to the finish
        sld.hover()
        pv = page.locator(".pppreview")
        assert (
            "Secret Lair Drop" in pv.locator("h3").inner_text()
            and "SLD #1074" in pv.locator(".ppline").inner_text()
        )
        assert pv.locator(".ppbig img").count() == 1
        assert (
            pv.locator(".ppnote").inner_text()
            == "Not made in non-foil: the card becomes etched with this printing."
        )
        assert (
            "Etched" in pv.locator(".ppprices").inner_text()
            and "$4.50" in pv.locator(".ppprices").inner_text()
        )
        # a click pins it; Use this printing saves at once (the menu's own endpoint)
        sld.click()
        assert page.evaluate("() => document.activeElement.innerText") == "Use this printing"
        with page.expect_request(EDIT_URL) as req:
            page.locator(".pppreview .ppuse").click()
        assert json.loads(req.value.post_data) == {
            "changes": [
                {
                    "action": "set_printing",
                    "card_name": "Sol Ring",
                    "set_code": "sld",
                    "collector_number": "1074",
                    "finish": "etched",
                }
            ],
            "confirmed": False,
        }
        assert page.locator(".printpick.open").count() == 0
        # shown at once, then Saved: the message says the finish changed (P6)
        assert card.get_attribute("data-set") == "SLD 1074" and card.get_attribute("data-finish") == "Etched"
        toast = page.locator(".deck-toast .msg", has_text="Saved")
        toast.wait_for(timeout=8000)
        assert toast.inner_text() == (
            "Saved. Sol Ring is now SLD #1074, etched: that printing is not made in non-foil. "
            "Snapshot kept under History."
        )
        assert server.sol_ring() == [("sld", "1074", "Etched")]
        # the row's set reads as the toast writes it (N15)
        assert card.locator(".set").first.get_attribute("title") == "SLD #1074"
        assert card.locator(".set").inner_text() == "SLD" and card.locator(".finish").inner_text() == "E"
        assert page.url == url
        # Undo: the old printing and finish
        with page.expect_request(EDIT_URL) as req2:
            page.locator(".deck-toast button", has_text="Undo").click()
        assert json.loads(req2.value.post_data)["changes"] == [
            {
                "action": "set_printing",
                "card_name": "Sol Ring",
                "set_code": "cmr",
                "collector_number": "472",
                "finish": "normal",
            }
        ]
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert server.sol_ring() == [("cmr", "472", "Normal")]
        assert card.get_attribute("data-set") == "CMR 472" and card.get_attribute("data-finish") == "Normal"
        assert card.locator(".finish").count() == 0
        # a printing in the card's own finish: the finish stays, the message names the printing
        _open_picker(page, card)
        page.locator(".printpick .pptile", has_text="SLD #1075").click()
        with page.expect_request(EDIT_URL) as req3:
            page.locator(".pppreview .ppuse").click()
        assert "finish" not in json.loads(req3.value.post_data)["changes"][0]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert (
            page.locator(".deck-toast .msg").inner_text()
            == "Saved. Sol Ring is now SLD #1075. Snapshot kept under History."
        )
        assert server.sol_ring() == [("sld", "1075", "Normal")]
        assert errors == []
        browser.close()


def test_picker_keyboard_escape_and_the_phone_preview(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        card = page.locator(".deckview .row[data-card='Sol Ring'][data-zone='main']")
        _open_picker(page, card)
        # Tab stays inside the dialog, both ways
        inside = "() => !!document.activeElement.closest('.printpick')"
        for _ in range(40):
            page.keyboard.press("Tab")
            assert page.evaluate(inside)
        for _ in range(5):
            page.keyboard.press("Shift+Tab")
            assert page.evaluate(inside)
        # keyboard focus on a tile previews it; Enter pins it and goes to Use this printing
        page.locator(".printpick .pptile", has_text="LEA #270").focus()
        assert "Limited Edition Alpha" in page.locator(".pppreview h3").inner_text()
        page.keyboard.press("Enter")
        assert page.evaluate("() => document.activeElement.innerText") == "Use this printing"
        # Escape closes it, nothing saved, and the focus goes back to the card
        page.keyboard.press("Escape")
        assert page.locator(".printpick.open").count() == 0
        assert page.evaluate("() => document.activeElement.getAttribute('data-card')") == "Sol Ring"
        assert server.sol_ring() == [("cmr", "472", "Normal")]
        # a click on the backdrop closes it too, and Ctrl+Z waits behind it
        _open_picker(page, card)
        page.mouse.click(5, 5)
        assert page.locator(".printpick.open").count() == 0
        browser.close()
        # a phone: a tap shows the preview over the list, Back to printings and Escape return
        browser, page, errors = _page(p, server, sid, 360, touch=True, height=780)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        card = page.locator(".deckview .row[data-card='Sol Ring'][data-zone='main']")
        _open_picker(page, card)
        assert page.locator(".pppreview").is_hidden()
        assert page.locator(".printpick .msel-btn[aria-label='Finish']").is_hidden()  # folded behind Filters
        page.locator(".printpick .ppfiltbtn").tap()
        assert page.locator(".printpick .msel-btn[aria-label='Finish']").is_visible()
        assert page.locator(".printpick .ppfiltbtn").get_attribute("aria-expanded") == "true"
        page.locator(".printpick .ppfiltbtn").tap()
        page.locator(".printpick .pptile", has_text="SLD #1075").tap()
        pv = page.locator(".pppreview")
        assert pv.is_visible() and "Secret Lair Drop" in pv.locator("h3").inner_text()
        assert page.locator(".pppreview .ppback").is_visible()
        assert page.evaluate("() => document.querySelector('.printpick .ppscroll').inert") is True
        page.locator(".pppreview .ppback").tap()
        assert pv.is_hidden()
        page.locator(".printpick .pptile", has_text="SLD #1075").tap()
        page.keyboard.press("Escape")
        assert pv.is_hidden() and page.locator(".printpick.open").count() == 1
        page.keyboard.press("Escape")
        assert page.locator(".printpick.open").count() == 0
        assert errors == []
        browser.close()


def test_load_more_pages_and_filters_go_to_scryfall(tmp_path: Path) -> None:
    """A card with more printings than one of Scryfall's pages (made small here): Load more reads
    the next page; a filter or sort is asked of Scryfall over every printing."""
    from playwright.sync_api import sync_playwright

    s = _server(tmp_path, fillers=19, page_size=10)
    try:
        _chromium()
        sid = s.sign_in_and_link()
        with sync_playwright() as p:
            browser, page, errors = _page(p, s, sid, 1280)
            page.goto(f"{s.base}/decks/42?view=text", wait_until="networkidle")
            _open_picker(page, page.locator(".deckview .row[data-card='Sol Ring'][data-zone='main']"))
            status = page.locator(".ppstatus")
            assert status.inner_text().startswith("Showing 10 of 30 printings."), status.inner_text()
            _shots_both_themes(page, "own-more-pages-1280")
            more = page.locator(".printpick .ppmore button")
            assert more.inner_text() == "Load more printings"
            first = len(_tiles(page))
            more.click()
            page.wait_for_function(
                "() => document.querySelector('.ppstatus').innerText.startsWith('Showing 20 of 30')"
            )
            assert len(_tiles(page)) > first
            more.click()
            page.wait_for_function(
                "() => document.querySelector('.ppstatus').innerText.startsWith('Showing 30 of 30')"
            )
            assert page.locator(".printpick .ppmore").is_hidden()
            pages = [q for q in s.sf.print_searches if " set:" not in q["q"]]
            assert [q.get("page") for q in pages] == [None, "2", "3"]
            # the card's own printing, when page 1 didn't list it, was asked for once by set and
            # number (N7)
            own = [q for q in s.sf.print_searches if " set:" in q["q"]]
            assert [q["q"].split(" ", 1)[1] for q in own] in ([], ["set:cmr cn:472"])
            # a filter now: Scryfall filters all 30 (not only the pages read); the card's own
            # printing (CMR #472, not a 1993 frame) still shows
            _choose(page, "Frame", "1993 frame")
            page.wait_for_function("() => document.querySelectorAll('.printpick .pptile').length === 3")
            assert _tiles(page) == ["CMR #472 · 2020", "4ED #325 · 1995", "LEA #270 · 1993"]
            assert "The card's current printing is shown too." in status.inner_text()
            assert [q for q in s.sf.print_searches if " set:" not in q["q"]][-1]["q"].endswith(" frame:1993")
            # filters Scryfall finds nothing for: only the card's own printing; the box says there
            # is no match, and the count line doesn't say it a second time
            _choose(page, "Border", "Borderless")
            page.wait_for_function("() => document.querySelectorAll('.printpick .pptile').length === 1")
            assert page.locator(".ppempty", has_text="No printing matches these filters.").is_visible()
            assert status.inner_text() == "The card's current printing is shown too.", status.inner_text()
            _shots_both_themes(page, "own-scryfall-none-1280")
            _choose(page, "Border", "Any border")
            _choose(page, "Frame", "Any frame")
            _choose(page, "Sort", "Cheapest (USD)")
            page.wait_for_function(
                "() => document.querySelector('.ppstatus').innerText.startsWith('Showing 10 of 30')"
            )
            last = [q for q in s.sf.print_searches if " set:" not in q["q"]][-1]
            assert last["order"] == "usd" and last["dir"] == "asc"
            assert _tiles(page)[0] == "4ED #325 · 1995"
            # oldest first: the card's own printing (2020) is not on the first page of 10; it is
            # shown after that page's tiles, not counted, and the count line says so (B3)
            _choose(page, "Sort", "Oldest first")
            page.wait_for_function(
                "() => document.querySelector('.ppstatus').innerText.includes('printing is shown too')"
            )
            assert status.inner_text().startswith("Showing 10 of 30 printings."), status.inner_text()
            assert len(_tiles(page)) == 11 and _tiles(page)[-1] == "CMR #472 · 2020", _tiles(page)
            _shots_both_themes(page, "own-more-pages-off-page-1280")
            assert errors == []
            browser.close()
    finally:
        s.stop()


def test_writes_off_keeps_the_printing_change_as_a_proposal(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    s = _server(tmp_path, writes=False)
    try:
        _chromium()
        sid = s.sign_in_and_link()
        with sync_playwright() as p:
            browser, page, errors = _page(p, s, sid, 1280)
            page.goto(f"{s.base}/decks/42?view=text", wait_until="networkidle")
            _open_picker(page, page.locator(".deckview .row[data-card='Sol Ring'][data-zone='main']"))
            page.locator(".printpick .pptile", has_text="SLD #1075").click()
            page.locator(".pppreview .ppuse").click()
            page.wait_for_url(re.compile(r"/proposals/[^/]+$"), timeout=8000)
            assert "SLD 1075" in page.locator("main").inner_text().upper()
            assert s.sol_ring() == [("cmr", "472", "Normal")]  # nothing was written
            assert errors == []
            browser.close()
    finally:
        s.stop()


def test_card_menu_near_the_bottom_keeps_move_to_usable(server: Server) -> None:
    """Seen at 1366x900 in the text view: the menu opened near the window's bottom squeezed its
    "Move to" list to about 2 px. The menu now moves up (and scrolls) to show the opened list."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366, height=900)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        card = page.locator(".deckview .row[data-card='Cultivate']").first
        card.scroll_into_view_if_needed()
        card.evaluate(
            "c => c.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true,"
            " clientX: 300, clientY: 880}))"
        )
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        menu.locator(".more").click()
        sub = menu.locator(".sub")
        box = sub.bounding_box()
        assert box["height"] > 120, box
        mbox = menu.bounding_box()
        assert mbox["y"] >= 0 and mbox["y"] + mbox["height"] <= 900, mbox
        found = page.evaluate(
            """() => {
              const sub = document.querySelector('.ctxmenu .sub'), out = [];
              const s = sub.getBoundingClientRect();
              for (const b of sub.querySelectorAll('button')) {
                const r = b.getBoundingClientRect();
                if (r.bottom < s.top || r.top > s.bottom) continue;
                if (r.height < 30) out.push(b.innerText + ' is ' + r.height + 'px');
                const y = (Math.max(r.top, s.top) + Math.min(r.bottom, s.bottom)) / 2;
                const hit = document.elementFromPoint(r.left + r.width / 2, y);
                if (!hit || !(hit === b || b.contains(hit))) out.push(b.innerText + ' covered');
              }
              return out;
            }"""
        )
        assert found == [], found
        # the list's own focus went to its first category; a click moves the card there
        target = sub.locator("button:not([disabled])").last
        target.scroll_into_view_if_needed()
        to = target.inner_text().strip()
        with page.expect_request(EDIT_URL) as req:
            target.click()
        assert json.loads(req.value.post_data)["changes"][0]["category"] == to
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        # let the save settle (its toast final) before the next step
        page.wait_for_function(
            "() => { const m = document.querySelector('.deck-toast .msg');"
            " return !/Refreshing|Saving/.test(m ? m.innerText : ''); }",
            timeout=8000,
        )
        # and in a window too short for the whole menu, it scrolls rather than shrink an item
        page.set_viewport_size({"width": 1366, "height": 420})
        # the page closes an open menu on a resize, and the resize event can arrive after the
        # viewport call returns: wait for it (the new height, then one frame) before opening the
        # menu, or the late event closes it (review 2 N2; the CI failure on 16fa4cf)
        page.wait_for_function("() => window.innerHeight === 420", timeout=3000)
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => r()))")
        card.evaluate(
            "c => c.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true,"
            " clientX: 300, clientY: 400}))"
        )
        menu.wait_for(timeout=3000)
        menu.locator(".more").click()
        heights = page.evaluate(
            "() => [...document.querySelectorAll('.ctxmenu > [role=menuitem], .ctxmenu > .sub')]"
            ".filter(e => !e.hidden).map(e => e.getBoundingClientRect().height)"
        )
        assert min(heights) >= 30, heights
        mbox = menu.bounding_box()
        assert mbox["y"] >= 0 and mbox["y"] + mbox["height"] <= 420, mbox
        assert page.evaluate(
            "() => { const m = document.querySelector('.ctxmenu'); return m.scrollHeight > m.clientHeight; }"
        )
        assert errors == []
        browser.close()


def test_the_editor_uses_the_shared_picker_and_queues_the_change(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    server.app.state.gateway.scan.names.set_names(sorted({c["name"] for c in server.sf.cards}))
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        assert page.locator(".picker").count() == 0  # the old picker is gone
        row = page.locator(".erow[id='card-Sol Ring']")
        row.locator("details.rowmenu summary").click()
        row.locator(".menu button", has_text="Change printing").click()
        dlg = page.locator(".printpick.open[role=dialog]")
        dlg.wait_for(timeout=3000)
        page.locator(".printpick .pptile").first.wait_for(timeout=5000)
        assert "CMR #472" in page.locator(".printpick .pptile.current").inner_text()
        page.locator(".printpick .pptile", has_text="SLD #1074").click()
        page.locator(".pppreview .ppuse").click()
        # queued, not saved: the row says what changes and the pending list has it
        assert dlg.count() == 0
        note = row.locator(".note").inner_text()
        assert note == "printing → SLD #1074 · finish → etched", note
        assert page.locator(".pendingbox .n").inner_text() == "1"
        assert "→ SLD #1074 etched" in page.locator(".pending").text_content()
        assert server.sol_ring() == [("cmr", "472", "Normal")]
        # the picker opened again marks the pick waiting; picking the deck's own takes it back
        row.locator("details.rowmenu summary").click()
        row.locator(".menu button", has_text="Change printing").click()
        page.locator(".printpick .pptile.picked").wait_for(timeout=5000)
        assert "Picked" in page.locator(".printpick .pptile.picked").inner_text()
        page.locator(".printpick .pptile.current").click()
        page.locator(".pppreview .ppuse").click()
        assert row.locator(".note").inner_text() == ""
        assert page.locator(".pendingbox .n").inner_text() == "0"
        # the add box's printings are the picker's tiles, and All printings opens the picker to add one
        box = page.locator("#addname")
        box.click()
        box.press_sequentially("sol r", delay=30)
        page.locator(".addprints:visible .pptile").first.wait_for(timeout=5000)
        page.locator(".addprints .allprints").click()
        dlg.wait_for(timeout=3000)
        page.locator(".printpick .pptile", has_text="SLD #1075").click()
        use = page.locator(".pppreview .ppuse")
        assert use.inner_text() == "Add this printing"
        use.click()
        assert "Added 1 × Sol Ring (SLD #1075)" in page.locator(".addstatus").inner_text()
        assert errors == []
        browser.close()


SIZES = [(360, "light"), (360, "dark"), (673, "light"), (673, "dark"), (1280, "light"), (1280, "dark")]


@pytest.mark.parametrize(("width", "theme"), SIZES)
def test_picker_fits_reads_and_touches_at_every_size(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    touch = width < 900
    with sync_playwright() as p:
        browser, page, errors = _page(
            p, server, sid, width, theme=theme, touch=touch, height=800 if touch else 900
        )
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Sol Ring'][data-zone='main']")
        _open_menu(page, card)
        _shot(page, f"menu-{width}-{theme}")
        page.locator(".ctxmenu [role=menuitem]", has_text="Change printing…").click()
        page.locator(".printpick .pptile").first.wait_for(timeout=5000)
        page.wait_for_timeout(250)  # the dialog's entrance
        problems = page.evaluate(MEASURE) + page.evaluate(COVERED)
        assert problems == [], problems
        assert page.evaluate(CONTRAST, TEXT) == []
        if touch:
            assert page.evaluate(SMALL) == []
        _shot(page, f"picker-{width}-{theme}")
        if width < 600:  # the filters, unfolded
            page.locator(".printpick .ppfiltbtn").tap()
            assert page.evaluate(MEASURE) + page.evaluate(COVERED) == []
            assert page.evaluate(CONTRAST, TEXT) == []
            assert page.evaluate(SMALL) == []
            _shot(page, f"picker-filters-{width}-{theme}")
            page.locator(".printpick .ppfiltbtn").tap()
        # a search the card's own printing doesn't match: it shows anyway, and the count says why (N7)
        page.locator("#pp-q").fill("secret")
        page.wait_for_timeout(100)
        assert "The card's current printing is shown too." in page.locator(".ppstatus").inner_text()
        assert page.evaluate(MEASURE) + page.evaluate(COVERED) == []
        assert page.evaluate(CONTRAST, TEXT + ", .printpick .ppstatus") == []
        _shot(page, f"own-{width}-{theme}")
        # nothing matches: the card's own printing stays, and "no match" is said once, in the box
        page.locator("#pp-q").fill("zzz")
        page.wait_for_timeout(100)
        assert page.locator(".ppempty", has_text="No printing matches these filters.").is_visible()
        assert page.locator(".ppstatus").inner_text().count("No printing matches") == 0
        assert page.evaluate(MEASURE) + page.evaluate(COVERED) == []
        assert page.evaluate(CONTRAST, TEXT + ", .printpick .ppstatus, .printpick .ppempty p") == []
        _shot(page, f"own-none-{width}-{theme}")
        page.locator("#pp-q").fill("")
        # the preview of a printing that changes the finish
        tile = page.locator(".printpick .pptile", has_text="SLD #1074")
        if touch:
            tile.tap()
        else:
            tile.click()
        page.locator(".pppreview .ppnote").wait_for(timeout=3000)
        page.wait_for_timeout(100)
        problems = page.evaluate(MEASURE) + page.evaluate(COVERED)
        assert problems == [], problems
        assert page.evaluate(CONTRAST, TEXT) == []
        if touch:
            assert page.evaluate(SMALL) == []
        _shot(page, f"preview-{width}-{theme}")
        assert errors == []
        browser.close()


TOAST = ".deck-toast .msg, .deck-toast button"


@pytest.mark.parametrize(("width", "theme"), SIZES)
def test_saving_messages_and_picked_states_read_at_every_size(server: Server, width: int, theme: str) -> None:
    """The deck page's messages for a printing change (saving, saved, saved with the finish
    changed, after Undo, and refused, which puts the card back to the printing Archidekt has),
    the picker on the deck's own printing, and the editor's add strip and a queued pick: nothing
    scrolls sideways or is covered, and text keeps 4.5:1."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    server.app.state.gateway.scan.names.set_names(sorted({c["name"] for c in server.sf.cards}))
    touch = width < 900
    tag = f"{width}-{theme}"

    def press(loc) -> None:
        if touch:
            loc.tap()
        else:
            loc.click()

    def check(name: str, texts: str) -> None:
        page.wait_for_timeout(150)
        _shot(page, f"{name}-{tag}")
        assert page.evaluate(MEASURE) == [], name
        assert page.evaluate(CONTRAST, texts) == [], name
        _shot(page, f"{name}-{tag}")

    def close() -> None:
        # Escape steps back: on a phone it first leaves the whole-card view, then closes
        for _ in range(3):
            if not page.locator(".printpick.open").count():
                return
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        assert page.locator(".printpick.open").count() == 0

    def pick(code: str) -> None:
        _open_picker(page, card)
        press(page.locator(".printpick .pptile", has_text=code))
        press(page.locator(".pppreview .ppuse"))

    with sync_playwright() as p:
        browser, page, errors = _page(
            p, server, sid, width, theme=theme, touch=touch, height=800 if touch else 900
        )
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        card = page.locator(".deckview .c[data-card='Sol Ring'][data-zone='main']")
        # the deck's own printing: In the deck now, and the button greyed out
        _open_picker(page, card)
        press(page.locator(".printpick .pptile.current"))
        use = page.locator(".pppreview .ppuse")
        assert use.inner_text() == "Already in the deck" and use.is_disabled()
        assert "In the deck now" in page.locator(".pppreview").inner_text()
        check("deck-current", TEXT)
        close()
        # saving, held until the screenshot is taken
        held: list = []
        page.route(EDIT_URL, lambda route: held.append(route))
        pick("SLD #1074")
        msg = page.locator(".deck-toast .msg")
        msg.filter(has_text="Changing").wait_for(timeout=5000)
        assert msg.inner_text() == "Changing Sol Ring to SLD #1074…"
        check("toast-saving", TOAST)
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(100)
        held[0].continue_()
        page.unroute(EDIT_URL)
        msg.filter(has_text="Saved").wait_for(timeout=8000)
        assert msg.inner_text() == (
            "Saved. Sol Ring is now SLD #1074, etched: that printing is not made in non-foil. "
            "Snapshot kept under History."
        )
        check("toast-saved-finish", TOAST)
        press(page.locator(".deck-toast button", has_text="Undo"))
        msg.filter(has_text="Undone").wait_for(timeout=8000)
        assert msg.inner_text() == "Undone. Snapshots are under History."
        assert server.sol_ring() == [("cmr", "472", "Normal")]
        check("toast-undo", TOAST)
        pick("SLD #1075")
        msg.filter(has_text="Saved").wait_for(timeout=8000)
        assert msg.inner_text() == "Saved. Sol Ring is now SLD #1075. Snapshot kept under History."
        check("toast-saved", TOAST)
        server.ark.fail_card_writes = True
        pick("SLD #1074")
        err = page.locator(".deck-toast.error .msg")
        err.wait_for(timeout=8000)
        assert err.inner_text().startswith("Printing not saved:"), err.inner_text()
        assert err.inner_text().endswith("Sol Ring is back to SLD #1075."), err.inner_text()
        check("toast-error", TOAST)
        server.ark.fail_card_writes = False
        # the editor: the add strip and the full picker to add (wide screens: phones have no strip),
        # and a queued pick
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        if not touch:
            box = page.locator("#addname")
            box.click()
            box.press_sequentially("sol r", delay=30)
            strip = page.locator(".addprints:visible")
            strip.locator(".pptile").first.wait_for(timeout=5000)
            first = strip.locator(".pptile").first
            assert (
                first.get_attribute("title")
                == "Reality Fracture Commander FRC #21 · 2026: click to add this printing"
            )
            assert first.locator(".ppcode").evaluate("e => e.scrollWidth <= e.clientWidth")  # never cut off
            allp = strip.locator(".allprints")
            assert allp.is_visible() and allp.evaluate(
                "b => { const r = b.getBoundingClientRect(), s = b.parentElement.getBoundingClientRect();"
                " return r.bottom <= s.bottom + 1; }"
            )  # in reach without scrolling the list
            _shot(page, f"editor-strip-{tag}")  # a pop-over: it covers the page below by design
            assert (
                page.evaluate(CONTRAST, ".addprints .ppcode, .addprints .head, .addprints .allprints") == []
            )
            press(page.locator(".addprints .allprints"))
            page.locator(".printpick .pptile").first.wait_for(timeout=5000)
            press(page.locator(".printpick .pptile", has_text="SLD #1074"))
            assert page.locator(".pppreview .ppuse").inner_text() == "Add this printing"
            check("editor-add", TEXT)
            page.keyboard.press("Escape")
            page.locator(".printpick.open").wait_for(state="detached", timeout=3000)
        row = page.locator(".erow[id='card-Sol Ring']")
        for _ in range(2):  # queue a pick, then open the picker again on it
            press(row.locator("details.rowmenu summary"))
            press(row.locator(".menu button", has_text="Change printing"))
            page.locator(".printpick .pptile").first.wait_for(timeout=5000)
            if not page.locator(".printpick .pptile.picked").count():
                press(page.locator(".printpick .pptile", has_text="SLD #1074"))
                press(page.locator(".pppreview .ppuse"))
                page.locator(".printpick.open").wait_for(state="detached", timeout=3000)
        press(page.locator(".printpick .pptile.picked"))
        use = page.locator(".pppreview .ppuse")
        assert use.inner_text() == "Already picked" and use.is_disabled()
        assert "Picked, not saved yet" in page.locator(".pppreview").inner_text()
        check("editor-picked", TEXT)
        assert errors == []
        browser.close()
