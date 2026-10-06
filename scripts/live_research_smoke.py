#!/usr/bin/env python3
"""Call every Mystic Forge research tool through the gateway against the live services.

Used by .github/workflows/live-research-smoke.yml. Read-only: every tool here
only reads from Scryfall, EDHREC, Commander Spellbook, MTGJSON, Wizards and
Archidekt's public API; nothing is written anywhere. LIVE_SMOKE_DECK_ID names the
public Archidekt deck the deck tools read (any public deck works; a Commander deck
exercises the most checks).

    python3 scripts/live_research_smoke.py --url http://localhost:8080/mcp \
        --token-file token.txt --out results.json

Exit code is 0 when every allowlisted tool passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mtg_gateway.mf_proxy import ALLOWED_TOOLS

DECK_ID = os.environ.get("LIVE_SMOKE_DECK_ID", "").strip()
if not DECK_ID.isdigit():
    sys.exit("set LIVE_SMOKE_DECK_ID to the numeric id of a public Archidekt deck")
DECK_URL = f"https://archidekt.com/decks/{DECK_ID}"
COMMANDER = "Aesi, Tyrant of Gyre Strait"
SHORT_LIST = "1 Aesi, Tyrant of Gyre Strait\n1 Sol Ring\n1 Command Tower\n1 Rampant Growth\n1 Kodama's Reach"

# Text Mystic Forge returns instead of an MCP error when an upstream call fails.
FAILURE = re.compile(
    r"^(error|unexpected error|no results found|not found|invalid|request to .* timed out|"
    r"rate limited|.*api error \(|comprehensive rules are unavailable|the research service is unavailable)",
    re.IGNORECASE,
)


def calls(deck_owner: str | None) -> list[tuple[str, dict[str, Any], str]]:
    """(tool, params, case-insensitive regex the answer must contain); {name} is filled in later."""
    return [
        ("scryfall_search", {"query": "t:kraken cmc>=8", "page": 1}, r"kraken"),
        ("scryfall_named", {"name": COMMANDER}, r"Aesi"),
        ("scryfall_random", {"query": "t:merfolk"}, r"merfolk"),
        ("scryfall_price", {"name": "Sol Ring", "limit": 3}, r"\$|€|tix"),
        ("scryfall_price_list", {"decklist": SHORT_LIST}, r"Sol Ring"),
        ("scryfall_card_text", {"cards": "Aesi, Tyrant of Gyre Strait\nSol Ring"}, r"landfall|draw"),
        ("scryfall_rulings", {"name": "Hullbreaker Horror"}, r"\d{4}-\d{2}-\d{2}|ruling"),
        ("edhrec_commander", {"name": COMMANDER, "limit": 5}, r"aesi"),
        ("edhrec_average_deck", {"name": COMMANDER, "limit": 5}, r"\d+ .+"),
        ("edhrec_combos", {"name": COMMANDER, "limit": 5}, r"combo|\+"),
        ("edhrec_top_cards", {"period": "week", "limit": 5}, r"\w"),
        ("edhrec_recommendations", {"commanders": [COMMANDER], "cards": ["Sol Ring"], "limit": 5}, r"\w"),
        ("edhrec_salt", {"limit": 5}, r"salt|\d"),
        ("edhrec_precon_upgrade", {"precon": "Reap the Tides", "limit": 5}, r"reap|tides|add|cut"),
        ("archidekt_deck", {"deck": DECK_ID, "include_text": False}, r"\d+ \w"),
        (
            "archidekt_user_decks",
            {"username": deck_owner or "missing-owner", "limit": 5},
            r"deck",
        ),
        ("archidekt_export", {"deck": DECK_URL}, r"\d+ \w"),
        (
            "format_archidekt",
            {"cards": [{"name": COMMANDER, "commander": True}, {"name": "Sol Ring", "category": "Ramp"}]},
            r"Sol Ring",
        ),
        ("validate_decklist", {"decklist": SHORT_LIST, "commander": COMMANDER}, r"deck size"),
        ("validate_archidekt_deck", {"deck": DECK_ID}, r"legal|valid|card|deck"),
        ("spellbook_combos", {"cards": [COMMANDER, "Retreat to Coralhelm"], "limit": 5}, r"\w"),
        ("spellbook_card_combos", {"card": "Retreat to Coralhelm", "limit": 5}, r"combo|retreat"),
        ("rules_get", {"ref": "704.5a"}, r"0 or less life"),
        ("rules_search", {"query": "deathtouch", "limit": 3}, r"deathtouch"),
        ("precon_search", {"query": "Reap the Tides", "limit": 5}, r"reap"),
        ("precon_decklist", {"file_name": "{precon_file}"}, r"aesi"),
        ("precon_export", {"file_name": "{precon_file}"}, r"aesi"),
        ("precon_diff", {"file_name": "{precon_file}", "deck": DECK_ID}, r"cut|add|same|identical|\+|-"),
        ("goldfish_odds", {"deck_size": 99, "draws": 7, "copies": 36, "min_successes": 3}, r"50\.11%"),
        ("goldfish_annotate", {"deck": DECK_ID}, r"land|annotat|turn"),
        ("goldfish_run", {"deck": DECK_ID, "n": 300, "seed": 7, "until_turn": 8}, r"turn|land|%"),
        (
            "goldfish_ab",
            {"deck_a": DECK_ID, "deck_b": DECK_ID, "n": 200, "seed": 7, "until_turn": 8},
            r"turn|%|diff|delta",
        ),
    ]


def deck_owner() -> str | None:
    """Public deck owner from Archidekt's read-only API, for archidekt_user_decks."""
    try:
        r = httpx.get(f"https://archidekt.com/api/decks/{DECK_ID}/", timeout=30, follow_redirects=True)
        r.raise_for_status()
        return (r.json().get("owner") or {}).get("username")
    except Exception as exc:  # reported in the results, not fatal
        print(f"could not read the deck owner: {exc}", file=sys.stderr)
        return None


def text_of(result: Any) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content)


async def run(url: str, token: str) -> list[dict[str, Any]]:
    owner = deck_owner()
    results: list[dict[str, Any]] = []
    placeholders: dict[str, str] = {}
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(300.0)) as hc:
        async with streamable_http_client(url, http_client=hc) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = {t.name for t in (await session.list_tools()).tools}
                who = await session.call_tool("whoami", {})
                print("whoami:", text_of(who)[:200])
                for tool, params, expect in calls(owner):
                    raw = json.dumps(params)
                    raw = re.sub(r"\{(\w+)\}", lambda m: placeholders.get(m.group(1), m.group(0)), raw)
                    filled = json.loads(raw)
                    row: dict[str, Any] = {"tool": tool, "params": filled, "listed": tool in listed}
                    for attempt in (1, 2):
                        started = time.monotonic()
                        try:
                            res = await session.call_tool(tool, {"params": filled})
                            body = text_of(res)
                            row["is_error"] = bool(res.is_error)
                        except Exception as exc:
                            body = f"client exception: {type(exc).__name__}: {exc}"
                            row["is_error"] = True
                        row["seconds"] = round(time.monotonic() - started, 2)
                        if attempt == 1 and re.search(r"rate limited|timed out", body[:200], re.IGNORECASE):
                            await asyncio.sleep(10)
                            continue
                        break
                    row["chars"] = len(body)
                    row["excerpt"] = body[:600]
                    problems = []
                    if not row["listed"]:
                        problems.append("not in the gateway's tools/list")
                    if row["is_error"]:
                        problems.append("MCP error result")
                    if FAILURE.match(body.strip()):
                        problems.append("upstream failure text")
                    if not re.search(expect, body, re.IGNORECASE):
                        problems.append(f"answer lacks {expect!r}")
                    if tool == "archidekt_user_decks" and not owner:
                        problems.append("deck owner unknown")
                    row["status"] = "fail" if problems else "pass"
                    row["problems"] = problems
                    results.append(row)
                    print(f"{row['status'].upper():4} {tool:24} {row['seconds']:>7}s {'; '.join(problems)}")
                    print("     " + body[: 300 if problems else 120].replace("\n", " | "))
                    if tool == "precon_search":
                        m = re.search(r"\b([A-Za-z0-9]+_[A-Z0-9]{3,5})\b", body)
                        if m:
                            placeholders["precon_file"] = m.group(1)
                    await asyncio.sleep(1)  # be polite to the public APIs
    missing = sorted(ALLOWED_TOOLS - {r["tool"] for r in results})
    for tool in missing:
        results.append({"tool": tool, "status": "fail", "problems": ["no call defined in the smoke script"]})
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    token = Path(args.token_file).read_text().strip()
    results = asyncio.run(run(args.url, token))
    passed = sum(r["status"] == "pass" for r in results)
    summary = {"deck": DECK_URL, "passed": passed, "total": len(results), "results": results}
    Path(args.out).write_text(json.dumps(summary, indent=2))
    print(f"{passed}/{len(results)} tools passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
