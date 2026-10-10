"""Tool input shapes shared by the assistant's tools.

Two jobs:

* ``inline_refs`` turns a JSON schema with ``$defs`` / ``$ref`` into one self-contained schema,
  so an assistant reads every field name and allowed value where the parameter is declared
  (some hosts show the model a ``$ref`` without the definition it points to).
* The card-field spelling: every tool that takes a card says ``name``, ``set_code``,
  ``collector_number``, ``quantity`` and ``finish`` (nonfoil, foil or etched). Older spellings
  (``card_name``, ``set``, ``foil: true``, finish ``normal``) are still accepted, so an assistant
  following an earlier description keeps working: ``card_aliases`` maps them onto one spelling.
"""

from __future__ import annotations

import copy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictInt, model_validator

from .archidekt import FORMAT_IDS

# finish names an assistant may use, mapped to the one spelling the tools document
FINISH_ALIASES = {"nonfoil": "nonfoil", "normal": "nonfoil", "foil": "foil", "etched": "etched"}


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``schema`` with every local ``#/$defs/...`` reference replaced by its definition
    and ``$defs`` dropped. A definition that refers to itself (directly or through others) stays
    a reference at the point where it would repeat, with its ``$defs`` entry kept, so the result
    is always finite."""
    defs = schema.get("$defs") or {}
    kept: dict[str, Any] = {}

    def walk(node: Any, seen: tuple[str, ...]) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                key = ref[len("#/$defs/") :]
                if key in defs and key not in seen:
                    merged = {**copy.deepcopy(defs[key]), **{k: v for k, v in node.items() if k != "$ref"}}
                    return walk(merged, (*seen, key))
                if key in defs:
                    kept[key] = defs[key]
                return dict(node)
            return {k: walk(v, seen) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v, seen) for v in node]
        return node

    out = walk(schema, ())
    if kept:
        out["$defs"] = {k: walk(v, (k,)) for k, v in kept.items()}
    return out


_SCHEMA_MAPS = ("properties", "$defs", "definitions", "patternProperties")
_SCHEMA_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")
_SCHEMA_ONE = ("items", "additionalProperties", "not")


def compact_schema(schema: Any) -> Any:
    """The same schema in fewer characters, for the tool list every conversation pays for: no
    generated ``title`` on each field (the field's name says it), and an optional field written
    as its one real type rather than "this type or null, default null". A field whose description
    mentions null (null clears it, say) keeps its null. Validation is untouched: the server checks
    the arguments against its own model, never against this listing."""
    if not isinstance(schema, dict):
        return schema
    node = {k: v for k, v in schema.items() if not (k == "title" and isinstance(v, str))}
    branches = node.get("anyOf")
    if (
        isinstance(branches, list)
        and len(branches) == 2
        and {"type": "null"} in branches
        and node.get("default", ...) is None
        and "null" not in str(node.get("description", ""))
    ):
        (real,) = [b for b in branches if b != {"type": "null"}]
        rest = {k: v for k, v in node.items() if k not in ("anyOf", "default")}
        if isinstance(real, dict):
            return compact_schema({**real, **rest})  # the branch's own title and nulls go too
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in _SCHEMA_MAPS and isinstance(v, dict):
            out[k] = {name: compact_schema(sub) for name, sub in v.items()}
        elif k in _SCHEMA_LISTS and isinstance(v, list):
            out[k] = [compact_schema(sub) for sub in v]
        elif k in _SCHEMA_ONE and isinstance(v, dict):
            out[k] = compact_schema(v)
        else:
            out[k] = v
    return out


def flatten_params(schema: dict[str, Any]) -> dict[str, Any] | None:
    """For a tool whose only argument is one object named ``params`` (Mystic Forge's style), the
    schema of that object as the whole input, references inlined; None for any other shape."""
    props = schema.get("properties") or {}
    if set(props) != {"params"}:
        return None
    inner = inline_refs({"$defs": schema.get("$defs") or {}, **props["params"]})
    if inner.get("type") != "object" or "properties" not in inner:
        return None
    out = {k: v for k, v in inner.items() if k not in ("title", "description")}
    out["type"] = "object"
    return out


def card_aliases(item: Any) -> Any:
    """One card entry with the older field names mapped onto the documented ones: card_name ->
    name, set -> set_code, foil: true -> finish foil, finish normal -> nonfoil. Plain strings
    and non-dicts pass through unchanged; a documented field wins over its older alias."""
    if not isinstance(item, dict):
        return item
    out = dict(item)
    card_name = out.pop("card_name", None)
    if "name" not in out and card_name is not None:
        out["name"] = card_name
    old_set = out.pop("set", None)
    if "set_code" not in out and old_set is not None:
        out["set_code"] = old_set
    if out.get("finish") is None and out.get("foil") is True:
        out["finish"] = "foil"
    out.pop("foil", None)
    if isinstance(out.get("finish"), str):
        out["finish"] = FINISH_ALIASES.get(out["finish"].strip().lower(), out["finish"])
    return out


def drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# -- typed inputs ------------------------------------------------------------------------------
# The models below only describe and type the inputs; the services still validate every value
# (ranges, lengths, set codes), so a model never widens what a tool accepts (numbers and
# yes/no fields are strict: "3" or true is not a quantity, "yes" is not a boolean). ``extra="allow"``
# keeps older field names working (card_aliases maps them before validation).


def _lower(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


def _upper(value: Any) -> Any:
    return value.strip().upper() if isinstance(value, str) else value


# Case and spacing aside, as the services always read these words.
Finish = Annotated[Literal["nonfoil", "foil", "etched"], BeforeValidator(_lower)]
# Every format name Archidekt takes (the services' own list, so a new format needs one edit).
DeckFormat = Annotated[Literal[tuple(FORMAT_IDS)], BeforeValidator(_lower)]  # type: ignore[valid-type]


class _Card(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data: Any) -> Any:
        return card_aliases(data)


class DeckChange(_Card):
    action: Annotated[
        Literal[
            "add",
            "remove",
            "set_quantity",
            "set_category",
            "set_commander",
            "set_finish",
            "set_printing",
            "set_label",
            "set_mana_value",
        ],
        BeforeValidator(_lower),
    ] = Field(description="What to do with the card.")
    name: str = Field(description="The card's name, as Archidekt or Scryfall spells it.")
    quantity: StrictInt | None = Field(
        default=None,
        ge=0,
        le=99,
        description="Copies, 0 to 99: how many to add or remove, or the new count for set_quantity.",
    )
    category: str | None = Field(default=None, description="The category, for add or set_category.")
    set_code: str | None = Field(
        default=None, description="Set code of the exact printing (with collector_number), e.g. cmr."
    )
    collector_number: str | None = Field(default=None, description="Collector number of that printing.")
    finish: Finish | None = Field(default=None, description="nonfoil, foil or etched.")
    zone: Annotated[Literal["main", "side"], BeforeValidator(_lower)] | None = Field(
        default=None, description="main (default, the deck proper) or side (maybeboard and sideboard rows)."
    )
    label: str | None = Field(
        default=None,
        max_length=48,  # a name of up to 40 characters, or Archidekt's own "Name,#rrggbb"
        description="For set_label: the colour tag's name, e.g. Have or Proxy; an empty string takes it off.",
    )
    color: str | None = Field(
        default=None,
        pattern=r"^#[0-9a-fA-F]{6}$",
        description="For set_label: the tag's colour as #rrggbb (grey when left out).",
    )
    mana_value: StrictInt | None = Field(
        default=None,
        ge=0,
        le=20,
        description="For set_mana_value: the card's custom mana value, 0 to 20; null takes it off.",
    )


class NewDeckCard(_Card):
    name: str = Field(description="The card's name.")
    quantity: StrictInt = Field(default=1, ge=1, le=99, description="Copies, 1 to 99.")
    category: str | None = Field(default=None, description="Category, e.g. Commander, Ramp.")
    set_code: str | None = Field(
        default=None, description="Set code of the printing (with collector_number)."
    )
    collector_number: str | None = Field(default=None, description="Collector number of that printing.")
    finish: Finish | None = Field(default=None, description="nonfoil (default), foil or etched.")


class CollectionAdd(_Card):
    name: str | None = Field(
        default=None, description="The card's name (or give set_code + collector_number)."
    )
    set_code: str | None = Field(default=None, description="Set code of the printing, e.g. cmr.")
    collector_number: str | None = Field(default=None, description="Collector number of that printing.")
    quantity: StrictInt | None = Field(default=None, ge=1, description="Copies (default 1).")
    finish: Finish | None = Field(default=None, description="nonfoil (default), foil or etched.")
    condition: Annotated[Literal["NM", "LP", "MP", "HP", "DMG"], BeforeValidator(_upper)] | None = Field(
        default=None, description="Card condition."
    )


class CollectionRemove(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: StrictInt | str | None = Field(
        default=None, description="The collection row id from list_collection."
    )
    name: str | None = Field(default=None, description="Or the card's name: every row of it.")
    quantity: StrictInt | None = Field(
        default=None, ge=1, description="Copies to remove; omit to remove every copy."
    )


class DeckDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, description="New deck name, 1 to 200 characters.")
    description: str | None = Field(
        default=None, description="New description (plain text, up to 20000 characters)."
    )
    deck_format: DeckFormat | None = Field(default=None, description="New format.")
    edh_bracket: StrictInt | None = Field(
        default=None, ge=1, le=5, description="Bracket 1 to 5; null clears it."
    )
    private: StrictBool | None = Field(default=None, description="Private deck.")
    unlisted: StrictBool | None = Field(default=None, description="Unlisted deck.")
    folder: str | None = Field(
        default=None,
        description='Move the deck into this existing folder (its name; "" or "top" for the top level).',
    )
    add_tags: list[str] | None = Field(default=None, description="Tags to put on the deck (names).")
    remove_tags: list[str] | None = Field(default=None, description="Tags to take off the deck (names).")
    cover: str | None = Field(default=None, description="A card in the deck whose art becomes the cover.")


class Mulligan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_sources: StrictInt | None = Field(
        default=None, ge=0, le=7, description="Keep a 7 only with at least this many sources (default 3)."
    )
    max_sources: StrictInt | None = Field(
        default=None, ge=0, le=7, description="...and at most this many (default 5)."
    )
    lands_only: StrictBool | None = Field(
        default=None, description="Count only lands as sources, not mana rocks."
    )
    free_first: StrictBool | None = Field(default=None, description="Free first mulligan (default true).")
    min_real_lands: StrictInt | None = Field(
        default=None, ge=0, le=7, description="Minimum lands in a kept 7 (default 2)."
    )


class _SimShared(BaseModel):
    model_config = ConfigDict(extra="forbid")
    annotations: list[dict[str, Any]] | None = Field(
        default=None,
        description="Card annotations as goldfish_annotate writes them, for cards the engine cannot model.",
    )
    combos: list[list[str] | dict[str, Any]] | None = Field(
        default=None,
        description='Declared combos: each a list of card names, or {"cards": [...], "wins": true}.',
    )
    seed: StrictInt | None = Field(default=None, description="Random seed (default 42), for repeatable runs.")
    until_turn: StrictInt | None = Field(
        default=None, ge=1, le=30, description="Simulate through this turn (default 10)."
    )


class RunOptions(_SimShared):
    opponents: StrictInt | None = Field(
        default=None, ge=1, le=5, description="Goldfish opponents (default 1)."
    )
    mulligan: Mulligan | None = Field(default=None, description="Mulligan rules.")


class AbOptions(_SimShared):
    annotations_a: list[dict[str, Any]] | None = Field(
        default=None, description="Annotations for deck a only."
    )
    annotations_b: list[dict[str, Any]] | None = Field(
        default=None, description="Annotations for deck b only."
    )
    allow_different_commanders: StrictBool | None = Field(
        default=None,
        description="Compare decks with different commanders anyway (the deltas are then confounded).",
    )


def dumped(model: BaseModel | dict[str, Any] | str | None, *, unset: bool = False) -> Any:
    """A model as the plain dict the services take: unset fields dropped (``unset``) or None
    fields dropped (default). Dicts and strings pass through."""
    if isinstance(model, BaseModel):
        return model.model_dump(exclude_unset=True) if unset else model.model_dump(exclude_none=True)
    return model


def deck_change_for_service(change: Any) -> Any:
    """A DeckChange in the shape DeckService.parse_changes reads (card_name; finish normal)."""
    d = card_aliases(dumped(change))
    if not isinstance(d, dict):
        return d
    out = dict(d)
    if isinstance(change, BaseModel) and "mana_value" in change.model_fields_set:
        out["mana_value"] = getattr(change, "mana_value", None)  # null is meaningful: it clears
    if "name" in out:
        out["card_name"] = out.pop("name")
    if out.get("finish") == "nonfoil":
        out["finish"] = "normal"
    return out


def change_for_assistant(change: Any) -> Any:
    """A deck change the gateway built (scan sessions, resolve_cards) in the documented spelling:
    name, set_code, collector_number, finish."""
    if not isinstance(change, dict):
        return change
    out = dict(change)
    if "card_name" in out:
        out["name"] = out.pop("card_name")
    foil = out.pop("foil", None)
    if isinstance(foil, bool) and "finish" not in out:
        out["finish"] = "foil" if foil else "nonfoil"
    if out.get("finish") == "normal":
        out["finish"] = "nonfoil"
    return out


def new_deck_card_for_service(card: Any) -> Any:
    """A NewDeckCard in the shape normalise_cards reads (card_name; finish foil or etched)."""
    d = card_aliases(dumped(card))
    if not isinstance(d, dict):
        return d
    out = dict(d)
    if "name" in out:
        out["card_name"] = out.pop("name")
    finish = out.pop("finish", None)
    if finish in ("foil", "etched"):
        out["finish"] = finish
    return out


__all__ = [
    "AbOptions",
    "CollectionAdd",
    "CollectionRemove",
    "DeckChange",
    "DeckDetails",
    "FINISH_ALIASES",
    "NewDeckCard",
    "RunOptions",
    "card_aliases",
    "change_for_assistant",
    "deck_change_for_service",
    "drop_none",
    "dumped",
    "compact_schema",
    "flatten_params",
    "inline_refs",
    "new_deck_card_for_service",
]
