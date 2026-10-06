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
import html
import re
import time
from collections import Counter
from typing import Any

from .archidekt import Deck, DeckCard
from .deck_stats import WUBRG, colour_letter, is_land, mana_pips
from .theme import icon
from .views import cards_by_category

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SYMBOL = re.compile(r"\{([^{}]+)\}")

VIEWS = {"text": "Text", "stacks": "Stacks", "grid": "Grid"}
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
TYPE_ORDER = [
    "Creature",
    "Planeswalker",
    "Battle",
    "Instant",
    "Sorcery",
    "Artifact",
    "Enchantment",
    "Land",
]
RARITY_ORDER = {"mythic": 0, "rare": 1, "uncommon": 2, "common": 3, "special": 4}


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def image_url(uid: str | None, size: str = "normal") -> str | None:
    """A Scryfall card image by Scryfall id (the scheme Scryfall's own image URIs use)."""
    if not uid or not _UUID.fullmatch(uid):
        return None
    return f"https://cards.scryfall.io/{size}/front/{uid[0]}/{uid[1]}/{uid}.jpg"


def card_image(card: DeckCard, size: str = "normal") -> str | None:
    return image_url(getattr(card, "scryfall_uid", "") or None, size)


def mana_html(cost: str) -> str:
    """Mana pips as our own CSS circles: {2}{G}{U} -> three pips. Hybrid pips show both letters."""
    if not cost:
        return ""
    out = []
    for sym in _SYMBOL.findall(cost):
        parts = [p for p in sym.upper().split("/") if p != "P"]
        letters = [colour_letter(p) for p in parts]
        colours = [c for c in letters if c]
        if len(colours) >= 2:
            out.append(
                f"<i class='pip pip-{colours[0]} hy' data-b='{colours[1]}'>"
                f"<span class='sr-only'>{esc(sym)}</span></i>"
            )
        elif colours:
            out.append(f"<i class='pip pip-{colours[0]}'><span class='sr-only'>{esc(sym)}</span></i>")
        elif parts and parts[0] in ("C",):
            out.append("<i class='pip pip-C'><span class='sr-only'>colorless</span></i>")
        else:
            out.append(f"<i class='pip pip-g'>{esc(parts[0] if parts else sym)}</i>")
    return "<span class='mana' aria-label='mana cost'>" + "".join(out) + "</span>"


def money(value: float | None) -> str:
    return "–" if value is None else f"${value:,.2f}"


def ago(iso: str) -> str:
    """'15 hrs ago' the way Archidekt words it, from an ISO timestamp; '' when unreadable."""
    if not iso:
        return ""
    try:
        then = calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return ""
    secs = max(0, int(time.time() - then))
    for unit, name in (
        (86400 * 365, "yr"),
        (86400 * 30, "mo"),
        (86400 * 7, "wk"),
        (86400, "day"),
        (3600, "hr"),
    ):
        if secs >= unit:
            n = secs // unit
            return f"{n} {name}{'s' if n != 1 else ''} ago"
    if secs >= 60:
        n = secs // 60
        return f"{n} min{'s' if n != 1 else ''} ago"
    return "just now"


def primary_type(card: DeckCard) -> str:
    for t in TYPE_ORDER:
        if t in card.types:
            return t
    return card.types[0] if card.types else "Other"


def colour_group(card: DeckCard) -> str:
    cols = [c for c in WUBRG if c in {colour_letter(x) for x in card.colors}]
    if not cols:
        return "Colorless" if not is_land(card) else "Land"
    return "Multicolor" if len(cols) > 1 else COLOUR_NAMES[cols[0]]


def mv_group(card: DeckCard) -> str:
    if is_land(card):
        return "Land"
    if card.cmc is None:
        return "No mana value"
    return f"{int(card.cmc) if card.cmc == int(card.cmc) else card.cmc} mana value"


def _colour_rank(card: DeckCard) -> tuple[int, str]:
    cols = [c for c in WUBRG if c in {colour_letter(x) for x in card.colors}]
    if not cols:
        return (6 if not is_land(card) else 7, card.name.lower())
    return (WUBRG.index(cols[0]) if len(cols) == 1 else 5, card.name.lower())


def sort_cards(cards: list[DeckCard], key: str) -> list[DeckCard]:
    if key == "mv":
        return sorted(cards, key=lambda c: (is_land(c), c.cmc if c.cmc is not None else 99, c.name.lower()))
    if key == "type":
        return sorted(
            cards,
            key=lambda c: (
                primary_type(c) not in TYPE_ORDER,
                TYPE_ORDER.index(primary_type(c)) if primary_type(c) in TYPE_ORDER else 99,
                c.name.lower(),
            ),
        )
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
    """The card whose art fronts the deck: the first commander, else the first card with art."""
    for c in deck.cards:
        if "Commander" in c.categories and card_image(c):
            return c
    for c in deck.cards:
        if card_image(c):
            return c
    return None


def banner_html(
    deck: Deck,
    stats: dict[str, Any] | None,
    *,
    own: bool,
    csrf: str | None,
    writes_enabled: bool,
) -> str:
    stats = stats or {}
    art = featured(deck)
    art_url = card_image(art, "art_crop") if art else None
    style = f" style=\"background-image:url('{esc(art_url)}')\"" if art_url else ""
    bracket = stats.get("bracket_estimate") or {}
    bracket_names = {1: "Exhibition (1)", 2: "Core (2)", 3: "Upgraded (3)", 4: "Optimized (4)", 5: "cEDH (5)"}
    legal_problems = stats.get("legality_problems") or []
    legality = (
        f"<span class='legal ok' title='Legal in {esc(deck.format or 'this format')}'>"
        f"{icon('check')} Legality</span>"
        if deck.format and not legal_problems
        else f"<span class='legal bad' title='{len(legal_problems)} problem(s)'>{icon('x')} Legality</span>"
        if deck.format
        else ""
    )
    privacy = (
        f"<span class='privacy' title='Private deck'>{icon('eye-off')}</span>"
        if deck.private
        else f"<span class='privacy' title='Unlisted deck'>{icon('eye-off')}</span>"
        if deck.unlisted
        else ""
    )
    est_bracket = (
        f"<span>Est Bracket: {esc(bracket_names.get(bracket.get('bracket'), bracket.get('bracket')))}</span>"
        if bracket.get("bracket")
        else ""
    )
    own_bracket = (
        f"<span>Bracket: {esc(bracket_names.get(deck.edh_bracket, deck.edh_bracket))}</span>"
        if deck.edh_bracket
        else ""
    )
    salt = stats.get("salt_total") if stats.get("salt_total") is not None else "–"
    tags = (
        "".join(f"<span class='pill'>{esc(t)}</span>" for t in deck.tags)
        if deck.tags
        else "<span class='notags'>No deck tags</span>"
    )
    csrf_in = f"<input type='hidden' name='csrf' value='{esc(csrf)}'>" if csrf else ""
    did = esc(deck.id)
    primary = ""
    if own and csrf:
        primary += f"<a class='btn btn-primary' href='/decks/{did}/edit'>{icon('edit')} Edit deck</a>"
        primary += (
            f"<form method='post' action='/decks/{did}/clone' class='inline'>{csrf_in}"
            f"<button type='submit'>{icon('clone')} Clone deck</button></form>"
        )
    more_items = []
    if own and csrf:
        more_items.append(f"<a href='/decks/{did}/settings'>{icon('settings')} Deck settings</a>")
    more_items.append(f"<a href='/decks/{did}/export'>{icon('download')} Export deck</a>")
    more_items.append(f"<a href='/history?deck_id={did}'>{icon('history')} History and snapshots</a>")
    if csrf:
        more_items.append(
            f"<form method='post' action='/decks/{did}/report'>{csrf_in}"
            f"<button type='submit'>{icon('report')} Run deck report</button></form>"
        )
    more_items.append(f"<a href='/decks/{did}#stats'>{icon('stats')} Deck stats</a>")
    more_items.append(
        f"<a href='https://archidekt.com/decks/{did}' target='_blank' rel='noreferrer noopener'>"
        f"{icon('external')} Open on Archidekt</a>"
    )
    more = (
        f"<details class='dd'><summary class='btn'>{icon('more')} More</summary>"
        f"<div class='menu left'>{''.join(more_items)}</div></details>"
    )
    writes_note = (
        "<p class='small notags'>Deck writes are switched off on this gateway; edits can be proposed "
        "and reviewed but not applied.</p>"
        if own and not writes_enabled
        else ""
    )
    return (
        f"<section class='banner'{style}><div class='shade'><div class='content'>"
        f"<div class='strip'{style}></div>"
        "<div class='info'>"
        f"<h1 class='deckname'>{privacy}<span>{esc(deck.name)}</span></h1>"
        "<div class='row'>"
        + (f"<span>{esc(ago(deck.updated_at))}</span>" if ago(deck.updated_at) else "")
        + f"<span>{stats.get('distinct', 0)} distinct cards</span>"
        "</div><div class='row'>"
        f"<span>{esc((deck.format or 'custom').capitalize())}</span>{legality}{own_bracket}{est_bracket}"
        "</div><div class='row'>"
        f"<span>Size: {stats.get('card_count', sum(c.quantity for c in deck.main_cards))}</span>"
        f"<span>Est cost: <b class='orange'>{money(stats.get('price_total'))}</b></span>"
        f"<span>Salt sum: <b class='orange'>{esc(salt)}</b></span>"
        "</div>"
        f"<div class='tags'>{icon('tag')} {tags}</div>"
        f"<div class='controls'><div class='primary'>{primary}{more}</div></div>{writes_note}"
        "</div>"
        f"<a class='owner' href='https://archidekt.com/u/{esc(deck.owner)}' target='_blank' "
        "rel='noreferrer noopener'>"
        f"{avatar_html(deck.owner)}<span class='uname'>{esc(deck.owner or 'unknown')}</span></a>"
        "</div></div></section>"
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
    did = esc(deck.id)
    add = (
        "<div class='field add'><label for='quick'>Add card</label>"
        f"<form method='get' action='/decks/{did}/edit' class='quick'>"
        "<input id='quick' type='text' name='add' placeholder='Quick add (card name)' list='cardnames' "
        "autocomplete='off'><datalist id='cardnames'></datalist>"
        f"<button type='submit' class='primary'>{icon('search')} Card search</button></form></div>"
        if own
        else ""
    )
    return (
        "<section class='toolbar panel'>"
        f"<form method='get' action='/decks/{did}' class='controls' id='viewform'>{add}"
        "<div class='views'>"
        + _select("view", VIEWS, view, "View as", "layers")
        + _select("group", GROUPS, group, "Group by", "grid")
        + _select("sort", SORTS, sort, "Sort by", "sort")
        + "</div>"
        "<div class='field filter'><label for='q'>Local filter</label><span class='search'>"
        f"<input id='q' type='search' name='q' value='{esc(q)}' placeholder='Filter deck (eg: Sol Ring)' "
        "autocomplete='off'>"
        f"<button type='submit' aria-label='Apply filter'>{icon('search')}</button></span></div>"
        "<noscript><button type='submit' class='apply'>Apply</button></noscript>"
        "</form></section>"
    )


def _finish_badge(card: DeckCard) -> str:
    if card.modifier and card.modifier != "Normal":
        return f"<span class='finish' title='{esc(card.modifier)}'>{esc(card.modifier[:1])}</span>"
    return ""


def _label_dot(card: DeckCard) -> str:
    if card.label:
        colour = card.label.split(",")[-1].strip() if "," in card.label else ""
        style = (
            f" style='background:{esc(colour)}'" if re.fullmatch(r"#[0-9a-fA-F]{3,8}", colour or "") else ""
        )
        return f"<span class='tagdot'{style} title='{esc(card.label.split(',')[0])}'></span>"
    return ""


def text_row(card: DeckCard, *, deck: Deck) -> str:
    img = card_image(card)
    hover = f"<span class='hover'><img src='{esc(img)}' alt='' loading='lazy'></span>" if img else ""
    cls = " side" if not deck.in_deck(card) else ""
    return (
        f"<li class='row{cls}' data-name='{esc(card.name.lower())}'>"
        f"<span class='q'>{card.quantity}</span>"
        f"<span class='n'>{_label_dot(card)}<span class='name'>{esc(card.name)}</span>{_finish_badge(card)}"
        f"{hover}</span>"
        f"<span class='mc'>{mana_html(card.mana_cost)}</span>"
        f"<span class='set' title='{esc(card.set_code.upper())} {esc(card.collector_number)}'>"
        f"{esc(card.set_code.upper())}</span>"
        f"<span class='price'>{money(card.price) if card.price is not None else ''}</span>"
        "</li>"
    )


def image_card(card: DeckCard) -> str:
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
        f"<div class='c' data-name='{esc(card.name.lower())}' title='{esc(card.name)}'>{body}{qty}{extra}"
        f"{_finish_badge(card)}</div>"
    )


def cards_html(deck: Deck, *, view: str, group: str, sort: str, q: str, own: bool) -> str:
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
            f"<details class='dd'><summary class='icon-only btn-ghost' aria-label='Category options'>"
            f"{icon('more')}</summary><div class='menu'>"
            f"<a href='/decks/{did}/edit#cat-{esc(name)}'>{icon('edit')} Edit cards in {esc(name)}</a>"
            f"<a href='/decks/{did}/settings#categories'>{icon('settings')} Category options</a>"
            "</div></details>"
            if own and group == "category"
            else ""
        )
        head = (
            f"<div class='stackhead'><h4><span class='title{strike}'>{esc(name)}</span>{menu}</h4>"
            f"<div class='meta'>Qty: {qty}" + (f" · Price: {money(price)}" if price else "") + "</div></div>"
        )
        if view == "text":
            body = "<ul class='plain rows'>" + "".join(text_row(c, deck=deck) for c in cards) + "</ul>"
        else:
            body = "<div class='cards'>" + "".join(image_card(c) for c in cards) + "</div>"
        sections.append(f"<section class='stack' data-group='{esc(name)}'>{head}{body}</section>")
    return f"<div class='deckview {esc(view)}' id='cards'>{''.join(sections)}</div>"


def _bar(parts: dict[str, float], *, label: str) -> str:
    total = sum(v for v in parts.values() if v) or 0
    if not total:
        return f"<div class='cbar empty' aria-label='{esc(label)}'></div>"
    segs = "".join(
        f"<span class='seg seg-{k}' style='width:{100 * v / total:.1f}%' "
        f"title='{COLOUR_NAMES.get(k, k)}: {v:g}'></span>"
        for k, v in parts.items()
        if v
    )
    return f"<div class='cbar' role='img' aria-label='{esc(label)}'>{segs}</div>"


def stats_panel_html(deck: Deck, stats: dict[str, Any] | None) -> str:
    if not stats:
        return ""
    pips = {k: float(v) for k, v in (stats.get("colour_pips") or {}).items()}
    sources = {k: float(v) for k, v in (stats.get("mana_sources") or {}).items() if k in WUBRG}
    cards = deck.main_cards
    cost_cards = Counter()
    prod_cards = Counter()
    for c in cards:
        for col in mana_pips(c.mana_cost):
            cost_cards[col] += c.quantity
        for col in c.mana_production or {}:
            if col in WUBRG:
                prod_cards[col] += c.quantity
    pip_total = sum(pips.values()) or 1
    src_total = sum(sources.values()) or 1
    colour_cards = "".join(
        "<div class='ccard'>"
        f"<div class='cname'><i class='pip pip-{col}'></i> {COLOUR_NAMES[col]}</div>"
        f"<div class='lbl'>Cost</div><div class='pbar'><span class='fill seg-{col}' "
        f"style='width:{100 * pips.get(col, 0) / pip_total:.0f}%'></span>"
        f"<b>{100 * pips.get(col, 0) / pip_total:.0f}%</b></div>"
        f"<div class='sub'>{pips.get(col, 0):g} pips - {cost_cards.get(col, 0)} cards</div>"
        f"<div class='lbl'>Production</div><div class='pbar'><span class='fill seg-{col}' "
        f"style='width:{100 * sources.get(col, 0) / src_total:.0f}%'></span>"
        f"<b>{100 * sources.get(col, 0) / src_total:.0f}%</b></div>"
        f"<div class='sub'>{sources.get(col, 0):g} mana - {prod_cards.get(col, 0)} cards</div>"
        "</div>"
        for col in WUBRG
        if pips.get(col) or sources.get(col)
    )
    curve = stats.get("mana_curve") or {}
    top = max([int(v or 0) for v in curve.values()] + [1])
    bars = "".join(
        f"<div class='bar'><b>{int(v or 0)}</b>"
        f"<span style='height:{max(2, round(100 * int(v or 0) / top))}%'></span>"
        f"<em>{esc(k.replace('7+', '7+'))}</em></div>"
        for k, v in curve.items()
    )
    mv_total = sum(c.cmc * c.quantity for c in cards if c.cmc is not None and not is_land(c))
    types = stats.get("type_counts") or {}
    rarities = stats.get("rarity_counts") or {}
    type_rows = "".join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in types.items())
    rarity_rows = "".join(f"<tr><td>{esc(k.capitalize())}</td><td>{v}</td></tr>" for k, v in rarities.items())
    problems = stats.get("legality_problems") or []
    problems_html = (
        "<div class='legality'><h3>Legality</h3><ul>"
        + "".join(
            f"<li><b>{esc(p.get('name') if isinstance(p, dict) else p)}</b> "
            f"<span class='muted'>{esc(p.get('status', '') if isinstance(p, dict) else '')}</span></li>"
            for p in problems[:30]
        )
        + (f"<li class='muted'>…and {len(problems) - 30} more</li>" if len(problems) > 30 else "")
        + "</ul></div>"
        if problems
        else (
            f"<div class='legality'><h3>Legality</h3><p class='ok'>{icon('check')} Legal in "
            f"{esc(deck.format.capitalize())}</p></div>"
            if deck.format
            else ""
        )
    )
    bracket = stats.get("bracket_estimate") or {}
    basis = bracket.get("basis") or []
    avg_mv = stats.get("average_mana_value") if stats.get("average_mana_value") is not None else "–"
    no_types = "<tr><td class=muted>No type data</td></tr>"
    bracket_html = (
        "<div class='bracket'><h3>Commander bracket estimate</h3>"
        f"<p><b class='orange'>Bracket {esc(bracket.get('bracket'))}</b>"
        + (" · " + "; ".join(esc(b) for b in basis) if basis else "")
        + "</p>"
        f"<p class='small muted'>Game changers: {len(stats.get('game_changers') or [])} · Tutors: "
        f"{stats.get('tutors', 0)} · Extra turns: {stats.get('extra_turns', 0)} · Mass land denial: "
        f"{stats.get('mass_land_denial', 0)}. From Archidekt's card flags only; an estimate, not a "
        "ruling.</p>"
        "</div>"
        if bracket.get("bracket")
        else ""
    )
    return (
        "<section class='panel stats' id='stats'><div class='head'><h2>Deck stats</h2></div>"
        "<div class='grid'><div>"
        f"<div class='lbl'>Cost</div>{_bar(pips, label='Mana cost by colour')}"
        f"<div class='lbl'>Production</div>{_bar(sources, label='Mana production by colour')}"
        f"<div class='ccards'>{colour_cards}</div>"
        f"<p class='avg'><b>Avg Mana Value: {esc(avg_mv)}</b>"
        f"<br><span class='small'>Total Mana Value: {mv_total:.2f}</span></p>"
        f"<div class='curve' role='img' aria-label='Mana curve'>{bars}</div>"
        "</div><div class='side'>"
        f"<div class='tiles'><div class='tile'><b>{stats.get('card_count', 0)}</b>"
        "<span>Deck size</span></div>"
        f"<div class='tile'><b>{stats.get('land_count', 0)}</b><span>Lands</span></div>"
        f"<div class='tile'><b>{money(stats.get('price_total'))}</b><span>Est cost</span></div>"
        f"<div class='tile'><b>{stats.get('priced_cards', 0)}</b><span>Priced cards</span></div></div>"
        f"<h3>Quantity of types</h3><table class='qty'><tbody>{type_rows or no_types}</tbody></table>"
        + (f"<h3>Rarity</h3><table class='qty'><tbody>{rarity_rows}</tbody></table>" if rarity_rows else "")
        + problems_html
        + bracket_html
        + "</div></div></section>"
    )


def description_html(deck: Deck) -> str:
    text = (getattr(deck, "description", "") or "").strip()
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
) -> str:
    view = view if view in VIEWS else "text"
    group = group if group in GROUPS else "category"
    sort = sort if sort in SORTS else "name"
    return (
        banner_html(deck, stats, own=own, csrf=csrf, writes_enabled=writes_enabled)
        + notice
        + toolbar_html(deck, own=own, view=view, group=group, sort=sort, q=q)
        + cards_html(deck, view=view, group=group, sort=sort, q=q, own=own)
        + stats_panel_html(deck, stats)
        + description_html(deck)
    )


# -- deck list ------------------------------------------------------------------------------------

LIST_ORDERS = {"updated": "Updated at", "created": "Created at", "name": "Name", "format": "Deck format"}


def colour_bar_html(colors: dict[str, Any] | None) -> str:
    parts = {k: float(v) for k, v in (colors or {}).items() if k in WUBRG and v}
    return _bar(parts, label="Colour identity") if parts else "<div class='cbar empty'></div>"


def deck_card_html(d: dict[str, Any], *, cover: dict[str, Any] | None, selected: bool) -> str:
    did = esc(d["id"])
    art = image_url((cover or {}).get("scryfall_uid"), "art_crop")
    style = f" style=\"background-image:url('{esc(art)}')\"" if art else ""
    fmt = (d.get("format_name") or "").capitalize() if d.get("format_name") else ""
    if not fmt and isinstance(d.get("format"), str):
        fmt = d["format"].capitalize()
    bracket = d.get("bracket")
    bracket_names = {1: "Exhibition (1)", 2: "Core (2)", 3: "Upgraded (3)", 4: "Optimized (4)", 5: "cEDH (5)"}
    line = fmt or "Custom"
    if bracket:
        line += f" - Bracket: {bracket_names.get(bracket, bracket)}"
    tags = d.get("tags") or []
    tags_html = (
        "".join(f"<span class='pill'>{esc(t)}</span>" for t in tags[:6])
        if tags
        else "<span class='notags'>No deck tags</span>"
    )
    initial = esc((d.get("name") or "?")[:1].upper())
    size = d.get("size")
    return (
        f"<li class='deck{' selected' if selected else ''}'><a href='/decks/{did}'>"
        f"<span class='thumb{' noart' if not art else ''}'{style}><span class='ini'>{initial}</span>"
        + (f"<span class='views'>{size} cards</span>" if size else "")
        + colour_bar_html(d.get("colors"))
        + "</span>"
        "<span class='info'>"
        f"{avatar_html(d.get('owner') or '', 'sm')}"
        f"<span class='text'><span class='name'>{esc(d.get('name'))}</span>"
        f"<span class='fmt'>{esc(line)}</span>"
        f"<span class='sub'>{esc(d.get('folder') or '')}{' · ' if d.get('folder') else ''}"
        f"{esc(ago(d.get('updated_at') or ''))}</span>"
        "</span></span>"
        f"<span class='tags'>{tags_html}</span></a></li>"
    )


def deck_list_html(
    decks: list[dict[str, Any]],
    *,
    covers: dict[str, dict[str, Any]] | None = None,
    selected: str | None = None,
    q: str = "",
    view: str = "grid",
) -> str:
    if not decks:
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
        )
        for d in decks
    )
    return f"<ul class='plain decklist {esc(view)}'>{items}</ul>"


def deck_list_controls_html(
    *, q: str, order: str, view: str, folders: list[str], folder: str, total: int
) -> str:
    folder_opts = "".join(
        f"<option value='{esc(f)}'{' selected' if f == folder else ''}>{esc(f)}</option>" for f in folders
    )
    return (
        "<section class='panel listbar'><form method='get' action='/decks' class='controls' id='listform'>"
        "<div class='field grow'><label for='q'>Filter deck name</label><span class='search'>"
        f"<input id='q' type='search' name='q' value='{esc(q)}' placeholder='Example deck name'>"
        f"<button type='submit' aria-label='Filter'>{icon('search')}</button></span></div>"
        + _select("view", {"grid": "Grid", "list": "List"}, view, "View as", "grid")
        + _select("order", LIST_ORDERS, order, "Order by", "sort")
        + (
            "<div class='field'><label for='f-folder'>Folder</label><span class='sel'>"
            f"{icon('decks')}<select id='f-folder' name='folder'><option value=''>All folders</option>"
            f"{folder_opts}"
            "</select></span></div>"
            if folders
            else ""
        )
        + f"<div class='field'><span class='lbl'>Total decks: {total}</span>"
        f"<a class='btn btn-primary' href='/decks/new'>{icon('plus')} New deck</a></div>"
        "<noscript><button type='submit' class='apply'>Apply</button></noscript>"
        "</form></section>"
    )


DECK_CSS = """
/* deck banner (cardBanner / deckHeaderInfo) */
.banner{position:relative;margin:0 -1rem 1rem;background:var(--surface-2) center 30%/cover no-repeat;
  color:#fff;overflow:hidden}
.banner .shade{background:linear-gradient(112deg,var(--banner-a),var(--banner-a) 25%,var(--banner-b) 76%,
  var(--banner-a));backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);padding:1rem}
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
.banner .owner{display:flex;flex-direction:column;align-items:center;gap:.35rem;color:#fff;
  text-decoration:none;font-weight:700;max-width:160px;flex:none}
.banner .owner:hover{color:var(--orange)}
.banner .strip{display:none}
.banner .owner .uname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:160px}
.avatar{display:inline-flex;align-items:center;justify-content:center;border-radius:50%;
  background:var(--surface-3);color:#fff;font-weight:900;border:1px solid var(--border);flex:none}
.avatar.lg{width:50px;height:50px;font-size:1.4rem} .avatar.sm{width:40px;height:40px;font-size:1.1rem}
.avatar.xl{width:80px;height:80px;font-size:2rem}
@media (max-width:600px){
  .banner .shade{padding:0}
  .banner .content{flex-direction:column;gap:0}
  .banner .strip{display:block;height:110px;background:var(--surface-2) center 30%/cover no-repeat}
  .banner .info{padding:0 1rem 1rem}
  .banner h1.deckname{font-size:24px;margin-top:.5rem}
  .banner .row{font-size:13px}
  .banner .owner{flex-direction:row;justify-content:flex-end;max-width:none;padding:.5rem 1rem 0;order:-1}
  .banner .owner .avatar{width:80px;height:80px;font-size:2rem;margin-right:auto;margin-top:-40px;
    border-width:3px;border-color:var(--bg)}
  .banner .controls .primary,.banner .controls .primary .btn,.banner .controls .primary button,
  .banner .controls details.dd{width:100%}
}

/* toolbar panel (filterBar) */
.toolbar{padding:.5rem 1rem 1rem}
.toolbar .controls{display:grid;grid-template-columns:auto 1fr auto;gap:1rem;align-items:end}
.toolbar .views{display:grid;grid-template-columns:repeat(3,minmax(140px,1fr));gap:.5rem 1rem}
.field .sel{position:relative;display:block}
.field .sel > svg{position:absolute;left:.75rem;top:50%;transform:translateY(-50%);color:var(--orange);
  pointer-events:none}
.field .sel select{padding-left:2.1rem}
.field .search{margin:0}
.toolbar .quick{display:flex;gap:.5rem}
.toolbar .quick input{min-width:12rem}
.toolbar .apply{margin-top:.5rem}
@media (max-width:1000px){ .toolbar .controls{grid-template-columns:1fr 1fr}
  .toolbar .filter{grid-column:1 / -1} }
@media (max-width:600px){ .toolbar .controls{grid-template-columns:1fr}
  .toolbar .views{grid-template-columns:1fr 1fr}
  .toolbar .quick{flex-direction:column} }

/* category (stack) headers */
.stackhead{padding-top:.5rem;margin-bottom:.25rem}
.stackhead h4{display:flex;align-items:center;justify-content:space-between;gap:.5rem;font-size:14px;
  font-weight:700;min-height:28px}
.stackhead .title{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stackhead .title.strike{text-decoration:line-through;color:var(--text-muted)}
.stackhead .meta{font-size:12px;color:var(--text-muted);display:block}
.stackhead summary.icon-only{width:28px;height:28px;border:0;background:transparent;color:var(--text-muted)}
.stackhead summary.icon-only:hover{color:var(--orange)}

/* mana pips (own CSS circles, no icon font) */
.mana{display:inline-flex;gap:2px;align-items:center;white-space:nowrap}
.pip{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;border-radius:50%;
  font-size:10px;font-weight:900;font-style:normal;color:#111;background:#cbc2bf;
  box-shadow:-1px 1px 0 rgba(0,0,0,.6);flex:none;vertical-align:middle}
.pip-W{background:#f8f6d8} .pip-U{background:#c1d7e9} .pip-B{background:#bab1ab} .pip-R{background:#e49977}
.pip-G{background:#a3c095} .pip-C{background:#cbc2bf} .pip-g{background:#cbc2bf}
.pip.hy{background:linear-gradient(135deg,var(--h1) 50%,var(--h2) 50%)}
.pip-W.hy{--h1:#f8f6d8} .pip-U.hy{--h1:#c1d7e9} .pip-B.hy{--h1:#bab1ab} .pip-R.hy{--h1:#e49977}
.pip-G.hy{--h1:#a3c095}
.pip.hy[data-b=W]{--h2:#f8f6d8} .pip.hy[data-b=U]{--h2:#c1d7e9} .pip.hy[data-b=B]{--h2:#bab1ab}
.pip.hy[data-b=R]{--h2:#e49977} .pip.hy[data-b=G]{--h2:#a3c095}
.stats .pip,.ccard .pip{width:18px;height:18px}

/* text view rows (textViewCard: 30px, bold name, mana column) */
.deckview.text{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:0 2rem;
  align-items:start}
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
ul.rows .hover{display:none;position:absolute;left:2rem;top:30px;z-index:12;width:223px;aspect-ratio:5/7;
  border-radius:4.5%;overflow:hidden;box-shadow:var(--shadow);background:var(--surface-2);pointer-events:none}
ul.rows .hover img{width:100%;height:100%;display:block}
@media (hover:hover) and (min-width:700px){ ul.rows .row:hover .hover{display:block} }
@media (max-width:600px){ ul.rows .row{grid-template-columns:1.6rem minmax(0,1fr) auto}
  ul.rows .set,ul.rows .price{display:none} }

/* image cards: stacks and grid (basicCard 5:7, 4.5% radius, 2px border, corner quantity) */
.deckview.stacks,.deckview.grid{display:grid;gap:1rem;align-items:start}
.deckview.stacks{grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}
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
.deckview.stacks .c:hover ~ .c{transform:translateY(90%)}
.deckview.grid .cards{display:grid;grid-template-columns:repeat(5,1fr);gap:1rem}
@media (max-width:1500px){ .deckview.grid .cards{grid-template-columns:repeat(4,1fr)} }
@media (max-width:1200px){ .deckview.grid .cards{grid-template-columns:repeat(3,1fr)} }
@media (max-width:1000px){ .deckview.grid .cards{grid-template-columns:repeat(3,1fr)} }
@media (max-width:600px){ .deckview.grid .cards{grid-template-columns:repeat(2,1fr)}
  .deckview.stacks{grid-template-columns:1fr 1fr;gap:.75rem} }

/* deck stats panel */
.stats .head{display:flex;justify-content:space-between;align-items:center;gap:1rem}
.stats .grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(16rem,1fr);gap:1.5rem}
.stats .lbl{font-weight:700;margin:.75rem 0 .25rem}
.cbar{display:flex;height:36px;border-radius:3px;overflow:hidden;background:var(--surface-2);gap:2px}
.cbar .seg{display:block;height:100%;clip-path:polygon(0 0,100% 0,calc(100% - 7px) 100%,0 100%);min-width:6px}
.cbar.empty{opacity:.5}
.seg-W{background:#f8f6d8} .seg-U{background:#92c5de} .seg-B{background:#8a8a8a} .seg-R{background:#e6948a}
.seg-G{background:#a6d8a0} .seg-C{background:#cbc2bf}
.ccards{display:grid;grid-template-columns:repeat(auto-fill,minmax(14rem,1fr));gap:1rem;margin-top:1rem}
.ccard{border:1px solid var(--border);border-radius:3px;padding:.75rem;background:var(--bg)}
.ccard .cname{font-weight:700;display:flex;align-items:center;gap:.4rem;margin-bottom:.25rem}
.ccard .lbl{margin:.5rem 0 .25rem;font-size:.93rem}
.pbar{position:relative;height:26px;border-radius:3px;background:var(--surface-2);overflow:hidden}
.pbar .fill{position:absolute;left:0;top:0;bottom:0}
.pbar b{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);font-size:.8rem;
  background:var(--surface-3);color:#fff;padding:0 .35rem;border-radius:3px}
.ccard .sub{font-size:.8rem;color:var(--text-muted);text-align:center;margin-top:.2rem}
.stats .avg{margin:1rem 0 .25rem}
.curve{display:flex;align-items:flex-end;gap:.5rem;height:9rem;padding:.25rem 0;
  border-bottom:1px solid var(--border)}
.curve .bar{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;
  font-size:.75rem}
.curve .bar span{display:block;width:70%;background:var(--orange);border-radius:2px 2px 0 0}
.curve .bar b{font-variant-numeric:tabular-nums;margin-bottom:.15rem}
.curve .bar em{font-style:normal;font-weight:700;margin-top:.3rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(6.5rem,1fr));gap:.5rem;margin:0 0 .75rem}
.tile{background:var(--surface-2);border:1px solid var(--border-soft);border-radius:3px;padding:.5rem .6rem;
  display:flex;flex-direction:column;gap:.1rem}
.tile b{font-size:1.25rem;font-variant-numeric:tabular-nums} .tile span{color:var(--text-muted);
  font-size:.8rem}
.tile .spark{color:var(--orange);width:100%;height:36px}
table.qty{width:100%;border-collapse:collapse;font-size:.93rem}
table.qty td{padding:.3rem .25rem;border-top:1px solid var(--surface-2)}
table.qty td:last-child{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.legality ul{margin:.25rem 0 0;padding-left:1.2rem} .legality .ok{color:var(--green-text);font-weight:700}
@media (max-width:1000px){ .stats .grid{grid-template-columns:1fr} }

.description{font-size:16px} .description .empty{color:var(--text-muted)}

/* deck list (deckLink grid: thumbnail with colour bar, info block, tags strip) */
.listbar .controls{display:grid;grid-template-columns:1fr repeat(3,minmax(140px,auto)) auto;gap:1rem;
  align-items:end}
.listbar .field.grow{min-width:12rem}
.listbar .field .btn{margin:0}
.listbar .apply{margin-top:.5rem}
@media (max-width:1000px){ .listbar .controls{grid-template-columns:1fr 1fr} }
@media (max-width:600px){ .listbar .controls{grid-template-columns:1fr} }
ul.decklist.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:25px}
@media (max-width:1500px){ ul.decklist.grid{grid-template-columns:repeat(4,1fr)} }
@media (max-width:1200px){ ul.decklist.grid{grid-template-columns:repeat(3,1fr)} }
@media (max-width:1000px){ ul.decklist.grid{grid-template-columns:repeat(2,1fr)} }
@media (max-width:600px){ ul.decklist.grid{grid-template-columns:1fr;gap:1rem} }
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
ul.decklist.list .deck{margin-bottom:.5rem}
.pane.list ul.decklist.grid{grid-template-columns:1fr;gap:.75rem}
.pane.list ul.decklist .thumb{height:90px}

/* editor (own-deck edits that become one proposal) */
.editor .edithead .row{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap}
.editor .edithead .kicker{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;
  color:var(--text-muted)}
.editor .edithead h1.deckname{margin:0;font-size:24px}
.editor .edithead .btn{margin:0}
.editbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;
  background:var(--toolbar-bg);color:var(--toolbar-text);padding:.5rem 1rem;margin:0 -1rem 1rem;
  box-shadow:0 2px 6px rgba(0,0,0,.35)}
.editbar button{margin:0}
.editbar .count{color:inherit;opacity:.85}
.editbar .status{margin:0;flex-basis:100%}
.editbar .status:empty{display:none}
.pendingbox summary{cursor:pointer;font-weight:700}
.pendingbox summary b{margin-left:.5rem;background:var(--orange);color:#fff;border-radius:10px;
  padding:0 .5rem}
.addbox form.addcard{display:grid;grid-template-columns:minmax(0,2fr) 5.5rem minmax(0,1fr) minmax(0,1fr) auto;
  gap:.5rem;align-items:end}
.addbox form.addcard .field{margin:0}
.addbox form.addcard button{margin:0;height:var(--ctl)}
.addbox form.scanpick{display:flex;gap:.5rem;align-items:end;flex-wrap:wrap;margin-bottom:.75rem}
.addbox form.scanpick button{margin:0}
details.cat summary{display:flex;justify-content:space-between;align-items:center;cursor:pointer;
  font-weight:700;font-size:14px;list-style:none;padding:.25rem 0}
details.cat summary::-webkit-details-marker{display:none}
details.cat summary b{color:var(--text-muted);font-weight:400}
ul.erows{list-style:none;margin:.5rem 0 0;padding:0}
.erow{display:grid;grid-template-columns:34px minmax(0,1fr) auto minmax(8rem,12rem) auto;gap:.6rem;
  align-items:center;padding:.4rem 0;border-top:1px solid var(--border)}
.erow.changed{background:var(--orange-tint)}
.erow.removed .name{text-decoration:line-through;color:var(--text-muted)}
.erow.side{opacity:.75}
.erow .thumb{width:34px;height:48px;border-radius:3px;object-fit:cover;background:var(--surface-3);
  display:inline-flex;align-items:center;justify-content:center;color:var(--text-muted)}
.erow .main{display:flex;flex-direction:column;min-width:0}
.erow .name{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.erow .meta{font-size:.8rem;color:var(--text-muted)}
.erow .note:empty{display:none}
.erow .note{color:var(--orange-text)}
.erow .qty{display:inline-flex;align-items:center;gap:.25rem}
.erow .qty input{width:3.4rem;height:2.25rem;text-align:center;margin:0;padding:0 .25rem;
  font-variant-numeric:tabular-nums}
.erow .qty button.mini{margin:0}
.erow .sel select{height:2.25rem}
.erow details.dd summary.mini{display:inline-flex;align-items:center;justify-content:center;width:2.25rem;
  height:2.25rem;padding:0;margin:0}
.erow .menu .field{display:flex;flex-direction:column;gap:.25rem;padding:.5rem .75rem}
.erow .menu button{width:100%;text-align:left;border:0;background:none;margin:0;height:35px}
.erow .menu button:hover{background:var(--surface-3)}
.erow button.remove{margin:0}
.picker .pickbox .head{display:flex;justify-content:space-between;align-items:center;gap:1rem}
.picker .pickbox .head h2{margin:0}
.picker .prints{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:.6rem;
  margin-top:.75rem}
.picker .print{border:2px solid transparent;border-radius:6px;background:var(--surface-2);padding:.3rem;
  margin:0;height:auto;display:flex;flex-direction:column;gap:.3rem;align-items:center;cursor:pointer}
.picker .print img{width:100%;aspect-ratio:5/7;border-radius:4.5%;object-fit:cover}
.picker .print .cap{font-size:.75rem;color:var(--text-muted)}
.picker .print.current{border-color:var(--orange)}
.picker .print:hover{border-color:var(--link)}
@media (max-width:600px){
  .editbar{top:auto;bottom:50px;margin:0;position:fixed;left:0;right:0;
    padding:.5rem max(1rem,env(safe-area-inset-right)) .5rem max(1rem,env(safe-area-inset-left))}
  .editor{padding-bottom:6rem}
  .editbar .review{flex:1}
  .addbox form.addcard{grid-template-columns:1fr 1fr}
  .addbox form.addcard .grow,.addbox form.addcard button{grid-column:1 / -1}
  .erow{grid-template-columns:34px minmax(0,1fr) auto;grid-template-rows:auto auto}
  .erow .sel{grid-column:2;grid-row:2}
  .erow details.dd{grid-column:3;grid-row:2;justify-self:end}
}
"""

__all__ = [
    "DECK_CSS",
    "GROUPS",
    "SORTS",
    "VIEWS",
    "card_image",
    "deck_list_controls_html",
    "deck_list_html",
    "deck_page_html",
    "featured",
    "group_cards",
    "image_url",
    "mana_html",
]
