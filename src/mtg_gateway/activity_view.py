"""The two Activity pages as dashboards: the admin's Activity
(/admin/activity, everyone's audit rows) and the member's My activity (/activity, their own).

Both read the audit log in pages of 25 with the filters in SQL (no silent cap: a row is kept a
year, db.AUDIT_RETENTION_SECONDS), and share the event words, the labels and the app a row came
from. The labels follow "Labels on the Activity pages": red Refused (the gateway turned down a
sign-in, an app's access or an approval), red Failed, green Applied, grey Rejected (a proposal
someone said no to, or a Deny on the consent screen). Admin actions have no label, only a grey
shield before their name. A row shows an app only when it has a client id; otherwise a dash.

An action an admin takes on a member's account is written twice (admin.py ``act``): the admin's
row (browser client) and a copy on the member's log (administrator client). The admin page lists
the admin's row only; the copy shows on the member's My activity.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

from . import tool_labels, ui
from .companion import EVENT_LABELS, card_model_line, event_label, member_name
from .config import MANAGED_SETTINGS
from .decks import ADMIN_CLIENT, BROWSER_CLIENT, DECK_LIST_LAST
from .settings_page import FOLDED, change_line
from .settings_page import label as setting_label
from .theme import esc, icon

PER_PAGE = ui.PER_PAGE

# -- which events carry which label -------------------------------------------------------------

REFUSED_EVENTS = (
    "approval_refused",
    "login_rejected_group",
    "login_rejected_disabled",
    "login_rejected_issuer",
    "refresh_rejected",
    "refresh_reuse_detected",
    "code_reuse_detected",
    "disabled_user_refused",
    "not_in_group_refused",
    "cimd_rejected",
)
FAILED_EVENTS = (
    "proposal_failed",
    "archidekt_link_failed",
    "backup_failed",
    "backup_delete_failed",
    "backup_delete_given_up",
)
SIGNIN_EVENTS = (
    "browser_login",
    "login_ok",
    "login_denied",
    "login_rejected_group",
    "login_rejected_disabled",
    "login_rejected_issuer",
    "issuer_repinned",
    "tokens_issued",
    "tokens_revoked",
    "refresh_rejected",
    "refresh_reuse_detected",
    "code_reuse_detected",
    "signed_out_everywhere",
)
REPORT_EVENTS = ("report_created", "report_more_games", "report_deleted", "snapshot_deleted")
ADMIN_ACTIONS = ("admin_disable", "admin_enable", "admin_revoke", "admin_unlink", "admin_delete_data")
# admin actions written once (no member copy): the admin page's "Admin actions" counts them too
ADMIN_ONCE = ("setting_changed", "card_model_removed")

# The admin's own row of an action on a member's account, as the admin page names it.
ADMIN_PAGE_NAMES = {
    "admin_disable": "Account disabled",
    "admin_enable": "Account turned back on",
    "admin_revoke": "Assistants' access and sign-ins revoked",
    "admin_unlink": "Archidekt link removed",
    "admin_delete_data": "Former member's data deleted",
}
# The acting admin's own copy on their My activity (browser client), in the first person; the
# member's copy (administrator client) keeps EVENT_LABELS' "An administrator ... your account".
FIRST_PERSON_NAMES = {
    "admin_disable": "You disabled a member's account",
    "admin_enable": "You turned a member's account back on",
    "admin_revoke": "You revoked a member's app access and sign-ins",
    "admin_delete_data": "You deleted a former member's data",
    "admin_unlink": "You unlinked a member's Archidekt account",
}
# The "Admin actions" widget's line: (one, more than one).
ADMIN_ACTION_WORDS = {
    "setting_changed": ("setting changed", "settings changed"),
    "card_model_removed": ("card model removed", "card models removed"),
    "admin_disable": ("account disabled", "accounts disabled"),
    "admin_enable": ("account turned back on", "accounts turned back on"),
    "admin_revoke": ("member's access revoked", "members' access revoked"),
    "admin_unlink": ("Archidekt link removed", "Archidekt links removed"),
    "admin_delete_data": ("member's data deleted", "members' data deleted"),
}

ICONS = {
    "browser_login": "login",
    "login_ok": "login",
    "login_denied": "login",
    "issuer_repinned": "login",
    "signed_out_everywhere": "login",
    "tokens_issued": "link",
    "tokens_revoked": "link",
    "apps_disconnected": "link",
    "archidekt_linked": "link",
    "archidekt_unlinked": "link",
    "archidekt_link_expired": "link",
    "archidekt_link_unsealed": "link",
    "archidekt_session_refreshed": "refresh",
    "setting_changed": "settings",
    "approval_mode_set": "settings",
    "view_choices_forgotten": "settings",
    "report_created": "report",
    "report_more_games": "report",
    "report_deleted": "report",
    "snapshot_deleted": "bookmark",
    "folder_created": "folder",
    "folder_renamed": "folder",
    "folder_moved": "folder",
    "folder_deleted": "folder",
    "folder_delete_stopped": "folder",
    "deck_moved": "folder",
    "deck_commented": "comment",
    "deck_comment_edited": "comment",
    "deck_comment_deleted": "comment",
    "comment_voted": "comment",
    "deck_voted": "heart",
    "deck_bookmarked": "bookmark",
    "user_followed": "user",
    "groups_changed": "user",
    "membership_revoked": "user",
    "membership_unverifiable": "user",
    "collection_added": "collection",
    "collection_removed": "collection",
    "collection_updated": "collection",
    "collection_changed": "collection",
    "scan_session_created": "collection",
    "scan_session_saved": "collection",
    "scan_session_deleted": "collection",
    "deck_deleted": "trash",
    "backup_deleted": "trash",
    "card_model_removed": "trash",
}

# The error a failed row stores, in words; the code goes only in the row's details.
ERROR_WORDS = {
    "rate_limited": "Archidekt asked the gateway to slow down",
    "unavailable": "Archidekt can't be reached right now",
    "not_found": "Archidekt could not find that deck",
    "forbidden": "Archidekt refused access to that deck",
    "auth": "Archidekt did not accept that username and password",
    "backup_failed": "The backup copy could not be made",
}
OTHER_ERROR = "Something went wrong"
GONE_DECK = "a deck that was removed"
NO_DETAIL = "No detail"
NO_NAME = "a member with no name"  # a member with neither a username nor an email on record
NO_MEMBER = "No member"  # a row with no member recorded (a refused sign-in before any account)


def refused_label(word: str = "Refused", *, title: str = "") -> str:
    """The red Refused label with the ban sign (theme icon "ban"), so it differs from Failed by icon
    as well as by word."""
    return ui.label_for("refused", word=word, title=title)


# Detail keys as words on the admin page; keys that only name a row elsewhere are left to
# the row's details. Unmapped keys show as stored.
DETAIL_WORDS = {
    "deck_id": "deck",
    "restores_snapshot": "restore point",
    "games": "games",
    "groups": "groups",
    "reason": "reason",
    "family": "family",
    "by": "by",
    "target": "account",
    "tokens": "tokens",
    "revoked": "tokens",
    "scope": "scope",
    "folder_id": "name",
    "error": "error",
    "archidekt_username": "Archidekt account",
    "via": "via",
    "cards": "cards",
    "quantity": "cards",
    "risk": "risk",
    "version": "version",
    "sessions": "sessions",
}
HIDDEN_KEYS = frozenset(
    {
        "proposal_id",
        "proposal_ids",
        "report_id",
        "snapshot_id",
        "backup_deck_id",
        "batch_id",
        "comment",
        "relation_id",
        "tag_id",
        "id",
        "card_key",
        "client_name",
        "parent",
        "verified",
        "origin",
        "applied",
        "not_sent",
        "failed",
        "later",
        "due",
        "attempts",
        "set",
        "redirect_uris",
        "redirect_hosts",
        "redirect_uri_count",
        "truncated",
        "raw",
    }
)
VIA_WORDS = {
    "review_page": "on the review page",
    "mcp": "by an assistant",
    "app": "on the in-chat card",
    "browser": "in the browser",
    "api": "through the API",
    "android_app": "in the Android app",
}

# -- the filters --------------------------------------------------------------------------------

KINDS_ADMIN = ("all", "refused", "failed", "admin", "proposals", "signins", "reports")
KINDS_MEMBER = ("all", "proposals", "signins", "reports", "refused", "failed", "byadmin")
KIND_WORDS = {
    "all": "All",
    "refused": "Refused",
    "failed": "Failed",
    "admin": "Admin actions",
    "proposals": "Proposals",
    "signins": "Sign-ins",
    "reports": "Reports",
    "byadmin": "By an administrator",
}
TIMES = (("", "Any time"), ("24h", "Last 24 hours"), ("7d", "Last 7 days"), ("30d", "Last 30 days"))
TIME_SECONDS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


@dataclass
class Query:
    kind: str = "all"
    member: str = ""
    app: str = ""
    event: str = ""
    time: str = ""
    q: str = ""
    offset: int = 0
    open: int | None = None

    @classmethod
    def read(cls, params: Mapping[str, str], kinds: tuple[str, ...]) -> Query:
        kind = params.get("kind", "all")
        try:
            offset = max(0, int(params.get("offset", "0") or 0))
        except ValueError:
            offset = 0
        try:
            opened: int | None = int(params.get("open", "") or "")
        except ValueError:
            opened = None
        return cls(
            kind=kind if kind in kinds or kind == "problems" else "all",
            member=str(params.get("sub", ""))[:200],
            app=str(params.get("app", ""))[:120],
            event=str(params.get("event", ""))[:80],
            time=params.get("time", "") if params.get("time", "") in TIME_SECONDS else "",
            q=" ".join(str(params.get("q", "")).split())[:100],
            offset=offset,
            open=opened,
        )

    def href(self, path: str, **change: Any) -> str:
        """This list's address with ``change`` applied; a filter change starts at the first page."""
        values = {
            "kind": self.kind,
            "sub": self.member,
            "app": self.app,
            "event": self.event,
            "time": self.time,
            "q": self.q,
            "offset": self.offset,
            "open": self.open,
        }
        if any(k != "offset" and k != "open" for k in change):
            values["offset"] = 0
        if "open" not in change:
            values["open"] = None
        values.update(change)
        out = {k: v for k, v in values.items() if v not in (None, "", 0) and not (k == "kind" and v == "all")}
        return path + ("?" + urlencode(out) if out else "")


# -- reading the log ---------------------------------------------------------------------------


@dataclass
class Scope:
    """Which rows a page may show: one member's (My activity), or everyone's but the member copies
    of an admin's actions (the admin page)."""

    sub: str | None = None
    admin: bool = False
    names: dict[str, str] = field(default_factory=dict)  # client id -> registered name


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _marks(items: Iterable[Any]) -> str:
    return ",".join("?" for _ in items)


REFUSED_SQL = (
    f"(event IN ({_marks(REFUSED_EVENTS)}) OR (event = 'login_denied' AND NOT ("
    "detail_json IS NOT NULL AND json_valid(detail_json) AND json_extract(detail_json, '$.by') = 'user')))"
)
FAILED_SQL = f"event IN ({_marks(FAILED_EVENTS)})"


def _kind_sql(kind: str) -> tuple[str, list[Any]]:
    if kind == "refused":
        return REFUSED_SQL, list(REFUSED_EVENTS)
    if kind == "failed":
        return FAILED_SQL, list(FAILED_EVENTS)
    if kind == "problems":
        return f"({REFUSED_SQL} OR {FAILED_SQL})", [*REFUSED_EVENTS, *FAILED_EVENTS]
    if kind == "admin":
        return (
            f"(event IN ({_marks(ADMIN_ACTIONS)}) OR event IN ({_marks(ADMIN_ONCE)}))",
            [*ADMIN_ACTIONS, *ADMIN_ONCE],
        )
    if kind == "byadmin":
        return "client_id = ?", [ADMIN_CLIENT]
    if kind == "proposals":
        return "(event LIKE 'proposal%' OR event = 'apply_needs_user')", []
    if kind == "signins":
        return f"event IN ({_marks(SIGNIN_EVENTS)})", list(SIGNIN_EVENTS)
    if kind == "reports":
        return f"event IN ({_marks(REPORT_EVENTS)})", list(REPORT_EVENTS)
    return "", []


class Log:
    """The audit rows one page may show, read with SQL filters (every row a year keeps)."""

    def __init__(self, db: Any, scope: Scope, *, admin_label: str):
        self.db = db
        self.scope = scope
        self.admin_label = admin_label  # the administrator client's app name on this page
        self._clients: list[str] | None = None
        self._events: list[str] | None = None

    def _rows(self, sql: str, args: Iterable[Any]) -> list[Any]:
        with self.db._lock:
            return self.db._conn.execute(sql, tuple(args)).fetchall()

    def _scope_sql(self) -> tuple[list[str], list[Any]]:
        if self.scope.sub is not None:
            return ["sub = ?"], [self.scope.sub]
        # the admin page: an admin's action on a member's account once, the admin's own row
        return ["(client_id IS NULL OR client_id != ?)"], [ADMIN_CLIENT]

    # the apps -----------------------------------------------------------------------------
    def clients(self) -> list[str]:
        if self._clients is None:
            where, args = self._scope_sql()
            sql = (
                f"SELECT DISTINCT client_id FROM audit_log WHERE {' AND '.join(where)} "
                "AND client_id IS NOT NULL"
            )
            self._clients = [r[0] for r in self._rows(sql, args)]
        return self._clients

    def app_name(self, client_id: str | None) -> str:
        """The app a row came from, by name only; "" for a row with no client id."""
        if not client_id:
            return ""
        if client_id == BROWSER_CLIENT:
            return "Website"
        if client_id == ADMIN_CLIENT:
            return self.admin_label
        return self.scope.names.get(client_id) or "An assistant"

    def app_options(self) -> list[tuple[str, str]]:
        named = sorted(
            {self.app_name(c) for c in self.clients() if c not in (BROWSER_CLIENT, ADMIN_CLIENT)},
            key=str.casefold,
        )
        opts = [("", "Any app")] + [(f"app:{n}", n) for n in named] + [("website", "Website")]
        if self.scope.sub is not None:
            # only a member's copy of an admin's action carries the administrator client; the admin
            # page lists the admin's own row instead (from the website), so it has no such option
            opts.append(("admin", self.admin_label))
        return opts + [("none", "No app")]

    def _app_sql(self, app: str) -> tuple[str, list[Any]]:
        if app == "website":
            return "client_id = ?", [BROWSER_CLIENT]
        if app == "admin":
            # the admin page's Admin app: the administrator client (only member copies carry it,
            # so it lists nothing there) and the admin's own rows are the website's
            return "client_id = ?", [ADMIN_CLIENT]
        if app == "none":
            return "client_id IS NULL", []
        if app.startswith("app:"):
            ids = [
                c
                for c in self.clients()
                if c not in (BROWSER_CLIENT, ADMIN_CLIENT) and self.app_name(c) == app[4:]
            ]
            return (f"client_id IN ({_marks(ids)})", ids) if ids else ("0", [])
        return "", []

    # the filters ----------------------------------------------------------------------------
    def where(
        self,
        query: Query,
        *,
        kind: bool = True,
        search: Callable[[str], tuple[str, list[Any]]] | None = None,
        now: float | None = None,
    ) -> tuple[str, list[Any]]:
        where, args = self._scope_sql()
        if query.member and self.scope.sub is None:
            # one member: their rows, and the admin's rows of an action on their account
            where.append(
                "(sub = ? OR (event LIKE 'admin\\_%' ESCAPE '\\' AND detail_json IS NOT NULL "
                "AND json_valid(detail_json) AND json_extract(detail_json, '$.target') = ?))"
            )
            args += [query.member, query.member]
        if query.app:
            sql, a = self._app_sql(query.app)
            if sql:
                where.append(sql)
                args += a
        if query.event:
            where.append("event = ?")
            args.append(query.event)
        if query.time:
            where.append("at >= ?")
            args.append(int((time.time() if now is None else now) - TIME_SECONDS[query.time]))
        if kind and query.kind != "all":
            sql, a = _kind_sql(query.kind)
            if sql:
                where.append(sql)
                args += a
        if query.q and search is not None:
            sql, a = search(query.q)
            where.append(sql)
            args += a
        return " AND ".join(where), args

    def count(self, where: str, args: Iterable[Any]) -> int:
        return int(self._rows(f"SELECT COUNT(*) FROM audit_log WHERE {where}", args)[0][0])

    def page(self, where: str, args: list[Any], offset: int) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT id, at, sub, client_id, event, detail_json FROM audit_log "
            f"WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*args, PER_PAGE, offset],
        )
        return [_row(r) for r in rows]

    def events(self) -> list[str]:
        if self._events is None:
            where, args = self._scope_sql()
            sql = f"SELECT DISTINCT event FROM audit_log WHERE {' AND '.join(where)}"
            self._events = [r[0] for r in self._rows(sql, args)]
        return self._events

    def since(self, seconds: int, now: float, extra: str = "", extra_args: Iterable[Any] = ()) -> list[Any]:
        where, args = self._scope_sql()
        where.append("at >= ?")
        args.append(int(now - seconds))
        if extra:
            where.append(extra)
            args += list(extra_args)
        return self._rows(
            f"SELECT at, sub, client_id, event, detail_json FROM audit_log WHERE {' AND '.join(where)}", args
        )


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    raw = d.pop("detail_json")
    try:
        d["detail"] = json.loads(raw) if raw else None
    except ValueError:
        d["detail"] = {"raw": raw}
    return d


def _detail(r: Any) -> dict[str, Any]:
    raw = r["detail_json"] if not isinstance(r, dict) else None
    if isinstance(r, dict):
        d = r.get("detail")
        return d if isinstance(d, dict) else {}
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def is_refused(event: str, detail: Mapping[str, Any]) -> bool:
    if event == "login_denied":
        return detail.get("by") != "user"
    return event in REFUSED_EVENTS


# -- one row in words --------------------------------------------------------------------------


def status_html(event: str, detail: Mapping[str, Any], *, title: str = "") -> str:
    """The short status label before an event's name, or "" for an event without a status."""
    if is_refused(event, detail):
        return refused_label(title=title)
    if event in FAILED_EVENTS:
        return ui.label_for("failed", title=title)
    if event == "proposal_applied":
        return ui.label_for("applied", title=title)
    if event == "proposal_rejected" or (event == "login_denied" and detail.get("by") == "user"):
        return ui.label_for("rejected", title=title)
    return ""


def event_name(event: str, client_id: str | None, *, admin_page: bool) -> str:
    if event in ADMIN_ACTIONS:
        if admin_page:
            return ADMIN_PAGE_NAMES[event]
        if client_id != ADMIN_CLIENT:
            return FIRST_PERSON_NAMES[event]
    return event_label(event)


def event_html(event: str, client_id: str | None, detail: Mapping[str, Any], *, admin_page: bool) -> str:
    """A short status label when the event has one, then its name as plain text that wraps; an
    admin action has the grey shield and no label, any other event its icon."""
    title = event if admin_page else ""
    lbl = status_html(event, detail, title=title)
    if event in ADMIN_ACTIONS:
        mark = icon("shield")
    elif lbl:
        mark = ""
    else:
        mark = icon(ICONS.get(event) or ("proposals" if event.startswith("proposal") else "activity"))
    tip = f" title='{esc(event)}'" if admin_page else ""
    return (
        f"<span class='evt'{tip}>{lbl}<span class='en'>{mark}"
        f"<span>{esc(event_name(event, client_id, admin_page=admin_page))}</span></span></span>"
    )


def error_words(code: Any) -> str:
    return ERROR_WORDS.get(str(code), OTHER_ERROR)


@dataclass
class Names:
    """The lookups a row's detail needs (no Archidekt call): deck names from the member's proposals
    and snapshots and their deck list as last read, folder names from their folder tree as last
    read, and members' names."""

    db: Any
    decks: Any = None
    _decks: dict[str, tuple[dict[str, str], set[str] | None]] = field(default_factory=dict)
    _folders: dict[str, dict[str, str]] = field(default_factory=dict)
    _members: dict[str, str | None] = field(default_factory=dict)
    _proposals: dict[tuple[str, str], Any] = field(default_factory=dict)

    def _deck_info(self, sub: str) -> tuple[dict[str, str], set[str] | None]:
        if sub not in self._decks:
            names = {k: v for k, v in self.db.history_decks(sub).items() if v}
            listed: set[str] | None = None
            held = None
            if self.decks is not None:
                try:
                    held = self.decks.deck_lists.last(sub, DECK_LIST_LAST)
                except Exception:  # noqa: BLE001 - a lookup for words only
                    held = None
            if held is not None and isinstance(held[1], list):
                listed = set()
                for d in held[1]:
                    if isinstance(d, dict) and d.get("id") is not None:
                        listed.add(str(d["id"]))
                        if d.get("name"):
                            names[str(d["id"])] = str(d["name"])
            self._decks[sub] = (names, listed)
        return self._decks[sub]

    def deck(self, sub: str | None, deck_id: Any, *, own: bool = True) -> tuple[str | None, bool]:
        """(the deck's name or None, whether it is known to be gone from the member's list). A deck
        of someone else's (``own`` False: a like, a comment) is never judged gone by that list."""
        if not sub:
            return None, False
        names, listed = self._deck_info(sub)
        did = str(deck_id)
        gone = own and listed is not None and did not in listed
        return names.get(did), gone

    def proposal_deck(self, sub: str | None, proposal_id: Any) -> Any:
        """The deck id of a proposal of the member's (rows that store only the proposal), or None."""
        if not sub or not proposal_id:
            return None
        key = (sub, str(proposal_id))
        if key not in self._proposals:
            prop = self.db.get_proposal(str(proposal_id), sub)
            deck_id = (prop or {}).get("deck_id")
            if prop and prop.get("deck_name") and deck_id not in (None, "", "account", "lists"):
                self._deck_info(sub)[0].setdefault(str(deck_id), str(prop["deck_name"]))
            self._proposals[key] = deck_id if deck_id not in (None, "", "account", "lists") else None
        return self._proposals[key]

    def folder(self, sub: str | None, folder_id: Any) -> str | None:
        if not sub or self.decks is None:
            return None
        if sub not in self._folders:
            out: dict[str, str] = {}
            try:
                held = self.decks.folder_trees.last(sub, DECK_LIST_LAST)
                if held is not None and held[1] is not None:
                    for f in self.decks._flatten_folders(held[1]).get("folders", []):
                        out[str(f["id"])] = str(f["name"])
            except Exception:  # noqa: BLE001 - a lookup for words only
                out = {}
            self._folders[sub] = out
        return self._folders[sub].get(str(folder_id))

    def member(self, sub: Any) -> str | None:
        key = str(sub or "")
        if not key:
            return None
        if key not in self._members:
            self._members[key] = member_name(self.db, key)
        return self._members[key]


# Events about someone else's deck: the member's own deck list says nothing about whether it is gone.
OTHERS_DECKS = frozenset(
    {
        "deck_voted",
        "deck_bookmarked",
        "deck_commented",
        "deck_comment_edited",
        "deck_comment_deleted",
        "comment_voted",
    }
)


def _deck_of(row: Mapping[str, Any], d: Mapping[str, Any], names: Names) -> Any:
    deck_id = d.get("deck_id")
    if deck_id in (None, ""):
        deck_id = names.proposal_deck(row["sub"], d.get("proposal_id"))
    return deck_id


def _deck_html(names: Names, sub: str | None, deck_id: Any, *, link: bool, own: bool = True) -> str:
    """The deck by name (a link on My activity); "a deck that was removed" when it is gone from
    the member's deck list as last read. A deck the gateway has no name for: "Open the deck" on
    My activity (never its id), the id on the admin page."""
    name, gone = names.deck(sub, deck_id, own=own)
    href = f"/decks/{quote(str(deck_id), safe='')}"
    if gone:
        return f"<span class='gone'>{GONE_DECK}</span>"
    if name:
        return f"<a href='{href}'>{esc(name)}</a>" if link else esc(name)
    # the admin page: the deck's id stays in the opened row's details, never in the cell
    return ui.cap_link("Open the deck", href) if link else f"<span class='gone'>{GONE_DECK}</span>"


def _member_chip(log: Any, client_id: str | None) -> str:
    """The By column's app chip, its mark by the client (ui.client_brand)."""
    return ui.app_chip(log.app_name(client_id), client_id=client_id)


def member_detail_html(row: Mapping[str, Any], names: Names) -> str:
    """A row's detail on My activity: the deck and what to open, never an id."""
    event, d, sub = row["event"], _detail(row), row.get("sub")
    said = change_line(d) if event == "setting_changed" else None
    if event == "card_model_removed":
        said = card_model_line(d, names.member(d.get("added_by")))
    bits: list[str] = []
    if said:
        bits.append(esc(said))
    deck_id = _deck_of(row, d, names)
    if deck_id not in (None, ""):
        bits.append(_deck_html(names, sub, deck_id, link=True, own=event not in OTHERS_DECKS))
    if event in ("folder_created", "folder_renamed", "folder_moved"):
        folder = names.folder(sub, d.get("folder_id"))
        if folder:
            bits.append(esc(folder))
    if event == "collection_removed":
        n = d.get("quantity")
        bits.append(f"{int(n)} card{'' if int(n) == 1 else 's'}" if isinstance(n, int) else "1 card row")
    if event == "collection_added" and isinstance(d.get("cards"), int):
        bits.append(f"{d['cards']} card{'' if d['cards'] == 1 else 's'}")
    if d.get("archidekt_username"):
        bits.append(f"Archidekt account {esc(d['archidekt_username'])}")
    if d.get("error") not in (None, "") and event in (
        "proposal_failed",
        "archidekt_link_failed",
        "backup_failed",
    ):
        bits.append(esc(error_words(d["error"])))  # in words only: the code stays off a member's page
    pid = d.get("proposal_id")
    if pid:
        href = f"/proposals/{quote(str(pid), safe='')}"
        text = "Open the proposal to see why" if event == "proposal_failed" else "Open the proposal"
        bits.append(ui.cap_link(text, href))
    # a deleted report has no page to open (its deck, when recorded, is named above)
    if event in REPORT_EVENTS and event != "report_deleted" and d.get("report_id"):
        bits.append(ui.cap_link("Open the report", f"/history/reports/{quote(str(d['report_id']), safe='')}"))
    if d.get("via") in VIA_WORDS and d.get("via") not in ("mcp", "app"):  # those: the By column
        bits.append(VIA_WORDS[d["via"]])
    if d.get("by") == "deck sync":
        bits.append("During deck sync")
    if d.get("by") == "admin" or row.get("client_id") == ADMIN_CLIENT:
        bits.append("By an administrator")
    if event in ADMIN_ACTIONS and row.get("client_id") != ADMIN_CLIENT and d.get("target"):
        who = names.member(d["target"])
        if who:
            bits.append(f"Account {esc(who)}")
    if not bits:
        return f"<span class='none'>{NO_DETAIL}</span>"
    return " · ".join(bits)


def _value(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) or "none"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def admin_detail_html(row: Mapping[str, Any], names: Names) -> str:
    """A row's detail on the admin page: the setting and card-model sentences as
    today, any other detail as "key value" pairs in words."""
    event, d, sub = row["event"], _detail(row), row.get("sub")
    if event == "setting_changed" and (said := change_line(d)):
        return f"<span class='said'>{esc(said)}</span>"
    if event == "card_model_removed" and (said := card_model_line(d, names.member(d.get("added_by")))):
        return f"<span class='said'>{esc(said)}</span>"
    pairs: list[str] = []
    items = list(d.items())
    if "deck_id" not in d and (deck_id := _deck_of(row, d, names)) not in (None, ""):
        items.insert(0, ("deck_id", deck_id))
    for k, v in items:
        if k in HIDDEN_KEYS or v in (None, "") or k == "client_name":
            continue
        word = DETAIL_WORDS.get(k, k)
        if k == "deck_id":
            shown = _deck_html(names, sub, v, link=False, own=event not in OTHERS_DECKS)
        elif k == "folder_id":
            folder = names.folder(sub, v)
            if not folder:
                continue
            shown = esc(folder)
        elif k == "target":
            shown = esc(names.member(v) or str(v))
        elif k == "tool":
            # the tool in words; its raw name stays in the row's details (the stored detail)
            shown = esc(tool_labels.tool_name(str(v)))
        elif k == "error":
            shown = f"<span title='{esc(v)}'>{esc(error_words(v))}</span>"
        elif k == "via":
            shown = esc(VIA_WORDS.get(str(v), str(v)))
            pairs.append(f"<span class='kv'>{shown}</span>")
            continue
        else:
            shown = esc(_value(v))
        pairs.append(f"<span class='kv'><span class='k'>{esc(word)}</span> {shown}</span>")
    return "".join(pairs) or f"<span class='none'>{NO_DETAIL}</span>"


# -- the page parts ----------------------------------------------------------------------------


def _counts(rows: Iterable[Any], log: Log) -> dict[str, int]:
    """Events per app ("Claude 3", "no app 4"), in the order the widget lists them."""
    out: dict[str, int] = {}
    for r in rows:
        name = log.app_name(r["client_id"]) or "no app"
        out[name] = out.get(name, 0) + 1
    order = sorted((n for n in out if n not in ("Website", log.admin_label, "no app")), key=str.casefold)
    order += [n for n in ("Website", log.admin_label, "no app") if n in out]
    return {n: out[n] for n in order}


def _problem_widget(rows: list[Any], href: str) -> str:
    refused = sum(1 for r in rows if is_refused(r["event"], _detail(r)))
    failed = sum(1 for r in rows if r["event"] in FAILED_EVENTS)
    labels = refused_label(f"Refused {refused}") + ui.label_for("failed", word=f"Failed {failed}")
    return ui.widget(
        "Refused or failed",
        "alert",
        refused + failed,
        unit="in 7 days",
        detail_html=labels,
        actions=[ui.button_link("Show them", href)],
        tone="alert" if refused + failed else "",
    )


def _list_parts(
    *,
    path: str,
    query: Query,
    log: Log,
    kinds: tuple[str, ...],
    search: Callable[[str], tuple[str, list[Any]]],
    filters: list[str],
    search_label: str,
    placeholder: str,
    now: float,
) -> tuple[str, list[dict[str, Any]], int]:
    """The list's heading, chips and toolbar; the rows of this page and the total."""
    base, base_args = log.where(query, kind=False, search=search, now=now)
    chips = []
    total_all = 0
    for k in kinds:
        sql, a = _kind_sql(k)
        n = log.count(f"{base} AND {sql}" if sql else base, [*base_args, *a])
        if k == "all":
            total_all = n
        # the "Refused or failed" quick start (kind=problems) marks both chips as current
        current = query.kind == k or (query.kind == "problems" and k in ("refused", "failed"))
        chips.append(ui.Chip(KIND_WORDS[k], n, query.href(path, kind=k), current=current))
    where, args = log.where(query, search=search, now=now)
    total = log.count(where, args)
    offset = min(query.offset, max(0, (total - 1) // PER_PAGE * PER_PAGE))
    rows = log.page(where, args, offset)
    query.offset = offset
    head = ui.list_head("All activity", total_all, note="Newest first") + _chip_icons(ui.chips(chips))
    tools = ui.toolbar(
        path,
        search_label=search_label,
        placeholder=placeholder,
        q=query.q,
        filters=filters,
        keep={"kind": query.kind if query.kind != "all" else ""},
    )
    return head + tools, rows, total


def _chip_icons(chips_html: str) -> str:
    """Refused and Failed differ by icon on the chips too: the ban sign and the alert sign."""
    return chips_html.replace(">Refused <span", f">{icon('ban')}Refused <span", 1).replace(
        ">Failed <span", f">{icon('alert')}Failed <span", 1
    )


def _empty(path: str, query: Query, nothing_at_all: str) -> str:
    """What an empty list says: nothing recorded, or nothing matching the chosen filters."""
    if query.kind == "all" and not (query.member or query.app or query.event or query.time or query.q):
        return f"<div class='ui-card'><p>{nothing_at_all}</p></div>"
    return (
        "<div class='ui-card'><p>Nothing matches these filters. "
        + ui.cap_link("Show all activity", path)
        + "</p></div>"
    )


def _pager(path: str, query: Query, total: int) -> str:
    newer = query.href(path, offset=max(0, query.offset - PER_PAGE)) if query.offset > 0 else None
    older = query.href(path, offset=query.offset + PER_PAGE) if query.offset + PER_PAGE < total else None
    return ui.pager(query.offset, total, newer_href=newer, older_href=older)


def _event_options(log: Log, *, admin_page: bool) -> list[tuple[str, str]]:
    seen = log.events()
    named = sorted(
        ((e, event_name(e, BROWSER_CLIENT if admin_page else None, admin_page=admin_page)) for e in seen),
        key=lambda p: p[1].casefold(),
    )
    return [("", "Any event"), *named]


def _search(
    log: Log, names: Names, *, admin_page: bool, member_sub: str | None
) -> Callable[[str], tuple[str, list[Any]]]:
    """Search over the member, the event's name, the app and the detail, as SQL."""

    def build(q: str) -> tuple[str, list[Any]]:
        low = q.casefold()
        parts = ["detail_json LIKE ? ESCAPE '\\'", "event LIKE ? ESCAPE '\\'"]
        args: list[Any] = [_like(q), _like(q)]
        events = [
            e
            for e in set(log.events()) | set(EVENT_LABELS)
            if low in event_name(e, BROWSER_CLIENT, admin_page=admin_page).casefold()
            or (
                not admin_page
                and e in ADMIN_ACTIONS
                and low in event_name(e, ADMIN_CLIENT, admin_page=False).casefold()
            )
        ]
        if events:
            parts.append(f"event IN ({_marks(events)})")
            args += events
        clients = [c for c in log.clients() if low in log.app_name(c).casefold()]
        if clients:
            parts.append(f"client_id IN ({_marks(clients)})")
            args += clients
        if low == "no app" or (len(low) >= 2 and "no app".startswith(low)):
            parts.append("client_id IS NULL")
        if admin_page:
            with log.db._lock:
                subs = [
                    r[0]
                    for r in log.db._conn.execute(
                        "SELECT sub FROM users WHERE preferred_username LIKE ? ESCAPE '\\' "
                        "OR email LIKE ? ESCAPE '\\' OR sub LIKE ? ESCAPE '\\'",
                        (_like(q), _like(q), _like(q)),
                    ).fetchall()
                ]
            if subs:
                parts.append(f"sub IN ({_marks(subs)})")
                args += subs
        # setting rows by the setting's name, deck rows by the deck's name
        keys = [k for k in MANAGED_SETTINGS if low in setting_label(k).casefold()]
        for k in keys[:20]:
            parts.append("detail_json LIKE ? ESCAPE '\\'")
            args.append(_like(f'"key": "{k}"'))
        if member_sub is not None:
            deck_names, _listed = names._deck_info(member_sub)
            ids = [i for i, n in deck_names.items() if low in n.casefold()][:50]
            for i in ids:
                parts.append("detail_json LIKE ? ESCAPE '\\'")
                args.append(_like(f'"deck_id": "{i}"'))
                parts.append("detail_json LIKE ? ESCAPE '\\'")
                args.append(_like(f'"deck_id": {i}'))
        return "(" + " OR ".join(parts) + ")", args

    return build


def _spark(values: list[int]) -> str:
    top = max(values) or 1
    bars = "".join(f"<i style='height:{max(4, round(v * 100 / top))}%'></i>" for v in values)
    said = ", ".join(str(v) for v in values)
    return f"<span class='spark' role='img' aria-label='Events per day, oldest first: {said}'>{bars}</span>"


def _join(parts: Iterable[str]) -> str:
    return " · ".join(parts)


ACT_CSS = """
.act-page .ui-chip svg.i{width:15px;height:15px;margin-right:.3rem;vertical-align:-2px}
.act-page .wdg .det .ui-lbl{height:22px;font-size:.78rem}
.act-page .spark{flex:1 1 100%;display:flex;align-items:flex-end;gap:3px;height:36px;margin:.1rem 0 0}
.act-page .spark i{flex:1;display:block;min-height:2px;background:var(--text-muted);border-radius:2px 2px 0 0}
.act-page .spark i:last-child{background:var(--text)}
.act-page form.ui-tools{display:grid;grid-template-columns:minmax(0,1fr) repeat(var(--nf),minmax(0,10rem));
  gap:.5rem}
.act-page form.ui-tools .f{min-width:0}
.act-page form.ui-tools .search input{-webkit-appearance:none;appearance:none;width:100%;
  border:1px solid var(--control-border);border-radius:var(--radius)}
.act-page form.ui-tools .f select:not(.msel-native),.act-page form.ui-tools .f .msel-btn{width:100%}
@media (max-width:899.98px){.act-page form.ui-tools{grid-template-columns:repeat(var(--nf),minmax(0,1fr))}
  .act-page form.ui-tools .search{grid-column:1 / -1}}
@media (max-width:599.98px){.act-page form.ui-tools{grid-template-columns:repeat(2,minmax(0,1fr))}
  .act-page form.ui-tools .f:last-of-type:nth-of-type(odd){grid-column:1 / -1}}
.act-table .tr{column-gap:.75rem}
/* with large text a widget's word can be wider than its half of a phone's row */
.act-page .wdg .big,.act-page .wdg .lab,.act-page .wdg .det{overflow-wrap:anywhere}
@media (max-width:599.98px){
  /* two widgets a row on a phone, one when the text is large */
  .act-page .ui-widgets{grid-template-columns:repeat(auto-fit,minmax(min(100%,9rem),1fr))}}
/* below 900 px the rows stack their cells in two lines, so the column headings no longer fit */
@media (max-width:899.98px){.act-table .tr.th{display:none}}
.act-table .c-who{font-weight:700;min-width:0;overflow-wrap:anywhere}
.act-table .c-det{display:flex;flex-wrap:wrap;gap:.2rem .6rem;font-size:.9rem;align-items:baseline;
  overflow-wrap:anywhere}
.act-table .kv{max-width:100%}
.act-table .kv .k{color:var(--text-muted)}
.act-table .c-btns{display:flex;justify-content:flex-end;align-items:center;gap:0;margin:-.45rem 0;
  justify-self:end}
.act-table .c-btns .kebab{color:var(--text-muted);width:44px;height:44px}
.act-table .c-more{display:inline-grid;place-items:center;width:44px;min-height:44px;padding:0;
  margin:0;color:var(--text-muted);text-decoration:none}
.act-table .c-more svg.i{width:20px;height:20px}
.act-table .tr.open{background:var(--surface-2)}
.act-table .tr.open .c-more svg{transform:rotate(180deg)}
.act-table .more-body{grid-column:1 / -1;padding:.6rem 0 .25rem;margin-top:.45rem;
  border-top:1px solid var(--border-soft)}
.act-table .more-body[hidden]{display:none}
.act-table .more-body .ui-meta{margin:0 0 .25rem;font-size:.9rem}
.act-table .more-body .ui-actions{margin:.25rem 0}
.act-table .more-body .btn-plain{min-height:var(--ctl)}
"""

ADMIN_CSS = (
    ACT_CSS
    + """
.act-admin .act-table .tr{grid-template-columns:15rem 6.5rem minmax(0,1fr) 7rem 6.5rem 88px}
.act-admin .act-table .tr.th > span:last-child{text-align:right}
@media (max-width:899.98px){
  .act-admin .act-table .tr.ev{grid-template-columns:auto minmax(0,1fr) auto 88px;
    grid-template-areas:"ev who when more" "det det app more";row-gap:.3rem}
  .act-admin .act-table .c-ev{grid-area:ev} .act-admin .act-table .c-who{grid-area:who}
  .act-admin .act-table .c-when{grid-area:when} .act-admin .act-table .c-det{grid-area:det}
  .act-admin .act-table .c-app{grid-area:app;justify-self:end} .act-admin .act-table .c-btns{grid-area:more}
  .act-admin .act-table .more-body{grid-area:auto;grid-column:1 / -1}
}
@media (max-width:599.98px){
  .act-admin .act-table .tr.ev{display:flex;flex-wrap:wrap;align-items:center;gap:.3rem .5rem;
    position:relative;padding-right:92px}
  .act-admin .act-table .c-ev{flex:0 0 calc(100% - 6.5rem);order:1}
  .act-admin .act-table .c-when{flex:0 0 6rem;order:2;text-align:right;font-size:.86rem}
  .act-admin .act-table .c-who{order:3;font-size:.9rem}
  .act-admin .act-table .c-app{order:4;font-size:.86rem}
  .act-admin .act-table .c-det{order:5;flex:1 1 9rem;font-size:.86rem}
  .act-admin .act-table .c-btns{position:absolute;right:2px;top:50%;transform:translateY(-50%);margin:0}
  .act-admin .act-table .tr.open .c-btns{top:.35rem;transform:none}
  .act-admin .act-table .more-body{order:6;flex:1 1 100%;margin-right:-88px}
}
"""
)

MEMBER_CSS = (
    ACT_CSS
    + """
.act-member .act-table .tr{grid-template-columns:17rem minmax(0,1fr) 8rem 6.5rem}
.act-member .act-table .c-det{display:block;font-size:.92rem}
@media (max-width:899.98px){
  .act-member .act-table .tr.ev{grid-template-columns:minmax(0,1fr) auto;
    grid-template-areas:"ev when" "det app";
    row-gap:.3rem}
  .act-member .act-table .c-ev{grid-area:ev} .act-member .act-table .c-when{grid-area:when;justify-self:end}
  .act-member .act-table .c-det{grid-area:det} .act-member .act-table .c-app{grid-area:app;justify-self:end}
}
@media (max-width:599.98px){
  .act-member .act-table .tr.ev{display:flex;flex-wrap:wrap;align-items:baseline;gap:.3rem .5rem}
  .act-member .act-table .c-ev{flex:1 1 100%;order:1}
  .act-member .act-table .c-app{order:2;font-size:.86rem;align-self:center}
  .act-member .act-table .c-det{order:3;flex:1 1 8rem;font-size:.86rem}
  .act-member .act-table .c-when{order:4;flex:none;margin-left:auto;font-size:.86rem}
}
"""
)

HEAD_ADMIN = (
    f"<style>{ADMIN_CSS}</style><script src='/static/activity.js' defer></script>"
    "<script src='/static/admin.js' defer></script>"
)


# -- the admin's Activity page ---------------------------------------------------------------


def admin_body(state: Any, params: Mapping[str, str], *, nav: str, now: float | None = None) -> str:
    """/admin/activity: widgets, chips, search, filters, the lined-up list (rows open in
    place) and paging; every row the log keeps, none capped."""
    now = time.time() if now is None else now
    path = "/admin/activity"
    db = state.db
    query = Query.read(params, KINDS_ADMIN)
    scope = Scope(admin=True)
    log = Log(db, scope, admin_label="Admin")
    scope.names = db.client_names(c for c in log.clients() if c not in (BROWSER_CLIENT, ADMIN_CLIENT))
    names = Names(db, getattr(state, "decks", None))
    # every member's name in one read, so the rows need no lookup each (member_name's wording)
    with db._lock:
        people = db._conn.execute(
            "SELECT sub, preferred_username, email FROM users "
            "ORDER BY COALESCE(preferred_username, email, sub)"
        ).fetchall()
    for p in people:
        names._members[str(p["sub"])] = str(p["preferred_username"] or p["email"] or "") or NO_NAME

    # widgets
    week = log.since(7 * 86400, now)
    day = [r for r in week if r["at"] >= now - 86400]
    admin_rows = [r for r in week if r["event"] in ADMIN_ACTIONS or r["event"] in ADMIN_ONCE]
    by_kind: dict[str, int] = {}
    for r in admin_rows:
        by_kind[r["event"]] = by_kind.get(r["event"], 0) + 1
    admin_line = _join(
        f"{n} {ADMIN_ACTION_WORDS[e][0 if n == 1 else 1]}"
        for e, n in by_kind.items()
        if e in ADMIN_ACTION_WORDS
    )
    members: dict[str, int] = {}
    for r in week:
        if r["sub"]:
            members[r["sub"]] = members.get(r["sub"], 0) + 1
    with db._lock:
        everyone = int(db._conn.execute("SELECT COUNT(*) FROM users WHERE disabled_at IS NULL").fetchone()[0])
    day_members = len({r["sub"] for r in day if r["sub"]})
    apps = _join(
        [f"From {day_members} member{'' if day_members == 1 else 's'}"] * bool(day)
        + [f"{n} {c}" for n, c in _counts(day, log).items()]
    )
    top = max(members.items(), key=lambda kv: kv[1]) if members else None
    top_name = names.member(top[0]) if top else None
    fortnight = log.since(14 * 86400, now)
    start = int(now // 86400 * 86400) - 13 * 86400
    per_day = [0] * 14
    for r in fortnight:
        i = (int(r["at"]) - start) // 86400
        if 0 <= i < 14:
            per_day[i] += 1
    cards = [
        _problem_widget(week, query.href(path, kind="problems", time="7d", sub="", app="", event="", q="")),
        ui.widget(
            "Last 24 hours",
            "activity",
            len(day),
            unit="events",
            detail=apps,
            actions=[ui.button_link("Show them", query.href(path, time="24h", kind="all"))],
        ),
        ui.widget(
            "Admin actions",
            "shield",
            len(admin_rows),
            unit="in 7 days",
            detail=admin_line,
            actions=[
                ui.button_link("Show them", query.href(path, kind="admin", time="7d")),
                ui.button_link("Users", "/admin/users"),
            ],
        ),
        ui.widget(
            "Members active",
            "user",
            len(members),
            unit=f"of {everyone} in 7 days",
            detail=f"Most active: {top_name}, {top[1]} event{'' if top[1] == 1 else 's'}"
            if top and top_name
            else "",
            actions=[
                ui.button_link(
                    "Show this member only" if top_name == NO_NAME else f"Show {top_name} only",
                    query.href(path, sub=top[0]),
                )
            ]
            if top and top_name
            else (),
        ),
        ui.widget(
            "Last 14 days",
            "stats",
            sum(per_day),
            unit="events",
            detail_html=_spark(per_day),
            actions=[ui.button_link("Metrics", "/admin/metrics")],
            wide_sm=True,
        ),
    ]

    # the list
    member_opts = [("", "Everyone")] + [
        (p["sub"], str(p["preferred_username"] or p["email"] or p["sub"])) for p in people
    ]
    if query.member and query.member not in {v for v, _t in member_opts}:
        member_opts.append((query.member, names.member(query.member) or query.member))
    filters = [
        ui.filter_select("sub", "Member", member_opts, query.member),
        ui.filter_select("app", "App", log.app_options(), query.app),
        ui.filter_select("event", "Event", _event_options(log, admin_page=True), query.event),
        ui.filter_select("time", "When", TIMES, query.time),
    ]
    top_html, rows, total = _list_parts(
        path=path,
        query=query,
        log=log,
        kinds=KINDS_ADMIN,
        search=_search(log, names, admin_page=True, member_sub=None),
        filters=filters,
        search_label="Search activity",
        placeholder="Search member, event or detail",
        now=now,
    )
    if not rows:
        table = _empty(path, query, "No activity recorded.") if not total else ""
    else:
        cols = [
            ui.Column("Event"),
            ui.Column("Member"),
            ui.Column("Detail"),
            ui.Column("App"),
            ui.Column("When"),
            ui.Column("", html="<span class='sr-only'>Actions</span>"),
        ]
        table = (
            ui.table_open(
                "15rem 6.5rem minmax(0,1fr) 7rem 6.5rem 44px",
                "auto minmax(0,1fr) auto 44px",
                "1fr",
                cls="act-table",
                label="All activity",
            )
            + ui.table_header(cols)
            + "".join(_admin_row(r, log, names, query, path, now) for r in rows)
            + "</div>"
        )
    head = ui.page_head(
        "Admin: activity",
        "What members, their apps and admins did on this gateway, newest first. Kept for a year.",
    )
    return (
        f"<div class='act-page act-admin' style='--nf:4'>{head}{nav}"
        f"{ui.widgets(cards)}{top_html}"
        f"{table}{_pager(path, query, total)}</div>"
    )


def _admin_row(r: dict[str, Any], log: Log, names: Names, query: Query, path: str, now: float) -> str:
    rid, event, d = int(r["id"]), str(r["event"]), _detail(r)
    sub = r.get("sub")
    who_name = names.member(sub) if sub else None
    if sub and who_name:
        who = f"<a href='{esc(query.href(path, sub=sub))}'>{esc(who_name)}</a>"
    else:
        who = f"<span class='none'>{NO_MEMBER}</span>"
    cid = r.get("client_id")
    app = log.app_name(cid)
    opened = query.open == rid
    toggle = query.href(path, open=None if opened else rid) + f"#ev-{rid}"
    if cid:
        app_detail = f"{esc(app)} <span class='muted'>(<code>{esc(cid)}</code>)</span>"
    else:
        app_detail = "No app <span class='muted'>(no client id stored)</span>"
    member_detail = (
        f"{esc(who_name or NO_NAME)} <span class='muted'>(<code>{esc(sub)}</code>)</span>"
        if sub
        else NO_MEMBER
    )
    raw = json.dumps(r.get("detail"), sort_keys=True) if r.get("detail") is not None else ""
    actions = []
    if sub and who_name:
        only = "Show this member only" if who_name == NO_NAME else f"Show {who_name} only"
        actions.append(ui.button_link(only, query.href(path, sub=sub)))
    if event == "setting_changed" and isinstance(d.get("key"), str):
        spec = MANAGED_SETTINGS.get(d["key"])
        if spec is not None:
            opener = f"?open={spec.group}" if spec.group in FOLDED else ""
            actions.append(ui.button_link("Open this setting", f"/admin/settings{opener}#s-row-{spec.key}"))
    body = ui.meta(
        [
            ("Event", f"<code>{esc(event)}</code>"),
            ("Member", member_detail),
            ("App", app_detail),
            ("Time", esc(ui.full_text(r["at"]))),
            ("Detail", f"<code>{esc(raw)}</code>" if raw else NO_DETAIL),
        ]
    ) + (f"<div class='ui-actions'>{''.join(actions)}</div>" if actions else "")
    return (
        f"<div class='tr ev{' open' if opened else ''}' id='ev-{rid}'>"
        f"<span class='c-ev'>{event_html(event, cid, d, admin_page=True)}</span>"
        f"<span class='c-who'>{who}</span>"
        f"<span class='c-det'>{admin_detail_html(r, names)}</span>"
        f"<span class='c-app'>{ui.app_chip(app, client_id=cid)}</span>"
        f"<span class='c-when'>{ui.when(r['at'], now=now)}</span>"
        # the row's menu (static/admin.js: right-click, press and hold, the ⋮); without scripts the
        # ⋮ opens the row's details, as the chevron does
        f"<span class='c-btns'><a class='kebab' href='{esc(toggle)}' aria-label='Actions for this event' "
        f"aria-haspopup='menu' title='Menu'>{icon('more')}</a>"
        f"<a class='c-more' href='{esc(toggle)}' role='button' "
        f"aria-expanded='{'true' if opened else 'false'}' "
        f"aria-controls='more-{rid}' aria-label='Details' title='Details'>{icon('chev')}</a></span>"
        f"<div class='more-body' id='more-{rid}'{'' if opened else ' hidden'}>{body}</div></div>"
    )


# -- the member's My activity -----------------------------------------------------------------


def member_body(state: Any, sub: str, params: Mapping[str, str], *, now: float | None = None) -> str:
    """/activity: the member's whole kept log as a dashboard; no ids in member text."""
    now = time.time() if now is None else now
    path = "/activity"
    db = state.db
    query = Query.read(params, KINDS_MEMBER)
    query.member = ""
    scope = Scope(sub=sub)
    log = Log(db, scope, admin_label="Administrator")
    scope.names = db.client_names(c for c in log.clients() if c not in (BROWSER_CLIENT, ADMIN_CLIENT))
    names = Names(db, getattr(state, "decks", None))

    week = log.since(7 * 86400, now)
    applied = [r for r in week if r["event"] == "proposal_applied"]
    last_applied = ""
    if applied:
        latest = max(applied, key=lambda r: r["at"])
        deck_id = _deck_of(latest, _detail(latest), names)
        deck = names.deck(sub, deck_id)[0] if deck_id not in (None, "") else None
        when_html = ui.when(latest["at"], now=now)
        last_applied = f"Last: {esc(deck)}, {when_html}" if deck else f"Last: {when_html}"
    apps_now = db.user_clients(sub)
    app_line = _join(
        f"{esc(a['name'] or 'An assistant')} {ui.when(a['last_issued_at'], now=now)}" for a in apps_now[:3]
    )
    cards = [
        _problem_widget(week, query.href(path, kind="problems", time="7d", app="", event="", q="")),
        ui.widget(
            "Last 7 days",
            "activity",
            len(week),
            unit="events",
            detail=_join(f"{n} {c}" for n, c in _counts(week, log).items()),
            actions=[ui.button_link("Show them", query.href(path, time="7d", kind="all"))],
        ),
        ui.widget(
            "Changes applied",
            "check",
            len(applied),
            unit="in 7 days",
            detail_html=last_applied,
            actions=[ui.button_link("Proposals", "/proposals")],
        ),
        ui.widget(
            "Your assistants",
            "link",
            len(apps_now),
            unit="connected",
            detail_html=app_line,
            actions=[ui.button_link("Connected apps", "/account/details#apps")],
        ),
    ]
    filters = [
        ui.filter_select("app", "App", log.app_options(), query.app),
        ui.filter_select("event", "Event", _event_options(log, admin_page=False), query.event),
        ui.filter_select("time", "When", TIMES, query.time),
    ]
    top_html, rows, total = _list_parts(
        path=path,
        query=query,
        log=log,
        kinds=KINDS_MEMBER,
        search=_search(log, names, admin_page=False, member_sub=sub),
        filters=filters,
        search_label="Search my activity",
        placeholder="Search event, deck or app",
        now=now,
    )
    if not rows:
        table = _empty(path, query, "No activity recorded yet.") if not total else ""
    else:
        table = (
            ui.table_open(
                "17rem minmax(0,1fr) 8rem 6.5rem",
                "minmax(0,1fr) auto",
                "1fr",
                cls="act-table",
                label="All activity",
            )
            + ui.table_header([ui.Column("Event"), ui.Column("Detail"), ui.Column("By"), ui.Column("When")])
            + "".join(
                f"<div class='tr ev'><span class='c-ev'>"
                f"{event_html(str(r['event']), r.get('client_id'), _detail(r), admin_page=False)}</span>"
                f"<span class='c-det'>{member_detail_html(r, names)}</span>"
                f"<span class='c-app'>{_member_chip(log, r.get('client_id'))}</span>"
                f"<span class='c-when'>{ui.when(r['at'], now=now)}</span></div>"
                for r in rows
            )
            + "</div>"
        )
    return (
        "<div class='act-page act-member' style='--nf:3'>"
        + ui.page_head(
            "My activity",
            "Everything done in your name on this gateway: sign-ins, which assistant proposed and applied "
            "what, links and unlinks, and anything an administrator did to your account. Kept for a year.",
        )
        + ui.widgets(cards)
        + top_html
        + table
        + _pager(path, query, total)
        + "</div>"
    )
