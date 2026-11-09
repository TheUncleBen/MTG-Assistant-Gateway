"""0.7.47: a token named "Goblin" is that token (owner, 2026-11-08: "as I was looking at goblin
token in different artwork variations ... I saw two goblin creatures").

Scryfall's own exact-name lookups ignore punctuation, so ``/cards/named?exact=Goblin`` and a
``/cards/collection`` entry ``{"name": "Goblin"}`` answer with "_____ Goblin" (an Unfinity card with
two printings), and neither finds tokens at all. The gateway now takes a card for an exact name only
when it has that very name (scryfall.same_name), and otherwise looks for the name among tokens and
emblems too (``!"name"`` with ``include_extras``)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from mtg_gateway.scan.scryfall import ScryfallClient, same_name

UNF_GOBLIN = {
    "object": "card",
    "id": "0e2b2c3e-0000-4000-8000-000000000001",
    "oracle_id": "88222fd2-8316-426c-8218-64f6be5ca0f8",
    "name": "_____ Goblin",
    "layout": "normal",
    "type_line": "Creature — Goblin Guest",
    "set": "unf",
    "collector_number": "107",
}
GOBLIN_TOKEN = {
    "object": "card",
    "id": "0e2b2c3e-0000-4000-8000-000000000002",
    "oracle_id": "11111111-2222-4333-8444-555555555555",
    "name": "Goblin",
    "layout": "token",
    "type_line": "Token Creature — Goblin",
    "set": "tdmu",
    "collector_number": "9",
}


def scryfall_like(seen: list[str]) -> httpx.MockTransport:
    """Scryfall as it answers today: named and collection ignore the underscores and know no
    tokens; a search with include_extras finds the token."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path + ("?" + request.url.query.decode() if request.url.query else ""))
        if request.url.path == "/cards/named":
            return httpx.Response(200, json=UNF_GOBLIN)
        if request.url.path == "/cards/collection":
            body: dict[str, Any] = json.loads(request.content)
            found = [UNF_GOBLIN for _ in body["identifiers"]]
            return httpx.Response(200, json={"object": "list", "data": found, "not_found": []})
        if request.url.path == "/cards/search":
            q = request.url.params.get("q", "")
            if q == '!"Goblin"' and request.url.params.get("include_extras") == "true":
                return httpx.Response(200, json={"object": "list", "data": [UNF_GOBLIN, GOBLIN_TOKEN]})
            return httpx.Response(404, json={"object": "error", "details": "No cards found."})
        return httpx.Response(404, json={"object": "error", "details": "?"})

    return httpx.MockTransport(handler)


def client(seen: list[str]) -> ScryfallClient:
    return ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=scryfall_like(seen), base_url="https://scryfall.test"),
    )


def test_same_name_counts_every_character_but_case_and_accents() -> None:
    assert not same_name("Goblin", "_____ Goblin")
    assert same_name("goblin", "Goblin")
    assert same_name("Jotun Grunt", "Jötun Grunt")
    assert same_name("Urza's Saga", "Urza’s Saga")
    assert same_name("Delver of Secrets", "Delver of Secrets // Insectile Aberration")
    assert same_name("Fire // Ice", "Fire // Ice")
    assert not same_name("", "Goblin")


async def test_an_exact_name_finds_the_token_not_the_unfinity_card() -> None:
    seen: list[str] = []
    sf = client(seen)
    card = await sf.named("Goblin")
    assert card["name"] == "Goblin" and card["oracle_id"] == GOBLIN_TOKEN["oracle_id"]
    raw = await sf.named_raw("Goblin")  # the card viewer's text
    assert raw["name"] == "Goblin" and raw["layout"] == "token"
    # the Unfinity card is still found by its own name
    assert (await sf.named("_____ Goblin"))["oracle_id"] == UNF_GOBLIN["oracle_id"]
    await sf.aclose()


async def test_the_printing_pickers_peek_finds_the_token(tmp_path) -> None:
    from mtg_gateway.scan.service import ScanService

    seen: list[str] = []
    service = ScanService.__new__(ScanService)
    service.scryfall = client(seen)
    service._lookup_slot = lambda owner: _Null()  # type: ignore[method-assign]
    cards = await service.peek(["Goblin"])
    assert [c["name"] for c in cards] == ["Goblin"]
    assert cards[0]["oracle_id"] == GOBLIN_TOKEN["oracle_id"]
    # asked again: the cache answers, and still with the token
    n = len(seen)
    assert [c["name"] for c in await service.peek(["Goblin"])] == ["Goblin"]
    assert len(seen) == n
    await service.scryfall.aclose()


class _Null:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None
