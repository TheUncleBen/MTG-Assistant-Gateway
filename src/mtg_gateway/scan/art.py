"""Artwork matching: a second opinion on which printing a scanned card is.

The phone hashes the art box of the flattened card (``static/scan-art.js``) and sends
128 bytes with the title. Here that hash is compared, by Hamming distance, with hashes of
every printing of the card the title resolved to. Art alone cannot tell printings apart
when they share an illustration (40% of a typical deck's artworks are reprinted
unchanged), so the collector line stays the printing decider: a set+number read is only
ever confirmed or annotated, never replaced. Art chooses among printings whose art
differs when only the name was read, and narrows the picker to the printings that share
the matched artwork.

Hash (the ``algo_version 1`` spec of neotoxicfr/mtg-scanner-art-index, MIT; see
``docs/THIRD-PARTY-NOTICES.md``): interior art box y 16% to 50%, x 14% to 86% of the
portrait card, four 256-bit dHash planes (grey, B, G, R) from a 17x16 box-resampled
thumbnail. The published index file of that project is not used: one hash per
illustration mis-hashes retro-frame reprints of the same art. The gateway builds its own
gallery with one row per printing from Scryfall's ``small`` images (about 15 KB each),
fetched once per card, paced, and kept in SQLite (768 bytes per printing: six framing
offsets so the phone's crop may be a little off).

Measured on 1,338 synthetic photos (research/scan-art-matching, 2026-10-05): with the
card flattened, the artwork is right 99.0% of the time; accepting only when distance
<= 380 and the margin to the nearest other artwork >= 40 leaves 0 wrong picks on a
normal photo and about 0.5% on a bad one.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import httpx

from ..db import Database
from .scryfall import ScryfallClient, ScryfallError

logger = logging.getLogger(__name__)

HASH_BYTES = 128
SIDE = 16
ART_TOP, ART_BOTTOM, ART_LEFT, ART_RIGHT = 0.16, 0.50, 0.14, 0.86
# Gallery-side framing offsets (dx, dy): the mirror of the spec's query offsets, so the phone
# sends one hash and a crop that sits up to 4% high or 2% low still matches.
OFFSETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (0.0, 0.02),
    (0.0, -0.02),
    (0.0, -0.04),
    (0.011, 0.0),
    (-0.011, 0.0),
)
ROW_BYTES = HASH_BYTES * len(OFFSETS)
IMAGE_HOST = "https://cards.scryfall.io/"
MAX_IMAGE_BYTES = 400_000  # a ``small`` JPEG is about 15 KB; anything near this is not one
GALLERY_TTL = 30 * 24 * 3600  # rebuild a card's gallery after a month (new printings)
RETRY_AFTER_FAILURE = 600  # seconds before a failed or interrupted build is tried again
MAX_PICKER = 12  # printings listed for the picker when the art is matched
MAX_PENDING_BUILDS = 8  # galleries queued at once; later scans of the other cards queue them again
# The member whose scan is being resolved (set by ScanService.resolve). Gallery builds and the
# images they fetch are counted against that member's own share, so one member cannot spend the
# gateway-wide image budget (pausing every build) or learn enough cards to evict everyone else's.
art_owner: ContextVar[str | None] = ContextVar("art_owner", default=None)
OWNER_IMAGE_SHARE = 4  # one member may use at most 1/4 of images_per_hour in any rolling hour
OWNER_GALLERY_SHARE = 20  # ...and start at most max_galleries/20 new galleries in any rolling hour
TOUCH_INTERVAL = 600  # seconds between last_used writes for the same gallery (one per scan is noise)

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_art_hashes (
    printing_id TEXT PRIMARY KEY,
    oracle_id TEXT NOT NULL,
    illustration_id TEXT NOT NULL,
    released_at TEXT NOT NULL DEFAULT '',
    hashes BLOB NOT NULL,
    card_json TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS scan_art_hashes_oracle ON scan_art_hashes(oracle_id);
CREATE TABLE IF NOT EXISTS scan_art_galleries (
    oracle_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    printings INTEGER NOT NULL DEFAULT 0,
    hashed INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    last_used INTEGER NOT NULL DEFAULT 0
);
"""
# Columns added after the first release, applied to databases created before them.
_MIGRATIONS = (("scan_art_galleries", "last_used", "INTEGER NOT NULL DEFAULT 0"),)

# The raw Scryfall keys ``scryfall.summarize`` reads, so a stored printing can be handed back
# through the resolver's art hook like a fresh Scryfall card.
_KEEP = (
    "id",
    "oracle_id",
    "name",
    "set",
    "set_name",
    "collector_number",
    "rarity",
    "type_line",
    "mana_cost",
    "cmc",
    "color_identity",
    "layout",
    "lang",
    "released_at",
    "image_uris",
    "scryfall_uri",
    "legalities",
    "illustration_id",
    "card_faces",
    "frame",
    "finishes",
)


@dataclass
class ArtSettings:
    """Filled from MTG_SCAN_ART_* settings; see docs/SCANNING.md."""

    enabled: bool = True
    max_distance: int = 380  # bits of 1024; a correct match under glare can reach 430
    min_margin: int = 40  # bits to the nearest other artwork; the real confidence signal
    max_printings: int = 250  # newest printings hashed per card (basic lands have hundreds)
    image_interval: float = 0.1  # seconds between image fetches, gateway-wide
    images_per_hour: int = 1500  # gateway-wide budget of image fetches in any rolling hour
    max_galleries: int = 500  # cards kept learned; past it the least recently scanned are dropped
    gallery_idle_days: int = 180  # a card not scanned for this long is forgotten (relearned on demand)


# -- hashing -------------------------------------------------------------------


def _plane_bits(small: Any) -> bytes:
    """256-bit dHash of a 17x16 greyscale thumbnail: bit = pixel[x+1] > pixel[x], row-major."""
    px = small.load()
    out = bytearray(SIDE * SIDE // 8)
    bit = 0
    for y in range(SIDE):
        for x in range(SIDE):
            if px[x + 1, y] > px[x, y]:
                out[bit >> 3] |= 0x80 >> (bit & 7)
            bit += 1
    return bytes(out)


def art_hash(image: Any, dx: float = 0.0, dy: float = 0.0) -> bytes:
    """128-byte art hash of a portrait card image (a Pillow image), art box shifted by dx, dy."""
    from PIL import Image

    rgb = image.convert("RGB")
    w, h = rgb.size
    box = (
        max(0, round(w * (ART_LEFT + dx))),
        max(0, round(h * (ART_TOP + dy))),
        min(w, round(w * (ART_RIGHT + dx))),
        min(h, round(h * (ART_BOTTOM + dy))),
    )
    crop = rgb.crop(box)
    planes = [crop.convert("L")]
    r, g, b = crop.split()
    planes += [b, g, r]
    return b"".join(_plane_bits(p.resize((SIDE + 1, SIDE), Image.Resampling.BOX)) for p in planes)


def gallery_row(image: Any) -> bytes:
    """All framing offsets of one reference image, concatenated (ROW_BYTES)."""
    return b"".join(art_hash(image, dx, dy) for dx, dy in OFFSETS)


def hamming(a: bytes, b: bytes) -> int:
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).bit_count()


def row_distance(probe: bytes, row: bytes) -> int:
    """Smallest distance between the probe and any framing offset of a gallery row."""
    return min(hamming(probe, row[i : i + HASH_BYTES]) for i in range(0, len(row), HASH_BYTES))


def decode_hash(text: str) -> bytes | None:
    """The page's base64 (standard or URL-safe) hash; None when it is not 128 bytes."""
    s = text.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == HASH_BYTES else None


# -- gallery and matcher -----------------------------------------------------------


@dataclass
class _Printing:
    id: str
    illustration_id: str
    released_at: str
    hashes: bytes
    card: dict[str, Any]


class ArtIndex:
    """Per-card galleries of printing hashes, built in the background, and the match itself.

    ``match`` is the resolver's art hook: it never waits for Scryfall. A card whose gallery
    is not built yet gets a "learning" note and a background build (one at a time, paced);
    the next scan of that card uses whatever is hashed by then.
    """

    def __init__(
        self,
        db: Database,
        scryfall: ScryfallClient,
        settings: ArtSettings | None = None,
        *,
        image_http: httpx.AsyncClient | None = None,
        image_host: str = IMAGE_HOST,
    ):
        self.db = db
        self.scryfall = scryfall
        self.s = settings or ArtSettings()
        self._own_http = image_http is None
        self.http = image_http or httpx.AsyncClient(timeout=20.0, follow_redirects=False)
        self.image_host = image_host
        self._pace = asyncio.Lock()
        self._last_image = 0.0
        self._build_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self.paused_until = 0.0  # builds hold while this is in the future: a 429, or the budget spent
        self.images_fetched = 0
        self._fetch_times: list[float] = []  # monotonic times of the last hour's image fetches
        self._owner_fetches: dict[str, list[float]] = {}  # the same, per member
        self._owner_builds: dict[str, list[float]] = {}  # galleries each member started, last hour
        with db.tx() as c:
            for stmt in SCHEMA.split(";"):
                if stmt.strip():
                    c.execute(stmt)
            for table, column, decl in _MIGRATIONS:
                have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
                if column not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                    if column == "last_used":  # galleries learned before this column count as used then
                        c.execute(f"UPDATE {table} SET last_used = updated_at")
        try:
            self.prune()
        except Exception:  # a cache: never keep the gateway from starting
            logger.exception("art gallery prune failed at startup")

    async def aclose(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()
        for t in list(self._tasks.values()):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if self._own_http:
            await self.http.aclose()

    async def wait_idle(self) -> None:
        """Tests: wait for every background build to finish."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    def pause(self, seconds: float) -> None:
        """Hold gallery fetches for a while (a scan call ran out of budget or Scryfall said 429)."""
        self.paused_until = max(self.paused_until, time.monotonic() + seconds)

    # -- the hook ---------------------------------------------------------------
    async def match(self, inp: Any, card: dict[str, Any]) -> dict[str, Any] | None:
        """Resolver art hook. ``card`` is the summarized card the title (and maybe the info
        line) resolved to. Returns None, or a dict carrying ``art`` (status, distance,
        margin, note, printings) and, when art should pick the printing, the chosen
        printing's fields under the usual Scryfall keys (``name`` present)."""
        if not self.s.enabled:
            return None
        probe = decode_hash(getattr(inp, "art_hash", "") or "")
        oracle_id = card.get("oracle_id")
        if probe is None or not oracle_id:
            return None
        rows = self._rows(oracle_id)
        state = self._state(oracle_id)
        if rows and state is not None:
            self._touch(oracle_id, state)
        owner = art_owner.get()
        learning = self._ensure_gallery(oracle_id, state, owner)
        if not rows:
            if learning:
                return {"art": {"status": "learning", "note": self._learning_note(state)}}
            if len(self._tasks) >= MAX_PENDING_BUILDS or self._owner_over_builds(owner):
                return {
                    "art": {"status": "busy", "note": "other cards are being learned first; scan again later"}
                }
            return {"art": {"status": "unavailable"}}

        scored = sorted(((row_distance(probe, p.hashes), p) for p in rows), key=lambda t: t[0])
        best_d, best = scored[0]
        others = [d for d, p in scored if p.illustration_id != best.illustration_id]
        margin = (others[0] - best_d) if others else 1024
        same_art = [p for _, p in scored if p.illustration_id == best.illustration_id]
        same_art.sort(key=lambda p: p.released_at, reverse=True)
        art: dict[str, Any] = {
            "distance": best_d,
            "margin": margin,
            "illustration_id": best.illustration_id,
            "printings": [_label(p.card) for p in same_art[:MAX_PICKER]],
        }
        confident = best_d <= self.s.max_distance and margin >= self.s.min_margin
        current_id = card.get("scryfall_id") or card.get("id")
        current = next((p for p in rows if p.id == current_id), None)
        if learning:
            art["note"] = self._learning_note(state)
        if not confident:
            art["status"] = "unsure"
            return {"art": art}
        if current is not None and current.illustration_id == best.illustration_id:
            art["status"] = "confirmed"
            return {"art": art}
        inp_set = (getattr(inp, "set_code", "") or "").lower()
        if inp_set and getattr(inp, "collector_number", ""):
            # A set and number were read: the collector line decides. Say what the art saw.
            art["status"] = "mismatch"
            art["note"] = (
                f"artwork looks like {_text_label(same_art[0].card)} rather than "
                f"{inp_set.upper()} {inp.collector_number}; check the card"
            )
            return {"art": art}
        if inp_set:
            # A set lock (or a set read without a number): never pick outside that set.
            in_set = [p for p in same_art if (p.card.get("set") or "").lower() == inp_set]
            if not in_set:
                art["status"] = "mismatch"
                art["note"] = (
                    f"artwork looks like {_text_label(same_art[0].card)}, not a {inp_set.upper()} printing"
                )
                return {"art": art}
            same_art = in_set
        art["status"] = "matched"
        return {**same_art[0].card, "art": art}

    # -- storage ----------------------------------------------------------------
    def _rows(self, oracle_id: str) -> list[_Printing]:
        with self.db.tx() as c:
            rs = c.execute(
                "SELECT printing_id, illustration_id, released_at, hashes, card_json "
                "FROM scan_art_hashes WHERE oracle_id = ?",
                (oracle_id,),
            ).fetchall()
        return [
            _Printing(
                r["printing_id"],
                r["illustration_id"],
                r["released_at"],
                r["hashes"],
                json.loads(r["card_json"]),
            )
            for r in rs
        ]

    def _state(self, oracle_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM scan_art_galleries WHERE oracle_id = ?", (oracle_id,)).fetchone()
        return dict(r) if r else None

    def _set_state(self, oracle_id: str, status: str, printings: int, hashed: int) -> None:
        now = int(time.time())
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO scan_art_galleries (oracle_id, status, printings, hashed, updated_at, "
                "last_used) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(oracle_id) DO UPDATE SET "
                "status = excluded.status, printings = excluded.printings, hashed = excluded.hashed, "
                "updated_at = excluded.updated_at",
                (oracle_id, status, printings, hashed, now, now),
            )

    def _touch(self, oracle_id: str, state: dict[str, Any]) -> None:
        """Record that this card's gallery served a scan, at most once per TOUCH_INTERVAL."""
        now = int(time.time())
        if now - int(state.get("last_used") or 0) < TOUCH_INTERVAL:
            return
        with self.db.tx() as c:
            c.execute("UPDATE scan_art_galleries SET last_used = ? WHERE oracle_id = ?", (now, oracle_id))

    def prune(self) -> int:
        """Forget galleries nobody scans: those unused for ``gallery_idle_days`` and, past
        ``max_galleries``, the least recently used. A forgotten card is simply learned again on
        its next scan. Galleries being built right now are kept. Returns the number dropped."""
        now = int(time.time())
        cutoff = now - self.s.gallery_idle_days * 86400
        busy = set(self._tasks)
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT oracle_id, last_used FROM scan_art_galleries ORDER BY last_used DESC, oracle_id"
            ).fetchall()
            drop = {
                r["oracle_id"] for r in rows if int(r["last_used"]) < cutoff and r["oracle_id"] not in busy
            }
            kept = [r["oracle_id"] for r in rows if r["oracle_id"] not in drop]
            if len(kept) > self.s.max_galleries:
                drop.update(o for o in kept[self.s.max_galleries :] if o not in busy)
            for oracle_id in sorted(drop):
                c.execute("DELETE FROM scan_art_hashes WHERE oracle_id = ?", (oracle_id,))
                c.execute("DELETE FROM scan_art_galleries WHERE oracle_id = ?", (oracle_id,))
            c.execute(
                "DELETE FROM scan_art_hashes "
                "WHERE oracle_id NOT IN (SELECT oracle_id FROM scan_art_galleries)"
            )
        if drop:
            logger.info("art gallery: forgot %d card(s) not scanned recently", len(drop))
        return len(drop)

    @staticmethod
    def _learning_note(state: dict[str, Any] | None) -> str:
        if state and state.get("printings"):
            return f"learning this card's printings ({state['hashed']} of {state['printings']} done)"
        return "learning this card's printings"

    @staticmethod
    def _recent(times: list[float], now: float) -> list[float]:
        return [t for t in times if now - t < 3600]

    def _owner_build_cap(self) -> int:
        return max(1, self.s.max_galleries // OWNER_GALLERY_SHARE)

    def _owner_image_cap(self) -> int:
        return max(1, self.s.images_per_hour // OWNER_IMAGE_SHARE)

    def _owner_over_builds(self, owner: str | None) -> bool:
        if owner is None:
            return False
        now = time.monotonic()
        recent = self._recent(self._owner_builds.get(owner, []), now)
        if recent:
            self._owner_builds[owner] = recent
        else:
            self._owner_builds.pop(owner, None)
        return len(recent) >= self._owner_build_cap()

    def _ensure_gallery(self, oracle_id: str, state: dict[str, Any] | None, owner: str | None = None) -> bool:
        """Start a background build when the gallery is missing, stale or was interrupted.
        Returns True when a build is running or pending for this card. A member who started
        their share of builds this hour starts no more until the hour rolls on."""
        if oracle_id in self._tasks:
            return True
        if len(self._tasks) >= MAX_PENDING_BUILDS:
            return False
        now = int(time.time())
        if state is not None:
            age = now - int(state["updated_at"])
            if state["status"] == "ready" and age < GALLERY_TTL:
                return False
            if state["status"] in ("partial", "failed") and age < RETRY_AFTER_FAILURE:
                return False
            if state["status"] == "too_many" and age < GALLERY_TTL:
                return False
        if self._owner_over_builds(owner):
            return False
        if owner is not None:
            self._owner_builds.setdefault(owner, []).append(time.monotonic())
        self._tasks[oracle_id] = asyncio.create_task(self._build(oracle_id, owner))
        return True

    # -- building --------------------------------------------------------------
    async def _build(self, oracle_id: str, owner: str | None = None) -> None:
        try:
            async with self._build_lock:  # one gallery at a time: the Pi decodes one image at once
                await self._build_locked(oracle_id, owner)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("art gallery build failed for %s", oracle_id)
            state = self._state(oracle_id) or {}
            self._set_state(
                oracle_id, "failed", int(state.get("printings") or 0), int(state.get("hashed") or 0)
            )
        finally:
            self._tasks.pop(oracle_id, None)
            try:
                self.prune()
            except Exception:
                logger.exception("art gallery prune failed")

    async def _build_locked(self, oracle_id: str, owner: str | None = None) -> None:
        await self._wait_unpaused()
        try:
            printings = await self._printings(oracle_id)
        except ScryfallError as exc:
            if exc.kind == "rate_limited":
                self.pause(self.scryfall.retry_in() or 60)
            self._set_state(oracle_id, "failed", 0, 0)
            logger.warning("art gallery: could not list printings for %s: %s", oracle_id, exc)
            return
        total = len(printings)
        if total > self.s.max_printings:
            printings = printings[: self.s.max_printings]
        have = {p.id for p in self._rows(oracle_id)}
        done = len(have)
        self._set_state(oracle_id, "building", total, done)
        for card in printings:
            if card["id"] in have:
                continue
            url = _small_image(card)
            if not url:
                continue
            await self._wait_unpaused()
            try:
                row = await self._hash_image(url, owner)
            except ScryfallError as exc:
                if exc.kind == "rate_limited":
                    self.pause(60)  # the gateway's budget or the image host: every build waits
                logger.warning("art gallery: %s: %s", url, exc)
                self._set_state(oracle_id, "partial", total, done)
                return
            if row is None:
                continue
            with self.db.tx() as c:
                c.execute(
                    "INSERT OR REPLACE INTO scan_art_hashes (printing_id, oracle_id, illustration_id, "
                    "released_at, hashes, card_json, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        card["id"],
                        oracle_id,
                        card.get("illustration_id") or card["id"],
                        card.get("released_at") or "",
                        row,
                        json.dumps({k: card[k] for k in _KEEP if k in card}, separators=(",", ":")),
                        int(time.time()),
                    ),
                )
            done += 1
            self._set_state(oracle_id, "building", total, done)
        status = "too_many" if total > self.s.max_printings else "ready"
        self._set_state(oracle_id, status, total, done)

    def _take_budget(self, owner: str | None = None) -> bool:
        """Count one image fetch against the rolling hourly budget; pause builds when it is spent.
        With ``owner``, the fetch also counts against that member's share of the budget; past it
        that member's build stops (``owner_budget``) without pausing anyone else's."""
        now = time.monotonic()
        mine: list[float] = []
        if owner is not None:
            mine = self._recent(self._owner_fetches.get(owner, []), now)
            self._owner_fetches[owner] = mine
            if len(mine) >= self._owner_image_cap():
                raise ScryfallError("owner_budget", "your share of this hour's image budget is spent")
        self._fetch_times = self._recent(self._fetch_times, now)
        if len(self._fetch_times) >= self.s.images_per_hour:
            self.pause(3600 - (now - self._fetch_times[0]))
            return False
        self._fetch_times.append(now)
        if owner is not None:
            mine.append(now)
        return True

    async def _wait_unpaused(self) -> None:
        while (wait := self.paused_until - time.monotonic()) > 0:
            await asyncio.sleep(min(wait, 5.0))

    async def _printings(self, oracle_id: str) -> list[dict[str, Any]]:
        """Every paper, English, non-digital printing of the card, newest first."""
        path: str | None = "/cards/search"
        params: dict[str, str] | None = {
            "q": f"oracleid:{oracle_id}",
            "unique": "prints",
            "order": "released",
            "dir": "desc",
        }
        out: list[dict[str, Any]] = []
        pages = 0
        while path and pages < 10:
            resp = await self.scryfall._request(
                "GET", path, params=params, interval=self.scryfall.lookup_interval
            )
            data = self.scryfall._json(resp)
            if resp.status_code == 404 or data.get("object") == "error":
                break
            cards = data.get("data")
            if data.get("object") != "list" or not isinstance(cards, list):
                raise ScryfallError("contract", "Scryfall search returned an unexpected body")
            for c in cards:
                if not isinstance(c, dict) or c.get("digital") or c.get("lang") != "en":
                    continue
                if "paper" not in (c.get("games") or ["paper"]):
                    continue
                out.append(c)
            pages += 1
            nxt = data.get("next_page") if data.get("has_more") else None
            if nxt and isinstance(nxt, str) and nxt.startswith(self.scryfall.base + "/"):
                path, params = nxt[len(self.scryfall.base) :], None
            else:
                path = None
        return out

    async def _hash_image(self, url: str, owner: str | None = None) -> bytes | None:
        """Fetch one ``small`` card image, paced gateway-wide, and hash it at every offset."""
        if not url.startswith(self.image_host):
            logger.warning("art gallery: refusing image off %s: %s", self.image_host, url)
            return None
        async with self._pace:
            wait = self._last_image + self.s.image_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            if not self._take_budget(owner):
                raise ScryfallError("rate_limited", "image budget for this hour is spent")
            try:
                resp = await self.http.get(url, headers={"User-Agent": self.scryfall.headers["User-Agent"]})
            except httpx.HTTPError as exc:
                raise ScryfallError(
                    "unavailable", f"image host unreachable: {exc.__class__.__name__}"
                ) from exc
            finally:
                self._last_image = time.monotonic()
                self.images_fetched += 1
        if resp.status_code == 429:
            raise ScryfallError("rate_limited", "image host asked us to slow down")
        if resp.status_code != 200 or len(resp.content) > MAX_IMAGE_BYTES:
            logger.info("art gallery: skipping %s (%s, %d bytes)", url, resp.status_code, len(resp.content))
            return None
        return await asyncio.to_thread(_hash_bytes, resp.content)


def _hash_bytes(data: bytes) -> bytes | None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(data), formats=("JPEG",)) as im:
            if im.width > 2000 or im.height > 2000:  # the header is read; nothing is decoded yet
                return None
            im.draft("RGB", (SIDE * 8, SIDE * 8))  # JPEG decodes at a reduced scale, still above 17x16
            im.load()
            return gallery_row(im)
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def _small_image(card: dict[str, Any]) -> str | None:
    faces = card.get("card_faces") or []
    uris = card.get("image_uris") or (faces[0].get("image_uris") if faces else None) or {}
    url = uris.get("small")
    return url if isinstance(url, str) else None


def _text_label(card: dict[str, Any]) -> str:
    return f"{str(card.get('set') or '').upper()} {card.get('collector_number') or ''}".strip()


def _label(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "scryfall_id": card.get("id"),
        "set": card.get("set"),
        "set_name": card.get("set_name"),
        "collector_number": card.get("collector_number"),
        "released_at": card.get("released_at"),
    }
