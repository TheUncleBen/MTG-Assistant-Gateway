"""Polish of 0.7.46's parity and quality-of-life features (the audit-fix pass): Binder tags as
colour chips on the tiles and rows, the Details box's tag chips, Clear binder in the Binder's More
menu, the stopped import's strip, the restore question as a box on the Binder page, History's
Snapshots kept widget counting the binder copy apart, the Remove for good review row's label, and
the privacy words that say what the gateway keeps of the binder."""

from __future__ import annotations

import re

from mtg_gateway import collection_bulk

from .conftest import COLLECTION, Stack, stack_with
from .test_binder_release3 import _seed, _tags
from .test_browse_collection import NAV, linked

stack_options = stack_with(COLLECTION)


def _binder_copy(stack: Stack, sub: str = "user-1") -> str:
    """A copy of the binder kept before a clear, straight into the database."""
    snap = {
        "name": collection_bulk.NAME,
        "kind": "binder",
        "tags": [{"id": 1, "name": "Trade", "color": "#336699"}],
        "cards": [
            {
                "card_id": 9003,
                "name": "Sol Ring",
                "quantity": 2,
                "set": "cmr",
                "collector_number": "472",
                "finish": "nonfoil",
                "condition": "NM",
                "language": "EN",
                "tags": ["Trade"],
                "purchase_price": None,
                "scryfall_id": "",
            }
        ],
    }
    assert stack.gw.db.save_snapshot(
        "bindercopy1",
        owner_sub=sub,
        deck_id=collection_bulk.TARGET,
        proposal_id=None,
        fingerprint="",
        deck=snap,
    )
    return "bindercopy1"


async def test_binder_tiles_and_rows_carry_the_tags_as_colour_chips(stack: Stack) -> None:
    ids = _tags(stack, "Trade", "Keep")
    _seed(stack, 2, tags=[ids["Trade"]])
    b = await linked(stack)
    try:
        for view in ("grid", "list"):
            page = (await b.http.get(f"/collection?view={view}", headers=NAV)).text
            cards = page.split("id='cards'", 1)[1]
            chips = re.findall(r"<span class='pill tag'[^>]*>.*?</span>", cards)
            assert len(chips) == 2, view  # one tag on each of the two entries
            assert all("Trade" in c and "--tagc:#336699" in c for c in chips), chips
            # the chip has the colour as a dot beside the name, never as the text's colour
            assert "<i class='sw' aria-hidden='true'></i><span class='t'>Trade</span>" in chips[0], chips[0]
            # the page's script redraws a row's chips after a save: the colours are on the list
            assert "data-tag-colors='" in page and "Keep" in page.split("data-tag-colors='", 1)[1][:200]
    finally:
        await b.aclose()


async def test_the_details_box_and_the_tags_box_show_each_tag_with_its_colour(stack: Stack) -> None:
    ids = _tags(stack, "Trade", "Keep")
    stack.ark.collection_tag_lists["alice"][ids["Keep"]]["color"] = "#2a7d2a"
    _seed(stack, 1)
    b = await linked(stack)
    try:
        page = (await b.http.get("/collection", headers=NAV)).text
        details = page.split("id='col-details'", 1)[1].split("</form>", 1)[0]
        ticks = re.findall(r"<label class='tagck'[^>]*>.*?</label>", details)
        assert len(ticks) == 2 and "<i class='sw' aria-hidden='true'></i>" in ticks[0], ticks
        assert "--tagc:#2a7d2a" in ticks[1] and "Keep" in ticks[1], ticks[1]
        # the Select bar's Tags… box: the same chips
        tags_box = page.split("id='col-bulk-tags'", 1)[1].split("</form>", 1)[0]
        assert tags_box.count("<i class='sw' aria-hidden='true'></i>") == 2
    finally:
        await b.aclose()


async def test_clear_binder_sits_in_the_binders_more_menu(stack: Stack) -> None:
    _seed(stack, 2)
    b = await linked(stack)
    try:
        page = (await b.http.get("/collection", headers=NAV)).text
        head = page.split("<div class='ui-head", 1)[1].split("<p class='ui-sub'", 1)[0]
        menu = re.search(r"<details class='dd colmore'>.*?</details>", head, re.S)
        assert menu is not None, head
        # the menu holds Export CSV and the destructive Clear binder… (a red item at the end)
        assert "/collection/export.csv" in menu.group(0)
        assert "class='danger'" in menu.group(0) and "value='clear'>" in menu.group(0)
        assert menu.group(0).index("export.csv") < menu.group(0).index("value='clear'")
        # outside the menu the head keeps only Add cards: no third button crowds a phone
        outside = head.replace(menu.group(0), "")
        assert "value='clear'" not in outside and "export.csv" not in outside
        assert "Add cards" in outside
    finally:
        await b.aclose()


async def test_a_stopped_import_is_a_warning_strip_with_the_bar_under_the_words(stack: Stack) -> None:
    _seed(stack, 1)
    b = await linked(stack)
    try:
        jobs = stack.gw.collection_jobs
        jobs.store.start(
            "user-1", "job1", "import", [{"name": f"Card {i}", "quantity": 1} for i in range(10)], None
        )
        jobs.store.update(
            "user-1",
            "job1",
            state="stopped",
            done=4,
            result={"added": [], "skipped": [], "stopped": "Archidekt is not answering right now"},
        )
        page = (await b.http.get("/collection?import=job1", headers=NAV)).text
        strip = re.search(r"<div class='notice warn importing stopped'.*?</div>", page, re.S)
        assert strip is not None and "role='alert'" in strip.group(0), page[:3000]
        s = strip.group(0)
        # the words, then the bar across the strip, then Resume (the main button) and Stop here
        assert s.index("<span class='itext'>") < s.index("<progress class='iprog'") < s.index("class='iacts'")
        assert "max='10' value='4'" in s and "aria-label='Lines done'" in s
        assert s.index("value='import_resume'") < s.index("value='import_done'")
        assert "class='btn btn-primary'" in s.split("value='import_resume'", 1)[1][:60]
    finally:
        await b.aclose()


async def test_restoring_onto_a_binder_that_is_not_empty_asks_in_a_box_on_the_binder_page(
    stack: Stack,
) -> None:
    _seed(stack, 2)
    b = await linked(stack)
    try:
        snap_id = _binder_copy(stack)
        r = await b.http.post(
            "/collection/restore", data={"csrf": await b.csrf("/history"), "snapshot_id": snap_id}
        )
        assert r.status_code == 303 and r.headers["location"] == f"/collection?restore={snap_id}", r.text
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        box = re.search(
            r"<div class='ui-box mtgbox[^>]*id='col-restore'.*?</div><div class='acts'>.*?</div>", page, re.S
        )
        assert box is not None, page[:2000]
        s = box.group(0)
        assert "data-open" in s and "Your binder is not empty" in s
        assert "It holds 2 entries now" in s and "the 2 cards of this copy" in s
        # the question's form: the copy, the confirmation, the one orange button and the way out
        assert f"name='snapshot_id' value='{snap_id}'" in s and "name='confirm' value='1'" in s
        assert "Add them to my binder" in s and "Keep my binder as it is" in s
        assert "href='/history?type=snapshots'" in s
        # a copy no longer kept: no box, the page says so
        gone = (await b.http.get("/collection?restore=nothing", headers=NAV)).text
        assert "id='col-restore'" not in gone and "no longer kept" in gone
    finally:
        await b.aclose()


async def test_snapshots_kept_counts_the_binder_copy_apart_from_the_decks(stack: Stack) -> None:
    _seed(stack, 1)
    b = await linked(stack)
    try:
        db = stack.gw.db
        _binder_copy(stack)
        hist = (await b.http.get("/history", headers=NAV)).text
        widget = hist.split("Snapshots kept", 1)[1].split("</div>", 1)[0]
        assert "of your binder" in widget and "on 0 decks" not in widget and "1 deck" not in widget, widget
        assert "Restore my binder…" in widget and "Restore a deck…" not in widget
        st = db.snapshot_stats("user-1")
        assert st == {"count": 1, "decks": 0, "binder": 1, "newest": st["newest"]}
        # with a deck snapshot too: the decks are counted without the binder, which is named apart
        assert db.save_snapshot(
            "decksnap1",
            owner_sub="user-1",
            deck_id="42",
            proposal_id=None,
            fingerprint="f",
            deck={"name": "D"},
        )
        st = db.snapshot_stats("user-1")
        assert st["count"] == 2 and st["decks"] == 1 and st["binder"] == 1
        hist = (await b.http.get("/history", headers=NAV)).text
        widget = hist.split("Snapshots kept", 1)[1].split("</div>", 1)[0]
        assert "on 1 deck and your binder" in widget, widget
        assert "Restore a deck…" in widget
    finally:
        await b.aclose()


async def test_the_remove_for_good_review_row_has_a_short_label(stack: Stack) -> None:
    ark = stack.ark
    name = ark.decks[42]["cards"][0]["card"]["oracleCard"]["name"]
    ark.add_removed_row(42, name, 2)
    b = await linked(stack)
    try:
        page = (await b.http.get("/history/archidekt?deck_id=42", headers=NAV)).text
        rel = re.search(r"name='relation_id' value='(\d+)'", page).group(1)
        r = await b.http.post(
            "/history/archidekt/remove",
            data={"csrf": await b.csrf("/history/archidekt?deck_id=42"), "deck_id": "42", "relation_id": rel},
        )
        assert r.status_code == 303, r.text
        review = (await b.http.get(r.headers["location"], headers=NAV)).text
        row = re.search(r"<li class='del action'><span class='act'>(.*?)</span>", review).group(1)
        assert row == "Remove", row
    finally:
        await b.aclose()


def test_the_privacy_words_say_what_the_gateway_keeps_of_the_binder() -> None:
    from mtg_gateway.guide import guide_body

    guide = guide_body(site="Gateway", writes_enabled=True, has_collection=True)
    part = guide.split("id='exp-privacy'", 1)[1].split("<section", 1)[0]
    assert "Nothing about the cards in your binder is stored" not in part
    assert "Clear binder" in part and "card names" in part and "import" in part, part


def test_the_docs_say_what_the_gateway_keeps_of_the_binder() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "docs"
    using = (root / "USING.md").read_text()
    privacy = using.split("## Your account and privacy", 1)[1].split("\n## ", 1)[0]
    assert "Clear binder" in privacy and "card names" in privacy and "import" in privacy
    onboarding = (root / "ONBOARDING.md").read_text().split("## 8. Privacy", 1)[1]
    assert "binder" in onboarding and "card names" in onboarding
