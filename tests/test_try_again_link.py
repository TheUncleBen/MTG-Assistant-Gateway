"""The "Try again" button on the 500 and membership-503 pages goes back to this request's own
path on this gateway, never to another site (L-1, review 2026-10-22): a path that starts with
``//`` or ``/\\`` would otherwise be a protocol-relative link to another host."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from mtg_gateway.app import _same_url


def _req(path: str, query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": [(b"host", b"gw.test")],
            "scheme": "https",
            "server": ("gw.test", 443),
        }
    )


@pytest.mark.parametrize("path", ["//evil.example/x", "///evil.example/x", "/\\evil.example/x", "/\\/evil"])
def test_try_again_never_leaves_the_gateway(path: str) -> None:
    href = _same_url(_req(path))
    assert href.startswith("/") and not href.startswith(("//", "/\\")), href


def test_try_again_keeps_the_path_and_query() -> None:
    assert _same_url(_req("/decks/42", "tab=cards")) == "/decks/42?tab=cards"
    assert _same_url(_req("/")) == "/"
