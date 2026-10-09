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
import json
import re
import time
from collections import Counter
from typing import Any

from .archidekt import VOTE_UP, Deck, DeckCard, featured_scryfall_id, format_label
from .deck_stats import WUBRG, colour_letter, is_basic_land, is_land, mana_pips
from .mana import mana_html as _mana_html
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
    """Mana pips (mana.py): {2}{G}{U} -> three discs with our own glyphs."""
    return _mana_html(cost)


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
    style = f" style=\"--art:url('{esc(art_url)}')\"" if art_url else ""
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
    if csrf:
        # Any deck the member can read can be cloned into their own account, as on archidekt.com
        # (its Clone button is on public decks and precons too).
        primary += (
            f"<form method='post' action='/decks/{did}/clone' class='inline'>{csrf_in}"
            f"<button type='submit' title='Copy this deck into your own Archidekt account as a new private "
            f"deck'>{icon('clone')} Clone deck</button></form>"
        )
    # Archidekt's own playtester (draw, mulligan, play by hand) in its own tab (D-13, T-098): a frame
    # inside the gateway never carries the person's Archidekt sign-in, so a private deck stayed empty
    # there. The app opens the link in the phone's browser, where that sign-in lives.
    primary += (
        f"<a class='btn' href='https://archidekt.com/playtester-v2/{did}' target='_blank' "
        "rel='noreferrer noopener' title='Draw and play this deck by hand in Archidekt&#39;s playtester, "
        f"in a new tab'>{icon('play')} Playtest</a>"
    )
    if csrf:
        # The same run as the assistant's run_deck_report: statistics, validation and 300
        # goldfish games on the research service, stored under History.
        primary += (
            f"<form method='post' action='/decks/{did}/report' class='inline'>{csrf_in}"
            "<button type='submit' title='Goldfish simulation (300 games), validation and statistics; the "
            f"same run the assistant&#39;s run_deck_report makes. Saved under History.'>{icon('stats')} "
            "Run simulation</button></form>"
        )
    more_items = []
    if own and csrf:
        more_items.append(f"<a href='/decks/{did}/settings'>{icon('settings')} Deck settings</a>")
    more_items.append(f"<a href='/decks/{did}/export'>{icon('download')} Export deck</a>")
    more_items.append(f"<a href='/history?deck_id={did}'>{icon('history')} History and snapshots</a>")
    if own and csrf:
        more_items.append(f"<a class='danger' href='/decks/{did}/delete'>{icon('trash')} Delete deck…</a>")
    more_items.append(f"<a href='/decks/{did}/compare'>{icon('swap')} Compare with another deck…</a>")
    more_items.append(f"<a href='/decks/{did}#stats'>{icon('report')} Deck stats</a>")
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
        f"<section class='banner{'' if art_url else ' noart'}'{style}>"
        "<div class='shade'><div class='content'>"
        f"<div class='strip'{style}></div>"
        "<div class='info'>"
        f"<h1 class='deckname'>{privacy}<span>{esc(deck.name)}</span></h1>"
        "<div class='row'>"
        + (f"<span>{esc(ago(deck.updated_at))}</span>" if ago(deck.updated_at) else "")
        + f"<span>{stats.get('distinct', 0)} distinct cards</span>"
        "</div><div class='row'>"
        f"<span>{esc(format_label(deck.format))}</span>{legality}{own_bracket}{est_bracket}"
        "</div><div class='row'>"
        f"<span>Size: {stats.get('card_count', sum(c.quantity for c in deck.main_cards))}</span>"
        f"<span>Est cost: <b class='orange'>{money(stats.get('price_total'))}</b></span>"
        f"<span>Salt sum: <b class='orange'>{esc(salt)}</b></span>"
        "</div>"
        f"<div class='tags'>{icon('tag')} {tags}</div>"
        f"<div class='controls'><div class='primary'>{primary}{more}</div></div>{writes_note}"
        f"{social_html(deck, own=own) if csrf else ''}"
        "</div>"
        f"<a class='owner' href='/users/{esc(deck.owner)}' title='{esc(deck.owner)}: public decks'>"
        f"{avatar_html(deck.owner)}<span class='uname'>{esc(deck.owner or 'unknown')}</span></a>"
        "</div></div></section>"
    )


def social_html(deck: Deck, *, own: bool) -> str:
    """Like, Bookmark, Follow and Comments, as Archidekt's deck page offers them. The buttons are
    the person's own clicks: deck.js asks for a confirmation and posts to /social/api; without
    script they are inert and the More menu's "Open on Archidekt" link is the way."""
    did = esc(deck.id)
    liked = deck.user_vote == VOTE_UP
    like = (
        f"<button type='button' class='soc{' on' if liked else ''}' data-social='vote' "
        f"data-state='{deck.user_vote}' aria-pressed='{'true' if liked else 'false'}' "
        f"title='{'You like this deck' if liked else 'Like this deck on Archidekt'}'>"
        f"{icon('heart')}<b class='n'>{deck.points}</b><span>{'Liked' if liked else 'Like'}</span></button>"
    )
    marked = deck.bookmarked
    bookmark = (
        f"<button type='button' class='soc{' on' if marked else ''}' data-social='bookmark' "
        f"data-state='{1 if marked else 0}' aria-pressed='{'true' if marked else 'false'}' "
        f"title='{'Bookmarked on Archidekt' if marked else 'Bookmark this deck on Archidekt'}'>"
        f"{icon('bookmark')}<span>{'Bookmarked' if marked else 'Bookmark'}</span></button>"
    )
    follow = (
        f"<button type='button' class='soc' data-social='follow' data-user='{esc(deck.owner_id)}' "
        f"data-name='{esc(deck.owner)}' data-state='unknown' title='Follow {esc(deck.owner)} on Archidekt'>"
        f"{icon('follow')}<span>Follow {esc(deck.owner)}</span></button>"
        if deck.owner_id and not own
        else ""
    )
    comments = (
        f"<a class='soc' href='#comments' data-social='comments'>{icon('comment')}<span>Comments</span></a>"
    )
    return (
        f"<div class='social' data-deck='{did}' aria-label='Archidekt actions'>"
        f"{like}{bookmark}{follow}{comments}</div>"
    )


def comments_html(deck: Deck) -> str:
    """The deck's comment thread (loaded by deck.js when the section scrolls into view) and the
    form to add one; a post is confirmed before it goes out, under the member's Archidekt name."""
    did = esc(deck.id)
    return (
        f"<section class='panel comments' id='comments' data-deck='{did}' "
        f"data-root='{esc(deck.comment_root or '')}'>"
        f"<div class='panel-head'><h2>Comments</h2><span class='muted small' data-count></span></div>"
        f"<div class='thread' data-src='/social/api/decks/{did}/comments'><p class='muted'>Comments from "
        f"Archidekt appear here. <a href='https://archidekt.com/decks/{did}' target='_blank' "
        "rel='noreferrer noopener'>Read the thread on Archidekt</a>.</p></div>"
        "<form class='newcomment'><label for='ctext'>Add a comment</label>"
        "<textarea id='ctext' name='text' rows='3' maxlength='2000' "
        "placeholder='Say something about this deck…'></textarea>"
        "<div class='replyto' hidden></div>"
        f"<button type='submit' class='btn'>{icon('comment')} Post comment</button>"
        "<p class='muted small'>Posted publicly on Archidekt under your Archidekt name, after you "
        "confirm.</p>"
        "</form></section>"
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
        f"<button type='submit' class='primary'>{icon('search')} "
        "<span>Card search</span></button></div></form>"
        if own
        else ""
    ).replace("{did}", did)
    return (
        "<section class='toolbar panel'><div class='controls'>"
        f"{add}"
        f"<form method='get' action='/decks/{did}' class='views' id='viewform'>"
        + _select("view", VIEWS, view, "View as", "layers")
        + _select("group", GROUPS, group, "Group by", "grid")
        + _select("sort", SORTS, sort, "Sort by", "sort")
        + "<div class='field filter'><label for='q'>Local filter</label><span class='search'>"
        f"<input id='q' type='search' name='q' value='{esc(q)}' placeholder='Filter deck (eg: Sol Ring)' "
        "autocomplete='off'>"
        f"<button type='submit' aria-label='Apply filter'>{icon('search')}</button></span></div>"
        "<noscript><button type='submit' class='apply'>Apply</button></noscript>"
        "</form></div></section>"
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


def text_row(card: DeckCard, *, deck: Deck, owned: dict[str, int] | None = None) -> str:
    img = card_image(card)
    hover = f"<span class='hover'><img src='{esc(img)}' alt='' loading='lazy'></span>" if img else ""
    cls = " side" if not deck.in_deck(card) else ""
    return (
        f"<li class='row{cls}' data-name='{esc(card.name.lower())}' data-card='{esc(card.name)}'"
        f"{_card_data(card)}>"
        f"<span class='q'>{card.quantity}</span>"
        f"<span class='n'>{_label_dot(card)}{_owned_dot(card, owned)}<span "
        f"class='name'>{esc(card.name)}</span>"
        f"{_finish_badge(card)}{hover}</span>"
        f"<span class='mc'>{mana_html(card.mana_cost)}</span>"
        f"<span class='set' title='{esc(card.set_code.upper())} {esc(card.collector_number)}'>"
        f"{esc(card.set_code.upper())}</span>"
        f"<span class='price'>{money(card.price) if card.price is not None else ''}</span>"
        "</li>"
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


def _card_data(card: DeckCard) -> str:
    """Data attributes the page script reads for the card viewer and for dragging between stacks."""
    return card_view_attrs(card, img=card_image(card))


def image_card(card: DeckCard, *, owned: dict[str, int] | None = None) -> str:
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
        f"<div class='c' data-name='{esc(card.name.lower())}' data-card='{esc(card.name)}'{_card_data(card)} "
        f"title='{esc(card.name)}' tabindex='0' role='button'>{body}{qty}{extra}"
        f"{_finish_badge(card)}{_owned_dot(card, owned)}</div>"
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
            body = (
                "<ul class='plain rows'>"
                + "".join(text_row(c, deck=deck, owned=owned) for c in cards)
                + "</ul>"
            )
        else:
            body = "<div class='cards'>" + "".join(image_card(c, owned=owned) for c in cards) + "</div>"
        sections.append(f"<section class='stack' data-group='{esc(name)}'>{head}{body}</section>")
    # data-own and data-group let deck.js offer drag-and-drop between categories on the member's
    # own deck (the drops become one proposal, applied from the review page like every other edit).
    droppable = " data-own='1'" if own and group == "category" else ""
    return (
        f"<div class='deckview {esc(view)}' id='cards' data-deck='{esc(deck.id)}'{droppable}>"
        f"{''.join(sections)}</div>"
    )


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
        for cat in c.categories or ["Uncategorized"]:
            groups["Categories"][cat] += c.quantity
        groups["Primary category"][(c.categories or ["Uncategorized"])[0]] += c.quantity
        groups["Card name"][c.name] += c.quantity
        for t in c.types or ["Other"]:
            groups["Types"][t] += c.quantity
        for t in c.subtypes:
            groups["Sub types"][t] += c.quantity
        if c.cmc is not None and not is_land(c):
            groups["Mana value"][f"{c.cmc:g}"] += c.quantity
    return {k: dict(sorted(v.items(), key=lambda kv: (-kv[1], kv[0]))) for k, v in groups.items() if v}


def _odds_html(deck: Deck, cards: list[DeckCard]) -> str:
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
        "<input type='number' name='k' value='1' min='0' max='99' aria-label='How many'> card(s) by "
        f"<select name='by' aria-label='Group by'>{options}</select> having drawn "
        f"<input type='number' name='n' value='7' min='1' max='{size}' aria-label='Cards drawn'> card(s)"
        "</form>"
        "<table class='qty oddstable'><thead><tr><th>Group</th><th>Qty</th><th>Odds</th></tr></thead>"
        "<tbody><tr><td class='muted' colspan='3'>Turn on scripts to see the odds.</td></tr></tbody></table>"
        f"<script type='application/json' id='odds-data'>{data}</script>"
        "</div>"
    )


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
            f"{esc(format_label(deck.format))}</p></div>"
            if deck.format
            else ""
        )
    )
    odds_html = _odds_html(deck, cards)
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
                    ),
                    zone.get("ok", True),
                )
            )
            outside = checks.get("colour_identity_violations") or []
            rows.append(
                ("Colour identity", "all cards inside" if not outside else names(outside), not outside)
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
                text += f" ({legal['unknown']} card(s) without legality data)"
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
        br = checks.get("bracket") or {}
        if br.get("set"):
            rows.append(
                (
                    "Bracket",
                    f"set to {br['set']}"
                    + (f", cards suggest {br['estimate']} (estimate)" if br.get("estimate") else ""),
                    br.get("ok", True),
                )
            )
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
        f"{odds_html}"
        "</div><div class='side'>"
        f"<div class='tiles'><div class='tile'><b>{stats.get('card_count', 0)}</b>"
        "<span>Deck size</span></div>"
        f"<div class='tile'><b>{stats.get('land_count', 0)}</b><span>Lands</span></div>"
        f"<div class='tile'><b>{money(stats.get('price_total'))}</b><span>Est cost</span></div>"
        f"<div class='tile'><b>{stats.get('priced_cards', 0)}</b><span>Priced cards</span></div></div>"
        f"<h3>Quantity of types</h3><table class='qty'><tbody>{type_rows or no_types}</tbody></table>"
        + (f"<h3>Rarity</h3><table class='qty'><tbody>{rarity_rows}</tbody></table>" if rarity_rows else "")
        + checks_html
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
    owned: dict[str, int] | None = None,
) -> str:
    view = view if view in VIEWS else "text"
    group = group if group in GROUPS else "category"
    sort = sort if sort in SORTS else "name"
    return (
        banner_html(deck, stats, own=own, csrf=csrf, writes_enabled=writes_enabled)
        + notice
        + toolbar_html(deck, own=own, view=view, group=group, sort=sort, q=q)
        + cards_html(deck, view=view, group=group, sort=sort, q=q, own=own, owned=owned)
        + stats_panel_html(deck, stats)
        + description_html(deck)
        + (comments_html(deck) if csrf else "")
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


def _compare_row(name: str, qty_text: str, cards: dict[str, DeckCard]) -> str:
    """One card of a comparison list; when either deck holds the card the row opens the card
    viewer (``static/compare.js``), else it is plain text."""
    card = cards.get(name.split(" // ", 1)[0].strip().casefold())
    if card is None:
        return f"<li><b>{esc(qty_text)}</b> {esc(name)}</li>"
    return (
        f"<li><b>{esc(qty_text)}</b> <button type='button' class='cardlink' data-card='{esc(card.name)}'"
        f"{card_view_attrs(card, img=card_image(card))}>{esc(name)}</button></li>"
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
) -> str:
    """The deck page's compare view: a form to pick the other deck (Archidekt's preconstructed
    decks are offered as suggestions, any deck id or link and a pasted list work too) and, once
    chosen, what this build took out of it, put in and changed, with the statistics' differences
    when both are Archidekt decks."""
    did = esc(deck.id)
    name = esc(deck.name or f"Deck {deck.id}")
    options = esc(json.dumps(precon_labels(precons), ensure_ascii=False))
    form = (
        "<section class='panel comparehead'>"
        f"<div><a href='/decks/{did}'>← {name}</a>"
        "<span class='muted small'> · compare with another deck</span></div>"
        f"<form method='get' action='/decks/{did}/compare' class='compareform'>"
        "<label class='field'><span>Other deck: a preconstructed deck from the list, or any Archidekt deck "
        "id or link</span>"
        f"<input name='with' data-suggest='static' data-options='{options}' value='{esc(other_ref)}' "
        "placeholder='Start typing a precon name, or paste a deck link' autocomplete='off'></label>"
        "<label class='field'><span>…or paste a decklist (one card per line, Archidekt's export text works)"
        f"</span><textarea name='paste' rows='4' placeholder='1 Sol Ring&#10;1 Arcane Signet'>{esc(paste)}"
        "</textarea></label>"
        f"<button type='submit' class='btn-primary'>{icon('swap')} Compare</button></form>"
        "<p class='muted small'>Reads both decks; changes nothing on Archidekt. The assistant's "
        "compare_decks tool makes the same comparison, and can add a paired goldfish A/B when asked.</p>"
        "</section>"
    )
    if error:
        return form + f"<p class='notice error'>{error}</p>"
    if result is None or other is None:
        return form
    cards: dict[str, DeckCard] = {}
    if isinstance(other, Deck):
        cards.update({front_name(c.name).casefold(): c for c in other.cards})
    cards.update({front_name(c.name).casefold(): c for c in deck.cards})
    sm = result["summary"]
    oname = esc(other_name)
    tiles = [
        (f"{sm['before_size']}", f"cards in {other_name}"),
        (f"{sm['after_size']}", f"cards in {deck.name or 'this deck'}"),
        (f"{sm['cut']}", f"cards taken out ({sm['cut_pct']}% of {other_name})"),
        (f"{sm['added']}", f"cards put in ({sm['added_pct']}% of {deck.name or 'this deck'})"),
        (f"{sm['kept']}", "cards kept (basics aside)"),
    ]
    tile_html = "".join(f"<div class='tile'><b>{esc(v)}</b><span>{esc(k)}</span></div>" for v, k in tiles)
    # Basic lands are listed once, under "Basic lands", so the two card lists and their counts
    # agree with the tiles above them.
    removed_rows = [r for r in result["removed"] if not is_basic_land(r["name"])]
    added_rows = [r for r in result["added"] if not is_basic_land(r["name"])]
    removed = "".join(_compare_row(r["name"], f"{r['quantity']}x", cards) for r in removed_rows)
    added = "".join(_compare_row(r["name"], f"{r['quantity']}x", cards) for r in added_rows)
    changed = "".join(
        _compare_row(r["name"], f"{r['before']}→{r['after']}", cards) for r in result["changed"]
    )
    basics = "".join(
        f"<li><b>{b['before']}→{b['after']}</b> {esc(b['name'])}</li>" for b in sm["basic_land_changes"]
    )
    empty = "<li class='muted'>None</li>"
    lists = (
        "<div class='comparecols'>"
        f"<section class='panel'><h3>Taken out of {oname} <span class='count'>{len(removed_rows)}</span>"
        f"</h3><ul class='comparelist'>{removed or empty}</ul></section>"
        f"<section class='panel'><h3>Put into {name} <span class='count'>{len(added_rows)}</span></h3>"
        f"<ul class='comparelist'>{added or empty}</ul></section>"
        f"<section class='panel'><h3>Changed counts <span class='count'>{len(result['changed'])}</span></h3>"
        f"<ul class='comparelist'>{changed or empty}</ul>"
        f"<h3>Basic lands</h3><ul class='comparelist'>{basics or empty}</ul></section></div>"
    )
    delta_html = ""
    delta = result.get("stats_delta")
    if delta:
        rows = "".join(
            f"<tr><th>{esc(STAT_LABELS.get(k, k))}</th><td>{'+' if v > 0 else ''}{esc(v)}</td></tr>"
            for k, v in delta.items()
            if isinstance(v, (int, float)) and v != 0
        )
        delta_html = (
            "<section class='panel'><h3>Statistics: this deck minus "
            f"{oname}</h3><table class='deltatable'>{rows or '<tr><td class=muted>No difference</td></tr>'}"
            "</table></section>"
        )
    return (
        form + f"<section class='panel'><div class='tiles'>{tile_html}</div></section>" + lists + delta_html
    )


# -- deck list ------------------------------------------------------------------------------------

LIST_ORDERS = {"updated": "Updated at", "created": "Created at", "name": "Name", "format": "Deck format"}


def colour_bar_html(colors: dict[str, Any] | None) -> str:
    parts = {k: float(v) for k, v in (colors or {}).items() if k in WUBRG and v}
    return _bar(parts, label="Colour identity") if parts else "<div class='cbar empty'></div>"


def deck_card_html(
    d: dict[str, Any], *, cover: dict[str, Any] | None, selected: bool, show_owner: bool = False
) -> str:
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
    sub_bits = []
    if show_owner and d.get("owner"):
        sub_bits.append(f"by {d['owner']}")
    elif d.get("folder"):
        sub_bits.append(str(d["folder"]))
    if d.get("views") and show_owner:
        sub_bits.append(f"{d['views']:,} views")
    if ago(d.get("updated_at") or ""):
        sub_bits.append(ago(d.get("updated_at") or ""))
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
        f"<span class='sub'>{esc(' · '.join(sub_bits))}</span>"
        "</span></span>"
        f"<span class='tags'>{tags_html}</span></a></li>"
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
            show_owner=show_owner,
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
        f"<a class='btn' href='/folders'>{icon('folder')} Folders</a>"
        f"<a class='btn btn-primary' href='/decks/new'>{icon('plus')} New deck</a></div>"
        "<noscript><button type='submit' class='apply'>Apply</button></noscript>"
        "</form></section>"
    )


DECK_CSS = """
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
.comparehead{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap}
/* compare page */
.comparehead{flex-direction:column;align-items:stretch}
.compareform{display:grid;gap:.75rem}
.compareform .field span{display:block;font-size:.85rem;color:var(--text-muted);margin-bottom:.25rem}
.compareform input,.compareform textarea{width:100%}
.compareform button{justify-self:start}
.comparecols{display:grid;grid-template-columns:repeat(auto-fit,minmax(18rem,1fr));gap:1rem;margin-top:1rem}
.comparehead,.comparetiles{margin-bottom:1rem}
.comparecols .panel{margin:0}
.comparelist{list-style:none;margin:0;padding:0}
.comparelist li{display:flex;gap:.6rem;align-items:baseline;padding:.3rem 0;
  border-bottom:1px solid var(--border)}
.comparelist li:last-child{border-bottom:0}
.comparelist b{flex:0 0 3.2rem;color:var(--text-muted);font-variant-numeric:tabular-nums}
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
.banner .social .soc.on{background:var(--orange);border-color:var(--orange);color:#fff}
.banner .social .soc.on:hover{color:#fff;filter:brightness(1.08)}
.banner .social .soc svg{width:18px;height:18px}
.banner .social .soc[disabled]{opacity:.6;cursor:progress}
.banner .social .soc[hidden]{display:none}
.banner .social .confirm{display:inline-flex;align-items:center;flex-wrap:wrap;gap:.4rem;background:#fff;
  color:#111;border-radius:17px;padding:.25rem .35rem .25rem .85rem;min-height:34px;max-width:100%;
  font-size:.9rem;font-weight:700;box-sizing:border-box}
.banner .social .confirm button{margin:0;height:26px;padding:0 .7rem;border-radius:13px;font-size:.85rem}
.banner .social .note{flex-basis:100%;color:#ffd9b3;font-size:.9rem}
.banner .social .note a{color:#fff}
.comments .panel-head{display:flex;align-items:baseline;justify-content:space-between;gap:1rem}
.comments .panel-head h2{margin:0}
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
.comments form.newcomment{display:flex;flex-direction:column;gap:.5rem}
.comments form.newcomment textarea{width:100%;resize:vertical;min-height:4.5rem}
.comments form.newcomment button{align-self:flex-start;margin:0}
.comments form.newcomment .replyto{display:flex;gap:.5rem;align-items:center;font-size:.9rem}
.comments form.newcomment .replyto button{margin:0;height:26px;padding:0 .6rem;font-size:.8rem}
.comments .confirmbar{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;padding:.5rem .75rem;
  background:var(--surface-2);border-radius:var(--radius)}
.comments .confirmbar button{margin:0}
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
  .banner .strip{display:block;height:110px;background:var(--surface-2) var(--art,
    none) center 30%/cover no-repeat}
  .banner .info{padding:0 1rem 1rem}
  .banner h1.deckname{font-size:24px;margin-top:.5rem}
  .banner .row{font-size:13px}
  .banner .owner{flex-direction:row;justify-content:flex-end;max-width:none;padding:.5rem 1rem 0;order:-1}
  .banner .owner .avatar{width:80px;height:80px;font-size:2rem;margin-right:auto;margin-top:0;
    border-width:3px;border-color:var(--bg)}
  .banner .controls .primary,.banner .controls .primary .btn,.banner .controls .primary button,
  .banner .controls details.dd{width:100%}
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
.field .sel{position:relative;display:block}
.field .sel > svg{position:absolute;left:.75rem;top:50%;transform:translateY(-50%);color:var(--orange);
  pointer-events:none}
.field .sel select{padding-left:2.1rem}
.field .search{margin:0}
.toolbar .apply{margin-top:.5rem}
@media (max-width:1200px){ .toolbar .controls{grid-template-columns:1fr}
  .toolbar .views{grid-template-columns:repeat(3,minmax(0,1fr))} .toolbar .filter{grid-column:1 / -1} }
@media (max-width:600px){ .toolbar{padding:.5rem .75rem .75rem}
  .toolbar .views{grid-template-columns:1fr 1fr} .toolbar .views .field:nth-child(3){grid-column:1 / -1}
  .toolbar .quickrow button span{display:none} .toolbar .quickrow button{width:var(--ctl);padding:0;
    justify-content:center} }

/* category (stack) headers */
.stackhead{padding-top:.5rem;margin-bottom:.25rem}
.stackhead h4{display:flex;align-items:center;justify-content:space-between;gap:.5rem;font-size:14px;
  font-weight:700;min-height:28px}
.stackhead .title{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stackhead .title.strike{text-decoration:line-through;color:var(--text-muted)}
.stackhead .meta{font-size:12px;color:var(--text-muted);display:block}
.stackhead summary.icon-only{width:28px;height:28px;border:0;background:transparent;color:var(--text-muted)}
.stackhead summary.icon-only:hover{color:var(--orange)}

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
.owned{display:inline-block;width:9px;height:9px;border-radius:50%;background:#1ebb6c;flex:none;
  box-shadow:0 0 0 2px var(--bg)}
.deckview .c .owned{position:absolute;left:8px;bottom:8px;width:12px;height:12px;box-shadow:0 0 0 2px #fff}
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
.deckview .c{cursor:pointer}
.deckview .c:focus-visible{outline:3px solid var(--orange);outline-offset:2px}
/* a stack fans out below the card under the pointer (Archidekt's hover); on touch screens a tap on the
   stack toggles the fan instead, and a tap on a fanned card opens it */
@media (hover:hover){ .deckview.stacks .c:hover ~ .c{transform:translateY(90%)} }
.deckview.stacks .cards.fanned .c + .c{margin-top:-108%}
.deckview.stacks .cards.fanned .c{transform:none}
.deckview .c.dragging{opacity:.4}
.deckview .stack.dropping{outline:3px dashed var(--orange);outline-offset:4px;border-radius:5px}
.deckview[data-own] .stackhead .meta::after{content:' · drag cards here to recategorise';
  color:var(--text-muted)}
@media (hover:none){ .deckview[data-own] .stackhead .meta::after{content:' · hold a card to move it'} }
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
.cardview .facts .gc{color:var(--orange-text);font-weight:700}
.cardview .chips{display:flex;flex-wrap:wrap;gap:.3rem}
.cardview .chip{display:inline-block;padding:.05rem .5rem;border-radius:1rem;background:var(--surface-2);
  font-size:.82rem;line-height:1.5;white-space:nowrap}
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
  .cardview .cardtext{overflow:visible}
  .cardview .acts .btn,.cardview .acts button{flex:1 1 100%} }
@media (min-width:1400px){
  .cardview .box{width:min(100%,66rem);grid-template-columns:minmax(16rem,27rem) minmax(0,1fr)} }
/* pending category moves (own deck, stacks or grid): a bar like the editor's */
.movebar{position:sticky;bottom:0;z-index:20;display:none;align-items:center;gap:.5rem;flex-wrap:wrap;
  background:var(--toolbar-bg);color:var(--toolbar-text);padding:.5rem 1rem;margin:1rem -1rem 0;
  box-shadow:0 -2px 6px rgba(0,0,0,.35)}
.movebar.show{display:flex}
.movebar button{margin:0} .movebar .count{opacity:.85}
.movebar .status{margin:0;flex-basis:100%} .movebar .status:empty{display:none}
@media (max-width:900px){ .has-tabbar .movebar{position:fixed;left:0;right:0;
  bottom:calc(56px + env(safe-area-inset-bottom));margin:0} }
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
.odds{margin-top:1.25rem} .odds h3{margin:0 0 .4rem}
.odds .oddsform{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;font-size:.9rem;
  color:var(--text-muted)}
.odds .oddsform select,.odds .oddsform input{height:2rem;padding:0 .4rem;font-size:.9rem;width:auto;
  min-width:6rem;flex:none}
.odds .oddsform input{width:4rem;min-width:4rem}
.odds .oddstable{margin-top:.5rem;max-height:16rem;overflow:auto;display:block;width:100%}
.odds .oddstable td:nth-child(2),.odds .oddstable td:nth-child(3){text-align:right;white-space:nowrap}
.odds .oddstable td:first-child{width:100%}
.odds .oddstable th{text-align:left;font-weight:600;color:var(--text-muted);font-size:.8rem}
.odds .oddstable th:last-child{text-align:right}
.checks ul{list-style:none;margin:.25rem 0 0;padding:0} .checks li{display:flex;gap:.4rem;
  align-items:baseline;
  padding:.2rem 0;border-top:1px solid var(--border)} .checks li:first-child{border-top:0}
.checks li b{flex:none} .checks li span{color:var(--text-muted);overflow-wrap:anywhere}
.checks li.ok svg{color:var(--green-text)} .checks li.bad svg{color:var(--red-text,#d33)}
@media (max-width:1000px){ .stats .grid{grid-template-columns:1fr} }

.description{font-size:16px} .description .empty{color:var(--text-muted)}

/* deck list (deckLink grid: thumbnail with colour bar, info block, tags strip) */
.listbar .controls{display:grid;grid-template-columns:1fr repeat(3,minmax(140px,auto)) auto;gap:1rem;
  align-items:end}
.listbar .field.grow{min-width:12rem}
.listbar .field .btn{margin:0}
.listbar .apply{margin-top:.5rem}
@media (max-width:1000px){ .listbar .controls{grid-template-columns:1fr 1fr;align-items:start}
  .listbar .field:last-of-type{grid-column:1 / -1;display:flex;flex-direction:row;flex-wrap:wrap;gap:.5rem;
    align-items:center}
  .listbar .field:last-of-type .lbl{margin-right:auto} }
@media (max-width:600px){ .listbar .controls{grid-template-columns:1fr} }
ul.decklist.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:25px;margin-bottom:1.25rem}
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
/* add a card: the name box leads; count, category, finish, zone and the button sit on the same
   row where there is room, and fold onto two rows on narrow screens (never a stray button) */
.addbox form.addcard{display:grid;gap:.6rem .75rem;align-items:end;
  grid-template-columns:minmax(12rem,3fr) 5rem minmax(8rem,1.2fr) minmax(6.5rem,1fr) minmax(6.5rem,1fr) auto}
.addbox form.addcard .field{margin:0}
.addbox form.addcard button{margin:0;height:var(--ctl);white-space:nowrap}
.addbox form.addcard .addstatus{grid-column:1 / -1;margin:0;min-height:1.2em}
.addbox form.addcard .addstatus:empty{display:none}
@media (max-width:1100px){
  .addbox form.addcard{grid-template-columns:minmax(0,1fr) 5rem auto}
  .addbox form.addcard .grow{grid-column:1} .addbox form.addcard .qtyf{grid-column:2}
  .addbox form.addcard .go{grid-column:3}
  .addbox form.addcard .field:not(.grow):not(.qtyf):not(.go){grid-row:2;grid-column:auto}
  .addbox form.addcard{grid-template-areas:none} }
@media (max-width:1100px) and (min-width:601px){
  .addbox form.addcard{grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1fr)}
  .addbox form.addcard .grow{grid-column:1 / 3} .addbox form.addcard .qtyf{grid-column:3;grid-row:1}
  .addbox form.addcard .go{grid-column:3;grid-row:2} }
/* categories side by side on wide screens, one column on phones */
.cats.existing{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,30rem),1fr));gap:1rem;
  align-items:start}
.cats.existing > details.cat{margin:0}
.addbox form.scanpick{display:flex;gap:.5rem;align-items:end;flex-wrap:wrap;margin-bottom:.75rem}
.addbox form.scanpick button{margin:0}
details.cat summary{display:flex;justify-content:space-between;align-items:center;cursor:pointer;
  font-weight:700;font-size:14px;list-style:none;padding:.25rem 0}
details.cat summary::-webkit-details-marker{display:none}
details.cat summary b{color:var(--text-muted);font-weight:400}
ul.erows{list-style:none;margin:.5rem 0 0;padding:0}
.erow{display:grid;grid-template-columns:40px minmax(0,1fr) auto minmax(7rem,10rem) auto;gap:.6rem;
  align-items:center;padding:.4rem 0;border-top:1px solid var(--border-soft)}
.erow.changed{background:var(--orange-tint)}
.erow.removed .name{text-decoration:line-through;color:var(--text-muted)}
.erow.side .thumb{filter:saturate(.6)}
.erow.side .meta{font-style:italic}
.erow .remove{margin-left:.25rem}
/* settings page: cover, tags, folder, delete */
.coverform{display:flex;flex-wrap:wrap;gap:1rem;align-items:flex-end}
.coverart{width:100%;max-width:22rem;aspect-ratio:16/9;object-fit:cover;border-radius:var(--radius-panel);
  background:var(--surface-2);display:flex;align-items:center;justify-content:center;flex:1 1 14rem}
.coverform .field{flex:1 1 14rem;margin:0}
.taglist{display:flex;flex-wrap:wrap;gap:.5rem;margin:0 0 1rem}
.taglist li{display:flex;align-items:center;gap:.25rem}
.taglist .pill{font-size:.95rem;padding:.3rem .6rem}
.taglist form.inline{display:inline;margin:0}
.taglist button.mini{width:1.9rem;height:1.9rem;min-height:0;padding:0;margin:0;display:inline-flex;
  align-items:center;justify-content:center;border-radius:50%}
.taglist button.mini svg{width:14px;height:14px}
.addtag,.moveform{display:flex;flex-wrap:wrap;gap:.75rem;align-items:flex-end}
.addtag .field,.moveform .field{margin:0}
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
.picker .prints{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:.6rem;
  overflow:auto;min-height:0;padding:2px}
.picker .print{border:2px solid transparent;border-radius:6px;background:var(--surface-2);padding:.3rem;
  margin:0;height:auto;display:flex;flex-direction:column;gap:.3rem;align-items:center;cursor:pointer}
.picker .print img{width:100%;aspect-ratio:5/7;border-radius:4.5%;object-fit:cover}
.picker .print .cap{font-size:.75rem;color:var(--text-muted)}
.picker .print.current{border-color:var(--orange)}
.picker .print:hover{border-color:var(--link)}
@media (max-width:700px){
  .picker{padding:0;align-items:flex-end;-webkit-backdrop-filter:none;backdrop-filter:none}
  .picker .pickbox{width:100%;max-height:94vh;border-radius:var(--radius-panel) var(--radius-panel) 0 0;
    border-bottom:0;padding:.75rem .75rem calc(.75rem + env(safe-area-inset-bottom))}
  .picker .prints{grid-template-columns:repeat(auto-fill,minmax(96px,1fr))} }
@media (max-width:600px){
  .editbar{top:auto;bottom:50px;margin:0;position:fixed;left:0;right:0;
    padding:.5rem max(1rem,env(safe-area-inset-right)) .5rem max(1rem,env(safe-area-inset-left))}
  .editor{padding-bottom:6rem}
  .editbar .review{flex:1}
  .addbox form.addcard{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
  .addbox form.addcard .grow{grid-column:1 / -1;grid-row:1}
  .addbox form.addcard .qtyf{grid-column:2;grid-row:2}
  .addbox form.addcard .go{grid-column:1;grid-row:2} .addbox form.addcard .go button{width:100%}
  .addbox form.addcard .field:not(.grow):not(.qtyf):not(.go){grid-row:auto;grid-column:auto}
  .erow{grid-template-columns:40px minmax(0,1fr) auto;grid-template-rows:auto auto}
  .erow .sel{grid-column:2;grid-row:2}
  .erow details.dd,.erow button.remove{grid-column:3;grid-row:2;justify-self:end}
}
"""

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
