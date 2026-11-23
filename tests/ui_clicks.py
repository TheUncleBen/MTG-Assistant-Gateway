"""The click sweep: every button, link, tab, chip and menu item on every page is pressed, and
whatever it did is recorded. A control that did nothing (no navigation, no request to the gateway,
no change to the page, no download, no new window, no change of focus or scroll) is a dead button.
A control whose press threw a script error, or led to a page or a request that failed, is broken.

Each press starts from a freshly loaded page and a database and Archidekt fake restored to the
seed, so a Delete pressed on one page does not change what the next press sees. A press that
opened a layer (a menu, a sheet, a box, a details panel) has the layer's own controls pressed in
turn (level 2), each from a fresh page with the opening press replayed first.

``python -m tests.ui_clicks OUT_DIR [--pages /a,/b] [--displays app,laptop]`` writes
``OUT_DIR/clicks.json`` and ``OUT_DIR/clicks.md``.
"""

# ruff: noqa: E501, B023, C901 - the sweep's scripts run in the browser; its helpers close over one page per screen
from __future__ import annotations

import copy
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from .ui_matrix import DISPLAYS, pages

# Every control a member can press, in document order. Layers (menus, sheets, boxes) are scoped by
# ``root``. Repeats of one kind (forty card rows) are cut to the first two: their handler is shared.
ENUM = r"""
(root) => {
  const R = root ? document.querySelector(root) : document;
  if (!R) return [];
  const vis = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0 || cs.pointerEvents === 'none') return false;
    const r = el.getBoundingClientRect();
    if (!(r.width > 1 && r.height > 1)) return false;
    if (el.closest('[hidden],[inert],[aria-hidden=true]')) return false;
    for (let d = el.parentElement && el.parentElement.closest('details:not([open])'); d; d = d.parentElement && d.parentElement.closest('details:not([open])')) {
      const sm = d.querySelector(':scope > summary');
      if (!(sm && sm.contains(el))) return false;
    }
    return true;
  };
  const sel = 'a[href], button, [role=button], [role=menuitem], [role=menuitemradio], [role=tab], [role=option], [role=switch], summary, input[type=checkbox], input[type=radio], input[type=submit], input[type=button], label.btn, label.ui-chip, [onclick], [data-href]';
  // an open modal box covers the page: only its own controls can be reached
  const modal = [...document.querySelectorAll('[aria-modal=true]')].find(m => { const r = m.getBoundingClientRect(); return r.width > 1 && r.height > 1 && getComputedStyle(m).visibility !== 'hidden'; });
  const reach = (el) => !modal || modal.contains(el);
  const all = [...R.querySelectorAll(sel)].filter(el => vis(el) && reach(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true');
  const seen = {};
  const out = [];
  all.forEach((el, i) => {
    const t = (el.getAttribute('aria-label') || el.innerText || el.value || el.title || '').trim().replace(/\s+/g, ' ').slice(0, 50);
    const cls = [...el.classList].filter(c => !/^(on|active|sel|open|is-)/.test(c)).sort().join('.');
    const kind = el.tagName.toLowerCase() + (cls ? '.' + cls : '') + (el.getAttribute('role') ? '[' + el.getAttribute('role') + ']' : '');
    seen[kind] = (seen[kind] || 0) + 1;
    // a long run of one kind with differing words (card rows, deck tiles) shares one handler
    if (seen[kind] > 2 && all.filter(e => e.tagName === el.tagName && e.className === el.className).length > 6) return;
    const chrome = !!el.closest('header, nav, footer, .topbar, .rail, .tabbar, .bottombar, #mtg-top, .appbar, .skip');
    const cur = el.getAttribute('aria-current') === 'page' || (el.getAttribute('href') && new URL(el.href, location.href).href === location.href);
    out.push({i, text: t, kind, href: el.getAttribute('href') || '', chrome, current: !!cur, target: el.getAttribute('target') || '', download: el.hasAttribute('download')});
  });
  return out;
}
"""

# Watches the page for any effect of the next press (installed just before it).
WATCH = r"""
(idx) => {
  const sel = 'a[href], button, [role=button], [role=menuitem], [role=menuitemradio], [role=tab], [role=option], [role=switch], summary, input[type=checkbox], input[type=radio], input[type=submit], input[type=button], label.btn, label.ui-chip, [onclick], [data-href]';
  const w = window.__ck = {mut: 0, ev: 0, what: []};
  const el = window.__ckEl;
  const noisy = new Set(['aria-busy', 'data-busy-restore', 'tabindex']);
  w.obs = new MutationObserver(recs => {
    for (const r of recs) {
      if (r.type === 'attributes') {
        if (noisy.has(r.attributeName)) continue;
        if (r.target === el && (r.attributeName === 'class' || r.attributeName === 'style')) continue;
        if (r.attributeName === 'class' && /\b(pressed|press|ripple|tap|hover|focus)\b/.test(r.target.className + ' ' + (r.oldValue || ''))) continue;
      }
      w.mut++;
      if (w.what.length < 3) w.what.push(r.type + ':' + (r.attributeName || '') + ':' + (r.target.className || r.target.nodeName));
    }
  });
  w.obs.observe(document.documentElement, {subtree: true, childList: true, attributes: true, characterData: true, attributeOldValue: true});
  for (const t of ['change', 'input', 'toggle', 'submit', 'popstate', 'hashchange'])
    window.addEventListener(t, () => { w.ev++; w.what.push('event:' + t); }, true);
  w.sx = scrollX; w.sy = scrollY;
  w.focus = document.activeElement;
  w.url = location.href;
}
"""

# A layer the press opened: the first open menu, sheet, box or dialog that was not open before.
LAYER = r"""
() => {
  const cands = ['.mtgmenu', '.ctxmenu', '[role=menu]', '[role=dialog]', 'dialog[open]', '.mtgbox', '.spanel.open', '.sheet.open', '.cardview.open', '.cardview[open]', '.dd[open] > .dd-menu', 'details[open] > .menu', '[role=listbox]'];
  for (const c of cands) {
    for (const el of document.querySelectorAll(c)) {
      const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
      if (cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 1 && r.height > 1 && !el.dataset.ckSeen) {
        el.dataset.ckSeenNow = '1';
        const id = 'ck-layer-' + Math.random().toString(36).slice(2, 8);
        el.setAttribute('data-ck-layer', id);
        return '[data-ck-layer="' + id + '"]';
      }
    }
  }
  return '';
}
"""

MARK_LAYERS = r"""
() => { for (const el of document.querySelectorAll('.mtgmenu, .ctxmenu, [role=menu], [role=dialog], dialog[open], .mtgbox, .spanel.open, .sheet.open, .cardview, .dd[open] > .dd-menu, details[open] > .menu, [role=listbox]')) el.dataset.ckSeen = '1'; }
"""

PICK = r"""
([root, i, want]) => {
  const R = root ? document.querySelector(root) : document;
  const sel = 'a[href], button, [role=button], [role=menuitem], [role=menuitemradio], [role=tab], [role=option], [role=switch], summary, input[type=checkbox], input[type=radio], input[type=submit], input[type=button], label.btn, label.ui-chip, [onclick], [data-href]';
  const vis = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0 || cs.pointerEvents === 'none') return false;
    const r = el.getBoundingClientRect();
    if (!(r.width > 1 && r.height > 1)) return false;
    if (el.closest('[hidden],[inert],[aria-hidden=true]')) return false;
    for (let d = el.parentElement && el.parentElement.closest('details:not([open])'); d; d = d.parentElement && d.parentElement.closest('details:not([open])')) {
      const sm = d.querySelector(':scope > summary');
      if (!(sm && sm.contains(el))) return false;
    }
    return true;
  };
  // an open modal box covers the page: only its own controls can be reached
  const modal = [...document.querySelectorAll('[aria-modal=true]')].find(m => { const r = m.getBoundingClientRect(); return r.width > 1 && r.height > 1 && getComputedStyle(m).visibility !== 'hidden'; });
  const reach = (el) => !modal || modal.contains(el);
  const all = R ? [...R.querySelectorAll(sel)].filter(el => vis(el) && reach(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true') : [];
  const label = (e) => (e.getAttribute('aria-label') || e.innerText || e.value || e.title || '').trim().replace(/\s+/g, ' ').slice(0, 50);
  // the same words first (a list whose rows moved since the first look), nearest the old place
  let el = null;
  if (want) {
    let best = 1e9;
    all.forEach((e, k) => { if (label(e) === want && Math.abs(k - i) < best) { best = Math.abs(k - i); el = e; } });
  }
  if (!el) el = all[i];
  if (!el) return null;
  document.querySelectorAll('[data-ck-target]').forEach(e => e.removeAttribute('data-ck-target'));
  el.setAttribute('data-ck-target', '1');
  window.__ckEl = el;
  return (el.getAttribute('aria-label') || el.innerText || el.value || el.title || '').trim().replace(/\s+/g, ' ').slice(0, 50);
}
"""

SKIP_TEXT = re.compile(r"^(sign out|log out)$", re.I)


class Fresh:
    """Puts the gateway's database and the Archidekt fake back to the seed before each press."""

    def __init__(self, server) -> None:  # noqa: ANN001
        import tempfile

        self.s = server
        self.path = Path(tempfile.mkdtemp(prefix="ckseed")) / "seed.db"
        snap = sqlite3.connect(str(self.path))
        with server.db._lock:
            server.db._conn.backup(snap)
        snap.close()
        self.tables = [
            r[0]
            for r in server.db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        self.ark = {
            k: copy.deepcopy(v)
            for k, v in vars(server.ark).items()
            if isinstance(v, (dict, list, set, int, str, bool)) and not k.startswith("_")
        }

    def restore(self) -> None:
        c = self.s.db._conn
        with self.s.db._lock:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("ATTACH DATABASE ? AS ckseed", (str(self.path),))
            try:
                c.execute("BEGIN IMMEDIATE")
                for t in self.tables:
                    c.execute(f'DELETE FROM main."{t}"')
                    c.execute(f'INSERT INTO main."{t}" SELECT * FROM ckseed."{t}"')
                c.execute("COMMIT")
            finally:
                c.execute("DETACH DATABASE ckseed")
                c.execute("PRAGMA foreign_keys=ON")
        for k, v in self.ark.items():
            setattr(self.s.ark, k, copy.deepcopy(v))


def sweep(server, sid: str, paths: list[str], displays, out: Path, log=print) -> list[dict]:  # noqa: ANN001, C901
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    from .test_editor_browser import PNG
    from .test_scan_browser import _chromium_path
    from .waits import redrawn

    fresh = Fresh(server)
    base = server.base
    host = urlparse(base).netloc
    rows: list[dict] = []
    exe = _chromium_path()
    chrome_done: set[tuple[str, str]] = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for d in displays:
            ctx = browser.new_context(**d.context(), accept_downloads=True)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": base}])
            ctx.add_cookies([{"name": "mtg_theme", "value": "light", "url": base}])
            ctx.route(
                re.compile(r"https://cards\.scryfall\.io/.*"),
                lambda r: r.fulfill(status=200, content_type="image/png", body=PNG),
            )
            ctx.route(re.compile(r"/decks/[^/]+/check(\?|$)"), lambda r: r.abort())
            state: dict = {"req": [], "bad": [], "err": [], "nav": [], "popup": 0, "download": 0, "dialog": 0}

            def on_req(r, state=state):  # noqa: ANN001
                u = urlparse(r.url)
                if (
                    u.netloc == host
                    and not u.path.startswith(("/static/", "/favicon", "/account/avatar"))
                    and r.resource_type in ("fetch", "xhr", "document", "eventsource")
                ):
                    state["req"].append(r.method + " " + u.path)

            def on_resp(r, state=state):  # noqa: ANN001
                u = urlparse(r.url)
                if (
                    u.netloc == host
                    and r.status >= 400
                    and r.request.resource_type in ("fetch", "xhr", "document")
                ):
                    state["bad"].append(f"{r.status} {r.request.method} {u.path}")

            def on_dialog(dl, state=state):  # noqa: ANN001
                state["dialog"] += 1
                dl.dismiss()

            page = ctx.new_page()
            page.on("request", on_req)
            page.on("response", on_resp)
            page.on("pageerror", lambda e, state=state: state["err"].append(str(e)[:200]))
            page.on("dialog", on_dialog)
            page.on("download", lambda dl, state=state: state.__setitem__("download", state["download"] + 1))
            ctx.on(
                "page", lambda pg, state=state: (state.__setitem__("popup", state["popup"] + 1), pg.close())
            )

            def load(path: str) -> bool:
                fresh.restore()
                server.db.delete_prefs("user-1", prefixes=("view.",))
                try:
                    r = page.goto(f"{base}{path}", wait_until="load", timeout=30_000)
                except Exception as exc:  # noqa: BLE001
                    log(f"  load failed {path}: {exc}")
                    return False
                if r is None or r.status != 200:
                    log(f"  load failed {path}: status {r and r.status}")
                    return False
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:  # noqa: BLE001  (a page that keeps a connection open)
                    pass
                redrawn(page)
                page.evaluate(MARK_LAYERS)
                return True

            def press(path: str, chain: list[tuple[str, int, str]]) -> dict:
                """Loads path, replays chain[:-1], presses chain[-1]; returns what happened."""
                if not load(path):
                    return {"verdict": "unloadable"}
                for k, (root, i, text) in enumerate(chain):
                    if root == "__layer__":
                        root = page.evaluate(LAYER)
                        if not root:
                            return {"verdict": "unstable", "detail": "the layer did not open again"}
                    got = page.evaluate(PICK, [root, i, text])
                    if got is None or (got != text and text):
                        return {"verdict": "unstable", "detail": f"expected {text!r} found {got!r}"}
                    last = k == len(chain) - 1
                    if last:
                        page.evaluate(WATCH, i)
                        for key in ("req", "bad", "err", "nav"):
                            state[key] = []
                        state["popup"] = state["download"] = state["dialog"] = 0
                        url0 = page.url
                    el = page.locator("[data-ck-target]")
                    try:
                        el.scroll_into_view_if_needed(timeout=2000)
                        el.click(timeout=3000, no_wait_after=True)
                    except PWTimeout:
                        try:
                            el.click(timeout=2000, force=True, no_wait_after=True)
                        except Exception as exc:  # noqa: BLE001
                            return {"verdict": "unclickable", "detail": str(exc).splitlines()[0][:160]}
                    except Exception as exc:  # noqa: BLE001
                        return {"verdict": "unclickable", "detail": str(exc).splitlines()[0][:160]}
                    if not last:
                        try:
                            page.wait_for_load_state("networkidle", timeout=3000)
                        except Exception:  # noqa: BLE001
                            pass
                        redrawn(page)
                        continue
                    effect = ""
                    try:
                        page.wait_for_function(
                            "(u) => location.href !== u || (window.__ck && (window.__ck.mut > 0 || window.__ck.ev > 0 || scrollX !== window.__ck.sx || scrollY !== window.__ck.sy))",
                            arg=url0,
                            timeout=1500,
                        )
                    except Exception:  # noqa: BLE001  (navigated away: the context is gone; or nothing happened)
                        pass
                    try:
                        page.wait_for_load_state("networkidle", timeout=4000)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        ck = page.evaluate(
                            "() => window.__ck ? {mut: window.__ck.mut, ev: window.__ck.ev, what: window.__ck.what, scrolled: scrollX !== window.__ck.sx || scrollY !== window.__ck.sy, focus: document.activeElement !== window.__ck.focus && document.activeElement !== window.__ckEl && document.activeElement !== document.body, url: location.href} : null"
                        )
                    except Exception:  # noqa: BLE001
                        ck = None
                    navigated = (
                        page.url != url0 or ck is None or any(r.startswith("GET ") and "document" for r in [])
                    )
                    if (
                        not navigated
                        and state["req"]
                        and any(r == "GET " + urlparse(url0).path for r in state["req"])
                    ):
                        navigated = True  # a link to the page it is on: the page loads again
                    if navigated:
                        effect = (
                            "navigated to "
                            + urlparse(page.url).path
                            + (("?" + urlparse(page.url).query) if urlparse(page.url).query else "")
                        )
                    elif state["popup"]:
                        effect = "opened a window"
                    elif state["download"]:
                        effect = "download"
                    elif state["dialog"]:
                        effect = "browser dialog"
                    elif ck and (ck["mut"] or ck["ev"]):
                        effect = f"page changed ({ck['mut']} changes, {ck['ev']} events: {', '.join(ck['what'][:2])})"
                    elif state["req"]:
                        effect = "request " + ", ".join(state["req"][:2])
                    elif ck and ck["scrolled"]:
                        effect = "scrolled"
                    elif ck and ck["focus"]:
                        effect = "moved focus"
                    res = {"verdict": "ok" if effect else "dead", "effect": effect}
                    if state["err"]:
                        res["verdict"] = "js-error"
                        res["detail"] = state["err"][0]
                    elif state["bad"]:
                        bad = [b for b in state["bad"] if not b.startswith("401")]
                        if bad:
                            res["verdict"] = "failed-request"
                            res["detail"] = "; ".join(bad[:3])
                    if res["verdict"] == "ok" and not navigated:
                        try:
                            res["layer"] = page.evaluate(LAYER)
                        except Exception:  # noqa: BLE001
                            res["layer"] = ""
                    return res
                return {"verdict": "unreachable"}

            for path in paths:
                if not load(path):
                    rows.append(
                        {"page": path, "display": d.key, "verdict": "unloadable", "text": "", "kind": ""}
                    )
                    continue
                items = page.evaluate(ENUM, None)
                log(f"{d.key} {path}: {len(items)} controls")
                for it in items:
                    if SKIP_TEXT.match(it["text"]) or it["kind"].startswith("a.skip"):
                        continue  # Sign out ends the sweep's session; the skip link shows on focus only
                    if it.get("current"):
                        rows.append(
                            {
                                "page": path,
                                "display": d.key,
                                "level": 1,
                                **it,
                                "verdict": "ok",
                                "effect": "the page it is on",
                            }
                        )
                        continue
                    if it["chrome"]:
                        key = (d.key, it["kind"] + "|" + it["text"] + "|" + it["href"])
                        if key in chrome_done:
                            continue
                        chrome_done.add(key)
                    href = it["href"]
                    if href and urlparse(href).netloc and urlparse(href).netloc != host:
                        rows.append(
                            {
                                "page": path,
                                "display": d.key,
                                "level": 1,
                                **it,
                                "verdict": "external",
                                "effect": href,
                            }
                        )
                        continue
                    res = press(path, [(None, it["i"], it["text"])])
                    row = {"page": path, "display": d.key, "level": 1, **it, **res}
                    rows.append(row)
                    if res.get("verdict") != "ok" or not res.get("layer"):
                        continue
                    layer = res["layer"]
                    try:
                        subs = page.evaluate(ENUM, layer)
                    except Exception:  # noqa: BLE001
                        subs = []
                    for sub in subs[:25]:
                        if SKIP_TEXT.match(sub["text"]):
                            continue
                        if (
                            sub["href"]
                            and urlparse(sub["href"]).netloc
                            and urlparse(sub["href"]).netloc != host
                        ):
                            continue
                        # the layer gets a new marker on each replay: find it by what opened it
                        r2 = press_layer(press, path, it, sub)
                        rows.append(
                            {"page": path, "display": d.key, "level": 2, "via": it["text"], **sub, **r2}
                        )
                bad = [
                    r
                    for r in rows
                    if r["page"] == path
                    and r["display"] == d.key
                    and r.get("verdict") not in ("ok", "external")
                ]
                log(f"  -> {len(bad)} not ok")
                (out / "clicks.json").write_text(json.dumps(rows, indent=1))
            ctx.close()
        browser.close()
    return rows


def press_layer(press, path: str, first: dict, sub: dict) -> dict:  # noqa: ANN001
    """Replays the press that opened the layer, then presses the layer's control ``sub``."""
    # press() replays chain[:-1] blind; the layer's marker is set by LAYER after the first press,
    # so a two-step chain uses a root that LAYER re-marks: done here by a small wrapper root.
    return press(path, [(None, first["i"], first["text"]), ("__layer__", sub["i"], sub["text"])])


def write_md(rows: list[dict], out: Path) -> None:
    lines = ["# Click sweep", ""]
    bad = [r for r in rows if r.get("verdict") not in ("ok", "external")]
    lines.append(f"{len(rows)} presses, {len(bad)} not ok.")
    for verdict in (
        "dead",
        "js-error",
        "failed-request",
        "unclickable",
        "unstable",
        "unloadable",
        "unreachable",
    ):
        vs = [r for r in bad if r.get("verdict") == verdict]
        if not vs:
            continue
        lines += ["", f"## {verdict} ({len(vs)})", ""]
        for r in vs:
            via = f" (in {r['via']!r})" if r.get("via") else ""
            lines.append(
                f"- {r['display']} `{r['page']}`{via}: {r.get('kind', '')} {r.get('text', '')!r} {r.get('detail', '')}"
            )
    (out / "clicks.md").write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--pages", default="")
    ap.add_argument("--displays", default="app,laptop")
    ap.add_argument("--slice", default="", help="I/N: only every Nth page from the Ith (parallel jobs)")
    a = ap.parse_args(argv)

    from . import conftest  # noqa: F401
    from . import test_overflow_sweep_browser as OS

    a.out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="uiclicks"))
    fx = OS.server
    gen = (fx._get_wrapped_function() if hasattr(fx, "_get_wrapped_function") else fx.__wrapped__)(tmp)
    s = next(gen)
    try:
        sid, pending = OS._seed(s)
        s.app.state.gateway.decks.archidekt_budget.per_window = 100_000
        paths = a.pages.split(",") if a.pages else [p for p in pages(pending) if "#" not in p]
        if a.slice:
            i, n = (int(x) for x in a.slice.split("/"))
            paths = paths[i::n]
        disp = [d for d in DISPLAYS if d.key in a.displays.split(",")]
        rows = sweep(s, sid, paths, disp, a.out)
        (a.out / "clicks.json").write_text(json.dumps(rows, indent=1))
        write_md(rows, a.out)
        print(f"{len(rows)} presses -> {a.out / 'clicks.md'}")
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
