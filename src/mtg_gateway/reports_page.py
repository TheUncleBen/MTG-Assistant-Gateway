"""The Reports page (/reports): a dashboard of the member's stored deck reports.

Four widgets (what Forge plays or waits for now, the latest report, the partial or failed ones,
the decks tested with their newest lethal turn), then every report as a table: chips with counts,
a search over deck and commander, filters for the deck, who asked and when, and paging. Each row
shows the status as a label, Forge's win rate against precons with its 95% interval, the solo
test's median turn for lethal damage, the app that asked and when it ran. History lists the
reports too. Reads the gateway's own database only: Archidekt is never asked.

The route (companion_tools.py) reads ``ReportService.index`` once (at most 200 light rows) and the
full summaries of the shown page only; everything else here is worked out from those."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from . import ui
from .bracket_view import ESTIMATE_LABEL, bracket_name
from .theme import esc, icon

PAGE = ui.PER_PAGE
BROWSER_CLIENT = "__browser__"  # decks.BROWSER_CLIENT: the member's own browser
YOU = "You"

# A report's overall status (reports.report_status) as its status label: Complete green, Forge running or
# waiting orange (running), Partial orange (partly done), Failed red.
STATUS_LABEL = {
    "complete": ("Complete", "green", "check"),
    "running": ("Forge running", "orange", "spinner"),
    "queued": ("Forge waiting", "orange", "clock"),
    "partial": ("Partial", "orange", "triangle"),
    "failed": ("Failed", "red", "alert"),
}
# The research service's part still being asked (reports.ReportService._summary "research"), and a
# report whose quick solo test or validation failed while its Forge games still play.
TESTING_LABEL = ("Solo test running", "orange", "spinner")
PARTIAL_GOING_LABEL = {
    "running": ("Partial, Forge running", "orange", "triangle"),
    "queued": ("Partial, Forge waiting", "orange", "triangle"),
}
# The chips: the query value, the words, and which reports each holds
CHIPS = (
    ("", "All"),
    ("complete", "Complete"),
    ("going", "Running or waiting"),  # the solo test or Forge still going
    ("problem", "Partial or failed"),
    ("forge", "With Forge games"),
)
WHEN = (("", "Any time"), ("today", "Today"), ("7", "Last 7 days"), ("30", "Last 30 days"))
# Forge games that ended without a game played
FORGE_NOT_RUN = frozenset({"failed", "timeout", "cancelled", "lost"})
QUEUE_NOTE = "Your Forge games play one report at a time, in the order you ran them."
SUB_LINE = (
    "Every deck report you or your assistants ran, newest first. The gateway keeps your newest 200; "
    "History lists them too."
)


@dataclass(frozen=True)
class Query:
    """What the address asks for: the chip, the search, the filters, the order and the page."""

    show: str = ""
    q: str = ""
    deck_id: str = ""
    by: str = ""
    when: str = ""
    order: str = ""
    offset: int = 0

    @classmethod
    def parse(cls, qp: Any) -> Query:
        def get(k: str, n: int) -> str:
            return str(qp.get(k) or "").strip()[:n]

        try:
            offset = max(0, min(int(qp.get("offset") or 0), 2000))
        except ValueError:
            offset = 0
        show = get("show", 20)
        when = get("when", 10)
        return cls(
            show=show if show in {c[0] for c in CHIPS} else "",
            q=get("q", 100),
            deck_id=get("deck_id", 40),
            by=get("by", 100).casefold(),
            when=when if when in {w[0] for w in WHEN} else "",
            order="oldest" if get("order", 10) == "oldest" else "",
            offset=offset,
        )

    def params(self, **change: Any) -> dict[str, str]:
        """The address's values with ``change`` applied, without the empty ones."""
        cur = {
            "show": self.show,
            "q": self.q,
            "deck_id": self.deck_id,
            "by": self.by,
            "when": self.when,
            "order": self.order,
            "offset": str(self.offset) if self.offset else "",
        }
        cur.update({k: str(v) if v else "" for k, v in change.items()})
        return {k: v for k, v in cur.items() if v}

    def href(self, **change: Any) -> str:
        p = self.params(**change)
        return "/reports" + ("?" + urlencode(p) if p else "")

    @property
    def filtered(self) -> bool:
        return bool(self.q or self.deck_id or self.by or self.when)


def asked_by(row: dict[str, Any], apps: dict[str, str]) -> str:
    """Who asked for a report: "You" (the member's browser), the app's name, or "" (none recorded)."""
    client = row.get("created_by_client")
    if client == BROWSER_CLIENT:
        return YOU
    return apps.get(str(client or ""), "")


def in_chip(row: dict[str, Any], chip: str) -> bool:
    status = row.get("status")
    return {
        "": True,
        "complete": status == "complete",
        "going": status in ("running", "queued"),
        "problem": status in ("partial", "failed"),
        "forge": bool(row.get("forge_played")),
    }[chip]


def _since(when: str, now: float) -> float | None:
    if when == "today":
        return now - now % 86400  # the start of today (UTC)
    if when in ("7", "30"):
        return now - int(when) * 86400
    return None


def matches(row: dict[str, Any], query: Query, apps: dict[str, str], now: float) -> bool:
    """Whether a report passes the search and the filters (not the chip)."""
    if query.deck_id and str(row.get("deck_id")) != query.deck_id:
        return False
    if query.by and asked_by(row, apps).casefold() != query.by:
        return False
    since = _since(query.when, now)
    if since is not None and (row.get("taken_at") or 0) < since:
        return False
    if query.q:
        needle = query.q.casefold()
        hay = [str(row.get("deck_name") or "")] + [str(c) for c in row.get("commanders") or []]
        if not any(needle in h.casefold() for h in hay):
            return False
    return True


def run_text(f: dict[str, Any]) -> str:
    """One unfinished Forge run in words: its place in line, or its games played so far (counts,
    never a time: the gateway can't know how long Forge's games will take)."""
    if f.get("waiting"):
        if f.get("engine_busy"):
            return "Waiting for Forge, which is busy with other members' games"
        return f"Waiting: number {f.get('position') or 1} in line"
    done, total = f.get("games_done") or 0, f.get("games_requested") or 0
    return f"{done} of {total} games played"


def status_label(status: Any, *, cls: str = "", research: Any = None) -> str:
    """A report's status label. ``research`` (the summary's): "running" while the solo test is being
    asked for, "failed" when it or the validation did not come back."""
    if research == "running":
        word, colour, ic = TESTING_LABEL
    elif research == "failed" and status in PARTIAL_GOING_LABEL:
        word, colour, ic = PARTIAL_GOING_LABEL[str(status)]
    else:
        word, colour, ic = STATUS_LABEL.get(str(status), (str(status or ""), "grey", "clock"))
    return ui.label(word, colour, ic, cls=cls)


def _pct(v: float | None) -> str:
    return "–" if v is None else f"{100 * v:.0f}%"


def _turn(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return ""
    return f"{v:.0f}" if v == int(v) else f"{v:.1f}"


def source_label_html(r: dict[str, Any]) -> str:
    """What a report was run on when it is not a stored deck as it is (a pasted list, a waiting
    proposal's resulting deck, a comparison), as a blue label; "" for a deck's own report."""
    src = r.get("source")
    label = src.get("label") if isinstance(src, dict) else r.get("source_label")
    return f"<span class='ui-lbl blue src'>{esc(str(label))}</span>" if label else ""


def deck_line(r: dict[str, Any]) -> str:
    """The row's second line: the commander, then the one thing to know about this report (Forge
    games under way, Forge games that could not run, which checks it holds) or else the bracket."""
    state = r.get("forge_state")
    status = r.get("status")
    if r.get("research") == "running":
        what = "The solo test is running"
    elif status == "running":
        what = esc(f"{r.get('forge_done') or 0} of {r.get('forge_asked') or 0} Forge games played")
    elif status == "queued":
        what = "Waiting for Forge"
    elif state in FORGE_NOT_RUN and not r.get("forge_done"):
        what = "The Forge games could not run"
    elif not r.get("has_goldfish") and r.get("engine") != "forge" and r.get("has_validation"):
        what = "Legality check and statistics only"
    elif r.get("constructed"):
        what = "<span class='muted'>Commander check only</span>"
    elif not r.get("has_validation"):
        what = "<span class='muted'>No legality check</span>"
    elif r.get("bracket_set"):
        what = esc(f"Bracket: {bracket_name(r['bracket_set'])}")
    elif r.get("bracket_estimate"):
        what = f"{ESTIMATE_LABEL}: {esc(bracket_name(r['bracket_estimate']))}"
    else:
        what = ""
    parts = [source_label_html(r)] if source_label_html(r) else []
    if r.get("commander"):
        parts.append(esc(r["commander"]))
    if what:
        parts.append(what)
    return " · ".join(parts)


def _ci_bar(win: dict[str, Any]) -> str:
    lo, hi = win.get("lo"), win.get("hi")
    if lo is None or hi is None:
        return ""
    lo, hi = max(0.0, float(lo)), min(1.0, float(hi))
    return (
        f"<span class='ci' title='95% interval {_pct(lo)} to {_pct(hi)}' aria-hidden='true'>"
        f"<i style='left:{100 * lo:.1f}%;width:{max(1.0, 100 * (hi - lo)):.1f}%'></i></span>"
    )


FEW_GAMES_CELL = "{n} games, too few"


def won_cell(r: dict[str, Any]) -> tuple[str, str]:
    """(the Won vs precons cell, the phone line's short value)."""
    win = r.get("win")
    if win and win.get("enough", True):
        return f"<span class='md-hide wr'><b>{_pct(win['rate'])}</b>{_ci_bar(win)}</span>", _pct(win["rate"])
    if win:  # a few games say little (owner decision C7): no headline rate
        games = int(win.get("games") or 0)
        return f"<span class='md-hide muted'>{FEW_GAMES_CELL.format(n=games)}</span>", "–"
    state = r.get("forge_state")
    if r.get("research") == "running":
        text = "waiting"
    elif r.get("status") == "running":
        text = "playing"
    elif r.get("status") == "queued":
        text = "waiting"
    elif state in FORGE_NOT_RUN:
        text = "did not run"
    else:
        text = "no Forge"
    return f"<span class='md-hide muted'>{text}</span>", "–"


def damage_cell(r: dict[str, Any]) -> tuple[str, str]:
    turn = _turn(r.get("kill_median_turn"))
    if turn:
        return f"<span class='md-hide num'>turn {turn}</span>", f"T{turn}"
    if not r.get("has_goldfish"):
        return "<span class='md-hide muted'>no solo test</span>", "–"
    return "<span class='md-hide muted'>not reached</span>", "–"


def row_html(r: dict[str, Any], apps: dict[str, str], *, now: float) -> str:
    rid = esc(r["report_id"])
    href = f"/history/reports/{rid}"
    name = esc(r.get("deck_name") or "Unnamed deck")
    won, won_short = won_cell(r)
    dmg, dmg_short = damage_cell(r)
    who = asked_by(r, apps)
    client = r.get("created_by_client")
    app = ui.app_chip(who, client_id=str(client) if client else None)
    ran = ui.when(r.get("taken_at"), now=now)
    kill = f"{r.get('kill_damage') or 40} damage"
    line = deck_line(r)
    # the row's deck, for its menu's "Open the deck" (static/menus.js)
    deck = f" data-deck='{esc(str(r['deck_id']))}'" if r.get("deck_id") else ""
    return (
        "<div class='tr'>"
        f"<span class='title'{deck}><span class='t'><a href='{href}'>{name}</a>"
        f"{status_label(r.get('status'), cls='stin', research=r.get('research'))}</span>"
        + (f"<span class='deck'>{line}</span>" if line else "")
        + "<span class='sub'>"
        f"<span class='kv'>Won <b>{esc(won_short)}</b></span>"
        f"<span class='kv'>{esc(kill)} <b>{esc(dmg_short)}</b></span>{app}{ran}</span></span>"
        f"<span class='sm-hide'>{status_label(r.get('status'), research=r.get('research'))}</span>"
        f"{won}{dmg}<span class='md-hide'>{app}</span><span class='md-hide'>{ran}</span>"
        f"{ui.open_link(href, 'Open')}</div>"
    )


# -- widgets ---------------------------------------------------------------------------------------


def forge_widget(runs: list[dict[str, Any]]) -> str:
    playing = [r for r in runs if not r["forge"].get("waiting")]
    waiting = [r for r in runs if r["forge"].get("waiting")]
    big = (
        f"{len(playing)}<small>playing</small> {len(waiting)}<small>waiting</small>"
        if runs
        else "0<small>playing</small> 0<small>waiting</small>"
    )
    if not runs:
        return ui.widget("Forge now", "play", 0, big_html=big, detail="Nothing plays or waits now.")
    first = runs[0]
    f = first["forge"]
    href = f"/history/reports/{first['report_id']}"
    link = f"<a href='{esc(href)}'>{esc(first['deck_name'] or 'A deck')}</a>"
    det = f"<span class='tx'>{link}: {esc(run_text(f))}</span>"
    if not f.get("waiting") and f.get("games_requested"):
        share = max(0, min(100, round(100 * (f.get("games_done") or 0) / f["games_requested"])))
        det += (
            "<span class='bar-split rp-prog' aria-hidden='true'>"
            f"<i style='width:{share}%;background:var(--orange)'></i></span>"
        )
    actions = [ui.button_link("Open it", href)]
    if len(runs) > 1:
        actions.append(ui.button_link(f"Show waiting ({len(runs) - 1})", "/reports?show=going"))
    return ui.widget("Forge now", "play", 0, big_html=big, detail_html=det, actions=actions, tone="accent")


def latest_widget(latest: dict[str, Any] | None, *, now: float) -> str:
    if latest is None:
        return ui.widget("Latest report", "report", "–", detail="No reports yet.")
    win, turn = latest.get("win"), _turn(latest.get("kill_median_turn"))
    if win and win.get("enough", True):
        big = f"{_pct(win['rate'])}<small>won against precons</small>"
    elif turn:
        big = f"Turn {turn}<small>{esc(str(latest.get('kill_damage') or 40))} damage, median</small>"
    elif win:
        big = f"{int(win.get('games') or 0)}<small>Forge games, too few to tell yet</small>"
    else:
        big = "–"
    href = f"/history/reports/{esc(latest['report_id'])}"
    det = (
        status_label(latest.get("status"), research=latest.get("research"))
        + f"<span><a href='{href}'>{esc(latest.get('deck_name') or 'Unnamed deck')}</a>, "
        f"{ui.when(latest.get('taken_at'), now=now)}</span>"
    )
    trend = "/history?" + urlencode({"deck_id": str(latest.get("deck_id") or ""), "type": "tests"})
    return ui.widget(
        "Latest report",
        "report",
        0,
        big_html=big,
        detail_html=det,
        # a pasted list's (or a proposal's, a comparison's) report is in no deck's trend
        actions=[ui.button_link("Open report", href)]
        + (
            [ui.button_link("Deck trend", trend)]
            if latest.get("deck_id") and not latest.get("source")
            else []
        ),
    )


def problem_why(row: dict[str, Any]) -> str:
    if row.get("forge_state") in FORGE_NOT_RUN and not row.get("forge_played"):
        return "the Forge games could not run"
    if row.get("status") == "failed":
        return "nothing beyond the deck statistics came back"
    return "some parts did not run or did not finish"


def problem_widget(index: list[dict[str, Any]], *, now: float) -> str:
    bad = [r for r in index if r.get("status") in ("partial", "failed")]
    if not bad:
        return ui.widget("Partial or failed", "alert", 0, unit="reports")
    first = bad[0]
    ago = ui.when_text(first.get("taken_at"), now=now)
    det = f"{first.get('deck_name') or 'A deck'}, {ago}: {problem_why(first)}"
    return ui.widget(
        "Partial or failed",
        "alert",
        len(bad),
        unit="report" if len(bad) == 1 else "reports",
        detail=det,
        actions=[ui.button_link("Show them", "/reports?show=problem")],
        tone="alert",
    )


def decks_widget(index: list[dict[str, Any]], trends: list[dict[str, Any]]) -> str:
    """How many decks were tested, and the newest few with the median turn their newest report
    dealt lethal damage and its change from the report before (faster green, slower red)."""
    decks = {r["deck_id"] for r in index if r.get("deck_id") and not r.get("source_label")}
    rows = []
    for t in trends:
        turn = _turn(t.get("latest"))
        prev = t.get("previous")
        delta = ""
        if turn and isinstance(prev, (int, float)):
            d = float(t["latest"]) - float(prev)
            n = _turn(abs(d))
            if d < 0:
                delta = f"<span class='dl down' title='{n} turns faster than its report before'>↓{n}</span>"
            elif d > 0:
                delta = f"<span class='dl up' title='{n} turns slower than its report before'>↑{n}</span>"
            else:
                delta = "<span class='dl'>±0</span>"
        rows.append(
            f"<li><span>{esc(t['deck_name'] or 'Unnamed deck')}</span><b>{'T' + turn if turn else '–'}</b>"
            f"{delta or '<span class=dl></span>'}</li>"
        )
    det = (
        "<ul class='kd-trend' aria-label='Median turn lethal damage was dealt, newest report of each deck'>"
        + "".join(rows)
        + "</ul>"
        if rows
        else ""
    )
    count = len(index)
    unit = f"deck{'' if len(decks) == 1 else 's'} · {count} report{'' if count == 1 else 's'}"
    return ui.widget(
        "Decks tested",
        "stats",
        len(decks),
        unit=unit,
        detail_html=det,
        actions=[ui.button_link("Trends in History", "/history?type=reports")] if index else (),
    )


# -- the page ------------------------------------------------------------------------------------


def toolbar_html(query: Query, decks: dict[str, str], apps_seen: list[str]) -> str:
    deck_opts = [("", "All decks")] + [
        (did, name or "Unnamed deck")
        for did, name in sorted(decks.items(), key=lambda kv: (str(kv[1]).casefold(), kv[0]))
    ]
    by_opts = [("", "Anyone")] + [(n.casefold(), n) for n in apps_seen]
    filters = [
        ui.filter_select("deck_id", "Deck", deck_opts, query.deck_id),
        ui.filter_select("by", "Asked by", by_opts, query.by),
        ui.filter_select("when", "When", list(WHEN), query.when),
    ]
    return ui.toolbar(
        "/reports",
        search_label="Search reports",
        placeholder="Search deck or commander",
        q=query.q,
        filters=filters,
        keep={"show": query.show, "order": query.order},
    )


def reports_page_html(
    index: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    query: Query,
    total: int,
    counts: dict[str, int],
    runs: list[dict[str, Any]],
    decks: dict[str, str],
    apps: dict[str, str],
    latest: dict[str, Any] | None,
    trends: list[dict[str, Any]],
    now: float | None = None,
    notice: str = "",
    csrf: str = "",
) -> str:
    """The page. ``notice``: a line under the head in the page's own words (a report just deleted).
    ``csrf``: the form token for Test a pasted list (none: the box is left out).
    ``index``: every report (ReportService.index); ``rows``: the shown page's
    summaries; ``total``: how many pass the chip, search and filters; ``counts``: each chip's
    count under the search and filters; ``apps``: client id to app name."""
    now = time.time() if now is None else now
    seen = sorted({asked_by(r, apps) for r in index} - {""}, key=lambda n: (n != YOU, n.casefold()))
    head = ui.assistant_head("/reports", "Reports", SUB_LINE)
    wid = ui.widgets(
        [
            forge_widget(runs),
            latest_widget(latest, now=now),
            problem_widget(index, now=now),
            decks_widget(index, trends),
        ]
    )
    chips = ui.chips(
        ui.Chip(text, counts.get(key, 0), query.href(show=key, offset=0), current=query.show == key)
        for key, text in CHIPS
    )
    order_note = "Oldest first" if query.order else "Newest first"
    listing = ui.list_head("All reports", len(index), order_note)
    sort = (
        f"<a class='rp-sort' href='{esc(query.href(order='' if query.order else 'oldest', offset=0))}' "
        f"aria-label='Ran, {order_note.lower()}: show {'newest' if query.order else 'oldest'} first'>"
        f"Ran {icon('sort')}</a>"
    )
    cols = [
        ui.Column("Deck"),
        ui.Column("Status", "sm-hide"),
        ui.Column("Won vs precons", "md-hide"),
        ui.Column("Lethal by", "md-hide"),
        ui.Column("Asked by", "md-hide"),
        ui.Column("Ran", "md-hide", html=sort),
        ui.Column(""),
    ]
    if rows:
        body = "".join(row_html(r, apps, now=now) for r in rows)
    else:
        if query.offset and total:
            empty = "No older reports."
        elif not index:
            empty = (
                "No reports yet. Open one of your decks and press <strong>Run test</strong>, or ask "
                "your assistant to test a deck."
            )
        elif query.deck_id and not query.show and not (query.q or query.by or query.when):
            empty = "No reports for this deck yet. Run one from the deck's page."
        else:
            empty = "No reports match. Clear the search or the filters to see them all."
        body = f"<div class='tr rp-empty'><p>{empty}</p></div>"
    table = (
        ui.table_open(
            "minmax(0,1fr) minmax(0,9.5rem) minmax(0,8rem) minmax(0,6.5rem) minmax(0,7rem) minmax(0,6.5rem) "
            "minmax(0,4rem)",
            "minmax(0,1fr) minmax(0,9.5rem) minmax(0,4rem)",
            "minmax(0,1fr) auto",
            cls="rp-t",
            label="Reports",
        )
        + ui.table_header(cols)
        + body
        + "</div>"
    )
    # Load more (the standard's button; reports.js adds the next rows in place), never a pager
    shown_to = min(query.offset + PAGE, total)
    if not total:
        pager = ""
    elif shown_to < total:
        pager = ui.load_more(
            query.href(offset=shown_to), text="Load more reports", shown=shown_to, total=total
        )
    else:
        pager = ui.load_more("", done=f"All {total:,} {'report' if total == 1 else 'reports'} shown.")
    note = (
        f"<p class='ui-note'>{QUEUE_NOTE} To run a report, open one of your decks and press "
        "<strong>Run test</strong>, or ask your assistant to test a deck.</p>"
    )
    return (
        "<div class='reports-page gb-dash'>"
        + head
        + (f"<p class='notice ok' role='status'>{esc(notice)}</p>" if notice else "")
        + wid
        + listing
        + chips
        + toolbar_html(query, decks, seen)
        + table
        + pager
        + paste_test_html(csrf)
        + note
        + "</div>"
    )


PASTE_TEST_LINE = (
    "Plays a list that is not one of your decks, the way an assistant's test does. The report is filed "
    "under no deck, so no deck's trend counts it."
)


def paste_test_html(csrf: str) -> str:
    """Test a pasted list (owner decision C7: the website starts every kind of test), folded."""
    if not csrf:
        return ""
    return (
        "<details class='fold rp-paste'><summary class='fold-btn'>Test a pasted list</summary>"
        "<form method='post' action='/reports/test' class='rp-paste-f'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<p class='ui-sub'>{PASTE_TEST_LINE}</p>"
        "<textarea name='decklist' rows='6' aria-label='The decklist to test' "
        "placeholder='1 Sol Ring&#10;1 Arcane Signet'></textarea>"
        "<button type='submit' class='btn-plain' data-busy-label='Testing…'>"
        f"{icon('play')}<span class='label'>Test this list</span></button></form></details>"
    )


# Phones: two widgets a row as the widgets draw them, one a row when the text is so large that half the
# width cannot hold a word (the column's minimum is in the widgets' own text size, so it grows
# with a phone's font scale or a text-only zoom). Shared by the dashboard pages.
PHONE_WIDGETS_CSS = """
@media (max-width:599.98px){
  .gb-dash .ui-widgets{grid-template-columns:repeat(auto-fit,minmax(min(100%,8.5em),1fr))}
}
/* a label in a narrow table cell wraps (large text) instead of running out of its column */
.gb-dash .ui-table .ui-lbl{white-space:normal;height:auto;min-height:24px;line-height:1.2}
/* a widget's line with a long unbroken name (a deck's) wraps inside the widget: a phone page that
   scrolled sideways widened the layout viewport, and the tab bar's More sheet ran past the edge */
.gb-dash .wdg .det > *{min-width:0;max-width:100%;overflow-wrap:anywhere}
"""

REPORTS_PAGE_CSS = (
    PHONE_WIDGETS_CSS
    + """
/* the Reports page */
.reports-page .rp-paste{margin:1rem 0}
.reports-page .rp-paste-f{display:flex;flex-direction:column;gap:.5rem;align-items:flex-start;
  margin-top:.5rem}
.reports-page .rp-paste-f textarea{width:100%;min-height:8rem;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.reports-page .rp-paste-f button{min-height:44px}
.reports-page .kd-trend{list-style:none;margin:0;padding:0;font-size:.88rem;width:100%}
.reports-page .kd-trend li{display:grid;grid-template-columns:minmax(0,1fr) auto 2.4rem;gap:.5rem;
  align-items:baseline;padding:.15rem 0;border-top:1px solid var(--border-soft)}
.reports-page .kd-trend li:first-child{border-top:0}
.reports-page .kd-trend li span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  color:var(--text-muted)}
.reports-page .kd-trend b{font-variant-numeric:tabular-nums;color:var(--text)}
.reports-page .kd-trend .dl{color:var(--text-muted);text-align:right;font-weight:700;
  font-variant-numeric:tabular-nums}
.reports-page .kd-trend .dl.down{color:var(--green-text)}
.reports-page .kd-trend .dl.up{color:var(--red-text)}
.reports-page .rp-prog{width:100%}
.reports-page .rp-t .title .t a{font-weight:700}
/* the deck name is a finger-sized link on a touch screen */
@media (pointer:coarse){.reports-page .rp-t .title .t a{display:inline-flex;align-items:center;
  min-height:44px}}
.reports-page .rp-t .title .deck .muted{color:var(--text-muted)}
.reports-page .rp-t .wr{display:none;flex-direction:column;gap:.2rem;font-variant-numeric:tabular-nums}
@media (min-width:900px){.reports-page .rp-t .wr{display:flex}}
.reports-page .rp-t .ci{position:relative;display:block;height:6px;width:5.5rem;max-width:100%;
  border-radius:3px;background:var(--surface-2)}
.reports-page .rp-t .ci i{position:absolute;top:0;bottom:0;border-radius:3px;background:var(--orange)}
.reports-page .rp-t .num{font-variant-numeric:tabular-nums}
.reports-page .rp-t .muted{color:var(--text-muted);font-size:.9rem}
.reports-page .rp-t .kv{color:var(--text-muted);font-size:.88rem;white-space:nowrap}
.reports-page .rp-t .kv b{color:var(--text)}
.reports-page .rp-t .rp-empty{grid-template-columns:1fr}
.reports-page .rp-t .rp-empty p{margin:.25rem 0}
.reports-page .rp-sort{display:inline-flex;align-items:center;gap:.2rem;color:inherit;text-decoration:none;
  min-height:24px}
.reports-page .rp-sort svg.i{width:14px;height:14px}
@media (pointer:coarse){.reports-page .rp-sort{min-height:44px}}
"""
)
