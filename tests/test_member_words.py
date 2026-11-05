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
