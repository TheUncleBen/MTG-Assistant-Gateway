"""A custom card's picture, fetched by the gateway from the address Archidekt gives for it and
served from the gateway's own address (custom_pictures.py)."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import io

import httpx
import pytest

from mtg_gateway import custom_pictures

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED
from .test_deck_custom_cards import _propose, _seed
from .test_decks_and_proxy import Browser, linked_user

stack_options = stack_with(SHIPPED)


def _png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (5, 7), (1, 2, 3)).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def pictures(stack: Stack, monkeypatch):
    custom_pictures._KEPT.clear()
    monkeypatch.setattr(custom_pictures, "_public_host", lambda host: host == "cdn.example")
    seen: list[str] = []
    answers: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return answers.get(str(request.url)) or httpx.Response(404)

    stack.h.app.state.gateway.custom_picture_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return seen, answers


async def _deck_with_custom(stack: Stack) -> Browser:
    _seed(stack.ark)
    token = await linked_user(stack)
    p = await _propose(stack.h, token, [{"action": "add", "custom_card_id": 41}])
    from .test_account_actions import _browser_apply

    assert (await _browser_apply(stack.h, p["proposal_id"]))["state"] == "applied"
    b = Browser(stack.h)
    await b.login("/decks")
    return b


async def test_a_custom_cards_picture_is_served_from_the_gateway(stack: Stack, pictures) -> None:
    seen, answers = pictures
    answers["https://cdn.example/41.png"] = httpx.Response(200, content=_png())
    b = await _deck_with_custom(stack)
    try:
        r = await b.http.get("/decks/api/custom_picture/42/41")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", r.text
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in r.headers["content-security-policy"]
        assert (await b.http.get("/decks/api/custom_picture/42/41")).status_code == 200
        assert seen == ["https://cdn.example/41.png"]  # kept: one fetch
        # a card not in the deck, a back face it hasn't, a bad face: 404, nothing fetched
        for path in (
            "/decks/api/custom_picture/42/44",
            "/decks/api/custom_picture/42/41?face=back",
            "/decks/api/custom_picture/42/41?face=x",
            "/decks/api/custom_picture/42/x",
        ):
            assert (await b.http.get(path)).status_code == 404, path
        assert len(seen) == 1
    finally:
        await b.aclose()


async def test_only_a_picture_from_a_public_https_address_is_served(stack: Stack, pictures) -> None:
    seen, answers = pictures
    b = await _deck_with_custom(stack)
    rows = stack.ark.decks[42]["customCards"]
    try:
        for url, answer in (
            ("http://cdn.example/41.png", httpx.Response(200, content=_png())),  # not https
            ("https://intranet.local/41.png", httpx.Response(200, content=_png())),  # not public
            (
                "https://cdn.example/page.html",
                httpx.Response(200, content=b"<html><script>"),
            ),  # not a picture
            ("https://cdn.example/big.png", httpx.Response(200, content=_png() + b"0" * (3 * 1024 * 1024))),
            (
                "https://cdn.example/moved.png",
                httpx.Response(302, headers={"location": "https://x.example/"}),
            ),
        ):
            custom_pictures._KEPT.clear()
            rows[0]["card"]["frontImageUrl"] = url
            answers[url] = answer
            stack.h.app.state.gateway.decks.client._cache.clear()
            r = await b.http.get("/decks/api/custom_picture/42/41")
            assert r.status_code == 404, url
        assert "http://cdn.example/41.png" not in seen and "https://intranet.local/41.png" not in seen
        assert all("x.example" not in u for u in seen)  # no redirect followed
    finally:
        await b.aclose()


async def test_a_signed_out_visitor_gets_nothing(stack: Stack, pictures) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stack.h.app), base_url="https://gw.test"
    ) as c:
        r = await c.get("/decks/api/custom_picture/42/41")
    assert r.status_code == 401


async def test_my_lists_rows_pictures_come_from_the_lists_last_read(stack: Stack, pictures) -> None:
    seen, answers = pictures
    answers["https://cdn.example/41.png"] = httpx.Response(200, content=_png())
    _seed(stack.ark)
    await linked_user(stack)
    b = Browser(stack.h)
    try:
        await b.login("/lists")
        # nothing read yet: nothing served
        assert (await b.http.get("/lists/api/custom_picture/41")).status_code == 404
        assert (await b.http.get("/lists/custom")).status_code == 200
        r = await b.http.get("/lists/api/custom_picture/41")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert (await b.http.get("/lists/api/custom_picture/51")).status_code == 404  # amy's card
        # a relink forgets them
        stack.h.app.state.gateway.decks.forget_member("user-1")
        assert (await b.http.get("/lists/api/custom_picture/41")).status_code == 404
    finally:
        await b.aclose()
