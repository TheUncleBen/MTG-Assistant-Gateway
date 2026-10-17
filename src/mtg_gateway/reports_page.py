"""The Reports page (/reports, under More; T-152, Q28, K8 in Q136): every stored deck report of the
member, newest first, with its status and the engines that ran; what Forge is playing for them now
and what waits, with the expected times; a deck filter; and each deck's trend in History, where
reports stay listed too. Reads the gateway's own database only: Archidekt is never asked."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from .bracket_view import ESTIMATE_LABEL, bracket_name
from .theme import esc, icon, time_html

PAGE = 25
# A report's overall status (reports.report_status) as the badge shows it.
STATUS_BADGE = {
    "complete": ("ok", "Complete"),
    "running": ("info", "Forge playing"),
    "queued": ("info", "Forge waiting"),
    "partial": ("warn", "Partial"),
    "failed": ("danger", "Failed"),
}


def _minutes(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        return ""
    minutes = max(1, round(seconds / 60))
    return f"about {minutes} minute{'s' if minutes != 1 else ''}"


def engines_text(r: dict[str, Any]) -> str:
    """What ran for a report, in the page's words (K7: the 300 automatic games are the solo test)."""
    parts = []
    if r.get("has_goldfish"):
        games = (r.get("settings") or {}).get("games")
        parts.append(f"Solo test ({games} games)" if isinstance(games, int) else "Solo test")
    if r.get("engine") == "forge":
        parts.append("Forge games")
    if r.get("has_validation"):
        parts.append("legality check")
    parts.append("statistics")
    text = ", ".join(parts)
    return text[:1].upper() + text[1:]


def queue_html(runs: list[dict[str, Any]]) -> str:
    """The member's unfinished Forge runs: the one playing, then the ones waiting in turn."""
    if not runs:
        return ""
    items = []
    for run in runs:
        f = run["forge"]
        link = f"<a href='/history/reports/{esc(run['report_id'])}'>{esc(run['deck_name'] or 'A deck')}</a>"
        done, total = f.get("games_done") or 0, f.get("games_requested") or 0
        if f.get("waiting"):
            when = _minutes(f.get("expected_start_seconds"))
            what = f"Waiting: number {esc(f.get('position') or 1)} in line" + (
                f"; starts in {when}" if when else ""
            )
            if f.get("engine_busy"):
                what = "Waiting for Forge, which is busy with other members' games"
        else:
            left = _minutes(f.get("expected_seconds"))
            what = f"Playing: {esc(done)} of {esc(total)} games" + (f"; done in {left}" if left else "")
        items.append(f"<li>{link}<span class='muted'>{what}</span></li>")
    return (
        "<section class='card rp-queue' aria-labelledby='rp-queue-h'>"
        "<h2 id='rp-queue-h'>Forge now</h2>"
        "<p class='muted small'>Your Forge games play one report at a time, in the order you ran them. "
        "Times are estimates from the games played so far.</p>"
        f"<ol class='rp-runs'>{''.join(items)}</ol></section>"
    )


def filter_html(decks: dict[str, str], deck_id: str) -> str:
    options = "<option value=''>All decks</option>" + "".join(
        f"<option value='{esc(did)}'{' selected' if did == deck_id else ''}>{esc(name or f'Deck {did}')}"
        "</option>"
        for did, name in sorted(decks.items(), key=lambda kv: (str(kv[1]).casefold(), kv[0]))
    )
    return (
        "<form method='get' action='/reports' class='rp-filter'>"
        "<label for='rp-deck'>Deck</label>"
        f"<select id='rp-deck' name='deck_id'>{options}</select>"
        f"<button type='submit' class='btn'>{icon('filter')} Show</button>"
        "</form>"
    )


def row_html(r: dict[str, Any]) -> str:
    kind, label = STATUS_BADGE.get(str(r.get("status")), ("", str(r.get("status") or "")))
    rid, did = esc(r["report_id"]), str(r.get("deck_id") or "")
    trend = "?" + urlencode({"deck_id": did, "type": "reports"})
    # the bracket set on the deck is THE bracket; the estimate shows only without one (Q49, O14)
    if r.get("bracket_set"):
        bracket = f" · Bracket: {esc(bracket_name(r['bracket_set']))}"
    elif r.get("bracket_estimate"):
        bracket = f" · {ESTIMATE_LABEL}: {esc(bracket_name(r['bracket_estimate']))}"
    else:
        bracket = ""
    return (
        "<li class='rp-row'><div class='rp-main'>"
        f"<a class='rp-deck' href='/history/reports/{rid}'>{esc(r.get('deck_name') or f'Deck {did}')}</a>"
        f"<span class='badge {kind}'>{esc(label)}</span></div>"
        f"<p class='rp-meta'>{time_html(r.get('taken_at'))} · {esc(engines_text(r))}" + bracket + "</p>"
        f"<a class='rp-trend' href='/history{esc(trend)}'>{icon('stats')} Deck trend and history</a>"
        "</li>"
    )


def reports_page_html(
    rows: list[dict[str, Any]],
    *,
    runs: list[dict[str, Any]],
    decks: dict[str, str],
    deck_id: str,
    offset: int,
    has_more: bool,
) -> str:
    intro = (
        "<p class='muted'>Your stored deck reports, newest first (the gateway keeps the newest 200). Each "
        "opens the full report; History lists them too.</p>"
    )
    if rows:
        listing = f"<ul class='rp-list'>{''.join(row_html(r) for r in rows)}</ul>"
    elif offset:
        listing = "<p>No older reports.</p>"
    elif deck_id:
        listing = "<p>No reports for this deck yet. Run one from the deck's page.</p>"
    else:
        listing = (
            "<p>No reports yet. Open one of your decks and press <strong>Run full test</strong>, or ask "
            "your assistant to test a deck.</p>"
        )
    base = {"deck_id": deck_id} if deck_id else {}
    pager = []
    if offset:
        newer = {**base, **({"offset": offset - PAGE} if offset > PAGE else {})}
        pager.append(
            f"<a class='btn' href='/reports{esc('?' + urlencode(newer) if newer else '')}'>Newer</a>"
        )
    if has_more:
        older = {**base, "offset": offset + PAGE}
        pager.append(f"<a class='btn' href='/reports?{esc(urlencode(older))}'>Older</a>")
    return (
        "<div class='reports-page'>"
        + intro
        + queue_html(runs)
        + "<section class='card rp-all' aria-labelledby='rp-all-h'><div class='rp-head'>"
        "<h2 id='rp-all-h'>Reports</h2>"
        + (filter_html(decks, deck_id) if decks else "")
        + "</div>"
        + listing
        + (f"<nav class='rp-pager' aria-label='Pages'>{''.join(pager)}</nav>" if pager else "")
        + "</section></div>"
    )


REPORTS_PAGE_CSS = """
.reports-page .rp-head{display:flex;flex-wrap:wrap;gap:.5rem 1rem;align-items:center;
  justify-content:space-between;margin-bottom:.25rem}
.reports-page .rp-head h2{margin:0}
.reports-page .rp-filter{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:0;max-width:100%}
.reports-page .rp-filter label{display:inline;margin:0;font-weight:600}
.reports-page .rp-filter select:not(.msel-native){width:auto;min-width:0;max-width:min(18rem,100%);
  min-height:44px;margin:0}
.reports-page .rp-filter .btn{min-height:44px;margin:0}
.reports-page .rp-filter .msel-btn{width:auto;min-width:min(11rem,100%);max-width:min(18rem,100%);height:44px}
/* one line beside the heading where there is room; a fixed minimum there, as a percentage one
   makes the filter measure narrower than its contents and wrap the Show button */
@media (min-width:600px){
  .reports-page .rp-filter{flex-wrap:nowrap}
  .reports-page .rp-filter .msel-btn{min-width:11rem}
}
.reports-page .rp-list,.reports-page .rp-runs{list-style:none;margin:0;padding:0}
.reports-page .rp-row{display:grid;grid-template-columns:minmax(0,1fr);gap:.2rem;padding:.75rem 0;
  border-top:1px solid var(--border-soft)}
.reports-page .rp-row:first-child{border-top:0}
.reports-page .rp-main{display:flex;flex-wrap:wrap;align-items:baseline;gap:.25rem .6rem}
.reports-page .rp-deck{font-weight:600;overflow-wrap:anywhere;min-width:0}
.reports-page .rp-meta{margin:0;color:var(--text-muted);font-size:.93rem;overflow-wrap:anywhere}
.reports-page .rp-trend{font-size:.93rem;display:inline-flex;align-items:center;gap:.35rem;min-height:44px;
  justify-self:start}
.reports-page .rp-runs li{display:flex;flex-direction:column;gap:.15rem;padding:.5rem 0;
  border-top:1px solid var(--border-soft)}
.reports-page .rp-runs li:first-child{border-top:0}
.reports-page .rp-runs a{font-weight:600;overflow-wrap:anywhere}
.reports-page .rp-pager{display:flex;gap:.5rem;justify-content:flex-end;margin-top:.75rem}
@media (min-width:720px){
  .reports-page .rp-row{grid-template-columns:minmax(0,1fr) auto;column-gap:1rem;align-items:center}
  .reports-page .rp-main,.reports-page .rp-meta{grid-column:1}
  .reports-page .rp-trend{grid-column:2;grid-row:1 / 3;justify-self:end}
}
"""
