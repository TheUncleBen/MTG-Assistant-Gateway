"""The Watchlist page (``/watchlist``): Binder › Watchlist, the member's want list (0.7.46, mockup
screen 8).

Under Binder's switch: a top row with the search, "+ Watch a card" (a box), Select, the sort and
the finish filter; chips with counts (All, With a price alert, Up and Down in 7 days, Decks); one
status line (the last check, the biggest 7-day move, the price alerts past, where prices come
from); then the watched cards grouped by watchlist list (the member's own groups, kept on this
gateway; the cards in no list after them) and the watched decks as a group of their own. A row:
its tick box, picture, name (opens the card viewer), printing, price and 7-day change, its price
alert as a blue label and "In binder · you own N" when the member's Binder holds the card. Each
row has Details (its 30-day chart, "Full history (N days)", the price history table, the alert
form, Open card and a red-outline Remove) and a ⋮ (static/menus.js: Open card, Details, Price
alert…, Move to list…, Stop watching). A tick on any row shows the bulk bar: Price alert…, Move to
list…, Stop watching (a pop-up asks first) and Cancel; Select ticks every row on screen. A group's
head carries its count and value and, for a list, Rename list… and Delete list….

The rows come from ``PriceService.card_summaries`` and ``deck_summaries``: each watch's latest
price and its changes, in one query each whatever the list's size (up to 5,000 cards and 100
decks), kept until something changes; the chips, search, filter and sort work on those, with names
in their natural order (a 2 before a 10). Only the rows on screen get their 30 days of prices
(``card_histories``, ``deck_histories``). Load more shows 25 more cards, or 25 more decks:
static/watchlist.js asks for just those rows (``watchlist_fragment_html``) and adds them in place;
without a script its link is the longer page. Every box (Watch a card, Watch a deck, Price alert
defaults, New list, Rename and Delete list, the bulk bar's) is a form the page holds, drawn open by
an address with ``box=`` for a browser without scripts."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote, urlencode

from . import ui
from .deckpage import image_url
from .history_view import _trend_svg
from .prices import ALERT_KINDS, MAX_LIST_NAME, NO_LIST, money
from .reports_page import PHONE_WIDGETS_CSS
from .theme import esc, icon, natural_key, plural

PAGE = ui.PER_PAGE
FINISH_WORDS = {"nonfoil": "Normal", "foil": "Foil", "etched": "Etched"}
CHIPS = (
    ("", "All"),
    ("alert", "With a price alert"),
    ("up", "Up in 7 days"),
    ("down", "Down in 7 days"),
    ("decks", "Decks"),
)
SORTS = (
    ("", "Sort: Name"),
    ("price", "Price"),
    ("d1", "Change, 1 day"),
    ("d7", "Change, 7 days"),
    ("d30", "Change, 30 days"),
)
CHANGE_KEYS = (("1 day", "change_1d_pct"), ("7 days", "change_7d_pct"), ("30 days", "change_30d_pct"))
SHORT_DAYS = 30
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NO_LIST_WORD = "Not in a list"  # the group of the cards in no list (the mockup's "Ungrouped")
DECKS_WORD = "Decks"
# the boxes an address can draw open (``box=``), for a browser without scripts
BOXES = ("add", "deck", "defaults", "newlist", "rename", "delete", "alerts", "move", "stop")
OK_NOTES = {
    "card_added": "Added to your watchlist.",
    "card_updated": "Price alert saved.",
    "card_removed": "Removed from your watchlist.",
    "deck_added": "Deck added to your watchlist.",
    "deck_removed": "Deck removed from your watchlist.",
    "defaults_saved": "Price alert defaults saved. They apply to every card you watch from now on.",
    "list_created": "List created.",
    "list_renamed": "List renamed.",
    "list_deleted": "List deleted. Its cards stay on your watchlist.",
    "moved": "Moved to the list.",
    "alerts_set": "Price alert saved on the selected cards.",
    "removed": "The selected cards are no longer watched.",
    "rewatched": "The cards are watched again.",
}
CARD_HINT = (
    "A name alone follows the cheapest printing of the card, checked daily; add a set code and number "
    "to follow one printing."
)
SEARCH_HINT = "You can also watch a card from Card search: press and hold it, or right-click it."
ALERT_HINT = (
    "Checked once a day against the day before. Leave a box empty for no price alert. Price alerts "
    "reach you through push notifications (Account page)."
)
DEFAULTS_HINT = (
    "Applied to every card you watch from now on. Change one card's price alert from its row, or "
    "several at once with Select."
)
DECK_HINT = (
    "A watched deck is read again once a day, so its value follows its cards. Its value uses "
    "Scryfall's prices, so it can differ from the deck page's estimated cost, which uses Archidekt's."
)
# the five alert fields: (key, form name, label)
ALERT_FIELDS = (
    ("drop", "alert_drop", "Drops below the price when added by (%)"),
    ("rise", "alert_rise", "Rises above the price when added by (%)"),
    ("pct", "alert_pct", "Moves in a day by more than (%)"),
    ("above", "alert_above", "Rises above"),
    ("below", "alert_below", "Falls below"),
)


@dataclass(frozen=True)
class Query:
    """What the address asks for: the chip, the search, the finish, the sort, one list, how many
    rows are shown (``upto``, which Load more raises), the open row and the box drawn open."""

    show: str = ""
    q: str = ""
    finish: str = ""
    sort: str = ""
    upto: int = 0  # rows shown; 0 is the first screenful (PAGE)
    open: str = ""
    dupto: int = 0  # decks shown, as ``upto``
    list: str = ""  # one list's cards only ("-": the cards in no list)
    box: str = ""

    @classmethod
    def parse(cls, qp: Any) -> Query:
        def get(k: str, n: int) -> str:
            return str(qp.get(k) or "").strip()[:n]

        def number(k: str) -> int:
            try:
                return max(0, min(int(qp.get(k) or 0), 10_000))
            except ValueError:
                return 0

        # an old page link (``offset``) still shows the rows it pointed at
        upto = number("upto") or (number("offset") + PAGE if number("offset") else 0)
        show, finish, sort, box = get("show", 10), get("finish", 10), get("sort", 10), get("box", 10)
        return cls(
            show=show if show in dict(CHIPS) else "",
            q=get("q", 100),
            finish=finish if finish in FINISH_WORDS else "",
            sort=sort if sort in dict(SORTS) else "",
            upto=upto,
            open=get("open", 64),
            dupto=number("dupto"),
            list=get("list", MAX_LIST_NAME),
            box=box if box in BOXES else "",
        )

    def href(self, **change: Any) -> str:
        values = {
            "show": self.show,
            "q": self.q,
            "finish": self.finish,
            "sort": self.sort,
            "list": self.list,
            "upto": self.upto,
            "dupto": self.dupto,
            **change,
        }
        params = {k: v for k, v in values.items() if v}
        return "/watchlist" + (f"?{urlencode(params)}" if params else "")


# -- words ------------------------------------------------------------------------------------
def day_text(day: str | None, today: str) -> str:
    """A price's day as list dates write it: "today", else "Oct 12" (with the year when not this one)."""
    if not day:
        return ""
    if day == today:
        return "today"
    try:
        y, m, d = (int(x) for x in day.split("-"))
    except ValueError:
        return day
    return f"{_MONTHS[m - 1]} {d}" + ("" if str(y) == today[:4] else f", {y}")


def change_html(v: float | None) -> str:
    """A change as an arrow and a word (colour never alone): green up, red down."""
    if v is None:
        return "<span class='dl'>–</span>"
    if not v:
        return "<span class='dl'>no change</span>"
    up = v > 0
    return (
        f"<span class='dl {'up' if up else 'down'}'><span aria-hidden='true'>{'↑' if up else '↓'}</span>"
        f"{'up' if up else 'down'} {abs(v):g}%</span>"
    )


def changes_html(view: dict[str, Any]) -> str:
    return "".join(f"<span>{label} {change_html(view.get(key))}</span>" for label, key in CHANGE_KEYS)


def alert_text(alerts: dict[str, Any], currency: str, added: float | None = None) -> str:
    """A card's alert in its short form: "Moves more than 10%", "Below $150.00", "Above $45.00 ·
    below $30.00", "Drops 10% below $2.00" (``added``: the price when added; without one, "below
    the price when added")."""
    parts = []
    at = money(added, currency) if added is not None else "the price when added"
    if alerts.get("drop") is not None:
        parts.append(f"drops {alerts['drop']:g}% below {at}")
    if alerts.get("rise") is not None:
        parts.append(f"rises {alerts['rise']:g}% above {at}")
    if alerts.get("pct") is not None:
        parts.append(f"moves more than {alerts['pct']:g}%")
    if alerts.get("above") is not None:
        parts.append(f"above {money(alerts['above'], currency)}")
    if alerts.get("below") is not None:
        parts.append(f"below {money(alerts['below'], currency)}")
    text = " · ".join(parts)
    return text[:1].upper() + text[1:]


def alert_label(v: dict[str, Any]) -> str:
    alerts = v.get("alerts") or {}
    if not alerts:
        return "<span class='none'>No price alert</span>"
    return ui.label(alert_text(alerts, v.get("currency") or "usd", v.get("added_price")), "blue", "tag")


def owned_label(v: dict[str, Any]) -> str:
    """ "In binder · you own N" when the member's Binder holds the card (any printing)."""
    n = int(v.get("owned") or 0)
    if n <= 0:
        return ""
    return ui.label(f"In binder · you own {n}", "green", "collection", cls="owned")


def where_text(v: dict[str, Any]) -> str:
    """The printing a row follows: "CMR 472 · Foil", or for a watch by name alone "Cheapest
    printing (now PLST CMR-365) · Normal"."""
    where = " ".join(p for p in ((v.get("set") or "").upper(), v.get("collector_number") or "") if p)
    if not where:
        low = v.get("cheapest") or {}
        now_at = " ".join(p for p in ((low.get("set") or "").upper(), low.get("collector_number") or "") if p)
        where = f"Cheapest printing (now {now_at})" if now_at else "Cheapest printing"
    return f"{where} · {FINISH_WORDS.get(v.get('finish') or '', v.get('finish') or '')}"


def _priced(hist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [h for h in hist if h.get("price") is not None]


def list_word(name: str) -> str:
    return name if name else NO_LIST_WORD


# -- the chart -------------------------------------------------------------------------------
def chart_html(hist: list[dict[str, Any]], currency: str, label: str, today: str) -> str:
    """The opened row's price chart: a line with its top, middle and bottom prices beside it and up
    to five days under it, all as page text (static/watchlist.js draws the same for the full
    history)."""
    pts = _priced(hist)
    if len(pts) < 2:
        return "<p class='ui-hint'>The chart shows once there are two days of prices.</p>"
    values = [float(h["price"]) for h in pts]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    w, h = 600, 160
    step = w / (len(values) - 1)
    coords = " ".join(
        f"{i * step:.1f},{(h / 2 if hi == lo else h - 6 - (v - lo) / span * (h - 12)):.1f}"
        for i, v in enumerate(values)
    )
    said = (
        f"{label}: {money(values[0], currency)} on {pts[0]['day']}, "
        f"{money(values[-1], currency)} on {pts[-1]['day']}."
    )
    ys = [hi, (hi + lo) / 2, lo] if hi != lo else [hi]
    n = len(pts)
    picks = sorted({round(i * (n - 1) / 4) for i in range(5)}) if n >= 5 else list(range(n))
    xs = "".join(f"<span>{esc(day_text(pts[i]['day'], today))}</span>" for i in picks)
    return (
        "<div class='pc'><div class='pc-y' aria-hidden='true'>"
        + "".join(f"<span>{esc(money(y, currency))}</span>" for y in ys)
        + f"</div><svg class='pcl' viewBox='0 0 {w} {h}' preserveAspectRatio='none' role='img' "
        f"aria-label='{esc(said)}'><polyline points='{coords}' vector-effect='non-scaling-stroke'/></svg>"
        f"<div class='pc-x' aria-hidden='true'>{xs}</div></div>"
    )


def table_html(hist: list[dict[str, Any]], currency: str, what: str) -> str:
    pts = _priced(hist)
    if not pts:
        return ""
    rows = "".join(
        f"<tr><th scope='row'>{esc(h['day'])}</th><td>{esc(money(h['price'], currency))}</td></tr>"
        for h in reversed(pts)
    )
    return (
        "<details class='rv-det wx-tbl'><summary class='fold-btn'>Price history table</summary>"
        f"<div class='tbl' role='region' aria-label='{esc(what)}, newest first' tabindex='0'>"
        "<table><thead><tr><th scope='col'>Day</th><th scope='col'>Price</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></details>"
    )


def spark_html(hist: list[dict[str, Any]], currency: str, what: str) -> str:
    pts = _priced(hist)
    if len(pts) < 2:
        return "<span class='none'>–</span>"
    said = (
        f"{what}: {money(pts[0]['price'], currency)} on {pts[0]['day']}, "
        f"{money(pts[-1]['price'], currency)} on {pts[-1]['day']}."
    )
    return _trend_svg([h["price"] for h in pts], said)


def _range_buttons(full_days: int) -> str:
    """ "30 days" and "Full history (N days)"; only "30 days" while there is
    no more."""
    full = (
        "<button type='button' data-range='full' aria-pressed='false'>"
        f"Full history ({full_days} days)</button>"
        if full_days > SHORT_DAYS
        else ""
    )
    return (
        "<span class='seg' role='group' aria-label='Range'>"
        f"<button type='button' data-range='30' aria-pressed='true'>30 days</button>{full}</span>"
    )


# -- rows ----------------------------------------------------------------------------------------
# the first column is the row's tick box (the bulk bar); the last holds Details and the row's ⋮
# (static/menus.js adds it) side by side
TABLE_COLS = "32px 56px minmax(0,1fr) 7rem 10.5rem 120px minmax(0,12rem) 8.5rem"
TABLE_MD = "32px 56px minmax(0,1fr) 8.5rem"
TABLE_SM = "32px 48px minmax(0,1fr) auto"
BULK_FORM = "w-bulk"


def _thumb(name: str, uid: str | None, *, deck: bool = False) -> str:
    src = image_url(uid, "small") if uid else None
    if src:
        return (
            f"<span class='thumb' aria-hidden='true'><img src='{esc(src)}' alt='' loading='lazy' "
            "decoding='async' width='48' height='67'></span>"
        )
    return (
        f"<span class='thumb{' deckart' if deck else ''}' aria-hidden='true'><span>{esc(name)}</span></span>"
    )


def _row(
    *,
    key: str,
    check: str,
    thumb: str,
    title: str,
    line: str,
    view: dict[str, Any],
    today: str,
    what: str,
    alert: str,
    is_open: bool,
    details: str,
    more_label: str,
    query: Query,
    attrs: str = "",
) -> str:
    cur = view.get("currency") or "usd"
    price = view.get("price")
    px = money(price, cur) if price is not None else "–"
    when = day_text(view.get("day"), today) if price is not None else "No price yet"
    seven = view.get("change_7d_pct")
    row_hist = view.get("history") or []
    short = row_hist[-SHORT_DAYS:]
    sub = (
        f"<b class='px'>{esc(px)}</b>"
        + (f"{change_html(seven)}<span class='kv'>in 7 days</span>" if seven is not None else "")
        + alert
    )
    # without the script, Details is a link to the page with this row open: it keeps the chip, the
    # search, the filters and the page (query.href), as History's Details does
    href = f"{query.href(open=key)}#wr-{esc(key)}"
    return (
        f"<div class='tr{' open' if is_open else ''}' id='wr-{esc(key)}'{attrs}>{check}{thumb}"
        f"<span class='title'><span class='t'>{title}</span><span class='deck'>{esc(line)}</span>"
        f"<span class='sub'>{sub}</span></span>"
        f"<span class='px md-hide'>{esc(px)}<small>{esc(when)}</small></span>"
        f"<span class='md-hide chgs'>{changes_html(view)}</span>"
        f"<span class='md-hide spark'>{spark_html(short, cur, what)}</span>"
        f"<span class='md-hide lbls'>{alert}</span>"
        f"<span class='act'><a class='btn btn-plain more' href='{href}' role='button' "
        f"aria-expanded='{'true' if is_open else 'false'}' aria-controls='wx-{esc(key)}' "
        f"aria-label='{esc(more_label)}'>{'Close' if is_open else 'Details'}</a></span></div>"
        f"<div class='wx' id='wx-{esc(key)}'{'' if is_open else ' hidden'}>{details}</div>"
    )


def _wx_chart(view: dict[str, Any], today: str, what: str, heading: str, *, extra: str = "") -> str:
    cur = view.get("currency") or "usd"
    hist = view.get("history") or []
    full_days = int(view.get("full_days") or len(_priced(hist)))
    short = hist[-SHORT_DAYS:]
    return (
        f"<p class='wx-chg'>{changes_html(view)}</p>"
        f"<div class='wx-chart'{extra}><div class='wx-h'><h3>{esc(heading)}</h3>"
        f"{_range_buttons(full_days)}</div>"
        f"<div class='wx-draw'>{chart_html(short, cur, what, today)}</div>"
        f"<div class='wx-table'>{table_html(short, cur, what)}</div></div>"
    )


def alert_fields_html(values: dict[str, Any], *, suffix: str, form: str = "") -> str:
    """The five alert boxes, each labelled; ``suffix`` makes the ids unique on the page;
    ``form`` ties them to a form they sit outside of (the bulk bar's box)."""
    f = f" form='{esc(form)}'" if form else ""

    def num(v: Any) -> str:  # 10.0 reads "10"
        if v is None or v == "":
            return ""
        return f"{v:g}" if isinstance(v, (int, float)) else str(v)

    return "".join(
        f"<label for='{key}-{esc(suffix)}'>{label}<input id='{key}-{esc(suffix)}' name='{name}' "
        f"inputmode='decimal' maxlength='10' value='{esc(num(values.get(key)))}'{f}></label>"
        for key, name, label in ALERT_FIELDS
    )


def list_select_html(lists: list[str], current: str, *, id_: str, form: str = "", new: bool = True) -> str:
    """The list drop-down (no list, each list) and, with ``new``, a box for a new list's name."""
    f = f" form='{esc(form)}'" if form else ""
    options = f"<option value=''{' selected' if not current else ''}>{NO_LIST_WORD}</option>" + "".join(
        f"<option value='{esc(name)}'{' selected' if name == current else ''}>{esc(name)}</option>"
        for name in lists
    )
    out = f"<label for='{esc(id_)}'>List<select id='{esc(id_)}' name='list'{f}>{options}</select></label>"
    if new:
        out += (
            f"<label for='{esc(id_)}-new'>Or a new list<input id='{esc(id_)}-new' name='new_list' "
            f"maxlength='{MAX_LIST_NAME}' autocomplete='off' placeholder='Name'{f}></label>"
        )
    return out


def card_row(
    v: dict[str, Any],
    uid: str | None,
    csrf_in: str,
    today: str,
    *,
    query: Query,
    lists: list[str] | None = None,
) -> str:
    wid = v["watch_id"]
    name = v["name"]
    alerts = v.get("alerts") or {}
    cur = v.get("currency") or "usd"
    fields = alert_fields_html({k: alerts.get(k, "") for k in ALERT_KINDS}, suffix=wid)
    on = " " + ui.label("On", "blue", "tag") if alerts else ""
    added = v.get("added_price")
    added_line = (
        f"<p class='ui-hint'>Added at {esc(money(added, v.get('added_currency') or cur))}.</p>"
        if added is not None
        else ""
    )
    details = _wx_chart(
        v,
        today,
        name,
        "Price, last 30 days",
        extra=f" data-card='{esc(name)}' data-watch='{esc(wid)}' data-currency='{esc(cur)}'",
    ) + (
        f"<form method='post' class='wx-alert'>{csrf_in}<input type='hidden' name='action' value='alerts'>"
        f"<input type='hidden' name='watch_id' value='{esc(wid)}'><h3>Price alert{on}</h3>"
        f"<div class='af'>{fields}</div>{added_line}<p class='ui-hint'>{ALERT_HINT}</p>"
        f"<div class='af wl'>"
        f"{list_select_html(lists or [], v.get('list') or '', id_=f'l-{wid}', new=False)}</div>"
        "<div class='ui-actions'><button class='btn-plain' type='submit'>Save price alert</button>"
        f"<a class='btn btn-plain' href='/cards?q={quote(name)}'>Open card</a>"
        f"<button class='btn-outline-danger' type='submit' form='rm-{esc(wid)}' "
        f"aria-label='Remove {esc(name)} from your watchlist'>Remove</button></div></form>"
        f"<form method='post' id='rm-{esc(wid)}' class='rm'>{csrf_in}"
        "<input type='hidden' name='action' value='remove_card'>"
        f"<input type='hidden' name='watch_id' value='{esc(wid)}'></form>"
    )
    img = image_url(uid, "small") if uid else None
    title = (
        f"<a href='/cards?q={quote(name)}' class='wcard' data-card='{esc(name)}'"
        + (f" data-img='{esc(img)}'" if img else "")
        + f">{esc(name)}</a>"
    )
    return _row(
        key=wid,
        check=ui.check_cell("watch_id", wid, f"Select {name}", form=BULK_FORM),
        thumb=_thumb(name, uid),
        title=title,
        line=where_text(v),
        view=v,
        today=today,
        what=name,
        alert=alert_label(v) + owned_label(v),
        is_open=query.open == wid,
        details=details,
        more_label=f"{name}: chart and alert",
        query=query,
        attrs=f" data-list='{esc(v['list'])}'" if v.get("list") else "",
    )


def deck_row(v: dict[str, Any], csrf_in: str, today: str, *, query: Query) -> str:
    did = str(v["deck_id"])
    name = v.get("name") or "Unnamed deck"
    read = ""
    if v.get("refreshed_at"):
        read = f"Read again once a day; last read {ui.when_text(v['refreshed_at'])}."
    details = _wx_chart(
        v,
        today,
        f"Value of {name}",
        "Value, last 30 days",
        extra=f" data-deck='{esc(did)}' data-currency='usd'",
    ) + (
        "<div class='wx-alert'><h3>This deck</h3>"
        + (f"<p class='ui-hint'>{esc(read)}</p>" if read else "")
        + "<div class='ui-actions'>"
        f"<a class='btn btn-plain' href='/decks/{esc(did)}'>Open deck</a>"
        f"<form method='post' class='rm'>{csrf_in}<input type='hidden' name='action' value='remove_deck'>"
        f"<input type='hidden' name='deck_id' value='{esc(did)}'>"
        "<button class='btn-outline-danger' type='submit' "
        f"aria-label='Remove {esc(name)} from your watchlist'>"
        "Remove</button></form></div></div>"
    )
    return _row(
        key=f"d{did}",
        check="<span></span>",
        thumb=_thumb(name, None, deck=True),
        title=f"<a href='/decks/{esc(did)}'>{esc(name)}</a>",
        line=f"{v.get('cards') or 0} cards · Scryfall prices",
        view=v,
        today=today,
        what=f"Value of {name}",
        alert="",
        is_open=query.open == f"d{did}",
        details=details,
        more_label=f"{name}: chart",
        query=query,
    )


# -- the list's chips, filters and order -------------------------------------------------------------
def in_chip(v: dict[str, Any], chip: str) -> bool:
    # called for every card and chip on each view (25,000 times at the cap): no dict per call
    if chip == "":
        return True
    if chip == "alert":
        return bool(v.get("alerts"))
    if chip == "up" or chip == "down":
        seven = v.get("change_7d_pct")
        return seven is not None and (seven > 0 if chip == "up" else seven < 0)
    if chip == "decks":
        return False
    raise KeyError(chip)


def in_list(v: dict[str, Any], which: str) -> bool:
    """Whether a card is in the list the address asks for ("" is every list, "-" no list)."""
    if not which:
        return True
    return (v.get("list") or NO_LIST) == ("" if which == "-" else which)


def shown_cards(cards: list[dict[str, Any]], query: Query) -> list[dict[str, Any]]:
    """The cards the address shows, in their order: by list (the member's lists in their natural
    order, the cards in no list after them), then within a list by name with its numbers as numbers
    ("Filler Card 2" before "Filler Card 10") and a printing's set and finish; the price and change
    sorts keep that order among equals."""
    q = query.q.casefold()
    out = [
        v
        for v in cards
        if in_chip(v, query.show)
        and in_list(v, query.list)
        and (not q or q in str(v.get("name") or "").casefold())
        and (not query.finish or v.get("finish") == query.finish)
    ]
    # one sort with one key per row (a list at the cap is 5,000 keys, not three passes of them);
    # the few list names' keys are worked out once each
    list_keys: dict[str, tuple] = {}

    def by(v: dict[str, Any]) -> tuple:
        lst = v.get("list") or ""
        lk = list_keys.get(lst)
        if lk is None:
            lk = list_keys[lst] = (not lst, natural_key(lst))
        if query.sort == "price":
            mid: float = -(v.get("price") or 0)
        elif query.sort in ("d1", "d7", "d30"):
            mid = -abs(v.get(f"change_{query.sort[1:]}d_pct") or 0)
        else:
            mid = 0
        return (lk, mid, natural_key(v.get("name")), v.get("set") or "", v.get("finish") or "")

    out.sort(key=by)
    return out


def shown_decks(decks: list[dict[str, Any]], query: Query | None = None) -> list[dict[str, Any]]:
    q = (query.q.casefold() if query else "") or ""
    out = [d for d in decks if not q or q in str(d.get("name") or "").casefold()]
    return sorted(out, key=lambda d: (natural_key(d.get("name")), str(d.get("deck_id") or "")))


# -- the status line -------------------------------------------------------------------------------
def due_line(v: dict[str, Any]) -> str:
    """What a card's alert says about today's price, or '' when nothing is past it."""
    alerts = v.get("alerts") or {}
    price, cur = v.get("price"), v.get("currency") or "usd"
    if price is None:
        return ""
    if alerts.get("below") is not None and price < alerts["below"]:
        return f"{v['name']} is below your {money(alerts['below'], cur)}"
    if alerts.get("above") is not None and price > alerts["above"]:
        return f"{v['name']} is above your {money(alerts['above'], cur)}"
    added = v.get("added_price")
    if added is not None and alerts.get("drop") is not None and price <= added * (1 - alerts["drop"] / 100):
        return f"{v['name']} is {alerts['drop']:g}% below the {money(added, cur)} it was added at"
    if added is not None and alerts.get("rise") is not None and price >= added * (1 + alerts["rise"] / 100):
        return f"{v['name']} is {alerts['rise']:g}% above the {money(added, cur)} it was added at"
    moved = v.get("change_1d_pct")
    if alerts.get("pct") is not None and moved is not None and abs(moved) >= alerts["pct"]:
        return f"{v['name']} moved {abs(moved):g}% in a day"
    return ""


def status_line(data: dict[str, Any], *, checked: str, defaults_html: str = "") -> str:
    """One line under the chips: the last check, the biggest 7-day move, how many price alerts
    are past, and where the prices come from (the mockup's status line in place of the widgets)."""
    cards = data["cards"]
    parts = [f"Last check {checked}" if checked else "No price check yet"]
    moved = [v for v in cards if v.get("change_7d_pct")]
    if moved:
        top = max(moved, key=lambda v: abs(v["change_7d_pct"]))
        c = top["change_7d_pct"]
        parts.append(
            f"Biggest move, 7 days: {esc(top['name'])} <span class='dl {'up' if c > 0 else 'down'}'>"
            f"<span aria-hidden='true'>{'↑' if c > 0 else '↓'}</span>{abs(c):g}%</span>"
        )
    due = [d for d in (due_line(v) for v in cards if v.get("alerts")) if d]
    if due:
        parts.append(
            f"<a class='ui-cap' href='/watchlist?show=alert' title='{esc(due[0])}'>"
            f"{icon('tag')}{plural(len(due), 'price alert')} past</a>"
        )
    parts.append("Prices from Scryfall")
    if defaults_html:
        parts.append(defaults_html)
    return f"<p class='w-status'>{' · '.join(parts)}</p>"


# -- the rows on screen, and Load more -------------------------------------------------------------
def _no_history(_views: list[dict[str, Any]]) -> None:
    return None


def group_head(
    name: str, rows: list[dict[str, Any]], *, csrf_in: str = "", query: Query | None = None
) -> str:
    """A list's row at the head of its cards: a tick box for every card in it, its name, how many
    cards and what they are worth together, and for a list of the member's a ⋮ with Rename list…
    and Delete list… (a plain drop-down of links without scripts)."""
    usd = [v["price"] for v in rows if v.get("price") is not None and v.get("currency") == "usd"]
    meta = plural(len(rows), "card") + (f" · {money(sum(usd))}" if usd else "")
    menu = ""
    if name:
        q = query or Query()
        items = "".join(
            f"<a href='{esc(q.href(box=box, list=name))}' data-icon='{ic}'{extra}>{icon(ic)}{text}</a>"
            for text, ic, box, extra in (
                ("Rename list…", "edit", "rename", ""),
                ("Delete list…", "trash", "delete", " class='danger'"),
            )
        )
        menu = (
            "<details class='dd rowmenu w-dd'><summary class='kebab' "
            f"aria-label='Menu for the list {esc(name)}' "
            f"title='Menu' aria-haspopup='menu'>{icon('more')}</summary>"
            f"<div class='menu'><div class='head'>{esc(name)}</div>{items}</div></details>"
        )
    check = (
        "<label class='chk'><input type='checkbox' class='w-grpsel' "
        f"aria-label='Select every card in {esc(list_word(name))}'></label>"
    )
    return (
        f"<div class='tr group wgrp' data-list='{esc(name)}'>{check}"
        f"<span class='gname'>{icon('folder')}<b>{esc(list_word(name))}</b></span>"
        f"<span class='meta'>{esc(meta)}{menu}</span></div>"
    )


def card_rows_html(
    cards: list[dict[str, Any]],
    ids: dict[str, str | None],
    csrf_in: str,
    today: str,
    query: Query,
    card_history: Callable[[list[dict[str, Any]]], None],
    *,
    start: int = 0,
    lists: list[str] | None = None,
) -> tuple[Query, list[dict[str, Any]], str, str]:
    """The card rows from ``start`` up to the query's ``upto`` (the first screenful at least),
    each list's head before its first row on screen, and the Load more under them; only those
    rows get their 30 days of prices. An open row (a price alert's link) past the first screenful
    is found by searching the list for its name, never by drawing every row before it. Returns the
    query as drawn, the whole list as sorted and filtered, the rows and the Load more."""
    shown = shown_cards(cards, query)
    upto = max(PAGE, query.upto)
    if query.open and not query.open.startswith("d") and not query.q:
        at = next((i for i, v in enumerate(shown) if str(v["watch_id"]) == query.open), -1)
        if at >= upto:
            query = replace(query, q=str(shown[at]["name"]), upto=0)
            shown = shown_cards(cards, query)
    total = len(shown)
    page_rows = shown[start:upto]
    card_history(page_rows)
    # a head before the first row of each list on this screen; one unnamed group needs none
    named = any(v.get("list") for v in shown)
    last = shown[start - 1].get("list") if start and start <= len(shown) else None
    parts = []
    for v in page_rows:
        name = v.get("list") or NO_LIST
        if name != last and (named or query.list == "-"):
            parts.append(
                group_head(name, [x for x in shown if (x.get("list") or NO_LIST) == name], query=query)
            )
            last = name
        parts.append(card_row(v, ids.get(v["watch_id"]), csrf_in, today, query=query, lists=lists))
    if page_rows:
        body = "".join(parts)
    elif start:
        body = ""
    elif query.list and not any(in_list(v, query.list) for v in cards):
        # a list with no card in it yet: nothing was filtered away (independent review 2, L4)
        name = list_word("" if query.list == "-" else query.list)
        body = (
            f"<p class='ui-empty'>No cards in “{esc(name)}” yet. Move cards here with a card's "
            "Move to list…, or tick several with Select.</p>"
        )
    elif cards:
        body = "<p class='ui-empty'>No cards match. Clear the search or the filters to see them all.</p>"
    else:
        body = "<p class='ui-empty'>No cards watched yet.</p>"
    # Load more, not pages: the list is sorted (by name or price), so it grows in place. Without a
    # script the link loads the longer list and lands on the first new row.
    end = min(upto, total)
    if end < total:
        pager = ui.load_more(
            query.href(upto=end + PAGE, open="") + f"#wr-{shown[end]['watch_id']}",
            text="Load more cards",
            shown=end,
            total=total,
        )
    elif total > PAGE:
        pager = ui.load_more("", done=f"All {total:,} cards shown.")
    else:
        pager = ""
    return query, shown, body, pager


def deck_rows_html(
    decks: list[dict[str, Any]],
    csrf_in: str,
    today: str,
    query: Query,
    deck_history: Callable[[list[dict[str, Any]]], None],
    *,
    start: int = 0,
) -> tuple[str, str]:
    """The deck rows from ``start`` up to ``dupto`` and their Load more, as card_rows_html."""
    decks = shown_decks(decks, query)
    dupto = max(PAGE, query.dupto)
    if query.open.startswith("d"):  # an opened deck is shown wherever it sits (100 at most)
        at = next((i for i, v in enumerate(decks) if f"d{v['deck_id']}" == query.open), -1)
        if at >= dupto:
            dupto = (at // PAGE + 1) * PAGE
    deck_rows = decks[start:dupto]
    deck_history(deck_rows)
    if deck_rows or start:
        body = "".join(deck_row(v, csrf_in, today, query=query) for v in deck_rows)
    else:
        body = "<p class='ui-empty'>No decks watched yet.</p>"
    end = min(dupto, len(decks))
    if end < len(decks):
        pager = ui.load_more(
            query.href(dupto=end + PAGE, open="") + f"#wr-d{decks[end]['deck_id']}",
            text="Load more decks",
            shown=end,
            total=len(decks),
        )
    elif len(decks) > PAGE:
        pager = ui.load_more("", done=f"All {len(decks):,} decks shown.")
    else:
        pager = ""
    return body, pager


def watchlist_fragment_html(
    data: dict[str, Any],
    ids: dict[str, str | None],
    csrf: str | None,
    query: Query,
    *,
    part: str,
    start: int,
    now: float | None = None,
    card_history: Callable[[list[dict[str, Any]]], None] = _no_history,
    deck_history: Callable[[list[dict[str, Any]]], None] = _no_history,
) -> str:
    """What Load more (static/watchlist.js) adds in place: the card (``part`` "cards") or deck
    ("decks") rows from ``start``, the rows already on screen, up to the query's ``upto`` or
    ``dupto``, then the Load more that follows them."""
    now = time.time() if now is None else now
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    csrf_in = f"<input type='hidden' name='csrf' value='{esc(csrf or '')}'>"
    if part == "decks":
        rows, pager = deck_rows_html(data["decks"], csrf_in, today, query, deck_history, start=start)
    else:
        _q, _shown, rows, pager = card_rows_html(
            data["cards"],
            ids,
            csrf_in,
            today,
            query,
            card_history,
            start=start,
            lists=[x["name"] for x in data.get("lists") or []],
        )
    return f"<div class='w-rows' data-part='{esc(part)}'>{rows}</div>{pager}"


# -- the boxes ----------------------------------------------------------------------------------
def _box(
    box_id: str,
    title: str,
    body: str,
    *,
    actions: list[str],
    query: Query,
    key: str,
) -> str:
    return ui.box(
        box_id,
        title,
        body,
        actions=actions,
        open=query.box == key,
        close_href=query.href(box="", list=query.list if query.list != "" else ""),
    )


def boxes_html(
    data: dict[str, Any],
    csrf_in: str,
    query: Query,
    *,
    defaults: dict[str, Any],
    kept: dict[str, Any],
) -> str:
    """Every box the page holds (hidden until opened; ``query.box`` names the one drawn open)."""
    lim = data["limits"]
    lists = [x["name"] for x in data.get("lists") or []]
    fin = kept.get("finish") if kept.get("finish") in FINISH_WORDS else "nonfoil"
    default_words = alert_text(defaults, "usd") if defaults else "none"
    add = _box(
        "w-add",
        "Watch a card",
        "<form method='post' class='w-addrow' id='w-add-f'>"
        f"{csrf_in}<input type='hidden' name='action' value='add_card'>"
        "<label class='wide' for='w-name'>Name<input id='w-name' name='name' maxlength='150' "
        "autocomplete='off' data-suggest='cards' required></label>"
        "<label for='w-set'>Set code (optional)<input id='w-set' name='set_code' maxlength='6' "
        "autocomplete='off'></label>"
        "<label for='w-cn'>Collector no. (optional)<input id='w-cn' name='cn' maxlength='10' "
        "autocomplete='off'></label>"
        "<label for='w-fin'>Finish<select id='w-fin' name='finish'>"
        + "".join(
            f"<option value='{k}'{' selected' if k == fin else ''}>{w}</option>"
            for k, w in FINISH_WORDS.items()
        )
        + "</select></label>"
        + list_select_html(lists, query.list if query.list != "-" else "", id_="w-list")
        + "<label class='check wide'><input type='checkbox' name='remember' value='1' checked>"
        "<span>Use this finish next time, here and in card menus</span></label>"
        f"<p class='ui-hint wide'>Price alert: {esc(default_words)} (your defaults). {CARD_HINT} "
        f"{SEARCH_HINT} You may watch up to {lim['cards']:,} cards and {lim['decks']:,} decks.</p></form>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Watch card", kind="primary", form="w-add-f"),
        ],
        query=query,
        key="add",
    )
    deck = _box(
        "w-deck",
        "Watch a deck",
        f"<form method='post' class='w-deckadd' id='w-deck-f' aria-label='Watch a deck'>{csrf_in}"
        "<input type='hidden' name='action' value='add_deck'>"
        "<label class='wide' for='w-deckref'>Archidekt deck link or ID"
        "<input id='w-deckref' name='deck' maxlength='200' autocomplete='off' required></label>"
        f"<p class='ui-hint'>{DECK_HINT}</p></form>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Watch deck", kind="primary", form="w-deck-f"),
        ],
        query=query,
        key="deck",
    )
    defaults_box = _box(
        "w-defaults",
        "Price alert defaults",
        f"<form method='post' class='wx-alert' id='w-defaults-f'>{csrf_in}"
        "<input type='hidden' name='action' value='defaults'>"
        "<div class='af'>"
        f"{alert_fields_html({k: defaults.get(k, '') for k in ALERT_KINDS}, suffix='def')}</div>"
        f"<p class='ui-hint'>{DEFAULTS_HINT} {ALERT_HINT}</p></form>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Save defaults", kind="primary", form="w-defaults-f"),
        ],
        query=query,
        key="defaults",
    )
    newlist = _box(
        "w-newlist",
        "New list",
        f"<form method='post' class='w-deckadd' id='w-newlist-f'>{csrf_in}"
        "<input type='hidden' name='action' value='list_new'>"
        "<label class='wide' for='w-newname'>Name<input id='w-newname' name='name' "
        f"maxlength='{MAX_LIST_NAME}' "
        "autocomplete='off' required></label>"
        "<p class='ui-hint'>A list groups the cards you watch: wants for one deck, cards to trade for, "
        "anything you like. Move cards into it from their ⋮ or with Select.</p></form>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Create list", kind="primary", form="w-newlist-f"),
        ],
        query=query,
        key="newlist",
    )
    which = query.list if query.list not in ("", "-") else ""
    rename = _box(
        "w-rename",
        "Rename list",
        f"<form method='post' class='w-deckadd' id='w-rename-f'>{csrf_in}"
        "<input type='hidden' name='action' value='list_rename'>"
        f"<input type='hidden' name='name' value='{esc(which)}' data-list-name>"
        f"<label class='wide' for='w-renamed'>New name<input id='w-renamed' name='new_name' "
        f"maxlength='{MAX_LIST_NAME}' value='{esc(which)}' autocomplete='off' required></label></form>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Rename", kind="primary", form="w-rename-f"),
        ],
        query=query,
        key="rename",
    )
    delete = _box(
        "w-delete",
        "Delete this list?",
        f"<form method='post' id='w-delete-f'>{csrf_in}"
        "<input type='hidden' name='action' value='list_delete'>"
        f"<input type='hidden' name='name' value='{esc(which)}' data-list-name>"
        f"<p>Delete <b data-list-word>“{esc(which)}”</b>? Its cards stay on your watchlist, "
        "in no list.</p></form>",
        actions=[
            ui.box_button("Keep it", cancel=True),
            ui.box_button("Delete list", kind="danger-solid", form="w-delete-f"),
        ],
        query=query,
        key="delete",
    )
    # the bulk bar's boxes: their fields and buttons belong to the bulk form (BULK_FORM), which
    # holds the rows' tick boxes, so a press sends the selected cards with the box's values
    bulk_alerts = _box(
        "w-alerts",
        "Price alert for the selected cards",
        f"<div class='wx-alert'><div class='af'>{alert_fields_html({}, suffix='bulk', form=BULK_FORM)}</div>"
        f"<p class='ui-hint'>The same price alert on every selected card. {ALERT_HINT}</p></div>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Save price alert", kind="primary", name="do", value="alerts", form=BULK_FORM),
        ],
        query=query,
        key="alerts",
    )
    move = _box(
        "w-move",
        "Move the selected cards to a list",
        f"<div class='w-addrow'>{list_select_html(lists, '', id_='w-move-list', form=BULK_FORM)}</div>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Move", kind="primary", name="do", value="move", form=BULK_FORM),
        ],
        query=query,
        key="move",
    )
    stop = _box(
        "w-stop",
        "Stop watching the selected cards?",
        "<p><span data-stop-count>The selected cards</span> go from your watchlist with their price "
        "history. Your Binder is not touched.</p>",
        actions=[
            ui.box_button("Keep them", cancel=True),
            ui.box_button("Stop watching", kind="danger-solid", name="do", value="remove", form=BULK_FORM),
        ],
        query=query,
        key="stop",
    )
    return add + deck + defaults_box + newlist + rename + delete + bulk_alerts + move + stop


# -- the page ---------------------------------------------------------------------------------------


def stop_words(ok: str, said: dict[str, str] | None) -> str:
    """The notice after Stop watching names how many went, and after its Undo which are back."""
    said = said or {}
    if ok not in ("removed", "rewatched"):
        return OK_NOTES[ok]
    tail = "no longer watched." if ok == "removed" else "watched again."
    if said.get("w"):
        names = said["w"]
        many = ", " in names
        if many:
            head, _, last = names.rpartition(", ")
            names = f"{head} and {last}"
        return f"{names} {'are' if many else 'is'} {tail}"
    if said.get("n", "").isdigit():
        n = int(said["n"])
        return f"1 card is {tail}" if n == 1 else f"{n} cards are {tail}"
    return OK_NOTES[ok]


def undo_form(ok: str, said: dict[str, str] | None, csrf_in: str) -> str:
    """The Undo button beside the Stop watching notice (0.7.46 audit QG-09): a small form, so
    it works without scripts; the token is the member's own and lasts a while."""
    token = (said or {}).get("undo") or ""
    if ok != "removed" or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token):
        return ""
    return (
        f" <form method='post' action='/watchlist' class='inline'>{csrf_in}"
        "<input type='hidden' name='action' value='rewatch'>"
        f"<input type='hidden' name='token' value='{esc(token)}'>"
        "<button type='submit' class='btn small'>Undo</button></form>"
    )


def watchlist_page_html(
    data: dict[str, Any],
    ids: dict[str, str | None],
    csrf: str | None,
    query: Query,
    *,
    ok: str = "",
    err: str = "",
    said: dict[str, str] | None = None,
    now: float | None = None,
    card_history: Callable[[list[dict[str, Any]]], None] = _no_history,
    deck_history: Callable[[list[dict[str, Any]]], None] = _no_history,
    defaults: dict[str, Any] | None = None,
    alert_defaults: dict[str, Any] | None = None,
) -> str:
    """The page's body from the watch summaries (``data``: PriceService.card_summaries and
    deck_summaries, no history; ``lists``: the member's watchlist lists) and each watch's Scryfall
    id (``ids``, for its picture). ``card_history`` and ``deck_history`` give the rows on screen
    their last 30 days. ``defaults``: the Watch form's remembered finish (prefs.WATCH_ADD);
    ``alert_defaults``: the price alert every new watch gets (prefs.alert_defaults)."""
    now = time.time() if now is None else now
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    # every form comes back to the list as it was looked at (its chip, search, filters and list)
    csrf_in = (
        f"<input type='hidden' name='csrf' value='{esc(csrf or '')}'>"
        f"<input type='hidden' name='back' value='{esc(query.href())}'>"
    )
    notice = ""
    if ok in OK_NOTES:
        notice = (
            f"<div class='notice ok' role='status'>{esc(stop_words(ok, said))}"
            f"{undo_form(ok, said, csrf_in)}</div>"
        )
    if err:
        notice = f"<div class='notice error' role='alert'>{esc(err)}</div>"
    kept = defaults or {}
    alerts_default = alert_defaults if alert_defaults is not None else {}
    lists = [x["name"] for x in data.get("lists") or []]
    at = data["checked_daily_at_utc"]
    days = [v["day"] for v in data["cards"] + data["decks"] if v.get("day")]
    last = max(days) if days else ""
    checked = f"{day_text(last, today)} at {at} UTC" if last else ""
    cards = data["cards"]
    query, _shown, body, pager = card_rows_html(cards, ids, csrf_in, today, query, card_history, lists=lists)
    # the list the address shows: its own chips' counts and total (L4), every card without one
    listed = [v for v in cards if in_list(v, query.list)]
    decks = data["decks"]
    chips = ui.chips(
        [
            ui.Chip(
                word,
                len(decks) if key == "decks" else sum(1 for v in listed if in_chip(v, key)),
                query.href(show=key, upto=0),
                key == query.show,
            )
            for key, word in CHIPS
        ]
    )
    # the top row: the search and the filters as one GET form (a filter applies as it changes,
    # static/feedback.js), with "+ Watch a card" and Select between them as the mockup draws it
    keep = "".join(
        f"<input type='hidden' name='{k}' value='{esc(v)}'>"
        for k, v in (("show", query.show), ("list", query.list))
        if v
    )
    tools = (
        f"<form class='ui-tools w-top' role='search' method='get' action='/watchlist'>{keep}"
        "<label class='search'><span class='sr-only'>Search your watchlist</span>"
        f"{icon('search')}<input type='search' name='q' value='{esc(query.q)}' "
        "placeholder='Search'></label>"
        f"<a class='btn btn-plain' href='{esc(query.href(box='add'))}' {ui.box_open_attr('w-add')}>"
        f"{icon('plus')}Watch a card</a>"
        "<button type='button' class='btn-plain w-select' aria-pressed='false' hidden>"
        f"{icon('check')}Select</button>"
        + ui.filter_select("sort", "Sort by", list(SORTS), query.sort)
        + ui.filter_select("finish", "Finish", [("", "Any finish"), *FINISH_WORDS.items()], query.finish)
        + "<noscript><button type='submit' class='btn-plain'>Search</button></noscript></form>"
    )
    defaults_link = (
        f"<a class='ui-cap' href='{esc(query.href(box='defaults'))}' {ui.box_open_attr('w-defaults')}>"
        f"{icon('tag')}Price alert defaults</a>"
    )
    status = status_line(data, checked=checked, defaults_html=defaults_link)
    if query.list:
        back = ui.cap_link("Every list", query.href(list="", upto=0), icon_name="list")
        showing = list_word("" if query.list == "-" else query.list)
        status += f"<p class='w-status'>Showing {esc(showing)} · {back}</p>"
    # the bulk bar and the form behind it (the rows' tick boxes and the boxes' fields belong to it)
    # one slim row (final audit F01): × Cancel, the count, ⋯ (Price alert…, Move to list…), Stop
    # watching
    bar = ui.selection_bar(
        "0 selected",
        [],
        label="Selected cards",
        hidden=True,
        cancel_cls="w-clear",
        more=[
            ui.menu_opener("Price alert…", query.href(box="alerts"), "w-alerts", icon_name="tag"),
            ui.menu_opener("Move to list…", query.href(box="move"), "w-move", icon_name="folder"),
        ],
        end=[
            f"<a class='btn btn-outline-danger' href='{esc(query.href(box='stop'))}' "
            f"{ui.box_open_attr('w-stop')} aria-label='Stop watching' title='Stop watching'>"
            f"{icon('x')}<span class='lbl'>Stop watching</span></a>"
        ],
    )
    bulk_form = (
        f"<form method='post' id='{BULK_FORM}' class='w-bulkform'>{csrf_in}"
        "<input type='hidden' name='action' value='bulk'></form>"
    )
    head = ui.table_header(
        [
            ui.Column(""),
            ui.Column(""),
            ui.Column("Card"),
            ui.Column("Price", "md-hide"),
            ui.Column("Change", "md-hide"),
            ui.Column("30 days", "md-hide"),
            ui.Column("Price alert", "md-hide"),
            ui.Column(""),
        ]
    )
    new_list = (
        f"<a class='ui-cap' href='{esc(query.href(box='newlist'))}' {ui.box_open_attr('w-newlist')}>"
        f"{icon('plus')}New list</a>"
    )
    worth = [c["price"] for c in listed if c.get("price") is not None]
    card_list = (
        "<div id='w-cards' class='w-sec'>"
        + ui.list_head("Cards", len(listed), f"Together {money(sum(worth))} today" if worth else "")
        + f"<span class='w-secacts'>{new_list}{ui.card_size_control(cls='cardsize w-size')}</span></div>"
        + ui.table_open(TABLE_COLS, TABLE_MD, TABLE_SM, cls="w-t", label="Watched cards")
        + head
        + body
        + "</div>"
        + pager
    )
    deck_head = ui.table_header(
        [
            ui.Column(""),
            ui.Column(""),
            ui.Column("Deck"),
            ui.Column("Value", "md-hide"),
            ui.Column("Change", "md-hide"),
            ui.Column("30 days", "md-hide"),
            ui.Column("", "md-hide"),
            ui.Column(""),
        ]
    )
    deck_body, deck_pager = deck_rows_html(decks, csrf_in, today, query, deck_history)
    usd = [d["price"] for d in decks if d.get("price") is not None]
    watch_deck = (
        f"<a class='ui-cap' href='{esc(query.href(box='deck'))}' {ui.box_open_attr('w-deck')}>"
        f"{icon('plus')}Watch a deck</a>"
    )
    deck_list = (
        "<div id='w-decks' class='w-sec'>"
        + ui.list_head(DECKS_WORD, len(decks), f"Together {money(sum(usd))}" if usd else "")
        + f"<span class='w-secacts'>{watch_deck}</span></div>"
        + ui.table_open(TABLE_COLS, TABLE_MD, TABLE_SM, cls="w-t", label="Watched decks")
        + deck_head
        + deck_body
        + "</div>"
        + deck_pager
    )
    only_decks = query.show == "decks"
    return (
        f"<div class='watch-page gb-dash{' decks-only' if only_decks else ''}'>"
        + ui.binder_head("/watchlist", "Binder: Watchlist", badges={"/watchlist": len(cards)})
        + notice
        + tools
        + chips
        + status
        + bar
        + bulk_form
        + ("" if only_decks else card_list)
        + deck_list
        + boxes_html(data, csrf_in, query, defaults=alerts_default, kept=kept)
        + "</div>"
    )


WATCH_PAGE_CSS = (
    PHONE_WIDGETS_CSS
    + """
/* the Watchlist */
@media (min-width:900px){.watch-page .w-t .chgs{display:flex}}
.watch-page .ui-tools.w-top{margin:0 0 .6rem}
.watch-page .w-top .btn,.watch-page .w-top button{margin:0;white-space:nowrap}
.watch-page .w-top .btn svg.i,.watch-page .w-top button svg.i{margin-right:.35rem}
.watch-page .w-top .w-select[aria-pressed=true]{background:var(--orange-tint);color:var(--orange-text);
  border-color:var(--orange-text)}
.watch-page .w-top .w-select:not([hidden]){display:inline-flex;align-items:center}
.watch-page .w-status{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem .4rem;margin:0 0 .75rem;
  color:var(--text-muted);font-size:.9rem}
.watch-page .w-status .dl{font-size:inherit}
.watch-page .w-sec{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;
  gap:.4rem .75rem}
.watch-page .w-sec .list-head{margin:0;flex:1 1 auto}
.watch-page .w-secacts{display:inline-flex;flex-wrap:wrap;align-items:center;gap:.5rem}
.watch-page .w-secacts .ui-cap{white-space:nowrap}
.watch-page .w-addrow{display:grid;gap:.6rem 1rem;align-items:end;grid-template-columns:1fr 1fr}
.watch-page .w-addrow .wide{grid-column:1 / -1}
.watch-page .w-addrow label,.watch-page .w-deckadd label{display:flex;flex-direction:column;gap:.25rem;
  margin:0;
  min-width:0;font-weight:700}
.watch-page .w-addrow input,.watch-page .w-addrow select:not(.msel-native),
.watch-page .w-deckadd input{width:100%;margin:0}
.watch-page .w-addrow button,.watch-page .w-deckadd button{margin:0}
@media (max-width:599.98px){.watch-page .w-addrow{grid-template-columns:1fr}}
.watch-page .w-addrow label.check{flex-direction:row;align-items:center;gap:.5rem;font-weight:400;
  min-height:44px;grid-column:1 / -1}
.watch-page .w-addrow label.check input{width:auto;min-width:22px;min-height:22px}
.watch-page .w-addrow .ui-hint{margin:0}
.watch-page .w-deckadd{display:flex;flex-wrap:wrap;gap:.6rem 1rem;align-items:end;margin:0}
.watch-page .w-deckadd .wide{flex:1 1 18rem}
.watch-page .w-deckadd .ui-hint{flex-basis:100%;margin:0}
.watch-page .ui-empty{padding:1rem;margin:0;color:var(--text-muted)}
.watch-page .w-t{margin:0 0 .5rem}
.watch-page .w-t .thumb{display:flex;align-items:center;justify-content:center;width:48px;height:67px;
  border-radius:4px;overflow:hidden;background:var(--surface-2);font-size:.62rem;line-height:1.1;
  text-align:center;
  color:var(--text-muted);padding:2px;overflow-wrap:anywhere}
.watch-page .w-t .thumb img{width:100%;height:100%;object-fit:cover}
.watch-page .w-t .thumb.deckart{height:48px;background:var(--orange-tint);color:var(--orange-text)}
.watch-page .w-t .title .t a{font-weight:700}
/* the grid's card name is a 44 px target on a touch screen (audit D07) */
.watch-page a.wcard{display:inline-flex;align-items:center;min-height:44px}
.watch-page .w-t .title .deck{color:var(--text-muted);font-size:.86rem}
.watch-page .w-t .px{font-variant-numeric:tabular-nums;font-weight:700}
.watch-page .w-t .px small{display:block;font-weight:400;color:var(--text-muted);font-size:.8rem}
.watch-page .w-t .chgs{flex-direction:column;gap:.1rem;font-size:.84rem;color:var(--text-muted)}
.watch-page .dl{font-weight:700;color:var(--text-muted);white-space:nowrap}
.watch-page .dl.up{color:var(--green-text)}
.watch-page .dl.down{color:var(--red-text)}
.watch-page .dl span{margin-right:.15rem}
.watch-page .w-t .sub{flex-wrap:wrap;gap:.2rem .5rem;align-items:center}
.watch-page .w-t .sub .kv{color:var(--text-muted);font-size:.84rem}
/* the alert column from 900 px only: below, the labels sit in the row's .sub line */
@media (min-width:900px){.watch-page .w-t .lbls{display:flex;flex-wrap:wrap;gap:.25rem}}
.watch-page .none{color:var(--text-muted);font-size:.86rem}
.watch-page .w-t .spark .tspark{display:block;width:100%;height:36px;color:var(--orange-text)}
.watch-page .tspark polyline,.watch-page .pcl polyline{fill:none;stroke:currentColor;stroke-width:2;
  stroke-linejoin:round;stroke-linecap:round}
.watch-page .w-t .tr.open{background:var(--orange-tint)}
.watch-page .w-t .tr.sel{background:var(--orange-tint)}
.watch-page .w-t .act{display:inline-flex;align-items:center;gap:.4rem;flex-wrap:nowrap;white-space:nowrap}
.watch-page .w-t .act .more{min-height:var(--ctl);display:inline-flex;align-items:center;margin:0}
/* a list's head: its name, count and value, and its ⋮ at the row's end */
.watch-page .w-t .tr.wgrp{grid-template-columns:32px minmax(0,1fr) auto;padding:.35rem .75rem;
  min-height:44px}
.watch-page .w-t .wgrp .gname{display:inline-flex;align-items:center;gap:.4rem;min-width:0}
.watch-page .w-t .wgrp .gname svg.i{color:var(--orange-text);flex:none}
.watch-page .w-t .wgrp .gname b{overflow-wrap:anywhere}
.watch-page .w-t .wgrp .meta{display:inline-flex;align-items:center;gap:.5rem;white-space:normal}
@media (max-width:599.98px){
  /* one line on a phone too: name, then count and value, then the ⋮ */
  .watch-page .w-t .tr.wgrp{grid-template-columns:32px minmax(0,1fr) auto;padding:.35rem .6rem}
  .watch-page .w-t .wgrp .meta{grid-column:auto;font-size:.84rem}
  /* Watch a card and Select side by side; one that no longer fits half the row (200% text)
     takes a row of its own instead of being cut */
  .watch-page .w-top > .btn,.watch-page .w-top > button.w-select{flex:1 1 calc(50% - .5rem);
    justify-content:center;min-width:max-content}
  /* the row's end (Details, ⋮) may stack at 200% text on a narrow phone, never push sideways */
  .watch-page .w-t .act{flex-wrap:wrap;justify-content:flex-end;min-width:0;white-space:normal}
  .watch-page .w-t .act .more{white-space:nowrap}
  .watch-page .w-t .sub .dl{white-space:normal}
}
/* Large text on a narrow phone (independent review 2, M5): the table and the tools row are
   containers, and under about 20 em of their own text (200% text at 375 px) their rows change
   shape rather than squeeze the name to one letter per line: no thumbnail, the name wraps between
   its words over the whole width, the row's end (Details, ⋮) takes a line of its own under the
   name, a list head's count and value go under the list's name, and each picker takes a whole
   line instead of showing "So…". */
.watch-page .w-t,.watch-page .ui-tools.w-top{container-type:inline-size}
/* a row narrower than "Watch a card" itself (320 px with the browser's font doubled: final
   audit F02): the button takes the row and wraps its words instead of pushing the page sideways */
@container (max-width:11em){
  .watch-page .w-top > .btn,.watch-page .w-top > button.w-select{flex:1 1 100%;min-width:0;
    white-space:normal;height:auto} }
@container (max-width:20em){
  .watch-page .w-top .f{flex:1 1 100%}
  .watch-page .w-t .tr{grid-template-columns:32px minmax(0,1fr)}
  .watch-page .w-t .tr > .thumb{display:none}
  .watch-page .w-t .tr > .act{grid-column:2;justify-self:end;margin-top:.25rem}
  .watch-page .w-t .tr.wgrp{grid-template-columns:32px minmax(0,1fr)}
  .watch-page .w-t .wgrp .meta{grid-column:2;width:100%;justify-content:space-between}
}
.watch-page .w-dd > summary.kebab{display:inline-grid;place-items:center;width:44px;height:44px;
  margin:-.45rem -.4rem -.45rem 0;border-radius:var(--radius);color:var(--text)}
.watch-page .w-dd > summary.kebab:hover{background:var(--surface-3)}
.watch-page .w-dd .menu{min-width:12rem}
.watch-page .w-size{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:.5rem;
  margin:0;max-width:100%}
.watch-page .w-size .sizeseg{max-width:100%}
.watch-page .w-size .lbl{color:var(--text-muted);font-size:.9rem}
.watch-page .w-bulkform{display:none}
.watch-page .ui-bar .btn,.watch-page .ui-bar button{display:inline-flex;align-items:center;gap:.35rem}
.watch-page .wx{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:.75rem 1.5rem;
  padding:.75rem 1rem 1rem;border-bottom:1px solid var(--border-soft)}
.watch-page .wx[hidden]{display:none}
.watch-page .wx-chg{grid-column:1 / -1;display:none;flex-wrap:wrap;gap:.25rem 1rem;margin:0;font-size:.88rem;
  color:var(--text-muted)}
@media (max-width:899.98px){.watch-page .wx{grid-template-columns:1fr}.watch-page .wx-chg{display:flex}}
.watch-page .wx h3{font-size:1rem;margin:0}
.watch-page .wx-h{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;
  gap:.4rem .75rem;
  margin:0 0 .5rem}
.watch-page .seg{display:inline-flex;flex-wrap:wrap;border:1px solid var(--card-border);
  border-radius:var(--radius);
  overflow:hidden}
.watch-page .seg button{margin:0;border:0;border-radius:0;background:transparent;color:var(--text);
  min-height:36px;
  padding:0 .75rem;font-weight:700}
.watch-page .seg button + button{border-left:1px solid var(--card-border)}
.watch-page .seg button[aria-pressed=true]{background:var(--orange-tint);color:var(--orange-text)}
.watch-page .seg button{min-height:44px}
.watch-page .pc{display:grid;grid-template-columns:auto minmax(0,1fr);grid-template-rows:10rem auto;
  gap:.2rem .5rem}
.watch-page .pc-y{display:flex;flex-direction:column;justify-content:space-between;font-size:.8rem;
  color:var(--text-muted);text-align:right;font-variant-numeric:tabular-nums}
.watch-page .pcl{width:100%;height:100%;color:var(--orange-text);border-left:1px solid var(--border-soft);
  border-bottom:1px solid var(--border-soft)}
.watch-page .pc-x{grid-column:2;display:flex;justify-content:space-between;font-size:.8rem;
  color:var(--text-muted)}
.watch-page .pc-x{flex-wrap:wrap;gap:0 .5rem;min-width:0}
@media (max-width:599.98px){.watch-page .pc-x span:nth-child(2),
  .watch-page .pc-x span:nth-child(4){display:none}}
.watch-page .wx .tbl{overflow:auto;max-height:16rem;max-width:100%}
.watch-page .wx table{border-collapse:collapse;font-size:.86rem;font-variant-numeric:tabular-nums}
.watch-page .wx th,.watch-page .wx td{padding:.25rem .6rem;border-bottom:1px solid var(--border-soft);
  text-align:right}
.watch-page .wx th[scope=row],.watch-page .wx thead th:first-child{text-align:left}
.watch-page .wx-alert{margin:0;min-width:0}
.watch-page .wx-alert h3{display:flex;align-items:center;gap:.5rem;margin:0 0 .5rem}
.watch-page .wx-alert .af{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,10rem),1fr));
  gap:.5rem}
.watch-page .wx-alert .af.wl{margin-top:.5rem}
.watch-page .wx-alert label{display:flex;flex-direction:column;gap:.25rem;margin:0;min-width:0;
  font-weight:700}
.watch-page .wx-alert input,.watch-page .wx-alert select:not(.msel-native){width:100%;margin:0}
.watch-page .wx .ui-actions{display:flex;flex-wrap:wrap;gap:.5rem;margin:.5rem 0 0}
.watch-page .wx .ui-actions > *{margin:0}
.watch-page .wx form.rm{margin:0;display:contents}
.watch-page .ui-box .ui-hint{margin:.5rem 0 0}
"""
)
