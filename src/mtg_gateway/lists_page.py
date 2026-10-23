"""My lists (/lists): the member's five account-wide Archidekt lists (account_lists.py) as one
page with five tabs: Category templates, Default categories, Color tags, Pinned cards and Custom
cards (parity release 2, mockup screens/10-lists.js).

Each tab is one Archidekt read. Every row has a ⋮ (with scripts a menu from the ⋮, a right-click
or a press and hold, static/lists.js; without them a plain drop-down of links). The boxes (ui.box)
are drawn hidden once per tab and filled from the row with scripts; an old-style address (?new=1,
?edit=ID, ?delete=ID, ?clear=ID, ?remove=ID, ?remove_all=1) draws the box open with its values, so
a browser without scripts asks the same way before a delete. Every save is the member's own press
(account_lists.hand_change: the same proposal an assistant makes, applied at once), then the tab
opens again with a notice, or the box shows Archidekt's refusal.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any, NamedTuple
from urllib.parse import parse_qs, quote, urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import account_lists, modes, ui
from .busy import busy_response
from .companion import DECK_CSP
from .deckpage import image_url
from .decks import DeckError, current_client
from .pages import (
    BROWSER_CLIENT_ID,
    TOO_BIG,
    _csrf,
    browser_session,
    limited_form,
    login_redirect,
    read_limited,
)
from .theme import esc, icon

PAGE = "/lists"
TITLE = "My lists"
SUB_TEXT = (
    "Your Archidekt lists that follow you into every deck. Changes are saved to Archidekt and read "
    "back; a copy from just before is kept under History."
)
# the page shows the chosen picture before it is sent (a blob: address), and a pin's printing
LISTS_CSP = DECK_CSP.replace("img-src 'self'", "img-src 'self' blob:")
PICTURE_BODY_LIMIT = account_lists.PICTURE_MAX_BYTES + 64_000  # the file plus the form's own fields


class Tab(NamedTuple):
    slug: str
    label: str
    kind: str


TABS = (
    Tab("templates", "Category templates", "templates"),
    Tab("defaults", "Default categories", "default_categories"),
    Tab("tags", "Color tags", "color_tags"),
    Tab("pins", "Pinned cards", "pins"),
    Tab("custom", "Custom cards", "custom_cards"),
)
BY_SLUG = {t.slug: t for t in TABS}
FLAGS = (
    ("in_deck", "In deck", "Counts in the deck"),
    ("in_price", "In price", "Counts in the price"),
    ("premier", "Premier", "Premier (commander zone)"),
)
HIST_TAIL = " is kept under History, and the result is read back to check."
# the member's own text in the box after a save: the mockup's toasts
NOTICES = {
    "template_created": "Template created on Archidekt and read back.",
    "template_saved": "Template saved on Archidekt and read back.",
    "template_deleted": "Deleted “{name}” on Archidekt.",
    "defaults_saved": "Default categories for “{name}” saved on Archidekt and read back.",
    "defaults_cleared": "Cleared on Archidekt.",
    "tag_created": "Color tag created on Archidekt and read back.",
    "tag_saved": "Color tag saved on Archidekt and read back.",
    "tag_deleted": "Deleted “{name}” on Archidekt.",
    "pin_saved": "Saved on Archidekt.",
    "pin_removed": "Pin removed on Archidekt.",
    "pins_removed": "All pins removed on Archidekt.",
    "card_created": "Custom card created on Archidekt and read back.",
    "card_saved": "Custom card saved on Archidekt and read back.",
    "card_deleted": "Deleted “{name}” on Archidekt.",
    "card_added": "Added “{name}” to the deck on Archidekt and read back.",
    "picture_saved": "Picture saved on Archidekt and read back.",
}
UNCONFIRMED = (
    "Sent to Archidekt, but the read-back didn't confirm every change. See Result on the review page."
)
CUSTOM_FACE_FIELDS = (
    ("Name", "n", "", 200, ""),
    ("Mana cost", "m", "{2}{G}", 200, ""),
    ("Types", "t", "Creature", 200, ""),
    ("Subtypes", "st", "Human Advisor", 200, ""),
    ("Supertypes", "su", "Legendary", 200, ""),
    ("Power", "p", "", 20, ""),
    ("Toughness", "tg", "", 20, ""),
    ("Loyalty", "l", "", 20, ""),
    ("Artist", "a", "", 200, ""),
)
# form field suffix -> Archidekt field name (per face)
FACE_KEYS = {
    "n": "Name",
    "m": "ManaCost",
    "t": "Types",
    "st": "SubTypes",
    "su": "SuperTypes",
    "x": "Text",
    "p": "Power",
    "tg": "Toughness",
    "l": "Loyalty",
    "a": "ArtistName",
}
_MANA = re.compile(r"\{([^{}]+)\}")
_ID = re.compile(r"[0-9]{1,12}")


def tab_href(slug: str, **params: Any) -> str:
    q = {k: v for k, v in params.items() if v not in (None, "", 0, False)}
    return f"{PAGE}/{slug}" + (f"?{urlencode(q)}" if q else "")


def notice_text(code: str, name: str = "") -> str:
    text = NOTICES.get(code, "")
    return text.replace("{name}", name) if text else ""


def cmc_of(cost: str) -> float:
    """The mana value of a mana cost like {2}{G}{G}, as Archidekt's card builder works it out."""
    total = 0.0
    for sym in _MANA.findall(cost or ""):
        parts = [p.strip().upper() for p in sym.split("/")]
        best = 0.0
        for p in parts:
            if p.isdigit():
                best = max(best, float(p))
            elif p in ("X", "Y", "Z", "P", "S", ""):
                best = max(best, 0.0)
            elif re.fullmatch(r"[0-9]+(\.[0-9]+)?", p):
                best = max(best, float(p))
            else:
                best = max(best, 1.0)
        total += best
    return int(total) if total == int(total) else total


# -- pieces -----------------------------------------------------------------------------------------


def hist(what: str) -> str:
    return f"<p class='muted small lp-hist'>{icon('history')} {esc(what)}{HIST_TAIL}</p>"


def warn(lead: str, rest: str) -> str:
    return ui.notice(
        f"<b>{esc(lead)}</b> {esc(rest)}" if lead else esc(rest), "warn", html=True, cls="lp-warn"
    )


def counter(for_id: str, n: int, limit: int) -> str:
    return (
        f"<div class='lp-under'><span></span><span class='muted small' id='{esc(for_id)}-c' data-counter"
        f" aria-live='polite'>{n} / {limit}</span></div>"
    )


def text_field(
    fid: str,
    name: str,
    label: str,
    value: str = "",
    *,
    limit: int | None = None,
    placeholder: str = "",
    required: bool = False,
    cls: str = "",
) -> str:
    extra = (f" maxlength='{limit}'" if limit else "") + (" required" if required else "")
    ph = f" placeholder='{esc(placeholder)}'" if placeholder else ""
    counted = f" data-count='{esc(fid)}-c'" if limit else ""
    return (
        f"<div class='field{(' ' + cls) if cls else ''}'><label for='{esc(fid)}'>{esc(label)}</label>"
        f"<input id='{esc(fid)}' type='text' name='{esc(name)}' value='{esc(value)}' autocomplete='off'"
        f"{extra}{ph}{counted}></div>" + (counter(fid, len(value), limit) if limit else "")
    )


def switch(name: str, label: str, on: bool, *, fid: str = "") -> str:
    i = f" id='{esc(fid)}'" if fid else ""
    return (
        f"<label class='switch lp-switch'><input{i} type='checkbox' name='{esc(name)}' value='1'"
        f"{' checked' if on else ''}><span class='track' aria-hidden='true'></span>{esc(label)}</label>"
    )


def kebab_menu(name: str, items: list[tuple[str, str, str, dict[str, str]] | None]) -> str:
    """The row's ⋮: a plain drop-down of links (details/summary) without scripts; static/lists.js
    turns it into the shared menu. ``items``: (text, icon, href, attrs) or None for a line."""
    out = []
    for it in items:
        if it is None:
            out.append("<div class='sep'></div>")
            continue
        text, ic, href, attrs = it
        a = "".join(f" {k}='{esc(v)}'" for k, v in attrs.items())
        out.append(f"<a href='{esc(href)}' data-icon='{esc(ic)}'{a}>{icon(ic)}{esc(text)}</a>")
    return (
        f"<span class='act'><details class='dd rowmenu lp-dd'><summary class='kebab' aria-label='Menu for "
        f"{esc(name)}' title='Menu' aria-haspopup='menu'>{icon('more')}</summary>"
        f"<div class='menu'><div class='head'>{esc(name)}</div>{''.join(out)}</div></details></span>"
    )


def row_attrs(kind: str, name: str, data: dict[str, Any]) -> str:
    return (
        f"class='tr' data-kind='{esc(kind)}' data-name='{esc(name)}' data-row='{esc(json.dumps(data))}' "
        "tabindex='0'"
    )


def box_note(error: str) -> str:
    return f"<p class='notice error boxnote' role='alert'>{esc(error)}</p>" if error else ""


def tabs_html(current: str, counts: dict[str, int]) -> str:
    out = []
    for t in TABS:
        n = counts.get(t.kind)
        cur = " aria-current='page'" if t.slug == current else ""
        num = f"<span class='n'>{n:,}</span>" if n is not None else ""
        out.append(f"<a href='{PAGE}/{t.slug}'{cur}>{esc(t.label)}{num}</a>")
    return f"<nav class='adm-nav lp-tabs' aria-label='Lists'>{''.join(out)}</nav>"


def list_head(title: str, count_html: str, actions_html: str = "") -> str:
    acts = f"<span class='acts'>{actions_html}</span>" if actions_html else ""
    return (
        f"<div class='list-head'><h2>{esc(title)} <span class='ui-sub'>{count_html}</span></h2>{acts}</div>"
    )


def thumb(uid: str, name: str) -> str:
    src = image_url(uid, "small") if uid else None
    if src:
        return f"<img class='lp-thumb' src='{esc(src)}' alt='' loading='lazy' width='34' height='47'>"
    return "<span class='lp-thumb' aria-hidden='true'></span>"


def chip(name: str, main: bool) -> str:
    star = "<span class='star' aria-hidden='true'>★</span>" if main else ""
    return f"<span class='lp-chip{' main' if main else ''}'>{star}{esc(name)}</span>"


# -- Category templates -------------------------------------------------------------------------------


def templates_rows(items: list[dict[str, Any]]) -> str:
    out = []
    for t in items:
        name = t["name"]
        on = [w for k, w, _l in FLAGS if t[k]]
        lbls = "".join(
            ui.label(w, "green", "check") if t[k] else ui.label(w, "grey", "x") for k, w, _l in FLAGS
        )
        slug = "templates"
        menu = kebab_menu(
            name,
            [
                ("Edit…", "edit", tab_href(slug, edit=t["id"]), {"data-box": "tpl-box", "data-mode": "edit"}),
                ("Add to a deck…", "plus", tab_href(slug, add=t["id"]), {"data-box": "tpl-deck"}),
                None,
                (
                    "Delete…",
                    "trash",
                    tab_href(slug, delete=t["id"]),
                    {"class": "danger", "data-box": "tpl-del"},
                ),
            ],
        )
        out.append(
            f"<div {row_attrs('template', name, t)}>"
            f"<span class='title'><span class='t'>{esc(name)}</span>"
            f"<span class='sm-only'>{esc(' · '.join(on) or 'Not in deck or price')}</span></span>"
            f"<span class='sm-hide flags'>{lbls}</span>{menu}</div>"
        )
    return "".join(out)


def template_deck_box(csrf: str, st: dict[str, Any]) -> str:
    """Add to a deck… (parity release 2): the deck picked from the member's decks (read when the
    box opens, static/lists.js; the old address ?add= draws it with the list read here), then the
    deck gets one category made from the template (account_lists.from_templates), proposed as a
    categories change and sent to its review page."""
    name = (st.get("values") or {}).get("name", "")
    rows = st.get("decks") or []
    picked = (st.get("values") or {}).get("deck", "")
    opts = "".join(
        f"<option value='{esc(str(d['id']))}'{' selected' if str(d['id']) == picked else ''}>{esc(d['name'])}"
        "</option>"
        for d in rows
    )
    body = (
        f"<form id='tpl-deck-f' method='post' action='{PAGE}/templates'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='action' "
        f"value='add_to_deck'><input type='hidden' name='was_name' value='{esc(name)}'>"
        "<div class='field'><label for='tpl-deck-sel'>Deck</label><span class='sel'>"
        f"<select id='tpl-deck-sel' name='deck' required data-src='/api/decks/mine'>{opts}</select></span>"
        "</div></form>"
        "<p class='muted small'>The deck gets a category with this name and these flags. The same as "
        "Deck settings › Categories › Add template.</p>" + box_note(st.get("error", ""))
    )
    return ui.box(
        "tpl-deck",
        f"Add “{name}” to a deck",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Add category", kind="primary", form="tpl-deck-f", icon_name="plus"),
        ],
        open=bool(st),
        close_href=f"{PAGE}/templates",
        cls="lp-box",
    ).replace("<div class='ui-box", "<div data-t='Add “{name}” to a deck' class='ui-box", 1)


def templates_tab(got: dict[str, Any], csrf: str, box: dict[str, Any]) -> str:
    items = got["items"]
    head = list_head(
        "Category templates",
        f"{len(items)}",
        ui.box_opener(
            "New template", tab_href("templates", new=1), "tpl-box", kind="primary", icon_name="plus"
        ),
    )
    table = (
        ui.table_open(
            "minmax(0,1.6fr) minmax(0,2.4fr) 44px",
            "minmax(0,1fr) minmax(0,1.6fr) 44px",
            "minmax(0,1fr) 44px",
            cls="lp-tbl lp-tpl",
            label="Category templates",
        )
        + ui.table_header([ui.Column("Template"), ui.Column("Flags", "sm-hide"), ui.Column("")])
        + (
            templates_rows(items)
            or "<div class='tr lp-empty'><span class='muted'>No category templates yet.</span></div>"
        )
        + "</div>"
    )
    return (
        head
        + "<p class='ui-sub'>Categories you can add to any deck in one press. Once added, the deck's copy "
        "and the template are not linked.</p>"
        + table
        + template_box(csrf, box if box.get("box") == "tpl-box" else {})
        + template_delete_box(csrf, box if box.get("box") == "tpl-del" else {})
        + template_deck_box(csrf, box if box.get("box") == "tpl-deck" else {})
    )


def template_box(csrf: str, st: dict[str, Any]) -> str:
    v = st.get("values") or {}
    editing = st.get("mode") == "edit"
    name = v.get("name", "")
    body = (
        f"<form id='tpl-f' method='post' action='{PAGE}/templates'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='action' value='{'edit' if editing else 'create'}'>"
        f"<input type='hidden' name='was_name' value='{esc(v.get('was_name', ''))}'>"
        + text_field(
            "tpl-n",
            "name",
            "Name",
            name,
            limit=account_lists.TEMPLATE_NAME_MAX,
            placeholder="Ramp",
            required=True,
        )
        + "<div class='lp-switches'>"
        + "".join(
            switch(k, lab, v.get(k, k != "premier") if v else k != "premier", fid=f"tpl-{k}")
            for k, _w, lab in FLAGS
        )
        + "</div>"
        + f"<div data-edit-only{'' if editing else ' hidden'}>"
        + warn(
            "",
            "Cards with this name in their default categories follow the change. Cards already in decks "
            "don't change.",
        )
        + "</div>"
        + "<p class='muted small'>Once a template is added to a deck, the two are not linked: editing one "
        "doesn't change the other.</p>"
        + hist("A copy of your templates from just before")
        + "</form>"
        + box_note(st.get("error", ""))
    )
    return ui.box(
        "tpl-box",
        f"Edit “{name}”" if editing else "New category template",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(
                "Save template" if editing else "Create template",
                kind="primary",
                form="tpl-f",
                icon_name="check",
            ),
        ],
        open=bool(st),
        close_href=f"{PAGE}/templates",
        cls="lp-box",
    ).replace(
        "<div class='ui-box",
        "<div data-t-new='New category template' data-t-edit='Edit “{name}”' data-ok-new='Create template' "
        "data-ok-edit='Save template' class='ui-box",
        1,
    )


def template_delete_box(csrf: str, st: dict[str, Any]) -> str:
    name = (st.get("values") or {}).get("name", "")
    body = (
        f"<form id='tpl-del-f' method='post' action='{PAGE}/templates'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='action' "
        "value='delete'>"
        "<input type='hidden' name='confirmed' value='1'><input type='hidden' name='was_name' "
        f"value='{esc(name)}'></form>"
        + ui.notice(
            f"<b>“<span data-name>{esc(name)}</span>” is also removed from every card's default "
            "categories.</b> Cards already in decks keep their categories.",
            "warn",
            html=True,
            cls="lp-warn",
        )
        + hist("A copy of your templates and default categories from just before")
        + box_note(st.get("error", ""))
    )
    return ui.box(
        "tpl-del",
        f"Delete the template “{name}”?",
        body,
        actions=[
            ui.box_button("Keep it", cancel=True),
            ui.box_button(f"Delete “{name}”", kind="danger-solid", form="tpl-del-f", icon_name="trash"),
        ],
        open=bool(st),
        close_href=f"{PAGE}/templates",
        cls="lp-box",
    ).replace(
        "<div class='ui-box",
        "<div data-t='Delete the template “{name}”?' data-ok='Delete “{name}”' class='ui-box",
        1,
    )


# -- Default categories --------------------------------------------------------------------------------


def defaults_rows(items: list[dict[str, Any]]) -> str:
    out = []
    for d in items:
        name = d["name"]
        cats = d["categories"]
        chips = "".join(chip(c, i == 0) for i, c in enumerate(cats))
        menu = kebab_menu(
            name,
            [
                (
                    "Change categories…",
                    "edit",
                    tab_href("defaults", edit=d["oracle_id"]),
                    {"data-box": "def-box"},
                ),
                None,
                (
                    "Clear…",
                    "x",
                    tab_href("defaults", clear=d["oracle_id"]),
                    {"class": "danger", "data-box": "def-clear"},
                ),
            ],
        )
        out.append(
            f"<div {row_attrs('default', name, d)} data-q='{esc(name.casefold())}'>"
            f"<span class='title'><span class='t'>{esc(name)}</span><span class='sm-only "
            f"flags'>{chips}</span></span>"
            f"<span class='sm-hide flags'>{chips}</span>{menu}</div>"
        )
    return "".join(out)


def defaults_tab(got: dict[str, Any], csrf: str, box: dict[str, Any], q: str) -> str:
    items = got["items"]
    shown = [d for d in items if not q or q.casefold() in d["name"].casefold()]
    total = got["total"]
    page = got["page"]
    head = list_head("Default categories", f"{total:,} card{'s' if total != 1 else ''}")
    rows = defaults_rows(shown)
    if not rows:
        rows = (
            "<div class='tr lp-empty'><span class='muted'>"
            + (
                "No card with default categories matches that name."
                if q
                else "No cards with default categories yet."
            )
            + "</span></div>"
        )
    table = (
        ui.table_open(
            "minmax(0,1.4fr) minmax(0,2.6fr) 44px",
            "minmax(0,1fr) minmax(0,1.6fr) 44px",
            "minmax(0,1fr) 44px",
            cls="lp-tbl lp-def",
            label="Default categories",
        )
        + ui.table_header([ui.Column("Card"), ui.Column("Default categories", "sm-hide"), ui.Column("")])
        + rows
        + "</div>"
    )
    seen = min(total, page * account_lists.DEFAULT_PAGE_SIZE)
    more = (
        ui.load_more(
            tab_href("defaults", page=page + 1, q=q), text=f"Load more ({seen:,} of {total:,} shown)"
        )
        if got["more"]
        else (ui.load_more("", done=f"All {total:,} shown.") if total else "")
    )
    return (
        head
        + "<p class='ui-sub'>When you add one of these cards to a deck, it goes into these categories; ★ is "
        "the main one. Set them from a card's menu on a deck (Lock personal categories) or here. Choices "
        "come from your category templates.</p>"
        + ui.toolbar(f"{PAGE}/defaults", search_label="Search by card name", placeholder="Card name", q=q)
        + "<p class='sr-only' data-filter-count role='status' aria-live='polite'></p>"
        + table
        + more
        + defaults_box(csrf, got.get("templates") or [], box if box.get("box") == "def-box" else {})
        + defaults_clear_box(csrf, box if box.get("box") == "def-clear" else {})
    )


def defaults_box(csrf: str, templates: list[str], st: dict[str, Any]) -> str:
    v = st.get("values") or {}
    name = v.get("name", "")
    picked = list(v.get("categories") or [])
    rows = []
    for t in templates:
        on = t in picked
        main = bool(picked) and picked[0] == t
        rows.append(
            f"<label><input type='checkbox' name='cat' value='{esc(t)}'{' checked' if on else ''} "
            f"aria-label='{esc(t)}'>"
            f"<span>{esc(t)}</span><span class='main'><input type='radio' name='main' value='{esc(t)}'"
            f"{' checked' if main else ''} aria-label='Main category {esc(t)}'>"
            "<span class='star' aria-hidden='true'>★</span> Main</span></label>"
        )
    body = (
        f"<form id='def-f' method='post' action='{PAGE}/defaults'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='action' "
        "value='set'>"
        f"<input type='hidden' name='card' value='{esc(name)}'>"
        "<p class='muted small'>When you add this card to a deck, it goes into these categories. The main "
        "one (★) is listed first. Choices come from your category templates.</p>"
        "<div class='lp-defpick' role='group' aria-label='Categories'>"
        + (
            "".join(rows)
            or "<p class=muted>You have no category templates yet. "
            "Make one on the Category templates tab.</p>"
        )
        + "</div>"
        + hist("A copy of your default categories from just before")
        + "</form>"
        + box_note(st.get("error", ""))
    )
    return ui.box(
        "def-box",
        f"Default categories for “{name}”",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Save", kind="primary", form="def-f", icon_name="check"),
        ],
        open=bool(st),
        close_href=f"{PAGE}/defaults",
        cls="lp-box",
    ).replace("<div class='ui-box", "<div data-t='Default categories for “{name}”' class='ui-box", 1)


def defaults_clear_box(csrf: str, st: dict[str, Any]) -> str:
    name = (st.get("values") or {}).get("name", "")
    body = (
        f"<form id='def-clear-f' method='post' action='{PAGE}/defaults'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='action' "
        "value='clear'>"
        "<input type='hidden' name='confirmed' value='1'><input type='hidden' name='card' "
        f"value='{esc(name)}'></form>"
        "<p>It will be added to decks with no preset category, as a card without defaults. A copy from just "
        "before is kept under History.</p>" + box_note(st.get("error", ""))
    )
    return ui.box(
        "def-clear",
        f"Clear the default categories of “{name}”?",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(f"Clear “{name}”", kind="danger-solid", form="def-clear-f", icon_name="x"),
        ],
        open=bool(st),
        close_href=f"{PAGE}/defaults",
        cls="lp-box",
    ).replace(
        "<div class='ui-box",
        "<div data-t='Clear the default categories of “{name}”?' data-ok='Clear “{name}”' class='ui-box",
        1,
    )


# -- Color tags -----------------------------------------------------------------------------------------


def swatch(color: str) -> str:
    return f"<span class='lp-swatch' style='background:{esc(color)}' aria-hidden='true'></span>"


def tags_rows(items: list[dict[str, Any]]) -> str:
    out = []
    for t in items:
        name, color = t["name"], t["color"]
        menu = kebab_menu(
            name,
            [
                (
                    "Rename or recolor…",
                    "edit",
                    tab_href("tags", edit=t["id"]),
                    {"data-box": "tag-box", "data-mode": "edit"},
                ),
                None,
                (
                    "Delete…",
                    "trash",
                    tab_href("tags", delete=t["id"]),
                    {"class": "danger", "data-box": "tag-del"},
                ),
            ],
        )
        out.append(
            f"<div {row_attrs('tag', name, t)}>"
            f"<span class='title lp-tagc'>{swatch(color)}<span class='t'>{esc(name)}</span>"
            f"<span class='sm-only'><code>{esc(color)}</code></span></span>"
            f"<span class='sm-hide'><code>{esc(color)}</code></span>{menu}</div>"
        )
    return "".join(out)


def tags_tab(got: dict[str, Any], csrf: str, box: dict[str, Any]) -> str:
    items = got["items"]
    head = list_head(
        "Color tags",
        f"{len(items)}",
        ui.box_opener("New color tag", tab_href("tags", new=1), "tag-box", kind="primary", icon_name="plus"),
    )
    table = (
        ui.table_open(
            "minmax(0,2fr) 7rem 44px",
            "minmax(0,1fr) 7rem 44px",
            "minmax(0,1fr) 44px",
            cls="lp-tbl lp-tag",
            label="Color tags",
        )
        + ui.table_header([ui.Column("Tag"), ui.Column("Color", "sm-hide"), ui.Column("")])
        + (tags_rows(items) or "<div class='tr lp-empty'><span class='muted'>No color tags yet.</span></div>")
        + "</div>"
    )
    return (
        head
        + "<p class='ui-sub'>Tags you can put on cards in any deck. A card keeps the name and color it was "
        "tagged with, so changes here don't reach cards already tagged.</p>"
        + table
        + tag_box(csrf, box if box.get("box") == "tag-box" else {})
        + tag_delete_box(csrf, box if box.get("box") == "tag-del" else {})
    )


def tag_box(csrf: str, st: dict[str, Any]) -> str:
    v = st.get("values") or {}
    editing = st.get("mode") == "edit"
    name = v.get("name", "")
    color = v.get("color") or account_lists.DEFAULT_TAG_COLOR
    body = (
        f"<form id='tag-f' method='post' action='{PAGE}/tags'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='action' value='{'edit' if editing else 'create'}'>"
        f"<input type='hidden' name='was_name' value='{esc(v.get('was_name', ''))}'>"
        + text_field(
            "tag-n", "name", "Name", name, limit=account_lists.TAG_NAME_MAX, placeholder="Cut", required=True
        )
        + "<div class='field'><label for='tag-c'>Color</label><div class='lp-tagpv'>"
        f"<input type='color' id='tag-c' value='{esc(color)}' aria-label='Pick a color' "
        "data-color-for='tag-h'>"
        f"<input type='text' class='hex' id='tag-h' name='color' value='{esc(color)}' aria-label='Color as "
        f"hex' maxlength='7' pattern='#[0-9a-fA-F]{{6}}' autocomplete='off' required>"
        "<span class='lp-chip' id='tag-pv' "
        f"style='border-color:{esc(color)};color:{esc(color)}'>{swatch(color)}<span "
        f"class='nm'>{esc(name or 'Preview')}</span></span>"
        "</div></div>"
        + f"<div data-edit-only{'' if editing else ' hidden'}>"
        + warn(
            "",
            "Cards already tagged keep their old name and color. Archidekt doesn't link a card's tag to this "
            "list.",
        )
        + "</div>"
        + hist("A copy of your color tags from just before")
        + "</form>"
        + box_note(st.get("error", ""))
    )
    return ui.box(
        "tag-box",
        f"Rename or recolor “{name}”" if editing else "New color tag",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(
                "Save tag" if editing else "Create tag", kind="primary", form="tag-f", icon_name="check"
            ),
        ],
        open=bool(st),
        close_href=f"{PAGE}/tags",
        cls="lp-box",
    ).replace(
        "<div class='ui-box",
        "<div data-t-new='New color tag' data-t-edit='Rename or recolor “{name}”' data-ok-new='Create tag' "
        "data-ok-edit='Save tag' data-default-color='" + account_lists.DEFAULT_TAG_COLOR + "' class='ui-box",
        1,
    )


def tag_delete_box(csrf: str, st: dict[str, Any]) -> str:
    name = (st.get("values") or {}).get("name", "")
    body = (
        f"<form id='tag-del-f' method='post' action='{PAGE}/tags'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='action' "
        "value='delete'>"
        "<input type='hidden' name='confirmed' value='1'><input type='hidden' name='was_name' "
        f"value='{esc(name)}'></form>"
        "<p>Cards already tagged keep their color and name; the tag only leaves this list and the deck's tag "
        "picker. A copy of the list from just before is kept under History.</p>"
        + box_note(st.get("error", ""))
    )
    return ui.box(
        "tag-del",
        f"Delete the tag “{name}”?",
        body,
        actions=[
            ui.box_button("Keep it", cancel=True),
            ui.box_button(f"Delete “{name}”", kind="danger-solid", form="tag-del-f", icon_name="trash"),
        ],
        open=bool(st),
        close_href=f"{PAGE}/tags",
        cls="lp-box",
    ).replace(
        "<div class='ui-box",
        "<div data-t='Delete the tag “{name}”?' data-ok='Delete “{name}”' class='ui-box",
        1,
    )


# -- Pinned cards -----------------------------------------------------------------------------------------


def printing_text(p: dict[str, Any]) -> str:
    where = p.get("set_name") or ""
    code = p.get("set_code") or ""
    num = p.get("collector_number") or ""
    parts = []
    if where and code:
        parts.append(f"{where} ({code}) {num}".strip())
    elif code or num:
        parts.append(f"{code} {num}".strip())
    if p.get("finish") and p["finish"] != "Normal":
        parts.append(p["finish"])
    return " · ".join(parts)


def pin_row(p: dict[str, Any], csrf: str) -> str:
    name, pid, q = p["name"], p["id"], int(p["quantity"])
    notes = p.get("notes") or ""
    csrf_in = f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
    fewer_title = "Below 1 removes the pin" if q <= 1 else "One fewer"
    stepper = (
        f"<form method='post' action='{PAGE}/pins' class='lp-step' role='group' aria-label='Copies of "
        f"{esc(name)}'>"
        f"{csrf_in}<input type='hidden' name='action' value='edit'><input type='hidden' name='pin_id' "
        f"value='{pid}'>"
        f"<button type='submit' name='quantity' value='{q - 1}' aria-label='One fewer' title='{fewer_title}'"
        f"{' data-removes' if q <= 1 else ''}>{icon('minus')}</button><b class='n' aria-live='polite'>{q}</b>"
        f"<button type='submit' name='quantity' value='{q + 1}' aria-label='One more' title='One more'"
        f"{' disabled' if q >= account_lists.PIN_MAX_COPIES else ''}>{icon('plus')}</button></form>"
    )
    note = (
        f"<form method='post' action='{PAGE}/pins' class='lp-note'>{csrf_in}"
        f"<input type='hidden' name='action' value='edit'><input type='hidden' name='pin_id' value='{pid}'>"
        f"<textarea name='notes' rows='1' maxlength='{account_lists.PIN_NOTES_MAX}' placeholder='Add a note'"
        f" aria-label='Note on {esc(name)}' data-count='pn-{pid}-c' data-saves>{esc(notes)}</textarea>"
        "<span class='lp-under'><noscript><button type='submit' class='btn-plain small'>Save "
        "note</button></noscript>"
        f"<span class='muted small c' id='pn-{pid}-c' data-counter>{len(notes)} / "
        f"{account_lists.PIN_NOTES_MAX}</span></span></form>"
    )
    menu = kebab_menu(
        name,
        [
            ("Open card", "eye", f"/cards?q={quote(name)}", {}),
            ("Change printing…", "image", f"/cards?q={quote(name)}", {"data-printing": "1"}),
            ("Add to deck…", "plus", f"/cards?q={quote(name)}", {}),
            None,
            ("Remove pin", "x", tab_href("pins", remove=pid), {"class": "danger", "data-box": "pin-rm"}),
        ],
    )
    return (
        f"<div {row_attrs('pin', name, p)}>"
        f"<span class='title'><span class='cardc'>{thumb(p.get('uid') or '', name)}<span class='tx'>"
        f"<span class='t'>{esc(name)}</span><span "
        f"class='s'>{esc(printing_text(p))}</span></span></span></span>"
        f"<span class='lp-copies'>{stepper}</span><span class='lp-notes'>{note}</span>{menu}</div>"
    )


def pins_tab(got: dict[str, Any], csrf: str, box: dict[str, Any]) -> str:
    items = got["items"]
    n = len(items)
    head = list_head(
        "Pinned cards",
        f"{n} / {account_lists.MAX_PINS}",
        ui.box_opener(
            "Remove all…", tab_href("pins", remove_all=1), "pin-all", kind="danger", icon_name="trash"
        )
        if items
        else "",
    )
    table = (
        ui.table_open(
            "minmax(0,1.6fr) 9rem minmax(0,2fr) 44px",
            "minmax(0,1fr) 9rem 44px",
            "minmax(0,1fr) 44px",
            cls="lp-tbl lp-pin",
            label="Pinned cards",
        )
        + ui.table_header(
            [ui.Column("Card"), ui.Column("Copies"), ui.Column("Notes", "lp-notes-h"), ui.Column("")]
        )
        + (
            "".join(pin_row(p, csrf) for p in items)
            or "<div class='tr lp-empty'><span class='muted'>No pinned cards yet. Pin one from any card's "
            "menu.</span></div>"
        )
        + "</div>"
    )
    return (
        head
        + "<p class='ui-sub'>Cards you keep to hand, with a note. Pin one from any card's menu. Notes save "
        "when you leave the box.</p>"
        + table
        + pin_remove_box(csrf, box if box.get("box") == "pin-rm" else {})
        + pins_remove_all_box(csrf, n, box if box.get("box") == "pin-all" else {})
    )


def pin_remove_box(csrf: str, st: dict[str, Any]) -> str:
    v = st.get("values") or {}
    name = v.get("name", "")
    body = (
        f"<form id='pin-rm-f' method='post' action='{PAGE}/pins'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='action' "
        "value='unpin'>"
        "<input type='hidden' name='confirmed' value='1'><input type='hidden' name='pin_id' "
        f"value='{esc(str(v.get('id', '')))}'></form>"
        "<p>Its note goes with it. A copy of your pinned cards from just before is kept under History.</p>"
        + box_note(st.get("error", ""))
    )
    return ui.box(
        "pin-rm",
        f"Remove the pin on “{name}”?",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Remove pin", kind="danger-solid", form="pin-rm-f", icon_name="x"),
        ],
        open=bool(st),
        close_href=f"{PAGE}/pins",
        cls="lp-box",
    ).replace("<div class='ui-box", "<div data-t='Remove the pin on “{name}”?' class='ui-box", 1)


def pins_remove_all_box(csrf: str, n: int, st: dict[str, Any]) -> str:
    body = (
        f"<form id='pin-all-f' method='post' action='{PAGE}/pins'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='action' "
        "value='remove_all'>"
        "<input type='hidden' name='confirmed' value='1'></form>"
        "<p>Every pin and its note is removed from Archidekt. A copy of the list from just before is kept "
        "under History.</p>" + box_note(st.get("error", ""))
    )
    return ui.box(
        "pin-all",
        f"Remove all {n} pinned card{'s' if n != 1 else ''}?",
        body,
        actions=[
            ui.box_button("Keep them", cancel=True),
            ui.box_button(f"Remove all {n}", kind="danger-solid", form="pin-all-f", icon_name="trash"),
        ],
        open=bool(st),
        close_href=f"{PAGE}/pins",
        cls="lp-box",
    )


# -- Custom cards ------------------------------------------------------------------------------------------


def type_line(c: dict[str, Any], face: str = "front") -> str:
    sup = str(c.get(f"{face}SuperTypes") or "").strip()
    typ = str(c.get(f"{face}Types") or "").strip()
    sub = str(c.get(f"{face}SubTypes") or "").strip()
    line = " ".join(x for x in (sup, typ) if x)
    if sub:
        line = f"{line} — {sub}" if line else sub
    return line


def pt_text(c: dict[str, Any], face: str = "front") -> str:
    p, t, loyalty = (str(c.get(f"{face}{k}") or "").strip() for k in ("Power", "Toughness", "Loyalty"))
    if p or t:
        return f"{p}/{t}"
    return loyalty


def custom_rows(items: list[dict[str, Any]]) -> str:
    out = []
    for c in items:
        name = c["name"]
        tl, pt = type_line(c), pt_text(c)
        setnum = " · ".join(
            x for x in (str(c.get("setCode") or "").upper(), str(c.get("collectorNumber") or "")) if x
        )
        sm = " · ".join(x for x in (tl + (f" {pt}" if pt else ""), setnum) if x)
        menu = kebab_menu(
            name,
            [
                (
                    "Edit…",
                    "edit",
                    tab_href("custom", edit=c["id"]),
                    {"data-box": "cc-box", "data-mode": "edit"},
                ),
                (
                    "Add to deck…",
                    "plus",
                    tab_href("custom", add=c["id"]),
                    {"data-box": "cc-add"},
                ),
                None,
                (
                    "Delete…",
                    "trash",
                    tab_href("custom", delete=c["id"]),
                    {"class": "danger", "data-box": "cc-del"},
                ),
            ],
        )
        out.append(
            f"<div {row_attrs('custom', name, c)}>"
            f"<span class='title'><span class='t'>{esc(name)}</span><span "
            f"class='sm-only'>{esc(sm)}</span></span>"
            f"<span class='sm-hide'>{esc(tl)}{(' <span class=muted>' + esc(pt) + '</span>') if pt else ''}"
            "</span>"
            f"<span class='sm-hide lp-setnum'>{esc(setnum)}</span>{menu}</div>"
        )
    return "".join(out)


def custom_tab(got: dict[str, Any], csrf: str, box: dict[str, Any], q: str) -> str:
    items = got["items"]
    total = got["total"]
    page = got["page"]
    head = list_head(
        "Custom cards",
        f"{total:,}",
        ui.box_opener(
            "New custom card", tab_href("custom", new=1), "cc-box", kind="primary", icon_name="plus"
        ),
    )
    rows = custom_rows(items) or (
        "<div class='tr lp-empty'><span class='muted'>"
        + ("No custom card matches that name." if q else "No custom cards yet.")
        + "</span></div>"
    )
    table = (
        ui.table_open(
            "minmax(0,2fr) minmax(0,1.6fr) 7rem 44px",
            "minmax(0,1fr) minmax(0,1fr) 44px",
            "minmax(0,1fr) 44px",
            cls="lp-tbl lp-cc",
            label="Custom cards",
        )
        + ui.table_header(
            [
                ui.Column("Card"),
                ui.Column("Type", "sm-hide"),
                ui.Column("Set · number", "sm-hide lp-setnum"),
                ui.Column(""),
            ]
        )
        + rows
        + "</div>"
    )
    shown = len(items) + (page - 1) * 50
    more = (
        ui.load_more(tab_href("custom", page=page + 1, q=q), text=f"Load more ({shown:,} of {total:,} shown)")
        if got["more"]
        else (ui.load_more("", done=f"All {total:,} shown.") if total else "")
    )
    return (
        head
        + "<p class='ui-sub'>Cards you made up. Add one to a deck from its menu; deleting one blanks it in "
        "every deck that has it.</p>"
        + ui.toolbar(f"{PAGE}/custom", search_label="Search by card name", placeholder="Card name", q=q)
        + table
        + more
        + custom_box(csrf, box if box.get("box") == "cc-box" else {})
        + custom_delete_box(csrf, box if box.get("box") == "cc-del" else {})
        + custom_add_box(csrf, box if box.get("box") == "cc-add" else {})
    )


ADD_DEFAULT_CATEGORY = "The deck's default"
ADD_CUSTOM_NOTE = (
    "The card goes in by its custom card id, as Archidekt's deck page adds it. With no category, "
    "Archidekt files it itself. The deck is read back to check."
)


def custom_add_box(csrf: str, st: dict[str, Any]) -> str:
    """Add to deck… on a custom card's row (the mockup's Add to deck box): one of the member's
    decks (read as the box opens, static/lists.js; drawn without scripts, the list is in the
    page), how many copies and, once a deck is picked, one of its categories or the deck's
    default. The card is proposed for the deck (decks.propose_custom_cards) and, when nothing
    needs a second look, applied through the member's press here; otherwise it goes to its
    review page."""
    v = st.get("values") or {}
    name = v.get("name", "")
    rows = st.get("decks") or []
    picked = str(v.get("deck", ""))
    opts = "".join(
        f"<option value='{esc(str(d['id']))}'{' selected' if str(d['id']) == picked else ''}>{esc(d['name'])}"
        "</option>"
        for d in rows
    )
    qty = str(v.get("quantity") or "1")
    cat = str(v.get("category") or "")
    cat_opt = f"<option value='{esc(cat)}' selected>{esc(cat)}</option>" if cat else ""
    body = (
        f"<form id='cc-add-f' method='post' action='{PAGE}/custom' class='stack' novalidate>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='action' "
        f"value='add_to_deck'><input type='hidden' name='custom_id' value='{esc(str(v.get('id', '')))}'>"
        f"<input type='hidden' name='was_name' value='{esc(name)}'>"
        "<div class='field'><label for='cc-add-deck'>Deck</label><span class='sel'>"
        f"<select id='cc-add-deck' name='deck' required data-src='/api/decks/mine'>{opts}</select></span>"
        "</div>"
        "<div class='lp-two'>"
        "<div class='field'><label for='cc-add-qty'>Copies</label>"
        f"<input id='cc-add-qty' name='quantity' type='number' min='1' max='99' step='1' "
        f"inputmode='numeric' value='{esc(qty)}' required></div>"
        "<div class='field'><label for='cc-add-cat'>Category</label><span class='sel'>"
        "<select id='cc-add-cat' name='category' data-src='/api/v1/decks/{id}?cards=0'>"
        f"<option value=''>{esc(ADD_DEFAULT_CATEGORY)}</option>{cat_opt}</select></span></div>"
        "</div></form>"
        f"<p class='muted small'>{esc(ADD_CUSTOM_NOTE)}</p>" + box_note(st.get("error", ""))
    )
    return ui.box(
        "cc-add",
        f"Add “{name}” to a deck",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Add to deck", kind="primary", form="cc-add-f", icon_name="plus"),
        ],
        open=bool(st),
        close_href=f"{PAGE}/custom",
        cls="lp-box",
    ).replace("<div class='ui-box", "<div data-t='Add “{name}” to a deck' class='ui-box", 1)


def face_fields(face: str, v: dict[str, Any], *, editing: bool, has_picture: bool) -> str:
    p = "f" if face == "front" else "b"
    req = face == "front"

    def fld(label: str, key: str, placeholder: str = "", limit: int = 200) -> str:
        return text_field(
            f"{p}-{key}",
            f"{p}_{key}",
            label,
            str(v.get(f"{face}{FACE_KEYS[key]}") or ""),
            placeholder=placeholder,
            limit=None,
            cls="lp-f",
        )

    name_label = "Name (required)" if req else "Name"
    types_label = "Types (at least one)" if req else "Types"
    text = str(v.get(f"{face}Text") or "")
    return (
        f"<div class='lp-face' id='cc-face-{p}'{'' if req else ' hidden'}>"
        f"<div class='lp-two'>{fld(name_label, 'n')}{fld('Mana cost', 'm', '{2}{G}')}</div>"
        f"<div class='lp-three'>{fld(types_label, 't', 'Creature')}{fld('Subtypes', 'st', 'Human Advisor')}"
        f"{fld('Supertypes', 'su', 'Legendary')}</div>"
        f"<div class='field'><label for='{p}-x'>Rules text</label><textarea id='{p}-x' name='{p}_x' rows='3' "
        f"maxlength='{account_lists.CUSTOM_TEXT_MAX}'>{esc(text)}</textarea></div>"
        f"<div class='lp-three'>{fld('Power', 'p')}{fld('Toughness', 'tg')}{fld('Loyalty', 'l')}</div>"
        "<div class='lp-img'><div class='lp-crop' "
        f"data-crop='{p}'>{'Picture set' if has_picture else 'No picture yet'}</div><div>"
        f"<p class='lbl'>Picture</p>"
        + (
            f"<input type='file' id='{p}-file' name='picture' form='cc-pic-f' "
            f"accept='image/jpeg,image/png,image/webp' class='lp-file' data-face='{face}'>"
            f"<button type='button' class='btn-plain lp-pick' data-for='{p}-file'>{icon('image')} Choose a "
            "picture…</button>"
            f"<noscript><button type='submit' form='cc-pic-f' name='face' value='{face}' "
            "class='btn-plain'>Upload picture</button></noscript>"
            if editing
            else "<button type='button' class='btn-plain lp-pick' disabled title='Create the card "
            f"first'>{icon('image')} Choose a picture…</button>"
        )
        + "<p class='muted small lp-pichelp'>JPEG, PNG or WebP up to 1.5 MB. You crop it, and it is saved as "
        "a 500 × 700 PNG.</p>"
        + ("" if editing else "<p class='muted small'>Create the card first, then choose its picture.</p>")
        + f"{fld('Artist', 'a')}</div></div></div>"
    )


def custom_box(csrf: str, st: dict[str, Any]) -> str:
    v = st.get("values") or {}
    editing = st.get("mode") == "edit"
    name = str(v.get("frontName") or "")
    has_back = bool(v.get("hasBack"))
    body = (
        f"<form id='cc-f' method='post' action='{PAGE}/custom'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='action' value='{'edit' if editing else 'create'}'>"
        f"<input type='hidden' name='was_name' value='{esc(v.get('was_name', ''))}'>"
        "<div class='segc lp-faces' role='tablist' aria-label='Face'>"
        "<button type='button' role='tab' data-face='f' aria-selected='true' id='cc-tab-f' "
        "aria-controls='cc-face-f'>Front</button>"
        "<button type='button' role='tab' data-face='b' aria-selected='false' id='cc-tab-b' "
        f"aria-controls='cc-face-b'{'' if has_back else ' disabled'}>Back</button></div>"
        + face_fields("front", v, editing=editing, has_picture=bool(v.get("frontImageUrl")))
        + face_fields("back", v, editing=editing, has_picture=bool(v.get("backImageUrl")))
        + switch("has_back", "Has a back face", has_back, fid="cc-back")
        + "<div class='lp-two'>"
        + text_field("cc-set", "set_code", "Set code", str(v.get("setCode") or ""), limit=None, cls="lp-f")
        + text_field(
            "cc-num",
            "collector_number",
            "Collector number",
            str(v.get("collectorNumber") or ""),
            limit=None,
            cls="lp-f",
        )
        + "</div>"
        + "<p class='notice error' id='cc-err' role='alert' hidden></p>"
        + hist("A copy of your custom cards from just before")
        + "</form>"
        + (
            f"<form id='cc-pic-f' method='post' action='{PAGE}/custom/picture' enctype='multipart/form-data' "
            "hidden>"
            f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='name' "
            f"value='{esc(v.get('was_name', ''))}'>"
            "<input type='hidden' name='face' value='front'><input type='hidden' name='crop' value=''></form>"
            if editing
            else ""
        )
        + box_note(st.get("error", ""))
    )
    return ui.box(
        "cc-box",
        f"Edit “{name}”" if editing else "New custom card",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(
                "Save card" if editing else "Create card", kind="primary", form="cc-f", icon_name="check"
            ),
        ],
        wide=True,
        open=bool(st),
        close_href=f"{PAGE}/custom",
        cls="lp-box lp-ccbox",
    ).replace(
        "<div class='ui-box",
        "<div data-t-new='New custom card' data-t-edit='Edit “{name}”' data-ok-new='Create card' "
        "data-ok-edit='Save card' class='ui-box",
        1,
    )


def custom_delete_box(csrf: str, st: dict[str, Any]) -> str:
    name = (st.get("values") or {}).get("name", "")
    body = (
        f"<form id='cc-del-f' method='post' action='{PAGE}/custom'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'><input type='hidden' name='action' "
        "value='delete'>"
        "<input type='hidden' name='confirmed' value='1'><input type='hidden' name='was_name' "
        f"value='{esc(name)}'>"
        + ui.notice(
            "<b>In every deck that has it, the card becomes a blank.</b> It can't be edited again, and the "
            "blank record stays on Archidekt. Archidekt has no undo for this.",
            "warn",
            html=True,
            cls="lp-warn",
        )
        + "<label class='check lp-check'><input type='checkbox' name='understood' value='1' required "
        "data-gate='cc-del'>"
        "<span>I understand it stays as a blank card in those decks</span></label></form>"
        + hist("A copy of your custom cards from just before")
        + box_note(st.get("error", ""))
    )
    return ui.box(
        "cc-del",
        f"Delete “{name}”?",
        body,
        actions=[
            ui.box_button("Keep it", cancel=True),
            ui.box_button(
                "Delete this custom card",
                kind="danger-solid",
                form="cc-del-f",
                icon_name="trash",
                attrs="data-gated",
            ),
        ],
        open=bool(st),
        close_href=f"{PAGE}/custom",
        cls="lp-box",
    ).replace("<div class='ui-box", "<div data-t='Delete “{name}”?' class='ui-box", 1)


# -- the page --------------------------------------------------------------------------------------------

CSS = """
.lp .list-head{margin-top:.25rem}
.lp .list-head .acts{display:flex;gap:8px;flex-wrap:wrap}
.lp .list-head .acts .btn{margin:0}
.lp-tabs{margin:0 0 1rem}
.lp-tabs a{white-space:normal;text-align:center;line-height:1.15;padding:.2rem .4rem;gap:.35rem}
.lp-tabs a .n{font-weight:400;color:var(--text-muted);margin-left:.1rem}
.lp-tabs a[aria-current] .n{color:inherit}
.lp .ui-table .tr{min-height:52px}
.lp .ui-table .tr.lp-empty{display:block}
.lp .title{min-width:0}
.lp .title .t{display:block;font-weight:700;overflow-wrap:anywhere}
.lp .title .s{display:block;font-weight:400;color:var(--text-muted);font-size:.86rem}
.lp .flags{display:flex;flex-wrap:wrap;gap:.3rem}
.lp .cardc{display:flex;align-items:center;gap:.6rem;min-width:0}
.lp .cardc .tx{min-width:0;flex:1}
.lp-thumb{display:block;width:34px;height:47px;border-radius:3px;border:1px solid var(--border);flex:none;
  object-fit:cover;background:linear-gradient(160deg,var(--surface-3),var(--surface-2))}
.lp-chip{display:inline-flex;align-items:center;gap:.25rem;border:1px solid var(--border);border-radius:999px;
  padding:.1rem .6rem;font-size:.86rem;line-height:1.5;white-space:nowrap;color:var(--text)}
.lp-chip.main{border-color:var(--orange-text); color:var(--orange-text); background:var(--orange-tint);
  font-weight:700}
.lp-chip svg.i{width:14px;height:14px;fill:currentColor;stroke:currentColor}
.lp-swatch{display:inline-block; width:22px; height:22px; border-radius:50%; border:1px solid rgba(0,0,0,.35);
  flex:none;
  vertical-align:middle}
.lp-tagc{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}
.lp-tagc .t{display:inline}
.lp .ui-table code{font-size:.9rem}
.lp .act{justify-self:end;align-self:center}
.lp .act .lp-dd > summary.kebab{display:inline-flex; align-items:center; justify-content:center; width:44px;
  min-height:44px;
  padding:0; border:0; border-radius:var(--radius); background:transparent; color:var(--text-muted);
    cursor:pointer}
.lp .act .lp-dd > summary.kebab:hover,
.lp .act .lp-dd > summary.kebab:focus-visible{background:var(--surface-2);color:var(--text)}
.lp .act .lp-dd > summary.kebab svg.i{width:20px;height:20px;fill:currentColor;stroke:currentColor}
.lp .lp-dd .menu{min-width:220px;max-width:min(20rem,calc(100vw - 2rem))}
.lp .lp-dd .menu a{min-height:44px}
.lp .lp-dd .menu svg.i{width:18px; height:18px; color:var(--orange); fill:none; stroke:currentColor;
  stroke-width:1.8}
.lp .ui-table .sm-only.flags{display:none}
.lp-under{display:flex; justify-content:space-between; align-items:center; gap:.5rem; margin-top:.2rem;
  min-height:1.2rem}
.lp-under .c{margin-left:auto}
.lp-hist{margin:.5rem 0 0}
.lp-hist svg.i,.lp-chip svg.i{vertical-align:-2px}
.lp-hist svg.i{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:2}
.lp-box .field{margin:0 0 .25rem}
.lp-box .field > label{margin:0 0 .3rem}
.lp-box .lp-warn{margin:.75rem 0}
.lp-box .body p.muted{margin:.6rem 0 0}
.lp-box .notice.boxnote{margin-top:.75rem}
.lp-switches{display:grid;margin:.5rem 0 0}
.lp-switches .lp-switch{margin:0;min-height:44px;border-top:1px solid var(--border-soft)}
.lp-switches .lp-switch:first-child{border-top:0}
.lp-defpick label,.lp-defpick label:has(> input[type=checkbox]){display:grid;
  grid-template-columns:auto minmax(0,1fr) auto; align-items:center; gap:.6rem;
  min-height:44px;
  margin:0;padding:0 .3rem;border-top:1px solid var(--border-soft);font-weight:400}
.lp-defpick label:first-child{border-top:0}
.lp-defpick input[type=checkbox]{width:20px;height:20px;min-height:0;margin:0;accent-color:var(--orange-ctl)}
.lp-defpick .main{display:inline-flex; align-items:center; gap:.3rem; font-size:.86rem;
  color:var(--text-muted); min-height:44px; cursor:pointer}
.lp-defpick .main input{width:18px;height:18px;min-height:0;margin:0;accent-color:var(--orange-ctl)}
.lp-defpick .main svg.i{width:14px;height:14px;fill:none;stroke:currentColor}
.lp-defpick p{margin:0;padding:.5rem 0}
.lp-tagpv{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}
.lp-tagpv input[type=color]{width:56px; height:var(--ctl); min-width:56px; padding:2px;
  border:1px solid var(--control-border);
  border-radius:var(--radius);background:var(--surface);cursor:pointer}
.lp-tagpv .hex{flex:0 0 8rem;width:8rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.lp-tagpv .lp-chip{flex:0 1 auto;max-width:100%;overflow:hidden}
.lp-tagpv .lp-chip .nm{overflow:hidden;text-overflow:ellipsis}
.lp-step{display:inline-flex;align-items:center;gap:4px;margin:0}
.lp-step button{width:44px;min-width:44px;min-height:44px;padding:0;margin:0}
.lp-step button svg.i{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2}
.lp-step .n{min-width:2ch;text-align:center;font-variant-numeric:tabular-nums}
.lp-note{margin:0;display:grid;gap:.1rem;width:100%}
.lp-note textarea{min-height:44px;font-size:.92rem;padding:.4rem .6rem;resize:vertical;margin:0}
.lp-note .lp-under{margin:0;justify-content:flex-end}
.lp-note .c{font-size:.78rem}
.lp-copies,.lp-notes{min-width:0}
.lp-pin .tr{align-items:center}
.lp-cc .lp-setnum{white-space:nowrap}
.lp-faces{display:flex}
.lp-faces [role=tab]{flex:1 1 auto}
.lp-box .body .segc.lp-faces{margin:0 0 .75rem}
.lp-face{display:grid;grid-template-columns:minmax(0,1fr);gap:.5rem}
.lp-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,10rem),1fr));gap:.25rem 12px}
.lp-three{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,6rem),1fr));gap:.25rem 12px}
.mtgbox.lp-box .body textarea{min-height:5rem;font-family:inherit;font-size:.95rem;line-height:1.4}
.mtgbox .body .lp-two .field + .field,.mtgbox .body .lp-three .field + .field{margin-top:0}
.lp-two .field,.lp-three .field{margin-bottom:.5rem}
.lp-img{display:grid; grid-template-columns:auto minmax(0,1fr); gap:12px; align-items:start;
  margin:.25rem 0 .5rem}
.lp-img .lbl{font-weight:700;margin:0 0 .3rem}
.lp-img .lp-pichelp{margin:.35rem 0 .5rem}
/* the Artist row under the picture's help line: the same .5rem the other rows keep (the shared
   box rule .mtgbox .body .field{margin:0} would otherwise win over .lp-img .field) */
.mtgbox .body .lp-img .field,.lp-img .field{margin-top:.5rem}
.lp-crop{aspect-ratio:5/7; width:min(12rem,100%); border:2px dashed var(--control-border);
  border-radius:4.5%/3.2%;
  display:grid;place-items:center;text-align:center;color:var(--text-muted);font-size:.86rem;padding:.5rem;
  background:linear-gradient(160deg,var(--surface-3),var(--surface-2));overflow:hidden;position:relative}
.lp-crop img{position:absolute; left:0; top:0; max-width:none; user-select:none; -webkit-user-drag:none;
  touch-action:none}
.lp-crop.has{border-style:solid;cursor:grab}
.lp-cropper{display:grid;gap:.5rem;margin:.25rem 0 .5rem}
.lp-cropper .lp-zoom{display:flex;align-items:center;gap:.5rem;margin:0}
.lp-cropper .lp-zoom input{flex:1;margin:0;min-height:44px}
.lp-cropper .acts{display:flex;gap:.5rem;flex-wrap:wrap}
.lp-file{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden}
.lp-check{margin:.75rem 0 0}
.lp-check input{width:20px;height:20px;min-height:0;margin:0;accent-color:var(--orange-ctl);flex:none}
.lp .lp-switch{margin:.5rem 0 .25rem;min-height:44px}
.lp-ccbox .mtgbox .body,.lp-ccbox .body{overflow-x:hidden}
@media (min-width:600px) and (max-width:899.98px){
  .lp-pin .tr:not(.th){grid-template-columns:var(--cols-md);
    grid-template-areas:"card copies menu" "notes notes notes"}
  .lp-pin .tr:not(.th) > .title{grid-area:card} .lp-pin .tr:not(.th) > .lp-copies{grid-area:copies}
  .lp-pin .tr:not(.th) > .lp-notes{grid-area:notes; margin-top:.4rem}
    .lp-pin .tr:not(.th) > .act{grid-area:menu}
  .lp-pin .th .lp-notes-h{display:none}
  .lp-cc .tr{grid-template-columns:var(--cols-md)} .lp-cc .lp-setnum{display:none}
}
@media (max-width:599.98px){
  .lp .ui-table .sm-hide{display:none}
  .lp .ui-table .tr{padding:.55rem .75rem}
  .lp .ui-table .sm-only.flags{display:flex; gap:.3rem; flex-wrap:wrap; margin-top:.3rem; font-size:inherit;
    color:inherit; flex-basis:auto}
  .lp .ui-table .sm-only{display:block;color:var(--text-muted);font-size:.86rem;font-weight:400}
  .lp .ui-table .sm-only code{font-size:.84rem}
  .lp-tagc .sm-only{flex-basis:auto}
  .lp-pin .tr:not(.th){grid-template-columns:minmax(0,1fr) 44px;
    grid-template-areas:"card menu" "copies copies" "notes notes"}
  .lp-pin .tr:not(.th) > .title{grid-area:card} .lp-pin .tr:not(.th) > .lp-copies{grid-area:copies;
    margin:.4rem 0 0 calc(34px + .6rem)}
  .lp-pin .tr:not(.th) > .lp-notes{grid-area:notes; margin:.4rem 0 0 calc(34px + .6rem)}
    .lp-pin .tr:not(.th) > .act{grid-area:menu}
  .lp .list-head .acts{flex:1 1 auto;justify-content:flex-end}
  .lp-img{grid-template-columns:minmax(0,1fr)}
}
@media (pointer:coarse){ .lp-defpick .main{min-height:44px} .lp-tagpv input[type=color]{height:44px} }
"""


def add_lists_routes(server: Any, state: Any, h: SimpleNamespace) -> None:
    decks = h.decks
    page = h.page
    check_csrf = h.check_csrf
    s = h.s

    def respond(
        body: str, sub: str, sid: str | None, *, status: int = 200, scripts: tuple[str, ...] = ("lists.js",)
    ) -> Response:
        return page(
            TITLE,
            body,
            sub=sub,
            sid=sid,
            status=status,
            current=PAGE,
            csp=LISTS_CSP,
            heading=False,
            extra_css=CSS,
            extra_scripts=("printpick.js", *scripts),
            main_class="ui-wide",
        )

    def wants_json(request: Request) -> bool:
        return "application/json" in (request.headers.get("accept") or "")

    def top_notice(q: Any) -> str:
        ok = q.get("ok") or ""
        if ok == "unconfirmed":
            pid = q.get("p") or ""
            link = (
                f"<a href='/proposals/{esc(pid)}'>review page</a>"
                if re.fullmatch(r"[A-Za-z0-9_-]{4,40}", pid)
                else "review page"
            )
            return ui.notice(esc(UNCONFIRMED).replace("review page", link), "warn", html=True, role="status")
        text = notice_text(ok, (q.get("n") or "")[:200])
        return ui.notice(text, "ok", role="status") if text else ""

    async def read_tab(sub: str, tab: Tab, *, page_no: int = 1, q: str = "") -> dict[str, Any]:
        return await account_lists.read(
            decks, sub, tab.kind, page=page_no, search=q if tab.kind == "custom_cards" else ""
        )

    def by_id(items: list[dict[str, Any]], raw: str | None, key: str = "id") -> dict[str, Any] | None:
        if not raw or not _ID.fullmatch(raw):
            return None
        want = int(raw)
        return next((i for i in items if i.get(key) == want), None)

    def box_from_query(tab: Tab, got: dict[str, Any], q: Any) -> dict[str, Any]:
        """The box an old-style address asks to draw open, with the row's values."""
        items = got["items"]
        if tab.slug == "templates":
            if "new" in q:
                return {"box": "tpl-box", "mode": "new", "values": {}}
            if t := by_id(items, q.get("edit")):
                return {"box": "tpl-box", "mode": "edit", "values": {**t, "was_name": t["name"]}}
            if t := by_id(items, q.get("delete")):
                return {"box": "tpl-del", "values": t}
            if t := by_id(items, q.get("add")):
                return {"box": "tpl-deck", "values": t}
        elif tab.slug == "defaults":
            if d := by_id(items, q.get("edit"), "oracle_id"):
                return {"box": "def-box", "values": d}
            if d := by_id(items, q.get("clear"), "oracle_id"):
                return {"box": "def-clear", "values": d}
        elif tab.slug == "tags":
            if "new" in q:
                return {"box": "tag-box", "mode": "new", "values": {}}
            if t := by_id(items, q.get("edit")):
                return {"box": "tag-box", "mode": "edit", "values": {**t, "was_name": t["name"]}}
            if t := by_id(items, q.get("delete")):
                return {"box": "tag-del", "values": t}
        elif tab.slug == "pins":
            if p := by_id(items, q.get("remove")):
                return {"box": "pin-rm", "values": p}
            if "remove_all" in q and items:
                return {"box": "pin-all", "values": {"n": len(items)}}
        else:
            if "new" in q:
                return {"box": "cc-box", "mode": "new", "values": {}}
            if c := by_id(items, q.get("edit")):
                return {"box": "cc-box", "mode": "edit", "values": {**c, "was_name": c["name"]}}
            if c := by_id(items, q.get("delete")):
                return {"box": "cc-del", "values": c}
            if c := by_id(items, q.get("add")):
                return {"box": "cc-add", "values": c}
        return {}

    def tab_body(
        tab: Tab, got: dict[str, Any], csrf: str, box: dict[str, Any], q: str, counts: dict[str, int]
    ) -> str:
        counts = {**counts, tab.kind: int(got["total"])}
        if tab.slug == "templates":
            inner = templates_tab(got, csrf, box)
        elif tab.slug == "defaults":
            inner = defaults_tab(got, csrf, box, q)
        elif tab.slug == "tags":
            inner = tags_tab(got, csrf, box)
        elif tab.slug == "pins":
            inner = pins_tab(got, csrf, box)
        else:
            inner = custom_tab(got, csrf, box, q)
        return f"<div class='lp lp-{tab.slug}' data-tab='{tab.slug}'>{inner}</div>"

    def page_body(
        tab: Tab, got: dict[str, Any], csrf: str, box: dict[str, Any], q: str, sub: str, notice: str = ""
    ) -> str:
        counts = {**account_lists.counts(sub), tab.kind: int(got["total"])}
        return (
            ui.page_head(TITLE, SUB_TEXT)
            + tabs_html(tab.slug, counts)
            + notice
            + tab_body(tab, got, csrf, box, q, counts)
        )

    def problem(request: Request, exc: DeckError, sub: str, sid: str | None, tab: Tab) -> Response:
        if busy := busy_response(exc, request, page, sub=sub, sid=sid):
            return busy
        if exc.kind == "not_linked":
            body = (
                "<div class='card'><h2>Link your Archidekt account</h2><p>Your lists appear here once "
                "Archidekt is "
                "linked.</p><div class='actions'><a class='btn btn-primary' href='/account'>Link "
                "Archidekt</a></div></div>"
            )
            return respond(ui.page_head(TITLE, SUB_TEXT) + body, sub, sid, status=200)
        body = (
            ui.page_head(TITLE, SUB_TEXT)
            + tabs_html(tab.slug, account_lists.counts(sub))
            + ui.notice(str(exc), "error", role="alert")
        )
        return respond(body, sub, sid, status=400 if exc.kind == "invalid" else 502)

    def q_of(q: Any) -> str:
        return (q.get("q") or "").strip()[:100]

    def page_of(q: Any) -> int:
        raw = q.get("page") or "1"
        return int(raw) if raw.isdigit() and 1 <= int(raw) <= 1000 else 1

    @server.custom_route(PAGE, methods=["GET"], include_in_schema=False)
    async def lists_home(request: Request) -> Response:
        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect(PAGE)
        return RedirectResponse(f"{PAGE}/templates", status_code=302)

    @server.custom_route(PAGE + "/{tab}", methods=["GET"], include_in_schema=False)
    async def lists_tab(request: Request) -> Response:
        tab = BY_SLUG.get(request.path_params["tab"])
        if tab is None:
            return RedirectResponse(f"{PAGE}/templates", status_code=302)
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"{PAGE}/{tab.slug}")
        q = request.query_params
        search, page_no = q_of(q), page_of(q)
        try:
            got = await read_tab(sub, tab, page_no=page_no, q=search)
        except DeckError as exc:
            if wants_json(request):
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
            return problem(request, exc, sub, sid, tab)
        if wants_json(request) and tab.slug in ("defaults", "custom"):
            # Load more with scripts: the next page's rows, appended in place (static/lists.js)
            if tab.slug == "defaults":
                items = [d for d in got["items"] if not search or search.casefold() in d["name"].casefold()]
                html = defaults_rows(items)
                shown = min(got["total"], page_no * account_lists.DEFAULT_PAGE_SIZE)
            else:
                html = custom_rows(got["items"])
                shown = len(got["items"]) + (page_no - 1) * 50
            return JSONResponse(
                {
                    "ok": True,
                    "html": html,
                    "has_more": bool(got["more"]),
                    "next": tab_href(tab.slug, page=page_no + 1, q=search),
                    "shown": shown,
                    "total": got["total"],
                    "label": f"Load more ({shown:,} of {got['total']:,} shown)",
                    "done": f"All {got['total']:,} shown.",
                }
            )
        box = box_from_query(tab, got, q)
        if box.get("box") in ("tpl-deck", "cc-add"):  # the deck list, for the box drawn open without scripts
            box["decks"] = await decks_or_none(sub)
        body = page_body(tab, got, _csrf(s, sid) or "", box, search, sub, top_notice(q))
        return respond(body, sub, sid)

    async def decks_or_none(sub: str) -> list[dict[str, Any]]:
        try:
            return sorted(await decks.list_decks(sub), key=lambda d: (d.get("name") or "").casefold())
        except DeckError:
            return []

    async def add_template_to_deck(
        request: Request, sub: str, sid: str | None, data: dict[str, str]
    ) -> Response:
        """Add to a deck… on a template's row: one category from the template, proposed for the
        deck (decks.propose_categories) and sent to its review page, where the member applies it."""
        tab = BY_SLUG["templates"]
        name = (data.get("was_name") or "").strip()
        deck_id = (data.get("deck") or "").strip()
        try:
            if not deck_id:
                raise DeckError("invalid", "Pick a deck first.")
            current_client.set(BROWSER_CLIENT_ID)
            changes = await account_lists.from_templates(
                decks, sub, [{"action": "create", "from_template": name}]
            )
            p = await decks.propose_categories(sub, deck_id, changes)
        except DeckError as exc:
            if wants_json(request):
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
            try:
                got = await read_tab(sub, tab)
            except DeckError as again:
                return problem(request, again, sub, sid, tab)
            box = {
                "box": "tpl-deck",
                "values": {"name": name, "deck": deck_id},
                "error": str(exc),
                "decks": await decks_or_none(sub),
            }
            body = page_body(tab, got, _csrf(s, sid) or "", box, "", sub)
            return respond(body, sub, sid, status=400)
        where = f"/proposals/{p['proposal_id']}"
        if wants_json(request):
            return JSONResponse({"ok": True, "location": where})
        return RedirectResponse(where, status_code=303)

    async def add_custom_to_deck(
        request: Request, sub: str, sid: str | None, data: dict[str, str]
    ) -> Response:
        """Add to deck… on a custom card's row: the card proposed for the deck by its custom card
        id (decks.propose_custom_cards) and applied through this press, as the deck page applies
        its own custom card edits (api.custom_cards_edit); a change that needs a second look
        (modes.hand_edit_confirm: a commander-zone category) or writes being off send the member
        to the review page instead."""
        tab = BY_SLUG["custom"]
        name = (data.get("was_name") or "").strip()
        deck_id = (data.get("deck") or "").strip()
        raw_id = (data.get("custom_id") or "").strip()  # not "id": a field of that name shadows form.id
        raw_qty = (data.get("quantity") or "1").strip()
        category = (data.get("category") or "").strip()
        try:
            if not _ID.fullmatch(raw_id):
                raise DeckError(
                    "invalid", "That custom card is not on this page any more. Reload and try again."
                )
            if not deck_id:
                raise DeckError("invalid", "Pick a deck first.")
            if not re.fullmatch(r"[0-9]{1,2}", raw_qty) or int(raw_qty) < 1:
                raise DeckError("invalid", "Copies must be a whole number from 1 to 99.")
            change: dict[str, Any] = {
                "action": "add",
                "custom_card_id": int(raw_id),
                "quantity": int(raw_qty),
            }
            if category:
                change["category"] = category
            current_client.set(BROWSER_CLIENT_ID)
            p = await decks.propose_custom_cards(sub, deck_id, [change])
            pid = p["proposal_id"]
            decks.keep_no_backup(sub, pid)
            why = modes.hand_edit_confirm("custom_cards", p.get("rows"))
            if why or not s.writes_enabled:
                where = f"/proposals/{pid}"
                if wants_json(request):
                    return JSONResponse({"ok": True, "location": where})
                return RedirectResponse(where, status_code=303)
            out = await decks.apply(sub, pid, via="browser", archidekt_backup=False)
        except DeckError as exc:
            if wants_json(request):
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
            try:
                got = await read_tab(sub, tab)
            except DeckError as again:
                return problem(request, again, sub, sid, tab)
            box = {
                "box": "cc-add",
                "values": {
                    "name": name,
                    "id": raw_id,
                    "deck": deck_id,
                    "quantity": raw_qty,
                    "category": category,
                },
                "error": str(exc),
                "decks": await decks_or_none(sub),
            }
            body = page_body(tab, got, _csrf(s, sid) or "", box, "", sub)
            return respond(body, sub, sid, status=400)
        if out.get("state") != "applied":  # still going on Archidekt: the review page shows how far it got
            where = f"/proposals/{pid}"
            if wants_json(request):
                return JSONResponse({"ok": True, "location": where})
            return RedirectResponse(where, status_code=303)
        return done(request, tab, {"proposal_id": pid, "result": out.get("result")}, "card_added", name)

    # -- the member's own press --------------------------------------------------------------------

    def flag(data: dict[str, str], key: str) -> bool:
        return data.get(key) == "1"

    def change_of(tab: Tab, data: dict[str, str]) -> tuple[dict[str, Any], str, str]:
        """(the change for account_lists, the notice code, the box this form came from)."""
        action = data.get("action") or ""
        was = (data.get("was_name") or "").strip()
        name = (data.get("name") or "").strip()
        if tab.slug == "templates":
            flags = {k: flag(data, k) for k, _w, _l in FLAGS}
            if action == "create":
                return {"op": "create", "name": name, **flags}, "template_created", "tpl-box"
            if action == "edit":
                c: dict[str, Any] = {"op": "edit", "name": was, **flags}
                if name and name != was:
                    c["new_name"] = name
                return c, "template_saved", "tpl-box"
            if action == "delete":
                return {"op": "delete", "name": was}, "template_deleted", "tpl-del"
        elif tab.slug == "defaults":
            card = (data.get("card") or "").strip()
            if action == "set":
                picked = [v for v in data.get("cat_list", "").split("\n") if v]
                main = (data.get("main") or "").strip()
                if main and main in picked:
                    picked.remove(main)
                    picked.insert(0, main)
                elif main and not picked:
                    picked = [main]
                if not picked:
                    raise DeckError(
                        "invalid", "Tick at least one category, or use Clear… to remove them all."
                    )
                return {"op": "set", "card": card, "categories": picked}, "defaults_saved", "def-box"
            if action == "clear":
                return {"op": "clear", "card": card}, "defaults_cleared", "def-clear"
        elif tab.slug == "tags":
            color = (data.get("color") or "").strip()
            if action == "create":
                return {"op": "create", "name": name, "color": color or None}, "tag_created", "tag-box"
            if action == "edit":
                c = {"op": "edit", "name": was, "color": color or None}
                if name and name != was:
                    c["new_name"] = name
                return c, "tag_saved", "tag-box"
            if action == "delete":
                return {"op": "delete", "name": was}, "tag_deleted", "tag-del"
        elif tab.slug == "pins":
            pid = data.get("pin_id") or ""
            pin_id = int(pid) if _ID.fullmatch(pid) else None
            if action == "edit":
                c = {"op": "edit", "pin_id": pin_id}
                if "quantity" in data:
                    qty = data["quantity"]
                    c["quantity"] = int(qty) if re.fullmatch(r"-?[0-9]{1,4}", qty) else qty
                if "notes" in data:
                    c["notes"] = data["notes"]
                if data.get("set_code"):
                    c["set_code"] = data["set_code"].strip()[:10]
                    c["collector_number"] = (data.get("collector_number") or "").strip()[:20]
                if data.get("finish"):
                    c["finish"] = data["finish"]
                return c, "pin_saved", ""
            if action == "unpin":
                return {"op": "unpin", "pin_id": pin_id}, "pin_removed", "pin-rm"
            if action == "remove_all":
                return {"op": "remove_all"}, "pins_removed", "pin-all"
        else:
            if action == "delete":
                return {"op": "delete", "name": was}, "card_deleted", "cc-del"
            if action in ("create", "edit"):
                fields = custom_fields_of(data)
                if action == "create":
                    return {"op": "create", "fields": fields}, "card_created", "cc-box"
                return {"op": "edit", "name": was, "fields": fields}, "card_saved", "cc-box"
        raise DeckError("invalid", "That is not something this page can do.")

    def custom_fields_of(data: dict[str, str]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        has_back = flag(data, "has_back")
        for p, face in (("f", "front"), ("b", "back")):
            for key, suffix in FACE_KEYS.items():
                raw = data.get(f"{p}_{key}", "")
                if p == "b" and not has_back:
                    raw = ""
                fields[f"{face}{suffix}"] = raw.strip() or None
        fields["hasBack"] = has_back
        fields["setCode"] = (data.get("set_code") or "").strip() or None
        fields["collectorNumber"] = (data.get("collector_number") or "").strip() or None
        fields["cmc"] = cmc_of(fields.get("frontManaCost") or "")
        return fields

    def posted_box(tab: Tab, box_id: str, data: dict[str, str], error: str) -> dict[str, Any]:
        """The box drawn open again with what was typed and the refusal."""
        action = data.get("action") or ""
        mode = "edit" if action in ("edit", "set") else "new"
        v: dict[str, Any] = {k: v for k, v in data.items() if k != "csrf"}
        v["name"] = v.get("name") or v.get("was_name") or v.get("card") or ""
        for k, _w, _l in FLAGS:
            v[k] = flag(data, k)
        if box_id == "def-box":
            picked = [x for x in data.get("cat_list", "").split("\n") if x]
            main = data.get("main") or ""
            if main in picked:
                picked.remove(main)
                picked.insert(0, main)
            v["categories"] = picked
        if box_id == "cc-box":
            fields = {}
            for p, face in (("f", "front"), ("b", "back")):
                for key, suffix in FACE_KEYS.items():
                    fields[f"{face}{suffix}"] = data.get(f"{p}_{key}", "")
            v.update(fields)
            v["frontName"] = data.get("f_n", "")
            v["hasBack"] = flag(data, "has_back")
            v["setCode"] = data.get("set_code", "")
            v["collectorNumber"] = data.get("collector_number", "")
        if box_id == "pin-rm":
            v["id"] = data.get("pin_id", "")
        return {"box": box_id, "mode": mode, "values": v, "error": error}

    async def fail(
        request: Request,
        tab: Tab,
        exc: DeckError,
        sub: str,
        sid: str | None,
        box_id: str,
        data: dict[str, str],
    ) -> Response:
        if wants_json(request):
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        try:
            got = await read_tab(sub, tab, q=q_of(request.query_params))
        except DeckError as again:
            return problem(request, again, sub, sid, tab)
        if box_id == "pin-rm":  # the row's values, for the name in the title
            p = by_id(got["items"], data.get("pin_id"))
            data = {**data, "name": p["name"] if p else ""}
        box = posted_box(tab, box_id, data, str(exc)) if box_id else {}
        notice = ui.notice(str(exc), "error", role="alert") if not box_id else ""
        body = page_body(tab, got, _csrf(s, sid) or "", box, q_of(request.query_params), sub, notice)
        return respond(body, sub, sid, status=400)

    def done(
        request: Request, tab: Tab, out: dict[str, Any], code: str, name: str, *, q: str = ""
    ) -> Response:
        result = out.get("result") if isinstance(out.get("result"), dict) else {}
        params: dict[str, Any] = {"q": q} if q else {}
        if result.get("verified") is False:
            params.update(ok="unconfirmed", p=out.get("proposal_id") or "")
            text = UNCONFIRMED
        else:
            params.update(ok=code, n=name[:200] if "{name}" in NOTICES.get(code, "") else "")
            text = notice_text(code, name)
        if wants_json(request):
            return JSONResponse(
                {
                    "ok": True,
                    "notice": text,
                    "verified": result.get("verified"),
                    "proposal_id": out.get("proposal_id"),
                }
            )
        return RedirectResponse(tab_href(tab.slug, **params), status_code=303)

    @server.custom_route(PAGE + "/{tab}", methods=["POST"], include_in_schema=False)
    async def lists_post(request: Request) -> Response:
        tab = BY_SLUG.get(request.path_params["tab"])
        if tab is None:
            return RedirectResponse(f"{PAGE}/templates", status_code=302)
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"{PAGE}/{tab.slug}")
        raw = await read_limited(request, 64_000)
        fields = parse_qs((raw or b"").decode("utf-8", "replace"), keep_blank_values=True)
        data = {k: v[0] for k, v in fields.items()}
        if tab.slug == "defaults":
            # the checklist's ticks come as several cat= values
            data["cat_list"] = "\n".join(c.strip() for c in fields.get("cat") or [] if c.strip())
        if not check_csrf(sid, data):
            if wants_json(request):
                return JSONResponse(
                    {"ok": False, "message": "This form expired. Reload and try again."}, status_code=403
                )
            return page(TITLE, h.EXPIRED, sub=sub, sid=sid, status=403)
        if tab.slug == "templates" and data.get("action") == "add_to_deck":
            return await add_template_to_deck(request, sub, sid, data)
        if tab.slug == "custom" and data.get("action") == "add_to_deck":
            return await add_custom_to_deck(request, sub, sid, data)
        box_id = ""
        try:
            change, code, box_id = change_of(tab, data)
            # a delete, a clear, a pin below one copy and Remove all ask first: the box's own form
            # says confirmed=1; a plain press without scripts gets the box drawn open
            if tab.slug == "pins" and change.get("op") == "edit" and change.get("quantity") == 0:
                if data.get("confirmed") != "1":
                    return RedirectResponse(tab_href("pins", remove=data.get("pin_id")), status_code=303)
                change = {"op": "unpin", "pin_id": change["pin_id"]}
                code, box_id = "pin_removed", "pin-rm"
            if change["op"] in ("delete", "clear", "unpin", "remove_all") and data.get("confirmed") != "1":
                key = {
                    "templates": "delete",
                    "tags": "delete",
                    "custom": "delete",
                    "defaults": "clear",
                    "pins": "remove",
                }[tab.slug]
                ident = data.get("pin_id") or data.get("id") or ""
                if tab.slug == "pins" and change["op"] == "remove_all":
                    return RedirectResponse(tab_href("pins", remove_all=1), status_code=303)
                if not ident:  # the row's id is not on the form: find it by name
                    got = await read_tab(
                        sub,
                        tab,
                        q=(data.get("was_name") or data.get("card") or "")[:100]
                        if tab.slug == "custom"
                        else "",
                    )
                    want = (data.get("was_name") or data.get("card") or "").casefold()
                    hit = next((i for i in got["items"] if i["name"].casefold() == want), None)
                    ident = str((hit or {}).get("oracle_id" if tab.slug == "defaults" else "id") or "")
                return RedirectResponse(tab_href(tab.slug, **{key: ident}), status_code=303)
            if tab.slug == "custom" and change["op"] == "delete" and data.get("understood") != "1":
                raise DeckError("invalid", "Tick the box first: the card stays as a blank in those decks.")
            current_client.set(BROWSER_CLIENT_ID)
            out = await account_lists.hand_change(decks, sub, tab.kind, change)
        except DeckError as exc:
            return await fail(request, tab, exc, sub, sid, box_id, data)
        name = (
            change.get("new_name")
            or change.get("name")
            or data.get("card")
            or data.get("was_name")
            or (change.get("fields") or {}).get("frontName")
            or ""
        )
        return done(request, tab, out, code, str(name), q=q_of(request.query_params))

    @server.custom_route(PAGE + "/custom/picture", methods=["POST"], include_in_schema=False)
    async def picture_post(request: Request) -> Response:
        """The member's own picture for one face of a custom card: the file as chosen, with the
        crop box in its own pixels; the gateway checks it and redraws the crop (set_picture)."""
        tab = BY_SLUG["custom"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"{PAGE}/custom")
        posted = await limited_form(request, PICTURE_BODY_LIMIT, max_files=1, max_fields=20)
        if posted is TOO_BIG:
            exc = DeckError(
                "invalid", "The picture is larger than 1.5 MB, Archidekt's limit. Choose a smaller one."
            )
            return await fail(request, tab, exc, sub, sid, "", {})
        data: dict[str, str] = {}
        raw = b""
        if posted is not None:
            for k, v in posted.multi_items():
                if hasattr(v, "read"):
                    raw = await v.read()
                    if len(raw) > PICTURE_BODY_LIMIT:
                        raw = raw[: PICTURE_BODY_LIMIT + 1]
                else:
                    data[k] = str(v)
        if not check_csrf(sid, data):
            if wants_json(request):
                return JSONResponse(
                    {"ok": False, "message": "This form expired. Reload and try again."}, status_code=403
                )
            return page(TITLE, h.EXPIRED, sub=sub, sid=sid, status=403)
        name = (data.get("name") or "").strip()
        face = data.get("face") or "front"
        crop = None
        m = re.fullmatch(r"\s*(\d{1,6}),(\d{1,6}),(\d{1,6}),(\d{1,6})\s*", data.get("crop") or "")
        if m:
            crop = tuple(int(x) for x in m.groups())
        try:
            current_client.set(BROWSER_CLIENT_ID)
            out = await account_lists.set_picture(decks, sub, name, face, raw, crop)  # type: ignore[arg-type]
        except DeckError as exc:
            return await fail(
                request, tab, exc, sub, sid, "cc-box", {"action": "edit", "was_name": name, "f_n": name}
            )
        return done(request, tab, out, "picture_saved", name)
