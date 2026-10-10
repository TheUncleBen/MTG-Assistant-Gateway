"""The site search in headless Chromium: the top bar's box lists Cards and Decks groups as text
is typed (a deck search for the text, "Decks with commander …" for a legendary creature among
the matches), a card row opens /cards for it, a deck row opens /search, Enter on plain text is
the deck search; the card page's Add to deck dialog adds a card through the deck page's own
edit endpoint. At 360, 720 and 1280 px, light and dark, with long, right-to-left and emoji
names: nothing scrolls sideways, and every control of the open list and the dialog is uncovered
(the topmost element at its centre is the control itself). Skipped without a Chromium build."""

from __future__ import annotations

from pathlib import Path

import pytest

from mtg_gateway.archidekt_csv import parse_export, to_deck_json

from . import fake_archidekt as FA
from .test_editor_browser import _link
from .test_overflow_sweep_browser import MEASURE
from .test_scan_browser import _chromium_path
from .test_suggest_browser import SHELOBS, Server

pytest.importorskip("playwright")

LONG_NAME = "Okiri, Belligerent Bannerkeeper of the Greatest Grand Army"
RTL_NAME = "Solemn Simulacrum שלום עולם"
EMOJI_NAME = "Solar Blaze \U0001f525\U0001f525"
CATALOG = [
    "Aesi, Tyrant of Gyre Strait",
    "Sol Ring",
    "Sol Talisman",
    "Mountain",
    "Mountain Goat",
    LONG_NAME,
    RTL_NAME,
    EMOJI_NAME,
    "Asmoranomardicadaistinaculdacar",
] + SHELOBS
RTL_DECK = "משחק שלי \U0001f409 Dragons of the Multiverse " * 3
UNBROKEN_DECK = "Superlongunbrokendecknamewithoutanyspaceatallwhatsoeverreally"
SIZES = [(360, "light"), (360, "dark"), (720, "light"), (720, "dark"), (1280, "light"), (1280, "dark")]

# Every option of the open suggestion list, and the list itself, inside the window and uncovered.
LIST_CHECK = """
() => {
  const vw = document.documentElement.clientWidth, out = [];
  const list = document.querySelector('.topbar [role=listbox]:not([hidden])');
  if (!list) return ['no list is open'];
  const r = list.getBoundingClientRect();
  if (r.left < -1 || r.right > vw + 1)
    out.push('list outside the window ' + Math.round(r.left) + '..' + Math.round(r.right) + ' of ' + vw);
  for (const o of list.querySelectorAll('[role=option]')) {
    const b = o.getBoundingClientRect(), label = '"' + o.innerText.slice(0, 30) + '"';
    if (!(b.width > 0 && b.height > 0)) { out.push(label + ' has no size'); continue; }
    if (b.left < -1 || b.right > vw + 1) out.push(label + ' outside the window');
    const top = document.elementFromPoint(b.left + b.width / 2, b.top + Math.min(b.height / 2, 20));
    if (!top || !(top === o || o.contains(top)))
      out.push(label + ' is covered by ' + (top ? top.tagName : 'nothing'));
  }
  if (document.documentElement.scrollWidth > vw) out.push('document scrolls sideways with the list open');
  const cs = getComputedStyle(list.querySelector('li.go'));
  return {problems: out, color: cs.color, weight: cs.fontWeight,
    text: getComputedStyle(document.body).getPropertyValue('--text').trim()};
}
"""
# Every control of the Add to deck dialog inside the window and uncovered.
SHEET_CHECK = """
() => {
  const vw = document.documentElement.clientWidth, out = [];
  const sheet = document.querySelector('.addsheet.open');
  if (!sheet) return ['no dialog is open'];
  for (const c of sheet.querySelectorAll('button, input, .msel-btn, select:not(.msel-native)')) {
    const b = c.getBoundingClientRect(), label = c.id || c.innerText || c.className;
    if (!(b.width > 0 && b.height > 0)) { out.push(label + ' has no size'); continue; }
    if (b.left < -1 || b.right > vw + 1)
      out.push(label + ' outside the window ' + Math.round(b.left) + '..' + Math.round(b.right));
    const top = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    if (!top || !(top === c || c.contains(top)))
      out.push(label + ' is covered by ' + (top ? top.tagName + '.' + top.className : 'nothing'));
  }
  if (document.documentElement.scrollWidth > vw) out.push('document scrolls sideways with the dialog open');
  return out;
}
"""


def _hex_to_rgb(value: str) -> str:
    v = value.lstrip("#")
    r, g, b = (int(v[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgb({r}, {g}, {b})"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.sf.catalog = CATALOG + [f"Filler Card {i}" for i in range(1200)]
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    FA.CARD_DB.setdefault("sol talisman", {"id": 9803, "oracleCard": {"name": "Sol Talisman"}})
    cards = parse_export((Path(__file__).parent / "fixtures" / "sample_deck.csv").read_text(encoding="utf-8"))
    s.ark.decks[61] = to_deck_json(cards[:12], deck_id=61, name=RTL_DECK, owner="alice")
    s.ark.decks[62] = to_deck_json(cards[:12], deck_id=62, name=UNBROKEN_DECK, owner="alice")
    s.ark.users["alice"]["decks"] += [61, 62]
    # a plain category on the sample deck for the dialog to pick (Commander would change the commander)
    s.ark.decks[42]["categories"].append({"name": "Ramp", "isPremier": False, "includedInDeck": True})
    object.__setattr__(s.app.state.gateway.settings, "writes_enabled", True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _chromium() -> str | None:
    """The build to launch, found before Playwright is entered (the finder opens its own)."""
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _page(p, server: Server, sid: str, width: int, theme: str, exe: str | None):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": width, "height": 860}, has_touch=width < 900)
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": sid, "url": server.base},
            {"name": "mtg_theme", "value": theme, "url": server.base},
        ]
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


def _open_site_search(page, width: int) -> None:
    if width < 600:
        btn = page.locator(".topbar .searchbtn")
        assert btn.is_visible()
        btn.click()
        assert page.evaluate("document.body.classList.contains('search-open')")
    box = page.locator("#site-q")
    box.wait_for(state="visible", timeout=3000)
    box.click()


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_top_bar_search_lists_cards_and_decks_without_overflow(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        _open_site_search(page, width)
        box = page.locator("#site-q")
        box.press_sequentially("aesi", delay=25)
        listbox = page.locator(".topbar [role=listbox]:visible")
        listbox.wait_for(timeout=3000)
        # the commander row appears once the card details (type line) have come back
        page.locator("[role=option]", has_text="Decks with commander").wait_for(timeout=3000)
        heads = page.locator(".topbar .suggest-list li.head").all_text_contents()  # innerText is uppercased
        assert heads == ["Cards", "Decks"], heads
        options = page.locator(".topbar [role=option]").evaluate_all(
            "els => els.map(e => e.getAttribute('data-name'))"
        )
        assert options[0] == "Aesi, Tyrant of Gyre Strait", options
        assert "Search decks named “aesi”" in options, options
        assert "Decks with commander Aesi, Tyrant of Gyre Strait" in options, options
        got = page.evaluate(LIST_CHECK)
        assert got["problems"] == [], got
        # the list's rows keep the page's text colour and weight, never the bar's white bold
        assert got["color"] == _hex_to_rgb(got["text"]) and got["weight"] == "400", got
        assert page.evaluate(MEASURE) == []
        # Escape closes the list; a second Escape in an emptied box folds the phone's box away
        box.press("Escape")
        assert page.locator(".topbar [role=listbox]:visible").count() == 0
        if width < 600:
            box.fill("")
            box.press("Escape")
            assert not page.evaluate("document.body.classList.contains('search-open')")
        assert errors == [], errors
        browser.close()


def test_rows_open_the_card_page_the_deck_search_and_enter_searches_decks(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        # a card row (click) opens the card page for that card
        page.locator("#site-q").click()
        page.locator("#site-q").press_sequentially("sol t", delay=25)
        page.locator(".topbar [role=option]").first.wait_for(timeout=3000)
        with page.expect_navigation():
            page.locator(".topbar [role=option]", has_text="Sol Talisman").first.click()
        assert page.url.endswith("/cards?q=Sol%20Talisman"), page.url
        assert page.locator("#cardgrid .cc").count() == 1
        # the keyboard reaches the Decks rows: Down to the commander row, Enter opens the deck search
        page.locator("#site-q").click()
        page.locator("#site-q").press_sequentially("aesi", delay=25)
        page.locator("[role=option]", has_text="Decks with commander").wait_for(timeout=3000)
        for _ in range(3):
            page.locator("#site-q").press("ArrowDown")
        active = page.locator("[role=option][aria-selected=true]").inner_text()
        assert active.startswith("Decks with commander"), active
        with page.expect_navigation():
            page.locator("#site-q").press("Enter")
        assert "/search?commander=Aesi%2C%20Tyrant%20of%20Gyre%20Strait" in page.url, page.url
        assert page.locator("#s-cmd").input_value() == "Aesi, Tyrant of Gyre Strait"
        # Enter on plain text (nothing highlighted) is the deck search for that text
        page.locator("#site-q").click()
        page.locator("#site-q").press_sequentially("zz", delay=25)
        page.locator(".topbar [role=listbox]:visible").wait_for(timeout=3000)
        with page.expect_navigation():
            page.locator("#site-q").press("Enter")
        assert page.url.endswith("/search?q=zz"), page.url
        assert page.locator("#s-name").input_value() == "zz"
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize("width,theme", SIZES)
def test_add_to_deck_from_the_card_page_saves_through_the_edit_endpoint(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    before = len(server.ark.patches)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        edits: list[str] = []
        page.on("request", lambda r: edits.append(r.url) if r.method == "POST" else None)
        page.goto(f"{server.base}/cards?q=sol", wait_until="networkidle")
        assert page.evaluate(MEASURE) == []
        # the picture opens the shared viewer with an Add to deck action
        page.locator(".cc .pic").nth(1).click()
        viewer = page.locator(".cardview.open")
        viewer.wait_for(timeout=3000)
        assert viewer.locator("h3").inner_text().startswith("Sol Talisman")
        viewer.locator("button", has_text="Add to deck").click()
        sheet = page.locator(".addsheet.open")
        sheet.wait_for(timeout=3000)
        assert page.locator(".cardview.open").count() == 0
        assert sheet.locator("h3").inner_text() == "Add Sol Talisman"
        # the deck list has been read (the member cache); the long, RTL and emoji names are options
        page.wait_for_function("document.querySelectorAll('#ad-deck option').length >= 3", timeout=5000)
        names = page.locator("#ad-deck option").all_inner_texts()
        assert any(n.startswith("Sample Commander Deck") for n in names), names
        assert any(n.startswith(UNBROKEN_DECK) for n in names), names
        assert any("\U0001f409" in n for n in names), names
        page.evaluate(
            "() => { const s = document.getElementById('ad-deck'); s.value = '42';"
            " s.dispatchEvent(new Event('change', {bubbles: true})); }"
        )
        page.wait_for_function("document.querySelectorAll('#ad-cat option').length >= 2", timeout=5000)
        assert "Ramp" in page.locator("#ad-cat option").all_inner_texts()
        assert page.evaluate(SHEET_CHECK) == []
        page.locator("#ad-qty").fill("2")
        page.evaluate(
            "() => { const s = document.getElementById('ad-cat'); s.value = 'Ramp';"
            " s.dispatchEvent(new Event('change', {bubbles: true})); }"
        )
        sheet.locator("button[type=submit]").click()
        toast = page.locator(".cards-toast")
        toast.wait_for(timeout=8000)
        assert "Added 2 × Sol Talisman to Sample Commander Deck" in toast.inner_text()
        assert toast.locator("a", has_text="Open deck").get_attribute("href") == "/decks/42"
        assert page.locator(".addsheet.open").count() == 0
        assert errors == [], errors
        browser.close()
    # the save went through the deck page's own edit endpoint and reached Archidekt
    assert any(u.endswith("/api/v1/decks/42/edit") for u in edits), edits
    assert len(server.ark.patches) > before
    rows = [c for c in server.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Talisman"]
    assert rows and rows[0]["quantity"] == 2 and rows[0]["categories"] == ["Ramp"], rows


def test_with_writes_off_the_add_is_kept_for_review_and_nothing_is_written(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    object.__setattr__(server.app.state.gateway.settings, "writes_enabled", False)
    sid = _link(server)
    before = len(server.ark.patches)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 720, "light", exe)
        page.goto(f"{server.base}/cards?q=sol", wait_until="networkidle")
        page.locator("button[data-add]").nth(1).click()
        page.locator(".addsheet.open").wait_for(timeout=3000)
        page.wait_for_function("document.querySelectorAll('#ad-deck option').length >= 3", timeout=5000)
        page.evaluate(
            "() => { const s = document.getElementById('ad-deck'); s.value = '42';"
            " s.dispatchEvent(new Event('change', {bubbles: true})); }"
        )
        with page.expect_navigation():
            page.locator(".addsheet.open button[type=submit]").click()
        assert "/proposals/" in page.url, page.url
        assert "Sol Talisman" in page.locator("main").inner_text()
        assert errors == [], errors
        browser.close()
    assert len(server.ark.patches) == before
    pending = [p for p in server.db.list_proposals("user-1", limit=10) if p["state"] == "pending"]
    assert len(pending) == 1 and pending[0]["deck_id"] == "42", pending


def test_a_member_without_a_link_cannot_open_the_dialog(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360, "dark", exe)
        page.goto(f"{server.base}/cards?q=sol", wait_until="networkidle")
        assert page.evaluate(MEASURE) == []
        btn = page.locator("button[data-add]").first
        assert btn.is_disabled()
        assert "Link your Archidekt account" in (btn.get_attribute("title") or "")
        reads: list[str] = []
        page.on("request", lambda r: reads.append(r.url) if "/cards/api/text" in r.url else None)
        page.locator(".cc .pic").first.click()
        page.locator(".cardview.open").wait_for(timeout=3000)
        assert page.locator(".cardview.open button", has_text="Add to deck").is_disabled()
        assert page.locator(".addsheet.open").count() == 0
        # the rules text arrives after the viewer opened and is drawn into it (symbols as glyphs)
        rules = page.locator(".cardview.open .rules")
        rules.wait_for(timeout=5000)
        assert "Add" in rules.inner_text() and rules.locator("svg, .pip").count() >= 2
        assert page.locator(".cardview.open .facts").inner_text().count("Artist") == 1
        assert page.evaluate(MEASURE) == []
        # closing and opening the same card again reads nothing more
        page.keyboard.press("Escape")
        page.wait_for_function("!document.querySelector('.cardview.open')", timeout=3000)
        page.locator(".cc .pic").first.click()
        page.locator(".cardview.open .rules").wait_for(timeout=3000)
        assert len(reads) == 1, reads
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize(
    "path", ["/account", "/proposals", "/history", "/collection", "/search", "/decks/42"]
)
def test_the_top_bar_search_suggests_on_every_page_without_csp_violations(server: Server, path: str) -> None:
    """Each page carries its own Content-Security-Policy; the box must work under all of them
    (it fetches suggestions from the gateway and shows Scryfall card pictures)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "dark", exe)
        blocked: list[str] = []
        page.on(
            "console",
            lambda m: blocked.append(m.text) if "Content Security Policy" in m.text else None,
        )
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        page.locator("#site-q").click()
        page.locator("#site-q").press_sequentially("sol r", delay=25)
        page.locator(".topbar [role=option]", has_text="Sol Ring").first.wait_for(timeout=3000)
        page.wait_for_load_state("networkidle")
        assert blocked == [], blocked
        assert errors == [], errors
        browser.close()
