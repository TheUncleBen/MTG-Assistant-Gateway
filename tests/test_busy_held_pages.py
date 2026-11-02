"""While Archidekt can't be asked (the member's allowance used up, Archidekt's pause, an outage)
the Binder, My lists, a profile and a search a member has already read show that last read,
dated and with Try again, instead of the whole-page "Too many requests" (0.7.46 audit): the
same strip the deck page and My decks use. A page never read before still gets the busy page
(test_companion). On the overflow sweep's seeded site (test_overflow_sweep_browser)."""

from __future__ import annotations

import html
import re

import httpx

from mtg_gateway.decks import RateBudget

from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_suggest_browser import Server

HELD_PAGES = (
    ("/collection", "your binder", "Sol Ring"),
    ("/lists/custom", "this list", "lp-custom"),
    ("/users/alice", "this profile", "Sample Commander Deck"),
    ("/search?name=Sample", "this search", "Sample Commander Deck"),
)


def _spend(server: Server, h: httpx.Client) -> None:  # noqa: F811
    """The member's allowance used up, after a write of theirs forgot the pages' fresh copies."""
    decks = server.app.state.gateway.decks
    decks.archidekt_budget = RateBudget(per_window=1, window=600)
    assert decks.archidekt_budget.take("user-1")  # the one call of the window
    assert not decks.archidekt_budget.take("user-1")
    # what a write of the member's does to the pages' fresh copies (DeckService._on_archidekt_write)
    decks.short_reads.drop("user-1")
    for hook in decks.write_hooks:
        hook("user-1", "/collection/api/add")


def test_pages_already_read_show_their_last_read_while_archidekt_is_busy(server: Server) -> None:  # noqa: F811
    sid, _ = _seed(server)
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    for path, _what, content in HELD_PAGES:
        r = h.get(path)
        assert r.status_code == 200 and content in r.text, path
    _spend(server, h)
    for path, what, content in HELD_PAGES:
        r = h.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
        assert content in r.text, path
        strip = re.search(r"<p class='notice warn held' role='status'>(.*?)</p>", r.text, re.S)
        assert strip, (path, "no strip over the held copy")
        assert strip.group(1).startswith(
            f"Too many requests right now, so this is {what} as it was read 1 minute ago."
        ), (path, strip.group(1))
        assert f"<a class='btn' href='{path}'>Try again</a>" in strip.group(1), path
        assert "busy-title" not in r.text and "<h1>Too many requests" not in r.text, path
        assert "retry-after" not in r.headers, path


def test_a_page_never_read_keeps_its_frame_with_the_busy_strip_and_try_again(server: Server) -> None:  # noqa: F811
    """No held copy to show (0.7.46 re-audit D29): the page keeps its own title and frame, with
    the same strip the held pages show (Try again in it) over why, HTTP 429 with Retry-After,
    never a page whose heading is the error."""
    sid, _ = _seed(server)
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    _spend(server, h)
    for path, title in (
        ("/users/alice?order=-viewCount", "alice"),  # the seed read the profile in its usual order only
        ("/search?name=Nothing", "Search decks"),
        ("/users/nobodyhere", "nobodyhere"),
        ("/forum", "Forum"),
    ):
        r = h.get(path)
        assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 600, (path, r.status_code)
        assert f"<title>{title} · " in r.text and f"<h1>{title}</h1>" in r.text, (path, r.text[:400])
        assert "<h1>Too many requests" not in r.text and "busy-title" not in r.text, path
        strip = re.search(r"<p class='notice warn held' role='status'>(.*?)</p>", r.text, re.S)
        assert strip, (path, "no strip")
        assert strip.group(1).startswith("Too many requests right now, so this page can't be read yet."), path
        assert f"<a class='btn' href='{html.escape(path)}'>Try again</a>" in strip.group(1), path
        assert "used up its Archidekt allowance" in r.text, path
