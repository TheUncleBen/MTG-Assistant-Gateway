"""The gateway's busy answer (dbwait.py, a request that waited the limit for the database's write
lock): a program gets JSON it can read, and a browser gets a page in the site's look whose Try
again really sends the same request once more, with what the member typed. Both are marked with
the X-MTG-Busy header, so the pages' scripts (static/fetchjson.js) and the Android app tell it
from a reverse proxy's own 503."""

from __future__ import annotations

import html
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from mtg_gateway import dbwait


class _LockedDb:
    """A database whose write lock is held for as long as the test says."""

    def __init__(self) -> None:
        self.locked = True
        self.path = ":memory:"

    def write_free(self) -> bool:
        return not self.locked

    def wait_write_free(self, limit: float) -> bool:  # noqa: ARG002
        return not self.locked


async def _echo(request: Request) -> PlainTextResponse:
    body = (await request.body()).decode("utf-8", "replace")
    return PlainTextResponse(f"{request.method} {request.url.path}?{request.url.query} {body}")


def _client(db: _LockedDb, limit: float = 0.0) -> httpx.AsyncClient:
    app = Starlette(routes=[Route("/{rest:path}", _echo, methods=["GET", "POST"])])
    wrapped = dbwait.DatabaseWaitMiddleware(app, db=db, limit=limit)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapped), base_url="http://gateway.example.com"
    )


HTML = {"accept": "text/html,application/xhtml+xml"}


async def test_a_program_gets_json_it_can_read_marked_busy() -> None:
    async with _client(_LockedDb()) as http:
        r = await http.get("/api/v1/proposals")
        assert r.status_code == 503
        assert r.json() == {"ok": False, "error": "busy", "message": dbwait.BUSY_MESSAGE}
        assert r.headers["retry-after"] == "5"
        assert r.headers["x-mtg-busy"] == "1"
        assert r.headers["cache-control"] == "no-store"
        # a page's script asking for JSON (Accept) gets JSON too, whatever the path
        r = await http.post("/decks/1/cards", json={"x": 1}, headers={"accept": "application/json"})
        assert r.status_code == 503 and r.json()["error"] == "busy" and r.headers["x-mtg-busy"] == "1"


async def test_a_browser_page_gets_the_sites_look_and_a_try_again_that_loads_the_same_address() -> None:
    async with _client(_LockedDb()) as http:
        r = await http.get("/decks?view=list&q=a%20b", headers={**HTML, "cookie": "mtg_theme=light"})
        assert r.status_code == 503 and r.headers["x-mtg-busy"] == "1"
        assert r.headers["content-type"].startswith("text/html")
        assert r.headers["retry-after"] == "5" and r.headers["cache-control"] == "no-store"
        page = r.text
        assert "<title>The gateway is busy" in page
        assert "<html lang='en' data-theme='light'>" in page  # the member's chosen theme
        assert "Nothing was changed" in page and "<script" not in page.split("</head>")[0]
        # the one button, in the site's orange, back to the very same address (query and all)
        assert "<a class='btn btn-primary' href='/decks?view=list&amp;q=a%20b'>Try again</a>" in page
        assert "href=''" not in page
        # nothing of the page can ever run or load from elsewhere
        csp = r.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "script-src 'self'" in csp and "form-action 'self'" in csp
        # the theme cookie's other values: dark, and system (no data-theme: the system's choice)
        r = await http.get("/decks", headers={**HTML, "cookie": "mtg_theme=dark"})
        assert "<html lang='en' data-theme='dark'>" in r.text
        r = await http.get("/decks", headers=HTML)
        assert "<html lang='en'>" in r.text and "prefers-color-scheme" in r.text


async def test_a_posted_form_is_kept_on_the_page_and_try_again_posts_it_again() -> None:
    """A member who pressed a form's button must not lose what they typed: the page holds the
    posted fields and its Try again is a form that posts them to the same address once more."""
    db = _LockedDb()
    async with _client(db) as http:
        data = {"csrf": "tok", "name": 'Meren <of> "Clan" & co', "note": "line one\nline two", "empty": ""}
        r = await http.post("/decks/42/rename?from=list", data=data, headers=HTML)
        assert r.status_code == 503 and r.headers["x-mtg-busy"] == "1"
        page = r.text
        assert "<form method='post' action='/decks/42/rename?from=list'>" in page
        for name, value in data.items():
            assert f"<input type='hidden' name='{name}' value='{html.escape(value, quote=True)}'>" in page
        assert "<button type='submit' class='btn btn-primary'>Try again</button>" in page
        assert "What you sent is kept here" in page
        # the form really posts the same thing: once the lock is free the request goes through
        db.locked = False
        r = await http.post("/decks/42/rename?from=list", data=data, headers=HTML)
        assert r.status_code == 200 and r.text.startswith("POST /decks/42/rename?from=list csrf=tok&name=")


async def test_a_form_with_a_file_or_an_oversized_body_gets_go_back_instead() -> None:
    """A file cannot be put back into a page, and a body past the limit is not copied: the member
    goes back to the form they filled in (static/busy.js keeps the typed draft for it)."""
    async with _client(_LockedDb()) as http:
        r = await http.post(
            "/lists/custom/picture",
            files={"picture": ("a.png", b"\x89PNG", "image/png")},
            data={"csrf": "tok", "name": "x"},
            headers=HTML,
        )
        assert r.status_code == 503
        assert "<form method='post'" not in r.text and "\x89PNG" not in r.text
        assert "<button type='button' class='btn btn-primary' data-busy-back>Go back</button>" in r.text
        assert "<script src='/static/busy.js' defer></script>" in r.text
        assert "go back to what you typed" in r.text.lower()
        big = "a" * (dbwait.KEEP_BODY_LIMIT + 1)
        r = await http.post("/x", data={"csrf": "tok", "text": big}, headers=HTML)
        assert r.status_code == 503 and big not in r.text and "data-busy-back" in r.text


async def test_a_posted_form_whose_body_was_read_does_not_hang_the_connection() -> None:
    """The busy answer reads a form's body before answering; one it did not read (a file) is
    drained too, so the connection can be used again (http keep-alive)."""
    async with _client(_LockedDb()) as http:
        for _ in range(3):
            r = await http.post("/x", data={"a": "b"}, headers=HTML)
            assert r.status_code == 503
            r = await http.post("/x", files={"f": ("f.bin", b"0" * 1000)}, headers=HTML)
            assert r.status_code == 503


def test_the_busy_script_sends_the_member_back_and_keeps_the_draft(tmp_path: Any) -> None:  # noqa: ARG001
    from pathlib import Path

    src = Path(dbwait.__file__).parent / "static" / "busy.js"
    js = src.read_text(encoding="utf-8")
    assert "history.back()" in js and "data-busy-back" in js and "mtg_draft_back" in js
    draft = (src.parent / "draft.js").read_text(encoding="utf-8")
    assert "mtg_draft_back" in draft


@pytest.mark.parametrize("path", ["/mcp", "/.well-known/oauth-authorization-server", "/token"])
async def test_machine_paths_get_json_even_when_a_browser_asks(path: str) -> None:
    async with _client(_LockedDb()) as http:
        r = await http.get(path, headers=HTML)
        assert r.status_code == 503 and r.json()["error"] == "busy"


async def test_a_posted_secret_is_never_written_back_into_the_page() -> None:
    """The Archidekt link form posts a password (pages.py). A field that looks secret is never
    kept on the busy page as a hidden input: the member goes back to the form instead (the page
    is HTML on the way to the screen, cached nowhere, but a password belongs in no page)."""
    async with _client(_LockedDb()) as http:
        data = {"csrf": "tok", "action": "link", "archidekt_login": "alice", "archidekt_password": "hunter2"}
        r = await http.post("/account", data=data, headers=HTML)
        assert r.status_code == 503
        assert "hunter2" not in r.text and "archidekt_password" not in r.text and "<form" not in r.text
        assert "data-busy-back>Go back</button>" in r.text
        # the words that make a field secret, and the forms' plain names that must stay keepable
        for name in (
            "archidekt_password",
            "passwd",
            "pwd",
            "secret",
            "token",
            "api_key",
            "apiKey",
            "code",
            "code_verifier",
            "client_secret",
            "otp",
            "key",
            "pin",
            "refresh_token",
            "x-password",
        ):
            assert dbwait.looks_secret(name), name
        for name in (
            "csrf",
            "set_code",
            "card_key",
            "pin_id",
            "keywords",
            "archidekt_login",
            "name",
            "note",
            "deck_id",
            "action",
            "accept_risk",
        ):
            assert not dbwait.looks_secret(name), name
