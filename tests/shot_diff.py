"""Before and after screenshots: every page, on a phone, an open Fold and a laptop, drawn from the
base commit and from the change, compared pixel by pixel. No AI looks at a page that did not change.

``python -m tests.shot_diff run OUT_DIR [--base REF] [--map MAP.md] [--displays app,fold-673,laptop]
[--pages /a,/b] [--themes light,dark]``

1. The base commit is checked out in a temporary worktree and every page is drawn from it, on the
   same seeded data (tests/test_overflow_sweep_browser.py's), with animations off, the clock frozen
   and times masked. Each page's drawn classes and loaded scripts are recorded (``index.json``), so
   tests/impact_map.py can tell which pages draw a changed class.
2. The same pages are drawn from the working tree.
3. Each pair is compared. A page whose pixels differ is drawn again from the base, to tell a real
   change from a page that never draws the same way twice (that one is reported as noisy, not as a
   change).
4. Each changed page is sorted: expected (the map lists it under "Pages expected to look
   different") or unexpected. An unexpected change fails the run: the change reached a page it
   should not have. ``OUT_DIR/report.md`` lists both, with before | after | difference pictures
   for each, so a reviewer looks only at what changed.

``capture`` and ``diff`` are the two halves, for running by hand.
"""

# ruff: noqa: C901, E501 - the run is one pass over pages and screens; its page scripts are one statement a line
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parent.parent

DEFAULT_DISPLAYS = "app,fold-673,laptop"

# Animations off, the caret hidden, the clock text masked: what differs run to run and is not
# the change. A frozen clock keeps "3 minutes ago" from ticking during the run.
STEADY_CSS = """
*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}
time,.ui-when,[data-fmt=rel]{visibility:hidden!important}
"""

# Math.random and crypto.getRandomValues from a fixed seed: a page that shuffles (the solo test's sample hands) draws the same
# shuffle on both sides
SEEDED_RANDOM = """
(() => { let a = 20261123; const next = () => { a |= 0; a = a + 0x6D2B79F5 | 0;
  let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
  return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  Math.random = next;
  if (window.crypto && crypto.getRandomValues) {
    crypto.getRandomValues = (arr) => { for (let i = 0; i < arr.length; i++) arr[i] = Math.floor(next() * 4294967296); return arr; };
  }
})();
"""

# What differs between two runs and is not the change: the test server's port, and clock times
# the server writes as text ("Since 23 Nov 20:45", "in 58 min"). Rewritten to fixed words in the
# text the page shows, so the pictures compare the page, not the clock.
STEADY_TEXT = r"""
() => {
  const subs = [
    [/127\.0\.0\.1:\d+/g, '127.0.0.1:8080'],
    [/\b\d{1,2}:\d{2}(:\d{2})?\b/g, '12:00'],
    [/\b\d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{4} )?/g, '1 Jan '],
    [/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}\b/g, 'Jan 1, 2000'],
    [/\b\d{4}-\d{2}-\d{2}\b/g, '2000-01-01'],
    [/\b\d+(\.\d+)? ?(s|sec|seconds?|min|minutes?|h|hours?|d|days?) ago\b/g, 'a while ago'],
    [/\bin \d+(\.\d+)? ?(s|sec|seconds?|min|minutes?|h|hours?|d|days?)\b/g, 'in a while'],
  ];
  const fix = (t) => subs.reduce((s, [re, to]) => s.replace(re, to), t);
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) { const t = fix(n.nodeValue); if (t !== n.nodeValue) n.nodeValue = t; }
  for (const el of document.querySelectorAll('input, textarea')) { const v = fix(el.value || ''); if (v !== el.value) el.value = v; }
}
"""

INDEX_JS = r"""
() => {
  const classes = new Set();
  for (const el of document.querySelectorAll('[class]')) for (const c of el.classList) classes.add(c);
  const scripts = [...document.scripts].map(s => s.getAttribute('src')).filter(Boolean)
    .map(s => s.split('/').pop().split('?')[0]);
  return {classes: [...classes].sort(), scripts};
}
"""


def slug(page: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", page).strip("_") or "home"
    return s[:80]


def capture(out: Path, displays: list[str], only: list[str], themes: list[str]) -> int:
    """Draw every page (or ``only``) on each display and theme into ``out``, with ``index.json``.
    Runs against whichever source tree is first on sys.path (the run puts the base worktree there)."""
    from playwright.sync_api import sync_playwright

    import tests.conftest  # noqa: F401 - the fixtures' settings
    from tests import test_overflow_sweep_browser as OS
    from tests.test_editor_browser import PNG
    from tests.test_scan_browser import _chromium_path
    from tests.ui_matrix import DISPLAYS, pages
    from tests.waits import redrawn

    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="shots"))
    fx = OS.server
    gen = (fx._get_wrapped_function() if hasattr(fx, "_get_wrapped_function") else fx.__wrapped__)(tmp)
    server = next(gen)
    index: dict[str, dict] = {}
    try:
        sid, pending = OS._seed(server)
        server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
        paths = [p for p in pages(pending) if "#" not in p]
        named = {p.replace(pending, "{pending}"): p for p in paths}
        if only:
            named = {k: v for k, v in named.items() if k in only}
        disp = [d for d in DISPLAYS if d.key in displays]
        errors: list[BaseException] = []
        exe = _chromium_path()

        def draw() -> None:  # in a thread of its own: the gateway's loop owns this one
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
                    for d in disp:
                        for theme in themes:
                            ctx = browser.new_context(
                                **d.context(), color_scheme=theme, reduced_motion="reduce"
                            )
                            ctx.add_cookies(
                                [
                                    {"name": "mtg_session", "value": sid, "url": server.base},
                                    {"name": "mtg_theme", "value": theme, "url": server.base},
                                ]
                            )
                            ctx.route(
                                re.compile(r"https://cards\.scryfall\.io/.*"),
                                lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
                            )
                            ctx.route(re.compile(r"/decks/[^/]+/check(\?|$)"), lambda route: route.abort())
                            page = ctx.new_page()
                            page.add_init_script(SEEDED_RANDOM)
                            for name, path in named.items():
                                server.db.delete_prefs("user-1", prefixes=("view.",))
                                try:
                                    r = page.goto(
                                        f"{server.base}{path}", wait_until="networkidle", timeout=30_000
                                    )
                                except Exception as e:  # noqa: BLE001
                                    index.setdefault(name, {})["error"] = str(e)[:200]
                                    continue
                                page.add_style_tag(content=STEADY_CSS)
                                page.evaluate(STEADY_TEXT)
                                redrawn(page)
                                status = r.status if r else 0
                                f = out / f"{d.key}-{theme}" / f"{slug(name)}.png"
                                f.parent.mkdir(parents=True, exist_ok=True)
                                page.screenshot(
                                    path=str(f), full_page=True, animations="disabled", caret="hide"
                                )
                                seen = page.evaluate(INDEX_JS)
                                slot = index.setdefault(
                                    name, {"classes": set(), "scripts": set(), "status": {}}
                                )
                                slot["classes"] |= set(seen["classes"])
                                slot["scripts"] |= set(seen["scripts"])
                                slot["status"][f"{d.key}-{theme}"] = status
                            ctx.close()
                    browser.close()
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        import threading

        t = threading.Thread(target=draw)
        t.start()
        t.join()
        if errors:
            raise errors[0]
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
    for v in index.values():
        for k in ("classes", "scripts"):
            if isinstance(v.get(k), set):
                v[k] = sorted(v[k])
    (out / "index.json").write_text(json.dumps(index, indent=1))
    return 0


def compare(
    a: Path, b: Path, out: Path | None = None, tolerance: int = 24, ignore: Path | None = None
) -> dict:
    """How two pictures differ: the share of pixels off by more than ``tolerance`` in any channel,
    the box around them, and (with ``out``) a before | after | difference picture. ``ignore`` is a
    third picture of ``a``'s page drawn again: what differs between ``a`` and it (widened by 16 px)
    is the page's own noise, and is not counted."""
    import numpy as np
    from PIL import Image

    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    w, h = max(ia.width, ib.width), max(ia.height, ib.height)
    pa = Image.new("RGB", (w, h), (255, 0, 255))
    pb = Image.new("RGB", (w, h), (255, 0, 255))
    pa.paste(ia, (0, 0))
    pb.paste(ib, (0, 0))
    na, nb = np.asarray(pa, dtype=np.int16), np.asarray(pb, dtype=np.int16)
    mask = (np.abs(na - nb) > tolerance).any(axis=2)
    if ignore is not None and ignore.exists():
        ic = Image.new("RGB", (w, h), (255, 0, 255))
        ic.paste(Image.open(ignore).convert("RGB"), (0, 0))
        noise = (np.abs(na - np.asarray(ic, dtype=np.int16)) > tolerance).any(axis=2)
        if noise.any():
            # widen the noise by 16 px each way (a moving number moves its neighbours' antialiasing)
            k = 16
            ys, xs = np.nonzero(noise)
            wide = np.zeros_like(noise)
            for y, x in zip(ys[:: max(1, len(ys) // 4000)], xs[:: max(1, len(xs) // 4000)], strict=False):
                wide[max(0, y - k) : y + k + 1, max(0, x - k) : x + k + 1] = True
            mask &= ~(wide | noise)
    n = int(mask.sum())
    res = {"pixels": n, "share": n / float(w * h), "size": [ia.size, ib.size], "box": None}
    if n:
        ys, xs = np.nonzero(mask)
        res["box"] = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
        if out is not None:
            diff = np.asarray(pb, dtype=np.uint8).copy() // 3 + 150
            diff[mask] = (230, 0, 60)
            gap = 12
            sheet = Image.new("RGB", (w * 3 + gap * 2, h), (40, 40, 40))
            sheet.paste(pa, (0, 0))
            sheet.paste(pb, (w + gap, 0))
            sheet.paste(Image.fromarray(diff.astype(np.uint8)), (2 * (w + gap), 0))
            if sheet.height > 6000:  # a long page: keep the part around the difference
                top = max(0, res["box"][1] - 400)
                sheet = sheet.crop((0, top, sheet.width, min(sheet.height, top + 6000)))
            out.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(out)
    return res


# a page counts as changed when this many pixels differ: below it is antialiasing, not a change
MIN_PIXELS = 40


def diff(base: Path, head: Path, out: Path) -> dict[str, dict]:
    """screen/page -> how it differs, for every picture in either folder."""
    res: dict[str, dict] = {}
    names = {p.relative_to(base) for p in base.rglob("*.png")} | {
        p.relative_to(head) for p in head.rglob("*.png")
    }
    for rel in sorted(names):
        a, b = base / rel, head / rel
        key = str(rel.with_suffix(""))
        if not a.exists() or not b.exists():
            res[key] = {"pixels": -1, "missing": "before" if not a.exists() else "after"}
            continue
        r = compare(a, b, out / "diff" / rel)
        if r["pixels"] < MIN_PIXELS:
            (out / "diff" / rel).unlink(missing_ok=True)
        res[key] = r
    return res


def _page_of(index: dict, key: str) -> str:
    if key.startswith("cards/"):  # an in-chat card: "card:deck", "card:printings", ...
        return "card:" + key.split("/", 1)[1].split("-")[0]
    s = key.split("/", 1)[1]
    for name in index:
        if slug(name) == s:
            return name
    return s


def _capture_in(tree: Path, out: Path, displays: str, only: list[str], themes: str) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{tree / 'src'}{os.pathsep}{tree}"
    cmd = [sys.executable, str(HERE), "capture", str(out), "--displays", displays, "--themes", themes]
    if only:
        cmd += ["--pages", ",".join(only)]
    subprocess.run(cmd, cwd=tree, env=env, check=True)


def _cards_in(tree: Path, out: Path) -> None:
    """The in-chat cards (Claude and ChatGPT) drawn by tests/test_cards_browser.py's screenshot
    test, at phone, Fold and desktop widths, light and dark, into ``out``."""
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{tree / 'src'}{os.pathsep}{tree}"
    env["MTG_CARD_SHOTS"] = str(out)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_cards_browser.py",
            "-k",
            "screenshots",
        ],
        cwd=tree,
        env=env,
        check=False,
        capture_output=True,
    )


def run(out: Path, base: str, map_path: Path | None, displays: str, only: list[str], themes: str) -> int:
    sys.path.insert(0, str(ROOT))
    from tests import impact_map as IM

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    wt = Path(tempfile.mkdtemp(prefix="shotbase"))
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt), base], cwd=ROOT, check=True, capture_output=True
    )
    try:
        _capture_in(wt, out / "before", displays, only, themes)
        _capture_in(ROOT, out / "after", displays, only, themes)
        if not only:
            _cards_in(wt, out / "before" / "cards")
            _cards_in(ROOT, out / "after" / "cards")
        res = diff(out / "before", out / "after", out)
        changed = {k: v for k, v in res.items() if v["pixels"] < 0 or v["pixels"] >= MIN_PIXELS}
        # a page that changed: draw it from the base again; when the base differs from itself it is noise
        index = json.loads((out / "before" / "index.json").read_text())
        recheck = sorted({_page_of(index, k) for k in changed if not k.startswith("cards/")})
        noisy: set[str] = set()
        cards_changed = any(k.startswith("cards/") for k in changed)
        if cards_changed:
            _cards_in(wt, out / "before-again" / "cards")
        if recheck:
            _capture_in(wt, out / "before-again", displays, recheck, themes)
        if recheck or cards_changed:
            again = diff(out / "before", out / "before-again", out / "noise")
            noisy = {k for k, v in again.items() if v["pixels"] >= MIN_PIXELS}
            # a noisy page: compare again with its noise left out; what is left is the change
            for k in noisy & set(changed):
                rel = Path(k + ".png")
                r = compare(
                    out / "before" / rel,
                    out / "after" / rel,
                    out / "diff" / rel,
                    ignore=out / "before-again" / rel,
                )
                r["noisy"] = True
                changed[k] = r
            changed = {k: v for k, v in changed.items() if v["pixels"] < 0 or v["pixels"] >= MIN_PIXELS}
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True)

    expected: set[str] = set()
    if map_path:
        expected = IM.parse_map(map_path.read_text()).expected_pages
    could = IM.deps(base, index)
    unexpected, wanted, noise = [], [], []
    for k, v in sorted(changed.items()):
        page = _page_of(index, k)
        if v.get("noisy"):
            noise.append((k, page, v))  # still counted below: it changed outside its noise
        if page in expected or page.split("?")[0] in expected:
            wanted.append((k, page, v))
        else:
            unexpected.append((k, page, v))
    lines = [
        "# Before and after",
        "",
        f"Base {base[:12]}, screens {displays}, themes {themes}, {len(res)} pictures compared.",
        "",
    ]
    lines.append(f"## Changed and NOT expected: {len(unexpected)} (each one fails the round)")
    for k, page, v in unexpected:
        why = "; ".join(sorted(could.why.get(page, set()))) or "no dependency path found: look closely"
        lines.append(
            f"- **{page}** on {k.split('/')[0]}: {v['pixels']} px differ, box {v.get('box')} ({why})  "
        )
        lines.append(f"  ![diff](diff/{k}.png)")
    lines += ["", f"## Changed as expected: {len(wanted)} (review each picture)"]
    for k, page, v in wanted:
        lines.append(f"- {page} on {k.split('/')[0]}: {v['pixels']} px, box {v.get('box')}  ")
        lines.append(f"  ![diff](diff/{k}.png)")
    noisy_pages = sorted(noisy)
    lines += [
        "",
        f"## Noisy: drawn differently each time, compared without the parts that move: {len(noisy_pages)}",
    ]
    for k in noisy_pages:
        lines.append(f"- {_page_of(index, k)} on {k.split('/')[0]} ![noise](noise/diff/{k}.png)")
    missing = sorted(
        p for p in expected if not any(_page_of(index, k).split("?")[0] == p.split("?")[0] for k in changed)
    )
    if missing:
        lines += ["", "## Expected to change but did not (is the change on the page?)"]
        lines += [f"- {p}" for p in missing]
    lines += ["", f"## Could be impacted, from the code's dependencies: {len(could.pages)}"]
    lines += [f"- {p}" for p in sorted(could.pages)]
    (out / "report.md").write_text("\n".join(lines))
    (out / "result.json").write_text(
        json.dumps(
            {
                "unexpected": [p for _, p, _ in unexpected],
                "expected": [p for _, p, _ in wanted],
                "noisy": [_page_of(index, k) for k in sorted(noisy)],
                "missing": missing,
            },
            indent=1,
        )
    )
    print(f"{len(unexpected)} unexpected, {len(wanted)} expected, {len(noise)} noisy -> {out / 'report.md'}")
    return 1 if unexpected else 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m tests.shot_diff")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("out", type=Path)
    d = sub.add_parser("diff")
    d.add_argument("before", type=Path)
    d.add_argument("after", type=Path)
    d.add_argument("out", type=Path)
    r = sub.add_parser("run")
    r.add_argument("out", type=Path)
    r.add_argument("--base")
    r.add_argument("--map", type=Path)
    for p in (c, r):
        p.add_argument("--displays", default=DEFAULT_DISPLAYS)
        p.add_argument("--pages", default="")
        p.add_argument("--themes", default="light")
    a = ap.parse_args(argv)
    only = [x for x in a.pages.split(",") if x] if hasattr(a, "pages") else []
    if a.cmd == "capture":
        return capture(a.out, a.displays.split(","), only, a.themes.split(","))
    if a.cmd == "diff":
        res = diff(a.before, a.after, a.out)
        n = sum(1 for v in res.values() if v["pixels"] < 0 or v["pixels"] >= MIN_PIXELS)
        print(f"{n} of {len(res)} pictures differ")
        return 1 if n else 0
    sys.path.insert(0, str(ROOT))
    from tests.impact_map import default_base

    return run(a.out, a.base or default_base(), a.map, a.displays, only, a.themes)


if __name__ == "__main__":
    # run as a file: its own folder must not shadow anything; the tree to draw is on PYTHONPATH
    if sys.path and Path(sys.path[0]).resolve() == HERE.parent:
        sys.path.pop(0)
    if str(Path.cwd()) not in sys.path:
        sys.path.insert(0, str(Path.cwd()))
    raise SystemExit(main())
