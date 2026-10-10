"""Keep the newest Commander precons whose every card Forge can play (image build only).

A precon from a set newer than the pinned Forge would make every run that seats it fail, so it is
left out here and the next older one takes its place. Fails when fewer than MIN remain.
    python3 filter_precons.py precons.json out.json
"""

import json
import sys

import forge_service

KEEP, MIN = 12, 3

precons = json.loads(open(sys.argv[1], encoding="utf-8").read())
kept = []
for p in precons:
    names = list(p["commander"]) + [name for _, name in p["main"]]
    missing = sorted({n for n in names if forge_service.resolve(n) is None})
    if missing:
        print(f"left out {p['name']}: Forge lacks {'; '.join(missing)}")
    elif len(kept) < KEEP:
        kept.append(p)
print(f"{len(kept)} precons kept: " + "; ".join(p["name"] for p in kept))
if len(kept) < MIN:
    sys.exit(f"only {len(kept)} precons have every card in Forge; at least {MIN} are needed")
open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(kept))
