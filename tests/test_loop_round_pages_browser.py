"""The 0.7.48 loop round's page fixes in headless Chromium (docs/UI-STANDARD.md), on the overflow
sweep's data: the Alerts actions are one row on a phone and only the ones that can act, the
Binder's picked view has no hole under its title, Home's title keeps its size on a phone, the Scan
page keeps the camera on a phone's first screen with chips that hug their words, a folder's trail
is one row over its heading, a Watchlist row's main action is its 44 px chevron, a form's main
action takes a phone's row, a report's title is one line, and the admin Settings chips share the
form's column. Skipped without a Chromium build."""

# ruff: noqa: E501 - the scripts the browser runs are kept one statement a line
from __future__ import annotations

import pytest

from .test_cardsearch_browser import _page
from .test_overflow_sweep_browser import LONGUSER, _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

SHOWN = """(sel) => [...document.querySelectorAll(sel)].filter(e => e.getClientRects().length).map(e => {
  const r = e.getBoundingClientRect();
  return {text: e.innerText.trim(), left: Math.round(r.left), right: Math.round(r.right),
          top: Math.round(r.top), bottom: Math.round(r.bottom), width: Math.round(r.width),
          height: Math.round(r.height)}; })"""


def _start(server: Server) -> tuple[str, str, str]:  # noqa: F811
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
    return sid, pending, exe


@pytest.mark.parametrize("width", [320, 360, 412])
def test_alerts_actions_are_one_row_on_a_phone(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        # nothing kept: only Settings is drawn, beside the title, under an unread badge
        page.goto(f"{server.base}/notifications", wait_until="networkidle")
        acts = page.evaluate(SHOWN, ".al-acts button, .al-acts a")
        (h1,) = page.evaluate(SHOWN, "main h1")
        assert [a["text"] for a in acts] == ["Settings"], acts
        assert acts[0]["top"] < h1["bottom"] and acts[0]["width"] < width / 2, (acts, h1)
        # with alerts to mark and clear: three whole buttons on one row, in a phone's short words
        for i in range(2):
            server.db.add_gateway_alert("user-1", "applied", f"Applied · Deck {i}", "Done.", "/history")
        page.goto(f"{server.base}/notifications", wait_until="networkidle")
        acts = page.evaluate(SHOWN, ".al-acts button, .al-acts a")
        assert [a["text"] for a in acts] == ["Read all", "Clear all", "Settings"], acts
        assert len({a["top"] for a in acts}) == 1 and all(a["height"] == 44 for a in acts), acts
        assert acts[0]["left"] >= 0 and acts[-1]["right"] <= width, acts
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        fits = page.evaluate(
            "[...document.querySelectorAll('.al-acts button, .al-acts a')].every(b => b.scrollWidth <= b.clientWidth)"
        )
        assert fits  # no word is cut inside its button
        # marking them read takes Mark all read away (nothing left for it); Clear all stays
        page.locator("[data-al-all=read_all]").click()
        page.wait_for_function("() => document.querySelector('[data-al-all=read_all]').disabled")
        acts = page.evaluate(SHOWN, ".al-acts button, .al-acts a")
        assert [a["text"] for a in acts] == ["Clear all", "Settings"], acts
        assert errors == []
        browser.close()


def test_alerts_actions_keep_their_words_on_a_computer(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    server.db.add_gateway_alert("user-1", "applied", "Applied · Deck", "Done.", "/history")
    with sync_playwright() as p:
        browser, page, _errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/notifications", wait_until="networkidle")
        acts = page.evaluate(SHOWN, ".al-acts button, .al-acts a")
        assert [a["text"] for a in acts] == ["Mark all read", "Clear all", "Settings"], acts
        assert len({a["top"] for a in acts}) == 1
        assert page.locator("[data-al-all=read_all]").get_attribute("aria-label") == "Mark all read"
        browser.close()


def test_binder_picked_view_has_no_hole_under_its_title(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, _errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/binder?show=all", wait_until="networkidle")
        (h1,) = page.evaluate(SHOWN, ".bn-top h1")
        (switch,) = page.evaluate(SHOWN, ".bn-main .hubseg")
        # the switch follows the title (0.7.48 left 110 px between them)
        assert 0 <= switch["top"] - h1["bottom"] <= 32, (h1, switch)
        browser.close()


def test_home_title_keeps_its_size_and_the_headings_sit_over_their_panels(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, _errors = _page(p, server, sid, 412, "light", exe)
        page.goto(f"{server.base}/", wait_until="networkidle")
        size = page.evaluate(
            "parseFloat(getComputedStyle(document.querySelector('.home .hero h1')).fontSize)"
        )
        (h1,) = page.evaluate(SHOWN, ".home .hero h1")
        (sub,) = page.evaluate(SHOWN, ".home .hero .ui-sub")
        (new,) = page.evaluate(SHOWN, ".home .hero > a.btn")
        # the title at the standard's 2rem over the whole row; New deck under the purpose line
        assert size == 28 and h1["width"] >= 412 - 32, (size, h1)
        assert new["top"] >= sub["bottom"], (new, sub)
        (head,) = page.evaluate(SHOWN, ".home-decks > .list-head")
        (panel,) = page.evaluate(SHOWN, ".home-decks > .panel")
        assert head["text"].startswith("My decks") and head["bottom"] <= panel["top"], (head, panel)
        assert page.locator(".home-decks > .panel h2").count() == 0
        browser.close()
        # a computer: the two sections start level, their headings on one line
        browser, page, _errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/", wait_until="networkidle")
        (panel,) = page.evaluate(SHOWN, ".home-decks > .panel")
        first = page.evaluate(SHOWN, ".home .needs li")[0]
        assert abs(panel["top"] - first["top"]) <= 4, (panel, first)
        browser.close()


def test_scan_page_keeps_the_camera_on_a_phones_first_screen(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    server.db.delete_pref("user-1", "scan.intro")
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 412, "light", exe)
        page.goto(f"{server.base}/scan")
        page.wait_for_function("() => window.__scan && window.__scan.showTab")
        # the first visit: the steps in short as a notice with its ×, the capsule under it folded
        note = page.locator("#scan-first")
        assert note.count() == 1 and "check the list" in note.inner_text()
        intro = page.evaluate(
            """() => { const d = document.querySelector('#intro'), cs = getComputedStyle(d);
              return {open: d.open, edge: cs.borderTopWidth, pad: cs.paddingLeft}; }"""
        )
        assert intro == {"open": False, "edge": "0px", "pad": "0px"}, intro  # a capsule, not a panel
        top = page.locator("#camera").bounding_box()["y"]
        assert top < 500, top
        chips = page.evaluate(SHOWN, ".scan-opts .switch")
        assert [c["text"] for c in chips] == ["Foil", "Auto"] and all(c["width"] < 90 for c in chips), chips
        note.locator(".close").click()
        assert page.locator("#scan-first").count() == 0
        # the capsule opens the steps in place; Got it takes them away
        page.locator("#intro > summary").click()
        assert page.locator("#intro .steps li").count() == 3
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [412, 673])
def test_a_folders_way_back_is_one_row_over_its_heading(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, _errors = _page(p, server, sid, width, "light", exe)
        page.goto(f"{server.base}/decks?folder=501", wait_until="networkidle")
        (back,) = page.evaluate(SHOWN, "#decks-list a.ui-back")
        (head,) = page.evaluate(SHOWN, "#fv-here")
        assert back["text"] == "Decks" and back["bottom"] <= head["top"], (back, head)
        assert head["text"].startswith("Commander decks in progress") and head["text"].endswith("1 deck")
        assert "›" not in page.locator("#decks-list").inner_text()
        size = page.evaluate("getComputedStyle(document.querySelector('#fv-here')).fontSize")
        assert size == "16.8px"  # the section heading's 1.2rem
        browser.close()


def test_a_watchlist_rows_main_action_is_its_chevron_on_a_phone(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 412, "light", exe)
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        more = page.locator(".w-t .tr .more").first
        box = more.bounding_box()
        assert box["width"] == 44 and box["height"] >= 44, box
        assert more.inner_text().strip() == "›" and "chart and alert" in more.get_attribute("aria-label")
        (name,) = page.evaluate(SHOWN, ".w-t .tr:not(.wgrp) .title .t")[:1]
        assert name["width"] >= 150, name  # the name has the row (it had 125 px beside the button)
        more.click()
        assert more.get_attribute("aria-expanded") == "true"
        assert more.locator(".chev").count() == 1  # the script kept the chevron
        assert errors == []
        browser.close()
        browser, page, _errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        more = page.locator(".w-t .tr .more").first
        assert more.inner_text().strip() == "Details"
        more.click()
        assert more.inner_text().strip() == "Close"
        browser.close()


def test_a_forms_main_action_takes_a_phones_row(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, _errors = _page(p, server, sid, 412, "light", exe)
        page.goto(f"{server.base}/binder/settings", wait_until="networkidle")
        (save,) = page.evaluate(SHOWN, ".bn-save .primary")
        (form,) = page.evaluate(SHOWN, ".bn-save")
        assert save["width"] == form["width"] >= 300, (save, form)
        page.goto(f"{server.base}/install?for=chatgpt", wait_until="networkidle")
        for btn, row in zip(
            page.evaluate(SHOWN, ".inst .actions .btn-primary"),
            page.evaluate(SHOWN, ".inst .actions:has(.btn-primary)"),
            strict=True,
        ):
            assert btn["width"] == row["width"], (btn, row)
        browser.close()


def test_a_reports_title_is_one_line_with_its_deck_under_it(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, _errors = _page(p, server, sid, 412, "light", exe)
        page.goto(f"{server.base}/history/reports/r1", wait_until="networkidle")
        (h1,) = page.evaluate(SHOWN, ".rv-title h1")
        (deck,) = page.evaluate(SHOWN, ".rv-title .rv-deck")
        assert h1["text"] == "Deck test report" and h1["height"] <= 40, h1
        assert deck["text"].startswith("Aesi, Tyrant of Gyre Strait") and deck["top"] >= h1["bottom"]
        whole = page.evaluate(
            "(() => { const e = document.querySelector('.rv-deck'); return e.scrollHeight <= e.clientHeight + 1; })()"
        )
        assert whole  # the deck's name is not cut
        backs = page.evaluate(SHOWN, "main a.ui-back")
        assert [b["text"] for b in backs] == ["All reports"]
        acts = [a["text"] for a in page.evaluate(SHOWN, ".rv-acts > a.btn")]
        assert acts[:2] == ["Open the deck", "Deck history"], acts
        browser.close()


def test_a_profiles_follow_button_is_one_word(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/users/{LONGUSER}", wait_until="networkidle")
        btn = page.locator("[data-social=follow]")
        assert btn.inner_text().strip() in ("Follow", "Following")
        assert btn.get_attribute("aria-label").endswith(LONGUSER)
        assert btn.bounding_box()["width"] < 160
        assert errors == []
        browser.close()


def test_admin_settings_chips_share_the_forms_column(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid, _pending, exe = _start(server)
    with sync_playwright() as p:
        browser, page, _errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/admin/settings", wait_until="networkidle")
        (nav,) = page.evaluate(SHOWN, ".set-nav")
        group = page.evaluate(SHOWN, ".set-group")[0]
        assert (nav["left"], nav["right"]) == (group["left"], group["right"]), (nav, group)
        assert nav["width"] == 616  # the standard's 44rem
        browser.close()
