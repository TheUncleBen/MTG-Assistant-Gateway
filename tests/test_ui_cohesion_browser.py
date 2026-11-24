"""The cohesion census (tests/ui_cohesion.py) in CI: every page outside the deck page draws each
shared role in the standard's looks (docs/UI-STANDARD.md), so a page cannot grow a look of its own
again. The deck page and its sub-pages keep their stated exceptions (the banner title over the
deck's art, the category headings of the list) and are held by their own tests.

Each limit is how many looks the role may have across all those pages on one screen. Where a limit
is above one, the extra looks are named: page nuance the standard allows, not drift."""

from __future__ import annotations

import json

import pytest

from . import ui_cohesion as C
from . import ui_matrix as M
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

LIMITS = {
    "title": 1,
    # the section heading; the same with no margin in a row with its controls (ui.list_head);
    # the group label over a grouped list (h2.ui-group)
    "h2": 3,
    "h3": 1,
    # the button; the scanner's camera tab and round shutter; a row's action in a narrow row
    "button:plain": 4,
    "button:primary": 1,
    "button:danger-outline": 1,
    "capsule": 1,
    "chip:chosen": 1,
    "label": 1,
    # every pick list, text and number box; the scanner's Set box (label, field and clear in one)
    "field": 2,
    "textarea": 1,
}


@pytest.mark.parametrize("screen", ["app", "laptop"])
def test_every_page_draws_each_role_in_the_standards_looks(server: Server, screen: str) -> None:  # noqa: F811
    if _chromium_path() == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
    paths = [p for p in M.pages(pending) if "#" not in p and not p.startswith(("/decks/42", "/decks/50"))]
    displays = [d for d in M.DISPLAYS if d.key == screen]
    rows = C.census(server, sid, paths, displays)
    summary = C.summarize(rows)
    over = []
    for role, limit in LIMITS.items():
        looks = summary.get(role, {}).get(screen, {})
        if len(looks) > limit:
            detail = "; ".join(
                f"{k} on {sorted(v['pages'])[:4]} e.g. {sorted(v['els'])[:2]}"
                for k, v in sorted(looks.items(), key=lambda kv: kv[1]["count"])
            )
            over.append(f"{role}: {len(looks)} looks (at most {limit}): {detail}")
    assert over == [], "\n".join(over) + "\n" + json.dumps(LIMITS)
