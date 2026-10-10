"""Coverage gate: the share of cards legal in at least one format that Forge can play (CI only).

Runs inside the built image, matching names exactly as the service does (forge_service.resolve).
Fails below the floor; prints every card Forge lacks so the list ships with the release.
    python3 coverage.py scryfall-legal.json [floor_percent]
"""

import json
import sys

import forge_service

pool = json.loads(open(sys.argv[1], encoding="utf-8").read())
floor = float(sys.argv[2]) if len(sys.argv) > 2 else 99.5
missing = sorted(n for n in pool if forge_service.resolve(n) is None)
share = 100 * (len(pool) - len(missing)) / len(pool)
print(f"Forge {forge_service.VERSION}: {len(pool) - len(missing)} of {len(pool)} legal cards ({share:.2f}%)")
print("MISSING: " + "; ".join(missing))
if share < floor:
    sys.exit(f"coverage {share:.2f}% is below the {floor}% floor")
