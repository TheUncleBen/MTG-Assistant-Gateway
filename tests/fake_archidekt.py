"""In-memory stand-in for the parts of Archidekt's API the gateway uses.

Shapes follow the handoff's endpoint map and the project's real CSV export
(turned into API-shaped JSON). Nothing here was recorded from archidekt.com.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import httpx

from mtg_gateway.archidekt_csv import parse_export, to_deck_json

FIXTURE = Path(__file__).parent / "fixtures" / "sample_deck.csv"

CARD_DB = {
    "arcane signet": {"id": 9001, "oracleCard": {"name": "Arcane Signet"}},
    "rampant growth": {"id": 9002, "oracleCard": {"name": "Rampant Growth"}},
    "sol ring": {"id": 9003, "oracleCard": {"name": "Sol Ring"}},
    "forest": {"id": 9004, "oracleCard": {"name": "Forest"}},
    "island": {"id": 9005, "oracleCard": {"name": "Island"}},
    "aesi, tyrant of gyre strait": {"id": 9006, "oracleCard": {"name": "Aesi, Tyrant of Gyre Strait"}},
    # Names that break a first-page substring search on the real site (verified 2026-10-04):
    "opt": {"id": 9007, "oracleCard": {"name": "Opt"}},
    "swamp": {"id": 9008, "oracleCard": {"name": "Swamp"}},
    "delver of secrets // insectile aberration": {
        "id": 9009,
        "oracleCard": {"name": "Delver of Secrets // Insectile Aberration"},
    },
    "acidic slime": {"id": 9010, "oracleCard": {"name": "Acidic Slime"}},
}


# Specific printings, in the live search-result shape (ids, set codes, numbers, options and
# Scryfall uids as Archidekt returned them on 2026-10-05).
PRINTINGS = [
    {
        "id": 87176,
        "uid": "be68e315-ffef-40a8-8a46-1c042b148c03",
        "oracleCard": {"name": "Swamp"},
        "edition": {"editioncode": "m21"},
        "collectorNumber": "267",
        "options": ["Normal", "Foil"],
    },
    {
        "id": 91043,
        "uid": "58b26011-e103-45c4-a253-900f4e6b2eeb",
        "oracleCard": {"name": "Sol Ring"},
        "edition": {"editioncode": "cmr"},
        "collectorNumber": "472",
        "options": ["Normal"],
    },
    {
        "id": 124026,
        "uid": "00000000-0000-4000-8000-000000124026",  # placeholder, not the real Scryfall id
        "oracleCard": {"name": "Sol Ring"},
        "edition": {"editioncode": "sld"},
        "collectorNumber": "1074",
        "options": ["Etched"],  # an etched-only printing: a Foil row cannot move onto it
    },
    {
        "id": 124027,
        "uid": "00000000-0000-4000-8000-000000124027",  # placeholder, not the real Scryfall id
        "oracleCard": {"name": "Sol Ring"},
        "edition": {"editioncode": "sld"},
        "collectorNumber": "1075",
        # a printing in every finish, so a wrong finish choice shows up (an etched-only printing
        # hid an etched-read-as-foil bug on the import path)
        "options": ["Normal", "Foil", "Etched"],
    },
]


class FakeArchidekt:
    def __init__(self) -> None:
        cards = parse_export(FIXTURE.read_text(encoding="utf-8"))
        self.users = {
            "alice": {"password": "pw-alice", "id": 77, "decks": [42]},
            "amy": {"password": "pw-amy", "id": 78, "decks": [43]},
        }
        # token -> username, for access tokens (JWT-shaped, see issue_access) and refresh tokens
        # ("ref-<name>") alike, so clearing it forgets the whole session as the real site would.
        self.tokens: dict[str, str] = {}
        self.access_ttl = 3600  # live access JWTs expire 3600 s after issue (verified 2026-10-05)
        self.issue_refresh = True  # login answers with a refresh token
        self.refresh_calls = 0
        self.decks: dict[int, dict[str, Any]] = {
            42: to_deck_json(
                cards,
                deck_id=42,
                name="Sample Commander Deck",
                owner="alice",
                updated_at="2026-10-01T10:00:00Z",
            ),
            43: to_deck_json(
                cards[:5], deck_id=43, name="Amy's deck", owner="amy", updated_at="2026-10-01T10:00:00Z"
            ),
        }
        self.calls: list[tuple[str, str]] = []
        # Answers given before the normal handling, one per request, in order: an int status (with
        # an optional Retry-After) or an exception to raise, e.g. httpx.ReadTimeout.
        self.inject: list[tuple[int | Exception, str | None]] = []
        self.patches: list[dict[str, Any]] = []
        self.fail_patch_silently = False
        # Faults a verify must catch: added rows stored without their finish, or with another
        # printing of the same card (card id -> card id to store instead).
        self.add_rows_lose_finish = False
        self.empty_label_ignored = False
        self.add_rows_swap_printing: dict[int, int] = {}
        self.create_returns_full_deck = True  # live Archidekt does not (verified 2026-10-05)
        self.fail_deck_reads = False  # signed-in deck reads answer 503
        self.next_rel_id = 2000
        self.next_deck_id = 100
        self.private = {43}  # deck ids that need the owner's session
        self.next_folder_id = 500
        self.folders: dict[str, list[dict[str, Any]]] = {}  # username -> folders under their root
        self.deck_folder: dict[int, int] = {}  # deck id -> folder id (decks not listed sit in root)
        self.fail_deck_update = False
        self.updates: list[dict[str, Any]] = []  # bodies of PATCH /decks/{id}/update/, with deck_id
        self.fail_backup = False  # POST /decks/copy/ answers 503
        # Like the real site: the old owner= filter is ignored and every deck comes back.
        self.ignore_owner_username = False
        # Suspected live behaviour (unverified): the ownerUsername listing leaves the owner's
        # private decks out, while an authenticated ownerId listing includes them.
        self.username_listing_hides_private = False
        self.list_auth_schemes: list[str] = []  # Authorization scheme of each /decks/v3/ call
        self.search_params: list[dict[str, str]] = []  # query of each /decks/v3/ call
        # The member's Collection (username -> record id -> record in the v2 listing shape), and the
        # social state: follows (username -> user ids), bookmarks (username -> deck ids), votes
        # ((username, comment root) -> 1 up / 2 down) and comment threads (root id -> comments).
        self.collections: dict[str, dict[int, dict[str, Any]]] = {}
        self.next_coll_id = 5000
        self.follows: dict[str, set[int]] = {}
        self.bookmarks: dict[str, set[int]] = {}
        self.editors: dict[int, list[dict[str, Any]]] = {}  # deck id -> collaborator rows
        self.next_editor_id = 900
        self.votes: dict[tuple[str, int], int] = {}
        self.comments: dict[int, list[dict[str, Any]]] = {}  # thread root -> flat list of comments
        self.next_comment_id = 800000
        # Hand actions added 2026-10-07: global deck tags (id -> name), each deck's tag relations
        # (deck id -> [{id, tag, name, position}]), deleted deck ids and massUpdate bodies.
        self.tags: dict[int, str] = {1: "budget", 2: "tribal"}
        self.deck_tags: dict[int, list[dict[str, Any]]] = {}
        self.next_tag_id = 10
        self.next_tag_rel_id = 90000
        self.deleted: list[int] = []
        self.mass_updates: list[dict[str, Any]] = []
        self.transport = httpx.MockTransport(self.handle)

    def add_side_row(self, deck_id: int, name: str, quantity: int = 1, category: str = "Maybeboard") -> None:
        """Give a deck a maybeboard / sideboard row (a category not counted in the deck) holding
        ``name``, a printing already known to the fake."""
        deck = self.decks[deck_id]
        if not any(c["name"] == category for c in deck["categories"]):
            deck["categories"].append({"name": category, "isPremier": False, "includedInDeck": False})
        card = self.printing(CARD_DB[name.lower()]["id"]) if name.lower() in CARD_DB else None
        if card is None:
            for d in self.decks.values():
                for c in d["cards"]:
                    if c["card"]["oracleCard"]["name"] == name:
                        card = json.loads(json.dumps(c["card"]))
                        break
                if card:
                    break
        assert card is not None, name
        self.next_rel_id += 1
        deck["cards"].append(
            {
                "id": self.next_rel_id,
                "quantity": quantity,
                "modifier": "Normal",
                "categories": [category],
                "card": card,
            }
        )

    # -- collection and social helpers -------------------------------------------------------------
    def _user_id(self, who: str) -> int:
        return int(self.users[who]["id"])

    def _username(self, user_id: int) -> str | None:
        return next((n for n, u in self.users.items() if u["id"] == user_id), None)

    def owned_count(self, who: str, oracle_name: str) -> int:
        """Copies of a card (any printing) in a member's collection: what the deck JSON's per-card
        ``owned`` reports for the session that reads the deck (reported, not verified live)."""
        return sum(
            r["quantity"]
            for r in self.collections.get(who, {}).values()
            if r["card"]["oracleCard"]["name"].lower() == oracle_name.lower()
        )

    def _deck_for(self, deck: dict[str, Any], who: str | None) -> dict[str, Any]:
        """A deck as the live JSON carries it for one session: owner id, social fields and the
        per-card ``owned`` count."""
        out = json.loads(json.dumps(deck))
        owner = out["owner"]["username"]
        out["owner"] = {"id": self._user_id(owner), **out["owner"]}
        root = 300000 + out["id"]
        votes = [v for (u, r), v in self.votes.items() if r == root]
        out.setdefault("commentRoot", root)
        out["points"] = sum(1 if v == 1 else -1 for v in votes)
        out["userInput"] = self.votes.get((who or "", root), 0)
        out["bookmarked"] = who is not None and out["id"] in self.bookmarks.get(who, set())
        out["viewCount"] = out.get("viewCount", 0)
        out["deckTags"] = [dict(r) for r in self.deck_tags.get(out["id"], [])]
        out["parentFolder"] = self.deck_folder.get(out["id"], 1000 + self._user_id(owner))
        out.setdefault("featured", "")
        out.setdefault("customFeatured", "")
        for c in out["cards"]:
            c["card"]["owned"] = self.owned_count(who, c["card"]["oracleCard"]["name"]) if who else 0
        return out

    def _collection_row(self, rid: int, card: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return {
            "id": rid,
            "game": body.get("game", 1),
            "quantity": int(body["quantity"]),
            "card": card,
            "modifier": body.get("modifier") or "Normal",
            "language": body.get("language") or "EN",
            "condition": body.get("condition") or "NM",
            "tags": body.get("tags") or [],
            "purchasePrice": body.get("purchasePrice"),
            "createdAt": now,
            "modifiedAt": now,
        }

    def _comment(self, cid: int, who: str) -> dict[str, Any]:
        for comments in self.comments.values():
            for c in comments:
                if c["id"] == cid:
                    return c
        raise KeyError(cid)

    def _folder_name(self, username: str, folder_id: int | None) -> str | None:
        return next((f["name"] for f in self.folders.get(username, []) if f["id"] == folder_id), None)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport)

    def issue_access(self, name: str) -> str:
        """An unsigned JWT-shaped access token carrying ``exp`` (the gateway reads it to refresh
        ahead of expiry) and a counter so each one is distinct."""
        now = int(time.time())
        header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
        claims = {"token_type": "access", "exp": now + self.access_ttl, "iat": now, "n": len(self.tokens)}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        token = f"{header}.{payload}.sig"
        self.tokens[token] = name
        return token

    def expire_access_tokens(self) -> None:
        """Forget every access token but keep the refresh tokens (an hour has passed)."""
        for token in [t for t in self.tokens if "." in t]:
            del self.tokens[token]

    def _who(self, token: str) -> str | None:
        name = self.tokens.get(token)
        if name is None or "." not in token:
            return None  # refresh tokens never sign a request in
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return name if claims["exp"] > time.time() else None

    def bump(self, deck_id: int) -> None:
        """Simulate an edit made elsewhere (changes the fingerprint)."""
        self.decks[deck_id]["cards"][0]["quantity"] += 1
        self.decks[deck_id]["updatedAt"] = "2026-10-02T00:00:00Z"

    def _deck_printings(self) -> list[dict[str, Any]]:
        """The printings the decks here hold or have held, in the card-search result shape
        (``options`` as the row's finish plus Normal), deduplicated by card id. A card taken out of
        every deck stays known: the live site knows every printed card, so a removal can be undone
        with an ``add`` without a backup copy keeping the printing around."""
        seen: dict[int, dict[str, Any]] = getattr(self, "_known_printings", {})
        for d in self.decks.values():
            for c in d["cards"]:
                card = c["card"]
                if card["id"] in seen or "edition" not in card:
                    continue
                entry = json.loads(json.dumps(card))
                entry.setdefault("options", sorted({"Normal", c.get("modifier") or "Normal"}))
                seen[card["id"]] = entry
        self._known_printings = seen
        return list(seen.values())

    def _listing_row(self, d: dict[str, Any]) -> dict[str, Any]:
        """One row of the ``/decks/v3/`` listing (and of the precon listing) for a stored deck."""
        return {
            "id": d["id"],
            "name": d["name"],
            "owner": {"id": self.users[d["owner"]["username"]]["id"], **d["owner"]},
            "updatedAt": d["updatedAt"],
            "deckFormat": 3,
            "private": d["id"] in self.private,
            # Live listings carry the deck's folder (verified 2026-10-05); decks in the
            # root folder are reported here as null.
            "parentFolderId": self.deck_folder.get(d["id"]),
            "parentFolderName": self._folder_name(d["owner"]["username"], self.deck_folder.get(d["id"])),
            # Listing extras as the live v3 rows carry them (seen 2026-10-05).
            "size": sum(c["quantity"] for c in d["cards"]),
            "edhBracket": d.get("edhBracket"),
            "colors": {"W": 0, "U": 12, "B": 0, "R": 0, "G": 14},
            "featured": d.get("featured") or "",
            "tags": ([{"id": 1, "name": "ramp"}, {"id": 2, "name": "sea monsters"}] if d["id"] == 42 else []),
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if request.method != "GET":
            self._deck_printings()  # remember every printing before a write may take it out
        if self.inject:
            answer, retry_after = self.inject.pop(0)
            if isinstance(answer, Exception):
                raise answer
            headers = {"Retry-After": retry_after} if retry_after else {}
            return httpx.Response(answer, json={"detail": "injected"}, headers=headers)
        if path == "/api/rest-auth/login/":
            body = json.loads(request.content)
            name = body.get("username") or body.get("email", "").split("@")[0]
            user = self.users.get(name)
            if not user or body.get("password") != user["password"]:
                return httpx.Response(
                    400, json={"non_field_errors": ["Unable to log in with provided credentials."]}
                )
            token = self.issue_access(name)
            body = {"access_token": token, "user": {"id": user["id"], "username": name}}
            if self.issue_refresh:
                self.tokens[f"ref-{name}"] = name
                body["refresh_token"] = f"ref-{name}"
            return httpx.Response(200, json=body)
        if path == "/api/rest-auth/token/refresh/":
            # Live shape (verified 2026-10-05): {"refresh"} -> 200 {"access", "access_token_expiration"};
            # the refresh token is not rotated. A refresh token the site does not know gets 401.
            self.refresh_calls += 1
            body = json.loads(request.content)
            refresh = body.get("refresh")
            name = self.tokens.get(refresh) if isinstance(refresh, str) and "." not in refresh else None
            if name is None:
                return httpx.Response(
                    401, json={"detail": "Token is invalid or expired", "code": "token_not_valid"}
                )
            token = self.issue_access(name)
            expiry = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + self.access_ttl))
            return httpx.Response(200, json={"access": token, "access_token_expiration": expiry})
        auth = request.headers.get("Authorization", "")
        # Like the real site: only the JWT scheme signs a request in; "Bearer <token>" is treated
        # as anonymous (verified live 2026-10-05), which hides the owner's private decks.
        scheme, _, token = auth.partition(" ")
        who = self._who(token) if scheme == "JWT" else None
        parts = path.strip("/").split("/")
        # anonymous read of a public deck
        if (
            who is None
            and request.method == "GET"
            and len(parts) == 3
            and parts[1] == "decks"
            and parts[2].isdigit()
        ):
            deck = self.decks.get(int(parts[2]))
            if deck is None or int(parts[2]) in self.private:
                return httpx.Response(404, json={"detail": "Not found."})
            return httpx.Response(200, json=self._deck_for(deck, None))
        # The deck listing answers anonymous callers too (the public deck search; verified live
        # 2026-10-07); private decks are dropped from it further down.
        if path == "/api/decks/precons/" and request.method == "GET":
            rows = [self._listing_row(d) for d in self.decks.values() if d["id"] not in self.private]
            return httpx.Response(200, json={"Sample Set (SMP)": rows[:1], "Older Set (OLD)": rows[1:2]})
        if who is None and not (path == "/api/decks/v3/" and request.method == "GET" and not auth):
            if auth:
                return httpx.Response(401, json={"detail": "Given token not valid for any token type"})
            return httpx.Response(401, json={"detail": "Authentication credentials were not provided."})
        if path == "/api/decks/folderTree/" and request.method == "GET":
            root = 1000 + self.users[who]["id"]
            kids = [{**f, "children": None} for f in self.folders.get(who, [])]
            return httpx.Response(
                200, json={"id": root, "name": "root", "children": kids or None, "private": False}
            )
        if path == "/api/decks/folders/" and request.method == "POST":
            body = json.loads(request.content)
            assert body["parentFolder"] == 1000 + self.users[who]["id"], body
            self.next_folder_id += 1
            folder = {"id": self.next_folder_id, "name": body["name"], "private": body["private"]}
            self.folders.setdefault(who, []).append(folder)
            return httpx.Response(201, json={**folder, "parentFolder": body["parentFolder"]})
        if path == "/api/massUpdate/" and request.method == "PATCH":
            body = json.loads(request.content)
            self.mass_updates.append(body)
            own = {f["id"] for f in self.folders.get(who, [])} | {1000 + self.users[who]["id"]}
            for item in body.get("items", []):
                patch = item.get("patch") or {}
                if item.get("type") == "deck":
                    deck = self.decks.get(item.get("id"))
                    if deck is None or deck["owner"]["username"] != who:
                        return httpx.Response(404, json={"detail": "Not found."})
                    if "parentFolder" in patch:
                        if patch["parentFolder"] not in own:
                            return httpx.Response(400, json={"parentFolder": ["Invalid folder."]})
                        self.deck_folder[deck["id"]] = patch["parentFolder"]
                elif item.get("type") == "folder":
                    folder = next((f for f in self.folders.get(who, []) if f["id"] == item.get("id")), None)
                    if folder is None:
                        return httpx.Response(404, json={"detail": "Not found."})
                    if "name" in patch:
                        folder["name"] = patch["name"]
                else:
                    return httpx.Response(400, json={"items": ["Unknown type."]})
            return httpx.Response(200, json={"ok": True})
        if path == "/api/decks/tags/v2/" and request.method == "GET":
            q = (request.url.params.get("q") or "").lower()
            hits = [{"id": i, "name": n} for i, n in self.tags.items() if q in n.lower()]
            return httpx.Response(200, json={"count": len(hits), "results": hits})
        if path == "/api/decks/tags/" and request.method == "POST":
            body = json.loads(request.content)
            name = str(body.get("name") or "").strip()
            if not name:
                return httpx.Response(400, json={"name": ["This field may not be blank."]})
            self.next_tag_id += 1
            self.tags[self.next_tag_id] = name
            return httpx.Response(201, json={"id": self.next_tag_id, "name": name})
        if path == "/api/decks/tagRelations/" and request.method == "POST":
            body = json.loads(request.content)
            deck = self.decks.get(body.get("deck"))
            if deck is None or deck["owner"]["username"] != who or body.get("tag") not in self.tags:
                return httpx.Response(400, json={"deck": ["Invalid."]})
            self.next_tag_rel_id += 1
            rel = {
                "id": self.next_tag_rel_id,
                "tag": body["tag"],
                "deck": deck["id"],
                "name": self.tags[body["tag"]],
                "position": body.get("position"),
            }
            self.deck_tags.setdefault(deck["id"], []).append(rel)
            return httpx.Response(201, json=rel)
        if (
            len(parts) == 4
            and parts[1:3] == ["decks", "tagRelations"]
            and parts[3].isdigit()
            and request.method == "DELETE"
        ):
            rid = int(parts[3])
            for did, rels in self.deck_tags.items():
                hit = next((r for r in rels if r["id"] == rid), None)
                if hit is not None:
                    if self.decks[did]["owner"]["username"] != who:
                        return httpx.Response(403, json={"detail": "Not yours."})
                    rels.remove(hit)
                    return httpx.Response(204)
            return httpx.Response(404, json={"detail": "Not found."})
        if (
            len(parts) == 3
            and parts[1] == "comments"
            and parts[2].isdigit()
            and request.method in ("PATCH", "DELETE")
        ):
            cid = int(parts[2])
            for flat in self.comments.values():
                hit = next((c for c in flat if c["id"] == cid), None)
                if hit is None:
                    continue
                if hit["owner"]["username"] != who:
                    return httpx.Response(403, json={"detail": "You do not have permission."})
                if request.method == "DELETE":
                    flat[:] = [c for c in flat if c["id"] != cid and c["parent"] != cid]
                    return httpx.Response(204)
                body = json.loads(request.content)
                if isinstance(body.get("text"), str) and body["text"].strip():
                    hit["text"] = body["text"]
                    hit["editedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                return httpx.Response(200, json=hit)
            return httpx.Response(404, json={"detail": "Not found."})
        if path == "/api/decks/copy/" and request.method == "POST":
            if self.fail_backup:
                return httpx.Response(503, json={"detail": "Service unavailable."})
            body = json.loads(request.content)
            src = self.decks.get(body["copyId"])
            if src is None or (body["copyId"] in self.private and src["owner"]["username"] != who):
                return httpx.Response(404, json={"detail": "Not found."})
            own = {f["id"] for f in self.folders.get(who, [])} | {1000 + self.users[who]["id"]}
            if body["parent_folder"] not in own:
                return httpx.Response(400, json={"parent_folder": ["Invalid folder."]})
            self.next_deck_id += 1
            copy = json.loads(json.dumps(src))
            copy.update(
                id=self.next_deck_id,
                name=body["name"],
                owner={"username": who},
                description="",
                private=body["private"],
            )
            self.decks[self.next_deck_id] = copy
            self.private.add(self.next_deck_id)
            self.deck_folder[self.next_deck_id] = body["parent_folder"]
            return httpx.Response(
                201, json={"name": copy["name"], "id": copy["id"], "parent_folder": body["parent_folder"]}
            )
        if path == "/api/decks/v2/" and request.method == "POST":
            body = json.loads(request.content)
            self.next_deck_id += 1
            deck = {
                "id": self.next_deck_id,
                "name": body["name"],
                "owner": {"username": who},
                "updatedAt": "2026-10-03T00:00:00Z",
                "deckFormat": body.get("deckFormat"),
                "private": body.get("private", True),
                "categories": [],
                "cards": [],
            }
            self.decks[self.next_deck_id] = deck
            if deck["private"]:
                self.private.add(self.next_deck_id)
            if not self.create_returns_full_deck:
                return httpx.Response(201, json={"id": deck["id"], "name": deck["name"]})
            return httpx.Response(201, json=deck)
        if path == "/api/decks/v3/":
            self.list_auth_schemes.append(scheme)
            # Archidekt honours ownerUsername and ownerId and ignores owner/ownerexact (verified
            # live for ownerUsername). Private decks are only visible to their owner's session.
            wanted = request.url.params.get("ownerUsername")
            wanted_id = request.url.params.get("ownerId")
            if wanted is not None and not self.ignore_owner_username:
                chosen = [d for d in self.decks.values() if d["owner"]["username"] == wanted]
                if self.username_listing_hides_private:
                    chosen = [d for d in chosen if d["id"] not in self.private]
            elif wanted_id is not None:
                chosen = [
                    d
                    for d in self.decks.values()
                    if str(self.users[d["owner"]["username"]]["id"]) == wanted_id
                ]
            else:
                chosen = list(self.decks.values())
            chosen = [d for d in chosen if d["id"] not in self.private or d["owner"]["username"] == who]
            # The public deck search sends name= (a substring of the deck name; verified live
            # 2026-10-07) and deckFormat=; both narrow the listing.
            name = request.url.params.get("name")
            if name:
                chosen = [d for d in chosen if name.lower() in d["name"].lower()]
            fmt = request.url.params.get("deckFormat")
            if fmt is not None:
                chosen = [d for d in chosen if str(3) == fmt]
            # commanderName matches only a commander's full name: a part of it ("Krenko") finds
            # nothing, answered with count -1 (seen live 2026-10-08).
            commander = request.url.params.get("commanderName")
            if commander is not None:
                chosen = [
                    d
                    for d in chosen
                    if any(
                        "Commander" in (c.get("categories") or [])
                        and c["card"]["oracleCard"]["name"].lower() == commander.lower()
                        for c in d["cards"]
                    )
                ]
                if not chosen:
                    self.search_params.append(dict(request.url.params))
                    return httpx.Response(200, json={"count": -1, "results": []})
            self.search_params.append(dict(request.url.params))
            results = [self._listing_row(d) for d in chosen]
            return httpx.Response(200, json={"count": len(results), "results": results})
        if path == "/api/cards/v2/":
            # name= is a substring search over a huge card pool; exact=true returns exact oracle
            # names only. Without exact, thirty look-alike printings crowd out the real card the
            # way "Opt" and "Swamp" drown on the real site.
            name = request.url.params.get("name", "").lower()
            page = int(request.url.params.get("pageSize", "25"))
            uids = request.url.params.get("uids")
            edition = request.url.params.get("edition")
            if uids is not None:
                return httpx.Response(200, json={"results": [p for p in PRINTINGS if p["uid"] == uids]})
            if edition is not None:  # live: lower-case set codes only; name stays a substring match
                number = request.url.params.get("collectorNumber")
                hits = [
                    p
                    for p in PRINTINGS + self._deck_printings()
                    if p["edition"]["editioncode"] == edition
                    and name in p["oracleCard"]["name"].lower()
                    and (number is None or p["collectorNumber"] == number)
                ]
                return httpx.Response(200, json={"results": hits[:page]})
            if request.url.params.get("exact") == "true":
                # every card some deck here holds exists on Archidekt too, so a list exported from
                # a deck can be re-created (the live site knows every printed card)
                hit = CARD_DB.get(name) or next(
                    (p for p in self._deck_printings() if p["oracleCard"]["name"].lower() == name), None
                )
                return httpx.Response(200, json={"results": [hit] if hit else []})
            fillers = [
                {"id": 70000 + i, "oracleCard": {"name": f"{name.title()} Lookalike {i}"}} for i in range(30)
            ]
            real = [v for k, v in CARD_DB.items() if name in k]
            return httpx.Response(200, json={"results": (fillers + real)[:page]})
        # -- the member's Collection (routes as archidekt.com's collection page uses them) ------
        if len(parts) == 4 and parts[1] == "collection" and parts[2].isdigit() and parts[3] == "v2":
            if int(parts[2]) != self._user_id(who):
                return httpx.Response(400, json=["No public collection found."])
            rows = list(self.collections.get(who, {}).values())
            name = request.url.params.get("cardName", "").lower()
            if name:
                rows = [r for r in rows if name in r["card"]["oracleCard"]["name"].lower()]
            order = request.url.params.get("collectionOrderBy")
            if order == "editionDate":
                rows.sort(key=lambda r: r["card"]["edition"]["editioncode"])
            else:
                rows.sort(key=lambda r: -r["id"])
            size = int(request.url.params.get("pageSize", "50"))
            page = int(request.url.params.get("page", "1"))
            pages = max(1, -(-len(rows) // size))
            chunk = rows[(page - 1) * size : page * size]
            return httpx.Response(
                200,
                json={
                    "count": len(rows),
                    "results": chunk,
                    "next": f"https://ark.test/api/collection/{parts[2]}/v2/?page={page + 1}"
                    if page < pages
                    else None,
                    "previous": None,
                    "page": page,
                    "totalPages": pages,
                    "tags": [],
                    "isPublic": False,
                    "owner": {"id": self._user_id(who), "username": who, "avatar": None, "frame": None},
                },
            )
        if path == "/api/collection/v2/" and request.method == "POST":
            body = json.loads(request.content)
            if not isinstance(body.get("card"), int) or not isinstance(body.get("quantity"), int):
                return httpx.Response(400, json={"card": ["This field is required."]})
            try:
                card = self.printing(body["card"])
            except AssertionError:
                return httpx.Response(400, json={"card": ["Invalid pk - object does not exist."]})
            self.next_coll_id += 1
            row = self._collection_row(self.next_coll_id, card, body)
            self.collections.setdefault(who, {})[row["id"]] = row
            return httpx.Response(201, json=row)
        if len(parts) == 4 and parts[1:3] == ["collection", "v2"] and parts[3].isdigit():
            rid = int(parts[3])
            row = self.collections.get(who, {}).get(rid)
            if row is None or request.method not in ("PUT", "PATCH"):
                return httpx.Response(404, json={"detail": "Not found."})
            body = json.loads(request.content)
            for key in ("quantity", "modifier", "language", "condition", "tags", "purchasePrice"):
                if key in body:
                    row[key] = body[key]
            row["modifiedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return httpx.Response(200, json=row)
        if path == "/api/collection/bulk/" and request.method == "DELETE":
            body = json.loads(request.content)
            mine_rows = self.collections.get(who, {})
            for rid in body.get("ids", []):
                mine_rows.pop(int(rid), None)
            return httpx.Response(204)
        # -- social: follow, following list, votes, comments ---------------------------------------
        if path == "/api/users/follow/" and request.method == "POST":
            body = json.loads(request.content)
            target = body.get("followId")
            if self._username(target) is None:
                return httpx.Response(404, json={"detail": "Not found."})
            follows = self.follows.setdefault(who, set())
            (follows.discard if body.get("unfollow") else follows.add)(int(target))
            return httpx.Response(200, json={"following": not body.get("unfollow")})
        if len(parts) == 4 and parts[1] == "users" and parts[2].isdigit() and parts[3] == "following":
            name = self._username(int(parts[2]))
            ids = sorted(self.follows.get(name or "", set()))
            mine_ids = self.follows.get(who, set())
            results = [
                {"id": i, "username": self._username(i), "avatar": None, "following": i in mine_ids}
                for i in ids
            ]
            return httpx.Response(
                200, json={"count": len(results), "next": None, "previous": None, "results": results}
            )
        if (
            len(parts) == 4
            and parts[1:3] == ["comments", "vote"]
            and parts[3].isdigit()
            and request.method == "PUT"
        ):
            body = json.loads(request.content)
            root = int(parts[3])
            if body.get("remove"):
                self.votes.pop((who, root), None)
            else:
                self.votes[(who, root)] = 1 if body.get("up") else 2
            return httpx.Response(200, json={"ok": True})
        if path == "/api/comments/createComment/" and request.method == "POST":
            body = json.loads(request.content)
            parent = body.get("parent")
            text = body.get("text")
            if not isinstance(parent, int) or not isinstance(text, str) or not text.strip():
                return httpx.Response(400, json={"text": ["This field may not be blank."]})
            root = parent if parent in self.comments or parent >= 300000 and parent < 400000 else None
            if root is None:
                root = next(
                    (r for r, cs in self.comments.items() if any(c["id"] == parent for c in cs)), None
                )
            if root is None:
                return httpx.Response(404, json={"detail": "Not found."})
            self.next_comment_id += 1
            comment = {
                "id": self.next_comment_id,
                "text": text,
                "owner": {"id": self._user_id(who), "username": who, "avatar": None, "frame": None},
                "parent": parent,
                "originalPost": root,
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "editedAt": None,
                "points": 0,
                "userInput": 0,
                "childrenCount": 0,
                "children": {"count": 0, "results": []},
                "archived": False,
                "locked": False,
                "type": 4,
            }
            self.comments.setdefault(root, []).append(comment)
            return httpx.Response(201, json=comment)
        if len(parts) == 3 and parts[1] == "comments" and parts[2].isdigit() and request.method == "GET":
            root = int(parts[2])
            deck = self.decks.get(root - 300000)
            if deck is None:
                return httpx.Response(404, json={"detail": "Not found."})
            flat = self.comments.get(root, [])

            def tree(parent: int) -> list[dict[str, Any]]:
                kids = [c for c in flat if c["parent"] == parent]
                return [
                    {
                        **c,
                        # a comment's score and the reader's vote come from the votes (same store
                        # as a deck's like, whose thread root is a comment too)
                        "points": c["points"]
                        + sum(1 if v == 1 else -1 for (_u, r), v in self.votes.items() if r == c["id"]),
                        "userInput": self.votes.get((who or "", c["id"]), 0),
                        "childrenCount": len(tree(c["id"])),
                        "children": {"count": 0, "results": tree(c["id"])},
                    }
                    for c in kids
                ]

            top = tree(root)
            return httpx.Response(
                200,
                json={
                    "id": root,
                    "title": None,
                    "text": None,
                    "owner": {
                        "id": self._user_id(deck["owner"]["username"]),
                        "username": deck["owner"]["username"],
                    },
                    "parent": None,
                    "originalPost": None,
                    "deck": {"id": deck["id"]},
                    "childrenCount": len(top),
                    "children": {
                        "links": {"next": None, "previous": None},
                        "count": len(top),
                        "results": top,
                    },
                    "createdAt": "2026-10-01T10:00:00Z",
                    "points": 0,
                    "userInput": 0,
                    "archived": False,
                    "locked": False,
                    "type": 4,
                },
            )
        if len(parts) == 4 and parts[1:3] == ["decks", "editors"] and request.method == "DELETE":
            for deck_id, rows in self.editors.items():
                row = next((r for r in rows if str(r["id"]) == parts[3]), None)
                if row is not None:
                    if self.decks.get(deck_id, {}).get("owner", {}).get("username") != who:
                        return httpx.Response(403, json={"detail": "You do not have permission."})
                    rows.remove(row)
                    return httpx.Response(204)
            return httpx.Response(404, json={"detail": "Not found."})
        if len(parts) >= 3 and parts[1] == "decks" and parts[2].isdigit():
            deck = self.decks.get(int(parts[2]))
            mine = deck is not None and deck["owner"]["username"] == who
            if deck is None or (not mine and int(parts[2]) in self.private):
                return httpx.Response(404, json={"detail": "Not found."})
            if len(parts) == 3 and request.method == "GET":
                if self.fail_deck_reads:
                    return httpx.Response(503, json={"detail": "Service unavailable."})
                return httpx.Response(200, json=self._deck_for(deck, who))
            if parts[3:] == ["tagRelations"] and request.method == "GET":
                rels = self.deck_tags.get(deck["id"], [])
                return httpx.Response(200, json={"count": len(rels), "results": [dict(r) for r in rels]})
            if parts[3:] == ["bookmarks"] and request.method in ("POST", "DELETE"):
                marks = self.bookmarks.setdefault(who, set())
                (marks.add if request.method == "POST" else marks.discard)(deck["id"])
                return httpx.Response(200 if request.method == "POST" else 204, json={"ok": True})
            if not mine:
                return httpx.Response(
                    403, json={"detail": "You do not have permission to perform this action."}
                )
            if parts[3:] == ["editors"] and request.method == "GET":
                rows = self.editors.get(deck["id"], [])
                return httpx.Response(200, json={"count": len(rows), "results": [dict(r) for r in rows]})
            if parts[3:] == ["editors"] and request.method == "POST":
                uid = json.loads(request.content).get("user")
                name = self._username(uid) if isinstance(uid, int) else None
                if name is None:
                    return httpx.Response(400, json={"user": ["Invalid pk - object does not exist."]})
                self.next_editor_id += 1
                row = {
                    "id": self.next_editor_id,
                    "user": {"id": uid, "username": name, "avatar": None},
                    "createdBy": {"id": self._user_id(who), "username": who, "avatar": None},
                    "createdAt": "2026-10-10T00:00:00Z",
                }
                self.editors.setdefault(deck["id"], []).append(row)
                return httpx.Response(201, json=row)
            if len(parts) == 3 and request.method == "DELETE":
                del self.decks[deck["id"]]
                self.private.discard(deck["id"])
                self.deck_tags.pop(deck["id"], None)
                self.deleted.append(deck["id"])
                return httpx.Response(204)
            if parts[3:] == ["update"] and request.method == "PATCH":
                if self.fail_deck_update:
                    return httpx.Response(503, json={"detail": "Service unavailable."})
                body = json.loads(request.content)
                self.updates.append({"deck_id": deck["id"], **body})
                for key in ("name", "description", "deckFormat", "edhBracket", "private", "unlisted"):
                    if key in body:
                        deck[key] = body[key]
                if "customFeatured" in body or "featured" in body:
                    # the site's "deck image": a card art URL, or an empty customFeatured for automatic
                    deck["customFeatured"] = body.get("customFeatured") or ""
                    deck["featured"] = body.get("featured") or (
                        ""
                        if "customFeatured" in body and "featured" not in body
                        else deck.get("featured", "")
                    )
                if "private" in body:
                    (self.private.add if body["private"] else self.private.discard)(deck["id"])
                deck["updatedAt"] = "2026-10-03T00:00:00Z"
                return httpx.Response(200, json={"id": deck["id"], "name": deck["name"]})
            if parts[3:] == ["modifyCards", "v2"] and request.method == "PATCH":
                body = json.loads(request.content)
                self.patches.append(body)
                if not self.fail_patch_silently:
                    self.apply_patch(deck, body["cards"])
                return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": "Not found."})

    def printing(self, card_id: int) -> dict[str, Any]:
        """The card object for a printing id: a CARD_DB card, or a printing seen in any deck
        (so a removed row can be added back by its id, as a restore does)."""
        for p in PRINTINGS:
            if p["id"] == card_id:
                return {
                    "id": card_id,
                    "collectorNumber": p["collectorNumber"],
                    "edition": {"editioncode": p["edition"]["editioncode"]},
                    "oracleCard": p["oracleCard"],
                }
        for v in CARD_DB.values():
            if v["id"] == card_id:
                return {
                    "id": card_id,
                    "collectorNumber": "1",
                    "edition": {"editioncode": "xxx"},
                    "oracleCard": v["oracleCard"],
                }
        for d in self.decks.values():
            for c in d["cards"]:
                if c["card"]["id"] == card_id:
                    return json.loads(json.dumps(c["card"]))
        known = getattr(self, "_known_printings", {}).get(card_id)
        if known:  # a printing every deck here has since let go of (the live site still knows it)
            return {k: v for k, v in json.loads(json.dumps(known)).items() if k != "options"}
        raise AssertionError(f"unknown printing id {card_id}")

    def ignore_empty_label_or_set(self, entry: dict[str, Any]) -> bool:
        """A fault a verify must catch: a modify with an empty label leaves the old tag."""
        return self.empty_label_ignored and not entry["modifications"]["label"]

    def apply_patch(self, deck: dict[str, Any], entries: list[dict[str, Any]]) -> None:
        """Apply entries in the reference shape: action add|modify|remove, cardid, deckRelationId,
        modifications.quantity. A modify also sets the row's categories (an empty list reads back
        as null, as the live site sends it) and finish."""
        by_rel = {c["id"]: c for c in deck["cards"]}
        for e in entries:
            assert e["action"] in ("add", "modify", "remove"), e
            assert "patchId" in e and "modifications" in e, e
            qty = e["modifications"]["quantity"]
            if e["action"] in ("modify", "remove"):
                rel = by_rel[e["deckRelationId"]]
                rel["quantity"] = 0 if e["action"] == "remove" else qty
                if e["action"] == "modify":  # a modify carries the row's categories and finish too
                    rel["categories"] = e.get("categories") or None
                    rel["modifier"] = e["modifications"].get("modifier", rel.get("modifier", "Normal"))
                    if "label" in e["modifications"] and not self.ignore_empty_label_or_set(e):
                        rel["label"] = e["modifications"]["label"]  # the colour tag, "Name,#rrggbb"
                    if "companion" in e["modifications"]:
                        rel["companion"] = bool(e["modifications"]["companion"])
                    if "customCmc" in e["modifications"]:  # the deck's own mana value for the row
                        rel["customCmc"] = e["modifications"]["customCmc"]
            else:
                self.next_rel_id += 1
                deck["cards"].append(
                    {
                        "id": self.next_rel_id,
                        "quantity": qty,
                        "categories": e.get("categories") or None,
                        "modifier": "Normal"
                        if self.add_rows_lose_finish
                        else e["modifications"].get("modifier", "Normal"),
                        "card": self.printing(self.add_rows_swap_printing.get(e["cardid"], e["cardid"])),
                    }
                )
                for cat in e.get("categories") or []:
                    if not any(c["name"] == cat for c in deck["categories"]):
                        # A category named for the first time appears on the deck. Maybeboard is
                        # not counted in the deck on archidekt.com (verified on existing decks
                        # 2026-10-07); whether a brand-new deck gets that flag the same way is
                        # not verified live.
                        deck["categories"].append(
                            {"name": cat, "isPremier": False, "includedInDeck": cat != "Maybeboard"}
                        )
        deck["cards"] = [c for c in deck["cards"] if c["quantity"] > 0]
        deck["updatedAt"] = "2026-10-03T00:00:00Z"
