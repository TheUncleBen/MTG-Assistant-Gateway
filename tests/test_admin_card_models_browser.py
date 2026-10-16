"""The admin Card models page in headless Chromium at 344, 360, 673, 720 and 1280 px, light and dark:
nothing scrolls sideways, text reads at 4.5:1 or better, Remove is big enough to tap, and the
admin tabs fit. Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import SIZES, _chromium, _page
from .test_overflow_sweep_browser import MEASURE
from .test_settings_page_browser import _admin, _shot
from .test_suggest_browser import Server

pytest.importorskip("playwright")


# Each card name's lines: a break inside a word (between two letters) is listed.
NAME_BREAKS = """
() => [...document.querySelectorAll('.models .name')].flatMap(el => {
  const n = el.firstChild, text = el.textContent, out = [];
  let top = null;
  for (let i = 0; i < text.length; i++) {
    const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + 1);
    const t = Math.round(r.getBoundingClientRect().top);
    if (top !== null && t > top + 2 && /\\S/.test(text[i - 1]))
      out.push(text.slice(0, i) + '|' + text.slice(i));
    top = t;
  }
  return out;
})
"""


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path, admin_group="mtg-admins")
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("width,theme", [*SIZES, (344, "light"), (673, "dark")])
def test_the_card_models_page_fits_and_reads(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _admin(server)
    server.db.upsert_user(
        "user-9", email=None, name=None, preferred_username="a_member_with_a_long_name", groups=[]
    )
    names = ["Sol Ring", "Kenrith, the Returned King", "Asmoranomardicadaistinaculdacar", "Opt"]
    for i, name in enumerate(names):
        server.db.put_card_model(name.casefold(), name, "{}", "user-9", 100 + i)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/admin/models", wait_until="networkidle")
        assert page.locator(".models li").count() == 4
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, ".models li .name, .models li .small, .models button, .card p") == []
        sizes = page.evaluate(
            "() => [...document.querySelectorAll('.models button')]"
            ".map(b => b.getBoundingClientRect().height)"
        )
        assert min(sizes) >= 43.5, sizes  # review round 2, R2-2: 44 px, as on every touch target
        # R2-4: every Remove in one column, and a name breaks between words where its words fit
        rights = page.evaluate(
            "() => [...document.querySelectorAll('.models button')]"
            ".map(b => Math.round(b.getBoundingClientRect().right))"
        )
        assert len(set(rights)) == 1, rights
        if width >= 673:
            assert page.evaluate(NAME_BREAKS) == []
        _shot(page, f"admin-models-{width}-{theme}.png")
        page.locator(".models li", has_text="Opt").locator("button").click()
        page.wait_for_load_state("networkidle")
        assert "Card model removed." in page.locator(".notice.ok").inner_text()
        assert page.locator(".models li").count() == 3
        browser.close()
    assert errors == []
