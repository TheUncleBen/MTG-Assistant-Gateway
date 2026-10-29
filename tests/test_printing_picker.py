"""The printing picker's server side (static/printpick.js): ``GET /scan/api/prints`` pages past
Scryfall's 175 with ``page`` (Scryfall's own has_more and next_page), filters by finish, frame and
border and sorts newest, oldest or cheapest on Scryfall's side, keeps every page in its cache and
refuses values it does not know; and the deck page's "Printings…" save: one set_printing
change through the deck edit endpoint, its Undo (the old set, number and finish), and a printing
not made in the card's finish moving the finish with it."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest

from .fake_printings import GOBLIN_TOKEN_ORACLE, SOL_RING_ORACLE, goblin_cards, sol_ring_printings
from .test_browse_collection import Stack, api, linked, stack_options  # noqa: F401 - fixture


def _prints_calls(st: Stack) -> list[dict[str, str]]:
    return [q for q in st.sf.print_searches if SOL_RING_ORACLE in q.get("q", "")]


async def test_printings_page_past_scryfalls_page_and_are_cached(stack: Stack) -> None:
    stack.sf.extra_cards = sol_ring_printings(fillers=7)
    stack.sf.page_size = 6  # Scryfall's is 175; small here so the card has three pages
    b = await linked(stack)
    try:
        r = await b.http.get(f"/scan/api/prints?oracle_id={SOL_RING_ORACLE}")
        assert r.status_code == 200, r.text
        one = r.json()
        total = len(sol_ring_printings(7)) + 3  # and the three Sol Rings of the recorded fixtures
        assert one["page"] == 1 and one["has_more"] is True and one["total_cards"] == total
        assert len(one["cards"]) == 6
        # the picker's tile needs these: set name, code, number, year, finishes, prices, frame, border
        first = one["cards"][0]
        for key in (
            "set_name",
            "set",
            "collector_number",
            "released_at",
            "finishes",
            "prices",
            "frame",
            "border",
        ):
            assert key in first, key
        r = await b.http.get(f"/scan/api/prints?oracle_id={SOL_RING_ORACLE}&page=2")
        two = r.json()
        assert two["page"] == 2 and two["has_more"] is True and len(two["cards"]) == 6
        three = (await b.http.get(f"/scan/api/prints?oracle_id={SOL_RING_ORACLE}&page=3")).json()
        assert three["has_more"] is False and len(three["cards"]) == total - 12
        seen = [(c["set"], c["collector_number"]) for c in one["cards"] + two["cards"] + three["cards"]]
        assert len(set(seen)) == total
        # Scryfall was asked for page 2 and 3 as its next_page names them, newest first
        calls = _prints_calls(stack)
        assert [c.get("page") for c in calls] == [None, "2", "3"]
        assert all(c["order"] == "released" and c["dir"] == "desc" and c["unique"] == "prints" for c in calls)
        # every page is kept: asking again costs Scryfall nothing
        for page in ("", "&page=2", "&page=3"):
            assert (
                await b.http.get(f"/scan/api/prints?oracle_id={SOL_RING_ORACLE}{page}")
            ).status_code == 200
        assert len(_prints_calls(stack)) == 3
        # a page past the end is an empty page, not an error
        four = (await b.http.get(f"/scan/api/prints?oracle_id={SOL_RING_ORACLE}&page=4")).json()
        assert four["cards"] == [] and four["has_more"] is False
    finally:
        await b.aclose()


async def test_printings_filter_and_sort_on_scryfalls_side(stack: Stack) -> None:
    stack.sf.extra_cards = sol_ring_printings()
    b = await linked(stack)
    try:
        base = f"/scan/api/prints?oracle_id={SOL_RING_ORACLE}"
        foil = (await b.http.get(base + "&finish=foil")).json()
        assert {(c["set"], c["collector_number"]) for c in foil["cards"]} >= {("sld", "1075")}
        assert all("foil" in c["finishes"] for c in foil["cards"])
        etched = (await b.http.get(base + "&finish=etched")).json()
        assert sorted(c["collector_number"] for c in etched["cards"]) == ["1074", "1075"]
        old = (await b.http.get(base + "&frame=1993")).json()
        assert [c["set"] for c in old["cards"]] == ["4ed", "lea"]  # newest first
        white = (await b.http.get(base + "&frame=1993&border=white")).json()
        assert [c["set"] for c in white["cards"]] == ["4ed"]
        borderless = (await b.http.get(base + "&border=borderless")).json()
        assert [(c["set"], c["collector_number"]) for c in borderless["cards"]] == [("sld", "1074")]
        oldest = (await b.http.get(base + "&order=oldest")).json()
        assert oldest["cards"][0]["set"] == "lea"
        cheap = (await b.http.get(base + "&order=cheapest")).json()
        usd = [float(c["prices"]["usd"]) for c in cheap["cards"] if c["prices"].get("usd")]
        assert usd == sorted(usd) and cheap["cards"][0]["set"] == "4ed"
        # a filter nothing matches: an empty list, not "no such card"
        none = await b.http.get(base + "&frame=future&border=gold")
        assert none.status_code == 200 and none.json()["cards"] == []
        # what Scryfall was asked
        qs = [(c["q"], c["order"], c["dir"]) for c in _prints_calls(stack)]
        assert (f"oracleid:{SOL_RING_ORACLE} is:foil", "released", "desc") in qs
        assert (f"oracleid:{SOL_RING_ORACLE} frame:1993 border:white", "released", "desc") in qs
        assert (f"oracleid:{SOL_RING_ORACLE}", "released", "asc") in qs
        assert (f"oracleid:{SOL_RING_ORACLE}", "usd", "asc") in qs
        # each filter is a page of its own in the cache
        before = len(_prints_calls(stack))
        await b.http.get(base + "&finish=foil")
        await b.http.get(base + "&order=cheapest")
        assert len(_prints_calls(stack)) == before
        # the default list still puts the released paper printings first (the default printing)
        plain = (await b.http.get(base)).json()["cards"]
        flags = [bool(c.get("unreleased") or c.get("paper") is False) for c in plain]
        assert flags == sorted(flags) and flags[-1] is True
    finally:
        await b.aclose()


async def test_one_printing_by_set_and_number(stack: Stack) -> None:
    """The picker's own printing, for a card with more printings than a page: one search by
    set and collector number, kept like every page; another card's printing is not given."""
    stack.sf.extra_cards = sol_ring_printings()
    b = await linked(stack)
    try:
        base = f"/scan/api/prints?oracle_id={SOL_RING_ORACLE}"
        one = (await b.http.get(base + "&set=CMR&number=472")).json()
        assert [(c["set"], c["collector_number"]) for c in one["cards"]] == [("cmr", "472")]
        assert _prints_calls(stack)[-1]["q"] == f"oracleid:{SOL_RING_ORACLE} set:cmr cn:472"
        assert (await b.http.get(base + "&set=cmr&number=472")).status_code == 200
        assert len(_prints_calls(stack)) == 1  # kept
        none = (await b.http.get(base + "&set=lea&number=999")).json()
        assert none["cards"] == [] and none["has_more"] is False
    finally:
        await b.aclose()


async def test_a_token_is_found_by_its_own_printing_and_lists_its_printings(stack: Stack) -> None:
    """Ben, 2026-10-28: "Goblin token" listed two Goblin creature cards and no token. The picker
    finds a row's card by its own set and number (GET /scan/api/printing), and the list asks
    Scryfall to include extras (tokens, emblems), which its search leaves out otherwise."""
    stack.sf.extra_cards = goblin_cards()
    b = await linked(stack)
    try:
        r = await b.http.get("/scan/api/printing?set=TM19&number=9")
        assert r.status_code == 200, r.text
        card = r.json()["card"]
        assert card["oracle_id"] == GOBLIN_TOKEN_ORACLE and card["name"] == "Goblin"
        assert card["layout"] == "token"
        listed = (await b.http.get(f"/scan/api/prints?oracle_id={GOBLIN_TOKEN_ORACLE}")).json()
        assert sorted(c["set"] for c in listed["cards"]) == ["tdmu", "tm19"]
        assert all(c["name"] == "Goblin" for c in listed["cards"])
        assert stack.sf.print_searches[-1]["include_extras"] == "true"
        # a printing Scryfall doesn't have, and values that name none
        missing = await b.http.get("/scan/api/printing?set=tm19&number=999")
        assert missing.status_code == 404 and missing.json()["error"] == "not_found"
        for bad in ("set=tm19", "number=9", "set=t%20m&number=9", "set=tm19&number=" + "9" * 30):
            r = await b.http.get("/scan/api/printing?" + bad)
            assert r.status_code == 400 and r.json()["error"] == "invalid", bad
    finally:
        await b.aclose()


async def test_printings_wait_a_moment_for_the_accounts_other_lookups(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many cards said Scryfall couldn't be reached: past four lookups at once an account's next
    one was refused at once. It now waits a moment for a free place, and only then says busy."""
    from mtg_gateway.scan import service as scan_service

    stack.sf.extra_cards = sol_ring_printings()
    scan = stack.h.app.state.gateway.scan
    scan._lookups["user-1"] = scan_service.MAX_LOOKUPS_PER_USER  # four of the account's own running

    async def done_soon() -> None:
        await asyncio.sleep(0.3)
        scan._lookups["user-1"] = scan_service.MAX_LOOKUPS_PER_USER - 1

    b = await linked(stack)
    try:
        freed = asyncio.create_task(done_soon())
        r = await b.http.get(f"/scan/api/prints?oracle_id={SOL_RING_ORACLE}")
        await freed
        assert r.status_code == 200 and r.json()["cards"], r.text
        monkeypatch.setattr(scan_service, "LOOKUP_WAIT_SECONDS", 0.2)
        scan._lookups["user-1"] = scan_service.MAX_LOOKUPS_PER_USER
        r = await b.http.get(f"/scan/api/prints?oracle_id={SOL_RING_ORACLE}&finish=foil")
        assert r.status_code == 429 and r.json()["error"] == "busy"
    finally:
        scan._lookups.pop("user-1", None)
        await b.aclose()


async def test_printings_refuse_values_they_do_not_know(stack: Stack) -> None:
    stack.sf.extra_cards = sol_ring_printings()
    b = await linked(stack)
    try:
        base = f"/scan/api/prints?oracle_id={SOL_RING_ORACLE}"
        for bad in (
            "&finish=shiny",
            "&frame=1980",
            "&border=purple",
            "&order=random",
            "&page=0",
            "&page=21",
            "&page=x",
            "&page=%C2%B2",  # "²": isdigit() says yes, int() says no
            "&page=%D9%A3",  # an Arabic-Indic 3
            "&set=cmr",  # set and number go together
            "&number=472",
            "&set=c%20mr&number=472",
            "&set=cmr&number=472&page=2",
        ):
            r = await b.http.get(base + bad)
            assert r.status_code == 400 and r.json()["error"] == "invalid", (bad, r.text)
        assert _prints_calls(stack) == []  # nothing reached Scryfall
        # upper case is read as the same value
        assert (await b.http.get(base + "&finish=FOIL&order=Newest")).status_code == 200
    finally:
        await b.aclose()


def _sol_ring(st: Stack) -> list[dict]:
    return [
        c
        for c in st.ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == "Sol Ring" and c["categories"] != ["Maybeboard"]
    ]


def _adds(st: Stack, since: int) -> list[dict]:
    return [e for p in st.ark.patches[since:] for e in p["cards"] if e["action"] == "add"]


async def test_change_printing_saves_at_once_and_undo_puts_it_back(stack: Stack) -> None:
    """The deck page's menu sends one set_printing change to the edit endpoint (as its Quantity and
    Move do); the answer's row carries the new printing for the page to draw. The toast's Undo
    sends the inverse change: the old set and number, and the old finish when the pick changed it."""
    b = await linked(stack)
    try:
        before = _sol_ring(stack)
        assert [
            (r["card"]["edition"]["editioncode"], r["card"]["collectorNumber"], r["modifier"]) for r in before
        ] == [("cmr", "472", "Normal")]
        since = len(stack.ark.patches)
        qty = sum(x["quantity"] for x in before)
        # SLD 1075 comes in non-foil: the finish stays, the change names none
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {
                "changes": [
                    {
                        "action": "set_printing",
                        "card_name": "Sol Ring",
                        "set_code": "sld",
                        "collector_number": "1075",
                    }
                ]
            },
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True
        row = next(x for x in d["rows"] if x["zone"] == "main")
        assert (row["set_code"], row["collector_number"], row["finish"]) == ("sld", "1075", "Normal")
        assert [(e["cardid"], e["modifications"]["modifier"]) for e in _adds(stack, since)] == [
            (124027, "Normal")
        ]
        # Undo: the old printing back
        since = len(stack.ark.patches)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {
                "changes": [
                    {
                        "action": "set_printing",
                        "card_name": "Sol Ring",
                        "set_code": "cmr",
                        "collector_number": "472",
                    }
                ]
            },
        )
        assert r.status_code == 201 and r.json()["applied"] is True, r.text
        assert [(e["cardid"], e["modifications"]["modifier"]) for e in _adds(stack, since)] == [
            (91043, "Normal")
        ]
        now = _sol_ring(stack)
        assert [
            (x["card"]["edition"]["editioncode"], x["card"]["collectorNumber"], x["modifier"]) for x in now
        ] == [("cmr", "472", "Normal")]
        assert sum(x["quantity"] for x in now) == qty
    finally:
        await b.aclose()


async def test_a_printing_not_made_in_the_finish_moves_the_finish_and_undo_restores_it(stack: Stack) -> None:
    """SLD 1074 is etched only. The picker names the finish the printing has (etched), the
    card becomes etched; the Undo names the old finish (normal) with the old printing."""
    b = await linked(stack)
    try:
        # without the finish Archidekt's printing cannot take the row: refused, nothing sent
        since = len(stack.ark.patches)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {
                "changes": [
                    {
                        "action": "set_printing",
                        "card_name": "Sol Ring",
                        "set_code": "sld",
                        "collector_number": "1074",
                    }
                ]
            },
        )
        assert r.status_code >= 400 and "does not come in Normal" in r.json()["message"], r.text
        assert stack.ark.patches[since:] == []
        # as the picker sends it: the finish the printing has
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {
                "changes": [
                    {
                        "action": "set_printing",
                        "card_name": "Sol Ring",
                        "set_code": "sld",
                        "collector_number": "1074",
                        "finish": "etched",
                    }
                ]
            },
        )
        assert r.status_code == 201 and r.json()["applied"] is True, r.text
        row = next(x for x in r.json()["rows"] if x["zone"] == "main")
        assert (row["set_code"], row["collector_number"], row["finish"]) == ("sld", "1074", "Etched")
        assert [(e["cardid"], e["modifications"]["modifier"]) for e in _adds(stack, since)] == [
            (124026, "Etched")
        ]
        # the Undo the page sends: old set, number and finish
        since = len(stack.ark.patches)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {
                "changes": [
                    {
                        "action": "set_printing",
                        "card_name": "Sol Ring",
                        "set_code": "cmr",
                        "collector_number": "472",
                        "finish": "normal",
                    }
                ]
            },
        )
        assert r.status_code == 201 and r.json()["applied"] is True, r.text
        assert [(e["cardid"], e["modifications"]["modifier"]) for e in _adds(stack, since)] == [
            (91043, "Normal")
        ]
        assert [(x["card"]["collectorNumber"], x["modifier"]) for x in _sol_ring(stack)] == [
            ("472", "Normal")
        ]
    finally:
        await b.aclose()


def test_next_page_is_the_same_search_one_page_on() -> None:
    """The fake's next_page (as Scryfall's) is this search with page one higher: what the gateway's
    page + 1 asks for."""
    import httpx

    from .fake_scryfall import FakeScryfall

    sf = FakeScryfall()
    sf.extra_cards = sol_ring_printings(fillers=4)
    sf.page_size = 5
    req = httpx.Request(
        "GET",
        "https://scryfall.test/cards/search",
        params={"q": f"oracleid:{SOL_RING_ORACLE}", "order": "released", "dir": "desc", "unique": "prints"},
    )
    body = sf.printings({k: v[0] for k, v in parse_qs(req.url.query.decode()).items()}, req).json()
    assert body["has_more"] is True
    nxt = parse_qs(urlparse(body["next_page"]).query)
    assert (
        nxt["page"] == ["2"] and nxt["q"] == [f"oracleid:{SOL_RING_ORACLE}"] and nxt["order"] == ["released"]
    )
