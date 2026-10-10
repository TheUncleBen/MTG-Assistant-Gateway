"""The newest Commander precons from MTGJSON, as default opponents for simulations (CI only).

Writes [{"name", "release_date", "commander": [...], "main": [[count, name], ...]}] for the newest
complete 100-card Commander decks. The image is rebuilt with each Forge release, so this list
follows new precons with no upkeep.
    python3 docker/forge/precons.py docker/forge/precons.json
"""

import gzip
import json
import sys
import urllib.request

HEADERS = {"User-Agent": "MTG-Assistant-Gateway-CI/1.0", "Accept": "application/json"}
COUNT = 12


def get(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as r:
        data = r.read()
    return json.loads(gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data)


listing = [
    d
    for d in get("https://mtgjson.com/api/v5/DeckList.json")["data"]
    if d.get("type") == "Commander Deck" and d.get("releaseDate")
]
listing.sort(key=lambda d: d["releaseDate"], reverse=True)
out = []
for meta in listing:
    if len(out) >= COUNT:
        break
    deck = get(f"https://mtgjson.com/api/v5/decks/{meta['fileName']}.json")["data"]
    commanders = deck.get("commander") or []
    main = deck.get("mainBoard") or []
    if not 1 <= len(commanders) <= 2 or sum(c.get("count", 1) for c in commanders + main) != 100:
        continue
    out.append(
        {
            "name": meta["name"],
            "release_date": meta["releaseDate"],
            "commander": [c["name"] for c in commanders],
            "main": [[c.get("count", 1), c["name"]] for c in main],
        }
    )
if len(out) < 3:
    sys.exit(f"only {len(out)} complete Commander precons found; refusing to write")
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print(f"{len(out)} precons -> {sys.argv[1]}: " + "; ".join(d["name"] for d in out))
