"""Phone photos as they come out of a real phone, through the /scan page's real code in headless
Chromium: the website's Photo button and the Android app's glue (ScanGlue.kt).

The card is drawn like a printed one (black border, a framed title bar, art, type line, text box,
the info line in the bottom border) and laid on a wooden table at a few angles, sizes and
sharpnesses, as in the 0.7.46 review's 40-photo stress set. Most tests use the real text
recognition (skipped without the OCR assets); the rest stand it in.
"""

from __future__ import annotations

import base64
import math
import random
import time
from pathlib import Path

import pytest

from .test_scan_browser import (  # noqa: F401
    GLUE_NONCE,
    VENDOR,
    _chromium_path,
    _perspective_coeffs,
    android_glue,
    server,
)

pytest.importorskip("playwright")

needs_ocr = pytest.mark.skipif(
    not (VENDOR / "worker.min.js").exists(), reason="run scripts/fetch_ocr_assets.py first"
)
SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def printed_card(name: str, info: tuple[str, str], w: int = 1260):
    """A 63 x 88 mm card ``w`` px wide, drawn like a printed one: black border, framed title bar
    starting 7% in, artwork, type line, text box, and the info line in the bottom border."""
    from PIL import Image, ImageDraw, ImageFont

    if not Path(SERIF).exists() or not Path(SANS).exists():
        pytest.skip("DejaVu fonts are needed to draw the card")
    h = round(w * 88 / 63)
    mm = w / 63
    im = Image.new("RGB", (w, h), (12, 12, 14))
    d = ImageDraw.Draw(im)
    b = 3 * mm
    d.rounded_rectangle([b, b, w - b, 83.6 * mm], radius=2 * mm, fill=(170, 175, 160))
    tb = [b + 1.5 * mm, b + 1.8 * mm, w - b - 1.5 * mm, b + 7.2 * mm]
    d.rounded_rectangle(tb, radius=1.2 * mm, fill=(222, 216, 200), outline=(60, 60, 60), width=3)
    d.text(
        (tb[0] + 1.6 * mm, tb[1] + 1.0 * mm),
        name,
        fill=(15, 15, 15),
        font=ImageFont.truetype(SERIF, int(3.3 * mm)),
    )
    d.ellipse(
        [tb[2] - 6 * mm, tb[1] + 1.2 * mm, tb[2] - 2.2 * mm, tb[3] - 1.2 * mm],
        fill=(200, 200, 200),
        outline=(20, 20, 20),
        width=3,
    )
    art = [b + 2 * mm, tb[3] + 0.6 * mm, w - b - 2 * mm, h * 0.555]
    rnd = random.Random(name)
    aw, ah = int(art[2] - art[0]), int(art[3] - art[1])
    aim = Image.new("RGB", (aw, ah), (90, 100, 120))
    ad = ImageDraw.Draw(aim)
    for _ in range(60):
        x, y, r = rnd.uniform(0, aw), rnd.uniform(0, ah), rnd.uniform(10, aw * 0.12)
        ad.ellipse(
            [x - r, y - r, x + r, y + r],
            fill=(rnd.randint(20, 200), rnd.randint(40, 160), rnd.randint(60, 220)),
        )
    im.paste(aim, (int(art[0]), int(art[1])))
    d.rectangle(art, outline=(30, 30, 30), width=3)
    ty = [b + 1.5 * mm, h * 0.56, w - b - 1.5 * mm, h * 0.56 + 5 * mm]
    d.rounded_rectangle(ty, radius=1 * mm, fill=(222, 216, 200), outline=(60, 60, 60), width=3)
    d.text(
        (ty[0] + 1.6 * mm, ty[1] + 0.9 * mm),
        "Artifact",
        fill=(15, 15, 15),
        font=ImageFont.truetype(SERIF, int(2.8 * mm)),
    )
    tx = [b + 2.2 * mm, ty[3] + 0.6 * mm, w - b - 2.2 * mm, 82.6 * mm]
    d.rectangle(tx, fill=(232, 228, 214))
    d.text(
        (tx[0] + 1.5 * mm, tx[1] + 2 * mm),
        "{T}: Add {C}{C}.",
        fill=(20, 20, 20),
        font=ImageFont.truetype(SERIF, int(2.6 * mm)),
    )
    small = ImageFont.truetype(SANS, int(1.35 * mm))
    d.text((3.4 * mm, 84.4 * mm), info[0], fill=(235, 235, 235), font=small)
    d.text((3.4 * mm, 86.0 * mm), info[1], fill=(235, 235, 235), font=small)
    return im


def table_photo(
    out: Path,
    size: tuple[int, int] = (3000, 4000),
    frac: float = 0.55,
    rot: float = 0.0,
    blur: float = 0.0,
    name: str = "Sol Ring",
    info: tuple[str, str] = ("0021 U", "FRC • EN"),
    exif6: bool = False,
) -> Path:
    """The card on a wooden table, ``frac`` of the picture's width wide, turned ``rot`` degrees with a
    little perspective, blurred by ``blur`` of the picture's width (out of focus), saved as a JPEG.
    ``exif6`` stores the pixels sideways with EXIF orientation 6, as phone cameras do."""
    from PIL import Image, ImageFilter

    W, H = size
    cw = W * frac
    ch = cw * 88 / 63
    x0, y0 = W / 2 - cw / 2, H / 2 - ch / 2
    t = 0.03
    pts = [
        (x0 + t * cw, y0),
        (x0 + cw, y0 + t * cw * 0.5),
        (x0 + cw - t * cw * 0.4, y0 + ch),
        (x0, y0 + ch - t * cw * 0.3),
    ]
    a = math.radians(rot)
    c, s = math.cos(a), math.sin(a)
    pts = [
        (W / 2 + (x - W / 2) * c - (y - H / 2) * s, H / 2 + (x - W / 2) * s + (y - H / 2) * c) for x, y in pts
    ]
    card = printed_card(name, info, 2520)
    cw_px = int(min(max(cw * 1.1, 600), 6000))
    card = card.resize((cw_px, round(cw_px * 88 / 63)), Image.Resampling.LANCZOS)
    coeffs = _perspective_coeffs(pts, [(0, 0), (card.width, 0), (card.width, card.height), (0, card.height)])
    warped = card.convert("RGBA").transform(
        (W, H), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BILINEAR
    )
    table = Image.new("RGBA", (W, H), (176, 146, 108, 255))
    table.alpha_composite(warped)
    im = table.convert("RGB")
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(radius=W * blur))
    if exif6:
        exif = Image.Exif()
        exif[0x0112] = 6
        im.transpose(Image.Transpose.ROTATE_90).save(out, "JPEG", quality=90, exif=exif)
    else:
        im.save(out, "JPEG", quality=90)
    return out


@pytest.fixture
def exe() -> str | None:
    """The Chromium to run (looked up before a test opens its own Playwright)."""
    found = _chromium_path()
    if found == "missing":
        pytest.skip("no Chromium available for Playwright")
    return found


def _open(p, exe, srv, sid, ocr: bool = True):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": srv.base}])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{srv.base}/scan")
    page.wait_for_function("() => window.__scan && window.__scan.flattenCard && window.ScanGeometry")
    if ocr:
        page.evaluate("() => window.__scan.ensureOcr()")
        page.wait_for_function("() => window.__scan.state.ocrState === 'ready'", timeout=120_000)
        # Count the real text recognition's reads, title and info line alike.
        page.evaluate(
            """() => { const w = window.__scan.state.ocr; const real = w.recognize.bind(w);
                 window.__reads = 0; w.recognize = (...a) => { window.__reads++; return real(...a); }; }"""
        )
    return browser, page, errors


DONE = """() => { const s = (document.querySelector('#cam-status') || {}).textContent || '';
  return /^(Could not|Found the card|Scan failed|No match|Matched)/.test(s); }"""


def photo_scan(page, path: Path) -> dict:
    """The Photo button with ``path``: what the page said and found, its reads and time."""
    page.evaluate(
        """() => { window.__scan.state.pending = null; window.__reads = 0;
             document.querySelector('#cam-status').textContent = ''; }"""
    )
    t0 = time.monotonic()
    page.set_input_files("#photo-file", str(path))
    page.wait_for_function(DONE, timeout=120_000)
    out = page.evaluate(
        """() => { const p = window.__scan.state.pending;
             return { status: document.querySelector('#cam-status').textContent, reads: window.__reads,
                      card: p && p.card ? [p.card.name, p.card.set, p.card.collector_number] : null,
                      text: p && p.ocr ? p.ocr.text : null, blurry: p && p.ocr ? p.ocr.blurry : null,
                      sharpness: p && p.ocr ? p.ocr.sharpness : null }; }"""
    )
    out["seconds"] = round(time.monotonic() - t0, 2)
    return out


TO_BLOB = """(b) => { const bin = atob(b), a = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
    return URL.createObjectURL(new Blob([a], {type: 'image/jpeg'})); }"""


def install_glue(page) -> None:
    page.evaluate(
        """() => { window.__events = [];
             window.MtgNative = { scanEvent: (j) => window.__events.push(JSON.parse(j)),
                                  openCamera: () => {}, version: () => 'test' }; }"""
    )
    assert page.evaluate(android_glue()) == "installed"
    assert page.evaluate("() => window.__mtgNative.begin()") == "ok"


def app_shot(page, path: Path, guide: str = "null") -> dict:
    """One photo handed over as the app does: the done event it ends in."""
    url = page.evaluate(TO_BLOB, base64.b64encode(path.read_bytes()).decode())
    page.evaluate("() => { window.__events = []; }")
    assert page.evaluate(f"() => window.__mtgNative.onPhoto({url!r}, {guide})").startswith("ok:")
    page.wait_for_function("() => window.__events.some((e) => e.type === 'done')", timeout=120_000)
    done = page.evaluate("() => window.__events.find((e) => e.type === 'done')")
    assert done["nonce"] == GLUE_NONCE
    return done


# ------------------------------------------------------------- F3: the title bar's edge
def test_the_title_bar_edge_is_dropped_from_a_read(server, exe) -> None:  # noqa: F811
    """F3: the title bar's own left edge, read as a letter glued in front of the name ("fSolRing",
    "l Sol Ring") is taken off on the page; a name that starts with a capital keeps it."""
    from playwright.sync_api import sync_playwright

    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid, ocr=False)
        cases = {
            "fSolRing": "SolRing",
            "l Sol Ring .": "Sol Ring",
            "f Sol Ring / -": "Sol Ring",
            "l Sol 1 Ring": "Sol 1 Ring",
            "Sol Ring": "Sol Ring",
            "A Little Chat": "A Little Chat",
            "I Know All, I See All": "I Know All, I See All",
            "Island": "Island",
        }
        got = page.evaluate("(xs) => xs.map((x) => window.__scan.cleanOcr(x))", list(cases))
        assert got == list(cases.values())
        # The flattened card's title crop starts past the frame edge (about 7% in) and still ends
        # before the mana cost.
        r = page.evaluate("() => window.__scan.flatTitleRegion(1008, 1408)")
        assert r["x"] / 1008 >= 0.065 and abs((r["x"] + r["w"]) / 1008 - 0.905) < 0.002, r
        assert errors == []
        browser.close()


# ------------------------------------------------------------- F2: any way up
@needs_ocr
@pytest.mark.parametrize(
    "rot", [0, 180, 90, 270], ids=["upright", "upside-down", "sideways-left", "sideways-right"]
)
def test_a_card_any_way_up_is_read_and_upright_costs_one_title_read(
    server,  # noqa: F811
    exe,
    tmp_path: Path,
    rot: int,
) -> None:
    """F2: a 12 MP photo of a card at 55% of the width, upright, upside down or on either side, is
    read with the real text recognition and matched to the card and its printing. An upright card
    is read once (title and info line: two reads); only the other ways up cost a second title read."""
    from playwright.sync_api import sync_playwright

    photo = table_photo(tmp_path / f"r{rot}.jpg", rot=rot)
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid)
        out = photo_scan(page, photo)
        print(f"rotated {rot}: {out}")
        assert out["card"] == ["Sol Ring", "frc", "21"], out
        assert out["reads"] == (2 if rot == 0 else 3), out
        assert errors == []
        browser.close()


@needs_ocr
def test_the_app_reads_an_upside_down_card_and_names_it(server, exe, tmp_path: Path) -> None:  # noqa: F811
    """F1 + F2 through the app's glue: an upside-down card as the phone hands it over (2048 x 1536,
    stored sideways with EXIF orientation 6) is read, and the done event names the card."""
    from PIL import Image
    from playwright.sync_api import sync_playwright

    big = table_photo(tmp_path / "big.jpg", rot=180)
    small = Image.open(big).resize((1536, 2048), Image.Resampling.LANCZOS)
    exif = Image.Exif()
    exif[0x0112] = 6
    shot = tmp_path / "shot.jpg"
    small.transpose(Image.Transpose.ROTATE_90).save(shot, "JPEG", quality=92, exif=exif)
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid)
        install_glue(page)
        done = app_shot(page, shot, "{x: 0.25, y: 0.27, w: 0.5, h: 0.47}")
        assert done["matched"] is True and done["name"] == "Sol Ring", done
        assert (done["set"], done["number"]) == ("FRC", "21"), done
        assert errors == []
        browser.close()


@needs_ocr
def test_a_strip_that_reads_nothing_is_read_again_inside_the_bar(server, exe, tmp_path: Path) -> None:  # noqa: F811
    """A 50 MP photo, the card at 35% of the width and turned 8°: its flattened title strip is clean,
    but text recognition reads nothing in it while the title bar's outline runs along its top and
    bottom edges. Read again without them, the name is found."""
    from playwright.sync_api import sync_playwright

    photo = table_photo(tmp_path / "far.jpg", size=(6120, 8160), frac=0.35, rot=8)
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid)
        out = photo_scan(page, photo)
        assert out["card"] == ["Sol Ring", "frc", "21"], out
        assert errors == []
        browser.close()


# ------------------------------------------------------------- F1: every shot says what happened
def test_each_app_shot_reports_its_own_outcome(server, exe, tmp_path: Path) -> None:  # noqa: F811
    """F1: three shots in a row through the app's glue: a match, a name that matches nothing, and a
    card whose name cannot be read. Each done event says what that photo came to, with the card's
    name, set and number for the match; a read still waiting from the shot before never stands in
    for a miss."""
    from playwright.sync_api import sync_playwright

    photo = table_photo(tmp_path / "card.jpg")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid, ocr=False)
        page.evaluate(
            """() => { window.__t = 'Sol Ring';
                 window.__scanOcrOverride = async (c, kind) => kind === 'info'
                   ? {text: '0021 U\\nFRC • EN', confidence: 90, words: []}
                   : {text: window.__t, confidence: window.__t ? 95 : 0, words: []}; }"""
        )
        install_glue(page)
        first = app_shot(page, photo)
        assert first["matched"] is True and first["pending"] is True, first
        assert (first["name"], first["set"], first["number"]) == ("Sol Ring", "FRC", "21"), first
        page.evaluate("() => { window.__t = 'Xyzzy Qwerty'; }")
        second = app_shot(page, photo)
        assert second["matched"] is False and second["pending"] is True, second
        assert second["name"] == "" and second["read"] == "Xyzzy Qwerty", second
        assert second["status"].startswith("No match"), second
        page.evaluate("() => { window.__t = ''; }")
        third = app_shot(page, photo)
        assert third["matched"] is False and third["pending"] is False, third
        assert third["status"].startswith("Found the card but could not read its name"), third
        assert errors == []
        browser.close()


# ------------------------------------------------------------- F4: blur says so
@needs_ocr
@pytest.mark.parametrize("blur", [0.0035, 0.007], ids=["soft", "heavy"])
def test_a_blurred_photo_says_it_is_blurred(server, exe, tmp_path: Path, blur: float) -> None:  # noqa: F811
    """F4: a card out of focus (the lens held closer than it can focus) reads nothing useful, and
    the status line says the picture is blurred and how to get a sharp one, on the website and in
    the app (the done event carries blurry). A sharp photo never says so."""
    from playwright.sync_api import sync_playwright

    soft = table_photo(tmp_path / "soft.jpg", blur=blur)
    sharp = table_photo(tmp_path / "sharp.jpg")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid)
        out = photo_scan(page, soft)
        print(f"blur {blur}: {out}")
        assert out["card"] is None and "The picture is blurred" in out["status"], out
        ok = photo_scan(page, sharp)
        assert ok["card"] == ["Sol Ring", "frc", "21"] and "blurred" not in ok["status"], ok
        install_glue(page)
        done = app_shot(page, soft)
        assert done["matched"] is False and done["blurry"] is True, done
        assert errors == []
        browser.close()


# ------------------------------------------------------------- F5: big photos decode small
@needs_ocr
def test_a_big_photo_is_decoded_straight_to_the_working_size(server, exe, tmp_path: Path) -> None:  # noqa: F811
    """F5: a 48 MP photo (6000 x 8000) goes to createImageBitmap with a resize to the working copy
    (long side 2400), so the browser never needs the full-size bitmap; it is read as before. A
    browser without the resize options falls back to the picture as it was drawn before. A photo
    stored sideways with EXIF orientation 6 comes out upright either way."""
    from playwright.sync_api import sync_playwright

    big = table_photo(tmp_path / "big.jpg", size=(6000, 8000))
    turned = table_photo(tmp_path / "turned.jpg", size=(6000, 8000), exif6=True)
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid)
        page.evaluate(
            """() => { window.__bitmaps = []; const real = window.createImageBitmap;
                 window.createImageBitmap = (src, ...rest) => {
                   const o = rest.length === 1 ? rest[0] : rest[4];
                   window.__bitmaps.push({blob: src instanceof Blob, opts: o || null});
                   return window.__noResize ? Promise.reject(new TypeError('no resize'))
                     : real.call(window, src, ...rest); }; }"""
        )
        for path in (big, turned):
            out = photo_scan(page, path)
            assert out["card"] == ["Sol Ring", "frc", "21"], (path.name, out)
            pic = page.evaluate("() => window.__scan.state.lastPicture")
            assert pic == {"w": 1800, "h": 2400}, (path.name, pic)
        calls = page.evaluate("() => window.__bitmaps")
        assert [c["blob"] for c in calls] == [True, True], calls
        for c in calls:
            o = c["opts"]
            assert (o["resizeWidth"], o["resizeHeight"]) == (1800, 2400), o
            assert o["resizeQuality"] == "high" and o["imageOrientation"] == "from-image", o
        # Without the resize options the picture is drawn as before (a copy of the file: the same
        # file chosen twice in a row is no new choice).
        again = tmp_path / "again.jpg"
        again.write_bytes(turned.read_bytes())
        page.evaluate("() => { window.__noResize = true; }")
        out = photo_scan(page, again)
        assert out["card"] == ["Sol Ring", "frc", "21"], out
        assert page.evaluate("() => window.__scan.state.lastPicture") == {"w": 1800, "h": 2400}
        assert errors == []
        browser.close()
