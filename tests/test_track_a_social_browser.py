"""Release 0.7.29, review round 2: the deck page's confirm chips (Like, Bookmark, Follow and the
comment confirms share the style) show a focus ring when focus moves to Yes by script after a
tap, which :focus-visible may not mark. Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_confirm_chip_shows_a_focus_ring_on_yes(server: Server, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(
                viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True, color_scheme=scheme
            )
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/decks/43", wait_until="networkidle")
            page.locator("[data-social=vote]").click()
            chip = page.locator(".banner .social .confirm")
            chip.wait_for(timeout=5000)
            yes = chip.locator("button").first
            got = yes.evaluate(
                "e => ({focused: e === document.activeElement, ring: getComputedStyle(e).outlineStyle,"
                " width: getComputedStyle(e).outlineWidth})"
            )
            assert got["focused"] and got["ring"] != "none" and got["width"] != "0px", got
            assert server.ark.votes == {}  # nothing sent before Yes
            assert errors == []
        finally:
            browser.close()


def test_the_comment_confirms_show_a_focus_ring(server: Server) -> None:
    """Posting a comment and changing your own ask first; focus moves to the
    confirm's first button, and it shows a ring."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    ring = (
        "() => { const e = document.activeElement; return !!e && e.tagName === 'BUTTON' &&"
        " getComputedStyle(e).outlineStyle !== 'none' && getComputedStyle(e).outlineWidth !== '0px'"
        " && (e.closest('.confirmbar') || e.closest('.confirm')) ? e.textContent : null; }"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/decks/43", wait_until="networkidle")
            form = page.locator("form.newcomment")
            if form.count() == 0:
                pytest.skip("this deck page draws no comment form")
            form.locator("textarea").fill("Nice deck")
            form.locator("button[type=submit]").click()
            form.locator(".confirmbar").wait_for(timeout=5000)
            assert page.evaluate(ring) == "Post", page.evaluate("() => document.activeElement.outerHTML")
            form.locator(".confirmbar button").first.click()
            mine = page.locator(".cmt", has_text="Nice deck").first
            mine.wait_for(timeout=5000)
            mine.locator("button", has_text="Edit").click()
            edit = mine.locator("form.editcomment")
            edit.locator("textarea").fill("Nice deck, changed")
            edit.locator("button", has_text="Save").click()
            edit.locator(".confirm").wait_for(timeout=5000)
            assert page.evaluate(ring) == "Change"
            assert errors == []
        finally:
            browser.close()


def test_the_user_page_follow_confirm_shows_a_focus_ring(server: Server) -> None:
    """Every confirm matches (review round 4 note): the user page's Follow asks first, and its
    first button shows a ring when focus moves to it."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/users/amy", wait_until="networkidle")
            follow = page.locator(".profile [data-social=follow]")
            page.wait_for_function(
                "() => { const b = document.querySelector('.profile [data-social=follow]');"
                " return b && b.dataset.state !== 'unknown'; }"
            )
            before = {k: set(v) for k, v in server.ark.follows.items()}
            follow.click()
            chip = page.locator(".profile .social .confirm")
            chip.wait_for(timeout=5000)
            got = chip.locator("button").first.evaluate(
                "e => ({focused: e === document.activeElement, ring: getComputedStyle(e).outlineStyle})"
            )
            assert got["focused"] and got["ring"] != "none", got
            assert server.ark.follows == before  # nothing sent before the answer
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize("where", ["deck", "user"])
def test_a_follow_state_not_known_offers_follow_and_unfollow(server: Server, where: str) -> None:
    """When the gateway could not check whether the member follows someone, the deck
    page and the user page say so and offer both Follow and Unfollow; Unfollow asks first, and its
    answer leaves one button."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    server.ark.private.discard(43)
    url = f"{server.base}/decks/43" if where == "deck" else f"{server.base}/users/amy"
    scope = ".banner" if where == "deck" else ".profile"
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            def unknown(route) -> None:
                if route.request.method == "GET":
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body='{"ok":true,"self":false,"following":false,"unknown":true}',
                    )
                else:
                    route.continue_()

            page.route("**/social/api/users/*/follow", unknown)
            page.goto(url, wait_until="networkidle")
            unfollow = page.locator(f"{scope} [data-social=unfollow]")
            unfollow.wait_for(timeout=5000)
            assert page.locator(f"{scope} [data-social=follow]").is_visible()
            assert "could not check whether you follow amy" in page.locator(scope).inner_text()
            unfollow.click()
            chip = page.locator(f"{scope} .social .confirm")
            chip.wait_for(timeout=5000)
            assert server.ark.follows.get("alice", set()) == set()  # nothing sent before the answer
            chip.locator("button").first.click()
            page.wait_for_function(f"() => !document.querySelector('{scope} [data-social=unfollow]')")
            assert page.locator(f"{scope} [data-social=follow]").is_visible()
            assert "could not check" not in page.locator(scope).inner_text()
            assert errors == []
        finally:
            browser.close()


def test_a_down_vote_shows_as_voted_down_and_can_be_taken_back(server: Server) -> None:
    """A down vote cast through the assistant shows on the deck page as "Voted down",
    and the button takes it back ("Remove vote") rather than turning it into a like. The page adds
    no down button."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    server.ark.private.discard(43)
    server.ark.votes[("alice", 300043)] = 2
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/decks/43", wait_until="networkidle")
            vote = page.locator(".banner [data-social=vote]")
            assert vote.locator("span").inner_text() == "Voted down"
            assert vote.get_attribute("aria-pressed") == "false"
            kinds = page.eval_on_selector_all(".banner [data-social]", "es => es.map(e => e.dataset.social)")
            assert "vote" in kinds and not any("down" in k for k in kinds), kinds  # no down button
            vote.click()
            chip = page.locator(".banner .social .confirm")
            chip.wait_for(timeout=5000)
            assert "Remove your down vote?" in chip.inner_text()
            yes = chip.locator("button").first
            assert yes.inner_text() == "Remove vote"
            assert server.ark.votes[("alice", 300043)] == 2  # nothing sent before the answer
            yes.click()
            page.wait_for_function(
                "() => document.querySelector('.banner [data-social=vote] span').textContent === 'Like'"
            )
            assert ("alice", 300043) not in server.ark.votes
            assert errors == []
        finally:
            browser.close()


def test_with_writes_off_the_unknown_follows_unfollow_is_off_too(tmp_path, monkeypatch) -> None:
    """With Archidekt writes switched off, the Unfollow offered beside
    Follow (follow state not known) is off like Follow, with the same reason; the server refuses
    a follow sent anyway, and nothing reaches Archidekt."""
    from playwright.sync_api import sync_playwright

    from . import test_social_browser

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    real = test_social_browser.make_settings
    monkeypatch.setattr(
        test_social_browser, "make_settings", lambda *a, **k: real(*a, **{**k, "writes_enabled": False})
    )
    server = test_social_browser.Server(tmp_path)
    server.ark.private.discard(43)
    server.start()
    try:
        sid = server.sign_in_and_link()
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            try:
                ctx = browser.new_context(
                    viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True
                )
                ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
                page = ctx.new_page()
                page.route(
                    "**/social/api/users/*/follow",
                    lambda r: (
                        r.fulfill(
                            status=200,
                            content_type="application/json",
                            body='{"ok":true,"self":false,"following":false,"unknown":true}',
                        )
                        if r.request.method == "GET"
                        else r.continue_()
                    ),
                )
                for url, scope in (
                    (f"{server.base}/decks/43", ".banner"),
                    (f"{server.base}/users/amy", ".profile"),
                ):
                    page.goto(url, wait_until="networkidle")
                    unfollow = page.locator(f"{scope} [data-social=unfollow]")
                    unfollow.wait_for(timeout=5000)
                    follow = page.locator(f"{scope} [data-social=follow]")
                    assert follow.evaluate("e => e.disabled") and unfollow.evaluate("e => e.disabled"), url
                    assert unfollow.get_attribute("title") == follow.get_attribute("title"), url
                    note = page.locator(f"{scope} p.note")
                    assert note.get_attribute("role") == "status", url
                sent = page.evaluate(
                    "async () => { const t = document.querySelector('input[name=csrf]');"
                    " const r = await fetch('/social/api/users/78/follow', {method: 'POST',"
                    " headers: {'Content-Type': 'application/json', 'X-CSRF-Token': t ? t.value : ''},"
                    " body: JSON.stringify({on: false})});"
                    " return [r.status, await r.text()]; }"
                )
                assert sent[0] >= 400 and "switched off" in sent[1], sent
                writes = [c for c in server.ark.calls if c[0] != "GET" and "rest-auth" not in c[1]]
                assert writes == [], writes
            finally:
                browser.close()
    finally:
        server.stop()
