"""The in-memory card-name catalog behind typed suggestions (scan/names.py): ranking, accents,
background loading and the Scryfall fallback while it loads."""

from __future__ import annotations

import asyncio

import pytest

from mtg_gateway.scan.names import TOKEN_NAMES, NameCatalog, fold, rank
from mtg_gateway.scan.scryfall import ScryfallClient
from mtg_gateway.scan.service import ScanService

from .fake_scryfall import EXTRA_LAYOUTS, FakeScryfall

NAMES = [
    "Shelob, Child of Ungoliant",
    "Shelob, Dread Weaver",
    "Shelob's Ambush",
    "Lim-Dûl the Necromancer",
    "Sol Ring",
    "Solemn Simulacrum",
    "Unsol Ring Of Fire",
    "Marisol Fighter",
    "Fire // Ice",
]


def test_fold_drops_accents_case_and_joiners() -> None:
    assert fold("Lim-DÛL's Vault") == "lim dul s vault"
    assert fold("  Sol   Ring ") == "sol ring"
    assert fold("Shelob, Child") == "shelob child"


def test_rank_prefers_a_later_word_start_over_an_earlier_inside_hit() -> None:
    assert rank("sunscape apprentice", "ap") == 1
    assert rank("apprentice wizard", "ap") == 0
    assert rank("zap", "ap") == 2
    assert rank("zap", "zz") is None


def test_catalog_close_cancels_a_running_download() -> None:
    async def run() -> None:
        started = asyncio.Event()

        class Slow:
            async def catalog_card_names(self) -> list[str]:
                started.set()
                await asyncio.sleep(60)
                return []

        cat = NameCatalog(lambda: Slow())
        cat.ensure()
        await started.wait()
        task = cat._task
        cat.close()
        await asyncio.sleep(0)
        assert task is not None and task.cancelled()

    asyncio.run(run())


def test_suggest_ranks_prefix_then_word_start_then_inside() -> None:
    cat = NameCatalog(lambda: None)
    cat.set_names(NAMES)
    assert cat.suggest("sol") == [
        "Sol Ring",
        "Solemn Simulacrum",
        "Marisol Fighter",
        "Unsol Ring Of Fire",
        # the token names come after every card (0.7.46)
        "Human Soldier",
        "Soldier",
    ]
    assert cat.suggest("shelo") == ["Shelob, Child of Ungoliant", "Shelob, Dread Weaver", "Shelob's Ambush"]
    assert cat.suggest("lim-dul") == ["Lim-Dûl the Necromancer"]
    assert cat.suggest("lim dul") == ["Lim-Dûl the Necromancer"]
    assert cat.suggest("shelob child") == ["Shelob, Child of Ungoliant"]
    assert cat.suggest("ice") == ["Fire // Ice"]
    assert cat.suggest("s") == []  # too short
    assert cat.suggest("zzz") == []
    assert cat.suggest("sol", limit=2) == ["Sol Ring", "Solemn Simulacrum"]


def test_suggest_is_none_until_loaded() -> None:
    assert NameCatalog(lambda: None).suggest("sol") is None


def _service(sf: FakeScryfall) -> ScanService:
    from mtg_gateway.db import Database

    db = Database(":memory:")
    service = ScanService(db, ScryfallClient("https://scryfall.test", min_interval=0.0, http=sf.client()))
    return service


def test_suggest_falls_back_to_scryfall_until_the_catalog_loads() -> None:
    sf = FakeScryfall()  # no catalog: /catalog/card-names answers 404

    async def run() -> list[str]:
        service = _service(sf)
        names = await service.suggest("sol r")
        await asyncio.sleep(0.05)  # the background load fails and is retried later, not now
        return names

    names = asyncio.run(run())
    assert names and all(isinstance(n, str) for n in names)
    assert any("/cards/autocomplete" in u for _m, u in sf.requests)
    assert any("/catalog/card-names" in u for _m, u in sf.requests)


def test_suggest_answers_from_the_catalog_without_scryfall() -> None:
    sf = FakeScryfall()
    sf.catalog = NAMES + [f"Filler Card {i}" for i in range(1200)]

    async def run() -> tuple[list[str], list[str], int]:
        service = _service(sf)
        first = await service.suggest("shelo")  # starts the load; answered by Scryfall this once
        for _ in range(50):
            await asyncio.sleep(0.01)
            if service.names.loaded:
                break
        before = len(sf.requests)
        second = await service.suggest("shelo")
        third = await service.suggest("Shelob, D")
        return first, second + third, len(sf.requests) - before

    first, later, extra = asyncio.run(run())
    assert later[:3] == ["Shelob, Child of Ungoliant", "Shelob, Dread Weaver", "Shelob's Ambush"]
    assert "Shelob, Dread Weaver" in later
    assert extra == 0, "catalog answers cost no Scryfall request"


def test_catalog_download_is_validated() -> None:
    sf = FakeScryfall()
    sf.catalog = ["only", "a", "few"]

    async def run() -> bool:
        client = ScryfallClient("https://scryfall.test", min_interval=0.0, http=sf.client())
        cat = NameCatalog(lambda: client)
        return await cat.load()

    assert asyncio.run(run()) is False


@pytest.mark.parametrize("query", ["", " ", "a"])
def test_short_queries_answer_nothing(query: str) -> None:
    cat = NameCatalog(lambda: None)
    cat.set_names(NAMES)
    assert cat.suggest(query) == []


def test_a_longer_text_searches_only_the_names_the_shorter_one_matched() -> None:
    """Each keystroke narrows the last keystroke's matches; the answers are the same as a
    scan of the whole catalog, in the same order."""
    import random

    rnd = random.Random(7)
    words = [
        "".join(rnd.choice("abcdeilnorst") for _ in range(rnd.randint(3, 8))).title() for _ in range(800)
    ]
    names = [" ".join(rnd.choice(words) for _ in range(rnd.randint(1, 4))) for _ in range(6000)]
    narrowed = NameCatalog(lambda: None)
    narrowed.set_names(names)

    def whole(q: str) -> list[str]:
        fresh = NameCatalog(lambda: None)
        fresh.set_names(names)
        return fresh.suggest(q) or []

    for text in ("sol", "solemn", "an ab", "tri", "trice", "zz"):
        for k in range(2, len(text) + 1):
            assert narrowed.suggest(text[:k]) == whole(text[:k]), text[:k]
    # backspacing and retyping reuses the kept answer; a new catalog forgets them all
    assert "so" in narrowed._hits and "sol" in narrowed._hits
    narrowed.set_names(names[:10])
    assert not narrowed._hits
    assert narrowed.suggest("zz") == whole_small(names[:10], "zz")


def whole_small(names: list[str], q: str) -> list[str]:
    c = NameCatalog(lambda: None)
    c.set_names(names)
    return c.suggest(q) or []


def test_token_names_are_suggested_beside_the_catalog() -> None:
    """Scryfall's name catalog leaves tokens out, so Add to binder, Watch a card and Add to deck
    never offered "Treasure" or "Soldier" (0.7.46): a list of token names sits beside it, after
    the cards that match unless the token is typed in full."""
    cat = NameCatalog(lambda: None)
    cat.set_names([*NAMES, "Treasure Map"])
    assert cat.suggest("treas") == ["Treasure Map", "Treasure"]  # cards first
    assert cat.suggest("treasure") == ["Treasure", "Treasure Map"]  # the token typed in full
    assert "Food" in cat.suggest("food") and "Clue" in cat.suggest("clue")
    assert "Eldrazi Spawn" in cat.suggest("eldrazi sp")
    assert cat.known("Treasure") is True and cat.known("Not A Token") is False
    assert cat.size == len(NAMES) + 1 + len(set(TOKEN_NAMES) - {*NAMES, "Treasure Map"})


def test_unknown_names_without_the_catalog_cost_about_two_lookups_each() -> None:
    """The catalog's load failed: every recovery step is a paced Scryfall request. Six names that
    are no card cost 38 requests in this fake (25 and 12 s in the final audit of 0.7.46), each
    walking its misread variants and shorter prefixes. Once two walks found nothing, the call's
    other names keep their own lookup and suggestions but skip the walk; and a name that skipped
    it is not remembered as a miss, so it is tried in full once the catalog is there."""
    from mtg_gateway.scan.service import MAX_FRUITLESS_RECOVERIES_WITHOUT_CATALOG, CardInput

    sf = FakeScryfall()  # no catalog: /catalog/card-names answers 404
    names = ["Xqzv Plorth", "Blorf Gandu", "Qwmx Tarrel", "Zzyx Vombat", "Hrrk Pellum", "Fnord Quillip"]

    async def run() -> tuple[list[str], ScanService]:
        service = _service(sf)
        out = await service.resolve([CardInput(n) for n in names])
        return [r.status for r in out], service

    statuses, service = asyncio.run(run())
    assert statuses == ["not_found"] * 6
    lookups = [u for _m, u in sf.requests if "/cards/named" in u or "/cards/autocomplete" in u]
    walked = 2 * len(names) + MAX_FRUITLESS_RECOVERIES_WITHOUT_CATALOG * 4  # a walk: up to 4 here
    assert len(lookups) <= walked, (len(lookups), lookups)
    for later in names[MAX_FRUITLESS_RECOVERIES_WITHOUT_CATALOG:]:
        assert sum(f"={later.replace(' ', '+')}" in u for u in lookups) == 2, later  # its own two
    # the two that walked are remembered as misses; the ones that skipped the walk are not
    remembered = [n for n in names if service._missed(CardInput(n)) is not None]
    assert remembered == names[:MAX_FRUITLESS_RECOVERIES_WITHOUT_CATALOG]


def test_misses_without_the_catalog_are_remembered_until_it_loads() -> None:
    """10a of the 0.7.46 audit: without the catalog, six unknown names cost 20 requests and 9.6 s,
    and the same six again cost 7 requests and 3.1 s, since the names that skipped the walk were
    not remembered. They are now remembered for MISS_TTL_WITHOUT_CATALOG (about ten minutes) while
    the catalog is not loaded: a repeat within that time asks Scryfall nothing. Once the catalog
    loads, that memory is dropped and those names are asked again, so the catalog can place them."""
    from mtg_gateway.scan import service as svc
    from mtg_gateway.scan.service import MAX_FRUITLESS_RECOVERIES_WITHOUT_CATALOG, CardInput

    assert 300 <= svc.MISS_TTL_WITHOUT_CATALOG <= 900  # about ten minutes
    sf = FakeScryfall()  # no catalog: /catalog/card-names answers 404
    names = ["Xqzv Plorth", "Blorf Gandu", "Qwmx Tarrel", "Zzyx Vombat", "Hrrk Pellum", "Fnord Quillip"]
    skipped = names[MAX_FRUITLESS_RECOVERIES_WITHOUT_CATALOG:]

    async def run() -> tuple[list[str], int, list[str]]:
        service = _service(sf)
        first = await service.resolve([CardInput(n) for n in names])
        assert [r.status for r in first] == ["not_found"] * 6
        n0 = len(sf.requests)
        again = await service.resolve([CardInput(n) for n in names])
        repeat = len(sf.requests) - n0
        # the catalog loads (its daily load, or a retry after a failed one)
        service.names.set_names(sf.names_catalog())
        n1 = len(sf.requests)
        await service.resolve([CardInput(n) for n in names])
        asked = [u for _m, u in sf.requests[n1:]]
        return [r.status for r in again], repeat, asked

    statuses, repeat, asked = asyncio.run(run())
    assert statuses == ["not_found"] * 6
    assert repeat == 0, f"a repeat within ten minutes made {repeat} Scryfall requests"
    for name in skipped:  # forgotten once the catalog loaded: asked again
        assert any(name.replace(" ", "+") in u for u in asked), (name, asked)


def test_a_miss_without_the_catalog_is_forgotten_after_its_time() -> None:
    """A name that skipped the walk is asked again once its MISS_TTL_WITHOUT_CATALOG is over, even
    with the catalog still not loaded."""
    import time

    from mtg_gateway.scan import service as svc
    from mtg_gateway.scan.service import MAX_FRUITLESS_RECOVERIES_WITHOUT_CATALOG, CardInput

    sf = FakeScryfall()
    names = ["Xqzv Plorth", "Blorf Gandu", "Qwmx Tarrel", "Zzyx Vombat"]
    skipped = names[MAX_FRUITLESS_RECOVERIES_WITHOUT_CATALOG:]

    async def run() -> list[str]:
        service = _service(sf)
        assert service.down_misses.ttl == svc.MISS_TTL_WITHOUT_CATALOG
        await service.resolve([CardInput(n) for n in names])
        assert len(service.down_misses) == len(skipped)
        # their time is over
        past = time.monotonic() - 1
        service.down_misses._items = {k: (past, v) for k, (_t, v) in service.down_misses._items.items()}
        n0 = len(sf.requests)
        await service.resolve([CardInput(n) for n in names])
        return [u for _m, u in sf.requests[n0:]]

    asked = asyncio.run(run())
    for name in skipped:
        assert any(name.replace(" ", "+") in u for u in asked), (name, asked)


def test_unknown_names_without_scryfalls_catalog_use_the_printings_copys_names(tmp_path) -> None:
    """B-4 of the 0.7.46 sweep: with Scryfall's catalog not loaded, six unknown names still cost 20
    paced Scryfall requests and 9.5 s, each walking misread variants and shorter prefixes. The
    printings copy (on by default) holds every card's name, so it stands in for the catalog until
    Scryfall's own loads: the six cost what they cost with the catalog, one lookup each and no
    autocomplete, a misread is still recovered, and Scryfall's catalog is tried again later."""
    import gzip
    import json
    import time

    from mtg_gateway.scan.bulk import PrintingsCopy
    from mtg_gateway.scan.service import CardInput

    sf = FakeScryfall()  # no catalog: /catalog/card-names answers 404
    body = gzip.compress("".join(json.dumps(c) + "\n" for c in sf.cards).encode())
    path = "/default-cards/default-cards-20261103220649.jsonl.gz"
    sf.bulk_files = {path: body}
    sf.bulk_index = [
        {
            "object": "bulk_data",
            "type": "default_cards",
            "updated_at": "2026-11-03T22:06:49.499+00:00",
            "jsonl_download_uri": f"https://data.scryfall.io{path}",
            "compressed_size": len(body),
        }
    ]
    names = ["Xqzv Plorth", "Blorf Gandu", "Qwmx Tarrel", "Zzyx Vombat", "Hrrk Pellum", "Fnord Quillip"]

    async def run() -> tuple[list[str], list[str], ScanService, list[str], str | None]:
        service = _service(sf)
        service.copy = PrintingsCopy(tmp_path, lambda: service.scryfall)
        service.copy.min_printings = 1
        assert await service.copy.refresh()
        listed = service.copy.card_names_now()
        n0 = len(sf.requests)
        out = await service.resolve([CardInput(n) for n in names])
        asked = [u for _m, u in sf.requests[n0:]]
        misread = await service.resolve([CardInput("Sol Rlng")])
        got = misread[0].card and misread[0].card.get("name")
        return [r.status for r in out], asked, service, listed, got

    statuses, asked, service, listed, recovered = asyncio.run(run())
    assert statuses == ["not_found"] * 6
    # the copy lists the cards as Scryfall's catalog does: no token, emblem or other extra
    assert set(listed) == {c["name"] for c in sf.cards if c.get("layout") not in EXTRA_LAYOUTS}
    assert service.names.loaded and service.names.from_copy
    lookups = [u for u in asked if "/cards/named" in u or "/cards/autocomplete" in u]
    assert not any("/cards/autocomplete" in u for u in lookups), lookups
    assert len(lookups) <= len(names), lookups  # was 20 requests for these six
    assert sum("/catalog/card-names" in u for u in asked) == 1, asked  # Scryfall's was asked first
    assert recovered == "Sol Ring"
    # Scryfall's own catalog is tried again after a failure's wait, and replaces the copy's names
    sf.catalog = sf.names_catalog()
    service.names._next_try = time.monotonic() - 1

    async def later() -> bool:
        service.names.ensure()
        assert service.names._task is not None
        await service.names._task
        return service.names.from_copy

    assert asyncio.run(later()) is False
    assert service.names.known("Filler Card 3") is True
