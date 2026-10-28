"""Measure the card scanner's two signals on real phone photos: text recognition (the card's title
and its collector line, the main signal today) and artwork matching. Numbers only; it changes
nothing on any gateway.

    python3 scripts/measure_scanner.py PHOTOS --template     # writes PHOTOS/labels.csv to fill in
    python3 scripts/measure_scanner.py PHOTOS                # reads it, writes PHOTOS/scanner-report.md

PHOTOS is a folder of pictures, one card each, as a phone takes them (JPEG or PNG; HEIC is counted
as the failure it is in a browser). labels.csv says what each card really is: ``file``, ``name``
and, where known, ``set`` and ``number`` (the collector line, for the printing), ``foil`` (yes or
no) and ``tags`` (words such as ``foil; sleeved; non-English``, separated by ``;``, for the
breakdown). How to take the photos: docs/SCANNING.md, "Measuring the scanner".

Each photo goes through the /scan page's own code in headless Chromium, as its Photo button does:
the edge finding and straightening (geometry.js), the real text recognition (Tesseract.js, so run
``scripts/fetch_ocr_assets.py`` first) and the artwork fingerprint (scan-art.js). What it reads is
resolved by the gateway's own resolver twice: without the artwork (the text signal on its own) and
with it (what the page shows today). The fingerprint is also compared with every printing of every
labelled card (plus any ``--pool-extra`` names), which is how artwork would do as the main signal:
a stand-in for the whole card pool, so its figures are an upper bound (see the report's note).

It reads Scryfall (api.scryfall.com, spaced as the gateway spaces it) and Scryfall's card pictures
(cards.scryfall.io) and nothing else, never Archidekt. What it learns is kept in ``--cache`` (a
SQLite file next to the photos), so a second run costs little. Needs the dev extras
(``pip install -e ".[dev]"``) and Playwright's Chromium (``playwright install chromium``).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # runs from a checkout; the gateway's own code is measured

import uvicorn  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

from mtg_gateway import __version__  # noqa: E402
from mtg_gateway.db import Database  # noqa: E402
from mtg_gateway.scan.art import IMAGE_HOST, ArtIndex, ArtSettings, decode_hash, row_distance  # noqa: E402
from mtg_gateway.scan.routes import SCAN_ICONS, STATIC_DIR, ocr_assets_present  # noqa: E402
from mtg_gateway.scan.scryfall import ScryfallClient  # noqa: E402
from mtg_gateway.scan.service import CardInput, ScanError, ScanService, parse_cards  # noqa: E402
from mtg_gateway.theme import ICONS  # noqa: E402

PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
COLUMNS = ["file", "name", "set", "number", "foil", "tags"]
USER_AGENT = "MTG-Assistant-Gateway-measure/1.0"
YES, NO = {"yes", "y", "true", "1", "foil"}, {"no", "n", "false", "0", "nonfoil", "non-foil"}


# -- labels ---------------------------------------------------------------------------------
@dataclass
class Label:
    """What the card in one photo really is."""

    file: str
    name: str
    set: str = ""  # lower case, as Scryfall keys it
    number: str = ""  # leading zeros dropped, as Scryfall keys it
    foil: bool | None = None
    tags: list[str] = field(default_factory=list)


def _number(text: str) -> str:
    text = text.strip()
    return text.lstrip("0") or ("0" if text else "")


def photos_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in PHOTO_TYPES)


def write_template(photos: Path, path: Path | None = None) -> Path:
    """labels.csv with one row per photo and the name to fill in. Never writes over one."""
    out = path or photos / "labels.csv"
    if out.exists():
        raise FileExistsError(f"{out} is already there; fill that one in, or move it away first")
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for p in photos_in(photos):
            w.writerow([p.name, "", "", "", "", ""])
    return out


def read_labels(photos: Path, path: Path) -> tuple[list[Label], list[str]]:
    """The filled-in rows, and a line for each row left out (no name, or no such photo)."""
    labels: list[Label] = []
    skipped: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            file = (row.get("file") or "").strip()
            name = (row.get("name") or "").strip()
            if not file:
                continue
            if not name:
                skipped.append(f"{file}: no card name")
                continue
            if not (photos / file).is_file():
                skipped.append(f"{file}: no such photo")
                continue
            foil_text = (row.get("foil") or "").strip().lower()
            foil = True if foil_text in YES else False if foil_text in NO else None
            tags = [t.strip() for t in (row.get("tags") or "").replace(",", ";").split(";") if t.strip()]
            labels.append(
                Label(
                    file,
                    name,
                    (row.get("set") or "").strip().lower(),
                    _number(row.get("number") or ""),
                    foil,
                    tags,
                )
            )
    return labels, skipped


# -- the gateway's code, served to the browser --------------------------------------------------
class Bench:
    """The gateway's resolver and artwork index on a cache database of their own."""

    def __init__(
        self,
        db_path: Path,
        scryfall: ScryfallClient,
        *,
        image_http: Any = None,
        image_host: str = IMAGE_HOST,
        image_interval: float = 0.1,
    ):
        self.db = Database(db_path)
        self.scryfall = scryfall
        self.art = ArtIndex(
            self.db,
            scryfall,
            ArtSettings(image_interval=image_interval),
            image_http=image_http,
            image_host=image_host,
        )
        self.service = ScanService(self.db, scryfall, art_matcher=self.art.match)
        self.last: dict[str, Any] | None = None

    async def aclose(self) -> None:
        await self.art.aclose()
        await self.scryfall.aclose()


def _page_html(bench: Bench) -> str:
    config = {
        "csrf": "measure",
        "user": "measure",
        "draft": "measure",
        "ocr": ocr_assets_present(),
        "version": "measure",
        "static": "/scan/static",
        "thresholds": bench.service.t.as_dict(),
        "icons": {k: ICONS[k] for k in SCAN_ICONS},
    }
    data = json.dumps(config).replace("<", "\\u003c")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Scanner measurement</title></head>"
        f"<body><script id='scan-config' type='application/json'>{data}</script>"
        "<div id='scan-app' class='scan'></div>"
        "<script src='/scan/static/geometry.js'></script><script src='/scan/static/scan-art.js'></script>"
        "<script src='/scan/static/scan.js'></script></body></html>"
    )


def _app(bench: Bench, photos: list[Path]) -> Starlette:
    page = _page_html(bench)
    static_root = STATIC_DIR.resolve()

    async def index(_request: Request) -> Response:
        return HTMLResponse(page)

    async def static(request: Request) -> Response:
        target = (STATIC_DIR / request.path_params["path"]).resolve()
        if not target.is_relative_to(static_root) or not target.is_file():
            return Response("not found", 404)
        return FileResponse(target)

    async def photo(request: Request) -> Response:
        i = int(request.path_params["i"])
        return FileResponse(photos[i]) if 0 <= i < len(photos) else Response("not found", 404)

    async def resolve(request: Request) -> Response:
        data = await request.json()
        try:
            cards = parse_cards(data.get("cards"))
            shown = await bench.service.resolve(cards)
            text = await bench.service.resolve([replace(c, art_hash="") for c in cards])
        except ScanError as exc:
            return JSONResponse({"ok": False, "error": exc.kind, "message": str(exc)}, 503)
        out = bench.service.describe(shown)
        bench.last = {
            "request": data["cards"][0],
            "page": out["cards"][0],
            "text": bench.service.describe(text)["cards"][0],
        }
        return JSONResponse({"ok": True, **out})

    async def search(request: Request) -> Response:
        try:
            names = await bench.service.suggest(request.query_params.get("q", ""))
        except ScanError as exc:
            return JSONResponse({"ok": False, "error": exc.kind, "message": str(exc)}, 503)
        return JSONResponse({"ok": True, "names": names})

    async def last(_request: Request) -> Response:
        return JSONResponse(bench.last or {})

    async def reset(_request: Request) -> Response:
        bench.last = None
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/", index),
            Route("/scan/static/{path:path}", static),
            Route("/photo/{i:int}", photo),
            Route("/scan/api/resolve", resolve, methods=["POST"]),
            Route("/scan/api/search", search),
            Route("/harness/last", last),
            Route("/harness/reset", reset, methods=["POST"]),
        ]
    )


class _Loop:
    """An event loop on a thread of its own: the server and the resolver run there, Playwright here."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coro: Any, timeout: float | None = None) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


# One photo through the page, as its Photo button reads it. The outline is looked for separately
# (the same call the Photo button makes) so a failed read still says whether the edges were found.
MEASURE_JS = """async (i) => {
  const s = window.__scan;
  s.state.pending = null; s.state.continuous = false;
  const blob = await (await fetch('/photo/' + i)).blob();
  let edges = false, sideways = false;
  try {
    const bmp = await createImageBitmap(blob);
    const k = Math.min(1, 2400 / Math.max(bmp.width, bmp.height));
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(bmp.width * k)); c.height = Math.max(1, Math.round(bmp.height * k));
    c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
    const flat = s.flattenCard(c, null);
    edges = !!flat; sideways = !!(flat && flat.sideways);
  } catch (e) { /* a picture the browser cannot open: scanFile says so below */ }
  await fetch('/harness/reset', {method: 'POST'});
  await s.scanFile(new File([blob], 'photo', {type: blob.type || 'image/jpeg'}));
  const status = (document.querySelector('#cam-status') || {}).textContent || '';
  const res = s.state.pending;
  const last = await (await fetch('/harness/last')).json();
  let tierText = null, tierPage = null;
  if (res) {
    tierPage = await s.tierOf(res);
    if (last.text) tierText = await s.tierOf(Object.assign({}, last.text, {ocr: res.ocr}));
  }
  const o = res && res.ocr;
  if (o) { edges = !!o.flattened; sideways = sideways && edges; } // what the read itself went by
  return {edges, sideways, status, tierText, tierPage, request: last.request || null,
          page: last.page || null, text: last.text || null,
          ocr: o ? {text: o.text, confidence: o.confidence, glare: o.glare, flattened: o.flattened,
                    info: o.info && {set: o.info.set, number: o.info.number, foil: o.info.foil}} : null};
}"""
NO_CAMERA = (
    "if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = "
    "() => Promise.reject(new DOMException('no camera here', 'NotFoundError'));"
)


# -- scoring --------------------------------------------------------------------------------------
@dataclass
class _PoolCard:
    oracle_id: str
    name: str
    rows: list[Any]  # art._Printing: id, illustration_id, hashes, card


def _where(card: dict[str, Any] | None) -> str:
    if not card or not card.get("set"):
        return ""
    return f"{str(card['set']).upper()} {card.get('collector_number') or ''}".strip()


def _same_card(card: dict[str, Any] | None, label_card: dict[str, Any] | None) -> bool:
    if not card or not label_card:
        return False
    if card.get("oracle_id") and label_card.get("oracle_id"):
        return card["oracle_id"] == label_card["oracle_id"]
    front = lambda n: str(n or "").split(" // ")[0].strip().casefold()  # noqa: E731
    return front(card.get("name")) == front(label_card.get("name"))


def _same_printing(card: dict[str, Any] | None, label: Label) -> bool | None:
    if not (label.set and label.number):
        return None
    if not card:
        return False
    return str(card.get("set") or "").lower() == label.set and _number(
        str(card.get("collector_number") or "")
    ) == (label.number)


def _art_main(probe: bytes, pool: dict[str, _PoolCard], s: ArtSettings) -> dict[str, Any] | None:
    """The fingerprint against every printing of every pool card: the closest card, how far, and
    its lead over the next card (the confidence the gateway's matcher uses between artworks)."""
    best: list[tuple[int, str, Any]] = []
    for oid, entry in pool.items():
        if entry.rows:
            d, p = min(((row_distance(probe, p.hashes), p) for p in entry.rows), key=lambda t: t[0])
            best.append((d, oid, p))
    if not best:
        return None
    best.sort(key=lambda t: t[0])
    d0, oid0, p0 = best[0]
    margin = best[1][0] - d0 if len(best) > 1 else 1024
    return {
        "oracle_id": oid0,
        "name": pool[oid0].name,
        "printing_id": p0.id,
        "illustration_id": p0.illustration_id,
        "distance": d0,
        "margin": margin,
        "confident": d0 <= s.max_distance and margin >= s.min_margin,
    }


def score(
    label: Label,
    raw: dict[str, Any],
    label_card: dict[str, Any] | None,
    pool: dict[str, _PoolCard],
    settings: ArtSettings,
) -> dict[str, Any]:
    """One photo's outcome for each signal."""
    illustrations = {p.id: p.illustration_id for e in pool.values() for p in e.rows}
    label_ill = (
        illustrations.get((label_card or {}).get("scryfall_id")) if label.set and label.number else None
    )
    ocr = raw.get("ocr") or {}
    text_res = raw.get("text") or {}
    tcard = text_res.get("card")
    card_ok = _same_card(tcard, label_card)
    printing_ok = _same_printing(tcard, label) if tcard or (label.set and label.number) else None
    foil_ok = None
    if label.foil is not None:
        foil_ok = card_ok and (text_res.get("foil") is True) == label.foil
    auto = raw.get("tierText") == "certain"
    page_res = raw.get("page") or {}
    pcard = page_res.get("card")
    art = page_res.get("art") or {}
    probe = decode_hash(str((raw.get("request") or {}).get("art_hash") or ""))
    main = _art_main(probe, pool, settings) if probe else None
    main_ok = bool(main and label_card and main["oracle_id"] == label_card.get("oracle_id"))
    picker_ok = None
    if label_ill and art.get("status") in ("matched", "confirmed"):
        picker_ok = illustrations.get((pcard or {}).get("scryfall_id")) == label_ill
    info = ocr.get("info") or {}
    return {
        "file": label.file,
        "pool": len(pool),
        "label": {"name": label.name, "set": label.set, "number": label.number, "foil": label.foil},
        "tags": label.tags,
        "error": None if label_card else "the label names no card Scryfall knows",
        "edges": bool(raw.get("edges")),
        "sideways": bool(raw.get("sideways")),
        "status": raw.get("status") or "",
        "text": {
            "read": ocr.get("text") or "",
            "confidence": ocr.get("confidence"),
            "info": f"{str(info.get('set') or '').upper()} {info.get('number') or ''}".strip(),
            "card": (tcard or {}).get("name"),
            "printing": _where(tcard),
            "status": text_res.get("status"),
            "card_ok": card_ok,
            "printing_ok": printing_ok,
            "foil_ok": foil_ok,
            "tier": raw.get("tierText"),
            "auto_add": auto,
            "wrong_auto_add": auto and (not card_ok or printing_ok is False or foil_ok is False),
        },
        "art": {
            "fingerprint": probe is not None,
            "picker": art.get("status"),
            "picker_ok": picker_ok,
            "main_card": main["name"] if main else None,
            "main_card_ok": main_ok,
            "main_printing_ok": (main["illustration_id"] == label_ill) if main and label_ill else None,
            "main_distance": main["distance"] if main else None,
            "main_margin": main["margin"] if main else None,
            "main_confident": bool(main and main["confident"]),
            "main_wrong_confident": bool(main and main["confident"] and not main_ok),
        },
        "page": {
            "card": (pcard or {}).get("name"),
            "printing": _where(pcard),
            "card_ok": _same_card(pcard, label_card),
            "printing_ok": _same_printing(pcard, label) if label.set and label.number else None,
        },
    }


def _count(rows: Iterable[dict[str, Any]], part: str, key: str, among: str | None = None) -> tuple[int, int]:
    """(how many are true, out of how many count): None means "not scored for this photo"."""
    vals = [r[part][key] for r in rows if among is None or r[part][among]]
    scored = [v for v in vals if v is not None]
    return sum(1 for v in scored if v), len(scored)


def summarize(rows: list[dict[str, Any]], pool: int, extra: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {
        "photos": len(rows),
        "pool": pool,
        "extra": extra,
        "edges": (sum(1 for r in rows if r["edges"]), len(rows)),
        "text": {
            "read": (sum(1 for r in rows if r["text"]["read"]), len(rows)),
            "card_ok": _count(rows, "text", "card_ok"),
            "printing_ok": _count(rows, "text", "printing_ok"),
            "foil_ok": _count(rows, "text", "foil_ok"),
            "auto_add": _count(rows, "text", "auto_add"),
            "wrong_auto_add": _count(rows, "text", "wrong_auto_add", among="auto_add"),
        },
        "art": {
            "fingerprint": _count(rows, "art", "fingerprint"),
            "picker_ok": _count(rows, "art", "picker_ok"),
            "main_card_ok": _count(rows, "art", "main_card_ok"),
            "main_printing_ok": _count(rows, "art", "main_printing_ok"),
            "main_confident": _count(rows, "art", "main_confident"),
            "main_wrong_confident": _count(rows, "art", "main_wrong_confident", among="main_confident"),
        },
        "page": {
            "card_ok": _count(rows, "page", "card_ok"),
            "printing_ok": _count(rows, "page", "printing_ok"),
        },
        "by_tag": {},
    }
    for tag in sorted({t for r in rows for t in r["tags"]}):
        some = [r for r in rows if tag in r["tags"]]
        out["by_tag"][tag] = {
            "photos": len(some),
            "text": _count(some, "text", "card_ok"),
            "art": _count(some, "art", "main_card_ok"),
        }
    return out


def _share(pair: tuple[int, int]) -> str:
    k, n = pair
    return f"{k} of {n} ({round(100 * k / n)}%)" if n else "none to count"


def _mark(v: bool | None) -> str:
    return "" if v is None else ("right" if v else "wrong")


def report(summary: dict[str, Any], rows: list[dict[str, Any]], skipped: Iterable[str] = ()) -> str:
    s, t, a, p = summary, summary["text"], summary["art"], summary["page"]
    settings = ArtSettings()
    lines = [
        "# Scanner measurement",
        "",
        f"{s['photos']} photos, measured on {date.today().isoformat()} with the scan page and resolver of "
        f"gateway {__version__}. The artwork was matched among {s['pool']} cards (every English paper "
        f"printing of each: the labelled cards"
        + (f" and {s['extra']} more named with --pool-extra" if s["extra"] else "")
        + ").",
        "",
        "What decides the question: compare **Right card** for the two signals, and above all the "
        "**Of those, wrong** rows, which count wrong cards that would go on the list without a tap. "
        "The tags show where each signal struggles (foils, sleeves, other languages).",
        "",
        "## Text recognition (title and collector line)",
        "",
        "The main signal today: the title read on the phone, and the collector line when it reads.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Card outline found | {_share(s['edges'])} |",
        f"| A name read | {_share(t['read'])} |",
        f"| Right card | {_share(t['card_ok'])} |",
        f"| Right printing (photos labelled with set and number) | {_share(t['printing_ok'])} |",
        f"| Right foil (photos labelled with foil) | {_share(t['foil_ok'])} |",
        f"| Certain enough for Auto to add without a tap | {_share(t['auto_add'])} |",
        f"| Of those, wrong (card, printing or foil) | {_share(t['wrong_auto_add'])} |",
        "",
        "## Artwork matching",
        "",
        "The fingerprint of the art box (it needs the card outline). Today it only picks the printing "
        "after the name is known; as the main signal it would have to name the card itself.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Fingerprint taken | {_share(a['fingerprint'])} |",
        f"| Today: right artwork when it picked or backed a printing | {_share(a['picker_ok'])} |",
        f"| As the main signal: right card | {_share(a['main_card_ok'])} |",
        f"| As the main signal: right artwork (photos labelled with set and number) "
        f"| {_share(a['main_printing_ok'])} |",
        f"| Sure enough to act on (difference at most {settings.max_distance}, lead at least "
        f"{settings.min_margin} over the next card) | {_share(a['main_confident'])} |",
        f"| Of those, wrong | {_share(a['main_wrong_confident'])} |",
        "",
        f"The artwork had {s['pool']} cards to choose from; the real pool is every card printed, so "
        "these figures are the best artwork could do. Add look-alike cards with --pool-extra to make "
        "the test harder.",
        "",
        "## What the page shows today (both signals together)",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Right card | {_share(p['card_ok'])} |",
        f"| Right printing (photos labelled with set and number) | {_share(p['printing_ok'])} |",
        "",
    ]
    if s["by_tag"]:
        lines += [
            "## By tag",
            "",
            "| Tag | Photos | Right card, text | Right card, artwork |",
            "|---|---:|---|---|",
        ]
        for tag, v in s["by_tag"].items():
            lines.append(f"| {tag} | {v['photos']} | {_share(v['text'])} | {_share(v['art'])} |")
        lines.append("")
    lines += [
        "## Each photo",
        "",
        "| Photo | Card | Outline | Read | Text says | Artwork says | Page said |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        tx, ar = r["text"], r["art"]
        read = tx["read"] + (f" ({round(tx['confidence'])}%)" if tx["confidence"] is not None else "")
        if tx["info"]:
            read += f", {tx['info']}"
        said = "no card"
        if tx["card"]:
            said = " ".join(x for x in (tx["card"], tx["printing"], _mark(tx["card_ok"])) if x)
        if tx["auto_add"]:
            said += ", added without a tap"
        art = "no fingerprint"
        if ar["fingerprint"]:
            art = f"{ar['main_card'] or 'nothing'} {_mark(ar['main_card_ok'])}"
            art += f", {ar['main_distance']}/{ar['main_margin']}" + (", sure" if ar["main_confident"] else "")
        page = r["error"] or r["status"]
        outline = "yes" if r["edges"] else "no"
        lines.append(
            f"| {r['file']} | {r['label']['name']} | {outline} | {read or '-'} | {said} "
            f"| {art} | {page.replace('|', '/')} |"
        )
    if skipped:
        lines += ["", "Left out: " + "; ".join(skipped) + "."]
    lines += [
        "",
        "Artwork figures read difference/lead: the difference (of 1024 bits) to the closest printing, "
        "and how much closer that is than the closest printing of any other card.",
        "",
    ]
    return "\n".join(lines)


# -- the run ----------------------------------------------------------------------------------------
def measure(
    photos_dir: Path,
    labels: list[Label],
    make_bench: Callable[[], Bench],
    *,
    pool_extra: Iterable[str] = (),
    chromium: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Every labelled photo through both signals; one row of outcomes per photo."""
    from playwright.sync_api import sync_playwright

    say = progress or (lambda _m: None)
    photos = [photos_dir / lb.file for lb in labels]
    loop = _Loop()

    async def start() -> Bench:
        return make_bench()

    bench: Bench = loop.run(start())
    try:
        label_cards, pool = loop.run(_prepare(bench, labels, list(pool_extra), say))
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(_app(bench, photos), log_level="warning", lifespan="off"))
        served = asyncio.run_coroutine_threadsafe(server.serve(sockets=[sock]), loop.loop)
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        rows: list[dict[str, Any]] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chromium, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 412, "height": 915})
            ctx.add_init_script(NO_CAMERA)
            page = ctx.new_page()
            page.goto(base + "/")
            page.wait_for_function("() => window.__scan && window.__scan.scanFile")
            page.wait_for_function(
                "() => ['ready', 'missing', 'failed'].includes(window.__scan.state.ocrState)", timeout=180_000
            )
            if page.evaluate("() => window.__scan.state.ocrState") != "ready":
                raise SystemExit("Text recognition did not load; run scripts/fetch_ocr_assets.py first.")
            for i, lb in enumerate(labels):
                raw = page.evaluate(MEASURE_JS, i)
                key = (lb.name.casefold(), lb.set, lb.number)
                row = score(lb, raw, label_cards.get(key), pool, bench.art.s)
                rows.append(row)
                say(
                    f"{i + 1}/{len(labels)} {lb.file}: text {_mark(row['text']['card_ok']) or '-'}, "
                    f"artwork {_mark(row['art']['main_card_ok']) if row['art']['fingerprint'] else '-'}"
                )
            browser.close()
        server.should_exit = True
        served.result(timeout=10)
        return rows
    finally:
        loop.run(bench.aclose())
        loop.close()


async def _prepare(
    bench: Bench, labels: list[Label], extra: list[str], say: Callable[[str], None]
) -> tuple[dict[tuple[str, str, str], dict[str, Any] | None], dict[str, _PoolCard]]:
    """Each label's card (its printing when set and number are given), and every printing of every
    pool card learned, so the artwork has something to be matched against."""
    cards: dict[tuple[str, str, str], dict[str, Any] | None] = {}
    for lb in labels:
        key = (lb.name.casefold(), lb.set, lb.number)
        if key in cards:
            continue
        inp = CardInput(name=lb.name, set_code=lb.set, collector_number=lb.number if lb.set else "")
        res = (await bench.service.resolve([inp]))[0]
        cards[key] = res.card if res.card and res.status in ("exact", "printing", "fuzzy") else None
        if cards[key] is None:
            say(f"{lb.file}: Scryfall knows no card “{lb.name}”")
    oracles: dict[str, str] = {c["oracle_id"]: c["name"] for c in cards.values() if c and c.get("oracle_id")}
    for name in extra:
        res = (await bench.service.resolve([CardInput(name=name)]))[0]
        if res.card and res.card.get("oracle_id"):
            oracles.setdefault(res.card["oracle_id"], res.card["name"])
        else:
            say(f"--pool-extra: Scryfall knows no card “{name}”")
    pool: dict[str, _PoolCard] = {}
    for n, (oid, name) in enumerate(sorted(oracles.items(), key=lambda t: t[1]), 1):
        say(f"learning the printings of {name} ({n} of {len(oracles)})")
        pool[oid] = _PoolCard(oid, name, await bench.art.gallery(oid))
    return cards, pool


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("photos", type=Path, help="the folder of photos")
    ap.add_argument("--template", action="store_true", help="write labels.csv to fill in, then stop")
    ap.add_argument("--labels", type=Path, help="the labels file (default: PHOTOS/labels.csv)")
    ap.add_argument(
        "--pool-extra",
        type=Path,
        help="a text file of card names (one a line) for the artwork to choose among",
    )
    ap.add_argument("--out", type=Path, help="the report (default: PHOTOS/scanner-report.md)")
    ap.add_argument("--json", type=Path, help="also write every photo's outcome here")
    ap.add_argument(
        "--cache", type=Path, help="what was learned from Scryfall (default: PHOTOS/.scanner-measure.sqlite)"
    )
    ap.add_argument("--chromium", help="a Chromium to use instead of Playwright's own")
    args = ap.parse_args()
    photos: Path = args.photos
    if not photos.is_dir():
        ap.error(f"{photos} is not a folder")
    if args.template:
        out = write_template(photos, args.labels)
        print(f"Wrote {out}: fill in each card's name (and set, number, foil and tags where you know them).")
        return 0
    if not ocr_assets_present():
        print("Text recognition is not installed: run scripts/fetch_ocr_assets.py first.", file=sys.stderr)
        return 2
    labels, skipped = read_labels(photos, args.labels or photos / "labels.csv")
    for line in skipped:
        print("left out: " + line, file=sys.stderr)
    if not labels:
        print("No labelled photos; run with --template and fill in labels.csv.", file=sys.stderr)
        return 2
    extra = []
    if args.pool_extra:
        extra = [ln.strip() for ln in args.pool_extra.read_text("utf-8").splitlines() if ln.strip()]

    def bench() -> Bench:
        return Bench(args.cache or photos / ".scanner-measure.sqlite", ScryfallClient(user_agent=USER_AGENT))

    rows = measure(
        photos, labels, bench, pool_extra=extra, chromium=args.chromium, progress=lambda m: print(m)
    )
    summary = summarize(rows, pool=rows[0]["pool"] if rows else 0, extra=len(extra))
    text = report(summary, rows, skipped)
    out = args.out or photos / "scanner-report.md"
    out.write_text(text, "utf-8")
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "photos": rows}, indent=1), "utf-8")
    print(text)
    print(f"Report written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
