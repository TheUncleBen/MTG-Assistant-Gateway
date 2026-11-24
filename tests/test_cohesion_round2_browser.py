"""Cohesion round 2 in headless Chromium (docs/UI-STANDARD.md), on the overflow sweep's data: a
proposal as its own page says its number once, every page's way back a level is one capsule above
the title, the Binder page has the Account page's title and narrow column and its switch in the
middle, the Scan area button keeps its words outside the card's guide, My decks' size switch is
never alone on a row, a row's main action is the button the tiles use, and icons grow with the
text. Skipped without a Chromium build."""

# ruff: noqa: E501 - the scripts the browser runs are kept one statement a line
from __future__ import annotations

import pytest

from .test_cardsearch_browser import _page
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_text_zoom_browser import TEXT_200
from .waits import frame

pytest.importorskip("playwright")

NARROW = 44 * 14  # the standard's narrow column, in px at the 14 px root

BACK = """() => { const a = document.querySelector('main a.ui-back');
  if (!a || !a.getClientRects().length) return null;
  const cs = getComputedStyle(a), r = a.getBoundingClientRect();
  const h1 = [...document.querySelectorAll('main h1')].find(h => h.getClientRects().length);
  const hr = h1 ? h1.getBoundingClientRect() : null;
  const svg = a.querySelector('svg.i');
  return {look: [cs.backgroundColor, cs.borderTopLeftRadius, cs.fontSize, cs.fontWeight, cs.color,
                 cs.paddingLeft, !!svg].join(' '),
          tall: r.height, aboveTitle: hr ? r.bottom <= hr.top + 1 : null,
          // the capsule's drawn edge (inside its transparent border) starts where the title does
          lead: hr ? Math.round(r.left + parseFloat(cs.borderLeftWidth) - hr.left) : null,
          text: a.innerText.trim()}; }"""


def _start(server: Server) -> tuple[str, str, str]:  # noqa: F811
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
    return sid, pending, exe


def test_a_proposal_as_its_own_page_says_its_number_once(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 412, "light", exe)
        page.goto(f"{server.base}/proposals/{pending}", wait_until="networkidle")
        head = page.evaluate(
            """() => { const d = document.querySelector('.ui-drawer');
              const shown = (e) => e && e.getClientRects().length > 0;
              const h1 = d.querySelector('h1'), h2 = d.querySelector('.dh h2');
              return {h1: shown(h1) ? h1.innerText : '', h2: shown(h2) ? h2.innerText : '',
                      close: shown(d.querySelector('.dh .close'))}; }"""
        )
        assert head["h1"].startswith("Proposal #1 for "), head
        # the heading under the title says what changes, without the number a second time
        assert head["h2"] and "#" not in head["h2"] and not head["close"], head
        browser.close()
        # beside the list (a computer) the panel keeps its numbered heading and its ×; the page's
        # title is the list's
        browser, page, errors2 = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/proposals/{pending}", wait_until="networkidle")
        side = page.evaluate(
            """() => { const d = document.querySelector('.ui-drawer');
              const shown = (e) => e && e.getClientRects().length > 0;
              return {h1: shown(d.querySelector('h1')), h2: d.querySelector('.dh h2').innerText,
                      back: shown(d.querySelector('.ui-back')), close: shown(d.querySelector('.dh .close'))}; }"""
        )
        assert not side["h1"] and side["h2"].startswith("#1 ") and not side["back"] and side["close"], side
        browser.close()
        assert not errors and not errors2, errors + errors2


def test_every_page_has_the_one_way_back_above_its_title(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, pending, exe = _start(server)
    paths = {
        "/account/details": "Account",
        "/account/assistants": "Account details",
        "/binder/settings": "Binder",
        "/binder?show=all": "Binder",
        "/collection?view=list": "Binder",
        f"/proposals/{pending}": "Back to Proposals",
        "/decks/42/settings": None,  # the deck's name
    }
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 412, "light", exe)
        seen = {}
        for path, text in paths.items():
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            frame(page)
            got = page.evaluate(BACK)
            assert got, path
            assert got["tall"] >= 44 and got["aboveTitle"] and got["lead"] == 0, (path, got)
            assert text is None or got["text"] == text, (path, got)
            seen[path] = got["look"]
        assert len(set(seen.values())) == 1, seen
        # the arrow grows with the words (200% text)
        page.goto(f"{server.base}/account/details", wait_until="networkidle")
        small = page.locator("main a.ui-back svg.i").bounding_box()
        row = page.locator("main .ygrp .yrow > svg.i").first.bounding_box()
        page.evaluate(TEXT_200)
        frame(page)
        big = page.locator("main a.ui-back svg.i").bounding_box()
        row2 = page.locator("main .ygrp .yrow > svg.i").first.bounding_box()
        assert small and big and big["width"] >= 1.8 * small["width"], (small, big)
        assert row and row2 and row2["width"] >= 1.3 * row["width"], (row, row2)
        # and it goes back
        page.goto(f"{server.base}/binder/settings", wait_until="networkidle")
        page.locator("main a.ui-back").click()
        page.wait_for_url("**/binder")
        browser.close()
        assert not errors, errors


def test_the_binder_page_has_the_account_pages_title_and_column(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    measure = """() => { const h = [...document.querySelectorAll('main h1')].filter(x => x.getClientRects().length);
      const cs = getComputedStyle(h[0]), hr = h[0].getBoundingClientRect();
      const col = document.querySelector('main .yhub').getBoundingClientRect();
      const vw = document.documentElement.clientWidth;
      return {n: h.length, text: h[0].innerText, size: cs.fontSize, weight: cs.fontWeight,
              width: Math.round(col.width), lean: Math.round(col.left - (vw - col.right)),
              lead: Math.round(hr.left - col.left)}; }"""
    with sync_playwright() as p:
        # an open Fold: one column, so the standard's narrow one, in the middle, as Account's
        browser, page, errors = _page(p, server, sid, 800, "light", exe)
        got = {}
        for path in ("/account", "/binder"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            frame(page)
            got[path] = page.evaluate(measure)
        a, b = got["/account"], got["/binder"]
        assert (b["n"], b["text"], b["size"], b["weight"]) == (1, "Binder", "28px", "700"), got
        assert b["width"] == a["width"] == NARROW and b["lead"] == a["lead"] == 0, got
        assert abs(b["lean"] - a["lean"]) <= 1, got
        # a picked view on a phone: the way back, then its name as the page's title
        browser.close()
        browser, page, errors2 = _page(p, server, sid, 412, "light", exe)
        page.goto(f"{server.base}/binder?show=all", wait_until="networkidle")
        frame(page)
        title = page.evaluate(
            """() => { const h = [...document.querySelectorAll('main h1')].filter(x => x.getClientRects().length);
              return [h.length, h[0].innerText, getComputedStyle(h[0]).fontSize]; }"""
        )
        assert title == [1, "All cards", "28px"], title
        seg = page.locator(".bn-seg").bounding_box()
        main = page.locator(".bn-main").bounding_box()
        # the switch takes the list's whole width on a phone
        assert seg and main and abs(seg["width"] - main["width"]) <= 1, (seg, main)
        browser.close()
        # a computer: the library beside the list, the switch in the middle of the list
        browser, page, errors3 = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/binder?show=all", wait_until="networkidle")
        frame(page)
        seg = page.locator(".bn-seg").bounding_box()
        main = page.locator(".bn-main").bounding_box()
        assert seg and main and seg["width"] < main["width"] - 100
        left, right = seg["x"] - main["x"], main["x"] + main["width"] - seg["x"] - seg["width"]
        assert abs(left - right) <= 2, (left, right)
        assert page.locator("main h1").inner_text() == "All cards"
        assert not page.locator("main a.ui-back").is_visible()  # the library is beside it
        browser.close()
        assert not errors and not errors2 and not errors3, errors + errors2 + errors3


def test_scan_area_keeps_its_words_outside_the_cards_guide(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 412, "light", exe)
        page.goto(f"{server.base}/scan", wait_until="networkidle")
        btn = page.locator("#area-btn")
        btn.wait_for(state="visible")
        got = page.evaluate(
            """() => { const b = document.querySelector('#area-btn'), l = b.querySelector('.lbl');
              const g = document.querySelector('#scan-guide').getBoundingClientRect();
              const r = b.getBoundingClientRect();
              return {words: l.getClientRects().length ? l.innerText : '', name: b.getAttribute('aria-label'),
                      icon: !!b.querySelector('svg.i'), tall: r.height,
                      over: !(r.right <= g.left || r.left >= g.right || r.bottom <= g.top || r.top >= g.bottom)}; }"""
        )
        assert got["words"] == "Scan area" and got["name"] == "Scan area" and got["icon"], got
        assert got["tall"] >= 44 and not got["over"], got
        browser.close()
        assert not errors, errors


def test_the_size_switch_is_not_alone_and_row_actions_are_buttons(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        # an open Fold beside the rail leaves the panel about 780 px
        browser, page, errors = _page(p, server, sid, 884, "light", exe)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        frame(page)
        tops = page.evaluate(
            """() => { const bar = document.querySelector('.spanel .rbar');
              const kids = [...bar.children].filter(k => k.getClientRects().length && k.getBoundingClientRect().height > 20);
              const rs = kids.map(k => k.getBoundingClientRect());
              return kids.map((k, i) => [k.className, rs.filter((r, j) => j !== i && Math.min(r.bottom, rs[i].bottom) - Math.max(r.top, rs[i].top) > 4).length]); }"""
        )
        # every piece of the bar shares its row with another: the size switch is not alone
        assert tops and all(n >= 1 for _cls, n in tops), tops
        browser.close()
        # Reports on a computer: the tile's Open report and a row's Open are the same button
        browser, page, errors2 = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/reports", wait_until="networkidle")
        looks = page.evaluate(
            """() => { const look = (e) => { const cs = getComputedStyle(e);
                return [cs.backgroundColor, cs.borderTopWidth, cs.borderTopColor, cs.borderTopLeftRadius,
                        cs.fontSize, cs.fontWeight, cs.paddingLeft, Math.round(e.getBoundingClientRect().height)].join(' '); };
              const tile = [...document.querySelectorAll('main .wdg .go a')].find(a => /Open report/.test(a.innerText));
              const row = document.querySelector('main .ui-rows .tr:not(.group) .act > a.btn');
              const paste = document.querySelector('main .rp-paste > summary');
              return [look(tile), look(row), look(paste)]; }"""
        )
        assert len(set(looks)) == 1, looks
        # the row's button sits inside its row
        fit = page.evaluate(
            """() => { const b = document.querySelector('main .ui-rows .tr:not(.group) .act > a.btn');
              const r = b.getBoundingClientRect(), t = b.closest('.tr').getBoundingClientRect();
              return r.top >= t.top + 2 && r.bottom <= t.bottom - 2; }"""
        )
        assert fit
        page.locator("main .rp-paste > summary").click()
        assert page.locator("main .rp-paste textarea").is_visible()
        browser.close()
        assert not errors and not errors2, errors + errors2
