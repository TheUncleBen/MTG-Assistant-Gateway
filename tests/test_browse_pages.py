"""The preconstructed decks and a member's profile (UI overhaul 2026-10-21, mockups 21-precons
and 22-user-profile): deck search's panel and results bar on both, a heading per set with Show
all and Load more sets, the profile's one header with the count in the results bar and Load
more decks; deck search's colour match says "colors" and its count is never the word "Decks"."""

from __future__ import annotations

import pytest

from mtg_gateway import browse

from .conftest import Stack
from .test_browse_collection import NAV
from .test_decks_and_proxy import Browser

pytestmark = pytest.mark.anyio


async def _signed_in(stack: Stack, path: str) -> Browser:
    b = Browser(stack.h)
    await b.login(path)
    return b


async def test_precons_have_the_shared_panel_set_headings_and_load_more(stack: Stack) -> None:
    b = await _signed_in(stack, "/precons")
    try:
        r = await b.http.get("/precons", headers=NAV)
        assert r.status_code == 200
        page = r.text
        assert page.count("<h1>Preconstructed decks</h1>") == 1
        assert "<p class='ui-sub'>Archidekt&#x27;s preconstructed decks, newest set first.</p>" in page
        assert "<label for='pre-q'>Set or deck name</label>" in page and "placeholder='Ixalan'" in page
        assert "Search all decks" in page and "href='/search'" in page
        assert "<option value='Sample Set (SMP)'>Sample Set (SMP)</option>" in page
        assert "<option value='' selected>All sets</option>" in page
        assert "id='pre-count' aria-live='polite'>1 deck in 1 set</h2>" in page  # the fake lists one deck
        assert "<option value='set' selected>Set release</option>" in page
        assert "<option value='name'>Deck name</option>" in page
        assert "class='segc view' role='radiogroup' aria-label='View'" in page
        assert "class='g size cardsize'" in page and "href='/precons'>Clear filters</a>" in page
        # no panel boxes the tiles: a heading per set with its code and count, Show all hidden
        assert "preconset" not in page and "<details" not in page.split("<main")[1].split("pre-grp")[0]
        assert (
            "<h2 class='sec'>Sample Set <span class='code'>(SMP)</span> <span class='n'>1 deck</span></h2>"
            in page
        )
        # precon tiles: no owner picture and no deck tags (as deck search's tiles); the list date
        tiles = page.split("<ul class='plain decklist", 1)[1]
        assert "class='avatar" not in tiles and "class='tags'" not in tiles and "No deck tags" not in tiles
        assert "<span class='info plain'>" in tiles and "<time class='ui-when'" in tiles
        assert (
            "class='btn btn-plain pre-all' data-n='1' hidden aria-expanded='false'>Show all 1</button>"
            in page
        )
        assert "All 1 set shown." in page and "data-load-more" not in page
        assert "No preconstructed deck matches that filter." not in page
        # by name is one grid without headings
        r = await b.http.get("/precons?sort=name", headers=NAV)
        assert "class='sec'" not in r.text and r.text.count("<ul class='plain decklist") == 1
        assert "All 1 decks shown." in r.text
        # the filter: the count says how many of all, every matching set is open
        r = await b.http.get("/precons?q=sample", headers=NAV)
        assert "id='pre-count' aria-live='polite'>1 of 1 decks</h2>" in r.text
        assert "<section class='pre-grp open'" in r.text and "class='btn btn-plain pre-all'" not in r.text
        r = await b.http.get("/precons?set=Sample+Set+(SMP)", headers=NAV)
        assert "1 of 1 decks" in r.text and "<option value='Sample Set (SMP)' selected>" in r.text
        r = await b.http.get("/precons?q=zzzz", headers=NAV)
        assert "No preconstructed deck matches that filter." in r.text and "0 of 1 decks" in r.text
        # the page's script asks the same listing as JSON
        r = await b.http.get("/precons/api/sets?q=sample", headers=NAV)
        d = r.json()
        assert (
            d["ok"] and d["count"] == "1 of 1 decks" and "Sample Set <span" in d["html"] and d["next"] == ""
        )
    finally:
        await b.aclose()


def test_precons_show_a_screenful_of_sets_then_load_more() -> None:
    listing = {
        f"Set {i:02d} (S{i:02d})": [{"id": 1000 + i, "name": f"Deck {i:02d}", "owner": {}}] for i in range(7)
    }
    pq = browse.precon_query({})
    groups = browse.precon_groups(listing, pq)
    assert [g["code"] for g in groups] == [f"S{i:02d}" for i in range(7)]
    html, nxt = browse.precon_body(groups, listing, pq, view="grid", view_in_link=None)
    assert html.count("<section class='pre-grp'") == browse.PRECON_SETS == 4
    assert nxt == "/precons?sets=8"
    assert f"<a class='btn btn-plain' href='{nxt}' rel='next' data-load-more>Load more sets</a>" in html
    assert "<p class='muted'>4 of 7</p>" in html
    assert browse.precon_count_text(groups, listing, pq) == "7 decks in 7 sets"
    more = browse.precon_query({"sets": "8"})
    html, nxt = browse.precon_body(groups, listing, more, view="grid", view_in_link=None)
    assert html.count("<section class='pre-grp'") == 7 and nxt == "" and "All 7 sets shown." in html
    # sorted by name: one grid, a screenful of decks, then Load more decks
    by_name = browse.precon_query({"sort": "name", "dir": "asc"})
    big = {"One Set (ONE)": [{"id": 2000 + i, "name": f"Deck {i:02d}", "owner": {}} for i in range(30)]}
    groups = browse.precon_groups(big, by_name)
    html, nxt = browse.precon_body(groups, big, by_name, view="grid", view_in_link="list")
    assert html.count("<li class='deck'") == browse.PRECON_DECKS == 24
    assert nxt == "/precons?sort=name&dir=asc&view=list&offset=24" and ">Load more decks</a>" in html
    assert html.index("Deck 00") < html.index("Deck 01")


async def test_a_profile_has_one_header_the_results_bar_and_load_more(stack: Stack) -> None:
    b = await _signed_in(stack, "/users/alice")
    try:
        r = await b.http.get("/users/alice", headers=NAV)
        assert r.status_code == 200
        page = r.text
        assert page.count("<h1") == 1 and "<h1 class='uname'>alice</h1>" in page
        assert "<p class='sub'>Public decks on Archidekt</p>" in page
        assert "class='avatar xl'" in page
        assert "<time class='ui-when'" in page.split("<ul class='plain decklist", 1)[1]  # the list date
        assert "Archidekt profile</a>" in page and "href='https://archidekt.com/u/alice'" in page
        assert "class='btn btn-plain ext'" in page  # the one orange button is the follow question's
        # the results bar: the count, Sort by with its direction, Grid or List, card size; no Clear
        assert "class='panel spanel'" in page
        assert "id='s-count' aria-live='polite'>1 deck</h2>" in page
        assert "<option value='-updatedAt' selected>Updated at</option>" in page
        assert "<select class='dir' name='dir' aria-label='Direction'>" in page
        assert "class='segc view' role='radiogroup' aria-label='View'" in page
        assert "class='g size cardsize'" in page and "Clear filters" not in page
        assert "<noscript><button type='submit' class='btn apply'>Apply</button></noscript>" in page
        # Load more decks instead of the pager; it carries the owner so the next page is theirs
        assert "Previous" not in page and "Page 1" not in page and "results-head" not in page
        assert (
            "id='s-more' data-page='1'" in page and "owner=alice" in page.split("data-qs='")[1].split("'")[0]
        )
        assert "order=-updatedAt" in page.split("data-qs='")[1].split("'")[0]
        assert ">Load more decks</button>" in page
        assert "Sample Commander Deck" in page
        # a member without public decks
        r = await b.http.get("/users/amy", headers=NAV)
        assert "<p class='sub'>No public decks, or no such user</p>" in r.text
        assert "<p class='muted'>No public decks yet.</p>" in r.text and "Try fewer filters" not in r.text
        assert "id='s-count' aria-live='polite'>0 decks</h2>" in r.text
    finally:
        await b.aclose()


async def test_deck_search_says_colors_and_counts_what_it_found(stack: Stack) -> None:
    b = await _signed_in(stack, "/search")
    try:
        r = await b.http.get("/search?q=Sample", headers=NAV)
        assert "<option value='incl' selected>Including these colors</option>" in r.text
        assert "<option value='exact'>Exactly these colors</option>" in r.text
        assert "<option value='atmost'>At most these colors</option>" in r.text
        assert "id='s-count' aria-live='polite'>1 deck</h2>" in r.text
        assert ">Decks</h2>" not in r.text
    finally:
        await b.aclose()
    # a count Archidekt did not give is the decks shown, never the word "Decks"
    q = browse.read_query({})
    assert browse.count_text({"decks": [1, 2, 3], "count": None}, q, shown=3) == "3 decks"
    assert browse.count_text({"decks": [1], "count": -1}, q, shown=1) == "1 deck"
