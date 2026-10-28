"""Finding the card in a phone picture, in headless Chromium through the page's real code.

A phone photo seldom has the card filling the frame: a 12 MP picture (4000 x 3000) of one card on
a table usually shows the card at a third to a half of the picture's width, off centre, slightly
turned. These tests draw such pictures, put them through the Photo button and the scan hooks the
Android app uses, and check that the card's outline is found where it really is and that the strip
handed to text recognition is the card's title, not the table. Text
recognition is stood in for, so no OCR assets are needed. Skipped without a Chromium build."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from .test_scan_browser import _chromium_path, _perspective_coeffs, card_photo, server  # noqa: F401

pytest.importorskip("playwright")

WOOD = (176, 146, 108)


def card_corners(cx: float, cy: float, cw: float, turn: float = 0.0) -> list[tuple[float, float]]:
    """A 63:88 card centred at (cx, cy), cw wide, its corners shifted by ``turn`` of its width."""
    ch = cw * 88 / 63
    x0, y0, d = cx - cw / 2, cy - ch / 2, turn * cw
    return [(x0 + d, y0), (x0 + cw + d, y0 + d), (x0 + cw - d, y0 + ch), (x0 - d, y0 + ch - d)]


def scene(card: Path, out: Path, size: tuple[int, int], corners: list[tuple[float, float]]) -> None:
    """The card (without its dark surround) laid on a plain table at ``corners``, saved as a JPEG."""
    from PIL import Image

    w, h = size
    src = Image.open(card)
    src = src.crop((20, 20, src.width - 20, src.height - 20))
    coeffs = _perspective_coeffs(
        list(corners),
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
    table = Image.new("RGBA", (w, h), WOOD + (255,))
    table.alpha_composite(warped)
    table.convert("RGB").save(out, "JPEG", quality=88)


# Loads a picture as the page does (a copy with the long side at most 2400 px) and runs the
# page's flattenCard on it, with the box (fractions of the picture) or on the whole picture.
FLATTEN = """async ([b64, guide]) => {
  const bin = atob(b64), a = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
  const url = URL.createObjectURL(new Blob([a], {type: 'image/jpeg'}));
  const img = await new Promise((res, rej) => {
    const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
  const k = Math.min(1, 2400 / Math.max(img.naturalWidth, img.naturalHeight));
  const c = document.createElement('canvas');
  c.width = Math.round(img.naturalWidth * k); c.height = Math.round(img.naturalHeight * k);
  c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
  const g = guide && {x: guide[0] * c.width, y: guide[1] * c.height,
                      w: guide[2] * c.width, h: guide[3] * c.height};
  const flat = window.__scan.flattenCard(c, g || null);
  return {k, corners: flat ? flat.corners : null};
}"""

# The share of dark pixels in the title strip text recognition was given (black text on white).
INK = """(url) => new Promise((res) => { const i = new Image(); i.onload = () => {
  const c = document.createElement('canvas'); c.width = i.width; c.height = i.height;
  const x = c.getContext('2d'); x.drawImage(i, 0, 0);
  const d = x.getImageData(0, 0, c.width, c.height).data; let dark = 0;
  for (let p = 0; p < d.length; p += 4) if (d[p] < 100) dark++;
  res(dark / (d.length / 4)); }; i.src = url; })"""


def _open(p, exe, srv, sid):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": srv.base}])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{srv.base}/scan")
    page.wait_for_function("() => window.__scan && window.__scan.flattenCard && window.ScanGeometry")
    # Stand-in text recognition: keeps every strip it is given and reads the title as "Sol Ring".
    page.evaluate(
        """() => { window.__strips = [];
             window.__scanOcrOverride = async (c, kind) => {
               window.__strips.push([kind, c.toDataURL('image/png')]);
               return kind === 'info' ? {text: '', confidence: 0, words: []}
                 : {text: 'Sol Ring', confidence: 95, words: []}; }; }"""
    )
    return browser, page, errors


def _exe() -> str | None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


# (picture size, card centre as fractions, card width as a fraction of the picture's width, turn)
PHOTOS = {
    "portrait-12mp-third": ((3000, 4000), (0.5, 0.5), 0.33, 0.02),
    "portrait-12mp-off-centre": ((3000, 4000), (0.42, 0.56), 0.45, -0.03),
    "landscape-12mp-third": ((4000, 3000), (0.55, 0.48), 0.30, 0.02),
}


@pytest.mark.parametrize("name", list(PHOTOS))
def test_a_photo_with_the_card_in_part_of_the_picture_is_read_from_the_card(
    server,  # noqa: F811
    tmp_path: Path,
    name: str,
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    (w, h), (fx, fy), fw, turn = PHOTOS[name]
    card = tmp_path / "card.jpg"
    card_photo(card, "Sol Ring")
    photo = tmp_path / f"{name}.jpg"
    corners = card_corners(w * fx, h * fy, w * fw, turn)
    scene(card, photo, (w, h), corners)
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid)
        # The outline is found where the card is: each corner within 2% of the card's width.
        got = page.evaluate(FLATTEN, [base64.b64encode(photo.read_bytes()).decode(), None])
        assert got["corners"], "no card outline found in " + name
        tol = 0.02 * w * fw * got["k"]
        for (ex, ey), c in zip(corners, got["corners"], strict=True):
            assert abs(ex * got["k"] - c["x"]) < tol and abs(ey * got["k"] - c["y"]) < tol, (corners, got)
        # Through the Photo button: the card is flattened, and the title strip read is the card's
        # title bar (dark letters on a light bar), not a blank stretch of table.
        page.set_input_files("#photo-file", str(photo))
        page.wait_for_function("() => window.__scan.state.pending", timeout=60_000)
        ocr = page.evaluate("() => window.__scan.state.pending.ocr")
        assert ocr["flattened"] is True, ocr
        strips = page.evaluate("() => window.__strips")
        title = next(url for kind, url in strips if kind == "title")
        ink = page.evaluate(INK, title)
        assert 0.03 < ink < 0.5, ink
        assert errors == []
        browser.close()


def test_no_outline_where_there_is_no_card(server, tmp_path: Path) -> None:  # noqa: F811
    """A bare table, plain or grainy, gives no outline: the search does not make a card up."""
    from PIL import Image, ImageFilter
    from playwright.sync_api import sync_playwright

    exe = _exe()
    plain = tmp_path / "plain.jpg"
    Image.new("RGB", (3000, 4000), WOOD).save(plain, "JPEG", quality=85)
    grain = tmp_path / "grain.jpg"
    noise = Image.effect_noise((750, 1000), 60).filter(ImageFilter.GaussianBlur(1.2)).resize((3000, 4000))
    Image.merge("RGB", (noise, noise, noise)).save(grain, "JPEG", quality=85)
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid)
        for pic in (plain, grain):
            got = page.evaluate(FLATTEN, [base64.b64encode(pic.read_bytes()).decode(), None])
            assert got["corners"] is None, (pic.name, got)
        assert errors == []
        browser.close()


def test_a_shot_with_the_card_smaller_than_the_dashed_box_still_finds_it(server, tmp_path: Path) -> None:  # noqa: F811
    """The app's camera hands over where its dashed box lay (here as on a tall phone: about half the
    photo's width). A card held further back, at about half the box's size, is still found; before,
    only the box's own surroundings were searched and its empty title area was read."""
    from playwright.sync_api import sync_playwright

    exe = _exe()
    w, h = 1536, 2048
    guide = [0.245, 0.232, 0.51, 0.536]
    card = tmp_path / "card.jpg"
    card_photo(card, "Sol Ring")
    photo = tmp_path / "held-back.jpg"
    gx, gy, gw, gh = guide[0] * w, guide[1] * h, guide[2] * w, guide[3] * h
    corners = card_corners(gx + gw / 2, gy + gh / 2, gw * 0.5, 0.02)
    scene(card, photo, (w, h), corners)
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _open(p, exe, server, sid)
        got = page.evaluate(FLATTEN, [base64.b64encode(photo.read_bytes()).decode(), guide])
        assert got["corners"], "card inside the box not found"
        tol = 0.02 * gw * 0.5
        for (ex, ey), c in zip(corners, got["corners"], strict=True):
            assert abs(ex - c["x"]) < tol and abs(ey - c["y"]) < tol, (corners, got)
        assert errors == []
        browser.close()
