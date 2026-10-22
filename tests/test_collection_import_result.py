"""The Collection page's import says what it did: the parser's own reason when it refuses the
whole import, the rows kept as English because Archidekt's collection has no code for their
language, the rows added to a copy already held in another language, and the rows not found. The
details cross the redirect by a one-time token, never as text in the URL."""

from __future__ import annotations

import html
import re

import pytest

from mtg_gateway.collection import (
    CollectionError,
    import_result,
    import_result_html,
    parse_collection_import,
)

from .test_browse_collection import NAV, Stack, linked, stack_options  # noqa: F401  (fixture)
from .test_decks_and_proxy import Browser


@pytest.mark.parametrize(
    ("given", "kept"),
    [("PH", "PH"), ("he", "he"), ("LA", "LA"), ("AR", "AR"), ("SA", "SA"), ("GRC", "GRC"), ("zz", "zz")],
)
def test_a_language_archidekt_cannot_store_is_english_with_the_value_kept(given: str, kept: str) -> None:
    items = parse_collection_import(f"Quantity,Name,Language\n1,Opt,JP\n2,Sol Ring,{given}\n")
    assert items[0]["language"] == "JA" and "language_given" not in items[0] and items[0]["line"] == 2
    assert (items[1]["language"], items[1]["language_given"], items[1]["line"]) == ("EN", kept, 3)


def test_the_language_names_and_an_empty_cell_are_read_without_a_note() -> None:
    items = parse_collection_import("Name,Language\nOpt,Japanese\nOpt,\nOpt,en\nOpt,Chinese (Simplified)\n")
    assert [i["language"] for i in items] == ["JA", "EN", "EN", "ZHS"]
    assert not any("language_given" in i for i in items)


def test_a_plain_list_has_no_line_numbers() -> None:
    items = parse_collection_import("2 Sol Ring\n1 Opt\n")
    assert [("line" in i, i["language"]) for i in items] == [(False, "EN"), (False, "EN")]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Quantity,Name\n1,Opt\nx,Sol Ring\n", "line 3: quantity 'x' is not a number"),
        ("Quantity,Name\n1,Opt\n1,\n", "line 3: no card name"),
        ("Card Name,Quantity\nOpt,1\n", None),  # "card name" is a Name column: no refusal
        ("   ", "paste or choose a CSV or a card list first"),
    ],
)
def test_refusal_reasons(text: str, reason: str | None) -> None:
    if reason is None:
        assert parse_collection_import(text)[0]["name"] == "Opt"
        return
    with pytest.raises(CollectionError) as caught:
        parse_collection_import(text)
    assert str(caught.value) == reason
    html = import_result_html({"refused": str(caught.value)})
    assert html == f"<p class='notice error'>Nothing was imported: {reason.replace(chr(39), '&#x27;')}.</p>"


def test_the_result_names_every_kind_of_row_and_escapes_them() -> None:
    items = parse_collection_import(
        "Quantity,Name,Language\n"
        "2,Sol Ring,PH\n"  # line 2: kept as English
        "1,<b>Foo</b>,EN\n"  # line 3: not found
        "3,Opt,JP\n"  # line 4: added to an English copy
        "1,Forest,PH\n"  # line 5: kept as English
        "1,Island,XYZ\n"  # line 6: kept as English, a value with no name
        "1,Mountain,PH\n"  # line 7: not found, so not named as kept
    )
    out = {
        "added": [{"id": 1}],
        "skipped": [{"index": 1}, {"index": 5}],
        "merged": [{"index": 2, "language": "JA", "existing_language": "EN"}],
    }
    result = import_result(items, out)
    assert result["cards"] == 7
    html = import_result_html(result)
    assert html == (
        "<div class='notice ok importres' role='status'><p>Imported 7 cards into your Collection on "
        "Archidekt.</p><ul><li>Kept as English, because Phyrexian is not a language Archidekt stores "
        "for collection cards: "
        "line 2 (Sol Ring), line 5 (Forest).</li>"
        "<li>Kept as English, because “XYZ” is not a language Archidekt stores "
        "for collection cards: line 6 (Island).</li>"
        "<li>Added to a copy you already had in another language, so it counts as English: line 4 (Opt, "
        "Japanese added to English).</li>"
        "<li>Not found, so not imported: line 3 (&lt;b&gt;Foo&lt;/b&gt;), line 7 (Mountain).</li></ul></div>"
    )


def test_one_card_two_existing_languages_and_nothing_added() -> None:
    items = parse_collection_import("1 Opt\n1 Forest\n")
    one = import_result(
        items,
        {
            "added": [{"id": 1}],
            "skipped": [],
            "merged": [
                {"index": 0, "language": "EN", "existing_language": "ZHT"},
                {"index": 1, "language": "EN", "existing_language": "KO"},
            ],
        },
    )
    assert import_result_html(one) == (
        "<div class='notice ok importres' role='status'><p>Imported 2 cards into your Collection on "
        "Archidekt.</p><ul><li>Added to a copy you already had in another language, so each counts in "
        "that copy&#x27;s language: row 1 (Opt, English added to Chinese (Traditional)), row 2 (Forest, "
        "English added to Korean).</li></ul></div>"
    )
    single = import_result(parse_collection_import("1 Opt\n"), {"added": [{"id": 1}]})
    assert "<p>Imported 1 card into your Collection on Archidekt.</p></div>" in import_result_html(single)
    none = import_result(items, {"added": [], "skipped": [{"index": 0}, {"index": 1}], "merged": []})
    assert import_result_html(none) == (
        "<div class='notice error importres' role='status'><p>Nothing was imported: no card in the list was "
        "found.</p><ul><li>Not found, so not imported: row 1 (Opt), row 2 (Forest)."
        "</li></ul></div>"
    )


async def test_import_through_the_page_reports_merges_and_misses_once(stack: Stack) -> None:
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        r = await b.http.post(
            "/collection",
            data={"csrf": csrf, "action": "import", "text": "Quantity,Name,Language\n1,Sol Ring,EN\n"},
        )
        assert re.fullmatch(r"/collection\?ok=imported&r=[\w-]{16}", r.headers["location"]), r.headers
        text = "Quantity,Name,Language\n2,Sol Ring,JP\n1,Opt,PH\n1,Foo Bar,EN\n"
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": text})
        where = r.headers["location"]
        assert where.startswith("/collection?ok=imported&r=") and "Foo" not in where
        recs = [
            c
            for c in stack.ark.collections["alice"].values()
            if c["card"]["oracleCard"]["name"] == "Sol Ring"
        ]
        assert [(c["quantity"], c["language"]) for c in recs] == [(3, 1)]  # topped up, still English
        page = (await b.http.get(where, headers=NAV)).text
        assert "Imported 3 cards into your Collection on Archidekt." in page
        assert (
            "Kept as English, because Phyrexian is not a language Archidekt stores "
            "for collection cards: line 3 (Opt)." in page
        )
        assert (
            "Added to a copy you already had in another language, so it counts as English: line 2 "
            "(Sol Ring, Japanese added to English)." in page
        )
        assert "Not found, so not imported: line 4 (Foo Bar)." in page
        assert "aria-labelledby='col-add-title' data-open>" in page
        assert "data-coltab='import' aria-selected='true'" in page
        # read once: the same address falls back to the plain notice
        again = (await b.http.get(where, headers=NAV)).text
        assert "Imported into your Collection on Archidekt." in again and "Foo Bar" not in again
        # a refusal names the line
        bad = "Quantity,Name\n1,Opt\nx,Sol Ring\n"
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": bad})
        assert r.headers["location"].startswith("/collection?err=unreadable&r=")
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "Nothing was imported: line 3: quantity &#x27;x&#x27; is not a number." in page
        # nothing found: the plain notice, with the rows not found
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": "1 Foo Bar\n"})
        assert r.headers["location"].startswith("/collection?err=nothingimported&r=")
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert (
            "Nothing was imported: no card in the list was found." in page
            and "Not found, so not imported: row 1 (Foo Bar)." in page
        )
    finally:
        await b.aclose()


async def test_a_result_token_is_only_read_by_the_member_who_imported(stack: Stack) -> None:
    alice = await linked(stack)
    try:
        csrf = await alice.csrf("/collection")
        text = "Quantity,Name,Language\n1,Opt,PH\n"
        r = await alice.http.post("/collection", data={"csrf": csrf, "action": "import", "text": text})
        where = r.headers["location"]
    finally:
        await alice.aclose()
    bob = Browser(stack.h)
    try:
        stack.h.idp.user = {
            **stack.h.idp.user,
            "sub": "bob-sub",
            "preferred_username": "bob",
            "email": "bob@x.test",
        }
        await bob.login("/collection")
        assert (await bob.link("amy", "pw-amy")).status_code == 303
        page = (await bob.http.get(where, headers=NAV)).text
        assert "Phyrexian" not in page and "Imported into your Collection on Archidekt." in page
    finally:
        await bob.aclose()


# A row's line is the physical line it starts on, so a quoted cell with a line break and
# blank lines at the top are counted, and a name with a line break is read with a space.
def test_lines_count_quoted_line_breaks_and_leading_blank_lines() -> None:
    items = parse_collection_import('Quantity,Name,Language\n1,"Sol\nRing",EN\n1,Opt,XX\n')
    assert [(i["name"], i["line"]) for i in items] == [("Sol Ring", 2), ("Opt", 4)]
    items = parse_collection_import("\n\n\nQuantity,Name,Language\n1,Opt,XX\n")
    assert items[0]["line"] == 5
    items = parse_collection_import('Quantity,Name\r\n1,"A\r\nB"\r\n\r\n1,Opt\r\n')
    assert [(i["name"], i["line"]) for i in items] == [("A B", 2), ("Opt", 5)]
    items = parse_collection_import('Quantity,Name,Language\n1,"Opt\u2028X",EN\n1,Y,ZZ\n')
    assert [i["line"] for i in items] == [2, 3]
    with pytest.raises(CollectionError, match="^line 5: quantity 'x' is not a number$"):
        parse_collection_import('Quantity,Name\n1,"A\nB\nC"\nx,Opt\n')
    with pytest.raises(CollectionError, match="^line 5: no card name$"):
        parse_collection_import('\nQuantity,Name\n1,"Sol\nRing"\n1,\n')


# A Condition the gateway does not know is imported as NM and the result names its line, as a
# language Archidekt cannot store is kept as English.
def test_an_unknown_condition_is_near_mint_and_named() -> None:
    items = parse_collection_import(
        "Quantity,Name,Condition\n1,Opt,NM\n1,Sol Ring,EX\n2,Forest,ex\n1,Island,D\n1,Swamp,Mint\n1,Foo,EX\n"
    )
    assert [(i["condition"], i.get("condition_given"), i["line"]) for i in items] == [
        ("NM", None, 2),
        ("NM", "EX", 3),
        ("NM", "ex", 4),
        ("DMG", None, 5),
        ("NM", "Mint", 6),
        ("NM", "EX", 7),
    ]
    result = import_result(items, {"added": [{"id": 1}], "skipped": [{"index": 5}], "merged": []})
    assert result["kept_condition"] == [
        {"condition": "EX", "rows": [{"where": "line 3", "name": "Sol Ring"}]},
        {"condition": "ex", "rows": [{"where": "line 4", "name": "Forest"}]},
        {"condition": "Mint", "rows": [{"where": "line 6", "name": "Swamp"}]},
    ]
    one = import_result_html(
        import_result(parse_collection_import("Quantity,Name,Condition\n1,Opt,EX\n"), {"added": [{"id": 1}]})
    )
    assert one == (
        "<div class='notice ok importres' role='status'><p>Imported 1 card into your Collection on "
        "Archidekt.</p><ul><li>Kept as Near Mint, because “EX” is not a condition Archidekt stores "
        "for collection cards: line 2 (Opt).</li></ul></div>"
    )
    two = import_result_html(
        import_result(
            parse_collection_import("Quantity,Name,Condition,Language\n1,Opt,EX,PH\n1,Forest,EX,\n"),
            {"added": [{"id": 1}]},
        )
    )
    assert (
        "<li>Kept as English, because Phyrexian is not a language Archidekt stores for collection cards: "
        "line 2 (Opt).</li><li>Kept as Near Mint, because “EX” is not a condition Archidekt stores for "
        "collection cards: line 2 (Opt), line 3 (Forest).</li>"
    ) in two


# A cell quoted in a refusal is cut, so a refusal kept for the page stays small.
def test_a_quoted_cell_in_a_refusal_is_cut() -> None:
    with pytest.raises(CollectionError) as caught:
        parse_collection_import("Quantity,Name\n" + "x" * 131_000 + ",Opt\n")
    assert str(caught.value) == "line 2: quantity 'xxxxxxxxxxxxxxxxxxxx…' is not a number"


TOO_LONG = 'a cell is longer than 131,072 characters; check for a quotation mark (") that is not closed'


# A cell over the csv module's limit, or a quantity int() refuses, is a refusal with its
# line, not an exception the page turns into HTTP 500.
@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Quantity,Name\n1,Opt\n1," + "x" * 140_000 + "\n", f"line 3: {TOO_LONG}"),
        ('Quantity,Name\n1,"Opt\n' + "x\n" * 70_000, f"line 2: {TOO_LONG}"),
        (
            "Quantity,Name\n" + "9" * 5000 + ",Opt\n",
            "line 2: quantity '99999999999999999999…' is not from 1 to 9,999",
        ),
        ("Quantity,Name\n1,Opt\n0,Opt\n", "line 3: quantity '0' is not from 1 to 9,999"),
        ("Quantity,Name\n10000,Opt\n", "line 2: quantity '10000' is not from 1 to 9,999"),
    ],
)
def test_huge_cells_are_refused_with_their_line(text: str, reason: str) -> None:
    with pytest.raises(CollectionError) as caught:
        parse_collection_import(text)
    assert str(caught.value) == reason
    assert parse_collection_import("Quantity,Name\n0009999,Opt\n")[0]["quantity"] == 9999


async def test_huge_cells_and_an_unknown_condition_through_the_page(stack: Stack) -> None:
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        for text, said in [
            ("Quantity,Name\n1," + "x" * 140_000 + "\n", "line 2: " + html.escape(TOO_LONG)),
            ("Quantity,Name\n" + "9" * 5000 + ",Opt\n", "line 2: quantity &#x27;99999999999999999999…&#x27;"),
            (
                "Quantity,Name\n" + "y" * 100_000 + ",Opt\n",
                "line 2: quantity &#x27;yyyyyyyyyyyyyyyyyyyy…&#x27;",
            ),
        ]:
            r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": text})
            assert r.status_code == 303, r.status_code
            assert r.headers["location"].startswith("/collection?err=unreadable&r=")
            page = (await b.http.get(r.headers["location"], headers=NAV)).text
            assert f"Nothing was imported: {said}" in page and "y" * 21 not in page
        text = "Quantity,Name,Condition\n1,Sol Ring,EX\n"
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": text})
        assert r.headers["location"].startswith("/collection?ok=imported&r=")
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert (
            "Kept as Near Mint, because “EX” is not a condition Archidekt stores for collection cards: "
            "line 2 (Sol Ring)." in page
        )
        recs = [
            c
            for c in stack.ark.collections["alice"].values()
            if c["card"]["oracleCard"]["name"] == "Sol Ring"
        ]
        assert [(c["quantity"], c["condition"]) for c in recs] == [(1, 1)]  # Archidekt's 1 is NM
    finally:
        await b.aclose()


@pytest.mark.parametrize("name_col", ["Card", "card name", "NAME"])
def test_every_name_column_the_csv_reader_knows_makes_the_text_a_csv(name_col: str) -> None:
    """A header with "Card" (one of the Name column's names) is read as a CSV, not as a plain
    list whose every line, the header included, would be a card name."""
    items = parse_collection_import(f"Quantity,{name_col},Set\n2,Sol Ring,cmr\n1,Opt,xln\n")
    assert [(i["name"], i["quantity"], i["set"], i.get("line")) for i in items] == [
        ("Sol Ring", 2, "cmr", 2),
        ("Opt", 1, "xln", 3),
    ]


@pytest.mark.parametrize(
    ("given", "code"),
    [
        ("Lightly Played", "LP"),
        ("near mint", "NM"),
        ("Moderately-Played", "MP"),
        ("HEAVILY  PLAYED", "HP"),
        ("Damaged", "DMG"),
    ],  # fmt: skip
)
def test_a_condition_written_out_is_its_code(given: str, code: str) -> None:
    """A Condition cell with the condition's name is read as its code, not kept as Near Mint."""
    item = parse_collection_import(f"Quantity,Name,Condition\n1,Opt,{given}\n")[0]
    assert item["condition"] == code and "condition_given" not in item


async def test_a_collection_edit_with_writes_switched_off_says_so_in_track_as_words(stack: Stack) -> None:
    """The shared refusal words: the writes-off notice on the Collection
    page reads as everywhere else and doesn't name the setting."""
    import dataclasses

    b = await linked(stack)
    decks = stack.h.app.state.gateway.decks
    decks.settings = dataclasses.replace(decks.settings, writes_enabled=False)
    try:
        csrf = await b.csrf("/collection")
        before = dict(stack.ark.collections.get("alice", {}))
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "add", "name": "Sol Ring"})
        assert r.headers["location"] == "/collection?err=writes", r.headers
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "Archidekt writes are switched off on this gateway, so nothing was sent to Archidekt." in page
        assert "MTG_WRITES_ENABLED" not in page
        assert stack.ark.collections.get("alice", {}) == before
    finally:
        await b.aclose()
