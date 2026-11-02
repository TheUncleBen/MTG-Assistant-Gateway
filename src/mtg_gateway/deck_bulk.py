"""Select on My decks (0.7.46 audit QG-17): a tick box on every deck tile of the member's own,
and a bar for what a tile's menu offers that makes sense in bulk: Move to folder… and
Visibility…. Each deck goes through the same write a single one does (decks.move_deck; a details
proposal applied at once as the member's own press), one deck at a time, read back; a deck that
could not be changed is named and the rest still go. Delete stays the deck's own box (its name
typed, one deck at a time): the bar never deletes. Works without scripts: the boxes open as
pages and the bar's form posts to /decks/bulk."""

from __future__ import annotations

import contextlib
import logging
import secrets
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from . import ui
from .deckpage import folder_options_html
from .decks import DeckError, current_client
from .pages import BROWSER_CLIENT_ID, browser_session, csrf_form_ok, login_redirect_here, read_limited
from .theme import esc, icon

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

log = logging.getLogger(__name__)

MAX_BULK_DECKS = 50  # the bar changes at most this many decks in one press (one write each)
NOTICE_TTL = 300.0  # seconds a press's notice waits for the page the redirect lands on
NOTICES_MAX = 2_000
BULK_FORM = "decks-bulk"
VISIBILITIES = (("public", "Public"), ("unlisted", "Unlisted"), ("private", "Private"))
STOP_KINDS = ("not_linked", "auth", "writes_disabled", "unavailable", "rate_limited", "busy")

BULK_CSS = """
.decks-bulkform{display:none}
ul.decklist .deck{position:relative}
body.decks-selectable:not(.decks-selecting) ul.decklist .deck .chk{display:none}
ul.decklist .deck .chk{display:grid;place-items:center;width:44px;height:44px;cursor:pointer;
  position:absolute;top:0;left:0;z-index:2;border-radius:var(--radius) 0 var(--radius) 0;
  background:var(--surface)}
ul.decklist .deck .chk input{width:20px;height:20px;margin:0;accent-color:var(--orange-ctl)}
ul.decklist .deck.sel a{outline:3px solid var(--orange-ctl);outline-offset:-3px}
ul.decklist.grid .deck .chk ~ a .thumb{padding-left:0}
.decks-select[aria-pressed=true]{background:var(--orange-tint);color:var(--orange-text)}
.decks-bulk-notice form.inline{display:inline-flex;margin:0 0 0 .5rem;vertical-align:middle}
.mtgbox .body .radios{display:flex;flex-direction:column;gap:0}
.mtgbox .body .radios label{display:inline-flex;align-items:center;gap:.5rem;min-height:44px}
.mtgbox .body .radios input{width:20px;height:20px;margin:0;accent-color:var(--orange-ctl)}
"""


def tick_html(deck_id: str, name: str) -> str:
    """The tile's tick box: the whole 44 px cell is the target."""
    return ui.check_cell("deck_id", deck_id, f"Select {name}", form=BULK_FORM)


def select_button_html() -> str:
    """Select: drawn hidden, the page's script shows it (it opens the select mode)."""
    return (
        "<button type='button' class='btn decks-select' aria-pressed='false' hidden>"
        f"{icon('check')}Select</button>"
    )


def bulk_html(csrf: str, folders: dict[str, Any] | None, here: str) -> str:
    """The hidden form, the bar (hidden until Select opens the select mode) and its two boxes."""
    form = (
        f"<form method='post' action='/decks/bulk' id='{BULK_FORM}' class='decks-bulkform'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='next' value='{esc(here)}'></form>"
    )
    actions = [
        "<button type='button' class='btn decks-all'>Select all</button>",
        ui.box_opener("Move to folder…", "/decks?bulk=move", "decks-bulk-move"),
        ui.box_opener("Visibility…", "/decks?bulk=vis", "decks-bulk-vis"),
        "<button type='button' class='btn decks-clear'>Cancel</button>",
    ]
    bar = ui.selection_bar("0 selected", actions, label="Selected decks", hidden=True)
    move = ui.box(
        "decks-bulk-move",
        "Move to a folder",
        "<p><span data-bulk-count>The selected decks</span> go into this folder on Archidekt.</p>"
        f"<label class='field'><span>Folder</span><select name='folder_id' form='{BULK_FORM}'>"
        f"{folder_options_html(folders, '')}</select></label>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Move", kind="primary", form=BULK_FORM, name="do", value="move"),
        ],
        label="Move to a folder",
    )
    radios = "".join(
        f"<label><input type='radio' name='visibility' value='{v}' form='{BULK_FORM}'"
        f"{' checked' if v == 'private' else ''}> {label}</label>"
        for v, label in VISIBILITIES
    )
    vis = ui.box(
        "decks-bulk-vis",
        "Set who can see them",
        "<p><span data-bulk-count>The selected decks</span> get this visibility on Archidekt. Unlisted "
        "decks open by their link but are not listed; private ones are yours alone.</p>"
        f"<div class='radios' role='radiogroup' aria-label='Visibility'>{radios}</div>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Set visibility", kind="primary", form=BULK_FORM, name="do", value="visibility"),
        ],
        label="Set who can see them",
    )
    return form + bar + move + vis


def _decks(n: int) -> str:
    return f"{n} deck" if n == 1 else f"{n} decks"


def add_bulk_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings
    decks = state.decks
    notices: dict[str, tuple[str, float, str, str]] = {}  # token -> (sub, when, kind, text)

    def keep(sub: str, kind: str, text: str) -> str:
        now = time.monotonic()
        for k in [k for k, (_, t, _, _) in notices.items() if now - t > NOTICE_TTL]:
            notices.pop(k, None)
        while len(notices) >= NOTICES_MAX:
            notices.pop(next(iter(notices)))
        token = secrets.token_urlsafe(12)
        notices[token] = (sub, now, kind, text)
        return f"{'ok' if kind == 'ok' else 'err'}=bulk&t={token}"

    def notice_html(sub: str, token: str | None) -> str:
        """The bar's notice on the My decks page that follows (read once)."""
        hit = notices.pop(token or "", None)
        if hit is None or hit[0] != sub or time.monotonic() - hit[1] > NOTICE_TTL:
            return ""
        kind = "ok" if hit[2] == "ok" else "error"
        return ui.notice(hit[3], kind, role="status" if kind == "ok" else "alert", cls="decks-bulk-notice")

    state.deck_bulk_notice = notice_html  # type: ignore[attr-defined]

    async def form(request: Request) -> dict[str, list[str]]:
        raw = await read_limited(request, 200_000)
        if raw is None:
            return {}
        return parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)

    async def set_visibility(sub: str, deck_id: str, want: str) -> bool:
        """One deck's visibility, the way the Settings page saves it: a details proposal applied
        at once as the member's own press. Already so: nothing sent, still done."""
        private = want == "private"
        unlisted = want == "unlisted"
        try:
            p = await decks.propose_deck_details(sub, deck_id, {"private": private, "unlisted": unlisted})
        except DeckError as exc:
            if exc.kind == "invalid" and "nothing would change" in str(exc):
                return True
            raise
        pid = p["proposal_id"]
        try:
            result = await decks.apply(sub, pid, via="browser")
        except DeckError:
            with contextlib.suppress(DeckError):
                decks.reject(sub, pid, via="browser")
            raise
        if result.get("state") != "applied":
            raise DeckError("failed", "Archidekt has not taken the change yet; see the deck's review page.")
        return True

    @server.custom_route("/decks/bulk", methods=["POST"], include_in_schema=False)
    async def decks_bulk(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/decks")
        data = await form(request)
        flat = {k: v[0] for k, v in data.items() if v}
        back = "/decks"
        nxt = flat.get("next") or ""
        if nxt.startswith("/decks") and "//" not in nxt and "\\" not in nxt:
            back = nxt[:300]
        sep = "&" if "?" in back else "?"
        if not csrf_form_ok(s, sid, flat):
            return RedirectResponse(f"{back}{sep}err=expired", status_code=303)
        doing = flat.get("do", "")
        ids = [d for d in data.get("deck_id", []) if d.isdigit() and len(d) <= 12]
        if doing not in ("move", "visibility"):
            return RedirectResponse(f"{back}{sep}err=invalid", status_code=303)
        if not ids:
            return RedirectResponse(f"{back}{sep}{keep(sub, 'error', 'Tick a deck first.')}", status_code=303)
        if len(ids) > MAX_BULK_DECKS:
            text = (
                f"Select changes at most {MAX_BULK_DECKS} decks at a time. Tick fewer and go on with "
                "the rest after."
            )
            return RedirectResponse(f"{back}{sep}{keep(sub, 'error', text)}", status_code=303)
        folder_id: int | None = None
        folder_name = "the top level"
        want = flat.get("visibility", "")
        if doing == "move":
            raw = flat.get("folder_id", "")
            if raw:
                if not raw.isdigit():
                    return RedirectResponse(f"{back}{sep}err=invalid", status_code=303)
                folder_id = int(raw)
        elif want not in {v for v, _ in VISIBILITIES}:
            return RedirectResponse(f"{back}{sep}err=invalid", status_code=303)
        current_client.set(BROWSER_CLIENT_ID)
        done = 0
        failed: list[str] = []
        try:
            if folder_id is not None:
                _info, folder = await decks._folder_in(sub, folder_id)
                folder_name = f"“{folder['name']}”"
            for deck_id in ids:
                try:
                    if doing == "move":
                        await decks.move_deck(sub, deck_id, folder_id)
                    else:
                        await set_visibility(sub, deck_id, want)
                    done += 1
                except DeckError as exc:
                    if exc.kind in STOP_KINDS:
                        raise
                    failed.append(f"{deck_id} ({exc})")
        except DeckError as exc:
            text = str(exc)
            if done:
                text = f"{_decks(done)} changed before Archidekt stopped answering: {text}"
            return RedirectResponse(f"{back}{sep}{keep(sub, 'error', text)}", status_code=303)
        if doing == "move":
            text = f"{_decks(done)} moved to {folder_name}."
        else:
            text = f"Visibility set on {_decks(done)}."
        if failed:
            text += f" {len(failed)} could not be changed: " + "; ".join(failed[:5])
            if len(failed) > 5:
                text += f"; and {len(failed) - 5} more"
            text += "."
        decks.reread_deck_list(sub)  # the list shows the move at once
        return RedirectResponse(f"{back}{sep}{keep(sub, 'ok' if done else 'error', text)}", status_code=303)
