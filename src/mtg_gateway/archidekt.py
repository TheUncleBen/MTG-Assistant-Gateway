"""Archidekt provider adapter.

Endpoints come from the handoff's endpoint map (third-party source reading, not
Archidekt documentation). Nothing here has been run against archidekt.com; the
tests use recorded fixtures. Every response is validated and the adapter fails
closed on an unexpected shape. Requests go through one global pacer (minimum
interval between calls, Retry-After honoured, circuit breaker after repeated
failures) so a chatty assistant cannot hammer the provider.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TOKEN_FIELDS = ("access_token", "access", "token", "jwt")

# Archidekt's numeric deck formats as observed by the nccurry/mtg-mcp reference (reported, not verified).
FORMAT_IDS = {
    "standard": 1,
    "modern": 2,
    "commander": 3,
    "edh": 3,
    "legacy": 4,
    "vintage": 5,
    "pauper": 6,
    "pioneer": 7,
    "brawl": 8,
    "historic": 9,
    "oathbreaker": 10,
}
# Reverse map, one name per id (3 reads back as "commander", not "edh").
FORMAT_NAMES: dict[int, str] = {}
for _name, _fid in FORMAT_IDS.items():
    FORMAT_NAMES.setdefault(_fid, _name)
REFRESH_FIELDS = ("refresh_token", "refresh")


class ArchidektError(Exception):
    """Provider failure.

    ``kind`` is one of auth, not_found, rate_limited, unavailable, contract, forbidden.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


BACKUP_FOLDER_NAME = "MTG Gateway backups"
DECK_ID_RE = re.compile(r"[0-9]{1,12}")


def backup_name(deck_name: str, when: float) -> str:
    """\"<deck> (backup YYYY-MM-DD HH:MM UTC)\", readable by people and agents alike."""
    return f"{deck_name} (backup {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(when))})"


@dataclass
class DeckCard:
    relation_id: int | None
    card_id: int | None
    name: str
    quantity: int
    categories: list[str]
    modifier: str
    set_code: str
    collector_number: str
    # Oracle and printing details from Archidekt's deck JSON (all optional; the live payload
    # sends nulls freely). None of these feed Deck.fingerprint().
    oracle_name: str = ""
    cmc: float | None = None
    mana_cost: str = ""
    colors: list[str] = field(default_factory=list)
    color_identity: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    subtypes: list[str] = field(default_factory=list)
    supertypes: list[str] = field(default_factory=list)
    rarity: str = ""
    price: float | None = None
    legalities: dict[str, str] = field(default_factory=dict)
    edhrec_rank: int | None = None
    salt: float | None = None
    game_changer: bool = False
    tutor: bool = False
    extra_turns: bool = False
    mass_land_denial: bool = False
    mana_production: dict[str, int] | None = None
    two_card_combo_ids: list[str] = field(default_factory=list)
    notes: str = ""  # the user's own text: carried as is, never interpreted
    label: str = ""
    companion: bool = False
    image_hash: str = ""
    scryfall_uid: str = ""
    default_category: str = ""  # Archidekt's auto category for cards with categories null


@dataclass
class Deck:
    id: str
    name: str
    owner: str
    updated_at: str
    cards: list[DeckCard]
    categories: list[dict[str, Any]]
    raw: dict[str, Any] = field(repr=False)
    description: str = ""  # the user's own text (Archidekt stores Quill JSON here); never interpreted
    format_id: int | None = None
    format: str | None = None  # FORMAT_NAMES[format_id], None when unknown
    edh_bracket: int | None = None
    private: bool = False
    unlisted: bool = False
    tags: list[str] = field(default_factory=list)
    created_at: str = ""

    def _rows(self) -> list[tuple[Any, ...]]:
        return sorted(
            (c.relation_id or 0, c.card_id or 0, c.name, c.quantity, tuple(sorted(c.categories)), c.modifier)
            for c in self.cards
        )

    def fingerprint(self) -> str:
        """Stable digest of the card relations and metadata that an edit would touch."""
        blob = json.dumps(
            {"id": self.id, "updated_at": self.updated_at, "rows": self._rows()}, sort_keys=True
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def rows_fingerprint(self) -> str:
        """Like fingerprint() without the deck's updated time, which other actions (such as
        copying the deck for a backup) may bump without changing a card."""
        blob = json.dumps({"id": self.id, "rows": self._rows()}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def excluded_categories(self) -> set[str]:
        """Category names the deck marks as not part of the deck (Maybeboard, Sideboard...)."""
        return {
            str(c.get("name"))
            for c in self.categories
            if isinstance(c.get("name"), str) and c.get("includedInDeck") is False
        }

    def in_deck(self, card: DeckCard) -> bool:
        """A card counts as in the deck unless every one of its categories is excluded.
        Uncategorized cards count as in the deck (as Mystic Forge does)."""
        if not card.categories:
            return True
        excluded = self.excluded_categories()
        return any(cat not in excluded for cat in card.categories)

    @property
    def main_cards(self) -> list[DeckCard]:
        return [c for c in self.cards if self.in_deck(c)]

    @property
    def side_cards(self) -> list[DeckCard]:
        return [c for c in self.cards if not self.in_deck(c)]

    def counts_by_name(self) -> dict[str, int]:
        """Card counts for the deck proper; maybeboard and sideboard copies are left out."""
        out: dict[str, int] = {}
        for c in self.main_cards:
            out[c.name] = out.get(c.name, 0) + c.quantity
        return out


def jwt_exp(token: str) -> int | None:
    """The ``exp`` claim of a JWT, read without verifying the signature (we only use it to
    decide when to refresh; Archidekt verifies the token). None when anything is off."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = claims.get("exp") if isinstance(claims, dict) else None
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        return int(exp)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def front_face(name: str) -> str:
    """'Delver of Secrets // Insectile Aberration' -> 'Delver of Secrets'; other names unchanged."""
    return name.split(" // ", 1)[0].strip()


_SCRYFALL_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SET_CODE_RE = re.compile(r"[A-Za-z0-9]{2,6}")


def _printing(card: dict[str, Any], *, exact: bool) -> dict[str, Any]:
    cid = card.get("id")
    if isinstance(cid, bool) or not isinstance(cid, int):
        raise ArchidektError("contract", "card search result has no integer id")
    edition = card.get("edition") if isinstance(card.get("edition"), dict) else {}
    oracle = card.get("oracleCard") if isinstance(card.get("oracleCard"), dict) else {}
    options = card.get("options")
    return {
        "id": cid,
        "name": str(oracle.get("name", "")),
        "set_code": str(edition.get("editioncode", "") or ""),
        "collector_number": str(card.get("collectorNumber", "") or ""),
        "options": [str(o) for o in options] if isinstance(options, list) else [],
        "exact_printing": exact,
    }


def finish_modifier(options: list[str], *, foil: bool = False, etched: bool = False) -> str:
    """The modifyCards ``modifier`` for the wanted finish ("Normal", "Foil" or "Etched", the
    values Archidekt's site sends; Foil and Etched verified live 2026-10-05). A finish the
    printing does not offer falls back to "Normal" when offered, else the printing's only
    finish; with no option list the wanted finish is sent as is."""
    want = "Etched" if etched else "Foil" if foil else "Normal"
    if not options or want in options:
        return want
    return "Normal" if "Normal" in options else options[0]


def _card_matches(card: dict[str, Any], name: str) -> bool:
    oracle = (
        str(card.get("oracleCard", {}).get("name", "")) if isinstance(card.get("oracleCard"), dict) else ""
    )
    want = name.casefold().strip()
    return oracle.casefold() == want or front_face(oracle).casefold() == front_face(name).casefold()


def _prefer_printing(
    hits: list[dict[str, Any]], set_code: str | None, collector_number: str | None
) -> dict[str, Any]:
    if not set_code and not collector_number:
        return hits[0]

    def score(c: dict[str, Any]) -> int:
        edition = c.get("edition") if isinstance(c.get("edition"), dict) else {}
        code = str(edition.get("editioncode", "")).casefold()
        number = str(c.get("collectorNumber", "")).casefold()
        s = 0
        if set_code and code == set_code.casefold():
            s += 2
        if collector_number and number == collector_number.casefold():
            s += 1
        return s

    return max(hits, key=score)  # stable: the first hit wins ties


def _owned_by(owner: Any, username: str, user_id: str | None) -> bool:
    """True only when a deck entry's owner field names the linked account."""
    if isinstance(owner, dict):
        if user_id and owner.get("id") is not None and str(owner["id"]) == str(user_id):
            return True
        return str(owner.get("username", "")).casefold() == username.casefold()
    if isinstance(owner, str):
        return owner.casefold() == username.casefold()
    return False


class Pacer:
    """Global request pacing shared by every user: one call at a time, a minimum
    gap between calls, Retry-After respected, and a breaker that opens for a
    minute after five consecutive failures."""

    MAX_RETRY_AFTER = 120.0  # a larger (or garbled) Retry-After must not stall every user for longer

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0
        self._failures = 0
        self._open_until = 0.0
        self._retry_until = 0.0  # set by a 429's Retry-After: calls are refused until then

    async def __aenter__(self) -> None:
        await self._lock.acquire()
        try:
            now = time.monotonic()
            if now < self._open_until:
                raise ArchidektError(
                    "unavailable", "Archidekt requests are paused after repeated failures; try later"
                )
            if now < self._retry_until:
                raise ArchidektError(
                    "rate_limited",
                    f"Archidekt asked us to slow down; try again in {int(self._retry_until - now) + 1} s",
                )
            if now < self._next_allowed:
                await asyncio.sleep(self._next_allowed - now)
        except BaseException:
            # a cancelled or failed wait must never leave the shared lock held
            self._lock.release()
            raise

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any
    ) -> None:
        self._next_allowed = time.monotonic() + self.min_interval
        self._lock.release()

    def record(self, ok: bool, retry_after: float | None = None) -> None:
        if ok:
            self._failures = 0
            return
        self._failures += 1
        if retry_after:
            wait = min(retry_after, self.MAX_RETRY_AFTER)
            self._retry_until = max(self._retry_until, time.monotonic() + wait)
        if self._failures >= 5:
            self._open_until = time.monotonic() + 60
            self._failures = 0


class ArchidektClient:
    def __init__(
        self, base_url: str, user_agent: str, pacer: Pacer, *, http: httpx.AsyncClient | None = None
    ):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.pacer = pacer
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- plumbing -----------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        scheme: str = "JWT",
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Accept": "application/json", "User-Agent": self.user_agent}
        if token:
            headers["Authorization"] = f"{scheme} {token}"
        async with self.pacer:
            try:
                resp = await self._http.request(
                    method, f"{self.base_url}{path}", headers=headers, json=json_body, params=params
                )
            except httpx.HTTPError as exc:
                self.pacer.record(False)
                raise ArchidektError(
                    "unavailable", f"Archidekt request failed: {type(exc).__name__}"
                ) from exc
            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After")
                self.pacer.record(False, float(ra) if ra and ra.isdigit() else 30.0)
                raise ArchidektError("rate_limited", "Archidekt asked us to slow down; try again in a minute")
            if resp.status_code in (401, 403):
                self.pacer.record(True)
                raise ArchidektError(
                    "auth" if resp.status_code == 401 else "forbidden",
                    "Archidekt rejected the stored session; relink your account",
                )
            if resp.status_code == 404:
                self.pacer.record(True)
                raise ArchidektError("not_found", "Archidekt could not find that deck")
            if resp.status_code >= 500:
                self.pacer.record(False)
                raise ArchidektError("unavailable", f"Archidekt returned HTTP {resp.status_code}")
            if resp.status_code >= 400:
                self.pacer.record(True)
                raise ArchidektError("contract", f"Archidekt rejected the request (HTTP {resp.status_code})")
            self.pacer.record(True)
            if resp.status_code == 204 or not resp.content:
                return None
            try:
                return resp.json()
            except ValueError as exc:
                raise ArchidektError("contract", "Archidekt returned a non-JSON body") from exc

    # -- auth ---------------------------------------------------------------
    async def login(self, username_or_email: str, password: str) -> dict[str, Any]:
        """Exchange credentials once for provider session tokens. The password is
        used here and nowhere else; callers must not store it."""
        key = "email" if "@" in username_or_email else "username"
        body = await self._request(
            "POST", "/rest-auth/login/", json_body={key: username_or_email, "password": password}
        )
        if not isinstance(body, dict):
            raise ArchidektError("contract", "unexpected login response shape")
        access = next((body[k] for k in TOKEN_FIELDS if isinstance(body.get(k), str) and body[k]), None)
        if access is None:
            raise ArchidektError("contract", "Archidekt login did not return a recognised token field")
        refresh = next((body[k] for k in REFRESH_FIELDS if isinstance(body.get(k), str)), None)
        user = body.get("user") if isinstance(body.get("user"), dict) else {}
        return {
            "access": access,
            "refresh": refresh,
            "user_id": str(user.get("id")) if user.get("id") is not None else None,
            "username": str(user.get("username") or username_or_email),
        }

    async def refresh(self, refresh_token: str) -> str:
        """Trade a refresh token for a new access token: POST /rest-auth/token/refresh/
        {"refresh"} -> {"access", "access_token_expiration"} (verified live 2026-10-05; the
        refresh token is not rotated). A rejected refresh token (400 or 401) is ``auth``: the
        link is dead and the user has to sign in again."""
        try:
            body = await self._request(
                "POST", "/rest-auth/token/refresh/", json_body={"refresh": refresh_token}
            )
        except ArchidektError as exc:
            if exc.kind in ("auth", "forbidden", "contract"):
                raise ArchidektError("auth", "Archidekt rejected the stored refresh token; relink") from exc
            raise
        if not isinstance(body, dict):
            raise ArchidektError("contract", "unexpected token refresh response shape")
        access = next((body[k] for k in TOKEN_FIELDS if isinstance(body.get(k), str) and body[k]), None)
        if access is None:
            raise ArchidektError("contract", "Archidekt token refresh did not return an access token")
        return access

    # -- reads --------------------------------------------------------------
    async def list_decks(
        self,
        token: str,
        username: str,
        user_id: str | None = None,
        *,
        exclude_folder: str | None = None,
    ) -> list[dict[str, Any]]:
        """The linked user's decks. Archidekt honours ``ownerUsername`` (verified live 2026-10-04;
        the older ``owner``/``ownerexact`` parameters are ignored and return everyone's decks).
        When the linked account's id is known a second ``ownerId`` listing is merged in. Both
        are sent with the usual ``JWT`` scheme: Archidekt treats ``Bearer`` as signed out and then
        leaves the owner's private decks out (verified live 2026-10-05, both filters returned
        exactly the account's private decks with JWT).
        Every entry is still checked against the linked account on our side, and an entry whose
        owner cannot be read is dropped rather than shown as the user's. Entries in the folder
        named ``exclude_folder`` (the gateway's backup copies) are left out."""
        filters: list[dict[str, Any]] = [{"ownerUsername": username}]
        if user_id:
            filters.append({"ownerId": user_id})
        decks: list[dict[str, Any]] = []
        seen: set[str] = set()
        dropped = 0
        for flt in filters:
            body = await self._request(
                "GET",
                "/decks/v3/",
                token=token,
                params={**flt, "orderBy": "-updatedAt", "pageSize": 50},
            )
            results = body.get("results") if isinstance(body, dict) else None
            if not isinstance(results, list):
                raise ArchidektError("contract", "unexpected deck list shape")
            for d in results:
                if not isinstance(d, dict) or "id" not in d:
                    raise ArchidektError("contract", "unexpected deck entry shape")
                if not _owned_by(d.get("owner"), username, user_id):
                    dropped += 1
                    continue
                deck_id = str(d["id"])
                if deck_id in seen:
                    continue
                seen.add(deck_id)
                folder = d.get("parentFolderName")
                folder = str(folder) if isinstance(folder, str) and folder else None
                if exclude_folder and folder == exclude_folder:
                    continue  # the gateway's backup copies stay out of the user's list
                fmt = d.get("deckFormat")
                colors = d.get("colors") if isinstance(d.get("colors"), dict) else {}
                raw_tags = d.get("tags") if isinstance(d.get("tags"), list) else []
                bracket = d.get("edhBracket")
                size = d.get("size")
                decks.append(
                    {
                        "id": deck_id,
                        "name": str(d.get("name", "")),
                        "format": fmt,
                        "format_name": FORMAT_NAMES.get(fmt) if isinstance(fmt, int) else None,
                        "updated_at": str(d.get("updatedAt", "")),
                        "created_at": str(d.get("createdAt", "")),
                        "private": bool(d.get("private", False)),
                        "unlisted": bool(d.get("unlisted", False)),
                        "folder": folder,
                        # The list row's own extras (verified in the public v3 listing 2026-10-05):
                        # colour-identity pip counts, deck size, bracket and tag names.
                        "colors": {k: v for k, v in colors.items() if k in "WUBRG" and isinstance(v, int)},
                        "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
                        "bracket": bracket
                        if isinstance(bracket, int) and not isinstance(bracket, bool)
                        else None,
                        "tags": [
                            str(t.get("name") or t.get("tag"))
                            for t in raw_tags
                            if isinstance(t, dict) and (t.get("name") or t.get("tag"))
                        ],
                        "owner": username,
                    }
                )
        if dropped:
            logger.info("deck list: dropped %d entries not owned by the linked account", dropped)
        decks.sort(key=lambda d: d["updated_at"], reverse=True)
        return decks

    async def get_deck(self, token: str | None, deck_id: str) -> Deck:
        """Fetch a deck. With ``token=None`` this is an anonymous read of a public deck."""
        body = await self._request("GET", f"/decks/{deck_id}/", token=token)
        return parse_deck(body)

    async def create_deck(
        self,
        token: str,
        *,
        name: str,
        format_id: int,
        description: str = "",
        private: bool = True,
        unlisted: bool = False,
    ) -> Deck:
        """POST /decks/v2/ (route and body per the nccurry reference). Live Archidekt creates the
        deck but answers with a body that is not a full deck (verified 2026-10-05). Then this
        returns an empty Deck carrying only the new id, so the caller records the id before
        filling the deck and verifies it by reading it back."""
        body = await self._request(
            "POST",
            "/decks/v2/",
            token=token,
            json_body={
                "name": name,
                "description": description,
                "deckFormat": format_id,
                "edhBracket": None,
                "parentFolder": None,
                "private": private,
                "unlisted": unlisted,
                "theorycrafted": False,
                "game": None,
                "cardPackage": None,
                "extras": {
                    "decksToInclude": [],
                    "commandersToAdd": [],
                    "forceCardsToSingleton": False,
                    "ignoreCardsOutOfCommanderIdentity": True,
                },
            },
        )
        if isinstance(body, dict) and isinstance(body.get("cards"), list) and "id" in body:
            return parse_deck(body)
        new_id = body.get("id") if isinstance(body, dict) else None
        if (
            isinstance(new_id, bool)
            or not isinstance(new_id, (int, str))
            or not DECK_ID_RE.fullmatch(str(new_id))
        ):
            keys = sorted(body)[:20] if isinstance(body, dict) else type(body).__name__
            raise ArchidektError("contract", f"deck create response has no deck id (fields: {keys})")
        return Deck(id=str(new_id), name=name, owner="", updated_at="", cards=[], categories=[], raw=body)

    # -- backups ------------------------------------------------------------
    async def ensure_folder(self, token: str, name: str = BACKUP_FOLDER_NAME) -> dict[str, str]:
        """``{id, name}`` of the private folder ``name`` directly under the account's root folder, created
        there if missing. Routes per Archidekt's own site code: GET /decks/folderTree/ (root
        with ``children``) and POST /decks/folders/ {name, private, parentFolder} (verified
        live 2026-10-05)."""
        tree = await self._request("GET", "/decks/folderTree/", token=token)
        if not isinstance(tree, dict) or not isinstance(tree.get("id"), int):
            raise ArchidektError("contract", "unexpected folder tree shape")
        for child in tree.get("children") or []:
            if isinstance(child, dict) and child.get("name") == name and isinstance(child.get("id"), int):
                return {"id": str(child["id"]), "name": name}
        made = await self._request(
            "POST",
            "/decks/folders/",
            token=token,
            json_body={"name": name, "private": True, "parentFolder": tree["id"]},
        )
        if not isinstance(made, dict) or not isinstance(made.get("id"), int):
            raise ArchidektError("contract", "unexpected folder create response")
        return {"id": str(made["id"]), "name": name}

    async def backup_deck(
        self, token: str, deck: Deck, *, name: str, folder_id: str, description: str = ""
    ) -> dict[str, str]:
        """Copy ``deck`` into a new private deck in ``folder_id``; returns ``{id, url, name}``.

        POST /decks/copy/ (the site's own copy route; ``parent_folder`` is snake_case there)
        copies every card with quantity, categories and modifier, and the deck's categories,
        commander included (verified live 2026-10-05). The copy route ignores ``description``,
        so it is set afterwards with PATCH /decks/{id}/update/; a failure there leaves a valid,
        named backup and is not raised."""
        if not DECK_ID_RE.fullmatch(str(folder_id)):
            raise ArchidektError("contract", "backup folder id is not a number")
        body = await self._request(
            "POST",
            "/decks/copy/",
            token=token,
            json_body={
                "name": name,
                "deckFormat": deck.raw.get("deckFormat") or 3,
                "edhBracket": None,
                "description": description,
                "featured": "",
                "playmat": "",
                "copyId": int(deck.id),
                "private": True,
                "unlisted": False,
                "theorycrafted": False,
                "game": None,
                "parent_folder": int(folder_id),
                "cardPackage": None,
                "extras": {},
            },
        )
        new_id = body.get("id") if isinstance(body, dict) else None
        if isinstance(new_id, bool) or not isinstance(new_id, int):
            raise ArchidektError("contract", "deck copy response has no deck id")
        if description:
            try:
                await self._request(
                    "PATCH", f"/decks/{new_id}/update/", token=token, json_body={"description": description}
                )
            except ArchidektError as exc:
                logger.info("backup %s: description not set (%s)", new_id, exc.kind)
        return {"id": str(new_id), "url": f"https://archidekt.com/decks/{new_id}", "name": name}

    async def copy_deck(
        self, token: str, deck: Deck, *, name: str, private: bool = True, folder_id: str | None = None
    ) -> dict[str, str]:
        """Clone ``deck`` into a new deck named ``name`` (private by default) in ``folder_id``, or
        the account's root folder when None: the same POST /decks/copy/ the backups use (verified
        live 2026-10-05), so the copy keeps cards, quantities, categories and finishes."""
        if folder_id is None:
            tree = await self._request("GET", "/decks/folderTree/", token=token)
            if not isinstance(tree, dict) or not isinstance(tree.get("id"), int):
                raise ArchidektError("contract", "unexpected folder tree shape")
            folder_id = str(tree["id"])
        if not DECK_ID_RE.fullmatch(str(folder_id)):
            raise ArchidektError("contract", "folder id is not a number")
        body = await self._request(
            "POST",
            "/decks/copy/",
            token=token,
            json_body={
                "name": name,
                "deckFormat": deck.raw.get("deckFormat") or 3,
                "edhBracket": deck.raw.get("edhBracket"),
                "description": deck.description or "",
                "featured": "",
                "playmat": "",
                "copyId": int(deck.id),
                "private": private,
                "unlisted": False,
                "theorycrafted": False,
                "game": None,
                "parent_folder": int(folder_id),
                "cardPackage": None,
                "extras": {},
            },
        )
        new_id = body.get("id") if isinstance(body, dict) else None
        if isinstance(new_id, bool) or not isinstance(new_id, int):
            raise ArchidektError("contract", "deck copy response has no deck id")
        return {"id": str(new_id), "url": f"https://archidekt.com/decks/{new_id}", "name": name}

    # -- writes -------------------------------------------------------------
    async def resolve_card_id(
        self,
        token: str,
        name: str,
        *,
        set_code: str | None = None,
        collector_number: str | None = None,
        scryfall_id: str | None = None,
    ) -> int:
        """Archidekt printing id for a card; see resolve_card."""
        card = await self.resolve_card(
            token, name, set_code=set_code, collector_number=collector_number, scryfall_id=scryfall_id
        )
        return card["id"]

    async def resolve_card(
        self,
        token: str,
        name: str,
        *,
        set_code: str | None = None,
        collector_number: str | None = None,
        scryfall_id: str | None = None,
        require_printing: bool = False,
    ) -> dict[str, Any]:
        """Find an Archidekt printing for a card: ``{id, name, set_code, collector_number,
        options, exact_printing}``. ``options`` lists the finishes Archidekt offers for that
        printing ("Normal", "Foil", "Etched"); pick the deck modifier with finish_modifier.

        A printing is pinned server-side when the caller knows it (verified live 2026-10-05):
        ``uids=<Scryfall id>`` (Archidekt's ``uid`` is the Scryfall id), else ``edition=<set code,
        lower case>`` with ``collectorNumber=``. When that finds nothing for this name the
        lookup falls back to the name alone, and ``exact_printing`` is false.

        Name-only: ``/cards/v2/?name=`` is a substring search, so a plain page of 25 misses "Opt"
        or "Swamp" (verified live 2026-10-04: 177 and 878 matches). ``exact=true`` returns only
        exact oracle names, but nothing for a double-faced card's front face ("Delver of Secrets"
        is stored as "Delver of Secrets // Insectile Aberration"), so that falls back to the
        substring search matched on the front face.

        With ``require_printing`` (a printing the user picked by hand) there is no fallback: the
        set and collector number must name a printing of this very card, else ``not_found`` with
        a message saying what that printing is, if anything."""
        if require_printing:
            return await self._require_printing(token, name, set_code, collector_number)
        if scryfall_id and _SCRYFALL_ID_RE.fullmatch(scryfall_id.strip().lower()):
            hits = [
                c
                for c in await self._cards(token, {"uids": scryfall_id.strip().lower()})
                if _card_matches(c, name)
            ]
            if hits:
                return _printing(hits[0], exact=True)
        if set_code and _SET_CODE_RE.fullmatch(set_code.strip()):
            params: dict[str, Any] = {"name": name, "edition": set_code.strip().lower(), "pageSize": 50}
            number = (collector_number or "").strip()
            if number:
                hits = [
                    c
                    for c in await self._cards(token, {**params, "collectorNumber": number})
                    if _card_matches(c, name)
                ]
                if hits:
                    return _printing(hits[0], exact=True)
            hits = [c for c in await self._cards(token, params) if _card_matches(c, name)]
            if hits:
                return _printing(_prefer_printing(hits, set_code, collector_number), exact=not number)
        exact = await self._card_search(token, name, exact=True, page_size=25)
        hits = [c for c in exact if _card_matches(c, name)]
        if not hits:
            loose = await self._card_search(token, name, exact=False, page_size=50)
            hits = [c for c in loose if _card_matches(c, name)]
        if not hits:
            raise ArchidektError("not_found", f"no Archidekt printing found for '{name}'")
        return _printing(_prefer_printing(hits, set_code, collector_number), exact=False)

    async def _require_printing(
        self, token: str, name: str, set_code: str | None, collector_number: str | None
    ) -> dict[str, Any]:
        code, number = (set_code or "").strip(), (collector_number or "").strip()
        label = f"{code.upper()} #{number}"
        if not _SET_CODE_RE.fullmatch(code) or not number or len(number) > 10:
            raise ArchidektError(
                "not_found", f"'{name}': a set code and collector number are needed, got {label!r}"
            )
        params = {"edition": code.lower(), "collectorNumber": number, "pageSize": 10}
        found = await self._cards(token, params)
        hits = [c for c in found if _card_matches(c, name)]
        if hits:
            return _printing(hits[0], exact=True)
        others = sorted({_printing(c, exact=True)["name"] for c in found if isinstance(c.get("id"), int)})
        if others:
            raise ArchidektError(
                "not_found", f"{label} is {', '.join(others)}, not '{name}'; nothing was added"
            )
        raise ArchidektError("not_found", f"Archidekt has no printing {label} of '{name}'; nothing was added")

    async def _cards(self, token: str, params: dict[str, Any]) -> list[dict]:
        body = await self._request("GET", "/cards/v2/", token=token, params=params)
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise ArchidektError("contract", "unexpected card search shape")
        return [c for c in results if isinstance(c, dict)]

    async def _card_search(self, token: str, name: str, *, exact: bool, page_size: int) -> list[dict]:
        params: dict[str, Any] = {"name": name, "pageSize": page_size}
        if exact:
            params["exact"] = "true"
        body = await self._request("GET", "/cards/v2/", token=token, params=params)
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise ArchidektError("contract", "unexpected card search shape")
        return [c for c in results if isinstance(c, dict)]

    async def modify_card(self, token: str, deck_id: str, entry: dict[str, Any]) -> Any:
        """PATCH /decks/{id}/modifyCards/v2/ with {"cards": [entry]}, one card per call, as the
        nccurry reference does (reported shape, unverified against Archidekt)."""
        return await self._request(
            "PATCH", f"/decks/{deck_id}/modifyCards/v2/", token=token, json_body={"cards": [entry]}
        )

    async def update_deck(self, token: str, deck_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """PATCH /decks/{id}/update/ with the deck's own details: any of ``name``, ``description``,
        ``deckFormat`` (int, see FORMAT_IDS), ``edhBracket`` (int or None), ``private`` and
        ``unlisted`` (bool). Only the keys given are sent. The route with ``description`` is
        verified live (backup_deck uses it); the other keys are what Archidekt's own site bundle
        sends on the same route (reported, not verified). Callers re-read the deck afterwards and
        compare every field rather than trusting the response, which only has to be a JSON object."""
        if not DECK_ID_RE.fullmatch(str(deck_id)):
            raise ArchidektError("contract", "deck id is not a number")
        allowed = ("name", "description", "deckFormat", "edhBracket", "private", "unlisted")
        body = {k: fields[k] for k in allowed if k in fields}
        if not body:
            raise ArchidektError("contract", "no deck details to update")
        resp = await self._request("PATCH", f"/decks/{deck_id}/update/", token=token, json_body=body)
        if not isinstance(resp, dict):
            raise ArchidektError("contract", "unexpected deck update response shape")
        return resp


def _str_list(value: Any) -> list[str]:
    return [str(v) for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _card_price(prices: Any) -> float | None:
    """Card Kingdom's normal-finish price (``prices.ck``) when present, else TCGplayer's
    (``prices.tcg``); both are Archidekt's reported figures, not verified by us. The finish
    the deck row uses is ignored here (foil prices live under ``ckfoil``/``tcgfoil``)."""
    if not isinstance(prices, dict):
        return None
    for key in ("ck", "tcg"):
        p = _number(prices.get(key))
        if p is not None and p > 0:
            return p
    return None


def _mana_production(value: Any) -> dict[str, int] | None:
    """``{"W": 1, "U": null, ...}`` -> ``{"W": 1}``; None when nothing is produced."""
    if not isinstance(value, dict):
        return None
    out = {str(k): int(v) for k, v in value.items() if isinstance(v, int) and not isinstance(v, bool) and v}
    return out or None


def parse_deck(body: Any) -> Deck:
    if not isinstance(body, dict) or "id" not in body or not isinstance(body.get("cards"), list):
        raise ArchidektError("contract", "unexpected deck shape")
    cards: list[DeckCard] = []
    for entry in body["cards"]:
        if not isinstance(entry, dict):
            raise ArchidektError("contract", "unexpected card entry shape")
        if entry.get("deletedAt"):
            continue  # soft-deleted relation still present in the payload
        card = entry.get("card") if isinstance(entry.get("card"), dict) else {}
        oracle = card.get("oracleCard") if isinstance(card.get("oracleCard"), dict) else {}
        edition = card.get("edition") if isinstance(card.get("edition"), dict) else {}
        qty = entry.get("quantity", 1)
        if not isinstance(qty, int):
            raise ArchidektError("contract", "card quantity is not an integer")
        legalities = oracle.get("legalities") if isinstance(oracle.get("legalities"), dict) else {}
        rank = oracle.get("edhrecRank")
        combos = oracle.get("twoCardComboIds") if isinstance(oracle.get("twoCardComboIds"), list) else []
        cards.append(
            DeckCard(
                relation_id=entry.get("id") if isinstance(entry.get("id"), int) else None,
                card_id=card.get("id") if isinstance(card.get("id"), int) else None,
                name=str(oracle.get("name", "?")),
                quantity=qty,
                # Live decks send "categories": null for cards with no category set.
                categories=[str(c) for c in (entry.get("categories") or []) if isinstance(c, str)],
                modifier=str(entry.get("modifier", "") or ""),
                set_code=str(edition.get("editioncode", "") or ""),
                collector_number=str(card.get("collectorNumber", "") or ""),
                oracle_name=str(oracle.get("name", "") or ""),
                cmc=_number(oracle.get("cmc")),
                mana_cost=str(oracle.get("manaCost", "") or ""),
                colors=_str_list(oracle.get("colors")),
                color_identity=_str_list(oracle.get("colorIdentity")),
                types=_str_list(oracle.get("types")),
                subtypes=_str_list(oracle.get("subTypes")),
                supertypes=_str_list(oracle.get("superTypes")),
                rarity=str(card.get("rarity", "") or ""),
                price=_card_price(card.get("prices")),
                legalities={str(k): str(v) for k, v in legalities.items() if isinstance(v, str)},
                edhrec_rank=rank if isinstance(rank, int) and not isinstance(rank, bool) else None,
                salt=_number(oracle.get("salt")),
                game_changer=oracle.get("gameChanger") is True,
                tutor=oracle.get("tutor") is True,
                extra_turns=oracle.get("extraTurns") is True,
                mass_land_denial=oracle.get("massLandDenial") is True,
                mana_production=_mana_production(oracle.get("manaProduction")),
                two_card_combo_ids=[str(i) for i in combos if i is not None],
                notes=str(entry.get("notes") or ""),
                label=str(entry.get("label") or ""),
                companion=entry.get("companion") is True,
                image_hash=str(card.get("scryfallImageHash") or ""),
                scryfall_uid=str(card.get("uid") or ""),
                default_category=str(oracle.get("defaultCategory") or ""),
            )
        )
    owner = body.get("owner") if isinstance(body.get("owner"), dict) else {}
    fmt = body.get("deckFormat")
    format_id = fmt if isinstance(fmt, int) and not isinstance(fmt, bool) else None
    bracket = body.get("edhBracket")
    tags: list[str] = []
    raw_tags = body.get("deckTags")
    for t in raw_tags if isinstance(raw_tags, list) else []:
        if isinstance(t, str):
            tags.append(t)
        elif isinstance(t, dict) and isinstance(t.get("name"), str):
            tags.append(t["name"])
    return Deck(
        id=str(body["id"]),
        name=str(body.get("name", "")),
        owner=str(owner.get("username", "")),
        updated_at=str(body.get("updatedAt", "")),
        cards=cards,
        categories=[c for c in (body.get("categories") or []) if isinstance(c, dict)],
        raw=body,
        description=body.get("description") if isinstance(body.get("description"), str) else "",
        format_id=format_id,
        format=FORMAT_NAMES.get(format_id) if format_id is not None else None,
        edh_bracket=bracket if isinstance(bracket, int) and not isinstance(bracket, bool) else None,
        private=body.get("private") is True,
        unlisted=body.get("unlisted") is True,
        tags=tags,
        created_at=str(body.get("createdAt") or ""),
    )
