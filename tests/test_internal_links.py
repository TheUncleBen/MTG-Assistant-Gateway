"""Every internal link on the main pages leads somewhere: a crawl of the signed-in, linked member's
pages (and the signed-out ones) follows each same-site href and none of them answers 404."""

from __future__ import annotations

import html
import re
from urllib.parse import urldefrag, urlparse

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_decks_and_proxy import Browser
from .test_history_page import store_report

stack_options = stack_with(COMPANION)

# The pages a member reaches from the tab bar, the More sheet, the account menu and the footer.
START = (
    "/",
    "/decks",
    "/collection",
    "/cards",
    "/scan",
    "/proposals",
    "/history",
    "/reports",
    "/activity",
    "/account",
    "/account/details",
    "/guide",
    "/install",
    "/install?for=skill",
    "/install?for=android",
    "/lists",
    "/watchlist",
    "/notifications",
)
# Links that end the session or start a sign-in leg (and so leave the crawl's browser signed out).
SKIP = ("/logout", "/login", "/signout", "/account/logout", "/oauth", "/authorize", "/callback")
HREF = re.compile(r"""href=(?:'([^']*)'|"([^"]*)")""")


def _internal(href: str) -> str | None:
    """The path and query of a same-site href, or None for another site, a fragment, mailto: etc."""
    href = html.unescape(href).strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "blob:", "intent:")):
        return None
    u = urlparse(href)
    if u.scheme or u.netloc:
        if u.netloc != "mtg.test":
            return None
    if not u.path.startswith("/"):
        return None
    if u.path.startswith(("/static/", "/assets/")) or "{" in href:
        return None
    path, _ = urldefrag(u.path + (f"?{u.query}" if u.query else ""))
    return path


async def _crawl(b: Browser, start: tuple[str, ...], *, depth: int) -> dict[str, tuple[int, str]]:
    """Breadth-first over same-site links up to ``depth`` hops from ``start``: href -> (status,
    the page that linked it)."""
    seen: dict[str, tuple[int, str]] = {}
    frontier = [(p, "(start)") for p in start]
    for _ in range(depth + 1):
        nxt: list[tuple[str, str]] = []
        for path, origin in frontier:
            if path in seen or path.startswith(SKIP):
                continue
            r = await b.http.get(path, headers=NAV)
            seen[path] = (r.status_code, origin)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
            for m in HREF.finditer(r.text):
                got = _internal(m.group(1) if m.group(1) is not None else m.group(2))
                if got and got not in seen:
                    nxt.append((got, path))
        frontier = nxt
    return seen


async def test_no_internal_link_is_dead(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        store_report(gw.db, sub, "rep_links")
        seen = await _crawl(b, START, depth=2)
        dead = {p: origin for p, (status, origin) in seen.items() if status == 404}
        # the crawl did reach the pages that link the most places
        assert len(seen) > 60, sorted(seen)
        assert "/account/details" in seen and "/proposals" in seen and "/history/reports/rep_links" in seen
        assert dead == {}, dead
    finally:
        await b.aclose()


async def test_no_dead_link_signed_out(stack: Stack) -> None:
    b = Browser(stack.h)
    try:
        seen = await _crawl(b, ("/", "/guide", "/install", "/install?for=skill", "/install?for=android"), depth=2)
        dead = {p: origin for p, (status, origin) in seen.items() if status == 404}
        assert dead == {}, dead
    finally:
        await b.aclose()


async def test_footer_names_the_assistant_skill(stack: Stack) -> None:
    """The website footer's link to the skill's install page says what it opens."""
    b = Browser(stack.h)
    await b.login("/decks")
    r = await b.http.get("/decks", headers=NAV)
    await b.aclose()
    footer = r.text.split("<footer class='site'>")[1].split("</footer>")[0]
    assert "<a href='/install?for=skill'>Assistant skill</a>" in footer
