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

import html
import json
import re
from typing import Any

from .theme import ICON_DATA_URL, time_html, time_text

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
# The milestones the simulator tracks as "reached by turn N", in plain words and plot order.
MILESTONES = (
    ("commander_cast", "Commander cast"),
    ("table_lethal", "Lethal power on board"),
    ("kill", "40 damage dealt"),
    ("cmdr21", "21 commander damage"),
)
MAX_RAW = 60_000


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def front_face(name: Any) -> str:
    """The front face of a double-faced card name ("A // B" -> "A"), other names unchanged."""
    text = str(name or "").strip()
    return text.split(" // ")[0].strip() or text


def plain_words(text: str) -> str:
    """The simulator's text without the assistant tool names it mentions (``goldfish_report``,
    ``goldfish_annotate``): the page speaks to a person, not to an assistant."""
    text = re.sub(r'Full report: goldfish_report\("([^"]*)"\)', r"Run id: \1", text)
    text = re.sub(r"\s*\(candidates for annotation; see goldfish_annotate\)", " (could be annotated)", text)
    text = re.sub(r"goldfish_report\((\"?)([^)\"]*)\1\)", r"run \2", text)
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
    out["honesty"] = honesty
    if not out["metrics"] and not out["summary"]:
        # A refusal or an outage: the simulator's sentence is the whole story.
        out["message"] = plain_words(preamble or text)[:600] or ("Not simulated." if not ok else "")
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


def tiles_for(g: dict[str, Any]) -> list[dict[str, Any]]:
    """The headline numbers: label, value, interval, note."""
    m = g.get("metrics") or {}
    turns = g.get("until_turn") or 10
    out: list[dict[str, Any]] = []
    cc = m.get("commander_cast") if isinstance(m.get("commander_cast"), dict) else {}
    reached = _rate(cc.get("reached_pct"))
    med = cc.get("median_among_reached")
    if med is not None or reached:
        out.append(
            {
                "label": "Commander cast (median turn)",
                "value": f"Turn {med}" if med is not None else "–",
                "rate": None,
                "note": f"cast in {_pct(reached['value'])} of games by turn {turns}" if reached else "",
            }
        )
    for key, label in (("kill", "40 damage dealt"), ("table_lethal", "Lethal power on board")):
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


# -- drawing -----------------------------------------------------------------------
def _tile(t: dict[str, Any]) -> str:
    rate = t.get("rate")
    ci = ""
    if rate and rate.get("lo") is not None and rate.get("hi") is not None:
        lo, hi = max(0.0, rate["lo"]), min(1.0, rate["hi"])
        ci = (
            f"<span class='ci' title='95% interval {_esc(_ci_text(rate))}'>"
            f"<i style='left:{100 * lo:.1f}%;width:{max(1.0, 100 * (hi - lo)):.1f}%'></i></span>"
            f"<span>95% interval {_esc(_ci_text(rate))}</span>"
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
        drawn = f" <i>drawn {_pct(c['drawn'])}</i>" if c.get("drawn") is not None else ""
        items.append(f"<li>{_esc(front_face(c['name']))}{drawn}</li>")
    return f"<ul class='names'>{''.join(items)}</ul>"


def honesty_html(g: dict[str, Any]) -> str:
    """The "what the simulation could not model" panel body, or '' when there is nothing to say."""
    h = g.get("honesty") or {}
    size = g.get("deck_size")
    parts: list[str] = []
    unrec = h.get("unrecognized") or []
    if unrec:
        parts.append(
            f"<div class='group'><h3>Not recognized <small>{_esc(_share(len(unrec), size))}</small></h3>"
            "<p>The simulator has no model for these cards. It drew and cast them as blanks, so anything "
            "they would do in a real game is missing from the numbers above.</p>" + _names(unrec) + "</div>"
        )
    oos = h.get("out_of_scope") or []
    if oos:
        total = sum(len(grp["cards"]) for grp in oos)
        groups = "".join(
            f"<h4>{_esc(grp['label'])} <small>{len(grp['cards'])}</small></h4>" + _names(grp["cards"])
            for grp in oos
        )
        parts.append(
            f"<div class='group'><h3>Out of scope <small>{_esc(_share(total, size))}</small></h3>"
            "<p>The simulator plays against an opponent who does nothing, so removal, counterspells and "
            "protection have nothing to act on. These cards sat in hand.</p>" + groups + "</div>"
        )
    low = h.get("low_impact") or []
    if low:
        parts.append(
            f"<div class='group'><h3>Modeled but never mattered <small>{_esc(_share(len(low), size))}"
            "</small></h3>"
            "<p>These cards were understood, drawn and played, and nothing they do changed a game. For lands "
            "and mana rocks that is expected; for anything else it is a fair hint the card underperforms.</p>"
            + _names([{"name": n} for n in low])
            + "</div>"
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
    "queued": "Waiting for the simulation engine.",
    "running": "Games are being played now.",
    "failed": "The games could not run.",
    "timeout": "The games ran out of time before finishing.",
    "cancelled": "The games were stopped.",
    "lost": "The simulation engine lost this run.",
    "skipped": "No Forge games for this report.",
}


def forge_lines(f: dict[str, Any]) -> list[str]:
    """Forge's result as plain sentences, shared by the page and the Markdown export."""
    res = f.get("result") or {}
    out: list[str] = []
    seats = res.get("seats") or []
    if res.get("games"):
        out.append(f"Games played: {res['games']} of {res.get('games_requested') or res['games']}.")
        for seat in seats:
            low, high = (seat.get("win_rate_95") or [None, None])[:2]
            ci = (
                f" (95% interval {_pct(low)} to {_pct(high)})" if low is not None and high is not None else ""
            )
            out.append(
                f"{front_face(seat.get('deck'))}: {seat.get('wins', 0)} wins, "
                f"{_pct(seat.get('win_rate'))}{ci}."
            )
        if res.get("draws"):
            slow = (
                f", {res['stopped_slow']} of them stopped at the time limit"
                if res.get("stopped_slow")
                else ""
            )
            out.append(f"Draws: {res['draws']}{slow}.")
        if res.get("average_game_seconds") is not None:
            out.append(f"Average game: {res['average_game_seconds']} seconds.")
    if f.get("seed") is not None:
        out.append(f"Seed: {f['seed']}.")
    return out


def forge_html(f: Any, *, live: bool = False) -> str:
    """The Forge card: real games against precons (forge.py), or why there are none yet. ``live``
    (the signed-in page, not an export) marks an unfinished run for forge-live.js, which refreshes
    the card until the run ends."""
    if not isinstance(f, dict):
        return ""
    state = str(f.get("state") or "")
    badge = {
        "done": "",
        "queued": " <span class='badge'>queued</span>",
        "running": " <span class='badge'>running</span>",
    }
    not_run = " <span class='badge danger'>not run</span>"
    head = f"<h2>Forge games{badge.get(state, not_run)}</h2>"
    parts = [
        head,
        "<p class='muted small'>Forge, an open-source rules engine, plays the deck against the "
        "opponent decks below, with combat, the stack and every player's interaction, all piloted by "
        "Forge's AI. Treat the result as a test of the deck against these opponents, "
        "not a prediction for your table.</p>",
    ]
    pending = state in ("queued", "running")
    if state != "done":
        msg = f.get("error") or FORGE_STATE_TEXT.get(state, "")
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
    lines = forge_lines(f)
    if lines:
        parts.append("<ul class='sumlines'>" + "".join(f"<li>{_esc(ln)}</li>" for ln in lines) + "</ul>")
    if f.get("not_played"):
        parts.append(
            "<div class='honesty'><h3>Cards Forge could not play</h3><p class='muted small'>These were "
            "left out of the games, so the numbers above do not include them.</p><p>"
            + ", ".join(_esc(n) for n in f["not_played"])
            + "</p></div>"
        )
    attrs = " data-forge-live aria-live='polite'" if live and pending else ""
    return f"<div class='card forge'{attrs}>" + "".join(parts) + "</div>"


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
        "triggers": _triggers_html(g) if g else "",
    }


def report_body_html(
    r: dict[str, Any],
    *,
    stats_html: str = "",
    actions_html: str = "",
    reused: bool = False,
    standalone: bool = False,
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
    body = head + forge_html(r.get("forge"), live=not standalone)
    if g is None and v is None:
        body += (
            "<div class='card'><h2>Goldfish simulation</h2><p class='muted'>The research service was not "
            "configured when this report ran, so there is no simulation and no validation: only the deck "
            "statistics below.</p></div>"
        )
    elif g is None:
        body += "<div class='card'><h2>Goldfish simulation</h2><p class='muted'>Not simulated.</p></div>"
    elif g.get("message") and not g.get("metrics"):
        body += (
            "<div class='card'><h2>Goldfish simulation <span class='badge danger'>not run</span></h2>"
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
            "<div class='card'><h2>Goldfish simulation</h2>"
            "<p class='muted small'>A goldfish game is played against an opponent who does nothing: the deck "
            "draws, plays lands, casts what it can and attacks. The numbers say how fast the deck does its "
            "own "
            "thing, not how it fares against interaction.</p>"
            + warn
            + (f"<div class='tiles wide'>{tiles}</div>" if tiles else "")
            + (f"<div class='charts'>{charts}</div>" if charts else "")
            + s["triggers"]
            + summary
            + "</div>"
        )
        if s["honesty"]:
            body += (
                "<div class='card honesty'><h2>What the simulation could not model</h2>"
                "<p class='muted small'>Read the numbers above with this in mind: the more of the deck sits "
                "here, the less the simulation says about it.</p>" + s["honesty"] + "</div>"
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
    if g and g.get("games"):
        extra = f", through turn {g['until_turn']}" if g.get("until_turn") else ""
        lines.append(f"- Games: {g['games']}{extra}")
    lines.append("")
    if s["tiles"]:
        lines += ["## Goldfish simulation", ""]
        for t in s["tiles"]:
            ci = f" (95% interval {_ci_text(t['rate'])})" if t.get("rate") and _ci_text(t["rate"]) else ""
            note = f" — {t['note']}" if t.get("note") else ""
            lines.append(f"- {t['label']}: {t['value']}{ci}{note}")
        lines.append("")
    elif g and g.get("message"):
        lines += ["## Goldfish simulation", "", g["message"], ""]
    forge = r.get("forge") if isinstance(r.get("forge"), dict) else None
    if forge:
        lines += ["## Forge games", ""]
        if forge.get("state") != "done":
            lines += [str(forge.get("error") or FORGE_STATE_TEXT.get(str(forge.get("state")), "")), ""]
        lines += [f"- {ln}" for ln in forge_lines(forge)]
        if forge.get("not_played"):
            lines.append("- Cards Forge could not play: " + ", ".join(forge["not_played"]))
        lines.append("")
    if g:
        h = g["honesty"]
        size = g.get("deck_size")
        if h["unrecognized"] or h["out_of_scope"] or h["low_impact"]:
            lines += ["## What the simulation could not model", ""]
        if h["unrecognized"]:
            lines.append(f"Not recognized — {_share(len(h['unrecognized']), size)}:")
            lines.append(", ".join(front_face(c["name"]) for c in h["unrecognized"]))
            lines.append("")
        if h["out_of_scope"]:
            total = sum(len(grp["cards"]) for grp in h["out_of_scope"])
            lines.append(f"Out of scope — {_share(total, size)}:")
            for grp in h["out_of_scope"]:
                lines.append(f"- {grp['label']}: " + ", ".join(front_face(c["name"]) for c in grp["cards"]))
            lines.append("")
        if h["low_impact"]:
            lines.append(f"Modeled but never mattered — {_share(len(h['low_impact']), size)}:")
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
    "svg_columns",
    "svg_lines",
    "tiles_for",
]
