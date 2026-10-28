"""A fake Scryfall built from recorded fixtures (tests/fixtures/scan, recorded live on 2026-10-04).

It answers the four endpoints the gateway uses, with the real response shapes,
and records every request so tests can assert on pacing and caching.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

from mtg_gateway.archidekt_csv import parse_export

FIXTURES = Path(__file__).parent / "fixtures" / "scan"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _oracle(card: dict[str, Any]) -> str | None:
    faces = card.get("card_faces") or []
    return card.get("oracle_id") or (faces[0].get("oracle_id") if faces else None)


def _split(query: str) -> list[str]:
    """Top-level search terms: spaces outside brackets and quotes split them."""
    out: list[str] = []
    depth = 0
    quoted = False
    cur = ""
    for ch in query:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        if ch == " " and depth == 0 and not quoted:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _archidekt_cards() -> dict[str, dict[str, Any]]:
    """Every card the fake Archidekt knows (its card pool and the sample deck), as Scryfall
    cards keyed by normalised name. Real Scryfall knows every card Archidekt has, so an exact
    lookup of one of them must not say "no such card" (the deck tools refuse an add that Scryfall
    says is no card). Built from the sample deck's export where it has the card, else name only."""
    from .fake_archidekt import CARD_DB, FIXTURE, PRINTINGS

    out: dict[str, dict[str, Any]] = {}
    for c in parse_export(FIXTURE.read_text(encoding="utf-8")):
        out[_norm(c.name)] = {
            "object": "card",
            "id": c.scryfall_id,
            "name": c.name,
            "set": c.set_code,
            "collector_number": c.collector_number,
            "mana_cost": "".join(f"{{{s}}}" for s in c.mana_cost),
            "cmc": c.mana_value,
            "type_line": c.types,
            "oracle_text": c.card_text,
            "rarity": c.rarity,
        }
    for name in [v["oracleCard"]["name"] for v in CARD_DB.values()] + [
        p["oracleCard"]["name"] for p in PRINTINGS
    ]:
        out.setdefault(_norm(name), {"object": "card", "name": name})
    # an exact lookup by a double-faced card's front face finds the card, as on Scryfall
    for card in list(out.values()):
        out.setdefault(_norm(card["name"].split("//")[0]), card)
    return out


# Layouts Scryfall's search counts as extras (left out unless include_extras=true).
EXTRA_LAYOUTS = ("token", "double_faced_token", "emblem", "art_series", "planar", "scheme", "vanguard")


class FakeScryfall:
    def __init__(self) -> None:
        self.cards: list[dict[str, Any]] = []
        seen: set[str] = set()
        for f in sorted(FIXTURES.glob("*.json")):
            data = _load(f.name)
            objs = data["data"] if data.get("object") == "list" else [data]
            for c in objs:
                if isinstance(c, dict) and c.get("object") == "card" and c["id"] not in seen:
                    seen.add(c["id"])
                    self.cards.append(c)
        self.archidekt_cards = _archidekt_cards()
        self.fuzzy = {
            "sol rng": _load("named_fuzzy_sol_rng.json"),  # Scryfall really answers Oathsworn Giant
            "aesi": _load("named_fuzzy_aesi_ambiguous.json"),
            "aesi tyrant of gyre stralt": _load("named_fuzzy_aesi_ocr_typo.json"),
            "fire ice": _load("named_fuzzy_fire_ice.json"),
        }
        self.autocomplete = {
            "aesi": _load("autocomplete_aesi.json"),
            "sol r": _load("autocomplete_sol_r.json"),
            "sol rng": _load("autocomplete_sol_rng.json"),
        }
        # Answers for the is:commander name search that the fixtures cannot give (several
        # commanders sharing a name part), set by a test: query text -> card names.
        self.commanders: dict[str, list[str]] = {}
        self.requests: list[tuple[str, str]] = []
        # /catalog/card-names: None answers 404 (the gateway then falls back to autocomplete).
        self.catalog: list[str] | None = None
        self.fail_with: int | None = None
        self.rulings: dict[str, list[dict[str, Any]]] = {}  # card id -> Scryfall ruling objects
        # Today's prices by card id, set by a test to move prices from day to day (prices.py);
        # cards not named keep their recorded prices. collection_bodies: every identifier list.
        self.prices: dict[str, dict[str, str | None]] = {}
        self.collection_bodies: list[list[dict[str, str]]] = []
        # The card search (/cards): page size (Scryfall's is 175; a test sets it small to page),
        # extra cards a test adds to search over, and the queries the gateway sent.
        self.page_size = 175
        self.extra_cards: list[dict[str, Any]] = []
        self.searches: list[dict[str, str]] = []
        self.print_searches: list[dict[str, str]] = []  # the printing picker's oracleid: searches
        self.sets: list[dict[str, Any]] = [
            {
                "object": "set",
                "code": "c16",
                "name": "Commander 2016",
                "set_type": "commander",
                "released_at": "2016-11-11",
                "digital": False,
            },
            {
                "object": "set",
                "code": "ice",
                "name": "Ice Age",
                "set_type": "expansion",
                "released_at": "1995-06-03",
                "block_code": "ice",
                "block": "Ice Age",
                "digital": False,
            },
            {
                "object": "set",
                "code": "csp",
                "name": "Coldsnap",
                "set_type": "expansion",
                "released_at": "2006-07-21",
                "block_code": "ice",
                "block": "Ice Age",
                "digital": False,
            },
        ]
        self.catalogs: dict[str, list[str]] = {
            "card-types": ["Artifact", "Creature", "Instant", "Land", "Sorcery"],
            "supertypes": ["Basic", "Legendary"],
            "creature-types": ["Goblin", "Time Lord", "Elemental"],
        }

    def names_catalog(self) -> list[str]:
        """An offline card-name catalog (``/catalog/card-names``): the recorded cards' names, then
        filler names enough to pass the gateway's size check (a real catalog holds 1,000 or more)."""
        return sorted({c["name"] for c in self.cards}) + [f"Filler Card {i}" for i in range(1200)]

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), base_url="https://scryfall.test")

    # -- lookups ---------------------------------------------------------------
    def by_name(self, name: str, set_code: str | None = None) -> dict[str, Any] | None:
        n = _norm(name)
        for c in self.cards:
            if set_code and c["set"] != set_code.lower():
                continue
            if _norm(c["name"]) == n or _norm(c["name"].split("//")[0]) == n:
                return c
        return None

    def by_print(self, set_code: str, number: str) -> dict[str, Any] | None:
        for c in self.cards + self.extra_cards:
            if c["set"] == set_code.lower() and c["collector_number"].lower() == number.lower():
                return c
        return None

    # -- the card search: a small part of Scryfall's syntax, enough for the page's own tests ----
    def matches(self, card: dict[str, Any], term: str) -> bool:
        neg = term.startswith("-")
        t = term[1:] if neg else term
        if t.startswith("(") and t.endswith(")"):
            inner = t[1:-1]
            parts = re.split(r" (?:OR|or) ", inner)
            if len(parts) > 1:
                hit = any(self.matches(card, p.strip()) for p in parts)
            else:
                hit = all(self.matches(card, p) for p in _split(inner))
            return hit != neg
        m = re.fullmatch(r"([a-z]+)(:|>=|<=|=|<|>|!=)(.+)", t)
        if not m:
            hit = _norm(t) in _norm(card["name"])
            return hit != neg
        key, op, value = m.group(1), m.group(2), m.group(3).strip('"').lower()
        if key == "game":
            hit = value in (card.get("games") or [])
        elif key == "type":
            hit = value in (card.get("type_line") or "").lower()
        elif key == "oracle":
            hit = value in (card.get("oracle_text") or "").lower()
        elif key == "rarity":
            hit = (card.get("rarity") or "")[:1] == value[:1]
        elif key == "set":
            hit = card.get("set") == value
        elif key == "color":
            have = set(card.get("colors") or [])
            want = set(value.upper()) - {"C"}
            hit = {"=": have == want, ">=": have >= want, "<=": have <= want}.get(op, False)
        elif key == "cmc":
            v = float(card.get("cmc") or 0)
            n = float(value)
            hit = {"=": v == n, ">=": v >= n, "<=": v <= n, "<": v < n, ">": v > n, "!=": v != n}[op]
        elif key in ("unique", "include", "prefer", "lang", "legal", "is", "commander", "block", "artist"):
            hit = True  # read but not modelled here
        else:
            return False
        return hit != neg

    def search(self, q: dict[str, str]) -> httpx.Response:
        query = q.get("q", "")
        self.searches.append(dict(q))
        if "bad:" in query:
            return httpx.Response(
                400, json={"object": "error", "code": "bad_request", "details": "unknown keyword bad"}
            )
        unique_prints = "unique:prints" in query
        pool = self.cards + self.extra_cards
        found = [c for c in pool if all(self.matches(c, t) for t in _split(query))]
        if not unique_prints:  # one card per name, as Scryfall's default unique=cards
            seen: set[str] = set()
            once = []
            for c in found:
                if c["name"] not in seen:
                    seen.add(c["name"])
                    once.append(c)
            found = once
        found.sort(key=lambda c: c["name"], reverse=q.get("dir") == "desc")
        if not found:
            return self.not_found("Your query didn’t match any cards.")
        page = int(q.get("page") or 1)
        start = (page - 1) * self.page_size
        chunk = found[start : start + self.page_size]
        more = start + self.page_size < len(found)
        return httpx.Response(
            200, json={"object": "list", "total_cards": len(found), "has_more": more, "data": chunk}
        )

    def printings(self, q: dict[str, str], request: httpx.Request) -> httpx.Response:
        """One card's printings (``oracleid:<id>``, the printing picker's search): the terms
        ``is:nonfoil|foil|etched``, ``frame:`` and ``border:`` narrow it, ``order`` released or
        usd with ``dir`` sorts it, and it pages at ``page_size`` with Scryfall's ``has_more`` and
        ``next_page`` (this same search, ``page`` one higher)."""
        self.print_searches.append(dict(q))
        terms = q.get("q", "").split()
        # several cards' printings at once: "oracleid:a or oracleid:b" (prices.py, cheapest printing)
        wanted: set[str] = set()
        while len(terms) >= 3 and terms[1] == "or" and terms[0].startswith("oracleid:"):
            wanted.add(terms[0].split(":", 1)[1])
            terms = terms[2:]
        m2 = re.fullmatch(r"oracleid:([0-9a-f-]+)", terms[0] if terms else "")
        if not m2:
            return httpx.Response(400, json={"object": "error", "details": "unsupported query"})
        wanted.add(m2.group(1))
        found = [c for c in self.cards + self.extra_cards if _oracle(c) in wanted]
        if self.prices:  # today's prices a test set, as /cards/collection answers them
            found = [
                {**c, "prices": {**(c.get("prices") or {}), **self.prices[c["id"]]}}
                if c["id"] in self.prices
                else c
                for c in found
            ]
        if q.get("include_extras") != "true":  # Scryfall leaves tokens, emblems and the like out
            found = [c for c in found if c.get("layout") not in EXTRA_LAYOUTS]
        for t in terms[1:]:
            key, _, value = t.partition(":")
            if key == "is" and value in ("nonfoil", "foil", "etched"):
                found = [c for c in found if value in (c.get("finishes") or [])]
            elif key == "frame":
                found = [c for c in found if str(c.get("frame") or "") == value]
            elif key == "border":
                found = [c for c in found if str(c.get("border_color") or "") == value]
            elif key == "set":
                found = [c for c in found if str(c.get("set") or "") == value]
            elif key == "cn":
                found = [c for c in found if str(c.get("collector_number") or "") == value.strip('"')]
            else:
                return httpx.Response(400, json={"object": "error", "details": f"unsupported term {t}"})
        if q.get("order") == "usd":
            priced = [c for c in found if (c.get("prices") or {}).get("usd")]
            unpriced = [c for c in found if not (c.get("prices") or {}).get("usd")]
            priced.sort(key=lambda c: float(c["prices"]["usd"]), reverse=q.get("dir") == "desc")
            found = priced + unpriced  # as Scryfall: a printing with no price comes last
        else:
            found.sort(key=lambda c: c.get("released_at", ""), reverse=q.get("dir", "desc") != "asc")
        if not found:
            return self.not_found("Your query didn’t match any cards.")
        page = int(q.get("page") or 1)
        start = (page - 1) * self.page_size
        chunk = found[start : start + self.page_size]
        if not chunk:
            return self.not_found("You have paged past the end of the results.")
        body: dict[str, Any] = {
            "object": "list",
            "total_cards": len(found),
            "has_more": start + self.page_size < len(found),
            "data": chunk,
        }
        if body["has_more"]:
            nxt = {**q, "page": str(page + 1)}
            body["next_page"] = str(request.url.copy_with(params=nxt))
        return httpx.Response(200, json=body)

    @staticmethod
    def not_found(detail: str) -> httpx.Response:
        return httpx.Response(
            404, json={"object": "error", "code": "not_found", "status": 404, "details": detail}
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, str(request.url)))
        assert request.headers.get("user-agent"), "Scryfall requires a User-Agent"
        assert request.headers.get("accept") == "application/json"
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"object": "error", "details": "boom"})
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        if path == "/cards/named":
            if "exact" in q:
                card = self.by_name(q["exact"], q.get("set"))
                if card is None and not q.get("set"):
                    card = self.archidekt_cards.get(_norm(q["exact"]))
                return (
                    httpx.Response(200, json=card)
                    if card
                    else self.not_found(f"No cards found matching “{q['exact']}”")
                )
            key = _norm(q.get("fuzzy", ""))
            exact = self.by_name(key, q.get("set"))
            if exact:
                return httpx.Response(200, json=exact)
            canned = {_norm(k): v for k, v in self.fuzzy.items()}.get(key)
            if canned is not None and not q.get("set"):
                return httpx.Response(canned.get("status", 200), json=canned)
            return self.not_found(f"No cards found matching “{q.get('fuzzy')}”")
        if path == "/catalog/card-names":
            if self.catalog is None:
                return self.not_found("no catalog in this fake")
            return httpx.Response(
                200, json={"object": "catalog", "total_values": len(self.catalog), "data": self.catalog}
            )
        if path == "/cards/autocomplete":
            canned = self.autocomplete.get(q.get("q", "").strip().lower())
            if canned is None:
                names = sorted(
                    {c["name"] for c in self.cards if _norm(c["name"]).startswith(_norm(q.get("q", "")))}
                )
                canned = {"object": "catalog", "total_values": len(names), "data": names}
            return httpx.Response(200, json=canned)
        m = re.fullmatch(r"/cards/([0-9a-f-]{36})/rulings", path)
        if m:
            if not any(c["id"] == m.group(1) for c in self.cards):
                return self.not_found("No card found with the given ID")
            rows = self.rulings.get(m.group(1), [])
            return httpx.Response(200, json={"object": "list", "has_more": False, "data": rows})
        m = re.fullmatch(r"/cards/([a-z0-9]+)/([A-Za-z0-9★-]+)", path)
        if m:
            card = self.by_print(m.group(1), m.group(2))
            return httpx.Response(200, json=card) if card else self.not_found("No card found")
        if path == "/cards/search" and q.get("q", "").startswith("is:commander "):
            m3 = re.fullmatch(r'is:commander name:"([^"]+)"', q["q"])
            if not m3:
                return httpx.Response(400, json={"object": "error", "details": "unsupported query"})
            text = m3.group(1).lower()
            names = self.commanders.get(text)
            if names is None:
                names = sorted(
                    {
                        c["name"]
                        for c in self.cards
                        if text in c["name"].lower()
                        and "Legendary" in (c.get("type_line") or "")
                        and "Creature" in (c.get("type_line") or "")
                    }
                )
            if not names:
                return self.not_found("Your query didn’t match any cards.")
            data = [{"object": "card", "name": n} for n in names]
            return httpx.Response(
                200, json={"object": "list", "total_cards": len(data), "has_more": False, "data": data}
            )
        if path == "/cards/search" and not q.get("q", "").startswith("oracleid:"):
            return self.search(q)
        if path == "/sets":
            return httpx.Response(200, json={"object": "list", "has_more": False, "data": self.sets})
        m4 = re.fullmatch(r"/catalog/([a-z-]+)", path)
        if m4 and m4.group(1) != "card-names":
            values = self.catalogs.get(m4.group(1), [])
            return httpx.Response(
                200, json={"object": "catalog", "total_values": len(values), "data": values}
            )
        if path == "/cards/search":
            return self.printings(q, request)
        if path == "/cards/collection" and request.method == "POST":
            body = json.loads(request.content)
            idents = body["identifiers"]
            assert len(idents) <= 75
            self.collection_bodies.append(idents)
            found, missing = [], []
            for ident in idents:
                if "id" in ident:
                    card = next((c for c in self.cards if c["id"] == ident["id"]), None)
                elif "collector_number" in ident:
                    card = self.by_print(ident["set"], ident["collector_number"])
                elif "//" in ident["name"]:
                    # Scryfall's collection lookup knows a two-part card by its front face only:
                    # {"name": "Fire // Ice"} is not_found (checked 2026-10-28)
                    card = None
                else:
                    card = self.by_name(ident["name"], ident.get("set"))
                if card and card["id"] in self.prices:
                    card = {**card, "prices": {**(card.get("prices") or {}), **self.prices[card["id"]]}}
                (found.append(card) if card else missing.append(ident))
            return httpx.Response(200, json={"object": "list", "not_found": missing, "data": found})
        return httpx.Response(404, json={"object": "error", "details": f"unknown path {path}"})
