"""scripts/measure_scanner.py: the scanner's two signals measured on a folder of photos.

The script runs the real /scan page code (edge finding, the real text recognition, the artwork
fingerprint) in headless Chromium on each photo, resolves what it read with the gateway's own
resolver, and compares the artwork fingerprint with every printing of every labelled card. Here it
runs offline on drawn card photos, with the fake Scryfall and a fake image host that serves the
same drawn cards as Scryfall's pictures. Skipped without the OCR assets or a Chromium build.
"""

from __future__ import annotations

import csv
import sys
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from mtg_gateway.scan.scryfall import ScryfallClient

from .fake_scryfall import FakeScryfall
from .test_scan_art import _art
from .test_scan_browser import VENDOR, _chromium_path, card_photo, skewed_photo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import measure_scanner as ms  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (VENDOR / "worker.min.js").exists(), reason="run scripts/fetch_ocr_assets.py first"
)

ART = {"Sol Ring": 11, "Command Tower": 22, "Cultivate": 33}


def drawn_card(path: Path, name: str, info: tuple[str, str] | None) -> Image.Image:
    """A phone photo of a drawn card (test_scan_browser's drawing, with its own artwork in the art
    box) lying a little turned on a table, and the card itself as Scryfall's ``small`` picture."""
    card = path.with_suffix(".card.jpg")
    card_photo(card, name, info)
    im = Image.open(card).convert("RGB")
    w, h = im.size
    box = (int(w * 0.12), int(h * 0.17), int(w * 0.88), int(h * 0.49))
    im.paste(_art(ART[name], box[2] - box[0], box[3] - box[1]), box[:2])
    im.save(card, "JPEG", quality=92)
    skewed_photo(path, card, [(300, 220), (1130, 300), (1060, 1620), (230, 1520)])
    small = im.crop((20, 20, w - 20, h - 20)).resize((146, 204), Image.Resampling.BILINEAR)
    card.unlink()
    return small


@pytest.fixture
def bench(tmp_path: Path):
    photos = tmp_path / "photos"
    photos.mkdir()
    smalls = {
        "Sol Ring": drawn_card(photos / "a-sol-ring.jpg", "Sol Ring", ("0021 U", "FRC • EN")),
        "Command Tower": drawn_card(photos / "b-tower.jpg", "Command Tower", None),
    }
    smalls["Cultivate"] = drawn_card(tmp_path / "cultivate.jpg", "Cultivate", None)
    Image.new("RGB", (1400, 1800), (176, 146, 108)).save(photos / "c-table.jpg", "JPEG", quality=85)
    (photos / "notes.txt").write_text("not a photo")
    sf = FakeScryfall()
    by_url = {(c.get("image_uris") or {}).get("small"): c["name"] for c in sf.cards if c.get("image_uris")}
    images: list[str] = []

    def image(request: httpx.Request) -> httpx.Response:
        images.append(str(request.url))
        name = by_url.get(str(request.url))
        if name not in smalls:
            return httpx.Response(404)
        buf = BytesIO()
        smalls[name].save(buf, "JPEG", quality=85)
        return httpx.Response(200, content=buf.getvalue(), headers={"content-type": "image/jpeg"})

    def make() -> ms.Bench:
        client = ScryfallClient("https://scryfall.test", min_interval=0.0, http=sf.client())
        return ms.Bench(
            tmp_path / "cache.sqlite",
            client,
            image_http=httpx.AsyncClient(transport=httpx.MockTransport(image)),
            image_interval=0.0,
        )

    return photos, make, images


def test_the_template_lists_every_photo_once(bench) -> None:
    photos, _make, _images = bench
    out = ms.write_template(photos)
    assert out == photos / "labels.csv"
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert [r["file"] for r in rows] == ["a-sol-ring.jpg", "b-tower.jpg", "c-table.jpg"]
    assert list(rows[0]) == ["file", "name", "set", "number", "foil", "tags"]
    # Filled in, it reads back; a row without a name is left out and said so.
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "name", "set", "number", "foil", "tags"])
        w.writerow(["a-sol-ring.jpg", "Sol Ring", "FRC", "0021", "no", "plain; info line"])
        w.writerow(["b-tower.jpg", "Command Tower", "", "", "", "plain"])
        w.writerow(["c-table.jpg", "", "", "", "", ""])
    labels, skipped = ms.read_labels(photos, out)
    assert [(lb.file, lb.name, lb.set, lb.number, lb.foil, lb.tags) for lb in labels] == [
        ("a-sol-ring.jpg", "Sol Ring", "frc", "21", False, ["plain", "info line"]),
        ("b-tower.jpg", "Command Tower", "", "", None, ["plain"]),
    ]
    assert skipped == ["c-table.jpg: no card name"]
    # A template is never written over labels already there.
    with pytest.raises(FileExistsError):
        ms.write_template(photos)


def test_both_signals_are_measured_per_photo_and_summed_up(bench) -> None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    photos, make, images = bench
    labels = [
        ms.Label("a-sol-ring.jpg", "Sol Ring", "frc", "21", False, ["plain"]),
        ms.Label("b-tower.jpg", "Command Tower", "", "", None, ["plain"]),
        ms.Label("c-table.jpg", "Command Tower", "", "", None, ["no card"]),
    ]
    rows = ms.measure(photos, labels, make, pool_extra=["Cultivate"], chromium=exe)
    sol, tower, table = rows
    # Sol Ring: outline, the title and the collector line read, the right card and printing (the
    # page's own tiers say whether Auto would add it: a turned photo reads under the bar here); the
    # artwork names it among all three cards, and is sure of it.
    assert sol["edges"] is True and sol["text"]["read"].startswith("Sol Ring"), sol
    assert sol["text"]["card_ok"] is True and sol["text"]["printing_ok"] is True, sol
    assert sol["text"]["tier"] in ("certain", "unsure") and sol["text"]["wrong_auto_add"] is False, sol
    assert sol["text"]["auto_add"] is (sol["text"]["tier"] == "certain"), sol
    assert sol["art"]["fingerprint"] is True and sol["art"]["main_card_ok"] is True, sol
    assert sol["art"]["main_confident"] is True, sol
    # Command Tower: no printing labelled, so the printing is not scored; the artwork still finds it.
    assert tower["text"]["card_ok"] is True and tower["text"]["printing_ok"] is None, tower
    assert tower["art"]["main_card_ok"] is True, tower
    # The bare table: no outline, no fingerprint, no card.
    assert table["edges"] is False and table["art"]["fingerprint"] is False, table
    assert table["text"]["card_ok"] is False and table["text"]["auto_add"] is False, table
    assert table["art"]["main_card_ok"] is False, table
    # Every image came from the fake host (three cards' galleries), none from the network.
    assert images and all(u.startswith("https://cards.scryfall.io/") for u in images)

    summary = ms.summarize(rows, pool=3)
    assert summary["photos"] == 3
    assert summary["text"]["card_ok"] == (2, 3) and summary["text"]["printing_ok"] == (1, 1)
    assert summary["art"]["main_card_ok"] == (2, 3)
    assert summary["by_tag"]["plain"]["text"] == (2, 2) and summary["by_tag"]["no card"]["text"] == (0, 1)
    text = ms.report(summary, rows)
    assert "## Text recognition (title and collector line)" in text
    assert "## Artwork matching" in text
    assert "| Right card | 2 of 3 (67%) |" in text
    assert "3 cards" in text  # the pool the artwork was matched in, said plainly
    assert "c-table.jpg" in text  # every photo has its own line


def test_a_wrong_add_and_a_sure_wrong_artwork_are_counted_against_the_signal() -> None:
    """The figures that decide the question: a card Auto would add that is wrong (here the foil),
    and artwork that is sure of the wrong card."""
    from mtg_gateway.scan import art

    def printing(pid: str, ill: str, seed: int) -> art._Printing:
        return art._Printing(pid, ill, "2020-01-01", bytes([seed]) * art.ROW_BYTES, {"id": pid})

    pool = {
        "o-sol": ms._PoolCard("o-sol", "Sol Ring", [printing("p-frc", "i-1", 0x00)]),
        "o-tower": ms._PoolCard("o-tower", "Command Tower", [printing("p-tow", "i-2", 0x0F)]),
    }
    label = ms.Label("x.jpg", "Sol Ring", "frc", "21", True, ["foil"])
    label_card = {"name": "Sol Ring", "oracle_id": "o-sol", "scryfall_id": "p-frc", "set": "frc"}
    card = {**label_card, "collector_number": "21"}
    import base64

    raw = {
        "edges": True,
        "tierText": "certain",
        "ocr": {"text": "Sol Ring", "confidence": 95, "info": {"set": "frc", "number": "21", "foil": False}},
        "text": {"card": card, "status": "printing", "foil": False},
        "page": {"card": card, "art": {"status": "matched"}},
        # A fingerprint nearest the other card's artwork by far: sure, and wrong.
        "request": {"art_hash": base64.b64encode(bytes([0x0F]) * art.HASH_BYTES).decode()},
    }
    row = ms.score(label, raw, label_card, pool, art.ArtSettings())
    assert row["text"]["card_ok"] and row["text"]["printing_ok"] and row["text"]["foil_ok"] is False
    assert row["text"]["auto_add"] and row["text"]["wrong_auto_add"]
    assert row["art"]["main_card"] == "Command Tower" and row["art"]["main_confident"]
    assert row["art"]["main_wrong_confident"] and row["art"]["main_printing_ok"] is False
    assert row["art"]["picker_ok"] is True  # today's role: it backed the printing the name found
    summary = ms.summarize([row], pool=2)
    assert summary["text"]["wrong_auto_add"] == (1, 1) and summary["art"]["main_wrong_confident"] == (1, 1)
    assert summary["by_tag"]["foil"] == {"photos": 1, "text": (1, 1), "art": (0, 1)}
