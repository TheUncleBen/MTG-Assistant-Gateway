"""Picture scanning on the /scan page (0.7.47), in headless Chromium through the page's real code.

The camera is a canvas stream showing a card on a table, so the page's own edge search finds a
card outline; the gateway's answer to /scan/api/identify is stood in for (picture.py and the
service have their own tests in test_scan_pictures.py), so these test the page's rules:

- the card is sent once it holds still, and not again while it stays in view;
- "Check each card" shows every card; "Add right away" adds sure cards only, with Undo;
- printings that share the artwork are told apart by the set and number line among those
  printings only, and the sheet asks when that line can't be read;
- the finish, the printing and "Not this card" can be changed before Add;
- a miss offers "Keep this picture for the admin", worded for an admin or a member;
- the scan area moves and resizes, and is kept on the device.

Skipped without a Chromium build.
"""

from __future__ import annotations

import json
import re

import pytest

from .test_scan_browser import Server, _chromium_path, server  # noqa: F401

pytest.importorskip("playwright")


def _card_camera() -> str:
    """A phone camera over a card on a table (the scanner tests' drawn Sol Ring, laid on a plain
    table as a photo of it would be), as a canvas stream the page's own camera code receives; with
    window.__noCard set it shows the bare table."""
    import base64
    import tempfile
    from pathlib import Path

    from .test_scan_browser import card_photo
    from .test_scan_detect_browser import card_corners, scene

    with tempfile.TemporaryDirectory() as d:
        card, photo = Path(d) / "card.jpg", Path(d) / "scene.jpg"
        card_photo(card, "Sol Ring")
        scene(card, photo, (640, 480), card_corners(320, 240, 170, 0.01))
        data = base64.b64encode(photo.read_bytes()).decode()
    return (
        """
navigator.mediaDevices.getUserMedia = async () => {
  window.__cameraOpened = (window.__cameraOpened || 0) + 1;
  const img = new Image(); img.src = 'data:image/jpeg;base64,"""
        + data
        + """';
  await img.decode();
  const c = document.createElement('canvas'); c.width = 640; c.height = 480;
  // headless Chromium's video of a canvas stream is black: the page's frame grab reads this canvas
  window.__scanFrameOverride = () => c;
  const x = c.getContext('2d');
  const draw = () => {
    if (window.__noCard) { x.fillStyle = '#b0926c'; x.fillRect(0, 0, 640, 480); } else x.drawImage(img, 0, 0);
  };
  draw(); setInterval(draw, 100);
  return c.captureStream(10);
};
"""
    )


STAND_IN_OCR = """() => {
  window.__i = '';
  window.__scanOcrOverride = async (canvas, kind) => kind === 'info'
    ? {text: window.__i, confidence: window.__i ? 90 : 0, words: []}
    : {text: '', confidence: 0, words: []};
}"""


def card(set_code: str, number: str, *, name: str = "Sol Ring", oracle: str = "o-sol", **extra) -> dict:
    out = {
        "name": name,
        "scryfall_id": f"{set_code}-{number}",
        "oracle_id": oracle,
        "set": set_code,
        "set_name": set_code.upper() + " set",
        "collector_number": number,
        "finishes": ["nonfoil", "foil"],
        "type_line": "Artifact",
        "released_at": "2021-04-23",
        "prices": {"usd": "1.50", "usd_foil": "4.00"},
    }
    out.update(extra)
    return out


SOL_C21 = card("c21", "263")
SOL_FRC = card("frc", "21", released_at="2025-01-01")
MIND_STONE = card("c21", "246", name="Mind Stone", oracle="o-mind")


def answer(best: dict | None, *, sure: bool = True, same: list[dict] | None = None) -> dict:
    if best is None:
        return {"ok": True, "card": None, "sure": False, "margin": 0.0, "same_art": [], "others": []}
    return {
        "ok": True,
        "card": best,
        "sure": sure,
        "margin": 0.12 if sure else 0.01,
        "similarity": 0.8,
        "same_art": same if same is not None else [best],
        "others": [{**MIND_STONE, "similarity": 0.6}],
    }


def _chromium() -> str:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe  # type: ignore[return-value]


def _page(p, exe, srv: Server, *, answers: list[dict], admin: bool = False, width: int = 412, init: str = ""):
    """The scan page with picture scanning on (the config says the index is ready), the camera
    showing a card, and /scan/api/identify answering from ``answers`` in turn (the last one
    repeats). Sent pictures and kept misses are recorded on ``calls``."""
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": width, "height": 915}, is_mobile=True, has_touch=True)
    ctx.add_cookies([{"name": "mtg_session", "value": srv.sign_in(), "url": srv.base}])
    ctx.add_init_script(_card_camera() + init)
    page = ctx.new_page()
    errors: list[str] = []
    calls: dict[str, list] = {"identify": [], "miss": []}
    page.on("pageerror", lambda e: errors.append(str(e)))

    def scan_html(route):
        resp = route.fetch()
        body = re.sub(r'"pictures": (true|false)', '"pictures": true', resp.text())
        body = re.sub(r'"admin": (true|false)', f'"admin": {"true" if admin else "false"}', body)
        route.fulfill(response=resp, body=body)

    def identify(route):
        req = route.request
        calls["identify"].append(
            {"type": req.headers.get("content-type"), "size": len(req.post_data_buffer or b"")}
        )
        out = answers[min(len(calls["identify"]) - 1, len(answers) - 1)]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(out))

    def miss(route):
        req = route.request
        calls["miss"].append(
            {"note": req.headers.get("x-scan-note"), "size": len(req.post_data_buffer or b"")}
        )
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "id": "m1"}))

    page.route(f"{srv.base}/scan", scan_html)
    page.route(f"{srv.base}/scan/api/identify", identify)
    page.route(f"{srv.base}/scan/api/miss", miss)
    page.goto(f"{srv.base}/scan")
    page.wait_for_function("() => window.__scan && window.__scan.ownCamera")
    page.evaluate(STAND_IN_OCR)
    return browser, page, errors, calls


APP_BRIDGE = """
window.__calls = [];
window.MtgNative = {
  openCamera: function () { window.__calls.push('openCamera'); },
  openCameraAuto: function () { window.__calls.push('openCameraAuto'); },
  version: function () { return 'test'; },
};
"""


def test_in_the_app_the_page_is_the_scanner_once_pictures_are_ready(server: Server) -> None:  # noqa: F811
    """With the picture index ready the page starts its own live camera inside the Android app too,
    never opens the app's camera panel, and tells the app so (window.__scan.ownCamera, read by
    MainActivity.startScan). Before then the app keeps its panel (test_scan_direction_browser.py)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors, calls = _page(p, exe, server, answers=[answer(SOL_C21)], init=APP_BRIDGE)
        assert page.evaluate("() => window.__scan.ownCamera") is True
        page.wait_for_function("() => (document.querySelector('#camera video') || {}).videoWidth > 0")
        assert page.locator("#open-phone-camera").count() == 0
        assert page.locator("#shutter").get_attribute("aria-label") == "Detect now"
        page.wait_for_selector("#found", timeout=15000)
        assert page.evaluate("() => window.__calls") == []
        assert errors == []
        browser.close()


def test_a_card_held_still_is_found_shown_to_check_and_sent_once(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors, calls = _page(p, exe, server, answers=[answer(SOL_C21)])
        # the mode switch and Detect now replace Auto and the shutter's Scan card
        assert page.locator(".scan-mode [aria-checked=true]").text_content() == "Check each card"
        assert page.locator("#shutter").get_attribute("aria-label") == "Detect now"
        assert page.locator("#auto-switch").count() == 0
        page.wait_for_selector("#found", timeout=15000)
        assert calls["identify"][0]["type"] == "image/jpeg"
        assert 2_000 < calls["identify"][0]["size"] < 400_000
        sheet = page.locator("#found")
        assert sheet.locator("h3").text_content() == "Sol Ring"
        assert "C21 263" in sheet.locator(".sheet-row.printing").text_content()
        assert sheet.locator(".status").text_content() == "Found by its picture"
        # Finish: Foil, then Add: the list keeps the exact printing in foil
        sheet.locator("[data-finish=foil]").click()
        page.locator("#add-card").click()
        items = page.evaluate("() => window.__scan.state.items")
        assert len(items) == 1 and items[0]["status"] == "printing" and items[0]["foil"] is True, items
        assert items[0]["card"]["scryfall_id"] == "c21-263"
        # the card is still in view: it is not sent again
        sent = len(calls["identify"])
        page.wait_for_timeout(1500)  # fixed wait: a negative check (nothing more is sent)
        assert len(calls["identify"]) == sent
        # once it has left the frame and comes back, it is looked at again
        page.evaluate("() => { window.__noCard = true; }")
        page.wait_for_function("() => !window.__scan.state.holdUntilGone", timeout=10000)
        page.evaluate("() => { window.__noCard = false; }")
        page.wait_for_selector("#found", timeout=15000)
        assert len(calls["identify"]) == sent + 1
        assert errors == []
        browser.close()


def test_add_right_away_adds_sure_cards_with_undo_and_shows_unsure_ones(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        init = "try { localStorage.setItem('mtg-scan-mode', 'auto'); } catch (e) {}"
        browser, page, errors, calls = _page(p, exe, server, answers=[answer(SOL_C21)], init=init)
        assert page.locator(".scan-mode [aria-checked=true]").text_content() == "Add right away"
        page.wait_for_function("() => window.__scan.state.items.length === 1", timeout=15000)
        assert page.locator("#found").count() == 0
        toast = page.locator("#auto-toast")
        assert not toast.is_hidden() and "Added Sol Ring (C21 263)" in toast.text_content()
        toast.locator(".undo").click()
        assert page.evaluate("() => window.__scan.state.items.length") == 0
        # undone, the card is not added again while it stays in view
        page.wait_for_timeout(1500)  # fixed wait: a negative check (nothing is added)
        assert page.evaluate("() => window.__scan.state.items.length") == 0
        assert errors == []
        browser.close()


def test_an_unsure_answer_waits_for_the_looks_to_agree_and_is_never_added_on_its_own(
    server: Server,  # noqa: F811
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        init = "try { localStorage.setItem('mtg-scan-mode', 'auto'); } catch (e) {}"
        browser, page, errors, calls = _page(
            p, exe, server, answers=[answer(MIND_STONE, sure=False)], init=init
        )
        page.wait_for_selector("#found", timeout=15000)
        # two looks that agree show the card; it is not added, and the sheet says to check it
        assert len(calls["identify"]) == 2
        assert page.evaluate("() => window.__scan.state.items.length") == 0
        assert page.locator("#found .status").text_content() == "Best match: check it"
        # Not this card: the next closest cards, and Type the name
        page.locator("#found button", has_text="Not this card").click()
        page.locator("#found .others button", has_text="Mind Stone (C21)").click()
        assert page.locator("#found .status").text_content() == "Chosen by hand"
        page.locator("#add-card").click()
        items = page.evaluate("() => window.__scan.state.items")
        assert [i["card"]["name"] for i in items] == ["Mind Stone"] and items[0]["status"] == "printing"
        assert errors == []
        browser.close()


def test_printings_that_share_the_art_are_told_apart_by_the_set_and_number_line(
    server: Server,  # noqa: F811
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        same = [SOL_FRC, SOL_C21]
        browser, page, errors, calls = _page(p, exe, server, answers=[answer(SOL_C21, same=same)])
        page.evaluate("() => { window.__i = '0021 U\\nFRC • EN'; }")
        page.wait_for_selector("#found", timeout=15000)
        sheet = page.locator("#found")
        assert "FRC 21" in sheet.locator(".sheet-row.printing").text_content()
        assert sheet.locator(".same-art [aria-pressed=true]").text_content() == "FRC 21"
        assert page.locator("#add-card").text_content() == "Add"
        # the line unread: the sheet asks, and Add keeps the card by name only
        out = page.evaluate(
            """async ([a]) => { window.__i = '';
                 const flat = {canvas: document.createElement('canvas')};
                 const r = await window.__scan.pictureResult(a, flat, true, {});
                 return {doubt: r.printingDoubt, card: r.card.scryfall_id}; }""",
            [answer(SOL_C21, same=same)],
        )
        assert out == {"doubt": True, "card": "c21-263"}
        assert (
            page.evaluate("(c) => window.__scan.pickPrinting(c, {set: '', number: '263'}).scryfall_id", same)
            == "c21-263"
        )
        assert page.evaluate("(c) => window.__scan.pickPrinting(c, {set: 'xyz', number: ''})", same) is None
        assert errors == []
        browser.close()


@pytest.mark.parametrize("admin", [False, True], ids=["member", "admin"])
def test_a_miss_offers_to_keep_the_picture_for_the_admin(server: Server, admin: bool) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors, calls = _page(p, exe, server, answers=[answer(None)], admin=admin)
        # the text read is off for this test: OCR is not there to fall back on
        page.evaluate("() => { window.__scanOcrOverride = null; window.__scan.state.ocrState = 'failed'; }")
        page.wait_for_selector("#miss", timeout=20000)
        assert len(calls["identify"]) == 2  # two looks in a row that found nothing
        assert "whoever runs" not in page.content()
        page.locator("#keep-miss").click()
        kept = page.locator("#miss .kept")
        kept.wait_for(state="visible")
        page.wait_for_function("() => !/Keeping/.test(document.querySelector('#miss .kept').textContent)")
        words = kept.text_content()
        if admin:
            assert "Admin › Card scanner" in words, words
        else:
            assert "gateway’s admin" in words, words
        assert calls["miss"] == [{"note": "no match", "size": calls["miss"][0]["size"]}]
        assert calls["miss"][0]["size"] > 2000
        assert errors == []
        browser.close()


def test_the_scan_area_moves_resizes_and_is_kept_on_the_device(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors, calls = _page(p, exe, server, answers=[answer(SOL_C21)])
        page.evaluate("() => { window.__noCard = true; }")
        guide = page.locator("#scan-guide")
        before = guide.bounding_box()
        page.locator("#area-btn").click()
        assert not page.locator("#area-bar").is_hidden()
        guide.press("ArrowUp")
        guide.press("ArrowUp")
        guide.press("+")
        after = guide.bounding_box()
        assert after["y"] + after["height"] / 2 < before["y"] + before["height"] / 2 - 5, (before, after)
        assert after["width"] > before["width"], (before, after)
        # a drag of the corner grip resizes; the area never leaves the viewfinder
        grip = guide.locator(".grip").bounding_box()
        page.mouse.move(grip["x"] + 14, grip["y"] + 14)
        page.mouse.down()
        page.mouse.move(grip["x"] + 600, grip["y"] + 14, steps=4)
        page.mouse.up()
        cam = page.locator("#camera").bounding_box()
        big = guide.bounding_box()
        assert big["x"] >= cam["x"] - 3 and big["x"] + big["width"] <= cam["x"] + cam["width"] + 3, (big, cam)
        page.locator("#area-bar .done").click()
        kept = json.loads(page.evaluate("() => localStorage.getItem('mtg-scan-area')"))
        assert 0.25 <= kept["w"] <= 0.9 and kept["y"] < 0.5
        page.reload()
        page.wait_for_function("() => window.__scan && window.__scan.ownCamera")
        again = page.locator("#scan-guide").bounding_box()
        assert abs(again["width"] - big["width"]) < 2 and abs(again["y"] - big["y"]) < 2, (again, big)
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [344, 412, 1280])
@pytest.mark.parametrize("text", ["100%", "200%"])
def test_the_check_sheet_fits_at_any_width_and_text_size(server: Server, width: int, text: str) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        same = [SOL_FRC, SOL_C21]
        browser, page, errors, calls = _page(
            p, exe, server, answers=[answer(SOL_C21, same=same)], width=width
        )
        page.evaluate(f"() => {{ document.documentElement.style.fontSize = '{text}'; }}")
        page.wait_for_selector("#found", timeout=15000)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
        small = page.evaluate(
            """() => [...document.querySelectorAll('#found button, .scan-mode button, #shutter, #area-btn')]
                 .filter((b) => b.offsetParent)
                 .map((b) => { const r = b.getBoundingClientRect();
                               return [b.textContent.trim(), r.width, r.height]; })
                 .filter(([, w, h]) => w < 43.5 || h < 43.5)"""
        )
        assert small == [], small
        assert errors == []
        browser.close()
