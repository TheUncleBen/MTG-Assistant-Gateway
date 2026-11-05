"""The toasts at the foot of the screen (static/cardmenu.js's shared one and the deck page's own,
static/deck.js) in headless Chromium at 320, 360, 412 and 1280 px, at 100% and 200% text (a 28 px
root, and every font doubled). Final polish AA2 on 71911eba: in the card-added toast at 200% text
the check mark and the × sat alone on the first row and the message started under them; at 100%
the check mark sat beside the middle line of a long message. The icon, the message's first line
and the × share one row in every toast, and the icon is the text's size."""

from __future__ import annotations

import pytest

from .test_cardsearch_browser import _chromium, _page
from .test_deck_toasts_browser import _commander
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

LONG = "Okiri, Belligerent Bannerkeeper of the Greatest Grand Army"

# the shared toast's variants, as the pages make them (static/cardmenu.js collect and watch): a
# card added to the binder, a card put on the watchlist (its list and Undo), a card already
# there, a lost connection, a plain note; True where the toast carries the check mark
SHARED = {"added": True, "watched": True, "there": True, "lost": False, "plain": False}
MAKE = """([kind, name]) => {
  const M = window.MtgMenu;
  M.dismissToast();
  const link = (t, href) => { const a = M.el('a', 'btn', t); a.href = href; return a; };
  const undo = M.el('button', 'btn-plain', 'Undo');
  const list = () => link('Open the watchlist', '/watchlist');
  ({
    added: () => M.toast(name + ' added to your binder.',
      {ok: true, actions: [link('Open the binder', '/collection')]}),
    watched: () => M.toast(name + ' added to your watchlist.', {ok: true, actions: [list(), undo]}),
    there: () => M.toast(name + ' is on your watchlist.', {ok: true, actions: [list()]}),
    lost: () => M.toast('No connection; the card was not added.', {error: true}),
    plain: () => M.toast(name + ' is no longer on your watchlist.'),
  })[kind]();
}"""

# one toast: whether its icon and its × share the row of the message's first line, the icon's
# size against the text's, and the toast's fit on the screen
ROW = """(sel) => {
  const t = document.querySelector(sel);
  if (!t) return null;
  const msg = t.querySelector('.msg'), x = t.querySelector('.close'), ic = t.querySelector('svg');
  const w = document.createTreeWalker(msg, NodeFilter.SHOW_TEXT);
  let first = null;
  for (let n = w.nextNode(); n && !first; n = w.nextNode()) {
    if (!n.textContent.trim()) continue;
    const q = document.createRange(); q.selectNodeContents(n);
    const rs = [...q.getClientRects()].filter(r => r.width > 0);
    if (rs.length) first = rs[0];
  }
  const row = (a, b) => !!(a && b && a.top < b.bottom && b.top < a.bottom);
  const tb = t.getBoundingClientRect(), vw = document.documentElement.clientWidth;
  const ir = ic ? ic.getBoundingClientRect() : null;
  return {text: msg.textContent.trim().slice(0, 40), font: parseFloat(getComputedStyle(msg).fontSize),
    icon: ir ? {width: Math.round(ir.width), onRow: row(ir, first)} : null,
    closeOnRow: row(x.getBoundingClientRect(), first),
    inside: tb.left >= -1 && tb.right <= vw + 1 && tb.bottom <= document.documentElement.clientHeight + 1,
    scroll: document.documentElement.scrollWidth - vw};
}"""


def _zoom(page, zoom: str) -> None:  # noqa: ANN001
    """The page at 200% text. A toast made afterwards inherits the body's doubled size (the
    text-only zoom sets it on the body, as Android's font scale does)."""
    if zoom != "none":
        page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
        page.wait_for_timeout(100)


def _check(where: str, got: dict | None, *, icon: bool) -> None:
    assert got, where
    assert got["scroll"] <= 0 and got["inside"], (where, got)
    assert got["closeOnRow"], (where, got)
    if icon:
        assert got["icon"] and got["icon"]["onRow"], (where, got)
        assert got["icon"]["width"] >= got["font"] * 0.9, (where, got)


@pytest.mark.parametrize("zoom", ["none", "root", "text"])
@pytest.mark.parametrize("width", [320, 360, 412, 1280])
def test_every_toast_keeps_its_icon_its_first_line_and_its_x_on_one_row(
    server: Server,  # noqa: F811
    width: int,
    zoom: str,
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    _commander(server)
    server.app.state.gateway.scan.names.set_names(["Cultivate"])
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        page.goto(f"{server.base}/collection?view=list", wait_until="networkidle")
        _zoom(page, zoom)
        # the Binder's own: a row removed, with Undo
        row = page.locator("li[data-id]").first
        assert row.locator("form.qty output").inner_text().strip() == "1"
        row.locator("form.qty button[value=dec]").click()
        page.locator(".toast.cards-toast", has_text="Removed from your binder").wait_for()
        _check(f"removed @{width} {zoom}", page.evaluate(ROW, ".toast.cards-toast"), icon=True)
        page.locator(".toast.cards-toast button", has_text="Undo").click()
        page.locator(".toast.cards-toast", has_text="back in your binder").wait_for()
        for kind, with_icon in SHARED.items():
            page.evaluate(MAKE, [kind, LONG])
            page.locator(".toast.cards-toast").wait_for()
            _check(f"{kind} @{width} {zoom}", page.evaluate(ROW, ".toast.cards-toast"), icon=with_icon)
        # the deck page's own, after a quick add
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        _zoom(page, zoom)
        page.locator("#quick").fill("Cultivate")
        page.locator("#quick").press("Enter")
        page.locator(".deck-toast .msg", has_text="Added Cultivate").wait_for(timeout=8000)
        _check(f"deck @{width} {zoom}", page.evaluate(ROW, ".toast.deck-toast"), icon=False)
        assert errors == []
        browser.close()
