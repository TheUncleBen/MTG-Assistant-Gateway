"""The launcher shortcuts in the web manifest: My decks, Scan page,
Proposals, Reports and, only while watchlists are on, Watchlist, in that order; the Android app's
GatewayUrl.SHORTCUTS lists the same pages (GatewayUrlTest)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mtg_gateway import theme

from .conftest import Stack

KT = Path(__file__).parent.parent / "android/app/src/main/kotlin/local/mtgassistantgateway/app/GatewayUrl.kt"


@pytest.mark.parametrize("on", [False, True])
async def test_manifest_shortcuts_follow_the_watchlists(stack: Stack, monkeypatch, on: bool) -> None:
    monkeypatch.setattr(theme, "WATCHLISTS", on)
    m = (await stack.h.http.get("/app.webmanifest")).json()
    got = [(s["name"], s["url"]) for s in m["shortcuts"]]
    want = [
        ("My decks", "/decks"),
        ("Scan page", "/scan"),
        ("Proposals", "/proposals"),
        ("Reports", "/reports"),
    ]
    assert got == want + ([("Watchlist", "/watchlist")] if on else [])


def test_the_android_app_lists_the_same_pages() -> None:
    src = KT.read_text(encoding="utf-8")
    block = src.split("val SHORTCUTS")[1].split(")\n")[0]
    assert re.findall(r'"(/\w+)"', block) == ["/decks", "/scan", "/proposals", "/reports", "/watchlist"]
