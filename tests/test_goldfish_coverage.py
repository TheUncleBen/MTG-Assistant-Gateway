"""The solo test's coverage at the top of a report (assistant tool review, 2026-10-28): a kill rate
said with no word that a third of the deck, the commander among it, was played without its effect.
The research service sends its result as text only, with the metrics in a JSON block."""

from __future__ import annotations

import json

from mtg_gateway.reports import _commander_aside, solo_coverage

HONESTY = {
    "out_of_scope": {"interaction_removal": [{"name": "Doom Blade", "drawn_pct": 0.18}]},
    "unrecognized": [
        {"name": "Aegis of the Gods", "drawn_pct": 0.11},
        {"name": "Urza, Chief Artificer", "drawn_pct": 0.0},
        {"name": "Sun Titan", "drawn_pct": 0.18},
    ],
    "modeled_low_impact": [],
}


def _text(honesty: dict) -> str:
    payload = {"n": 30, "seed": 1, "until_turn": 10, "run_id": "abc", "metrics": {"honesty": honesty}}
    return "\n".join(
        [
            "# Goldfish run",
            "Deck: 100 cards including commander | Commander: Urza, Chief Artificer | 30 games, seed 1, "
            "through turn 10 | run_id: abc",
            "",
            "## Summary",
            "- Kill (40 damage): reached 53.3% by T10",
            "",
            "## Metrics",
            "```json",
            json.dumps(payload),
            "```",
            "",
            "## Honesty report",
            "Unrecognized — 3 cards (candidates for annotation; see goldfish_annotate):",
            "- Aegis of the Gods (drawn 11%), Urza, Chief Artificer (drawn 0%), Sun Titan (drawn 18%)",
        ]
    )


def _block() -> dict:
    # what the gateway keeps of the research service's answer: its text, no structured data
    return {"tool": "goldfish_run", "ok": True, "text": _text(HONESTY)}


def test_the_commander_is_set_aside_in_a_text_only_result() -> None:
    gf = _block()
    _commander_aside(gf, "Urza, Chief Artificer")
    assert "Urza, Chief Artificer (drawn 0%)" not in gf["text"]
    assert "Unrecognized — 2 cards" in gf["text"]
    assert "Commander — Urza, Chief Artificer is cast from the command zone" in gf["text"]
    block = gf["text"].split("```json\n", 1)[1].split("\n```", 1)[0]
    honesty = json.loads(block)["metrics"]["honesty"]
    assert [c["name"] for c in honesty["unrecognized"]] == ["Aegis of the Gods", "Sun Titan"]
    assert honesty["commander_note"].startswith("Urza, Chief Artificer is cast")


def test_coverage_leads_the_report() -> None:
    gf = _block()
    _commander_aside(gf, "Urza, Chief Artificer")
    gf["quantities"] = {"Aegis of the Gods": 1, "Sun Titan": 1, "Doom Blade": 1}
    cov = solo_coverage(gf, deck_size=100)
    assert cov is not None
    assert (cov["modeled"], cov["deck_size"], cov["played_without_effect"]) == (97, 100, 2)
    assert cov["commander_modeled"] is False
    assert cov["note"].startswith("Played with their effect: 97 of 100 cards.")
    assert "2 cards were played without their effect" in cov["note"]
    assert "the commander's abilities beyond combat are not modeled" in cov["note"]
    assert "less reliable" in cov["note"]
    # saved card models are named: another assistant's model changes this run too
    gf["kept_models"] = [{"name": "Aetherflux Reservoir"}]
    assert "1 saved card model (Aetherflux Reservoir)" in solo_coverage(gf, deck_size=100)["note"]
    # no simulation, no coverage
    assert solo_coverage({"tool": "goldfish_run", "ok": False, "text": "x"}, deck_size=100) is None


def test_an_ab_says_each_commander_is_cast_from_the_command_zone() -> None:
    from mtg_gateway.reports import _ab_commanders_aside, _list_commander

    text = "\n".join(
        [
            "# Goldfish A/B",
            "## Deltas (A − B)",
            "- kill: Δ +0.1",
            "## Honesty report — Deck A",
            "Out of scope — 2 cards, counted by class:",
            "- political: Galadriel, Light of Valinor (drawn 0%), Ghostly Prison (drawn 12%)",
            "Unrecognized — 0 cards (candidates for annotation; see goldfish_annotate):",
            "- (none)",
            "## Honesty report — Deck B",
            "- political: Galadriel, Light of Valinor (drawn 4%)",
        ]
    )
    gf = {"tool": "goldfish_ab", "ok": True, "text": text}
    deck = "1 Galadriel, Light of Valinor [Commander]\n1 Ghostly Prison\n"
    assert _list_commander(deck) == "Galadriel, Light of Valinor"
    _ab_commanders_aside(gf, (_list_commander(deck), None))
    a, b = gf["text"].split("## Honesty report — Deck B")
    assert "Galadriel, Light of Valinor (drawn 0%)" not in a
    assert "Galadriel, Light of Valinor (commander, cast from the command zone;" in a
    assert "Ghostly Prison (drawn 12%)" in a
    # deck B's commander is another card: its line is left as it is
    assert "Galadriel, Light of Valinor (drawn 4%)" in b


async def test_the_forge_view_carries_the_forge_result(tmp_path) -> None:
    import asyncio

    from mtg_gateway.db import Database
    from mtg_gateway.reports import ReportService

    from .test_forge import FakeForge, _Decks

    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    out = await reports.run("alice", "42", games=300, options={"seed": 7})
    assert reports.forge_only("alice", out["report_id"])["forge_result"] is None  # still playing
    fake.finish(out["forge"]["job_id"], [1, 2, 1, 1])
    await asyncio.wait_for(reports._forge_task, timeout=20)
    view = reports.forge_only("alice", out["report_id"])
    full = reports.get("alice", out["report_id"])["forge"]
    assert view["forge_result"]["result"] == full["result"], view
    assert view["forge_result"]["result"]["seats"][0]["wins"] == 3
    assert "opponent_choice" in view["forge_result"]


def test_a_handful_of_forge_games_is_said_to_tell_little() -> None:
    from mtg_gateway.report_view import forge_lines

    seat = {"deck": "Mine", "wins": 1, "win_rate": 0.2, "win_rate_95": [0.036, 0.625]}
    few = forge_lines({"state": "done", "result": {"games": 5, "games_requested": 5, "seats": [seat]}})
    assert any(line.startswith("Only 5 games: that tells you very little") for line in few), few
    many = forge_lines({"state": "done", "result": {"games": 40, "games_requested": 40, "seats": [seat]}})
    assert not any(line.startswith("Only ") for line in many), many


def test_solo_numbers_are_never_called_a_floor() -> None:
    """With no opponents to interact, a solo test usually makes a deck look faster than it is at a
    table (independent review IR-07). Calling its numbers "a floor" said the opposite: that the deck
    is at least this fast. Every place that describes the solo test's numbers says they overstate."""
    from pathlib import Path

    from mtg_gateway import guide, report_page
    from mtg_gateway.reports import SIDE_BY_SIDE_NOTE

    gf = _block()
    _commander_aside(gf, "Urza, Chief Artificer")
    gf["quantities"] = {"Aegis of the Gods": 1, "Sun Titan": 1, "Doom Blade": 1}
    note = solo_coverage(gf, deck_size=100)["note"]
    assert "floor" not in note and "floor" not in SIDE_BY_SIDE_NOTE
    assert "Cards played without their effect make these numbers less reliable." in note
    for text in (note, SIDE_BY_SIDE_NOTE, report_page.SOLO_SUB):
        assert "faster than" in text, text
    root = Path(__file__).resolve().parents[1]
    sources = {
        "guide": Path(guide.__file__).read_text(encoding="utf-8"),
        "report view": (root / "src/mtg_gateway/report_view.py").read_text(encoding="utf-8"),
        "skill": (root / "plugin/mtg-gateway/skills/mtg-gateway/SKILL.md").read_text(encoding="utf-8"),
        "ChatGPT block": (root / "plugin/mtg-gateway/chatgpt-instructions.md").read_text(encoding="utf-8"),
        "USING.md": (root / "docs/USING.md").read_text(encoding="utf-8"),
    }
    for name, text in sources.items():
        flat = " ".join(text.replace('"', " ").split())
        assert "faster than" in flat and "floor, not a forecast" not in flat, name
