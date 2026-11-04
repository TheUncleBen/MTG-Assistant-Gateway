"""The 0.7.46 final polish, measured in a browser on the overflow sweep's hostile data (findings
batch 1, B and C): the deck banner's Like count centred, the list's names in one column, no lone
grip icon, Undo and Redo drawn as buttons, Bar and Pie joined with Lands only apart, long binder
tags one line with their name as hover text, binder tiles in step across a row, the watchlist's
alert pills one line, the report's way-back capsules 8 px apart, the editor's category chips
left-aligned blocks, facts lines whose dots never dangle at a line end, grey zero badges, Clear
filters hidden until a filter is set, one deck count meaning, a muted "unknown" price, and the
Move to a folder box's gap before its field. Phone 412 (touch) and desktop 1280 (mouse)."""

from __future__ import annotations

import re

import pytest

from .test_editor_browser import PNG
from .test_overflow_sweep_browser import MEASURE, _seed, server  # noqa: F401
from .test_printing_picker_browser import Server
from .test_scan_browser import _chromium_path

pytest.importorskip("playwright")

# [left, top, right, bottom, height] of an element, rounded
RECT = (
    "e => { const r = e.getBoundingClientRect();"
    " return [r.left, r.top, r.right, r.bottom, r.height].map(Math.round); }"
)
# each child of a facts line: its rect, whether a dot is drawn before it, and the line's left edge
DOTS = """e => [...e.children].map(c => { const r = c.getBoundingClientRect(), p = e.getBoundingClientRect();
  const b = getComputedStyle(c, '::before'), dl = r.left + (parseFloat(b.left) || 0);
  return { left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top),
    line: Math.round(p.left), dot: b.content.includes('·'),
    dotLeft: Math.round(dl), dotRight: Math.round(dl + (parseFloat(b.width) || 0)) }; })"""


def _exe() -> str | None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _open(p, exe, srv: Server, sid: str, width: int, *, dark: bool = False):  # noqa: ANN001
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    kw = {"has_touch": True, "is_mobile": True} if width < 900 else {}
    ctx = browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme="dark" if dark else "light", **kw
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": srv.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


def _muted(page) -> str:  # noqa: ANN001
    return page.evaluate(
        "() => { const s = document.createElement('span'); s.className = 'muted'; document.body.append(s);"
        " const c = getComputedStyle(s).color; s.remove(); return c; }"
    )


def _no_dangling_dot(page, sel: str) -> None:  # noqa: ANN001
    """A facts line (.ui-dots): every item but the first carries its dot, and an item that starts
    a new line sits at the line's left edge (its dot is drawn outside, where the line clips it)."""
    lines = page.locator(sel)
    assert lines.count() >= 1, sel
    for i in range(lines.count()):
        kids = lines.nth(i).evaluate(DOTS)
        assert len(kids) >= 2 and all(k["dot"] for k in kids[1:]) and not kids[0]["dot"], (sel, kids)
        for prev, k in zip(kids, kids[1:], strict=False):
            if k["top"] > prev["top"]:
                assert abs(k["left"] - k["line"]) <= 1, (sel, kids)
            else:
                # the dot sits in the gap, clear of both words
                assert prev["right"] <= k["dotLeft"] and k["dotRight"] <= k["left"], (sel, kids)


@pytest.mark.parametrize("width", [412, 1280])
def test_the_deck_page_banner_list_and_tools_line_up(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid, width, dark=width == 412)
        # "load", then the parts measured: a view load after the page's kept read has aged starts
        # the /decks/{id}/check long-poll, which "networkidle" would wait out (audit, section 6)
        page.goto(f"{server.base}/decks/42?view=list", wait_until="load")
        page.locator("ul.rows .row .name").first.wait_for(timeout=10_000)
        page.locator(".statctl[aria-label=Colors] button").first.wait_for(state="attached", timeout=10_000)
        assert page.evaluate(MEASURE) == []
        # B1: the Like count's centre is the heart's centre, inside the chip's
        soc = page.locator(".banner .social .soc[data-social=vote]")
        n, heart, chip = (
            soc.locator("b.n").evaluate(RECT),
            soc.locator("svg").first.evaluate(RECT),
            soc.evaluate(RECT),
        )
        mid = lambda r: (r[1] + r[3]) / 2  # noqa: E731
        assert abs(mid(n) - mid(heart)) <= 1 and abs(mid(n) - mid(chip)) <= 1.5, (n, heart, chip)
        # C7: no price yet reads "unknown" in muted text, never an orange dash. The banner's own
        # muted colour, not the page's: that grey was 1.5:1 on the shade (polish R2,
        # test_recheck_0746_browser measures the contrast)
        est = page.locator(".banner .unpriced")
        assert est.inner_text() == "unknown"
        assert est.evaluate("e => getComputedStyle(e).color") == "rgba(255, 255, 255, 0.72)" != _muted(page)
        assert "–" not in est.locator("xpath=..").inner_text()
        # B2: every name in the list starts at the same x, owned dot and two-digit quantity or not
        xs = page.evaluate(
            "() => [...document.querySelectorAll('ul.rows .row .name')].map(n =>"
            " Math.round(n.getBoundingClientRect().left - n.closest('.row').getBoundingClientRect().left))"
        )
        assert len(xs) >= 6 and len(set(xs)) == 1, xs
        qs = page.locator("ul.rows .row.brew .n .q")
        if qs.count():
            assert qs.first.evaluate("e => getComputedStyle(e).textAlign") == "right"
        # B3: no lone grip icon in the toolbar
        assert page.locator(".toolbar .hint").count() == 0
        # B5: Bar and Pie are one joined switch; Lands only sits apart as a toggle of its own
        bar, pie, lands = (
            page.locator(".statctl[aria-label=Colors] button").nth(i).evaluate(RECT) for i in range(3)
        )
        assert pie[0] <= bar[2] and bar[1] == pie[1] and bar[4] == pie[4], (bar, pie)
        assert lands[0] - pie[2] >= 8, (pie, lands)
        if width >= 1280:
            # B4: Undo and Redo are drawn as their neighbour Select cards is: a bordered 44 px button
            undo = page.locator(".bulkbar .histtools button").first
            select = page.locator(".bulkbar > button").first
            style = (
                "e => { const c = getComputedStyle(e);"
                " return [c.borderStyle, c.borderWidth, Math.round(e.getBoundingClientRect().height)]; }"
            )
            assert undo.evaluate(style) == select.evaluate(style) and undo.evaluate(style)[0] == "solid", (
                undo.evaluate(style),
                select.evaluate(style),
            )
        assert errors == []
        browser.close()


def test_the_editor_chips_pending_badge_and_goldfish_facts(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid, 412)
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        assert page.evaluate(MEASURE) == []
        # D05 and B10: the category chips are blocks that end in an ellipsis, and the Maybeboard
        # chip flows after the others at a row's left edge, never alone at the right
        chips = page.locator(".addbox .tchip")
        assert chips.count() >= 4
        info = page.evaluate(
            "() => [...document.querySelectorAll('.addbox .tchip')].map(c => {"
            " const r = c.getBoundingClientRect(), s = getComputedStyle(c);"
            " return [c.textContent.trim(), Math.round(r.left), Math.round(r.top),"
            " s.display, s.textOverflow, s.overflow, c.scrollWidth <= c.clientWidth]; })"
        )
        assert all(
            i[3] in ("block", "inline-block") and i[4] == "ellipsis" and i[5] == "hidden" and i[6]
            for i in info
        ), info
        side = [i for i in info if i[0] == "Maybeboard"]
        first_left = min(i[1] for i in info)
        assert side and (
            side[0][1] == first_left or any(i[2] == side[0][2] and i[1] < side[0][1] for i in info)
        ), info
        # a chip that starts a new row starts where the first does
        rows = sorted({i[2] for i in info})
        for top in rows:
            assert min(i[1] for i in info if i[2] == top) == first_left, info
        # C3: nothing pending is a grey badge, not the orange attention one
        badge = page.locator(".pendingbox summary .n")
        assert badge.inner_text() == "0" and "zero" in (badge.get_attribute("class") or "")
        grey = badge.evaluate("e => getComputedStyle(e).backgroundColor")
        orange = page.evaluate(
            "() => getComputedStyle(document.documentElement).getPropertyValue('--orange').trim()"
        )
        assert grey != orange and grey == page.evaluate(
            "() => { const s = document.createElement('span'); s.style.background = 'var(--surface-3)';"
            " document.body.append(s); const c = getComputedStyle(s).backgroundColor; s.remove(); return c; }"
        )
        # C1: the goldfish intro's dots never dangle at a line end
        page.goto(f"{server.base}/decks/42/goldfish", wait_until="networkidle")
        _no_dangling_dot(page, ".gf-intro.ui-dots")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("view", ["grid", "list"])
def test_binder_tags_tiles_and_clear_filters(server: Server, view: str) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid, 412)
        page.goto(f"{server.base}/collection?view={view}", wait_until="networkidle")
        assert page.evaluate(MEASURE) == []
        # B6: a long tag is one line in its pill, with its full name as the hover text
        pills = page.locator(".pill.tag")
        assert pills.count() >= 2
        long_one = [i for i in range(pills.count()) if len(pills.nth(i).inner_text()) > 30]
        assert long_one, [pills.nth(i).inner_text() for i in range(pills.count())]
        for i in long_one:
            pill = pills.nth(i)
            rect = pill.evaluate(RECT)
            assert rect[4] <= 26, rect
            text = pill.locator(".t")
            assert text.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
            assert text.evaluate("e => e.scrollWidth > e.clientWidth")
            assert pill.get_attribute("title") == text.evaluate("e => e.textContent")
        # C4: Clear filters stays hidden while nothing is filtered
        for clear in page.locator(".sp-clear").all():
            assert clear.evaluate("e => e.hidden && !e.offsetParent")
        if view == "grid":
            # B7: the steppers of one row share a top, and the ⋮ sits beside the name
            acts = page.evaluate(
                "() => [...document.querySelectorAll('ul.collgrid > li .act')].slice(0, 4)"
                ".map(a => Math.round(a.getBoundingClientRect().top))"
            )
            assert len(acts) == 4 and acts[0] == acts[1] and acts[2] == acts[3], acts
            kebab = page.locator("ul.collgrid .nm .kebab").first.evaluate(RECT)
            name = page.locator("ul.collgrid .nm .name").first.evaluate(RECT)
            assert kebab[1] < name[3] and kebab[3] > name[1] and kebab[0] >= name[2], (kebab, name)
        assert errors == []
        browser.close()


def test_watchlist_pills_and_facts_line(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid, 412)
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        assert page.evaluate(MEASURE) == []
        # B8: each alert pill is one line inside its cell, the words in its hover text
        pills = page.locator(".w-t .sub .ui-lbl")
        assert pills.count() >= 1
        for pill in pills.all():
            rect = pill.evaluate(RECT)
            assert rect[4] <= 26 and pill.evaluate("e => e.scrollWidth <= e.clientWidth"), rect
            if "±" in pill.inner_text():
                assert pill.get_attribute("title", timeout=1000).startswith("Moves more than")
        # C1: the status line's dots never dangle
        _no_dangling_dot(page, ".w-status.ui-dots")
        # C6: both list heads say their total the same way
        totals = re.findall(r"Together [^<]*", page.content())
        assert totals and all(re.fullmatch(r"Together \$[\d,.]+ today", t) for t in totals), totals
        assert errors == []
        browser.close()


def test_report_capsules_and_facts_admin_line_and_the_deck_count(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid, 412)
        page.goto(f"{server.base}/history/reports/r1", wait_until="networkidle")
        assert page.evaluate(MEASURE) == []
        # B11: the way-back capsules sit 8 px apart, like any chip row
        caps = [a.evaluate(RECT) for a in page.locator(".rv-crumbs a").all()]
        assert len(caps) == 3, caps
        for a, b in zip(caps, caps[1:], strict=False):
            if a[1] == b[1]:
                assert b[0] - a[2] == 8, (a, b)
        # C1: the coverage widget's two facts carry their dot in the gap, each at a line's start
        _no_dangling_dot(page, ".wdg .det .tx.ui-dots")
        assert (
            page.locator(".wdg .det .tx.ui-dots").first.evaluate("e => getComputedStyle(e).display") == "flex"
        )
        # D02: the Move to a folder box leaves a gap between its sentence and the Folder field
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        page.evaluate("() => { document.getElementById('decks-bulk-move').hidden = false; }")
        sentence = page.locator("#decks-bulk-move .body p").evaluate(RECT)
        field = page.locator("#decks-bulk-move .body .field").evaluate(RECT)
        assert field[1] - sentence[3] >= 10, (sentence, field)
        # C5: the count says every deck as Home does, and how many sit in folders
        assert page.locator("#decks-total").inner_text() == "5 decks · 2 in folders"
        page.goto(f"{server.base}/", wait_until="networkidle")
        page.wait_for_function("() => /All decks \\(\\d+\\)/.test(document.body.innerText)")
        assert "All decks (5)" in page.inner_text("body")
        # C3: Scan's empty list badge is grey
        page.goto(f"{server.base}/scan", wait_until="networkidle")
        badge = page.locator("#list-badge")
        assert badge.inner_text() == "0" and "zero" in (badge.get_attribute("class") or "")
        assert badge.evaluate("e => getComputedStyle(e).backgroundColor") != page.evaluate(
            "() => getComputedStyle(document.documentElement).getPropertyValue('--orange').trim()"
        )
        assert errors == []
        browser.close()


def test_the_admin_overview_facts_line_at_desktop_width(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid, 1280)
        page.goto(f"{server.base}/admin", wait_until="networkidle")
        _no_dangling_dot(page, ".wdg .det .ui-dots")
        # N13: a long listing tag on the Decks list ends in an ellipsis inside its row
        page.goto(f"{server.base}/decks?view=list", wait_until="networkidle")
        pills = page.locator("ul.decklist .tags .pill")
        assert pills.count() >= 2
        clipped = 0
        for pill in pills.all():
            rect, row = pill.evaluate(RECT), pill.locator("xpath=ancestor::li[1]").evaluate(RECT)
            assert rect[0] >= row[0] and rect[2] <= row[2] and rect[4] <= 26, (rect, row)
            assert pill.evaluate("e => getComputedStyle(e).whiteSpace") == "nowrap"
            if pill.evaluate("e => e.scrollWidth > e.clientWidth"):
                clipped += 1
                assert pill.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
                assert pill.get_attribute("title") == pill.evaluate("e => e.textContent")
        assert clipped >= 1
        assert errors == []
        browser.close()


def test_a_chip_row_that_runs_out_of_room_fades_its_edge(server: Server) -> None:  # noqa: F811
    """A quick-filter chip row wider than the screen scrolls, and the edge with more chips fades
    out instead of cutting a chip (final audit N12), as the Binder's switch does."""
    from playwright.sync_api import sync_playwright

    sid, _pending = _seed(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid, 412)
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        nav = page.locator("nav.ui-chips").first
        assert nav.evaluate("n => n.scrollWidth > n.clientWidth + 1"), "the chips run out of room at 412"
        page.wait_for_function("() => document.querySelector('nav.ui-chips').classList.contains('more-end')")
        assert nav.evaluate(
            "n => getComputedStyle(n).maskImage || getComputedStyle(n).webkitMaskImage"
        ) not in (
            "none",
            "",
        )
        assert not nav.evaluate("n => n.classList.contains('more-start')")
        nav.evaluate("n => { n.scrollLeft = n.scrollWidth; }")
        page.wait_for_function(
            "() => { const n = document.querySelector('nav.ui-chips');"
            " return n.classList.contains('more-start') && !n.classList.contains('more-end'); }"
        )
        assert errors == []
        browser.close()
