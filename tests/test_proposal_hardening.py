"""Proposals show what is written and record how far an apply got; parsers stay linear;
the Archidekt pacer honours Retry-After."""

from __future__ import annotations

import asyncio
import re
import time

import httpx
import pytest

from mtg_gateway.archidekt import ArchidektClient, ArchidektError, Pacer
from mtg_gateway.archidekt_csv import CsvError, parse_export
from mtg_gateway.decklist import parse_decklist

from .conftest import FakeIdP, Harness, make_settings, running
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, Stack, _client, call, linked_user, mcp_token, structured


@pytest.fixture
async def stack(tmp_path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, apply_via_mcp=True, archidekt_base="https://ark.test/api"
    )
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h:
        yield Stack(h, ark)


async def _propose(h, token: str, changes: list[dict]) -> dict:
    return structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))


def _review_rows(text: str) -> list[str]:
    return re.findall(r"<span class='name'>(.*?)</span>", text)


# -- 12/33: the category an add writes is shown and bounded ------------------------------------
async def test_category_of_an_add_is_in_the_diff_and_capped(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    p = await _propose(h, token, [{"action": "add", "card_name": "Arcane Signet", "category": "Commander"}])
    assert p["ok"] and p["diff"] == "+1 Arcane Signet [Commander]", p
    b = Browser(h)
    await b.login()
    r = await b.http.get(f"/proposals/{p['proposal_id']}")
    assert "Arcane Signet [Commander]" in _review_rows(r.text)
    await b.aclose()
    big = await _propose(h, token, [{"action": "add", "card_name": "Sol Ring", "category": "X" * 300_000}])
    assert big["ok"] is False and big["error"] == "invalid" and "category" in big["message"]
    # control characters cannot hide part of a category on its own line
    p2 = await _propose(h, token, [{"action": "add", "card_name": "Island", "category": "Ramp\n-1 Forest"}])
    assert p2["ok"] and "[" not in p2["diff"], p2  # existing row: the category is not sent
    assert p2["changes"][0]["category"] == "Ramp -1 Forest"


# -- 13/32: a new deck's name cannot forge review rows -------------------------------------------
async def test_new_deck_name_with_newlines_stays_one_row(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    name = "Gifts' (2 cards, private)\n+1 Island ‮"
    p = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": name, "private": False, "cards": [{"card_name": "Sol\r\nRing", "quantity": 1}]},
        )
    )
    assert p["ok"], p
    assert p["diff"].splitlines() == [
        "New commander deck 'Gifts' (2 cards, private) +1 Island' (1 cards, public)",
        "+1 Sol Ring",
    ]
    b = Browser(h)
    await b.login()
    page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
    rows = _review_rows(page)
    await b.aclose()
    # the deck name is one field of the New deck row; the visibility comes from its own field
    assert len(rows) == 2 and rows[0] == "Gifts&#x27; (2 cards, private) +1 Island"
    assert "commander, 1 cards, PUBLIC" in page


async def test_csv_cells_with_newlines_stay_one_row(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    csv_text = 'Quantity,Name,Category\n1,"Sol Ring\n+4 Island","Ramp\n-1 Forest"\n'
    p = structured(await call(h, token, "propose_new_deck", {"name": "x", "csv_text": csv_text}))
    assert p["ok"], p
    assert p["diff"].splitlines()[1:] == ["+1 Sol Ring +4 Island [Ramp -1 Forest]"]


# -- 14: a pending proposal is applied over MCP only by the client that made it -----------------
async def test_another_client_cannot_apply_a_pending_proposal(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token_a = await linked_user(stack)
    p = await _propose(h, token_a, [{"action": "add", "card_name": "Arcane Signet"}])
    token_b = await mcp_token(h)  # a second connected app of the same user
    out = structured(await call(h, token_b, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "other_client", out
    assert out["review_url"].endswith(p["proposal_id"]) and ark.patches == []
    assert h.db.get_proposal(p["proposal_id"], "user-1")["state"] == "pending"
    # the client that proposed it still can, and so can the browser review page
    a = structured(await call(h, token_a, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["state"] == "applied", a
    p2 = await _propose(h, token_a, [{"action": "add", "card_name": "Sol Ring"}])
    b = Browser(h)
    await b.login()
    url = f"/proposals/{p2['proposal_id']}"
    r = await b.http.post(url, data={"csrf": await b.csrf(url), "action": "apply"})
    await b.aclose()
    assert r.status_code == 303 and h.db.get_proposal(p2["proposal_id"], "user-1")["state"] == "applied"


# -- 31: a pinned add next to a remove/set of the same card is refused ---------------------------
async def test_pinned_add_with_set_quantity_of_the_same_card_is_refused(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    for other in (
        {"action": "set_quantity", "card_name": "sol ring", "quantity": 0},
        {"action": "remove", "card_name": "Sol Ring"},
    ):
        changes = [
            {
                "action": "add",
                "card_name": "Sol Ring",
                "set_code": "SLD",
                "collector_number": "1074",
                "quantity": 3,
            },
            other,
        ]
        out = await _propose(h, token, changes)
        assert out["ok"] is False and out["error"] == "invalid" and "pinned" in out["message"], out
    assert ark.patches == []


def test_build_payload_never_shrinks_rows_below_the_plan() -> None:
    from mtg_gateway.archidekt import parse_deck
    from mtg_gateway.decks import DeckError, build_payload

    deck = parse_deck(FakeArchidekt().decks[42])
    pin = {"Sol Ring": {"cardid": 124026, "modifier": "Etched", "quantity": 3, "categories": []}}
    with pytest.raises(DeckError):
        build_payload(deck, {"Sol Ring": 0}, {}, pinned=pin)


# -- 54/55/56: an apply records how far it got and re-checks the deck before writing ----------------
THREE = [
    {"action": "set_quantity", "card_name": "Forest", "quantity": 3},
    {"action": "remove", "card_name": "Acidic Slime"},
    {"action": "set_quantity", "card_name": "Island", "quantity": 2},
]


async def test_apply_failing_partway_says_how_far_it_got(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = await _propose(h, token, THREE)
    orig = ark.transport.handler

    def flaky(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and len(ark.patches) >= 1:
            return httpx.Response(503)
        return orig(req)

    ark.transport.handler = flaky
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "unavailable"
    assert out["sent_entries"] == 1 and out["of_entries"] == 3 and "partly changed" in out["message"]
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["state"] == "failed" and got["result"]["sent_entries"] == 1
    assert got["result"]["snapshot_id"] == got["snapshot_id"] and got["result"]["backup_deck_id"]
    assert "Part of this change reached Archidekt" in got["next_step"]


async def test_cancelled_apply_is_recorded_not_left_applying(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = await _propose(h, token, THREE)
    orig = ark.transport.handler
    gate = asyncio.Event()

    async def slow(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and len(ark.patches) >= 1:
            gate.set()
            await asyncio.sleep(3600)
        return orig(req)

    ark.transport.handler = slow
    task = asyncio.create_task(h.app.state.gateway.decks.apply("user-1", p["proposal_id"], via="browser"))
    await asyncio.wait_for(gate.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = h.db.get_proposal(p["proposal_id"], "user-1")
    assert row["state"] == "failed" and row["result"]["error"] == "interrupted"
    assert row["result"]["sent_entries"] == 1
    with h.db.tx() as c:
        events = [r[0] for r in c.execute("SELECT event FROM audit_log ORDER BY id")]
    assert events[-1] == "proposal_failed"


async def test_verify_mismatch_keeps_the_detailed_result(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.fail_patch_silently = True
    p = await _propose(h, token, [{"action": "set_quantity", "card_name": "Island", "quantity": 16}])
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["error"] == "verify_mismatch"
    result = h.db.get_proposal(p["proposal_id"], "user-1")["result"]
    assert result["error"] == "verify_mismatch" and result["mismatched_cards"] == ["Island"]
    assert result["sent_entries"] == 1 and result["backup_deck_id"] and result["snapshot_id"]


async def test_deck_edited_during_lookups_is_not_overwritten(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = await _propose(
        h,
        token,
        [
            {"action": "add", "card_name": "Arcane Signet"},
            {"action": "set_quantity", "card_name": "Forest", "quantity": 14},
        ],
    )
    orig = ark.transport.handler
    done = {"x": False}

    def concurrent(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/api/cards/v2/") and not done["x"]:
            done["x"] = True  # the user edits the deck on Archidekt meanwhile
            for c in ark.decks[42]["cards"]:
                if c["card"]["oracleCard"]["name"] == "Forest":
                    c["categories"] = ["Lands", "Edited Elsewhere"]
            ark.decks[42]["updatedAt"] = "2026-10-04T00:00:00Z"
        return orig(req)

    ark.transport.handler = concurrent
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "stale", out
    assert ark.patches == []


async def test_backup_bumping_the_decks_updated_time_does_not_block_the_apply(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = await _propose(h, token, [{"action": "set_quantity", "card_name": "Forest", "quantity": 14}])
    orig = ark.transport.handler

    def copy_bumps(req: httpx.Request) -> httpx.Response:
        resp = orig(req)
        if req.url.path == "/api/decks/copy/":
            ark.decks[42]["updatedAt"] = "2026-10-06T00:00:00Z"  # if Archidekt touches the source
        return resp

    ark.transport.handler = copy_bumps
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is True, out


# -- 15/16: parsers stay linear and bounded ---------------------------------------------------
def test_decklist_category_merge_is_linear() -> None:
    lines = []
    n = 0
    for _ in range(670):
        cats = ",".join(f"c{n + i}" for i in range(40))
        n += 40
        lines.append(f"1 Sol Ring [{cats}]")
    start = time.monotonic()
    cards = parse_decklist("\n".join(lines))
    assert time.monotonic() - start < 1.0
    assert len(cards) == 1 and cards[0].quantity == 670 and len(cards[0].categories) == n
    # order kept, duplicates dropped
    assert parse_decklist("1 Opt [A,B]\n1 Opt [B,C]")[0].categories == ["A", "B", "C"]


def test_csv_parse_stops_at_the_row_cap() -> None:
    import tracemalloc

    tracemalloc.start()
    with pytest.raises(CsvError, match="5000 rows"):
        parse_export("a\n" * 1_000_000)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 20_000_000  # was over 100 MB when every row was built first
    with pytest.raises(CsvError, match="lines"):
        parse_export("\n" * 1_000_000)


# -- 53: Retry-After is honoured ----------------------------------------------------------------
async def test_pacer_honours_retry_after() -> None:
    hits: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(time.monotonic())
        return httpx.Response(429, headers={"Retry-After": "60"})

    pacer = Pacer(0.0)
    client = ArchidektClient(
        "https://ark.test/api", "t", pacer, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ArchidektError) as first:
        await client.get_deck(None, "42")
    assert first.value.kind == "rate_limited"
    for _ in range(3):
        with pytest.raises(ArchidektError) as again:
            await client.get_deck(None, "42")
        assert again.value.kind == "rate_limited"
    assert len(hits) == 1  # nothing else went out while Archidekt asked us to wait
    pacer.record(False, 10_000)  # an absurd Retry-After is bounded
    assert pacer._retry_until - time.monotonic() <= Pacer.MAX_RETRY_AFTER
    await client.aclose()
