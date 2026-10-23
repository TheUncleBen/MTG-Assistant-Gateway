"""The deck registration PDF (parity release 2): the flat form Archidekt's site sends to
/decks/exportPdf/ (REQUESTS.md section 12), filled from the deck as the member sees it."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import pytest

from mtg_gateway import registration
from mtg_gateway.archidekt import Deck, DeckCard

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, structured


def _card(name: str, qty: int, cats: list[str], **kw: object) -> DeckCard:
    return DeckCard(None, None, name, qty, cats, "Normal", "", "", **kw)  # type: ignore[arg-type]


def _deck() -> Deck:
    cats = [
        {"name": "Ramp", "includedInDeck": True},
        {"name": "Sideboard", "includedInDeck": False},
        {"name": "Maybeboard", "includedInDeck": False},
    ]
    cards = [
        _card("Sol Ring", 1, ["Ramp"], cmc=1.0, types=["Artifact"]),
        _card("Lightning Bolt", 2, [], cmc=1.0, colors=["R"], types=["Instant"]),
        _card("Lightning Bolt", 1, ["Ramp"], cmc=1.0, colors=["R"], types=["Instant"]),  # another printing
        _card("Counterspell", 4, ["Ramp"], cmc=2.0, colors=["U"], types=["Instant"]),
        _card("Duress", 3, ["Sideboard"], cmc=1.0, colors=["B"], types=["Sorcery"]),
        _card("Mox Opal", 1, ["Maybeboard", "Ramp"], cmc=0.0, types=["Artifact"]),
    ]
    return Deck("9", "Burn", "alice", "", cards, cats, {})


def test_the_form_lists_the_main_deck_and_sideboard_as_the_site_sends_them() -> None:
    form = registration.fields(
        _deck(),
        {"first_name": " Ben ", "last_name": "Smith", "dci_number": "12 34", "date": "10/24/2026"},
        "alpha",
    )
    assert [form[f"card_{i}"] for i in range(3)] == ["Counterspell", "Lightning Bolt", "Sol Ring"]
    assert [form[f"card_{i}_qty"] for i in range(3)] == [4, 3, 1]  # two printings, one line
    assert "card_3" not in form  # the Maybeboard card is left off
    assert (form["sideboard_0"], form["sideboard_0_qty"], form["sideboardSize"]) == ("Duress", 3, 3)
    assert form["deckSize"] == 8 and form["deckName"] == "Burn"
    assert (form["firstName"], form["lastName"], form["firstOfLastName"]) == ("Ben", "Smith", "S")
    assert [form[f"dci_{i}"] for i in range(4)] == ["1", "2", "3", "4"] and "dci_4" not in form
    assert form["date"] == "10/24/2026"


@pytest.mark.parametrize(
    ("sort", "names"),
    [
        ("manaValue", ["Lightning Bolt", "Sol Ring", "Counterspell"]),
        ("color", ["Counterspell", "Lightning Bolt", "Sol Ring"]),
        ("type", ["Counterspell", "Lightning Bolt", "Sol Ring"]),  # instants before artifacts
    ],
)
def test_each_sort_orders_the_lines(sort: str, names: list[str]) -> None:
    form = registration.fields(_deck(), {}, sort)
    assert [form[f"card_{i}"] for i in range(3)] == names


def test_type_order_puts_creatures_first_and_lands_last() -> None:
    cards = [
        _card("Forest", 1, [], types=["Land"]),
        _card("Ornithopter", 1, [], types=["Artifact", "Creature"]),
        _card("Opt", 1, [], types=["Instant"]),
    ]
    assert [n for n, _ in registration.lines(cards, "type")] == ["Ornithopter", "Opt", "Forest"]


@pytest.mark.parametrize(
    ("details", "sort", "says"),
    [
        ({}, "price", "sort must be one of"),
        ({"dci_number": "12a"}, "alpha", "digits only"),
        ({"dci_number": "12345678901"}, "alpha", "at most 10"),
        ({"first_name": "x" * 61}, "alpha", "at most 60"),
        ({"date": 5}, "alpha", "must be text"),
    ],
)
def test_bad_details_are_refused_before_anything_is_sent(details: dict, sort: str, says: str) -> None:
    with pytest.raises(Exception, match=says):
        registration.fields(_deck(), details, sort)


async def test_the_tool_answers_with_the_pdf_address_and_keeps_no_details(stack: Stack) -> None:
    token = await linked_user(stack)
    out = structured(
        await call(
            stack.h,
            token,
            "get_registration_pdf",
            {"deck_id": "42", "first_name": "Ben", "last_name": "Smith", "event": "FNM", "sort": "type"},
        )
    )
    assert out["ok"] is True, out
    assert out["pdf_url"] == "https://storage.example.com/pdf/1.pdf"
    assert out["sorted_by"] == "card type" and out["sideboard"] == 0
    sent = stack.ark.account.pdfs[0]
    assert (sent["by"], sent["event"], sent["deckSize"]) == ("alice", "FNM", out["main_deck"])
    assert sent["deckSize"] == sum(v for k, v in sent.items() if k.startswith("card_") and k.endswith("_qty"))
    rows = stack.h.app.state.gateway.db._conn.execute(
        "SELECT detail_json FROM audit_log WHERE event = 'registration_pdf'"
    ).fetchall()
    assert len(rows) == 1 and "Smith" not in rows[0][0] and "FNM" not in rows[0][0]
    bad = structured(await call(stack.h, token, "get_registration_pdf", {"deck_id": "42", "dci_number": "x"}))
    assert bad["ok"] is False and len(stack.ark.account.pdfs) == 1


def test_the_pdf_needs_the_write_scope() -> None:
    """Security review 0.7.44, S-10: it sends the member's typed details to Archidekt in their
    name, so an app that may only read cannot ask for it."""
    from mtg_gateway.app import WRITE_TOOLS

    assert "get_registration_pdf" in WRITE_TOOLS


async def test_the_pdf_tool_says_it_is_not_read_only(stack: Stack) -> None:
    """Confirmation pass 0.7.44 (N-4): a write tool marked readOnlyHint true may be run by an app
    without asking, and a read-only token is refused a tool it was told only reads."""
    from .test_decks_and_proxy import sse_json

    token = await linked_user(stack)
    tools = sse_json(await stack.h.mcp(token, "tools/list", {}, rid=7))["result"]["tools"]
    pdf = next(t for t in tools if t["name"] == "get_registration_pdf")
    assert pdf["annotations"]["readOnlyHint"] is False
