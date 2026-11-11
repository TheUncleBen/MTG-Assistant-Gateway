"""Finding a card by its picture: an index of every printing's artwork, kept on the gateway, and
the search the scanner asks when it sees a card (0.7.47, checklist v39 T-218).

The scanner used to find a card only by reading its title with text recognition; a foil's glare,
a blurred shot or a showcase frame lost the card. Now the page sends the straightened card (a
small JPEG) and the gateway compares its artwork with the artwork of every printing it knows.

How the artwork is compared. The art box of the card (ART_BOX, a share of the card's width and
height) goes through a small image model, MediaPipe's MobileNet V3 small image embedder
(Apache-2.0, ``model/mobilenet_v3_small.tflite``, fetched pinned and checksummed at image build
time by scripts/fetch_ocr_assets.py), run with Google's LiteRT interpreter (``ai-edge-litert``).
It turns a picture into 1,024 numbers; two pictures of the same artwork give nearly the same
numbers (cosine similarity), whatever the light, tilt or blur. Measured on 8,474 printings with
300 synthetic phone photos (research/2026-11-09-scanner-rebuild): 99.0% found the right card
first, against 94.7% for the older art fingerprint (art.py).

The index. One row per printing in its own SQLite file beside the database (``INDEX_FILE``):
the printing's ids, set and number and its 1,024 numbers as int8 (1 KB a printing, about 120 MB
for every printing). The gateway's memory limit is 256 MB by default, so the whole index is
never held in memory: a 128-number summary of each row (its PCA projection, int8: about 15 MB
for every printing) picks the CANDIDATES closest rows, and only those are read back from disk
and compared in full. On the same test that two-step search found exactly what the full search
found. The PCA is worked out from the index itself (FIT_MIN rows or more) and again whenever the
index has doubled since.

How the index is built. From the gateway's local copy of every printing (bulk.py, refreshed
daily from Scryfall's bulk data), every paper printing with a ``small`` picture (146 x 204,
about 15 KB) is fetched from cards.scryfall.io once, gently (IMAGE_INTERVAL between fetches,
WORKERS at a time), turned into its numbers and kept. The first build is the long one, about
100,000 pictures; after that only new printings are fetched. It runs in the background, survives
restarts (what is in the file is kept) and stops while the scanner's artwork setting
(``scan_art_enabled``, MTG_SCAN_ART_ENABLED) or the local printings copy is off. Until the index
is ready, and wherever the model can't run, the scanner reads the card's title as before.

Deciding. The best match is "sure" when it beats the best match of any *other* card by
SURE_MARGIN or more (on the test, 100% right at 91 to 96% of the photos); otherwise the page
asks. Printings that share one artwork (reprints: about half of all printings) can't be told
apart by artwork, so the answer lists them (``same_art``) and the page reads the card's
set and number line to choose among them, or asks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MODEL_FILE = Path(__file__).parent / "model" / "mobilenet_v3_small.tflite"
INDEX_FILE = "scan-pictures.sqlite"
SIDE = 224  # the model's input, square
DIM = 1024  # numbers per picture
COARSE = 128  # numbers per row kept in memory for the first step of a search
CANDIDATES = 200  # rows compared in full
FIT_MIN = 3000  # rows needed before the first PCA; until then every row is compared in full
FULL_SCAN_MAX = 20000  # with no PCA yet, a full comparison of up to this many rows is cheap
CHUNK = 5000  # rows read or multiplied at a time (bounded memory)
# The art box as shares of a straightened card's width and height: the picture on a modern card,
# and still mostly picture on older, showcase and full-art frames (the tested crop).
ART_BOX = (0.075, 0.115, 0.925, 0.56)
SURE_MARGIN = 0.06  # best card's similarity over the best other card's: "sure" from here
MIN_SIMILARITY = 0.45  # below this the best match is no match at all (an empty table, a hand)
MAX_UPLOAD = 400_000  # bytes of the picture the page sends (a 488 x 680 JPEG is about 60 KB)
MAX_IMAGE_BYTES = 400_000  # a ``small`` card picture is about 15 KB; anything near this is not one
IMAGE_HOST = "https://cards.scryfall.io/"
IMAGE_INTERVAL = 0.12  # seconds between picture fetches, gateway-wide: about 8 a second
WORKERS = 4  # pictures fetched and turned into numbers at a time
BATCH = 100  # rows written at a time
PASS = 2000  # printings taken from the copy per pass of a round
CHECK_EVERY = 3600.0  # how often the background loop looks for new printings
START_DELAY = 120.0  # after a start, out of the start's way (and after the printings copy's look)
SLOW_DOWN = object()  # _read_capped: the image host answered 429
LATER = b""  # _fetch_and_embed: the host said slow down; the printing is tried again, not skipped
COMPLETE_SHARE = 0.95  # of the copy's printings in the index before the scanner uses it
RETRY_AFTER_ERROR = 600.0  # a failed picture is tried again on a later round, not at once
PAUSE_ON_429 = 300.0  # cards.scryfall.io said slow down: every fetch waits this long

SCHEMA = """
CREATE TABLE IF NOT EXISTS vecs (
    id TEXT PRIMARY KEY,
    oracle_id TEXT NOT NULL,
    illustration_id TEXT NOT NULL DEFAULT '',
    set_code TEXT NOT NULL,
    cn TEXT NOT NULL,
    vec BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS vecs_art ON vecs (illustration_id);
CREATE TABLE IF NOT EXISTS skipped (id TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value BLOB);
"""


def _trim() -> None:
    """Hand freed memory back to the system after a large read (glibc keeps it otherwise: a full
    load of 100,000 rows left about 75 MB held on to without this, measured). No-op elsewhere."""
    with contextlib.suppress(Exception):
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)


def runtime_available() -> bool:
    """The model file is there and the LiteRT interpreter can be imported."""
    if not MODEL_FILE.is_file():
        return False
    try:
        import ai_edge_litert.interpreter  # noqa: F401
        import numpy  # noqa: F401
    except Exception:  # an ImportError, or a wheel built for another CPU
        return False
    return True


def art_crop(image: Any) -> Any:
    """The art box of a straightened, portrait card picture (a Pillow image)."""
    w, h = image.size
    x0, y0, x1, y1 = ART_BOX
    return image.crop((round(w * x0), round(h * y0), round(w * x1), round(h * y1)))


class Embedder:
    """The image model: a picture in, DIM numbers out (unit length). One interpreter, used by one
    thread at a time; it is loaded on first use (about 20 MB)."""

    def __init__(self, model: Path = MODEL_FILE, threads: int = 2):
        self.model = model
        self.threads = threads
        self._lock = threading.Lock()
        self._it: Any = None

    def _interpreter(self) -> Any:
        if self._it is None:
            from ai_edge_litert.interpreter import Interpreter

            it = Interpreter(model_path=str(self.model), num_threads=self.threads)
            it.allocate_tensors()
            self._in = it.get_input_details()[0]["index"]
            self._out = it.get_output_details()[0]["index"]
            self._it = it
        return self._it

    def embed(self, image: Any) -> Any:
        """Numbers for a picture already cropped to what is compared (a Pillow image)."""
        import numpy as np
        from PIL import Image

        rgb = image.convert("RGB").resize((SIDE, SIDE), Image.Resampling.BILINEAR)
        # the model takes values from 0 to 1 (checked: -1 to 1 found the right card 73% of the time)
        a = np.asarray(rgb, dtype=np.float32)[None] / 255.0
        with self._lock:
            it = self._interpreter()
            it.set_tensor(self._in, a)
            it.invoke()
            v = it.get_tensor(self._out)[0].astype(np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    def embed_card(self, image: Any) -> Any:
        return self.embed(art_crop(image))


def to_int8(v: Any) -> bytes:
    """Unit-length numbers as int8 bytes, scaled so the largest is 127 (the scale doesn't matter:
    rows are compared by angle)."""
    import numpy as np

    peak = float(np.abs(v).max()) or 1.0
    return np.round(v * (127.0 / peak)).astype(np.int8).tobytes()


def _rows_int8(blobs: list[bytes]) -> Any:
    import numpy as np

    return np.frombuffer(b"".join(blobs), dtype=np.int8).reshape(len(blobs), -1)


def decode_picture(data: bytes, max_side: int = 2000) -> Any | None:
    """A JPEG or PNG the page sent, opened safely (size checked from the header before decoding);
    None when it is not a picture."""
    from PIL import Image, UnidentifiedImageError

    try:
        im = Image.open(BytesIO(data), formats=("JPEG", "PNG", "WEBP"))
        if im.width < 40 or im.height < 40 or im.width > max_side or im.height > max_side:
            return None
        im.load()
        return im.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None


KEEP_SIDE = 1000  # longest side of a kept missed picture (the page sends 488 x 680)


def as_plain_jpeg(im: Any, *, max_side: int = KEEP_SIDE, max_bytes: int = MAX_UPLOAD) -> bytes:
    """A decoded picture saved again as a plain JPEG: no EXIF, GPS or other metadata from the
    upload is kept, and what is stored is always the JPEG it is served as. It is never larger than
    an upload may be: shrunk to ``max_side`` and saved at a lower quality until it fits (a small
    PNG of a fine pattern can turn into a JPEG of 2 MB)."""
    im = im.copy()
    side = max_side
    while True:
        im.thumbnail((side, side))
        for quality in (85, 70, 55, 40, 25):
            out = BytesIO()
            im.save(out, "JPEG", quality=quality)
            if out.tell() <= max_bytes or side <= 64:
                return out.getvalue()
        side //= 2


class PictureIndex:
    """The index file, its in-memory summary, the search and the background build."""

    def __init__(
        self,
        folder: Path,
        copy: Any,
        *,
        enabled: Callable[[], bool] | None = None,
        user_agent: str = "mtg-assistant-gateway",
        embedder: Embedder | None = None,
        image_http: httpx.AsyncClient | None = None,
        image_host: str = IMAGE_HOST,
        image_interval: float = IMAGE_INTERVAL,
    ):
        self.path = Path(folder) / INDEX_FILE
        self.copy = copy
        self._enabled = enabled or (lambda: True)
        self.user_agent = user_agent
        self.embedder = embedder or Embedder()
        self._own_model = embedder is None  # a given embedder (the tests') needs no model file
        self._own_http = image_http is None
        self._http = image_http
        self.image_host = image_host
        self.image_interval = image_interval
        self._db_lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._mem_lock = threading.Lock()
        # the in-memory summary: rowids (int64), and their COARSE numbers (int8) once a PCA exists
        self._rowids: Any = None
        self._coarse: Any = None
        self._mean: Any = None
        self._comps: Any = None
        self._fitted = 0
        self._count = 0
        self._loaded = False
        self._complete = False  # a round has added every printing the copy had (kept in meta)
        self._pace = asyncio.Lock()
        self._last_fetch = 0.0
        self._paused_until = 0.0
        self._halt = threading.Event()
        self.status: dict[str, Any] = {"state": "starting", "have": 0, "want": None, "failed": 0, "error": ""}

    # -- switches -----------------------------------------------------------
    @property
    def enabled(self) -> bool:
        try:
            on = bool(self._enabled())
        except Exception:
            on = False
        return on and (not self._own_model or runtime_available())

    def ready(self) -> bool:
        """Searching is worth it: a round has added every printing once (until then the scanner
        reads titles, as before 0.7.47: a half-built index would miss the cards not in it yet).
        Printings added later (a new set) come in on later rounds while the index stays in use."""
        return self.enabled and self._count > 0 and self._complete

    # -- storage ------------------------------------------------------------
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)
            self._conn = conn
        return self._conn

    def close(self) -> None:
        self._halt.set()
        with self._db_lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    async def aclose(self) -> None:
        self.close()
        if self._own_http and self._http is not None:
            await self._http.aclose()

    def _meta_get(self, key: str) -> bytes | None:
        row = self._db().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _meta_put(self, key: str, value: bytes | str) -> None:
        self._db().execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def count(self) -> int:
        with self._db_lock:
            return int(self._db().execute("SELECT COUNT(*) FROM vecs").fetchone()[0])

    def have_ids(self) -> set[str]:
        with self._db_lock:
            return {r[0] for r in self._db().execute("SELECT id FROM vecs")}

    def skipped_ids(self, since: float) -> set[str]:
        with self._db_lock:
            return {r[0] for r in self._db().execute("SELECT id FROM skipped WHERE at > ?", (since,))}

    def add_rows(self, rows: list[tuple[str, str, str, str, str, bytes]]) -> None:
        """Keep finished rows (id, oracle_id, illustration_id, set, number, int8 numbers) and add
        them to the in-memory summary."""
        if not rows:
            return
        with self._db_lock:
            db = self._db()
            db.execute("BEGIN")
            try:
                db.executemany("INSERT OR REPLACE INTO vecs VALUES (?,?,?,?,?,?)", rows)
                db.executemany("DELETE FROM skipped WHERE id = ?", [(r[0],) for r in rows])
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            ids = [r[0] for r in rows]
            marks = ",".join("?" * len(ids))
            got = db.execute(f"SELECT rowid, vec FROM vecs WHERE id IN ({marks})", ids).fetchall()  # noqa: S608
        self._append(got)

    def mark_skipped(self, ids: list[str]) -> None:
        if not ids:
            return
        now = time.time()
        with self._db_lock:
            self._db().executemany("INSERT OR REPLACE INTO skipped VALUES (?, ?)", [(i, now) for i in ids])

    def remove(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._db_lock:
            db = self._db()
            for i in range(0, len(ids), 500):
                part = ids[i : i + 500]
                db.execute(f"DELETE FROM vecs WHERE id IN ({','.join('?' * len(part))})", part)  # noqa: S608
        self.load()

    # -- the in-memory summary ----------------------------------------------
    def _project(self, full: Any) -> Any:
        """Rows of DIM numbers (float32) to their COARSE summary, unit length."""
        import numpy as np

        p = (full - self._mean) @ self._comps.T
        n = np.linalg.norm(p, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return p / n

    def _chunks(self) -> Iterator[tuple[Any, Any]]:
        """(rowids, int8 rows) of the whole index, CHUNK rows at a time, in rowid order."""
        import numpy as np

        last = 0
        while True:
            with self._db_lock:
                got = (
                    self._db()
                    .execute(
                        "SELECT rowid, vec FROM vecs WHERE rowid > ? ORDER BY rowid LIMIT ?", (last, CHUNK)
                    )
                    .fetchall()
                )
            if not got:
                return
            last = got[-1][0]
            yield np.array([g[0] for g in got], dtype=np.int64), _rows_int8([g[1] for g in got])

    def load(self) -> None:
        """Read the PCA and build the in-memory summary from the file (at start, and after a refit
        or a removal)."""
        import numpy as np

        with self._db_lock:
            mean = self._meta_get("pca_mean")
            comps = self._meta_get("pca_comps")
            fitted = self._meta_get("pca_rows")
            complete = self._meta_get("complete") is not None
        with self._mem_lock:
            if mean and comps:
                self._mean = np.frombuffer(mean, dtype=np.float32).copy()
                self._comps = np.frombuffer(comps, dtype=np.float32).reshape(COARSE, DIM).copy()
                self._fitted = int(fitted or 0)
            else:
                self._mean = self._comps = None
                self._fitted = 0
        rowids: list[Any] = []
        coarse: list[Any] = []
        for ids, rows in self._chunks():
            rowids.append(ids)
            if self._comps is not None:
                coarse.append(self._coarse_int8(rows))
        with self._mem_lock:
            self._rowids = np.concatenate(rowids) if rowids else np.zeros(0, dtype=np.int64)
            self._coarse = np.concatenate(coarse) if coarse else None
            self._count = len(self._rowids)
            self._complete = complete
            self._loaded = True
        self.status["have"] = self._count
        _trim()

    def _coarse_int8(self, rows: Any) -> Any:
        import numpy as np

        p = self._project(rows.astype(np.float32))
        return np.round(p * 127.0).astype(np.int8)

    def _append(self, got: list[tuple[int, bytes]]) -> None:
        import numpy as np

        if not got:
            return
        ids = np.array([g[0] for g in got], dtype=np.int64)
        with self._mem_lock:
            if self._rowids is None:
                self._rowids = np.zeros(0, dtype=np.int64)
            keep = ~np.isin(ids, self._rowids)
            ids = ids[keep]
            if not len(ids):
                return
            rows = _rows_int8([g[1] for g, k in zip(got, keep, strict=True) if k])
            self._rowids = np.concatenate([self._rowids, ids])
            if self._comps is not None and self._coarse is not None:
                self._coarse = np.concatenate([self._coarse, self._coarse_int8(rows)])
            self._count = len(self._rowids)
        self.status["have"] = self._count
        if self._count >= FIT_MIN and (self._comps is None or self._count >= 2 * self._fitted):
            self.fit()

    def fit(self) -> None:
        """Work out the PCA from every row (a running sum and a 1,024 x 1,024 covariance, so memory
        stays small), keep it in the file and rebuild the summary."""
        import numpy as np

        n = 0
        total = np.zeros(DIM, dtype=np.float64)
        outer = np.zeros((DIM, DIM), dtype=np.float64)
        for _ids, rows in self._chunks():
            x = rows.astype(np.float32)
            x /= np.linalg.norm(x, axis=1, keepdims=True) + 1e-9
            total += x.sum(0)
            outer += x.T @ x  # float32 product (4 MB), summed in float64
            n += len(x)
        if n < 2:
            return
        mean = total / n
        cov = outer / n - np.outer(mean, mean)
        vals, vecs = np.linalg.eigh(cov)
        comps = vecs[:, np.argsort(vals)[::-1][:COARSE]].T.astype(np.float32)
        with self._db_lock:
            self._meta_put("pca_mean", mean.astype(np.float32).tobytes())
            self._meta_put("pca_comps", comps.tobytes())
            self._meta_put("pca_rows", str(n))
        self.load()

    # -- search -------------------------------------------------------------
    def search(self, query: Any, k: int = 12) -> list[dict[str, Any]]:
        """The ``k`` closest printings to the query's numbers, best first: id, oracle_id,
        illustration_id, set, collector_number and similarity (cosine, -1 to 1)."""
        import numpy as np

        with self._mem_lock:
            rowids, coarse, n = self._rowids, self._coarse, self._count
            mean, comps = self._mean, self._comps
        if not n or rowids is None:
            return []
        q = np.asarray(query, dtype=np.float32)
        if coarse is not None and comps is not None and len(coarse) == n:
            qc = (q - mean) @ comps.T
            qc /= float(np.linalg.norm(qc)) or 1.0
            scores = np.empty(n, dtype=np.float32)
            for i in range(0, n, CHUNK * 4):
                scores[i : i + CHUNK * 4] = coarse[i : i + CHUNK * 4].astype(np.float32) @ qc
            take = min(CANDIDATES, n)
            pick = rowids[np.argpartition(-scores, take - 1)[:take]]
            with self._db_lock:
                marks = ",".join("?" * len(pick))
                got = (
                    self._db()
                    .execute(
                        "SELECT id, oracle_id, illustration_id, set_code, cn, vec FROM vecs "
                        f"WHERE rowid IN ({marks})",  # noqa: S608
                        [int(r) for r in pick],
                    )
                    .fetchall()
                )
        elif n <= FULL_SCAN_MAX:
            with self._db_lock:
                got = (
                    self._db()
                    .execute("SELECT id, oracle_id, illustration_id, set_code, cn, vec FROM vecs")
                    .fetchall()
                )
        else:  # a large index whose PCA is being worked out: the next search will have it
            return []
        if not got:
            return []
        full = _rows_int8([g[5] for g in got]).astype(np.float32)
        full /= np.linalg.norm(full, axis=1, keepdims=True) + 1e-9
        sims = full @ q
        order = np.argsort(-sims)[:k]
        return [
            {
                "id": got[i][0],
                "oracle_id": got[i][1],
                "illustration_id": got[i][2],
                "set": got[i][3],
                "collector_number": got[i][4],
                "similarity": round(float(sims[i]), 4),
            }
            for i in order
        ]

    def same_art(self, illustration_id: str, limit: int = 60) -> list[dict[str, str]]:
        """Every printing in the index that carries this artwork."""
        if not illustration_id:
            return []
        with self._db_lock:
            got = (
                self._db()
                .execute(
                    "SELECT id, oracle_id, set_code, cn FROM vecs WHERE illustration_id = ? LIMIT ?",
                    (illustration_id, limit),
                )
                .fetchall()
            )
        return [{"id": g[0], "oracle_id": g[1], "set": g[2], "collector_number": g[3]} for g in got]

    def identify(self, picture: Any) -> dict[str, Any]:
        """What the card in a straightened picture is: ``best`` (the closest printing), ``sure``,
        ``margin`` over the best other card, ``others`` (the closest other cards, best first) and
        ``same_art`` (every printing sharing the best one's artwork). Runs in a worker thread."""
        q = self.embedder.embed_card(picture)
        hits = self.search(q, k=40)
        if not hits or hits[0]["similarity"] < MIN_SIMILARITY:
            return {"best": None, "sure": False, "margin": 0.0, "others": [], "same_art": []}
        best = hits[0]
        others: list[dict[str, Any]] = []
        seen = {best["oracle_id"]}
        for h in hits[1:]:
            if h["oracle_id"] not in seen:
                seen.add(h["oracle_id"])
                others.append(h)
        rival = others[0]["similarity"] if others else -1.0
        margin = round(best["similarity"] - rival, 4)
        art = self.same_art(best["illustration_id"]) if best["illustration_id"] else []
        if not any(a["id"] == best["id"] for a in art):
            art.insert(0, {k: best[k] for k in ("id", "oracle_id", "set", "collector_number")})
        return {
            "best": best,
            "sure": margin >= SURE_MARGIN,
            "margin": margin,
            "others": others[:3],
            "same_art": art,
        }

    # -- the background build -------------------------------------------------
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=20.0, follow_redirects=False)
        return self._http

    async def loop(self) -> None:
        """Runs for the gateway's life: load what is kept, then look for printings to add."""
        await asyncio.sleep(START_DELAY)
        while not self._halt.is_set():
            try:
                await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a failed round never stops the loop
                logger.exception("card picture index: round failed")
                self.status.update(state="error", error=str(exc)[:200])
            await asyncio.sleep(CHECK_EVERY)

    def _todo(self, have: set[str], skip: set[str], limit: int) -> tuple[list[tuple[str, ...]], set[str]]:
        """Read the copy once: up to ``limit`` printings to add, and the ids of every printing it
        has (to drop what it no longer has). Blocking."""
        wanted: list[tuple[str, ...]] = []
        seen: set[str] = set()
        for row in self.copy.picture_rows():
            seen.add(row[0])
            if len(wanted) < limit and row[0] not in have and row[0] not in skip:
                wanted.append(row)
        return wanted, seen

    async def sync(self) -> int:
        """One round: add every printing the copy has and the index doesn't, PASS printings at a
        time (so only a pass is ever held in memory); drop printings the copy no longer has.
        Returns how many rows were added."""
        if not self.enabled:
            self.status.update(state="off")
            return 0
        if not self._loaded:
            await asyncio.to_thread(self.load)
        if not getattr(self.copy, "enabled", False) or self.copy.meta().get("count") is None:
            self.status.update(state="waiting")
            return 0
        added = 0
        first = True
        while not self._halt.is_set() and self.enabled:
            have = await asyncio.to_thread(self.have_ids)
            skip = await asyncio.to_thread(self.skipped_ids, time.time() - 24 * 3600)
            wanted, seen = await asyncio.to_thread(self._todo, have, skip, PASS)
            if first:
                first = False
                gone = [i for i in have if i not in seen]
                if gone and len(seen) > 0.5 * max(1, len(have)):  # never empty the index on a short copy
                    await asyncio.to_thread(self.remove, gone)
            self.status.update(want=len(seen), have=self._count)
            if not wanted:
                # every printing tried once; pictures that failed are tried again a day later, and
                # the index counts as complete only when it holds nearly all (an outage of the
                # image host leaves it in waiting, never half used)
                if self._count >= COMPLETE_SHARE * len(seen) and not self._complete:
                    with self._db_lock:
                        self._meta_put("complete", "1")
                    self._complete = True
                break
            self.status.update(state="building", error="")
            got = await self._run(wanted)
            added += got
            del wanted, seen, have
            if got == 0 and self._paused_until <= time.monotonic():
                break  # nothing could be fetched this pass: try again next round
        self.status.update(state="ready" if self._count else "waiting", have=self._count)
        return added

    async def _run(self, wanted: list[tuple[str, ...]]) -> int:
        """Fetch, turn into numbers and keep one pass of printings, WORKERS at a time."""
        added = 0
        queue: asyncio.Queue[tuple[str, ...]] = asyncio.Queue()
        for w in wanted:
            queue.put_nowait(w)
        done: list[tuple[str, str, str, str, str, bytes]] = []
        failed: list[str] = []
        flush = asyncio.Lock()

        async def save(force: bool = False) -> None:
            nonlocal added
            async with flush:
                if done and (force or len(done) >= BATCH):
                    batch = done[:]
                    done.clear()
                    await asyncio.to_thread(self.add_rows, batch)
                    added += len(batch)
                if failed and (force or len(failed) >= BATCH):
                    batch_f = failed[:]
                    failed.clear()
                    await asyncio.to_thread(self.mark_skipped, batch_f)
                    self.status["failed"] = self.status.get("failed", 0) + len(batch_f)

        async def worker() -> None:
            while not self._halt.is_set() and self.enabled:
                try:
                    pid, oid, art, set_code, cn, url = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                vec = await self._fetch_and_embed(url)
                if vec == LATER:
                    continue  # the next pass takes it, once the pause is over
                if vec is None:
                    failed.append(pid)
                else:
                    done.append((pid, oid, art, set_code, cn, vec))
                await save()

        await asyncio.gather(*(worker() for _ in range(WORKERS)))
        await save(force=True)
        return added

    async def _fetch_and_embed(self, url: str) -> bytes | None:
        if not url.startswith(self.image_host):
            return None
        async with self._pace:
            while (
                wait := max(self._paused_until, self._last_fetch + self.image_interval) - time.monotonic()
            ) > 0:
                await asyncio.sleep(min(wait, 5.0))
            self._last_fetch = time.monotonic()
        try:
            data = await self._read_capped(url)
        except httpx.HTTPError:
            return None
        if data is SLOW_DOWN:
            self._paused_until = time.monotonic() + PAUSE_ON_429
            return LATER
        if not isinstance(data, bytes):
            return None
        return await asyncio.to_thread(self._embed_bytes, data)

    async def _read_capped(self, url: str) -> bytes | object | None:
        """The picture's bytes, read as they arrive and given up on as soon as they pass
        MAX_IMAGE_BYTES; SLOW_DOWN on a 429; None for any other answer."""
        async with self._client().stream("GET", url, headers={"User-Agent": self.user_agent}) as resp:
            if resp.status_code == 429:
                return SLOW_DOWN
            if resp.status_code != 200:
                return None
            got = bytearray()
            async for chunk in resp.aiter_bytes():
                got += chunk
                if len(got) > MAX_IMAGE_BYTES:
                    return None
            return bytes(got)

    def _embed_bytes(self, data: bytes) -> bytes | None:
        im = decode_picture(data)
        if im is None:
            return None
        with contextlib.suppress(Exception):
            return to_int8(self.embedder.embed_card(im))
        return None
