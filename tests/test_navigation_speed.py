"""Moving between pages quickly (0.7.23, R-131): every /static file a page names carries its
digest and is kept by the browser for a year, text files go out gzipped and a file the browser
already has costs a 304; the shared stylesheets are files instead of 100 KB of inline CSS on every
page; a deck page shown again within a few seconds (another view, Back) asks Archidekt nothing,
while a write or the browser's Reload does; the service worker uses navigation preload; a tap on a
link shows the navigation bar at once."""

from __future__ import annotations

import asyncio
import gzip
import re

from mtg_gateway import theme
from mtg_gateway.deckpage import DECK_CSS

from .test_decks_and_proxy import Browser, Stack, call, linked_user, stack, structured

__all__ = ["stack"]

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
STATIC_REF = re.compile(r"(?:src|href)='(/static/[^']+)'")
PAGES = ("/", "/decks", "/decks/42", "/decks/42?view=grid", "/cards?q=sol", "/collection", "/search?q=x")
PAGES += ("/scan", "/proposals", "/history", "/account", "/guide", "/install", "/decks/42/settings")


def _deck_reads(stack: Stack) -> int:
    return sum(1 for m, p in stack.ark.calls if m == "GET" and p == "/api/decks/42/")


async def _linked(stack: Stack) -> Browser:
    b = Browser(stack.h)
    await b.login("/")
    r = await b.link("alice", "pw-alice")
    assert r.status_code == 303, r.text
    return b


async def test_pages_name_static_files_by_digest_and_link_the_shared_stylesheets(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        for path in PAGES:
            r = await b.http.get(path, headers=NAV)
            assert r.status_code == 200, (path, r.status_code)
            refs = STATIC_REF.findall(r.text)
            assert refs, path
            for ref in refs:
                assert re.fullmatch(r"/static/[A-Za-z0-9_.-]+\?v=[0-9a-f]{12}", ref), (path, ref)
            # the site's stylesheet is a file the browser keeps, not 60 KB in every page
            assert "/static/site.css?v=" in r.text and theme.CSS not in r.text, path
            assert DECK_CSS not in r.text, path
            # ... which every page's policy allows
            style_src = r.headers["content-security-policy"].split("style-src")[1].split(";")[0]
            assert "'self'" in style_src.split(), (path, style_src)
        site = await b.http.get(re.search(r"href='(/static/site\.css\?v=[0-9a-f]+)'", r.text)[1])
        assert site.status_code == 200 and site.text == theme.CSS
        assert site.headers["content-type"].startswith("text/css")
        deck = await b.http.get("/decks/42", headers=NAV)
        css = await b.http.get(re.search(r"href='(/static/deck\.css\?v=[0-9a-f]+)'", deck.text)[1])
        assert css.status_code == 200 and css.text == DECK_CSS
    finally:
        await b.aclose()


async def test_static_files_are_kept_long_gzipped_and_revalidated_without_a_body(stack: Stack) -> None:
    http = stack.h.http
    version = theme.static_version("deck.js")
    assert version and re.fullmatch(r"[0-9a-f]{12}", version)
    # asked for by its digest: a year, never asked again
    r = await http.get(f"/static/deck.js?v={version}", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers["content-encoding"] == "gzip" and r.headers["vary"] == "Accept-Encoding"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"].startswith("text/javascript")
    plain = (theme.STATIC_DIR / "deck.js").read_bytes()
    assert r.content == plain  # httpx undoes the gzip
    assert int(r.headers["content-length"]) < len(plain) / 2
    # without it (an old link, a hand-typed address) or with an old digest: five minutes as before
    for url in ("/static/deck.js", "/static/deck.js?v=000000000000"):
        r = await http.get(url, headers={"Accept-Encoding": "identity"})
        assert r.headers["cache-control"] == "public, max-age=300" and "content-encoding" not in r.headers
        assert r.content == plain
    # a copy the browser already holds costs no body
    etag = r.headers["etag"]
    r = await http.get("/static/deck.js", headers={"If-None-Match": etag})
    assert r.status_code == 304 and r.content == b"" and r.headers["etag"] == etag
    # images are sent as they are
    r = await http.get("/static/gateway-icon-192.png", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and "content-encoding" not in r.headers
    assert r.headers["content-type"] == "image/png"
    # nothing outside the folder, nothing that isn't there
    for bad in ("/static/nope.js", "/static/..%2fapp.py", "/static/site.css.map"):
        assert (await http.get(bad)).status_code == 404, bad


def test_gzipped_copy_is_the_file() -> None:
    from mtg_gateway.companion import _static_payload

    body, gz, media = _static_payload("site.css")
    assert body == theme.CSS.encode() and gz is not None and gzip.decompress(gz) == body
    assert media == "text/css"


async def test_a_deck_page_shown_again_asks_archidekt_once_until_a_write_or_reload(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        before = _deck_reads(stack)
        for view in ("text", "grid", "table", "stacks", "text"):
            r = await b.http.get(f"/decks/42?view={view}", headers=NAV)
            assert r.status_code == 200 and "Sample" in r.text
        assert _deck_reads(stack) == before + 1  # one read for five views
        # the browser's Reload (and a hard reload) reads it again
        await b.http.get("/decks/42", headers={**NAV, "Cache-Control": "max-age=0"})
        assert _deck_reads(stack) == before + 2
        await b.http.get("/decks/42", headers={**NAV, "Cache-Control": "no-cache", "Pragma": "no-cache"})
        assert _deck_reads(stack) == before + 3
        await b.http.get("/decks/42", headers=NAV)
        assert _deck_reads(stack) == before + 3
        # a change through the gateway shows on the very next view
        token = await linked_user(stack)
        changes = [{"action": "set_quantity", "card_name": "Forest", "quantity": 9}]
        p = structured(
            await call(stack.h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes})
        )
        done = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
        assert done["ok"], done
        reads = _deck_reads(stack)
        r = await b.http.get("/decks/42", headers=NAV)
        assert _deck_reads(stack) == reads + 1
        assert re.search(r"data-card='Forest'[^>]*data-qty='9'", r.text), "the page shows the new count"
    finally:
        await b.aclose()


async def test_a_deck_read_under_way_during_a_write_is_not_kept(stack: Stack) -> None:
    decks = stack.h.app.state.gateway.decks
    started, release = asyncio.Event(), asyncio.Event()
    reads = 0

    async def slow_read(sub, ref):
        nonlocal reads
        reads += 1
        started.set()
        await release.wait()
        return {"read": reads}

    decks.get_any_deck = slow_read  # type: ignore[method-assign]
    task = asyncio.ensure_future(decks.page_deck("u1", "42"))
    await started.wait()
    decks._on_archidekt_write("/decks/42/modifyCards/")  # a write lands meanwhile
    release.set()
    assert await task == {"read": 1}
    assert await decks.page_deck("u1", "42") == {"read": 2}  # not served the pre-write read
    assert await decks.page_deck("u1", "42") == {"read": 2}  # this one is kept
    # one member's read is never shown to another
    assert await decks.page_deck("u2", "42") == {"read": 3}


async def test_reload_skips_the_shared_read_cache_for_a_deck_read_without_a_link(stack: Stack) -> None:
    """Bug 11: a member with no linked account reads decks anonymously, through the client's
    shared 60-second cache; the page's Reload (page_deck fresh) now asks Archidekt again."""
    decks = stack.h.app.state.gateway.decks
    seen: list[dict] = []
    real = decks.client.get_deck

    async def get_deck(token, deck_id, **kw):
        seen.append({"token": token, **kw})
        return await real(token, deck_id, **kw)

    decks.client.get_deck = get_deck  # type: ignore[method-assign]
    await decks.page_deck("nobody-linked", "42")
    await decks.page_deck("nobody-linked", "42", fresh=True)
    assert seen == [{"token": None}, {"token": None, "fresh": True}]


async def test_page_deck_off_when_the_cache_is_off(stack: Stack) -> None:
    decks = stack.h.app.state.gateway.decks
    decks.page_deck_seconds = 0.0
    count = 0

    async def read(sub, ref):
        nonlocal count
        count += 1
        return count

    decks.get_any_deck = read  # type: ignore[method-assign]
    assert [await decks.page_deck("u1", "42") for _ in range(3)] == [1, 2, 3]


async def test_service_worker_uses_navigation_preload(stack: Stack) -> None:
    r = await stack.h.http.get("/sw.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "navigationPreload.enable()" in r.text and "e.preloadResponse" in r.text
    # still network only, still the offline page as the last resort
    assert "caches" not in r.text and "You're offline" in r.text


async def test_feedback_script_shows_the_navigation_bar_on_a_tap(stack: Stack) -> None:
    js = (await stack.h.http.get("/static/feedback.js")).text
    assert "navprogress" in js and "navigating" in js
    assert ".navprogress{" in theme.CSS and "html.navigating .navprogress" in theme.CSS
    assert "prefers-reduced-motion" in theme.CSS.split(".navprogress{", 1)[1]
