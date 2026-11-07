#!/usr/bin/env python3
"""Record the pinned Mystic Forge's tool listing as a test fixture.

The gateway lists 25 of Mystic Forge's tools as its own (mf_proxy.ALLOWED_TOOLS), so the size of
the gateway's tools/list depends on their descriptions and schemas. The tests have only a small
fake Mystic Forge, so they measure the listing with this recording of the real one:
tests/fixtures/mystic_forge/tools.json.

Run it against a checkout of the pinned upstream commit with the local patches applied, in an
environment with Mystic Forge's own requirements installed (docker/mystic-forge/Dockerfile names
the commit, the version and the patch set):

    git -C mf init -q && git -C mf fetch -q --depth 1 <MF_REPO> <MF_COMMIT>
    git -C mf checkout -q FETCH_HEAD
    for p in docker/mystic-forge/patches/*.patch; do git -C mf apply "$PWD/$p"; done
    <mf-venv>/bin/python scripts/record_mystic_forge_tools.py mf

Re-record whenever the pin or the patch set changes; a test fails until the recording matches the
pin in the Dockerfile.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "mystic_forge" / "tools.json"


def pinned_tag() -> str:
    """The image tag the Dockerfile builds: <MF_VERSION>-<MF_LOCAL>."""
    text = (ROOT / "docker" / "mystic-forge" / "Dockerfile").read_text(encoding="utf-8")
    version = re.search(r"^ARG MF_VERSION=(\S+)", text, re.M)
    local = re.search(r"^ARG MF_LOCAL=(\S+)", text, re.M)
    if not version or not local:
        raise SystemExit("MF_VERSION or MF_LOCAL not found in docker/mystic-forge/Dockerfile")
    return f"{version.group(1)}-{local.group(1)}"


async def listing(checkout: Path) -> list[dict[str, object]]:
    sys.path.insert(0, str(checkout))
    os.chdir(checkout)
    os.environ.setdefault("MYSTIC_FORGE_NO_INGEST", "1")
    os.environ.setdefault("MYSTIC_FORGE_NTFY_OFF", "1")
    os.environ.setdefault("MYSTIC_FORGE_DB", str(Path(tempfile.mkdtemp()) / "mystic_forge.db"))
    import server  # Mystic Forge's server.py
    from mcp import Client

    async with Client(server.mcp) as client:
        tools = (await client.list_tools()).tools
    return [t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in tools]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    checkout = Path(sys.argv[1]).resolve()
    tools = asyncio.run(listing(checkout))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = {"image_tag": pinned_tag(), "tools": sorted(tools, key=lambda t: str(t["name"]))}
    OUT.write_text(json.dumps(body, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(tools)} tools written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
