"""The Binder's big jobs (0.7.46, Archidekt parity release 3): an import of more than a hundred
lines, clearing the whole Binder, and putting a cleared Binder back.

**Import.** Archidekt's collection is written one card at a time (its file upload's options were
not recorded, so the gateway does not send it). An import of up to MAX_IMPORT_ROWS lines runs in
the background through CollectionService.add, CHUNK lines at a time, a card at a time under the
member's own Archidekt allowance (decks.ArchidektBudget): while a quarter of it is left the job
waits (paused) so the member's own changes still go through, and a refusal that slips past, or
Archidekt asking to slow down (429), is waited out and the card tried again. Where it stands is
kept in the database (``binder_jobs``, one row per member), so a stop (Archidekt down, a
refusal, the gateway restarting) keeps the lines done: the Binder page says what went in, where it
stopped and why, and Resume carries on from the next line. The card whose write is out is kept
with the job too (``in_flight``, saved just before the write is sent): a write Archidekt took
without answering is read back at once (CollectionService.read_back) and counted when it landed;
when that read fails, or the gateway stopped while the write was out, the page says that card may
already be in the binder and Resume reads it back before sending anything for it, so no card is
ever added twice.

**Clear.** Archidekt's ``DELETE /collection/clear/`` removes every entry, with no undo. The gateway
asks it only through an account action (actions.py ``clear_collection``) that waits for the
member's own press on its review page in every approval mode. Before anything is sent the whole
Binder is read and kept as a snapshot under History (``deck_id`` "collection"); the clear is
refused, with nothing sent, when that read is not the whole Binder, the Binder changed since the
review, or the snapshot could not be stored. The snapshot can be downloaded as a CSV and put back
with “Restore to my binder”.

**Restore.** The snapshot's entries go back through the same job as an import, with their own
Archidekt printing (no lookup), finish, condition, language, price paid and tags (a tag the
member no longer has is made again first)."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import secrets
import time
from typing import TYPE_CHECKING, Any

from . import collection_tags
from .busy import outage
from .decks import DeckError, needs_look_error

if TYPE_CHECKING:
    from .collection import CollectionError, CollectionService
    from .db import Database
    from .decks import DeckService

logger = logging.getLogger(__name__)

TARGET = "collection"  # a binder snapshot's deck_id, and a clear's proposal target
NAME = "Your binder"
MAX_IMPORT_ROWS = 5_000  # an import's lines (2 MB of text at most, the parser's own limit)
CHUNK = 100  # lines handed to CollectionService.add at a time (its limit for one call)
SLOW_DOWN_TRIES = 5  # Archidekt's "slow down" on one card, waited out this many times in a row
SAVE_EVERY = 10  # cards between saves of the job's result so far (the line count is saved each card)
SNAPSHOT_FIELDS = (
    "card_id",
    "name",
    "set",
    "collector_number",
    "scryfall_id",
    "quantity",
    "finish",
    "condition",
    "language",
    "tags",
    "purchase_price",
    "added_at",
    "set_name",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS binder_jobs (
    sub TEXT PRIMARY KEY,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    done INTEGER NOT NULL,
    total INTEGER NOT NULL,
    items_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    snapshot_id TEXT,
    started_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)
"""
RUNNING = ("running", "paused")


class ImportStore:
    """One import (or restore) per member, as it stands. A member's row goes with their data
    (db.MEMBER_TABLES)."""

    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.execute(SCHEMA)

    def get(self, sub: str) -> dict[str, Any] | None:
        with self.db._lock:
            r = self.db._conn.execute("SELECT * FROM binder_jobs WHERE sub = ?", (sub,)).fetchone()
        if r is None:
            return None
        out = dict(r)
        out["items"] = json.loads(out.pop("items_json"))
        out["result"] = json.loads(out.pop("result_json"))
        return out

    def start(
        self, sub: str, jid: str, kind: str, items: list[dict[str, Any]], snapshot_id: str | None
    ) -> None:
        now = int(time.time())
        with self.db.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO binder_jobs (sub, id, kind, state, done, total, items_json, "
                "result_json, snapshot_id, started_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sub,
                    jid,
                    kind,
                    "running",
                    0,
                    len(items),
                    json.dumps(items, separators=(",", ":")),
                    json.dumps({"added": [], "skipped": []}),
                    snapshot_id,
                    now,
                    now,
                ),
            )

    def update(
        self,
        sub: str,
        jid: str,
        *,
        state: str | None = None,
        done: int | None = None,
        result: dict[str, Any] | None = None,
        in_flight: dict[str, Any] | None | bool = False,
    ) -> None:
        """``in_flight``: the card whose write is about to go out (kept in the result), None to
        drop it once its line is counted, False (the default) to leave it as it is."""
        sets, args = ["updated_at = ?"], [int(time.time())]
        if state is not None:
            sets.append("state = ?")
            args.append(state)
        if done is not None:
            sets.append("done = ?")
            args.append(int(done))
        if result is not None:
            sets.append("result_json = ?")
            args.append(json.dumps(result, separators=(",", ":")))
        if isinstance(in_flight, dict):
            sets.append("result_json = json_set(result_json, '$.in_flight', json(?))")
            args.append(json.dumps(in_flight, separators=(",", ":")))
        elif in_flight is None:
            sets.append("result_json = json_remove(result_json, '$.in_flight')")
        with self.db.tx() as c:
            c.execute(
                f"UPDATE binder_jobs SET {', '.join(sets)} WHERE sub = ? AND id = ?",
                (*args, sub, jid),
            )

    def drop(self, sub: str, jid: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM binder_jobs WHERE sub = ? AND id = ?", (sub, jid))

    def stop_all_running(self, reason: str) -> int:
        """At start-up: a job that was running when the gateway stopped is stopped, with Resume."""
        rows = []
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT sub, id, result_json FROM binder_jobs WHERE state IN ('running', 'paused')"
            ).fetchall()
        for r in rows:
            result = json.loads(r["result_json"])
            result["stopped"] = reason
            self.update(r["sub"], r["id"], state="stopped", result=result)
        return len(rows)


def compact(out: dict[str, Any]) -> dict[str, Any]:
    """What a job keeps of CollectionService.add's result: each row's line and where its copies
    went, each skipped line (import_result reads no more)."""
    return {
        "added": [
            {"index": int(a["index"]), "went_to": a.get("went_to")}
            for a in out.get("added") or []
            if isinstance(a.get("index"), int)
        ],
        "skipped": [
            {"index": int(x["index"])} for x in out.get("skipped") or [] if isinstance(x.get("index"), int)
        ],
    }


class BulkJobs:
    """The import and restore jobs of every member: one at a time per member, in the background,
    with their state in ``ImportStore``."""

    def __init__(self, service: CollectionService):
        self.service = service
        self.store = ImportStore(service.decks.db)
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.pause_poll: float | None = None  # None: collection.IMPORT_PAUSE_POLL
        stopped = self.store.stop_all_running("the gateway restarted while it ran")
        if stopped:
            logger.info("%d binder import(s) stopped by a restart; each can be resumed", stopped)

    def job(self, sub: str) -> dict[str, Any] | None:
        return self.store.get(sub)

    def poll(self) -> float:
        """Seconds a paused job waits before looking at the allowance again."""
        from .collection import IMPORT_PAUSE_POLL

        return IMPORT_PAUSE_POLL if self.pause_poll is None else self.pause_poll

    def running(self, sub: str) -> bool:
        task = self.tasks.get(sub)
        return task is not None and not task.done()

    def start(
        self, sub: str, items: list[dict[str, Any]], *, kind: str = "import", snapshot_id: str | None = None
    ) -> dict[str, Any]:
        """Start a job for ``items`` (parse_collection_import's, or a snapshot's); one that is
        running or stopped part way is returned as it is (the page offers Resume or Done)."""
        have = self.store.get(sub)
        if have is not None and (have["state"] in RUNNING or have["state"] == "stopped"):
            return have
        jid = secrets.token_urlsafe(9)
        self.store.start(sub, jid, kind, items, snapshot_id)
        self._spawn(sub, jid)
        return self.store.get(sub) or {}

    def resume(self, sub: str) -> dict[str, Any] | None:
        have = self.store.get(sub)
        if have is None or have["state"] != "stopped" or self.running(sub):
            return have
        result = dict(have["result"])
        result.pop("stopped", None)  # in_flight stays: the job reads that card back first
        self.store.update(sub, have["id"], state="running", result=result)
        self._spawn(sub, have["id"])
        return self.store.get(sub)

    def finish(self, sub: str) -> dict[str, Any] | None:
        """The member is done with a job that ended or stopped (Done on the page): what it did, for
        the page's notice, and the job is forgotten."""
        have = self.store.get(sub)
        if have is None or have["state"] in RUNNING:
            return None
        self.store.drop(sub, have["id"])
        return have

    def _spawn(self, sub: str, jid: str) -> None:
        task = asyncio.create_task(self._run(sub, jid))
        self.tasks[sub] = task
        task.add_done_callback(
            lambda _t, sub=sub: self.tasks.pop(sub, None) if self.tasks.get(sub) is _t else None
        )

    async def _run(self, sub: str, jid: str) -> None:  # noqa: C901
        from .collection import CollectionError, _Found

        service = self.service
        job = self.store.get(sub)
        if job is None or job["id"] != jid:
            return
        items, kind = job["items"], job["kind"]
        result = {
            "added": list(job["result"].get("added") or []),
            "skipped": list(job["result"].get("skipped") or []),
        }
        done = int(job["done"])
        state = {"name": "running", "since_save": 0, "slow": 0}
        unsure = job["result"].get("in_flight")

        def save(**kw: Any) -> None:
            self.store.update(sub, jid, **kw)

        def low() -> bool:
            budget = service.decks.archidekt_budget
            return budget.room(sub) < budget.per_window // 4 + 4  # a card costs up to three calls

        async def room() -> None:
            while low():
                if state["name"] != "paused":
                    state["name"] = "paused"
                    save(state="paused")
                await asyncio.sleep(self.poll())
            if state["name"] != "running":
                state["name"] = "running"
                save(state="running")

        async def wait(exc: CollectionError) -> bool:
            # nothing was sent: the member's allowance refused the call, or Archidekt asked the
            # gateway to slow down (a few times in a row at most, then the job stops with Resume)
            if getattr(exc, "busy_cause", None) != "budget":
                if exc.kind not in ("rate_limited", "busy") or state["slow"] >= SLOW_DOWN_TRIES:
                    return False
                state["slow"] += 1
            state["name"] = "paused"
            save(state="paused")
            await asyncio.sleep(max(self.poll(), float(exc.retry_after or 0)))
            await room()
            return True

        out: dict[str, Any] = {"added": [], "skipped": []}
        try:
            if kind == "restore":
                items = await self._restore_tags(sub, items)
            if isinstance(unsure, dict) and int(unsure.get("index", -1)) == done:
                # the card whose write was out when the job stopped: read back before anything
                # is sent for it, and counted when it landed
                if await service.read_back(sub, unsure) is not None:
                    result["added"].append(
                        {"index": done, "went_to": "existing" if unsure["before"] else "new"}
                    )
                    done += 1
                    save(done=done, result=_merged(result, out), in_flight=None)
                else:
                    save(in_flight=None)
                unsure = None
            found = _Found()
            await self._prime(sub, found, items[done:])
            for start in range(done, len(items), CHUNK):
                chunk = items[start : start + CHUNK]

                def counted(n: int, start: int = start) -> None:
                    state["slow"] = 0
                    state["since_save"] += 1
                    if state["since_save"] >= SAVE_EVERY:
                        state["since_save"] = 0
                        save(done=start + n, result=_merged(result, out), in_flight=None)
                    else:
                        save(done=start + n, in_flight=None)

                def sending(card: dict[str, Any]) -> None:
                    save(in_flight=card)

                await service.add(
                    sub,
                    chunk,
                    source="restore" if kind == "restore" else "manual",
                    result=out,
                    on_item=counted,
                    retry=wait,
                    before_item=room,
                    found=found,
                    limit=CHUNK,
                    trusted=kind == "restore",
                    first_index=start,
                    before_write=sending,
                )
                save(done=start + len(chunk), result=_merged(result, out))
        except Exception as exc:  # anything: the job must end, and say what went in
            if not isinstance(exc, (CollectionError, DeckError)):
                logger.exception("binder %s stopped", kind)
            reason = (
                str(exc)
                if isinstance(exc, (CollectionError, DeckError))
                and exc.kind not in ("unavailable", "rate_limited", "busy")
                else outage("unavailable")
            )
            merged = _merged(result, out)
            merged["stopped"] = reason.rstrip(".")
            card = getattr(exc, "in_flight", None)
            if card is None and isinstance(unsure, dict) and int(unsure.get("index", -1)) == done:
                card = unsure  # stopped reading back the card left out before: still unsure
            if card is not None:
                merged["in_flight"] = card  # it may have landed: Resume reads it back first
            save(state="stopped", result=merged)
            return
        save(state="done", result=_merged(result, out))

    async def _prime(self, sub: str, found: Any, items: list[dict[str, Any]]) -> None:
        """Read the Binder whole first when that costs fewer calls than looking each distinct card
        up by name (a large import into a large Binder): after it, a card costs its lookup and its
        write only. A small Binder is read whole by the first card anyway."""
        from .collection import WHOLE_READ_SIZE

        try:
            count = int((await self.service.page(sub))["count"])
        except Exception:
            return  # the first card reads as usual
        names = {str(i.get("name") or "").casefold() for i in items}
        pages = -(-count // collection_tags.WHOLE_PAGE_SIZE)
        if count <= WHOLE_READ_SIZE or pages > len(names) or pages > collection_tags.WHOLE_PAGES:
            return
        records, _tags, complete = await self.service.whole(sub)
        found.stamp = self.service._writes.get(sub, 0)
        found.tried_whole = True
        found.take(records)
        found.whole = complete

    async def _restore_tags(self, sub: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """A restore's entries with their tags as the member's tag ids now: a tag the snapshot
        had and the member no longer has is made again (its old color) first."""
        wanted: dict[str, str] = {}
        for it in items:
            for n in it.get("tags") or []:
                wanted.setdefault(str(n).casefold(), str(n))
        if not wanted:
            return items
        decks = self.service.decks
        have = await collection_tags.read_tags(decks, sub)
        job = self.store.get(sub) or {}
        colors: dict[str, str] = {}
        if job.get("snapshot_id"):
            snap = decks.db.get_snapshot(str(job["snapshot_id"]), sub) or {}
            for t in (snap.get("deck") or {}).get("tags") or []:
                if isinstance(t, dict) and t.get("name"):
                    colors[str(t["name"]).casefold()] = str(t.get("color") or "")
        from .account_lists import DEFAULT_TAG_COLOR
        from .archidekt_account import HEX_COLOR_RE

        decks._writes_switch()
        for key, name in wanted.items():
            if any(collection_tags.same_name(t["name"], name) for t in have):
                continue
            color = colors.get(key) or DEFAULT_TAG_COLOR
            if not HEX_COLOR_RE.fullmatch(color):
                color = DEFAULT_TAG_COLOR
            await decks._call(
                sub, lambda token, *_, n=name, c=color: decks.client.create_collection_tag(token, n, c)
            )
        have = await collection_tags.read_tags(decks, sub)
        out = []
        for it in items:
            ids, _missing = collection_tags.ids_for([str(n) for n in it.get("tags") or []], have)
            out.append({**it, "tag_ids": ids})
        return out


def _merged(before: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    now = compact(out)
    return {"added": before["added"] + now["added"], "skipped": before["skipped"] + now["skipped"]}


# -- a binder snapshot -------------------------------------------------------------------------


def snapshot_rows(records: list[dict[str, Any]], tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Binder's records as a snapshot keeps them: each entry's printing (Archidekt's id and
    the Scryfall id, set and number), copies, finish, condition, language, tags by name, price
    paid and when it was added."""
    from .collection import row_out

    by_id = {t["id"]: t for t in tags}
    out = []
    for rec in records:
        r = row_out(rec)
        if not isinstance(r.get("archidekt_card_id"), int) or int(r.get("quantity") or 0) < 1:
            continue
        price = r.get("purchase_price")
        out.append(
            {
                "card_id": r["archidekt_card_id"],
                "name": r["name"],
                "set": r["set"],
                "collector_number": r["collector_number"],
                "scryfall_id": r["scryfall_id"],
                "quantity": int(r["quantity"]),
                "finish": r["finish"],
                "condition": r["condition"] or "",
                "language": r["lang"] or "EN",
                "tags": collection_tags.record_tag_names(rec, by_id),
                "purchase_price": float(price)
                if isinstance(price, (int, float)) and not isinstance(price, bool)
                else None,
                "added_at": r["added_at"],
                "set_name": r["set_name"],
            }
        )
    return out


def restore_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """A binder snapshot's entries as a restore job's items (normalise_items, trusted)."""
    from .collection import LANGUAGES

    items = []
    for c in (snapshot.get("deck") or {}).get("cards") or []:
        if not isinstance(c, dict) or not isinstance(c.get("card_id"), int):
            continue
        lang = str(c.get("language") or "EN").upper()
        items.append(
            {
                "card_id": c["card_id"],
                "name": str(c.get("name") or "?"),
                "set": str(c.get("set") or ""),
                "collector_number": str(c.get("collector_number") or ""),
                "scryfall_id": str(c.get("scryfall_id") or ""),
                "quantity": max(1, min(9_999, int(c.get("quantity") or 1))),
                "finish": c.get("finish") if c.get("finish") in ("nonfoil", "foil", "etched") else "nonfoil",
                "condition": str(c.get("condition") or ""),
                "language": lang if lang in LANGUAGES else "EN",
                "tags": [str(t) for t in c.get("tags") or [] if str(t).strip()],
                **(
                    {"purchase_price": c["purchase_price"]}
                    if isinstance(c.get("purchase_price"), (int, float))
                    else {}
                ),
            }
        )
    return items


def is_binder_snapshot(snap: dict[str, Any] | None) -> bool:
    return bool(snap) and str(snap.get("deck_id") or "") == TARGET


def snapshot_csv(snapshot: dict[str, Any]) -> str:
    """A binder snapshot as the Binder's own Export CSV, with Tags and Price Paid after it (the
    gateway's import reads it back; on archidekt.com pick Ignore for those two)."""
    from .collection import ARCHIDEKT_CONDITION, LANGUAGE_TO_ARCHIDEKT, MODIFIERS

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "Quantity",
            "Name",
            "Finish",
            "Condition",
            "Language",
            "Edition Code",
            "Edition Name",
            "Collector Number",
            "Scryfall ID",
            "Date Added",
            "Tags",
            "Price Paid",
        ]
    )
    for c in (snapshot.get("deck") or {}).get("cards") or []:
        if not isinstance(c, dict):
            continue
        cond = str(c.get("condition") or "")
        lang = str(c.get("language") or "EN")
        price = c.get("purchase_price")
        w.writerow(
            [
                int(c.get("quantity") or 0),
                str(c.get("name") or ""),
                MODIFIERS.get(str(c.get("finish") or "nonfoil"), "Normal"),
                ARCHIDEKT_CONDITION.get(cond, cond),
                LANGUAGE_TO_ARCHIDEKT.get(lang, lang),
                str(c.get("set") or "").upper(),
                str(c.get("set_name") or ""),
                str(c.get("collector_number") or ""),
                str(c.get("scryfall_id") or ""),
                str(c.get("added_at") or "")[:10],
                ", ".join(str(t) for t in c.get("tags") or []),
                f"{float(price):.2f}" if isinstance(price, (int, float)) else "",
            ]
        )
    return out.getvalue()


# -- clearing the whole binder (actions.py clear_collection) -------------------------------------


async def binder_count(decks: DeckService, sub: str) -> tuple[int, int | None]:
    """(entries, copies when the first page holds them all) as Archidekt lists them now."""
    uid = collection_tags.user_id(decks, sub)
    body = await decks._call_unslotted(
        sub, lambda token, *_: decks.client.collection_page(token, uid, page=1, page_size=100)
    )
    count = body.get("count") if isinstance(body.get("count"), int) else len(body["results"])
    copies = None
    if not body.get("next"):
        copies = sum(int(r.get("quantity") or 0) for r in body["results"] if isinstance(r, dict))
    return int(count), copies


async def clear(
    decks: DeckService, sub: str, row: dict[str, Any], progress: dict[str, Any]
) -> dict[str, Any]:
    """Apply an approved clear: the whole Binder read and kept as a snapshot, then Archidekt's
    clear, then a read-back. Nothing is sent when the read is not the whole Binder, the Binder no
    longer has the entries the member reviewed, or the snapshot could not be kept."""
    from .binder_prices import BinderStore

    decks._writes_switch()
    changes = row.get("changes") or {}
    records, tags, complete = await collection_tags.whole_records(decks, sub, slotted=False)
    if not complete:
        raise DeckError(
            "invalid",
            f"Your binder has more entries than the gateway reads at once ({len(records):,} read), so it "
            "can't keep a whole copy first. Nothing was cleared.",
        )
    if len(records) != int(changes.get("entries") or 0):
        raise needs_look_error(
            f"Your binder has {len(records):,} entries now, not the {int(changes.get('entries') or 0):,} "
            "this was reviewed with."
        )
    if not records:
        raise needs_look_error("Your binder is already empty.")
    kept = snapshot_rows(records, tags)
    snapshot = {"name": NAME, "kind": "binder", "cards": kept, "tags": tags}
    sid = secrets.token_urlsafe(12)
    if not decks.db.save_snapshot(
        sid,
        owner_sub=sub,
        deck_id=TARGET,
        proposal_id=row["id"],
        fingerprint="",
        deck=snapshot,
        while_applying=True,
    ):
        raise DeckError("unavailable", "The copy of your binder could not be kept, so nothing was cleared.")
    progress["snapshot_id"] = sid
    await decks._call_unslotted(sub, lambda token, *_: decks.client.clear_collection(token))
    copies = sum(int(c["quantity"]) for c in kept)
    try:
        left, _ = await binder_count(decks, sub)
    except DeckError:
        left = -1  # not read back: not confirmed
    store = BinderStore(decks.db)
    with decks.db._lock:
        held = decks.db._conn.execute(
            "SELECT 1 FROM binder_reads WHERE sub = ? AND read_at IS NOT NULL", (sub,)
        ).fetchone()
    if held:
        store.replace(sub, [], complete=True)
    decks._audit("collection_cleared", sub=sub, detail={"entries": len(records), "snapshot_id": sid})
    return {
        "snapshot_id": sid,
        "entries": len(records),
        "cards": copies,
        "left": left,
        "verified": left == 0,
    }
