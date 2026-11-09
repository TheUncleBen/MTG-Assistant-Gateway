"""Binder (0.7.47 One Binder, the owner's approved mockup round 1): one page for every card the
member owns or wants, in place of the three-part switch (Cards, Lists, Watchlist).

``/binder`` is a grouped library (All cards, Owned, Want, Price alerts, the member's lists, and
More: Archidekt collection, Decks I watch, Watch settings, Archidekt lists) beside one list of
rows. On a phone the library is the page until a row of it is picked (``show`` or ``list`` in
the address); then the list is the page, with "‹ Binder" back. When the page is about 56 em wide
in its own text size (900 px at normal text size), both show side by side (split view).
Every card is one row (binder_library.py): picture, name, printing, price and its move over a
week, and chips for the copies owned, wanted, the alert's prices and the list it is in. A tap opens
the shared card viewer with this page's actions. Select ticks rows for the bar: Price alerts,
Add to list, Watch, Stop watching, Tag on Archidekt and Remove from binder; each change says what
it did and offers Undo (the Archidekt ones through the Binder's own bulk change and its Undo).

``/binder/settings`` (Watch settings) holds the member's price alert defaults, shown in dollars
for a sample card. Cards that follow the defaults change with them (prices.apply_defaults).

Watching never writes to Archidekt; Tag on Archidekt and Remove from binder post to the Binder's
own form (/collection, action=bulk), which writes to Archidekt as the Cards page does.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from . import account_hub, binder_library, prefs, prices, ui
from .decks import DeckError
from .pages import _csrf, browser_session, csrf_form_ok, login_redirect_here, read_limited
from .prices import money
from .theme import esc, icon, plural, render, stylesheet_link

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

PATH = "/binder"
SETTINGS = "/binder/settings"
TITLE = "Binder"
UNDO_TTL = 15 * 60  # seconds an Undo of a bulk change stays offered
MAX_FORM = 600_000  # a bulk form names up to prices.MAX_BULK cards
NO_STORE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
SHOW_WORDS = {"all": "All cards", "owned": "Owned", "want": "Want", "alerts": "Price alerts"}
SORT_WORDS = {"price": "Price", "name": "Name", "added": "Recently added", "move": "Biggest move"}
FINISH_WORDS = {"nonfoil": "", "foil": "Foil", "etched": "Etched"}
OK_WORDS = {
    "alerts": "Price alerts set on {n}.",
    "defaults": "{n} now use your defaults.",
    "list": "Moved {n} to {l}.",
    "nolist": "Took {n} out of their lists.",
    "watch": "Watching {n}.",
    "unwatch": "Stopped watching {n}.",
    "undone": "Undone.",
    "newlist": "List {l} made.",
    "renamed": "List renamed to {l}.",
    "deleted": "List {l} deleted. Its cards stay in your binder and watchlist.",
    "settings": "Watch settings saved. {n} follow them.",
}

_undos: dict[str, tuple[str, float, str, Any]] = {}


def _keep_undo(sub: str, kind: str, payload: Any) -> str:
    t = time.time()
    for k in [k for k, (_s, at, _k, _p) in _undos.items() if t - at > UNDO_TTL]:
        _undos.pop(k, None)
    while len(_undos) >= 200:
        _undos.pop(next(iter(_undos)))
    token = secrets.token_urlsafe(16)
    _undos[token] = (sub, t, kind, payload)
    return token


def _take_undo(sub: str, token: str) -> tuple[str, Any] | None:
    hit = _undos.pop(token or "", None)
    if hit is None or hit[0] != sub or time.time() - hit[1] > UNDO_TTL:
        return None
    return hit[2], hit[3]


class View:
    """A Binder address: what the list shows (``show``, ``list``), its filters and order."""

    def __init__(self, qp: Any) -> None:
        self.show = qp.get("show") if qp.get("show") in binder_library.SHOWS else "all"
        raw_list = qp.get("list")
        self.list = prices.clean_list_name(raw_list) if raw_list else None
        self.q = (qp.get("q") or "").strip()[:80]
        self.tag = (qp.get("tag") or "").strip()[:80]
        self.sort = qp.get("sort") if qp.get("sort") in binder_library.SORTS else "price"
        self.desc = qp.get("dir") != "asc"
        # the phone shows the list once the member picked something from the library
        self.picked = any(qp.get(k) for k in ("show", "list", "q", "tag"))

    def href(self, **change: Any) -> str:
        v = {
            "show": self.show,
            "list": self.list,
            "q": self.q,
            "tag": self.tag,
            "sort": self.sort,
            "dir": "" if self.desc else "asc",
        }
        v.update(change)
        q = {k: x for k, x in v.items() if x not in (None, "")}
        if q.get("show") == "all" and len(q) > 1 and ("list" in q or "q" in q or "tag" in q):
            q.pop("show")
        if q.get("sort") == "price":
            q.pop("sort")
        return PATH + ("?" + urlencode(q) if q else "")

    def title(self) -> str:
        if self.list is not None:
            return self.list
        if self.q:
            return f"“{self.q}”"
        return SHOW_WORDS[self.show]


# -- drawing ------------------------------------------------------------------------------------------


def _row_label(
    href: str, icon_name: str, label: str, value: str = "", sub: str = "", on: bool = False
) -> str:
    small = f"<small>{esc(sub)}</small>" if sub else ""
    cur = " aria-current='page'" if on else ""
    return (
        f"<a class='yrow{' on' if on else ''}' href='{esc(href)}'{cur}>{icon(icon_name)}"
        f"<span class='lbl'>{esc(label)}{small}</span><span class='val'>{esc(value)}</span>"
        f"<span class='ch'>{icon('chev')}</span></a>"
    )


def library_html(
    view: View,
    counts: dict[str, int],
    lists: list[dict[str, Any]],
    per_list: dict[str, dict[str, int]],
    *,
    decks: int,
    defaults: dict[str, float],
) -> str:
    def on(show: str) -> bool:
        return view.list is None and not view.q and not view.tag and view.show == show

    cards = "".join(
        _row_label(
            view.href(show=s, list=None, q="", tag=""), ic, SHOW_WORDS[s], f"{counts[s]:,}", sub, on(s)
        )
        for s, ic, sub in (
            ("all", "layers", ""),
            ("owned", "collection", "Your Archidekt collection"),
            ("want", "eye", "Cards you watch for a price"),
            ("alerts", "bell", "Past their alert price now"),
        )
    )
    mine = []
    for wl in lists:
        c = per_list.get(wl["name"], {"want": 0, "owned": 0})
        bits = [f"{c['want']:,} want" if c["want"] else "", f"{c['owned']:,} owned" if c["owned"] else ""]
        mine.append(
            _row_label(
                view.href(list=wl["name"], show="all", q="", tag=""),
                "list",
                wl["name"],
                f"{wl['count']:,}",
                " · ".join(b for b in bits if b) or "Empty",
                view.list == wl["name"],
            )
        )
    mine.append(
        f"<a class='yrow add' href='{PATH}?box=newlist' {ui.box_open_attr('bn-newlist')}>{icon('plus')}"
        "<span class='lbl'>New list…</span></a>"
    )
    words = _defaults_words(defaults)
    more = (
        _row_label("/collection", "collection", "Archidekt collection", "", "Add cards, condition, language")
        + _row_label(
            "/watchlist?show=decks", "decks", "Decks I watch", f"{decks:,}", "Whole-deck value each day"
        )
        + _row_label(SETTINGS, "settings", "Watch settings", "", words)
        + _row_label(
            "/collection/lists",
            "folder",
            "Archidekt lists",
            "",
            "Category templates, color tags, pinned and custom cards, binder tags",
        )
    )
    return (
        "<div class='yhub bn-lib'>"
        f"<form class='bn-find' method='get' action='{PATH}' role='search'>"
        f"<label class='sr-only' for='bn-q'>Search your binder</label>{icon('search')}"
        f"<input id='bn-q' name='q' type='search' placeholder='Search your binder' value='{esc(view.q)}' "
        "autocomplete='off' enterkeyhint='search'></form>"
        f"<section class='ygrp'><h2>Cards</h2><div class='rows'>{cards}</div></section>"
        f"<section class='ygrp'><h2>My lists</h2><div class='rows'>{''.join(mine)}</div></section>"
        f"<section class='ygrp'><h2>More</h2><div class='rows'>{more}</div></section>"
        "</div>"
    )


def _defaults_words(d: dict[str, float]) -> str:
    parts = []
    if d.get("drop") is not None:
        parts.append(f"{d['drop']:g}% under")
    if d.get("rise") is not None:
        parts.append(f"{d['rise']:g}% over")
    if d.get("pct") is not None:
        parts.append(f"a day's move over {d['pct']:g}%")
    return "Alerts: " + (" · ".join(parts) if parts else "none")


def _chips(r: dict[str, Any]) -> str:
    out = []
    if r["own"]:
        out.append(f"<span class='chip own'>{icon('check')}Own {r['own']:,}</span>")
    elif r.get("own_any"):
        out.append(f"<span class='chip own'>{icon('check')}Own {r['own_any']:,} (any printing)</span>")
    if r["watch_id"]:
        if r["past"]:
            out.append(f"<span class='chip hit'>{icon('bell')}Past its alert</span>")
        else:
            out.append(f"<span class='chip want'>{icon('eye')}Want</span>")
        band = []
        if r["under"] is not None:
            band.append(f"under {money(r['under'], r['currency'])}")
        if r["over"] is not None:
            band.append(f"over {money(r['over'], r['currency'])}")
        pct = (r.get("alerts") or {}).get("pct")
        if pct is not None:
            band.append(f"a day's move over {pct:g}%")
        if band:
            out.append(f"<span class='band'>Alert {esc(' · '.join(band))}</span>")
    if r["list"]:
        out.append(f"<span class='chip lst'>{esc(r['list'])}</span>")
    for t in r["tags"][:3]:
        out.append(f"<span class='chip tg'>{icon('tag')}{esc(t)}</span>")
    return "".join(out)


def _price(r: dict[str, Any]) -> str:
    if r["price"] is None:
        return "<span class='pr none'>No price yet</span>"
    ch = r["change_7d_pct"]
    if ch is None or ch == 0:
        move = "<small class='flat'>Same as last week</small>" if ch == 0 else ""
    else:
        cls = "dn" if ch < 0 else "up"
        move = f"<small class='{cls}'>{'−' if ch < 0 else '+'}{abs(ch):g}% this week</small>"
    return f"<span class='pr'><b>{money(r['price'], r['currency'])}</b>{move}</span>"


def row_html(r: dict[str, Any]) -> str:
    printing = " · ".join(
        p
        for p in (
            "Any printing" if r["any_printing"] else " ".join(x for x in (r["set"], r["cn"]) if x),
            FINISH_WORDS.get(r["finish"], ""),
        )
        if p
    )
    data = {
        "key": r["key"],
        "wid": r["watch_id"] or "",
        "own": str(r["own"]),
        "sid": r["scryfall_id"],
        "finish": r["finish"],
        "card": r["name"],
        "set": " ".join(x for x in (r["set"], r["cn"]) if x),
        "cn": r["cn"],
        "img": r["img"] or "",
        "list": r["list"],
        "uses": "1" if r["uses_defaults"] else "",
    }
    attrs = " ".join(f"data-{k}='{esc(v)}'" for k, v in data.items())
    rids = esc(json.dumps(r["rids"], separators=(",", ":"))) if r["rids"] else ""
    pic = (
        f"<img class='pic' src='{esc(r['img'])}' alt='' loading='lazy' decoding='async'>"
        if r["img"]
        else "<span class='pic ph' aria-hidden='true'></span>"
    )
    label = esc(r["name"])
    return (
        f"<li class='bn-row' {attrs}{f' data-rids={chr(39)}{rids}{chr(39)}' if rids else ''}>"
        f"<label class='tick'><input type='checkbox' class='bn-tick' aria-label='Select {label}'></label>"
        f"<button type='button' class='bn-open' aria-label='Open {label}'>{pic}"
        f"<span class='nm'>{label}<small>{esc(printing)}</small></span>{_price(r)}</button>"
        f"<div class='meta'>{_chips(r)}</div></li>"
    )


def _with(href: str, **q: Any) -> str:
    return (
        href
        + ("&" if "?" in href else "?")
        + urlencode({("from" if k == "start" else k): v for k, v in q.items()})
    )


def main_html(
    view: View,
    rows: list[dict[str, Any]],
    seg: dict[str, int],
    *,
    total_value: float,
    start: int,
    tags: list[str],
    notice: str,
    csrf: str,
) -> str:
    segs = "".join(
        f"<a href='{esc(view.href(show=s))}'{' aria-current=page' if view.show == s else ''}>"
        f"{word}<span class='cn'>{seg[s]:,}</span></a>"
        for s, word in (("all", "All"), ("owned", "Owned"), ("want", "Want"))
    )
    page = rows[start : start + binder_library.PAGE]
    items = "".join(row_html(r) for r in page)
    left = len(rows) - start - len(page)
    more = (
        f"<p class='bn-more'><a class='btn' href='{esc(_with(view.href(), start=start + len(page)))}' "
        f"data-from='{start + len(page)}'>Load {min(left, binder_library.PAGE):,} more</a></p>"
        if left > 0
        else ""
    )
    sorts = "".join(
        f"<option value='{k}'{' selected' if k == view.sort else ''}>{w}</option>"
        for k, w in SORT_WORDS.items()
    )
    tag_opts = "".join(
        f"<option value='{esc(t)}'{' selected' if t == view.tag else ''}>{esc(t)}</option>" for t in tags
    )
    list_menu = ""
    if view.list:
        list_menu = (
            "<details class='dd bn-listmenu'><summary class='btn' aria-haspopup='menu'>"
            f"{icon('more')}<span>List</span></summary><div class='menu'>"
            f"<a href='{PATH}?box=rename' {ui.box_open_attr('bn-rename')}>{icon('edit')} Rename list…</a>"
            f"<a href='{PATH}?box=delete' class='danger' {ui.box_open_attr('bn-delete')}>{icon('trash')} "
            "Delete list…</a></div></details>"
        )
    empty = ""
    if not rows:
        empty = (
            "<div class='bn-empty'><p>Nothing here yet.</p><p class='ui-hint'>"
            + (
                "Add cards to this list from any other view: Select them, then Add to list."
                if view.list
                else "Watch a card from Card search or a deck, or add cards to your binder."
            )
            + "</p></div>"
        )
    hidden = "".join(
        f"<input type='hidden' name='{k}' value='{esc(v)}'>"
        for k, v in (("show", view.show), ("list", view.list or ""), ("tag", view.tag))
        if v and not (k == "show" and v == "all")
    )
    return (
        "<section class='bn-main' aria-labelledby='bn-title'>"
        f"<a class='bn-back' href='{PATH}'>{icon('back')}Binder</a>"
        f"<div class='bn-head'><h2 id='bn-title'>{esc(view.title())}</h2>{list_menu}</div>"
        + notice
        + f"<nav class='hubseg bn-seg' aria-label='Show'>{segs}</nav>"
        f"<form class='bn-tools' method='get' action='{PATH}'>{hidden}"
        f"<label class='sr-only' for='bn-sort'>Order</label>"
        f"<select id='bn-sort' name='sort' data-autosubmit>{sorts}</select>"
        + (
            f"<label class='sr-only' for='bn-tag'>Archidekt tag</label><select id='bn-tag' name='tag' "
            f"data-autosubmit><option value=''>Any tag</option>{tag_opts}</select>"
            if tags
            else ""
        )
        + "<noscript><button class='btn' type='submit'>Show</button></noscript>"
        f"<button type='button' class='btn bn-select' aria-pressed='false'>{icon('check')}Select</button>"
        "</form>"
        + ui.selection_bar(
            "0 selected",
            [
                "<button type='button' class='btn'"
                f" data-bulk='alerts'>{icon('bell')}<span>Price alerts</span></button>",
                f"<button type='button' class='btn' data-bulk='list'>{icon('list')}<span>Add to list</span>"
                "</button>",
                f"<button type='button' class='btn' data-bulk='watch'>{icon('eye')}<span>Watch</span>"
                "</button>",
            ],
            label="Selected cards",
            hidden=True,
            cancel_cls="bn-cancel",
            more=[
                f"<button type='button' data-bulk='unwatch'>{icon('eye-off')} Stop watching</button>",
                f"<button type='button' data-bulk='tag'>{icon('tag')} Tag on Archidekt…</button>",
                "<button type='button' class='danger'"
                f" data-bulk='remove'>{icon('trash')} Remove from binder…</button>",
                "<button type='button' data-bulk='all'>Select all in this view</button>",
            ],
        )
        + f"<p class='bn-sum'><b>{plural(len(rows), 'card')}</b>"
        + (f"<span>{money(total_value, 'usd')} today</span>" if total_value else "")
        + "</p>"
        + (f"<ul class='bn-rows' aria-label='Cards'>{items}</ul>" if rows else empty)
        + more
        + "</section>"
    )


def _box_form(action: str, inner: str, csrf: str, back: str, *, post_to: str = PATH) -> str:
    return (
        f"<form id='bn-{action}-f' method='post' action='{post_to}' class='bn-form' data-action='{action}'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='action' value='{action}'>"
        f"<input type='hidden' name='back' value='{esc(back)}'>"
        "<input type='hidden' name='keys' value=''>"
        f"{inner}</form>"
    )


def boxes_html(
    view: View, lists: list[dict[str, Any]], defaults: dict[str, float], csrf: str, tags: list[str]
) -> str:
    back = view.href()
    dwords = _defaults_words(defaults)[len("Alerts: ") :]
    list_opts = "<option value=''>Not in a list</option>" + "".join(
        f"<option value='{esc(x['name'])}'>{esc(x['name'])}</option>" for x in lists
    )

    def num(name: str, label: str, unit: str, value: str = "", hint: str = "") -> str:
        pre, post = ("$", "") if unit == "$" else ("", unit)
        return (
            f"<label class='bn-num'><span>{label}</span><span class='in'>{pre}"
            f"<input name='{name}' inputmode='decimal' value='{esc(value)}' placeholder='Off' "
            f"aria-describedby='{name}-u'><span id='{name}-u'>{post}</span></span>{hint}</label>"
        )

    alerts = ui.box(
        "bn-alerts",
        "Price alerts",
        _box_form(
            "alerts",
            "<p class='bn-count ui-hint'></p><fieldset class='bn-choice'>"
            "<legend class='sr-only'>Which alerts</legend>"
            "<label><input type='radio' name='mode' value='defaults' checked><span>Use my defaults"
            f"<small>{esc(dwords)}</small></span></label>"
            "<label><input type='radio' name='mode' value='own'><span>Set alerts for these cards</span>"
            "</label>"
            "</fieldset><div class='bn-own'>"
            + num("alert_drop", "Under the price when added by", "%", "10")
            + num("alert_rise", "Over the price when added by", "%", "20")
            + num("alert_pct", "A single day's move of more than", "%")
            + "</div><p class='bn-example ui-hint' aria-live='polite'></p>",
            csrf,
            back,
        ),
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Apply", kind="primary", form="bn-alerts-f"),
        ],
        label="Price alerts for the selected cards",
    )
    move = ui.box(
        "bn-list",
        "Add to list",
        _box_form(
            "list",
            "<p class='bn-count ui-hint'></p>"
            "<label for='bn-list-pick'>List</label><select id='bn-list-pick'"
            f" name='list'>{list_opts}</select>"
            "<label for='bn-list-new'>Or a new list</label>"
            f"<input id='bn-list-new' name='new_list' maxlength='{prices.MAX_LIST_NAME}' autocomplete='off'>"
            "<p class='ui-hint'>A card is in one list at a time; adding moves it.</p>",
            csrf,
            back,
        ),
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Add to list", kind="primary", form="bn-list-f"),
        ],
    )
    tag_opts = "".join(f"<option value='{esc(t)}'>{esc(t)}</option>" for t in tags)
    tag = ui.box(
        "bn-tag",
        "Tag on Archidekt",
        (
            "<form id='bn-tag-f' method='post' action='/collection' class='bn-form' data-action='tag'>"
            f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='action'"
            " value='bulk'>"
            "<input type='hidden' name='do' value='addtag'><input type='hidden' name='next'"
            f" value='{esc(back)}'>"
            "<input type='hidden' name='sel' value=''><p class='bn-count ui-hint'></p>"
            + (
                "<label for='bn-tag-pick'>Tag</label><select id='bn-tag-pick'"
                f" name='tags'>{tag_opts}</select>"
                "<p class='ui-hint'>Added to the cards you own;"
                " their other tags stay. Saved to Archidekt.</p>"
                if tags
                else "<p>You have no binder tags yet. Make one under Archidekt lists › Binder tags.</p>"
            )
            + "</form>"
        ),
        actions=[ui.box_button("Cancel", cancel=True)]
        + ([ui.box_button("Add tag", kind="primary", form="bn-tag-f")] if tags else []),
    )
    remove = ui.box(
        "bn-remove",
        "Remove from binder",
        (
            "<form id='bn-remove-f' method='post' action='/collection' class='bn-form' data-action='remove'>"
            f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='action'"
            " value='bulk'>"
            "<input type='hidden' name='do' value='remove'><input type='hidden' name='next'"
            f" value='{esc(back)}'>"
            "<input type='hidden' name='sel' value=''><p class='bn-count'></p>"
            "<p class='ui-hint'>They are removed from your Archidekt collection. Undo puts them back.</p>"
            "</form>"
        ),
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Remove", kind="danger-solid", form="bn-remove-f"),
        ],
    )
    newlist = ui.box(
        "bn-newlist",
        "New list",
        _box_form(
            "newlist",
            f"<label for='bn-new-name'>Name</label><input id='bn-new-name' name='name' required "
            f"maxlength='{prices.MAX_LIST_NAME}' autocomplete='off'>",
            csrf,
            back,
        ),
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Make list", kind="primary", form="bn-newlist-f"),
        ],
    )
    extra = ""
    if view.list:
        extra = ui.box(
            "bn-rename",
            "Rename list",
            _box_form(
                "rename",
                f"<input type='hidden' name='name' value='{esc(view.list)}'>"
                f"<label for='bn-rn'>New name</label><input id='bn-rn' name='new_name' required "
                f"maxlength='{prices.MAX_LIST_NAME}' value='{esc(view.list)}' autocomplete='off'>",
                csrf,
                back,
            ),
            actions=[
                ui.box_button("Cancel", cancel=True),
                ui.box_button("Rename", kind="primary", form="bn-rename-f"),
            ],
        ) + ui.box(
            "bn-delete",
            "Delete list",
            _box_form(
                "delete",
                f"<input type='hidden' name='name' value='{esc(view.list)}'>"
                f"<p>Delete “{esc(view.list)}”? Its cards stay in your binder and watchlist, in no list.</p>",
                csrf,
                PATH,
            ),
            actions=[
                ui.box_button("Cancel", cancel=True),
                ui.box_button("Delete list", kind="danger-solid", form="bn-delete-f"),
            ],
        )
    return alerts + move + tag + remove + newlist + extra


def settings_html(d: dict[str, float], split: tuple[int, int], csrf: str, notice: str) -> str:
    def val(k: str) -> str:
        return f"{d[k]:g}" if d.get(k) is not None else ""

    follow, own = split
    floor_text = "{:.2f}".format(d["floor"]) if d.get("floor") else ""
    ex = []
    for added in (10.0, 2.0):
        under, over = prices.alert_band(added, d.get("drop"), d.get("rise"), d.get("floor"))
        bits = [
            f"under <b>{money(under)}</b>" if under is not None else "",
            f"over <b>{money(over)}</b>" if over is not None else "",
        ]
        said = " or ".join(b for b in bits if b)
        ex.append(f"A card added at <b>{money(added)}</b>: " + (f"alert {said}." if said else "no alert."))
    return (
        "<div class='yhub bn-set'>"
        f"<a class='bn-back' href='{PATH}'>{icon('back')}Binder</a><h1>Watch settings</h1>{notice}"
        f"<form method='post' action='{SETTINGS}'><input type='hidden' name='csrf' value='{esc(csrf)}'>"
        "<section class='ygrp bn-keep'><h2>Price alert defaults</h2><div class='rows bn-fields'>"
        "<p class='ui-hint'>Every card you watch gets these, unless you give it its own.</p>"
        "<label class='bn-num'><span>Under the price when added by</span><span class='in'>"
        "<input name='alert_drop' "
        f"inputmode='decimal' value='{val('drop')}' placeholder='Off'><span>%</span></span></label>"
        "<label class='bn-num'><span>Over the price when added by</span><span class='in'>"
        "<input name='alert_rise' "
        f"inputmode='decimal' value='{val('rise')}' placeholder='Off'><span>%</span></span></label>"
        "<label class='bn-num'><span>A single day's move of more than</span><span class='in'>"
        "<input name='alert_pct' "
        f"inputmode='decimal' value='{val('pct')}' placeholder='Off'><span>%</span></span></label>"
        "<label class='bn-num'><span>Ignore moves smaller than</span>"
        "<span class='in'>$<input name='alert_floor' "
        f"inputmode='decimal' value='{floor_text}' placeholder='0.00'></span></label>"
        f"<p class='bn-example'>{'<br>'.join(ex)}</p>"
        f"<p class='ui-hint'>{plural(follow, 'card')} use these. "
        + (
            f"{plural(own, 'card')} {'has' if own == 1 else 'have'} their own alerts and keep them."
            if own
            else ""
        )
        + "</p></div></section>"
        "<section class='ygrp'><h2>Alerts</h2><div class='rows'>"
        + _row_label(
            "/account/notifications", "bell", "Phone notifications", "", "Set in Account › Notifications"
        )
        + "</div></section>"
        "<div class='bn-save'><button class='btn primary' type='submit'>Save</button></div></form></div>"
    )


CSS = """
.bn{display:grid;gap:1.25rem;align-items:start}
.bn .bn-lib{max-width:none;margin:0}
.bn-find{display:flex;align-items:center;gap:.5rem;min-height:44px;margin:0 0 1rem;padding:0 .75rem;
  border:1px solid var(--control-border);border-radius:10px;background:var(--surface);color:var(--text-muted)}
.bn-find svg.i{width:18px;height:18px;flex:none}
.bn-find input{flex:1;min-width:0;border:0;background:transparent;min-height:42px;padding:0;font:inherit;
  color:var(--text)}
.bn-find input:focus{outline:none}
.bn-find:focus-within{outline:2px solid var(--focus);outline-offset:2px}
.bn .ygrp .yrow small,.bn-set .ygrp .yrow small{display:block;
  color:var(--text-muted);font-size:.82rem;margin-top:.1rem}
.bn .ygrp .yrow .lbl{padding:.5rem 0}
.bn .ygrp .yrow.on{background:var(--orange-tint)}
.bn .ygrp .yrow.add{color:var(--orange-text);font-weight:700}
.bn-head{display:flex;align-items:center;gap:.75rem;flex-wrap:wrap}
.bn-head h2{margin:0;font-size:1.5rem;margin-right:auto;overflow-wrap:break-word;min-width:0}
.bn-main{display:flex;flex-direction:column;gap:.75rem;min-width:0}
.bn-seg{display:flex;width:100%;max-width:28rem}
.bn-seg a{flex:1 1 0;justify-content:center}
.bn-tools{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:0}
.bn-tools select,.bn-tools .msel-btn{flex:1 1 9rem;width:auto;min-width:0;max-width:16rem}
.bn-tools .bn-select{margin-left:auto}
.bn-tools .bn-select[aria-pressed=true]{background:var(--orange-tint);color:var(--orange-text);
  border-color:var(--orange-text)}
.bn-sum{display:flex;flex-wrap:wrap;justify-content:space-between;gap:.25rem 1rem;margin:0;
  color:var(--text-muted);
  font-size:.92rem}
.bn-sum b{color:var(--text)}
.bn-rows{list-style:none;margin:0;padding:0;background:var(--surface);border:1px solid var(--border-soft);
  border-radius:14px;overflow:hidden}
.bn-row{display:grid;grid-template-columns:auto minmax(0,1fr);gap:0 .5rem;padding:.6rem .9rem;
  align-items:center}
.bn-row + .bn-row{border-top:1px solid var(--border-soft)}
.bn-row.sel{background:var(--orange-tint)}
.bn-row .tick{display:none;align-items:center;justify-content:center;width:44px;height:44px;
  margin:-.4rem 0 -.4rem -.6rem;
  grid-row:span 2}
.bn.selecting .bn-row .tick{display:flex}
.bn-row .tick input{width:22px;height:22px;accent-color:var(--orange-ctl)}
.bn:not(.selecting) .bn-row{grid-template-columns:minmax(0,1fr)}
.bn-open{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:.2rem .75rem;align-items:center;
  width:100%;
  min-height:44px;margin:0;padding:0;border:0;background:none;color:var(--text);font:inherit;text-align:left;cursor:pointer}
.bn-open:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:6px}
.bn-open .pic{width:2.6rem;aspect-ratio:63/88;height:auto;border-radius:4px;object-fit:cover;
  background:var(--surface-2)}
.bn-open .nm{font-weight:700;min-width:0;overflow-wrap:break-word}
.bn-open .nm small{display:block;font-weight:400;color:var(--text-muted);font-size:.85rem;margin-top:.1rem}
.bn-open .pr{display:flex;flex-direction:column;align-items:flex-end;font-variant-numeric:tabular-nums;
  white-space:nowrap}
.bn-open .pr small{font-size:.8rem;font-weight:700}
.bn-open .pr.none{color:var(--text-muted);font-size:.85rem;white-space:normal;text-align:right}

  .bn-open .dn{color:var(--green-text)} .bn-open .up{color:var(--red-text)}
  .bn-open .flat{color:var(--text-muted);font-weight:400}
.bn-row .meta{display:flex;flex-wrap:wrap;gap:.3rem .4rem;align-items:center;padding:.35rem 0 0 3.35rem;
  font-size:.85rem;color:var(--text-muted)}
.bn-row .meta:empty{display:none}
.bn-row .chip{display:inline-flex;align-items:center;gap:.25rem;border-radius:999px;padding:.1rem .55rem;
  font-weight:700;
  min-height:1.6rem;max-width:100%;overflow-wrap:anywhere}
.bn-row .chip svg.i{width:14px;height:14px;flex:none}
.bn-row .chip.own{background:var(--green-tint);color:var(--green-text)}
.bn-row .chip.want{background:var(--orange-tint);color:var(--orange-text)}
.bn-row .chip.hit{background:var(--blue-tint);color:var(--blue-text)}
.bn-row .chip.lst,.bn-row .chip.tg{background:var(--surface-2);color:var(--text)}
.bn-row .band{font-variant-numeric:tabular-nums}
.bn-empty{padding:1.5rem 1rem;text-align:center;border:1px dashed var(--border);border-radius:14px}
.bn-more{display:flex;justify-content:center;margin:.25rem 0 0}
.bn .ui-bar.slim{margin:0}
.bn-num{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem .75rem;margin:0}
.bn-num > span:first-child{flex:1 1 11rem;min-width:0}
.bn-num .in{display:inline-flex;align-items:center;gap:.3rem;font-weight:700}
.bn-num input{width:6rem;min-height:44px;text-align:right}
.bn-choice{border:0;margin:0 0 .5rem;padding:0;display:flex;flex-direction:column;gap:.25rem}
.bn-choice label{display:flex;gap:.6rem;align-items:flex-start;min-height:44px;padding:.35rem 0}
.bn-choice small{display:block;color:var(--text-muted)}
.bn-own{display:flex;flex-direction:column;gap:.5rem}
.bn-own[hidden]{display:none}
.bn-example{background:var(--surface-2);border-radius:10px;padding:.65rem .85rem;margin:.5rem 0 0;
  font-variant-numeric:tabular-nums}
.bn-example:empty{display:none}
.bn-set{max-width:42rem}
.bn-set h1{margin:.25rem 0 1rem;font-size:1.6rem}
.bn-set .ygrp.bn-keep{display:block}  /* a group of fields, no rows: the empty-group rule would hide it */
.bn-set .bn-fields{display:flex;flex-direction:column;gap:.75rem;padding:.9rem 1rem}
.bn-set .bn-fields .ui-hint{margin:0}
.bn-save{display:flex;justify-content:flex-end}
.bn-save .btn{min-width:8rem}
/* the split view needs room for the words, not only pixels: the queries are on the page's width
   in its own text size (.bn-wrap), so 200% text on an open Fold gets the phone's one column */
.bn-wrap{container:bn / inline-size}
/* a phone: the library, or the list once one is picked */
@container bn (max-width:55.99em){
  .bn.picked .bn-lib{display:none}
  .bn:not(.picked) .bn-main{display:none}
}
/* a wide screen: the library beside the list (split view), the list centred in the space left */
@container bn (min-width:56em){
  .bn{grid-template-columns:minmax(0,min(22rem,40%)) minmax(0,1fr);gap:clamp(1rem,3vw,2rem)}
  .bn .bn-back{display:none}
  .bn-main{max-width:60rem}
  .bn-row{grid-template-columns:minmax(0,1fr)}
  .bn-row .meta{padding-left:3.35rem}
}
@container bn (min-width:68em){
  .bn-row{grid-template-columns:minmax(0,1fr) minmax(0,.9fr);align-items:center}
  .bn.selecting .bn-row{grid-template-columns:auto minmax(0,1fr) minmax(0,.9fr)}
  .bn-row .tick{grid-row:auto}
  .bn-row .meta{padding:0}
  .bn-open{grid-template-columns:auto minmax(0,1fr) auto}
  .bn-open .pr{order:3}
}
"""


def add_binder_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings

    def page(
        title: str, body: str, *, sub: str, sid: str | None, current: str = PATH, status: int = 200
    ) -> Response:
        user = state.db.get_user(sub) or {}
        return render(
            title,
            body,
            site=s.server_name,
            status=status,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=bool(s.admin_group and s.admin_group in (user.get("groups") or [])),
            current=current,
            heading=False,
            main_class="ui-wide",
            # deck.css carries the card viewer's own styles (the dialog, its bottom sheet on phones)
            head_extra=f"{stylesheet_link('deck.css')}<style>{account_hub.HUB_CSS}{CSS}</style>"
            "<script src='/static/cardview.js' defer></script>"
            "<script src='/static/binder.js' defer></script>",
            sub=sub,
        )

    def notice_of(sub: str, qp: Any, csrf: str, back: str) -> str:
        ok, err = qp.get("ok") or "", qp.get("err") or ""
        # a bulk change the Binder's own form made (Tag on Archidekt, Remove from binder)
        coll = getattr(state, "collection_notice", None)
        if ok == "bulk" or err == "bulk":
            return coll("bulk", sub=sub, token=qp.get("t"), csrf=csrf, back=back) if coll else ""
        if err:
            return ui.notice(qp.get("e") or "That did not work. Try again.", "error", role="alert")
        if ok not in OK_WORDS:
            return ""
        n = qp.get("n") or ""
        text = OK_WORDS[ok].format(n=n, l=f"“{qp.get('l') or ''}”")
        undo = qp.get("undo") or ""
        btn = (
            f" <form method='post' action='{PATH}' class='inline'><input type='hidden' name='csrf'"
            f" value='{esc(csrf)}'>"
            "<input type='hidden' name='action' value='undo'><input type='hidden' name='token'"
            f" value='{esc(undo)}'>"
            f"<input type='hidden' name='back' value='{esc(back)}'><button class='btn small'"
            " type='submit'>Undo</button></form>"
            if undo
            else ""
        )
        return ui.notice(esc(text) + btn, "ok", role="status", html=True)

    @server.custom_route(PATH, methods=["GET"], include_in_schema=False)
    async def binder_get(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, PATH)
        svc = getattr(state, "prices", None)
        if svc is None:  # watchlists off: the Binder is the Archidekt collection page
            return RedirectResponse("/collection", status_code=307)
        qp = request.query_params
        view = View(qp)
        csrf = _csrf(s, sid) or ""
        rows = binder_library.all_rows(svc, sub)
        shown, seg = binder_library.view(
            rows,
            show=view.show,
            list_name=view.list,
            q=view.q,
            tag=view.tag,
            sort=view.sort,
            descending=view.desc,
        )
        try:
            start = max(0, min(int(qp.get("from") or 0), 1_000_000))
        except ValueError:
            start = 0
        if qp.get("part") == "rows":  # Load more: the next rows only
            body = "".join(row_html(r) for r in shown[start : start + binder_library.PAGE])
            left = max(0, len(shown) - start - binder_library.PAGE)
            return HTMLResponse(body, headers={**NO_STORE, "X-More": str(left)})
        tags = sorted({t for r in rows for t in r["tags"]}, key=str.casefold)
        lists = svc.lists(sub)
        defaults = svc.defaults_of(sub)
        back = view.href()
        main = main_html(
            view,
            shown,
            seg,
            total_value=binder_library.value(shown),
            start=start,
            tags=tags,
            notice=notice_of(sub, qp, csrf, back),
            csrf=csrf,
        )
        body = (
            f"<h1 class='sr-only'>{TITLE}</h1>"
            f"<div class='bn-wrap'><div class='bn{' picked' if view.picked else ''}' data-csrf='{esc(csrf)}' "
            f"data-floor='{defaults.get('floor') or 0:g}'>"
            + library_html(
                view,
                binder_library.counts(rows),
                lists,
                binder_library.list_counts(rows),
                decks=len(svc.watched_decks(sub)),
                defaults=defaults,
            )
            + main
            + boxes_html(view, lists, defaults, csrf, tags)
            + "</div></div>"
        )
        return page(f"{TITLE}: {view.title()}" if view.picked else TITLE, body, sub=sub, sid=sid)

    def done(back: str, ok: str, **more: str) -> RedirectResponse:
        if not back.startswith(PATH):
            back = PATH
        q = urlencode({"ok": ok, **{k: v for k, v in more.items() if v}})
        return RedirectResponse(back + ("&" if "?" in back else "?") + q, status_code=303)

    def failed(back: str, message: str) -> RedirectResponse:
        if not back.startswith(PATH):
            back = PATH
        q = urlencode({"err": "1", "e": message[:300]})
        return RedirectResponse(back + ("&" if "?" in back else "?") + q, status_code=303)

    def picked(data: dict[str, list[str]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keys = set((data.get("keys") or [""])[0].split())
        return [r for r in rows if r["key"] in keys]

    @server.custom_route(PATH, methods=["POST"], include_in_schema=False)
    async def binder_post(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, PATH)
        svc = getattr(state, "prices", None)
        if svc is None:
            return RedirectResponse("/collection", status_code=303)
        raw = await read_limited(request, MAX_FORM)
        fields = parse_qs((raw or b"").decode("utf-8", "replace"))
        data = {k: v[0] for k, v in fields.items()}
        back = (data.get("back") or PATH)[:600]
        if raw is None or not csrf_form_ok(s, sid, data):
            return failed(back, "This form expired. Reload the page and try again.")
        action = data.get("action", "")
        try:
            if action == "undo":
                hit = _take_undo(sub, data.get("token", ""))
                if hit is None:
                    return failed(back, "That Undo is no longer available.")
                kind, payload = hit
                if kind == "state":
                    svc.restore_watch_state(sub, payload["watches"])
                    for name, items in payload.get("owned", {}).items():
                        svc.put_owned_in_list(sub, items, name)
                elif kind == "watch":
                    svc.stop_watching(sub, payload)
                elif kind == "unwatch":
                    svc.rewatch(sub, payload)
                return done(back, "undone")
            if action in ("newlist", "rename", "delete"):
                if action == "newlist":
                    name = svc.create_list(sub, data.get("name", ""))
                    return done(PATH + "?" + urlencode({"list": name}), "newlist", l=name)
                if action == "rename":
                    name = svc.rename_list(sub, data.get("name", ""), data.get("new_name", ""))
                    return done(PATH + "?" + urlencode({"list": name}), "renamed", l=name)
                name = data.get("name", "")
                svc.delete_list(sub, name)
                return done(PATH, "deleted", l=name)
            rows = picked(fields, binder_library.all_rows(svc, sub))
            if not rows:
                return failed(back, "Select at least one card first.")
            watched = [r["watch_id"] for r in rows if r["watch_id"]]
            if action == "alerts":
                if not watched:
                    return failed(back, "None of those cards is watched yet. Watch them first.")
                before = svc.watch_state(sub, watched)
                if data.get("mode") == "own":
                    svc.set_alerts_many(
                        sub,
                        watched,
                        drop=data.get("alert_drop") or None,
                        rise=data.get("alert_rise") or None,
                        pct=data.get("alert_pct") or None,
                    )
                    word = "alerts"
                else:
                    svc.use_defaults_many(sub, watched)
                    word = "defaults"
                token = _keep_undo(sub, "state", {"watches": before})
                return done(back, word, n=plural(len(watched), "card"), undo=token)
            if action == "list":
                name = prices.clean_list_name(data.get("new_list") or data.get("list") or "")
                before = svc.watch_state(sub, watched) if watched else []
                owned_only = [r for r in rows if not r["watch_id"] and r["scryfall_id"]]
                was: dict[str, list[dict[str, Any]]] = {}
                for r in owned_only:
                    was.setdefault(r["list"], []).append(
                        {"scryfall_id": r["scryfall_id"], "finish": r["finish"], "name": r["name"]}
                    )
                if watched:
                    svc.move_to_list(sub, watched, name)
                if owned_only:
                    svc.put_owned_in_list(sub, [x for xs in was.values() for x in xs], name)
                token = _keep_undo(sub, "state", {"watches": before, "owned": was})
                n = plural(len(watched) + len(owned_only), "card")
                return done(back, "list" if name else "nolist", n=n, l=name, undo=token)
            if action == "watch":
                items = [
                    {
                        "scryfall_id": r["scryfall_id"],
                        "name": r["name"],
                        "set": r["set"],
                        "collector_number": r["cn"],
                        "finish": r["finish"],
                    }
                    for r in rows
                    if not r["watch_id"] and r["scryfall_id"]
                ]
                if not items:
                    return failed(back, "Those cards are watched already.")
                made = svc.watch_printings(sub, items)
                token = _keep_undo(sub, "watch", made)
                return done(back, "watch", n=plural(len(made), "card"), undo=token)
            if action == "unwatch":
                if not watched:
                    return failed(back, "None of those cards is watched.")
                token, gone = svc.stop_watching(sub, watched)
                keep = _keep_undo(sub, "unwatch", token)
                return done(back, "unwatch", n=plural(len(gone), "card"), undo=keep)
            return failed(back, "Choose what to do with the selected cards.")
        except DeckError as exc:
            return failed(back, str(exc))

    @server.custom_route(SETTINGS, methods=["GET"], include_in_schema=False)
    async def settings_get(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, SETTINGS)
        svc = getattr(state, "prices", None)
        if svc is None:
            return RedirectResponse("/collection", status_code=307)
        qp = request.query_params
        notice = ""
        if qp.get("ok") == "settings":
            notice = ui.notice(OK_WORDS["settings"].format(n=qp.get("n") or ""), "ok", role="status")
        elif qp.get("err"):
            notice = ui.notice(qp.get("e") or "Those settings could not be saved.", "error", role="alert")
        body = settings_html(svc.defaults_of(sub), svc.defaults_split(sub), _csrf(s, sid) or "", notice)
        return page("Watch settings", body, sub=sub, sid=sid)

    @server.custom_route(SETTINGS, methods=["POST"], include_in_schema=False)
    async def settings_post(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, SETTINGS)
        svc = getattr(state, "prices", None)
        if svc is None:
            return RedirectResponse("/collection", status_code=303)
        raw = await read_limited(request, 8192)
        data = {k: v[0] for k, v in parse_qs((raw or b"").decode("utf-8", "replace")).items()}
        if raw is None or not csrf_form_ok(s, sid, data):
            return RedirectResponse(
                SETTINGS + "?" + urlencode({"err": "1", "e": "This form expired. Reload the page."}), 303
            )
        try:
            values = prices._alerts(
                data.get("alert_pct"), None, None, data.get("alert_drop"), data.get("alert_rise")
            )
            kept = {k: v for k, v in zip(prices.ALERT_KINDS, values, strict=True) if v is not None}
            floor = (data.get("alert_floor") or "").strip().lstrip("$")
            if floor:
                try:
                    f = round(float(floor), 2)
                except ValueError as exc:
                    raise DeckError("invalid", "The smallest move must be an amount, such as 0.50.") from exc
                if not 0 <= f <= 1000:
                    raise DeckError("invalid", "The smallest move must be from 0 to 1,000.")
                if f > 0:
                    kept["floor"] = f
            # the above and below prices are a card's own (they make no sense as one for every card)
            had = svc.defaults_of(sub)
            kept.update({k: had[k] for k in ("above", "below") if had.get(k) is not None})
            if not prefs.save(state.db, sub, prefs.WATCH_ALERTS, kept):
                raise DeckError("invalid", "Those settings could not be saved.")
            svc.apply_defaults(sub)
            binder_library.forget(sub)
        except DeckError as exc:
            return RedirectResponse(SETTINGS + "?" + urlencode({"err": "1", "e": str(exc)}), 303)
        follow, _own = svc.defaults_split(sub)
        return RedirectResponse(
            SETTINGS + "?" + urlencode({"ok": "settings", "n": plural(follow, "card")}), 303
        )
