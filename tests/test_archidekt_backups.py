"""Backup copies of a deck in a dedicated Archidekt folder (client functions only)."""

from __future__ import annotations

import pytest

from mtg_gateway.archidekt import BACKUP_FOLDER_NAME, ArchidektClient, ArchidektError, Pacer, backup_name
from tests.fake_archidekt import FakeArchidekt


async def _client() -> tuple[FakeArchidekt, ArchidektClient, str]:
    ark = FakeArchidekt()
    client = ArchidektClient("https://archidekt.com/api", "test", Pacer(0.0), http=ark.client())
    session = await client.login("alice", "pw-alice")
    return ark, client, session["access"]


def test_backup_name_is_readable() -> None:
    assert (
        backup_name("Sample Commander Deck", 1_791_160_000)
        == "Sample Commander Deck (backup 2026-10-05 00:26 UTC)"
    )


async def test_folder_is_created_once_then_found() -> None:
    ark, client, token = await _client()
    first = await client.ensure_folder(token)
    again = await client.ensure_folder(token)
    assert first == again and first["name"] == BACKUP_FOLDER_NAME
    assert ark.folders["alice"] == [{"id": int(first["id"]), "name": BACKUP_FOLDER_NAME, "private": True}]
    assert [c for c in ark.calls if c == ("POST", "/api/decks/folders/")] == [("POST", "/api/decks/folders/")]


async def test_backup_copy_keeps_every_card_and_lands_in_the_folder() -> None:
    ark, client, token = await _client()
    deck = await client.get_deck(token, "42")
    folder = (await client.ensure_folder(token))["id"]
    name = backup_name(deck.name, 1_791_160_000)
    made = await client.backup_deck(
        token, deck, name=name, folder_id=folder, description="Backup of deck 42 before proposal p1."
    )
    new_id = made["id"]
    assert made == {"id": new_id, "url": f"https://archidekt.com/decks/{new_id}", "name": name}
    copy = await client.get_deck(token, new_id)
    assert new_id != "42" and copy.name == name
    assert ark.deck_folder[int(new_id)] == int(folder) and int(new_id) in ark.private
    assert ark.decks[int(new_id)]["description"] == "Backup of deck 42 before proposal p1."

    def rows(d):  # noqa: ANN001, ANN202
        return sorted((c.name, c.quantity, tuple(sorted(c.categories)), c.modifier) for c in d.cards)

    assert rows(copy) == rows(deck) and copy.categories == deck.categories
    assert ark.decks[42]["name"] == "Sample Commander Deck"  # the original is untouched


async def test_backup_survives_a_failed_description_update() -> None:
    ark, client, token = await _client()
    ark.fail_deck_update = True
    deck = await client.get_deck(token, "42")
    folder = (await client.ensure_folder(token))["id"]
    new_id = (await client.backup_deck(token, deck, name="b", folder_id=folder, description="d"))["id"]
    assert ark.decks[int(new_id)]["description"] == "" and ark.deck_folder[int(new_id)] == int(folder)


async def test_copy_into_an_unknown_folder_is_refused() -> None:
    ark, client, token = await _client()
    deck = await client.get_deck(token, "42")
    with pytest.raises(ArchidektError) as err:
        await client.backup_deck(token, deck, name="b", folder_id="999")
    assert err.value.kind == "contract"


async def test_fail_backup_switch_makes_the_copy_fail() -> None:
    ark, client, token = await _client()
    ark.fail_backup = True
    deck = await client.get_deck(token, "42")
    folder = (await client.ensure_folder(token))["id"]
    with pytest.raises(ArchidektError) as err:
        await client.backup_deck(token, deck, name="b", folder_id=folder)
    assert err.value.kind == "unavailable" and len(ark.decks) == 2


async def test_listing_reports_the_backup_folder() -> None:
    ark, client, token = await _client()
    deck = await client.get_deck(token, "42")
    folder = (await client.ensure_folder(token))["id"]
    new_id = (await client.backup_deck(token, deck, name="b", folder_id=folder))["id"]
    body = await client._request("GET", "/decks/v3/", token=token, params={"ownerUsername": "alice"})
    rows = {str(r["id"]): (r["parentFolderId"], r["parentFolderName"]) for r in body["results"]}
    assert rows[new_id] == (int(folder), BACKUP_FOLDER_NAME) and rows["42"] == (None, None)
