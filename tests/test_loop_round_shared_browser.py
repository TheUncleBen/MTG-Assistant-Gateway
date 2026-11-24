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
    got = _each(server, (360, PHONE, FOLD), ("/history", "/admin/users", "/proposals"), SELECTS)
    for key, sels in got.items():
        assert sels, key
        for s in sels:
            assert s["lines"] == 1 and s["ws"] == "nowrap" and s["ell"] == "ellipsis", (key, s)
            # the row wraps a whole control before a choice is cut: every choice is whole
            assert s["cutBy"] <= 1, (key, s)
    # History has three filters: on a phone they share one row, each as wide as its value needs
    # (audit D31: never two and the third alone under them)
    for width in (360, PHONE):
        tops = [s["top"] for s in got[(width, "/history")]]
        assert len(tops) == 3 and len(set(tops)) == 1, (width, tops)


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
        # (a phone folds a list's tiles behind "At a glance"; the report's are always shown)
        assert labs or (key[0] == PHONE and key[1] != "/history/reports/r1"), key
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


# --- #9 the disclosure capsule -------------------------------------------------------------------

CAPSULE = """() => { const b = document.querySelector('main [data-more-filters]');
  if (!b || !b.getClientRects().length) return null;
  // the standard's capsule, drawn fresh beside it
  const ref = document.createElement('button'); ref.type = 'button'; ref.className = 'disc'; ref.textContent = 'More';
  b.parentElement.appendChild(ref);
  const look = (e) => { const cs = getComputedStyle(e), af = getComputedStyle(e, '::after');
    return [cs.backgroundColor, cs.borderTopLeftRadius, cs.borderTopWidth, cs.borderTopColor, cs.fontSize, cs.fontWeight,
            cs.color, Math.round(e.getBoundingClientRect().height), af.content, af.borderBottomWidth].join(' | '); };
  const got = {look: look(b), ref: look(ref), svg: !!b.querySelector('svg'), text: b.innerText.trim(),
               left: Math.round(b.getBoundingClientRect().left - b.closest('.rbar').getBoundingClientRect().left)};
  ref.remove(); return got; }"""


def test_more_filters_is_the_disclosure_capsule(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        for w in (FOLD, LAPTOP):
            browser, page, errors = _page(p, server, sid, w, "light", exe)
            page.goto(f"{server.base}/cards", wait_until="networkidle")
            frame(page)
            cap = page.evaluate(CAPSULE)
            assert cap, w
            # one look for "show more in place": the capsule, its chevron drawn by the capsule itself
            assert cap["look"] == cap["ref"] and not cap["svg"] and cap["text"] == "More filters", (w, cap)
            assert cap["left"] <= 16, (w, cap)  # it leads the controls' row
            if w == FOLD:
                browser.close()
        btn = page.locator("main [data-more-filters]")
        assert btn.get_attribute("aria-expanded") == "false"
        closed = page.evaluate(
            "getComputedStyle(document.querySelector('main [data-more-filters]'), '::after').transform"
        )
        btn.click()
        page.locator("#cs-more-filters[open]").wait_for(timeout=3000)
        assert btn.get_attribute("aria-expanded") == "true"
        frame(page)
        page.wait_for_timeout(250)  # the chevron's turn
        opened = page.evaluate(
            "getComputedStyle(document.querySelector('main [data-more-filters]'), '::after').transform"
        )
        assert opened != closed, (closed, opened)
        browser.close()
        assert not errors, errors


# --- #17 the empty state -------------------------------------------------------------------------

EMPTY = """() => { const es = [...document.querySelectorAll('main .ui-empty')].filter(e => e.getClientRects().length);
  const shown = (e) => e && e.getClientRects().length > 0;
  return {n: es.length, heads: [...document.querySelectorAll('main .ui-table .tr.th')].filter(shown).length,
          old: [...document.querySelectorAll('main .lp-empty, main .yempty')].filter(shown).length,
          looks: es.map(e => { const cs = getComputedStyle(e), t = e.querySelector('.t'), d = e.querySelector('.d'), b = e.querySelector('.btn');
            const ts = t ? getComputedStyle(t) : null, ds = d ? getComputedStyle(d) : null;
            return {look: [cs.backgroundColor, cs.borderTopWidth, cs.borderTopColor, cs.borderTopLeftRadius, cs.paddingTop, cs.paddingLeft,
                           ts && ts.fontWeight, ts && ts.fontSize, ts && ts.color, ds && ds.color, ds && ds.fontSize].join(' | '),
                    title: t ? t.innerText.trim() : '', text: d ? d.innerText.trim() : '',
                    under: t && d ? d.getBoundingClientRect().top >= t.getBoundingClientRect().bottom - 1 : null,
                    btn: b ? [getComputedStyle(b).borderTopWidth, Math.round(b.getBoundingClientRect().height)].join(' ') : null,
                    inPanel: !!e.parentElement.closest('.panel, .card, .ui-card')}; })}; }"""


def test_an_empty_list_is_the_one_empty_state_with_no_table_header_over_it(server: Server) -> None:  # noqa: F811
    paths = ("/lists/custom", "/collection/wants", "/admin/metrics", "/cards")
    got = _each(server, (PHONE, FOLD, LAPTOP), paths, EMPTY)
    looks = set()
    for key, e in got.items():
        assert e["n"] == 1 and not e["heads"] and not e["old"], (key, e)
        one = e["looks"][0]
        # a bold line, then a muted line under it; a panel of its own, never inside another
        assert one["title"] and one["text"] and one["under"] and not one["inPanel"], (key, one)
        looks.add(one["look"])
    assert len(looks) == 1, looks
    titles = {path: got[(PHONE, path)]["looks"][0]["title"] for path in paths}
    assert titles == {
        "/lists/custom": "No custom cards yet",
        "/collection/wants": "No pinned cards yet",
        "/admin/metrics": "No activity counted in the last 30 days",
        "/cards": "Find a card",
    }, titles


# --- #19 a proposal beside the list --------------------------------------------------------------

DRAWER = """() => { const d = document.querySelector('.ui-split.with-panel .ui-drawer'), dr = d.getBoundingClientRect();
  const inView = (e) => { const r = e.getBoundingClientRect(); return r.top >= dr.top - 1 && r.bottom <= Math.min(dr.bottom, innerHeight) + 1 && r.height > 0; };
  const bs = [...d.querySelectorAll('.apply form.guarded button[name=action], .apply form.guarded button[data-guard]')].filter(b => b.getClientRects().length);
  const tiles = [...document.querySelectorAll('.list-only .ui-widgets > .wdg')].filter(t => t.getClientRects().length);
  const rows = {}; tiles.forEach(t => { const y = Math.round(t.getBoundingClientRect().top); rows[y] = (rows[y] || 0) + 1; });
  return {scrolls: d.scrollHeight - d.clientHeight, top: d.scrollTop, n: bs.length, seen: bs.filter(inView).length,
          words: bs.map(b => b.innerText.trim()), perRow: Object.values(rows),
          cover: (() => { const b = bs[0].getBoundingClientRect(), e = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); return !!e && (bs[0].contains(e) || e.contains(bs[0])); })()}; }"""


def test_a_proposal_beside_the_list_keeps_apply_and_reject_in_view(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, LAPTOP, "light", exe)
        # a laptop's window: the panel is shorter than the proposal it shows
        page.set_viewport_size({"width": LAPTOP, "height": 640})
        page.goto(f"{server.base}/proposals/{pending}", wait_until="networkidle")
        frame(page)
        got = page.evaluate(DRAWER)
        assert got["scrolls"] > 40 and got["n"] >= 2, got
        assert got["words"][0].startswith("Apply") and any(w.startswith("Reject") for w in got["words"]), got
        # at the panel's top, half way and at its end: the buttons are whole, inside the panel
        for at in (0, 0.5, 1):
            page.evaluate(
                "(f) => { const d = document.querySelector('.ui-drawer'); d.scrollTop = f * (d.scrollHeight - d.clientHeight); }",
                at,
            )
            frame(page)
            now = page.evaluate(DRAWER)
            assert now["seen"] == now["n"] and now["cover"], (at, now)
        # the tiles beside the open panel go two a row (four narrow columns wrapped their labels)
        assert got["perRow"] and set(got["perRow"]) == {2}, got
        browser.close()
        # without the panel they keep their one row
        browser, page, errors2 = _page(p, server, sid, LAPTOP, "light", exe)
        page.goto(f"{server.base}/proposals", wait_until="networkidle")
        frame(page)
        tiles = page.evaluate(
            "() => new Set([...document.querySelectorAll('main .ui-widgets > .wdg')].map(t => Math.round(t.getBoundingClientRect().top))).size"
        )
        assert tiles == 1, tiles
        browser.close()
        assert not errors and not errors2, errors + errors2


# --- #1 one page column --------------------------------------------------------------------------

COLUMN = """() => { const m = document.querySelector('main'), cs = getComputedStyle(m), r = m.getBoundingClientRect();
  const left = r.left + parseFloat(cs.paddingLeft), right = r.right - parseFloat(cs.paddingRight);
  const shown = (e) => e.getClientRects().length > 0 && getComputedStyle(e).position !== 'absolute';
  const kids = [...m.children].filter(shown).map(e => { const b = e.getBoundingClientRect(); return {cls: e.tagName + '.' + e.className, l: Math.round(b.left - left), r: Math.round(b.right - right)}; });
  const grids = [...m.querySelectorAll('ul.cardgrid, ul.decklist.grid')].filter(shown).filter(g => !g.closest('.mtgbox, .panel')).map(g => { const b = g.getBoundingClientRect();
    return {w: Math.round(b.width), l: Math.round(b.left), gapR: Math.round(document.documentElement.clientWidth - b.right)}; });
  const head = [...m.querySelectorAll('.ui-head, .dkhead, .hero, .hubbar, .spanel, .ui-tools')].filter(shown).map(e => { const b = e.getBoundingClientRect(); return {cls: e.className, l: Math.round(b.left - left), r: Math.round(b.right - right)}; });
  return {left: Math.round(left), width: Math.round(right - left), kids: kids, grids: grids, head: head,
          over: document.documentElement.scrollWidth - document.documentElement.clientWidth}; }"""

WIDE_PAGES = (
    "/", "/decks", "/decks?view=list", "/cards", "/search", "/precons", "/collection?view=grid",
    "/collection?view=list", "/users/alice", "/decks/42/compare",
    "/guide", "/forum", "/proposals", "/history", "/reports", "/watchlist", "/admin", "/activity",
)  # fmt: skip


def test_every_wide_page_is_the_one_centred_column_on_a_laptop(server: Server) -> None:  # noqa: F811
    got = _each(server, (LAPTOP,), (*WIDE_PAGES, "/decks/42"), COLUMN)
    for (_w, path), c in got.items():
        # 1200 px with the gutters: the same left edge on every page, so nothing shifts sideways
        assert (c["left"], c["width"]) == (54, 1172) and c["over"] <= 0, (path, c["left"], c["width"])
        for part in c["head"]:
            assert part["l"] >= 0 and part["r"] <= 0, (path, part)
        if path != "/decks/42":  # (the deck's banner reaches out to the column's own edges)
            for kid in c["kids"]:
                assert kid["l"] >= -4 and kid["r"] <= 4, (path, kid)


def test_on_a_wide_screen_only_a_card_grid_grows_past_the_column(server: Server) -> None:  # noqa: F811
    pages = (
        "/decks",
        "/collection?view=grid",
        "/precons",
        "/users/alice",
        "/search?q=sample",
        "/collection?view=list",
        "/proposals",
    )
    got = _each(server, (1920,), pages, COLUMN)
    grown = 0
    for (_w, path), c in got.items():
        # the page's column, its head, its toolbar and its lists stay where they are on a laptop
        assert c["width"] == 1172 and c["left"] == (1920 - 1172) // 2 and c["over"] <= 0, (
            path,
            c["left"],
            c["width"],
        )
        for part in c["head"]:
            assert part["l"] >= 0 and part["r"] <= 0, (path, part)
        for g in c["grids"]:
            grown += 1
            # the grid alone reaches out, evenly, to the screen's gutters
            assert g["w"] > 1700 and 8 <= g["l"] <= 32 and abs(g["l"] - g["gapR"]) <= 1, (path, g)
    assert grown >= 5, got
    assert not got[(1920, "/collection?view=list")]["grids"] and not got[(1920, "/proposals")]["grids"]
