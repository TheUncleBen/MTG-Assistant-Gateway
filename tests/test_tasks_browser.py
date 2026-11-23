"""Real tasks, end to end, the way a member does them, on a phone, an open Fold and a laptop.

The click sweep (tests/ui_clicks.py) proves each button does something; it cannot tell whether it
did the right thing. A button that adds a card twice, saves to the wrong category, or changes the
page but not Archidekt passes it. Each test here does one whole task and then checks the RESULT in
three places:

1. the store the task writes to (the Archidekt fake, the database): exactly the change asked for,
   and nothing else changed (the whole deck is compared before and after, so a card added twice,
   a second card touched, or a category lost fails);
2. the page itself after a reload (the change is really saved, not only drawn);
3. another surface that shows the same thing (the deck's total, the bell's count, the deck list),
   so a change that reaches one surface and not the other fails.

No live Archidekt: the fake answers as Archidekt does. Skipped without Chromium."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from .test_editor_browser import PNG, _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .ui_matrix import DISPLAYS

pytest.importorskip("playwright")

# the three screens every task runs on: a phone in the app, an open Fold beside the rail, a laptop
SCREENS = [d for d in DISPLAYS if d.key in ("app", "fold-673", "laptop")]
IDS = [d.key for d in SCREENS]


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True)
    s.sf.catalog = s.sf.names_catalog()
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    s.ark.decks[42]["deckFormat"] = 3
    s.start()
    try:
        yield s
    finally:
        s.stop()


_EXE: list[str] = []


def _exe() -> str:
    if not _EXE:
        _EXE.append(_chromium_path())
    if _EXE[0] == "missing":
        pytest.skip("no Chromium available for Playwright")
    return _EXE[0]


def deck_rows(server: Server, deck: int = 42) -> Counter:
    """The deck as Archidekt holds it: (card, categories) -> copies, for every row."""
    out: Counter = Counter()
    for c in server.ark.decks[deck]["cards"]:
        if c.get("quantity", 0) > 0:
            out[(c["card"]["oracleCard"]["name"], tuple(c.get("categories") or ()))] += c["quantity"]
    return out


def delta(before: Counter, after: Counter) -> dict:
    """What changed: (card, categories) -> +n / -n."""
    keys = set(before) | set(after)
    return {k: after[k] - before[k] for k in sorted(keys) if after[k] != before[k]}


class Screen:
    def __init__(self, p, server: Server, sid: str, display) -> None:  # noqa: ANN001
        self.browser = p.chromium.launch(executable_path=_exe(), args=["--no-sandbox"])
        ctx = self.browser.new_context(**display.context())
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        self.page = ctx.new_page()
        self.errors: list[str] = []
        self.failed: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on(
            "response",
            lambda r: (
                self.failed.append(f"{r.status} {r.url}")
                if r.status >= 500 and r.url.startswith(server.base)
                else None
            ),
        )
        self.width = display.width

    def done(self) -> None:
        """No script error, no server error, nothing wider than the screen, on the last page."""
        over = self.page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert self.errors == [], self.errors
        assert self.failed == [], self.failed
        assert over <= 0, f"the page scrolls sideways by {over}px"
        self.browser.close()


def _toast(page, text: str) -> None:  # noqa: ANN001
    page.locator(".toast, .deck-toast .msg, [role=status]", has_text=text).first.wait_for(timeout=8000)


def _card_menu(page, name: str):  # noqa: ANN001, ANN202
    """The card's menu, opened the way the screen does it: a right-click with a mouse; on a touch
    screen, the same contextmenu a press and hold sends."""
    card = page.locator(f".deckview [data-card='{name}']").first
    card.scroll_into_view_if_needed()
    card.click(button="right")
    menu = page.locator(".ctxmenu")
    menu.wait_for(timeout=4000)
    return menu


@pytest.mark.parametrize("display", SCREENS, ids=IDS)
def test_add_a_card_to_a_category(server: Server, display) -> None:  # noqa: ANN001
    """Add a card: type its name, pick two copies and a category, Add to deck. Archidekt has
    exactly two more Swamps in Enchantment and nothing else changed; the page after a reload shows
    them there; the deck's total went up by two."""
    from playwright.sync_api import sync_playwright

    _exe()  # found before the browser starts
    sid = _link(server)
    before = deck_rows(server)
    with sync_playwright() as p:
        s = Screen(p, server, sid, display)
        page = s.page
        total = _deck_total(page, server.base)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        box = page.locator("#quick")
        box.click()
        box.press_sequentially("swam", delay=20)
        page.locator("[role=option]").first.wait_for(timeout=4000)
        box.press("Enter")
        preview = page.locator(".mtgbox.addbox-preview")
        preview.wait_for(timeout=5000)
        page.locator("#ap-cat").select_option("Enchantment")
        preview.locator("[aria-label^='One more']").click()
        preview.locator("button", has_text="Add to deck").click()
        _toast(page, "Added 2 × Swamp")
        # 1. the store: exactly two Swamps under Enchantment, nothing else
        assert delta(before, deck_rows(server)) == {("Swamp", ("Enchantment",)): 2}
        # 2. the page after a reload
        page.reload(wait_until="networkidle")
        assert page.locator(".stack[data-group='Enchantment'] [data-card='Swamp']").count() >= 1
        # 3. the deck list's total for the deck went up by two
        assert _deck_total(page, server.base) == total + 2
        s.done()


@pytest.mark.parametrize("display", SCREENS, ids=IDS)
def test_change_copies_then_remove_a_card(server: Server, display) -> None:  # noqa: ANN001
    """A card's menu: one more copy, then Remove from deck. Archidekt ends with the card gone and
    nothing else changed; it stays gone after a reload; the total went down by its copies."""
    from playwright.sync_api import sync_playwright

    _exe()  # found before the browser starts
    sid = _link(server)
    name = "Cultivate"
    before = deck_rows(server)
    held = sum(n for (c, _cats), n in before.items() if c == name)
    assert held >= 1
    with sync_playwright() as p:
        s = Screen(p, server, sid, display)
        page = s.page
        total = _deck_total(page, server.base)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        menu = _card_menu(page, name)
        menu.locator(".qtyrow .step").nth(1).click()
        _toast(page, "Saved")
        assert sum(n for (c, _), n in deck_rows(server).items() if c == name) == held + 1
        page.keyboard.press("Escape")
        menu = _card_menu(page, name)
        menu.get_by_role("menuitem", name=re.compile("^Remove from deck")).click()
        page.wait_for_function(
            f"() => !document.querySelector(\".deckview [data-card='{name}']\")", timeout=5000
        )
        page.wait_for_function(
            "() => !/Removing/.test((document.querySelector('.deck-toast .msg') || {}).textContent || '')",
            timeout=8000,
        )
        d = delta(before, deck_rows(server))
        assert d == {k: -n for k, n in before.items() if k[0] == name}, d
        page.reload(wait_until="networkidle")
        assert page.locator(f".deckview [data-card='{name}']").count() == 0
        assert _deck_total(page, server.base) == total - held
        s.done()


@pytest.mark.parametrize("display", SCREENS, ids=IDS)
def test_move_a_card_to_another_category(server: Server, display) -> None:  # noqa: ANN001
    """Move to: the card leaves its category for the one picked, with all its copies; nothing
    else changes; the page after a reload shows it under the new category only."""
    from playwright.sync_api import sync_playwright

    _exe()  # found before the browser starts
    sid = _link(server)
    name = "Cultivate"
    before = deck_rows(server)
    old = [k for k in before if k[0] == name]
    copies = sum(before[k] for k in old)
    with sync_playwright() as p:
        s = Screen(p, server, sid, display)
        page = s.page
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        menu = _card_menu(page, name)
        menu.locator(".more").click()
        menu.locator(".sub button", has_text="Land").first.click()
        _toast(page, "Saved")
        d = delta(before, deck_rows(server))
        want = {k: -before[k] for k in old}
        want[(name, ("Land",))] = want.get((name, ("Land",)), 0) + copies
        assert d == {k: v for k, v in want.items() if v}, d
        page.reload(wait_until="networkidle")
        assert page.locator(f".stack[data-group='Land'] [data-card='{name}']").count() >= 1
        others = page.locator(f".stack:not([data-group='Land']) [data-card='{name}']")
        assert others.count() == 0
        s.done()


@pytest.mark.parametrize("display", SCREENS, ids=IDS)
def test_read_and_clear_notifications(server: Server, display) -> None:  # noqa: ANN001
    """Notifications: the bell on another page counts the new alerts; on the Alerts page, Mark read
    on one, then Clear all. The database agrees at each step, the page after a reload agrees, and
    the bell, once the list was opened, counts nothing."""
    from playwright.sync_api import sync_playwright

    _exe()  # found before the browser starts
    sid = server.sign_in()
    ids = [
        server.db.add_gateway_alert("user-1", k, f"{k.title()} · test", "body", "/watchlist", kind=k)
        for k in ("price", "report", "summary")
    ]
    with sync_playwright() as p:
        s = Screen(p, server, sid, display)
        page = s.page
        # another page first: the bell counts the three new alerts
        assert _bell(page, f"{server.base}/decks") == 3
        page.goto(f"{server.base}/notifications", wait_until="networkidle")
        assert page.locator(".al-row").count() == 3
        row = page.locator(f".al-row[data-alert='{ids[0]}']")
        row.locator(".al-kebab").click()
        page.get_by_role("menuitem", name="Mark read").click()
        page.wait_for_selector(f".al-row.unread[data-alert='{ids[0]}']", state="detached")
        unread = [a for a in server.db.gateway_alerts("user-1") if not a.get("read_at")]
        assert sorted(a["id"] for a in unread) == sorted(ids[1:])
        page.locator("[data-al-all='clear_all']").click()
        page.get_by_text("You're all caught up.").wait_for()
        assert server.db.gateway_alerts("user-1") == []
        page.reload(wait_until="networkidle")
        page.get_by_text("You're all caught up.").wait_for()
        # the bell on another page: the list was opened, nothing is new
        assert _bell(page, f"{server.base}/decks") == 0
        s.done()


def _deck_total(page, base: str) -> int:  # noqa: ANN001
    """The deck's card count as the deck list states it (another page than the deck's own)."""
    page.goto(f"{base}/decks", wait_until="networkidle")
    text = page.locator("a[href='/decks/42'] .views").first.inner_text()
    m = re.search(r"(\d+)\s*cards?", text)
    assert m, text
    return int(m.group(1))


def _bell(page, url: str) -> int:  # noqa: ANN001
    """The bell's count in the top bar of ``url`` (0 when it shows none), as a member sees it once
    the page has settled: the bell asks for its count when the browser is next idle."""
    page.goto(url, wait_until="load")
    page.evaluate(
        "() => new Promise(r => (window.requestIdleCallback || setTimeout)"
        "(() => setTimeout(r, 0), {timeout: 2100}))"
    )
    page.wait_for_load_state("networkidle")
    return int(
        page.evaluate(
            "() => { const b = document.querySelector('[data-bell] .n');"
            " return !b || b.hidden ? 0 : parseInt(b.textContent, 10) || 0; }"
        )
    )


@pytest.fixture
def lists_server(tmp_path: Path):
    from .test_lists_page import _seed as seed_lists

    s = Server(tmp_path, writes_enabled=True)
    seed_lists(s.ark)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("display", SCREENS, ids=IDS)
def test_change_a_wants_copies_and_note(lists_server: Server, display) -> None:  # noqa: ANN001
    """Binder's Wants: one more copy and a note. Archidekt's pin has exactly those (and the other
    pins are as they were); the page after a reload shows them."""
    from playwright.sync_api import sync_playwright

    _exe()  # found before the browser starts
    server = lists_server
    sid = _link(server)
    pins = server.ark.account.pins["alice"]
    before = [dict(p) for p in pins]
    with sync_playwright() as p:
        s = Screen(p, server, sid, display)
        page = s.page
        page.goto(f"{server.base}/collection/wants", wait_until="networkidle")
        row = page.locator(".lp .ui-table .tr[data-row]").first
        row.locator(".lp-step button[aria-label='One more']").click()
        _toast(page, "Saved on Archidekt.")
        note = row.locator("textarea")
        note.fill("Trade for it")
        with page.expect_response(
            lambda r: r.request.method == "POST" and "action=edit" in (r.request.post_data or "")
        ):
            note.press("Tab")
        after = server.ark.account.pins["alice"]
        assert after[0]["quantity"] == before[0]["quantity"] + 1 and after[0]["notes"] == "Trade for it"
        assert after[1:] == before[1:], "another pin changed"
        page.reload(wait_until="networkidle")
        row = page.locator(".lp .ui-table .tr[data-row]").first
        assert row.locator(".lp-step .n").inner_text() == str(before[0]["quantity"] + 1)
        assert row.locator("textarea").input_value() == "Trade for it"
        s.done()
