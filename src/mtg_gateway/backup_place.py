"""Where the gateway's backup folder sits on Archidekt, and moving or renaming it from the gateway
(Account details › Backup folder, and the My decks boxes a folder move or delete is refused in
because the backup folder is inside).

The backup folder is the folder the gateway copies a deck into before it applies a change (when
backups are on): the private folder with the Settings page's name (``MTG Gateway Backups`` by
default) directly under the member's top level, which ``ArchidektClient.ensure_folder`` reuses or
makes. A folder of that exact name deeper down is still treated as it (hidden from My decks, its
decks backup copies), but new copies go to the one at the top level, so a backup folder moved
into another folder on Archidekt blocks moving or deleting that folder. Two writes put it right,
as archidekt.com's own folder page sends them (``PATCH /api/massUpdate/``, the same items the
gateway's folder move and rename send):

* **Move to your top level**: ``{"type": "folder", "id", "patch": {"parentFolder": <top level>}}``.
  Refused when a folder of that name is at the top level already (Archidekt would have two).
* **Rename**: ``{"type": "folder", "id", "patch": {"name"}}``. The folder stops being the backup
  folder by name: the next backup copy goes into a new folder with the backup name, and the
  renamed folder shows in My decks like any other. The gateway records it first
  (``db.record_backup_folder``), so it still knows the folder and its copies as its backups and
  never deletes them as an ordinary folder (DeckService.folder_contents).

Both are the member's own presses (a session and the page's CSRF token), never an assistant's tool,
as the other folder moves and renames are. Each is checked by reading the folder tree back."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from .decks import DeckError

if TYPE_CHECKING:
    from .decks import DeckService

ACTIONS = ("backup_top", "backup_rename")
# The card's own notices, by the code its redirect carries
OK_WORDS = {
    "backup_moved": "The backup folder is at your top level now.",
    "backup_renamed": (
        "The folder is renamed. The next backup copy goes into a new backup folder at your top level."
    ),
    "backup_same": "Nothing to change: it is already like that.",
}


def roots(decks: DeckService, sub: str, info: dict[str, Any]) -> list[dict[str, Any]]:
    """The backup folders in the member's folder tree, each the outermost one: those known by the
    backup folder's name (``current``) and those the gateway recorded putting copies in and that
    were renamed since. Each ``{id, name, depth, parent, current, top, path}``, ``path`` the
    names of the folders it sits in, top level first."""
    recorded = decks.db.backup_folder_ids(sub)
    by_id = {f["id"]: f for f in info["folders"]}
    out: list[dict[str, Any]] = []
    for f in info["folders"]:
        if not f["depth"]:
            continue
        parent = by_id.get(f["parent"])
        current = bool(f.get("backup")) and not (parent and parent.get("backup"))
        if not current and not (f["id"] in recorded and not f.get("backup")):
            continue
        path: list[str] = []
        up = parent
        while up is not None and up["depth"]:
            path.insert(0, up["name"])
            up = by_id.get(up["parent"])
        out.append({**f, "current": current, "top": f["depth"] == 1, "path": path})
    return out


def in_the_way(decks: DeckService, sub: str, info: dict[str, Any], folder_id: int) -> int | None:
    """The id of a backup folder below ``folder_id`` that the gateway could move to the top level
    (the My decks box's button), else None."""
    below: set[int] = set()
    parents = {int(folder_id)}
    for f in info["folders"]:  # parents come before their children
        if f["parent"] in parents:
            below.add(f["id"])
            parents.add(f["id"])
    return next((r["id"] for r in roots(decks, sub, info) if r["id"] in below), None)


async def _root(decks: DeckService, sub: str, folder_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    info = await decks.folders(sub)
    found = next((r for r in roots(decks, sub, info) if r["id"] == int(folder_id)), None)
    if found is None:
        raise DeckError(
            "not_found", "That is not one of the gateway's backup folders on your Archidekt account."
        )
    return info, found


async def move_to_top(decks: DeckService, sub: str, folder_id: int) -> dict[str, Any]:
    """Put a backup folder, with the copies inside, back at the member's top level."""
    decks._writes_switch()
    info, folder = await _root(decks, sub, folder_id)
    if folder["top"]:
        return {**folder, "changed": False}
    decks._not_being_deleted(sub, folder["id"], moving=True)
    root = int(info["root_id"])
    if any(
        f["depth"] == 1 and f["id"] != folder["id"] and f["name"].casefold() == folder["name"].casefold()
        for f in info["folders"]
    ):
        raise DeckError(
            "invalid",
            f"There is already a folder called “{folder['name']}” at your top level, so this one can't go "
            "there too. Rename this one first, then move it.",
        )
    if folder["current"]:
        decks.db.record_backup_folder(sub, folder["id"])
    await decks._call(
        sub,
        lambda token, *_: decks.client.mass_update(
            token, [{"type": "folder", "id": folder["id"], "patch": {"parentFolder": root}}]
        ),
    )
    _after, now = await decks._folder_in(sub, folder["id"])
    ok = now["parent"] == root
    decks._audit("folder_moved", sub=sub, detail={"folder_id": folder["id"], "backup": True, "verified": ok})
    if not ok:
        raise DeckError("verify_mismatch", "Archidekt accepted the request but the folder did not move.")
    return {**now, "changed": True}


async def rename(decks: DeckService, sub: str, folder_id: int, name: Any) -> dict[str, Any]:
    """Rename a backup folder (it stays recorded as one: see the module's words)."""
    decks._writes_switch()
    clean = decks._folder_name(name)
    info, folder = await _root(decks, sub, folder_id)
    if folder["name"] == clean:
        return {**folder, "changed": False}
    decks._not_being_deleted(sub, folder["id"], moving=True)
    decks._not_the_backup_name(clean, folder["depth"])
    if any(
        f["parent"] == folder["parent"]
        and f["id"] != folder["id"]
        and f["name"].casefold() == clean.casefold()
        for f in info["folders"]
    ):
        raise DeckError("invalid", f"There is already a folder called “{clean}” there.")
    # recorded before the write: a rename that lands but can't be read back is still known
    decks.db.record_backup_folder(sub, folder["id"])
    await decks._call(
        sub,
        lambda token, *_: decks.client.mass_update(
            token, [{"type": "folder", "id": folder["id"], "patch": {"name": clean}}]
        ),
    )
    _after, now = await decks._folder_in(sub, folder["id"])
    ok = now["name"] == clean
    decks._audit(
        "folder_renamed", sub=sub, detail={"folder_id": folder["id"], "backup": True, "verified": ok}
    )
    if not ok:
        raise DeckError(
            "verify_mismatch", "Archidekt accepted the request but the folder's name did not change."
        )
    return {**now, "changed": True}


async def post(decks: DeckService, sub: str, action: str, data: dict[str, Any]) -> str:
    """The Backup folder page's two forms: the notice code to show (``ok=...`` or ``err=...``)
    and, for a refusal, its words (kept for the page, which shows them as they are)."""
    raw = str(data.get("folder_id") or "")
    if not raw.isdigit():
        raise DeckError("invalid", "Pick a backup folder.")
    if action == "backup_top":
        out = await move_to_top(decks, sub, int(raw))
        return "backup_moved" if out["changed"] else "backup_same"
    out = await rename(decks, sub, int(raw), data.get("name"))
    return "backup_renamed" if out["changed"] else "backup_same"


def _where(r: dict[str, Any]) -> str:
    if r["top"]:
        return "at your top level"
    return "inside “" + " › ".join(html.escape(p) for p in r["path"]) + "”"


async def card_html(
    decks: DeckService, sub: str, csrf_in: str, *, ok: str | None = None, error: str = ""
) -> str:
    """Account details › Backup folder: where it is, and Move to your top level / Rename."""
    from . import ui

    if not decks.status(sub)["linked"]:
        return (
            "<div class='card' id='backups'><p>Link your Archidekt account first: the backup folder is "
            f"on Archidekt.</p><div class='ui-actions'>"
            f"{ui.button_link('Link Archidekt', '/account/details/archidekt', kind='primary')}</div></div>"
        )
    name = html.escape(decks.backup_folder)
    on = bool(decks.live.get("archidekt_backups"))
    lead = (
        f"<p>Before the gateway applies a change to one of your decks, it keeps a private copy of the deck "
        f"in the folder “{name}” on Archidekt.</p>"
        if on
        else f"<p>Backup copies on Archidekt are switched off on this gateway. Copies made before stay "
        f"in the folder “{name}”.</p>"
    )
    notice = ""
    if ok in OK_WORDS:
        notice = f"<div class='notice ok' role='status'>{html.escape(OK_WORDS[ok])}</div>"
    elif error:
        notice = f"<div class='notice error' role='alert'>{html.escape(error)}</div>"
    try:
        info = await decks.folders(sub)
    except DeckError as exc:
        return (
            f"<div class='card' id='backups'>{notice}{lead}"
            f"<p class='notice error'>{html.escape(str(exc))}</p></div>"
        )
    found = roots(decks, sub, info)
    current = [r for r in found if r["current"]]
    earlier = [r for r in found if not r["current"]]
    parts = [f"<div class='card' id='backups'>{notice}{lead}"]
    if not current:
        parts.append(
            "<p class='muted'>There is no backup folder on Archidekt yet. The gateway makes it at your top "
            "level the first time it keeps a copy.</p>"
        )
    for r in current:
        parts.append(_folder_block(r, csrf_in, earlier=False))
    if earlier:
        parts.append(
            "<h3 class='ui-section'>Renamed backup folders</h3><p class='muted small'>The gateway put "
            "backup copies in these before they were renamed. It still knows them as its backups, so it "
            "never deletes them as ordinary folders; delete them on Archidekt when you no longer need "
            "the copies.</p>"
        )
        parts += [_folder_block(r, csrf_in, earlier=True) for r in earlier]
    parts.append("</div>")
    return "".join(parts)


def _folder_block(r: dict[str, Any], csrf_in: str, *, earlier: bool) -> str:
    fid = int(r["id"])
    name = html.escape(r["name"])
    head = f"<p><strong>“{name}”</strong> is {_where(r)}.</p>"
    move = ""
    if not r["top"]:
        why = (
            ""
            if earlier
            else "<p class='small'>New backup copies go to a backup folder at your top level, and a folder "
            "with this one inside can't be moved or deleted from the gateway. Moving it to your top level "
            "puts it right.</p>"
        )
        move = (
            f"{why}<form method='post' class='inbox'>{csrf_in}"
            "<input type='hidden' name='action' value='backup_top'>"
            f"<input type='hidden' name='folder_id' value='{fid}'>"
            "<div class='ui-actions'><button class='primary'>Move to your top level</button></div></form>"
        )
    rename_note = (
        ""
        if earlier
        else "<p class='small muted'>After a rename the folder shows in My decks like any other, the copies "
        "already made stay in it, and the next backup copy goes into a new “"
        + html.escape(r["name"])
        + "” folder at your top level.</p>"
    )
    rename = (
        f"<form method='post' class='inbox'>{csrf_in}"
        "<input type='hidden' name='action' value='backup_rename'>"
        f"<input type='hidden' name='folder_id' value='{fid}'>"
        f"<label for='bk-name-{fid}'>New name</label>"
        f"<input id='bk-name-{fid}' type='text' name='name' maxlength='100' required "
        f"value='{name}' autocomplete='off'>"
        f"{rename_note}"
        "<div class='ui-actions'><button class='btn'>Rename</button></div></form>"
    )
    return f"<div class='bkf'>{head}{move}{rename}</div>"
