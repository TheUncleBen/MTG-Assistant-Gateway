"""Release 0.7.29: every page of a deck's comment thread can be
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
        # shown on page 1, it is found on the last page too (a new comment sits there)
        r = await post(b, "/social/api/decks/43/comments/555005/vote", {"vote": "none"})
        assert r.json()["ok"] is True and ("alice", 555005) not in stack.ark.votes
        # one that is in no page read is still refused
        r = await post(b, "/social/api/decks/43/comments/555099/vote", {"vote": "up"})
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
    vote on a comment from it reaches Archidekt. Phone, Fold and desktop; light and dark."""
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


def test_post_reply_edit_and_delete_a_comment_in_the_browser(tmp_path) -> None:
    """Comments on a phone: every comment write asks first (an edit too, which
    used to save at once), the count counts comments not replies, and a refused write says why."""
    import os

    import pytest
    from playwright.sync_api import sync_playwright

    from .test_scan_browser import _chromium_path
    from .test_social_browser import Server

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    server = Server(tmp_path)
    server.ark.comments[ROOT] = [_comment(555001, 3)]
    server.start()
    shots = os.environ.get("MTG_SHOTS_DIR")
    try:
        sid = server.sign_in_and_link()
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/decks/43#comments", wait_until="networkidle")
            page.locator("#comments").scroll_into_view_if_needed()
            page.wait_for_selector("#comments .cmt[data-id='555001']", timeout=10000)
            count = page.locator("#comments [data-count]")
            # a top-level comment: asked first, then posted, and counted
            page.fill("#comments form.newcomment textarea", "Nice deck.")
            page.click("#comments form.newcomment button[type=submit]")
            page.locator("#comments .confirmbar button.btn-primary").click()
            page.wait_for_function("() => document.querySelectorAll('#comments .thread > .cmt').length === 2")
            assert count.inner_text() == "2 comments"
            # a reply: posted under its comment, the count stays (it counts comments, not replies)
            page.locator("#comments .cmt[data-id='555001'] button", has_text="Reply").click()
            page.fill("#comments form.newcomment textarea", "Thanks!")
            page.click("#comments form.newcomment button[type=submit]")
            page.locator("#comments .confirmbar button.btn-primary").click()
            page.wait_for_selector("#comments .cmt[data-id='555001'] .replies .cmt", timeout=10000)
            assert count.inner_text() == "2 comments"
            # the new comment is the member's own at once: Edit and Delete, no vote buttons
            mine = page.locator("#comments .thread > .cmt").first
            assert mine.locator("button.vote").count() == 0
            mine.locator("button", has_text="Edit").click()
            mine.locator("form.editcomment textarea").fill("Nice deck, great curve.")
            mine.locator("form.editcomment button", has_text="Save").click()
            chip = mine.locator(".confirm")
            chip.wait_for(timeout=5000)
            assert "Change your public comment on Archidekt?" in chip.inner_text()
            from mtg_gateway.archidekt import rich_text

            sent = [c for c in server.ark.comments[ROOT] if rich_text(c["text"]) == "Nice deck."]
            assert sent, "nothing is changed before the confirmation"
            if shots:
                page.screenshot(path=f"{shots}/social-edit-confirm-phone-light.png")
            chip.locator("button", has_text="Change").click()
            page.wait_for_function(
                "() => [...document.querySelectorAll('#comments .cmt p')].some(p => p.textContent === "
                "'Nice deck, great curve.')"
            )
            assert any(rich_text(c["text"]) == "Nice deck, great curve." for c in server.ark.comments[ROOT])
            # delete: asked first, then gone
            mine.locator("button", has_text="Delete").click()
            mine.locator(".confirmbar button.btn-danger").click()
            page.wait_for_function("() => document.querySelectorAll('#comments .thread > .cmt').length === 1")
            assert count.inner_text() == "1 comment"
            width = page.evaluate("document.documentElement.scrollWidth")
            assert width <= 412, width
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


async def test_a_reply_says_when_archidekt_is_busy_instead_of_not_in_thread(
    stack: Stack, monkeypatch
) -> None:
    """The social review: a reply whose thread read fails says why, not "not in this deck's thread"."""
    from mtg_gateway.decks import DeckError
    from mtg_gateway.social import SocialService

    async def busy(*a, **k):
        raise DeckError("rate_limited", "Archidekt asked us to slow down; try again later.")

    b = await linked(stack)
    try:
        _thread(stack)
        monkeypatch.setattr(SocialService, "find_comment", busy)
        r = await post(b, "/social/api/decks/43/comments", {"text": "Agreed.", "parent": 555001})
        assert r.status_code == 503 and r.json()["error"] == "rate_limited", r.text
    finally:
        await b.aclose()


async def test_a_bookmark_already_so_says_so(stack: Stack) -> None:
    """The social review: like and follow said "already the case"; a bookmark now does too."""
    h = stack.h
    token = await linked_user(stack)
    stack.ark.private.discard(43)
    note = "(already the case; applying changes nothing)"
    stack.ark.bookmarks["alice"] = {43}
    p = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "bookmark"}))
    assert p["rows"][0]["text"].endswith(note), p
    p = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "unbookmark"}))
    assert not p["rows"][0]["text"].endswith(note), p


def test_with_writes_switched_off_the_deck_page_is_read_only(tmp_path, monkeypatch) -> None:
    """The social review: with MTG_WRITES_ENABLED off, the social buttons show their state but are
    disabled, with the reason on the page (a phone shows no tooltip), and the comment thread has no
    form, Reply, votes or edits. A user's page has its Follow off the same way. Phone and desktop,
    light and dark; the reason reads at 4.5:1."""
    import os

    import pytest
    from playwright.sync_api import sync_playwright

    from . import test_social_browser
    from .test_proposal_review_browser import CONTRAST
    from .test_scan_browser import _chromium_path

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    real = test_social_browser.make_settings
    monkeypatch.setattr(
        test_social_browser, "make_settings", lambda *a, **k: real(*a, **{**k, "writes_enabled": False})
    )
    server = test_social_browser.Server(tmp_path)
    server.ark.comments[ROOT] = [_comment(555001, 3)]
    server.start()
    shots = os.environ.get("MTG_SHOTS_DIR")
    try:
        sid = server.sign_in_and_link()
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            for name, size, mobile, scheme in (
                ("phone", (412, 915), True, "light"),
                ("phone", (412, 915), True, "dark"),
                ("desktop", (1366, 900), False, "dark"),
            ):
                where = f"{name}-{scheme}"
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
                like = page.locator("[data-social=vote]")
                assert like.is_disabled() and like.get_attribute("title") == (
                    "Archidekt writes are switched off on this gateway"
                )
                assert page.locator("[data-social=bookmark]").is_disabled()
                assert page.locator("[data-social=follow]").is_disabled()
                reason = page.locator(".banner .social-off")
                assert reason.is_visible(), where
                assert reason.inner_text() == (
                    "Archidekt writes are switched off on this gateway, so these buttons are off."
                )
                got = reason.evaluate(CONTRAST)
                assert got["ratio"] >= 4.5, (where, got)
                if shots:
                    reason.scroll_into_view_if_needed()
                    page.wait_for_timeout(300)  # let the scroll settle before the picture
                    page.screenshot(path=f"{shots}/a-writes-off-deck-{where}.png")
                page.locator("#comments").scroll_into_view_if_needed()
                page.wait_for_selector("#comments .cmt[data-id='555001']", timeout=10000)
                assert page.locator("#comments form.newcomment").count() == 0
                assert "can be read here but not posted" in page.locator("#comments").inner_text()
                assert page.locator("#comments .cmt button").count() == 0  # no Reply, votes or edits
                # a user's page: Follow is off too, and says why
                page.goto(f"{server.base}/users/amy", wait_until="networkidle")
                follow = page.locator("[data-social=follow]")
                assert follow.count() == 1 and follow.is_disabled(), where
                line = page.locator(".profile .social-off")
                assert line.is_visible() and line.inner_text().endswith(
                    "so following can't be changed here."
                ), where
                got = line.evaluate(CONTRAST)
                assert got["ratio"] >= 4.5, (where, got)
                follow.click(force=True)  # a disabled button sends nothing
                width = page.evaluate("document.documentElement.scrollWidth")
                assert width <= size[0], f"{where}: page scrolls sideways ({width}px)"
                if shots:
                    page.screenshot(path=f"{shots}/a-writes-off-user-{where}.png")
                assert server.ark.votes == {} and not any(server.ark.follows.values()), where
                assert errors == [], errors
                ctx.close()
            browser.close()
    finally:
        server.stop()


async def test_a_comment_just_posted_can_be_changed_and_deleted_on_a_long_thread(stack: Stack) -> None:
    """The page draws a new comment at the top (page 1), but with no
    points it is on the last page of a thread of 3 or more pages."""
    b = await linked(stack)
    try:
        _thread(stack)
        r = await post(b, "/social/api/decks/43/comments", {"text": "New here."})
        assert r.status_code == 201, r.text
        cid = r.json()["comment"]["id"] if "comment" in r.json() else r.json()["id"]
        third = (await b.http.get("/social/api/decks/43/comments?page=3")).json()
        assert cid in [c["id"] for c in third["comments"]], third
        csrf = await b.csrf("/decks")
        r = await b.http.patch(
            f"/social/api/decks/43/comments/{cid}",
            json={"text": "New here, changed.", "page": 1},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.json().get("ok") is True, r.text
        assert (
            next(c for c in stack.ark.comments[ROOT] if c["id"] == cid)["text"].count("New here, changed.")
            == 1
        )
        r = await b.http.request(
            "DELETE", f"/social/api/decks/43/comments/{cid}?page=1", headers={"X-CSRF-Token": csrf}
        )
        assert r.json().get("ok") is True, r.text
        assert all(c["id"] != cid for c in stack.ark.comments[ROOT])
    finally:
        await b.aclose()


async def test_a_follow_the_gateway_could_not_check_says_so(stack: Stack, monkeypatch) -> None:
    """The member's following list is read only so far (gentle on Archidekt). When it
    goes on past that and the person is not in the part read, the follow state is unknown: the page
    is told so (it offers Follow and Unfollow), and the assistant's row says it could not check."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    stack.ark.private.discard(43)
    client = h.app.state.gateway.social.client

    async def endless(token: str, user_id: str, page: int = 1) -> dict[str, Any]:
        rows = [{"id": 9000 + page, "username": f"someone{page}"}]
        return {"count": 999, "next": f"page={page + 1}", "results": rows}

    monkeypatch.setattr(client, "following", endless)
    b = await linked(stack)
    try:
        got = (await b.http.get("/social/api/users/78/follow")).json()
        assert got == {"ok": True, "self": False, "following": False, "unknown": True}, got
        # someone in the part read is known
        assert (await b.http.get("/social/api/users/9001/follow")).json()["following"] is True
    finally:
        await b.aclose()
    p = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "follow_owner"}))
    assert p["rows"][0]["text"].endswith("(the gateway could not check whether you already follow them)"), p
    assert ark.follows.get("alice", set()) == set()


def _long_following(stack: Stack, monkeypatch, *, amy_on: int | None) -> list[int]:
    """A following list of one person a page, going on past every cap, with amy (78) on page
    ``amy_on``; the pages read are recorded."""
    client = stack.h.app.state.gateway.social.client
    read: list[int] = []

    async def pages(token: str, user_id: str, page: int = 1) -> dict[str, Any]:
        read.append(page)
        row = {"id": 78, "username": "amy"} if page == amy_on else {"id": 9000 + page, "username": f"p{page}"}
        return {"count": 999, "next": f"page={page + 1}", "results": [row]}

    monkeypatch.setattr(client, "following", pages)
    return read


async def test_a_followed_person_past_the_first_pages_is_found_by_both_lookups(
    stack: Stack, monkeypatch
) -> None:
    """Someone on page 8 of the member's following list can be added as a collaborator, and a follow
    proposal for them says they are already followed: both read on until they are found, no further."""
    h = stack.h
    token = await linked_user(stack)
    read = _long_following(stack, monkeypatch, amy_on=8)
    p = structured(
        await call(h, token, "propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"})
    )
    assert p.get("ok") is not False and p["rows"][0]["text"].startswith("Let amy edit"), p
    assert read == list(range(1, 9)), read  # stopped at the page that has amy
    stack.ark.private.discard(43)  # amy's deck: its owner is the one followed
    p = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "follow_owner"}))
    assert p["rows"][0]["text"] == "Follow amy on Archidekt (already the case; applying changes nothing)", p
    assert read == list(range(1, 9)), read  # the remembered pages answered it


async def test_a_lookup_stops_at_its_cap_and_says_so(stack: Stack, monkeypatch) -> None:
    from mtg_gateway.social import FOLLOWING_LOOKUP_PAGES

    h = stack.h
    token = await linked_user(stack)
    read = _long_following(stack, monkeypatch, amy_on=None)
    p = structured(
        await call(h, token, "propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"})
    )
    assert p["ok"] is False and p["error"] == "not_found", p
    assert (
        f"amy isn't in the first {FOLLOWING_LOOKUP_PAGES} pages of the people you follow on Archidekt, "
        "which is as far as the gateway reads" in p["message"]
    ) and "if you don't follow them yet, follow them" in p["message"]
    assert read == list(range(1, FOLLOWING_LOOKUP_PAGES + 1)), read


async def test_a_name_not_followed_is_looked_up_once_until_the_list_expires(
    stack: Stack, monkeypatch
) -> None:
    """A lookup that read the following list to its cap (or its end) answers every lookup until
    the list expires: a retried misspelt collaborator and a follow proposal for someone not
    followed read no page again, and two lookups at once read each page once."""
    import asyncio

    from mtg_gateway.social import FOLLOWING_LOOKUP_PAGES, FOLLOWING_TTL

    h = stack.h
    token = await linked_user(stack)
    read = _long_following(stack, monkeypatch, amy_on=None)
    p = {}
    for _ in range(2):
        p = structured(
            await call(
                h, token, "propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"}
            )
        )
        assert p["ok"] is False and p["error"] == "not_found", p
    assert read == list(range(1, FOLLOWING_LOOKUP_PAGES + 1)), read  # the second call read nothing
    assert FOLLOWING_TTL == 120 and p["message"].endswith(
        "Someone you followed on archidekt.com very recently may not be in the list the gateway last read."
    )
    assert (
        f"amy isn't in the first {FOLLOWING_LOOKUP_PAGES} pages of the people you follow on Archidekt, "
        "which is as far as the gateway reads" in p["message"]
    ) and "if you don't follow them yet, follow them" in p["message"]
    stack.ark.private.discard(43)  # amy's deck: a follow of its owner, whom the list does not hold
    p = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "follow_owner"}))
    assert "could not check whether you already follow them" in p["rows"][0]["text"], p
    assert len(read) == FOLLOWING_LOOKUP_PAGES  # answered by the remembered list
    # once it expires, two lookups at once share one read
    social = h.app.state.gateway.social
    social._following.clear()
    social._following_next.clear()
    read.clear()
    found = await asyncio.gather(
        social.find_followed("user-1", "nobody"), social.find_followed("user-1", "p3")
    )
    assert found[1] == (9003, "p3") and found[0] is None
    assert read == list(range(1, FOLLOWING_LOOKUP_PAGES + 1)), read


def _slow_following(stack: Stack, monkeypatch) -> list[int]:
    """A following list of one person a page, going on past every cap and taking a moment a page;
    amy (78) is on page 1 once the member follows her on Archidekt. The pages read are recorded."""
    import asyncio

    client = stack.h.app.state.gateway.social.client
    read: list[int] = []

    async def pages(token: str, user_id: str, page: int = 1) -> dict[str, Any]:
        read.append(page)
        await asyncio.sleep(0.05)
        if page == 1 and 78 in stack.ark.follows.get("alice", set()):
            row = {"id": 78, "username": "amy"}
        else:
            row = {"id": 9000 + page, "username": f"p{page}"}
        return {"count": 999, "next": f"page={page + 1}", "results": [row]}

    monkeypatch.setattr(client, "following", pages)
    return read


async def test_a_deck_page_does_not_wait_behind_a_long_following_lookup(stack: Stack, monkeypatch) -> None:
    """A deck page's read of the following list (its first pages) takes its turn between the pages
    of an assistant's long lookup instead of waiting for all of them, and still reads no page twice."""
    import asyncio

    from mtg_gateway.social import FOLLOWING_LOOKUP_PAGES, FOLLOWING_PAGES

    await linked_user(stack)
    read = _slow_following(stack, monkeypatch)
    social = stack.h.app.state.gateway.social
    lookup = asyncio.create_task(social.find_followed("user-1", "nobody"))
    await asyncio.sleep(0.01)  # the lookup is reading page 1
    names = await social.following_names("user-1")
    assert len(read) <= FOLLOWING_PAGES + 1 and not lookup.done(), read
    assert len(names) >= FOLLOWING_PAGES - 1, names
    assert await lookup is None
    assert read == list(range(1, FOLLOWING_LOOKUP_PAGES + 1)), read


async def test_a_follow_during_a_following_read_is_not_undone(stack: Stack, monkeypatch) -> None:
    """A follow sent through the gateway while a following-list read is under way makes that read
    store nothing it read before the follow: the next lookup finds the person just followed."""
    import asyncio

    await linked_user(stack)
    read = _slow_following(stack, monkeypatch)
    social = stack.h.app.state.gateway.social
    lookup = asyncio.create_task(social.find_followed("user-1", "nobody"))
    while len(read) < 2:  # page 1 was read without amy
        await asyncio.sleep(0.01)
    await social.follow("user-1", 78, True)
    await lookup
    assert await social.find_followed("user-1", "amy") == (78, "amy")


async def test_a_name_missing_from_a_list_read_to_its_end_says_follow_them_first(
    stack: Stack, monkeypatch
) -> None:
    """When the whole following list was read and the name isn't in it, the refusal says plainly
    that they aren't followed (the capped text, which can't say that, is only for a list cut short)."""
    client = stack.h.app.state.gateway.social.client

    async def pages(token: str, user_id: str, page: int = 1) -> dict[str, Any]:
        return {"count": 1, "next": None, "results": [{"id": 9001, "username": "p1"}]}

    monkeypatch.setattr(client, "following", pages)
    token = await linked_user(stack)
    p = structured(
        await call(
            stack.h, token, "propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"}
        )
    )
    assert p["ok"] is False and p["error"] == "not_found", p
    assert p["message"].startswith("amy is not among the people you follow on Archidekt. "), p
    assert "as far as the gateway reads" not in p["message"]
