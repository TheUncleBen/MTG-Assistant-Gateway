"""The site crawl: every page the gateway draws (signed in as an admin, as a plain member and
signed out), with its folds opened and each menu, box, sheet and pick-list opened in turn, at
the phone (320, 360, 412), Fold (717) and computer (900, 1280, 1440) widths, in light and dark,
at 100% text, at a 28 px root (ROOT_200), with the browser's own 16 px default doubled
(BROWSER_200) and with every font doubled in place (TEXT_200, a phone's font scale), on the
website and in the Android app's shell. Each page is loaded once per width class and the text
sizes, themes and shell are applied in place, so the whole crawl costs one page load per page
and width class (final polish, B-13), and every check runs in one evaluation:

- whole words: no word (a heading's too; the browser hyphenates English, and a heading split at a
  syllable where the whole word fits the next line is the same defect, final polish P-1) is drawn
  on more than one line where it would have fitted the box around it (the nearest card, panel,
  tile, list item, menu, box or sheet, less the padding of what lies between): a column, a key
  column of a dl or a label squeezed below its longest word breaks the word although the card has
  room for it;
- text that stays small: at a larger root every piece of text grows with it, except a heading that
  shrinks to fit the viewport, which may grow less but never shrinks (AC1);
- text spilling out of a box: a pill, chip, badge or avatar of a fixed height or width that its
  text outgrows (AC2);
- text cut off: an ellipsis or a clipped overflow where the text had more to say (P-3, P-4, P-5,
  P-19, AC6), except where the full value is reachable (ALLOW_CUT);
- overlapping controls and badges (AC3);
- tap targets under 44 px on a touch screen (P-11), except a link inside running text;
- a box's or sheet's close × alone on a line of its own (P-12);
- anything off the right or left edge of the window, and a page that scrolls sideways.

A name seeded without any break opportunity (forty letters and more) breaks anywhere by design
and is not counted, nor is a word in a code box. A word wider than its box has nowhere else to
go. The dark theme and the app shell are measured again only where they lay the page out
differently (every element's rectangle is compared first, so an identical layout is not measured
twice). Sharded by page group so the crawl runs in parallel."""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

import pytest

from .test_cardsearch_browser import _chromium
from .test_editor_browser import PNG
from .test_overflow_sweep_browser import (
    FOLDERS,
    LONG64,
    LONG_CATS,
    LONGUSER,
    SHEETS_LOADED,
    UNBROKEN,
    UNBROKEN_2,
    _seed,
    server,  # noqa: F401
)
from .test_suggest_browser import Server
from .waits import redrawn

pytest.importorskip("playwright")

PHONE_WIDTHS = [320, 360, 412, 717]
DESKTOP_WIDTHS = [900, 1280, 1440]
ZOOMS = ["none", "root", "browser", "text"]
# where the layers (each kind of menu with the boxes its items open, the page's own boxes, the
# shortcuts box, the filter sheet with its pick-lists) are opened and measured: the audit's four
# variants (a phone at 100%, at a 28 px root and with every font doubled, and a computer)
LAYERS_AT = {320: ["root"], 360: ["none"], 412: ["text"], 1280: ["none"]}
SHARDS = 4

# the names seeded without a break opportunity: they break anywhere by design
HOSTILE = [LONGUSER, UNBROKEN, UNBROKEN_2, FOLDERS[1], LONG64, LONG_CATS[0]]

# where a cut-off is the design and the whole value is reachable: the deck table's type column
# and the Brewer view's rules text (four lines), both whole in the card viewer. A cut-off anywhere
# else is a defect
ALLOW_CUT = ["ul.rows .ty", ".row.brew .tx"]
# a one-line control showing a value a member typed (a drop-down's choice, a category chip, a
# username): a long value (24 letters and more) ends in an ellipsis there, with the whole value in
# the open list or on the page; a short one must show whole
ALLOW_LONG = [".msel-txt", ".tchip", ".uname", ".soc > span", "ul.decklist .info .sub", ".pill.tag > .t"]
# whole or none: the brand's word moves out of sight whole where the bar has no room for it (the
# mark alone is the link Home); a part of it, or an ellipsis, is a defect (AC6)
ALLOW_HIDE = [".brand"]
# a select button's chosen value may end in an ellipsis only where the button already spans its box
# (a one-word value wider than a 320 px phone at 200% text): its list shows the whole value
ALLOW_FULL = [".msel-txt", ".pill.tag > .t"]
# controls drawn over another on purpose: the watch button on a card's picture
ALLOW_OVER = [".cardgrid .wbtn"]

GUIDES = (
    "tut-start tut-link tut-connect tut-first-edit tut-first-sim how-new-deck how-add-cards how-organise "
    "how-approve how-restore how-compare how-export how-search how-scan how-collection how-playtest "
    "how-layout how-app how-signout ref-assistant ref-pages ref-approval ref-keys ref-syntax ref-formats "
    "ref-tools exp-approvals exp-fresh exp-assistant exp-privacy exp-playtest"
).split()
DECK_42 = (
    "?view=grid ?view=list ?view=text ?view=table ?view=brewer ?view=stacks ?view=scroll /help /delist "
    "/registration /package /contest /withdraw /lock /report-to-archidekt /settings /delete /edit /export "
    "/goldfish /compare /made"
).split()
PUBLIC = [
    "/install",
    "/install?for=claude",
    "/install?for=claude-code",
    "/install?for=chatgpt",
    "/install?for=codex",
    "/guide",
    "/guide/search?q=deck",
    *[f"/guide/{t}" for t in GUIDES],
]
ADMIN = [
    "/",
    "/decks",
    "/decks?view=list",
    f"/decks?folder={FOLDERS[0]}",
    "/decks/new",
    "/decks/new-package",
    "/decks/42",
    *[f"/decks/42{s}" for s in DECK_42],
    "/decks/44",
    "/decks/44?view=table",
    "/decks/44/goldfish",
    "/decks/44/export",
    "/decks/45?view=grid",
    "/decks/45/edit",
    "/decks/45/goldfish",
    "/decks/46",
    "/decks/46?view=text",
    "/decks/47",
    "/decks/49",
    "/decks/49?view=scroll",
    "/decks/50",
    "/decks/50?view=stacks",
    "/decks/50/edit",
    "/collection",
    "/collection?view=grid",
    "/collection?view=list",
    "/collection/lists",
    "/collection/wants",
    "/lists/templates",
    "/lists/defaults",
    "/lists/tags",
    "/lists/custom",
    "/lists/custom?new=1",
    "/lists/btags",
    "/watchlist",
    "/search",
    f"/search?q={UNBROKEN_2}",
    "/search?q=alice",
    "/cards",
    "/cards?q=sol",
    "/precons",
    "/users/alice",
    "/users/amy",
    f"/users/{LONGUSER}",
    f"/users/{LONG64}",
    "/history",
    "/history/reports/r1",
    "/history/reports/r1/changes",
    "/history/models",
    "/history/running",
    "/activity",
    "/proposals",
    "/notifications",
    "/forum",
    "/forum/thread/700001",
    "/forum/thread/700002",
    "/forum/thread/700003",
    "/forum/Archidekt/General",
    "/forum/Archidekt/Deck%20Help",
    "/account",
    "/account/details",
    "/account/details/archidekt",
    "/account/details/profile",
    "/account/details/profile?picture=1",
    "/account/details/email",
    "/account/details/defaults",
    "/account/details/backups",
    "/account/details/notifications",
    "/account/details/choices",
    "/account/assistants",
    "/account/assistants/default",
    "/scan",
    "/logout",
    "/data-deleted",
    "/admin",
    "/admin/users",
    "/admin/models",
    "/admin/activity",
    "/admin/settings",
    "/admin/metrics",
    *PUBLIC,
]
# a plain member (no admin group), linked to Archidekt's "amy"
MEMBER = [
    "/",
    "/decks",
    "/decks/43",
    "/collection",
    "/watchlist",
    "/proposals",
    "/history",
    "/account",
    "/account/details",
]


def _site(pending: str) -> list[tuple[str, str]]:
    """Every page with the session it is drawn for: ``admin`` (alice, an admin), ``member`` (a
    plain member) or ``out`` (signed out)."""
    out: list[tuple[str, str]] = [(p, "admin") for p in [*ADMIN, f"/proposals/{pending}"]]
    out += [(p, "member") for p in MEMBER]
    out += [(p, "out") for p in [*PUBLIC, "/data-deleted"]]
    return out


def _member(server: Server) -> str:  # noqa: F811
    """A second member's session: no admin group, linked to Archidekt's amy."""
    import httpx

    sid = secrets.token_urlsafe(32)
    server.db.upsert_user("user-2", email="bea@example.test", name="Bea", preferred_username="bea", groups=[])
    server.db.create_browser_session(sid, "user-2", 3600)
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/account/details").text).group(1)
    r = h.post(
        "/account",
        data={
            "csrf": csrf,
            "action": "link",
            "archidekt_login": "amy",
            "archidekt_password": "pw-amy",
            "accept_risk": "1",
        },
    )
    assert r.status_code == 303, r.text
    return sid


# opens every fold (not a menu, the More sheet or a pick-list, which are layers of their own)
# and shows the glance tiles, as the sweeps do before measuring
OPEN_FOLDS = """() => {
  for (const d of document.querySelectorAll('details:not(.dd):not(.more):not(.pickm)')) d.open = true;
  for (const c of document.querySelectorAll('.glance-tgl')) c.checked = true;
}"""

# the boxes a word is judged against: the nearest one around it must have had room for the word
# (the last three: a text column beside a fixed-width thumbnail or menu button, which is its room)
BOXES = (
    "main, li, .card, .ui-card, .panel, .tile, .ndtile, .wdg, .wcard, details.dd > .menu, .menu.ctxmenu, "
    ".picker, aside, nav, .tabbar, [role=dialog], .mtgbox, .sp-filters, header, footer, .toast, .strip, "
    ".notice, .modal, .sheet, .gf-card, .deckview .c, .home .recent a, .erow > .main, .cc .nm > .name, "
    ".cardc > .tx, .row.brew .bd, .guide .steps li > span"
)

# a layer's history entry still on its way back (every page script's backPending/backLanding)
BACK_PENDING = """() => (window.__hist && window.__hist.pushes > window.__hist.pops) ||
  Object.keys(window).filter(k => k.startsWith('Mtg')).some(k => {
    const o = window[k];
    return !!(o && ((o.backPending && o.backPending()) || (o.backLanding && o.backLanding())));
  })"""

CHECKS = r"""async ([o]) => {
  const q = (s, r) => [...(r || document).querySelectorAll(s)];
  const frame = () => new Promise(r => requestAnimationFrame(r));
  // a frame (its resize observers run at its end), a task, then the next frame
  const tick = () =>
    new Promise(r => requestAnimationFrame(() => setTimeout(() => requestAnimationFrame(r), 0)));
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  // a menu, drop-down, box or sheet gives its history entry back with history.back() when it
  // closes, and the browser takes that on its own time: the next thing opened (or the next page
  // loaded in this tab) must wait for it, or the Back lands on the page before this one
  // (every pushState is counted against the popstates seen: a box's own back() is not told)
  if (!window.__hist) {
    const h = { pushes: 0, pops: 0 }, orig = history.pushState.bind(history);
    history.pushState = function (...a) { h.pushes++; return orig(...a); };
    window.addEventListener('popstate', () => { h.pops++; });
    window.__hist = h;
  }
  const backPending = (BACK_PENDING_JS);
  const quiet = async () => {
    let i = 0;
    for (; i < 150 && backPending(); i++) await sleep(20);
    if (i === 150) window.__hist.pops = window.__hist.pushes;  // an entry nobody gives back
    await tick();
  };
  const T = {}, timed = (k, t0) => { T[k] = (T[k] || 0) + performance.now() - t0; };
  const SKIP = 'code,pre,kbd,script,style,noscript,textarea,option,svg,.sr-only,[hidden],[aria-hidden=true]';
  const INTER = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],' +
    '[role=menuitem]';
  const BADGE = '.ui-lbl,.cn,.views,.badge,.pill,.chip,.avatar,.count,.cnt,.tag,kbd';
  const CLOSE = '.bx,.close,[aria-label="Close"],[aria-label="Close menu"]';
  const OVERLAY = '.mtgbox-scrim,details.dd[open] > .menu,.menu.ctxmenu,.sp-filters.open,.toast,.cardview,' +
    '[popover]';
  const nm = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    else if (typeof el.className === 'string' && el.className.trim())
      s += '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.');
    return s;
  };
  const txt = (el) => ((el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ')).slice(0, 30);
  const rect = (el) => el.getBoundingClientRect();
  const shown = (el) => {
    const r = rect(el);
    return r.width > 0 && r.height > 0 && (!el.checkVisibility || el.checkVisibility());
  };
  const cs = (el) => getComputedStyle(el);
  // the part of an element's rectangle that is in view: what its scrolling or clipping ancestors show
  const visRect = (el) => {
    let { left, top, right, bottom } = rect(el);
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const as = cs(a);
      if (as.overflowX === 'visible' && as.overflowY === 'visible') continue;
      const ar = rect(a);
      if (as.overflowX !== 'visible') { left = Math.max(left, ar.left); right = Math.min(right, ar.right); }
      if (as.overflowY !== 'visible') { top = Math.max(top, ar.top); bottom = Math.min(bottom, ar.bottom); }
    }
    return { left, top, right, bottom, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
  };
  const px = (v) => parseFloat(v) || 0;
  const inProse = (a) => {
    // a link inside running text: its parent block holds other words around it
    if (cs(a).display !== 'inline') return false;
    let p = a.parentElement;
    while (p && cs(p).display === 'inline') p = p.parentElement;
    if (!p) return false;
    const own = (a.textContent || '').trim().length, all = (p.textContent || '').trim().length;
    return all > own + 3;
  };
  const scrollsX = (el) => /auto|scroll/.test(cs(el).overflowX);
  const layerOf = (el) => el.closest(OVERLAY);
  const fixedOf = (el) => {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const p = cs(e).position; if (p === 'fixed' || p === 'sticky') return e;
    }
    return null;
  };
  const sizes = new WeakMap();  // each text-bearing element's font size at 100%
  const hostile = new Set(o.hostile);

  // -- the checks on one root, as laid out now -------------------------------------------------
  const measure = (root, v) => {
    const out = [], seen = new Set();
    const add = (kind, key, msg) => {
      const k = kind + '|' + key; if (!seen.has(k)) { seen.add(k); out.push(msg); }
    };
    const vw = document.documentElement.clientWidth;
    const body = px(cs(document.body).fontSize);
    const rootFs = px(cs(document.documentElement).fontSize);
    // elements with text of their own, once
    const texts = [];
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const p = n.parentElement;
      if (!p || !n.textContent.trim() || !p.getClientRects().length || p.closest(SKIP)) continue;
      const ps = cs(p);
      if (ps.visibility === 'hidden' || px(ps.opacity) === 0) continue;
      if (ps.clip !== 'auto' || cs(p.parentElement).clip !== 'auto') continue;  // for screen readers only
      texts.push([n, p, ps]);
    }
    // (1) whole words
    let t0 = performance.now();
    for (const [n, p, ps] of texts) {
      const box = p.closest(o.boxSel) || document.body;
      let room = box.clientWidth - px(cs(box).paddingLeft) - px(cs(box).paddingRight);
      for (let e = p; e && e !== box; e = e.parentElement) {
        const es = cs(e);
        room -= px(es.paddingLeft) + px(es.paddingRight) + px(es.borderLeftWidth) + px(es.borderRightWidth) +
          px(es.marginLeft) + px(es.marginRight);
      }
      const font = px(ps.fontSize);
      const scale = v.zoom !== 'text' && p.closest('h1,h2,h3,.ui-head') && font > body ? body / font : 1;
      // a word: between spaces, hyphens, dashes and slashes; a mana cost's {G}{U} breaks after a }
      const re = /[^\s\-–—\/}]+/g; let m;
      while ((m = re.exec(n.textContent))) {
        if (m[0].length < 2 || hostile.has(m[0].replace(/^[“"(\[]+|[”"?!.,:;)\]]+$/g, ''))) continue;
        const rg = document.createRange(); rg.setStart(n, m.index); rg.setEnd(n, m.index + m[0].length);
        const rs = [...rg.getClientRects()].filter(r => r.width > 0);
        if (rs.length < 2 || Math.abs(rs[0].top - rs[rs.length - 1].top) <= 2) continue;
        const whole = rs.reduce((t, r) => t + r.width, 0);
        if (whole * scale > room) continue;  // wider than its box: nowhere else to go
        add('word', nm(p) + '|' + m[0], 'word "' + m[0] + '" (' + Math.round(whole) + ' of ' +
          Math.round(room) + 'px) breaks in ' + nm(p) + ' (box ' + nm(box) + ')');
      }
    }
    timed('words', t0); t0 = performance.now();
    // (2) text that stays small at a larger root (headings may shrink to fit, never below 100%)
    if (root === document.body) {
      for (const [n, p, ps] of texts) {
        const fs = px(ps.fontSize);
        // a mana glyph inside its circle, a deck's initial inside its thumbnail: icons, not text
        if (p.closest('.pip, .thumb .ini')) continue;
        if (v.zoom === 'none') { if (!sizes.has(p)) sizes.set(p, fs); continue; }
        if (v.zoom === 'text' || !sizes.has(p)) continue;
        const was = sizes.get(p), grew = fs / was, want = rootFs / o.rootNone;
        const heading = p.closest('h1,h2,h3,.ui-head');
        if (grew < want * 0.75 && !(heading && grew >= 0.999))
          add('small', nm(p), 'text stays ' + Math.round(was) + ' to ' + Math.round(fs) + 'px at a ' +
            Math.round(rootFs) + 'px root in ' + nm(p) + ' "' + txt(p) + '"');
      }
    }
    timed('small', t0); t0 = performance.now();
    // (3) text spilling out of a box of a fixed size: a line of text above, below or beside the
    // padding box of the nearest block around it (overflow visible: nothing clips it)
    for (const [n, p, ps] of texts) {
      const rg = document.createRange(); rg.selectNodeContents(n);
      const lines = [...rg.getClientRects()].filter(r => r.width > 0 && r.height > 0);
      if (!lines.length) continue;
      let e = p;
      while (e && e !== root && cs(e).display === 'inline') e = e.parentElement;
      if (!e || e === root) continue;
      const es = cs(e);
      if (es.overflow !== 'visible' && es.overflowX !== 'visible') continue;
      if (es.textBoxTrim && es.textBoxTrim !== 'none') continue;  // a box trimmed to the glyphs on purpose
      const er = rect(e);
      // pre-wrap keeps a trailing space at a line's end, hanging past the box: half a space's room
      const tol = Math.max(2, px(ps.fontSize) * (/pre-wrap|break-spaces/.test(ps.whiteSpace) ? 0.5 : 0.1));
      const t = Math.min(...lines.map(r => r.top)), b = Math.max(...lines.map(r => r.bottom));
      const l = Math.min(...lines.map(r => r.left)), rt = Math.max(...lines.map(r => r.right));
      if (t < er.top - tol || b > er.bottom + tol || l < er.left - tol || rt > er.right + tol)
        add('spill', nm(e), 'text "' + txt(p) + '" spills out of ' + nm(e) + ' (' + Math.round(rt - l) + 'x' +
          Math.round(b - t) + ' in ' + Math.round(er.width) + 'x' + Math.round(er.height) + ')');
    }
    timed('spill', t0); t0 = performance.now();
    // (4) text cut: an ellipsis or a clipped overflow with more to say
    for (const e of q('*', root)) {
      if (e === root || e.closest(SKIP) || !shown(e)) continue;
      const es = cs(e);
      const clipX = /hidden|clip/.test(es.overflowX), clipY = /hidden|clip/.test(es.overflowY);
      if (!clipX && !clipY) continue;
      const moreX = clipX && e.scrollWidth > e.clientWidth + 1;
      const moreY = clipY && e.scrollHeight > e.clientHeight + 1;
      if (!moreX && !moreY) continue;
      const t = txt(e);
      // a screen-reader-only box (1 px, or a legend's 1 px plus its own padding)
      if (!t || (e.clientWidth <= 8 && e.clientHeight <= 8) || es.clip !== 'auto') continue;
      if (o.allowCut.some(s => e.matches(s))) continue;
      // a value whose control already spans its box: no column could give it more room, and the
      // whole value is one tap away (a select's list)
      if (o.allowFull.some(s => e.matches(s))) {
        const b = e.closest('button,.pill') || e, box = b.parentElement && b.parentElement.closest(o.boxSel);
        if (box) {
          const bs = cs(box), room = box.clientWidth - px(bs.paddingLeft) - px(bs.paddingRight);
          if (rect(b).width >= room - 2) continue;
        }
      }
      // whole or none: every line of its text is either in view whole or out of view whole
      if (o.allowHide.some(s => e.matches(s))) {
        const er = rect(e);
        let part = false;
        const tw = document.createTreeWalker(e, NodeFilter.SHOW_TEXT);
        for (let n = tw.nextNode(); n && !part; n = tw.nextNode()) {
          if (!n.textContent.trim()) continue;
          const rg = document.createRange(); rg.selectNodeContents(n);
          for (const r of rg.getClientRects()) {
            if (r.width === 0) continue;
            const inside = r.left >= er.left - 1 && r.right <= er.right + 1 && r.top >= er.top - 1 &&
              r.bottom <= er.bottom + 1;
            const outside = r.right <= er.left + 1 || r.left >= er.right - 1 || r.bottom <= er.top + 1 ||
              r.top >= er.bottom - 1;
            if (!inside && !outside) { part = true; break; }
          }
        }
        if (!part) continue;
      }
      const full = (e.textContent || '').trim();
      if (o.hostile.some(h => full.startsWith(h))) continue;  // a name seeded without a break
      if (full.length >= 24 && o.allowLong.some(s => e.matches(s))) continue;
      if (e.title && e.title.trim() === full) continue;  // the whole value in its tooltip
      const how = es.textOverflow === 'ellipsis' && moreX ? 'cut with an ellipsis' : 'clipped';
      add('cut', nm(e) + '|' + t, '"' + t + '" ' + how + ' in ' + nm(e) + ' (' + Math.round(e.scrollWidth) +
        'x' + Math.round(e.scrollHeight) + ' in ' + Math.round(e.clientWidth) + 'x' +
        Math.round(e.clientHeight) + ')');
    }
    timed('cut', t0); t0 = performance.now();
    // (5) controls and badges that overlap each other (what their scrolling ancestors show of them;
    // a tile's link under its own ⋮ button is the tile's design)
    const cands = q(INTER + ',' + BADGE, root).filter(e => shown(e) && !e.closest(SKIP) &&
      px(cs(e).opacity) > 0 && cs(e).visibility !== 'hidden');
    // a finger box drawn with padding and a negative margin (an inline link, a check box's label)
    // reaches over its neighbours by design: its visible box is the one that must not overlap
    const hitRect = (e) => {
      const s = cs(e), r = visRect(e);
      if ([s.marginTop, s.marginBottom, s.marginLeft, s.marginRight].every(m => px(m) >= 0)) return r;
      const left = r.left + px(s.paddingLeft), right = r.right - px(s.paddingRight);
      const top = r.top + px(s.paddingTop), bottom = r.bottom - px(s.paddingBottom);
      return { left, right, top, bottom, width: right - left, height: bottom - top };
    };
    const boxes = cands.map(e => ({ e, r: hitRect(e), layer: layerOf(e), fixed: fixedOf(e) }))
      .filter(b => b.r.width > 0 && b.r.height > 0);
    boxes.sort((a, b) => a.r.top - b.r.top);
    const within = (a, b) => a.left <= b.left + 1 && a.right >= b.right - 1 && a.top <= b.top + 1 &&
      a.bottom >= b.bottom - 1;
    for (let i = 0; i < boxes.length; i++) {
      const a = boxes[i];
      for (let j = i + 1; j < boxes.length && boxes[j].r.top < a.r.bottom - 3; j++) {
        const b = boxes[j];
        if (a.e.contains(b.e) || b.e.contains(a.e) || a.layer !== b.layer || a.fixed !== b.fixed) continue;
        if (a.e.tagName === 'LABEL' && a.e.control === b.e) continue;
        if (b.e.tagName === 'LABEL' && b.e.control === a.e) continue;
        if (a.e.tagName === 'A' && within(a.r, b.r) || b.e.tagName === 'A' && within(b.r, a.r)) continue;
        // cards fanned in a stack cover each other by design; a watch button sits on its picture
        if (a.e.closest('.stack') && a.e.closest('.stack') === b.e.closest('.stack')) continue;
        if (o.allowOver.some(s => a.e.matches(s) || b.e.matches(s))) continue;
        // an inline link over two lines: its line boxes, not the rectangle around both
        const inl = (el) => cs(el).display === 'inline';
        if (inl(a.e) || inl(b.e)) {
          const ra = inl(a.e) ? [...a.e.getClientRects()] : [a.r];
          const rb = inl(b.e) ? [...b.e.getClientRects()] : [b.r];
          const hit = ra.some(p => rb.some(q => Math.min(p.right, q.right) - Math.max(p.left, q.left) > 3 &&
            Math.min(p.bottom, q.bottom) - Math.max(p.top, q.top) > 3));
          if (!hit) continue;
        }
        const x = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
        const y = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
        if (x > 3 && y > 3)
          add('overlap', nm(a.e) + '|' + nm(b.e), nm(a.e) + ' "' + txt(a.e) + '" overlaps ' + nm(b.e) +
            ' "' + txt(b.e) + '" by ' + Math.round(x) + 'x' + Math.round(y));
      }
    }
    timed('overlap', t0); t0 = performance.now();
    // (6) tap targets under 44 px on a touch screen (a link inside running text excepted)
    if (o.touch) {
      for (const e of q(INTER, root)) {
        if (!shown(e) || e.closest(SKIP)) continue;
        const es = cs(e);
        if (px(es.opacity) === 0 || es.visibility === 'hidden') continue;
        let target = e;
        if (e.tagName === 'INPUT' && /checkbox|radio/.test(e.type)) {
          const lab = e.closest('label') || (e.labels && e.labels[0]);
          if (lab) target = lab;
        }
        if (e.tagName === 'A' && inProse(e)) continue;
        const r = rect(target);
        if (r.right <= 0) continue;  // placed out of view on purpose (a file input behind its button)
        if (r.width < 43.5 || r.height < 43.5)
          add('tap', nm(e) + '|' + txt(e), nm(e) + ' "' + (txt(e) || e.getAttribute('aria-label') || '') +
            '" is ' + (r.height < 43.5 ? Math.round(r.height * 10) / 10 + 'px tall' :
              Math.round(r.width * 10) / 10 + 'px wide'));
      }
    }
    timed('tap', t0); t0 = performance.now();
    // (7) a close × alone on a line of its own (below everything before it in its header)
    for (const x of q(CLOSE, root)) {
      if (!shown(x)) continue;
      const prev = x.previousElementSibling;
      if (!prev || !shown(prev)) continue;
      const xr = rect(x), pr = rect(prev);
      if (xr.top >= pr.bottom - 1)
        add('x', nm(x.parentElement), 'the close x sits alone under "' + txt(prev) + '" in ' +
          nm(x.parentElement));
    }
    timed('x', t0); t0 = performance.now();
    // (8) off the window's edges, and a page that scrolls sideways
    if (root === document.body) {
      const sw = document.documentElement.scrollWidth;
      if (sw > vw) add('wide', '', 'the page scrolls sideways by ' + (sw - vw) + 'px');
    }
    for (const e of q('*', root)) {
      if (!shown(e) || e.closest(SKIP)) continue;
      const r = rect(e);
      if (r.right <= vw + 1 && r.left >= -1) continue;
      if (r.right <= 0) continue;  // wholly left of the window: hidden on purpose, as .sr-only is
      let clipped = false;
      for (let a = e.parentElement; a && a !== document.body; a = a.parentElement)
        if (cs(a).overflowX !== 'visible') { clipped = true; break; }
      if (clipped) continue;
      add('edge', nm(e), nm(e) + ' "' + txt(e) + '" runs past the window (' + Math.round(r.left) + '..' +
        Math.round(r.right) + ' of ' + vw + ')');
    }
    timed('edge', t0);
    return out;
  };

  // -- the layers: each opened in turn and measured on its own ----------------------------------
  const box = () => document.querySelector('.mtgbox-scrim .mtgbox');
  const settle = async () => {
    await tick();
    for (let i = 0; i < 10 && document.querySelector('.mtgbox .loading'); i++) await sleep(50);
  };
  const closeBox = async () => {
    if (window.MtgBox && window.MtgBox.isOpen()) window.MtgBox.close();
    for (let i = 0; i < 20 && box(); i++) await sleep(20);
    await quiet();
  };
  // a plain button whose label ends with "…" opens a box; a submit button or a link leaves the page
  const dots = (el) => el.tagName === 'BUTTON' && el.type === 'button' &&
    /…\s*$/.test((el.textContent || '').trim());
  const labels = (menu) => q('a,button', menu).map(txt).join('|');
  const layers = async (v) => {
    const out = [], kinds = new Set();
    // what is being opened, kept in window.name: it outlives a click that leaves the page
    const mark = (s) => { window.name = 'opening ' + s; };
    const tag = (label, f) => out.push(f + ' [' + label + ']');
    const t0 = performance.now();
    // the drop-down menus (details.dd), each kind of menu (its items' labels) once, with the box
    // each item ending in "…" opens
    for (const d of q('details.dd')) {
      const sm = d.querySelector(':scope > summary'), panel = d.querySelector(':scope > .menu');
      if (!sm || !panel || !shown(sm)) continue;
      const label = 'menu ' + (sm.getAttribute('aria-label') || txt(sm) || d.className).slice(0, 24);
      mark(label); sm.click(); await tick();
      if (!d.open) d.open = true;
      const kind = 'dd|' + labels(panel);
      if (!kinds.has(kind)) {
        kinds.add(kind);
        for (const f of measure(panel, v)) tag(label, f);
        const done = new Set();
        for (;;) {
          const it = q('button', panel).filter(dots).find(b => !done.has(b));
          if (!it) break;
          done.add(it);
          if (!d.open) { sm.click(); await tick(); if (!d.open) d.open = true; }
          mark(label + ' › ' + txt(it)); it.click(); await settle();
          const b = box();
          if (b) { for (const f of measure(b, v)) tag(label + ' › ' + txt(it), f); await closeBox(); }
        }
      }
      if (d.open) { sm.click(); await tick(); }
      if (d.open) d.removeAttribute('open');
      await quiet();
    }
    // the script-built menus (a row's or card's ⋮), each kind once
    for (const k of q('button[aria-haspopup=menu]')) {
      if (!shown(k) || k.closest('details.dd') || k.type !== 'button') continue;
      const label = 'menu ' + (k.getAttribute('aria-label') || txt(k) || nm(k)).slice(0, 30);
      mark(label); k.click(); await tick();
      const m = document.querySelector('.menu.ctxmenu');
      if (!m) continue;
      const kind = 'ctx|' + labels(m);
      if (kinds.has(kind)) { if (window.MtgMenu) window.MtgMenu.close(false); await quiet(); continue; }
      kinds.add(kind);
      for (const f of measure(m, v)) tag(label, f);
      const done = new Set();
      for (;;) {
        if (!document.querySelector('.menu.ctxmenu')) { k.click(); await tick(); }
        const menu = document.querySelector('.menu.ctxmenu');
        if (!menu) break;
        const it = q('button', menu).filter(dots).find(b => !done.has(txt(b)));
        if (!it) break;
        done.add(txt(it));
        mark(label + ' › ' + txt(it)); it.click(); await settle();
        const b = box();
        if (b) { for (const f of measure(b, v)) tag(label + ' › ' + txt(it), f); await closeBox(); }
        if (document.querySelector('.menu.ctxmenu') && window.MtgMenu) {
          window.MtgMenu.close(false); await quiet();
        }
      }
      if (window.MtgMenu && window.MtgMenu.isOpen()) { window.MtgMenu.close(false); await quiet(); }
    }
    // the boxes the page holds (ui.box), and the buttons that build one
    for (const el of q('.ui-box')) {
      if (!window.MtgBox) break;
      mark('box #' + el.id); window.MtgBox.openElement(el.id); await settle();
      const b = box();
      if (b) {
        for (const f of measure(b, v)) tag('box ' + txt(b.querySelector('h2') || b), f);
        await closeBox();
      }
    }
    for (const bt of q('button').filter(dots)) {
      if (!shown(bt) || bt.closest('details.dd, .menu, .mtgbox, .sp-filters')) continue;
      if (kinds.has('btn|' + txt(bt))) continue;
      kinds.add('btn|' + txt(bt));
      mark('button ' + txt(bt)); bt.click(); await settle();
      const b = box();
      if (b) { for (const f of measure(b, v)) tag('box ' + txt(bt), f); await closeBox(); }
    }
    // the keyboard shortcuts box
    mark('the shortcuts box');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: '?', bubbles: true, cancelable: true }));
    await settle();
    if (box()) { for (const f of measure(box(), v)) tag('shortcuts box', f); await closeBox(); }
    // the filter sheet (a phone), with each of its pick-lists opened
    const open = document.querySelector('.sp-open');
    if (open && shown(open)) {
      mark('the filter sheet'); open.click(); await tick();
      const sheet = document.querySelector('.sp-filters.open');
      if (sheet) {
        for (const f of measure(sheet, v)) tag('filter sheet', f);
        for (const d of q('details.pickm', sheet)) {
          d.open = true; await tick();
          for (const f of measure(d, v)) tag('filter sheet › ' + txt(d.querySelector('summary')), f);
          d.open = false; await tick();
        }
        if (window.MtgSearchPanel) window.MtgSearchPanel.closeSheet(); else sheet.classList.remove('open');
        await quiet();
      }
    }
    await quiet();
    window.name = '';
    timed('layers', t0);
    return out;
  };

  // -- the text sizes, themes and the app shell, applied in place --------------------------------
  const html = document.documentElement;
  const rootInline = [html.style.getPropertyValue('font-size'), html.style.getPropertyPriority('font-size')];
  const restoreRoot = () => rootInline[0] ?
    html.style.setProperty('font-size', rootInline[0], rootInline[1]) :
    html.style.removeProperty('font-size');
  const zoomOn = (zoom) => {
    if (zoom === 'root') { html.style.setProperty('font-size', '28px', 'important'); return restoreRoot; }
    if (zoom === 'browser') { html.style.setProperty('font-size', '200%', 'important'); return restoreRoot; }
    if (zoom === 'text') {
      const els = [html, ...q('body, body *')];
      const saved = els.map(el => [el, el.style.getPropertyValue('font-size'),
        el.style.getPropertyPriority('font-size')]);
      const fs = els.map(el => px(cs(el).fontSize));
      els.forEach((el, i) => {
        if (fs[i]) el.style.setProperty('font-size', fs[i] * 2 + 'px', 'important');
      });
      return () => saved.forEach(([el, val, pr]) => val ? el.style.setProperty('font-size', val, pr) :
        el.style.removeProperty('font-size'));
    }
    return () => {};
  };
  const fingerprint = () => q('body *').map(el => {
    const r = rect(el); return (r.x | 0) + ',' + (r.y | 0) + ',' + (r.width | 0) + ',' + (r.height | 0);
  }).join(';');
  const theme0 = html.getAttribute('data-theme');
  const all = [];
  for (const zoom of o.zooms) {
    let t0 = performance.now();
    const undo = zoomOn(zoom);
    const v = { zoom, theme: 'light', app: false };
    // a text size set in place changes no element's size, so the scripts that size themselves to
    // the text (the tab bar's icons mode) hear a resize, as a real change (a reload) reaches them
    html.setAttribute('data-theme', 'light'); window.dispatchEvent(new Event('resize')); await tick();
    timed('zoom', t0); t0 = performance.now();
    const fp = fingerprint();
    timed('fp', t0);
    let found = measure(document.body, v);
    if (o.layers.includes(zoom)) found.push(...await layers(v));
    all.push(...found.map(f => zoom + ' light: ' + f));
    t0 = performance.now();
    html.setAttribute('data-theme', 'dark'); await tick();
    const dark = fingerprint() !== fp;
    timed('fp', t0);
    if (dark) all.push(...measure(document.body, { ...v, theme: 'dark' }).map(f => zoom + ' dark: ' + f));
    if (o.app) {
      t0 = performance.now();
      document.body.classList.add('app'); await tick();
      const app = fingerprint() !== fp;
      timed('fp', t0);
      if (app) all.push(...measure(document.body, { ...v, app: true }).map(f => zoom + ' app: ' + f));
      document.body.classList.remove('app');
    }
    if (theme0 === null) html.removeAttribute('data-theme'); else html.setAttribute('data-theme', theme0);
    t0 = performance.now();
    undo(); window.dispatchEvent(new Event('resize')); await tick();
    timed('zoom', t0);
  }
  if (o.profile) {
    const ms = Object.fromEntries(Object.entries(T).map(([k, t]) => [k, Math.round(t)]));
    all.push('time: ' + JSON.stringify(ms));
  }
  return all;
}"""
CHECKS = CHECKS.replace("BACK_PENDING_JS", BACK_PENDING)


def _goto(page, url: str):  # noqa: ANN001, ANN202
    """Loads a page, waiting out a busy answer (the fake Archidekt's pace) a few times. A
    history entry a closed layer of the page before is still giving back (history.back() on
    its way) is waited out first: loaded over it, the Back would land on that earlier page."""
    from playwright.sync_api import Error as PlaywrightError

    try:
        page.wait_for_function(f"() => !({BACK_PENDING})()", timeout=5_000)
    except PlaywrightError:
        pass
    for attempt in range(5):
        r = page.goto(url, wait_until="networkidle")
        if r is not None and r.status in (429, 503) and attempt < 4:
            # fixed wait: the server's busy answer (429/503) asks for a pause before the next try
            page.wait_for_timeout(1500)
            continue
        return r
    return r


@pytest.mark.parametrize("shard", range(SHARDS))
def test_every_page_holds_its_text_and_controls_at_every_size(server: Server, shard: int) -> None:  # noqa: F811
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, pending = _seed(server)
    sids = {"admin": sid, "member": _member(server), "out": ""}
    # the pace cap and a member's Archidekt budget are for members' pages, not for a crawl of every
    # page at once
    pacer = server.app.state.gateway.archidekt.pacer
    pacer.max_per_minute = 0
    pacer.page_reserve = 0
    server.app.state.gateway.decks.archidekt_budget.per_window = 10**6
    pages = _site(pending)[shard::SHARDS]
    problems: list[str] = []
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        contexts: dict[tuple[str, bool], object] = {}

        def page_for(state: str, touch: bool):  # noqa: ANN202
            key = (state, touch)
            if key not in contexts:
                widths = PHONE_WIDTHS if touch else DESKTOP_WIDTHS
                ctx = browser.new_context(
                    viewport={"width": widths[0], "height": 860}, has_touch=touch, reduced_motion="reduce"
                )
                if sids[state]:
                    ctx.add_cookies([{"name": "mtg_session", "value": sids[state], "url": server.base}])
                ctx.route(
                    re.compile(r"https://cards\.scryfall\.io/.*"),
                    lambda r: r.fulfill(content_type="image/png", body=PNG),
                )
                # a deck view's long-poll would hold "networkidle" (see the overflow sweep)
                ctx.route(re.compile(r"/decks/[^/]+/check(\?|$)"), lambda r: r.abort())
                pg = ctx.new_page()
                pg.on("pageerror", lambda e: errors.append(str(e)))
                contexts[key] = pg
            return contexts[key]

        for path, state in pages:
            for touch, widths in ((True, PHONE_WIDTHS), (False, DESKTOP_WIDTHS)):
                page = page_for(state, touch)
                server.db.delete_prefs("user-1", prefixes=("view.",))
                r = _goto(page, f"{server.base}{path}")
                assert r is not None and r.status == 200, (path, state, r and r.status)
                page.wait_for_function(SHEETS_LOADED, timeout=10_000)
                root_none = page.evaluate(
                    "() => parseFloat(getComputedStyle(document.documentElement).fontSize)"
                )
                page.evaluate(OPEN_FOLDS)
                seen: dict[str, int] = {}
                for w in widths:
                    page.set_viewport_size({"width": w, "height": 860})
                    redrawn(page)
                    opts = {
                        "touch": touch,
                        "app": touch,
                        "boxSel": BOXES,
                        "hostile": HOSTILE,
                        "allowCut": ALLOW_CUT,
                        "allowLong": ALLOW_LONG,
                        "allowHide": ALLOW_HIDE,
                        "allowFull": ALLOW_FULL,
                        "allowOver": ALLOW_OVER,
                        "zooms": ZOOMS,
                        "layers": LAYERS_AT.get(w, []),
                        "rootNone": root_none,
                    }
                    try:
                        found = page.evaluate(CHECKS, [opts])
                    except PlaywrightError as exc:  # a control's click left the page
                        try:
                            what = page.evaluate("window.name") or "?"
                            page.evaluate("window.name = ''")
                        except PlaywrightError:
                            what = "?"
                        problems.append(
                            f"{path} [{state}] @{w}px: {what} left the page for {page.url} ({exc})"
                        )
                        page.goto(f"{server.base}{path}", wait_until="networkidle")
                        continue
                    for f in found:
                        variant, _, what = f.partition(": ")
                        key = re.sub(r"\d+(\.\d+)?", "#", what)  # the same defect, whatever its size
                        if key in seen:
                            seen[key] += 1
                            continue
                        seen[key] = 1
                        problems.append(f"{path} [{state}] @{w}px {variant}: {what}")
        browser.close()
    if os.environ.get("SWEEP_OUT"):
        Path(f"{os.environ['SWEEP_OUT']}-{shard}.txt").write_text("\n".join(problems))
    assert errors == []
    more = f"\n… {len(problems)} in all" if len(problems) > 80 else ""
    assert problems == [], "\n".join(problems[:80]) + more
