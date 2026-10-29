"""Decision C8 (0.7.46), Home: recent decks and what needs you, not a second copy of the
navigation. Needs you lists proposals waiting, Forge tests running, Archidekt alerts (filled
by static/bell.js) and watched cards past a price alert, each only when there is one; the
Find a deck form and the Quick access tiles are gone; Connect an AI assistant shows only while
no assistant is connected; the site search suggests the member's own decks."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mtg_gateway import home

from .test_suggest_browser import Server

NOW = 1_800_000_000.0


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _client(server: Server) -> httpx.Client:
    return httpx.Client(base_url=server.base, cookies={"mtg_session": server.sign_in()})


def test_needs_you_shows_only_what_waits() -> None:
    nothing = home.needs_you_html(pending=0, board=[], forge_running=0, watch_hits=[], linked=False, now=NOW)
    assert nothing == ""
    rows = [{"deck_id": "42", "created_at": NOW - 3600, "expires_at": NOW + 7200, "status": "pending"}]
    out = home.needs_you_html(
        pending=1,
        board=rows,
        forge_running=2,
        watch_hits=["Mana Crypt is below your $150.00"],
        linked=True,
        now=NOW,
    )
    assert "<h2 id='needs-h'>Needs you</h2>" in out
    assert "<li class=accent>" in out and "<b>1 proposal</b> waiting for you" in out
    assert "1 on 1 deck · oldest 1 h · next expires in 2 h" in out
    assert "href='/proposals'" in out and ">Review it</a>" in out and out.count("btn-primary") == 1
    two = home.needs_you_html(pending=2, board=rows, forge_running=0, watch_hits=[], linked=True, now=NOW)
    assert "<b>2 proposals</b> waiting for you" in two and ">Review them</a>" in two
    plain = home.needs_you_html(pending=1, board=rows, forge_running=0, watch_hits=[], linked=False, now=NOW)
    assert ">Review it</a>" in plain and "btn-primary" not in plain  # Link Archidekt keeps the orange
    assert "<b>2 Forge tests</b> running" in out and ">Open reports</a>" in out
    # the alerts row waits for the script's count (hidden until it is above zero)
    assert (
        "<li data-bell-row hidden>" in out
        and "<span data-bell-n>0</span> new <span data-bell-word>alerts</span>" in out
    )
    assert "<b>1 watched card</b> past a price alert" in out and "Mana Crypt is below your $150.00" in out
    # not linked: no alerts row (the bell reads Archidekt)
    assert "data-bell-row" not in home.needs_you_html(
        pending=0, board=[], forge_running=1, watch_hits=[], linked=False, now=NOW
    )


def test_home_is_recent_decks_and_needs_you_not_the_navigation_again(server: Server) -> None:
    page = _client(server).get("/").text
    assert "Quick access" not in page and "Find a deck" not in page and "Search Archidekt" not in page
    assert "In the same order as the navigation" not in page
    # not linked: Link Archidekt is the one orange button, no deck panel, nothing needs them yet
    assert page.count("btn-primary") == 1 and "Link Archidekt</a>" in page
    assert "<h2>My decks</h2>" not in page and "id='needs-h'" not in page
    # no assistant connected: the connection details are offered
    assert "<details class='connect panel'>" in page and "Guided setup" in page
    assert "<script src='/static/bell.js" in page


def test_the_connection_panel_leaves_once_an_assistant_is_connected(server: Server) -> None:
    c = _client(server)
    server.db.save_client("c1", {"client_name": "Claude"})
    server.db.save_token(
        "t1",
        kind="access",
        client_id="c1",
        sub="user-1",
        scopes=["mtg"],
        resource=None,
        family="f1",
        expires_at=2**31,
    )
    page = c.get("/").text
    assert "<details class='connect panel'>" not in page and "Guided setup" not in page


def test_proposals_waiting_take_the_orange_button(server: Server) -> None:
    import time

    c = _client(server)
    server.db.save_proposal(
        {
            "id": "p1",
            "owner_sub": "user-1",
            "deck_id": "42",
            "deck_name": "Meren",
            "baseline_fingerprint": "f",
            "changes": [],
            "diff_text": "",
            "expires_at": int(time.time()) + 3600,
        }
    )
    page = c.get("/").text
    assert "id='needs-h'" in page and "<b>1 proposal</b> waiting for you" in page
    # not linked: Link Archidekt keeps the orange; Review it is plain beside it
    assert ">Review it</a>" in page and page.count("btn-primary") == 1 and "Link Archidekt</a>" in page


def test_the_deck_names_api_answers_the_held_list_only(server: Server) -> None:
    anon = httpx.get(f"{server.base}/api/decks/names")
    assert anon.status_code == 401
    c = _client(server)
    r = c.get("/api/decks/names")
    assert r.status_code == 200 and r.json() == {"ok": True, "decks": []}  # not linked: nothing
    assert r.headers["cache-control"] == "no-store"
    js = c.get("/static/suggest.js").text
    assert '"/api/decks/names"' in js and 'head("Your decks")' in js
