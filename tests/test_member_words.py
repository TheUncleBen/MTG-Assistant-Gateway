"""House style over every string the gateway says (0.7.46 sweep at eccfa3d9, B-2, B-3, B-15): the
words a member or an assistant reads, in the tools' answers and descriptions, the site and the
cards. Checked over the source itself, so a new string is held to it too: docstrings, comments,
log lines, SQL and HTML attributes are not words anyone reads."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import mtg_gateway

PKG = Path(mtg_gateway.__file__).parent
SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|WHERE|DELETE FROM|CREATE|ALTER|JOIN|ORDER BY|AND \w+ IN)\b")
LOGS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def _sources() -> Iterator[tuple[str, ast.AST, str]]:
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        yield str(path.relative_to(PKG)), ast.parse(text), text


def _logged(tree: ast.AST) -> set[int]:
    """ids of every node inside a logger call's arguments: an operator's log, not member words."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOGS
            and isinstance(node.func.value, ast.Name | ast.Attribute)
            and "log" in ast.unparse(node.func.value).casefold()
        ):
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                out.update(id(n) for n in ast.walk(arg))
    return out


def _words() -> Iterator[tuple[str, int, str]]:
    """(file, line, text) of every string constant that isn't a docstring, a log line or SQL."""
    for name, tree, _text in _sources():
        docs = {
            id(n.value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
        }
        logged = _logged(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docs
                and id(node) not in logged
                and not SQL.search(node.value)
            ):
                yield name, node.lineno, node.value


# -- B-15: "ID", not "id" ---------------------------------------------------------------------------

LOWER_ID = re.compile(r"[A-Za-z,] ids?\b(?!=)")
# "id" as the name of a field or a column, not the word
ID_AS_FIELD = {
    "collection.py": ("Each remove entry needs id or name", "Each tag entry needs id or name", "scryfall id"),
    "archidekt_csv.py": ("scryfall id",),
    "browse.py": ("Each result has an id and url",),
}


def test_prose_says_id_in_capitals() -> None:
    """B-15: a deck ID, a snapshot ID, a comment's ID: "ID" in capitals wherever it is a word."""
    found = []
    for name, line, text in _words():
        for m in LOWER_ID.finditer(text):
            around = text[max(0, m.start() - 40) : m.end() + 20]
            spans = [
                (at, at + len(ok))
                for ok in ID_AS_FIELD.get(name, ())
                for at in [i.start() for i in re.finditer(re.escape(ok), text)]
            ]
            if any(a <= m.start() < b for a, b in spans):
                continue
            found.append(f"{name}:{line}: …{around}…")
    assert not found, "\n".join(found)


# -- B-1: one sentence per outage state -------------------------------------------------------------

SERVICE = (
    r"(Archidekt|Scryfall|Forge|Mystic Forge|[Tt]he research service|Authentik|GitHub|"
    r"[Tt]he sign-in service|[Tt]he identity provider|[Tt]he notification server)"
)
# Any other way of saying a service is down, slow or pausing: busy.OUTAGE says each state once
OTHER_OUTAGE = re.compile(
    SERVICE + r"('s \w+)? ("
    r"is not answering|isn't answering|is not reachable|did not answer|could not be reached"
    r"|is unavailable|asked us to|asked the gateway to wait|is busy and"
    r"|didn't answer(?! in time\.| in time;|, so nothing was changed\.)"
    r"|can't be reached(?! right now)"
    r")"
    r"|asked the gateway to slow down(?!, so its requests are paused for now\.| for longer than)"
    r"|\btry later\b|\bpausing its\b"
)
# "can't be reached" as a condition, not as the news itself
OUTAGE_AS_CONDITION = {"settings_page.py": ("Used only when Scryfall can't be reached.",)}


def _static_texts() -> Iterator[tuple[str, int, str]]:
    """(file, line, text) of each line of the static scripts and cards, comments left out."""
    for path in sorted((PKG / "static").rglob("*")):
        if path.suffix not in (".js", ".html"):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.strip()
            if not code.startswith(("//", "/*", "*")):
                yield str(path.relative_to(PKG)), n, code


def test_every_outage_is_said_in_the_words_of_one_table() -> None:
    """B-1: a 429 read "slow down; try again later" in one tool and "paused for now" in the next,
    the home panel said "did not answer in time" for a 429, a 503 and a refused connection alike,
    and the site said "can't be reached", "is not answering" and "is busy" for one state. Every
    surface now takes its sentence from busy.OUTAGE; any other phrasing of a service being down
    or slow is refused here, in the Python strings and in the static scripts and cards alike."""
    found = []
    for name, line, text in [*_words(), *_static_texts()]:
        for ok in OUTAGE_AS_CONDITION.get(name, ()):
            text = text.replace(ok, "")
        if m := OTHER_OUTAGE.search(text):
            found.append(f"{name}:{line}: {m.group(0)!r} in {text[:120]!r}")
    assert not found, "\n".join(found)


def test_the_outage_table_has_one_sentence_a_state() -> None:
    from mtg_gateway.busy import AGAIN, OUTAGE, outage

    assert outage("timeout") == "Archidekt didn't answer in time."
    assert outage("slow_down", "Scryfall", again=True) == (
        f"Scryfall asked the gateway to slow down, so its requests are paused for now. {AGAIN}"
    )
    assert len(set(OUTAGE.values())) == len(OUTAGE)
    for state, words in OUTAGE.items():
        assert words.startswith("{s} ") and words.endswith("."), state


# -- B-2: names in curly quotes ---------------------------------------------------------------------

# Not member words: the operator's configuration, the OAuth protocol's answers to apps, SQL
OPERATOR_FILES = {"config.py", "cimd.py", "oidc.py", "db.py", "prices.py"}
# A card's text for the assistant's next turn quotes a value as data, in straight quotes, so the
# assistant can pass it on as it is (B.quote): the model reads it, the member never does
FOR_THE_MODEL = {"_bridge.js": ("return '\"' + s + '\"';",), "proposal-card.html": ("(data, quoted)",)}
# a programmer's mistake, never shown to a member
PROGRAMMER_ERRORS = {"ValueError", "TypeError", "KeyError", "RuntimeError", "AssertionError"}
STRAIGHT_NAME = re.compile(r"\{[^{}]*?!r\}|(?<![=(\[,\w])'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'")


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _for_a_programmer(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    while node in parents:
        node = parents[node]
        if (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id in PROGRAMMER_ERRORS
        ):
            return True
        if isinstance(node, ast.FunctionDef) and node.name == "__repr__":
            return True
    return False


def test_names_in_member_text_sit_in_curly_quotes() -> None:
    """B-2: about 35 Python strings quoted a name in straight quotes ("no card found for 'X'",
    "{code!r}") while the house style, and the JS boxes, use “X”; the old guard looked at four JS
    files only. Every f-string a member or an assistant reads, and every static script and card,
    is checked here."""
    found = []
    for name, tree, text in _sources():
        if name in OPERATOR_FILES:
            continue
        logged, parents = _logged(tree), _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr) or id(node) in logged:
                continue
            seg = ast.get_source_segment(text, node) or ""
            if SQL.search(seg) or _for_a_programmer(node, parents):
                continue
            if m := STRAIGHT_NAME.search(seg):
                found.append(f"{name}:{node.lineno}: {m.group(0)} in {seg[:100]}")
    js = re.compile(
        r"\"'\" \+|\+ \"'\"|'\"' \+|\+ '\"'|\"\\\"\" \+|\+ \"\\\"\"|\\\"\$\{|'\$\{[^}]+\}'|\"\$\{[^}]+\}\""
    )
    for path in sorted([*PKG.rglob("*.js"), *PKG.rglob("*.html")]):
        # the OCR engine's third-party files (fetched by scripts/fetch_ocr_assets.py, not in git)
        # carry no member text of ours
        if "vendor" in path.relative_to(PKG).parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # a CSS selector or a Scryfall search (!"name") is code, not words
            if re.search(r"querySelector|\[[\w-]+=|scryfall\.com|CSS\.escape", line):
                continue
            if any(ok in line for ok in FOR_THE_MODEL.get(path.name, ())):
                continue
            if m := js.search(line):
                found.append(f"{path.relative_to(PKG)}:{n}: {m.group(0)} in {line.strip()[:100]}")
    assert not found, "\n".join(found)


# -- B-3: refusals as sentences, one card-not-found, no pydantic words ------------------------------

ERRORS = {
    "DeckError",
    "CollectionError",
    "ScanError",
    "ArchidektError",
    "ScryfallError",
    "ForgeError",
    "SweepRefused",
    "LoginError",
}
# a refusal may open with the argument it is about, as the tool lists it: "deck_format must be ..."
AS_ARGUMENT = re.compile(
    r"^([a-z][\w.\[\]-]*)( must| is | are | takes| needs| has | have | cannot| can't| holds| was "
    r"| were | should| may | gives|:| \(| and | or |,)"
)
SENTENCE_END = re.compile(r"([.?!…]|[.?!…][\"”')\]]|[.?!][”\"]\))$")


def _argument_names() -> set[str]:
    """Every argument a tool takes, and every field of the models in schemas.py."""
    out = {"bulk-data"}  # Scryfall's endpoint, named as it is
    for name, tree, _text in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "tool"
                for d in node.decorator_list
            ):
                out.update(a.arg for a in [*node.args.args, *node.args.kwonlyargs])
            if name == "schemas.py" and isinstance(node, ast.ClassDef):
                out.update(
                    s.target.id
                    for s in node.body
                    if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
                )
    return out


def _messages() -> Iterator[tuple[str, int, ast.expr]]:
    """(file, line, message) of every refusal the gateway raises: the message of each of
    ERRORS, and of a file's own helper that wraps one (account_lists._err)."""
    for name, tree, _text in _sources():
        if name in OPERATOR_FILES:
            continue
        helpers: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.body and isinstance(node.body[-1], ast.Return):
                call = node.body[-1].value
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id in ERRORS
                    and len(call.args) >= 2
                    and isinstance(call.args[1], ast.Name)
                    and call.args[1].id in (params := [a.arg for a in node.args.args])
                ):
                    helpers[node.name] = params.index(call.args[1].id)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            called = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            msg: ast.expr | None = None
            if called in helpers and len(node.args) > helpers[called]:
                msg = node.args[helpers[called]]
            elif called in ERRORS:
                kw = [k.value for k in node.keywords if k.arg in ("message", "msg")]
                at = 0 if called == "SweepRefused" else 1
                msg = kw[0] if kw else node.args[at] if len(node.args) > at else None
            if msg is not None:
                yield name, node.lineno, msg


def _ends(node: ast.expr, right: bool) -> ast.expr:
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        node = node.right if right else node.left
    return node


def test_every_refusal_is_a_sentence() -> None:
    """B-3: 356 refusals started lowercase or had no full stop ("no cards found in the text",
    "give exactly one of cards, ..."). Each refusal a tool or a page gives now starts with a
    capital (or with the argument it is about, as the tool names it) and ends with a full stop."""
    arguments = _argument_names()
    found = []
    for name, line, msg in _messages():
        head, tail = _ends(msg, right=False), _ends(msg, right=True)
        first = (
            head.value
            if isinstance(head, ast.Constant)
            else head.values[0].value
            if isinstance(head, ast.JoinedStr) and head.values and isinstance(head.values[0], ast.Constant)
            else None
        )
        if isinstance(first, str) and first[:1].islower():
            m = AS_ARGUMENT.match(first)
            if not (m and (m.group(1) in arguments or "_" in m.group(1))):
                found.append(f"{name}:{line}: starts lowercase: {first[:60]!r}")
        last = (
            tail.value
            if isinstance(tail, ast.Constant)
            else tail.values[-1].value
            if isinstance(tail, ast.JoinedStr) and tail.values and isinstance(tail.values[-1], ast.Constant)
            else None
        )
        if isinstance(last, str) and not SENTENCE_END.search(last.rstrip()):
            found.append(f"{name}:{line}: no full stop: …{last[-50:]!r}")
    assert not found, f"{len(found)} refusals:\n" + "\n".join(found)


NOT_A_CARD = re.compile(
    r"[Nn]o card (named|found)|knows no card|[Nn]o such card|does not know this card|no card by that name"
)


def test_a_name_that_is_no_card_is_said_one_way() -> None:
    """B-3: "No card is called “X”." (decks.no_card_called), "Scryfall knows no card named “X”.",
    "no card found for 'X'" and "no card named 'X' in your binder" said one thing four ways. Every
    surface says "No card is called “X”." (a binder without it: "Your binder has no card called
    “X”.")."""
    found = [
        f"{name}:{line}: {text[:100]!r}"
        for name, line, text in [*_words(), *_static_texts()]
        if NOT_A_CARD.search(text)
    ]
    assert not found, "\n".join(found)


def test_no_answer_carries_pydantics_own_words() -> None:
    """B-3: a refused argument answered "the arguments do not fit the tool: changes.0.action: Input
    should be 'add', 'remove' ..." (pydantic's text, straight quotes). The gateway maps each kind of
    refusal to plain words (app._plain_reason); its own strings never say pydantic's."""
    from mtg_gateway.app import _schema_problems

    framework = (
        "Error executing tool propose_deck_changes: 3 validation errors for propose_deck_changesArguments\n"
        "changes.0.action\n"
        "  Input should be 'add', 'remove' or 'set_quantity' [type=literal_error, input_value='x', "
        "input_type=str]\n"
        "    For further information visit the link pydantic gives\n"
        "games\n"
        "  Input should be greater than or equal to 10 [type=greater_than_equal, input_value=3, "
        "input_type=int]\n"
        "deck_id\n"
        "  Field required [type=missing, input_value={}, input_type=dict]\n"
    )
    assert _schema_problems(framework) == (
        "changes[0].action must be add, remove or set_quantity; games must be at least 10; "
        "deck_id is required"
    )
    # a union argument (a list of changes or one object): pydantic tags each branch and refuses
    # the whole field on the branch that did not fit; the answer names only the real problem
    union = (
        "Error executing tool amend_proposal: 2 validation errors for amend_proposalArguments\n"
        "changes.list[DeckChange].0.action\n"
        "  Input should be 'add' or 'remove' [type=literal_error, input_value='x', input_type=str]\n"
        "changes.dict[str,any]\n"
        "  Input should be a valid dictionary [type=dict_type, input_value=[{}], input_type=list]\n"
    )
    assert _schema_problems(union) == "changes[0].action must be add or remove"
    pydantic = re.compile(r"Input should be|Field required|Extra inputs are not permitted|validation error")
    found = [f"{name}:{line}: {text[:80]!r}" for name, line, text in _words() if pydantic.search(text)]
    # app.py reads the framework's text (its head pattern and the fallback), never says it
    assert all(f.startswith("app.py:") for f in found), "\n".join(found)
