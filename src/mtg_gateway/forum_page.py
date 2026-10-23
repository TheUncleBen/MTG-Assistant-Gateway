"""The Forum area (parity release 2, mockup screens/60-forum.js; PLAN.md rows 8.4 to 8.6):
Archidekt's forum read and replied to from the gateway.

/forum lists the groups and their sections; /forum/{group}/{section} is one section's thread
table with its search box, order choice and a Load more button (never endless scrolling);
/forum/thread/{id} is a thread: its first post, then its replies in the deck page's own comment
panel (static/social.js over the comment routes, whose target is "forum:<thread id>"), so a reply,
an edit, a delete and a vote work as they do on a deck. A reply is public under the member's
Archidekt name and goes through their own press, as a deck comment does. A locked or archived
thread shows no reply box. Starting a thread stays on Archidekt (it asks for a reCAPTCHA): one
button opens its New thread page in a new tab.

The sections and thread lists come from Archidekt's own page data (forum.py), not an API; when
that changes, the pages say so plainly (forum.FORUM_CHANGED), offer to open a thread by its link
or number, and link to Archidekt's forum. Each page reads Archidekt once: the thread page hands
the thread it read to the script as JSON, so the panel does not read it again."""

from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import actions, deck_extras, forum, ui
from .companion import DECK_CSP, _json_for_html
from .decks import DeckError, current_client
from .pages import BROWSER_CLIENT_ID, _csrf, browser_session, login_redirect
from .theme import esc, icon

PAGE = "/forum"
TITLE = "Forum"
SUB = "Archidekt's forum: read and reply here; start a thread on Archidekt."
ARCHIDEKT_FORUM = "https://archidekt.com/forum"
NEW_THREAD_URL = "https://archidekt.com/forum/thread/new"
NEW_THREAD = "New thread on Archidekt"
NEW_THREAD_WHY = (
    "Archidekt asks you to prove you're human before starting a thread, so that step stays on "
    "Archidekt. Replies work here."
)
OPEN_LABEL = "Open a thread by its link or number"
OPEN_PLACEHOLDER = "https://archidekt.com/forum/thread/… or the number"
OPEN_BUTTON = "Open thread"
OPEN_BAD = "That is not a thread: give the thread's number or its link."
LOCKED = "This thread is locked on Archidekt, so it takes no new replies."
ARCHIVED = "This thread is archived on Archidekt, so it takes no new replies."
# under the reply box (the mockup's words): the member's Archidekt name fills {name}
REPLY_PUBLIC = "Public under “{name}”. An assistant's reply always waits for your press."
REPLY_PUBLIC_UNNAMED = "Public under your Archidekt name. An assistant's reply always waits for your press."
WRITES_OFF = "Archidekt writes are switched off on this gateway, so replies can be read here but not posted."
# the contest strip (the mockup's r2-contest screen)
CONTEST_INTRO = (
    "Enter one of your decks with a short summary. Archidekt replaces that deck's description with "
    "the summary."
)
CONTEST_ENTRIES_UNREAD = "The entries could not be read from Archidekt right now."
CONTEST_ENTER = "Enter a deck…"
CONTEST_WITHDRAW = "Withdraw…"
CONTEST_SUMMARY_HELP = "Shown on the contest page."
CONTEST_REPLACES = "Archidekt replaces the deck's description with this summary."
CONTEST_REVIEWED = "Reviewed"
CONTEST_NOT_REVIEWED = "Not yet reviewed"
CONTEST_BOXES = ("enter", "withdraw", "reportcomment")
# the reply box's text style buttons: the marks they put in (formatting.marks_doc)
TOOLBAR = (("bold", "Bold", "**"), ("italic", "Italic", "_"), ("link", "Link", "[]()"))
# the thread marks Archidekt's list carries (forum.THREAD_TYPES), as section-4 labels
MARKS = {
    "sticky": ("Sticky", "orange", "bookmark"),
    "global": ("Global", "blue", "bell"),
    "news": ("News", "blue", "bell"),
    "features": ("Features", "green", "check"),
    "contest": ("Contest", "orange", "trophy"),
}
ORDERS = (("recent", "Most recent"), ("points", "Points"))

CSS = """
.fm .crumbs{display:flex;flex-wrap:wrap;align-items:center;gap:.3rem;margin:0 0 .5rem;
  color:var(--text-muted);font-size:.9rem}
.fm .crumbs a{display:inline-flex;align-items:center;justify-content:center;min-height:44px;min-width:44px;
  padding:0 .25rem}
.fm .fm-why{display:flex;flex-wrap:wrap;align-items:center;gap:.3rem .75rem;margin:0 0 .75rem}
.fm .fm-why .btn{margin:0;flex:none}
.fm .fm-why span{flex:1 1 14rem;color:var(--text-muted);font-size:.9rem}
.fm .ui-head.phead .acts .btn{margin:0}
.fm .list-head{margin-top:1rem}
.fm .ui-table a.tr{color:inherit;text-decoration:none}
.fm .ui-table a.tr:hover .t,.fm .ui-table a.tr:focus-visible .t{text-decoration:underline}
.fm .ui-table a.tr:focus-visible{outline:2px solid var(--focus);outline-offset:-2px}
.fm .ui-table .t{font-weight:700}
.fm .ui-table .t .ui-lbl{margin-left:.35rem;vertical-align:middle}
.fm .ui-table .sub,.fm .ui-table .sub .ui-when{color:var(--text-muted);font-size:.86rem}
.fm .ui-table .fm-n{font-variant-numeric:tabular-nums}
/* the Last activity column: who and when on one line, cut short with an ellipsis, so the row
   stays as tall as the others */
.fm .ui-table .fm-last{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fm .ui-table .fm-last .fm-by{color:var(--text-muted)}
.fm .fm-post{border:1px solid var(--card-border);border-radius:var(--radius-panel);background:var(--surface);
  padding:.75rem .9rem;margin:0 0 .75rem}
.fm .fm-post .ph{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin:0 0 .35rem;
  color:var(--text-muted);font-size:.9rem}
.fm .fm-post .ph b{color:var(--text);overflow-wrap:anywhere}
.fm .fm-post .ph .avatar{width:32px;height:32px;font-size:1rem}
.fm .fm-post .text{margin:.2rem 0;white-space:pre-wrap;overflow-wrap:anywhere}
.fm .fm-post .pf{display:flex;flex-wrap:wrap;gap:.3rem .75rem;margin-top:.4rem;color:var(--text-muted);
  font-size:.9rem}
.fm .notice{margin:0 0 .75rem}
.fm .comments{margin-top:.75rem}
.fm .comments form.newcomment label{font-weight:700}
.fm form.fm-open{display:flex;flex-wrap:wrap;gap:.5rem;align-items:end;margin:.75rem 0}
.fm form.fm-open .field{flex:1 1 16rem;min-width:0;margin:0}
.fm form.fm-open .field label{display:block;font-weight:700;margin:0 0 .25rem}
.fm form.fm-open .field input{width:100%;min-height:44px}
.fm form.fm-open .btn{margin:0;min-height:44px}
.fm .fm-links{margin:.5rem 0 1rem}
.fm .loadmore .btn{margin:0;min-width:12rem}
/* the thread's first post: its ⋮ at the end of the header line */
.fm .fm-post .ph .kebab,.comments .cmt .who .kebab{display:inline-flex;align-items:center;
  justify-content:center;width:44px;height:44px;margin:-8px 0 -8px auto;padding:0;border:0;
  background:transparent;border-radius:50%;color:var(--text-muted);cursor:pointer}
.fm .fm-post .ph .kebab:hover,.fm .fm-post .ph .kebab:focus-visible,.comments .cmt .who .kebab:hover,
.comments .cmt .who .kebab:focus-visible{background:var(--surface-2);color:var(--text)}
.fm .fm-post .ph .kebab svg.i,.comments .cmt .who .kebab svg.i{width:20px;height:20px;fill:currentColor;
  stroke:currentColor}
.comments .cmt .who{min-height:44px}
/* the reply box: the text style buttons above it, the public line and Post reply under it */
.comments form.newcomment .rt{display:flex;gap:.25rem;margin:0}
.comments form.newcomment .rt button{width:44px;min-height:44px;margin:0;padding:0;display:inline-flex;
  align-items:center;justify-content:center;border:1px solid var(--control-border);
  border-radius:var(--radius);background:var(--surface);color:var(--text)}
.comments form.newcomment .rt button:hover,.comments form.newcomment .rt button:focus-visible{
  background:var(--surface-2)}
.comments form.newcomment .rt button svg.i{width:18px;height:18px}
.comments form.newcomment .racts{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}
.comments form.newcomment .racts .pub{flex:1 1 16rem;color:var(--text-muted);font-size:.9rem;
  display:inline-flex;gap:.35rem;align-items:center}
.comments form.newcomment .racts .pub svg.i{width:16px;height:16px;flex:none}
.comments form.newcomment .racts button{margin:0;align-self:auto}
/* the contest strip (deck_extras' strip) and the entries fold */
.fm .dx-strip .t .muted a{color:inherit}
.fm details.entries{margin:0 0 .75rem}
.fm details.entries summary{display:inline-flex;align-items:center;gap:.3rem;min-height:44px;cursor:pointer;
  font-weight:700}
.fm details.entries summary svg.i{width:16px;height:16px;transition:transform .15s}
.fm details.entries[open] summary svg.i{transform:rotate(180deg)}
.fm .entries-list{list-style:none;margin:0;padding:0}
.fm .entries-list li{display:flex;flex-wrap:wrap;align-items:center;gap:.3rem .75rem;min-height:44px;
  border-top:1px solid var(--border-soft)}
.fm .entries-list li:first-child{border-top:0}
.fm .entries-list li .t{flex:1 1 10rem;min-width:0}
.fm .entries-list li .t a{display:inline-flex;align-items:center;min-height:44px}
.fm .post-preview{white-space:pre-wrap;overflow-wrap:anywhere;border-left:3px solid var(--border);
  padding-left:.6rem;margin:0 0 .5rem}
.fm .post-preview a{word-break:break-all}
.fm .loadmore .btn[aria-busy=true]{cursor:progress;opacity:.7}
@media (max-width:599.98px){
  .fm .ui-table .sub .fm-when{display:block}
  .fm .fm-why .btn{flex:1 1 100%}
  .fm .ui-head.phead .acts{flex:1 1 100%}
  .fm .ui-head.phead .acts .btn{width:100%}
  .comments form.newcomment .racts button{flex:1 1 auto}
}
"""


# -- the pieces --------------------------------------------------------------------------------------


def section_href(group: str, section: str) -> str:
    return f"{PAGE}/{quote(group, safe='')}/{quote(section, safe='')}"


def thread_href(thread_id: Any) -> str:
    return f"{PAGE}/thread/{int(thread_id)}"


def _list_href(group: str, section: str, *, page: int = 1, order: str = "recent", q: str = "") -> str:
    params: dict[str, str] = {}
    if page > 1:
        params["page"] = str(page)
    if order != "recent":
        params["order"] = order
    if q:
        params["q"] = q
    return section_href(group, section) + (f"?{urlencode(params)}" if params else "")


def _ts(iso: str) -> float | None:
    """Archidekt's ISO time as a timestamp for ui.when, or None when it is not one."""
    raw = (iso or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _when(iso: str) -> str:
    ts = _ts(iso)
    return ui.when(ts) if ts is not None else ""


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"{n:,} {word}"
    many = word[:-1] + "ies" if word.endswith("y") else word + "s"  # reply -> replies
    return f"{n:,} {many}"


def new_thread_html(section: str = "") -> str:
    """The plain button that opens Archidekt's New thread page in a new tab, with the one line why."""
    url = NEW_THREAD_URL + (f"?section={quote(section, safe='')}" if section else "")
    return (
        f"<div class='fm-why'><a class='btn btn-plain' href='{esc(url)}' target='_blank' "
        f"rel='noreferrer noopener'>{icon('external')}{esc(NEW_THREAD)}</a>"
        f"<span>{esc(NEW_THREAD_WHY)}</span></div>"
    )


def group_anchor(group: str) -> str:
    """The id of a group's list on the forum's front page, for the crumbs' link to it: letters,
    digits and "-" kept, any other character as "_<hex>_", so two group names never share an id."""
    slug = re.sub(r"[^A-Za-z0-9-]", lambda m: f"_{ord(m.group()):x}_", group)
    return f"group-{slug}"


def crumbs_html(*parts: tuple[str, str]) -> str:
    """Forum › group › section: every crumb a link (a group's goes to its list on the front page)."""
    out = [f"<a href='{PAGE}'>Forum</a>"]
    for text, href in parts:
        out.append("<span aria-hidden='true'>›</span>")
        out.append(f"<a href='{esc(href)}'>{esc(text)}</a>" if href else f"<span>{esc(text)}</span>")
    return f"<nav class='crumbs' aria-label='You are in'>{''.join(out)}</nav>"


def open_form_html(value: str = "", error: str = "") -> str:
    """Open a thread by its link or number: a GET form to /forum/thread, which redirects."""
    err = ui.notice(error, "error", role="alert") if error else ""
    return (
        f"{err}<form class='fm-open' method='get' action='{PAGE}/thread'>"
        f"<div class='field'><label for='fm-thread'>{esc(OPEN_LABEL)}</label>"
        f"<input id='fm-thread' type='text' name='thread' value='{esc(value)}' "
        f"placeholder='{esc(OPEN_PLACEHOLDER)}' autocomplete='off' maxlength='200' required></div>"
        f"<button type='submit' class='btn btn-plain'>{icon('comment')}{esc(OPEN_BUTTON)}</button></form>"
    )


def changed_html(message: str, *, value: str = "", error: str = "") -> str:
    """The forum's page data can't be read (forum.FORUM_CHANGED): said plainly, with the way to a
    thread by its link or number and the link to Archidekt's own forum."""
    return (
        ui.notice(message, "warn", role="alert")
        + open_form_html(value, error)
        + f"<p class='fm-links'><a class='btn btn-plain' href='{ARCHIDEKT_FORUM}' target='_blank' "
        f"rel='noreferrer noopener'>{icon('external')}Archidekt's forum</a></p>"
    )


def sections_html(got: dict[str, Any]) -> str:
    """The groups and their sections: chips to each section, then a table a group."""
    groups = got.get("groups") or []
    chips = [
        ui.Chip(s["section"], s["posts"], section_href(g["group"], s["section"]))
        for g in groups
        for s in g["sections"]
    ]
    out = [ui.chips(chips, label="Sections")] if chips else []
    for g in groups:
        rows = []
        for s in g["sections"]:
            desc = f"<span class='deck'>{esc(s['description'])}</span>" if s["description"] else ""
            rows.append(
                f"<a class='tr' href='{section_href(g['group'], s['section'])}'>"
                f"<span class='title'><span class='t'>{esc(s['section'])}</span>{desc}</span>"
                f"<span class='fm-n'>{s['posts']:,}</span></a>"
            )
        if not rows:
            continue
        out.append(
            f"<div class='fm-group' id='{group_anchor(g['group'])}'>"
            + ui.list_head(g["group"])
            + ui.table_open(
                "minmax(0,1fr) 7rem", "minmax(0,1fr) 6rem", "minmax(0,1fr) auto", label=g["group"]
            )
            + ui.table_header([ui.Column("Section"), ui.Column("Posts", "fm-n")])
            + "".join(rows)
            + "</div></div>"
        )
    if not out:
        out.append("<p class='muted'>Archidekt's forum lists no sections right now.</p>")
    return "".join(out)


def section_label(section: str) -> str:
    """The thread's section as a label (the mockup's lbl("section", …))."""
    return ui.label(section, "grey", "comment", cls="fm-section")


def thread_rows_html(threads: list[dict[str, Any]]) -> str:
    """One row a thread (the mockup's table: title, who, replies, last activity): a link to its
    page; on a phone the author, the replies and the date fold under the title as a second line.
    A section's page lists that section's threads, which the heading names, so no row repeats
    it (Archidekt's thread rows carry no section of their own)."""
    rows = []
    for t in threads:
        mark = MARKS.get(t["type"])
        label = ui.label(*mark) if mark else ""
        if t["archived"]:
            label += ui.label("Archived", "grey", "lock")
        last = t["last_post"]
        when = _when(last["at"])
        # who and when on one line (the column is cut short with an ellipsis, so the row stays
        # as tall as the others)
        by = f"<span class='fm-by'>{esc(last['by'])}</span>" if last["by"] else ""
        last_html = by + (" · " if by and when else "") + when
        # on a phone the date takes a line of its own under the owner and the replies
        sub = " · ".join((esc(t["owner"]), _plural(t["responses"], "reply"))) + (
            f"<span class='sm-only fm-when'>{when}</span>" if when else ""
        )
        rows.append(
            f"<a class='tr' href='{thread_href(t['id'])}' data-id='{t['id']}'>"
            f"<span class='title'><span class='t'>{esc(t['title'])}{label}</span>"
            f"<span class='sub'>{sub}</span></span>"
            f"<span class='md-hide'>{esc(t['owner'])}</span>"
            f"<span class='md-hide fm-n'>{t['responses']:,}</span>"
            f"<span class='fm-last sm-hide'>{last_html}</span></a>"
        )
    return "".join(rows) or "<div class='tr'><span class='muted'>No threads here yet.</span></div>"


def _shown(got: dict[str, Any]) -> int:
    return min(got["count"], got["page"] * forum.THREADS_PER_PAGE)


def load_more_words(got: dict[str, Any]) -> dict[str, Any]:
    """What the Load more button says, for the page and for the script that appends rows."""
    shown, total = _shown(got), got["count"]
    return {
        "shown": shown,
        "total": total,
        "label": f"Load more ({shown:,} of {total:,} shown)",
        "done": f"All {total:,} shown.",
    }


def threads_html(got: dict[str, Any]) -> str:
    """One section's page: crumbs, the head, the New thread button, the toolbar, the table and
    Load more (a button; without scripts, the next page's address)."""
    group, section = got["group"], got["section"]
    order, q, page = got["order"], got["search"], got["page"]
    head = ui.page_head(section, f"{group} · {_plural(got['count'], 'thread')}")
    tools = ui.toolbar(
        section_href(group, section),
        search_label="Search threads",
        placeholder="Search threads",
        q=q,
        filters=[ui.filter_select("order", "Order", ORDERS, order)],
    )
    table = (
        ui.table_open(
            "minmax(0,2.6fr) 9rem 5rem 11rem",
            "minmax(0,1fr) 11rem",
            "minmax(0,1fr)",
            cls="fm-threads",
            label="Threads",
        )
        + ui.table_header(
            [
                ui.Column("Thread"),
                ui.Column("Started by", "md-hide"),
                ui.Column("Replies", "md-hide fm-n"),
                ui.Column("Last activity", "sm-hide"),
            ]
        )
        + thread_rows_html(got["threads"])
        + "</div>"
    )
    more = ""
    if page < got["pages"]:
        words = load_more_words(got)
        more = ui.load_more(
            _list_href(group, section, page=page + 1, order=order, q=q),
            text=words["label"],
        )
    elif got["count"] and page > 1:
        more = ui.load_more("", done=load_more_words(got)["done"])
    return (
        crumbs_html((group, f"{PAGE}#{group_anchor(group)}"), (section, section_href(group, section)))
        + head
        + new_thread_html(section)
        + tools
        + table
        + more
    )


def _comment_html(c: dict[str, Any]) -> str:
    """A reply as the page shows it without scripts (social.js redraws it with its actions)."""
    pts = f" · {_plural(c['points'], 'point')}" if c.get("points") else ""
    edited = " · edited" if c.get("edited_at") else ""
    kids = "".join(_comment_html(k) for k in c.get("replies") or [])
    replies = f"<div class='replies'>{kids}</div>" if kids else ""
    return (
        f"<article class='cmt' id='c{c['id']}' data-id='{c['id']}'><div class='who'>"
        f"<b>{esc(c['owner']['username'])}</b>"
        f"<span>· {_when(c['created_at']) or esc(c['created_at'])}{edited}{pts}</span></div>"
        f"<p>{esc(c['text'])}</p>{replies}</article>"
    )


def _pub(what: str) -> str:
    return deck_extras.PUBLIC_LINE.format(what=what)


def toolbar_html() -> str:
    """The reply box's text style buttons (the mockup's r2-rt): Bold, Italic and Link, which put
    the reply box's marks around the selected words (static/social.js; formatting.marks_doc
    writes them as Archidekt's own bold, italic and linked text)."""
    buttons = "".join(
        f"<button type='button' data-mark='{name}' aria-label='{label}' title='{label}'>{icon(name)}</button>"
        for name, label, _mark in TOOLBAR
    )
    return f"<div class='rt' role='toolbar' aria-label='Text style'>{buttons}</div>"


def reply_form_html(csrf: str, me_name: str) -> str:
    public = REPLY_PUBLIC.format(name=me_name) if me_name else REPLY_PUBLIC_UNNAMED
    return (
        f"<form class='newcomment' data-forum='1' data-me-name='{esc(me_name)}'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        "<label for='ctext'>Your reply</label>"
        + toolbar_html()
        + "<textarea id='ctext' name='text' rows='4' maxlength='2000' placeholder='Write a reply'></textarea>"
        "<div class='replyto' hidden></div>"
        f"<div class='racts'><span class='pub'>{icon('hand')}<span>{esc(public)}</span></span>"
        f"<button type='submit' class='btn btn-primary'>{icon('send')} Post reply</button></div></form>"
    )


def _entry_bits(e: dict[str, Any]) -> dict[str, Any]:
    """One contest entry as Archidekt lists it (contestSubmission/decks/{thread}): the deck, its
    owner and, when the row carries it, whether staff reviewed it."""
    deck = e.get("deck") if isinstance(e.get("deck"), dict) else {}
    owner = e.get("owner") if isinstance(e.get("owner"), dict) else {}
    deck_id = deck.get("id") if isinstance(deck, dict) else e.get("deck")
    reviewed = e.get("reviewed")
    return {
        "id": e.get("id"),
        "deck_id": str(deck_id) if deck_id not in (None, "") else "",
        "deck_name": str(deck.get("name") or f"Deck {deck_id}") if deck_id not in (None, "") else "Deck",
        "owner_id": str(owner.get("id") or e.get("owner") or ""),
        "owner": str(owner.get("username") or ""),
        "reviewed": reviewed if isinstance(reviewed, bool) else None,
        "created_at": str(e.get("createdAt") or ""),
    }


def contest_html(
    t: dict[str, Any], entries: list[dict[str, Any]] | None, *, me: int | None, csrf: str, can_write: bool
) -> str:
    """The contest strip above the posts (the mockup's r2-contest): how many decks are entered,
    Enter a deck… or, once entered, the member's entry with Withdraw…, and the entries fold with
    each deck, its owner and its review mark (when Archidekt's row carries one)."""
    tid = t["id"]
    rows = [_entry_bits(e) for e in entries] if entries is not None else None
    mine = next((r for r in rows or [] if me is not None and r["owner_id"] == str(me)), None)
    n = len(rows) if rows is not None else None
    if n is None:
        head = "Contest"
    elif n == 0:
        head = "Contest · No decks entered yet"
    else:
        head = f"Contest · {_plural(n, 'deck')} entered"
    if mine:
        when = _when(mine["created_at"])
        link = (
            f"<a href='/decks/{esc(mine['deck_id'])}'>{esc(mine['deck_name'])}</a>"
            if mine["deck_id"]
            else esc(mine["deck_name"])
        )
        line = f"Your entry: {link}" + (f", entered {when}" if when else "")
        button = (
            f"<a class='btn btn-outline-danger' href='{thread_href(tid)}/withdraw' "
            f"{ui.box_open_attr('withdraw')}>{icon('x')}{esc(CONTEST_WITHDRAW)}</a>"
            if can_write
            else ""
        )
    else:
        line = esc(CONTEST_INTRO) if rows is not None else esc(CONTEST_ENTRIES_UNREAD)
        button = (
            f"<a class='btn btn-plain' href='{thread_href(tid)}/enter' {ui.box_open_attr('enter')}>"
            f"{icon('trophy')}{esc(CONTEST_ENTER)}</a>"
            if can_write and rows is not None
            else ""
        )
    strip = (
        f"<div class='dx-strip' id='fm-contest'><span class='t'><b>{icon('trophy')} {head}</b>"
        f"<span class='muted small'>{line}</span></span>{button}</div>"
    )
    if not rows:
        return strip
    items = []
    for r in rows:
        name = (
            f"<a href='/decks/{esc(r['deck_id'])}'>{esc(r['deck_name'])}</a>"
            if r["deck_id"]
            else esc(r["deck_name"])
        )
        you = ui.label("You", "blue", "user") if me is not None and r["owner_id"] == str(me) else ""
        mark = ""
        if r["reviewed"] is True:
            mark = ui.label(CONTEST_REVIEWED, "green", "check")
        elif r["reviewed"] is False:
            mark = ui.label(CONTEST_NOT_REVIEWED, "orange", "clock")
        items.append(
            f"<li><span class='t'>{name} <span class='muted small'>by {esc(r['owner'])}</span></span>"
            f"{you}{mark}</li>"
        )
    fold = (
        f"<details class='entries' open><summary>{icon('chev')}Entries ({len(rows)})</summary>"
        f"<ul class='entries-list'>{''.join(items)}</ul></details>"
    )
    return strip + fold


def enter_box_html(
    t: dict[str, Any], csrf: str, *, open: bool = False, error: str = "", values: dict[str, str] | None = None
) -> str:
    """Enter a deck… (the mockup's BOXES.r2Enter): one of the member's decks (read as the box
    opens, static/forum.js) and the summary, then Review the entry: the contest entry is proposed
    as the deck page proposes it (actions.propose, contest_enter) and the member applies it on
    its review page, which shows the description change."""
    tid = t["id"]
    v = values or {}
    summary = v.get("summary", "")
    body = (
        f"<form id='fm-enter-f' method='post' action='{thread_href(tid)}/contest' class='stack' novalidate>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        "<input type='hidden' name='entry_action' value='enter'>"
        "<div class='field'><label for='fm-ed'>Deck</label><span class='sel'>"
        "<select id='fm-ed' name='deck' required data-src='/api/decks/mine'>"
        + (
            f"<option value='{esc(v['deck'])}' selected>Deck {esc(v['deck'])}</option>"
            if v.get("deck")
            else ""
        )
        + "</select></span></div>"
        "<div class='field'><label for='fm-es'>Summary of the deck (required)</label>"
        f"<textarea id='fm-es' name='summary' rows='4' maxlength='{actions.CONTEST_SUMMARY_MAX}' "
        f"data-count='fm-es-c' required>{esc(summary)}</textarea>"
        f"<div class='dx-under'><span class='muted small'>{esc(CONTEST_SUMMARY_HELP)}</span>"
        f"<span class='muted small' id='fm-es-c' data-counter>{len(summary)} / "
        f"{actions.CONTEST_SUMMARY_MAX}</span></div></div>"
        + ui.notice(
            f"<b>{esc(CONTEST_REPLACES)}</b> {esc(deck_extras.CONTEST_WARN)}",
            "warn",
            html=True,
        )
        + f"<p class='muted small'>{icon('hand')} {esc(_pub('A contest entry'))}</p>"
        "</form>" + deck_extras._err(error)
    )
    return ui.box(
        "enter",
        f"Enter a deck in “{t['title']}”",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Review the entry", kind="primary", form="fm-enter-f", icon_name="trophy"),
        ],
        open=open,
        close_href=thread_href(tid),
        cls="dx-box dx-wide",
    )


def withdraw_box_html(
    t: dict[str, Any],
    csrf: str,
    mine: dict[str, Any] | None,
    *,
    open: bool = False,
    error: str = "",
) -> str:
    """Withdraw… (the mockup's BOXES.r2Withdraw): asks once more; the entry leaves the contest
    through the member's press on the review page."""
    tid = t["id"]
    deck_id = mine["deck_id"] if mine else ""
    name = mine["deck_name"] if mine else "your deck"
    body = (
        f"<form id='fm-withdraw-f' method='post' action='{thread_href(tid)}/contest' class='stack' "
        "novalidate>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        "<input type='hidden' name='entry_action' value='withdraw'>"
        f"<input type='hidden' name='deck' value='{esc(deck_id)}'>"
        f"<p>{esc(deck_extras.WITHDRAW_TEXT)}</p>"
        f"<p class='muted small'>{icon('hand')} {esc(_pub('Withdrawing an entry'))}</p>"
        "</form>" + deck_extras._err(error)
    )
    return ui.box(
        "withdraw",
        f"Withdraw “{name}” from this contest?",
        body,
        actions=[
            ui.box_button("Keep my entry", cancel=True),
            ui.box_button("Withdraw", kind="danger-solid", form="fm-withdraw-f", icon_name="x"),
        ],
        open=open,
        close_href=thread_href(tid),
        cls="dx-box",
    )


def thread_html(
    t: dict[str, Any],
    *,
    csrf: str,
    me: int | None,
    me_name: str = "",
    writes_enabled: bool,
    entries: list[dict[str, Any]] | None = None,
    open_box: str = "",
    box_error: str = "",
    box_values: dict[str, str] | None = None,
) -> str:
    """A thread: the first post with its ⋮, the locked or archived note, on a contest thread the
    contest strip, the replies in the deck page's comment panel and the reply box with its text
    style buttons. The thread's replies go to the script as JSON, in the shape the comment route
    answers, so the panel draws them without reading the thread again. ``entries``: a contest
    thread's entries (None when they could not be read)."""
    tid = t["id"]
    target = forum.target(tid)
    closed = t["locked"] or t["archived"]
    can_write = writes_enabled and not closed
    contest = t.get("type") == "contest"
    opens = (
        f"<a class='btn btn-plain' href='{esc(t['url'])}' target='_blank' rel='noreferrer noopener'>"
        f"{icon('external')}Open on Archidekt</a>"
    )
    sub = f"started by {t['owner']} · {_plural(t['reply_count'], 'reply')}"
    initial = esc((t["owner"] or "?")[:1].upper())
    when = _when(t["created_at"])
    own_post = bool(me_name) and t["owner"].casefold() == me_name.casefold()
    post = (
        f"<article class='fm-post' id='post-{tid}' data-id='{tid}' data-owner='{esc(t['owner'])}'"
        f"{' data-own=1' if own_post else ''} tabindex='0'>"
        f"<div class='ph'><span class='avatar' aria-hidden='true'>{initial}</span>"
        f"<b>{esc(t['owner'])}</b>{when}"
        f"<button type='button' class='kebab' aria-label='Menu for {esc(t['owner'])}&#39;s post' "
        f"aria-haspopup='menu' title='Menu'>{icon('more')}</button></div>"
        f"<p class='text'>{esc(t['text'])}</p>"
        f"<div class='pf'><span>{_plural(t['points'], 'point')}</span></div></article>"
    )
    note = ""
    if t["locked"]:
        note = ui.notice(LOCKED, "info", role="status")
    elif t["archived"]:
        note = ui.notice(ARCHIVED, "info", role="status")
    pre = {
        "root": tid,
        "count": t["reply_count"],
        "comments": t["replies"],
        "page": t["page"],
        "has_more": t["has_more"],
        "me": me,
    }
    data = _json_for_html(pre)  # every < escaped: no reply can open a tag inside the block
    if can_write:
        form = reply_form_html(csrf, me_name)
    elif closed:
        form = ""
    else:
        form = f"<p class='muted small'>{esc(WRITES_OFF)}</p>"
    replies = "".join(_comment_html(c) for c in t["replies"]) or "<p class='muted'>No replies yet.</p>"
    panel = (
        f"<section class='panel comments' id='comments' data-deck='{target}' data-forum='1' "
        f"data-writes='{'on' if can_write else 'off'}' data-root='{tid}' data-me-name='{esc(me_name)}'>"
        f"<div class='panel-head'><h2>Replies</h2></div>"
        f"<script type='application/json' id='forum-thread'>{data}</script>"
        f"<div class='thread' data-src='/social/api/decks/{target}/comments'>"
        f"{replies}</div>"
        f"{form}</section>"
    )
    boxes = ""
    if csrf:
        st = {"open": open_box == "reportcomment", "error": box_error if open_box == "reportcomment" else ""}
        boxes += deck_extras.report_comment_box_html(
            csrf,
            action=f"{thread_href(tid)}/report",
            close_href=thread_href(tid),
            values=box_values if open_box == "reportcomment" else None,
            **st,
        )
    strip = ""
    if contest:
        strip = contest_html(t, entries, me=me, csrf=csrf, can_write=bool(csrf) and can_write)
        if csrf and can_write:
            rows = [_entry_bits(e) for e in entries or []]
            mine = next((r for r in rows if me is not None and r["owner_id"] == str(me)), None)
            boxes += enter_box_html(
                t,
                csrf,
                open=open_box == "enter",
                error=box_error if open_box == "enter" else "",
                values=box_values if open_box == "enter" else None,
            )
            boxes += withdraw_box_html(
                t, csrf, mine, open=open_box == "withdraw", error=box_error if open_box == "withdraw" else ""
            )
    return (
        crumbs_html()
        + ui.page_head(t["title"], sub, actions_html=opens)
        + boxes
        + note
        + strip
        + post
        + panel
    )


# -- the routes --------------------------------------------------------------------------------------


def add_forum_routes(server: Any, state: Any, h: SimpleNamespace) -> None:
    decks = h.decks
    page = h.page
    s = h.s

    def respond(
        body: str,
        sub: str,
        sid: str | None,
        *,
        title: str = TITLE,
        status: int = 200,
        thread: bool = False,
    ) -> Response:
        return page(
            title,
            f"<div class='fm'>{body}</div>",
            sub=sub,
            sid=sid,
            status=status,
            current=PAGE,
            csp=DECK_CSP,
            heading=False,
            deck_css=thread,  # the comment panel's styles live with the deck page's
            extra_css=CSS + (deck_extras.CSS if thread else ""),
            extra_scripts=("social.js", "forum.js") if thread else ("forum.js",),
            main_class="ui-wide",
        )

    def wants_json(request: Request) -> bool:
        return "application/json" in (request.headers.get("accept") or "")

    def problem(exc: DeckError, sub: str, sid: str | None, *, status: int = 400) -> Response:
        if exc.kind == "unavailable" and str(exc) == forum.FORUM_CHANGED:
            body = ui.page_head(TITLE, SUB) + changed_html(str(exc))
            return respond(body, sub, sid)
        if exc.kind == "not_found":
            status = 404
        body = ui.page_head(TITLE, SUB) + ui.notice(str(exc), "error", role="alert") + open_form_html()
        return respond(body, sub, sid, status=status)

    @server.custom_route(PAGE, methods=["GET"], include_in_schema=False)
    async def forum_home(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(PAGE)
        try:
            got = await forum.sections(decks, sub)
        except DeckError as exc:
            return problem(exc, sub, sid)
        body = ui.page_head(TITLE, SUB) + new_thread_html() + sections_html(got)
        return respond(body, sub, sid)

    @server.custom_route(PAGE + "/thread", methods=["GET"], include_in_schema=False)
    async def open_thread(request: Request) -> Response:
        """The little form: a thread's link or number goes to its page."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(PAGE)
        raw = (request.query_params.get("thread") or "").strip()[:200]
        try:
            tid = forum._thread_id(raw)
        except DeckError:
            body = ui.page_head(TITLE, SUB) + open_form_html(raw, OPEN_BAD)
            return respond(body, sub, sid, status=400)
        return RedirectResponse(thread_href(tid), status_code=303)

    BOX_NAMES = ("enter", "withdraw")  # the thread page's boxes with an old address of their own

    @server.custom_route(PAGE + "/thread/{thread_id:path}", methods=["GET"], include_in_schema=False)
    async def thread_page(request: Request) -> Response:
        raw = str(request.path_params["thread_id"])[:200]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(PAGE)
        # the old addresses of the thread's boxes (/forum/thread/<n>/enter and so on): the thread
        # page with that box drawn open, so the strip's buttons work without scripts
        head, _slash, tail = raw.rpartition("/")
        if tail in BOX_NAMES and head.isdigit():
            raw = head
            open_box = tail
        else:
            open_box = ""
        try:
            tid = forum._thread_id(raw)
        except DeckError as exc:
            return problem(exc, sub, sid)
        if raw != str(tid):  # a link pasted into the address: its own page
            return RedirectResponse(thread_href(tid), status_code=303)
        if open_box:
            return await thread_response(request, sub, sid, tid, open_box=open_box)
        try:
            page_no = max(1, int(request.query_params.get("page") or 1))
        except ValueError:
            page_no = 1
        return await thread_response(request, sub, sid, tid, page_no=page_no)

    async def thread_response(
        request: Request,
        sub: str,
        sid: str | None,
        tid: int,
        *,
        page_no: int = 1,
        open_box: str = "",
        box_error: str = "",
        box_values: dict[str, str] | None = None,
        status: int = 200,
    ) -> Response:
        """The thread page; ``open_box`` names one of its boxes (CONTEST_BOXES) drawn open, as
        the old addresses draw it and as a refused press comes back with its words."""
        try:
            t = await forum.thread(decks, sub, tid, page=page_no)
        except DeckError as exc:
            return problem(exc, sub, sid)
        link = state.db.get_link(sub) or {}
        raw_me = str(link.get("archidekt_user_id") or "")
        me = int(raw_me) if raw_me.isdigit() else None
        me_name = str(link.get("archidekt_username") or "")
        entries = None
        if t.get("type") == "contest" and link:
            # the contest's entries, as the contest page reads them (one read, on contest threads only)
            try:
                entries = await decks._call_unslotted(
                    sub, lambda token, *_: decks.client.contest_entries(token, tid)
                )
            except DeckError:
                entries = None
        body = thread_html(
            t,
            csrf=_csrf(s, sid) or "",
            me=me,
            me_name=me_name,
            writes_enabled=bool(s.writes_enabled),
            entries=entries,
            open_box=open_box,
            box_error=box_error,
            box_values=box_values,
        )
        return respond(body, sub, sid, title=t["title"] or TITLE, thread=True, status=status)

    async def pressed(request: Request) -> tuple[str, str | None, int, dict[str, str]] | Response:
        """A press on one of the thread page's forms: the member, the thread and the form."""
        raw = str(request.path_params["thread_id"])[:200]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(PAGE)
        try:
            tid = forum._thread_id(raw)
        except DeckError as exc:
            return problem(exc, sub, sid)
        data = await h.form(request, limit=64_000)
        if not h.check_csrf(sid, data):
            if wants_json(request):
                return JSONResponse(
                    {"ok": False, "message": "This form expired. Reload and try again."}, status_code=403
                )
            return page(TITLE, h.EXPIRED, sub=sub, sid=sid, status=403)
        current_client.set(BROWSER_CLIENT_ID)
        return sub, sid, tid, data

    def to_review(request: Request, p: dict[str, Any]) -> Response:
        """The member's press is on the proposal's review page."""
        url = f"/proposals/{p['proposal_id']}"
        if wants_json(request):
            return JSONResponse({"ok": True, "proposal_id": p["proposal_id"], "review_url": url})
        return RedirectResponse(url, status_code=303)

    async def refused(
        request: Request,
        sub: str,
        sid: str | None,
        tid: int,
        box: str,
        exc: DeckError,
        values: dict[str, str],
    ) -> Response:
        if wants_json(request):
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        return await thread_response(
            request, sub, sid, tid, open_box=box, box_error=str(exc), box_values=values, status=400
        )

    @server.custom_route(PAGE + "/thread/{thread_id}/contest", methods=["POST"], include_in_schema=False)
    async def contest_post(request: Request) -> Response:
        """Enter a deck… and Withdraw… on a contest thread: proposed as the deck page proposes
        them (actions.propose, contest_enter / contest_withdraw) and applied by the member on the
        review page, which shows the description change."""
        got = await pressed(request)
        if isinstance(got, Response):
            return got
        sub, sid, tid, data = got
        withdraw = data.get("entry_action") == "withdraw"
        values = {"deck": (data.get("deck") or "").strip()[:40], "summary": data.get("summary", "")}
        box = "withdraw" if withdraw else "enter"
        try:
            if not values["deck"]:
                raise DeckError("invalid", "Pick a deck first.")
            if withdraw:
                p = await decks.propose_action(
                    sub, "contest_withdraw", deck_id=values["deck"], contest=str(tid)
                )
            else:
                if not values["summary"].strip():
                    raise DeckError("invalid", "A summary is required.")
                p = await decks.propose_action(
                    sub, "contest_enter", deck_id=values["deck"], contest=str(tid), summary=values["summary"]
                )
        except DeckError as exc:
            return await refused(request, sub, sid, tid, box, exc, values)
        return to_review(request, p)

    @server.custom_route(PAGE + "/thread/{thread_id}/report", methods=["POST"], include_in_schema=False)
    async def report_post(request: Request) -> Response:
        """Report comment… on a post: the report action (actions.propose, report_kind comment)
        with the thread as its comment target, sent to its review page for the member's press."""
        got = await pressed(request)
        if isinstance(got, Response):
            return got
        sub, sid, tid, data = got
        raw_id = (data.get("comment_id") or "").strip()[:20]
        raw_page = (data.get("page") or "").strip()[:6]
        values = {
            "message": data.get("message", ""),
            "comment_id": raw_id,
            "page": raw_page or "1",
            "who": (data.get("who") or "")[:150],
        }
        try:
            p = await decks.propose_action(
                sub,
                "report",
                thread_id=str(tid),
                report_kind="comment",
                comment_id=int(raw_id) if raw_id.isdigit() else None,
                page=int(raw_page) if raw_page.isdigit() else 1,
                message=values["message"],
            )
        except DeckError as exc:
            return await refused(request, sub, sid, tid, "reportcomment", exc, values)
        return to_review(request, p)

    @server.custom_route(PAGE + "/{group}/{section}", methods=["GET"], include_in_schema=False)
    async def section_page(request: Request) -> Response:
        group = str(request.path_params["group"])[:100]
        section = str(request.path_params["section"])[:100]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(section_href(group, section))
        q = request.query_params
        order = q.get("order") if q.get("order") in forum.ORDERS else "recent"
        search = (q.get("q") or "").strip()[:100]
        try:
            page_no = max(1, min(int(q.get("page") or 1), forum.MAX_PAGE))
        except ValueError:
            page_no = 1
        try:
            got = await forum.threads(decks, sub, group, section, page=page_no, order=order, search=search)
        except DeckError as exc:
            if wants_json(request):
                return JSONResponse(
                    {"ok": False, "message": str(exc)}, status_code=400, headers={"Cache-Control": "no-store"}
                )
            return problem(exc, sub, sid)
        if wants_json(request):
            # Load more with scripts: the next page's rows, appended in place (static/forum.js)
            has_more = got["page"] < got["pages"]
            return JSONResponse(
                {
                    "ok": True,
                    "html": thread_rows_html(got["threads"]),
                    "has_more": has_more,
                    "next": _list_href(group, section, page=got["page"] + 1, order=order, q=search),
                    **load_more_words(got),
                },
                headers={"Cache-Control": "no-store"},
            )
        return respond(threads_html(got), sub, sid, title=f"{section}: {TITLE}")
