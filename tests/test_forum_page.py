"""The Forum pages (/forum, forum_page.py; parity release 2, mockup screens/60-forum.js): the
groups and their sections, a section's thread table with its search, order and Load more, a
thread with its first post and the deck page's comment panel over its replies, a locked thread
with no reply box, the plain words when Archidekt's forum pages change, and the links to the
area from the More menu and the account menu."""

from __future__ import annotations

import json
import re
from html import unescape as html_unescape

import pytest

from mtg_gateway.forum import FORUM_CHANGED
from mtg_gateway.forum_page import ARCHIVED, LOCKED
from mtg_gateway.theme import esc

from .conftest import Stack, stack_with
from .test_account_actions import _browser_apply
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
    # no section chips above the tables: the rows name every section once (0.7.44 L13)
    assert "ui-chip" not in page
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
    # the crumbs: every one a link, the group's to its list on the front page
    assert "href='/forum'>Forum</a>" in page
    assert "href='/forum#group-Archidekt'>Archidekt</a>" in page
    assert "href='/forum/Archidekt/General'>General</a>" in page
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
    # the front page carries the anchor the group crumb links to
    front = (await member.http.get("/forum")).text
    assert "<div class='fm-group' id='group-Archidekt'>" in front
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
    assert "<form class='newcomment' data-forum='1' data-me-name='alice'>" in page and "Your reply" in page
    assert esc("Public under “alice”. An assistant's reply always waits for your press.") in page
    # the text style buttons above the box, the one orange button under it
    assert "role='toolbar' aria-label='Text style'" in page
    assert page.count("data-mark=") == 3 and "data-mark='bold'" in page and "data-mark='link'" in page
    assert "class='btn btn-primary'" in page and "Post reply" in page
    # the first post carries its ⋮ and who wrote it; the Report comment box is on the page, hidden
    assert "class='fm-post' id='post-700002' data-id='700002' data-owner='amy' tabindex='0'" in page
    assert "aria-label='Menu for amy&#39;s post'" in page
    assert "id='reportcomment'" in page and "action='/forum/thread/700002/report'" in page
    assert "id='fm-contest'" not in page  # not a contest thread
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
    # Forum is a row on the Account page (Your account), the same on every screen
    hub = (await member.http.get("/account")).text
    assert "<a class='yrow' href='/forum'>" in hub
    anon = Browser(stack.h)
    try:
        r = await anon.http.get("/forum/thread/700001")
        assert r.status_code == 302 and "/login" in r.headers["location"]
    finally:
        await anon.aclose()


# -- the UI pass: the section column, the contest strip, Report comment… and the text marks ---------


def _contest(ark, tid: int = 700007, **more) -> None:
    ark.forum_threads[tid] = {
        "title": "October deck contest",
        "section": "General",
        "type": 7,
        "owner": "amy",
        **more,
    }


async def test_the_thread_table_has_a_section_column_and_two_line_rows(member: Browser) -> None:
    r = await member.http.get(SECTION)
    assert r.status_code == 200, r.text
    page = r.text
    # the columns as the mockup lists them; the Points column of before is gone
    head = re.search(r"<div class='tr th'.*?</div>", page, re.S)
    assert head, page
    cols = re.findall(r"<span[^>]*>(.*?)</span>", head.group(0))
    assert [re.sub(r"<[^>]+>", "", c).strip() for c in cols] == [
        "Thread",
        "Started by",
        "Replies",
        "Last activity",
    ]
    # the section is the page's own (its heading names it; Archidekt's rows carry no section of
    # their own), so no row repeats it as a chip
    assert "fm-section" not in page
    row = re.search(r"<a class='tr' href='/forum/thread/700002'.*?</a>", page, re.S).group(0)
    assert "General" not in row
    # the Last activity column: who and when on one line
    last = re.search(r"<span class='fm-last sm-hide'>(.*?)</span></a>", row, re.S).group(1)
    assert last.startswith("<span class='fm-by'>amy</span> · <time class='ui-when'"), last


async def test_a_contest_thread_has_its_strip_the_entries_and_the_enter_and_withdraw_boxes(
    member: Browser, stack: Stack
) -> None:
    ark = stack.ark
    _contest(ark)
    ark.account.entries[700007] = [
        {
            "id": 1,
            "deck": {"id": 77, "name": "Amy's pile"},
            "owner": {"id": ark._user_id("amy"), "username": "amy"},
        }
    ]
    r = await member.http.get("/forum/thread/700007")
    assert r.status_code == 200, r.text
    page = html_unescape(r.text)
    assert "id='fm-contest'" in page and "Contest · 1 deck entered" in page
    assert "Enter a deck…" in page and "data-box-open='enter'" in page
    assert "href='/forum/thread/700007/enter'" in page
    # the entries fold: the deck, by whom; no Reviewed mark when Archidekt's row says nothing of it
    assert "Entries (1)" in page and "Amy's pile" in page and "by amy" in page
    assert "Reviewed" not in page and "Not yet reviewed" not in page
    # the boxes are on the page, hidden; the Enter box reads the decks when it opens
    assert (
        "id='enter'" in page and "id='fm-enter-f'" in page and "data-src='/api/decks/mine?packages=1'" in page
    )
    assert "Summary of the deck (required)" in page and "Shown on the contest page." in page
    assert "Archidekt replaces the deck's description with this summary." in page
    assert "Review the entry" in page
    assert "id='withdraw'" in page and "id='fm-withdraw-f'" in page
    # the entries were read once, the thread once
    assert ark.calls.count(("GET", "/api/comments/contestSubmission/decks/700007/")) == 1
    # the old addresses draw the boxes open, with the deck list in the page (no scripts needed)
    r = await member.http.get("/forum/thread/700007/enter")
    assert r.status_code == 200 and "id='enter'" in r.text and "data-open" in r.text
    assert "Enter a deck in “October deck contest”" in html_unescape(r.text)
    # entering: the checks, then the review page, where the member applies it
    csrf = await member.csrf("/forum/thread/700007")
    bad = await member.http.post(
        "/forum/thread/700007/contest",
        data={"csrf": csrf, "entry_action": "enter", "deck": "", "summary": "x"},
    )
    assert bad.status_code == 400 and "Pick a deck first." in bad.text
    bad = await member.http.post(
        "/forum/thread/700007/contest",
        data={"csrf": csrf, "entry_action": "enter", "deck": "42", "summary": " "},
    )
    assert bad.status_code == 400 and "A summary is required." in bad.text
    r = await member.http.post(
        "/forum/thread/700007/contest",
        data={"csrf": csrf, "entry_action": "enter", "deck": "42", "summary": "Simic ramp"},
    )
    assert r.status_code == 303, r.text
    pid = r.headers["location"].rpartition("/")[2]
    assert r.headers["location"] == f"/proposals/{pid}"
    assert ark.account.entries[700007] and len(ark.account.entries[700007]) == 1  # nothing sent yet
    done = await _browser_apply(stack.h, pid)
    assert done["state"] == "applied", done
    assert [e["deck"]["id"] for e in ark.account.entries[700007]] == [77, 42]
    # entered: the strip shows the member's entry and Withdraw…; the entries fold marks You
    r = await member.http.get("/forum/thread/700007")
    page = html_unescape(r.text)
    assert "Your entry:" in page and "Sample Commander Deck" in page and "Withdraw…" in page
    assert "Enter a deck…" not in page
    assert "Contest · 2 decks entered" in page
    assert ">You<" in page
    r = await member.http.get("/forum/thread/700007/withdraw")
    assert (
        "Withdraw “Sample Commander Deck” from this contest?" in html_unescape(r.text)
        and "data-open" in r.text
    )
    r = await member.http.post(
        "/forum/thread/700007/contest", data={"csrf": csrf, "entry_action": "withdraw", "deck": "42"}
    )
    assert r.status_code == 303
    done = await _browser_apply(stack.h, r.headers["location"].rpartition("/")[2])
    assert done["state"] == "applied", done
    assert [e["deck"]["id"] for e in ark.account.entries[700007]] == [77]


async def test_a_contest_whose_entries_cannot_be_read_says_so_and_a_locked_one_offers_nothing(
    member: Browser, stack: Stack
) -> None:
    ark = stack.ark
    _contest(ark)
    ark.account.fail_entries = True
    r = await member.http.get("/forum/thread/700007")
    assert r.status_code == 200
    assert "The entries could not be read from Archidekt right now." in r.text
    _contest(ark, tid=700008, locked=True)
    r = await member.http.get("/forum/thread/700008")
    assert r.status_code == 200 and LOCKED in r.text
    assert "data-box-open='enter'" not in r.text and "id='fm-enter-f'" not in r.text


async def test_a_reply_and_the_first_post_are_reported_through_the_review_page(
    member: Browser, stack: Stack
) -> None:
    ark = stack.ark
    cid = _reply(ark, 700002, "amy", "Buy my cards at spam.example")
    csrf = await member.csrf("/forum/thread/700002")
    r = await member.http.post(
        "/forum/thread/700002/report",
        data={
            "csrf": csrf,
            "report_kind": "comment",
            "comment_id": str(cid),
            "page": "1",
            "message": "Spam.",
        },
    )
    assert r.status_code == 303, r.text
    pid = r.headers["location"].rpartition("/")[2]
    shown = (await member.http.get(f"/api/v1/proposals/{pid}")).json()
    assert "amy's comment" in str(shown) and "Best mana rocks?" in str(shown), shown
    done = await _browser_apply(stack.h, pid)
    assert done["state"] == "applied", done
    sent = ark.account.reports[-1]
    assert sent["by"] == "alice" and "Spam." in str(sent) and str(cid) in str(sent)
    # the thread's first post is a comment too (the thread's root)
    r = await member.http.post(
        "/forum/thread/700002/report",
        data={
            "csrf": csrf,
            "report_kind": "comment",
            "comment_id": "700002",
            "page": "1",
            "message": "Off topic.",
        },
    )
    assert r.status_code == 303, r.text
    done = await _browser_apply(stack.h, r.headers["location"].rpartition("/")[2])
    assert done["state"] == "applied", done
    assert "Off topic." in str(ark.account.reports[-1])
    # a report needs a message; a card cannot be reported from a thread
    bad = await member.http.post(
        "/forum/thread/700002/report",
        data={"csrf": csrf, "report_kind": "comment", "comment_id": str(cid), "page": "1", "message": " "},
    )
    assert bad.status_code == 400 and "id='reportcomment'" in bad.text and "data-open" in bad.text
    bad = await member.http.post(
        "/forum/thread/700002/report",
        data={"csrf": csrf, "report_kind": "card", "card": "Sol Ring", "message": "x"},
    )
    assert bad.status_code == 400


def test_the_text_marks_become_archidekts_formatting() -> None:
    from mtg_gateway.formatting import has_marks, marks_doc, marks_text

    assert not has_marks("plain words") and marks_doc("plain words") is None
    text = "Try **Sol Ring** and _Arcane Signet_: [the list](https://archidekt.com/decks/42)"
    assert has_marks(text)
    assert marks_text(text) == "Try Sol Ring and Arcane Signet: the list"
    doc = json.loads(marks_doc(text))
    ops = doc["ops"]
    assert ops[1] == {"insert": "Sol Ring", "attributes": {"bold": True}}
    assert ops[3] == {"insert": "Arcane Signet", "attributes": {"italic": True}}
    assert ops[5] == {"insert": "the list", "attributes": {"link": "https://archidekt.com/decks/42"}}
    assert ops[-1]["insert"].endswith("\n")
    # a link that is not a web address stays as typed
    assert marks_doc("[bad](javascript:1)") is None
    assert marks_text("[bad](javascript:1)") == "[bad](javascript:1)"


async def test_a_reply_with_marks_is_posted_as_archidekts_formatting(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    csrf = await member.csrf("/forum/thread/700002")
    r = await member.http.post(
        "/social/api/decks/forum:700002/comments",
        json={"text": "Run **Sol Ring**, see [this](https://archidekt.com/decks/42).", "marks": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["ok"] is True, r.text
    posted = ark.comments[700002][-1]["text"]
    doc = json.loads(posted)
    assert {"insert": "Sol Ring", "attributes": {"bold": True}} in doc["ops"]
    assert any(op.get("attributes", {}).get("link") == "https://archidekt.com/decks/42" for op in doc["ops"])
    # and without the flag the marks are plain words, as typed
    r = await member.http.post(
        "/social/api/decks/forum:700002/comments",
        json={"text": "Not **bold** here."},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.json()["ok"] is True, r.text
    assert "**bold**" in ark.comments[700002][-1]["text"]


async def test_reply_counts_are_worded_right(member: Browser, stack: Stack) -> None:
    from mtg_gateway.forum_page import _plural

    assert _plural(0, "reply") == "0 replies" and _plural(1, "reply") == "1 reply"
    assert _plural(2, "thread") == "2 threads" and _plural(1200, "post") == "1,200 posts"
    r = await member.http.get("/forum/thread/700003")
    assert "0 replies" in r.text and "replys" not in r.text
    r = await member.http.get(SECTION)
    assert "replys" not in r.text and "0 replies" in r.text


async def test_a_reply_cannot_break_out_of_the_threads_json_block(member: Browser, stack: Stack) -> None:
    """Security review F-01: the thread handed to the script sits in a <script type=application/json>
    block; a reply holding "<!--<script>" must not open a script that swallows the page."""
    _reply(stack.ark, 700002, "amy", "look <!--<script>alert(1)</script> at this </script><b>x</b>")
    r = await member.http.get("/forum/thread/700002")
    assert r.status_code == 200
    m = re.search(r"<script type='application/json' id='forum-thread'>(.*?)</script>", r.text, re.S)
    assert m, r.text
    block = m.group(1)
    assert "<" not in block, block  # every < is <
    assert "alert(1)" in json.loads(block)["comments"][-1]["text"]
    assert "<form class='newcomment'" in r.text  # the rest of the page is still there


async def test_forum_is_on_the_account_page_and_off_the_bars(member: Browser) -> None:
    """The navigation is four places and More (Ben, 2026-10-25): Forum is a row on the Account page
    the picture opens, not a tab, a rail place or a More item."""
    page = (await member.http.get("/forum")).text
    header = page[page.index("<header") : page.index("</header>")]
    tabbar = re.search(r"<nav class='tabbar'.*?</details></nav>", page, re.S)
    assert tabbar and "href='/forum'" not in header + tabbar.group(0)
    hub = (await member.http.get("/account")).text
    assert "<a class='yrow' href='/forum'>" in hub


async def test_a_forum_reply_is_voted_down_and_back_through_the_comment_routes(
    member: Browser, stack: Stack
) -> None:
    from .test_social_layout import post

    ark = stack.ark
    cid = _reply(ark, 700002, "amy", "Arcane Signet.")
    path = f"/social/api/decks/forum:700002/comments/{cid}/vote"
    r = await post(member, path, {"vote": "down"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "comment": cid, "vote": 2, "points": -1}
    assert ark.votes[("alice", cid)] == 2
    assert ark.vote_calls[-1] == {"path": f"/api/comments/vote/{cid}/", "up": False, "remove": False}
    got = (await member.http.get("/social/api/decks/forum:700002/comments")).json()["comments"][0]
    assert got["points"] == -1 and got["user_vote"] == 2
    # from down to up in one press, then taken back
    r = await post(member, path, {"vote": "up"})
    assert r.json()["vote"] == 1 and r.json()["points"] == 1 and ark.votes[("alice", cid)] == 1
    r = await post(member, path, {"vote": "none"})
    assert r.json() == {"ok": True, "comment": cid, "vote": 0, "points": 0}
    assert ("alice", cid) not in ark.votes
    # the same vote again sends nothing
    sent = len(ark.vote_calls)
    r = await post(member, path, {"vote": "none"})
    assert r.status_code == 200 and len(ark.vote_calls) == sent
    # a reply of the member's own, one outside the thread and a bad vote are refused
    mine = _reply(ark, 700002, "alice", "Mine.")
    r = await post(member, f"/social/api/decks/forum:700002/comments/{mine}/vote", {"vote": "down"})
    assert r.status_code == 400
    r = await post(member, "/social/api/decks/forum:700002/comments/999999/vote", {"vote": "down"})
    assert r.status_code == 404
    r = await post(member, path, {"vote": "sideways"})
    assert r.status_code == 400


def test_group_anchors_differ_for_every_group_name() -> None:
    from mtg_gateway.forum_page import group_anchor

    names = ["Deck Help", "Deck-Help", "deck help", "Deck_Help", "Deck  Help", "Déck Help", "", "-"]
    anchors = [group_anchor(n) for n in names]
    assert len(set(anchors)) == len(names), anchors
    assert all(re.fullmatch(r"group-[A-Za-z0-9_-]*", a) for a in anchors), anchors
    assert group_anchor("Archidekt") == "group-Archidekt"


async def test_a_comment_vote_without_the_page_token_is_refused(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    cid = _reply(ark, 700002, "amy", "Arcane Signet.")
    sent = len(ark.vote_calls)
    r = await member.http.post(
        f"/social/api/decks/forum:700002/comments/{cid}/vote",
        content=json.dumps({"vote": "down"}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 403 and r.json()["error"] == "csrf", r.text
    assert len(ark.vote_calls) == sent and ("alice", cid) not in ark.votes


async def test_a_trail_is_only_archidekts_own_names_never_a_members_url(stack: Stack) -> None:
    """Security review 0.7.44 S-02: a thread's trail is stored only under a group and section that
    the forum's own section listing names, in Archidekt's spelling. One member's made-up group in
    the URL never becomes another member's trail."""
    from mtg_gateway import forum

    from .test_access_control import AMY, browser_for

    h = stack.h
    forum._IN_SECTION.clear()
    alice_user = dict(h.idp.user)
    amy = await browser_for(h, AMY, "amy", "pw-amy")
    assert (await amy.http.get("/forum")).status_code == 200  # the listing has been read
    r = await amy.http.get("/forum/Made%20up/General")
    assert r.status_code == 200, r.text  # the fake serves General's threads under any group name
    assert forum.in_section(700001) is None
    await amy.aclose()

    h.idp.user = alice_user
    alice = await browser_for(h, None, "alice", "pw-alice")
    page = (await alice.http.get("/forum/thread/700001")).text
    crumbs = re.search(r"<nav class='crumbs'.*?</nav>", page).group(0)
    assert "Made up" not in crumbs and crumbs.count("<a ") == 1  # the plain Forum crumb
    # a real section, typed in another case: stored in Archidekt's own spelling
    assert (await alice.http.get("/forum/archidekt/General")).status_code == 200
    assert forum.in_section(700001) == ("Archidekt", "General")
    page = (await alice.http.get("/forum/thread/700001")).text
    crumbs = re.search(r"<nav class='crumbs'.*?</nav>", page).group(0)
    assert "href='/forum#group-Archidekt'>Archidekt</a>" in crumbs
    assert "href='/forum/Archidekt/General'>General</a>" in crumbs
    await alice.aclose()
    forum._IN_SECTION.clear()
