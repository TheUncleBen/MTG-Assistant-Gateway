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
        "options": ["Etched"],
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
        self.patches: list[dict[str, Any]] = []
        self.fail_patch_silently = False
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
        self.transport = httpx.MockTransport(self.handle)

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

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
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
            return httpx.Response(200, json=deck)
        # The deck listing answers anonymous callers too (the public deck search; verified live
        # 2026-10-07); private decks are dropped from it further down.
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
            self.search_params.append(dict(request.url.params))
            results = [
                {
                    "id": d["id"],
                    "name": d["name"],
                    "owner": {"id": self.users[d["owner"]["username"]]["id"], **d["owner"]},
                    "updatedAt": d["updatedAt"],
                    "deckFormat": 3,
                    "private": d["id"] in self.private,
                    # Live listings carry the deck's folder (verified 2026-10-05); decks in the
                    # root folder are reported here as null.
                    "parentFolderId": self.deck_folder.get(d["id"]),
                    "parentFolderName": self._folder_name(
                        d["owner"]["username"], self.deck_folder.get(d["id"])
                    ),
                    # Listing extras as the live v3 rows carry them (seen 2026-10-05).
                    "size": sum(c["quantity"] for c in d["cards"]),
                    "edhBracket": d.get("edhBracket"),
                    "colors": {"W": 0, "U": 12, "B": 0, "R": 0, "G": 14},
                    "tags": (
                        [{"id": 1, "name": "ramp"}, {"id": 2, "name": "sea monsters"}]
                        if d["id"] == 42
                        else []
                    ),
                }
                for d in chosen
            ]
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
                    for p in PRINTINGS
                    if p["edition"]["editioncode"] == edition
                    and name in p["oracleCard"]["name"].lower()
                    and (number is None or p["collectorNumber"] == number)
                ]
                return httpx.Response(200, json={"results": hits[:page]})
            if request.url.params.get("exact") == "true":
                hit = CARD_DB.get(name)
                return httpx.Response(200, json={"results": [hit] if hit else []})
            fillers = [
                {"id": 70000 + i, "oracleCard": {"name": f"{name.title()} Lookalike {i}"}} for i in range(30)
            ]
            real = [v for k, v in CARD_DB.items() if name in k]
            return httpx.Response(200, json={"results": (fillers + real)[:page]})
        if len(parts) >= 3 and parts[1] == "decks" and parts[2].isdigit():
            deck = self.decks.get(int(parts[2]))
            mine = deck is not None and deck["owner"]["username"] == who
            if deck is None or (not mine and int(parts[2]) in self.private):
                return httpx.Response(404, json={"detail": "Not found."})
            if len(parts) == 3 and request.method == "GET":
                if self.fail_deck_reads:
                    return httpx.Response(503, json={"detail": "Service unavailable."})
                return httpx.Response(200, json=deck)
            if not mine:
                return httpx.Response(
                    403, json={"detail": "You do not have permission to perform this action."}
                )
            if parts[3:] == ["update"] and request.method == "PATCH":
                if self.fail_deck_update:
                    return httpx.Response(503, json={"detail": "Service unavailable."})
                body = json.loads(request.content)
                self.updates.append({"deck_id": deck["id"], **body})
                for key in ("name", "description", "deckFormat", "edhBracket", "private", "unlisted"):
                    if key in body:
                        deck[key] = body[key]
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
        raise AssertionError(f"unknown printing id {card_id}")

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
            else:
                self.next_rel_id += 1
                deck["cards"].append(
                    {
                        "id": self.next_rel_id,
                        "quantity": qty,
                        "categories": e.get("categories") or None,
                        "modifier": e["modifications"].get("modifier", "Normal"),
                        "card": self.printing(e["cardid"]),
                    }
                )
        deck["cards"] = [c for c in deck["cards"] if c["quantity"] > 0]
        deck["updatedAt"] = "2026-10-03T00:00:00Z"
