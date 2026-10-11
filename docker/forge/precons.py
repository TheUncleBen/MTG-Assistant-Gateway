"""The newest Commander precons from MTGJSON, as default opponents for simulations (CI only).

Writes [{"name", "release_date", "commander": [...], "main": [[count, name], ...], "game_changers":
[...], "bracket"}] for the newest complete 100-card Commander decks. The image is rebuilt with each
Forge release, so this list follows new precons with no upkeep. Each precon's bracket is estimated
the way the gateway estimates a deck's (deck_stats.bracket_estimate) from the one flag a name list
can carry: the game changers it holds, by Scryfall's list (one search, is:gamechanger). No game
changers is bracket 2, one to three is 3, more is 4. The gateway seats the precons of the tested
deck's bracket (forge.pick_opponents).
    python3 docker/forge/precons.py docker/forge/precons.json
"""

import gzip
import json
import sys
import time
import urllib.parse
import urllib.request

HEADERS = {"User-Agent": "MTG-Assistant-Gateway-CI/1.0", "Accept": "application/json"}
# Enough precons that every bracket the newest sets print has a few to choose from
# (filter_precons.py keeps the ones Forge can play).
COUNT = 40


def get(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as r:
        data = r.read()
    return json.loads(gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data)


def front(name: str) -> str:
    return name.split(" // ", 1)[0].strip().casefold()


def game_changers() -> set[str]:
    """Scryfall's game changers, front faces folded (a list of about fifty: one or two pages)."""
    out: set[str] = set()
    url = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode(
        {"q": "is:gamechanger", "unique": "cards"}
    )
    while url:
        page = get(url)
        out.update(front(c["name"]) for c in page.get("data", []))
        url = page.get("next_page") if page.get("has_more") else None
        time.sleep(0.1)  # Scryfall asks for 50-100 ms between requests
    return out


def bracket_of(names: list[str], changers: set[str]) -> tuple[list[str], int]:
    """(the precon's game changers, its estimated bracket)."""
    held = sorted({n for n in names if front(n) in changers})
    return held, 2 if not held else 3 if len(held) <= 3 else 4


def main(path: str) -> None:
    listing = [
        d
        for d in get("https://mtgjson.com/api/v5/DeckList.json")["data"]
        if d.get("type") == "Commander Deck" and d.get("releaseDate")
    ]
    listing.sort(key=lambda d: d["releaseDate"], reverse=True)
    changers = game_changers()
    if len(changers) < 10:
        sys.exit(f"only {len(changers)} game changers from Scryfall; refusing to write")
    out = []
    for meta in listing:
        if len(out) >= COUNT:
            break
        deck = get(f"https://mtgjson.com/api/v5/decks/{meta['fileName']}.json")["data"]
        commanders = deck.get("commander") or []
        main = deck.get("mainBoard") or []
        if not 1 <= len(commanders) <= 2 or sum(c.get("count", 1) for c in commanders + main) != 100:
            continue
        names = [c["name"] for c in commanders + main]
        held, bracket = bracket_of(names, changers)
        out.append(
            {
                "name": meta["name"],
                "release_date": meta["releaseDate"],
                "commander": [c["name"] for c in commanders],
                "main": [[c.get("count", 1), c["name"]] for c in main],
                "game_changers": held,
                "bracket": bracket,
            }
        )
    if len(out) < 3:
        sys.exit(f"only {len(out)} complete Commander precons found; refusing to write")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"{len(out)} precons -> {path}: " + "; ".join(f"{d['name']} (bracket {d['bracket']})" for d in out))


if __name__ == "__main__":
    main(sys.argv[1])
