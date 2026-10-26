"""The UI standard's shared pieces (ui.py, theme.UI_CSS, static/feedback.js) in headless Chromium,
on a small page built from them: no sideways scroll, 44 px touch targets, label contrast in both
themes, the table's three layouts (900 / 600 / below 600 px), the side panel beside the list from
1100 px and its own page below, the sticky selection bar above the phone's tab bar, and the list
dates in the viewer's own time zone. Skipped without a Chromium build."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.routing import Route

from mtg_gateway import theme, ui

from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

COLS = "32px minmax(0,1fr) 7.5rem 8rem 6.5rem 6.5rem 5rem"
COLS_MD = "32px minmax(0,1fr) 8rem 5rem"
COLS_SM = "32px minmax(0,1fr) 44px"
KEYS = list(ui.LABELS)


def _row(i: int, now: float) -> str:
    key = KEYS[i % len(KEYS)]
    risk = ui.RISK_KEYS[i % len(ui.RISK_KEYS)]
    app = (None, "Claude", "ChatGPT")[i % 3]
    status = ui.label_for(key)
    return (
        "<div class='tr'>"
        + ui.check_cell("id", f"p{i}", f"#{i} change")
        + f"<span class='title'><span class='t'><span class='no'>#{i}</span>+1 Arcane Signet, "
        f"−1 Counterspell<span class='stin'>{status}</span></span>"
        f"<span class='sub'>{ui.risk_label(risk)}{ui.app_chip(app)}{ui.when(now - i * 7200)}</span></span>"
        f"<span class='md-hide'>{ui.risk_label(risk)}</span>"
        f"<span class='sm-hide'>{status}</span>"
        f"<span class='md-hide'>{ui.app_chip(app)}</span>"
        f"<span class='md-hide'>{ui.when(now - i * 7200)}</span>"
        + ui.open_link(f"/ui-kit/{i}", "Review")
        + "</div>"
    )


def _list(now: float) -> str:
    plain = ui.button_link("Show them", "/ui-kit?show=press")
    rows = "".join(_row(i, now) for i in range(1, 26))
    return (
        ui.page_head("Proposals", "Every change your assistants proposed, waiting and past.")
        + ui.widgets(
            [
                ui.widget(
                    "Waiting for you",
                    "clock",
                    8,
                    unit="waiting",
                    detail="6 on 4 decks",
                    tone="accent",
                    actions=[plain],
                ),
                ui.widget("Needs a look", "alert", 1, detail="Sol Ring changed", tone="alert"),
                ui.widget(
                    "Always need your press",
                    "hand",
                    2,
                    detail_html=ui.label_for("your_press", word="Your press 1")
                    + ui.label_for("delete", word="Delete 1"),
                ),
                ui.widget("Last 7 days", "check", 12, unit="applied", detail="4 rejected"),
                ui.widget(
                    "Waiting by app",
                    "link",
                    "5 Claude",
                    wide_sm=True,
                    actions=[ui.button_link("Connected apps", "/apps")],
                ),
            ]
        )
        + ui.list_head("All proposals", 51, "Waiting ones are grouped by deck")
        + ui.chips(
            [
                ui.Chip("Waiting", 8, "/ui-kit?show=waiting", True),
                ui.Chip("Needs a look", 1, "/ui-kit?show=look"),
                ui.Chip("Expired, withdrawn, replaced", 5, "/ui-kit?show=closed"),
                ui.Chip("All", 51, "/ui-kit?show=all"),
            ]
        )
        + ui.toolbar(
            "/ui-kit",
            search_label="Search proposals",
            placeholder="Search deck or card",
            filters=[
                ui.filter_select("deck", "Deck", [("", "All decks"), ("1", "Meren")]),
                ui.filter_select("risk", "Risk", [("", "Any risk"), ("low", "Low")]),
                ui.filter_select("app", "From", [("", "Any app"), ("claude", "Claude")]),
            ],
            keep={"show": "waiting"},
        )
        + ui.table_open(COLS, COLS_MD, COLS_SM, label="Proposals")
        + ui.table_header(
            [
                ui.Column(
                    "", html="<label class='chk'><input type='checkbox' aria-label='Check all'></label>"
                ),
                ui.Column("Change"),
                ui.Column("Risk", "md-hide"),
                ui.Column("Status", "sm-hide"),
                ui.Column("From", "md-hide"),
                ui.Column("", "md-hide", html=f"<button type='button'>Made {theme.icon('sort')}</button>"),
                ui.Column(""),
            ]
        )
        + ui.group_row("<a href='/decks/1'>Meren Graveyard Value</a>", "25 waiting")
        + ui.day_row("Today")
        + rows
        + "</div>"
        + "<details class='rv-det'><summary>Show the details</summary><p>Details</p></details>"
        + ui.table_open("minmax(0,1fr) 6.5rem", "minmax(0,1fr) 6.5rem", "1fr", cls="act-table")
        + "<div class='tr ev'><span class='c-ev'><span class='evt'>"
        + ui.label_for("refused")
        + f"<span class='en'>{theme.icon('shield')}Approval refused</span></span></span>"
        + f"<span class='c-when'>{ui.when(now - 3600)}</span></div></div>"
        + ui.pager(
            0, 51, newer_href=None, older_href="/ui-kit?page=2", note="past proposals are kept 30 days"
        )
        + ui.selection_bar(
            "2 checked on 1 deck",
            [
                "<button class='btn-plain' type='button'>Clear</button>",
                "<button class='btn-outline-danger' type='button'>Reject 2 changes</button>",
                "<button class='btn-primary' type='button'>Apply 2 changes</button>",
            ],
            label="Checked changes",
        )
    )


def _panel(i: int, now: float) -> str:
    body = (
        f"<div class='pills'>{ui.status_label('waiting')}{ui.risk_label('high')}</div>"
        + ui.meta([("From", ui.app_chip("Claude")), ("Made", ui.when(now - 3 * 3600))])
        + "<ul class='ui-chg'><li class='add'><span class='k'>ADD</span><span class='n'>Arcane Signet</span>"
        "<span class='d'>+1</span></li></ul>"
        + "<div class='ui-actions'><button class='btn-outline-danger' type='button'>Reject</button>"
        "<button class='btn-primary' type='button'>Apply this change</button></div>"
    )
    return ui.side_panel(
        f"#{i} +1 Arcane Signet",
        body,
        label=f"Proposal #{i}",
        close_href="/ui-kit",
        back_text="Back to Proposals",
    )


class KitServer(Server):
    """The test gateway with one page made only of the shared pieces (/ui-kit, /ui-kit/{i})."""

    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path)
        self.now = time.time()

        async def kit(request: Request):
            i = request.path_params.get("i")
            panel = _panel(int(i), self.now) if i else ""
            return theme.render(
                "UI kit",
                ui.split(_list(self.now), panel),
                site="MTG Gateway",
                signed_in=True,
                csrf="x",
                heading=False,
                main_class="ui-wide",
            )

        self.app.router.routes.insert(0, Route("/ui-kit", kit))
        self.app.router.routes.insert(0, Route("/ui-kit/{i:int}", kit))


@pytest.fixture
def kit(tmp_path: Path):
    s = KitServer(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


SIZES = [(390, 844, True), (344, 882, True), (673, 841, True), (1280, 800, False)]

# Text contrast of each label against what it sits on, compositing translucent backgrounds.
CONTRAST_JS = """
() => {
  const parse = c => { const m = c.match(/[\\d.]+/g).map(Number);
    return [m[0], m[1], m[2], m.length > 3 ? m[3] : 1]; };
  const over = (top, under) => { const a = top[3];
    return [0,1,2].map(i => top[i]*a + under[i]*(1-a)).concat(1); };
  const lum = c => { const v = c.slice(0,3).map(x => { x /= 255;
      return x <= .03928 ? x/12.92 : ((x+.055)/1.055)**2.4; });
    return .2126*v[0] + .7152*v[1] + .0722*v[2]; };
  const bgOf = el => { const stack = []; for (let n = el; n; n = n.parentElement) {
      const b = parse(getComputedStyle(n).backgroundColor);
      if (b[3] > 0) stack.push(b); if (b[3] >= 1) break; }
    let c = [255,255,255,1]; for (let i = stack.length - 1; i >= 0; i--) c = over(stack[i], c); return c; };
  return [...document.querySelectorAll('.ui-lbl, .ui-app, .ui-when, .ui-chip, .wdg .lab, .ui-pager .info')]
    .filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden')
    .map(el => { const bg = bgOf(el); const fg = over(parse(getComputedStyle(el).color), bg);
      const a = lum(fg), b = lum(bg); return [el.className + ':' + el.textContent.trim().slice(0, 20),
      (Math.max(a, b) + .05) / (Math.min(a, b) + .05)]; });
}
"""

TARGETS_JS = """
() => [...document.querySelectorAll(
    'main a.btn, main button, main .ui-chip, main .linkbtn, main select, main .msel-btn, '
    + 'main input[type=search], main .chk, main .close, main details > summary')]
  .filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden'
    && !el.closest('noscript') && el.offsetParent !== null && !el.classList.contains('msel-native'))
  .map(el => { const r = el.getBoundingClientRect();
    return [el.tagName + '.' + el.className + ':' + el.textContent.trim().slice(0, 20), r.width, r.height]; })
"""


def _exe() -> str | None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _browser(p, exe: str | None):
    return p.chromium.launch(executable_path=exe, args=["--no-sandbox"])


def _ctx(browser, kit: KitServer, sid: str, w: int, h: int, touch: bool, scheme: str, **kw):
    extra = {"has_touch": True, "is_mobile": True} if touch else {}
    ctx = browser.new_context(
        viewport={"width": w, "height": h}, color_scheme=scheme, reduced_motion="reduce", **extra, **kw
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": kit.base}])
    return ctx


def test_components_fit_touch_and_read_at_every_size(kit: KitServer) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        for scheme in ("light", "dark"):
            for w, h, touch in SIZES:
                ctx = _ctx(browser, kit, sid, w, h, touch, scheme)
                page = ctx.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                for path in ("/ui-kit", "/ui-kit/3"):
                    page.goto(kit.base + path, wait_until="networkidle")
                    where = (scheme, w, path)
                    # no sideways scroll
                    sw = page.evaluate("document.documentElement.scrollWidth")
                    assert sw <= w, (where, sw)
                    # label and small text contrast at least 4.5:1
                    texts = page.evaluate(CONTRAST_JS)
                    assert len([n for n, _ in texts if n.startswith("ui-lbl")]) >= (
                        10 if path == "/ui-kit" else 2
                    ), where
                    for name, ratio in texts:
                        assert ratio >= 4.5, (where, name, round(ratio, 2))
                    if touch:
                        assert page.evaluate("matchMedia('(pointer:coarse)').matches")
                        targets = page.evaluate(TARGETS_JS)
                        assert len(targets) >= (20 if path == "/ui-kit" else 2), (where, targets)
                    if path == "/ui-kit":  # the Activity pages' short label beside the event's name
                        h = page.evaluate(
                            "document.querySelector('.act-table .evt .ui-lbl').getBoundingClientRect().height"
                        )
                        assert abs(h - 22) < 0.5, (where, h)
                        for name, tw, th in targets:
                            assert th >= 44 - 0.5 and tw >= 44 - 0.5, (where, name, tw, th)
                    # 200% text: nothing in the page runs past the window's edge (the chips' row
                    # scrolls sideways inside itself)
                    page.evaluate("document.documentElement.style.fontSize = '28px'")
                    past = page.evaluate(
                        "[...document.querySelectorAll('main *')].filter(e => e.getClientRects().length"
                        " && e.getBoundingClientRect().right > innerWidth + 0.5 && !e.closest('.ui-chips'))"
                        ".map(e => e.className)"
                    )
                    assert not past, (where, "200%", past)
                    page.evaluate("document.documentElement.style.fontSize = ''")
                assert not errors, errors
                ctx.close()
        browser.close()


def _cols(page, sel: str) -> int:
    return page.evaluate(
        f"getComputedStyle(document.querySelector({sel!r})).gridTemplateColumns.split(' ').length"
    )


def _shown(page, sel: str) -> bool:
    return page.evaluate(f"!!document.querySelector({sel!r}).getClientRects().length")


def test_table_switches_layout_at_600_and_900(kit: KitServer) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    row = ".ui-table .tr:not(.th):not(.group):not(.daygroup)"
    with sync_playwright() as p:
        browser = _browser(p, exe)
        ctx = _ctx(browser, kit, sid, 1280, 800, False, "light")
        page = ctx.new_page()
        page.goto(kit.base + "/ui-kit", wait_until="networkidle")
        for width, cols, head, md, sub, stin in (
            (1280, 7, True, True, False, False),
            (900, 7, True, True, False, False),
            (899, 4, True, False, True, False),
            (600, 4, True, False, True, False),
            (599, 3, False, False, True, True),
            (390, 3, False, False, True, True),
        ):
            page.set_viewport_size({"width": width, "height": 800})
            where = width
            assert _cols(page, row) == cols, where
            assert _shown(page, ".ui-table .th") is head, where
            assert _shown(page, f"{row} > .md-hide") is md, where
            assert _shown(page, f"{row} .sub") is sub, where
            assert _shown(page, f"{row} .stin") is stin, where
            assert _shown(page, f"{row} > .sm-hide") is (width >= 600), where
            # the row's open action: the word from 600 px, the chevron below
            assert _shown(page, f"{row} .act .chev") is (width < 600), where
            # widgets: two a row at 600-899 and below 600, more from 900
            tops = page.evaluate(
                "[...document.querySelectorAll('.wdg')].map(w => w.getBoundingClientRect().top)"
            )
            per_row = sum(1 for t in tops if abs(t - tops[0]) < 1)
            assert per_row == 2 if width < 900 else per_row > 2, (where, tops)
            assert page.evaluate("document.documentElement.scrollWidth") <= width, where
        ctx.close()
        browser.close()


def test_side_panel_beside_the_list_from_1100_and_its_own_page_below(kit: KitServer) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        ctx = _ctx(browser, kit, sid, 1280, 800, False, "dark")
        page = ctx.new_page()
        page.goto(kit.base + "/ui-kit/3", wait_until="networkidle")
        for width in (1100, 1280, 1600):
            page.set_viewport_size({"width": width, "height": 800})
            lst = page.locator(".list-only").bounding_box()
            drawer = page.locator(".ui-drawer").bounding_box()
            assert lst and drawer, width
            assert abs(drawer["width"] - 440) < 1 and drawer["x"] >= lst["x"] + lst["width"], (
                width,
                lst,
                drawer,
            )
            assert (
                page.evaluate("getComputedStyle(document.querySelector('.ui-drawer')).position") == "sticky"
            )
            assert not _shown(page, ".ui-drawer .page-only") and _shown(page, ".ui-drawer .close")
            # the list folds to its 600-899 layout beside the panel
            assert not _shown(page, ".ui-table .tr:not(.th):not(.group):not(.daygroup) > .md-hide"), width
            assert page.evaluate("document.documentElement.scrollWidth") <= width
        # the panel stays in view while the list scrolls
        page.set_viewport_size({"width": 1280, "height": 800})
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(200)
        top = page.locator(".ui-drawer").bounding_box()["y"]
        assert 0 <= top < 120, top
        # narrower: the item is its own page, with a way back
        for width in (1099, 673, 390):
            page.set_viewport_size({"width": width, "height": 800})
            assert not _shown(page, ".list-only"), width
            assert _shown(page, ".ui-drawer") and _shown(page, ".ui-drawer .page-only")
            assert not _shown(page, ".ui-drawer .close"), width
        # Esc closes the panel, as the × does
        page.set_viewport_size({"width": 1280, "height": 800})
        page.keyboard.press("Escape")
        page.wait_for_url(kit.base + "/ui-kit")
        assert not _shown(page, ".ui-drawer") if page.locator(".ui-drawer").count() else True
        ctx.close()
        browser.close()


def test_selection_bar_stays_above_the_phone_tab_bar(kit: KitServer) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        ctx = _ctx(browser, kit, sid, 390, 844, True, "light")
        page = ctx.new_page()
        page.goto(kit.base + "/ui-kit", wait_until="networkidle")
        page.locator(".ui-table").first.scroll_into_view_if_needed()
        bar = page.locator(".ui-bar").bounding_box()
        tabs = page.locator(".tabbar").bounding_box()
        assert bar and tabs
        assert bar["y"] + bar["height"] <= tabs["y"] + 0.5, (bar, tabs)
        assert bar["y"] + bar["height"] <= 844
        ctx.close()
        browser.close()


def test_dates_follow_the_rule_in_the_viewers_time_zone(kit: KitServer) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        ctx = _ctx(
            browser, kit, sid, 1280, 800, False, "light", locale="en-US", timezone_id="Pacific/Auckland"
        )
        page = ctx.new_page()
        page.goto(kit.base + "/ui-kit", wait_until="networkidle")
        first = page.locator("time[data-fmt=rel]").first
        assert first.inner_text() == "2 h ago"
        title = first.get_attribute("title") or ""
        assert "UTC" not in title and str(time.gmtime(kit.now - 7200).tm_year) in title, title
        # the script's rule is the server's, boundary included (times in the viewer's zone)
        now = int(kit.now)
        for age in (
            0,
            59,
            60,
            3599,
            3600,
            86399,
            86400,
            6 * 86400,
            7 * 86400,
            7 * 86400 + 1,
            9 * 86400,
            400 * 86400,
        ):
            js = page.evaluate(
                "([t, n]) => MtgText.rel(new Date(t * 1000), new Date(n * 1000))", [now - age, now]
            )
            py = ui.when_text(now - age, now=now)
            if age <= 7 * 86400:
                assert js == py, (age, js, py)
            else:  # the date: in Auckland, not UTC
                local = page.evaluate(
                    "t => new Date(t * 1000).toLocaleDateString('en-US', {month: 'short', day: 'numeric'})",
                    now - age,
                )
                assert js.startswith(local), (age, js, local)
                assert js.endswith(f", {time.gmtime(now - age).tm_year}") is (age > 300 * 86400), (age, js)
        # without scripts the server's text reads on its own
        noscript = browser.new_context(java_script_enabled=False)
        noscript.add_cookies([{"name": "mtg_session", "value": sid, "url": kit.base}])
        pg = noscript.new_page()
        pg.goto(kit.base + "/ui-kit")
        t = pg.locator("time[data-fmt=rel]").first
        assert t.inner_text() == "2 h ago" and t.get_attribute("title").endswith(" UTC")
        assert pg.locator("form.ui-tools noscript").count() == 1
        noscript.close()
        ctx.close()
        browser.close()


PHONE_FIT_JS = """
() => {
  const i = getComputedStyle(document.querySelector('.ui-tools .search input'));
  const h = document.querySelector('.ui-head h1');
  const r = document.createRange(); r.selectNodeContents(h);
  const lines = new Set([...r.getClientRects()].map(x => Math.round(x.top))).size;
  const grid = document.querySelector('.ui-widgets');
  const cols = getComputedStyle(grid).gridTemplateColumns.split(' ').length;
  const clipped = [...grid.querySelectorAll('.wdg .lab, .wdg .big, .wdg .det')]
    .filter(e => e.getClientRects().length)
    .filter(e => { const edge = e.closest('.wdg').getBoundingClientRect().right + .5;
      return [...e.getClientRects()].some(x => x.right > edge) || e.scrollWidth > e.clientWidth + 1; })
    .map(e => e.textContent.trim().slice(0, 24));
  return {border: ['Top', 'Right', 'Bottom', 'Left'].map(s => i['border' + s + 'Width']),
    radius: [i.borderTopLeftRadius, i.borderTopRightRadius, i.borderBottomRightRadius,
      i.borderBottomLeftRadius],
    theme: getComputedStyle(document.documentElement).getPropertyValue('--radius').trim(),
    lines, cols, clipped};
}
"""


def test_phone_search_box_heading_and_widgets_at_200_percent_text(kit: KitServer) -> None:
    """Below 600 px the toolbar's search box keeps its full border and the theme's corners; at
    200% text on a 344-390 px phone the page title stays whole on one line and the widgets go one
    a row instead of clipping their labels; at normal text they stay two a row."""
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = kit.sign_in()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        for w in (344, 360, 390):
            ctx = _ctx(browser, kit, sid, w, 800, True, "light")
            page = ctx.new_page()
            page.goto(kit.base + "/ui-kit", wait_until="networkidle")
            got = page.evaluate(PHONE_FIT_JS)
            assert got["border"] == ["1px"] * 4, (w, got)
            assert got["radius"] == [got["theme"]] * 4 and got["theme"] not in ("", "0px"), (w, got)
            assert got["lines"] == 1 and got["cols"] == 2, (w, got)
            page.evaluate("document.documentElement.style.fontSize = '32px'")
            got = page.evaluate(PHONE_FIT_JS)
            assert got["lines"] == 1, (w, "200%", got)
            assert got["cols"] == 1 and not got["clipped"], (w, "200%", got)
            ctx.close()
        browser.close()
