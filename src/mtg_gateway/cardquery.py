"""The card search page's filters (/cards) turned into one Scryfall search.

Every field of Scryfall's own advanced search form (scryfall.com/advanced) has a filter here, and
each is written into the search the way that form writes it (read 2026-10-14 by submitting the form
on scryfall.com and reading the search it produced):

* name: the typed words as they are (in brackets when they hold an ``or``, so it cannot reach past
  them);
* rules text, flavour text, artist and Lore Finder: one ``oracle:``/``flavor:``/``artist:``/``lore:``
  term per word, grouped in brackets when there are several (a quoted phrase stays one term);
* types: ``type:x``, ``-type:x`` for one marked NOT; several are all required unless "partial" is
  ticked, which takes any of them (``OR``);
* colours: ``color=``, ``color>=`` or ``color<=`` and the letters; commander colours: ``commander:``;
* mana cost: ``mana=``; mana value, power, toughness and loyalty: ``cmc<=2`` and so on;
* games: ``(game:paper)``, several joined by ``or``; formats: ``legal:``/``banned:``/``restricted:``;
* sets, blocks and rarities: one term each, several joined by ``OR``;
* criteria: ``is:x``, ``-is:x``; all required unless "partial" is ticked (``OR``);
* prices: ``usd<5`` and so on; language: ``lang:``; "Prefer": ``prefer:``; "Show all card prints":
  ``unique:prints``; "Include extra cards": ``include:extras``.

The order goes to Scryfall as its own ``order`` and ``dir`` parameters. Only known values are
accepted from each control (sets, blocks and types by their shape, since those lists come from
Scryfall itself), so nothing a page sends can reach Scryfall as anything but a search.
Cards are shown only for paper unless the games say otherwise. The two
free-text boxes (name, Scryfall syntax) have their quotes and brackets balanced, so neither can
close the bracket around it or open one that swallows the filters after it; a search longer than
Scryfall takes is refused whole (``too_long``), never cut.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

MAX_TEXT = 200  # characters per text box
MAX_ROWS = 4  # stat, format and price rows at most
MAX_PICKS = 40  # types, sets, blocks or criteria at most
MAX_QUERY = 1000  # characters in the search sent to Scryfall; a longer one is refused (too_long)

COLORS = ("W", "U", "B", "R", "G", "C")
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colorless"}
# Scryfall's three colour comparisons, by the short names the page's address uses
COLOR_COMPARE = {"eq": ("=", "Exactly these colors"), "ge": (">=", "Including these colors"),
                 "le": ("<=", "At most these colors")}  # fmt: skip
# How the commander colours match (Scryfall's identity comparisons, its syntax page read
# 2026-10-20): "within" is commander:, cards that fit a commander of these colours (today's one
# meaning, the default); "exact" is id=; "incl" is id>=.
IDENTITY_COMPARE = {"within": ("commander:", "Within these"), "exact": ("id=", "Exactly these"),
                    "incl": ("id>=", "Including these")}  # fmt: skip
STATS = (("cmc", "Mana value"), ("pow", "Power"), ("tou", "Toughness"), ("loy", "Loyalty"))
COMPARE = (
    ("eq", "=", "equal to"),
    ("lt", "<", "less than"),
    ("gt", ">", "greater than"),
    ("le", "<=", "less than or equal to"),
    ("ge", ">=", "greater than or equal to"),
    ("ne", "!=", "not equal to"),
)
PRICE_COMPARE = tuple(c for c in COMPARE if c[0] in ("lt", "gt", "le", "ge"))
CURRENCIES = (("usd", "USD"), ("eur", "EUR"), ("tix", "MTGO tickets"))
GAMES = (("paper", "Paper"), ("arena", "Arena"), ("mtgo", "Magic Online"))
FORMAT_STATUS = (("legal", "Legal"), ("restricted", "Restricted"), ("banned", "Banned"))
# Scryfall's formats, in the order of its advanced form and syntax page (read 2026-10-14)
FORMATS = (
    ("standard", "Standard"),
    ("future", "Future Standard"),
    ("historic", "Historic"),
    ("timeless", "Timeless"),
    ("gladiator", "Gladiator"),
    ("pioneer", "Pioneer"),
    ("modern", "Modern"),
    ("legacy", "Legacy"),
    ("pauper", "Pauper"),
    ("vintage", "Vintage"),
    ("penny", "Penny Dreadful"),
    ("commander", "Commander"),
    ("oathbreaker", "Oathbreaker"),
    ("standardbrawl", "Standard Brawl"),
    ("brawl", "Brawl"),
    ("competitivebrawl", "Competitive Brawl"),
    ("alchemy", "Alchemy"),
    ("paupercommander", "Pauper Commander"),
    ("duel", "Duel Commander"),
    ("oldschool", "Old School 93/94"),
    ("premodern", "Premodern"),
    ("predh", "PreDH"),
    ("tlr", "Tiny Leaders: Reborn"),
)
RARITIES = (("c", "Common"), ("u", "Uncommon"), ("r", "Rare"), ("m", "Mythic rare"))
# Scryfall's "Criteria" list (its advanced form, read 2026-10-14; one duplicate entry dropped)
CRITERIA = (
    ("adventure", "Adventure"),
    ("arenaid", "Arena ID"),
    ("artseries", "Art Series"),
    ("artist", "Artist"),
    ("artistmisprint", "Artist Misprint"),
    ("lights", "Attraction Lights"),
    ("atypical", "Atypical"),
    ("augmentation", "Augment"),
    ("back", "Back"),
    ("bear", "Bear"),
    ("beginnerbox", "Beginner Box"),
    ("booster", "Booster"),
    ("borderless", "Borderless"),
    ("brawlcommander", "Brawl Commander"),
    ("buyabox", "Buy-a-Box"),
    ("cardmarket", "Cardmarket ID"),
    ("class", "Class Layout"),
    ("ci", "Color Indicator"),
    ("colorshifted", "Colorshifted"),
    ("commander", "Commander"),
    ("companion", "Companion"),
    ("contentwarning", "Content Warning"),
    ("covered", "Covered"),
    ("manland", "Creature Land"),
    ("datestamped", "Datestamped"),
    ("default", "Default"),
    ("digital", "Digital"),
    ("doublesided", "Double Sided"),
    ("duelcommander", "Duel Commander"),
    ("etb", "E T B"),
    ("englishart", "English Art"),
    ("etch", "Etched"),
    ("extended", "Extended Art"),
    ("extra", "Extra"),
    ("ff", "Final Fantasy"),
    ("firstprint", "First Printing"),
    ("flavorname", "Flavor Name"),
    ("flavor", "Flavor Text"),
    ("flip", "Flip"),
    ("foil", "Foil"),
    ("fbb", "Foreign Black Border"),
    ("fwb", "Foreign White Border"),
    ("frenchvanilla", "French Vanilla"),
    ("fullart", "Full Art"),
    ("funny", "Funny"),
    ("future", "Future"),
    ("gamechanger", "Game Changer"),
    ("gameday", "Game Day"),
    ("hires", "Highres"),
    ("historic", "Historic"),
    ("splitmana", "Hybrid Mana"),
    ("illustration", "Illustration"),
    ("intropack", "Intro Pack"),
    ("invitational", "Invitational Card"),
    ("leveler", "Leveler"),
    ("localizedname", "Localized Name"),
    ("mtgoid", "MTGO ID"),
    ("masterpiece", "Masterpiece"),
    ("meld", "Meld"),
    ("modal", "Modal"),
    ("mdfc", "Modal Double Faced"),
    ("modern", "Modern"),
    ("multiverse", "Multiverse ID"),
    ("new", "New"),
    ("nonfoil", "Nonfoil"),
    ("notuniversesbeyond", "Not Universes Beyond"),
    ("oathbreaker", "Oathbreaker"),
    ("old", "Old"),
    ("outlaw", "Outlaw"),
    ("oversized", "Oversized"),
    ("partner", "Paired Commander"),
    ("paperart", "Paper Art"),
    ("party", "Party"),
    ("permanent", "Permanent"),
    ("phyrexia", "Phyrexian Mana"),
    ("planar", "Planar"),
    ("planeswalkerdeck", "Planeswalker Deck"),
    ("prepare", "Prepare"),
    ("prerelease", "Prerelease Promo"),
    ("printedtext", "Printed Text"),
    ("promo", "Promo"),
    ("related", "Related"),
    ("release", "Release Promo"),
    ("reprint", "Reprint"),
    ("reserved", "Reserved List"),
    ("resource", "Resource ID"),
    ("reversible", "Reversible"),
    ("stamp", "Security Stamp"),
    ("showcase", "Showcase"),
    ("spell", "Spell"),
    ("spellbook", "Spellbook"),
    ("spikey", "Spikey"),
    ("split", "Split Card"),
    ("stamped", "Stamped"),
    ("startercollection", "Starter Collection"),
    ("starterdeck", "Starter Deck"),
    ("story", "Story Spotlight"),
    ("tcgplayer", "TCGplayer ID"),
    ("textless", "Textless"),
    ("token", "Token"),
    ("tombstone", "Tombstone"),
    ("transform", "Transform"),
    ("translucent", "Translucent"),
    ("onlyprint", "Unique"),
    ("universesbeyond", "Universes Beyond"),
    ("vanilla", "Vanilla"),
    ("variation", "Variation"),
    ("watermark", "Watermark"),
    ("worthy", "Worthy"),
)
# Scryfall's language list (its advanced form): "" is its default (English, or the printing it
# prefers), "any" widens the search to every language
LANGUAGES = (
    ("", "Default"),
    ("any", "Any"),
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("ru", "Russian"),
    ("zhs", "Simplified Chinese"),
    ("zht", "Traditional Chinese"),
    ("he", "Hebrew"),
    ("la", "Latin"),
    ("grc", "Ancient Greek"),
    ("ar", "Arabic"),
    ("sa", "Sanskrit"),
    ("ph", "Phyrexian"),
    ("qya", "Quenya"),
    ("dw", "Dwarvish"),
    ("tlh", "Klingon"),
)
ORDERS = (
    ("name", "Name"),
    ("released", "Release date"),
    ("set", "Set and number"),
    ("rarity", "Rarity"),
    ("color", "Color"),
    ("usd", "Price: USD"),
    ("tix", "Price: TIX"),
    ("eur", "Price: EUR"),
    ("cmc", "Mana value"),
    ("power", "Power"),
    ("toughness", "Toughness"),
    ("artist", "Artist name"),
    ("edhrec", "EDHREC rank"),
    ("review", "Set review"),
)
DIRECTIONS = (("auto", "Automatic"), ("asc", "Ascending"), ("desc", "Descending"))
PREFER = (
    ("", "No preference"),
    ("newest", "Newest"),
    ("oldest", "Oldest"),
    ("promo", "Promo"),
    ("default", "Default Magic frame"),
    ("atypical", "Atypical Magic frame"),
    ("universesbeyond", "Universes Beyond"),
    ("notuniversesbeyond", "Not Universes Beyond"),
    ("usdlow", "Lowest USD"),
    ("usdhigh", "Highest USD"),
    ("eurlow", "Lowest EUR"),
    ("eurhigh", "Highest EUR"),
    ("tixlow", "Lowest TIX"),
    ("tixhigh", "Highest TIX"),
)
DEFAULT_ORDER = "name"

_SET_CODE = re.compile(r"[a-z0-9]{2,6}")
_TYPE_WORD = re.compile(r"[a-z0-9][a-z0-9.' -]{0,39}")  # Urza's, B.O.B., Power-Plant, Time Lord
_NUMBER = re.compile(r"-?\d{1,4}(\.\d{1,2})?")
_TOKEN = re.compile(r'"([^"]*)"|(\S+)')
_COLOR_LETTERS = set(COLORS)

_KNOWN = {
    "criteria": {v for v, _ in CRITERIA},
    "formats": {v for v, _ in FORMATS},
    "format_status": {v for v, _ in FORMAT_STATUS},
    "games": {v for v, _ in GAMES},
    "rarity": {v for v, _ in RARITIES},
    "languages": {v for v, _ in LANGUAGES},
    "orders": {v for v, _ in ORDERS},
    "dirs": {v for v, _ in DIRECTIONS},
    "prefer": {v for v, _ in PREFER},
    "stats": {v for v, _ in STATS},
    "currencies": {v for v, _ in CURRENCIES},
}
_OPS = {k: op for k, op, _ in COMPARE}
_PRICE_OPS = {k: op for k, op, _ in PRICE_COMPARE}


@dataclass
class Filters:
    """What the page's controls chose, each already limited to known values. The field names
    are the page address's parameter names (``to_params`` writes them back)."""

    name: str = ""
    oracle: str = ""
    types: list[str] = field(default_factory=list)  # "goblin", or "-goblin" for NOT
    types_any: bool = False
    colors: list[str] = field(default_factory=list)
    color_cmp: str = "ge"
    identity: list[str] = field(default_factory=list)
    identity_cmp: str = "within"
    mana: str = ""
    mv_min: str = ""  # the quick "Mana value from … to …" (the stat rows can say more)
    mv_max: str = ""
    stats: list[tuple[str, str, str]] = field(default_factory=list)  # (stat, cmp, value)
    games: list[str] = field(default_factory=lambda: ["paper"])
    formats: list[tuple[str, str]] = field(default_factory=list)  # (status, format)
    sets: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    rarity: list[str] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)  # "foil", or "-promo" for NOT
    criteria_any: bool = False
    prices: list[tuple[str, str, str]] = field(default_factory=list)  # (currency, cmp, value)
    artist: str = ""
    flavor: str = ""
    lore: str = ""
    lang: str = ""
    syntax: str = ""
    order: str = DEFAULT_ORDER
    dir: str = "auto"
    prefer: str = ""
    prints: bool = False
    extras: bool = False

    def chosen(self) -> bool:
        """Whether anything that narrows the search was picked (the games alone do not: paper is
        the default); with nothing picked the page shows its help instead of every card."""
        return any(
            (
                self.name,
                self.oracle,
                self.types,
                self.colors,
                self.identity,
                self.mana,
                self.mv_min,
                self.mv_max,
                self.stats,
                self.formats,
                self.sets,
                self.blocks,
                self.rarity,
                self.criteria,
                self.prices,
                self.artist,
                self.flavor,
                self.lore,
                self.lang,
                self.syntax,
            )
        )

    def shows_printings(self) -> bool:
        """Whether a result stands for one printing the member chose to see (every printing, a
        preferred printing or a language), so Add to deck adds that printing."""
        return self.prints or bool(self.prefer) or bool(self.lang)

    def to_params(self) -> list[tuple[str, str]]:
        """The page address's parameters for these filters (defaults left out)."""
        out: list[tuple[str, str]] = []
        for key, value in (("q", self.name), ("o", self.oracle), ("m", self.mana)):
            if value:
                out.append((key, value))
        out += [("t", t) for t in self.types]
        if self.types_any:
            out.append(("tany", "1"))
        out += [("c", c) for c in self.colors]
        if self.colors and self.color_cmp != "ge":
            out.append(("cmp", self.color_cmp))
        out += [("id", c) for c in self.identity]
        if self.identity and self.identity_cmp != "within":
            out.append(("idm", self.identity_cmp))
        if self.mv_min:
            out.append(("mv", self.mv_min))
        if self.mv_max:
            out.append(("mvx", self.mv_max))
        for stat, cmp, value in self.stats:
            out += [("st", stat), ("sto", cmp), ("stv", value)]
        if self.games != ["paper"]:
            out += [("g", g) for g in self.games] or [("g", "any")]
        for status, fmt in self.formats:
            out += [("fs", status), ("f", fmt)]
        out += [("s", s) for s in self.sets]
        out += [("b", b) for b in self.blocks]
        out += [("r", r) for r in self.rarity]
        out += [("is", c) for c in self.criteria]
        if self.criteria_any:
            out.append(("isany", "1"))
        for cur, cmp, value in self.prices:
            out += [("pc", cur), ("po", cmp), ("pv", value)]
        for key, value in (
            ("a", self.artist),
            ("ft", self.flavor),
            ("lore", self.lore),
            ("lang", self.lang),
            ("x", self.syntax),
            ("prefer", self.prefer),
        ):
            if value:
                out.append((key, value))
        if self.order != DEFAULT_ORDER:
            out.append(("order", self.order))
        if self.dir != "auto":
            out.append(("dir", self.dir))
        if self.prints:
            out.append(("prints", "1"))
        if self.extras:
            out.append(("extras", "1"))
        return out

    def query_string(self) -> str:
        return urlencode(self.to_params())


def _text(raw: Any) -> str:
    return " ".join(str(raw or "").split())[:MAX_TEXT]


def _getall(params: Any, key: str) -> list[str]:
    if hasattr(params, "getlist"):
        return [str(v) for v in params.getlist(key)]
    if isinstance(params, Mapping):
        value = params.get(key)
        if value is None:
            return []
        return [str(v) for v in value] if isinstance(value, list | tuple) else [str(value)]
    return [str(v) for k, v in params if k == key]


def _picks(params: Any, key: str) -> list[str]:
    """A picker's values: one per parameter, or several in one separated by commas (the page
    without scripts has a plain text box for each picker)."""
    return [v for raw in _getall(params, key) for v in raw.split(",") if v.strip()]


def _first(params: Any, key: str) -> str:
    values = _getall(params, key)
    return values[0] if values else ""


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out[:MAX_PICKS]


def _signed(raw: str, ok: Any) -> str | None:
    """``x`` or ``-x`` when ``x`` passes ``ok``, else None."""
    value = raw.strip().casefold()
    neg = value.startswith("-")
    core = value[1:] if neg else value
    if not core or not ok(core):
        return None
    return f"-{core}" if neg else core


def _number(raw: str) -> str | None:
    value = raw.strip()
    return value if _NUMBER.fullmatch(value) else None


def parse(params: Any) -> Filters:
    """The filters from the page address (a Starlette ``QueryParams``, a mapping of lists or a
    list of pairs). Unknown values are dropped, never passed on."""
    f = Filters()
    f.name = _text(_first(params, "q"))
    f.oracle = _text(_first(params, "o"))
    f.mana = _text(_first(params, "m"))[:60]
    f.artist = _text(_first(params, "a"))
    f.flavor = _text(_first(params, "ft"))
    f.lore = _text(_first(params, "lore"))
    f.syntax = _text(_first(params, "x"))
    f.types = _unique(
        t for raw in _picks(params, "t") if (t := _signed(raw, _TYPE_WORD.fullmatch)) is not None
    )
    f.types_any = _first(params, "tany") == "1"
    f.colors = [c for c in COLORS if c in {v.strip().upper() for v in _getall(params, "c")}]
    cmp = _first(params, "cmp")
    f.color_cmp = cmp if cmp in COLOR_COMPARE else "ge"
    f.identity = [c for c in COLORS if c in {v.strip().upper() for v in _getall(params, "id")}]
    idm = _first(params, "idm")
    f.identity_cmp = idm if idm in IDENTITY_COMPARE else "within"
    f.mv_min = _number(_first(params, "mv")) or ""
    f.mv_max = _number(_first(params, "mvx")) or ""
    stats = zip(_getall(params, "st"), _getall(params, "sto"), _getall(params, "stv"), strict=False)
    for stat, cmp, raw in stats:
        value = _number(raw)
        if stat in _KNOWN["stats"] and cmp in _OPS and value is not None and len(f.stats) < MAX_ROWS:
            f.stats.append((stat, cmp, value))
    games = [g.strip().lower() for g in _getall(params, "g")]
    if games:
        f.games = [g for g, _ in GAMES if g in games]  # "any" (or nothing known) leaves every game
    for status, fmt in zip(_getall(params, "fs"), _getall(params, "f"), strict=False):
        if status in _KNOWN["format_status"] and fmt in _KNOWN["formats"] and len(f.formats) < MAX_ROWS:
            f.formats.append((status, fmt))
    f.sets = _unique(s for raw in _picks(params, "s") if _SET_CODE.fullmatch(s := raw.strip().lower()))
    f.blocks = _unique(b for raw in _picks(params, "b") if _SET_CODE.fullmatch(b := raw.strip().lower()))
    picked = {v.strip().lower() for v in _getall(params, "r")}
    f.rarity = [r for r, _ in RARITIES if r in picked]
    f.criteria = _unique(
        c for raw in _picks(params, "is") if (c := _signed(raw, _KNOWN["criteria"].__contains__)) is not None
    )
    f.criteria_any = _first(params, "isany") == "1"
    prices = zip(_getall(params, "pc"), _getall(params, "po"), _getall(params, "pv"), strict=False)
    for cur, cmp, raw in prices:
        value = _number(raw)
        if (
            cur in _KNOWN["currencies"]
            and cmp in _PRICE_OPS
            and value is not None
            and not value.startswith("-")
            and len(f.prices) < MAX_ROWS
        ):
            f.prices.append((cur, cmp, value))
    lang = _first(params, "lang").strip().lower()
    f.lang = lang if lang in _KNOWN["languages"] else ""
    order = _first(params, "order")
    f.order = order if order in _KNOWN["orders"] else DEFAULT_ORDER
    direction = _first(params, "dir")
    f.dir = direction if direction in _KNOWN["dirs"] else "auto"
    prefer = _first(params, "prefer")
    f.prefer = prefer if prefer in _KNOWN["prefer"] else ""
    f.prints = _first(params, "prints") == "1"
    f.extras = _first(params, "extras") == "1"
    return f


def _words(text: str) -> list[str]:
    """The typed words, a quoted phrase kept as one."""
    out: list[str] = []
    for quoted, word in _TOKEN.findall(text):
        # a backslash at the end would hide the quote put around the word
        value = (quoted if quoted else word).replace('"', "").rstrip("\\").strip()
        if value:
            out.append(value)
    return out


def _term(key: str, value: str) -> str:
    """``key:value``, the value quoted when it has a space or a character Scryfall's syntax reads
    (brackets, a colon, a comparison) so it stays one plain search word."""
    if re.fullmatch(r"[\w{}/+.'*-]+", value) and not value.startswith("-"):
        return f"{key}:{value}"
    return f'{key}:"{value}"'


def _balanced(text: str) -> str:
    """``text`` with its quotes and brackets paired: the last of an odd number of quotes, a ``)``
    with no ``(`` before it and a ``(`` never closed are left out (brackets inside quotes are
    words, not grouping). A backslash before a quote, or at the very end, goes too, so it cannot
    hide the quote or the closing bracket that follows."""
    text = text.replace('\\"', '"').rstrip("\\")
    if text.count('"') % 2:
        cut = text.rindex('"')
        text = text[:cut] + text[cut + 1 :]
    drop: set[int] = set()
    opened: list[int] = []
    quoted = False
    for i, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
        elif quoted:
            continue
        elif ch == "(":
            opened.append(i)
        elif ch == ")":
            if opened:
                opened.pop()
            else:
                drop.add(i)
    drop.update(opened)
    return " ".join("".join(ch for i, ch in enumerate(text) if i not in drop).split())


_OR = re.compile(r'"[^"]*"|(?<!\S)or(?!\S)', re.IGNORECASE)


def _all_words(key: str, text: str) -> str:
    terms = [_term(key, w) for w in _words(text)]
    if not terms:
        return ""
    return terms[0] if len(terms) == 1 else "(" + " ".join(terms) + ")"


def _either(terms: list[str], joiner: str = "OR") -> str:
    if not terms:
        return ""
    return terms[0] if len(terms) == 1 else "(" + f" {joiner} ".join(terms) + ")"


def _signed_terms(key: str, values: list[str], any_of: bool) -> list[str]:
    plus = [_term(key, v) for v in values if not v.startswith("-")]
    minus = ["-" + _term(key, v[1:]) for v in values if v.startswith("-")]
    if any_of:
        return [t for t in [_either(plus)] if t] + minus
    return plus + minus


def scryfall_query(f: Filters) -> str:
    """The Scryfall search for ``f`` (without the order, which travels as its own parameters)."""
    parts: list[str] = []
    if f.name:
        name = _balanced(" ".join(f.name.replace("(", " ").replace(")", " ").split()))
        # an "or" in the name stays between the name's own words, not the filters after it
        if any(not m.group().startswith('"') for m in _OR.finditer(name)):
            name = f"({name})"
        parts.append(name)
    parts.append(_all_words("oracle", f.oracle))
    parts += _signed_terms("type", f.types, f.types_any)
    if f.colors:
        parts.append(f"color{COLOR_COMPARE[f.color_cmp][0]}{''.join(f.colors)}")
    if f.identity:
        parts.append(f"{IDENTITY_COMPARE[f.identity_cmp][0]}{''.join(f.identity)}")
    if f.mana:
        mana = re.sub(r"[^0-9A-Za-z{}/]", "", f.mana)
        if mana:
            parts.append(f"mana={mana}")
    if f.mv_min and f.mv_min == f.mv_max:
        parts.append(f"cmc={f.mv_min}")
    else:
        parts += [f"cmc>={f.mv_min}"] if f.mv_min else []
        parts += [f"cmc<={f.mv_max}"] if f.mv_max else []
    parts += [f"{stat}{_OPS[cmp]}{value}" for stat, cmp, value in f.stats]
    if f.games:
        parts.append("(" + " or ".join(f"game:{g}" for g in f.games) + ")")
    parts += [f"{status}:{fmt}" for status, fmt in f.formats]
    parts.append(_either([f"set:{s}" for s in f.sets]))
    parts.append(_either([f"block:{b}" for b in f.blocks]))
    parts.append(_either([f"rarity:{r}" for r in f.rarity]))
    parts += _signed_terms("is", f.criteria, f.criteria_any)
    parts += [f"{cur}{_PRICE_OPS[cmp]}{value}" for cur, cmp, value in f.prices]
    parts.append(_all_words("flavor", f.flavor))
    parts.append(_all_words("artist", f.artist))
    parts.append(_all_words("lore", f.lore))
    if f.lang:
        parts.append(f"lang:{f.lang}")
    if syntax := _balanced(f.syntax):
        parts.append(f"({syntax})")  # its own OR stays inside it
    if f.prefer:
        parts.append(f"prefer:{f.prefer}")
    if f.prints:
        parts.append("unique:prints")
    if f.extras:
        parts.append("include:extras")
    return " ".join(p for p in parts if p)  # longer than MAX_QUERY: refused by the caller
