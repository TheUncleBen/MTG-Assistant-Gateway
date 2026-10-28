"""The fix pass after the menus and responsive layout build, in headless Chromium: late deck-list
answers never overwrite newer ones, Load more decks keeps the search on screen, the Delete folder
box stays while its job runs, the phone's Back closes a menu in the app and on a tablet at every
rail width, an unnamed deck reads "Unnamed deck" everywhere and can be deleted from its box, the
backup folder's tile offers no Move or Delete, and the screens match the mockups: the lock in the
folder icon's place, 44 px targets on touch, the phone results bar on one row, readable controls at
200% text, the Filters button beside the name box, the brand whole at 1280 px, My decks' spacing,
the Filters badge's contrast, the drop-down chevron on hover, whole names in the stacks and card
tiles of one height. Skipped without Chromium."""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from .test_cardsearch_browser import CATALOG
from .test_deck_menu_browser import PNG
from .test_editor_browser import _link
from .test_menus_browser import _box, _exe, _items, _menu, _pick
from .test_suggest_browser import Server
from .test_text_zoom_browser import TEXT_200

pytest.importorskip("playwright")

APP_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-F956B) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36 "
    "MTGAssistant/0.7.36"
)
# width, the Android app, a touch screen
CONFIGS = [(344, True, True), (390, True, True), (810, True, True), (1024, False, True), (1280, False, False)]


def _server(tmp_path: Path, **over: object) -> Server:
    """alice's folders (Commander with Precons and Brews inside, Archive, the backup folder),
    decks in them, a deck with no name, 30 public decks so a search pages at 12, and a card
    catalogue for the card search."""
    over.setdefault("writes_enabled", True)
    over.setdefault("watchlists_enabled", True)
    s = Server(tmp_path, **over)
    s.sf.catalog = CATALOG + [f"Filler Card {i}" for i in range(30)]
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    ark = s.ark
    ark.folders["alice"] = [
        {"id": 501, "name": "Commander", "private": False},
        {"id": 502, "name": "Precons", "private": False, "parent": 501},
        {"id": 503, "name": "Brews", "private": True, "parent": 501},
        {"id": 504, "name": "Archive", "private": False},
        {"id": 505, "name": s.app.state.gateway.decks.settings.archidekt_backup_folder, "private": True},
    ]
    for did, name in ((201, "Precon deck"), (202, "Loose deck"), (203, "Urza's workshop"), (204, "")):
        deck = copy.deepcopy(ark.decks[42])
        deck["id"] = did
        deck["name"] = name
        ark.decks[did] = deck
        ark.users["alice"]["decks"].append(did)
    ark.deck_folder.update({42: 503, 201: 502, 202: 501, 203: 501, 204: 501})
    for did in range(100, 130):
        d = copy.deepcopy(ark.decks[42])
        d["id"] = did
        d["name"] = f"Public deck number {did}"
        d["updatedAt"] = f"2026-09-{(did % 28) + 1:02d}T10:00:00Z"
        ark.decks[did] = d
    ark.search_page_size = 12
    s.start()
    return s


@pytest.fixture
def server(tmp_path: Path):
    exe = _exe()  # found before Playwright is entered (the finder opens its own)
    s = _server(tmp_path)
    s.exe = exe
    try:
        yield s
    finally:
        s.stop()


def _open(
    p, server: Server, sid: str, width: int, *, app: bool = False, touch: bool = True, theme: str = "light"
):
    browser = p.chromium.launch(executable_path=server.exe, args=["--no-sandbox"])
    kw: dict = {
        "viewport": {"width": width, "height": 860},
        "has_touch": touch,
        "is_mobile": touch,
        "color_scheme": theme,
    }
    if app:
        kw["user_agent"] = APP_UA
    ctx = browser.new_context(**kw)
    ctx.route("https://cards.scryfall.io/**", lambda r: r.fulfill(content_type="image/png", body=PNG))
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": sid, "url": server.base},
            {"name": "mtg_theme", "value": theme, "url": server.base},
        ]
    )
    if app:
        ctx.add_init_script("window.MtgNative = {openCamera: function () {}};")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


RECT = """(sel) => { const e = document.querySelector(sel); if (!e) return null;
  const b = e.getBoundingClientRect();
  return {l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height}; }"""


# -- A. the code review's findings ------------------------------------------------------------------


def test_a_late_deck_list_answer_never_overwrites_a_newer_one(server: Server) -> None:
    """The member types "ur" and then "urza": the first answer is held back until the second
    has drawn, then let through; the list, the count and the address stay with "urza"."""
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, touch=False)
        held = []

        def gate(route):
            q = parse_qs(urlparse(route.request.url).query).get("q", [""])[0]
            if q == "ur":
                held.append(route)  # answered later, after the newer search has drawn
            else:
                route.continue_()

        page.route("**/api/decks/mine*", gate)
        page.goto(f"{server.base}/decks?folder=501", wait_until="networkidle")
        box = page.locator("#q")
        box.fill("ur")
        page.wait_for_timeout(400)  # past the 250 ms pause: the "ur" request is out and held
        assert len(held) == 1
        box.fill("urza")
        page.wait_for_function(
            "() => document.querySelector('#decks-total').textContent === '1 deck'", timeout=5000
        )
        assert page.locator("ul.decklist .deck .name").all_inner_texts() == ["Urza's workshop"]
        try:
            held[0].continue_()  # the older answer lands late (or was cut off by the page)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(800)
        names = page.locator("ul.decklist .deck .name").all_inner_texts()
        assert names == ["Urza's workshop"], names  # not the wider "ur" matches
        assert page.locator("#decks-total").inner_text() == "1 deck"
        assert parse_qs(urlparse(page.url).query)["q"] == ["urza"]
        assert errors == [], errors
        browser.close()


def test_load_more_decks_keeps_the_search_the_results_are_of(server: Server) -> None:
    """A colour ticked but not sent does not change what Load more asks for."""
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, touch=False)
        page.goto(f"{server.base}/search?format=3", wait_until="networkidle")
        assert page.locator("ul.decklist li.deck").count() == 12
        page.locator(".spanel .tog input[value=U]").check()  # not sent: no search runs
        page.wait_for_timeout(300)
        assert page.locator("ul.decklist li.deck").count() == 12
        page.locator("#s-more [data-more]").click()
        page.wait_for_function(
            "() => document.querySelectorAll('ul.decklist li.deck').length === 24", timeout=5000
        )
        sent = server.ark.search_params[-1]
        assert sent["page"] == "2" and "colors" not in sent, sent
        assert page.locator("#s-count").inner_text() == "35 decks"
        assert errors == [], errors
        browser.close()


def test_the_delete_folder_box_stays_until_its_job_ends(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, touch=False)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        page.locator("li.folder[data-folder-id='501'] button.kebab").click()
        _menu(page)
        _pick(page, "Delete folder…")
        box = _box(page)
        box.locator("#mb-typed").wait_for(timeout=5000)
        box.locator("#mb-typed").fill("Commander")
        held = threading.Event()  # the delete waits until the box has been tried
        server.ark.hold_folder_deletes = held
        box.locator("button", has_text="Delete folder").click()
        box.locator(".progress").wait_for(timeout=5000)
        # Esc and a press on the scrim do nothing while the job runs
        page.keyboard.press("Escape")
        page.mouse.click(5, 5)
        page.wait_for_timeout(200)
        assert page.locator(".mtgbox[role=dialog]:not([hidden])").count() == 1
        assert page.locator(".mtgbox[role=dialog]:not([hidden]) [data-cancel]").count() == 0
        assert server.ark.folder_deletes == []  # the job really was still running
        held.set()
        # then the outcome: My decks with the notice
        page.wait_for_url(lambda u: "ok=folder_deleted" in u, timeout=30000)
        assert "Folder deleted" in page.locator("main .notice.ok").inner_text()
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize("width,app", [(810, True), (1000, False)])
def test_back_closes_a_menu_in_the_app_and_on_a_tablet(server: Server, width: int, app: bool) -> None:
    """Deck tile menus (cardmenu.js) and the deck page's card menu (deck.js) at rail widths."""
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width, app=app, touch=True)
        page.goto(f"{server.base}/", wait_until="networkidle")
        page.goto(f"{server.base}/decks?folder=503", wait_until="networkidle")
        page.locator("li.deck[data-deck-id='42'] button.kebab").click()
        _menu(page)
        page.go_back()
        page.wait_for_timeout(300)
        assert page.locator(".ctxmenu").count() == 0
        assert urlparse(page.url).path == "/decks", page.url  # still on My decks
        # the deck page's own card menu
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        card = page.locator(".deckview .c, .deckview .row").first
        card.click(button="right")
        page.locator(".ctxmenu[role=menu]").wait_for(timeout=3000)
        page.go_back()
        page.wait_for_timeout(300)
        assert page.locator(".ctxmenu").count() == 0
        assert urlparse(page.url).path == "/decks/42", page.url
        assert errors == [], errors
        browser.close()


def test_an_unnamed_deck_reads_unnamed_deck_everywhere_and_can_be_deleted(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, touch=False)
        page.goto(f"{server.base}/decks?folder=501", wait_until="networkidle")
        tile = page.locator("li.deck[data-deck-id='204']")
        assert tile.locator(".name").inner_text() == "Unnamed deck"
        assert tile.locator("button.kebab").get_attribute("aria-label") == "Menu for Unnamed deck"
        tile.locator("button.kebab").click()
        menu = _menu(page)
        assert menu.get_attribute("aria-label") == "Deck: Unnamed deck"
        assert menu.locator(".head").inner_text() == "Unnamed deck"
        _pick(page, "Delete deck…")
        box = _box(page)
        box.locator("#mb-typed").wait_for(timeout=5000)
        assert box.locator("h2").inner_text() == "Delete “Unnamed deck”?"
        ok = box.locator("button", has_text="Delete this deck")
        assert ok.is_disabled()
        box.locator("#mb-typed").fill("unnamed DECK")
        assert ok.is_enabled()
        ok.click()
        page.wait_for_url(lambda u: "ok=deleted" in u, timeout=10000)
        assert 204 not in server.ark.decks
        assert errors == [], errors
        browser.close()


OWNER_ONLY = ["Edit deck", "Rename…", "Move to folder…", "Deck settings", "Delete deck…"]


def test_the_deck_menu_on_another_members_deck_has_no_owner_items(server: Server) -> None:
    """A deck search result (someone else's deck) opens a menu without Edit deck, Rename, Move to
    folder, Deck settings and Delete deck; the member's own deck on My decks has them all."""
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, touch=False)
        page.goto(f"{server.base}/search?format=3", wait_until="networkidle")
        tile = page.locator("li.deck[data-deck-id='100']")
        assert tile.get_attribute("data-own") == "0"
        tile.locator("button.kebab").click()
        _menu(page)
        items = _items(page)
        assert not [i for i in OWNER_ONLY if i in items], items
        assert "Clone into my decks" in items and "Bookmark" in items and "Open on Archidekt" in items, items
        page.keyboard.press("Escape")
        page.goto(f"{server.base}/decks?folder=501", wait_until="networkidle")
        page.locator("li.deck[data-deck-id='202'] button.kebab").click()
        _menu(page)
        items = _items(page)
        assert all(i in items for i in OWNER_ONLY), items
        assert "Clone into my decks" not in items and "Bookmark" not in items, items
        assert errors == [], errors
        browser.close()


def test_the_backup_folders_tile_offers_no_move_or_delete(server: Server) -> None:
    """The tile never shows (visible_folders hides it); were it on the page, its menu has no Move
    to folder or Delete folder, and the Move and Inside lists leave the folder out."""
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, touch=False)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        assert page.locator("li.folder[data-folder-id='505']").count() == 0
        # a tile with the backup mark (folderview.py adds it when the folder is a backup one)
        page.evaluate(
            """() => { const t = document.querySelector("li.folder[data-folder-id='504']");
                       t.setAttribute('data-backup', ''); }"""
        )
        page.locator("li.folder[data-folder-id='504'] button.kebab").click()
        _menu(page)
        items = _items(page)
        assert "Move to folder…" not in items and "Delete folder…" not in items, items
        assert "Rename…" in items and "Open" in items
        page.keyboard.press("Escape")
        page.locator("li.folder[data-folder-id='501'] button.kebab").click()
        _menu(page)
        _pick(page, "Move to folder…")
        box = _box(page)
        box.locator(".tree").wait_for(timeout=5000)
        names = box.locator(".tree label").all_inner_texts()
        backup = server.app.state.gateway.decks.settings.archidekt_backup_folder
        assert not any(backup in n for n in names), names
        assert errors == [], errors
        browser.close()


# -- B. the screens ---------------------------------------------------------------------------------

LAYOUT = """({touch, rail}) => {
  const q = s => document.querySelector(s), qa = s => [...document.querySelectorAll(s)];
  const R = e => { const b = e.getBoundingClientRect();
    return {l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height}; };
  const hit = (a, b) => a.l < b.r - 1 && b.l < a.r - 1 && a.t < b.b - 1 && b.t < a.b - 1;
  const out = {sideways: document.documentElement.scrollWidth - document.documentElement.clientWidth};
  const brews = q("li.folder[data-folder-id='503']");
  if (brews) {
    const lock = brews.querySelector('.lock'), kebab = brews.querySelector('button.kebab');
    out.lockInIcon = !!(lock && lock.classList.contains('ic'));
    out.lockOverKebab = !!(lock && kebab && hit(R(lock), R(kebab)));
    out.plainFolderIcons = brews.querySelectorAll('.ic').length;
  }
  const site = q('.topbar #site-q');
  if (site && site.getClientRects().length) out.siteSearchH = R(site).h;
  const head = q('.ui-head.phead'), panel = q('.spanel'), crumbs = q('nav.crumbs');
  const areas = qa('section.fv-area');
  if (head && panel) out.headToPanel = R(panel).t - R(head).b;
  if (panel && crumbs) out.panelToCrumbs = R(crumbs).t - R(panel).b;
  if (crumbs && areas.length) out.crumbsToArea = R(areas[0].querySelector('h2')).t - R(crumbs).b;
  if (areas.length) out.areaBorders = areas.map(a => parseFloat(getComputedStyle(a).borderTopWidth));
  if (panel) {
    const grid = panel.querySelector('.fgrid'), rbar = panel.querySelector('.rbar');
    if (grid && rbar && grid.getClientRects().length && getComputedStyle(grid).display !== 'none')
      out.gridToBar = R(rbar).t - R(grid).b;
  }
  const badge = q('.spanel .fbtn .n');
  if (badge && badge.getClientRects().length) {
    const cs = getComputedStyle(badge);
    out.badge = {fg: cs.color, bg: cs.backgroundColor};
  }
  return out;
}"""


def _contrast(fg: str, bg: str) -> float:
    def lum(c: str) -> float:
        parts = [float(x) for x in c.replace("rgba(", "").replace("rgb(", "").rstrip(")").split(",")[:3]]
        vals = []
        for v in parts:
            v /= 255
            vals.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * vals[0] + 0.7152 * vals[1] + 0.0722 * vals[2]

    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.mark.parametrize("width,app,touch", CONFIGS)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_my_decks_matches_the_mockup(server: Server, width: int, app: bool, touch: bool, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width, app=app, touch=touch, theme=theme)
        page.goto(f"{server.base}/decks?folder=501", wait_until="networkidle")
        got = page.evaluate(LAYOUT, {"touch": touch, "rail": 600 <= width < 1280})
        assert got["sideways"] <= 0, got
        # 1: the lock replaces the folder icon; nothing over the ⋮
        assert got["lockInIcon"] and not got["lockOverKebab"] and got["plainFolderIcons"] == 1, got
        # 2: the top bar's search box is a 44 px target on the rail
        if touch and 600 <= width < 1280:
            assert got["siteSearchH"] >= 44, got
        # 10: the spacing: 8 px inside a group, 12 to 16 between; no bordered areas
        assert 8 <= got["headToPanel"] <= 16, got
        assert 8 <= got["panelToCrumbs"] <= 16, got
        assert 4 <= got["crumbsToArea"] <= 16, got
        assert all(b == 0 for b in got["areaBorders"]), got
        if width >= 600:
            assert 10 <= got["gridToBar"] <= 18, got
        # 11: the Filters count badge (a phone) reads at 4.5:1
        if width < 600:
            page.goto(f"{server.base}/decks?folder=501&c=U", wait_until="networkidle")
            got = page.evaluate(LAYOUT, {"touch": touch, "rail": False})
            assert got.get("badge") and _contrast(got["badge"]["fg"], got["badge"]["bg"]) >= 4.5, got
        assert errors == [], errors
        browser.close()


DECK_TARGETS = """() => {
  const R = e => e.getBoundingClientRect();
  const shown = e => e.getClientRects().length > 0;
  const stat = [...document.querySelectorAll('.statctl button')].filter(shown).map(b => R(b).height);
  const size = b => ({w: R(b).width, h: R(b).height});
  const axis = document.querySelector('.curveaxis');
  const mv = [...document.querySelectorAll('.curveaxis .mv')].filter(shown).map(size);
  const gap = axis ? parseFloat(getComputedStyle(axis).columnGap) || 0 : 0;
  const room = axis ? (R(axis).width - gap * (mv.length - 1)) / mv.length : 0;
  const de = document.documentElement;
  return {stat, mv, room, sideways: de.scrollWidth - de.clientWidth};
}"""


@pytest.mark.parametrize("width,app,touch", [c for c in CONFIGS if c[2]])
def test_deck_page_targets_are_44_px_on_touch(server: Server, width: int, app: bool, touch: bool) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width, app=app, touch=touch)
        page.goto(f"{server.base}/decks/42?view=stacks", wait_until="networkidle")
        got = page.evaluate(DECK_TARGETS)
        assert got["sideways"] <= 0, got
        assert got["stat"] and all(h >= 44 for h in got["stat"]), got
        # the axis buttons: 44 px high, and 44 px wide or as wide as the box lets eight be
        assert got["mv"] and all(m["w"] >= min(44, got["room"]) - 1 and m["h"] >= 44 for m in got["mv"]), got
        assert errors == [], errors
        browser.close()


PHONE_BAR = """() => {
  const R = e => e.getBoundingClientRect();
  const bar = document.querySelector('.spanel .rbar');
  const cnt = bar.querySelector('.cnt'), sort = bar.querySelector('.g.sort');
  const view = bar.querySelector('.view');
  const name = document.querySelector('.spanel .sp-name .search');
  const fbtn = document.querySelector('.spanel .fbtn');
  const mid = e => (R(e).top + R(e).bottom) / 2;
  return {cnt: mid(cnt), sort: mid(sort), view: mid(view), nameB: R(name).bottom, fbtnT: R(fbtn).top};
}"""


@pytest.mark.parametrize("width", [344, 390])
@pytest.mark.parametrize("path", ["/cards?q=s", "/search?format=3", "/decks?folder=501"])
def test_the_phone_results_bar_and_name_row(server: Server, width: int, path: str) -> None:
    """4: the count, Sort and the view share one row (deck search's longer count at 344 is on its
    own line, as the mockup draws it); 6: Filters sits beside the name box."""
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width, app=True, touch=True)
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        got = page.evaluate(PHONE_BAR)
        assert abs(got["sort"] - got["view"]) < 2, got  # the same row: their middles meet
        if not (width == 344 and path.startswith("/search")):
            assert abs(got["cnt"] - got["sort"]) < 2, got
        assert got["fbtnT"] < got["nameB"], got  # the Filters button beside the name box
        assert errors == [], errors
        browser.close()


ZOOM_1280 = """() => {
  const R = e => e.getBoundingClientRect();
  const em = e => parseFloat(getComputedStyle(e).fontSize);
  const mv = [...document.querySelectorAll('.spanel .range input')].map(i => R(i).width / em(i));
  const fits = e => e ? e.scrollWidth <= e.clientWidth + 1 : null;
  const legal = document.querySelector('.spanel .fmtrow .row2 .msel-btn .msel-txt');
  const colour = document.querySelector('.spanel .colors .msel-btn .msel-txt');
  const de = document.documentElement;
  return {mv, legal: fits(legal), colour: fits(colour), sideways: de.scrollWidth - de.clientWidth};
}"""


def test_card_search_controls_read_at_200_percent_text_on_a_desktop(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, touch=False)
        page.goto(f"{server.base}/cards?q=s", wait_until="networkidle")
        page.evaluate(TEXT_200)
        page.wait_for_timeout(200)
        got = page.evaluate(ZOOM_1280)
        assert got["sideways"] <= 0, got
        assert len(got["mv"]) == 2 and all(w >= 4 for w in got["mv"]), got  # room for "From" and "To"
        assert got["legal"] is not False and got["colour"] is not False, got
        assert errors == [], errors
        browser.close()


ZOOM_390 = """() => {
  const R = e => e.getBoundingClientRect();
  const lines = a => { const r = document.createRange(); r.selectNodeContents(a);
    const t = [...a.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim());
    if (!t.length) return 1; r.selectNodeContents(t[t.length - 1]); return r.getClientRects().length; };
  const tabs = [...document.querySelectorAll('.tabbar > a')].filter(a => a.getClientRects().length)
    .map(a => ({t: a.textContent.trim(), lines: lines(a)}));
  const sort = document.querySelector('.spanel .rbar .sortcw .msel-btn .msel-txt')
    || document.querySelector('.spanel .rbar .sortcw select');
  const sortFits = sort ? sort.scrollWidth <= sort.clientWidth + 1 : null;
  const set = document.querySelector('#set-lock');
  return {tabs, sortFits, setW: set ? R(set).width / parseFloat(getComputedStyle(set).fontSize) : null,
    sideways: document.documentElement.scrollWidth - document.documentElement.clientWidth};
}"""


def test_phone_text_at_200_percent_keeps_tab_labels_whole_and_controls_readable(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    server.db.set_pref("user-1", "scan.intro", True)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 390, app=True, touch=True)
        page.goto(f"{server.base}/decks?folder=501", wait_until="networkidle")
        page.evaluate(TEXT_200)
        page.wait_for_timeout(200)
        got = page.evaluate(ZOOM_390)
        assert got["sideways"] <= 0, got
        # 7: a one-word label never breaks mid-word; the sort drop-down shows its words
        assert all(t["lines"] == 1 for t in got["tabs"] if " " not in t["t"]), got
        assert got["sortFits"] is not False, got
        # 8: the Scan page's Set box can be typed in
        page.goto(f"{server.base}/scan", wait_until="networkidle")
        page.evaluate(TEXT_200)
        page.wait_for_timeout(200)
        got = page.evaluate(ZOOM_390)
        assert got["sideways"] <= 0, got
        assert got["setW"] is not None and got["setW"] >= 3, got
        assert errors == [], errors
        browser.close()


BRAND = """() => { const w = document.querySelector('.topbar .brand .word');
  const links = [...document.querySelectorAll('.topbar nav.site > a')].filter(a => a.getClientRects().length);
  return {text: w.textContent, cut: w.scrollWidth > w.clientWidth + 1, links: links.length}; }"""


@pytest.mark.parametrize("path", ["/decks", "/search?format=3", "/cards?q=s", "/decks/42", "/scan"])
def test_the_brand_is_whole_at_1280_px_with_every_link(server: Server, path: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, touch=False)
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        got = page.evaluate(BRAND)
        assert got["text"] == "MTG Assistant Gateway" and not got["cut"], got
        # the four places, none dropped (navigation round 2: the rest under More)
        assert got["links"] == 4, got
        assert errors == [], errors
        browser.close()


def test_a_drop_down_keeps_its_chevron_while_hovered(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, touch=False)
        page.goto(f"{server.base}/cards?q=s", wait_until="networkidle")
        btn = page.locator(".spanel .colors .msel-btn").first
        btn.hover()
        page.wait_for_timeout(300)
        image = btn.evaluate("b => getComputedStyle(b).backgroundImage")
        assert "chevron" in image, image
        assert errors == [], errors
        browser.close()


STACKS = """() => {
  const R = e => e.getBoundingClientRect();
  const out = [];
  document.querySelectorAll('.deckview.stacks .cards').forEach(pile => {
    const cards = [...pile.querySelectorAll('.c')];
    cards.forEach((c, i) => {
      const nm = c.querySelector('.ph .nm'); const next = cards[i + 1];
      if (nm && next) out.push({name: nm.textContent, nmB: R(nm).bottom, nextT: R(next).top,
        lines: Math.round(R(nm).height / parseFloat(getComputedStyle(nm).lineHeight))});
    });
  });
  return out;
}"""


def test_stack_cards_without_an_image_show_their_whole_name(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, 1280, touch=False)
        page.goto(f"{server.base}/decks/42?view=stacks", wait_until="networkidle")
        got = page.evaluate(STACKS)
        assert got, "no stacked placeholder cards"
        covered = [g for g in got if g["nmB"] > g["nextT"] + 1]
        assert covered == [], covered
        assert errors == [], errors
        browser.close()


TILES = """() => {
  const R = e => e.getBoundingClientRect();
  const rows = {};
  document.querySelectorAll('ul.cardgrid li.cc').forEach(li => {
    const top = Math.round(R(li).top), nm = li.querySelector('.nm > span:first-child');
    const k = li.querySelector('.nm .kebab'), ty = li.querySelector('.ty');
    (rows[top] = rows[top] || []).push({name: nm.textContent, nmT: R(nm).top, kT: k ? R(k).top : null,
      kB: k ? R(k).bottom : null, tyT: R(ty).top, mana: !!li.querySelector('.nm .mana')});
  });
  return Object.values(rows);
}"""


@pytest.mark.parametrize("width,app,touch", CONFIGS)
def test_card_tiles_keep_the_kebab_beside_the_name(
    server: Server, width: int, app: bool, touch: bool
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width, app=app, touch=touch)
        page.goto(f"{server.base}/cards?q=s", wait_until="networkidle")
        page.locator("ul.cardgrid li.cc .kebab").first.wait_for(timeout=5000)
        rows = page.evaluate(TILES)
        assert rows and any(t["mana"] for r in rows for t in r), rows
        for row in rows:
            for t in row:
                assert t["kT"] is not None and t["kT"] < t["nmT"] + 24, t  # the ⋮ on the name's first line
            assert max(t["tyT"] for t in row) - min(t["tyT"] for t in row) < 2, row  # type lines level
        assert errors == [], errors
        browser.close()
