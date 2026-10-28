"""Release 0.7.29, approvals and proposals in headless Chromium: the Proposals page with its
checkboxes, the confirm and the result; the deck page's waiting strip; and the review page of a
change that keeps formatting. Phone, Fold and desktop, light and dark: no sideways scroll,
labels at 4.5:1. Skipped without a Chromium build."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from mtg_gateway.archidekt import rich_text

from .test_editor_browser import _link
from .test_proposal_review_browser import CONTRAST
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

FORMATTED = json.dumps(
    {
        "ops": [
            {"insert": "Great "},
            {"insert": "ramp", "attributes": {"bold": True}},
            {"insert": " deck. See "},
            {"insert": "the guide", "attributes": {"link": "https://example.com/guide"}},
            {"insert": ".\nOld plan here.\n"},
        ]
    }
)

SIZES = (
    ("phone", 390, 844, True, "light"),
    ("phone", 390, 844, True, "dark"),
    ("fold", 673, 841, True, "dark"),
    ("desktop", 1280, 800, False, "light"),
)


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _waiting(server: Server) -> tuple[str, list[str], str]:
    """Three waiting card changes to deck 42 and a description edit that keeps formatting."""
    sid = _link(server)
    server.ark.decks[42]["description"] = FORMATTED
    decks = server.app.state.gateway.decks

    async def make() -> tuple[list[str], str]:
        ids = []
        for name in ("Opt", "Arcane Signet", "Delver of Secrets"):
            p = await decks.propose("user-1", "42", [{"action": "add", "card_name": name}])
            ids.append(p["proposal_id"])
        new = rich_text(FORMATTED).replace("Great", "Fun")
        d = await decks.propose_deck_details("user-1", "42", {"description": new})
        return ids, d["proposal_id"]

    ids, fmt = asyncio.run(make())
    return sid, ids, fmt


def _context(browser, server: Server, sid: str, vw: int, vh: int, phone: bool, scheme: str):
    ctx = browser.new_context(
        viewport={"width": vw, "height": vh}, color_scheme=scheme, is_mobile=phone, has_touch=phone
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    # nothing leaves the test: pictures and links in a description point elsewhere
    ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(server.base) else r.abort())
    return ctx


def _local_only(route, base: str) -> None:
    route.continue_() if route.request.url.startswith(base) else route.abort()


def _readable(page, selector: str, where: str) -> None:
    for el in page.locator(selector).all():
        if not el.is_visible():
            continue
        got = el.evaluate(CONTRAST)
        if got["disabled"]:
            continue  # a locked button (click guard) is exempt; its label is checked when unlocked
        assert got["ratio"] >= 4.5, (where, selector, el.inner_text()[:40], got)


def _no_sideways(page, vw: int, where: str) -> None:
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= vw, f"{where}: page scrolls sideways ({width}px)"


def test_proposals_page_strip_and_formatting_review_in_the_browser(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, ids, fmt = _waiting(server)
    shots = os.environ.get("MTG_SHOTS_DIR")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        try:
            for name, vw, vh, phone, scheme in SIZES:
                where = f"{name}-{scheme}"
                ctx = _context(browser, server, sid, vw, vh, phone, scheme)
                page = ctx.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))

                # the Proposals page: rows to tick, grouped by deck
                page.goto(f"{server.base}/proposals", wait_until="networkidle")
                for pid in ids:
                    assert page.locator(f"input[name=pick][value='{pid}']").count() == 1, where
                _readable(page, "main a, main label, main .st, main h2, main p", where)
                _no_sideways(page, vw, where)
                if shots:
                    page.screenshot(path=f"{shots}/a-proposals-{where}.png", full_page=True)
                # a tick shows Apply and Reject within reach, on screen
                page.locator(f"input[name=pick][value='{ids[0]}']").check()
                apply = page.locator("button[name=action][value=apply]").first
                assert apply.is_visible() and apply.is_enabled(), where
                box = apply.bounding_box()
                assert box is not None and 0 <= box["y"] and box["y"] + box["height"] <= vh, (where, box)
                # and nothing (the phone's tab bar) covers it: a tap lands on the button itself
                hit = page.evaluate(
                    "([x, y]) => { const e = document.elementFromPoint(x, y);"
                    " return !!e && !!e.closest('button[name=action][value=apply]'); }",
                    [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
                )
                assert hit, f"{where}: Apply is covered"
                _readable(page, ".wbar button, .wbar span", where)
                if shots:
                    page.screenshot(path=f"{shots}/a-proposals-ticked-{where}.png")

                # the deck page's Proposals pill and its box (navigation round 2)
                page.goto(f"{server.base}/decks/42", wait_until="networkidle")
                page.locator(".banner .soc.prop").click()
                strip = page.locator("#proposals form.ybox")
                strip.wait_for(timeout=5000)
                _readable(page, "#proposals form.ybox a, #proposals form.ybox label", where)
                _no_sideways(page, vw, where)
                if shots:
                    strip.scroll_into_view_if_needed()
                    page.screenshot(path=f"{shots}/a-strip-{where}.png")

                # the review page of a change that keeps formatting draws how it will look
                page.goto(f"{server.base}/proposals/{fmt}", wait_until="networkidle")
                assert page.get_by_text("How it will look on Archidekt").count() >= 1, where
                assert page.locator(".qdoc strong", has_text="ramp").count() == 1, where
                _readable(page, ".qdoc, .qdoc strong, .qdoc a", where)
                _no_sideways(page, vw, where)
                if shots:
                    page.screenshot(path=f"{shots}/a-formatting-review-{where}.png", full_page=True)
                assert errors == [], (where, errors)
                ctx.close()

            # tick two, Apply checked, the confirm, the press, the result (desktop, once: it applies)
            ctx = _context(browser, server, sid, 1280, 800, False, "dark")
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/proposals", wait_until="networkidle")
            page.locator(f"input[name=pick][value='{ids[0]}']").check()
            page.locator(f"input[name=pick][value='{ids[1]}']").check()
            page.locator("button[name=action][value=apply]").first.click()
            page.wait_for_selector("text=Apply 2 changes")
            button = page.locator("form.guarded button[value=apply]")
            # locked until the page was seen (click guard), but still in the Tab order, so a fast
            # keyboard user does not skip past it
            assert button.get_attribute("aria-disabled") == "true" and not button.evaluate("e => e.disabled")
            locked = button.evaluate(CONTRAST)
            assert locked["ratio"] >= 4.5 and locked["opacity"] == 1, locked
            reached = False
            for _ in range(40):
                page.keyboard.press("Tab")
                if button.evaluate("e => e === document.activeElement"):
                    reached = True
                    break
            assert reached, "Tab never reached Apply"
            if button.get_attribute("aria-disabled") == "true":
                # pressed while locked: refused, nothing sent
                page.keyboard.press("Enter")
                page.wait_for_timeout(300)
                assert "/proposals/applied/" not in page.url and server.ark.patches == []
            page.wait_for_function(
                "() => !document.querySelector('form.guarded button[value=apply]')"
                ".hasAttribute('aria-disabled')",
                timeout=10000,
            )
            page.wait_for_timeout(400)  # the unlocked colours fade in over 0.2 s
            got = button.evaluate(CONTRAST)
            assert got["ratio"] >= 4.5 and got["opacity"] == 1, got
            ring = button.evaluate("e => getComputedStyle(e).outlineStyle")
            assert ring != "none", ring  # focused from the keyboard: the focus ring shows
            if shots:
                page.screenshot(path=f"{shots}/a-confirm-desktop-dark.png")
            page.keyboard.press("Enter")
            page.wait_for_url("**/proposals/applied/**")
            # the result card updates in place while applying (applied-live.js); done, the mark goes
            page.wait_for_function("() => !document.querySelector('[data-applied-live]')", timeout=20000)
            body = page.locator("main").inner_text()
            assert "Opt" in body and "Arcane Signet" in body, body
            if shots:
                page.screenshot(path=f"{shots}/a-result-desktop-dark.png", full_page=True)
            assert errors == [], errors
            ctx.close()
        finally:
            browser.close()
    names = [c["card"]["oracleCard"]["name"] for c in server.ark.decks[42]["cards"]]
    assert "Opt" in names and "Arcane Signet" in names and "Delver of Secrets" not in names


WORDS_BROKEN = """(sel) => {
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      const re = /\\S+/g;
      let m;
      while ((m = re.exec(n.data))) {
        const r = document.createRange();
        r.setStart(n, m.index);
        r.setEnd(n, m.index + m[0].length);
        const lines = new Set([...r.getClientRects()].map((b) => Math.round(b.top)));
        if (lines.size > 1) out.push(m[0]);
      }
    }
  }
  return out;
}"""


# The same, for words that would fit whole on a line of their box: on a phone at 200% text the
# change column of the redesigned table (beside the tick box and the 44 px Review arrow) is
# narrower than a long word such as "description:", which then has to break.
WORDS_BROKEN_THAT_FIT = """(sel) => {
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      const re = /\\S+/g;
      let m;
      while ((m = re.exec(n.data))) {
        const r = document.createRange();
        r.setStart(n, m.index);
        r.setEnd(n, m.index + m[0].length);
        const rects = [...r.getClientRects()];
        const lines = new Set(rects.map((b) => Math.round(b.top)));
        const width = rects.reduce((sum, b) => sum + b.width, 0);
        if (lines.size > 1 && width <= el.clientWidth) out.push(m[0]);
      }
    }
  }
  return out;
}"""


def test_the_checked_bar_at_200_percent_text_on_a_phone(server: Server) -> None:
    """At 200% text the bar stays two rows (Apply and Reject side by
    side, Clear by the count), words wrap whole, and Apply is on screen and not covered."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, ids, _fmt = _waiting(server)
    shots = os.environ.get("MTG_SHOTS_DIR")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        try:
            for vw, vh, scheme in ((360, 780, "dark"), (390, 844, "light")):
                where = f"{vw}-{scheme}-200"
                ctx = _context(browser, server, sid, vw, vh, True, scheme)
                # the phone's text size at 200%: the root font doubles
                ctx.add_init_script(
                    "document.addEventListener('DOMContentLoaded', () =>"
                    " { document.documentElement.style.fontSize = '200%'; });"
                )
                page = ctx.new_page()
                page.goto(f"{server.base}/proposals", wait_until="networkidle")
                bar = page.locator(".ui-bar").first  # the list's selection bar
                assert not bar.locator("button.clear").is_visible(), where  # nothing checked yet
                page.locator(f"input[name=pick][value='{ids[0]}']").check()
                page.locator(f"input[name=pick][value='{ids[1]}']").check()
                assert bar.locator("button.clear").is_visible(), where
                apply = bar.locator("button[name=action][value=apply]")
                reject = bar.locator("button[name=action][value=reject]")
                a, r, b = apply.bounding_box(), reject.bounding_box(), bar.bounding_box()
                assert a and r and b, where
                assert abs(a["y"] - r["y"]) < 2, (where, a, r)
                assert b["height"] <= 0.3 * vh, (where, b)
                assert 0 <= a["y"] and a["y"] + a["height"] <= vh, (where, a)
                hit = page.evaluate(
                    "([x, y]) => { const e = document.elementFromPoint(x, y);"
                    " return !!e && !!e.closest('button[name=action][value=apply]'); }",
                    [a["x"] + a["width"] / 2, a["y"] + a["height"] / 2],
                )
                assert hit, f"{where}: Apply is covered"
                assert page.evaluate(WORDS_BROKEN_THAT_FIT, ".pr-table .title .t") == [], (
                    where,
                    page.evaluate(WORDS_BROKEN_THAT_FIT, ".pr-table .title .t"),
                )
                _readable(page, ".ui-bar button, .ui-bar span", where)
                _no_sideways(page, vw, where)
                if shots:
                    page.screenshot(path=f"{shots}/a-proposals-ticked-{where}.png")
                ctx.close()
        finally:
            browser.close()


def test_reject_asks_first_on_the_list_and_the_deck_strip(server: Server) -> None:
    """Reject on the Proposals page and the deck's Proposals box asks first in the shared box
    ("Reject 2 changes?": Cancel, then the solid red Reject 2, as every delete on the site asks);
    Cancel rejects nothing and gives the focus back; the box's Reject rejects only the checked
    ones. Phone, Fold and desktop, light and dark: readable, no sideways scroll."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, ids, fmt = _waiting(server)
    db = server.app.state.gateway.db
    shots = os.environ.get("MTG_SHOTS_DIR")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        try:
            for name, vw, vh, phone, scheme in SIZES:
                where = f"{name}-{scheme}"
                ctx = _context(browser, server, sid, vw, vh, phone, scheme)
                page = ctx.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.goto(f"{server.base}/proposals", wait_until="networkidle")
                bar = page.locator(".ui-bar").first  # the list's selection bar
                page.locator(f"input[name=pick][value='{ids[0]}']").check()
                page.locator(f"input[name=pick][value='{ids[1]}']").check()
                bar.locator("button[name=action][value=reject]").click()
                ask = page.locator(".mtgbox-scrim.in .mtgbox")
                ask.wait_for(timeout=5000)
                assert ask.locator(".bh h2").inner_text() == "Reject 2 changes?", where
                assert "can't be brought back" in ask.locator(".body").inner_text(), where
                assert bar.locator(".rejectask").count() == 0, where  # no second row in the bar
                got = ask.locator(".acts button").evaluate_all(
                    "els => els.map(e => ({focused: e === document.activeElement, cls: e.className,"
                    " text: e.textContent.trim()}))"
                )
                assert got == [
                    {"focused": True, "cls": "btn", "text": "Cancel"},
                    {"focused": False, "cls": "danger", "text": "Reject 2"},
                ], (where, got)
                _readable(page, ".mtgbox .bh h2, .mtgbox .body p, .mtgbox .acts button", where)
                _no_sideways(page, vw, where)
                if shots:
                    page.screenshot(path=f"{shots}/a-reject-box-{where}.png")
                ask.locator("button", has_text="Cancel").click()
                page.locator(".mtgbox-scrim").wait_for(state="detached", timeout=5000)
                reject = bar.locator("button[name=action][value=reject]")
                assert reject.is_visible() and reject.evaluate("e => e === document.activeElement"), where
                assert all(db.get_proposal(i, "user-1")["state"] == "pending" for i in ids), where
                assert errors == [], (where, errors)
                ctx.close()
            # the box's Reject rejects the checked ones, in the deck's Proposals box too
            ctx = _context(browser, server, sid, 390, 844, True, "light")
            page = ctx.new_page()
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            # navigation round 2: the banner's Proposals pill opens the deck's box
            page.locator(".banner .soc.prop").click()
            strip = page.locator("#proposals form.ybox")
            strip.wait_for(timeout=5000)
            strip.locator(f"input[name=pick][value='{ids[2]}']").check()
            strip.locator("button[name=action][value=reject]").click()
            ask = page.locator(".mtgbox-scrim.in .mtgbox")
            ask.wait_for(timeout=5000)
            assert ask.locator(".bh h2").inner_text() == "Reject this change?"
            assert db.get_proposal(ids[2], "user-1")["state"] == "pending"
            with page.expect_navigation():
                ask.locator(".acts button.danger", has_text="Reject").click()
            assert db.get_proposal(ids[2], "user-1")["state"] == "rejected"
            assert [db.get_proposal(i, "user-1")["state"] for i in (ids[0], ids[1], fmt)] == ["pending"] * 3
            ctx.close()
        finally:
            browser.close()


def test_the_full_test_buttons_fit_every_screen_and_text_size(tmp_path: Path) -> None:
    """The banner's full-test texts on the deck page: Test and play's "Run full test" and "Solo test
    only", with their buttons' tooltips, at 344, 360 and 390 px, on the Fold and on desktop, light
    and dark, 100% and 200% text. No sideways scroll, words whole, labels readable (contrast at least
    4.5:1), and the tooltips word for word."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    s = Server(tmp_path, writes_enabled=True, forge_url="http://127.0.0.1:9")
    s.start()
    shots = os.environ.get("MTG_SHOTS_DIR")
    try:
        sid = _link(s)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            try:
                for vw, vh, phone in (
                    (344, 882, True),
                    (360, 780, True),
                    (390, 844, True),
                    (673, 841, True),
                    (1280, 800, False),
                ):
                    for scheme in ("light", "dark"):
                        for text in ("100%", "200%"):
                            where = f"{vw}-{scheme}-{text}"
                            ctx = _context(browser, s, sid, vw, vh, phone, scheme)
                            ctx.add_init_script(
                                "document.addEventListener('DOMContentLoaded', () =>"
                                f" {{ document.documentElement.style.fontSize = '{text}'; }});"
                            )
                            page = ctx.new_page()
                            page.goto(f"{s.base}/decks/42", wait_until="networkidle")
                            # navigation round 2: the banner's Test and play opens the shared
                            # menu; its Run full test and Solo test only submit the banner's hidden
                            # forms, whose buttons keep the busy label and the title
                            run = page.locator("#fullf button")
                            assert run.get_attribute("data-busy-label") == "Testing…", where
                            assert run.get_attribute("title") == (
                                "The solo test (300 games, no opponents), a legality check and the deck "
                                "statistics, plus Forge games against precons when Forge is on and the deck "
                                "has a commander; saved under History. The report opens at once and fills in "
                                "as each part finishes."
                            ), where
                            form_btn = page.locator("#solof button")
                            assert form_btn.get_attribute("data-busy-label") == "Testing…", where
                            assert form_btn.get_attribute("title") == (
                                "The same run without Forge games: the solo test, the legality check and "
                                "the statistics"
                            ), where
                            page.locator(".banner .decktestbtn").click()
                            menu = ".ctxmenu[role=menu] [role=menuitem]"
                            full = page.locator(menu, has_text="Run full test")
                            solo = page.locator(menu, has_text="Solo test only")
                            for b in (page.locator(".banner .decktestbtn"), full, solo):
                                assert b.is_visible(), where
                                box = b.bounding_box()
                                assert box and box["x"] >= 0 and box["x"] + box["width"] <= vw, (where, box)
                                assert b.evaluate("e => e.scrollWidth <= e.clientWidth + 1"), where
                            sel = ".ctxmenu[role=menu] [role=menuitem], .banner .decktestbtn"
                            assert page.evaluate(WORDS_BROKEN, sel) == [], (
                                where,
                                page.evaluate(WORDS_BROKEN, sel),
                            )
                            _readable(page, sel, where)
                            page.keyboard.press("Escape")
                            # the banner itself never runs wider than the screen (the whole page:
                            # test_the_deck_page_never_scrolls_sideways)
                            _no_sideways(page, vw, where)
                            banner = page.locator(".banner").first.bounding_box()
                            assert banner and banner["x"] + banner["width"] <= vw + 0.5, (where, banner)
                            assert page.evaluate(
                                "() => [...document.querySelectorAll('.banner .controls *')]"
                                ".every(e => e.getBoundingClientRect().right <= innerWidth + 0.5"
                                " || !e.getClientRects().length || e.closest('details:not([open])'))"
                            ), where
                            if shots:
                                page.screenshot(path=f"{shots}/a-full-test-{where.replace('%', '')}.png")
                            ctx.close()
            finally:
                browser.close()
    finally:
        s.stop()


WIDENS_PAGE = """(vw) => { const de = document.documentElement, base = de.scrollWidth, out = [];
  for (const e of document.querySelectorAll('body *')) {
    const r = e.getBoundingClientRect();
    if (!(r.width > 0 && r.right > vw + 0.5)) continue;
    const old = e.style.display; e.style.display = 'none';
    if (de.scrollWidth < base)
      out.push(`${e.tagName}.${e.getAttribute('class')} ${(e.textContent || '').trim().slice(0, 30)}`);
    e.style.display = old; }
  return out.slice(-6); }"""

# each of these stays inside its panel (they used to run past the panel's edge at 344 px, 200% text)
INSIDE_PANEL = """() => [...document.querySelectorAll(
    '.panel.description > h2, .comments .panel-head h2, .checks li, .curve, .curveaxis')]
  .filter(e => { const p = e.closest('.panel').getBoundingClientRect(), r = e.getBoundingClientRect();
    return r.width > 0 && (r.right > p.right + 0.5 || e.scrollWidth > e.clientWidth + 1); })
  .map(e => `${e.tagName}.${e.getAttribute('class')} ${(e.textContent || '').trim().slice(0, 30)}`)"""


VIEWS_COLUMNS = (
    "() => getComputedStyle(document.querySelector('form.views')).gridTemplateColumns.split(' ').length"
)

CHECK_VALUES = """() => [...document.querySelectorAll('.checks li')].map(li => {
  const b = li.querySelector('b').getBoundingClientRect();
  const v = li.querySelector('span').getBoundingClientRect();
  return Math.abs(b.top - v.top) < 4 ? 'beside' : 'under'; })"""


def test_the_deck_page_never_scrolls_sideways(tmp_path: Path) -> None:
    """The whole deck page, not only its banner: 320, 344, 360, 390, 430 and 600 px, the Fold and
    desktop, light and dark, 100% and 200% text, scripts on and off. At 344 px with 200% text a mana
    curve count pushed the page 4 px wider than the screen (39 px with scripts off, from the Group by
    box), and the Description and Comments headings and a long deck check ran past their panels; at
    320 px the probability form's hidden native select added 5 px. At 100% text the layout stays as
    it was: two View/Group/Sort boxes a row on a phone, and each check's value beside its label."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    s = Server(tmp_path, writes_enabled=True, forge_url="http://127.0.0.1:9")
    s.start()
    try:
        sid = _link(s)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            try:
                for vw, vh, phone in (
                    (320, 640, True),
                    (344, 882, True),
                    (360, 780, True),
                    (390, 844, True),
                    (430, 932, True),
                    (600, 960, True),
                    (673, 841, True),
                    (1280, 800, False),
                ):
                    for scheme in ("light", "dark"):
                        for text in ("100%", "200%"):
                            for scripts in (True, False):
                                where = f"{vw}-{scheme}-{text}-{'js' if scripts else 'nojs'}"
                                ctx = browser.new_context(
                                    viewport={"width": vw, "height": vh},
                                    color_scheme=scheme,
                                    is_mobile=phone,
                                    has_touch=phone,
                                    java_script_enabled=scripts,
                                )
                                ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": s.base}])
                                ctx.route("**/*", lambda r: _local_only(r, s.base))

                                # the text size goes into the page itself: with scripts off a style
                                # tag can't be added afterwards
                                def sized(route, _request, text=text):
                                    got = route.fetch()
                                    head, _, tail = got.text().rpartition("</body>")
                                    body = f"{head}<style>html{{font-size:{text}}}</style></body>{tail}"
                                    route.fulfill(response=got, body=body)

                                ctx.route("**/decks/42", sized)
                                page = ctx.new_page()
                                page.goto(f"{s.base}/decks/42", wait_until="load")
                                assert page.locator("section.panel.description").count() == 1, where
                                fs = page.evaluate("getComputedStyle(document.documentElement).fontSize")
                                assert text == "100%" or fs == "32px", (where, fs)
                                width = page.evaluate("document.documentElement.scrollWidth")
                                assert width <= vw, (where, width, page.evaluate(WIDENS_PAGE, vw))
                                assert page.evaluate(INSIDE_PANEL) == [], where
                                # at 100% text the layout is as before the fix: View as, Group by and
                                # Sort by two a row on a phone (never three), and each deck check's
                                # value beside its label
                                if text == "100%":
                                    cols = page.evaluate(VIEWS_COLUMNS)
                                    assert vw > 600 or cols == 2, (where, cols)
                                    assert set(page.evaluate(CHECK_VALUES)) == {"beside"}, where
                                ctx.close()
            finally:
                browser.close()
    finally:
        s.stop()


def test_card_search_keeps_its_name_and_no_mana_data_is_readable(tmp_path: Path) -> None:
    """On a phone the Quick add row's Card search button shows only its icon: screen readers still
    hear "Card search", as on wider screens. "No mana data" reads at 4.5:1 or better in both themes
    (it used to be dimmed with opacity to 2.77:1 light and 3.24:1 dark)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    s = Server(tmp_path, writes_enabled=True, forge_url="http://127.0.0.1:9")
    s.start()
    try:
        sid = _link(s)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            try:
                for vw, vh, phone in ((344, 882, True), (390, 844, True), (1280, 800, False)):
                    for scheme in ("light", "dark"):
                        where = f"{vw}-{scheme}"
                        ctx = _context(browser, s, sid, vw, vh, phone, scheme)
                        page = ctx.new_page()
                        page.goto(f"{s.base}/decks/42", wait_until="networkidle")
                        search = page.locator("form.quick button[type=submit]")
                        assert search.is_visible(), where
                        assert page.get_by_role("button", name="Card search", exact=True).count() == 1, where
                        label = search.locator("span")
                        assert label.is_visible() == (not phone), where
                        empty = page.locator(".cbar.empty span", has_text="No mana data").first
                        assert empty.is_visible(), where
                        assert empty.evaluate(
                            "e => { for (; e; e = e.parentElement) if (+getComputedStyle(e).opacity < 1)"
                            " return false; return true; }"
                        ), where
                        got = empty.evaluate(CONTRAST)
                        assert got["ratio"] >= 4.5, (where, got)
                        ctx.close()
            finally:
                browser.close()
    finally:
        s.stop()


def test_the_deck_menus_run_full_test_submits_the_full_test(tmp_path: Path) -> None:
    """N-5 (review 2026-10-22): More's "Run full test" finds the banner's full-test form without a
    has-selector and submits it: the report page opens, with no script error."""
    import re

    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    s = Server(tmp_path, writes_enabled=True)
    s.start()
    try:
        sid = _link(s)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            try:
                ctx = _context(browser, s, sid, 1280, 800, False, "light")
                page = ctx.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"{s.base}/decks/42", wait_until="networkidle")
                page.locator(".banner .decktestbtn").click()
                page.locator(".ctxmenu[role=menu] [role=menuitem]", has_text="Run full test").click()
                page.wait_for_url(re.compile(r"/(history/reports|decks/42/report)"), timeout=60000)
                assert errors == [], errors
            finally:
                browser.close()
    finally:
        s.stop()
