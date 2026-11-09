"""The UI matrix: every page of the site drawn on every kind of screen a member uses (a phone in
the browser, the phone app, a folded and an open Fold, a tablet, a laptop and a wide desktop), in
the light and the dark theme, at 100% and 200% text, and measured against the UI standard.

Two ways in:

* ``tests/test_ui_matrix_browser.py`` runs the checks in CI, on the overflow sweep's hostile data
  (tests/test_overflow_sweep_browser.py), and fails on any finding.
* ``python -m tests.ui_matrix OUT_DIR`` runs the whole matrix locally, saves a screenshot of every
  page on every screen, and writes ``OUT_DIR/index.html``: each page's screenshots side by side,
  with the findings under each, so a person can look at the whole site in one place before a push.

What is measured, beyond the overflow sweep's sideways scroll, clipping and overlaps:

* ``off-centre``: on a screen 900 px or wider, the page's content leans to one side (its left and
  right margins differ by more than 48 px and 8% of the window).
* ``hole``: two panels side by side whose heights differ so much that a large empty block is left
  under the shorter one (a two-column grid where one column holds three rows and the other five).
* ``stranded``: a row of controls that wrapped so that its last control sits alone on a line of its
  own (a proposal's More under the rest of its buttons).
* ``link-blue``: a link drawn in the browser's or the site's plain blue link colour: the site's
  links are text, rows or buttons in its own colours.
* ``contrast``: text whose colour against its background is under 4.5:1 (3:1 for large text).
* ``unstyled``: a button or select the browser draws itself (its grey default face).
* ``raw-value``: shown text that leaks a program value: undefined, null, NaN, None, [object …], an
  ISO timestamp, a price with one decimal ($12.5) or a number with four or more decimals.
* ``plural``: "1 cards", "1 decks" and the like.
* ``target``: on a touch screen, a control smaller than 44 × 44 px.
* ``mid-word``: a word of 4 to 12 letters drawn across two lines.
* ``payment-autofill``: a text box a phone could take for a payment card field (its name, id,
  label or placeholder says card number, cvc, expiry…) without an autocomplete that rules it out.
"""

# ruff: noqa: E501 - the checks are scripts the browser runs, kept one statement a line
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

APP_UA = (
    "Mozilla/5.0 (Linux; Android 15; SM-S938U) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Mobile Safari/537.36 MTGAssistant/0.7.47"
)
FOLD_UA = (
    "Mozilla/5.0 (Linux; Android 15; SM-F966U) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Mobile Safari/537.36 MTGAssistant/0.7.47"
)
IPAD_UA = (
    "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.0 Mobile/15E148 Safari/604.1"
)


@dataclass(frozen=True)
class Display:
    key: str
    label: str
    width: int
    height: int
    touch: bool
    ua: str | None = None

    def context(self) -> dict:
        kw: dict = {"viewport": {"width": self.width, "height": self.height}}
        if self.touch:
            kw.update(has_touch=True, is_mobile=True)
        if self.ua:
            kw["user_agent"] = self.ua
        return kw


# Screen sizes are Playwright's own device descriptors where one exists (Galaxy S24, Galaxy Z Fold 7
# and its cover screen, iPad Pro 11); the laptop and the desktop are the two most common windows.
DISPLAYS = [
    Display("phone", "Phone, browser (360)", 360, 780, True),
    Display("app", "Phone, app (412)", 412, 915, True, APP_UA),
    Display("fold-cover", "Fold folded, app (360)", 360, 764, True, FOLD_UA),
    Display("fold-open", "Fold open, app (984)", 984, 1016, True, FOLD_UA),
    Display("tablet", "Tablet (834)", 834, 1194, True, IPAD_UA),
    Display("laptop", "Laptop (1280)", 1280, 800, False),
    Display("desktop", "Wide desktop (1920)", 1920, 1080, False),
]
THEMES = ("light", "dark")
TEXT = (1, 2)  # 100% and 200% text


def pages(pending: str) -> list[str]:
    """Every page a member or an admin reaches, on the overflow sweep's data."""
    from .test_overflow_sweep_browser import FOLDERS, LONGUSER

    return [
        "/",
        "/decks",
        "/decks?view=list",
        "/decks/42",
        "/decks/42?view=grid",
        "/decks/42?view=table",
        "/decks/50",
        "/decks/42/edit",
        "/decks/42/settings",
        "/decks/new",
        "/decks/new-package",
        "/decks/42/export",
        "/decks/42/compare",
        "/decks/42/goldfish",
        "/decks/42/package",
        f"/decks?folder={FOLDERS[0]}",
        "/collection?view=grid",
        "/collection?view=list",
        "/collection/lists",
        "/collection/wants",
        "/lists/custom",
        "/watchlist",
        "/search",
        "/cards",
        "/precons",
        "/forum",
        "/users/alice",
        f"/users/{LONGUSER}",
        "/notifications",
        "/history",
        "/history/reports/r1",
        "/history/models",
        "/reports",
        "/proposals",
        f"/proposals/{pending}",
        "/activity",
        "/account",
        "/account/details",
        "/account/assistants",
        "/guide",
        "/scan",
        "/skill",
        "/app",
        "/install",
        "/install?for=chatgpt",
        "/admin",
        "/admin/users",
        "/admin/models",
        "/admin/activity",
        "/admin/settings",
        "/admin/metrics",
        *STATE_PAGES,
    ]


# States reached from a page, swept like pages: "path#state" opens the path, then runs the state's
# script (a tap on a card, a hold's menu, a filter sheet) and waits for what it opens.
STATES = {
    "viewer": (
        "document.querySelector('.deckview .row, .deckview .c').click()",
        ".cardview.open, .cardview[open], .cardview",
    ),
    "cardmenu": (
        "(() => { const r = document.querySelector('.deckview .row, .deckview .c');"
        " const b = r.getBoundingClientRect();"
        " r.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true,"
        " clientX: b.left + 20, clientY: b.top + 10})); })()",
        ".ctxmenu, .mtgmenu",
    ),
    "filters": ("document.querySelector('.sp-open').click()", ".spanel.open, .spanel:not(.folded)"),
}
STATE_PAGES = [
    "/decks/42#viewer",
    "/decks/42#cardmenu",
    "/decks/42?view=grid#viewer",
    "/search#filters",
    "/decks#filters",
    "/collection?view=grid#filters",
]


# Doubles every element's font size, as Android's and the browsers' text scaling do (the same
# script as tests/test_text_zoom_browser.py's TEXT_200).
TEXT_200 = """
() => {
  const els = [document.documentElement, ...document.querySelectorAll('body, body *')];
  const sizes = els.map(el => parseFloat(getComputedStyle(el).fontSize));
  els.forEach((el, i) => {
    if (sizes[i]) el.style.setProperty('font-size', (sizes[i] * 2) + 'px', 'important');
  });
}
"""

_COMMON = """
  const vw = document.documentElement.clientWidth;
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    if (el.closest('[hidden],[aria-hidden=true],.sr-only')) return false;
    for (let d = el.parentElement && el.parentElement.closest('details:not([open])'); d;
         d = d.parentElement && d.parentElement.closest('details:not([open])')) {
      const sm = d.querySelector(':scope > summary');
      if (!(sm && sm.contains(el))) return false;
    }
    return true;
  };
  const name = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    else if (el.classList.length) s += '.' + [...el.classList].slice(0, 3).join('.');
    const t = (el.getAttribute('aria-label') || el.placeholder || el.innerText || el.value || '')
      .trim().replace(/\\s+/g, ' ').slice(0, 40);
    return t ? s + ' "' + t + '"' : s;
  };
  const out = [];
  const add = (kind, el, msg) => out.push({kind, el: el ? name(el) : '', msg});
  const pinned = (el) => {
    for (let a = el; a && a !== document.body; a = a.parentElement) {
      const p = getComputedStyle(a).position;
      if (p === 'fixed' || p === 'sticky') return true;
    }
    return false;
  };
  const inScroller = (el) => {
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (/(auto|scroll)/.test(cs.overflowX) && a.scrollWidth > a.clientWidth + 1) return true;
    }
    return false;
  };
  const rgb = (s) => { const m = s.match(/[\\d.]+/g); return m ? m.map(Number) : null; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; };
    return .2126 * f(c[0]) + .7152 * f(c[1]) + .0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
  // the colour drawn behind an element: the first ancestor with an opaque enough fill, blended
  const behind = (el) => {
    let layers = [];
    for (let a = el; a; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;  // a picture or a scrim: by eye
      const c = rgb(cs.backgroundColor);
      if (c && (c.length < 4 || c[3] > 0)) { layers.push(c); if (c.length < 4 || c[3] >= .99) break; }
    }
    let base = [255, 255, 255];
    const root = rgb(getComputedStyle(document.body).backgroundColor);
    if (root && (root.length < 4 || root[3] > 0)) base = root.slice(0, 3);
    for (let i = layers.length - 1; i >= 0; i--) {
      const c = layers[i], al = c.length < 4 ? 1 : c[3];
      base = [0, 1, 2].map(k => c[k] * al + base[k] * (1 - al));
    }
    return base;
  };
"""

# All the checks of one layout, as a list of {kind, el, msg}. ``touch``: the screen is a touch screen.
CHECKS = (
    "(opts) => {"
    + _COMMON
    + r"""
  const touch = opts.touch, big = opts.text > 1;
  const main = document.querySelector('main') || document.body;
  if (document.documentElement.scrollWidth > vw + 1)
    add('overflow', null, 'page scrolls sideways by ' + (document.documentElement.scrollWidth - vw) + 'px');

  // --- off-centre: the content's union box against the room beside the side rail, on wide screens
  if (vw >= 900 && !big) {
    let room = 0;  // the right edge of a rail pinned to the left
    for (const el of document.querySelectorAll('body > *, body > * > nav')) {
      const cs = getComputedStyle(el);
      if (cs.position !== 'fixed' || !visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.left <= 1 && r.width < vw / 3 && r.height > window.innerHeight / 2) room = Math.max(room, r.right);
    }
    // a panel pinned to the right edge (a proposal open beside the list) takes its room the same way
    let roomR = vw;
    const inflow = [];
    for (const el of main.querySelectorAll('*')) {
      const cs = getComputedStyle(el);
      if ((cs.position !== 'fixed' && cs.position !== 'sticky') || !visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.right >= vw - 48 && r.left > vw / 2 && r.height > window.innerHeight / 2) roomR = Math.min(roomR, r.left);
      else if (cs.position === 'sticky' && r.height > window.innerHeight / 2) inflow.push(r);
    }
    let L = Infinity, R = -Infinity;
    // a sticky panel inside the page's width (the proposal beside the list on a wide screen) is content
    for (const r of inflow) if (r.right <= roomR) { L = Math.min(L, r.left); R = Math.max(R, r.right); }
    const leaves = main.querySelectorAll('h1,h2,h3,h4,p,li,a,button,input,select,textarea,img,table,'
      + 'summary,label,.row,section,article,[class*=card],[class*=tile],[class*=list] > *');
    for (const el of leaves) {
      if (!visible(el) || pinned(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.right <= 0 || r.left >= roomR || r.width >= roomR - room - 2) continue;
      if (inScroller(el)) continue;  // a chip row that scrolls sideways reaches past the edge by design
      L = Math.min(L, r.left); R = Math.max(R, r.right);
    }
    if (L < R) {
      const lg = L - room, rg = roomR - R;
      if (Math.abs(lg - rg) > Math.max(48, (roomR - room) * .08))
        add('off-centre', main, 'content spans ' + Math.round(L) + '..' + Math.round(R) + ' of ' + room + '..' +
          vw + 'px: ' + Math.round(lg) + 'px left of it, ' + Math.round(rg) + 'px right');
    }
    // lopsided: a wide row of two or more panels that fills one side and leaves the other empty
    for (const box of main.querySelectorAll('*')) {
      const cs = getComputedStyle(box);
      if (!/grid|flex/.test(cs.display) || !visible(box)) continue;
      if (cs.display.includes('flex') && cs.flexDirection.startsWith('column')) continue;
      const cr = box.getBoundingClientRect();
      if (cr.width < 600) continue;
      const kids = [...box.children].filter(visible);
      if (kids.length < 2) continue;
      const rs = kids.map(k => k.getBoundingClientRect());
      if (rs.some(r => r.width < 200)) continue;
      // panels are boxes (a face or a border); a form's row of controls starts at the left by design
      if (!kids.every(k => { const s = getComputedStyle(k);
        return s.backgroundColor !== 'rgba(0, 0, 0, 0)' || parseFloat(s.borderTopWidth) > 0; })) continue;
      const l = Math.min(...rs.map(r => r.left)), r = Math.max(...rs.map(r => r.right));
      const el = l - cr.left, er = cr.right - r;
      if (er > cr.width * .2 && el < cr.width * .05 && !box.matches('ul, ol, .cards'))
        { add('off-centre', box, 'its panels fill ' + Math.round(r - l) + ' of ' + Math.round(cr.width) +
            'px and leave ' + Math.round(er) + 'px empty on the right'); break; }
    }
  }

  // --- hole: side-by-side boxes in one grid/flex row with very different heights
  for (const box of main.querySelectorAll('*')) {
    const cs = getComputedStyle(box);
    if (!/grid|flex/.test(cs.display) || (cs.display.includes('flex') && cs.flexDirection.startsWith('column')))
      continue;
    if (!visible(box)) continue;
    const kids = [...box.children].filter(visible).filter(k => getComputedStyle(k).position !== 'absolute');
    if (kids.length < 2) continue;
    const rows = new Map();
    for (const k of kids) {
      const r = k.getBoundingClientRect(), t = Math.round(r.top / 4);
      if (!rows.has(t)) rows.set(t, []);
      rows.get(t).push(r);
    }
    for (const rs of rows.values()) {
      if (rs.length < 2) continue;
      const hs = rs.map(r => r.height), ws = rs.map(r => r.width);
      const hi = Math.max(...hs), lo = Math.min(...hs);
      // a narrow side column (a sidebar, a nav) beside the main one is meant to be shorter
      if (hi - lo > 80 && lo < hi * .7 && Math.min(...ws) > 200 && Math.min(...ws) > Math.max(...ws) * .6)
        { add('hole', box, 'panels side by side are ' + Math.round(lo) + ' and ' + Math.round(hi) +
            'px tall: ' + Math.round(hi - lo) + 'px of empty space under the shorter'); break; }
    }
  }

  // --- stranded: a wrapped row of controls whose last line holds a lone control
  const ctl = 'button, a.btn, .btn, details.dd, summary, select, [role=button]';
  for (const row of main.querySelectorAll('*')) {
    const cs = getComputedStyle(row);
    if (!(cs.display.includes('flex') && cs.flexWrap === 'wrap' && !cs.flexDirection.startsWith('column'))) continue;
    if (!visible(row)) continue;
    const kids = [...row.children].filter(visible);
    if (kids.length < 3 || !kids.every(k => k.matches(ctl) || k.querySelector(':scope > ' + ctl))) continue;
    if (kids.every(k => /chip|pill|tag/.test(k.className))) continue;  // a cloud of chips wraps as it may
    const tops = kids.map(k => Math.round(k.getBoundingClientRect().top));
    const lines = [...new Set(tops)].sort((a, b) => a - b);
    if (lines.length < 2) continue;
    const w = (ks) => ks.reduce((t, k) => t + k.getBoundingClientRect().width, 0);
    const rw = row.getBoundingClientRect().width;
    const by = lines.map(t => kids.filter((k, i) => tops[i] === t));
    const lone = by.find(ks => ks.length === 1 && w(ks) < rw * .6);
    if (lone && by.some(ks => ks.length >= 2))
      add('stranded', lone[0], 'alone on a line of its own beside lines of ' +
        Math.max(...by.map(ks => ks.length)) + ' controls');
  }

  // --- link-blue: a link drawn in a blue (the browser's default or any blue text colour)
  const blue = (c) => { const v = rgb(c); return v && v[2] > v[0] + 40 && v[2] > v[1] + 15; };
  let nBlue = 0, firstBlue = null;
  for (const a of document.querySelectorAll('main a[href], .sheet a[href], .menu a[href], main .cardlink')) {
    if (!visible(a)) continue;
    if (blue(getComputedStyle(a).color)) { nBlue++; firstBlue = firstBlue || a; }
  }
  if (nBlue) add('link-blue', firstBlue, nBlue + ' link(s) drawn in blue');

  // --- contrast, on every shown text run (one finding per class of element)
  const seenC = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n, checked = 0;
  while ((n = walker.nextNode()) && checked < 4000) {
    if (!n.nodeValue.trim()) continue;
    const el = n.parentElement;
    if (!el || !visible(el) || el.closest('svg,script,style,noscript,.img-fallback')) continue;
    if (el.closest('button:disabled,[aria-disabled=true],input:disabled')) continue;
    checked++;
    const cs = getComputedStyle(el);
    const fg = rgb(cs.color); if (!fg || (fg.length > 3 && fg[3] < .99 && fg[3] === 0)) continue;
    const bg = behind(el); if (!bg) continue;
    // text drawn over a picture (a deck tile's name on its art) is measured by eye, not here
    const er = el.getBoundingClientRect();
    const stack = document.elementsFromPoint(er.left + Math.min(er.width / 2, 8), er.top + er.height / 2);
    if (stack.some(x => x.tagName === 'IMG' || x.tagName === 'VIDEO' || x.tagName === 'CANVAS')) continue;
    let f = fg.slice(0, 3);
    if (fg.length > 3) f = [0, 1, 2].map(k => fg[k] * fg[3] + bg[k] * (1 - fg[3]));
    const px = parseFloat(cs.fontSize) / opts.text, bold = parseInt(cs.fontWeight) >= 700;
    const need = (px >= 24 || (bold && px >= 18.66)) ? 3 : 4.5;
    const c = ratio(f, bg);
    if (c < need - .05) {
      const key = name(el).split(' "')[0] + Math.round(c * 10);
      if (!seenC.has(key)) { seenC.add(key);
        add('contrast', el, c.toFixed(2) + ':1 (needs ' + need + ':1), ' + cs.color + ' on rgb(' +
          bg.map(Math.round).join(', ') + ')'); }
    }
  }

  // --- unstyled: buttons and selects in the browser's own grey face
  for (const el of document.querySelectorAll('button, select, input[type=button], input[type=submit]')) {
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    if (/^rgb\(2(39|40), 2(39|40), 2(39|40)\)$/.test(cs.backgroundColor) || /outset/.test(cs.borderStyle))
      add('unstyled', el, 'drawn in the browser\'s own style (' + cs.backgroundColor + ')');
  }

  // --- raw values and plural slips in shown text
  const text = document.body.innerText;
  const raws = [
    [/\b(undefined|NaN)\b/, 'a program value'], [/\[object \w+\]/, 'a program value'],
    [/(^|[\s:(])(null|None)([\s).,]|$)/m, 'an empty value'],
    [/\b\d{4}-\d\d-\d\dT\d\d:\d\d/, 'an ISO timestamp'],
    [/[$€£]\d[\d,]*\.\d(?!\d)/, 'a price with one decimal'],
    [/\b\d+\.\d{4,}\b/, 'a number with four or more decimals'],
  ];
  for (const [re, what] of raws) { const m = text.match(re);
    if (m) add('raw-value', null, what + ' shown: "' + text.substr(Math.max(0, m.index - 20), 60).replace(/\s+/g, ' ') + '"'); }
  const pl = text.match(/(?<![\d.,])1 (cards|decks|proposals|changes|items|games|members|copies|printings|lists|days|hours|minutes|results|reports|rows)\b/);
  if (pl) add('plural', null, '"' + pl[0] + '"');

  // --- 44 px targets on touch screens
  if (touch) {
    const seenT = new Set();
    for (const el of document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab]')) {
      if (!visible(el) || el.matches('a.skip')) continue;
      // a link inside running text is not a control on its own
      if (el.matches('a') && el.closest('p, li') && !el.matches('.btn, [class*=row]') &&
          el.closest('p, li').innerText.trim().length > el.innerText.trim().length + 12) continue;
      let hit = el;
      if (el.matches('input[type=checkbox], input[type=radio], input[type=file]')) {
        const lab = el.closest('label') || (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'));
        if (lab) hit = lab;
      }
      const r = hit.getBoundingClientRect();
      let w = r.width, h = r.height;
      const af = getComputedStyle(hit, '::after');
      if (af.content !== 'none' && af.position === 'absolute') {
        const t = parseFloat(af.top), b = parseFloat(af.bottom), l = parseFloat(af.left), rr = parseFloat(af.right);
        h = Math.max(h, r.height - (isNaN(t) ? 0 : t) - (isNaN(b) ? 0 : b));
        w = Math.max(w, r.width - (isNaN(l) ? 0 : l) - (isNaN(rr) ? 0 : rr));
      }
      if (w < 43.5 || h < 43.5) { const k = name(el).split(' "')[0];
        if (!seenT.has(k)) { seenT.add(k); add('target', el, Math.round(w) + 'x' + Math.round(h) + 'px'); } }
    }
  }

  // --- a word drawn across two lines
  const seenW = new Set();
  const w2 = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while ((n = w2.nextNode())) {
    const el = n.parentElement;
    if (!el || !visible(el) || el.closest('textarea,script,style,pre,code')) continue;
    const re = /(?<![A-Za-z])[A-Za-z][A-Za-z'’]{3,11}(?![A-Za-z])/g;
    let m;
    while ((m = re.exec(n.nodeValue))) {
      const rg = document.createRange();
      rg.setStart(n, m.index); rg.setEnd(n, m.index + m[0].length);
      const rects = [...rg.getClientRects()].filter(x => x.width > 0);
      if (rects.length > 1 && Math.abs(rects[0].top - rects[rects.length - 1].top) > 2) {
        const k = name(el) + m[0];
        if (!seenW.has(k)) { seenW.add(k); add('mid-word', el, '"' + m[0] + '" broken across two lines'); }
      }
    }
  }

  // --- a text box a phone could fill with a payment card
  const cc = /card(?!s\b)|number|cvc|cvv|csc|expir|credit|(?<![a-z])cc(?![a-z])/i;  // tests/test_autofill_fields.py
  for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio])')) {
    const hints = [el.name, el.id, el.placeholder, el.getAttribute('aria-label'),
      el.labels && el.labels[0] && el.labels[0].innerText].filter(Boolean).join(' ');
    const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
    if (cc.test(hints) && !/^(off|one-time-code)$/.test(ac))
      add('payment-autofill', el, 'looks like a payment field (' + hints.slice(0, 60) + ')');
    if (/^cc-/.test(ac)) add('payment-autofill', el, 'autocomplete=' + ac);
  }
  return out;
}"""
)


@dataclass
class Finding:
    page: str
    display: str
    theme: str
    text: int
    kind: str
    el: str
    msg: str

    def line(self) -> str:
        return f"{self.page} [{self.display} {self.theme} {self.text * 100}%] {self.kind}: {self.el} {self.msg}".strip()


@dataclass
class Sweep:
    findings: list[Finding] = field(default_factory=list)
    shots: dict[tuple[str, str, str, int], str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")[:60] or "home"


def sweep(
    server,  # noqa: ANN001
    sid: str,
    paths: list[str],
    *,
    displays: list[Display] = DISPLAYS,
    themes: tuple[str, ...] = THEMES,
    texts: tuple[int, ...] = TEXT,
    kinds: set[str] | None = None,
    shots_dir: Path | None = None,
    probe: str = "",
) -> Sweep:
    """Draws every path on every display, theme and text size and runs CHECKS on each; with
    ``shots_dir``, saves a full-page screenshot of each. ``kinds`` keeps only those findings."""
    from playwright.sync_api import sync_playwright

    from .test_editor_browser import PNG
    from .test_scan_browser import _chromium_path
    from .waits import redrawn, settled

    res = Sweep()
    exe = _chromium_path()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for d in displays:
            ctx = browser.new_context(**d.context())
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            ctx.route(
                re.compile(r"https://cards\.scryfall\.io/.*"),
                lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
            )
            ctx.route(re.compile(r"/decks/[^/]+/check(\?|$)"), lambda route: route.abort())
            page = ctx.new_page()
            page.on("pageerror", lambda e, d=d: res.errors.append(f"{d.key}: {e}"))
            for path in paths:
                for theme in themes:
                    for text in texts:
                        ctx.add_cookies([{"name": "mtg_theme", "value": theme, "url": server.base}])
                        server.db.delete_prefs("user-1", prefixes=("view.",))
                        url, _, state = path.partition("#")
                        try:
                            r = page.goto(f"{server.base}{url}", wait_until="networkidle", timeout=30_000)
                        except Exception as exc:  # noqa: BLE001
                            res.errors.append(f"{path} {d.key}: {exc}")
                            continue
                        if r is None or r.status != 200:
                            res.errors.append(f"{path} {d.key}: status {r and r.status}")
                            continue
                        if state:
                            js, opened = STATES[state]
                            try:
                                page.evaluate(js)
                                page.wait_for_selector(opened, state="visible", timeout=5_000)
                            except Exception as exc:  # noqa: BLE001
                                res.errors.append(f"{path} {d.key}: the state did not open ({exc})")
                                continue
                            settled(page)
                        if text > 1:
                            page.evaluate(TEXT_200)
                        redrawn(page)
                        if probe:  # a developer's question about the layout: printed, not checked
                            print(path, d.key, theme, text, page.evaluate(probe), flush=True)
                        for f in page.evaluate(CHECKS, {"touch": d.touch, "text": text}):
                            if kinds is None or f["kind"] in kinds:
                                res.findings.append(
                                    Finding(path, d.key, theme, text, f["kind"], f["el"], f["msg"])
                                )
                        if shots_dir is not None:
                            name = f"{_slug(path)}__{d.key}__{theme}__{text * 100}.png"
                            page.screenshot(path=str(shots_dir / name), full_page=not state)
                            res.shots[(path, d.key, theme, text)] = name
            ctx.close()
        browser.close()
    return res


def report(res: Sweep, out: Path, paths: list[str], displays: list[Display] = DISPLAYS) -> None:
    """``out/index.html``: one section per page, its screenshots in a row per theme and text size,
    each with its findings; ``out/findings.json`` and a count per kind at the top."""
    import html

    out.mkdir(parents=True, exist_ok=True)
    (out / "findings.json").write_text(json.dumps([f.__dict__ for f in res.findings], indent=1))
    by: dict[tuple, list[Finding]] = {}
    for f in res.findings:
        by.setdefault((f.page, f.display, f.theme, f.text), []).append(f)
    kinds: dict[str, int] = {}
    for f in res.findings:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    parts = [
        "<!doctype html><meta charset=utf-8><title>UI matrix</title><style>"
        "body{font:14px system-ui;margin:16px;background:#f4f4f4}h2{margin:2rem 0 .5rem}"
        ".row{display:flex;gap:12px;overflow-x:auto;align-items:flex-start;padding-bottom:8px}"
        ".cell{flex:none;width:260px;background:#fff;border:1px solid #ddd;border-radius:8px;padding:6px}"
        ".cell img{width:100%;border:1px solid #eee}.cell.bad{border-color:#d33}"
        ".cell ul{margin:4px 0 0;padding-left:16px;font-size:12px;color:#a00}"
        ".cell b{font-size:12px}</style><h1>UI matrix</h1><p>"
        + ", ".join(f"{html.escape(k)}: {v}" for k, v in sorted(kinds.items()))
        + "</p>"
    ]
    if res.errors:
        parts.append(
            "<h2>Errors</h2><ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in res.errors) + "</ul>"
        )
    themes = sorted({k[2] for k in res.shots} | {f.theme for f in res.findings})
    texts = sorted({k[3] for k in res.shots} | {f.text for f in res.findings})
    for path in paths:
        parts.append(f"<h2 id='{_slug(path)}'>{html.escape(path)}</h2>")
        for theme in themes:
            for text in texts:
                parts.append("<div class=row>")
                for d in displays:
                    fs = by.get((path, d.key, theme, text), [])
                    shot = res.shots.get((path, d.key, theme, text))
                    parts.append(
                        f"<div class='cell{' bad' if fs else ''}'><b>{html.escape(d.label)} · {theme} · "
                        f"{text * 100}%</b>"
                        + (
                            f"<a href='shots/{shot}'><img loading=lazy src='shots/{shot}'></a>"
                            if shot
                            else ""
                        )
                        + (
                            "<ul>"
                            + "".join(
                                f"<li>{html.escape(f.kind)}: {html.escape(f.el)} {html.escape(f.msg)}</li>"
                                for f in fs
                            )
                            + "</ul>"
                            if fs
                            else ""
                        )
                        + "</div>"
                    )
                parts.append("</div>")
    (out / "index.html").write_text("".join(parts))


def main(argv: list[str] | None = None) -> int:
    """``python -m tests.ui_matrix OUT_DIR [--pages /a,/b] [--displays app,laptop] [--no-shots]``"""
    import argparse
    import tempfile

    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--pages", default="")
    ap.add_argument("--displays", default="")
    ap.add_argument("--themes", default="light,dark")
    ap.add_argument("--text", default="1,2")
    ap.add_argument("--no-shots", action="store_true")
    ap.add_argument("--probe", default="", help="a script evaluated on each layout, its answer printed")
    a = ap.parse_args(argv)

    from . import conftest  # noqa: F401  (the offline guards)
    from . import test_overflow_sweep_browser as OS

    tmp = Path(tempfile.mkdtemp(prefix="uimatrix"))
    fx = OS.server
    gen = (fx._get_wrapped_function() if hasattr(fx, "_get_wrapped_function") else fx.__wrapped__)(tmp)
    s = next(gen)
    try:
        sid, pending = OS._seed(s)
        s.app.state.gateway.decks.archidekt_budget.per_window = 100_000
        paths = a.pages.split(",") if a.pages else pages(pending)
        disp = [d for d in DISPLAYS if not a.displays or d.key in a.displays.split(",")]
        shots = None if a.no_shots else a.out / "shots"
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
        res = sweep(
            s,
            sid,
            paths,
            displays=disp,
            themes=tuple(a.themes.split(",")),
            texts=tuple(int(t) for t in a.text.split(",")),
            shots_dir=shots,
            probe=a.probe,
        )
        report(res, a.out, paths, disp)
        print(f"{len(res.findings)} findings, {len(res.errors)} errors -> {a.out / 'index.html'}")
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
