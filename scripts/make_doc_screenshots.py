"""Regenerate docs/screenshots/*.png from the app at HEAD, on the browser tests' in-process harness
(a fake Archidekt with the fixture deck, no real account). Desktop 1280 wide (the page's height,
at most 1800) and phone 412x915 at DPR 1, dark except the -light ones, a deployment named
"MTG Assistant Gateway" (the default name) at https://gateway.example.com. Run from the
repository root with the test extras installed::

    PYTHONPATH=src .venv/bin/python scripts/make_doc_screenshots.py [docs/screenshots]

Needs Playwright's Chromium (or a build the browser tests find, see tests/test_scan_browser.py)
and Pillow, which recompresses each file losslessly. The browser visits the example host; a
Playwright route answers each request from the local server, so the pages carry the deployment's
address and its ``__Host-`` cookies as a real https deployment does. Card pictures come from
Scryfall as on the live app (fetched here, so a proxy in the environment is honoured); without
network the pages show their placeholders instead.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# the tests' offline guard (tests/conftest.py) stands aside for a live run: the card pictures
# are fetched from Scryfall here (Routes), nothing else leaves this machine
os.environ.setdefault("SCAN_LIVE", "1")

import tests.test_suggest_browser as harness  # noqa: E402
from tests.test_scan_browser import _chromium_path  # noqa: E402

PUBLIC_URL = "https://gateway.example.com"
SESSION_COOKIE = "__Host-mtg_session"  # the session cookie's name on https (auth_provider.cookie_name)
SCRYFALL = "https://cards.scryfall.io/"
USER_AGENT = "MTG-Assistant-Gateway docs screenshots (https://github.com/TheUncleBen/MTG-Assistant-Gateway)"
DESKTOP = dict(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
PHONE = dict(viewport={"width": 412, "height": 915}, device_scale_factor=1, is_mobile=True, has_touch=True)
MAX_HEIGHT = 1800  # a long desktop page is cut here, as the 0.7.2 set was
HOP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}

# name -> (path, who is signed in, a light variant too). "landing" is the Home page: since 0.7.2
# the gateway's front page is for signed-in members (a visitor is sent to the sign-in).
PAGES: dict[str, tuple[str, str, bool]] = {
    "landing": ("/", "alice", False),
    "account_unlinked": ("/account", "bob", False),
    "account_linked": ("/account", "alice", False),
    "decks": ("/decks", "alice", True),
    "deck": ("/decks/42", "alice", True),
    "deck_stacks": ("/decks/42?view=stacks", "alice", False),
    "deck_editor": ("/decks/42/edit", "alice", False),
    "deck_settings": ("/decks/42/settings", "alice", False),
    "new_deck": ("/decks/new", "alice", False),
    "proposals_list": ("/proposals", "alice", False),
    "proposal_pending": ("/proposals/{pending}", "alice", False),
}


def _server(tmp: Path) -> harness.Server:
    """The browser tests' server, public at the example address instead of its loopback one."""
    make_settings = harness.make_settings
    harness.make_settings = lambda path, **kw: make_settings(path, **{**kw, "public_url": PUBLIC_URL})
    try:
        return harness.Server(tmp, writes_enabled=True)
    finally:
        harness.make_settings = make_settings


def _dress_deck(server: harness.Server) -> None:
    """The sample deck as Archidekt sends a real one: each card's Scryfall id and price (the
    fixture export's columns the deck fixture leaves out) and a cover, so the pages draw pictures
    and prices instead of placeholders."""
    from mtg_gateway.archidekt import art_url
    from mtg_gateway.archidekt_csv import parse_export

    fixture = ROOT / "tests" / "fixtures" / "sample_deck.csv"
    by_name = {c.name: c for c in parse_export(fixture.read_text(encoding="utf-8"))}
    deck = server.ark.decks[42]
    for row in deck["cards"]:
        card = row["card"]
        export = by_name.get(card["oracleCard"]["name"])
        if export and export.scryfall_id:
            card["uid"] = export.scryfall_id
            card["prices"] = {"tcg": export.price}
    deck["featured"] = art_url(by_name["Aesi, Tyrant of Gyre Strait"].scryfall_id)


def _seed(server: harness.Server) -> dict[str, str]:
    """alice, linked to the fake Archidekt (the sample deck) with one waiting proposal; bob, signed
    in but not linked. Returns each one's browser session and the proposal's id."""
    _dress_deck(server)
    alice = server.sign_in()
    h = httpx.Client(base_url=server.base, headers={"Cookie": f"{SESSION_COOKIE}={alice}"})
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
    bob = secrets.token_urlsafe(32)
    server.db.upsert_user("user-2", email="bob@example.test", name="Bob", preferred_username="bob", groups=[])
    server.db.create_browser_session(bob, "user-2", 3600)
    edit = asyncio.run(
        server.app.state.gateway.decks.propose(
            "user-1",
            "42",
            [
                {"action": "add", "card_name": "Arcane Signet"},
                {"action": "remove", "card_name": "Counterspell"},
                {"action": "set_quantity", "card_name": "Forest", "quantity": 13},
            ],
        )
    )
    return {"alice": alice, "bob": bob, "pending": edit["proposal_id"]}


class Routes:
    """Answers the browser's requests: the example host from the local server (as ``sid``'s
    browser), card pictures from Scryfall through httpx, each picture fetched once."""

    def __init__(self, server: harness.Server) -> None:
        self.server = server
        self.pictures: dict[str, tuple[int, str, bytes]] = {}
        self.http = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)

    def install(self, ctx, sid: str | None) -> None:
        def local(route) -> None:
            req = route.request
            headers = {k: v for k, v in req.headers.items() if k.lower() not in ("host", "cookie")}
            if sid:
                headers["Cookie"] = f"{SESSION_COOKIE}={sid}"
            url = self.server.base + req.url[len(PUBLIC_URL) :]
            resp = ctx.request.fetch(url, method=req.method, headers=headers, data=req.post_data_buffer)
            kept = {k: v for k, v in resp.headers.items() if k.lower() not in HOP_HEADERS}
            route.fulfill(status=resp.status, headers=kept, body=resp.body())

        def picture(route) -> None:
            url = route.request.url
            if url not in self.pictures:
                try:
                    r = self.http.get(url)
                    self.pictures[url] = (r.status_code, r.headers.get("content-type", ""), r.content)
                except httpx.HTTPError as exc:
                    print(f"{url}: {exc}", file=sys.stderr)
                    self.pictures[url] = (502, "text/plain", b"")
            status, kind, body = self.pictures[url]
            route.fulfill(status=status, content_type=kind, body=body)

        ctx.route(f"{PUBLIC_URL}/**", local)
        ctx.route(f"{SCRYFALL}**", picture)


def _shoot(page, path: Path, *, phone: bool) -> None:
    """A phone shows its screen; a desktop page is shown whole (its height at most MAX_HEIGHT,
    the window grown to it so a bar fixed at the bottom sits at the capture's bottom)."""
    if not phone:
        size = page.viewport_size
        for _ in range(2):  # a page whose height follows the window settles on the second pass
            height = min(MAX_HEIGHT, page.evaluate("document.documentElement.scrollHeight"))
            page.set_viewport_size({"width": size["width"], "height": max(size["height"], height)})
        page.wait_for_timeout(300)
    page.screenshot(path=str(path), full_page=False)


def _compress(path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a test extra
        return
    with Image.open(path) as im:
        im.save(path, optimize=True)


def main(out: Path) -> int:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        print("no Chromium build for Playwright", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        server = _server(Path(tmp))
        server.start()
        try:
            ids = _seed(server)
            routes = Routes(server)
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
                for name, (path, who, light) in PAGES.items():
                    variants = [("desktop", DESKTOP, "dark"), ("phone", PHONE, "dark")]
                    if light:
                        variants.append(("desktop-light", DESKTOP, "light"))
                    for suffix, device, scheme in variants:
                        ctx = browser.new_context(color_scheme=scheme, reduced_motion="reduce", **device)
                        routes.install(ctx, ids.get(who))
                        pg = ctx.new_page()
                        pg.goto(PUBLIC_URL + path.format(pending=ids["pending"]), wait_until="networkidle")
                        pg.wait_for_timeout(300)
                        file = out / f"{name}-{suffix}.png"
                        _shoot(pg, file, phone=device is PHONE)
                        _compress(file)
                        print(file.name, "-", re.sub(r"\s+", " ", pg.title()))
                        ctx.close()
                browser.close()
        finally:
            server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"))
