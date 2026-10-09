"""The in-memory card-name catalog behind typed suggestions (scan/names.py): ranking, accents,
background loading and the Scryfall fallback while it loads."""

from __future__ import annotations

import asyncio

import pytest

from mtg_gateway.scan.names import NameCatalog, fold, rank
from mtg_gateway.scan.scryfall import ScryfallClient
from mtg_gateway.scan.service import ScanService

from .fake_scryfall import FakeScryfall

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
    assert cat.suggest("sol") == ["Sol Ring", "Solemn Simulacrum", "Marisol Fighter", "Unsol Ring Of Fire"]
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
