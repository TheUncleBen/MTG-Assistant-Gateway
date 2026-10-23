"""The second UI pass over the Forum, the Deck page's custom cards, My lists and the Add a package
box, in headless Chromium: one list date on every forum post and reply (the first post's and
the replies' dates are the same ``<time data-fmt=rel>``); a reply's points as the footer's ▲
button, with Vote up (and Take back your vote) in its ⋮ and no Down button; a ⋮ menu that stays
inside its column; the deck banner's date at 4.5:1; My lists' tabs on one row on a phone, the
current tab in view; the card viewer of a custom card filled with what the card has, and a
failed picture shown as the card's name and type line (in the stacks and in the viewer); the
Color tag select's sheet never covering its own field. Skipped without a Chromium build."""

from __future__ import annotations

import re

import httpx
import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_deck_custom_cards import _custom
from .test_deck_extras_browser import _open_from_deck_menu
from .test_editor_browser import _link
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server
from .test_ui_pass_c_browser import _close_menu, _items, _seed, _targets

SIZES = [(344, "dark"), (390, "light"), (1280, "dark"), (1280, "light")]
# the one list date (ui.when_text / MtgText.rel): "just now", "40 min ago", "2 h ago", "3 days ago",
# then "Oct 12" (with the year when it isn't this year)
LIST_DATE = re.compile(r"^(just now|\d+ min ago|\d+ h ago|\d+ days? ago|[A-Z][a-z]{2} \d{1,2}(, \d{4})?)$")


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path, writes_enabled=True)
    _seed(s.ark)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _pictures(server: Server, monkeypatch) -> None:
    """Grizzled Mentor (41) has a picture; Old Ranger (44) is in the deck and its picture fails."""
    from mtg_gateway import custom_pictures

    from .test_custom_pictures import _png

    custom_pictures._KEPT.clear()
    monkeypatch.setattr(custom_pictures, "_public_host", lambda host: host == "cdn.example")
    picture = _png()

    def handler(request):
        if str(request.url) == "https://cdn.example/41.png":
            return httpx.Response(200, content=picture)
        return httpx.Response(404)

    gw = server.app.state.gateway
    gw.custom_picture_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server.ark.decks[42]["customCards"].append(
        {"id": 3101, "quantity": 1, "categories": ["Ramp"], "card": _custom(44, "Old Ranger")}
    )
    gw.decks.client._cache.clear()


def _banner_contrast(page) -> float:
    """The least contrast of the banner's grey row text against the banner's own background,
    measured from pixels: the shade is a gradient over the art, so a computed background would
    be wrong. In the rows' own box each column's commonest colour is the background there (the
    text is sparse); the least ratio over the columns is the answer."""
    import io

    from PIL import Image

    def lum(c):
        v = []
        for x in c[:3]:
            x /= 255
            v.append(x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4)
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]

    text = page.evaluate("() => getComputedStyle(document.querySelector('.banner .row time')).color")
    text = tuple(int(x) for x in re.findall(r"\d+", text)[:3])
    rows = page.locator(".banner .row")
    boxes = [_rect(rows.nth(i)) for i in range(rows.count())]
    top, bottom = min(b["t"] for b in boxes) - 2, max(b["b"] for b in boxes) + 2
    left, right = min(b["l"] for b in boxes), max(b["r"] for b in boxes)
    shot = page.screenshot(clip={"x": left, "y": top, "width": right - left, "height": bottom - top})
    im = Image.open(io.BytesIO(shot)).convert("RGB")
    w, h = im.size
    lt, worst = lum(text), 99.0
    for x in range(w):
        counts: dict[tuple, int] = {}
        for y in range(h):
            c = im.getpixel((x, y))
            counts[c] = counts.get(c, 0) + 1
        bg = max(counts, key=counts.get)
        lb = lum(bg)
        worst = min(worst, (max(lt, lb) + 0.05) / (min(lt, lb) + 0.05))
    return worst


def _rect(loc) -> dict:
    return loc.evaluate(
        "e => { const r = e.getBoundingClientRect();"
        " return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height}; }"
    )


@pytest.mark.parametrize("width,theme", SIZES)
def test_forum_dates_votes_and_the_menu_in_its_column(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/forum/thread/700002", wait_until="networkidle")
        page.locator(".comments .cmt .who .kebab").first.wait_for(state="visible")
        # one date format: the first post's date and every reply's are the same list date
        first = page.locator(".fm-post .ph time.ui-when[data-fmt=rel]")
        assert first.count() == 1 and first.get_attribute("title")
        assert LIST_DATE.match(first.inner_text()), first.inner_text()
        whos = page.locator(".comments .cmt > .who")
        assert whos.count() == 2
        for i in range(whos.count()):
            t = whos.nth(i).locator("time.ui-when[data-fmt=rel]")
            assert t.count() == 1, whos.nth(i).inner_text()
            assert LIST_DATE.match(t.inner_text()), t.inner_text()
            assert t.get_attribute("title") and "points" not in whos.nth(i).inner_text()
        dates = ".fm-post .ph time, .comments .cmt .who time, .comments .cmt .acts .pts"
        assert page.evaluate(CONTRAST, dates) == []
        # the footer: a ▲ button with the points on someone else's reply, plain points on your own;
        # no Down, no inline Edit or Delete (those are in the ⋮)
        amy = page.locator(".comments .cmt[data-owner='amy']").first
        vote = amy.locator("> .acts button.vote")
        assert vote.count() == 1 and vote.locator(".n").inner_text() == "0"
        assert vote.get_attribute("aria-label") == "0 points. Vote up: comment by amy"
        assert amy.locator("> .acts").inner_text().count("Down") == 0
        assert [b.strip() for b in amy.locator("> .acts button").all_inner_texts()] == ["0", "Reply"]
        mine = page.locator(".comments .cmt[data-owner='alice']").first
        assert mine.locator("> .acts button.vote").count() == 0
        assert mine.locator("> .acts .pts").inner_text() == "0 points"
        assert [b.strip() for b in mine.locator("> .acts button").all_inner_texts()] == ["Reply"]
        if width < 900:
            sizes = _targets(page, ".comments .cmt .acts button")
            assert sizes and min(sizes) >= 43.5, sizes
        # the ⋮: Vote up on another's, Edit… and Delete… on your own
        amy.locator("> .who .kebab").click()
        page.locator(".ctxmenu").wait_for(state="visible")
        assert _items(page) == ["Reply", "Copy link", "Vote up", "Report comment…"]
        # the menu keeps inside the panel's column and the window
        menu, panel = _rect(page.locator(".ctxmenu")), _rect(page.locator("#comments"))
        vw = page.evaluate("() => window.innerWidth")
        assert menu["r"] <= vw and menu["l"] >= 0, (menu, vw)
        if width >= 900:
            assert menu["r"] <= panel["r"] + 1 and menu["l"] >= panel["l"] - 1, (menu, panel)
        _close_menu(page)
        mine.scroll_into_view_if_needed()
        mine.locator("> .who .kebab").click()
        page.locator(".ctxmenu").wait_for(state="visible")
        assert _items(page) == ["Reply", "Copy link", "Edit…", "Delete…"]
        _close_menu(page)
        # a vote from the footer: asked first, then the count and the menu's words change
        vote.scroll_into_view_if_needed()
        vote.click()
        page.locator(".cmt .confirm button", has_text="Vote").click()
        page.wait_for_function(
            "() => document.querySelector(\".cmt[data-owner='amy'] > .acts button.vote .n\")"
            ".textContent === '1'"
        )
        assert vote.get_attribute("aria-pressed") == "true"
        amy.locator("> .who .kebab").click()
        page.locator(".ctxmenu").wait_for(state="visible")
        assert _items(page) == ["Reply", "Copy link", "Take back your vote", "Report comment…"]
        _close_menu(page)
        assert page.evaluate(MEASURE) == []
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_deck_pages_custom_card_viewer_and_a_failed_picture(
    server: Server, width: int, theme: str, monkeypatch
) -> None:
    from playwright.sync_api import sync_playwright

    _pictures(server, monkeypatch)
    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/42?view=stacks", wait_until="networkidle")
        # the banner's date reads at 4.5:1 in both themes
        when = page.locator(".banner .row time.ui-when")
        assert when.count() == 1 and when.inner_text()
        # its colour is the row's own (the list date's muted grey was faint on the banner), and
        # that reads at 4.5:1 over the whole shade
        assert when.evaluate(
            "e => getComputedStyle(e).color === getComputedStyle(e.parentNode.parentNode).color"
        )
        assert _banner_contrast(page) >= 4.5
        # the card whose picture failed: its name and type line in the stack, no broken picture
        ranger = page.locator(".deckview .c.custom[data-custom-id='44']")
        ranger.scroll_into_view_if_needed()
        page.wait_for_function("() => !document.querySelector(\".c.custom[data-custom-id='44'] img\")")
        assert ranger.locator(".ph .nm").inner_text() == "Old Ranger"
        assert ranger.locator(".ph .ty").inner_text() == "Creature — Human Advisor"
        assert ranger.locator(".ph .nm").is_visible() and ranger.locator(".ph .ty").is_visible()
        tile_text = ".c.custom[data-custom-id='44'] .ph .nm, .c.custom[data-custom-id='44'] .ph .ty"
        assert page.evaluate(CONTRAST, tile_text) == []
        # the viewer of that card: the name and the type line where the picture would be, the
        # Custom mark, and what the deck knows of it; no rulings, prices or card links
        ranger.focus()
        page.keyboard.press("Enter")
        view = page.locator(".cardview.open")
        view.wait_for(state="visible")
        page.wait_for_timeout(300)
        tile = view.locator(".pane .ph")
        assert tile.locator(".nm").inner_text() == "Old Ranger"
        assert tile.locator(".ty").inner_text() == "Creature — Human Advisor"
        assert view.locator(".pane img:not(.img-broken)").count() == 0
        assert view.locator("h3 .custom-badge").inner_text().lower() == "custom"
        facts = view.locator(".facts").inner_text()
        assert "In this deck" in facts and "1 copy · Ramp" in facts
        assert "Printing" in facts and "CST #001" in facts
        assert "Custom card, made on Archidekt" in facts
        assert view.locator(".more").count() == 0  # no Rulings, EDHREC or Price history
        assert view.locator(".acts button", has_text="Remove from deck").count() == 1
        assert page.evaluate(MEASURE) == []
        view_text = ".cardview .ph .nm, .cardview .ph .ty, .cardview .facts dt, .cardview .facts dd"
        assert page.evaluate(CONTRAST, view_text) == []
        page.keyboard.press("Escape")
        view.wait_for(state="hidden")
        # the card with a picture: the viewer shows it (and the same facts)
        mentor = page.locator(".deckview .c.custom[data-custom-id='41']")
        mentor.scroll_into_view_if_needed()
        page.wait_for_function(
            "() => { const i = document.querySelector(\".c.custom[data-custom-id='41'] img.pic\");"
            " return i && i.complete && i.naturalWidth > 0; }"
        )
        mentor.focus()
        page.keyboard.press("Enter")
        view.wait_for(state="visible")
        page.wait_for_function(
            "() => { const i = document.querySelector('.cardview.open .pane img');"
            " return i && i.complete && i.naturalWidth > 0; }"
        )
        assert "In this deck" in view.locator(".facts").inner_text()
        page.keyboard.press("Escape")
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", [(344, "light"), (390, "dark"), (1280, "light")])
def test_my_lists_tabs_sit_on_one_row(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/lists/custom", wait_until="networkidle")
        page.wait_for_timeout(300)
        tabs = page.locator(".lp-tabs a")
        assert tabs.count() == 5
        tops = [_rect(tabs.nth(i))["t"] for i in range(5)]
        heights = [_rect(tabs.nth(i))["h"] for i in range(5)]
        if width < 600:
            # one row that scrolls sideways, every tab the same height, the current one in view
            assert len(set(round(t) for t in tops)) == 1, tops
            nav = _rect(page.locator(".lp-tabs"))
            cur = _rect(page.locator(".lp-tabs a[aria-current]"))
            assert cur["l"] >= nav["l"] - 1 and cur["r"] <= nav["r"] + 1, (cur, nav)
            assert page.locator(".lp-tabs a[aria-current]").inner_text().startswith("Custom cards")
            sizes = _targets(page, ".lp-tabs a")
            assert min(sizes) >= 43.5, sizes
        else:
            assert len(set(round(t) for t in tops)) == 1, tops
        assert len(set(round(h) for h in heights)) == 1, heights
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, ".lp-tabs a, .lp-tabs a .n") == []
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", [(344, "dark"), (390, "light")])
def test_the_color_tag_sheet_never_covers_its_field(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        box = _open_from_deck_menu(page, "Add a package…", "package")
        btn = box.locator("#dx-ptag-btn")
        btn.scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        btn.click()
        sheet = page.locator(".msel-list.sheet")
        sheet.wait_for(state="visible")
        page.wait_for_timeout(300)
        b, s = _rect(btn), _rect(sheet)
        label = _rect(box.locator("label[for='dx-ptag']"))
        assert b["b"] <= s["t"], (b, s)  # the field sits above the sheet
        assert label["t"] >= 0 and label["b"] <= s["t"], (label, s)  # and so does its label
        assert sheet.locator(".head.title").inner_text().lower() == "color tag"
        page.keyboard.press("Escape")
        sheet.wait_for(state="detached")
        assert box.is_visible()
        browser.close()
    assert errors == []
