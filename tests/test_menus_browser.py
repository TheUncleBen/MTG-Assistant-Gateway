"""The deck, folder, card and row menus and their boxes in headless Chromium (static/menus.js on
static/cardmenu.js): a deck tile's menu opens from a right-click, its ⋮, Shift+F10 and a press and
hold replayed as Android sends it (no tile left stuck); Esc, a press outside and the phone's Back
close it; the owner's items against someone else's; Watch deck price only with watchlists on. Each
box talks to the gateway's JSON routes: Rename, Move to folder (the tree as radios, the top level
first), Delete (the typed name, in any capitals), Export, Compare with, + New folder (two steps),
Rename folder, Move folder (itself and its own folders left out), Delete folder (the counts, the
progress line, then My decks with its notice) and the refusal over the backup folder; the Settings
page's collaborator forms ask in a box first. The rows of Proposals, History, Reports and the
Watchlist, Card search's tiles, the Collection's rows and the editor's rows have their menus.
Skipped without a Chromium build."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from pathlib import Path

import httpx
import pytest

from .test_cardsearch_browser import _page
from .test_deck_menu_browser import _overflow
from .test_editor_browser import _link
from .test_history_page import seed_history, store_report
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

OWN = [
    "Open",
    "Open in a new tab",
    "Edit deck",
    "Goldfish",
    "Run full test",
    "Clone",
    "Rename…",
    "Move to folder…",
    "Export…",
    "Compare with…",
    "Watch deck price",
    "Deck settings",
    "Copy link",
    "Open on Archidekt",
    "Delete deck…",
]
OTHERS = [
    "Open",
    "Open in a new tab",
    "Clone into my decks",
    "Goldfish",
    "Run full test",
    "Export…",
    "Compare with…",
    "Bookmark",
    "Watch deck price",
    "Copy link",
    "Open on Archidekt",
]
FOLDER = [
    "Open",
    "Open in a new tab",
    "New folder inside…",
    "Rename…",
    "Move to folder…",
    "Copy link",
    "Open on Archidekt",
    "Delete folder…",
]


def _exe() -> str | None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _server(tmp_path: Path, **over: object) -> Server:
    """alice's folders as the folder routes' tests have them: Commander (Precons and Brews
    inside), Archive and the backup folder; deck 42 in Brews, 201 in Precons, 202 in Commander, a
    card package in Precons. Writes on; watchlists on unless said otherwise."""
    over.setdefault("writes_enabled", True)
    over.setdefault("watchlists_enabled", True)
    s = Server(tmp_path, **over)
    ark = s.ark
    ark.folders["alice"] = [
        {"id": 501, "name": "Commander", "private": False},
        {"id": 502, "name": "Precons", "private": False, "parent": 501},
        {"id": 503, "name": "Brews", "private": True, "parent": 501},
        {"id": 504, "name": "Archive", "private": False},
        {"id": 505, "name": s.app.state.gateway.decks.settings.archidekt_backup_folder, "private": True},
    ]
    for did, name in ((201, "Precon deck"), (202, "Loose deck")):
        deck = copy.deepcopy(ark.decks[42])
        deck["id"] = did
        deck["name"] = name
        ark.decks[did] = deck
        ark.users["alice"]["decks"].append(did)
    ark.deck_folder.update({42: 503, 201: 502, 202: 501})
    ark.packages[502] = [{"id": 900, "name": "Ramp package"}]
    ark.next_folder_id = 600  # a new folder's id, past the ones above
    s.start()
    return s


def _items(page) -> list[str]:
    return [t.strip() for t in page.locator(".ctxmenu[role=menu] [role=menuitem]").all_inner_texts()]


def _menu(page):
    menu = page.locator(".ctxmenu[role=menu]")
    menu.wait_for(timeout=3000)
    return menu


def _box(page):
    box = page.locator(".mtgbox[role=dialog]:not([hidden])")
    box.wait_for(timeout=3000)
    return box


def _gone(page) -> None:
    """The box closes with a short transition (none under reduced motion): wait until it is gone."""
    page.locator(".mtgbox-scrim").first.wait_for(state="detached", timeout=3000)


def _notice(page) -> str:
    return page.locator("main .notice.ok").first.inner_text()


def _pick(page, text: str) -> None:
    page.locator(
        ".ctxmenu[role=menu] [role=menuitem]", has_text=re.compile(rf"^\s*{re.escape(text)}\s*$")
    ).click()


# -- the deck tile's menu ---------------------------------------------------------------------------


def test_a_deck_tile_menu_opens_four_ways_and_lists_the_owners_actions(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _link(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, 1280, "dark", exe)
            page.goto(f"{server.base}/decks?folder=503", wait_until="networkidle")
            tile = page.locator("li.deck[data-deck-id='42']")
            assert tile.get_attribute("data-own") == "1" and tile.get_attribute("data-folder") == "503"
            # a right-click
            tile.locator(".name").click(button="right")
            menu = _menu(page)
            assert menu.locator(".head").inner_text() == "Sample Commander Deck"
            assert _items(page) == OWN
            assert page.evaluate("() => document.activeElement.getAttribute('role')") == "menuitem"
            assert menu.locator("[role=menuitem].danger").inner_text().strip() == "Delete deck…"
            assert _overflow(page) == 0
            # Esc closes it and the focus goes back to the tile's link
            page.keyboard.press("Escape")
            assert page.locator(".ctxmenu").count() == 0
            assert page.evaluate("() => document.activeElement.getAttribute('href')") == "/decks/42"
            # Shift+F10 on the focused tile
            page.keyboard.press("Shift+F10")
            _menu(page)
            assert _items(page) == OWN
            page.mouse.click(5, 5)
            page.wait_for_timeout(100)
            assert page.locator(".ctxmenu").count() == 0
            # the ⋮, a real button outside the tile's link
            kebab = tile.locator("button.kebab")
            assert kebab.get_attribute("aria-label") == "Menu for Sample Commander Deck"
            assert tile.locator("a button").count() == 0
            kebab.click()
            _menu(page)
            assert _items(page) == OWN
            # Open in a new tab is a link that opens one; Open on Archidekt too
            assert page.locator(".ctxmenu a[target=_blank][href='/decks/42']").count() == 1
            assert (
                page.locator(".ctxmenu a[target=_blank][href='https://archidekt.com/decks/42']").count() == 1
            )
            page.keyboard.press("Escape")
            # the tile is still a link: a click opens the deck
            tile.locator(".name").click()
            page.wait_for_url(re.compile(r"/decks/42$"), timeout=5000)
            # Home's recent decks: the same menu from the ⋮ over the picture
            page.goto(f"{server.base}/", wait_until="networkidle")
            page.locator(".home .recent .rd[data-deck-id='42'] button.kebab").wait_for(timeout=5000)
            page.locator(".home .recent .rd[data-deck-id='42'] button.kebab").click()
            _menu(page)
            assert _items(page) == OWN
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


def test_someone_elses_deck_and_watchlists_off_change_the_items(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path, watchlists_enabled=False)
    try:
        sid = _link(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, 1280, "light", exe)
            page.goto(f"{server.base}/decks?folder=501", wait_until="networkidle")
            page.locator("li.deck[data-deck-id='202'] .name").click(button="right")
            _menu(page)
            assert _items(page) == [x for x in OWN if x != "Watch deck price"]
            page.keyboard.press("Escape")
            # a deck in the public listing is someone else's here
            page.goto(f"{server.base}/users/alice", wait_until="networkidle")
            tile = page.locator("li.deck[data-deck-id='42']")
            if tile.count():
                assert tile.get_attribute("data-own") == "0"
                tile.locator(".name").click(button="right")
                _menu(page)
                assert _items(page) == [x for x in OTHERS if x != "Watch deck price"]
                _pick(page, "Bookmark")
                box = _box(page)
                assert box.locator("h2").inner_text() == "Bookmark this deck on Archidekt?"
                box.locator("button", has_text="Bookmark").click()
                page.locator(".toast.cards-toast", has_text="bookmarked on Archidekt").wait_for(timeout=3000)
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


def test_a_hold_on_a_deck_tile_opens_its_menu_and_leaves_nothing_stuck(tmp_path: Path) -> None:
    """A press and hold on a phone, replayed as Chrome on Android sends it: the touch starts, its
    own drag starts (refused), the touch is cancelled and its contextmenu event comes. The menu
    opens as a bottom sheet, Back closes it, and a tap on the tile still opens the deck."""
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _link(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, 390, "dark", exe)
            page.goto(f"{server.base}/decks?folder=503&view=list", wait_until="networkidle")
            tile = page.locator("li.deck[data-deck-id='42']")
            tile.scroll_into_view_if_needed()
            box = tile.locator(".info").bounding_box()
            x, y = box["x"] + 30, box["y"] + box["height"] / 2
            cdp = page.context.new_cdp_session(page)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
            page.wait_for_timeout(500)
            refused = tile.locator("a").evaluate(
                """(a) => {
                    const dt = new DataTransfer();
                    const init = {bubbles: true, cancelable: true, dataTransfer: dt};
                    const ev = new DragEvent("dragstart", init);
                    a.dispatchEvent(ev);
                    return ev.defaultPrevented;
                }"""
            )
            assert refused
            cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
            tile.evaluate(
                """(t, xy) => t.querySelector('.name').dispatchEvent(new MouseEvent("contextmenu",
                    {bubbles: true, cancelable: true, clientX: xy[0], clientY: xy[1]}))""",
                [x, y],
            )
            menu = _menu(page)
            assert page.locator(".ctxmenu").count() == 1 and _items(page) == OWN
            assert page.locator(".cs-scrim").count() == 1  # a sheet over a dimmed page
            b = menu.bounding_box()
            assert b["x"] == 0 and b["width"] == 390, b
            assert _overflow(page) == 0
            assert page.url.endswith("/decks?folder=503&view=list")
            # Back closes the sheet and stays on the page
            page.go_back()
            page.wait_for_timeout(300)
            assert page.locator(".ctxmenu").count() == 0 and "/decks" in page.url
            assert "/decks/42" not in page.url
            # a tap on the tile still opens the deck (nothing swallowed or faded)
            page.wait_for_timeout(500)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
            page.wait_for_timeout(80)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            page.wait_for_url(re.compile(r"/decks/42$"), timeout=5000)
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


# -- the deck boxes --------------------------------------------------------------------------------


def test_the_deck_boxes_rename_move_export_compare_and_delete(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    ark = server.ark
    try:
        sid = _link(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, 1280, "dark", exe)
            page.goto(f"{server.base}/decks?folder=503", wait_until="networkidle")
            # Rename
            page.locator("li.deck[data-deck-id='42'] button.kebab").click()
            _pick(page, "Rename…")
            box = _box(page)
            assert box.locator("h2").inner_text() == "Rename deck"
            name = box.locator("input#mb-name")
            assert name.input_value() == "Sample Commander Deck"
            name.fill("")
            box.locator("button", has_text="Rename").click()
            assert box.locator(".boxnote").inner_text() == "Type a name."
            name.fill("Renamed deck")
            with page.expect_navigation(wait_until="networkidle"):
                box.locator("button", has_text="Rename").click()
            assert page.url.endswith("/decks?folder=503&ok=saved")
            assert _notice(page) == "Deck renamed on Archidekt."
            assert ark.decks[42]["name"] == "Renamed deck"
            # Move to folder: the tree as radios, the top level first, this folder checked
            page.locator("li.deck[data-deck-id='42'] button.kebab").click()
            _pick(page, "Move to folder…")
            box = _box(page)
            assert box.locator("h2").inner_text() == "Move “Renamed deck”"
            labels = [t.strip() for t in box.locator(".tree label").all_inner_texts()]
            assert labels == ["Top level (no folder)", "Archive", "Commander", "Brews", "Precons"]
            assert box.locator(".tree input:checked").evaluate("i => i.value") == "503"
            box.locator(".tree label", has_text="Archive").click()
            with page.expect_navigation(wait_until="networkidle"):
                box.locator("button", has_text="Move").click()
            assert page.url.endswith("/decks?folder=503&ok=moved")
            assert _notice(page) == "Deck moved on Archidekt."
            assert ark.deck_folder[42] == 504
            # Export: a wide box, the texts under tabs and the seven files
            page.goto(f"{server.base}/decks?folder=504", wait_until="networkidle")
            page.locator("li.deck[data-deck-id='42'] button.kebab").click()
            _pick(page, "Export…")
            box = _box(page)
            assert "wide" in (box.get_attribute("class") or "")
            assert box.locator("h2").inner_text() == "Export “Renamed deck”"
            tabs = [t.strip() for t in box.locator("[role=tab]").all_inner_texts()]
            # (the sample deck has no sideboard or maybeboard, so that tab is left out)
            assert tabs == ["Archidekt import text", "Plain decklist", "Files"]
            assert "Sol Ring" in box.locator("textarea").input_value()
            box.locator("[role=tab]", has_text="Files").click()
            assert box.locator(".files a[download]").count() == 7
            assert box.locator("textarea").is_hidden()
            assert box.locator(".acts button", has_text="Copy").is_hidden()
            page.keyboard.press("Escape")
            _gone(page)
            # Compare with: the member's decks, then today's comparison page
            page.locator("li.deck[data-deck-id='42'] button.kebab").click()
            _pick(page, "Compare with…")
            box = _box(page)
            assert box.locator("h2").inner_text() == "Compare “Renamed deck” with"
            # the deck list fills in after the box opens: wait for it, not a fixed moment
            loose = box.locator("select#mb-cmp option", has_text="Loose deck")
            loose.wait_for(state="attached", timeout=5000)
            options = [t.strip() for t in box.locator("select#mb-cmp option").all_inner_texts()]
            assert "Renamed deck" not in options and "Loose deck" in options
            assert options[-1] == "Any Archidekt deck (paste a link)…"
            box.locator("select#mb-cmp").select_option("202")
            with page.expect_navigation(wait_until="networkidle"):
                box.locator("button", has_text="Compare").click()
            assert page.url.endswith("/decks/42/compare?with=202")
            # Delete: today's words, the typed name in any capitals, the red button
            page.goto(f"{server.base}/decks?folder=504", wait_until="networkidle")
            page.locator("li.deck[data-deck-id='42'] button.kebab").click()
            _pick(page, "Delete deck…")
            box = _box(page)
            box.locator("input#mb-typed").wait_for(timeout=3000)
            assert box.locator("h2").inner_text() == "Delete “Renamed deck”?"
            assert "This deletes the deck (" in box.locator(".body p").first.inner_text()
            red = box.locator("button.danger", has_text="Delete this deck")
            assert red.is_disabled()
            assert box.locator("button", has_text="Keep it").count() == 1
            box.locator("input#mb-typed").fill("renamed DECK")
            assert red.is_enabled()
            with page.expect_navigation(wait_until="networkidle"):
                red.click()
            assert page.url.endswith("/decks?ok=deleted")
            assert _notice(page).startswith("Deck deleted on Archidekt.")
            assert 42 not in ark.decks
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


# -- the folder tile's menu and its boxes ----------------------------------------------------------


def test_the_folder_menu_new_rename_and_move_boxes(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    ark = server.ark
    try:
        sid = _link(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, 1280, "light", exe)
            page.goto(f"{server.base}/decks", wait_until="networkidle")
            # the heading: + New folder beside New deck, the one orange button
            head = page.locator(".ui-head.phead")
            assert head.locator("h1").inner_text() == "My decks"
            assert head.locator(".acts .btn-primary").inner_text().strip() == "New deck"
            assert page.locator(".listbar a[href='/folders']").count() == 0
            assert page.locator("form.openform").count() == 0
            # + New folder: Name and Inside, Next, then the question, Back, then Create folder
            head.locator("[data-new-folder]").click()
            box = _box(page)
            box.locator("input#mb-fname").wait_for(timeout=3000)
            assert box.locator("h2").inner_text() == "New folder"
            inside = [t.strip() for t in box.locator("select#mb-finside option").all_inner_texts()]
            assert inside == ["Top level (no folder)", "Archive", "Commander", "Brews", "Precons"]
            box.locator("input#mb-fname").fill("Cube")
            box.locator("button", has_text="Next").click()
            page.locator(".mtgbox h2", has_text="Create this folder?").wait_for(timeout=3000)
            question = "Create the folder “Cube” in your top level on Archidekt?"
            assert box.locator(".body").inner_text() == question
            assert [t.strip() for t in box.locator(".acts button").all_inner_texts()] == [
                "Back",
                "Create folder",
            ]
            box.locator("button", has_text="Back").click()
            assert box.locator("input#mb-fname").input_value() == "Cube"
            box.locator("button", has_text="Next").click()
            page.locator(".mtgbox h2", has_text="Create this folder?").wait_for(timeout=3000)
            with page.expect_navigation(wait_until="networkidle"):
                box.locator("button", has_text="Create folder").click()
            assert page.url.endswith("/decks?ok=folder_created")
            assert _notice(page) == "Folder created on Archidekt."
            cube = [f for f in ark.folders["alice"] if f["name"] == "Cube"]
            assert cube and cube[0].get("parent") is None
            # the folder tile's menu
            tile = page.locator("li.folder[data-folder-id='504']")
            assert tile.get_attribute("data-folder-name") == "Archive"
            tile.locator(".name").click(button="right")
            menu = _menu(page)
            assert menu.locator(".head").inner_text() == "Archive" and _items(page) == FOLDER
            assert page.locator(".ctxmenu a[href='/decks?folder=504']").count() == 2
            # Rename
            _pick(page, "Rename…")
            box = _box(page)
            assert box.locator("h2").inner_text() == "Rename folder"
            assert box.locator("input#mb-fname").input_value() == "Archive"
            box.locator("input#mb-fname").fill("Old lists")
            with page.expect_navigation(wait_until="networkidle"):
                box.locator("button", has_text="Rename").click()
            assert page.url.endswith("/decks?ok=folder_renamed")
            assert _notice(page) == "Folder renamed on Archidekt."
            assert [f["name"] for f in ark.folders["alice"] if f["id"] == 504] == ["Old lists"]
            # Move: Commander, its own folders left out, with the note
            page.locator("li.folder[data-folder-id='501'] button.kebab").click()
            _pick(page, "Move to folder…")
            box = _box(page)
            box.locator(".tree").wait_for(timeout=3000)
            assert box.locator("h2").inner_text() == "Move the folder “Commander”"
            labels = [t.strip() for t in box.locator(".tree label").all_inner_texts()]
            assert labels == ["Top level (no folder)", "Cube", "Old lists"]
            assert box.locator(".body .muted").inner_text() == (
                "Everything inside moves with it. A folder can't go inside itself, so it and its own "
                "folders are not listed."
            )
            box.locator(".tree label", has_text="Old lists").click()
            with page.expect_navigation(wait_until="networkidle"):
                box.locator("button", has_text="Move").click()
            assert page.url.endswith("/decks?ok=folder_moved")
            assert _notice(page) == "Folder moved on Archidekt."
            assert [f.get("parent") for f in ark.folders["alice"] if f["id"] == 501] == [504]
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


def test_the_delete_folder_box_counts_asks_for_the_name_and_shows_the_progress(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    ark = server.ark
    try:
        sid = _link(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, 390, "dark", exe)
            page.goto(f"{server.base}/decks", wait_until="networkidle")
            page.locator("li.folder[data-folder-id='501'] button.kebab").click()
            _menu(page)
            _pick(page, "Delete folder…")
            box = _box(page)
            box.locator("input#mb-typed").wait_for(timeout=3000)
            assert box.locator("h2").inner_text() == "Delete the folder “Commander”?"
            body = box.locator(".body").inner_text()
            assert "This deletes, on Archidekt:" in body
            assert (
                "3 decks: each gets a snapshot under History first, and a private copy in your backup "
                "folder when backups are on" in body
            )
            assert "1 card package: no snapshot or copy, it is lost" in body
            assert "2 folders inside it, and the folder itself" in body
            assert (
                "The gateway makes the snapshots and copies first and stops if any of them fails. Then "
                "Archidekt deletes everything in one step, which can't be undone." in body
            )
            assert box.locator("label[for=mb-typed]").inner_text() == "Type the folder's name to confirm"
            red = box.locator("button.danger", has_text="Delete folder")
            keep = box.locator("button", has_text="Keep it")
            assert red.is_disabled() and keep.count() == 1
            # a sheet from the bottom on a phone
            b = box.bounding_box()
            assert b["x"] == 0 and b["width"] == 390 and _overflow(page) == 0, b
            box.locator("input#mb-typed").fill("COMMANDER")
            assert red.is_enabled()
            red.click()
            page.locator(".mtgbox .progress").wait_for(timeout=3000)
            page.wait_for_url(re.compile(r"/decks\?ok=folder_deleted$"), timeout=15000)
            assert _notice(page).startswith("Folder deleted on Archidekt.")
            assert ark.folder_deletes == [{"items": [{"id": 501, "type": "folder"}]}]
            assert page.locator("li.folder").count() == 1  # Archive alone (the backup folder hidden)
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


def test_a_folder_holding_the_backup_folder_is_refused_in_the_box(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    ark = server.ark
    try:
        for f in ark.folders["alice"]:
            if f["id"] == 505:
                f["parent"] = 504  # the backup folder inside Archive
        sid = _link(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, 1280, "light", exe)
            page.goto(f"{server.base}/decks", wait_until="networkidle")
            page.locator("li.folder[data-folder-id='504'] button.kebab").click()
            _pick(page, "Delete folder…")
            box = _box(page)
            page.locator(".mtgbox h2", has_text="can't be deleted here").wait_for(timeout=3000)
            assert box.locator("h2").inner_text() == "“Archive” can't be deleted here"
            assert box.locator(".body").inner_text().strip()
            assert [t.strip() for t in box.locator(".acts button").all_inner_texts()] == ["OK"]
            box.locator(".acts button").click()
            _gone(page)
            assert ark.folder_deletes == []
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


# -- the Settings page: collaborators ask in a box -------------------------------------------------


def test_the_collaborator_form_asks_in_a_box_before_it_is_sent(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _link(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, 1280, "dark", exe)
            page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
            section = page.locator("#collaborators")
            assert section.get_attribute("data-deck-name") == "Sample Commander Deck"
            page.locator("#collabname").fill("bob")
            calls = len(server.ark.calls)
            section.locator("form.addtag button").click()
            box = _box(page)
            assert box.locator("h2").inner_text() == "Let “bob” edit “Sample Commander Deck” on Archidekt?"
            assert box.locator(".body").inner_text().strip() == section.get_attribute("data-note")
            assert [t.strip() for t in box.locator(".acts button").all_inner_texts()] == [
                "Cancel",
                "Add “bob”",
            ]
            box.locator("button", has_text="Cancel").click()
            _gone(page)
            assert len(server.ark.calls) == calls
            assert "/settings" in page.url
            # Add sends the form with its confirmation; no confirmation page in between
            section.locator("form.addtag button").click()
            with page.expect_navigation(wait_until="networkidle"):
                _box(page).locator("button", has_text="Add “bob”").click()
            assert page.locator("form.confirmform").count() == 0
            assert page.locator("#collaborators").count() == 1
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


# -- rows: Proposals, History, Reports, the Watchlist; cards on Card search, the Collection, the editor


def _seed_rows(server: Server) -> str:
    sid = _link(server)
    decks = server.app.state.gateway.decks
    asyncio.run(decks.propose("user-1", "42", [{"action": "add", "card_name": "Arcane Signet"}]))
    seed_history(server.db, "user-1", now=int(time.time()))
    store_report(server.db, "user-1", "rep_menu", deck_id="42", deck_name="Sample Commander Deck")
    now = int(time.time())
    with server.db.tx() as c:
        c.execute(
            "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, scryfall_id, "
            "alert_pct, alert_above, alert_below, created_at) "
            "VALUES ('w_one', 'user-1', 'Sol Ring', '', '', 'nonfoil', 's-one', NULL, NULL, NULL, ?)",
            (now,),
        )
        c.execute(
            "INSERT INTO watch_decks (sub, deck_id, deck_name, cards_json, refreshed_at, created_at) "
            "VALUES ('user-1', '42', 'Sample Commander Deck', ?, ?, ?)",
            (json.dumps([{"qty": 100}]), now, now),
        )
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/collection").text).group(1)
    r = h.post(
        "/collection/api/add",
        headers={"X-CSRF-Token": csrf},
        json={"items": [{"name": "Sol Ring", "quantity": 2}], "source": "scan"},
    )
    assert r.status_code == 200, r.text
    return sid


def test_the_rows_of_the_list_pages_have_their_menus(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _seed_rows(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, 1280, "dark", exe)
            # Proposals: a waiting change may be applied from its row
            page.goto(f"{server.base}/proposals", wait_until="networkidle")
            row = page.locator(".pr-table .tr", has_text="Arcane Signet").first
            assert row.locator("button.kebab.inline").count() == 1 and row.locator("a button").count() == 0
            row.locator("button.kebab").click()
            _menu(page)
            assert _items(page) == ["Review", "Apply", "Reject", "Open the deck"]
            assert page.locator(".ctxmenu a[href='/decks/42']").count() == 1
            page.keyboard.press("Escape")
            # History: Details, Restore (review first), Open report, Open the deck
            page.goto(f"{server.base}/history", wait_until="networkidle")
            rows = page.locator(".hy-table .tr:not(.th):not(.daygroup):not(.group)")
            found: set[str] = set()
            for i in range(min(rows.count(), 12)):
                rows.nth(i).locator(".title").click(button="right")
                _menu(page)
                found.update(_items(page))
                page.keyboard.press("Escape")
            assert {"Details", "Open the deck"} <= found, found
            assert "Restore (review first)" in found, found
            assert found <= {"Details", "Restore (review first)", "Open report", "Open the deck"}, found
            # Reports
            page.goto(f"{server.base}/reports", wait_until="networkidle")
            row = page.locator(".rp-t .tr", has_text="Sample Commander Deck").first
            assert row.locator("a button").count() == 0
            row.locator("button.kebab.inline").click()
            _menu(page)
            assert _items(page) == ["Open", "Play more games", "Copy as Markdown", "Open the deck"]
            assert page.locator(".ctxmenu a[href='/history/reports/rep_menu#forge-more']").count() == 1
            page.keyboard.press("Escape")
            # the Watchlist: a card row and a deck row
            page.goto(f"{server.base}/watchlist", wait_until="networkidle")
            page.locator("#wr-w_one .title").click(button="right")
            _menu(page)
            assert _items(page) == [
                "Open card",
                "Add to deck…",
                "Add to collection",
                "Price alert…",
                "Copy name",
                "Open on Scryfall",
                "Remove",
            ]
            page.keyboard.press("Escape")
            page.locator("#wr-d42 .act button.kebab").click()
            _menu(page)
            assert _items(page) == ["Open", "Price alert…", "Remove"]
            _pick(page, "Price alert…")
            assert page.locator("#wx-d42").is_visible()  # a deck row has no alert fields: its details open
            # a card row's Price alert… is a box (M16) with the row's three fields, posting as the row's form
            page.locator("#wr-w_one .act button.kebab").click()
            _menu(page)
            _pick(page, "Price alert…")
            box = page.locator(".mtgbox[role=dialog]:not([hidden])")
            box.wait_for(timeout=3000)
            assert box.locator("h2").inner_text().startswith("Price alert: ")
            assert box.locator("input[name=alert_pct]").count() == 1
            assert box.locator("form input[name=action][value=alerts]").count() == 1
            assert box.locator(".acts button", has_text="Save alert").is_visible()
            page.keyboard.press("Escape")
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


def test_cards_have_their_menus_on_search_the_collection_and_the_editor(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _seed_rows(server)
        with sync_playwright() as p:
            browser, page, errors = _page(p, server, sid, 1280, "light", exe)
            # Card search: every tile has a ⋮; the menu adds to the collection
            page.goto(f"{server.base}/cards?q=sol", wait_until="networkidle")
            page.locator("#cardgrid .cc").first.wait_for(timeout=5000)
            tile = page.locator("#cardgrid .cc").first
            assert tile.locator("button.kebab").count() == 1
            tile.locator("button.kebab").click()
            _menu(page)
            items = _items(page)
            assert items[:3] == ["Open card", "Add to deck…", "Add to collection"]
            assert "Copy name" in items and items[-1] == "Open on Scryfall"
            _pick(page, "Add to collection")
            page.locator(".toast.cards-toast", has_text="added to your collection").wait_for(timeout=5000)
            # the Collection: the quantity row, Details…, Remove from collection
            page.goto(f"{server.base}/collection?view=list", wait_until="networkidle")
            row = page.locator("ul.colllist li.row[data-id]").first
            row.locator(".name").click(button="right")
            menu = _menu(page)
            items = _items(page)
            assert items[:2] == ["Open card", "Add to deck…"]
            assert "Details…" in items and items[-1] == "Remove from collection"
            assert "Add to collection" not in items
            assert menu.locator(".mqty .lbl").inner_text() == "Quantity"
            n = int(menu.locator(".mqty .n").inner_text())
            menu.locator(".mqty button.step", has_text="+").click()
            page.wait_for_timeout(800)
            assert page.locator(".ctxmenu").count() == 1  # the menu stays for more steps
            assert int(menu.locator(".mqty .n").inner_text()) == n + 1
            _pick(page, "Details…")
            page.wait_for_function("() => !document.getElementById('col-details').hidden")
            assert page.locator("#col-details input[name=id]").input_value() == row.get_attribute("data-id")
            page.keyboard.press("Escape")
            page.wait_for_function("() => document.getElementById('col-details').hidden")
            # the row's own ⋮ opens the same shared menu
            row.locator(".kebab").click()
            assert page.locator(".ctxmenu").count() == 1
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)
            assert page.locator(".ctxmenu").count() == 0
            # the editor's rows
            page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
            erow = page.locator("li.erow").first
            erow.locator(".name").click(button="right")
            _menu(page)
            items = _items(page)
            assert items[:3] == ["Open card", "Add to collection", "Watch this card"]
            assert "Change printing…" in items and "Remove from deck" in items
            assert "Copy name" in items and items[-1] == "Open on Scryfall"
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()
