"""Serve tests/fake_scryfall.py as https://api.scryfall.com inside the test network.

The gateway's Scryfall client has no base-URL setting (on purpose: phones never call
Scryfall, the gateway does, and only the real host). So the e2e stack gives this
service the network alias ``api.scryfall.com`` and a certificate for that name
signed by the test CA, which the test-only ``-trusted`` gateway image trusts. The
gateway therefore talks to the real URL and nothing leaves the sandbox.

Shapes come from the fixtures recorded live in tests/fixtures/scan. One deliberate
twist: ``POST /cards/collection`` answers with the found cards in **reverse** order,
so the suite proves the gateway matches results to its own identifiers rather than
by position (the live check saw no shift on real data; this makes the test strict).

/__e2e/requests lists every request seen, for assertions on batching.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.fake_scryfall import FakeScryfall  # noqa: E402

fake = FakeScryfall()


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] != "http":
        return
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body"):
            break
    path = scope["path"]
    query = scope.get("query_string", b"").decode()
    if path == "/__e2e/requests":
        resp = httpx.Response(200, json=fake.requests)
    elif path == "/__e2e/health":
        resp = httpx.Response(200, json={"ok": True, "cards": len(fake.cards)})
    else:
        url = f"https://api.scryfall.com{path}" + (f"?{query}" if query else "")
        headers = [(k.decode(), v.decode()) for k, v in scope["headers"]]
        req = httpx.Request(scope["method"], url, headers=headers, content=body)
        resp = fake.handle(req)
        if path == "/cards/collection" and resp.status_code == 200:
            data = resp.json()
            data["data"] = list(reversed(data["data"]))
            resp = httpx.Response(200, json=data)
    await send(
        {
            "type": "http.response.start",
            "status": resp.status_code,
            "headers": [(b"content-type", resp.headers.get("content-type", "application/json").encode())],
        }
    )
    await send({"type": "http.response.body", "body": resp.content})


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "443")),
        ssl_certfile=os.environ["SSL_CERT"],
        ssl_keyfile=os.environ["SSL_KEY"],
        log_level="info",
    )
