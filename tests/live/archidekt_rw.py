"""Live Archidekt read-write check through the gateway's own code paths.

Runs the real gateway app in-process (MCP tools over its OAuth tokens, the
browser account and review pages with their CSRF forms) against live
archidekt.com. Only the identity provider is the test stand-in from
tests/conftest.py. Nothing is mocked on the Archidekt side unless --offline.

    # Logic check with no network (in-memory Archidekt stand-in):
    python -m tests.live.archidekt_rw --offline --write

    # Live, read-only: link, list, read, propose and reject (no deck changes):
    ARCHIDEKT_TEST_USERNAME=... ARCHIDEKT_TEST_PASSWORD=... python -m tests.live.archidekt_rw

    # Live writes: creates a private "MAG-TEST ..." deck and edits only that deck:
    ARCHIDEKT_TEST_USERNAME=... ARCHIDEKT_TEST_PASSWORD=... python -m tests.live.archidekt_rw --write

Safety rules enforced in code:
* Live mode refuses to start unless ARCHIDEKT_TEST_USERNAME and
  ARCHIDEKT_TEST_PASSWORD are set. The password is never printed or saved.
* Edits are proposed and applied only for decks this run created, or a deck
  passed with --deck-id whose name starts with "MAG-TEST". Any other deck
  aborts the run before a proposal is made, and so does any deck listed in
  ARCHIDEKT_TEST_NEVER_TOUCH (comma-separated ids), whatever its name.
* Applies go through the browser review page, the gateway's default path.
* The script cannot delete decks (the gateway has no delete call); it prints
  the ids of the decks it created so a person can delete them on Archidekt.

Output: one line per step and a JSON summary (--out). Token values never
appear in it; for the login response only field names and the access token's
lifetime are recorded.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from mtg_gateway.archidekt import ArchidektClient, Pacer
from mtg_gateway.decks import DeckError, _clean_deck_id
from tests.conftest import GATEWAY, FakeIdP, Harness, make_settings, running, sse_json

PREFIX = "MAG-TEST"
# Real decks no live check may ever write to, on top of the name-prefix rule below:
# comma-separated ids in ARCHIDEKT_TEST_NEVER_TOUCH.
NEVER_TOUCH = {d.strip() for d in os.environ.get("ARCHIDEKT_TEST_NEVER_TOUCH", "").split(",") if d.strip()}
LIVE_BASE = "https://archidekt.com/api"
USER_AGENT = "mtg-assistant-gateway/0.1 (+https://github.com/TheUncleBen/MTG-Assistant-Gateway) live-check"

# Every card here is also in tests/fake_archidekt.py's CARD_DB, so --offline runs the same plan.
NEW_DECK_CARDS = [
    {"card_name": "Aesi, Tyrant of Gyre Strait", "quantity": 1, "category": "Commander"},
    {"card_name": "Sol Ring", "quantity": 1, "category": "Ramp"},
    {"card_name": "Arcane Signet", "quantity": 1, "category": "Ramp"},
    {"card_name": "Island", "quantity": 10},
    {"card_name": "Forest", "quantity": 10},
]
# Cards the card lookup is known to miss (report finding F2); added one at a time as probes.
PROBE_CARDS = ["Swamp", "Opt", "Delver of Secrets"]
EDITS = [
    {"action": "add", "card_name": "Rampant Growth", "quantity": 1, "category": "Ramp"},
    {"action": "remove", "card_name": "Arcane Signet"},
    {"action": "set_quantity", "card_name": "Island", "quantity": 12},
]


class Abort(Exception):
    pass


class Run:
    def __init__(
        self,
        h: Harness,
        *,
        write: bool,
        deck_id: str | None,
        mcp_apply: bool = False,
        extra_only: bool = False,
    ):
        self.h = h
        self.extra_only = extra_only
        self.write = write
        self.mcp_apply = mcp_apply
        self.deck_id = deck_id
        self.created: set[str] = set()
        self.steps: list[dict[str, Any]] = []
        self.login_info: dict[str, Any] = {}
        self.browser = httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url=GATEWAY)
        self.token = ""

    # -- recording ------------------------------------------------------------
    def step(self, name: str, ok: bool, evidence: Any, *, expected: str = "", probe: bool = False) -> bool:
        """Record a step. A probe documents known behaviour and does not fail the run."""
        text = evidence if isinstance(evidence, str) else json.dumps(evidence, default=str)
        self.steps.append(
            {"step": name, "ok": ok, "probe": probe, "expected": expected, "evidence": text[:1500]}
        )
        label = ("PROBE-OK" if ok else "PROBE-NO") if probe else ("PASS" if ok else "FAIL")
        print(f"{label}  {name}: {text[:220]}", flush=True)
        return ok

    # -- gateway access ---------------------------------------------------------
    async def tool(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        r = await self.h.mcp(self.token, "tools/call", {"name": name, "arguments": args or {}}, rid=9)
        msg = sse_json(r)
        if "error" in msg:
            return {"ok": False, "error": "jsonrpc", "message": json.dumps(msg["error"])}
        result = msg["result"]
        out = result.get("structuredContent")
        if out is None:
            out = {"ok": not result.get("isError"), "text": result.get("content")}
        return out

    async def browser_login(self) -> None:
        r = await self.browser.get("/login", params={"next": "/account"})
        cb = await self.h.idp_leg(r)
        p = urlparse(cb)
        r2 = await self.browser.get(f"{p.path}?{p.query}")
        if r2.status_code != 302:
            raise Abort(f"browser sign-in failed: HTTP {r2.status_code}")

    async def csrf(self, path: str) -> str:
        r = await self.browser.get(path)
        m = re.search(r"name='csrf' value='([0-9a-f]+)'", r.text)
        if not m:
            raise Abort(f"no CSRF field on {path} (HTTP {r.status_code})")
        return m.group(1)

    async def review_page(self, pid: str, action: str) -> httpx.Response:
        csrf = await self.csrf(f"/proposals/{pid}")
        return await self.browser.post(f"/proposals/{pid}", data={"csrf": csrf, "action": action})

    async def apply(self, pid: str) -> dict[str, Any]:
        """Apply the way this run is configured: the review page, or apply_proposal over MCP.
        Returns the proposal afterwards, plus what the apply call itself said."""
        said: dict[str, Any]
        if self.mcp_apply:
            said = await self.tool("apply_proposal", {"proposal_id": pid})
        else:
            r = await self.review_page(pid, "apply")
            said = {"http": r.status_code, "location": r.headers.get("location")}
        done = await self.tool("get_proposal", {"proposal_id": pid})
        done["apply_said"] = said
        return done

    # -- guard ------------------------------------------------------------------
    async def touchable(self, deck_id: str) -> dict[str, Any]:
        try:
            deck_id = _clean_deck_id(deck_id)  # a URL or an id: compare the bare numeric id
        except DeckError as exc:
            raise Abort(f"not a deck id: {deck_id!r}") from exc
        if deck_id in NEVER_TOUCH:
            raise Abort(f"refusing to touch deck {deck_id}: it is a real deck")
        deck = await self.tool("get_my_deck", {"deck_id": deck_id})
        if not deck.get("ok"):
            raise Abort(f"cannot read deck {deck_id}: {deck}")
        if deck_id not in self.created and not str(deck.get("name", "")).startswith(PREFIX):
            raise Abort(
                f"refusing to touch deck {deck_id} ({deck.get('name')!r}): not created by this run "
                f"and its name does not start with {PREFIX!r}"
            )
        return deck

    # -- plan -------------------------------------------------------------------
    async def go(self, login: str, password: str) -> None:
        client = await self.h.register()
        self.token = (await self.h.tokens_for(client))["access_token"]
        await self.browser_login()

        # 1. Link the account through the account page, as a person would.
        csrf = await self.csrf("/account")
        r = await self.browser.post(
            "/account",
            data={
                "csrf": csrf,
                "action": "link",
                "archidekt_login": login,
                "archidekt_password": password,
                "accept_risk": "1",
            },
        )
        linked = r.status_code == 303 and r.headers.get("location") == "/account?ok=linked"
        detail = {
            "http": r.status_code,
            "location": r.headers.get("location"),
            "login_response": self.login_info,
        }
        if not linked:
            m = re.search(r"class='notice[^']*'[^>]*>(.*?)</", r.text, re.S)
            detail["page_error"] = m.group(1).strip() if m else r.text[:300]
        if not self.step("link account (account page)", linked, detail):
            raise Abort("linking failed; nothing else can run")
        status = await self.tool("account_status")
        self.step("account_status after link", status.get("linked") is True, status)

        # 2. Reads.
        decks = await self.tool("list_my_decks")
        owners_ok = decks.get("ok") is True
        self.step(
            "list_my_decks",
            owners_ok,
            {
                "ok": decks.get("ok"),
                "count": len(decks.get("decks", [])),
                "first": decks.get("decks", [])[:5],
                "error": decks.get("message"),
            },
        )
        # The listed decks must belong to the linked account: read up to three and check the owner.
        me = str(status.get("archidekt_username") or "").lower()
        owners = []
        for d in decks.get("decks", [])[:3]:
            got = await self.tool("get_deck", {"deck_ref": d["id"]})
            owners.append(str(got.get("owner", "")))
        self.step(
            "list_my_decks lists only the linked account's decks",
            all(o.lower() == me for o in owners),
            {"linked_as": me, "owners_of_first_listed": owners},
        )
        target = self.deck_id
        if target:
            deck = await self.touchable(target)
            self.step(
                f"get_my_deck {target} (named {PREFIX}*)",
                True,
                {"name": deck.get("name"), "card_count": deck.get("card_count")},
            )
            if self.extra_only and self.write:
                # Only the details, category, phone-edit and refresh checks, on this MAG-TEST deck.
                await self.details_and_categories(target, str(deck.get("name")))
                await self.phone_edit(target)
                await self.finish_printing_clone(target)
                await self.forced_refresh()
                return

        # 3. Propose a new deck and reject it on the review page (no Archidekt write).
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        name = f"{PREFIX} e2e {stamp}"
        prop = await self.tool(
            "propose_new_deck",
            {"name": name, "deck_format": "commander", "cards": NEW_DECK_CARDS, "private": True},
        )
        self.step(
            "propose_new_deck (stored only)", prop.get("ok") is True and prop.get("state") == "pending", prop
        )
        if prop.get("ok"):
            r = await self.review_page(prop["proposal_id"], "reject")
            after = await self.tool("get_proposal", {"proposal_id": prop["proposal_id"]})
            self.step(
                "reject on review page",
                after.get("state") == "rejected",
                {"http": r.status_code, "state": after.get("state")},
            )
        if not self.write:
            self.step(
                "writes skipped", True, "run with --write (after the owner's go-ahead) to create and edit"
            )
            return

        # 4. Create a private MAG-TEST deck through propose + review-page apply.
        prop = await self.tool(
            "propose_new_deck",
            {"name": name, "deck_format": "commander", "cards": NEW_DECK_CARDS, "private": True},
        )
        if not self.step("propose_new_deck", prop.get("ok") is True, prop.get("diff", prop)):
            raise Abort("could not propose the test deck")
        how = "apply_proposal over MCP" if self.mcp_apply else "review page"
        if not self.mcp_apply:
            mcp_apply = await self.tool("apply_proposal", {"proposal_id": prop["proposal_id"]})
            self.step(
                "apply_proposal over MCP is refused (browser only)",
                mcp_apply.get("error") == "browser_required",
                mcp_apply,
                expected="browser_required",
            )
        done = await self.apply(prop["proposal_id"])
        new_id = str((done.get("result") or {}).get("deck_id") or "")
        if new_id:
            self.created.add(new_id)
        self.step(
            f"create deck via {how}",
            done.get("state") == "applied" and bool(new_id),
            {"said": done.get("apply_said"), "state": done.get("state"), "result": done.get("result")},
        )
        if not new_id:
            raise Abort("deck was not created")

        # 5. Private reads of the new deck: with the session, and get_deck's fallback.
        deck = await self.touchable(new_id)
        counts = {c["name"]: c["quantity"] for c in deck.get("cards", [])}
        cats = {c["name"]: c["categories"] for c in deck.get("cards", [])}
        want = {c["card_name"]: c["quantity"] for c in NEW_DECK_CARDS}
        self.step("private deck read (get_my_deck)", counts == want, {"counts": counts})
        self.step("commander set on create", "Commander" in cats.get("Aesi, Tyrant of Gyre Strait", []), cats)
        self.step("category set on create", "Ramp" in cats.get("Sol Ring", []), cats)
        anyread = await self.tool("get_deck", {"deck_ref": f"https://archidekt.com/decks/{new_id}"})
        self.step(
            "get_deck falls back to the session for a private deck",
            anyread.get("ok") is True,
            {"ok": anyread.get("ok"), "error": anyread.get("message")},
        )
        listed = await self.tool("list_my_decks")
        self.step(
            "list_my_decks includes the new deck",
            any(d.get("id") == new_id for d in listed.get("decks", [])),
            {"count": len(listed.get("decks", [])), "first": listed.get("decks", [])[:3]},
        )

        # 6. Edit: add (with category), remove, change quantity.
        await self.touchable(new_id)
        prop = await self.tool("propose_deck_changes", {"deck_id": new_id, "changes": EDITS})
        self.step("propose_deck_changes", prop.get("ok") is True, prop.get("diff", prop))
        if prop.get("ok"):
            done = await self.apply(prop["proposal_id"])
            self.step(
                f"apply edits via {how}",
                done.get("state") == "applied",
                {"said": done.get("apply_said"), "state": done.get("state"), "result": done.get("result")},
            )
            deck = await self.touchable(new_id)
            counts = {c["name"]: c["quantity"] for c in deck.get("cards", [])}
            cats = {c["name"]: c["categories"] for c in deck.get("cards", [])}
            self.step("edit result: Rampant Growth added", counts.get("Rampant Growth") == 1, counts)
            self.step("edit result: Rampant Growth category", "Ramp" in cats.get("Rampant Growth", []), cats)
            self.step("edit result: Arcane Signet removed", "Arcane Signet" not in counts, counts)
            self.step("edit result: Island quantity 12", counts.get("Island") == 12, counts)
            self.step(
                "edit result: untouched cards keep categories",
                "Commander" in cats.get("Aesi, Tyrant of Gyre Strait", [])
                and "Ramp" in cats.get("Sol Ring", []),
                cats,
            )

        # 7. Stale protection: a proposal made before another change must not apply.
        old = await self.tool(
            "propose_deck_changes",
            {
                "deck_id": new_id,
                "changes": [{"action": "set_quantity", "card_name": "Forest", "quantity": 9}],
            },
        )
        newer = await self.tool(
            "propose_deck_changes",
            {
                "deck_id": new_id,
                "changes": [{"action": "set_quantity", "card_name": "Island", "quantity": 11}],
            },
        )
        if old.get("ok") and newer.get("ok"):
            await self.apply(newer["proposal_id"])
            o = await self.apply(old["proposal_id"])
            self.step(
                "stale proposal refused",
                o.get("state") == "failed" and (o.get("result") or {}).get("error") == "stale",
                {"state": o.get("state"), "result": o.get("result")},
            )

        # 7b. Deck details, categories, a phone edit and a forced session refresh.
        await self.details_and_categories(new_id, name)
        await self.phone_edit(new_id)
        await self.finish_printing_clone(new_id)
        await self.forced_refresh()

        # 8. Probes of known card-lookup limits (F2): one proposal per card, so one failure
        # does not hide another. Recorded as probes; they do not fail the run.
        for card in PROBE_CARDS:
            await self.touchable(new_id)
            p = await self.tool(
                "propose_deck_changes",
                {"deck_id": new_id, "changes": [{"action": "add", "card_name": card, "quantity": 1}]},
            )
            if not p.get("ok"):
                self.step(f"probe: add {card}", False, p, probe=True)
                continue
            d = await self.apply(p["proposal_id"])
            self.step(
                f"probe: add {card}",
                d.get("state") == "applied",
                {"state": d.get("state"), "result": d.get("result"), "said": d.get("apply_said")},
                probe=True,
            )

    async def details_and_categories(self, new_id: str, name: str) -> None:
        """propose_deck_details and set_category / set_commander, applied and read back."""
        how = "apply_proposal over MCP" if self.mcp_apply else "review page"
        await self.touchable(new_id)
        details = {
            "name": f"{name} renamed",
            "description": "MTG Assistant Gateway live check",
            "edh_bracket": 2,
        }
        prop = await self.tool("propose_deck_details", {"deck_id": new_id, "details": details})
        self.step("propose_deck_details", prop.get("ok") is True, prop.get("diff", prop))
        if prop.get("ok"):
            done = await self.apply(prop["proposal_id"])
            self.step(
                f"apply deck details via {how}",
                done.get("state") == "applied",
                {"said": done.get("apply_said"), "state": done.get("state"), "result": done.get("result")},
            )
            deck = await self.touchable(new_id)
            got = {k: deck.get(k) for k in ("name", "description", "edh_bracket", "private", "format")}
            self.step("details result: name", got["name"] == details["name"], got)
            self.step("details result: description", got["description"] == details["description"], got)
            self.step("details result: bracket 2", got["edh_bracket"] == 2, got)
            self.step("details result: still private", got["private"] is True, got)
            second = {"edh_bracket": None, "deck_format": "commander"}
            prop = await self.tool("propose_deck_details", {"deck_id": new_id, "details": second})
            if prop.get("ok"):
                done = await self.apply(prop["proposal_id"])
                deck = await self.touchable(new_id)
                self.step(
                    "details result: bracket cleared",
                    done.get("state") == "applied" and deck.get("edh_bracket") is None,
                    {"state": done.get("state"), "edh_bracket": deck.get("edh_bracket")},
                )
            else:
                self.step("propose_deck_details (clear bracket)", False, prop)
        await self.touchable(new_id)
        changes = [
            {"action": "set_category", "card_name": "Sol Ring", "category": "Artifacts"},
            {"action": "set_commander", "card_name": "Rampant Growth"},
        ]
        prop = await self.tool("propose_deck_changes", {"deck_id": new_id, "changes": changes})
        self.step("propose set_category + set_commander", prop.get("ok") is True, prop.get("diff", prop))
        if prop.get("ok"):
            done = await self.apply(prop["proposal_id"])
            self.step(
                f"apply category changes via {how}",
                done.get("state") == "applied",
                {"said": done.get("apply_said"), "state": done.get("state"), "result": done.get("result")},
            )
            deck = await self.touchable(new_id)
            cats = {c["name"]: c["categories"] for c in deck.get("cards", [])}
            counts = {c["name"]: c["quantity"] for c in deck.get("cards", [])}
            self.step(
                "category result: Sol Ring in Artifacts only", cats.get("Sol Ring") == ["Artifacts"], cats
            )
            self.step(
                "category result: Rampant Growth is the commander",
                "Commander" in cats.get("Rampant Growth", []),
                cats,
            )
            self.step(
                "category result: Aesi no longer Commander",
                "Commander" not in cats.get("Aesi, Tyrant of Gyre Strait", []),
                cats,
            )
            self.step(
                "category result: counts unchanged",
                counts.get("Sol Ring") == 1 and counts.get("Rampant Growth") == 1,
                counts,
            )
            # put the commander back
            back = [{"action": "set_commander", "card_name": "Aesi, Tyrant of Gyre Strait"}]
            prop = await self.tool("propose_deck_changes", {"deck_id": new_id, "changes": back})
            if prop.get("ok"):
                done = await self.apply(prop["proposal_id"])
                deck = await self.touchable(new_id)
                cats = {c["name"]: c["categories"] for c in deck.get("cards", [])}
                self.step(
                    "category result: Aesi restored as commander, Rampant Growth demoted",
                    done.get("state") == "applied"
                    and "Commander" in cats.get("Aesi, Tyrant of Gyre Strait", [])
                    and "Commander" not in cats.get("Rampant Growth", []),
                    {"state": done.get("state"), "cats": cats},
                )

    async def phone_edit(self, new_id: str) -> None:
        """The companion editor's path: the edit page, then the JSON API with the session cookie
        and CSRF header, then Apply on the review page."""
        await self.touchable(new_id)
        page = await self.browser.get(f"/decks/{new_id}/edit", headers={"Sec-Fetch-Mode": "navigate"})
        self.step(
            "edit page renders for the test deck",
            page.status_code == 200 and "editor-config" in page.text and "/static/companion.js" in page.text,
            {"http": page.status_code, "csp": page.headers.get("content-security-policy")},
        )
        csrf = await self.csrf("/account")
        body = {
            "kind": "edit",
            "deck_id": new_id,
            "changes": [{"action": "set_quantity", "card_name": "Forest", "quantity": 8}],
        }
        r = await self.browser.post("/api/v1/proposals", json=body, headers={"X-CSRF-Token": csrf})
        created = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        self.step(
            "phone edit: POST /api/v1/proposals with cookie + CSRF",
            r.status_code == 201 and created.get("ok") is True,
            {"http": r.status_code, "diff": created.get("diff"), "error": created.get("message")},
        )
        nocsrf = await self.browser.post("/api/v1/proposals", json=body)
        self.step(
            "phone edit: the same call without the CSRF header is refused",
            nocsrf.status_code == 403,
            {"http": nocsrf.status_code},
            expected="403",
        )
        if created.get("ok"):
            pid = created["proposal_id"]
            r = await self.review_page(pid, "apply")
            done = await self.tool("get_proposal", {"proposal_id": pid})
            deck = await self.touchable(new_id)
            counts = {c["name"]: c["quantity"] for c in deck.get("cards", [])}
            self.step(
                "phone edit: applied on the review page, Forest is 8",
                done.get("state") == "applied" and counts.get("Forest") == 8,
                {"http": r.status_code, "state": done.get("state"), "counts": counts},
            )
            hist = await self.browser.get(f"/api/v1/decks/{new_id}/history")
            h = hist.json() if hist.status_code == 200 else {}
            self.step(
                "phone edit: deck history lists the proposal and a snapshot",
                any(p.get("id") == pid for p in h.get("proposals", [])) and bool(h.get("snapshots")),
                {
                    "http": hist.status_code,
                    "proposals": len(h.get("proposals", [])),
                    "snapshots": len(h.get("snapshots", [])),
                },
            )

    async def finish_printing_clone(self, new_id: str) -> None:
        """set_finish, set_printing and a clone, each proposed over MCP and applied on the review
        page; the deck (and the copy) is re-read afterwards."""
        await self.touchable(new_id)

        def sol_ring(deck: dict[str, Any]) -> list[dict[str, Any]]:
            return [c for c in deck.get("cards", []) if c["name"] == "Sol Ring"]

        def printing_of(r: dict[str, Any]) -> tuple[str, str]:
            return (str(r.get("set") or "").lower(), str(r.get("collector_number") or ""))

        before = sol_ring(await self.touchable(new_id))
        prop = await self.tool(
            "propose_deck_changes",
            {
                "deck_id": new_id,
                "changes": [{"action": "set_finish", "card_name": "Sol Ring", "finish": "foil"}],
            },
        )
        self.step("propose set_finish Sol Ring -> foil", prop.get("ok") is True, prop.get("diff", prop))
        if prop.get("ok"):
            done = await self.apply(prop["proposal_id"])
            rows = sol_ring(await self.touchable(new_id))
            self.step(
                "set_finish applied: Sol Ring is Foil",
                done.get("state") == "applied"
                and rows
                and all(str(r.get("finish")).lower() == "foil" for r in rows),
                {"state": done.get("state"), "before": before, "after": rows, "result": done.get("result")},
            )
        rows = sol_ring(await self.touchable(new_id))
        current = {
            (str(r.get("set_code") or r.get("set") or "").lower(), str(r.get("collector_number")))
            for r in rows
        }
        target = ("cmr", "472") if ("cmr", "472") not in current else ("sld", "1074")
        prop = await self.tool(
            "propose_deck_changes",
            {
                "deck_id": new_id,
                "changes": [
                    {
                        "action": "set_printing",
                        "card_name": "Sol Ring",
                        "set_code": target[0],
                        "collector_number": target[1],
                    }
                ],
            },
        )
        self.step(
            f"propose set_printing Sol Ring -> {target}", prop.get("ok") is True, prop.get("diff", prop)
        )
        if prop.get("ok"):
            done = await self.apply(prop["proposal_id"])
            rows = sol_ring(await self.touchable(new_id))
            got = {
                (str(r.get("set_code") or r.get("set") or "").lower(), str(r.get("collector_number")))
                for r in rows
            }
            self.step(
                f"set_printing applied: Sol Ring is {target}, one row, quantity kept",
                done.get("state") == "applied" and got == {target} and sum(r["quantity"] for r in rows) == 1,
                {"state": done.get("state"), "rows": rows, "result": done.get("result")},
            )
        prop = await self.tool("propose_clone_deck", {"deck_id": new_id})
        self.step("propose_clone_deck", prop.get("ok") is True, prop.get("diff", prop))
        if prop.get("ok"):
            done = await self.apply(prop["proposal_id"])
            copy_id = str((done.get("result") or {}).get("deck_id") or "")
            if copy_id:
                self.created.add(copy_id)
            source = await self.touchable(new_id)
            copy = await self.touchable(copy_id) if copy_id else {}
            same = {c["name"]: c["quantity"] for c in source.get("cards", [])} == {
                c["name"]: c["quantity"] for c in copy.get("cards", [])
            }
            self.step(
                "clone applied: copy exists in the root folder with the same cards",
                done.get("state") == "applied" and bool(copy_id) and same,
                {"state": done.get("state"), "result": done.get("result"), "copy_name": copy.get("name")},
            )

    async def forced_refresh(self) -> None:
        """Make the stored access token unusable and check the gateway refreshes it against the
        live refresh route before the next call (the same path an expired token takes)."""
        from cryptography.fernet import Fernet

        db = self.h.db
        link = db.get_link("user-1")
        if not link:
            self.step("forced refresh: link present", False, "no active link")
            return
        f = Fernet(self.h.settings.fernet_key.encode())
        blob = json.loads(f.decrypt(link["secret_enc"].encode()))
        if not blob.get("refresh"):
            self.step("forced refresh: refresh token stored", False, {"keys": sorted(blob)})
            return
        blob["access"] = "x.y.z"  # unreadable: the gateway must refresh before using it
        db.update_link_secret("user-1", f.encrypt(json.dumps(blob).encode()).decode())
        before = len(
            [e for e in db.audit_for_user("user-1", 500) if e.get("event") == "archidekt_session_refreshed"]
        )
        out = await self.tool("list_my_decks")
        after = [
            e for e in db.audit_for_user("user-1", 500) if e.get("event") == "archidekt_session_refreshed"
        ]
        new_blob = json.loads(f.decrypt(db.get_link("user-1")["secret_enc"].encode()))
        self.step(
            "forced refresh: list works after the gateway refreshed the session",
            out.get("ok") is True
            and len(after) == before + 1
            and new_blob.get("access") not in ("x.y.z", None),
            {
                "ok": out.get("ok"),
                "error": out.get("message"),
                "refresh_events": len(after) - before,
                "new_access_claim_names": sorted(_claims(new_blob.get("access", ""))),
            },
        )

    async def cleanup(self) -> None:
        try:
            if self.token:
                csrf = await self.csrf("/account")
                await self.browser.post("/account", data={"csrf": csrf, "action": "unlink"})
                st = await self.tool("account_status")
                self.step("cleanup: unlink (stored session deleted)", st.get("linked") is False, st)
        except Exception as exc:  # cleanup must not hide the run's own result
            self.step("cleanup: unlink", False, f"{type(exc).__name__}: {exc}")
        await self.browser.aclose()


def _claims(token: str) -> dict[str, Any]:
    try:
        seg = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
    except (IndexError, ValueError):
        return {}


def _record_login(info: dict[str, Any]):
    """httpx response hook: keep the login response's field names and token lifetime, never values."""

    async def hook(response: httpx.Response) -> None:
        if not response.request.url.path.endswith("/rest-auth/login/"):
            return
        await response.aread()
        info["http"] = response.status_code
        try:
            body = response.json()
        except ValueError:
            info["body"] = "non-JSON"
            return
        if not isinstance(body, dict):
            info["shape"] = type(body).__name__
            return
        info["fields"] = sorted(body)
        if isinstance(body.get("user"), dict):
            info["user_fields"] = sorted(body["user"])
        for key in ("access_token", "access", "token", "jwt", "refresh_token", "refresh"):
            val = body.get(key)
            if isinstance(val, str) and val.count(".") == 2:
                try:
                    seg = val.split(".")[1]
                    claims = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
                    if "exp" in claims and "iat" in claims:
                        info[f"{key}_lifetime_seconds"] = int(claims["exp"]) - int(claims["iat"])
                    info[f"{key}_claim_names"] = sorted(claims)
                except (ValueError, TypeError):
                    info[f"{key}_jwt"] = "unreadable"
        if "non_field_errors" in body:
            info["error"] = body["non_field_errors"]

    return hook


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="use the in-memory Archidekt stand-in")
    ap.add_argument("--write", action="store_true", help="create and edit a MAG-TEST deck")
    ap.add_argument("--deck-id", help=f"an existing deck to read; its name must start with {PREFIX!r}")
    ap.add_argument("--mcp-apply", action="store_true", help="apply with apply_proposal (auto approval mode)")
    ap.add_argument("--out", help="write the JSON summary here")
    ap.add_argument(
        "--extra-only",
        action="store_true",
        help="with --write and --deck-id: run only the deck-details, category, phone-edit and refresh checks",
    )
    ap.add_argument("--pace", type=float, default=1.0, help="seconds between live Archidekt requests")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    for noisy in ("httpx", "mcp", "mtg_gateway"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    login_info: dict[str, Any] = {}
    if args.offline:
        from tests.fake_archidekt import FakeArchidekt

        ark = FakeArchidekt()
        base = "https://ark.test/api"
        http = httpx.AsyncClient(
            transport=ark.transport, event_hooks={"response": [_record_login(login_info)]}
        )
        login, password = "alice", "pw-alice"
        pacer = Pacer(0.0)
    else:
        login = os.environ.get("ARCHIDEKT_TEST_USERNAME", "")
        password = os.environ.get("ARCHIDEKT_TEST_PASSWORD", "")
        if not login or not password:
            print("refusing to run: set ARCHIDEKT_TEST_USERNAME and ARCHIDEKT_TEST_PASSWORD", file=sys.stderr)
            return 2
        base = LIVE_BASE
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0), event_hooks={"response": [_record_login(login_info)]}
        )
        pacer = Pacer(args.pace)

    with tempfile.TemporaryDirectory() as tmp:
        settings = make_settings(
            Path(tmp),
            writes_enabled=True,
            approval_mode_default="auto" if args.mcp_apply else "manual",
            archidekt_base=base,
        )
        client = ArchidektClient(base, USER_AGENT, pacer, http=http)
        async with running(Harness(settings, FakeIdP(), archidekt=client)) as h:
            run = Run(
                h,
                write=args.write,
                deck_id=args.deck_id,
                mcp_apply=args.mcp_apply,
                extra_only=args.extra_only,
            )
            run.login_info = login_info
            aborted = None
            try:
                await run.go(login, password)
            except Abort as exc:
                aborted = str(exc)
                run.step("aborted", False, aborted)
            finally:
                await run.cleanup()
    summary = {
        "mode": "offline" if args.offline else "live",
        "write": args.write,
        "apply_path": "mcp" if args.mcp_apply else "review page",
        "when_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "created_decks_to_delete_by_hand": sorted(run.created),
        "login_response": login_info,
        "aborted": aborted,
        "passed": sum(s["ok"] for s in run.steps if not s["probe"]),
        "failed": sum(not s["ok"] for s in run.steps if not s["probe"]),
        "probes": {s["step"]: s["ok"] for s in run.steps if s["probe"]},
        "steps": run.steps,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "steps"}, indent=1))
    if run.created:
        print(
            "Delete these test decks on Archidekt by hand:",
            ", ".join(f"https://archidekt.com/decks/{d}" for d in sorted(run.created)),
        )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
