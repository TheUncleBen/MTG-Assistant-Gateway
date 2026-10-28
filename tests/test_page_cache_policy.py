"""Back is instant (owner decision C1, 0.7.46): ordinary pages are ``private, no-cache``, which a
browser may keep in its back/forward cache but must ask again before any other use. Pages that
carry the sign-in, the Archidekt link, connected apps, Admin, the assistant consent or the app's
install steps stay ``no-store``, as does any error or busy page; sign-out still clears site
storage."""

from __future__ import annotations

from mtg_gateway.theme import page_cache_control

from .conftest import Stack
from .test_navigation_speed import NAV, _linked


def test_the_policy_by_address() -> None:
    for path in ("/", "/decks", "/decks/42", "/collection", "/forum", "/users/amy", "/lists/templates"):
        assert page_cache_control(path, 200) == "private, no-cache", path
    for path in ("/account", "/account/details", "/admin", "/admin/users", "/login", "/logout"):
        assert page_cache_control(path, 200) == "no-store", path
    for path in ("/signed-out", "/authorize/confirm", "/install", "/app", "/data-deleted"):
        assert page_cache_control(path, 200) == "no-store", path
    assert page_cache_control("/decks/42", 503) == "no-store"
    assert page_cache_control("/decks/42", 404) == "no-store"


async def test_pages_say_so(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        for path in ("/", "/decks", "/decks/42", "/collection", "/proposals"):
            r = await b.http.get(path, headers=NAV)
            assert r.status_code == 200 and r.headers["cache-control"] == "private, no-cache", path
        for path in ("/account", "/account/details"):
            r = await b.http.get(path, headers=NAV)
            assert r.status_code == 200 and r.headers["cache-control"] == "no-store", path
        stack.ark.fail_deck_reads = True
        r = await b.http.get("/decks/43", headers=NAV)
        assert r.status_code >= 400 and r.headers["cache-control"] == "no-store"
        out = await b.http.post("/logout", data={"csrf": await b.csrf()})
        assert out.headers["clear-site-data"] == '"storage"'
    finally:
        await b.aclose()
