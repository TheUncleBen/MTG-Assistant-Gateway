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
    names = sorted(i["name"] for i in again["items"]) if "items" in again else None
    if names is not None:
        assert names == ["Command Tower", "Sol Ring"]
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
