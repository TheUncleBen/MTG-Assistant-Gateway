"""One stored report as the signed-in page (``/history/reports/{id}``).

Crumbs, a header (the deck as the title, the status as a label, the meta line, the export
buttons), six key-number widgets, a sticky row of section links, then the sections as flat blocks
with one heading each: Forge games, Quick solo test, What the quick solo test leaves out (inset
lists, each group with Details), Validation, Deck statistics and the raw output (folded). One orange
button: Play more games.

Every number and sentence comes from report_view, which also draws the Markdown and the
self-contained HTML export (those keep their own plain layout). Nothing here reads more than the
report row: the widgets repeat numbers the sections show."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from . import card_models, card_models_view, ui
from .report_view import (
    APPROX_HEADING,
    APPROX_TEXT,
    CONSTRUCTED_VALIDATION,
    ENDED_TEXT,
    FORGE_HEADING,
    FORGE_STATE_TEXT,
    LOW_IMPACT_HEADING,
    LOW_IMPACT_TEXT,
    MODEL_HEADING,
    MORE_DID_NOT_START,
    OUT_OF_SCOPE_HEADING,
    OUT_OF_SCOPE_TEXT,
    PREFLIGHT_ONLY,
    TOO_FEW_TEXT,
    UNMODELED_FORGE_TEXT,
    UNMODELED_HEADING,
    UNMODELED_SAVED_TEXT,
    UNMODELED_TEXT,
    _copies,
    _esc,
    _float,
    _forge_key,
    _int,
    _interval,
    _losses_text,
    _pct,
    _seconds_text,
    _share,
    _tile,
    _turn,
    ask_html,
    coverage,
    forge_enough,
    forge_lines,
    forge_solo_done,
    forge_stopped_early,
    forge_stopped_text,
    front_face,
    kept_models,
    kept_models_html,
    model_box_id,
    more_games_html,
    plain_words,
    report_sections,
    solo_heading,
    validation_html,
)
from .reports_page import PHONE_WIDGETS_CSS, STATUS_LABEL, source_label_html, status_label
from .theme import icon

# The "Played with their effect" widget takes the orange edge below this share of the deck.
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
# The summary card's footer: the known share (the cards played with their effect and those
# that need opponents), as coverage_text says it on the export.
KNOWN_TEXT = (
    "Known: {known} of {deck_size} cards ({known_pct}% of the deck), counting the cards played with "
    "their effect and those that need opponents."
)
MODEL_SUB = (
    "Read the numbers above with this in mind: the more of the deck sits here, the less the solo test "
    "says about it."
)
ABOUT_FORGE = "About these Forge games"


def _sec(
    sid: str,
    heading: str,
    body: str,
    *,
    label: str = "",
    cls: str = "",
    attrs: str = "",
    flat: bool = False,
) -> str:
    """One section: its heading (with a label beside it) and its body. ``flat``: a heading over a
    divider line, not a bordered panel (the solo test sections, whose widgets and tables would
    otherwise sit as panels inside a panel)."""
    extra = f" {cls}" if cls else ""
    shell = "rv-sec rv-flat" if flat else "ui-card rv-sec"
    return (
        f"<section class='{shell}{extra}' id='{sid}' aria-labelledby='{sid}-h'{attrs}>"
        f"<div class='rv-sh'><h2 id='{sid}-h'>{_esc(heading)}</h2>{label}</div>{body}</section>"
    )


LEFT_OUT_HEADING = "Cards Forge left out"
LEFT_OUT_LINE = "Not counted above."


def cards_table(
    names: list[Any], *, sid: str, types: Mapping[str, str] | None = None, label: str = ""
) -> str:
    """A table of card names, each row with the shared card menu (right-click, press and hold, ⋮;
    static/report.js). ``types`` gives a card's type line, shown under its name when known."""
    rows = []
    for raw in names:
        name = str(raw or "").strip()
        if not name:
            continue
        shown = front_face(name)
        ty = (types or {}).get(name) or (types or {}).get(shown) or ""
        sub = f"<span class='s'>{_esc(ty)}</span>" if ty else ""
        rows.append(
            f"<div class='tr' tabindex='0' role='button' data-card='{_esc(shown)}' "
            f"aria-label='{_esc(shown)}'>"
            f"<span class='title'><span class='t'>{_esc(shown)}</span>{sub}</span>"
            f"<span class='act'><button type='button' class='kebab' aria-label='Menu for {_esc(shown)}'>"
            f"{icon('more')}</button></span></div>"
        )
    if not rows:
        return ""
    return (
        ui.table_open(
            "minmax(0,1fr) 44px", "minmax(0,1fr) 44px", "minmax(0,1fr) 44px", cls="rv-cards", label=label
        )
        + "".join(rows)
        + "</div>"
    )


def remove_model_boxes(
    used: list[dict[str, Any]], *, removable: frozenset[str], csrf: str, report_id: str
) -> str:
    """One confirm box per saved description the member may remove (POPUP-INVENTORY: a remove asks
    first). The box holds the form that removes; its final button is the solid red one."""
    if not (csrf and report_id):
        return ""
    out = []
    for u in sorted(used, key=lambda u: str(u.get("name") or "").casefold()):
        name = str(u.get("name") or "")
        card = front_face(name)
        if not card or card.casefold() not in removable:
            continue
        bid = model_box_id("rm-model", name)
        fid = f"{bid}-f"
        body = (
            f"<form id='{fid}' method='post' action='/history/reports/{_esc(report_id)}/models/remove'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            f"<input type='hidden' name='card' value='{_esc(name)}'></form>"
            f"<p>Later solo tests play “{_esc(card)}” without its effect again, until a new description "
            "is saved. The reports that already played it keep their numbers.</p>"
        )
        out.append(
            ui.box(
                bid,
                f"Remove the saved description of “{card}”?",
                body,
                actions=[
                    ui.box_button("Keep it", cancel=True),
                    ui.box_button("Remove", kind="danger-solid", form=fid, icon_name="trash"),
                ],
            )
        )
    return "".join(out)


def left_out_html(names: list[Any], *, sid: str, types: Mapping[str, str] | None = None) -> str:
    """ "Cards Forge left out": the cards Forge could not play, as a table with card menus."""
    table = cards_table(names, sid=sid, types=types, label=LEFT_OUT_HEADING)
    if not table:
        return ""
    n = sum(1 for x in names if str(x or "").strip())
    return (
        f"<div class='rv-np'><h3>{LEFT_OUT_HEADING} <span class='n'>{n}</span></h3>"
        f"<p class='ui-hint'>{LEFT_OUT_LINE}</p>{table}</div>"
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
        f"<details class='rv-det games'><summary class='fold-btn'>Each game ({len(listed)})</summary>"
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


def forge_section_html(
    f: Any,
    *,
    live: bool = False,
    report_id: Any = None,
    more: str = "",
    types: Mapping[str, str] | None = None,
) -> str:
    """The Forge section of the signed-in page, or the preflight-only section when the report has
    no Forge games. ``live`` marks an unfinished run for forge-live.js (which swaps this section's
    inside); ``more`` is the Play more games block, an empty holder when it has none; ``types``
    the type lines of the cards Forge left out, when the gateway knows them."""
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
    elif forge_stopped_text(f):  # done, with games Forge gave no result for
        parts.append(f"<p class='notice warn'>{_esc(forge_stopped_text(f))}</p>")
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
        parts.append(left_out_html(list(f["not_played"]), sid="rv-forge", types=types))
    links = []
    ahead = f.get("waiting_for") if isinstance(f.get("waiting_for"), dict) else None
    if live and pending and ahead and ahead.get("report_id"):
        links.append(
            ui.cap_link("Open the report it is waiting for", f"/history/reports/{ahead['report_id']}")
        )
    choice = f.get("opponent_choice") if isinstance(f.get("opponent_choice"), dict) else {}
    pair = choice.get("shared_with") or f.get("shared_with")
    if isinstance(pair, dict) and isinstance(pair.get("report_id"), str):
        name = str(pair.get("deck_name") or "the other deck")
        links.append(
            ui.cap_link(
                f"Open {name}’s report, which met the same opponents", f"/history/reports/{pair['report_id']}"
            )
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
        .replace("<button type='submit' class='btn' ", "<button type='submit' class='btn-primary' ")
        .replace("<p class='muted small'>", "<p class='ui-hint'>")
    )


# -- the solo test played by Forge (the report's ``forge_solo`` block; None when Forge is off) ----
SOLO_FORGE_NAME = "Full-rules solo test by Forge"
SOLO_FORGE_SUB = (
    "Against opponents that do nothing, up to turn {turns}: how fast the deck works on its own, not how "
    "it fares against interaction."
)
SOLO_FORGE_PENDING = " This section updates by itself when the games finish."
SOLO_FORGE_STATE_TEXT = {
    "queued": "Waiting for Forge.",
    "running": "Games are going now.",
    "failed": "The games could not run.",
    "timeout": "The games ran out of time before finishing.",
    "cancelled": "The games were stopped.",
    "lost": "Forge lost this run.",
    "skipped": "No solo test by Forge for this report.",
}
SOLO_FORGE_FINAL = frozenset({"done", "failed", "skipped", "lost", "timeout", "cancelled"})


def forge_solo_pending(fs: Any) -> bool:
    """Whether the solo test by Forge is still queued or running (the page then polls for it)."""
    return isinstance(fs, dict) and str(fs.get("state") or "") in ("queued", "running")


def _by_turn(values: Any, turns: int) -> list[float | None]:
    """A per-turn series (a list from turn 1, or a dict keyed by the turn) as ``turns`` values."""
    out: list[float | None] = [None] * turns
    if isinstance(values, dict):
        for k, v in values.items():
            t = _int(k)
            if t is not None and 1 <= t <= turns:
                out[t - 1] = _float(v)
    elif isinstance(values, list):
        for i, v in enumerate(values[:turns]):
            out[i] = _float(v)
    return out


def _n1(v: float | None) -> str:
    return "–" if v is None else f"{v:.1f}"


def forge_solo_heading(fs: dict[str, Any]) -> str:
    res = fs.get("result") if isinstance(fs.get("result"), dict) else {}
    games = _int(res.get("games")) or _int(fs.get("games_requested")) or _int(res.get("games_requested"))
    return f"{SOLO_FORGE_NAME} ({games} games)" if games else SOLO_FORGE_NAME


def forge_solo_label(fs: dict[str, Any]) -> str:
    """The label beside the heading: "300 of 300 played" once done, Queued, Running, else Not run."""
    state = str(fs.get("state") or "")
    res = fs.get("result") if isinstance(fs.get("result"), dict) else {}
    games = _int(res.get("games")) or 0
    asked = _int(res.get("games_requested")) or _int(fs.get("games_requested")) or games
    if state == "done":
        if games:
            return ui.label(f"{games} of {asked} played", "green", "check")
        return ui.label("No result", "orange", "triangle")
    if state == "queued":
        return ui.label("Queued", "orange", "clock")
    if state == "running":
        return ui.label("Running", "orange", "spinner")
    if games:
        return ui.label("Stopped early", "orange", "triangle")
    return ui.label("Not run", "red", "alert")


def _solo_widgets(res: dict[str, Any], games: int, turns: int) -> str:
    asked = _int(res.get("games_requested")) or games
    out = [
        ui.widget(
            "Games played",
            "layers",
            games,
            unit=f"of {asked}" if asked != games else "",
            detail=f"up to turn {turns} each" if turns else "",
        )
    ]
    won = _int(res.get("won"))
    rate = _float(res.get("won_rate"))
    if rate is None and won is not None and games:
        rate = won / games
    ci = _interval(res.get("won_rate_95"))
    out.append(
        ui.widget(
            "Wins",
            "check",
            _pct(rate) if rate is not None else "–",
            unit=f"{won} of {games}" if won is not None else "",
            detail=f"95% interval {ci}" if ci else "",
        )
    )
    mwt = _float(res.get("median_won_turn"))
    span = res.get("won_turn_range") if isinstance(res.get("won_turn_range"), list) else []
    out.append(
        ui.widget(
            "Median win turn",
            "clock",
            f"Turn {_turn(mwt)}" if mwt is not None else "–",
            detail=f"wins from turn {_turn(span[0])} to {_turn(span[1])}"
            if len(span) == 2
            else ("no game was won" if won == 0 else ""),
        )
    )
    cmd = _float(res.get("median_commander_turn"))
    out.append(
        ui.widget(
            "Commander cast",
            "user",
            f"Turn {_turn(cmd)}" if cmd is not None else "–",
            unit="median" if cmd is not None else "",
        )
    )
    first = _float(res.get("median_first_out_turn"))
    out.append(
        ui.widget(
            "First opponent out",
            "follow",
            f"Turn {_turn(first)}" if first is not None else "–",
            unit="median" if first is not None else "",
        )
    )
    mull = _float(res.get("mulligans"))
    out.append(
        ui.widget(
            "Mulligans",
            "refresh",
            f"{mull:g}" if mull is not None else "–",
            unit=f"in {games} games" if mull is not None and games else "",
            detail=f"{mull / games:.1f} a game on average" if mull is not None and games else "",
        )
    )
    return ui.widgets(out, label="Solo test by Forge")


def _turns_table(res: dict[str, Any], turns: int) -> str:
    lands = _by_turn(res.get("lands_by_turn"), turns)
    casts = _by_turn(res.get("casts_by_turn"), turns)
    dmg = _by_turn(res.get("damage_by_turn"), turns)
    if not any(v is not None for v in lands + casts + dmg):
        return ""
    peak = max((v for v in dmg if v is not None), default=0.0)
    rows = []
    for i in range(turns):
        d = dmg[i]
        width = max(2.0, 100.0 * d / peak) if d is not None and peak > 0 else 0.0
        bar = (
            f"<span class='sf-bc'><span class='bar' style='width:{width:.0f}%' aria-hidden='true'></span>"
            "</span>"
            if d is not None
            else "<span class='sf-bc'></span>"
        )
        rows.append(
            f"<div class='tr'><span class='t'>{i + 1}</span><span class='n'>{_n1(lands[i])}</span>"
            f"<span class='n'>{_n1(casts[i])}</span><span class='n'>{_n1(d)}</span>{bar}</div>"
        )
    head = ui.table_header(
        [
            ui.Column("Turn"),
            ui.Column("Lands", "n"),
            ui.Column("Spells cast", "n"),
            ui.Column("Damage", "n"),
            ui.Column("", "sf-bc"),
        ]
    )
    return (
        f"<h3>Turn by turn (averages, up to turn {turns})</h3>"
        + ui.table_open(
            "4rem repeat(3,minmax(0,1fr)) minmax(0,2fr)",
            "4rem repeat(3,minmax(0,1fr)) minmax(0,1.4fr)",
            "3rem repeat(3,minmax(0,1fr))",
            cls="sf-turns",
            label="Turn by turn",
        )
        + head
        + "".join(rows)
        + "</div>"
    )


def forge_solo_section_html(
    fs: Any, *, live: bool = False, report_id: Any = None, types: Mapping[str, str] | None = None
) -> str:
    """The "Solo test, played by Forge" section: nothing at all when the report has no
    ``forge_solo`` block (Forge off, or a report from before), so such a report renders as before.
    Six widgets, the per-turn table with a damage bar, the cards Forge left out with card menus,
    one muted line for unfinished games; queued, running and failed runs read like the Forge
    section's, and ``live`` marks a pending run for forge-live.js."""
    if not isinstance(fs, dict) or not fs:
        return ""
    state = str(fs.get("state") or "")
    pending = state in ("queued", "running")
    res = fs.get("result") if isinstance(fs.get("result"), dict) else {}
    games = _int(res.get("games")) or 0
    turns = _int(res.get("until_turn")) or 10
    parts = [f"<p class='ui-sub'>{_esc(SOLO_FORGE_SUB.format(turns=turns))}</p>"]
    if state == "done" and not games:
        # finished, but no game counted: say so, never a bare "Done" over nothing
        stopped = _int(res.get("unfinished")) or 0
        why = (
            f" {stopped} {'game was' if stopped == 1 else 'games were'} stopped by the time limit."
            if stopped
            else ""
        )
        parts.append(
            f"<p class='notice warn'>{_esc('No game reached a result, so there are no numbers.' + why)}</p>"
        )
    if state != "done":
        msg = str(fs.get("error") or "") or SOLO_FORGE_STATE_TEXT.get(state, "")
        if games and state not in ("queued", "running"):
            msg = (
                f"Forge stopped after {games} of {_int(res.get('games_requested')) or games} games. "
                f"The numbers below count only the {games} played. {msg}"
            ).strip()
        if msg and pending and live:
            msg += SOLO_FORGE_PENDING
        if msg:
            parts.append(f"<p class='notice{'' if pending else ' warn'}'>{_esc(msg)}</p>")
    if games:
        parts.append(_solo_widgets(res, games, turns))
        parts.append(_turns_table(res, turns))
        unfinished = _int(res.get("unfinished")) or 0
        if unfinished > 0:
            parts.append(
                f"<p class='ui-hint sf-unfinished'>{unfinished} {'game' if unfinished == 1 else 'games'} "
                f"the time limit stopped before turn {turns} or before a win "
                f"{'is' if unfinished == 1 else 'are'} left out of these numbers.</p>"
            )
    if fs.get("not_played"):
        parts.append(left_out_html(list(fs["not_played"]), sid="rv-solo-forge", types=types))
    rid = f" data-report-id='{_esc(report_id)}'" if isinstance(report_id, str) and report_id else ""
    attrs = f" data-forge-live aria-live='polite'{rid}" if live and pending else ""
    return _sec(
        "rv-solo-forge",
        forge_solo_heading(fs),
        "".join(parts),
        label=forge_solo_label(fs),
        cls="solo-forge",
        attrs=attrs,
        flat=True,
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
    if f and games and seats and not forge_enough(f):
        # a few Forge games say little: their widget follows the solo test's, without a headline rate
        # (owner decision C7)
        mine = seats[0]
        low, high = (mine.get("win_rate_95") or [None, None])[:2]
        interval = _interval([low, high])
        later = ui.widget(
            "Forge: your deck won",
            "play",
            "",
            big_html=f"<span class='rv-few'>{_esc(TOO_FEW_TEXT)}</span>",
            detail=(
                f"{games} game{'s' if games != 1 else ''} so far"
                + (f", 95% interval {interval}" if interval else "")
                + ". More games narrow it."
            ),
            actions=[_go("Forge games", "#rv-forge")],
        )
    else:
        later = ""
    if f and games and seats and not later:
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
                + f"<span class='tx'>{blanks} played without their effect · {aside} need opponents</span>",
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
        if not price or stats.get("priced_cards") == 0:
            price = None  # no card had a price: "No price", never a $0 deck
            det = " · ".join(x for x in (det, "No price") if x)
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
    if later:  # after the solo test's numbers, before the deck's
        out.insert((1 if cc else 0) + (1 if kill else 0), later)
    return (
        f"<section class='ui-widgets rv-w' aria-label='Key numbers'>{''.join(out)}</section>" if out else ""
    )


def _coverage_bar(modeled: int, aside: int, blanks: int, size: int, *, cls: str = "") -> str:
    total = max(1, size)
    parts = (("m", modeled), ("u", blanks), ("o", aside))
    bars = "".join(f"<i class='{k}' style='width:{100 * n / total:.0f}%'></i>" for k, n in parts if n)
    return f"<span class='bar-split rv-cov-bar{(' ' + cls) if cls else ''}' aria-hidden='true'>{bars}</span>"


# -- the sections ------------------------------------------------------------------------
TESTING_TEXT = (
    "The solo test is running on the research service. This page fills in by itself when it is done; "
    "you can leave it and come back from Reports."
)


def testing_section_html(r: dict[str, Any], *, live: bool = True) -> str:
    """The solo test's section while the research service is asked (the report is stored first):
    ``live`` marks it for forge-live.js, which reloads the page once the answer is in."""
    rid = str(r.get("report_id") or "")
    attrs = (
        f" data-forge-live data-research aria-live='polite' data-report-id='{_esc(rid)}'"
        if live and rid
        else ""
    )
    return _sec(
        "rv-solo",
        solo_heading(None),
        f"<p class='notice'>{_esc(TESTING_TEXT)}</p>",
        label=ui.label("Running", "orange", "spinner"),
        attrs=attrs,
        flat=True,
    )


def run_again_html(r: dict[str, Any], csrf: str) -> str:
    """Run again, for a report of one of the member's decks as it is (not a list, a proposal or a
    comparison, which an assistant runs): the same full test as the deck page's button."""
    deck_id = str(r.get("deck_id") or "")
    if not csrf or not deck_id.isdigit() or r.get("source"):
        return ""
    return (
        f"<form method='post' action='/decks/{_esc(deck_id)}/report' class='rv-again'>"
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        f"<button type='submit' class='btn-primary' data-busy-label='Starting…'>{icon('stats')} Run again"
        "</button></form>"
    )


def solo_section_html(
    r: dict[str, Any],
    s: dict[str, Any],
    *,
    removable: frozenset[str],
    csrf: str,
    models_opener: str = "",
) -> tuple[str, str]:
    """The solo test section and the "what the quick solo test leaves out" section ('' when none).
    ``models_opener``: the My card models button, placed where saved descriptions are mentioned
    (under the ones this run played, and beside the request for cards played without effect)."""
    g, v = s["goldfish"], s["validation"]
    if r.get("research") == "running":
        return testing_section_html(r), ""
    if g is None and v is None and not s["constructed"]:
        body = (
            "<p class='ui-sub'>The research service was not configured when this report ran, so there is no "
            "solo test and no validation: only the deck statistics below.</p>"
        )
        return _sec("rv-solo", solo_heading(g), body, flat=True), ""
    if g is None:
        return _sec(
            "rv-solo",
            solo_heading(g),
            "<p class='ui-sub'>The solo test did not run for this report.</p>",
            flat=True,
        ), ""
    if g.get("message") and not g.get("metrics"):
        body = f"<p class='notice warn'>{_esc(g['message'])}</p>" + run_again_html(r, csrf)
        return _sec(
            "rv-solo", solo_heading(g), body, label=ui.label("Not run", "red", "alert"), flat=True
        ), ""
    warn = "".join(f"<p class='notice warn'>{_esc(w)}</p>" for w in g.get("warnings") or [])
    tiles = "".join(_tile(t) for t in s["tiles"])
    charts = "".join(
        f"<figure class='chart rv-chart'><h3>{_esc(t)}</h3><p class='ui-hint'>{_esc(b)}</p>{svg}</figure>"
        for t, b, svg in s["charts"]
    )
    summary = ""
    if g.get("summary"):
        summary = (
            "<details class='rv-det'><summary class='fold-btn'>The simulator's own summary</summary>"
            "<ul class='rv-notes'>"
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
            kept_models(r),
            removable=removable,
            csrf=csrf,
            report_id=rid,
            button="btn-outline-danger",
            box_prefix="rm-model",
        )
        + (f"<div class='rv-models'>{models_opener}</div>" if models_opener and kept_models(r) else "")
        + summary
        + remove_model_boxes(kept_models(r), removable=removable, csrf=csrf, report_id=rid)
    )
    solo = _sec("rv-solo", solo_heading(g), body, flat=True)
    ask = ask_html(
        r,
        s["unmodeled"],
        (g or {}).get("honesty"),
        cls="btn-plain",
        button=f"{icon('copy')}<span class='label'>{_esc('Copy a request for your assistant')}</span>",
    )
    model = model_section_html(g, ask, models_opener, forge_full=forge_solo_done(r))
    return solo, model


# -- what the quick solo test leaves out: an inset grouped list (the account settings look) -------
def _rows(cards: list[dict[str, Any]]) -> str:
    """Card rows: the name opens the shared card menu (static/menus.js), its draw rate trails."""
    items = _row_items(cards)
    # an odd last row keeps its partner cell on the two-column desktop grid
    return "".join(items) + ("<li class='fill' aria-hidden='true'></li>" if len(items) % 2 else "")


def _row_items(cards: list[dict[str, Any]]) -> list[str]:
    items = []
    for c in cards:
        copies = f"{c['copies']}× " if isinstance(c.get("copies"), int) and c["copies"] > 1 else ""
        name = front_face(c["name"])
        drawn = f"<span class='v'>drawn {_pct(c['drawn'])}</span>" if c.get("drawn") is not None else ""
        drawn = "<span class='v'>commander</span>" if c.get("commander") else drawn
        items.append(
            f"<li><span class='nm'><button type='button' class='cardlink' data-card='{_esc(name)}'>"
            f"{copies}{_esc(name)}</button></span>{drawn}</li>"
        )
    return items


# A long list shows its first rows and a "Show all" row for the rest (Ben, 10-25: less scrolling).
SHOW_FIRST = 6


def _card(rows: list[str], kind: str, noun: str) -> str:
    """One inset card of ready rows; past SHOW_FIRST + 2 rows, the rest wait behind Show all."""

    def ul(items: list[str]) -> str:
        pad = "<li class='fill' aria-hidden='true'></li>" if kind == "ig-cards" and len(items) % 2 else ""
        return f"<ul class='ig-list {kind}'>{''.join(items)}{pad}</ul>"

    if len(rows) <= SHOW_FIRST + 2:
        return f"<div class='ig'>{ul(rows)}</div>"
    return (
        f"<div class='ig'>{ul(rows[:SHOW_FIRST])}<details class='ig-more'>"
        f"<summary><span class='disc'>Show all {len(rows)} {noun}</span></summary>"
        f"{ul(rows[SHOW_FIRST:])}</details></div>"
    )


def _group(gid: str, heading: str, count: str, info: str, body: str) -> str:
    """One group: its heading and count, with Details opening the group's explanation in an
    outlined box (the whole heading is the toggle), then its list."""
    return (
        f"<section class='ig-sec' id='{gid}' aria-labelledby='{gid}-h'>"
        f"<details class='ig-info'><summary><h3 id='{gid}-h'>{_esc(heading)}</h3>"
        f"<span class='n'>{_esc(count)}</span><span class='disc'>Details</span></summary>"
        f"<div class='ig-blurb'>{info}</div></details>{body}</section>"
    )


def model_section_html(
    g: dict[str, Any], ask: str = "", models_opener: str = "", *, forge_full: bool = False
) -> str:
    """The quick solo test's "leaves out" section: a summary card (the bar and the three counts),
    then one group per kind of card, each a heading with Details and a list of rows; the request
    and My card models as two even buttons under the cards played without their effect."""
    h = g.get("honesty") or {}
    size = g.get("deck_size")
    unrec = h.get("unrecognized") or []
    oos = h.get("out_of_scope") or []
    low = h.get("low_impact") or []
    approx = [a for a in h.get("approximations") or [] if a.get("cards")]
    if not (unrec or oos or low or approx):
        return ""
    parts: list[str] = []
    cov = coverage(g)
    if cov and (unrec or oos):
        aside = _copies([c for grp in oos for c in grp["cards"]])
        blanks = _copies(unrec)
        size_n = cov["deck_size"]

        def row(dot: str, label: str, n: int, href: str = "") -> str:
            val = f"<span class='v'><b>{n}</b> · {round(100 * n / size_n)}%</span>"
            inner = f"<i class='dot {dot}'></i><span class='nm'>{label}</span>{val}"
            if href and n:
                return f"<li><a href='{href}'>{inner}{icon('chev')}</a></li>"
            return f"<li>{inner}<span class='sp'></span></li>"

        parts.append(
            "<div class='ig ig-sum'>"
            + _coverage_bar(cov["modeled"], aside, blanks, size_n, cls="big")
            + "<ul class='ig-list'>"
            + row("m", "Played with their effect", cov["modeled"])
            + row("u", UNMODELED_HEADING, blanks, "#rv-g-unmodeled")
            + row("o", OUT_OF_SCOPE_HEADING, aside, "#rv-g-opp")
            + f"</ul></div><p class='ig-foot'>{_esc(KNOWN_TEXT.format(**cov))}</p>"
        )
    if unrec:
        info = f"<p>{UNMODELED_TEXT}"
        info += f" {UNMODELED_FORGE_TEXT}" if forge_full else ""
        info += f" {UNMODELED_SAVED_TEXT}" if g.get("models_saved") and card_models.SWITCHED_ON else ""
        actions = ""
        if ask:
            actions = f"<div class='ig-actions{' two' if models_opener else ''}'>{ask}{models_opener}</div>"
        parts.append(
            _group(
                "rv-g-unmodeled",
                UNMODELED_HEADING,
                _share(_copies(unrec), size),
                info + "</p>",
                _card(_row_items(unrec), "ig-cards", "cards") + actions,
            )
        )
    if oos:
        total = _copies([c for grp in oos for c in grp["cards"]])
        lists = "".join(
            f"<details class='ig-dis'><summary><span class='nm'>{_esc(grp['label'])}</span>"
            f"<span class='v'>{_copies(grp['cards'])}</span>{icon('chev')}</summary>"
            f"<ul class='ig-list ig-cards'>{_rows(grp['cards'])}</ul></details>"
            for grp in oos
        )
        lists = f"<div class='ig'>{lists}</div>"
        parts.append(
            _group(
                "rv-g-opp", OUT_OF_SCOPE_HEADING, _share(total, size), f"<p>{OUT_OF_SCOPE_TEXT}</p>", lists
            )
        )
    if low:
        parts.append(
            _group(
                "rv-g-low",
                LOW_IMPACT_HEADING,
                _share(len(low), size),
                f"<p>{LOW_IMPACT_TEXT}</p>",
                _card(_row_items([{"name": n} for n in low]), "ig-cards", "cards"),
            )
        )
    if approx:
        rows = [
            f"<li><span class='nm'>{_esc(a['label'])}</span>"
            f"<span class='cards'>{_esc(a['cards'])}</span></li>"
            for a in approx
        ]
        parts.append(
            _group(
                "rv-g-approx",
                APPROX_HEADING,
                f"{len(approx)} shortcut{'s' if len(approx) != 1 else ''}",
                f"<p>{APPROX_TEXT}</p>",
                _card(rows, "ig-approx", "shortcuts"),
            )
        )
    return _sec(
        "rv-model", MODEL_HEADING, f"<p class='ui-sub'>{_esc(MODEL_SUB)}</p>{''.join(parts)}", flat=True
    )


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
    r: dict[str, Any],
    s: dict[str, Any],
    *,
    model: bool,
    valid: bool,
    stats: bool,
    raw: bool,
    forge_first: bool = True,
    tested: bool = False,
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
    items = []
    forge_items = []
    if isinstance(r.get("forge_solo"), dict) and r.get("forge_solo"):
        forge_items.append(ui.Chip("Full-rules solo test", None, "#rv-solo-forge"))
    # a report without Forge games starts with its preflight-only section: no link to it
    if forge:
        forge_items.append(ui.Chip("Forge games", None, "#rv-forge"))
    if isinstance(r.get("paired"), dict):
        items.append(ui.Chip("Against the deck now", None, "#rv-paired"))
    solo_items = [ui.Chip("Quick solo test", None, "#rv-solo")]
    if model:
        solo_items.append(ui.Chip("What the quick test leaves out", n or None, "#rv-model"))
    items += forge_items + solo_items if forge_first else solo_items + forge_items
    if valid:
        items.append(ui.Chip("Validation", None, "#rv-valid"))
    if stats:
        items.append(ui.Chip("Deck statistics", None, "#rv-stats"))
    if tested:
        items.append(ui.Chip("Tested list", None, "#rv-tested"))
    if raw:
        items.append(ui.Chip("Raw output", None, "#rv-raw"))
    return f"<div class='rv-jump'>{ui.chips(items, label='Sections of this report')}</div>"


def comparison_section_html(r: dict[str, Any]) -> str:
    """A simulated comparison's paired A/B (compare_decks with simulate): which decks, and the
    research service's deltas as it wrote them."""
    ab = r.get("comparison")
    if not isinstance(ab, dict):
        return ""
    src = r.get("source") if isinstance(r.get("source"), dict) else {}
    if r.get("research") == "running":
        return testing_section_html(r).replace("id='rv-solo'", "id='rv-compare'", 1)
    body = ab_html(ab, str(src.get("a") or "Deck A"), str(src.get("b") or "Deck B"))
    return _sec("rv-compare", "Comparison: paired solo test", body)


# -- what the test kept and how it compares (owner decision C7) ---------------------------------------
def _card_moves(diff: dict[str, list[dict[str, Any]]], *, most: int = 8) -> str:
    """ "+1 Opt, −2 Island (maybeboard)…" for a list_diff: what came in, then what went."""
    items = [("+", r) for r in diff.get("added") or []] + [("−", r) for r in diff.get("removed") or []]
    words = [
        f"<span class='mv {'in' if sign == '+' else 'out'}'>{sign}{int(r['quantity'])} "
        f"{_esc(front_face(r['name']))}{' (side)' if r.get('zone') == 'side' else ''}</span>"
        for sign, r in items[:most]
    ]
    more = len(items) - most
    return ", ".join(words) + (f" and {more} more" if more > 0 else "")


RESTORE_LIST_TEXT = "Put this list back (review first)"


def deck_changes_html(
    r: dict[str, Any], tested: list[dict[str, Any]] | None, now: Any, *, csrf: str, diff: Any = None
) -> str:
    """The line under the report's head: whether the deck has changed since this test and how, with
    a button that puts the tested list back through an ordinary proposal (reviewed before anything
    changes on Archidekt). ``now``: the deck as read now; ``diff``: list_diff(tested, now's rows)."""
    rid = _esc(r.get("report_id") or "")
    if tested is None:  # a report made before reports kept their list: the fingerprint alone
        if getattr(now, "fingerprint", None) is None or now.fingerprint() == r.get("fingerprint"):
            return ""
        return (
            "<div class='rv-changed' role='status'><p><strong>The deck has changed since this test.</strong> "
            "This report was made before reports kept the list they played, so it can't say how.</p></div>"
        )
    if not diff or not (diff.get("added") or diff.get("removed")):
        return (
            "<div class='rv-changed same' role='status'><p>"
            f"{icon('check')}The deck is as this test played it.</p></div>"
        )
    button = (
        f"<form method='post' action='/history/reports/{rid}/restore' class='rv-restore'>"
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        f"<button type='submit' class='btn-plain' data-busy-label='Making the proposal…'>"
        f"{icon('undo')}{_esc(RESTORE_LIST_TEXT)}</button></form>"
        if csrf
        else ""
    )
    return (
        "<div class='rv-changed' role='status'><p><strong>The deck has changed since this test:</strong> "
        f"{_card_moves(diff)}.</p>{button}</div>"
    )


CHANGES_CHECKING = "Looking at the deck for changes since this test…"


def changes_placeholder_html(report_id: Any) -> str:
    """The changes line while the deck's read is still under way: report.js asks again."""
    rid = _esc(report_id or "")
    return (
        f"<div class='rv-changed wait' data-changes-url='/history/reports/{rid}/changes' aria-live='polite'>"
        f"<p class='ui-hint'>{_esc(CHANGES_CHECKING)}</p></div>"
    )


SINCE_SAME = "The same solo test numbers as last time."


def since_last_html(
    r: dict[str, Any],
    prev: dict[str, Any] | None,
    *,
    tested: list[dict[str, Any]] | None = None,
) -> str:
    """ "Since your last test": this test's solo test numbers against the deck's test before it (the
    same game and last turn), each with its move and whether it is better or worse, and the cards
    that changed between the two lists. '' when there is no such test."""
    from .report_view import compared_numbers, since_rows
    from .reports import list_diff

    if not prev:
        return ""
    rows = since_rows(compared_numbers(r.get("goldfish")), compared_numbers(prev.get("goldfish")))
    before = prev.get("tested")
    moves = list_diff(before, tested) if before is not None and tested is not None else None
    if not rows and not moves:
        return ""
    items = []
    for x in rows:
        value = f"turn {_turn(x['now'])}" if x["unit"] == "turn" else f"{x['now']:.0f}%"
        was = f"turn {_turn(x['before'])}" if x["unit"] == "turn" else f"{x['before']:.0f}%"
        way = {"better": "Better", "worse": "Worse"}.get(x["way"], "")
        mark = {"better": "check", "worse": "alert"}.get(x["way"], "")
        items.append(
            f"<li class='{_esc(x['way'])}'><span class='k'>{_esc(x['label'])}</span>"
            f"<b>{_esc(value)}</b><span class='was'>was {_esc(was)}</span>"
            f"<span class='mv'>{icon(mark) if mark else ''}{_esc(x['words'])}"
            f"{f' · {way}' if way else ''}</span></li>"
        )
    cards = ""
    if moves is not None:
        cards = (
            f"<p class='rv-since-cards'><span class='k'>Cards</span> {_card_moves(moves)}</p>"
            if moves["added"] or moves["removed"]
            else "<p class='rv-since-cards'><span class='k'>Cards</span> The same list both times.</p>"
        )
    href = f"/history/reports/{_esc(prev['report_id'])}"
    return (
        "<section class='rv-since' aria-labelledby='rv-since-h'>"
        "<div class='rv-sh'><h2 id='rv-since-h'>Since your last test</h2>"
        f"<span class='ui-sub'>{ui.when(prev.get('taken_at'))}</span>"
        f"{ui.cap_link('Open that test', href)}</div>"
        + (
            f"<p class='rv-since-same'>{_esc(SINCE_SAME)}</p>"
            if rows and all(x["way"] == "same" for x in rows)
            else f"<ul class='rv-since-l'>{''.join(items)}</ul>"
            if items
            else ""
        )
        + cards
        + "</section>"
    )


def tested_list_html(rows: list[dict[str, Any]] | None, text: str | None) -> str:
    """The list this test played, folded: the commander, the deck and the side rows, with Copy."""
    if not rows:
        return ""
    main = [x for x in rows if x.get("z") != "side"]
    side = [x for x in rows if x.get("z") == "side"]
    commanders = [x for x in main if x.get("cmd")]
    deck = [x for x in main if not x.get("cmd")]

    def group(title: str, part: list[dict[str, Any]]) -> str:
        if not part:
            return ""
        n = sum(int(x["q"]) for x in part)
        lis = "".join(
            f"<li><span class='q'>{int(x['q'])}</span><button type='button' class='cardlink' "
            f"data-card='{_esc(front_face(x['n']))}'>{_esc(x['n'])}</button></li>"
            for x in sorted(part, key=lambda x: str(x["n"]).casefold())
        )
        return f"<h3>{_esc(title)} <span class='n'>{n}</span></h3><ul class='rv-list'>{lis}</ul>"

    total = sum(int(x["q"]) for x in main)
    copy = (
        "<button type='button' class='btn-plain copybtn' data-copy='rep-list'>"
        f"{icon('copy')}<span class='label'>Copy the list</span></button>"
        f"<textarea id='rep-list' class='sr-only' readonly aria-label='The tested list'>{_esc(text or '')}"
        "</textarea>"
        if text
        else ""
    )
    body = (
        f"<details class='rv-det' id='rv-tested-d'><summary class='fold-btn'>Show the {total} cards this "
        "test played</summary>"
        + group("Commander", commanders)
        + group("Deck", deck)
        + group("Sideboard and maybeboard", side)
        + f"<div class='rv-acts2'>{copy}</div></details>"
    )
    return _sec("rv-tested", "Tested list", body, flat=True)


def ab_html(block: Any, a_name: str, b_name: str, *, who: str = "") -> str:
    """A paired A/B's result: one row per number with its change (deck A minus deck B), whether it
    is clear or within chance, then the research service's own text folded away."""
    from .report_view import CHECK_FAILED, ab_deltas, ab_verdict, research_failure

    if not isinstance(block, dict):
        return ""
    if not block.get("ok"):
        why = research_failure(block, RESEARCH_PAIR_FAILED) or plain_words(
            str(block.get("text") or "The paired test did not run.")
        )
        return f"<p class='notice error'>{_esc(why if why != CHECK_FAILED else RESEARCH_PAIR_FAILED)}</p>"
    rows = ab_deltas(block)
    table = ""
    if rows:
        lis = "".join(
            f"<li class='{_esc(x['way'])}'><span class='k'>{_esc(x['label'])}</span>"
            f"<span class='mv'>{_esc(x['words'])}"
            + (
                {"better": " · Better", "worse": " · Worse"}.get(x["way"], "")
                if x["clear"] is not False
                else ""
            )
            + "</span><span class='cl'>"
            + ("Clear difference" if x["clear"] else "Could be chance" if x["clear"] is False else "")
            + "</span></li>"
            for x in rows
        )
        verdict = ab_verdict(rows)
        table = (f"<p class='rv-verdict'>{_esc(verdict)}</p>" if verdict else "") + (
            f"<ul class='rv-since-l rv-ab-l'>{lis}</ul>"
        )
    raw = (
        "<details class='rv-det'><summary class='fold-btn'>The research service's own text</summary>"
        f"<pre class='rv-ab'>{_esc(block.get('text') or '')}</pre></details>"
        if block.get("text")
        else ""
    )
    names = (
        f"<p class='ui-sub'>{who}Deck A: <strong>{_esc(a_name)}</strong> · Deck B: <strong>{_esc(b_name)}"
        "</strong>. Both played game for game under the same seeds; each change is deck A against deck B."
        "</p>"
    )
    if not rows:  # an answer whose numbers the gateway can't read: its own text, open
        raw = f"<pre class='rv-ab'>{_esc(block.get('text') or '')}</pre>"
    return names + table + raw


RESEARCH_PAIR_FAILED = "The paired test could not run: the research service did not answer."


def paired_section_html(r: dict[str, Any]) -> str:
    """A proposal's test: the deck after the change against the deck as it is now, game for game."""
    from .reports import PAIRED_A, PAIRED_B

    block = r.get("paired")
    if not isinstance(block, dict):
        return ""
    return _sec(
        "rv-paired",
        "Against the deck now: paired solo test",
        ab_html(block, PAIRED_A, PAIRED_B),
    )


# -- a Forge pair, head to head (owner decision C7) -----------------------------------------------
HEAD_TO_HEAD_SUB = (
    "Both decks played Forge games against the same opponents, so their win rates can be set side by "
    "side. These games stay out of each deck's own trend."
)


def _side_html(r: dict[str, Any]) -> str:
    """One deck of a pair: its name, its Forge state, its win rate with the 95% interval (or why there
    is none yet) and a way to its report."""
    f = r.get("forge") if isinstance(r.get("forge"), dict) else {}
    res = f.get("result") if isinstance(f.get("result"), dict) else {}
    seats = [x for x in res.get("seats") or [] if isinstance(x, dict)]
    games = _int(res.get("games")) or 0
    rid = _esc(r.get("report_id") or "")
    name = _esc(r.get("deck_name") or "Unnamed deck")
    if games and seats:
        mine = seats[0]
        low, high = (mine.get("win_rate_95") or [None, None])[:2]
        interval = _interval([low, high])
        big = (
            f"<b class='h2h-rate'>{_pct(_float(mine.get('win_rate')))}</b>"
            f"<span class='ui-sub'>won, {games} game{'s' if games != 1 else ''}</span>"
            if forge_enough(f)
            else f"<b class='h2h-few'>{_esc(TOO_FEW_TEXT)}</b><span class='ui-sub'>{games} game"
            f"{'s' if games != 1 else ''} so far, {_pct(_float(mine.get('win_rate')))} won</span>"
        )
        bar = _ci_bar(_float(low), _float(high)) + (
            f"<span class='ui-sub'>95% interval {_esc(interval)}</span>" if interval else ""
        )
    else:
        big = f"<b class='h2h-few'>{_esc(FORGE_STATE_TEXT.get(_forge_key(f), '') or 'No games yet')}</b>"
        bar = ""
    return (
        f"<section class='ui-card h2h-side' aria-labelledby='h2h-{rid}'>"
        f"<h2 id='h2h-{rid}'>{name}</h2><p class='h2h-st'>{forge_state_label(f) if f else ''}</p>"
        f"<div class='h2h-big'>{big}</div><div class='h2h-ci'>{bar}</div>"
        f"<p>{ui.button_link('Open its report', f'/history/reports/{rid}')}</p></section>"
    )


def head_to_head_verdict(a: dict[str, Any], b: dict[str, Any]) -> str:
    """One sentence on a pair: which deck won more often when the two 95% intervals do not overlap,
    else that the games can't tell them apart yet."""

    def rate(r: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
        f = r.get("forge") if isinstance(r.get("forge"), dict) else {}
        res = f.get("result") if isinstance(f.get("result"), dict) else {}
        seats = [x for x in res.get("seats") or [] if isinstance(x, dict)]
        if not (_int(res.get("games")) and seats):
            return None, None, None
        low, high = (seats[0].get("win_rate_95") or [None, None])[:2]
        return _float(seats[0].get("win_rate")), _float(low), _float(high)

    ra, rb = rate(a), rate(b)
    if None in (*ra, *rb):
        return "The verdict comes once both decks have played their games."
    na, nb = (f"“{x.get('deck_name') or 'Unnamed deck'}”" for x in (a, b))
    if ra[1] > rb[2]:  # type: ignore[operator]
        return f"{na} won more often than {nb} against this table."
    if rb[1] > ra[2]:  # type: ignore[operator]
        return f"{nb} won more often than {na} against this table."
    return (
        "The two win rates overlap, so these games can't tell the decks apart yet. Play more games: "
        "both decks get them."
    )


def head_to_head_html(a: dict[str, Any], b: dict[str, Any], *, csrf: str = "") -> str:
    """Both reports of a Forge pair side by side, the verdict, the shared table and Play more games."""
    fa = a.get("forge") if isinstance(a.get("forge"), dict) else {}
    res = fa.get("result") if isinstance(fa.get("result"), dict) else {}
    seats = [x for x in res.get("seats") or [] if isinstance(x, dict)]
    table = [str(x.get("deck") or "") for x in seats[1:] if x.get("deck")]
    if not table:
        start = fa.get("seats") if isinstance(fa.get("seats"), list) else []
        table = [str(x) for x in start[1:]]
    opponents = (
        "<section class='h2h-table'><h2>The table</h2><ul class='rv-list'>"
        + "".join(f"<li>{_esc(n)}</li>" for n in table)
        + "</ul></section>"
        if table
        else ""
    )
    live = any(
        isinstance(x.get("forge"), dict) and str(x["forge"].get("state")) in ("queued", "running")
        for x in (a, b)
    )
    marks = (
        " data-pair-live "
        + " ".join(f"data-pair-{i}='{_esc(x.get('report_id') or '')}'" for i, x in enumerate((a, b)))
        if live
        else ""
    )
    more = more_html(fa, csrf, str(a.get("report_id") or "")) if csrf else ""
    back = ui.cap_link("← Back to the report", f"/history/reports/{_esc(b.get('report_id') or '')}")
    return (
        f"<div class='h2h-page gb-dash'{marks}><p class='hy-back'>{back}</p>"
        + ui.page_head("Head to head", HEAD_TO_HEAD_SUB)
        + f"<p class='rv-verdict' role='status'>{_esc(head_to_head_verdict(a, b))}</p>"
        + f"<div class='h2h-sides'>{_side_html(a)}{_side_html(b)}</div>"
        + more
        + opponents
        + "</div>"
    )


HEAD_TO_HEAD_CSS = """
.h2h-page .h2h-sides{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,18rem),1fr));gap:1rem;
  margin:0 0 1rem}
.h2h-page .h2h-side h2{margin:0 0 .25rem;font-size:1.15rem;overflow-wrap:anywhere}
.h2h-page .h2h-st{margin:0 0 .5rem;min-height:24px}
.h2h-page .h2h-big{display:flex;flex-wrap:wrap;align-items:baseline;gap:.25rem .6rem}
.h2h-page .h2h-rate{font-size:2.4rem;font-weight:900;font-variant-numeric:tabular-nums;line-height:1.1}
.h2h-page .h2h-few{font-size:1.15rem;line-height:1.3}
.h2h-page .h2h-ci{display:flex;flex-direction:column;gap:.25rem;margin:.5rem 0}
.h2h-page .ci-w{position:relative;display:block;width:100%;height:8px;border-radius:4px;
  background:var(--surface-2)}
.h2h-page .ci-w i{position:absolute;top:0;bottom:0;border-radius:4px;background:var(--orange)}
.h2h-page .rv-verdict{font-weight:700;margin:.5rem 0 1rem}
.h2h-page .rv-more{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem 1rem;margin:0 0 1rem}
.h2h-page .rv-more form{margin:0}
.h2h-page .rv-more button{display:inline-flex;align-items:center;gap:.4rem;margin:0;min-height:44px}
.h2h-page .rv-list{list-style:none;margin:0;padding:0}
.h2h-page .rv-list li{min-height:44px;display:flex;align-items:center;border-top:1px solid var(--border-soft)}
.h2h-page .rv-list li:first-child{border-top:0}
.h2h-page .h2h-side .btn{display:inline-flex;align-items:center;min-height:44px}
"""


def status_label_html(status: Any, research: Any = None) -> str:
    """The report's overall status as its status label (the same words as the Reports list)."""
    if not (isinstance(status, str) and status in STATUS_LABEL):
        return ""
    return status_label(status, research=research)


def report_page_html(
    r: dict[str, Any],
    *,
    stats_html: str = "",
    actions_html: str = "",
    notice_html: str = "",
    reused: bool = False,
    removable: frozenset[str] = frozenset(),
    csrf: str = "",
    card_types: Mapping[str, str] | None = None,
    my_models: str = "",
    by_html: str = "",
    changes_html: str = "",
    since_html: str = "",
    tested: list[dict[str, Any]] | None = None,
    pair_href: str = "",
) -> str:
    """The signed-in report page's body. ``stats_html`` is the deck statistics strip
    (companion.stats_strip), ``actions_html`` the export buttons, ``notice_html`` a notice from the
    address (a Remove or a Play more games that started nothing), ``card_types`` the type lines the
    gateway knows for the cards Forge left out, ``my_models`` the My card models box
    (card_models_view.box_html), whose opener sits where saved descriptions are mentioned.
    Owner decision C7: ``by_html`` who ran it (the app chip), ``changes_html`` whether the deck changed
    since (deck_changes_html or its placeholder), ``since_html`` the "since your last test" strip,
    ``tested`` the rows it played (the Tested list), ``pair_href`` its head-to-head view."""
    s = report_sections(r)
    g = s["goldfish"]
    deck_id = _esc(r.get("deck_id"))
    name = _esc(r.get("deck_name") or r.get("deck_id") or "Unnamed deck")
    # a pasted list (or a new deck's proposal) has no deck to go back to
    on_deck = str(r.get("deck_id") or "").isdigit()
    crumbs = (
        "<nav class='crumbs rv-crumbs' aria-label='Back'>"
        + (
            f"<a href='/decks/{deck_id}'>← Back to the deck</a><span aria-hidden='true'>·</span>"
            f"<a href='/history?deck_id={deck_id}'>Deck history</a><span aria-hidden='true'>·</span>"
            if on_deck
            else ""
        )
        + "<a href='/reports'>All reports</a></nav>"
    )
    meta = [f"<span id='rep-status'>{status_label_html(r.get('status'), r.get('research'))}</span>"]
    src = r.get("source") if isinstance(r.get("source"), dict) else {}
    if src.get("kind") == "proposal" and src.get("proposal_id"):
        meta.append(
            f"<span><a href='/proposals/{_esc(src['proposal_id'])}'>{source_label_html(r)}</a></span>"
        )
    elif src.get("kind") == "pair" and pair_href:
        meta.append(f"<span><a href='{_esc(pair_href)}'>{source_label_html(r)}</a></span>")
    elif src:
        meta.append(f"<span>{source_label_html(r)}</span>")
    if s["commander"]:
        meta.append(f"<span><span class='k'>Commander</span> {_esc(front_face(s['commander']))}</span>")
    if g and g.get("games"):
        turns = f", through turn {g['until_turn']}" if g.get("until_turn") else ""
        seed = f" (seed {g['seed']})" if g.get("seed") is not None else ""
        meta.append(
            f"<span><span class='k'>Solo test games</span> {_esc(g['games'])}{_esc(turns)}{_esc(seed)}</span>"
        )
    meta.append(f"<span><span class='k'>Run</span> {ui.when(r.get('taken_at'))}</span>")
    if by_html:
        meta.append(f"<span><span class='k'>By</span> {by_html}</span>")
    head = (
        "<header class='rv-head'><div class='rv-title'>"
        + (f"<h1><a href='/decks/{deck_id}'>{name}</a></h1>" if on_deck else f"<h1>{name}</h1>")
        + f"<p class='rv-meta'>{''.join(meta)}</p></div>"
        f"<div class='rv-acts'>{actions_html}</div></header>"
    )
    if reused:
        notice_html += (
            "<p class='notice'>The deck had not changed since its last report a few minutes ago, so that "
            "report is shown instead of running the simulation again.</p>"
        )
    rid = str(r.get("report_id") or "")
    forge = forge_section_html(
        r.get("forge"),
        live=True,
        report_id=r.get("report_id"),
        more=more_html(r.get("forge"), csrf, rid),
        types=card_types,
    )
    solo_forge = forge_solo_section_html(
        r.get("forge_solo"), live=True, report_id=r.get("report_id"), types=card_types
    )
    if r.get("research") == "running":
        # Forge's parts start once the solo test is in: nothing to say about them yet (not "Forge was
        # not set up"); the page is read again then
        forge = solo_forge = ""
    solo, model = solo_section_html(
        r, s, removable=removable, csrf=csrf, models_opener=card_models_view.opener() if my_models else ""
    )
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
            "<details class='ui-card rv-det rv-raw raw' id='rv-raw'><summary class='fold-btn'>"
            f"Raw output from the research service</summary>{raw}</details>"
        )
    compare = comparison_section_html(r)
    paired = paired_section_html(r)
    if pair_href and forge:  # a Forge pair: both decks side by side on their own view
        forge += (
            "<p class='rv-pair'>"
            + ui.button_link("See both decks head to head", pair_href, icon_name="swap")
            + "</p>"
        )
    # The solo test leads until Forge has enough games to say something (owner decision C7)
    lead_forge = forge_enough(r.get("forge"))
    tested_html = tested_list_html(tested, r.get("tested_list"))
    jump = _jump_html(
        r,
        s,
        model=bool(model),
        valid=bool(valid),
        stats=bool(stats),
        raw=bool(raw),
        forge_first=lead_forge,
        tested=bool(tested_html),
    )
    parts = [solo_forge, forge, solo, model] if lead_forge else [solo, model, solo_forge, forge]
    return (
        "<div class='report rv-page gb-dash'>"
        + crumbs
        + head
        + notice_html
        + changes_html
        + since_html
        + widgets_html(r, s, stats_section=bool(stats))
        + jump
        + compare
        + paired
        + "".join(parts)
        + valid
        + stats
        + tested_html
        + raw
        + my_models
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
.rv-page .bar-split .m,.rv-page .dot.m{background:var(--green)}
.rv-page .bar-split .o,.rv-page .dot.o{background:var(--control-border)}
.rv-page .bar-split .u,.rv-page .dot.u{background:var(--orange)}
.rv-page .bar-split.big{height:14px}
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
/* the table narrower than 20rem (a phone at large text, the smallest phones): the win rate goes
   under the deck's name with a longer meter, instead of squeezing the name to a letter a line */
.rv-page .rv-seats{container-type:inline-size}
@container (max-width:20rem){
  .rv-page .rv-seats .tr{grid-template-columns:minmax(0,1fr)}
  .rv-page .rv-seats .th{display:none}
  .rv-page .rv-seats .rate{margin-top:.3rem}
  .rv-page .rv-seats .meter{max-width:10rem} }
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
.rv-page .rv-det{border-top:1px solid var(--border-soft);margin:.5rem 0 0;padding:.25rem 0 0}
/* what the quick solo test leaves out: inset grouped lists, as on the account settings pages */
.rv-page .ig{list-style:none;margin:.5rem 0 0;padding:0;background:var(--group-bg);
  border:1px solid var(--border-soft);border-radius:12px;overflow:hidden}
.rv-page .ig-list{list-style:none;margin:0;padding:0}
.rv-page .ig.ig-list{margin:.5rem 0 0}
.rv-page .ig-list li{display:flex;align-items:center;gap:.75rem;min-height:44px;padding:0 16px;
  border-top:1px solid var(--border-soft)}
.rv-page .ig-list li:first-child{border-top:0}
.rv-page .ig-list .nm{flex:1;min-width:0;overflow-wrap:anywhere}
.rv-page .ig-list .v{color:var(--text-muted);font-size:.9rem;font-variant-numeric:tabular-nums;
  white-space:nowrap}
.rv-page .ig-list .v b{color:var(--text)}
.rv-page .ig-list a{display:flex;align-items:center;gap:.75rem;flex:1;align-self:stretch;color:inherit;
  text-decoration:none}
.rv-page .ig-list a svg{transform:rotate(-90deg);color:var(--text-muted);width:16px;height:16px;flex:none}
.rv-page .ig-list a:hover .nm{text-decoration:underline}
.rv-page .ig-list .dot{width:12px;height:12px;border-radius:50%;flex:none}
.rv-page .ig-cards .cardlink{display:block;width:100%;min-height:44px;text-align:left}
.rv-page .ig .cardlink:focus-visible,.rv-page .ig-list a:focus-visible{outline:2px solid var(--focus);
  outline-offset:-2px}
.rv-page .ig-sum .bar-split{margin:16px 16px 8px}
.rv-page .ig-foot{margin:.4rem 16px 0;font-size:.85rem;line-height:1.4;color:var(--text-muted)}
.rv-page .ig-sec{margin:1.75rem 0 0}
.rv-page .ig-info > summary{list-style:none;cursor:pointer;display:flex;flex-wrap:wrap;align-items:baseline;
  gap:.1rem .6rem;padding:.35rem 16px;min-height:44px;align-content:center;box-sizing:border-box}
.rv-page .ig-info > summary::-webkit-details-marker{display:none}
.rv-page .ig-info > summary:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:6px}
.rv-page .ig-info h3{margin:0;font-size:1.05rem;font-weight:700;flex-basis:100%}
.rv-page .ig-info .n{color:var(--text-muted);font-size:.9rem}
.rv-page .ig-blurb{margin:.25rem 0 .5rem;padding:10px 14px;border:1px solid var(--border);border-radius:12px;
  font-size:.92rem;line-height:1.45}
.rv-page .ig-blurb p{margin:0}
.rv-page .ig-sub{margin:1rem 16px .35rem;font-size:.78rem;font-weight:600;letter-spacing:.05em;
  text-transform:uppercase;color:var(--text-muted)}
.rv-page .ig-sub span{font-weight:400}
.rv-page .ig-info + .ig-sub{margin-top:.35rem}
.rv-page .ig-more > summary,.rv-page .ig-dis > summary{list-style:none;cursor:pointer;display:flex;
  align-items:center;gap:.75rem;min-height:44px;padding:0 16px;border-top:1px solid var(--border-soft)}
.rv-page .ig-more > summary::-webkit-details-marker,
.rv-page .ig-dis > summary::-webkit-details-marker{display:none}
.rv-page .ig-more > summary{justify-content:center;min-height:52px}
.rv-page .ig-more[open] > summary{display:none}
.rv-page .ig-more > ul{border-top:1px solid var(--border-soft)}
.rv-page .ig-dis:first-child > summary{border-top:0}
.rv-page .ig-dis > summary .nm{flex:1;min-width:0;font-weight:600}
.rv-page .ig-dis > ul li{padding-left:28px}
.rv-page .ig-dis > summary .v{color:var(--text-muted);font-size:.9rem}
.rv-page .ig-dis svg{width:16px;height:16px;color:var(--text-muted);transform:rotate(-90deg);
  transition:transform .15s}
.rv-page .ig-dis[open] > summary svg{transform:none}
.rv-page .ig-dis > ul{border-top:1px solid var(--border-soft);background:var(--bg)}
.rv-page .ig-more > summary:focus-visible,.rv-page .ig-dis > summary:focus-visible{
  outline:2px solid var(--focus);outline-offset:-2px}
.rv-page .ig-approx li{flex-direction:column;align-items:stretch;gap:.15rem;padding:10px 16px}
.rv-page .ig-approx .cards{color:var(--text-muted);font-size:.9rem;line-height:1.4}
.rv-page .ig-actions{display:grid;grid-template-columns:minmax(0,1fr);gap:.5rem;margin:.75rem auto 0;
  max-width:46rem}
.rv-page .ig-actions.two{grid-template-columns:repeat(2,minmax(0,1fr))}
.rv-page .ig-actions .ask{position:relative;display:flex;margin:0}
.rv-page .ig-actions .ask [role=status]{position:absolute;top:100%;left:0;right:0;text-align:center}
.rv-page .ig-actions button,.rv-page .ig-actions .btn{display:flex;align-items:center;justify-content:center;
  gap:.4rem;width:100%;min-height:48px;margin:0;padding:6px 10px;white-space:normal;text-align:center;
  line-height:1.25;box-sizing:border-box}
.rv-page .ig-actions svg{flex:none}
@media (max-width:480px){.rv-page .ig-actions.two{grid-template-columns:minmax(0,1fr)}}
.rv-page .ig-cards li.fill{display:none}
.rv-page .ig-list .sp{width:16px;flex:none}
@media (min-width:600px){.rv-page .ig-info h3{flex-basis:auto}}
@media (min-width:900px){
  .rv-page .ig-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}
  .rv-page .ig-cards li:nth-child(2){border-top:0}
  .rv-page .ig-cards li:nth-child(even){border-left:1px solid var(--border-soft)}
  .rv-page .ig-cards li.fill{display:flex}
  .rv-page .ig-sum .ig-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}
  .rv-page .ig-sum .ig-list li{border-top:0}
  .rv-page .ig-sum .ig-list li + li{border-left:1px solid var(--border-soft)}
  .rv-page .ig-sum .ig-list .sp{display:none}
  .rv-page .ig-approx li{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,3fr);gap:1rem;
    align-items:center}
}
.rv-page .rv-games .tr{grid-template-columns:var(--cols)!important}
@media (max-width:599.98px){.rv-page .rv-games .tr{grid-template-columns:var(--cols-sm)!important}
  .rv-page .rv-games .th{display:grid!important}}
.rv-page .rv-more{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem 1rem;margin:.75rem 0 0;
  padding-top:.75rem;
  border-top:1px solid var(--border-soft)}
.rv-page .rv-more form{margin:0}
.rv-page .rv-more button{display:inline-flex;align-items:center;gap:.4rem;margin:0;min-height:44px}
.rv-page .rv-more .ui-hint{flex:1 1 18rem;margin:0}
.rv-page .rv-again{margin:.5rem 0 0}
.rv-page .rv-again button{display:inline-flex;align-items:center;gap:.4rem;margin:0;min-height:44px}
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
.rv-page .rv-raw pre,.rv-page .rv-ab{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.85rem}
.rv-page .rv-np p{overflow-wrap:anywhere}
.rv-page .rv-np h3 .n,.rv-page .rv-sec h3 .n{color:var(--text-muted);font-weight:400;font-size:.9rem;
  margin-left:.3rem}
/* the solo test sections: a heading over a divider line, not a panel (their widgets and tables are
   panels of their own) */
.rv-page .rv-flat{border-top:2px solid var(--card-border);padding-top:.75rem;margin:0 0 1.25rem}
.rv-page .rv-flat .ui-widgets{margin:.75rem 0 .5rem}
.rv-page .rv-flat .rv-sh h2{font-size:1.25rem}
.rv-page .rv-flat .ui-table{border:1px solid var(--card-border);border-radius:var(--radius-panel);
  background:var(--surface)}
.rv-page .rv-flat .ui-table .th{background:var(--surface-2);border-bottom:0}
@media (min-width:900px){.rv-page .solo-forge .ui-widgets{grid-template-columns:repeat(3,minmax(0,1fr))}}
/* turn by turn: four short numbers and a damage bar */
.rv-page .sf-turns .tr{min-height:40px}
.rv-page .sf-turns .n{text-align:right;font-variant-numeric:tabular-nums}
.rv-page .sf-turns .th .n{text-align:right}
.rv-page .sf-turns .bar{display:block;height:10px;border-radius:2px;background:var(--orange);opacity:.8}
@media (max-width:599.98px){.rv-page .sf-turns .sf-bc{display:none}.rv-page .sf-turns .th{display:grid}}
.rv-page .sf-unfinished{margin:.25rem 0 .75rem}
/* the cards Forge left out: a row per card with the shared card menu */
.rv-page .rv-cards .tr{cursor:pointer;padding-top:0;padding-bottom:0}
.rv-page .rv-cards .tr:hover,.rv-page .rv-cards .tr:focus-visible{background:var(--surface-2)}
.rv-page .rv-cards .title .s{display:block;color:var(--text-muted);font-size:.86rem}
.rv-page .rv-cards .kebab{margin:0}
.rv-page .cardlink{background:none;border:0;padding:0;margin:0;color:inherit;cursor:pointer;font:inherit;
  text-align:left;min-height:0}
.rv-page .cardlink:hover{text-decoration:underline;color:var(--orange-text)}
/* owner decision C7: has the deck changed since, since the last test, the tested list, paired tests */
.rv-page .rv-changed{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem 1rem;margin:0 0 1rem;
  padding:.6rem .9rem;border-left:4px solid var(--orange);background:var(--orange-tint);border-radius:6px}
.rv-page .rv-changed p{margin:0;flex:1 1 20rem;overflow-wrap:anywhere}
.rv-page .rv-changed.same{border-left-color:var(--green);background:var(--green-tint)}
.rv-page .rv-changed.same p{display:flex;align-items:center;gap:.4rem}
.rv-page .rv-changed.wait{border-left-color:var(--border);background:transparent}
.rv-page .rv-changed form{margin:0}
.rv-page .rv-changed button{display:inline-flex;align-items:center;gap:.4rem;margin:0;min-height:44px}
.rv-page .mv.in{color:var(--green-text);font-weight:700}
.rv-page .mv.out{color:var(--red-text);font-weight:700}
.rv-page .rv-since{margin:0 0 1rem;padding:.75rem 0 .25rem;border-top:2px solid var(--card-border)}
.rv-page .rv-since .rv-sh h2{font-size:1.1rem}
.rv-page .rv-since .ui-cap{margin-left:auto}
.rv-page .rv-since-l{list-style:none;margin:.5rem 0;padding:0;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(min(100%,13rem),1fr));gap:0 1rem;
  border-top:1px solid var(--border-soft)}
.rv-page .rv-since-l li{display:flex;flex-wrap:wrap;align-items:baseline;gap:.15rem .5rem;padding:.55rem 0;
  border-bottom:1px solid var(--border-soft);min-width:0}
.rv-page .rv-since-l .k{flex-basis:100%;color:var(--text-muted);font-size:.86rem;font-weight:700}
.rv-page .rv-since-l b{font-size:1.2rem;font-variant-numeric:tabular-nums}
.rv-page .rv-since-l .was{color:var(--text-muted);font-size:.86rem}
.rv-page .rv-since-l .mv,.rv-page .rv-since-l .cl{flex-basis:100%;display:flex;align-items:center;gap:.3rem;
  font-size:.9rem}
.rv-page .rv-since-l .cl{color:var(--text-muted)}
.rv-page .rv-since-l .mv svg{width:16px;height:16px;flex:none}
.rv-page .rv-since-l li.better .mv{color:var(--green-text);font-weight:700}
.rv-page .rv-since-l li.worse .mv{color:var(--red-text);font-weight:700}
.rv-page .rv-since-l li.same .mv{color:var(--text-muted)}
.rv-page .rv-ab-l li b{display:none}
.rv-page .rv-since-cards{margin:.25rem 0 .5rem;overflow-wrap:anywhere}
.rv-page .rv-since-cards .k{color:var(--text-muted);font-weight:700;margin-right:.35rem}
.rv-page .rv-verdict{font-weight:700;margin:.5rem 0}
.rv-page .rv-few{font-size:1.05rem;font-weight:700;line-height:1.25}
.rv-page .rv-pair{margin:-.5rem 0 1.25rem}
.rv-page .rv-pair .btn{display:inline-flex;align-items:center;gap:.4rem;min-height:44px}
.rv-page .rv-list{list-style:none;margin:0 0 .5rem;padding:0;columns:16rem auto;column-gap:1.5rem}
.rv-page .rv-list li{display:flex;gap:.5rem;align-items:baseline;break-inside:avoid;min-height:32px;
  padding:.15rem 0}
@media (pointer:coarse){.rv-page .rv-list li{min-height:44px;align-items:center}}
.rv-page .rv-list .q{min-width:1.6rem;text-align:right;color:var(--text-muted);
  font-variant-numeric:tabular-nums}
.rv-page .rv-list .cardlink{overflow-wrap:anywhere}
.rv-page #rv-tested h3 .n{color:var(--text-muted);font-weight:400;font-size:.9rem;margin-left:.3rem}
.rv-page .rv-acts2{display:flex;flex-wrap:wrap;gap:.5rem;margin:.5rem 0}
.rv-page .rv-acts2 button{display:inline-flex;align-items:center;gap:.4rem;margin:0;min-height:44px}
"""
)
