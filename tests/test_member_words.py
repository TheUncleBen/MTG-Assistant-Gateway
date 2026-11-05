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
    "collection.py": ("each remove entry needs id or name", "each tag entry needs id or name", "scryfall id"),
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
