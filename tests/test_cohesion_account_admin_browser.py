"""The cohesion round's Account, Install, Guide and Admin pages in headless Chromium, against
docs/UI-STANDARD.md: the admin tabs on a phone are two rows of equal tabs (never seven stacked ones),
the Card models filter row has no fixed-width picker with an empty corner beside it, the settings
form is the narrow column under the full-width tab bar, the Account pages share one narrow column
whose title and way back are the shared pieces, and a guide topic's text is the reading column
beside its contents. Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_cardsearch_browser import _chromium, _page
from .test_settings_page_browser import _admin
from .test_suggest_browser import Server
from .waits import expect, frame

pytest.importorskip("playwright")

NARROW = 44 * 14  # the standard's narrow column, in px at the 14 px root


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path, admin_group="mtg-admins")
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_admin_tabs_are_two_rows_on_a_phone_and_filters_fill_their_row(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    server.db.put_card_model("sol ring", "Sol Ring", "{}", "user-1", 100)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        for path in ("/admin", "/admin/models", "/admin/scanner"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            nav = page.locator("nav.adm-nav")
            frame(page)
            box = nav.bounding_box()
            # two rows of 44 px tabs (four over three), not seven rows
            assert box and box["height"] <= 2 * 44 + 12, (path, box)
            tabs = [t.bounding_box() for t in nav.locator("a").all()]
            assert len(tabs) == 7 and all(tabs)
            rows = sorted({round(t["y"]) for t in tabs})
            assert len(rows) == 2 and [sum(round(t["y"]) == y for t in tabs) for y in rows] == [4, 3], tabs
            # every tab the same width, each row in the middle of the bar, no tab's words cut,
            # nothing scrolling sideways
            assert max(t["width"] for t in tabs) - min(t["width"] for t in tabs) <= 2, tabs
            for y in rows:
                row = [t for t in tabs if round(t["y"]) == y]
                left, right = (
                    row[0]["x"] - box["x"],
                    box["x"] + box["width"] - row[-1]["x"] - row[-1]["width"],
                )
                assert abs(left - right) <= 2, (left, right)
            assert nav.evaluate("n => n.scrollWidth <= n.clientWidth + 1")
            assert nav.evaluate("n => [...n.children].every(a => a.scrollWidth <= a.clientWidth + 1)")
            assert page.evaluate("document.documentElement.scrollWidth") <= 390
        page.goto(f"{server.base}/admin", wait_until="networkidle")
        page.locator("nav.adm-nav a", has_text="Card scanner").click()
        page.wait_for_url("**/admin/scanner")
        # Card models: the name box and the member picker each take the row's whole width on a
        # phone (no 9.5rem picker with an empty corner beside it), the picker's words on one line
        page.goto(f"{server.base}/admin/models", wait_until="networkidle")
        tools = page.locator(".amo .ui-tools").bounding_box()
        pick = page.locator(".amo .ui-tools .f .msel-btn").bounding_box()
        assert tools and pick and pick["width"] >= tools["width"] - 2, (tools, pick)
        assert pick["height"] <= 46, pick
        browser.close()

        # a computer: seven tabs across the admin pages' whole width, in the same place on every tab
        browser, page, errors2 = _page(p, server, sid, 1280, "light", exe)
        places = []
        for path in ("/admin", "/admin/settings", "/admin/users"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            frame(page)
            b = page.locator("nav.adm-nav").bounding_box()
            assert b
            places.append((round(b["x"]), round(b["y"]), round(b["width"])))
        assert len(set(places)) == 1, places
        # Settings: the form is the standard's narrow column, centred under the tab bar
        page.goto(f"{server.base}/admin/settings", wait_until="networkidle")
        form = page.locator(".set-page").bounding_box()
        assert form and abs(form["width"] - NARROW) <= 1, form
        assert abs(form["x"] - (1280 - form["x"] - form["width"])) <= 16, form
        # a group's heading is the site's section heading, the size no page sets itself
        sizes = page.evaluate(
            "() => [...document.querySelectorAll('.set-group h2')].map(h => getComputedStyle(h).fontSize)"
        )
        assert sizes and set(sizes) == {"16.8px"}, sizes
        browser.close()
        assert not errors and not errors2, errors + errors2


def test_account_pages_share_the_narrow_column_title_and_way_back(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "light", exe)
        looks = {}
        for path in ("/account", "/account/details", "/account/assistants", "/account/assistants/default"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            h1 = page.locator("main .ui-head h1")
            expect(h1).to_have_count(1)
            looks[path] = page.evaluate(
                """() => { const h = document.querySelector('main .ui-head h1'), cs = getComputedStyle(h);
                  const col = document.querySelector('main .yhub, main .acc').getBoundingClientRect();
                  const r = h.getBoundingClientRect();
                  return [cs.fontSize, cs.fontWeight, cs.marginBottom, Math.round(col.width),
                          Math.round(col.left), Math.round(r.left)]; }"""
            )
        # one title look, one column, the title starting where the column does
        assert len({tuple(v) for v in looks.values()}) == 1, looks
        size, weight, _mb, width, left, h1_left = next(iter(looks.values()))
        assert (size, weight, width) == ("28px", "700", NARROW) and left == h1_left, looks
        assert abs(left - (1280 - left - width)) <= 16, looks
        # the group labels are the shared h2.ui-group, in one look
        page.goto(f"{server.base}/account", wait_until="networkidle")
        labels = page.evaluate(
            """() => [...document.querySelectorAll('main .ygrp h2')].filter(h => h.offsetParent).map(h => {
              const cs = getComputedStyle(h);
              return [h.className, cs.fontSize, cs.textTransform, cs.color].join(' '); })"""
        )
        assert labels and len(set(labels)) == 1 and labels[0].startswith("ui-group "), labels
        # Sign out is a button: its icon and words centred together, the column's whole width
        out = page.locator(".ysignout button")
        box = out.bounding_box()
        mid = page.evaluate(
            """() => { const b = document.querySelector('.ysignout button').getBoundingClientRect();
              const kids = [...document.querySelector('.ysignout button').children]
                .map(k => k.getBoundingClientRect());
              const l = Math.min(...kids.map(k => k.left)), r = Math.max(...kids.map(k => k.right));
              return Math.abs((l + r) / 2 - (b.left + b.right) / 2); }"""
        )
        assert box and abs(box["width"] - NARROW) <= 1 and box["height"] == 44 and mid <= 1, (box, mid)
        # the way back a level goes back
        page.goto(f"{server.base}/account/details", wait_until="networkidle")
        page.locator("a.bn-back").click()
        page.wait_for_url("**/account")
        browser.close()
        assert not errors, errors


def test_a_guide_topic_is_the_reading_column_beside_its_contents(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1920, "light", exe)
        page.goto(f"{server.base}/guide/how-export", wait_until="networkidle")
        side = page.locator(".guide .gside").bounding_box()
        text = page.locator(".guide .gmain").bounding_box()
        assert side and text and side["x"] + side["width"] <= text["x"]
        assert text["width"] <= NARROW + 1, text
        # the two together sit in the middle of a wide window
        assert abs(side["x"] - (1920 - text["x"] - text["width"])) <= 24, (side, text)
        # a topic's sections are section headings (h2), under the page's one title
        assert page.locator(".gsec h1").count() == 1 and page.locator(".gsec h2").count() >= 2
        assert page.locator(".gsec h4").count() == 0
        # a section's link shows when its heading is hovered, and goes to the section
        h2 = page.locator(".gsec h2").first
        h2.hover()
        expect(h2.locator(".anchor")).to_have_css("opacity", "1")
        h2.locator(".anchor").click()
        assert "#" in page.url
        browser.close()
        assert not errors, errors
