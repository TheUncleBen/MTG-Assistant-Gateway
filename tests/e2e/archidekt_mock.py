"""Serve tests/fake_archidekt.py over HTTP so the deployed gateway can talk to it.

The unit tests drive FakeArchidekt through an in-process httpx transport. The e2e
stack runs the real gateway image, which only knows URLs, so this wraps the same
fake in a small ASGI app (the gateway image already ships uvicorn and httpx) and
the stack points MTG_ARCHIDEKT_BASE at it. Nothing here is recorded from
archidekt.com; the shapes come from the fake.

Extra endpoints under /__e2e/ exist only for the tests: health, reset, the PATCH bodies
the gateway sent, and a way to edit a deck behind the gateway's back (stale check).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.fake_archidekt import FakeArchidekt  # noqa: E402

fake = FakeArchidekt()


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
    if path.startswith("/__e2e/"):
        resp = _debug(scope["method"], path, body)
    else:
        url = f"http://archidekt.test{path}" + (f"?{query}" if query else "")
        headers = [(k.decode(), v.decode()) for k, v in scope["headers"]]
        req = httpx.Request(scope["method"], url, headers=headers, content=body)
        resp = fake.handle(req)
    await send(
        {
            "type": "http.response.start",
            "status": resp.status_code,
            "headers": [(b"content-type", resp.headers.get("content-type", "application/json").encode())],
        }
    )
    await send({"type": "http.response.body", "body": resp.content})


def _debug(method: str, path: str, body: bytes) -> httpx.Response:
    global fake
    if path == "/__e2e/reset" and method == "POST":
        fake = FakeArchidekt()  # fresh decks, no recorded patches: lets the suite run twice on one stack
        return httpx.Response(200, json={"ok": True})
    if path == "/__e2e/health":
        return httpx.Response(200, json={"ok": True, "decks": sorted(fake.decks)})
    if path == "/__e2e/patches":
        return httpx.Response(200, json=fake.patches)
    if path.startswith("/__e2e/bump/") and method == "POST":
        deck_id = int(path.rsplit("/", 1)[1])
        fake.bump(deck_id)
        return httpx.Response(200, json={"ok": True})
    if path.startswith("/__e2e/deck/"):
        deck = fake.decks.get(int(path.rsplit("/", 1)[1]))
        return httpx.Response(200 if deck else 404, json=deck or {"detail": "Not found."})
    return httpx.Response(404, json={"detail": json.dumps({"unknown": path})})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="info")
