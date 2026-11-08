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
from .waits import bell_read

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


RUNNING = [
    {
        "report_id": "rep_a",
        "deck_id": "42",
        "deck_name": "Liesa <b>",
        "progress": "Forge has played 2 of 5 games",
    },
    {"report_id": "rep_b", "deck_id": "7", "deck_name": "Aesi", "progress": "waiting for Forge"},
]


def test_needs_you_shows_only_what_waits() -> None:
    nothing = home.needs_you_html(pending=0, board=[], running=[], watch_hits=[], linked=False, now=NOW)
    assert nothing == ""
    rows = [{"deck_id": "42", "created_at": NOW - 3600, "expires_at": NOW + 7200, "status": "pending"}]
    out = home.needs_you_html(
        pending=1,
        board=rows,
        running=RUNNING,
        watch_hits=["Mana Crypt is below your $150.00"],
        linked=True,
        now=NOW,
    )
    assert "<h2 id='needs-h'>Needs you</h2>" in out
    assert "<li class=accent>" in out and "<b>1 proposal</b> waiting for you" in out
    assert "<span>1 on 1 deck</span><span>oldest 1 h</span><span>next expires in 2 h</span>" in out
    assert "href='/proposals'" in out and ">Review it</a>" in out and out.count("btn-primary") == 1
    two = home.needs_you_html(pending=2, board=rows, running=[], watch_hits=[], linked=True, now=NOW)
    assert "<b>2 proposals</b> waiting for you" in two and ">Review them</a>" in two
    plain = home.needs_you_html(pending=1, board=rows, running=[], watch_hits=[], linked=False, now=NOW)
    assert ">Review it</a>" in plain and "btn-primary" not in plain  # Link Archidekt keeps the orange
    # several tests going: each deck and how far it is, the animated spinner, the Reports list
    assert (
        "<b>2 tests running</b>: Liesa &lt;b&gt;, Forge has played 2 of 5 games; Aesi, waiting for Forge."
        in out
    )
    assert "<span class='ui-spin' aria-hidden='true'></span>" in out and ">Open reports</a>" in out
    assert "Forge test" not in out and "lands under Reports" not in out
    # the alerts row waits for the script's count (hidden until it is above zero)
    assert (
        "<li data-bell-row hidden>" in out
        and "<span data-bell-n>0</span> new <span data-bell-word>alerts</span>" in out
    )
    assert "<b>1 watched card</b> past a price alert" in out and "Mana Crypt is below your $150.00" in out
    # not linked: no alerts row (the bell reads Archidekt)
    one = home.needs_you_html(pending=0, board=[], running=RUNNING[:1], watch_hits=[], linked=False, now=NOW)
    assert "data-bell-row" not in one
    # one test: its own words and the way to that report, not the list
    assert "<b>1 test running</b>: Forge has played 2 of 5 games." in one and "ui-spin" in one
    assert "href='/history/reports/rep_a'" in one and ">Open the report</a>" in one and "/reports'" not in one
    assert "data-bell-row" not in home.needs_you_html(
        pending=0, board=[], running=RUNNING[:1], watch_hits=[], linked=False, now=NOW
    )
    # with something else waiting, the section shows from the start
    assert "<section class='needs' aria-labelledby='needs-h'>" in out


def test_no_needs_you_heading_when_nothing_waits() -> None:
    """Review F8: a linked member with nothing waiting got a "Needs you" heading over nothing (the
    alerts row is hidden until bell.js counts above zero). With only that row, the whole section
    starts hidden and bell.js shows it with the row."""
    only_bell = home.needs_you_html(pending=0, board=[], running=[], watch_hits=[], linked=True, now=NOW)
    assert only_bell.startswith("<section class='needs' aria-labelledby='needs-h' data-bell-section hidden>")
    assert "<li data-bell-row hidden>" in only_bell
    js = (Path(home.__file__).parent / "static" / "bell.js").read_text(encoding="utf-8")
    assert "[data-bell-section]" in js


def _answer(kind: str, body: str):
    return lambda route: route.fulfill(content_type=kind, body=body)


def test_the_needs_you_section_follows_the_alert_count_in_a_browser() -> None:
    """The same in Chromium: no count, no heading; a count above zero shows the heading and the row."""
    from playwright.sync_api import sync_playwright

    from mtg_gateway import theme

    from .test_scan_browser import _chromium_path

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    section = home.needs_you_html(pending=0, board=[], running=[], watch_hits=[], linked=True, now=NOW)
    bell_js = (Path(home.__file__).parent / "static" / "bell.js").read_text(encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            for n, shown in ((0, False), (3, True)):
                page = browser.new_page()
                body = (
                    f"<!doctype html><style>{theme.CSS}</style><a data-bell href='/notifications'>"
                    f"<span class='n' hidden></span></a><main class='home'>{section}</main>"
                    f"<script>{bell_js}</script>"
                )
                page.route(
                    "https://mtg.test/notifications/count",
                    _answer("application/json", f'{{"ok": true, "unseen": {n}}}'),
                )
                page.route("https://mtg.test/", _answer("text/html", body))
                page.goto("https://mtg.test/")
                bell_read(page)
                assert page.locator("#needs-h").is_visible() is shown, n
                assert page.locator("[data-bell-row]").is_visible() is shown, n
                page.close()
        finally:
            browser.close()


def test_home_is_recent_decks_and_needs_you_not_the_navigation_again(server: Server) -> None:
    page = _client(server).get("/").text
    assert "Quick access" not in page and "Find a deck" not in page and "Search Archidekt" not in page
    assert "In the same order as the navigation" not in page
    # not linked: Link Archidekt is the one orange button, no deck panel, nothing needs them yet
    assert page.count("btn-primary") == 1 and "Link Archidekt</a>" in page
    assert "<h2>My decks</h2>" not in page and "id='needs-h'" not in page
    # no assistant connected: the connection details are offered; the row says what it is for
    # and the connector address sits inside with Copy, not as the row's text (0.7.46 audit QG-20)
    assert "<details class='connect panel'>" in page and "Guided setup" in page
    assert "<summary>Connect an AI assistant <span class='muted addr'>Claude or ChatGPT" in page
    assert f"<code>{server.base}/mcp</code>" in page and f"data-copy-text='{server.base}/mcp'" in page
    assert f"<span class='muted addr'>{server.base}/mcp" not in page
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


@pytest.mark.parametrize("ua", ["", " MTGAssistant/0.7.46"])
def test_the_brand_mark_is_a_whole_touch_target_on_a_phone(server: Server, ua: str) -> None:
    """Review F12: on a phone the top bar's brand is its 25 px mark alone (the word is hidden), so
    the link that goes Home was 25 px wide; on a touch screen it is at least 44 px each way."""
    from playwright.sync_api import sync_playwright

    from .test_scan_browser import _chromium_path

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            agent = "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36" + ua
            ctx = browser.new_context(
                viewport={"width": 375, "height": 800}, has_touch=True, is_mobile=True, user_agent=agent
            )
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            page.goto(f"{server.base}/", wait_until="networkidle")
            box = page.locator(".topbar .brand").bounding_box()
            assert box and box["width"] >= 44 and box["height"] >= 44, box
            wide = page.evaluate("() => document.documentElement.scrollWidth > innerWidth")
            assert not wide
        finally:
            browser.close()
