"""Release 0.7.29, approvals and proposals in headless Chromium: the Proposals page with its
checkboxes, the confirm and the result; the deck page's waiting strip; and the review page of a
change that keeps formatting (Q137). Phone, Fold and desktop, light and dark: no sideways scroll,
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

                # the deck page's strip
                page.goto(f"{server.base}/decks/42", wait_until="networkidle")
                strip = page.locator("details.wstrip")
                assert strip.count() == 1, where
                if not strip.evaluate("e => e.open"):
                    strip.locator("summary").click()
                # open, the heading has no separator left over from the hidden "Review"
                assert not strip.locator("summary").inner_text().strip().endswith("·"), where
                _readable(page, "details.wstrip summary, details.wstrip a, details.wstrip label", where)
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
            # keyboard user does not skip past it (review round 2)
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
            page.wait_for_function(
                "() => !document.querySelector(\"meta[http-equiv='refresh']\")", timeout=20000
            )
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


def test_the_checked_bar_at_200_percent_text_on_a_phone(server: Server) -> None:
    """Review round 1, finding 8: at 200% text the bar stays two rows (Apply and Reject side by
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
                bar = page.locator(".wbar:not(.inline)").first
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
                assert page.evaluate(WORDS_BROKEN, ".pick .what") == [], (
                    where,
                    page.evaluate(WORDS_BROKEN, ".pick .what"),
                )
                _readable(page, ".wbar button, .wbar span", where)
                _no_sideways(page, vw, where)
                if shots:
                    page.screenshot(path=f"{shots}/a-proposals-ticked-{where}.png")
                ctx.close()
        finally:
            browser.close()
