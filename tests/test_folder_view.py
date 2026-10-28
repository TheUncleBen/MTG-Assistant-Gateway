"""My decks by folder (folderview.py, 0.7.28 §6): one level of the folder tree at a time, folders
A to Z above the decks directly in the open folder, a breadcrumb back up, the name filter over
every deck with each match's folder, the backup folder hidden everywhere, the old Folder
dropdown gone, and the tree read once and kept like the deck list."""

from __future__ import annotations

import html
import re

from mtg_gateway import folderview as fv

from .test_companion import NAV, Stack, linked_browser, stack_options  # noqa: F401  (fixture)

ROOT = 1


def _info(*folders: tuple[int, str, int, bool], backup: set[int] = frozenset()) -> dict:  # type: ignore[assignment]
    out = [{"id": ROOT, "name": "Top level (no folder)", "depth": 0, "parent": None, "private": False}]
    depth = {ROOT: 0}
    for fid, name, parent, private in folders:
        depth[fid] = depth[parent] + 1
        out.append(
            {
                "id": fid,
                "name": name,
                "depth": depth[fid],
                "parent": parent,
                "private": private,
                "backup": fid in backup,
            }
        )
    return {"root_id": ROOT, "folders": out}


INFO = _info(
    (10, "commander", ROOT, False),
    (11, "Precons", 10, False),
    (12, "Brews", 10, True),
    (20, "Archive", ROOT, False),
    (30, "MTG Gateway Backups", ROOT, True),
    (31, "Old copies", 30, False),
    backup={30, 31},
)


def _deck(i: int, name: str, folder_id: int | None, updated: str = "2026-01-01") -> dict:
    return {"id": str(i), "name": name, "folder_id": folder_id, "folder": "x", "updated_at": updated}


ROWS = [
    _deck(1, "Top deck", None, "2026-03-01"),
    _deck(2, "Root deck", ROOT, "2026-02-01"),
    _deck(3, "Lost deck", 999),  # a folder the tree doesn't have: shown at the top level
    _deck(4, "Atraxa precon", 11),
    _deck(5, "Zur brew", 12),
    _deck(6, "Another brew", 12, "2026-05-01"),
    _deck(7, "Hidden copy", 31),
    _deck(8, "Commander loose deck", 10),
]


def test_the_top_level_shows_its_folders_a_to_z_and_only_its_own_decks() -> None:
    level = fv.arrange(ROWS, INFO, folder_id=None, q="", order="updated")
    assert [t["name"] for t in level.folders] == ["Archive", "commander"]  # A to Z, any case
    assert [d["name"] for d in level.decks] == ["Top deck", "Root deck", "Lost deck"]
    commander = level.folders[1]
    assert (commander["decks"], commander["subfolders"]) == (1, 2)
    assert level.folders[0]["decks"] == level.folders[0]["subfolders"] == 0  # empty folders show
    assert level.crumbs == [(None, "Decks")] and level.current is None
    assert level.total == 7  # the copy in the backup folder is not counted


def test_an_open_folder_has_a_breadcrumb_its_subfolders_and_its_decks() -> None:
    level = fv.arrange(ROWS, INFO, folder_id=10, q="", order="name")
    assert level.crumbs == [(None, "Decks"), (10, "commander")]
    assert [(t["name"], t["private"], t["decks"]) for t in level.folders] == [
        ("Brews", True, 2),
        ("Precons", False, 1),
    ]
    assert [d["name"] for d in level.decks] == ["Commander loose deck"]
    assert level.decks[0]["folder"] is None  # the breadcrumb already says where these are
    deeper = fv.arrange(ROWS, INFO, folder_id=12, q="", order="name")
    assert deeper.crumbs == [(None, "Decks"), (10, "commander"), (12, "Brews")]
    assert [d["name"] for d in deeper.decks] == ["Another brew", "Zur brew"]  # the deck order applies


def test_the_filter_searches_every_deck_and_says_where_each_one_is() -> None:
    level = fv.arrange(ROWS, INFO, folder_id=11, q="BREW", order="updated")
    assert level.searching
    assert [(d["name"], d["folder"]) for d in level.decks] == [
        ("Another brew", "commander › Brews"),
        ("Zur brew", "commander › Brews"),
    ]
    assert [(t["name"], t["where"]) for t in level.folders] == [("Brews", "commander")]
    top = fv.arrange(ROWS, INFO, folder_id=None, q="deck", order="name")
    assert {d["name"]: d["folder"] for d in top.decks}["Commander loose deck"] == "commander"
    assert {d["name"]: d["folder"] for d in top.decks}["Top deck"] is None
    assert "Hidden copy" not in [
        d["name"] for d in fv.arrange(ROWS, INFO, folder_id=None, q="copy", order="name").decks
    ]


def test_the_backup_folder_and_everything_in_it_never_shows() -> None:
    assert [f["name"] for f in fv.visible_folders(INFO)] == [
        "Top level (no folder)",
        "commander",
        "Precons",
        "Brews",
        "Archive",
    ]
    assert fv.resolve_folder(INFO, "30") is None and fv.resolve_folder(INFO, "31") is None
    assert fv.arrange(ROWS, INFO, folder_id=30, q="", order="name").current is None
    found = fv.arrange(ROWS, INFO, folder_id=None, q="backups", order="name")
    assert found.folders == [] and found.decks == []


def test_folder_addresses_by_id_and_old_ones_by_name() -> None:
    assert fv.resolve_folder(INFO, "12") == 12
    assert fv.resolve_folder(INFO, "Precons") == 11  # an address from before 0.7.28
    assert fv.resolve_folder(INFO, "1") is None  # the top level itself
    assert fv.resolve_folder(INFO, "nope") is None and fv.resolve_folder(INFO, "") is None
    assert fv.resolve_folder(None, "12") is None


def test_without_the_tree_every_deck_is_listed_with_a_notice() -> None:
    level = fv.arrange(ROWS[:3], None, folder_id=10, q="", order="name")
    assert not level.tree and [d["name"] for d in level.decks] == ["Lost deck", "Root deck", "Top deck"]
    html = fv.level_html(level, covers={}, q="", view="grid", order="name")
    assert fv.esc(fv.FOLDERS_UNREADABLE) in html
    nothing = fv.arrange([], None, folder_id=None, q="", order="name")
    assert nothing.tree and fv.esc(fv.FOLDERS_UNREADABLE) not in fv.level_html(
        nothing, covers={}, q="", view="grid", order="name"
    )


def test_the_html_breadcrumb_tiles_and_empty_states() -> None:
    level = fv.arrange(ROWS, INFO, folder_id=12, q="", order="name")
    html = fv.level_html(level, covers={}, q="", view="list", order="name")
    assert "<nav class='crumbs' aria-label='Folder path'>" in html
    assert "href='/decks?order=name'>Decks</a>" in html
    assert "href='/decks?folder=10&amp;order=name'>commander</a>" in html
    assert "<li aria-current='page'><span>Brews</span></li>" in html
    assert "class='plain decklist list'" in html and "Folders <span" not in html  # no subfolders here
    top = fv.level_html(
        fv.arrange(ROWS, INFO, folder_id=None, q="", order="updated"),
        covers={},
        q="",
        view="grid",
        order="updated",
    )
    assert "class='crumbs'" not in top  # nothing to go back to
    assert "<span class='sub'>1 deck · 2 folders</span>" in top and "<span class='sub'>Empty</span>" in top
    assert "href='/decks?folder=20'" in top and "Folders <span class='n'>2</span>" in top
    brews = fv.level_html(
        fv.arrange(ROWS, INFO, folder_id=10, q="", order="updated"),
        covers={},
        q="",
        view="grid",
        order="updated",
    )
    assert brews.count("title='Private folder'") == 1 and "<span class='sr-only'>Private</span>" in brews
    empty = fv.level_html(
        fv.arrange(ROWS, INFO, folder_id=20, q="", order="updated"),
        covers={},
        q="",
        view="grid",
        order="updated",
    )
    assert "This folder is empty." in empty
    none = fv.level_html(
        fv.arrange(ROWS, INFO, folder_id=None, q="zzz", order="updated"),
        covers={},
        q="zzz",
        view="grid",
        order="updated",
    )
    assert "No decks matching “zzz” in any folder." in none
    assert (
        "<"
        not in fv.folder_tile_html(
            {"id": 5, "name": "<b>x</b>", "private": False, "decks": 0, "subfolders": 0, "where": ""},
            order="updated",
        )
        .split("title='")[1]
        .split("'")[0]
    )


# -- the pages -------------------------------------------------------------------------------------


async def test_my_decks_opens_folders_and_hides_the_backup_folder(stack: Stack) -> None:
    ark = stack.ark
    root = 1000 + ark.users["alice"]["id"]
    ark.folders["alice"] = [
        {"id": 501, "name": "Commander", "private": False},
        {"id": 502, "name": "Precons", "private": True, "parent": 501},
        {"id": 503, "name": "MTG Gateway Backups", "private": True},
    ]
    ark.decks[46] = dict(ark.decks[42], id=46, name="Precon in a folder")
    ark.deck_folder[46] = 502
    b = await linked_browser(stack)
    try:
        top = (await b.http.get("/decks", headers=NAV)).text
        assert "Folders <span class='n'>1</span>" in top and ">Commander</span>" in top
        assert "MTG Gateway Backups" not in top and "folder-field" not in top
        assert "data-new-folder" in top and "Precon in a folder" not in top
        inside = (await b.http.get("/decks?folder=502", headers=NAV)).text
        assert re.search(r"<li><a class='ui-cap' href='/decks\?folder=501'>Commander</a></li>", inside)
        assert "Precon in a folder" in inside and "Sample Commander Deck" not in inside
        assert "<input type='hidden' name='folder' value='502'>" in inside
        found = (await b.http.get("/decks?folder=502&q=sample", headers=NAV)).text  # every folder
        assert "Sample Commander Deck" in found and "Precon in a folder" not in found
        api = (await b.http.get("/api/decks/mine", params={"shape": "list", "folder": "501"})).json()
        assert ">Precons</span>" in api["html"] and "title='Private folder'" in api["html"]
        # the tree was read once for all of that: it is kept like the deck list
        assert ark.calls.count(("GET", "/api/decks/folderTree/")) == 1
        # the old Folders page's address goes to My decks (its boxes do the folder work now)
        gone = await b.http.get("/folders", headers=NAV)
        assert gone.status_code == 303 and gone.headers["location"] == "/decks"
        assert root  # the top level is never a tile
    finally:
        await b.aclose()


async def test_the_backup_folder_stays_hidden_with_backups_switched_off(stack: Stack) -> None:
    """With MTG_ARCHIDEKT_BACKUPS off the gateway's backup folder still never
    shows on My decks or the Folders page, and a deck in it is handled as with backups on: kept
    out of My decks and listed as a backup copy."""
    ark = stack.ark
    ark.folders["alice"] = [
        {"id": 501, "name": "Commander", "private": False},
        {"id": 503, "name": "MTG Gateway Backups", "private": True},
    ]
    ark.decks[46] = dict(ark.decks[42], id=46, name="Copy in the backup folder")
    ark.deck_folder[46] = 503
    decks = stack.h.app.state.gateway.decks
    stack.h.app.state.gateway.live.set("archidekt_backups", False, by="admin-1")
    b = await linked_browser(stack)
    try:
        top = (await b.http.get("/decks", headers=NAV)).text
        assert "Folders <span class='n'>1</span>" in top and ">Commander</span>" in top
        assert "MTG Gateway Backups" not in top and "Copy in the backup folder" not in top
        assert "MTG Gateway Backups" not in (await b.http.get("/decks?folder=503", headers=NAV)).text
        tree = (await b.http.get("/decks/api/folders")).json()
        assert [f["name"] for f in tree["folders"] if f["depth"]] == ["Commander"]
        assert [d["id"] for d in await decks.backup_copies("user-1")] == ["46"]  # conftest's member
    finally:
        await b.aclose()


async def test_the_server_refuses_the_backup_folder_as_a_target(stack: Stack) -> None:
    """A hand-made POST cannot move a deck into the backup folder (or a folder
    in it), rename it, or make a folder inside it; each is refused as the move by name refuses
    it, and nothing is sent. A deck already in it keeps its place when its own folder is posted."""
    import pytest

    from mtg_gateway.decks import DeckError

    ark = stack.ark
    ark.folders["alice"] = [
        {"id": 501, "name": "Commander", "private": False},
        {"id": 503, "name": "MTG Gateway Backups", "private": True},
        {"id": 504, "name": "Old copies", "private": True, "parent": 503},
    ]
    ark.decks[46] = dict(ark.decks[42], id=46, name="Copy in the backup folder")
    ark.deck_folder[46] = 503
    b = await linked_browser(stack)
    try:
        csrf = await b.csrf()
        calls = len(ark.calls)
        for path, data in (
            ("/decks/42/move", {"folder_id": "503"}),
            ("/decks/42/move", {"folder_id": "504"}),
            ("/folders", {"action": "rename", "folder_id": "503", "name": "Mine now"}),
            ("/folders", {"action": "rename", "folder_id": "504", "name": "Mine now"}),
            ("/folders", {"action": "create", "name": "Inside", "parent_id": "503"}),
            ("/folders", {"action": "create", "name": "Inside", "parent_id": "503", "confirmed": "1"}),
        ):
            r = await b.http.post(path, data={"csrf": csrf, **data})
            assert r.status_code == 400 and "No folder is called" in r.text, (path, data, r.text[:300])
        assert all(m == "GET" for m, _p in ark.calls[calls:]), ark.calls[calls:]
        assert ark.deck_folder.get(42) is None
        assert [f["name"] for f in ark.folders["alice"]] == ["Commander", "MTG Gateway Backups", "Old copies"]
    finally:
        await b.aclose()
    decks = stack.h.app.state.gateway.decks
    sub = "user-1"  # the signed-in member (conftest)
    with pytest.raises(DeckError) as exc:
        await decks.move_deck(sub, "42", 503)
    assert str(exc.value) == "No folder is called 'MTG Gateway Backups'. Your folders: Commander"
    assert (await decks.move_deck(sub, "46", 503))["changed"] is False


async def test_a_folder_cannot_be_named_like_the_backup_folder(stack: Stack) -> None:
    """Renaming a folder to the backup folder's name, or making one with it, would hide that
    folder and list its decks as backup copies (the folder list matches the name exactly anywhere,
    and in any capitals directly under the top level), so both are refused and nothing is sent.
    The same name in other capitals deeper down is not the backup folder and is allowed."""
    ark = stack.ark
    ark.folders["alice"] = [
        {"id": 501, "name": "Commander", "private": False},
        {"id": 502, "name": "Cube", "private": False, "parent": 501},
    ]
    b = await linked_browser(stack)
    try:
        csrf = await b.csrf()
        calls = len(ark.calls)
        for data in (
            {"action": "rename", "folder_id": "501", "name": "MTG Gateway Backups"},
            {"action": "rename", "folder_id": "501", "name": "mtg gateway BACKUPS"},
            {"action": "rename", "folder_id": "502", "name": "MTG Gateway Backups"},
            {"action": "create", "name": "MTG Gateway Backups", "confirmed": "1"},
            {"action": "create", "name": "Mtg Gateway Backups", "confirmed": "1"},
            {"action": "create", "name": "MTG Gateway Backups", "parent_id": "501", "confirmed": "1"},
        ):
            r = await b.http.post("/folders", data={"csrf": csrf, **data})
            assert r.status_code == 400, (data, r.text[:300])
            words = f"'{data['name']}' is the name of the gateway's backup folder. Pick another name."
            assert words in r.text or html.escape(words) in r.text, (data, r.text[:300])
        assert all(m == "GET" for m, _p in ark.calls[calls:]), ark.calls[calls:]
        # other capitals below the top level are another folder to the gateway, so allowed
        r = await b.http.post(
            "/folders",
            data={"csrf": csrf, "action": "rename", "folder_id": "502", "name": "mtg gateway backups"},
        )
        assert r.status_code in (200, 303), r.text[:300]
        assert [f["name"] for f in ark.folders["alice"]] == ["Commander", "mtg gateway backups"]
    finally:
        await b.aclose()


async def test_a_folder_write_drops_the_kept_tree(stack: Stack) -> None:
    ark = stack.ark
    ark.folders["alice"] = [{"id": 601, "name": "Brews", "private": False}]
    b = await linked_browser(stack)
    try:
        assert ">Brews</span>" in (await b.http.get("/decks", headers=NAV)).text
        svc = stack.h.app.state.gateway.decks
        sub = next(iter(svc.folder_trees._entries))
        await svc.rename_folder(sub, 601, "Brews renamed")
        assert ">Brews renamed</span>" in (await b.http.get("/decks", headers=NAV)).text
    finally:
        await b.aclose()


async def test_home_goldfish_and_compare_do_without_deck_js(stack: Stack) -> None:
    """Home loads only decks.js; Goldfish and Compare load the card viewer
    without deck.js; the deck page and My decks still load it."""
    b = await linked_browser(stack)
    try:
        home = (await b.http.get("/", headers=NAV)).text
        assert "/static/deck.js" not in home and "/static/decks.js" in home
        for path in ("/decks/42/goldfish", "/decks/42/compare"):
            text = (await b.http.get(path, headers=NAV)).text
            assert "/static/deck.js" not in text and "/static/cardview.js" in text, path
        for path in ("/decks/42", "/decks"):
            assert "/static/deck.js" in (await b.http.get(path, headers=NAV)).text, path
    finally:
        await b.aclose()
