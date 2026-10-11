"""The tests stay offline (conftest.py): the gateway's own requests to other hosts are refused and
fail the test, a gateway the tests build answers its card-name catalog download from the recorded
fixtures, and the browser tests' Chromium reaches nothing but this machine and what a test serves
with page.route."""

from __future__ import annotations

import httpx
import pytest

from mtg_gateway.app import create_app

from .conftest import OUTBOUND, make_settings
from .test_consent_browser import _chromium_or_skip, _launch


def test_a_request_to_another_host_is_refused() -> None:
    with pytest.raises(httpx.ConnectError, match="tests stay offline"):
        httpx.get("https://api.scryfall.com/catalog/card-names")
    assert OUTBOUND.pop() == "https://api.scryfall.com/catalog/card-names"  # expected here


async def test_the_catalog_download_at_start_up_uses_the_recorded_scryfall(tmp_path) -> None:
    scan = create_app(make_settings(tmp_path)).state.gateway.scan
    assert scan.scryfall.base == "https://scryfall.test"
    assert not await scan.names.load()  # the fake has no catalog: 404, as when Scryfall is down
    await scan.scryfall.aclose()


def test_chromium_reaches_only_this_machine_and_routed_pages() -> None:
    from playwright.sync_api import Error, sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        page = browser.new_page()
        with pytest.raises(Error, match="ERR_NAME_NOT_RESOLVED"):
            page.goto("https://api.scryfall.com/cards/named?exact=Sol+Ring", timeout=10_000)
        page.route("https://cards.scryfall.io/**", lambda r: r.fulfill(body=b"ok"))
        assert page.goto("https://cards.scryfall.io/x").ok
        browser.close()
