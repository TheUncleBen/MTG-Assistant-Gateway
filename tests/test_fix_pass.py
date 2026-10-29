"""The fix pass after the menus and responsive layout build, without a browser: the deck search's
colour modes ("Including these" first, kept by the gateway; "At most these" by Archidekt), its sort
direction, the links that keep working, the results bar's count, the folder tile's lock and backup
mark, the guide's Decks row, History without Restore for a deck gone from Archidekt, and the
wording in the scripts and cards the pages load."""

from __future__ import annotations

import copy
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import mtg_gateway
from mtg_gateway import browse, guide, history_view
from mtg_gateway.folderview import folder_tile_html

from .conftest import COLLECTION, Stack, stack_with
from .test_browse_collection import NAV, linked

stack_options = stack_with(COLLECTION)
PKG = Path(mtg_gateway.__file__).parent


class _Params(dict):
    def getlist(self, key: str) -> list[str]:
        v = self.get(key)
        return list(v) if isinstance(v, list) else [v] if v is not None else []


def _query(**kw: object) -> dict:
    return browse.read_query(_Params(kw))


# -- C1 and C2: the colour modes and the sort direction, in the query ------------------------------


def test_including_these_is_the_first_colour_mode_and_the_default() -> None:
    assert [m for m, _t in browse.COLOUR_MODES] == ["incl", "exact", "atmost"]
    assert browse.COLOUR_MODES[0][1] == "Including these colors"
    assert _query(c=["U", "G"])["colour_mode"] == "incl"
    assert _query(c=["U"], cm="exact")["colour_mode"] == "exact"  # an old link keeps working
    assert _query(c=["U"], cm="atmost")["colour_mode"] == "atmost"
    assert _query(c=["U"], cm="nonsense")["colour_mode"] == "incl"
    assert _query(cm="exact")["colour_mode"] == "incl"  # no colours: nothing to match


def test_including_these_colours_are_the_gateways_filter_not_archidekts() -> None:
    assert browse.archidekt_query(_query(c=["U", "G"]))["colors"] == ""
    assert browse.archidekt_query(_query(c=["U", "G"], cm="exact"))["colors"] == "UG"
    assert browse.archidekt_query(_query(c=["U", "G"], cm="atmost"))["colors"] == "UG"
    rows = [
        {"id": 1, "colors": {"U": 3, "G": 2}},
        {"id": 2, "colors": {"U": 3}},
        {"id": 3, "colors": {"U": 1, "G": 1, "W": 1}},
        {"id": 4, "colors": {}},
    ]
    ids = lambda mode: [r["id"] for r in browse.matching_colours(rows, "UG", mode)]  # noqa: E731
    assert ids("incl") == [1, 3]
    assert ids("exact") == [1]
    assert ids("atmost") == [1, 2, 4]


def test_the_direction_goes_into_archidekts_order() -> None:
    assert _query()["direction"] == "auto"
    assert _query(dir="asc")["direction"] == "asc" and _query(dir="up")["direction"] == "auto"
    assert browse.archidekt_order("-viewCount", "auto") == "-viewCount"
    assert browse.archidekt_order("-viewCount", "desc") == "-viewCount"
    assert browse.archidekt_order("-viewCount", "asc") == "viewCount"
    assert browse.archidekt_order("edhBracket", "desc") == "-edhBracket"
    assert browse.archidekt_order("edhBracket", "asc") == "edhBracket"
    assert browse.archidekt_query(_query(order="-size", dir="asc"))["order_by"] == "size"
    assert browse.archidekt_query(_query(order="-size", dir="desc"))["order_by"] == "-size"
    assert browse.archidekt_query(_query(order="-size"))["order_by"] == "-size"


def test_search_links_carry_only_what_differs_from_the_defaults() -> None:
    assert browse.search_link(_query(c=["U", "G"])) == "/search?c=U&c=G"
    assert browse.search_link(_query(c=["U"], cm="exact")) == "/search?c=U&cm=exact"
    assert browse.search_link(_query(c=["U"], cm="atmost")) == "/search?c=U&cm=atmost"
    assert browse.search_link(_query(order="-size", dir="asc")) == "/search?order=-size&dir=asc"
    assert browse.search_link(_query()) == "/search"
    link = browse.search_link(_query(c=["U"], cm="exact", order="-size", dir="desc"), page=2)
    assert parse_qs(urlparse(link).query) == {
        "c": ["U"],
        "cm": ["exact"],
        "order": ["-size"],
        "dir": ["desc"],
        "page": ["2"],
    }


def test_the_count_is_what_the_gateway_kept_for_the_modes_it_filters() -> None:
    found = {"count": 40, "decks": []}
    assert browse.count_text(found, _query(c=["U"]), shown=3) == "3 decks"
    assert browse.count_text(found, _query(c=["U"], cm="exact"), shown=1) == "1 deck"
    assert browse.count_text(found, _query(c=["U"], cm="atmost"), shown=3) == "40 decks"
    assert browse.count_text(found, _query(), shown=3) == "40 decks"
    assert browse.count_text(None, _query()) == "Decks"


async def test_the_deck_search_keeps_including_decks_itself_and_sends_the_direction(stack: Stack) -> None:  # noqa: F811
    """Over the fake Archidekt: "Including these" sends no colour filter and lists only the decks
    with every picked colour; "At most these" is Archidekt's colorIdentity filter; the direction
    is the order with or without its leading "-"; an old "Exactly these" link still works."""
    b = await linked(stack)
    ark = stack.ark
    try:
        for did, colors in ((101, {"W": 4}), (102, {"U": 2, "G": 3, "W": 1})):
            d = copy.deepcopy(ark.decks[42])
            d["id"], d["name"], d["colors"] = did, f"Deck {did}", colors
            ark.decks[did] = d
        r = await b.http.get("/search?c=U&c=G", headers=NAV)
        assert r.status_code == 200
        sent = ark.search_params[-1]
        assert "colors" not in sent and "colorIdentity" not in sent, sent
        assert "data-deck-id='42'" in r.text and "data-deck-id='102'" in r.text
        assert "data-deck-id='101'" not in r.text
        assert ">2 decks<" in r.text
        assert "<option value='incl' selected>Including these colors</option>" in r.text

        r = await b.http.get("/search?c=U&c=G&cm=atmost", headers=NAV)
        sent = ark.search_params[-1]
        assert sent["colors"] == "U,G" and sent["colorIdentity"] == "true", sent
        assert "<option value='atmost' selected>At most these colors</option>" in r.text

        r = await b.http.get("/search?c=U&c=G&cm=exact", headers=NAV)
        assert "data-deck-id='42'" in r.text and "data-deck-id='102'" not in r.text
        assert ">1 deck<" in r.text

        r = await b.http.get("/search?format=3&order=-size&dir=asc", headers=NAV)
        assert ark.search_params[-1]["orderBy"] == "size"
        assert "<option value='asc' selected>Ascending</option>" in r.text
        await b.http.get("/search?format=3&order=-size&dir=desc", headers=NAV)
        assert ark.search_params[-1]["orderBy"] == "-size"
        await b.http.get("/search?format=3&order=-size", headers=NAV)
        assert ark.search_params[-1]["orderBy"] == "-size"
        # Load more carries the search its results are of
        r = await b.http.get("/search/api/decks?c=U&c=G&page=1", headers={"Accept": "application/json"})
        assert r.status_code == 200
        body = r.json()
        assert body["shown"] == 2 and "data-deck-id='101'" not in body["html"], body
    finally:
        await b.aclose()


# -- C7: the name box ---------------------------------------------------------------------------------


async def test_the_deck_search_name_box_says_what_it_takes(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        r = await b.http.get("/search", headers=NAV)
        assert "Deck name, or an Archidekt link or deck ID" in r.text
        assert "placeholder='Deck name, link or ID'" in r.text
    finally:
        await b.aclose()


# -- B1 and D: the folder tile ------------------------------------------------------------------------


def _tile(**over: object) -> dict:
    t = {"id": 501, "name": "Brews", "decks": 2, "subfolders": 0, "private": False, "where": ""}
    t.update(over)
    return t


def test_a_private_folders_lock_takes_the_icons_place() -> None:
    plain = folder_tile_html(_tile(), order="name")
    private = folder_tile_html(_tile(private=True), order="name")
    assert "<span class='ic'>" in plain and "lock" not in plain
    assert "<span class='ic lock' title='Private folder'>" in private
    assert "<span class='sr-only'>Private</span>" in private
    assert "lock" not in private.split("class='kebab'")[1]  # nothing over or after the ⋮
    assert private.index("class='ic lock'") < private.index("class='kebab'")


def test_the_backup_folders_tile_is_marked_for_its_menu() -> None:
    assert "data-backup" not in folder_tile_html(_tile(), order="name")
    assert "<li class='folder' data-folder-id='9' data-folder-name='MTG Assistant backups' data-backup>" in (
        folder_tile_html(_tile(id=9, name="MTG Assistant backups", backup=True), order="name")
    )


# -- C3: the guide ------------------------------------------------------------------------------------


def test_the_guides_decks_row_says_how_folders_are_made_and_managed() -> None:
    page = guide.guide_body(site="Gateway", writes_enabled=True, has_collection=True)
    assert (
        "<strong>+ New folder</strong> on My decks makes a folder; a folder tile's menu (its ⋮, a "
        "right-click, or press and hold on a phone) renames, moves or deletes it." in page
    )


# -- D: History and a deck gone from Archidekt --------------------------------------------------------


def _snapshot(**over: object) -> dict:
    s = {
        "snapshot_id": "snap-1",
        "deck_id": "42",
        "deck_name": "Aesi",
        "taken_at": 1_700_000_000,
        "card_count": 100,
    }
    s.update(over)
    return s


def test_history_offers_no_restore_for_a_deck_gone_from_archidekt() -> None:
    """History learns a deck is gone from the member's Archidekt deck list (decks.mark_gone sets
    ``deck_gone`` on a snapshot older than the list whose deck the list lacks); such a row has no
    Restore (review first), in the table or in its row menu (menus.js reads the form)."""
    kept = history_view.build_events([], [_snapshot()], [], csrf_input="")
    gone = history_view.build_events([], [_snapshot(deck_gone=True)], [], csrf_input="")
    assert "Restore (review first)" in kept[0]["action"] and "hy-restore" in kept[0]["action"]
    assert gone[0]["action"] == "" and "hy-restore" not in gone[0]["details"]
    assert "This deck is no longer on your Archidekt account" in gone[0]["details"]
    assert "Restore (review first)" in history_view.events_html(kept, now=1_700_000_100)
    assert "Restore (review first)" not in history_view.events_html(gone, now=1_700_000_100)


# -- C4, C5, C6, C8, C9, C10, C12: the wording the scripts and cards carry -----------------------------


def _static(name: str) -> str:
    return (PKG / "static" / name).read_text(encoding="utf-8")


def test_the_compare_and_bookmark_boxes_and_the_unreadable_answer() -> None:
    menus = _static("menus.js")
    assert 'placeholder: "Archidekt link or deck ID"' in menus
    assert '"Pick a deck, or paste an Archidekt link or deck ID."' in menus
    assert '"This adds “" + d.name + "” to your bookmarks on Archidekt."' in menus
    assert "deck id" not in menus and "deck Id" not in menus  # "deck ID" everywhere
    assert (
        'var ODD = "The gateway sent an answer this page could not read. Reload the page to see whether '
        'the change was made.";'
    ) in _static("cardmenu.js")


def test_a_deck_with_no_name_reads_unnamed_deck_in_the_deck_list_card() -> None:
    card = _static("cards/deck-list-card.html")
    assert 'String(d.name || "Unnamed deck")' in card and "Untitled" not in card
    assert "Untitled deck" not in _static("menus.js") and "Untitled deck" not in _static("decks.js")


def test_the_scan_pages_wording() -> None:
    scan = (PKG / "scan" / "static" / "scan.js").read_text(encoding="utf-8")
    # the shutter is Scan card in a browser; in the Android app it opens the phone's camera (0.7.46)
    assert "const shutterLabel = app ? 'Scan with the phone camera' : 'Scan card';" in scan
    assert "title: shutterLabel, 'aria-label': shutterLabel," in scan
    assert "Shoot" not in scan  # that word is the Android camera screen's own
    assert "told(msg, 'Name copied.')" in scan
    assert "throw new Error('Give the scan a name.')" in scan
    assert "{ text: 'Add to deck…', icon: 'plus', href: '/cards?q=' + encodeURIComponent(name) }" in scan
    assert (
        "items.push({ text: 'Add to binder', icon: 'collection', onClick: () => CM.collect(name) })" in scan
    )
    assert "document.body.hasAttribute('data-watchlist')" in scan
    assert "items.push({ text: 'Watch this card', icon: 'bookmark'" in scan


def test_new_member_text_quotes_names_with_curly_quotes() -> None:
    """Every name a box or a notice quotes sits in “ and ”: no straight quotes around a name."""
    for name in ("menus.js", "cardmenu.js", "decks.js"):
        # a Scryfall search (!"name") is not member text
        lines = [ln for ln in _static(name).splitlines() if "scryfall.com" not in ln]
        assert not [ln for ln in lines if '\\"" +' in ln or '+ "\\""' in ln], name
    scan = (PKG / "scan" / "static" / "scan.js").read_text(encoding="utf-8")
    assert '\\"${' not in scan and '`"' not in scan, "scan.js"
