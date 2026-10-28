"""The Binder in a browser (0.7.46): the name box finds every card the member owns, not only the
rows already on the page, and its count follows; + adds one to what Archidekt holds, so a change
made in another tab is kept; the list view's picture button is a 44 px target on a phone."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_printing_picker_browser import Server, _chromium, _page

pytest.importorskip("playwright")

SLIME = 9010  # Acidic Slime, the binder's oldest entry: never on the first screen


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _seed(server: Server, n: int) -> None:
    ark = server.ark
    rows = ark.collections.setdefault("alice", {})
    rows[1] = ark._collection_row(1, ark.printing(SLIME), {"quantity": 2})
    pks = (9003, 9001, 9002, 9005, 9008)
    for i in range(n):
        ark.next_coll_id += 1
        row = ark._collection_row(ark.next_coll_id, ark.printing(pks[i % 5]), {"quantity": 1})
        rows[row["id"]] = row


def test_typing_a_name_finds_a_card_past_the_first_screen(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server, 60)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.goto(f"{server.base}/collection", wait_until="networkidle")
        assert page.locator("#col-count").inner_text() == "61 entries"
        assert page.locator("#cards li[data-id='1']").count() == 0  # not loaded yet
        page.locator("#col-q").fill("Acid")
        # the rows on the page do not decide: no "No cards match" while the binder is asked
        assert not page.locator(".col-nomatch").is_visible()
        page.locator("#cards li[data-id='1']").wait_for(state="visible", timeout=5000)
        assert page.locator("#col-count").inner_text() == "1 entry"
        assert page.locator("#cards > li[data-id]:visible").count() == 1
        assert not page.locator(".col-nomatch").is_visible()
        assert page.locator(".loadmore p").inner_text() == "All 1 entry shown."
        assert "q=Acid" in page.url
        # a name nobody owns: then, and only then, "No cards match"
        page.locator("#col-q").fill("Zzyzx")
        page.locator(".col-nomatch").wait_for(state="visible", timeout=5000)
        assert page.locator("#col-count").inner_text() == "0 entries"
        # cleared: the first screen again, with Load more
        page.locator("#col-q").fill("")
        page.wait_for_function("() => document.getElementById('col-count').textContent === '61 entries'")
        assert page.locator(".loadmore [data-load-more]").count() == 1
        assert errors == []
        browser.close()


def test_plus_adds_to_what_archidekt_holds_and_the_picture_button_is_44px(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server, 3)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, touch=True)
        page.goto(f"{server.base}/collection?view=list", wait_until="networkidle")
        row = page.locator("#cards li[data-id='1']")
        assert row.locator("output").first.inner_text() == "2"
        server.ark.collections["alice"][1]["quantity"] = 5  # another tab added three meanwhile
        row.locator("button[value=inc]").first.click()
        page.wait_for_function("() => document.querySelector(\"[data-id='1'] output\").textContent === '6'")
        assert server.ark.collections["alice"][1]["quantity"] == 6
        pic = row.locator(".thumbbtn").first.bounding_box()
        assert pic is not None and pic["width"] >= 44 and pic["height"] >= 44, pic
        assert errors == []
        browser.close()
