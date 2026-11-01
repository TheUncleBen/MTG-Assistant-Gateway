"""0.7.46: the four Archidekt parity gaps built in this release.

* Archidekt's own change log of a deck, on its own page linked from the deck's History (labelled
  as Archidekt's) and through ``list_snapshots`` with ``archidekt``.
* The rows Archidekt keeps of cards taken out of a deck, removed for good: a destructive proposal
  only the member's press applies, on its review page, after the confirm box.
* The backup folder moved to the top level or renamed from the gateway: in the box a folder move or
  delete is refused in, and on Account details › Backup folder.
* Following someone with no public decks: found through their Archidekt profile page, and their
  gateway profile page shows a proper empty state with Follow.

The layouts of Archidekt's change log and profile page data are not recorded, so the fake's
answers are its own guesses (fake_archidekt.py says so); each is on the owner's live check."""

from __future__ import annotations

import pytest

from mtg_gateway import modes

from .conftest import Stack, stack_with
from .fake_archidekt import FakeArchidekt
from .test_approval_modes import SHIPPED
from .test_browse_collection import NAV
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .track_a_helpers import account_call

pytestmark = pytest.mark.anyio
SUB = "user-1"  # the signed-in member of linked_user


def _fake() -> FakeArchidekt:
    ark = FakeArchidekt()
    ark.private.discard(43)  # amy's deck is public
    ark.users["bo"] = {"password": "pw-bo", "id": 79, "decks": []}  # no decks at all
    return ark


stack_options = stack_with(SHIPPED.but(archidekt=_fake))


async def _browser(stack: Stack) -> tuple[Browser, str]:
    b = Browser(stack.h)
    await b.login()
    return b, await b.csrf()


async def _browser_apply(stack: Stack, pid: str) -> str:
    """The member's press on the review page's Apply; the redirect's error code, or ""."""
    b = Browser(stack.h)
    try:
        await b.login()
        url = f"/proposals/{pid}"
        r = await b.http.post(url, data={**await b.review_form(url), "action": "apply"})
        assert r.status_code == 303, r.text
        return r.headers["location"].partition("?err=")[2]
    finally:
        await b.aclose()


# -- following someone with no public decks ------------------------------------------------------
async def test_someone_with_no_public_decks_can_be_followed_by_name(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    res = await account_call(h, token, "propose_deck_social", {"action": "follow_user", "username": "bo"})
    p = structured(res)
    assert p["ok"] and p["rows"][0]["text"] == "Follow bo on Archidekt", p
    assert p["assistant_may_apply"] is False and p["risk"] in modes.MEMBER_ONLY
    assert ("/u/bo", {}) in ark.site_pages  # found on their Archidekt profile page
    assert await _browser_apply(stack, p["proposal_id"]) == ""
    assert 79 in ark.follows["alice"]
    # and unfollowed the same way
    res = await account_call(h, token, "propose_deck_social", {"action": "unfollow_user", "username": "BO"})
    p = structured(res)
    assert p["rows"][0]["text"] == "Stop following bo on Archidekt", p
    assert await _browser_apply(stack, p["proposal_id"]) == ""
    assert 79 not in ark.follows["alice"]
    # a name Archidekt has no user for is refused in plain words, nothing proposed
    out = structured(
        await account_call(
            h, token, "propose_deck_social", {"action": "follow_user", "username": "nobody-here"}
        )
    )
    assert out["ok"] is False and out["error"] == "not_found", out
    assert out["message"] == "No Archidekt user called nobody-here was found. Check the spelling.", out


async def test_an_unreadable_profile_page_still_unfollows_someone_already_followed(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.forum_page_changed = True  # archidekt.com's pages carry no data the gateway knows
    out = structured(
        await account_call(h, token, "propose_deck_social", {"action": "follow_user", "username": "bo"})
    )
    assert out["ok"] is False and out["error"] == "unavailable", out
    assert "https://archidekt.com/u/bo" in out["message"] and "follow" in out["message"]
    ark.follows["alice"] = {79}  # followed on archidekt.com: the following list knows them
    res = await account_call(h, token, "propose_deck_social", {"action": "unfollow_user", "username": "bo"})
    assert structured(res)["rows"][0]["text"] == "Stop following bo on Archidekt"


async def test_a_profile_with_no_public_decks_has_an_empty_state_and_follow(stack: Stack) -> None:
    await linked_user(stack)
    b = Browser(stack.h)
    await b.login("/users/bo")
    try:
        r = await b.http.get("/users/bo", headers=NAV)
        assert r.status_code == 200, r.text
        page = r.text
        assert "<h1 class='uname'>bo</h1>" in page and "<p class='sub'>Archidekt profile</p>" in page
        assert "data-social='follow' data-user='79' data-name='bo'" in page
        assert "<h2 id='pempty-h'>No public decks yet</h2>" in page
        assert "“bo” has no public decks on Archidekt yet. When they make one public, it shows here." in page
        assert "id='s-count'" not in page and "Sort by" not in page  # no results bar for nothing
        # a name Archidekt has no user for: said so, and nothing to follow
        r = await b.http.get("/users/nobody-here", headers=NAV)
        assert "<h2 id='pempty-h'>No such user</h2>" in r.text
        assert "No Archidekt user called “nobody-here” was found. Check the spelling." in r.text
        assert "<p class='sub'>No such user on Archidekt</p>" in r.text
        assert "data-social='follow'" not in r.text
    finally:
        await b.aclose()


def test_the_user_is_found_wherever_the_page_data_holds_them() -> None:
    from mtg_gateway.archidekt import find_user

    data = {"a": [{"id": 5, "username": "someone"}], "b": {"c": {"id": 9, "username": "Bo"}}}
    assert find_user(data, "bo") == {"username": "Bo", "id": 9}
    assert find_user(data, "nobody") is None
    assert find_user({"id": True, "username": "bo"}, "bo") is None  # a flag is not an id
    assert find_user({"id": "9", "username": "bo"}, "bo") is None


# -- Archidekt's change log ----------------------------------------------------------------------
LOG = [
    {
        "id": 1,
        "createdAt": "2026-10-21T08:00:00Z",
        "user": {"id": 77, "username": "alice"},
        "description": "Added 1 Sol Ring to Ramp",
    },
    {"id": 2, "createdAt": "2026-10-20T08:00:00Z", "user": {"username": "alice"}, "quantity": 2},
]


async def test_archidekts_change_log_is_its_own_page_linked_from_the_deck_history(stack: Stack) -> None:
    await linked_user(stack)
    stack.ark.deck_logs[42] = LOG
    b, _csrf = await _browser(stack)
    try:
        r = await b.http.get("/history?deck_id=42", headers=NAV)
        assert "<h2 class='ui-section'>Archidekt's own change log</h2>" in r.text
        assert "href='/history/archidekt?deck_id=42'" in r.text
        r = await b.http.get("/history/archidekt?deck_id=42", headers=NAV)
        assert r.status_code == 200, r.text
        page = r.text
        assert "<h1>Archidekt&#x27;s change log</h1>" in page or "<h1>Archidekt's change log</h1>" in page
        assert "Archidekt's own record of changes to “Sample Commander Deck”" in page
        assert "Added 1 Sol Ring to Ramp" in page and "· alice" in page
        assert "<time datetime='2026-10-21T08:00:00Z'>" in page
        assert "quantity: 2" in page  # an entry with no words it knows shows its plain fields
        assert "Archidekt keeps no rows of cards taken out of this deck." in page
    finally:
        await b.aclose()


async def test_a_change_log_the_gateway_cannot_read_says_so(stack: Stack) -> None:
    from mtg_gateway import archidekt_log

    await linked_user(stack)
    stack.ark.deck_logs_answer = {"unexpected": True}
    b, _csrf = await _browser(stack)
    try:
        r = await b.http.get("/history/archidekt?deck_id=42", headers=NAV)
        assert r.status_code == 200 and archidekt_log.LOG_UNREAD in r.text.replace("&#x27;", "'")
        r = await b.http.get("/history/archidekt?deck_id=43", headers=NAV)  # amy's deck
        assert r.status_code == 400 and "not to your linked account" in r.text
    finally:
        await b.aclose()


async def test_list_snapshots_reads_archidekts_log_and_removed_cards(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.deck_logs[42] = LOG
    name = ark.decks[42]["cards"][0]["card"]["oracleCard"]["name"]
    ark.add_removed_row(42, name, 2)
    out = structured(await call(h, token, "list_snapshots", {"deck_id": "42", "archidekt": True}))
    assert out["ok"] and out["archidekt_log"][0] == {
        "when": "2026-10-21T08:00:00Z",
        "who": "alice",
        "what": "Added 1 Sol Ring to Ramp",
    }, out
    assert out["has_more"] is False and out["log_error"] is None and "page" not in out
    assert out["removed_cards"] == [
        {"name": name, "quantity": 2, "categories": ["Ramp"], "removed_at": "2026-10-20T09:30:00Z"}
    ]
    refused = structured(await call(h, token, "list_snapshots", {"archidekt": True}))
    assert refused["ok"] is False and "needs deck_id" in refused["message"]


def test_a_log_entry_keeps_to_words() -> None:
    from mtg_gateway.archidekt import log_entry

    assert log_entry({"date": "2026-01-01", "owner": "x", "message": ["a", "b"]}) == {
        "when": "2026-01-01",
        "who": "x",
        "what": "a; b",
    }
    assert log_entry({"id": 3, "deck": 42})["what"] == ""


# -- removing a removed card's row for good ------------------------------------------------------
async def test_a_removed_card_row_is_removed_for_good_only_after_the_confirm_box(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    name = ark.decks[42]["cards"][0]["card"]["oracleCard"]["name"]
    rid = ark.add_removed_row(42, name)
    b, csrf = await _browser(stack)
    try:
        page = (await b.http.get("/history/archidekt?deck_id=42", headers=NAV)).text
        assert "Cards taken out that Archidekt keeps" in page and f"<strong>{name}</strong>" in page
        assert f"name='relation_id' value='{rid}'" in page and "Remove for good…</button>" in page
        r = await b.http.post(
            "/history/archidekt/remove", data={"csrf": csrf, "deck_id": "42", "relation_id": str(rid)}
        )
        assert r.status_code == 303, r.text
        pid = r.headers["location"].rsplit("/", 1)[1]
        assert ark.relation_deletes == []  # nothing is sent before the member applies it
        review = (await b.http.get(f"/proposals/{pid}", headers=NAV)).text
        assert f"Remove “{name}” for good?" in review  # the confirm box's title
        assert "Remove for good on Archidekt" in review and "Archidekt itself has no undo." in review
    finally:
        await b.aclose()
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["risk"] == "destructive" and got["assistant_may_apply"] is False, got
    refused = structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
    assert refused["ok"] is False and ark.relation_deletes == []
    assert (
        stack.h.app.state.gateway.decks.card_may_approve(h.app.state.gateway.db.get_proposal(pid, SUB))
        is False
    )
    assert await _browser_apply(stack, pid) == ""
    assert ark.relation_deletes == [rid]
    assert all(c["id"] != rid for c in ark.decks[42]["cards"])
    done = h.app.state.gateway.db.get_proposal(pid, SUB)
    assert done["state"] == "applied" and done["result"]["verified"] is True, done


async def test_an_assistant_proposes_it_by_card_name_and_a_card_back_in_the_deck_stops_it(
    stack: Stack,
) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    name = ark.decks[42]["cards"][0]["card"]["oracleCard"]["name"]
    rid = ark.add_removed_row(42, name)
    res = await call(h, token, "propose_delete_deck", {"deck_id": "42", "removed_card": name.upper()})
    p = structured(res)
    assert p["ok"] and p["risk"] == "destructive", p
    assert p["rows"][0]["text"] == (
        f"Remove “{name}” from the cards Archidekt keeps as taken out of your deck “Sample Commander Deck”, "
        "for good"
    )
    assert 42 in ark.decks  # the deck itself is never part of it
    # the row is a card in the deck again before the press: nothing is removed
    row = next(c for c in ark.decks[42]["cards"] if c["id"] == rid)
    del row["deletedAt"]
    await _browser_apply(stack, p["proposal_id"])
    assert ark.relation_deletes == [] and any(c["id"] == rid for c in ark.decks[42]["cards"])
    assert h.app.state.gateway.db.get_proposal(p["proposal_id"], SUB)["state"] != "applied"
    # a name that is not a removed card: refused, naming those kept
    out = structured(await call(h, token, "propose_delete_deck", {"deck_id": "42", "removed_card": "Nope"}))
    assert out["ok"] is False and out["error"] == "not_found", out
    assert "is not among the removed cards Archidekt keeps for this deck" in out["message"]


# -- the backup folder, moved or renamed from the gateway ----------------------------------------
def _folders(stack: Stack) -> dict[str, int]:
    """alice's folders: Archive with the backup folder inside it, and Commander."""
    backup = stack.h.app.state.gateway.decks.settings.archidekt_backup_folder
    stack.ark.folders["alice"] = [
        {"id": 501, "name": "Commander", "private": False},
        {"id": 504, "name": "Archive", "private": False},
        {"id": 505, "name": backup, "private": True, "parent": 504},
    ]
    return {"commander": 501, "archive": 504, "backup": 505}


def _parent(stack: Stack, fid: int) -> int:
    f = next(f for f in stack.ark.folders["alice"] if f["id"] == fid)
    return f.get("parent", 1000 + stack.ark.users["alice"]["id"])


async def test_the_refusing_box_offers_to_move_the_backup_folder_to_the_top_level(stack: Stack) -> None:
    await linked_user(stack)
    ids = _folders(stack)
    root = 1000 + stack.ark.users["alice"]["id"]
    b, csrf = await _browser(stack)
    try:
        r = await b.http.get(f"/decks/api/folders/{ids['archive']}/contents")
        assert r.status_code == 409 and r.json()["backup_folder_id"] == ids["backup"], r.text
        assert r.json()["message"].endswith("Move the backup folder to your top level first.")
        r = await b.http.post(
            f"/decks/api/folders/{ids['archive']}/move",
            json={"parent_id": ids["commander"]},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 409 and r.json()["backup_folder_id"] == ids["backup"], r.text
        r = await b.http.post(
            f"/decks/api/folders/backup/{ids['backup']}/top", json={}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 200 and r.json()["notice"] == "backup_moved", r.text
        assert _parent(stack, ids["backup"]) == root
        assert stack.ark.mass_updates[-1] == {
            "items": [{"type": "folder", "id": ids["backup"], "patch": {"parentFolder": root}}]
        }
        r = await b.http.get(f"/decks/api/folders/{ids['archive']}/contents")
        assert r.status_code == 200, r.text  # the box asks again, and now it can
        # an ordinary folder is not a backup folder to move
        r = await b.http.post(
            f"/decks/api/folders/backup/{ids['commander']}/top", json={}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 404, r.text
    finally:
        await b.aclose()


async def test_account_details_moves_and_renames_the_backup_folder(stack: Stack) -> None:
    await linked_user(stack)
    ids = _folders(stack)
    db = stack.h.app.state.gateway.db
    b, csrf = await _browser(stack)
    try:
        listing = (await b.http.get("/account/details", headers=NAV)).text
        assert "href='/account/details/backups'" in listing and ">Backup folder<" in listing
        page = (await b.http.get("/account/details/backups", headers=NAV)).text
        assert "<strong>“MTG Gateway Backups”</strong> is inside “Archive”." in page
        assert "Move to your top level</button>" in page
        r = await b.http.post(
            "/account/details/backups",
            data={"csrf": csrf, "action": "backup_top", "folder_id": str(ids["backup"])},
        )
        assert r.status_code == 303 and r.headers["location"] == "/account/details/backups?ok=backup_moved"
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "The backup folder is at your top level now." in page
        assert "is at your top level." in page and "Move to your top level</button>" not in page
        # the backup folder's own name is refused, with nothing sent
        sent = len(stack.ark.mass_updates)
        r = await b.http.post(
            "/account/details/backups",
            data={
                "csrf": csrf,
                "action": "backup_rename",
                "folder_id": str(ids["backup"]),
                "name": "mtg gateway backups",
            },
        )
        said = r.text.replace("&#x27;", "'")
        assert r.status_code == 400 and "is the name of the gateway's backup folder" in said
        assert len(stack.ark.mass_updates) == sent
        r = await b.http.post(
            "/account/details/backups",
            data={
                "csrf": csrf,
                "action": "backup_rename",
                "folder_id": str(ids["backup"]),
                "name": "Old copies",
            },
        )
        assert r.headers["location"] == "/account/details/backups?ok=backup_renamed", r.text
        assert next(f for f in stack.ark.folders["alice"] if f["id"] == ids["backup"])["name"] == "Old copies"
        assert ids["backup"] in db.backup_folder_ids(SUB)  # still known as the gateway's backups
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert (
            "Renamed backup folders" in page and "<strong>“Old copies”</strong> is at your top level." in page
        )
        assert "There is no backup folder on Archidekt yet." in page
    finally:
        await b.aclose()
    # still never deleted as an ordinary folder
    from mtg_gateway.decks import DeckError

    with pytest.raises(DeckError) as exc:
        await stack.h.app.state.gateway.decks.folder_contents(SUB, ids["backup"])
    assert exc.value.kind == "backup_inside"


async def test_a_backup_folder_does_not_go_beside_another_of_its_name(stack: Stack) -> None:
    await linked_user(stack)
    ids = _folders(stack)
    stack.ark.folders["alice"].append(
        {"id": 506, "name": stack.h.app.state.gateway.decks.backup_folder, "private": True}
    )
    from mtg_gateway import backup_place
    from mtg_gateway.decks import DeckError

    with pytest.raises(DeckError) as exc:
        await backup_place.move_to_top(stack.h.app.state.gateway.decks, SUB, ids["backup"])
    assert "already a folder called" in str(exc.value) and "Rename this one first" in str(exc.value)
    assert _parent(stack, ids["backup"]) == ids["archive"]
