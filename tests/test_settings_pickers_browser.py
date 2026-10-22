"""The deck settings page's pickers in headless Chromium: the Collaborators box is the site's own
combobox over the people the member follows (static/suggest.js, data-suggest="list"; no browser
datalist): focusing it shows them all, typing narrows them, the keyboard and a tap pick one, and
Add sends that name to the confirmation step; a person not followed is refused under the box with
their Archidekt profile, and the page opens on that section. Phone and desktop widths, light and
dark, text contrast at least 4.5:1. Skipped without a Chromium build."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from .test_editor_browser import PNG, _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

FRIENDS = ["bram", "cora_mtg", "Dmitri", "elspeth.fan", "finn", "gwen", "hollis", "Ines"]


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True)
    for i, name in enumerate(FRIENDS):
        s.ark.users[name] = {"password": "x", "id": 200 + i, "decks": []}
    s.ark.follows["alice"] = {78} | {200 + i for i in range(len(FRIENDS))}
    s.ark.editors[42] = [
        {
            "id": 901,
            "user": {"id": 78, "username": "amy", "avatar": None},
            "createdBy": {"id": 77, "username": "alice", "avatar": None},
            "createdAt": "2026-10-10T00:00:00Z",
        }
    ]
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _exe() -> str:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _page(p, exe: str, server: Server, sid: str, width: int, scheme: str):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": width, "height": 844}, color_scheme=scheme)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


# Every visible text in the given elements against the background it is drawn on.
CONTRAST = """(sel) => {
  function rgb(s) { var m = s.match(/[\\d.]+/g); return m ? m.map(Number) : [0, 0, 0, 0]; }
  function lum(c) {
    var a = c.slice(0, 3).map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
  }
  function bg(e) {
    // translucent backgrounds (the notices' tints) are laid over what is under them
    var layers = [];
    for (; e; e = e.parentElement) {
      var c = rgb(getComputedStyle(e).backgroundColor);
      if (c.length < 4 || c[3] >= 1) { layers.push(c); break; }
      if (c[3] > 0) layers.push(c);
    }
    var out = layers.length && (layers[layers.length - 1].length < 4 || layers[layers.length - 1][3] >= 1)
      ? layers.pop().slice(0, 3) : [255, 255, 255];
    while (layers.length) {
      var t = layers.pop();
      out = out.map(function (v, i) { return t[i] * t[3] + v * (1 - t[3]); });
    }
    return out;
  }
  var worst = 99, what = "";
  document.querySelectorAll(sel).forEach(function (e) {
    if (!e.getClientRects().length || !e.textContent.trim()) return;
    var a = lum(rgb(getComputedStyle(e).color)), b = lum(bg(e));
    var r = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (r < worst) {
      worst = r;
      what = e.tagName + "." + e.className + ":" + e.textContent.trim().slice(0, 30);
    }
  });
  return [worst, what];
}"""


def _contrast_ok(page, sel: str) -> None:
    worst, what = page.evaluate(CONTRAST, sel)
    assert worst >= 4.5, (worst, what)


def _options(page) -> list[str]:
    return page.locator("#collabname-list [role=option]").all_inner_texts()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [390, 1280])
def test_collaborator_box_picks_from_the_people_followed(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _page(p, exe, server, sid, width, scheme)
        posted: list[str] = []
        page.on(
            "request",
            lambda r: (
                posted.append(r.post_data or "") if r.method == "POST" and "collaborators" in r.url else None
            ),
        )
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        section = page.locator("#collaborators")
        assert section.locator("datalist").count() == 0
        # the collaborator is a row with its remove button; the box is the site's combobox
        assert section.locator(".collablist li .name").all_inner_texts() == ["amy"]
        assert section.locator("button[aria-label='Remove “amy”']").is_visible()
        box = page.locator("#collabname")
        assert box.get_attribute("role") == "combobox" and box.get_attribute("aria-expanded") == "false"
        assert box.get_attribute("list") is None
        # the box and its button share one row, the button at the box's height; on a phone the
        # shared form row puts the button under the box, full width (the approved mockup)
        b, a = box.bounding_box(), section.locator("form.addtag button").bounding_box()
        if width >= 600:
            assert abs(b["y"] - a["y"]) < 2 and abs(b["height"] - a["height"]) < 2, (b, a)
            assert a["x"] > b["x"] + b["width"]
        else:
            assert a["y"] > b["y"] + b["height"] - 2 and abs(a["width"] - b["width"]) < 2, (b, a)
        # focusing shows everyone followed, less the collaborators, under a heading
        box.click()
        listbox = page.locator("#collabname-list[role=listbox]")
        listbox.wait_for(state="visible", timeout=3000)
        assert box.get_attribute("aria-expanded") == "true"
        assert listbox.locator("li.head").inner_text().strip().lower() == "people you follow"
        assert _options(page) == sorted(FRIENDS, key=str.casefold)
        lb = listbox.bounding_box()
        assert lb["x"] >= 0 and lb["x"] + lb["width"] <= width
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width  # no sideways scroll
        _contrast_ok(page, "#collabname-list li, #collaborators label, #collaborators .who span")
        # typing narrows the list (any part of a name, starts first)
        box.press_sequentially("in", delay=30)
        page.wait_for_function(
            "() => document.querySelectorAll('#collabname-list [role=option]').length === 2"
        )
        assert _options(page) == ["Ines", "finn"]
        assert listbox.locator("mark").first.inner_text().lower() == "in"
        # nothing matching says so, in the list's own words
        box.fill("zzz")
        listbox.locator("li.none", has_text="Not among the people you follow").wait_for(timeout=3000)
        # clearing the box shows the whole list again; the keyboard picks
        box.fill("")
        page.wait_for_function(
            "() => document.querySelectorAll('#collabname-list [role=option]').length === 8"
        )
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        active = box.get_attribute("aria-activedescendant")
        assert active and page.locator(f"#{active}").inner_text() == "cora_mtg"
        page.keyboard.press("Enter")
        assert box.input_value() == "cora_mtg" and not listbox.is_visible()
        # Escape closes; a click on the box opens the list again, and a tap on a row picks it
        box.click()
        listbox.wait_for(state="visible", timeout=3000)
        page.keyboard.press("Escape")
        assert not listbox.is_visible()
        box.click()
        listbox.wait_for(state="visible", timeout=3000)
        box.fill("")
        listbox.locator("[role=option]", has_text="gwen").click()
        assert box.input_value() == "gwen" and not listbox.is_visible()
        # Add asks in a box first; nothing is sent until the box's Add
        section.locator("form.addtag button").click()
        dialog = page.locator("[role=dialog]", has_text="Let “gwen” edit")
        dialog.wait_for(timeout=3000)
        assert posted == []
        with page.expect_navigation():
            dialog.locator("button", has_text="Add “gwen”").click()
        assert any("action=add" in d and "username=gwen" in d and "confirmed=1" in d for d in posted), posted
        assert [e["user"]["username"] for e in server.ark.editors[42]] == ["amy", "gwen"]
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1280, "light")])
def test_a_person_not_followed_is_refused_under_the_box(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _page(p, exe, server, sid, width, scheme)
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        box = page.locator("#collabname")
        box.fill("nobody")
        page.keyboard.press("Escape")
        with page.expect_navigation():
            page.locator("#collaborators form.addtag button").click()
        page.wait_for_load_state("networkidle")
        # the answer opens on the section, the message right under the box, nothing at the top
        msg = page.locator("#collaborators-msg")
        assert msg.is_visible() and page.url.endswith("#collaborators")
        top = page.evaluate(
            "() => document.getElementById('collaborators-msg').getBoundingClientRect().bottom"
        )
        assert 0 < top <= 844
        assert page.locator("main > .notice.error").count() == 0
        assert "nobody isn't someone you follow on Archidekt." in msg.inner_text()
        link = msg.locator("a.btn")
        assert link.inner_text().strip() == "Open nobody on Archidekt"
        assert link.get_attribute("href") == "https://archidekt.com/u/nobody"
        assert link.get_attribute("target") == "_blank"
        assert box.input_value() == "nobody" and box.get_attribute("aria-invalid") == "true"
        assert "collaborators-msg" in (box.get_attribute("aria-describedby") or "")
        _contrast_ok(page, "#collaborators-msg p, #collaborators-msg a")
        assert errors == []
        browser.close()


def test_nobody_followed_offers_a_way_to_find_people(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    server.ark.follows["alice"] = set()
    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _page(p, exe, server, sid, 390, "light")
        page.goto(f"{server.base}/decks/42/settings#collaborators", wait_until="networkidle")
        box = page.locator("#collabname")
        # a plain box: nothing to pick from, so no list opens
        assert box.get_attribute("data-suggest") is None and box.get_attribute("role") is None
        hint = page.locator("#collaborators p", has_text="You don't follow anyone on Archidekt yet.")
        assert hint.is_visible() and hint.locator("a[href='/search']").is_visible()
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1280, "light")])
def test_tag_box_suggests_archidekts_tags(server: Server, width: int, scheme: str) -> None:
    """The tag box asks the gateway (/decks/api/tags) for Archidekt's tags as the member types,
    leaves out the deck's own, and a picked tag is added with the form's own button."""
    from playwright.sync_api import sync_playwright

    server.ark.tags[4] = "Budget Tribal"
    server.ark.deck_tags[42] = [{"id": 5, "tag": 2, "deck": 42, "name": "tribal", "position": "M-1"}]
    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _page(p, exe, server, sid, width, scheme)
        asked: list[str] = []
        page.on("request", lambda r: asked.append(r.url) if "/decks/api/tags" in r.url else None)
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        box = page.locator("#tagname")
        assert box.get_attribute("role") == "combobox"
        listbox = page.locator("#tagname-list[role=listbox]")
        # one letter asks nothing; two do
        box.click()
        box.press_sequentially("b", delay=40)
        page.wait_for_timeout(400)
        assert asked == [] and not listbox.is_visible()
        box.press_sequentially("u", delay=40)
        listbox.wait_for(state="visible", timeout=3000)
        assert listbox.locator("[role=option]").all_inner_texts() == ["budget", "Budget Tribal"]
        _contrast_ok(page, "#tagname-list li, #tags label")
        # the deck's own tag is left out
        box.fill("tri")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#tagname-list [role=option]')]"
            ".map(o => o.textContent).join('|') === 'Budget Tribal'"
        )
        box.fill("zzz")
        listbox.locator("li.none", has_text="No Archidekt tag by that name").wait_for(timeout=3000)
        box.fill("bu")
        listbox.locator("[role=option]", has_text="budget").first.wait_for(timeout=3000)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert box.input_value() == "budget" and not listbox.is_visible()
        with page.expect_navigation():
            page.locator("#tags form.addtag button").click()
        page.wait_for_load_state("networkidle")
        assert page.url.endswith("#tags")
        assert "budget" in page.locator("#tags .taglist").inner_text()
        assert page.locator("#tags .notice.ok").inner_text() == "Tag added on Archidekt."
        assert not any(u.endswith("q=b") for u in asked), asked
        assert errors == []
        browser.close()


def test_tag_box_stays_a_plain_box_when_the_lookup_fails(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _page(p, exe, server, sid, 1280, "light")
        page.route(re.compile(r".*/decks/api/tags.*"), lambda r: r.fulfill(status=429, body="{}"))
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        box = page.locator("#tagname")
        box.click()
        box.press_sequentially("budget", delay=40)
        page.wait_for_timeout(600)
        assert not page.locator("#tagname-list").is_visible()
        with page.expect_navigation():
            page.keyboard.press("Enter")
        assert "Tag added on Archidekt." in page.locator("#tags").inner_text()
        assert errors == []
        browser.close()


# The placeholder's width against the room the box gives it (inside its padding and border)
PLACEHOLDER_ROOM = """() => {
  const i = document.getElementById('collabname'), cs = getComputedStyle(i);
  const c = document.createElement('canvas').getContext('2d');
  c.font = cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
  const room = i.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  return [c.measureText(i.placeholder).width, room];
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [344, 390, 673, 1280])
def test_collaborator_picker_fits_names_its_list_and_has_finger_sized_rows(
    server: Server, width: int, scheme: str
) -> None:
    """At 344 px (Fold cover) the placeholder "Pick someone you follow" shows whole, the box
    and Add still on one row. The list has an accessible name, its heading's words. Each
    person in it is at least 44 px tall."""
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _page(p, exe, server, sid, width, scheme)
        page.goto(f"{server.base}/decks/42/settings#collaborators", wait_until="networkidle")
        section = page.locator("#collaborators")
        box = page.locator("#collabname")
        text, room = page.evaluate(PLACEHOLDER_ROOM)
        assert text <= room, (text, room)
        b, a = box.bounding_box(), section.locator("form.addtag button").bounding_box()
        if width >= 600:
            assert abs(b["y"] - a["y"]) < 2 and a["x"] > b["x"] + b["width"], (b, a)
        else:  # the shared form row on a phone: the button under the box (the approved mockup)
            assert a["y"] > b["y"] + b["height"] - 2, (b, a)
        assert section.locator("form.addtag button").inner_text().strip() == "Add"
        box.click()
        listbox = page.locator("#collabname-list[role=listbox]")
        listbox.wait_for(state="visible", timeout=3000)
        assert listbox.get_attribute("aria-label") == "People you follow"
        heights = [r["height"] for r in (o.bounding_box() for o in listbox.locator("[role=option]").all())]
        assert heights and min(heights) >= 44, heights
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        _contrast_ok(page, "#collabname-list li, #collaborators label, #collaborators .who span")
        section.scroll_into_view_if_needed()
        if os.environ.get("MTG_SHOTS"):
            page.screenshot(path=str(Path(os.environ["MTG_SHOTS"]) / f"l9-l11-collab-{width}-{scheme}.png"))
        assert errors == []
        browser.close()
