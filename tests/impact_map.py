"""The impact map: what a change touches, found from the code's real dependencies, and a check that
the change did everything its map says and nothing more.

A request in plain words ("rename Torch to Flash") reaches many places: the page that draws it, the
script that changes it, the stylesheet, the Android app, the Claude and ChatGPT cards, the assistant
tools, the docs. A change that fixes one of them and leaves the rest is what this file is for.

Three commands, all plain scripts (no AI, no network):

``python -m tests.impact_map find WORD [WORD ...]``
    Every line in every surface (web pages, scripts and styles, Android, cards, assistant tools,
    plugin and skill, docs, tests) that holds one of the words, grouped by surface: the starting
    list for the map a builder writes before changing anything.

``python -m tests.impact_map deps [--base REF] [--index PAGES.json] [--json]``
    From the diff against REF (default: the merge base with origin/main): the symbols the diff
    changed (functions, classes, constants, CSS classes, element ids, scripts), every module, script
    and test that uses them, and the pages those reach through the app's routes. With ``--index``
    (written by tests/shot_diff.py: each page's classes and scripts as the browser saw them) a page
    whose drawn markup holds a changed CSS class or loads a changed script is included too. This is
    what "could be impacted" means: every page here gets its screenshots compared and its task tests
    run. When in doubt a page is included.

``python -m tests.impact_map check MAP.md [--base REF]``
    The map, written in the format below, against the diff. It fails when a box is not ticked, a
    surface was not considered, a file the map says to change is not changed (a missed place), or
    the diff changes a file the map does not list (scope creep).

The map's format (markdown; the headings are fixed, the words are the builder's)::

    # Impact map: <the request in a line>
    ## Change
    - [x] src/mtg_gateway/scan/static/scan.js: the button's word
    - [x] docs/SCANNING.md: the guide's word
    ## Checked, no change
    - [x] android/app/src/main/res/values/strings.xml: the app has no such button
    ## Surfaces
    - [x] Web pages: /scan
    - [x] Android app: none, the app shows the website's page
    - [x] Claude and ChatGPT cards: none, no card shows it
    - [x] Assistant tools: none
    - [x] Plugin and skill: none
    - [x] Docs: SCANNING.md
    ## Pages expected to look different
    - /scan

"Pages expected to look different" is what tests/shot_diff.py holds the screenshots to: any other
page that looks different fails the round.
"""

# ruff: noqa: C901, E501 - each command is one pass over the diff; its patterns read best on one line
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "mtg_gateway"

# Where a change can land. Order is the order the map lists them.
SURFACES: list[tuple[str, tuple[str, ...]]] = [
    ("Web pages", ("src/mtg_gateway/*.py", "src/mtg_gateway/scan/*.py")),
    ("Web scripts and styles", ("src/mtg_gateway/static/*", "src/mtg_gateway/scan/static/*.js")),
    ("Android app", ("android/",)),
    (
        "Claude and ChatGPT cards",
        ("src/mtg_gateway/cards.py", "src/mtg_gateway/approve.py", "src/mtg_gateway/static/cards/*"),
    ),
    (
        "Assistant tools",
        (
            "src/mtg_gateway/tools_*.py",
            "src/mtg_gateway/api.py",
            "src/mtg_gateway/tool_labels.py",
            "src/mtg_gateway/refusals.py",
            "src/mtg_gateway/app.py",
        ),
    ),
    (
        "Plugin and skill",
        (
            "plugin/",
            "docs/SKILL.md",
            "src/mtg_gateway/skill_page.py",
            "src/mtg_gateway/plugin_page.py",
            "src/mtg_gateway/chatgpt_plugin.py",
        ),
    ),
    ("Docs", ("docs/", "README.md", "CHANGELOG.md", "src/mtg_gateway/guide.py")),
    ("Tests", ("tests/",)),
]
MAP_SURFACES = [s for s, _ in SURFACES if s not in ("Tests", "Web scripts and styles")]

# words that look like a CSS class after a dot but are file endings, modules or attributes
NOT_CLASSES = {
    "py",
    "js",
    "css",
    "png",
    "svg",
    "json",
    "md",
    "com",
    "test",
    "html",
    "txt",
    "csv",
    "zip",
    "apk",
    "io",
    "org",
    "net",
    "jpg",
    "webp",
    "ico",
    "yml",
    "yaml",
    "toml",
    "env",
    "kt",
    "kts",
    "xml",
    "gz",
    "map",
    "min",
    "then",
    "catch",
    "length",
    "push",
    "join",
    "filter",
    "value",
    "style",
    "classlist",
    "dataset",
    "add",
    "remove",
    "toggle",
    "contains",
    "closest",
    "matches",
    "get",
    "set",
    "items",
    "keys",
    "values",
    "append",
    "format",
    "strip",
    "split",
    "lower",
    "upper",
    "replace",
}
CLASS_RE = re.compile(r"(?<![\w$/])\.([a-z][a-z0-9_-]*[a-z0-9])(?![\w(-])")
ATTR_CLASS_RE = re.compile(r"class(?:Name)?=['\"\\]*([^'\"\\{}]+)")
LIST_CLASS_RE = re.compile(r"classList\.(?:add|remove|toggle|contains)\(([^)]*)\)")
ID_RE = re.compile(r"(?:id=['\"]|getElementById\(['\"]|#)([a-z][a-z0-9_-]{2,})")


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def default_base() -> str:
    for ref in ("origin/main", "main"):
        out = git("merge-base", "HEAD", ref, check=False).strip()
        if out:
            return out
    raise SystemExit("no base: pass --base REF")


def changed_files(base: str) -> list[str]:
    """Files the diff against ``base`` changes: commits and the working tree, new files included."""
    out = set(git("diff", "--name-only", base).split())
    out |= set(git("ls-files", "--others", "--exclude-standard").split())
    return sorted(out)


@dataclass
class Hunk:
    path: str
    new_lines: list[int] = field(default_factory=list)  # changed line numbers in the new file
    old_lines: list[int] = field(default_factory=list)  # removed line numbers in the old file
    text: list[str] = field(default_factory=list)  # the added and removed lines
    removed: list[str] = field(default_factory=list)  # the removed lines alone


def hunks(base: str) -> dict[str, Hunk]:
    out: dict[str, Hunk] = {}
    diff = git("diff", "-U0", "--no-color", base)
    cur: Hunk | None = None
    old_no = new_no = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            p = line[4:]
            p = p[2:] if p.startswith("b/") else p
            cur = None if p == "/dev/null" else out.setdefault(p, Hunk(p))
        elif line.startswith("--- "):
            p = line[4:]
            if p != "/dev/null":
                p = p[2:] if p.startswith("a/") else p
                cur = out.setdefault(p, Hunk(p))
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            old_no, new_no = int(m.group(1)), int(m.group(2))
        elif cur is not None and line.startswith("+"):
            cur.new_lines.append(new_no)
            cur.text.append(line[1:])
            new_no += 1
        elif cur is not None and line.startswith("-"):
            cur.old_lines.append(old_no)
            cur.text.append(line[1:])
            cur.removed.append(line[1:])
            old_no += 1
    for p in git("ls-files", "--others", "--exclude-standard").split():
        f = ROOT / p
        if f.is_file() and p not in out:
            try:
                lines = f.read_text().splitlines()
            except UnicodeDecodeError:
                lines = []
            out[p] = Hunk(p, list(range(1, len(lines) + 1)), [], lines)
    return out


def _py_units(source: str) -> list[tuple[int, int, str]]:
    """(first line, last line, name) of every top-level def, class and assignment, and of every
    method as ``Class.method`` (listed after its class, so the innermost unit is found last)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    units = []

    def first(node: ast.AST) -> int:
        return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])

    for node in tree.body:
        names: list[str] = []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        for n in names:
            units.append((first(node), node.end_lineno or node.lineno, n))
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                    units.append((first(sub), sub.end_lineno or sub.lineno, f"{node.name}.{sub.name}"))
    return units


def _innermost(units: list[tuple[int, int, str]], ln: int) -> str | None:
    hit = [(b - a, n) for a, b, n in units if a <= ln <= b]
    return min(hit)[1] if hit else None


JS_FN_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)|"
    r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"
)


def _js_names(lines: list[str], wanted: list[int]) -> set[str]:
    """The named functions the given line numbers sit in (the nearest one above each line)."""
    starts = []
    for i, line in enumerate(lines, 1):
        m = JS_FN_RE.match(line)
        if m:
            starts.append((i, m.group(1) or m.group(2)))
    out = set()
    for w in wanted:
        above = [n for i, n in starts if i <= w]
        if above:
            out.add(above[-1])
    return out


def _file_at(base: str, path: str) -> str:
    return git("show", f"{base}:{path}", check=False)


@dataclass
class Symbols:
    python: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))  # module -> names
    whole_modules: set[str] = field(default_factory=set)  # changed outside any named unit
    js: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))  # script -> functions
    scripts: set[str] = field(default_factory=set)  # changed script file names
    classes: set[str] = field(default_factory=set)
    ids: set[str] = field(default_factory=set)


def _classes_in(text: str) -> set[str]:
    out = set()
    for m in CLASS_RE.finditer(text):
        c = m.group(1)
        if c.lower() not in NOT_CLASSES and not c.isdigit():
            out.add(c)
    for m in ATTR_CLASS_RE.finditer(text):
        out |= {c for c in re.split(r"\s+", m.group(1)) if re.fullmatch(r"[a-z][a-z0-9_-]+", c)}
    for m in LIST_CLASS_RE.finditer(text):
        out |= set(re.findall(r"['\"]([a-z][a-z0-9_-]+)['\"]", m.group(1)))
    return out


def symbols(base: str, hs: dict[str, Hunk]) -> Symbols:
    s = Symbols()
    for path, h in hs.items():
        text = "\n".join(h.text)
        if path.startswith("src/") and path.endswith((".py", ".js", ".css", ".html")):
            s.classes |= _classes_in(text)
            s.ids |= {m.group(1) for m in ID_RE.finditer(text)}
        if path.startswith("src/") and path.endswith(".py"):
            mod = path[len("src/") : -3].replace("/", ".")
            for src, lines in ((_safe_read(path), h.new_lines), (_file_at(base, path), h.old_lines)):
                units = _py_units(src)
                for ln in lines:
                    hit = _innermost(units, ln)
                    if hit:
                        s.python[mod].add(hit)
                    elif src.splitlines()[ln - 1 : ln] and src.splitlines()[ln - 1].strip():
                        s.whole_modules.add(mod)
        elif path.startswith("src/") and path.endswith(".js"):
            name = Path(path).name
            s.scripts.add(name)
            s.js[name] |= _js_names(_safe_read(path).splitlines(), h.new_lines)
            s.js[name] |= _js_names(_file_at(base, path).splitlines(), h.old_lines)
    # names any page would use that say nothing on their own
    s.classes -= {"on", "open", "active", "hidden", "sel", "x", "a", "b", "c"}
    return s


def _safe_read(path: str) -> str:
    f = ROOT / path
    try:
        return f.read_text() if f.is_file() else ""
    except UnicodeDecodeError:
        return ""


def _rg(args: list[str]) -> str:
    """ripgrep's answer (``rg`` + args, run from the repository): ripgrep where it is installed,
    else the same search in Python, for the flags this file uses (-l -n -i -F -w --no-heading
    --no-messages -e PATTERN -- PATHS). A CI runner has no ripgrep."""
    if shutil.which("rg"):
        return subprocess.run(["rg", *args], cwd=ROOT, capture_output=True, text=True).stdout
    flags = set(args[: args.index("-e")])
    pattern = args[args.index("-e") + 1]
    paths = args[args.index("--") + 1 :]
    body = re.escape(pattern) if "-F" in flags else pattern
    if "-w" in flags:
        body = rf"\b(?:{body})\b"
    rx = re.compile(body, re.IGNORECASE if "-i" in flags else 0)
    files: list[Path] = []
    for p in paths:
        f = ROOT / p
        if f.is_dir():  # as ripgrep: no hidden files, no caches
            files += sorted(
                x
                for x in f.rglob("*")
                if x.is_file()
                and not any(q.startswith(".") or q == "__pycache__" for q in x.relative_to(ROOT).parts)
            )
        elif f.is_file():
            files.append(f)
    out = []
    for f in files:
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        rel = f.relative_to(ROOT).as_posix()
        if "-l" in flags:
            if rx.search(text):
                out.append(rel)
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                out.append(f"{rel}:{n}:{line}")
    return "\n".join(out)


def rg_files(pattern: str, *paths: str, fixed: bool = False, word: bool = False) -> set[str]:
    args = ["rg", "-l", "--no-messages"]
    if fixed:
        args.append("-F")
    if word:
        args.append("-w")
    args += ["-e", pattern, "--", *paths]
    return set(_rg(args[1:]).split())


def import_graph() -> dict[str, set[str]]:
    """module -> the modules that import it (mtg_gateway only)."""
    rev: dict[str, set[str]] = defaultdict(set)
    for f in SRC.rglob("*.py"):
        mod = "mtg_gateway." + ".".join(f.relative_to(SRC).with_suffix("").parts)
        mod = mod.removesuffix(".__init__")
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        pkg = mod.rsplit(".", 1)[0] if not f.name == "__init__.py" else mod
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = pkg.split(".")
                    up = parts[: len(parts) - node.level + 1]
                    base = ".".join(up + ([base] if base else []))
                if base.startswith("mtg_gateway"):
                    rev[base].add(mod)
                    for a in node.names:
                        rev[f"{base}.{a.name}"].add(mod)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("mtg_gateway"):
                        rev[a.name].add(mod)
    return rev


def module_file(mod: str) -> str:
    p = Path("src", *mod.split("."))
    return str(p.with_suffix(".py")) if (ROOT / p.with_suffix(".py")).exists() else str(p / "__init__.py")


def file_module(path: str) -> str | None:
    if not (path.startswith("src/") and path.endswith(".py")):
        return None
    return path[len("src/") : -3].replace("/", ".").removesuffix(".__init__")


_ROUTES: dict[str, list[tuple[str, str]]] | None = None


def routes() -> dict[str, list[tuple[str, str]]]:
    """module -> (handler name, path template) of every route it serves, read from the app itself."""
    global _ROUTES
    if _ROUTES is None:
        import tempfile

        sys.path.insert(0, str(ROOT))
        from mtg_gateway.app import create_app
        from mtg_gateway.db import Database
        from tests.conftest import make_settings

        s = make_settings(Path(tempfile.mkdtemp(prefix="impact")))
        app = create_app(s, db=Database(s.db_path))
        out: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for r in app.routes:
            ep = getattr(r, "endpoint", None)
            m = getattr(ep, "__module__", None) or ""
            if m.startswith("mtg_gateway"):
                out[m].append((getattr(ep, "__name__", ""), getattr(r, "path", "")))
        _ROUTES = dict(out)
    return _ROUTES


def sample_pages() -> list[str]:
    from tests.ui_matrix import pages

    return [p for p in pages("PENDING") if "#" not in p]


def _template_re(t: str) -> re.Pattern:
    out = re.sub(r"\{[^}:]+:path\}", ".+", t)
    out = re.sub(r"\{[^}]+\}", "[^/]+", out)
    return re.compile("^" + out + "$")


def pages_for(templates: set[str]) -> set[str]:
    out = set()
    pats = [_template_re(t) for t in templates]
    for p in sample_pages():
        path = p.split("?")[0].split("#")[0]
        if any(r.match(path) for r in pats):
            out.add(p.replace("PENDING", "{pending}"))
    return out


@dataclass
class Deps:
    files: list[str]
    symbols: Symbols
    modules: set[str]
    scripts: set[str]
    pages: set[str]
    tests: set[str]
    surfaces: dict[str, set[str]]
    why: dict[str, set[str]]  # page -> reasons


def _is_css(node_src: str) -> bool:
    return node_src.count("{") >= 2 and ":" in node_src and ";" in node_src


_UNITS: dict[str, list[tuple[int, int, str]]] = {}


def units_of(path: str) -> list[tuple[int, int, str]]:
    if path not in _UNITS:
        _UNITS[path] = _py_units(_safe_read(path))
    return _UNITS[path]


def units_naming(path: str, pattern: re.Pattern) -> set[str]:
    """The top-level units of a module whose source holds the pattern (a module-level line that
    holds it counts as the whole module)."""
    lines = _safe_read(path).splitlines()
    out = set()
    for i, line in enumerate(lines, 1):
        if pattern.search(line):
            if re.match(r"\s*(from\s+\S+\s+)?import\b", line) or line.lstrip().startswith(("#", ")")):
                continue  # an import says nothing about use; its users are found by name
            hit = _innermost(units_of(path), i)
            out.add(hit or "*")
    return out


# method names so common (dict.get, file.read) that a call says nothing about which class it is
GENERIC_METHODS = {
    "get",
    "read",
    "write",
    "apply",
    "index",
    "text",
    "items",
    "keys",
    "values",
    "update",
    "add",
    "run",
    "close",
    "open",
    "json",
    "copy",
    "pop",
    "list",
    "count",
    "find",
    "check",
    "save",
    "load",
    "delete",
    "remove",
    "set",
    "clear",
    "start",
    "stop",
    "send",
    "call",
    "put",
    "post",
    "fetch",
    "render",
    "parse",
    "__init__",
    "__call__",
    "__enter__",
    "__exit__",
    "__aenter__",
    "__aexit__",
    "__repr__",
    "__str__",
}


@dataclass
class Unit:
    names: set[str]  # bare names it reads (from an import or its own module)
    qualified: set[str]  # "module.name" it reads (``ui.back_link``)
    attrs: set[str]  # every attribute it reads (``.propose``)


_REFS: dict[str, dict[str, Unit]] = {}


def refs(mod: str) -> dict[str, Unit]:
    """unit name -> what the unit's code (not its strings or comments) refers to; "*" is the
    module's own top-level code outside any unit."""
    if mod in _REFS:
        return _REFS[mod]
    path = module_file(mod)
    src = _safe_read(path)
    out: dict[str, Unit] = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        _REFS[mod] = out
        return out

    def collect(nodes: list[ast.AST]) -> Unit:
        u = Unit(set(), set(), set())
        for top in nodes:
            for n in ast.walk(top):
                if isinstance(n, ast.Name):
                    u.names.add(n.id)
                elif isinstance(n, ast.Attribute):
                    u.attrs.add(n.attr)
                    if isinstance(n.value, ast.Name):
                        u.qualified.add(f"{n.value.id}.{n.attr}")
        return u

    loose: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            out[node.name] = collect([node])
        elif isinstance(node, ast.ClassDef):
            body = [x for x in node.body if not isinstance(x, ast.FunctionDef | ast.AsyncFunctionDef)]
            out[node.name] = collect(body + node.bases + node.decorator_list)
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                    out[f"{node.name}.{sub.name}"] = collect([sub])
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            named = [t.id for t in targets if isinstance(t, ast.Name)]
            for t in named:
                out[t] = collect([node.value] if node.value else [])
            if not named:
                loose.append(node)
        elif not (isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant)):
            loose.append(node)  # top-level code that is not a docstring
    if loose:
        out["*"] = collect(loose)
    _REFS[mod] = out
    return out


def all_modules() -> list[str]:
    mods = []
    for f in SRC.rglob("*.py"):
        mods.append(
            ("mtg_gateway." + ".".join(f.relative_to(SRC).with_suffix("").parts)).removesuffix(".__init__")
        )
    return mods


def propagate(seeds: dict[str, set[str]], whole: set[str]) -> dict[str, set[str]]:
    """module -> its units that use, directly or through other units, a changed unit: a call graph
    read from the code (names and attributes, never strings). A stylesheet string is not followed:
    what it styles is found by its classes."""
    rev = import_graph()
    mods = all_modules()
    defs: dict[str, int] = defaultdict(int)  # how many units in the package have each name
    for m in mods:
        for un in refs(m):
            defs[un.rsplit(".", 1)[-1]] += 1
    found: dict[str, set[str]] = defaultdict(set)
    todo: list[tuple[str, str]] = [(m, n) for m, ns in seeds.items() for n in ns] + [(m, "*") for m in whole]
    seen: set[tuple[str, str]] = set()
    while todo:
        mod, name = todo.pop()
        if (mod, name) in seen:
            continue
        seen.add((mod, name))
        found[mod].add(name)
        short = mod.rsplit(".", 1)[-1]
        if name == "*":
            # the module's own top-level code: whatever imports it, as a whole
            for user in rev.get(mod, ()):
                todo.append((user, "*"))
            continue
        if name.isupper() and name.endswith("CSS"):
            continue
        hits: list[tuple[str, str]] = []
        if "." in name:
            cls, meth = name.split(".", 1)
            if meth in GENERIC_METHODS or defs.get(meth, 0) > 1:
                # a name more than one class (or dict, or file) answers to: followed only where its
                # class is named or its module imported, and inside the class itself
                scope = [mod] + [
                    m for m in mods if m in rev.get(mod, ()) or any(cls in u.names for u in refs(m).values())
                ]
                for m in scope:
                    for un, u in refs(m).items():
                        if meth in u.attrs and (m != mod or un.startswith(cls + ".") or cls in u.names):
                            hits.append((m, un))
            else:
                # only one class has it: any call by that name is a call to it
                for m in mods:
                    for un, u in refs(m).items():
                        if meth in u.attrs:
                            hits.append((m, un))
        else:
            for un, u in refs(mod).items():
                if name in u.names:
                    hits.append((mod, un))
            for user in set(rev.get(mod, ())) | set(rev.get(f"{mod}.{name}", ())):
                for un, u in refs(user).items():
                    if name in u.names or f"{short}.{name}" in u.qualified:
                        hits.append((user, un))
        for h in hits:
            if h not in seen and h != (mod, name):
                todo.append(h)
    return found


def deps(base: str, index: dict | None = None) -> Deps:
    files = changed_files(base)
    hs = hunks(base)
    sym = symbols(base, hs)
    why: dict[str, set[str]] = defaultdict(set)
    scripts: set[str] = set(sym.scripts)
    seeds: dict[str, set[str]] = defaultdict(set)
    for mod, names in sym.python.items():
        seeds[mod] |= names
    # a script function another script calls
    for _scr, fns in sym.js.items():
        for fn in fns:
            for f in rg_files(
                rf"\b{re.escape(fn)}\b", "src/mtg_gateway/static", "src/mtg_gateway/scan/static"
            ):
                scripts.add(Path(f).name)
    # a CSS class or an element id: every unit and script that draws or styles it
    marks = [(c, re.compile(rf"(\.|['\" ]){re.escape(c)}(['\" .:,{{>)\[]|$)")) for c in sym.classes]
    marks += [(i, re.compile(rf"\b{re.escape(i)}\b")) for i in sym.ids]
    for _, pat in marks:
        for f in rg_files(pat.pattern, "src/mtg_gateway"):
            um = file_module(f)
            if um:
                for u in units_naming(f, pat):
                    if not (u.isupper() and u.endswith("CSS")):
                        seeds[um].add(u)
            elif f.endswith(".js"):
                scripts.add(Path(f).name)
    # the modules that load a changed script
    for scr in list(scripts):
        pat = re.compile(re.escape(scr))
        for f in rg_files(scr, "src/mtg_gateway", fixed=True):
            um = file_module(f)
            if um:
                seeds[um] |= units_naming(f, pat)
                why[um].add(f"loads {scr}")
    found = propagate(seeds, set(sym.whole_modules))
    modules = set(found) | {m for m in (file_module(f) for f in files) if m}

    rt = routes()
    templates: set[str] = set()
    for m in modules:
        hit = found.get(m, set())
        for handler, t in rt.get(m, ()):
            # a handler defined inside a function (a router factory) is reached through that function
            if (
                "*" in hit
                or handler in hit
                or any(
                    u.islower() and u.startswith(("make_", "build_", "router", "add_", "mount", "register"))
                    for u in hit
                )
                or m not in found
            ):
                templates.add(t)
    pages = pages_for(templates)
    for p in pages:
        why[p].add("its route's module or a module it uses changed")
    # what the browser saw: a page whose markup holds a changed class, or that loads a changed script
    if index:
        for page, seen in index.items():
            cls = set(seen.get("classes", ())) & sym.classes
            scr = {s for s in seen.get("scripts", ()) if Path(s.split("?")[0]).name in scripts}
            if cls or scr:
                pages.add(page)
                if cls:
                    why[page].add("draws " + ", ".join(sorted(cls)[:6]))
                if scr:
                    why[page].add("loads " + ", ".join(sorted(scr)[:4]))
        # a whole stylesheet module changed outside any rule we could read: every page
        if {"mtg_gateway.theme", "mtg_gateway.menus"} & sym.whole_modules:
            pages |= set(index)

    # the tests that use any of it
    tests: set[str] = set()
    for m in modules:
        short = m.rsplit(".", 1)[-1]
        imp = rf"(from mtg_gateway(\.\w+)* import .*\b{short}\b|mtg_gateway\.{re.escape(short)}\b)"
        tests |= rg_files(imp, "tests")
    for scr in scripts:
        tests |= rg_files(scr, "tests", fixed=True)
    for f in files:
        if f.startswith("tests/") and f.endswith(".py"):
            tests.add(f)
    tests = {t for t in tests if t.endswith(".py") and "/e2e/" not in t and Path(t).name.startswith("test_")}

    surf: dict[str, set[str]] = defaultdict(set)
    names = {n for ns in sym.python.values() for n in ns} | sym.classes
    for name, globs in SURFACES:
        for f in files:
            if any(_glob_hit(f, g) for g in globs):
                surf[name].add(f)
    for m in modules:
        f = module_file(m)
        for name, globs in SURFACES:
            if any(_glob_hit(f, g) for g in globs):
                surf[name].add(f)
    for t in templates:
        lit = re.sub(r"\{.*", "", t).rstrip("/")
        if len(lit) > 4:
            for f in rg_files(lit, "android", "plugin", "docs", fixed=True):
                surf[
                    "Android app"
                    if f.startswith("android")
                    else "Docs"
                    if f.startswith("docs")
                    else "Plugin and skill"
                ].add(f)
    for n in sorted(names):
        if len(n) > 5:
            for f in rg_files(n, "android", "plugin", fixed=True, word=True):
                surf["Android app" if f.startswith("android") else "Plugin and skill"].add(f)
    return Deps(files, sym, modules, scripts, pages, tests, dict(surf), dict(why))


def _glob_hit(path: str, g: str) -> bool:
    if g.endswith("/"):
        return path.startswith(g)
    return Path(path).match(g) if "*" in g else path == g


# ---- find -------------------------------------------------------------------------------------


def find(words: list[str]) -> str:
    lines = [f"# Places that hold: {', '.join(words)}", ""]
    pattern = "|".join(re.escape(w) for w in words)
    found = _rg(
        [
            "-n",
            "-i",
            "--no-heading",
            "--no-messages",
            "-e",
            pattern,
            "--",
            "src",
            "android",
            "plugin",
            "docs",
            "README.md",
            "CHANGELOG.md",
            "tests",
        ]
    )
    by: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in found.splitlines():
        path, _, rest = row.partition(":")
        surface = next((s for s, gs in SURFACES if any(_glob_hit(path, g) for g in gs)), "Other")
        by[surface][path].append(rest[:160])
    for name, _ in [*SURFACES, ("Other", ())]:
        if name not in by:
            lines.append(f"## {name}: nothing found")
            continue
        lines.append(f"## {name}: {len(by[name])} files")
        for path, rows in sorted(by[name].items()):
            lines.append(f"- [ ] {path} ({len(rows)})")
            for row in rows[:5]:
                lines.append(f"      {row}")
        lines.append("")
    return "\n".join(lines)


# ---- check ------------------------------------------------------------------------------------

ITEM_RE = re.compile(r"^- \[( |x|X)\] ([^:\s]+)(?::\s*(.*))?$")


@dataclass
class Map:
    change: dict[str, str]
    checked: dict[str, str]
    surfaces: dict[str, str]
    unticked: list[str]
    expected_pages: set[str]


def parse_map(text: str) -> Map:
    sec = ""
    m = Map({}, {}, {}, [], set())
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            sec = line[3:].strip().lower()
            continue
        if sec == "surfaces":
            mm = re.match(r"^- \[( |x|X)\] ([^:]+):\s*(.*)$", line)
            if mm:
                if mm.group(1) == " ":
                    m.unticked.append(f"surface {mm.group(2)}")
                m.surfaces[mm.group(2).strip()] = mm.group(3).strip()
            continue
        if sec.startswith("pages expected"):
            mm = re.match(r"^- (/\S*)", line)
            if mm:
                m.expected_pages.add(mm.group(1))
            continue
        mm = ITEM_RE.match(line)
        if not mm:
            continue
        if mm.group(1) == " ":
            m.unticked.append(mm.group(2))
        if sec == "change":
            m.change[mm.group(2)] = mm.group(3) or ""
        elif sec.startswith("checked"):
            m.checked[mm.group(2)] = mm.group(3) or ""
    return m


def check(map_text: str, files: list[str]) -> list[str]:
    m = parse_map(map_text)
    problems = [f"not ticked: {u}" for u in m.unticked]
    for s in MAP_SURFACES:
        if s not in m.surfaces:
            problems.append(f"surface not considered: {s} (add '- [x] {s}: ...' under ## Surfaces)")
        elif not m.surfaces[s]:
            problems.append(f"surface {s} has no words: say what changes there, or 'none' and why")
    for path, why in m.checked.items():
        if not why:
            problems.append(f"checked, no change, but no reason given: {path}")
    listed = set(m.change)
    for path in sorted(listed - set(files)):
        if not (ROOT / path).exists() and not any(f.startswith(path.rstrip("/") + "/") for f in files):
            problems.append(f"the map names a file that does not exist: {path}")
        elif not any(f == path or f.startswith(path.rstrip("/") + "/") for f in files):
            problems.append(f"missed: the map says to change {path}, the diff does not")
    for f in files:
        if not any(f == p or f.startswith(p.rstrip("/") + "/") for p in listed):
            problems.append(f"scope creep: the diff changes {f}, the map does not list it under ## Change")
    return problems


# words a member reads: a quoted phrase of two or more words, or one capitalised word of five or
# more letters (a label like "Alerts"), in a removed line of a page, a script, a card or a doc
PHRASE_RE = re.compile(r"""["'`>]([A-Z][A-Za-z']+(?: [A-Za-z'][A-Za-z']*){1,7}|[A-Z][a-z]{4,})["'`<]""")
WORD_SURFACES = ("src", "android", "plugin", "docs", "README.md")


def left_behind(base: str, hs: dict[str, Hunk], map_text: str) -> list[str]:
    """Words the change took out of one place that are still in another: a renamed button, label or
    message the diff changed in the page but not in the app, the cards, the tools or the docs. Each
    file still holding such words must be on the map (changed, or checked with its reason)."""
    m = parse_map(map_text)
    listed = set(m.change) | set(m.checked)
    gone: dict[str, set[str]] = defaultdict(set)  # phrase -> files it was taken out of
    for path, h in hs.items():
        if not path.startswith(("src/", "docs/", "plugin/", "android/")) or path.startswith(
            "src/mtg_gateway/static/vendor"
        ):
            continue
        new_text = _safe_read(path)
        old_text = _file_at(base, path)
        for line in h.removed:
            for ph in PHRASE_RE.findall(line):
                # taken out of this file, not only moved within it: it is there fewer times now
                if new_text.count(ph) < old_text.count(ph):
                    gone[ph].add(path)
    out = []
    for ph, sources in sorted(gone.items()):
        for f in sorted(set(_rg(["-l", "-F", "--no-messages", "-e", ph, "--", *WORD_SURFACES]).split())):
            if f in sources or any(f == p or f.startswith(p.rstrip("/") + "/") for p in listed):
                continue
            if f.startswith(("tests/", "CHANGELOG")):
                continue
            out.append(
                f'left behind: {f} still says "{ph}", which the change took out of {", ".join(sorted(sources))}'
            )
    return out


# ---- command line -----------------------------------------------------------------------------


def _deps_md(d: Deps) -> str:
    out = ["# What this change could impact (from the code's dependencies)", ""]
    out.append(f"Changed files: {len(d.files)}")
    for f in d.files:
        out.append(f"- {f}")
    out += ["", "## Changed symbols"]
    for mod, ns in sorted(d.symbols.python.items()):
        out.append(f"- {mod}: {', '.join(sorted(ns))}")
    for mod in sorted(d.symbols.whole_modules):
        out.append(f"- {mod}: changed outside any function (everything importing it counts)")
    for scr, fns in sorted(d.symbols.js.items()):
        out.append(f"- {scr}: {', '.join(sorted(fns)) or '(top level)'}")
    if d.symbols.classes:
        out.append(f"- CSS classes: {', '.join(sorted(d.symbols.classes))}")
    out += ["", f"## Pages that could be impacted ({len(d.pages)})"]
    for p in sorted(d.pages):
        out.append(f"- {p}: {'; '.join(sorted(d.why.get(p, {'route'})))}")
    out += ["", f"## Modules ({len(d.modules)})", ", ".join(sorted(m.rsplit(".", 1)[-1] for m in d.modules))]
    out += ["", f"## Scripts ({len(d.scripts)})", ", ".join(sorted(d.scripts))]
    out += ["", "## Surfaces"]
    for s, fs in d.surfaces.items():
        out.append(f"- {s}: {', '.join(sorted(fs)[:12])}{' …' if len(fs) > 12 else ''}")
    out += ["", f"## Tests to run ({len(d.tests)})", "pytest -q " + " ".join(sorted(d.tests))]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m tests.impact_map")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("words", nargs="+")
    d = sub.add_parser("deps")
    d.add_argument("--base")
    d.add_argument("--index", type=Path)
    d.add_argument("--json", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("map", type=Path)
    c.add_argument("--base")
    a = ap.parse_args(argv)
    if a.cmd == "find":
        print(find(a.words))
        return 0
    base = a.base or default_base()
    if a.cmd == "deps":
        index = json.loads(a.index.read_text()) if a.index else None
        dd = deps(base, index)
        if a.json:
            print(
                json.dumps(
                    {
                        "files": dd.files,
                        "pages": sorted(dd.pages),
                        "tests": sorted(dd.tests),
                        "modules": sorted(dd.modules),
                        "scripts": sorted(dd.scripts),
                        "classes": sorted(dd.symbols.classes),
                    },
                    indent=1,
                )
            )
        else:
            print(_deps_md(dd))
        return 0
    problems = check(a.map.read_text(), changed_files(base))
    problems += left_behind(base, hunks(base), a.map.read_text())
    if problems:
        print("The impact map check FAILED:")
        for p in problems:
            print(f"- {p}")
        return 1
    print("The impact map check passed: every listed place changed, nothing else did.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
