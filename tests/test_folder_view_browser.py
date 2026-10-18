"""My decks by folder in headless Chromium: a folder tile opens the folder, the breadcrumb goes
back up, the name filter finds a deck in any folder and says where it is, and Order by keeps the
open folder. At 360, 720 (Fold) and 1280 px, light and dark, grid and list: nothing scrolls
sideways, every link is uncovered and the folder text keeps a 4.5:1 contrast. Set
MTG_FOLDER_SHOTS=<dir> to also save screenshots. Skipped without a Chromium build."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mtg_gateway.archidekt_csv import parse_export, to_deck_json

from .test_card_search_filters_browser import CONTRAST, CONTROLS
from .test_cardsearch_browser import SIZES, _chromium, _page
from .test_editor_browser import _link
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_FOLDER_SHOTS")
LONG = "Commander decks in progress for Friday Night Magic at the local game store downtown"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    cards = parse_export((Path(__file__).parent / "fixtures" / "sample_deck.csv").read_text(encoding="utf-8"))
    ark = s.ark
    ark.folders["alice"] = [
        {"id": 501, "name": "Commander", "private": False},
        {"id": 502, "name": "Precons", "private": True, "parent": 501},
        {"id": 503, "name": LONG, "private": False, "parent": 501},
        {"id": 504, "name": "Archive", "private": False},
        {"id": 505, "name": "MTG Gateway backups", "private": True},
    ]
    for did, name, folder in (
        (61, "Atraxa precon", 502),
        (62, "Brewing Zur the Enchanter", 501),
        (63, "Old aggro list", 504),
    ):
        ark.decks[did] = to_deck_json(cards[:12], deck_id=did, name=name, owner="alice")
        ark.deck_folder[did] = folder
        ark.users["alice"]["decks"].append(did)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _names(page) -> list[str]:
    return page.locator("ul.decklist .deck .name").all_inner_texts()


def test_folders_open_the_breadcrumb_goes_back_and_the_filter_searches_everywhere(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        page.wait_for_selector("ul.folderlist", timeout=10_000)
        tiles = page.locator("ul.folderlist .folder .name").all_inner_texts()
        assert tiles == ["Archive", "Commander"], tiles  # A to Z, the backup folder never shows
        assert _names(page) == ["Sample Commander Deck"]
        page.locator("ul.folderlist .folder a", has_text="Commander").click()
        page.wait_for_selector("nav.crumbs")
        assert page.locator("nav.crumbs li").all_inner_texts()[-1].endswith("Commander")
        assert page.locator("ul.folderlist .folder .name").all_inner_texts() == [LONG, "Precons"]
        assert _names(page) == ["Brewing Zur the Enchanter"]
        page.locator("ul.folderlist .folder a", has_text="Precons").click()
        page.wait_for_selector("nav.crumbs li[aria-current] >> text=Precons")
        assert _names(page) == ["Atraxa precon"]
        assert page.locator("ul.folderlist").count() == 0  # no subfolders: no Folders area
        # Order by keeps the open folder
        with page.expect_navigation():
            page.locator("#f-order").select_option("name")
        assert "folder=502" in page.url and "order=name" in page.url, page.url
        assert _names(page) == ["Atraxa precon"]
        # the breadcrumb goes back up, keeping the order
        page.locator("nav.crumbs a", has_text="Commander").click()
        page.wait_for_selector("nav.crumbs li[aria-current] >> text=Commander")
        assert "order=name" in page.url
        page.locator("nav.crumbs a", has_text="Decks").click()
        page.wait_for_function("() => !document.querySelector('nav.crumbs')")
        # the filter, from inside a folder, finds decks in every folder and names each one's folder
        page.goto(f"{server.base}/decks?folder=502", wait_until="networkidle")
        page.locator("#q").fill("a")
        with page.expect_navigation():
            page.locator("#q").press("Enter")
        rows = dict(
            zip(
                _names(page),
                page.locator("ul.decklist .deck .sub").all_inner_texts(),
                strict=True,
            )
        )
        assert rows["Atraxa precon"].startswith("Commander › Precons"), rows
        assert rows["Old aggro list"].startswith("Archive"), rows
        assert "MTG Gateway backups" not in page.content()
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize("width,theme", SIZES)
@pytest.mark.parametrize("view", ["grid", "list"])
def test_my_decks_by_folder_fits_every_width(server: Server, width: int, theme: str, view: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        for where in ("", "folder=501", "folder=503", "q=precon"):
            page.goto(f"{server.base}/decks?view={view}&{where}", wait_until="networkidle")
            page.wait_for_selector("section.fv-area", timeout=10_000)
            assert page.evaluate(MEASURE) == [], where
            assert page.evaluate(CONTROLS, "main") == [], where
            texts = "nav.crumbs a, nav.crumbs span, .fv-area > h2, ul.folderlist .name, ul.folderlist .sub"
            assert page.evaluate(CONTRAST, texts) == [], where
            if SHOTS:
                tag = where.replace("=", "-") or "top"
                page.screenshot(
                    path=str(Path(SHOTS) / f"decks-{tag}-{view}-{width}-{theme}.png"), full_page=True
                )
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_backup_folder_name_is_refused_and_reads_everywhere(
    tmp_path: Path, width: int, theme: str
) -> None:
    """Manage folders refuses renaming a folder to the backup folder's name; the refusal fits
    and keeps a 4.5:1 contrast at every size."""
    server = Server(tmp_path, writes_enabled=True)
    server.ark.folders["alice"] = [
        {"id": 501, "name": "Commander", "private": False},
        {"id": 504, "name": "Archive", "private": False},
    ]
    server.start()
    try:
        _backup_name_refused(server, width, theme)
    finally:
        server.stop()


def _backup_name_refused(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/folders", wait_until="networkidle")
        page.select_option("#rfolder", "504")
        page.fill("#rname", "mtg gateway backups")
        with page.expect_navigation(wait_until="networkidle"):
            page.locator("form.folderform button", has_text="Rename").click()
        note = page.locator(".notice.error")
        assert note.inner_text() == (
            "'mtg gateway backups' is the name of the gateway's backup folder. Pick another name."
        )
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, ".notice.error") == []
        if SHOTS:
            note.scroll_into_view_if_needed()
            page.screenshot(path=str(Path(SHOTS) / f"backup-name-{width}-{theme}.png"))
        assert [f["name"] for f in server.ark.folders["alice"]] == ["Commander", "Archive"]
        assert errors == [], errors
        browser.close()
