"""The navigation's second round (Ben, 2026-10-25): four places (Home, Decks, Binder, Assistant)
and More in two groups; the picture opens a settings-style Account page (account_hub.py) whose
first row is Account details (/account/details); a switch at the top of Decks (My Decks or
Packages) and of Assistant (Proposals, History or Reports); the deck page's Test and play, its
grouped More and its Proposals pill."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from mtg_gateway import account_hub, theme
from mtg_gateway.deckpage import banner_html

from .test_deck_stats import card, deck
from .test_suggest_browser import Server

NAV = {"Accept": "text/html"}
APP_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 16) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.7.45"}


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, admin_group="admins")
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _client(server: Server, *, admin: bool = False) -> httpx.Client:
    sid = server.sign_in()
    if admin:
        server.db.upsert_user(
            "user-1", email="alice@example.test", name="Alice", preferred_username="alice", groups=["admins"]
        )
    return httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, headers=NAV)


# -- the bars --------------------------------------------------------------------------------------


def test_the_places_are_four_and_more_holds_two_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(theme, "WATCHLISTS", True)
    assert [label for _h, label, _i in theme.NAV_LINKS] == ["Home", "Decks", "Binder", "Assistant"]
    groups = theme.more_groups()
    assert [g for g, _links in groups] == ["Find", "Your cards"]
    hrefs = [h for _g, links in groups for h, _l, _i in links]
    assert hrefs == ["/search", "/precons", "/scan", "/watchlist", "/lists"]


def test_watchlist_leaves_more_when_watchlists_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(theme, "WATCHLISTS", False)
    hrefs = [h for _g, links in theme.more_groups() for h, _l, _i in links]
    assert "/watchlist" not in hrefs and "/lists" in hrefs


def test_the_switch_marks_its_page_and_counts_on_its_first_part() -> None:
    out = theme.hub_switch(
        "Assistant", [("/proposals", "Proposals", "a"), ("/history", "History", "b")], "b", badge=3
    )
    assert out.startswith("<nav class='hubseg' aria-label='Assistant'>")
    assert "<a href='/history' aria-current=page>History</a>" in out
    assert "<a href='/proposals'>Proposals<span class='nb'>3</span></a>" in out
    assert "99+" in theme.hub_switch("A", [("/p", "P", "")], "", badge=120)


def test_every_signed_in_page_carries_its_form_token_first(server: Server) -> None:
    """The scripts read the page's first csrf input (cardmenu.js and the rest): the header has it,
    so a page without a form of its own still lets a menu act."""
    h = _client(server)
    for path in ("/", "/proposals", "/history", "/guide", "/notifications"):
        page = h.get(path).text
        first = re.search(r"<input type='hidden' name='csrf' value='([0-9a-f]+)'>", page)
        assert first and page.index(first.group(0)) < page.index("<main"), path


def test_history_and_reports_mark_assistant_as_the_place(server: Server) -> None:
    h = _client(server)
    for path in ("/proposals", "/history", "/reports"):
        page = h.get(path).text
        assert "<a href='/proposals' aria-current='page'>Assistant" in page, path
        assert re.search(
            rf"<nav class='hubseg' aria-label='Assistant'>.*<a href='{path}' aria-current=page>", page
        )


def test_the_history_purpose_line_links_to_proposals(server: Server) -> None:
    page = _client(server).get("/history").text
    assert "proposed are on <a href='/proposals'>Proposals</a>.</p>" in page
    assert "&lt;a href" not in page


def test_a_long_word_scrolls_the_switch_not_the_page() -> None:
    assert ".hubseg{display:flex;width:100%;overflow-x:auto;scrollbar-width:none}" in theme.CSS
    js = (Path(theme.__file__).parent / "static" / "topbar.js").read_text(encoding="utf-8")
    assert 'seg.querySelector("[aria-current=page]")' in js


def test_the_assistant_place_counts_what_waits(server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(theme, "WAITING_COUNT", lambda sub: 4)
    page = _client(server).get("/").text
    top = page.split("<nav class='site'")[1].split("</nav>")[0]
    assert (
        "<a href='/proposals'>Assistant<span class='nb' aria-hidden='true'>4</span>"
        "<span class='sr-only'>, 4 waiting</span></a>" in top
    )


def test_a_closed_drop_down_takes_no_room() -> None:
    """A closed details' panel is still laid out by Chromium: the phone's fixed More sheet off to
    the side widened the page (the Reports filters' sheets then opened under their scrim)."""
    assert "details.dd:not([open]) > .menu{display:none}" in theme.CSS


# -- the Account page ------------------------------------------------------------------------------


def test_balance_centres_the_smallest_of_an_odd_number_and_keeps_your_account_top_left() -> None:
    rows = lambda n: [account_hub._row(f"r{i}", "x", "/") for i in range(n)]  # noqa: E731
    # a member on the website: Guide, Assistant skill and Android app beside Your account
    gs = [(0, "Your account", rows(3)), (1, "Look", rows(2)), (2, "Assistants and apps", rows(3))]
    cols, lone = account_hub.balance(gs)
    assert [g[1] for g in lone] == ["Look"]
    assert cols[0][0][1] == "Your account" and [g[1] for g in cols[1]] == ["Assistants and apps"]
    gs.append((3, "This app", rows(5)))
    cols, lone = account_hub.balance(gs)
    assert lone == [] and cols[0][0][1] == "Your account"
    heights = [sum(len(g[2]) + 1 for g in c) for c in cols]
    assert abs(heights[0] - heights[1]) <= 3, heights


def test_the_account_page_is_a_settings_list_and_details_is_one_row_in(server: Server) -> None:
    h = _client(server)
    hub = h.get("/account").text
    assert "<title>Account · " in hub and "<div class='yhub'>" in hub
    for name in ("Your account", "Look", "Assistants and apps"):
        assert f"<h2>{name}</h2>" in hub, name
    for href in (
        "/account/details",
        "/activity",
        "/forum",
        "/guide",
        "/install?for=skill",
        "/install?for=android",
    ):
        assert f"<a class='yrow' href='{href}'>" in hub, href
    assert "href='/admin'" not in hub and "<h2>This app</h2>" not in hub
    assert "<form method='post' action='/theme'>" in hub and "<form method='post' action='/layout'>" in hub
    # Sign out: one real button, centred at the foot
    assert re.search(
        r"<form class='ysignout' method='post' action='/logout'>.*Sign out</span></button></form></div>$",
        hub.split("</main>")[0],
    )
    details = h.get("/account/details").text
    assert "<title>Account details · " in details
    assert "<a class='yback' href='/account'>" in details and "<h1>Account details</h1>" in details
    assert "Sign out on all my devices" in details and ">Sign out</button>" not in details


def test_old_account_links_land_on_details(server: Server) -> None:
    h = _client(server)
    page = h.get("/account?ok=linked").text
    assert "<h1>Account details</h1>" in page
    # a #section is moved by pages.js (the server never sees it)
    assert 'location.replace("/account/details"' in h.get("/static/pages.js").text


def test_admins_and_the_app_get_their_own_rows(server: Server) -> None:
    hub = _client(server, admin=True).get("/account").text
    assert "<a class='yrow' href='/admin'>" in hub
    app = _client(server).get("/account", headers=APP_UA).text
    assert "<h2>This app</h2>" in app and "href='/install?for=android'" not in app.split("<main")[1]
    for action in ("openCamera", "reload", "openCopiedLink", "changeGateway", "checkForUpdates"):
        assert f"data-native='{action}'" in app, action
    assert "openInBrowser" not in app


# -- Decks -----------------------------------------------------------------------------------------


def test_the_decks_switch_keeps_the_folder_open() -> None:
    from mtg_gateway import companion_decks

    src = Path(companion_decks.__file__).read_text(encoding="utf-8")
    assert 'in_folder = f"folder={quote(folder, safe=\'\')}" if folder else ""' in src


# -- the deck page ---------------------------------------------------------------------------------


def test_test_and_play_holds_the_runs_and_the_other_sites() -> None:
    html = banner_html(deck([card("Opt")]), {}, own=True, csrf="t", writes_enabled=True)
    test = html.split("<details class='dd decktest'>")[1].split("</details>")[0]
    assert "Test and play</summary>" in html
    assert " Goldfish</a>" in test and "id='fullf'" in test and "Run full test</button>" in test
    assert "Playtest on Archidekt</a>" in test and "data-edhplay" in test
    # the banner's buttons are not repeated in the no-script More
    more = html.split("<details class='dd deckmore'>")[1].split("</details>")[0]
    assert "Goldfish" not in more and "Edit deck" not in more and "Open in a new tab" not in more


def test_the_proposals_pill_counts_the_decks_waiting_changes() -> None:
    from mtg_gateway.deckpage import social_html

    d = deck([card("Opt")])
    none = social_html(d, own=True, writes_enabled=True, waiting=0)
    assert "<a class='soc prop' href='/proposals?deck_id=" in none and "class='nb'" not in none
    two = social_html(d, own=True, writes_enabled=True, waiting=2)
    assert "<b class='nb'>2</b><span class='sr-only'> waiting</span>" in two


def test_the_deck_menus_groups_in_order() -> None:
    js = (Path(theme.__file__).parent / "static" / "deck.js").read_text(encoding="utf-8")
    body = js.split("function items() {")[1].split("\n  }\n")[0]
    heads = re.findall(r'head\("([^"]+)"\)', body)
    assert heads == ["This deck", "Look into it", "Share and print", "Community"]
    for gone in ("Open in a new tab", "Copy link", "Proposals for this deck", '"Goldfish"', "Clone"):
        assert gone not in body, gone
    assert 'item("Share…", "send", share)' in body and 'link("Deck reports"' in body


def test_open_in_a_new_tab_is_for_the_website_only() -> None:
    static = Path(theme.__file__).parent / "static"
    menus = (static / "menus.js").read_text(encoding="utf-8")
    assert "function inApp() { return !!window.MtgNative; }" in menus
    assert menus.count('"Open in a new tab"') == menus.count(
        'inApp() ? null : link("Open in a new tab"'
    ) + menus.count('if (!inApp()) items.push(link("Open in a new tab"')
    forum = (static / "forum.js").read_text(encoding="utf-8")
    assert 'window.MtgNative ? null : Menu.link("Open in a new tab"' in forum


def test_the_top_bar_names_the_member_by_a_hash_for_the_owed_reject(server: Server) -> None:
    """static/cardmenu.js keeps the deck page's owed reject under the member who is signed in
    (security review 0.7.44 S-03): the old account menu's "Signed in as" line named them."""
    import hashlib

    page = _client(server).get("/").text
    want = hashlib.sha256(b"user-1").hexdigest()[:16]
    link = "<a class='icon-btn acct' href='/account' aria-label='Account' title='Account'"
    assert f"{link} data-who='{want}'>" in page
    js = (Path(theme.__file__).parent / "static" / "cardmenu.js").read_text(encoding="utf-8")
    assert 'document.querySelector("header.topbar [data-who]")' in js


def test_the_header_token_is_the_sessions_own(server: Server) -> None:
    """The header's token (masked afresh on every page, so it differs from a form's) is accepted
    as this session's: a script reading it can post."""
    h = _client(server)
    token = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/").text).group(1)
    r = h.post("/theme", data={"csrf": token, "theme": "dark", "next": "/decks"})
    assert r.status_code == 303 and r.headers["location"] == "/decks"
