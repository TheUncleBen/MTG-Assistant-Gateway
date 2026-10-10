"""The 2026-10 UI polish pass: tab metadata and icons (manifest short name, maskable and opaque
icons, theme-color per theme, robots), the shell (skip link, "Signed in as", Sign in when
signed out, error pages with the member's navigation, the offline page), wording (plurals,
event sentences, actor badges, US spelling) and the small helpers behind them."""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from mtg_gateway import companion, deckpage, theme
from mtg_gateway.archidekt_csv import parse_export, to_deck_json
from mtg_gateway.pages import actor_html
from mtg_gateway.report_view import report_export_html

from .test_companion import NAV, Stack, linked_browser, stack  # noqa: F401  (fixture)

STATIC = Path(companion.__file__).parent / "static"
SCAN_STATIC = Path(companion.__file__).parent / "scan" / "static"


# -- helpers ----------------------------------------------------------------------------------
def test_plural_and_short_name_and_time() -> None:
    assert theme.plural(1, "card") == "1 card" and theme.plural(0, "card") == "0 cards"
    assert theme.plural(2, "copy", "copies") == "2 copies" and theme.plural(1200, "card") == "1,200 cards"
    assert theme.plural(2.5, "pip") == "2.5 pips"
    assert companion.short_name("MTG Assistant Gateway") == "MTG Gateway"
    assert (
        companion.short_name("My Gateway") == "My Gateway"
        and len(companion.short_name("Abcdefghijklmnop")) <= 12
    )
    assert theme.time_html(0) == "" and theme.time_html("x") == ""
    assert (
        theme.time_html(1_759_000_000) == "<time datetime='2025-09-27T19:06:40Z'>2025-09-27 19:06 UTC</time>"
    )
    assert theme.time_html(1_759_000_000, day=False).startswith(
        "<time datetime='2025-09-27T19:06:40Z' data-fmt=time>"
    )


def test_event_sentences_and_actor_badges() -> None:
    assert companion.event_label("archidekt_linked") == "Archidekt account linked"
    assert companion.event_label("something_new") == "Something new"
    html = companion.event_detail_html(
        {"deck_id": "42", "deck_name": "Reap", "proposal_id": "p0123456789", "via": "x"}
    )
    assert "<a href='/decks/42'>Reap</a>" in html and "proposal p0123456" in html and "via x" in html
    assert "archidekt_username" not in companion.event_detail_html({"archidekt_username": "alice"})
    assert (
        actor_html("app: Claude (c-123)")
        == "<span class='badge' title='app: Claude (c-123)'>app: Claude</span>"
    )
    assert (
        actor_html("browser") == "<span class='badge' title='browser'>browser</span>" and actor_html("") == ""
    )


def test_est_cost_delta_and_csv_types() -> None:
    assert (
        deckpage.est_cost_html({"price_total": 0, "priced_cards": 0})
        == "<span title='No prices from Archidekt yet'>–</span>"
    )
    assert deckpage.est_cost_html({"price_total": 12.5, "priced_cards": 3}) == "$12.50"
    assert (
        deckpage._delta_text("price_total", 310.0) == "$310.00"
        and deckpage._delta_text("price_total", -2) == "-$2.00"
    )
    assert (
        deckpage._delta_text("average_mana_value", 0.3333) == "0.33"
        and deckpage._delta_text("card_count", 3.0) == "3"
    )
    header = (
        "Quantity,Name,Finish,Condition,Date Added,Language,Purchase Price,Tags,Edition Name,Edition Code,"
        "Multiverse Id,Scryfall ID,MTGO ID,Collector Number,Types"
    )
    row = '1,Sol Ring,Normal,NM,,EN,,,Commander Legends,cmr,,,,472,"Artifact,Creature"'
    cards = parse_export(header + "\n" + row + "\n")
    out = to_deck_json(cards, deck_id=1, name="x", owner="o")
    assert out["cards"][0]["card"]["oracleCard"]["types"] == ["Artifact", "Creature"]


def test_icons_are_consistent_and_maskable() -> None:
    rounded = Image.open(STATIC / "gateway-icon-192.png").convert("RGBA")
    assert rounded.getpixel((0, 0))[3] == 0  # the "any" icon keeps its rounded corners
    for name in ("gateway-icon-maskable-192.png", "gateway-icon-maskable-512.png", "gateway-icon-180.png"):
        img = Image.open(STATIC / name).convert("RGBA")
        assert img.getpixel((0, 0)) == (0x1C, 0x1F, 0x26, 255), name  # full bleed, opaque corners
        assert img.size[0] == img.size[1] == int(re.search(r"(\d+)\.png", name).group(1))
    mask = Image.open(STATIC / "gateway-icon-maskable-512.png").convert("RGBA")
    # the art stays inside the central safe zone: the outer 10 % is plain background
    for x, y in ((40, 256), (471, 256), (256, 40), (256, 471)):
        assert mask.getpixel((x, y)) == (0x1C, 0x1F, 0x26, 255), (x, y)
    scan = Image.open(SCAN_STATIC / "icon-192.png").convert("RGBA")
    assert scan.getpixel((0, 0)) == (0x1C, 0x1F, 0x26, 255)  # the scanner shares the gateway's tile
    assert scan.getpixel((30, 30))[:3] == (0xFA, 0x89, 0x0D)  # with its orange viewfinder corners
    assert theme.ICON_DATA_URL.startswith("data:image/svg+xml,%3Csvg")
    assert "<path d='M12" in theme.ICONS["brand"] and "'brand'" not in theme.CSS


def test_report_export_document_has_scheme_icon_and_title() -> None:
    doc = report_export_html({"id": "r1", "deck_name": "Reap", "deck_id": "42", "created_at": 1})
    assert doc.startswith("<!doctype html><html lang='en'>")
    assert "<meta name='color-scheme' content='light dark'>" in doc
    assert (
        f"<link rel='icon' href='{theme.ICON_DATA_URL}'" in doc and "<title>Deck report: Reap</title>" in doc
    )


def test_offline_page_names_the_site_and_follows_the_theme() -> None:
    page = companion.offline_page("Ben's Gateway")
    assert "<title>You&#x27;re offline · Ben&#x27;s Gateway</title>" in page
    assert "<link rel='icon' href='data:image/svg+xml," in page and "<img src='data:image/svg+xml," in page
    assert (
        ":root[data-theme=light]{" in page
        and "@media (prefers-color-scheme: light){:root:not([data-theme=dark])" in page
    )
    assert "<script" not in page


# -- the shell over HTTP ---------------------------------------------------------------------
async def test_manifest_robots_and_app_shell(stack: Stack) -> None:  # noqa: F811
    h = stack.h
    m = (await h.http.get("/app.webmanifest")).json()
    assert m["short_name"] == "MTG Gateway" and m["id"] == "/" and m["description"]
    purposes = {(i["src"], i.get("purpose")) for i in m["icons"]}
    assert ("/static/gateway-icon-maskable-512.png", "maskable") in purposes
    assert ("/static/gateway-icon-512.png", "any") in purposes
    assert all(s["icons"] for s in m["shortcuts"])
    for src in {i["src"] for i in m["icons"]}:
        r = await h.http.get(src)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", src
    robots = await h.http.get("/robots.txt")
    assert robots.status_code == 200 and robots.text == "User-agent: *\nDisallow: /\n"
    assert robots.headers["content-type"].startswith("text/plain")
    sw = (await h.http.get("/sw.js")).text
    assert "'X-MTG-Offline':'1'" in sw and "status:503" in sw and "cookieStore" in sw
    assert "img-src data:" in sw and "You're offline" in sw
    check = await h.http.get("/static/check.svg")
    assert check.status_code == 200 and check.headers["content-type"].startswith("image/svg")


async def test_page_head_and_skip_link(stack: Stack) -> None:  # noqa: F811
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/decks", headers=NAV)).text
        assert "<meta name='robots' content='noindex, nofollow'>" in page
        assert "<meta property='og:site_name' content='MTG Assistant Gateway'>" in page
        assert "<meta name='theme-color' media='(prefers-color-scheme: light)' content='#313131'>" in page
        assert "<meta name='theme-color' media='(prefers-color-scheme: dark)' content='#111111'>" in page
        assert page.index("<a class='skip' href='#main'>Skip to content</a>") < page.index("<header")
        assert "<main class='wrap' id='main' tabindex='-1'>" in page
        assert "Signed in as <b>alice</b>" in page
        assert "<button name='theme' value='system' class=on aria-pressed='true'>" in page
        assert "<button name='theme' value='light' aria-pressed='false'>" in page
        activity = page.split("<a href='/activity'>")[1][:120]
        assert "<path d='M3 12h4l3-7 4 14 3-7h4'/>" in activity  # its own icon, not History's
        assert (
            "<span class='mark'><svg class='i' viewBox='0 0 24 24' aria-hidden='true'><g transform=" in page
        )
        assert "title='Search decks'" in page and "title='Account menu'" in page
        b.http.cookies.set("mtg_theme", "light")
        light = (await b.http.get("/decks", headers=NAV)).text
        assert (
            "<meta name='theme-color' content='#313131'>" in light
            and "media='(prefers-color-scheme" not in light
        )
        b.http.cookies.set("mtg_theme", "dark")
        dark = (await b.http.get("/decks", headers=NAV)).text
        assert "<meta name='theme-color' content='#111111'>" in dark
        # an error page keeps the member's navigation and account menu
        missing = await b.http.get("/no-such-page", headers=NAV)
        assert (
            missing.status_code == 404 and "class='tabbar'" in missing.text and "Signed in as" in missing.text
        )
        # the admin pages name their area in the tab
        assert "<title>Page not found · MTG Assistant Gateway</title>" in missing.text
    finally:
        await b.aclose()


async def test_signed_out_shell_offers_sign_in(stack: Stack) -> None:  # noqa: F811
    page = (await stack.h.http.get("/no-such-page", headers=NAV)).text
    assert "<a class='btn signin' href='/login'>" in page and "Sign in</a>" in page
    assert "<a href='/decks'>Decks</a>" not in page.split("<footer")[1]  # the footer lists public pages only
    assert "<a href='/login'>Sign in</a>" in page.split("<footer")[1]


async def test_nav_highlights_export_and_proposals_and_copy_keeps_its_icon(stack: Stack) -> None:  # noqa: F811
    b = await linked_browser(stack)
    try:
        export = (await b.http.get("/decks/42/export", headers=NAV)).text
        assert "<a href='/decks' aria-current='page'>Decks</a>" in export
        assert (
            "<span class='label'>Copy</span></button><span class='sr-only' id='exp-arch-status' role='status'"
            in export
        )
        assert "aria-label='Archidekt import text'" in export
        proposals = (await b.http.get("/proposals", headers=NAV)).text
        assert "<a href='/proposals' aria-current='page'>Proposals</a>" in proposals
        compare = (await b.http.get("/decks/42/compare", headers=NAV)).text
        assert "<input type='text' name='with' data-suggest='static'" in compare
        search = (await b.http.get("/search", headers=NAV)).text
        assert "name='cC' value='1'" in search and "Colorless" in search and "Color identity" in search
        deck = (await b.http.get("/decks/42", headers=NAV)).text
        assert (
            "<div class='stackhead'><h2>" in deck
            and "<h4>" not in deck.split("id='cards'")[1].split("</div>")[0]
        )
        assert "No prices from Archidekt yet" in deck or "$" in deck
        assert "<span data-plural='k'>card</span>" in deck and "card(s)" not in deck
        assert "/edit#cat-Commander'" in deck or "/edit#cat-" in deck
        assert "distinct cards" in deck or "1 distinct card" in deck
    finally:
        await b.aclose()


async def test_long_folder_filter_and_its_empty_message(stack: Stack) -> None:  # noqa: F811
    b = await linked_browser(stack)
    try:
        name = "Commander decks in progress and testing builds for Friday Night Magic at the shop " + "x" * 12
        assert len(name) > 80
        page = (await b.http.get("/decks", params={"folder": name}, headers=NAV)).text
        assert "No decks yet" not in page and ("No decks in the folder" in page or "data-decks-src" in page)
        listing = await b.http.get("/api/decks/mine", params={"shape": "list", "folder": name})
        assert listing.status_code == 200 and "No decks in the folder" in listing.json()["html"]
        assert "Show every folder" in listing.json()["html"]
    finally:
        await b.aclose()
