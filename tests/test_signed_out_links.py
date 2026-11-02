"""A link opened while signed out comes back to the same address after the sign-in: the whole
address, path and query, so a price alert's push (``/watchlist?open=…``), a shared forum thread
or a folder of decks lands where it pointed and not on a generic page (0.7.46 audit QG-05).

Every GET page route the app registers is walked signed out, with a query on it; each one that
sends the browser to sign in must keep that query in ``next``. A POST cannot be replayed, so it
comes back to the page its form lives on."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.routing import Route

from mtg_gateway.pages import _safe_next

from .conftest import FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser

NAVIGATE = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
QUERY = "open=w0&view=list"

# routes that are not member pages: machine-facing, sign-in legs, static files, the admin area
# (its own guard, admin.py) and the offline shell. Each is read without a sign-in or has a
# sign-in of its own, and none of them is a page a member would share a link to.
NOT_A_PAGE = re.compile(
    r"^/(\.well-known|healthz|metrics|mcp|register|token|authorize|login|logout|signed-out|auth/|"
    r"static|sw\.js|offline|install|skill|plugin|app/|app$|app-|consent|callback|api/|admin)"
)


@pytest.fixture
async def harness(tmp_path: Path, idp: FakeIdP):
    async with running(Harness(make_settings(tmp_path, required_group="mtg-users"), idp)) as h:
        yield h


def _fill(path: str) -> str:
    """A route pattern with every parameter given a value (``{deck_id}`` → ``42``)."""
    return re.sub(r"\{[^}]+\}", "42", path)


def _page_routes(h: Harness) -> list[str]:
    out = []
    for r in h.app.router.routes:
        if not isinstance(r, Route) or "GET" not in (r.methods or ()):
            continue
        if NOT_A_PAGE.match(r.path) or r.path.endswith((".json", ".csv", ".md", ".txt", ".pdf", ".zip")):
            continue
        out.append(_fill(r.path))
    assert len(out) > 60, out  # the walk covers the whole site, not a handful of pages
    return out


async def test_every_signed_out_page_keeps_its_query_in_next(harness: Harness) -> None:
    lost: list[str] = []
    sent = 0
    for path in _page_routes(harness):
        r = await harness.http.get(f"{path}?{QUERY}", headers=NAVIGATE)
        loc = r.headers.get("location", "")
        if r.status_code not in (302, 303) or not loc.startswith("/login?"):
            continue  # read signed out (Guide, Precons…) or its own answer: not a sign-in
        sent += 1
        nxt = parse_qs(urlparse(loc).query).get("next", [""])[0]
        if nxt != f"{path}?{QUERY}":
            lost.append(f"{path}: next={nxt}")
    assert sent > 40, sent  # most pages need a sign-in
    assert not lost, "these pages lose their address on the way to sign-in:\n" + "\n".join(lost)


async def test_the_links_the_audit_named(harness: Harness) -> None:
    # the price-alert push, a shared thread, a folder, the Binder's list view
    for path in ("/watchlist?open=w0", "/forum/thread/42", "/decks?folder=7", "/collection?view=list"):
        r = await harness.http.get(path, headers=NAVIGATE)
        assert r.status_code == 302, path
        nxt = parse_qs(urlparse(r.headers["location"]).query)["next"][0]
        assert nxt == path, (path, nxt)


async def test_a_query_with_encoded_characters_survives_the_round_trip(harness: Harness) -> None:
    """The search's own words come back exactly: percent-encoded letters, a plus and an ampersand."""
    r = await harness.http.get("/search?q=Sol%20Ring&sort=price+desc", headers=NAVIGATE)
    assert r.status_code == 302
    nxt = parse_qs(urlparse(r.headers["location"]).query)["next"][0]
    assert nxt == "/search?q=Sol%20Ring&sort=price+desc"
    b = Browser(harness)
    try:
        await b.login(nxt)  # the sign-in's last redirect is the very address
    finally:
        await b.aclose()


async def test_a_post_comes_back_to_its_page_and_the_new_deck_form_keeps_its_draft(
    harness: Harness,
) -> None:
    r = await harness.http.post("/decks/new", data={"name": "x"}, headers=NAVIGATE)
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/decks/new?draft=1"
    r = await harness.http.post("/collection", data={"action": "add"}, headers=NAVIGATE)
    assert r.status_code == 302 and r.headers["location"].startswith("/login?next=/collection")


def test_only_a_relative_address_on_this_site_is_a_next() -> None:
    for bad in ("//evil.example/x", "https://evil.example/x", "/\\evil.example", "", None):
        assert _safe_next(bad) == "/account", bad
    assert _safe_next("/watchlist?open=w0") == "/watchlist?open=w0"
    long = "/search?q=" + "a" * 1500
    assert _safe_next(long) == long[:1000]


class _Req:
    def __init__(self, method: str, path: str, query: str = "") -> None:
        self.method = method
        self.url = type("U", (), {"path": path, "query": query})()


def test_login_redirect_here_keeps_a_get_and_falls_back_for_a_post() -> None:
    from mtg_gateway.pages import login_redirect_here

    assert login_redirect_here(_Req("GET", "/watchlist", "open=w0")).headers["location"] == (
        "/login?next=/watchlist?open=w0"
    )
    assert login_redirect_here(_Req("GET", "/forum/thread/7")).headers["location"] == (
        "/login?next=/forum/thread/7"
    )
    assert (
        login_redirect_here(_Req("POST", "/decks/new", "x=1"), "/decks/new?draft=1").headers["location"]
        == "/login?next=/decks/new?draft=1"
    )
    # the ampersand is carried as one character of the next value, not as a second parameter
    loc = login_redirect_here(_Req("GET", "/search", "q=a&sort=b")).headers["location"]
    assert parse_qs(urlparse(loc).query)["next"] == ["/search?q=a&sort=b"]


# -- every sign-in page's own Sign in / Try again link keeps the whole return address -------------

WHOLE = "/collection?view=list&q=sol"
HOSTILE = (
    "//evil.example/x",
    "https://evil.example/x",
    "http://evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "//%2Fevil.example",
    "javascript:alert(1)",
    "evil.example",
    "",
    "/%5Cevil.example",
    "/\\/evil.example",
    "data:text/html,x",
)


def _sign_in_links(text: str) -> list[str]:
    """The ``next`` of each link on the page that starts a sign-in."""
    import html as _html

    out = []
    for href in re.findall(r"href='(/login\?[^']*)'", text):
        q = parse_qs(urlparse(_html.unescape(href)).query)
        out.append(q.get("next", [""])[0])
    return out


async def _signin_pages(h: Harness, monkeypatch: pytest.MonkeyPatch, nxt: str) -> dict[str, list[str]]:
    """Each sign-in page that offers a Sign in or Try again link, opened with ``next=nxt``; for each
    one the ``next`` its links carry. A page that redirects instead is followed once."""
    from mtg_gateway.app_signin import challenge_of
    from mtg_gateway.auth_provider import LoginError, cookie_name

    good = challenge_of("v" * 43)
    ended = {cookie_name("mtg_session", h.settings): "ended"}
    pages: dict[str, list[str]] = {}
    r = await h.http.get("/login", params={"next": nxt, "app_challenge": "bad"}, headers=NAVIGATE)
    pages["bad app challenge"] = _sign_in_links(r.text)
    r = await h.http.get(
        "/login", params={"next": nxt, "app_challenge": good, "app_return": "elsewhere"}, headers=NAVIGATE
    )
    pages["bad app return"] = _sign_in_links(r.text)
    # the identity provider's sign-in could not be started
    provider = h.app.state.gateway.provider

    async def refused(*_a: object, **_k: object) -> str:
        raise LoginError("The sign-in service is not answering.", 503, kind="unavailable")

    with monkeypatch.context() as m:
        m.setattr(provider, "start_idp_login", refused)
        r = await h.http.get("/login", params={"next": nxt}, headers=NAVIGATE)
    pages["sign-in not started"] = _sign_in_links(r.text)
    # this browser's sign-in ended (an admin signed it out): /login sends it to /signed-out first
    with monkeypatch.context() as m:
        m.setattr(h.db, "session_end_reason", lambda _sid: "admin")
        r = await h.http.get("/login", params={"next": nxt}, headers=NAVIGATE, cookies=ended)
        assert r.status_code == 303 and r.headers["location"].startswith("/signed-out?"), r.headers
        loc = r.headers["location"]
        pages["sign-in ended (redirect)"] = [parse_qs(urlparse(loc).query).get("next", [""])[0]]
        r = await h.http.get(loc, headers=NAVIGATE, cookies=ended)
        pages["sign-in ended (page)"] = _sign_in_links(r.text)
    r = await h.http.get("/signed-out", params={"next": nxt}, headers=NAVIGATE)
    pages["signed out"] = _sign_in_links(r.text)
    # canceled at the identity provider
    start = await h.http.get("/login", params={"next": nxt})
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    r = await h.http.get(
        "/auth/callback", params={"state": state, "error": "access_denied"}, headers=NAVIGATE
    )
    pages["canceled"] = _sign_in_links(r.text)
    h.http.cookies.clear()
    return pages


async def test_each_sign_in_page_link_keeps_the_whole_return_address(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A return address with an ``&`` (the Binder's list view, filtered) comes back whole from every
    sign-in page's Sign in or Try again link, not cut at the ``&`` (0.7.46 re-audit)."""
    pages = await _signin_pages(harness, monkeypatch, WHOLE)
    assert len(pages) == 7
    wrong = {name: got for name, got in pages.items() if not got or any(n != WHOLE for n in got)}
    assert not wrong, wrong


async def test_no_sign_in_page_link_leaves_the_site(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    for bad in HOSTILE:
        pages = await _signin_pages(harness, monkeypatch, bad)
        for name, got in pages.items():
            for n in got:
                # a path on this site: never another host, never a backslash a browser reads as "/"
                assert n.startswith("/") and not n.startswith("//") and "\\" not in n, (bad, name, n)
                assert urlparse(n).netloc == "" and urlparse(n).scheme == "", (bad, name, n)
