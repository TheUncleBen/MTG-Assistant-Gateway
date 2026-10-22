"""My card models: the member's own saved card descriptions as a box (ui.box) opened from the
Goldfish page and the report page, wherever the saved descriptions are mentioned, and as the
page /history/models for a browser without scripts.

Each row is the card's name (the shared card menu: right-click, press and hold, ⋮, Shift+F10
through static/menus.js's cardLinkOf), when the description was added or last changed (ui.when)
and Remove, which asks first. The list is searched by card name in the page (static/cardmodels.js).
Only the member's own descriptions are ever listed (card_models.KeptModels.mine). No hand editing:
a description is written by the assistant and checked by the gateway.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from . import ui
from .theme import esc, icon

BOX_ID = "my-models"
TITLE = "My card models"
PAGE = "/history/models"
REMOVE_ACTION = "/history/models/remove"
SUB_TEXT = (
    "The card descriptions your assistant saved for your solo tests. They play only in your own "
    "solo tests, and only you see them here."
)
EMPTY_TEXT = (
    "No saved descriptions yet. When a solo test plays a card without its effect, your assistant can "
    "describe the card for it; the descriptions that pass the gateway's check are kept here."
)
REMOVE_TEXT = (
    "Later solo tests play “{card}” without its effect again, until a new description is saved. "
    "The reports that already played it keep their numbers."
)
# where a Remove may send the member back: their own pages that show the ?models= notice
BACK_PREFIXES = ("/history/", "/decks/")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).casefold()).strip("-") or "card"


def safe_back(back: str | None, default: str = PAGE) -> str:
    """The page a Remove returns to: one of the member's own pages, never an outside address."""
    b = str(back or "")
    if b.startswith(BACK_PREFIXES) and not b.startswith("//") and "\n" not in b and len(b) <= 200:
        return b
    return default


def opener(*, kind: str = "plain", cls: str = "") -> str:
    """The button that opens the box (a link to the page without scripts)."""
    return ui.box_opener(TITLE, PAGE, BOX_ID, kind=kind, cls=cls, icon_name="box")


def _when(m: dict[str, Any]) -> str:
    added, changed = m.get("added_at"), m.get("updated_at")
    if changed and added and int(changed) != int(added):
        return f"changed {ui.when(changed)}"
    return f"added {ui.when(added or changed)}" if (added or changed) else ""


def _row(m: dict[str, Any]) -> str:
    name = str(m.get("name") or "")
    when = _when(m)
    return (
        f"<li data-name='{esc(name.casefold())}'>"
        f"<button type='button' class='cardlink' data-card='{esc(name)}' data-href='/cards?q={quote(name)}'>"
        f"<span class='nm'>{esc(name)}</span></button>"
        + (f"<span class='mm-when'>{when}</span>" if when else "")
        + f"<a class='btn btn-outline-danger small mm-rm' href='{PAGE}?remove={quote(name)}' "
        f"data-remove-model='{esc(name)}' aria-label='Remove the saved description of {esc(name)}'>"
        f"{icon('trash')} Remove</a></li>"
    )


def box_html(
    models: list[dict[str, Any]], *, csrf: str, back: str, open: bool = False, remove: str = ""
) -> str:
    """The box (hidden until opened; ``open`` for the page itself), with the one hidden form that
    removes (its card is filled in by the row's Remove), the search box and the rows. ``remove``
    names a card whose confirm box is drawn open: the page's own Remove without scripts."""
    body = [f"<p class='ui-sub'>{esc(SUB_TEXT)}</p>"]
    if models:
        body.append(
            "<div class='field mm-search'><label for='mm-q'>Search by card name</label>"
            "<input id='mm-q' type='search' data-models-filter placeholder='Card name' autocomplete='off'>"
            "</div>"
            f"<p class='sr-only' data-models-count role='status' aria-live='polite'></p>"
            f"<ul class='mm-list' data-models-list>{''.join(_row(m) for m in models)}</ul>"
            "<p class='muted mm-none' data-models-none hidden>No saved description matches that name.</p>"
        )
    else:
        body.append(f"<p class='mm-empty'>{esc(EMPTY_TEXT)}</p>")
    body.append(
        f"<form id='mm-rm-f' method='post' action='{REMOVE_ACTION}' hidden>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='back' value='{esc(back)}'>"
        "<input type='hidden' name='card' value=''></form>"
    )
    out = ui.box(
        BOX_ID,
        TITLE,
        "".join(body),
        actions=[ui.box_button("Close", cancel=True)],
        open=open,
        close_href=back,
        cls="mm-box",
    )
    if remove:
        out += confirm_box_html(remove, csrf=csrf, back=back)
    return out


def confirm_box_html(card: str, *, csrf: str, back: str) -> str:
    """The confirm for one card's Remove, drawn open on the page (the no-script path; with scripts
    static/cardmodels.js asks with MtgBox.confirm instead)."""
    fid = f"rm-mine-{slug(card)}-f"
    body = (
        f"<form id='{fid}' method='post' action='{REMOVE_ACTION}'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='back' value='{esc(back)}'>"
        f"<input type='hidden' name='card' value='{esc(card)}'></form>"
        f"<p>{esc(REMOVE_TEXT.format(card=card))}</p>"
    )
    return ui.box(
        f"rm-mine-{slug(card)}",
        f"Remove the saved description of “{card}”?",
        body,
        actions=[
            ui.box_button("Keep it", cancel=True),
            ui.box_button("Remove", kind="danger-solid", form=fid, icon_name="trash"),
        ],
        open=True,
        close_href=PAGE,
    )


CSS = """
.mm-box .ui-sub{margin:0 0 .75rem}
.mm-search{margin:0 0 .5rem}
.mm-list{list-style:none;margin:0;padding:0}
.mm-list li{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem .75rem;min-height:44px;
  padding:.15rem 0;border-top:1px solid var(--border-soft)}
.mm-list li:first-child{border-top:0}
.mm-list li[hidden]{display:none}
.mm-list .cardlink{flex:1 1 10rem;min-width:0;min-height:44px;display:inline-flex;align-items:center;
  background:none;border:0;padding:0;margin:0;font:inherit;font-weight:700;color:var(--link);
  text-align:left;cursor:pointer;overflow-wrap:anywhere}
.mm-list .cardlink:hover .nm{text-decoration:underline}
.mm-when{color:var(--text-muted);font-size:.9rem;white-space:nowrap}
.mm-list .mm-rm{margin:0;min-height:44px}
.mm-empty{margin:0}
.gf-models,.rv-models{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin:.5rem 0 0}
.gf-models .btn,.rv-models .btn{margin:0}
"""
