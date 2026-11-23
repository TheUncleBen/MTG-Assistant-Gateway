"""The shared pieces of the browser tests that sign in as a member with a linked Archidekt
account: a live gateway whose fixture cards are suggestable (``server``), the sign-in and link
(``_link``) and a tiny card picture (``PNG``). The deck editor these tests once covered folded
into the deck page in 0.7.48 (tests/test_deck_add_browser.py)."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from .test_suggest_browser import Server

pytest.importorskip("playwright")

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAUAAAAHCAIAAAAbd2raAAAAFElEQVR4nGNgYGD4z8DAwMDAwMAAAAwAA/8BpYQAAAAASUVORK5CYII="
)


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    # the fixture cards' names are suggestable, with their summaries behind /scan/api/peek
    s.sf.catalog = s.sf.names_catalog()
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _link(server: Server) -> str:
    import httpx

    sid = server.sign_in()
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/account/details").text).group(1)
    r = h.post(
        "/account",
        data={
            "csrf": csrf,
            "action": "link",
            "archidekt_login": "alice",
            "archidekt_password": "pw-alice",
            "accept_risk": "1",
        },
    )
    assert r.status_code == 303, r.text
    return sid


# Overlapping boxes among an element's laid-out children (a child of 0 size, or hidden, is left out).
_OVERLAPS = """el => {
  const kids = [...el.children].map(c => [c, c.getBoundingClientRect()])
    .filter(([c, r]) => r.width > 0 && r.height > 0 && getComputedStyle(c).visibility !== 'hidden');
  const out = [];
  for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) {
    const [a, ra] = kids[i], [b, rb] = kids[j];
    const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
    const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
    if (w > 1 && h > 1) out.push(a.className + ' / ' + b.className);
  }
  return out;
}"""
