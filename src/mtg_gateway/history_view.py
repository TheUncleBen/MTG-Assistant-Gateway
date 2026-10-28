"""The History page (``/history``): a row of widgets, then the member's
snapshots, applied restores and deck reports as one list, newest first, grouped by day (or by
deck), with chips and their counts, a deck-name search, filter drop-downs, paging, and each row's
Details beside the list (from 1100 px) or on its own page; then the backup copies on Archidekt.
Changes the assistants proposed are on Proposals (proposals_view.py).

The rows come from the database already narrowed and paged (db.list_snapshots,
DeckService.list_proposals, ReportService.list); this module only reads a request's filters and
draws the page. Every string a row shows is escaped here; nothing in it runs script (the site's
CSP forbids it).
"""

from __future__ import annotations

import calendar
import re
import time
from typing import Any
from urllib.parse import urlencode

from . import ui
from .decks import actor_label
from .reports_page import source_label_html
from .theme import esc as _esc
from .theme import icon, time_html

PAGE = ui.PER_PAGE
MAX_OFFSET = 2000
# The chips. "changes" (a link from before proposals moved to Proposals) reads as Everything.
TYPES = (("all", "Everything"), ("snapshots", "Snapshots"), ("restores", "Restores"), ("reports", "Reports"))
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
GROUPS = (("day", "Group by day"), ("deck", "Group by deck"))
DEFAULTS = {"type": "all", "group": "day"}
PAGE_SUB_TEXT = "Snapshots of your decks, restores and deck reports. Changes your assistants proposed are on"
NO_WHO = "Snapshots do not record who made them"
NOTHING_YET = (
    "Nothing yet. The snapshots taken before a change is applied, the restores and deck reports appear here."
)


def read_query(params: Any) -> dict[str, Any]:
    """The page's filters from the query string, each limited to what the page offers."""
    kind = (params.get("type") or "all").strip().lower()
    when = (params.get("when") or "").strip().lower()
    group = (params.get("group") or "day").strip().lower()
    opened = (params.get("open") or "").strip()
    try:
        offset = int(params.get("offset") or 0)
    except ValueError:
        offset = 0
    return {
        "deck_id": (params.get("deck_id") or "").strip()[:40],
        "type": kind if kind in {t for t, _ in TYPES} else "all",
        "when": when if when in {w for w, _ in WHENS} else "",
        "group": group if group in {g for g, _ in GROUPS} else "day",
        "q": (params.get("q") or "").strip()[:120],
        "offset": max(0, min(offset, MAX_OFFSET)),
        "open": opened if re.fullmatch(r"[spr]-[A-Za-z0-9_-]{1,64}", opened) else "",
    }


def is_filtered(query: dict[str, Any]) -> bool:
    """Whether any filter (not the grouping, the page or an open row) narrows the list."""
    return any(
        v and v != DEFAULTS.get(k, "") for k, v in query.items() if k not in ("offset", "group", "open")
    )


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


def _chip(e: dict[str, Any]) -> str:
    """An entry's app chip, its mark by the client that made it (ui.client_brand)."""
    return ui.app_chip(e["app"], client_id=e.get("client"))


def _client(created_by: str | None) -> str | None:
    """The client id inside an actor label ("app: Claude (id)"), for the app chip's mark."""
    m = re.fullmatch(r"app: .+ \((.+)\)", created_by or "")
    return m.group(1) if m else None


def _app(created_by: str | None) -> str | None:
    """Who made an entry, as the app chip names it: "You", "The administrator", the app's name
    (never its client id), or None."""
    label = created_by or ""
    if label == "browser":
        return "You"
    if label == "administrator":
        return "The administrator"
    if label.startswith("app: "):
        name = re.sub(r" \(.+\)$", "", label[5:])
        return name if name != label[5:] else "An assistant"
    return None


def _taken(text: str) -> int | None:
    """A restore row's "taken" ("2026-10-11 16:10 UTC") as epoch seconds."""
    try:
        return int(calendar.timegm(time.strptime(str(text), "%Y-%m-%d %H:%M UTC")))
    except (TypeError, ValueError):
        return None


def build_events(
    restores: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    *,
    csrf_input: str,
    client_names: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """One event per row, newest first. Each carries what the page shows: kind, deck, the line
    under the deck's name (markup), who made it, the time, its quick action and its Details."""
    events: list[dict[str, Any]] = []
    for p in restores:
        taken = _taken(p.get("taken") or "")
        when = f"Put back as it stood on {time_html(taken)}" if taken else "Put back as a snapshot had it"
        line = f"{when} · " + ui.cap_link("Proposal", f"/proposals/{p['id']}")
        diff = str(p.get("diff_text") or "")
        details = (
            f"<p>{when}.</p>"
            + (f"<pre class='diff'>{_esc(diff)}</pre>" if diff.strip() else "")
            + "<div class='ui-actions'>"
            + ui.button_link("Open the review page", f"/proposals/{p['id']}")
            + "</div>"
        )
        events.append(
            {
                "key": f"p-{p['id']}",
                "ts": int(p.get("applied_at") or p.get("created_at") or 0),
                "kind": "restore",
                "deck_id": p.get("deck_id"),
                "deck_name": p.get("deck_name") or p.get("deck_id"),
                "line": line,
                "app": _app(p.get("created_by")),
                "client": _client(p.get("created_by")),
                "who": True,
                "action": "",
                "details": details,
            }
        )
    for x in snapshots:
        # deck_gone / backup_gone: what the member's Archidekt deck list says (decks.mark_gone)
        bits: list[str] = []
        count = x.get("card_count")
        if x.get("deck_gone"):
            bits.append("Deleted on Archidekt")
            if count is not None:
                bits.append(f"{count} cards")
        else:
            summary = (
                f"{count} cards as the deck stood" if count is not None else "The whole deck as it stood"
            )
            if x.get("stopped_attempt"):  # taken for an apply that changed nothing
                summary += " just before an apply that stopped before it changed anything"
            elif x.get("proposal_id"):
                summary += " just before a change was applied"
            bits.append(_esc(summary))
        if x.get("proposal_id"):
            bits.append(ui.cap_link("The change", f"/proposals/{x['proposal_id']}"))
        if x.get("backup_gone"):
            bits.append("its backup copy is no longer on Archidekt")
        elif x.get("backup_url"):
            bits.append(
                ui.cap_link(
                    "Backup copy on Archidekt",
                    str(x["backup_url"]),
                    attrs="target='_blank' rel='noopener noreferrer'",
                )
            )
        restore_form = (
            "<form method='post' action='/history/restore' class='hy-restore'>"
            + csrf_input
            + f"<input type='hidden' name='snapshot_id' value='{_esc(x['snapshot_id'])}'>"
            + "<button type='submit' class='btn-plain'>Restore (review first)</button></form>"
        )
        if x.get("deck_gone"):
            # A restore rewrites the deck itself; with the deck deleted on Archidekt it cannot work.
            restore = (
                "<p class='muted'>This deck is no longer on your Archidekt account (deleted there?), so "
                "the snapshot cannot be restored onto it. The snapshot stays here.</p>"
            )
            action = ""
        else:
            restore = (
                "<p>Restoring makes a proposal that puts the deck back to this snapshot; you review and "
                "confirm it before anything changes on Archidekt.</p>"
                f"<div class='ui-actions'>{restore_form}</div>"
            )
            action = restore_form
        backup = ""
        if x.get("backup_gone"):
            backup = (
                "<p>The Archidekt backup copy made with it is no longer on Archidekt (deleted there). "
                "Restore does not need it: it uses this snapshot, kept in the gateway.</p>"
            )
        elif x.get("backup_url"):
            backup = (
                "<p>"
                + ui.cap_link(
                    "Archidekt backup copy",
                    str(x["backup_url"]),
                    attrs="target='_blank' rel='noopener noreferrer'",
                )
                + "</p>"
            )
        link = (
            "<p>" + ui.cap_link("The change it was taken before", f"/proposals/{x['proposal_id']}") + "</p>"
            if x.get("proposal_id")
            else ""
        )
        events.append(
            {
                "key": f"s-{x['snapshot_id']}",
                "ts": int(x.get("taken_at") or 0),
                "kind": "snapshot",
                "deck_id": x.get("deck_id"),
                "deck_name": x.get("deck_name") or x.get("deck_id"),
                "line": " · ".join(bits),
                "app": None,
                "who": False,
                "action": action,
                "details": link + backup + restore,
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
        if m.get("price_total"):  # 0: no card had a price, not a free deck
            bits.append(f"${float(m['price_total']):,.0f}")
        # the bracket set on the deck is THE bracket; the estimate only when none is set
        if r.get("bracket_set"):
            bits.append(f"bracket {r['bracket_set']}")
        elif r.get("bracket_estimate"):
            bits.append(f"bracket about {r['bracket_estimate']}")
        sim = (
            "the solo test is running"
            if r.get("research") == "running"
            else "with a goldfish simulation"
            if r.get("has_goldfish")
            else "statistics only"
        )
        client = r.get("created_by_client")
        app = (
            _app(actor_label(client, (client_names or {}).get(client) if client else None))
            if client
            else None
        )
        href = f"/history/reports/{r['report_id']}"
        open_report = ui.button_link("Open report", href)
        events.append(
            {
                "key": f"r-{r['report_id']}",
                "ts": int(r.get("taken_at") or 0),
                "kind": "report",
                "deck_id": r.get("deck_id"),
                "deck_name": r.get("deck_name") or r.get("deck_id"),
                # the report's overall status (reports.report_status), shown unless complete
                "state": r["status"] if r.get("status") and r.get("status") != "complete" else "",
                "line": source_label_html(r) + _esc((", ".join(bits) + f" · {sim}") if bits else sim),
                "app": app,
                "client": client,
                "who": True,
                "action": open_report,
                "details": f"<div class='ui-actions'>{open_report}</div>",
            }
        )
    events.sort(key=lambda e: e["ts"], reverse=True)
    return events


# Type labels (status label shape): Snapshot grey, Restore green, Report blue; a report's status beside it
KIND_LABELS = {
    "snapshot": ("Snapshot", "grey", "bookmark"),
    "restore": ("Restore", "green", "undo"),
    "report": ("Report", "blue", "report"),
}
REPORT_STATES = {
    "running": ("Running", "orange", "spinner"),
    "queued": ("Queued", "orange", "clock"),
    "partial": ("Partial", "orange", "alert"),
    "failed": ("Failed", "red", "alert"),
}


def _type_html(e: dict[str, Any]) -> str:
    word, colour, ic = KIND_LABELS[e["kind"]]
    out = ui.label(word, colour, ic)
    st = e.get("state") or ""
    if st:
        w, c, i = REPORT_STATES.get(st, (st.capitalize(), "orange", "clock"))
        out += ui.label(w, c, i)
    return out


def _day_label(day: str, today: str, yesterday: str) -> str:
    if day == today:
        return "Today"
    if day == yesterday:
        return "Yesterday"
    t = time.strptime(day, "%Y-%m-%d")
    return time.strftime("%A %d %B %Y", t).replace(" 0", " ")


COLS = ("8rem minmax(0,1fr) 4rem 6.5rem 17rem", "minmax(0,1fr) auto", "minmax(0,1fr) 44px")


def _row_html(e: dict[str, Any], query: dict[str, Any], *, now: float) -> str:
    name = _esc(e["deck_name"])
    deck = (
        f"<a class='t' href='/decks/{_esc(e['deck_id'])}'>{name}</a>"
        if e.get("deck_id")
        else f"<span class='t'>{name}</span>"
    )
    who = (
        _chip(e)
        if e["who"]
        else f"<span class='ui-when' title='{_esc(NO_WHO)}'>—"
        f"<span class='sr-only'>{_esc(NO_WHO)}</span></span>"
    )
    when = ui.when(e["ts"], now=now)
    details = "/history" + query_string(query, open=e["key"])
    acts = e.get("action") or ""
    if e["kind"] != "report":
        acts += f"<a class='btn btn-plain' href='{_esc(details)}'>Details</a>"
    chev_href = details
    chev_text = "Details"
    cls = "tr" + (" open" if query.get("open") == e["key"] else "")
    return (
        f"<div class='{cls}'><span class='md-hide'>{_type_html(e)}</span>"
        f"<span class='title'>{deck}<span class='deck'>{e['line']}</span>"
        f"<span class='sub'>{_type_html(e)}{_chip(e) if e['who'] else ''}{when}</span></span>"
        f"<span class='md-hide'>{who}</span><span class='md-hide'>{when}</span>"
        f"<span class='act'><span class='sm-hide acts'>{acts}</span>"
        f"<a class='sm-only chev' href='{_esc(chev_href)}' aria-label='{chev_text}'>›</a></span></div>"
    )


def events_html(
    events: list[dict[str, Any]],
    *,
    now: int | None = None,
    group: str = "day",
    query: dict[str, Any] | None = None,
) -> str:
    """The table: a header row, then the rows under a row per UTC day ("Today · 2", as every
    time on the site), or with ``group='deck'`` under a row per deck (its name linked to its page
    and, unless the page is already one deck's, "only this deck"), decks in the order of their
    newest entry and each deck's rows newest first."""
    query = query or {}
    now = int(time.time()) if now is None else now
    sort = f"<span class='hy-sort'>When {icon('sort')}</span>"
    out = [
        ui.table_open(*COLS, cls="hy-table", label="History"),
        ui.table_header(
            [
                ui.Column("Type", "md-hide"),
                ui.Column("Deck and what"),
                ui.Column("By", "md-hide"),
                ui.Column("When", "md-hide", html=sort),
                ui.Column(""),
            ]
        ),
    ]
    if group == "deck":
        order: list[str] = []
        by_deck: dict[str, list[dict[str, Any]]] = {}
        for e in events:
            key = str(e.get("deck_id") or e.get("deck_name") or "")
            if key not in by_deck:
                order.append(key)
                by_deck[key] = []
            by_deck[key].append(e)
        for key in order:
            rows = by_deck[key]
            name = _esc(rows[0].get("deck_name") or key)
            deck_id = rows[0].get("deck_id")
            heading = f"<a href='/decks/{_esc(deck_id)}'>{name}</a>" if deck_id else name
            n = len(rows)
            extra = ""
            if deck_id and not query.get("deck_id"):
                own = query_string({**query, "offset": 0, "open": ""}, deck_id=str(deck_id))
                extra = " · " + ui.cap_link("Only this deck", f"/history{own}")
            out.append(
                f"<div class='tr group hy-group'><span>{heading}</span>"
                f"<span class='meta'>{n} {'entry' if n == 1 else 'entries'}{extra}</span></div>"
            )
            out += [_row_html(e, query, now=now) for e in rows]
    else:
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        yesterday = time.strftime("%Y-%m-%d", time.gmtime(now - 86400))
        days: list[tuple[str, list[dict[str, Any]]]] = []
        for e in events:
            day = time.strftime("%Y-%m-%d", time.gmtime(e["ts"]))
            if not days or days[-1][0] != day:
                days.append((day, []))
            days[-1][1].append(e)
        for day, rows in days:
            out.append(ui.day_row(f"{_day_label(day, today, yesterday)} · {len(rows)}"))
            out += [_row_html(e, query, now=now) for e in rows]
    out.append("</div>")
    return "".join(out)


DELETED_NOTICES = {"report": "The report was deleted.", "snapshot": "The snapshot was deleted."}


def delete_box_html(
    what: str,
    action: str,
    csrf_input: str,
    *,
    box_id: str,
    held: str = "",
    open: bool = False,
    close_href: str,
) -> tuple[str, str]:
    """(the red-outline Delete… opener, its confirm box) for one report or snapshot. ``held``: why
    it can't be deleted now; the box then says so and has no Delete button."""
    opener_href = close_href + ("&" if "?" in close_href else "?") + "box=delete"
    opener = ui.box_opener("Delete…", opener_href, box_id, kind="danger")
    if held:
        body = f"<p>{_esc(held)}</p>"
        actions = [ui.box_button("Close", cancel=True)]
    else:
        gone = (
            "Its numbers leave your Reports and History pages and any deck trend it was part of."
            if what == "report"
            else "Restore can no longer put the deck back to it."
        )
        body = (
            f"<form id='{box_id}f' class='inbox' method='post' action='{_esc(action)}'>{csrf_input}"
            f"<p>{gone} This can't be undone. Nothing changes on Archidekt.</p></form>"
        )
        actions = [
            ui.box_button("Cancel", cancel=True),
            ui.box_button(f"Delete {what}", kind="danger-solid", form=f"{box_id}f"),
        ]
    box = ui.box(box_id, f"Delete this {what}?", body, actions=actions, open=open, close_href=close_href)
    return opener, box


def details_panel(e: dict[str, Any], query: dict[str, Any], *, extra_html: str = "") -> str:
    """One row's Details: beside the list from 1100 px, its own page below that. ``extra_html``
    (a Delete opener and its box) goes under the row's own actions."""
    word = KIND_LABELS[e["kind"]][0]
    body = (
        f"<div class='pills'>{_type_html(e)}</div>"
        f"<p class='ui-why'>{e['line']}</p>"
        + ui.meta(
            [
                ("Deck", f"<a href='/decks/{_esc(e['deck_id'])}'>{_esc(e['deck_name'])}</a>")
                if e.get("deck_id")
                else ("Deck", _esc(e["deck_name"])),
                ("By", _chip(e) if e["who"] else "—"),
                ("When", time_html(e["ts"])),
            ]
        )
        + e["details"]
        + extra_html
    )
    return ui.side_panel(
        _esc(f"{word}: {e['deck_name']}"),
        body,
        label=f"{word} details",
        close_href="/history" + query_string(query, open=""),
        back_text="Back to History",
    )


COLLECTION_ID = "collection"  # decks.Decks.COLLECTION_TARGET: collection proposals file under it
COLLECTION_LABEL = "Your Archidekt collection"
LISTS_ID = "lists"  # account_lists.LISTS_TARGET: changes to the member's lists file under it
LISTS_LABEL = "Your Archidekt lists"


def deck_filter_groups(
    seen: dict[str, str],
    live: list[dict[str, Any]] | None,
    *,
    listed: set[str] | None,
    own: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """The History deck filter's two groups from the decks that have history here (``seen``: id ->
    the name recorded then): current decks, under their live names from the member's Archidekt
    deck list (``live``, backup copies left out; None when it can't be read), and the decks no
    longer on Archidekt. A deck is gone only when all of these hold: ``listed`` (the ids of a
    complete deck list, ``DeckService.on_archidekt``) is known and lacks it, and the member's
    history shows it as their own (``own``, ``db.own_history_deck_ids``: a report or a copy may be
    of anyone's deck, which is never in the member's list). Anything else is unknown, and an
    unknown deck stays with the current decks under its recorded name. The placeholder of a
    new-deck proposal never applied ("new") is not a deck and is left out."""
    names = {str(d["id"]): str(d.get("name") or "") for d in live or []}
    current: dict[str, str] = {}
    gone: dict[str, str] = {}
    for did, recorded in seen.items():
        if did == "new":
            continue
        if did == COLLECTION_ID:
            current[did] = COLLECTION_LABEL
        elif did == LISTS_ID:
            current[did] = LISTS_LABEL
        elif did in names:
            current[did] = names[did] or recorded
        elif listed is not None and did not in listed and did in own:
            gone[did] = recorded
        else:
            current[did] = recorded
    return current, gone


def toolbar_html(query: dict[str, Any], decks: dict[str, str], *, gone: dict[str, str] | None = None) -> str:
    """The search (deck names) and the drop-downs: deck (with its "No longer on Archidekt"
    group), when (UTC days) and the grouping. They apply as they change; a Search button
    without scripts."""

    def by_name(group: dict[str, str]) -> list[tuple[str, str]]:
        return [
            (did, name or did) for did, name in sorted(group.items(), key=lambda kv: (kv[1] or kv[0]).lower())
        ]

    gone = gone or {}
    deck_items = [("", "All decks"), *by_name(decks)]
    if query["deck_id"] and query["deck_id"] not in decks and query["deck_id"] not in gone:
        deck_items.append((query["deck_id"], query["deck_id"]))
    deck_select = ui.filter_select("deck_id", "Deck", deck_items, query["deck_id"])
    if gone:
        opts = "".join(
            f"<option value='{_esc(v)}'{' selected' if v == query['deck_id'] else ''}>{_esc(t)}</option>"
            for v, t in by_name(gone)
        )
        deck_select = deck_select.replace(
            "</select>", f"<optgroup label='No longer on Archidekt'>{opts}</optgroup></select>", 1
        )
    return ui.toolbar(
        "/history",
        search_label="Search history",
        placeholder="Deck name",
        q=query["q"],
        filters=[
            deck_select,
            ui.filter_select("when", "When (UTC days)", list(WHENS), query["when"]),
            ui.filter_select("group", "Group by", list(GROUPS), query["group"]),
        ],
        keep={"type": query["type"] if query["type"] != "all" else ""},
    )


def _copy_ts(d: dict[str, Any]) -> int | None:
    raw = str(d.get("updated_at") or d.get("created_at") or "")[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return int(calendar.timegm(time.strptime(raw[: 19 if "T" in fmt else 10], fmt)))
        except ValueError:
            continue
    return None


def backup_copies_html(
    copies: list[dict[str, Any]] | None,
    *,
    folder: str,
    deck_id: str | None = None,
    names: dict[str, str] | None = None,
    now: float | None = None,
) -> str:
    """The "Backup copies on Archidekt" table (``id=backups``, where the deck pages' links
    land): the deck copies the gateway made (kept out of Home and My decks), each opening on
    Archidekt in a new tab, with the deck it was a copy of. ``None`` (the deck list still cold)
    says so; an empty list renders nothing."""
    head = "<h2 id='backups' class='ui-section hy-backups'>Backup copies on Archidekt</h2>"
    if copies is None:
        return (
            head + "<p class='muted'>Your Archidekt decks are still being read; reload the page to see "
            "the backup copies.</p>"
        )
    if not copies:
        return ""
    where = "this deck" if deck_id else "your decks"
    rows = [
        ui.table_open(
            "minmax(0,1fr) 7rem 12rem",
            "minmax(0,1fr) auto",
            "minmax(0,1fr) 44px",
            cls="hy-copies",
            label="Backup copies",
        ),
        ui.table_header([ui.Column("Copy"), ui.Column("Made", "md-hide"), ui.Column("")]),
    ]
    for d in copies:
        did = _esc(str(d.get("id", "")))
        name = _esc(str(d.get("name", "")))
        url = f"https://archidekt.com/decks/{did}"
        of = ""
        if d.get("backup_of") and not deck_id:
            of_name = (names or {}).get(str(d["backup_of"])) or "this deck"
            of = f" · copy of <a href='/decks/{_esc(str(d['backup_of']))}'>{_esc(of_name)}</a>"
        ts = _copy_ts(d)
        when = ui.when(ts, now=now) if ts else ""
        rows.append(
            f"<div class='tr'><span class='title'><a class='t' href='{url}' target='_blank' "
            f"rel='noopener noreferrer'>{name}</a>"
            f"<span class='deck'>In &ldquo;{_esc(folder)}&rdquo;{of}</span>"
            f"<span class='sub'>{when}</span></span><span class='md-hide'>{when}</span>"
            f"<span class='act'><a class='btn btn-plain sm-hide' href='{url}' target='_blank' "
            f"rel='noopener noreferrer'>{icon('external')}Open on Archidekt</a>"
            f"<a class='sm-only chev' href='{url}' target='_blank' rel='noopener noreferrer' "
            f"aria-label='Open on Archidekt'>{icon('external')}</a></span></div>"
        )
    rows.append("</div>")
    return (
        head + f"<p class='ui-note'>Full copies of {where} as they were before a change, kept in the "
        f"&ldquo;{_esc(folder)}&rdquo; folder of your Archidekt account. They stay out of Home and My "
        "decks; Restore here uses the gateway's own snapshots.</p>" + "".join(rows)
    )


# Further History page styles: HISTORY_CSS ends with this.
HISTORY_CSS_A = ""


def _trend_value(value: Any, unit: str) -> str:
    """One trend number as the page says it: '$310.00', '57%', 'turn 5', '3.21'."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "–"
    if unit == "$":
        return f"${value:,.2f}"
    if unit == "%":
        return f"{value:g}%"
    if unit == "turn":
        return f"turn {value:g}"
    return f"{round(value, 2):g}"


def _trend_change(change: float, unit: str) -> str:
    """The change since the first report in words, so it reads without colour or arrows."""
    if not change:
        return "no change"
    size = abs(change)
    if unit == "$":
        amount = f"${size:,.2f}"
    elif unit == "%":
        amount = f"{size:g} percentage points"
    elif unit == "turn":
        amount = f"{size:g} {'turn' if size == 1 else 'turns'}"
    else:
        amount = f"{round(size, 2):g}"
    return f"{'up' if change > 0 else 'down'} {amount}"


def _day(ts: Any) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts or 0)))


def _trend_svg(values: list[Any], label: str, *, width: int = 120, height: int = 32) -> str:
    """A sparkline of one metric across the reports, oldest left (no script, CSP-safe). It
    stretches to its box and the line keeps its width (non-scaling stroke). ``label`` is what a
    screen reader hears instead of the picture."""
    points = [(i, float(v)) for i, v in enumerate(values) if isinstance(v, (int, float))]
    lo, hi = min(v for _, v in points), max(v for _, v in points)
    span = (hi - lo) or 1.0
    step = (width - 8) / max(1, len(values) - 1)

    def xy(i: int, v: float) -> str:
        y = height / 2 if hi == lo else height - 4 - (v - lo) / span * (height - 8)
        return f"{4 + i * step:.1f},{y:.1f}"

    coords = " ".join(xy(i, v) for i, v in points)
    return (
        f"<svg class='tspark' viewBox='0 0 {width} {height}' preserveAspectRatio='none' role='img' "
        f"aria-label='{_esc(label)}'><polyline points='{coords}' vector-effect='non-scaling-stroke'/></svg>"
    )


def settings_text(settings: Any) -> str:
    """A report's run settings (reports.run_settings) in a few words, for the trend table."""
    if not isinstance(settings, dict):
        return "–"
    bits = []
    mode = {"commander": "Commander", "constructed": "60-card"}.get(str(settings.get("mode")))
    if mode:
        bits.append(mode)
    if settings.get("games") is not None:
        bits.append(f"{settings['games']} games")
    if settings.get("until_turn") is not None:
        bits.append(f"to turn {settings['until_turn']}")
    if settings.get("seed") is not None:
        bits.append(f"seed {settings['seed']}")
    engines = settings.get("engines") or []
    bits.append("Forge" if "forge" in engines else "no rules engine")
    return " · ".join(bits)


def trend_html(trend: dict[str, Any] | None) -> str:
    """The "Report trends" card of a deck's history: one sparkline per metric its stored reports
    hold (ReportService.trend, the same numbers list_deck_reports gives the assistant), each with
    the latest value and the change since the first report, then every report's numbers as a
    table for anyone who cannot see the lines. Nothing with fewer than two reports."""
    if not trend or trend.get("reports", 0) < 2 or not trend.get("metrics"):
        return ""
    series = trend["series"]
    items = []
    for m in trend["metrics"]:
        unit = m["unit"]
        latest, first = _trend_value(m["latest"], unit), _trend_value(m["first"], unit)
        change = _trend_change(m["change"], unit)
        said = (
            f"{m['label']} over {m['reports']} reports: {first} on {_day(m['first_at'])}, "
            f"{latest} on {_day(m['latest_at'])}, {change}."
        )
        values = [p.get(m["key"]) for p in series]
        broke = ""
        if m.get("left_out"):
            # the series breaks where the simulation's settings changed: only the latest stretch
            # is drawn and compared, never 40-life numbers against 20-life ones
            since = next((i for i, p in enumerate(series) if p["report_id"] == m.get("since_report_id")), 0)
            values = [None] * since + values[since:]
            many = m["left_out"] != 1
            broke = (
                f"<p class='delta'>{m['left_out']} earlier report{'s' if many else ''} ran with other "
                f"settings (game or turns) and {'are' if many else 'is'} left out.</p>"
            )
        items.append(
            f"<li class='tmetric'><h3>{_esc(m['label'])}</h3>"
            f"{_trend_svg(values, said)}"
            f"<p class='now'><b>{_esc(latest)}</b> <span>latest, {time_html(m['latest_at'])}</span></p>"
            f"<p class='delta'>{_esc(change[:1].upper() + change[1:])} since {_esc(first)} on "
            f"{time_html(m['first_at'])}</p>{broke}</li>"
        )
    shown = trend["metrics"]
    turns = any(isinstance(p.get("until_turn"), int) for p in series)
    head = "".join(f"<th scope='col'>{_esc(m['label'])}</th>" for m in shown)
    head += "<th scope='col'>Turns simulated</th>" if turns else ""
    head += "<th scope='col'>Settings</th>"
    rows = []
    for p in series:
        cells = "".join(f"<td>{_esc(_trend_value(p.get(m['key']), m['unit']))}</td>" for m in shown)
        if turns:
            cells += f"<td>{_esc(p.get('until_turn') if p.get('until_turn') is not None else '–')}</td>"
        cells += f"<td class='settings'>{_esc(settings_text(p.get('settings')))}</td>"
        rows.append(
            f"<tr><th scope='row'><a href='/history/reports/{_esc(p['report_id'])}'>"
            f"{time_html(p['taken_at'])}</a></th>{cells}</tr>"
        )
    n = trend["reports"]
    changed = (
        "<p class='notice warn'>The run settings changed between these reports (see Settings in the "
        "numbers by report). A simulation number is only compared across reports run the same way.</p>"
        if trend.get("settings_changes")
        else ""
    )
    return (
        "<section class='card trend' aria-labelledby='trend-h'><h2 id='trend-h'>Report trends</h2>"
        f"<p class='muted'>How this deck's {n} most recent stored reports moved, oldest to newest, from "
        f"{time_html(trend['first_at'])} to {time_html(trend['latest_at'])}. The goldfish numbers come "
        "from the reports that ran a simulation.</p>"
        + changed
        + f"<ul class='plain tmetrics'>{''.join(items)}</ul>"
        "<details class='tdata'><summary class='fold-btn'>The numbers by report</summary>"
        "<div class='tbl' role='region' aria-label='Report numbers, oldest first' tabindex='0'>"
        f"<table><caption class='sr-only'>Each report's numbers, oldest first</caption><thead><tr>"
        f"<th scope='col'>Report</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        "</details></section>"
    )


# The trends' own History page styles: HISTORY_CSS ends with this.
HISTORY_CSS_C = ""


HISTORY_CSS = (
    """
/* History (UI standard): the shared table, widgets and side panel are theme.UI_CSS */
.hy-table .act .acts form{display:contents}
.hy-table .act .acts .btn,.hy-table .act .acts button{margin:0}
.hy-table .title .deck a{font-weight:400}
.hy-table .group.hy-group{grid-template-columns:minmax(0,1fr) auto}
.hy-table .ui-lbl + .ui-lbl{margin-left:.25rem}
.hy-table .th .hy-sort{display:inline-flex;align-items:center;gap:.2rem}
.hy-table .th .hy-sort svg.i{width:14px;height:14px}
.hy-copies .act .btn{display:inline-flex;align-items:center;gap:.35rem;margin:0}
.hy-copies .act .chev svg.i{width:20px;height:20px}
h2.hy-backups{font-size:1.2rem}
/* a group row's meta wraps rather than push the page sideways at large text sizes */
.ui-table .group > *{min-width:0}
.ui-table .group .meta{white-space:normal}
@media (min-width:900px){.ui-table .group .meta a{white-space:nowrap}}
@media (min-width:600px){.ui-table .group .meta{text-align:right}}
/* a widget's number and its word wrap apart at large text sizes */
.wdg .big{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:.35rem}
.wdg .big small{margin-left:0}
.ui-drawer pre.diff{max-height:16rem;overflow:auto}
.ui-drawer .page-only a{display:inline-flex;align-items:center;min-height:44px}
.hy-empty p{margin:0}
.hy-back{margin:0 0 .5rem}
.trend h2{font-size:1.2rem} .trend > p.muted{margin:.2rem 0 .75rem}
.tmetrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,13rem),1fr));gap:.75rem;margin:0}
.tmetric{border:1px solid var(--border);border-radius:3px;padding:.6rem .75rem;min-width:0}
.tmetric h3{margin:0 0 .3rem;font-size:.95rem;overflow-wrap:anywhere}
.tspark{display:block;width:100%;height:36px;color:var(--orange-text)}
.tspark polyline{fill:none;stroke:currentColor;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.tmetric p{margin:.3rem 0 0;font-size:.88rem;overflow-wrap:anywhere}
.tmetric .now b{font-size:1.2rem;font-variant-numeric:tabular-nums}
.tmetric .now span,.tmetric .delta{color:var(--text-muted)}
.tdata{margin-top:.75rem}
.tdata .tbl{overflow-x:auto;max-width:100%;margin-top:.4rem}
.tdata table{border-collapse:collapse;font-size:.86rem;font-variant-numeric:tabular-nums}
.tdata th,.tdata td{padding:.3rem .5rem;border-bottom:1px solid var(--border-soft);text-align:right;
  vertical-align:top}
.tdata thead th{min-width:6rem}
.tdata th[scope=row],.tdata thead th:first-child{text-align:left;min-width:0;white-space:nowrap}
"""
    + HISTORY_CSS_A
    + HISTORY_CSS_C
)

__all__ = [
    "HISTORY_CSS",
    "PAGE",
    "backup_copies_html",
    "build_events",
    "deck_filter_groups",
    "details_panel",
    "events_html",
    "is_filtered",
    "query_string",
    "read_query",
    "toolbar_html",
    "trend_html",
    "when_since",
]
