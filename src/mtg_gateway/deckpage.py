"""The deck page and the deck list, laid out the way Archidekt lays them out.

Everything here is server-rendered HTML built from the same ``Deck`` the tools and the JSON API
use. The page works without script (every control is a form or a link); ``static/deck.js``
only makes the local filter live and the toolbar submit on change. Card images come from
Scryfall by the card's Scryfall id (the page's CSP allows ``cards.scryfall.io`` only); a card
without an id gets a drawn placeholder frame. No Archidekt asset is loaded.

Layout, in order: banner (featured art blurred behind, name, info rows, tags, actions, owner),
toolbar panel (add card, view as, group by, sort by, local filter), the cards in the chosen
view (text rows, stacks of images, or a grid), deck stats, description.
"""

from __future__ import annotations

import calendar
import json
import math
import re
import time
from collections import Counter
from typing import Any
from urllib.parse import quote, urlencode

from . import searchpanel as sp
from . import ui, waiting_strip
from .archidekt import (
    FORMAT_NAMES,
    VOTE_DOWN,
    VOTE_UP,
    Deck,
    DeckCard,
    featured_scryfall_id,
    format_label,
    rich_text,
)
from .bracket_view import banner_bracket_html, checks_bracket_row, deck_card_bracket_text, stats_bracket_html
from .deck_stats import (
    CURVE_BUCKETS,
    PRIMARY_TYPES,
    WUBRG,
    auto_category,
    colour_letter,
    curve_bucket,
    is_basic_land,
    is_land,
    mana_pips,
    mana_value,
    primary_category,
    primary_type,
)
from .decks import mana_value_text, split_label
from .export_formats import EDHPLAY_URL
from .mana import mana_html as _mana_html
from .theme import card_size_control, esc, icon, plural
from .views import cards_by_category

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SYMBOL = re.compile(r"\{([^{}]+)\}")

VIEWS = {
    "text": "Text",
    "table": "Table",
    "brewer": "Brewer",
    "stacks": "Stacks",
    "scroll": "Scroll",
    "grid": "Grid",
}
# The views that show cards as pictures: the card size switch (0.7.29) sits next to View as in these.
PICTURE_VIEWS = ("stacks", "scroll", "grid")
GROUPS = {
    "category": "Categories",
    "type": "Type",
    "mv": "Mana value",
    "color": "Color",
    "rarity": "Rarity",
    "none": "Full deck",
}
SORTS = {
    "name": "Alphabet",
    "mv": "Mana value",
    "type": "Type",
    "price": "Price",
    "color": "Color",
    "rarity": "Rarity",
    "salt": "Salt",
    "edhrec": "EDHREC rank",
}
COLOUR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colorless"}
TYPE_ORDER = list(PRIMARY_TYPES)  # deck_stats owns the order (and primary_type, auto_category)
RARITY_ORDER = {"mythic": 0, "rare": 1, "uncommon": 2, "common": 3, "special": 4}


def image_url(uid: str | None, size: str = "normal") -> str | None:
    """A Scryfall card image by Scryfall id (the scheme Scryfall's own image URIs use)."""
    if not uid or not _UUID.fullmatch(uid):
        return None
    return f"https://cards.scryfall.io/{size}/front/{uid[0]}/{uid[1]}/{uid}.jpg"


def print_label(set_code: str | None, number: str | None) -> str:
    """A printing as every screen writes it: "SLD #1074"; the set code alone when the
    number is not known."""
    code = (set_code or "").upper()
    return f"{code} #{number}" if code and number else code


def card_image(card: DeckCard, size: str = "normal") -> str | None:
    return image_url(getattr(card, "scryfall_uid", "") or None, size)


def mana_html(cost: str) -> str:
    """Mana pips (mana.py): {2}{G}{U} -> three discs with our own glyphs."""
    return _mana_html(cost)


def money(value: float | None) -> str:
    return "–" if value is None else f"${value:,.2f}"


def est_cost_html(stats: dict[str, Any]) -> str:
    """The deck's estimated cost: a dash with an explanation while no card has a price."""
    if not stats.get("priced_cards"):
        return "<span title='No prices from Archidekt yet'>–</span>"
    return money(stats.get("price_total"))


def iso_ts(iso: str) -> int | None:
    """An Archidekt ISO timestamp as seconds since the epoch; None when unreadable."""
    if not iso:
        return None
    try:
        return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def colour_group(card: DeckCard) -> str:
    cols = [c for c in WUBRG if c in {colour_letter(x) for x in card.colors}]
    if not cols:
        return "Colorless" if not is_land(card) else "Land"
    return "Multicolor" if len(cols) > 1 else COLOUR_NAMES[cols[0]]


def mv_group(card: DeckCard) -> str:
    """The Mana value grouping's stack: the deck's custom mana value when the card has one."""
    mv = mana_value(card)
    if is_land(card):
        return "Land"
    if mv is None:
        return "No mana value"
    return f"{int(mv) if mv == int(mv) else mv} mana value"


def _mv_or(card: DeckCard, default: float) -> float:
    mv = mana_value(card)
    return default if mv is None else mv


def _colour_rank(card: DeckCard) -> tuple[int, str]:
    cols = [c for c in WUBRG if c in {colour_letter(x) for x in card.colors}]
    if not cols:
        return (6 if not is_land(card) else 7, card.name.lower())
    return (WUBRG.index(cols[0]) if len(cols) == 1 else 5, card.name.lower())


def sort_cards(cards: list[DeckCard], key: str) -> list[DeckCard]:
    if key == "mv":
        return sorted(cards, key=lambda c: (is_land(c), _mv_or(c, 99), c.name.lower()))
    if key == "type":
        rank = {t: i for i, t in enumerate(TYPE_ORDER)}
        return sorted(cards, key=lambda c: (rank.get(primary_type(c), 99), c.name.lower()))
    if key == "price":
        return sorted(cards, key=lambda c: (-(c.price or 0), c.name.lower()))
    if key == "color":
        return sorted(cards, key=_colour_rank)
    if key == "rarity":
        return sorted(cards, key=lambda c: (RARITY_ORDER.get(c.rarity.lower(), 9), c.name.lower()))
    if key == "salt":
        return sorted(cards, key=lambda c: (-(c.salt or 0), c.name.lower()))
    if key == "edhrec":
        return sorted(
            cards, key=lambda c: (c.edhrec_rank if c.edhrec_rank is not None else 10**9, c.name.lower())
        )
    return sorted(cards, key=lambda c: c.name.lower())


def group_cards(deck: Deck, group: str, sort: str, q: str = "") -> list[tuple[str, list[DeckCard]]]:
    """(group name, cards) in Archidekt's order for the chosen grouping, the cards sorted and
    filtered by the local filter ``q`` (a case-insensitive name or type substring)."""
    needle = q.strip().lower()

    def keep(c: DeckCard) -> bool:
        if not needle:
            return True
        hay = " ".join([c.name, " ".join(c.types), " ".join(c.subtypes), c.set_code]).lower()
        return needle in hay

    if group == "category":
        groups = [(name, [c for c in cards if keep(c)]) for name, cards in cards_by_category(deck)]
        return [(n, sort_cards(cs, sort)) for n, cs in groups if cs]
    cards = [c for c in deck.cards if keep(c)]
    buckets: dict[str, list[DeckCard]] = {}
    if group == "none":
        buckets["Deck"] = [c for c in cards if deck.in_deck(c)]
        side = [c for c in cards if not deck.in_deck(c)]
        if side:
            buckets["Maybeboard / Sideboard"] = side
        return [(n, sort_cards(cs, sort)) for n, cs in buckets.items() if cs]
    for c in cards:
        if group == "type":
            key = primary_type(c)
        elif group == "mv":
            key = mv_group(c)
        elif group == "color":
            key = colour_group(c)
        elif group == "rarity":
            key = (c.rarity or "unknown").capitalize()
        else:
            key = "Deck"
        buckets.setdefault(key, []).append(c)

    def order(name: str) -> tuple[int, Any]:
        if group == "type":
            return (TYPE_ORDER.index(name) if name in TYPE_ORDER else 50, name)
        if group == "mv":
            if name == "Land":
                return (98, name)
            if name == "No mana value":
                return (99, name)
            return (0, float(name.split()[0]))
        if group == "color":
            seq = ["White", "Blue", "Black", "Red", "Green", "Multicolor", "Colorless", "Land"]
            return (seq.index(name) if name in seq else 50, name)
        if group == "rarity":
            return (RARITY_ORDER.get(name.lower(), 9), name)
        return (0, name)

    return [(n, sort_cards(buckets[n], sort)) for n in sorted(buckets, key=order)]


# -- pieces ------------------------------------------------------------------------------------


def avatar_html(name: str, size: str = "lg") -> str:
    initial = (name or "?").strip()[:1].upper() or "?"
    return f"<span class='avatar {size}' aria-hidden='true'>{esc(initial)}</span>"


def featured(deck: Deck) -> DeckCard | None:
    """The card whose art fronts the deck: the cover the owner picked on Archidekt when it is a card
    of the deck, else the first commander, else the first card with art."""
    chosen = featured_scryfall_id(deck.featured)
    if chosen:
        for c in deck.cards:
            if c.scryfall_uid.lower() == chosen.lower() and card_image(c):
                return c
    for c in deck.cards:
        if "Commander" in c.categories and card_image(c):
            return c
    for c in deck.cards:
        if card_image(c):
            return c
    return None


def legal_problems(stats: dict[str, Any] | None) -> list[str]:
    """What keeps the deck from being legal in its format, as archidekt.com's banner counts it:
    every format rule from deck_stats.deck_checks (deck size, command zone, colour identity,
    copies, card legality...), not only the cards' own legality. Computed from the deck as just
    read, so an edit made on Archidekt shows on the next view."""
    stats = stats or {}
    checks = stats.get("checks")
    if isinstance(checks, dict) and "legal_problems" in checks:
        return [str(p) for p in checks["legal_problems"]]
    return [
        f"{p.get('name')} ({p.get('status')})" if isinstance(p, dict) else str(p)
        for p in stats.get("legality_problems") or []
    ]


def legality_chip_html(deck: Deck, stats: dict[str, Any] | None) -> str:
    """The banner's Legality chip: green when the deck meets every rule of its format (the size
    too, as on archidekt.com), red with the number of problems otherwise, nothing for a deck
    without a format. Rendered again by the edit endpoint (api.py) so the page can swap it in
    after a card changes."""
    problems = legal_problems(stats)
    if not deck.format:
        return ""
    if not problems:
        return (
            f"<span class='legal ok' title='Legal in {esc(deck.format or 'this format')}'>"
            f"{icon('check')} Legality</span>"
        )
    # every failing rule with its reason, as archidekt.com's legality list shows them
    title = plural(len(problems), "problem") + ": " + "; ".join(problems)
    return f"<span class='legal bad' title='{esc(title)}'>{icon('x')} Legality</span>"


def deck_title(deck: Deck) -> str:
    """The deck's name in headings, menus and boxes; a deck with no name reads "Unnamed deck"."""
    return deck.name or UNNAMED_DECK


def updated_when(iso: str) -> str:
    """The banner's date in the shared list format (ui.when: relative within 7 days, then the date;
    the full date and time on hover). '' when the stamp is unreadable."""
    if not iso:
        return ""
    try:
        ts = calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return ""
    return ui.when(ts)


def banner_html(
    deck: Deck,
    stats: dict[str, Any] | None,
    *,
    own: bool,
    csrf: str | None,
    writes_enabled: bool,
    forge_on: bool = False,
) -> str:
    """The deck banner: name, facts, the buttons (Edit deck the page's one orange button) and the
    deck menu. The menu is the shared deck menu (static/deck.js builds it with cardmenu.js's
    layer, from the ⋮ More button, a right-click on the banner or a press and hold) plus the old
    More's page-only items. Without scripts the same items are a details drop-down, and its
    Export…, Compare with… and Delete deck… links go to the old addresses, which open this page
    with that box open (deck_boxes_html)."""
    stats = stats or {}
    art = featured(deck)
    art_url = card_image(art, "art_crop") if art else None
    style = f" style=\"--art:url('{esc(art_url)}')\"" if art_url else ""
    legality = legality_chip_html(deck, stats)
    privacy = (
        f"<span class='privacy' title='Private deck'>{icon('eye-off')}"
        "<span class='sr-only'>Private deck</span></span>"
        if deck.private
        else f"<span class='privacy' title='Unlisted deck'>{icon('eye-off')}"
        "<span class='sr-only'>Unlisted deck</span></span>"
        if deck.unlisted
        else ""
    )
    brackets = banner_bracket_html(deck, stats)  # bracket_view.py
    salt = stats.get("salt_total") if stats.get("salt_total") is not None else "–"
    tags = (
        "".join(f"<span class='pill'>{esc(t)}</span>" for t in deck.tags)
        if deck.tags
        else "<span class='notags'>No deck tags</span>"
    )
    csrf_in = f"<input type='hidden' name='csrf' value='{esc(csrf)}'>" if csrf else ""
    did = esc(deck.id)
    title = deck_title(deck)
    primary = ""
    if own and csrf:
        primary += f"<a class='btn btn-primary' href='/decks/{did}/edit'>{icon('edit')} Edit deck</a>"
    if csrf:
        # Any deck the member can read can be cloned into their own account, as on archidekt.com
        # (its Clone button is on public decks and precons too). The button opens the Clone box
        # (name of the copy, folder); without scripts it submits the box's form with its defaults.
        primary += (
            f"<button type='submit' form='clonef' class='btn' {ui.box_open_attr('clone')} "
            "title='Copy this deck into your own Archidekt account'>"
            f"{icon('clone')} Clone deck</button>"
        )
    # Archidekt's own playtester (draw, mulligan, play by hand) in its own tab: a frame
    # inside the gateway never carries the person's Archidekt sign-in, so a private deck stayed empty
    # there. The app opens the link in the phone's browser, where that sign-in lives.
    primary += (
        f"<a class='btn' href='https://archidekt.com/playtester-v2/{did}' target='_blank' "
        "rel='noreferrer noopener' title='Draw and play this deck by hand in Archidekt&#39;s playtester, "
        f"in a new tab'>{icon('play')} Playtest</a>"
        # The gateway's own goldfish: shuffle, mulligan, draw and play turns by hand in this
        # browser, on the deck this page read (static/goldfish.js).
        f"<a class='btn' href='/decks/{did}/goldfish' title='Shuffle, draw an opening hand and play "
        f"turns by hand, here on the gateway'>{icon('layers')} Goldfish</a>"
    )
    # Play on EDHPlay: EDHPlay is the online table; it has no import by link, so the
    # button opens a short box whose Copy and Open EDHPlay.com copies the whole deck in its import
    # format and opens the site (deck.js). The box asks /decks/{id}/edhplay.json for the list when it
    # opens (companion_tools.py), so the page itself doesn't carry the whole list. Without script the
    # link still opens EDHPlay, and Export has the list.
    primary += (
        f"<a class='btn' href='{EDHPLAY_URL}' target='_blank' rel='noreferrer noopener' data-edhplay "
        f"data-src='/decks/{did}/edhplay.json' "
        "title='Copy this deck list and open EDHPlay to play it online'>"
        f"{icon('play')} Play on EDHPlay</a>"
    )
    if csrf:
        # The same run as the assistant's run_deck_report: statistics, validation and the solo
        # test on the research service, plus Forge games when Forge is on, stored under History.
        # "Solo test", not "goldfish": Goldfish is the button beside it, where the member plays by
        # hand.
        primary += (
            f"<form method='post' action='/decks/{did}/report' class='inline'>{csrf_in}"
            "<button type='submit' data-busy-label='Testing…' title='The solo test (300 games, no "
            "opponents), a legality check and the deck statistics, plus Forge games against precons when "
            "Forge is on and the deck has a commander; saved under History. The solo test takes up to a "
            f"minute; the result page opens when the solo test is done.'>{icon('stats')} Run full test"
            "</button></form>"
        )
    more_items = []
    if csrf and forge_on:
        # The same run without the Forge games, for a quick result (forge=0, companion_tools.py).
        more_items.append(
            f"<form method='post' action='/decks/{did}/report' id='solof'>{csrf_in}"
            "<input type='hidden' name='forge' value='0'>"
            "<button type='submit' data-busy-label='Testing…' title='The same run without Forge games: "
            f"the solo test, the legality check and the statistics'>{icon('stats')} "
            "Solo test only</button></form>"
        )
    if own and csrf:
        more_items.append(f"<a href='/decks/{did}/settings'>{icon('settings')} Deck settings</a>")
    more_items.append(
        f"<a href='/decks/{did}/export' {ui.box_open_attr('export')}>{icon('download')} Export…</a>"
    )
    more_items.append(f"<a href='/history?deck_id={did}'>{icon('history')} History and snapshots</a>")
    more_items.append(f"<a href='/proposals?deck_id={did}'>{icon('proposals')} Proposals for this deck</a>")
    more_items.append(
        f"<a href='/decks/{did}/compare' {ui.box_open_attr('compare')}>{icon('layers')} Compare with…</a>"
    )
    more_items.append(f"<a href='/decks/{did}#stats'>{icon('report')} Deck stats</a>")
    more_items.append(
        f"<a href='https://archidekt.com/decks/{did}' target='_blank' rel='noreferrer noopener'>"
        f"{icon('external')} Open on Archidekt</a>"
    )
    if own and csrf:
        more_items.append(
            f"<a class='danger' href='/decks/{did}/delete' {ui.box_open_attr('delete')}>{icon('trash')} "
            "Delete deck…</a>"
        )
    # Without scripts: the drop-down. With them deck.js hides it and puts the ⋮ More button that
    # opens the shared deck menu in its place.
    more = (
        f"<details class='dd deckmore'><summary class='btn'>{icon('more')} More</summary>"
        f"<div class='menu left'>{''.join(more_items)}</div></details>"
    )
    writes_note = (
        f"<p class='small notags'>{WRITES_OFF_TITLE}; edits can be proposed and reviewed but not applied.</p>"
        if own and not writes_enabled
        else ""
    )
    when = updated_when(deck.updated_at)
    data = (
        f" data-deck-id='{did}' data-deck-name='{esc(title)}' data-own='{1 if own and csrf else 0}'"
        f" data-can='{1 if csrf else 0}' data-forge='{1 if forge_on and csrf else 0}'"
    )
    return (
        f"<section class='banner{'' if art_url else ' noart'}'{style}{data} aria-label='Deck: {esc(title)}'>"
        "<div class='shade'><div class='content'>"
        f"<div class='strip'{style}></div>"
        f"<a class='owner' href='/users/{esc(deck.owner)}' title='{esc(deck.owner)}: public decks'>"
        f"{avatar_html(deck.owner)}<span class='uname'>{esc(deck.owner or 'unknown')}</span></a>"
        "<div class='info'>"
        f"<h1 class='deckname{'' if deck.name else ' unnamed'}'>{privacy}"
        f"<span title='{esc(title)}'>{esc(title)}</span></h1>"
        "<div class='row'>"
        + (f"<span>{when}</span>" if when else "")
        + f"<span>{plural(stats.get('distinct', 0), 'distinct card')}</span>"
        "</div><div class='row'>"
        f"<span>{esc(format_label(deck.format))}</span>{legality}{brackets}"
        "</div><div class='row'>"
        f"<span>Size: {stats.get('card_count', sum(c.quantity for c in deck.main_cards))}</span>"
        f"<span>Est cost: <b class='orange'>{est_cost_html(stats)}</b> (Archidekt prices)</span>"
        f"<span>Salt sum: <b class='orange'>{esc(salt)}</b></span>"
        "</div>"
        f"<div class='tags'>{icon('tag')} {tags}</div>"
        f"<div class='controls'><div class='primary'>{primary}{more}</div></div>{writes_note}"
        f"{social_html(deck, own=own, writes_enabled=writes_enabled) if csrf else ''}"
        "</div>"
        "</div></div></section>"
    )


UNNAMED_DECK = "Unnamed deck"
INDENT = "\u2003"  # an em space per folder level in folder pickers
DECK_BOXES = ("export", "delete", "compare", "clone")
EXPORT_FILES = (
    ("export.archidekt.txt", "Archidekt .txt"),
    ("export.txt", "Plain .txt"),
    ("export.csv", ".csv"),
    ("export.json", ".json"),
    ("export.arena.txt", "Arena .txt"),
    ("export.dek", "MTGO .dek"),
    ("export.pdf", "PDF"),
)


def _export_pane(key: str, title: str, text: str, *, current: bool) -> str:
    """One tab's pane of the Export box: the list in a read-only text box with its Copy button
    (static/export.js). The ids and the status span are what the old Export page had."""
    return (
        f"<div class='xpane' data-pane='{key}'{'' if current else ' hidden'}>"
        f"<div class='xhead'><button type='button' class='btn small copybtn' data-copy='{key}' "
        f"aria-describedby='{key}-status'>{icon('copy')} <span class='label'>Copy</span></button>"
        f"<span class='sr-only' id='{key}-status' role='status' aria-live='polite'></span></div>"
        f"<textarea id='{key}' rows='{min(18, text.count(chr(10)) + 2)}' readonly aria-label='{title}'>"
        f"{esc(text)}</textarea></div>"
    )


def export_box_html(deck: Deck, *, open: bool = False) -> str:
    """The Export box (F3): tabs Archidekt text, Plain list, Side and maybe (when the deck has
    rows outside the deck) and Files, with the seven downloads. Without scripts every pane
    shows, one under the other."""
    from .decks import deck_to_archidekt_text, deck_to_text

    did = esc(deck.id)
    main = deck_to_text(deck)
    side = deck_to_text(deck, zone="side")
    arch = deck_to_archidekt_text(deck)
    tabs = [("exp-arch", "Archidekt text", "Archidekt import text", arch)]
    tabs.append(("exp-main", "Plain list", "Plain decklist", main))
    if side:
        tabs.append(("exp-side", "Side and maybe", "Sideboard and maybeboard", side))
    tablist = (
        "".join(
            f"<button type='button' role='tab' aria-selected='{'true' if i == 0 else 'false'}' "
            f"aria-controls='{key}-pane' data-tab='{key}'>{esc(short)}</button>"
            for i, (key, short, _long, _text) in enumerate(tabs)
        )
        + "<button type='button' role='tab' aria-selected='false' data-tab='exp-files'>Files</button>"
    )
    panes = "".join(
        _export_pane(key, long, text, current=i == 0) for i, (key, _short, long, text) in enumerate(tabs)
    )
    files = "".join(
        f"<a class='btn' href='/decks/{did}/{path}' download>{icon('download')} {esc(label)}</a>"
        for path, label in EXPORT_FILES
    )
    panes += (
        "<div class='xpane' data-pane='exp-files' hidden>"
        "<p class='muted small'>Downloads are made in this browser from the deck as read now.</p>"
        f"<div class='files'>{files}</div></div>"
    )
    body = f"<div class='segc xtabs' role='tablist' aria-label='Export'>{tablist}</div>{panes}"
    return ui.box(
        "export",
        f"Export “{deck_title(deck)}”",
        body,
        actions=[ui.box_button("Close", cancel=True)],
        wide=True,
        open=open,
        close_href=f"/decks/{did}",
    )


def delete_box_html(
    deck: Deck, csrf: str | None, *, backups: bool, open: bool = False, error: str = ""
) -> str:
    """The typed-name Delete box (F3, M4): what is deleted and what is kept, the name to type, Keep
    it and the solid red Delete this deck (static/deck.js keeps it off until the name matches).
    An unnamed deck is typed as "Unnamed deck"."""
    did = esc(deck.id)
    title = deck_title(deck)
    n = sum(c.quantity for c in deck.cards if deck.in_deck(c))
    undo = (
        "A snapshot of the deck is kept under History"
        + (", and a private copy is made in your backup folder on Archidekt first" if backups else "")
        + ". Archidekt itself has no undo for a deleted deck."
    )
    body = (
        (ui.notice(error, "error", role="alert") if error else "")
        + f"<form id='delf' method='post' action='/decks/{did}/delete' class='deleteform'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf or '')}'>"
        f"<p>This deletes the deck ({plural(n, 'card')}) from your Archidekt account. {esc(undo)}</p>"
        "<div class='field'><label for='typed'>Type the deck's name to confirm</label>"
        f"<input id='typed' type='text' name='name' autocomplete='off' required maxlength='200' "
        f"placeholder='{esc(title)}' data-typed='{esc(title)}'></div></form>"
    )
    return ui.box(
        "delete",
        f"Delete “{title}”?",
        body,
        actions=[
            ui.box_button("Keep it", cancel=True),
            ui.box_button("Delete this deck", kind="danger-solid", form="delf", icon_name="trash"),
        ],
        open=open,
        close_href=f"/decks/{did}",
        cls="dangerbox",
    )


def compare_box_html(deck: Deck, *, open: bool = False) -> str:
    """The Compare with box (M8): the other deck (a precon's name, a deck link or id, or a pasted
    list in the fold), then the Compare page with both decks. static/deck.js adds the member's
    own decks as a list to pick from."""
    did = esc(deck.id)
    body = (
        f"<form id='cmpf' method='get' action='/decks/{did}/compare' class='stack'>"
        "<div class='field'><label for='cmp-with'>Other deck</label>"
        "<input id='cmp-with' type='text' name='with' placeholder='Precon name, deck link or deck ID' "
        "autocomplete='off'></div>"
        "<details class='fold'><summary>…or paste a decklist</summary>"
        "<textarea name='paste' rows='5' aria-label='Paste a decklist' "
        "placeholder='1 Sol Ring&#10;1 Arcane Signet'></textarea></details></form>"
    )
    return ui.box(
        "compare",
        f"Compare “{deck_title(deck)}” with",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Compare", kind="primary", form="cmpf", icon_name="swap"),
        ],
        open=open,
        close_href=f"/decks/{did}",
    )


def clone_box_html(
    deck: Deck,
    csrf: str | None,
    folders: dict[str, Any] | None,
    *,
    open: bool = False,
    error: str = "",
    values: dict[str, str] | None = None,
) -> str:
    """The Clone box: the name of the copy (today's default "Copy of - {deck}") and the folder it
    goes in, then the copy is made (POST /decks/{id}/clone, decks.propose_clone with folder_id).
    ``folders``: decks.folders' flattened tree, or None when it could not be read (the top level
    is then the only choice)."""
    did = esc(deck.id)
    v = values or {}
    name = v.get("name") if v.get("name") is not None else f"Copy of - {deck_title(deck)}"
    chosen = v.get("folder_id", "")
    opts = "<option value=''" + (" selected" if not chosen else "") + ">Top level (no folder)</option>"
    for f in (folders or {}).get("folders", []):
        if f.get("backup") or not f.get("depth"):
            continue  # the backup folder is no place for a copy; the root is the top level
        fid = str(f["id"])
        opts += (
            f"<option value='{esc(fid)}'{' selected' if chosen == fid else ''}>"
            f"{esc(INDENT * (int(f['depth']) - 1) + str(f['name']))}</option>"
        )
    body = (
        (ui.notice(error, "error", role="alert") if error else "")
        + f"<form id='clonef' method='post' action='/decks/{did}/clone' class='stack'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf or '')}'>"
        "<div class='field'><label for='clone-name'>Name of the copy</label>"
        f"<input id='clone-name' type='text' name='name' value='{esc(name)}' maxlength='200' required></div>"
        "<div class='field'><label for='clone-folder'>Folder</label>"
        f"<span class='sel'><select id='clone-folder' name='folder_id'>{opts}</select></span></div>"
        f"<p class='muted small'>{icon('eye-off')} Private copy in your Archidekt account.</p></form>"
    )
    return ui.box(
        "clone",
        f"Clone “{deck_title(deck)}”",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Clone", kind="primary", form="clonef", icon_name="clone"),
        ],
        open=open,
        close_href=f"/decks/{did}",
    )


def deck_boxes_html(
    deck: Deck,
    *,
    own: bool,
    csrf: str | None,
    backups: bool,
    folders: dict[str, Any] | None = None,
    open_box: str = "",
    box_error: str = "",
    box_values: dict[str, str] | None = None,
) -> str:
    """The boxes the deck page holds (hidden until the menu or a button opens them): Export, Compare
    with, Clone and, on the member's own deck, Delete. ``open_box`` names the one the page comes
    with open (the old /export and /delete addresses, or a refused clone or delete, whose
    ``box_error`` shows in it)."""
    if not csrf:
        return ""
    out = export_box_html(deck, open=open_box == "export")
    out += compare_box_html(deck, open=open_box == "compare")
    out += clone_box_html(
        deck,
        csrf,
        folders,
        open=open_box == "clone",
        error=box_error if open_box == "clone" else "",
        values=box_values if open_box == "clone" else None,
    )
    if own:
        out += delete_box_html(
            deck,
            csrf,
            backups=backups,
            open=open_box == "delete",
            error=box_error if open_box == "delete" else "",
        )
    return out


WRITES_OFF_TITLE = "Archidekt writes are switched off on this gateway"
# shown under the greyed buttons too: a phone shows no tooltip
WRITES_OFF_LINE = "Archidekt writes are switched off on this gateway, so these buttons are off."
WRITES_OFF_FOLLOW = "Archidekt writes are switched off on this gateway, so following can't be changed here."


def social_html(deck: Deck, *, own: bool, writes_enabled: bool = True) -> str:
    """Like, Bookmark, Follow and Comments, as Archidekt's deck page offers them. The buttons are
    the person's own clicks: social.js asks for a confirmation and posts to /social/api; without
    script they are inert and the More menu's "Open on Archidekt" link is the way. With Archidekt
    writes switched off they show their state but are disabled, saying why (social review)."""
    did = esc(deck.id)

    def title(text: str) -> str:
        # writes switched off: the button shows its state but is disabled, and says why
        return f" title='{text}'" if writes_enabled else f" disabled title='{WRITES_OFF_TITLE}'"

    liked = deck.user_vote == VOTE_UP
    # A down vote (cast through the assistant; the page has no down button) shows
    # as such, and the button takes it back
    down = deck.user_vote == VOTE_DOWN
    like_tip = (
        "You voted this deck down"
        if down
        else "You like this deck"
        if liked
        else "Like this deck on Archidekt"
    )
    like = (
        f"<button type='button' class='soc{' on' if liked else ''}' data-social='vote' "
        f"data-state='{deck.user_vote}' aria-pressed='{'true' if liked else 'false'}'"
        f"{title(like_tip)}>"
        f"{icon('heart')}<b class='n'>{deck.points}</b>"
        f"<span>{'Voted down' if down else 'Liked' if liked else 'Like'}</span></button>"
    )
    marked = deck.bookmarked
    bookmark = (
        f"<button type='button' class='soc{' on' if marked else ''}' data-social='bookmark' "
        f"data-state='{1 if marked else 0}' aria-pressed='{'true' if marked else 'false'}'"
        f"{title('Bookmarked on Archidekt' if marked else 'Bookmark this deck on Archidekt')}>"
        f"{icon('bookmark')}<span>{'Bookmarked' if marked else 'Bookmark'}</span></button>"
    )
    follow = (
        f"<button type='button' class='soc' data-social='follow' data-user='{esc(deck.owner_id)}' "
        f"data-name='{esc(deck.owner)}' data-state='unknown'"
        f"{title('Follow ' + esc(deck.owner) + ' on Archidekt')}>"
        f"{icon('follow')}<span>Follow {esc(deck.owner)}</span></button>"
        if deck.owner_id and not own
        else ""
    )
    comments = (
        f"<a class='soc' href='#comments' data-social='comments'>{icon('comment')}<span>Comments</span></a>"
    )
    off = "" if writes_enabled else " data-writes='off' aria-describedby='social-off'"
    return (
        f"<div class='social' role='group' data-deck='{did}' aria-label='Archidekt actions'{off}>"
        f"{like}{bookmark}{follow}{comments}</div>"
        + (
            ""
            if writes_enabled
            else f"<p class='social-off muted small' id='social-off'>{WRITES_OFF_LINE}</p>"
        )
    )


def comments_html(deck: Deck, *, writes_enabled: bool = True) -> str:
    """The deck's comment thread (loaded by social.js when the section scrolls into view) and the
    form to add one; a post is confirmed before it goes out, under the member's Archidekt name.
    With Archidekt writes switched off the thread is read-only: no form, no Reply, votes or edits."""
    did = esc(deck.id)
    form = (
        "<form class='newcomment'><label for='ctext'>Add a comment</label>"
        "<textarea id='ctext' name='text' rows='3' maxlength='2000' "
        "placeholder='Say something about this deck…'></textarea>"
        "<div class='replyto' hidden></div>"
        f"<button type='submit' class='btn'>{icon('comment')} Post comment</button>"
        "<p class='muted small'>Posted publicly on Archidekt under your Archidekt name, after you "
        "confirm.</p>"
        "</form>"
        if writes_enabled
        else f"<p class='muted small'>{WRITES_OFF_TITLE}, so comments can be read here but not "
        "posted, changed or voted on.</p>"
    )
    return (
        f"<section class='panel comments' id='comments' data-deck='{did}' "
        f"data-writes='{'on' if writes_enabled else 'off'}' "
        f"data-root='{esc(deck.comment_root or '')}'>"
        f"<div class='panel-head'><h2>Comments</h2><span class='muted small' data-count></span></div>"
        f"<div class='thread' data-src='/social/api/decks/{did}/comments'><p class='muted'>Comments from "
        f"Archidekt appear here. <a href='https://archidekt.com/decks/{did}' target='_blank' "
        "rel='noreferrer noopener'>Read the thread on Archidekt</a>.</p></div>"
        f"{form}</section>"
    )


# The deck page's styles for the banner, social actions and the waiting changes strip: DECK_CSS
# ends with this, so these rules need no edit inside DECK_CSS.
DECK_CSS_A = (
    waiting_strip.LIST_CSS  # the "Waiting changes" strip
    # comments and social actions: a long name wraps instead of running off a phone screen, replies
    # indent less on a phone, and a button disabled because writes are off says so by its cursor
    + """
.banner .social .soc{height:auto;min-height:34px;max-width:100%}
/* focus moves to Yes by script after a click, which :focus-visible may not mark: always ring it
   (the banner's chips, a comment's vote, edit and delete, and posting) */
.banner .social .confirm button:focus,.comments .confirm button:focus,.comments .confirmbar button:focus{
  outline:2px solid var(--focus);outline-offset:2px}
.banner .social .soc span{min-width:0;overflow-wrap:anywhere;line-height:1.2;padding:.35rem 0}
.banner .social[data-writes=off] .soc[disabled]{cursor:not-allowed}
.banner .social-off{display:inline-block;margin:.5rem 0 0;padding:.3rem .65rem;border-radius:6px;
  background:rgba(0,0,0,.72);color:#fff;font-size:.85rem;overflow-wrap:anywhere}
.comments .cmt .who{flex-wrap:wrap}
.comments .cmt .who b{overflow-wrap:anywhere}
@media (max-width:600px){.comments .cmt .replies{margin-left:.35rem;padding-left:.5rem}}
/* the deck menu (deck.js): the ⋮ More button replaces the no-script drop-down once the script runs */
.banner .deckmenu{display:inline-flex;align-items:center;gap:.4rem;margin:0}
.banner[data-menu-ready] details.deckmore{display:none}
.banner h1.deckname.unnamed > span{color:#cfcfcf;font-style:italic}
/* the waiting changes strip: Apply plain, Reject a red outline, a divider line instead of a
   nested bordered bar (the markup is waiting_strip.py's) */
.wstrip .wbar.inline{border:0;border-top:1px solid var(--border-soft);border-radius:0;background:transparent;
  padding:.6rem 0 .3rem;margin-top:.25rem}
.wstrip .wbar button.btn-primary{background:transparent;border:1px solid var(--control-border);
  color:var(--text);font-weight:700}
.wstrip .wbar button.btn-primary:hover:not(:disabled){background:var(--surface-2)}
.wstrip .wbar button.danger{background:transparent;color:var(--danger-text);
  border:1px solid var(--danger-text)}
.wstrip .wbar button.danger:hover:not(:disabled){background:var(--red-tint);color:var(--danger-text)}
/* the drag hint is the grip icon's hover text */
.toolbar .views .hint{display:inline-flex;align-items:center;justify-content:center;width:44px;
  min-height:44px;
  color:var(--text-muted);grid-column:auto}
.toolbar .views .hint svg{width:20px;height:20px}
/* the Export box: tabs over one pane at a time (every pane without scripts), Copy at the top right */
.mtgbox .xtabs [role=tab]{flex:1 1 auto}
.mtgbox .xpane[hidden]{display:none}
.mtgbox .xpane .xhead{display:flex;justify-content:flex-end;margin:0 0 .4rem}
.mtgbox .xpane textarea{min-height:12rem}
.mtgbox .xpane + .xpane{margin-top:1rem}
/* a phone's full-screen sheet: the text fills the height instead of leaving a void above Close */
@media (max-width:599.98px){ #export.mtgbox .body{display:flex;flex-direction:column}
  #export.mtgbox .body > .xpane:not([hidden]){flex:1 1 auto;display:flex;flex-direction:column;min-height:0}
  #export.mtgbox .xpane textarea{flex:1 1 auto;min-height:8rem} }
.mtgbox .body .fold summary{display:inline-flex;align-items:center;min-height:44px;cursor:pointer;
  color:var(--link);font-weight:700}
.mtgbox .body .fold textarea{min-height:7rem;margin-top:.5rem}
.mtgbox .body .field label{display:block;font-weight:700;margin:0 0 .25rem}
.mtgbox .body .field input:not([type=file]),.mtgbox .body .field select{width:100%}
.mtgbox.dangerbox .bh h2{color:var(--danger-text)}
/* a phone at card size L: one stack a row (S and M stay two a row) */
@media (max-width:420px){ html[data-card-size=l] .deckview.stacks{grid-template-columns:minmax(0,1fr)} }
"""
)


def _select(name: str, options: dict[str, str], current: str, label: str, ic: str) -> str:
    opts = "".join(
        f"<option value='{esc(k)}'{' selected' if k == current else ''}>{esc(v)}</option>"
        for k, v in options.items()
    )
    return (
        f"<div class='field'><label for='f-{name}'>{esc(label)}</label>"
        f"<span class='sel'>{icon(ic)}<select id='f-{name}' name='{name}'>{opts}</select></span></div>"
    )


def toolbar_html(deck: Deck, *, own: bool, view: str, group: str, sort: str, q: str) -> str:
    """The toolbar panel. Two separate forms: Quick add (goes to the editor) and the view controls.
    They must never nest: HTML has no nested forms, the parser would end the outer form at the
    inner one's close tag and the View / Group / Sort selects would submit nothing."""
    did = esc(deck.id)
    add = (
        "<form method='get' action='/decks/{did}/edit' class='field add quick'>"
        "<label for='quick'>Add card</label><div class='quickrow'>"
        "<input id='quick' type='text' name='add' placeholder='Quick add (card name)' data-suggest='cards' "
        "autocomplete='off'>"
        # on a phone the label is hidden and only the icon shows: the name stays the same
        f"<button type='submit' aria-label='Card search'>{icon('search')} "
        "<span>Card search</span></button></div></form>"
        if own
        else ""
    ).replace("{did}", did)
    return (
        "<section class='toolbar panel'><div class='controls'>"
        f"{add}"
        f"<form method='get' action='/decks/{did}' class='views' id='viewform'>"
        + _select("view", VIEWS, view, "View as", "layers")
        + (card_size_control() if view in PICTURE_VIEWS else "")
        + _select("group", GROUPS, group, "Group by", "grid")
        + _select("sort", SORTS, sort, "Sort by", "sort")
        + "<div class='field filter'><label for='q'>Local filter</label><span class='search'>"
        f"<input id='q' type='search' name='q' value='{esc(q)}' placeholder='Filter deck (eg: Sol Ring)' "
        "autocomplete='off'>"
        f"<button type='submit' aria-label='Apply filter' title='Apply filter'>{icon('search')}</button>"
        "</span></div>"
        + (
            # the hint is the grip icon's hover text (show, not tell); the grip itself is deck.js's
            f"<span class='hint' title='{DRAG_HINT}' aria-label='{DRAG_HINT}' role='img'>"
            f"{icon('drag')}</span>"
            if own and view in PICTURE_VIEWS
            else ""
        )
        + "<noscript><button type='submit' class='apply'>Apply</button></noscript>"
        "</form></div></section>"
    )


DRAG_HINT = "Drag a card onto another category to move it; on touch, hold it first."


def _finish_badge(card: DeckCard) -> str:
    if card.modifier and card.modifier != "Normal":
        return f"<span class='finish' title='{esc(card.modifier)}'>{esc(card.modifier[:1])}</span>"
    return ""


def _label_dot(card: DeckCard) -> str:
    """Archidekt's colour tag: a dot in the tag's colour, its name for screen readers and on hover
    (a tag with no name, Archidekt's empty one, shows nothing)."""
    name, colour = split_label(card.label)
    if not name:
        return ""
    style = f" style='background:{colour}'" if colour else ""  # split_label lets only #rrggbb through
    return (
        f"<span class='tagdot'{style} title='Tag: {esc(name)}'>"
        f"<span class='sr-only'>tag {esc(name)}</span></span>"
    )


def _owned_dot(card: DeckCard, owned: dict[str, int] | None) -> str:
    """Archidekt's green collection dot: the member owns copies of this card (by name)."""
    if not owned:
        return ""
    n = owned.get(card.name.lower()) or owned.get(front_name(card.name).lower())
    if not n:
        return ""
    return f"<span class='owned' title='You own {n}'><span class='sr-only'>owned {n}</span></span>"


def front_name(name: str) -> str:
    return name.split(" // ")[0].strip() if " // " in name else name


TABLE_HEAD = (
    "<li class='th' aria-hidden='true'><span class='q'>Qty</span><span>Name</span>"
    "<span class='ty'>Type</span>"
    "<span class='mv'>MV</span><span class='mc'>Cost</span><span class='set'>Set</span>"
    "<span class='price'>Price</span></li>"
)
ROW_VIEWS = ("text", "table", "brewer")


def card_row(card: DeckCard, *, deck: Deck, view: str = "text", owned: dict[str, int] | None = None) -> str:
    """One card as a row of the list views, from the same data as Archidekt's: Text (count, name,
    cost, set, price, the picture on hover), Table (the Text row with the type line, mana value
    and collector number in columns of their own) and Brewer (a small picture beside the name and
    cost, the type line and the rules text, so a deck can be read without opening every card,
    as read from Archidekt's site code). The Table's mana value is the deck's custom one when the
    card has one, the printed one on hover."""
    cls = " side" if not deck.in_deck(card) else ""
    qty = f"<span class='q'>{card.quantity}</span>"
    marks = f"{_label_dot(card)}{_owned_dot(card, owned)}"
    name = f"<span class='name' title='{esc(card.name)}'>{esc(card.name)}</span>{_finish_badge(card)}"
    cost = f"<span class='mc'>{mana_html(card.mana_cost)}</span>"
    price = f"<span class='price'>{money(card.price) if card.price is not None else ''}</span>"
    if view == "brewer":
        img = card_image(card)
        pic = (
            f"<img class='art' src='{esc(img)}' alt='' loading='lazy'>"
            if img
            else "<span class='art ph' aria-hidden='true'></span>"
        )
        cls = " brew" + cls
        body = (
            f"{pic}<span class='bd'><span class='n'>{qty}{marks}{name}{cost}</span>"
            f"<span class='ty'>{esc(card.type_line)}</span>"
            + (f"<span class='tx'>{esc(card.oracle_text)}</span>" if card.oracle_text else "")
            + f"{price}</span>"
        )
    elif view == "table":
        printing = print_label(card.set_code, card.collector_number)
        mv = mana_value(card)
        printed = (
            f" title='Custom mana value; printed {card.cmc:g}'"
            if card.custom_cmc is not None and card.cmc is not None
            else ""
        )
        body = (
            f"{qty}<span class='n'>{marks}{name}</span>"
            f"<span class='ty' title='{esc(card.type_line)}'>{esc(card.type_line)}</span>"
            f"<span class='mv'{printed}>{f'{mv:g}' if mv is not None else ''}</span>{cost}"
            f"<span class='set' title='{esc(printing)}'>{esc(printing)}</span>{price}"
        )
    else:
        img = card_image(card)
        hover = f"<span class='hover'><img src='{esc(img)}' alt='' loading='lazy'></span>" if img else ""
        body = (
            f"{qty}<span class='n'>{marks}{name}{hover}</span>{cost}"
            f"<span class='set' title='{esc(print_label(card.set_code, card.collector_number))}'>"
            f"{esc(card.set_code.upper())}</span>{price}"
        )
    return (
        f"<li class='row{cls}' tabindex='0' role='button' data-name='{esc(card.name.lower())}' "
        f"data-card='{esc(card.name)}'{_card_data(card, deck)}>{body}</li>"
    )


def card_view_attrs(card: DeckCard, *, img: str | None = None) -> str:
    """Data attributes ``static/cardview.js`` reads to show the whole card: image, printing,
    finish, mana cost, type line, rules text, power and toughness or loyalty, and every face."""
    pt = f"{card.power}/{card.toughness}" if card.power or card.toughness else ""
    faces = [
        {
            "name": f["name"],
            "mana": f["mana_cost"],
            "type": f["type_line"],
            "text": f["text"],
            "pt": f"{f['power']}/{f['toughness']}" if f["power"] or f["toughness"] else "",
            "loyalty": f["loyalty"],
        }
        for f in card.faces
    ]
    legal = ",".join(sorted(k for k, v in card.legalities.items() if v == "legal"))
    return (
        (f" data-img='{esc(img)}'" if img else "")
        + f" data-set='{esc(card.set_code.upper())} {esc(card.collector_number)}'"
        f" data-type='{esc(card.type_line)}'"
        f" data-mana='{esc(card.mana_cost)}'"
        f" data-finish='{esc(card.modifier)}'"
        + (f" data-text='{esc(card.oracle_text)}'" if card.oracle_text else "")
        + (f" data-pt='{esc(pt)}'" if pt else "")
        + (f" data-loyalty='{esc(card.loyalty)}'" if card.loyalty else "")
        + (f" data-faces='{esc(json.dumps(faces, separators=(',', ':')))}'" if faces else "")
        + (f" data-rarity='{esc(card.rarity)}'" if card.rarity else "")
        + (f" data-price='{card.price:.2f}'" if card.price is not None else "")
        + (f" data-artist='{esc(card.artist)}'" if card.artist else "")
        + (f" data-flavor='{esc(card.flavor)}'" if card.flavor else "")
        + (f" data-salt='{card.salt:g}'" if card.salt is not None else "")
        + (f" data-rank='{card.edhrec_rank}'" if card.edhrec_rank is not None else "")
        + (f" data-legal='{esc(legal)}'" if card.legalities else "")
        + (" data-gc='1'" if card.game_changer else "")
    )


def _card_data(card: DeckCard, deck: Deck) -> str:
    """Data attributes the page script reads for the card viewer and for its own edits (the card
    menu, the viewer's quantity buttons, dragging between stacks): the row's count, zone (main
    or side), first category (empty when Archidekt has none) and relation id, which pick the row
    an edit targets when the same card sits in the deck and on the maybeboard. A row in more than
    one category lists them all in data-allcats, so an Undo of its removal puts every one back.
    data-cmc is the printed mana value, which a cleared custom one (data-mv) goes back to.
    data-companion and data-flipped mark a companion and a card set to show its back, which an
    Undo of its removal puts back too."""
    zone = "main" if deck.in_deck(card) else "side"
    cat = card.categories[0] if card.categories else ""
    rel = f" data-rel='{card.relation_id}'" if card.relation_id is not None else ""
    allcats = (
        f" data-allcats='{esc(json.dumps(list(card.categories), separators=(',', ':')))}'"
        if len(card.categories) > 1
        else ""
    )
    return (
        card_view_attrs(card, img=card_image(card))
        + f" data-qty='{card.quantity}' data-zone='{zone}' data-cat='{esc(cat)}'{rel}{allcats}"
        + (f" data-label='{esc(card.label)}'" if split_label(card.label)[0] else "")
        + (f" data-mv='{mana_value_text(card.custom_cmc)}'" if card.custom_cmc is not None else "")
        + (f" data-cmc='{card.cmc:g}'" if card.cmc is not None else "")
        + (f" data-notes='{esc(card.notes)}'" if card.notes else "")
        + (" data-companion='1'" if card.companion else "")
        + (" data-flipped='1'" if card.flipped_default else "")
    )


def image_card(card: DeckCard, *, deck: Deck, owned: dict[str, int] | None = None) -> str:
    img = card_image(card)
    body = (
        f"<img src='{esc(img)}' alt='{esc(card.name)}' loading='lazy'>"
        if img
        else (
            "<span class='ph'><span class='t'>"
            f"<span class='nm'>{esc(card.name)}</span>{mana_html(card.mana_cost)}</span>"
            f"<span class='ty'>{esc(' '.join(card.types) or '')}</span></span>"
        )
    )
    qty = f"<span class='qty'>{card.quantity}</span>"
    extra = ""
    if card.companion:
        extra += "<span class='corner comp' title='Companion'></span>"
    if card.game_changer:
        extra += "<span class='corner gc' title='Game changer'></span>"
    return (
        f"<div class='c' data-name='{esc(card.name.lower())}' data-card='{esc(card.name)}'"
        f"{_card_data(card, deck)} "
        f"title='{esc(card.name)}' tabindex='0' role='button'>{body}{qty}{extra}"
        f"{_finish_badge(card)}{_owned_dot(card, owned)}{_label_dot(card)}</div>"
    )


def cards_html(
    deck: Deck, *, view: str, group: str, sort: str, q: str, own: bool, owned: dict[str, int] | None = None
) -> str:
    groups = group_cards(deck, group, sort, q)
    if not groups:
        return "<section class='panel'><p class='muted'>No cards match the filter.</p></section>"
    did = esc(deck.id)
    excluded = deck.excluded_categories()
    sections = []
    for name, cards in groups:
        qty = sum(c.quantity for c in cards)
        price = sum((c.price or 0) * c.quantity for c in cards)
        strike = " strike" if name in excluded else ""
        menu = (
            f"<details class='dd'><summary class='icon-only btn-ghost' aria-label='Options for {esc(name)}' "
            f"title='Category options'>{icon('more')}</summary><div class='menu'>"
            f"<a href='/decks/{did}/edit#cat-{quote(name, safe='')}'>{icon('edit')} Edit cards in "
            f"{esc(name)}</a>"
            f"<a href='/decks/{did}/settings#categories'>{icon('settings')} Category options</a>"
            "</div></details>"
            if own and group == "category"
            else ""
        )
        head = (
            f"<div class='stackhead'><h2><span class='title{strike}' title='{esc(name)}'>{esc(name)}</span>"
            f"{menu}</h2>"
            f"<div class='meta'>Qty: {qty}" + (f" · Price: {money(price)}" if price else "") + "</div></div>"
        )
        if view in ROW_VIEWS:
            body = (
                "<ul class='plain rows'>"
                + (TABLE_HEAD if view == "table" else "")
                + "".join(card_row(c, deck=deck, view=view, owned=owned) for c in cards)
                + "</ul>"
            )
        else:
            imgs = "".join(image_card(c, deck=deck, owned=owned) for c in cards)
            body = f"<div class='cards'>{imgs}</div>"
        sections.append(f"<section class='stack' data-group='{esc(name)}'>{head}{body}</section>")
    # data-own marks the member's own deck (the card menu and the viewer offer edits); data-drop
    # the category grouping on it, where cards drag between stacks. Every one of those edits is
    # saved to Archidekt at once (the edit endpoint), with Undo and Redo. The card menu's "Move to" list
    # (deck.js) names the deck's own categories whatever the grouping, and knows the maybeboard.
    cats = [c["name"] for c in deck.categories if isinstance(c.get("name"), str)]
    droppable = (
        " data-own='1'"
        + (" data-drop='1'" if group == "category" else "")
        + f" data-cats='{esc(json.dumps(cats, separators=(',', ':')))}'"
        f" data-side='{esc(deck.side_category())}'"
        f" data-excluded='{esc(json.dumps(sorted(excluded), separators=(',', ':')))}'"
        if own
        else ""
    )
    # The card menu (deck.js) draws these icons; a template keeps them out of the page's flow.
    icons = (
        "<template class='icons'>"
        + "".join(
            f"<span data-ic='{n}'>{icon(n)}</span>"
            for n in ("eye", "bookmark", "swap", "tag", "eye-off", "x", "edit", "external", "image")
        )
        + "</template>"
    )
    return (
        f"<div class='deckview {esc(view)}' id='cards' data-deck='{esc(deck.id)}' "
        f"data-grouping='{esc(group)}'{droppable}>{icons}{''.join(sections)}</div>"
    )


def _bar(parts: dict[str, float], *, label: str) -> str:
    total = sum(v for v in parts.values() if v) or 0
    if not total:
        return (
            f"<div class='cbar empty' role='img' aria-label='{esc(label)}: no data'>"
            "<span>No mana data</span></div>"
        )
    segs = "".join(
        f"<span class='seg seg-{k}' style='width:{100 * v / total:.1f}%' "
        f"title='{COLOUR_NAMES.get(k, k)}: {v:g}'></span>"
        for k, v in parts.items()
        if v
    )
    return f"<div class='cbar' role='img' aria-label='{esc(label)}'>{segs}</div>"


def _odds_groups(cards: list[DeckCard]) -> dict[str, dict[str, int]]:
    """Card counts per group, the way Archidekt's "Probability of draw" tab groups them."""
    groups: dict[str, Counter] = {
        "Categories": Counter(),
        "Primary category": Counter(),
        "Card name": Counter(),
        "Types": Counter(),
        "Sub types": Counter(),
        "Mana value": Counter(),
    }
    for c in cards:
        # a card without a category of its own sits under its auto category, as the page groups it
        for cat in c.categories or [auto_category(c)]:
            groups["Categories"][cat] += c.quantity
        groups["Primary category"][(c.categories or [auto_category(c)])[0]] += c.quantity
        groups["Card name"][c.name] += c.quantity
        for t in c.types or ["Other"]:
            groups["Types"][t] += c.quantity
        for t in c.subtypes:
            groups["Sub types"][t] += c.quantity
        mv = mana_value(c)  # the deck's custom mana value when it has one, as the curve counts it
        if mv is not None and not is_land(c):
            groups["Mana value"][f"{mv:g}"] += c.quantity
    return {k: dict(sorted(v.items(), key=lambda kv: (-kv[1], kv[0]))) for k, v in groups.items() if v}


def _odds_html(cards: list[DeckCard]) -> str:
    """The "Probability of draw" calculator: a form plus the counts as a JSON data block the page
    script reads (a data block is not executed, so the page's script policy allows it)."""
    size = sum(c.quantity for c in cards)
    if not size:
        return ""
    groups = _odds_groups(cards)
    data = json.dumps({"size": size, "groups": groups}, separators=(",", ":")).replace("</", "<\\/")
    options = "".join(f"<option value='{esc(k)}'>{esc(k)}</option>" for k in groups)
    return (
        "<div class='odds' id='odds'><h3>Probability of draw</h3>"
        "<form class='oddsform' onsubmit='return false'>"
        "<select name='mode' aria-label='At least or exactly'><option value='atleast'>At least</option>"
        "<option value='exact'>Exactly</option></select>"
        "<input type='number' name='k' value='1' min='0' max='99' aria-label='How many'> "
        "<span data-plural='k'>card</span> by "
        f"<select name='by' aria-label='Group by'>{options}</select> having drawn "
        f"<input type='number' name='n' value='7' min='1' max='{size}' aria-label='Cards drawn'> "
        "<span data-plural='n'>cards</span>"
        "</form>"
        "<table class='qty oddstable'><thead><tr><th>Group</th><th>Qty</th><th>Odds</th></tr></thead>"
        "<tbody><tr><td class='muted' colspan='3'>Turn on scripts to see the odds.</td></tr></tbody></table>"
        f"<script type='application/json' id='odds-data'>{data}</script>"
        "</div>"
    )


def legality_panel_html(deck: Deck, stats: dict[str, Any] | None) -> str:
    """The stats panel's Legality block: every rule of the deck's format it does not meet, each
    with its reason and cards (as archidekt.com's legality list gives them), or one green line.
    Re-rendered by the edit endpoint after a card changes."""
    if not deck.format:
        return ""
    problems = legal_problems(stats)
    if not problems:
        return (
            f"<div class='legality'><h3>Legality</h3><p class='ok'>{icon('check')} Legal in "
            f"{esc(format_label(deck.format))}</p></div>"
        )
    return (
        f"<div class='legality'><h3>Legality</h3><p>{icon('x')} Not legal in "
        f"{esc(format_label(deck.format))}:</p><ul>"
        + "".join(f"<li>{esc(p)}</li>" for p in problems)
        + "</ul></div>"
    )


def checks_panel_html(deck: Deck, stats: dict[str, Any] | None) -> str:
    """The stats panel's Deck checks block (deck_stats.deck_checks rendered as Archidekt's
    checklist). Re-rendered by the edit endpoint after a card changes."""
    stats = stats or {}
    checks = stats.get("checks") or {}
    if checks:
        size = checks.get("deck_size") or {}
        zone = checks.get("commander_zone") or {}
        rows = [
            (
                "Deck size",
                f"{size.get('actual', 0)}"
                + (f" of {size['expected']}" if size.get("expected") else "")
                + (" cards" if not size.get("expected") else ""),
                size.get("ok", True),
            )
        ]
        family = checks.get("format_family") or "custom"

        def names(items: list[Any], key: str = "name", qty: str | None = None) -> str:
            shown = [
                esc(x[key] if isinstance(x, dict) else x)
                + (f" ×{x[qty]}" if qty and isinstance(x, dict) else "")
                for x in items[:6]
            ]
            return ", ".join(shown) + ("…" if len(items) > 6 else "")

        if family == "commander":
            pairing = zone.get("pairing")
            rows.append(
                (
                    "Commander",
                    f"{zone.get('count', 0)} in the Commander category"
                    + (f" ({esc(pairing)})" if pairing else "")
                    + (
                        " · cannot command: " + names(zone["cannot_command"])
                        if zone.get("cannot_command")
                        else ""
                    )
                    + (
                        f" · not checked: {esc(zone['unverified_note'])}"
                        if zone.get("unverified_note")
                        else ""
                    ),
                    zone.get("ok", True),
                )
            )
            outside = checks.get("colour_identity_violations") or []
            rows.append(
                ("Color identity", "all cards inside" if not outside else names(outside), not outside)
            )
        elif family == "highlander" and not zone.get("ok", True):
            rows.append(
                ("Command zone", f"{zone.get('count', 0)} in the Commander category; none expected", False)
            )
        if family in ("commander", "highlander"):
            dupes = checks.get("singleton_violations") or []
            rows.append(
                ("Singleton", "no duplicates" if not dupes else names(dupes, qty="quantity"), not dupes)
            )
        if family == "constructed":
            copies = checks.get("copy_limit_violations") or []
            rows.append(
                (
                    "Copies",
                    "at most four of each" if not copies else names(copies, qty="quantity"),
                    not copies,
                )
            )
            side = checks.get("sideboard") or {}
            rows.append(
                (
                    "Sideboard",
                    f"{side.get('count', 0)} of at most {side.get('maximum', 15)}",
                    side.get("ok", True),
                )
            )
        legal = checks.get("legality") or {}
        if deck.format and deck.format != "custom":
            bad = [f"banned: {names(legal['banned'])}"] if legal.get("banned") else []
            if legal.get("not_legal"):
                bad.append(f"not legal: {names(legal['not_legal'])}")
            if legal.get("restricted_violations"):
                bad.append(
                    f"restricted, more than one copy: {names(legal['restricted_violations'], qty='quantity')}"
                )
            text = "every card legal" if legal.get("ok", True) else "; ".join(bad)
            if legal.get("unknown"):
                text += f" ({plural(legal['unknown'], 'card')} without legality data)"
            rows.append((f"Legal in {esc(format_label(deck.format))}", text, legal.get("ok", True)))
        comp = checks.get("companion") or {}
        if comp.get("count"):
            rows.append(
                (
                    "Companion",
                    f"{comp['count']} marked"
                    + (
                        " · without the ability: " + names(comp["not_companions"])
                        if comp.get("not_companions")
                        else ""
                    ),
                    comp.get("ok", True),
                )
            )
        if bracket_row := checks_bracket_row(checks):  # bracket_view.py
            rows.append(bracket_row)
        uncat = checks.get("uncategorised") or []
        rows.append(
            (
                "Categories",
                "every card has one"
                if not uncat
                else f"{len(uncat)} without: "
                + ", ".join(esc(x) for x in uncat[:6])
                + ("…" if len(uncat) > 6 else ""),
                not uncat,
            )
        )
        checks_html = (
            "<div class='checks'><h3>Deck checks</h3><ul>"
            + "".join(
                f"<li class='{'ok' if ok else 'bad'}'>{icon('check' if ok else 'x')} <b>{label}</b> "
                f"<span>{value}</span></li>"
                for label, value, ok in rows
            )
            + "</ul></div>"
        )
    else:
        checks_html = ""
    return checks_html


# -- stats panel: Archidekt's extra controls (curve by type or category, bar or pie, lands only,
# out-of-identity pips, quantities by subtype and keyword; a click on a chart part focuses its
# cards). Every variant is drawn here; static/deckstats.js only shows one and filters the list.

CURVE_MODES = (("none", "None"), ("type", "Type"), ("category", "Category"))
SERIES_SLOTS = 7  # categorical colours k1..k7; any further group folds into "Other" (k0)


def _focus(cards: list[DeckCard], label: str) -> str:
    """Attributes that let a click on a chart part show just these cards in the deck list (the
    names match the list's lower-case ``data-name``)."""
    names = sorted({c.name.lower() for c in cards})
    return f" data-focus='{esc(json.dumps(names))}' data-focus-label='{esc(label)}'"


def _toggle(group: str, value: str, label: str, pressed: bool) -> str:
    return (
        f"<button type='button' data-set='{group}' data-value='{value}' "
        f"aria-pressed='{'true' if pressed else 'false'}'>{esc(label)}</button>"
    )


def _qty(cards: list[DeckCard]) -> int:
    return sum(c.quantity for c in cards)


def _curve_variant(cards: list[DeckCard], mode: str) -> str:
    """One mana curve: plain bars ("none") or bars stacked by primary type or primary category,
    with a legend. The bars are a picture (role=img, every number in its label and on the bar);
    the mana value buttons under them and the legend's buttons focus the cards."""
    key = {"type": primary_type, "category": primary_category}.get(mode)
    cols: dict[str, list[DeckCard]] = {b: [] for b in CURVE_BUCKETS}
    for c in cards:
        b = curve_bucket(c)
        if b is not None:
            cols[b].append(c)
    top = max([_qty(cs) for cs in cols.values()] + [1])
    slot: dict[str, int] = {}
    if key:
        totals: Counter[str] = Counter()
        for cs in cols.values():
            for c in cs:
                totals[key(c)] += c.quantity
        ordered = [g for g, _ in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0].casefold()))]
        slot = {g: (i + 1 if i < SERIES_SLOTS else 0) for i, g in enumerate(ordered)}

    def group(c: DeckCard) -> str:
        g = key(c) if key else ""
        return g if slot.get(g) else "Other"

    rank = lambda g: (g == "Other", slot.get(g, 0))  # noqa: E731
    groups: dict[str, list[DeckCard]] = {}
    bars, axis, said = [], [], []
    for b, cs in cols.items():
        n = _qty(cs)
        mv = f"mana value {b}"
        if key and cs:
            per: dict[str, list[DeckCard]] = {}
            for c in cs:
                per.setdefault(group(c), []).append(c)
                groups.setdefault(group(c), []).append(c)
            order = sorted(per, key=rank)
            segs = "".join(
                f"<i class='sg k{slot.get(g, 0)}' style='flex-grow:{_qty(per[g])}' "
                f"title='{esc(g)}: {_qty(per[g])}'{_focus(per[g], f'{g} at {mv}')}></i>"
                for g in order
            )
            said.append(f"{b}: {n} (" + ", ".join(f"{_qty(per[g])} {g}" for g in order) + ")")
        else:
            segs = f"<i class='sg'{_focus(cs, mv)}></i>" if cs else ""
            said.append(f"{b}: {n}")
        bars.append(
            f"<div class='bar'><b>{n}</b><span class='col' style='height:{max(2, round(100 * n / top))}%'>"
            f"{segs}</span></div>"
        )
        axis.append(
            f"<button type='button' class='mv'{_focus(cs, mv)} aria-label='Show the "
            f"{plural(n, 'card')} at {mv}'{'' if cs else ' disabled'}>{esc(b)}</button>"
        )
    how = {"type": " by type", "category": " by category"}.get(mode, "")
    legend = ""
    if key:
        legend = (
            f"<ul class='slegend' aria-label='Mana curve{how}: show the cards of'>"
            + "".join(
                f"<li><button type='button'{_focus(groups[g], g)}><i class='sw k{slot.get(g, 0)}'></i>"
                f"{esc(g)} <b>{_qty(groups[g])}</b></button></li>"
                for g in sorted(groups, key=rank)
            )
            + "</ul>"
        )
    return (
        f"<div class='curve' role='img' aria-label='Mana curve{how}, cards per mana value: "
        f"{esc('; '.join(said))}'>{''.join(bars)}</div>"
        f"<div class='curveaxis'>{''.join(axis)}</div>{legend}"
    )


def curve_html(cards: list[DeckCard]) -> str:
    """The mana curve with Archidekt's "Mana curve by" choice: None, Type or Category."""
    return (
        "<div class='curvebox statbox'>"
        "<div class='statctl' role='group' aria-label='Mana curve by' hidden><span>Mana curve by</span>"
        + "".join(_toggle("curve", v, label, v == "none") for v, label in CURVE_MODES)
        + "</div>"
        + "".join(
            f"<div data-curve='{v}'{'' if v == 'none' else ' hidden'}>{_curve_variant(cards, v)}</div>"
            for v, _ in CURVE_MODES
        )
        + "</div>"
    )


def _focus_bar(parts: dict[str, float], members: dict[str, list[DeckCard]], *, label: str, what: str) -> str:
    """``_bar`` with each colour's segment focusing its cards on a click."""
    total = sum(v for v in parts.values() if v)
    if not total:
        return _bar(parts, label=label)
    segs = "".join(
        f"<span class='seg seg-{k}' style='width:{100 * v / total:.1f}%' title='{COLOUR_NAMES[k]}: {v:g}'"
        f"{_focus(members.get(k, []), f'{COLOUR_NAMES[k]} {what}')}></span>"
        for k, v in parts.items()
        if v
    )
    said = ", ".join(f"{COLOUR_NAMES[k]} {100 * v / total:.0f}%" for k, v in parts.items() if v)
    return f"<div class='cbar' role='img' aria-label='{esc(label)}: {esc(said)}'>{segs}</div>"


def _arc(cx: float, cy: float, r: float, a0: float, a1: float) -> str:
    """An SVG path for a pie slice from a0 to a1 (fractions of a turn, clockwise from the top)."""
    if a1 - a0 >= 0.9999:
        return f"M{cx} {cy - r}A{r} {r} 0 1 1 {cx - 0.01} {cy - r}Z"
    x0, y0 = cx + r * math.sin(2 * math.pi * a0), cy - r * math.cos(2 * math.pi * a0)
    x1, y1 = cx + r * math.sin(2 * math.pi * a1), cy - r * math.cos(2 * math.pi * a1)
    return f"M{cx} {cy}L{x0:.2f} {y0:.2f}A{r} {r} 0 {1 if a1 - a0 > 0.5 else 0} 1 {x1:.2f} {y1:.2f}Z"


def _pie(parts: dict[str, float], members: dict[str, list[DeckCard]], *, title: str, unit: str) -> str:
    """A colour pie (inline SVG, role=img with every share in its label) and a legend of buttons
    with the numbers that focus each colour's cards."""
    shown = {k: v for k, v in parts.items() if v}
    total = sum(shown.values())
    if not total:
        return (
            f"<figure class='pie'><figcaption>{esc(title)}</figcaption>"
            "<p class='muted small'>No data</p></figure>"
        )
    slices, a = [], 0.0
    for k, v in shown.items():
        b = a + v / total
        slices.append(
            f"<path class='seg-{k}' d='{_arc(50, 50, 46, a, b)}'"
            f"{_focus(members.get(k, []), f'{COLOUR_NAMES[k]} {title.lower()}')}>"
            f"<title>{COLOUR_NAMES[k]}: {v:g} ({100 * v / total:.0f}%)</title></path>"
        )
        a = b
    said = ", ".join(f"{COLOUR_NAMES[k]} {v:g} {unit} ({100 * v / total:.0f}%)" for k, v in shown.items())
    legend = "".join(
        f"<li><button type='button'{_focus(members.get(k, []), f'{COLOUR_NAMES[k]} {title.lower()}')}>"
        f"<i class='pip pip-{k}'></i>{COLOUR_NAMES[k]} <b>{100 * v / total:.0f}%</b>"
        f"<span class='muted'>{v:g}</span></button></li>"
        for k, v in shown.items()
    )
    return (
        f"<figure class='pie'><figcaption>{esc(title)}</figcaption>"
        f"<svg viewBox='0 0 100 100' role='img' aria-label='{esc(title)} by color: {esc(said)}'>"
        f"{''.join(slices)}</svg><ul class='slegend'>{legend}</ul></figure>"
    )


def _colour_cards(
    pips: dict[str, float],
    sources: dict[str, float],
    cost_cards: Counter[str],
    prod_cards: Counter[str],
    members: dict[str, list[DeckCard]],
) -> str:
    pip_total = sum(pips.values()) or 1
    src_total = sum(sources.values()) or 1
    return "".join(
        "<div class='ccard'>"
        f"<div class='cname'><button type='button' class='rowbtn'"
        f"{_focus(members.get(col, []), f'{COLOUR_NAMES[col]} cards and sources')}>"
        f"<i class='pip pip-{col}'></i> {COLOUR_NAMES[col]}</button></div>"
        f"<div class='lbl'>Cost</div><div class='pbar'><span class='fill seg-{col}' "
        f"style='width:{100 * pips.get(col, 0) / pip_total:.0f}%'></span>"
        f"<b>{100 * pips.get(col, 0) / pip_total:.0f}%</b></div>"
        f"<div class='sub'>{plural(pips.get(col, 0), 'pip')} · {plural(cost_cards.get(col, 0), 'card')}</div>"
        f"<div class='lbl'>Production</div><div class='pbar'><span class='fill seg-{col}' "
        f"style='width:{100 * sources.get(col, 0) / src_total:.0f}%'></span>"
        f"<b>{100 * sources.get(col, 0) / src_total:.0f}%</b></div>"
        f"<div class='sub'>{sources.get(col, 0):g} mana · {plural(prod_cards.get(col, 0), 'card')}</div>"
        "</div>"
        for col in WUBRG
        if pips.get(col) or sources.get(col)
    )


def colours_html(deck: Deck, stats: dict[str, Any]) -> str:
    """The colour panel with Archidekt's toggles: Bar or Pie, Lands only (production counted from
    the lands alone), and the mana-cost pips outside the commander's colour identity."""
    cards = deck.main_cards
    pips = {k: float(v) for k, v in (stats.get("colour_pips") or {}).items()}
    cost_members: dict[str, list[DeckCard]] = {}
    cost_cards: Counter[str] = Counter()
    for c in cards:
        for col in mana_pips(c.mana_cost):
            cost_cards[col] += c.quantity
            cost_members.setdefault(col, []).append(c)
    variants = []
    for src, key, only_lands in (("all", "mana_sources", False), ("lands", "mana_sources_lands", True)):
        sources = {k: float(v) for k, v in (stats.get(key) or {}).items() if k in WUBRG}
        prod_members: dict[str, list[DeckCard]] = {}
        prod_cards: Counter[str] = Counter()
        for c in cards:
            if only_lands and not is_land(c):
                continue
            for col in c.mana_production or {}:
                if col in WUBRG:
                    prod_cards[col] += c.quantity
                    prod_members.setdefault(col, []).append(c)
        both = {k: cost_members.get(k, []) + prod_members.get(k, []) for k in WUBRG}
        prod = "Production (lands only)" if only_lands else "Production"
        bar = (
            "<div class='lbl'>Cost</div>"
            + _focus_bar(pips, cost_members, label="Mana cost by color", what="mana cost")
            + f"<div class='lbl'>{prod}</div>"
            + _focus_bar(sources, prod_members, label=f"{prod} by color", what=prod.lower())
            + f"<div class='ccards'>{_colour_cards(pips, sources, cost_cards, prod_cards, both)}</div>"
        )
        pie = (
            "<div class='pies'>"
            + _pie(pips, cost_members, title="Mana cost", unit="pips")
            + _pie(sources, prod_members, title=prod, unit="mana")
            + "</div>"
        )
        variants.append(
            f"<div data-src='{src}'{' hidden' if only_lands else ''}>"
            f"<div data-chart='bar'>{bar}</div><div data-chart='pie' hidden>{pie}</div></div>"
        )
    ctl = (
        "<div class='statctl' role='group' aria-label='Colors' hidden><span>Colors</span>"
        + _toggle("chart", "bar", "Bar", True)
        + _toggle("chart", "pie", "Pie", False)
        + "<button type='button' data-set='lands' aria-pressed='false'>Lands only</button></div>"
    )
    return f"<div class='colourbox statbox'>{ctl}{''.join(variants)}{_identity_html(deck, stats)}</div>"


def _identity_html(deck: Deck, stats: dict[str, Any]) -> str:
    """Mana-cost pips outside the commander's colour identity (only for a deck with one)."""
    ooi = stats.get("out_of_identity")
    if not ooi:
        return ""
    ident = "".join(
        f"<i class='pip pip-{k}' title='{COLOUR_NAMES[k]}'></i>" for k in ooi.get("identity") or []
    )
    rows = ooi.get("cards") or []
    if not rows:
        return (
            f"<p class='ooi ok'>{icon('check')} <span>No mana-cost pips outside the commander's color "
            f"identity {ident or '(colorless)'}</span></p>"
        )
    names = {r["name"] for r in rows}
    hit = [c for c in deck.main_cards if c.name in names]
    pips = ooi.get("pips") or {}
    detail = ", ".join(f"{COLOUR_NAMES[k]} {v:g}" for k, v in pips.items())
    return (
        f"<p class='ooi bad'>{icon('x')} <span><b>{plural(sum(pips.values()), 'pip')} outside the "
        f"commander's color identity</b> {ident} on {plural(len(rows), 'card')} ({esc(detail)}) "
        f"<button type='button' class='rowbtn'{_focus(hit, 'cards with pips outside the identity')}>"
        "Show them</button></span></p>"
    )


def _qty_rows(counts: dict[str, int], cards: list[DeckCard], attr: str, what: str = "") -> str:
    """Rows of a quantity table whose names focus the cards they count."""
    by: dict[str, list[DeckCard]] = {}
    for c in cards:
        for v in getattr(c, attr):
            by.setdefault(v, []).append(c)
    return "".join(
        f"<tr><td><button type='button' class='rowbtn'{_focus(by.get(k, []), f'{k} {what}'.strip())}>"
        f"{esc(k)}</button></td><td>{v}</td></tr>"
        for k, v in counts.items()
    )


def stats_panel_html(deck: Deck, stats: dict[str, Any] | None) -> str:
    if not stats:
        return ""
    cards = deck.main_cards
    mv_total = sum(_mv_or(c, 0) * c.quantity for c in cards if not is_land(c))
    types = stats.get("type_counts") or {}
    rarities = stats.get("rarity_counts") or {}
    type_rows = _qty_rows(types, cards, "types")
    subtypes = stats.get("subtype_counts") or {}
    keywords = stats.get("keyword_counts") or {}
    sub_rows = _qty_rows(subtypes, cards, "subtypes")
    kw_rows = _qty_rows(keywords, cards, "keywords")
    rarity_rows = "".join(f"<tr><td>{esc(k.capitalize())}</td><td>{v}</td></tr>" for k, v in rarities.items())
    problems_html = legality_panel_html(deck, stats)
    odds_html = _odds_html(cards)
    checks_html = checks_panel_html(deck, stats)
    avg_mv = stats.get("average_mana_value") if stats.get("average_mana_value") is not None else "–"
    no_types = "<tr><td class=muted>No type data</td></tr>"
    bracket_html = stats_bracket_html(stats)  # bracket_view.py
    return (
        "<section class='panel stats' id='stats'><div class='head'><h2>Deck stats</h2></div>"
        "<div class='grid'><div>"
        f"{colours_html(deck, stats)}"
        f"<p class='avg'><b>Avg Mana Value: {esc(avg_mv)}</b>"
        f"<br><span class='small'>Total Mana Value: {mv_total:.2f}</span></p>"
        f"{curve_html(cards)}"
        f"{odds_html}"
        "</div><div class='side'>"
        f"<div class='tiles'><div class='tile'><b>{stats.get('card_count', 0)}</b>"
        "<span>Deck size</span></div>"
        f"<div class='tile'><b>{stats.get('land_count', 0)}</b><span>Lands</span></div>"
        f"<div class='tile'><b>{est_cost_html(stats)}</b><span>Est cost (Archidekt)</span></div>"
        f"<div class='tile'><b>{stats.get('priced_cards', 0)}</b><span>Priced cards</span></div></div>"
        f"<h3>Quantity of types</h3><table class='qty'><tbody>{type_rows or no_types}</tbody></table>"
        + (f"<h3>Rarity</h3><table class='qty'><tbody>{rarity_rows}</tbody></table>" if rarity_rows else "")
        + (
            "<h3>Quantity by subtype</h3><div class='qtyscroll'><table class='qty'>"
            f"<tbody>{sub_rows}</tbody></table></div>"
            if sub_rows
            else ""
        )
        + "<h3>Quantity by keyword</h3>"
        + (
            f"<div class='qtyscroll'><table class='qty'><tbody>{kw_rows}</tbody></table></div>"
            if kw_rows
            else "<p class='small muted'>No keyword data: Archidekt sent no keywords for these cards.</p>"
        )
        + checks_html
        + problems_html
        + bracket_html
        + "</div></div></section>"
    )


def description_html(deck: Deck) -> str:
    text = rich_text(getattr(deck, "description", "")).strip()  # Archidekt keeps a Quill document
    if not text:
        body = "<p class='muted empty'><i>This deck has no description</i></p>"
    else:
        paras = [p.strip() for p in re.split(r"\n\s*\n", text[:8000]) if p.strip()]
        body = "".join(f"<p>{esc(p).replace(chr(10), '<br>')}</p>" for p in paras)
    return f"<section class='panel description'><h2>Description</h2>{body}</section>"


def deck_page_html(
    deck: Deck,
    stats: dict[str, Any] | None,
    *,
    own: bool,
    csrf: str | None,
    writes_enabled: bool,
    view: str,
    group: str,
    sort: str,
    q: str,
    notice: str = "",
    owned: dict[str, int] | None = None,
    forge_on: bool = False,
    waiting: list[dict[str, Any]] | None = None,
    boxes: str = "",
) -> str:
    # ``waiting``: the "Waiting changes" strip's rows (waiting_strip.py), drawn at the top under
    # the banner; ``boxes``: the page's boxes (deck_boxes_html), hidden until opened
    view = view if view in VIEWS else "text"
    group = group if group in GROUPS else "category"
    sort = sort if sort in SORTS else "name"
    return (
        banner_html(deck, stats, own=own, csrf=csrf, writes_enabled=writes_enabled, forge_on=forge_on)
        + boxes
        + waiting_strip.strip_html(waiting or [], csrf=csrf, deck_id=str(deck.id))
        + notice
        + toolbar_html(deck, own=own, view=view, group=group, sort=sort, q=q)
        + cards_html(deck, view=view, group=group, sort=sort, q=q, own=own, owned=owned)
        + stats_panel_html(deck, stats)
        + description_html(deck)
        + (comments_html(deck, writes_enabled=writes_enabled) if csrf else "")
    )


# -- compare page ---------------------------------------------------------------------------------

STAT_LABELS = {
    "card_count": "Cards",
    "distinct": "Distinct cards",
    "land_count": "Lands",
    "nonland_count": "Nonlands",
    "average_mana_value": "Average mana value",
    "price_total": "Price",
    "priced_cards": "Priced cards",
    "tutors": "Tutors",
    "extra_turns": "Extra turns",
    "mass_land_denial": "Mass land denial",
    "salt_total": "Salt",
}
COMPARE_FORGE_LINE = (
    "The same three precons for both decks, picked for the higher bracket. Each deck gets its own "
    "report under <a href='/reports'>Reports</a>."
)


def _delta_text(key: str, v: float) -> str:
    """A statistics difference, rounded: money for the price, two decimals for averages."""
    if key == "price_total":
        return f"${abs(v):,.2f}" if v > 0 else f"-${abs(v):,.2f}"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.2f}"
    return f"{int(v)}"


def _compare_row(name: str, qty_text: str, cards: dict[str, DeckCard]) -> str:
    """One card of a comparison list, a 44 px table row: the count, the card (a button that opens
    the card viewer, static/compare.js), its type and the row's ⋮. Every row has the shared card
    menu (static/menus.js's cardLinkOf: right-click, press and hold, ⋮, Shift+F10), a pasted card
    too."""
    card = cards.get(name.split(" // ", 1)[0].strip().casefold())
    kind = esc(card.type_line.split(" — ")[0]) if card is not None and card.type_line else ""
    attrs = card_view_attrs(card, img=card_image(card)) if card is not None else ""
    return (
        f"<div class='tr'><span class='q'>{esc(qty_text)}</span>"
        f"<button type='button' class='cardlink' data-card='{esc(card.name if card else name)}'{attrs}>"
        f"<span class='nm'>{esc(name)}</span>"
        + (f"<span class='s'>{kind}</span>" if kind else "")
        + "</button>"
        f"<button type='button' class='kebab' aria-label='Menu for {esc(name)}' aria-haspopup='menu' "
        f"title='Menu'>{icon('more')}</button></div>"
    )


def precon_labels(precons: dict[str, list[dict[str, Any]]]) -> list[str]:
    """'Deck name (Set)' for every preconstructed deck, the labels the compare box suggests."""
    return [
        f"{d['name']} ({set_name})"
        for set_name, rows in precons.items()
        for d in rows
        if d.get("id") and d.get("name")
    ]


def precon_by_label(precons: dict[str, list[dict[str, Any]]], text: str) -> int | None:
    """The deck id of the precon whose label is ``text`` (case-insensitive), or whose bare name is
    when exactly one precon carries that name; None otherwise."""
    want = " ".join(text.split()).casefold()
    if not want:
        return None
    by_name: list[int] = []
    for set_name, rows in precons.items():
        for d in rows:
            if not (d.get("id") and d.get("name")):
                continue
            if want == f"{d['name']} ({set_name})".casefold():
                return int(d["id"])
            if want == str(d["name"]).casefold():
                by_name.append(int(d["id"]))
    return by_name[0] if len(by_name) == 1 else None


def _compare_table(title: str, n: int, rows: str) -> str:
    empty = "<div class='tr'><span class='muted'>None</span></div>"
    return (
        f"<section class='cmp-col'><h2 class='cmp-h'>{title} <span class='n'>{n}</span></h2>"
        f"<div class='ui-table cmp-t' role='list'>{rows or empty}</div></section>"
    )


def compare_page_html(
    deck: Deck,
    *,
    other: Deck | dict[str, int] | None,
    other_name: str,
    other_ref: str,
    paste: str,
    result: dict[str, Any] | None,
    precons: dict[str, list[dict[str, Any]]],
    error: str = "",
    csrf: str | None = None,
    forge_on: bool = False,
) -> str:
    """Compare decks: a heading with a back link and one purpose line, a compact form (the other
    deck and Compare on one row, the pasted list in a fold), then widgets, three tables of card
    rows with the shared card menu, the statistics' differences, and the Forge panel. One orange
    button: Compare until results show with the Forge panel, then that panel's button (F5: no
    card size control here, the rows are text)."""
    did = esc(deck.id)
    name = esc(deck_title(deck))
    options = esc(json.dumps(precon_labels(precons), ensure_ascii=False))
    forge_html = (
        compare_forge_html(deck, other, other_name, csrf=csrf, forge_on=forge_on)
        if result is not None and other is not None and not error
        else ""
    )
    compare_kind = "" if forge_html else "primary"
    form = (
        f"<a class='cmp-back' href='/decks/{did}'>← {name}</a>"
        + ui.page_head("Compare decks", f"What “{name}” took out of another deck, put in and changed.")
        + f"<form method='get' action='/decks/{did}/compare' class='panel cmp-form'>"
        "<div class='row'><div class='field fld'><label for='cmp-with'>Other deck</label>"
        f"<input id='cmp-with' type='text' name='with' data-suggest='static' data-options='{options}' "
        f"value='{esc(other_ref)}' placeholder='Precon name, deck link or deck ID' autocomplete='off'></div>"
        f"<button type='submit' class='{compare_kind}'>{icon('swap')} Compare</button></div>"
        f"<details class='fold'{' open' if paste else ''}><summary>{icon('chev')} …or paste a decklist"
        "</summary><textarea name='paste' rows='5' aria-label='Paste a decklist' "
        "placeholder='1 Sol Ring&#10;1 Arcane Signet'>"
        f"{esc(paste)}</textarea></details></form>"
    )
    if error:
        return form + ui.notice(error, "error", html=True, role="alert")
    if result is None or other is None:
        return form
    cards: dict[str, DeckCard] = {}
    if isinstance(other, Deck):
        cards.update({front_name(c.name).casefold(): c for c in other.cards})
    cards.update({front_name(c.name).casefold(): c for c in deck.cards})
    sm = result["summary"]
    # a deck's name in curly quotes; "the pasted list" is a phrase, not a name
    oq = f"“{other_name}”" if isinstance(other, Deck) else other_name
    oname = esc(oq)
    widgets = ui.widgets(
        [
            ui.widget(f"In {oq}", "decks", str(sm["before_size"]), unit="cards"),
            ui.widget(f"In “{deck_title(deck)}”", "decks", str(sm["after_size"]), unit="cards"),
            ui.widget("Taken out", "minus", str(sm["cut"]), unit="cards", detail=f"{sm['cut_pct']}% of {oq}"),
            ui.widget(
                "Put in",
                "plus",
                str(sm["added"]),
                unit="cards",
                detail=f"{sm['added_pct']}% of “{deck_title(deck)}”",
            ),
            ui.widget(
                "Kept", "check", str(sm["kept"]), unit="cards", detail="Basic lands aside", wide_sm=True
            ),
        ],
        label="The comparison",
    )
    # Basic lands are listed once, under "Basic lands", so the two card lists and their counts
    # agree with the widgets above them.
    removed_rows = [r for r in result["removed"] if not is_basic_land(r["name"])]
    added_rows = [r for r in result["added"] if not is_basic_land(r["name"])]
    removed = "".join(_compare_row(r["name"], f"{r['quantity']}x", cards) for r in removed_rows)
    added = "".join(_compare_row(r["name"], f"{r['quantity']}x", cards) for r in added_rows)
    changed = "".join(
        _compare_row(r["name"], f"{r['before']}→{r['after']}", cards) for r in result["changed"]
    )
    basics = "".join(
        _compare_row(b["name"], f"{b['before']}→{b['after']}", cards) for b in sm["basic_land_changes"]
    )
    empty = "<div class='tr'><span class='muted'>None</span></div>"
    lists = (
        "<div class='cmp-cols'>"
        + _compare_table(f"Taken out of {oname}", len(removed_rows), removed)
        + _compare_table(f"Put into “{name}”", len(added_rows), added)
        + "<section class='cmp-col'><h2 class='cmp-h'>Changed counts "
        f"<span class='n'>{len(result['changed'])}</span></h2>"
        f"<div class='ui-table cmp-t' role='list'>{changed or empty}</div>"
        f"<h3 class='cmp-sub'>Basic lands</h3><div class='ui-table cmp-t' role='list'>{basics or empty}</div>"
        "</section>"
        "</div>"
    )
    delta_html = ""
    delta = result.get("stats_delta")
    if delta:
        rows = [
            f"<div class='tr'><span class='t'>{esc(STAT_LABELS.get(k, k))}</span>"
            f"<span class='v'>{'+' if v > 0 else ''}{_delta_text(k, v)}</span></div>"
            for k, v in delta.items()
            if isinstance(v, (int, float)) and v != 0
        ]
        if rows:
            half = (len(rows) + 1) // 2
            cols = "".join(
                f"<div class='ui-table cmp-st'>{''.join(part)}</div>"
                for part in (rows[:half], rows[half:])
                if part
            )
        else:
            cols = (
                "<div class='ui-table cmp-st'><div class='tr'>"
                "<span class='muted'>No difference</span></div></div>"
            )
        delta_html = (
            f"<section class='cmp-stats'><h2 class='cmp-h'>Statistics: this deck minus {oname}</h2>"
            f"<div class='cmp-st2'>{cols}</div></section>"
        )
    return form + widgets + lists + delta_html + forge_html


def _has_commander(deck: Deck) -> bool:
    return any(deck.in_deck(c) and deck.is_commander(c) for c in deck.cards)


def compare_forge_html(deck: Deck, other: Any, other_name: str, *, csrf: str | None, forge_on: bool) -> str:
    """Forge games of both decks against the same three precons, picked for the higher
    of the two decks' brackets, from the Compare page (POST /decks/<id>/compare/forge).
    Only when Forge is set up and both sides are Archidekt Commander decks: Forge plays nothing
    else, and a pasted list has no report to keep its games. Its button is the page's orange one
    once results show."""
    if not (forge_on and csrf and isinstance(other, Deck) and _has_commander(deck) and _has_commander(other)):
        return ""
    return (
        "<section class='ui-card cmp-forge'><h2>Forge games against the same opponents</h2>"
        f"<p class='muted small'>{COMPARE_FORGE_LINE}</p>"
        f"<form method='post' action='/decks/{esc(deck.id)}/compare/forge'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='with' value='{esc(other.id)}'>"
        "<button type='submit' class='btn-primary' data-busy-label='Starting…' "
        "title='Makes both reports and starts or queues their Forge games, then opens the second report'>"
        f"{icon('stats')} Test both "
        f"in Forge against the same opponents</button></form>"
        "</section>"
    )


COMPARE_CSS = """
.cmp-back{display:inline-flex;align-items:center;min-height:44px}
.cmp-form{padding:.75rem 1rem;margin:0 0 1rem}
.cmp-form .row{display:flex;gap:.5rem;align-items:flex-end}
.cmp-form .row .fld{flex:1;min-width:0;margin:0}
.cmp-form .fld label{display:block;font-weight:700;margin:0 0 .25rem}
.cmp-form .fld input{width:100%}
.cmp-form .row button{margin:0;flex:none}
.cmp-form .fold{margin-top:.5rem}
.cmp-form .fold summary{display:inline-flex;align-items:center;gap:.3rem;min-height:44px;cursor:pointer;
  color:var(--link);font-weight:700}
.cmp-form .fold summary svg.i{width:16px;height:16px}
.cmp-form textarea{width:100%;min-height:6rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:.9rem}
.cmp-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr));gap:1rem;
  align-items:start;margin:0 0 1rem}
.cmp-h{display:flex;align-items:baseline;gap:.35rem;font-size:1.1rem;margin:0 0 .5rem;overflow-wrap:anywhere}
.cmp-h .n{color:var(--text-muted);font-weight:400}
.cmp-sub{font-size:1rem;margin:1rem 0 .5rem}
.cmp-t{--cols:4rem minmax(0,1fr) 44px;--cols-md:4rem minmax(0,1fr) 44px;--cols-sm:3.6rem minmax(0,1fr) 44px}
.cmp-t .tr{min-height:44px;align-items:center}
.cmp-t .q{font-weight:900;color:var(--text-muted);white-space:nowrap;font-variant-numeric:tabular-nums}
.cmp-t .cardlink{display:flex;flex-direction:column;align-items:flex-start;min-height:44px;
  justify-content:center;
  width:100%;color:var(--text);text-align:left;cursor:pointer;user-select:none;-webkit-touch-callout:none}
.cmp-t .cardlink .nm{color:var(--link);font-weight:700;overflow-wrap:anywhere}
.cmp-t .cardlink .s{display:block;font-size:.86rem;color:var(--text-muted)}
.cmp-t .cardlink:hover .nm{text-decoration:underline}
.cmp-stats{margin:0 0 1rem}
.cmp-st2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr));gap:1rem;align-items:start}
.cmp-st{--cols:minmax(0,1fr) 8rem;--cols-md:minmax(0,1fr) 8rem;--cols-sm:minmax(0,1fr) 7rem}
.cmp-st .v{text-align:right;font-weight:900;font-variant-numeric:tabular-nums}
.cmp-forge button.btn-primary{white-space:normal;text-align:left;line-height:1.2;margin:0}
.cmp-forge > p{margin:0 0 .75rem}
@media (max-width:599.98px){ .cmp-form .row{flex-direction:column;align-items:stretch}
  .cmp-form .row button{width:100%} }
"""


# -- goldfish page --------------------------------------------------------------------------------


def goldfish_data(deck: Deck) -> dict[str, Any]:
    """The deck as static/goldfish.js plays it: the commanders (they start in the command zone)
    and the rest of the main deck, one entry per row with its count. The sideboard and
    maybeboard are left out. Each card carries what the shared card viewer shows."""

    def entry(card: DeckCard) -> dict[str, Any]:
        out = {
            "name": card.name,
            "qty": card.quantity,
            "img": card_image(card) or "",
            "set": f"{card.set_code.upper()} {card.collector_number}".strip(),
            "type": card.type_line,
            "mana": card.mana_cost,
            "text": card.oracle_text,
            "pt": f"{card.power}/{card.toughness}" if card.power or card.toughness else "",
            "loyalty": card.loyalty,
            "faces": [
                {
                    "name": f["name"],
                    "mana": f["mana_cost"],
                    "type": f["type_line"],
                    "text": f["text"],
                    "pt": f"{f['power']}/{f['toughness']}" if f["power"] or f["toughness"] else "",
                    "loyalty": f["loyalty"],
                }
                for f in card.faces
            ],
            "land": "Land" in card.types,
        }
        return {k: v for k, v in out.items() if v not in ("", [], False)}

    main = [c for c in deck.cards if deck.in_deck(c) and c.quantity > 0]
    commanders = [c for c in main if deck.is_commander(c)]
    return {
        "commanders": [{**entry(c), "qty": 1} for c in commanders],
        # A commander row with more than one copy (it should not have) keeps the rest in the library.
        "library": [
            entry(c) if not deck.is_commander(c) else {**entry(c), "qty": c.quantity - 1}
            for c in main
            if not deck.is_commander(c) or c.quantity > 1
        ],
    }


# The Goldfish page's own guide: its steps, in order. goldfish.js marks the step the game is at;
# the member can hide the guide, and the choice follows the account (account_prefs
# GOLDFISH_GUIDE_PREF).
GOLDFISH_GUIDE_PREF = "goldfish_guide"
GOLDFISH_GUIDE_STEPS = (
    ("hand", "Look at your seven cards. Press Keep hand, or Mulligan for seven new ones."),
    (
        "bottom",
        "After a mulligan, press Keep hand. If the game asks for cards to go on the bottom, select them, "
        "then press the Put … on the bottom button.",
    ),
    ("land", "Play a land: select it in your hand and choose Play to the battlefield."),
    ("cast", "Tap lands for mana: select a land and choose Tap. Then play what you could pay for."),
    ("next", "Press Next turn: everything untaps and you draw a card."),
    ("again", "Keep going until the deck does what it was built to do. New game starts over."),
)
GOLDFISH_GUIDE_HIDE = "Hide the guide"
GOLDFISH_GUIDE_SHOW = "How to play"
# Phones show the step the game is at; this opens all six
GOLDFISH_GUIDE_ALL = "Show all steps"
GOLDFISH_GUIDE_CURRENT = "Show the current step only"
GOLDFISH_NOTE = "Nothing is saved or sent to Archidekt"
GOLDFISH_NO_SOLO = "No solo test yet."


def goldfish_guide_html(*, hidden: bool = False) -> str:
    """The guide panel: a numbered list, no box around each step; the step the game is at has an
    orange number and bold text. Hidden (the member hid it) it stays in the page, so "How to play"
    brings it back without a reload."""
    steps = "".join(
        f"<li data-step='{key}'><span class='gf-step-n' aria-hidden='true'>{i}</span>"
        f"<span>{esc(text)}</span></li>"
        for i, (key, text) in enumerate(GOLDFISH_GUIDE_STEPS, start=1)
    )
    return (
        "<section class='panel gf-guide' id='gf-guide' aria-labelledby='gf-guide-h'"
        f"{' hidden' if hidden else ''}>"
        "<div class='gf-guide-head'><h2 id='gf-guide-h'>How to play</h2>"
        "<span class='gf-guide-btns'>"
        "<button type='button' class='btn small gf-allsteps' aria-expanded='false' aria-controls='gf-steps' "
        f"data-all='{esc(GOLDFISH_GUIDE_ALL)}' data-current='{esc(GOLDFISH_GUIDE_CURRENT)}'>"
        f"{esc(GOLDFISH_GUIDE_ALL)}</button>"
        f"<button type='button' class='btn small' data-guide='hide'>{esc(GOLDFISH_GUIDE_HIDE)}</button>"
        "</span></div>"
        f"<ol class='gf-steps' id='gf-steps'>{steps}</ol>"
        "</section>"
    )


def goldfish_intro(size: int, *, commander: bool) -> str:
    """The one intro line: the rules as a few words, and that nothing leaves the browser."""
    rule = (
        "Commander: first mulligan free"
        if commander
        else "No commander: every mulligan puts one more card on the bottom"
    )
    return f"{plural(size, 'card')} · {rule} · on the play · {GOLDFISH_NOTE}"


def forge_solo_line(deck: Deck, solo: dict[str, Any] | None, *, csrf: str | None) -> str:
    """The line under the Goldfish intro about the deck's newest Forge solo test
    (ReportService.newest_forge_solo: report_id, finished_at, won_rate, games, median_won_turn):
    the win rate and median win turn with Open the report, or, with no report yet, a plain Run
    full test. The games count is the report's own."""
    did = esc(deck.id)
    if not solo:
        run = (
            f"<form method='post' action='/decks/{did}/report' class='inline'>"
            f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
            f"<button type='submit' data-busy-label='Testing…'>{icon('stats')} Run full test</button></form>"
            if csrf
            else ""
        )
        return f"<div class='notice info gf-solo'><span>{GOLDFISH_NO_SOLO}</span>{run}</div>"
    games = int(solo.get("games") or 0)
    rate = solo.get("won_rate")
    pct = (
        f"{round(float(rate) * 100) if float(rate) <= 1 else round(float(rate))}%"
        if rate is not None
        else "–"
    )
    turn = solo.get("median_won_turn")
    turn_text = f", median win turn {esc(turn)}" if turn is not None else ""
    finished = ui.when(solo.get("finished_at")) if solo.get("finished_at") else ""
    rid = esc(solo.get("report_id") or "")
    return (
        "<div class='notice info gf-solo' role='status'><span>Forge's solo test of this deck: won "
        f"{pct} of {plural(games, 'game')}{turn_text}.</span>"
        + (f"<span>{finished}</span>" if finished else "")
        + (f"<a href='/history/reports/{rid}'>Open the report</a>" if rid else "")
        + "</div>"
    )


_NO_SOLO_LINE = object()


def goldfish_page_html(
    deck: Deck,
    *,
    guide_hidden: bool = False,
    forge_solo: Any = _NO_SOLO_LINE,
    csrf: str | None = None,
    extra_head: str = "",
) -> str:
    """Goldfish by hand: shuffle, an opening hand with London mulligans, then draw, play, tap and
    pass turns, all in the browser (static/goldfish.js) on the deck already read for the page.
    Nothing is sent anywhere and nothing is kept, except whether the member hid the guide.
    ``forge_solo``: the newest Forge solo test's figures, None for none yet; left out when the
    gateway has no such results (no line then). ``extra_head``: markup under the solo line (the
    My card models opener, card_models_view)."""
    did = esc(deck.id)
    name = esc(deck_title(deck))
    data = goldfish_data(deck)
    commander = bool(data["commanders"])
    size = sum(c["qty"] for c in data["library"]) + len(data["commanders"])
    zone = (
        "<section class='gf-zone' id='gf-{id}' aria-labelledby='gf-{id}-h'>"
        "<h2 id='gf-{id}-h'>{label} <span class='gf-n'></span></h2><div class='gf-row'></div></section>"
    )
    js_data = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    solo = "" if forge_solo is _NO_SOLO_LINE else forge_solo_line(deck, forge_solo, csrf=csrf)
    return (
        "<section class='panel gf-head'>"
        f"<a class='gf-back' href='/decks/{did}'>← {name}</a>"
        "<h1>Goldfish</h1>"
        f"<p class='muted small gf-intro'>{esc(goldfish_intro(size, commander=commander))}</p>"
        + solo
        + extra_head
        + "<p class='gf-status' id='gf-status'></p>"
        "<p class='gf-hint' id='gf-hint'></p>"
        "<div class='gf-controls' role='group' aria-label='Game'>"
        "<button type='button' class='btn-primary' data-act='keep' hidden>Keep hand</button>"
        "<button type='button' data-act='mulligan' hidden>Mulligan</button>"
        "<button type='button' class='btn-primary' data-act='bottom' hidden>Put on bottom</button>"
        "<button type='button' class='btn-primary' data-act='draw' hidden>Draw a card</button>"
        "<button type='button' data-act='next' hidden>Next turn</button>"
        "<button type='button' data-act='untap' hidden>Untap all</button>"
        f"<button type='button' data-act='reset'>{icon('refresh')} New game</button>"
        f"<button type='button' class='gf-guide-show' data-guide='show'{'' if guide_hidden else ' hidden'}>"
        f"{icon('guide')} {esc(GOLDFISH_GUIDE_SHOW)}</button>"
        # the card size every page with card pictures follows (theme.card_size_control, prefs.js),
        # at the end of the game buttons' row
        f"<div class='gf-size'>{card_size_control()}</div>"
        "</div>"
        "<p class='sr-only' id='gf-live' aria-live='polite'></p>"
        "<noscript><p class='notice error'>The goldfish needs JavaScript.</p></noscript>"
        "</section>"
        + goldfish_guide_html(hidden=guide_hidden)
        + (zone.format(id="command", label="Command zone") if commander else "")
        + zone.format(id="battlefield", label="Battlefield")
        + zone.format(id="hand", label="Hand")
        + "<div class='gf-piles'>"
        + zone.format(id="graveyard", label="Graveyard")
        + zone.format(id="exile", label="Exile")
        + "</div>"
        + f"<script type='application/json' id='goldfish-data'>{js_data}</script>"
    )


GOLDFISH_CSS = """
.gf-head h1{margin:.1rem 0 .3rem;font-size:1.6rem}
.gf-back{display:inline-flex;align-items:center;min-height:44px}
.gf-intro{margin:0}
.gf-solo{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem .75rem;margin:.6rem 0 0}
.gf-solo form.inline{display:contents} .gf-solo button{margin:0}
.gf-status{font-weight:700;margin:.6rem 0 .25rem}
.gf-hint{margin:0 0 .5rem} .gf-hint:empty{display:none}
.gf-controls{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}
.gf-controls [hidden]{display:none}
.gf-controls button{margin:0}
.gf-size{margin-left:auto}
.gf-size .cardsize{display:flex;flex-direction:row;flex-wrap:wrap;align-items:center;gap:.25rem .5rem;
  margin:0}
.gf-size .cardsize .lbl{color:var(--text-muted)}
/* at 200% text the three buttons' 2.75rem minimum is wider than a folded Fold leaves the switch:
   let them shrink, down to 44px, instead of the switch cutting off L */
.gf-size .sizeseg{max-width:100%}
.gf-size .sizeseg button{flex:0 1 auto;min-width:min(2.75rem,44px)}
@media (pointer:coarse){ .gf-size .sizeseg button{min-width:44px} }
.gf-zone{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-panel);
  padding:.75rem;margin:0 0 1rem;min-width:0}
.gf-zone h2{font-size:1.1rem;margin:0 0 .5rem}
.gf-zone .gf-n{color:var(--text-muted);font-weight:400}
/* the cards wrap at every size: a grid of columns the card's width (--gw), never a sideways scroll */
.gf-row{--gw:130px;display:grid;grid-template-columns:repeat(auto-fill,minmax(var(--gw),1fr));gap:.5rem;
  align-items:start;min-height:2rem}
html[data-card-size=s] .gf-row{--gw:92px} html[data-card-size=l] .gf-row{--gw:180px}
.gf-row + .gf-row{margin-top:.75rem;padding-top:.75rem;border-top:1px dashed var(--border)}
.gf-empty{color:var(--text-muted);margin:0;grid-column:1 / -1}
.gf-piles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,18rem),1fr));gap:0 1rem}
/* a card's cell: the card and its ⋮ (hidden at the small size, where the cell is a thumbnail) */
.gf-c{display:flex;flex-direction:column;align-items:center;gap:2px;min-width:0}
.gf-c .kebab{flex:none}
html[data-card-size=s] .gf-c .kebab{display:none}
.gf-card{position:relative;width:100%;max-width:220px;aspect-ratio:63/88;height:auto;
  min-height:0;padding:0;border:1px solid var(--card-border);border-radius:4.5%/3.2%;
  background:var(--surface-2);color:var(--text);overflow:hidden;display:block;cursor:pointer;
  user-select:none;-webkit-touch-callout:none;transition:transform .15s}
.gf-card img{width:100%;height:100%;object-fit:cover;display:block}
.gf-card .ph{display:flex;flex-direction:column;justify-content:space-between;height:100%;padding:.35rem;
  font-size:.78rem;text-align:left;line-height:1.2}
.gf-card .ph .nm{overflow-wrap:break-word;hyphens:auto}
.gf-card .ph .ty{color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gf-card:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
/* a tapped permanent turns sideways inside its cell */
.gf-card.tapped{transform:rotate(90deg) scale(.72)}
.gf-card .tag{position:absolute;left:0;right:0;bottom:0;padding:.15rem .25rem;font-size:.75rem;
  font-weight:700;background:var(--orange);color:var(--on-orange);text-align:center}
.gf-card[aria-pressed=true]{outline:3px solid var(--orange-ctl);outline-offset:2px}
.gf-card[aria-pressed=true]:focus-visible{outline-color:var(--focus)}
@media (prefers-reduced-motion:reduce){.gf-card{transition:none}}
/* the guide: a numbered list without boxes; the step the game is at has an orange number and bold text */
.gf-guide[hidden],.gf-guide-show[hidden]{display:none}
.gf-guide-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.5rem}
.gf-guide h2{font-size:1.1rem;margin:0}
.gf-steps{list-style:none;margin:.5rem 0 0;padding:0;display:grid;gap:.1rem 1.5rem;
  grid-template-columns:repeat(auto-fill,minmax(min(100%,22rem),1fr))}
.gf-steps li{display:flex;gap:.6rem;align-items:flex-start;padding:.35rem 0;line-height:1.4}
.gf-steps .gf-step-n{flex:none;display:inline-flex;align-items:center;justify-content:center;width:1.6rem;
  height:1.6rem;border-radius:50%;background:var(--surface-2);color:var(--text);font-weight:900;font-size:.85rem}
.gf-steps li[aria-current=step]{font-weight:700}
.gf-steps li[aria-current=step] .gf-step-n{background:var(--orange);color:var(--on-orange)}
.gf-steps li.done{color:var(--text-muted)}
/* phones: only the step the game is at, so the hand stays near the top; seven cards a row at S,
   four at M, three at L */
.gf-guide-btns{display:flex;flex-wrap:wrap;gap:.4rem}
.gf-allsteps{display:none}
@media (max-width:600px){
  .gf-allsteps{display:inline-flex}
  .gf-steps:not(.all):has(li[aria-current=step]) li:not([aria-current=step]){display:none}
  .gf-row{gap:4px;grid-template-columns:repeat(4,minmax(0,1fr))}
  html[data-card-size=s] .gf-row{grid-template-columns:repeat(7,minmax(0,1fr))}
  html[data-card-size=l] .gf-row{grid-template-columns:repeat(3,minmax(0,1fr))}
  .gf-card .ph{padding:3px;font-size:.6rem}
  .gf-size{margin-left:0;flex-basis:100%}
}
"""

# The deck page's styles for the bracket display and the simulator: DECK_CSS ends with this, so
# these rules need no edit inside DECK_CSS.
DECK_CSS_C = ""


# -- deck list ------------------------------------------------------------------------------------

LIST_ORDERS = {"updated": "Updated at", "created": "Created at", "name": "Name", "format": "Deck format"}


def colour_bar_html(colors: dict[str, Any] | None) -> str:
    parts = {k: float(v) for k, v in (colors or {}).items() if k in WUBRG and v}
    return _bar(parts, label="Color identity") if parts else "<div class='cbar empty'></div>"


NO_TAGS = "<span class='notags'>No deck tags</span>"


def deck_card_html(
    d: dict[str, Any],
    *,
    cover: dict[str, Any] | None,
    selected: bool,
    show_owner: bool = False,
    own: bool = False,
    plain: bool = False,
) -> str:
    """One deck tile: a link to the deck, and after it the tile's ⋮ (static/menus.js opens the
    deck's menu from it, a right-click, a press and hold or Shift+F10). ``own``: the member's own
    deck, whose menu has Edit deck, Rename, Move to folder, Delete deck and the rest. ``plain``:
    a precon tile, without the owner's picture and the deck tags (as deck search's tiles)."""
    did = esc(d["id"])
    name = d.get("name") or "Unnamed deck"
    folder_id = d.get("folder_id")
    data = f" data-deck-id='{did}' data-deck-name='{esc(name)}' data-own='{1 if own else 0}'" + (
        f" data-folder='{esc(str(folder_id))}'" if folder_id is not None else ""
    )
    kebab = (
        f"<button type='button' class='kebab' aria-label='Menu for {esc(name)}' aria-haspopup='menu' "
        f"title='Menu'>{icon('more')}</button>"
    )
    art = image_url((cover or {}).get("scryfall_uid"), "art_crop")
    style = f" style=\"background-image:url('{esc(art)}')\"" if art else ""
    fmt = format_label(d.get("format_name")) if d.get("format_name") else ""
    if not fmt and isinstance(d.get("format"), str):
        fmt = format_label(d["format"])
    line = (fmt or "Custom") + deck_card_bracket_text(d.get("bracket"))  # bracket_view.py
    tags = d.get("tags") or []
    pills = "".join(f"<span class='pill'>{esc(t)}</span>" for t in tags[:6]) or NO_TAGS
    tags_html = "" if plain else f"<span class='tags'>{pills}</span>"
    initial = esc((d.get("name") or "?")[:1].upper())
    size = d.get("size")
    sub_bits = []
    if show_owner and d.get("owner"):
        sub_bits.append(f"by {d['owner']}")
    elif d.get("folder"):
        sub_bits.append(str(d["folder"]))
    if d.get("views") and show_owner:
        sub_bits.append(f"{d['views']:,} views")
    sub_text = " · ".join(sub_bits)
    when = ui.when(iso_ts(d.get("updated_at") or ""))  # the one list date (feedback.js keeps it current)
    sub_html = esc(sub_text) + (f"{' · ' if sub_text else ''}{when}" if when else "")
    return (
        f"<li class='deck{' selected' if selected else ''}'{data}><a href='/decks/{did}'>"
        f"<span class='thumb{' noart' if not art else ''}'{style}><span class='ini'>{initial}</span>"
        + (f"<span class='views'>{plural(size, 'card')}</span>" if size else "")
        + colour_bar_html(d.get("colors"))
        + "</span>"
        f"<span class='info{' plain' if plain else ''}'>"
        + ("" if plain else avatar_html(d.get("owner") or "", "sm"))
        + f"<span class='text'><span class='name' title='{esc(name)}'>{esc(name)}</span>"
        f"<span class='fmt' title='{esc(line)}'>{esc(line)}</span>"
        f"<span class='sub' title='{esc(sub_text)}'>{sub_html}</span>"
        "</span></span>"
        f"{tags_html}</a>{kebab}</li>"
    )


def covers_for(
    decks: list[dict[str, Any]], stored: dict[str, dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    """Cover art per deck id for a listing: the cover chosen on Archidekt (its ``featured`` art, a
    Scryfall id) wins over the card the gateway remembered from the deck page."""
    out = dict(stored or {})
    for d in decks:
        uid = d.get("featured_scryfall_id")
        if uid:
            out[str(d["id"])] = {"scryfall_uid": uid, "card_name": ""}
    return out


def deck_list_html(
    decks: list[dict[str, Any]],
    *,
    covers: dict[str, dict[str, Any]] | None = None,
    selected: str | None = None,
    q: str = "",
    view: str = "grid",
    show_owner: bool = False,
    folder: str = "",
    own: bool = False,
    plain: bool = False,
) -> str:
    """The deck tiles. ``own``: the member's own decks (My decks, Home), whose tiles' menus carry
    the owner's actions; a listing of other members' decks leaves it off. ``plain``: precon tiles
    (no owner picture, no tags)."""
    if not decks:
        if folder:
            return (
                f"<div class='panel'><p>No decks in the folder “{esc(folder)}”"
                + (f" matching “{esc(q)}”" if q else "")
                + ". <a href='/decks'>Show every folder</a>.</p></div>"
            )
        return (
            "<div class='panel'><p>No decks yet"
            + (f" matching “{esc(q)}”" if q else "")
            + ". Create one with New deck, or ask your assistant to propose a new deck.</p></div>"
        )
    items = "".join(
        deck_card_html(
            d,
            cover=(covers or {}).get(str(d["id"])),
            selected=bool(selected and str(d["id"]) == str(selected)),
            show_owner=show_owner,
            own=own,
            plain=plain,
        )
        for d in decks
    )
    return f"<ul class='plain decklist {esc(view)}'>{items}</ul>"


# Archidekt's deck list came back with entries whose owner couldn't be read, so they were
# left out. Shown like the gateway's other "couldn't load" answers (busy.busy_body): the reason in
# a panel and a Try again button that loads the page again with reread=1, which reads the list
# again (a list like this is otherwise kept for a minute: decks.DECK_LIST_PARTIAL).
DECKS_UNREADABLE = "Your deck list couldn't be read from Archidekt. Try again."
DECKS_PARTLY_UNREADABLE = (
    "Part of your deck list couldn't be read from Archidekt, so some decks may be missing here. Try again."
)


def decks_unreadable_text(*, some_shown: bool) -> str:
    """The words of the notice that the deck list couldn't be read: none of the list read, or only
    part of it."""
    return DECKS_PARTLY_UNREADABLE if some_shown else DECKS_UNREADABLE


REREAD_KEEPS = ("q", "order", "view", "folder")  # the deck list's own query, kept by Try again


def reread_href(path: str, query: Any = ()) -> str:
    """Where Try again on the notice that the deck list couldn't be read goes: ``path`` with the
    list's own query (REREAD_KEEPS, from ``query``) and ``reread=1``. The page reads the list again
    and sends the browser back to the address without ``reread``."""
    kept = [(k, v) for k, v in dict(query).items() if k in REREAD_KEEPS and v]
    return f"{path}?{urlencode([*kept, ('reread', '1')])}"


def decks_unreadable_html(*, some_shown: bool, panel: bool = True, again: str = "") -> str:
    """The notice over (or instead of) the deck list when entries couldn't be read;
    ``panel=False`` for a notice inside a panel already (the home page's My decks). ``again``:
    the Try again link (``reread_href``); empty loads the same address again."""
    text = decks_unreadable_text(some_shown=some_shown)
    return (
        f"<div class='{'panel ' if panel else ''}decks-unreadable' role='alert'><p>{esc(text)}</p>"
        f"<div class='actions'><a class='btn btn-primary' href='{esc(again)}'>Try again</a></div></div>"
    )


def deck_list_held_text(read_at: float, *, now: float | None = None) -> str:
    """What My decks and Home say over a deck list shown as last read (DeckService.last_deck_list)."""
    seconds = max(0, int((time.time() if now is None else now) - read_at))
    # spelled out in a sentence (waiting_strip._age's "min" and "h" are for list columns)
    n, unit = (max(1, seconds // 60), "minute") if seconds < 3600 else (seconds // 3600, "hour")
    age = f"{n} {unit}{'' if n == 1 else 's'}"
    return f"Your deck list can't be read from Archidekt right now; this is how it was {age} ago."


def deck_list_held_html(read_at: float) -> str:
    """The one notice line over a deck list shown as last read."""
    return f"<p class='notice warn' role='status'>{esc(deck_list_held_text(read_at))}</p>"


def deck_count_text(total: int | None, *, pending: bool = False) -> str:
    """The count in the deck list's results bar ("3 decks"); blank while the list is being read."""
    if pending or total is None:
        return "Decks"
    return f"{total} deck{'' if total == 1 else 's'}"


def deck_list_controls_html(
    *,
    q: str,
    order: str,
    view: str,
    folder: str,
    total: int | None,
    pending: bool = False,
    direction: str = "auto",
    colors: str = "",
    colour_mode: str = "incl",
    fmt: str = "",
) -> str:
    """The filter panel over the deck list (searchpanel.py, the panel the search pages share):
    the name box, Colors and Format (static/decks.js filters the list as they change), and the
    results bar with the count, Sort by and its direction, Grid or List, the card size and Clear
    filters. ``folder``: the open folder's id ("" at the top level), kept so the sort and the view
    stay in that folder (a name, colour or format filter searches every folder). ``pending``: the
    list is still being read (no count yet; decks.js fills it). ``total`` None: the list couldn't
    be read, so no count."""
    here = f"/decks?{urlencode({'folder': folder})}" if folder else "/decks"
    formats = sorted({(n, format_label(n)) for n in FORMAT_NAMES.values()}, key=lambda f: f[1].lower())
    groups = sp.filter_grid(
        [
            sp.colours_group(
                label="Colors",
                name="c",
                chosen=colors,
                mode_name="cm",
                mode_options=sp.DECK_COLOUR_MODES,
                mode_value=colour_mode,
            ),
            sp.select_group(
                label="Format",
                name="format",
                options=[("", "Any format"), *formats],
                # a link from before the drop-down sent slugs may carry the format's name
                value=next((n for n, label in formats if fmt.casefold() in (n, label.casefold())), fmt),
                input_id="f-format",
            ),
        ]
    )
    bar = sp.results_bar(
        count_html=sp.count_html(deck_count_text(total, pending=pending), count_id="decks-total"),
        sort_html=sp.sort_group(
            order_name="order",
            orders=LIST_ORDERS.items(),
            order=order,
            order_id="f-order",
            dir_name="dir",
            direction=direction,
        ),
        view_html=sp.view_group(view),
        clear_html=f"<a class='btn sp-clear clr' href='{esc(here)}'>Clear filters</a>",
    )
    return (
        "<section class='panel spanel'><form method='get' action='/decks' id='listform' "
        "data-live>"
        + (f"<input type='hidden' name='folder' value='{esc(folder)}'>" if folder else "")
        + sp.name_row(
            label="Filter deck name",
            name="q",
            value=q,
            placeholder="Example deck name",
            input_id="q",
            search_label="Filter",
            attrs=" maxlength='80'",
        )
        + sp.filters_sheet(
            groups,
            show_html="<button type='submit' class='btn-primary sp-show'>Show decks</button>",
            clear_html=f"<a class='btn sp-clear' href='{esc(here)}'>Clear filters</a>",
        )
        + bar
        + "</form></section>"
    )


DECK_CSS = (
    """
/* deck banner (cardBanner / deckHeaderInfo) */
.banner{position:relative;margin:0 -1rem 1rem;color:#fff;isolation:isolate;z-index:2}
/* the toolbar below is positioned too; while a banner menu is open the banner must win */
.banner:has(details[open]){z-index:6}
/* no featured art: no empty grey band on phones */
@media (max-width:600px){ .banner.noart .strip{height:0} }
/* the blurred art sits on a pseudo-element clipped to the banner, so the banner itself can stay
   overflow:visible and its More menu is never cut off */
.banner::before{content:'';position:absolute;inset:0;z-index:-1;background:var(--surface-2) var(--art,
  none) center 30%/cover no-repeat;
  border-radius:0;overflow:hidden}
.banner .shade{background:linear-gradient(112deg,var(--banner-a),var(--banner-a) 25%,var(--banner-b) 76%,
  var(--banner-a));backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);padding:1rem}
.banner .controls details.dd .menu{z-index:40}
.banner .content{display:flex;justify-content:space-between;gap:1rem;max-width:2300px;margin:0 auto}
.banner .info{min-width:0;flex:1}
.banner h1.deckname{display:flex;align-items:center;gap:.5rem;margin:0 0 .5rem;font-size:28px;
  font-weight:700;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.banner h1.deckname > span{overflow:hidden;text-overflow:ellipsis}
.banner .privacy{color:#ababab;display:inline-flex}
.banner .row{display:flex;flex-wrap:wrap;column-gap:1rem;row-gap:.2rem;font-size:16px;color:#ababab;
  margin:.15rem 0;align-items:center}
.banner .row b.orange{color:var(--orange);font-weight:700}
.banner .legal{display:inline-flex;align-items:center;gap:.3rem}
.banner .legal.ok svg{color:#1ebb6c} .banner .legal.bad svg{color:#f21b3f}
.banner .tags{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin-top:1rem;color:#ababab}
.banner .tags .pill{background:var(--toolbar-bg);color:var(--toolbar-text)}
.banner .notags{opacity:.8}
.banner .controls{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem;
  margin-top:1rem}
.banner .controls .primary{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}
.banner .controls .btn,.banner .controls button{margin:0}
.banner .controls form.inline{display:contents}
.cardlink{background:none;border:0;padding:0;margin:0;color:var(--link);cursor:pointer;font:inherit;text-align:left}
.cardlink:hover{text-decoration:underline}
h3 .count{font-weight:400;color:var(--text-muted);font-size:.9rem}
.deltatable{border-collapse:collapse}
.deltatable th{text-align:left;font-weight:500;padding:.3rem 1rem .3rem 0}
.deltatable td{text-align:right;font-variant-numeric:tabular-nums}
.banner .social{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.9rem}
.banner .social .soc{display:inline-flex;align-items:center;gap:.4rem;height:34px;padding:0 .85rem;
  border-radius:17px;border:1px solid rgba(255,255,255,.4);background:rgba(0,0,0,.28);color:#fff;
  font-weight:700;font-size:.9rem;text-decoration:none;cursor:pointer;margin:0;line-height:1}
.banner .social .soc:hover,.banner .social .soc:focus-visible{border-color:var(--orange);color:var(--orange)}
/* dark text on orange (white on Archidekt's orange is 2.4:1) */
.banner .social .soc.on{background:var(--orange);border-color:var(--orange);color:var(--on-orange)}
.banner .social .soc.on:hover{color:var(--on-orange);filter:brightness(1.08)}
.banner .social .soc svg{width:18px;height:18px}
.banner .social .soc[disabled]{opacity:.6;cursor:progress}
.banner .social .soc[hidden]{display:none}
.banner .social .confirm{display:inline-flex;align-items:center;flex-wrap:wrap;gap:.4rem;background:#fff;
  color:#111;border-radius:17px;padding:.25rem .35rem .25rem .85rem;min-height:34px;max-width:100%;
  font-size:.9rem;font-weight:700;box-sizing:border-box}
.banner .social .confirm button{margin:0;height:26px;padding:0 .7rem;border-radius:13px;font-size:.85rem}
.banner .social .note{flex-basis:100%;color:#ffd9b3;font-size:.9rem}
.banner .social .note a{color:#fff}
.comments .panel-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;
  gap:0 1rem}
.comments .panel-head [data-count]:empty{display:none}
.comments .panel-head h2{margin:0}
/* one-word headings can't wrap whole: with large text on a narrow phone they stop growing at 8% of the
   screen width (min(), so 100% text and wider screens are unchanged); a longer word still breaks */
.panel.description > h2,.comments .panel-head h2{min-width:0;overflow-wrap:anywhere;font-size:min(1.5rem,8vw)}
/* the View as / Group by / Sort by boxes on a phone: two a row as before (each column at least half
   the row, so never three), one a row when large text makes a box wider than half the row (scripts
   off, the longest choice used to push the page 39 px wide) */
@media (max-width:600px){
  .toolbar form.views{
    grid-template-columns:repeat(auto-fit,minmax(min(100%,max(7.5rem,calc(50% - .5rem))),1fr))} }
.toolbar form.views .field,.toolbar form.views .sel,.toolbar form.views select{min-width:0}
.comments .thread{margin:.75rem 0 1rem}
.comments .cmt{border-top:1px solid var(--border);padding:.6rem 0 .4rem}
.comments .cmt .who{display:flex;gap:.5rem;align-items:center;font-size:.85rem;color:var(--text-muted)}
.comments .cmt .who b{color:var(--text)}
.comments .cmt p{margin:.3rem 0;white-space:pre-wrap;overflow-wrap:anywhere}
.comments .cmt .replies{margin-left:1rem;border-left:2px solid var(--border);padding-left:.75rem}
.comments .cmt .acts .del{color:var(--danger-text)}
.comments .cmt form.editcomment{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:.3rem 0}
.comments .cmt form.editcomment textarea{flex:1 1 100%;min-height:4rem}
.comments .cmt .confirmbar{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:.3rem 0}
.comments .cmt .acts button{margin:0;height:28px;padding:0 .6rem;font-size:.8rem}
.comments .cmt .acts{display:flex;flex-wrap:wrap;gap:.25rem;align-items:center}
.comments .cmt .acts button.vote[aria-pressed=true]{color:var(--orange-text);font-weight:700}
.comments .cmt .acts .confirm{display:inline-flex;flex-wrap:wrap;gap:.4rem;align-items:center;min-width:0;
  font-size:.85rem}
.comments .cmt .acts .confirm > span{min-width:0;overflow-wrap:anywhere}
.comments .cmt .acts .notice{flex-basis:100%;margin:.25rem 0 0}
.comments form.newcomment{display:flex;flex-direction:column;gap:.5rem}
.comments form.newcomment textarea{width:100%;resize:vertical;min-height:4.5rem}
.comments form.newcomment button{align-self:flex-start;margin:0}
.comments form.newcomment .replyto{display:flex;gap:.5rem;align-items:center;font-size:.9rem}
.comments form.newcomment .replyto button{margin:0;height:26px;padding:0 .6rem;font-size:.8rem}
.comments .confirmbar{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;padding:.5rem .75rem;
  background:var(--surface-2);border-radius:var(--radius)}
.comments .confirmbar button{margin:0}
.comments .notice button{margin:0 0 0 .5rem;min-height:30px;padding:0 .7rem;font-size:.9rem}
.banner .owner{display:flex;flex-direction:column;align-items:center;gap:.35rem;color:#fff;
  text-decoration:none;font-weight:700;max-width:160px;flex:none;order:1}
.banner .owner:hover{color:var(--orange)}
.banner .strip{display:none}
.banner .owner .uname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:160px}
/* initials in the page's text colour on surface-3: 6.5:1 light, 6.8:1 dark (white was 1.8:1 in light) */
.avatar{display:inline-flex;align-items:center;justify-content:center;border-radius:50%;
  background:var(--surface-3);color:var(--text);font-weight:900;border:1px solid var(--border);flex:none}
.banner .avatar{background:#3a3a3a;color:#fff;border-color:rgba(255,255,255,.4)}
.avatar.lg{width:50px;height:50px;font-size:1.4rem} .avatar.sm{width:40px;height:40px;font-size:1.1rem}
.avatar.xl{width:80px;height:80px;font-size:2rem}
@media (max-width:600px){
  .banner .shade{padding:0}
  .banner .content{flex-direction:column;gap:0}
  .banner .strip{display:block;height:110px;background:var(--surface-2) var(--art,
    none) center 30%/cover no-repeat}
  .banner .info{padding:0 1rem 1rem}
  .banner h1.deckname{font-size:24px;margin-top:.5rem}
  .banner .row{font-size:13px}
  .banner .owner{flex-direction:row;justify-content:flex-end;max-width:none;padding:.5rem 1rem 0;order:-1}
  .banner .owner .avatar{width:80px;height:80px;font-size:2rem;margin-right:auto;margin-top:0;
    border-width:3px;border-color:var(--bg)}
  /* phones: the actions in two columns instead of five full-width rows; a column is at least 9rem,
     so at large text sizes they go one a row, and a long label wraps whole instead of being cut */
  .banner .controls .primary{display:grid;width:100%;gap:.5rem;
    grid-template-columns:repeat(auto-fill,minmax(min(100%,9rem),1fr))}
  .banner .controls .primary .btn,.banner .controls .primary button,.banner .controls details.dd{width:100%;
    min-width:0}
  .banner .controls .primary .btn,.banner .controls .primary button{padding-left:.5rem;padding-right:.5rem;
    white-space:normal;overflow-wrap:normal;text-align:center}
  .banner .controls .primary form.inline{display:contents}
  .banner .controls details.dd > summary.btn{width:100%}
}

/* toolbar panel (filterBar) */
.toolbar{padding:.5rem 1rem 1rem;position:relative;z-index:1}
.toolbar .controls{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,2fr);gap:1rem;align-items:end}
.toolbar .controls > form.views:only-child{grid-column:1 / -1}
.toolbar form.quick{margin:0} .toolbar .quickrow{display:flex;gap:.5rem}
.toolbar .quickrow input{min-width:0;flex:1}
.toolbar .quickrow button{margin:0;flex:none}
.toolbar .views{display:grid;grid-template-columns:repeat(3,minmax(0,1fr)) minmax(10rem,1.4fr);
  gap:.5rem 1rem;margin:0}
.toolbar .views .field{margin:0}
.toolbar .views .hint{grid-column:1 / -1;margin:0;color:var(--text-muted);font-size:.9rem;align-self:center}
/* with the card size switch (0.7.29) next to View as: it takes only its own width. Above 1200 px the
   boxes share one row while they fit and wrap onto the next row when large text makes them wider
   (one grid row let Group by and Sort by run over each other at 200% text) */
@media (min-width:1200.02px){
  .toolbar .views:has(.cardsize){display:flex;flex-wrap:wrap;align-items:flex-end}
  .toolbar .views:has(.cardsize) > .field{flex:1 1 8rem;min-width:max-content}
  .toolbar .views:has(.cardsize) > .field.cardsize{flex:0 0 auto}
  .toolbar .views:has(.cardsize) > .field.filter{flex:1.4 1 10rem;min-width:10rem}
  .toolbar .views:has(.cardsize) > .hint{flex:1 1 240px;min-width:0} }
.field .sel{position:relative;display:block}
.field .sel > svg{position:absolute;left:.75rem;top:50%;transform:translateY(-50%);color:var(--orange);
  pointer-events:none}
.field .sel > svg ~ select,.field .sel > svg ~ .msel-btn{padding-left:2.1rem}
.field .search{margin:0}
.toolbar .apply{margin-top:.5rem}
@media (max-width:1200px){ .toolbar .controls{grid-template-columns:1fr}
  .toolbar .views{grid-template-columns:repeat(3,minmax(0,1fr))}
  .toolbar .views:has(.cardsize){grid-template-columns:repeat(auto-fit,minmax(min(100%,10rem),1fr))}
  .toolbar .views .field.cardsize .sizeseg{width:100%}
  .toolbar .views .field.cardsize .sizeseg button{flex:1 1 0}
  .toolbar .filter{grid-column:1 / -1} }
@media (max-width:600px){ .toolbar{padding:.5rem .75rem .75rem}
  .toolbar .views{grid-template-columns:1fr 1fr}
  .toolbar .views .field:nth-child(3){grid-column:1 / -1}
  .toolbar .views:has(.cardsize) .field:nth-child(3){grid-column:auto}
  .toolbar .quickrow button span{display:none} .toolbar .quickrow button{width:var(--ctl);padding:0;
    justify-content:center} }

/* category (stack) headers */
.stackhead{padding-top:.5rem;margin-bottom:.25rem}
.stackhead h2{display:flex;align-items:center;justify-content:space-between;gap:.5rem;font-size:14px;
  font-weight:700;min-height:28px;margin:0}
.stackhead .title{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stackhead .title.strike{text-decoration:line-through;color:var(--text-muted)}
.stackhead .meta{font-size:12px;color:var(--text-muted);display:block}
.stackhead summary.icon-only{width:28px;height:28px;border:0;background:transparent;color:var(--text-muted)}
.stackhead summary.icon-only:hover{color:var(--orange)}

.stats .pip,.ccard .pip{width:18px;height:18px}

/* text view rows (textViewCard: 30px, bold name, mana column): the categories flow down
   columns like a newspaper, so a short category leaves no hole under it */
.deckview.text{columns:300px;column-gap:2rem}
.deckview.text .stack{break-inside:avoid;page-break-inside:avoid;margin-bottom:.75rem}
ul.rows{list-style:none;margin:0;padding:0}
ul.rows .row{display:grid;grid-template-columns:1.6rem minmax(0,1fr) auto 3rem 4.5rem;align-items:center;
  gap:.4rem;height:30px;border-top:1px solid var(--surface-2);position:relative;font-size:1rem}
ul.rows .row:hover{background:var(--surface-2)}
ul.rows .row.side{opacity:.7}
ul.rows .q{color:var(--text-muted);text-align:right;font-variant-numeric:tabular-nums}
ul.rows .n{display:flex;align-items:center;gap:.35rem;min-width:0}
ul.rows .n .name{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
ul.rows .mc{min-width:0}
ul.rows .set{font-size:.78rem;color:var(--text-muted);text-align:center}
ul.rows .price{font-size:.86rem;color:var(--text-muted);text-align:right;font-variant-numeric:tabular-nums}
.finish{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;
  border-radius:3px;
  font-size:10px;font-weight:900;background:linear-gradient(135deg,#f6d365,#b7e3ff 50%,#f6a5c0);
  color:#111;flex:none}
.tagdot{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--orange);flex:none}
.deckview .c .tagdot{position:absolute;right:8px;top:8px;width:14px;height:14px;box-shadow:0 0 0 2px #fff}
.owned{display:inline-block;width:9px;height:9px;border-radius:50%;background:#1ebb6c;flex:none;
  box-shadow:0 0 0 2px var(--bg)}
.deckview .c .owned{position:absolute;left:8px;bottom:8px;width:12px;height:12px;box-shadow:0 0 0 2px #fff}
ul.rows .hover{display:none;position:absolute;left:2rem;top:30px;z-index:12;width:223px;aspect-ratio:5/7;
  border-radius:4.5%;overflow:hidden;box-shadow:var(--shadow);background:var(--surface-2);pointer-events:none}
ul.rows .hover img{width:100%;height:100%;display:block}
@media (hover:hover) and (min-width:700px){ ul.rows .row:hover .hover{display:block} }
@media (max-width:600px){ ul.rows .row{grid-template-columns:1.6rem minmax(0,1fr) auto}
  ul.rows .set,ul.rows .price{display:none} }

/* stack order handles (deck.js): drag, or arrow keys while focused */
/* button.stackgrip, so the 28 px drawn size wins over the theme's icon-only width (the control height,
   44 px on touch); the ::before halo below is the finger target */
button.stackgrip{position:relative;width:28px;min-width:28px;height:28px;min-height:28px;padding:0;
  margin:0 .25rem 0 0;cursor:grab;touch-action:none;user-select:none;-webkit-user-select:none;
  -webkit-touch-callout:none;color:var(--text-muted);font-size:1rem;line-height:1;flex:none}
/* a 44 px target for a finger without making the header taller */
.stackgrip::before{content:'';position:absolute;inset:-8px}
/* no press-down nudge (theme buttons move 1 px): in the columns that lays every column out again */
.stackgrip:active:not(:disabled){cursor:grabbing;transform:none;filter:none}
/* no opacity here or on a dragged row: in the text and Brewer views' columns a change of opacity
   lays every column out again, which takes as long as moving the stack itself */
.stack.moving{outline:2px dashed var(--orange);outline-offset:4px;background:var(--surface-2)}
/* while a stack is dragged a bar shows where it lands and its name follows the pointer; both, and
   a card slid by a finger, move by transform only */
/* nor does a row's hover picture or a stack's fan open under the pointer meanwhile, nor while a
   card is dragged (deck.js): the fan would cover the top of the stack directly below */
.deckview.sorting ul.rows .row:hover .hover,.deckview.carddrag ul.rows .row:hover .hover{display:none}
.deckview.sorting.stacks .c:hover ~ .c{transform:none}
/* at once, not after the fan's 0.3 s slide: a quick drag reaches the lower stack before that */
.deckview.carddrag.stacks .c:hover ~ .c{transform:none;transition:none}
.stackdrop,.stacktag,.dragghost{position:fixed;left:0;top:0;pointer-events:none;will-change:transform;margin:0}
.stackdrop{z-index:70;width:1px;height:1px;opacity:0;transform-origin:0 0;background:var(--orange)}
.stacktag{z-index:71;max-width:60vw;padding:.3rem .6rem;border-radius:var(--radius);background:var(--surface);
  color:var(--text);border:1px solid var(--orange);box-shadow:var(--shadow);font-size:.9rem;font-weight:700;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dragghost{z-index:70;opacity:.92;box-shadow:var(--shadow);background:var(--surface);border-radius:4.5%}
.dragghost .row{border-top:0;padding:0 .25rem}
.stack-reset{margin:.25rem 0 1rem}
.stackhead .stackgrip ~ .title{flex:1;min-width:0}
/* select several cards (own deck): the bar above the cards, and the chosen cards ringed */
.bulkbar{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin:0 0 .75rem}
.bulkbar .bulktools{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.bulkbar .bulktools[hidden],.bulkbar .btn[hidden]{display:none}
.bulkbar select:not(.msel-native){width:auto;max-width:100%;min-width:0;min-height:36px}
.bulkbar .msel-btn{max-width:100%;min-width:0}
.bulkbar .count{font-weight:600}
.bulkbar .histtools{display:inline-flex;gap:.25rem}
.bulkbar .tagform{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;flex-basis:100%}
.bulkbar .tagform[hidden]{display:none}
.bulkbar .tagform input[type=text]{width:auto;flex:1 1 10rem;min-width:0;max-width:18rem}
.bulkbar .tagform input[type=color]{width:44px;min-width:44px;height:36px;padding:2px;border-radius:6px}
.bulkbar .status{margin:0;flex-basis:100%} .bulkbar .status:empty{display:none}
.deckview.picking .c,.deckview.picking .row{cursor:pointer}
.deckview.picking .c.picked{outline:3px solid var(--orange);outline-offset:2px}
/* a check mark, so a chosen card differs from the focused one (same orange ring) */
.deckview.picking .c.picked::after{content:'✓';position:absolute;left:50%;top:50%;
  transform:translate(-50%,-50%);
  width:44px;height:44px;border-radius:50%;background:var(--orange);color:#000;font-size:26px;font-weight:700;
  display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 3px var(--bg)}
.deckview.picking .row.picked{background:var(--surface-2);box-shadow:inset 4px 0 0 var(--orange)}
.deckview.picking .row.picked .name{font-weight:700}
/* Table view: one full-width list per group, a column each for type, mana value, cost, printing */
.deckview.table .stack{margin-bottom:1rem}
.deckview.table ul.rows .row,.deckview.table ul.rows .th{
  grid-template-columns:2rem minmax(0,2fr) minmax(0,1.6fr) 2.5rem minmax(4rem,auto) 5.5rem 4.5rem}
.deckview.table ul.rows .th{display:grid;align-items:center;gap:.4rem;height:28px;font-size:.78rem;
  font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted)}
.deckview.table ul.rows .ty{font-size:.86rem;color:var(--text-muted);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;min-width:0}
.deckview.table ul.rows .mv{text-align:center;font-variant-numeric:tabular-nums}
.deckview.table ul.rows .set{text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media (max-width:700px){
  .deckview.table ul.rows .row,.deckview.table ul.rows .th{
    grid-template-columns:1.6rem minmax(0,1fr) 2rem auto}
  .deckview.table ul.rows .ty,.deckview.table ul.rows .set,.deckview.table ul.rows .price{display:none} }
/* Scroll view: each group one row of whole cards, scrolled sideways inside its own strip */
/* brewer view: each card's picture, name and cost, type line and rules text, in columns */
.deckview.brewer{columns:340px;column-gap:1.5rem}
.deckview.brewer .stack{break-inside:avoid;page-break-inside:avoid;margin-bottom:1rem}
ul.rows .row.brew{display:flex;align-items:flex-start;gap:.6rem;height:auto;padding:.4rem 0}
.row.brew .art{width:56px;aspect-ratio:63/88;border-radius:4px;object-fit:cover;flex:none;
  background:var(--surface-2)}
.row.brew .bd{display:flex;flex-direction:column;gap:.15rem;min-width:0;flex:1}
.row.brew .n .q{min-width:1.2rem}
.row.brew .n .mc{margin-left:auto;flex:none}
.row.brew .ty{font-size:.82rem;color:var(--text-muted)}
.row.brew .tx{font-size:.82rem;white-space:pre-line;display:-webkit-box;-webkit-line-clamp:4;
  -webkit-box-orient:vertical;overflow:hidden}
.row.brew .price{text-align:left}
.deckview.scroll .stack{margin-bottom:1rem;min-width:0}
.deckview.scroll .cards{display:flex;gap:.75rem;overflow-x:auto;overscroll-behavior-x:contain;
  scroll-snap-type:x proximity;padding:.25rem .1rem .75rem}
.deckview.scroll .c{flex:0 0 clamp(140px,22vw,200px);scroll-snap-align:start}
/* image cards: stacks and grid (basicCard 5:7, 4.5% radius, 2px border, corner quantity) */
.deckview.stacks,.deckview.grid{display:grid;gap:1rem;align-items:start}
.deckview.stacks{grid-template-columns:repeat(auto-fill,minmax(min(100%,170px),1fr))}
.deckview.grid{grid-template-columns:1fr}
.deckview .c{position:relative;aspect-ratio:5/7;border-radius:4.5%;border:2px solid var(--card-border);
  overflow:hidden;background:var(--surface-2);transition:transform .3s ease-in-out}
.deckview .c img{width:100%;height:100%;display:block;object-fit:cover}
.deckview .c .qty{position:absolute;top:0;left:0;width:38px;height:38px;clip-path:polygon(0 0,0 100%,100% 0);
  border-radius:11px 0 0 0;background:#3a3a3a;color:#fff;font-size:12px;font-weight:700;padding:5px 0 0 7px}
.deckview .c .finish{position:absolute;right:6px;bottom:6px}
.deckview .c .corner{position:absolute;top:0;right:0;width:30px;height:30px;
  clip-path:polygon(100% 0,0 0,100% 100%)}
.deckview .c .corner.comp{background:#e03997} .deckview .c .corner.gc{background:#fa890d}
.deckview .c .ph{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:space-between;
  padding:12% 8% 8%;background:linear-gradient(160deg,var(--surface-3),var(--surface-2));font-size:.8rem}
.deckview .c .ph .t{display:flex;justify-content:space-between;gap:.3rem;align-items:flex-start}
.deckview .c .ph .nm{font-weight:700;overflow-wrap:anywhere}
.deckview .c .ph .ty{color:var(--text-muted);font-size:.72rem}
.deckview.stacks .cards{display:flex;flex-direction:column}
.deckview.stacks .c + .c{margin-top:-123%}
.deckview.stacks .c .ph{padding:3% 6% 8% 42px}
.deckview.stacks .c .ph .t{align-items:center;min-height:0}
.deckview.stacks .c .ph .nm{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.deckview.stacks .c .ph .mana{flex:none}
.deckview .c{cursor:pointer}
.deckview .c:focus-visible{outline:3px solid var(--orange);outline-offset:2px}
/* a stack fans out below the card under the pointer (Archidekt's hover); on touch screens a tap on the
   stack toggles the fan instead, and a tap on a fanned card opens it */
@media (hover:hover){ .deckview.stacks .c:hover ~ .c{transform:translateY(90%)} }
.deckview.stacks .cards.fanned .c + .c{margin-top:-108%}
.deckview.stacks .cards.fanned .c{transform:none}
.deckview .c.dragging{opacity:.4}
.deckview .row.dragging{outline:2px dashed var(--orange);outline-offset:-2px;background:var(--surface-2)}
.deckview .stack.dropping{outline:3px dashed var(--orange);outline-offset:4px;border-radius:5px}
.deckview[data-drop] .stackhead .meta::after{content:' · drag cards here to recategorize';
  color:var(--text-muted)}
@media (hover:none){ .deckview[data-drop] .stackhead .meta::after{content:' · hold a card to move it'} }
/* card viewer: a tapped card, large, with what can be done with it. A dialog over a blurred,
   darkened page; the image column grows with the window, the text column scrolls on its own. */
.cardview{position:fixed;inset:0;z-index:60;display:none;align-items:center;justify-content:center;
  background:var(--scrim-strong,rgba(0,0,0,.78));padding:1.5rem;
  -webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)}
.cardview.open{display:flex}
html.cardview-open{overflow:hidden}
.cardview .box{display:grid;grid-template-columns:minmax(14rem,24rem) minmax(16rem,1fr);
  grid-template-rows:auto minmax(0,1fr);grid-template-areas:'pic head' 'pic info';gap:.75rem 1.25rem;
  width:min(100%,58rem);max-height:100%;background:var(--surface);color:var(--text);
  border:1px solid var(--border);border-radius:var(--radius-panel);padding:1.25rem;
  box-shadow:0 12px 40px rgba(0,0,0,.5);overflow:hidden;animation:cardin .16s ease-out}
@keyframes cardin{from{opacity:0;transform:translateY(8px) scale(.985)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){ .cardview .box{animation:none} }
.cardview .pane{grid-area:pic;min-width:0;align-self:start}
.cardview img,.cardview .ph{width:100%;aspect-ratio:5/7;border-radius:4.5%;object-fit:cover;
  background:var(--surface-2);display:block}
.cardview .ph{display:flex;align-items:center;justify-content:center;color:var(--text-muted);padding:1rem;
  text-align:center;font-weight:700}
.cardview .info{grid-area:info;min-width:0;display:flex;flex-direction:column;gap:.75rem;min-height:0}
.cardview .head{grid-area:head;display:flex;align-items:flex-start;justify-content:space-between;gap:.75rem;
  min-width:0}
.cardview h3{margin:0;font-size:1.35rem;line-height:1.25;display:flex;align-items:center;gap:.6rem;
  flex-wrap:wrap;overflow-wrap:anywhere;min-width:0}
.cardview .close{flex:none;margin:0;width:2.4rem;min-height:2.4rem;height:2.4rem;font-size:1.5rem;
  line-height:1;font-weight:400;border-radius:50%}
.cardview .cardtext{overflow:auto;min-height:0;padding-right:.25rem;overscroll-behavior:contain}
.cardview h4{margin:.9rem 0 .2rem;font-size:1.05rem;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.cardview .face:first-child h4{margin-top:0}
.cardview .typeline{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem .75rem;margin:0 0 .5rem;
  font-weight:700;font-size:.95rem}
.cardview .typeline .pt{padding:0 .5rem;border:1px solid var(--border);border-radius:var(--radius);
  background:var(--bg)}
.cardview .rules{white-space:pre-line;font-size:1rem;line-height:1.5;margin:0 0 .6rem;overflow-wrap:anywhere}
.cardview .flavor{font-style:italic;color:var(--text-muted);font-size:.93rem;white-space:pre-line;
  margin:0 0 .6rem;overflow-wrap:anywhere}
.cardview .facts{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:.3rem .9rem;margin:0;
  font-size:.9rem;border-top:1px solid var(--border-soft);padding-top:.75rem}
.cardview .facts dt{color:var(--text-muted);font-weight:700}
.cardview .facts dd{margin:0;min-width:0;overflow-wrap:anywhere}
.cardview .facts dd.notes{white-space:pre-wrap}
.cardview .facts .gc{color:var(--orange-text);font-weight:700}
.cardview .chips{display:flex;flex-wrap:wrap;gap:.3rem}
.cardview .chip{display:inline-block;padding:.05rem .5rem;border-radius:1rem;background:var(--surface-2);
  font-size:.82rem;line-height:1.5;white-space:nowrap}
.cardview .pane .flip{display:flex;margin:.5rem auto 0;min-height:40px}
.cardview .pane .flip[hidden]{display:none}
.cardview .more{display:flex;flex-wrap:wrap;gap:.4rem}
.cardview .more .btn{margin:0;min-height:40px}
.cardview .rulings{font-size:.9rem;border-top:1px solid var(--border-soft);padding-top:.5rem;flex:0 1 auto;
  min-height:4rem;overflow:auto;overscroll-behavior:contain}
.cardview .rulings ul{list-style:none;margin:0;padding:0;display:grid;gap:.6rem}
.cardview .rulings time{font-weight:700;color:var(--text-muted);font-size:.82rem;display:block}
.cardview .rulings p{margin:.1rem 0;line-height:1.45;overflow-wrap:anywhere}
.cardview .rulings .src{font-size:.8rem}
.cardview .prices table{border-collapse:collapse;font-variant-numeric:tabular-nums;margin:.3rem 0}
.cardview .prices th,.cardview .prices td{padding:.15rem .6rem .15rem 0;text-align:left;
  border-bottom:1px solid var(--border-soft)}
.cardview .acts{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:auto;padding-top:.25rem}
.cardview .acts .btn,.cardview .acts button{margin:0;flex:1 1 auto}
@media (max-width:700px){
  .cardview{padding:0;align-items:flex-end;-webkit-backdrop-filter:none;backdrop-filter:none}
  .cardview .box{grid-template-columns:minmax(0,44%) minmax(0,1fr);grid-template-rows:auto auto;
    grid-template-areas:'pic head' 'info info';gap:.75rem;width:100%;max-height:94vh;max-height:94dvh;
    border-radius:var(--radius-panel) var(--radius-panel) 0 0;border-bottom:0;
    padding:.75rem .75rem calc(.75rem + env(safe-area-inset-bottom));overflow:auto;animation-name:cardup}
  @keyframes cardup{from{transform:translateY(12px)}to{transform:none}}
  .cardview .head{flex-direction:column-reverse;align-items:flex-end;justify-content:flex-end;gap:.5rem}
  .cardview .head h3{align-self:stretch;font-size:1.2rem}
  .cardview .cardtext,.cardview .rulings{overflow:visible}
  .cardview .acts .btn,.cardview .acts button{flex:1 1 100%} }
@media (min-width:1400px){
  .cardview .box{width:min(100%,66rem);grid-template-columns:minmax(16rem,27rem) minmax(0,1fr)} }
/* the card menu (deck.js): its base is theme.CARD_MENU_CSS */
.ctxmenu .qtyrow{display:flex;align-items:center;gap:.5rem;min-height:38px;padding:0 1rem;cursor:default}
.ctxmenu .qtyrow .lbl{flex:1}
.ctxmenu .mvform{display:flex;flex-direction:column;gap:.4rem;padding:.25rem .75rem .5rem}
.ctxmenu .mvform label{font-weight:600}
.ctxmenu .mvform input{width:6rem;min-height:38px}
.ctxmenu .mvform .hint{margin:0;font-size:.85rem;color:var(--text-muted)}
.ctxmenu .mvform .acts{display:flex;gap:.4rem;flex-wrap:wrap}
.ctxmenu .mvform .acts .btn{min-height:38px;justify-content:center}
.ctxmenu .mvform .status{margin:0;font-size:.9rem}
.ctxmenu .mvform .status.err{color:var(--red-text)}
.ctxmenu .qtyrow .step{width:36px;min-width:36px;height:32px;min-height:32px;padding:0;justify-content:center;
  border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);font-size:1.15rem;
  font-weight:700;line-height:1}
.ctxmenu .qtyrow .step:hover:not(:disabled){background:var(--border)}
.ctxmenu .qtyrow .n{min-width:1.6rem;text-align:center;font-variant-numeric:tabular-nums}
.ctxmenu .more .chev{margin-left:auto;color:var(--text-muted);font-size:1.2rem;line-height:1}
.ctxmenu .more[aria-expanded=true] .chev{transform:rotate(90deg)}
.ctxmenu .sub{display:flex;flex-direction:column;background:var(--surface);border-top:1px solid var(--border);
  border-bottom:1px solid var(--border);max-height:40vh;overflow:auto}
.ctxmenu .sub[hidden]{display:none}
.ctxmenu .sub button{padding-left:2.4rem;min-height:34px;font-size:.95rem}
.ctxmenu .sub .on:disabled{color:var(--toolbar-active);font-weight:700}
.deckview .c.held,.deckview .row.held{outline:3px solid var(--orange);outline-offset:2px}
/* the toast after a save (theme .toast, bottom right): the message, Undo and History */
.deck-toast{display:flex;align-items:center;gap:.75rem;flex-wrap:wrap;padding:.75rem 1rem;z-index:59;
  border-left:4px solid var(--blue);color:var(--text)}
.deck-toast.error{border-left-color:var(--red)} .deck-toast.warn{border-left-color:var(--orange)}
/* the note of a save sits above the card menu, which stays open for more quantity steps; a
   question ("Save anyway?") and an error sit above the card viewer too */
.deck-toast[role=alertdialog],.deck-toast.error{z-index:66}
.deck-toast .msg{flex:1 1 10rem;min-width:0;overflow-wrap:anywhere}
.deck-toast .acts{display:inline-flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.deck-toast .acts button,.deck-toast .acts .btn{margin:0;min-height:34px;padding:0 .75rem}
.deck-toast .acts a:not(.btn){color:var(--orange-text);font-weight:700;text-decoration:underline}
.deck-toast .close{width:2rem;min-height:2rem;height:2rem;padding:0;margin:0 0 0 auto;border:0;
  background:transparent;font-size:1.3rem;line-height:1;color:var(--text-muted)}
.deck-toast .close:hover{color:var(--text)}
/* Play on EDHPlay's note: one line per point (deck.js) */
.edhplay-toast .msg span{display:block} .edhplay-toast .msg span+span{margin-top:.3rem}
/* its box: the editor's printing picker (.picker below), narrow and in the middle of the
   window on a phone too (the picker is a bottom sheet there), with the import steps folded in */
.picker.edhplay-dialog{padding:1rem;align-items:center}
.picker.edhplay-dialog .pickbox{width:min(100%,30rem);max-height:100%;overflow:auto;padding:1rem 1.15rem;
  border-radius:var(--radius-panel);border-bottom:1px solid var(--border)}
.edhplay-dialog details.disclosure{margin:0;padding-top:.15rem}
/* finger-sized (44 px): the fold's summary and the box's x */
.edhplay-dialog details.disclosure > summary{min-height:44px}
.picker.edhplay-dialog .pickbox .close{width:44px;min-height:44px;height:44px;margin:-.2rem -.35rem 0 0}
.edhplay-dialog .steps{margin:.15rem 0 .2rem;padding-left:1.4rem;display:flex;flex-direction:column;gap:.4rem}
.edhplay-dialog p{margin:0}
.edhplay-dialog .edhplay-state{display:flex;flex-direction:column;gap:.5rem}
.edhplay-dialog .acts{display:flex;margin-top:.25rem}
.edhplay-dialog .acts .btn{margin:0;flex:1;justify-content:center}
/* the list could not be had: the reason, and Try again beside it (a wrapped line on a phone) */
.edhplay-dialog .notice.error{display:flex;flex-wrap:wrap;align-items:center;gap:.35rem .75rem;margin:0}
.edhplay-dialog .notice.error span{flex:1 1 12rem}
.edhplay-dialog .notice.error .btn{margin:0}
@media (max-width:600px){ .deck-toast{right:.5rem;left:.5rem;bottom:.5rem;width:auto;max-width:none} }
@media (max-width:900px){ .has-tabbar .deck-toast{bottom:calc(64px + env(safe-area-inset-bottom))} }
/* the viewer's own copies control: one fewer, the count, one more */
.cardview .acts .qtyctl{display:inline-flex;align-items:center;gap:.4rem;flex:1 1 auto}
.cardview .acts .qtyctl button{flex:0 0 auto;width:2.6rem;padding:0;font-size:1.2rem;font-weight:700}
.cardview .acts .qtyctl .n{min-width:2rem;text-align:center;font-size:1.1rem;
  font-variant-numeric:tabular-nums}
/* grid view: cards about 170 to 230 px wide, as many per row as fit (five in a 1366 px window) */
.deckview.grid .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:1rem}
@media (max-width:600px){ .deckview.grid .cards{grid-template-columns:repeat(2,minmax(0,1fr))}
  .deckview.stacks{grid-template-columns:repeat(2,minmax(0,1fr));gap:.75rem} }
/* a stack never widens its column past the window (its header's name ellipsises; 0.7.29 fix: at
   360 px a stack's Options button sat 11 px outside the window) */
.deckview.stacks .stack{min-width:0}

/* deck stats panel */
.stats .head{display:flex;justify-content:space-between;align-items:center;gap:1rem}
.stats .grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(16rem,1fr);gap:1.5rem}
.stats .lbl{font-weight:700;margin:.75rem 0 .25rem}
.cbar{display:flex;height:36px;border-radius:3px;overflow:hidden;background:var(--surface-2);gap:2px}
.cbar .seg{display:block;height:100%;clip-path:polygon(0 0,100% 0,calc(100% - 7px) 100%,0 100%);min-width:6px}
.cbar.empty{align-items:center;justify-content:center;font-size:.8rem;color:var(--text-muted)}
.seg-W{background:#f8f6d8} .seg-U{background:#92c5de} .seg-B{background:#8a8a8a} .seg-R{background:#e6948a}
.seg-G{background:#a6d8a0} .seg-C{background:#cbc2bf}
.ccards{display:grid;grid-template-columns:repeat(auto-fill,minmax(14rem,1fr));gap:1rem;margin-top:1rem}
.ccard{border:1px solid var(--border);border-radius:3px;padding:.75rem;background:var(--bg)}
.ccard .cname{font-weight:700;display:flex;align-items:center;gap:.4rem;margin-bottom:.25rem}
.ccard .lbl{margin:.5rem 0 .25rem;font-size:.93rem}
.pbar{position:relative;height:26px;border-radius:3px;background:var(--surface-2);overflow:hidden}
.pbar .fill{position:absolute;left:0;top:0;bottom:0}
.pbar b{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);font-size:.8rem;
  background:var(--surface);color:var(--text);padding:0 .35rem;border-radius:3px;
  border:1px solid var(--border)}
.ccard .sub{font-size:.8rem;color:var(--text-muted);text-align:center;margin-top:.2rem}
.stats .avg{margin:1rem 0 .25rem}
.curve{display:flex;align-items:flex-end;gap:min(.5rem,2.1vw);height:9rem;padding:.25rem 0;
  border-bottom:1px solid var(--border)}
.curve .bar{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;
  justify-content:flex-end;height:100%;font-size:min(.75rem,3.5vw)}
.curve .bar .col{display:flex;flex-direction:column-reverse;width:70%;gap:2px;border-radius:2px 2px 0 0;
  overflow:hidden}
.curve .bar b{font-variant-numeric:tabular-nums;margin-bottom:.15rem}
/* stats controls (static/deckstats.js): Mana curve by, Bar / Pie, Lands only; a click on a chart part
   or a legend, axis or table button shows just those cards in the list above */
.statbox{min-width:0}
.statctl{display:flex;flex-wrap:wrap;align-items:center;gap:.35rem;margin:.75rem 0 .25rem;font-size:.9rem}
.statctl[hidden]{display:none}
.statctl span{font-weight:700;margin-right:.25rem}
.statctl button{min-height:32px;padding:0 .7rem;font-size:.9rem}
.statctl button[aria-pressed=true]{background:var(--orange);border-color:var(--orange);color:var(--on-orange);
  font-weight:700}
.statctl button[data-set=lands]{margin-left:.5rem}
.sg{display:block;flex:1 1 0;min-height:2px;background:var(--orange);cursor:pointer}
.k1{background:#3987e5} .k2{background:#eb6834} .k3{background:#1baf7a} .k4{background:#eda100}
.k5{background:#e87ba4} .k6{background:#9085e9} .k7{background:#e34948} .k0{background:#8a8a8a}
/* the counts and the axis can't wrap: with large text on a narrow phone their gaps and type stop
   growing at a share of the screen width (min(), so 100% text is unchanged) */
.curveaxis{display:flex;gap:min(.5rem,2.1vw)}
.curveaxis .mv{flex:1;min-width:0;min-height:28px;padding:0;border:0;background:none;font-weight:700;
  font-size:min(.8rem,3.8vw);color:var(--text)}
.curveaxis .mv:hover:not(:disabled),.slegend button:hover,.rowbtn:hover{background:var(--surface-2)}
.slegend{list-style:none;display:flex;flex-wrap:wrap;gap:.25rem .5rem;margin:.5rem 0 0;padding:0}
.slegend button{min-height:28px;padding:0 .4rem;border:0;background:none;font-size:.85rem;gap:.35rem;
  color:var(--text);font-weight:400;max-width:100%;overflow-wrap:anywhere;text-align:left}
.slegend button b{font-variant-numeric:tabular-nums}
.slegend .sw{display:inline-block;flex:none;width:.8rem;height:.8rem;border-radius:2px}
.cbar .seg[data-focus],.pie path[data-focus]{cursor:pointer}
.pies{display:flex;flex-wrap:wrap;gap:1rem 2rem;margin-top:.5rem}
.pie{margin:0;flex:1 1 12rem;min-width:0;display:flex;flex-direction:column;align-items:flex-start}
.pie figcaption{font-weight:700;margin-bottom:.35rem}
.pie svg{width:9rem;height:9rem;max-width:100%}
.pie path{stroke:var(--surface);stroke-width:1.5;stroke-linejoin:round}
.pie .seg-W{fill:#f8f6d8} .pie .seg-U{fill:#92c5de} .pie .seg-B{fill:#8a8a8a} .pie .seg-R{fill:#e6948a}
.pie .seg-G{fill:#a6d8a0} .pie .seg-C{fill:#cbc2bf}
.pie .slegend{flex-direction:column;gap:0}
.pie .slegend .muted{font-size:.8rem}
.rowbtn{min-height:24px;padding:0 .25rem;margin:0 0 0 -.25rem;border:0;background:none;color:var(--text);
  font:inherit;text-align:left;overflow-wrap:anywhere;justify-content:flex-start;gap:.4rem}
.ccard .cname .rowbtn{font-weight:700}
[data-focus].on{outline:2px solid var(--focus);outline-offset:1px}
.ooi{display:flex;gap:.4rem;align-items:baseline;margin:.75rem 0 0;font-size:.93rem}
.ooi svg{flex:none} .ooi.ok svg{color:var(--green-text)} .ooi.bad svg{color:var(--red-text)}
.ooi .pip{width:16px;height:16px;vertical-align:-3px}
.ooi .rowbtn{color:var(--link);text-decoration:underline;margin:0}
.qtyscroll{max-height:16rem;overflow:auto}
.statfocus{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin:.5rem 0;padding:.5rem .75rem;
  border:1px solid var(--border);border-radius:3px;background:var(--surface)}
.statfocus span{flex:1 1 12rem;min-width:0;overflow-wrap:anywhere}
.statfocus button{min-height:32px}
/* .tiles/.tile/.spark: theme.py (shared with the report and history pages) */
table.qty{width:100%;border-collapse:collapse;font-size:.93rem}
table.qty td{padding:.3rem .25rem;border-top:1px solid var(--surface-2);overflow-wrap:anywhere}
table.qty td:last-child{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.legality ul{margin:.25rem 0 0;padding-left:1.2rem} .legality .ok{color:var(--green-text);font-weight:700}
.odds{margin-top:1.25rem} .odds h3{margin:0 0 .4rem}
.odds .oddsform{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;font-size:.9rem;
  color:var(--text-muted)}
.odds .oddsform select:not(.msel-native),.odds .oddsform .msel-btn,.odds .oddsform input{height:2rem;
  padding:0 .4rem;
  font-size:.9rem;width:auto;min-width:6rem;flex:none}
.odds .oddsform .msel-btn{padding-right:1.5rem;background-position:right .5rem center}
/* a long choice (Group by) never runs past the panel on a narrow phone with large text; the hidden
   native select behind a script-drawn one keeps the theme's 1 px size (it used to get 6rem here) */
.odds .oddsform select:not(.msel-native),.odds .oddsform .msel,.odds .oddsform .msel-btn{max-width:100%}
.odds .oddsform input{width:4rem;min-width:4rem}
.odds .oddstable{margin-top:.5rem;max-height:16rem;overflow:auto;display:block;width:100%}
.odds .oddstable td:nth-child(2),.odds .oddstable td:nth-child(3){text-align:right;white-space:nowrap}
.odds .oddstable td:first-child{width:100%}
.odds .oddstable th{text-align:left;font-weight:600;color:var(--text-muted);font-size:.8rem}
.odds .oddstable th:last-child{text-align:right}
.checks ul{list-style:none;margin:.25rem 0 0;padding:0} .checks li{display:flex;gap:.4rem;
  align-items:baseline;
  padding:.2rem 0;border-top:1px solid var(--border)} .checks li:first-child{border-top:0}
.checks li b{flex:none} .checks li span{color:var(--text-muted);overflow-wrap:anywhere;min-width:0}
/* large text on a narrow phone: the value goes under its label instead of past the panel, and the
   label stays beside its icon (it breaks inside a word only if that word alone is too wide). The
   value keeps its place beside the label, wrapping inside it, while at least 6rem is left there */
.checks li{flex-wrap:wrap;column-gap:min(.4rem,2vw)} .checks li svg{flex:none}
.checks li span{flex:1 1 6rem}
.checks li b{flex:0 1 auto;min-width:0;overflow-wrap:anywhere}
.checks li.ok svg{color:var(--green-text)} .checks li.bad svg{color:var(--red-text,#d33)}
@media (max-width:1000px){ .stats .grid{grid-template-columns:minmax(0,1fr)} }

.description{font-size:16px} .description .empty{color:var(--text-muted)}

/* deck list (deckLink grid: thumbnail with colour bar, info block, tags strip) */
.listbar .controls{display:grid;grid-template-columns:1fr repeat(2,minmax(140px,auto)) auto;gap:1rem;
  align-items:end}
.listbar .field.grow{min-width:12rem}
.listbar .field .btn{margin:0}
.listbar .apply{margin-top:.5rem}
@media (max-width:1000px){ .listbar .controls{grid-template-columns:minmax(0,1fr) minmax(0,1fr);
    align-items:start}
  .listbar .field:last-of-type{grid-column:1 / -1;display:flex;flex-direction:row;flex-wrap:wrap;gap:.5rem;
    align-items:center}
  .listbar .field:last-of-type .lbl{margin-right:auto} .listbar .field.grow{min-width:0} }
@media (max-width:600px){ .listbar .controls{grid-template-columns:minmax(0,1fr)} }
/* the list couldn't be read: the reason and Try again, a full-size touch target */
.decks-unreadable p{margin:0}
.decks-unreadable .actions .btn{min-height:44px}
/* minmax(0,1fr), never 1fr: a long deck name (the "Copy of - …" names the app itself makes) must shrink
   and ellipsise inside its tile, not widen the track and scroll the page sideways. */
ul.decklist.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,220px),1fr));gap:25px;
  margin-bottom:1.25rem}
@media (max-width:600px){ ul.decklist.grid{gap:1rem} }
ul.decklist .deck,ul.decklist .deck a,ul.decklist .info{min-width:0}
ul.decklist .deck a{display:flex;flex-direction:column;color:var(--text);text-decoration:none;
  border-radius:5px;overflow:hidden;border:1px solid var(--border);background:var(--surface)}
ul.decklist .deck.selected a{outline:2px solid var(--orange)}
ul.decklist .thumb{position:relative;display:block;height:175px;
  background:var(--surface-3) center/cover no-repeat;
  overflow:hidden;transition:transform .2s ease}
ul.decklist .thumb::after{content:'';position:absolute;inset:0;
  background:linear-gradient(to bottom,rgba(0,0,0,0) 55%,var(--surface) 100%)}
ul.decklist .deck a:hover .thumb{transform:scale(1.02)}
ul.decklist .thumb .ini{display:none;position:absolute;inset:0;align-items:center;justify-content:center;
  font-size:4rem;font-weight:900;color:rgba(255,255,255,.25)}
ul.decklist .thumb.noart .ini{display:flex}
ul.decklist .thumb.noart{background:linear-gradient(135deg,#3a3a3a,#1f1f1f)}
ul.decklist .thumb .views{position:absolute;right:.5rem;top:.5rem;font-size:12px;color:#fff;
  background:rgba(0,0,0,.55);padding:.1rem .4rem;border-radius:3px;z-index:1}
ul.decklist .thumb .cbar{position:absolute;left:0;right:0;bottom:0;height:15px;border-radius:0;z-index:1;
  gap:0}
ul.decklist .thumb .cbar.empty{display:none}
ul.decklist .info{display:flex;gap:.6rem;align-items:center;min-height:75px;padding:.5rem .75rem}
ul.decklist .info .text{display:flex;flex-direction:column;min-width:0;gap:.1rem}
ul.decklist .info .name{font-size:16px;font-weight:700;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
ul.decklist .deck a:hover .name{text-decoration:underline}
ul.decklist .info .fmt,ul.decklist .info .sub{font-size:.86rem;color:var(--text-muted);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
ul.decklist .tags{display:flex;flex-wrap:wrap;gap:.35rem;padding:.4rem .75rem;
  border-top:1px solid var(--border-soft);
  font-size:.86rem;color:var(--text-muted);min-height:2rem;align-items:center}
ul.decklist.list .deck a{flex-direction:row;align-items:center}
ul.decklist.list .thumb{width:90px;height:60px;flex:none}
ul.decklist.list .thumb .ini{font-size:1.5rem} ul.decklist.list .thumb .views{display:none}
ul.decklist.list .info{flex:1;min-height:0}
ul.decklist.list .tags{border-top:0;max-width:30%}
@media (max-width:600px){ ul.decklist.list .deck a{flex-wrap:wrap}
  ul.decklist.list .tags{max-width:none;flex-basis:100%;border-top:1px solid var(--border-soft)}
  ul.decklist.list .tags:empty{display:none} }
ul.decklist.list .deck{margin-bottom:.5rem}

/* editor (own-deck edits that become one proposal) */
.editor .edithead .row{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap}
.editor .edithead .kicker{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;
  color:var(--text-muted)}
.editor .edithead h1.deckname{margin:0;font-size:24px}
.editor .edithead .btn{margin:0}
/* the save bar sticks just under the site's top bar (theme.py: sticky, 50px tall, plus the safe
   area in the app), never over it */
.editbar{position:sticky;top:50px;z-index:20;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;
  background:var(--toolbar-bg);color:var(--toolbar-text);padding:.5rem 1rem;margin:0 -1rem 1rem;
  box-shadow:0 2px 6px rgba(0,0,0,.35)}
@media (min-width:600px){ body.app .editbar{top:calc(50px + env(safe-area-inset-top))}
  html:has(.editbar){scroll-padding-top:120px} }
.editbar button{margin:0}
.editbar .count{color:inherit;opacity:.85}
.editbar .backup{display:inline-flex;align-items:center;gap:.35rem;margin:0;font-size:.9rem;
  opacity:.9;white-space:nowrap}
.editbar .backup input{margin:0}
.editbar .status{margin:0;flex-basis:100%}
.editbar .status:empty{display:none}
/* a question in the save bar (Save anyway? / Discard the unsaved changes?): its own full-width row,
   in the body text colour (--orange-text on the tinted grey bar is 3.9:1 in the light theme) */
.editbar .confirmbar{flex-basis:100%;display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:0;
  padding:.5rem .75rem;color:var(--text)}
.editbar .confirmbar span{flex:1 1 14rem;min-width:0;overflow-wrap:anywhere}
.pendingbox summary{cursor:pointer;font-weight:700}
.pendingbox summary b{margin-left:.5rem;background:var(--orange);color:var(--on-orange);border-radius:10px;
  padding:0 .5rem}
/* add a card: one search bar. Chips above it say where the card goes; the name box leads with the
   Add button and the Foil tick beside it; on wide screens the second column also leaves room for
   the printings that float beside the suggestion list. Never a stray button. */
.addbox form.addcard{display:grid;gap:.6rem .75rem;align-items:end;grid-template-columns:minmax(0,1fr) auto}
.addbox form.addcard .field{margin:0}
.addbox form.addcard .targets{grid-column:1 / -1;display:flex;flex-wrap:wrap;gap:.35rem;align-items:center}
.addbox .tchip{margin:0;height:auto;min-height:1.9rem;padding:.15rem .7rem;border-radius:999px;
  font-size:.86rem;font-weight:600;background:var(--surface);border:1px solid var(--border);
  color:var(--text-muted)}
.addbox .tchip.on{background:var(--orange-tint);border-color:var(--orange);color:var(--text)}
.addbox .tchip.side{margin-left:auto}
.addbox form.addcard .grow{position:relative}
.addbox form.addcard .go{display:flex;flex-direction:row;align-items:center;gap:.75rem}
.addbox form.addcard .go button{margin:0;height:var(--ctl);white-space:nowrap}
.addbox form.addcard .go .foil{display:inline-flex;align-items:center;gap:.35rem;font-size:.9rem;
  white-space:nowrap}
.addbox form.addcard .addstatus{grid-column:1 / -1;margin:0;min-height:1.2em}
.addbox form.addcard .addstatus:empty{display:none}
.addprints{display:none}
@media (min-width:900px){
  .addbox form.addcard{grid-template-columns:minmax(0,1fr) 19rem}
  .addprints{display:block;position:absolute;top:calc(100% + 3px);left:calc(100% + .5rem);width:18.5rem;
    z-index:70;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    box-shadow:var(--shadow);    padding:.6rem .7rem;max-height:min(24rem,60vh);overflow:auto}
  .addprints[hidden]{display:none}
  .addprints .head{display:flex;justify-content:space-between;gap:.5rem;align-items:baseline;
    margin-bottom:.5rem}
  .addprints .head b{overflow-wrap:anywhere}
  .addprints .pgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.4rem;margin:0;
    padding:0;list-style:none}
  .addprints .pgrid li{min-width:0}
  .addprints .hint{margin:.5rem 0 0}
  .addprints .allprints{width:100%;margin:.5rem 0 0;min-height:36px;position:sticky;bottom:0} }
@media (max-width:600px){
  .addbox form.addcard{grid-template-columns:1fr}
  .addbox form.addcard .go{justify-content:space-between}
  .addbox form.addcard .go button{flex:1} }
/* categories side by side on wide screens, one column on phones */
.cats.existing{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,36rem),1fr));gap:1rem;
  align-items:start}
.cats.existing > details.cat{margin:0}
.addbox form.scanpick{display:flex;gap:.5rem;align-items:end;flex-wrap:wrap;margin-bottom:.75rem}
.addbox form.scanpick button{margin:0}
details.cat summary{display:flex;justify-content:space-between;align-items:center;cursor:pointer;
  font-weight:700;font-size:14px;list-style:none;padding:.25rem 0}
details.cat summary::-webkit-details-marker{display:none}
details.cat summary b{color:var(--text-muted);font-weight:400;flex:none}
details.cat summary .cname{min-width:0;overflow-wrap:anywhere}
ul.erows{list-style:none;margin:.5rem 0 0;padding:0}
.erow{display:grid;grid-template-columns:40px minmax(0,1fr) auto minmax(7rem,10rem) auto;gap:.6rem;
  align-items:center;padding:.4rem 0;border-top:1px solid var(--border-soft)}
.erow.changed{background:var(--orange-tint)}
.erow.target,details.cat.target > summary{outline:3px solid var(--orange);outline-offset:2px;
  border-radius:3px}
.erow.removed .name{text-decoration:line-through;color:var(--text-muted)}
.erow.side .thumb{filter:saturate(.6)}
.erow.side .meta{font-style:italic}
.erow .remove{margin-left:.25rem}
/* settings page: cover, tags, folder, delete */
.coverform{display:flex;flex-wrap:wrap;gap:1rem;align-items:flex-end}
.coverart{width:100%;max-width:22rem;aspect-ratio:16/9;object-fit:cover;border-radius:var(--radius-panel);
  background:var(--surface-2);display:flex;align-items:center;justify-content:center;flex:1 1 14rem}
.coverform .field{flex:1 1 14rem;margin:0}
ul.plain.taglist{display:flex;flex-wrap:wrap;gap:.5rem;margin:0 0 1rem}
.taglist li{display:flex;align-items:center;gap:.25rem}
.taglist .pill{font-size:.95rem;padding:.3rem .6rem}
.taglist form.inline{display:inline;margin:0}
.taglist button.mini{width:1.9rem;height:1.9rem;min-height:0;padding:0;margin:0;display:inline-flex;
  align-items:center;justify-content:center;border-radius:50%}
.taglist button.mini svg{width:14px;height:14px}
/* a box (or list) and its button on one row, the button at the box's height: on a phone the box
   narrows rather than the button dropping to a row of its own; only with large text, where the
   two can't share a row, does the button wrap below */
.addtag,.moveform{display:flex;flex-wrap:wrap;gap:.6rem;align-items:flex-end}
.addtag .field,.moveform .field{margin:0;flex:1 1 10rem;max-width:24rem;min-width:0}
/* (main form > button is full width on a phone; here the button keeps its own width) */
main .panel form.addtag > button,main .panel form.moveform > button{flex:none;width:auto}
/* the Fold's cover screen (under 360px): the buttons drop their icon, so the box keeps room for its
   whole placeholder ("Pick someone you follow") and the two still share a row */
@media (max-width:359.98px){ main .panel form.addtag > button svg,
  main .panel form.moveform > button svg{display:none} }
.tagbox .notice,.collabbox .notice,.folderbox .notice,.coverbox .notice{margin:.75rem 0 0}
/* collaborators: one row each, like the page's other lists, the remove button at the end */
ul.plain.collablist{margin:0 0 .75rem;border-bottom:1px solid var(--surface-2)}
.collablist li{flex-wrap:nowrap}
.collablist .who{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;gap:.1rem}
.collablist .name{flex:none}
.collablist li.none{min-height:0;padding:.25rem 0 .75rem}
.follownote{display:flex;flex-direction:column;align-items:flex-start;gap:.5rem}
.follownote p{margin:0;font-weight:400}
.follownote p.lead{font-weight:700}
.follownote .btn{color:var(--text)}
.foldertree li{padding-left:calc(var(--depth,0) * 1.25rem)}
.foldertree li .name{display:flex;align-items:center;gap:.5rem}
.foldertree li .btn{margin-left:auto}
.folderform .row{display:flex;flex-wrap:wrap;gap:.75rem}
.folderform .row .field{margin:0}
.panel.danger{border-color:var(--danger-fill)}
/* --danger-fill is for button backgrounds: as text on the dark panel it is 2.55:1 */
.panel.danger h2{color:var(--danger-text)}
.pastebox{margin:.75rem 0 0}
.pastebox summary{cursor:pointer;font-weight:700;padding:.4rem 0}
.pastebox textarea{width:100%;font-family:ui-monospace,monospace;margin:.5rem 0}
.pastebox .actions{margin-top:.25rem}
.erow .thumb{width:40px;height:56px;border-radius:3px;object-fit:cover;background:var(--surface-3);
  display:inline-flex;align-items:center;justify-content:center;color:var(--text-muted)}
.erow .main{display:flex;flex-direction:column;min-width:0;gap:.1rem}
.erow .name{font-weight:700;display:flex;align-items:center;gap:.4rem;min-width:0}
.erow .name .mana{flex:none}
.erow .name:not(:has(.mana)){white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}
.erow .meta{font-size:.8rem;color:var(--text-muted)}
.erow .note:empty{display:none}
.erow .note{color:var(--orange-text)}
.erow .qty{display:inline-flex;align-items:center;gap:.25rem}
.erow .qty input{width:min(3.4rem,18vw);height:2.25rem;text-align:center;margin:0;padding:0 .25rem;
  font-variant-numeric:tabular-nums}
.erow .qty button.mini{margin:0}
.erow .sel select,.erow .sel .msel-btn{height:2.25rem}
.erow details.dd summary.mini{display:inline-flex;align-items:center;justify-content:center;width:2.25rem;
  height:2.25rem;padding:0;margin:0}
.erow .menu .field{display:flex;flex-direction:column;gap:.25rem;padding:.5rem .75rem}
.erow .menu button{width:100%;text-align:left;border:0;background:none;margin:0;height:35px}
.erow .menu button:hover{background:var(--surface-3)}
.erow button.remove{margin:0}
.picker{position:fixed;inset:0;z-index:60;display:flex;align-items:center;justify-content:center;
  background:rgba(0,0,0,.78);padding:1.5rem;-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)}
.picker[hidden]{display:none}
.picker .pickbox{width:min(100%,64rem);max-height:100%;display:flex;flex-direction:column;gap:.5rem;
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-panel);padding:1.25rem;
  box-shadow:0 12px 40px rgba(0,0,0,.5);overflow:hidden}
.picker .pickbox .head{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem}
.picker .pickbox .head h2{margin:0;font-size:1.3rem;overflow-wrap:anywhere}
.picker .pickbox .close{flex:none;margin:0;width:2.4rem;min-height:2.4rem;height:2.4rem;font-size:1.5rem;
  line-height:1;font-weight:400;border-radius:50%}
.picker .status{margin:0}
@media (max-width:700px){
  .picker{padding:0;align-items:flex-end;-webkit-backdrop-filter:none;backdrop-filter:none}
  .picker .pickbox{width:100%;max-height:94vh;border-radius:var(--radius-panel) var(--radius-panel) 0 0;
    border-bottom:0;padding:.75rem .75rem calc(.75rem + env(safe-area-inset-bottom))} }
/* The printing picker (static/printpick.js), shared by the deck page's card menu and the deck
   editor: a dialog themed like the card viewer, a bottom sheet on a phone. The list of tiles
   scrolls on its own; beside it on a wide screen the preview shows the pointed-at printing
   whole, and below 900px the preview is a sheet over the list, opened by a tap. */
.printpick{position:fixed;inset:0;z-index:62;display:none;align-items:center;justify-content:center;
  background:var(--scrim-strong,rgba(0,0,0,.78));padding:1.5rem;
  -webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)}
.printpick.open{display:flex}
html.printpick-open{overflow:hidden}
.printpick .ppbox{position:relative;width:min(100%,76rem);height:min(100%,56rem);display:flex;
  flex-direction:column;gap:.6rem;background:var(--surface);color:var(--text);border:1px solid var(--border);
  border-radius:var(--radius-panel);padding:1.25rem;box-shadow:0 12px 40px rgba(0,0,0,.5);overflow:hidden;
  animation:cardin .16s ease-out}
@media (prefers-reduced-motion:reduce){ .printpick .ppbox{animation:none} }
.printpick .pphead{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;min-width:0;
  flex:none}
.printpick .pphead h2{margin:0;font-size:1.3rem;line-height:1.25;overflow-wrap:anywhere;min-width:0}
.printpick .close{flex:none;margin:0;width:2.4rem;min-height:2.4rem;height:2.4rem;font-size:1.5rem;
  line-height:1;font-weight:400;border-radius:50%}
.printpick .pptools{display:flex;flex-direction:column;gap:.5rem;flex:none;min-width:0}
.printpick .ppsearch{display:flex;gap:.5rem;min-width:0}
.printpick .ppsearch input{flex:1 1 auto;min-width:0;width:auto;margin:0}
.printpick .ppfiltbtn{display:none;margin:0;flex:none}
/* the card size switch (0.7.29) keeps its size; on a narrow phone it wraps under the search */
.printpick .ppsearch{flex-wrap:wrap}
.printpick .ppsearch input{flex-basis:10rem}
.printpick .ppsearch .sizeseg{flex:none}
.printpick .ppfilters{display:grid;grid-template-columns:repeat(4,minmax(0,1fr)) auto;gap:.5rem;
  align-items:center;min-width:0}
.printpick .ppfilters select,.printpick .ppfilters .msel-btn{width:100%;min-width:0;margin:0}
.printpick .ppall{margin:0;font-size:.93rem;line-height:1.3}
.printpick .ppstatus{margin:0;font-size:.93rem;color:var(--text-muted);flex:none;display:flex;flex-wrap:wrap;
  align-items:center;gap:.5rem}
.printpick .ppstatus.error{color:var(--text)}
.printpick .ppstatus .btn{margin:0}
.printpick .ppmain{flex:1 1 auto;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 21rem;
  gap:1rem}
.printpick .ppscroll{overflow:auto;min-height:0;overscroll-behavior:contain;padding:3px}
.printpick .ppgrid{list-style:none;margin:0;padding:0;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.75rem}
.printpick .ppgrid li,.addprints .pgrid li{min-width:0;display:flex}
.pptile{position:relative;display:flex;flex-direction:column;align-items:stretch;justify-content:flex-start;
  gap:.4rem;width:100%;height:auto;min-width:0;margin:0;padding:.4rem;border:2px solid var(--border-soft);
  border-radius:8px;background:var(--surface-2);color:var(--text);text-align:left;font-weight:400;
  cursor:pointer}
.pptile:hover{border-color:var(--link);background:var(--surface-2);color:var(--text)}
.pptile:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.pptile.current,.pptile.picked{border-color:var(--orange)}
.pptile.on{border-color:var(--link);box-shadow:0 0 0 2px var(--link)}
.pptile .ppimg{display:block;width:100%;aspect-ratio:5/7;border-radius:4.5%;overflow:hidden;
  background:var(--surface-3)}
.pptile .ppimg img{display:block;width:100%;height:100%;object-fit:cover}
.pptile .ph,.pppreview .ph{display:flex;align-items:center;justify-content:center;width:100%;height:100%;
  padding:.5rem;text-align:center;font-weight:700;color:var(--text-muted);overflow-wrap:anywhere}
.pptile .ppcap{display:flex;flex-direction:column;gap:.2rem;min-width:0}
.pptile .ppsetname{font-weight:700;font-size:.9rem;line-height:1.25;overflow-wrap:anywhere;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.pptile .ppcode{font-size:.82rem;color:var(--text-muted);font-variant-numeric:tabular-nums;
  overflow-wrap:anywhere}
.pptile .ppprice{font-weight:700;font-size:.9rem;font-variant-numeric:tabular-nums}
.pptile .ppprice.none{font-weight:400;color:var(--text-muted)}
.pptile .ppmark{position:absolute;top:.7rem;left:.7rem;padding:.1rem .55rem;border-radius:1rem;
  background:var(--orange);color:var(--on-orange);font-size:.78rem;font-weight:700;box-shadow:var(--shadow)}
.ppchips{display:flex;flex-wrap:wrap;gap:.25rem}
.ppchip{display:inline-block;padding:.05rem .45rem;border-radius:1rem;border:1px solid var(--border);
  background:var(--surface);color:var(--text);font-size:.76rem;line-height:1.45}
.ppchip.ppflag{border-color:var(--orange);color:var(--orange-text);font-weight:700}
.ppchip.ppmine{border-color:var(--orange);font-weight:700}
.pptile.compact{padding:.2rem;gap:.15rem;border-radius:6px;border-color:transparent}
.pptile.compact.current{border-color:var(--orange)}
.pptile.compact .ppcode{font-size:.7rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  text-align:center}
.printpick .ppempty{display:flex;flex-direction:column;align-items:flex-start;gap:.5rem;padding:1rem 0}
.printpick .ppempty p{margin:0}
.printpick .ppempty .btn{margin:0}
.printpick .ppempty[hidden],.printpick .ppmore[hidden]{display:none}
.printpick .ppmore{display:flex;justify-content:center;padding:1rem 0 .5rem}
.printpick .ppmore .btn{margin:0}
.printpick .pppreview{min-height:0;overflow:auto;overscroll-behavior:contain;display:flex;
  flex-direction:column;
  gap:.75rem;border-left:1px solid var(--border-soft);padding:0 .25rem 0 1rem}
.pppreview .ppbig{width:100%;max-width:18rem;align-self:center;flex:none}
.pppreview .ppbig img,.pppreview .ppbig .ph{display:flex;width:100%;aspect-ratio:5/7;border-radius:4.5%;
  object-fit:cover;background:var(--surface-2)}
.pppreview .ppinfo{display:flex;flex-direction:column;gap:.4rem;min-width:0}
.pppreview h3{margin:0;font-size:1.15rem;line-height:1.25;overflow-wrap:anywhere}
.pppreview .ppline{margin:0;color:var(--text-muted);font-size:.9rem;font-variant-numeric:tabular-nums}
.pppreview .ppprices{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:.2rem .9rem;
  margin:.25rem 0 0;font-variant-numeric:tabular-nums}
.pppreview .ppprices dt{color:var(--text-muted);font-weight:700}
.pppreview .ppprices dd{margin:0}
.pppreview .ppnote{margin:.25rem 0 0;padding:.5rem .65rem;border-left:3px solid var(--orange);
  background:var(--surface-2);font-size:.9rem;color:var(--text)}
.pppreview .ppacts{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.5rem}
.pppreview .ppacts button{margin:0;flex:1 1 auto}
.pppreview .ppback{display:none}
.pppreview .pphint{margin:0}
@media (max-width:899.98px){
  .printpick .ppmain{grid-template-columns:minmax(0,1fr);position:relative}
  /* the preview covers the list (the heading and its Close button stay); the search and filters
     step aside meanwhile */
  .printpick .pppreview{display:none;position:absolute;inset:0;z-index:2;background:var(--surface);
    border:0;border-radius:0;padding:.25rem .25rem env(safe-area-inset-bottom)}
  .printpick .ppbox.previewing .pppreview{display:flex}
  .printpick .ppbox.previewing .pptools,.printpick .ppbox.previewing .ppstatus{display:none}
  .printpick .pppreview .ppback{display:inline-flex}
  .pppreview .ppbig{max-width:min(20rem,72%)}
  .printpick .ppfilters{grid-template-columns:repeat(2,minmax(0,1fr))}
  .printpick .ppall{grid-column:1 / -1} }
@media (max-width:700px){
  .printpick{padding:0;align-items:flex-end;-webkit-backdrop-filter:none;backdrop-filter:none}
  .printpick .ppbox{width:100%;height:94vh;height:94dvh;
    border-radius:var(--radius-panel) var(--radius-panel) 0 0;
    border-bottom:0;padding:.75rem .75rem calc(.75rem + env(safe-area-inset-bottom))}
  .printpick .ppgrid{grid-template-columns:repeat(auto-fill,minmax(138px,1fr));gap:.5rem} }
@media (max-width:599.98px){
  .printpick .ppfiltbtn{display:inline-flex}
  .printpick .ppfilters:not(.open){display:none} }
/* finger-sized on a touch screen (44 px) */
@media (pointer:coarse){
  /* the stats panel's buttons that pick out cards (colours, types, keywords), the curve's axis
     buttons, the legend and the dialog strip's buttons: 44 px, set here, after their own rules
     above, so the touch size wins */
  .rowbtn,.curveaxis .mv,.slegend button{min-height:44px}
  .statctl button{min-height:44px}
  /* the curve's axis buttons share the box's width (a phone's 320 px holds no eight 44 px
     columns without a sideways scroll): 44 px high, as wide as the box lets them; the stack
     handle keeps its 28 px drawing inside the 44 px finger area its ::before draws */
  .deck-toast .acts button,.deck-toast .acts .btn{min-height:44px}
  .deck-toast .close{width:44px;height:44px;min-height:44px}
  .printpick .close{width:44px;min-height:44px;height:44px}
  .printpick input[type=search],.printpick .msel-btn,.printpick select{height:44px;min-height:44px}
  .printpick .ppfiltbtn,.printpick .ppacts button,.printpick .ppmore .btn,.printpick .ppempty .btn,
  .printpick .ppstatus .btn{min-height:44px}
  .printpick .ppall{min-height:44px} }
/* Compact width: the save bar sits fixed on top of the bottom tab bar. The same breakpoint as the
   tab bar (theme.py's __MOBILE__, under 600px): at exactly 600px the rail or the top bar is
   showing and the bar stays sticky at the top, so nothing is left under a fixed bar with no room
   kept for it (gate 0.7.10: the footer links at 600px). The footer keeps room for both bars.
   One slim row: the count is in the review button, the backup tick's text is smaller. */
@media (max-width:599.98px){
  .editbar{top:auto;bottom:calc(56px + env(safe-area-inset-bottom));margin:0;position:fixed;left:0;right:0;
    padding:.35rem max(.75rem,env(safe-area-inset-right)) .35rem max(.75rem,env(safe-area-inset-left));
    gap:.4rem;flex-wrap:nowrap}
  .editbar .count{display:none}
  /* Undo and Redo keep their names for screen readers; on a phone the arrows alone show */
  .editbar .undo .bl,.editbar .redo .bl{position:absolute;width:1px;height:1px;overflow:hidden;
    clip:rect(0 0 0 0);white-space:nowrap}
  .editbar .undo,.editbar .redo{padding-inline:.6rem;flex:none}
  .editbar .backup{font-size:.8rem;white-space:normal;flex:1 1 6rem;min-width:0}
  .editbar .status{flex-basis:100%}
  .editor{padding-bottom:6rem}
  body:has(#editor) footer.site{padding-bottom:calc(11rem + env(safe-area-inset-bottom))}
  .editbar .review{flex:1}
  /* the card size keeps to the one slim row: no label, narrow S M L */
  .editbar .cardsize{flex:0 1 auto;min-width:0;margin:0}
  .editbar .cardsize .lbl{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
  .editbar .cardsize .sizeseg{display:flex;max-width:100%}
  .editbar .cardsize .sizeseg button{min-width:0;flex:1 1 2rem;padding:0 .4rem}
  .addbox form.addcard{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
  .addbox form.addcard .grow{grid-column:1 / -1;grid-row:1}
  .addbox form.addcard .go{grid-column:1;grid-row:2} .addbox form.addcard .go button{width:100%}
  .addbox form.addcard .field:not(.grow):not(.go){grid-row:auto;grid-column:auto}
  .erow{grid-template-columns:40px minmax(0,1fr) auto;grid-template-rows:auto auto}
  .erow .sel{grid-column:2;grid-row:2}
  .erow details.dd,.erow button.remove{grid-column:3;grid-row:2;justify-self:end}
}
/* A card row also stacks by the room its list has, so large text (a phone's font scale, 200%
   zoom) stacks it on a wider screen too: the category and menu under the name, then, in a still
   narrower list, the count on a row of its own. The widths are in rem, so they grow with the
   text; at the normal text size neither applies where the rule above does not. */
ul.erows{container-type:inline-size}
@container (max-width:30rem){
  .erow{grid-template-columns:40px minmax(0,1fr) auto;grid-template-rows:auto auto}
  .erow .sel{grid-column:2;grid-row:2}
  .erow details.dd,.erow button.remove{grid-column:3;grid-row:2;justify-self:end}
}
@container (max-width:20rem){
  .erow .qty{grid-column:2 / -1;grid-row:2}
  .erow .sel{grid-row:3}
  .erow details.dd,.erow button.remove{grid-row:3}
}
/* The phone's one-row save bar wraps with large text rather than run off the screen. */
.editor{container-type:inline-size}
@container (max-width:17rem){ .editbar{flex-wrap:wrap} .editbar .review{flex:1 1 auto} }
"""
    + DECK_CSS_A
    + DECK_CSS_C
)

__all__ = [
    "DECK_CSS",
    "GROUPS",
    "SORTS",
    "VIEWS",
    "card_image",
    "covers_for",
    "deck_list_controls_html",
    "deck_list_html",
    "deck_page_html",
    "featured",
    "group_cards",
    "image_url",
    "mana_html",
]
