"""The card viewer's layout with a long card (0.7.48, the owner's Fold, open: Liesa, Forgotten
Archangel). With three paragraphs of rules text, seventeen formats and the deck's five actions the
viewer was taller than the window, and its text column, a flex column that did not scroll, shrank
its children to fit: the rules text cut to one line, Add to binder and EDHREC clipped at the bottom
of their groups, and Price history gone. The Legal in chips, plain words, were 44 px tall on a touch
screen with their word at the top. A tap on Rulings answered at the bottom of the viewer, away from
the row, and "This card has no rulings." read as a fault. Skipped without a Chromium build."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from .test_cardview_browser import PNG
from .test_scan_browser import _chromium_path
from .test_social_browser import Server
from .ui_matrix import FOLD_UA, TEXT_200
from .waits import settled

pytest.importorskip("playwright")

LONG_TEXT = (
    "Flying, lifelink\n"
    "Whenever another nontoken creature you control dies, return that card to its owner's hand at "
    "the beginning of the next end step.\n"
    "If a creature an opponent controls would die, exile it instead."
)
LEGAL = (
    "1v1,canlander,commander,competitivebrawl,duel,gladiator,historic,historicbrawl,legacy,modern,"
    "oathbreaker,penny,pioneer,timeless,vintage,brawl,explorer"
)

# (width, height, touch, text size)
SCREENS = [
    (884, 1000, True, 1),  # the owner's Fold, open, in the app
    (884, 1000, True, 2),
    (1280, 800, False, 1),  # a laptop
    (390, 844, True, 1),  # a phone
    (390, 844, True, 2),
]


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _open_long_card(p, exe: str, server: Server, sid: str, width: int, height: int, touch: bool, text: int):  # noqa: ANN001
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    kw: dict = {"viewport": {"width": width, "height": height}}
    if touch:
        kw.update(has_touch=True, is_mobile=True, user_agent=FOLD_UA)
    ctx = browser.new_context(**kw)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    ctx.route(
        re.compile(r".*/cards/api/rulings.*"),
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"ok": True, "rulings": []})
        ),
    )
    page = ctx.new_page()
    page.goto(f"{server.base}/decks/42", wait_until="networkidle")
    page.evaluate(
        "([t, l]) => { const r = document.querySelector('.deckview .row, .deckview .c');"
        " r.setAttribute('data-text', t); r.setAttribute('data-legal', l);"
        " r.setAttribute('data-artist', 'Evan Cagle'); r.setAttribute('data-salt', '0.56');"
        " r.setAttribute('data-rank', '2130'); r.click(); }",
        [LONG_TEXT, LEGAL],
    )
    page.wait_for_selector(".cardview.open", timeout=3000)
    settled(page)
    if text > 1:
        page.evaluate(TEXT_200)
        settled(page)
    return browser, page


# What the viewer hides: a box whose content is taller than the box (cut, or a scroller squeezed
# inside the viewer), and a chip whose box is much taller than its one line of words.
MEASURE = """
() => {
  const v = document.querySelector('.cardview');
  const out = {cut: [], tallChips: [], rows: 0};
  for (const sel of ['.cardtext', '.facts', '.printrow', '.more', '.acts', '.more > *', '.acts > *']) {
    for (const e of v.querySelectorAll(sel)) {
      if (!e.getClientRects().length) continue;
      if (e.scrollHeight - e.clientHeight > 2)
        out.cut.push(sel + ' "' + e.textContent.trim().slice(0, 30) + '" shows '
          + e.clientHeight + ' of ' + e.scrollHeight + 'px');
    }
  }
  for (const c of v.querySelectorAll('.chip')) {
    const cs = getComputedStyle(c);
    const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.5;
    const inner = c.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    if (inner > lh * 1.4)
      out.tallChips.push(c.textContent + ' ' + c.clientHeight + 'px for a ' + Math.round(lh) + 'px line');
  }
  out.rows = v.querySelectorAll('.more > :is(a,button), .acts > :is(a,button,.qtyctl)').length;
  // every row can be reached: scrolled to, it sits inside the window
  const rows = [...v.querySelectorAll('.more > :is(a,button), .acts > :is(a,button,.qtyctl)')];
  out.unreachable = [];
  for (const r of rows) {
    r.scrollIntoView({block: 'nearest'});
    const b = r.getBoundingClientRect();
    if (b.bottom > innerHeight + 1 || b.top < -1) out.unreachable.push(r.textContent.trim().slice(0, 30));
  }
  return out;
}
"""


@pytest.mark.parametrize(("width", "height", "touch", "text"), SCREENS)
def test_a_long_card_shows_all_of_its_text_and_rows(
    server: Server, width: int, height: int, touch: bool, text: int
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page = _open_long_card(p, exe, server, sid, width, height, touch, text)
        m = page.evaluate(MEASURE)
        assert m["cut"] == [], m["cut"]
        assert m["tallChips"] == [], m["tallChips"]
        assert m["unreachable"] == [], m["unreachable"]
        assert m["rows"] >= 5, m
        assert "If a creature an opponent controls" in page.locator(".cardview .rules").inner_text()
        browser.close()


@pytest.mark.parametrize(("width", "height", "touch"), [(884, 1000, True), (390, 844, True)])
def test_rulings_answer_under_their_row_in_plain_words(
    server: Server, width: int, height: int, touch: bool
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page = _open_long_card(p, exe, server, sid, width, height, touch, 1)
        btn = page.locator(".cardview button[aria-controls='cardview-rulings']")
        btn.click()
        box = page.locator("#cardview-rulings")
        box.wait_for(state="visible")
        settled(page)
        assert box.inner_text().strip() == "No official rulings have been published for this card."
        where = page.evaluate(
            "() => { const b = document.querySelector(\"button[aria-controls='cardview-rulings']\");"
            " const box = document.getElementById('cardview-rulings');"
            " const br = b.getBoundingClientRect(), xr = box.getBoundingClientRect();"
            " return {next: b.nextElementSibling === box, gap: xr.top - br.bottom,"
            " seen: xr.top >= 0 && xr.bottom <= innerHeight + 1,"
            " expanded: b.getAttribute('aria-expanded')}; }"
        )
        assert where["next"], where  # the answer comes right under the row that asked for it
        assert -1 <= where["gap"] <= 2, where
        assert where["seen"], where  # and is on screen without a scroll
        assert where["expanded"] == "true"
        browser.close()
