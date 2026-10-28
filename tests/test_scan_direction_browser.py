"""The scanner's direction (decision C9), on the /scan page in headless Chromium through its real code.

- Auto with no card outline: after a few frames without one the dashed box is read as it is, and a
  card found that way is only ever offered as "unsure" for a tap, never added on its own.
- A collector line that names another set than the card found (a misread set code such as "FERC",
  or a set lock the card is not in) never adds the card on its own either.
- In the Android app the orange shutter, the viewfinder and the Auto switch open the phone's own
  camera; the page's own camera is not started there.
- A running count of the scan (and its value, once the gateway prices scanned cards) sits over the
  viewfinder while scanning.

Text recognition is stood in for (the read is whatever the test says), so the rules are tested,
not Tesseract. Skipped without a Chromium build.
"""

from __future__ import annotations

import pytest

from .test_scan_browser import Server, _chromium_path, server  # noqa: F401

pytest.importorskip("playwright")

# A phone camera pointed at a bare table: a canvas stream the page's own camera code receives.
TABLE_CAMERA = """
navigator.mediaDevices.getUserMedia = async () => {
  window.__cameraOpened = (window.__cameraOpened || 0) + 1;
  const c = document.createElement('canvas'); c.width = 640; c.height = 480;
  const x = c.getContext('2d');
  const draw = () => { x.fillStyle = '#b0926c'; x.fillRect(0, 0, 640, 480); };
  draw(); setInterval(draw, 100);
  return c.captureStream(10);
};
"""
STAND_IN_OCR = """() => {
  window.__t = 'Sol Ring'; window.__c = 95; window.__i = '';
  window.__scanOcrOverride = async (canvas, kind) => kind === 'info'
    ? {text: window.__i, confidence: window.__i ? 90 : 0, words: []}
    : {text: window.__t, confidence: window.__c, words: []};
  window.__events = [];
  ['auto-added', 'unsure', 'unidentified'].forEach((n) =>
    document.addEventListener('scan:' + n, () => window.__events.push(n)));
  const s = window.__scan.state; s.items = []; s.unidentified = [];
}"""


def _chromium() -> str | None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _page(p, exe, srv: Server, *, init: str = "", width: int = 412):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": width, "height": 915}, is_mobile=True, has_touch=True)
    ctx.add_cookies([{"name": "mtg_session", "value": srv.sign_in(), "url": srv.base}])
    if init:
        ctx.add_init_script(init)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{srv.base}/scan")
    page.wait_for_function("() => window.__scan && window.__scan.tierOf")
    return browser, page, errors


def test_auto_reads_the_dashed_box_when_it_finds_no_outline_and_never_adds_from_it(
    server: Server,  # noqa: F811
) -> None:
    """The real Auto loop on a camera that shows no card outline (a bare table): the first frames
    read nothing, then the dashed box is read, and the card found there waits for a tap."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, exe, server, init=TABLE_CAMERA)
        page.wait_for_function("() => (document.querySelector('#camera video') || {}).videoWidth > 0")
        page.evaluate(STAND_IN_OCR)
        assert page.evaluate("() => window.__scan.startContinuous()") is True
        page.wait_for_function("() => window.__scan.state.pending", timeout=60_000)
        s = page.evaluate(
            """() => { const s = window.__scan.state;
                 return {items: s.items.length, name: s.pending.card.name, guide: !!s.pending.ocr.guide,
                         frames: s.noCardFrames, lookups: s.stats.lookups,
                         unidentified: s.unidentified.length,
                         status: document.querySelector('#cam-status').textContent,
                         note: document.querySelector('#result').textContent, events: window.__events}; }"""
        )
        # A clean, unambiguous read (Sol Ring at 95%) that a card outline would have added at once.
        assert s["name"] == "Sol Ring" and s["guide"] is True
        assert s["items"] == 0 and s["events"] == ["unsure"], s
        assert s["frames"] >= 4 and s["lookups"] == 1 and s["unidentified"] == 0, s
        assert "edges" in s["status"] and "dashed box" in s["status"], s["status"]
        assert "Read from the dashed box" in s["note"], s["note"]
        # The member confirms it with Add; the loop carries on and does not offer the same card again.
        page.locator("#result button.add").click()
        items = page.evaluate("() => window.__scan.state.items.map((i) => [i.name, i.quantity])")
        assert items == [["Sol Ring", 1]]
        page.wait_for_function("() => window.__scan.state.noCardFrames >= 9", timeout=60_000)
        assert page.evaluate("() => window.__scan.state.pending") is None
        assert page.evaluate("() => window.__events") == ["unsure"]
        assert page.locator("#cam-status").text_content().startswith("Still Sol Ring")
        page.evaluate("() => window.__scan.stopContinuous()")
        assert errors == []
        browser.close()


def test_reading_without_an_outline_offers_each_card_once_and_keeps_no_rubbish(
    server: Server,  # noqa: F811
) -> None:
    """The rules of a read without an outline, frame by frame through the hook the app's glue uses."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, exe, server)
        page.evaluate(STAND_IN_OCR)
        page.evaluate("() => { window.__scan.state.continuous = true; }")

        def frame(text: str = "Sol Ring", conf: float = 95, info: str = "") -> dict:
            return page.evaluate(
                """async ([t, c, i]) => { window.__t = t; window.__c = c; window.__i = i;
                     const cv = document.createElement('canvas'); cv.width = 630; cv.height = 880;
                     const x = cv.getContext('2d'); x.fillStyle = '#b0926c'; x.fillRect(0, 0, 630, 880);
                     const r = await window.__scan.noOutlineFrame(cv, {x: 0, y: 0, w: 630, h: 880});
                     const s = window.__scan.state;
                     return {r, frames: s.noCardFrames, lookups: s.stats.lookups,
                             pending: s.pending && s.pending.card ? s.pending.card.name : null,
                             items: s.items.length, unidentified: s.unidentified.length,
                             status: document.querySelector('#cam-status').textContent}; }""",
                [text, conf, info],
            )

        def clear() -> None:
            page.evaluate("() => { window.__scan.state.pending = null; }")

        # A few frames without an outline read nothing and ask the server nothing.
        for n in (1, 2, 3):
            out = frame()
            assert out["r"] == "no-card" and out["frames"] == n and out["lookups"] == 0, out
        # The fourth reads the dashed box: a clean read with a collector line that agrees is still
        # only offered.
        out = frame(info="0021 U\nFRC • EN")
        assert out["r"] == "read" and out["pending"] == "Sol Ring" and out["items"] == 0, out
        clear()
        # Every fourth frame after that; the same card is not offered twice in a row.
        for _ in range(3):
            assert frame()["r"] == "no-card"
        out = frame()
        assert out["r"] == "read" and out["pending"] is None, out
        assert out["status"].startswith("Still Sol Ring"), out
        # Nothing readable in the box (the card has gone): nothing kept, the hint says what to do.
        for _ in range(3):
            frame("")
        out = frame("")
        assert out["r"] == "no-card" and out["unidentified"] == 0, out
        assert "edges" in out["status"] and "background" in out["status"], out["status"]
        # A read no card matches (the table's grain read as letters) is not kept either.
        for _ in range(3):
            frame("Xq Zzyx")
        out = frame("Xq Zzyx")
        assert out["r"] == "no-card" and out["unidentified"] == 0 and out["pending"] is None, out
        # Nor is a shaky read looked up.
        lookups = out["lookups"]
        for _ in range(4):
            out = frame("Sol Ring", 30)
        assert out["r"] == "no-card" and out["lookups"] == lookups and out["unidentified"] == 0, out
        # After a frame with nothing on it the same card is offered again.
        for _ in range(4):
            out = frame()
        assert out["r"] == "read" and out["pending"] == "Sol Ring" and out["items"] == 0, out
        assert page.evaluate("() => window.__events") == ["unsure", "unsure"]
        assert errors == []
        browser.close()


def test_a_collector_line_naming_another_set_never_adds_on_its_own(server: Server) -> None:  # noqa: F811
    """A clean title with a collector line whose set the card is not in (an info strip misread as
    "FERC", or a set lock the card is not in) waits for a tap: the line was read wrong somewhere,
    and with it the printing and the foil star."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, exe, server)
        page.evaluate(STAND_IN_OCR)
        page.evaluate("() => { window.__scan.state.continuous = true; }")

        def scan(info: str, lock: str = "") -> dict:
            return page.evaluate(
                """async ([i, lock]) => { window.__i = i; window.__scan.setSetLock(lock);
                     const s = window.__scan.state; s.pending = null; s.lastAutoKey = null; s.lastRead = null;
                     const cv = document.createElement('canvas'); cv.width = 400; cv.height = 100;
                     const title = {x: 0, y: 0, w: 400, h: 60}, info = {x: 0, y: 60, w: 200, h: 40};
                     await window.__scan.scanRegion(cv, title, info, null);
                     const card = s.pending && s.pending.card;
                     return {items: s.items.length, pending: card ? card.name : null,
                             set: card ? card.set : null}; }""",
                [info, lock],
            )

        out = scan("0021 U\nFERC • EN")
        assert out["items"] == 0 and out["pending"] == "Sol Ring", out
        out = scan("", "mh2")
        assert out["items"] == 0 and out["pending"] == "Sol Ring" and out["set"] != "mh2", out
        # The line read right is still certain.
        out = scan("0021 U\nFRC • EN")
        assert out["items"] == 1 and out["pending"] is None, out
        assert errors == []
        browser.close()


APP_BRIDGE = """
window.__calls = [];
window.MtgNative = {
  openCamera: function () { window.__calls.push('openCamera'); },
  openCameraAuto: function () { window.__calls.push('openCameraAuto'); },
  version: function () { return 'test'; },
};
"""


@pytest.mark.parametrize("app", ["app", "older-app", "browser"])
def test_in_the_app_the_shutter_and_auto_open_the_phone_camera(server: Server, app: str) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    older = APP_BRIDGE.replace("openCameraAuto", "notThere")
    bridge = {"app": APP_BRIDGE, "older-app": older, "browser": ""}[app]
    with sync_playwright() as p:
        browser, page, errors = _page(p, exe, server, init=TABLE_CAMERA + bridge)
        page.wait_for_function("() => window.__scan.state.ocrState !== 'idle'")
        page.wait_for_timeout(300)
        opened = page.evaluate("() => window.__cameraOpened || 0")
        if app == "browser":
            # A browser keeps its own camera, and the shutter reads the frame.
            assert opened == 1 and page.locator("#camera video").count() == 1
            assert page.locator("#phone-camera, #open-phone-camera").count() == 0
            assert page.evaluate("() => window.__calls") is None
            browser.close()
            return
        # The app: the page's own camera never starts; the shutter is the phone camera.
        assert opened == 0 and page.locator("#camera video").count() == 0
        assert page.locator("#phone-camera").count() == 0  # the shutter does its job now
        shutter = page.locator("#shutter")
        assert shutter.get_attribute("aria-label") == "Scan with the phone camera"
        assert "phone camera" in page.locator("#cam-hint").text_content()
        shutter.click()
        # The viewfinder itself is one big way in.
        page.locator("#open-phone-camera").click()
        box = page.locator("#open-phone-camera").bounding_box()
        assert box["height"] >= 44 and box["width"] >= 44
        # Auto opens the phone camera scanning on its own; an older app opens it for the person to
        # switch Auto on there.
        page.locator("#auto-switch").click()
        assert page.locator("#auto-switch").get_attribute("aria-checked") == "false"
        auto = "openCameraAuto" if app == "app" else "openCamera"
        assert page.evaluate("() => window.__calls") == ["openCamera", "openCamera", auto]
        assert page.evaluate("() => window.__scan.state.continuous") is False
        assert errors == []
        browser.close()


def test_a_running_count_shows_over_the_viewfinder(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, exe, server, init=TABLE_CAMERA)
        page.evaluate("() => { window.__scan.state.items = []; window.__scan.showTab('camera'); }")
        tally = page.locator("#scan-tally")
        assert tally.is_hidden()  # nothing scanned yet
        card = "{card: {name: 'Sol Ring', scryfall_id: 'x1', set: 'frc', collector_number: '21'}}"
        page.evaluate(f"() => window.__scan.addItem({card}, 1)")
        assert tally.is_visible() and tally.text_content().strip() == "1 card"
        page.evaluate(f"() => window.__scan.addItem({card}, 2)")
        assert tally.text_content().strip() == "3 cards"
        assert tally.get_attribute("aria-label") == "3 cards in this scan. Open the list"
        b = tally.bounding_box()
        assert b["height"] >= 44
        # Inside the viewfinder's top corner, clear of the dashed name box.
        cam = page.locator("#camera").bounding_box()
        name = page.locator("#camera .guide .title").bounding_box()
        assert cam["x"] <= b["x"] and b["y"] >= cam["y"] and b["y"] + b["height"] <= name["y"], (b, name)
        # The value joins the count once the gateway prices scanned cards (decision C5's hook).
        page.evaluate("() => { window.__scan.listValue = () => ({amount: 12.5, currency: 'USD'}); }")
        page.evaluate(f"() => window.__scan.addItem({card}, 1)")
        assert tally.text_content().strip() == "4 cards · $12.50"
        tally.click()
        assert page.evaluate("() => window.__scan.state.tab") == "list"
        assert errors == []
        browser.close()
