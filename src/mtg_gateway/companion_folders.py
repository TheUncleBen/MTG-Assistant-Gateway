"""The JSON routes behind My decks' menus and boxes: a folder's new folder, rename, move and
delete, and a deck's rename, move, delete and export. Each is the member's own press on a gateway
page (a session cookie and the page's X-CSRF-Token), with the same checks as the pages that did
these before; none of them is an assistant's tool.

Deleting a folder runs as a job, since it saves every deck inside first (a snapshot and, with
backups on, a backup copy) at the gateway's Archidekt pace: the box starts it and reads its
progress until it ends."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .decks import DeckError, current_client
from .folderview import visible_folders
from .pages import BROWSER_CLIENT_ID, browser_session, csrf_header_ok, read_limited

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

logger = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
MAX_BODY = 8192
KEEP_JOBS = 50  # finished folder deletes kept for their box (per gateway)
STATUS = {
    "unauthenticated": 401,
    "csrf": 403,
    "forbidden": 403,
    "not_found": 404,
    "backup_inside": 409,
    "changed": 409,
    "busy": 409,
    "not_linked": 409,
    "rate_limited": 429,
    "writes_disabled": 403,
    "unavailable": 503,
}
FOLDER_ID = re.compile(r"[0-9]{1,12}")


def _fail(kind: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": kind, "message": message, **extra}, STATUS.get(kind, 400), headers=NO_STORE
    )


def _from(exc: DeckError) -> JSONResponse:
    extra = {k: v for k, v in exc.extra.items() if k in ("counts",)}
    resp = _fail(exc.kind, str(exc), **extra)
    if exc.retry_after:
        resp.headers["Retry-After"] = str(exc.retry_after)
    return resp


def _id_or_none(value: Any) -> int | None:
    """A folder id from a box (None, "" and "root" mean the top level); ValueError otherwise."""
    if value is None or value == "" or value == "root":
        return None
    text = str(value)
    if not FOLDER_ID.fullmatch(text):
        raise ValueError(text)
    return int(text)


def _folder_out(f: dict[str, Any]) -> dict[str, Any]:
    return {k: f[k] for k in ("id", "name", "depth", "parent", "private") if k in f}


class FolderJobs:
    """The folder deletes running and the last finished ones, in memory: what a delete did is in
    the audit log and each deck's snapshot anyway. One at a time per member."""

    def __init__(self) -> None:
        self._jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def get(self, sub: str, job_id: str) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        return job if job is not None and job["sub"] == sub else None

    def running(self, sub: str) -> bool:
        return any(j["sub"] == sub and j["state"] == "running" for j in self._jobs.values())

    def start(self, run: Any, sub: str) -> dict[str, Any]:
        job_id = secrets.token_urlsafe(9)
        job: dict[str, Any] = {
            "id": job_id,
            "sub": sub,
            "state": "running",
            "step": "",
            "done": 0,
            "total": 0,
            "message": "",
            "notice": "",
            "started_at": int(time.time()),
        }
        self._jobs[job_id] = job
        while len(self._jobs) > KEEP_JOBS:
            old_id, old = next(iter(self._jobs.items()))
            if old["state"] == "running":
                break
            self._jobs.pop(old_id)

        async def go() -> None:
            try:
                await run(job)
            except DeckError as exc:
                job.update(state="failed", message=str(exc), error=exc.kind)
            except Exception:
                logger.exception("deleting a folder failed")
                job.update(
                    state="failed",
                    error="failed",
                    message="Something went wrong while deleting the folder. Check it on Archidekt; "
                    "every deck's snapshot made so far is under History."
                    if job.get("delete_sent")
                    else "Something went wrong while deleting the folder. Nothing was deleted; every "
                    "deck's snapshot made so far is under History.",
                )
            else:
                job["state"] = "done"

        task = asyncio.create_task(go())
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))
        return job

    async def aclose(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


def _job_out(job: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in job.items() if k != "sub"}


def add_folder_routes(server: MCPServer, state: AppState) -> FolderJobs:
    """Register the routes; the folder delete jobs they share are returned (app shutdown closes them)."""
    s = state.settings
    decks = state.decks
    jobs = FolderJobs()

    async def member(request: Request, *, write: bool) -> tuple[str | None, JSONResponse | None]:
        sub, sid = browser_session(state, request)
        if not sub:
            return None, _fail("unauthenticated", "Sign in again to use this.")
        if write and not csrf_header_ok(s, sid, request):
            return None, _fail("csrf", "Reload the page and try again.")
        current_client.set(BROWSER_CLIENT_ID)
        return sub, None

    async def body_of(request: Request) -> dict[str, Any] | None:
        raw = await read_limited(request, MAX_BODY)
        try:
            body = json.loads(raw or b"{}") if raw is not None else None
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def ok(**fields: Any) -> JSONResponse:
        return JSONResponse({"ok": True, **fields}, headers=NO_STORE)

    def folder_id_of(request: Request) -> int | None:
        raw = request.path_params.get("fid", "")
        return int(raw) if FOLDER_ID.fullmatch(raw) else None

    # -- folders -------------------------------------------------------------------------------
    @server.custom_route("/decks/api/folders", methods=["GET"], include_in_schema=False)
    async def folder_list(request: Request) -> Response:
        """The member's folders for the Move and Inside lists: the backup folder never shows."""
        sub, refused = await member(request, write=False)
        if refused:
            return refused
        try:
            info = await decks.folders(sub)
        except DeckError as exc:
            return _from(exc)
        return ok(root_id=info["root_id"], folders=[_folder_out(f) for f in visible_folders(info)])

    @server.custom_route("/decks/api/folders/check", methods=["POST"], include_in_schema=False)
    async def folder_check(request: Request) -> Response:
        """The new folder box's first step: the name and the place, refused as creating it would
        be; nothing is created."""
        sub, refused = await member(request, write=True)
        if refused:
            return refused
        body = await body_of(request)
        if body is None:
            return _fail("invalid", "Send JSON.")
        try:
            name, parent = await decks.check_new_folder(
                sub, body.get("name") or "", _id_or_none(body.get("parent_id"))
            )
        except ValueError:
            return _fail("invalid", "Pick a folder.")
        except DeckError as exc:
            return _from(exc)
        top = parent["depth"] == 0
        return ok(name=name, parent_id=parent["id"], parent_name="" if top else parent["name"], top_level=top)

    @server.custom_route("/decks/api/folders", methods=["POST"], include_in_schema=False)
    async def folder_create(request: Request) -> Response:
        sub, refused = await member(request, write=True)
        if refused:
            return refused
        body = await body_of(request)
        if body is None:
            return _fail("invalid", "Send JSON.")
        try:
            made = await decks.create_folder(sub, body.get("name") or "", _id_or_none(body.get("parent_id")))
        except ValueError:
            return _fail("invalid", "Pick a folder.")
        except DeckError as exc:
            return _from(exc)
        return ok(folder=_folder_out(made), notice="folder_created")

    @server.custom_route("/decks/api/folders/{fid}/rename", methods=["POST"], include_in_schema=False)
    async def folder_rename(request: Request) -> Response:
        sub, refused = await member(request, write=True)
        if refused:
            return refused
        fid, body = folder_id_of(request), await body_of(request)
        if fid is None or body is None:
            return _fail("invalid", "Pick a folder.")
        try:
            now = await decks.rename_folder(sub, fid, body.get("name") or "")
        except DeckError as exc:
            return _from(exc)
        return ok(folder=_folder_out(now), notice="folder_renamed")

    @server.custom_route("/decks/api/folders/{fid}/move", methods=["POST"], include_in_schema=False)
    async def folder_move(request: Request) -> Response:
        sub, refused = await member(request, write=True)
        if refused:
            return refused
        fid, body = folder_id_of(request), await body_of(request)
        if fid is None or body is None:
            return _fail("invalid", "Pick a folder.")
        try:
            now = await decks.move_folder(sub, fid, _id_or_none(body.get("parent_id")))
        except ValueError:
            return _fail("invalid", "Pick a folder.")
        except DeckError as exc:
            return _from(exc)
        return ok(folder=_folder_out(now), notice="folder_moved")

    @server.custom_route("/decks/api/folders/{fid}/contents", methods=["GET"], include_in_schema=False)
    async def folder_contents(request: Request) -> Response:
        """What the delete box counts: decks, card packages and folders inside, read now."""
        sub, refused = await member(request, write=False)
        if refused:
            return refused
        fid = folder_id_of(request)
        if fid is None:
            return _fail("invalid", "Pick a folder.")
        try:
            got = await decks.folder_contents(sub, fid)
        except DeckError as exc:
            return _from(exc)
        return ok(
            name=got["folder"]["name"],
            decks=len(got["decks"]),
            packages=len(got["packages"]),
            folders=len(got["folders"]),
            backups=bool(decks.live.get("archidekt_backups")),
        )

    @server.custom_route("/decks/api/folders/{fid}/delete", methods=["POST"], include_in_schema=False)
    async def folder_delete(request: Request) -> Response:
        sub, refused = await member(request, write=True)
        if refused:
            return refused
        fid, body = folder_id_of(request), await body_of(request)
        if fid is None or body is None:
            return _fail("invalid", "Pick a folder.")
        try:
            decks._writes_switch()  # refused at once rather than in the job
        except DeckError as exc:
            return _from(exc)
        if jobs.running(sub):
            return _fail("busy", "A folder of yours is already being deleted. Wait for it to finish.")
        typed = str(body.get("typed_name") or "")
        shown = {k: body.get(k) for k in ("decks", "packages", "folders")}

        async def run(job: dict[str, Any]) -> None:
            done = await decks.delete_folder(sub, fid, typed, shown, progress=job)
            counts = done["counts"]
            job.update(
                step="",
                notice="folder_deleted",
                message=f"Deleted “{done['folder']['name']}” on Archidekt with "
                f"{_n(counts['decks'], 'deck')}, {_n(counts['packages'], 'card package')} and "
                f"{_n(counts['folders'], 'folder')} inside it.",
            )

        job = jobs.start(run, sub)
        return JSONResponse({"ok": True, "job": _job_out(job)}, 202, headers=NO_STORE)

    @server.custom_route("/decks/api/folders/jobs/{job_id}", methods=["GET"], include_in_schema=False)
    async def folder_job(request: Request) -> Response:
        sub, refused = await member(request, write=False)
        if refused:
            return refused
        job = jobs.get(sub, request.path_params.get("job_id", ""))
        if job is None:
            return _fail(
                "not_found",
                "The gateway has no record of that folder delete any more. Reload My decks and check "
                "whether the folder is gone.",
            )
        return ok(job=_job_out(job))

    # -- decks ---------------------------------------------------------------------------------
    @server.custom_route("/decks/api/decks/{deck_id}/rename", methods=["POST"], include_in_schema=False)
    async def deck_rename(request: Request) -> Response:
        """The deck menu's Rename…: the same change as the name on the deck's Settings page,
        applied at once as the member's own press."""
        sub, refused = await member(request, write=True)
        if refused:
            return refused
        body = await body_of(request)
        if body is None:
            return _fail("invalid", "Send JSON.")
        deck_id = request.path_params["deck_id"]
        try:
            decks._writes_switch()  # refused before a proposal is made
            p = await decks.propose_deck_details(sub, deck_id, {"name": str(body.get("name") or "").strip()})
            pid = p["proposal_id"]
            try:
                await decks.apply(sub, pid, via="browser")
            except DeckError:
                # the box shows why; no waiting change is left behind that the member never asked for
                with contextlib.suppress(DeckError):
                    decks.reject(sub, pid, via="browser")
                raise
            await decks.wait_applied(pid)
            row = state.db.get_proposal(pid, sub) or {}
            if row.get("state") != "applied":
                return _fail("failed", "Archidekt did not take the new name, so the deck keeps its old one.")
            deck = await decks.get_own_deck(sub, deck_id)
        except DeckError as exc:
            return _from(exc)
        return ok(name=deck.name or "Unnamed deck", notice="saved")

    @server.custom_route("/decks/api/decks/{deck_id}/move", methods=["POST"], include_in_schema=False)
    async def deck_move(request: Request) -> Response:
        sub, refused = await member(request, write=True)
        if refused:
            return refused
        body = await body_of(request)
        if body is None:
            return _fail("invalid", "Send JSON.")
        try:
            got = await decks.move_deck(
                sub, request.path_params["deck_id"], _id_or_none(body.get("folder_id"))
            )
        except ValueError:
            return _fail("invalid", "Pick a folder.")
        except DeckError as exc:
            return _from(exc)
        return ok(folder_id=got["folder_id"], notice="moved")

    @server.custom_route("/decks/api/decks/{deck_id}/delete-info", methods=["GET"], include_in_schema=False)
    async def deck_delete_info(request: Request) -> Response:
        sub, refused = await member(request, write=False)
        if refused:
            return refused
        try:
            deck = await decks.get_own_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return _from(exc)
        cards = sum(c.quantity for c in deck.cards if deck.in_deck(c))
        return ok(
            name=deck.name or "Unnamed deck", cards=cards, backups=bool(decks.live.get("archidekt_backups"))
        )

    @server.custom_route("/decks/api/decks/{deck_id}/delete", methods=["POST"], include_in_schema=False)
    async def deck_delete(request: Request) -> Response:
        sub, refused = await member(request, write=True)
        if refused:
            return refused
        body = await body_of(request)
        if body is None:
            return _fail("invalid", "Send JSON.")
        try:
            await decks.delete_deck(sub, request.path_params["deck_id"], str(body.get("typed_name") or ""))
        except DeckError as exc:
            return _from(exc)
        return ok(notice="deleted")

    @server.custom_route("/decks/api/decks/{deck_id}/export", methods=["GET"], include_in_schema=False)
    async def deck_export(request: Request) -> Response:
        """The Export box: the texts and files of the deck's Export page."""
        from .decks import deck_to_archidekt_text, deck_to_text

        sub, refused = await member(request, write=False)
        if refused:
            return refused
        try:
            deck = await decks.get_any_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return _from(exc)
        base = f"/decks/{deck.id}/export"
        files = [
            ("Archidekt .txt", ".archidekt.txt"),
            ("Plain .txt", ".txt"),
            (".csv", ".csv"),
            (".json", ".json"),
            ("Arena .txt", ".arena.txt"),
            ("MTGO .dek", ".dek"),
            ("PDF", ".pdf"),
        ]
        return ok(
            name=deck.name or "Unnamed deck",
            archidekt=deck_to_archidekt_text(deck),
            plain=deck_to_text(deck),
            side=deck_to_text(deck, zone="side"),
            files=[{"label": label, "href": base + ext} for label, ext in files],
        )

    return jobs


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"
