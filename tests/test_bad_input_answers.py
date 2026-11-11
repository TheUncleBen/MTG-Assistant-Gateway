"""Input that names nothing or has the wrong shape gets a plain answer, never a 500.

Found by fuzzing every route (0.7.48 review): a picture whose header declares more pixels than
Pillow will open, a deck check for an address that names no deck, and a list where the edit
route expects the name of a view, grouping or sort."""

from __future__ import annotations

import struct
import zlib

import pytest

from mtg_gateway import account_lists, profile
from mtg_gateway.decks import DeckError

from .conftest import COLLECTION, Stack, stack_with
from .test_browse_collection import api, linked

stack_options = stack_with(COLLECTION)


def _png_header(w: int, h: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\0" * 10))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("side", [12000, 20000])
def test_a_picture_declaring_too_many_pixels_is_refused_in_plain_words(side: int) -> None:
    raw = _png_header(side, side)
    for check in (profile._clean_avatar, lambda r: account_lists._redraw(r, None)):
        with pytest.raises(DeckError) as e:
            check(raw)
        assert "too many pixels" in str(e.value)


async def test_a_deck_check_for_an_address_that_names_no_deck_answers_404(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await b.http.get("/decks/not-a-number/check")
        assert r.status_code == 404, r.text
        assert r.json() == {"changed": False, "failed": False}
    finally:
        await b.http.aclose()


async def test_the_edit_route_ignores_a_view_that_is_not_text(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {
                "changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}],
                "view": [None],
                "group": {"a": 1},
                "sort": [],
            },
        )
        assert r.status_code == 201, r.text
    finally:
        await b.http.aclose()
