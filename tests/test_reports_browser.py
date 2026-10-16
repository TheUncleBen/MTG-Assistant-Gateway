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
        assert "Games played: 2 of 2." in page.locator("#rep-md").input_value()  # Copy as Markdown too
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


@pytest.mark.parametrize("width", [320, 344, 717, 1366])
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
    playing = {"state": "running", "job_id": "job1", "games_requested": 10, "result": {"games_done": 3}}
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
                targets: [...h('.rp-trend'), ...h('.rp-filter select'), ...h('.rp-filter .btn')],
              };
            }""",
            CONTRAST,
        )
        assert state["scrollWidth"] <= state["innerWidth"], state
        assert state["right"] <= state["innerWidth"] + 0.5, state
        assert state["checked"] > 10 and not state["low"], state["low"]
        assert state["rows"] == 3 and state["runs"] == 2
        assert min(state["targets"]) >= 44, state["targets"]
        text = page.locator(".rp-queue").inner_text()
        assert "Playing: 0 of 10 games" in text
        assert "Waiting, place 1" in text
        page.select_option("#rp-deck", "43")
        page.locator(".rp-filter .btn").click()
        page.wait_for_load_state("networkidle")
        assert page.locator(".rp-row").count() == 1 and "deck_id=43" in page.url
        assert not errors, errors
        browser.close()
