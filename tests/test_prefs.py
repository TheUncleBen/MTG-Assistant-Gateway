"""Choices that follow the account (0.7.29): the
pages' own API for them, what it accepts, the one-time copy from a device, the newest 150 stack
orders, "View as" moving from a cookie to the account, and "Forget my view choices"."""

from __future__ import annotations

import html
import json
from pathlib import Path

from mtg_gateway import prefs
from mtg_gateway.db import Database

from .conftest import Stack
from .test_browse_collection import api, linked
from .test_round4_data import _app_token

SUB = "user-1"


async def test_the_pages_save_and_read_choices(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await api(
            b,
            "POST",
            "/api/v1/prefs",
            {
                "set": {
                    "card.size": "l",
                    "stack:42:category": ["Lands", "Commander"],
                    "view.deck": "grid",
                    "deckstats": {"curve": "category", "chart": "pie", "lands": True},  # as deckstats.js
                }
            },
        )
        assert r.status_code == 200 and r.json()["refused"] == [], r.text
        got = (await b.http.get("/api/v1/prefs")).json()["prefs"]
        assert got == {
            "card.size": "l",
            "stack:42:category": ["Lands", "Commander"],
            "view.deck": "grid",
            "deckstats": {"curve": "category", "chart": "pie", "lands": True},
        }
        # null forgets one
        r = await api(b, "POST", "/api/v1/prefs", {"set": {"card.size": None}})
        assert r.json()["saved"] == ["card.size"] and stack.h.db.get_pref(SUB, "card.size") is None
    finally:
        await b.aclose()


async def test_only_well_formed_choices_are_kept(stack: Stack) -> None:
    b = await linked(stack)
    try:
        bad = {
            "card.size": "xl",
            "view.everything": "grid",
            "view.deck": "<b>",
            "stack:abc:category": ["x"],
            "stack:42:category": "not a list",
            "theme": "dark",  # light or dark stays on each device
            "edhplay.steps": "yes",
            "deckstats": {"chart": "x" * 41, "lands": 1},
        }
        r = await api(b, "POST", "/api/v1/prefs", {"set": bad})
        assert sorted(r.json()["refused"]) == sorted(bad) and stack.h.db.get_prefs(SUB) == {}
        r = await api(b, "POST", "/api/v1/prefs", {"set": ["card.size"]})
        assert r.status_code == 400
    finally:
        await b.aclose()


async def test_the_api_needs_the_browser_and_its_form_token(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await api(b, "POST", "/api/v1/prefs", {"set": {"card.size": "s"}}, csrf="wrong")
        assert r.status_code == 403 and stack.h.db.get_pref(SUB, "card.size") is None
        token, _client = await _app_token(stack.h, "Some app")
        for method in ("GET", "POST"):
            r = await stack.h.http.request(
                method,
                "/api/v1/prefs",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps({"set": {"card.size": "s"}}),
            )
            assert r.status_code == 403 and r.json()["error"] == "browser_required", r.text
        assert stack.h.db.get_pref(SUB, "card.size") is None
    finally:
        await b.aclose()
    r = await stack.h.http.get("/api/v1/prefs")
    assert r.status_code == 401


async def test_the_copy_from_a_device_never_overwrites_the_account(stack: Stack) -> None:
    b = await linked(stack)
    try:
        stack.h.db.set_pref(SUB, "deckstats", {"chart": "bar"})
        r = await api(
            b,
            "POST",
            "/api/v1/prefs",
            {"copy": {"deckstats": {"chart": "pie"}, "scan.intro": True, "stack:42:type": ["Land"]}},
        )
        assert sorted(r.json()["saved"]) == ["scan.intro", "stack:42:type"]
        assert stack.h.db.get_pref(SUB, "deckstats") == {"chart": "bar"}
        assert stack.h.db.get_pref(SUB, "scan.intro") is True
    finally:
        await b.aclose()


def test_the_newest_150_stack_orders_are_kept(tmp_path: Path) -> None:
    """One more forgets the oldest instead of refusing; nothing else goes."""
    db = Database(tmp_path / "g.sqlite")
    db.upsert_user(SUB, email=None, name=None, preferred_username=None, groups=[])
    assert prefs.save(db, SUB, "card.size", "s")
    for i in range(prefs.MAX_STACK_ORDERS):
        assert prefs.save(db, SUB, f"stack:{i}:category", ["A"])
        with db.tx() as c:  # each one a second older than the next
            c.execute(
                "UPDATE account_prefs SET updated_at = ? WHERE name = ?", (1000 + i, f"stack:{i}:category")
            )
    assert prefs.save(db, SUB, "stack:0:category", ["B"])  # an update forgets nothing
    assert len([k for k in db.get_prefs(SUB) if k.startswith("stack:")]) == 150
    assert prefs.save(db, SUB, "stack:999:category", ["A"])
    kept = db.get_prefs(SUB)
    assert len([k for k in kept if k.startswith("stack:")]) == 150
    assert "stack:1:category" not in kept  # the oldest went
    assert kept["stack:0:category"] == ["B"] and kept["card.size"] == "s"
    db.close()


async def test_view_as_moves_from_the_cookie_to_the_account(stack: Stack) -> None:
    """A view this browser's cookie held before 0.7.29 is copied to the account on the first page
    that reads it, and the cookie is removed; another device then shows the same view."""
    b = await linked(stack)
    try:
        b.http.cookies.set("mtg_view_cards", "list")
        r = await b.http.get("/cards", params={"q": "sol"})
        assert "value='list' checked" in r.text
        assert stack.h.db.get_pref(SUB, "view.cards") == "list"
        assert 'mtg_view_cards=""' in r.headers.get("set-cookie", "")
    finally:
        await b.aclose()
    other = await linked(stack)  # another browser, no cookie
    try:
        r = await other.http.get("/cards", params={"q": "sol"})
        assert "value='list' checked" in r.text and "set-cookie" not in r.headers
        await other.http.get("/cards", params={"q": "sol", "view": "grid"})
        assert stack.h.db.get_pref(SUB, "view.cards") == "grid"
    finally:
        await other.aclose()


async def test_a_link_from_another_site_shows_a_view_but_never_saves_it(stack: Stack) -> None:
    """A plain link carries the sign-in cookie, so a view in a link on another site is shown but
    not kept on the account, and its Try again on the deck list doesn't read the list again."""
    b = await linked(stack)
    try:
        await b.http.get("/cards", params={"q": "sol", "view": "grid"})
        assert stack.h.db.get_pref(SUB, "view.cards") == "grid"
        for site in ("cross-site", "same-site"):
            r = await b.http.get(
                "/cards", params={"q": "sol", "view": "list"}, headers={"Sec-Fetch-Site": site}
            )
            assert "value='list' checked" in r.text
            assert stack.h.db.get_pref(SUB, "view.cards") == "grid"
        decks = stack.h.app.state.gateway.decks
        dropped: list[str] = []
        decks.reread_deck_list = dropped.append
        r = await b.http.get("/decks", params={"reread": "1"}, headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 303 and dropped == []
        # the gateway's own pages, and an address typed or bookmarked, still save
        await b.http.get(
            "/cards", params={"q": "sol", "view": "list"}, headers={"Sec-Fetch-Site": "same-origin"}
        )
        assert stack.h.db.get_pref(SUB, "view.cards") == "list"
        await b.http.get("/cards", params={"q": "sol", "view": "grid"}, headers={"Sec-Fetch-Site": "none"})
        assert stack.h.db.get_pref(SUB, "view.cards") == "grid"
        await b.http.get("/decks", params={"reread": "1"}, headers={"Sec-Fetch-Site": "same-origin"})
        assert dropped == [SUB]
    finally:
        await b.aclose()


async def test_the_home_pages_try_again_reads_the_list_only_from_this_site(stack: Stack) -> None:
    b = await linked(stack)
    try:
        decks = stack.h.app.state.gateway.decks
        dropped: list[str] = []
        decks.reread_deck_list = dropped.append
        r = await b.http.get("/", params={"reread": "1"}, headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 303 and dropped == []
        await b.http.get("/", params={"reread": "1"}, headers={"Sec-Fetch-Site": "same-origin"})
        assert dropped == [SUB]
    finally:
        await b.aclose()


async def test_without_sec_fetch_site_the_origin_or_referer_decides(stack: Stack) -> None:
    """An older browser sends no Sec-Fetch-Site: a link from another site still names that site in
    Origin or Referer, so its view is shown but not kept; this gateway's own pages, or no such
    header at all (typed or bookmarked), still save."""
    b = await linked(stack)
    try:
        here = stack.h.app.state.gateway.settings.public_url
        await b.http.get("/cards", params={"q": "sol", "view": "grid"})
        assert stack.h.db.get_pref(SUB, "view.cards") == "grid"
        for header in (
            {"Referer": "https://elsewhere.example/page"},
            {"Origin": "https://elsewhere.example"},
        ):
            await b.http.get("/cards", params={"q": "sol", "view": "list"}, headers=header)
            assert stack.h.db.get_pref(SUB, "view.cards") == "grid", header
        await b.http.get("/cards", params={"q": "sol", "view": "list"}, headers={"Referer": f"{here}/decks"})
        assert stack.h.db.get_pref(SUB, "view.cards") == "list"
        await b.http.get("/cards", params={"q": "sol", "view": "grid"})
        assert stack.h.db.get_pref(SUB, "view.cards") == "grid"
    finally:
        await b.aclose()


async def test_forget_my_view_choices(stack: Stack) -> None:
    b = await linked(stack)
    try:
        db = stack.h.db
        for name, value in (("view.deck", "grid"), ("card.size", "l"), ("stack:42:type", ["Land"])):
            db.set_pref(SUB, name, value)
        db.set_pref(SUB, "track.a.something", True)  # not a view choice: stays
        page = html.unescape((await b.http.get("/account/details")).text)
        assert "Forget my view choices" in page and "Light or dark mode stays on each device." in page
        csrf = await b.csrf("/account/details")
        r = await b.http.post("/account", data={"csrf": csrf, "action": "forget_choices"})
        assert r.status_code == 303 and "err=confirm_forget" in r.headers["location"]
        assert db.get_pref(SUB, "card.size") == "l"
        r = await b.http.post("/account", data={"csrf": csrf, "action": "forget_choices", "confirm": "yes"})
        assert r.status_code == 303 and "ok=choices_forgotten" in r.headers["location"]
        where = r.headers["location"]
        assert db.get_prefs(SUB) == {"track.a.something": True, prefs.FORGOTTEN: True}
        # a device not used since the update can't bring the forgotten choices back: its
        # stored choices aren't copied, and its view cookie is dropped, not copied
        r = await api(b, "POST", "/api/v1/prefs", {"copy": {"stack:42:type": ["Land"], "scan.intro": True}})
        assert r.json()["ok"] is True and r.json()["saved"] == []
        b.http.cookies.set("mtg_view_cards", "list")
        page = await b.http.get("/cards", params={"q": "sol"})
        assert "value='list' checked" not in page.text
        assert 'mtg_view_cards=""' in page.headers.get("set-cookie", "")
        assert db.get_pref(SUB, "stack:42:type") is None and db.get_pref(SUB, "view.cards") != "list"
        # a choice made after forgetting is kept as usual, and the marker isn't a choice
        r = await api(b, "POST", "/api/v1/prefs", {"set": {"card.size": "s", prefs.FORGOTTEN: False}})
        assert r.json()["saved"] == ["card.size"] and r.json()["refused"] == [prefs.FORGOTTEN]
        assert prefs.FORGOTTEN not in (await b.http.get("/api/v1/prefs")).json()["prefs"]
        done = html.unescape((await b.http.get(where)).text)
        assert "Every page starts with its usual view again, on all your devices." in done
        assert [e for e in db.audit_recent(20, sub=SUB) if e["event"] == "view_choices_forgotten"]
    finally:
        await b.aclose()


async def test_each_page_keeps_its_own_card_size(stack: Stack) -> None:
    """Ben, 2026-10-28: one card size for every page meant a change in the printings dialog resized
    the deck page. Each page keeps its own (card.size.<page>), M until chosen, or the one size
    chosen before for every page, so nobody's choice is lost."""
    b = await linked(stack)
    try:
        db = stack.h.db
        assert set(prefs.CARD_SIZE_PAGES) >= {
            "deck", "printings", "search", "watchlist", "goldfish", "scan", "collection", "editor", "decks"
        }  # fmt: skip
        r = await api(
            b, "POST", "/api/v1/prefs", {"set": {"card.size.deck": "s", "card.size.printings": "l"}}
        )
        assert r.json()["saved"] == ["card.size.deck", "card.size.printings"], r.text
        r = await api(b, "POST", "/api/v1/prefs", {"set": {"card.size.nowhere": "s", "card.size.deck": "xl"}})
        assert r.json()["refused"] == ["card.size.nowhere", "card.size.deck"]
        page = (await b.http.get("/decks/42")).text
        assert "data-card-size=s data-card-size-page=deck data-card-size-printings=l>" in page
        # a page with no choice of its own: M, or the size chosen once for every page before
        page = (await b.http.get("/cards", params={"q": "sol"})).text
        assert (
            "data-card-size=m data-card-size-page=search data-card-size-unset data-card-size-printings=l>"
            in page
        )
        db.set_pref(SUB, "card.size", "l")
        page = (await b.http.get("/cards", params={"q": "sol"})).text
        assert "data-card-size=l data-card-size-page=search data-card-size-printings=l>" in page
        assert "data-card-size=s data-card-size-page=deck" in (await b.http.get("/decks/42")).text
        assert "data-card-size-page=editor" in (await b.http.get("/decks/42/edit")).text
        assert "data-card-size-page=collection" in (await b.http.get("/collection")).text
        assert "data-card-size-page=decks" in (await b.http.get("/decks")).text
        # Forget my view choices forgets each page's
        prefs.forget(db, SUB)
        assert db.get_pref(SUB, "card.size.deck") is None and db.get_pref(SUB, "card.size.printings") is None
    finally:
        await b.aclose()
