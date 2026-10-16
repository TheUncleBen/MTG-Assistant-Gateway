"""The test suite's offline guard (conftest.py): every way out to a host other than this machine is
refused and counted against the test, a reserved .test name fails as it would offline, this machine
stays reachable (the browser tests' servers), and a gateway's start-up download of Scryfall's
card-name catalog goes to the recorded fake, never to api.scryfall.com."""

from __future__ import annotations

import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from mtg_gateway.scan.scryfall import ScryfallClient

from . import conftest
from .conftest import open_stack
from .fake_scryfall import FakeScryfall

pytestmark = pytest.mark.skipif(conftest.ONLINE, reason="the guard stands aside when going online")

CATALOG = "https://api.scryfall.com/catalog/card-names"


def _take_refusals(n: int) -> list[str]:
    """The last ``n`` refusals, taken off the list so that the test that caused them on purpose
    still passes the guard's own check."""
    assert len(conftest.OUTBOUND) - conftest._REPORTED[0] == n, conftest.OUTBOUND[conftest._REPORTED[0] :]
    taken = conftest.OUTBOUND[-n:]
    del conftest.OUTBOUND[-n:]
    return taken


async def test_httpx_requests_to_another_host_are_refused_and_counted() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectError, match="tests stay offline"):
            await client.get(CATALOG)
    with httpx.Client() as client, pytest.raises(httpx.ConnectError, match="tests stay offline"):
        client.get(CATALOG)
    assert _take_refusals(2) == [CATALOG, CATALOG]


def test_sockets_and_name_lookups_are_refused_and_counted() -> None:
    with pytest.raises(socket.gaierror, match="tests stay offline"):
        socket.getaddrinfo("api.scryfall.com", 443)
    with pytest.raises(OSError, match="tests stay offline"):
        socket.create_connection(("api.scryfall.com", 443), timeout=1)
    with socket.socket() as s, pytest.raises(OSError, match="tests stay offline"):
        s.connect(("198.51.100.7", 443))  # an address, so no name lookup first
    assert _take_refusals(3) == [
        "name lookup: api.scryfall.com",
        "name lookup: api.scryfall.com",
        "('198.51.100.7', 443)",
    ]


async def test_a_reserved_test_name_fails_as_offline_without_counting() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://idp.test/")
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("idp.test", 443)
    assert conftest.OUTBOUND[conftest._REPORTED[0] :] == []


def test_this_machine_stays_reachable() -> None:
    class Hello(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the http.server name
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello")

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Hello)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        for host in ("127.0.0.1", "localhost"):
            assert httpx.get(f"http://{host}:{port}/", timeout=5).text == "hello"
    finally:
        server.shutdown()
        server.server_close()


async def test_a_gateways_card_name_catalog_comes_from_the_recorded_fake(tmp_path, idp) -> None:
    # the gateway builds its own client; the guard hands it one on the recorded fixtures
    async with open_stack(tmp_path, idp) as st:
        names = st.gw.scan.names
        assert st.gw.scan.scryfall.base == "https://scryfall.test"
        if names._task is not None:
            await asyncio.wait({names._task}, timeout=5)
        assert not names.loaded  # the recorded fake has no catalog unless a test gives it one
    # a test that wants suggestions from a catalog gives the fake the offline name list
    sf = FakeScryfall()
    sf.catalog = sf.names_catalog()
    client = ScryfallClient("https://scryfall.test", min_interval=0.0, http=sf.client())
    got = await client.catalog_card_names()
    assert "Sol Ring" in got and len(got) >= 1000
    assert [u for _m, u in sf.requests] == ["https://scryfall.test/catalog/card-names"]
