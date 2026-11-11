"""0.7.47 menu and layout alignment (the owner, 2026-11-10: "menus being misaligned"): the UI matrix's
menu pass and loose-button check (tests/ui_matrix.py) on the pages where they found each class, on
a phone in the browser and a laptop, at 100% and 200% text. Each failed before its fix:

* a ⋮ menu near the window's foot was pushed up over its own button (cardmenu.js openMenu), and so
  was a menu a script opens from its button (Test and play, Deck tools: Menu.open's anchor);
* the phone's More sheet floated in the middle of the window, beside the tab it came from;
* a menu's link items kept an 18 px icon while its button items' icons grew with the text, so
  their text started in two columns at large text, and a phone rule for the page head's buttons
  stripped the icons and padding of the Binder's More menu;
* Lands only, wrapped to a line of its own, started .5rem in from the line's edge;
* Assistants' foot action was a 16rem button centred under rows that span the column."""

from __future__ import annotations

import pytest

from . import ui_matrix as M
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

KINDS = {
    "menu-clip",
    "menu-covers",
    "menu-detached",
    "menu-misaligned",
    "menu-items",
    "menu-none",
    "loose-button",
}


@pytest.mark.parametrize("screen", ["phone", "laptop"])
def test_menus_open_at_their_buttons_and_lone_buttons_fit_their_column(
    server: Server,  # noqa: F811
    screen: str,
) -> None:
    if _chromium_path() == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _pending = _seed(server)
    server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
    displays = [d for d in M.DISPLAYS if d.key == screen]
    paths = ["/", "/decks", "/decks/42", "/collection?view=list", "/account/assistants"]
    res = M.sweep(server, sid, paths, displays=displays, themes=("light",), texts=(1, 2), kinds=KINDS)
    lines = [f.line() for f in res.findings]
    assert res.errors == [], "\n".join(res.errors[:20])
    assert lines == [], "\n".join(lines[:40])
