"""Write every card name in Forge's card scripts, one per line (build time only).

Every face counts: front, back, meld result, adventure and split halves each carry a Name: line.
"""

import json
import re
import sys
import zipfile
from pathlib import Path

forge, out = Path(sys.argv[1]), Path(sys.argv[2])
scryfall = Path(sys.argv[3]) if len(sys.argv) > 3 else None
folder = forge / "res" / "cardsfolder"
names: set[str] = set()


def scan(text: str) -> None:
    for m in re.finditer(r"^Name:(.+)$", text, re.MULTILINE):
        names.add(m.group(1).strip())


zpath = folder / "cardsfolder.zip"
if zpath.exists():
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            if info.filename.endswith(".txt"):
                scan(z.read(info).decode("utf-8", "replace"))
for p in folder.rglob("*.txt"):
    scan(p.read_text("utf-8", "replace"))
if len(names) < 20000:
    sys.exit(f"only {len(names)} card names found in {folder}; refusing to build")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(sorted(names)) + "\n", "utf-8")
print(f"{len(names)} card names -> {out}")

# Scryfall names Forge knows only under another printed name (Universes Within, face names):
# {Scryfall name: Forge name}. Without the Scryfall list, no aliases (the service still runs).
aliases: dict[str, str] = {}
if scryfall and scryfall.exists():
    import forge_service  # same normalisation the service matches with

    known = {forge_service.norm(n): n for n in names}
    for name, others in json.loads(scryfall.read_text("utf-8")).items():
        if forge_service.norm(name) in known:
            continue
        for other in [name.split(" // ")[0], *others]:
            hit = known.get(forge_service.norm(other))
            if hit:
                aliases[name] = hit
                break
(out.parent / "aliases.json").write_text(json.dumps(aliases, ensure_ascii=False, sort_keys=True), "utf-8")
print(f"{len(aliases)} aliases -> {out.parent / 'aliases.json'}")
