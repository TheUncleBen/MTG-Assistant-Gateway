"""The Forum pages (/forum, forum_page.py; parity release 2, mockup screens/60-forum.js): the
groups and their sections, a section's thread table with its search, order and Load more, a
thread with its first post and the deck page's comment panel over its replies, a locked thread
with no reply box, the plain words when Archidekt's forum pages change, and the links to the
area from the More menu and the account menu."""

from __future__ import annotations

import json
import re

import pytest

from mtg_gateway.forum import FORUM_CHANGED
from mtg_gateway.forum_page import ARCHIVED, LOCKED
from mtg_gateway.theme import esc

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED
from .test_decks_and_proxy import Browser, linked_user

stack_options = stack_with(SHIPPED)
SECTION = "/forum/Archidekt/General"


def _reply(ark, root: int, who: str, text: str, parent: int | None = None, cid: int | None = None) -> int:
    ark.next_comment_id += 1
    cid = cid or ark.next_comment_id
    ark.comments.setdefault(root, []).append(
        {
            "id": cid,
            "text": text,
            "owner": {"id": ark._user_id(who), "username": who, "avatar": None, "frame": None},
            "parent": parent or root,
            "originalPost": root,
            "createdAt": "2026-10-21T10:00:00Z",
            "editedAt": None,
            "points": 0,
            "userInput": 0,
            "childrenCount": 0,
            "children": {"count": 0, "results": []},
            "archived": False,
            "locked": False,
            "type": 4,
        }
    )
    return cid


@pytest.fixture
async def member(stack: Stack):
    await linked_user(stack)
    b = Browser(stack.h)
    await b.login("/forum")
    try:
        yield b
    finally:
        await b.aclose()


async def test_the_forum_page_lists_the_groups_and_their_sections(member: Browser, stack: Stack) -> None:
    r = await member.http.get("/forum")
    assert r.status_code == 200, r.text
    page = r.text
    assert "<h1>Forum</h1>" in page
    assert esc("Archidekt's forum: read and reply here; start a thread on Archidekt.") in page
    # the group, its sections as rows that open the section, their descriptions and counts
    assert "<h2>Archidekt" in page
    assert f"href='{SECTION}'" in page and "href='/forum/Archidekt/Deck%20Help'" in page
    assert "Talk about anything" in page and "Ask for help with a deck" in page
    assert "1,200" in page and ">300<" in page
    # the section chips at the top
    assert "class='ui-chip' href='/forum/Archidekt/Deck%20Help'" in page
    # starting a thread stays on Archidekt, with the one line why
    assert "href='https://archidekt.com/forum/thread/new'" in page and "New thread on Archidekt" in page
    assert esc("Archidekt asks you to prove you're human before starting a thread") in page
    # the whole page read the forum's front page once and nothing else from Archidekt
    assert stack.ark.site_pages == [("/forum", {})]


async def test_a_sections_threads_with_their_marks_search_order_and_paging(
    member: Browser, stack: Stack
) -> None:
    ark = stack.ark
    _reply(ark, 700002, "amy", "Sol Ring.")
    r = await member.http.get(SECTION)
    assert r.status_code == 200, r.text
    page = r.text
    assert "<h1>General</h1>" in page
    assert "href='/forum'>Forum</a>" in page  # the crumbs
    # one row a thread, each a link to the thread page, with its author, responses and points
    rows = re.findall(r"<a class='tr' href='/forum/thread/(\d+)'", page)
    assert rows == ["700001", "700002", "700003"]
    assert "Welcome to the forum" in page and "Best mana rocks?" in page
    assert "Sticky" in page  # 700001's mark
    assert page.count("amy") >= 3
    assert "<time class='ui-when'" in page  # the last post's date, the standard's one format
    # the order choice and the search box; the default order is the most recent
    assert "<option value='recent' selected>Most recent</option>" in page
    assert "<option value='points'>Points</option>" in page
    assert "name='q'" in page
    assert ark.site_pages[-1] == ("/forum/Archidekt/General", {"page": "1", "orderBy": "-updatedAt"})
    # the New thread button names the section
    assert "href='https://archidekt.com/forum/thread/new?section=General'" in page
    # three threads: no Load more
    assert "data-load-more" not in page
    # ordered by points and searched: the same page data, the site's own query
    r = await member.http.get(SECTION, params={"order": "points", "q": "mana"})
    assert r.status_code == 200
    assert re.findall(r"<a class='tr' href='/forum/thread/(\d+)'", r.text) == ["700002"]
    assert "<option value='points' selected>Points</option>" in r.text
    assert "value='mana'" in r.text
    assert ark.site_pages[-1] == (
        "/forum/Archidekt/General",
        {"page": "1", "orderBy": "-points", "q": "mana"},
    )
    # a bad order is the default, not an error
    r = await member.http.get(SECTION, params={"order": "silly"})
    assert r.status_code == 200 and "<option value='recent' selected>" in r.text


async def test_load_more_is_a_button_that_appends_the_next_page(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    for i in range(25):
        ark.forum_threads[710000 + i] = {
            "title": f"Thread {i}",
            "section": "General",
            "type": 0,
            "owner": "amy",
        }
    ark.forum_page_size = 20
    r = await member.http.get(SECTION)
    page = r.text
    assert "data-load-more" in page and f"href='{SECTION}?page=2'" in page
    assert "Load more" in page and "20 of 28" in page
    # with scripts the button fetches the next page's rows as JSON and appends them
    r = await member.http.get(SECTION, params={"page": "2"}, headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["ok"] is True and got["has_more"] is False
    assert got["html"].count("<a class='tr'") == 8
    assert got["label"] == "Load more (28 of 28 shown)" and got["done"] == "All 28 shown."
    # without scripts the same address is the page with that page's rows
    r = await member.http.get(SECTION, params={"page": "2"})
    assert r.status_code == 200 and r.text.count("<a class='tr'") == 8
    assert "data-load-more" not in r.text


async def test_a_thread_shows_its_first_post_and_the_comment_panel_over_its_replies(
    member: Browser, stack: Stack
) -> None:
    ark = stack.ark
    first = _reply(ark, 700002, "amy", "Arcane Signet.")
    _reply(ark, 700002, "alice", "And Sol Ring.", parent=first)
    r = await member.http.get("/forum/thread/700002")
    assert r.status_code == 200, r.text
    page = r.text
    assert "<h1>Best mana rocks?</h1>" in page
    assert "First post of Best mana rocks?" in page
    assert "started by amy" in page
    assert "href='https://archidekt.com/forum/thread/700002'" in page and "Open on Archidekt" in page
    # the deck page's comment panel, pointed at the thread: social.js reads data-deck
    assert "id='comments' data-deck='forum:700002'" in page
    assert "data-src='/social/api/decks/forum:700002/comments'" in page
    assert "data-writes='on'" in page
    # the replies are on the page without scripts, nested
    assert "Arcane Signet." in page and "And Sol Ring." in page
    assert page.index("Arcane Signet.") < page.index("And Sol Ring.")
    assert "<div class='replies'>" in page
    # and handed to the script as the thread it already read: no second read of the thread
    m = re.search(r"<script type='application/json' id='forum-thread'>(.*?)</script>", page, re.S)
    assert m, page
    pre = json.loads(m.group(1))
    assert [c["text"] for c in pre["comments"]] == ["Arcane Signet."]
    assert pre["comments"][0]["replies"][0]["text"] == "And Sol Ring."
    assert pre["count"] == 1 and pre["has_more"] is False and isinstance(pre["me"], int)
    assert ark.calls.count(("GET", "/api/comments/700002/")) == 1
    # the reply box, public under the member's name, posting through the same panel
    assert "<form class='newcomment'" in page and "Your reply" in page
    assert "Posted publicly on Archidekt under your Archidekt name, after you confirm." in page
    assert LOCKED not in page and ARCHIVED not in page
    # a thread by its link opens the same page
    r = await member.http.get("/forum/thread/https://archidekt.com/forum/thread/700002")
    assert r.status_code in (200, 303)


async def test_a_locked_thread_has_no_reply_box(member: Browser, stack: Stack) -> None:
    r = await member.http.get("/forum/thread/700003")
    assert r.status_code == 200, r.text
    assert "<h1>Old news</h1>" in r.text
    assert LOCKED in r.text
    assert "<form class='newcomment'" not in r.text
    assert "data-writes='off'" in r.text  # the panel offers no Reply, votes or edits either


async def test_a_changed_forum_page_is_said_plainly_with_a_way_to_a_thread(
    member: Browser, stack: Stack
) -> None:
    stack.ark.forum_page_changed = True
    for path in ("/forum", SECTION):
        r = await member.http.get(path)
        assert r.status_code == 200, (path, r.text)
        assert esc(FORUM_CHANGED) in r.text, path
        assert "Open a thread by its link or number" in r.text, path
        assert "<form class='fm-open'" in r.text and "action='/forum/thread'" in r.text, path
        assert "href='https://archidekt.com/forum'" in r.text and esc("Archidekt's forum") in r.text, path
    # the little form: a link or a number goes to the thread's page
    r = await member.http.get("/forum/thread", params={"thread": "https://archidekt.com/forum/thread/700001"})
    assert r.status_code == 303 and r.headers["location"] == "/forum/thread/700001"
    r = await member.http.get("/forum/thread", params={"thread": " 700002 "})
    assert r.status_code == 303 and r.headers["location"] == "/forum/thread/700002"
    # not a thread: the page says so and keeps what was typed
    r = await member.http.get("/forum/thread", params={"thread": "x<y"})
    assert r.status_code == 400 and esc("thread's number or its link") in r.text
    assert "value='x&lt;y'" in r.text
    # a thread still opens
    r = await member.http.get("/forum/thread/700001")
    assert r.status_code == 200 and "<h1>Welcome to the forum</h1>" in r.text


async def test_a_thread_that_is_not_there_and_a_bad_number(member: Browser, stack: Stack) -> None:
    r = await member.http.get("/forum/thread/700999")
    assert r.status_code == 404, r.text
    r = await member.http.get("/forum/thread/abc")
    assert r.status_code == 400, r.text
    assert esc("thread's number or its link") in r.text


async def test_the_forum_is_in_the_menus_and_needs_a_sign_in(stack: Stack, member: Browser) -> None:
    page = (await member.http.get("/forum")).text
    menu = page[page.index("<div class='menu'>") :]
    assert "<a href='/forum'>" in menu
    # the phone's More sheet and the rail list the same pages
    assert page.count("href='/forum'") >= 2
    assert "<a href='/forum' aria-current=page>" in page
    anon = Browser(stack.h)
    try:
        r = await anon.http.get("/forum/thread/700001")
        assert r.status_code == 302 and "/login" in r.headers["location"]
    finally:
        await anon.aclose()
