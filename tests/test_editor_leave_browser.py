"""Leaving the deck editor with unsaved changes, in headless Chromium.

Owner test round 0.7.10: with an edit pending, Close editor brought up the browser's generic
"Leave site?" box. Close editor, a tab, a menu link and a form that leaves the page now ask in
the page's own save bar (Keep editing / Discard changes / Save changes); Discard leaves without
the browser's box, which is kept for what the page cannot see (closing the tab, reload).

Gate 0.7.10: at exactly 600 px the fixed save bar sat over the footer links (the save bar went
fixed at max-width 600px, the bottom tab bar and its padding stop at 599.98px). Every footer
link must be the element at its own centre at 599, 600 and 601 px, with a mouse and on a touch
screen.

The deck's name is hostile (long, unbroken, right-to-left text and emoji). Skipped without
Chromium."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mtg_gateway.archidekt_csv import parse_export, to_deck_json

from .test_editor_browser import PNG, _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

# long and unbroken, right-to-left Hebrew and Arabic, emoji (one of them a ZWJ sequence)
HOSTILE = (
    "Superlongunbrokendecknamewithoutanyspaceatall שלום עולם مرحبا بالعالم 🃏🔥🧙‍♂️ "
    "Aesi, Tyrant of Gyre Strait: Lands Matter Big Ramp Value Pile"
)
DECK = 51


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_deck.csv"
    cards = parse_export(fixture.read_text(encoding="utf-8"))
    s.ark.decks[DECK] = to_deck_json(cards[:14], deck_id=DECK, name=HOSTILE, owner="alice")
    s.ark.users["alice"]["decks"].append(DECK)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _exe():
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _browser(p, server: Server, sid: str, width: int, height: int, *, touch: bool = False, scheme="light"):
    exe = server.exe
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(
        viewport={"width": width, "height": height}, has_touch=touch, is_mobile=touch, color_scheme=scheme
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    return browser, ctx


# WCAG contrast of an element's text against what is behind it: the translucent backgrounds of it
# and its ancestors laid over the first opaque one
CONTRAST = """
(el) => {
  const rgb = (s) => {
    const m = (s.match(/[\\d.]+/g) || []).map(Number);
    return m.length === 3 ? [...m, 1] : m;
  };
  const f = (c) => { c /= 255; return c <= .03928 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
  const lum = ([r, g, b]) => .2126 * f(r) + .7152 * f(g) + .0722 * f(b);
  const layers = [];
  for (let a = el; a; a = a.parentElement) {
    const c = rgb(getComputedStyle(a).backgroundColor);
    if (c.length === 4 && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
  }
  let bg = [255, 255, 255];
  for (const [r, g, b, al] of layers.reverse())
    bg = [r * al + bg[0] * (1 - al), g * al + bg[1] * (1 - al), b * al + bg[2] * (1 - al)];
  const fg = rgb(getComputedStyle(el).color);
  const [hi, lo] = [lum(fg), lum(bg)].sort((x, y) => y - x);
  return (hi + .05) / (lo + .05);
}
"""


def _make_a_change(page) -> None:
    page.locator(".cats.existing .erow button[title='One more']").first.click()
    page.locator(".pendingbox .n", has_text="1").wait_for(timeout=3000)


@pytest.mark.parametrize(
    "width,height,touch,scheme",
    [(360, 720, True, "dark"), (720, 720, True, "light"), (1366, 900, False, "dark")],
)
def test_close_editor_with_pending_edits_asks_in_the_page(
    server: Server, width: int, height: int, touch: bool, scheme: str
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    server.exe = _exe()
    with sync_playwright() as p:
        browser, ctx = _browser(p, server, sid, width, height, touch=touch, scheme=scheme)
        try:
            page = ctx.new_page()
            dialogs: list[str] = []
            errors: list[str] = []
            page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
            page.on("pageerror", lambda e: errors.append(str(e)))
            edit = f"{server.base}/decks/{DECK}/edit"
            page.goto(edit, wait_until="networkidle")
            assert page.evaluate("() => document.documentElement.scrollWidth") <= width
            # nothing pending: Close editor just leaves
            page.locator("a.btn", has_text="Close editor").click()
            page.wait_for_url(f"{server.base}/decks/{DECK}")
            page.goto(edit, wait_until="networkidle")
            _make_a_change(page)

            # Close editor with a change pending: the question is in the page, Keep editing focused
            page.locator("a.btn", has_text="Close editor").click()
            bar = page.locator(".editbar .leavebar[role=alertdialog]")
            bar.wait_for(state="visible", timeout=3000)
            assert page.url == edit and dialogs == []
            assert "1 unsaved change" in bar.inner_text()
            assert page.evaluate("() => document.activeElement.textContent") == "Keep editing"
            box = bar.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 0.5
            assert 0 <= box["y"] and box["y"] + box["height"] <= height + 0.5, box
            for b in bar.locator("button").all():
                b_box = b.bounding_box()
                assert b_box and b_box["x"] >= 0 and b_box["x"] + b_box["width"] <= width + 0.5, b_box
            ratio = bar.locator("#leave-q").evaluate(CONTRAST)
            assert ratio >= 4.5, f"question text contrast {ratio:.2f}:1 ({scheme})"
            # Keep editing: the question goes, the change stays
            bar.locator("button", has_text="Keep editing").click()
            assert bar.count() == 0 and page.locator(".pendingbox .n").inner_text() == "1"
            # Escape is Keep editing too
            page.locator("a.btn", has_text="Close editor").click()
            bar.wait_for(state="visible", timeout=3000)
            page.keyboard.press("Escape")
            assert bar.count() == 0 and page.url == edit

            # a link out of the editor elsewhere on the page (the tab bar or rail, else the top bar)
            nav = page.locator(
                ".tabbar > a[href='/collection']:visible, .topbar nav.site a[href='/collection']:visible"
            )
            nav.first.click()
            bar.wait_for(state="visible", timeout=3000)
            assert page.url == edit and dialogs == []
            bar.locator("button", has_text="Keep editing").click()

            # Discard: leaves without the browser's own "Leave site?" box
            page.locator("a.btn", has_text="Close editor").click()
            bar.locator("button", has_text="Discard changes").click()
            page.wait_for_url(f"{server.base}/decks/{DECK}")
            assert dialogs == [], dialogs
            assert errors == []
        finally:
            browser.close()


def test_save_from_the_question_and_the_browser_box_on_tab_close(server: Server) -> None:
    """Save changes in the question saves and goes to the deck page; closing the tab with a change
    pending still gets the browser's own box (the page cannot ask then)."""
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    server.exe = _exe()
    with sync_playwright() as p:
        browser, ctx = _browser(p, server, sid, 1024, 800)
        try:
            page = ctx.new_page()
            dialogs: list[str] = []
            page.on("dialog", lambda d: (dialogs.append(d.type), d.accept()))
            edit = f"{server.base}/decks/{DECK}/edit"
            page.goto(edit, wait_until="networkidle")
            _make_a_change(page)
            page.locator("a.btn", has_text="Close editor").click()
            page.locator(".leavebar button", has_text="Save changes").click()
            # the save runs: back on the deck when it is applied, or (deck writes are off on this test
            # gateway) the save bar says why, the question gone and the changes still here
            page.wait_for_function(
                "() => /[?]ok=saved$|[/]proposals[/]/.test(location.href) ||"
                " /switched off/.test((document.querySelector('.editbar .status') || {}).textContent)",
                timeout=10000,
            )
            if "/edit" in page.url:
                assert "switched off" in page.locator(".editbar .status").inner_text()
                assert page.locator(".leavebar").count() == 0
            assert dialogs == []

            # (a page.goto is not a link on the page: with the unsaved change still there the
            # browser's own box comes up for it, and is accepted)
            page.goto(edit, wait_until="networkidle")
            assert dialogs in ([], ["beforeunload"]), dialogs
            dialogs.clear()
            # a form that leaves the page (the account menu's theme switch) asks too; Discard sends
            # it with the button that was pressed, and still no browser box
            _make_a_change(page)
            page.locator("summary[aria-label='Account menu']").click()
            page.locator("form[action='/theme'] button[value=dark]").click()
            page.locator(".leavebar").wait_for(state="visible", timeout=3000)
            assert page.url == edit and dialogs == []
            page.locator(".leavebar button", has_text="Discard changes").click()
            page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'", timeout=5000)
            assert dialogs == [] and page.locator(".pendingbox .n").inner_text() == "0"
            # closing the tab with a change pending: the browser's own box
            _make_a_change(page)
            helper = ctx.new_page()
            page.close(run_before_unload=True)
            for _ in range(50):
                if dialogs:
                    break
                helper.wait_for_timeout(100)
            assert dialogs == ["beforeunload"], dialogs
        finally:
            browser.close()


# Every footer link, with the page scrolled to the bottom: in the window, and the element at its
# own centre (not the fixed save bar or the tab bar over it). Returns the problems found.
COVERED = """
() => {
  window.scrollTo(0, document.documentElement.scrollHeight);
  const out = [];
  const vh = window.innerHeight, vw = document.documentElement.clientWidth;
  const links = [...document.querySelectorAll('footer.site a')];
  if (!links.length) out.push('no footer links');
  for (const a of links) {
    const r = a.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || x > vw || y < 0 || y > vh) {
      out.push(a.textContent.trim() + ' is outside the window');
      continue;
    }
    const top = document.elementFromPoint(x, y);
    if (!(top && a.contains(top))) {
      const t = top ? top.tagName.toLowerCase() + '.' + [...top.classList].join('.') : 'nothing';
      out.push(a.textContent.trim() + ' at ' + Math.round(x) + ',' + Math.round(y) + ' is under ' + t);
    }
  }
  return out;
}
"""


@pytest.mark.parametrize("touch", [False, True], ids=["mouse", "touch"])
def test_no_footer_link_under_the_save_bar_at_the_breakpoint(server: Server, touch: bool) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    server.exe = _exe()
    with sync_playwright() as p:
        problems: list[str] = []
        for width in (360, 599, 600, 601, 720):
            browser, ctx = _browser(p, server, sid, width, 720, touch=touch)
            try:
                page = ctx.new_page()
                page.goto(f"{server.base}/decks/{DECK}/edit", wait_until="networkidle")
                _make_a_change(page)  # the save bar's full height, count and all
                page.wait_for_timeout(100)
                problems += [f"{width}px: {m}" for m in page.evaluate(COVERED)]
                # and the save bar itself is never under the tab bar
                save = page.locator(".editbar button.review")
                b = save.bounding_box()
                top = page.evaluate(
                    "([x, y]) => { const e = document.elementFromPoint(x, y);"
                    " return !!e && !!e.closest('.editbar'); }",
                    [b["x"] + b["width"] / 2, b["y"] + b["height"] / 2],
                )
                if not top:
                    problems.append(f"{width}px: the Save button is covered")
            finally:
                browser.close()
        assert problems == [], "\n".join(problems)


# The centre of each visible row of an open list is that row (or inside it), not something on top
ROWS_ON_TOP = """
(sel) => [...document.querySelectorAll(sel)].filter(e => e.getClientRects().length).flatMap(e => {
  const r = e.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  if (y < 0 || y > innerHeight) return [];
  const box = e.closest('[role=listbox], .menu');  // rows scrolled out of their list are hidden anyway
  if (box) { const b = box.getBoundingClientRect(); if (y < b.top || y > b.bottom) return []; }
  const hit = document.elementFromPoint(x, y);
  return hit && (hit === e || e.contains(hit)) ? [] :
    [`${(e.textContent || '').trim().slice(0, 30)} under ${hit ? hit.className || hit.tagName : 'nothing'}`];
})
"""


def test_the_top_bar_list_and_the_more_sheet_sit_above_the_save_bar(server: Server) -> None:
    # Gate 0.7.11: a suggestion row lay under the editor's save bar, so tapping it pressed Save
    # changes; on phones the More sheet's rows lay under it too.
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    server.exe = _exe()
    with sync_playwright() as p:
        problems: list[str] = []
        for width, touch in ((600, True), (720, True), (1366, False)):
            browser, ctx = _browser(p, server, sid, width, 760, touch=touch)
            try:
                page = ctx.new_page()
                page.goto(f"{server.base}/decks/{DECK}/edit", wait_until="networkidle")
                _make_a_change(page)
                box = page.locator("#site-q")
                if box.is_visible():
                    box.click()
                    box.press_sequentially("filler", delay=25)  # a long list
                    page.locator(".topbar [role=option]").first.wait_for(timeout=3000)
                    problems += [
                        f"{width}px: {m}" for m in page.evaluate(ROWS_ON_TOP, ".topbar [role=option]")
                    ]
            finally:
                browser.close()
        for width in (360, 390, 599):
            browser, ctx = _browser(p, server, sid, width, 760, touch=True)
            try:
                page = ctx.new_page()
                page.goto(f"{server.base}/decks/{DECK}/edit", wait_until="networkidle")
                _make_a_change(page)
                page.locator(".tabbar details.more summary").click()
                page.wait_for_timeout(150)
                problems += [
                    f"{width}px: {m}"
                    for m in page.evaluate(ROWS_ON_TOP, ".tabbar details.more .menu.sheet a")
                ]
            finally:
                browser.close()
        assert problems == [], "\n".join(problems)
