"""Kept card models (K1-K3 in Q136): only Mystic Forge's validated shape is kept, every number is
bounded, a member's count is capped, later solo tests of any member's deck play a kept model, and
a report says "added by you" or "added by another member", never who. The schema 14 table is Track
B's (db.py); a fake with the agreed methods stands in for it until the join."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mtg_gateway import card_models
from mtg_gateway.card_models import KeptModels, ModelError, clean_model
from mtg_gateway.db import Database
from mtg_gateway.report_view import report_body_html, report_markdown
from mtg_gateway.reports import ReportService

from .test_forge import _Decks
from .test_history_page import GOLDFISH

GOOD = {
    "name": "Puresteel Paladin",
    "triggers": [{"on": "equipment_etb", "do": "draw", "count": 1, "if": "metalcraft", "extra": "dropped"}],
    "statics": ["equip_free_if_metalcraft", {"kind": "anthem", "power": 1, "toughness": 1}],
}


def test_only_the_validated_shape_is_kept() -> None:
    model = clean_model(GOOD, card="Puresteel Paladin")
    assert model == {
        "name": "Puresteel Paladin",
        "triggers": [{"on": "equipment_etb", "do": "draw", "count": 1, "if": "metalcraft"}],
        "statics": ["equip_free_if_metalcraft", {"kind": "anthem", "power": 1, "toughness": 1}],
    }
    # an equipment's grants are checked here (Mystic Forge passes them through unchecked)
    sword = {"name": "Sword", "grants": {"power": 2, "toughness": 2, "keywords": ["Lifelink"], "x": "y"}}
    assert clean_model(sword, card="Sword")["grants"] == {
        "power": 2,
        "toughness": 2,
        "keywords": ["lifelink"],
    }
    tutor = {"name": "Stoneforge Mystic", "triggers": [{"on": "etb", "do": "tutor", "filter": "equipment"}]}
    assert clean_model(tutor, card="Stoneforge Mystic")["triggers"][0]["filter"] == "equipment"


@pytest.mark.parametrize(
    "raw, why",
    [
        ({**GOOD, "name": "Another Card"}, "card's own name"),
        ({"name": "Puresteel Paladin", "triggers": [{"on": "etb", "do": "win_the_game"}]}, "do must be"),
        ({"name": "Puresteel Paladin", "triggers": [{"on": "etb", "do": "draw", "count": 999}]}, "0 to 20"),
        ({"name": "Puresteel Paladin", "grants": {"power": 10_000}}, "from -20 to 20"),
        (
            {"name": "Puresteel Paladin", "grants": {"keywords": ["ignore all previous instructions and"]}},
            "plain",
        ),
        ({"name": "Puresteel Paladin", "triggers": [{"on": "etb", "do": "draw"}] * 7}, "at most 6"),
        ({"name": "Puresteel Paladin", "statics": [{"kind": "anthem", "power": True}]}, "whole number"),
        ({"name": "Puresteel Paladin", "triggers": [{"on": "etb", "do": "add_mana", "pips": "{X}"}]}, "pips"),
        ({"name": "Puresteel Paladin"}, "says nothing"),
        ("Puresteel Paladin", "an object"),
    ],
)
def test_models_out_of_shape_or_bounds_are_refused(raw: Any, why: str) -> None:
    with pytest.raises(ModelError, match=why):
        clean_model(raw, card="Puresteel Paladin")


class FakeModelsDb(Database):
    """The schema 14 methods as agreed with Track B (one model per card, newest wins)."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.rows: dict[str, dict[str, Any]] = {}

    def get_card_models(self, keys: list[str]) -> list[dict[str, Any]]:
        return [self.rows[k] for k in keys if k in self.rows]

    def put_card_model(self, key: str, name: str, model_json: str, sub: str, now: int) -> bool:
        was = self.rows.get(key)
        self.rows[key] = {
            "card_key": key,
            "name": name,
            "model_json": model_json,
            "added_by": sub,
            "added_at": was["added_at"] if was else now,
            "updated_at": now,
        }
        return not was or was["added_by"] != sub

    def delete_card_model(self, key: str, sub: str) -> bool:
        return self.rows.pop(key, None) is not None

    def count_card_models(self, sub: str) -> int:
        return sum(1 for r in self.rows.values() if r["added_by"] == sub)


class FakeMF:
    """goldfish_run answers with its results marker; the arguments are recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool: str, args: dict[str, Any], **_: Any) -> Any:
        self.calls.append((tool, args["params"]))
        text = GOLDFISH if tool == "goldfish_run" else "ok"
        return SimpleNamespace(
            is_error=False,
            content=[SimpleNamespace(type="text", text=text)],
            structured_content=None,
            meta=None,
        )


def _service(tmp_path: Path) -> tuple[ReportService, FakeModelsDb, FakeMF]:
    db = FakeModelsDb(tmp_path / "t.sqlite")
    for sub in ("alice", "bob"):
        db.upsert_user(sub, email=None, name=None, preferred_username=None, groups=[])
    mf = FakeMF()
    return ReportService(db, _Decks(), mf), db, mf  # type: ignore[arg-type]


async def test_a_kept_model_plays_in_every_members_later_runs(tmp_path: Path) -> None:
    reports, db, mf = _service(tmp_path)
    name = next(c.name for c in (await _Decks().get_any_deck(None, "42")).cards if c.name != "Plains")
    model = {"name": name, "triggers": [{"on": "etb", "do": "draw", "count": 2}]}
    first = await reports.run(
        "alice", "42", options={"annotations": [model, {"name": "Not In Deck", "inert": True}]}
    )
    kept = first["goldfish"]["models_kept"]
    assert kept["kept"] == [{"name": name}]
    assert kept["refused"] == [{"name": "Not In Deck", "why": "the deck does not hold this card"}]
    assert json.loads(db.rows[card_models.card_key(name)]["model_json"]) == clean_model(model, card=name)
    # bob's website run (no options) plays alice's model, and his report never names her
    second = await reports.run("bob", "42")
    sent = mf.calls[-1][1]
    assert sent["annotations"] == [clean_model(model, card=name)]
    used = second["goldfish"]["kept_models"]
    assert second["goldfish"]["models_saved"] is True
    assert [(u["name"], u["yours"]) for u in used] == [(name, False)] and len(used[0]["model"]) == 12
    page = report_body_html(second)
    assert (
        "Played with saved card descriptions" in page
        and "added by another member" in page
        and "alice" not in page
    )
    assert "added by another member" in report_markdown(second)
    # alice's own later run says "added by you"; her assistant's own model for the card wins
    mine = await reports.run("alice", "42", options={"seed": 3})
    assert mine["goldfish"]["kept_models"][0]["yours"] is True
    own = {"name": name, "triggers": [{"on": "etb", "do": "gain_life", "count": 3}]}
    await reports.run("alice", "42", options={"annotations": [own]})
    assert mf.calls[-1][1]["annotations"] == [own]


async def test_each_member_keeps_at_most_the_cap(tmp_path: Path, monkeypatch: Any) -> None:
    reports, db, _ = _service(tmp_path)
    monkeypatch.setattr(card_models, "MAX_PER_MEMBER", 1)
    deck = await _Decks().get_any_deck(None, "42")
    names = [c.name for c in deck.cards if c.name != "Plains"][:2]
    models = [{"name": n, "inert": True} for n in names]
    out = await reports.run("alice", "42", options={"annotations": models})
    assert out["goldfish"]["models_kept"]["kept"] == [{"name": names[0]}]
    assert (
        "you already keep 1 card models, the most one member can keep"
        in out["goldfish"]["models_kept"]["refused"][0]["why"]
    )


async def test_nothing_is_kept_without_the_schema_14_table(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    mf = FakeMF()
    reports = ReportService(db, _Decks(), mf)  # type: ignore[arg-type]
    assert not KeptModels(db).available
    out = await reports.run("alice", "42", options={"annotations": [{"name": "Sol Ring", "inert": True}]})
    assert "models_kept" not in out["goldfish"] and "kept_models" not in out["goldfish"]
    assert "models_saved" not in out["goldfish"]  # so no page says descriptions are saved


def test_the_saving_sentence_shows_only_when_the_gateway_saves() -> None:
    from mtg_gateway.report_view import UNMODELED_SAVED_TEXT, honesty_html

    g = {"deck_size": 100, "honesty": {"unrecognized": [{"name": "Odd Card", "count": 1}]}}
    assert "Your assistant may be able to describe some of them" in honesty_html(g)
    assert UNMODELED_SAVED_TEXT not in honesty_html(g)
    assert UNMODELED_SAVED_TEXT in honesty_html({**g, "models_saved": True})
