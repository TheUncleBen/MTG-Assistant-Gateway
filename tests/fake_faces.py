"""Stand-in card pictures for browser tests and screenshots: a real PNG for each face of a card,
visibly different (a blue FRONT, a red BACK, different sizes), served for any cards.scryfall.io
address so a test or a shot can prove which face the page draws. Drawn with Pillow on first use;
the pages never see these outside the tests."""

from __future__ import annotations

import io
import re
from functools import cache

FACES = {"front": ((250, 350), (37, 99, 235), "FRONT"), "back": ((260, 364), (190, 24, 93), "BACK")}
_FACE = re.compile(r"/(front|back)/")


@cache
def face_png(face: str) -> bytes:
    """The PNG of one face: its colour filled edge to edge with the face's word in white."""
    from PIL import Image, ImageDraw, ImageFont

    size, colour, word = FACES[face]
    img = Image.new("RGB", size, colour)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=48)
    left, top, right, bottom = draw.textbbox((0, 0), word, font=font)
    draw.text(
        ((size[0] - right + left) / 2 - left, (size[1] - bottom + top) / 2 - top),
        word,
        fill="white",
        font=font,
    )
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def face_of(url: str) -> str:
    """Which face a Scryfall picture address asks for ("front" unless it says /back/)."""
    m = _FACE.search(url)
    return m.group(1) if m else "front"


def serve_faces(route) -> None:
    """A Playwright route handler: answers a cards.scryfall.io request with that face's PNG."""
    route.fulfill(status=200, content_type="image/png", body=face_png(face_of(route.request.url)))
