"""Archidekt's own records of a deck, beside the gateway's History: its change log, and the rows of
cards taken out of the deck that Archidekt still keeps, which the member can remove for good.

* **The change log** is ``GET /api/decks/{id}/logs/`` (the parity report: seen in archidekt.com's
  scripts). The layout of its answer is not recorded, so each entry is shown with the fields the
  gateway recognises (archidekt.log_entry), and an answer it can't read says so. Only the
  member's own decks: the page and the ``list_snapshots`` tool's ``archidekt`` switch.
* **Removed card rows**: Archidekt keeps a card taken out of a deck as a row with ``deletedAt``
  set in the deck itself (archidekt.removed_rows). ``DELETE /api/decks/relations/{id}/`` removes
  one for good. That is a write with no undo (the gateway's snapshots don't hold these rows), so
  it is a proposal (actions.py ``purge_removed``) in the destructive tier: only the member's own
  press on its review page applies it, after the confirm box, whatever the approval mode.

Neither request has been exercised live by the gateway yet: both are on the owner's live check."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from . import ui
from .archidekt import removed_rows
from .decks import DeckError, _clean_deck_id, current_client
from .digits import is_digits
from .theme import esc, time_html

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .decks import DeckService

PAGE = "/history/archidekt"
TITLE = "Archidekt's change log"
LOG_UNREAD = (
    "Archidekt didn't show this deck's change log just now, so it isn't listed here. The gateway's own "
    "History of the deck is unchanged."
)
CSS = (
    ".alog{max-width:62rem}.alog .ui-card{margin-bottom:1rem}"
    ".alog ol.entries,.alog ul.removed{list-style:none;margin:0;padding:0}"
    ".alog ol.entries li,.alog ul.removed li{display:flex;flex-wrap:wrap;gap:.25rem .75rem;"
    "align-items:center;padding:.6rem 0;border-top:1px solid var(--border-soft)}"
    ".alog ol.entries li:first-child,.alog ul.removed li:first-child{border-top:0}"
    ".alog .what{flex:1 1 16rem;min-width:0;overflow-wrap:anywhere}"
    ".alog .alog-meta{color:var(--text-muted);font-size:.9rem}"
    ".alog ul.removed form{margin:0 0 0 auto}"
    ".alog ul.removed button{min-height:44px}"
)


def when_ts(text: str) -> int | None:
    """Archidekt's ISO time as a timestamp, None when it is not one."""
    try:
        return int(datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


async def read(decks: DeckService, sub: str, deck_id: str, page: int = 1) -> dict[str, Any]:
    """One of the member's decks: Archidekt's change log (one page) and its removed card rows.
    ``log`` is None with ``log_error`` saying why when the log could not be read; the removed
    rows come from the deck read itself."""
    deck = await decks.get_own_deck(sub, _clean_deck_id(deck_id))
    log: dict[str, Any] | None = None
    error = ""
    try:
        log = await decks._call(sub, lambda token, *_: decks.client.deck_logs(token, deck.id, page))
    except DeckError as exc:
        if exc.kind not in ("changed", "not_found", "contract", "unavailable", "forbidden"):
            raise
        error = LOG_UNREAD
    return {
        "deck_id": deck.id,
        "deck_name": deck.name,
        "page": max(1, int(page)),
        "log": log,
        "log_error": error,
        "removed": removed_rows(deck),
    }


def _entry_html(e: dict[str, Any]) -> str:
    ts = when_ts(e["when"])
    when = time_html(ts) if ts else esc(e["when"])
    who = f" · {esc(e['who'])}" if e["who"] else ""
    what = esc(e["what"]) or "<span class='alog-meta'>No words for this entry</span>"
    return f"<li><span class='what'>{what}</span><span class='alog-meta'>{when}{who}</span></li>"


def _removed_html(r: dict[str, Any], deck_id: str, csrf_in: str) -> str:
    ts = when_ts(r["removed_at"])
    qty = f"{r['quantity']}× " if r["quantity"] else ""
    meta = [esc(", ".join(r["categories"]))] if r["categories"] else []
    if ts:
        meta.append(f"taken out {time_html(ts)}")
    return (
        f"<li><span class='what'>{qty}<strong>{esc(r['name'])}</strong></span>"
        f"<span class='alog-meta'>{' · '.join(meta)}</span>"
        f"<form method='post' action='{PAGE}/remove'>{csrf_in}"
        f"<input type='hidden' name='deck_id' value='{esc(deck_id)}'>"
        f"<input type='hidden' name='relation_id' value='{int(r['relation_id'])}'>"
        f"<button class='btn btn-outline-danger'>Remove for good…</button></form></li>"
    )


def page_html(got: dict[str, Any], csrf_in: str) -> str:
    did = got["deck_id"]
    back = ui.cap_link("← Back to the deck's History", f"/history?deck_id={quote(did)}")
    intro = (
        f"<p class='ui-sub'>Archidekt's own record of changes to “{esc(got['deck_name'])}”, as Archidekt "
        "keeps it. The gateway's own snapshots and changes are on the deck's History.</p>"
    )
    log = got["log"]
    if log is None:
        log_html = f"<p class='notice warn'>{esc(got['log_error'])}</p>"
    elif not log["entries"]:
        log_html = "<p class='muted'>Archidekt has no changes logged for this deck.</p>"
    else:
        items = "".join(_entry_html(e) for e in log["entries"])
        older = (
            ui.cap_link("Older entries", f"{PAGE}?deck_id={quote(did)}&page={got['page'] + 1}")
            if log["has_more"]
            else ""
        )
        newer = (
            ui.cap_link("Newer entries", f"{PAGE}?deck_id={quote(did)}&page={got['page'] - 1}")
            if got["page"] > 1
            else ""
        )
        log_html = f"<ol class='entries'>{items}</ol>" + (
            f"<p class='ui-actions'>{newer}{older}</p>" if older or newer else ""
        )
    removed = got["removed"]
    if removed:
        rows = "".join(_removed_html(r, did, csrf_in) for r in removed)
        removed_html = (
            "<p class='small'>Archidekt keeps a row for each card taken out of the deck. Removing one for "
            "good deletes that row on Archidekt; Archidekt has no undo, and the gateway's snapshots don't "
            "hold these rows. Each removal is a proposal you approve on its review page.</p>"
            f"<ul class='removed'>{rows}</ul>"
        )
    else:
        removed_html = "<p class='muted'>Archidekt keeps no rows of cards taken out of this deck.</p>"
    return (
        f"<div class='alog'><p class='hy-back'>{back}</p>"
        f"<h1>{TITLE}</h1>{intro}"
        "<section class='ui-card' aria-labelledby='alog-h'>"
        f"<h2 id='alog-h' class='ui-section'>Changes Archidekt logged</h2>{log_html}</section>"
        "<section class='ui-card' aria-labelledby='arem-h'>"
        f"<h2 id='arem-h' class='ui-section'>Cards taken out that Archidekt keeps</h2>{removed_html}"
        "</section></div>"
    )


def history_link_html(deck_id: str) -> str:
    """The deck History's way to Archidekt's own log, labelled as Archidekt's."""
    return (
        "<div class='ui-card hy-alog'><h2 class='ui-section'>Archidekt's own change log</h2>"
        "<p class='small'>Archidekt keeps its own log of changes to this deck, and the rows of cards "
        "taken out of it. The list above is the gateway's record.</p>"
        + ui.cap_link("Open Archidekt's change log", f"{PAGE}?deck_id={quote(deck_id)}")
        + "</div>"
    )


def add_routes(server: MCPServer, state: AppState, h: SimpleNamespace) -> None:
    from .pages import BROWSER_CLIENT_ID, _csrf, browser_session, login_redirect_here

    decks = h.decks
    page = h.page

    def problem(sub: str, sid: str | None, exc: DeckError, back: str) -> Response:
        return page(
            TITLE,
            f"<div class='card'><p class='notice error'>{esc(exc)}</p>"
            f"<div class='actions'>{ui.cap_link('Back to History', back)}</div></div>",
            sub=sub,
            sid=sid,
            status=404 if exc.kind == "not_found" else 400,
        )

    @server.custom_route(PAGE, methods=["GET"], include_in_schema=False)
    async def archidekt_log(request: Request) -> Response:
        q = request.query_params
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request)
        raw_page = q.get("page") or "1"
        num = int(raw_page) if is_digits(raw_page) and 0 < int(raw_page) <= 500 else 1
        try:
            got = await read(decks, sub, q.get("deck_id") or "", num)
        except DeckError as exc:
            return problem(sub, sid, exc, "/history")
        csrf_in = f"<input type='hidden' name='csrf' value='{esc(_csrf(h.s, sid))}'>"
        return page(
            TITLE,
            page_html(got, csrf_in),
            sub=sub,
            sid=sid,
            current="/history",
            extra_css=CSS,
            heading=False,
        )

    @server.custom_route(PAGE + "/remove", methods=["POST"], include_in_schema=False)
    async def remove_row(request: Request) -> Response:
        """Remove for good…: a proposal (destructive), then its review page, where the member's
        press on Apply opens the confirm box. Nothing is sent to Archidekt here."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/history")
        data = await h.form(request)
        if not h.check_csrf(sid, data):
            return page(
                TITLE,
                "<p class='notice error'>This form expired. Reload and try again.</p>",
                sub=sub,
                sid=sid,
                status=403,
            )
        current_client.set(BROWSER_CLIENT_ID)
        deck_id = str(data.get("deck_id") or "")
        try:
            p = await decks.propose_action(
                sub, "purge_removed", deck_id=deck_id, relation_id=data.get("relation_id")
            )
        except DeckError as exc:
            return problem(sub, sid, exc, f"{PAGE}?deck_id={quote(deck_id)}")
        return RedirectResponse(f"/proposals/{p['proposal_id']}", status_code=303)
