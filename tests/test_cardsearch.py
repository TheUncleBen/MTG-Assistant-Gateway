"""The card search (cardsearch.py) and the top bar's search box (theme.py): ``/cards?q=`` lists
the matching cards with the data the card viewer reads and an Add to deck button that is live
for a linked member and disabled, with the reason, for one without an Archidekt link; the top
bar carries the site search on every signed-in page and never on the sign-in page; the deck
search links to the card search for the same text; hostile text is escaped."""

from __future__ import annotations

import pytest

from .test_browse_collection import Stack, linked, stack  # noqa: F401 - fixture
from .test_decks_and_proxy import Browser

CATALOG = [
    "Sol Ring",
    "Sol Talisman",
    "Solemn Simulacrum שלום",  # Hebrew in a name
    "Solar Blaze \U0001f525",  # emoji in a name
    "Aesi, Tyrant of Gyre Strait",
    "Okiri, Belligerent Bannerkeeper of the Greatest Grand Army",
] + [f"Filler Card {i}" for i in range(50)]


def _catalog(st: Stack) -> None:
    st.h.app.state.gateway.scan.names.set_names(CATALOG)


async def test_the_card_page_needs_a_sign_in(stack: Stack) -> None:  # noqa: F811
    r = await stack.h.http.get("/cards?q=sol")
    assert r.status_code in (302, 303), r.status_code
    assert r.headers["location"] == "/login?next=/cards?q=sol", r.headers["location"]


async def test_matching_cards_are_listed_with_viewer_data_and_a_live_add_button(stack: Stack) -> None:  # noqa: F811
    _catalog(stack)
    b = await linked(stack)
    try:
        r = await b.http.get("/cards?q=sol")
        assert r.status_code == 200, r.text
        t = r.text
        # the two names the fake Scryfall knows come back with picture, cost and type; the names it
        # does not know (the hostile ones) are left out rather than shown empty
        assert "Cards matching “sol”" in t and "2 cards" in t
        assert "data-card='Sol Ring'" in t and "data-card='Sol Talisman'" in t
        assert "data-mana='{1}'" in t and "data-type='Artifact'" in t
        assert "data-img='https://cards.scryfall.io/" in t
        assert "data-scry='https://scryfall.com/" in t
        assert "<ul class='cardgrid' id='cardgrid' data-linked>" in t
        assert "data-add='Sol Ring'>" in t and "disabled" not in t.split("id='cardgrid'")[1]
        # the page's own script and the shared viewer, and pictures allowed from Scryfall only
        assert "/static/cardsearch.js" in t and "/static/cardview.js" in t
        assert "img-src 'self' https://cards.scryfall.io" in r.headers["content-security-policy"]
        assert "connect-src 'self'" in r.headers["content-security-policy"]
        # the box keeps the text, suggests as it is typed and offers the deck search for it
        assert "id='c-q' type='search' name='q' value='sol'" in t and "data-suggest-submit" in t
        assert "href='/search?name=sol'" in t
    finally:
        await b.aclose()


async def test_a_member_without_an_archidekt_link_sees_the_add_disabled_with_the_reason(
    stack: Stack,  # noqa: F811
) -> None:
    _catalog(stack)
    b = Browser(stack.h)
    await b.login("/cards")
    try:
        r = await b.http.get("/cards?q=sol ring")
        assert r.status_code == 200, r.text
        t = r.text
        assert "data-card='Sol Ring'" in t
        assert "<ul class='cardgrid' id='cardgrid'>" in t  # no data-linked
        assert "data-add='Sol Ring' disabled title='Link your Archidekt account on the Account page" in t
        assert "no Archidekt account is linked" in t and "href='/account'" in t
    finally:
        await b.aclose()


async def test_hostile_text_is_escaped_and_short_text_shows_the_help(stack: Stack) -> None:  # noqa: F811
    _catalog(stack)
    b = await linked(stack)
    try:
        r = await b.http.get("/cards", params={"q": "<script>alert(1)</script> שלום \U0001f525"})
        assert r.status_code == 200
        assert "<script>alert" not in r.text and "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
        assert "No card is named like that" in r.text
        r = await b.http.get("/cards?q=s")
        assert r.status_code == 200 and "Find a card" in r.text and "id='cardgrid'" not in r.text
        r = await b.http.get("/cards")
        assert r.status_code == 200 and "Find a card" in r.text
        # a very long text is cut to the catalog's limit, not refused
        r = await b.http.get("/cards", params={"q": "Okiri " * 60})
        assert r.status_code == 200 and "Cards matching" in r.text
    finally:
        await b.aclose()


async def test_a_name_the_catalog_knows_but_scryfall_does_not_is_left_out(stack: Stack) -> None:  # noqa: F811
    _catalog(stack)
    b = await linked(stack)
    try:
        r = await b.http.get("/cards?q=okiri")
        assert r.status_code == 200
        assert "0 cards" in r.text and "No card is named like that" in r.text
    finally:
        await b.aclose()


async def test_card_lookup_switched_off_is_said_plainly(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    gw = stack.h.app.state.gateway
    scan = gw.scan
    gw.scan = None
    try:
        r = await b.http.get("/cards?q=sol")
        assert r.status_code == 200 and "Card lookup is switched off" in r.text
    finally:
        gw.scan = scan
        await b.aclose()


async def test_the_top_bar_carries_the_site_search_on_signed_in_pages_only(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        for path in ("/decks", "/account", "/search", "/collection", "/proposals"):
            r = await b.http.get(path)
            assert r.status_code == 200, (path, r.status_code)
            t = r.text
            form = "<form class='topsearch' id='topsearch' role='search' method='get' action='/search'>"
            assert form in t, path
            assert "id='site-q' type='search' name='q'" in t and "data-suggest-site='/cards?q='" in t, path
            assert "/static/sitesearch.js" in t and "/static/suggest.js" in t, path
            # the phone's magnifier stays a plain link to the deck search without script
            assert "class='icon-btn searchbtn' href='/search'" in t, path
            # the thumbnails in the suggestions may come from Scryfall on every page
            assert "https://cards.scryfall.io" in r.headers["content-security-policy"], path
    finally:
        await b.aclose()
    r = await stack.h.http.get("/signed-out")  # a page rendered without a session
    assert r.status_code == 200
    assert "<form class='topsearch'" not in r.text and "sitesearch.js" not in r.text


async def test_enter_on_plain_text_is_the_deck_search_and_the_deck_search_links_to_cards(
    stack: Stack,  # noqa: F811
) -> None:
    b = await linked(stack)
    try:
        r = await b.http.get("/search?q=Aesi")  # what the top bar's form sends
        assert r.status_code == 200
        assert "Decks matching Aesi" in r.text
        assert "value='Aesi' placeholder='Any part of the name'" in r.text
        assert "href='/cards?q=Aesi'" in r.text
        r = await b.http.get("/search", params={"commander": "Aesi, Tyrant of Gyre Strait"})
        assert r.status_code == 200
        assert "href='/cards?q=Aesi%2C+Tyrant+of+Gyre+Strait'" in r.text
        r = await b.http.get("/search")
        assert "href='/cards'" in r.text
    finally:
        await b.aclose()


@pytest.mark.parametrize("raw,expect", [(" sol   ring ", "sol ring"), ("x" * 300, "x" * 100), (None, "")])
def test_clean_query(raw: str | None, expect: str) -> None:
    from mtg_gateway.cardsearch import clean_query

    assert clean_query(raw) == expect


# -- the card text the viewer reads on demand (/cards/api/text) ---------------------------------
def _named_calls(st: Stack) -> int:
    return sum(1 for _m, u in st.sf.requests if "/cards/named" in u)


async def test_card_text_needs_a_sign_in_and_a_name(stack: Stack) -> None:  # noqa: F811
    r = await stack.h.http.get("/cards/api/text?name=Sol%20Ring")
    assert r.status_code == 401 and r.json()["error"] == "unauthenticated"
    b = await linked(stack)
    try:
        r = await b.http.get("/cards/api/text?name=s")
        assert r.status_code == 400 and r.json()["error"] == "invalid"
    finally:
        await b.aclose()


async def test_card_text_is_read_once_from_scryfall_and_kept(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        n = _named_calls(stack)
        r = await b.http.get("/cards/api/text", params={"name": "Sol Ring"})
        assert r.status_code == 200, r.text
        card = r.json()["card"]
        assert card["name"] == "Sol Ring" and "{T}: Add {C}{C}" in card["text"]
        assert "commander" in card["legal"].split(",") and card["faces"] == [] and card["pt"] == ""
        assert card["artist"] and card["price"]
        assert r.headers["cache-control"] == "private, max-age=3600"
        assert _named_calls(stack) == n + 1
        # the same card again (any spelling of the spaces and case): the cache answers
        r = await b.http.get("/cards/api/text", params={"name": "  sol  RING "})
        assert r.status_code == 200 and r.json()["card"]["name"] == "Sol Ring"
        assert _named_calls(stack) == n + 1
        # the persistent slim cache is untouched: nothing of the card went in there
        assert not any(k.startswith("named:") for k in stack.h.app.state.gateway.scan.scryfall.cards._items)
    finally:
        await b.aclose()


async def test_a_card_with_two_faces_carries_both(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        r = await b.http.get("/cards/api/text", params={"name": "Fire // Ice"})
        assert r.status_code == 200, r.text
        card = r.json()["card"]
        assert [f["name"] for f in card["faces"]] == ["Fire", "Ice"]
        assert all(f["text"] and f["type"] == "Instant" and f["mana"] for f in card["faces"])
        r = await b.http.get("/cards/api/text", params={"name": "Mountain Goat"})
        assert r.status_code == 200 and r.json()["card"]["pt"] == "1/1"
    finally:
        await b.aclose()


async def test_concurrent_openings_share_one_scryfall_request(stack: Stack) -> None:  # noqa: F811
    import asyncio

    b = await linked(stack)
    try:
        n = _named_calls(stack)
        answers = await asyncio.gather(
            *(b.http.get("/cards/api/text", params={"name": "Cultivate"}) for _ in range(4))
        )
        assert all(r.status_code == 200 for r in answers), [r.status_code for r in answers]
        assert {r.json()["card"]["name"] for r in answers} == {"Cultivate"}
        assert _named_calls(stack) == n + 1
    finally:
        await b.aclose()


async def test_an_unknown_card_is_not_found_and_not_kept(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        r = await b.http.get("/cards/api/text", params={"name": "No Such Card"})
        assert r.status_code == 404 and r.json()["error"] == "not_found"
        assert len(stack.h.app.state.gateway.card_texts) == 0
    finally:
        await b.aclose()


async def test_text_cache_is_bounded_and_expires() -> None:
    import asyncio

    from mtg_gateway.cardsearch import TextCache

    calls: list[str] = []

    async def source(name: str) -> dict:
        calls.append(name)
        return {"name": name, "oracle_text": "x"}

    cache = TextCache(ttl=0.05, max_items=2)
    for name in ("A", "B", "C"):
        assert (await cache.fetch(name, source))["name"] == name
    assert len(cache) == 2 and cache.get("A") is None and cache.get("C")  # the oldest went
    assert (await cache.fetch("B", source)) and calls == ["A", "B", "C"]  # B from the cache
    await asyncio.sleep(0.06)
    assert cache.get("B") is None  # expired
    await cache.fetch("B", source)
    assert calls == ["A", "B", "C", "B"]

    async def failing(name: str) -> dict:
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        await cache.fetch("D", failing)
    assert cache.get("D") is None and "d" not in cache._inflight
