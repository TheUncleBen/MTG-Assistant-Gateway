"""The companion pages: my decks, one deck, history and activity, plus the app shell.

These are the pages the Android app wraps and the browser shows on a phone. They render on
the server from the same services the MCP tools and the JSON API use; the small amount of
JavaScript (the deck editor) lives in ``static/companion.js`` and talks to ``/api/v1``.
Internal links never open a new tab; external sites (Archidekt, Scryfall) may.
"""

from __future__ import annotations

import functools
import gzip
import html
import json
import logging
import mimetypes
import re
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from . import deck_stats
from .archidekt import FORMAT_NAMES, format_label
from .deckpage import (
    LIST_ORDERS,
    money,
)
from .decks import DeckError
from .pages import (
    _csrf,
    _err_code,
    csrf_form_ok,
    read_limited,
)
from .theme import (
    ICON_DATA_URL,
    STYLESHEETS,
    display_name,
    render,
    stylesheet_link,
)
from .theme import (
    esc as _esc,
)

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .reports import ReportService

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
ICON_HEADERS = {"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"}
# Pages with the editor load one script from /static; everything else keeps the default CSP.
DECK_CSP = (
    "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "manifest-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
DECKS_JSON_TIMEOUT = 25.0  # /api/decks/mine waits this long for a cold list at most
# Format names the settings and new-deck forms offer, one per Archidekt format id.
FORMAT_CHOICES = sorted({FORMAT_NAMES[i] for i in FORMAT_NAMES}, key=lambda n: format_label(n).lower())
EDITOR_CSP = (
    "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "manifest-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)


def _json_for_html(data: Any) -> str:
    """JSON safe inside a <script type=application/json> block: every '<' becomes \\u003c, which
    is still valid JSON, so neither '</script>' nor '<!--' can appear in the page source."""
    return json.dumps(data).replace("<", "\\u003c")


TEXT_PLAIN = "text/plain; charset=utf-8"


def _filename(name: str) -> str:
    """A download's file name: the deck's name in safe characters, at most 60."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:60]


def _num(value: Any, digits: int = 1) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _bars(values: dict[str, Any], *, label: str) -> str:
    """A tiny CSS bar chart (no script): one bar per key, height relative to the max."""
    items = [(k, int(v or 0)) for k, v in values.items()]
    top = max([v for _k, v in items] + [1])
    bars = "".join(
        f"<div class='bar' title='{_esc(k)}: {v}'>"
        f"<span style='height:{max(4, round(100 * v / top))}%'></span>"
        f"<em>{_esc(k)}</em><b>{v}</b></div>"
        for k, v in items
    )
    return f"<div class='bars' role='img' aria-label='{_esc(label)}'>{bars}</div>"


def _pips(pips: dict[str, Any], sources: dict[str, Any] | None) -> str:
    names = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colorless"}
    rows = []
    for col in ("W", "U", "B", "R", "G", "C"):
        p = pips.get(col)
        s = (sources or {}).get(col)
        if not p and not s:
            continue
        rows.append(
            f"<li><span class='pip pip-{col}' aria-hidden='true'></span><span>{names[col]}</span>"
            f"<b>{_num(p, 0)} pips</b>" + (f"<i>{_num(s, 0)} sources</i>" if s is not None else "") + "</li>"
        )
    return f"<ul class='plain pips'>{''.join(rows)}</ul>" if rows else ""


def stats_strip(stats: dict[str, Any] | None) -> str:
    if not stats:
        return ""
    bracket = stats.get("bracket_estimate") or {}
    tiles = [
        ("Cards", _num(stats.get("card_count"), 0)),
        ("Lands", _num(stats.get("land_count"), 0)),
        ("Avg MV", _num(stats.get("average_mana_value"))),
        ("Price", money(stats.get("price_total")) if stats.get("price_total") is not None else "–"),
        ("Bracket", f"~{bracket.get('bracket')}" if bracket.get("bracket") else "–"),
        ("Game changers", _num(len(stats.get("game_changers") or []), 0)),
    ]
    tile_html = "".join(f"<div class='tile'><b>{_esc(v)}</b><span>{_esc(k)}</span></div>" for k, v in tiles)
    curve = stats.get("mana_curve") or {}
    out = [f"<div class='tiles'>{tile_html}</div>"]
    if curve:
        out.append(
            "<div class='twocol'><div><h3>Mana curve</h3>"
            + _bars(curve, label="Mana curve")
            + "</div><div><h3>Colors</h3>"
            + _pips(stats.get("colour_pips") or {}, stats.get("mana_sources"))
            + "</div></div>"
        )
    # the deck page's verdict (deck_checks), also for a report stored before the engines merged
    problems = deck_stats.legality_problems_of(stats)
    if problems:
        out.append(
            "<p class='notice warn'>Not legal in this format: "
            + ", ".join(_esc(p if isinstance(p, str) else p.get("name")) for p in problems[:12])
            + ("…" if len(problems) > 12 else "")
            + "</p>"
        )
    if bracket.get("basis"):
        out.append(
            "<p class='muted small'>Bracket estimate from Archidekt's card flags only: "
            + "; ".join(_esc(b) for b in bracket["basis"])
            + ".</p>"
        )
    return "".join(out)


# Notices /decks/{id} shows for ?ok= and ?err=. Only these codes are accepted; anything else shows
# nothing, so a crafted link cannot put words of its choosing in the page's notice box.
DECK_OK_MESSAGES = {
    "report": "Report saved. See it under History.",
    "saved": "Saved to Archidekt. A snapshot from just before is under History if you want to undo.",
    "created": "Created on Archidekt.",
}
INDENT = "\u2003"  # an em space per folder level in folder pickers
# Notices the settings page shows after one of its hand actions (?ok=).
SETTINGS_OK_MESSAGES = {
    "cover": "Cover image saved to Archidekt. A snapshot from just before is under History.",
    "cover_auto": "Archidekt picks the cover image again. A snapshot from just before is under History.",
    "tag_added": "Tag added on Archidekt.",
    "tag_removed": "Tag removed on Archidekt.",
    "moved": "Deck moved to that folder on Archidekt.",
    "collaborator_added": "Collaborator added on Archidekt.",
    "collaborator_removed": "Collaborator removed on Archidekt.",
}
# Notices My decks shows (?ok=).
LIST_OK_MESSAGES = {
    "deleted": "Deck deleted on Archidekt. Its last snapshot is under History; when backups are on, a copy "
    "is in your backup folder.",
    "folder_created": "Folder created on Archidekt.",
    "folder_renamed": "Folder renamed on Archidekt.",
}
DECK_ERR_MESSAGES = {
    "report_failed": "The deck report could not be made. Try again later.",
    "invalid": "The deck report could not be made: that request was not valid.",
    "not_found": "Archidekt could not find that deck.",
    "rate_limited": "Too many requests for your account are still running. Wait for them to finish "
    "and try again.",
    "unavailable": "Archidekt or the research service is unavailable right now. Try again later.",
    "not_linked": "Your Archidekt account is not linked, or Archidekt no longer accepts the stored "
    "session. Relink it on the Account page.",
    "forbidden": "Archidekt refused access to that deck.",
}


# the deck list's View as choices (deckpage.deck_list_controls_html)
LIST_VIEWS = ("grid", "list")


def add_companion_routes(server: MCPServer, state: AppState, reports: ReportService) -> None:
    s = state.settings
    secure = s.public_url.startswith("https://")

    async def apply_now(sub: str, pid: str, *, ok: str, deck_id: str | None = None) -> Response:
        """A member's own action in the app (new deck, clone, settings) is their approval: the
        proposal it made is applied at once, with its snapshot, and the deck opens with a notice.
        When Archidekt refuses or writes are off, the proposal stays pending on its review page,
        which says why and keeps the Apply button for later."""
        try:
            result = await state.decks.apply(sub, pid, via="browser")
        except DeckError as exc:
            code = _err_code(exc.kind, proposal_exists=state.db.get_proposal(pid, sub) is not None)
            return RedirectResponse(f"/proposals/{pid}?err={code}", status_code=303)
        if result.get("state") == "applying":  # a large one carries on; its page shows the progress
            return RedirectResponse(f"/proposals/{pid}?ok=applying", status_code=303)
        made = result.get("result") if isinstance(result.get("result"), dict) else {}
        target = deck_id or str(made.get("deck_id") or result.get("deck_id") or "")
        if not target.isdigit():
            return RedirectResponse(f"/proposals/{pid}?ok=applied", status_code=303)
        return RedirectResponse(f"/decks/{target}?ok={ok}", status_code=303)

    decks = state.decks

    def page(
        title: str,
        body: str,
        *,
        sub: str,
        sid: str | None,
        status: int = 200,
        two_pane: bool = False,
        csp: str | None = None,
        current: str | None = None,
        scripts: bool = False,
        heading: bool = True,
        deck_css: bool = False,
        extra_scripts: tuple[str, ...] = (),
        extra_css: str = "",
    ) -> Response:
        user = state.db.get_user(sub) or {}
        admin = bool(s.admin_group and s.admin_group in (user.get("groups") or []))
        resp = render(
            title,
            body,
            site=s.server_name,
            status=status,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=admin,
            user=display_name(user),
            wide=two_pane,
            current=current,
            heading=heading,
            head_extra=(stylesheet_link("deck.css") if deck_css else "")
            + (f"<style>{extra_css}</style>" if extra_css else "")
            + (
                "<script src='/static/cardview.js' defer></script>"
                "<script src='/static/deck.js' defer></script>"
                if scripts
                else ""
            )
            + "".join(f"<script src='/static/{name}' defer></script>" for name in extra_scripts),
        )
        if csp:
            resp.headers["Content-Security-Policy"] = csp
        return resp

    EXPIRED = "<p class='notice error'>This form expired. Reload and try again.</p>"

    async def form(request: Request, limit: int = 16_384) -> dict[str, str]:
        from urllib.parse import parse_qs

        raw = await read_limited(request, limit)
        if raw is None:
            return {}
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}

    def check_csrf(sid: str | None, data: dict[str, str]) -> bool:
        return csrf_form_ok(s, sid, data)

    async def my_decks(
        sub: str, *, wait: float | None = None
    ) -> tuple[list[dict[str, Any]] | None, str | None, DeckError | None]:
        """(decks, problem, failure) where problem is a short message when the list is unavailable
        and failure the DeckError behind it (for the shared busy page). With ``wait``, decks is
        None when the list is cold and Archidekt has not answered in time: the page then renders a
        placeholder that decks.js fills from /api/decks/mine."""
        try:
            return await decks.list_decks_quick(sub, wait=wait), None, None
        except DeckError as exc:
            if exc.kind == "not_linked":
                return [], "not_linked", exc
            return [], str(exc), exc

    def arrange_decks(
        rows: list[dict[str, Any]], *, q: str, order: str, folder: str
    ) -> tuple[list[dict[str, Any]], list[str], int]:
        """Filter and sort the member's list as the /decks controls ask: (rows, folders, total)."""
        folders = sorted({str(d.get("folder")) for d in rows if d.get("folder")}, key=str.lower)
        total = len(rows)
        if q:
            rows = [d for d in rows if q.lower() in str(d.get("name", "")).lower()]
        if folder:
            rows = [d for d in rows if d.get("folder") == folder]
        if order == "name":
            rows.sort(key=lambda d: str(d.get("name", "")).lower())
        elif order == "created":
            rows.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
        elif order == "format":
            rows.sort(key=lambda d: (str(d.get("format_name") or ""), str(d.get("name", "")).lower()))
        return rows, folders, total

    def list_query(qp: Any, view: str | None = None) -> tuple[str, str, str, str]:
        """The deck list's query parameters, each limited to its known values: (q, order, view, folder).
        ``view``: the one the page already chose (the address's, else the remembered one)."""
        q = (qp.get("q") or "").strip()[:80]
        order = qp.get("order") if qp.get("order") in LIST_ORDERS else "updated"
        if view not in LIST_VIEWS:
            view = qp.get("view") if qp.get("view") in LIST_VIEWS else "grid"
        folder = (qp.get("folder") or "").strip()[:200]  # folder names run to 100 characters
        return q, order, view, folder

    def deck_list_skeleton(view: str) -> str:
        return (
            f"<ul class='plain decklist {_esc(view)} skeleton' aria-hidden='true'>"
            + "<li></li>" * 6
            + "</ul><p class='sr-only' role='status'>Loading your decks</p>"
            "<noscript><p class='muted'><a href='/decks'>Reload</a> to see your decks (this page fills "
            "itself with scripts on).</p></noscript>"
        )

    def link_prompt() -> str:
        return (
            "<div class='card'><h2>Link your Archidekt account</h2><p>Your decks appear here once Archidekt "
            "is linked. Public decks can still be opened by their Archidekt link.</p>"
            "<div class='actions'><a class='btn btn-primary' href='/account'>Link Archidekt</a></div></div>"
        )

    # The route modules import this module's helpers, so they are imported here, not at the top.
    from .companion_decks import add_deck_routes
    from .companion_history import add_history_routes
    from .companion_settings import add_settings_routes
    from .companion_shell import add_shell_routes
    from .companion_tools import add_deck_tool_routes

    # The helpers above, for the route modules (each takes what it uses). Registered in this
    # order, as before the split: /decks/new and /decks/open must come before /decks/{deck_id}.
    h = SimpleNamespace(
        EXPIRED=EXPIRED,
        apply_now=apply_now,
        arrange_decks=arrange_decks,
        check_csrf=check_csrf,
        deck_list_skeleton=deck_list_skeleton,
        decks=decks,
        form=form,
        link_prompt=link_prompt,
        list_query=list_query,
        my_decks=my_decks,
        page=page,
        s=s,
        secure=secure,
    )
    add_deck_routes(server, state, h)
    add_settings_routes(server, state, h)
    add_deck_tool_routes(server, state, reports, h)
    add_history_routes(server, state, reports, h)
    add_shell_routes(server, h)


_COMPRESSIBLE = (".js", ".css", ".svg", ".json", ".html")


@functools.cache
def _static_payload(rel: str) -> tuple[bytes, bytes | None, str]:
    """A /static file (or shared stylesheet) as served: its bytes, a gzipped copy for the text
    files (scripts and styles shrink to a quarter), and its type. Read and compressed once."""
    if rel in STYLESHEETS:
        body = STYLESHEETS[rel]().encode()
    else:
        body = (STATIC_DIR / rel).read_bytes()
    media_type = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    if rel.endswith(".js"):
        media_type = "text/javascript"
    gz = gzip.compress(body, 9, mtime=0) if rel.endswith(_COMPRESSIBLE) else None
    return body, gz if gz is not None and len(gz) < len(body) else None, media_type


SHORTCUT_ICON = {"src": "/static/gateway-icon-maskable-192.png", "sizes": "192x192", "type": "image/png"}


def short_name(name: str, limit: int = 12) -> str:
    """A home-screen name of at most ``limit`` characters that never ends mid-word: the whole
    name when it fits, else its first and last words ("MTG Assistant Gateway" gives "MTG
    Gateway"), else the longest run of leading words, else the first word cut."""
    words = (name or "").split()
    if not words:
        return "MTG Gateway"
    if len(name.strip()) <= limit:
        return name.strip()
    if len(words) > 2 and len(f"{words[0]} {words[-1]}") <= limit:
        return f"{words[0]} {words[-1]}"
    out = ""
    for w in words:
        cand = f"{out} {w}".strip()
        if len(cand) > limit:
            break
        out = cand
    return out or words[0][:limit]


def offline_page(site: str) -> str:
    """The service worker's offline page: the site's name and icon, the chosen theme (the
    worker sets data-theme from the mtg_theme cookie; otherwise the system's), one button.
    static/offline.js (answered by the worker itself) loads the page again once the connection
    is back (Q63)."""
    title = html.escape(f"You're offline · {site}")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<meta name='color-scheme' content='dark light'><title>{title}</title>"
        f"<link rel='icon' href='{ICON_DATA_URL}' type='image/svg+xml'>"
        "<style>:root{--bg:#181818;--text:#e3e3e3;--muted:#a8a8a8}"
        "@media (prefers-color-scheme: light){:root:not([data-theme=dark]){--bg:#f9fafb;--text:#383838;"
        "--muted:#5e5e5e}}"
        ":root[data-theme=light]{--bg:#f9fafb;--text:#383838;--muted:#5e5e5e}"
        "body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
        "font:16px/1.4 Lato,'Helvetica Neue',Arial,sans-serif;background:var(--bg);color:var(--text);"
        "padding:1rem}"
        "main{max-width:28rem;text-align:center}h1{font-size:1.4rem;margin:.75rem 0 .5rem}"
        ".site{color:var(--muted);font-weight:700;font-size:.9rem;margin:0}"
        "img{width:64px;height:64px;border-radius:14px}"
        "a{display:inline-block;margin-top:1rem;padding:.6rem 1.2rem;border-radius:5px;background:#fa890d;"
        "color:#111;font-weight:700;text-decoration:none}</style></head><body><main>"
        f"<img src='{ICON_DATA_URL}' alt=''><p class='site'>{html.escape(site)}</p>"
        "<h1>You're offline</h1><p>The gateway can't be reached right now. If you just pressed a button, "
        "nothing was sent. Check your connection, then try again.</p>"
        "<a href=''>Try again</a></main><script src='/static/offline.js'></script></body></html>"
    )


OFFLINE_PAGE = offline_page("MTG Assistant Gateway")
# The offline page's script, which the service worker answers itself (companion_shell, /sw.js).
OFFLINE_SCRIPT = (STATIC_DIR / "offline.js").read_text(encoding="utf-8")


# Audit event names as sentences for the member's activity page (anything unlisted is shown
# with its underscores as spaces, first letter capitalised).
EVENT_LABELS = {
    "browser_login": "Signed in on the website",
    "login_ok": "Signed in",
    "login_denied": "Sign-in refused",
    "login_rejected_group": "Sign-in refused: not in an allowed group",
    "login_rejected_disabled": "Sign-in refused: account disabled",
    "login_rejected_issuer": "Sign-in refused: unexpected identity provider",
    "tokens_issued": "An assistant connected",
    "tokens_revoked": "An assistant's access was revoked",
    "refresh_rejected": "An assistant's expired access was refused",
    "refresh_reuse_detected": "A reused assistant token was refused",
    "code_reuse_detected": "A reused sign-in code was refused",
    "proposal_created": "Proposal created",
    "proposal_applied": "Proposal applied",
    "proposal_rejected": "Proposal rejected",
    "proposal_failed": "Proposal failed",
    "archidekt_linked": "Archidekt account linked",
    "archidekt_unlinked": "Archidekt account unlinked",
    "archidekt_link_failed": "Archidekt link failed",
    "archidekt_link_expired": "Archidekt link expired",
    "archidekt_session_refreshed": "Archidekt sign-in renewed",
    "collection_added": "Cards added to the collection",
    "collection_removed": "Cards removed from the collection",
    "collection_changed": "Collection changed",
    "deck_voted": "Liked a deck",
    "deck_bookmarked": "Bookmarked a deck",
    "deck_commented": "Commented on a deck",
    "user_followed": "Followed a user",
    "report_created": "Deck simulation run",
    "scan_session_saved": "Scan saved",
    "scan_session_deleted": "Scan deleted",
    "folder_created": "Folder created",
    "groups_changed": "Your groups changed",
    "membership_revoked": "Membership removed",
    "membership_unverifiable": "Membership could not be checked",
    "disabled_user_refused": "Request refused: account disabled",
    "not_in_group_refused": "Request refused: not in an allowed group",
}
DETAIL_LABELS = {
    "proposal_id": "proposal",
    "deck_id": "deck",
    "via": "via",
    "error": "error",
    "archidekt_username": "Archidekt account",
    "by": "by",
}


def event_label(event: str) -> str:
    return EVENT_LABELS.get(event) or event.replace("_", " ").capitalize()


def event_detail_html(detail: dict[str, Any]) -> str:
    """The details of an audit row in words: the deck and the proposal become links."""
    bits = []
    for k, label in DETAIL_LABELS.items():
        v = detail.get(k)
        if v in (None, ""):
            continue
        if k == "deck_id":
            name = detail.get("deck_name")
            text = _esc(name) if name else f"deck {_esc(v)}"
            bits.append(f"<a href='/decks/{quote(str(v), safe='')}'>{text}</a>")
        elif k == "proposal_id":
            bits.append(f"<a href='/proposals/{quote(str(v), safe='')}'>proposal {_esc(str(v)[:8])}</a>")
        else:
            bits.append(f"{label} {_esc(v)}")
    return " · ".join(bits)


__all__ = ["add_companion_routes", "stats_strip"]
