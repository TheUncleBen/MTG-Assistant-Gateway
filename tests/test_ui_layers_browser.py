"""The layers census (tests/ui_layers.py) in CI: every box (dialog) and the first menu of every
page are opened and measured, and each kind has one look per screen. A box or a menu that a page
drew its own way fails here, as a page's own button fails the cohesion census.

Sizes are read without transforms (a box eases in from 98%), so a box measured while it opens
reads as it will settle. ``KNOWN`` names boxes that still differ, by id: the list may only shrink."""

from __future__ import annotations

import json

import pytest

from . import ui_layers as L
from . import ui_matrix as M
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

# boxes that still draw their own look, by id: each one is a finding to fix (none known today)
KNOWN: dict[str, set[str]] = {}


@pytest.mark.parametrize("screen", ["app", "laptop"])
def test_every_box_and_menu_has_the_one_look(server: Server, screen: str) -> None:  # noqa: F811
    if _chromium_path() == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
    paths = [p for p in M.pages(pending) if "#" not in p]
    rows = L.layers(server, sid, paths, [d for d in M.DISPLAYS if d.key == screen])
    known = KNOWN.get(screen, set())
    rows = [r for r in rows if r["el"] not in known]
    assert any(r["role"] == "box" for r in rows) and any(r["role"] == "menu" for r in rows), "nothing opened"
    over = []
    for role, by in L.summarize(rows).items():
        groups: dict[str, dict] = {}
        for key, v in by.get(screen, {}).items():
            kind = json.loads(key).get("kind", "")
            groups.setdefault(kind, {})[key] = v
        for kind, looks in groups.items():
            if len(looks) > 1:
                detail = "; ".join(
                    f"{k} on {sorted(v['pages'])[:3]} ids {sorted(v['els'])[:4]}"
                    for k, v in sorted(looks.items(), key=lambda kv: kv[1]["count"])
                )
                over.append(f"{role}{':' + kind if kind else ''}: {len(looks)} looks: {detail}")
    assert over == [], "\n".join(over)
