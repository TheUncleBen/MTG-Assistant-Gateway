"""Artwork matching for scans: the hash, the per-printing gallery, and the resolver hook."""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image, ImageDraw

from mtg_gateway.config import ConfigError, load_settings
from mtg_gateway.db import Database
from mtg_gateway.scan import art
from mtg_gateway.scan.art import MAX_PENDING_BUILDS
from mtg_gateway.scan.scryfall import ScryfallClient
from mtg_gateway.scan.service import CardInput, ScanService

from .test_backup_and_config import _write_secrets

ROOT = Path(__file__).resolve().parents[1]
SCAN_ART_JS = ROOT / "src" / "mtg_gateway" / "scan" / "static" / "scan-art.js"
API = "https://scryfall.test"
IMAGES = "https://images.test/"


# -- synthetic cards -------------------------------------------------------------
def _art(seed: int, w: int, h: int) -> Image.Image:
    """A smooth, colourful picture: coarse random blocks scaled up, so neighbours differ."""
    rng = random.Random(seed)
    tiny = Image.new("RGB", (9, 6))
    tiny.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(54)])
    return tiny.resize((w, h), Image.Resampling.BILINEAR)


def card_image(art_seed: int, frame: tuple[int, int, int], size: tuple[int, int] = (146, 204)) -> Image.Image:
    """A portrait card: a frame colour, the art in the real art box, a light text box below."""
    w, h = size
    im = Image.new("RGB", size, frame)
    d = ImageDraw.Draw(im)
    d.rectangle((int(w * 0.08), int(h * 0.56), int(w * 0.92), int(h * 0.93)), fill=(235, 230, 220))
    im.paste(_art(art_seed, int(w * 0.84), int(h * 0.42)), (int(w * 0.08), int(h * 0.1)))
    return im


def photo_like(im: Image.Image) -> Image.Image:
    """What the phone would hand over: a bigger, slightly darker, JPEG-compressed copy."""
    big = im.resize((488, 680), Image.Resampling.BILINEAR).point(lambda v: int(v * 0.9))
    buf = BytesIO()
    big.save(buf, "JPEG", quality=70)
    return Image.open(BytesIO(buf.getvalue())).convert("RGB")


def probe_of(im: Image.Image) -> str:
    import base64

    return base64.b64encode(art.art_hash(photo_like(im))).decode()


# -- a tiny Scryfall with one card in three printings ----------------------------------
ORACLE = "11111111-2222-3333-4444-555555555555"


def printing(
    pid: str, set_code: str, number: str, released: str, illustration: str, image: str
) -> dict[str, Any]:
    return {
        "object": "card",
        "id": pid,
        "oracle_id": ORACLE,
        "name": "Test Card",
        "set": set_code,
        "set_name": set_code.upper() + " set",
        "collector_number": number,
        "released_at": released,
        "illustration_id": illustration,
        "lang": "en",
        "digital": False,
        "games": ["paper"],
        "layout": "normal",
        "type_line": "Artifact",
        "mana_cost": "{1}",
        "cmc": 1,
        "color_identity": [],
        "rarity": "uncommon",
        "image_uris": {"small": image, "normal": image.replace("small", "normal")},
        "scryfall_uri": f"https://scryfall.com/card/{set_code}/{number}",
    }


class TinyScryfall:
    """One oracle card: printings A1 and A2 share artwork X; printing B has artwork Y."""

    def __init__(self) -> None:
        self.images = {
            "a1.jpg": card_image(1, (40, 40, 40)),
            "a2.jpg": card_image(1, (120, 60, 20)),  # same art, a different frame
            "b.jpg": card_image(2, (40, 40, 40)),
        }
        self.cards = [
            printing("id-a1", "aaa", "1", "2020-01-01", "ill-x", IMAGES + "a1.jpg"),
            printing("id-a2", "bbb", "2", "2022-01-01", "ill-x", IMAGES + "a2.jpg"),
            printing("id-b", "ccc", "3", "2018-01-01", "ill-y", IMAGES + "b.jpg"),
        ]
        self.api_requests: list[str] = []
        self.image_requests: list[str] = []

    def api(self, request: httpx.Request) -> httpx.Response:
        self.api_requests.append(str(request.url))
        path = request.url.path
        if path == "/cards/search":
            assert "oracleid:" + ORACLE in request.url.params["q"]
            data = sorted(self.cards, key=lambda c: c["released_at"], reverse=True)
            return httpx.Response(200, json={"object": "list", "has_more": False, "data": data})
        if path == "/cards/collection":
            found = []
            for ident in json.loads(request.content)["identifiers"]:
                if "collector_number" in ident:
                    found += [
                        c
                        for c in self.cards
                        if (c["set"], c["collector_number"]) == (ident["set"], ident["collector_number"])
                    ]
                elif ident.get("name", "").lower() == "test card":
                    found.append(self.cards[0])
            return httpx.Response(200, json={"object": "list", "not_found": [], "data": found})
        if path == "/cards/named" and request.url.params.get("exact", "").lower() == "test card":
            return httpx.Response(200, json=self.cards[0])
        return httpx.Response(404, json={"object": "error", "details": "no"})

    def image(self, request: httpx.Request) -> httpx.Response:
        self.image_requests.append(str(request.url))
        name = request.url.path.rsplit("/", 1)[-1]
        if name not in self.images:
            return httpx.Response(404)
        buf = BytesIO()
        self.images[name].save(buf, "JPEG", quality=85)
        return httpx.Response(200, content=buf.getvalue(), headers={"content-type": "image/jpeg"})


@pytest.fixture
def tiny(tmp_path: Path):
    sf = TinyScryfall()
    db = Database(tmp_path / "t.sqlite")
    client = ScryfallClient(
        API, min_interval=0.0, http=httpx.AsyncClient(transport=httpx.MockTransport(sf.api), base_url=API)
    )
    index = art.ArtIndex(
        db,
        client,
        art.ArtSettings(image_interval=0.0),
        image_http=httpx.AsyncClient(transport=httpx.MockTransport(sf.image)),
        image_host=IMAGES,
    )
    yield sf, db, client, index


# -- the hash ----------------------------------------------------------------------
def test_hash_is_stable_and_tells_artworks_apart() -> None:
    a = card_image(1, (40, 40, 40))
    row = art.gallery_row(a)
    assert len(row) == art.ROW_BYTES and art.art_hash(a) == art.art_hash(a)
    # The phone's copy of the same printing is close; a shifted crop is absorbed by the offsets.
    assert art.row_distance(art.art_hash(photo_like(a)), row) < 150
    assert art.row_distance(art.art_hash(a, 0, 0.02), row) == 0
    # Same art in another frame is still close; a different artwork is far.
    assert art.row_distance(art.art_hash(card_image(1, (120, 60, 20))), row) < 150
    assert art.row_distance(art.art_hash(card_image(2, (40, 40, 40))), row) > 300


def test_decode_hash_accepts_only_128_bytes() -> None:
    import base64

    raw = bytes(range(128))
    assert art.decode_hash(base64.b64encode(raw).decode()) == raw
    assert art.decode_hash(base64.urlsafe_b64encode(raw).decode().rstrip("=")) == raw
    assert art.decode_hash(base64.b64encode(raw[:100]).decode()) is None
    assert art.decode_hash("not base64!") is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run scan-art.js")
def test_browser_hash_matches_python_hash() -> None:
    im = photo_like(card_image(3, (10, 90, 30)))
    out = subprocess.run(
        [
            "node",
            str(ROOT / "tests" / "scan_art_harness.js"),
            str(SCAN_ART_JS),
            str(im.width),
            str(im.height),
        ],
        input=im.convert("RGBA").tobytes(),
        capture_output=True,
        check=True,
    )
    js = art.decode_hash(out.stdout.decode())
    assert js is not None
    drift = art.hamming(js, art.art_hash(im))
    assert drift <= 40, f"JS and Python hashes differ by {drift} bits"  # measured 14; margin is 40


# -- gallery and matching ----------------------------------------------------------------
async def test_first_scan_learns_then_matches(tiny) -> None:
    sf, db, client, index = tiny
    current = {"oracle_id": ORACLE, "scryfall_id": "id-a1", "name": "Test Card"}
    first = await index.match(CardInput(name="Test Card", art_hash=probe_of(sf.images["b.jpg"])), current)
    assert first == {"art": {"status": "learning", "note": "learning this card's printings"}}
    await index.wait_idle()
    assert len(sf.image_requests) == 3 and index._state(ORACLE)["status"] == "ready"
    assert index._state(ORACLE)["hashed"] == 3

    # Artwork Y: art picks printing B and says so; the picker gets only that artwork's printings.
    out = await index.match(CardInput(name="Test Card", art_hash=probe_of(sf.images["b.jpg"])), current)
    assert out["id"] == "id-b" and out["name"] == "Test Card"
    assert out["art"]["status"] == "matched" and out["art"]["margin"] >= 40
    assert [p["set"] for p in out["art"]["printings"]] == ["ccc"]

    # Artwork X from the other frame: the current printing already has it, so just confirm,
    # and list both printings that share it (newest first) for the picker.
    out = await index.match(CardInput(name="Test Card", art_hash=probe_of(sf.images["a2.jpg"])), current)
    assert out["art"]["status"] == "confirmed" and "name" not in out
    assert [p["set"] for p in out["art"]["printings"]] == ["bbb", "aaa"]

    # Noise: not confident, nothing chosen.
    noise = Image.new("RGB", (146, 204))
    noise.putdata([(random.randrange(256), 0, random.randrange(256)) for _ in range(146 * 204)])
    out = await index.match(CardInput(name="Test Card", art_hash=probe_of(noise)), current)
    assert out["art"]["status"] == "unsure" and "name" not in out
    # Everything came from the cache: no further Scryfall traffic.
    assert len(sf.api_requests) == 1 and len(sf.image_requests) == 3
    await index.aclose()
    await client.aclose()


async def test_gallery_is_capped_and_off_host_images_are_refused(tiny) -> None:
    sf, db, client, index = tiny
    index.s.max_printings = 2
    sf.cards[2]["image_uris"]["small"] = "https://elsewhere.test/b.jpg"
    current = {"oracle_id": ORACLE, "scryfall_id": "id-a1", "name": "Test Card"}
    await index.match(CardInput(name="Test Card", art_hash=probe_of(sf.images["a1.jpg"])), current)
    await index.wait_idle()
    state = index._state(ORACLE)
    assert state["status"] == "too_many" and state["printings"] == 3 and state["hashed"] == 2
    assert all(u.startswith(IMAGES) for u in sf.image_requests) and len(sf.image_requests) == 2
    await index.aclose()
    await client.aclose()


async def test_disabled_or_missing_hash_is_a_no_op(tiny) -> None:
    sf, db, client, index = tiny
    current = {"oracle_id": ORACLE, "scryfall_id": "id-a1", "name": "Test Card"}
    assert await index.match(CardInput(name="Test Card"), current) is None
    assert await index.match(CardInput(name="Test Card", art_hash="AAAA"), current) is None
    index.s.enabled = False
    assert (
        await index.match(CardInput(name="Test Card", art_hash=probe_of(sf.images["b.jpg"])), current) is None
    )
    assert not index._tasks and not sf.api_requests
    await index.aclose()
    await client.aclose()


# -- through the resolver -------------------------------------------------------------------
async def test_resolver_uses_art_for_names_but_never_overrides_a_collector_line(tiny) -> None:
    sf, db, client, index = tiny
    service = ScanService(db, client, art_matcher=index.match)
    probe_b = probe_of(sf.images["b.jpg"])

    # First scan: the name resolves as before, with a learning note while the gallery builds.
    (res,) = await service.resolve([CardInput(name="Test Card", art_hash=probe_b)])
    assert res.status == "exact" and res.card["set"] == "aaa"
    assert res.art["status"] == "learning" and "learning this card's printings" in res.note
    await index.wait_idle()

    # Name only: art chooses the printing whose artwork matches.
    (res,) = await service.resolve([CardInput(name="Test Card", art_hash=probe_b)])
    assert res.status == "exact" and res.card["set"] == "ccc" and res.card["scryfall_id"] == "id-b"
    assert res.note == "printing chosen by artwork" and res.art["status"] == "matched"
    d = res.as_dict()
    assert d["art"]["status"] == "matched" and d["card"]["collector_number"] == "3"

    # Set and number read from the card: the printing stays, art only notes the disagreement.
    (res,) = await service.resolve(
        [CardInput(name="Test Card", set_code="aaa", collector_number="1", art_hash=probe_b)]
    )
    assert res.status == "printing" and res.card["set"] == "aaa" and res.card["scryfall_id"] == "id-a1"
    assert res.art["status"] == "mismatch" and "artwork looks like CCC 3" in res.note

    # Set and number whose artwork agrees: confirmed, printing unchanged.
    (res,) = await service.resolve(
        [
            CardInput(
                name="Test Card", set_code="aaa", collector_number="1", art_hash=probe_of(sf.images["a2.jpg"])
            )
        ]
    )
    assert (
        res.status == "printing" and res.card["scryfall_id"] == "id-a1" and res.art["status"] == "confirmed"
    )

    # Without a hash nothing changes.
    (res,) = await service.resolve([CardInput(name="Test Card")])
    assert res.card["set"] == "aaa" and res.art is None
    await index.aclose()
    await client.aclose()


def test_art_settings_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_secrets(tmp_path, monkeypatch)
    s = load_settings()
    assert (s.scan_art_enabled, s.scan_art_max_distance, s.scan_art_min_margin) == (True, 380, 40)
    assert (s.scan_art_max_printings, s.scan_art_image_interval) == (250, 0.1)
    assert (s.scan_art_max_galleries, s.scan_art_gallery_idle_days) == (500, 180)
    monkeypatch.setenv("MTG_SCAN_ART_ENABLED", "false")
    monkeypatch.setenv("MTG_SCAN_ART_MAX_GALLERIES", "50")
    monkeypatch.setenv("MTG_SCAN_ART_MIN_MARGIN", "60")
    monkeypatch.setenv("MTG_SCAN_ART_MAX_PRINTINGS", "2000")
    s = load_settings()
    assert (s.scan_art_enabled, s.scan_art_min_margin, s.scan_art_max_printings) == (False, 60, 2000)
    assert s.scan_art_max_galleries == 50
    monkeypatch.setenv("MTG_SCAN_ART_MAX_PRINTINGS", "5000")
    with pytest.raises(ConfigError, match="MTG_SCAN_ART_MAX_PRINTINGS"):
        load_settings()


# -- caps and hardening from the review -------------------------------------------------
async def test_pending_builds_are_capped(tiny) -> None:
    sf, db, client, index = tiny
    probe = probe_of(sf.images["b.jpg"])
    outs = []
    for i in range(MAX_PENDING_BUILDS + 3):
        outs.append(
            await index.match(CardInput(name="x", art_hash=probe), {"oracle_id": f"oracle-{i}", "name": "x"})
        )
    assert len(index._tasks) == MAX_PENDING_BUILDS
    assert [o["art"]["status"] for o in outs] == ["learning"] * MAX_PENDING_BUILDS + ["busy"] * 3
    await index.wait_idle()
    assert not index._tasks
    await index.aclose()
    await client.aclose()


async def test_hourly_image_budget_pauses_learning(tiny) -> None:
    sf, db, client, index = tiny
    index.s.images_per_hour = 2
    current = {"oracle_id": ORACLE, "scryfall_id": "id-a1", "name": "Test Card"}
    await index.match(CardInput(name="Test Card", art_hash=probe_of(sf.images["b.jpg"])), current)
    await index.wait_idle()
    state = index._state(ORACLE)
    assert state["status"] == "partial" and state["hashed"] == 2 and len(sf.image_requests) == 2
    assert index.paused_until > __import__("time").monotonic() + 3000
    # The two hashed printings already serve matches; the rest waits for the hour to roll on.
    out = await index.match(CardInput(name="Test Card", art_hash=probe_of(sf.images["a2.jpg"])), current)
    assert out["art"]["status"] == "confirmed" and not index._tasks
    await index.aclose()
    await client.aclose()


def test_image_decoding_refuses_oversized_or_non_jpeg_data() -> None:
    buf = BytesIO()
    Image.new("RGB", (2500, 60), (9, 9, 9)).save(buf, "JPEG", quality=10)
    assert len(buf.getvalue()) < art.MAX_IMAGE_BYTES and art._hash_bytes(buf.getvalue()) is None
    png = BytesIO()
    card_image(1, (0, 0, 0)).save(png, "PNG")
    assert art._hash_bytes(png.getvalue()) is None
    jpg = BytesIO()
    card_image(1, (0, 0, 0)).save(jpg, "JPEG")
    assert len(art._hash_bytes(jpg.getvalue()) or b"") == art.ROW_BYTES


async def test_set_lock_keeps_the_pick_inside_the_set(tiny) -> None:
    sf, db, client, index = tiny
    # The name lookup resolved to the ccc printing (artwork Y); the user has locked set aaa.
    current = {"oracle_id": ORACLE, "scryfall_id": "id-b", "name": "Test Card"}
    await index.match(CardInput(name="Test Card", art_hash=probe_of(sf.images["a1.jpg"])), current)
    await index.wait_idle()
    # Artwork X exists in sets aaa and bbb: with the lock on aaa the older aaa printing is picked.
    out = await index.match(
        CardInput(name="Test Card", set_code="aaa", art_hash=probe_of(sf.images["a1.jpg"])), current
    )
    assert out["art"]["status"] == "matched" and out["id"] == "id-a1"
    # Artwork Y exists only in ccc: a lock on aaa gets a note, never a printing from another set.
    current = {"oracle_id": ORACLE, "scryfall_id": "id-a1", "name": "Test Card"}
    out = await index.match(
        CardInput(name="Test Card", set_code="aaa", art_hash=probe_of(sf.images["b.jpg"])), current
    )
    assert (
        out["art"]["status"] == "mismatch" and "not a AAA printing" in out["art"]["note"] and "id" not in out
    )
    await index.aclose()
    await client.aclose()


# -- pruning ------------------------------------------------------------------------------
def _gallery(index: art.ArtIndex, oracle_id: str, last_used: int, printings: int = 2) -> None:
    """A learned gallery as the build would leave it, with a chosen last-used time."""
    index._set_state(oracle_id, "ready", printings, printings)
    with index.db.tx() as c:
        for i in range(printings):
            c.execute(
                "INSERT INTO scan_art_hashes (printing_id, oracle_id, illustration_id, released_at, hashes, "
                "card_json, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"{oracle_id}-{i}", oracle_id, f"ill-{oracle_id}", "", b"\0" * art.ROW_BYTES, "{}", 0),
            )
        c.execute("UPDATE scan_art_galleries SET last_used = ? WHERE oracle_id = ?", (last_used, oracle_id))


def _galleries(index: art.ArtIndex) -> dict[str, int]:
    with index.db.tx() as c:
        rows = c.execute(
            "SELECT g.oracle_id, COUNT(h.printing_id) AS n FROM scan_art_galleries g "
            "LEFT JOIN scan_art_hashes h ON h.oracle_id = g.oracle_id GROUP BY g.oracle_id"
        ).fetchall()
        orphans = c.execute(
            "SELECT COUNT(*) FROM scan_art_hashes "
            "WHERE oracle_id NOT IN (SELECT oracle_id FROM scan_art_galleries)"
        ).fetchone()[0]
    assert orphans == 0
    return {r["oracle_id"]: r["n"] for r in rows}


async def test_idle_galleries_are_forgotten(tiny) -> None:
    sf, db, client, index = tiny
    now = int(__import__("time").time())
    _gallery(index, "fresh", now - 86400)
    _gallery(index, "old", now - (index.s.gallery_idle_days + 1) * 86400)
    assert index.prune() == 1
    assert _galleries(index) == {"fresh": 2}
    await index.aclose()
    await client.aclose()


async def test_gallery_count_is_capped_by_last_use(tiny) -> None:
    sf, db, client, index = tiny
    index.s.max_galleries = 3
    now = int(__import__("time").time())
    for i in range(5):
        _gallery(index, f"card-{i}", now - i * 3600)  # card-0 scanned most recently
    assert index.prune() == 2
    assert set(_galleries(index)) == {"card-0", "card-1", "card-2"}
    # A scan keeps its card: it moves to the front before the next prune.
    with db.tx() as c:
        c.execute(
            "UPDATE scan_art_galleries SET last_used = ? WHERE oracle_id = 'card-2'", (now - 10 * 86400,)
        )
    index._touch("card-2", index._state("card-2"))
    _gallery(index, "card-9", now)
    index.prune()
    assert set(_galleries(index)) == {"card-0", "card-2", "card-9"}
    await index.aclose()
    await client.aclose()


async def test_a_gallery_being_built_is_kept_and_prune_runs_after_builds(tiny) -> None:
    sf, db, client, index = tiny
    index.s.max_galleries = 1
    now = int(__import__("time").time())
    _gallery(index, "stale", now - 3 * 86400)
    current = {"oracle_id": ORACLE, "scryfall_id": "id-a1", "name": "Test Card"}
    out = await index.match(CardInput(name="Test Card", art_hash=probe_of(sf.images["b.jpg"])), current)
    assert out["art"]["status"] == "learning"
    # The build in flight is never dropped, even though the cap is already full.
    assert index.prune() == 0
    await index.wait_idle()
    # Once built, the newly scanned card is the one kept and the stale one went.
    assert set(_galleries(index)) == {ORACLE}
    await index.aclose()
    await client.aclose()


def test_old_database_gets_the_last_used_column(tmp_path: Path) -> None:
    db = Database(tmp_path / "old.sqlite")
    now = int(__import__("time").time())
    with db.tx() as c:
        c.execute(
            "CREATE TABLE scan_art_galleries (oracle_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "printings INTEGER NOT NULL DEFAULT 0, hashed INTEGER NOT NULL DEFAULT 0, "
            "updated_at INTEGER NOT NULL)"
        )
        c.execute("INSERT INTO scan_art_galleries VALUES ('x', 'ready', 1, 1, ?)", (now - 60,))
    client = ScryfallClient(
        API,
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
    )
    index = art.ArtIndex(db, client, art.ArtSettings(), image_http=httpx.AsyncClient(), image_host=IMAGES)
    # Galleries learned before the column existed count as used when they were built, so an
    # upgrade keeps them; the startup prune only drops what the idle rule says.
    state = index._state("x")
    assert state is not None and state["last_used"] == now - 60


def test_startup_prune_failure_does_not_stop_the_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(self: art.ArtIndex) -> int:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(art.ArtIndex, "prune", boom)
    client = ScryfallClient(
        API,
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
    )
    with caplog.at_level("ERROR"):
        index = art.ArtIndex(
            Database(tmp_path / "t.sqlite"), client, art.ArtSettings(), image_http=httpx.AsyncClient()
        )
    assert index._state("x") is None  # the index works; the prune error was only logged
    assert "prune failed at startup" in caplog.text
