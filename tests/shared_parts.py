"""Where pages draw their own version of a shared part instead of using the shared one.

The standard (docs/UI-STANDARD.md) gives each part one look: the page title (ui.page_head), the
section heading, the button and its kinds, the chip, the panel, the box (ui.box, static/box.js) and
the menu (static/cardmenu.js, static/menus.js). Their looks live in the shared sheets (theme.py,
menus.py). A page that restyles one of them in its own stylesheet, writes one's markup by hand, or
sets one's look from its script, grows a variant: the next change to the shared part does not
reach it. That is how a fix "in one place" leaves the rest behind.

This module counts those, per file. tests/test_shared_parts.py holds the counts to
tests/shared_parts_baseline.json: a file may only go down. ``python -m tests.shared_parts`` prints
the table; ``--lower`` writes the new, lower counts into the baseline after a migration.
"""

# ruff: noqa: E501 - its patterns read best on one line
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "mtg_gateway"
BASELINE = Path(__file__).resolve().parent / "shared_parts_baseline.json"

# the shared sheets and the shared builders: the one place each part is drawn
SHARED = {"theme.py", "menus.py", "ui.py", "box.js", "cardmenu.js", "menus.js", "layers.js", "topbar.js"}

# the parts and what selects them (the last compound of a selector)
PARTS = {
    "title": r"(^|[\s>+~,])h1\b",
    "heading": r"(^|[\s>+~,])h[23]\b",
    "button": r"(^|[\s>+~,])button\b|\.btn\b|\.ui-cap\b|\.kebab\b",
    "chip": r"\.ui-chip\b|\.chip\b|\.pill\b|\.ui-tag\b|\.ui-lbl\b",
    "panel": r"\.panel\b|\.ui-card\b",
    "box": r"\.ui-box\b|\.mtgbox\b|\[role=dialog\]|(^|[\s>+~,])dialog\b",
    "menu": r"\.ctxmenu\b|\.mtgmenu\b|\.menu\b|\[role=menu\]",
}
# the properties that make a look (where a part sits on a page is the page's own business)
LOOK = {
    "font-size",
    "font-weight",
    "font-family",
    "border-radius",
    "border",
    "border-color",
    "border-width",
    "background",
    "background-color",
    "padding",
    "padding-left",
    "padding-right",
    "padding-top",
    "padding-bottom",
    "height",
    "min-height",
    "box-shadow",
    "color",
    "text-transform",
    "letter-spacing",
    "line-height",
}

RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _strip_at(css: str) -> str:
    # @media (...) { a{...} } -> a{...}; the wrapper braces are dropped, the rules kept
    return re.sub(r"@[^{]+\{", "", re.sub(r"/\*.*?\*/", "", css, flags=re.S))


def restyles(css: str) -> list[tuple[str, str, str]]:
    """(part, selector, property) for each look a stylesheet gives a shared part."""
    out = []
    for m in RULE_RE.finditer(_strip_at(css)):
        decls = [d.split(":", 1)[0].strip().lower() for d in m.group(2).split(";") if ":" in d]
        looks = [d for d in decls if d in LOOK]
        if not looks:
            continue
        for sel in m.group(1).split(","):
            sel = sel.strip()
            if not sel or sel.startswith(("@", "from", "to", "0%", "100%")):
                continue
            last = re.split(r"\s*[\s>+~]\s*", sel)[-1]
            last = re.sub(r"::?[a-z-]+(\([^)]*\))?", "", last)  # :hover, ::before
            for part, pat in PARTS.items():
                # a box's or a menu's insides are the layer's look too; other parts, the part itself
                where = sel if part in ("box", "menu") else last
                if re.search(pat, " " + where):
                    out += [(part, sel, p) for p in looks]
                    break
    return out


def module_css(name: str) -> str:
    mod = importlib.import_module(f"mtg_gateway.{name}")
    parts = []
    for attr in dir(mod):
        v = getattr(mod, attr)
        if attr.endswith("CSS") and isinstance(v, str):
            parts.append(v)
    return "\n".join(parts)


# markup a page writes by hand that a shared builder makes
MARKUP = {
    "title": re.compile(r"<h1[\s>]"),
    "box": re.compile(r"role=['\"]dialog|aria-modal=|<dialog[\s>]"),
    "menu": re.compile(r"role=['\"]menu['\"]|className\s*=\s*['\"][^'\"]*\bctxmenu\b"),
}
# a script that sets a shared part's look itself
SCRIPT_LOOK = re.compile(
    r"\.style\.(fontSize|fontWeight|borderRadius|border|background|backgroundColor|padding|boxShadow)\s*="
)


def census() -> dict[str, dict[str, int]]:
    """file -> {kind: count}, for every file outside the shared ones that has any."""
    out: dict[str, dict[str, int]] = {}
    for f in sorted(SRC.glob("*.py")):
        if f.name in SHARED or f.name.startswith("_"):
            continue
        counts: dict[str, int] = {}
        try:
            css = module_css(f.stem)
        except Exception:  # noqa: BLE001 - a module that cannot import on its own has no sheet here
            css = ""
        for part, _sel, _prop in restyles(css):
            counts[f"css:{part}"] = counts.get(f"css:{part}", 0) + 1
        text = f.read_text()
        for part, pat in MARKUP.items():
            n = len(pat.findall(text))
            if n:
                counts[f"markup:{part}"] = n
        if counts:
            out[f.name] = counts
    for f in sorted((SRC / "static").glob("*.js")) + sorted((SRC / "scan" / "static").glob("*.js")):
        if f.name in SHARED:
            continue
        text = f.read_text()
        counts = {}
        for part, pat in MARKUP.items():
            n = len(pat.findall(text))
            if n:
                counts[f"markup:{part}"] = n
        n = len(SCRIPT_LOOK.findall(text))
        if n:
            counts["script:look"] = n
        # a stylesheet a script injects
        for block in re.findall(r"`([^`]*\{[^`]*\})`", text):
            for part, _sel, _prop in restyles(block):
                counts[f"css:{part}"] = counts.get(f"css:{part}", 0) + 1
        if counts:
            out[
                f"static/{f.name}"
                if f.parent.name == "static" and f.parent.parent == SRC
                else f"scan/{f.name}"
            ] = counts
    return out


def compare(now: dict[str, dict[str, int]], base: dict[str, dict[str, int]]) -> tuple[list[str], list[str]]:
    """(went up, went down): a file whose count of a kind is above the baseline, and below it."""
    up, down = [], []
    for f in sorted(set(now) | set(base)):
        for k in sorted(set(now.get(f, {})) | set(base.get(f, {}))):
            a, b = base.get(f, {}).get(k, 0), now.get(f, {}).get(k, 0)
            if b > a:
                up.append(f"{f}: {k} {a} -> {b}")
            elif b < a:
                down.append(f"{f}: {k} {a} -> {b}")
    return up, down


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m tests.shared_parts")
    ap.add_argument("--lower", action="store_true", help="write lower counts into the baseline")
    ap.add_argument("--init", action="store_true", help="write the baseline from scratch")
    a = ap.parse_args(argv)
    now = census()
    if a.init:
        BASELINE.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n")
        print(f"baseline written: {sum(sum(v.values()) for v in now.values())} across {len(now)} files")
        return 0
    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    up, down = compare(now, base)
    totals: dict[str, int] = {}
    for counts in now.values():
        for k, n in counts.items():
            totals[k] = totals.get(k, 0) + n
    print("Own versions of shared parts, by kind:", json.dumps(totals, sort_keys=True))
    for f, counts in sorted(now.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {sum(counts.values()):4d}  {f}  {counts}")
    for u in up:
        print("UP:", u)
    for d in down:
        print("down:", d)
    if a.lower and not up:
        merged = {f: {k: min(v, base.get(f, {}).get(k, v)) for k, v in c.items()} for f, c in now.items()}
        BASELINE.write_text(json.dumps(merged, indent=1, sort_keys=True) + "\n")
        print("baseline lowered")
    return 1 if up else 0


if __name__ == "__main__":
    raise SystemExit(main())
