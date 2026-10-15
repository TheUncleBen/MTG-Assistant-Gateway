"""A50 C1, C3, C4, C6, C7: the collection CSV export writes Archidekt's own condition and language
codes, the gateway's import reads a Language column and both code sets, and the Collection page
says how to map the columns on archidekt.com (which does not read column names)."""

from __future__ import annotations

import pytest

from mtg_gateway.collection import (
    EXPORT_COLUMN_TYPES,
    CollectionError,
    _item_options,
    export_help_html,
    parse_collection_import,
)

from .test_browse_collection import NAV, Stack, linked, stack_options  # noqa: F401  (fixture)


def test_import_reads_language_in_either_code_set_and_defaults_to_english():
    text = (
        "Quantity,Name,Language,Condition\n"
        "1,Sol Ring,JP,D\n1,Opt,ja,DMG\n1,Forest,SP,\n1,Island,,NM\n1,Swamp,CT,LP\n1,Arcane Signet,zhs,\n"
    )
    items = parse_collection_import(text)
    assert [(i["name"], i["language"], i["condition"]) for i in items] == [
        ("Sol Ring", "JA", "DMG"),
        ("Opt", "JA", "DMG"),
        ("Forest", "ES", ""),
        ("Island", "EN", "NM"),
        ("Swamp", "ZHT", "LP"),
        ("Arcane Signet", "ZHS", ""),
    ]
    assert parse_collection_import("Name,Lang\nSol Ring,KR\n")[0]["language"] == "KO"
    assert parse_collection_import("Quantity,Name\n1,Sol Ring\n")[0]["language"] == "EN"
    assert parse_collection_import("1 Sol Ring\n")[0]["language"] == "EN"


def test_an_unknown_language_refuses_the_import_like_other_bad_values():
    with pytest.raises(CollectionError, match=r"^line 3: language 'XX' is not a known language code$"):
        parse_collection_import("Name,Language\nSol Ring,EN\nOpt,XX\n")


def test_item_options_take_archidekt_codes():
    assert _item_options({"condition": "d", "language": "kr"}, 0)["condition"] == "DMG"
    assert _item_options({"language": "kr"}, 0)["language"] == "KO"
    assert _item_options({}, 0)["language"] == "EN"
    with pytest.raises(CollectionError, match="card 2: language must be one of EN, ES"):
        _item_options({"language": "PH"}, 2)


def test_export_help_gives_every_column_type():
    html = export_help_html()
    assert EXPORT_COLUMN_TYPES == (
        ("Quantity", "Quantity"),
        ("Name", "Card name"),
        ("Finish", "Foil/Variant"),
        ("Condition", "Condition"),
        ("Language", "Language"),
        ("Edition Code", "Edition code"),
        ("Edition Name", "Edition name"),
        ("Collector Number", "Collector number"),
        ("Scryfall ID", "Scryfall ID"),
        ("Date Added", "Ignore"),
    )
    for col, kind in EXPORT_COLUMN_TYPES:
        assert f"<th scope='row'>{col}</th><td>{kind}</td>" in html
    assert "Skip first line" in html and "Archidekt does not read the column names" in html


async def test_import_and_export_round_trip_archidekt_codes(stack: Stack) -> None:
    b = await linked(stack)
    try:
        page = await b.http.get("/collection", headers=NAV)
        assert "Importing the CSV on archidekt.com" in page.text and "Foil/Variant" in page.text
        csrf = await b.csrf("/collection")
        text = "Quantity,Name,Finish,Condition,Language\n2,Sol Ring,Normal,D,JP\n1,Opt,Foil,LP,CS\n"
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": text})
        assert r.headers["location"] == "/collection?ok=imported", r.headers
        recs = {c["card"]["oracleCard"]["name"]: c for c in stack.ark.collections["alice"].values()}
        # Archidekt's numbers: JP = 6, CS = 10, D = 5, LP = 2
        assert (recs["Sol Ring"]["language"], recs["Sol Ring"]["condition"]) == (6, 5)
        assert (recs["Opt"]["language"], recs["Opt"]["condition"]) == (10, 2)
        exported = (await b.http.get("/collection/export.csv")).text.splitlines()
        assert exported[0] == (
            "Quantity,Name,Finish,Condition,Language,Edition Code,Edition Name,Collector Number,"
            "Scryfall ID,Date Added"
        )
        rows = {line.split(",")[1]: line.split(",") for line in exported[1:]}
        assert rows["Sol Ring"][3:5] == ["D", "JP"] and rows["Opt"][3:5] == ["LP", "CS"]
        # the page keeps the gateway's own labels
        page = await b.http.get("/collection?view=list", headers=NAV)
        assert "<span class='pill cond'>DMG</span>" in page.text
        # an unknown language adds nothing and the page says the text could not be read
        bad = "Name,Language\nForest,XX\n"
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": bad})
        assert r.headers["location"] == "/collection?err=unreadable"
        assert "Forest" not in {
            c["card"]["oracleCard"]["name"] for c in stack.ark.collections["alice"].values()
        }
    finally:
        await b.aclose()
