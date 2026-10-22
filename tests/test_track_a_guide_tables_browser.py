"""The Guide's reference tables stay readable on a phone and at large text: where four columns would
squeeze to a word or two a line, each row becomes a block with every cell under its column's name,
and the page never scrolls sideways. Phone, Fold and desktop, light and dark, 100% and 200% text.
Skipped without a Chromium build."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_guide_browser import _EXE, Server, _chromium

pytest.importorskip("playwright")

# per table: its height, how wide its narrowest wrapped cell is in that cell's own em, whether the
# rows are stacked, and whether the table and every cell keep their roles and column names
TABLES = """() => [...document.querySelectorAll('.guide .tbl table')].map(t => {
  const cells = [...t.querySelectorAll('td')];
  let narrow = 99;
  for (const c of cells) {
    const s = getComputedStyle(c), em = parseFloat(s.fontSize), line = parseFloat(s.lineHeight) || em * 1.5;
    const pad = parseFloat(s.paddingTop) + parseFloat(s.paddingBottom);
    const label = s.display === 'block' ? line * 0.85 * 1.2 : 0;  // the column name above it
    const wraps = c.getBoundingClientRect().height - pad - label > line * 1.6;
    if (wraps) narrow = Math.min(narrow, c.clientWidth / em);
  }
  return {
    head: t.querySelector('th').textContent,
    rows: t.querySelectorAll('tbody tr').length,
    chars: cells.reduce((n, c) => n + c.textContent.trim().length, 0),
    em: cells.length ? parseFloat(getComputedStyle(cells[0]).fontSize) : 16,
    height: Math.round(t.getBoundingClientRect().height),
    narrow: Math.round(narrow * 10) / 10,
    stacked: getComputedStyle(t.querySelector('tbody tr')).display === 'block',
    roles: t.getAttribute('role') === 'table'
      && cells.every(c => c.getAttribute('role') === 'cell' && c.dataset.label),
  };
})"""

SIZES = [(344, 882, True), (390, 844, True), (673, 841, True), (1280, 800, False)]


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_guide_tables_stay_readable_at_every_width_and_text_size(server: Server, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_EXE["path"], args=["--no-sandbox"])
        try:
            for vw, vh, phone in SIZES:
                for text in ("100%", "200%"):
                    ctx = browser.new_context(
                        viewport={"width": vw, "height": vh},
                        color_scheme=scheme,
                        is_mobile=phone,
                        has_touch=phone,
                    )
                    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])

                    def sized(route, _request, text=text):
                        got = route.fetch()
                        head, _, tail = got.text().rpartition("</body>")
                        body = f"{head}<style>html{{font-size:{text}}}</style></body>{tail}"
                        route.fulfill(response=got, body=body)

                    if text != "100%":
                        ctx.route("**/guide", sized)
                    page = ctx.new_page()
                    errors: list[str] = []
                    page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                    page.goto(f"{server.base}/guide", wait_until="networkidle")
                    where = (vw, text, scheme)
                    tables = page.evaluate(TABLES)
                    print(where, tables)
                    assert len(tables) == 6, where  # the five reference tables and the tool-names fold
                    width = page.evaluate("() => document.documentElement.scrollWidth")
                    assert width <= vw, (where, width)
                    for t in tables:
                        # a wrapped cell holds at least about ten letters a line (squeezed, about
                        # two at 200% text and three or four at 100%)
                        assert t["narrow"] >= 5, (where, t)
                        assert t["roles"], (where, t)
                        # the height a stacked table needs grows with its text: about 8 em a
                        # row for the column names and padding, and about 0.2 em a letter
                        # (five letters a line of 1.5 em is 0.3 em). The Mode table's three
                        # paragraphs are 4,634 px at 344 px and 200% text; squeezed to a letter
                        # or two a line the old Capability table was 56,216 px for 44 rows
                        assert t["height"] <= (8 * t["rows"] + 0.2 * t["chars"]) * t["em"], (where, t)
                    # at 100% on a desktop the table stays a table (on the Fold its cells were
                    # squeezed to about three letters, so it stacks there too)
                    if text == "100%" and vw >= 1280:
                        assert not any(t["stacked"] for t in tables), (where, tables)
                    if vw <= 390 or text == "200%":
                        assert all(t["stacked"] for t in tables), (where, tables)
                    assert not errors, (where, errors)
                    ctx.close()
        finally:
            browser.close()
