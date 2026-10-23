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

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import forum, ui
from .companion import DECK_CSP
from .decks import DeckError
from .pages import _csrf, browser_session, login_redirect
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
REPLY_PUBLIC = "Posted publicly on Archidekt under your Archidekt name, after you confirm."
WRITES_OFF = "Archidekt writes are switched off on this gateway, so replies can be read here but not posted."
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
.fm .ui-table .fm-last .deck{display:block}
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
.fm .loadmore .btn[aria-busy=true]{cursor:progress;opacity:.7}
@media (max-width:599.98px){
  .fm .fm-why .btn{flex:1 1 100%}
  .fm .ui-head.phead .acts{flex:1 1 100%}
  .fm .ui-head.phead .acts .btn{width:100%}
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
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}s"


def new_thread_html(section: str = "") -> str:
    """The plain button that opens Archidekt's New thread page in a new tab, with the one line why."""
    url = NEW_THREAD_URL + (f"?section={quote(section, safe='')}" if section else "")
    return (
        f"<div class='fm-why'><a class='btn btn-plain' href='{esc(url)}' target='_blank' "
        f"rel='noreferrer noopener'>{icon('external')}{esc(NEW_THREAD)}</a>"
        f"<span>{esc(NEW_THREAD_WHY)}</span></div>"
    )


def crumbs_html(*parts: tuple[str, str]) -> str:
    """Forum › group › section: each but the last a link."""
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
        out.append(ui.list_head(g["group"]))
        out.append(
            ui.table_open("minmax(0,1fr) 7rem", "minmax(0,1fr) 6rem", "minmax(0,1fr) auto", label=g["group"])
            + ui.table_header([ui.Column("Section"), ui.Column("Posts", "fm-n")])
            + "".join(rows)
            + "</div>"
        )
    if not out:
        out.append("<p class='muted'>Archidekt's forum lists no sections right now.</p>")
    return "".join(out)


def thread_rows_html(threads: list[dict[str, Any]]) -> str:
    """One row a thread: a link to its page; the author, responses and points fold under the title
    on narrower screens, and the last post's date stays at the end."""
    rows = []
    for t in threads:
        mark = MARKS.get(t["type"])
        label = ui.label(*mark) if mark else ""
        if t["archived"]:
            label += ui.label("Archived", "grey", "lock")
        last = t["last_post"]
        when = _when(last["at"])
        by = f"<span class='deck'>{esc(last['by'])}</span>" if last["by"] else ""
        sub = " · ".join(
            x
            for x in (
                esc(t["owner"]),
                _plural(t["responses"], "response"),
                _plural(t["points"], "point"),
                f"<span class='sm-only'>{when}</span>" if when else "",
            )
            if x
        )
        rows.append(
            f"<a class='tr' href='{thread_href(t['id'])}' data-id='{t['id']}'>"
            f"<span class='title'><span class='t'>{esc(t['title'])}{label}</span>"
            f"<span class='sub'>{sub}</span></span>"
            f"<span class='md-hide'>{esc(t['owner'])}</span>"
            f"<span class='md-hide fm-n'>{t['responses']:,}</span>"
            f"<span class='md-hide fm-n'>{t['points']:,}</span>"
            f"<span class='fm-last sm-hide'>{when}{by}</span></a>"
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
            "minmax(0,2.6fr) 9rem 6rem 5rem 9rem",
            "minmax(0,1fr) 9rem",
            "minmax(0,1fr)",
            cls="fm-threads",
            label="Threads",
        )
        + ui.table_header(
            [
                ui.Column("Thread"),
                ui.Column("Started by", "md-hide"),
                ui.Column("Responses", "md-hide fm-n"),
                ui.Column("Points", "md-hide fm-n"),
                ui.Column("Last post", "sm-hide"),
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
        crumbs_html((group, ""), (section, section_href(group, section)))
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
        f"<article class='cmt' data-id='{c['id']}'><div class='who'><b>{esc(c['owner']['username'])}</b>"
        f"<span>· {_when(c['created_at']) or esc(c['created_at'])}{edited}{pts}</span></div>"
        f"<p>{esc(c['text'])}</p>{replies}</article>"
    )


def thread_html(t: dict[str, Any], *, csrf: str, me: int | None, writes_enabled: bool) -> str:
    """A thread: the first post, the locked or archived note, the replies in the deck page's
    comment panel and the reply box. The thread's replies go to the script as JSON, in the shape
    the comment route answers, so the panel draws them without reading the thread again."""
    tid = t["id"]
    target = forum.target(tid)
    closed = t["locked"] or t["archived"]
    can_write = writes_enabled and not closed
    opens = (
        f"<a class='btn btn-plain' href='{esc(t['url'])}' target='_blank' rel='noreferrer noopener'>"
        f"{icon('external')}Open on Archidekt</a>"
    )
    sub = f"started by {t['owner']} · {_plural(t['reply_count'], 'reply')}"
    initial = esc((t["owner"] or "?")[:1].upper())
    when = _when(t["created_at"])
    post = (
        f"<article class='fm-post'><div class='ph'><span class='avatar' aria-hidden='true'>{initial}</span>"
        f"<b>{esc(t['owner'])}</b>{when}</div>"
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
    data = json.dumps(pre, separators=(",", ":")).replace("</", "<\\/")
    if can_write:
        form = (
            "<form class='newcomment'>"
            f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
            "<label for='ctext'>Your reply</label>"
            "<textarea id='ctext' name='text' rows='3' maxlength='2000' "
            "placeholder='Write a reply'></textarea>"
            "<div class='replyto' hidden></div>"
            f"<button type='submit' class='btn'>{icon('send')} Post reply</button>"
            f"<p class='muted small'>{esc(REPLY_PUBLIC)}</p></form>"
        )
    elif closed:
        form = ""
    else:
        form = f"<p class='muted small'>{esc(WRITES_OFF)}</p>"
    replies = "".join(_comment_html(c) for c in t["replies"]) or "<p class='muted'>No replies yet.</p>"
    panel = (
        f"<section class='panel comments' id='comments' data-deck='{target}' "
        f"data-writes='{'on' if can_write else 'off'}' data-root='{tid}'>"
        f"<div class='panel-head'><h2>Replies</h2></div>"
        f"<script type='application/json' id='forum-thread'>{data}</script>"
        f"<div class='thread' data-src='/social/api/decks/{target}/comments'>"
        f"{replies}</div>"
        f"{form}</section>"
    )
    return crumbs_html() + ui.page_head(t["title"], sub, actions_html=opens) + note + post + panel


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
            extra_css=CSS,
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

    @server.custom_route(PAGE + "/thread/{thread_id:path}", methods=["GET"], include_in_schema=False)
    async def thread_page(request: Request) -> Response:
        raw = str(request.path_params["thread_id"])[:200]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(PAGE)
        try:
            tid = forum._thread_id(raw)
        except DeckError as exc:
            return problem(exc, sub, sid)
        if raw != str(tid):  # a link pasted into the address: its own page
            return RedirectResponse(thread_href(tid), status_code=303)
        try:
            page_no = max(1, int(request.query_params.get("page") or 1))
        except ValueError:
            page_no = 1
        try:
            t = await forum.thread(decks, sub, tid, page=page_no)
        except DeckError as exc:
            return problem(exc, sub, sid)
        link = state.db.get_link(sub) or {}
        raw_me = str(link.get("archidekt_user_id") or "")
        me = int(raw_me) if raw_me.isdigit() else None
        body = thread_html(t, csrf=_csrf(s, sid) or "", me=me, writes_enabled=bool(s.writes_enabled))
        return respond(body, sub, sid, title=t["title"] or TITLE, thread=True)

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
