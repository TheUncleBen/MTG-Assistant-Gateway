"""The loop round's shared pieces in headless Chromium (docs/UI-STANDARD.md), on the overflow
sweep's data: one page column on a computer, the purpose line under the title on a phone, one
search field, one segmented switch, one disclosure capsule, chip rows that wrap from 600 px,
one-line selects and tags, a tile's icon beside its label, the empty state, level list heads and
the proposal panel's actions in view. Skipped without a Chromium build."""

# ruff: noqa: E501 - the scripts the browser runs are kept one statement a line
from __future__ import annotations

import pytest

from .test_cardsearch_browser import _page
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .waits import frame

pytest.importorskip("playwright")

PHONE, FOLD, LAPTOP = 412, 673, 1280


def _start(server: Server) -> tuple[str, str, str]:  # noqa: F811
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
    return sid, pending, exe


def _each(server: Server, widths, paths, script: str):  # noqa: F811
    """``script``'s answer on each path at each width: {(width, path): answer}."""
    from playwright.sync_api import sync_playwright

    sid, pending, exe = _start(server)
    got = {}
    with sync_playwright() as p:
        for w in widths:
            browser, page, errors = _page(p, server, sid, w, "light", exe)
            for path in paths:
                page.goto(f"{server.base}{path.replace('PENDING', pending)}", wait_until="networkidle")
                frame(page)
                got[(w, path)] = page.evaluate(script)
            browser.close()
            assert not errors, errors
    return got


# --- #10 chip rows -------------------------------------------------------------------------------

CHIPS = """() => [...document.querySelectorAll('main .ui-chips')].filter(n => n.getClientRects().length).map(n => {
  const r = n.getBoundingClientRect(), cs = getComputedStyle(n);
  return {wrap: cs.flexWrap, over: n.scrollWidth - n.clientWidth,
          cut: [...n.children].filter(c => c.getBoundingClientRect().right > r.right + 1).length,
          rows: new Set([...n.children].map(c => Math.round(c.getBoundingClientRect().top))).size,
          fade: n.classList.contains('more-end')}; })"""


def test_a_chip_row_wraps_from_600px_and_scrolls_only_on_a_phone(server: Server) -> None:  # noqa: F811
    got = _each(server, (PHONE, FOLD, LAPTOP), ("/proposals", "/reports", "/admin/activity"), CHIPS)
    for (w, path), rows in got.items():
        assert rows, (w, path)
        for row in rows:
            if w >= 600:
                assert row["wrap"] == "wrap" and row["over"] <= 1 and not row["cut"], (w, path, row)
                assert not row["fade"], (w, path, row)
            else:
                assert row["wrap"] == "nowrap" and row["rows"] == 1, (w, path, row)
                # a row with more chips than fit ends in the fade, never a hard cut
                assert not row["cut"] or row["fade"], (w, path, row)
    assert any(r["rows"] > 1 for r in got[(FOLD, "/proposals")]), got[(FOLD, "/proposals")]


# --- #11 selects ---------------------------------------------------------------------------------

SELECTS = """() => { const bs = [...document.querySelectorAll('main .ui-tools .msel-btn')].filter(b => b.getClientRects().length);
  return bs.map(b => { const t = b.querySelector('.msel-txt') || b, cs = getComputedStyle(t), r = b.getBoundingClientRect();
    const one = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.25;
    return {text: t.textContent.trim(), lines: Math.round(t.getBoundingClientRect().height / one),
            ws: cs.whiteSpace, ell: cs.textOverflow, cutBy: t.scrollWidth - t.clientWidth,
            top: Math.round(r.top), w: Math.round(r.width)}; }); }"""


def test_a_selects_text_stays_on_one_line_and_the_filter_row_wraps_whole_controls(
    server: Server,  # noqa: F811
) -> None:
    got = _each(server, (PHONE, FOLD), ("/history", "/admin/users", "/proposals"), SELECTS)
    for key, sels in got.items():
        assert sels, key
        for s in sels:
            assert s["lines"] == 1 and s["ws"] == "nowrap" and s["ell"] == "ellipsis", (key, s)
            # the row wraps a whole control before a choice is cut: every choice is whole
            assert s["cutBy"] <= 1, (key, s)
    # History has three filters: on a phone the third takes a row of its own
    tops = [s["top"] for s in got[(PHONE, "/history")]]
    assert len(tops) == 3 and tops[0] == tops[1] < tops[2], tops


# --- #12 tags ------------------------------------------------------------------------------------

TAGS = """() => [...document.querySelectorAll('main .ui-tag, main .pill')].filter(t => t.getClientRects().length && !t.closest('.summary-pills')).map(t => {
  const r = t.getBoundingClientRect(), pr = t.parentElement.getBoundingClientRect(), cs = getComputedStyle(t);
  return {text: t.textContent.trim(), h: Math.round(r.height), ws: cs.whiteSpace, ell: cs.textOverflow,
          out: Math.round(r.right - pr.right), cut: t.scrollWidth > t.clientWidth + 1, title: t.getAttribute('title') || ''}; })"""


def test_a_tag_is_one_line_with_its_whole_text_in_its_title(server: Server) -> None:  # noqa: F811
    paths = (
        "/collection?view=grid",
        "/collection?view=list",
        "/decks?view=list",
        "/users/alice",
        "/binder?show=all",
    )
    got = _each(server, (PHONE, FOLD, LAPTOP), paths, TAGS)
    seen = cut = 0
    for key, tags in got.items():
        for t in tags:
            seen += 1
            cut += t["cut"]
            assert t["h"] <= 26 and t["ws"] == "nowrap" and t["ell"] == "ellipsis", (key, t)
            assert t["out"] <= 1, (key, t)
            assert not t["cut"] or t["title"], (key, t)
    assert seen > 20 and cut, (seen, cut)


# --- #13 a tile's label --------------------------------------------------------------------------

LABS = """() => [...document.querySelectorAll('main .wdg .lab')].filter(l => l.getClientRects().length && l.querySelector('svg.i')).map(l => {
  const i = l.querySelector('svg.i').getBoundingClientRect(), t = l.querySelector('.t');
  if (!t) return {text: l.textContent.trim(), span: false};
  const r = t.getBoundingClientRect(), one = parseFloat(getComputedStyle(t).lineHeight) || 18;
  return {text: t.textContent.trim(), span: true, lines: Math.round(r.height / one),
          beside: r.left >= i.right - 1 && r.top < i.bottom && r.bottom > i.top,
          under: r.top >= i.bottom - 1}; })"""


def test_a_tiles_icon_stays_beside_its_wrapping_label(server: Server) -> None:  # noqa: F811
    got = _each(server, (PHONE, LAPTOP), ("/history/reports/r1", "/proposals/PENDING", "/admin"), LABS)
    wrapped = 0
    for key, labs in got.items():
        # a proposal on a phone is its own page, without the list's tiles
        assert labs or key == (PHONE, "/proposals/PENDING"), key
        for lab in labs:
            assert lab["span"], (key, lab)
            wrapped += lab["lines"] > 1
            # the label wraps beside its icon; only a word wider than the room beside the icon
            # goes whole under it (none at 100% text on these pages)
            assert lab["beside"], (key, lab)
    assert wrapped, got


# --- #18 a list's heading ------------------------------------------------------------------------

LIST_HEAD = """() => { const h = document.querySelector('main .list-head'); if (!h) return null;
  const side = h.querySelector(':scope > .ui-sub'), cap = side && side.querySelector('.ui-cap');
  const words = side && (side.querySelector('.ui-dots > span') || side);
  const mid = (e) => { const r = e.getBoundingClientRect(); return (r.top + r.bottom) / 2; };
  const range = document.createRange(); if (words) range.selectNodeContents(words);
  const wr = words ? range.getBoundingClientRect() : null;
  return {h: Math.round(h.getBoundingClientRect().height), cap: !!cap,
          off: cap && wr ? Math.abs(mid(cap) - (wr.top + wr.bottom) / 2) : 0,
          h2off: wr ? Math.abs(mid(h.querySelector('h2')) - (wr.top + wr.bottom) / 2) : 0}; }"""


def test_a_lists_heading_is_one_height_and_its_order_note_is_level(server: Server) -> None:  # noqa: F811
    got = _each(server, (PHONE, FOLD, LAPTOP), ("/reports", "/history", "/proposals", "/activity"), LIST_HEAD)
    for w in (PHONE, FOLD, LAPTOP):
        heads = {path: got[(w, path)] for path in ("/reports", "/history", "/proposals", "/activity")}
        assert all(heads.values()), heads
        # (Proposals' longer note takes the line under its heading on a phone: two rows there)
        level = {path: h for path, h in heads.items() if h["h2off"] <= 3}
        assert set(heads) - set(level) <= ({"/proposals"} if w < 600 else set()), (w, heads)
        assert len({h["h"] for h in level.values()}) == 1, (w, heads)
        rep = heads["/reports"]
        # "Newest first" and the "Show oldest first" capsule sit on one line, level with each other
        assert rep["cap"] and rep["off"] <= 1.5, (w, rep)


# --- #22 a switch's parts on a phone -------------------------------------------------------------

SEG_PARTS = """() => [...document.querySelectorAll('.hubseg')].filter(n => n.getClientRects().length).map(n => {
  const ks = [...n.children].filter(k => k.getClientRects().length && getComputedStyle(k).position !== 'absolute');
  const ws = ks.map(k => Math.round(k.getBoundingClientRect().width));
  return {cls: n.className, ws: ws, spread: Math.max(...ws) - Math.min(...ws),
          broken: ks.filter(k => k.scrollWidth > k.clientWidth + 1).length,
          out: Math.round(n.getBoundingClientRect().right - document.documentElement.clientWidth)}; })"""


def test_a_switchs_parts_are_equal_in_width_on_a_phone(server: Server) -> None:  # noqa: F811
    # 412 and 480 px are where the Export box's four tabs fit one row only at their own widths
    paths = ("/decks/42/export", "/decks/new", "/scan", "/binder?show=all", "/admin")
    got = _each(server, (360, PHONE, 480), paths, SEG_PARTS)
    for key, segs in got.items():
        assert segs, key
        for s in segs:
            assert s["spread"] <= 1 and not s["broken"] and s["out"] <= 0, (key, s)


# --- #2 the purpose line -------------------------------------------------------------------------

HEAD = """() => { const h = document.querySelector('main .ui-head'); if (!h) return null;
  const b = (s) => { const e = h.querySelector(s) || (s === '.ui-sub' && h.nextElementSibling && h.nextElementSibling.matches('.ui-sub') ? h.nextElementSibling : null);
    return e && e.getClientRects().length ? e.getBoundingClientRect() : null; };
  const t = b('h1'), s = b('.ui-sub'), a = b('.acts');
  return t && s && a ? {subUnderTitle: s.top >= t.bottom - 1 && s.top - t.bottom < 20, subTop: s.top, actsTop: a.top,
                        actsBottom: a.bottom, titleBottom: t.bottom, subBottom: s.bottom,
                        below: (document.querySelector('main .ui-head ~ :not(.ui-sub)') || h).getBoundingClientRect().top} : null; }"""


def test_the_purpose_line_comes_right_under_the_title_before_the_heads_actions(server: Server) -> None:  # noqa: F811
    got = _each(server, (PHONE, FOLD, LAPTOP), ("/collection?view=list", "/collection?view=grid"), HEAD)
    for (w, path), h in got.items():
        assert h, (w, path)
        if w < 600:
            assert h["subUnderTitle"] and h["actsTop"] >= h["subBottom"] - 1, (w, path, h)
        else:
            # beside the title on a wider screen, the purpose line under both
            assert h["actsTop"] < h["titleBottom"] and h["subTop"] >= h["actsBottom"] - 12, (w, path, h)


# --- #33 Alerts ----------------------------------------------------------------------------------


def test_alerts_is_a_narrow_page(server: Server) -> None:  # noqa: F811
    col = """() => { const m = document.querySelector('main'), cs = getComputedStyle(m), r = m.getBoundingClientRect();
      const vw = document.documentElement.clientWidth;
      return {w: Math.round(r.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)),
              lean: Math.round(r.left - (vw - r.right)), cls: m.className}; }"""
    got = _each(server, (LAPTOP,), ("/notifications", "/account"), col)
    alerts, account = got[(LAPTOP, "/notifications")], got[(LAPTOP, "/account")]
    assert "ui-narrow" in alerts["cls"] and alerts["w"] == account["w"] == 44 * 14, got
    assert abs(alerts["lean"]) <= 1, alerts


# --- #6 one segmented switch ---------------------------------------------------------------------

SWITCHES = """() => { const look = (n) => { const cs = getComputedStyle(n); return [cs.backgroundColor, cs.borderTopWidth, cs.borderTopLeftRadius].join(' '); };
  const on = (k) => k.matches('[aria-pressed=true],[aria-selected=true],[aria-current]') || !!k.querySelector('input:checked');
  // the pill is the part's padding box (a transparent edge keeps a 44 px part inside a 44 px track)
  const pill = (k) => { const cs = getComputedStyle(k); return cs.backgroundColor; };
  const all = [...document.querySelectorAll('main .hubseg, main .sizeseg, main .segc, main .ui-seg, main .budget')].filter(n => n.getClientRects().length);
  return all.map(n => { const ks = [...n.children].filter(k => k.getClientRects().length), r = n.getBoundingClientRect();
    const row = n.closest('.rbar, .w-size, .wx-h, .ui-row, .field') || n.parentElement, rr = row.getBoundingClientRect(), rcs = getComputedStyle(row);
    const sameRow = [...row.querySelectorAll('.sizeseg, .segc, .ui-seg')].filter(m => Math.abs(m.getBoundingClientRect().top - r.top) < 30);
    return {cls: n.className, hub: n.classList.contains('hubseg'), track: look(n), h: Math.round(r.height), w: Math.round(r.width),
            on: ks.filter(on).map(pill), off: ks.filter(k => !on(k)).map(pill),
            natural: Math.round(ks.reduce((a, k) => a + k.getBoundingClientRect().width, 0)),
            grow: ks.map(k => getComputedStyle(k).flexGrow).concat(getComputedStyle(n).flexGrow).join(''),
            inBar: !!n.closest('.rbar'),
            // the row's last piece: nothing of the bar is drawn to its right on its line
            last: ![...(n.closest('.rbar') || row).querySelectorAll('button, .btn, select, .msel-btn, label')].some(c => c.getClientRects().length && !n.contains(c)
              && c.getBoundingClientRect().left >= r.right - 1 && Math.abs(c.getBoundingClientRect().top - r.top) < 30),
            toEnd: Math.round(rr.right - parseFloat(rcs.paddingRight) - r.right)}; }); }"""


def test_every_segmented_switch_has_the_one_track_and_pill_look(server: Server) -> None:  # noqa: F811
    paths = ("/decks", "/cards", "/watchlist", "/collection?view=grid", "/decks/new-package", "/precons")
    got = _each(server, (LAPTOP, FOLD, PHONE), paths, SWITCHES)
    hubs = [s for segs in got.values() for s in segs if s["hub"]]
    small = [s for segs in got.values() for s in segs if not s["hub"]]
    assert hubs and len(small) >= 12, got
    track, chosen = hubs[0]["track"], hubs[0]["on"][0]
    for key, segs in got.items():
        for s in segs:
            assert s["track"] == track, (key, s)
            assert s["on"] and all(p == chosen for p in s["on"]), (key, s)
            assert all(p == "rgba(0, 0, 0, 0)" for p in s["off"]), (key, s)
            if not s["hub"]:
                assert s["h"] == 44, (key, s)  # the compact size: the control height


def test_a_view_switch_keeps_its_own_width_at_the_rows_end(server: Server) -> None:  # noqa: F811
    paths = ("/decks", "/cards", "/search?q=sample", "/collection?view=grid", "/precons", "/users/alice")
    got = _each(server, (FOLD, LAPTOP), paths, SWITCHES)
    seen = 0
    for key, segs in got.items():
        for s in segs:
            if not s["inBar"]:
                continue
            seen += 1
            # never stretched: as wide as its parts, and the row's last one ends the row
            assert set(s["grow"]) == {"0"} and s["w"] <= s["natural"] + 8, (key, s)
            assert not s["last"] or abs(s["toEnd"]) <= 1, (key, s)
    assert seen >= 16, got


# --- #5 one search field -------------------------------------------------------------------------

SEARCH = """() => [...document.querySelectorAll('main input[type=search]')].filter(i => i.getClientRects().length && !i.closest('.mtgbox, dialog')).map(i => {
  const f = i.closest('.ui-search'), r = i.getBoundingClientRect(), cs = getComputedStyle(i);
  const glass = f && f.querySelector('svg.i'), g = glass && glass.getBoundingClientRect();
  const go = f && f.querySelector('button[type=submit]'), b = go && go.getBoundingClientRect();
  const form = i.closest('form');
  const others = form ? [...form.querySelectorAll('button[type=submit], button:not([type])')].filter(x => x.getClientRects().length && !(f && f.contains(x)) && /^\\s*search\\s*$/i.test(x.innerText || x.getAttribute('aria-label') || '')) : [];
  return {id: i.id || i.name, field: !!f,
          glassIn: !!g && g.left >= r.left && g.right <= r.left + 48 && g.top >= r.top && g.bottom <= r.bottom,
          textAfterGlass: !!g && parseFloat(cs.paddingLeft) >= g.right - r.left,
          look: [cs.paddingLeft, cs.borderTopLeftRadius, cs.borderTopRightRadius, cs.borderRightWidth, cs.backgroundColor, Math.round(r.height), g ? Math.round(g.left - r.left) : -1, g ? Math.round(g.width) : -1].join(' '),
          go: b ? [Math.round(b.width), Math.round(b.height), Math.round(b.left - r.left)] : null,
          form: !!form, others: others.length}; })"""

FILTER_PAGES = (
    "/forum", "/proposals", "/history", "/reports", "/watchlist", "/binder", "/notifications", "/activity",
    "/admin/users", "/admin/activity", "/admin/models", "/lists/custom", "/collection/wants", "/collection/lists",
)  # fmt: skip
SUBMIT_PAGES = ("/decks", "/search", "/cards", "/precons", "/collection?view=grid", "/guide")


def test_every_search_field_is_the_one_field_with_its_magnifier_inside(server: Server) -> None:  # noqa: F811
    got = _each(server, (PHONE, LAPTOP), FILTER_PAGES + SUBMIT_PAGES, SEARCH)
    looks: dict[str, tuple] = {}
    for (w, path), fields in got.items():
        assert fields, (w, path)
        for f in fields:
            assert f["field"] and f["glassIn"] and f["textAfterGlass"], (w, path, f)
            # no second drawing of the same job: no Search button beside the field
            assert not f["others"], (w, path, f)
            looks.setdefault(f["look"], (w, path, f["id"]))
            if path in SUBMIT_PAGES:
                # the magnifier is the form's submit button, inside the field, a whole tap target
                assert f["form"] and f["go"] and f["go"][0] >= 44 and f["go"][1] >= 44 and f["go"][2] == 0, (
                    w,
                    path,
                    f,
                )
    assert len(looks) == 1, looks


def test_enter_submits_a_search_form_and_the_magnifier_does_too(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, PHONE, "light", exe)
        # the Guide: Enter goes to the results page (its list also fills as you type)
        page.goto(f"{server.base}/guide", wait_until="networkidle")
        box = page.locator("#guide-q")
        box.fill("proposal")
        box.press("Enter")
        page.wait_for_url("**/guide/search?q=proposal")
        assert page.locator("main form.gsearch button", has_text="Search").count() == 0
        # and the magnifier inside the field submits the same form
        page.locator("#guide-q").fill("deck")
        page.locator("main form.gsearch .ui-search button[type=submit]").click()
        page.wait_for_url("**/guide/search?q=deck")
        # Search decks: Enter and the magnifier both submit
        page.goto(f"{server.base}/search", wait_until="networkidle")
        name = page.locator("main .spanel .ui-search input[type=search]")
        name.fill("sample")
        name.press("Enter")
        page.wait_for_url("**/search?*sample*")
        page.goto(f"{server.base}/search", wait_until="networkidle")
        page.locator("main .spanel .ui-search input[type=search]").fill("aesi")
        page.locator("main .spanel .ui-search button[type=submit]").click()
        page.wait_for_url("**/search?*aesi*")
        # a list's filter (no button): Enter submits its form
        page.goto(f"{server.base}/forum", wait_until="networkidle")
        q = page.locator("main .ui-tools .ui-search input")
        q.fill("rules")
        q.press("Enter")
        page.wait_for_url("**/forum?*q=rules*")
        browser.close()
        assert not errors, errors
