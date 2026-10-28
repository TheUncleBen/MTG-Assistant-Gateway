"""Split, adventure and two-faced cards named in full ("Fire // Ice") through the gateway's own
Scryfall collection lookups. Scryfall's POST /cards/collection knows such a card by its front face
only; the fake answers the same way (tests/fake_scryfall.py)."""

from __future__ import annotations

from mtg_gateway.scan.scryfall import ScryfallClient

from .fake_scryfall import FakeScryfall


async def test_collection_finds_a_two_part_card_by_its_full_name() -> None:
    sf = FakeScryfall()
    client = ScryfallClient("https://scryfall.test", min_interval=0.01, http=sf.client())
    found, missing = await client.collection(
        [{"name": "Fire // Ice"}, {"name": "Fire // Ice", "set": "dmr"}, {"name": "Notacard // Zzz"}]
    )
    assert [c["name"] for c in found] == ["Fire // Ice", "Fire // Ice"]
    # what was not found comes back as the caller asked for it, full name and all
    assert missing == [{"name": "Notacard // Zzz"}]
    assert {"name": "Fire"} in sf.collection_bodies[0]  # sent by its front face
    await client.aclose()
