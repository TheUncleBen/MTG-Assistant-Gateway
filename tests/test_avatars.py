"""Profile pictures come from the provider's ``picture`` claim (avatars.py), and only safe ones."""

from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path

import httpx

from mtg_gateway.avatars import AvatarStore, decode_data_uri, gravatar_url, initials_svg

from .conftest import FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser

SUB = "user-1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"


def data_uri(kind: str, data: bytes) -> str:
    return f"data:{kind};base64,{base64.b64encode(data).decode()}"


def live(tmp_path: Path, **over: object):
    return make_settings(tmp_path, required_group="mtg-users", membership_check_ttl=0, **over)


async def test_an_uploaded_picture_is_shown_in_the_account_icon(tmp_path: Path, idp: FakeIdP) -> None:
    idp.user = {**idp.user, "picture": data_uri("image/png", PNG)}
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        page = await b.http.get("/account")
        assert "src='/account/avatar'" in page.text
        assert "img-src 'self'" in page.headers["content-security-policy"]
        r = await b.http.get("/account/avatar")
        assert r.status_code == 200 and r.content == PNG
        assert r.headers["content-type"].startswith("image/png")
        assert r.headers["cache-control"].startswith("private")
        assert r.headers["x-content-type-options"] == "nosniff"
        # Changed at the provider: the next membership check picks it up.
        idp.directory[SUB]["picture"] = ""
        await b.http.get("/account")
        r = await b.http.get("/account/avatar")
        assert r.headers["content-type"].startswith("image/svg+xml") and b">A<" in r.content
        await b.aclose()


async def test_svg_and_other_addresses_are_never_used(tmp_path: Path, idp: FakeIdP) -> None:
    idp.user = {**idp.user, "picture": data_uri("image/svg+xml", SVG)}
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        r = await b.http.get("/account/avatar")
        assert b"script" not in r.content and b">A<" in r.content  # initials instead
        await b.aclose()
    assert decode_data_uri(data_uri("image/png", SVG)) is None  # the bytes decide, not the label
    assert decode_data_uri(data_uri("image/png", PNG)) == (PNG, "image/png")
    for bad in (
        "http://www.gravatar.com/avatar/x",
        "https://gravatar.com.evil.test/avatar/x",
        "https://user:pw@gravatar.com/avatar/x",
        "https://gravatar.com:8443/avatar/x",
        "https://169.254.169.254/latest/meta-data",
        "https://authentik.internal/media/x.png",
    ):
        assert gravatar_url(bad) is None, bad
    assert gravatar_url("https://www.gravatar.com/avatar/abc?s=128&d=404")


async def test_a_gravatar_is_fetched_by_the_gateway_once(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, content=PNG)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = AvatarStore(tmp_path, http=http)
    url = "https://secure.gravatar.com/avatar/abc?s=128"
    await store.update(SUB, {"picture": url})
    for task in list(store._tasks):
        await task
    assert store.get(SUB) == (PNG, "image/png")
    await store.update(SUB, {"picture": url})  # unchanged claim: nothing fetched again
    assert calls == [url] and not store._tasks
    await store.update(SUB, {"picture": "https://evil.test/x.png"})  # ignored: initials, no request
    assert store.get(SUB) is None and len(calls) == 1
    store.delete(SUB)
    assert not list(tmp_path.iterdir())
    await http.aclose()


async def test_a_gravatar_that_is_not_an_image_is_not_kept(tmp_path: Path) -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=SVG)))
    store = AvatarStore(tmp_path, http=http)
    await store.update(SUB, {"picture": "https://www.gravatar.com/avatar/abc"})
    for task in list(store._tasks):
        await task
    assert store.get(SUB) is None
    await http.aclose()


async def test_no_session_gets_no_picture_and_delete_my_data_removes_it(tmp_path: Path, idp: FakeIdP) -> None:
    idp.user = {**idp.user, "picture": data_uri("image/png", PNG)}
    async with running(Harness(live(tmp_path), idp)) as h:
        anon = await h.http.get("/account/avatar")
        assert anon.status_code in (302, 401, 404) and anon.content != PNG
        b = Browser(h)
        await b.login()
        assert (await b.http.get("/account/avatar")).content == PNG
        csrf = await b.csrf()
        assert list((tmp_path / "data" / "avatars").glob("*.img"))
        await b.http.post("/account", data={"csrf": csrf, "action": "delete_data", "confirm": "yes"})
        assert not list((tmp_path / "data" / "avatars").iterdir())
        await b.aclose()


def test_initials_are_escaped_and_never_empty() -> None:
    assert b">AL<" in initials_svg("Ada Lovelace", "s")
    assert b">?<" in initials_svg("", "s")
    assert not re.search(rb"<(?!/?(svg|circle|text)\b)", initials_svg("<b>&", "s"))


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


async def settle(h: Harness) -> None:
    for task in list(h.app.state.gateway.membership.avatars._tasks):
        await task


async def test_an_uploaded_picture_comes_once_and_never_rides_in_tokens(tmp_path: Path, idp: FakeIdP) -> None:
    """Authentik 2026.8.3 and newer leave uploaded pictures out of ``picture``; the optional
    scope mapping sends a short version instead, and the image only when asked for it."""
    idp.uploaded[SUB] = data_uri("image/png", PNG)
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        await settle(h)
        assert (await b.http.get("/account/avatar")).content == PNG
        assert idp.picture_fetches == 1
        for _ in range(3):  # membership checks: the version is unchanged, nothing is fetched
            await b.http.get("/account")
            await settle(h)
        assert idp.picture_fetches == 1
        idp.uploaded[SUB] = data_uri("image/jpeg", JPEG)  # a new upload in Authentik
        await b.http.get("/account")
        await settle(h)
        assert idp.picture_fetches == 2
        r = await b.http.get("/account/avatar")
        assert r.content == JPEG and r.headers["content-type"].startswith("image/jpeg")
        assert all(len(t) < 200 for t in idp.access_tokens)  # the image never rides in a token
        await b.aclose()


async def test_an_uploaded_svg_or_a_failed_fetch_leaves_initials(tmp_path: Path, idp: FakeIdP) -> None:
    idp.uploaded[SUB] = data_uri("image/svg+xml", SVG)
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        await settle(h)
        r = await b.http.get("/account/avatar")
        assert b"script" not in r.content and r.headers["content-type"].startswith("image/svg+xml")
        await b.aclose()

    store = AvatarStore(tmp_path / "s")

    async def boom() -> str | None:
        raise httpx.ConnectError("down")

    await store.update(SUB, {"mtg_picture_version": "abc"}, boom)
    for task in list(store._tasks):
        await task
    assert store.get(SUB) is None and not (tmp_path / "s").exists()  # nothing saved: tried again later
    await store.update(SUB, {"mtg_picture_version": "../../x"}, boom)  # not a version: ignored
    assert not store._tasks


async def test_delete_during_a_fetch_is_not_undone(tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def slow() -> str | None:
        await gate.wait()
        return data_uri("image/png", PNG)

    store = AvatarStore(tmp_path)
    await store.update(SUB, {"mtg_picture_version": "v1"}, slow)
    await asyncio.sleep(0)
    store.delete(SUB)  # Delete my data while the picture is on its way
    gate.set()
    for task in list(store._tasks):
        await task
    assert store.get(SUB) is None and not list(tmp_path.glob("*"))
