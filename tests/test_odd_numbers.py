"""A number the gateway cannot use gets a plain answer, never the framework's bare tool error.

"²".isdigit() is True and int("²") raises; an offset past what SQLite stores raises too. Both used
to end as "Error executing tool ..." with no reason and no next step.
"""

from __future__ import annotations

import pytest

from mtg_gateway.digits import is_digits
from mtg_gateway.theme import natural_key

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, structured


def test_only_plain_digits_count_as_a_number():
    assert is_digits("0") and is_digits("12345678")
    for odd in ("", "²", "①", "٣", "1 ", "-1", "1.0", "9" * 5000, None, 7):
        assert not is_digits(odd), odd
    assert is_digits("123456", most=6) and not is_digits("1234567", most=6)


def test_a_name_with_a_superscript_still_sorts():
    assert sorted(["Deck ²", "Deck 10", "Deck 2"], key=natural_key)[:2] == ["Deck 2", "Deck 10"]


@pytest.mark.parametrize(
    "tool, args",
    [
        ("get_forum", {"thread_id": "²"}),
        ("search_decks", {"format": "²"}),
        ("list_snapshots", {"offset": 2**63}),
        ("list_my_proposals", {"offset": 2**63}),
        ("propose_collection_changes", {"text": "1 Sol Ring\n" * 2001}),
    ],
)
async def test_an_odd_number_is_answered_in_plain_words(stack: Stack, tool, args):
    token = await linked_user(stack)
    result = await call(stack.h, token, tool, args)
    text = result["content"][0]["text"]
    assert not text.startswith("Error executing tool"), text
    data = structured(result)
    if tool.startswith("list_"):  # a skip past the end is an empty page
        assert data["ok"] is True and data["has_more"] is False
    else:
        assert data["ok"] is False and data["error"] in ("invalid", "not_found"), data
        assert data["message"].endswith("."), data


async def test_a_tool_that_raises_answers_with_the_house_sentence(stack: Stack, monkeypatch):
    token = await linked_user(stack)

    def boom(*_a, **_k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(stack.gw.decks, "list_snapshots", boom)
    result = await call(stack.h, token, "list_snapshots", {})
    data = structured(result)
    assert result["isError"] is True
    assert data["ok"] is False and data["error"] == "internal" and data["error_code"] == "INTERNAL"
    assert "secret detail" not in str(result) and "Error executing tool" not in str(result)
    assert "Try it once more" in data["message"]


def test_no_module_asks_isdigit_by_itself():
    """The class, kept fixed: a number read out of text goes through digits.is_digits."""
    import pathlib

    src = pathlib.Path(__file__).parent.parent / "src" / "mtg_gateway"
    found = [
        f"{f.relative_to(src)}:{n}"
        for f in sorted(src.rglob("*.py"))
        if f.name != "digits.py"
        for n, line in enumerate(f.read_text().splitlines(), 1)
        if ".isdigit()" in line and "isascii()" not in line
    ]
    assert not found, found
