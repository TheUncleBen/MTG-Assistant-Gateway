#!/usr/bin/env python3
"""Download the self-hosted OCR assets for the /scan page.

Fetches pinned releases of Tesseract.js (MIT/Apache-2.0), its WebAssembly core
and the fast English language model (Apache-2.0) from their canonical sources,
verifies each download against a SHA-256 recorded here, and unpacks the files
the page needs into src/mtg_gateway/scan/static/vendor/. Pre-compressed .gz
copies are written next to the large files so the gateway can serve them with
Content-Encoding: gzip.

The vendor directory is not committed; the Dockerfile runs this script at image
build time and CI runs it before the browser tests. Run it locally with:

    python3 scripts/fetch_ocr_assets.py

Only the Python standard library is used.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "src" / "mtg_gateway" / "scan" / "static" / "vendor"

TESSERACT_JS = "7.0.0"
TESSERACT_CORE = "7.0.0"
TESSDATA_FAST_COMMIT = (
    "87416418657359cb625c412a48b6e1d6d41c29bd"  # tesseract-ocr/tessdata_fast main, 2026-10-04
)
ASSETS = [
    {
        "url": f"https://registry.npmjs.org/tesseract.js/-/tesseract.js-{TESSERACT_JS}.tgz",
        "sha256": "9a93bf51c3387f945d10a24bf8b3a4bf2e45c7c7161b8242aafbaf9d3c4b606a",
        "extract": {
            "package/dist/tesseract.min.js": "tesseract.min.js",
            "package/dist/worker.min.js": "worker.min.js",
            "package/dist/tesseract.min.js.LICENSE.txt": "tesseract.min.js.LICENSE.txt",
            "package/dist/worker.min.js.LICENSE.txt": "worker.min.js.LICENSE.txt",
            "package/LICENSE.md": "LICENSE.tesseract.js.md",
        },
    },
    {
        "url": f"https://registry.npmjs.org/tesseract.js-core/-/tesseract.js-core-{TESSERACT_CORE}.tgz",
        "sha256": "ba584355515eaff877552022853c0e71f2cb70466e759d1d6940484929718ee0",
        "extract": {
            # LSTM-only cores: the page always asks for OEM 1 (LSTM), so the legacy engine is not needed.
            "package/tesseract-core-lstm.wasm.js": "core/tesseract-core-lstm.wasm.js",
            "package/tesseract-core-simd-lstm.wasm.js": "core/tesseract-core-simd-lstm.wasm.js",
            "package/tesseract-core-relaxedsimd-lstm.wasm.js": "core/tesseract-core-relaxedsimd-lstm.wasm.js",
            "package/LICENSE": "LICENSE.tesseract.js-core",
        },
    },
    {
        "url": f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{TESSDATA_FAST_COMMIT}/eng.traineddata",
        "sha256": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
        "save_as": "lang/eng.traineddata",
    },
]
GZIP_MIN_BYTES = 64 * 1024


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mtg-assistant-gateway-asset-fetch/1"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - pinned https URLs
        return resp.read()


def verify(data: bytes, expected: str, url: str) -> None:
    got = hashlib.sha256(data).hexdigest()
    if got != expected:
        sys.exit(f"checksum mismatch for {url}\n  expected {expected}\n  got      {got}")


def write(rel: str, data: bytes) -> None:
    dest = VENDOR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    if len(data) >= GZIP_MIN_BYTES:
        with gzip.open(str(dest) + ".gz", "wb", compresslevel=9) as gz:
            gz.write(data)
    print(f"  {rel}  {len(data) / 1e6:.1f} MB")


def main() -> int:
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    VENDOR.mkdir(parents=True)
    for asset in ASSETS:
        print(asset["url"])
        data = fetch(asset["url"])
        verify(data, asset["sha256"], asset["url"])
        if "save_as" in asset:
            write(asset["save_as"], data)
            continue
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = {m.name: m for m in tar.getmembers()}
            for src, rel in asset["extract"].items():
                member = members.get(src)
                if member is None:
                    sys.exit(f"{src} missing from {asset['url']}")
                fh = tar.extractfile(member)
                if fh is None:
                    sys.exit(f"{src} is not a file")
                write(rel, fh.read())
    (VENDOR / "VERSIONS.txt").write_text(
        f"tesseract.js {TESSERACT_JS}\ntesseract.js-core {TESSERACT_CORE}\n"
        f"tessdata_fast {TESSDATA_FAST_COMMIT} eng.traineddata sha256 " + ASSETS[2]["sha256"] + "\n",
        encoding="utf-8",
    )
    print("done:", VENDOR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
