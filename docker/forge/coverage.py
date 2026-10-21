"""Coverage gate: the share of a pool of cards that Forge can play (CI only).

Runs inside the built image, matching names exactly as the service does (forge_service.resolve).
Fails below the floor; prints every card Forge lacks so the list ships with the release. The image
workflow runs it twice: cards legal in at least one format, and cards legal in Commander.
    python3 coverage.py pool.json [floor_percent [pool words]]
"""

import json
import sys

import forge_service

pool = json.loads(open(sys.argv[1], encoding="utf-8").read())
floor = float(sys.argv[2]) if len(sys.argv) > 2 else 99.5
words = sys.argv[3] if len(sys.argv) > 3 else "legal cards"
missing = sorted(n for n in pool if forge_service.resolve(n) is None)
share = 100 * (len(pool) - len(missing)) / len(pool)
print(f"Forge {forge_service.VERSION}: {len(pool) - len(missing)} of {len(pool)} {words} ({share:.2f}%)")
print("MISSING: " + "; ".join(missing))
if share < floor:
    sys.exit(f"coverage {share:.2f}% is below the {floor}% floor")
