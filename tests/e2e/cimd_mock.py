"""Publish a Client ID Metadata Document, as an MCP client such as Claude does.

Serves https://cimd.e2e.test/claude-e2e.json inside the test network with a test-CA
certificate. The gateway's fetcher only accepts hosts that resolve to a public address
and connects to that address with the hostname in SNI, so run.sh creates the test
overlay network in a public range (see README) and this service carries the alias.
"""

from __future__ import annotations

import json
import os
from typing import Any

import uvicorn

DOC = {
    "client_id": "https://cimd.e2e.test/claude-e2e.json",
    "client_name": "E2E CIMD Assistant",
    "client_uri": "https://cimd.e2e.test/",
    "redirect_uris": ["http://127.0.0.1:18766/cb", "https://client.e2e.test/callback"],
    "token_endpoint_auth_method": "none",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "scope": "mtg",
}


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] != "http":
        return
    if scope["path"] == "/claude-e2e.json":
        status, ctype, body = 200, b"application/json", json.dumps(DOC).encode()
    elif scope["path"] == "/not-json.json":
        status, ctype, body = 200, b"text/html", b"<p>not a document</p>"
    else:
        status, ctype, body = 404, b"application/json", b'{"error":"not found"}'
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", ctype), (b"cache-control", b"max-age=60")],
        }
    )
    await send({"type": "http.response.body", "body": body})


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "443")),
        ssl_certfile=os.environ["SSL_CERT"],
        ssl_keyfile=os.environ["SSL_KEY"],
        log_level="info",
    )
