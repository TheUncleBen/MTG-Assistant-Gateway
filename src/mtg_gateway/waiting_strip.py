"""Waiting changes as rows with checkboxes: the Proposals page's deck groups and the deck page's
"Waiting changes" strip.
Owned by the approvals work.

The deck route (companion_decks.py) asks ``waiting_changes`` for the strip's data and hands it to
``deckpage.deck_page_html`` as ``waiting``, which draws it with ``strip_html`` at the top of the
page, under the banner: folded to one line on a phone ("3 waiting changes · oldest 2 h ·
Review"), open on a computer. Every list posts to /proposals/select (pages.py): Apply opens a
confirm that lists the order, Reject rejects the checked rows at once (nothing changes on
Archidekt).
"""

from __future__ import annotations

import html
import time
from typing import Any

from . import board, ui

LIST_SCRIPT = "/static/proposals.js"


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min"
    if seconds < 2 * 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def line_for(db: Any, sub: str, row: dict[str, Any], *, risk: str | None = None) -> dict[str, Any]:
    """One waiting change as a list row: its number, summary, who asked and when, version, status
    (in words too) and expiry. ``row`` is a ``Database.pending_board`` row; ``risk`` its risk
    tier when the caller worked it out (DeckService.proposal_risk)."""
    from .decks import ADMIN_CLIENT, BROWSER_CLIENT, actor_label

    client = row.get("created_by_client")
    name = None if not client or client in (BROWSER_CLIENT, ADMIN_CLIENT) else db.client_name(client)
    lines = str(row.get("diff_text") or "").splitlines()
    summary = ", ".join(lines[:4]) + (f" (+{len(lines) - 4} more)" if len(lines) > 4 else "")
    return {
        "proposal_id": row["id"],
        "board_no": row.get("board_no"),
        "kind": row.get("kind"),
        "deck_id": str(row.get("deck_id")),
        "deck_name": row.get("deck_name") or str(row.get("deck_id")),
        "summary": summary[:300] or "(no changes listed)",
        "created_at": int(row.get("created_at") or 0),
        "expires_at": int(row.get("expires_at") or 0),
        "created_by": actor_label(client, name) if client else "",
        "version": int(row.get("version") or 1),
        "status": row.get("status"),
        "status_text": board.status_text(db, sub, row) if row.get("status") else "",
        "link_note": board.link_note(db, sub, row),
        **({"risk": risk} if risk else {}),
    }


def waiting_changes(
    db: Any, sub: str, deck_id: int | str, *, own: bool, risk_of: Any = None
) -> list[dict[str, Any]]:
    """The strip's rows for this member and deck, oldest first: their own waiting changes to
    the deck (on someone else's deck, their waiting likes, comments or follows). ``risk_of``
    (DeckService.proposal_risk) adds each one's risk label."""
    if db is None:  # no database to ask: nothing waits
        return []
    out = []
    for r in db.pending_board(sub, str(deck_id)):
        full = db.get_proposal(r["id"], sub) if risk_of is not None else None
        out.append(line_for(db, sub, r, risk=risk_of(sub, full)[0] if full else None))
    return out


def chips_html(line: dict[str, Any], now: float | None = None) -> str:
    """The row's §4 labels: its status (Waiting, Needs a look or Held; Updated beside Waiting),
    its risk when known, then the expiry and version notes."""
    now = time.time() if now is None else now
    status = str(line.get("status") or "")
    tip = str(line.get("status_text") or "")
    out = ui.status_label(status if status in ("needs_look", "held") else "waiting", title=tip)
    if status == "updated":
        out += ui.status_label("updated", title=tip)
    if line.get("risk"):
        out += ui.risk_label(str(line["risk"]))
    left = int(line.get("expires_at") or 0) - now
    if 0 < left < 2 * 86400:
        out += f"<span class='st exp'>Expires in {_age(left)}</span>"
    if int(line.get("version") or 1) > 1:
        out += f"<span class='st ver'>Version {int(line['version'])}</span>"
    return out


def _who(label: str) -> str:
    if label.startswith("app: "):
        name = label[5:].rsplit(" (", 1)[0] if label.endswith(")") else ""
        return name or "an assistant"
    return {"browser": "you", "admin": "the administrator"}.get(label, label)


def row_html(line: dict[str, Any], *, now: float | None = None, group: str = "") -> str:
    """One waiting change: a full-row checkbox (the row is the tap target), the summary, status
    chips, who asked and when, and a link to its full review."""
    now = time.time() if now is None else now
    pid = html.escape(line["proposal_id"])
    no = f"#{int(line['board_no'])} " if line.get("board_no") else ""
    why = (
        f"<span class='why'>{html.escape(line['status_text'])}</span>"
        if line.get("status") in ("needs_look", "held") and line.get("status_text")
        else ""
    )
    if line.get("link_note"):
        why += f"<span class='why'>{html.escape(line['link_note'])}</span>"
    who = _who(line.get("created_by") or "")  # empty when no app was recorded (older changes)
    return (
        f"<li class='wrow'><label class='pick'><input type='checkbox' name='pick' value='{pid}'"
        + (f" data-group='{html.escape(group)}'" if group else "")
        + f"><span class='what'><b>{html.escape(no)}</b>{html.escape(line['summary'])}</span>"
        f"<span class='chips'>{chips_html(line, now)}</span>"
        f"<span class='by'>{html.escape(who) + ', ' if who else ''}"
        f"{_age(now - int(line.get('created_at') or now))} ago</span>{why}</label>"
        f"<input type='hidden' name='v_{pid}' value='{int(line.get('version') or 1)}'>"
        f"<a class='review' href='/proposals/{pid}' "
        f"aria-label='Review {html.escape(no.strip() or 'this change')}'>"
        "Review</a></li>"
    )


def action_bar(*, inline: bool = False) -> str:
    """The bar that stays on screen while anything is checked (FLEX §3): Apply N, Reject N, Clear.
    Without JavaScript it is always there and acts on the checked rows."""
    return (
        f"<div class='wbar{' inline' if inline else ''}' role='group' aria-label='Checked changes'>"
        "<div class='wtop'><span class='count' aria-live='polite'></span>"
        "<button type='button' class='btn clear' hidden>Clear</button></div>"
        "<button type='submit' name='action' value='apply' class='btn-primary' data-label='Apply'>"
        "Apply checked</button>"
        "<button type='submit' name='action' value='reject' class='danger' data-label='Reject'>"
        "Reject checked</button></div>"
    )


def strip_html(lines: list[dict[str, Any]], *, csrf: str | None, deck_id: str) -> str:
    """The deck page's strip, at the top under the banner. Empty without waiting
    changes, and without a session (nothing to approve with)."""
    if not lines or not csrf:
        return ""
    now = time.time()
    n = len(lines)
    oldest = _age(now - min(int(x.get("created_at") or now) for x in lines))
    head = (
        f"<summary><b>{n} waiting change{'s' if n != 1 else ''}</b>"
        f"<span class='sep'>·</span><span>oldest {oldest}</span>"
        "<span class='open'><span class='sep'>·</span><span class='go'>Review</span></span></summary>"
    )
    rows = "".join(row_html(x, now=now, group=deck_id) for x in lines)
    return (
        "<details class='wstrip' id='waiting' data-open-wide>"
        + head
        + "<form method='post' action='/proposals/select' class='wform'>"
        f"<input type='hidden' name='csrf' value='{html.escape(csrf)}'>"
        f"<input type='hidden' name='back' value='/decks/{html.escape(deck_id)}'>"
        f"<label class='pick all'><input type='checkbox' class='all' data-all='{html.escape(deck_id)}'>"
        "<span>All waiting changes to this deck</span></label>"
        f"<ul class='wlist'>{rows}</ul>" + action_bar(inline=True) + "<p class='small more'>"
        f"<a href='/proposals#deck-{html.escape(deck_id)}'>All waiting changes</a></p>"
        "</form></details>"
        f"<script src='{LIST_SCRIPT}' defer></script>"
    )


# Shared by the Proposals page, the strip and the confirm and result pages. Colours are the theme's
# tokens (theme.py), each pair at least 4.5:1 in light and dark; rows are at least 44 px high.
LIST_CSS = """
/* the confirm's and Undo's locked buttons stay in the Tab order (click guard data-tab): outlined in
   their colour, not faded, as the review page's locked ones are */
form.guarded button[data-guard][aria-disabled=true]{opacity:1;background:transparent;
  border:1px dashed currentColor;cursor:default}
form.guarded button.primary[aria-disabled=true]{color:var(--orange-text)}
form.guarded button.danger[aria-disabled=true]{color:var(--danger-text)}
/* Reject's short confirm in the bar; focus moves to its Reject by script */
.wbar .rejectask{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;flex:1 1 100%}
.wbar .rejectask span{flex:1 1 10rem;font-weight:700}
.wbar .rejectask button{min-height:44px}
.wbar .rejectask button:focus{outline:2px solid var(--focus);outline-offset:2px}
.wlist{list-style:none;margin:0;padding:0}
.wrow{display:flex;flex-wrap:wrap;align-items:stretch;column-gap:.5rem;border-top:1px solid var(--surface-2)}
.wrow:first-child{border-top:0}
/* with big text the row has no room for Review beside it, so Review goes under */
.pick{display:flex;align-items:flex-start;gap:.6rem;flex:1 1 12em;min-width:0;min-height:44px;
  padding:.55rem .25rem;cursor:pointer;flex-wrap:wrap}
.pick input[type=checkbox]{width:1.25rem;height:1.25rem;margin:.1rem 0 0;flex:none;
  accent-color:var(--orange-ctl)}
.pick .what{flex:1 1 14rem;min-width:0;overflow-wrap:break-word}
.pick .chips{display:flex;flex-wrap:wrap;gap:.3rem}
.pick .by{color:var(--text-muted);font-size:.86rem;flex:0 1 auto}
.pick .why{flex:1 1 100%;font-size:.88rem;color:var(--text-muted);padding-left:1.85rem}
.pick.all{border-bottom:1px solid var(--border-soft);font-weight:700}
.wrow .review{display:flex;align-items:center;padding:0 .6rem;min-width:44px;min-height:44px;
  justify-content:center;white-space:nowrap}
.st{display:inline-block;padding:.05rem .45rem;border-radius:3px;font-size:.8rem;font-weight:700;
  border:1px solid var(--border-soft);background:var(--surface-2);color:var(--text)}
.st.exp,.st.ver{font-weight:400}
.wbar{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;padding:.6rem .75rem;margin:.75rem 0 0;
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-panel)}
.wbar:not(.inline){position:sticky;bottom:.5rem;z-index:6;box-shadow:var(--shadow)}
.wbar .count{flex:1 1 auto;font-weight:700}
.wbar button{margin:0;width:auto;min-height:44px}
.wbar[data-none] button[name=action]{opacity:.6}
.wbar .wtop{display:flex;align-items:center;gap:.5rem;flex:1 1 auto;min-width:0}
.wbar .clear{background:none;border:0;color:var(--link);text-decoration:underline;padding:0 .4rem}
.wbar .clear[hidden]{display:none}
.dgroup{margin:0 0 1rem}
.dgroup h2{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;font-size:1.1rem;margin:0 0 .25rem}
.dgroup h2 .pick{flex:0 1 auto;padding:.3rem 0;min-height:44px;align-items:center}
.dgroup h2 .meta2{color:var(--text-muted);font-size:.86rem;font-weight:400}
.wstrip{margin:0 0 1rem;border:1px solid var(--border);border-left:4px solid var(--orange);
  border-radius:var(--radius-panel);background:var(--surface)}
.wstrip > summary{cursor:pointer;display:flex;flex-wrap:wrap;align-items:center;gap:.4rem;min-height:44px;
  padding:.35rem .75rem;list-style:none}
.wstrip > summary::-webkit-details-marker{display:none}
.wstrip > summary .open{display:inline-flex;gap:.4rem}
.wstrip > summary .open .go{color:var(--link);text-decoration:underline}
.wstrip[open] > summary .open{display:none}
.wstrip .sep{color:var(--text-muted)}
.wstrip .wform{padding:0 .75rem .6rem}
.wstrip .more{margin:.5rem 0 0}
/* links a thumb taps on their own are 44 px tall */
.wstrip .more a,.dgroup h2 > a,.results li > a{display:inline-flex;align-items:center;min-height:44px}
.order{margin:0 0 1rem;padding-left:1.4rem}
.order li{margin:.3rem 0;overflow-wrap:break-word}
.order .held{display:block;font-size:.9rem;margin-top:.2rem}
.skips li,.results li{margin:.35rem 0;overflow-wrap:break-word}
@media (max-width:700px){
  .wbar:not(.inline){position:fixed;left:0;right:0;bottom:0;margin:0;border-radius:0;border-width:1px 0 0;
    padding:.5rem .75rem calc(.5rem + env(safe-area-inset-bottom))}
  /* the count and Clear on one row, Apply and Reject side by side on the next: two rows at
     200% text too */
  .wbar:not(.inline) .wtop{flex-basis:100%}
  .wbar button[name=action]{flex:1 1 0;min-width:0}
  .pick .by{flex-basis:100%;padding-left:1.85rem}
  .wrow .review{padding:0 .4rem}
}
/* the bar sits above the phone's bottom tab bar and beside the touch rail, never under them. The
   tab bar grows when large text wraps its labels: --tab-room is its real height (static/topbar.js),
   and 4rem without scripts covers it at 200% text */
@media (max-width:599.98px){
  .has-tabbar .wbar:not(.inline){bottom:var(--tab-room,calc(max(56px,4rem) + env(safe-area-inset-bottom)));
    padding-bottom:.5rem}
  body.has-tabbar:has(.wbar:not(.inline)) main.wrap{padding-bottom:calc(12rem + env(safe-area-inset-bottom))}
}
@media (min-width:600px) and (max-width:700px) and ((pointer:coarse) or (hover:none)){
  .has-tabbar .wbar:not(.inline){left:calc(80px + env(safe-area-inset-left))}
}
@media (min-width:600px) and (max-width:700px){
  body.app.has-tabbar .wbar:not(.inline){left:calc(80px + env(safe-area-inset-left))}
}
"""
