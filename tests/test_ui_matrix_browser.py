"""The UI matrix's checks (tests/ui_matrix.py) in CI: every page and its opened states (the card
viewer, the card menu, the filter sheets), on the phone app, an open Fold at 600, 884 and 984 px
and a laptop, in the light
and the dark theme, with the phone also at 200% text, and none may show a finding: content leaning
to one side or lopsided, a hole beside a shorter panel, a control stranded on a line of its own, a
blue link, text under 4.5:1, a browser-drawn control, a raw program value or a "1 cards", a touch
target under 44 px, a word broken in two, or a box a phone could take for a payment card field.

``python -m tests.ui_matrix OUT_DIR`` runs the whole matrix (twelve screens) with screenshots before
a push; this is its CI share, split by screen so pytest-xdist runs the parts side by side. Skipped
without a Chromium build."""

from __future__ import annotations

import pytest

from . import ui_matrix as M
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

SCREENS = {
    "app": ([d for d in M.DISPLAYS if d.key == "app"], (1, 2)),
    "fold-600": ([d for d in M.DISPLAYS if d.key == "fold-600"], (1, 2)),
    "fold-884": ([d for d in M.DISPLAYS if d.key == "fold-884"], (1, 2)),
    "fold-open": ([d for d in M.DISPLAYS if d.key == "fold-open"], (1,)),
    "laptop": ([d for d in M.DISPLAYS if d.key == "laptop"], (1,)),
}


@pytest.mark.parametrize("screen", list(SCREENS))
def test_every_page_meets_the_ui_standard(server: Server, screen: str) -> None:  # noqa: F811
    if _chromium_path() == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
    displays, texts = SCREENS[screen]
    res = M.sweep(server, sid, M.pages(pending), displays=displays, texts=texts)
    lines = [f.line() for f in res.findings]
    more = f"\n… {len(lines)} in all" if len(lines) > 60 else ""
    assert res.errors == [], "\n".join(res.errors[:20])
    assert lines == [], "\n".join(lines[:60]) + more


# -- the checks find what they are for (each on a page made to fail it) -----------------------------

FIXTURE = """
<!doctype html><html><head><meta name=viewport content='width=device-width'>
<style>body{margin:0;background:#fff;color:#111;font:16px sans-serif} main{display:block}
.grid{display:grid;grid-template-columns:420px 420px;gap:16px}
.p{background:#eee;border-radius:8px}
.row{display:flex;flex-wrap:wrap;gap:8px;width:300px}
.row button{width:90px;height:44px}
a.blue{color:#1d55b3} .faint{color:#bbb}
</style></head><body><main>
<div class=grid><div class=p style='height:120px'>A</div><div class=p style='height:400px'>B</div></div>
<div class=row><button>One</button><button>Two</button><button>Three</button><button>More</button></div>
<p><a class=blue href='#'>a blue link</a> <span class=faint>faint words</span> Total: $12.5 1 cards</p>
<input name=cardnumber placeholder='Card name'>
<p><button style='all:revert'>Plain</button>
<a href='#' class=btn style='display:inline-block;width:24px;height:24px'>x</a></p>
<div style='width:70px;overflow-wrap:anywhere;font-size:20px'>Cultivating</div>
<div style='width:1400px;height:2px'></div>
<div style='width:860px'><div><button style='width:400px'>Sign out</button></div></div>
</main></body></html>
"""


def test_the_checks_find_each_kind_of_problem() -> None:
    """Every kind of check the matrix makes fires on a page made to fail it (audit AO5: three of
    them were not proved), so a check that stops working fails here rather than passing quietly."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(FIXTURE)
        kinds = {f["kind"] for f in page.evaluate(M.CHECKS, {"touch": True, "text": 1})}
        browser.close()
    for kind in (
        "off-centre",
        "hole",
        "stranded",
        "link-blue",
        "contrast",
        "raw-value",
        "plural",
        "payment-autofill",
        "unstyled",
        "target",
        "mid-word",
        "overflow",
        "loose-button",
    ):
        assert kind in kinds, (kind, kinds)


# each with a filter set, so the sheet's foot holds Clear filters beside Show results and wraps to
# two rows at 200% text: without one the foot is one row and the old fixed room was enough (audit AO3)
@pytest.mark.parametrize("path", ["/search?name=a", "/cards?q=s", "/decks?q=a"])
def test_a_filter_sheets_foot_never_covers_its_last_control(server: Server, path: str) -> None:  # noqa: F811
    """At 200% text on a 320 px phone the sheet's Clear filters and Show results wrap onto two rows
    (176 px), and the sheet's fixed 154 px of room left the card size S/M/L partly under them
    (0.7.46 audit AM1): the room follows the foot's real height, so scrolled to its end the sheet's
    last control sits above the foot."""
    import re

    from playwright.sync_api import sync_playwright

    from .test_editor_browser import PNG
    from .waits import redrawn

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 320, "height": 640}, has_touch=True, is_mobile=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(re.compile(r"https://cards\.scryfall\.io/.*"), lambda r: r.fulfill(status=200, body=PNG))
        page = ctx.new_page()
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        page.evaluate(M.TEXT_200)
        page.locator(".sp-open").first.click()
        page.locator(".sp-filters.open").wait_for(state="visible")
        redrawn(page)
        page.wait_for_function(
            "() => { const s = document.querySelector('.sp-filters.open');"
            " const f = s.querySelector('.sp-sheet-foot');"
            " return parseFloat(getComputedStyle(s).paddingBottom) >= f.getBoundingClientRect().height; }",
            timeout=5_000,
        )
        gap = page.evaluate(
            "() => { const s = document.querySelector('.sp-filters.open'); s.scrollTop = s.scrollHeight;"
            " const f = s.querySelector('.sp-sheet-foot').getBoundingClientRect();"
            " const c = [...s.querySelectorAll('button, input, select, summary, .sizeseg')]"
            "   .filter(e => !e.closest('.sp-sheet-foot, .sp-sheet-head')"
            "     && e.checkVisibility({visibilityProperty: true})"
            "     && !(() => { for (let a = e.parentElement; a && a !== s; a = a.parentElement) {"
            "       if (/(hidden|clip|auto|scroll)/.test(getComputedStyle(a).overflowY)) {"
            "         const r = a.getBoundingClientRect(), b = e.getBoundingClientRect();"
            "         if (b.top >= r.bottom || b.bottom <= r.top) return true; } } return false; })())"
            "   .map(e => e.getBoundingClientRect().bottom);"
            " return f.top - Math.max(...c); }"
        )
        browser.close()
    assert gap >= 0, f"the sheet's last control is {-gap:.0f} px under its foot"


PLACEHOLDERS = """
<!doctype html><html><head><meta name=viewport content='width=device-width'>
<style>body{margin:0;background:#fff;color:#111;font:16px sans-serif} .c{display:inline-block}</style>
</head><body><main>
<p>Newest backup: off (MTG_BACKUP_COPY_DIR not set)</p>
<ul><li>Sol Ring <span>unknown</span></li><li>Arcane Signet <span>unknown</span></li></ul>
<p><span class=c hidden>Est unknown</span></p>
</main></body></html>
"""


def test_the_raw_value_check_finds_placeholders_and_setting_names() -> None:
    """0.7.47 polish found what the first checks let through: a setting's name on Admin, "unknown"
    in every Binder row, and the deck banner's hidden "Est unknown" drawn by its own display rule."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(PLACEHOLDERS)
        found = page.evaluate(M.CHECKS, {"touch": False, "text": 1})
        msgs = [f["msg"] for f in found if f["kind"] == "raw-value"]
        browser.close()
    assert any("MTG_BACKUP_COPY_DIR" in m for m in msgs), msgs
    assert any('"unknown" shown 2 times' in m for m in msgs), msgs
    assert any("marked hidden but drawn" in m for m in msgs), msgs


MENU_FIXTURE = """
<!doctype html><html><head><meta name=viewport content='width=device-width'>
<style>body{margin:0;font:16px sans-serif} main{display:block;padding:40px 200px}
details{position:relative;margin:0 0 60px} summary{list-style:none;width:80px;height:44px}
.menu{position:absolute;background:#eee;width:220px;display:flex;flex-direction:column}
.menu a{display:flex;gap:8px;min-height:40px;padding:0 16px;align-items:center;color:#111}
#wide{display:block;width:700px} #wide .menu{right:0;top:48px}
#far .menu{left:0;top:110px}
#over .menu{left:0;top:0}
#off{width:80px} #off .menu{right:150px;top:48px}
#ragged .menu{left:0;top:48px} #ragged a.ind{padding-left:40px}
</style></head><body><main>
<details class='dd wide' id=wide><summary>Wide</summary><div class=menu><a>One</a><a>Two</a></div></details>
<details class='dd far' id=far><summary>Far</summary><div class=menu><a>One</a><a>Two</a></div></details>
<details class='dd over' id=over><summary>Over</summary><div class=menu><a>One</a><a>Two</a></div></details>
<details class='dd off' id=off><summary>Off</summary><div class=menu><a>One</a><a>Two</a></div></details>
<details class='dd ragged' id=ragged><summary>Ragged</summary>
<div class=menu><a>One</a><a class=ind>Two</a></div></details>
<button aria-haspopup=menu style='width:80px;height:44px'>Dead</button>
</main></body></html>
"""


def test_the_menu_pass_finds_each_kind_of_misplaced_menu() -> None:
    """Each menu check fires on a page made to fail it: a panel hanging from a details wider than
    its button (misaligned), one 66 px under it (detached), one over it (covers), one past the
    window's left edge (clip), items whose text starts in two columns, and a menu button that opens
    nothing."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    res = M.Sweep()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(MENU_FIXTURE)
        laptop = next(d for d in M.DISPLAYS if d.key == "laptop")
        M._menu_pass(page, "fixture", laptop, "light", 1, res, None, None)
        browser.close()
    got = {(f.kind, f.el.split()[0]) for f in res.findings}
    for want in (
        ("menu-misaligned", "summary"),
        ("menu-detached", "summary"),
        ("menu-covers", "summary"),
        ("menu-clip", "summary"),
        ("menu-items", "summary"),
        ("menu-none", "button"),
    ):
        assert want in got, (want, sorted(got))
    assert res.errors == []


LEFT_HUG = """
<!doctype html><html><head><meta name=viewport content='width=device-width'>
<style>body{margin:0;font:16px sans-serif;background:#fff;color:#111}
nav.rail{position:fixed;left:0;top:0;bottom:0;width:80px;background:#333}
main{margin-left:80px;padding:0 14px} h1{margin:16px 0}
.col{max-width:560px;margin:0} .card{background:#eee;border-radius:8px;padding:12px;margin:0 0 12px}
</style></head><body><nav class=rail></nav><main><h1>Assistants</h1>
<div class=col><div class=card>Default for new assistants</div><div class=card>Claude</div>
<p>How much each assistant may change on Archidekt without asking you first.</p></div>
</main></body></html>
"""


@pytest.mark.parametrize("text", [1, 2])
@pytest.mark.parametrize("width", [800, 984])
def test_off_centre_sees_a_column_at_the_left_under_a_full_width_heading(width: int, text: int) -> None:
    """0.7.46's Assistants (audit AU1): a 40rem column (560 px at the site's 14 px text) held at the
    left beside the rail, under an h1
    as wide as the page. The h1's block box spans the room, so measured by boxes the page read as
    centred; measured by its words, the column's lean shows. At 200% text too (audit AW1)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.set_content(LEFT_HUG)
        kinds = {f["kind"] for f in page.evaluate(M.CHECKS, {"touch": True, "text": text})}
        browser.close()
    assert "off-centre" in kinds, kinds


CONTROL_ROW = """
<!doctype html><html><head><meta name=viewport content='width=device-width'>
<style>body{margin:0;font:28px sans-serif;background:#fff;color:#111}
main{max-width:900px;margin:0 auto;padding:0 16px}
.row{display:flex;gap:12px} .row > *{min-width:240px;min-height:44px;background:#eee;border:1px solid #999}
</style></head><body><main><h1>Cover image</h1>
<div class=row><button>Automatic (Archidekt picks)</button><button class=btn>Set cover</button></div>
</main></body></html>
"""


@pytest.mark.parametrize("text", [1, 2])
def test_off_centre_leaves_a_row_of_controls_at_the_left(text: int) -> None:
    """A picker and its button start at the left like any form row. Their faces are not a lopsided
    row of panels (deck settings' cover row at 200% text, audit AW1)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(CONTROL_ROW)
        found = page.evaluate(M.CHECKS, {"touch": False, "text": text})
        found = [f for f in found if f["kind"] == "off-centre"]
        browser.close()
    assert not found, found
