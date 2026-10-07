"""End-to-end browser test of the /scan page in headless Chromium (Playwright).

Runs the real gateway over HTTP with a fake Scryfall, signs a browser session
in, then drives the page: a synthetic photo of a card goes through the real
on-device OCR (Tesseract.js served from /scan/static/vendor), is resolved,
added, saved as a session and read back through the API. Skipped when the
vendored OCR assets or a Chromium build are not available.
"""

from __future__ import annotations

import glob
import os
import secrets
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from mtg_gateway.app import create_app
from mtg_gateway.db import Database
from mtg_gateway.oidc import OIDCClient
from mtg_gateway.scan.scryfall import ScryfallClient

from .conftest import CLIENT_ID, CLIENT_SECRET, IDP, make_settings
from .fake_scryfall import FakeScryfall

VENDOR = Path(__file__).resolve().parents[1] / "src" / "mtg_gateway" / "scan" / "static" / "vendor"
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
]

pytestmark = pytest.mark.skipif(
    not (VENDOR / "worker.min.js").exists(), reason="run scripts/fetch_ocr_assets.py first"
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chromium_path() -> str | None:
    """None lets Playwright use its own download; otherwise a preinstalled build."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover
        return None
    with sync_playwright() as p:
        if Path(p.chromium.executable_path).exists():
            return None
    for pattern in (
        "/opt/pw-browsers/chromium-*/chrome-linux/chrome",
        "/usr/bin/chromium*",
        "/usr/bin/google-chrome",
    ):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return "missing"


def card_photo(path: Path, name: str, info: tuple[str, str] | None = None) -> None:
    """A plausible card photo: dark surround, card with a light title bar carrying the name.

    ``info`` draws the modern bottom-left info block, e.g. ("0021 U", "FRC • EN") or with ★ for foil.
    """
    from PIL import Image, ImageDraw, ImageFont

    w, h = 1200, 1600
    im = Image.new("RGB", (w, h), (40, 36, 30))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([20, 20, w - 20, h - 20], radius=60, fill=(22, 24, 28))
    bar = (70, 60, w - 70, 200)
    d.rounded_rectangle(bar, radius=18, fill=(226, 222, 210))
    d.rectangle([70, 240, w - 70, 1000], fill=(80, 110, 150))
    font = None
    for cand in FONT_CANDIDATES:
        if Path(cand).exists():
            font = ImageFont.truetype(cand, 86)
            break
    assert font, "no TrueType font available for the synthetic card"
    d.text((110, 78), name, fill=(20, 20, 20), font=font)
    d.ellipse([w - 190, 85, w - 110, 165], outline=(20, 20, 20), width=6)  # a mana symbol
    d.text((w - 170, 92), "1", fill=(20, 20, 20), font=ImageFont.truetype(font.path, 60))
    if info:
        sans = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if not Path(sans).exists():
            pytest.skip("DejaVu Sans is needed to draw the info line")
        small = ImageFont.truetype(sans, 34)
        d.text((62, 1484), info[0], fill=(235, 235, 235), font=small)
        d.text((62, 1528), info[1], fill=(235, 235, 235), font=small)
    im.save(path, "JPEG", quality=90)


def _perspective_coeffs(target: list[tuple[float, float]], source: list[tuple[float, float]]) -> list[float]:
    """Pillow PERSPECTIVE coefficients mapping each ``target`` corner onto its ``source`` corner."""
    rows: list[list[float]] = []
    rhs: list[float] = []
    for (x, y), (sx, sy) in zip(target, source, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -sx * x, -sx * y])
        rhs.append(sx)
        rows.append([0, 0, 0, x, y, 1, -sy * x, -sy * y])
        rhs.append(sy)
    n = 8
    for col in range(n):  # Gaussian elimination with partial pivoting
        piv = max(range(col, n), key=lambda r: abs(rows[r][col]))
        rows[col], rows[piv] = rows[piv], rows[col]
        rhs[col], rhs[piv] = rhs[piv], rhs[col]
        for r in range(n):
            if r != col:
                f = rows[r][col] / rows[col][col]
                rows[r] = [a - f * b for a, b in zip(rows[r], rows[col], strict=True)]
                rhs[r] -= f * rhs[col]
    return [rhs[i] / rows[i][i] for i in range(n)]


def skewed_photo(path: Path, card: Path, corners: list[tuple[int, int]]) -> None:
    """A card photo as a phone takes it: the card at an angle on a lighter table, not filling the frame."""
    from PIL import Image

    w, h = 1400, 1800
    src = Image.open(card)
    src = src.crop((20, 20, src.width - 20, src.height - 20))  # the card itself, no dark surround
    coeffs = _perspective_coeffs(
        [(float(x), float(y)) for x, y in corners],
        [
            (0.0, 0.0),
            (float(src.width), 0.0),
            (float(src.width), float(src.height)),
            (0.0, float(src.height)),
        ],
    )
    warped = src.convert("RGBA").transform(
        (w, h), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC
    )
    table = Image.new("RGBA", (w, h), (176, 146, 108, 255))
    table.alpha_composite(warped)
    table.convert("RGB").save(path, "JPEG", quality=88)


class Server:
    def __init__(self, tmp_path: Path):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        settings = make_settings(
            tmp_path, public_url=self.base, allowed_hosts=["127.0.0.1", f"127.0.0.1:{self.port}"]
        )
        self.db = Database(settings.db_path)
        oidc = OIDCClient(IDP, CLIENT_ID, CLIENT_SECRET, settings.callback_url, settings.oidc_scopes)
        self.app = create_app(settings, db=self.db, oidc=oidc)
        # Sessions here are made directly in the database (sign_in), with no identity provider to
        # ask, so the live membership check is switched off (test_live_membership.py covers it).
        self.app.state.gateway.membership = None
        self.sf = FakeScryfall()
        service = self.app.state.gateway.scan
        service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=self.sf.client())
        self.server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        for _ in range(100):
            try:
                if httpx.get(f"{self.base}/healthz", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("gateway did not start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def sign_in(self, sub: str = "user-1") -> str:
        sid = secrets.token_urlsafe(32)
        self.db.upsert_user(
            sub, email="alice@example.test", name="Alice", preferred_username="alice", groups=[]
        )
        self.db.create_browser_session(sid, sub, 3600)
        return sid


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_scan_page_end_to_end(server: Server, tmp_path: Path) -> None:
    from playwright.sync_api import expect, sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    photo = tmp_path / "sol-ring.jpg"
    card_photo(photo, "Sol Ring")
    sid = server.sign_in()
    console: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=exe,
            args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--no-sandbox"],
        )
        ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))

        # Anonymous visitors are sent to sign in.
        anon = browser.new_context().new_page()
        anon.goto(f"{server.base}/scan")
        assert anon.url.endswith("/login?next=/scan") or "Sign-in" in anon.content()
        anon.close()

        page.goto(f"{server.base}/scan")
        expect(page.get_by_role("tab", name="Camera")).to_be_visible()
        # The fake camera streams, so the OCR engine loads (worker + wasm + language data, all same-origin).
        expect(page.locator("#cam-status")).to_contain_text("Ready", timeout=90_000)

        # Photo path: the synthetic card goes through the real OCR and the resolver.
        page.set_input_files("#photo-file", str(photo))
        expect(page.locator("#result h3")).to_have_text("Sol Ring", timeout=60_000)
        read = page.locator("#result .ocr").inner_text()
        assert "Sol" in read, read
        shots = os.environ.get("SCAN_SCREENSHOTS")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=f"{shots}/01-camera-match.png", full_page=True)
            (Path(shots) / "ocr-read.txt").write_text(read + "\n")
        page.locator("#result").get_by_role("button", name="Add", exact=True).click()
        expect(page.locator("#list-badge")).to_have_text("1")

        # Typing path with suggestions.
        page.get_by_role("tab", name="Type").click()
        page.fill("#type-input", "aesi")
        expect(page.locator(".suggest li").first).to_contain_text("Aesi", timeout=10_000)
        page.locator(".suggest li").first.click()
        expect(page.locator("#type-result h3")).to_have_text("Aesi, Tyrant of Gyre Strait")
        page.locator("#type-result input[type=number]").fill("1")
        page.locator("#type-result").get_by_role("button", name="Add", exact=True).click()
        expect(page.locator("#list-badge")).to_have_text("2")

        # A pasted list: exact lines are added, the rest asks for a decision.
        page.fill("#paste-input", "2 Sol Ring\nSol Rng")
        page.get_by_role("button", name="Resolve list").click()
        expect(page.locator("#type-result .notice.ok")).to_contain_text("Added 1 line")
        expect(page.locator("#type-result")).to_contain_text("Several cards match")
        expect(page.locator("#list-badge")).to_have_text("4")
        if shots:
            page.screenshot(path=f"{shots}/02-type-and-paste.png", full_page=True)

        # Save the session and confirm it through the JSON API and the MCP-side store.
        page.get_by_role("tab", name="List").click()
        page.fill("#session-name", "Playwright binder")
        page.get_by_role("button", name="Save scan").click()
        expect(page.locator("#list-msg .notice.ok")).to_contain_text(
            "Saved as Playwright binder", timeout=10_000
        )
        if shots:
            page.screenshot(path=f"{shots}/03-list-saved.png", full_page=True)
        page.get_by_role("tab", name="Sessions").click()
        expect(page.locator(".sessions li")).to_have_count(1)
        expect(page.locator(".sessions li")).to_contain_text("4 cards")
        if shots:
            page.screenshot(path=f"{shots}/04-sessions.png", full_page=True)

        # Installability pieces.
        assert page.evaluate("navigator.serviceWorker.getRegistration('/scan/').then(r => !!r)") is True
        manifest = page.evaluate("fetch('/scan/app.webmanifest').then(r => r.json())")
        assert manifest["display"] == "standalone" and manifest["start_url"] == "/scan"
        browser.close()

    sessions = server.db.tx
    with sessions() as c:
        rows = c.execute("SELECT name, items_json FROM scan_sessions").fetchall()
    assert len(rows) == 1 and rows[0]["name"] == "Playwright binder"
    assert "Sol Ring" in rows[0]["items_json"] and "Aesi" in rows[0]["items_json"]
    errors = [line for line in console if line.startswith("pageerror") or "Refused" in line or "CSP" in line]
    assert not errors, errors
    assert not any("cdn." in u for _, u in server.sf.requests)


def test_two_quick_scans_do_not_collide(server: Server) -> None:
    """The page queues its own lookups and quietly retries a busy answer caused by another caller."""
    import asyncio

    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sf = server.sf

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.4)  # long enough for two quick scans to overlap server-side
        return sf.handle(request)

    service = server.app.state.gateway.scan
    service.scryfall = ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(slow), base_url="https://scryfall.test"),
    )
    sid = server.sign_in()
    statuses: list[int] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.on(
            "response",
            lambda r: statuses.append(r.status) if r.url.endswith("/scan/api/resolve") else None,
        )
        page.goto(f"{server.base}/scan")
        page.wait_for_function("() => window.__scan && window.__scan.api")

        # Two scans fired at once: both resolve, and the page never sent them concurrently.
        names = page.evaluate(
            """() => {
                 const go = (name) => window.__scan.api('/scan/api/resolve', 'POST', { cards: [{ name }] });
                 return Promise.all([go('Sol Ring'), go('Aesi, Tyrant of Gyre Strait')])
                   .then((outs) => outs.map((o) => o.cards[0].card.name));
               }"""
        )
        assert names == ["Sol Ring", "Aesi, Tyrant of Gyre Strait"]
        assert statuses == [200, 200], statuses

        # Someone else (an assistant call) holds this account's lookup for a moment: the page
        # gets a 429 first, retries on its own and still shows the card.
        service._in_flight.add("user-1")
        threading.Timer(0.7, lambda: service._in_flight.discard("user-1")).start()
        statuses.clear()
        name = page.evaluate(
            """() => window.__scan.api('/scan/api/resolve', 'POST', { cards: [{ name: 'Sol Ring' }] })
                 .then((o) => o.cards[0].card.name)"""
        )
        assert name == "Sol Ring"
        assert statuses[0] == 429 and statuses[-1] == 200, statuses
        browser.close()


def test_photo_scan_reads_the_info_line_and_picks_the_printing(server: Server, tmp_path: Path) -> None:
    """The bottom-left info line gives set, number and the foil star; the resolver returns that printing."""
    from playwright.sync_api import expect, sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    plain = tmp_path / "sol-ring-frc.jpg"
    card_photo(plain, "Sol Ring", ("0021 U", "FRC • EN"))
    foil = tmp_path / "cultivate-msc-foil.jpg"
    card_photo(foil, "Cultivate", ("0172 U", "MSC ★ EN"))
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/scan")
        page.wait_for_function("() => window.__scan && window.__scan.parseInfoLine")

        # The parser on its own, without pixels: numbers lose leading zeros, glyphs decide foil.
        parsed = page.evaluate("() => window.__scan.parseInfoLine('0472 R\\nCMR • EN', [], null)")
        assert (parsed["number"], parsed["rarity"], parsed["set"], parsed["lang"], parsed["foil"]) == (
            "472",
            "R",
            "cmr",
            "en",
            False,
        )
        star = page.evaluate("() => window.__scan.parseInfoLine('123/264 C\\nM15 ★ EN', [], null)")
        assert star["foil"] is True and star["number"] == "123"
        # The request builder honours the set lock and the foil default (the pages thread's controls).
        page.evaluate("() => { window.__scan.setSetLock('cmr'); window.__scan.setFoilDefault(true); }")
        read = "{number: '472', set: '', foil: null}"
        req = page.evaluate(f"() => window.__scan.cardRequest('Sol Ring', {read})")
        assert req == {"name": "Sol Ring", "set": "cmr", "collector_number": "472", "foil": True}
        assert page.evaluate("() => window.__scan.getSetLock()") == "cmr"
        # No number read: the set lock still narrows the name lookup instead of being dropped.
        req = page.evaluate("() => window.__scan.cardRequest('Sol Ring', {number: '', set: '', foil: null})")
        assert req == {"name": "Sol Ring", "set": "cmr", "foil": True}
        page.evaluate("() => { window.__scan.setSetLock(''); window.__scan.setFoilDefault(false); }")
        # A foil and a non-foil copy of one printing are two list lines; a repeat merges into its own.
        page.evaluate(
            """() => { const s = window.__scan; s.state.items = [];
                 const card = {name: 'Sol Ring', scryfall_id: 'id-1', set: 'frc', collector_number: '21'};
                 s.addItem({card, status: 'printing', foil: false}, 1);
                 s.addItem({card, status: 'printing', foil: true}, 1);
                 s.addItem({card, status: 'printing', foil: true}, 2); }"""
        )
        lines = page.evaluate("() => window.__scan.state.items.map((it) => [it.foil, it.quantity])")
        assert lines == [[False, 1], [True, 3]]
        page.evaluate("() => { window.__scan.state.items = []; }")

        # Real OCR on a photo with an info line: the printing, not Scryfall's default, comes back.
        # (Chosen before the engine finished loading: the scan waits for it rather than failing.)
        page.set_input_files("#photo-file", str(plain))
        expect(page.locator("#result h3")).to_have_text("Sol Ring", timeout=90_000)
        pending = page.evaluate("() => window.__scan.state.pending")
        info = pending["ocr"]["info"]
        assert (info["set"], info["number"]) == ("frc", "21"), info
        assert pending["status"] == "printing", pending["note"]
        assert (pending["card"]["set"], pending["card"]["collector_number"]) == ("frc", "21")
        assert pending["foil"] is False, info

        # A foil star: set and number pick the exact card, foil is carried.
        page.set_input_files("#photo-file", str(foil))
        expect(page.locator("#result h3")).to_have_text("Cultivate", timeout=90_000)
        pending = page.evaluate("() => window.__scan.state.pending")
        info = pending["ocr"]["info"]
        assert (info["set"], info["number"], info["foil"]) == ("msc", "172", True), info
        assert pending["status"] == "printing" and pending["foil"] is True
        page.locator("#result").get_by_role("button", name="Add", exact=True).click()
        assert page.evaluate("() => window.__scan.state.items[0].foil") is True
        browser.close()


def test_skewed_photo_is_flattened_before_reading(server: Server, tmp_path: Path) -> None:
    """A card at an angle on a table: edges are found, the card is flattened, title and info line read."""
    from playwright.sync_api import expect, sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    card = tmp_path / "sol-ring-card.jpg"
    card_photo(card, "Sol Ring", ("0021 U", "FRC • EN"))
    photo = tmp_path / "sol-ring-on-table.jpg"
    skewed_photo(photo, card, [(300, 220), (1130, 300), (1060, 1620), (230, 1520)])
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/scan")
        page.wait_for_function("() => window.__scan && window.__scan.flattenCard && window.ScanGeometry")

        # Glare is measured on raw pixels: a blown-out crop reads as all glare, a grey one as none.
        glare = page.evaluate(
            """() => { const c = document.createElement('canvas'); c.width = 40; c.height = 10;
                 const ctx = c.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 40, 10);
                 const hot = window.__scan.glareOf(c, {x: 0, y: 0, w: 40, h: 10});
                 ctx.fillStyle = '#888'; ctx.fillRect(0, 0, 40, 10);
                 return [hot, window.__scan.glareOf(c, {x: 0, y: 0, w: 40, h: 10})]; }"""
        )
        assert glare == [1, 0]
        # Without a camera track there is no torch; the hooks say so instead of throwing.
        torch = page.evaluate("() => [window.__scan.torchSupported(), window.__scan.getTorch()]")
        assert torch == [False, False]

        page.set_input_files("#photo-file", str(photo))
        expect(page.locator("#result h3")).to_have_text("Sol Ring", timeout=90_000)
        pending = page.evaluate("() => window.__scan.state.pending")
        assert pending["ocr"]["flattened"] is True, pending["ocr"]
        assert pending["ocr"]["edges"] >= 0.45, pending["ocr"]
        assert pending["ocr"]["glare"] < 0.08, pending["ocr"]
        info = pending["ocr"]["info"]
        assert (info["set"], info["number"]) == ("frc", "21"), info
        assert pending["status"] == "printing" and pending["card"]["collector_number"] == "21"
        # Memory stays bounded: the warp sampled only the card's box, not the whole picture.
        sampled = pending["ocr"]["sampled"]
        assert sampled["w"] <= 1000 and sampled["h"] <= 1500, sampled

        # A big phone photo (here 4800 x 6400, about 31 MP) is worked on as a copy with the long side
        # at 2400 px, and the warp samples at most 2048 px on its long side.
        from PIL import Image

        big = tmp_path / "sol-ring-big.jpg"
        Image.open(photo).resize((4800, 6400), Image.Resampling.BICUBIC).save(big, "JPEG", quality=80)
        page.evaluate("() => { window.__scan.state.pending = null; }")
        page.set_input_files("#photo-file", str(big))
        page.wait_for_function(
            "() => window.__scan.state.pending && window.__scan.state.pending.card", timeout=90_000
        )
        ocr = page.evaluate("() => window.__scan.state.pending.ocr")
        assert page.locator("#result h3").text_content() == "Sol Ring"
        assert ocr["flattened"] is True and max(ocr["picture"]["w"], ocr["picture"]["h"]) == 2400, ocr
        assert max(ocr["sampled"]["w"], ocr["sampled"]["h"]) <= 2048, ocr
        browser.close()


def test_continuous_scan_sorts_reads_into_tiers(server: Server) -> None:
    """Only agreed cards are added without a tap, once per card shown; shaky reads wait or are kept."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/scan")
        page.wait_for_function("() => window.__scan && window.__scan.tierOf")
        # Stand-in OCR: the title and info reads are whatever the test says.
        page.evaluate(
            """() => { window.__scanOcrOverride = async (canvas, kind) => kind === 'info'
                 ? {text: window.__i || '', confidence: window.__i ? 90 : 0, words: []}
                 : {text: window.__t, confidence: window.__c, words: []};
                 const s = window.__scan.state; s.items = []; s.continuous = true;
                 window.__events = [];
                 ['auto-added', 'unsure', 'unidentified', 'undo', 'continuous'].forEach((n) =>
                   document.addEventListener('scan:' + n, () => window.__events.push(n))); }"""
        )

        def scan(text: str, conf: float, info: str = "") -> dict:
            return page.evaluate(
                """async ([t, c, i]) => { window.__t = t; window.__c = c; window.__i = i;
                     const cv = document.createElement('canvas'); cv.width = 400; cv.height = 100;
                     const ctx = cv.getContext('2d'); ctx.fillStyle = '#ddd'; ctx.fillRect(0, 0, 400, 100);
                     const title = {x: 0, y: 0, w: 400, h: 60}, info = {x: 0, y: 60, w: 200, h: 40};
                     await window.__scan.scanRegion(cv, title, info, null);
                     const s = window.__scan.state;
                     return {items: s.items.map((i) => [i.name, i.quantity]), undo: s.autoAdded.length,
                             pending: s.pending && s.pending.card ? s.pending.card.name : null,
                             unidentified: s.unidentified.map((u) => u.text),
                             stats: Object.assign({}, s.stats)}; }""",
                [text, conf, info],
            )

        def card_left() -> None:
            page.evaluate("() => { for (let i = 0; i < 3; i++) window.__scan.noCardFrame(); }")

        def clear_pending() -> None:
            page.evaluate("() => { window.__scan.state.pending = null; }")

        # A clean read of a name no other card's name begins with is added without a tap.
        first = scan("Sol Ring", 95)
        assert first["items"] == [["Sol Ring", 1]] and first["undo"] == 1 and first["pending"] is None
        assert first["stats"]["lookups"] == 1
        # The same card still in front of the camera is not added twice, and the identical read
        # is answered without asking the server again.
        again = scan("Sol Ring", 97)
        assert again["items"] == [["Sol Ring", 1]]
        assert again["stats"] == {**first["stats"], "frames": 2, "reused": 1}
        # A blurry frame of the same card (read as rubbish) does not clear the way for a second add.
        out = scan("Xq Zzyx", 95)
        assert out["unidentified"] == ["Xq Zzyx"] and out["items"] == [["Sol Ring", 1]]
        assert scan("Sol Ring", 95)["items"] == [["Sol Ring", 1]]
        # Only the card leaving the frame does.
        card_left()
        assert scan("Sol Ring", 95)["items"] == [["Sol Ring", 2]]
        # Undo takes one copy off and keeps the card out until it has left the frame.
        undone = page.evaluate("() => window.__scan.undoLastAdd()")
        assert undone["name"] == "Sol Ring"
        assert scan("Sol Ring", 95)["items"] == [["Sol Ring", 1]]
        card_left()
        assert scan("Sol Ring", 95)["items"] == [["Sol Ring", 2]]
        page.evaluate("() => window.__scan.undoLastAdd()")
        # A title that disagrees with the collector line waits for a tap even though the name is exact.
        out = scan("Sol Ring", 95, "0236 U\nMH2 • EN")
        assert out["pending"] == "Sol Ring" and out["items"] == [["Sol Ring", 1]]
        clear_pending()
        # So does an exact name that begins another card's name (Mountain, Mountain Goat).
        out = scan("Mountain", 95)
        assert out["pending"] == "Mountain" and out["items"] == [["Sol Ring", 1]]
        clear_pending()
        # Title and collector line agreeing is certain.
        card_left()
        out = scan("Sol Ring", 95, "0021 U\nFRC • EN")
        assert out["items"] == [["Sol Ring", 2]] and out["pending"] is None
        lookups = out["stats"]["lookups"]
        # A read under the OCR confidence floor is kept for later without a lookup.
        out = scan("Sol Ring", 30)
        assert out["unidentified"][-1] == "Sol Ring" and out["items"] == [["Sol Ring", 2]]
        assert out["stats"]["lookups"] == lookups and out["stats"]["skipped"] == 1
        # The same shaky read again is not kept twice.
        assert scan("Sol Ring", 30)["unidentified"] == out["unidentified"]
        # A clean name under the auto-add bar, or a fuzzy match, waits for a tap.
        out = scan("Sol Ring", 60)
        assert out["pending"] == "Sol Ring" and out["items"] == [["Sol Ring", 2]]
        clear_pending()
        out = scan("Aesi Tyrant of Gyre Stralt", 95)
        assert out["pending"] == "Aesi, Tyrant of Gyre Strait" and out["items"] == [["Sol Ring", 2]]
        assert page.evaluate("() => window.__scan.tierOf(window.__scan.state.pending)") == "unsure"
        # The completeness check fails closed: a list without the name itself (a misread, or a
        # query the server declined) is not a pass.
        assert page.evaluate("() => window.__scan.nameIsComplete('Sol Rng')") is False
        assert page.evaluate("() => window.__scan.nameIsComplete('x'.repeat(101))") is False
        assert page.evaluate("() => window.__scan.nameIsComplete('Sol Ring')") is True
        assert page.evaluate("() => window.__events") == [
            "auto-added",
            "unidentified",
            "auto-added",
            "undo",
            "auto-added",
            "undo",
            "unsure",
            "unsure",
            "auto-added",
            "unidentified",
            "unidentified",
            "unsure",
            "unsure",
        ]
        # Start and stop are idempotent and announce themselves.
        page.evaluate("() => { window.__scan.state.pending = null; window.__scan.state.continuous = false; }")
        assert page.evaluate("() => window.__scan.startContinuous()") is True
        assert page.evaluate("() => window.__scan.startContinuous()") is False
        assert page.evaluate("() => window.__scan.state.continuous") is True
        page.evaluate("() => window.__scan.stopContinuous()")
        assert page.evaluate("() => window.__scan.state.continuous") is False
        assert page.evaluate("() => window.__events.slice(-2)") == ["continuous", "continuous"]
        browser.close()


def test_printing_picker_hooks(server: Server) -> None:
    """Pick another printing of the same card, never a different card; foil follows the finishes."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/scan")
        page.wait_for_function("() => window.__scan && window.__scan.loadPrints")
        out = page.evaluate(
            """async () => {
                 const s = window.__scan;
                 const body = {cards: [{name: 'Aesi, Tyrant of Gyre Strait'}]};
                 const res = (await s.api('/scan/api/resolve', 'POST', body)).cards[0];
                 const prints = await s.loadPrints(res.card.oracle_id);
                 const sets = prints.map((c) => c.set);
                 const foilOnly = prints.find((c) => c.set === 'cmr');
                 const plain = prints.find((c) => c.set === 'dsc');
                 const other = {scryfall_id: 'x', oracle_id: 'other', name: 'Sol Ring'};
                 const noOracle = Object.assign({}, foilOnly, {oracle_id: undefined});
                 const wrong = [s.choosePrinting(res, other), s.choosePrinting(res, noOracle)];
                 // An old item without finishes may be marked either way.
                 const legacy = s.foilChoices({name: 'Sol Ring'});
                 s.choosePrinting(res, foilOnly);
                 const afterFoilOnly = [res.card.set, res.status, res.foil, s.setFoil(res, false), res.foil];
                 s.choosePrinting(res, plain);
                 const afterPlain = [res.card.set, res.foil, s.setFoil(res, true), res.foil];
                 s.state.items = []; s.addItem(res, 1);
                 const item = s.state.items[0];
                 s.choosePrinting(item, foilOnly);
                 const picked = [item.card.set, item.foil, item.status, s.state.dirty];
                 // A second DSC line re-pointed at CMR folds into the first: one line, quantity summed.
                 s.addItem(Object.assign({}, res, {card: plain, foil: false}), 2);
                 const second = s.state.items[1];
                 const before = s.state.items.length;
                 let merged = null;
                 document.addEventListener('scan:printing', (e) => { merged = e.detail.merged; },
                   {once: true});
                 s.choosePrinting(second, foilOnly);
                 const lines = s.state.items.map((it) => [it.card.set, it.quantity]);
                 return {sets, wrong, legacy, afterFoilOnly, afterPlain, item: picked,
                         before, merged, lines}; }"""
        )
        assert out["sets"] == ["sld", "dsc", "plst", "cmr"]
        assert out["wrong"] == [False, False] and out["legacy"] == {"foil": True, "nonfoil": True}
        # Foil-only printing: foil is on and cannot be turned off; nonfoil-only: the reverse.
        assert out["afterFoilOnly"] == ["cmr", "printing", True, False, True]
        assert out["afterPlain"] == ["dsc", False, False, False]
        assert out["item"] == ["cmr", True, "printing", True]
        assert out["before"] == 2 and out["merged"] is True and out["lines"] == [["cmr", 3]]
        browser.close()


def test_printing_picker_keeps_the_chosen_finish(server: Server) -> None:
    """Picking a printing with Foil on never folds the line into that printing's non-foil line."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/scan")
        page.wait_for_function("() => window.__scan && window.__scan.openPicker")
        both = page.evaluate(
            """async () => {
                 const s = window.__scan;
                 const body = {cards: [{name: 'Aesi, Tyrant of Gyre Strait'}]};
                 const res = (await s.api('/scan/api/resolve', 'POST', body)).cards[0];
                 const prints = await s.loadPrints(res.card.oracle_id);
                 const both = prints.find((c) => c.finishes.includes('foil')
                   && c.finishes.includes('nonfoil'));
                 const plain = prints.find((c) => c.set === 'dsc');
                 s.state.items = [];
                 s.addItem(Object.assign({}, res, {card: both, foil: false}), 1);   // a non-foil line of P
                 s.addItem(Object.assign({}, res, {card: plain, foil: false}), 1);  // the line to re-point
                 s.showTab('list');
                 return both ? [both.set, both.collector_number] : null; }"""
        )
        assert both is not None
        # Open the picker from the second line, turn Foil on, pick printing P.
        page.locator("ul.items .setbtn").nth(1).click()
        page.wait_for_selector("#picker .tile")
        page.locator("#picker").get_by_role("switch", name="Foil").click()
        code = f"{both[0].upper()} {both[1]}"
        page.locator("#picker .tile", has=page.locator(".setcode", has_text=code)).first.click()
        page.wait_for_selector("#picker", state="detached")
        lines = page.evaluate(
            "() => window.__scan.state.items.map((it) => [it.card.set, it.foil, it.quantity])"
        )
        assert lines == [[both[0], False, 1], [both[0], True, 1]]
        # Closing by a tile (not Escape) leaves no Escape handler behind: a later Escape does nothing odd,
        # and reopening then closing with the X button works the same way.
        page.keyboard.press("Escape")
        page.locator("ul.items .setbtn").first.click()
        page.wait_for_selector("#picker .tile")
        page.locator("#picker").get_by_role("button", name="Close").click()
        page.wait_for_selector("#picker", state="detached")
        assert page.evaluate("() => window.__scan.state.items.length") == 2
        browser.close()


# ---------------------------------------------------------------- Android glue
ANDROID_GLUE = Path(__file__).resolve().parents[1] / "android" / "app" / "src" / "main" / "kotlin"
ANDROID_GLUE = ANDROID_GLUE / "local" / "mtgassistantgateway" / "app" / "ScanGlue.kt"


def android_glue() -> str:
    """The JavaScript the Android app installs on /scan, lifted out of ScanGlue.kt as the app ships it."""
    import re

    src = ANDROID_GLUE.read_text(encoding="utf-8")
    hooks = re.search(r"val HOOKS = listOf\(([^)]*)\)", src).group(1)
    names = re.findall(r'"([A-Za-z]+)"', hooks)
    photo_max = re.search(r"const val PHOTO_MAX = (\d+)", src).group(1)
    js = re.search(r'val INSTALL_TEMPLATE: String = """\n(.*?)"""', src, re.S).group(1)
    js = js.replace("$PHOTO_MAX", photo_max).replace("__NONCE__", GLUE_NONCE)
    js = re.sub(r"\$\{HOOKS\.joinToString[^}]*\}\s*\}", ", ".join(f"'{n}'" for n in names), js)
    assert "$" not in js, "unexpanded Kotlin template in the glue"
    return js


GLUE_NONCE = "0123456789abcdef0123456789abcdef"


def test_android_glue_drives_the_real_page(server: Server, tmp_path: Path) -> None:
    """The app's glue against the real /scan page: one shot waits for a tap, continuous mode adds
    clean reads once, Undo works, a frame without a card is reported, and leaving restores the page."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    import base64

    from PIL import Image

    card = tmp_path / "sol-ring-card.jpg"
    card_photo(card, "Sol Ring")
    # As the phone takes it: the card on a table, roughly inside the dashed box, edges to find.
    photo = tmp_path / "sol-ring.jpg"
    skewed_photo(photo, card, [(300, 220), (1130, 300), (1060, 1620), (230, 1520)])
    blank = tmp_path / "table.jpg"
    Image.new("RGB", (1400, 1800), (176, 146, 108)).save(blank, "JPEG", quality=85)
    b64 = {p.name: base64.b64encode(p.read_bytes()).decode() for p in (photo, blank)}
    # The app serves each photo at a gateway-origin URL it intercepts itself; a blob: URL (allowed
    # by the page's img-src too) stands in for that here.
    to_blob = """(b) => { const bin = atob(b), a = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
        return URL.createObjectURL(new Blob([a], {type: 'image/jpeg'})); }"""
    sid = server.sign_in()
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/scan")
        page.wait_for_function("() => window.__scan && window.__scan.flattenCard")
        # The app's bridge object and a stand-in OCR (the title read is whatever the test says).
        page.evaluate(
            """() => { window.__events = [];
                 window.MtgNative = { scanEvent: (j) => window.__events.push(JSON.parse(j)),
                                      openCamera: () => { window.__opened = (window.__opened || 0) + 1; },
                                      version: () => 'test' };
                 window.__t = 'Sol Ring';
                 window.__scanOcrOverride = async (canvas, kind) => kind === 'info'
                   ? {text: '', confidence: 0, words: []} : {text: window.__t, confidence: 95, words: []};
                 window.__scan.state.items = []; }"""
        )
        glue = android_glue()
        assert page.evaluate(glue) == "installed"
        assert page.evaluate(glue) == "present"
        assert page.evaluate("() => window.__mtgNative.ready()") is True

        def events() -> list[dict]:
            return page.evaluate("() => window.__events.splice(0)")

        urls = {name: page.evaluate(to_blob, data) for name, data in b64.items()}
        seqs: list[int] = []

        def shoot(name: str, guide: str = "{x: 0.12, y: 0.08, w: 0.72, h: 0.84}") -> list[dict]:
            # The dashed box as the app reports it, roughly where the card lies in the picture.
            out = page.evaluate(f"() => window.__mtgNative.onPhoto({urls[name]!r}, {guide})")
            assert out.startswith("ok:"), out
            seqs.append(int(out[3:]))
            page.wait_for_function(
                "() => window.__events.some((e) => e.type === 'done' || e.type === 'no-card')", timeout=60_000
            )
            evs = events()
            # Every event names the glue's nonce and the photo it belongs to, so the app can drop stale ones.
            assert all(e["nonce"] == GLUE_NONCE and e["seq"] == seqs[-1] for e in evs), evs
            return evs

        # Opening the phone camera parks the page on its list tab, with a hidden status line to read back.
        assert page.evaluate("() => window.__mtgNative.begin()") == "ok"
        assert page.evaluate("() => window.__scan.state.tab") == "list"
        assert page.locator("#scan-app #cam-status[data-mtg-native]").count() == 1

        # One shot without a box: the whole picture is read, and the result waits on the page for a
        # tap, as the page's own Photo button does.
        evs = shoot("sol-ring.jpg", "null")
        assert [e["type"] for e in evs] == ["done"], evs
        assert evs[0]["pending"] is True and evs[0]["total"] == 0
        assert "Matched" in evs[0]["status"], evs[0]
        assert page.evaluate("() => window.__scan.state.pending.card.name") == "Sol Ring"
        page.evaluate("() => { window.__scan.state.pending = null; }")

        # Continuous: a clean read is added without a tap, the same card still in view is not added twice.
        assert page.evaluate("() => window.__mtgNative.setContinuous(true)") == "ok"
        evs = shoot("sol-ring.jpg")
        assert [e["type"] for e in evs] == ["auto-added", "done"], evs
        assert evs[0]["name"] == "Sol Ring" and evs[0]["total"] == 1 and evs[0]["undoable"] == 1
        assert evs[1]["pending"] is False and evs[1]["total"] == 1
        assert seqs == [1, 2]
        evs = shoot("sol-ring.jpg")
        assert [e["type"] for e in evs] == ["done"] and "Still Sol Ring" in evs[0]["status"], evs
        items = "() => window.__scan.state.items.map((i) => [i.name, i.quantity])"
        assert page.evaluate(items) == [["Sol Ring", 1]]
        # Undo takes the copy off and reports it.
        assert page.evaluate("() => window.__mtgNative.undo()") == "ok"
        evs = events()
        assert [e["type"] for e in evs] == ["undo"] and evs[0]["total"] == 0 and evs[0]["undoable"] == 0, evs
        assert page.evaluate("() => window.__mtgNative.undo()") == "nothing"
        # A frame with no card in it reads nothing and says so; three of them let the card come back.
        for frames in (1, 2, 3):
            evs = shoot("table.jpg")
            assert [e["type"] for e in evs] == ["no-card"] and evs[0]["frames"] == frames, evs
        evs = shoot("sol-ring.jpg")
        assert [e["type"] for e in evs] == ["auto-added", "done"], evs
        # A shaky read (a name that begins another card's name) waits on the page.
        page.evaluate("() => { window.__t = 'Mountain'; }")
        evs = shoot("sol-ring.jpg")
        assert [e["type"] for e in evs] == ["unsure", "done"], evs
        assert evs[0]["name"] == "Mountain" and evs[1]["pending"] is True

        # Closing the phone camera: the page goes back to its camera tab only because a read waits there.
        assert page.evaluate("() => window.__mtgNative.end()") == "pending"
        assert page.evaluate("() => window.__scan.state.tab") == "camera"
        # Inside the app the camera tab offers the phone camera; a browser (no MtgNative) does not.
        assert page.locator("#phone-camera").count() == 1
        page.locator("#phone-camera").click()
        assert page.evaluate("() => window.__opened") == 1
        assert page.evaluate("() => window.__mtgNative.ready()") is True
        assert page.evaluate("() => window.__scan.state.continuous") is False
        assert page.locator("#cam-status[data-mtg-native]").count() == 0
        page.evaluate("() => { window.__scan.state.pending = null; }")
        assert page.evaluate("() => window.__mtgNative.begin()") == "ok"
        assert page.evaluate("() => window.__mtgNative.end()") == "ok"
        assert page.evaluate("() => window.__scan.state.tab") == "list"
        assert errors == []
        browser.close()
