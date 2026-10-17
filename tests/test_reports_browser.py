"""The report page and the history page in headless Chromium: no sideways scroll at phone and
laptop widths, the tiles and charts are styled (the CSS the report page once lacked), the day
headings stick, and the Copy button has its script. Screenshots land in .scratch/ when it
exists (never committed). Skipped without a Chromium build."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from .test_history_page import seed_history, store_report
from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")

SCRATCH = Path(__file__).resolve().parents[1] / ".scratch"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _shot(page, name: str) -> None:
    if SCRATCH.is_dir():
        page.screenshot(path=str(SCRATCH / f"{name}.png"), full_page=True)


@pytest.mark.parametrize("width", [360, 1366])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_report_and_history_pages_fit(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    now = int(time.time())
    seed_history(server.db, "user-1", now=now)
    store_report(server.db, "user-1", "rep_browser", taken_at=now - 200)
    store_report(server.db, "user-1", "rep_older", taken_at=now - 3 * 86400)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{server.base}/history/reports/rep_browser", wait_until="networkidle")
        state = page.evaluate(
            """() => {
              const tile = document.querySelector('.tiles.wide .tile');
              const cs = tile ? getComputedStyle(tile) : null;
              const svg = document.querySelector('.chart svg');
              const line = document.querySelector('.chart polyline.s1');
              return {
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth,
                tiles: document.querySelectorAll('.tiles.wide .tile').length,
                tileBg: cs ? cs.backgroundColor : null,
                tileDisplay: cs ? cs.display : null,
                charts: document.querySelectorAll('.chart svg').length,
                svgWidth: svg ? svg.getBoundingClientRect().width : 0,
                lineStroke: line ? getComputedStyle(line).stroke : null,
                copy: !!document.querySelector('.copybtn[data-copy=rep-md]'),
                actionsRow: getComputedStyle(document.querySelector('.report .form-actions')).display,
                text: document.body.innerText,
                bodyBg: getComputedStyle(document.body).backgroundColor,
                wide: Array.from(document.querySelectorAll('body *'))
                  .filter(e => e.getBoundingClientRect().right > window.innerWidth)
                  .map(e => e.tagName + '.' + e.className).slice(0, 12),
              };
            }"""
        )
        _shot(page, f"report-{width}-{scheme}")
        assert state["scrollWidth"] <= state["innerWidth"], state["wide"]
        assert state["tiles"] >= 5 and state["tileDisplay"] == "flex"
        assert state["tileBg"] not in (None, "rgba(0, 0, 0, 0)", "transparent"), "tiles are styled"
        assert state["charts"] == 4 and 0 < state["svgWidth"] <= state["innerWidth"]
        assert state["lineStroke"] == "rgb(250, 137, 13)", state["lineStroke"]  # the theme's orange
        assert state["copy"] and state["actionsRow"] == "flex"
        assert "goldfish_" not in state["text"] and "What the solo test could not model" in state["text"]
        assert state["bodyBg"] == ("rgb(24, 24, 24)" if scheme == "dark" else "rgb(249, 250, 251)")

        page.goto(f"{server.base}/history?deck_id=42", wait_until="networkidle")
        hist = page.evaluate(
            """() => {
              const day = document.querySelector('.history .day');
              const sel = document.querySelector('.filterbar select');
              const spark = document.querySelector('.trend .tmetric');
              return {
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth,
                rows: document.querySelectorAll('.hrow').length,
                dayPosition: day ? getComputedStyle(day).position : null,
                selectArrow: sel ? getComputedStyle(sel).appearance : null,
                selectWidth: sel ? sel.getBoundingClientRect().width : 0,
                trendBorder: spark ? getComputedStyle(spark).borderTopStyle : null,
              };
            }"""
        )
        _shot(page, f"history-{width}-{scheme}")
        assert hist["scrollWidth"] <= hist["innerWidth"], hist
        assert hist["rows"] == 25 and hist["dayPosition"] == "sticky"
        assert hist["selectArrow"] == "none" and 0 < hist["selectWidth"] <= hist["innerWidth"]
        assert hist["trendBorder"] == "solid", "trend cards are styled"
        # a row's details open in place and the Restore button sits in a form-actions row
        page.locator(".hrow.k-snapshot details summary").first.click()
        restore = page.locator(".hrow.k-snapshot details[open] .form-actions button").first
        assert restore.is_visible() and "Restore (review first)" in restore.inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        # the "When" and "Group by" selects render as themed selects (no native date input) and,
        # like the other filters, reach the URL through the Filter button
        assert page.locator("input[type=date]").count() == 0
        for sel in ("#h-when", "#h-group"):
            assert (
                page.locator(sel).is_visible()
                and page.evaluate(f"getComputedStyle(document.querySelector('{sel}')).appearance") == "none"
            )
        page.select_option("#h-when", "7d")
        page.select_option("#h-group", "deck")
        page.locator(".filterbar button[type=submit]").click()
        page.wait_for_load_state("networkidle")
        assert "when=7d" in page.url and "group=deck" in page.url and "deck_id=42" in page.url
        assert page.locator(".history .day.deck").count() == 1
        assert (
            page.locator("#h-when").input_value() == "7d" and page.locator("#h-group").input_value() == "deck"
        )
        # the backups panel (when present) keeps a gap from the pager
        gap = page.evaluate(
            """() => {
              const pager = document.querySelector('.pager'), backups = document.querySelector('#backups');
              if (!pager || !backups) return null;
              return backups.getBoundingClientRect().top - pager.getBoundingClientRect().bottom;
            }"""
        )
        assert gap is None or gap >= 12, gap
        assert not errors, errors
        assert not errors, errors
        browser.close()


def test_forge_section_refreshes_itself_until_the_run_ends(server: Server) -> None:
    """A queued or running Forge run: the card updates in place (forge-live.js) once the run is
    stored as done, without a reload, and the polling then stops."""
    import json

    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    store_report(server.db, "user-1", "rep_forge")

    def set_forge(section: dict) -> None:
        with server.db.tx() as c:
            c.execute(
                "UPDATE reports SET forge_json = ?, forge_state = ? WHERE id = 'rep_forge'",
                (json.dumps(section), section["state"]),
            )

    set_forge({"state": "running", "job_id": "0" * 16, "not_played": []})
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 360, "height": 900})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        polls: list[str] = []
        page.on("request", lambda r: polls.append(r.url) if "/api/v1/reports/" in r.url else None)
        page.clock.install()
        page.goto(f"{server.base}/history/reports/rep_forge", wait_until="networkidle")
        assert page.locator(".card.forge[data-forge-live]").count() == 1
        assert "updates by itself" in page.locator(".card.forge").inner_text()
        assert page.locator("#rep-status").inner_text().startswith("running")  # the overall status
        page.evaluate("window.__same_page = true")  # gone if the page reloads
        # review r5 M3: a poll that brings nothing new leaves the live card's nodes untouched, so
        # screen readers are not told the whole card again every 5 seconds
        page.evaluate(
            "() => { const els = [...document.querySelectorAll('.card.forge > *, #rep-status > *')];"
            " els.forEach(n => { n.__kept = true; }); window.__nodes = els.length; }"
        )
        with page.expect_response(lambda r: r.url.endswith("/api/v1/reports/rep_forge/forge")):
            page.clock.run_for(6000)
        time.sleep(0.5)  # the answer is read and applied (fetch is not on the page clock)
        kept = page.evaluate(
            "() => [...document.querySelectorAll('.card.forge > *, #rep-status > *')]"
            ".filter(n => n.__kept).length"
        )
        assert kept == page.evaluate("window.__nodes") > 0, kept
        set_forge(
            {
                "state": "done",
                "job_id": "0" * 16,
                "not_played": [],
                "result": {
                    "games": 2,
                    "games_requested": 2,
                    "seats": [{"deck": "Immortal Reckoning", "wins": 1, "win_rate": 0.5}],
                },
            }
        )
        page.clock.run_for(16000)
        page.wait_for_function("() => !document.querySelector('.card.forge[data-forge-live]')")
        text = page.locator(".card.forge").inner_text()
        assert "2 of 2\nGames played" in text and "running" not in text, text
        # A32: it asked the small JSON status route, not the whole page, while the run played
        assert polls and all(u.endswith("/api/v1/reports/rep_forge/forge") for u in polls), polls
        # the card itself stayed, so it is still the live region screen readers announce
        assert page.locator(".card.forge[aria-live=polite]").count() == 1
        assert page.evaluate("window.__same_page === true")
        assert page.locator("#rep-status").inner_text().startswith("complete")  # follows the run
        # Copy as Markdown too: read from the page by a second request once the card has ended, so
        # it may land after the card swap (asserting at once failed under load)
        page.wait_for_function(
            "() => document.getElementById('rep-md').value.includes('Games played: 2 of 2.')", timeout=10000
        )
        assert not errors, errors
        browser.close()


# phone, a folded Fold's cover screen, an unfolded Fold, a laptop
@pytest.mark.parametrize("width", [320, 344, 717, 1366])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_report_trends_fit_and_read(server: Server, width: int, scheme: str) -> None:
    """A deck's report trends: no sideways scroll (the table scrolls in its own box), each
    sparkline is a labelled image drawn in a colour with 3:1 against the card, and every text
    in the section keeps 4.5:1 in both themes."""
    from playwright.sync_api import sync_playwright

    from .test_editor_leave_browser import CONTRAST
    from .test_history_page import GOLDFISH_PRECON

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    now = int(time.time())
    store_report(server.db, "user-1", "rep_a", taken_at=now - 6 * 86400)
    store_report(server.db, "user-1", "rep_b", taken_at=now - 3 * 86400, goldfish=None)
    store_report(
        server.db,
        "user-1",
        "rep_c",
        taken_at=now - 200,
        stats={"average_mana_value": 3.05, "price_total": 1234.5, "land_count": 35},
        goldfish=GOLDFISH_PRECON,
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/history?deck_id=42", wait_until="networkidle")
        page.locator(".trend details.tdata summary").click()
        _shot(page, f"trends-{width}-{scheme}")
        state = page.evaluate(
            """(contrast) => {
              const ratio = eval(contrast);
              const trend = document.querySelector('.trend');
              const texts = Array.from(trend.querySelectorAll('*'))
                .filter(e => Array.from(e.childNodes).some(n => n.nodeType === 3 && n.textContent.trim()))
                .filter(e => e.getClientRects().length);
              const low = texts.map(e => [e.tagName + ' ' + e.textContent.trim().slice(0, 30), ratio(e)])
                .filter(([, r]) => r < 4.5);
              const svg = trend.querySelector('svg.tspark');
              // the line against the card behind it: its stroke as the "text" colour
              const probe = document.createElement('span');
              probe.style.color = getComputedStyle(svg.querySelector('polyline')).stroke;
              svg.parentElement.appendChild(probe);
              const lineRatio = ratio(probe);
              probe.remove();
              const tbl = trend.querySelector('.tbl');
              return {
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth,
                low,
                checked: texts.length,
                lineRatio,
                svgs: Array.from(trend.querySelectorAll('svg.tspark')).map(s => [
                  s.getAttribute('role'), s.getAttribute('aria-label'), s.getBoundingClientRect().width]),
                tableRight: tbl.getBoundingClientRect().right,
              };
            }""",
            CONTRAST,
        )
        assert state["scrollWidth"] <= state["innerWidth"], state
        assert state["tableRight"] <= state["innerWidth"]
        assert state["checked"] > 20 and not state["low"], state["low"]
        assert state["lineRatio"] >= 3, state["lineRatio"]
        assert len(state["svgs"]) == 6
        for role, label, w in state["svgs"]:
            assert role == "img" and "over" in label and " on " in label and 0 < w <= state["innerWidth"]
        assert not errors, errors
        browser.close()


FORGE_DONE = {
    "state": "done",
    "job_id": "0" * 16,
    "seed": 7,
    "not_played": [],
    "seats": ["Immortal Reckoning", "Riders of the Long Name Precon Deck", "Precon 2", "Precon 3"],
    "result": {
        "games": 10,
        "games_requested": 10,
        "draws": 1,
        "stopped_slow": 1,
        "average_game_seconds": 141.2,
        "median_game_seconds": 128.0,
        "median_turn": 12,
        "turns_range": [8, 21],
        "ended": {"won": 9, "time_limit": 1},
        "seats": [
            {
                "deck": "Immortal Reckoning",
                "wins": 4,
                "win_rate": 0.4,
                "win_rate_95": [0.17, 0.69],
                "losses": {"commander damage": 2, "life": 3},
            },
            {
                "deck": "Riders of the Long Name Precon Deck",
                "wins": 3,
                "win_rate": 0.3,
                "win_rate_95": [0.11, 0.6],
                "losses": {"life": 6},
            },
            {
                "deck": "Precon 2",
                "wins": 1,
                "win_rate": 0.1,
                "win_rate_95": [0.02, 0.4],
                "losses": {"life": 8},
            },
            {
                "deck": "Precon 3",
                "wins": 1,
                "win_rate": 0.1,
                "win_rate_95": [0.02, 0.4],
                "losses": {"poison": 1, "life": 7},
            },
        ],
        "game_list": [
            {"game": i, "seconds": 100.0 + i, "turn": 8 + i, "winner": 1 + i % 4, "ended": "won"}
            for i in range(1, 10)
        ]
        + [{"game": 10, "seconds": 600.0, "turn": None, "winner": None, "ended": "time_limit"}],
    },
}


# phone, a folded Fold's cover screen, an unfolded Fold, a laptop
@pytest.mark.parametrize("width", [320, 344, 717, 1366])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_forge_results_fit_and_read(server: Server, width: int, scheme: str) -> None:
    """G9: Forge's games laid out as tiles, a table of the decks at the table, how the games ended
    and each game: no sideways page scroll (tables scroll in their own box), 4.5:1 text in both
    themes, and the folded game list opens with a 44px target."""
    import json

    from playwright.sync_api import sync_playwright

    from .test_editor_leave_browser import CONTRAST

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    store_report(server.db, "user-1", "rep_forge")
    with server.db.tx() as c:
        c.execute(
            "UPDATE reports SET forge_json = ?, forge_state = 'done' WHERE id = 'rep_forge'",
            (json.dumps(FORGE_DONE),),
        )
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/history/reports/rep_forge", wait_until="networkidle")
        summary = page.locator(".forge-results details.games summary")
        assert summary.bounding_box()["height"] >= 44
        summary.click()
        page.locator(".card.forge").scroll_into_view_if_needed()
        if SCRATCH.is_dir():
            page.locator(".card.forge").screenshot(path=str(SCRATCH / f"forge-results-{width}-{scheme}.png"))
        state = page.evaluate(
            """(contrast) => {
              const ratio = eval(contrast);
              const box = document.querySelector('.card.forge');
              const texts = Array.from(box.querySelectorAll('*'))
                .filter(e => Array.from(e.childNodes).some(n => n.nodeType === 3 && n.textContent.trim()))
                .filter(e => e.getClientRects().length && !e.closest('.sr-only'));
              const low = texts.map(e => [e.tagName + ' ' + e.textContent.trim().slice(0, 30), ratio(e)])
                .filter(([, r]) => r < 4.5);
              return {
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth,
                cardRight: box.getBoundingClientRect().right,
                low,
                checked: texts.length,
                rows: box.querySelectorAll('table.seats tbody tr').length,
                games: box.querySelectorAll('details.games tbody tr').length,
              };
            }""",
            CONTRAST,
        )
        assert state["scrollWidth"] <= state["innerWidth"], state
        assert state["cardRight"] <= state["innerWidth"], state
        assert state["checked"] > 30 and not state["low"], state["low"]
        assert state["rows"] == 4 and state["games"] == 10
        text = page.locator(".card.forge").inner_text()
        assert "turn 12" in text and "commander damage 2" in text and "Stopped at the time limit" in text
        assert not errors, errors
        browser.close()


@pytest.mark.parametrize("width", [320, 344, 600, 640, 717, 1366])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_reports_page_fits_and_reads(server: Server, width: int, scheme: str) -> None:
    """K8 (Q136): the Reports page under More, with a Forge run playing, one waiting and a long deck
    name: no sideways scroll, 4.5:1 text in both themes, 44px filter and trend targets."""
    import json
    import os

    from playwright.sync_api import sync_playwright

    from .test_editor_leave_browser import CONTRAST

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    now = int(time.time())
    store_report(server.db, "user-1", "rep_done", taken_at=now - 3 * 86400)
    long_name = "The Extremely Long Deck Name That Goes On And On Without Any Break At All"
    store_report(server.db, "user-1", "rep_play", deck_id="43", deck_name=long_name, taken_at=now - 600)
    store_report(server.db, "user-1", "rep_wait", taken_at=now - 300)
    playing = {"state": "running", "job_id": "job1", "games_requested": 10, "result": {"games": 3}}
    waiting = {"state": "queued", "waiting": True, "games_requested": 10, "queued_at": now - 300}
    with server.db.tx() as c:
        for rid, section in (("rep_play", playing), ("rep_wait", waiting)):
            c.execute(
                "UPDATE reports SET forge_json = ?, forge_state = ? WHERE id = ?",
                (json.dumps(section), section["state"], rid),
            )
    shots = os.environ.get("MTG_SHOTS")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/reports", wait_until="networkidle")
        if shots:
            page.screenshot(path=f"{shots}/reports-page-{width}-{scheme}.png", full_page=True)
        state = page.evaluate(
            """(contrast) => {
              const ratio = eval(contrast);
              const box = document.querySelector('.reports-page');
              const texts = Array.from(box.querySelectorAll('*'))
                .filter(e => Array.from(e.childNodes).some(n => n.nodeType === 3 && n.textContent.trim()))
                .filter(e => e.getClientRects().length && !e.closest('.sr-only'));
              const low = texts.map(e => [e.tagName + ' ' + e.textContent.trim().slice(0, 30), ratio(e)])
                .filter(([, r]) => r < 4.5);
              const h = s => Array.from(box.querySelectorAll(s)).map(e => e.getBoundingClientRect().height);
              return {
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth,
                right: Math.max(...Array.from(box.querySelectorAll('*'))
                  .map(e => e.getBoundingClientRect().right)),
                low,
                checked: texts.length,
                rows: box.querySelectorAll('.rp-row').length,
                runs: box.querySelectorAll('.rp-runs li').length,
                targets: [...h('.rp-trend'), ...h('.rp-filter select:not(.msel-native)'),
                          ...h('.rp-filter .msel-btn'), ...h('.rp-filter .btn')],
              };
            }""",
            CONTRAST,
        )
        assert state["scrollWidth"] <= state["innerWidth"], state
        assert state["right"] <= state["innerWidth"] + 0.5, state
        assert state["checked"] > 10 and not state["low"], state["low"]
        assert state["rows"] == 3 and state["runs"] == 2
        assert min(state["targets"]) >= 44, state["targets"]
        if width > 600:
            # review r2 item 19: on wider screens the Show button stays on the select's line (at
            # 600px and narrower the site's phone layout makes a form's button full width, below)
            tops = page.evaluate(
                "() => ['.rp-filter .msel-btn', '.rp-filter .btn'].map(s =>"
                " Math.round(document.querySelector(s).getBoundingClientRect().top))"
            )
            assert tops[0] == tops[1], tops
        text = page.locator(".rp-queue").inner_text()
        # review r1 fix 1: the games played so far, as the report page counts them
        assert "Playing: 3 of 10 games" in text
        assert "Waiting: number 1 in line" in text
        # review r1 fix 2 and r3 item 3: at 200% text the deck filter still fits, inside the window
        # and its panel, at every width, and the whole page fits the window too.
        from .test_overflow_sweep_browser import MEASURE
        from .test_text_zoom_browser import ROOT_200, TEXT_200

        for zoom in (ROOT_200, TEXT_200):
            page.reload(wait_until="networkidle")
            page.evaluate(zoom)
            fit = page.evaluate(
                """() => {
                  const panel = document.querySelector('.rp-all').getBoundingClientRect();
                  return Array.from(document.querySelectorAll('.rp-filter, .rp-filter *'))
                    .filter(e => e.getClientRects().length && !e.closest('.msel-native'))
                    .map(e => [e.tagName + '.' + e.className, e.getBoundingClientRect()])
                    .filter(([, r]) => r.width && (r.right > Math.min(window.innerWidth, panel.right) + 0.5
                                                   || r.left < Math.max(0, panel.left) - 0.5))
                    .map(([n, r]) => [n, Math.round(r.left), Math.round(r.right), window.innerWidth]);
                }"""
            )
            assert fit == [], (zoom[:40], fit)
            found = page.evaluate(MEASURE)
            assert found == [], (zoom[:40], found)
            if shots:
                page.screenshot(path=f"{shots}/reports-page-{width}-{scheme}-text200.png", full_page=True)
        page.select_option("#rp-deck", "43")
        page.locator(".rp-filter .btn").click()
        page.wait_for_load_state("networkidle")
        assert page.locator(".rp-row").count() == 1 and "deck_id=43" in page.url
        assert not errors, errors
        browser.close()


@pytest.mark.parametrize("width", [320, 717, 1366])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_saved_descriptions_and_remove_fit_and_read(
    server: Server, width: int, scheme: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q138 items 4 and 6: a report's saved card descriptions, with Remove on the member's own,
    and both recognition figures: no sideways scroll, 4.5:1 text, a 44px Remove target; Remove
    takes the description away and says so."""
    import json
    import os

    from playwright.sync_api import sync_playwright

    from mtg_gateway import card_models
    from mtg_gateway.card_models import KeptModels

    from .test_card_models import _Store
    from .test_editor_leave_browser import CONTRAST

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    monkeypatch.setattr(card_models, "SWITCHED_ON", True)
    store = _Store()
    reports = server.app.state.gateway.reports
    reports.models = KeptModels(store)
    store_report(server.db, "user-1", "rep_kept")
    used = [
        {"name": "Sol Ring", "model": "abc123abc123", "yours": True, "changed_at": 1_790_000_000},
        {
            "name": "An Unusually Long Card Name, the Wanderer of the Endless Plains",
            "model": "def456def456",
            "yours": False,
            "changed_at": 1_790_000_000,
        },
    ]
    row = server.db._one("SELECT goldfish_json FROM reports WHERE id = 'rep_kept'", ())
    goldfish = {**json.loads(row["goldfish_json"]), "kept_models": used}
    with server.db.tx() as c:
        c.execute("UPDATE reports SET goldfish_json = ? WHERE id = 'rep_kept'", (json.dumps(goldfish),))
    store.put_card_model("sol ring", "Sol Ring", json.dumps({"name": "Sol Ring", "inert": True}), "user-1", 1)
    shots = os.environ.get("MTG_SHOTS")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/history/reports/rep_kept", wait_until="networkidle")
        kept = page.locator(".report .kept")
        kept.scroll_into_view_if_needed()
        if shots:
            kept.screenshot(path=f"{shots}/report-saved-{width}-{scheme}.png")
            page.locator(".report .coverage").screenshot(path=f"{shots}/report-coverage-{width}-{scheme}.png")
        state = page.evaluate(
            """(contrast) => {
              const ratio = eval(contrast);
              const box = document.querySelector('.report .kept');
              const texts = Array.from(box.querySelectorAll('*'))
                .filter(e => Array.from(e.childNodes).some(n => n.nodeType === 3 && n.textContent.trim()))
                .filter(e => e.getClientRects().length && !e.closest('.sr-only'));
              return {
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth,
                right: Math.max(...Array.from(box.querySelectorAll('*'))
                  .map(e => e.getBoundingClientRect().right)),
                low: texts.map(e => [e.textContent.trim().slice(0, 30), ratio(e)]).filter(([, r]) => r < 4.5),
                buttons: Array.from(box.querySelectorAll('button')).map(b => b.offsetHeight),
              };
            }""",
            CONTRAST,
        )
        assert state["scrollWidth"] <= state["innerWidth"], state
        assert state["right"] <= state["innerWidth"] + 0.5, state
        assert not state["low"], state["low"]
        assert len(state["buttons"]) == 1 and state["buttons"][0] >= 44, state["buttons"]
        coverage = page.locator(".report .coverage").inner_text()
        assert "Played with their effect:" in coverage and "Known:" in coverage
        page.get_by_role("button", name="Remove the saved description of Sol Ring").click()
        page.wait_for_load_state("networkidle")
        assert "models=removed" in page.url and "sol ring" not in store.rows
        assert "The saved description was removed." in page.locator("main").inner_text()
        assert page.locator(".report .kept button").count() == 0
        assert not errors, errors
        browser.close()


@pytest.mark.parametrize("width", [344, 390, 673, 1280])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_compare_page_never_scrolls_sideways_at_200_percent_text(
    server: Server, width: int, scheme: str
) -> None:
    """Review r5 M2: the Compare page's two columns were at least 18rem wide, wider than a phone
    at 200% text (532 px of page at 390 px). At 100% and 200% text (root size and text-only), on
    a phone, a Fold folded and open, and a desktop, in light and dark, nothing is outside the
    window and the page does not scroll sideways."""
    from playwright.sync_api import sync_playwright

    from .test_text_zoom_browser import ROOT_200, TEXT_200

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    server.ark.private.discard(43)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        for zoom in ("none", ROOT_200, TEXT_200):
            page.goto(f"{server.base}/decks/42/compare?with=43", wait_until="networkidle")
            assert page.locator(".comparecols .panel").count() >= 2  # both decks' columns
            if zoom != "none":
                page.evaluate(zoom)
            state = page.evaluate(
                """() => {
                  const vw = document.documentElement.clientWidth;
                  const out = Array.from(document.querySelectorAll('.comparecols, .comparecols *'))
                    .filter(e => e.getClientRects().length && e.getBoundingClientRect().right > vw + 1);
                  return {sw: document.documentElement.scrollWidth, vw,
                          out: out.slice(0, 3).map(e => e.tagName + ' ' + e.textContent.trim().slice(0, 30))};
                }"""
            )
            label = "none" if zoom == "none" else ("root" if zoom == ROOT_200 else "text")
            _shot(page, f"compare-{width}-{scheme}-{label}")
            assert not state["out"], (label, state)
            assert state["sw"] <= state["vw"], (label, state)
        browser.close()


# phone, a folded Fold's cover screen, an unfolded Fold, a laptop
@pytest.mark.parametrize("width", [320, 344, 717, 1366])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_play_more_games_fits_reads_and_starts(server: Server, width: int, scheme: str) -> None:
    """Q146 item 10: the Play more games button under the Forge card, at 100% and 200% text, in
    light and dark: inside the window, a 44px target, 4.5:1 text. Pressing it starts more games at
    the same table; a refusal comes back to the report saying why."""
    import json

    import httpx
    from playwright.sync_api import sync_playwright

    from .test_editor_leave_browser import CONTRAST
    from .test_forge import DECK_TEXT, FakeForge
    from .test_text_zoom_browser import ROOT_200, TEXT_200

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    fake = FakeForge()
    server.app.state.gateway.reports.forge = fake.client()
    sid = server.sign_in_and_link()
    store_report(server.db, "user-1", "rep_more")
    seats = ["Immortal Reckoning", "Precon 1", "Precon 2", "Precon 3"]
    played = [{"game": i, "ms": 60000, "winner": 1 + i % 4, "draw": False} for i in range(1, 5)]
    section = {
        **FORGE_DONE,
        "seats": seats,
        "result": {
            **FORGE_DONE["result"],
            "seats": [{**s, "deck": n} for s, n in zip(FORGE_DONE["result"]["seats"], seats, strict=True)],
        },
        "played": played,
        "deck_text": DECK_TEXT,
    }
    with server.db.tx() as c:
        c.execute(
            "UPDATE reports SET forge_json = ?, forge_state = 'done' WHERE id = 'rep_more'",
            (json.dumps(section),),
        )
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for zoom in ("none", ROOT_200, TEXT_200):
            page.goto(f"{server.base}/history/reports/rep_more", wait_until="networkidle")
            if zoom != "none":
                page.evaluate(zoom)
            box = page.locator("#forge-more")
            box.scroll_into_view_if_needed()
            label = "none" if zoom == "none" else ("root" if zoom == ROOT_200 else "text")
            if SCRATCH.is_dir():
                box.screenshot(path=str(SCRATCH / f"forge-more-{width}-{scheme}-{label}.png"))
            state = page.evaluate(
                """(contrast) => {
                  const ratio = eval(contrast);
                  const box = document.getElementById('forge-more');
                  const vw = document.documentElement.clientWidth;
                  const texts = Array.from(box.querySelectorAll('*'))
                    .filter(e => Array.from(e.childNodes)
                      .some(n => n.nodeType === 3 && n.textContent.trim()));
                  const button = box.querySelector('button').getBoundingClientRect();
                  const out = Array.from(box.querySelectorAll('*'))
                    .filter(e => e.getClientRects().length && e.getBoundingClientRect().right > vw + 1);
                  return {sw: document.documentElement.scrollWidth, vw, buttonHeight: button.height,
                          low: texts.map(e => [e.textContent.trim().slice(0, 30), ratio(e)])
                            .filter(([, r]) => r < 4.5),
                          out: out.map(e => e.tagName), checked: texts.length};
                }""",
                CONTRAST,
            )
            assert state["sw"] <= state["vw"] and not state["out"], (label, state)
            assert state["buttonHeight"] >= 44 and state["checked"] >= 2 and not state["low"], (label, state)
        page.goto(f"{server.base}/history/reports/rep_more", wait_until="networkidle")
        page.locator("#forge-more button").click()
        page.wait_for_load_state("networkidle")
        assert page.url.endswith("/history/reports/rep_more")
        assert len(fake.started) == 1 and fake.started[0]["games"] == 4
        assert [d.get("precon") for d in fake.started[0]["decks"][1:]] == seats[1:]
        assert page.locator("#forge-more button").count() == 0  # none while the games play
        # a refusal, shown from its code
        page.goto(f"{server.base}/history/reports/rep_more?more=queue", wait_until="networkidle")
        notice = page.locator("p.notice.warn").first.inner_text()
        assert notice.startswith("Your Forge queue has no room for more games now")
        assert not errors, errors
        browser.close()
    with httpx.Client(base_url=server.base, cookies={"mtg_session": sid}) as h:
        assert h.post("/history/reports/rep_more/forge/more", data={}).status_code == 403
        csrf = h.get("/history/reports/rep_more").text.split("name='csrf' value='", 1)[1].split("'", 1)[0]
        ended = h.post("/history/reports/rep_more/forge/more", data={"csrf": csrf})
        assert ended.status_code == 303 and ended.headers["location"].endswith("?more=ended")
        assert h.post("/history/reports/nope/forge/more", data={"csrf": csrf}).status_code == 404
