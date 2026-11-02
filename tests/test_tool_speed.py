"""The slow assistant tools of the 0.7.46 review (save_scan_session, card_printings, watch_deck)
ask Scryfall and Archidekt only for what the gateway does not already hold, at the pace of a
member who is waiting."""

from __future__ import annotations

from typing import Any

from mtg_gateway.scan.scryfall import ScryfallClient

from .conftest import COLLECTION, Stack, stack_with
from .fake_scryfall import FakeScryfall
from .test_companion import NAV
from .test_decks_and_proxy import Browser, call, linked_user, structured

stack_options = stack_with(COLLECTION)


def _asked(sf: FakeScryfall, n: int) -> list[tuple[str, str]]:
    return sf.requests[n:]


async def test_the_resolver_asks_scryfall_once_for_a_card_it_read_today(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    first = structured(
        await call(h, token, "save_scan_session", {"name": "box", "text": "1 Sol Ring\n1 Command Tower"})
    )
    assert first["ok"], first
    n = len(stack.sf.requests)
    again = structured(
        await call(h, token, "save_scan_session", {"name": "box 2", "text": "2 sol ring\n1 Command Tower"})
    )
    assert again["ok"], again
    assert _asked(stack.sf, n) == []
    assert sorted(i["card"]["name"] for i in again["items"]) == ["Command Tower", "Sol Ring"], again
    # a card's printings by name: the name from the same kept answer, the printings from the
    # picker's own cache once read
    await call(h, token, "card_printings", {"name": "Sol Ring"})
    n = len(stack.sf.requests)
    out = structured(await call(h, token, "card_printings", {"name": "Sol Ring"}))
    assert out["ok"] and out["cards"], out
    assert _asked(stack.sf, n) == []


async def test_kept_cards_keep_the_identifiers_order_and_the_misses() -> None:
    sf = FakeScryfall()
    client = ScryfallClient("https://scryfall.test", min_interval=0.0, http=sf.client())
    try:
        found, missing = await client.collection([{"name": "Sol Ring"}], cached=True)
        assert [c["name"] for c in found] == ["Sol Ring"] and missing == []
        n = len(sf.requests)
        found, missing = await client.collection(
            [{"name": "Command Tower"}, {"name": "No Such Card Anywhere"}, {"name": "SOL RING"}], cached=True
        )
        assert [c["name"] for c in found] == ["Command Tower", "Sol Ring"]
        assert missing == [{"name": "No Such Card Anywhere"}]
        assert len(sf.requests) == n + 1  # Sol Ring was not asked again
        bodies = sf.collection_bodies[-1]
        assert {"name": "Sol Ring"} not in bodies and {"name": "SOL RING"} not in bodies
        # without ``cached`` (prices, the legality check) the card is asked for now
        n = len(sf.requests)
        await client.collection([{"name": "Sol Ring"}])
        assert len(sf.requests) == n + 1
    finally:
        await client.aclose()


async def test_watch_deck_uses_the_deck_page_read_and_the_batch_pace(stack: Stack, monkeypatch: Any) -> None:
    h = stack.h
    gw = stack.gw
    token = await linked_user(stack)
    b = Browser(h)  # the same member, already linked
    await b.login("/account")
    try:
        assert (await b.http.get("/decks/42", headers=NAV)).status_code == 200
        reads = [c for c in stack.ark.calls if c[0] == "GET" and c[1].rstrip("/").endswith("/decks/42")]
        paces: list[float | None] = []
        real = gw.scan.scryfall.collection

        async def spy(idents: list, *, interval: float | None = None, cached: bool = False) -> Any:
            paces.append(interval)
            return await real(idents, interval=interval, cached=cached)

        monkeypatch.setattr(gw.scan.scryfall, "lookup_interval", 0.5)
        monkeypatch.setattr(gw.scan.scryfall, "collection", spy)
        out = structured(await call(h, token, "watch_deck", {"deck_id": "42"}))
        assert out.get("ok") is not False, out
        again = [c for c in stack.ark.calls if c[0] == "GET" and c[1].rstrip("/").endswith("/decks/42")]
        assert again == reads  # the deck page's read, not a second one
        assert paces and all(p is None for p in paces), paces  # not the daily check's slow pace
    finally:
        await b.aclose()


# -- unknown names at the production pace (whole-application audit, row 5) --------------------------
UNKNOWN = [
    "Notacard Xyzzyplex",
    "Grimbly Vortaxe",
    "Quellmire Spoon",
    "Ztharn Wibble",
    "Plovak Mendrith",
    "Yorvex Q",
]


def _paced(stack: Stack, sf: FakeScryfall) -> ScryfallClient:
    """The scan service on a fake Scryfall at the shipped pace: 0.1 s between requests and 0.5 s
    between single-card lookups (scryfall_lookup_interval), as production runs it."""
    import dataclasses

    from mtg_gateway.config import Settings

    (lookup,) = [f.default for f in dataclasses.fields(Settings) if f.name == "scryfall_lookup_interval"]
    assert lookup == 0.5
    client = ScryfallClient(
        "https://scryfall.test", min_interval=0.1, lookup_interval=lookup, http=sf.client()
    )
    stack.gw.scan.scryfall = client
    return client


async def test_unknown_names_cost_one_lookup_each_at_the_real_pace(stack: Stack) -> None:
    """Six unknown names took 10.6 s at the production pace: each walked eight paced Scryfall
    requests (fuzzy, then six autocompletes on shorter and shorter prefixes). The completions now
    come from the card-name catalog the gateway keeps, OCR swaps it doesn't know are skipped, and a
    name that found nothing is remembered, so asking again costs no request at all."""
    import time

    sf = FakeScryfall()
    sf.catalog = sf.names_catalog()
    _paced(stack, sf)
    service = stack.gw.scan
    assert await service.names.load()
    token = await linked_user(stack)
    n = len(sf.requests)
    t0 = time.monotonic()
    out = structured(await call(stack.h, token, "resolve_cards", {"cards": [{"name": x} for x in UNKNOWN]}))
    took = time.monotonic() - t0
    assert [c["status"] for c in out["cards"]] == ["not_found"] * len(UNKNOWN), out
    asked = _asked(sf, n)
    # one batched collection read, then one fuzzy lookup per name: nothing else
    assert len(asked) == 1 + len(UNKNOWN), asked
    assert not any("/autocomplete" in url for _m, url in asked), asked
    assert took < 0.5 * len(UNKNOWN) + 1.5, took  # was 10.6 s
    # the same names again: no request, at once
    n = len(sf.requests)
    t0 = time.monotonic()
    again = structured(await call(stack.h, token, "resolve_cards", {"cards": [{"name": x} for x in UNKNOWN]}))
    assert [c["status"] for c in again["cards"]] == ["not_found"] * len(UNKNOWN), again
    assert _asked(sf, n) == [] and time.monotonic() - t0 < 0.5


async def test_unknown_names_before_the_catalog_has_loaded_wait_for_it_instead_of_walking(
    stack: Stack,
) -> None:
    """Right after a start the catalog has not loaded yet: six unknown names cost 25 paced Scryfall
    requests and 12 s, walking autocompletes for each name. The call now fetches the catalog (one
    request) and goes on as when it is loaded: one batched read and one fuzzy lookup per name."""
    import time

    sf = FakeScryfall()
    sf.catalog = sf.names_catalog()
    _paced(stack, sf)
    service = stack.gw.scan
    service.names = type(service.names)(lambda s=service: s.scryfall)  # a fresh start: not loaded
    assert not service.names.loaded
    token = await linked_user(stack)
    n = len(sf.requests)
    t0 = time.monotonic()
    out = structured(await call(stack.h, token, "resolve_cards", {"cards": [{"name": x} for x in UNKNOWN]}))
    took = time.monotonic() - t0
    assert [c["status"] for c in out["cards"]] == ["not_found"] * len(UNKNOWN), out
    asked = _asked(sf, n)
    assert sum("/catalog/card-names" in url for _m, url in asked) == 1, asked
    assert sum("/cards/collection" in url for _m, url in asked) == 1, asked
    assert not any("/autocomplete" in url for _m, url in asked), asked
    assert len(asked) == 2 + len(UNKNOWN), asked
    assert took < 0.5 * (1 + len(UNKNOWN)) + 1.5, took  # was 12 s


async def test_a_misread_name_is_still_recovered_at_the_real_pace(stack: Stack) -> None:
    """Cutting the lookups keeps what they recover: OCR misreads the resolver's tests know (a digit
    for a letter, l for i, a stray first letter, extra letters at the end) still find their card,
    with the catalog loaded and while it is not."""
    token = ""
    for catalog in (True, False):
        sf = FakeScryfall()
        if catalog:
            sf.catalog = sf.names_catalog()
        _paced(stack, sf)
        service = stack.gw.scan
        service.not_found = type(service.not_found)(ttl=3600, max_items=2000)
        service.names = type(service.names)(lambda s=service: s.scryfall)
        if catalog:
            assert await service.names.load()
        token = token or await linked_user(stack)
        out = structured(
            await call(
                stack.h,
                token,
                "resolve_cards",
                {
                    "cards": [
                        {"name": x} for x in ("S0l Ring", "Sol Rlng", "Cultlvate", "l Sol Ring", "Cultivatte")
                    ]
                },
            )
        )
        got = [(c["status"], (c.get("card") or {}).get("name")) for c in out["cards"]]
        assert got == [
            ("fuzzy", "Sol Ring"),
            ("fuzzy", "Sol Ring"),
            ("fuzzy", "Cultivate"),
            ("fuzzy", "Sol Ring"),
            ("fuzzy", "Cultivate"),
        ], (catalog, got)
