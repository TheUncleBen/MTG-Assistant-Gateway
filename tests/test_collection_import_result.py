"""The Collection page's import says what it did: the parser's own reason when it refuses the
whole import, the rows kept as English because Archidekt's collection has no code for their
language, the rows added to a copy already held in another language, and the rows not found. The
details cross the redirect by a one-time token, never as text in the URL."""

from __future__ import annotations

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
        "<div class='notice ok importres' role='status'><p>Imported 7 cards into your Archidekt "
        "collection.</p><ul><li>Kept as English, because Phyrexian is not a language Archidekt stores "
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
        "<div class='notice ok importres' role='status'><p>Imported 2 cards into your Archidekt "
        "collection.</p><ul><li>Added to a copy you already had in another language, so each counts in "
        "that copy&#x27;s language: row 1 (Opt, English added to Chinese (Traditional)), row 2 (Forest, "
        "English added to Korean).</li></ul></div>"
    )
    single = import_result(parse_collection_import("1 Opt\n"), {"added": [{"id": 1}]})
    assert "<p>Imported 1 card into your Archidekt collection.</p></div>" in import_result_html(single)
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
        assert "Imported 3 cards into your Archidekt collection." in page
        assert (
            "Kept as English, because Phyrexian is not a language Archidekt stores "
            "for collection cards: line 3 (Opt)." in page
        )
        assert (
            "Added to a copy you already had in another language, so it counts as English: line 2 "
            "(Sol Ring, Japanese added to English)." in page
        )
        assert "Not found, so not imported: line 4 (Foo Bar)." in page
        assert "<details class='addfold' open>" in page
        # read once: the same address falls back to the plain notice
        again = (await b.http.get(where, headers=NAV)).text
        assert "Imported into your Archidekt collection." in again and "Foo Bar" not in again
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
        assert "Phyrexian" not in page and "Imported into your Archidekt collection." in page
    finally:
        await bob.aclose()
