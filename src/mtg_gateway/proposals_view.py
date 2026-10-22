"""The Proposals page: a row of widgets, then every kept proposal,
waiting and past, with quick-filter chips and their counts, a search, filter drop-downs and paging.

The Waiting chip (the default) shows the waiting changes grouped by deck, oldest deck first, with
tick boxes and the bar that applies or rejects the checked ones (static/proposals.js). Every other
chip lists newest first, 25 to a page, with no tick boxes. One proposal (``/proposals/{id}``) opens
beside the list as a side panel from 1100 px and as its own page below that (pages.py draws the
panel's content).

Risk: a waiting proposal's risk is worked out on every read (it follows the live settings and its
amends); a closed one keeps the risk stored when it closed (Database.settle_risks). Every text a
row shows is escaped here.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from . import ui
from .decks import ADMIN_CLIENT, BROWSER_CLIENT
from .history_view import when_since
from .modes import NO_ROWS_REASON, UNJUDGED
from .theme import esc, icon
from .waiting_strip import line_for

# The chips: the value in the query string, then the words.
SHOWS = (
    ("waiting", "Waiting"),
    ("needs_look", "Needs a look"),
    ("applied", "Applied"),
    ("rejected", "Rejected"),
    ("closed", "Expired, withdrawn, replaced"),
    ("failed", "Failed"),
    ("all", "All"),
)
# Risk filter: the risk words. ``press`` (Your press or Delete) is what the "Always need your
# press" widget's Show them sets; the drop-down shows it only then.
RISKS = (
    ("", "Any risk"),
    ("low", "Low"),
    ("high", "High"),
    ("your_press", "Your press"),
    ("delete", "Delete"),
)
PRESS_FILTER = ("press", "Your press or Delete")
RISK_CODES = {
    "low": ("low",),
    "high": ("high",),
    "your_press": ("consent",),
    "delete": ("destructive",),
    "press": ("consent", "destructive"),
}
WHENS = (("", "Any time"), ("today", "Today"), ("7d", "Last 7 days"), ("30d", "Last 30 days"))
YOU, ADMIN = "you", "admin"
FROM_YOU, FROM_ADMIN = "You", "The administrator"
NO_APP_NAME = "An assistant"  # an app with no registered name: never its client id
# A failed apply's stored error when Archidekt answered and said no (ArchidektError kinds, and the
# gateway's own refusals of what it was about to send); anything else (the gateway restarted, a
# network error or a timeout, the backup copy) is "could not be applied", not a refusal.
ARCHIDEKT_REFUSALS = frozenset(
    {"auth", "forbidden", "not_found", "rate_limited", "contract", "invalid", "edit", "verify_mismatch"}
)
NOT_APPLIED = "Could not be applied"

PAGE_SUB = (
    "Every change your assistants proposed, waiting and past. Past proposals stay here for 30 days "
    "(at most 100), and applied ones for a year."
)
PAGER_NOTE = "past proposals are kept 30 days (at most 100), applied ones a year"
WAITING_NOTE = (
    "Check the changes to apply or reject, on any decks and in any order. They are applied oldest "
    "first, each checked against the deck as it is just before it is sent; one that would now do "
    "something else is not sent and waits as Needs a look."
)
NOTHING_WAITING = (
    "Nothing is waiting for your approval. Ask your assistant to propose a deck change and it will "
    "appear here for review."
)
NOTHING_MATCHES = "Nothing matches these filters."
UNTIED = "Not tied to one of your decks"
UPDATED_SHORT = "Updated: still does what was reviewed"
EXPIRE_SOON = 2 * 86400  # an "expires in" flag under two days left, as before

# Changes that are not edits to one of the member's decks ("Not tied to one of your decks").
_UNTIED_KINDS = ("create_deck", "clone", "collection")
_DECK_ACTIONS = ("collaborator_add", "collaborator_remove", "delete_deck")
UNDO_KINDS = ("edit", "details", "restore", "categories")

# Waiting table: tick box, change, risk, status, from, made, the open link.
WAIT_COLS = (
    "32px minmax(0,1fr) 7.5rem 10rem 6.5rem 6.5rem 5rem",
    "32px minmax(0,1fr) 10rem 5rem",
    "32px minmax(0,1fr) 44px",
)
# Every other chip: change, risk, status (with its closing line), from, made.
ALL_COLS = (
    "minmax(0,1fr) 7.5rem 13rem 6.5rem 6.5rem",
    "minmax(0,1fr) 11rem",
    "minmax(0,1fr)",
)


def read_query(params: Any) -> dict[str, Any]:
    """The page's filters from the query string, each limited to what the page offers."""

    def one(name: str, allowed: set[str], default: str = "") -> str:
        v = str(params.get(name) or "").strip().lower()
        return v if v in allowed else default

    try:
        offset = int(params.get("offset") or 0)
    except ValueError:
        offset = 0
    deck = str(params.get("deck_id") or "").strip()
    return {
        "show": one("show", {s for s, _ in SHOWS}, "waiting"),
        "deck_id": deck if deck.isdigit() and len(deck) <= 12 else "",
        "risk": one("risk", set(RISK_CODES)),
        "from": str(params.get("from") or "").strip()[:60],
        "when": one("when", {w for w, _ in WHENS if w}),
        "q": str(params.get("q") or "").strip()[:120],
        "group": one("group", {"deck"}),
        "order": one("order", {"old"}),
        "check": one("check", {"low"}),
        "offset": max(0, min(offset, 5000)),
    }


DEFAULTS = {"show": "waiting"}


def query_string(query: dict[str, Any], **override: Any) -> str:
    q = {**query, **override}
    pairs = [(k, v) for k, v in q.items() if v not in ("", 0, None) and v != DEFAULTS.get(k)]
    return "?" + urlencode(pairs) if pairs else ""


def _span(seconds: float) -> str:
    """A length of time in list-date words: "40 min", "20 h", "1 day", "6 days"."""
    s = max(0, int(seconds))
    if s < 3600:
        return f"{max(1, s // 60)} min"
    if s < 86400:
        return f"{s // 3600} h"
    days = s // 86400
    return f"{days} day{'' if days == 1 else 's'}"


def _summary(diff: str) -> str:
    lines = str(diff or "").splitlines()
    text = ", ".join(lines[:4]) + (f" (+{len(lines) - 4} more)" if len(lines) > 4 else "")
    return text[:300] or "(no changes listed)"


class Names:
    """Who made a proposal, in the page's words: the app's registered name (never its client id),
    "You" (the member's own browser), "The administrator", or None (no app recorded)."""

    def __init__(self, db: Any):
        self.db = db
        self._cache: dict[str, str] = {}

    def of(self, client: str | None) -> str | None:
        if not client:
            return None
        if client == BROWSER_CLIENT:
            return FROM_YOU
        if client == ADMIN_CLIENT:
            return FROM_ADMIN
        if client not in self._cache:
            self._cache[client] = str(self.db.client_name(client) or "").strip()[:60] or NO_APP_NAME
        return self._cache[client]

    @staticmethod
    def key(name: str | None) -> str:
        """The From filter's value for a name: "you", "admin" or the app's name."""
        return {FROM_YOU: YOU, FROM_ADMIN: ADMIN}.get(name or "", name or "")


def _untied(db: Any, sub: str, row: dict[str, Any]) -> bool:
    if row.get("kind") in _UNTIED_KINDS or not str(row.get("deck_id") or "").isdigit():
        return True
    if row.get("kind") == "action":
        full = db.get_proposal(row["id"], sub) or {}
        changes = full.get("changes")
        action = changes.get("action") if isinstance(changes, dict) else None
        return action not in _DECK_ACTIONS
    return False


def gather(state: Any, sub: str, *, now: float | None = None) -> dict[str, Any]:
    """Every kept proposal of the member as list items (newest first), with the waiting ones'
    board lines and risk worked out now."""
    now = time.time() if now is None else now
    db, decks = state.db, state.decks
    names = Names(db)
    waiting_rows = db.pending_board(sub)  # holds what must be held first; oldest first
    index = db.proposal_index(sub)
    # the links and app names the rows ask for one by one: read once for the whole page
    ids = [r["id"] for r in waiting_rows] + [r["id"] for r in index]
    clients = [r.get("created_by_client") for r in index] + [r.get("amended_by_client") for r in index]
    with db.read_ahead(sub, ids, clients):
        return _gathered(db, decks, names, sub, now, waiting_rows, index)


def _gathered(
    db: Any, decks: Any, names: Names, sub: str, now: float, waiting_rows: list, index: list
) -> dict:
    lines = {r["id"]: line_for(db, sub, r) for r in waiting_rows}
    items: list[dict[str, Any]] = []
    for r in index:
        seen = "expired" if r["state"] == "pending" and int(r["expires_at"] or 0) < now else r["state"]
        risk, why = r.get("risk"), ""
        if r["state"] in ("pending", "applying"):
            full = db.get_proposal(r["id"], sub)
            # nothing to judge it by (gone meanwhile, or no list of rows): a dash, never High
            risk, why = decks.proposal_risk(sub, full) if full else (UNJUDGED, "")
            if risk == "high" and why == NO_ROWS_REASON:
                risk, why = UNJUDGED, ""
        elif not risk:  # closed before its tier was stored: as the close would judge it
            full = db.get_proposal(r["id"], sub)
            risk = decks.stored_risk(full) if full else UNJUDGED
        app = names.of(r.get("created_by_client"))
        item = {
            "id": r["id"],
            "kind": r.get("kind") or "edit",
            "deck_id": str(r.get("deck_id") or ""),
            "deck_name": r.get("deck_name") or str(r.get("deck_id") or ""),
            "state": seen,
            "status": r.get("status") if seen == "pending" else None,
            "risk": risk,
            "risk_why": why,
            "app": app,
            "from": Names.key(app),
            "created_at": int(r.get("created_at") or 0),
            "expires_at": int(r.get("expires_at") or 0),
            "applied_at": int(r.get("applied_at") or 0),
            "status_at": int(r.get("status_at") or 0),
            "version": int(r.get("version") or 1),
            "board_no": r.get("board_no"),
            "changed_by": names.of(r.get("amended_by_client")) if r.get("amended_by_client") else None,
            "snapshot_id": r.get("snapshot_id"),
            "result": r.get("result") if isinstance(r.get("result"), dict) else {},
            "summary": _summary(r.get("diff_text") or ""),
            "diff_text": str(r.get("diff_text") or ""),
            "untied": _untied(db, sub, r),
            "line": lines.get(r["id"]),
        }
        items.append(item)
    return {"items": items, "waiting_order": [r["id"] for r in waiting_rows], "names": names}


def _matches(it: dict[str, Any], query: dict[str, Any], since: int | None) -> bool:
    """The search, the drop-downs (not the chip)."""
    if query["deck_id"] and it["deck_id"] != query["deck_id"]:
        return False
    if query["risk"] and it["risk"] not in RISK_CODES[query["risk"]]:
        return False
    if query["from"] and it["from"].casefold() != query["from"].casefold():
        return False
    if since is not None and it["created_at"] < since:
        return False
    q = query["q"].casefold()
    return not q or q in str(it["deck_name"]).casefold() or q in it["diff_text"].casefold()


def _in_chip(it: dict[str, Any], show: str) -> bool:
    st = it["state"]
    if show == "waiting":
        return st == "pending"
    if show == "needs_look":
        return st == "pending" and it["status"] == "needs_look"
    if show == "closed":
        return st in ("expired", "withdrawn", "replaced")
    if show == "all":
        return True
    return st == show


# -- widgets -------------------------------------------------------------------------------------


def _undo_form(it: dict[str, Any], csrf: str | None, label: str, cls: str) -> str:
    """Undo of an applied change: the same preview as its review page (POST /proposals/undo)."""
    return (
        "<form method='post' action='/proposals/undo' class='pr-undo'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf or '')}'>"
        "<input type='hidden' name='action' value='preview'>"
        f"<input type='hidden' name='snapshot' value='{esc(it['snapshot_id'])}'>"
        f"<input type='hidden' name='applied' value='{esc(it['id'])}'>"
        f"<input type='hidden' name='after' value='{int(it['applied_at'] or 0)}'>"
        f"<button type='submit' class='{esc(cls)}'>{esc(label)}</button></form>"
    )


def _undoable(it: dict[str, Any], csrf: str | None) -> bool:
    return bool(it["state"] == "applied" and it["kind"] in UNDO_KINDS and it["snapshot_id"] and csrf)


def _low_checkable(it: dict[str, Any]) -> bool:
    """A waiting change "Check all low-risk" ticks: low risk and not Needs a look."""
    return it["state"] == "pending" and it["risk"] == "low" and it["status"] != "needs_look"


APP_COLOURS = ("var(--orange)", "var(--blue)", "var(--green)", "var(--text-muted)")


def widgets_html(data: dict[str, Any], query: dict[str, Any], csrf: str | None, *, now: float) -> str:
    items = data["items"]
    waiting = [it for it in items if it["state"] == "pending"]
    tied = [it for it in waiting if not it["untied"]]
    decks_n = len({it["deck_id"] for it in tied})
    untied_n = len(waiting) - len(tied)
    # 1. Waiting for you
    if waiting:
        bits = []
        if tied:
            bits.append(f"{len(tied)} on {decks_n} deck{'' if decks_n == 1 else 's'}")
        if untied_n:
            bits.append(f"{untied_n} not tied to a deck")
        det = ", ".join(bits)
        det += f" · oldest {_span(now - min(it['created_at'] for it in waiting))}"
        soon = [it["expires_at"] for it in waiting if it["expires_at"] > now]
        if soon:
            det += f" · next expires in {_span(min(soon) - now)}"
    else:
        det = "Nothing is waiting."
    low = sum(1 for it in waiting if _low_checkable(it))
    acts = (
        [
            ui.button_link(
                f"Check all low-risk ({low})",
                "/proposals" + query_string(query, show="waiting", check="low", offset=0) + "#waiting",
            )
        ]
        if low
        else []
    )
    w1 = ui.widget(
        "Waiting for you",
        "clock",
        len(waiting),
        unit="waiting",
        detail=det,
        actions=acts,
        tone="accent" if waiting else "",
    )
    # 2. Needs a look
    look = [it for it in waiting if it["status"] == "needs_look"]
    if look:
        first = sorted(look, key=lambda it: it["created_at"])[0]
        no = f" #{first['board_no']}" if first.get("board_no") else ""
        why = (first.get("line") or {}).get("status_text") or ""
        det2 = f"{first['deck_name']}{no}" + (f": {why}" if why else "")
        acts2 = [ui.button_link("Open it", f"/proposals/{first['id']}")]
    else:
        det2, acts2 = "Nothing needs a look.", []
    w2 = ui.widget(
        "Needs a look", "alert", len(look), detail=det2, actions=acts2, tone="alert" if look else ""
    )
    # 3. Always need your press
    press = sum(1 for it in waiting if it["risk"] == "consent")
    delete = sum(1 for it in waiting if it["risk"] == "destructive")
    det3 = (
        (ui.label_for("your_press", word=f"Your press {press}") if press else "")
        + (ui.label_for("delete", word=f"Delete {delete}") if delete else "")
        + "<span class='tx'>in every approval mode</span>"
    )
    acts3 = (
        [
            ui.button_link(
                "Show them", "/proposals" + query_string(query, show="waiting", risk="press", offset=0)
            )
        ]
        if press + delete
        else []
    )
    w3 = ui.widget("Always need your press", "hand", press + delete, detail_html=det3, actions=acts3)
    # 4. Last 7 days
    week = now - 7 * 86400

    def closed_at(it: dict[str, Any]) -> int:
        return it["applied_at"] or it["status_at"] or it["created_at"]

    applied = [it for it in items if it["state"] == "applied" and closed_at(it) >= week]
    rejected = sum(1 for it in items if it["state"] == "rejected" and closed_at(it) >= week)
    failed = sum(1 for it in items if it["state"] == "failed" and closed_at(it) >= week)
    det4 = f"{rejected} rejected · {failed} failed"
    if applied:
        last = max(applied, key=closed_at)
        det4 += f" · last applied {ui.when_text(closed_at(last), now=now)} to {last['deck_name']}"
    undo = [it for it in sorted(applied, key=closed_at, reverse=True) if _undoable(it, csrf)]
    acts4 = [_undo_form(undo[0], csrf, "Undo last apply", "btn-plain")] if undo else []
    w4 = ui.widget("Last 7 days", "check", len(applied), unit="applied", detail=det4, actions=acts4)
    # 5. Waiting by app
    by_app: dict[str, int] = {}
    for it in waiting:
        name = it["app"] or "No app"
        by_app[name] = by_app.get(name, 0) + 1
    order = sorted(by_app.items(), key=lambda kv: (-kv[1], kv[0]))
    if order:
        big = " ".join(f"{n}<small>{esc(name)}</small>" for name, n in order)
        total = sum(by_app.values())
        bar = "".join(
            f"<i style='width:{round(100 * n / total)}%;background:{APP_COLOURS[min(i, 3)]}'></i>"
            for i, (_name, n) in enumerate(order)
        )
        det5 = f"<span class='bar-split' aria-hidden='true'>{bar}</span>"
    else:
        big, det5 = "0", ""
    w5 = ui.widget(
        "Waiting by app",
        "link",
        0,
        big_html=big,
        detail_html=det5,
        actions=[ui.button_link("Connected apps", "/apps")],
        wide_sm=True,
    )
    return ui.widgets([w1, w2, w3, w4, w5])


# -- rows ----------------------------------------------------------------------------------------


def _status_label(it: dict[str, Any]) -> str:
    line = it.get("line") or {}
    tip = line.get("status_text") or ""
    if it["state"] == "pending":
        key = it["status"] if it["status"] in ("needs_look", "held") else "waiting"
    else:
        key = it["state"]
    return ui.status_label(key, title=tip)


def _risk_label(it: dict[str, Any]) -> str:
    if it["risk"] == UNJUDGED:
        return ui.no_risk_label()
    return ui.risk_label(it["risk"], title=it.get("risk_why") or "")


def _version_line(it: dict[str, Any], now: float) -> str:
    """ "Changed by ChatGPT · Version 2 · expires in 20 h" (a waiting change)."""
    parts = []
    if it["version"] > 1 and it.get("changed_by"):
        parts.append(f"Changed by {it['changed_by']}")
    if it["version"] > 1:
        parts.append(f"Version {it['version']}")
    left = it["expires_at"] - now
    if 0 < left < EXPIRE_SOON:
        parts.append(f"expires in {_span(left)}")
    text = " · ".join(parts)
    return text[:1].upper() + text[1:]


def _waiting_flags(it: dict[str, Any], now: float) -> str:
    line = it.get("line") or {}
    out = []
    why = line.get("status_text") or ""
    if it["status"] in ("needs_look", "held") and why:
        out.append(f"<span class='flag'>{esc(why)}</span>")
    if it["status"] == "updated":
        out.append(
            f"<span class='flag'>{ui.status_label('updated')} "
            f"{esc(why or 'The deck changed since this was made; it still does exactly what was reviewed.')}"
            "</span>"
        )
    if line.get("link_note"):
        out.append(f"<span class='flag'>{esc(line['link_note'])}</span>")
    v = _version_line(it, now)
    if v:
        out.append(f"<span class='flag'>{esc(v)}</span>")
    return "".join(out)


def _deck_line(it: dict[str, Any]) -> str:
    return UNTIED if it["untied"] else str(it["deck_name"])


def _no(it: dict[str, Any]) -> str:
    return f"#{int(it['board_no'])}" if it["state"] == "pending" and it.get("board_no") else ""


def _deck_attr(it: dict[str, Any]) -> str:
    """The row's deck, for its menu's "Open the deck" (static/menus.js); none for an untied change."""
    if it.get("untied") or not str(it.get("deck_id") or "").isdigit():
        return ""
    return f" data-deck='{esc(it['deck_id'])}'"


def waiting_row(it: dict[str, Any], *, group: str, now: float, checked: bool, open_id: str, qs: str) -> str:
    """One waiting change: a tick box (the whole row is its target), the change and its flags,
    risk, status, the app, when, and Review (the panel or the page)."""
    pid = esc(it["id"])
    no = _no(it)
    box = f"pk-{it['id']}"
    aria = f"{no} {it['summary']}".strip()[:80]
    chk = (
        f"<label class='chk'><input type='checkbox' id='{esc(box)}' name='pick' value='{pid}' "
        f"data-group='{esc(group)}' data-risk='{esc(it['risk'])}'"
        + (" data-low" if _low_checkable(it) else "")
        + f" aria-label='{esc(aria)}'{' checked' if checked else ''}></label>"
    )
    num = f"<span class='no'>{esc(no)}</span>" if no else ""
    title = (
        f"<label class='title' for='{esc(box)}'><span class='t'>{num}{esc(it['summary'])}"
        f"<span class='stin'>{_status_label(it)}</span></span>{_waiting_flags(it, now)}"
        f"<span class='sub'>{_risk_label(it)}{ui.app_chip(it['app'])}{ui.when(it['created_at'], now=now)}"
        "</span></label>"
    )
    cls = "tr" + (" sel" if checked else "") + (" open" if it["id"] == open_id else "")
    return (
        f"<div class='{cls}'{_deck_attr(it)}>{chk}{title}"
        f"<label class='md-hide' for='{esc(box)}'>{_risk_label(it)}</label>"
        f"<label class='sm-hide' for='{esc(box)}'>{_status_label(it)}</label>"
        f"<label class='md-hide' for='{esc(box)}'>{ui.app_chip(it['app'])}</label>"
        f"<label class='md-hide' for='{esc(box)}'>{ui.when(it['created_at'], now=now)}</label>"
        f"<input type='hidden' name='v_{pid}' value='{int(it['version'])}'>"
        + ui.open_link(f"/proposals/{it['id']}{qs}", "Review")
        + "</div>"
    )


def _closing_html(
    it: dict[str, Any], *, csrf: str | None, replaced_by: dict[str, Any] | None, now: float
) -> str:
    """The short line under a row's status: what happened to it, or what a waiting one waits on."""
    st = it["state"]
    line = it.get("line") or {}
    if st == "pending":
        if it["status"] in ("needs_look", "held") and line.get("status_text"):
            return esc(line["status_text"])
        note = str(line.get("link_note") or "")
        if note.startswith("Waits for "):
            return esc(note.split(",", 1)[0])  # "Waits for #2"
        if note:
            return esc(note)
        if it["status"] == "updated":
            return esc(UPDATED_SHORT)
        return esc(_version_line(it, now))
    if st == "applied":
        at = it["applied_at"] or it["status_at"] or it["created_at"]
        undo = " · " + _undo_form(it, csrf, "Undo", "linkbtn inline") if _undoable(it, csrf) else ""
        return f"Applied {ui.when(at, now=now)}{undo}"
    if st == "rejected":
        by = (it["result"] or {}).get("rejected_by")
        if by == "assistant":
            return esc(f"Rejected by {it['app'] or 'your assistant'}")
        # The member's own presses: their pages, the review page or the chat card's Reject.
        return "Rejected by you" if by in ("review_page", "browser", "card") else ""
    if st == "expired":
        return esc(f"Expired {ui.when_text(it['expires_at'], now=now)}")
    if st == "replaced":
        if replaced_by is not None:
            no = f"#{replaced_by['board_no']}" if replaced_by.get("board_no") else "a newer change"
            return f"Replaced by <a href='/proposals/{esc(replaced_by['id'])}'>{esc(no)}</a>"
        return ""
    if st == "withdrawn":
        return (
            esc(f"Withdrawn by {it['app']}") if it["app"] and it["app"] not in (FROM_YOU, FROM_ADMIN) else ""
        )
    if st == "failed":
        said = (
            "Archidekt refused it" if (it["result"] or {}).get("error") in ARCHIDEKT_REFUSALS else NOT_APPLIED
        )
        return f"{said} · <a href='/proposals/{esc(it['id'])}'>Why</a>"
    return ""


def list_row(
    it: dict[str, Any],
    *,
    csrf: str | None,
    replaced_by: dict[str, Any] | None,
    now: float,
    open_id: str,
    qs: str,
) -> str:
    """One proposal on every chip but Waiting: the change (a link to it) and its deck, risk,
    status with its closing line, the app, when."""
    no = _no(it)
    num = f"<span class='no'>{esc(no)}</span>" if no else ""
    closing = _closing_html(it, csrf=csrf, replaced_by=replaced_by, now=now)
    sm = f"<span class='sm-only'>{closing}</span>" if closing else ""
    flag = f"<span class='flag'>{closing}</span>" if closing else ""
    cls = "tr" + (" open" if it["id"] == open_id else "")
    return (
        f"<div class='{cls}'{_deck_attr(it)}><span class='title'>"
        f"<a class='t' href='/proposals/{esc(it['id'])}{esc(qs)}'>"
        f"{num}{esc(it['summary'])}</a><span class='deck'>{esc(_deck_line(it))}"
        f"<span class='stin'>{_status_label(it)}</span></span>"
        f"<span class='sub'>{_risk_label(it)}{ui.app_chip(it['app'])}{ui.when(it['created_at'], now=now)}{sm}"
        "</span></span>"
        f"<span class='md-hide'>{_risk_label(it)}</span>"
        f"<span class='sm-hide'>{_status_label(it)}{flag}</span>"
        f"<span class='md-hide'>{ui.app_chip(it['app'])}</span>"
        f"<span class='md-hide'>{ui.when(it['created_at'], now=now)}</span></div>"
    )


# -- the page ------------------------------------------------------------------------------------


def _toolbar(data: dict[str, Any], query: dict[str, Any]) -> str:
    items = data["items"]
    decks: dict[str, str] = {}
    for it in items:
        if not it["untied"] and it["deck_id"].isdigit():
            decks.setdefault(it["deck_id"], str(it["deck_name"]))
    deck_opts = [("", "All decks"), *sorted(decks.items(), key=lambda kv: kv[1].casefold())]
    if query["deck_id"] and query["deck_id"] not in decks:
        deck_opts.append((query["deck_id"], query["deck_id"]))
    risks = list(RISKS) + ([PRESS_FILTER] if query["risk"] == "press" else [])
    apps = sorted(
        {it["app"] for it in items if it["app"] and it["app"] not in (FROM_YOU, FROM_ADMIN)}, key=str.casefold
    )
    froms = [("", "Any app"), *((a, a) for a in apps), (YOU, FROM_YOU), (ADMIN, FROM_ADMIN)]
    chosen_from = next((v for v, _t in froms if v and v.casefold() == query["from"].casefold()), "")
    return ui.toolbar(
        "/proposals",
        search_label="Search proposals",
        placeholder="Search deck or card",
        q=query["q"],
        filters=[
            ui.filter_select("deck_id", "Deck", deck_opts, query["deck_id"]),
            ui.filter_select("risk", "Risk", risks, query["risk"]),
            ui.filter_select("from", "From", froms, chosen_from),
            ui.filter_select("when", "When", list(WHENS), query["when"]),
        ],
        keep={
            "show": query["show"] if query["show"] != "waiting" else "",
            "group": query["group"],
            "order": query["order"],
        },
    )


def _bar() -> str:
    return ui.selection_bar(
        "Check the changes to apply or reject",
        [
            "<button type='button' class='btn-plain clear' hidden>Clear</button>",
            "<button type='submit' name='action' value='reject' class='btn-outline-danger' "
            "data-label='Reject' data-long>Reject checked</button>",
            "<button type='submit' name='action' value='apply' class='btn-primary' "
            "data-label='Apply' data-long>Apply checked</button>",
        ],
        label="Checked changes",
    )


def _waiting_table(
    rows: list[dict[str, Any]],
    data: dict[str, Any],
    *,
    csrf: str | None,
    query: dict[str, Any],
    now: float,
    open_id: str,
    qs: str,
) -> str:
    by_id = {it["id"]: it for it in rows}
    groups: dict[str, list[dict[str, Any]]] = {}
    untied: list[dict[str, Any]] = []
    for pid in data["waiting_order"]:  # oldest first, so the deck with the oldest leads
        it = by_id.get(pid)
        if it is None:
            continue
        if it["untied"]:
            untied.append(it)
        else:
            groups.setdefault(it["deck_id"], []).append(it)
    check_low = query["check"] == "low"
    out = [
        ui.table_open(*WAIT_COLS, cls="pr-table", label="Waiting changes"),
        ui.table_header(
            [
                ui.Column(
                    "",
                    html="<label class='chk'><input type='checkbox' class='all' data-all='*' "
                    "aria-label='Check every waiting change'></label>",
                ),
                ui.Column("Change"),
                ui.Column("Risk", "md-hide"),
                ui.Column("Status", "sm-hide"),
                ui.Column("From", "md-hide"),
                ui.Column("Made", "md-hide"),
                ui.Column(""),
            ]
        ),
    ]

    def group(key: str, title_html: str, aria: str, lines: list[dict[str, Any]], open_deck: str) -> None:
        oldest = _span(now - min(x["created_at"] for x in lines))
        meta = f"{len(lines)} waiting · oldest {oldest}" + (
            f" · <a href='{open_deck}'>Open deck</a>" if open_deck else ""
        )
        box = (
            f"<label class='chk'><input type='checkbox' class='all' data-all='{esc(key)}' "
            f"aria-label='{esc(aria)}'></label>"
        )
        out.append(ui.group_row(title_html, meta, check_html=box))
        for x in lines:
            out.append(
                waiting_row(
                    x, group=key, now=now, checked=check_low and _low_checkable(x), open_id=open_id, qs=qs
                )
            )

    for deck_id, lines in groups.items():
        name = str(lines[0]["deck_name"])
        href = f"/decks/{esc(deck_id)}"
        title = f"<a id='deck-{esc(deck_id)}' href='{href}'>{esc(name)}</a>"
        group(deck_id, title, f"All waiting changes to {name}", lines, href)
    if untied:
        group(
            "untied",
            f"<span id='untied'>{esc(UNTIED)}</span>",
            "All changes not tied to one of your decks",
            untied,
            "",
        )
    out.append("</div>")
    return (
        "<form method='post' action='/proposals/select' class='wform' id='waiting'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf or '')}'>"
        "<input type='hidden' name='back' value='/proposals'>" + "".join(out) + _bar() + "</form>"
    )


def _replacements(
    state: Any, sub: str, rows: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """For each replaced row shown: the proposal that replaced it (id and board number)."""
    ids = [it["id"] for it in rows if it["state"] == "replaced"]
    if not ids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for link in state.db.proposal_links(sub, ids):
        if link["kind"] == "replaces" and link["other_id"] in ids:
            new = by_id.get(link["proposal_id"]) or state.db.get_proposal(link["proposal_id"], sub)
            if new:
                out[link["other_id"]] = {"id": new["id"], "board_no": new.get("board_no")}
    return out


def _all_table(
    rows: list[dict[str, Any]],
    *,
    state: Any,
    sub: str,
    data: dict[str, Any],
    csrf: str | None,
    query: dict[str, Any],
    now: float,
    open_id: str,
    qs: str,
) -> str:
    by_id = {it["id"]: it for it in data["items"]}
    repl = _replacements(state, sub, rows, by_id)
    newer = "old" if query["order"] != "old" else ""
    sort = (
        f"<a class='pr-sort' href='/proposals{esc(query_string(query, order=newer, offset=0))}' "
        f"aria-label='{'Made, oldest first' if newer else 'Made, newest first'}'>Made {icon('sort')}</a>"
    )
    out = [
        ui.table_open(*ALL_COLS, cls="pr-table", label="Proposals"),
        ui.table_header(
            [
                ui.Column("Change"),
                ui.Column("Risk", "md-hide"),
                ui.Column("Status", "sm-hide"),
                ui.Column("From", "md-hide"),
                ui.Column("Made", "md-hide", html=sort),
            ]
        ),
    ]
    if query["group"] == "deck":
        order: list[str] = []
        groups: dict[str, list[dict[str, Any]]] = {}
        for it in rows:
            key = "untied" if it["untied"] else it["deck_id"]
            if key not in groups:
                order.append(key)
                groups[key] = []
            groups[key].append(it)
        for key in order:
            first = groups[key][0]
            title = (
                esc(UNTIED)
                if key == "untied"
                else f"<a href='/decks/{esc(key)}'>{esc(first['deck_name'])}</a>"
            )
            n = len(groups[key])
            out.append(
                f"<div class='tr group pr-nochk'><span>{title}</span>"
                f"<span class='meta'>{n} shown</span></div>"
            )
            out += [
                list_row(it, csrf=csrf, replaced_by=repl.get(it["id"]), now=now, open_id=open_id, qs=qs)
                for it in groups[key]
            ]
    else:
        out += [
            list_row(it, csrf=csrf, replaced_by=repl.get(it["id"]), now=now, open_id=open_id, qs=qs)
            for it in rows
        ]
    out.append("</div>")
    return "".join(out)


def list_html(
    state: Any,
    sub: str,
    csrf: str | None,
    query: dict[str, Any],
    *,
    notice: str = "",
    open_id: str = "",
    now: float | None = None,
) -> str:
    """The whole list side of the page: title, widgets, the full list with chips, search, filters
    and paging."""
    now = time.time() if now is None else now
    data = gather(state, sub, now=now)
    since = when_since(query["when"], now=int(now)) if query["when"] else None
    matching = [it for it in data["items"] if _matches(it, query, since)]
    counts = {s: sum(1 for it in matching if _in_chip(it, s)) for s, _t in SHOWS}
    show = query["show"]
    rows = [it for it in matching if _in_chip(it, show)]
    qs = query_string(query)
    chips = ui.chips(
        ui.Chip(
            text, counts[s], "/proposals" + query_string(query, show=s, offset=0, check=""), current=s == show
        )
        for s, text in SHOWS
    )
    waiting_view = show in ("waiting", "needs_look")
    if waiting_view:
        side = "Waiting ones are grouped by deck, oldest deck first"
    elif query["group"] == "deck":
        side = (
            "Grouped by deck · "
            f"<a href='/proposals{esc(query_string(query, group='', offset=0))}'>Show newest first</a>"
            if query["order"] != "old"
            else "Grouped by deck · "
            f"<a href='/proposals{esc(query_string(query, group='', offset=0))}'>Show oldest first</a>"
        )
    else:
        first = "Oldest first" if query["order"] == "old" else "Newest first"
        grouped = esc(query_string(query, group="deck", offset=0))
        side = f"{first} · <a href='/proposals{grouped}'>Group by deck</a>"
    head = (
        f"<div class='list-head'><h2>All proposals <span class='ui-sub'>{counts['all']:,}</span></h2>"
        f"<span class='ui-sub'>{side if not waiting_view else esc(side)}</span></div>"
    )
    parts = [
        ui.page_head("Proposals", PAGE_SUB),
        notice,
        widgets_html(data, query, csrf, now=now),
        head,
        chips,
        _toolbar(data, query),
    ]
    filtered = bool(query["deck_id"] or query["risk"] or query["from"] or query["when"] or query["q"])
    if waiting_view:
        if rows:
            parts.append(f"<p class='ui-note'>{esc(WAITING_NOTE)}</p>")
            parts.append(_waiting_table(rows, data, csrf=csrf, query=query, now=now, open_id=open_id, qs=qs))
            parts.append("<script src='/static/proposals.js' defer></script>")
        else:
            empty = (
                NOTHING_MATCHES if filtered or show == "needs_look" and counts["waiting"] else NOTHING_WAITING
            )
            parts.append(f"<div class='ui-card pr-empty'><p>{esc(empty)}</p></div>")
    else:
        waiting_href = "/proposals" + query_string(query, show="waiting", offset=0)
        parts.append(
            "<p class='ui-note'>To apply or reject several at once, open "
            f"<a href='{esc(waiting_href)}'>Waiting</a>.</p>"
        )
        if query["order"] == "old":
            rows = list(reversed(rows))
        total = len(rows)
        offset = min(query["offset"], max(0, (total - 1) // ui.PER_PAGE * ui.PER_PAGE))
        page = rows[offset : offset + ui.PER_PAGE]
        if page:
            parts.append(
                _all_table(
                    page,
                    state=state,
                    sub=sub,
                    data=data,
                    csrf=csrf,
                    query=query,
                    now=now,
                    open_id=open_id,
                    qs=qs,
                )
            )
            newer = (
                "/proposals" + query_string(query, offset=max(0, offset - ui.PER_PAGE))
                if offset > 0
                else None
            )
            older = (
                "/proposals" + query_string(query, offset=offset + ui.PER_PAGE)
                if offset + ui.PER_PAGE < total
                else None
            )
            parts.append(ui.pager(offset, total, newer_href=newer, older_href=older, note=PAGER_NOTE))
        else:
            parts.append(
                "<div class='ui-card pr-empty'><p>"
                f"{esc(NOTHING_MATCHES if filtered else 'Nothing here yet.')}</p></div>"
            )
    return "".join(p for p in parts if p)


def panel_close_href(query: dict[str, Any]) -> str:
    return "/proposals" + query_string(query)


__all__ = [
    "PAGE_SUB",
    "list_html",
    "panel_close_href",
    "query_string",
    "read_query",
]


# The Proposals page's own styles (pages.py puts them in the page's head; the shared pieces are
# theme.UI_CSS). Theme tokens only.
PROPOSALS_CSS = """
/* a waiting row's cells are labels of its tick box: the whole row is the tap target */
.pr-table label{margin:0;font-weight:inherit;cursor:pointer}
.pr-table label.title{display:block}
.pr-table .flag .ui-lbl{height:20px;font-size:.76rem;margin-right:.25rem}
.pr-table .sm-hide .flag{margin-top:.25rem}
.pr-table .group.pr-nochk{grid-template-columns:minmax(0,1fr) auto}
.pr-table .th .pr-sort{color:inherit;text-decoration:none;display:inline-flex;align-items:center;gap:.2rem}
.pr-table .th .pr-sort svg.i{width:14px;height:14px}
.ui-drawer .apply{background:none;border:0;padding:0;margin-top:1rem}
.ui-drawer details>summary{align-items:center;gap:.4rem}
.ui-drawer details>summary::before{content:"";border:5px solid transparent;border-left:7px solid currentColor;
  border-right:0;flex:none}
.ui-drawer details[open]>summary::before{transform:rotate(90deg)}
.ui-drawer form.reject{margin:.75rem 0 .25rem}
.pr-undo{display:inline;margin:0}
.pr-undo .linkbtn{background:none;border:0;padding:0;margin:0;color:var(--link);text-decoration:underline;
  font:inherit;font-weight:400;line-height:inherit;min-height:0;width:auto}
@media (pointer:coarse){.pr-undo .linkbtn{min-height:44px;min-width:44px;text-align:left}}
.ui-bar .rejectask,.ui-drawer .rejectask{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;
  flex:1 1 100%}
.ui-bar .rejectask span,.ui-drawer .rejectask span{flex:1 1 10rem;font-weight:700}
.ui-bar .rejectask button,.ui-drawer .rejectask button{min-height:44px;margin:0}
.ui-bar .rejectask button:focus,.ui-drawer .rejectask button:focus{
  outline:2px solid var(--focus);outline-offset:2px}
.ui-bar button[disabled]{opacity:.55}
.ui-bar [hidden]{display:none}
@media (max-width:599.98px){
  /* two rows: the count beside Clear, then Reject and Apply side by side; at large text sizes
     the words wrap inside the buttons rather than out of them */
  .ui-bar{padding:8px;gap:8px}
  .ui-bar .count{flex:1 1 40%;min-width:0}
  .ui-bar button{height:auto;white-space:normal;line-height:1.2;padding:6px 8px}
  .ui-bar button[name=action]{flex:1 1 40%;min-width:0}
}
/* one proposal: the panel (from 1100 px) or its own page */
.ui-drawer .dh .no{color:var(--text-muted)}
.ui-drawer .pr-why{margin:-.25rem 0 .5rem}
.ui-drawer .summary-pills .ui-lbl{font-weight:700}
.ui-drawer ul.changes li{background:transparent;border-left-color:transparent}
.ui-drawer .apply{margin-top:1rem}
.ui-drawer .apply .choice{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:.75rem 0 .25rem}
.ui-drawer .apply .choice button,.ui-drawer .apply .choice .btn{margin:0}
.ui-drawer .pr-tech pre{max-height:22rem;overflow:auto;font-size:.82rem}
.ui-drawer details > summary{min-height:44px;display:flex;align-items:center;cursor:pointer}
.ui-drawer .page-only{margin:0 0 .5rem}
.ui-drawer .page-only a{display:inline-flex;align-items:center;min-height:44px}
/* "Open as a full page": the panel alone, in the page's own column */
.pr-full .ui-split.with-panel{display:block}
.pr-full .ui-split.with-panel .ui-drawer{display:block;position:static;max-height:none;overflow:visible;
  box-shadow:none;border:0;padding:0;max-width:52rem;margin:0 auto}
.pr-full .ui-split.with-panel .page-only{display:block}
.pr-full .panel-only{display:none}
.pr-empty p{margin:0}
.wdg .det .bar-split{flex:1 1 100%;margin-top:.25rem}
/* a group row's meta wraps rather than push the page sideways at large text sizes */
.ui-table .group > *{min-width:0}
.ui-table .group .meta{white-space:normal}
@media (min-width:900px){.ui-table .group .meta a{white-space:nowrap}}
@media (min-width:600px){.ui-table .group .meta{text-align:right}}
/* change titles break between words (a long card name still wraps) */
.pr-table .title .t{overflow-wrap:break-word}
/* a widget's number and its word wrap apart at large text sizes */
.wdg .big{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:.35rem}
.wdg .big small{margin-left:0}
"""
