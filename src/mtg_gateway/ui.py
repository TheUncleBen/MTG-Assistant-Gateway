"""The shared pieces of the dashboard and list pages, as HTML strings for the list and
dashboard pages: status labels, the app a row came from, list dates, widgets, chips with counts, the
search toolbar, the table's header, paging, the selection bar and the side panel.

The look is theme.UI_CSS (in the shared site.css); the few behaviours (the dates in the viewer's own
time zone, a filter that applies when it changes, Esc closing the side panel) are in
static/feedback.js, which every page loads. Every argument named ``*_html`` (and every action or
cell given as markup) is trusted markup the caller built; every other text is escaped here.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from .theme import esc, icon

# --- status labels: a word, a colour and an icon -------------------------------------------------


class Label(NamedTuple):
    word: str
    colour: str  # orange, green, red, blue or grey (theme.UI_CSS .ui-lbl.<colour>)
    icon: str  # a theme.ICONS name
    meaning: str  # the standard's one line (for docs and tests; pages give their own reason)


# One table for every page, the chat cards' words included. Proposal status, then risk, then the
# Activity pages' extra word ("Refused").
LABELS: dict[str, Label] = {
    "waiting": Label("Waiting", "orange", "clock", "Waiting for your press."),
    "needs_look": Label(
        "Needs a look",
        "red",
        "alert",
        "The deck changed and this change no longer does what was reviewed.",
    ),
    "held": Label("Held", "orange", "pause", "Can't be applied as it stands."),
    "updated": Label(
        "Updated",
        "blue",
        "refresh",
        "The deck changed since this was made, and the change still does exactly what you reviewed.",
    ),
    "applying": Label("Applying", "blue", "spinner", "Being sent now."),
    "applied": Label("Applied", "green", "check", "Done on Archidekt."),
    "rejected": Label("Rejected", "grey", "x", "You said no."),
    "withdrawn": Label("Withdrawn", "grey", "back", "The app took it back."),
    "replaced": Label("Replaced", "grey", "arrows", "A newer change took its place."),
    "expired": Label("Expired", "grey", "hourglass", "Not approved in time."),
    "failed": Label("Failed", "red", "alert", "Archidekt refused it, or something broke."),
    # risk
    "low": Label("Low", "green", "shield", "A small deck or collection change."),
    "high": Label("High", "orange", "triangle", "Bigger, or touches commanders or deck details."),
    "your_press": Label("Your press", "blue", "hand", "An account action. It always waits for you."),
    "delete": Label("Delete", "red", "trash", "Deletes a deck. It always waits for you."),
    # Activity pages only
    "refused": Label(
        "Refused", "red", "alert", "The gateway turned down a sign-in, an app's access or an approval."
    ),
}
# The codes the database and the board use for the same things.
ALIASES = {
    "pending": "waiting",
    "superseded": "replaced",
    "consent": "your_press",
    "destructive": "delete",
}
STATUS_KEYS = (
    "waiting",
    "needs_look",
    "held",
    "updated",
    "applying",
    "applied",
    "rejected",
    "withdrawn",
    "replaced",
    "expired",
    "failed",
)
RISK_KEYS = ("low", "high", "your_press", "delete")
COLOURS = ("orange", "green", "red", "blue", "grey", "plain")


def label(word: str, colour: str, icon_name: str, *, title: str = "", cls: str = "") -> str:
    """A status label for any status (a report's Running, a watch's Alert sent): the word, the
    colour (orange waiting or running, green done, red failed, grey closed, blue informational) and
    an icon. Use ``status_label`` / ``risk_label`` for the standard's own words."""
    if colour not in COLOURS:
        raise ValueError(f"unknown label colour {colour!r}")
    extra = f" {esc(cls)}" if cls else ""
    tip = f" title='{esc(title)}'" if title else ""
    return f"<span class='ui-lbl {colour}{extra}'{tip}>{icon(icon_name)}{esc(word)}</span>"


def label_for(key: str, *, title: str = "", cls: str = "", word: str = "") -> str:
    """The standard's label for ``key`` (a LABELS key or a database code in ALIASES). ``word``
    replaces the word only for counts in a widget ("Delete 1"); the colour and icon stay."""
    lb = LABELS[ALIASES.get(key, key)]
    return label(word or lb.word, lb.colour, lb.icon, title=title, cls=cls)


def status_label(status: str, *, title: str = "", cls: str = "") -> str:
    """A proposal's status label (Waiting, Needs a look, Held, Updated, Applying, Applied, Rejected,
    Withdrawn, Replaced, Expired, Failed). ``pending`` reads Waiting, ``superseded`` Replaced."""
    key = ALIASES.get(status, status)
    if key not in STATUS_KEYS:
        raise KeyError(status)
    return label_for(key, title=title, cls=cls)


NO_RISK = "Risk not judged"


def no_risk_label() -> str:
    """A closed proposal whose risk could not be judged: a muted dash, never High for want of
    evidence; a screen reader hears why."""
    return (
        f"<span class='ui-none' title='{NO_RISK}'><span aria-hidden='true'>—</span>"
        f"<span class='sr-only'>{NO_RISK}</span></span>"
    )


def risk_label(risk: str, *, title: str = "", cls: str = "") -> str:
    """A proposal's risk label (Low, High, Your press, Delete); the stored codes ``consent`` and
    ``destructive`` read Your press and Delete."""
    key = ALIASES.get(risk, risk)
    if key not in RISK_KEYS:
        raise KeyError(risk)
    return label_for(key, title=title, cls=cls)


# --- the app a row came from ------------------------------------------------------------------

# The letter on an app's tile where its first letter would read as another app's.
_APP_LETTERS = {"chatgpt": "G"}


def app_chip(name: str | None) -> str:
    """The app a row came from: its name only (never a client id), on a letter tile. A row with no
    app recorded shows a grey dash with the tooltip "No app"; never a guessed app."""
    name = (name or "").strip()
    if not name:
        return (
            "<span class='ui-app none' title='No app'><span aria-hidden='true'>—</span>"
            "<span class='sr-only'>No app</span></span>"
        )
    letter = _APP_LETTERS.get(name.casefold()) or next((c for c in name if c.isalnum()), "?").upper()
    return f"<span class='ui-app'><i aria-hidden='true'>{esc(letter)}</i>{esc(name)}</span>"


# --- list dates ---------------------------------------------------------------------------------

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
RELATIVE_DAYS = 7  # relative up to and including 7 days, then the date


def _gm(ts: int | float | None) -> time.struct_time | None:
    if not ts:
        return None
    try:
        return time.gmtime(int(ts))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def when_text(ts: int | float | None, *, now: float | None = None) -> str:
    """The one list date format, as plain text in UTC (the page rewrites it in the viewer's time zone):
    "just now", "40 min ago", "2 h ago", "1 day ago" to "7 days ago", then "Oct 12", with the year
    only when it isn't this year ("Oct 12, 2025"). static/feedback.js (MtgText.rel) is its twin."""
    t = _gm(ts)
    if t is None:
        return ""
    now = time.time() if now is None else now
    age = int(now) - int(ts)  # type: ignore[arg-type]
    if age < 60:
        return "just now"
    if age < 3600:
        return f"{age // 60} min ago"
    if age < 86400:
        return f"{age // 3600} h ago"
    if age <= RELATIVE_DAYS * 86400:
        days = age // 86400
        return f"{days} day{'' if days == 1 else 's'} ago"
    short = f"{_MONTHS[t.tm_mon - 1]} {t.tm_mday}"
    return short if t.tm_year == time.gmtime(int(now)).tm_year else f"{short}, {t.tm_year}"


def full_text(ts: int | float | None) -> str:
    """The full date and time in UTC ("Oct 12, 2026, 14:10 UTC"): the tooltip before the page's
    script rewrites it in the viewer's own time zone."""
    t = _gm(ts)
    if t is None:
        return ""
    return f"{_MONTHS[t.tm_mon - 1]} {t.tm_mday}, {t.tm_year}, {t.tm_hour:02d}:{t.tm_min:02d} UTC"


def when(ts: int | float | None, *, now: float | None = None, cls: str = "") -> str:
    """A list date for a list or widget: ``<time data-fmt=rel>`` with the relative text (or the date
    after 7 days) and the full date and time as its tooltip. Without scripts both read in UTC;
    static/feedback.js rewrites them in the viewer's time zone and keeps the relative text current."""
    t = _gm(ts)
    if t is None:
        return ""
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", t)
    extra = f" {esc(cls)}" if cls else ""
    return (
        f"<time class='ui-when{extra}' datetime='{iso}' data-fmt='rel' title='{full_text(ts)}'>"
        f"{when_text(ts, now=now)}</time>"
    )


# --- page head, widgets, chips, toolbar --------------------------------------------------------


def page_head(title: str, sub: str = "") -> str:
    """The title with one line saying what the page holds. Render with heading=False."""
    line = f"<p class='ui-sub'>{esc(sub)}</p>" if sub else ""
    return f"<div class='ui-head'><h1>{esc(title)}</h1></div>{line}"


def list_head(title: str, total: int | None = None, note: str = "") -> str:
    """The full list's heading with its total count, and an optional note beside it."""
    count = f" <span class='ui-sub'>{total:,}</span>" if total is not None else ""
    side = f"<span class='ui-sub'>{esc(note)}</span>" if note else ""
    return f"<div class='list-head'><h2>{esc(title)}{count}</h2>{side}</div>"


def button_link(text: str, href: str, *, kind: str = "plain", cls: str = "") -> str:
    """A link drawn as a button: ``plain`` (bordered, no fill), ``primary`` (the one orange main
    button) or ``danger`` (a red outline)."""
    kinds = {"plain": "btn-plain", "primary": "btn-primary", "danger": "btn-outline-danger"}
    extra = f" {esc(cls)}" if cls else ""
    return f"<a class='btn {kinds[kind]}{extra}' href='{esc(href)}'>{esc(text)}</a>"


def widget(
    title: str,
    icon_name: str,
    big: int | str,
    *,
    unit: str = "",
    detail: str = "",
    detail_html: str = "",
    actions: Sequence[str] = (),
    tone: str = "",
    wide_sm: bool = False,
    big_html: str = "",
) -> str:
    """One dashboard widget: a label with an icon, one big number (``unit`` small beside it), one detail
    line and at most two quick starts (``actions``: button or link markup, see button_link).
    ``tone``: ``accent`` (orange top edge, something waits) or ``alert`` (red, a problem).
    ``wide_sm`` gives it a whole row where widgets go two a row (phones, the open Fold)."""
    if len(actions) > 2:
        raise ValueError("a widget has at most two quick starts")
    if tone not in ("", "accent", "alert"):
        raise ValueError(f"unknown widget tone {tone!r}")
    cls = " ".join(c for c in ("wdg", tone, "wide-sm" if wide_sm else "") if c)
    number = big_html or (
        f"{esc(f'{big:,}' if isinstance(big, int) else big)}"
        + (f"<small>{esc(unit)}</small>" if unit else "")
    )
    det = detail_html or esc(detail)
    go = f"<span class='go'>{''.join(actions)}</span>" if actions else ""
    return (
        f"<div class='{cls}'><span class='lab'>{icon(icon_name)}{esc(title)}</span>"
        f"<span class='big'>{number}</span>"
        + (f"<span class='det'>{det}</span>" if det else "")
        + f"{go}</div>"
    )


def widgets(items: Iterable[str], *, label: str = "At a glance") -> str:
    """The row of 3 to 6 widgets (two a row on the open Fold and on phones)."""
    return f"<section class='ui-widgets' aria-label='{esc(label)}'>{''.join(items)}</section>"


class Chip(NamedTuple):
    text: str
    count: int | None
    href: str
    current: bool = False


def chips(items: Iterable[Chip], *, label: str = "Show") -> str:
    """Quick-filter chips with counts ("Waiting 8", "All 51"); the current one is marked."""
    out = []
    for c in items:
        n = f" <span class='n'>{c.count:,}</span>" if c.count is not None else ""
        cur = " aria-current='true'" if c.current else ""
        out.append(f"<a class='ui-chip' href='{esc(c.href)}'{cur}>{esc(c.text)}{n}</a>")
    return f"<nav class='ui-chips' aria-label='{esc(label)}'>{''.join(out)}</nav>"


def filter_select(name: str, label: str, options: Sequence[tuple[str, str]], selected: str = "") -> str:
    """One filter drop-down for the toolbar: ``options`` are (value, text); the first is "any"."""
    opts = "".join(
        f"<option value='{esc(v)}'{' selected' if v == selected else ''}>{esc(t)}</option>"
        for v, t in options
    )
    return f"<span class='f'><select name='{esc(name)}' aria-label='{esc(label)}'>{opts}</select></span>"


def toolbar(
    action: str,
    *,
    search_label: str,
    placeholder: str = "",
    q: str = "",
    name: str = "q",
    filters: Sequence[str] = (),
    keep: Mapping[str, str] | None = None,
) -> str:
    """The list's search box and filters, as a GET form to ``action``. ``filters`` are
    filter_select markup; ``keep`` are other query values to carry (the current chip). A filter
    applies as soon as it changes (static/feedback.js); without scripts a Search button shows."""
    hidden = "".join(
        f"<input type='hidden' name='{esc(k)}' value='{esc(v)}'>" for k, v in (keep or {}).items() if v
    )
    return (
        f"<form class='ui-tools' role='search' method='get' action='{esc(action)}'>{hidden}"
        f"<label class='search'><span class='sr-only'>{esc(search_label)}</span>{icon('search')}"
        f"<input type='search' name='{esc(name)}' value='{esc(q)}' placeholder='{esc(placeholder)}'></label>"
        f"{''.join(filters)}"
        "<noscript><button type='submit' class='btn-plain'>Search</button></noscript></form>"
    )


# --- the table ---------------------------------------------------------------------------------


class Column(NamedTuple):
    text: str  # the header's words ("" for the tick box or action column)
    cls: str = ""  # md-hide (folds at 600-899 px), sm-hide (gone below 600 px)
    html: str = ""  # header markup instead of text (a check-all box, a sort button)


def table_open(cols: str, cols_md: str, cols_sm: str, *, cls: str = "", label: str = "") -> str:
    """The table's opening tag with its three grid layouts: ``cols`` from 900 px, ``cols_md`` at
    600-899 px (and beside an open side panel), ``cols_sm`` below 600 px. Close it with ``</div>``.
    Cells marked md-hide fold into the row's .sub line; sm-hide ones leave below 600 px."""
    extra = f" {esc(cls)}" if cls else ""
    aria = f" role='region' aria-label='{esc(label)}'" if label else ""
    style = f"--cols:{cols};--cols-md:{cols_md};--cols-sm:{cols_sm}"
    return f"<div class='ui-table{extra}'{aria} style='{esc(style)}'>"


def table_header(columns: Sequence[Column]) -> str:
    """The header row (hidden below 600 px), one cell per column, in the list's column order."""
    cells = []
    for c in columns:
        attr = f" class='{esc(c.cls)}'" if c.cls else ""
        cells.append(f"<span{attr}>{c.html or esc(c.text)}</span>")
    return f"<div class='tr th'>{''.join(cells)}</div>"


def check_cell(name: str, value: str, label: str, *, checked: bool = False, form: str = "") -> str:
    """A row's tick box: the whole 44 px cell is the target; ``label`` is read out for it."""
    f = f" form='{esc(form)}'" if form else ""
    on = " checked" if checked else ""
    return (
        f"<label class='chk'><input type='checkbox' name='{esc(name)}' value='{esc(value)}'"
        f" aria-label='{esc(label)}'{f}{on}></label>"
    )


def group_row(title_html: str, meta_html: str = "", *, check_html: str = "") -> str:
    """A group's row in the table (a deck's waiting changes): a tick box cell, the title, a meta line."""
    chk = check_html or "<span></span>"
    meta = f"<span class='meta'>{meta_html}</span>" if meta_html else ""
    return f"<div class='tr group'>{chk}<span>{title_html}</span>{meta}</div>"


def day_row(text: str) -> str:
    """A quiet day heading between rows ("Today", "Oct 12")."""
    return f"<div class='tr daygroup'>{esc(text)}</div>"


def open_link(href: str, text: str) -> str:
    """The row's open action: the word from 600 px, a 44 px chevron on phones."""
    return (
        f"<a class='act linkbtn' href='{esc(href)}' aria-label='{esc(text)}'>"
        f"<span class='sm-hide'>{esc(text)}</span><span class='sm-only chev' aria-hidden='true'>›</span></a>"
    )


# --- paging, the selection bar, the side panel ------------------------------------------------

PER_PAGE = 25
NEWER = "\u2190 Newer"
OLDER = "Older \u2192"


def pager(
    offset: int,
    total: int,
    *,
    newer_href: str | None,
    older_href: str | None,
    per_page: int = PER_PAGE,
    note: str = "",
) -> str:
    """Paging under a list: "1–25 of 51", "← Newer" and "Older →", the end that has nothing more
    disabled. ``note`` says anything that caps the list ("past proposals are kept 30 days"), so
    nothing is capped silently. Empty when the list is."""
    if total <= 0:
        return ""
    start = max(0, offset) + 1
    end = min(total, max(0, offset) + per_page)
    info = f"{start:,}–{end:,} of {total:,}" + (f" · {esc(note)}" if note else "")

    def btn(text: str, href: str | None, rel: str) -> str:
        if href is None:
            return f"<button class='btn-plain' type='button' disabled>{text}</button>"
        return f"<a class='btn btn-plain' href='{esc(href)}' rel='{rel}'>{text}</a>"

    return (
        f"<nav class='ui-pager' aria-label='Pages'><span class='info'>{info}</span><span class='btns'>"
        f"{btn(NEWER, newer_href, 'prev')}{btn(OLDER, older_href, 'next')}</span></nav>"
    )


def selection_bar(count_text: str, actions: Sequence[str], *, label: str, hidden: bool = False) -> str:
    """The sticky action bar while a list has a selection: the count ("2 checked on 1 deck"), then
    the actions (plain, then the red-outline one, then the one orange main button)."""
    h = " hidden" if hidden else ""
    return (
        f"<div class='ui-bar' role='group' aria-label='{esc(label)}'{h}>"
        f"<span class='count' aria-live='polite'>{esc(count_text)}</span>{''.join(actions)}</div>"
    )


def side_panel(
    heading_html: str,
    body_html: str,
    *,
    label: str,
    close_href: str,
    back_text: str,
) -> str:
    """One item opened from a list: from 1100 px a panel beside the list with a × that
    closes it (Esc too); narrower, the same markup is the item's own page with a back link. The
    × and the back link both go to ``close_href`` (the list); put the result in ``split``."""
    return (
        f"<aside class='ui-drawer' aria-label='{esc(label)}'>"
        f"<p class='page-only'><a href='{esc(close_href)}'>← {esc(back_text)}</a></p>"
        f"<div class='dh'><h2>{heading_html}</h2><a class='close btn panel-only' href='{esc(close_href)}'"
        f" aria-label='Close' title='Close'>{icon('x')}</a></div>{body_html}</aside>"
    )


def split(list_html: str, panel_html: str = "") -> str:
    """The list, with the side panel beside it when one is open (``panel_html`` from side_panel).
    Use with render(main_class='ui-wide')."""
    if not panel_html:
        return f"<div class='ui-split'>{list_html}</div>"
    return f"<div class='ui-split with-panel'><div class='list-only'>{list_html}</div>{panel_html}</div>"


def meta(rows: Iterable[tuple[str, str]]) -> str:
    """Key and value rows in a panel (``.ui-meta``); the values are markup."""
    return "<dl class='ui-meta'>" + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in rows) + "</dl>"
