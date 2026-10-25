"""Speed: the pages feel instant and self-contained (Ben, 21 Oct 2026, "fast, snappy, and
responsive"). Headless Chromium with the CPU slowed 4x (CDP Emulation.setCPUThrottlingRate), the
gateway with its fakes, at phone and desktop widths:

- a menu (the ⋮, a right-click, a press and hold) is on screen within MENU_MS of the input;
- a box (New deck, New folder, a menu's Export and Delete, Add cards, an admin member's box, the
  account's boxes) is on screen within BOX_MS, with the content it already has or a loading line,
  never after a round trip;
- a chip, a filter, the card size, a sort, a view switch or a letter typed into a live search
  shows its response (the new state, or the pending look) within CHIP_MS; a chip that is a link
  to another address shows the navigation bar within CHIP_MS;
- loading the heaviest pages runs no task longer than LONG_TASK_MS (the page's own first layout
  included), so a tap during the load is never swallowed;
- the server answers every main page within SERVER_MS with the fakes (Server-Timing total), and
  static files carry a year-long immutable cache header when asked for by their digest.

The budgets are for a 4x slowdown on the test runner (measured 21-22 Oct 2026, SPEED.md next
to the UI build plan): roughly a mid-range phone. Nothing here is skipped.
"""

from __future__ import annotations

import re
from functools import partial
from pathlib import Path

import httpx
import pytest

from mtg_gateway.archidekt_csv import parse_export, to_deck_json

from . import fake_archidekt as FA
from .test_editor_browser import PNG
from .test_overflow_sweep_browser import CARDS, _seed
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_ui_pass_c_browser import _seed as _parity_seed

pytest.importorskip("playwright")

THROTTLE = 4
MENU_MS = 100
BOX_MS = 150
CHIP_MS = 150
# the deck page's first layout of 100 cards is the longest task anywhere: 183 ms measured on a
# phone profile, so 300 keeps the headroom a busy runner needs without letting a second one in
LONG_TASK_MS = 300
SERVER_MS = 80
HOLD_MS = 450  # static/cardmenu.js: the press and hold that opens a menu

MAIN_PAGES = (
    "/",
    "/decks",
    "/decks/42",
    "/decks/42?view=grid",
    "/decks/42/edit",
    "/collection",
    "/search",
    "/cards?q=sol",
    "/history",
    "/history/reports/r1",
    "/reports",
    "/proposals",
    "/watchlist",
    "/account",
    "/admin",
    "/admin/users",
    "/admin/activity",
    "/forum",
    "/forum/Archidekt/General",
    "/forum/thread/700002",
)
HEAVY_PAGES = (
    "/decks/42",
    "/decks/42?view=grid",
    "/decks/42/edit",
    "/collection",
    "/cards?q=sol",
    "/admin",
    "/history/reports/r1",
    "/forum/thread/700002",
    "/account",
)

# Input to visible response, inside the page's own clock: the first input event after __arm()
# starts it; the first change to the document ends the "mut" leg and the frame after it the
# "paint" leg. Long tasks are collected from the start of the page.
INIT = r"""
(() => {
  const P = window.__perf = { longtasks: [], start: null, mut: null, paint: null, armed: false };
  try {
    new PerformanceObserver(l => l.getEntries().forEach(e => P.longtasks.push(Math.round(e.duration))))
      .observe({ type: 'longtask', buffered: true });
  } catch (e) {}
  window.__arm = () => { P.start = null; P.mut = null; P.paint = null; P.armed = true; };
  const first = () => { if (P.armed && P.start === null) P.start = performance.now(); };
  ['pointerdown', 'mousedown', 'touchstart', 'keydown', 'contextmenu', 'click', 'input', 'change']
    .forEach(t => document.addEventListener(t, first, true));
  const mo = new MutationObserver(() => {
    if (!P.armed || P.start === null || P.mut !== null) return;
    P.mut = performance.now();
    // the frame after the change is the response on screen; it is reported on the console, which
    // reaches the check even while the page is leaving for another address (an evaluate would wait)
    requestAnimationFrame(() => {
      P.paint = performance.now();
      console.log('__perf:' + Math.round(P.paint - P.start));
    });
  });
  const start = () => mo.observe(document.documentElement,
    { childList: true, subtree: true, attributes: true, characterData: true });
  if (document.documentElement) start(); else document.addEventListener('DOMContentLoaded', start);
})();
"""


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True, writes_enabled=True)
    for cid, name in ((9801, CARDS[-2]), (9802, CARDS[-1])):
        FA.CARD_DB.setdefault(name.lower(), {"id": cid, "oracleCard": {"name": name}})
    cards = parse_export((Path(__file__).parent / "fixtures" / "sample_deck.csv").read_text(encoding="utf-8"))
    s.ark.decks[44] = to_deck_json(cards, deck_id=44, name="Second deck", owner="alice")
    s.ark.users["alice"]["decks"].append(44)
    s.ark.folders.setdefault("alice", []).append({"id": 501, "name": "Brews", "private": False})
    s.ark.add_side_row(42, "Sol Ring")
    # parity release 2: a forum thread with replies, and a custom card in deck 42
    _parity_seed(s.ark)
    # a second member, for the admin's row menu (alice's own row has no Disable)
    s.db.upsert_user("user-2", email="bob@example.test", name="Bob", preferred_username="bob", groups=[])
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _exe() -> str:
    exe = _chromium_path()
    assert exe != "missing", "a Chromium build for Playwright is part of this check"
    return exe


class Driver:
    """One throttled page: ``measure(do)`` runs ``do()`` and returns the input-to-paint time."""

    def __init__(self, p, server: Server, sid: str, width: int, exe: str):
        self.server = server
        self.width = width
        self.browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        kw = {"has_touch": True, "is_mobile": True} if width < 600 else {}
        size = {"width": width, "height": 800 if width < 600 else 900}
        # no service worker: a navigation the check holds back (held()) must reach page.route
        self.ctx = self.browser.new_context(viewport=size, service_workers="block", **kw)
        self.ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        self.ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        self.ctx.add_init_script(INIT)
        self.page = self.ctx.new_page()
        self.cdp = self.ctx.new_cdp_session(self.page)
        self.cdp.send("Emulation.setCPUThrottlingRate", {"rate": THROTTLE})
        self.errors: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.perf: list[int] = []
        self.page.on("console", self._console)

    def _console(self, message) -> None:
        if message.text.startswith("__perf:"):
            self.perf.append(int(message.text[7:]))

    def goto(self, path: str) -> None:
        self.page.goto(self.server.base + path, wait_until="load")
        self.page.wait_for_timeout(300)  # the deferred scripts have run and the first frame is up

    def measure(self, do, *, tries: int = 2, reset=None) -> int:
        """The best of ``tries`` runs of ``do`` (input to the frame after the first change on the
        page), ``reset()`` between them. The best run is the one that shows the page's own cost;
        a worse one shows the runner being busy."""
        best = 10**6
        for i in range(tries):
            if i and reset:
                reset()
                # on a phone the close goes back over the box's own history entry; that lands later
                self.page.wait_for_timeout(700)
            self.perf.clear()
            self.page.evaluate("() => window.__arm()")
            do()
            for _ in range(60):  # the response, within 3 s at most
                if self.perf:
                    break
                self.page.wait_for_timeout(50)
            assert self.perf, "nothing on the page changed after the input"
            best = min(best, self.perf[0])
        return best

    def close(self) -> None:
        self.browser.close()

    def held(self, pattern):
        """Holds the answer to the first request matching ``pattern`` (a page the input navigates
        to) until ``release()``, so the measure can be read from the page that asked."""
        return _Held(self.page, pattern)

    # -- the pieces the checks share ----------------------------------------------------------
    def menu(self):
        return self.page.locator(".ctxmenu").first

    def box(self):
        return self.page.locator(".mtgbox-scrim .mtgbox").first

    def escape(self) -> None:
        self.page.keyboard.press("Escape")

    def hold(self, locator) -> None:
        """A finger pressed and held on ``locator`` for longer than the hold, then lifted."""
        b = locator.bounding_box()
        assert b is not None
        x, y = b["x"] + b["width"] / 2, b["y"] + min(b["height"] / 2, 30)
        self.cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        self.page.wait_for_timeout(HOLD_MS + 150)
        self.cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


class _Held:
    def __init__(self, page, pattern):
        self.page, self.pattern, self.routes = page, pattern, []

    def __enter__(self):
        self.page.route(self.pattern, lambda route: self.routes.append(route))
        return self

    def __exit__(self, *exc):
        return False

    def release(self) -> None:
        for _ in range(100):  # until the request has reached the route (5 s at most)
            if self.routes:
                break
            self.page.wait_for_timeout(50)
        assert self.routes, "the input sent no request"
        self.routes[0].continue_()
        self.page.unroute(self.pattern)
        self.page.wait_for_url(self.pattern)
        self.page.wait_for_load_state("load")


def _drivers(p, server: Server, sid: str, exe: str):
    for width in (390, 1366):
        d = Driver(p, server, sid, width, exe)
        try:
            yield d
        finally:
            d.close()


def test_menus_open_within_one_beat(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()  # its own Playwright: before ours opens
    slow: list[tuple] = []
    with sync_playwright() as p:
        for d in _drivers(p, server, sid, exe):
            page = d.page
            # the deck tile's ⋮ and right-click on My decks
            d.goto("/decks")
            tile = page.locator("li.deck[data-deck-id]").first
            kebab = tile.locator("button.kebab").first
            ms = d.measure(partial(kebab.click, no_wait_after=True), reset=d.escape)
            assert d.menu().is_visible()
            slow.append(("decks ⋮", d.width, ms))
            d.escape()
            page.wait_for_timeout(250)
            right = partial(tile.click, button="right", position={"x": 20, "y": 20}, no_wait_after=True)
            ms = d.measure(right, reset=d.escape)
            assert d.menu().is_visible()
            slow.append(("decks right-click", d.width, ms))
            d.escape()
            if d.width < 600:
                # a press and hold: the menu comes within the beat after the hold's own time
                d.goto("/decks")  # a fresh page: the menu just closed is still settling its Back entry
                tile = page.locator("li.deck[data-deck-id]").first
                ms = d.measure(partial(d.hold, tile), tries=1)
                assert d.menu().is_visible()
                slow.append(("decks hold", d.width, ms - HOLD_MS))
                d.escape()
            # the deck page's own card menu (static/deck.js)
            d.goto("/decks/42")
            card = page.locator(".deckview li[data-card]").first
            right = partial(card.click, button="right", position={"x": 10, "y": 10}, no_wait_after=True)
            ms = d.measure(right, reset=d.escape)
            assert d.menu().is_visible()
            slow.append(("deck card right-click", d.width, ms))
            d.escape()
            # a custom card's menu on the deck page (parity release 2)
            d.goto("/decks/42?view=text")
            custom = page.locator(".deckview .row[data-custom-id='41']")
            custom.scroll_into_view_if_needed()
            right = partial(custom.click, button="right", position={"x": 10, "y": 10}, no_wait_after=True)
            ms = d.measure(right, reset=d.escape)
            assert d.menu().is_visible()
            slow.append(("custom card right-click", d.width, ms))
            d.escape()
            # a forum reply's ⋮ (static/social.js through cardmenu.js)
            d.goto("/forum/thread/700002")
            reply_kebab = page.locator("#comments .kebab").first
            reply_kebab.scroll_into_view_if_needed()
            ms = d.measure(partial(reply_kebab.click, no_wait_after=True), reset=d.escape)
            assert d.menu().is_visible()
            slow.append(("forum reply ⋮", d.width, ms))
            d.escape()
            # an admin row's ⋮ (static/admin.js)
            d.goto("/admin/users")
            row_kebab = page.locator(".adm .ui-table .act .kebab").first
            ms = d.measure(partial(row_kebab.click, no_wait_after=True), reset=d.escape)
            assert d.menu().is_visible()
            slow.append(("admin users ⋮", d.width, ms))
            assert d.errors == [], d.errors
    print("menus (ms at 4x):", slow)
    over = [s for s in slow if s[2] > MENU_MS]
    assert not over, f"menus over {MENU_MS} ms at 4x: {over}; all: {slow}"


def test_boxes_open_within_one_beat(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()  # its own Playwright: before ours opens
    slow: list[tuple] = []
    with sync_playwright() as p:
        for d in _drivers(p, server, sid, exe):
            page = d.page
            d.goto("/decks")
            # New deck: a box the page already holds (ui.box), opened from its link
            opener = page.locator("[data-box-open=newdeck]").first
            ms = d.measure(partial(opener.click, no_wait_after=True), reset=d.escape)
            assert d.box().is_visible() and "New deck" in d.box().inner_text()
            slow.append(("New deck", d.width, ms))
            d.escape()
            page.wait_for_timeout(300)
            # New folder: built by a script; the box shows at once with its loading line
            new_folder = page.locator("[data-new-folder]").first
            ms = d.measure(partial(new_folder.click, no_wait_after=True), reset=d.escape)
            assert d.box().is_visible()
            slow.append(("New folder", d.width, ms))
            d.escape()
            page.wait_for_timeout(300)
            # a deck's menu → Export… and Delete… (the box comes before the answer from the gateway)
            for item in ("Export", "Delete"):
                tile = page.locator("li.deck[data-deck-id]").first
                tile.locator("button.kebab").first.click(no_wait_after=True)
                d.menu().wait_for()
                entry = d.menu().locator("[role=menuitem]", has_text=item).first
                ms = d.measure(partial(entry.click, no_wait_after=True), tries=1)
                assert d.box().is_visible(), item
                slow.append((f"menu {item}", d.width, ms))
                d.escape()
                page.wait_for_timeout(300)
            # admin: Disable… from a member's row menu is the member's page with its box drawn
            # open (the page cannot hold a box per member), so what must come at once is the
            # navigation bar; the box itself is checked by test_admin_pages_browser
            d.goto("/admin/users")
            bob = page.locator(".au .ui-table .tr.mem").filter(has_text="bob@example.test")
            bob.locator(".kebab").first.click(no_wait_after=True)
            d.menu().wait_for()
            with d.held(re.compile(r"/admin/users\?")) as held:
                disable = d.menu().locator("[role=menuitem]", has_text="Disable…").first
                ms = d.measure(partial(disable.click, no_wait_after=True), tries=1)
                slow.append(("admin Disable… (navigation bar)", d.width, ms))
            held.release()
            assert d.box().is_visible() and "Disable" in d.box().inner_text()
            d.escape()
            # account: the first box opener on the page
            d.goto("/account/details")
            acct = page.locator("main [data-box-open]").first
            if acct.count():
                ms = d.measure(partial(acct.click, no_wait_after=True), reset=d.escape)
                assert d.box().is_visible()
                slow.append(("account box", d.width, ms))
                d.escape()
            assert d.errors == [], d.errors
    print("boxes (ms at 4x):", slow)
    over = [s for s in slow if s[2] > BOX_MS]
    assert not over, f"boxes over {BOX_MS} ms at 4x: {over}; all: {slow}"


def test_chips_filters_sizes_and_searches_respond_at_once(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()  # its own Playwright: before ours opens
    slow: list[tuple] = []
    with sync_playwright() as p:
        for d in _drivers(p, server, sid, exe):
            page = d.page
            # the card size control on the deck page: the page redraws from the root attribute
            d.goto("/decks/42?view=grid")
            sizes = page.locator("[data-card-size-set]")
            current = page.evaluate("() => document.documentElement.getAttribute('data-card-size')")
            other = sizes.filter(has_not=page.locator(f"[data-card-size-set='{current}']")).first
            if d.width < 600 and not other.is_visible():
                other = page.locator("[data-card-size-set]:visible").first
            back = page.locator(f"[data-card-size-set='{current}']").first
            undo = partial(back.click, no_wait_after=True)
            ms = d.measure(partial(other.click, no_wait_after=True), reset=undo)
            slow.append(("card size", d.width, ms))
            # the deck list's name filter: the list shows the pending look as the letter lands,
            # the request itself waits for the pause in typing (static/decks.js)
            d.goto("/decks")
            q = page.locator("#listform #q")  # the name field sits in the toolbar at every width
            q.click()
            page.wait_for_timeout(200)
            ms = d.measure(partial(page.keyboard.type, "s"), tries=1)
            assert page.locator("#decks-list[aria-busy=true]").count() == 1
            slow.append(("decks search letter", d.width, ms))
            page.wait_for_timeout(800)
            # the card search page: the same pending look (static/cardsearch.js)
            d.goto("/cards?q=sol")
            cq = page.locator("#cs-form input[name=q]").first
            if not cq.is_visible():  # on a phone the field may sit in the Filters sheet
                page.locator(".sp-open").first.click()
                page.wait_for_timeout(300)
            cq.click()
            page.wait_for_timeout(200)
            ms = d.measure(partial(page.keyboard.type, "a"), tries=1)
            assert page.locator(".cs-results[aria-busy=true]").count() == 1
            slow.append(("card search letter", d.width, ms))
            page.wait_for_timeout(800)
            # a chip that is a link to another address shows the navigation bar at once; the
            # answer is held until the measure has been read, so the page is still there to ask
            d.goto("/proposals")
            with d.held(re.compile(r"/proposals\?show=")) as held:
                chip = page.locator(".ui-chips a:not([aria-current])").first
                ms = d.measure(partial(chip.click, no_wait_after=True), tries=1)
                slow.append(("proposals chip (navigation bar)", d.width, ms))
            held.release()
            # the collection's sort: its themed list (static/select.js, the button after the
            # hidden select) opens at once; the pick itself is the page with the new order
            d.goto("/collection?view=list")
            sort_btn = page.locator("#col-sort + button.msel-btn").first
            if sort_btn.is_visible():  # on a phone the sort is folded away with the filters
                ms = d.measure(partial(sort_btn.click, no_wait_after=True), tries=1)
                assert sort_btn.get_attribute("aria-expanded") == "true"
                slow.append(("collection sort list", d.width, ms))
                d.escape()
            assert d.errors == [], d.errors
    print("controls (ms at 4x):", slow)
    over = [s for s in slow if s[2] > CHIP_MS]
    assert not over, f"controls over {CHIP_MS} ms at 4x: {over}; all: {slow}"


def test_no_long_task_over_budget_while_the_heaviest_pages_load(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()
    worst: list[tuple] = []
    with sync_playwright() as p:
        for d in _drivers(p, server, sid, exe):
            for path in HEAVY_PAGES:
                best = 10**6
                for _ in range(2):  # the better of two loads: the page's own cost, not the runner's
                    d.page.goto(d.server.base + path, wait_until="load")
                    d.page.wait_for_timeout(700)
                    tasks = d.page.evaluate("() => window.__perf.longtasks")
                    best = min(best, max(tasks or [0]))
                worst.append((path, d.width, best))
            assert d.errors == [], d.errors
    print("longest task on load (ms at 4x):", worst)
    over = [w for w in worst if w[2] > LONG_TASK_MS]
    assert not over, f"long tasks over {LONG_TASK_MS} ms at 4x: {over}; all: {worst}"


# Each batch of editor rows put in the page, with the frame it came in (a frame counter kept by
# a requestAnimationFrame loop that starts before the page's own scripts).
ROW_BATCHES = r"""
(() => {
  const B = window.__rows = [];
  let frame = 0;
  const tick = () => { frame += 1; requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  new MutationObserver(records => {
    let n = 0;
    records.forEach(r => r.addedNodes.forEach(x => {
      if (x.nodeType === 1 && x.matches('li.erow') && x.closest('.existing')) n += 1;
    }));
    if (n) B.push([frame, n]);
  }).observe(document, { childList: true, subtree: true });
})();
"""


def test_the_editor_builds_its_rows_in_small_slices_after_the_first_frame(server: Server) -> None:
    """The deck editor's first load was the longest task left (up to about 350 ms at 4x on a busy
    runner, 25 Oct 2026): its first frame laid out the page for the top bar's measure, built a
    second slice of 12 rows and then styled the page with 24 rows of selects and menus at once.
    Now a slice is at most 6 rows, and the second one waits until the first frame is on screen."""
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()
    with sync_playwright() as p:
        for d in _drivers(p, server, sid, exe):
            d.ctx.add_init_script(ROW_BATCHES)
            d.page.goto(d.server.base + "/decks/42/edit", wait_until="load")
            d.page.wait_for_selector("#editor[data-rows-ready]", state="attached", timeout=15000)
            batches = d.page.evaluate("() => window.__rows")
            total = d.page.locator(".existing li.erow").count()
            assert total > 60, total  # the 100-card deck, every row built
            assert sum(n for _f, n in batches) == total, batches
            assert max(n for _f, n in batches) <= 6, f"a slice of more than 6 rows: {batches}"
            assert batches[1][0] - batches[0][0] >= 2, f"the second slice came in the first frame: {batches}"
            assert d.errors == [], d.errors


def test_the_server_answers_main_pages_fast_and_static_files_are_kept(server: Server) -> None:
    sid, pending = _seed(server)
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    slow: list[tuple] = []
    for path in MAIN_PAGES + (f"/proposals/{pending}",):
        h.get(path)  # the first read of a deck comes from the fake; the page's own cost is the second
        best = 10**6
        for _ in range(3):
            r = h.get(path)
            assert r.status_code == 200, (path, r.status_code)
            m = re.search(r"total;dur=([\d.]+)", r.headers.get("server-timing", ""))
            assert m, (path, r.headers.get("server-timing"))
            best = min(best, float(m.group(1)))
        slow.append((path, best))
        assert r.headers["cache-control"] == "no-store"  # a member's page is never kept
    print("server time (ms):", slow)
    over = [s for s in slow if s[1] > SERVER_MS]
    assert not over, f"server time over {SERVER_MS} ms: {over}; all: {slow}"
    # the scripts and stylesheets a page names carry a digest; asked for by it they are kept a year
    page = h.get("/decks/42").text
    refs = sorted(set(re.findall(r"(?:src|href)='(/static/[^']+\?v=[0-9a-f]+)'", page)))
    assert len(refs) >= 8, refs
    for ref in refs:
        r = h.get(ref)
        assert r.status_code == 200, ref
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable", (ref, r.headers)
        assert r.headers.get("etag"), ref
        again = h.get(ref, headers={"If-None-Match": r.headers["etag"]})
        assert again.status_code == 304, ref
    # and none of them is loaded in the way of the first paint but the stylesheets
    tags = re.findall(r"<script src='/static/[^>]*>", page)
    assert len(tags) >= 8 and all(" defer" in tag for tag in tags), tags


# Every loading look the pages draw: a busy mark (aria-busy, which theme.py dims or spins) or a
# loading line (menus.js's "Reading from Archidekt…"). Recorded from the moment __watch() is called.
WATCH = r"""
() => {
  const W = window.__looks = [];
  const look = n => n.nodeType === 1 && (
    n.getAttribute('aria-busy') === 'true' || n.classList.contains('loading') ||
    (n.querySelector && n.querySelector("[aria-busy='true'], .loading")));
  new MutationObserver(list => list.forEach(m => {
    if (m.type === 'attributes' && look(m.target)) W.push(m.target.outerHTML.slice(0, 120));
    m.addedNodes.forEach(n => { if (look(n)) W.push(n.outerHTML.slice(0, 120)); });
  })).observe(document.documentElement,
    { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-busy', 'class'] });
}
"""


def test_loading_looks_only_where_the_page_waits_on_the_gateway(server: Server) -> None:
    """Ben, 21 Oct: loading states only where we wait on Archidekt, Forge or similar. A control
    that needs nothing from the gateway (a menu, a box the page holds, the card size, a sort list,
    Undo changes) sends no request and draws no loading look; one that asks the gateway (a deck's
    Delete…, which reads the deck first) shows its loading line while it waits."""
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()  # its own Playwright: before ours opens
    with sync_playwright() as p:
        for d in _drivers(p, server, sid, exe):
            _loading_looks(d)
            assert d.errors == [], d.errors


def _loading_looks(d: Driver) -> None:
    page = d.page
    asked: list[str] = []

    def record(req) -> None:  # noqa: ANN001
        if req.url.startswith(d.server.base) and "/static/" not in req.url:
            asked.append(req.method + " " + req.url[len(d.server.base) :])

    page.on("request", record)

    def local(name: str, do, undo=None, remembers: bool = False) -> None:  # noqa: ANN001
        """``remembers``: the control also saves the choice in the background (the card size is
        kept per member), which is never waited on and so never draws a look."""
        asked.clear()
        page.evaluate(WATCH)
        do()
        page.wait_for_timeout(400)
        looks = page.evaluate("() => window.__looks")
        # the bell's count is the page's own read after it loads (static/bell.js, when the browser
        # is idle, up to 2 s later), not the control's: on a busy runner it can land in this window
        waits = [
            a
            for a in asked
            if not (remembers and a == "POST /api/v1/prefs") and a != "GET /notifications/count"
        ]
        assert waits == [] and looks == [], (name, d.width, asked, looks)
        if undo:
            undo()
            page.wait_for_timeout(400)

    d.goto("/decks")
    tile = page.locator("li.deck[data-deck-id]").first
    local("deck ⋮ menu", tile.locator("button.kebab").first.click, d.escape)
    local("New deck box", page.locator("[data-box-open=newdeck]").first.click, d.escape)
    d.goto("/decks/42?view=text")  # the view is remembered: the grid visit below sets it
    card = page.locator(".deckview li[data-card]").first
    local("deck card menu", partial(card.click, button="right", position={"x": 10, "y": 10}), d.escape)
    d.goto("/decks/42?view=grid")
    current = page.evaluate("() => document.documentElement.getAttribute('data-card-size')")
    other = page.locator(f"[data-card-size-set]:visible:not([data-card-size-set='{current}'])").first
    if other.count():
        back = page.locator(f"[data-card-size-set='{current}']").first
        local("card size", other.click, back.click, remembers=True)  # put back for the next width
    d.goto("/collection?view=list")
    sort_btn = page.locator("#col-sort + button.msel-btn").first
    if sort_btn.is_visible():
        local("collection sort list", sort_btn.click, d.escape)
    d.goto("/decks/42/settings")
    reset = page.locator("#categories button[type=reset]")
    if reset.count():
        local("categories Undo changes", reset.first.click)
    # a control that does ask the gateway shows its loading line while it waits
    d.goto("/decks")
    with d.held(re.compile(r"/delete-info")) as held:
        page.evaluate(WATCH)
        tile = page.locator("li.deck[data-deck-id]").first
        tile.locator("button.kebab").first.click()
        d.menu().wait_for()
        d.menu().locator("[role=menuitem]", has_text="Delete").first.click()
        d.box().wait_for()
        page.wait_for_timeout(200)
        assert held.routes, "Delete… read nothing from the gateway"
        assert d.box().locator(".loading").count() == 1
        assert page.evaluate("() => window.__looks"), "no loading look while it waited"
        held.routes[0].continue_()
        page.unroute(held.pattern)
    d.escape()


def test_category_boxes_and_the_companion_switch_respond_at_once(server: Server) -> None:
    """0.7.42's new controls: a category's Delete box and Delete unused categories' box come within
    BOX_MS (both drawn by the page, before any answer from the gateway), and the card menu's
    Companion switch shows its new state within CHIP_MS (saved in the background)."""
    from playwright.sync_api import sync_playwright

    from .test_deck_categories import _give_categories

    _give_categories(server.ark)
    server.ark.decks[42]["categories"].append({"name": "Spare", "isPremier": False, "includedInDeck": True})
    sid, _pending = _seed(server)
    exe = _exe()  # its own Playwright: before ours opens
    slow: list[tuple] = []
    with sync_playwright() as p:
        for d in _drivers(p, server, sid, exe):
            page = d.page
            d.goto("/decks/42/settings")
            for what, button in (
                ("category Delete", "#categories li[data-name='Ramp'] button.del"),
                ("Delete unused", "#categories button[value=unused]"),
            ):
                opener = page.locator(button).first
                opener.scroll_into_view_if_needed()
                ms = d.measure(partial(opener.click, no_wait_after=True), reset=d.escape)
                assert d.box().is_visible(), what
                slow.append((what, d.width, ms, BOX_MS))
                d.escape()
                page.wait_for_timeout(300)
            d.goto("/decks/42")
            card = page.locator(".deckview li[data-card]").first
            # the best of a press on and a press back off, as measure() takes the best of two
            # elsewhere: one press alone shows the runner being busy as often as the page's cost
            runs = []
            for turn in range(2):
                was = card.get_attribute("data-companion")
                card.click(button="right", position={"x": 10, "y": 10}, no_wait_after=True)
                d.menu().wait_for()
                item = d.menu().locator("[role=menuitemcheckbox]", has_text="Companion").first
                runs.append(d.measure(partial(item.click, no_wait_after=True), tries=1))
                assert card.get_attribute("data-companion") == (None if was else "1"), turn
                page.wait_for_timeout(700)  # the save lands before the next press
            slow.append(("Companion switch", d.width, min(runs), CHIP_MS))
            assert d.errors == [], d.errors
    print("0.7.42 controls (ms at 4x):", slow)
    over = [s for s in slow if s[2] > s[3]]
    assert not over, f"over budget at 4x: {over}; all: {slow}"
