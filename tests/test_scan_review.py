"""Scan and resolve_cards fixes from the assistant tool review (2026-10-28): a finish asked for on
a card matched by name, and what a saved session's list holds."""

from __future__ import annotations

from mtg_gateway.scan.service import ScanService, as_changes


def _item(name: str, *, status: str = "exact", foil: bool | None = None, qty: int = 1, **card) -> dict:
    return {
        "card": {"name": name, **card} if status != "not_found" else None,
        "status": status,
        "quantity": qty,
        "foil": foil,
        "input": {"name": name},
    }


def test_a_foil_asked_for_on_a_name_match_stays_foil() -> None:
    delver = "Delver of Secrets // Insectile Aberration"
    out = as_changes([_item(delver, foil=True)])
    assert out == [{"action": "add", "card_name": delver, "foil": True, "quantity": 1}]
    # copies of one name in two finishes are two adds, each with its own
    out = as_changes([_item("Sol Ring", foil=True), _item("Sol Ring", qty=2)])
    assert out == [
        {"action": "add", "card_name": "Sol Ring", "foil": True, "quantity": 1},
        {"action": "add", "card_name": "Sol Ring", "quantity": 2},
    ]
    # a pinned printing keeps its own finish, as before
    pinned = _item("Sol Ring", status="printing", foil=False, set="cmr", collector_number="472")
    assert as_changes([pinned]) == [
        {
            "action": "add",
            "card_name": "Sol Ring",
            "set_code": "cmr",
            "collector_number": "472",
            "foil": False,
            "quantity": 1,
        }
    ]


def test_a_saved_session_lists_only_cards_it_found() -> None:
    row = {
        "id": "scan_x",
        "name": "TEST",
        "status": "open",
        "source": "text",
        "created_at": 1,
        "updated_at": 1,
        "items": [
            _item("Sol Ring", set="cmr", collector_number="472"),
            _item("Xyzzy Plugh", status="not_found"),
        ],
    }
    out = ScanService.describe_session(row)
    assert "Xyzzy Plugh" not in out["decklist_text"], out["decklist_text"]
    assert "Sol Ring" in out["decklist_text"]
    assert out["unresolved"] == 1
