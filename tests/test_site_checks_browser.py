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
- anything off the right or left edge of the window, and a page that scrolls sideways;
- a dot between facts, or the "/" of a count, at the end of a line or the start of one (P-15);
- a text box lower than its text and some air, or a hint wider than its box (P-5, P-19, AD3);
- a pill or label on two lines at 100% text (P-8);
- a switch with a part cut off or scrolled away, parts of unequal widths or rows of unequal
  counts (P-9, NA4);
- a band of the page between the top bar and a page's header (P-16), and a line saying only
  "Default" (P-10);
- a box's title running under its content (NA1), and in the shortcuts box a key not beside or
  above its own words, or beside the next pair's (NA2, P-7);
- a switch on more rows than it needs: its parts fit one row at their own widths (NC1);
- a chart's labels running into each other, cut at its edge, on its axis line or larger than the
  words around it (NC2);
- a button with an icon whose label wraps, its content off its middle (P-13).

A name seeded without any break opportunity (forty letters and more) breaks anywhere by design
and is not counted, nor is a word in a code box. A word wider than its box has nowhere else to
go. The dark theme and the app shell are measured again only where they lay the page out
differently (every element's rectangle is compared first, so an identical layout is not measured
twice). Sharded by page group so the crawl runs in parallel."""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

import pytest

from .test_cardsearch_browser import _chromium
from .test_editor_browser import PNG
from .test_overflow_sweep_browser import (
    CARDS,
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
SHARDS = 12
FAMILY_RUN = 8  # the most pages of one family (_family) in a row on one shard

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


def _family(page: tuple[str, str]) -> tuple[str, str]:
    """The pages that share their layers (a deck's views and boxes, the guide, the install
    pages): the same session and the path's first two parts (the first alone for the guide's and
    the install pages)."""
    path, state = page
    parts = path.split("?")[0].split("/")
    return state, "/".join(parts[:2] if parts[1] in ("guide", "install") else parts[:3])


def _shards(pending: str, n: int = SHARDS) -> list[list[tuple[str, str]]]:
    """Every page, in shards of about equal work. A family's pages go to one shard, in runs of
    at most FAMILY_RUN, so a layer they share (a box, a menu at the same place) is measured once
    there (CHECKS' layerKey, B-13): a run's first page costs about twice what each later one does,
    and a deck's page (its views, menus and boxes) about three times another page. The costliest
    runs are placed first, each on the shard with the least work so far."""
    runs: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for page in _site(pending):
        runs.setdefault(_family(page), []).append(page)
    chunks = [fam[i : i + FAMILY_RUN] for fam in runs.values() for i in range(0, len(fam), FAMILY_RUN)]

    def cost(chunk: list[tuple[str, str]]) -> float:
        deck = 3 if re.match(r"/decks/\d", chunk[0][0]) else 1
        return deck * (1 + 0.5 * (len(chunk) - 1))

    chunks.sort(key=cost, reverse=True)  # a stable sort: the site's order among runs as costly
    shards: list[list[tuple[str, str]]] = [[] for _ in range(n)]
    work = [0.0] * n
    for chunk in chunks:
        i = min(range(n), key=lambda k: (work[k], k))
        shards[i] += chunk
        work[i] += cost(chunk)
    return shards


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
  const FIELD = 'input:not([type]),input[type=text],input[type=search],input[type=email],' +
    'input[type=number],input[type=url],input[type=password],select';
  const PILL = '.ui-lbl,.pill,.chip,.badge';
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
  // an element's computed style: a live object, so one per element serves every later read (B-13)
  const styles = new WeakMap();
  const cs = (el) => {
    let st = styles.get(el);
    if (!st) { st = getComputedStyle(el); styles.set(el, st); }
    return st;
  };
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
    const rooms = new Map();  // each text's parent: its box and the room the box leaves it
    for (const [n, p, ps] of texts) {
      let br = rooms.get(p);
      if (!br) {
        const bx = p.closest(o.boxSel) || document.body;
        let rm = bx.clientWidth - px(cs(bx).paddingLeft) - px(cs(bx).paddingRight);
        for (let e = p; e && e !== bx; e = e.parentElement) {
          const es = cs(e);
          rm -= px(es.paddingLeft) + px(es.paddingRight) + px(es.borderLeftWidth) + px(es.borderRightWidth) +
            px(es.marginLeft) + px(es.marginRight);
        }
        br = [bx, rm];
        rooms.set(p, br);
      }
      const [box, room] = br;
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
    timed('edge', t0); t0 = performance.now();
    // (9) a dot between facts, or the "/" of a count, left at the end of a line or the start of
    // one (P-15): each block holding one is read once, its shown characters in order
    const blockOf = (el) => {
      while (el && el !== root && cs(el).display.startsWith('inline')) el = el.parentElement;
      return el || root;
    };
    const dotted = new Set(), textOf = new Set(texts.map(([n]) => n));
    // (a control's chosen value, a drop-down's or a button's, is one value, not a line of facts)
    const CONTROL = 'button,select,[role=button],.msel-btn';
    for (const [n, p] of texts) {
      if (!/·| \/ /.test(n.textContent) || p.closest(CONTROL)) continue;
      const block = blockOf(p);
      if (dotted.has(block)) continue;
      dotted.add(block);
      const chars = [], bw = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
      for (let m = bw.nextNode(); m; m = bw.nextNode()) {
        if (!textOf.has(m) || blockOf(m.parentElement) !== block) continue;
        for (let i = 0; i < m.textContent.length; i++) chars.push([m, i, m.textContent[i]]);
      }
      const at = (k) => {
        const r = document.createRange(); r.setStart(chars[k][0], chars[k][1]);
        r.setEnd(chars[k][0], chars[k][1] + 1);
        return [...r.getClientRects()].find(x => x.width > 0 || x.height > 0) || null;
      };
      for (let k = 0; k < chars.length; k++) {
        const c = chars[k][2];
        const slash = c === '/' && k > 0 && k < chars.length - 1 && /\s/.test(chars[k - 1][2]) &&
          /\s/.test(chars[k + 1][2]);
        if (c !== '·' && !slash) continue;
        const me = at(k);
        if (!me) continue;
        let a = k - 1, b = k + 1;
        while (a >= 0 && /\s/.test(chars[a][2])) a--;
        while (b < chars.length && /\s/.test(chars[b][2])) b++;
        const ra = a >= 0 ? at(a) : null, rb = b < chars.length ? at(b) : null;
        const around = chars.slice(Math.max(0, k - 14), k + 14).map(x => x[2]).join('').replace(/\s+/g, ' ')
          .trim();
        const where = !ra ? '' : Math.abs(ra.top - me.top) > me.height / 2 ? 'starts' :
          rb && Math.abs(rb.top - me.top) > me.height / 2 ? 'ends' : '';
        if (where) add('dot', nm(block) + '|' + c, '"' + c + '" ' + where + ' a line in ' + nm(block) + ' "' +
          around + '"');
      }
    }
    timed('dot', t0); t0 = performance.now();
    // (10) a text box too low for its text, or a hint (placeholder) wider than the box (P-5,
    // P-19, AD3): the box grows with its text from 44 px, and a long hint gives way to a short one
    const canvas = document.createElement('canvas').getContext('2d');
    for (const el of q(FIELD, root)) {
      if (!shown(el) || el.closest(SKIP) || el.classList.contains('msel-native')) continue;
      const es = cs(el);
      if (px(es.opacity) === 0 || es.visibility === 'hidden') continue;
      const fs = px(es.fontSize);
      if (el.clientHeight < fs * 1.5 - 1)
        add('field', nm(el), nm(el) + ' "' + (el.name || el.placeholder) + '" is ' + el.clientHeight +
          'px inside for its ' + Math.round(fs) + 'px text');
      if (!el.placeholder || el.value) continue;
      canvas.font = es.fontStyle + ' ' + es.fontWeight + ' ' + es.fontSize + ' ' + es.fontFamily;
      const need = canvas.measureText(el.placeholder).width;
      const avail = el.clientWidth - px(es.paddingLeft) - px(es.paddingRight);
      if (need > avail + 1)
        add('hint', nm(el) + '|' + el.placeholder, 'the hint "' + el.placeholder + '" of ' + nm(el) +
          ' is cut (' + Math.round(need) + ' of ' + Math.round(avail) + 'px)');
    }
    timed('field', t0); t0 = performance.now();
    // (11) a pill or label on two lines at 100% text (P-8: "In binder · you own 3" at 412 px)
    if (v.zoom === 'none') {
      for (const e of q(PILL, root)) {
        if (!shown(e) || e.closest(SKIP) || e.parentElement.closest(PILL)) continue;
        const tops = [];
        const tw = document.createTreeWalker(e, NodeFilter.SHOW_TEXT);
        for (let n = tw.nextNode(); n; n = tw.nextNode()) {
          if (!n.textContent.trim() || n.parentElement.closest(SKIP)) continue;
          const rg = document.createRange(); rg.selectNodeContents(n);
          for (const r of rg.getClientRects()) if (r.width > 0 && !tops.some(t => Math.abs(t - r.top) < 4))
            tops.push(r.top);
        }
        if (tops.length > 1)
          add('pill', nm(e) + '|' + txt(e), nm(e) + ' "' + txt(e) + '" takes ' + tops.length +
            ' lines at 100% text');
      }
    }
    timed('pill', t0); t0 = performance.now();
    // (12) a switch (P-9, NA4): no part cut off or scrolled out of view, the parts of one width,
    // and the rows of as many parts each (one apart at most, never one alone under a full row)
    for (const s of q('.hubseg', root)) {
      if (!shown(s)) continue;
      const kids = [...s.children].filter(k => k.getClientRects().length && cs(k).position !== 'absolute');
      if (kids.length < 2) continue;
      const sr = rect(s), rows = [];
      const name = 'the switch ' + (s.getAttribute('aria-label') || nm(s)) + ' (' + kids.map(txt).join('|') +
        ')';
      for (const k of kids) {
        const r = rect(k);
        if (r.left < sr.left - 1 || r.right > sr.right + 1) add('seg', name + '|cut', name + ': "' + txt(k) +
          '" is cut off');
        const row = rows.find(x => Math.abs(x.top - r.top) < 3);
        if (row) row.n++; else rows.push({ top: r.top, n: 1 });
      }
      if (s.scrollWidth > s.clientWidth + 1) add('seg', name + '|scroll', name + ' scrolls sideways');
      const counts = rows.map(r => r.n), most = Math.max(...counts);
      if (most - Math.min(...counts) > 1 || (most > 1 && counts[counts.length - 1] === 1))
        add('seg', name + '|rows', name + ' has rows of ' + counts.join('+'));
      // each part at its own one-line width (a hidden copy laid out in one row), against the room
      // its line gives it: a switch whose parts fit one row is on one row (NC1), and a row of parts
      // as wide as their words is right only where equal parts would not fit it
      const ps = cs(s.parentElement);
      const room = Math.min(s.parentElement.clientWidth - px(ps.paddingLeft) - px(ps.paddingRight),
        document.documentElement.clientWidth) - px(cs(s).paddingLeft) - px(cs(s).paddingRight) -
        px(cs(s).borderLeftWidth) - px(cs(s).borderRightWidth);
      const c = s.cloneNode(true), gap = px(cs(s).columnGap);
      // (its own class kept, so its parts keep their padding; laid out in one row whatever it is)
      c.classList.remove('seg-grid', 'seg-row', 'more-start', 'more-end');
      c.style.cssText = 'position:absolute;visibility:hidden;display:flex;flex-wrap:nowrap;left:0;top:0;' +
        'width:auto;max-width:none';
      for (const k of c.children)
        k.style.cssText += ';flex:none!important;white-space:nowrap!important;min-width:auto!important';
      s.parentElement.appendChild(c);
      let sum = 0, widest = 0, n = 0;
      for (const k of c.children) {
        const w = k.getBoundingClientRect().width;
        if (w && getComputedStyle(k).position !== 'absolute') { sum += w; widest = Math.max(widest, w); n++; }
      }
      c.remove();
      if (rows.length > 1 && sum + gap * (n - 1) <= room)
        add('seg', name + '|stack', name + ' takes ' + rows.length + ' rows where its parts fit one');
      const ws = kids.map(k => rect(k).width);
      const equalFit = n * widest + gap * (n - 1) <= room + 0.5;
      if (Math.max(...ws) - Math.min(...ws) > 2 && (rows.length > 1 || equalFit))
        add('seg', name + '|widths', name + ' has parts of ' + ws.map(Math.round).join('/') + 'px');
    }
    timed('seg', t0); t0 = performance.now();
    // (13) a band of the page between the top bar and a page's header (P-16: 14 px on a phone's
    // deck page), and (14) a line saying only "Default" (P-10: 35 on the admin settings)
    if (root === document.body) {
      const bar = document.querySelector('.topbar'), head = document.querySelector('main .banner');
      if (bar && head && shown(head) && scrollY === 0) {
        const gap = rect(head).top - rect(bar).bottom;
        if (Math.abs(gap) > 1) add('band', '', 'a ' + Math.round(gap) + 'px band between the top bar and ' +
          nm(head));
      }
    }
    for (const [n, p] of texts) {
      if (!/^\(?Default\)?\.?$/.test(n.textContent.trim()) || p.closest(CONTROL)) continue;
      const block = blockOf(p);
      if (block !== root && /^\(?Default\)?\.?$/.test((block.textContent || '').trim()))
        add('default', nm(block), 'a line saying only "' + n.textContent.trim() + '" in ' + nm(block));
    }
    timed('band', t0); t0 = performance.now();
    // (15) a box's title kept above its content (NA1: under it at 200% text in a tall box)
    for (const b of root.matches('.mtgbox') ? [root] : q('.mtgbox', root)) {
      const bh = b.querySelector(':scope > .bh'), h = bh && bh.querySelector('h2');
      if (!h || !shown(h)) continue;
      let next = bh.nextElementSibling;
      while (next && !next.getClientRects().length) next = next.nextElementSibling;
      if (next && rect(h).bottom > rect(next).top + 1)
        add('title', nm(b), 'the box title "' + txt(h) + '" runs ' +
          Math.round(rect(h).bottom - rect(next).top) + 'px under ' + nm(next));
    }
    // (16) the shortcuts box: each key beside its own words or above them, the words beside a key
    // 12em wide at least (P-7), and the next key on lines of its own (NA2)
    for (const dl of q('dl.keys', root)) {
      if (!shown(dl)) continue;
      const dts = q('dt', dl), em = px(cs(dl).fontSize);
      dts.forEach((dt, i) => {
        const dd = dt.nextElementSibling, key = txt(dt);
        if (!dd || dd.tagName !== 'DD') return;
        const k = rect(dt), d = rect(dd);
        const beside = Math.abs(k.top - d.top) < 0.6 * em && d.left >= k.right - 1;
        const under = d.top >= k.bottom - 1 && d.left <= k.left + 1;
        if (!beside && !under) add('keys', key, 'the key "' + key + '" is not beside or above its words');
        if (beside && d.width < 12 * em - 1) add('keys', key + '|w', 'the words of the key "' + key +
          '" are squeezed to ' + Math.round(d.width) + 'px');
        const nx = dts[i + 1];
        if (nx && rect(nx).top < Math.max(k.bottom, d.bottom) - 1)
          add('keys', key + '|next', 'the key "' + txt(nx) + '" sits beside the words of "' + key + '"');
      });
    }
    timed('title', t0); t0 = performance.now();
    // (17) a chart's labels: none over another, none out of the chart's box or past its edge, the
    // turn labels under the axis line, none larger than the words around it (NC2), none smaller
    // than the chart's own label size or those words, when they are smaller (NC3)
    const meet = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5 &&
      Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5;
    for (const sv of q('.chart svg', root)) {
      if (!shown(sv)) continue;
      const ch = sv.closest('.chart'), cr = rect(ch), sr = rect(sv), body = px(cs(ch).fontSize);
      const title = txt(ch.querySelector('h3') || ch);
      const ts = q('text', sv).filter(t => getComputedStyle(t).display !== 'none' &&
        t.getClientRects().length);
      const rs = ts.map(rect), axis = sv.querySelector('line.axis'), ar = axis && rect(axis);
      ts.forEach((t, i) => {
        const r = rs[i], name = 'the chart "' + title + '": "' + t.textContent + '"';
        for (let j = i + 1; j < ts.length; j++)
          if (meet(r, rs[j])) add('chart', title + '|' + t.textContent + '|' + ts[j].textContent,
            name + ' runs into "' + ts[j].textContent + '"');
        if (r.left < sr.left - 1 || r.right > sr.right + 1 || r.top < sr.top - 1 ||
          r.bottom > sr.bottom + 1 || r.left < cr.left - 1 || r.right > cr.right + 1)
          add('chart', title + '|' + t.textContent + '|out', name + ' is cut at its edge');
        const size = px(getComputedStyle(t).fontSize) * t.getScreenCTM().a;
        if (size > body + 0.5) add('chart', title + '|' + t.textContent + '|size', name + ' is ' +
          Math.round(size) + 'px beside ' + body + 'px words');
        const want = Math.min(px(cs(sv).fontSize), body);
        if (size < want - 0.5) add('chart', title + '|' + t.textContent + '|small', name + ' is ' +
          size.toFixed(1) + 'px, under its ' + want + 'px size');
        if (ar && t.classList.contains('x') && r.top < ar.bottom + 1)
          add('chart', title + '|' + t.textContent + '|axis', name + ' sits on the axis line');
      });
    }
    // (18) a button with an icon whose label wraps: its icon and label in its middle (P-13); a
    // button laid out from its start (a menu's rows) is not centred by design
    for (const b of q('button, .btn', root)) {
      const sv = b.querySelector(':scope > svg');
      if (!sv || !rect(sv).width || !shown(b) || b.closest('[hidden]')) continue;
      const bj = cs(b).display.includes('flex') ? cs(b).justifyContent : cs(b).textAlign;
      if (!/center/.test(bj)) continue;
      const tops = new Set(), rights = [], lefts = [];
      for (const n of b.childNodes) {
        const span = n.nodeType === 1 && n.tagName === 'SPAN' && !n.classList.contains('sr-only');
        if (n.nodeType !== 3 && !span) continue;
        const rg = document.createRange(); rg.selectNodeContents(n);
        for (const x of rg.getClientRects())
          if (x.width > 0) { tops.add(Math.round(x.top)); rights.push(x.right); lefts.push(x.left); }
      }
      if (tops.size < 2) continue;
      const br = rect(b), bs = cs(b);
      const l = Math.min(rect(sv).left, ...lefts) - br.left - px(bs.borderLeftWidth);
      const r = br.right - px(bs.borderRightWidth) - Math.max(rect(sv).right, ...rights);
      if (Math.abs(l - r) > 6) add('centre', txt(b), 'the button "' + txt(b) + '" sits ' + Math.round(l) +
        'px from its left and ' + Math.round(r) + 'px from its right');
    }
    timed('chart', t0);
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
  // a layer measured before on another page of this crawl: the same opener (its markup, and for a
  // menu drawn at its button that button's place) on a page drawn from the same style sheets and
  // scripts, at the same width, height, text size, theme and shell, opens the same layer, laid out
  // the same; its findings were taken there (B-13). Each key is hashed (FNV-1a).
  const layerSeen = new Set(o.layerSeen || []), layerNew = [];
  const hash = (str) => {
    let h = 0x811c9dc5;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return h.toString(36) + '.' + str.length.toString(36);
  };
  const pageSig = (v) => [o.state, o.touch, innerWidth, innerHeight, v.zoom, v.theme, v.app,
    [...document.documentElement.attributes].map(a => a.name + '=' + a.value).join(' '),
    document.body.className, (document.querySelector('.mtgbox-scrim .mtgbox') || {}).id,
    q('link[rel=stylesheet],script[src]').map(e => e.href || e.src).join(' ')]
    .join('|');
  // the form tokens a page draws afresh each time it loads (csrf, the clickguard's "shown" stamp)
  const untokened = (html) => html
    .replace(/(<input[^>]*name="(?:csrf|shown)"[^>]*value=")[^"]*/g, '$1')
    .replace(/(<input[^>]*value=")[^"]*("[^>]*name="(?:csrf|shown)")/g, '$1$2');
  const layerKey = (v, what) => hash(pageSig(v) + '|' + untokened(what));
  const measuredBefore = (key, kind) => {
    const hit = layerSeen.has(key);
    T[(hit ? 'hit-' : 'new-') + kind] = (T[(hit ? 'hit-' : 'new-') + kind] || 0) + 1000;  // counted, as ms
    if (hit) return true;
    layerSeen.add(key); layerNew.push(key);
    return false;
  };
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
      const dr = rect(sm), at = [dr.x, dr.y, dr.width, dr.height].map(Math.round).join(',');
      if (measuredBefore(layerKey(v, 'dd|' + d.outerHTML + '|' + at), 'dd')) continue;
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
    let tl = performance.now(); timed('L-dd', t0);
    // the script-built menus (a row's or card's ⋮), each kind once
    for (const k of q('button[aria-haspopup=menu]')) {
      if (!shown(k) || k.closest('details.dd') || k.type !== 'button') continue;
      const kr = rect(k);
      const at = [kr.x, kr.y, kr.width, kr.height].map(Math.round).join(',');
      if (measuredBefore(layerKey(v, 'ctx|' + k.outerHTML + '|' + at), 'ctx')) continue;
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
    timed('L-ctx', tl); tl = performance.now();
    // the boxes the page holds (ui.box), and the buttons that build one
    for (const el of q('.ui-box')) {
      if (!window.MtgBox) break;
      if (measuredBefore(layerKey(v, 'box|' + el.outerHTML), 'box')) continue;
      mark('box #' + el.id); window.MtgBox.openElement(el.id); await settle();
      const b = box();
      if (b) {
        for (const f of measure(b, v)) tag('box ' + txt(b.querySelector('h2') || b), f);
        await closeBox();
      }
    }
    timed('L-box', tl); tl = performance.now();
    for (const bt of q('button').filter(dots)) {
      if (!shown(bt) || bt.closest('details.dd, .menu, .mtgbox, .sp-filters')) continue;
      if (kinds.has('btn|' + txt(bt))) continue;
      kinds.add('btn|' + txt(bt));
      if (measuredBefore(layerKey(v, 'btn|' + bt.outerHTML), 'btn')) continue;
      mark('button ' + txt(bt)); bt.click(); await settle();
      const b = box();
      if (b) { for (const f of measure(b, v)) tag('box ' + txt(bt), f); await closeBox(); }
    }
    timed('L-btn', tl); tl = performance.now();
    // the keyboard shortcuts box
    mark('the shortcuts box');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: '?', bubbles: true, cancelable: true }));
    await settle();
    if (box()) { for (const f of measure(box(), v)) tag('shortcuts box', f); await closeBox(); }
    timed('L-keys', tl); tl = performance.now();
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
    timed('L-sheet', tl);
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
  // each text size goes straight to the next (the one before taken off in the same step, with no
  // frame drawn between them): the page lays out once per size (B-13)
  let undo = () => {};
  for (const zoom of o.zooms) {
    let t0 = performance.now();
    undo();
    undo = zoomOn(zoom);
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
    // the dark theme laid out as the light one (most pages): no element changed its size, so no
    // script that sizes itself hears of it and there is no frame to wait for (B-13)
    html.setAttribute('data-theme', 'dark');
    let dark = fingerprint() !== fp;
    if (dark) { await tick(); dark = fingerprint() !== fp; }
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
  }
  {
    const t0 = performance.now();
    if (theme0 === null) html.removeAttribute('data-theme'); else html.setAttribute('data-theme', theme0);
    undo(); window.dispatchEvent(new Event('resize')); await tick();
    timed('zoom', t0);
  }
  all.push(...layerNew.map(k => 'layer: ' + k));
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


def test_the_shards_hold_every_page_once() -> None:
    every = sorted(_site("p01"))
    held = sorted(page for shard in _shards("p01") for page in shard)
    assert held == every and len(set(every)) == len(every)


@pytest.mark.parametrize("shard", range(SHARDS))
def test_every_page_holds_its_text_and_controls_at_every_size(server: Server, shard: int) -> None:  # noqa: F811
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, pending = _seed(server)
    # the Binder as last read whole, holding the watched cards: the watchlist's "You own" labels
    with server.db.tx() as c:
        for i, name in enumerate(CARDS[-2:]):
            c.execute(
                "INSERT INTO binder_records (sub, rid, scryfall_id, name, finish, quantity, row_json) "
                "VALUES ('user-1', ?, '', ?, 'nonfoil', 3, '{}')",
                (900 + i, name),
            )
    sids = {"admin": sid, "member": _member(server), "out": ""}
    # the pace cap and a member's Archidekt budget are for members' pages, not for a crawl of every
    # page at once
    pacer = server.app.state.gateway.archidekt.pacer
    pacer.max_per_minute = 0
    pacer.page_reserve = 0
    server.app.state.gateway.decks.archidekt_budget.per_window = 10**6
    pages = _shards(pending)[shard]
    if os.environ.get("CRAWL_PAGES"):  # a part of the site, to time a change to the crawl
        pages = [(path, state) for path, state in pages if re.search(os.environ["CRAWL_PAGES"], path)]
    problems: list[str] = []
    errors: list[str] = []
    # CRAWL_PROFILE=1 (with SWEEP_OUT): each check's time in the page, summed over the shard, and
    # each page's load and checks' wall time, written beside the findings
    profile = bool(os.environ.get("CRAWL_PROFILE"))
    spent: dict[str, float] = {}
    layer_seen: set[str] = set()  # the layers measured on an earlier page (CHECKS' layerKey)
    walls: list[tuple[str, str, float, float]] = []
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
                started = time.perf_counter()
                r = _goto(page, f"{server.base}{path}")
                assert r is not None and r.status == 200, (path, state, r and r.status)
                page.wait_for_function(SHEETS_LOADED, timeout=10_000)
                root_none = page.evaluate(
                    "() => parseFloat(getComputedStyle(document.documentElement).fontSize)"
                )
                page.evaluate(OPEN_FOLDS)
                loaded = time.perf_counter()
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
                        "state": state,
                        "layerSeen": sorted(layer_seen),
                        "profile": profile,
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
                        if f.startswith("layer: "):
                            layer_seen.add(f[7:])
                            continue
                        if f.startswith("time: "):
                            for k, ms in json.loads(f[6:]).items():
                                spent[k] = spent.get(k, 0) + ms
                            continue
                        variant, _, what = f.partition(": ")
                        key = re.sub(r"\d+(\.\d+)?", "#", what)  # the same defect, whatever its size
                        if key in seen:
                            seen[key] += 1
                            continue
                        seen[key] = 1
                        problems.append(f"{path} [{state}] @{w}px {variant}: {what}")
                walls.append(
                    (path, "phone" if touch else "computer", loaded - started, time.perf_counter() - loaded)
                )
        browser.close()
    if os.environ.get("SWEEP_OUT"):
        Path(f"{os.environ['SWEEP_OUT']}-{shard}.txt").write_text("\n".join(problems))
        if profile:
            Path(f"{os.environ['SWEEP_OUT']}-{shard}.json").write_text(
                json.dumps({"checks": spent, "pages": walls})
            )
    assert errors == []
    more = f"\n… {len(problems)} in all" if len(problems) > 80 else ""
    assert problems == [], "\n".join(problems[:80]) + more
