"""Release 0.6.4: Archidekt's social actions (like, bookmark, follow, comments) as browser-only
routes with the member's own session, and the adaptive navigation (bottom bar, rail, desktop)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import Client

from mtg_gateway.archidekt import ArchidektClient, Pacer
from mtg_gateway.mf_proxy import MysticForgeProxy
from mtg_gateway.theme import CSS

from .conftest import FakeIdP, Harness, make_settings, running, sse_json
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, fake_mystic_forge, mcp_token

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
APP_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 16) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.6.4"}


class Stack:
    def __init__(self, h: Harness, ark: FakeArchidekt):
        self.h = h
        self.ark = ark


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(tmp_path, writes_enabled=True, archidekt_base="https://ark.test/api")
    client = ArchidektClient(settings.archidekt_base, "test-agent", Pacer(0.0), http=ark.client())
    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(fake_mystic_forge()))
    async with running(Harness(settings, idp, archidekt=client, mf_proxy=proxy)) as h:
        yield Stack(h, ark)


async def linked(stack: Stack, user: str = "alice", pw: str = "pw-alice") -> Browser:
    b = Browser(stack.h)
    await b.login("/account")
    r = await b.link(user, pw)
    assert r.status_code == 303, r.text
    return b


async def post(b: Browser, path: str, body: dict, *, csrf: str | None = None):
    return await b.http.post(
        path,
        content=json.dumps(body),
        headers={
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf if csrf is not None else await b.csrf("/decks"),
        },
    )


# -- the deck page offers the actions ------------------------------------------------------------
async def test_deck_page_shows_like_bookmark_follow_and_comments(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await b.http.get("/decks/42", headers=NAV)  # alice's own deck
        assert r.status_code == 200
        assert "data-social='vote'" in r.text and "data-social='bookmark'" in r.text
        assert "data-social='follow'" not in r.text  # nobody follows themselves
        assert "id='comments'" in r.text and "Posted publicly on Archidekt" in r.text
        # amy's public deck: follow its owner, like count from Archidekt's points
        stack.ark.private.discard(43)
        r = await b.http.get("/decks/43", headers=NAV)
        assert r.status_code == 200
        assert "data-social='follow' data-user='78' data-name='amy'" in r.text
        assert "<b class='n'>0</b><span>Like</span>" in r.text
        # the user page carries a Follow button too, but not on your own profile
        r = await b.http.get("/users/amy", headers=NAV)
        assert "data-social='follow' data-user='78'" in r.text
        r = await b.http.get("/users/alice", headers=NAV)
        assert "data-social='follow'" not in r.text
    finally:
        await b.aclose()


async def test_like_bookmark_follow_write_to_archidekt_under_the_member(stack: Stack) -> None:
    b = await linked(stack)
    try:
        stack.ark.private.discard(43)
        r = await post(b, "/social/api/decks/43/vote", {"vote": "up"})
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "vote": 1, "points": 1}
        assert stack.ark.votes == {("alice", 300043): 1}
        r = await b.http.get("/decks/43", headers=NAV)
        assert "<b class='n'>1</b><span>Liked</span>" in r.text  # read back from Archidekt
        r = await post(b, "/social/api/decks/43/vote", {"vote": "none"})
        assert r.json()["points"] == 0 and stack.ark.votes == {}
        r = await post(b, "/social/api/decks/43/bookmark", {"on": True})
        assert r.status_code == 200 and stack.ark.bookmarks == {"alice": {43}}
        r = await b.http.get("/decks/43", headers=NAV)
        assert "<span>Bookmarked</span>" in r.text
        r = await post(b, "/social/api/decks/43/bookmark", {"on": False})
        assert stack.ark.bookmarks == {"alice": set()}
        r = await b.http.get("/social/api/users/78/follow")
        assert r.json() == {"ok": True, "self": False, "following": False}
        r = await post(b, "/social/api/users/78/follow", {"on": True})
        assert r.status_code == 200 and stack.ark.follows == {"alice": {78}}
        r = await b.http.get("/social/api/users/78/follow")
        assert r.json()["following"] is True
        r = await b.http.get("/social/api/users/77/follow")
        assert r.json()["self"] is True
        r = await post(b, "/social/api/users/77/follow", {"on": True})
        assert r.status_code == 400
        r = await post(b, "/social/api/users/78/follow", {"on": False})
        assert stack.ark.follows == {"alice": set()}
        # bad input
        r = await post(b, "/social/api/decks/43/vote", {"vote": "sideways"})
        assert r.status_code == 400
        r = await post(b, "/social/api/decks/43/bookmark", {"on": "yes"})
        assert r.status_code == 400
    finally:
        await b.aclose()


async def test_comments_read_post_reply_and_stay_in_the_thread(stack: Stack) -> None:
    b = await linked(stack)
    try:
        stack.ark.private.discard(43)
        r = await b.http.get("/social/api/decks/43/comments")
        assert r.status_code == 200 and r.json()["comments"] == [] and r.json()["root"] == 300043
        r = await post(b, "/social/api/decks/43/comments", {"text": "  Nice list!\r\nLove the ramp.  "})
        assert r.status_code == 201, r.text
        c = r.json()["comment"]
        assert c["text"] == "Nice list!\nLove the ramp." and c["owner"]["username"] == "alice"
        assert stack.ark.comments[300043][0]["parent"] == 300043
        r = await post(b, "/social/api/decks/43/comments", {"text": "Thanks!", "parent": c["id"]})
        assert r.status_code == 201 and r.json()["parent"] == c["id"]
        r = await b.http.get("/social/api/decks/43/comments")
        thread = r.json()
        assert thread["count"] == 1 and thread["comments"][0]["replies"][0]["text"] == "Thanks!"
        # a parent outside this deck's thread is refused, so the route cannot post elsewhere
        r = await post(b, "/social/api/decks/43/comments", {"text": "x", "parent": 300042})
        assert r.status_code == 400
        r = await post(b, "/social/api/decks/43/comments", {"text": "   "})
        assert r.status_code == 400
        r = await post(b, "/social/api/decks/43/comments", {"text": "y" * 2001})
        assert r.status_code == 400
        assert len(stack.ark.comments[300043]) == 2
    finally:
        await b.aclose()


async def test_social_routes_need_session_csrf_and_a_linked_account(stack: Stack) -> None:
    # anonymous: 401, nothing happens
    r = await stack.h.http.post(
        "/social/api/decks/43/vote",
        content=json.dumps({"vote": "up"}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401 and stack.ark.votes == {}
    r = await stack.h.http.get("/social/api/decks/43/comments")
    assert r.status_code == 401
    b = Browser(stack.h)
    try:
        await b.login("/decks")
        # signed in, no CSRF header
        r = await b.http.post(
            "/social/api/decks/43/vote",
            content=json.dumps({"vote": "up"}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 403 and r.json()["error"] == "csrf"
        # signed in with the token, but no Archidekt account linked
        r = await post(b, "/social/api/decks/43/vote", {"vote": "up"})
        assert r.status_code == 409 and r.json()["error"] == "not_linked"
        r = await post(b, "/social/api/users/78/follow", {"on": True})
        assert r.status_code == 409
        r = await post(b, "/social/api/decks/43/comments", {"text": "hi"})
        assert r.status_code == 409
        assert stack.ark.votes == {} and stack.ark.follows == {} and stack.ark.comments == {}
    finally:
        await b.aclose()
    # and no MCP tool can do any of it
    token = await mcp_token(stack.h)
    names = {t["name"] for t in sse_json(await stack.h.mcp(token, "tools/list"))["result"]["tools"]}
    assert not any(w in n for n in names for w in ("vote", "like", "follow", "comment", "bookmark")), names


# -- adaptive navigation -----------------------------------------------------------------------
def test_navigation_has_three_tiers_in_the_stylesheet() -> None:
    compact = CSS[CSS.index("@media (max-width:599.98px){") :]
    assert ".tabbar{display:grid" in compact.split("}\n@media", 1)[0]
    rail = CSS[
        CSS.index(
            "@media (min-width:600px) and (max-width:899.98px) and ((pointer:coarse) or (hover:none)){"
        ) :
    ]
    assert ".tabbar{display:flex;flex-direction:column" in rail
    assert "body.has-tabbar{padding-left:calc(80px + env(safe-area-inset-left))}" in rail
    app = CSS[CSS.index("@media (min-width:600px){") :]
    assert "body.app .tabbar{display:flex;flex-direction:column" in app
    assert "body.app footer.site{display:none}" in CSS
    # nothing keys the layout off a phone user agent: a browser's "Desktop site" switch widens the
    # viewport past 900px and gets the desktop bar from the same rules
    assert "min-width:900px" not in CSS or "pointer:coarse" not in CSS[CSS.index("min-width:900px") :]


async def test_app_and_browser_get_the_same_tab_bar_markup(stack: Stack) -> None:
    b = Browser(stack.h)
    try:
        await b.login("/decks")
        r = await b.http.get("/decks", headers={**NAV, **APP_UA})
        assert (
            "<body class='" in r.text and " app" in r.text[r.text.index("<body") : r.text.index("<body") + 80]
        )
        assert "<nav class='tabbar'" in r.text and "<footer class='site'" not in r.text
        r = await b.http.get("/decks", headers=NAV)
        assert "<nav class='tabbar'" in r.text and "<footer class='site'" in r.text
    finally:
        await b.aclose()
