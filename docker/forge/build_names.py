"""Write every card name in Forge's card scripts, one per line (build time only).

Every face counts: front, back, meld result, adventure and split halves each carry a Name: line.
"""

import re
import sys
import zipfile
from pathlib import Path

forge, out = Path(sys.argv[1]), Path(sys.argv[2])
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
