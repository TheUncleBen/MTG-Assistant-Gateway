"""The forum (parity release 2, Ben's choice (a), 23 Oct): sections and thread lists read as
Archidekt's own pages read them, a thread through the comment API, and a reply as a comment that
waits for the member's press. When the page data changes, the gateway says so plainly."""

from __future__ import annotations

from .conftest import Stack
from .test_account_actions import _browser_apply
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .track_a_helpers import account_call

GENERAL = {"group": "Archidekt", "section": "General"}


async def test_sections_and_a_sections_threads_come_from_the_forums_own_pages(stack: Stack) -> None:
    token = await linked_user(stack)
    got = structured(await call(stack.h, token, "get_forum", {}))
    assert got["ok"] is True, got
    assert got["groups"][0]["group"] == "Archidekt"
    assert [s["section"] for s in got["groups"][0]["sections"]] == ["General", "Deck Help"]
    listed = structured(await call(stack.h, token, "get_forum", {**GENERAL, "order": "points"}))
    assert listed["ok"] is True, listed
    assert [t["id"] for t in listed["threads"]] == [700001, 700002, 700003]
    assert listed["threads"][0]["type"] == "sticky" and listed["pages"] == 1
    # the site's own page and query: the section page, 1st page, ordered by points
    assert stack.ark.site_pages[-1] == ("/forum/Archidekt/General", {"page": "1", "orderBy": "-points"})
    found = structured(await call(stack.h, token, "get_forum", {**GENERAL, "search": "mana"}))
    assert [t["title"] for t in found["threads"]] == ["Best mana rocks?"]


async def test_a_thread_reads_its_first_post_and_replies(stack: Stack) -> None:
    token = await linked_user(stack)
    t = structured(
        await call(stack.h, token, "get_forum", {"thread_id": "https://archidekt.com/forum/thread/700002"})
    )
    assert t["ok"] is True, t
    assert t["title"] == "Best mana rocks?" and t["text"].startswith("First post of Best mana rocks?")
    assert t["replies"] == [] and t["locked"] is False


async def test_a_reply_waits_for_the_members_press_and_lands_in_the_thread(stack: Stack) -> None:
    token = await linked_user(stack)
    p = structured(
        await account_call(
            stack.h,
            token,
            "propose_comment",
            {"thread_id": "700002", "action": "post", "text": "Arcane Signet, every time."},
        )
    )
    assert p["ok"] is True, p
    assert p["risk"] == "consent" and "Post a public reply in the forum thread" in p["diff"], p
    assert stack.ark.comments.get(700002) is None  # nothing sent by a proposal
    no = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert no["ok"] is False  # an assistant never applies it
    done = await _browser_apply(stack.h, p["proposal_id"])
    assert done["state"] == "applied", done
    assert [c["text"] for c in stack.ark.comments[700002]] != []
    t = structured(await call(stack.h, token, "get_forum", {"thread_id": "700002"}))
    assert [r["text"] for r in t["replies"]] == ["Arcane Signet, every time."]


async def test_a_locked_thread_takes_no_reply(stack: Stack) -> None:
    token = await linked_user(stack)
    p = structured(
        await account_call(
            stack.h, token, "propose_comment", {"thread_id": "700003", "action": "post", "text": "Hi"}
        )
    )
    assert p["ok"] is False and "locked or archived" in p["message"], p


async def test_a_changed_forum_page_is_said_plainly_and_a_thread_still_opens(stack: Stack) -> None:
    token = await linked_user(stack)
    stack.ark.forum_page_changed = True
    got = structured(await call(stack.h, token, "get_forum", {}))
    assert got["ok"] is False and got["message"].startswith("Archidekt changed how its forum pages load"), got
    t = structured(await call(stack.h, token, "get_forum", {"thread_id": "700001"}))
    assert t["ok"] is True and t["title"] == "Welcome to the forum"


async def test_the_review_page_names_the_forum_thread(stack: Stack) -> None:
    token = await linked_user(stack)
    p = structured(
        await account_call(
            stack.h, token, "propose_comment", {"thread_id": "700002", "action": "post", "text": "Hi"}
        )
    )
    b = Browser(stack.h)
    try:
        await b.login(f"/proposals/{p['proposal_id']}")
        page = await b.http.get(f"/proposals/{p['proposal_id']}")
    finally:
        await b.aclose()
    assert page.status_code == 200
    assert "<a href='/forum/thread/700002'>Best mana rocks?</a>" in page.text


async def test_the_thread_page_reads_and_posts_through_the_deck_comment_routes(stack: Stack) -> None:
    """The forum thread page reuses the deck page's comment box: its routes take "forum:<n>" for a
    thread, and a History or Changes row naming the thread opens the thread page."""
    await linked_user(stack)
    b = Browser(stack.h)
    try:
        await b.login("/account")
        r = await b.http.get("/social/api/decks/forum:700002/comments")
        assert r.status_code == 200 and r.json()["root"] == 700002, r.text
        r = await b.http.get("/decks/forum:700002", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/forum/thread/700002"
        r = await b.http.get("/social/api/decks/forum:x/comments")
        assert r.status_code in (400, 404)
    finally:
        await b.aclose()


async def test_a_dot_name_never_leaves_the_forum(stack: Stack) -> None:
    """Security review F-03: a group or section named "." or ".." is refused, so the page read
    stays under /forum/."""
    token = await linked_user(stack)
    before = len(stack.ark.site_pages)
    for bad in ({"group": "..", "section": "users"}, {"group": "Archidekt", "section": "."}):
        got = structured(await call(stack.h, token, "get_forum", bad))
        assert got["ok"] is False, got
    assert len(stack.ark.site_pages) == before


async def test_an_oversized_forum_page_is_not_read_whole() -> None:
    """Security review F-04: the 3 MB page limit stops the read as it goes, not after it."""
    import httpx

    from mtg_gateway.archidekt import SITE_PAGE_MAX, ArchidektClient, ArchidektError, Pacer

    sent = 0

    class Endless(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal sent
            while sent < SITE_PAGE_MAX * 4:
                sent += 65536
                yield b"x" * 65536

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Endless(), headers={"content-type": "text/html"})

    client = ArchidektClient(
        "https://archidekt.com/api",
        "test",
        Pacer(0.0),
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        await client.site_page_data("/forum")
        raise AssertionError("an oversized page was read")
    except ArchidektError as exc:
        assert exc.kind == "changed"
    assert sent <= SITE_PAGE_MAX + 2 * 65536
