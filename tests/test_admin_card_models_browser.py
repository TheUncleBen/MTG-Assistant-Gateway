"""The admin Card models page in headless Chromium at 344, 390, 810 and 1280 px, light and dark:
nothing scrolls sideways, text reads at 4.5:1 or better, the ⋮ is big enough to tap, and Remove
lives only in the row's menu (the ⋮, a right-click or a press and hold), which opens the "are you
sure" box whose solid red button removes the model and says so. Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_overflow_sweep_browser import MEASURE
from .test_settings_page_browser import _admin, _shot
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

SIZES = [(344, "light"), (390, "dark"), (810, "light"), (810, "dark"), (1280, "light"), (1280, "dark")]
TEXT = (
    ".amo .ui-table .t, .amo .ui-table .sub, .amo .ui-table .sm-only, .amo .wdg .lab, .amo .wdg .big, "
    ".amo .wdg .det, .amo .list-head, .amo .ui-pager, .amo .btn, .ctxmenu [role=menuitem], "
    ".mtgbox h2, .mtgbox p, .mtgbox .acts button, .mtgbox .acts a"
)


# a finger's press and hold on an element (the pointer events a touch screen sends)
HOLD = """
(sel) => {
  const el = document.querySelector(sel), r = el.getBoundingClientRect();
  const at = { bubbles: true, pointerType: 'touch', clientX: r.x + 20, clientY: r.y + r.height / 2 };
  el.dispatchEvent(new PointerEvent('pointerdown', at));
  setTimeout(() => el.dispatchEvent(new PointerEvent('pointerup', at)), 600);
}
"""


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path, admin_group="mtg-admins")
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _models(server: Server, *, long: bool = False) -> None:
    server.db.upsert_user(
        "user-9",
        email=None,
        name=None,
        preferred_username="a_member_with_a_really_long_username_here" if long else "bram",
        groups=[],
    )
    names = ["Sol Ring", "Kenrith, the Returned King", "Asmoranomardicadaistinaculdacar", "Opt"]
    for i, name in enumerate(names):
        server.db.put_card_model(name.casefold(), name, "{}", "user-9", 100 + i)


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_card_models_page_fits_reads_and_removes_from_the_row_menu(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    _models(server, long=width == 1280)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/admin/models", wait_until="networkidle")
        rows = page.locator(".amo .ui-table .tr.model")
        assert rows.count() == 4
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        # no Remove on the page itself: it is in the row's menu only
        assert page.locator(".amo .ui-table").get_by_text("Remove").count() == 0
        sizes = page.evaluate(
            "() => [...document.querySelectorAll('.amo .ui-table .kebab')]"
            ".map(b => b.getBoundingClientRect().height)"
        )
        assert min(sizes) >= 43.5, sizes
        _shot(page, f"admin-models-{width}-{theme}.png")
        # the ⋮ opens the menu: the member's models, the card, then Remove… in red
        row = rows.filter(has_text="Opt")
        row.locator(".kebab").click()
        menu = page.locator(".ctxmenu")
        assert menu.is_visible()
        items = [t.strip() for t in menu.locator("[role=menuitem]").all_inner_texts()]
        assert items[-1] == "Remove…" and any(t.startswith("Show ") and "models only" in t for t in items)
        assert "Open card" in items
        assert page.evaluate(MEASURE) == []
        page.keyboard.press("Escape")
        assert menu.count() == 0
        # a right-click opens the same menu
        row.click(button="right")
        assert page.locator(".ctxmenu").is_visible()
        page.locator(".ctxmenu [role=menuitem]", has_text="Remove…").click()
        box = page.locator("#remove")
        box.wait_for(state="visible")  # Remove… is a link: the box is on the page it opens
        assert box.is_visible() and box.get_attribute("role") == "dialog"
        assert "Remove the model of “Opt”?" in box.inner_text()
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        _shot(page, f"admin-models-box-{width}-{theme}.png")
        last = box.locator(".acts button, .acts a").last
        assert last.inner_text().strip() == "Remove model"
        assert "danger" in (last.get_attribute("class") or "")
        last.click()
        page.wait_for_load_state("networkidle")
        assert "Card model removed." in page.locator(".notice.ok").inner_text()
        assert page.locator(".amo .ui-table .tr.model").count() == 3
        assert page.locator("#remove").count() == 0
        browser.close()
    assert errors == []


def test_cancel_and_the_close_link_leave_the_model_alone(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    _models(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        page.goto(f"{server.base}/admin/models?remove=opt&by=user-9", wait_until="networkidle")
        box = page.locator("#remove")
        assert box.is_visible()
        box.locator(".acts button", has_text="Cancel").click()
        page.wait_for_selector("#remove", state="hidden")
        assert page.url.endswith("/admin/models")  # the address no longer names the box
        assert page.locator(".amo .ui-table .tr.model").count() == 4
        # a press and hold on a row opens the menu on a touch screen
        page.evaluate(HOLD, "#cm-0 .title")
        page.wait_for_timeout(700)
        assert page.locator(".ctxmenu").count() == 1
        assert page.locator(".ctxmenu [role=menuitem]", has_text="Remove…").count() == 1
        browser.close()
    assert errors == []


@pytest.mark.parametrize(("width", "zoom"), [(344, "root"), (344, "text"), (390, "text")])
def test_the_card_models_page_fits_a_phone_at_200_percent_text(server: Server, width: int, zoom: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    _models(server, long=True)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        page.goto(f"{server.base}/admin/models", wait_until="networkidle")
        page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        _shot(page, f"admin-models-{width}-{zoom}200.png")
        browser.close()
    assert errors == []
