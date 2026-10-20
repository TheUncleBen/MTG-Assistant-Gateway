"""The search panel card search, deck search and My decks share: the same control groups, built
here once, so the three pages look the same at every width.

A panel (``panel``) is one form: the name row (the search box with its search button, the
Filters button on phones, a cross-link at the end of the row where there is room), the filter
groups in a fluid row that fills the width (``filter_grid``), and the results bar at the panel's
foot (``results_bar``: the count, Sort with its direction, Grid or List, the card size and Clear
filters). The groups: today's colour buttons with the match drop-down right after them
(``colours_group``), a pick-several drop-down that opens a checkbox list and shows what is picked
(``pick_group``), one-choice drop-downs and text boxes. On phones the filter groups open in a
full-screen Filters sheet.

The look is theme.SEARCH_PANEL_CSS (site.css); static/searchpanel.js opens and closes the
drop-downs and the sheet. Every ``*_html`` argument is trusted markup the caller built; every
other text is escaped here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .mana import pip_html
from .theme import card_size_control, esc, icon

COLORS = ("W", "U", "B", "R", "G", "C")
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colorless"}
# the sort directions My decks and card search offer: the natural order, then either way
DIRECTIONS = (("auto", "Automatic"), ("asc", "Ascending"), ("desc", "Descending"))
# how a deck's colour identity matches the picked colours (My decks filters its own list)
DECK_COLOUR_MODES = (("incl", "Including these"), ("exact", "Exactly these"), ("atmost", "At most these"))
Option = tuple[str, str]  # (value, text)


def colour_match(colors: dict[str, Any] | None, chosen: str, mode: str) -> bool:
    """Whether a deck whose colour identity is ``colors`` (Archidekt's pip counts by letter)
    matches the picked letters: ``incl`` has every picked colour, ``exact`` exactly those, ``atmost``
    no other. C (colourless) picked alone means a deck with no colour."""
    deck = {k for k, v in (colors or {}).items() if k in "WUBRG" and v}
    want = {c for c in chosen.upper() if c in "WUBRG"}
    if mode == "exact":
        return deck == want
    if mode == "atmost":
        return deck <= want
    if not want and "C" in chosen.upper():
        return not deck
    return want <= deck


def options_html(options: Iterable[Option], chosen: str) -> str:
    return "".join(
        f"<option value='{esc(v)}'{' selected' if v == chosen else ''}>{esc(t)}</option>" for v, t in options
    )


# --- the name row ------------------------------------------------------------------------------


def name_row(
    *,
    label: str,
    name: str,
    value: str,
    placeholder: str,
    input_id: str,
    search_label: str,
    cross_html: str = "",
    under_html: str = "",
    attrs: str = "",
    filters_button: bool = True,
) -> str:
    """The panel's first row: the name box with its search button (``attrs`` adds attributes to
    the box), ``under_html`` right under the box (deck search's "Open this deck"), the Filters
    button phones open the sheet with, and ``cross_html`` (the cross-link) at the end of the row,
    hidden on phones."""
    fbtn = (
        "<button type='button' class='btn sp-open fbtn' aria-controls='sp-filters' aria-expanded='false'>"
        f"{icon('filter')} Filters <span class='n'></span></button>"
        if filters_button
        else ""
    )
    return (
        f"<div class='srow'><div class='sp-name'><label for='{esc(input_id)}'>{esc(label)}</label>"
        f"<span class='search'><input id='{esc(input_id)}' type='search' name='{esc(name)}' "
        f"value='{esc(value)}' placeholder='{esc(placeholder)}' autocomplete='off' "
        f"enterkeyhint='search'{attrs}>"
        f"<button type='submit' aria-label='{esc(search_label)}' title='{esc(search_label)}'>{icon('search')}"
        f"</button></span><div class='under'>{under_html}</div></div>{fbtn}{cross_html}</div>"
    )


def cross_link(text: str, href: str, icon_name: str) -> str:
    """The link to the other search at the end of the name row (hidden on phones)."""
    return f"<a class='btn xlink' href='{esc(href)}'>{icon(icon_name)} {esc(text)}</a>"


def open_hint_html(deck_id: str, href: str) -> str:
    """Under deck search's name box when an Archidekt link or deck ID was typed."""
    return (
        f"<div class='openhint'><span>Archidekt deck {esc(deck_id)}</span>"
        f"<a class='btn btn-primary' href='{esc(href)}'>{icon('external')} Open this deck</a></div>"
    )


# --- the filter groups -------------------------------------------------------------------------


def filter_grid(groups: Iterable[str]) -> str:
    """The groups in a fluid wrapping row: each at least its own width, growing to fill its row."""
    return f"<div class='fgrid'>{''.join(groups)}</div>"


def group(label: str, control_html: str, *, cls: str = "", for_id: str = "", hint: str = "") -> str:
    """One group: its label (a <label> when ``for_id`` names the control), ``hint`` next to the
    label in a quieter type, then the control."""
    extra = f" {esc(cls)}" if cls else ""
    tip = f" <span class='hint'>{esc(hint)}</span>" if hint else ""
    lab = (
        f"<label class='l' for='{esc(for_id)}'>{esc(label)}{tip}</label>"
        if for_id
        else f"<span class='l'>{esc(label)}{tip}</span>"
    )
    return f"<div class='fg{extra}'>{lab}{control_html}</div>"


def colour_toggle(name: str, value: str, checked: bool) -> str:
    """One of today's colour buttons: a pill with the mana symbol and the colour name."""
    return (
        f"<label class='tog'><input type='checkbox' name='{esc(name)}' value='{esc(value)}'"
        f"{' checked' if checked else ''}>{pip_html(value)}<span>{esc(COLOR_NAMES[value])}</span></label>"
    )


def colours_group(
    *,
    label: str,
    name: str,
    chosen: Sequence[str],
    mode_name: str,
    mode_options: Iterable[Option],
    mode_value: str,
    hint: str = "",
) -> str:
    """The Colours group: the six colour buttons, then the match drop-down right after them (under
    them when the group is narrow, three and three buttons on phones; theme.SEARCH_PANEL_CSS)."""
    pills = "".join(colour_toggle(name, c, c in chosen) for c in COLORS)
    tip = f" <span class='hint'>{esc(hint)}</span>" if hint else ""
    return (
        f"<fieldset class='fg colors'><legend class='l'>{esc(label)}{tip}</legend><div class='in'>"
        f"<div class='pills'>{pills}</div>"
        f"<span class='slot'><select name='{esc(mode_name)}' aria-label='How the {esc(label.lower())} match'>"
        f"{options_html(mode_options, mode_value)}</select></span></div></fieldset>"
    )


def pick_group(
    *, label: str, name: str, options: Iterable[Option], chosen: Sequence[str], none_text: str
) -> str:
    """A pick-several filter: one drop-down that opens a checkbox list and shows what is picked
    ("Rare, Mythic rare", or ``none_text`` such as "Any rarity"). A <details>, so it opens without
    scripts too; static/searchpanel.js adds Esc, the outside press and the phone sheet."""
    opts = list(options)
    picked = [t for v, t in opts if v in chosen]
    boxes = "".join(
        f"<label><input type='checkbox' name='{esc(name)}' value='{esc(v)}'"
        f"{' checked' if v in chosen else ''}><span>{esc(t)}</span></label>"
        for v, t in opts
    )
    return (
        f"<div class='fg'><span class='l' id='pl-{esc(name)}'>{esc(label)}</span>"
        f"<details class='pickm' data-none='{esc(none_text)}'>"
        f"<summary class='pickm-btn' aria-labelledby='pl-{esc(name)} pv-{esc(name)}'>"
        f"<span class='v' id='pv-{esc(name)}'>{esc(', '.join(picked) if picked else none_text)}</span>"
        f"{icon('chev')}</summary><div class='pickm-pop'>{boxes}</div></details></div>"
    )


def select_group(
    *, label: str, name: str, options: Iterable[Option], value: str, input_id: str, cls: str = ""
) -> str:
    """A one-choice drop-down."""
    sel = f"<select id='{esc(input_id)}' name='{esc(name)}'>{options_html(options, value)}</select>"
    return group(label, sel, for_id=input_id, cls=cls)


def text_group(
    *, label: str, name: str, value: str, input_id: str, placeholder: str = "", attrs: str = "", cls: str = ""
) -> str:
    box = (
        f"<input id='{esc(input_id)}' type='text' name='{esc(name)}' value='{esc(value)}' "
        f"placeholder='{esc(placeholder)}' autocomplete='off'{attrs}>"
    )
    return group(label, box, for_id=input_id, cls=cls)


# --- the phone sheet ---------------------------------------------------------------------------


def filters_sheet(inner_html: str, *, show_html: str, clear_html: str, size: bool = True) -> str:
    """The filter groups, which phones open as a full-screen sheet (the Filters button in the
    name row): a head with Filters and ×, the groups, the card size (phones only, where the
    results bar hides it), then Clear filters and ``show_html`` (the button that closes it)."""
    size_html = (
        "<div class='sheet-size'>"
        + card_size_control().replace("class='field cardsize'", "class='cardsize'")
        + "</div>"
        if size
        else ""
    )
    return (
        "<div id='sp-filters' class='sp-filters'><div class='sp-sheet-head'><h2>Filters</h2>"
        f"<button type='button' class='close' aria-label='Close filters'>{icon('x')}</button></div>"
        f"{inner_html}{size_html}<div class='sp-sheet-foot'>{clear_html}{show_html}</div></div>"
    )


# --- the results bar ---------------------------------------------------------------------------


def sort_group(
    *,
    order_name: str,
    orders: Iterable[Option],
    order: str,
    order_id: str,
    dir_name: str = "",
    dirs: Iterable[Option] = DIRECTIONS,
    direction: str = "auto",
) -> str:
    """Sort with its direction (left out when ``dir_name`` is empty: Archidekt's deck search has
    no direction). On phones static/searchpanel.js folds the two into one drop-down."""
    out = (
        f"<span class='g sort'><label class='lbl' for='{esc(order_id)}'>Sort by</label>"
        f"<span class='sortw'><select class='sortf' id='{esc(order_id)}' name='{esc(order_name)}'>"
        f"{options_html(orders, order)}</select></span>"
    )
    if dir_name:
        out += (
            f"<span class='dirw'><select class='dir' name='{esc(dir_name)}' aria-label='Direction'>"
            f"{options_html(dirs, direction)}</select></span>"
        )
    return out + "</span>"


def view_group(view: str, *, name: str = "view") -> str:
    """Grid or List, as radio buttons drawn as one segmented control (the words show from 1280 px)."""
    return (
        "<span class='segc view' role='radiogroup' aria-label='View'>"
        + "".join(
            f"<label title='{label}'><input type='radio' name='{esc(name)}' value='{v}'"
            f"{' checked' if v == view else ''}>{icon(v)}<span class='tx'>{label}</span></label>"
            for v, label in (("grid", "Grid"), ("list", "List"))
        )
        + "</span>"
    )


def results_bar(
    *, count_html: str, sort_html: str, view_html: str, clear_html: str, more_html: str = "", cls: str = ""
) -> str:
    """The results controls at the panel's foot: ``more_html`` (card search's More filters) and the
    count at the left; Sort, the view, the card size and Clear filters at the right. The bar breaks
    after Sort when it is narrow, so no group is ever alone on a row."""
    extra = f" {esc(cls)}" if cls else ""
    size = card_size_control().replace("class='field cardsize'", "class='g size cardsize'")
    return (
        f"<div class='rbar{extra}'>{more_html}{count_html}<span class='brka'></span>{sort_html}"
        f"<span class='brk'></span>{view_html}{size}{clear_html}</div>"
    )


def count_html(text: str, *, count_id: str) -> str:
    return f"<h2 class='cnt' id='{esc(count_id)}' aria-live='polite'>{esc(text)}</h2>"
