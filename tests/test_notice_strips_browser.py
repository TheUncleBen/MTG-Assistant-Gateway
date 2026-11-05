"""Every notice strip the site shows, measured in headless Chromium at phone widths, the Fold's
floor and a desktop, at 100% text and both kinds of 200% text (a 28 px root, and every font size
doubled). Audit C1 on a62955eb: the theme's ``.notice{flex-wrap:wrap}`` (the B1 fix, so a held
strip's Try again drops under the words) put the × of every Binder strip alone on a row above the
words. C2: a stopped import's "Stop here and show what went in" was nowrap, 508 px wide at 200%
text, out of its strip and the Binder scrolled sideways. C4: "Show them first" on the added-cards
line was a 108×19 px inline link on a touch screen."""

from __future__ import annotations

import re
import time

import pytest

from .test_cardsearch_browser import _chromium, _page
from .test_overflow_sweep_browser import MEASURE, _seed, server  # noqa: F401
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

SUB = "user-1"
TEN_EM = 160  # the audit's "about 10 em" column, in the pages' 16 px em

# every visible notice on the page: its box and content width, its × (the Binder's strips), its
# buttons (box, lines of words, whole inside the strip) and its words' line boxes; a word broken
# across two lines anywhere in it (the words or a button) is listed by name
STRIPS = """() => {
  const vw = document.documentElement.clientWidth;
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const inBtn = (n) => !!(n.parentElement && n.parentElement.closest('.btn, button, .nx, progress'));
  const text = (root, pick) => {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), rects = [], broken = [];
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (!n.textContent.trim() || !pick(n)) continue;
      const re = /\\S+/g; let m;
      while ((m = re.exec(n.textContent))) {
        const q = document.createRange(); q.setStart(n, m.index); q.setEnd(n, m.index + m[0].length);
        const rs = [...q.getClientRects()].filter(r => r.width > 0);
        if (rs.length > 1) broken.push(m[0]);
        rects.push(...rs);
      }
    }
    return {rects, broken};
  };
  const lines = (rects) => {
    const rows = [];
    for (const r of [...rects].sort((a, b) => a.top - b.top)) {
      const row = rows.find(x => Math.abs(x.top - r.top) < r.height / 2 + 1);
      if (row) { row.left = Math.min(row.left, r.left); row.right = Math.max(row.right, r.right);
        row.bottom = Math.max(row.bottom, r.bottom); }
      else rows.push({top: r.top, bottom: r.bottom, left: r.left, right: r.right});
    }
    return rows.map(x => ({top: Math.round(x.top), bottom: Math.round(x.bottom),
      left: Math.round(x.left), right: Math.round(x.right)}));
  };
  const box = (r) => ({left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top),
    bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height)});
  return {
    vw, scroll: document.documentElement.scrollWidth - vw,
    strips: [...document.querySelectorAll('.notice')].filter(shown).map(n => {
      const cs = getComputedStyle(n), nb = n.getBoundingClientRect();
      const content = n.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      const pad = parseFloat(cs.paddingTop);
      const x = n.querySelector(':scope > .nx');
      // the words' own box where they have one (the Binder's strips: .nt beside the ×)
      const nt = n.querySelector(':scope > .nt');
      // the room the × takes in the row (its box less the padding it overhangs), and the body's
      // own box (its line boxes, or its least height) less the padding it overhangs
      const xrow = x ? x.getBoundingClientRect().height + 2 * parseFloat(getComputedStyle(x).marginTop) : 0;
      const ns = nt ? getComputedStyle(nt) : null;
      const body = nt ? nt.clientHeight - parseFloat(ns.paddingTop) - parseFloat(ns.paddingBottom) : 0;
      const words = text(n, t => !inBtn(t));
      const btns = [...n.querySelectorAll('.btn, button')].filter(b => !b.classList.contains('nx'))
        .filter(shown).map(b => {
          const t = text(b, () => true);
          // the label's width on one line: wider than the strip's content, and only then, it wraps
          const c = b.cloneNode(true);
          c.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;' +
            'width:max-content;max-width:none';
          n.appendChild(c);
          const natural = c.getBoundingClientRect().width;
          c.remove();
          return {text: b.textContent.trim(), box: box(b.getBoundingClientRect()),
            lines: lines(t.rects).length, broken: t.broken, natural: Math.round(natural)};
        });
      // the gaps between the sentence's lines (a list's lines have their own leading): even
      // when every one is the same (audit N4: a 44 px link box in the last line opened its gap)
      const bts = [...n.querySelectorAll('.btn, button')].filter(b => !b.classList.contains('nx'))
        .map(b => b.getBoundingClientRect());
      const rows = lines(text(n, t => !inBtn(t) && !t.parentElement.closest('ul')).rects);
      const withBtn = (l) => bts.some(b => b.top < l.bottom && l.top < b.bottom);
      const gaps = [];
      for (let i = 1; i < rows.length; i++)
        if (!withBtn(rows[i]) && !withBtn(rows[i - 1])) gaps.push(rows[i].top - rows[i - 1].top);
      return {cls: n.className, text: n.textContent.trim().replace(/\\s+/g, ' ').slice(0, 60),
        box: box(nb), content: Math.round(content), x: x ? box(x.getBoundingClientRect()) : null,
        nt: nt ? Math.round(nt.clientWidth) : null, gaps, pad, xrow, body,
        lines: lines(words.rects), broken: words.broken, btns,
        // a link's box, how many pieces it is in, and the words of the other lines its box reaches
        links: [...n.querySelectorAll('a:not(.btn)')].filter(shown).map(a => {
          const ab = a.getBoundingClientRect(), own = text(a, () => true).rects;
          const sameLine = (r) => own.some(o => Math.abs(o.top - r.top) < r.height / 2);
          const near = text(n, t => !inBtn(t) && !a.contains(t)).rects.filter(r => !sameLine(r))
            .filter(r => r.left < ab.right && ab.left < r.right && r.top < ab.bottom && ab.top < r.bottom);
          return {text: a.textContent.trim(), box: box(ab), pieces: a.getClientRects().length,
            over: near.map(r => box(r))};
        })};
    })
  };
}"""


def _zoom(page, zoom: str) -> None:  # noqa: ANN001
    if zoom != "none":
        page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
        page.wait_for_timeout(150)


def _column(s: dict) -> int:
    """The width the words are given to wrap in: their own box where they have one, else from
    their left edge to the strip's content edge, or to a button standing beside the words' block.
    Not how far the lines happen to reach: where a line breaks depends on the font's word widths,
    and the test machine's fonts are not the members' (CI drew the same Binder strip's lines
    153 px wide in DejaVu Sans and 170 px in Inter, in the same 187 px column)."""
    if s["nt"]:
        return s["nt"]
    left = min(ln["left"] for ln in s["lines"])
    reach = max(ln["right"] for ln in s["lines"])
    right = s["box"]["left"] + (s["box"]["width"] - s["content"]) / 2 + s["content"]
    for b in s["btns"]:
        bb = b["box"]
        beside = bb["left"] >= reach - 1
        if beside and any(bb["top"] < ln["bottom"] and ln["top"] < bb["bottom"] for ln in s["lines"]):
            right = min(right, bb["left"])
    return round(right - left)


def _check(where: str, got: dict, *, expect: str, touch: bool) -> None:
    """One page's strips: no sideways scroll and every strip inside the window, the words' column
    at least about 10 em when they wrap, the × on the words' first line, every button inside its
    strip with no word broken in two (a label wraps only when it is wider than the strip), and
    "Show them first" a 44 px target on a touch screen."""
    assert got["scroll"] <= 0, (where, got["scroll"])
    strips = [s for s in got["strips"] if expect in s["cls"] or expect in s["text"]]
    assert strips, (where, expect, [s["cls"] for s in got["strips"]])
    for s in strips:
        assert s["box"]["left"] >= -1 and s["box"]["right"] <= got["vw"] + 1, (where, s["box"])
        assert s["lines"], (where, s)
        assert s["broken"] == [], (where, s["broken"], s["text"])
        if len(s["lines"]) > 1:
            assert _column(s) >= TEN_EM, (where, _column(s), s)
        if s["x"]:
            # the strip's frame beside the words (its edges, the × and the gap) is sized in px,
            # so large text widens the words' column and not the frame (final polish Z6: at 360 px
            # and 200% text the frame took 101 px and "Bannerkeeper" broke in two)
            assert s["box"]["width"] - s["nt"] <= 80, (where, s["box"]["width"], s["nt"])
            first = s["lines"][0]
            # the × shares the words' first line: beside it, not on a row of its own above
            assert s["x"]["top"] < first["bottom"] and first["top"] < s["x"]["bottom"], (where, s)
            assert s["x"]["right"] <= first["left"] + 1, (where, s)
        for b in s["btns"]:
            assert b["broken"] == [], (where, b, s["text"])
            bb = b["box"]
            assert bb["left"] >= s["box"]["left"] - 1 and bb["right"] <= s["box"]["right"] + 1, (where, b, s)
            assert bb["top"] >= s["box"]["top"] - 1 and bb["bottom"] <= s["box"]["bottom"] + 1, (where, b, s)
            assert bb["height"] >= 44, (where, b)
            # a short label stays on one line; a long one wraps only because it is wider than the strip
            if b["lines"] > 1:
                assert b["natural"] > s["content"], (where, b, s)
            assert bb["width"] >= 44, (where, b)
        # the sentence's lines keep one leading: no line box grown by a target in it
        assert max(s["gaps"], default=0) - min(s["gaps"], default=0) <= 1, (where, s["gaps"], s["text"])
        for a in s["links"]:
            # a link's hit box reaches no words of the line above or below (audit D7 on 099f600a:
            # the inline padding that made "Show them first" 44 px covered them; it is a button
            # now, checked above with the strip's other buttons: audit N4)
            assert a["over"] == [], (where, a)
            if "Show them first" in a["text"]:
                # the whole body is the target, 44 px at the least, and the strip is no taller
                # than its words (and their leading is even, above): the body overhangs the
                # strip's padding as the × does, so only a one-line strip grows, by a few px
                assert a["box"]["height"] >= 44 and a["box"]["width"] >= 44, (where, a)
                least = max(s["body"], s["xrow"])
                assert s["box"]["height"] <= least + 2 * s["pad"] + 1, (where, s)


def _jobs(srv: Server, jid: str, state: str, done: int, result: dict | None = None) -> None:
    """The member's import job ``jid`` in ``state``, ``done`` of 130 lines in."""
    jobs = srv.app.state.gateway.collection_jobs
    items = [{"name": f"Card {i}", "quantity": 1} for i in range(130)]
    jobs.store.start(SUB, jid, "import", items, None)
    jobs.store.update(SUB, jid, state=state, done=done, result=result)


@pytest.mark.parametrize("width", [320, 360, 412, 1280])
@pytest.mark.parametrize("zoom", ["none", "root", "text"])
def test_every_notice_keeps_its_x_its_words_and_its_buttons_in_the_strip(
    server: Server,  # noqa: F811
    width: int,
    zoom: str,
) -> None:
    """Audit C1, C2 and C4: the Binder's × strips (ok, error, an import's results with their
    list, a bulk Undo, the added-cards line with "Show them first"), the running and stopped
    import strips, the busy strip, the held strips of the Binder, a deck, Lists, a profile, a
    search and the forum, and a draft's Discard note: at 320, 360, 412 and 1280 px, at 100% and
    200% text (a 28 px root; every font size doubled), nothing scrolls sideways, the words keep
    a column of at least about 10 em, the × sits on the words' first line, a button stays inside
    its strip and wraps at a space only when it is wider than the strip, and "Show them first" is
    a 44 px target on a touch screen."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    gw = server.app.state.gateway
    touch = width < 900
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)

        def look(path: str, expect: str, *, name: str = "") -> None:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            _zoom(page, zoom)
            _check(f"{name or path} @{width} {zoom}", page.evaluate(STRIPS), expect=expect, touch=touch)

        # the busy strip: a listing never read while Archidekt does not answer
        server.ark.fail_collection_reads = True
        try:
            look("/collection?q=never-read-before", "held", name="busy")
        finally:
            server.ark.fail_collection_reads = False
        # the × strips
        look("/collection?err=writes", "error")
        look("/collection?err=busyimport", "error")
        look("/collection?ok=added", "ok")
        # an import that ended: its result with the list of what was kept or not found
        _jobs(
            server,
            "job3",
            "done",
            130,
            {"added": [{"index": 0, "went_to": "existing"}], "skipped": [{"index": 1}, {"index": 2}]},
        )
        look("/collection", "ok", name="import results")
        # a stopped import (Resume, Stop here and show what went in) and one still running
        _jobs(
            server,
            "job1",
            "stopped",
            120,
            {"added": [], "skipped": [], "stopped": "Archidekt is not answering right now"},
        )
        look("/collection?import=job1", "stopped")
        _jobs(server, "job2", "running", 40, None)
        look("/collection?import=job2", "importing")
        gw.collection_jobs.store.update(
            SUB, "job2", state="done", done=130, result={"added": [], "skipped": []}
        )
        # the job ends: the page's own poll (static/collection.js, every 1.5 s) sees it done and
        # opens the result's address, which shows the result once. A goto of ours here raced that
        # navigation and was aborted under load (net::ERR_ABORTED), so the page's own move is the
        # condition to wait for
        page.wait_for_url(re.compile(r"ok=imported|err=nothingimported"))
        page.wait_for_load_state("networkidle")
        # a draft put back: Discard beside the words
        page.goto(f"{server.base}/decks/new", wait_until="networkidle")
        page.locator("#newdeck #name").fill("Old idea")
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.getElementById('newdeck').hidden")
        look("/decks/new", "draft-note")
        page.locator("#newdeck .draft-note button", has_text="Discard").click()
        # the pages read once, so each has a copy to hold while Archidekt does not answer
        import httpx

        h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
        for path in (
            "/collection?view=list&sort=edition&dir=desc",
            "/lists/templates",
            "/users/alice",
            "/search?q=Sample",
        ):
            assert h.get(path).status_code == 200, path
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        csrf = page.evaluate("() => document.querySelector('[name=csrf]').value")
        r = h.post(
            "/collection/api/add",
            headers={"X-CSRF-Token": csrf},
            json={"items": [{"name": "Island", "quantity": 1}], "source": "scan"},
        )
        assert r.status_code == 200, r.text
        decks = gw.decks
        decks._page_decks = {k: (v[0] - 7 * 3600, v[1]) for k, v in decks._page_decks.items()}
        gw.collection.first_page_ttl = 0
        decks.client.pacer._open_until = time.monotonic() + 600
        try:
            look("/collection?view=list&sort=edition&dir=desc", "held", name="held binder")
            look("/collection?view=list&sort=edition&dir=desc", "you added", name="added line")
            look("/decks/42", "held")
            # the held deck page as a whole: a deck that is not the member's own (a held copy is
            # read-only) had its view controls in the toolbar's first column, where at 1280 px and
            # 200% text Group by, Sort by and the filter ran over each other
            assert page.evaluate(MEASURE) == [], ("/decks/42", width, zoom)
            look("/lists/templates", "held")
            look("/users/alice", "held")
            look("/search?q=Sample", "held")
            look("/forum", "held")
        finally:
            decks.client.pacer._open_until = 0
        # the strips static/collection.js builds (audit C1 on 099f600a: its × stood alone above
        # the words with a Sign in or Undo button): Undo after a removal, where the strip stands
        # in for the shared menus' toast, and Sign in when a change fails because the session
        # ended. The first seeded card is one copy, so its − removes the row
        page.goto(f"{server.base}/collection?view=list", wait_until="networkidle")
        _zoom(page, zoom)
        page.evaluate("() => { window.MtgMenu.toast = null; }")
        row = page.locator("li[data-id]").first
        assert row.locator("form.qty output").inner_text().strip() == "1"
        row.locator("form.qty button[value=dec]").click()
        page.locator("#col-notes .notice[data-strip]", has_text="Removed").wait_for()
        _check(f"Undo @{width} {zoom}", page.evaluate(STRIPS), expect="Removed", touch=touch)
        page.locator("#col-notes .notice[data-strip] button", has_text="Undo").click()
        page.locator("#col-notes .notice[data-strip]", has_text="back in your binder").wait_for()
        page.context.clear_cookies()
        # the strip names the card: the sweep's long name, whose "Bannerkeeper" broke in two at
        # 360 px and 200% text in a 210 px column (final polish Z6)
        page.locator("li[data-id]", has_text="Bannerkeeper").first.locator(
            "form.qty button[value=inc]"
        ).click()
        page.locator("#col-notes .notice[data-strip]", has_text="signed out").wait_for()
        _check(f"Sign in @{width} {zoom}", page.evaluate(STRIPS), expect="signed out", touch=touch)
        assert errors == []
        browser.close()
