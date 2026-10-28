"""0.7.18 in headless Chromium: the deck page's Table and Scroll views; putting stacks in your own
order (arrow keys on a stack's handle, kept per deck and grouping (on the account from 0.7.29,
so another device shows the same order; an order kept in the browser before is copied once), Reset puts the
deck's order back; 0.7.22: dragging a handle by mouse or finger, the page scrolling on near the
window's edge, a card held and slid to a category off screen); selecting several cards and moving,
refinishing or removing them in one save with one Undo. Screenshots go to MTG_SHOTS when it is set.
Skipped without a Chromium build."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from .test_deck_menu_browser import EDIT_URL, Server, _cats, _chromium, _edits, _file_under, _overflow, _page

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_SHOTS")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=False)


def _rows(server: Server, name: str) -> list[dict]:
    return [c for c in server.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == name]


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1366, "light")])
def test_table_and_scroll_views(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42?view=table", wait_until="networkidle")
        assert page.locator(".deckview.table").count() == 1
        assert page.locator(".deckview.table .th").count() >= 1
        row = page.locator(".deckview.table .row[data-card='Cultivate']").first
        assert "Sorcery" in row.locator(".ty").inner_text()
        assert _overflow(page) == 0
        _shot(page, f"deck-table-{width}-{scheme}")
        row.click()
        page.wait_for_selector(".cardview.open", timeout=3000)
        page.keyboard.press("Escape")
        # the viewer closes through history.back(): wait for it, or the next goto can be aborted
        # by that traversal on a busy machine (net::ERR_ABORTED)
        page.wait_for_selector(".cardview.open", state="detached", timeout=3000)

        page.goto(f"{server.base}/decks/42?view=scroll", wait_until="networkidle")
        strip = page.locator(".deckview.scroll .cards").first
        assert strip.evaluate("e => getComputedStyle(e).overflowX") == "auto"
        # a group wider than the window scrolls sideways in its own strip
        wide = (
            "() => [...document.querySelectorAll('.deckview.scroll .cards')]"
            ".some(e => e.scrollWidth > e.clientWidth)"
        )
        assert page.evaluate(wide)
        assert _overflow(page) == 0  # ... inside itself, never the page
        _shot(page, f"deck-scroll-{width}-{scheme}")
        assert errors == []
        browser.close()


def test_stacks_follow_archidekts_category_order_with_no_handles(server: Server) -> None:
    """Owner decision C4 (0.7.46): the stacks come in Archidekt's category order, the one order
    there is; the side stacks last (D-14). No drag handle, no Reset stack order, and a stack
    order kept on the account by an earlier version changes nothing."""
    from playwright.sync_api import sync_playwright

    from mtg_gateway import views
    from mtg_gateway.archidekt import parse_deck

    expected = [n for n, _cards in views.cards_by_category(parse_deck(server.ark.decks[42]))]
    assert expected[0] == "Commander" and expected[-1] == "Maybeboard" and len(expected) >= 4
    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}{'/decks/42?view=text'}", wait_until="networkidle")
        names = page.evaluate(ORDER)
        assert names == expected, names
        assert page.locator(".stackgrip, .stack-reset").count() == 0
        # a stack order an earlier version kept on the account changes nothing
        server.db.set_pref("user-1", "stack:42:category", [names[2], names[0], names[1], *names[3:]])
        page.reload(wait_until="networkidle")
        assert page.evaluate(ORDER) == names
        assert errors == []
        browser.close()


def test_choices_kept_in_the_browser_before_are_copied_to_the_account_once(server: Server) -> None:
    """The stats toggles and the dismissed EDHPlay steps that 0.7.28 kept in this browser show
    at once on the first visit after the update, are saved to the account, and are removed from
    the browser; a value the account already has wins. A stack order kept then is dropped: the
    stacks follow Archidekt's order now (0.7.46, owner decision C4)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    server.db.set_pref("user-1", "edhplay.steps", True)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        names = page.eval_on_selector_all(".deckview .stack", "l => l.map(s => s.dataset.group)")
        mine = [names[2], names[0], names[1], *names[3:]]
        page.evaluate(
            "o => { localStorage.setItem('mtg-stack-order:42:category', JSON.stringify(o));"
            " localStorage.setItem('mtg.deckstats', JSON.stringify({chart: 'pie'}));"
            " localStorage.setItem('mtg.edhplay.steps', '1'); localStorage.setItem('mtg-other', 'x'); }",
            mine,
        )
        page.reload(wait_until="networkidle")
        assert page.eval_on_selector_all(".deckview .stack", "l => l.map(s => s.dataset.group)") == names
        page.wait_for_function("() => localStorage.getItem('mtg-stack-order:42:category') === null")
        assert server.db.get_pref("user-1", "stack:42:category") is None
        assert server.db.get_pref("user-1", "deckstats") == {"chart": "pie"}
        assert server.db.get_pref("user-1", "edhplay.steps") is True
        left = page.evaluate("() => Object.keys(localStorage).sort()")
        assert left == ["mtg-other"]
        assert errors == []
        browser.close()


def test_after_forget_a_devices_old_choices_are_never_shown(server: Server) -> None:
    """After "Forget my view choices" a device's pre-0.7.29 stats toggles aren't copied even on
    the first page it opens; they are dropped and removed from the browser."""
    from playwright.sync_api import sync_playwright

    from mtg_gateway.prefs import FORGOTTEN

    _chromium()
    sid = server.sign_in_and_link()
    server.db.set_pref("user-1", FORGOTTEN, True)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        page.evaluate("() => localStorage.setItem('mtg.deckstats', JSON.stringify({chart: 'pie'}))")
        page.reload(wait_until="networkidle")
        page.wait_for_function("() => localStorage.getItem('mtg.deckstats') === null")
        assert server.db.get_pref("user-1", "deckstats") is None
        assert errors == []
        browser.close()


ORDER = "() => [...document.querySelectorAll('.deckview .stack')].map(s => s.dataset.group)"


def _poll(page, js: str, timeout_ms: int = 6000) -> None:
    """Wait for a condition while a pointer or finger is held down (no clicks, no locators)."""
    for _ in range(timeout_ms // 50):
        if page.evaluate(js):
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timed out waiting for {js}")


def _touch(cdp, kind: str, x: float = 0, y: float = 0) -> None:
    points = [] if kind == "touchEnd" else [{"x": x, "y": y}]
    cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})


def test_a_held_card_drags_to_a_category_off_screen_by_touch(server: Server) -> None:
    """On a phone: a finger held near the bottom edge scrolls the page on under a held card, so a
    category off screen is reachable; Android's own long press does not open the card menu under
    a held card; the drop saves at once."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, has_touch=True, is_mobile=True)
        cdp = page.context.new_cdp_session(page)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        row = page.locator(".stack[data-group='Commander'] .row").first
        row.scroll_into_view_if_needed()
        b = row.bounding_box()
        x, y = b["x"] + 60, b["y"] + b["height"] / 2
        _touch(cdp, "touchStart", x, y)
        page.wait_for_timeout(450)
        page.evaluate(
            f"""() => document.elementFromPoint({x}, {y}).dispatchEvent(new MouseEvent('contextmenu',
                {{bubbles: true, cancelable: true, clientX: {x}, clientY: {y}}}))"""
        )
        assert page.locator(".ctxmenu").count() == 0
        for i in range(1, 11):
            _touch(cdp, "touchMove", x, y + (790 - y) * i / 10)
            page.wait_for_timeout(16)
        assert page.locator(".dragghost").count() == 1
        _poll(page, "document.querySelector(\"[data-group='Sorcery']\").getBoundingClientRect().top < 600")
        _touch(cdp, "touchMove", x, 450)
        page.wait_for_timeout(100)
        # on Sorcery's rows (the page may still be settling after the scroll: measure until the
        # finger really is over that stack)
        for _ in range(20):
            t = page.locator(".stack[data-group='Sorcery']").bounding_box()["y"]
            _touch(cdp, "touchMove", x, t + 40)
            page.wait_for_timeout(100)
            over = page.evaluate(
                f"() => (document.elementFromPoint({x}, {t + 40}).closest('.stack') || {{}}).dataset?.group"
            )
            if over == "Sorcery":
                break
        _shot(page, "card-drag-touch-390")
        # the drop is saved at once, as the card menu's Move to is
        with page.expect_request(EDIT_URL) as req:
            _touch(cdp, "touchEnd")
        assert json.loads(req.value.post_data)["changes"] == [
            {"action": "set_category", "card_name": "Aesi, Tyrant of Gyre Strait", "category": "Sorcery"}
        ]
        page.wait_for_timeout(100)
        assert page.locator(".dragghost").count() == 0
        moved = page.locator(".stack[data-group='Sorcery'] .row[data-card='Aesi, Tyrant of Gyre Strait']")
        assert moved.count() == 1
        assert errors == []
        browser.close()


# Every frame's scroll position and when each pointer or finger move arrived, on the page's clock.
SCROLL_LOG = """() => {
  window.__s = []; window.__ev = [];
  (function f() { __s.push([performance.now(), scrollY]); requestAnimationFrame(f); })();
  for (const k of ['pointermove', 'touchmove'])
    document.addEventListener(k, () => __ev.push(performance.now()), true);
}"""

# A screen that draws `hz` frames a second (headless Chromium draws 60).
FRAME_RATE = """(() => {
  const ms = 1000 / %s;
  window.requestAnimationFrame = cb => setTimeout(() => cb(performance.now()), ms);
  window.cancelAnimationFrame = id => clearTimeout(id);
})()"""


def _scroll_at(log: list, t: float) -> float:
    return [y for when, y in log if when <= t][-1]


def _edge_run(log: list, t_in: float, t_out: float, t_next: float) -> dict:
    """How the page scrolled while the pointer rested in the edge zone from t_in to t_out, and
    after it left until t_next: delay to the first step (ms), speed (px/s), the slowest and
    fastest 200 ms stretch against that speed, and how long it kept going after t_out (ms)."""
    y_in = _scroll_at(log, t_in)
    first = next(when for when, y in log if when > t_in and y != y_in)
    speed = (_scroll_at(log, t_out) - y_in) / (t_out - first) * 1000
    stretches = [
        (_scroll_at(log, t + 200) - _scroll_at(log, t)) / (speed / 5)
        for t in range(int(first) + 20, int(t_out) - 200, 100)
    ]
    pairs = zip(log[1:], log[:-1], strict=True)
    moved = [when for (when, y), (_w, prev) in pairs if t_out < when < t_next and y != prev]
    return {
        "delay": round(first - t_in),
        "speed": round(speed),
        "steady": (round(min(stretches), 2), round(max(stretches), 2)),
        "after": round(moved[-1] - t_out) if moved else 0,
    }


# How far ahead of the page's clock it is paused, in seconds. Playwright refuses to pause at a
# moment already past ("Cannot fast-forward to the past"), and on a busy machine 50 ms had passed
# by the time the pause arrived. Nothing the tests hold is pending when it pauses, so
# the jump ahead fires nothing they measure.
PAUSE_AHEAD_S = 2


def _pause_clock(page) -> None:
    """Pause the page's clock (installed with page.clock.install) a little ahead of now."""
    page.clock.pause_at(page.evaluate("Date.now()") / 1000 + PAUSE_AHEAD_S)


@pytest.mark.parametrize(("how", "hz"), [("card", None), ("card", 120), ("card", 30)])
def test_edge_auto_scroll_is_measured(server: Server, how: str, hz: int | None) -> None:
    """0.7.22's edge scrolling while something is dragged, measured: a held card by finger, and
    on a 120 Hz and a 30 Hz screen (the stacks' own drag went with owner decision C4). Resting
    near the bottom edge, the page starts on the next frame, scrolls at a steady speed (about
    1,200 px/s at the very edge, half that halfway into the zone) whatever the frame rate, and
    stops as soon as the pointer leaves the zone or is let go. The page's clock is
    Playwright's, paused while measuring and moved on by the test, so the frames come on time
    however busy the machine running the test is (a busy machine used to fail it)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    views = ["text"] if how == "card" or hz else ["text", "table", "brewer", "stacks", "scroll", "grid"]
    with sync_playwright() as p:
        touch = {"has_touch": True, "is_mobile": True} if how != "mouse" else {}
        browser, page, errors = _page(p, server, sid, 390, **touch)
        if hz:
            page.context.add_init_script(FRAME_RATE % hz)
        page.clock.install()  # runs as usual until paused for a measurement
        cdp = page.context.new_cdp_session(page)

        def move(x: float, y: float) -> float:
            """Move there; the page's clock just before the move (the move's own time is the
            first event after it)."""
            t = page.evaluate("performance.now()")
            page.mouse.move(x, y) if how == "mouse" else _touch(cdp, "touchMove", x, y)
            return t

        for view in views:
            page.clock.resume()
            page.goto(f"{server.base}/decks/42?view={view}", wait_until="networkidle")
            h = page.evaluate("innerHeight")
            zone = min(96, h * 0.15)
            what = page.locator(".stack .row" if how == "card" else ".stack .stackgrip").first
            what.scroll_into_view_if_needed()
            b = what.bounding_box()
            x, y = b["x"] + min(b["width"] / 2, 60), b["y"] + b["height"] / 2
            _pause_clock(page)  # the test moves it on
            if how == "mouse":
                page.mouse.move(x, y)
                page.mouse.down()
            else:
                _touch(cdp, "touchStart", x, y)
                if how == "card":
                    page.clock.run_for(450)  # held: the card is picked up
            move(x + 4, y + 10)
            move(x + 8, h / 2)
            page.clock.run_for(150)
            page.evaluate(SCROLL_LOG)
            # at the very edge, out of the zone, halfway into it, out, and let go at the edge
            times = []
            for at, wait in [(h - 8, 600), (h / 2, 250), (h - zone / 2, 600), (h / 2, 250), (h - 8, 250)]:
                times.append(move(x + 8, at))
                page.clock.run_for(wait)
            t_up = page.evaluate("performance.now()")
            page.mouse.up() if how == "mouse" else _touch(cdp, "touchEnd")
            page.clock.run_for(400)
            log, events = page.evaluate("__s"), page.evaluate("__ev")
            times = [next(e for e in events if e >= t) for t in times]
            edge = _edge_run(log, times[0], times[1], times[2])
            half = _edge_run(log, times[2], times[3], times[4])
            settled = {y for t, y in log if t > t_up + 50}
            label = f"{how} at {hz or 'native'} Hz, {view} view: edge {edge}, halfway {half}"
            print(label)
            assert edge["delay"] < 100 and half["delay"] < 100, label  # starts on the next frames
            assert 900 < edge["speed"] < 1500, label
            assert 0.35 < half["speed"] / edge["speed"] < 0.65, label  # faster the closer to the edge
            assert edge["steady"][0] > 0.6 and edge["steady"][1] < 1.4, label  # not in jumps
            assert edge["after"] < 100 and half["after"] < 100, label  # stops on leaving the zone
            assert len(settled) == 1, label  # and once let go
            assert page.locator(".dragghost").count() == 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme", "view"), [(390, "dark", "text"), (1366, "light", "grid")])
def test_bulk_move_finish_and_remove(server: Server, width: int, scheme: str, view: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    picks = ["Cultivate", "Kodama's Reach"]
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42?view={view}", wait_until="networkidle")
        sel = ".deckview .c, .deckview .row"
        page.locator(".bulkbar button", has_text="Select cards").click()
        tools = page.locator(".bulkbar .bulktools")
        assert tools.is_visible()
        assert page.locator(".bulkbar select").first.is_disabled()
        for name in picks:
            card = page.locator(f'.deckview [data-card="{name}"]').first
            card.scroll_into_view_if_needed()
            card.click()
            assert card.get_attribute("aria-pressed") == "true"
        assert page.locator(".cardview.open").count() == 0  # a tap selects; the viewer stays shut
        assert page.locator(".bulkbar .count").inner_text() == "2 cards selected"
        # the themed dropdown shows the select's own first entry, and is enabled with cards picked
        move_btn = page.locator(".bulkbar button[aria-label^='Move the selected']")
        assert "Move to" in move_btn.inner_text() and move_btn.is_enabled()
        page.locator(".bulkbar").scroll_into_view_if_needed()
        _shot(page, f"deck-bulk-{view}-{width}-{scheme}")

        # move both in one save, with one Undo
        with page.expect_response(EDIT_URL) as req:
            page.locator(".bulkbar select[aria-label^='Move']").select_option("Maybeboard")
        changes = json.loads(req.value.request.post_data)["changes"]
        assert sorted(c["card_name"] for c in changes) == sorted(picks)
        assert all(c["action"] == "set_category" and c["category"] == "Maybeboard" for c in changes)
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert all(_rows(server, n)[0]["categories"] == ["Maybeboard"] for n in picks)
        assert page.locator(".deckview .picked").count() == 0 and not tools.is_visible()
        with page.expect_response(EDIT_URL) as req2:
            page.locator(".deck-toast button", has_text="Undo").click()
        assert len(json.loads(req2.value.request.post_data)["changes"]) == 2
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert all(_rows(server, n)[0]["categories"] != ["Maybeboard"] for n in picks)

        # finish: one change per name; the badge redraws from the answer
        page.locator(".bulkbar button", has_text="Select cards").click()
        for name in picks:
            page.locator(f'.deckview [data-card="{name}"]').first.click()
        with page.expect_response(EDIT_URL) as req3:
            page.locator(".bulkbar select[aria-label^='Set the finish']").select_option("foil")
        assert json.loads(req3.value.request.post_data)["changes"] == [
            {"action": "set_finish", "card_name": n, "finish": "foil"} for n in picks
        ]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert all(r["modifier"] == "Foil" for n in picks for r in _rows(server, n))
        first = page.locator(f'.deckview [data-card="{picks[0]}"]').first
        # drawn from the answer, possibly after the Saved message (see the remove step below)
        page.wait_for_function(
            "(name) => [...document.querySelectorAll('.deckview [data-card]')]"
            ".some((e) => e.dataset.card === name && e.dataset.finish === 'Foil')",
            arg=picks[0],
            timeout=8000,
        )
        assert first.get_attribute("data-finish") == "Foil"

        # remove, with Escape leaving selection first to show it clears
        page.locator(".bulkbar button", has_text="Select cards").click()
        page.locator(f'.deckview [data-card="{picks[0]}"]').first.click()
        page.keyboard.press("Escape")
        assert page.locator(".deckview .picked").count() == 0
        page.locator(".bulkbar button", has_text="Select cards").click()
        for name in picks:
            page.locator(f'.deckview [data-card="{name}"]').first.click()
        before = page.locator(sel).count()
        with page.expect_response(EDIT_URL):
            page.locator(".bulkbar button", has_text="Remove").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert all(not _rows(server, n) for n in picks)
        # the rows can be drawn after the Saved message ("Saved. Refreshing…" when the re-read
        # was stale, redrawn 1.5 s later in deck.js), so wait for them rather than count at once
        page.wait_for_function(
            "([sel, n]) => document.querySelectorAll(sel).length === n", arg=[sel, before - 2], timeout=8000
        )
        assert page.locator(sel).count() == before - 2
        assert _overflow(page) == 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1366, "light")])
def test_colour_tag_from_the_card_menu_and_undo(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        row.scroll_into_view_if_needed()
        row.click(button="right")
        page.locator(".ctxmenu [role=menuitem]", has_text="Color tag").click()
        form = page.locator(".bulkbar .tagform")
        assert form.is_visible() and row.get_attribute("aria-pressed") == "true"
        page.locator(".bulkbar input[aria-label='Tag name']").fill("Have")
        page.locator(".bulkbar input[aria-label='Tag color']").evaluate(
            "e => { e.value = '#2ccce4'; e.dispatchEvent(new Event('input')); }"
        )
        _shot(page, f"deck-tag-form-{width}-{scheme}")
        with page.expect_response(EDIT_URL) as r:
            page.locator(".bulkbar button", has_text="Apply tag").click()
        assert json.loads(r.value.request.post_data)["changes"] == [
            {"action": "set_label", "card_name": "Cultivate", "label": "Have", "color": "#2ccce4"}
        ]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert _rows(server, "Cultivate")[0]["label"] == "Have,#2ccce4"
        dot = row.locator(".tagdot")
        assert dot.get_attribute("title") == "Tag: Have"
        assert dot.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(44, 204, 228)"
        assert _overflow(page) == 0
        _shot(page, f"deck-tagged-{width}-{scheme}")
        # the page after a reload draws the same tag from Archidekt's row
        page.reload(wait_until="networkidle")
        dot = page.locator(".deckview .row[data-card='Cultivate'] .tagdot")
        assert dot.get_attribute("title") == "Tag: Have"
        page.locator(".bulkbar button", has_text="Select cards").click()
        page.locator(".deckview .row[data-card='Cultivate']").first.click()
        page.locator(".bulkbar button", has_text="Color tag").click()
        with page.expect_response(EDIT_URL):
            page.locator(".bulkbar button", has_text="Take tag off").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert _rows(server, "Cultivate")[0].get("label") == ",#656565"  # the site's empty tag
        assert page.locator(".deckview .row[data-card='Cultivate'] .tagdot").count() == 0
        # Undo puts the tag back
        with page.expect_response(EDIT_URL) as r2:
            page.locator(".deck-toast button", has_text="Undo").click()
        assert json.loads(r2.value.request.post_data)["changes"][0]["label"] == "Have"
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert _rows(server, "Cultivate")[0]["label"] == "Have,#2ccce4"
        assert errors == []
        browser.close()


def test_bulk_edge_cases_at_320(server: Server) -> None:
    """320 px with a long category name: nothing scrolls sideways. A card on the maybeboard is
    left out of Set finish (the gateway refinishes the deck itself), and removing a card whose
    name has two rows in that zone offers no Undo (one could not put both rows back)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    long_cat = "Card advantage engines and other value <img src=x onerror=alert(1)>"
    for row in server.ark.decks[42]["cards"]:
        if row["card"]["oracleCard"]["name"] == "Cultivate":
            row["categories"] = [long_cat]
    server.ark.decks[42]["categories"].append({"name": long_cat, "isPremier": False, "includedInDeck": True})
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 320, color_scheme="dark")
        page.on("dialog", lambda d: errors.append("dialog: " + d.message))
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        page.locator(".bulkbar button", has_text="Select cards").click()
        side = page.locator(".deckview .row[data-zone='side'][data-card='Sol Ring']").first
        side.click()
        assert _overflow(page) == 0
        _shot(page, "deck-bulk-320-long-category")
        page.locator(".bulkbar select[aria-label^='Set the finish']").select_option("foil")
        assert "not on the maybeboard" in page.locator(".bulkbar .status").inner_text()
        assert page.locator(".deck-toast").count() == 0  # nothing was sent
        page.locator(".bulkbar button", has_text="Cancel").click()
        # two main rows of Sol Ring: the remove goes through, without an Undo
        page.locator(".bulkbar button", has_text="Select cards").click()
        page.locator(".deckview .row[data-zone='main'][data-card='Sol Ring']").first.click()
        page.evaluate(
            "() => { const r = document.querySelector(\".row[data-zone='main'][data-card='Sol Ring']\");"
            " const twin = r.cloneNode(true); twin.classList.remove('picked'); r.after(twin); }"
        )
        with page.expect_response(EDIT_URL):
            page.locator(".bulkbar button", has_text="Remove").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert page.locator(".deck-toast button", has_text="Undo").count() == 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(320, "dark"), (1366, "light")])
def test_brewer_view_and_custom_mana_value(server: Server, width: int, scheme: str) -> None:
    """0.7.20: the Brewer view (picture, name and cost, type line, rules text) and the card
    menu's Mana value form, which saves Archidekt's custom mana value and clears it again."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42?view=brewer", wait_until="networkidle")
        assert page.locator(".deckview.brewer").count() == 1
        row = page.locator(".deckview.brewer .row.brew[data-card='Cultivate']").first
        assert "Sorcery" in row.locator(".ty").inner_text()
        assert _overflow(page) == 0
        _shot(page, f"deck-brewer-{width}-{scheme}")
        row.scroll_into_view_if_needed()
        row.click(button="right")
        page.locator(".ctxmenu [role=menuitem]", has_text="Mana value").click()
        assert page.locator(".ctxmenu .mvform button", has_text="Clear").count() == 0  # nothing to clear
        page.locator(".ctxmenu .mvform input").fill("25")
        page.locator(".ctxmenu .mvform button", has_text="Save").click()
        assert "0 to 15" in page.locator(".ctxmenu .mvform .status").inner_text()
        page.locator(".ctxmenu .mvform input").fill("2")
        assert page.locator(".ctxmenu .mvform .status").inner_text() == ""  # typing clears the error
        _shot(page, f"deck-mv-form-{width}-{scheme}")
        with page.expect_response(EDIT_URL) as r:
            page.locator(".ctxmenu .mvform button", has_text="Save").click()
        assert json.loads(r.value.request.post_data)["changes"] == [
            {"action": "set_mana_value", "card_name": "Cultivate", "mana_value": 2, "zone": "main"}
        ]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert _rows(server, "Cultivate")[0]["customCmc"] == 2
        assert row.get_attribute("data-mv") == "2"
        # Undo takes the override off again
        with page.expect_response(EDIT_URL) as r2:
            page.locator(".deck-toast button", has_text="Undo").click()
        assert json.loads(r2.value.request.post_data)["changes"][0]["mana_value"] is None
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert _rows(server, "Cultivate")[0].get("customCmc") is None
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1280, "light")])
def test_mana_value_saves_in_the_background(server: Server, width: int, scheme: str) -> None:
    """0.7.27: Save closes the Mana value form at once and the new value is on the card
    while Archidekt is still answering, with a Saving note and then Saved; a save Archidekt refuses
    puts the old value back and says why."""
    import threading

    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    held = threading.Event()
    server.ark.hold_card_writes = held
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42?view=brewer", wait_until="networkidle")
        row = page.locator(".deckview.brewer .row.brew[data-card='Cultivate']").first
        row.scroll_into_view_if_needed()

        def save(value: str) -> None:
            row.click(button="right")
            page.locator(".ctxmenu [role=menuitem]", has_text="Mana value").click()
            page.locator(".ctxmenu .mvform input").fill(value)
            page.locator(".ctxmenu .mvform button", has_text="Save").click()

        try:
            save("3")
            # the form is gone and the card has its new value before Archidekt answers
            assert page.locator(".ctxmenu").count() == 0
            assert row.get_attribute("data-mv") == "3"
            page.locator(".deck-toast .msg", has_text="Saving").wait_for(timeout=2000)
            _shot(page, f"t159-after-saving-{scheme}-{'phone' if width < 500 else 'desktop'}")
        finally:
            held.set()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert _rows(server, "Cultivate")[0]["customCmc"] == 3
        assert row.get_attribute("data-mv") == "3"
        _shot(page, f"t159-after-saved-{scheme}-{'phone' if width < 500 else 'desktop'}")

        # Archidekt refuses the next one: the value goes back to 3 and the error is shown
        held.clear()
        server.ark.fail_card_writes = True
        try:
            save("5")
            assert page.locator(".ctxmenu").count() == 0
            assert row.get_attribute("data-mv") == "5"
        finally:
            held.set()
        err = page.locator(".deck-toast.error .msg")
        err.wait_for(timeout=8000)
        text = err.inner_text()
        assert text.startswith("Mana value not saved:") and text.endswith("Cultivate is back to 3."), text
        assert row.get_attribute("data-mv") == "3"
        assert _rows(server, "Cultivate")[0]["customCmc"] == 3
        # the form opens on the value the deck really has
        row.click(button="right")
        page.locator(".ctxmenu [role=menuitem]", has_text="Mana value").click()
        assert page.locator(".ctxmenu .mvform input").input_value() == "3"
        assert errors == []
        browser.close()


# 0.7.27: the card menu's Quantity, Move to and Remove from deck save in the background, as
# Mana value does: the change is on the page at once, the menu never fades, a Saving note shows
# until Archidekt answers (then Saved with Undo), and a save Archidekt refuses puts the card back
# as it was with a red note saying why. Shots go to MTG_SHOTS as t159-menu-*.
MENU_SIZES = [(390, 844, "dark"), (1280, 800, "light"), (673, 841, "light")]
ORDER_JS = "() => [...document.querySelectorAll('.deckview .row')].map(r => r.dataset.card)"


def _menu_page(p, server: Server, width: int, height: int, scheme: str, *, clock: bool = False):
    """``clock``: the page runs on Playwright's clock, so a test can hold the page's timers (the
    400 ms that gathers quantity steps into one save) while it clicks, however slow the clicks
    are on a busy machine."""
    sid = server.sign_in_and_link()
    browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
    if clock:
        page.clock.install()  # runs as usual until _hold_timers
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
    return browser, page, errors


def _hold_timers(page) -> None:
    """Stop the page's clock: no timer fires until the test moves it on (page.clock.run_for) or
    lets it run again (page.clock.resume)."""
    _pause_clock(page)


def _menu_shot(page, what: str, width: int, scheme: str) -> None:
    device = "phone" if width < 500 else "fold" if width < 1000 else "desktop"
    _shot(page, f"t159-menu-{what}-{scheme}-{device}")


def _open_menu(page, row):
    row.scroll_into_view_if_needed()
    row.click(button="right")
    menu = page.locator(".ctxmenu[role=menu]")
    menu.wait_for(timeout=3000)
    return menu


def _held(server: Server):
    import threading

    held = threading.Event()
    server.ark.hold_card_writes = held
    return held


def _error(page) -> str:
    err = page.locator(".deck-toast.error .msg")
    err.wait_for(timeout=8000)
    return err.inner_text()


@pytest.mark.parametrize(("width", "height", "scheme"), MENU_SIZES)
def test_quantity_steps_save_in_the_background(server: Server, width: int, height: int, scheme: str) -> None:
    """Quantity + and - change the count on the card and in the menu at once; the menu stays open
    and usable for more steps, the steps of a quick burst go to Archidekt as one save of the final
    count, a step made while that save is out follows it, and the count never jumps back while
    saving. Refused: the count Archidekt has comes back, with the reason."""
    from playwright.sync_api import sync_playwright

    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _menu_page(p, server, width, height, scheme, clock=True)
        sent = _edits(page)
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        held = _held(server)
        try:
            menu = _open_menu(page, row)
            # Move to's list stays folded until it is asked for (it used to show at all times)
            assert menu.locator(".sub").is_hidden()
            plus = menu.locator(".qtyrow .step").nth(1)
            # a quick burst: the page's 400 ms does not run out between these presses, however
            # long the presses take here (a busy run sent [2] and then 4)
            _hold_timers(page)
            plus.click()
            # on the page at once; the menu stays open, not faded, for more steps
            assert menu.locator(".qtyrow .n").inner_text() == "2"
            assert row.get_attribute("data-qty") == "2"
            assert menu.evaluate("m => getComputedStyle(m).opacity") == "1"
            plus.click()
            plus.click()
            assert menu.locator(".qtyrow .n").inner_text() == "4" and row.get_attribute("data-qty") == "4"
            page.locator(".deck-toast .msg", has_text="Saving Cultivate: 4 copies").wait_for(timeout=3000)
            assert sent == []  # nothing until the presses pause
            with page.expect_request(EDIT_URL):
                page.clock.run_for(400)  # the presses paused: one save goes out (held by Archidekt)
            page.clock.resume()
            assert [ch[0]["quantity"] for ch in sent] == [4]
            _menu_shot(page, "quantity-saving", width, scheme)
            # a step while that save is out waits for it; the count stays where the person put it
            plus.click()
            assert menu.locator(".qtyrow .n").inner_text() == "5"
        finally:
            held.set()
        # the first save lands, then the step made meanwhile goes out on its own; the count stays 5
        for _ in range(160):
            if len(sent) == 2 and server.quantity("Cultivate") == 5:
                break
            assert row.get_attribute("data-qty") == "5"
            page.wait_for_timeout(50)
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        page.wait_for_timeout(300)
        assert [ch[0]["quantity"] for ch in sent] == [4, 5], sent  # in order, never one back
        assert server.quantity("Cultivate") == 5 and row.get_attribute("data-qty") == "5"
        assert menu.locator(".qtyrow .n").inner_text() == "5"
        # Undo takes back the last save
        page.keyboard.press("Escape")
        page.locator(".deck-toast button", has_text="Undo").click()
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert sent[-1] == [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 4}]
        assert row.get_attribute("data-qty") == "4" and server.quantity("Cultivate") == 4

        # Archidekt refuses: the count it has comes back, in the menu too, with the reason
        server.ark.fail_card_writes = True
        menu = _open_menu(page, row)
        menu.locator(".qtyrow .step").nth(0).click()
        assert row.get_attribute("data-qty") == "3"
        text = _error(page)
        assert text.startswith("Quantity not saved:"), text
        assert text.endswith("Cultivate is back to 4 copies."), text
        assert row.get_attribute("data-qty") == "4" and menu.locator(".qtyrow .n").inner_text() == "4"
        assert server.quantity("Cultivate") == 4
        _menu_shot(page, "quantity-error", width, scheme)
        assert _overflow(page) == 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "height", "scheme"), MENU_SIZES[:2])
def test_move_to_saves_in_the_background(server: Server, width: int, height: int, scheme: str) -> None:
    """Move to closes the menu and files the card in its new stack at once; refused, it goes back."""
    from playwright.sync_api import sync_playwright

    _file_under(server, "Rampant Growth", ["Lands"])
    _file_under(server, "Cultivate", ["Ramp"])
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _menu_page(p, server, width, height, scheme)
        sent = _edits(page)
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        held = _held(server)
        try:
            menu = _open_menu(page, row)
            menu.locator(".more").click()
            menu.locator(".sub button", has_text="Lands").click()
            assert page.locator(".ctxmenu").count() == 0
            assert page.locator(".stack[data-group='Lands'] .row[data-card='Cultivate']").count() == 1
            assert page.locator(".stack[data-group='Ramp']").count() == 0  # emptied, so gone
            page.locator(".deck-toast .msg", has_text="Moving Cultivate to Lands").wait_for(timeout=3000)
            row.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            _menu_shot(page, "move-saving", width, scheme)
        finally:
            held.set()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert sent == [[{"action": "set_category", "card_name": "Cultivate", "category": "Lands"}]]
        assert _cats(server, "Cultivate") == ["Lands"]
        assert page.locator(".deck-toast button", has_text="Undo").count() == 1

        # refused: the card goes back to Lands, where Archidekt still has it
        server.ark.fail_card_writes = True
        before = page.evaluate(ORDER_JS)
        menu = _open_menu(page, row)
        menu.locator(".more").click()
        menu.locator(".sub button", has_text="Ramp").click()
        text = _error(page)
        assert text.startswith("Move not saved:") and text.endswith("Cultivate is back in Lands."), text
        assert page.locator(".stack[data-group='Lands'] .row[data-card='Cultivate']").count() == 1
        assert page.locator(".stack[data-group='Ramp']").count() == 0
        assert page.evaluate(ORDER_JS) == before
        assert _cats(server, "Cultivate") == ["Lands"]
        row.scroll_into_view_if_needed()
        _menu_shot(page, "move-error", width, scheme)
        assert _overflow(page) == 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "height", "scheme"), MENU_SIZES[:2])
def test_remove_saves_in_the_background(server: Server, width: int, height: int, scheme: str) -> None:
    """Remove from deck closes the menu and takes the card off at once; refused, it comes back in
    its place."""
    from playwright.sync_api import sync_playwright

    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _menu_page(p, server, width, height, scheme)
        sent = _edits(page)
        rows = page.locator(".deckview .row[data-card='Cultivate']")
        stack_of = (
            "() => document.querySelector('.deckview .row[data-card=\"Cultivate\"]')"
            ".closest('.stack').dataset.group"
        )
        home = page.evaluate(stack_of)
        assert home  # filed under no category of its own, it sits in a type's stack
        held = _held(server)
        try:
            menu = _open_menu(page, rows.first)
            menu.locator("[role=menuitem]", has_text="Remove from deck").click()
            assert page.locator(".ctxmenu").count() == 0
            assert rows.count() == 0  # off the page before Archidekt answers
            page.locator(".deck-toast .msg", has_text="Removing Cultivate").wait_for(timeout=3000)
            page.wait_for_timeout(300)
            _menu_shot(page, "remove-saving", width, scheme)
        finally:
            held.set()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert sent == [[{"action": "remove", "card_name": "Cultivate"}]]
        assert server.quantity("Cultivate") == 0 and rows.count() == 0
        # Undo brings it back
        page.locator(".deck-toast button", has_text="Undo").click()
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert rows.count() == 1 and server.quantity("Cultivate") == 1
        # back in the stack it was in (it used to come back in a new stack with no name)
        assert page.evaluate(stack_of) == home and page.locator(".stack[data-group='']").count() == 0

        # refused: the card comes back where it was
        server.ark.fail_card_writes = True
        before = page.evaluate(ORDER_JS)
        menu = _open_menu(page, rows.first)
        menu.locator("[role=menuitem]", has_text="Remove from deck").click()
        text = _error(page)
        assert text.startswith("Removal not saved:") and text.endswith("Cultivate is back in the deck."), text
        assert page.evaluate(ORDER_JS) == before
        assert server.quantity("Cultivate") == 1
        rows.first.scroll_into_view_if_needed()
        _menu_shot(page, "remove-error", width, scheme)
        assert _overflow(page) == 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "height", "scheme"), MENU_SIZES[:2])
def test_mana_value_grouping_restacks_at_once(server: Server, width: int, height: int, scheme: str) -> None:
    """0.7.27: grouped by Mana value, a card whose mana value is changed moves to its new stack at
    once (a new stack in its place in the order when there is none), and back on an error, an Undo
    or a Clear; the Table view's MV column shows the new value too. Both used to stay as they were
    until a reload."""
    from playwright.sync_api import sync_playwright

    stacks = (
        "() => Object.fromEntries([...document.querySelectorAll('.deckview .stack')].map(s =>"
        " [s.dataset.group, [...s.querySelectorAll('.row')].map(r => r.dataset.card)]))"
    )
    order = "() => [...document.querySelectorAll('.deckview .stack')].map(s => s.dataset.group)"
    _chromium()
    with sync_playwright() as p:
        sid = server.sign_in_and_link()
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(f"{server.base}/decks/42?view=table&group=mv", wait_until="networkidle")
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        cell = row.locator(".mv")

        def save(value: str) -> None:
            _open_menu(page, row)
            page.locator(".ctxmenu [role=menuitem]", has_text="Mana value").click()
            page.locator(".ctxmenu .mvform input").fill(value)
            page.locator(".ctxmenu .mvform button", has_text="Save").click()

        held = _held(server)
        try:
            save("12")
            now = page.evaluate(stacks)
            assert "Cultivate" in now["12 mana value"] and "Cultivate" not in now["3 mana value"]
            assert page.evaluate(order)[-3:] == ["12 mana value", "Land", "No mana value"]
            assert cell.inner_text() == "12"
            head = page.locator(".stack[data-group='12 mana value'] .stackhead")
            assert head.locator("h2 .title").inner_text() == "12 mana value"
            assert head.locator(".meta").inner_text() == "Qty: 1"
            row.scroll_into_view_if_needed()
            _menu_shot(page, "mv-group-saving", width, scheme)
        finally:
            held.set()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert "Cultivate" in page.evaluate(stacks)["12 mana value"]
        # Undo: back in 3 mana value, and the stack it made is gone again
        page.locator(".deck-toast button", has_text="Undo").click()
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        now = page.evaluate(stacks)
        assert "Cultivate" in now["3 mana value"] and "12 mana value" not in now
        assert cell.inner_text() == "3"
        # refused: back where it was
        server.ark.fail_card_writes = True
        held = _held(server)  # the refusal waits until the moved card is checked
        try:
            save("4")
            assert "Cultivate" in page.evaluate(stacks)["4 mana value"]
        finally:
            held.set()
        assert _error(page).endswith("Cultivate is back to its own mana value.")
        assert "Cultivate" in page.evaluate(stacks)["3 mana value"] and cell.inner_text() == "3"
        # Clear puts it back under its printed mana value
        server.ark.fail_card_writes = False
        save("5")
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert "Cultivate" in page.evaluate(stacks)["5 mana value"]
        _open_menu(page, row)
        page.locator(".ctxmenu [role=menuitem]", has_text="Mana value").click()
        page.locator(".ctxmenu .mvform button", has_text="Clear").click()
        assert "Cultivate" in page.evaluate(stacks)["3 mana value"] and cell.inner_text() == "3"
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert _rows(server, "Cultivate")[0].get("customCmc") is None
        assert _overflow(page) == 0
        assert errors == []
        browser.close()


# 0.7.27: the card menu's background saves never leave a stale note, and a card
# whose edits Archidekt refused goes back to what Archidekt really has.
STACK_OF_JS = (
    "n => document.querySelector(`.deckview .row[data-card=\"${n}\"]`)?.closest('.stack')?.dataset.group"
)


# every error note the page shows, in order (a later note replaces an earlier one on screen)
ERRORS_SEEN_JS = """() => { window.__errs = [];
  new MutationObserver(() => { const m = document.querySelector('.deck-toast.error .msg');
    if (m && window.__errs[window.__errs.length - 1] !== m.textContent) window.__errs.push(m.textContent); })
  .observe(document.body, {childList: true, subtree: true, characterData: true}); }"""


def _settle(page, ms: int = 1200) -> None:
    """Wait for the Saving note to give way to an answer, then make sure the end state holds: a
    later save of the same card (the old bug) would change it within this time."""
    page.wait_for_function(
        "() => !document.querySelector('.deck-toast .msg')?.textContent.startsWith('Saving')", timeout=8000
    )
    page.wait_for_timeout(ms)


@pytest.mark.parametrize(("width", "height", "scheme"), MENU_SIZES[:2])
def test_plus_then_minus_leaves_no_saving_note(server: Server, width: int, height: int, scheme: str) -> None:
    """+ then - inside the 400 ms window sends nothing, and the Saving note goes away (it used
    to stay on screen for good, saying a save was running)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _menu_page(p, server, width, height, scheme, clock=True)
        sent = _edits(page)
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        menu = _open_menu(page, row)
        _hold_timers(page)  # both presses inside the 400 ms, however slow they are here
        menu.locator(".qtyrow .step").nth(1).click()
        menu.locator(".qtyrow .step").nth(0).click()
        assert page.locator(".deck-toast .msg", has_text="Saving Cultivate: 1 copy").count() == 1
        page.clock.resume()
        page.locator(".deck-toast").wait_for(state="detached", timeout=3000)
        page.wait_for_timeout(600)
        assert sent == [] and row.get_attribute("data-qty") == "1"
        assert page.locator(".deck-toast").count() == 0
        _menu_shot(page, "m6-plus-minus", width, scheme)
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "height", "scheme"), MENU_SIZES[:2])
def test_two_refused_moves_put_the_card_back_where_archidekt_has_it(
    server: Server, width: int, height: int, scheme: str
) -> None:
    """Ramp -> Lands -> Draw, both refused: the card is back in Ramp (it used to stay in Lands,
    an unsaved state, with "back in Lands"). Then a move held while the card is removed, both
    refused: back in Ramp, not where the unsaved move put it."""
    from playwright.sync_api import sync_playwright

    _file_under(server, "Rampant Growth", ["Lands"])
    _file_under(server, "Explore", ["Draw"])
    _file_under(server, "Cultivate", ["Ramp"])
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _menu_page(p, server, width, height, scheme)
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        before = page.evaluate(ORDER_JS)

        def move(to: str) -> None:
            menu = _open_menu(page, row)
            menu.locator(".more").click()
            menu.locator(".sub button", has_text=to).click()

        held = _held(server)
        server.ark.fail_card_writes = True
        try:
            move("Lands")
            move("Draw")
            assert page.evaluate(STACK_OF_JS, "Cultivate") == "Draw"
        finally:
            held.set()
        _settle(page)
        text = _error(page)
        # the note says the later move was not sent
        assert text.startswith("Move not saved:"), text
        assert text.endswith("Cultivate is back in Ramp. Your later move of it was not sent."), text
        assert page.evaluate(STACK_OF_JS, "Cultivate") == "Ramp"
        assert page.evaluate(ORDER_JS) == before
        assert _cats(server, "Cultivate") == ["Ramp"]
        row.scroll_into_view_if_needed()
        _menu_shot(page, "m7-move-twice-error", width, scheme)

        # a move, then Remove from deck while it is held: both refused, back in Ramp; the removal
        # is still sent, so the move's note says nothing about a later edit not sent
        page.evaluate(ERRORS_SEEN_JS)
        held.clear()
        try:
            move("Lands")
            menu = _open_menu(page, row)
            menu.locator("[role=menuitem]", has_text="Remove from deck").click()
            assert page.locator(".deckview .row[data-card='Cultivate']").count() == 0
        finally:
            held.set()
        _settle(page)
        text = _error(page)
        assert text.startswith("Removal not saved:") and text.endswith("Cultivate is back in the deck."), text
        seen = page.evaluate("() => window.__errs")
        move_note = seen[-2]
        assert move_note.startswith("Move not saved:") and move_note.endswith("Cultivate is back in Ramp."), (
            seen
        )
        assert page.evaluate(STACK_OF_JS, "Cultivate") == "Ramp"
        assert page.evaluate(ORDER_JS) == before
        assert page.locator(".deckview .row[data-card='Cultivate']").get_attribute("data-cat") == "Ramp"
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1280, "light")])
def test_two_refused_mana_values_put_back_the_value_archidekt_has(
    server: Server, width: int, scheme: str
) -> None:
    """Mana value 5 then 7, both refused: the card has its own mana value again and the note
    says so (it used to show 5, "back to 5", a value Archidekt never had)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42?view=brewer", wait_until="networkidle")
        row = page.locator(".deckview.brewer .row.brew[data-card='Cultivate']").first
        row.scroll_into_view_if_needed()

        def save(value: str) -> None:
            row.click(button="right")
            page.locator(".ctxmenu [role=menuitem]", has_text="Mana value").click()
            page.locator(".ctxmenu .mvform input").fill(value)
            page.locator(".ctxmenu .mvform button", has_text="Save").click()

        held = _held(server)
        server.ark.fail_card_writes = True
        try:
            save("5")
            save("7")
            assert row.get_attribute("data-mv") == "7"
        finally:
            held.set()
        _settle(page)
        text = _error(page)
        assert text.startswith("Mana value not saved:"), text
        # the note says the later value was not sent
        assert text.endswith(
            "Cultivate is back to its own mana value. Your later mana value for it was not sent."
        ), text
        assert row.get_attribute("data-mv") is None
        assert _rows(server, "Cultivate")[0].get("customCmc") is None
        _shot(page, f"m7-mv-twice-error-{scheme}-{'phone' if width < 500 else 'desktop'}")

        # three in a row, the first refused: both later ones were dropped, and the note says so
        page.locator(".deck-toast .close").click()
        page.locator(".deck-toast").wait_for(state="detached", timeout=3000)
        held.clear()
        try:
            save("5")
            save("6")
            save("7")
        finally:
            held.set()
        _settle(page)
        text = _error(page)
        assert text.endswith(
            "Cultivate is back to its own mana value. Your later mana values for it were not sent."
        ), text
        assert row.get_attribute("data-mv") is None
        assert errors == []
        browser.close()
