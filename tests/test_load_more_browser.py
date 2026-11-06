"""Load more on History, Proposals and My activity in headless Chromium (Standard 13): pressing it
adds the next rows in place, without leaving the page, and never draws a day heading twice; the
button is 44 px tall on a phone, at 390 and 1280 px, light and dark."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from .test_history_page import seed_history
from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1280, "light")])
def test_load_more_adds_rows_in_place(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    seed_history(server.db, "user-1", now=int(time.time()))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(
                viewport={"width": width, "height": 900}, color_scheme=scheme, has_touch=width < 900
            )
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for path, table in (("/history", ".hy-table"), ("/proposals?show=all", ".pr-table")):
                page.goto(f"{server.base}{path}", wait_until="networkidle")
                rows = page.locator(f"{table} > .tr:not(.th):not(.daygroup):not(.group)")
                before = rows.count()
                assert before == 25, (path, before)
                btn = page.locator(".loadmore [data-load-more]")
                box = btn.bounding_box()
                assert box is not None and box["height"] >= (44 if width < 900 else 36)
                url = page.url
                btn.click()
                for _ in range(100):
                    if rows.count() > before:
                        break
                    page.wait_for_timeout(50)  # fixed wait: poll interval of a loop with a deadline
                assert rows.count() > before and page.url == url, path
                days = page.locator(f"{table} > .daygroup").all_inner_texts()
                plain = [d.split("·")[0].strip() for d in days]
                assert len(plain) == len(set(plain)), plain
                assert page.locator(".ui-pager").count() == 0
            assert errors == []
        finally:
            browser.close()


ROWS = "document.querySelectorAll('.hy-table > .tr:not(.th):not(.daygroup):not(.group)').length"
# the one wording for a page that could not be read (static/fetchjson.js, audit F-5)
NO_CONNECTION = "No connection; nothing more could be loaded. Try again."
DID_NOT_WORK = "That did not work; nothing more could be loaded. Try again."
SIGNED_OUT = "You're signed out, so nothing more could be loaded. Try again."


def _history(p, server: Server, sid: str, exe: str):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server.base}/history", wait_until="networkidle")
    return browser, page, errors


def test_the_pages_loaded_come_back_after_back_and_a_reload_with_the_scroll(server: Server) -> None:
    """Audit F-4 (eccfa3d9): the pages Load more added were lost on Back and a reload (the first
    page showed again). They are kept on the history entry with where the page was scrolled to
    (static/loadmore.js): a page loaded again on that entry presses its Load more that many times
    and scrolls back to where it was."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    seed_history(server.db, "user-1", now=int(time.time()))
    with sync_playwright() as p:
        browser, page, errors = _history(p, server, sid, exe)
        try:
            assert page.evaluate(f"() => {ROWS}") == 25
            page.locator(".loadmore [data-load-more]").click()
            page.wait_for_function(f"() => {ROWS} > 25", timeout=5000)
            n = page.evaluate(f"() => {ROWS}")
            assert page.evaluate("() => history.state && history.state.mtgmore") == 1
            page.evaluate("() => window.scrollTo(0, 500)")
            page.wait_for_function(
                "() => window.scrollY > 0 && history.state && history.state.mtgscroll === window.scrollY"
            )  # the place is noted once the scrolling settles (loadmore.js)
            y = page.evaluate("() => window.scrollY")
            assert y > 0
            # away and Back: the rows and the place are back
            page.goto(f"{server.base}/account", wait_until="networkidle")
            page.go_back()
            page.wait_for_function(f"() => {ROWS} === {n}", timeout=5000)
            page.wait_for_function(f"() => Math.abs(window.scrollY - {y}) < 40", timeout=5000)
            assert page.evaluate("() => history.state && history.state.mtgmore") == 1
            # a reload: the same
            page.reload(wait_until="networkidle")
            page.wait_for_function(f"() => {ROWS} === {n}", timeout=5000)
            page.wait_for_function(f"() => Math.abs(window.scrollY - {y}) < 40", timeout=5000)
            assert errors == [], errors
        finally:
            browser.close()


def test_a_page_that_could_not_be_loaded_says_so_in_the_one_wording(server: Server) -> None:
    """Audit F-5 (eccfa3d9): the Load more failure was worded five ways. Every Load more says it
    the one way (static/fetchjson.js's words with "nothing more could be loaded"): no connection,
    an answer that is not the page, and signed out, with Sign in; the button stays to try again."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    seed_history(server.db, "user-1", now=int(time.time()))
    with sync_playwright() as p:
        browser, page, errors = _history(p, server, sid, exe)
        try:
            btn = page.locator(".loadmore [data-load-more]")
            href = btn.get_attribute("href")
            how = {"answer": "abort"}

            def answer(route):  # noqa: ANN001
                if how["answer"] == "abort":
                    route.abort()
                elif how["answer"] == "500":
                    route.fulfill(status=500, content_type="text/html", body="<p>broken</p>")
                else:
                    route.fulfill(status=401, content_type="text/html", body="<p>signed out</p>")

            page.route(lambda u: u == f"{server.base}{href}", answer)
            for answer_kind, words in (("abort", NO_CONNECTION), ("500", DID_NOT_WORK), ("401", SIGNED_OUT)):
                how["answer"] = answer_kind
                page.evaluate("() => document.querySelectorAll('.toast').forEach(t => t.remove())")
                btn.click()
                # (the site's own "You're signed out" note, static/fetchjson.js, is another toast)
                toast = page.locator(".toast:not(.session-toast)")
                toast.wait_for(state="visible", timeout=5000)
                assert toast.inner_text().strip().startswith(words), (answer_kind, toast.inner_text())
                page.wait_for_function(
                    "() => !document.querySelector('.loadmore [data-load-more][aria-busy]')"
                )
                assert page.evaluate(f"() => {ROWS}") == 25
            assert toast.locator("a, button", has_text="Sign in").count() == 1
            assert errors == [], errors
        finally:
            browser.close()


def test_a_reload_with_a_layer_open_keeps_the_pages_loaded(server: Server) -> None:
    """Audit NB3 (0fe11813): in the history way (Safari, Firefox, a touch screen), a layer's entry
    held only its own mark, so a reload with a layer open lost the pages Load more had added (46 rows
    went back to the first 25). The layer's entry carries the page's own state (static/layers.js)
    and a reload on it drops only the layer's mark."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    seed_history(server.db, "user-1", now=int(time.time()))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(viewport={"width": 390, "height": 800}, has_touch=True)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/history", wait_until="networkidle")
            assert page.evaluate("() => MtgLayers.mode") == "history"
            page.locator(".loadmore [data-load-more]").click()
            page.wait_for_function(f"() => {ROWS} > 25", timeout=5000)
            n = page.evaluate(f"() => {ROWS}")
            page.evaluate("() => MtgLayers.open({name: 'probe', close: function () {}})")
            assert page.evaluate("() => !!(history.state && history.state.mtglayer)")
            page.reload(wait_until="networkidle")
            page.wait_for_function(f"() => {ROWS} === {n}", timeout=5000)
            assert page.evaluate("() => !(history.state && history.state.mtglayer)")
            assert page.evaluate("() => history.state && history.state.mtgmore") == 1
            assert errors == [], errors
        finally:
            browser.close()
