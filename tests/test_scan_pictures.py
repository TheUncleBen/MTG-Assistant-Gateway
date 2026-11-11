"""Finding a card by its picture (0.7.47): the index of every printing's artwork, the search, the
identify endpoint, the missed pictures kept for the admin, and Admin › Card scanner.

The picture model is stood in for by a small deterministic embedder (the art box shrunk to 32 x 32
grey levels), so the index, the search and the rules around them are tested without the model
file; test_the_real_model_runs is the one test of the model itself, where the image has it.
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import random
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from mtg_gateway.db import Database
from mtg_gateway.scan import picture as pic
from mtg_gateway.scan.misses import KEEP_DAYS, MAX_ROWS, PER_MEMBER_PER_DAY, MissStore

from .conftest import FakeIdP, Harness, Stack, StackOptions, make_settings, running, stack_with
from .test_admin import NAVIGATE, _with_admin, admin_browser
from .test_decks_and_proxy import Browser
from .test_scan import scan_csrf

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

stack_options = stack_with(StackOptions(settings={}, archidekt=None, scryfall=True))
HOST = "https://cards.scryfall.io/"


class TinyEmbedder:
    """The art box as 32 x 32 grey levels around their mean, unit length: pictures of the same art
    land close together, different random art far apart."""

    def embed_card(self, image: Any) -> Any:
        art = pic.art_crop(image).convert("L").resize((32, 32), Image.Resampling.BILINEAR)
        v = np.asarray(art, dtype=np.float32).reshape(-1)
        v = v - v.mean()
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v


def art(seed: int) -> Any:
    """A card picture (146 x 204, Scryfall's ``small``) with random blocky art of its own."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (146, 204), (20, 20, 20))
    box = Image.new("RGB", (8, 6))
    box.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(48)])
    im.paste(box.resize((124, 92), Image.Resampling.NEAREST), (11, 24))
    return im


def jpeg(im: Any, quality: int = 85) -> bytes:
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality)
    return out.getvalue()


def photo(seed: int, *, turn: bool = False) -> bytes:
    """The straightened card as the page sends it: larger, a little brighter, JPEG again."""
    im = art(seed).resize((488, 680), Image.Resampling.BILINEAR).point(lambda v: min(255, int(v * 1.08) + 6))
    if turn:
        im = im.transpose(Image.Transpose.ROTATE_180)
    return jpeg(im, 80)


# (scryfall id, oracle id, illustration id, set, number, art seed); two printings share art 1
PRINTINGS = [
    ("sol-c21", "o-sol", "ill-sol", "c21", "263", 1),
    ("sol-frc", "o-sol", "ill-sol", "frc", "21", 1),
    ("mind-c21", "o-mind", "ill-mind", "c21", "246", 2),
    ("opt-xln", "o-opt", "ill-opt", "xln", "65", 3),
    ("bolt-m10", "o-bolt", "ill-bolt", "m10", "146", 4),
]


class FakeCopy:
    """The printings copy as the index and the service use it."""

    enabled = True

    def close(self) -> None:
        pass

    def __init__(self, rows: list[tuple] = PRINTINGS):
        self.rows = rows

    def meta(self) -> dict[str, Any]:
        return {"count": len(self.rows)}

    def picture_rows(self, page: int = 2000):
        for sid, oracle, ill, set_code, cn, _seed in self.rows:
            yield (sid, oracle, ill, set_code, cn, f"{HOST}small/{sid}.jpg")

    def printing_now(self, set_code: str, number: str) -> dict[str, Any] | None:
        for sid, oracle, _ill, s, cn, _seed in self.rows:
            if s == set_code and cn == number:
                name = {
                    "o-sol": "Sol Ring",
                    "o-mind": "Mind Stone",
                    "o-opt": "Opt",
                    "o-bolt": "Lightning Bolt",
                }[oracle]
                return {
                    "name": name,
                    "scryfall_id": sid,
                    "oracle_id": oracle,
                    "set": s,
                    "collector_number": cn,
                    "released_at": "2025-01-01" if s == "frc" else "2021-01-01",
                    "finishes": ["nonfoil"],
                }
        return None


def image_host(
    rows: list[tuple] = PRINTINGS, *, status: int = 200, seen: list[str] | None = None
) -> httpx.AsyncClient:
    by_url = {f"{HOST}small/{r[0]}.jpg": r[5] for r in rows}

    def handle(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req.headers.get("user-agent", ""))
        seed = by_url.get(str(req.url))
        if status != 200:
            return httpx.Response(status)
        if seed is None:
            return httpx.Response(404)
        return httpx.Response(200, content=jpeg(art(seed)), headers={"content-type": "image/jpeg"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def index(tmp_path: Path, copy: FakeCopy | None = None, **kw: Any) -> pic.PictureIndex:
    kw.setdefault("image_http", image_host())
    return pic.PictureIndex(
        tmp_path, copy or FakeCopy(), embedder=TinyEmbedder(), image_interval=0.0, user_agent="ua-test", **kw
    )


# -- the index and the search -------------------------------------------------------------------
async def test_the_index_learns_every_printing_and_is_used_only_after_a_whole_round(tmp_path: Path) -> None:
    seen: list[str] = []
    idx = index(tmp_path, image_http=image_host(seen=seen))
    assert not idx.ready()  # nothing learned yet: the scanner keeps reading titles
    assert await idx.sync() == len(PRINTINGS)
    assert idx.ready() and idx.count() == len(PRINTINGS)
    assert idx.status["state"] == "ready" and idx.status["want"] == len(PRINTINGS)
    assert set(seen) == {"ua-test"}  # the gateway's own User-Agent on every fetch
    # nothing new: nothing fetched
    assert await idx.sync() == 0 and len(seen) == len(PRINTINGS)
    # the card is found from a photo of it, and the printings that share its art come with it
    found = idx.identify(pic.decode_picture(photo(2)))
    assert found["best"]["id"] == "mind-c21" and found["sure"] is True, found
    found = idx.identify(pic.decode_picture(photo(1)))
    assert found["best"]["oracle_id"] == "o-sol"
    assert {a["id"] for a in found["same_art"]} == {"sol-c21", "sol-frc"}
    assert found["others"] and found["others"][0]["oracle_id"] != "o-sol"
    idx.close()
    # kept on disk: a restart is ready at once, without fetching again
    again = index(tmp_path)
    await asyncio.to_thread(again.load)
    assert again.ready() and again.count() == len(PRINTINGS)
    again.close()


async def test_a_half_built_index_is_not_used(tmp_path: Path) -> None:
    idx = index(tmp_path)
    # one round that could fetch only some pictures (the host fails for the rest)
    good = PRINTINGS[:2]
    idx._http = image_host(good)
    await idx.sync()
    assert idx.count() == 2 and not idx.ready()
    # a day later the pictures that failed are tried again
    with idx._db_lock:
        idx._db().execute("UPDATE skipped SET at = 0")
    idx._http = image_host()
    await idx.sync()
    assert idx.count() == len(PRINTINGS) and idx.ready()
    idx.close()


async def test_a_slow_down_from_the_image_host_pauses_every_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429 from cards.scryfall.io pauses every fetch (PAUSE_ON_429, five minutes) and the printing
    is tried again after it, never skipped for a day."""
    monkeypatch.setattr(pic, "PAUSE_ON_429", 0.3)
    answers = [429]

    def handle(req: httpx.Request) -> httpx.Response:
        if answers:
            return httpx.Response(answers.pop())
        return httpx.Response(200, content=jpeg(art(1)))

    one = FakeCopy(PRINTINGS[:1])
    idx = index(tmp_path, one, image_http=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    start = time.monotonic()
    assert await idx.sync() == 1
    assert time.monotonic() - start >= 0.3 and idx.ready()
    idx.close()


async def test_only_the_image_host_is_ever_fetched(tmp_path: Path) -> None:
    idx = index(tmp_path)
    assert await idx._fetch_and_embed("https://elsewhere.test/x.jpg") is None
    idx.close()


async def test_a_picture_too_big_is_given_up_on_while_it_downloads(tmp_path: Path) -> None:
    """The size cap holds while the picture streams in: the gateway stops reading as soon as it
    passes MAX_IMAGE_BYTES instead of loading the whole body first."""
    sent = []

    async def endless():
        for _ in range(200):  # 200 x 64 KB, far past the cap
            sent.append(1)
            yield b"\0" * 65536

    def handle(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=endless(), headers={"content-type": "image/jpeg"})

    idx = index(tmp_path, image_http=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    assert await idx._fetch_and_embed(f"{HOST}small/sol-c21.jpg") is None
    assert len(sent) <= pic.MAX_IMAGE_BYTES // 65536 + 2, len(sent)
    # an ordinary picture still arrives whole, and an empty one is not taken for a slow-down
    idx._http = image_host()
    assert await idx._fetch_and_embed(f"{HOST}small/sol-c21.jpg")
    idx._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"")))
    assert await idx._fetch_and_embed(f"{HOST}small/sol-c21.jpg") is None
    idx.close()


async def test_printings_the_copy_no_longer_has_are_dropped(tmp_path: Path) -> None:
    idx = index(tmp_path)
    await idx.sync()
    idx.copy = FakeCopy(PRINTINGS[:-1])
    await idx.sync()
    assert idx.count() == len(PRINTINGS) - 1
    found = idx.identify(pic.decode_picture(photo(4)))
    assert found["best"] is None or found["best"]["id"] != "bolt-m10"
    # a copy that suddenly has far fewer printings (a broken download) never empties the index
    idx.copy = FakeCopy(PRINTINGS[:1])
    await idx.sync()
    assert idx.count() == len(PRINTINGS) - 1
    idx.close()


async def test_a_blank_picture_matches_nothing(tmp_path: Path) -> None:
    idx = index(tmp_path)
    await idx.sync()
    blank = pic.decode_picture(jpeg(Image.new("RGB", (488, 680), (128, 128, 128))))
    assert idx.identify(blank)["best"] is None
    idx.close()


def test_decode_picture_refuses_what_is_not_a_card_picture() -> None:
    assert pic.decode_picture(b"not a picture") is None
    assert pic.decode_picture(jpeg(Image.new("RGB", (20, 20)))) is None  # too small
    assert pic.decode_picture(jpeg(Image.new("RGB", (2400, 100)))) is None  # too large
    assert pic.decode_picture(jpeg(Image.new("RGB", (488, 680)))).size == (488, 680)


def test_the_real_model_runs() -> None:
    """The MediaPipe model the image ships: 1,024 numbers of unit length, the same picture twice
    the same, a different picture different. Skipped where the asset fetch step hasn't run."""
    if not pic.runtime_available():
        pytest.skip("no picture model (run scripts/fetch_ocr_assets.py)")
    e = pic.Embedder()
    a, b = e.embed_card(art(1)), e.embed_card(art(2))
    assert a.shape == (pic.DIM,) and abs(float(np.linalg.norm(a)) - 1) < 1e-3
    assert float(a @ e.embed_card(art(1))) > 0.999 and float(a @ b) < 0.99


# -- the identify endpoint ------------------------------------------------------------------------
async def ready_pictures(st: Stack, tmp_path: Path) -> pic.PictureIndex:
    idx = index(tmp_path / "pictures")
    await idx.sync()
    st.gw.scan.pictures = idx
    st.gw.scan.copy = FakeCopy()
    return idx


async def post_picture(b: Browser, path: str, data: bytes, *, kind: str = "image/jpeg", **headers: str):
    return await b.http.post(
        path, content=data, headers={"Content-Type": kind, "X-CSRF-Token": await scan_csrf(b), **headers}
    )


async def test_identify_finds_the_card_by_its_picture(stack: Stack, tmp_path: Path) -> None:
    b = Browser(stack.h)
    await b.login("/scan")
    st = stack.gw.scan
    # the index isn't there: the page reads titles as before, and the endpoint says so
    st.pictures = None
    cfg = await page_config(b)
    assert cfg["pictures"] is False and cfg["admin"] is False
    assert (await post_picture(b, "/scan/api/identify", photo(2))).status_code == 503
    idx = await ready_pictures(stack, tmp_path)
    assert (await page_config(b))["pictures"] is True
    r = await post_picture(b, "/scan/api/identify", photo(2))
    out = r.json()
    assert r.status_code == 200 and out["card"]["name"] == "Mind Stone" and out["sure"] is True, out
    assert out["same_art"] == [out["card"]] and out["others"][0]["similarity"] < out["similarity"]
    # printings that share the art come newest first
    out = (await post_picture(b, "/scan/api/identify", photo(1))).json()
    assert [c["scryfall_id"] for c in out["same_art"]] == ["sol-frc", "sol-c21"]
    # a card held upside down is found too
    out = (await post_picture(b, "/scan/api/identify", photo(3, turn=True))).json()
    assert out["card"]["name"] == "Opt", out
    # what isn't a card picture
    assert (await post_picture(b, "/scan/api/identify", b"x" * 100)).status_code == 400
    assert (await post_picture(b, "/scan/api/identify", photo(2), kind="text/plain")).status_code == 415
    assert (await post_picture(b, "/scan/api/identify", b"x" * 500_000)).status_code == 413
    # the form token is needed
    r = await b.http.post("/scan/api/identify", content=photo(2), headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 403
    idx.close()
    await b.aclose()


async def test_one_picture_at_a_time_per_member(stack: Stack, tmp_path: Path) -> None:
    idx = await ready_pictures(stack, tmp_path)
    service = stack.gw.scan
    service._identifying.add("user-x")
    from mtg_gateway.scan.service import ScanError

    with pytest.raises(ScanError) as err:
        await service.identify(photo(2), owner="user-x")
    assert err.value.kind == "busy"
    service._identifying.clear()
    assert (await service.identify(photo(2), owner="user-x"))["card"]["name"] == "Mind Stone"
    idx.close()


async def page_config(b: Browser) -> dict[str, Any]:
    r = await b.http.get("/scan")
    start = r.text.index("<script id='scan-config'")
    return json.loads(r.text[r.text.index(">", start) + 1 : r.text.index("</script>", start)])


# -- missed pictures ------------------------------------------------------------------------------
def test_missed_pictures_are_kept_30_days_capped_and_limited_per_member() -> None:
    store = MissStore(Database(":memory:"))
    now = time.time()
    first = store.add("u1", b"jpeg-1", "no match", now=now)
    assert first and store.picture(first) == b"jpeg-1"
    row = store.list()[0]
    assert row["owner_sub"] == "u1" and row["size"] == 6 and row["note"] == "no match"
    store.add("u1", b"x", "n" * 1000, now=now)
    assert max(len(r["note"]) for r in store.list()) == 300
    # 30 days on, they are gone
    assert store.purge(now + KEEP_DAYS * 86400 + 1) == 2 and store.count() == 0
    assert store.delete(first) is False
    # one member can keep only so many a day; others still can, and so can they the next day
    for i in range(PER_MEMBER_PER_DAY):
        assert store.add("u3", b"p", now=now + i) is not None
    assert store.add("u3", b"p", now=now + 100) is None
    assert store.add("u2", b"p", now=now + 100) is not None
    assert store.add("u3", b"p", now=now + 86400 + 200) is not None


def test_the_table_never_holds_more_than_its_cap() -> None:
    store = MissStore(Database(":memory:"))
    for i in range(MAX_ROWS + 5):
        store.add(f"m{i}", b"p", now=time.time() - 1000 + i)
    assert store.count() == MAX_ROWS
    assert "m0" not in {r["owner_sub"] for r in store.list()}  # the oldest went first


def test_a_members_missed_pictures_go_with_their_data() -> None:
    db = Database(":memory:")
    db.upsert_user("u1", email=None, name=None, preferred_username="u", groups=[])
    store = MissStore(db)
    store.add("u1", b"p")
    store.add("u2", b"p")
    db.delete_member_data("u1")
    assert [r["owner_sub"] for r in store.list()] == ["u2"]


async def test_a_member_keeps_a_missed_picture_for_the_admin(stack: Stack, tmp_path: Path) -> None:
    b = Browser(stack.h)
    await b.login("/scan")
    r = await post_picture(b, "/scan/api/miss", photo(9), **{"X-Scan-Note": "no match"})
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = stack.gw.scan.misses.list()
    assert len(rows) == 1 and rows[0]["note"] == "no match"
    assert (await post_picture(b, "/scan/api/miss", b"not a picture")).status_code == 400
    for _ in range(PER_MEMBER_PER_DAY - 1):
        await post_picture(b, "/scan/api/miss", photo(9))
    r = await post_picture(b, "/scan/api/miss", photo(9))
    assert r.status_code == 429 and "enough pictures for today" in r.json()["message"]
    await b.aclose()


async def test_a_kept_picture_is_stored_as_a_plain_jpeg(stack: Stack) -> None:
    """Whatever the upload carried (EXIF, GPS, another format) only the picture is kept, as the
    JPEG it is served as."""
    from PIL import Image

    b = Browser(stack.h)
    await b.login("/scan")
    exif = Image.Exif()
    exif[0x010F] = "Phone maker"  # Make
    exif[0x8825] = {2: (1.0, 2.0, 3.0)}  # GPS
    tagged = io.BytesIO()
    Image.open(io.BytesIO(photo(9))).save(tagged, "JPEG", exif=exif)
    png = io.BytesIO()
    Image.open(io.BytesIO(photo(9))).save(png, "PNG")
    for data, kind in ((tagged.getvalue(), "image/jpeg"), (png.getvalue(), "image/png")):
        r = await post_picture(b, "/scan/api/miss", data, kind=kind)
        assert r.status_code == 200, r.text
        kept = stack.gw.scan.misses.picture(r.json()["id"])
        assert kept[:3] == b"\xff\xd8\xff" and b"Phone maker" not in kept
        im = Image.open(io.BytesIO(kept))
        assert im.format == "JPEG" and not im.getexif()
    await b.aclose()


async def test_a_kept_picture_is_never_larger_than_an_upload(stack: Stack) -> None:
    """A small PNG of a fine pattern turns into a large JPEG (the audit saw 20 KB become 1.95 MB):
    the kept picture is shrunk until it fits the upload limit."""
    from PIL import Image

    b = Browser(stack.h)
    await b.login("/scan")
    board = Image.new("1", (2000, 2000))
    board.putdata([(x + y) % 2 for y in range(2000) for x in range(2000)])
    png = io.BytesIO()
    board.save(png, "PNG", optimize=True)
    assert len(png.getvalue()) < pic.MAX_UPLOAD
    r = await post_picture(b, "/scan/api/miss", png.getvalue(), kind="image/png")
    assert r.status_code == 200, r.text
    kept = stack.gw.scan.misses.picture(r.json()["id"])
    assert len(kept) <= pic.MAX_UPLOAD, len(kept)
    assert max(Image.open(io.BytesIO(kept)).size) <= pic.KEEP_SIDE
    await b.aclose()


async def test_the_hourly_purge_deletes_missed_pictures_past_30_days(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missed pictures go after 30 days even when no one keeps another one or opens Admin › Card
    scanner: the gateway's hourly purge round deletes them."""
    import mtg_gateway.app as app_mod

    rounds: list = []

    async def one_round(db: Any, interval: float = 3600, also: Any = None) -> None:
        rounds.append(also)
        await asyncio.Event().wait()

    monkeypatch.setattr(app_mod, "purge_loop", one_round)
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        misses = h.app.state.gateway.scan.misses
        new = misses.add("user-9", b"new")
        # one kept 31 days ago (written straight in: adding one would purge on its own)
        with misses.db.tx() as c:
            c.execute(
                "INSERT INTO scan_misses (id, owner_sub, created_at, picture, note) VALUES (?,?,?,?,?)",
                ("old", "user-9", time.time() - (KEEP_DAYS + 1) * 86400, b"old", ""),
            )
        old = "old"
        assert misses.picture(old) == b"old"
        for _ in range(100):
            if rounds:
                break
            await asyncio.sleep(0.01)
        assert rounds and rounds[0] is not None
        await asyncio.to_thread(rounds[0])
        assert misses.picture(old) is None and misses.picture(new) == b"new"


# -- Admin › Card scanner ------------------------------------------------------------------------
@pytest.fixture
async def gw(tmp_path: Path, idp: FakeIdP):
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
    async with running(_with_admin(Harness(settings, idp))) as h:
        h.db.upsert_user("user-9", email=None, name=None, preferred_username="bram", groups=["mtg-users"])
        yield h


async def test_admins_see_the_index_and_the_missed_pictures_and_can_delete_them(
    gw: Harness, tmp_path: Path
) -> None:
    scan = gw.app.state.gateway.scan
    b = await admin_browser(gw)
    try:
        r = await b.http.get("/admin/scanner", headers=NAVIGATE)
        text = html.unescape(r.text)
        assert r.status_code == 200 and "href='/admin/scanner' aria-current='page'>Card scanner</a>" in r.text
        assert "No missed pictures kept." in text and "Picture index" in text
        # the scan page tells an admin where the pictures go
        assert (await page_config(b))["admin"] is True
        mid = scan.misses.add("user-9", photo(9), "no match")
        r = await b.http.get("/admin/scanner", headers=NAVIGATE)
        text = html.unescape(r.text)
        assert f"src='/admin/scanner/miss/{mid}'" in text and "bram" in text
        # the picture is read off the event loop, as the scan routes read theirs
        import threading

        threads: list[str] = []
        read = scan.misses.picture
        scan.misses.picture = lambda m: threads.append(threading.current_thread().name) or read(m)
        pict = await b.http.get(f"/admin/scanner/miss/{mid}")
        del scan.misses.picture
        assert threads and threads[0] != threading.main_thread().name, threads
        assert pict.status_code == 200 and pict.headers["content-type"] == "image/jpeg"
        assert pict.headers["cache-control"] == "no-store" and pict.content == photo(9)
        csrf = await b.csrf("/admin/scanner")
        r = await b.http.post("/admin/scanner", data={"csrf": csrf, "id": mid})
        assert r.status_code == 303 and r.headers["location"] == "/admin/scanner?ok=miss_removed"
        assert scan.misses.count() == 0
        r = await b.http.post("/admin/scanner", data={"csrf": csrf, "id": mid})
        assert r.headers["location"] == "/admin/scanner?err=miss_gone"
        assert (await b.http.post("/admin/scanner", data={"id": mid})).status_code == 403
        assert (await b.http.get(f"/admin/scanner/miss/{mid}")).status_code == 404
    finally:
        await b.aclose()


async def test_members_cannot_see_the_missed_pictures(gw: Harness) -> None:
    from .test_admin import ADMIN

    scan = gw.app.state.gateway.scan
    mid = scan.misses.add("user-9", photo(9))
    gw.idp.user = {**ADMIN, "sub": "user-9", "groups": ["mtg-users"]}
    b = Browser(gw)
    await b.login("/scan")
    try:
        assert (await b.http.get("/admin/scanner", headers=NAVIGATE)).status_code == 404
        assert (await b.http.get(f"/admin/scanner/miss/{mid}")).status_code == 404
        assert (await page_config(b))["admin"] is False
    finally:
        await b.aclose()
