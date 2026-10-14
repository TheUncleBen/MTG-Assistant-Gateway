"""The proposal review page (/proposals/<id>) in headless Chromium, light and dark, desktop and
phone (R-181): the locked Apply and Reject labels read at 4.5:1, a tag change says what it adds and takes
off, a cover change names the cover it replaces, and an add or remove row is one line on a
phone. Skipped without a Chromium build."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mtg_gateway.archidekt import art_url

from .test_editor_browser import _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

SOL = "58b26011-e103-45c4-a253-900f4e6b2eeb"
CULTIVATE = "be68e315-ffef-40a8-8a46-1c042b148c03"

# The contrast of an element's text against what is drawn behind it: its own background, or the
# first ancestor's, each translucent one blended over the next.
CONTRAST = """(el) => {
  const rgba = s => {
    const m = s.match(/[\\d.]+/g).map(Number);
    return [m[0], m[1], m[2], m.length > 3 ? m[3] : 1];
  };
  const layers = [];
  for (let e = el; e; e = e.parentElement) {
    const c = rgba(getComputedStyle(e).backgroundColor);
    if (c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
  }
  let bg = [255, 255, 255];
  for (let i = layers.length - 1; i >= 0; i--) {
    const [r, g, b, a] = layers[i];
    bg = [r * a + bg[0] * (1 - a), g * a + bg[1] * (1 - a), b * a + bg[2] * (1 - a)];
  }
  const s = getComputedStyle(el);
  const fg = rgba(s.color);
  const lum = c => {
    const v = c.slice(0, 3).map(x => x / 255)
      .map(x => x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  };
  const a = lum(fg), b = lum(bg);
  const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  return {ratio: ratio, opacity: Number(s.opacity), disabled: el.disabled};
}"""


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _proposals(server: Server) -> tuple[str, str, str]:
    sid = _link(server)
    deck = server.ark.decks[42]
    by_name = {c["card"]["oracleCard"]["name"]: c for c in deck["cards"]}
    by_name["Sol Ring"]["card"]["uid"] = SOL
    by_name["Cultivate"]["card"]["uid"] = CULTIVATE
    deck["featured"] = art_url(SOL)
    server.ark.deck_tags[42] = [{"id": 501, "tag": 1, "name": "budget", "position": 0}]
    decks = server.app.state.gateway.decks

    async def make() -> tuple[str, str]:
        edit = await decks.propose(
            "user-1",
            "42",
            [
                {"action": "add", "card_name": "Arcane Signet"},
                {"action": "remove", "card_name": "Counterspell"},
                {"action": "set_quantity", "card_name": "Forest", "quantity": 13},
            ],
        )
        details = await decks.propose_deck_details(
            "user-1",
            "42",
            {"add_tags": ["ramp", "landfall"], "remove_tags": ["budget"], "cover": "Cultivate"},
        )
        return edit["proposal_id"], details["proposal_id"]

    edit, details = asyncio.run(make())
    return sid, edit, details


def test_review_page_reads_in_both_themes_on_desktop_and_phone(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, edit, details = _proposals(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        try:
            for theme in ("light", "dark"):
                for vw, vh, phone in ((1280, 800, False), (673, 841, True), (390, 844, True)):
                    ctx = browser.new_context(
                        viewport={"width": vw, "height": vh},
                        color_scheme=theme,
                        is_mobile=phone,
                        has_touch=phone,
                    )
                    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
                    page = ctx.new_page()
                    page.goto(f"{server.base}/proposals/{details}", wait_until="networkidle")
                    for value in ("apply", "reject"):
                        got = page.locator(f"form.guarded button[value={value}]").evaluate(CONTRAST)
                        assert got["disabled"], got  # locked until the click guard sees the page in use
                        assert got["opacity"] == 1 and got["ratio"] >= 4.5, (theme, vw, value, got)
                    rows = page.locator("ul.changes li")
                    tags = rows.filter(has_text="Adds ramp, landfall; removes budget")
                    assert tags.count() == 1 and "budget" in tags.locator(".was").inner_text()
                    assert rows.filter(has_text="Sol Ring").locator(".was").inner_text() == "Sol Ring"
                    assert "current" not in page.locator("ul.changes").inner_text()
                    page.goto(f"{server.base}/proposals/{edit}", wait_until="networkidle")
                    for kind in ("add", "del"):
                        li = page.locator(f"ul.changes li.{kind}").first
                        box, qty = li.bounding_box(), li.locator(".qty").bounding_box()
                        name = li.locator(".name").bounding_box()
                        # the count sits on the card's line, at every width
                        assert abs(qty["y"] - name["y"]) < 8 and box["height"] < 60, (
                            theme,
                            vw,
                            kind,
                            box,
                            qty,
                        )
                    ctx.close()
        finally:
            browser.close()
