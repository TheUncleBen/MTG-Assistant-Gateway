"""The human-facing view of a stored deck report (``/history/reports/{id}``) and its exports.

The research service answers a goldfish run as Markdown ("# Goldfish run", "## Summary",
"## Metrics" with a JSON block, "## Honesty report") and a validation as "# Validation:
PASSED" or "ISSUES FOUND" with the problems under it. This module reads those texts into plain
data (never raising on odd or partial input: a report of a failed run is still a page) and
draws the page from that data: summary tiles with their confidence intervals, small inline-SVG
charts coloured by the theme (no script), a plain-language account of what the simulation
could not model, the validation verdict, and the raw output folded away. The same data makes
the Markdown and the self-contained HTML exports.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import card_models
from .theme import ICON_DATA_URL, icon, time_html, time_text
from .theme import esc as _esc

# The simulator's honesty classes, in plain words.
HONESTY_CLASSES = {
    "interaction_removal": "Removal and other interaction",
    "interaction_counter": "Counterspells",
    "protection": "Protection",
    "unmodeled_other": "Other effects the simulator does not model",
}
# Its standing approximations (the "v1 model shortcuts"), in plain words.
APPROXIMATIONS = {
    "any-color": "Treated as producing any color",
    "creature-producer": "Treated as a plain creature producer",
    "equipment-extra": "Equipment beyond the first is ignored",
    "fetch": "Fetch lands find a basic at once",
    "hybrid-cost": "Hybrid costs paid with either color",
    "residual-text": "Extra card text ignored",
    "bounce-land": "Bounce lands enter untapped",
    "filter-ignored": "Mana filtering ignored",
    "ramp-widened": "Ramp counted as finding a land",
}
# The milestones the simulator tracks as "reached by turn N", in plain words and plot order. A
# 60-card game (no commander, 20 life) has no commander milestones and its kill is 20 damage.
COMMANDER_MILESTONES = ("commander_cast", "cmdr21")
MILESTONES = (
    ("commander_cast", "Commander cast"),
    ("table_lethal", "Lethal power on board"),
    ("kill", "40 damage dealt"),
    ("cmdr21", "21 commander damage"),
)
MAX_RAW = 60_000


def front_face(name: Any) -> str:
    """The front face of a double-faced card name ("A // B" -> "A"), other names unchanged."""
    text = str(name or "").strip()
    return text.split(" // ")[0].strip() or text


# The simulator's low-impact heading adds "(only this group supports 'the sim agrees this card
# underperforms')": a goldfish game has no opponent, so it supports no such claim (QA-005).
UNDERPERFORMS = re.compile(r"\s*\(only this group supports 'the sim agrees this card underperforms'\)")


def plain_words(text: str) -> str:
    """The simulator's text without the assistant tool names it mentions (``goldfish_report``,
    ``goldfish_annotate``): the page speaks to a person, not to an assistant."""
    text = re.sub(r'Full report: goldfish_report\("([^"]*)"\)', r"Run id: \1", text)
    text = re.sub(r"\s*\(candidates for annotation; see goldfish_annotate\)", " (could be annotated)", text)
    text = re.sub(r"goldfish_report\((\"?)([^)\"]*)\1\)", r"run \2", text)
    text = UNDERPERFORMS.sub("", text)
    return re.sub(r"\bgoldfish_([a-z_]+)", lambda m: m.group(1).replace("_", " "), text)


# -- reading the simulator's text --------------------------------------------------
def split_sections(text: str) -> tuple[str, dict[str, str]]:
    """The text before the first ``## `` heading and each ``## `` section's body, keyed by the
    heading in lower case (a later duplicate heading wins)."""
    preamble: list[str] = []
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip().lower()
            buf = []
        elif current is None:
            preamble.append(line)
        else:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return "\n".join(preamble).strip(), sections


def _json_block(body: str) -> Any:
    """The JSON inside a fenced block (or the whole body when it is bare JSON), else None."""
    m = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", body, re.S)
    raw = m.group(1) if m else body
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _bullets(body: str) -> list[str]:
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ")):
            s = s[2:].strip()
        if s:
            out.append(s)
    return out


def _int(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _rate(value: Any) -> dict[str, Any] | None:
    """A simulator rate ``{"value": 0.57, "ci": [lo, hi]}`` (or a bare number) as a dict with
    floats, else None."""
    if isinstance(value, dict):
        v = _float(value.get("value"))
        if v is None:
            return None
        ci = value.get("ci")
        lo = hi = None
        if isinstance(ci, (list, tuple)) and len(ci) == 2:
            lo, hi = _float(ci[0]), _float(ci[1])
        return {"value": v, "lo": lo, "hi": hi}
    v = _float(value)
    return {"value": v, "lo": None, "hi": None} if v is not None else None


def _cards(items: Any) -> list[dict[str, Any]]:
    """Honesty entries (``{"name", "drawn_pct"}`` dicts or bare names) as name + drawn share."""
    out = []
    if not isinstance(items, list):
        return out
    for it in items:
        if isinstance(it, dict):
            name = it.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            drawn = _rate(it.get("drawn_pct"))
            out.append({"name": name.strip(), "drawn": drawn["value"] if drawn else None})
        elif isinstance(it, str) and it.strip():
            out.append({"name": it.strip(), "drawn": None})
    return out


def _honesty_from_json(h: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"out_of_scope": [], "unrecognized": [], "low_impact": [], "approximations": []}
    if not isinstance(h, dict):
        return out
    oos = h.get("out_of_scope")
    if isinstance(oos, dict):
        for key, items in oos.items():
            cards = _cards(items)
            if cards:
                out["out_of_scope"].append(
                    {"kind": str(key), "label": _class_label(str(key)), "cards": cards}
                )
    out["unrecognized"] = _cards(h.get("unrecognized"))
    out["low_impact"] = [c["name"] for c in _cards(h.get("modeled_low_impact"))]
    return out


def _class_label(key: str) -> str:
    return HONESTY_CLASSES.get(key) or key.replace("_", " ").capitalize()


_DRAWN = re.compile(r"(.+?) \(drawn (\d+(?:\.\d+)?)%\)(?:, |$)")


def _honesty_from_text(body: str) -> dict[str, Any]:
    """The honesty report read from its text, for runs whose JSON lacks it (or is unreadable)."""
    out: dict[str, Any] = {"out_of_scope": [], "unrecognized": [], "low_impact": [], "approximations": []}
    group: str | None = None
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if not s.startswith("- "):
            if low.startswith("out of scope"):
                group = "out_of_scope"
            elif low.startswith("unrecognized") or low.startswith("unrecognised"):
                group = "unrecognized"
            elif low.startswith("modeled, low-impact") or low.startswith("modelled, low-impact"):
                group = "low_impact"
            elif low.startswith("standing approximations"):
                group = "approximations"
            else:
                group = None
            continue
        s = s[2:].strip()
        if group == "out_of_scope":
            key, _, rest = s.partition(": ")
            cards = [
                {"name": m.group(1).strip(), "drawn": float(m.group(2)) / 100} for m in _DRAWN.finditer(rest)
            ]
            if cards:
                out["out_of_scope"].append({"kind": key, "label": _class_label(key), "cards": cards})
        elif group == "unrecognized":
            out["unrecognized"].extend(
                {"name": m.group(1).strip(), "drawn": float(m.group(2)) / 100} for m in _DRAWN.finditer(s)
            )
        elif group == "low_impact":
            out["low_impact"].extend(n.strip() for n in s.split(", ") if n.strip())
        elif group == "approximations":
            key, _, rest = s.partition(": ")
            if rest:
                out["approximations"].append(
                    {
                        "kind": key,
                        "label": APPROXIMATIONS.get(key) or key.replace("-", " ").capitalize(),
                        "cards": rest,
                    }
                )
    return out


def _quantities(value: Any) -> dict[str, int]:
    """The stored copies per card, keyed by the front face in lower case."""
    if not isinstance(value, dict):
        return {}
    return {
        front_face(k).casefold(): v
        for k, v in value.items()
        if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0
    }


def parse_goldfish(block: Any, *, deck_size: int | None = None) -> dict[str, Any] | None:
    """The simulation block of a stored report (``{"tool","ok","data"?,"text"?}``) as plain
    data. None for no block; never raises. ``deck_size`` (the deck's card count from the
    statistics) backs the share-of-deck figures when the text does not say the deck size."""
    if not isinstance(block, dict):
        return None
    text = block.get("text")
    text = text if isinstance(text, str) else ""
    data = block.get("data") if isinstance(block.get("data"), dict) else None
    ok = bool(block.get("ok"))
    preamble, sections = split_sections(text)
    out: dict[str, Any] = {
        "ok": ok,
        "commander": None,
        "format": None,  # "constructed" for a 60-card game
        "deck_size": deck_size,
        "games": None,
        "seed": None,
        "until_turn": None,
        "run_id": None,
        "warnings": [],
        "summary": [],
        "metrics": {},
        "honesty": {"out_of_scope": [], "unrecognized": [], "low_impact": [], "approximations": []},
        "message": None,
        "raw": plain_words(text[:MAX_RAW]),
        # copies of each card in the simulated deck (reports._keep_quantities; none before 0.7.30)
        "quantities": _quantities(block.get("quantities")),
    }
    for line in preamble.splitlines():
        s = line.strip()
        if s.lower().startswith("warning"):
            # "Warning — not recognized by Scryfall, skipped: A // B, C": one sentence, back faces
            # dropped (names may hold commas, so the list is not split into cards here).
            body = re.sub(r"^warning\s*[—:-]\s*", "", s, flags=re.I)
            body = re.sub(r" // [^,]*", "", body)
            out["warnings"].append(body[:1].upper() + body[1:])
            continue
        for part in s.split("|"):
            part = part.strip()
            if not part or part.startswith("#"):
                continue
            key, _, val = part.partition(":")
            key, val = key.strip().lower(), val.strip()
            if key == "deck" and val:
                out["deck_size"] = _int(val.split()[0]) or out["deck_size"]
            elif key == "commander" and val:
                out["commander"] = val
            elif key == "format" and val:
                # "Format: 60-card (no commander, 20 life, on the play)"
                out["format"] = "constructed" if val.startswith("60-card") else val
            elif key == "run_id" and val:
                out["run_id"] = val
            else:
                m = re.search(r"(\d+)\s+games", part)
                if m:
                    out["games"] = _int(m.group(1))
                m = re.search(r"seed\s+(-?\d+)", part)
                if m:
                    out["seed"] = _int(m.group(1))
                m = re.search(r"through turn\s+(\d+)", part)
                if m:
                    out["until_turn"] = _int(m.group(1))
    out["summary"] = _bullets(sections.get("summary", ""))
    metrics = _json_block(sections["metrics"]) if "metrics" in sections else None
    if not isinstance(metrics, dict) and data is not None:
        metrics = data
    if isinstance(metrics, dict):
        for key in ("n", "seed", "until_turn", "run_id"):
            if key in metrics and metrics[key] is not None:
                target = "games" if key == "n" else key
                out[target] = out[target] if out[target] is not None else metrics[key]
        inner = metrics.get("metrics")
        out["metrics"] = inner if isinstance(inner, dict) else metrics
    m = out["metrics"]
    if isinstance(m.get("n"), int) and out["games"] is None:
        out["games"] = m["n"]
    if isinstance(m.get("until_turn"), int) and out["until_turn"] is None:
        out["until_turn"] = m["until_turn"]
    honesty = _honesty_from_json(m.get("honesty"))
    from_text = _honesty_from_text(sections.get("honesty report", ""))
    if not (honesty["out_of_scope"] or honesty["unrecognized"] or honesty["low_impact"]):
        honesty.update({k: from_text[k] for k in ("out_of_scope", "unrecognized", "low_impact")})
    honesty["approximations"] = from_text["approximations"]
    if out["quantities"]:
        for card in [*honesty["unrecognized"], *(c for grp in honesty["out_of_scope"] for c in grp["cards"])]:
            card["copies"] = out["quantities"].get(front_face(card["name"]).casefold())
    out["honesty"] = honesty
    if not out["metrics"] and not out["summary"]:
        # A refusal or an outage: the simulator's sentence is the whole story.
        out["message"] = plain_words(preamble or text)[:600] or (
            "The solo test did not run for this report." if not ok else ""
        )
    return out


def parse_validation(block: Any) -> dict[str, Any] | None:
    """The validation block as a verdict (``passed``, ``issues``, ``failed`` or None when the text
    gives none), a headline and the lines under it. None for no block; never raises."""
    if not isinstance(block, dict):
        return None
    text = block.get("text")
    if not isinstance(text, str) or not text.strip():
        data = block.get("data")
        text = json.dumps(data, indent=1) if data is not None else ""
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
    verdict: str | None = None
    headline = ""
    rest: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if not headline and s.startswith("#"):
            headline = s.lstrip("# ").strip()
            low = headline.lower()
            if "passed" in low or low.endswith("ok"):
                verdict = "passed"
            elif "issue" in low or "fail" in low or "problem" in low:
                verdict = "issues"
            continue
        if s.startswith("#"):  # a later section ("## Gateway checks") reads as a plain line
            s = s.lstrip("# ").strip()
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        if s.startswith(("- ", "* ")):
            s = s[2:].strip()
        if re.fullmatch(r"[^:]+ // [^:]+", s):
            s = front_face(s)
        rest.append(s)
    if not headline and rest:
        headline, rest = rest[0], rest[1:]
    if not block.get("ok"):
        verdict = "failed"
    return {
        "verdict": verdict,
        "headline": plain_words(headline),
        "lines": [plain_words(x) for x in rest[:200]],
        "raw": plain_words(text[:MAX_RAW]),
    }


# -- numbers ---------------------------------------------------------------------
def _pct(v: float | None, digits: int = 0) -> str:
    return "–" if v is None else f"{100 * v:.{digits}f}%"


def _num(v: Any, digits: int = 1) -> str:
    f = _float(v)
    if f is None:
        return "–"
    return f"{f:.{digits}f}" if digits else f"{round(f)}"


def _ci_text(rate: dict[str, Any] | None) -> str:
    if not rate or rate.get("lo") is None or rate.get("hi") is None:
        return ""
    return f"{100 * rate['lo']:.0f}–{100 * rate['hi']:.0f}%"


def _cumulative(hist: Any, turns: int) -> list[float | None]:
    """Share of games that reached a milestone by each turn 1..turns, from the simulator's
    turn histogram (counts of games by the turn they first reached it)."""
    if not isinstance(hist, dict):
        return []
    counts: dict[int, int] = {}
    for k, v in hist.items():
        t, n = _int(k), _int(v)
        if t is not None and n is not None:
            counts[t] = counts.get(t, 0) + n
    return [sum(n for t, n in counts.items() if t <= turn) for turn in range(1, turns + 1)]  # type: ignore[misc]


def kill_damage(g: dict[str, Any]) -> int:
    """The damage the simulation's kill stands for: the opponent's starting life."""
    return 20 if g.get("format") == "constructed" else 40


def tiles_for(g: dict[str, Any]) -> list[dict[str, Any]]:
    """The headline numbers: label, value, interval, note."""
    m = g.get("metrics") or {}
    turns = g.get("until_turn") or 10
    out: list[dict[str, Any]] = []
    cc = m.get("commander_cast") if isinstance(m.get("commander_cast"), dict) else {}
    reached = _rate(cc.get("reached_pct"))
    med = cc.get("median_among_reached")
    if (med is not None or reached) and g.get("format") != "constructed":
        out.append(
            {
                "label": "Commander cast (median turn)",
                "value": f"Turn {med}" if med is not None else "–",
                "rate": None,
                "note": f"cast in {_pct(reached['value'])} of games by turn {turns}" if reached else "",
            }
        )
    for key, label in (("kill", f"{kill_damage(g)} damage dealt"), ("table_lethal", "Lethal power on board")):
        d = m.get(key) if isinstance(m.get(key), dict) else {}
        r = _rate(d.get("reached_pct"))
        if r:
            med = d.get("median_among_reached")
            out.append(
                {
                    "label": f"{label} by turn {turns}",
                    "value": _pct(r["value"]),
                    "rate": r,
                    "note": f"median turn {med} when reached" if med is not None else "",
                }
            )
    dmg = m.get("damage") if isinstance(m.get("damage"), dict) else {}
    if _float(dmg.get("avg_total")) is not None:
        combat = (dmg.get("combat") or {}).get("avg_total") if isinstance(dmg.get("combat"), dict) else None
        out.append(
            {
                "label": f"Damage by turn {turns} (average)",
                "value": _num(dmg.get("avg_total")),
                "rate": None,
                "note": f"{_num(combat)} of it in combat" if combat is not None else "",
            }
        )
    mull = m.get("mulligans") if isinstance(m.get("mulligans"), dict) else {}
    r = _rate(mull.get("pct_mulliganed"))
    if r:
        out.append(
            {
                "label": "Games with a mulligan",
                "value": _pct(r["value"]),
                "rate": r,
                "note": f"kept {_num(mull.get('avg_kept_hand_size'))} cards with "
                f"{_num(mull.get('avg_kept_lands'))} lands",
            }
        )
    oc = m.get("on_curve") if isinstance(m.get("on_curve"), dict) else {}
    r = _rate(oc.get("pct_all_drops_through_t5"))
    if r:
        out.append(
            {"label": "Every land drop through turn 5", "value": _pct(r["value"]), "rate": r, "note": ""}
        )
    lands = oc.get("avg_lands_by_turn")
    if isinstance(lands, list) and len(lands) >= 5:
        expect = sum(f for f in (_float(x) for x in lands[:5]) if f is not None)
        mana = oc.get("avg_mana_by_turn")
        mana5 = _float(mana[4]) if isinstance(mana, list) and len(mana) >= 5 else None
        out.append(
            {
                "label": "Lands on turn 5 (average)",
                "value": _num(expect),
                "rate": None,
                "note": f"{_num(mana5)} mana available" if mana5 is not None else "",
            }
        )
    return out


def speed_numbers(block: Any) -> dict[str, Any]:
    """The simulation's speed numbers a deck's report trend follows (reports.TREND_METRICS):
    the commander's median cast turn, the median turn lethal damage (40, or 20 in a 60-card game)
    was dealt when it was, and the
    share of games (in percent) that dealt it by the last simulated turn (``until_turn``). Only
    what a successful simulation stored; a missing number is None, never guessed."""
    g = parse_goldfish(block)
    out: dict[str, Any] = {
        "commander_cast_median_turn": None,
        "kill_median_turn": None,
        "kill_pct": None,
        "until_turn": None,
    }
    if not g or not g["ok"]:
        return out
    m = g.get("metrics") or {}
    cc = m.get("commander_cast") if isinstance(m.get("commander_cast"), dict) else {}
    kill = m.get("kill") if isinstance(m.get("kill"), dict) else {}
    out["commander_cast_median_turn"] = _float(cc.get("median_among_reached"))
    out["kill_median_turn"] = _float(kill.get("median_among_reached"))
    rate = _rate(kill.get("reached_pct"))
    out["kill_pct"] = round(100 * rate["value"], 1) if rate else None
    out["until_turn"] = g.get("until_turn") if isinstance(g.get("until_turn"), int) else None
    return out


def goldfish_settings(block: Any) -> dict[str, Any]:
    """How a stored simulation was run, as its result says: games, seed, last simulated turn and
    whether it was a 60-card game (``constructed``). Only what a successful run stated; a missing
    value is None."""
    g = parse_goldfish(block)
    if not g or not g["ok"]:
        return {}
    return {
        "games": g["games"] if isinstance(g.get("games"), int) else None,
        "seed": g["seed"] if isinstance(g.get("seed"), int) else None,
        "until_turn": g["until_turn"] if isinstance(g.get("until_turn"), int) else None,
        "constructed": g.get("format") == "constructed",
    }


# -- drawing -----------------------------------------------------------------------
def _tile(t: dict[str, Any]) -> str:
    rate = t.get("rate")
    ci = ""
    if rate and rate.get("lo") is not None and rate.get("hi") is not None:
        lo, hi = max(0.0, rate["lo"]), min(1.0, rate["hi"])
        text = t.get("ci_text") or _ci_text(rate)  # the Forge card writes it as its table does
        ci = (
            f"<span class='ci' title='95% interval {_esc(text)}'>"
            f"<i style='left:{100 * lo:.1f}%;width:{max(1.0, 100 * (hi - lo)):.1f}%'></i></span>"
            f"<span>95% interval {_esc(text)}</span>"
        )
    note = f"<span>{_esc(t['note'])}</span>" if t.get("note") else ""
    return f"<div class='tile'><b>{_esc(t['value'])}</b><span>{_esc(t['label'])}</span>{ci}{note}</div>"


def svg_lines(series: list[tuple[str, list[float | None]]], *, turns: int, label: str) -> str:
    """A line chart of shares (0..1) by turn, one polyline per series, as inline SVG. The y axis
    is 0–100%, gridlines every 25%; colours come from the theme through the ``s1..s4`` classes."""
    series = [(name, pts) for name, pts in series if any(isinstance(p, (int, float)) for p in pts)]
    if not series or turns < 1:
        return ""
    w, h, left, top, right, bottom = 320, 170, 34, 8, 8, 24
    pw, ph = w - left - right, h - top - bottom
    step = pw / max(1, turns - 1)

    def x(i: int) -> float:
        return left + i * step

    def y(v: float) -> float:
        return top + ph - min(1.0, max(0.0, v)) * ph

    parts = [
        f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='{_esc(label)}' xmlns='http://www.w3.org/2000/svg'>"
    ]
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        yy = y(frac)
        parts.append(f"<line class='grid' x1='{left}' y1='{yy:.1f}' x2='{w - right}' y2='{yy:.1f}'/>")
        parts.append(f"<text x='{left - 4}' y='{yy + 3.5:.1f}' text-anchor='end'>{int(frac * 100)}%</text>")
    parts.append(f"<line class='axis' x1='{left}' y1='{y(0):.1f}' x2='{w - right}' y2='{y(0):.1f}'/>")
    every = 1 if turns <= 12 else 2
    for i in range(turns):
        if (i + 1) % every == 0 or i == 0:
            parts.append(f"<text x='{x(i):.1f}' y='{h - 8}' text-anchor='middle'>T{i + 1}</text>")
    for n, (name, pts) in enumerate(series[:4], start=1):
        coords = [f"{x(i):.1f},{y(p):.1f}" for i, p in enumerate(pts[:turns]) if isinstance(p, (int, float))]
        if len(coords) >= 2:
            parts.append(
                f"<polyline class='s{n}' points='{' '.join(coords)}'><title>{_esc(name)}</title></polyline>"
            )
        last = [(i, p) for i, p in enumerate(pts[:turns]) if isinstance(p, (int, float))]
        if last:
            i, p = last[-1]
            parts.append(f"<circle class='s{n}' cx='{x(i):.1f}' cy='{y(p):.1f}' r='3'/>")
    parts.append("</svg>")
    legend = "".join(
        f"<li><i class='s{n}'></i>{_esc(name)}</li>" for n, (name, _pts) in enumerate(series[:4], start=1)
    )
    return "".join(parts) + f"<ul class='legend'>{legend}</ul>"


def svg_columns(
    values: list[tuple[str, float | None]], *, label: str, unit: str = "", digits: int = 1
) -> str:
    """A column chart (one column per turn or bucket) as inline SVG, values printed above the
    columns; the column colour is the theme's orange through the ``col`` class."""
    vals = [(k, _float(v)) for k, v in values]
    if not vals or not any(v is not None for _k, v in vals):
        return ""
    top_v = max([v for _k, v in vals if v is not None] + [0.0]) or 1.0
    w, h, left, top, right, bottom = 320, 170, 8, 18, 8, 24
    pw, ph = w - left - right, h - top - bottom
    n = len(vals)
    slot = pw / n
    bar_w = max(4.0, slot * 0.62)
    parts = [
        f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='{_esc(label)}' xmlns='http://www.w3.org/2000/svg'>",
        f"<line class='axis' x1='{left}' y1='{top + ph}' x2='{w - right}' y2='{top + ph}'/>",
    ]
    every = 1 if n <= 12 else 2
    for i, (k, v) in enumerate(vals):
        cx = left + slot * i + slot / 2
        if v is not None:
            bh = max(1.0, v / top_v * ph) if v > 0 else 0.0
            parts.append(
                f"<rect class='col' x='{cx - bar_w / 2:.1f}' y='{top + ph - bh:.1f}' width='{bar_w:.1f}' "
                f"height='{bh:.1f}' rx='2'><title>{_esc(k)}: {_num(v, digits)}{_esc(unit)}</title></rect>"
            )
            if n <= 12 or i % every == 0:
                parts.append(
                    f"<text x='{cx:.1f}' y='{top + ph - bh - 4:.1f}' text-anchor='middle'>"
                    f"{_num(v, digits)}</text>"
                )
        if (i + 1) % every == 0 or i == 0 or n <= 12:
            parts.append(f"<text x='{cx:.1f}' y='{h - 8}' text-anchor='middle'>{_esc(k)}</text>")
    parts.append("</svg>")
    return "".join(parts)


def charts_for(g: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(title, blurb, svg) for each chart the metrics support."""
    m = g.get("metrics") or {}
    turns = g.get("until_turn") if isinstance(g.get("until_turn"), int) and g["until_turn"] > 0 else None
    games = g.get("games") if isinstance(g.get("games"), int) and g["games"] > 0 else None
    out: list[tuple[str, str, str]] = []
    if turns is None:
        for key in ("avg_mana_by_turn", "avg_lands_by_turn"):
            seq = (m.get("on_curve") or {}).get(key) if isinstance(m.get("on_curve"), dict) else None
            if isinstance(seq, list) and seq:
                turns = len(seq)
                break
    if turns and games:
        series = []
        for key, label in MILESTONES:
            if g.get("format") == "constructed" and key in COMMANDER_MILESTONES:
                continue
            if key == "kill":
                label = f"{kill_damage(g)} damage dealt"
            d = m.get(key) if isinstance(m.get(key), dict) else None
            if d and isinstance(d.get("histogram"), dict):
                counts = _cumulative(d["histogram"], turns)
                series.append((label, [c / games for c in counts]))  # type: ignore[operator]
        svg = svg_lines(series, turns=turns, label="Share of games that reached each milestone, by turn")
        if svg:
            out.append(
                (
                    "Reaching the milestones",
                    "Share of games in which each had happened by the end of a turn.",
                    svg,
                )
            )
    cc = m.get("commander_cast") if isinstance(m.get("commander_cast"), dict) else {}
    if isinstance(cc.get("histogram"), dict) and games:
        hist = {
            t: n for t, n in ((_int(k), _int(v)) for k, v in cc["histogram"].items()) if t and n is not None
        }
        if hist:
            first, last = min(hist), max(hist)
            cols = [(f"T{t}", 100 * hist.get(t, 0) / games) for t in range(first, last + 1)]
            svg = svg_columns(
                cols, label="Turn the commander was first cast, share of games", unit="%", digits=0
            )
            if svg:
                out.append(
                    ("When the commander comes down", "Share of games by the turn it was first cast.", svg)
                )
    oc = m.get("on_curve") if isinstance(m.get("on_curve"), dict) else {}
    mana = oc.get("avg_mana_by_turn")
    if isinstance(mana, list) and mana:
        cols = [(f"T{i + 1}", _float(v)) for i, v in enumerate(mana[: turns or len(mana)])]
        svg = svg_columns(cols, label="Average mana available by turn")
        if svg:
            out.append(
                ("Mana available", "Average mana the deck could spend on each turn (lands and rocks).", svg)
            )
    dmg = m.get("damage") if isinstance(m.get("damage"), dict) else {}
    by_turn = dmg.get("avg_by_turn")
    if isinstance(by_turn, list) and by_turn:
        cols = [(f"T{i + 1}", _float(v)) for i, v in enumerate(by_turn[: turns or len(by_turn)])]
        svg = svg_columns(cols, label="Average damage dealt per turn")
        if svg:
            out.append(("Damage per turn", "Average damage dealt to the opponent on each turn.", svg))
    return out


def _share(count: int, deck_size: int | None) -> str:
    if not deck_size or deck_size <= 0:
        return f"{count} card{'s' if count != 1 else ''}"
    return f"{count} of {deck_size} cards ({100 * count / deck_size:.0f}% of the deck)"


def _names(cards: list[dict[str, Any]]) -> str:
    items = []
    for c in cards:
        copies = f"{c['copies']}× " if isinstance(c.get("copies"), int) and c["copies"] > 1 else ""
        drawn = f" <i>drawn {_pct(c['drawn'])}</i>" if c.get("drawn") is not None else ""
        items.append(f"<li>{copies}{_esc(front_face(c['name']))}{drawn}</li>")
    return f"<ul class='names'>{''.join(items)}</ul>"


def _md_name(c: dict[str, Any]) -> str:
    copies = f"{c['copies']}× " if isinstance(c.get("copies"), int) and c["copies"] > 1 else ""
    return copies + front_face(c["name"])


def _copies(cards: list[dict[str, Any]]) -> int:
    """How many copies the listed cards are (each counts once without a stored quantity)."""
    return sum(c["copies"] if isinstance(c.get("copies"), int) else 1 for c in cards)


def coverage(g: dict[str, Any]) -> dict[str, Any] | None:
    """How much of the deck the simulator modeled, counted in copies: ``modeled``, the deck's cards
    less those it played without their effect and those out of its scope (set aside on purpose),
    and ``known``, those less only the ones played without their effect. The honesty report
    names each card once; the copies come from the quantities kept with the run (0.7.30). A
    60-card game stored before that has several copies per card and no quantities, so no figure."""
    h = g.get("honesty") or {}
    size = g.get("deck_size")
    if not isinstance(size, int) or size <= 0:
        return None
    if g.get("format") == "constructed" and not g.get("quantities"):
        return None
    blanks = _copies(h.get("unrecognized") or [])
    aside = _copies([c for grp in h.get("out_of_scope") or [] for c in grp["cards"]])
    modeled = max(0, size - blanks - aside)
    known = max(0, size - blanks)
    return {
        "modeled": modeled,
        "deck_size": size,
        "pct": round(100 * modeled / size),
        "known": known,
        "known_pct": round(100 * known / size),
    }


def coverage_text(g: dict[str, Any]) -> str:
    c = coverage(g)
    if c is None:
        return ""
    # both figures, as scripts/measure_recognition.py prints them (measured now on precons and test
    # decks, on real decks at deploy)
    return (
        f"Played with their effect: {c['modeled']} of {c['deck_size']} cards ({c['pct']}% of the deck). "
        f"Known: {c['known']} of {c['deck_size']} cards ({c['known_pct']}% of the deck), counting the "
        "cards played with their effect and those left out on purpose. The rest are listed below."
    )


# The cards the simulator plays without their effect (its "unrecognized" group), in plain words.
UNMODELED_HEADING = "Played without their effect"
# An assistant can describe only some of these (about 6 to 8 of Liesa's 33), so the
# text says so; the sentence on saving shows only when the gateway can save (the schema 14 table).
UNMODELED_TEXT = (
    "The solo test found these cards and their exact text, but has no description yet of what they "
    "do, so it drew and cast them without their effect. Whatever they would do in a real game is "
    "missing from the numbers above. Your assistant may be able to describe some of them from their "
    "exact text, in a form the solo test can play. Many cards need things the solo test does not "
    "model, such as opponents to interact with."
)
UNMODELED_SAVED_TEXT = (
    "The gateway can keep up to 200 of these descriptions for you once they pass its check, and your "
    "later solo tests then play those cards with them. Your saved descriptions play only in your own "
    "solo tests."
)
KEPT_HEADING = "Played with saved card descriptions"
KEPT_TEXT = (
    "Your assistant described each of these cards in a form the solo test can play, and the gateway "
    "checked its format and saved it. This run played these cards with those descriptions."
)


def kept_models(r: dict[str, Any]) -> list[dict[str, Any]]:
    """The kept card models a report's solo test played: card, model hash, when it last
    changed; only the member's own play. None while kept models
    are switched off, even for a report made while they were on."""
    if not card_models.SWITCHED_ON:
        return []
    block = r.get("goldfish") if isinstance(r.get("goldfish"), dict) else {}
    used = block.get("kept_models") if isinstance(block.get("kept_models"), list) else []
    return [u for u in used if isinstance(u, dict) and isinstance(u.get("name"), str)]


def _kept_who(u: dict[str, Any]) -> str:
    # every saved description is the member's own, so only its date is said
    when = time_text(u.get("changed_at"))[:10]
    return f"last changed {when}" if when else ""


REMOVE_MODEL_LABEL = "Remove"
# What the report page says after a Remove (?models=…)
MODEL_NOTICES = {
    "removed": "The saved description was removed. Later solo tests play that card without its effect "
    "again, until a new description is saved.",
    "gone": "That saved description was already removed, or an admin removed it.",
    "error": "The saved description could not be removed. Try again later.",
}


# Put before a Play more games run's reason when its games did not start (the report page and the
# play_more_games tool).
MORE_DID_NOT_START = "The extra games did not start. "


def more_games_html(f: Any, csrf: str, report_id: str) -> str:
    """The Play more games button under the Forge card, when the report can take more games (the
    Forge view's ``more_games``). Always present as an empty holder, so forge-live.js can fill it in
    once a run ends."""
    games = f.get("more_games") if isinstance(f, dict) else None
    if not (isinstance(games, int) and games > 0 and csrf and report_id):
        return "<div id='forge-more'></div>"
    word = "game" if games == 1 else "games"
    other = f.get("more_with")
    if isinstance(other, str) and other:  # a comparison: both decks get as many
        line = (
            f"Plays {games} more Forge {word} for this deck and {games} for {_esc(other)}, the other deck "
            "of this comparison, against the same opponents, so the two stay even. Each report's win "
            "rates and their 95% intervals then count every game."
        )
    else:
        line = (
            f"Plays {games} more Forge {word} against the same opponents and adds "
            f"{'it' if games == 1 else 'them'} to the results above, so the win rates and their 95% "
            "intervals count every game."
        )
    return (
        "<div id='forge-more' class='card more-games'>"
        f"<form method='post' action='/history/reports/{_esc(report_id)}/forge/more'>"
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        f"<button type='submit' class='btn'>{icon('play')} Play more games</button>"
        "</form>"
        f"<p class='muted small'>{line}</p>"
        "</div>"
    )


def _remove_form(u: dict[str, Any], csrf: str, report_id: str) -> str:
    """The adder's Remove button for a saved description."""
    card = front_face(u["name"])
    return (
        f"<form method='post' action='/history/reports/{_esc(report_id)}/models/remove' class='inline'>"
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        f"<input type='hidden' name='card' value='{_esc(u['name'])}'>"
        f"<button type='submit' class='btn small' aria-label='Remove the saved description of {_esc(card)}'>"
        f"{REMOVE_MODEL_LABEL}</button></form>"
    )


def kept_models_html(
    used: list[dict[str, Any]],
    *,
    removable: frozenset[str] = frozenset(),
    csrf: str = "",
    report_id: str = "",
) -> str:
    """The saved descriptions this run played. ``removable`` holds the cards (front face, case
    aside) whose saved description is now the member's own: those get a Remove button."""
    if not used:
        return ""
    items = "".join(
        f"<li>{_esc(front_face(u['name']))}"
        + (f" <i>{_esc(_kept_who(u))}</i>" if _kept_who(u) else "")
        + (
            " " + _remove_form(u, csrf, report_id)
            if csrf and report_id and front_face(u["name"]).casefold() in removable
            else ""
        )
        + "</li>"
        for u in sorted(used, key=lambda u: u["name"].casefold())
    )
    return (
        f"<div class='kept'><h3>{KEPT_HEADING} <small>{len(used)}</small></h3>"
        f"<p class='muted small'>{KEPT_TEXT}</p><ul class='names'>{items}</ul></div>"
    )


ASK_LABEL = "Copy a request for your assistant"


def ask_prompt(deck_name: str, deck_url: str, cards: list[dict[str, Any]], *, models: bool = False) -> str:
    """What the member pastes into Claude or ChatGPT to have these cards modeled (the same words
    for both): the assistant reads each card's exact text and passes its models to a new run.
    ``models``: the gateway keeps card models, so the request asks which it kept."""
    names = ", ".join(front_face(c["name"]) for c in cards)
    # the last clause only while kept models are switched on
    told = (
        ", which cards you could not describe, and which models the gateway kept, if any."
        if models
        else " and which cards you could not describe."
    )
    return (
        f'In the solo test (goldfish simulation) of my deck "{deck_name}" ({deck_url}), these cards '
        f"are played without their effect: {names}. Use the gateway's goldfish annotation tool on the "
        "deck to see what each one needs, and write each model only from the card's exact Oracle text "
        "(the gateway's card text and rules tools), never from memory. Then run a new deck report for "
        f"the deck with your models as its annotations, and tell me what changed{told}"
    )


def ask_html(r: dict[str, Any], cards: list[dict[str, Any]]) -> str:
    """The copy button and its hidden text for the request above (export.js copies it)."""
    if not cards:
        return ""
    g = r.get("goldfish") if isinstance(r.get("goldfish"), dict) else {}
    models = card_models.SWITCHED_ON and bool(g.get("models_saved"))
    text = ask_prompt(
        str(r.get("deck_name") or r.get("deck_id") or ""), str(r.get("deck_url") or ""), cards, models=models
    )
    return (
        "<div class='ask'><button type='button' class='btn small copybtn' data-copy='ask-model'>"
        f"{_esc(ASK_LABEL)}</button><span id='ask-model-status' class='small muted' role='status' "
        "aria-live='polite'></span>"
        f"<textarea id='ask-model' class='sr-only' readonly aria-label='{_esc(ASK_LABEL)}'>{_esc(text)}"
        "</textarea></div>"
    )


# The 300 automatic games are the "solo test"; "Goldfish" is the page where the member
# plays a hand. Forge's games always seat bundled precons (forge.start_run).
SOLO_NAME = "Solo test"
FORGE_HEADING = "Forge games (against precons)"


def solo_heading(g: dict[str, Any] | None) -> str:
    """The solo test's heading, with its game count when the run says it."""
    games = (g or {}).get("games")
    if isinstance(games, int) and not isinstance(games, bool) and games > 0:
        return f"{SOLO_NAME} ({games} games, no opponents)"
    return f"{SOLO_NAME} (no opponents)"


def honesty_html(g: dict[str, Any], ask: str = "") -> str:
    """The "what the simulation could not model" panel body, or '' when there is nothing to say.
    ``ask`` is the copy-a-request block (ask_html) the signed-in page puts under the cards played
    without their effect."""
    h = g.get("honesty") or {}
    size = g.get("deck_size")
    parts: list[str] = []
    if (h.get("unrecognized") or h.get("out_of_scope")) and coverage_text(g):
        parts.append(f"<p class='b coverage'>{_esc(coverage_text(g))}</p>")
    unrec = h.get("unrecognized") or []
    if unrec:
        # The simulator found these cards (their exact text comes from Scryfall; a name it cannot
        # find is a warning above); "unrecognized" is its word for "no effect model yet".
        parts.append(
            f"<div class='group unmodeled'><h3>{UNMODELED_HEADING} <small>"
            f"{_esc(_share(_copies(unrec), size))}</small></h3>"
            f"<p>{UNMODELED_TEXT}"
            + (f" {UNMODELED_SAVED_TEXT}" if g.get("models_saved") and card_models.SWITCHED_ON else "")
            + "</p>"
            + _names(unrec)
            + ask
            + "</div>"
        )
    oos = h.get("out_of_scope") or []
    if oos:
        total = _copies([c for grp in oos for c in grp["cards"]])
        groups = "".join(
            f"<h4>{_esc(grp['label'])} <small>{_copies(grp['cards'])}</small></h4>" + _names(grp["cards"])
            for grp in oos
        )
        parts.append(
            f"<div class='group'><h3>Left out on purpose <small>{_esc(_share(total, size))}</small></h3>"
            "<p>The simulator plays against an opponent who does nothing, so removal, counterspells and "
            "protection have nothing to act on. These cards sat in hand.</p>" + groups + "</div>"
        )
    low = h.get("low_impact") or []
    if low:
        parts.append(
            "<div class='group'><h3>Played with their effect but never mattered "
            f"<small>{_esc(_share(len(low), size))}</small></h3>"
            "<p>These cards were understood, drawn and played, and nothing they do changed a solo test game. "
            "A solo test game has no opponent and no interaction, so this says nothing about how the card "
            "does at a real table.</p>" + _names([{"name": n} for n in low]) + "</div>"
        )
    approx = h.get("approximations") or []
    if approx:
        rows = "".join(
            f"<dt>{_esc(a['label'])}</dt><dd>{_esc(a['cards'])}</dd>" for a in approx if a.get("cards")
        )
        parts.append(
            "<div class='group'><h3>Shortcuts the model takes</h3>"
            "<p>Simplifications applied to these cards; they play a little better or worse than in a real "
            "game.</p>"
            f"<dl class='meta approx'>{rows}</dl></div>"
        )
    return "".join(parts)


def _validation_badge(v: dict[str, Any] | None) -> str:
    if v is None:
        return ""
    return {
        "passed": "<span class='badge ok'>passed</span>",
        "issues": "<span class='badge warn'>issues found</span>",
        "failed": "<span class='badge danger'>not run</span>",
    }.get(v["verdict"] or "", "<span class='badge'>checked</span>")


def validation_html(v: dict[str, Any] | None) -> str:
    if v is None:
        return "<p class='muted'>The decklist was not validated: the research service was not configured.</p>"
    body = f"<p class='b'>{_esc(v['headline'])}</p>" if v.get("headline") else ""
    if v["lines"]:
        body += "<ul class='vlines'>" + "".join(f"<li>{_esc(ln)}</li>" for ln in v["lines"]) + "</ul>"
    return body or "<p class='muted'>No details.</p>"


def _when(ts: Any) -> str:
    return time_html(ts)


def _triggers_html(g: dict[str, Any]) -> str:
    m = g.get("metrics") or {}
    fires = m.get("trigger_fires") if isinstance(m.get("trigger_fires"), dict) else {}
    rows = []
    for key, rate in sorted(fires.items(), key=lambda kv: -(_float(kv[1]) or 0)):
        name, _, rest = str(key).partition("|")
        what = rest.replace("|", " → ").replace("_", " ")
        rows.append(
            f"<tr><td>{_esc(front_face(name))}</td><td class='muted'>{_esc(what)}</td>"
            f"<td>{_num(rate, 2)}</td></tr>"
        )
    combos = m.get("combos") if isinstance(m.get("combos"), list) else []
    out = ""
    if rows:
        out += (
            "<h3>Cards whose effect fired</h3><p class='muted small'>Only annotated cards fire; the number "
            "is "
            "fires per game.</p><table class='qty trig'><thead><tr><th>Card</th><th>Effect</th>"
            f"<th>Per game</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )
    if combos:
        out += (
            "<h3>Combos</h3><ul>"
            + "".join(f"<li>{_esc(json.dumps(c) if not isinstance(c, str) else c)}</li>" for c in combos[:20])
            + "</ul>"
        )
    return out


FORGE_STATE_TEXT = {
    "queued": "Waiting for Forge.",
    "waiting": "Waiting for your earlier Forge run to finish.",
    "running": "Games are being played now.",
    "failed": "The games could not run.",
    "timeout": "The games ran out of time before finishing.",
    "cancelled": "The games were stopped.",
    "lost": "Forge lost this run.",
    "skipped": "No Forge games for this report.",
    "busy": "Forge is busy with other runs. This one waits in the queue.",
}
# A report without Forge games (Forge off, or left out of this report) says so where Forge's games
# would be (reports.PREFLIGHT_ONLY).
PREFLIGHT_ONLY = "Preflight only (no rules engine)"
# The overall status (reports.report_status) in words, for the page head and the Markdown.
STATUS_TEXT = {
    "complete": "complete",
    "running": "running: Forge games are going",
    "queued": "queued: Forge games are waiting to start",
    "partial": "partial: some parts did not run or did not finish",
    "failed": "failed: nothing beyond the deck statistics came back",
}


# How a game ended (forge.summarise "ended") and how a seat lost (forge.LOSS_KINDS), in words.
ENDED_TEXT = {
    "won": "Won by a player",
    "draw": "Draw",
    "time_limit": "Stopped at the time limit (counted as a draw)",
    "unread": "Result not read",
}
LOSS_TEXT = {
    "life": "life total",
    "commander damage": "commander damage",
    "poison": "poison",
    "empty library": "drew from an empty library",
    "conceded": "conceded",
    "other": "another way",
}


def _seconds_text(seconds: Any) -> str:
    if _float(seconds) is None:
        return "–"
    seconds = float(seconds)
    # a number keeps its unit on a phone's narrow tile (no space to wrap at)
    if seconds < 90:
        return f"{seconds:g}\u00a0s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}\u00a0min {rest:02d}\u00a0s"


def _losses_text(losses: Any) -> str:
    if not isinstance(losses, dict) or not losses:
        return ""
    parts = [
        f"{LOSS_TEXT.get(k, str(k))} {v}"
        for k, v in sorted(losses.items(), key=lambda kv: (-(_int(kv[1]) or 0), str(kv[0])))
    ]
    return ", ".join(parts)


def forge_results_html(f: dict[str, Any]) -> str:
    """Forge's games laid out: headline tiles, one row per seat with its wins, win rate, 95%
    interval and how it lost, how the games ended, and each game in a folded table. '' before any
    game has finished."""
    res = f.get("result") if isinstance(f.get("result"), dict) else {}
    seats = [s for s in res.get("seats") or [] if isinstance(s, dict)]
    games = _int(res.get("games")) or 0
    if not games or not seats:
        return ""
    mine = seats[0]
    low, high = (mine.get("win_rate_95") or [None, None])[:2]
    tiles = [
        {"label": "Games played", "value": f"{games} of {_int(res.get('games_requested')) or games}"},
        {
            "label": "Your deck won",
            "value": _pct(_float(mine.get("win_rate"))),
            "rate": {"lo": _float(low), "hi": _float(high)},
            "ci_text": _interval([low, high]),
        },
        # runs stored before 0.7.30 have only the average, and say so
        {"label": "Median game time", "value": _seconds_text(res["median_game_seconds"])}
        if res.get("median_game_seconds") is not None
        else {"label": "Average game time", "value": _seconds_text(res.get("average_game_seconds"))},
    ]
    if res.get("median_turn") is not None:
        span = res.get("turns_range") if isinstance(res.get("turns_range"), list) else []
        note = f"from turn {span[0]} to {span[1]}" if len(span) == 2 else ""
        tiles.append(
            {"label": "Median game length", "value": f"turn {_turn(res['median_turn'])}", "note": note}
        )
    tiles_html = "<div class='tiles forge-tiles'>" + "".join(_tile(t) for t in tiles) + "</div>"
    any_losses = any(s.get("losses") for s in seats)
    rows = []
    for seat in seats:
        rate = seat.get("win_rate")
        width = max(0, min(100, round(100 * rate))) if _float(rate) is not None else 0
        you = " <span class='badge'>your deck</span>" if seat is mine else ""
        name = _esc(front_face(seat.get("deck")))
        mark = " class='mine'" if seat is mine else ""
        rows.append(
            f"<tr{mark}><th scope='row'>{name}{you}</th>"
            f"<td class='num' data-label='Wins'>{_int(seat.get('wins')) or 0}</td>"
            "<td class='num rate' data-label='Win rate'>"
            f"<span class='meter' aria-hidden='true'><span style='width:{width}%'></span></span>"
            f"{_esc(_pct(rate))}</td><td class='num' data-label='95% interval'>"
            f"{_esc(_interval(seat.get('win_rate_95')) or '–')}</td>"
            + (
                f"<td data-label='How it lost'>{_esc(_losses_text(seat.get('losses')) or '–')}</td>"
                if any_losses
                else ""
            )
            + "</tr>"
        )
    table = (
        "<div class='scroll'><table class='qty seats stack'><caption class='sr-only'>Results for each deck "
        "at the table</caption><thead><tr><th scope='col'>Deck</th><th scope='col' class='num'>Wins</th>"
        "<th scope='col' class='num'>Win rate</th><th scope='col' class='num'>95% interval</th>"
        + ("<th scope='col'>How it lost</th>" if any_losses else "")
        + f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )
    ended = res.get("ended") if isinstance(res.get("ended"), dict) else {}
    if not ended and f.get("state") not in ("queued", "running"):
        # a run stored before 0.7.30 counted only draws and games stopped at the limit (never worked
        # out while the run still plays: the games not yet finished are not wins)
        draws, slow = _int(res.get("draws")) or 0, _int(res.get("stopped_slow")) or 0
        ended = {"won": games - draws, "draw": draws - slow, "time_limit": slow}
    ended_rows = "".join(
        f"<li><span>{_esc(ENDED_TEXT[k])}</span><b>{_int(ended.get(k)) or 0}</b></li>"
        for k in ENDED_TEXT
        if _int(ended.get(k))
    )
    ended_html = (
        "<h3>How the games ended</h3><ul class='ended'>"
        + ended_rows
        + "</ul>"
        + (
            "<p class='muted small'>Forge counts a game that reaches its time limit as a draw. If many "
            "games end this way, Forge had too little time for these decks, and the win rates say "
            "less.</p>"
            if _int(ended.get("time_limit"))
            else ""
        )
        if ended_rows
        else ""
    )
    listed = [g for g in res.get("game_list") or [] if isinstance(g, dict)]
    games_html = ""
    if listed:
        names = {i + 1: front_face(s.get("deck")) for i, s in enumerate(seats)}
        body = "".join(
            f"<tr><th scope='row' class='num'>Game {_int(g.get('game')) or ''}</th>"
            f"<td data-label='Winner'>{_esc(names.get(g.get('winner'), '–'))}</td>"
            f"<td class='num' data-label='Turn'>{_esc(_turn(g.get('turn')))}</td>"
            f"<td class='num' data-label='Time'>{_esc(_seconds_text(g.get('seconds')))}</td>"
            f"<td data-label='Ended'>{_esc(ENDED_TEXT.get(str(g.get('ended')), '–'))}</td></tr>"
            for g in listed
        )
        games_html = (
            f"<details class='games'><summary>Each game ({len(listed)})</summary><div class='scroll'>"
            "<table class='qty stack'><thead><tr><th scope='col' class='num'>Game</th>"
            "<th scope='col'>Winner</th>"
            "<th scope='col' class='num'>Turn</th><th scope='col' class='num'>Time</th>"
            f"<th scope='col'>Ended</th></tr></thead><tbody>{body}</tbody></table></div></details>"
        )
    return "<div class='forge-results'>" + tiles_html + table + ended_html + games_html + "</div>"


def _turn(value: Any) -> str:
    f = _float(value)
    return "–" if f is None else (f"{f:.0f}" if f == int(f) else f"{f:.1f}")


def _interval(pair: Any) -> str:
    low, high = pair if isinstance(pair, (list, tuple)) and len(pair) == 2 else (None, None)
    return f"{_pct(low)} to {_pct(high)}" if _float(low) is not None and _float(high) is not None else ""


# No replay promise: the seed is for the record only, and every report, the
# page and every export, says how to compare runs instead.
FORGE_DRIFT_NOTE = (
    "Forge does not always replay the same games from the same seed, so compare runs by their win "
    "rates and 95% intervals, not game by game."
)


def forge_lines(f: dict[str, Any], *, results: bool = True) -> list[str]:
    """Forge's result as plain sentences, shared by the page and the Markdown export. The page
    draws the results themselves as a table (forge_results_html) and passes ``results=False``."""
    res = f.get("result") or {}
    out: list[str] = []
    ahead = f.get("waiting_for") if isinstance(f.get("waiting_for"), dict) else None
    if f.get("engine_busy") and f.get("state") == "queued":
        every = f" every {f['retry_after']} seconds" if f.get("retry_after") else ""
        out.append(f"The gateway asks Forge again{every} and starts the games when Forge has room.")
    elif f.get("waiting") and f.get("state") == "queued":
        place = f"Waiting: number {f['position']} in line" if f.get("position") else "Waiting in line"
        behind = (
            f", behind your report of {front_face(ahead.get('deck_name')) or 'another deck'}" if ahead else ""
        )
        out.append(f"{place}{behind}. It starts by itself when the run ahead of it ends.")
    elif f.get("state") == "running" and f.get("games_requested") and not (res.get("games") and not results):
        # the page's results show "Games played" as a tile once a game has finished
        out.append(f"Games finished so far: {res.get('games') or 0} of {f['games_requested']}.")
    if f.get("state") in ("queued", "running") and f.get("expected_seconds"):
        minutes = max(1, round(f["expected_seconds"] / 60))
        per = f.get("average_game_seconds_measured")
        basis = f" (at the measured {per} seconds a game)" if per else ""
        out.append(f"Expected to finish in about {minutes} minute{'s' if minutes != 1 else ''}{basis}.")
    seats = res.get("seats") or []
    if res.get("games") and results:
        asked = res.get("games_requested") or f.get("games_requested")
        out.append(f"Games played: {res['games']} of {asked}." if asked else f"Games played: {res['games']}.")
        for seat in seats:
            low, high = (seat.get("win_rate_95") or [None, None])[:2]
            ci = (
                f" (95% interval {_pct(low)} to {_pct(high)})" if low is not None and high is not None else ""
            )
            lost = _losses_text(seat.get("losses"))
            out.append(
                f"{front_face(seat.get('deck'))}: {seat.get('wins', 0)} wins, "
                f"{_pct(seat.get('win_rate'))}{ci}." + (f" Lost by: {lost}." if lost else "")
            )
        if res.get("draws"):
            slow = (
                f", {res['stopped_slow']} of them stopped at the time limit"
                if res.get("stopped_slow")
                else ""
            )
            out.append(f"Draws: {res['draws']}{slow}.")
        if res.get("average_game_seconds") is not None:
            median = (
                f", median {res['median_game_seconds']} seconds"
                if res.get("median_game_seconds") is not None
                else ""
            )
            out.append(f"Average game: {res['average_game_seconds']} seconds{median}.")
        if res.get("median_turn") is not None:
            span = res.get("turns_range") if isinstance(res.get("turns_range"), list) else []
            out.append(
                f"Median game length: turn {_turn(res['median_turn'])}"
                + (f" (from turn {span[0]} to {span[1]})" if len(span) == 2 else "")
                + "."
            )
    # Why these opponents (forge.pick_opponents): the deck's bracket, where it came from, and
    # each precon's bracket, or the fallback to the newest precons.
    choice = f.get("opponent_choice") if isinstance(f.get("opponent_choice"), dict) else {}
    if choice.get("note"):
        rated = choice.get("opponent_brackets") if isinstance(choice.get("opponent_brackets"), dict) else {}
        seated = ", ".join(f"{front_face(n)} (bracket {b})" for n, b in rated.items())
        out.append(f"Opponents: {choice['note']}" + (f" Seated: {seated}." if seated else ""))
    if f.get("seed") is not None:
        # The seed is kept for the record only, never as a way to replay the games,
        # and every report says why, the page and the export alike.
        whose = {
            "gateway": " (chosen by the gateway, kept for the record)",
            "request": " (set when this report was asked for, kept for the record)",
        }
        out.append(f"Seed: {f['seed']}{whose.get(str(f.get('seed_source')), ' (kept for the record)')}.")
        out.append(FORGE_DRIFT_NOTE)
    elif f.get("state") in ("done", "running") and f.get("job_id"):
        out.append("Seed: none recorded (this run was started without one).")
    return out


def forge_html(f: Any, *, live: bool = False, report_id: Any = None) -> str:
    """The Forge card: real games against precons (forge.py), or why there are none yet. ``live``
    (the signed-in page, not an export) marks an unfinished run for forge-live.js, which refreshes
    the card until the run ends."""
    if not isinstance(f, dict):
        return ""
    state = str(f.get("state") or "")
    if state == "skipped":
        return preflight_html(f)
    badge = {
        "done": "",
        "queued": " <span class='badge'>queued</span>",
        "running": " <span class='badge'>running</span>",
    }
    not_run = " <span class='badge danger'>not run</span>"
    stopped = forge_stopped_text(f)
    if stopped:
        # every game played: the notice says the run did not end cleanly, and no badge
        not_run = " <span class='badge warn'>stopped early</span>" if forge_stopped_early(f) else ""
    head = f"<h2>{FORGE_HEADING}{badge.get(state, not_run)}</h2>"
    parts = [
        head,
        "<p class='muted small'>Forge, an open-source rules engine, plays the deck against the "
        "opponent decks below, with combat, the stack and every player's interaction, all piloted by "
        "Forge's AI. Treat the result as a test of the deck against these opponents, "
        "not a prediction for your table.</p>",
    ]
    pending = state in ("queued", "running")
    if f.get("more_note"):
        parts.append(f"<p class='notice warn'>{_esc(MORE_DID_NOT_START + str(f['more_note']))}</p>")
    if state != "done":
        msg = stopped or f.get("error") or FORGE_STATE_TEXT.get(_forge_key(f), "")
        if msg and pending:
            msg += (
                " This section updates by itself when the games finish."
                if live
                else " See the gateway for the result."
            )
        if msg:
            parts.append(
                f"<p class='notice{' warn' if state not in ('queued', 'running') else ''}'>{_esc(msg)}</p>"
            )
    parts.append(forge_results_html(f))
    lines = forge_lines(f, results=False)
    if lines:
        parts.append("<ul class='sumlines'>" + "".join(f"<li>{_esc(ln)}</li>" for ln in lines) + "</ul>")
    if f.get("not_played"):
        parts.append(
            "<div class='honesty'><h3>Cards Forge could not play</h3><p class='muted small'>These were "
            "left out of the games, so the numbers above do not include them.</p><p>"
            + ", ".join(_esc(n) for n in f["not_played"])
            + "</p></div>"
        )
    ahead = f.get("waiting_for") if isinstance(f.get("waiting_for"), dict) else None
    if live and pending and ahead and ahead.get("report_id"):
        parts.append(
            f"<p class='small'><a href='/history/reports/{_esc(ahead['report_id'])}'>Open the report "
            "it is waiting for</a></p>"
        )
    # A run seated with another report's opponents links that report (the live page only;
    # an export has no gateway pages to link to).
    choice = f.get("opponent_choice") if isinstance(f.get("opponent_choice"), dict) else {}
    pair = choice.get("shared_with") or f.get("shared_with")
    if live and isinstance(pair, dict) and isinstance(pair.get("report_id"), str):
        name = str(pair.get("deck_name") or "the other deck")
        parts.append(
            f"<p class='small'><a href='/history/reports/{_esc(pair['report_id'])}'>Open {_esc(name)}'s "
            "report, which met the same opponents</a></p>"
        )
    # the report's id lets forge-live.js ask the JSON status route (api.py) for this card alone
    rid = f" data-report-id='{_esc(report_id)}'" if isinstance(report_id, str) and report_id else ""
    attrs = f" data-forge-live aria-live='polite'{rid}" if live and pending else ""
    return f"<div class='card forge'{attrs}>" + "".join(parts) + "</div>"


# A run that ended early but played some games (one group failed, the time limit, a cancel, or the
# gateway losing track of it): the card reports what did play, never "not run".
FORGE_STOPPED_WHY = {
    "failed": "because the rest of the games failed",
    "timeout": "because the run reached its time limit",
    "cancelled": "because the run was stopped",
}


def _forge_stop(f: dict[str, Any]) -> tuple[int, int | None] | None:
    """(games played, games asked for or None) for a run that ended without "done" after playing
    some games, else None."""
    if str(f.get("state") or "") not in (*FORGE_STOPPED_WHY, "lost"):
        return None
    result = f.get("result") if isinstance(f.get("result"), dict) else {}
    played = result.get("games")
    if not isinstance(played, int) or isinstance(played, bool) or played <= 0:
        return None
    total = f.get("games_requested") or result.get("games_requested")
    return played, total if isinstance(total, int) and total >= played else None


def forge_stopped_text(f: dict[str, Any]) -> str:
    """The sentence for a run that ended without "done" after playing some games, else ""."""
    stop = _forge_stop(f)
    if stop is None:
        return ""
    played, total = stop
    games = "game" if played == 1 else "games"
    those = "that one game" if played == 1 else f"those {played} games"
    of = f"{played} of {total} games" if total is not None else f"{played} {games}"
    if total is not None and played >= total:
        # every game asked for played, then one group still ended badly
        text = (
            "Forge played the one game asked for, but the run did not end cleanly. "
            "The results below count it."
            if played == 1
            else f"Forge played all {played} {games}, but the run did not end cleanly. "
            f"The results below count all {played}."
        )
    elif f.get("state") == "lost":
        # a lost run keeps what the last check saw; Forge may have played on
        text = f"The gateway lost track of this run after {of}. The results below count only {those}."
    else:
        counted = "that one game" if played == 1 else f"the {played} games played"
        text = (
            f"Forge stopped after {of}, {FORGE_STOPPED_WHY[str(f['state'])]}. "
            f"The results below count only {counted}."
        )
    error = str(f.get("error") or "")
    return f"{text} {error}" if error else text


def forge_stopped_early(f: dict[str, Any]) -> bool:
    """Whether the card carries the "stopped early" badge: some games played, but not all."""
    stop = _forge_stop(f)
    return stop is not None and (stop[1] is None or stop[0] < stop[1])


def _forge_key(f: dict[str, Any]) -> str:
    if f.get("engine_busy"):
        return "busy"
    return "waiting" if f.get("waiting") else str(f.get("state"))


def preflight_html(f: dict[str, Any] | None) -> str:
    """The card in Forge's place when a report has no rules-engine games: Forge off on this
    gateway (``f`` None) or left out of this report (a "skipped" section, with its reason)."""
    if f is None:
        why = (
            "Forge, the rules engine, was not set up on this gateway, or not asked for, when this report ran."
        )
        warn = ""
    else:
        why = str(f.get("note") or "")
        warn = str(f.get("error") or "")
    body = (
        f"<p class='muted small'>{_esc(why)} The report holds the deck statistics and, when the research "
        "service ran, the solo test and the validation.</p>"
        if why
        else ""
    )
    if warn:
        body += f"<p class='notice warn'>{_esc(warn)}</p>"
    return f"<div class='card forge preflight'><h2>{PREFLIGHT_ONLY}</h2>{body}</div>"


def engine_html(r: dict[str, Any], *, live: bool = False) -> str:
    """The Forge card of a report, or the preflight-only card when it has no Forge games."""
    f = r.get("forge")
    if not isinstance(f, dict):
        return preflight_html(None)
    return forge_html(f, live=live, report_id=r.get("report_id"))


def status_inner(status: Any) -> str:
    """The badge and words inside the head's status (``#rep-status``), or '' for no status."""
    if not isinstance(status, str) or status not in STATUS_TEXT:
        return ""
    cls = {"complete": "ok", "partial": "warn", "failed": "danger"}.get(status, "")
    return (
        f"<span class='badge {cls}'>{_esc(status)}</span> "
        f"<span class='muted small'>{_esc(STATUS_TEXT[status].partition(': ')[2])}</span>"
    )


def status_html(status: Any) -> str:
    inner = status_inner(status)
    return f"<dt>Status</dt><dd id='rep-status'>{inner}</dd>" if inner else ""


def forge_status(r: dict[str, Any]) -> dict[str, Any]:
    """What the report page's live view needs while the Forge games play: the run's state
    and section, the report's overall status, and the Forge card and the status as the page draws
    them, so forge-live.js swaps them in without fetching the whole page again."""
    f = r.get("forge") if isinstance(r.get("forge"), dict) else None
    state = str((f or {}).get("state") or "") or None
    return {
        "report_id": r.get("report_id"),
        "status": r.get("status"),
        "state": state,
        "live": state in ("queued", "running"),
        "forge": f,
        "forge_html": engine_html(r, live=True),
        "status_html": status_inner(r.get("status")),
    }


def report_sections(r: dict[str, Any]) -> dict[str, Any]:
    """Everything the page, the Markdown and the HTML export share, computed once."""
    stats = r.get("stats") if isinstance(r.get("stats"), dict) else {}
    g = parse_goldfish(r.get("goldfish"), deck_size=_int(stats.get("card_count")))
    v = parse_validation(r.get("validation"))
    commander = (g or {}).get("commander") or ((stats.get("commanders") or [None])[0] if stats else None)
    return {
        "goldfish": g,
        "validation": v,
        "commander": commander,
        "tiles": tiles_for(g) if g else [],
        "charts": charts_for(g) if g else [],
        "honesty": honesty_html(g) if g else "",
        "unmodeled": ((g or {}).get("honesty") or {}).get("unrecognized") or [],
        "triggers": _triggers_html(g) if g else "",
    }


def report_body_html(
    r: dict[str, Any],
    *,
    stats_html: str = "",
    actions_html: str = "",
    reused: bool = False,
    standalone: bool = False,
    removable: frozenset[str] = frozenset(),
    csrf: str = "",
) -> str:
    """The report page's body. ``stats_html`` is the deck statistics strip (companion.stats_strip)
    and ``actions_html`` the export buttons row; ``standalone`` leaves out everything that needs
    the live site (links into it, the copy area)."""
    s = report_sections(r)
    g, v = s["goldfish"], s["validation"]
    name = _esc(r.get("deck_name") or r.get("deck_id"))
    deck_href = f"/decks/{_esc(r.get('deck_id'))}"
    meta = []
    if s["commander"]:
        meta.append(f"<dt>Commander</dt><dd>{_esc(front_face(s['commander']))}</dd>")
    if g and g.get("games"):
        turns = f", through turn {g['until_turn']}" if g.get("until_turn") else ""
        seed = f" (seed {g['seed']})" if g.get("seed") is not None else ""
        meta.append(f"<dt>Games</dt><dd>{_esc(g['games'])}{_esc(turns)}{_esc(seed)}</dd>")
    meta.append(f"<dt>Run</dt><dd>{_when(r.get('taken_at'))}</dd>")
    meta.append(status_html(r.get("status")))
    if standalone:
        title = f"<h1>{name}</h1>"
        back = ""
    else:
        title = f"<h1><a href='{deck_href}'>{name}</a></h1>"
        back = (
            f"<p class='crumbs'><a href='{deck_href}'>← Back to the deck</a> · "
            f"<a href='/history?deck_id={_esc(r.get('deck_id'))}'>Deck history</a></p>"
        )
    notice = ""
    if reused:
        notice = (
            "<p class='notice'>The deck had not changed since its last report a few minutes ago, so that "
            "report is shown instead of running the simulation again.</p>"
        )
    head = (
        "<div class='card report-head'>"
        + back
        + title
        + f"<dl class='meta'>{''.join(meta)}</dl>"
        + notice
        + actions_html
        + "</div>"
    )
    body = head + engine_html(r, live=not standalone)
    if not standalone:
        body += more_games_html(r.get("forge"), csrf, str(r.get("report_id") or ""))
    if g is None and v is None:
        body += (
            f"<div class='card'><h2>{_esc(solo_heading(g))}</h2><p class='muted'>The research service "
            "was not configured when this report ran, so there is no solo test and no validation: only "
            "the deck statistics below.</p></div>"
        )
    elif g is None:
        body += (
            f"<div class='card'><h2>{_esc(solo_heading(g))}</h2>"
            "<p class='muted'>The solo test did not run for this report.</p></div>"
        )
    elif g.get("message") and not g.get("metrics"):
        body += (
            f"<div class='card'><h2>{_esc(solo_heading(g))} <span class='badge danger'>not run</span></h2>"
            f"<p class='notice warn'>{_esc(g['message'])}</p></div>"
        )
    else:
        warn = "".join(f"<p class='notice warn'>{_esc(w)}</p>" for w in g.get("warnings") or [])
        tiles = "".join(_tile(t) for t in s["tiles"])
        charts = "".join(
            f"<div class='chart'><h3>{_esc(t)}</h3><p class='muted small'>{_esc(b)}</p>{svg}</div>"
            for t, b, svg in s["charts"]
        )
        summary = ""
        if g.get("summary"):
            summary = (
                "<details class='disclosure'><summary>The simulator's own summary</summary>"
                "<ul class='sumlines'>"
                + "".join(f"<li>{_esc(plain_words(ln))}</li>" for ln in g["summary"])
                + "</ul></details>"
            )
        body += (
            f"<div class='card'><h2>{_esc(solo_heading(g))}</h2>"
            "<p class='muted small'>Each solo test game is played alone, with no opponent to interact: "
            "the deck draws, plays lands, casts what it can and attacks. The numbers say how fast the "
            "deck does its own thing, not how it fares against interaction.</p>"
            + warn
            + (f"<div class='tiles wide'>{tiles}</div>" if tiles else "")
            + (f"<div class='charts'>{charts}</div>" if charts else "")
            + s["triggers"]
            + kept_models_html(
                kept_models(r), removable=removable, csrf=csrf, report_id=str(r.get("report_id") or "")
            )
            + summary
            + "</div>"
        )
        honesty = s["honesty"] if standalone else honesty_html(g, ask_html(r, s["unmodeled"]))
        if honesty:
            body += (
                "<div class='card honesty'><h2>What the solo test could not model</h2>"
                "<p class='muted small'>Read the numbers above with this in mind: the more of the deck sits "
                "here, the less the solo test says about it.</p>" + honesty + "</div>"
            )
    if g is not None or v is not None:
        body += f"<div class='card'><h2>Validation {_validation_badge(v)}</h2>{validation_html(v)}</div>"
    if stats_html:
        body += f"<div class='card'><h2>Deck statistics</h2>{stats_html}</div>"
    raw = ""
    if g and g.get("raw"):
        raw += f"<h3>Simulation output</h3><pre>{_esc(g['raw'])}</pre>"
    if v and v.get("raw"):
        raw += f"<h3>Validation output</h3><pre>{_esc(v['raw'])}</pre>"
    if raw:
        body += (
            "<details class='card raw disclosure'><summary>Raw output from the research service</summary>"
            + raw
            + "</details>"
        )
    return f"<div class='report'>{body}</div>"


# -- exports -----------------------------------------------------------------------
def report_markdown(r: dict[str, Any]) -> str:
    """The report as Markdown: the headline numbers and the plain-language panels first, then the
    research service's own output."""
    s = report_sections(r)
    g, v = s["goldfish"], s["validation"]
    stats = r.get("stats") if isinstance(r.get("stats"), dict) else {}
    lines = [f"# Deck report: {r.get('deck_name') or r.get('deck_id')}", ""]
    if s["commander"]:
        lines.append(f"- Commander: {front_face(s['commander'])}")
    lines.append(f"- Deck: https://archidekt.com/decks/{r.get('deck_id')}")
    lines.append(f"- Run: {time_text(r.get('taken_at'))}")
    if r.get("status") in STATUS_TEXT:
        lines.append(f"- Status: {STATUS_TEXT[r['status']]}")
    if g and g.get("games"):
        extra = f", through turn {g['until_turn']}" if g.get("until_turn") else ""
        lines.append(f"- Games: {g['games']}{extra}")
    lines.append("")
    if s["tiles"]:
        lines += [f"## {solo_heading(g)}", ""]
        for t in s["tiles"]:
            ci = f" (95% interval {_ci_text(t['rate'])})" if t.get("rate") and _ci_text(t["rate"]) else ""
            note = f" — {t['note']}" if t.get("note") else ""
            lines.append(f"- {t['label']}: {t['value']}{ci}{note}")
        lines.append("")
    elif g and g.get("message"):
        lines += [f"## {solo_heading(g)}", "", g["message"], ""]
    if kept_models(r):
        used = sorted(kept_models(r), key=lambda u: u["name"].casefold())
        named = ", ".join(
            front_face(u["name"]) + (f" ({_kept_who(u)})" if _kept_who(u) else "") for u in used
        )
        lines += [f"{KEPT_HEADING}: {named}", ""]
    forge = r.get("forge") if isinstance(r.get("forge"), dict) else None
    if forge is None or forge.get("state") == "skipped":
        why = (
            "Forge, the rules engine, was not set up on this gateway, or not asked for, when this report ran."
            if forge is None
            else " ".join(str(x) for x in (forge.get("note"), forge.get("error")) if x)
        )
        lines += [f"## {PREFLIGHT_ONLY}", "", why, ""]
    elif forge:
        lines += [f"## {FORGE_HEADING}", ""]
        if forge.get("state") != "done":
            text = (
                forge_stopped_text(forge) or forge.get("error") or FORGE_STATE_TEXT.get(_forge_key(forge), "")
            )
            lines += [str(text), ""]
        lines += [f"- {ln}" for ln in forge_lines(forge)]
        if forge.get("not_played"):
            lines.append("- Cards Forge could not play: " + ", ".join(forge["not_played"]))
        lines.append("")
    if g:
        h = g["honesty"]
        size = g.get("deck_size")
        if h["unrecognized"] or h["out_of_scope"] or h["low_impact"]:
            lines += ["## What the solo test could not model", ""]
            if (h["unrecognized"] or h["out_of_scope"]) and coverage_text(g):
                lines += [coverage_text(g), ""]
        if h["unrecognized"]:
            lines.append(f"{UNMODELED_HEADING}: {_share(_copies(h['unrecognized']), size)}.")
            lines.append(", ".join(_md_name(c) for c in h["unrecognized"]))
            lines.append("")
        if h["out_of_scope"]:
            total = _copies([c for grp in h["out_of_scope"] for c in grp["cards"]])
            lines.append(f"Left out on purpose: {_share(total, size)}.")
            for grp in h["out_of_scope"]:
                lines.append(f"- {grp['label']}: " + ", ".join(_md_name(c) for c in grp["cards"]))
            lines.append("")
        if h["low_impact"]:
            lines.append(
                f"Played with their effect but never mattered: {_share(len(h['low_impact']), size)}."
            )
            lines.append(", ".join(front_face(n) for n in h["low_impact"]))
            lines.append("")
    if v is not None:
        verdict = {"passed": "passed", "issues": "issues found", "failed": "not run"}.get(
            v["verdict"] or "", ""
        )
        lines += ["## Validation" + (f": {verdict}" if verdict else ""), ""]
        if v.get("headline"):
            lines.append(v["headline"])
        lines += [f"- {ln}" for ln in v["lines"]]
        lines.append("")
    if stats:
        lines += ["## Deck statistics", ""]
        for key, label in (
            ("card_count", "Cards"),
            ("land_count", "Lands"),
            ("average_mana_value", "Average mana value"),
            ("price_total", "Price (USD)"),
        ):
            if stats.get(key) is not None:
                lines.append(f"- {label}: {stats[key]}")
        curve = stats.get("mana_curve")
        if isinstance(curve, dict) and curve:
            lines.append("- Mana curve: " + ", ".join(f"{k}: {val}" for k, val in curve.items()))
        lines.append("")
    if g and g.get("raw"):
        lines += ["## Simulation output", "", g["raw"], ""]
    if v and v.get("raw"):
        lines += ["## Validation output", "", v["raw"], ""]
    return "\n".join(lines).rstrip() + "\n"


EXPORT_CSS = """
:root{--bg:#f9fafb;--surface:#fafafa;--surface-2:#dedede;--surface-3:#c1c1c1;--border:#bababa;
  --border-soft:#d4d4d4;--text:#383838;--text-muted:#5e5e5e;--link:#2a66c9;--orange:#fa890d;--blue:#4286f4;
  --green:#1ebb6c;--purple:#6435c9;--on-orange:#111;--on-green:#111;--danger-fill:#c0182b;--red:#ff555b;
  --orange-tint:rgba(250,137,13,.16);--orange-text:#a65400;--radius:5px}
@media (prefers-color-scheme:dark){:root{--bg:#181818;--surface:#232323;--surface-2:#383838;
  --surface-3:#4b4b4b;--border:#5f5f5f;--border-soft:#323232;--text:#e3e3e3;--text-muted:#a8a8a8;--link:#73a8dc;
  --orange-tint:rgba(250,137,13,.14);--orange-text:#fa890d}}
*{box-sizing:border-box} html{font-size:14px;-webkit-text-size-adjust:100%}
body{margin:0;padding:1rem;background:var(--bg);color:var(--text);line-height:1.35;
  font-family:Lato,"Helvetica Neue",Arial,Helvetica,sans-serif}
.report{max-width:52rem;margin:0 auto} a{color:var(--link)}
h1{font-size:2rem;margin:.25rem 0 .75rem;overflow-wrap:anywhere} h2{font-size:1.5rem;margin:0 0 .6rem}
h3{font-size:1.1rem;margin:.75rem 0 .4rem} h4{font-size:1rem;margin:.6rem 0 .2rem} p{margin:.5rem 0}
h3 small,h4 small{font-weight:400;color:var(--text-muted);font-size:.86rem;margin-left:.4rem}
.muted{color:var(--text-muted)} .small{font-size:.86rem} .b{font-weight:700}
.card{background:var(--surface);border:1px solid var(--border);border-radius:3px;padding:1rem;margin:0 0 1rem}
.card > :first-child{margin-top:0} .card > :last-child{margin-bottom:0}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:.3rem 1rem;margin:0 0 1rem;font-size:.93rem}
.meta dt{color:var(--text-muted);font-weight:700} .meta dd{margin:0;overflow-wrap:anywhere}
.badge{display:inline-block;vertical-align:middle;padding:.125rem .5rem;border-radius:5px;font-size:.86rem;
  font-weight:700;background:#6b6b6b;color:#fff}
.badge.ok{background:var(--green);color:var(--on-green)}
.badge.warn{background:var(--orange);color:var(--on-orange)}
.badge.danger{background:var(--danger-fill)}
.notice{border:1px solid var(--border);border-left:4px solid var(--blue);background:var(--surface);
  border-radius:3px;padding:.7rem .9rem;margin:0 0 1rem;font-weight:700}
.notice.warn{border-left-color:var(--orange);background:var(--orange-tint);color:var(--orange-text)}
pre{font-size:.86rem;line-height:1.5;background:var(--bg);border:1px solid var(--border-soft);
  border-radius:5px;padding:.75rem .9rem;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}
details summary{cursor:pointer;font-weight:700}
"""


def report_export_html(r: dict[str, Any], *, stats_html: str = "", theme_css: str = "") -> str:
    """A self-contained HTML file of the report: inline CSS, inline SVG, no scripts, no links
    back into the gateway. ``theme_css`` is the shared chart/tile CSS (theme.REPORT_SHARED_CSS)."""
    body = report_body_html(r, stats_html=stats_html, standalone=True)
    title = _esc(f"Deck report: {r.get('deck_name') or r.get('deck_id')}")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='color-scheme' content='light dark'>"
        f"<link rel='icon' href='{ICON_DATA_URL}' type='image/svg+xml'>"
        f"<title>{title}</title><style>{EXPORT_CSS}{theme_css}{REPORT_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


# Page-only styles (the shared tiles, charts and badges live in theme.py).
REPORT_CSS = """
.report .crumbs{margin:0 0 .25rem} .report h1{margin:.25rem 0 .75rem}
.report h1 a{text-decoration:none;color:inherit}
.report h1 a:hover{color:var(--orange)}
.report .form-actions{margin-top:.75rem} .report .form-actions > *{margin:0}
.report .honesty .group{margin:1rem 0 0;padding-top:.75rem;border-top:1px solid var(--border-soft)}
.report .honesty .group:first-of-type{border-top:0;padding-top:0}
.report .honesty h3 small,.report .honesty h4 small{font-weight:400;color:var(--text-muted);font-size:.86rem;
  margin-left:.4rem}
.report .honesty h4{margin:.75rem 0 .25rem}
.report .names{list-style:none;margin:.25rem 0 0;padding:0;display:flex;flex-wrap:wrap;gap:.35rem}
.report .names li{display:inline-flex;align-items:baseline;gap:.35rem;padding:.2rem .55rem;border-radius:3px;
  background:var(--surface-2);border:1px solid var(--border-soft);font-size:.9rem;overflow-wrap:anywhere;
  max-width:100%}
.report .names li i{font-style:normal;color:var(--text-muted);font-size:.8rem;white-space:nowrap}
.report .approx{grid-template-columns:minmax(10rem,max-content) 1fr} .report .approx dd{font-size:.93rem}
@media (max-width:480px){ .report .approx{grid-template-columns:1fr} }
.report .kept{margin:1rem 0 0;padding-top:.75rem;border-top:1px solid var(--border-soft)}
.report .kept h3 small{font-weight:400;color:var(--text-muted);font-size:.86rem;margin-left:.4rem}
.report .kept .names li{flex-wrap:wrap;align-items:center}
.report .kept .names li i{white-space:normal}
.report .kept .names form{margin:0}
.report .kept .names button{min-height:44px}
.report .vlines{margin:.25rem 0 0;padding-left:1.25rem}
.report .vlines li{margin:.2rem 0;overflow-wrap:anywhere}
.report .sumlines{margin:.5rem 0 0;padding-left:1.25rem}
.report .sumlines li{margin:.25rem 0;overflow-wrap:anywhere}
.report details.raw{margin-top:1rem} .report details.raw > summary{margin:0}
.report details.raw[open] > summary{margin-bottom:.5rem}
.report table.trig{width:100%;border-collapse:collapse;font-size:.93rem;margin-top:.25rem}
.report table.trig th{text-align:left;font-size:.86rem;color:var(--text-muted);padding:.2rem .25rem}
.report table.trig td{padding:.3rem .25rem;border-top:1px solid var(--surface-2);overflow-wrap:anywhere}
.report table.trig td:last-child,.report table.trig th:last-child{text-align:right;
  font-variant-numeric:tabular-nums}
/* Forge's games (forge_results_html): a row per deck at the table, how the games ended, each game */
.report .forge-results{margin:.75rem 0 0}
.report .forge-results .scroll{overflow-x:auto;max-width:100%}
.report .forge-results table{width:100%;border-collapse:collapse;font-size:.93rem}
.report .forge-results th,.report .forge-results td{padding:.35rem .4rem;
  border-top:1px solid var(--border-soft);text-align:left;vertical-align:baseline;overflow-wrap:break-word}
.report .forge-results thead th{border-top:0;font-size:.86rem;color:var(--text-muted);font-weight:600}
.report .forge-results .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.report .forge-results tr.mine th,.report .forge-results tr.mine td{background:var(--surface-2)}
.report .forge-results tbody th{font-weight:600}
.report .forge-results .badge{margin-left:.35rem;vertical-align:baseline}
.report .forge-results .meter{display:inline-block;width:3.5rem;height:6px;margin-right:.4rem;
  border-radius:3px;background:var(--surface-3);overflow:hidden;vertical-align:middle}
.report .forge-results .meter span{display:block;height:100%;background:var(--orange)}
.report .forge-results h3{margin:1rem 0 .35rem}
.report .forge-results .ended{list-style:none;margin:0;padding:0;border:1px solid var(--border-soft);
  border-radius:3px;max-width:32rem}
.report .forge-results .ended li{display:flex;justify-content:space-between;gap:.75rem;padding:.35rem .6rem;
  border-top:1px solid var(--border-soft)}
.report .forge-results .ended li:first-child{border-top:0}
.report .forge-results .ended b{font-variant-numeric:tabular-nums}
.report .forge-results details.games{margin-top:.75rem}
.report .forge-results details.games > summary{cursor:pointer;min-height:44px;display:flex;align-items:center}
/* phones: each row of a results table becomes a small block, every value with its column's name */
@media (max-width:600px){
  .report .forge-results table.stack thead{position:absolute;width:1px;height:1px;overflow:hidden;
    clip:rect(0 0 0 0);white-space:nowrap}
  .report .forge-results table.stack tr{display:block;padding:.45rem .5rem;
    border-top:1px solid var(--border-soft)}
  .report .forge-results table.stack th,.report .forge-results table.stack td{display:flex;
    justify-content:space-between;gap:.75rem;border:0;padding:.15rem 0;text-align:left;white-space:normal;
    overflow-wrap:break-word}
  .report .forge-results table.stack tbody th{display:block;font-size:1rem}
  .report .forge-results table.stack td::before{content:attr(data-label);color:var(--text-muted);
    font-size:.86rem;font-weight:400;flex:none}
  .report .forge-results table.stack td{text-align:right}
}
"""

__all__ = [
    "REPORT_CSS",
    "front_face",
    "parse_goldfish",
    "parse_validation",
    "plain_words",
    "report_body_html",
    "report_export_html",
    "report_markdown",
    "report_sections",
    "speed_numbers",
    "svg_columns",
    "svg_lines",
    "tiles_for",
]
