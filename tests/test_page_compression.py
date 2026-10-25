"""Speed (Ben, 25 Oct 2026): pages go out compressed (a long deck page is about a sixth of its
size), and a page's form token is different on every load, so a compressed page can't be used to
guess it a character at a time (the BREACH attack: someone who can watch the encrypted sizes and
make the browser load pages with text of their choosing)."""

from __future__ import annotations

import gzip
import re

import httpx

from .conftest import GATEWAY, Stack
from .test_decks_and_proxy import Browser
from .test_folder_move_delete import _setup, linked_user, stack_options  # noqa: F401

PAGE = {"Accept": "text/html,application/xhtml+xml", "Accept-Encoding": "gzip"}


async def _raw(b: Browser, path: str, headers: dict[str, str]) -> httpx.Response:
    """The response as sent, not decoded by httpx."""
    req = b.http.build_request("GET", path, headers=headers)
    r = await b.http.send(req, stream=True)
    body = b"".join([chunk async for chunk in r.aiter_raw()])
    await r.aclose()
    r._content = body  # noqa: SLF001
    return r


async def test_pages_go_out_compressed_and_programs_answers_do_not(stack: Stack) -> None:
    await linked_user(stack)
    _setup(stack)
    b = Browser(stack.h)
    await b.login()
    try:
        r = await _raw(b, "/decks/42", PAGE)
        assert r.status_code == 200
        assert r.headers["content-encoding"] == "gzip"
        assert "accept-encoding" in r.headers["vary"].lower()
        page = gzip.decompress(r.content)
        assert b"</html>" in page and len(r.content) * 3 < len(page)
        # not without asking for it
        plain = await _raw(b, "/decks/42", {"Accept": "text/html", "Accept-Encoding": "identity"})
        assert "content-encoding" not in plain.headers and b"</html>" in plain.content
        # a script's JSON (and an assistant's answers) stay as they are
        api = await _raw(b, "/notifications/count", {"Accept": "application/json", "Accept-Encoding": "gzip"})
        assert api.status_code == 200 and "content-encoding" not in api.headers
    finally:
        await b.http.aclose()


async def test_the_form_token_is_different_on_every_load_and_each_one_works(stack: Stack) -> None:
    await linked_user(stack)
    b = Browser(stack.h)
    await b.login()
    try:
        first, second = await b.csrf(), await b.csrf()
        assert first != second
        for token in (first, second):
            r = await b.http.post(
                "/decks/api/folders",
                json={"name": "Speed test " + token[:4]},
                headers={"X-CSRF-Token": token},
            )
            assert r.status_code != 403, r.text
        # a made-up or cut token is still refused
        for bad in ("0" * 128, first[:64], first[:-1] + ("0" if first[-1] != "0" else "1"), ""):
            r = await b.http.post("/decks/api/folders", json={"name": "x"}, headers={"X-CSRF-Token": bad})
            assert r.status_code == 403, bad
    finally:
        await b.http.aclose()


def test_no_page_token_stays_the_same_across_loads() -> None:
    """Every page gets its token from pages._csrf, which masks it with fresh randomness."""
    from types import SimpleNamespace

    from mtg_gateway.pages import _csrf, csrf_header_ok

    s = SimpleNamespace(session_secret="x" * 40)
    a, b = _csrf(s, "sid"), _csrf(s, "sid")
    assert a and b and a != b and re.fullmatch(r"[0-9a-f]{128}", a)
    req = SimpleNamespace(headers={"x-csrf-token": a})
    assert csrf_header_ok(s, "sid", req) and not csrf_header_ok(s, "other", req)
    assert GATEWAY


async def test_sign_in_pages_are_never_compressed(stack: Stack) -> None:
    """The pages of a sign-in carry one-time values (the consent form's token, the Android app's
    hand-back code): they go out as they are, whatever the browser accepts."""
    await linked_user(stack)
    b = Browser(stack.h)
    await b.login()
    try:
        r = await _raw(b, "/login/app-return?code=" + "a" * 43, PAGE)
        assert r.status_code == 200 and "content-encoding" not in r.headers
        # nor the Account pages: inside the app, Account details carries the notification topic
        for path in ("/account", "/account/details", "/account?ok=saved"):
            r = await _raw(b, path, PAGE)
            assert r.status_code == 200 and "content-encoding" not in r.headers, path
    finally:
        await b.http.aclose()


def test_the_consent_token_is_masked_too() -> None:
    from mtg_gateway.masked import mask, matches

    key = b"k" * 32
    a, b = mask(key), mask(key)
    assert a != b and matches(key, a) and matches(key, b) and matches(key, key.hex())
    for bad in ("", "zz", a[:-2], "0" * 128, a + "00", " " + a):
        assert not matches(key, bad), bad
