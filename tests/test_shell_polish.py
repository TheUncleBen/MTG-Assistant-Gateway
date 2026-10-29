"""The shell's small fixes of 0.7.46 without a browser: Home greets the member by name, Theme and
Layout answer a script without a redirect, More lists Card models, the in-chat cards' links are
capsules, and the pages' remaining blue links are capsules or text-coloured."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mtg_gateway import card_models, folderview, forum_page, theme

from .conftest import Stack
from .test_decks_and_proxy import Browser, linked_user

STATIC = Path(__file__).resolve().parents[1] / "src" / "mtg_gateway" / "static"


@pytest.fixture
async def member(stack: Stack):
    await linked_user(stack)
    b = Browser(stack.h)
    await b.login("/account")
    try:
        yield b
    finally:
        await b.aclose()


# -- Home's greeting -------------------------------------------------------------------------------


async def test_home_greets_the_member_by_the_name_their_provider_gives(member: Browser, stack: Stack) -> None:
    page = (await member.http.get("/")).text
    assert "<h1>Hi Alice</h1>" in page  # the name, not the login name "alice"
    db = stack.h.app.state.gateway.db
    db.upsert_user("user-1", email="alice@example.test", name=None, preferred_username="alice", groups=[])
    assert "<h1>Hi alice</h1>" in (await member.http.get("/")).text  # no name: the username


# -- Theme and Layout from a script ----------------------------------------------------------------


async def test_theme_and_layout_answer_a_script_without_a_redirect(member: Browser) -> None:
    csrf = await member.csrf("/account")
    js = {"Accept": "application/json"}
    r = await member.http.post("/theme", data={"csrf": csrf, "theme": "dark", "next": "/account"}, headers=js)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "mtg_theme=dark" in r.headers.get("set-cookie", "")
    r = await member.http.post("/layout", data={"csrf": csrf, "layout": "desktop"}, headers=js)
    assert r.status_code == 200 and r.json() == {"ok": True} and "desktop" in r.headers.get("set-cookie", "")
    r = await member.http.post("/theme", data={"csrf": "nope", "theme": "light"}, headers=js)
    assert r.status_code == 403 and r.json() == {"ok": False} and "set-cookie" not in r.headers
    # the form without a script still gets its redirect
    r = await member.http.post("/theme", data={"csrf": csrf, "theme": "light", "next": "/decks"})
    assert r.status_code == 303 and r.headers["location"] == "/decks"


# -- More ------------------------------------------------------------------------------------------


def test_more_lists_my_card_models_while_they_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    groups = dict(theme.more_groups())
    assert ("/history/models", "Card models", "box") in groups["Your cards"]
    monkeypatch.setattr(card_models, "SWITCHED_ON", False)
    assert "/history/models" not in [h for _g, links in theme.more_groups() for h, _l, _i in links]


async def test_the_more_menu_opens_my_card_models(member: Browser) -> None:
    page = (await member.http.get("/decks")).text
    more = page.split("<details class='dd navmore'>", 1)[1].split("</details>", 1)[0]
    assert "href='/history/models'" in more and "Card models" in more
    assert (await member.http.get("/history/models")).status_code == 200


# -- the in-chat cards' links ------------------------------------------------------------------------


def test_the_cards_links_are_capsules_not_blue_text() -> None:
    css = (STATIC / "cards" / "_base.css").read_text(encoding="utf-8")
    rule = re.search(r"(?m)^a\.link\{([^}]*)\}", css)
    assert rule, "a.link rule"
    body = rule.group(1)
    assert "var(--blue)" not in body and "underline" not in body, body
    assert "color:var(--text)" in body and "min-height:44px" in body and "border-radius:22px" in body, body


# -- the shell's remaining links ---------------------------------------------------------------------


def test_the_forum_and_folder_crumbs_are_capsules() -> None:
    crumbs = forum_page.crumbs_html(("Archidekt", "/forum#g"), ("General", "/forum/s"))
    assert crumbs.count("class='ui-cap'") == 3, crumbs
    level = folderview.Level()
    level.current = {"id": 5, "name": "Precons"}
    level.crumbs = [(None, "Decks"), (5, "Precons")]
    out = folderview.breadcrumb_html(level, order="-updatedAt")
    assert "<a class='ui-cap' href=" in out and "<span>Precons</span>" in out, out
