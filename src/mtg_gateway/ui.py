"""The shared pieces of the dashboard and list pages, as HTML strings for the list and
dashboard pages: status labels, the app a row came from, list dates, notices, widgets, chips with
counts, the search toolbar, the table's header, paging and Load more, the selection bar, the side
panel, and the box (dialog) every pop-up shares (``box``, ``box_opener``; static/box.js).

The look is theme.UI_CSS and theme.BOX_CSS (in the shared site.css); the few behaviours (the dates
in the viewer's own time zone, a filter that applies when it changes, Esc closing the side panel)
are in static/feedback.js, which every page loads. Every argument named ``*_html`` (and every
action or cell given as markup) is trusted markup the caller built; every other text is escaped
here.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import NamedTuple
from urllib.parse import urlsplit

from .theme import ASSISTANT_PAGES, SPINNER, card_size_control, esc, hub_switch, icon

__all__ = ["card_size_control"]  # re-exported: the one card size switch (R-175), theme.card_size_control

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
    "delete": Label(
        "Delete", "red", "trash", "Deletes or takes out something for good. It always waits for you."
    ),
    # Activity pages only
    "refused": Label(
        "Refused", "red", "ban", "The gateway turned down a sign-in, an app's access or an approval."
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
    # a run in progress draws the theme's one animated spinner, not a still icon
    mark = SPINNER if icon_name == "spinner" else icon(icon_name)
    return f"<span class='ui-lbl {colour}{extra}'{tip}>{mark}{esc(word)}</span>"


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

UNNAMED_APP = "Unnamed app"
NO_APP = "No app"


def app_name(name: str | None) -> str:
    """An app's full name as member text ("Claude", "ChatGPT", "Codex"), or "Unnamed app" for an
    app that gave none. Never a client id, never initials."""
    name = (name or "").strip()
    return name or UNNAMED_APP


# The apps whose own mark is shown beside the name (Ben, 22 Oct): the official files in
# static/logos/, byte for byte (docs/THIRD-PARTY-NOTICES.md). The Claude spark has one colour; the
# OpenAI blossom comes as a black and a white file, the page's theme picking one (UI_CSS).
# The mark follows where the app verifiably comes from, never the name it gives itself (N-3,
# review 2026-10-22): its metadata document's own https address (a URL client id, which the
# gateway fetched from that host and whose document names that same address), or, for a
# registered app, sign-in addresses that all send the member back to that company's hosts.
BRAND_HOSTS = {
    "claude": ("claude.ai", "claude.com", "anthropic.com"),
    "openai": ("chatgpt.com", "openai.com"),
}
CLAUDE_LOGO = "/static/logos/claude.svg"
OPENAI_LOGO_LIGHT = "/static/logos/openai-black.svg"  # black on the light theme
OPENAI_LOGO_DARK = "/static/logos/openai-white.svg"  # white on the dark theme


def _no_client_info(_client_id: str) -> Mapping[str, object] | None:
    return None


# A registered app's stored record by client id (the app sets it to its database's lookup at
# start, use_client_info); none until then, so a chip shows the neutral icon.
_client_info = _no_client_info


def use_client_info(lookup: Callable[[str], Mapping[str, object] | None]) -> None:
    """Where ``client_brand`` reads a registered app's record (its redirect addresses)."""
    global _client_info
    _client_info = lookup


def _brand_of_url(url: object) -> str | None:
    """The company whose host an https address is on (the host itself or a subdomain), or None."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").casefold()
    except ValueError:
        return None
    if parts.scheme != "https" or not host:
        return None
    for brand, hosts in BRAND_HOSTS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return brand
    return None


def client_brand(client_id: str | None, info: Mapping[str, object] | None = None) -> str | None:
    """ "claude", "openai" or None for an app, by where it verifiably comes from: a URL client id
    (a metadata document) by its own host; a registered app (``info``, else its stored record)
    when every address it signs members back to is on one company's hosts."""
    if not client_id:
        return None
    if client_id.startswith("https://"):
        return _brand_of_url(client_id)
    if info is None:
        try:
            info = _client_info(client_id)
        except Exception:  # a chip never breaks a page
            info = None
    uris = info.get("redirect_uris") if isinstance(info, Mapping) else None
    if not isinstance(uris, list) or not uris:
        return None
    brands = {_brand_of_url(u) for u in uris}
    return brands.pop() if len(brands) == 1 and None not in brands else None


_BRAND_NAMES = {"claude": "Claude’s", "openai": "OpenAI’s"}


def _host_of(url: object) -> str:
    try:
        return (urlsplit(url).hostname or "").casefold() if isinstance(url, str) else ""
    except ValueError:
        return ""


def client_brand_note(client_id: str | None) -> str:
    """Why an app's chip shows its logo or the neutral icon, for the admin's member panel: the
    hosts it sends members back to (never a path or a query), read as ``client_brand`` reads them."""
    if not client_id:
        return "No logo: no app is recorded."
    brand = client_brand(client_id)
    if client_id.startswith("https://"):
        where = _host_of(client_id) or "no host"
        return f"{_BRAND_NAMES.get(brand or '', 'No')} logo: its address is on {esc(where)}."
    try:
        info = _client_info(client_id)
    except Exception:  # a note never breaks a page
        info = None
    if not isinstance(info, Mapping):
        return "No logo: no registration is kept for it."
    uris = info.get("redirect_uris")
    hosts = (
        sorted({_host_of(u) or "an address that is not a web address" for u in uris or []})
        if isinstance(uris, list)
        else []
    )
    if not hosts:
        return "No logo: its registration names no address to send members back to."
    listed = " and ".join(hosts) if len(hosts) < 3 else ", ".join(hosts[:-1]) + " and " + hosts[-1]
    if brand:
        return f"{_BRAND_NAMES[brand]} logo: it sends members back only to {esc(listed)}."
    if any(not str(u).startswith("https://") for u in uris):
        return f"No logo: it sends members back to {esc(listed)}, and not every address is https."
    return f"No logo: it sends members back to {esc(listed)}, not only to one company’s addresses."


def app_logo(brand: str | None) -> str:
    """The logo markup for a verified app (``client_brand``): its own mark (``<img>`` with an empty
    alt, so the name beside it is read once), or the neutral "connected app" icon otherwise."""
    if brand == "claude":
        return f"<img class='logo' src='{CLAUDE_LOGO}' alt='' width='16' height='16'>"
    if brand == "openai":
        return (
            f"<img class='logo on-light' src='{OPENAI_LOGO_LIGHT}' alt='' width='16' height='16'>"
            f"<img class='logo on-dark' src='{OPENAI_LOGO_DARK}' alt='' width='16' height='16'>"
        )
    return icon("app")


def app_chip(name: str | None, *, unnamed: bool = False, client_id: str | None = None) -> str:
    """The app a row came from: its full name beside its logo (an app verified by ``client_id`` as
    Anthropic's or OpenAI's, ``client_brand``: the official marks, unmodified, only to tell the
    connected app apart) or the one neutral "connected app" icon for any other app or a row
    without its client id; never initials. A row with no app recorded shows a
    grey dash with the tooltip "No app", never a guessed app; where the row *is* an app that gave
    no name (the Users page's connected apps), ``unnamed=True`` reads "Unnamed app" instead."""
    name = (name or "").strip()
    if not name and not unnamed:
        return (
            f"<span class='ui-app none' title='{NO_APP}'><span aria-hidden='true'>—</span>"
            f"<span class='sr-only'>{NO_APP}</span></span>"
        )
    cls = "ui-app" if name else "ui-app unnamed"
    logo = app_logo(client_brand(client_id))
    return f"<span class='{cls}'>{logo}<span class='nm'>{esc(app_name(name))}</span></span>"


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


# --- notices --------------------------------------------------------------------------------------

NOTICE_KINDS = ("info", "ok", "warn", "error")


def notice(
    text: str, kind: str = "info", *, html: bool = False, role: str = "", cls: str = "", id: str = ""
) -> str:
    """One notice strip (F8): a tinted strip with a coloured left edge and no border of its own.
    ``kind``: info (blue), ok (green), warn (orange) or error (red). ``html=True`` takes ``text`` as
    markup the caller built. ``role``: "status" for a result the page announces, "alert" for an
    error that needs attention; empty for a strip that is simply part of the page. ``id`` names the
    strip for an ``aria-describedby``."""
    if kind not in NOTICE_KINDS:
        raise ValueError(f"unknown notice kind {kind!r}")
    classes = " ".join(c for c in ("notice", kind if kind != "info" else "", cls) if c)
    i = f" id='{esc(id)}'" if id else ""
    r = f" role='{esc(role)}'" if role else ""
    return f"<div class='{classes}'{i}{r}>{text if html else esc(text)}</div>"


# --- page head, widgets, chips, toolbar --------------------------------------------------------


def page_head(title: str, sub: str = "", *, actions_html: str = "") -> str:
    """The title with one line saying what the page holds (the purpose line), and the page's
    actions at the title's end (``actions_html``: buttons or button_links, the one orange button
    among them). Render with heading=False."""
    line = f"<p class='ui-sub'>{esc(sub)}</p>" if sub else ""
    acts = f"<span class='acts'>{actions_html}</span>" if actions_html else ""
    cls = "ui-head phead" if actions_html else "ui-head"
    return f"<div class='{cls}'><h1>{esc(title)}</h1>{acts}</div>{line}"


def assistant_head(current: str, title: str, sub: str = "", *, sub_html: str = "") -> str:
    """The head of the Assistant place's pages (Proposals, History, Reports): their switch in the
    middle, which names the page, so the page's heading is for screen readers only, then the
    purpose line (``sub`` as text, or ``sub_html`` already escaped). ``current``: the page's path."""
    sub_html = sub_html or esc(sub)
    line = f"<p class='ui-sub'>{sub_html}</p>" if sub_html else ""
    switch = hub_switch("Assistant", [(href, text, href) for href, text, _ic in ASSISTANT_PAGES], current)
    return f"<div class='hubbar'><h1 class='sr-only'>{esc(title)}</h1>{switch}</div>{line}"


def list_head(title: str, total: int | None = None, note: str = "") -> str:
    """The full list's heading with its total count, and an optional note beside it."""
    count = f" <span class='ui-sub'>{total:,}</span>" if total is not None else ""
    side = f"<span class='ui-sub'>{esc(note)}</span>" if note else ""
    return f"<div class='list-head'><h2>{esc(title)}{count}</h2>{side}</div>"


BUTTON_KINDS = {
    "plain": "btn-plain",
    "primary": "btn-primary",
    "danger": "btn-outline-danger",  # a red outline: the step before a box's final solid-red button
    "danger-solid": "btn-danger",  # solid red: only the final button inside a delete or remove box
}


def button_link(text: str, href: str, *, kind: str = "plain", cls: str = "", icon_name: str = "") -> str:
    """A link drawn as a button: ``plain`` (bordered, no fill), ``primary`` (the one orange main
    button) or ``danger`` (a red outline)."""
    extra = f" {esc(cls)}" if cls else ""
    ic = icon(icon_name) if icon_name else ""
    return f"<a class='btn {BUTTON_KINDS[kind]}{extra}' href='{esc(href)}'>{ic}{esc(text)}</a>"


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


def cap_link(text: str, href: str, *, icon_name: str = "", attrs: str = "") -> str:
    """A small action or way to go inside a line of text or a row (Why, Group by deck, Open as a
    full page): the theme's grey capsule, 36 px inside a 44 px tap area, never bare blue text."""
    ic = icon(icon_name) if icon_name else ""
    extra = f" {attrs}" if attrs else ""
    return f"<a class='ui-cap' href='{esc(href)}'{extra}>{ic}{esc(text)}</a>"


def open_link(href: str, text: str) -> str:
    """The row's open action: the word in a capsule from 600 px, a 44 px chevron on phones."""
    return (
        f"<a class='act ui-cap' href='{esc(href)}' aria-label='{esc(text)}'>"
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


def load_more(
    href: str, *, text: str = "Load more", shown: int | None = None, total: int | None = None, done: str = ""
) -> str:
    """ "Load more" under a list that grows in place (the standard's Load more button; Ben: a button,
    never endless scrolling). ``href`` is the next page's address (the link works without scripts;
    a page's script may fetch it and append the rows). ``shown`` of ``total`` gives the line under
    it ("25 of 112"); ``done`` is the line shown instead when there is no more ("That is every
    card.")."""
    if done:
        return f"<div class='loadmore'><p class='muted'>{esc(done)}</p></div>"
    line = ""
    if shown is not None and total is not None:
        line = f"<p class='muted'>{shown:,} of {total:,}</p>"
    return (
        f"<div class='loadmore'><a class='btn btn-plain' href='{esc(href)}' rel='next' data-load-more>"
        f"{esc(text)}</a>{line}</div>"
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


# --- the box (dialog) every pop-up shares: static/box.js, theme.BOX_CSS ------------------------


def box_open_attr(box_id: str) -> str:
    """The attribute that makes any button, link or menu item open the page's box ``box_id``
    (``data-box-open``). On a link, keep ``href`` as the old page address: without scripts the link
    still goes there."""
    return f"data-box-open='{esc(box_id)}'"


def box_opener(
    text: str, href: str, box_id: str, *, kind: str = "plain", cls: str = "", icon_name: str = ""
) -> str:
    """A button that opens the box ``box_id`` the page holds (see ``box``): a real link to ``href``,
    the old page address, so it works without scripts and in a new tab; with scripts the box opens
    on this page instead. ``kind`` as button_link (plain, primary, danger)."""
    extra = f" {esc(cls)}" if cls else ""
    ic = icon(icon_name) if icon_name else ""
    return (
        f"<a class='btn {BUTTON_KINDS[kind]}{extra}' href='{esc(href)}' {box_open_attr(box_id)}>"
        f"{ic}{esc(text)}</a>"
    )


def box_button(
    text: str,
    *,
    kind: str = "plain",
    cancel: bool = False,
    name: str = "",
    value: str = "",
    form: str = "",
    icon_name: str = "",
    disabled: bool = False,
    attrs: str = "",
) -> str:
    """A button for a box's action row. ``cancel=True`` is the Cancel (or Keep it) button: it closes
    the box and is not shown without scripts. Otherwise a submit button for the form in the box's
    body (``form``: that form's id when the button sits outside it), ``primary`` the one orange
    button, ``danger-solid`` the final solid-red button of a delete or remove box."""
    classes = {"plain": "btn-plain", "primary": "primary", "danger": "btn-outline-danger"}
    cls = classes.get(kind) or {"danger-solid": "danger"}[kind]
    ic = icon(icon_name) if icon_name else ""
    if cancel:
        return f"<button type='button' class='{cls}' data-cancel>{ic}{esc(text)}</button>"
    a = "".join(
        (
            f" name='{esc(name)}'" if name else "",
            f" value='{esc(value)}'" if value else "",
            f" form='{esc(form)}'" if form else "",
            " disabled" if disabled else "",
            f" {attrs}" if attrs else "",
        )
    )
    return f"<button type='submit' class='{cls}'{a}>{ic}{esc(text)}</button>"


def box(
    box_id: str,
    title: str,
    body_html: str,
    *,
    actions: Sequence[str] = (),
    wide: bool = False,
    open: bool = False,
    close_href: str = "",
    label: str = "",
    cls: str = "",
) -> str:
    """A box the page holds, hidden until something with ``box_open_attr(box_id)`` opens it (or the
    page's script calls ``MtgBox.openElement(box_id)``). ``body_html`` is the box's content, as a
    rule a form posting to the route the old page posted to, so what the box does needs no new
    route; ``actions`` are its buttons (box_button: Cancel first, the main one last). ``wide`` is
    the Export box's width. ``open=True`` draws it open: the old page address renders its page with
    the box open, and without scripts the box is then a plain panel at the top of the page (its
    Cancel buttons hidden); ``close_href`` is the page without the box (the × points there, and
    closing the box puts that address in the bar). Put it anywhere in the page body."""
    classes = " ".join(c for c in ("ui-box mtgbox", "wide" if wide else "", cls) if c)
    hid = "" if open else " hidden"
    opened = " data-open" if open else ""
    aria = f" aria-label='{esc(label)}'" if label else ""
    tid = f"{box_id}-title"
    x = f"<a class='bx' href='{esc(close_href or '#')}' aria-label='Close' title='Close'>{icon('x')}</a>"
    acts = f"<div class='acts'>{''.join(actions)}</div>" if actions else "<div class='acts'></div>"
    return (
        f"<div class='{classes}' id='{esc(box_id)}' role='dialog' aria-modal='true' "
        f"aria-labelledby='{esc(tid)}'{aria}{hid}{opened}>"
        f"<div class='bh'><h2 id='{esc(tid)}'>{esc(title)}</h2>{x}</div>"
        f"<div class='body'>{body_html}</div>{acts}</div>"
    )


def thumb_size_css(selector: str, width: str, height: str = "") -> str:
    """The rule that makes a page's own list thumbnails follow the card size (R-175): ``width`` and
    ``height`` are their M sizes ("40px"); S and L multiply them by the theme's --thumb-scale."""
    # the scale is set on the thumbnails, not on <html>, so the switch restyles only them
    h = f";height:calc({height} * var(--thumb-scale,1))" if height else ""
    return (
        f"html[data-card-size=s] {selector}{{--thumb-scale:.75}}"
        f"html[data-card-size=l] {selector}{{--thumb-scale:1.35}}"
        f"html[data-card-size] {selector}{{width:calc({width} * var(--thumb-scale,1)){h}}}"
    )
