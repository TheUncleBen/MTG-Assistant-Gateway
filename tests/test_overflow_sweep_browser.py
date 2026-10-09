"""R-131 overflow sweep in headless Chromium: with realistic data (a deck named like the app's
own copies, a very long deck name, long card names in a full collection, a long member name,
history, a report and a pending proposal) every page is laid out at 18 widths from 320 to 1400
px, with a mouse and on a touch screen, and must never scroll sideways, keep every control
inside the window and inside whatever clips it, and never let two controls overlap. Every
dropdown menu (details.dd) is also opened and its panel must sit inside the window. Skipped
without a Chromium build."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from mtg_gateway.archidekt_csv import parse_export, to_deck_json

from . import fake_archidekt as FA
from .test_editor_browser import PNG, _link
from .test_history_page import seed_history, store_report
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

LONG_COPY = "Copy of - Sample Commander Deck"
LONGER = "Aesi, Tyrant of Gyre Strait: Lands Matter Big Ramp Value Pile (Budget Upgrade, v3)"
CARDS = [
    "Aesi, Tyrant of Gyre Strait",
    "Delver of Secrets // Insectile Aberration",
    "Sol Ring",
    "Arcane Signet",
    "Rampant Growth",
    "Acidic Slime",
    "Opt",
    "Forest",
    "Asmoranomardicadaistinaculdacar",
    "Okiri, Belligerent Bannerkeeper of the Greatest Grand Army",
]
WIDTHS = [320, 360, 400, 480, 560, 600, 640, 680, 720, 760, 800, 900, 1000, 1100, 1200, 1300, 1366, 1400]


LONGUSER = "Planeswalker_Archivist_of_the_Multiverse"  # 40 characters, one unbroken word
UNBROKEN = "Superlongunbrokendecknamewithoutanyspace"  # 40 characters, no space to wrap at
UNBROKEN_2 = "Thisisanotherveryveryverylongwordwithnospa"
FOLDERS = [
    "Commander decks in progress and testing builds for Friday Night Magic at the shop",
    "Archiveddecksnolongerplayedbutkeptforreference",
]
LONG_TAGS = ["landfall-and-ramp-synergies-with-an-extra-long-tag-name", "budget under 100 (paper only)"]
# Archidekt's real maximum username length is not verified; 40 unbroken characters is the hostile
# case this sweep uses. Everything a member can type is long and, where possible, unbroken.


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    long_cards = ((9801, CARDS[-2]), (9802, CARDS[-1]))
    for cid, name in long_cards:
        FA.CARD_DB.setdefault(name.lower(), {"id": cid, "oracleCard": {"name": name}})
    fixture = Path(__file__).parent / "fixtures" / "sample_deck.csv"
    cards = parse_export(fixture.read_text(encoding="utf-8"))
    s.ark.decks[44] = to_deck_json(cards, deck_id=44, name=LONG_COPY, owner="alice")
    s.ark.decks[45] = to_deck_json(cards[:20], deck_id=45, name=LONGER, owner="alice")
    s.ark.decks[46] = to_deck_json(cards[:12], deck_id=46, name=UNBROKEN, owner="alice")
    s.ark.users["alice"]["decks"] += [44, 45, 46]
    # another member with the longest username, owning two long-named decks (profile + deck page Follow)
    s.ark.users[LONGUSER] = {"password": "pw-long", "id": 79, "decks": [47, 48]}
    s.ark.decks[47] = to_deck_json(cards[:15], deck_id=47, name=UNBROKEN_2, owner=LONGUSER)
    s.ark.decks[48] = to_deck_json(cards[:15], deck_id=48, name=LONGER, owner=LONGUSER)
    for i, name in enumerate(FOLDERS, 1):
        s.ark.folders.setdefault("alice", []).append({"id": 500 + i, "name": name, "private": False})
    s.ark.deck_folder[44] = 501
    s.ark.deck_folder[46] = 502
    orig_row = s.ark._listing_row

    def row(d):  # long listing tags on every deck but the sample one
        r = orig_row(d)
        if d["id"] != 42:
            r["tags"] = [{"id": 11 + k, "name": t} for k, t in enumerate(LONG_TAGS + ["ramp"])]
        return r

    s.ark._listing_row = row
    s.ark.add_side_row(42, "Sol Ring")
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _pages(server: Server, pending: str) -> list[str]:
    return [
        "/",
        "/decks",
        "/decks?view=list",
        "/decks/42",
        "/decks/42?view=grid",
        "/decks/44",
        "/decks/45?view=grid",
        "/decks/46",
        "/decks/47",
        f"/decks?folder={FOLDERS[0]}",
        "/decks/42/edit",
        "/decks/45/edit",
        "/decks/42/settings",
        "/decks/new",
        "/decks/42/export",
        "/decks/42/compare",
        "/folders",
        "/collection?view=grid",
        "/collection?view=list",
        "/search",
        f"/search?q={UNBROKEN_2}",
        "/precons",
        "/users/alice",
        f"/users/{LONGUSER}",
        "/history",
        "/history/reports/r1",
        "/activity",
        "/proposals",
        f"/proposals/{pending}",
        "/account",
        "/guide",
        "/scan",
        "/skill",
        "/app",
        "/admin",
        "/admin/users",
        "/admin/activity",
        "/admin/metrics",
        "/logout",
        "/install",
    ]


# Measures the page as laid out now: the document's scroll width against its client width, every
# visible control against the window and the nearest ancestor that clips, and pairs of controls
# against each other. Returns the problems found, each a short string.
MEASURE = """
() => {
  const vw = document.documentElement.clientWidth;
  const out = [];
  if (document.documentElement.scrollWidth > vw) out.push('document scrolls sideways by ' +
    (document.documentElement.scrollWidth - vw) + 'px');
  const name = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    else if (el.classList.length) s += '.' + [...el.classList].slice(0, 3).join('.');
    const t = (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().slice(0, 28);
    return t ? s + ' "' + t + '"' : s;
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    if (el.closest('[hidden],[aria-hidden=true]')) return false;
    for (let d = el.parentElement && el.parentElement.closest('details:not([open])'); d;
         d = d.parentElement && d.parentElement.closest('details:not([open])')) {
      const sm = d.querySelector(':scope > summary');
      if (!(sm && sm.contains(el))) return false;
    }
    return true;
  };
  const clipper = (el) => {
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (/(auto|scroll|hidden|clip)/.test(cs.overflowX + cs.overflow)) return a;
    }
    return null;
  };
  const sel = 'button, a.btn, .btn, summary, input:not([type=hidden]), select, textarea, [role=combobox]';
  const pinned = (el) => {
    for (let a = el; a && a !== document.body; a = a.parentElement) {
      const p = getComputedStyle(a).position;
      if (p === 'fixed' || p === 'sticky') return true;
    }
    return false;
  };
  const ctrls = [...document.querySelectorAll(sel)].filter(visible)
    .filter(el => !el.matches('select[aria-hidden=true]'))
    .map(el => ({ el, r: el.getBoundingClientRect(), pinned: pinned(el) }));
  for (const { el, r } of ctrls) {
    if (r.right <= 0 || r.left >= vw) continue; // parked off screen on purpose (a hidden file input)
    if (r.left < -1 || r.right > vw + 1)
      out.push(name(el) + ' outside the window (' + Math.round(r.left) + '..' + Math.round(r.right) +
        ' of ' + vw + ')');
    const c = clipper(el);
    if (c) {
      const cr = c.getBoundingClientRect();
      if (r.left < cr.left - 1 || r.right > cr.right + 1)
        if (!(c.scrollWidth > c.clientWidth && getComputedStyle(c).overflowX !== 'hidden'))
          out.push(name(el) + ' clipped by ' + name(c) + ' (' + Math.round(r.left) + '..' +
            Math.round(r.right) + ' of ' + Math.round(cr.left) + '..' + Math.round(cr.right) + ')');
    }
  }
  const flow = ctrls.filter(c => !c.pinned).sort((a, b) => a.r.top - b.r.top);
  for (let i = 0; i < flow.length; i++) for (let j = i + 1; j < flow.length; j++) {
    const a = flow[i], b = flow[j];
    if (b.r.top >= a.r.bottom - 1) break;
    if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
    const ox = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
    const oy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
    if (ox > 1 && oy > 1)
      out.push(name(a.el) + ' overlaps ' + name(b.el) + ' by ' + Math.round(ox) + 'x' + Math.round(oy) +
        'px');
  }
  return out;
}
"""

# Opens each dropdown menu in turn (its summary's click runs feedback.js's placement) and checks
# the panel against the window; closes it again.
MENUS = """
async () => {
  const vw = document.documentElement.clientWidth, out = [];
  const name = (d) => {
    const s = d.querySelector(':scope > summary');
    const t = (s && (s.getAttribute('aria-label') || s.innerText)) || d.className;
    return 'menu of ' + t.trim().slice(0, 30);
  };
  for (const d of document.querySelectorAll('details.dd')) {
    const sm = d.querySelector(':scope > summary');
    if (!sm) continue;
    const cs = getComputedStyle(sm);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    if (sm.getBoundingClientRect().width === 0) continue;
    sm.click();
    await new Promise(r => setTimeout(r, 0));
    if (!d.open) { out.push(name(d) + ' did not open'); continue; }
    const m = d.querySelector(':scope > .menu');
    if (m) {
      const r = m.getBoundingClientRect();
      if (r.left < -1 || r.right > vw + 1 || r.width > vw + 1)
        out.push(name(d) + ' panel outside the window (' + Math.round(r.left) + '..' + Math.round(r.right) +
          ' of ' + vw + ')');
      if (document.documentElement.scrollWidth > vw)
        out.push(name(d) + ' open: document scrolls sideways by ' +
          (document.documentElement.scrollWidth - vw) + 'px');
    }
    sm.click();
    await new Promise(r => setTimeout(r, 0));
    if (d.open) d.removeAttribute('open');
  }
  return out;
}
"""


def _seed(server: Server) -> tuple[str, str]:
    import httpx

    sid = _link(server)
    object.__setattr__(server.app.state.gateway.settings, "admin_group", "mtg-admins")
    server.db.upsert_user(
        "user-1",
        email="alice@example.test",
        name="Alexandria Winterbourne-Castellanos",
        preferred_username="alice",
        groups=["mtg-admins"],
    )
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/collection").text).group(1)
    r = h.post(
        "/collection/api/add",
        headers={"X-CSRF-Token": csrf},
        json={"items": [{"name": n, "quantity": 1 + i % 3} for i, n in enumerate(CARDS)], "source": "scan"},
    )
    assert r.status_code == 200, r.text
    for i, row in enumerate(server.ark.collections.get("alice", {}).values()):  # long collection tags
        if i % 2 == 0:
            row["tags"] = [{"id": 21, "name": LONG_TAGS[0]}, {"id": 22, "name": "trade binder page 12"}]
    import time

    seed_history(server.db, "user-1", now=int(time.time()))
    store_report(server.db, "user-1", "r1", deck_name=LONGER)
    pending = next(p["id"] for p in server.db.list_proposals("user-1", limit=100) if p["state"] == "pending")
    return sid, pending


@pytest.mark.parametrize("touch", [False, True], ids=["mouse", "touch"])
def test_no_page_scrolls_sideways_or_clips_a_control(server: Server, touch: bool) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        kw = {"has_touch": True, "is_mobile": True} if touch else {}
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, **kw)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for path in _pages(server, pending):
            r = page.goto(f"{server.base}{path}", wait_until="networkidle")
            assert r is not None and r.status == 200, (path, r and r.status)
            for w in WIDTHS:
                page.set_viewport_size({"width": w, "height": 900})
                page.wait_for_timeout(20)
                found = page.evaluate(MEASURE) + page.evaluate(MENUS)
                problems += [f"{path} @{w}px: {f}" for f in found]
        browser.close()
    if os.environ.get("SWEEP_OUT"):
        Path(os.environ["SWEEP_OUT"]).write_text("\n".join(problems))
    assert errors == []
    more = f"\n… {len(problems)} in all" if len(problems) > 80 else ""
    assert problems == [], "\n".join(problems[:80]) + more
