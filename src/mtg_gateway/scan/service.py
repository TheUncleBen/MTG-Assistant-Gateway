"""Resolve card inputs to exact Scryfall cards and manage scan sessions."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..db import Database
from ..decklist import ListCard, to_text
from .art import art_owner
from .names import NameCatalog
from .scryfall import ScryfallClient, ScryfallError, summarize
from .store import ScanStore

logger = logging.getLogger(__name__)

MAX_CARDS = 500  # items per saved session, and per parsed input list
MAX_RESOLVE_CARDS = 250  # names per resolve call: a full deck plus sideboard, bounded Scryfall traffic
MAX_TEXT = 200_000
MAX_LINE = 300  # a card line never needs more; longer lines are skipped before any regex runs
MAX_LINES = 2000
MAX_SUGGESTIONS = 8
FUZZY_MIN_SIMILARITY = 0.65  # default; see ScanThresholds
# Recovery for names Scryfall cannot place at all (fuzzy 404, empty autocomplete):
# retry with common OCR confusions swapped, then autocomplete on shorter prefixes.
MAX_VARIANT_LOOKUPS = 4
MAX_PREFIX_LOOKUPS = 5
MAX_RECOVERY_LOOKUPS_PER_CALL = 40  # recovery lookups per call, however many names are garbage
MAX_LOOKUPS_PER_USER = 4  # concurrent name searches and printing lists per account; more is "busy"
MAX_SESSION_BYTES = 600_000  # one saved scan session as stored JSON (500 full cards take about 470 KB)
MAX_CALL_SECONDS = 40.0  # wall-clock budget per resolve call, under proxy and client timeouts
MIN_PREFIX = 4
AMBIGUITY_MARGIN = 0.05  # default; see ScanThresholds
STATUSES = ("exact", "printing", "fuzzy", "ambiguous", "not_found", "deferred", "error")


@dataclass(frozen=True)
class ScanThresholds:
    """Every match threshold in one place; the gateway fills it from MTG_SCAN_* settings.

    ``fuzzy_min_similarity``: a fuzzy or recovered match must resemble the name as read this much.
    ``fuzzy_confident_similarity``: a fuzzy match this close to the name as read is accepted
    without a second lookup for alternatives (saves one paced request per misread name).
    ``ambiguity_margin``: two candidates closer than this are reported as ambiguous, not picked.
    A set and number read from the card are trusted only when the title agrees: the same name, or
    the title on its own resolving to the same card. Neither similarity nor a name prefix is a
    threshold here: a misread number can land on a card with a similar name (Sol Ring and Sol
    Talisman), and many complete names begin a longer one (Mountain and Mountain Goat).
    ``auto_add_confidence``: page-side; OCR confidence (0-100) at or above which an exact or
    printing match may be added without a tap (continuous scan).
    ``foil_star_ink_ratio``: page-side; share of dark pixels between the set code and the
    language on the info line at or above which the glyph is read as the foil star, not the dot.
    ``glare_ratio``: page-side; share of blown-out (near white) pixels in the title strip at or
    above which the page warns about glare and asks for the card to be tilted.
    ``min_ocr_confidence``: page-side; in continuous scan a title read below this confidence
    (0-100) goes to the unidentified list without a lookup.
    """

    fuzzy_min_similarity: float = 0.65
    fuzzy_confident_similarity: float = 0.8
    ambiguity_margin: float = 0.05
    auto_add_confidence: float = 80.0
    foil_star_ink_ratio: float = 0.09
    glare_ratio: float = 0.08
    min_ocr_confidence: float = 50.0

    def as_dict(self) -> dict[str, float]:
        return {
            "fuzzy_min_similarity": self.fuzzy_min_similarity,
            "fuzzy_confident_similarity": self.fuzzy_confident_similarity,
            "ambiguity_margin": self.ambiguity_margin,
            "auto_add_confidence": self.auto_add_confidence,
            "foil_star_ink_ratio": self.foil_star_ink_ratio,
            "glare_ratio": self.glare_ratio,
            "min_ocr_confidence": self.min_ocr_confidence,
        }


# Optional second opinion for picking a printing: given the input (with ``art_hash``) and the
# card matched by name, return the printing to use instead, or None. Filled in by the art-match
# work; the resolver only calls it when the input carries an art hash.
ArtMatcher = Callable[["CardInput", dict[str, Any]], Awaitable[dict[str, Any] | None]]

# "2 Sol Ring (CMR) 472 *F*", "2x Sol Ring", "Sol Ring", "1 Fire // Ice [APC]"
_LINE = re.compile(
    r"""^\s*
    (?:(?P<qty>\d{1,3})\s*[xX×]?\s+)?          # optional quantity
    (?P<name>\S(?:.*?\S)?)                      # name (lazy, cannot end in whitespace)
    (?:\s+[\(\[](?P<set>[A-Za-z0-9]{2,6})[\)\]]  # optional (SET) or [SET]
       (?:\s+(?P<num>[A-Za-z0-9★†\-]{1,8}))?)?   # optional collector number
    (?:\s+\*F\*)?                               # optional foil marker
    \s*$""",
    re.VERBOSE,
)
_SKIP = re.compile(r"^\s*(//|#|$)|^\s*(deck|sideboard|commander|maybeboard|companion)\s*:?\s*$", re.I)


class ScanError(Exception):
    """User-facing failure. ``kind``: invalid, not_found, unavailable, rate_limited, busy."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class CardInput:
    name: str = ""
    set_code: str = ""
    collector_number: str = ""
    quantity: int = 1
    raw: str = ""
    foil: bool | None = None  # read from the info line's star, or set by the user; None = unknown
    lang: str = ""  # two-letter language from the info line, informational
    art_hash: str = ""  # optional artwork fingerprint for the art-match hook

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"quantity": self.quantity}
        if self.name:
            d["name"] = self.name
        if self.set_code:
            d["set"] = self.set_code
        if self.collector_number:
            d["collector_number"] = self.collector_number
        if self.raw:
            d["raw"] = self.raw
        if self.foil is not None:
            d["foil"] = self.foil
        if self.lang:
            d["lang"] = self.lang
        return d


@dataclass
class Resolution:
    input: CardInput
    status: str
    card: dict[str, Any] | None = None
    note: str = ""
    suggestions: list[str] = field(default_factory=list)
    # Every autocomplete completion of the name as read (up to Scryfall's 20), kept for the
    # printing check; ``suggestions`` is the shorter list shown to people.
    completions: list[str] = field(default_factory=list)
    # True when the title and a set+number read disagreed and the title decided: the match is
    # by name only and a page must not add it without a look (the note says what the number was).
    conflict: bool = False
    art: dict[str, Any] | None = None  # the art matcher's opinion (status, distance, margin, note)

    def as_dict(self) -> dict[str, Any]:
        self._note_foil_disagreement()
        return {
            "input": self.input.as_dict(),
            "quantity": self.input.quantity,
            "status": self.status,
            "card": self.card,
            "note": self.note,
            "suggestions": self.suggestions,
            "foil": self.foil,
            "conflict": self.conflict,
            "art": self.art,
        }

    @property
    def foil(self) -> bool | None:
        """Foil as decided by the printing when it exists in one finish only, else as given.

        A single finish is the stronger signal: a star misread on a nonfoil-only printing names
        a finish that does not exist, so the printing wins and ``note`` says the read disagreed.
        """
        finishes = (self.card or {}).get("finishes") or []
        if finishes == ["foil"]:
            return True
        if finishes == ["nonfoil"]:
            return False
        return self.input.foil

    def _note_foil_disagreement(self) -> None:
        got = self.foil
        if self.input.foil is not None and got is not None and got != self.input.foil:
            what = "foil" if got else "non-foil"
            msg = f"this printing only exists {what}; the foil read was ignored"
            if msg not in (self.note or ""):
                self.note = f"{self.note}; {msg}" if self.note else msg


def parse_text(text: str) -> list[CardInput]:
    """Parse a free-text decklist (one card per line) into inputs."""
    if len(text) > MAX_TEXT:
        raise ScanError("invalid", "text is too long")
    out: list[CardInput] = []
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise ScanError("invalid", f"at most {MAX_LINES} lines")
    for raw in lines:
        line = raw.strip()
        if len(line) > MAX_LINE or _SKIP.match(line):
            continue
        m = _LINE.match(line)
        if not m or not m.group("name"):
            continue
        qty = int(m.group("qty") or 1)
        out.append(
            CardInput(
                name=_clean_name(m.group("name")),
                set_code=(m.group("set") or "").lower(),
                collector_number=_clean_number(m.group("num") or ""),
                quantity=max(1, min(qty, 999)),
                raw=line,
                foil=True if "*F*" in line else None,
            )
        )
    return out


def parse_cards(raw: Any) -> list[CardInput]:
    """Validate a list of ``{name?, set?, collector_number?, quantity?}`` objects."""
    if not isinstance(raw, list):
        raise ScanError("invalid", "cards must be a list")
    if len(raw) > MAX_CARDS:
        raise ScanError("invalid", f"at most {MAX_CARDS} cards per call")
    out: list[CardInput] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            if len(item) > MAX_LINE:
                raise ScanError("invalid", f"card {i}: line longer than {MAX_LINE} characters")
            parsed = parse_text(item)
            if not parsed:
                raise ScanError("invalid", f"card {i}: could not read '{item[:40]}'")
            out.extend(parsed)
            continue
        if not isinstance(item, dict):
            raise ScanError("invalid", f"card {i} must be an object or a string")
        name = _clean_name(str(item.get("name") or item.get("card_name") or ""))
        set_code = str(item.get("set") or item.get("set_code") or "").strip().lower()
        number = _clean_number(str(item.get("collector_number") or item.get("number") or ""))
        if not name and not (set_code and number):
            raise ScanError("invalid", f"card {i}: give a name, or a set and collector_number")
        if len(name) > 200 or len(set_code) > 6 or len(number) > 8:
            raise ScanError("invalid", f"card {i}: field too long")
        if not re.fullmatch(r"[a-z0-9]*", set_code):
            raise ScanError("invalid", f"card {i}: set must be a set code such as cmr")
        if number and not re.fullmatch(r"[A-Za-z0-9★†\-]+", number):
            raise ScanError("invalid", f"card {i}: collector_number looks wrong")
        qty = item.get("quantity", 1)
        if qty is None:
            qty = 1
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1 or qty > 999:
            raise ScanError("invalid", f"card {i}: quantity must be an integer from 1 to 999")
        foil = item.get("foil")
        if foil is not None and not isinstance(foil, bool):
            raise ScanError("invalid", f"card {i}: foil must be true or false")
        lang = str(item.get("lang") or "").strip().lower()
        if lang and not re.fullmatch(r"[a-z]{2}", lang):
            raise ScanError("invalid", f"card {i}: lang must be a two-letter code such as en")
        art_hash = str(item.get("art_hash") or "").strip()
        if art_hash and (len(art_hash) > 512 or not re.fullmatch(r"[A-Za-z0-9+/=_\-]+", art_hash)):
            raise ScanError("invalid", f"card {i}: art_hash looks wrong")
        raw_line = str(item.get("raw") or "")[:300]
        out.append(CardInput(name, set_code, number, qty, raw_line, foil=foil, lang=lang, art_hash=art_hash))
    return out


def _clean_number(number: str) -> str:
    """Collector numbers as Scryfall keys them: no leading zeros ("0472" is printed, "472" is the key)."""
    number = number.strip()
    stripped = number.lstrip("0")
    if number and not stripped:
        return "0"
    return stripped if re.match(r"\d", stripped or "") else number


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip().strip("\"'").strip()
    name = re.sub(r"\s*\*F\*$", "", name)
    return name


def decklist_text(items: list[dict[str, Any]]) -> str:
    """Resolved items in the same plain format the other tools emit (decklist.to_text)."""
    cards = []
    for it in items:
        card = it.get("card") or {}
        name = card.get("name") or it.get("name") or (it.get("input") or {}).get("name")
        if not name:
            continue
        cards.append(
            ListCard(
                quantity=int(it.get("quantity", 1)),
                name=name,
                set_code=card.get("set") or "",
                collector_number=card.get("collector_number") or "",
                foil=bool(it.get("foil")),
            )
        )
    return to_text(cards)


def as_changes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolved items as ``propose_deck_changes`` additions, one per distinct name.

    When every copy of a name is one agreed printing (status ``printing``: title and collector
    line agreed, or the printing was chosen by hand) in one finish, the addition also carries
    ``set_code``, ``collector_number`` and ``foil`` so the deck tools can keep that printing.
    Copies matched by name alone, or spread over several printings, are added by name alone.
    """
    counts: dict[str, int] = {}
    prints: dict[str, set[tuple[str, str, bool | None]]] = {}
    by_name_only: set[str] = set()
    for it in items:
        card = it.get("card")
        if not card or not card.get("name"):
            continue
        name = card["name"]
        counts[name] = counts.get(name, 0) + int(it.get("quantity", 1))
        if it.get("status") == "printing" and card.get("set") and card.get("collector_number"):
            foil = it.get("foil")
            prints.setdefault(name, set()).add(
                (str(card["set"]), str(card["collector_number"]), foil if isinstance(foil, bool) else None)
            )
        else:
            by_name_only.add(name)
    out: list[dict[str, Any]] = []
    for name, qty in counts.items():
        change: dict[str, Any] = {"action": "add", "card_name": name}
        if name not in by_name_only and len(prints.get(name, ())) == 1:
            set_code, number, foil = next(iter(prints[name]))
            change.update({"set_code": set_code, "collector_number": number})
            if foil is not None:  # an unknown finish is not a claim of "not foil"
                change["foil"] = foil
        # propose_deck_changes takes at most 99 copies per change: split bigger piles (basic lands).
        while qty > 99:
            out.append({**change, "quantity": 99})
            qty -= 99
        out.append({**change, "quantity": qty})
    return out


def change_fields(resolved: list[dict[str, Any]]) -> dict[str, Any]:
    """``changes`` for propose_deck_changes, plus ``change_batches`` (one proposal each) when there
    are more than one proposal can hold."""
    from ..decks import MAX_CHANGES

    changes = as_changes(resolved)
    out: dict[str, Any] = {"changes": changes}
    if len(changes) > MAX_CHANGES:
        out["change_batches"] = [changes[i : i + MAX_CHANGES] for i in range(0, len(changes), MAX_CHANGES)]
        out["changes_note"] = (
            f"{len(changes)} changes: one proposal takes at most {MAX_CHANGES}, so propose each of "
            "change_batches separately, or use propose_new_deck with decklist_text for a new deck."
        )
    return out


class _Budget:
    """What one resolve call may still spend: extra recovery lookups, and wall-clock time."""

    def __init__(self, lookups: int, deadline: float):
        self.lookups = lookups
        self.deadline = deadline

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.deadline

    def spend(self) -> bool:
        if self.lookups <= 0 or self.expired:
            return False
        self.lookups -= 1
        return True


class ScanService:
    def __init__(
        self,
        db: Database,
        scryfall: ScryfallClient,
        *,
        call_seconds: float = MAX_CALL_SECONDS,
        thresholds: ScanThresholds | None = None,
        art_matcher: ArtMatcher | None = None,
    ):
        self.db = db
        self.scryfall = scryfall
        self.call_seconds = call_seconds
        self.names = NameCatalog(lambda: self.scryfall)
        self.t = thresholds or ScanThresholds()
        self.art_matcher = art_matcher
        self.store = ScanStore(db)
        self._in_flight: set[str] = set()
        self._lookups: dict[str, int] = {}

    # -- resolution -----------------------------------------------------------
    async def resolve(self, inputs: list[CardInput], *, owner: str | None = None) -> list[Resolution]:
        """Resolve ``inputs``. With ``owner`` set, one call per account runs at a time; a second
        concurrent call is rejected (kind ``busy``) rather than queued behind the shared pacer."""
        if not inputs:
            raise ScanError("invalid", "nothing to resolve")
        if len(inputs) > MAX_RESOLVE_CARDS:
            raise ScanError("invalid", f"at most {MAX_RESOLVE_CARDS} cards per call")
        if owner is None:
            return await self._resolve(inputs)
        if owner in self._in_flight:
            raise ScanError("busy", "another card lookup for your account is still running; wait for it")
        self._in_flight.add(owner)
        token = art_owner.set(owner)  # gallery builds this call starts count against this member
        try:
            return await self._resolve(inputs)
        finally:
            art_owner.reset(token)
            self._in_flight.discard(owner)

    @contextlib.contextmanager
    def _lookup_slot(self, owner: str | None) -> Iterator[None]:
        """Name searches and printing lists share the gateway-wide Scryfall pacer. With ``owner``
        set, at most MAX_LOOKUPS_PER_USER run at once for that account; more fail fast (``busy``)
        so one account cannot queue up the pacer for everyone."""
        if owner is None:
            yield
            return
        if self._lookups.get(owner, 0) >= MAX_LOOKUPS_PER_USER:
            raise ScanError("busy", "other card lookups for your account are still running; wait for them")
        self._lookups[owner] = self._lookups.get(owner, 0) + 1
        try:
            yield
        finally:
            left = self._lookups.get(owner, 1) - 1
            if left > 0:
                self._lookups[owner] = left
            else:
                self._lookups.pop(owner, None)

    async def _resolve(self, inputs: list[CardInput]) -> list[Resolution]:
        # The wall-clock budget runs from the start of the call, batched pass included.
        budget = _Budget(MAX_RECOVERY_LOOKUPS_PER_CALL, time.monotonic() + self.call_seconds)
        results: list[Resolution | None] = [None] * len(inputs)

        # 1. One batched exact pass for everything that has a name or a printing.
        idents: list[dict[str, str]] = []
        owners: list[int] = []
        for i, inp in enumerate(inputs):
            if inp.set_code and inp.collector_number:
                idents.append({"set": inp.set_code, "collector_number": inp.collector_number})
            elif inp.name and inp.set_code:
                idents.append({"name": inp.name, "set": inp.set_code})
            else:
                idents.append({"name": inp.name})
            owners.append(i)
        try:
            async with asyncio.timeout(max(0.0, budget.deadline - time.monotonic())):
                found, missing = await self.scryfall.collection(idents)
        except TimeoutError:
            return [self._deferred(inp, "budget") for inp in inputs]
        except ScryfallError as exc:
            if exc.kind == "rate_limited":
                # Nothing has been looked up yet: hand everything back as deferred, with the wait.
                return [self._deferred(inp, "rate_limited") for inp in inputs]
            if exc.kind == "unavailable":
                raise ScanError(exc.kind, str(exc)) from exc
            found, missing = [], idents  # fall back to per-card lookups below
        # Scryfall returns found cards in identifier order with not_found ones removed.
        # Position alone is not trusted: each card must match its identifier, or the
        # input goes to the per-card fallback below.
        missing_keys = {_ident_key(m) for m in missing}
        checks: dict[int, dict[str, Any]] = {}  # printings whose title must be confirmed by name
        fi = 0
        for i, ident in zip(owners, idents, strict=True):
            if _ident_key(ident) in missing_keys or fi >= len(found):
                continue
            card = found[fi]
            if not _matches_ident(card, ident):
                continue
            fi += 1
            inp = inputs[i]
            status = "printing" if "collector_number" in ident else "exact"
            if status == "printing" and inp.name and not _same_name(inp.name, card["name"]):
                # The info line and the title disagree. A misread set code or number can point at
                # a card with a similar name (Sol Ring, Sol Talisman), so a similarity score is
                # not trusted: the title is resolved on its own below and must name the same card.
                checks[i] = card
                continue
            results[i] = Resolution(inp, status, summarize(card))

        # 2. Everything else: fuzzy, then suggestions. Recovery lookups share one budget per call.
        # A 429 from Scryfall pauses all lookups for its Retry-After window. Names already
        # resolved keep their matches; the rest come back as "deferred" with the wait time,
        # instead of the whole call failing.
        # The call also has a wall-clock budget (``call_seconds``): a long list of unreadable
        # names is answered within proxy and client timeouts, with the rest deferred.
        for i, inp in enumerate(inputs):
            if results[i] is not None:
                continue
            if budget.expired:
                for j in range(i, len(inputs)):
                    if results[j] is None:
                        results[j] = self._deferred(inputs[j], "budget")
                break
            if i in checks:
                inp = CardInput(inp.name, "", "", inp.quantity, inp.raw, inp.foil, inp.lang, inp.art_hash)
            try:
                async with asyncio.timeout(max(0.0, budget.deadline - time.monotonic())):
                    res = await self._resolve_one(inp, budget)
            except TimeoutError:
                for j in range(i, len(inputs)):
                    if results[j] is None:
                        results[j] = self._deferred(inputs[j], "budget")
                break
            except ScanError as exc:
                if exc.kind != "rate_limited":
                    raise
                for j in range(i, len(inputs)):
                    if results[j] is None:
                        results[j] = self._deferred(inputs[j], "rate_limited")
                break
            if i in checks:
                res = self._settle_printing(inputs[i], checks[i], res)
            results[i] = res
        # 3. Optional second opinion on the printing, for cards matched by name only. It shares
        # the call's lookup and time budget: each match costs one lookup and ends at the deadline.
        if self.art_matcher is not None:
            for i, inp in enumerate(inputs):
                res = results[i]
                if (
                    res is not None
                    and res.card
                    and inp.art_hash
                    and res.status in ("exact", "fuzzy", "printing")
                ):
                    if not budget.spend():
                        break
                    results[i] = await self._apply_art_match(inp, res, budget)
        return [r for r in results if r is not None]

    def _settle_printing(self, inp: CardInput, printed: dict[str, Any], by_name: Resolution) -> Resolution:
        """Decide between a set+number read and a title that does not plainly agree with it.

        ``by_name`` is what the title resolved to on its own. The same card: the printing is
        right and the title was read badly. A different card, matched exactly or confidently: the
        title wins, since a misread digit on the info line is the likelier error. Anything else
        (a title cut short, say): ambiguous, with both cards as suggestions, rather than a guess.
        """
        where = f"{inp.set_code.upper()} {inp.collector_number}"
        if by_name.card and _same_card(by_name.card, printed):
            return Resolution(inp, "printing", summarize(printed), f"title read as '{inp.name}'")
        if by_name.card is None and _unique_completion(inp.name, by_name.completions, printed["name"]):
            # A long title cut short by the strip: no card matched the fragment, but the fragment
            # completes to exactly one card name and it is the printed card's.
            return Resolution(inp, "printing", summarize(printed), f"title read as '{inp.name}'")
        confident = by_name.card is not None and (
            by_name.status == "exact"
            or _similarity(inp.name, by_name.card["name"]) >= self.t.fuzzy_confident_similarity
        )
        if confident:
            assert by_name.card is not None
            note = f"{where} is {printed['name']}, which does not match '{inp.name}'; matched by name instead"
            if by_name.note:
                note += f"; {by_name.note}"
            return Resolution(inp, by_name.status, by_name.card, note, by_name.suggestions, conflict=True)
        names = [printed["name"]] + ([by_name.card["name"]] if by_name.card else []) + by_name.suggestions
        sugg: list[str] = []
        for n in names:
            if n not in sugg:
                sugg.append(n)
        return Resolution(
            inp,
            "ambiguous",
            note=f"{where} is {printed['name']} but the title read '{inp.name}'; pick the right card",
            suggestions=sugg[:MAX_SUGGESTIONS],
            conflict=True,
        )

    async def _apply_art_match(self, inp: CardInput, res: Resolution, budget: _Budget) -> Resolution:
        """Let the optional art matcher pick the printing when only the name was matched."""
        assert self.art_matcher is not None and res.card is not None
        try:
            async with asyncio.timeout(max(0.0, budget.deadline - time.monotonic())):
                chosen = await self.art_matcher(inp, res.card)
        except TimeoutError:
            logger.warning("art matcher ran out of this call's time budget")
            return res
        except Exception:  # a second opinion must never break resolution
            logger.exception("art matcher failed")
            return res
        if not chosen:
            return res
        # A set and number read from the card decide the printing; art may only confirm or annotate.
        if chosen.get("name") and res.status != "printing" and _same_name(chosen["name"], res.card["name"]):
            res.card = summarize(chosen)
            res.note = (res.note + "; " if res.note else "") + "printing chosen by artwork"
        if isinstance(chosen.get("art"), dict):
            res.art = chosen["art"]
            if chosen["art"].get("note"):
                res.note = (res.note + "; " if res.note else "") + str(chosen["art"]["note"])
        return res

    def _deferred(self, inp: CardInput, why: str) -> Resolution:
        if why == "budget":
            note = "not looked up yet; this call's lookup time is used up, resolve the remaining names again"
        else:
            note = f"not looked up yet; Scryfall asked us to wait, try again in {self.scryfall.retry_in()} s"
        return Resolution(inp, "deferred", note=note)

    async def _resolve_one(self, inp: CardInput, budget: _Budget | None = None) -> Resolution:
        if budget is None:
            budget = _Budget(MAX_RECOVERY_LOOKUPS_PER_CALL, time.monotonic() + self.call_seconds)
        if inp.set_code and inp.collector_number and not inp.name:
            return Resolution(inp, "not_found", note=f"No card {inp.set_code.upper()} {inp.collector_number}")
        if not inp.name:
            return Resolution(inp, "not_found", note="no name given")
        note = ""
        try:
            card = await self.scryfall.named(inp.name, fuzzy=True, set_code=inp.set_code or None)
        except ScryfallError as exc:
            if exc.kind in ("unavailable", "rate_limited"):
                raise ScanError(exc.kind, str(exc)) from exc
            if exc.kind == "ambiguous":
                sugg = await self._suggest(inp.name)
                if not sugg:
                    recovered = await self._recover(inp, "", budget)
                    if recovered is not None:
                        return recovered
                return Resolution(
                    inp, "ambiguous", note=str(exc), suggestions=sugg[:MAX_SUGGESTIONS], completions=sugg
                )
            card = None
            if inp.set_code:
                # The printing was wrong but the name may still be a card.
                try:
                    card = await self.scryfall.named(inp.name, fuzzy=True)
                    note = f"not in {inp.set_code.upper()}; "
                except ScryfallError as exc2:
                    if exc2.kind == "rate_limited":
                        raise ScanError("rate_limited", str(exc2)) from exc2
                    card = None
            if card is None:
                sugg = await self._suggest(inp.name)
                if not sugg:
                    recovered = await self._recover(inp, note, budget)
                    if recovered is not None:
                        return recovered
                return Resolution(
                    inp, "not_found", note=str(exc), suggestions=sugg[:MAX_SUGGESTIONS], completions=sugg
                )
        summary = summarize(card)
        if _same_name(inp.name, card["name"]):
            return Resolution(inp, "exact", summary, note.rstrip("; "))
        score = _similarity(inp.name, card["name"])
        if score >= self.t.fuzzy_confident_similarity:
            # Close enough that a second lookup for alternatives would be wasted pacing time.
            return Resolution(inp, "fuzzy", summary, f"{note}matched '{card['name']}' from '{inp.name}'")
        # Scryfall's fuzzy match happily returns an unrelated card for short or
        # garbled names ("Sol Rng" gives Oathsworn Giant). Only accept it when it
        # actually resembles what was read; otherwise hand back suggestions.
        suggestions = await self._suggest(inp.name)
        best_sugg = max(suggestions, key=lambda n: _similarity(inp.name, n), default=None)
        if best_sugg and _similarity(inp.name, best_sugg) > max(score, 0.6) and best_sugg != card["name"]:
            try:
                card = await self.scryfall.named(best_sugg)
                summary = summarize(card)
                score = _similarity(inp.name, card["name"])
            except ScryfallError as exc:
                if exc.kind == "rate_limited":
                    raise ScanError("rate_limited", str(exc)) from exc
        if score < self.t.fuzzy_min_similarity:
            sugg = [card["name"]] + [s for s in suggestions if s != card["name"]]
            return Resolution(
                inp,
                "ambiguous",
                note=f"'{inp.name}' is not close enough to any card name",
                suggestions=sugg[:MAX_SUGGESTIONS],
                completions=suggestions,
            )
        return Resolution(
            inp,
            "fuzzy",
            summary,
            f"{note}matched '{card['name']}' from '{inp.name}'",
            [s for s in suggestions if s != card["name"]][:MAX_SUGGESTIONS],
            completions=suggestions,
        )

    async def _recover(self, inp: CardInput, note: str, budget: _Budget) -> Resolution | None:
        """Last resort for a name Scryfall cannot place: OCR-confusion swaps, then shorter prefixes.

        Never assigns a card that does not resemble what was read (same similarity guard as
        the fuzzy path), and reports a tie between candidates as ambiguous instead of picking.
        ``budget`` is what the whole call may still spend (extra lookups and wall-clock time), so
        a list full of garbage cannot run the gateway into Scryfall's rate limit or a timeout.
        """
        name = inp.name
        spend = budget.spend

        # 1. Common OCR confusions ("S0l Ring", "Sol Rlng", "Cultlvate", "Rarnpant Growth").
        for variant in ocr_variants(name, MAX_VARIANT_LOOKUPS):
            if not spend():
                return None
            try:
                card = await self.scryfall.named(variant, fuzzy=True)
            except ScryfallError as exc:
                if exc.kind in ("unavailable", "rate_limited"):
                    raise ScanError(exc.kind, str(exc)) from exc
                continue
            if _similarity(name, card["name"]) >= self.t.fuzzy_min_similarity:
                return Resolution(
                    inp,
                    "fuzzy",
                    summarize(card),
                    f"{note}matched '{card['name']}' from '{name}' after correcting likely scan errors",
                )
        # 2. Autocomplete on a shorter and shorter prefix ("Sol R", "Cult").
        tried: set[str] = set()
        lookups = 0
        for cut in range(len(name) - 1, MIN_PREFIX - 1, -1):
            prefix = name[:cut].rstrip()
            if len(prefix) < MIN_PREFIX or prefix in tried:
                continue
            tried.add(prefix)
            if lookups >= MAX_PREFIX_LOOKUPS or not spend():
                break
            lookups += 1
            try:
                names = await self.scryfall.autocomplete(prefix)
            except ScryfallError as exc:
                if exc.kind in ("unavailable", "rate_limited"):
                    raise ScanError(exc.kind, str(exc)) from exc
                continue
            ranked = _rank_candidates(name, names, self.t.fuzzy_min_similarity)
            if not ranked:
                continue
            suggestions = [n for _, n in ranked][:MAX_SUGGESTIONS]
            if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < self.t.ambiguity_margin:
                return Resolution(
                    inp,
                    "ambiguous",
                    note=f"'{name}' could be any of several cards",
                    suggestions=suggestions,
                )
            if not spend():
                return None
            try:
                card = await self.scryfall.named(ranked[0][1])
            except ScryfallError as exc:
                if exc.kind in ("unavailable", "rate_limited"):
                    raise ScanError(exc.kind, str(exc)) from exc
                continue
            return Resolution(
                inp,
                "fuzzy",
                summarize(card),
                f"{note}matched '{card['name']}' from '{name}' by its first letters",
                suggestions[1:],
            )
        return None

    async def _suggest(self, name: str) -> list[str]:
        """All of Scryfall's completions (up to 20); callers shorten what they show."""
        try:
            return await self.scryfall.autocomplete(name)
        except ScryfallError as exc:
            if exc.kind == "rate_limited":
                raise ScanError("rate_limited", str(exc)) from exc
            return []

    async def printings(self, oracle_id: str, *, owner: str | None = None) -> dict[str, Any]:
        """Every printing of one card for the picker, as card summaries, newest first.

        ``{"cards": [...], "has_more": bool, "total_cards": int}``; ``has_more`` means the card has
        more printings than the one page shown (Scryfall pages at 175).
        """
        oid = str(oracle_id or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", oid):
            raise ScanError("invalid", "oracle_id must be a Scryfall oracle id")
        try:
            with self._lookup_slot(owner):
                return await self.scryfall.prints(oid)
        except ScryfallError as exc:
            if exc.kind in ("unavailable", "rate_limited", "not_found"):
                raise ScanError(exc.kind, str(exc)) from exc
            raise ScanError("unavailable", str(exc)) from exc

    async def suggest(self, query: str, *, owner: str | None = None) -> list[str]:
        """Card names for typed text: from the in-memory catalog (names.py) when it is loaded,
        which costs no Scryfall call; otherwise Scryfall's autocomplete while it loads."""
        query = query.strip()
        if len(query) < 2 or len(query) > 100:
            return []
        self.names.ensure()
        local = self.names.suggest(query)
        if local is not None:
            return local
        try:
            with self._lookup_slot(owner):
                return (await self.scryfall.autocomplete(query))[:20]
        except ScryfallError as exc:
            if exc.kind in ("unavailable", "rate_limited"):
                raise ScanError(exc.kind, str(exc)) from exc
            return []

    def describe(self, results: list[Resolution]) -> dict[str, Any]:
        items = [r.as_dict() for r in results]
        counts = {s: 0 for s in STATUSES}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
        resolved = [it for it in items if it["card"]]
        deferred = counts.get("deferred", 0)
        out = {
            "complete": deferred == 0,
            "card_count": sum(it["quantity"] for it in resolved),
            "distinct": len(resolved),
            "needs_review": [i for i, r in enumerate(results) if r.status not in ("exact", "printing")],
            "status_counts": {k: v for k, v in counts.items() if v},
            "cards": items,
            "decklist_text": decklist_text(resolved),
            **change_fields(resolved),
        }
        if deferred:
            n = self.scryfall.retry_in()
            out["deferred"] = deferred
            out["retry_in"] = n
            head = f"{deferred} of {len(results)} names were not looked up"
            if n:
                out["message"] = (
                    f"{head}: Scryfall asked us to wait. Keep the resolved cards and resolve the "
                    f"deferred names again in about {n} s."
                )
            else:
                out["message"] = (
                    f"{head}: this call's lookup time was used up. Keep the resolved cards and resolve "
                    "the deferred names again now."
                )
        return out

    # -- sessions -------------------------------------------------------------
    def new_session(self, sub: str, name: str, items: list[dict[str, Any]], *, source: str) -> dict[str, Any]:
        name = _clean_session_name(name)
        items = _clean_items(items)
        sid = "scan_" + secrets.token_urlsafe(9)
        now = int(time.time())
        row = {
            "id": sid,
            "owner_sub": sub,
            "name": name,
            "status": "open",
            "source": source,
            "items": items,
            "created_at": now,
            "updated_at": now,
        }
        self.store.save(row)
        self.store.prune(sub)
        self.db.audit(
            "scan_session_created", sub=sub, detail={"id": sid, "items": len(items), "source": source}
        )
        return self.describe_session(row)

    def update_session(self, sub: str, sid: str, **fields: Any) -> dict[str, Any]:
        row = self.store.get(sid, sub)
        if not row:
            raise ScanError("not_found", "no such scan session for your account")
        if "name" in fields and fields["name"] is not None:
            row["name"] = _clean_session_name(str(fields["name"]))
        if "items" in fields and fields["items"] is not None:
            row["items"] = _clean_items(fields["items"])
        if "status" in fields and fields["status"] is not None:
            if fields["status"] not in ("open", "done"):
                raise ScanError("invalid", "status must be open or done")
            row["status"] = fields["status"]
        row["updated_at"] = int(time.time())
        self.store.save(row)
        return self.describe_session(row)

    def delete_session(self, sub: str, sid: str) -> None:
        if not self.store.delete(sid, sub):
            raise ScanError("not_found", "no such scan session for your account")
        self.db.audit("scan_session_deleted", sub=sub, detail={"id": sid})

    def get_session(self, sub: str, sid: str) -> dict[str, Any]:
        row = self.store.get(sid, sub)
        if not row:
            raise ScanError("not_found", "no such scan session for your account")
        return self.describe_session(row)

    def find_session(self, sub: str, ref: str) -> dict[str, Any]:
        """By id, or by (case-insensitive) name, newest first."""
        ref = ref.strip()
        row = self.store.get(ref, sub) if ref.startswith("scan_") else None
        if row is None:
            for r in self.store.list(sub):
                if r["name"].lower() == ref.lower():
                    row = self.store.get(r["id"], sub)
                    break
        if row is None:
            raise ScanError("not_found", f"no scan session named or numbered '{ref[:40]}' for your account")
        return self.describe_session(row)

    def list_sessions(self, sub: str) -> list[dict[str, Any]]:
        return self.store.list(sub)

    def guessed_names(self, sub: str) -> dict[str, str]:
        """Card names this member's scans matched from a misread name: matched name (lower case)
        -> what was read. A proposal adding one of these cards says the name was guessed."""
        out: dict[str, str] = {}
        for listed in self.store.list(sub):
            row = self.store.get(listed["id"], sub)
            for it in (row or {}).get("items", []):
                card = it.get("card") or {}
                read = str((it.get("input") or {}).get("name") or "")
                if it.get("status") == "fuzzy" and card.get("name") and read:
                    out.setdefault(str(card["name"]).lower(), read)
        return out

    @staticmethod
    def describe_session(row: dict[str, Any]) -> dict[str, Any]:
        items = row["items"]
        resolved = [it for it in items if it.get("card")]
        return {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "source": row["source"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "item_count": len(items),
            "card_count": sum(int(it.get("quantity", 1)) for it in items),
            "unresolved": len(items) - len(resolved),
            "items": items,
            "decklist_text": decklist_text(items),
            **change_fields(resolved),
        }


def _matches_ident(card: dict[str, Any], ident: dict[str, Any]) -> bool:
    if "collector_number" in ident:
        return (
            str(card.get("set", "")).lower() == ident["set"].lower()
            and str(card.get("collector_number", "")).lower() == ident["collector_number"].lower()
        )
    if "set" in ident and str(card.get("set", "")).lower() != ident["set"].lower():
        return False
    return _same_name(ident.get("name", ""), str(card.get("name", "")))


def _ident_key(ident: dict[str, Any]) -> str:
    return "|".join(f"{k}={str(v).lower()}" for k, v in sorted(ident.items()))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# OCR of printed card names: digits that are really letters, and letter shapes that merge.
_DIGITS_TO_L = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "B"})
_DIGITS_TO_I = str.maketrans({"0": "o", "1": "i", "5": "s", "8": "B"})
_SWAPS = (("l", "i"), ("i", "l"), ("I", "l"), ("rn", "m"), ("m", "rn"))


def ocr_variants(name: str, limit: int) -> list[str]:
    """Up to ``limit`` plausible corrections of ``name``: digits folded to letters, then single swaps."""
    out: list[str] = []

    def add(v: str) -> None:
        if v != name and v not in out and len(out) < limit:
            out.append(v)

    folded = name.translate(_DIGITS_TO_L)
    add(folded)
    add(name.translate(_DIGITS_TO_I))
    for a, b in _SWAPS:
        start = 0
        while (pos := folded.find(a, start)) != -1:
            add(folded[:pos] + b + folded[pos + len(a) :])
            start = pos + 1
    return out


def _rank_candidates(name: str, names: list[str], min_similarity: float) -> list[tuple[float, str]]:
    """Autocomplete results that pass the similarity guard, best first, one per card face."""
    seen: set[str] = set()
    ranked: list[tuple[float, str]] = []
    for n in names:
        key = _norm(n.split("//")[0])
        if key in seen:
            continue
        seen.add(key)
        score = _similarity(name, n)
        if score >= min_similarity:
            ranked.append((score, n))
    ranked.sort(key=lambda t: -t[0])
    return ranked


def _similarity(a: str, b: str) -> float:
    """0..1 resemblance of two names, ignoring case, punctuation and a split card's back half."""
    import difflib

    na, nb = _norm(a), _norm(b)
    front = _norm(b.split("//")[0])
    return max(
        difflib.SequenceMatcher(None, na, nb).ratio(), difflib.SequenceMatcher(None, na, front).ratio()
    )


def _same_name(a: str, b: str) -> bool:
    return _norm(a) == _norm(b) or _norm(a) == _norm(b.split("//")[0])


def _front(name: str) -> str:
    """Normalized front-face name: Scryfall lists some cards twice, once as "Name // Name"."""
    return _norm(name.split("//")[0])


def _unique_completion(title: str, completions: list[str], name: str) -> bool:
    """``title`` is a start of ``name`` and ``name`` is the only card the title completes to."""
    t = _norm(title)
    if not t or not _front(name).startswith(t):
        return False
    starts = {_front(s) for s in completions if _front(s).startswith(t)}
    return starts == {_front(name)}


def _same_card(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Same oracle card (any printing); falls back to the name when an oracle id is missing."""
    if a.get("oracle_id") and b.get("oracle_id"):
        return a["oracle_id"] == b["oracle_id"]
    return _same_name(str(a.get("name", "")), str(b.get("name", "")))


def _clean_session_name(name: str) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not name:
        name = time.strftime("Scan %Y-%m-%d %H:%M", time.gmtime())
    return name[:80]


_ITEM_CARD_KEYS = (
    "name",
    "scryfall_id",
    "oracle_id",
    "set",
    "set_name",
    "collector_number",
    "rarity",
    "type_line",
    "mana_cost",
    "mana_value",
    "color_identity",
    "layout",
    "lang",
    "released_at",
    "image_small",
    "image_normal",
    "image_art",
    "scryfall_uri",
    "legal_commander",
    "finishes",
)


def _clean_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ScanError("invalid", "items must be a list")
    if len(raw) > MAX_CARDS:
        raise ScanError("invalid", f"at most {MAX_CARDS} items per session")
    out: list[dict[str, Any]] = []
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            raise ScanError("invalid", f"item {i} must be an object")
        qty = it.get("quantity", 1)
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1 or qty > 999:
            raise ScanError("invalid", f"item {i}: quantity must be an integer from 1 to 999")
        card = it.get("card")
        clean_card = None
        if card is not None:
            if not isinstance(card, dict) or not isinstance(card.get("name"), str):
                raise ScanError("invalid", f"item {i}: card must be an object with a name")
            clean_card = {}
            for k in _ITEM_CARD_KEYS:
                v = card.get(k)
                if isinstance(v, str):
                    if k.startswith("image_") or k == "scryfall_uri":
                        if not (
                            v.startswith("https://cards.scryfall.io/")
                            or v.startswith("https://scryfall.com/")
                        ):
                            continue
                    clean_card[k] = v[:200]
                elif isinstance(v, int | float) and not isinstance(v, bool):
                    clean_card[k] = v
                elif isinstance(v, list) and all(isinstance(x, str) for x in v):
                    clean_card[k] = [x[:40] for x in v[:10]]
        status = str(it.get("status") or ("exact" if clean_card else "not_found"))
        if status not in STATUSES:
            status = "exact" if clean_card else "not_found"
        clean = {
            "quantity": qty,
            "name": str(it.get("name") or (clean_card or {}).get("name") or "")[:200],
            "status": status,
            "note": str(it.get("note") or "")[:300],
            "card": clean_card,
        }
        if isinstance(it.get("foil"), bool):
            clean["foil"] = it["foil"]
        out.append(clean)
    if len(json.dumps(out, separators=(",", ":"))) > MAX_SESSION_BYTES:
        raise ScanError("invalid", f"this scan session is too large (over {MAX_SESSION_BYTES // 1000} KB)")
    return out
