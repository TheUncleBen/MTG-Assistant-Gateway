"""My decks by folder (0.7.28, ALIGNMENT §6): one level of the member's Archidekt folder tree at a
time, its subfolders in a "Folders" area above the decks directly in it, with a breadcrumb back
up. Folders are always A to Z; a tile shows the name, how many decks and subfolders are
directly inside, and a lock when the folder is private; empty folders show. The deck
filter searches every deck from any level and says which folder each match is in. The
gateway's backup folder, and anything inside it, never shows.

The tree comes from decks.DeckService.folder_view (cached like the deck list); the deck rows carry
their folder's id (archidekt.list_row ``folder_id``). A deck whose folder is not in the tree (the
tree was cut at MAX_FOLDERS, or changed since it was read) is shown at the top level, so no deck
is ever out of reach. When the tree can't be read, every deck is listed flat with a notice."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from .archidekt import format_label
from .deckpage import deck_list_html
from .searchpanel import DECK_COLOUR_MODES, DIRECTIONS, colour_match
from .theme import esc, icon

SEP = " › "

FOLDERS_UNREADABLE = "Your folders couldn't be read from Archidekt right now, so every deck is listed here."


def visible_folders(info: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The folders a member is shown: everything but the gateway's backup folder and what is
    inside it (decks.DeckService._flatten_folders marks them ``backup``), root first."""
    return [f for f in (info or {}).get("folders", []) if not f.get("backup")]


def resolve_folder(info: dict[str, Any] | None, raw: str) -> int | None:
    """The folder ``?folder=`` names: its id (digits), or for an address from before 0.7.28 the
    first shown folder of that name. None is the top level (also for an unknown or hidden one)."""
    raw = (raw or "").strip()
    if not raw or info is None:
        return None
    shown = [f for f in visible_folders(info) if f["depth"] > 0]
    if re.fullmatch(r"[0-9]{1,12}", raw):
        return next((f["id"] for f in shown if f["id"] == int(raw)), None)
    return next((f["id"] for f in shown if f["name"] == raw), None)


def sort_decks(rows: list[dict[str, Any]], order: str, direction: str = "auto") -> list[dict[str, Any]]:
    """The deck order the toolbar asks for (the list arrives newest change first). ``direction``
    "auto" is each order's own way (names A to Z, dates newest first); "asc" and "desc" force one."""
    rows = list(rows)
    if order == "name":
        rows.sort(key=lambda d: str(d.get("name", "")).lower())
        natural = "asc"
    elif order == "created":
        rows.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
        natural = "desc"
    elif order == "format":
        rows.sort(key=lambda d: (str(d.get("format_name") or ""), str(d.get("name", "")).lower()))
        natural = "asc"
    else:
        rows.sort(key=lambda d: str(d.get("updated_at") or ""), reverse=True)
        natural = "desc"
    if direction in ("asc", "desc") and direction != natural:
        rows.reverse()
    return rows


def read_filters(qp: Any) -> dict[str, str]:
    """The deck list's filters beyond the name, order and view, each limited to its known values:
    the sort direction (``dir``), the colours (``c``, repeated) with how they match (``cm``) and the
    format (``format``, a format name)."""
    picked = [v.upper() for v in (qp.getlist("c") if hasattr(qp, "getlist") else [])]
    colors = "".join(c for c in "WUBRGC" if c in picked)
    return {
        "direction": qp.get("dir") if qp.get("dir") in dict(DIRECTIONS) else "auto",
        "colors": colors,
        "colour_mode": qp.get("cm") if qp.get("cm") in dict(DECK_COLOUR_MODES) else "incl",
        "fmt": (qp.get("format") or "").strip()[:40],
    }


def filter_params(
    q: str, order: str, view: str, folder: str, filters: dict[str, str]
) -> list[tuple[str, str]]:
    """The deck list's address parameters for these controls, the defaults left out."""
    params: list[tuple[str, str]] = []
    if q:
        params.append(("q", q))
    if order and order != "updated":
        params.append(("order", order))
    if filters.get("direction", "auto") != "auto":
        params.append(("dir", filters["direction"]))
    params.extend(("c", c) for c in filters.get("colors", ""))
    if filters.get("colors") and filters.get("colour_mode", "incl") != "incl":
        params.append(("cm", filters["colour_mode"]))
    if filters.get("fmt"):
        params.append(("format", filters["fmt"]))
    if view and view != "grid":
        params.append(("view", view))
    if folder:
        params.append(("folder", folder))
    return params


@dataclass
class Level:
    """What My decks shows: ``crumbs`` [(folder id or None for the top, name)], the ``folders``
    tiles, the ``decks``, ``searching`` (a name filter: every level), ``tree`` False when the
    folder tree could not be read (a flat list), ``current`` the open folder (None: the top)."""

    crumbs: list[tuple[int | None, str]] = field(default_factory=list)
    folders: list[dict[str, Any]] = field(default_factory=list)
    decks: list[dict[str, Any]] = field(default_factory=list)
    searching: bool = False
    filtered: bool = False  # a colour or format filter (every level, like a name)
    tree: bool = True
    current: dict[str, Any] | None = None
    total: int = 0


def arrange(
    rows: list[dict[str, Any]],
    info: dict[str, Any] | None,
    *,
    folder_id: int | None,
    q: str,
    order: str,
    direction: str = "auto",
    colors: str = "",
    colour_mode: str = "incl",
    fmt: str = "",
) -> Level:
    """One level of My decks from the member's decks (backups already left out) and folder tree.
    A name, a colour or a format filter searches every folder."""
    needle = q.strip().casefold()
    filtered = bool(colors or fmt)

    def keep(d: dict[str, Any]) -> bool:
        if needle and needle not in str(d.get("name", "")).casefold():
            return False
        if colors and not colour_match(d.get("colors"), colors, colour_mode):
            return False
        if not fmt:
            return True
        slug = str(d.get("format_name") or "")
        # the drop-down sends the format's slug; a link from before it may still carry its name
        return slug.casefold() == fmt.casefold() or format_label(slug).casefold() == fmt.casefold()

    level = Level(searching=bool(needle) or filtered, filtered=filtered, total=len(rows))
    if info is None:  # no tree: every deck, flat, as before 0.7.28 (with no decks, nothing to say)
        level.tree = not rows
        level.decks = sort_decks([d for d in rows if keep(d)], order, direction)
        return level
    root = info["root_id"]
    every = info.get("folders", [])
    hidden = {f["id"] for f in every if f.get("backup")}
    shown = [f for f in visible_folders(info) if f["depth"] > 0]
    by_id = {f["id"]: f for f in shown}

    def home(d: dict[str, Any]) -> int | None:
        """The shown folder a deck sits in; None for the top level (and a folder not in the tree)."""
        fid = d.get("folder_id")
        if fid is None or fid == root or fid not in by_id:
            return None
        return fid

    rows = [d for d in rows if d.get("folder_id") not in hidden]
    level.total = len(rows)

    def path(fid: int | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while fid is not None and fid in by_id and len(out) < 64:
            out.append(by_id[fid])
            fid = by_id[fid]["parent"]
        return list(reversed(out))

    decks_in: dict[int | None, int] = {}
    for d in rows:
        decks_in[home(d)] = decks_in.get(home(d), 0) + 1
    subs_in: dict[int | None, int] = {}
    for f in shown:
        parent = None if f["parent"] == root else f["parent"]
        subs_in[parent] = subs_in.get(parent, 0) + 1

    def tile(f: dict[str, Any], *, with_path: bool) -> dict[str, Any]:
        where = SEP.join(p["name"] for p in path(f["parent"])) if with_path else ""
        return {
            "id": f["id"],
            "name": f["name"],
            "private": bool(f.get("private")),
            "decks": decks_in.get(f["id"], 0),
            "subfolders": subs_in.get(f["id"], 0),
            "where": where,
        }

    current = by_id.get(folder_id) if folder_id is not None else None
    level.current = current
    level.crumbs = [(None, "Decks")] + [
        (p["id"], p["name"]) for p in path(current["id"] if current else None)
    ]
    if needle or filtered:  # every level, each match with the folder it is in
        found = [f for f in shown if needle and needle in f["name"].casefold()]
        level.folders = [tile(f, with_path=True) for f in sorted(found, key=_folder_key)]
        decks = []
        for d in rows:
            if keep(d):
                d = dict(d)
                fid = home(d)
                d["folder"] = SEP.join(p["name"] for p in path(fid)) if fid is not None else None
                decks.append(d)
        level.decks = sort_decks(decks, order, direction)
        return level
    here = current["id"] if current else None
    kids = [f for f in shown if (None if f["parent"] == root else f["parent"]) == here]
    level.folders = [tile(f, with_path=False) for f in sorted(kids, key=_folder_key)]
    decks = []
    for d in rows:
        if home(d) == here:
            d = dict(d)
            d["folder"] = None  # every deck here is in this folder: the breadcrumb says which
            decks.append(d)
    level.decks = sort_decks(decks, order, direction)
    return level


def _folder_key(f: dict[str, Any]) -> tuple[str, str]:
    return (f["name"].casefold(), f["name"])


# -- HTML ------------------------------------------------------------------------------------------


def level_href(folder_id: int | None, *, order: str) -> str:
    params = []
    if folder_id is not None:
        params.append(("folder", str(folder_id)))
    if order and order != "updated":
        params.append(("order", order))
    return "/decks" + (f"?{urlencode(params)}" if params else "")


def _count_text(decks: int, subfolders: int) -> str:
    bits = []
    if decks:
        bits.append(f"{decks} deck{'' if decks == 1 else 's'}")
    if subfolders:
        bits.append(f"{subfolders} folder{'' if subfolders == 1 else 's'}")
    return " · ".join(bits) if bits else "Empty"


def breadcrumb_html(level: Level, *, order: str) -> str:
    """Decks › Commander › Precons: every step a link but the last (the open folder)."""
    if level.current is None:
        return ""
    items = []
    last = len(level.crumbs) - 1
    for i, (fid, name) in enumerate(level.crumbs):
        if i == last:
            items.append(f"<li aria-current='page'><span>{esc(name)}</span></li>")
        else:
            items.append(f"<li><a href='{esc(level_href(fid, order=order))}'>{esc(name)}</a></li>")
    return f"<nav class='crumbs' aria-label='Folder path'><ol>{''.join(items)}</ol></nav>"


def folder_tile_html(t: dict[str, Any], *, order: str) -> str:
    counts = _count_text(t["decks"], t["subfolders"])
    # a private folder shows a lock in place of the folder icon, so the tile's end stays clear
    # for its ⋮
    ic = (
        f"<span class='ic lock' title='Private folder'>{icon('lock')}"
        "<span class='sr-only'>Private</span></span>"
        if t["private"]
        else f"<span class='ic'>{icon('folder')}</span>"
    )
    where = f"<span class='where' title='{esc(t['where'])}'>In {esc(t['where'])}</span>" if t["where"] else ""
    # the tile's ⋮: static/menus.js opens the folder's menu from it (and from a right-click, a
    # press and hold or Shift+F10 on the tile); the tile itself stays a link. The gateway's backup
    # folder is never listed here (visible_folders); were it, its menu offers no Move or Delete.
    kebab = (
        f"<button type='button' class='kebab' aria-label='Menu for {esc(t['name'])}' aria-haspopup='menu' "
        f"title='Menu'>{icon('more')}</button>"
    )
    backup = " data-backup" if t.get("backup") else ""
    return (
        f"<li class='folder' data-folder-id='{esc(str(t['id']))}' data-folder-name='{esc(t['name'])}'"
        f"{backup}>"
        f"<a href='{esc(level_href(t['id'], order=order))}'>{ic}"
        f"<span class='text'><span class='name' title='{esc(t['name'])}'>{esc(t['name'])}</span>"
        f"<span class='sub'>{esc(counts)}</span>{where}</span></a>{kebab}</li>"
    )


def level_html(
    level: Level,
    *,
    covers: dict[str, dict[str, Any]] | None,
    q: str,
    view: str,
    order: str,
) -> str:
    """The breadcrumb, the Folders area (when there are folders to show) and the Decks area."""
    out = breadcrumb_html(level, order=order)
    if not level.tree:
        out += f"<p class='notice'>{esc(FOLDERS_UNREADABLE)}</p>"
    if level.folders:
        tiles = "".join(folder_tile_html(t, order=order) for t in level.folders)
        out += (
            "<section class='fv-area' aria-labelledby='fv-folders'>"
            f"<h2 id='fv-folders'>Folders <span class='n'>{len(level.folders)}</span></h2>"
            f"<ul class='plain folderlist {esc(view)}'>{tiles}</ul></section>"
        )
    if level.decks:
        listing = deck_list_html(level.decks, covers=covers, q=q, view=view, own=True)
    elif level.searching and q:
        listing = f"<p class='empty'>No decks matching “{esc(q)}” in any folder.</p>"
    elif level.searching:
        listing = "<p class='empty'>No decks match these filters in any folder.</p>"
    elif level.current is not None:
        listing = (
            "<p class='empty'>No decks in this folder.</p>"
            if level.folders
            else "<p class='empty'>This folder is empty.</p>"
        )
    else:
        listing = (
            "<p class='empty'>No decks yet. Create one with New deck, or ask your assistant to propose a "
            "new deck.</p>"
        )
    heading = f"Decks matching “{esc(q)}”" if q else "Matching decks" if level.searching else "Decks"
    out += (
        "<section class='fv-area' aria-labelledby='fv-decks'>"
        f"<h2 id='fv-decks'>{heading} <span class='n'>{len(level.decks)}</span></h2>{listing}</section>"
    )
    return out


FOLDER_CSS = """
/* My decks by folder (folderview.py): breadcrumb, then the Folders and Decks areas, each a
   heading with its count over the tiles (8 px inside a group, 12 to 16 px between) */
.crumbs{margin:0 0 8px}
.crumbs ol{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem .4rem;margin:0;padding:0;list-style:none;
  font-size:1.05rem}
.crumbs li{display:inline-flex;align-items:center;gap:.4rem;min-width:0;max-width:100%}
.crumbs li + li::before{content:'›';color:var(--text-muted)}
.crumbs a,.crumbs span{overflow-wrap:anywhere}
.crumbs a{display:inline-flex;align-items:center;min-height:44px;color:var(--link,var(--text))}
.crumbs li[aria-current] span{font-weight:700}
.fv-area{margin:0 0 14px}
.fv-area > h2{display:flex;align-items:center;gap:.45rem;margin:0 0 8px;font-size:1.05rem;
  overflow-wrap:anywhere}
.fv-area > h2 .n{font-size:.8rem;font-weight:700;color:var(--text-muted);
  border:1px solid var(--border);border-radius:10px;padding:0 .45rem;line-height:1.6}
.fv-area .empty{margin:0;color:var(--text-muted)}
.fv-area ul.decklist{margin-bottom:0}
ul.folderlist{display:grid;gap:10px;margin:0}
ul.folderlist.grid{grid-template-columns:repeat(auto-fill,minmax(min(100%,15rem),1fr))}
ul.folderlist.list{grid-template-columns:minmax(0,1fr);gap:.5rem}
ul.folderlist .folder,ul.folderlist .folder a,ul.folderlist .text{min-width:0}
ul.folderlist .folder a{display:flex;align-items:center;gap:.75rem;min-height:64px;padding:.65rem .85rem;
  border:1px solid var(--border);border-radius:5px;background:var(--surface-2);color:var(--text);
  text-decoration:none}
ul.folderlist.list .folder a{min-height:52px}
ul.folderlist .folder a:hover{border-color:var(--text-muted)}
ul.folderlist .folder a:hover .name{text-decoration:underline}
ul.folderlist .folder a:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
ul.folderlist .ic{flex:none;display:inline-flex;align-items:center;justify-content:center;width:2.4rem;
  height:2.4rem;border-radius:6px;background:var(--orange-tint);color:var(--orange-text,var(--text));
  font-size:1.25rem}
ul.folderlist .text{display:flex;flex-direction:column;gap:.1rem;flex:1}
ul.folderlist .name{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
ul.folderlist .sub,ul.folderlist .where{font-size:.86rem;color:var(--text-muted);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
"""
