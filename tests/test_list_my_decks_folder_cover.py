"""list_my_decks names a deck's folder and cover even when Archidekt's owner listing gives only
the folder's id and leaves its own automatic cover pick in ``featured``: the folder name comes from
the member's folder tree, the cover from ``customFeatured`` (what the gateway writes)."""

from __future__ import annotations

from typing import Any

from mtg_gateway.archidekt import art_url, list_row

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, structured

UID = "6511f317-bd38-46d0-b800-7125a3f420da"


def test_list_row_prefers_the_chosen_cover() -> None:
    row = list_row({"id": 1, "customFeatured": art_url(UID), "featured": ""})
    assert row["featured_scryfall_id"] == UID


async def test_list_my_decks_names_the_folder_from_its_id(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.folders["alice"] = [{"id": 5001, "name": "Brews", "private": False}]
    ark.deck_folder[42] = 5001
    real_row = ark._listing_row

    def row(d: dict[str, Any]) -> dict[str, Any]:
        out = real_row(d)
        out.pop("parentFolderName")  # the owner listing gives only the id
        return out

    ark._listing_row = row
    try:
        decks = structured(await call(h, token, "list_my_decks"))
        assert decks["ok"] and decks["decks"][0]["folder_id"] == 5001
        assert decks["decks"][0]["folder"] == "Brews"
        mine = structured(await call(h, token, "list_my_decks", {"folder": "Brews"}))
        assert [d["id"] for d in mine["decks"]] == ["42"]
    finally:
        ark._listing_row = real_row
