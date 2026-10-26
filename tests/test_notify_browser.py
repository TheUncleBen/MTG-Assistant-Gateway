"""The Notifications card's phone part and the account menu's App buttons inside the Android app,
in a real browser with a stand-in for the app's bridge (window.MtgNative). Android's bridge does
nothing for a method called on anything but the bridge object itself, so the stand-in refuses
such calls the same way."""

from __future__ import annotations

from pathlib import Path

import pytest

from mtg_gateway import notify, theme

from .test_consent_browser import _chromium_or_skip, _launch

pytest.importorskip("playwright")

FEEDBACK_JS = (Path(notify.__file__).parent / "static" / "feedback.js").read_text()

BRIDGE = """
window.calls = [];
window.MtgNative = {};
["openCamera", "reload", "changeGateway", "startNotifications"].forEach(function (name) {
  window.MtgNative[name] = function (arg) {
    if (this !== window.MtgNative) throw new Error("not called on the bridge");
    window.calls.push(arg === undefined ? name : name + ":" + arg);
  };
});
window.MtgNative.notificationsState = function () {
  if (this !== window.MtgNative) throw new Error("not called on the bridge");
  return window.phoneState;
};
"""

MENU = (
    "<button type='button' data-native='openCamera'>Scan with the app's camera</button>"
    "<button type='button' data-native='reload'>Reload</button>"
    "<button type='button' data-native='changeGateway'>Change gateway</button>"
)


def _page(p, exe: str | None, body: str, state: str | None):
    browser = _launch(p, exe)
    page = browser.new_page()
    if state is None:
        page.add_init_script(BRIDGE.replace("window.MtgNative.notificationsState", "window.oldApp"))
    else:
        page.add_init_script(f"window.phoneState = {state!r};" + BRIDGE)
    html = f"<!doctype html><style>{theme.CSS}</style><main>{body}</main><script>{FEEDBACK_JS}</script>"
    page.route("https://mtg.test/**", lambda route: route.fulfill(content_type="text/html", body=html))
    page.goto("https://mtg.test/account")
    return browser, page


def test_each_app_button_reaches_the_app() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser, page = _page(p, exe, MENU, "off")
        try:
            for label in ("Scan with the app's camera", "Reload", "Change gateway"):
                page.get_by_role("button", name=label).click()
            assert page.evaluate("window.calls") == ["openCamera", "reload", "changeGateway"]
        finally:
            browser.close()


@pytest.mark.parametrize(
    ("state", "button", "shown"),
    [
        ("connected", False, "Always connected"),
        ("off", True, "doesn't get your notifications yet"),
        ("blocked", True, "turned off for this app"),
        (None, False, "Update the app"),  # an app from before 0.7.30
    ],
)
def test_the_phone_part_shows_only_what_applies(
    state: str | None, button: bool, shown: str, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from playwright.sync_api import sync_playwright

    from mtg_gateway.db import Database

    db = Database(tmp_path / "gw.sqlite")
    key = notify.new_topic(db, "user-1")
    html = notify._phone_html(SimpleNamespace(db=db), "user-1", {"ntfy_topic": key})  # type: ignore[arg-type]
    db.close()
    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser, page = _page(p, exe, html, state)
        try:
            assert page.get_by_role("button", name="Get notifications on this phone").is_visible() is button
            assert page.get_by_text(shown, exact=False).first.is_visible()
            visible = [t for t in page.locator(".phone > p").all() if t.is_visible()]
            assert len(visible) == 1
            if button:
                page.get_by_role("button", name="Get notifications on this phone").click()
                assert page.evaluate("window.calls") == [f"startNotifications:{key}"]
        finally:
            browser.close()
