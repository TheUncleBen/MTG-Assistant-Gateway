"""Every other name each Scryfall card is printed under, for matching Forge's card list (CI only).

Writes {Scryfall name: [printed names, flavour names (Universes Within), face names]} for cards whose
printings carry more than one name. build_names.py keeps the entries Forge knows by another name.
    python3 docker/forge/scryfall_names.py docker/forge/scryfall-names.json
"""

import gzip
import json
import sys
import urllib.request

HEADERS = {"User-Agent": "MTG-Assistant-Gateway-CI/1.0", "Accept": "application/json"}


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=300) as r:
        data = r.read()
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


bulk = json.loads(get("https://api.scryfall.com/bulk-data/default-cards"))
names: dict[str, set[str]] = {}
for line in get(bulk["jsonl_download_uri"]).decode("utf-8").splitlines():
    if not line.strip():
        continue
    card = json.loads(line)
    bag = names.setdefault(card["name"], set())
    for part in [card, *(card.get("card_faces") or [])]:
        for key in ("name", "printed_name", "flavor_name"):
            if part.get(key):
                bag.add(part[key])
out = {name: sorted(bag - {name}) for name, bag in names.items() if bag - {name}}
if len(names) < 30000:
    sys.exit(f"only {len(names)} Scryfall names; refusing to write")
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, sort_keys=True)
print(f"{len(names)} Scryfall names, {len(out)} with other printed names -> {sys.argv[1]}")
