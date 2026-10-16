"""Kept card models (K1-K3 in Q136; R-134, T-152): an effect model an assistant wrote for a card the
solo test plays as a blank is kept by the gateway, for every member, and every later solo test of
a deck holding that card plays it, from the website too.

Safeguards (the coordinator's check of Q136):

- Only the shape Mystic Forge validates is kept, rebuilt here key by key from its vocabulary
  (Mystic Forge dd82cd1, mystic_forge/goldfish/cards.py): an unknown key is dropped, an unknown
  value refuses the model. Mystic Forge passes ``grants`` and the name through unchecked
  (cards.py:336-351), so both are checked here. Every number is bounded (a model past a bound is
  refused), which limits how inflated a kept model can be but does not stop one: a draw of 20 cards
  a turn is in bounds.
- A model is at most 4 KB, and each member keeps at most ``MAX_PER_MEMBER`` (a call on Ben's card).
- Other members see "added by another member", never a name.
- Models are data: an assistant that reads one (in a report) is told it was written by members.

The table is schema 14 (Track B's db.py: get_card_models, put_card_model, delete_card_model,
count_card_models). Until that lands, ``KeptModels.available`` is False and runs keep nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

MAX_MODEL_BYTES = 4096
MAX_PER_MEMBER = 200  # Ben's card (Q136 follow-up); B wires the cap value after his answer
MAX_PER_RUN = 100  # kept models merged into one run (a 100-card deck at most)
MAX_PARTS = 6  # triggers, statics and activated abilities each
MAX_COUNT = 20
MAX_STAT = 20  # power, toughness and the like, either sign
MAX_KEYWORDS = 8

# Mystic Forge's vocabulary at its pinned commit dd82cd1 (cards.py).
EVENTS = frozenset(
    {
        "cast",
        "etb",
        "spell_cast",
        "creature_etb",
        "equipment_etb",
        "land_etb",
        "attack",
        "combat_begin",
        "upkeep",
    }
)
VERBS = frozenset(
    {
        "draw",
        "gain_life",
        "treasure",
        "damage",
        "create_token",
        "ramp_land",
        "ramp_mana",
        "add_mana",
        "tutor",
        "attach",
        "attach_from_board",
        "pump",
        "extra_combat",
        "token_copy",
    }
)
CONDITIONS = frozenset({"power_gte", "metalcraft", "equipped"})
SYMBOLIC_COUNTS = frozenset(
    {
        "per_equipped_attacker",
        "per_artifact",
        "per_creature",
        "per_attacker",
        "per_land",
        "per_spell_cast_this_turn",
    }
)
STATIC_KINDS = frozenset(
    {
        "equip_free",
        "equip_free_if_metalcraft",
        "anthem",
        "token_doubling",
        "cost_reduction",
        "extra_land_drops",
    }
)
SPELL_CAST_FILTERS = frozenset({"instant_or_sorcery", "noncreature", "any"})
TUTOR_FILTERS = frozenset(
    {"equipment", "land", "creature", "instant", "sorcery", "artifact", "enchantment", "planeswalker", "any"}
)
COST_REDUCTION_FILTERS = frozenset(
    {"instant_or_sorcery", "noncreature", "creature", "artifact", "equipment", "any"}
)
DAMAGE_TARGETS = frozenset({"one_opponent", "each_opponent"})
COLORS = frozenset("WUBRG")
_PIPS = re.compile(r"^(\{(?:[WUBRGC]|\d{1,2})\})+$")
_COST = re.compile(r"^(\{(?:[WUBRGCT]|\d{1,2})\})*$")
_KEYWORD = re.compile(r"^[a-z][a-z \-]{0,29}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ModelError(ValueError):
    """A model that is not kept, and why (said to the assistant that wrote it)."""


def card_key(name: str) -> str:
    """The front face, case aside: one model per card."""
    return str(name).split(" // ")[0].strip().casefold()


def _int(card: str, field: str, value: Any, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ModelError(f"{card}: {field} must be a whole number from {low} to {high}")
    return value


def _choice(card: str, field: str, value: Any, allowed: frozenset[str]) -> str:
    if value not in allowed:
        raise ModelError(f"{card}: {field} must be one of {', '.join(sorted(allowed))}")
    return str(value)


def _count(card: str, value: Any) -> int | str:
    if isinstance(value, str):
        return _choice(card, "count", value, SYMBOLIC_COUNTS)
    return _int(card, "count", value, 0, MAX_COUNT)


def _keywords(card: str, value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_KEYWORDS:
        raise ModelError(f"{card}: keywords must be a list of at most {MAX_KEYWORDS} keywords")
    out = []
    for k in value:
        if not isinstance(k, str) or not _KEYWORD.match(k.strip().lower()):
            raise ModelError(f"{card}: keyword {str(k)[:40]!r} is not a plain keyword")
        out.append(k.strip().lower())
    return out


def _filter_name(card: str, value: str) -> str:
    name = value[5:].strip()
    if not name or len(name) > 141 or _CONTROL.search(name):
        raise ModelError(f"{card}: a tutor filter names one card, as name:<card name>")
    return f"name:{name}"


def _verb_params(card: str, raw: dict[str, Any], out: dict[str, Any], *, cast_filter: bool) -> None:
    """The verb's own parameters (cards.py _check_verb_params), each bounded."""
    do = out["do"]
    if "count" in raw:
        out["count"] = _count(card, raw["count"])
    if do == "damage":
        out["target"] = _choice(card, "target", raw.get("target"), DAMAGE_TARGETS)
    if do in ("create_token", "pump"):
        out["power"] = _int(card, "power", raw.get("power"), -MAX_STAT, MAX_STAT)
        out["toughness"] = _int(card, "toughness", raw.get("toughness"), -MAX_STAT, MAX_STAT)
    if do == "pump" and "duration" in raw:
        out["duration"] = _choice(card, "duration", raw["duration"], frozenset({"eot", "permanent"}))
    if do == "tutor" and not cast_filter:
        f = raw.get("filter") or "any"
        out["filter"] = (
            _filter_name(card, f)
            if isinstance(f, str) and f.startswith("name:")
            else _choice(card, "filter", f, TUTOR_FILTERS)
        )
    if do == "add_mana":
        if raw.get("colors") == "any":
            out["colors"] = "any"
        elif isinstance(raw.get("pips"), str) and _PIPS.match(raw["pips"]) and len(raw["pips"]) <= 40:
            out["pips"] = raw["pips"]
        else:
            raise ModelError(
                f'{card}: add_mana needs pips such as "{{R}}{{R}}", or colors "any" with a count'
            )
    kw = _keywords(card, raw.get("keywords"))
    if kw:
        out["keywords"] = kw


def _trigger(card: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ModelError(f"{card}: each trigger is an object")
    out: dict[str, Any] = {
        "on": _choice(card, "on", raw.get("on"), EVENTS),
        "do": _choice(card, "do", raw.get("do"), VERBS),
    }
    if out["on"] == "spell_cast" and out["do"] == "tutor":
        raise ModelError(f"{card}: a spell_cast trigger cannot tutor")
    if out["on"] == "spell_cast" and raw.get("filter") is not None:
        out["filter"] = _choice(card, "filter", raw["filter"], SPELL_CAST_FILTERS)
    if raw.get("if") is not None:
        head, _, arg = str(raw["if"]).partition(":")
        _choice(card, "if", head, CONDITIONS)
        if head == "power_gte":
            if not arg.isdigit() or not 0 <= int(arg) <= MAX_STAT:
                raise ModelError(f"{card}: if must be power_gte:<0 to {MAX_STAT}>")
            out["if"] = f"power_gte:{int(arg)}"
        else:
            out["if"] = head
    _verb_params(card, raw, out, cast_filter=out["on"] == "spell_cast")
    return out


def _activated(card: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ModelError(f"{card}: each activated ability is an object")
    cost = raw.get("cost") or ""
    if not isinstance(cost, str) or len(cost) > 40 or not _COST.match(cost):
        raise ModelError(f"{card}: cost must be a mana cost such as {{2}}{{W}}, optionally with {{T}}")
    out: dict[str, Any] = {"do": _choice(card, "do", raw.get("do"), VERBS)}
    if cost:
        out["cost"] = cost
    if raw.get("sac_self"):
        out["sac_self"] = True
    _verb_params(card, raw, out, cast_filter=False)
    return out


def _static(card: str, raw: Any) -> str | dict[str, Any]:
    if isinstance(raw, str):
        return _choice(card, "static", raw, STATIC_KINDS)
    if not isinstance(raw, dict):
        raise ModelError(f"{card}: each static is a kind or an object")
    out: dict[str, Any] = {"kind": _choice(card, "static", raw.get("kind"), STATIC_KINDS)}
    for key in ("power", "toughness"):
        if key in raw:
            out[key] = _int(card, key, raw[key], -MAX_STAT, MAX_STAT)
    if "count" in raw:
        out["count"] = _int(card, "count", raw["count"], 1, 5)
    if "amount" in raw:
        out["amount"] = _int(card, "amount", raw["amount"], 0, 10)
    if out["kind"] == "cost_reduction":
        f = raw.get("filter", "any")
        if not (
            f in COST_REDUCTION_FILTERS or (isinstance(f, str) and f[:6] == "color:" and f[6:] in COLORS)
        ):
            raise ModelError(
                f"{card}: a cost_reduction filter is one of {', '.join(sorted(COST_REDUCTION_FILTERS))}"
            )
        out["filter"] = f
    kw = _keywords(card, raw.get("keywords"))
    if kw:
        out["keywords"] = kw
    return out


def _parts(card: str, raw: Any, field: str, parse: Any) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_PARTS:
        raise ModelError(f"{card}: {field} must be a list of at most {MAX_PARTS}")
    return [parse(card, x) for x in raw]


def clean_model(raw: Any, *, card: str) -> dict[str, Any]:
    """The model rebuilt from Mystic Forge's vocabulary, for the card ``card`` (the deck's own
    name for it): its name must be that card. Raises ModelError with the reason."""
    if not isinstance(raw, dict):
        raise ModelError("a model is an object")
    name = raw.get("name")
    if not isinstance(name, str) or card_key(name) != card_key(card):
        raise ModelError(f"{str(name)[:60]!r}: the model's name must be the card's own name")
    out: dict[str, Any] = {"name": card}
    for field, parse in (("triggers", _trigger), ("statics", _static), ("activated", _activated)):
        parts = _parts(card, raw.get(field), field, parse)
        if parts:
            out[field] = parts
    grants = raw.get("grants")
    if grants is not None:
        if not isinstance(grants, dict):
            raise ModelError(f"{card}: grants is an object with power, toughness and keywords")
        g: dict[str, Any] = {}
        for key in ("power", "toughness"):
            if key in grants:
                g[key] = _int(card, f"grants {key}", grants[key], -MAX_STAT, MAX_STAT)
        kw = _keywords(card, grants.get("keywords"))
        if kw:
            g["keywords"] = kw
        if g:
            out["grants"] = g
    if raw.get("inert"):
        out["inert"] = True
    if len(out) == 1:
        raise ModelError(f"{card}: the model says nothing the solo test can play")
    if len(json.dumps(out)) > MAX_MODEL_BYTES:
        raise ModelError(f"{card}: the model is larger than 4 KB")
    return out


def model_hash(model: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()[:12]


class KeptModels:
    """The kept models through the database (schema 14). ``available`` is False until the table
    exists; then nothing is kept and runs use only the assistant's own models."""

    def __init__(self, db: Any) -> None:
        self.db = db

    @property
    def available(self) -> bool:
        return all(
            callable(getattr(self.db, m, None))
            for m in ("get_card_models", "put_card_model", "count_card_models")
        )

    def for_cards(self, names: list[str]) -> dict[str, dict[str, Any]]:
        """card key -> {name, model, added_by, added_at, updated_at} for the cards that have one."""
        if not self.available or not names:
            return {}
        try:
            rows = self.db.get_card_models([card_key(n) for n in names])
        except Exception:  # a store that cannot be read never stops a report
            logger.exception("reading kept card models failed")
            return {}
        out = {}
        for row in rows or []:
            try:
                model = json.loads(row["model_json"])
            except (TypeError, ValueError, KeyError):
                continue
            if isinstance(model, dict):
                out[str(row["card_key"])] = {**dict(row), "model": model}
        return out

    def _owns(self, sub: str, key: str) -> bool:
        """Whether ``sub`` added the kept model for ``key`` (a failed read counts as no)."""
        try:
            rows = self.db.get_card_models([key])
        except Exception:
            logger.exception("reading a kept card model failed")
            return False
        return any(str(dict(r).get("added_by")) == sub for r in rows)

    def keep(self, sub: str, raw_models: list[Any], deck_names: list[str]) -> dict[str, list[dict[str, str]]]:
        """Keep the valid models among ``raw_models`` for cards of the deck (by front face).
        Returns {"kept": [{"name"}], "refused": [{"name", "why"}]}: what the assistant is told."""
        kept: list[dict[str, str]] = []
        refused: list[dict[str, str]] = []
        if not self.available:
            return {"kept": kept, "refused": refused}
        by_key = {card_key(n): n for n in deck_names}
        try:
            have = int(self.db.count_card_models(sub))
        except Exception:
            logger.exception("counting kept card models failed")
            return {"kept": kept, "refused": refused}
        now = int(time.time())
        for raw in raw_models:
            name = str(raw.get("name") if isinstance(raw, dict) else "")[:141]
            card = by_key.get(card_key(name)) if name else None
            if card is None:
                refused.append({"name": name, "why": "the deck does not hold this card"})
                continue
            # the table has no length limits of its own (Track B's schema 14 review, note a): a
            # front face is at most 141 characters, both faces at most twice that and the " // "
            if len(card_key(card)) > 141 or len(card) > 286:
                refused.append({"name": card[:141], "why": "the card's name is too long"})
                continue
            try:
                model = clean_model(raw, card=card)
            except ModelError as exc:
                refused.append({"name": card, "why": str(exc)})
                continue
            # TODO(Q138 items 2-3): the cap value and who may replace a card's model wait on Ben.
            # The cap counts only a save that newly counts toward the member: re-saving a model the
            # member already keeps replaces it and is never refused at the cap.
            if have >= MAX_PER_MEMBER and not self._owns(sub, card_key(card)):
                refused.append(
                    {
                        "name": card,
                        "why": f"you already keep {MAX_PER_MEMBER} card models, the most one member can keep",
                    }
                )
                continue
            try:
                added = self.db.put_card_model(card_key(card), card, json.dumps(model), sub, now)
            except Exception:
                logger.exception("keeping a card model failed")
                refused.append({"name": card, "why": "the gateway could not keep it"})
                continue
            if added:
                have += 1
            kept.append({"name": card})
        return {"kept": kept, "refused": refused}


def merge(
    sub: str, store: KeptModels, names: list[str], options: dict[str, Any]
) -> tuple[dict[str, Any], list]:
    """The run's options with the kept models of the deck's cards added to its annotations (the
    assistant's own model for a card wins), and what the report records of each kept model used:
    its card, a hash of the model, whether the member added it, and when it was last changed. The
    report never records who else added one (K2)."""
    own = [a for a in options.get("annotations") or [] if isinstance(a, dict)]
    mine = {card_key(str(a.get("name") or "")) for a in own}
    kept = [k for key, k in store.for_cards(names).items() if key not in mine][:MAX_PER_RUN]
    if not kept:
        return options, []
    used = [
        {
            "name": str(k.get("name") or ""),
            "model": model_hash(k["model"]),
            "yours": k.get("added_by") == sub,
            "changed_at": int(k.get("updated_at") or k.get("added_at") or 0),
        }
        for k in kept
    ]
    return {**options, "annotations": own + [k["model"] for k in kept]}, used
