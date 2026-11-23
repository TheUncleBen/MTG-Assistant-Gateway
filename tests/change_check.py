"""The change check: one command a builder runs before every push. No AI, no network.

``python -m tests.change_check OUT_DIR --map MAP.md [--base REF] [--quick]``

It runs, in order, and stops at nothing (every result is in ``OUT_DIR/CHECK.md``):

1. lint and format (ruff), as CI does;
2. the impact map against the diff (tests/impact_map.py check): every place the map lists was
   changed, nothing the map does not list was (scope creep), every surface considered;
3. what the change could impact, from the code's dependencies (tests/impact_map.py deps): the pages,
   modules, scripts and tests that use what changed;
4. those tests, plus the checks that hold every reported problem (tests/issue_rules.py) that need
   no browser, and the real-task tests on a phone, an open Fold and a laptop;
5. the click sweep (tests/ui_clicks.py; a finding fails only when the base, pressed the same way,
   does not have it too), the look census (tests/ui_cohesion.py) and the layers
   census (tests/ui_layers.py) on the pages that could be impacted;
6. before and after screenshots of EVERY page (tests/shot_diff.py): a page that changed and is not
   in the map's "Pages expected to look different" fails.

``--quick`` skips 5 and 6 (for a change with no page in it: a tool, a doc). The full suite still
runs in CI on the push; this is what a builder sees before it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# the checks every change runs, whatever it touches: cheap, and they hold the reported problems
ALWAYS = [
    "tests/test_shared_parts.py",
    "tests/test_issue_rules.py",
    "tests/test_impact_map.py",
    "tests/test_tasks_browser.py",
]


def _run(cmd: list[str], log: Path, env: dict | None = None) -> tuple[int, float]:
    t = time.time()
    with log.open("w") as f:
        r = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)
    return r.returncode, time.time() - t


def _click_key(r: dict) -> tuple:
    """A finding, the same on the base and the change: the page, the screen, what was pressed and
    what went wrong (numbers in the detail, such as ids, left out)."""
    import re

    detail = re.sub(r"\d+", "#", str(r.get("detail") or ""))
    return (r.get("display"), r["page"], r.get("kind"), r.get("text"), r.get("verdict"), detail)


def _base_clicks(base: str, pages: list[str], out: Path, env: dict) -> list[dict]:
    """The click sweep on the base commit (a temporary worktree), on the given pages only."""
    import shutil
    import tempfile

    if not pages:
        return []
    wt = Path(tempfile.mkdtemp(prefix="clickbase"))
    subprocess.run(["git", "worktree", "add", "--detach", str(wt), base], cwd=ROOT, capture_output=True)
    try:
        if not (wt / "tests" / "ui_clicks.py").exists():
            return []  # the base has no sweep: every finding counts as new
        benv = dict(env, PYTHONPATH=f"{wt / 'src'}{os.pathsep}{wt}")
        if out.exists():
            shutil.rmtree(out)
        cmd = [sys.executable, "-m", "tests.ui_clicks", str(out), "--pages", ",".join(pages)]
        with (out.parent / "clicks-base.log").open("w") as f:
            subprocess.run([*cmd, "--displays", "app,laptop"], cwd=wt, env=benv, stdout=f, stderr=f)
        res = out / "clicks.json"
        return json.loads(res.read_text()) if res.exists() else []
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True)


def _write_click_findings(path: Path, new: list[dict], old: int) -> None:
    lines = [
        "# Controls this change broke",
        "",
        f"{len(new)} new; {old} more the base has too (left for the release run).",
        "",
    ]
    for r in new:
        lines.append(
            f"- **{r.get('verdict')}** {r.get('display')} {r['page']}: {r.get('kind', '')} "
            f"{r.get('text', '')!r} {r.get('detail') or ''}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m tests.change_check")
    ap.add_argument("out", type=Path)
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--base")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", default="auto")
    ap.add_argument("--max-tests", type=int, default=60, help="most test files run here (the rest: CI)")
    ap.add_argument("--max-sweep", type=int, default=12, help="most pages the click sweep presses")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from tests import impact_map as IM

    base = a.base or IM.default_base()
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{ROOT / 'src'}{os.pathsep}{ROOT}"
    py = sys.executable
    steps: list[tuple[str, int, float, str]] = []

    def step(name: str, cmd: list[str], log: str) -> int:
        code, secs = _run(cmd, out / log, env)
        steps.append((name, code, secs, log))
        print(f"{'ok  ' if code == 0 else 'FAIL'} {name} ({secs:.0f} s) -> {out / log}", flush=True)
        return code

    step("lint", ["ruff", "check", "src", "tests", "scripts"], "lint.log")
    step("format", ["ruff", "format", "--check", "src", "tests", "scripts"], "format.log")
    step("impact map", [py, "-m", "tests.impact_map", "check", str(a.map), "--base", base], "map.log")
    d = IM.deps(base)
    (out / "deps.json").write_text(
        json.dumps(
            {
                "files": sorted(d.files),
                "pages": sorted(d.pages),
                "tests": sorted(d.tests),
                "modules": sorted(d.modules),
            },
            indent=1,
        )
    )
    from tests.issue_rules import RULES

    rule_tests = {c.split("::")[0] for r in RULES for c in r.checks if not c.startswith("tool:")}
    tests = sorted(set(d.tests))
    note = "the ones that use what changed, the rule checks and the task tests"
    if len(tests) > a.max_tests:
        # a change that reaches the shared shell or sheet reaches most of the suite: here, the tests
        # of the files the diff changed itself; CI runs the whole suite on the push, nothing merges red
        direct = set()
        for f in d.files:
            m = IM.file_module(f)
            if m:
                short = m.rsplit(".", 1)[-1]
                direct |= IM.rg_files(
                    rf"(from mtg_gateway(\.\w+)* import .*\b{short}\b|mtg_gateway\.{short}\b)", "tests"
                )
            elif f.startswith("src/") and f.endswith(".js"):
                direct |= IM.rg_files(Path(f).name, "tests", fixed=True)
        direct = {t for t in direct if Path(t).name.startswith("test_")}
        note = (
            f"{len(tests)} files use what changed: here the {len(direct)} that test the changed files "
            "directly, the rule checks and the task tests; CI runs the whole suite on the push"
        )
        tests = sorted(direct)
    tests = sorted(set(tests) | set(ALWAYS) | {t for t in rule_tests if "_browser" not in t or t in ALWAYS})
    step(
        f"tests ({len(tests)} files: {note})",
        [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", a.workers, *tests],
        "tests.log",
    )
    pages = sorted(p for p in d.pages if "{pending}" not in p)
    # the click sweep presses every control from a fresh page (seconds a press): on a change that
    # reaches every page (the shell, the shared sheet) it would take hours. Then it presses the pages
    # the map expects to change and the pages whose own code changed; the screenshots still cover
    # every page, and the release run sweeps every page.
    expected = IM.parse_map(a.map.read_text()).expected_pages
    swept_note = ""
    if len(pages) > a.max_sweep:
        own = {m for m in (IM.file_module(f) for f in d.files) if m}
        rt = IM.routes()
        direct = IM.pages_for({t for m in own for _h, t in rt.get(m, ())})
        narrowed = sorted(p for p in pages if p in expected or p.split("?")[0] in expected or p in direct)
        swept_note = f"{len(pages) - len(narrowed)} of {len(pages)} impacted pages left to the release sweep"
        pages = narrowed
    if not a.quick and pages:
        joined = ",".join(pages)
        clicks = out / "clicks"
        step(
            f"click sweep on {len(pages)} pages",
            [py, "-m", "tests.ui_clicks", str(clicks), "--pages", joined, "--displays", "app,laptop"],
            "clicks.log",
        )
        rows = json.loads((clicks / "clicks.json").read_text()) if (clicks / "clicks.json").exists() else []
        bad = [r for r in rows if r.get("verdict") not in ("ok", "external")]
        # a problem the base already had is not this change's: press the same pages on the base and
        # fail only on what is new (the old ones are listed, for the release run to fix)
        t = time.time()
        old = _base_clicks(base, sorted({r["page"] for r in bad}), out / "clicks-base", env) if bad else []
        known = {_click_key(r) for r in old}
        new = [r for r in bad if _click_key(r) not in known]
        _write_click_findings(out / "clicks" / "new.md", new, len(bad) - len(new))
        steps.append(
            (
                f"no new dead or broken control ({len(new)} new, {len(bad) - len(new)} the base has too)",
                1 if new else 0,
                time.time() - t,
                "clicks/new.md",
            )
        )
        if swept_note:
            steps.append((f"click sweep narrowed: {swept_note}", 0, 0, ""))
        step(
            f"look census on {len(pages)} pages",
            [py, "-m", "tests.ui_cohesion", str(out / "census"), "--pages", joined],
            "census.log",
        )
        step(
            f"boxes and menus on {len(pages)} pages",
            [py, "-m", "tests.ui_layers", str(out / "layers"), "--pages", joined],
            "layers.log",
        )
    if not a.quick:
        step(
            "before and after screenshots, every page",
            [py, "-m", "tests.shot_diff", "run", str(out / "shots"), "--base", base, "--map", str(a.map)],
            "shots.log",
        )
    failed = [s for s in steps if s[1] != 0]
    lines = [
        "# Change check",
        "",
        f"Base {base[:12]}. {len(steps)} steps, {len(failed)} failed.",
        "",
        "| step | result | time | details |",
        "|---|---|---|---|",
    ]
    for name, code, secs, log in steps:
        lines.append(f"| {name} | {'pass' if code == 0 else '**FAIL**'} | {secs:.0f} s | {log} |")
    lines += ["", "## Pages that could be impacted", *[f"- {p}" for p in sorted(d.pages)]]
    if not a.quick:
        lines += [
            "",
            "Review: census/census.md, layers/layers.md and shots/report.md"
            " (the pictures of every page that changed).",
        ]
    (out / "CHECK.md").write_text("\n".join(lines))
    print(f"{len(failed)} of {len(steps)} steps failed -> {out / 'CHECK.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
