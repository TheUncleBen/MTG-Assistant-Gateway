"""Decision C8 (0.7.46), the navigation parts: Forum in More's Community group and on the rail;
Binder as one place with a Cards | Wants | Watchlist switch (Wants: the cards pinned on
Archidekt), and the Archidekt lists that shape decks (category templates, default categories,
color tags, custom cards) under Account; the picture lights up on them."""

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


def test_more_holds_forum_and_neither_watchlist_nor_my_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(theme, "WATCHLISTS", True)
    groups = theme.more_groups()
    assert [g for g, _links in groups] == ["Find", "Your cards", "Community"]
    hrefs = [h for _g, links in groups for h, _l, _i in links]
    assert hrefs == ["/search", "/precons", "/scan", "/history/models", "/forum"]
    assert [p[0] for p in theme.binder_pages()] == ["/collection", "/collection/wants", "/watchlist"]
    monkeypatch.setattr(theme, "WATCHLISTS", False)
    assert [p[0] for p in theme.binder_pages()] == ["/collection", "/collection/wants"]


def test_the_rail_has_forum_before_alerts_and_the_phone_bar_keeps_it_in_more(server: Server) -> None:
    page = _client(server).get("/decks").text
    bar = page.split("<nav class='tabbar' aria-label='Sections'>")[1].split("<details")[0]
    tabs = re.findall(r"<a href='([^']+)'(?: class='([^']*)')?", bar)
    assert tabs == [
        ("/", ""),
        ("/decks", ""),
        ("/collection", ""),
        ("/proposals", ""),
        ("/forum", "railonly"),
        ("/notifications", "railonly bell"),
    ]
    sheet = page.split("<div class='menu sheet'>")[1].split("</div></details>")[0]
    assert "<div class='head'>Community</div>" in sheet and "href='/forum'" in sheet
    assert "My lists" not in sheet and "href='/watchlist'" not in sheet


def test_watchlist_and_wants_light_binder_and_my_lists_lights_the_picture(server: Server) -> None:
    c = _client(server)
    for path in ("/watchlist", "/collection/wants"):
        nav = c.get(path).text.split("<nav class='site' aria-label='Site'>")[1].split("</nav>")[0]
        assert "<a href='/collection' aria-current='page'>Binder</a>" in nav, path
    page = c.get("/lists/templates").text
    assert "class='icon-btn acct' href='/account' aria-label='Account' title='Account'" in page
    assert re.search(r"class='icon-btn acct' href='/account'[^>]*aria-current=page", page)


async def test_binder_pages_share_one_switch(member: Browser) -> None:
    cards = (await member.http.get("/collection")).text
    assert _switch(cards) == ["/collection", "/collection/wants", "/watchlist"]
    assert "<h1 class='sr-only'>Binder: Cards</h1>" in cards
    assert "<a href='/collection' aria-current=page>Cards</a>" in cards
    wants = (await member.http.get("/collection/wants")).text
    assert _switch(wants) == ["/collection", "/collection/wants", "/watchlist"]
    assert "<a href='/collection/wants' aria-current=page>Wants</a>" in wants
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


async def test_my_lists_lives_under_account_without_the_pins_tab(member: Browser) -> None:
    page = (await member.http.get("/lists/templates")).text
    tabs = page.split("<nav class='adm-nav lp-tabs' aria-label='Lists'")[1].split("</nav>")[0]
    assert re.findall(r"<a href='([^']+)'", tabs) == [
        "/lists/templates",
        "/lists/defaults",
        "/lists/tags",
        "/lists/custom",
    ]
    assert "Pinned cards" not in tabs
    assert "<a class='ui-cap' href='/account'>Account</a>" in page  # the way back
    hub = (await member.http.get("/account")).text
    assert "href='/lists'" in hub and "My lists" in hub
    details = (await member.http.get("/account/details")).text
    lists = details.split("<div class='card' id='lists'>")[1].split("</div></div>")[0]
    assert "href='/lists/pins'" not in lists and "pinned" not in lists
    assert "href='/collection/wants'" in details
