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

FIXTURES = Path(__file__).parent / "fixtures" / "scan"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _oracle(card: dict[str, Any]) -> str | None:
    faces = card.get("card_faces") or []
    return card.get("oracle_id") or (faces[0].get("oracle_id") if faces else None)


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
        self.fail_with: int | None = None

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
        for c in self.cards:
            if c["set"] == set_code.lower() and c["collector_number"].lower() == number.lower():
                return c
        return None

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
        if path == "/cards/autocomplete":
            canned = self.autocomplete.get(q.get("q", "").strip().lower())
            if canned is None:
                names = sorted(
                    {c["name"] for c in self.cards if _norm(c["name"]).startswith(_norm(q.get("q", "")))}
                )
                canned = {"object": "catalog", "total_values": len(names), "data": names}
            return httpx.Response(200, json=canned)
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
        if path == "/cards/search":
            m2 = re.fullmatch(r"oracleid:([0-9a-f-]+)", q.get("q", ""))
            if not m2:
                return httpx.Response(400, json={"object": "error", "details": "unsupported query"})
            found = [c for c in self.cards if _oracle(c) == m2.group(1)]
            found.sort(key=lambda c: c.get("released_at", ""), reverse=True)  # as Scryfall: newest first
            if not found:
                return self.not_found("Your query didn’t match any cards.")
            return httpx.Response(
                200, json={"object": "list", "total_cards": len(found), "has_more": False, "data": found}
            )
        if path == "/cards/collection" and request.method == "POST":
            body = json.loads(request.content)
            idents = body["identifiers"]
            assert len(idents) <= 75
            found, missing = [], []
            for ident in idents:
                if "collector_number" in ident:
                    card = self.by_print(ident["set"], ident["collector_number"])
                else:
                    card = self.by_name(ident["name"], ident.get("set"))
                (found.append(card) if card else missing.append(ident))
            return httpx.Response(200, json={"object": "list", "not_found": missing, "data": found})
        return httpx.Response(404, json={"object": "error", "details": f"unknown path {path}"})
