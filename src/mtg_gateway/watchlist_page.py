"""The Watchlist page (``/watchlist``) as a dashboard.

The intro, four widgets (cards watched, alerts, the biggest 7-day move, decks watched), a
"Watch a card" panel, then the cards as the standard table (chips with counts, a search, a finish
filter and a sort, 25 a page) with each card's picture, price, its 1, 7 and 30-day changes (an arrow
and a word, green up and red down), a 30-day sparkline and its alert as a blue label; then the
decks with their own form and table. "Details" opens one row in place: the changes, the price
chart with "30 days" and "Full history (N days)", the price history table, the alert form, "Open
card" and a red-outline "Remove".

Everything comes from ``PriceService.overview`` (at most 200 cards and 10 decks): the chips,
search, filter and sort work on that in the page, with no new query. static/watchlist.js opens the
rows in place and draws the full history from the existing ``/watchlist/api/card`` (cards) or the
deck row's own data (decks)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from . import ui
from .deckpage import image_url
from .history_view import _trend_svg
from .prices import money
from .reports_page import PHONE_WIDGETS_CSS
from .theme import esc

PAGE = ui.PER_PAGE
FINISH_WORDS = {"nonfoil": "Normal", "foil": "Foil", "etched": "Etched"}
CHIPS = (("", "All"), ("alert", "With an alert"), ("up", "Up in 7 days"), ("down", "Down in 7 days"))
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
OK_NOTES = {
    "card_added": "Added to your watchlist.",
    "card_updated": "Alerts saved.",
    "card_removed": "Removed from your watchlist.",
    "deck_added": "Deck added to your watchlist.",
    "deck_removed": "Deck removed from your watchlist.",
}
CARD_HINT = (
    "A name alone follows the printing Scryfall gives for that name; add a set code and number to "
    "follow one printing."
)
SEARCH_HINT = "You can also watch a card from Card search: press and hold it, or right-click it."
ALERT_HINT = (
    "Checked once a day against the day before. Leave a box empty for no alert. Alerts reach you "
    "through notifications (Account page)."
)
DECK_HINT = (
    "A watched deck is read again once a day, so its value follows its cards. Its value uses "
    "Scryfall's prices, so it can differ from the deck page's estimated cost, which uses Archidekt's."
)


@dataclass(frozen=True)
class Query:
    """What the address asks for: the chip, the search, the finish, the sort, the page and the open row."""

    show: str = ""
    q: str = ""
    finish: str = ""
    sort: str = ""
    offset: int = 0
    open: str = ""

    @classmethod
    def parse(cls, qp: Any) -> Query:
        def get(k: str, n: int) -> str:
            return str(qp.get(k) or "").strip()[:n]

        try:
            offset = max(0, min(int(qp.get("offset") or 0), 1000))
        except ValueError:
            offset = 0
        show, finish, sort = get("show", 10), get("finish", 10), get("sort", 10)
        return cls(
            show=show if show in dict(CHIPS) else "",
            q=get("q", 100),
            finish=finish if finish in FINISH_WORDS else "",
            sort=sort if sort in dict(SORTS) else "",
            offset=offset,
            open=get("open", 64),
        )

    def href(self, **change: Any) -> str:
        values = {
            "show": self.show,
            "q": self.q,
            "finish": self.finish,
            "sort": self.sort,
            "offset": self.offset,
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


def alert_text(alerts: dict[str, Any], currency: str) -> str:
    """A card's alert in its short form: "Moves more than 10%", "Below $150.00", "Above $45.00 ·
    below $30.00"."""
    parts = []
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
        return "<span class='none'>No alert</span>"
    return ui.label(alert_text(alerts, v.get("currency") or "usd"), "blue", "bell")


def where_text(v: dict[str, Any]) -> str:
    where = " ".join(p for p in ((v.get("set") or "").upper(), v.get("collector_number") or "") if p)
    return f"{where or 'any printing'} · {FINISH_WORDS.get(v.get('finish') or '', v.get('finish') or '')}"


def _priced(hist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [h for h in hist if h.get("price") is not None]


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
# the last column holds Details and the row's ⋮ (static/menus.js adds it) side by side
TABLE_COLS = "56px minmax(0,1fr) 7rem 10.5rem 120px minmax(0,10rem) 8.5rem"
TABLE_MD = "56px minmax(0,1fr) 8.5rem"
TABLE_SM = "48px minmax(0,1fr) auto"


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
        f"<div class='tr{' open' if is_open else ''}' id='wr-{esc(key)}'>{thumb}"
        f"<span class='title'><span class='t'>{title}</span><span class='deck'>{esc(line)}</span>"
        f"<span class='sub'>{sub}</span></span>"
        f"<span class='px md-hide'>{esc(px)}<small>{esc(when)}</small></span>"
        f"<span class='md-hide chgs'>{changes_html(view)}</span>"
        f"<span class='md-hide spark'>{spark_html(short, cur, what)}</span>"
        f"<span class='md-hide'>{alert}</span>"
        f"<span class='act'><a class='btn btn-plain more' href='{href}' role='button' "
        f"aria-expanded='{'true' if is_open else 'false'}' aria-controls='wx-{esc(key)}' "
        f"aria-label='{esc(more_label)}'>{'Close' if is_open else 'Details'}</a></span></div>"
        f"<div class='wx' id='wx-{esc(key)}'{'' if is_open else ' hidden'}>{details}</div>"
    )


def _wx_chart(view: dict[str, Any], today: str, what: str, heading: str, *, extra: str = "") -> str:
    cur = view.get("currency") or "usd"
    hist = view.get("history") or []
    full_days = len(_priced(hist))
    short = hist[-SHORT_DAYS:]
    return (
        f"<p class='wx-chg'>{changes_html(view)}</p>"
        f"<div class='wx-chart'{extra}><div class='wx-h'><h3>{esc(heading)}</h3>"
        f"{_range_buttons(full_days)}</div>"
        f"<div class='wx-draw'>{chart_html(short, cur, what, today)}</div>"
        f"<div class='wx-table'>{table_html(short, cur, what)}</div></div>"
    )


def card_row(v: dict[str, Any], uid: str | None, csrf_in: str, today: str, *, query: Query) -> str:
    wid = v["watch_id"]
    name = v["name"]
    alerts = v.get("alerts") or {}
    cur = v.get("currency") or "usd"
    fields = "".join(
        f"<label for='{k[0]}-{esc(wid)}'>{label}<input id='{k[0]}-{esc(wid)}' name='alert_{k}' "
        f"inputmode='decimal' value='{esc(alerts.get(k, ''))}'></label>"
        for k, label in (("pct", "Moves more than (%)"), ("above", "Rises above"), ("below", "Falls below"))
    )
    on = " " + ui.label("On", "blue", "bell") if alerts else ""
    details = _wx_chart(
        v,
        today,
        name,
        "Price, last 30 days",
        extra=f" data-card='{esc(name)}' data-watch='{esc(wid)}' data-currency='{esc(cur)}'",
    ) + (
        f"<form method='post' class='wx-alert'>{csrf_in}<input type='hidden' name='action' value='alerts'>"
        f"<input type='hidden' name='watch_id' value='{esc(wid)}'><h3>Price alert{on}</h3>"
        f"<div class='af'>{fields}</div><p class='ui-hint'>{ALERT_HINT}</p>"
        "<div class='ui-actions'><button class='btn-plain' type='submit'>Save alert</button>"
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
        thumb=_thumb(name, uid),
        title=title,
        line=where_text(v),
        view=v,
        today=today,
        what=name,
        alert=alert_label(v),
        is_open=query.open == wid,
        details=details,
        more_label=f"{name}: chart and alert",
        query=query,
    )


def deck_row(v: dict[str, Any], csrf_in: str, today: str, *, query: Query) -> str:
    did = str(v["deck_id"])
    name = v.get("name") or "Unnamed deck"
    full = [[h["day"], h["price"]] for h in _priced(v.get("history") or [])]
    read = ""
    if v.get("refreshed_at"):
        read = f"Read again once a day; last read {ui.when_text(v['refreshed_at'])}."
    details = _wx_chart(
        v,
        today,
        f"Value of {name}",
        "Value, last 30 days",
        extra=f" data-full='{esc(json.dumps(full))}' data-currency='usd'",
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
    seven = v.get("change_7d_pct")
    return {
        "": True,
        "alert": bool(v.get("alerts")),
        "up": seven is not None and seven > 0,
        "down": seven is not None and seven < 0,
    }[chip]


def shown_cards(cards: list[dict[str, Any]], query: Query) -> list[dict[str, Any]]:
    q = query.q.casefold()
    out = [
        v
        for v in cards
        if in_chip(v, query.show)
        and (not q or q in str(v.get("name") or "").casefold())
        and (not query.finish or v.get("finish") == query.finish)
    ]
    if query.sort == "price":
        out.sort(key=lambda v: -(v.get("price") or 0))
    elif query.sort in ("d1", "d7", "d30"):
        key = f"change_{query.sort[1:]}d_pct"
        out.sort(key=lambda v: -abs(v.get(key) or 0))
    return out


# -- widgets -------------------------------------------------------------------------------------
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
    moved = v.get("change_1d_pct")
    if alerts.get("pct") is not None and moved is not None and abs(moved) >= alerts["pct"]:
        return f"{v['name']} moved {abs(moved):g}% in a day"
    return ""


def widgets_html(data: dict[str, Any], *, checked: str) -> str:
    cards, decks, lim = data["cards"], data["decks"], data["limits"]
    usd = [v["price"] for v in cards if v.get("price") is not None and v.get("currency") == "usd"]
    total = f"Together {money(sum(usd))} today" if usd else "No prices yet"
    out = [
        ui.widget(
            "Cards watched",
            "stats",
            len(cards),
            unit=f"of {lim['cards']}",
            detail=total,
            actions=[ui.button_link("Watch a card", "#w-add")],
        )
    ]
    with_alert = [v for v in cards if v.get("alerts")]
    due = [d for d in (due_line(v) for v in with_alert) if d]
    if with_alert:
        det = (due[0] + f" · checked {checked}") if due else f"Nothing past an alert · checked {checked}"
        out.append(
            ui.widget(
                "Alerts",
                "bell",
                len(with_alert),
                unit="card has one" if len(with_alert) == 1 else "cards have one",
                detail=det,
                actions=[ui.button_link("Show them", "/watchlist?show=alert#w-cards")],
            )
        )
    else:
        out.append(
            ui.widget("Alerts", "bell", 0, unit="cards have one", detail="Set one in a card's Details.")
        )
    moved = [v for v in cards if v.get("change_7d_pct")]
    if moved:
        top = max(moved, key=lambda v: abs(v["change_7d_pct"]))
        c = top["change_7d_pct"]
        other = [v for v in moved if (v["change_7d_pct"] > 0) != (c > 0)]
        det = ""
        if other:
            o = max(other, key=lambda v: abs(v["change_7d_pct"]))
            oc = o["change_7d_pct"]
            det = f"{'Up' if oc > 0 else 'Down'} most: {o['name']}, {'up' if oc > 0 else 'down'} {abs(oc):g}%"
        out.append(
            ui.widget(
                "Biggest move, 7 days",
                "stats",
                f"{'↑' if c > 0 else '↓'}{abs(c):g}%",
                unit=top["name"],
                detail=det,
                actions=[ui.button_link("Sort by change", "/watchlist?sort=d7#w-cards")],
            )
        )
    else:
        out.append(
            ui.widget("Biggest move, 7 days", "stats", "–", detail="Moves show after a week of prices.")
        )
    values = [d["price"] for d in decks if d.get("price") is not None]
    det = f"Together {money(sum(values))}" if values else "No values yet"
    moving = [d for d in decks if d.get("change_30d_pct")]
    if moving:
        m = max(moving, key=lambda d: abs(d["change_30d_pct"]))
        mc = m["change_30d_pct"]
        det += f" · {m.get('name') or 'A deck'} {'up' if mc > 0 else 'down'} {abs(mc):g}% in 30 days"
    out.append(
        ui.widget(
            "Decks watched",
            "decks",
            len(decks),
            unit=f"of {lim['decks']}",
            detail=det,
            actions=[ui.button_link("Watch a deck", "#w-decks")],
        )
    )
    return ui.widgets(out)


# -- the page ---------------------------------------------------------------------------------------
def watchlist_page_html(
    data: dict[str, Any],
    ids: dict[str, str | None],
    csrf: str | None,
    query: Query,
    *,
    ok: str = "",
    err: str = "",
    now: float | None = None,
) -> str:
    """The page's body from ``PriceService.overview`` (``data``, a year of history) and each
    watch's Scryfall id (``ids``, for its picture)."""
    now = time.time() if now is None else now
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    csrf_in = f"<input type='hidden' name='csrf' value='{esc(csrf or '')}'>"
    notice = ""
    if ok in OK_NOTES:
        notice = f"<div class='notice ok' role='status'>{esc(OK_NOTES[ok])}</div>"
    if err:
        notice = f"<div class='notice error' role='alert'>{esc(err)}</div>"
    lim = data["limits"]
    at = data["checked_daily_at_utc"]
    days = [v["day"] for v in data["cards"] + data["decks"] if v.get("day")]
    last = max(days) if days else ""
    checked = f"{day_text(last, today)} at {at} UTC" if last else ""
    intro = (
        "Prices come from Scryfall (TCGplayer in dollars, Cardmarket in euros), checked "
        f"once a day at {at} UTC for the cards and decks you watch here. "
        f"You may watch up to {lim['cards']} cards and {lim['decks']} decks. Your assistant can do the same "
        "with its watch tools."
    )
    add = (
        "<form method='post' class='ui-card w-add' id='w-add' aria-labelledby='w-add-h'>"
        f"{csrf_in}<input type='hidden' name='action' value='add_card'>"
        "<h2 id='w-add-h'>Watch a card</h2><div class='w-addrow'>"
        "<label class='wide' for='w-name'>Card name<input id='w-name' name='name' maxlength='150' "
        "autocomplete='off' data-suggest='cards'></label>"
        "<label for='w-set'>Set code (optional)<input id='w-set' name='set_code' maxlength='6' "
        "autocomplete='off'></label>"
        "<label for='w-cn'>Number (optional)<input id='w-cn' name='collector_number' maxlength='10' "
        "autocomplete='off'></label>"
        "<label for='w-fin'>Finish<select id='w-fin' name='finish'>"
        + "".join(f"<option value='{k}'>{w}</option>" for k, w in FINISH_WORDS.items())
        + "</select></label><button class='btn-primary' type='submit'>Watch card</button></div>"
        f"<p class='ui-hint'>{CARD_HINT} {SEARCH_HINT}</p></form>"
    )
    cards = data["cards"]
    shown = shown_cards(cards, query)
    total = len(shown)
    offset = query.offset if query.offset < max(1, total) else 0
    page_rows = shown[offset : offset + PAGE]
    chips = ui.chips(
        [
            ui.Chip(
                word,
                sum(1 for v in cards if in_chip(v, key)),
                query.href(show=key, offset=0),
                key == query.show,
            )
            for key, word in CHIPS
        ]
    )
    tools = ui.toolbar(
        "/watchlist",
        search_label="Search your watchlist",
        placeholder="Search card name",
        q=query.q,
        filters=[
            ui.filter_select("finish", "Finish", [("", "Any finish"), *FINISH_WORDS.items()], query.finish),
            ui.filter_select("sort", "Sort by", list(SORTS), query.sort),
        ],
        keep={"show": query.show},
    )
    head = ui.table_header(
        [
            ui.Column(""),
            ui.Column("Card"),
            ui.Column("Price", "md-hide"),
            ui.Column("Change", "md-hide"),
            ui.Column("30 days", "md-hide"),
            ui.Column("Alert", "md-hide"),
            ui.Column(""),
        ]
    )
    if page_rows:
        body = "".join(card_row(v, ids.get(v["watch_id"]), csrf_in, today, query=query) for v in page_rows)
    elif cards:
        body = "<p class='ui-empty'>No cards match. Clear the search or the filters to see them all.</p>"
    else:
        body = "<p class='ui-empty'>No cards watched yet.</p>"
    pager = ui.pager(
        offset,
        total,
        newer_href=query.href(offset=max(0, offset - PAGE)) if offset else None,
        older_href=query.href(offset=offset + PAGE) if offset + PAGE < total else None,
    )
    card_list = (
        "<div id='w-cards'>"
        + ui.list_head("Cards", len(cards), f"Last check {checked}" if checked else "")
        + "</div>"
        + chips
        + tools
        + ui.card_size_control(cls="cardsize w-size")  # R-175: the pictures follow the one card size
        + ui.table_open(TABLE_COLS, TABLE_MD, TABLE_SM, cls="w-t", label="Watched cards")
        + head
        + body
        + "</div>"
        + pager
    )
    decks = data["decks"]
    deck_head = ui.table_header(
        [
            ui.Column(""),
            ui.Column("Deck"),
            ui.Column("Value", "md-hide"),
            ui.Column("Change", "md-hide"),
            ui.Column("30 days", "md-hide"),
            ui.Column("", "md-hide"),
            ui.Column(""),
        ]
    )
    deck_body = (
        "".join(deck_row(v, csrf_in, today, query=query) for v in decks)
        if decks
        else "<p class='ui-empty'>No decks watched yet.</p>"
    )
    deck_list = (
        f"<div id='w-decks'>{ui.list_head('Decks', len(decks))}</div>"
        f"<form method='post' class='w-deckadd' aria-label='Watch a deck'>{csrf_in}"
        "<input type='hidden' name='action' value='add_deck'>"
        "<label class='wide' for='w-deck'>Archidekt deck link or number"
        "<input id='w-deck' name='deck' maxlength='200' autocomplete='off' required></label>"
        "<button class='btn-plain' type='submit'>Watch deck</button></form>"
        f"<p class='ui-hint'>{DECK_HINT}</p>"
        + ui.table_open(TABLE_COLS, TABLE_MD, TABLE_SM, cls="w-t", label="Watched decks")
        + deck_head
        + deck_body
        + "</div>"
        + ui.pager(0, len(decks), newer_href=None, older_href=None, per_page=max(PAGE, len(decks)))
    )
    return (
        "<div class='watch-page gb-dash'>"
        + ui.page_head("Watchlist", intro)
        + notice
        + widgets_html(data, checked=checked or "daily")
        + add
        + card_list
        + deck_list
        + "</div>"
    )


WATCH_PAGE_CSS = (
    PHONE_WIDGETS_CSS
    + """
/* the Watchlist */
@media (min-width:900px){.watch-page .w-t .chgs{display:flex}}
.watch-page .w-add{margin:0 0 1.25rem}
.watch-page .w-add h2{font-size:1.1rem;margin:0 0 .5rem}
.watch-page .w-addrow{display:grid;gap:.6rem 1rem;align-items:end;
  grid-template-columns:minmax(0,2fr) repeat(3,minmax(0,1fr)) auto}
.watch-page .w-addrow label,.watch-page .w-deckadd label{display:flex;flex-direction:column;gap:.25rem;
  margin:0;
  min-width:0;font-weight:700}
.watch-page .w-addrow input,.watch-page .w-addrow select:not(.msel-native),
.watch-page .w-deckadd input{width:100%;margin:0}
.watch-page .w-addrow button,.watch-page .w-deckadd button{margin:0}
@media (max-width:899.98px){.watch-page .w-addrow{grid-template-columns:1fr 1fr}
  .watch-page .w-addrow .wide{grid-column:1 / -1}}
@media (max-width:599.98px){.watch-page .w-addrow{grid-template-columns:1fr}}
.watch-page .w-add .ui-hint{margin:.5rem 0 0}
.watch-page .w-deckadd{display:flex;flex-wrap:wrap;gap:.6rem 1rem;align-items:end;margin:0 0 .25rem}
.watch-page .w-deckadd .wide{flex:1 1 18rem}
.watch-page .ui-empty{padding:1rem;margin:0;color:var(--text-muted)}
.watch-page .w-t{margin:0 0 .5rem}
.watch-page .w-t .thumb{display:flex;align-items:center;justify-content:center;width:48px;height:67px;
  border-radius:4px;overflow:hidden;background:var(--surface-2);font-size:.62rem;line-height:1.1;
  text-align:center;
  color:var(--text-muted);padding:2px;overflow-wrap:anywhere}
.watch-page .w-t .thumb img{width:100%;height:100%;object-fit:cover}
.watch-page .w-t .thumb.deckart{height:48px;background:var(--orange-tint);color:var(--orange-text)}
.watch-page .w-t .title .t a{font-weight:700}
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
.watch-page .none{color:var(--text-muted);font-size:.86rem}
.watch-page .w-t .spark .tspark{display:block;width:100%;height:36px;color:var(--orange-text)}
.watch-page .tspark polyline,.watch-page .pcl polyline{fill:none;stroke:currentColor;stroke-width:2;
  stroke-linejoin:round;stroke-linecap:round}
.watch-page .w-t .tr.open{background:var(--orange-tint)}
.watch-page .w-t .act{display:inline-flex;align-items:center;gap:.4rem;flex-wrap:nowrap;white-space:nowrap}
.watch-page .w-t .act .more{min-height:var(--ctl);display:inline-flex;align-items:center;margin:0}
.watch-page .w-size{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:.5rem;
  margin:0 0 .6rem;max-width:100%}
.watch-page .w-size .sizeseg{max-width:100%}
.watch-page .w-size .lbl{color:var(--text-muted);font-size:.9rem}
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
@media (pointer:coarse){.watch-page .seg button{min-height:44px}}
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
.watch-page .wx-alert .af{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,8rem),1fr));
  gap:.5rem}
.watch-page .wx-alert label{display:flex;flex-direction:column;gap:.25rem;margin:0;min-width:0;
  font-weight:700}
.watch-page .wx-alert input{width:100%;margin:0}
.watch-page .wx .ui-actions{display:flex;flex-wrap:wrap;gap:.5rem;margin:.5rem 0 0}
.watch-page .wx .ui-actions > *{margin:0}
.watch-page .wx form.rm{margin:0;display:contents}
"""
)
