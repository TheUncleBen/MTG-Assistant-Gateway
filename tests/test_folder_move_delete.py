"""Moving and deleting a folder from My decks (0.7.36), and the JSON routes My decks' menus and
boxes use for folders and decks. Both folder writes are the member's own presses: a session and
the page's CSRF token, the typed folder name for a delete, never an assistant's tool. A delete
saves every deck inside first and stops on the first failure; nothing here reaches the real
Archidekt (the fake stands in for it)."""

from __future__ import annotations

import asyncio
import copy
import dataclasses

import pytest

from mtg_gateway.busy import BUSY_TEXTS
from mtg_gateway.decks import DeckError

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED
from .test_decks_and_proxy import Browser, linked_user

stack_options = stack_with(SHIPPED)
SUB = "user-1"  # the signed-in member of linked_user


def _setup(stack: Stack) -> dict[str, int]:
    """alice's folders: Commander (with Precons and Brews inside), Archive, and the gateway's
    backup folder at the top level; deck 42 in Brews, 201 in Precons, 202 in Commander, a card
    package in Precons."""
    ark = stack.ark
    ark.folders["alice"] = [
        {"id": 501, "name": "Commander", "private": False},
        {"id": 502, "name": "Precons", "private": False, "parent": 501},
        {"id": 503, "name": "Brews", "private": True, "parent": 501},
        {"id": 504, "name": "Archive", "private": False},
        {
            "id": 505,
            "name": stack.h.app.state.gateway.decks.settings.archidekt_backup_folder,
            "private": True,
        },
    ]
    for did, name in ((201, "Precon deck"), (202, "Loose deck")):
        deck = copy.deepcopy(ark.decks[42])
        deck["id"] = did
        deck["name"] = name
        ark.decks[did] = deck
    ark.deck_folder.update({42: 503, 201: 502, 202: 501})
    ark.packages[502] = [{"id": 900, "name": "Ramp package"}]
    return {"commander": 501, "precons": 502, "brews": 503, "archive": 504, "backup": 505}


def _root(stack: Stack) -> int:
    return 1000 + stack.ark.users["alice"]["id"]  # the fake's top-level folder for alice


def _parent(stack: Stack, fid: int) -> int:
    f = next(f for f in stack.ark.folders["alice"] if f["id"] == fid)
    return f.get("parent", _root(stack))


async def _browser(stack: Stack) -> tuple[Browser, str]:
    b = Browser(stack.h)
    await b.login()
    return b, await b.csrf()


# -- folder move ---------------------------------------------------------------------------------
async def test_a_folder_moves_with_everything_inside_and_is_read_back(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    b, csrf = await _browser(stack)
    try:
        r = await b.http.post(
            f"/decks/api/folders/{ids['commander']}/move",
            json={"parent_id": ids["archive"]},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.text
        assert r.json()["notice"] == "folder_moved" and r.json()["folder"]["parent"] == ids["archive"]
        assert _parent(stack, ids["commander"]) == ids["archive"]
        assert stack.ark.mass_updates[-1] == {
            "items": [{"type": "folder", "id": ids["commander"], "patch": {"parentFolder": ids["archive"]}}]
        }
        back = await b.http.post(
            f"/decks/api/folders/{ids['commander']}/move",
            json={"parent_id": None},
            headers={"X-CSRF-Token": csrf},
        )
        assert back.status_code == 200 and _parent(stack, ids["commander"]) == _root(stack)
        listed = (await b.http.get("/decks/api/folders")).json()
        names = [f["name"] for f in listed["folders"]]
        assert names[0] == "Top level (no folder)" and "Archive" in names
        assert stack.h.app.state.gateway.decks.settings.archidekt_backup_folder not in names
    finally:
        await b.aclose()


@pytest.mark.parametrize(
    ("folder", "into", "says"),
    [
        ("commander", "commander", "inside itself"),
        ("commander", "brews", "inside itself"),  # one of its own folders
        ("backup", "archive", "No folder is called"),  # the backup folder never moves
        ("archive", "backup", "No folder is called"),  # nothing goes into it
    ],
)
async def test_a_folder_move_is_refused_into_itself_and_around_the_backup_folder(
    stack: Stack, folder: str, into: str, says: str
) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    sent = len(stack.ark.mass_updates)
    with pytest.raises(DeckError) as exc:
        await stack.h.app.state.gateway.decks.move_folder(SUB, ids[folder], ids[into])
    assert says in str(exc.value)
    assert len(stack.ark.mass_updates) == sent  # nothing was sent


async def test_the_top_level_folder_cannot_be_moved_or_deleted(stack: Stack) -> None:
    await linked_user(stack)
    _setup(stack)
    decks = stack.h.app.state.gateway.decks
    root = _root(stack)
    for attempt in (decks.move_folder(SUB, root, 504), decks.folder_contents(SUB, root)):
        with pytest.raises(DeckError) as exc:
            await attempt
        assert str(exc.value) == "Your top level can't be moved or deleted."


# -- folder delete -------------------------------------------------------------------------------
async def test_the_delete_box_counts_decks_packages_and_folders_inside(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    b, _csrf = await _browser(stack)
    try:
        r = await b.http.get(f"/decks/api/folders/{ids['commander']}/contents")
        assert r.status_code == 200, r.text
        got = r.json()
        assert (got["name"], got["decks"], got["packages"], got["folders"]) == ("Commander", 3, 1, 2)
        assert got["backups"] is True
    finally:
        await b.aclose()


async def test_a_folder_listing_over_several_pages_is_read_to_the_end(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    stack.ark.folder_page_size = 1
    stack.ark.packages[502].append({"id": 901, "name": "Draw package"})
    got = await stack.h.app.state.gateway.decks.folder_contents(SUB, ids["precons"])
    assert [d["name"] for d in got["decks"]] == ["Precon deck"]
    assert [p["name"] for p in got["packages"]] == ["Ramp package", "Draw package"]


async def test_a_folder_holding_the_backup_folder_is_refused_before_anything(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    backup = next(f for f in stack.ark.folders["alice"] if f["id"] == ids["backup"])
    backup["parent"] = ids["archive"]
    b, _csrf = await _browser(stack)
    try:
        r = await b.http.get(f"/decks/api/folders/{ids['archive']}/contents")
        assert r.status_code == 409 and r.json()["error"] == "backup_inside", r.text
        assert "backup folder is inside “Archive”" in r.json()["message"]
    finally:
        await b.aclose()
    assert stack.ark.folder_deletes == []


async def test_an_unreadable_folder_listing_stops_the_delete(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    stack.ark.folder_details_fail.add(ids["brews"])
    with pytest.raises(DeckError):
        await stack.h.app.state.gateway.decks.delete_folder(
            SUB, ids["commander"], "Commander", {"decks": 3, "packages": 1, "folders": 2}
        )
    assert stack.ark.folder_deletes == [] and 42 in stack.ark.decks


async def _run_delete(b: Browser, csrf: str, fid: int, body: dict) -> dict:
    r = await b.http.post(f"/decks/api/folders/{fid}/delete", json=body, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    for _ in range(200):
        got = (await b.http.get(f"/decks/api/folders/jobs/{job['id']}")).json()["job"]
        if got["state"] != "running":
            return got
        await asyncio.sleep(0.02)
    raise AssertionError("the folder delete did not end")


async def test_a_folder_delete_saves_every_deck_then_deletes_once_and_checks(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    gw = stack.h.app.state.gateway
    b, csrf = await _browser(stack)
    try:
        job = await _run_delete(
            b, csrf, ids["commander"], {"typed_name": "commander", "decks": 3, "packages": 1, "folders": 2}
        )
        assert job["state"] == "done", job
        assert job["notice"] == "folder_deleted"
        assert "with 3 decks, 1 card package and 2 folders inside it" in job["message"]
    finally:
        await b.aclose()
    assert stack.ark.folder_deletes == [{"items": [{"id": ids["commander"], "type": "folder"}]}]
    for did in (42, 201, 202):
        assert did not in stack.ark.decks
        assert gw.db.list_snapshots(SUB, deck_id=str(did)), did  # a snapshot of each deck first
    # a backup copy of each, in the backup folder, made before the delete
    copies = [d for d, f in stack.ark.deck_folder.items() if f == ids["backup"]]
    assert len(copies) == 3
    assert ids["archive"] in {f["id"] for f in stack.ark.folders["alice"]}
    audit = [e for e in gw.db.audit_for_user(SUB) if e["event"] == "folder_deleted"]
    assert audit and audit[-1]["detail"]["verified"] is True


async def test_a_wrong_name_or_changed_counts_delete_nothing(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    b, csrf = await _browser(stack)
    try:
        wrong = await _run_delete(
            b, csrf, ids["commander"], {"typed_name": "Command", "decks": 3, "packages": 1, "folders": 2}
        )
        assert wrong["state"] == "failed" and "does not match" in wrong["message"]
        changed = await _run_delete(
            b, csrf, ids["commander"], {"typed_name": "Commander", "decks": 2, "packages": 1, "folders": 2}
        )
        assert changed["state"] == "failed" and "changed since you looked" in changed["message"]
    finally:
        await b.aclose()
    assert stack.ark.folder_deletes == [] and 42 in stack.ark.decks


async def test_a_failed_backup_copy_stops_the_delete_before_anything_is_deleted(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    decks = stack.h.app.state.gateway.decks
    real = decks._backup_copy
    made: list[str] = []

    async def second_fails(sub, deck, snapshot_id, *, reason):  # type: ignore[no-untyped-def]
        if made:
            raise DeckError("unavailable", "Archidekt is unavailable right now.")
        made.append(deck.id)
        return await real(sub, deck, snapshot_id, reason=reason)

    decks._backup_copy = second_fails  # type: ignore[method-assign]
    with pytest.raises(DeckError) as exc:
        await decks.delete_folder(
            SUB, ids["commander"], "Commander", {"decks": 3, "packages": 1, "folders": 2}
        )
    assert exc.value.kind == "backup_failed"
    assert "Nothing was deleted; the snapshots and backup copies already made are kept." in str(exc.value)
    assert stack.ark.folder_deletes == [] and {42, 201, 202} <= set(stack.ark.decks)


async def test_the_members_own_limit_pauses_a_delete_instead_of_stopping_it(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    decks = stack.h.app.state.gateway.decks
    real = decks.get_own_deck
    refused: list[int] = []
    seen_steps: list[str] = []

    async def once_over_the_limit(sub, deck_id, *a, **kw):  # type: ignore[no-untyped-def]
        if not refused:
            refused.append(1)
            err = DeckError("rate_limited", "Your account has used its Archidekt actions.", retry_after=1)
            err.busy_cause = "budget"
            raise err
        return await real(sub, deck_id, *a, **kw)

    decks.get_own_deck = once_over_the_limit  # type: ignore[method-assign]
    progress: dict = {}

    async def watch() -> None:
        while True:
            seen_steps.append(str(progress.get("step")))
            await asyncio.sleep(0.05)

    watcher = asyncio.create_task(watch())
    try:
        await decks.delete_folder(
            SUB, ids["precons"], "Precons", {"decks": 1, "packages": 1, "folders": 0}, progress=progress
        )
    finally:
        watcher.cancel()
    assert BUSY_TEXTS["budget"] in seen_steps, seen_steps
    assert 201 not in stack.ark.decks


async def test_archidekts_own_slow_down_pauses_a_delete_with_its_seconds(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    decks = stack.h.app.state.gateway.decks
    real = decks.get_own_deck
    refused: list[int] = []
    seen_steps: list[str] = []

    async def one_429(sub, deck_id, *a, **kw):  # type: ignore[no-untyped-def]
        if not refused:
            refused.append(1)
            raise DeckError(
                "rate_limited", "Archidekt asked the gateway to slow down.", retry_after=1, sent=False
            )
        return await real(sub, deck_id, *a, **kw)

    decks.get_own_deck = one_429  # type: ignore[method-assign]
    progress: dict = {}

    async def watch() -> None:
        while True:
            seen_steps.append(str(progress.get("step")))
            await asyncio.sleep(0.05)

    watcher = asyncio.create_task(watch())
    try:
        await decks.delete_folder(
            SUB, ids["precons"], "Precons", {"decks": 1, "packages": 1, "folders": 0}, progress=progress
        )
    finally:
        watcher.cancel()
    assert (
        "Archidekt asked the gateway to slow down, so it is pausing the folder delete for 1 second."
        in seen_steps
    ), seen_steps
    assert 201 not in stack.ark.decks


async def test_a_delete_of_one_of_each_says_each_in_the_singular(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    stack.ark.folders["alice"].append({"id": 506, "name": "Empty", "private": False, "parent": ids["brews"]})
    stack.ark.packages[ids["brews"]] = [{"id": 901, "name": "Draw package"}]
    b, csrf = await _browser(stack)
    try:
        job = await _run_delete(
            b, csrf, ids["brews"], {"typed_name": "Brews", "decks": 1, "packages": 1, "folders": 1}
        )
    finally:
        await b.aclose()
    assert job["state"] == "done", job
    assert (
        job["message"] == "Deleted “Brews” on Archidekt with 1 deck, 1 card package and 1 folder inside it."
    )


async def test_a_backup_failure_names_the_deck_in_plain_words(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    decks = stack.h.app.state.gateway.decks

    async def fails(sub, deck, snapshot_id, *, reason):  # type: ignore[no-untyped-def]
        raise DeckError("unavailable", "Archidekt can't be reached right now.")

    decks._backup_copy = fails  # type: ignore[method-assign]
    with pytest.raises(DeckError) as exc:
        await decks.delete_folder(SUB, ids["precons"], "Precons", {"decks": 1, "packages": 1, "folders": 0})
    assert str(exc.value) == (
        "The deck “Precon deck” could not be saved first (Archidekt can't be reached right now). "
        "Nothing was deleted."
    )


@pytest.mark.parametrize("sent", [False, True])
async def test_an_unexpected_failure_says_whether_the_delete_was_sent(stack: Stack, sent: bool) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    decks = stack.h.app.state.gateway.decks
    if sent:
        real = decks.client.delete_folders

        async def breaks_after(token, folder_ids):  # type: ignore[no-untyped-def]
            await real(token, folder_ids)
            raise RuntimeError("boom")

        decks.client.delete_folders = breaks_after  # type: ignore[method-assign]
    else:

        def breaks(*a, **kw):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        decks._hand_snapshot = breaks  # type: ignore[method-assign]
    b, csrf = await _browser(stack)
    try:
        job = await _run_delete(
            b, csrf, ids["precons"], {"typed_name": "Precons", "decks": 1, "packages": 1, "folders": 0}
        )
    finally:
        await b.aclose()
    assert job["state"] == "failed"
    if sent:
        assert "Check it on Archidekt" in job["message"] and "Nothing was deleted" not in job["message"]
    else:
        assert "Nothing was deleted" in job["message"] and 201 in stack.ark.decks


async def test_a_delete_archidekt_did_not_answer_clearly_says_so(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    decks = stack.h.app.state.gateway.decks

    async def times_out(token, folder_ids):  # type: ignore[no-untyped-def]
        raise DeckError("unavailable", "Archidekt did not answer in time.", sent=True)

    decks.client.delete_folders = times_out  # type: ignore[method-assign]
    with pytest.raises(DeckError) as exc:
        await decks.delete_folder(SUB, ids["precons"], "Precons", {"decks": 1, "packages": 1, "folders": 0})
    assert "did not say whether the folder was deleted" in str(exc.value)


def test_folder_move_and_delete_are_never_assistant_tools() -> None:
    import pathlib

    tools = pathlib.Path(__file__).parents[1] / "src" / "mtg_gateway"
    for path in tools.glob("*.py"):
        if path.name in {"decks.py", "companion_folders.py", "archidekt.py"}:
            continue
        text = path.read_text()
        assert (
            "move_folder(" not in text and "delete_folder(" not in text and "delete_folders(" not in text
        ), path


async def test_folder_writes_need_the_csrf_header_and_obey_the_writes_switch(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    b, csrf = await _browser(stack)
    try:
        for path, body in (
            (f"/decks/api/folders/{ids['brews']}/move", {"parent_id": None}),
            (f"/decks/api/folders/{ids['brews']}/rename", {"name": "Old brews"}),
            (f"/decks/api/folders/{ids['brews']}/delete", {"typed_name": "Brews", "decks": 1}),
            ("/decks/api/folders", {"name": "New one"}),
            ("/decks/api/decks/42/rename", {"name": "Other"}),
            ("/decks/api/decks/42/move", {"folder_id": None}),
            ("/decks/api/decks/42/delete", {"typed_name": "Sample Commander Deck"}),
        ):
            r = await b.http.post(path, json=body)
            assert r.status_code == 403 and r.json()["error"] == "csrf", (path, r.text)
        decks = stack.h.app.state.gateway.decks
        decks.settings = dataclasses.replace(decks.settings, writes_enabled=False)
        calls = len(stack.ark.calls)
        for path, body in (
            (f"/decks/api/folders/{ids['brews']}/move", {"parent_id": None}),
            (f"/decks/api/folders/{ids['brews']}/delete", {"typed_name": "Brews", "decks": 1}),
        ):
            r = await b.http.post(path, json=body, headers={"X-CSRF-Token": csrf})
            assert r.status_code == 403 and "switched off" in r.json()["message"], (path, r.text)
        assert all(m == "GET" for m, _p in stack.ark.calls[calls:])
    finally:
        await b.aclose()
    assert _parent(stack, ids["brews"]) == ids["commander"] and 42 in stack.ark.decks


# -- the deck boxes and the new folder box -------------------------------------------------------
async def test_the_deck_boxes_rename_move_delete_and_export(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    b, csrf = await _browser(stack)
    head = {"X-CSRF-Token": csrf}
    try:
        r = await b.http.post("/decks/api/decks/42/rename", json={"name": "Renamed deck"}, headers=head)
        assert r.status_code == 200 and r.json() == {"ok": True, "name": "Renamed deck", "notice": "saved"}, (
            r.text
        )
        assert stack.ark.decks[42]["name"] == "Renamed deck"
        r = await b.http.post("/decks/api/decks/42/move", json={"folder_id": ids["archive"]}, headers=head)
        assert r.status_code == 200 and r.json()["notice"] == "moved"
        assert stack.ark.deck_folder[42] == ids["archive"]
        info = (await b.http.get("/decks/api/decks/42/delete-info")).json()
        assert info["name"] == "Renamed deck" and info["cards"] > 0 and info["backups"] is True
        exp = (await b.http.get("/decks/api/decks/42/export")).json()
        assert exp["name"] == "Renamed deck" and "Sol Ring" in exp["plain"]
        assert [f["label"] for f in exp["files"]] == [
            "Archidekt .txt",
            "Plain .txt",
            ".csv",
            ".json",
            "Arena .txt",
            "MTGO .dek",
            "PDF",
        ]
        for f in exp["files"]:
            assert (await b.http.get(f["href"])).status_code == 200, f
        bad = await b.http.post("/decks/api/decks/42/delete", json={"typed_name": "nope"}, headers=head)
        assert bad.status_code == 400 and 42 in stack.ark.decks
        r = await b.http.post("/decks/api/decks/42/delete", json={"typed_name": "renamed DECK"}, headers=head)
        assert r.status_code == 200 and r.json()["notice"] == "deleted" and 42 not in stack.ark.decks
    finally:
        await b.aclose()


async def test_the_new_folder_box_checks_first_then_creates(stack: Stack) -> None:
    await linked_user(stack)
    _setup(stack)
    b, csrf = await _browser(stack)
    head = {"X-CSRF-Token": csrf}
    try:
        before = len(stack.ark.folders["alice"])
        r = await b.http.post(
            "/decks/api/folders/check", json={"name": " Cube ", "parent_id": None}, headers=head
        )
        assert r.status_code == 200 and r.json()["name"] == "Cube" and r.json()["top_level"] is True, r.text
        assert len(stack.ark.folders["alice"]) == before  # nothing created yet
        dup = await b.http.post("/decks/api/folders/check", json={"name": "archive"}, headers=head)
        assert dup.status_code == 400 and "already a folder" in dup.json()["message"]
        r = await b.http.post("/decks/api/folders", json={"name": "Cube", "parent_id": None}, headers=head)
        assert r.status_code == 200 and r.json()["notice"] == "folder_created"
        assert any(f["name"] == "Cube" for f in stack.ark.folders["alice"])
        page = await b.http.get("/decks?ok=folder_moved")
        assert "Folder moved on Archidekt." in page.text
    finally:
        await b.aclose()


# -- review fixes --------------------------------------------------------------------------------
async def test_a_folder_the_tree_left_out_stops_the_delete_before_anything(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    decks = stack.h.app.state.gateway.decks
    decks.MAX_FOLDERS = 3  # the tree is cut after Commander's first folder inside
    calls = len(stack.ark.calls)
    with pytest.raises(DeckError) as exc:
        await decks.delete_folder(
            SUB, ids["commander"], "Commander", {"decks": 3, "packages": 1, "folders": 2}
        )
    assert str(exc.value) == decks.TOO_MUCH_TO_LIST
    assert stack.ark.folder_deletes == [] and all(m == "GET" for m, _p in stack.ark.calls[calls:])


async def test_a_deck_moved_in_while_the_decks_are_saved_stops_the_delete(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    decks = stack.h.app.state.gateway.decks
    real = decks._hand_snapshot

    def snapshot_then_a_deck_arrives(sub, deck):  # type: ignore[no-untyped-def]
        stack.ark.deck_folder[202] = ids["precons"]  # moved in from elsewhere (archidekt.com)
        return real(sub, deck)

    decks._hand_snapshot = snapshot_then_a_deck_arrives  # type: ignore[method-assign]
    with pytest.raises(DeckError) as exc:
        await decks.delete_folder(SUB, ids["precons"], "Precons", {"decks": 1, "packages": 1, "folders": 0})
    assert exc.value.kind == "changed" and "Nothing was deleted" in str(exc.value)
    assert stack.ark.folder_deletes == [] and 202 in stack.ark.decks


async def test_nothing_goes_into_a_folder_while_it_is_being_deleted(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    decks = stack.h.app.state.gateway.decks
    decks._deleting[SUB] = {ids["commander"], ids["precons"], ids["brews"]}
    for attempt in (
        decks.move_deck(SUB, "202", ids["precons"]),
        decks.move_folder(SUB, ids["archive"], ids["brews"]),
        decks.check_new_folder(SUB, "Inside", ids["commander"]),
    ):
        with pytest.raises(DeckError) as exc:
            await attempt
        assert "being deleted" in str(exc.value)


async def test_a_folder_holding_the_backup_folder_does_not_move(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    backup = next(f for f in stack.ark.folders["alice"] if f["id"] == ids["backup"])
    backup["parent"] = ids["archive"]
    # the exact name is the backup folder at any depth
    with pytest.raises(DeckError) as exc:
        await stack.h.app.state.gateway.decks.move_folder(SUB, ids["archive"], ids["commander"])
    assert exc.value.kind == "backup_inside"
    assert _parent(stack, ids["archive"]) == _root(stack)


async def test_an_unnamed_deck_is_deleted_by_typing_unnamed_deck(stack: Stack) -> None:
    await linked_user(stack)
    _setup(stack)
    stack.ark.decks[201]["name"] = ""
    b, csrf = await _browser(stack)
    head = {"X-CSRF-Token": csrf}
    try:
        info = (await b.http.get("/decks/api/decks/201/delete-info")).json()
        assert info["name"] == "Unnamed deck"
        r = await b.http.post(
            "/decks/api/decks/201/delete", json={"typed_name": "unnamed deck"}, headers=head
        )
        assert r.status_code == 200, r.text
    finally:
        await b.aclose()
    assert 201 not in stack.ark.decks


async def test_a_refused_rename_leaves_no_waiting_change(stack: Stack) -> None:
    await linked_user(stack)
    _setup(stack)
    gw = stack.h.app.state.gateway
    b, csrf = await _browser(stack)
    try:
        gw.decks.settings = dataclasses.replace(gw.decks.settings, writes_enabled=False)
        r = await b.http.post(
            "/decks/api/decks/42/rename", json={"name": "Other"}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 403
        gw.decks.settings = dataclasses.replace(gw.decks.settings, writes_enabled=True)

        async def refuses(*a, **kw):  # type: ignore[no-untyped-def]
            raise DeckError("unavailable", "Archidekt can't be reached right now.")

        gw.decks.apply = refuses  # type: ignore[method-assign]
        r = await b.http.post(
            "/decks/api/decks/42/rename", json={"name": "Other"}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 503
    finally:
        await b.aclose()
    assert not [p for p in gw.db.list_proposals(SUB) if p["state"] == "pending"]
    assert stack.ark.decks[42]["name"] != "Other"


def test_my_decks_format_filter_matches_by_format() -> None:
    from mtg_gateway.folderview import arrange

    rows = [
        {"id": "1", "name": "A", "format_name": "brawl", "folder_id": None},
        {"id": "2", "name": "B", "format_name": "historicbrawl", "folder_id": None},
        {"id": "3", "name": "C", "format_name": "paupercommander", "folder_id": None},
    ]

    def pick(fmt: str) -> list[str]:
        return [d["id"] for d in arrange(rows, None, folder_id=None, q="", order="name", fmt=fmt).decks]

    assert pick("historicbrawl") == ["2"] and pick("paupercommander") == ["3"]
    assert pick("Pauper EDH") == ["3"]  # a link from before still carries the name


async def test_the_collaborator_box_names_an_unnamed_deck(stack: Stack) -> None:
    await linked_user(stack)
    stack.ark.decks[42]["name"] = ""
    b, _csrf = await _browser(stack)
    try:
        r = await b.http.get("/decks/42/settings")
    finally:
        await b.aclose()
    assert "data-deck-name='Unnamed deck'" in r.text


async def test_the_folder_being_deleted_does_not_move(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    decks = stack.h.app.state.gateway.decks
    decks._deleting[SUB] = {ids["brews"]}
    with pytest.raises(DeckError) as exc:
        await decks.move_folder(SUB, ids["brews"], ids["archive"])
    assert str(exc.value) == "That folder is being deleted, so it can't be moved now."


async def test_a_failed_read_back_after_the_delete_says_to_look_on_archidekt(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    decks = stack.h.app.state.gateway.decks
    real = decks.list_decks

    async def unreadable(sub, *a, **kw):  # type: ignore[no-untyped-def]
        if stack.ark.folder_deletes:
            raise DeckError("unavailable", "Archidekt can't be reached right now.")
        return await real(sub, *a, **kw)

    decks.list_decks = unreadable  # type: ignore[method-assign]
    with pytest.raises(DeckError) as exc:
        await decks.delete_folder(SUB, ids["precons"], "Precons", {"decks": 1, "packages": 1, "folders": 0})
    assert str(exc.value) == (
        "Archidekt can't be reached right now. Check the folder on Archidekt to see what was deleted."
    )


async def test_a_renamed_backup_folder_is_known_by_the_copies_inside(stack: Stack) -> None:
    """The backup folder renamed on Archidekt no longer has its name, but the backup copies the
    gateway recorded making are still inside: the folder is refused as the backup folder is."""
    await linked_user(stack)
    ids = _setup(stack)
    db = stack.h.app.state.gateway.db
    backup = next(f for f in stack.ark.folders["alice"] if f["id"] == ids["backup"])
    backup["name"] = "Old copies"
    stack.ark.deck_folder[201] = ids["backup"]  # a copy the gateway made, recorded on a snapshot
    assert db.save_snapshot(
        "s-copy", owner_sub=SUB, deck_id="42", proposal_id=None, fingerprint="fp", deck={"name": "x"}
    )
    db.set_snapshot_backup("s-copy", backup_deck_id="201", backup_url="")
    b, _csrf = await _browser(stack)
    try:
        r = await b.http.get(f"/decks/api/folders/{ids['backup']}/contents")
        assert r.status_code == 409 and r.json()["error"] == "backup_inside", r.text
        assert "backup copies the gateway made" in r.json()["message"]
    finally:
        await b.aclose()
    with pytest.raises(DeckError) as exc:
        await stack.h.app.state.gateway.decks.delete_folder(
            SUB, ids["backup"], "Old copies", {"decks": 1, "packages": 0, "folders": 0}
        )
    assert exc.value.kind == "backup_inside"
    assert stack.ark.folder_deletes == [] and 201 in stack.ark.decks


async def test_a_renamed_backup_folder_with_no_copy_left_is_still_the_backup_folder(stack: Stack) -> None:
    """Renamed on Archidekt and emptied of copies, the folder the gateway put backup copies in is
    still refused, as is a folder it was moved into."""
    await linked_user(stack)
    ids = _setup(stack)
    db = stack.h.app.state.gateway.db
    backup = next(f for f in stack.ark.folders["alice"] if f["id"] == ids["backup"])
    backup["name"] = "Old copies"
    db.record_backup_folder(SUB, ids["backup"])
    decks = stack.h.app.state.gateway.decks
    with pytest.raises(DeckError) as exc:
        await decks.folder_contents(SUB, ids["backup"])
    assert exc.value.kind == "backup_inside" and "renamed on Archidekt" in str(exc.value)
    backup["parent"] = ids["archive"]
    decks.folder_trees.drop(SUB)  # the tree read again, with the folder moved
    with pytest.raises(DeckError) as exc:
        await decks.folder_contents(SUB, ids["archive"])
    assert exc.value.kind == "backup_inside"
    assert stack.ark.folder_deletes == []


def _spy(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    from mtg_gateway import archidekt

    calls: list[tuple[str, str]] = []
    orig = archidekt.ArchidektClient._request

    async def spy(self, method, path, *a, **k):  # noqa: ANN001
        calls.append((method, path))
        return await orig(self, method, path, *a, **k)

    monkeypatch.setattr(archidekt.ArchidektClient, "_request", spy)
    return calls


async def test_a_delete_after_its_box_sends_four_archidekt_calls_not_six(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Speed (Ben, 25 Oct): the delete box reads the deck, and finds the backup folder while the
    member types the name, so pressing Delete waits for four Archidekt calls (the copy, its
    description, the delete and the read-back), each at least a second apart, instead of six.
    The backup copy is still made from the deck as it is on Archidekt (the site's own copy)."""
    await linked_user(stack)
    ids = _setup(stack)
    b, csrf = await _browser(stack)
    calls = _spy(stack, monkeypatch)
    try:
        info = (await b.http.get("/decks/api/decks/201/delete-info")).json()
        assert info["name"] == "Precon deck"
        for _ in range(50):  # the folder look-up runs after the box's answer
            if ("GET", "/decks/folderTree/") in calls:
                break
            await asyncio.sleep(0.01)
        opened = len(calls)
        r = await b.http.post(
            "/decks/api/decks/201/delete", json={"typed_name": "Precon deck"}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 200, r.text
    finally:
        await b.aclose()
    pressed = [c for c in calls[opened:]]
    assert [m for m, _p in pressed] == ["POST", "PATCH", "DELETE", "GET"], pressed
    assert 201 not in stack.ark.decks
    copy = max(d for d in stack.ark.decks if d not in (42, 202))
    assert stack.ark.deck_folder.get(copy) == ids["backup"]


async def test_the_box_creates_nothing_and_an_old_box_reads_again(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening the box never makes the backup folder (only a delete does), and a box open longer
    than the reuse window reads the deck and the folders again when Delete is pressed."""
    from mtg_gateway import decks as decks_mod

    await linked_user(stack)
    _setup(stack)
    stack.ark.folders["alice"] = [f for f in stack.ark.folders["alice"] if f["id"] != 505]
    b, csrf = await _browser(stack)
    calls = _spy(stack, monkeypatch)
    try:
        await b.http.get("/decks/api/decks/201/delete-info")
        await asyncio.sleep(0.2)
        assert not [c for c in calls if c[0] == "POST"]  # no folder made by opening the box
        monkeypatch.setattr(decks_mod, "DELETE_PREP_SECONDS", 0)
        opened = len(calls)
        r = await b.http.post(
            "/decks/api/decks/201/delete", json={"typed_name": "Precon deck"}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 200, r.text
    finally:
        await b.aclose()
    pressed = calls[opened:]
    assert pressed[0] == ("GET", "/decks/201/") and ("GET", "/decks/folderTree/") in pressed
    assert ("POST", "/decks/folders/") in pressed  # the folder is made at the delete, as before


async def test_a_gateway_write_or_another_link_makes_the_delete_read_again(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The box's read is used only while nothing the gateway sent could have changed the deck,
    and only for the Archidekt account it was read with (security review, 25 Oct)."""
    await linked_user(stack)
    _setup(stack)
    decks = stack.h.app.state.gateway.decks
    b, csrf = await _browser(stack)
    calls = _spy(stack, monkeypatch)
    try:
        await b.http.get("/decks/api/decks/201/delete-info")
        decks._on_archidekt_write("/decks/202/update/")  # any write the gateway sends
        assert decks._prepared(SUB, "201") is None
        await b.http.get("/decks/api/decks/201/delete-info")
        entry = decks._delete_prep[(SUB, "201")]
        entry["link"] = "someone else"
        assert decks._prepared(SUB, "201") is None
        await b.http.get("/decks/api/decks/201/delete-info")
        decks.forget_member(SUB)
        assert (SUB, "201") not in decks._delete_prep
    finally:
        await b.aclose()
    assert not [c for c in calls if c[0] != "GET"]
