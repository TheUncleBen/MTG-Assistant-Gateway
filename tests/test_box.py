"""The UI foundation of the 2026-10-21 overhaul (package A), without a browser: the shared box
(ui.box, ui.box_opener, theme.BOX_CSS, static/box.js) and its no-script fallback, the notice strip
(F8), the app chip with the app's full name and one neutral icon (no initials), the card size
control that stays in list views (R-175) and the helpers the other packages build on."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mtg_gateway import menus, theme, ui

STATIC = Path(__file__).parents[1] / "src" / "mtg_gateway" / "static"


def _render(body: str, **kw) -> str:
    return theme.render("T", body, site="S", **kw).body.decode()


# -- the box -----------------------------------------------------------------------------------------


def test_a_box_the_page_holds_is_a_hidden_dialog_with_title_x_body_and_actions() -> None:
    out = ui.box(
        "export",
        "Export “Meren”",
        "<form id='f' method='post' action='/decks/42/export'></form>",
        actions=[ui.box_button("Cancel", cancel=True), ui.box_button("Copy", kind="primary", form="f")],
    )
    assert out.startswith(
        "<div class='ui-box mtgbox' id='export' role='dialog' aria-modal='true' "
        "aria-labelledby='export-title' hidden>"
    )
    assert "<div class='bh'><h2 id='export-title'>Export “Meren”</h2>" in out
    assert "<a class='bx' href='#' aria-label='Close' title='Close'>" in out and theme.icon("x") in out
    assert "<div class='body'><form id='f' method='post' action='/decks/42/export'></form></div>" in out
    assert out.endswith(
        "<div class='acts'><button type='button' class='btn-plain' data-cancel>Cancel</button>"
        "<button type='submit' class='primary' form='f'>Copy</button></div></div>"
    )
    assert "data-open" not in out
    wide = ui.box("x", "T", "", wide=True, label="Export the deck")
    assert "class='ui-box mtgbox wide'" in wide and "aria-label='Export the deck'" in wide
    assert "&lt;b&gt;" in ui.box("x", "<b>", "")


def test_an_old_address_draws_the_box_open_with_the_x_pointing_at_the_page() -> None:
    out = ui.box("export", "Export", "<p>x</p>", open=True, close_href="/decks/42")
    assert " hidden" not in out and " data-open>" in out
    assert "<a class='bx' href='/decks/42' aria-label='Close'" in out


def test_the_opener_is_a_real_link_to_the_old_address() -> None:
    a = ui.box_opener("Export…", "/decks/42/export", "export")
    assert a == "<a class='btn btn-plain' href='/decks/42/export' data-box-open='export'>Export…</a>"
    assert ui.box_opener("Delete deck…", "/decks/42/delete", "del", kind="danger").startswith(
        "<a class='btn btn-outline-danger'"
    )
    assert ui.box_open_attr("a'b") == "data-box-open='a&#x27;b'"
    with pytest.raises(KeyError):
        ui.box_opener("x", "/x", "x", kind="purple")


def test_box_buttons_cover_cancel_submit_and_the_solid_red_final_step() -> None:
    assert (
        ui.box_button("Keep it", cancel=True)
        == "<button type='button' class='btn-plain' data-cancel>Keep it</button>"
    )
    assert ui.box_button(
        "Delete this deck", kind="danger-solid", name="action", value="delete", disabled=True
    ) == (
        "<button type='submit' class='danger' name='action' value='delete' disabled>Delete this deck</button>"
    )
    assert ui.box_button("Add", kind="primary", icon_name="plus").startswith(
        "<button type='submit' class='primary'><svg"
    )
    assert "data-gate-off" in ui.box_button("Go", attrs="data-gate-off")


def test_the_box_look_is_rounder_and_softer_than_the_panels_in_both_themes() -> None:
    css = theme.CSS
    assert theme.BOX_CSS in css and ".mtgbox" not in menus.MENUS_CSS  # one place for the box's look
    assert "--radius-box:14px" in css and "--shadow-box:" in css
    # the light theme gives the shadow its own softer value
    assert (
        "--shadow-box:0 18px 48px rgba(0,0,0,.5)" in css and "--shadow-box:0 18px 48px rgba(0,0,0,.22)" in css
    )
    assert re.search(
        r"\.mtgbox\{[^}]*border-radius:var\(--radius-box\)[^}]*box-shadow:var\(--shadow-box\)", css
    )
    # the panels keep their 3 px corners: the box is rounder than them
    assert "--radius-panel:3px" in css
    # a calm open and close, none under reduced motion (the page's own rule and the box's)
    assert ".mtgbox-scrim{" in css and "transition:opacity .18s ease-out" in css
    assert ".mtgbox-scrim.in{opacity:1}" in css
    assert ".mtgbox-scrim > .mtgbox{transform:translateY(10px) scale(.98);\n  transition:transform" in css
    assert (
        "@media (prefers-reduced-motion:reduce){\n  .mtgbox-scrim,.mtgbox-scrim > .mtgbox{transition:none}"
        in css
    )
    # the header: the title and a 44 px round ×, on every box
    assert ".mtgbox .bh{display:flex;align-items:center" in css
    assert re.search(
        r"\.mtgbox \.bx,\.mtgbox a\.bx\{[^}]*width:44px;\s*height:44px;min-height:44px;min-width:44px", css
    )
    assert ".mtgbox[data-locked=true] .bx{display:none}" in css
    # long content scrolls inside; the page behind does not scroll
    assert ".mtgbox .body{flex:1 1 auto;min-height:0;overflow:auto" in css
    assert "html.mtgbox-open,html.mtgbox-open body{overflow:hidden}" in css
    # a bottom sheet on phones with the safe area kept clear
    phone = css[css.index("@media (max-width:599.98px){\n  .mtgbox-scrim{align-items:flex-end") :]
    assert "border-radius:var(--radius-box) var(--radius-box) 0 0" in phone
    assert "padding-bottom:calc(1.25rem + env(safe-area-inset-bottom))" in phone
    assert ".mtgbox-scrim > .mtgbox{transform:translateY(100%)}" in phone
    # drawn open without scripts (the old address): a plain panel, its Cancel buttons hidden
    assert ".ui-box:not(.in-scrim){margin:0 0 1rem;border:1px solid var(--card-border)" in css
    assert ".ui-box:not(.in-scrim) [data-cancel]{display:none}" in css
    assert ".ui-box[hidden]{display:none}" in css


def test_box_js_is_on_every_page_before_the_menus_and_the_page_scripts() -> None:
    for signed_in in (False, True):
        page = _render("<p>x</p>", signed_in=signed_in, csrf="c" if signed_in else None)
        assert "<script src='/static/box.js" in page
        if signed_in:
            assert (
                page.index("/static/box.js")
                < page.index("/static/cardmenu.js")
                < page.index("/static/menus.js")
            )
    js = (STATIC / "box.js").read_text(encoding="utf-8")
    for must in (
        "window.MtgBox = {",
        "openElement: openElement",
        "confirm: confirm",
        "prefers-reduced-motion: reduce",
        'closest("[data-box-open]")',
        '".ui-box[data-open]"',
        "history.replaceState",
        'e.key === "Escape"',
        'e.key !== "Tab"',
        'setAttribute("aria-label", "Close")',
    ):
        assert must in js, must
    # the box layer left cardmenu.js: one copy of it, in box.js
    cardmenu = (STATIC / "cardmenu.js").read_text(encoding="utf-8")
    assert "function openBox" not in cardmenu and "window.MtgBox = {" not in cardmenu
    assert "window.MtgBox" in (STATIC / "menus.js").read_text(encoding="utf-8")
    # a new tab or a modifier press leaves the link to the old address alone
    assert "e.ctrlKey || e.metaKey || e.shiftKey || e.altKey" in js


def test_the_collaborator_box_quotes_names_and_ends_red_when_removing() -> None:
    js = (STATIC / "menus.js").read_text(encoding="utf-8")
    assert '"Let “" + who + "” edit “" + deck + "” on Archidekt?"' in js
    assert '"Stop “" + who + "” editing “" + deck + "” on Archidekt?"' in js
    assert '"“" + who + "” can no longer change this deck. You can add them again later."' in js
    assert 'adding ? "Add “" + who + "”" : "Remove “" + who + "”", adding ? "primary" : "danger"' in js


# -- the notice strip (F8) -------------------------------------------------------------------------------


def test_a_notice_is_a_tinted_strip_with_a_coloured_left_edge_and_no_border() -> None:
    css = theme.CSS
    base = re.search(r"\n\.notice\{([^}]*)\}", css).group(1)
    assert "border:0;border-left:4px solid var(--blue);background:var(--blue-tint)" in base
    assert "border-radius:0 var(--radius) var(--radius) 0" in base
    assert "border:1px solid var(--border)" not in base
    for kind, colour in (("error", "red"), ("ok", "green"), ("warn", "orange")):
        rule = re.search(rf"\n\.notice\.{kind}\{{([^}}]*)\}}", css).group(1)
        assert (
            f"border-left-color:var(--{colour});background:var(--{colour}-tint);color:var(--{colour}-text)"
            == rule
        )
    strong = re.search(r"\n\.notice\.strong\{([^}]*)\}", css).group(1)
    assert "border-left:6px solid var(--orange);background:var(--orange-tint)" in strong
    assert "border-width:1px 1px 1px 6px" not in strong
    assert ui.notice("Deck renamed on Archidekt.", "ok", role="status") == (
        "<div class='notice ok' role='status'>Deck renamed on Archidekt.</div>"
    )
    assert ui.notice("Plain") == "<div class='notice'>Plain</div>"
    assert (
        ui.notice("<b>x</b>", "warn", html=True, cls="wide") == "<div class='notice warn wide'><b>x</b></div>"
    )
    assert "&lt;b&gt;" in ui.notice("<b>", "error")
    with pytest.raises(ValueError):
        ui.notice("x", "purple")


# -- the app chip (Ben, 21 Oct: full name, no initials; 22 Oct: the known apps' own marks) ------------


def test_the_app_chip_shows_the_full_name_beside_the_connected_app_icon() -> None:
    plug = theme.icon("app")
    claude = "<img class='logo' src='/static/logos/claude.svg' alt='' width='16' height='16'>"
    by_claude = "https://claude.ai/oauth/mcp-oauth-client-metadata"  # verified by its address (N-3)
    assert (
        ui.app_chip("Claude", client_id=by_claude)
        == f"<span class='ui-app'>{claude}<span class='nm'>Claude</span></span>"
    )
    assert ui.app_chip("ChatGPT", client_id="https://chatgpt.com/oauth/c.json").startswith(
        "<span class='ui-app'><img class='logo on-light' src='/static/logos/"
    )
    assert ui.app_chip("Codex").endswith("<span class='nm'>Codex</span></span>")
    # any other app keeps the gateway's own neutral icon; never initials
    assert ui.app_chip("browser") == f"<span class='ui-app'>{plug}<span class='nm'>browser</span></span>"
    assert "<i>" not in ui.app_chip("Claude") and "<i " not in ui.app_chip("Claude")  # no initials tile
    assert "&lt;script&gt;" in ui.app_chip("<script>")
    # an app that gave no name is "Unnamed app"; a row with no app recorded stays the dash
    assert (
        ui.app_chip("", unnamed=True)
        == f"<span class='ui-app unnamed'>{plug}<span class='nm'>Unnamed app</span></span>"
    )
    assert ui.app_name(None) == "Unnamed app" and ui.app_name(" Claude ") == "Claude"
    for none in (None, "", "  "):
        out = ui.app_chip(none)
        assert "class='ui-app none' title='No app'" in out and "—" in out
    # the neutral icon is the gateway's own stroke, not a company's mark
    assert "app" in theme.ICONS and "<path" in theme.ICONS["app"] and "fill='#" not in theme.ICONS["app"]
    assert ".ui-app svg.i{width:16px;height:16px" in theme.UI_CSS and ".ui-app i{" not in theme.UI_CSS
    assert ".ui-app img.logo{width:16px;height:16px" in theme.UI_CSS


# -- the card size control (R-175) ----------------------------------------------------------------------


def test_the_card_size_control_stays_in_list_views_and_sizes_their_thumbnails() -> None:
    css = theme.CSS
    assert ":has(input[name=view][value=list]:checked) .cardsize{display:none}" not in css
    assert (
        "html[data-card-size=s]{--thumb-scale:.75}" in css
        and "html[data-card-size=l]{--thumb-scale:1.35}" in css
    )
    for sel in (
        "ul.decklist.list .thumb",
        ".cardlist .pic",
        "ul.colllist .thumb",
        ".watch-page .w-t .thumb",
        ".erow .thumb",
    ):
        assert f"html[data-card-size=s] {sel},html[data-card-size=l] {sel}{{" in css, sel
    assert ui.thumb_size_css(".scan ul.items .thumb", "2.6rem", "3.6rem") == (
        "html[data-card-size] .scan ul.items .thumb{width:calc(2.6rem * var(--thumb-scale));"
        "height:calc(3.6rem * var(--thumb-scale))}"
    )
    assert ui.thumb_size_css(".x", "3rem") == "html[data-card-size] .x{width:calc(3rem * var(--thumb-scale))}"


def test_the_card_size_control_helper_is_shared_and_takes_the_toolbars_class() -> None:
    assert ui.card_size_control is theme.card_size_control
    theme.set_card_size("l")
    out = ui.card_size_control(cls="g size cardsize")
    assert out.startswith(
        "<div class='g size cardsize'><span class='lbl' aria-hidden='true'>Card size</span>"
    )
    assert "<span class='sizeseg' role='group' aria-label='Card size'>" in out
    assert (
        "data-card-size-set='l' aria-pressed='true'" in out
        and "data-card-size-set='m' aria-pressed='false'" in out
    )
    assert theme.card_size_control().startswith("<div class='field cardsize'>")
    theme.set_card_size("m")


# -- the helpers the B packages build on ----------------------------------------------------------------


def test_page_head_takes_the_pages_actions_and_load_more_is_a_real_link() -> None:
    head = ui.page_head("My decks", "Your decks on Archidekt.", actions_html="<button>New deck</button>")
    assert head == (
        "<div class='ui-head phead'><h1>My decks</h1><span class='acts'><button>New deck</button></span>"
        "</div><p class='ui-sub'>Your decks on Archidekt.</p>"
    )
    assert ui.page_head("Admin") == "<div class='ui-head'><h1>Admin</h1></div>"
    more = ui.load_more("/collection?page=2", shown=25, total=112)
    assert more == (
        "<div class='loadmore'><a class='btn btn-plain' href='/collection?page=2' rel='next' data-load-more>"
        "Load more</a><p class='muted'>25 of 112</p></div>"
    )
    assert (
        ui.load_more("/x", done="That is every card.")
        == "<div class='loadmore'><p class='muted'>That is every card.</p></div>"
    )
    assert ui.button_link("Open", "/d", kind="primary", icon_name="eye").startswith(
        "<a class='btn btn-primary' href='/d'><svg"
    )


def test_no_script_relies_on_has_in_a_selector() -> None:
    """N-5 (review 2026-10-22): a ``:has()`` selector in querySelector throws on browsers without
    it, so a menu item built on one would do nothing there. The scripts find such elements by hand."""
    static = Path(__file__).resolve().parents[1] / "src" / "mtg_gateway" / "static"
    for js in sorted(static.glob("*.js")):
        assert ":has(" not in js.read_text(encoding="utf-8"), js.name
