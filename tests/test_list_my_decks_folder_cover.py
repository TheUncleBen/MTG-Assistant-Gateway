"""list_my_decks names a deck's folder and cover even when Archidekt's owner listing gives only
the folder's id and leaves its own automatic cover pick in ``featured``: the folder name comes from
the member's folder tree, the cover from ``customFeatured`` (what the gateway writes)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from mtg_gateway.archidekt import art_url, list_row
from mtg_gateway.decks import DeckError
from mtg_gateway.tools_decks import FOLDER_NAMES_UNREAD

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


async def _folder_by_id_only(stack: Stack) -> tuple[str, Any]:
    """A linked member whose deck 42 is in folder "Old", which the owner listing gives by id only;
    list_my_decks has read the list and the folder tree once. (token, the real listing row)"""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.folders["alice"] = [{"id": 5001, "name": "Old", "private": False}]
    ark.deck_folder[42] = 5001
    real_row = ark._listing_row

    def row(d: dict[str, Any]) -> dict[str, Any]:
        out = real_row(d)
        out.pop("parentFolderName")
        return out

    ark._listing_row = row
    first = structured(await call(h, token, "list_my_decks"))
    assert first["decks"][0]["folder"] == "Old" and "note" not in first, first
    return token, real_row


async def test_list_my_decks_reads_the_folder_tree_once_without_waiting_for_a_slot(stack: Stack) -> None:
    """With every Archidekt slot of the member busy, list_my_decks asks for the folder tree once,
    doesn't wait behind the member's other work for it, and says the folder names couldn't be
    read instead of answering a folder filter with a silent empty list."""
    h, ark = stack.h, stack.ark
    svc = stack.gw.decks
    token, real_row = await _folder_by_id_only(stack)
    reads = 0
    real_get = svc.folder_trees.get

    async def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal reads
        reads += 1
        return await real_get(*args, **kwargs)

    svc.folder_trees.get = counted
    svc.folder_trees.drop("user-1")  # the deck list is still fresh: only the tree is read
    svc.archidekt_slot_wait = 2.0
    svc._archidekt_in_flight["user-1"] = svc.max_archidekt_per_user
    try:
        started = time.monotonic()
        out = structured(await call(h, token, "list_my_decks", {"folder": "Old"}))
        assert reads == 1, reads
        assert time.monotonic() - started < 1.5, "waited for a slot"
        assert out["ok"] and out["count"] == 0 and out["note"] == FOLDER_NAMES_UNREAD, out
    finally:
        svc._archidekt_in_flight.pop("user-1", None)
        svc.folder_trees.get = real_get
        ark._listing_row = real_row


async def test_list_my_decks_never_names_folders_from_the_tree_as_last_read(stack: Stack) -> None:
    """While Archidekt is busy, the folder tree as last read (up to a day old) is for My decks,
    which says how old it is; the assistant gets no folder name from it, and a note."""
    h, ark = stack.h, stack.ark
    svc = stack.gw.decks
    token, real_row = await _folder_by_id_only(stack)
    ark.folders["alice"][0]["name"] = "Legacy"  # renamed on archidekt.com
    svc.folder_trees.drop("user-1")
    try:
        ark.inject = [(429, "60")]
        out = structured(await call(h, token, "list_my_decks"))
        assert out["ok"] and out["decks"][0]["folder"] is None, out
        assert out["note"] == FOLDER_NAMES_UNREAD, out
        # no time promise in it
        assert FOLDER_NAMES_UNREAD == (
            "Folder names couldn't be read from Archidekt just now, so some decks show no folder here."
        )
        ark.inject = [(429, "60")]
        with pytest.raises(DeckError):
            await svc.folder_view("user-1")
        ark.inject = [(429, "60")]
        held = await svc.folder_view("user-1", last_read_ok=True)  # My decks
        assert held is not None and [f["name"] for f in held["folders"] if f["depth"]] == ["Old"], held
    finally:
        ark.inject = []
        ark._listing_row = real_row
