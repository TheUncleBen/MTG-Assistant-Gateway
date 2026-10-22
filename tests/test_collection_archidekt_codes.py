"""The collection CSV export writes Archidekt's own condition and language
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


def test_a_language_archidekt_cannot_store_is_kept_as_english_and_named():
    items = parse_collection_import("Name,Language\nSol Ring,EN\nOpt,XX\n")
    assert [(i["name"], i["language"], i.get("language_given")) for i in items] == [
        ("Sol Ring", "EN", None),
        ("Opt", "EN", "XX"),
    ]
    assert items[1]["line"] == 3


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
        assert r.headers["location"].startswith("/collection?ok=imported&r="), r.headers
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
        assert "role='cell'>Damaged (DMG)</span>" in page.text
        # a language Archidekt's collection has no code for is imported as English, and named
        odd = "Name,Language\nForest,XX\n"
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": odd})
        assert r.headers["location"].startswith("/collection?ok=imported&r=")
        shown = await b.http.get(r.headers["location"], headers=NAV)
        assert (
            "Kept as English, because “XX” is not a language Archidekt stores "
            "for collection cards: line 2 (Forest)." in shown.text
        )
        forest = [
            c for c in stack.ark.collections["alice"].values() if c["card"]["oracleCard"]["name"] == "Forest"
        ]
        assert [c["language"] for c in forest] == [1]  # English
    finally:
        await b.aclose()


def test_a_raw_archidekt_wait_reaches_the_busy_page_in_whole_seconds() -> None:
    """Archidekt's wait on a collection call is a float of seconds: it becomes whole seconds, at
    least 1, as for the deck pages, never dropped for the busy page's 60 s default."""
    from mtg_gateway.archidekt import ArchidektError
    from mtg_gateway.busy import busy_plan
    from mtg_gateway.collection import _wrap

    err = _wrap(ArchidektError("rate_limited", "slow down", retry_after=44.2))
    assert err.retry_after == 45 and busy_plan(err).retry_after == 45
    assert _wrap(ArchidektError("rate_limited", "slow down", retry_after=0.3)).retry_after == 1
    assert _wrap(ArchidektError("rate_limited", "slow down")).retry_after is None
    # a longer wait than the gateway pauses for (Pacer.MAX_RETRY_AFTER) says the gateway's pause
    assert _wrap(ArchidektError("rate_limited", "slow down", retry_after=300.0)).retry_after == 120
    # a Retry-After of 0 (or a date already past) pauses nothing: 1, not the busy page's 60 s default
    assert _wrap(ArchidektError("rate_limited", "slow down", retry_after=0.0)).retry_after == 1
