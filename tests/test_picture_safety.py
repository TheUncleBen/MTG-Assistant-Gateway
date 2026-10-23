"""Pictures a member uploads (security review 0.7.44, S-01 and S-05): a small file that decodes to
a huge picture is refused or decoded small, never at full size on the event loop, and nothing but
the picture leaves the gateway."""

from __future__ import annotations

import asyncio
import io
import threading

import pytest
from PIL import Image

from mtg_gateway import account_lists, profile


def _png(w: int, h: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(out, format="PNG", optimize=True)
    return out.getvalue()


def _jpeg(w: int, h: int, **kw: object) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(out, format="JPEG", quality=70, **kw)
    return out.getvalue()


@pytest.mark.parametrize("clean", [profile.clean_avatar, lambda raw: account_lists.redraw_picture(raw)])
def test_a_png_that_decodes_too_big_is_refused(clean) -> None:
    raw = _png(5000, 4999)  # about 25 MP from a small file
    assert len(raw) < 1_000_000
    with pytest.raises(Exception, match="too many pixels"):
        clean(raw)


def test_a_big_jpeg_card_picture_is_decoded_small() -> None:
    """A phone photo of many pixels still works: the decoder reads it at a fraction of its size."""
    seen: list[tuple[int, int]] = []
    real = Image.Image.convert

    def spy(self, *a, **kw):
        seen.append(self.size)
        return real(self, *a, **kw)

    raw = _jpeg(6000, 8000)  # 48 MP
    Image.Image.convert = spy  # type: ignore[method-assign]
    try:
        png = account_lists.redraw_picture(raw)
    finally:
        Image.Image.convert = real  # type: ignore[method-assign]
    with Image.open(io.BytesIO(png)) as out:
        assert out.size == account_lists.PICTURE_SIZE
    assert seen and max(w * h for w, h in seen) <= 6_000_000, seen


def test_the_avatar_keeps_no_comment_or_metadata() -> None:
    raw = _jpeg(64, 64, comment=b"home: 12 Elm St, phone 555-0100")
    data, name = profile.clean_avatar(raw)
    assert name == "avatar.jpg" and b"Elm St" not in data
    assert b"\xff\xfe" not in data  # no COM segment
    with Image.open(io.BytesIO(data)) as img:
        assert not {"comment", "exif", "icc_profile", "xmp"} & set(img.info)


def test_a_big_avatar_is_saved_at_most_1024_pixels_across() -> None:
    data, _name = profile.clean_avatar(_jpeg(3000, 2000))
    with Image.open(io.BytesIO(data)) as img:
        assert max(img.size) == 1024


async def test_pictures_are_decoded_off_the_event_loop_one_at_a_time() -> None:
    threads: set[str] = set()
    busy = {"now": 0, "most": 0}
    lock = threading.Lock()
    real = account_lists._redraw

    def watch(*a, **kw):
        with lock:
            busy["now"] += 1
            busy["most"] = max(busy["most"], busy["now"])
        threads.add(threading.current_thread().name)
        try:
            return real(*a, **kw)
        finally:
            with lock:
                busy["now"] -= 1

    account_lists._redraw = watch  # type: ignore[assignment]
    try:
        raw = _jpeg(800, 1120)
        await asyncio.gather(*(account_lists.redraw_picture_async(raw) for _ in range(4)))
    finally:
        account_lists._redraw = real  # type: ignore[assignment]
    assert threading.main_thread().name not in threads
    assert busy["most"] == 1
