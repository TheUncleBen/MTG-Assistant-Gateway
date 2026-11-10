"""0.7.47 audit AU2: an open Fold between 600 and 900 px wide (beside the app's rail, split screen,
a small tablet). The UI matrix (tests/ui_matrix.py) at 600 and 884 px, on the pages where each class
was found, at 100% and 200% text. Each failed before its fix:

* the deck page's More, and a report's Delete…, alone on a line under three buttons (a row of
  buttons that would leave one alone is an even grid: static/feedback.js, .even-rows);
* the deck editor's card rows kept their one-line layout in a 462 px list, the name 61 px wide and
  broken inside its words (the rows stack below 38rem now);
* the editor's "Also keep a backup copy" and a deck settings button ran out of the window at 200%
  text (they wrap their words when they don't fit);
* in the grid view at 200% text a tile without a picture broke "Terramorphic" in two (the columns
  grow with the text)."""

from __future__ import annotations

import pytest

from . import ui_matrix as M
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

PAGES = ["/decks/42", "/decks/42?view=grid", "/decks/42/edit", "/decks/42/settings", "/history/reports/r1"]


@pytest.mark.parametrize("screen", ["fold-600", "fold-884"])
def test_an_open_fold_between_600_and_900_px(server: Server, screen: str) -> None:  # noqa: F811
    if _chromium_path() == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _pending = _seed(server)
    server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
    displays = [d for d in M.DISPLAYS if d.key == screen]
    res = M.sweep(
        server,
        sid,
        PAGES,
        displays=displays,
        themes=("light",),
        texts=(1, 2),
        kinds={"stranded", "mid-word", "overflow", "loose-button"},
        menus=False,
    )
    lines = [f.line() for f in res.findings]
    assert res.errors == [], "\n".join(res.errors[:20])
    assert lines == [], "\n".join(lines[:40])
