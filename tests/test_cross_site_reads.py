"""The signed-in reads that ask Archidekt (My decks' folder and deck boxes, deck search's Load
more, the deck list, tag suggestions, social and collection reads, the JSON API with the browser's
sign-in, deck downloads) or Scryfall (card search) answer only the gateway's own pages: a link or
script on another site that opens one with the member's cookie is refused before anything is
asked, so it can't spend the member's allowance."""

from __future__ import annotations

import pytest

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED
from .test_decks_and_proxy import Browser, linked_user
from .test_folder_move_delete import _setup

stack_options = stack_with(SHIPPED)

READS = [
    "/decks/api/folders",
    "/decks/api/folders/{commander}/contents",
    "/decks/api/decks/42/delete-info",
    "/decks/api/decks/42/export",
    "/search/api/decks?q=sol",
    "/precons/api/sets",
    "/api/decks/mine",
    "/decks/api/tags?q=ra",
    "/social/api/users/1/follow",
    "/social/api/decks/42/comments",
    "/collection/api/summary",
    "/api/v1/decks",
    "/api/v1/decks/42",
    "/decks/42/export.txt",
    "/decks/42/export.json",
    "/decks/42/export.csv",
    "/decks/42/edhplay.json",
    "/cards/api/search?q=sol",
]

CROSS_SITE = [
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    # a browser that sends no Sec-Fetch-Site, from another site's page
    {"Referer": "https://elsewhere.example/page"},
    {"Origin": "https://elsewhere.example"},
]


@pytest.mark.parametrize("headers", CROSS_SITE)
async def test_a_read_opened_from_another_site_is_refused_before_archidekt_is_asked(
    stack: Stack, headers: dict[str, str]
) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    b = Browser(stack.h)
    await b.login()
    try:
        before = len(stack.ark.calls)
        for path in READS:
            r = await b.http.get(path.format(**ids), headers=headers)
            assert r.status_code == 403, (path, r.status_code, r.text)
            assert r.headers.get("cache-control") == "no-store", path
        assert stack.ark.calls[before:] == []
    finally:
        await b.aclose()


async def test_the_gateway_s_own_pages_and_a_typed_address_still_read(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    b = Browser(stack.h)
    await b.login()
    try:
        for headers in ({"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {}):
            r = await b.http.get("/decks/api/folders", headers=headers)
            assert r.status_code == 200, r.text
            r = await b.http.get(f"/decks/api/folders/{ids['precons']}/contents", headers=headers)
            assert r.status_code == 200, r.text
            r = await b.http.get("/api/decks/mine", headers=headers)
            assert r.status_code == 200, r.text
    finally:
        await b.aclose()


async def test_the_json_reads_refusals_are_never_kept_by_a_cache(stack: Stack) -> None:
    """L-2 (review 2026-10-22): the precons and deck-search JSON answer their refusals (signed
    out, Archidekt not answering) with no-store too, like their answers."""
    from mtg_gateway.decks import DeckError

    signed_out = Browser(stack.h)
    try:
        for path in ("/precons/api/sets", "/search/api/decks?q=sol"):
            r = await signed_out.http.get(path)
            assert r.status_code == 401 and r.headers.get("cache-control") == "no-store", path
    finally:
        await signed_out.aclose()
    await linked_user(stack)
    b = Browser(stack.h)
    await b.login()
    try:
        decks = stack.h.app.state.gateway.decks

        async def down(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
            raise DeckError("unavailable", "Archidekt is not answering right now.")

        decks.precons = down
        r = await b.http.get("/precons/api/sets")
        assert r.status_code == 503 and r.headers.get("cache-control") == "no-store", r.text
    finally:
        await b.aclose()
