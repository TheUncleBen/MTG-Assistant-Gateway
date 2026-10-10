"""D-03: the deck copies the gateway makes on Archidekt before a change stay out of Home, My decks,
the JSON list and the assistant's deck list, and are listed under History instead (each linked to
Archidekt and to the deck it copied)."""

from __future__ import annotations

from mtg_gateway.archidekt import is_backup_name
from mtg_gateway.archidekt_csv import parse_export, to_deck_json
from mtg_gateway.decks import mark_gone

from .fake_archidekt import FIXTURE
from .test_browse_collection import NAV, Stack, api, linked, stack  # noqa: F401 - fixture


def test_mark_gone_judges_only_what_the_list_can_know() -> None:
    def snaps() -> list[dict]:
        return [
            {"deck_id": "42", "backup_deck_id": "100", "taken_at": 1000},  # copy deleted
            {"deck_id": "43", "backup_deck_id": None, "taken_at": 1000},  # deck deleted
            {"deck_id": "44", "backup_deck_id": "101", "taken_at": 2000},  # newer than the list
        ]

    listed = {"ids": {"42", "44"}, "read_at": 1500.0}
    a, b, c = mark_gone(snaps(), listed)
    assert a.get("backup_gone") and not a.get("deck_gone")
    assert b.get("deck_gone") and not b.get("backup_gone")
    assert not c.get("deck_gone") and not c.get("backup_gone")
    # no list that can tell (cold, read by username, cut off): nothing is marked
    assert not any(x.get("deck_gone") or x.get("backup_gone") for x in mark_gone(snaps(), None))


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


async def test_history_reconciles_with_what_is_still_on_archidekt(stack: Stack) -> None:  # noqa: F811
    """T-074: backup copies (and decks) deleted on Archidekt are noticed from the deck list the
    gateway already reads: a deleted copy leaves the panel and its snapshot says so (Restore still
    works, from the snapshot kept here); a deleted deck's snapshot offers no Restore, and a restore
    asked anyway fails with a clear message. No request is sent per copy."""
    b = await linked(stack)
    ark, svc = stack.ark, stack.h.app.state.gateway.decks
    try:
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
        assert r.status_code == 201, r.text
        copy_id = r.json()["result"]["result"]["backup_deck_id"]
        snap_id = (await b.http.get("/api/v1/decks/42/history")).json()["snapshots"][0]["snapshot_id"]
        restore_form = f"name='snapshot_id' value='{snap_id}'"
        hist = await b.http.get("/history", headers=NAV)
        assert "Backup copies on Archidekt (1)" in hist.text and "Archidekt backup copy</a>" in hist.text
        assert restore_form in hist.text

        # the owner deletes the backup copy on archidekt.com; the cached list has aged
        del ark.decks[int(copy_id)]
        svc.deck_lists.fresh = 0
        ark.calls.clear()
        hist = await b.http.get("/history", headers=NAV)
        assert "Backup copies on Archidekt" not in hist.text and "Archidekt backup copy</a>" not in hist.text
        assert "no longer on Archidekt (deleted there)" in hist.text
        assert restore_form in hist.text  # Restore uses the gateway's snapshot, not the copy
        reads = [p for m, p in ark.calls if m == "GET"]
        assert all(p.startswith("/api/decks/v3/") for p in reads), reads  # only the deck list
        csrf = await b.csrf("/history")
        r = await b.http.post("/history/restore", data={"csrf": csrf, "snapshot_id": snap_id})
        assert r.status_code == 303 and r.headers["location"].startswith("/proposals/")

        # the deck itself is deleted on Archidekt: its snapshot cannot be put back onto it
        del ark.decks[42]
        hist = await b.http.get("/history", headers=NAV)
        assert "The deck is no longer on Archidekt." in hist.text and restore_form not in hist.text
        r = await b.http.post("/history/restore", data={"csrf": csrf, "snapshot_id": snap_id})
        assert r.status_code == 400
        assert "Deck 42 is no longer on your Archidekt account" in r.text and "Nothing was changed" in r.text
    finally:
        await b.aclose()
