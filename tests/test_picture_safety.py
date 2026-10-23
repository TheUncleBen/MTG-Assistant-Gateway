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


def test_the_pixel_cap_leaves_the_gateway_room() -> None:
    """Confirmation pass 0.7.44 (S-01): a 12 MP picture still took about 150 MB to redraw; 8 MP is
    the most decoded, and a picture just over it is refused."""
    assert account_lists.PICTURE_MAX_PIXELS == 8_000_000
    with pytest.raises(Exception, match="too many pixels"):
        account_lists.redraw_picture(_png(3000, 2700))
    with pytest.raises(Exception, match="too many pixels"):
        profile.clean_avatar(_png(3000, 2700))


def test_an_avatar_is_made_small_before_it_is_converted() -> None:
    """Confirmation pass 0.7.44 (S-01): the avatar was converted at full size, then shrunk."""
    seen: list[tuple[int, int]] = []
    real = Image.Image.convert

    def spy(self, *a, **kw):
        seen.append(self.size)
        return real(self, *a, **kw)

    Image.Image.convert = spy  # type: ignore[method-assign]
    try:
        data, _name = profile.clean_avatar(_png(2800, 2800))
    finally:
        Image.Image.convert = real  # type: ignore[method-assign]
    with Image.open(io.BytesIO(data)) as out:
        assert out.size == (profile.AVATAR_SIDE, profile.AVATAR_SIDE)
    assert seen and max(w * h for w, h in seen) <= profile.AVATAR_SIDE**2, seen


def test_a_jpeg_cropped_at_its_bottom_edge_has_no_black_band() -> None:
    """Confirmation pass 0.7.44 (N-1): the decoder's reduction was mapped back with the width's
    factor only, so a crop at the bottom of a picture whose height did not divide evenly ran past
    the picture and came back with black rows."""
    out = io.BytesIO()
    Image.new("RGB", (1001, 8000), (255, 255, 255)).save(out, format="JPEG", quality=95)
    png = account_lists.redraw_picture(out.getvalue(), (0, 6599, 1001, 1401))
    with Image.open(io.BytesIO(png)) as pic:
        rgb = pic.convert("RGB")
        assert min(min(rgb.getpixel((x, rgb.size[1] - 1))) for x in range(0, rgb.size[0], 25)) > 200


async def test_a_waiting_picture_holds_no_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirmation pass 0.7.44 (N-3): uploads waiting for their turn wait on the event loop, not
    each in a worker thread of their own that other work needs."""
    started: list[str] = []
    real = asyncio.to_thread

    async def counting(fn, *a, **kw):
        started.append(getattr(fn, "__name__", "?"))
        return await real(fn, *a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", counting)
    raw = _png(700, 980)
    async with account_lists.decode_turn():
        waiting = [asyncio.create_task(account_lists.redraw_picture_async(raw)) for _ in range(3)]
        waiting.append(asyncio.create_task(profile.clean_avatar_async(raw)))
        await asyncio.sleep(0.05)
        assert started == []
    await asyncio.gather(*waiting)
    assert len(started) == 4
