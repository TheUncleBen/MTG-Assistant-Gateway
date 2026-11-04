"""A local copy of every card printing, from Scryfall's own bulk data, for when Scryfall's API
can't be reached (10g of the 0.7.46 audit).

Once a day the gateway reads Scryfall's bulk-data index (``GET /bulk-data``) and, when its
``default_cards`` file (every printing, in English or else its printed language; a gzipped JSON
Lines file of about 80 MB, 120,000 cards on 2026-11-04) is newer than the copy, downloads the file
at the index's ``jsonl_download_uri`` with the client's own User-Agent and Accept headers. Scryfall
says its bulk data is collected once every 12 to 24 hours; the gateway downloads it at most once a
day (COPY_TTL). A failed look waits RETRY_AFTER_FAILURE, a file that came down damaged waits a day
(RETRY_AFTER_BAD_FILE), and that wait is kept in a small file beside the copy (``.next``), so a
restart never downloads the file again before it is due. The file is read line by line (``gzip.open``),
never whole, in a worker thread, and cut down to what the printings answers show (``browse_row``,
the printing picker's row, which has everything ``card_printings`` and ``printings_of`` return
too). Each row is kept zlib-compressed with a dictionary taken from the file's own first rows, so
the copy takes about 55 MB on disk, beside the database in the data folder (up to about 172 MB
in all while a new one is built: the download, the new copy and the old one; measured in the
0.7.46 audit with a full-size file).

Why a separate SQLite file and not a table of the database: the copy is replaced whole every day.
As a table, that is a 55 MB write holding the database's write lock (every member's write waits
behind it), and every nightly backup and upgrade copy would carry 55 MB that Scryfall can give
again. As a file of its own it is built under another name and moved into place with one rename
(``os.replace``): a failed, cut-off or damaged download never touches the copy in use, which stays
until a good one replaces it. Readers open it read-only and see either the old file or the new.

The copy is used only when a live printings request fails because Scryfall can't be reached, is
rate-limiting the gateway, or answers 5xx (ScanService.printings). The answer then says how old it
is: ``as_of`` (when Scryfall made the file, an ISO 8601 date-time the page writes in the viewer's
own way), ``from_local_copy``, and a plain line, ``as_of_note`` (as_of_line), such as "As of 3 Nov
2026 · Scryfall can't be reached", the same words for a 429, a 5xx and no answer at all; the tools
add "Prices are from that day, not today." (prices_line). A copy that can't be read is said once
per file in the log. Its prices are that day's. The daily price check (prices.py) never uses the
copy: it records today's prices, and a day-old price recorded as today's would be wrong; a card it
can't price waits for the next check.

The admin Settings row "Keep a local copy of card printings" (``printings_copy``, on by default,
no stack variable) turns it off: the daily download stops, the copy and every file it left are
deleted at once, and printings answers fail as they did before there was a copy. On again, the
next hourly check downloads a new copy.

The download goes through the scan service's Scryfall client (its address and HTTP client), the
same way the card-name catalog's does (names.py, through ``NameCatalog._scryfall``). The tests
replace that client with one answering from recorded fixtures (tests/conftest.py, ``_offline``),
which has no bulk data unless a test gives it some, so no test downloads anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
import os
import sqlite3
import threading
import time
import zlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .names import fold
from .scryfall import PRINT_ORDERS, PRINT_PAGES, ScryfallError, browse_row

logger = logging.getLogger(__name__)

COPY_FILE = "scryfall-printings.sqlite"
BULK_TYPE = "default_cards"
COPY_TTL = 24 * 3600  # the copy is downloaded again once it is a day old, never sooner
# any HTTP or transport failure (the index or the file: an error answer, a dropped or cut-off
# transfer), or an index with nothing newer: the next look waits this long
RETRY_AFTER_FAILURE = 6 * 3600
# a file received whole and then found damaged: the next download waits a day, so a file that
# keeps failing costs at most one 80 MB download a day
RETRY_AFTER_BAD_FILE = COPY_TTL
SETTING = "printings_copy"  # the admin Settings row "Keep a local copy of card printings"
START_DELAY = 60.0  # the first look after a start waits this long, out of the start's way
CHECK_EVERY = 3600.0  # how often the background loop asks whether the copy is due
MIN_PRINTINGS = 50_000  # fewer printings than this is a damaged file, never a copy (120,024 today)
PAGE = 175  # Scryfall's page of printings
BATCH = 2000  # rows written at a time
ZDICT_ROWS = 200  # the first rows of the file make the compression dictionary
ZDICT_MAX = 32 * 1024
CHUNK = 1 << 20  # downloaded bytes written to disk a megabyte at a time, in a worker thread
# hosts the index may send the download to (besides the client's own, a test's fake)
DOWNLOAD_HOSTS = ("data.scryfall.io", "api.scryfall.com")
PRICE_KEYS = ("usd", "usd_foil", "usd_etched", "eur", "eur_foil", "tix")
# printings that are no card to look a name up by (an art card is "Name // Name" with its own id)
NAMELESS_LAYOUTS = ("art_series",)

SCHEMA = """
CREATE TABLE printings (
    oracle_id TEXT NOT NULL,
    released TEXT NOT NULL,
    usd REAL,
    finishes TEXT NOT NULL,
    frame TEXT NOT NULL,
    border TEXT NOT NULL,
    set_code TEXT NOT NULL,
    cn TEXT NOT NULL,
    row BLOB NOT NULL
);
CREATE TABLE names (
    name TEXT PRIMARY KEY, oracle_id TEXT NOT NULL, card_name TEXT NOT NULL, whole INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE meta (key TEXT PRIMARY KEY, value BLOB) WITHOUT ROWID;
"""
INDEXES = "CREATE INDEX printings_oracle ON printings (oracle_id, released);"


class CopyBroken(Exception):
    """The downloaded file is cut off or damaged: the copy in use is kept."""


class Stopped(Exception):
    """The copy was turned off, or the gateway is shutting down, while it was downloaded or built:
    nothing is wrong, so no warning and no wait before the next try."""


def _iso(value: Any) -> str | None:
    """Scryfall's ``updated_at`` as an ISO 8601 date-time in UTC (``2026-11-03T22:06:49+00:00``),
    the ``as_of`` the answers carry and the page formats in the viewer's own way; None when there
    is none or it can't be read."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).astimezone(UTC).isoformat(timespec="seconds")
    except ValueError:
        return None


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _day(as_of: str | None) -> str | None:
    """The copy's day in English whatever the locale ("3 Nov 2026"), None when it can't be read."""
    try:
        day = datetime.fromisoformat(str(as_of)).astimezone(UTC)
    except ValueError:
        return None
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def as_of_line(as_of: str | None) -> str:
    """The one line that says an answer came from the copy, for the tools and the pages alike
    (``as_of_note``): "As of 3 Nov 2026 · Scryfall can't be reached", or the copy itself when its
    day can't be read. Never "and prices": the pages show no prices from the copy."""
    day = _day(as_of)
    where = f"As of {day}" if day else "From the gateway's own copy"
    return f"{where} \u00b7 Scryfall can't be reached"


def prices_line(as_of: str | None) -> str:
    """The tools' own line under as_of_line: the prices in an answer from the copy are its day's."""
    return "Prices are from that day, not today." if _day(as_of) else "Prices are from that copy, not today."


def _oracle(card: dict[str, Any]) -> str | None:
    faces = card.get("card_faces") if isinstance(card.get("card_faces"), list) else []
    oid = card.get("oracle_id") or (
        faces[0].get("oracle_id") if faces and isinstance(faces[0], dict) else None
    )
    return oid.lower() if isinstance(oid, str) and oid else None


def _cards(gz_path: Path, stop: threading.Event) -> Any:
    """Every card object of the gzipped JSON Lines file, one line at a time. A cut-off or damaged
    file raises CopyBroken."""
    try:
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if stop.is_set():
                    raise Stopped()
                text = line.strip()
                if not text:
                    continue
                try:
                    card = json.loads(text)
                except ValueError as exc:
                    raise CopyBroken(f"line {n} is not JSON") from exc
                if isinstance(card, dict) and card.get("object") == "card":
                    yield card
    except (OSError, EOFError, zlib.error, UnicodeDecodeError) as exc:  # BadGzipFile is an OSError
        raise CopyBroken(f"the file is cut off or damaged ({exc.__class__.__name__})") from exc


def _row(card: dict[str, Any]) -> dict[str, Any]:
    """What the copy keeps of a printing: the picker's row (``browse_row``, everything
    ``prints`` and ``card_printings`` show), and under ``_prices`` Scryfall's prices as it gives
    them (``printings_of`` hands them on as they are)."""
    row = browse_row(card)
    row["_prices"] = card.get("prices") if isinstance(card.get("prices"), dict) else {}
    return row


def build(
    gz_path: Path,
    out: Path,
    meta: dict[str, str],
    *,
    stop: threading.Event | None = None,
    min_printings: int = MIN_PRINTINGS,
) -> int:
    """Build the copy from the downloaded file ``gz_path`` into ``out``, replacing it only once the
    whole file was read and checked. Runs in a worker thread. Returns how many printings it holds;
    raises CopyBroken (the old copy kept) for a file cut off, damaged or too short."""
    stop = stop or threading.Event()
    tmp = out.with_name(out.name + ".new")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    count = 0
    try:
        conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
        # only the trimmed rows are held, never the whole cards (a card is up to 20 KB)
        rows = ((oid, _row(card)) for card in _cards(gz_path, stop) if (oid := _oracle(card)) is not None)
        first = [r for _n, r in zip(range(ZDICT_ROWS), rows, strict=False)]
        zdict = "".join(json.dumps(r, separators=(",", ":")) for _o, r in first).encode()[-ZDICT_MAX:]
        primed = zlib.compressobj(6, zdict=zdict)  # copied for each row: priming costs more

        # Each row is turned into plain values (text, a number, the compressed blob) as soon as it
        # is read, and its dicts are freed at once. Holding a batch of row dicts let thousands of
        # tracked objects outlive the young collections, and that set off full garbage
        # collections, which hold every thread (the event loop too) for as long as the whole
        # gateway's memory takes to walk: 0.1 to 0.3 s each. Tuples of plain values are dropped
        # from the collector's tracking at its first look, so the build hands it nearly nothing.
        values: list[tuple[Any, ...]] = []
        full: list[tuple[str, str, str]] = []
        faces: list[tuple[str, str, str]] = []

        def add(oid: str, row: dict[str, Any]) -> None:
            row["oracle_id"] = oid
            z = primed.copy()
            blob = z.compress(json.dumps(row, separators=(",", ":")).encode()) + z.flush()
            usd = (row.get("prices") or {}).get("usd")
            try:
                price = float(usd) if usd else None
            except ValueError:
                price = None
            values.append(
                (
                    oid,
                    str(row.get("released_at") or ""),
                    price,
                    "," + ",".join(row.get("finishes") or []) + ",",
                    row.get("frame") or "",
                    row.get("border") or "",
                    str(row.get("set") or "").lower(),
                    str(row.get("collector_number") or "").lower(),
                    blob,
                )
            )
            name = str(row.get("name") or "")
            if name and row.get("layout") not in NAMELESS_LAYOUTS:
                full.append((fold(name), oid, name))
                faces.extend((fold(p), oid, name) for p in name.split(" // ") if " // " in name)

        def put() -> int:
            conn.executemany("INSERT INTO printings VALUES (?,?,?,?,?,?,?,?,?)", values)
            # A card's own full name wins over a face of another card's that reads the same
            # (a reversible card is listed as "Name // Name"; "Fire" is a face of "Fire // Ice").
            conn.executemany("INSERT OR IGNORE INTO names VALUES (?,?,?,0)", faces)
            conn.executemany(
                "INSERT INTO names VALUES (?,?,?,1) ON CONFLICT (name) DO UPDATE SET "
                "oracle_id = excluded.oracle_id, card_name = excluded.card_name, whole = 1 "
                "WHERE names.whole = 0",
                full,
            )
            n = len(values)
            values.clear()
            full.clear()
            faces.clear()
            return n

        for oid, row in first:
            add(oid, row)
        del first
        for oid, row in rows:
            add(oid, row)
            if len(values) >= BATCH:
                count += put()
        count += put()
        if count < min_printings:
            raise CopyBroken(f"only {count} printings (at least {min_printings} expected)")
        conn.execute(INDEXES)
        conn.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [*({**meta, "count": str(count)}.items()), ("zdict", zdict)],
        )
        conn.commit()
        conn.close()
        if stop.is_set():  # turned off or shut down while it was built: never put in place
            raise Stopped()
        os.replace(tmp, out)  # the one step that swaps copies: the old one stays until here
    except BaseException:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    return count


class PrintingsCopy:
    """The local copy of every printing (see the module docstring): kept fresh in the background,
    read only when Scryfall can't answer, and gone while the admin Settings row "Keep a local copy
    of card printings" (``printings_copy``) is off."""

    def __init__(self, folder: Path, scryfall: Callable[[], Any], enabled: Callable[[], bool] | None = None):
        # A getter, as NameCatalog's: the scan service's client is replaced in tests.
        self._scryfall = scryfall
        self._enabled = enabled or (lambda: True)
        self.path = Path(folder) / COPY_FILE
        # when the next download may be tried (wall clock), kept beside the copy so a restart
        # waits for it too (N16 of the 0.7.46 audit): after a failure, or an index with nothing newer
        self.next_path = self.path.with_name(COPY_FILE + ".next")
        self.min_printings = MIN_PRINTINGS
        self._next_try = self._read_next_try()
        self._closed = threading.Event()
        self._halt = threading.Event()  # set to stop the build under way (closed or turned off)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._opened: tuple[int, int, int] | None = None
        self._meta: dict[str, Any] = {}
        self._damaged_stamp: tuple[int, int, int] | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        try:
            return bool(self._enabled())
        except Exception:  # noqa: BLE001 - a setting that can't be read: the default, on
            return True

    def _files(self) -> list[Path]:
        """Every file the copy may leave in the data folder."""
        return [
            self.path,
            self.path.with_name(COPY_FILE + ".new"),
            self.path.with_name(COPY_FILE + ".download.gz"),
            self.next_path,
        ]

    # -- the next try, kept across restarts -------------------------------------------------------
    def _read_next_try(self) -> float:
        try:
            return float(json.loads(self.next_path.read_text())["next_try"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0

    def _wait_until(self, when: float, why: str) -> None:
        self._next_try = when
        try:
            tmp = self.next_path.with_name(self.next_path.name + ".tmp")
            tmp.write_text(json.dumps({"next_try": when, "why": why}))
            os.replace(tmp, self.next_path)
        except OSError as exc:  # the wait still holds until the next restart
            logger.warning("could not keep when to download Scryfall's printings next (%s)", exc)

    def _clear_wait(self) -> None:
        self._next_try = 0.0
        with contextlib.suppress(OSError):
            self.next_path.unlink()

    # -- the copy on disk ----------------------------------------------------------------------
    @staticmethod
    def _stamp_of(st: os.stat_result) -> tuple[int, int, int]:
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    def _open(self) -> sqlite3.Connection | None:
        """The copy's read-only connection, opened again when a new copy replaced the file."""
        try:
            stamp = self._stamp_of(self.path.stat())
        except OSError:
            return None
        if self._conn is None or self._opened != stamp:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._meta = {}
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            try:
                self._meta = dict(conn.execute("SELECT key, value FROM meta"))
            except BaseException:
                conn.close()
                raise
            self._conn = conn
            self._opened = stamp
        return self._conn

    def _damaged(self, exc: BaseException) -> None:
        """A copy that can't be read: said once per file (N17), as a WARNING without a traceback.
        The next download replaces it (a copy that can't be read is due at once)."""
        try:
            stamp: tuple[int, int, int] | None = self._stamp_of(self.path.stat())
        except OSError:
            stamp = None
        if stamp != self._damaged_stamp:
            self._damaged_stamp = stamp
            logger.warning(
                "the local copy of Scryfall's printings can't be read (%s: %s); the next download "
                "replaces it",
                exc.__class__.__name__,
                exc,
            )

    def meta(self) -> dict[str, Any]:
        with self._lock:
            try:
                return dict(self._meta) if self._open() is not None else {}
            except (sqlite3.Error, ValueError) as exc:
                self._damaged(exc)
                return {}

    @property
    def as_of(self) -> str | None:
        """When Scryfall made the file the copy was built from, as an ISO date-time, or None."""
        return _iso(self.meta().get("updated_at"))

    def age(self) -> float:
        """Seconds since the copy was downloaded (inf without one)."""
        try:
            return time.time() - float(self.meta().get("downloaded_at") or "")
        except ValueError:
            return float("inf")

    def due(self) -> bool:
        return self.enabled and self.age() >= COPY_TTL and time.time() >= self._next_try

    # -- turning it off and on ---------------------------------------------------------------------
    def remove(self) -> None:
        """Turned off: stop a build under way and delete the copy and every file it left (the
        copy, a half-built ``.new``, a download, the next-try file)."""
        self._halt.set()
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._opened = None
            self._meta = {}
            removed = []
            for f in self._files():
                with contextlib.suppress(FileNotFoundError):
                    f.unlink()
                    removed.append(f.name)
        self._next_try = 0.0
        if removed:
            logger.info("local copy of Scryfall's printings turned off: %s deleted", ", ".join(removed))

    def setting_changed(self, key: str) -> None:
        """LiveSettings.on_change: off deletes the copy at once; on again downloads at the next
        check (no wait left from before)."""
        if key != SETTING:
            return
        if self.enabled:
            self._halt = threading.Event()
            self._clear_wait()
        else:
            self.remove()

    # -- refreshing ----------------------------------------------------------------------------
    async def loop(self) -> None:
        """The daily refresh, in the background: never in a request's way."""
        await asyncio.sleep(START_DELAY)
        while True:
            await self.refresh()
            await asyncio.sleep(CHECK_EVERY)

    async def refresh(self) -> bool:
        """Download and build a new copy when it is due and Scryfall has a newer file. Returns
        whether a new copy is in place. Never raises: a failure keeps the copy and waits
        RETRY_AFTER_FAILURE (RETRY_AFTER_BAD_FILE for a file received whole and found damaged), kept
        across restarts. Turned off, it deletes what is left of the copy instead; turned off while
        it downloads, the transfer stops at its next piece, with no warning and no wait."""
        if not self.enabled:
            if any(f.exists() for f in self._files()):
                await asyncio.to_thread(self.remove)
            return False
        if not self.due():
            return False
        client = self._scryfall()
        if client is None:
            return False
        # this download's own stop: Off (remove) and close set it; On again makes a new one for the
        # next download, so it never restarts this one
        halt = self._halt = threading.Event()
        received = {"whole": False}  # the file came down whole (only then is a failure its content's)
        try:
            entry = await self._index(client)
            if entry["updated_at"] == str(self.meta().get("updated_at") or "-"):
                logger.info("Scryfall's printings file is unchanged since %s", entry["updated_at"])
                self._wait_until(time.time() + RETRY_AFTER_FAILURE, "nothing newer")
                return False
            started = time.monotonic()
            count = await self._download_and_build(client, entry, halt, received)
        except asyncio.CancelledError:
            halt.set()
            raise
        except Stopped:  # turned off (or shutting down) meanwhile: nothing failed
            why = "the gateway is shutting down" if self._closed.is_set() else "the local copy was turned off"
            logger.info("download of Scryfall's printings stopped: %s", why)
            if not self.enabled:
                await asyncio.to_thread(self.remove)
            return False
        except Exception as exc:  # keep the copy in use, try again later
            bad_file = received["whole"] and isinstance(exc, CopyBroken)
            wait = RETRY_AFTER_BAD_FILE if bad_file else RETRY_AFTER_FAILURE
            self._wait_until(time.time() + wait, str(exc)[:200])
            logger.warning("local copy of Scryfall's printings not updated (%s); the old one stays", exc)
            return False
        if not self.enabled or halt.is_set():  # turned off while it was being built
            await asyncio.to_thread(self.remove)
            return False
        self._clear_wait()
        logger.info(
            "local copy of Scryfall's printings updated: %d printings as of %s, %.1f MB, %.0f s",
            count,
            entry["updated_at"],
            self.path.stat().st_size / 1e6,
            time.monotonic() - started,
        )
        return True

    async def _index(self, client: Any) -> dict[str, str]:
        """``default_cards`` in Scryfall's bulk-data index: its ``updated_at`` and download address."""
        if client.retry_in() > 0:
            raise ScryfallError("rate_limited", "Scryfall asked the gateway to slow down")
        try:
            resp = await client.http.get(f"{client.base}/bulk-data", headers=client.headers)
        except httpx.HTTPError as exc:
            raise ScryfallError("unavailable", f"bulk-data index: {exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            raise ScryfallError("unavailable", f"bulk-data index answered {resp.status_code}")
        try:
            rows = resp.json().get("data")
        except (ValueError, AttributeError) as exc:
            raise ScryfallError("contract", "bulk-data index is not JSON") from exc
        entry = next((r for r in rows or [] if isinstance(r, dict) and r.get("type") == BULK_TYPE), None)
        if entry is None:
            raise ScryfallError("contract", f"bulk-data index has no {BULK_TYPE}")
        uri = entry.get("jsonl_download_uri")
        updated = entry.get("updated_at")
        if not isinstance(uri, str) or not isinstance(updated, str) or not updated:
            raise ScryfallError("contract", f"{BULK_TYPE} has no jsonl_download_uri or updated_at")
        where = urlsplit(uri)
        if where.scheme != "https" or where.hostname not in (*DOWNLOAD_HOSTS, urlsplit(client.base).hostname):
            raise ScryfallError("contract", f"{BULK_TYPE} is offered from an unexpected address")
        size = entry.get("compressed_size")
        return {"uri": uri, "updated_at": updated, "size": str(size) if isinstance(size, int) else ""}

    async def _download_and_build(
        self, client: Any, entry: dict[str, str], halt: threading.Event, received: dict[str, bool]
    ) -> int:
        """Download the file to a temp file and build the copy from it. ``halt`` set (turned off)
        stops the transfer at the next piece (Stopped); ``received["whole"]`` turns True once the
        whole file is on disk, before it is read."""
        gz = self.path.with_name(self.path.name + ".download.gz")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        got = 0
        try:
            out = await asyncio.to_thread(open, gz, "wb")
            try:
                async with client.http.stream("GET", entry["uri"], headers=client.headers) as resp:
                    if resp.status_code != 200:
                        raise ScryfallError("unavailable", f"bulk file answered {resp.status_code}")
                    # the file as sent (Scryfall sends it as application/gzip, with no
                    # content-encoding): its length is the one to check against
                    encoded = bool(resp.headers.get("content-encoding"))
                    expected = "" if encoded else resp.headers.get("content-length") or entry["size"]
                    pending = bytearray()
                    async for chunk in resp.aiter_bytes():
                        if halt.is_set():  # turned off: leaving the block closes the transfer
                            raise Stopped()
                        pending += chunk
                        got += len(chunk)
                        if len(pending) >= CHUNK:
                            await asyncio.to_thread(out.write, bytes(pending))
                            pending.clear()
                    await asyncio.to_thread(out.write, bytes(pending))
            finally:
                await asyncio.to_thread(out.close)
            if expected and expected.isdigit() and int(expected) != got:
                # a transfer cut short is a transport failure, not a damaged file
                raise ScryfallError("unavailable", f"the download was cut off ({got} of {expected} bytes)")
            if halt.is_set():
                raise Stopped()
            received["whole"] = True
            meta = {"updated_at": entry["updated_at"], "downloaded_at": str(time.time()), "uri": entry["uri"]}
            return await asyncio.to_thread(
                build, gz, self.path, meta, stop=halt, min_printings=self.min_printings
            )
        finally:
            with contextlib.suppress(OSError):
                gz.unlink()

    def close(self) -> None:
        """Stop a build under way (the gateway shuts down) and close the reader."""
        self._closed.set()
        self._halt.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- reading ---------------------------------------------------------------------------------
    def _rows(self, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._open()
            if conn is None:
                return []
            zdict = self._meta.get("zdict") or b""
            out = []
            for (blob,) in conn.execute(sql, args):
                z = zlib.decompressobj(zdict=zdict)
                out.append(json.loads(z.decompress(blob) + z.flush()))
            return out

    def _picker_rows(self, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        rows = self._rows(sql, args)
        for row in rows:
            row.pop("_prices", None)
        return rows

    def _count(self, sql: str, args: tuple[Any, ...]) -> int:
        with self._lock:
            conn = self._open()
            return int(conn.execute(sql, args).fetchone()[0]) if conn is not None else 0

    def _labelled(self, out: dict[str, Any]) -> dict[str, Any]:
        as_of = _iso(self._meta.get("updated_at"))
        return {
            **out,
            "from_local_copy": True,
            "as_of": as_of,
            "prices_as_of": as_of,
            "as_of_note": as_of_line(as_of),
        }

    def _reading(self, read: Callable[[], Any], empty: Any) -> Any:
        """``read()`` from the copy: ``empty`` while the copy is turned off, and for a copy that
        can't be read (said once per file)."""
        if not self.enabled:
            return empty
        try:
            return read()
        except (sqlite3.Error, zlib.error, ValueError, KeyError) as exc:
            with self._lock:
                self._damaged(exc)
            return empty

    def prints_now(self, oracle_id: str, **kw: Any) -> dict[str, Any] | None:
        """One page of the card's printings as ``ScryfallClient.prints`` gives it, from the copy,
        with ``from_local_copy``, ``as_of`` and ``as_of_note``; None when the copy has no such card,
        there is no copy, it is turned off or it can't be read. Blocking: call it through
        ``prints``."""
        return self._reading(lambda: self._prints(oracle_id, **kw), None)

    def _prints(
        self,
        oracle_id: str,
        *,
        page: int = 1,
        finish: str | None = None,
        frame: str | None = None,
        border: str | None = None,
        order: str = "newest",
        set_code: str | None = None,
        number: str | None = None,
    ) -> dict[str, Any] | None:
        oid = oracle_id.strip().lower()
        where, args = ["oracle_id = ?"], [oid]
        if not self._count("SELECT count(*) FROM printings WHERE oracle_id = ?", (oid,)):
            return None
        if finish:
            where.append("finishes LIKE ?")
            args.append(f"%,{finish},%")
        if frame:
            where.append("frame = ?")
            args.append(frame)
        if border:
            where.append("border = ?")
            args.append(border)
        if set_code and number:
            where += ["set_code = ?", "cn = ?"]
            args += [set_code.lower(), number.lower()]
        cond = " AND ".join(where)
        sort, direction = PRINT_ORDERS.get(order, PRINT_ORDERS["newest"])
        if sort == "usd":  # as Scryfall: a printing with no price comes last
            by = "usd IS NULL, usd ASC, released DESC"
        else:
            by = f"released {'DESC' if direction == 'desc' else 'ASC'}"
        total = self._count(f"SELECT count(*) FROM printings WHERE {cond}", tuple(args))  # noqa: S608
        cards = self._picker_rows(
            f"SELECT row FROM printings WHERE {cond} ORDER BY {by} LIMIT ? OFFSET ?",  # noqa: S608
            (*args, PAGE, (page - 1) * PAGE),
        )
        has_more = total > page * PAGE and page < PRINT_PAGES
        return self._labelled({"cards": cards, "has_more": has_more, "total_cards": total, "page": page})

    async def prints(self, oracle_id: str, **kw: Any) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.prints_now, oracle_id, **kw)

    def printings_of_now(self, oracle_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Every printing of each card, as ``ScryfallClient.printings_of`` gives them, prices as
        Scryfall gave them on the copy's day. Not used by the daily price check, which records
        today's prices only (see the module docstring)."""
        return self._reading(lambda: self._printings_of(oracle_ids), {})

    def _printings_of(self, oracle_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for oid in sorted({o.strip().lower() for o in oracle_ids}):
            for row in self._rows(
                "SELECT row FROM printings WHERE oracle_id = ? ORDER BY released DESC", (oid,)
            ):
                out.setdefault(oid, []).append(
                    {
                        "id": row.get("scryfall_id"),
                        "oracle_id": oid,
                        "name": row.get("name"),
                        "set": row.get("set"),
                        "collector_number": row.get("collector_number"),
                        "finishes": list(row.get("finishes") or []),
                        "prices": row.get("_prices") or {},
                    }
                )
        return out

    def oracle_of_now(self, name: str) -> tuple[str, str] | None:
        """The oracle id and full name of the card called ``name`` (or one of its faces)."""
        return self._reading(lambda: self._oracle_of(name), None)

    def _oracle_of(self, name: str) -> tuple[str, str] | None:
        with self._lock:
            conn = self._open()
            if conn is None:
                return None
            hit = conn.execute(
                "SELECT oracle_id, card_name FROM names WHERE name = ?", (fold(name),)
            ).fetchone()
            return (str(hit[0]), str(hit[1])) if hit else None

    async def oracle_of(self, name: str) -> tuple[str, str] | None:
        return await asyncio.to_thread(self.oracle_of_now, name)
