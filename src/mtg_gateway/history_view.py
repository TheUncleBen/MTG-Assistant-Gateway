"""The history page (``/history``): the member's proposals, snapshots and deck reports as one
timeline, grouped by day or by deck, with a filter bar (deck, type, state, when, search) and
"Older" pages.

The rows come from the database already narrowed and paged (db.list_proposals, db.list_snapshots,
ReportService.list); this module only reads a request's filters and draws the page. Every
string a row shows is escaped here; nothing in it runs script (the site's CSP forbids it).
"""

from __future__ import annotations

import calendar
import html
import time
from typing import Any
from urllib.parse import urlencode

from .decks import actor_label
from .theme import icon

PAGE = 25
MAX_OFFSET = 2000
TYPES = (("all", "Everything"), ("changes", "Changes"), ("snapshots", "Snapshots"), ("reports", "Reports"))
STATES = (
    ("", "Any state"),
    ("pending", "Pending"),
    ("applied", "Applied"),
    ("failed", "Failed"),
    ("rejected", "Rejected"),
    ("expired", "Expired"),
)
# The "When" presets: UTC calendar days (every time on the site is UTC), each a window that
# starts at the beginning of a day and runs to now. The value is what the query string carries.
WHENS = (
    ("", "Any time"),
    ("today", "Today"),
    ("7d", "Last 7 days"),
    ("30d", "Last 30 days"),
    ("90d", "Last 90 days"),
    ("year", "This year"),
)
WHEN_DAYS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}
GROUPS = (("day", "Day"), ("deck", "Deck"))
DEFAULTS = {"type": "all", "group": "day"}
KIND_LABELS = {
    "edit": "Edit",
    "create_deck": "New deck",
    "restore": "Restore",
    "snapshot": "Snapshot",
    "report": "Report",
}
KIND_ICONS = {
    "edit": "edit",
    "create_deck": "plus",
    "restore": "undo",
    "snapshot": "bookmark",
    "report": "stats",
}


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def read_query(params: Any) -> dict[str, Any]:
    """The page's filters from the query string, each limited to what the page offers."""
    kind = (params.get("type") or "all").strip().lower()
    state = (params.get("state") or "").strip().lower()
    when = (params.get("when") or "").strip().lower()
    group = (params.get("group") or "day").strip().lower()
    try:
        offset = int(params.get("offset") or 0)
    except ValueError:
        offset = 0
    return {
        "deck_id": (params.get("deck_id") or "").strip()[:40],
        "type": kind if kind in {t for t, _ in TYPES} else "all",
        "state": state if state in {s for s, _ in STATES} else "",
        "when": when if when in {w for w, _ in WHENS} else "",
        "group": group if group in {g for g, _ in GROUPS} else "day",
        "q": (params.get("q") or "").strip()[:120],
        "offset": max(0, min(offset, MAX_OFFSET)),
    }


def is_filtered(query: dict[str, Any]) -> bool:
    """Whether any filter (not the grouping or the page) narrows the timeline."""
    return any(v and v != DEFAULTS.get(k, "") for k, v in query.items() if k not in ("offset", "group"))


def when_since(when: str, *, now: int | None = None) -> int | None:
    """The start (epoch seconds, inclusive) of a "When" preset's window: midnight UTC of the
    first of its days, or 1 January for "year"; ``None`` for "Any time" or an unknown value."""
    now = int(time.time()) if now is None else now
    if when == "year":
        return int(calendar.timegm((time.gmtime(now).tm_year, 1, 1, 0, 0, 0)))
    days = WHEN_DAYS.get(when)
    if not days:
        return None
    return (now // 86400 - (days - 1)) * 86400


def query_string(query: dict[str, Any], **override: Any) -> str:
    q = {**query, **override}
    pairs = [(k, v) for k, v in q.items() if v not in ("", 0, None) and v != DEFAULTS.get(k)]
    return "?" + urlencode(pairs) if pairs else ""


def _who(created_by: str | None) -> str:
    """Who made an entry, in plain words: the member's own browser is 'you', an app the
    assistant ('the assistant (app name)'), the administrator client an administrator."""
    label = created_by or ""
    if label == "browser":
        return "you"
    if label == "administrator":
        return "an administrator"
    if label.startswith("app: "):
        return f"the assistant ({label[5:]})"
    return ""


def build_events(
    proposals: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    *,
    csrf_input: str,
    client_names: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """One event per row, newest first. Each carries what the page shows: kind, deck, a
    one-line summary, who made it, the time and the folded details."""
    events: list[dict[str, Any]] = []
    for p in proposals:
        kind = p.get("kind") or "edit"
        summary = p.get("summary") or ""
        diff = str(p.get("diff_text") or "")
        details = f"<pre class='diff'>{_esc(diff)}</pre>" if diff.strip() else ""
        details += f"<p><a class='btn small' href='/proposals/{_esc(p['id'])}'>Open the review page</a></p>"
        events.append(
            {
                "ts": int(p.get("created_at") or 0),
                "kind": kind if kind in KIND_LABELS else "edit",
                "deck_id": p.get("deck_id"),
                "deck_name": p.get("deck_name") or p.get("deck_id"),
                "href": f"/proposals/{_esc(p['id'])}",
                "state": p.get("state") or "",
                "summary": summary or "No change lines recorded.",
                "who": _who(p.get("created_by")),
                "details": details,
            }
        )
    for x in snapshots:
        backup = (
            f"<a href='{_esc(x['backup_url'])}' target='_blank' rel='noopener noreferrer'>"
            "Archidekt backup copy</a>"
            if x.get("backup_url")
            else ""
        )
        link = (
            f"<a href='/proposals/{_esc(x['proposal_id'])}'>the change it was taken before</a>"
            if x.get("proposal_id")
            else ""
        )
        count = x.get("card_count")
        summary = f"{count} cards as the deck stood" if count is not None else "The whole deck as it stood"
        summary += " just before a change was applied." if x.get("proposal_id") else "."
        details = (
            "<p>Restoring makes a proposal that puts the deck back to this snapshot; you review and confirm "
            "it "
            "before anything changes on Archidekt.</p>"
            + (f"<p>See {link}.</p>" if link else "")
            + (f"<p>{backup}</p>" if backup else "")
            + "<form method='post' action='/history/restore' class='form-actions'>"
            + csrf_input
            + f"<input type='hidden' name='snapshot_id' value='{_esc(x['snapshot_id'])}'>"
            + f"<button type='submit'>{icon('undo')} Restore (review first)</button></form>"
        )
        events.append(
            {
                "ts": int(x.get("taken_at") or 0),
                "kind": "snapshot",
                "deck_id": x.get("deck_id"),
                "deck_name": x.get("deck_name") or x.get("deck_id"),
                "href": f"/proposals/{_esc(x['proposal_id'])}" if x.get("proposal_id") else "",
                "state": "",
                "summary": summary,
                "who": "",
                "details": details,
            }
        )
    for r in reports:
        m = r.get("metrics") or {}
        bits = []
        if m.get("card_count") is not None:
            bits.append(f"{m['card_count']} cards")
        if m.get("land_count") is not None:
            bits.append(f"{m['land_count']} lands")
        if m.get("average_mana_value") is not None:
            bits.append(f"average mana value {float(m['average_mana_value']):.1f}")
        if m.get("price_total") is not None:
            bits.append(f"${float(m['price_total']):.0f}")
        if r.get("bracket_estimate"):
            bits.append(f"bracket about {r['bracket_estimate']}")
        sim = "with a goldfish simulation" if r.get("has_goldfish") else "statistics only"
        client = r.get("created_by_client")
        who = (
            _who(actor_label(client, (client_names or {}).get(client) if client else None)) if client else ""
        )
        events.append(
            {
                "ts": int(r.get("taken_at") or 0),
                "kind": "report",
                "deck_id": r.get("deck_id"),
                "deck_name": r.get("deck_name") or r.get("deck_id"),
                "href": f"/history/reports/{_esc(r['report_id'])}",
                "state": "",
                "summary": (", ".join(bits) + f" · {sim}") if bits else sim,
                "who": who,
                "details": f"<p><a class='btn small' href='/history/reports/{_esc(r['report_id'])}'>"
                "Open the report</a></p>",
            }
        )
    events.sort(key=lambda e: e["ts"], reverse=True)
    return events


def _badge(state: str) -> str:
    cls = {"applied": "ok", "pending": "warn", "failed": "danger", "applying": "warn"}.get(state, "")
    return f"<span class='badge {cls}'>{_esc(state)}</span>" if state else ""


def _day_label(day: str, today: str, yesterday: str) -> str:
    if day == today:
        return "Today"
    if day == yesterday:
        return "Yesterday"
    t = time.strptime(day, "%Y-%m-%d")
    return time.strftime("%A %d %B %Y", t).replace(" 0", " ")


def _row_html(e: dict[str, Any], *, with_day: bool) -> str:
    kind = e["kind"]
    label = KIND_LABELS.get(kind, "Edit")
    name = _esc(e["deck_name"])
    deck_link = f"<a class='name' href='/decks/{_esc(e['deck_id'])}'>{name}</a>" if e.get("deck_id") else name
    title = (
        f"<a class='kind' href='{e['href']}'>{label}</a>"
        if e.get("href")
        else f"<span class='kind'>{label}</span>"
    )
    who = f"<span class='who'>by {_esc(e['who'])}</span>" if e.get("who") else ""
    fmt = "%d %b %Y %H:%M UTC" if with_day else "%H:%M UTC"
    when = time.strftime(fmt, time.gmtime(e["ts"]))
    if with_day:
        when = when.lstrip("0")
    details = (
        f"<details><summary>Details</summary><div class='more'>{e['details']}</div></details>"
        if e.get("details")
        else ""
    )
    return (
        f"<li class='hrow k-{_esc(kind)}'><span class='k' title='{label}'>"
        f"{icon(KIND_ICONS.get(kind, 'edit'))}</span><div class='body'><div class='l1'>{title}{deck_link}"
        f"{_badge(e.get('state') or '')}</div>"
        f"<p class='sum'>{_esc(e['summary'])}</p>{details}</div>"
        f"<div class='side'><time>{when}</time>{who}</div></li>"
    )


def _section_html(heading: str, rows: list[str], *, extra: str = "", cls: str = "") -> str:
    n = len(rows)
    return (
        f"<h2 class='day{(' ' + cls) if cls else ''}'><span>{heading}</span>"
        f"<small>{n} {'entry' if n == 1 else 'entries'}{extra}</small></h2>"
        f"<ul class='plain hrows'>{''.join(rows)}</ul>"
    )


def events_html(
    events: list[dict[str, Any]],
    *,
    now: int | None = None,
    group: str = "day",
    query: dict[str, Any] | None = None,
) -> str:
    """The rows under sticky headings: one per UTC day (as every time on the site), or with
    ``group='deck'`` one per deck, headed by the deck's name (linked to its page and to its own
    history), decks in the order of their newest entry and each deck's rows newest first."""
    if group == "deck":
        return _by_deck_html(events, query or {})
    now = int(time.time()) if now is None else now
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    yesterday = time.strftime("%Y-%m-%d", time.gmtime(now - 86400))
    out: list[str] = []
    current = None
    rows: list[str] = []
    for e in events:
        day = time.strftime("%Y-%m-%d", time.gmtime(e["ts"]))
        if day != current:
            if current is not None and rows:
                out.append(_section_html(_esc(_day_label(current, today, yesterday)), rows))
            current, rows = day, []
        rows.append(_row_html(e, with_day=False))
    if current is not None and rows:
        out.append(_section_html(_esc(_day_label(current, today, yesterday)), rows))
    return "".join(out)


def _by_deck_html(events: list[dict[str, Any]], query: dict[str, Any]) -> str:
    """The deck grouping: ``events`` are newest first, so the decks come out in the order of
    their newest entry and each deck's rows keep that order. Each heading links the deck's
    page and, unless the page is already one deck's, its own history with the same filters."""
    order: list[str] = []
    by_deck: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        key = str(e.get("deck_id") or e.get("deck_name") or "")
        if key not in by_deck:
            order.append(key)
            by_deck[key] = []
        by_deck[key].append(e)
    out: list[str] = []
    for key in order:
        group = by_deck[key]
        name = _esc(group[0].get("deck_name") or key)
        deck_id = group[0].get("deck_id")
        heading = f"<a href='/decks/{_esc(deck_id)}'>{name}</a>" if deck_id else name
        extra = ""
        if deck_id and not query.get("deck_id"):
            own = query_string({**query, "offset": 0}, deck_id=str(deck_id))
            extra = f" · <a href='/history{_esc(own)}'>only this deck</a>"
        rows = [_row_html(e, with_day=True) for e in group]
        out.append(_section_html(heading, rows, extra=extra, cls="deck"))
    return "".join(out)


def filter_bar_html(query: dict[str, Any], decks: dict[str, str], *, shown: int, total_hint: str = "") -> str:
    def options(items: list[tuple[str, str]], chosen: str) -> str:
        return "".join(
            f"<option value='{_esc(v)}'{' selected' if v == chosen else ''}>{_esc(label)}</option>"
            for v, label in items
        )

    deck_items = [("", "All decks")] + [
        (did, f"{name or did}")
        for did, name in sorted(decks.items(), key=lambda kv: (kv[1] or kv[0]).lower())
    ]
    if query["deck_id"] and query["deck_id"] not in decks:
        deck_items.append((query["deck_id"], query["deck_id"]))
    status = f"{shown} shown" + (f" · {total_hint}" if total_hint else "")
    return (
        "<form method='get' action='/history' class='card filterbar' role='search'>"
        "<div class='controls'>"
        f"<div class='field'><label for='h-deck'>Deck</label><span class='sel'>{icon('decks')}"
        f"<select id='h-deck' name='deck_id'>{options(deck_items, query['deck_id'])}</select></span></div>"
        f"<div class='field'><label for='h-type'>Type</label><span class='sel'>{icon('filter')}"
        f"<select id='h-type' name='type'>{options(list(TYPES), query['type'])}</select></span></div>"
        f"<div class='field'><label for='h-state'>State</label><span class='sel'>{icon('proposals')}"
        f"<select id='h-state' name='state'>{options(list(STATES), query['state'])}</select></span></div>"
        f"<div class='field'><label for='h-when'>When (UTC days)</label><span class='sel'>{icon('history')}"
        f"<select id='h-when' name='when'>{options(list(WHENS), query['when'])}</select></span></div>"
        f"<div class='field'><label for='h-group'>Group by</label><span class='sel'>{icon('layers')}"
        f"<select id='h-group' name='group'>{options(list(GROUPS), query['group'])}</select></span></div>"
        "<div class='field'><label for='h-q'>Search</label><input type='search' id='h-q' name='q' "
        f"value='{_esc(query['q'])}' placeholder='Deck name or a card in a change' autocomplete='off'></div>"
        "</div>"
        f"<div class='form-actions'><button type='submit' class='btn-primary'>{icon('filter')} Filter"
        "</button>"
        "<a class='btn' href='/history'>Clear</a>"
        f"<span class='status'>{_esc(status)}</span></div></form>"
    )


def backup_copies_html(
    copies: list[dict[str, Any]] | None, *, folder: str, deck_id: str | None = None
) -> str:
    """The "Backup copies on Archidekt" panel: the deck copies the gateway made (kept out of Home
    and My decks), each linked to Archidekt and to the deck it was a copy of. ``None`` (the deck
    list still cold) says so; an empty list renders nothing."""
    if copies is None:
        return (
            "<section class='card backups' id='backups'><h2>Backup copies on Archidekt</h2>"
            "<p class='muted'>Your Archidekt decks are still being read; reload in a moment to see the "
            "backup copies.</p></section>"
        )
    if not copies:
        return ""
    items = []
    for d in copies:
        did = _esc(str(d.get("id", "")))
        name = _esc(str(d.get("name", "")))
        of = ""
        if d.get("backup_of") and not deck_id:
            of = f" <span class='muted'>of <a href='/decks/{_esc(str(d['backup_of']))}'>this deck</a></span>"
        when = str(d.get("updated_at") or d.get("created_at") or "")[:10]
        items.append(
            f"<li><a href='https://archidekt.com/decks/{did}' target='_blank' "
            f"rel='noopener noreferrer'>{name}</a>{of}"
            + (f" <time class='muted'>{_esc(when)}</time>" if when else "")
            + "</li>"
        )
    where = "this deck" if deck_id else "your decks"
    return (
        f"<section class='card backups' id='backups'><h2>Backup copies on Archidekt ({len(copies)})</h2>"
        f"<p class='muted'>Full copies of {where} as they were before a change, kept in the "
        f"&ldquo;{_esc(folder)}&rdquo; folder of your Archidekt account. They stay out of Home and My "
        "decks; Restore here uses the gateway's own snapshots.</p>"
        f"<ul class='plain copies'>{''.join(items)}</ul></section>"
    )


def pager_html(query: dict[str, Any], *, has_more: bool) -> str:
    offset = query["offset"]
    if not has_more and offset == 0:
        return ""
    parts = ["<nav class='pager form-actions' aria-label='Older and newer entries'>"]
    if offset > 0:
        newer = max(0, offset - PAGE)
        parts.append(f"<a class='btn' href='/history{query_string(query, offset=newer)}'>← Newer</a>")
    if has_more:
        parts.append(f"<a class='btn' href='/history{query_string(query, offset=offset + PAGE)}'>Older →</a>")
    parts.append("</nav>")
    return "".join(parts)


HISTORY_CSS = """
.filterbar .controls{display:grid;gap:.6rem 1rem;align-items:end;
  grid-template-columns:repeat(auto-fit,minmax(min(100%,11rem),1fr))}
.filterbar .field label{margin:0 0 .3rem}
.filterbar .form-actions{margin-top:.75rem}
.history .day{position:sticky;top:50px;z-index:3;display:flex;align-items:baseline;gap:.6rem;margin:0;
  padding:.6rem 0 .4rem;font-size:1.05rem;background:var(--bg);border-bottom:1px solid var(--border)}
.history .day small{font-weight:400;color:var(--text-muted);font-size:.86rem}
.hrows{margin:0 0 1rem}
.hrow{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:.25rem .75rem;align-items:start;
  padding:.7rem 0;border-top:1px solid var(--surface-2)}
.hrow:first-child{border-top:0}
.hrow .k{display:inline-flex;align-items:center;justify-content:center;width:2rem;height:2rem;
  border-radius:50%;background:var(--surface-2);color:var(--orange);flex:none;margin-top:.1rem}
.hrow .k svg{width:1.1rem;height:1.1rem}
.hrow.k-report .k{color:var(--blue-text)} .hrow.k-snapshot .k{color:var(--green-text)}
.hrow .body{min-width:0}
.hrow .l1{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem .5rem;font-weight:700}
.hrow .kind{color:var(--text-muted);font-weight:700;text-decoration:none}
.hrow .kind:hover{color:var(--orange)}
.hrow .name{overflow-wrap:anywhere}
.hrow .sum{margin:.15rem 0 0;color:var(--text);font-size:.93rem;overflow-wrap:anywhere;line-height:1.4}
.hrow details{margin-top:.3rem} .hrow details > summary{cursor:pointer;color:var(--link);font-size:.9rem;
  min-height:2rem;display:flex;align-items:center}
.hrow details > summary:hover{color:var(--orange)}
.hrow .more{margin:.25rem 0 0;padding:.6rem .8rem;background:var(--surface-2);border-radius:3px;
  font-size:.93rem}
.hrow .more p{margin:.3rem 0} .hrow .more .form-actions{margin-top:.5rem}
.hrow .more pre.diff{margin:0 0 .5rem;max-height:14rem}
.hrow .side{display:flex;flex-direction:column;align-items:flex-end;gap:.15rem;color:var(--text-muted);
  font-size:.86rem;white-space:nowrap;text-align:right}
.hrow .side time{font-variant-numeric:tabular-nums}
@media (max-width:480px){ .hrow{grid-template-columns:auto minmax(0,1fr)}
  .hrow .side{grid-column:2;flex-direction:row;gap:.5rem;align-items:center;white-space:normal} }
.history .day.deck span a{text-decoration:none} .history .day.deck span a:hover{color:var(--orange)}
.history .day small a{font-weight:400}
.pager{justify-content:space-between} .pager .btn{margin:0}
.pager + .backups,.history + .backups{margin-top:1rem}
.trend h2{font-size:1.2rem}
.backups h2{font-size:1.2rem} .backups .copies{margin:0;display:grid;gap:.4rem}
.backups .copies li{display:flex;flex-wrap:wrap;gap:.25rem .5rem;align-items:baseline;overflow-wrap:anywhere}
.backups time{font-size:.86rem;font-variant-numeric:tabular-nums}
"""

__all__ = [
    "HISTORY_CSS",
    "PAGE",
    "backup_copies_html",
    "build_events",
    "events_html",
    "filter_bar_html",
    "is_filtered",
    "pager_html",
    "query_string",
    "read_query",
    "when_since",
]
