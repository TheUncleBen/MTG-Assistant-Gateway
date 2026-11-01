"""Decision C8 (0.7.46), the navigation parts: Forum in More's Community group and on the rail;
Binder as one place with a Cards | Lists | Watchlist switch (mockup screen 8): Lists holds the
Archidekt lists that shape decks (category templates, default categories, color tags, custom
cards), the cards pinned on Archidekt among them, and the watchlist's own lists; the Account
page keeps a row to it."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from mtg_gateway import theme

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED
from .test_decks_and_proxy import Browser, linked_user
from .test_lists_page import WHO, _seed
from .test_suggest_browser import Server

stack_options = stack_with(SHIPPED)
NAV = {"Accept": "text/html"}


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _client(server: Server) -> httpx.Client:
    return httpx.Client(base_url=server.base, cookies={"mtg_session": server.sign_in()}, headers=NAV)


@pytest.fixture
async def member(stack: Stack):
    _seed(stack.ark)
    await linked_user(stack)
    b = Browser(stack.h)
    await b.login("/collection")
    try:
        yield b
    finally:
        await b.aclose()


def _switch(page: str) -> list[str]:
    seg = page.split("<nav class='hubseg' aria-label='Binder'>")[1].split("</nav>")[0]
    return re.findall(r"<a href='([^']+)'", seg)


def test_more_holds_forum_and_neither_watchlist_nor_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(theme, "WATCHLISTS", True)
    groups = theme.more_groups()
    assert [g for g, _links in groups] == ["Find", "Your cards", "Community"]
    hrefs = [h for _g, links in groups for h, _l, _i in links]
    assert hrefs == ["/search", "/precons", "/scan", "/history/models", "/forum"]
    assert [p[0] for p in theme.binder_pages()] == ["/collection", "/collection/lists", "/watchlist"]
    monkeypatch.setattr(theme, "WATCHLISTS", False)
    assert [p[0] for p in theme.binder_pages()] == ["/collection", "/collection/lists"]


def test_the_rail_has_forum_before_alerts_and_the_phone_bar_keeps_it_in_more(server: Server) -> None:
    page = _client(server).get("/decks").text
    bar = page.split("<nav class='tabbar' aria-label='Sections'>")[1].split("<details")[0]
    tabs = re.findall(r"<a href='([^']+)'(?: class='([^']*)')?", bar)
    # the rail without More (0.7.46 redesign, screen 1): More's pages after the four, Forum last
    # of them, then Alerts at the foot
    assert tabs == [
        ("/", ""),
        ("/decks", ""),
        ("/collection", ""),
        ("/proposals", ""),
        ("/scan", "railonly"),
        ("/precons", "railonly"),
        ("/history/models", "railonly"),
        ("/forum", "railonly"),
        ("/notifications", "railonly bell foot"),
    ]
    sheet = page.split("<div class='menu sheet'>")[1].split("</div></details>")[0]
    assert "<div class='head'>Community</div>" in sheet and "href='/forum'" in sheet
    assert "My lists" not in sheet and "href='/watchlist'" not in sheet
    assert "href='/collection/lists'" not in sheet and "href='/lists'" not in sheet


def test_watchlist_and_every_list_light_binder_and_account_lights_the_picture(server: Server) -> None:
    c = _client(server)
    for path in ("/watchlist", "/collection/wants", "/collection/lists", "/lists/templates"):
        page = c.get(path).text
        nav = page.split("<nav class='site' aria-label='Site'>")[1].split("</nav>")[0]
        assert "<a href='/collection' aria-current='page'>Binder</a>" in nav, path
        assert not re.search(r"class='icon-btn acct' href='/account'[^>]*aria-current=page", page), path
    page = c.get("/account/details").text
    assert "class='icon-btn acct' href='/account' aria-label='Account' title='Account'" in page
    assert re.search(r"class='icon-btn acct' href='/account'[^>]*aria-current=page", page)


async def test_binder_pages_share_one_switch(member: Browser) -> None:
    cards = (await member.http.get("/collection")).text
    assert _switch(cards) == ["/collection", "/collection/lists", "/watchlist"]
    assert "<h1 class='sr-only'>Binder: Cards</h1>" in cards
    assert re.search(r"<a href='/collection' aria-current=page>Cards(<span class='cn'>|</a>)", cards)
    hub = (await member.http.get("/collection/lists")).text
    assert _switch(hub) == ["/collection", "/collection/lists", "/watchlist"]
    assert "<a href='/collection/lists' aria-current=page>Lists</a>" in hub
    assert "<h1 class='sr-only'>Binder: Lists</h1>" in hub
    assert "<h2>Your Archidekt lists</h2>" in hub and "<h2>Watchlist lists</h2>" in hub
    wants = (await member.http.get("/collection/wants")).text
    assert _switch(wants) == ["/collection", "/collection/lists", "/watchlist"]
    assert "<a href='/collection/lists' aria-current=page>Lists</a>" in wants  # the pins are a list
    assert "<h1 class='sr-only'>Binder: Pinned cards</h1>" in wants
    assert "Sol Ring" in wants and "for Meren" in wants  # the pin seeded on Archidekt
    assert "href='/collection/wants?remove=31'" in wants  # the row's menu stays on this page
    watch = (await member.http.get("/watchlist")).text
    assert "<a href='/watchlist' aria-current=page>Watchlist</a>" in watch


async def test_old_pins_addresses_land_on_wants_and_a_save_comes_back_there(
    member: Browser, stack: Stack
) -> None:
    r = await member.http.get("/lists/pins?remove=31")
    assert r.status_code == 302 and r.headers["location"] == "/collection/wants?remove=31"
    csrf = await member.csrf("/collection/wants")
    r = await member.http.post(
        "/lists/pins", data={"csrf": csrf, "action": "edit", "pin_id": "31", "quantity": "2"}
    )
    assert r.status_code == 303 and r.headers["location"] == "/collection/wants?ok=pin_saved"
    assert stack.ark.account.pins[WHO][0]["quantity"] == 2
    page = (await member.http.get(r.headers["location"])).text
    assert "Saved on Archidekt." in page


async def test_the_lists_live_in_binder_with_the_pins_among_them(member: Browser) -> None:
    page = (await member.http.get("/lists/templates")).text
    tabs = page.split("<nav class='adm-nav lp-tabs' aria-label='Lists'")[1].split("</nav>")[0]
    assert re.findall(r"<a href='([^']+)'", tabs) == [
        "/lists/templates",
        "/lists/defaults",
        "/lists/tags",
        "/collection/wants",
        "/lists/custom",
        "/lists/btags",
    ]
    assert "Pinned cards" in tabs
    # the way back: the Lists crumb to the hub, not Account
    crumbs = page.split("<nav class='crumbs lp-crumbs' aria-label='You are in'>")[1].split("</nav>")[0]
    assert "href='/collection/lists'" in crumbs and "<span>Category templates</span>" in crumbs
    assert "<a class='ui-cap' href='/account'>Account</a>" not in page
    hub = (await member.http.get("/account")).text
    assert "<a class='yrow' href='/collection/lists'>" in hub and "<span class='lbl'>Lists</span>" in hub
    assert "href='/lists'" not in hub and "My lists" not in hub
    details = (await member.http.get("/account/details")).text
    lists = details.split("<details class='yrow' id='lists'>")[1].split("</details>")[0]
    assert "href='/lists/pins'" not in lists and "href='/collection/wants'" in lists
    assert "href='/collection/lists'" in lists and "My lists" not in details
