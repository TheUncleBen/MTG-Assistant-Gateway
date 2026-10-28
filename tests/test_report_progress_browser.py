"""The report page while its solo test runs, in headless Chromium: the section says it is running,
fits at phone, open Fold and desktop widths in light and dark, and the page fills in by itself once
the research service's answer is stored. A failed solo test offers Run again as a full-size button.
Screenshots land in .scratch/ when it exists (never committed). Skipped without a Chromium build."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from .test_history_page import GOLDFISH, store_report
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


def _shot(page, name: str) -> None:  # noqa: ANN001
    if SCRATCH.is_dir():
        page.screenshot(path=str(SCRATCH / f"{name}.png"), full_page=False)


def _running(server: Server, rid: str) -> None:
    store_report(server.db, "user-1", rid, goldfish=None)
    with server.db.tx() as c:
        c.execute(
            "UPDATE reports SET research_state = 'running', validation_json = NULL WHERE id = ?", (rid,)
        )


def test_the_running_report_fits_and_fills_in_by_itself(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    _running(server, "rep_run")
    store_report(server.db, "user-1", "rep_bad", goldfish=None, taken_at=int(time.time()) - 900)
    with server.db.tx() as c:
        failed = {"tool": "goldfish_run", "ok": False, "error": True, "text": "Error executing tool x: boom"}
        c.execute("UPDATE reports SET goldfish_json = ? WHERE id = 'rep_bad'", (json.dumps(failed),))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            for width in (390, 840, 1280):
                for scheme in ("light", "dark"):
                    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
                    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
                    page = ctx.new_page()
                    for rid in ("rep_run", "rep_bad"):
                        page.goto(f"{server.base}/history/reports/{rid}", wait_until="networkidle")
                        page.locator("#rv-solo").scroll_into_view_if_needed()
                        _shot(page, f"report-{rid}-{width}-{scheme}")
                        state = page.evaluate(
                            """() => ({
                              scroll: document.documentElement.scrollWidth, inner: window.innerWidth,
                              solo: document.querySelector('#rv-solo').innerText,
                              again: (() => { const b = document.querySelector('#rv-solo .rv-again button');
                                return b ? b.getBoundingClientRect().height : 0; })(),
                            })"""
                        )
                        assert state["scroll"] <= state["inner"], (rid, width, scheme)
                        if rid == "rep_run":
                            assert "The solo test is running on the research service." in state["solo"]
                        else:
                            assert "the research service did not answer" in state["solo"]
                            assert "Error executing tool" not in state["solo"]
                            assert state["again"] >= 44, state
                    page.goto(f"{server.base}/reports", wait_until="networkidle")
                    _shot(page, f"reports-running-{width}-{scheme}")
                    listed = page.evaluate(
                        "() => [document.documentElement.scrollWidth, window.innerWidth, "
                        "document.body.innerText]"
                    )
                    assert listed[0] <= listed[1] and "The solo test is running" in listed[2], (width, scheme)
                    ctx.close()
            # the answer comes in: the page reads itself again and shows the solo test
            ctx = browser.new_context(viewport={"width": 390, "height": 900})
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            page.goto(f"{server.base}/history/reports/rep_run", wait_until="networkidle")
            assert page.locator("#rv-solo[data-research]").count() == 1
            with server.db.tx() as c:
                c.execute(
                    "UPDATE reports SET research_state = NULL, goldfish_json = ? WHERE id = 'rep_run'",
                    (json.dumps({"tool": "goldfish_run", "ok": True, "text": GOLDFISH}),),
                )
            page.wait_for_selector(".tiles.wide .tile", timeout=10_000)
            assert page.locator("#rv-solo[data-research]").count() == 0
            ctx.close()
        finally:
            browser.close()
