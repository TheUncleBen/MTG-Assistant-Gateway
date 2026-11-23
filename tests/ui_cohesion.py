"""The cohesion census: one shared look, measured across every page.

The UI matrix (tests/ui_matrix.py) checks each page against rules of its own (overflow, contrast,
targets...). It cannot see that two pages each pass and still look like two different products: a
primary button 39 px tall on one page and 44 px with a pill radius on the next, a page title in
20 px bold here and 24 px semibold there, panels with 3 px and 8 px corners. This census draws every
page, sorts what it sees into component roles (page title, section heading, primary and secondary
button, chip, panel, text box, segmented switch, list row, page column) and records each role's
measured look as a signature. A role that should have one look but has several is a finding, with
the pages each variant appears on.

``python -m tests.ui_cohesion OUT_DIR [--pages /a,/b] [--displays app,laptop]`` writes
``OUT_DIR/census.json`` (every signature with its pages and elements) and ``OUT_DIR/census.md``.
``tests/test_ui_cohesion_browser.py`` holds the agreed variant counts in CI, so a page cannot
grow a look of its own again.
"""

# ruff: noqa: E501 - the census is a script the browser runs, kept one statement a line
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .ui_matrix import DISPLAYS, Display, pages

CENSUS = r"""
() => {
  const vis = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    if (el.closest('[hidden],[aria-hidden=true],.sr-only,details:not([open]) > :not(summary)')) return false;
    return true;
  };
  const px = (v) => Math.round(parseFloat(v) || 0);
  const col = (c) => {
    const m = (c || '').match(/[\d.]+/g); if (!m) return 'none';
    if (m.length > 3 && +m[3] === 0) return 'none';
    return '#' + m.slice(0, 3).map(n => (+n).toString(16).padStart(2, '0')).join('') + (m.length > 3 && +m[3] < 1 ? '/' + (+m[3]).toFixed(2) : '');
  };
  const name = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    if (el.classList.length) s += '.' + [...el.classList].slice(0, 3).join('.');
    const t = (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 30);
    return t ? s + ' "' + t + '"' : s;
  };
  const painted = (cs) => col(cs.backgroundColor) !== 'none' || (px(cs.borderTopWidth) > 0 && cs.borderTopStyle !== 'none' && col(cs.borderTopColor) !== 'none');
  const radius = (cs, h) => { const r = px(cs.borderTopLeftRadius); return r >= h / 2 - 1 ? 'pill' : r + 'px'; };
  const out = [];
  const add = (role, el, sig) => out.push({role, el: name(el), sig});
  const skip = (el) => el.closest('.cardview,.mtgbox,.ctxmenu,.mtgmenu,.menu,[role=menu],dialog,svg');
  const chrome = (el) => el.closest('header.topbar,.topbar,nav.rail,.rail,.tabbar,footer,.bottombar,#topbar,.appbar');
  const main = document.querySelector('main') || document.body;

  // page column: where the page's content sits in the window
  const mr = main.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  let inner = main;
  for (let i = 0; i < 4; i++) { const kids = [...inner.children].filter(vis); if (kids.length === 1 && kids[0].getBoundingClientRect().width > 200) inner = kids[0]; else break; }
  const ir = inner.getBoundingClientRect();
  add('column', inner, {left: px(ir.left), right: px(vw - ir.right), width: px(ir.width)});

  for (const el of main.querySelectorAll('h1')) {
    if (!vis(el) || skip(el)) continue;
    const cs = getComputedStyle(el);
    add('title', el, {size: px(cs.fontSize), weight: cs.fontWeight, align: cs.textAlign === 'start' ? 'left' : cs.textAlign, mt: px(cs.marginTop), mb: px(cs.marginBottom), color: col(cs.color)});
  }
  for (const el of main.querySelectorAll('h2, h3')) {
    if (!vis(el) || skip(el)) continue;
    const cs = getComputedStyle(el);
    add(el.tagName.toLowerCase(), el, {size: px(cs.fontSize), weight: cs.fontWeight, mb: px(cs.marginBottom), transform: cs.textTransform, color: col(cs.color)});
  }
  const seen = new Set();
  // card pictures and card names are cards, not buttons; a pick list's button is a field
  const notButton = '.pic,.thumbbtn,.cardlink,.thumb,.tile-link,.c,.row';
  for (const el of main.querySelectorAll('button, a.btn, a[role=button], input[type=submit], input[type=button], summary.btn, label.btn')) {
    if (!vis(el) || skip(el) || seen.has(el) || el.matches(notButton)) continue;
    seen.add(el);
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (el.matches('.msel-btn')) {
      add('field', el, {h: px(r.height), radius: px(cs.borderTopLeftRadius) + 'px', size: px(cs.fontSize), border: px(cs.borderTopWidth) + ' ' + col(cs.borderTopColor), bg: col(cs.backgroundColor)});
      continue;
    }
    if (!painted(cs)) { continue; }  // text buttons (links in a row, icon buttons) are not the button look
    const bg = col(cs.backgroundColor);
    const isIcon = r.width < r.height * 1.3 && !(el.innerText || '').trim().match(/[A-Za-z]{3}/);
    const rad = radius(cs, r.height);
    const role = isIcon ? 'icon-button' : (el.matches('.ui-chip') || (rad === 'pill' && r.height < 48) ? 'chip' : 'button');
    const kind = el.matches('.primary,.btn-primary') ? 'primary' : el.matches('.danger,.btn-danger') ? 'danger' : el.matches('.btn-outline-danger') ? 'danger-outline' : el.matches('.secondary,.btn-ghost') ? 'quiet' : (role === 'chip' && el.matches('[aria-pressed=true],[aria-current]')) ? 'chosen' : 'plain';
    add(role + ':' + kind, el, {h: px(r.height), radius: rad, size: px(cs.fontSize), weight: cs.fontWeight, padx: px(cs.paddingLeft), bg, border: px(cs.borderTopWidth) + ' ' + col(cs.borderTopColor), color: col(cs.color)});
  }
  for (const el of main.querySelectorAll('.ui-chip,[class*=pill],[class*=badge],[class*=tag]:not(textarea):not(input),.ui-lbl,.chip,.set-badge')) {
    if (!vis(el) || skip(el) || seen.has(el) || el.matches('button,a.btn')) continue;
    const cs = getComputedStyle(el);
    if (!painted(cs)) continue;
    const r = el.getBoundingClientRect();
    if (r.height > 60) continue;
    seen.add(el);
    const role = el.matches('.ui-chip') ? (el.matches('[aria-pressed=true],[aria-current]') ? 'chip:chosen' : 'chip:plain') : el.matches('.ui-lbl') ? 'label' : el.matches('[class*=badge]') && !el.matches('.set-badge') ? 'badge' : 'tag';
    add(role, el, {h: px(r.height), radius: radius(cs, r.height), size: px(cs.fontSize), weight: cs.fontWeight, padx: px(cs.paddingLeft), bg: role === 'label' ? 'colour' : col(cs.backgroundColor), border: role === 'label' ? '' : px(cs.borderTopWidth) + ' ' + col(cs.borderTopColor)});
  }
  for (const el of main.querySelectorAll('input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=range]):not([type=file]):not([type=submit]):not([type=button]), select, textarea')) {
    if (!vis(el) || skip(el)) continue;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    add(el.tagName === 'TEXTAREA' ? 'textarea' : 'field', el, {h: el.tagName === 'TEXTAREA' ? 'n/a' : px(r.height), radius: px(cs.borderTopLeftRadius) + 'px', size: px(cs.fontSize), border: px(cs.borderTopWidth) + ' ' + col(cs.borderTopColor), bg: col(cs.backgroundColor)});
  }
  for (const el of main.querySelectorAll('[role=tablist], .seg, .segmented, [class*=switch]:not(input)')) {
    if (!vis(el) || skip(el)) continue;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (r.height < 20 || r.height > 80) continue;
    const kid = [...el.querySelectorAll('a,button,label')].find(vis);
    const kcs = kid ? getComputedStyle(kid) : null;
    add('switch', el, {h: px(r.height), radius: radius(cs, r.height), bg: col(cs.backgroundColor), border: px(cs.borderTopWidth) + ' ' + col(cs.borderTopColor), item: kcs ? px(kcs.fontSize) + '/' + kcs.fontWeight : '', place: Math.abs((r.left - mr.left) - (mr.right - r.right)) < 24 ? 'centred' : (r.left - mr.left < 24 && mr.right - r.right < 24 ? 'full' : 'side')});
  }
  // panels: boxes with a border or their own fill and rounded corners, holding content
  for (const el of main.querySelectorAll('div, section, article, aside, details, form, fieldset, li, ul')) {
    if (!vis(el) || skip(el) || el.matches('button,a,input,select,textarea')) continue;
    const cs = getComputedStyle(el);
    if (!painted(cs)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 220 || r.height < 56) continue;
    if (px(cs.borderTopLeftRadius) === 0 && px(cs.borderTopWidth) === 0) continue;
    // a panel inside a panel of the same look is the same panel's part, not a variant
    add('panel', el, {radius: px(cs.borderTopLeftRadius) + 'px', border: px(cs.borderTopWidth) + ' ' + col(cs.borderTopColor), bg: col(cs.backgroundColor), pad: px(cs.paddingTop) + '/' + px(cs.paddingLeft), shadow: cs.boxShadow === 'none' ? 'none' : 'shadow'});
  }
  return out;
}
"""

# How many looks each role is allowed to have (the standard, docs/UI-STANDARD.md). A role not
# listed is reported but not held.
ROLES = (
    "column",
    "title",
    "h2",
    "h3",
    "button:plain",
    "button:primary",
    "button:danger",
    "button:danger-outline",
    "button:quiet",
    "icon-button:plain",
    "icon-button:quiet",
    "chip:plain",
    "chip:chosen",
    "label",
    "tag",
    "badge",
    "field",
    "textarea",
    "switch",
    "panel",
)


def census(server, sid: str, paths: list[str], displays: list[Display], themes=("light",)) -> list[dict]:  # noqa: ANN001
    import re

    from playwright.sync_api import sync_playwright

    from .test_editor_browser import PNG
    from .test_scan_browser import _chromium_path
    from .waits import redrawn

    rows: list[dict] = []
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
            for theme in themes:
                ctx.add_cookies([{"name": "mtg_theme", "value": theme, "url": server.base}])
                for path in paths:
                    if "#" in path:
                        continue
                    server.db.delete_prefs("user-1", prefixes=("view.",))
                    try:
                        r = page.goto(f"{server.base}{path}", wait_until="networkidle", timeout=30_000)
                    except Exception:  # noqa: BLE001
                        continue
                    if r is None or r.status != 200:
                        continue
                    redrawn(page)
                    for f in page.evaluate(CENSUS):
                        rows.append({"page": path, "display": d.key, "theme": theme, **f})
            ctx.close()
        browser.close()
    return rows


def summarize(rows: list[dict]) -> dict:
    """role -> display -> signature -> {pages, elements, count}"""
    out: dict = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        if r["role"] == "column":
            continue
        key = json.dumps(r["sig"], sort_keys=True)
        slot = out[r["role"]][r["display"]].setdefault(key, {"pages": set(), "els": set(), "count": 0})
        slot["pages"].add(r["page"])
        if len(slot["els"]) < 6:
            slot["els"].add(r["el"])
        slot["count"] += 1
    return out


def write(rows: list[dict], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "census.json").write_text(json.dumps(rows, indent=1))
    s = summarize(rows)
    lines = ["# Cohesion census", ""]
    for role in ROLES:
        if role not in s:
            continue
        for disp, sigs in s[role].items():
            lines.append(f"## {role} on {disp}: {len(sigs)} looks")
            for key, v in sorted(sigs.items(), key=lambda kv: -kv[1]["count"]):
                lines.append(
                    f"- **{v['count']}x on {len(v['pages'])} pages** `{key}`  \n"
                    f"  pages: {', '.join(sorted(v['pages'])[:12])}  \n"
                    f"  e.g. {'; '.join(sorted(v['els'])[:4])}"
                )
            lines.append("")
    cols = defaultdict(lambda: defaultdict(set))
    for r in rows:
        if r["role"] == "column":
            cols[r["display"]][json.dumps(r["sig"], sort_keys=True)].add(r["page"])
    lines.append("## Page column (left/right margin, width)")
    for disp, sigs in cols.items():
        lines.append(f"### {disp}: {len(sigs)} variants")
        for k, v in sorted(sigs.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"- {k}: {', '.join(sorted(v))}")
    (out / "census.md").write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--pages", default="")
    ap.add_argument("--displays", default="app,laptop")
    ap.add_argument("--themes", default="light")
    a = ap.parse_args(argv)

    from . import conftest  # noqa: F401
    from . import test_overflow_sweep_browser as OS

    tmp = Path(tempfile.mkdtemp(prefix="uicohesion"))
    fx = OS.server
    gen = (fx._get_wrapped_function() if hasattr(fx, "_get_wrapped_function") else fx.__wrapped__)(tmp)
    s = next(gen)
    try:
        sid, pending = OS._seed(s)
        s.app.state.gateway.decks.archidekt_budget.per_window = 100_000
        paths = a.pages.split(",") if a.pages else pages(pending)
        disp = [d for d in DISPLAYS if d.key in a.displays.split(",")]
        rows = census(s, sid, paths, disp, tuple(a.themes.split(",")))
        write(rows, a.out)
        print(f"{len(rows)} elements -> {a.out / 'census.md'}")
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
