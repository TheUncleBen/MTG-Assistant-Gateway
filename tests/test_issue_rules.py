"""The reported-problem rules (tests/issue_rules.py) keep their checks: every check a rule names
exists, so a rule cannot lose its check when a test is renamed or deleted, and every rule is either
held by a check or says what only a person can judge."""

from __future__ import annotations

import ast
from pathlib import Path

from .issue_rules import RULES

TESTS = Path(__file__).parent
TOOLS = {"tool:impact_map check": "impact_map.py", "tool:shot_diff run": "shot_diff.py"}


def _defined(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


def test_every_rule_has_a_unique_id_and_a_check() -> None:
    ids = [r.id for r in RULES]
    assert len(ids) == len(set(ids)), ids
    for r in RULES:
        assert r.checks, f"{r.id} has no check: add one, or the problem can come back unseen"
        assert r.problem.strip(), r.id


def test_every_check_a_rule_names_exists() -> None:
    missing = []
    for r in RULES:
        for c in r.checks:
            if c.startswith("tool:"):
                if c not in TOOLS or not (TESTS / TOOLS[c]).exists():
                    missing.append(f"{r.id}: {c}")
                continue
            file, _, name = c.partition("::")
            path = TESTS.parent / file
            if not path.exists() or name not in _defined(path):
                missing.append(f"{r.id}: {c}")
    assert missing == [], missing
