"""The polished 0.7.46 Binder surfaces in headless Chromium: the tag chips on tiles and rows, the
Details box's tag chips (44 px, in a box that scrolls with many tags), Clear binder inside the
head's More menu, the stopped import's strip with its bar, the restore question as a box on the
Binder page, and the List view at 200% text on a desktop. Phone (360, 412), the Fold (717, 840) and
a desktop (1280), light and dark: nothing scrolls sideways, text keeps 4.5:1 and targets are 44 px.
Set MTG_POLISH_SHOTS=<dir> to keep screenshots."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from mtg_gateway import collection_bulk

from .test_card_search_filters_browser import CONTRAST
from .test_overflow_sweep_browser import MEASURE
from .test_printing_picker_browser import Server, _chromium, _page
from .test_text_zoom_browser import TEXT_200
from .waits import redrawn

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_POLISH_SHOTS")
TEXT = "main h1, main h2, main p, main li, main label, main button, main .btn, main a, .notice, .pill"
TAGS = [
    ("Trade", "#336699"),
    ("Keep", "#2a7d2a"),
    ("Wishlist", "#aa3377"),
    ("For Modern deck", "#cc8800"),
    ("Needs sleeving", "#777777"),
    ("Lent to a friend", "#1155cc"),
    ("Signed", "#993300"),
    ("Proxy", "#444444"),
    ("Bulk", "#5a5a9a"),
    ("Cube", "#118877"),
    ("Promo", "#bb2222"),
    ("Sell", "#006644"),
]


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _seed(server: Server) -> None:
    """Twelve binder tags, six entries (one with every tag, one with four), a copy of the binder
    kept before a clear, and an import that stopped at line 121 of 130."""
    ark = server.ark
    tags = {}
    for name, color in TAGS:
        ark.next_collection_tag_id += 1
        tid = ark.next_collection_tag_id
        ark.collection_tag_lists.setdefault("alice", {})[tid] = {"id": tid, "name": name, "color": color}
        tags[name] = tid
    for i, pk in enumerate((9003, 5011, 5007, 9005, 9008, 9003)):
        ark.next_coll_id += 1
        on = (
            [tags[n] for n, _c in TAGS]
            if i == 3
            else ([tags["Trade"], tags["Keep"], tags["Wishlist"], tags["Proxy"]] if i == 0 else [])
        )
        row = ark._collection_row(ark.next_coll_id, ark.printing(pk), {"quantity": 1, "tags": on})
        row["createdAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - i * 86400))
        ark.collections.setdefault("alice", {})[row["id"]] = row
    sub = server.db._one("SELECT sub FROM users", ())["sub"]
    snap = {
        "name": collection_bulk.NAME,
        "kind": "binder",
        "tags": [{"id": 1, "name": "Trade", "color": "#336699"}],
        "cards": [
            {
                "card_id": 9003,
                "name": "Sol Ring",
                "quantity": 2,
                "set": "cmr",
                "collector_number": "472",
                "finish": "nonfoil",
                "condition": "NM",
                "language": "EN",
                "tags": ["Trade"],
                "scryfall_id": "",
            }
        ],
    }
    assert server.db.save_snapshot(
        "bindercopy1",
        owner_sub=sub,
        deck_id=collection_bulk.TARGET,
        proposal_id=None,
        fingerprint="",
        deck=snap,
    )
    jobs = server.app.state.gateway.collection_jobs
    jobs.store.start(sub, "job1", "import", [{"name": f"Card {i}", "quantity": 1} for i in range(130)], None)
    jobs.store.update(
        sub,
        "job1",
        state="stopped",
        done=120,
        result={"added": [], "skipped": [], "stopped": "Archidekt is not answering right now"},
    )


def _shot(page, name: str) -> None:  # noqa: ANN001
    if SHOTS:
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def _short(page, sel: str) -> list[str]:
    return page.evaluate(
        "(sel) => [...document.querySelectorAll(sel)]"
        ".filter(e => e.getBoundingClientRect().width && getComputedStyle(e).visibility !== 'hidden')"
        ".filter(e => e.getBoundingClientRect().height < 43.5)"
        ".map(e => e.textContent.trim().slice(0, 30) + ' ' + Math.round(e.getBoundingClientRect().height))",
        sel,
    )


@pytest.mark.parametrize(
    "width,theme,view",
    [
        (360, "light", "grid"),
        (412, "dark", "list"),
        (717, "light", "list"),
        (840, "dark", "grid"),
        (1280, "light", "list"),
        (1280, "dark", "grid"),
    ],
)
def test_the_binder_with_tags_the_strip_and_the_menu_fits_each_surface(
    server: Server, width: int, theme: str, view: str
) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server)
    touch = width < 900
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme, touch=touch)
        page.goto(f"{server.base}/collection?view={view}&import=job1", wait_until="networkidle")
        tag = f"{view}-{width}-{theme}"
        assert page.evaluate(MEASURE) == [], tag
        assert page.evaluate(CONTRAST, TEXT) == [], tag
        # the tag chips: a colour dot and the name, on the tagged entries only
        chips = page.locator("#cards .tagrow .pill.tag")
        assert chips.count() == 4 + 4 + 1, tag  # four, then four and "+8"
        more = page.locator("#cards .pill.tag.more")
        assert more.inner_text() == "+8" and "Needs sleeving" in more.get_attribute("title")
        dot = chips.first.locator(".sw")
        assert dot.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(51, 102, 153)"
        # the stopped import's strip: a warning, its bar across the strip, Resume and Stop here 44 px
        strip = page.locator(".notice.warn.importing.stopped")
        assert strip.is_visible()
        bar = strip.locator("progress.iprog").bounding_box()
        inner = strip.bounding_box()
        assert bar["width"] >= inner["width"] * 0.85, (bar, inner)
        for b in ("button[value=import_resume]", "button[value=import_done]"):
            assert strip.locator(b).bounding_box()["height"] >= (44 if touch else 36), b
        # the head: More (Export CSV and Clear binder…) and Add cards, nothing else
        head = page.locator(".ui-head.phead .acts")
        assert head.locator("> *").count() == 2
        more_btn = head.locator("details.colmore > summary")
        assert more_btn.is_visible() and more_btn.bounding_box()["height"] >= (44 if touch else 36)
        assert not head.locator("button[value=clear]").is_visible()
        _shot(page, f"binder-{tag}")
        more_btn.click()
        clear = head.locator("details.colmore .menu button[value=clear]")
        assert clear.is_visible() and clear.bounding_box()["height"] >= 44
        assert head.locator("details.colmore .menu a[href='/collection/export.csv']").is_visible()
        # (the open menu lies over the page, as a menu does: no overlap sweep here)
        assert page.evaluate(CONTRAST, "details.colmore .menu a, details.colmore .menu button") == []
        _shot(page, f"binder-{tag}-more")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(360, "light"), (412, "dark"), (1280, "dark")])
def test_the_details_box_tag_chips_are_44px_and_scroll_with_many_tags(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme, touch=width < 900)
        page.goto(f"{server.base}/collection?view=grid", wait_until="networkidle")
        rid = page.locator("#cards > li[data-id]:has(.pill.tag.more)").first.get_attribute("data-id")
        page.goto(f"{server.base}/collection?view=grid&details={rid}", wait_until="networkidle")
        box = page.locator("#col-details")
        assert box.is_visible()
        ticks = box.locator("label.tagck")
        assert ticks.count() == 12
        first = ticks.first
        assert first.bounding_box()["height"] >= 44
        assert first.evaluate("e => getComputedStyle(e).display") in ("inline-flex", "flex")
        # the box, the colour dot and the name sit in a row with room between them
        xs = first.evaluate(
            "e => [e.querySelector('input'), e.querySelector('.sw'), e.querySelector('.t')]"
            ".map(c => c.getBoundingClientRect()).map(r => [r.x, r.x + r.width])"
        )
        assert xs[0][1] + 4 <= xs[1][0] and xs[1][1] + 4 <= xs[2][0], xs
        assert first.locator("input").evaluate("e => e.getBoundingClientRect().width") <= 24
        assert first.locator("input").is_checked()  # the entry carries every tag
        # with many tags the ticks box scrolls inside the Details box rather than pushing Save away
        ticks_box = box.locator(".tagline.ticks")
        assert ticks_box.evaluate("e => e.scrollHeight > e.clientHeight")
        save = box.locator("button.primary", has_text="Save")
        assert save.is_visible() and save.bounding_box()["y"] + save.bounding_box()["height"] <= 900
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, "#col-details label.tagck, #col-details .acts button") == []
        _shot(page, f"details-{width}-{theme}")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(360, "light"), (412, "dark"), (1280, "light")])
def test_the_restore_question_is_a_box_on_the_binder_page(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme, touch=width < 900)
        page.goto(f"{server.base}/collection?view=grid&restore=bindercopy1", wait_until="networkidle")
        box = page.locator("#col-restore")
        assert box.is_visible() and box.get_attribute("role") == "dialog"
        assert box.locator("h2").inner_text() == "Your binder is not empty"
        keep = box.locator(".acts a", has_text="Keep my binder as it is")
        add = box.locator(".acts button", has_text="Add them to my binder")
        for b in (keep, add):
            assert b.is_visible() and b.bounding_box()["height"] >= (44 if width < 900 else 36)
        assert keep.get_attribute("href") == "/history?type=snapshots"
        assert page.evaluate(MEASURE) == []
        assert (
            page.evaluate(
                CONTRAST, "#col-restore h2, #col-restore p, #col-restore .acts a, #col-restore .acts button"
            )
            == []
        )
        _shot(page, f"restore-{width}-{theme}")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_list_view_holds_together_at_200_percent_text_on_a_desktop(server: Server, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, theme=theme)
        page.goto(f"{server.base}/collection?view=list", wait_until="networkidle")
        page.evaluate(TEXT_200)
        redrawn(page)
        assert page.evaluate(MEASURE) == []
        # the table folded to its two-column layout: the name has room, the Set column is gone
        name = page.locator("#cards .tr.row .name").first.bounding_box()
        assert name["width"] >= 200, name
        assert not page.locator("#cards .tr.row .setc").first.is_visible()
        _shot(page, f"list-200-{theme}")
        assert errors == []
        browser.close()
