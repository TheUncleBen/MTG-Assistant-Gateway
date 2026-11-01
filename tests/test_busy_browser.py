"""The busy answer as a member meets it, in headless Chromium against the real gateway (its
DatabaseWaitMiddleware with the write lock held): a save sent by a page's script, through
MtgFetch or a plain fetch, shows one plain note with Try again, which sends the same request
again once the lock is free, and nothing typed is cleared; a form posted the browser's own way
lands on the busy page in the site's look, whose Try again posts the same form again with what
was typed; a form with a file goes back to the filled-in form. Light and dark, phone and desktop.
Skipped without a Chromium build."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mtg_gateway import dbwait, theme

from .test_box_browser import _ctx, _exe
from .test_card_search_filters_browser import CONTRAST
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server

pytest.importorskip("playwright")

KIT_JS = """
(function () {
  "use strict";
  var out = document.getElementById("out");
  function body(form) { return new URLSearchParams(new FormData(form)).toString(); }
  document.getElementById("f1").addEventListener("submit", function (e) {
    e.preventDefault();
    var form = e.target;
    window.MtgFetch.press(document.getElementById("save"), function () {
      return window.MtgFetch.answer("/busy-kit/save", { body: body(form), form: true }).then(function (d) {
        out.textContent = d.ok ? "Saved " + d.note : "Not saved: " + d.message;
      });
    });
  });
  document.getElementById("raw").addEventListener("click", function () {
    fetch("/busy-kit/save", { method: "POST", body: body(document.getElementById("f1")),
      credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded" } })
      .then(function (r) { return r.json(); })
      .then(function (d) { out.textContent = d.ok ? "Raw saved " + d.note : "Raw not saved: " + d.message; });
  });
})();
"""


def _page_html() -> str:
    return (
        "<form id='f1' class='panel' data-draft='busy-kit' method='post' action='/busy-kit/save'>"
        "<div class='field'><label for='note'>Note</label><textarea id='note' name='note'></textarea></div>"
        "<button id='save' class='btn btn-primary'>Save</button>"
        "<button id='raw' type='button' class='btn'>Save the plain way</button></form>"
        "<p id='out'></p>"
        "<form id='f2' class='panel' data-draft='busy-kit-2' method='post' action='/busy-kit/sent'>"
        "<input type='hidden' name='csrf' value='x'>"
        "<div class='field'><label for='nm'>Name</label><input id='nm' name='name'></div>"
        "<button id='send' class='btn btn-primary'>Send</button></form>"
        "<form id='f3' class='panel' data-draft='busy-kit-3' method='post' action='/busy-kit/sent' "
        "enctype='multipart/form-data'>"
        "<div class='field'><label for='cap'>Caption</label><input id='cap' name='caption'></div>"
        "<input type='file' name='picture'>"
        "<button id='upload' class='btn btn-primary'>Upload</button></form>"
    )


class BusyServer(Server):
    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path)
        self.busy = False
        db = self.db
        # the write lock as the test says it is (test_db_lock_wait.py holds the real lock)
        db.write_free = lambda: not self.busy  # type: ignore[method-assign]
        wait = next(m for m in self.app.user_middleware if m.cls is dbwait.DatabaseWaitMiddleware)
        wait.kwargs["limit"] = 0.2

        async def kit(_request: Request):
            return theme.render(
                "Busy kit",
                _page_html(),
                site="MTG Gateway",
                signed_in=True,
                csrf="x",
                head_extra="<script src='/busy-kit/kit.js' defer></script>",
            )

        async def kit_js(_request: Request):
            return Response(KIT_JS, media_type="text/javascript")

        async def save(request: Request):
            form = await request.form()
            return JSONResponse({"ok": True, "note": str(form.get("note", ""))})

        async def sent(request: Request):
            form = await request.form()
            name = str(form.get("name") or form.get("caption") or "")
            body = f"<p id='sent'>Saved {name}</p>"
            return theme.render("Sent", body, site="MTG Gateway", signed_in=True, csrf="x")

        self.app.router.routes.insert(0, Route("/busy-kit", kit))
        self.app.router.routes.insert(0, Route("/busy-kit/kit.js", kit_js))
        self.app.router.routes.insert(0, Route("/busy-kit/save", save, methods=["POST"]))
        self.app.router.routes.insert(0, Route("/busy-kit/sent", sent, methods=["POST"]))


@pytest.fixture
def kit(tmp_path: Path):
    s = BusyServer(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


TOAST = ".toast.busy-toast[role=alert]"


def _shot(page, name: str) -> None:
    """A screenshot for a look, when MTG_SHOTS names a directory; nothing otherwise."""
    where = os.environ.get("MTG_SHOTS")
    if where:
        Path(where).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(where) / f"busy-{name}.png"), full_page=True)


@pytest.mark.parametrize(("width", "theme_name"), [(360, "light"), (1280, "dark")])
def test_a_scripts_save_shows_one_note_with_try_again_and_keeps_what_was_typed(
    kit: BusyServer, width: int, theme_name: str
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _ctx(p, kit, sid, width, theme_name=theme_name, exe=exe)
        page.goto(f"{kit.base}/busy-kit", wait_until="networkidle")
        page.fill("#note", "Keep this note")
        kit.busy = True
        page.click("#save")
        toast = page.locator(TOAST)
        toast.wait_for(timeout=5000)
        assert toast.locator(".msg").inner_text() == dbwait.BUSY_MESSAGE  # the plain words, never JSON
        again = toast.locator("button.btn", has_text="Try again")
        assert again.count() == 1
        bb = again.bounding_box()
        # a touch screen's 44 px; a desktop's pointer gets the site's own control height (theme.py --ctl)
        assert bb["height"] >= (44 if width < 900 else 39) and bb["width"] >= 44
        assert page.evaluate(CONTRAST, TOAST + " .msg, " + TOAST + " button") == []
        assert page.evaluate(MEASURE) == []  # nothing wider than the window
        assert page.locator("#note").input_value() == "Keep this note"  # nothing cleared
        assert page.locator("#out").inner_text() == ""  # the script is still waiting: no raw error
        _shot(page, f"fetch-{width}-{theme_name}")
        # still busy: Try again shows the same note again, with the text still in place
        again.click()
        page.locator(TOAST).wait_for(timeout=5000)
        assert page.locator("#note").input_value() == "Keep this note"
        kit.busy = False
        page.locator(TOAST + " button.btn", has_text="Try again").click()
        page.locator("#out", has_text="Saved Keep this note").wait_for(timeout=5000)
        assert page.locator(TOAST).count() == 0
        assert page.locator("#note").input_value() == "Keep this note"
        assert not page.locator("#save").is_disabled()
        assert errors == []
        browser.close()


def test_a_plain_fetch_that_bypasses_mtgfetch_gets_the_same_note_and_retry(kit: BusyServer) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _ctx(p, kit, sid, 412, theme_name="light", exe=exe)
        page.goto(f"{kit.base}/busy-kit", wait_until="networkidle")
        page.fill("#note", "Plain way")
        kit.busy = True
        page.click("#raw")
        toast = page.locator(TOAST)
        toast.wait_for(timeout=5000)
        assert page.locator("#out").inner_text() == ""
        kit.busy = False
        toast.locator("button.btn", has_text="Try again").click()
        page.locator("#out", has_text="Raw saved Plain way").wait_for(timeout=5000)
        assert page.locator("#note").input_value() == "Plain way"
        # dismissing the note instead hands the script the busy answer as it was
        kit.busy = True
        page.click("#raw")
        page.locator(TOAST).wait_for(timeout=5000)
        page.locator(TOAST + " .close").click()
        page.locator("#out", has_text="Raw not saved: " + dbwait.BUSY_MESSAGE).wait_for(timeout=5000)
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "theme_name"), [(360, "light"), (1280, "dark")])
def test_a_posted_form_lands_on_the_busy_page_whose_try_again_posts_it_again(
    kit: BusyServer, width: int, theme_name: str
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _ctx(p, kit, sid, width, theme_name=theme_name, exe=exe)
        page.goto(f"{kit.base}/busy-kit", wait_until="networkidle")
        page.fill("#nm", "Meren & co")
        kit.busy = True
        page.click("#send")
        page.wait_for_url(f"{kit.base}/busy-kit/sent", timeout=5000)
        page.locator("h1", has_text="The gateway is busy").wait_for(timeout=5000)
        assert page.locator("body").inner_text().count("{") == 0  # never raw JSON
        assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == theme_name
        bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        assert bg == ("rgb(249, 250, 251)" if theme_name == "light" else "rgb(24, 24, 24)")
        again = page.locator("form[method=post] button", has_text="Try again")
        assert again.count() == 1
        bb = again.bounding_box()
        assert bb["height"] >= 44 and bb["width"] >= 44
        assert page.evaluate(CONTRAST, "main p, main h1, main button") == []
        assert page.evaluate(MEASURE) == []
        assert page.locator("input[name=name]").get_attribute("value") == "Meren & co"
        _shot(page, f"form-{width}-{theme_name}")
        # still busy: the same page again, the name still with it
        again.click()
        page.locator("h1", has_text="The gateway is busy").wait_for(timeout=5000)
        assert page.locator("input[name=name]").get_attribute("value") == "Meren & co"
        kit.busy = False
        page.locator("form[method=post] button", has_text="Try again").click()
        page.locator("#sent", has_text="Saved Meren & co").wait_for(timeout=5000)
        assert errors == []
        browser.close()


def test_the_busy_page_at_200_percent_text_has_no_sideways_scroll(kit: BusyServer) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser, page, _errors = _ctx(p, kit, sid, 360, theme_name="dark", exe=exe)
        page.goto(f"{kit.base}/busy-kit", wait_until="networkidle")
        page.fill("#nm", "x")
        kit.busy = True
        page.click("#send")
        page.locator("h1", has_text="The gateway is busy").wait_for(timeout=5000)
        page.evaluate("() => { document.documentElement.style.fontSize = '200%'; }")
        assert page.evaluate(MEASURE) == []
        root = "document.documentElement"
        assert page.evaluate(f"() => {root}.scrollWidth <= {root}.clientWidth")
        browser.close()


def test_a_form_with_a_file_goes_back_to_the_filled_in_form(kit: BusyServer) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _ctx(p, kit, sid, 412, theme_name="light", exe=exe)
        page.goto(f"{kit.base}/busy-kit", wait_until="networkidle")
        page.fill("#cap", "A caption")
        kit.busy = True
        page.click("#upload")
        page.locator("h1", has_text="The gateway is busy").wait_for(timeout=5000)
        back = page.locator("button[data-busy-back]", has_text="Go back")
        assert back.count() == 1 and "go back to what you typed" in page.locator("main").inner_text().lower()
        back.click()
        page.wait_for_url(f"{kit.base}/busy-kit", timeout=5000)
        page.locator("#cap").wait_for(timeout=5000)
        page.wait_for_function("() => document.getElementById('cap').value === 'A caption'", timeout=5000)
        kit.busy = False
        page.click("#upload")
        page.locator("#sent", has_text="Saved A caption").wait_for(timeout=5000)
        assert errors == []
        browser.close()


def test_the_busy_page_is_also_the_answer_in_the_android_app(kit: BusyServer) -> None:
    """The app's WebView carries the app's user agent; the page is the same, and the app's own
    error box stays away (test_android_shell_rules.py: the X-MTG-Busy header)."""
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        ua = "Mozilla/5.0 (Linux; Android 14) MTGAssistant/0.7.46"
        browser, page, errors = _ctx(p, kit, sid, 412, theme_name="dark", exe=exe, user_agent=ua)
        page.goto(f"{kit.base}/busy-kit", wait_until="networkidle")
        page.fill("#nm", "From the app")
        kit.busy = True
        with page.expect_response(f"{kit.base}/busy-kit/sent") as got:
            page.click("#send")
        assert got.value.status == 503 and got.value.headers.get("x-mtg-busy") == "1"
        page.locator("h1", has_text="The gateway is busy").wait_for(timeout=5000)
        kit.busy = False
        page.locator("form[method=post] button", has_text="Try again").click()
        page.locator("#sent", has_text="Saved From the app").wait_for(timeout=5000)
        assert errors == []
        browser.close()
