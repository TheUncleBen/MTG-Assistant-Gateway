"""The account-wide parts of the in-memory Archidekt (parity release 2): category templates,
personal default categories, the color tag list, pins, custom cards and picture uploads, deck
help requests, reports, the registration PDF, the private profile, notifications and contest
entries. Routes and bodies follow REQUESTS.md (archidekt.com's public scripts, 2026-10-22);
nothing here was recorded from archidekt.com, and the replies are kept to the fields the site's
own pages read."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .fake_archidekt import FakeArchidekt


def _ok(body: Any = None, status: int = 200) -> httpx.Response:
    if body is None:
        return httpx.Response(204 if status == 200 else status)
    return httpx.Response(status, json=body)


def _bad(field: str, text: str) -> httpx.Response:
    return httpx.Response(400, json={field: [text]})


class AccountFake:
    def __init__(self, ark: FakeArchidekt) -> None:
        self.ark = ark
        self.templates: dict[str, list[dict[str, Any]]] = {}
        self.defaults: dict[str, dict[int, list[str]]] = {}  # who -> oracle card id -> names
        self.tags: dict[str, list[dict[str, Any]]] = {}
        self.pins: dict[str, list[dict[str, Any]]] = {}
        self.customs: dict[str, list[dict[str, Any]]] = {}
        self.uploads: list[tuple[str, bytes, str]] = []  # (who, body, Content-Disposition)
        self.help: dict[int, dict[str, Any]] = {}  # deck id -> request
        self.reports: list[dict[str, Any]] = []
        self.pdfs: list[dict[str, Any]] = []
        self.private: dict[str, dict[str, Any]] = {}
        self.notes: dict[str, list[dict[str, Any]]] = {}
        self.entries: dict[int, list[dict[str, Any]]] = {}  # thread id -> submissions
        self.package_imports: dict[int, int] = {}  # package deck id -> incrementPackageImport reads
        self.next_id = 7000
        self.max_pins = 150

    def _new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def profile(self, who: str) -> dict[str, Any]:
        """The member's private profile as /users/{id}/private/ answers it."""
        uid = self.ark.users[who]["id"]
        return self.private.setdefault(
            who,
            {
                "id": uid,
                "username": who,
                "profile": {
                    "bio": '{"ops":[]}',
                    "avatar": "",
                    "contactable": True,
                    "newsletterable": False,
                    "comment_notification_emailable": True,
                    "is_collection_public": False,
                },
                "accountSettings": {
                    "deckView": 0,
                    "deckStack": 0,
                    "deckSorting": 0,
                    "deckPrices": [0, 1],
                    "searchUnique": 1,
                    "noUseDefaultCategories": False,
                    "searchGame": 1,
                    "show_custom_uploads": True,
                    "noArchidektNotifications": False,
                },
            },
        )

    def handle(self, request: httpx.Request, who: str, path: str) -> httpx.Response | None:  # noqa: C901, PLR0911, PLR0912
        m = request.method
        parts = path.strip("/").split("/")[1:]  # without "api"
        body: Any = None
        if request.content and m in ("POST", "PATCH", "PUT") and "upload" not in path:
            body = json.loads(request.content)
        uid = self.ark.users[who]["id"]
        # -- category templates
        if parts[:2] == ["users", "category"]:
            mine = self.templates.setdefault(who, [])
            if len(parts) == 2 and m == "GET":
                return _ok(mine)
            if len(parts) == 2 and m == "POST":
                t = {k: body[k] for k in ("name", "includedInDeck", "includedInPrice", "isPremier")}
                t["id"] = self._new_id()
                mine.append(t)
                return _ok(t, 201)
            tid = int(parts[2])
            t = next((x for x in mine if x["id"] == tid), None)
            if t is None:
                return httpx.Response(404, json={"detail": "Not found."})
            if m == "PATCH":
                t.update({k: body[k] for k in ("name", "includedInDeck", "includedInPrice", "isPremier")})
                return _ok(t)
            if m == "DELETE":
                mine.remove(t)
                for names in self.defaults.get(who, {}).values():  # as Archidekt warns it does
                    while t["name"] in names:
                        names.remove(t["name"])
                return _ok()
        # -- personal default categories
        if parts[:2] == ["users", "globalCategory"]:
            mine_d = self.defaults.setdefault(who, {})
            if len(parts) == 2 and m == "GET":
                page = int(request.url.params.get("page") or 1)
                rows = [
                    {"oracleCardId": oid, "name": self._card_name(oid), "globalCategories": list(names)}
                    for oid, names in sorted(mine_d.items())
                    if names
                ]
                chunk = rows[(page - 1) * 100 : page * 100]
                return _ok(
                    {
                        "total": len(rows),
                        "next": page + 1 if page * 100 < len(rows) else None,
                        "previous": page - 1 if page > 1 else None,
                        "categories": self.templates.get(who, []),
                        "cards": chunk,
                    }
                )
            if len(parts) == 4 and parts[2] == "oracleCard" and m == "POST":
                names = body.get("categories")
                known = {t["name"] for t in self.templates.get(who, [])}
                if any(n not in known for n in names):
                    return _bad("categories", "Not a category template.")
                mine_d[int(parts[3])] = list(names)
                return _ok({"oracleCardId": int(parts[3]), "categories": names})
        # -- color tags
        if parts[:2] == ["decks", "colorTags"]:
            mine_t = self.tags.setdefault(who, [])
            if len(parts) == 2 and m == "GET":
                return _ok(mine_t)
            if len(parts) == 2 and m == "POST":
                if len(body["name"]) > 32:
                    return _bad("name", "Ensure this field has no more than 32 characters.")
                t = {"id": self._new_id(), "name": body["name"], "color": body["color"]}
                mine_t.append(t)
                return _ok(t, 201)
            t = next((x for x in mine_t if x["id"] == int(parts[2])), None)
            if t is None:
                return httpx.Response(404, json={"detail": "Not found."})
            if m == "PATCH":
                t.update(name=body["name"], color=body["color"])
                return _ok(t)
            if m == "DELETE":
                mine_t.remove(t)
                return _ok()
        # -- pins
        if parts[:2] == ["users", "pins"]:
            mine_p = self.pins.setdefault(who, [])
            if len(parts) == 2 and m == "GET":
                return _ok({"cards": mine_p})
            if len(parts) == 2 and m == "POST":
                if len(mine_p) + len(body["cards"]) > self.max_pins:
                    return _bad("cards", "You can pin at most 150 cards.")
                made = []
                for c in body["cards"]:
                    p = {
                        "id": self._new_id(),
                        "card": self._card(c["cardId"]),
                        "notes": c["notes"],
                        "modifier": c["modifier"],
                        "quantity": c["quantity"],
                    }
                    mine_p.append(p)
                    made.append(p)
                return _ok(made[0], 201)
            if parts[2:] == ["deleteAll"] and m == "DELETE":
                mine_p.clear()
                return _ok()
            p = next((x for x in mine_p if x["id"] == int(parts[2])), None)
            if p is None:
                return httpx.Response(404, json={"detail": "Not found."})
            if m == "PATCH":
                p.update(
                    card=self._card(body["card"]),
                    quantity=body["quantity"],
                    notes=body["notes"],
                    modifier=body["modifier"],
                )
                return _ok(p)
            if m == "DELETE":
                mine_p.remove(p)
                return _ok()
        # -- custom cards and pictures
        if parts[:2] == ["decks", "customCards"]:
            mine_c = self.customs.setdefault(who, [])
            if len(parts) == 2 and m == "GET":
                q = (request.url.params.get("search") or "").casefold()
                rows = sorted(
                    (c for c in mine_c if q in (c.get("frontName") or "").casefold()),
                    key=lambda c: c["frontName"],
                )
                return _ok({"count": len(rows), "next": None, "previous": None, "results": rows})
            if len(parts) == 2 and m == "POST":
                made = []
                for c in body["customCards"]:
                    if c.get("owner") != uid:
                        return _bad("owner", "Not you.")
                    made.append({**c, "id": self._new_id()})
                mine_c.extend(made)
                return _ok({"customCards": made}, 201)
            c = next((x for x in mine_c if x["id"] == int(parts[2])), None)
            if c is None:
                return httpx.Response(404, json={"detail": "Not found."})
            if m == "PATCH":
                c.update({**body, "id": c["id"]})
                return _ok(c)
            if m == "DELETE":
                # Archidekt blanks it in every deck and keeps the record; gone from the list
                mine_c.remove(c)
                return _ok()
        if parts == ["users", "uploadImage"] and m == "PUT":
            self.uploads.append((who, request.content, request.headers.get("Content-Disposition", "")))
            return _ok({"imageUrl": f"https://storage.example.com/uploads/{len(self.uploads)}.png"})
        if parts[:2] == ["users", "uploadAvatar"] and m == "PUT":
            if int(parts[2]) != uid:
                return httpx.Response(403, json={"detail": "Not you."})
            self.uploads.append((who, request.content, request.headers.get("Content-Disposition", "")))
            url = f"https://storage.example.com/avatars/{len(self.uploads)}.png"
            self.profile(who)["profile"]["avatar"] = url
            return _ok(url)
        # -- deck help requests
        if len(parts) == 3 and parts[0] == "decks" and parts[2] == "help" and m == "POST":
            deck_id = int(parts[1])
            deck = self.ark.decks.get(deck_id)
            if deck is None or deck["owner"]["username"] != who:
                return httpx.Response(403, json={"detail": "Not your deck."})
            h = {"id": self._new_id(), **body, "createdAt": "2026-10-22T12:00:00Z", "deck": deck_id}
            self.help[deck_id] = h
            return _ok(h, 201)
        if parts[:2] == ["decks", "help"] and len(parts) == 3 and m == "PATCH":
            h = next((x for x in self.help.values() if x["id"] == int(parts[2])), None)
            if h is None:
                return httpx.Response(404, json={"detail": "Not found."})
            h.update(body)
            return _ok(h)
        # -- reports
        if parts == ["users", "moderator", "report"] and m == "POST":
            self.reports.append({**body, "by": who})
            return _ok({"ok": True}, 201)
        # -- card packages: a deck with a cardPackage block
        if parts == ["decks", "packages"] and m == "POST":
            self.ark.next_deck_id += 1
            deck = {
                "id": self.ark.next_deck_id,
                "name": body["name"],
                "owner": {"username": who},
                "updatedAt": "2026-10-03T00:00:00Z",
                "deckFormat": body.get("deckFormat"),
                "private": body.get("private", False),
                "categories": [],
                "cards": [],
                "cardPackage": {**body["cardPackage"], "id": self._new_id(), "importCount": 0},
            }
            self.ark.decks[deck["id"]] = deck
            if deck["private"]:
                self.ark.private.add(deck["id"])
            return _ok({"id": deck["id"], "name": deck["name"]}, 201)
        if parts[:2] == ["decks", "packages"] and len(parts) == 3 and m == "PATCH":
            for d in self.ark.decks.values():
                block = d.get("cardPackage")
                if isinstance(block, dict) and block["id"] == int(parts[2]):
                    if d["owner"]["username"] != who:
                        return httpx.Response(403, json={"detail": "Not your package."})
                    block.update({k: body[k] for k in ("keywords", "budget", "intendedFormats") if k in body})
                    return _ok(block)
            return httpx.Response(404, json={"detail": "Not found."})
        if (
            len(parts) == 2
            and parts[0] == "decks"
            and parts[1].isdigit()
            and m == "GET"
            and request.url.params.get("incrementPackageImport") == "true"
        ):
            did = int(parts[1])
            block = (self.ark.decks.get(did) or {}).get("cardPackage")
            if isinstance(block, dict):
                block["importCount"] = block.get("importCount", 0) + 1
                self.package_imports[did] = self.package_imports.get(did, 0) + 1
            return None  # the deck itself, as any read answers it
        if parts == ["decks", "v3"] and m == "GET" and request.url.params.get("packages") == "true":
            mine = [
                d
                for d in self.ark.decks.values()
                if isinstance(d.get("cardPackage"), dict)
                and d["owner"]["username"] == request.url.params.get("ownerUsername")
            ]
            mine.sort(key=lambda d: -d["cardPackage"].get("importCount", 0))
            rows = [{**self.ark._listing_row(d), "cardPackage": dict(d["cardPackage"])} for d in mine]
            return _ok({"count": len(rows), "next": None, "results": rows})
        # -- the registration PDF
        if parts == ["decks", "exportPdf"] and m == "POST":
            self.pdfs.append({**body, "by": who})
            return _ok({"fileUrl": f"https://storage.example.com/pdf/{len(self.pdfs)}.pdf"})
        # -- the private profile
        if parts[0] == "users" and len(parts) == 3 and parts[2] == "private":
            if int(parts[1]) != uid:
                return httpx.Response(403, json={"detail": "Not you."})
            me = self.profile(who)
            if m == "GET":
                return _ok(me)
            if m == "PATCH":
                if "username" in body:
                    name = body["username"]
                    if name != who and name in self.ark.users:
                        return _bad("username", "A user with that username already exists.")
                    me["username"] = name
                me["profile"].update(body.get("profile") or {})
                me["accountSettings"].update(body.get("accountSettings") or {})
                return _ok(me)
        # -- notifications
        if parts[0] == "users" and len(parts) == 3 and parts[2] in ("notificationCount", "notifications"):
            if int(parts[1]) != uid:
                return httpx.Response(403, json={"detail": "Not you."})
            notes = self.notes.get(who, [])
            if parts[2] == "notificationCount":
                return _ok(
                    {"notificationCount": sum(1 for n in notes if not n.get("seen")), "patreonAccount": None}
                )
            return _ok({"notifications": notes})
        # -- contest entries
        if parts[:2] == ["comments", "contestSubmission"]:
            if parts[2:3] == ["decks"] and m == "GET":
                return _ok({"results": self.entries.get(int(parts[3]), [])})
            if len(parts) == 2 and m == "POST":
                deck = self.ark.decks.get(body["deck"])
                if deck is None or deck["owner"]["username"] != who or body["owner"] != uid:
                    return httpx.Response(403, json={"detail": "Not your deck."})
                e = {
                    "id": self._new_id(),
                    "deck": {"id": deck["id"], "name": deck["name"]},
                    "owner": {"id": uid, "username": who},
                }
                self.entries.setdefault(body["parentPost"], []).append(e)
                return _ok(e, 201)
            if parts[2:3] == ["delete"] and m == "DELETE":
                sid = int(parts[3])
                for rows in self.entries.values():
                    for e in list(rows):
                        if e["id"] == sid and e["owner"]["id"] == uid:
                            rows.remove(e)
                            return _ok()
                return httpx.Response(404, json={"detail": "Not found."})
        return None

    def _card(self, card_id: int) -> dict[str, Any]:
        from .fake_archidekt import CARD_DB

        found = next((c for c in CARD_DB.values() if c["id"] == card_id), None)
        if found is None:
            return {"id": card_id, "oracleCard": {"name": f"Card {card_id}", "id": card_id}}
        return {**found, "oracleCard": {**found["oracleCard"], "id": found["id"]}}

    def _card_name(self, oracle_id: int) -> str:
        return self._card(oracle_id)["oracleCard"]["name"]
