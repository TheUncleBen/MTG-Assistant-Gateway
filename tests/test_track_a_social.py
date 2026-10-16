"""Release 0.7.29, the social review (G14/Q47) and A29: every page of a deck's comment thread can be
read, voted on, replied to, edited and deleted, on the website and from the assistant alike, and a
vote right after the thread loaded costs one thread read and the vote, not three Archidekt calls."""

from __future__ import annotations

from typing import Any

from .conftest import Stack
from .test_account_actions import _press
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import call, linked_user, structured
from .test_social_layout import linked, post

ROOT = 300043


def _comment(cid: int, points: int, *, owner: str = "amy", uid: int = 78) -> dict[str, Any]:
    return {
        "id": cid,
        "text": f"Comment {cid}.",
        "owner": {"id": uid, "username": owner, "avatar": None, "frame": None},
        "parent": ROOT,
        "originalPost": ROOT,
        "createdAt": "2026-10-10T12:00:00Z",
        "editedAt": None,
        "points": points,
        "userInput": 0,
        "childrenCount": 0,
        "children": {"count": 0, "results": []},
        "archived": False,
        "locked": False,
        "type": 4,
    }


def _thread(stack: Stack, n: int = 5, size: int = 2) -> None:
    stack.ark.private.discard(43)
    stack.ark.comment_page_size = size
    # points 50, 40, 30, 20, 10: 555001 and 555002 on page 1, 555005 alone on page 3
    stack.ark.comments[ROOT] = [_comment(555000 + i, 60 - 10 * i) for i in range(1, n + 1)]


async def test_the_website_reads_and_votes_on_a_later_page(stack: Stack) -> None:
    b = await linked(stack)
    try:
        _thread(stack)
        first = (await b.http.get("/social/api/decks/43/comments")).json()
        assert [c["id"] for c in first["comments"]] == [555001, 555002] and first["has_more"]
        third = (await b.http.get("/social/api/decks/43/comments?page=3")).json()
        assert [c["id"] for c in third["comments"]] == [555005] and not third["has_more"]
        csrf = await b.csrf("/decks")  # the page load reads the deck list: not part of the vote
        before = len(stack.ark.calls)
        r = await post(b, "/social/api/decks/43/comments/555005/vote", {"vote": "up", "page": 3}, csrf=csrf)
        assert r.json() == {"ok": True, "comment": 555005, "vote": 1, "points": 11}, r.text
        assert stack.ark.votes[("alice", 555005)] == 1
        sent = stack.ark.calls[before:]
        # the thread page and the vote: the deck is not read again for its thread root
        assert len(sent) == 2, sent
        assert sent[0][0] == "GET" and "/comments/" in sent[0][1] and sent[1][0] == "PUT"
        # the old page-1-only lookup refused this comment
        r = await post(b, "/social/api/decks/43/comments/555005/vote", {"vote": "none"})
        assert r.json()["ok"] is False and "no longer on the page" in r.json()["message"]
    finally:
        await b.aclose()


async def test_a_comment_that_moved_to_the_next_page_is_still_found(stack: Stack) -> None:
    b = await linked(stack)
    try:
        _thread(stack)
        # 555004 was shown on page 2; votes since lifted it to page 1
        stack.ark.comments[ROOT][3]["points"] = 45
        r = await post(b, "/social/api/decks/43/comments/555004/vote", {"vote": "down", "page": 2})
        assert r.json()["ok"] is True and stack.ark.votes[("alice", 555004)] == 2
    finally:
        await b.aclose()


async def test_reply_and_delete_on_a_later_page(stack: Stack) -> None:
    b = await linked(stack)
    try:
        _thread(stack)
        r = await post(b, "/social/api/decks/43/comments", {"text": "Agreed.", "parent": 555005, "page": 3})
        assert r.status_code == 201, r.text
        assert r.json()["parent"] == 555005
        # alice's own comment, low on points, sits on the last page
        mine = _comment(555009, 1, owner="alice", uid=stack.ark._user_id("alice"))
        stack.ark.comments[ROOT].append(mine)
        r = await b.http.request(
            "DELETE",
            "/social/api/decks/43/comments/555009?page=3",
            headers={"X-CSRF-Token": await b.csrf("/decks")},
        )
        assert r.json().get("ok") is True, r.text
        assert all(c["id"] != 555009 for c in stack.ark.comments[ROOT])
    finally:
        await b.aclose()


async def test_the_assistant_can_vote_on_a_later_page_too(stack: Stack) -> None:
    """Claude and ChatGPT parity: get_deck_comments reads any page, propose_comment takes it."""
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    _thread(stack)
    read = structured(await call(h, token, "get_deck_comments", {"deck_id": "43", "page": 3}))
    assert read["page"] == 3 and [c["id"] for c in read["comments"]] == [555005]
    res = await call(
        h, token, "propose_comment", {"deck_id": "43", "action": "vote_up", "comment_id": 555005, "page": 3}
    )
    p = structured(res)
    assert p["ok"], p
    await _press(h, token, res)
    assert stack.ark.votes[("alice", 555005)] == 1
    bad = structured(await call(h, token, "get_deck_comments", {"deck_id": "43", "page": 0}))
    assert bad["ok"] is False and bad["error"] == "invalid"


def test_show_more_comments_then_vote_on_page_two_in_the_browser(tmp_path) -> None:
    """The deck page's comments: "Show more comments" adds the next page under the first, and a
    vote on a comment from it reaches Archidekt (A29). Phone, Fold and desktop; light and dark."""
    import os

    import pytest
    from playwright.sync_api import sync_playwright

    from .test_scan_browser import _chromium_path
    from .test_social_browser import Server

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    server = Server(tmp_path)
    server.ark.comment_page_size = 2
    server.ark.comments[ROOT] = [_comment(555000 + i, 60 - 10 * i) for i in range(1, 6)]
    server.start()
    shots = os.environ.get("MTG_SHOTS_DIR")
    try:
        sid = server.sign_in_and_link()
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            for name, size, mobile, scheme in (
                ("phone", (412, 915), True, "light"),
                ("fold", (673, 841), True, "dark"),
                ("desktop", (1366, 900), False, "dark"),
            ):
                server.ark.votes.clear()
                ctx = browser.new_context(
                    viewport={"width": size[0], "height": size[1]},
                    is_mobile=mobile,
                    has_touch=mobile,
                    color_scheme=scheme,
                )
                ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
                page = ctx.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.goto(f"{server.base}/decks/43#comments", wait_until="networkidle")
                page.locator("#comments").scroll_into_view_if_needed()
                page.wait_for_selector("#comments .cmt[data-id='555002']", timeout=10000)
                more = page.locator("#comments .more-comments button")
                assert more.inner_text() == "Show more comments"
                if shots:
                    more.scroll_into_view_if_needed()
                    page.screenshot(path=f"{shots}/a29-more-{name}-{scheme}.png")
                more.click()
                page.wait_for_selector("#comments .cmt[data-id='555004']", timeout=10000)
                more.click()
                page.wait_for_selector("#comments .cmt[data-id='555005']", timeout=10000)
                assert page.locator("#comments .more-comments").count() == 0
                assert page.locator("#comments .cmt").count() == 5  # each once, in order
                box = page.locator("#comments .cmt[data-id='555004']")
                box.locator("button.vote").first.click()
                box.locator(".confirm button").first.click()
                page.wait_for_function(
                    "() => document.querySelector(\"#comments .cmt[data-id='555004'] button.vote\")"
                    ".getAttribute('aria-pressed') === 'true'"
                )
                assert server.ark.votes == {("alice", 555004): 1}
                width = page.evaluate("document.documentElement.scrollWidth")
                assert width <= size[0], f"{name}: page scrolls sideways ({width}px)"
                if shots:
                    box.scroll_into_view_if_needed()
                    page.screenshot(path=f"{shots}/a29-comments-{name}-{scheme}.png")
                assert errors == [], errors
                ctx.close()
            browser.close()
    finally:
        server.stop()
