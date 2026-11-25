"""The layers census: every box (dialog) and menu each page can open, opened and measured.

tests/ui_cohesion.py measures what a page draws; it leaves out what opens over it (a box, a menu),
because those are closed when the page loads. This opens them: each box the page holds
(``.ui-box[id]``, through ``MtgBox.openElement``) and the menu of the page's first ⋮ (or, without
one, a held first row), and records each one's look as a signature, as the census does for the
page's own parts. A box or a menu that looks different from the others is a page that drew its own.

``python -m tests.ui_layers OUT_DIR [--pages /a,/b] [--displays app,laptop]`` writes
``OUT_DIR/layers.json`` and ``OUT_DIR/layers.md``. tests/test_ui_layers_browser.py holds the
looks to one each in CI.
"""

# ruff: noqa: E501 - the measuring scripts run in the browser
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

MEASURE = r"""
(el) => {
  const px = (v) => Math.round(parseFloat(v) || 0);
  const col = (c) => { const m = (c || '').match(/[\d.]+/g); if (!m) return 'none'; if (m.length > 3 && +m[3] === 0) return 'none'; return '#' + m.slice(0, 3).map(n => (+n).toString(16).padStart(2, '0')).join(''); };
  const cs = getComputedStyle(el);
  const vis = (x) => x && x.getBoundingClientRect().height > 0 && getComputedStyle(x).visibility !== 'hidden';
  const sig = {radius: px(cs.borderTopLeftRadius) + 'px', bg: col(cs.backgroundColor), border: px(cs.borderTopWidth) + ' ' + col(cs.borderTopColor), shadow: cs.boxShadow === 'none' ? 'none' : 'shadow'};
  if (el.matches('.ui-box, .mtgbox, [role=dialog]')) {
    const h = el.querySelector('.bh h2, h2');
    if (vis(h)) { const hs = getComputedStyle(h); sig.head = px(hs.fontSize) + '/' + hs.fontWeight; }
    const x = el.querySelector('.bx');
    if (vis(x)) sig.close = x.offsetWidth + 'x' + x.offsetHeight;  // offset sizes: a box still easing in is scaled
    const body = el.querySelector(':scope > .body');
    if (body) { const bs = getComputedStyle(body); sig.pad = px(bs.paddingTop) + '/' + px(bs.paddingLeft); }
    // a wide box (Export, New deck) is a whole-screen sheet on a phone: its own role
    if (el.matches('.wide')) sig.kind = 'wide';
  } else {
    const items = [...el.querySelectorAll('[role=menuitem], a, button')].filter(vis);
    if (items.length) { const is = getComputedStyle(items[0]); sig.item = px(is.fontSize) + '/' + is.fontWeight + ' h' + items[0].offsetHeight; }
    const h = el.querySelector('.mh, .head, h2, h3');
    if (vis(h)) { const hs = getComputedStyle(h); sig.head = px(hs.fontSize) + '/' + hs.fontWeight; }
  }
  return sig;
}
"""


def layers(server, sid: str, paths: list[str], displays) -> list[dict]:  # noqa: ANN001, C901
    """One row per opened layer: page, display, role (box or menu), its id or opener, signature."""
    import re
    import threading

    from playwright.sync_api import sync_playwright

    from .test_editor_browser import PNG
    from .test_scan_browser import _chromium_path
    from .waits import redrawn

    rows: list[dict] = []
    exe = _chromium_path()
    errors: list[BaseException] = []

    def run() -> None:
        try:
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
                    for path in paths:
                        try:
                            r = page.goto(f"{server.base}{path}", wait_until="networkidle", timeout=30_000)
                        except Exception:  # noqa: BLE001
                            continue
                        if r is None or r.status != 200:
                            continue
                        redrawn(page)
                        ids = page.evaluate(
                            "() => [...document.querySelectorAll('.ui-box[id]')].map(b => b.id)"
                        )
                        for bid in ids:
                            opened = page.evaluate(
                                "(id) => { if (!window.MtgBox) return false; MtgBox.openElement(id); const b = document.getElementById(id); return !!b && !b.hidden && b.getBoundingClientRect().height > 0; }",
                                bid,
                            )
                            if not opened:
                                continue
                            sig = page.eval_on_selector(f"#{bid}", MEASURE)
                            rows.append(
                                {"page": path, "display": d.key, "role": "box", "el": bid, "sig": sig}
                            )
                            page.keyboard.press("Escape")
                            page.wait_for_function(
                                "(id) => { const b = document.getElementById(id); return !b || b.hidden || b.getBoundingClientRect().height === 0; }",
                                arg=bid,
                                timeout=3000,
                            )
                        # the page's first menu: its first ⋮, or a hold on its first row
                        kebab = page.locator("main .kebab:visible").first
                        try:
                            if kebab.count():
                                kebab.click(timeout=3000)
                            else:
                                row = page.locator(
                                    "main .ui-table .tr:not(.th), main .deckview .row, main ul.decklist .deck"
                                ).first
                                if not row.count():
                                    continue
                                row.click(button="right", timeout=3000)
                            page.wait_for_selector(".ctxmenu:visible, [role=menu]:visible", timeout=2500)
                        except Exception:  # noqa: BLE001 - no menu opened: nothing to measure
                            page.keyboard.press("Escape")
                            continue
                        menu = page.locator(".ctxmenu:visible, [role=menu]:visible").first
                        sig = menu.evaluate(MEASURE)
                        rows.append(
                            {"page": path, "display": d.key, "role": "menu", "el": "first menu", "sig": sig}
                        )
                        page.keyboard.press("Escape")
                    ctx.close()
                browser.close()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=run)  # the gateway's loop owns the calling thread
    t.start()
    t.join()
    if errors:
        raise errors[0]
    return rows


def summarize(rows: list[dict]) -> dict:
    """role -> display -> signature -> {pages, els, count}"""
    out: dict = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        key = json.dumps(r["sig"], sort_keys=True)
        slot = out[r["role"]][r["display"]].setdefault(key, {"pages": set(), "els": set(), "count": 0})
        slot["pages"].add(r["page"])
        slot["els"].add(r["el"])
        slot["count"] += 1
    return out


def write(rows: list[dict], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "layers.json").write_text(json.dumps(rows, indent=1))
    lines = ["# Layers census", ""]
    for role, by in summarize(rows).items():
        for disp, sigs in by.items():
            lines.append(f"## {role} on {disp}: {len(sigs)} looks")
            for k, v in sorted(sigs.items(), key=lambda kv: -kv[1]["count"]):
                lines.append(
                    f"- **{v['count']}x** `{k}` pages: {', '.join(sorted(v['pages'])[:10])}; e.g. {', '.join(sorted(v['els'])[:4])}"
                )
            lines.append("")
    (out / "layers.md").write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--pages", default="")
    ap.add_argument("--displays", default="app,laptop")
    a = ap.parse_args(argv)

    from . import conftest  # noqa: F401
    from . import test_overflow_sweep_browser as OS
    from .ui_matrix import DISPLAYS, pages

    tmp = Path(tempfile.mkdtemp(prefix="uilayers"))
    fx = OS.server
    gen = (fx._get_wrapped_function() if hasattr(fx, "_get_wrapped_function") else fx.__wrapped__)(tmp)
    s = next(gen)
    try:
        sid, pending = OS._seed(s)
        s.app.state.gateway.decks.archidekt_budget.per_window = 100_000
        paths = a.pages.split(",") if a.pages else [p for p in pages(pending) if "#" not in p]
        disp = [d for d in DISPLAYS if d.key in a.displays.split(",")]
        rows = layers(s, sid, paths, disp)
        write(rows, a.out)
        print(f"{len(rows)} layers -> {a.out / 'layers.md'}")
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
