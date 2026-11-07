"""While Archidekt can't be asked (the member's allowance used up, Archidekt's pause, an outage)
the Binder, My lists, a profile and a search a member has already read show that last read,
dated and with Try again, instead of the whole-page "Too many requests" (0.7.46 audit): the
same strip the deck page and My decks use. A page never read before still gets the busy page
(test_companion). On the overflow sweep's seeded site (test_overflow_sweep_browser)."""

from __future__ import annotations

import html
import re
import time

import httpx
import pytest

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


def _binder_row(text: str, name: str) -> str:
    """The Binder list's row (or tile) of ``name``, as the page drew it."""
    at = text.index(f">{name}<")
    start = max(text.rfind("<tr", 0, at), text.rfind("<li", 0, at))
    end = min(i for i in (text.find("</tr>", at), text.find("</li>", at)) if i > 0)
    return text[start:end]


def test_the_binders_held_copy_shows_what_the_member_just_saved_through_the_gateway(server: Server) -> None:  # noqa: F811
    """0.7.46 polish A1: a stepper press saved 2 copies (1 before), then Archidekt stopped answering. The
    Binder's held copy (read before the press) shows the 2 the gateway wrote, and says so; a row
    the member removed meanwhile is not shown as still there."""
    sid, _ = _seed(server)
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    page = h.get("/collection?view=list")
    assert page.status_code == 200
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page.text).group(1)
    recs = {r["card"]["oracleCard"]["name"]: r for r in server.ark.collections["alice"].values()}
    signet, ring = recs["Arcane Signet"], recs["Sol Ring"]
    assert signet["quantity"] == 1
    r = h.post(
        f"/collection/api/rows/{signet['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"delta": 1, "name": "Arcane Signet"},
    )
    assert r.status_code == 200 and r.json()["row"]["quantity"] == 2, r.text
    r = h.delete(f"/collection/api/rows/{ring['id']}?name=Sol+Ring", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    # the first page the removal's Undo read afterwards has aged out, and Archidekt is down
    server.app.state.gateway.collection.first_page_ttl = 0
    server.app.state.gateway.decks.client.pacer._open_until = time.monotonic() + 120
    try:
        held = h.get("/collection?view=list")
    finally:
        server.app.state.gateway.decks.client.pacer._open_until = 0
    assert held.status_code == 200, held.text[:300]
    strip = re.search(r"<p class='notice warn held' role='status'>(.*?)</p>", held.text, re.S)
    assert strip and "as it was read" in strip.group(1), held.text[:300]
    row = _binder_row(held.text, "Arcane Signet")
    assert re.findall(r"<output aria-label='Copies'>(\d+)</output>", row) == ["2"], row
    assert ">Sol Ring<" not in held.text
    assert "Cards you changed here since show what you saved." in strip.group(1), strip.group(1)


@pytest.mark.parametrize("down", ["busy", "unreachable"])
def test_the_json_reads_a_box_or_a_switch_makes_answer_busy_and_unreachable_as_the_pages_do(
    server: Server,  # noqa: F811
    down: str,
) -> None:
    """0.7.46 polish A4: the JSON addresses a page's script reads (a box's templates, color tags
    and packages, a list's Load more) and the Account switches answer Archidekt busy with 429 and
    Archidekt unreachable with 503, with Retry-After and the words the pages use, never a 400."""
    sid, _ = _seed(server)
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/account").text).group(1)
    json_ = {"Accept": "application/json"}
    pacer = server.app.state.gateway.decks.client.pacer
    if down == "busy":
        _spend(server, h)
    else:
        server.app.state.gateway.decks.short_reads.drop("user-1")
        pacer._open_until = time.monotonic() + 120
    want = 429 if down == "busy" else 503
    try:
        answers = {
            path: h.get(path, headers=json_)
            for path in (
                "/decks/api/templates",
                "/decks/api/color_tags",
                "/decks/api/packages?whose=mine",
                "/lists/custom?page=7",
            )
        }
        answers["/account/profile"] = h.post(
            "/account/profile",
            headers=json_,
            data={"csrf": csrf, "action": "email", "switch": "news", "on": "1"},
        )
    finally:
        pacer._open_until = 0
    assert {p: r.status_code for p, r in answers.items()} == dict.fromkeys(answers, want)
    for path, r in answers.items():
        assert r.status_code == want, (path, r.status_code, r.text[:300])
        assert 1 <= int(r.headers["retry-after"]) <= 3600, path
        body = r.json()
        assert body["ok"] is False and body["error"] == ("rate_limited" if down == "busy" else "unavailable")
        assert body["message"], path
