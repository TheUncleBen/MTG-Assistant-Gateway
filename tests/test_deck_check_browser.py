"""The deck page's /decks/{id}/check long-poll in the browser tests (conftest, DECK_CHECK_REFUSED).

The deck page asks /check, held up to 25 s, when it shows a deck as last read while it reads it
again. A "networkidle" load counts that request, so with an aged copy a deck page load waited out
the background read (8.5 s in the a6d09a11 audit's repro; up to 25 s, past the load's own limit,
on a starved runner). Every browser context the tests make now has that request refused at once,
so a load never waits on it; a test of the check itself opts out (``deck_check_live``) and sees
the strip the page draws from the real answer. Skipped without a Chromium build."""

from __future__ import annotations

import asyncio
import time

import pytest

from .test_deck_menu_browser import _EXE, Server, _chromium, _page

pytest.importorskip("playwright")

SLOW_REREAD = 8.0  # the background re-read's Archidekt answer, as in the audit's repro


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _age_and_slow(server: Server) -> list[str]:
    """The deck's kept copy aged past PAGE_DECK_RELOAD (a Playwright goto counts as a reload), so
    the next view shows it at once and reads it again in the background; that re-read takes
    SLOW_REREAD seconds. Returns the log of re-reads."""
    decks = server.app.state.gateway.decks
    real = decks._read_page_deck
    reads: list[str] = []

    async def slow(sub, ref, key, *, fresh):
        if key in decks._page_decks:  # the background re-read of a kept copy
            reads.append(str(ref))
            await asyncio.sleep(SLOW_REREAD)
        return await real(sub, ref, key, fresh=fresh)

    decks._read_page_deck = slow
    now = time.monotonic()
    decks._page_decks = {k: (now - 35.0, v) for k, (_t, v) in decks._page_decks.items()}
    return reads


def test_a_deck_page_load_never_waits_on_the_check_long_poll(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        failed: list[str] = []
        page.on("requestfailed", lambda r: failed.append(r.url))
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")  # reads and keeps it
        reads = _age_and_slow(server)
        started = time.monotonic()
        page.goto(f"{server.base}/decks/42?view=table", wait_until="networkidle")
        took = time.monotonic() - started
        assert reads == ["42"], reads  # the copy was shown as last read and read again behind it
        assert page.locator("[data-check]").count() == 1  # the page asked for the check...
        assert took < SLOW_REREAD / 2, f"the load waited {took:.1f} s on the check long-poll"
        assert page.locator("[data-check]").is_hidden()  # ...and, refused, says nothing
        assert not errors, errors
        browser.close()


@pytest.mark.deck_check_live
def test_a_test_of_the_check_itself_opts_out_and_gets_the_answer(server: Server) -> None:
    """Opted out, the page's own /check request reaches the gateway, is held for the background
    re-read, and its answer (the deck was renamed on Archidekt: changed) makes the page, shown on a
    reload, load itself again with the new deck."""
    from playwright.sync_api import expect, sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="load")
        reads = _age_and_slow(server)
        server.ark.decks[42]["name"] = "Renamed on Archidekt"  # what the re-read will find
        started = time.monotonic()
        with page.expect_response(
            lambda r: r.url == f"{server.base}/decks/42/check", timeout=(SLOW_REREAD + 10) * 1000
        ) as answer:
            page.goto(f"{server.base}/decks/42?view=table", wait_until="load")
            assert "Renamed on Archidekt" not in page.content()  # shown as last read, at once
        assert answer.value.status == 200
        assert time.monotonic() - started >= SLOW_REREAD - 0.5  # it was held for the re-read
        expect(page.locator("main")).to_contain_text("Renamed on Archidekt", timeout=10_000)
        assert reads == ["42"], reads
        assert not errors, errors
        browser.close()


# The strip's layout at a phone and a desktop, in both themes (0.7.46 audit P6): the two feedback.js
# branches a test never drove before.
STRIP_CASES = [(360, "light"), (360, "dark"), (1280, "light"), (1280, "dark")]


def _themed_page(p, server: Server, sid: str, width: int, theme: str, *, routes: bool = True):  # noqa: ANN001, ANN202
    """A page in the member's theme. ``routes=False``: a context without test_deck_menu_browser's
    picture route, because Chromium under Playwright's request interception caches nothing and so
    sends Pragma and Cache-Control: no-cache with every navigation, which the gateway reads as the
    browser's Reload (decks.is_reload); a view picked in the page must count as the plain link or
    form navigation it is."""
    if routes:
        browser, page, errors = _page(p, server, sid, width)
    else:
        browser = p.chromium.launch(executable_path=_EXE["path"], args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
    page.context.add_cookies([{"name": "mtg_theme", "value": theme, "url": server.base}])
    return browser, page, errors


def _pick_view(page, label: str, *, phone: bool) -> None:  # noqa: ANN001
    """Pick a view as a member does: the toolbar's View sheet on a phone (deck.js mirrors the
    select into it), the custom drop-down (static/select.js) on a wider screen."""
    if phone:
        page.locator(".toolbar .viewbtn").click()
        sheet = page.locator(".mtgbox[role=dialog]:not([hidden]) .viewsheet")
        sheet.wait_for(timeout=3000)
        sheet.locator("select#vs-view").select_option(label=label)
        return
    page.locator("#f-view-btn").click()
    listbox = page.locator("#msel-list[role=listbox]")
    listbox.wait_for(state="visible", timeout=3000)
    listbox.locator("[role=option]", has_text=label).click()


def _strip_fits(page, strip) -> None:  # noqa: ANN001
    from .test_card_search_filters_browser import CONTRAST
    from .test_overflow_sweep_browser import MEASURE

    assert strip.is_visible() and strip.get_attribute("role") == "status"
    assert page.evaluate(MEASURE) == []  # nothing scrolls sideways
    box, main = strip.bounding_box(), page.locator("main").bounding_box()
    assert box["x"] >= main["x"] - 1 and box["x"] + box["width"] <= main["x"] + main["width"] + 1, (box, main)
    assert strip.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    assert page.evaluate(CONTRAST, "[data-check], [data-check] *") == []


@pytest.mark.deck_check_live
@pytest.mark.parametrize(("width", "theme"), STRIP_CASES)
def test_a_deck_that_changed_offers_the_changes_in_a_strip_with_a_button(
    server: Server, width: int, theme: str
) -> None:
    """On a view switch (not a Reload) of a kept copy, the background re-read finds the deck changed
    on Archidekt: the strip says so with "Show the changes", a real button (44 px, not underlined,
    in the button's own colour) that loads the page again with the new deck. It fits the page at
    4.5:1 at a phone and a desktop, in both themes (audit P6)."""
    from playwright.sync_api import expect, sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _themed_page(p, server, sid, width, theme, routes=False)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="load")  # reads and keeps it
        reads = _age_and_slow(server)
        server.ark.decks[42]["name"] = "Renamed on Archidekt"  # what the re-read will find
        # another view, picked in the page (a GET form, no reload header): the copy is shown as
        # last read and the strip waits for the re-read
        with page.expect_response(
            lambda r: r.url == f"{server.base}/decks/42/check", timeout=(SLOW_REREAD + 10) * 1000
        ) as answer:
            _pick_view(page, "Table", phone=width < 600)
            page.wait_for_load_state("load")
            strip = page.locator("[data-check]")
            assert strip.get_attribute("data-reload") == "0" and "view=table" in page.url
            assert "Renamed on Archidekt" not in page.content()
        assert answer.value.status == 200 and answer.value.json() == {"changed": True, "failed": False}
        strip.wait_for(state="visible", timeout=5000)
        assert strip.inner_text().startswith("This deck changed on Archidekt.")
        assert "warn" not in (strip.get_attribute("class") or "")
        button = strip.locator("a.btn", has_text="Show the changes")
        assert button.count() == 1
        box = button.bounding_box()
        assert box["height"] >= 44, box
        style = button.evaluate(
            "e => { const cs = getComputedStyle(e); const probe = document.createElement('a');"
            " probe.className = 'btn'; probe.textContent = 'x'; document.body.appendChild(probe);"
            " const own = getComputedStyle(probe); const out = {deco: cs.textDecorationLine, color: cs.color,"
            " own: own.color, within: e.getBoundingClientRect().right <="
            " e.parentElement.getBoundingClientRect().right + 1};"
            " probe.remove(); return out; }"
        )
        assert style["deco"] == "none" and style["color"] == style["own"] and style["within"], style
        _strip_fits(page, strip)
        button.click()
        expect(page.locator("main")).to_contain_text("Renamed on Archidekt", timeout=10_000)
        assert reads == ["42"], reads
        assert not errors, errors
        browser.close()


@pytest.mark.deck_check_live
@pytest.mark.parametrize(("width", "theme"), STRIP_CASES)
def test_a_deck_archidekt_could_not_reread_says_how_old_the_copy_is(
    server: Server, width: int, theme: str
) -> None:
    """When the background re-read finds Archidekt unavailable, the strip turns to the warning
    look and says how old the copy on show is (deck_check_html's data-failed words), fitting the
    page at 4.5:1 at a phone and a desktop, in both themes (audit P6)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _themed_page(p, server, sid, width, theme)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        reads = _age_and_slow(server)
        server.ark.fail_deck_reads = True  # Archidekt answers 503 to the re-read
        with page.expect_response(
            lambda r: r.url == f"{server.base}/decks/42/check", timeout=(SLOW_REREAD + 10) * 1000
        ) as answer:
            page.goto(f"{server.base}/decks/42?view=table", wait_until="load")
        assert answer.value.status == 200 and answer.value.json() == {"changed": False, "failed": True}
        strip = page.locator("[data-check]")
        strip.wait_for(state="visible", timeout=5000)
        assert "warn" in (strip.get_attribute("class") or "")
        said = strip.get_attribute("data-failed")
        assert said.startswith("Archidekt can't be reached right now, so this is the deck as it was read ")
        assert strip.inner_text().strip() == said
        assert strip.locator("a").count() == 0  # nothing to press: the page's Reload reads again
        _strip_fits(page, strip)
        assert reads == ["42"], reads
        assert not errors, errors
        browser.close()
