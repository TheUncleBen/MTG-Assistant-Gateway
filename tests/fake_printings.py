"""Made-up Scryfall printings of Sol Ring for the printing picker's tests (static/printpick.js):
the three printings FakeArchidekt knows (CMR 472 non-foil only, SLD 1074 etched only, SLD 1075 in
every finish), old frames and borders, a digital-only printing, a preview not out yet, a
Japanese printing and, on request, filler printings so a card has more than one page. Card
objects in Scryfall's shape, enough for ``browse_row``; the ids and prices are invented."""

from __future__ import annotations

from typing import Any

SOL_RING_ORACLE = "6ad8011d-3471-4369-9d68-b264cc027487"  # Sol Ring's real oracle id


def _card(
    n: int,
    set_code: str,
    set_name: str,
    number: str,
    released: str,
    *,
    finishes: tuple[str, ...] = ("nonfoil",),
    frame: str = "2015",
    border: str = "black",
    prices: dict[str, str | None] | None = None,
    games: tuple[str, ...] = ("paper",),
    digital: bool = False,
    lang: str = "en",
) -> dict[str, Any]:
    cid = f"00000000-0000-4000-9000-{n:012d}"
    return {
        "object": "card",
        "id": cid,
        "oracle_id": SOL_RING_ORACLE,
        "name": "Sol Ring",
        "lang": lang,
        "released_at": released,
        "set": set_code,
        "set_name": set_name,
        "collector_number": number,
        "rarity": "uncommon",
        "type_line": "Artifact",
        "mana_cost": "{1}",
        "cmc": 1.0,
        "color_identity": [],
        "layout": "normal",
        "finishes": list(finishes),
        "frame": frame,
        "border_color": border,
        "games": list(games),
        "digital": digital,
        "set_type": "expansion",
        "prices": {"usd": None, "usd_foil": None, "usd_etched": None, **(prices or {})},
        "image_uris": {
            "small": f"https://cards.scryfall.io/small/front/0/0/{cid}.jpg",
            "normal": f"https://cards.scryfall.io/normal/front/0/0/{cid}.jpg",
            "art_crop": f"https://cards.scryfall.io/art_crop/front/0/0/{cid}.jpg",
        },
    }


GOBLIN_TOKEN_ORACLE = "0e3a9b5c-6c8a-4a5e-9f2d-1b7c2d4e5f60"  # invented
GOBLIN_GUIDE_ORACLE = "1f4b0c6d-7d9b-4b6f-8a3e-2c8d3e5f6a71"  # invented


def goblin_cards() -> list[dict[str, Any]]:
    """Two printings of a Goblin token (layout token, which Scryfall's search leaves out unless
    asked to include extras) and a Goblin creature card with a name that starts the same."""
    out = []
    for n, (code, name, number, released) in enumerate(
        (
            ("tm19", "Core Set 2019 Tokens", "9", "2018-07-13"),
            ("tdmu", "Dominaria United Tokens", "7", "2022-09-09"),
        ),
        start=201,
    ):
        token = _card(n, code, name, number, released)
        token.update(
            oracle_id=GOBLIN_TOKEN_ORACLE, name="Goblin", layout="token", type_line="Token Creature — Goblin",
            mana_cost="", cmc=0.0, set_type="token", rarity="common",
        )  # fmt: skip
        out.append(token)
    guide = _card(210, "zen", "Zendikar", "126", "2009-10-02")
    guide.update(oracle_id=GOBLIN_GUIDE_ORACLE, name="Goblin Guide", type_line="Creature — Goblin Scout")
    out.append(guide)
    return out


def sol_ring_printings(fillers: int = 0) -> list[dict[str, Any]]:
    out = [
        _card(1, "cmr", "Commander Legends", "472", "2020-11-20", prices={"usd": "1.46"}),
        _card(
            2,
            "sld",
            "Secret Lair Drop",
            "1074",
            "2022-08-05",
            finishes=("etched",),
            border="borderless",
            prices={"usd_etched": "4.50"},
        ),
        _card(
            3,
            "sld",
            "Secret Lair Drop",
            "1075",
            "2022-08-06",
            finishes=("nonfoil", "foil", "etched"),
            prices={"usd": "2.10", "usd_foil": "6.00", "usd_etched": "7.00"},
        ),
        _card(
            4, "lea", "Limited Edition Alpha", "270", "1993-08-05", frame="1993", prices={"usd": "3500.00"}
        ),
        _card(
            5,
            "4ed",
            "Fourth Edition",
            "325",
            "1995-04-01",
            frame="1993",
            border="white",
            prices={"usd": "0.95"},
        ),
        _card(6, "ymid", "Alchemy: Innistrad", "12", "2021-12-09", games=("arena",), digital=True),
        _card(7, "fut", "Future Sight Remastered", "99", "2027-03-01", frame="future", games=()),
        _card(8, "sta", "Strixhaven Mystical Archive", "70", "2021-04-23", lang="ja", prices={"usd": "9.00"}),
    ]
    for i in range(fillers):
        out.append(
            _card(
                100 + i,
                f"f{i:02d}",
                f"Filler Printing {i:02d}",
                str(i + 1),
                f"2012-{1 + i % 12:02d}-{1 + i // 12:02d}",
                prices={"usd": f"{3 + i * 0.25:.2f}"},
            )
        )
    return out
