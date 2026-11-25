# ruff: noqa: E501 - the failure lines read best whole
"""The click sweep (tests/ui_clicks.py) as a test: every control on the given pages is pressed, and
none may do nothing (dead), throw a script error, or lead to a failed request.

It presses each control from a freshly loaded page and a restored database, which is what makes
it trustworthy and also slow (a few seconds a press, about two and a half hours for every page on
one screen). So it runs on the pages a change could impact, and on every page before a release:

* ``MTG_CHECK_PAGES=/binder,/decks/42`` runs it on those pages (tests/change_check.py passes the
  pages tests/impact_map.py found from the change's dependencies);
* ``MTG_FULL_SWEEP=1`` runs it on every page (the release run);
* with neither, it is skipped and says why: the suite's other browser tests still run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from . import ui_clicks as C
from . import ui_matrix as M
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

BAD = ("dead", "js-error", "failed-request", "unclickable", "unreachable", "unloadable")


@pytest.mark.parametrize("screen", ["app", "laptop"])
def test_no_control_is_dead_or_broken(server: Server, screen: str, tmp_path: Path) -> None:  # noqa: F811
    chosen = [p for p in os.environ.get("MTG_CHECK_PAGES", "").split(",") if p]
    if not chosen and os.environ.get("MTG_FULL_SWEEP") != "1":
        pytest.skip(
            "the click sweep runs on a change's pages (MTG_CHECK_PAGES) or before a release (MTG_FULL_SWEEP=1)"
        )
    if _chromium_path() == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
    every = [p for p in M.pages(pending) if "#" not in p]
    paths = every if not chosen else [p.replace("{pending}", pending) for p in chosen]
    rows = C.sweep(
        server, sid, paths, [d for d in M.DISPLAYS if d.key == screen], tmp_path, log=lambda *_: None
    )
    bad = [r for r in rows if r.get("verdict") in BAD]
    assert bad == [], "\n".join(
        f"{r['verdict']}: {r['page']} {r.get('via') or ''} {r.get('kind', '')} {r.get('text', '')!r} {r.get('detail', '')}"
        for r in bad
    )
