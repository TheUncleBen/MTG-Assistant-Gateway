"""The Alerts page: the one place for every alert (0.7.47, the owner's notifications redesign).

The bell in the top bar and Alerts on the rail open it. It lists, newest first, what waits for
the member's press now (read live, one row per deck: those rows leave once the changes are
applied or rejected) and every kept alert (notify.KINDS: a report that finished, a price alert,
Archidekt's alerts, a new assistant, a summary, an admin's gateway alerts). Each row opens its
page and is marked read there (/notifications/open/<id>); its ⋮ menu (also a right-click or a
press and hold, static/bell.js) marks it read or unread, or dismisses it. Mark all read and Clear
all act on every row; Undo in the toast takes either back. The Type and Show filters and the
search apply as a GET. Read and dismissed are kept on the server, so a reload, another tab or
another device shows the same, and the phone's message for an alert that was read or dismissed
anywhere is withdrawn (notify.Notifier.withdraw_alerts).

Archidekt's alerts are read from Archidekt and kept here by their fingerprint
(alerts_push.mirror): reading or dismissing one here changes nothing on archidekt.com, since the
gateway knows no request that would.

The count on the bell is the member's alerts that came since they last opened this list (as
Archidekt's own bell counts), asked after the page has loaded (static/bell.js).
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import alerts_push, notify, prefs, profile_page, ui
from .decks import DeckError
from .digits import is_digits
from .pages import (
    browser_session,
    csrf_form_ok,
    csrf_header_ok,
    login_redirect_here,
    read_limited,
    section_href,
)
from .theme import esc, icon

NO_STORE = {"Cache-Control": "no-store"}
BOX_ROWS = 8  # rows the bell's box shows; the page shows them all
SHOW_CHOICES = (("", "All"), ("unread", "Unread"))
ACTIONS = ("read", "unread", "dismiss", "undo", "read_all", "clear_all")
FOOT = (
    "Alerts from Archidekt are read from Archidekt: marking them read or dismissing them here "
    "doesn't change them on archidekt.com. Dismissed alerts are kept 30 days for Undo."
)
# where the kinds of Archidekt's own alerts are chosen (the gateway's are under Settings)
ARK_CHOICE = "<a class='btn btn-plain' href='/account/details/email'>Email and alerts on Archidekt</a>"
EMPTY = "You're all caught up."
EMPTY_HINT = (
    "Changes waiting for you, finished reports, price alerts, new assistants and your Archidekt alerts "
    "show here."
)
NO_MATCH = "No alerts match."
# what each action says in its toast (static/bell.js) and in the page's notice without scripts
DONE_WORDS = {
    "read": "Marked read.",
    "unread": "Marked unread.",
    "dismiss": "Dismissed.",
    "undo": "Back in your alerts.",
    "read_all": "All marked read.",
    "clear_all": "All cleared.",
}
# the mark at the end of a no-script action's redirect (?done=...)
DONE_CODES = frozenset(DONE_WORDS)
# A count of Archidekt's unseen alerts the list was last read for, per member: a different count
# reads the list again (and keeps its alerts here), the same one never does, so a bell asked on
# every page costs Archidekt one count read a minute at most (profile_page.COUNT_TTL).
_MIRRORED: dict[str, int] = {}


def kinds_for(admin: bool) -> list[notify.Kind]:
    return [k for k in notify.KINDS.values() if admin or not k.admin]


def type_choices(admin: bool) -> tuple[tuple[str, str], ...]:
    return (("", "Every type"),) + tuple((k.key, k.name) for k in kinds_for(admin))


def _has_words(q: str, *parts: Any) -> bool:
    return not q or any(q in str(p or "").casefold() for p in parts)


def waiting_rows(waiting: dict[str, tuple[str, int, str]]) -> list[dict[str, Any]]:
    """What waits for the member's press now, one row a deck (DeckService.waiting_by_deck)."""
    out = []
    for deck_id, (name, count, pid) in sorted(waiting.items(), key=lambda kv: kv[1][0].lower()):
        if count <= 0:
            continue
        title = notify.new_decks_title(count) if deck_id == "new" else notify.waiting_title(count, name)
        if count == 1 and pid:
            href = f"/proposals/{pid}"
        else:
            href = "/proposals" if deck_id == "new" else f"/decks/{deck_id}"
        out.append(
            {
                "id": None,
                "kind": "waiting",
                "title": title,
                "message": notify.waiting_text(count),
                "href": href,
                "created_at": None,
                "read": False,
            }
        )
    return out


def kept_rows(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": a["id"],
            "kind": a.get("kind") or notify.kind_of(a.get("event") or ""),
            "title": a["title"],
            "message": a["message"],
            "href": f"/notifications/open/{a['id']}",
            "created_at": a["created_at"],
            "read": a.get("read_at") is not None,
            "away": safe_path(a.get("path") or "").startswith(alerts_push.ARCHIDEKT_SITE),
        }
        for a in alerts
    ]


def safe_path(path: str) -> str:
    """Where an alert may open: a page here, or Archidekt's own site (news and contests); else
    the Alerts page."""
    if path.startswith(alerts_push.ARCHIDEKT_SITE) and "\\" not in path:
        return path
    if path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return "/notifications" if path.startswith("/notifications/open/") else path
    return "/notifications"


def narrow(
    rows: list[dict[str, Any]], *, kind: str = "", show: str = "", q: str = ""
) -> list[dict[str, Any]]:
    """The rows the Type and Show filters and the search keep."""
    q = q.casefold().strip()
    out = []
    for r in rows:
        if kind and r["kind"] != kind:
            continue
        if show == "unread" and r["read"]:
            continue
        if q and not _has_words(
            q, r["title"], r["message"], notify.KINDS.get(r["kind"], notify.KINDS["waiting"]).name
        ):
            continue
        out.append(r)
    return out


def _menu(r: dict[str, Any], csrf_in: str) -> str:
    """A kept row's ⋮: a menu that works without scripts (forms in a <details>); static/bell.js
    opens the shared row menu in its place (and on a right-click or a press and hold)."""
    rid = int(r["id"])
    read_act = "unread" if r["read"] else "read"
    read_word = "Mark unread" if r["read"] else "Mark read"

    def form(action: str, words: str, ic: str) -> str:
        return (
            f"<form method='post' action='/notifications/act'>{csrf_in}"
            f"<input type='hidden' name='action' value='{action}'>"
            f"<input type='hidden' name='ids' value='{rid}'>"
            f"<button type='submit' role='menuitem'>{icon(ic)}{esc(words)}</button></form>"
        )

    return (
        f"<details class='al-menu'><summary class='kebab' aria-label='{esc('Actions for ' + r['title'])}' "
        f"title='Actions'>{icon('more')}</summary><div class='menu' role='menu'>"
        f"{form(read_act, read_word, 'check' if read_act == 'read' else 'bell')}"
        f"{form('dismiss', 'Dismiss', 'x')}</div></details>"
    )


def split_title(title: str, k: notify.Kind) -> tuple[str, str]:
    """A push title ("Price drop · Sol Ring") as the page shows it: the type word above, the deck
    or card as the row's title, so no "·" has to break a line on a narrow screen. A title with
    no type word keeps the kind's name above it."""
    head, sep, rest = title.partition(" \u00b7 ")
    if sep and rest.strip() and len(head) <= 40:
        return head, rest
    return k.name, title


def row_html(r: dict[str, Any], *, now: float | None = None, csrf_in: str = "") -> str:
    k = notify.KINDS.get(r["kind"]) or notify.KINDS["waiting"]
    word, title = split_title(r["title"], k)
    unread = not r["read"]
    when = ui.when_text(r["created_at"], now=now) if r["created_at"] else "Waiting now"
    state = "Unread" if unread else "Read"
    attrs = f" data-alert='{int(r['id'])}' data-read='{0 if unread else 1}'" if r["id"] is not None else ""
    cls = "al-row" + (" unread" if unread else "") + (" live" if r["id"] is None else "")
    dot = "<span class='al-dot' aria-hidden='true'></span>" if unread else ""
    # Archidekt's news and contests open on archidekt.com, in a new tab (as links to Archidekt do)
    away = " target='_blank' rel='noopener'" if r.get("away") else ""
    ext = "<span class='sr-only'> (opens Archidekt in a new tab)</span>" if r.get("away") else ""
    return (
        f"<li class='{cls}' data-kind='{esc(k.key)}'{attrs}>"
        f"<a class='al-main' href='{esc(r['href'])}'{away}>"
        f"<span class='al-ic k-{esc(k.key)}' aria-hidden='true'>{icon(k.icon)}</span>"
        "<span class='al-body'>"
        f"<span class='al-meta'><span class='al-type'>{esc(word)}</span><span class='sr-only'>, </span>"
        f"<span class='al-when'>{esc(when)}</span><span class='sr-only'>, {state}</span></span>"
        f"<b class='al-title'>{esc(title)}</b>"
        f"<span class='al-text'>{esc(r['message'])}</span>{ext}</span>{dot}</a>"
        f"{_menu(r, csrf_in) if r['id'] is not None else ''}</li>"
    )


def list_html(rows: list[dict[str, Any]], *, filtered: bool, csrf_in: str = "") -> str:
    if not rows:
        if filtered:
            return f"<p class='al-empty muted'>{NO_MATCH}</p>"
        return (
            f"<div class='al-empty'>{icon('check')}<p><b>{EMPTY}</b></p>"
            f"<p class='muted'>{esc(EMPTY_HINT)}</p></div>"
        )
    now = time.time()
    return (
        f"<ul class='al-list' role='list'>{''.join(row_html(r, now=now, csrf_in=csrf_in) for r in rows)}</ul>"
    )


def tools_html(csrf_in: str, *, any_unread: bool, any_rows: bool) -> str:
    """Mark all read, Clear all and Settings: forms without scripts; static/bell.js acts in place
    and offers Undo."""

    def form(action: str, words: str, ic: str, *, disabled: bool) -> str:
        dis = " disabled" if disabled else ""
        return (
            f"<form method='post' action='/notifications/act'>{csrf_in}"
            f"<input type='hidden' name='action' value='{action}'>"
            f"<button type='submit' class='btn-plain' data-al-all='{action}'{dis}>{icon(ic)}{esc(words)}"
            "</button></form>"
        )

    return (
        "<div class='al-acts'>"
        + form("read_all", "Mark all read", "check", disabled=not any_unread)
        + form("clear_all", "Clear all", "x", disabled=not any_rows)
        + f"<a class='btn btn-plain' href='{section_href('notifications')}'>{icon('settings')}Settings</a>"
        "</div>"
    )


async def mirror_archidekt(state: Any, sub: str, *, force: bool = False) -> str:
    """Read Archidekt's alert list and keep its alerts here (alerts_push.mirror). Only when its
    unseen count is not the one the list was last read for, unless ``force`` (the Alerts page
    opened). Returns an error's words, or ""."""
    decks = state.decks
    if not decks.status(sub)["linked"]:
        return ""
    try:
        n = await profile_page.unseen_count(decks, sub)
    except DeckError as exc:
        return str(exc)
    if not force and (n == 0 or _MIRRORED.get(sub) == n):
        return ""
    try:
        got = await profile_page.notification_list(decks, sub)
    except DeckError as exc:
        return str(exc)
    await asyncio.to_thread(alerts_push.mirror, state.db, sub, got.get("notifications") or [])
    _MIRRORED[sub] = 0  # the list was read: Archidekt's count is 0 now (profile_page.notification_list)
    if len(_MIRRORED) > 5000:
        _MIRRORED.clear()
    return ""


def _ids(raw: Any) -> list[int]:
    if isinstance(raw, int | str):
        raw = str(raw).split(",")
    out = []
    for v in raw if isinstance(raw, list) else []:
        s = str(v).strip()
        if is_digits(s) and len(s) <= 12:
            out.append(int(s))
    return out[:500]


def act(state: Any, sub: str, action: str, ids: list[int]) -> list[int]:
    """One of :data:`ACTIONS` on the member's alerts; the ids that changed. A read or a dismissal
    takes the alert's message off the member's phones."""
    db = state.db
    if action == "read":
        changed = db.set_gateway_alerts_read(sub, ids)
    elif action == "unread":
        changed = db.set_gateway_alerts_read(sub, ids, read=False)
    elif action == "dismiss":
        changed = db.dismiss_gateway_alerts(sub, ids)
    elif action == "undo":
        changed = db.dismiss_gateway_alerts(sub, ids, undo=True)
    elif action == "read_all":
        changed = db.set_gateway_alerts_read(sub, None)
    elif action == "clear_all":
        changed = db.dismiss_gateway_alerts(sub, None)
    else:
        return []
    if action in ("read", "dismiss", "read_all", "clear_all") and changed and state.notifier is not None:
        state.notifier.withdraw_alerts(sub, changed)
    return changed


def add_alerts_routes(server: Any, state: Any, h: SimpleNamespace) -> None:
    decks = h.decks
    page = h.page
    s = h.s

    def is_admin(sub: str) -> bool:
        user = state.db.get_user(sub) or {}
        return bool(s.admin_group and s.admin_group in (user.get("groups") or []))

    async def waiting_of(sub: str) -> dict[str, tuple[str, int, str]]:
        try:
            return await asyncio.to_thread(decks.waiting_by_deck, sub)
        except Exception:  # noqa: BLE001 - the list never fails for its waiting rows
            return {}

    @server.custom_route("/notifications/count", methods=["GET"], include_in_schema=False)
    async def count(request: Request) -> Response:
        """The bell's count: the member's alerts since they last opened the list. Archidekt's are
        kept here first when its own count changed."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False}, status_code=401, headers=NO_STORE)
        await mirror_archidekt(state, sub)
        n = state.db.unseen_gateway_alerts(sub)
        return JSONResponse({"ok": True, "unseen": n}, headers=NO_STORE)

    @server.custom_route("/notifications", methods=["GET"], include_in_schema=False)
    async def notifications(request: Request) -> Response:
        """The Alerts page; as JSON, the bell's box (its newest rows)."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/notifications")
        wants_json = "application/json" in (request.headers.get("accept") or "")
        # a link on another site opens the page but changes nothing: opening the list clears the
        # count and reads Archidekt's list (security review 0.7.43)
        cross_site = not prefs.from_this_site(request)
        error = ""
        if not cross_site:
            error = await mirror_archidekt(state, sub, force=True)
        unseen = state.db.unseen_gateway_alerts(sub)
        live = notify.prefs(state.db, sub)["deliver"].get("waiting") != "off"
        rows = (waiting_rows(await waiting_of(sub)) if live else []) + kept_rows(state.db.gateway_alerts(sub))
        if not cross_site:
            state.db.mark_gateway_alerts_seen(sub)
        unread = sum(1 for r in rows if not r["read"])
        from .pages import _csrf

        csrf = _csrf(s, sid)
        csrf_in = f"<input type='hidden' name='csrf' value='{esc(csrf)}'>" if csrf else ""
        err = (
            ui.notice(f"Archidekt's alerts couldn't be read: {error}", "warn", role="status") if error else ""
        )
        if wants_json:
            shown = rows[:BOX_ROWS]
            body = err + list_html(shown, filtered=False, csrf_in=csrf_in)
            return JSONResponse(
                {"ok": True, "unseen": unseen, "unread": unread, "total": len(rows), "html": body},
                headers=NO_STORE,
            )
        qp = request.query_params
        admin = is_admin(sub)
        kinds = type_choices(admin)
        kind = qp.get("type") if qp.get("type") in {v for v, _ in kinds} else ""
        show = qp.get("show") if qp.get("show") in {v for v, _ in SHOW_CHOICES} else ""
        q = (qp.get("q") or "").strip()[:100]
        shown = narrow(rows, kind=kind, show=show, q=q)
        tools = ui.toolbar(
            "/notifications",
            search_label="Search alerts",
            placeholder="Search alerts|Search",
            q=q,
            filters=[
                ui.filter_select("type", "Type", kinds, kind),
                ui.filter_select("show", "Show", SHOW_CHOICES, show),
            ],
        )
        done = qp.get("done")
        note = (
            ui.notice(DONE_WORDS[done], "ok", role="status", cls="once")
            if done in DONE_CODES and not cross_site
            else ""
        )
        link_note = ""
        if not decks.status(sub)["linked"] and not cross_site:
            link_note = (
                "<div class='al-linknote'><p class='muted'>Link your Archidekt account to see its alerts "
                "here.</p><a class='btn btn-plain' href='/account/details/archidekt'>Link Archidekt</a></div>"
            )
        if cross_site:
            body_list = f"<p><a class='btn' href='/notifications'>{icon('bell')} Show my alerts</a></p>"
        else:
            body_list = err + list_html(shown, filtered=bool(kind or show or q), csrf_in=csrf_in)
        badge = f" <span class='bell-badge'>{unread} unread</span>" if unread and not cross_site else ""
        kept_any = any(r["id"] is not None for r in rows)
        any_unread = any(not r["read"] and r["id"] is not None for r in rows)
        return page(
            "Alerts",
            "<div class='bell-page'>"
            f"<div class='ui-head phead al-head'><h1>Alerts{badge}</h1>"
            f"{tools_html(csrf_in, any_unread=any_unread, any_rows=kept_any) if not cross_site else ''}</div>"
            "<p class='ui-sub'>What needs you and what happened, from this gateway and from Archidekt.</p>"
            f"{note}{tools}{link_note}<div class='card al-card' data-al-page>{body_list}</div>"
            f"<p class='muted small bell-foot'>{esc(FOOT)} Choose which alerts Archidekt sends you under "
            f"Email and alerts on Archidekt.</p><p class='al-ark'>{ARK_CHOICE}</p></div>",
            sub=sub,
            sid=sid,
            current="/notifications",
            heading=False,
        )

    @server.custom_route("/notifications/open/{alert_id}", methods=["GET"], include_in_schema=False)
    async def open_alert(request: Request) -> Response:
        """An alert's own page, and the alert marked read (from a row or the phone's message)."""
        sub, _sid = browser_session(state, request)
        raw = request.path_params.get("alert_id", "")
        if not sub:
            return login_redirect_here(request, f"/notifications/open/{raw}")
        if not is_digits(str(raw)) or len(str(raw)) > 12:
            return RedirectResponse("/notifications", 303)
        a = state.db.gateway_alert(sub, int(raw))
        if a is None:
            return RedirectResponse("/notifications", 303)
        if prefs.from_this_site(request) and a.get("read_at") is None:
            await asyncio.to_thread(act, state, sub, "read", [a["id"]])
        path = safe_path(a["path"])
        return RedirectResponse(path, 303)

    @server.custom_route("/notifications/act", methods=["POST"], include_in_schema=False)
    async def act_route(request: Request) -> Response:
        """Mark read or unread, dismiss, undo, Mark all read, Clear all: JSON with the
        X-CSRF-Token header (static/bell.js), or a form without scripts."""
        sub, sid = browser_session(state, request)
        as_json = "application/json" in (request.headers.get("content-type") or "")
        if not sub:
            if as_json:
                return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
            return login_redirect_here(request, "/notifications")
        raw = await read_limited(request, 16_384)
        if as_json:
            if not csrf_header_ok(s, sid, request):
                return JSONResponse(
                    {"ok": False, "error": "csrf", "message": "Reload the page and try again."},
                    403,
                    headers=NO_STORE,
                )
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return JSONResponse(
                    {"ok": False, "error": "invalid", "message": "Send JSON."}, 400, headers=NO_STORE
                )
        else:
            from urllib.parse import parse_qs

            fields = parse_qs((raw or b"").decode("utf-8", "replace"))
            body = {k: v[0] for k, v in fields.items()}
            if not csrf_form_ok(s, sid, body):
                return page("Alerts", h.EXPIRED, sub=sub, sid=sid, status=403)
        action = str(body.get("action") or "")
        if action not in ACTIONS:
            if as_json:
                return JSONResponse(
                    {"ok": False, "error": "invalid", "message": "That is not something this page can do."},
                    400,
                    headers=NO_STORE,
                )
            return RedirectResponse("/notifications", 303)
        ids = _ids(body.get("ids"))
        changed = await asyncio.to_thread(act, state, sub, action, ids)
        if as_json:
            return JSONResponse(
                {
                    "ok": True,
                    "changed": changed,
                    "message": DONE_WORDS[action],
                    "unseen": state.db.unseen_gateway_alerts(sub),
                },
                headers=NO_STORE,
            )
        return RedirectResponse(f"/notifications?done={action}", 303)


def assistant_view(state: Any, sub: str, limit: int = 30) -> list[dict[str, Any]]:
    """The member's kept alerts for an assistant (get_my_notifications): kind, title, words,
    when, read; never ids or paths to act on. Archidekt's own are left out: the tool reads those
    from Archidekt."""
    out = []
    kinds = tuple(k for k in notify.KINDS if k not in ("archidekt", "waiting"))
    for a in state.db.gateway_alerts(sub, kinds=kinds)[:limit]:
        k = notify.KINDS.get(a.get("kind") or "")
        out.append(
            {
                "type": k.name if k else "Gateway",
                "title": a["title"],
                "text": a["message"],
                "when": int(a["created_at"]),
                "read": a.get("read_at") is not None,
            }
        )
    return out
