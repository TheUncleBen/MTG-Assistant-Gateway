"""One stored report as the signed-in page (``/history/reports/{id}``, approved design K5).

Crumbs, a header (the deck as the title, the status as a §4 label, the meta line, the export
buttons), six key-number widgets, a sticky row of section links, then the sections as flat blocks
with one heading each: Forge games, Solo test, What the solo test could not model (its groups
folded, the first open), Validation, Deck statistics and the raw output (folded). One orange
button: Play more games.

Every number and sentence comes from report_view, which also draws the Markdown and the
self-contained HTML export (those keep their own plain layout). Nothing here reads more than the
report row: the widgets repeat numbers the sections show."""

from __future__ import annotations

import re
from typing import Any

from . import ui
from .report_view import (
    CONSTRUCTED_VALIDATION,
    ENDED_TEXT,
    FORGE_HEADING,
    FORGE_STATE_TEXT,
    MORE_DID_NOT_START,
    PREFLIGHT_ONLY,
    _copies,
    _esc,
    _float,
    _forge_key,
    _int,
    _interval,
    _losses_text,
    _pct,
    _seconds_text,
    _tile,
    _turn,
    ask_html,
    coverage,
    coverage_text,
    forge_lines,
    forge_stopped_early,
    forge_stopped_text,
    front_face,
    honesty_groups,
    kept_models,
    kept_models_html,
    more_games_html,
    plain_words,
    report_sections,
    solo_heading,
    validation_html,
)
from .reports_page import PHONE_WIDGETS_CSS, STATUS_LABEL, status_label
from .theme import icon

# The "Played with their effect" widget takes the orange edge below this share of the deck
# (approved question 6).
COVERAGE_WARN = 70
VALIDATION_LABEL = {
    "passed": ("Passed", "green", "check"),
    "issues": ("Issues found", "orange", "triangle"),
    "failed": ("Not run", "red", "alert"),
}
SOLO_SUB = (
    "Each solo test game is played alone, with no opponent to interact: the deck draws, plays lands, "
    "casts what it can and attacks. The numbers say how fast the deck does its own thing, not how it "
    "fares against interaction."
)
FORGE_SUB = (
    "Forge, an open-source rules engine, plays the deck against the opponent decks below, with combat, "
    "the stack and every player's interaction, all piloted by Forge's AI. Treat the result as a test of "
    "the deck against these opponents, not a prediction for your table."
)
MODEL_SUB = (
    "Read the numbers above with this in mind: the more of the deck sits here, the less the solo test "
    "says about it."
)
ABOUT_FORGE = "About these Forge games"


def _sec(sid: str, heading: str, body: str, *, label: str = "", cls: str = "", attrs: str = "") -> str:
    """One flat section: its heading (with a label beside it) and its body."""
    extra = f" {cls}" if cls else ""
    return (
        f"<section class='ui-card rv-sec{extra}' id='{sid}' aria-labelledby='{sid}-h'{attrs}>"
        f"<div class='rv-sh'><h2 id='{sid}-h'>{_esc(heading)}</h2>{label}</div>{body}</section>"
    )


def _strip(items: list[tuple[str, str]]) -> str:
    """A one-line strip of numbers (label, value markup)."""
    return (
        "<dl class='rv-strip'>"
        + "".join(f"<div><dt>{_esc(k)}</dt><dd>{v}</dd></div>" for k, v in items)
        + "</dl>"
    )


# -- Forge ---------------------------------------------------------------------------
def forge_state_label(f: dict[str, Any]) -> str:
    """The label beside the Forge heading: "10 of 10 played" green once done; queued, running,
    stopped early and not run keep today's words."""
    state = str(f.get("state") or "")
    res = f.get("result") if isinstance(f.get("result"), dict) else {}
    if state == "done":
        games = _int(res.get("games"))
        asked = _int(res.get("games_requested")) or _int(f.get("games_requested")) or games
        return ui.label(f"{games} of {asked} played", "green", "check") if games else ""
    if state == "queued":
        return ui.label("Queued", "orange", "clock")
    if state == "running":
        return ui.label("Running", "orange", "spinner")
    if forge_stopped_text(f):
        return ui.label("Stopped early", "orange", "triangle") if forge_stopped_early(f) else ""
    return ui.label("Not run", "red", "alert")


def _seats_html(res: dict[str, Any], seats: list[dict[str, Any]]) -> str:
    """The decks at the table in the standard table style; your deck's row tinted. Phones: the
    wins, interval and how it lost under the name, the win rate at the end."""
    any_losses = any(s.get("losses") for s in seats)
    cols = "minmax(0,1fr) 4rem 9rem 8.5rem" + (" minmax(0,1fr)" if any_losses else "")
    head = [
        ui.Column("Deck"),
        ui.Column("Wins", "md-hide num"),
        ui.Column("Win rate"),
        ui.Column("95% interval", "md-hide num"),
    ] + ([ui.Column("How it lost", "md-hide")] if any_losses else [])
    rows = []
    for i, seat in enumerate(seats):
        mine = i == 0
        rate = _float(seat.get("win_rate"))
        width = max(0, min(100, round(100 * rate))) if rate is not None else 0
        wins = _int(seat.get("wins")) or 0
        ci = _interval(seat.get("win_rate_95")) or "–"
        lost = _losses_text(seat.get("losses"))
        you = " " + ui.label("your deck", "grey", "user") if mine else ""
        sub = (
            f"<span class='kv'>{wins} {'win' if wins == 1 else 'wins'}</span>"
            f"<span class='kv'>95% interval {_esc(ci)}</span>"
            + (f"<span class='kv'>Lost by {_esc(lost)}</span>" if lost else "")
        )
        rows.append(
            f"<div class='tr{' mine' if mine else ''}'>"
            f"<span class='title'><span class='t'>{_esc(front_face(seat.get('deck')))}{you}</span>"
            f"<span class='sub'>{sub}</span></span>"
            f"<span class='num md-hide'>{wins}</span>"
            "<span class='rate'><span class='meter' aria-hidden='true'>"
            f"<i style='width:{width}%'></i></span><b>{_esc(_pct(rate))}</b></span>"
            f"<span class='num md-hide'>{_esc(ci)}</span>"
            + (f"<span class='md-hide'>{_esc(lost or '–')}</span>" if any_losses else "")
            + "</div>"
        )
    return (
        ui.table_open(
            cols,
            "minmax(0,1fr) 9rem",
            "minmax(0,1fr) 7.5rem",
            cls="rv-seats",
            label="Results for each deck at the table",
        )
        + ui.table_header(head)
        + "".join(rows)
        + "</div>"
    )


def _games_html(res: dict[str, Any], seats: list[dict[str, Any]]) -> str:
    listed = [g for g in res.get("game_list") or [] if isinstance(g, dict)]
    if not listed:
        return ""
    names = {i + 1: front_face(s.get("deck")) for i, s in enumerate(seats)}
    rows = "".join(
        f"<div class='tr'><span class='num'>Game {_int(g.get('game')) or ''}</span>"
        f"<span>{_esc(names.get(g.get('winner'), '–'))}</span>"
        f"<span class='num'>{_esc(_turn(g.get('turn')))}</span>"
        f"<span class='num'>{_esc(_seconds_text(g.get('seconds')))}</span>"
        f"<span class='sm-hide'>{_esc(ENDED_TEXT.get(str(g.get('ended')), '–'))}</span></div>"
        for g in listed
    )
    head = ui.table_header(
        [
            ui.Column("Game"),
            ui.Column("Winner"),
            ui.Column("Turn"),
            ui.Column("Time"),
            ui.Column("Ended", "sm-hide"),
        ]
    )
    table = ui.table_open(
        "5.5rem minmax(0,1fr) 4rem 6rem minmax(0,1fr)",
        "5.5rem minmax(0,1fr) 4rem 6rem minmax(0,1fr)",
        "4.5rem minmax(0,1fr) 3rem 5rem",
        cls="rv-games",
    )
    return (
        f"<details class='rv-det games'><summary>Each game ({len(listed)})</summary>"
        f"{table}{head}{rows}</div></details>"
    )


def _ended_html(f: dict[str, Any], res: dict[str, Any], games: int) -> str:
    ended = res.get("ended") if isinstance(res.get("ended"), dict) else {}
    if not ended and f.get("state") not in ("queued", "running"):
        # a run stored before 0.7.30 counted only draws and games stopped at the limit
        draws, slow = _int(res.get("draws")) or 0, _int(res.get("stopped_slow")) or 0
        ended = {"won": games - draws, "draw": draws - slow, "time_limit": slow}
    rows = "".join(
        f"<li><span>{_esc(ENDED_TEXT[k])}</span><b>{_int(ended.get(k)) or 0}</b></li>"
        for k in ENDED_TEXT
        if _int(ended.get(k))
    )
    if not rows:
        return ""
    note = (
        "<p class='ui-hint'>Forge counts a game that reaches its time limit as a draw. If many games end "
        "this way, Forge had too little time for these decks, and the win rates say less.</p>"
        if _int(ended.get("time_limit"))
        else ""
    )
    return f"<div><h3>How the games ended</h3><ul class='rv-ended'>{rows}</ul>{note}</div>"


def forge_section_html(f: Any, *, live: bool = False, report_id: Any = None, more: str = "") -> str:
    """The Forge section of the signed-in page, or the preflight-only section when the report has
    no Forge games. ``live`` marks an unfinished run for forge-live.js (which swaps this section's
    inside); ``more`` is the Play more games block, an empty holder when it has none."""
    holder = more or "<div id='forge-more'></div>"
    if not isinstance(f, dict) or str(f.get("state") or "") == "skipped":
        if isinstance(f, dict):
            why, warn = str(f.get("note") or ""), str(f.get("error") or "")
        else:
            why = (
                "Forge, the rules engine, was not set up on this gateway, or not asked for, when this "
                "report ran."
            )
            warn = ""
        body = (
            f"<p class='ui-sub'>{_esc(why)} The report holds the deck statistics and, when the research "
            "service ran, the solo test and the validation.</p>"
            if why
            else ""
        )
        if warn:
            body += f"<p class='notice warn'>{_esc(warn)}</p>"
        return _sec("rv-forge", PREFLIGHT_ONLY, body, cls="forge preflight")
    state = str(f.get("state") or "")
    pending = state in ("queued", "running")
    parts = [f"<p class='ui-sub'>{_esc(FORGE_SUB)}</p>"]
    if f.get("more_note"):
        parts.append(f"<p class='notice warn'>{_esc(MORE_DID_NOT_START + str(f['more_note']))}</p>")
    if state != "done":
        msg = forge_stopped_text(f) or f.get("error") or FORGE_STATE_TEXT.get(_forge_key(f), "")
        if msg and pending:
            msg += " This section updates by itself when the games finish." if live else ""
        if msg:
            parts.append(f"<p class='notice{'' if pending else ' warn'}'>{_esc(msg)}</p>")
    res = f.get("result") if isinstance(f.get("result"), dict) else {}
    seats = [s for s in res.get("seats") or [] if isinstance(s, dict)]
    games = _int(res.get("games")) or 0
    lines = forge_lines(f, results=False)
    about = (
        f"<div><h3>{ABOUT_FORGE}</h3><ul class='rv-notes'>"
        + "".join(f"<li>{_esc(ln)}</li>" for ln in lines)
        + "</ul></div>"
        if lines
        else ""
    )
    if games and seats:
        mine = seats[0]
        strip = [
            ("Games played", f"{games} of {_int(res.get('games_requested')) or games}"),
            ("Your deck won", _esc(_pct(_float(mine.get("win_rate"))))),
        ]
        if res.get("median_game_seconds") is not None:
            strip.append(("Median game time", _esc(_seconds_text(res["median_game_seconds"]))))
        else:
            strip.append(("Average game time", _esc(_seconds_text(res.get("average_game_seconds")))))
        if res.get("median_turn") is not None:
            span = res.get("turns_range") if isinstance(res.get("turns_range"), list) else []
            note = f" <small>from turn {_esc(span[0])} to {_esc(span[1])}</small>" if len(span) == 2 else ""
            strip.append(("Median game length", f"turn {_esc(_turn(res['median_turn']))}{note}"))
        parts.append(_strip(strip))
        parts.append(_seats_html(res, seats))
        ended = _ended_html(f, res, games)
        parts.append(f"<div class='rv-two'>{ended}{about}</div>" if ended or about else "")
    elif about:
        parts.append(f"<div class='rv-two'>{about}</div>")
    if f.get("not_played"):
        parts.append(
            "<div class='rv-np'><h3>Cards Forge could not play</h3><p class='ui-hint'>These were left out "
            "of the games, so the numbers above do not include them.</p><p>"
            + ", ".join(_esc(n) for n in f["not_played"])
            + "</p></div>"
        )
    links = []
    ahead = f.get("waiting_for") if isinstance(f.get("waiting_for"), dict) else None
    if live and pending and ahead and ahead.get("report_id"):
        links.append(
            f"<a href='/history/reports/{_esc(ahead['report_id'])}'>Open the report it is waiting for</a>"
        )
    choice = f.get("opponent_choice") if isinstance(f.get("opponent_choice"), dict) else {}
    pair = choice.get("shared_with") or f.get("shared_with")
    if isinstance(pair, dict) and isinstance(pair.get("report_id"), str):
        name = str(pair.get("deck_name") or "the other deck")
        links.append(
            f"<a href='/history/reports/{_esc(pair['report_id'])}'>Open {_esc(name)}'s report, which met "
            "the same opponents</a>"
        )
    parts.extend(f"<p class='rv-link'>{a}</p>" for a in links)
    if games and seats:
        parts.append(_games_html(res, seats))
    parts.append(holder)
    rid = f" data-report-id='{_esc(report_id)}'" if isinstance(report_id, str) and report_id else ""
    attrs = f" data-forge-live aria-live='polite'{rid}" if live and pending else ""
    return _sec(
        "rv-forge", FORGE_HEADING, "".join(parts), label=forge_state_label(f), cls="forge", attrs=attrs
    )


def more_html(f: Any, csrf: str, report_id: str) -> str:
    """Play more games as the screen's one orange button, its line beside it."""
    block = more_games_html(f, csrf, report_id)
    if "<form" not in block:
        return block
    return (
        block.replace("id='forge-more' class='card more-games'", "id='forge-more' class='rv-more'")
        .replace("<button type='submit' class='btn'>", "<button type='submit' class='btn-primary'>")
        .replace("<p class='muted small'>", "<p class='ui-hint'>")
    )


# -- the widgets -------------------------------------------------------------------------
def _ci_bar(lo: float | None, hi: float | None, cls: str = "ci-w") -> str:
    if lo is None or hi is None:
        return ""
    lo, hi = max(0.0, lo), min(1.0, hi)
    return (
        f"<span class='{cls}' aria-hidden='true'>"
        f"<i style='left:{100 * lo:.0f}%;width:{max(1.0, 100 * (hi - lo)):.0f}%'></i></span>"
    )


def _go(text: str, href: str) -> str:
    return ui.button_link(text, href)


def _validation_issues(v: dict[str, Any]) -> tuple[int, str]:
    """How many issues the validation names, and its first line: "2 card(s) not found on
    Scryfall:" counts 2; lines without a count count one each."""
    counted = [(int(m.group(1)), ln) for ln in v.get("lines") or [] if (m := re.match(r"(\d+)\b.*:$", ln))]
    if counted:
        return sum(n for n, _ln in counted), counted[0][1].rstrip(":")
    lines = v.get("lines") or []
    return max(1, len(lines)), (lines[0] if lines else "")


def widgets_html(r: dict[str, Any], s: dict[str, Any], *, stats_section: bool = True) -> str:
    """The key numbers, from what the sections show (no new query). ``stats_section``: the page
    has a Deck statistics section for the Deck widget to point at."""
    g, v = s["goldfish"], s["validation"]
    out: list[str] = []
    f = r.get("forge") if isinstance(r.get("forge"), dict) else None
    res = (f or {}).get("result") if isinstance((f or {}).get("result"), dict) else {}
    seats = [x for x in res.get("seats") or [] if isinstance(x, dict)]
    games = _int(res.get("games")) or 0
    if f and games and seats:
        mine = seats[0]
        low, high = (mine.get("win_rate_95") or [None, None])[:2]
        precons = len(seats) - 1
        det = " · ".join(
            x
            for x in (
                f"95% interval {_interval([low, high])}" if _interval([low, high]) else "",
                f"against {precons} precon{'s' if precons != 1 else ''}" if precons else "",
            )
            if x
        )
        out.append(
            ui.widget(
                "Forge: your deck won",
                "play",
                _pct(_float(mine.get("win_rate"))),
                unit=f"of {games} game{'s' if games != 1 else ''}",
                detail_html=_ci_bar(_float(low), _float(high)) + f"<span class='tx'>{_esc(det)}</span>",
                actions=[_go("Forge games", "#rv-forge")],
            )
        )
    elif f and str(f.get("state")) in ("queued", "running"):
        out.append(
            ui.widget(
                "Forge: your deck won",
                "play",
                "–",
                detail=FORGE_STATE_TEXT.get(_forge_key(f), ""),
                actions=[_go("Forge games", "#rv-forge")],
                tone="accent",
            )
        )
    tiles = {t["label"]: t for t in s["tiles"]}
    cc = tiles.get("Commander cast (median turn)")
    if cc:
        big, _, _ = str(cc["value"]).partition(" (")
        out.append(
            ui.widget(
                "Commander cast",
                "clock",
                big,
                unit="median" if big != "–" else "",
                detail=cc.get("note") or "",
                actions=[_go("Solo test", "#rv-solo")],
            )
        )
    kill = next((t for t in s["tiles"] if " damage dealt by turn " in t["label"]), None)
    if kill:
        out.append(
            ui.widget(kill["label"], "stats", kill["value"], unit="of games", detail=kill.get("note") or "")
        )
    cov = coverage(g) if g else None
    h = (g or {}).get("honesty") or {}
    if cov and (h.get("unrecognized") or h.get("out_of_scope")):
        size = cov["deck_size"]
        blanks = _copies(h.get("unrecognized") or [])
        aside = _copies([c for grp in h.get("out_of_scope") or [] for c in grp["cards"]])
        out.append(
            ui.widget(
                "Played with their effect",
                "alert" if cov["pct"] < COVERAGE_WARN else "check",
                f"{cov['pct']}%",
                unit=f"{cov['modeled']} of {size} cards",
                detail_html=_coverage_bar(cov["modeled"], aside, blanks, size)
                + f"<span class='tx'>{blanks} played without their effect · {aside} left out on "
                "purpose</span>",
                actions=[_go("See the cards", "#rv-model")],
                tone="accent" if cov["pct"] < COVERAGE_WARN else "",
            )
        )
    if v is not None:
        verdict = v.get("verdict")
        if verdict == "issues":
            n, first = _validation_issues(v)
            out.append(
                ui.widget(
                    "Validation",
                    "alert",
                    n,
                    unit="issue found" if n == 1 else "issues found",
                    detail=first,
                    actions=[_go("See them", "#rv-valid")],
                    tone="alert",
                )
            )
        else:
            word, colour, ic = VALIDATION_LABEL.get(str(verdict), ("Checked", "grey", "check"))
            out.append(
                ui.widget(
                    "Validation",
                    "shield",
                    "",
                    big_html=ui.label(word, colour, ic),
                    detail=v.get("headline") or "",
                    actions=[_go("See it", "#rv-valid")],
                    tone="alert" if verdict == "failed" else "",
                )
            )
    stats = r.get("stats") if isinstance(r.get("stats"), dict) else {}
    if stats:
        price = _float(stats.get("price_total"))
        cards = _int(stats.get("card_count"))
        mv = _float(stats.get("average_mana_value"))
        det = " · ".join(
            x
            for x in (
                f"{_int(stats.get('land_count'))} lands" if _int(stats.get("land_count")) is not None else "",
                f"Avg MV {mv:.1f}" if mv is not None else "",
                f"{len(stats.get('game_changers') or [])} game changers",
            )
            if x
        )
        out.append(
            ui.widget(
                "Deck",
                "decks",
                f"${price:,.2f}" if price is not None else (str(cards) if cards is not None else "–"),
                unit=(
                    f"{cards} cards"
                    if price is not None and cards is not None
                    else ("cards" if cards else "")
                ),
                detail=det,
                actions=[_go("Deck statistics", "#rv-stats")] if stats_section else [],
            )
        )
    return (
        f"<section class='ui-widgets rv-w' aria-label='Key numbers'>{''.join(out)}</section>" if out else ""
    )


def _coverage_bar(modeled: int, aside: int, blanks: int, size: int, *, cls: str = "") -> str:
    total = max(1, size)
    parts = (("m", modeled), ("o", aside), ("u", blanks))
    bars = "".join(f"<i class='{k}' style='width:{100 * n / total:.0f}%'></i>" for k, n in parts if n)
    return f"<span class='bar-split rv-cov-bar{(' ' + cls) if cls else ''}' aria-hidden='true'>{bars}</span>"


# -- the sections ------------------------------------------------------------------------
def solo_section_html(
    r: dict[str, Any], s: dict[str, Any], *, removable: frozenset[str], csrf: str
) -> tuple[str, str]:
    """The solo test section and the "what it could not model" section ('' when none)."""
    g, v = s["goldfish"], s["validation"]
    if g is None and v is None and not s["constructed"]:
        body = (
            "<p class='ui-sub'>The research service was not configured when this report ran, so there is no "
            "solo test and no validation: only the deck statistics below.</p>"
        )
        return _sec("rv-solo", solo_heading(g), body), ""
    if g is None:
        return _sec(
            "rv-solo", solo_heading(g), "<p class='ui-sub'>The solo test did not run for this report.</p>"
        ), ""
    if g.get("message") and not g.get("metrics"):
        body = f"<p class='notice warn'>{_esc(g['message'])}</p>"
        return _sec("rv-solo", solo_heading(g), body, label=ui.label("Not run", "red", "alert")), ""
    warn = "".join(f"<p class='notice warn'>{_esc(w)}</p>" for w in g.get("warnings") or [])
    tiles = "".join(_tile(t) for t in s["tiles"])
    charts = "".join(
        f"<figure class='chart rv-chart'><h3>{_esc(t)}</h3><p class='ui-hint'>{_esc(b)}</p>{svg}</figure>"
        for t, b, svg in s["charts"]
    )
    summary = ""
    if g.get("summary"):
        summary = (
            "<details class='rv-det'><summary>The simulator's own summary</summary><ul class='rv-notes'>"
            + "".join(f"<li>{_esc(plain_words(ln))}</li>" for ln in g["summary"])
            + "</ul></details>"
        )
    rid = str(r.get("report_id") or "")
    body = (
        f"<p class='ui-sub'>{_esc(SOLO_SUB)}</p>"
        + warn
        + (f"<div class='tiles wide rv-stats'>{tiles}</div>" if tiles else "")
        + (f"<div class='charts rv-charts'>{charts}</div>" if charts else "")
        + s["triggers"]
        + kept_models_html(
            kept_models(r), removable=removable, csrf=csrf, report_id=rid, button="btn-outline-danger"
        )
        + summary
    )
    solo = _sec("rv-solo", solo_heading(g), body)
    ask = ask_html(
        r,
        s["unmodeled"],
        (g or {}).get("honesty"),
        cls="btn-plain",
        button=f"{icon('copy')}<span class='label'>{_esc('Copy a request for your assistant')}</span>",
    )
    groups = honesty_groups(g, ask)
    if not groups:
        return solo, ""
    h = g.get("honesty") or {}
    head = ""
    cov = coverage(g)
    if cov and (h.get("unrecognized") or h.get("out_of_scope")):
        aside = _copies([c for grp in h.get("out_of_scope") or [] for c in grp["cards"]])
        blanks = _copies(h.get("unrecognized") or [])
        legend = (
            "<ul class='legend rv-legend'>"
            f"<li><i class='m'></i>Played with their effect <b>{cov['modeled']}</b></li>"
            f"<li><i class='o'></i>Left out on purpose <b>{aside}</b></li>"
            f"<li><i class='u'></i>Played without their effect <b>{blanks}</b></li></ul>"
        )
        head = (
            "<div class='rv-cov'>"
            + _coverage_bar(cov["modeled"], aside, blanks, cov["deck_size"], cls="big")
            + legend
            + f"<p class='b coverage'>{_esc(coverage_text(g))}</p></div>"
        )
    elif coverage_text(g) and (h.get("unrecognized") or h.get("out_of_scope")):
        head = f"<p class='b coverage'>{_esc(coverage_text(g))}</p>"
    folds = "".join(
        f"<details class='rv-grp{' unmodeled' if key == 'unmodeled' else ''}'{' open' if i == 0 else ''}>"
        f"<summary><span>{_esc(heading)}</span>"
        + (f"<span class='n'>{_esc(count)}</span>" if count else "")
        + f"</summary>{body}</details>"
        for i, (key, heading, count, body) in enumerate(groups)
    )
    model = _sec(
        "rv-model",
        "What the solo test could not model",
        f"<p class='ui-sub'>{_esc(MODEL_SUB)}</p>{head}{folds}",
    )
    return solo, model


def validation_section_html(s: dict[str, Any]) -> str:
    g, v = s["goldfish"], s["validation"]
    if s["constructed"]:
        return _sec("rv-valid", "Validation", f"<p class='ui-sub'>{_esc(CONSTRUCTED_VALIDATION)}</p>")
    if g is None and v is None:
        return ""
    lab = ""
    if v is not None:
        word, colour, ic = VALIDATION_LABEL.get(str(v.get("verdict")), ("Checked", "grey", "check"))
        lab = ui.label(word, colour, ic)
    body = validation_html(v).replace("<ul class='vlines'>", "<ul class='vlines rv-notes'>")
    return _sec("rv-valid", "Validation", body, label=lab)


def _jump_html(
    r: dict[str, Any], s: dict[str, Any], *, model: bool, valid: bool, stats: bool, raw: bool
) -> str:
    g = s["goldfish"]
    h = (g or {}).get("honesty") or {}
    n = (
        _copies(h.get("unrecognized") or [])
        + _copies([c for grp in h.get("out_of_scope") or [] for c in grp["cards"]])
        + len(h.get("low_impact") or [])
    )
    f = r.get("forge")
    forge = isinstance(f, dict) and f.get("state") != "skipped"
    # a report without Forge games starts with its preflight-only section: no link to it
    items = [ui.Chip("Forge games", None, "#rv-forge")] if forge else []
    items.append(ui.Chip("Solo test", None, "#rv-solo"))
    if model:
        items.append(ui.Chip("What it could not model", n or None, "#rv-model"))
    if valid:
        items.append(ui.Chip("Validation", None, "#rv-valid"))
    if stats:
        items.append(ui.Chip("Deck statistics", None, "#rv-stats"))
    if raw:
        items.append(ui.Chip("Raw output", None, "#rv-raw"))
    return f"<div class='rv-jump'>{ui.chips(items, label='Sections of this report')}</div>"


def status_label_html(status: Any) -> str:
    """The report's overall status as its §4 label (the same words as the Reports list)."""
    return status_label(status) if isinstance(status, str) and status in STATUS_LABEL else ""


def report_page_html(
    r: dict[str, Any],
    *,
    stats_html: str = "",
    actions_html: str = "",
    notice_html: str = "",
    reused: bool = False,
    removable: frozenset[str] = frozenset(),
    csrf: str = "",
) -> str:
    """The signed-in report page's body. ``stats_html`` is the deck statistics strip
    (companion.stats_strip), ``actions_html`` the export buttons, ``notice_html`` a notice from the
    address (a Remove or a Play more games that started nothing)."""
    s = report_sections(r)
    g = s["goldfish"]
    deck_id = _esc(r.get("deck_id"))
    name = _esc(r.get("deck_name") or r.get("deck_id"))
    crumbs = (
        "<nav class='crumbs rv-crumbs' aria-label='Back'>"
        f"<a href='/decks/{deck_id}'>← Back to the deck</a><span aria-hidden='true'>·</span>"
        f"<a href='/history?deck_id={deck_id}'>Deck history</a><span aria-hidden='true'>·</span>"
        "<a href='/reports'>All reports</a></nav>"
    )
    meta = [f"<span id='rep-status'>{status_label_html(r.get('status'))}</span>"]
    if s["commander"]:
        meta.append(f"<span><span class='k'>Commander</span> {_esc(front_face(s['commander']))}</span>")
    if g and g.get("games"):
        turns = f", through turn {g['until_turn']}" if g.get("until_turn") else ""
        seed = f" (seed {g['seed']})" if g.get("seed") is not None else ""
        meta.append(
            f"<span><span class='k'>Solo test games</span> {_esc(g['games'])}{_esc(turns)}{_esc(seed)}</span>"
        )
    meta.append(f"<span><span class='k'>Run</span> {ui.when(r.get('taken_at'))}</span>")
    head = (
        "<header class='rv-head'><div class='rv-title'>"
        f"<h1><a href='/decks/{deck_id}'>{name}</a></h1>"
        f"<p class='rv-meta'>{''.join(meta)}</p></div>"
        f"<div class='rv-acts'>{actions_html}</div></header>"
    )
    if reused:
        notice_html += (
            "<p class='notice'>The deck had not changed since its last report a few minutes ago, so that "
            "report is shown instead of running the simulation again.</p>"
        )
    rid = str(r.get("report_id") or "")
    forge = forge_section_html(
        r.get("forge"), live=True, report_id=r.get("report_id"), more=more_html(r.get("forge"), csrf, rid)
    )
    solo, model = solo_section_html(r, s, removable=removable, csrf=csrf)
    valid = validation_section_html(s)
    stats = (
        _sec("rv-stats", "Deck statistics", f"<div class='rv-dstats'>{stats_html}</div>")
        if stats_html
        else ""
    )
    v = s["validation"]
    raw = ""
    if g and g.get("raw"):
        raw += f"<h3>Simulation output</h3><pre>{_esc(g['raw'])}</pre>"
    if v and v.get("raw"):
        raw += f"<h3>Validation output</h3><pre>{_esc(v['raw'])}</pre>"
    if raw:
        raw = (
            "<details class='ui-card rv-det rv-raw raw' id='rv-raw'><summary>Raw output from the research "
            f"service</summary>{raw}</details>"
        )
    jump = _jump_html(r, s, model=bool(model), valid=bool(valid), stats=bool(stats), raw=bool(raw))
    return (
        "<div class='report rv-page gb-dash'>"
        + crumbs
        + head
        + notice_html
        + widgets_html(r, s, stats_section=bool(stats))
        + jump
        + forge
        + solo
        + model
        + valid
        + stats
        + raw
        + "</div>"
    )


# The page's own styles, scoped to .rv-page (the export keeps report_view.EXPORT_CSS).
REPORT_PAGE_CSS = (
    PHONE_WIDGETS_CSS
    + """
/* one report */
.rv-page .rv-crumbs{display:flex;flex-wrap:wrap;gap:.25rem .5rem;align-items:center;margin:0 0 .25rem;
  font-size:.93rem}
.rv-page .rv-crumbs a{display:inline-flex;align-items:center;min-height:32px}
@media (pointer:coarse){.rv-page .rv-crumbs a{min-height:44px}}
.rv-page .rv-head{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;
  gap:.5rem 1rem;margin:0 0 1rem}
.rv-page .rv-title{min-width:0;flex:1 1 24rem}
.rv-page .rv-title h1{margin:0 0 .3rem;overflow-wrap:anywhere}
.rv-page .rv-title h1 a{color:inherit;text-decoration:none}
.rv-page .rv-title h1 a:hover{color:var(--orange-text)}
.rv-page .rv-meta{display:flex;flex-wrap:wrap;gap:.35rem 1.1rem;align-items:center;margin:0}
.rv-page .rv-meta .k{color:var(--text-muted);margin-right:.2rem}
.rv-page .rv-acts{display:flex;flex-wrap:wrap;gap:.5rem}
.rv-page .rv-acts .btn,.rv-page .rv-acts button{margin:0;display:inline-flex;align-items:center;gap:.35rem}
.rv-page .ci-w{position:relative;display:block;width:100%;height:8px;border-radius:4px;
  background:var(--surface-2)}
.rv-page .ci-w i{position:absolute;top:0;bottom:0;border-radius:4px;background:var(--orange)}
.rv-page .wdg .det .bar-split{width:100%}
.rv-page .bar-split .m,.rv-page .rv-legend .m{background:var(--green)}
.rv-page .bar-split .o,.rv-page .rv-legend .o{background:var(--surface-3)}
.rv-page .bar-split .u,.rv-page .rv-legend .u{background:var(--orange)}
.rv-page .bar-split.big{height:14px}
.rv-page .rv-legend i{width:12px;height:12px;border-radius:2px}
.rv-page .rv-jump{position:sticky;top:0;z-index:5;background:var(--bg);padding:.5rem 0;margin:0 0 .75rem;
  border-bottom:1px solid var(--border-soft)}
.rv-page .rv-jump .ui-chips{margin:0}
.rv-page .rv-sec{scroll-margin-top:4rem;margin:0 0 1rem}
.rv-page .rv-sh{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem .75rem;margin:0 0 .25rem}
.rv-page .rv-sh h2{margin:0;font-size:1.2rem}
.rv-page .rv-sec h3{font-size:1rem;margin:.75rem 0 .35rem}
.rv-page .rv-strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,9.5rem),1fr));
  margin:0 0 1rem;
  border-top:1px solid var(--border-soft);border-bottom:1px solid var(--border-soft)}
.rv-page .rv-strip div{padding:.6rem .8rem;border-left:1px solid var(--border-soft);min-width:0}
.rv-page .rv-strip div:first-child{border-left:0}
.rv-page .rv-strip dt{color:var(--text-muted);font-size:.82rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.03em}
.rv-page .rv-strip dd{margin:.15rem 0 0;font-size:1.35rem;font-weight:900;font-variant-numeric:tabular-nums;
  overflow-wrap:anywhere}
.rv-page .rv-strip dd small{display:block;font-size:.84rem;font-weight:400;color:var(--text-muted)}
@media (max-width:599.98px){.rv-page .rv-strip{grid-template-columns:1fr 1fr}
  .rv-page .rv-strip div{border-left:0;border-top:1px solid var(--border-soft)}
  .rv-page .rv-strip div:nth-child(-n+2){border-top:0}
  .rv-page .rv-strip div:nth-child(even){border-left:1px solid var(--border-soft)}}
.rv-page .rv-sec .ui-table{border:0;border-radius:0;background:transparent;
  border-top:1px solid var(--border-soft);
  border-bottom:1px solid var(--border-soft);margin:0 0 .5rem}
.rv-page .rv-sec .ui-table .th{background:transparent;border-bottom:1px solid var(--border-soft)}
.rv-page .rv-seats .tr.mine{background:var(--orange-tint)}
.rv-page .rv-seats .rate{display:flex;align-items:center;gap:.5rem;font-variant-numeric:tabular-nums}
.rv-page .rv-seats .meter{flex:1;max-width:5rem;height:8px;border-radius:4px;background:var(--surface-2);
  overflow:hidden}
.rv-page .rv-seats .meter i{display:block;height:100%;background:var(--orange)}
.rv-page .rv-seats .kv{color:var(--text-muted);font-size:.86rem}
.rv-page .rv-seats .t .ui-lbl{margin-left:.35rem}
.rv-page .num{font-variant-numeric:tabular-nums}
.rv-page .rv-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,20rem),1fr));
  gap:.5rem 1.5rem;
  margin:0 0 .5rem}
.rv-page .rv-ended{list-style:none;margin:0;padding:0}
.rv-page .rv-ended li{display:flex;justify-content:space-between;gap:1rem;padding:.4rem 0;
  border-top:1px solid var(--border-soft)}
.rv-page .rv-ended li:first-child{border-top:0}
.rv-page .rv-ended b{font-variant-numeric:tabular-nums;white-space:nowrap}
.rv-page .rv-notes{margin:.25rem 0 .5rem;padding-left:1.1rem}
.rv-page .rv-notes li{margin:.2rem 0;overflow-wrap:anywhere}
.rv-page .rv-det,.rv-page .rv-grp{border-top:1px solid var(--border-soft);margin:.5rem 0 0;padding:.25rem 0 0}
.rv-page .rv-grp > summary{cursor:pointer;display:flex;flex-wrap:wrap;align-items:center;gap:.2rem .75rem;
  min-height:44px;font-weight:700;font-size:1.02rem}
.rv-page .rv-grp > summary .n{color:var(--text-muted);font-weight:400;font-size:.9rem}
.rv-page .rv-grp h4{margin:.6rem 0 0;font-size:.95rem}
.rv-page .rv-grp h4 small{color:var(--text-muted);font-weight:400}
.rv-page .rv-games .tr{grid-template-columns:var(--cols)!important}
@media (max-width:599.98px){.rv-page .rv-games .tr{grid-template-columns:var(--cols-sm)!important}
  .rv-page .rv-games .th{display:grid!important}}
.rv-page .rv-more{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem 1rem;margin:.75rem 0 0;
  padding-top:.75rem;
  border-top:1px solid var(--border-soft)}
.rv-page .rv-more form{margin:0}
.rv-page .rv-more button{display:inline-flex;align-items:center;gap:.4rem;margin:0;min-height:44px}
.rv-page .rv-more .ui-hint{flex:1 1 18rem;margin:0}
.rv-page .rv-link{margin:.25rem 0}
.rv-page .rv-link a{display:inline-flex;align-items:center;min-height:32px}
/* the solo test's seven numbers: a compact grid, rules between rows only */
.rv-page .tiles.rv-stats{grid-template-columns:repeat(auto-fill,minmax(min(100%,8.5rem),1fr));gap:0 1rem;
  margin:.75rem 0;border-top:1px solid var(--border-soft)}
.rv-page .rv-stats .tile{background:transparent;border:0;border-radius:0;
  border-bottom:1px solid var(--border-soft);
  padding:.6rem 0;font-size:.84rem}
.rv-page .rv-stats .tile b{font-size:1.4rem}
.rv-page .rv-stats .tile span{font-size:.84rem}
.rv-page .rv-stats .tile > span:nth-of-type(1){color:var(--text);font-weight:700;font-size:.88rem}
.rv-page .rv-stats .tile .ci{background:var(--surface-2)}
.rv-page .charts.rv-charts{grid-template-columns:1fr;gap:.5rem 1.5rem}
@media (min-width:900px){.rv-page .charts.rv-charts{grid-template-columns:1fr 1fr}
  .rv-page .rv-w{grid-template-columns:repeat(3,minmax(0,1fr))}}
.rv-page .rv-chart{margin:0}
.rv-page .rv-chart h3{margin:.5rem 0 .2rem}
.rv-page .rv-chart .ui-hint{margin:0 0 .35rem}
.rv-page .rv-chart svg{max-height:15rem}
@media (max-width:599.98px){.rv-page .rv-chart svg{font-size:18px;padding-left:1.25rem}}
.rv-page .rv-cov{margin:.5rem 0 .75rem}
.rv-page .rv-cov .b{margin:.5rem 0 0}
.rv-page .ask{margin:.5rem 0 .75rem;display:flex;flex-wrap:wrap;align-items:center;gap:.5rem}
.rv-page .ask button{display:inline-flex;align-items:center;gap:.4rem;margin:0}
.rv-page .kept .names button{min-height:44px;margin:0}
/* the deck statistics: the six numbers as a strip, the curve and colours side by side */
.rv-page .rv-dstats .tiles{grid-template-columns:repeat(auto-fit,minmax(min(100%,8rem),1fr));gap:0;
  border-top:1px solid var(--border-soft);border-bottom:1px solid var(--border-soft);margin:.5rem 0 1rem}
.rv-page .rv-dstats .tile{flex-direction:column-reverse;justify-content:flex-end;background:transparent;
  border:0;
  border-radius:0;border-left:1px solid var(--border-soft);padding:.6rem .8rem}
.rv-page .rv-dstats .tile:first-child{border-left:0}
.rv-page .rv-dstats .tile span{font-size:.82rem;font-weight:700;text-transform:uppercase;letter-spacing:.03em}
.rv-page .rv-dstats .tile b{font-size:1.35rem;font-weight:900}
@media (max-width:599.98px){.rv-page .rv-dstats .tiles{grid-template-columns:1fr 1fr}
  .rv-page .rv-dstats .tile{border-left:0;border-top:1px solid var(--border-soft)}
  .rv-page .rv-dstats .tile:nth-child(-n+2){border-top:0}
  .rv-page .rv-dstats .tile:nth-child(even){border-left:1px solid var(--border-soft)}}
.rv-page .rv-raw{padding:.25rem 1rem .5rem}
.rv-page .rv-raw pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.85rem}
.rv-page .rv-np p{overflow-wrap:anywhere}
"""
)
