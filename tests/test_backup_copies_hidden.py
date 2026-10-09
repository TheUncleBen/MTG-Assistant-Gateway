"""D-03: the deck copies the gateway makes on Archidekt before a change stay out of Home, My decks,
the JSON list and the assistant's deck list, and are listed under History instead (each linked to
Archidekt and to the deck it copied)."""

from __future__ import annotations

from mtg_gateway.archidekt import is_backup_name
from mtg_gateway.archidekt_csv import parse_export, to_deck_json

from .fake_archidekt import FIXTURE
from .test_browse_collection import NAV, Stack, api, linked, stack  # noqa: F401 - fixture


def test_backup_names_are_recognised() -> None:
    assert is_backup_name("Sample Commander Deck (backup 2026-10-01 10:00 UTC)")
    assert not is_backup_name("Sample Commander Deck")
    assert not is_backup_name("Backup plans (my deck)")


async def test_backup_copies_leave_the_lists_and_show_under_history(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    ark = stack.ark
    try:
        # a copy the gateway made earlier, moved out of the backup folder (root), still recognised
        # by its name; alice owns it
        cards = parse_export(FIXTURE.read_text(encoding="utf-8"))
        ark.decks[77] = to_deck_json(
            cards[:10],
            deck_id=77,
            name="Sample Commander Deck (backup 2026-10-01 10:00 UTC)",
            owner="alice",
            updated_at="2026-10-01T10:00:00Z",
        )
        ark.users["alice"]["decks"].append(77)
        # a hand edit makes one more copy, in the backup folder, recorded on its snapshot
        r = await api(
            b,
            "POST",
            "/api/v1/proposals",
            {
                "kind": "edit",
                "deck_id": "42",
                "changes": [{"action": "add", "card_name": "Cultivate", "quantity": 1}],
                "apply": True,
            },
        )
        assert r.status_code == 201 and r.json()["result"]["result"].get("backup_deck_id"), r.text
        copy_id = r.json()["result"]["result"]["backup_deck_id"]
        # My decks: only the real decks, with a note pointing at History
        page = await b.http.get("/decks", headers=NAV)
        assert page.status_code == 200
        assert "Sample Commander Deck" in page.text and "(backup 2026-10-01" not in page.text
        assert f"/decks/{copy_id}'" not in page.text
        assert "2 backup copies made before changes are kept out of this list" in page.text
        assert "/history#backups" in page.text
        # Home and the JSON list the pages fill themselves from
        home = await b.http.get("/", headers=NAV)
        assert "(backup " not in home.text
        mine = (await b.http.get("/api/decks/mine?shape=list")).json()
        assert all(not is_backup_name(d["name"]) and d["id"] != copy_id for d in mine["decks"])
        listed = (await b.http.get("/api/v1/decks")).json()
        ids = {d["id"] for d in listed["decks"]}
        assert "42" in ids and "77" not in ids and copy_id not in ids
        # History lists both copies, linked to Archidekt, the recorded one linked to its deck
        hist = await b.http.get("/history", headers=NAV)
        assert hist.status_code == 200 and "Backup copies on Archidekt (2)" in hist.text
        assert f"https://archidekt.com/decks/{copy_id}'" in hist.text
        assert "https://archidekt.com/decks/77'" in hist.text
        assert "of <a href='/decks/42'>this deck</a>" in hist.text
        # a deck's own history shows only that deck's copies; a filter hides the panel
        own = await b.http.get("/history?deck_id=42", headers=NAV)
        assert "Backup copies on Archidekt (2)" in own.text  # 77 by name, the new copy by record
        other = await b.http.get("/history?deck_id=43", headers=NAV)
        assert "Backup copies on Archidekt" not in other.text
        filtered = await b.http.get("/history?type=snapshots", headers=NAV)
        assert "Backup copies on Archidekt" not in filtered.text
    finally:
        await b.aclose()
