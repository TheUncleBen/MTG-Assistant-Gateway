"""The Proposals list's chrome and the review page's lock in headless Chromium (final audit F04,
F09; polish A7): a deck group's Open deck capsule sits inside its row at phone and desktop widths,
the pinned Apply bar shows only once a change is ticked, a locked Apply wears a small lock that
goes the moment the guard opens (the unlocking tap looks pressed meanwhile), and the lock's note
names a tap on a touch screen and the mouse on a desktop. Skipped without a Chromium build."""

from __future__ import annotations

import re

import pytest

from .test_cardsearch_browser import PNG
from .test_overflow_sweep_browser import MEASURE, _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


def _ctx(browser, srv: Server, sid: str, width: int, height: int = 860):  # noqa: ANN001
    touch = width < 900
    ctx = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch, is_mobile=touch)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": srv.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda r: r.fulfill(status=200, content_type="image/png", body=PNG),
    )
    return ctx


def _exe() -> str | None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


@pytest.mark.parametrize("width", [412, 1280])
def test_open_deck_sits_in_its_row_and_the_bar_waits_for_a_tick(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, width)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/proposals", wait_until="networkidle")
        group = page.locator(".pr-table .tr.group").first
        cap = group.locator(".act > .ui-cap", has_text="Open deck")
        g, c = group.bounding_box(), cap.bounding_box()
        assert g and c, (g, c)
        assert c["height"] >= 44, c
        # inside the row: not hanging off its corner (it sat 8 px above and 2 px past it)
        assert c["y"] >= g["y"] and c["y"] + c["height"] <= g["y"] + g["height"], (g, c)
        assert c["x"] + c["width"] <= g["x"] + g["width"], (g, c)
        # the capsule's words sit in the middle of it
        words = cap.evaluate(
            "a => { const r = document.createRange(); r.selectNodeContents(a);"
            " const t = r.getBoundingClientRect(), b = a.getBoundingClientRect();"
            " return [t.top - b.top, b.bottom - t.bottom]; }"
        )
        assert abs(words[0] - words[1]) <= 3, words
        # the bar is not on screen until a change is ticked
        bar = page.locator(".pr-table ~ .ui-bar, form .ui-bar").first
        assert not bar.is_visible()
        page.locator("input[name=pick]").first.check()
        assert bar.is_visible()
        assert bar.locator("button[name=action][value=apply]").is_enabled()
        page.locator("input[name=pick]").first.uncheck()
        assert not bar.is_visible()
        assert page.evaluate(MEASURE) == [], width
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [412, 1280])
def test_a_locked_apply_wears_a_lock_until_the_guard_opens(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid, pending = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, width)
        page = ctx.new_page()
        page.goto(f"{server.base}/proposals/{pending}", wait_until="domcontentloaded")
        form = page.locator("form.guarded").first
        apply = form.locator("button[data-guard][value=apply]")
        apply.wait_for(state="visible")
        lock = apply.locator(".lk")
        assert apply.is_disabled() and lock.is_visible()
        hint = form.locator(".guard-hint")
        text = hint.inner_text()
        if width < 900:
            assert "tap once to unlock" in text and "mouse" not in text, text
            # the unlocking tap: the buttons look pressed until the guard opens
            box = apply.bounding_box()
            page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            assert form.evaluate("f => f.classList.contains('unlocking')")
            assert apply.is_disabled() and lock.is_visible()
        else:
            assert "move the mouse" in text and "tap once" not in text, text
            page.mouse.move(200, 300)
        # the guard opens: the lock goes with the note, so the member sees the next press counts
        page.locator("form.guarded button[data-guard][value=apply]:enabled").first.wait_for(timeout=5000)
        assert not lock.is_visible() and not hint.is_visible()
        assert not form.evaluate("f => f.classList.contains('unlocking')")
        browser.close()
