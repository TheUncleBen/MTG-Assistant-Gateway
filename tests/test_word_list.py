"""One name per thing (decision C6, 0.7.46): the words a member reads on the pages, in the scripts'
menus and toasts, on the cards in the chat, in the Android app, in the tool texts an assistant
relays, in the skill and in the docs use the one approved word list:

- **Binder** for the member's own cards ("Add to binder", "Remove from binder");
- **Alerts** for Archidekt's bell, **Push notifications** for what the app sends to a phone;
- **Assistant** for the hub, with its tabs Proposals, History and Reports;
- **Apply** for sending a change to Archidekt ("Apply now" only where the assistant could too;
  "Only you can apply" where it can't);
- **Test** for a test run, its parts **Solo test** and **Forge games**;
- **Printings…** for the printing picker (no "Change printing" or "Art variants");
- **Card models** for the descriptions kept for the solo test;
- **On your watchlist** for a watched card.

This test fails on any retired name. Code (identifiers, addresses, JSON keys, tool names) is not
words a member reads, so it is left out: comments and docstrings, log lines, SQL, regexes, and
`code` in Markdown. What the patterns still meet on purpose is in ALLOWED, each with its reason."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from mtg_gateway import app as app_mod

from .conftest import Stack, make_settings, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_history_page import store_report

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "mtg_gateway"

# (what it names, the retired words, what to say instead). A word that is code (part of an
# identifier, an address or a key: next to a letter, digit, "_", "/", ".", "#" or "-") is not met.
_C = r"(?<![\w/.#-])"
_E = r"(?![\w/#=-]|\.\w)"
RETIRED: list[tuple[str, re.Pattern[str], str]] = [
    (
        "binder",
        re.compile(
            rf"{_C}(?:Add to|Remove from|Save to|Open the) collection{_E}"
            rf"|{_C}(?:[Yy]our|[Tt]he|[Mm]y|the user's|their|[Ii]ts) (?:Archidekt )?[Cc]ollection{_E}"
            rf"|{_C}Collection on Archidekt|{_C}Archidekt Collection{_E}|I own this card"
            rf"|{_C}collection (?:changes?|edits?|add){_E}|{_C}decks and collections{_E}"
            r"|\*\*Collection\*\*|#+ Collection\b|\bCollection (?:>|›|changed|card details|import\b)"
        ),
        "Binder: “Add to binder”, “Remove from binder”, “your binder”",
    ),
    (
        "alerts",
        re.compile(
            rf"{_C}Archidekt(?:'s)? notifications{_E}|Email and notifications|{_C}[Mm]y notifications{_E}"
            rf"|(?<!Push )(?<!push ){_C}Notifications{_E}"
        ),
        "Alerts (Archidekt's bell) or Push notifications (the app's)",
    ),
    (
        "push notifications",
        re.compile(rf"(?<!push )(?<!Push )(?<!Android's ){_C}notifications{_E}"),
        "Push notifications",
    ),
    ("printings", re.compile(r"Change printing|Art variants"), "Printings…"),
    (
        "test",
        re.compile(
            rf"(?i:{_C}goldfish(?:ing|ed)?{_E}|{_C}simulat\w*{_E}|{_C}full test{_E}|{_C}full-rules solo test)"
        ),
        "Test, Solo test or Forge games",
    ),
    ("watchlist", re.compile(rf"{_C}Watching{_E}"), "On your watchlist"),
    (
        "card models",
        re.compile(r"(?i:saved (?:card )?descriptions?|simulator card models)|My card models"),
        "Card models",
    ),
    (
        "apply",
        re.compile(rf"Always need your press|{_C}Approve{_E}(?! and sign in| only if)|press Approve"),
        "Apply (“Only you can apply” where the assistant can't)",
    ),
]

# What the patterns meet on purpose: (file name, a regex the text found matches, why it stays).
# The Goldfish page (/decks/<id>/goldfish) is where a member draws and plays a deck by hand; it is
# no test run, and the word list gives it no other name, so its own name and its links keep it.
GOLDFISH_PAGE = "the hand-play page's own name: no test run, and the word list renames it to nothing"
ALLOWED: list[tuple[str, str, str]] = [
    ("deckpage.py", r"<h1>Goldfish</h1>|by hand, here on the gateway'>\{\} Goldfish</a>", GOLDFISH_PAGE),
    ("deckpage.py", r"The goldfish needs JavaScript", GOLDFISH_PAGE),
    ("companion_tools.py", r"^Goldfish: \{\}$", GOLDFISH_PAGE),
    ("deck.js", r"^Goldfish$", GOLDFISH_PAGE),
    ("menus.js", r"^Goldfish$", GOLDFISH_PAGE),
    ("guide.py", r"^Goldfish$|Playtest, Goldfish, Play on EDHPlay|<strong>Goldfish</strong>", GOLDFISH_PAGE),
    (
        "SKILL.md",
        r"what players call goldfishing|asks to \"goldfish\" a deck|its \"Goldfish\" page",
        "maps the user's own word to the Solo test, and names the hand-play page so no one takes it for "
        "a test",
    ),
    (
        "*.md",
        r"\*\*Goldfish\*\*|deck page's Goldfish|Goldfish(?:: play| \(0\.7\.23: play) the deck by hand",
        GOLDFISH_PAGE,
    ),
    ("/decks/42", r"\bGoldfish\b(?! (?:simulation|test|games?)\b)", GOLDFISH_PAGE),
    ("/guide", r"\bGoldfish\b(?! (?:simulation|test|games?)\b)", GOLDFISH_PAGE),
]

# Explanations that name the old word to say what the new one is: taken out before the check.
EXPLAINED = re.compile(
    r"\((?:your|their|the user's|the member's) Archidekt collection\)"
    r"|binder, (?:your|their|the user's) Archidekt collection"
)


# Code inside a text: CSS comments, and the attributes of markup that are names or addresses
_CODE = re.compile(
    r"/\*.*?\*/"
    r"|\b(?:id|class|href|name|for|value|action|src|data-[\w-]+|aria-(?:labelledby|describedby|controls))"
    r"=(?:'[^']*'|\"[^\"]*\")",
    re.S,
)


def _retired(text: str) -> list[tuple[str, str]]:
    text = EXPLAINED.sub("", " ".join(_CODE.sub(" ", text).split()))
    out = []
    for name, pattern, _use in RETIRED:
        for m in pattern.finditer(text):
            out.append((name, text[max(0, m.start() - 60) : m.end() + 60]))
    return out


def _allowed(key: str, around: str) -> bool:
    return any(
        (key == name or (name.startswith("*") and key.endswith(name[1:]))) and re.search(words, around)
        for name, words, _why in ALLOWED
    )


def _report(path: Path | str, text: str) -> list[str]:
    """The retired words in ``text`` from ``path`` (a file, or a page's address), less ALLOWED."""
    key, where = (path.name, path.relative_to(ROOT)) if isinstance(path, Path) else (path, path)
    return [
        f"{where} [{name}]: ...{around}..." for name, around in _retired(text) if not _allowed(key, around)
    ]


# -- Python: the string literals that are words (not docstrings, logs, SQL or regexes) -------------
_LOG = {"debug", "info", "warning", "error", "exception", "critical"}
_SQL = re.compile(
    r"^\s*(?:SELECT|UPDATE|INSERT|CREATE|DELETE|WITH|ALTER|DROP|PRAGMA)\b|\bWHERE\b|\bFROM \w+ WHERE\b"
)


def _not_words(node: ast.AST) -> set[int]:
    """The ids of string nodes that are code: docstrings, log lines, regexes and SQL."""
    skip: set[int] = set()
    for n in ast.walk(node):
        body = getattr(n, "body", None)
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            base = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
            if (name in _LOG and base in {"logger", "log", "logging", "_log", "LOG"}) or (
                base == "re"
                and name in {"compile", "sub", "search", "match", "fullmatch", "findall", "finditer", "split"}
            ):
                for a in ast.walk(n):
                    if isinstance(a, (ast.Constant, ast.JoinedStr)):
                        skip.add(id(a))
    return skip


def _python_words(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _not_words(tree)
    texts = []
    for n in ast.walk(tree):
        if id(n) in skip:
            continue
        if isinstance(n, ast.JoinedStr):
            if any(id(v) in skip for v in n.values):
                continue
            text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in n.values)
            for v in n.values:
                skip.add(id(v))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            text = n.value
        else:
            continue
        if _SQL.search(text) or re.fullmatch(r"[a-z0-9_./:#{}-]*", text):
            continue  # SQL, or a key, name or address
        texts.append(text)
    return texts


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_python_words_use_the_word_list(path: Path) -> None:
    found = [f for text in _python_words(path) for f in _report(path, text)]
    assert not found, "\n".join(found)


# -- scripts and in-chat cards: their strings and their HTML, without comments --------------------
_JS_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\\\"'\w])//[^\n]*", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_JS_STRING = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`", re.S)


def _script_words(path: Path) -> list[str]:
    text = _HTML_COMMENT.sub("", path.read_text(encoding="utf-8"))
    text = _JS_COMMENT.sub("", text)
    if path.suffix == ".js":
        return [m.group(0)[1:-1] for m in _JS_STRING.finditer(text)]
    # an in-chat card: its markup's words, and its script's strings
    words = []
    for m in re.finditer(r"<script\b[^>]*>(.*?)</script>", text, re.S):
        words += [s.group(0)[1:-1] for s in _JS_STRING.finditer(m.group(1))]
    words.append(re.sub(r"<script\b.*?</script>|<style\b.*?</style>", "", text, flags=re.S))
    return words


def _script_files() -> list[Path]:
    return sorted([*SRC.rglob("*.js"), *SRC.rglob("*.html")])


@pytest.mark.parametrize("path", _script_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_script_and_card_words_use_the_word_list(path: Path) -> None:
    found = [f for text in _script_words(path) for f in _report(path, text)]
    assert not found, "\n".join(found)


# -- the Android app's strings ----------------------------------------------------------------------
def test_android_strings_use_the_word_list() -> None:
    path = ROOT / "android" / "app" / "src" / "main" / "res" / "values" / "strings.xml"
    texts = re.findall(r"<string\b[^>]*>(.*?)</string>", path.read_text(encoding="utf-8"), re.S)
    assert texts
    found = [f for text in texts for f in _report(path, text)]
    assert not found, "\n".join(found)


# -- the skill, the ChatGPT block and the docs: their prose, without code ---------------------------
_MD_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)


def _markdown_files() -> list[Path]:
    docs = [p for p in sorted((ROOT / "docs").glob("*.md")) if p.name != "THIRD-PARTY-NOTICES.md"]
    return [ROOT / "README.md", *sorted((ROOT / "plugin").rglob("*.md")), *docs]


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_skill_and_docs_use_the_word_list(path: Path) -> None:
    text = _MD_CODE.sub("", path.read_text(encoding="utf-8"))
    text = re.sub(r"\]\([^)]*\)", "]", text)  # a link's address
    found = _report(path, text)
    assert not found, "\n".join(found)


# -- the tool texts an assistant reads and relays -----------------------------------------------------
stack_options = stack_with(COMPANION)


def _visible(html: str) -> str:
    """A page's words: its text and the attributes read aloud, without scripts, styles and <pre>
    blocks (a report's raw output from the research service, and a summary's plain text, which
    the Python check reads at its source)."""
    html = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<pre\b.*?</pre>", " ", html, flags=re.S)
    attrs = " ".join(re.findall(r"(?:aria-label|title|placeholder|alt|data-busy-label)='([^']*)'", html))
    return re.sub(r"<[^>]+>", " ", html) + " " + attrs


def _descriptions(schema: object) -> list[str]:
    if isinstance(schema, dict):
        own = [schema["description"]] if isinstance(schema.get("description"), str) else []
        return own + [d for k, v in schema.items() if k != "description" for d in _descriptions(v)]
    if isinstance(schema, list):
        return [d for v in schema for d in _descriptions(v)]
    return []


def test_tool_texts_use_the_word_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each tool's title, description and parameter texts as an assistant reads them, and the
    server's instructions: what an assistant says to the member comes from these."""
    servers = []
    build = app_mod.build_mcp_server
    monkeypatch.setattr(
        app_mod, "build_mcp_server", lambda state: servers.append(build(state)) or servers[-1]
    )
    app_mod.create_app(make_settings(tmp_path, watchlists_enabled=True))
    tools = servers[-1]._tool_manager.list_tools()
    assert len(tools) > 30
    path = SRC / "app.py"
    found = _report(path, app_mod.server_instructions("https://gateway.test"))
    for t in tools:
        for text in [t.title or "", t.description or "", *_descriptions(t.parameters)]:
            found += [f"{t.name}: {f}" for f in _report(f"tool {t.name}", text)]
    assert not found, "\n".join(found)


async def test_rendered_pages_use_the_word_list(stack: Stack) -> None:
    """The pages a member opens, as the server renders them for a linked member with a report."""
    gw = stack.gw
    b = await linked_browser(stack)
    try:
        store_report(gw.db, gw.db._one("SELECT sub FROM users", ())["sub"], "rep_w")
        found = []
        for path in (
            "/",
            "/decks",
            "/decks/42",
            "/collection",
            "/watchlist",
            "/lists",
            "/notifications",
            "/proposals",
            "/history",
            "/reports",
            "/history/reports/rep_w",
            "/account",
            "/account/details",
            "/settings",
            "/guide",
            "/cards",
            "/scan",
        ):
            r = await b.http.get(path, headers=NAV)
            if r.status_code != 200:
                continue
            found += _report(path, _visible(r.text))
        assert not found, "\n".join(found)
    finally:
        await b.aclose()
