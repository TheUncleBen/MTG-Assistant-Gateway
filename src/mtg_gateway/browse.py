"""Browsing Archidekt the way its site does: deck search and public profiles.

``/search`` is the gateway's counterpart of archidekt.com/search/decks: filter public decks by
name, commander, owner, format, colour identity and sort order, page through them and open any
of them on the gateway's own deck page. ``/users/{username}`` is a public profile: that person's
public decks. Both read Archidekt anonymously (only public decks come back) through the member's
Archidekt budget, and both need the gateway sign-in like every other page. The same search is
offered as the ``search_decks`` MCP tool (``owner`` for one user's public decks) and under ``/api/v1``.

Deck art on the result cards is shown from Scryfall by the card's id, which Archidekt's listing
names in its ``featured`` URL; no Archidekt image is loaded.
"""

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlencode

from pydantic import Field
from starlette.requests import Request
from starlette.responses import Response

from .archidekt import FORMAT_IDS, FORMAT_NAMES, SEARCH_ORDERS, format_label, list_row
from .busy import busy_response
from .deckpage import DECK_CSS, avatar_html, covers_for, deck_list_html
from .decks import DeckError
from .pages import _csrf, browser_session, login_redirect
from .theme import icon, render

SearchOrder = Literal["-viewCount", "-updatedAt", "-createdAt", "-size", "edhBracket"]  # SEARCH_ORDERS

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

logger = logging.getLogger(__name__)


async def commander_names(state: Any, text: str) -> list[str]:
    """Cards that can be a commander whose name contains ``text`` (Scryfall), for a commander
    search Archidekt answered with nothing: it matches only a commander's full name. Empty when
    Scryfall is not loaded or the lookup fails (the plain empty answer then stands)."""
    scryfall = getattr(getattr(state, "scan", None), "scryfall", None)
    if scryfall is None:
        return []
    try:
        return await scryfall.commander_names(text)
    except Exception as exc:
        logger.info("commander lookup failed: %s", type(exc).__name__)
        return []


SEARCH_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; "
    "manifest-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
COLOURS = (("W", "White"), ("U", "Blue"), ("B", "Black"), ("R", "Red"), ("G", "Green"))
FORMAT_OPTIONS = sorted(
    ((str(i), format_label(FORMAT_NAMES[i])) for i in sorted(FORMAT_NAMES)), key=lambda kv: kv[1].lower()
)
BROWSE_CSS = """
/* The deck search form: one grid of equal-height controls, labels on one baseline, the colour
   toggles and the buttons on a closing row that stacks on a phone. */
.searchbar{padding:1.1rem 1.1rem 1rem}
.searchbar .controls{display:grid;gap:.85rem 1rem;align-items:start;
  grid-template-columns:repeat(auto-fit,minmax(min(100%,12rem),1fr))}
.searchbar .controls .field{margin:0;gap:.3rem}
.searchbar .field > label,.searchbar .field .lbl{display:block;font-size:.93rem;font-weight:700;margin:0;
  line-height:1.3;color:var(--text)}
.searchbar .field .lbl{margin-bottom:.3rem}
.searchbar .field input,.searchbar .field .sel,.searchbar .field select{height:var(--ctl);margin:0}
.searchbar .field .suggest{display:block}
.searchbar .foot{grid-column:1 / -1;display:flex;flex-wrap:wrap;align-items:center;
  justify-content:space-between;gap:.75rem 1.5rem;margin-top:.15rem;padding-top:.9rem;
  border-top:1px solid var(--border-soft)}
.searchbar .foot .field{flex:1 1 20rem;min-width:0}
.searchbar .colours{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem .5rem}
.searchbar .colours label{display:inline-flex;align-items:center;gap:.35rem;margin:0;cursor:pointer;
  font-weight:400;height:34px;padding:0 .75rem 0 .5rem;border:1px solid var(--border);border-radius:17px;
  background:var(--surface);transition:border-color .2s ease,background-color .2s ease;user-select:none}
.searchbar .colours label:hover{border-color:var(--orange)}
.searchbar .colours label:has(input:checked){border-color:var(--orange);background:var(--orange-tint)}
.searchbar .colours label:has(input:focus-visible){outline:2px solid var(--focus);outline-offset:2px}
.searchbar .colours input{position:absolute;width:1px;height:1px;margin:-1px;padding:0;border:0;
  overflow:hidden;clip:rect(0,0,0,0);opacity:0}
.searchbar .form-actions{margin:0;flex:0 0 auto;justify-content:flex-end;gap:.5rem}
.searchbar .form-actions .btn,.searchbar .form-actions button{margin:0;height:var(--ctl)}
.searchbar .form-actions .btn-primary{min-width:11rem}
.searchbar .form-actions .cards{gap:.4rem}
@media (min-width:1200px){ .searchbar .controls{
  grid-template-columns:minmax(0,2fr) minmax(0,2fr) minmax(0,1.5fr) minmax(10rem,1fr) minmax(10rem,1fr)} }
@media (max-width:600px){ .searchbar .foot{flex-direction:column;align-items:stretch}
  .searchbar .foot .field{flex:0 0 auto}
  .searchbar .form-actions{display:grid;grid-template-columns:1fr 1fr}
  .searchbar .form-actions .btn-primary{grid-column:1 / -1;min-width:0} }
.results-head{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;
  margin:0 0 .75rem}
.results-head .muted{margin:0}
.results-head h2,.results-head .muted{min-width:0;overflow-wrap:anywhere}
.pager{display:flex;justify-content:center;align-items:center;gap:.5rem;margin:1rem 0}
.pager .btn{margin:0}
/* Avatar, name and the social row: the name block takes the room and wraps a long unbroken name;
   the social row keeps its own size (Follow ellipsises inside it) and drops under the name when the
   two no longer fit side by side, so neither squeezes the other (gate R5-2: a 64-character name). */
.profile{display:flex;flex-wrap:wrap;align-items:center;gap:1rem;margin:0 0 1rem;min-width:0}
.profile .who .uname{overflow-wrap:anywhere}
.profile .who{display:flex;flex-direction:column;flex:1 1 16rem;min-width:0}
.profile .who .uname{font-size:1.5rem;font-weight:900}
.profile .who .sub{color:var(--text-muted)}
.profile .social{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-left:auto;flex:0 0 auto;
  max-width:100%}
.profile .social .btn,.profile .social .soc{margin:0;display:inline-flex;align-items:center;gap:.4rem}
.profile .social .soc[hidden]{display:none}
.profile .social .soc.on{background:var(--orange);border-color:var(--orange);color:#fff}
/* The follow question grows with its text: a long name wraps onto more lines and the Follow and
   No buttons stay inside the chip instead of dropping under the link (gate R5-1). */
.profile .social .confirm{display:inline-flex;align-items:center;flex-wrap:wrap;gap:.4rem;
  background:var(--surface-2);border-radius:17px;padding:.25rem .35rem .25rem .85rem;min-height:34px;
  max-width:100%;box-sizing:border-box;font-size:.9rem;font-weight:700}
.profile .social .confirm button{margin:0;height:26px;padding:0 .7rem;border-radius:13px;font-size:.85rem}
.profile .social .note{flex-basis:100%;color:var(--text-muted);font-size:.9rem;margin:0}
@media (max-width:600px){ .profile .social{margin-left:0;flex-basis:100%} }
.popular{display:flex;flex-wrap:wrap;gap:.4rem;margin:.5rem 0 0}
.popular a{display:inline-flex;align-items:center;height:30px;padding:0 .7rem;border-radius:15px;
  background:var(--surface-2);color:var(--text);text-decoration:none;font-size:.9rem;
    border:1px solid var(--border)}
.popular a:hover{border-color:var(--orange);color:var(--orange)}
.popular a.precons{gap:.4rem;font-weight:700}
.preconset summary{display:flex;align-items:center;justify-content:space-between;gap:1rem;cursor:pointer;
  font-weight:700;font-size:1.1rem;padding:.25rem 0}
.preconset[open] summary{margin-bottom:.75rem}
.preconset .decklist{margin-bottom:0}
.listbar .controls{display:flex;flex-wrap:wrap;gap:1rem;align-items:end}
.listbar .controls .field{margin:0}
.listbar .controls .grow{flex:1 1 16rem}
"""


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def read_query(qp: Any) -> dict[str, Any]:
    """The search form's fields, cleaned: unknown values fall back to the defaults."""
    fmt = (qp.get("format") or "").strip()
    colors = "".join(
        c for c, _n in COLOURS if qp.get(f"c{c}") == "1" or c in (qp.get("colors") or "").upper()
    )
    try:
        page = max(1, min(int(qp.get("page") or 1), 17))  # Archidekt caps a listing at 1000 rows
    except ValueError:
        page = 1
    return {
        "name": (qp.get("name") or qp.get("q") or "").strip()[:120],
        "commander": (qp.get("commander") or "").strip()[:120],
        "owner": (qp.get("owner") or "").strip()[:120],  # a profile's username comes through here too
        "deck_format": int(fmt) if fmt.isdigit() and int(fmt) in FORMAT_NAMES else None,
        "colors": colors,
        "order_by": qp.get("order") if qp.get("order") in SEARCH_ORDERS else "-viewCount",
        "page": page,
    }


def search_link(query: dict[str, Any], **over: Any) -> str:
    q = {**query, **over}
    params: dict[str, Any] = {}
    if q.get("name"):
        params["name"] = q["name"]
    if q.get("commander"):
        params["commander"] = q["commander"]
    if q.get("owner"):
        params["owner"] = q["owner"]
    if q.get("deck_format") is not None:
        params["format"] = q["deck_format"]
    for c in q.get("colors") or "":
        params[f"c{c}"] = "1"
    if q.get("order_by") and q["order_by"] != "-viewCount":
        params["order"] = q["order_by"]
    if q.get("page", 1) > 1:
        params["page"] = q["page"]
    return "/search" + ("?" + urlencode(params) if params else "")


def search_form_html(query: dict[str, Any]) -> str:
    fmt_opts = "<option value=''>Any format</option>" + "".join(
        f"<option value='{v}'{' selected' if str(query['deck_format']) == v else ''}>{_esc(n)}</option>"
        for v, n in FORMAT_OPTIONS
    )
    order_opts = "".join(
        f"<option value='{_esc(k)}'{' selected' if k == query['order_by'] else ''}>{_esc(v)}</option>"
        for k, v in SEARCH_ORDERS.items()
    )
    colours = "".join(
        f"<label><input type='checkbox' name='c{c}' value='1'{' checked' if c in query['colors'] else ''}>"
        f"<i class='pip pip-{c}' aria-hidden='true'></i>{n}</label>"
        for c, n in COLOURS
    )
    # the card search for the same text: a commander's name is a card name, a deck name may be
    cards_q = query["commander"] or query["name"]
    cards_link = "/cards" + (f"?{urlencode({'q': cards_q})}" if cards_q else "")
    return (
        "<section class='panel searchbar'><form method='get' "
        "action='/search' class='controls' id='searchform'>"
        "<div class='field'><label for='s-name'>Deck name</label>"
        "<input id='s-name' type='search' name='name' "
        f"value='{_esc(query['name'])}' placeholder='Any part of the name'></div>"
        "<div class='field'><label for='s-cmd'>Commander</label>"
        f"<input id='s-cmd' type='text' name='commander' value='{_esc(query['commander'])}' "
        "placeholder='Card name, e.g. Atraxa, Praetors&#39; Voice' data-suggest='cards' "
        "autocomplete='off'></div>"
        "<div class='field'><label for='s-owner'>Owner</label>"
        "<input id='s-owner' type='text' name='owner' "
        f"value='{_esc(query['owner'])}' placeholder='Archidekt username'></div>"
        f"<div class='field'><label for='s-format'>Format</label><span class='sel'>{icon('decks')}"
        f"<select id='s-format' name='format'>{fmt_opts}</select></span></div>"
        f"<div class='field'><label for='s-order'>Sort by</label><span class='sel'>{icon('sort')}"
        f"<select id='s-order' name='order'>{order_opts}</select></span></div>"
        "<div class='foot'><div class='field'><span class='lbl'>Colour "
        f"identity</span><div class='colours'>{colours}</div></div>"
        "<div class='form-actions'><button type='submit' "
        f"class='btn-primary'>{icon('search')} Search decks</button>"
        "<a class='btn' href='/search'>Clear</a>"
        f"<a class='btn cards' href='{_esc(cards_link)}' title='Find a card and add it to a deck'>"
        f"{icon('image')} Search cards</a></div></div>"
        "</form></section>"
    )


def results_html(found: dict[str, Any], query: dict[str, Any], *, heading: str) -> str:
    rows = found["decks"]
    covers = {
        r["id"]: {"scryfall_uid": r.get("featured_scryfall_id")}
        for r in rows
        if r.get("featured_scryfall_id")
    }
    count = found.get("count")
    if isinstance(count, int):
        about = f"About {count:,} decks" if count >= 1000 else f"{count} deck{'' if count == 1 else 's'}"
    else:
        about = ""
    head = f"<div class='results-head'><h2>{_esc(heading)}</h2><p class='muted'>{_esc(about)}</p></div>"
    if not rows:
        return (
            head + "<section class='panel'><p class='muted'>No public decks match. Try fewer filters.</p>"
            "</section>"
        )
    body = deck_list_html(rows, covers=covers, view="grid", show_owner=True)
    pager = "<nav class='pager' aria-label='Pages'>"
    if query["page"] > 1:
        pager += f"<a class='btn' href='{_esc(search_link(query, page=query['page'] - 1))}'>Previous</a>"
    pager += f"<span class='muted small'>Page {query['page']}</span>"
    if found.get("has_more") and query["page"] < 17:
        pager += f"<a class='btn' href='{_esc(search_link(query, page=query['page'] + 1))}'>Next</a>"
    pager += "</nav>"
    return head + body + pager


def add_browse_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings
    decks = state.decks

    def page(
        title: str, body: str, *, sub: str, sid: str | None, current: str = "/search", status: int = 200
    ) -> Response:
        user = state.db.get_user(sub) or {}
        admin = bool(s.admin_group and s.admin_group in (user.get("groups") or []))
        resp = render(
            title,
            body,
            site=s.server_name,
            status=status,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=admin,
            wide=True,
            current=current,
            scripts=True,
            head_extra=f"<style>{DECK_CSS}{BROWSE_CSS}</style><script src='/static/deck.js' defer></script>",
        )
        resp.headers["Content-Security-Policy"] = SEARCH_CSP
        return resp

    def problem(exc: DeckError) -> str:
        text = {
            "rate_limited": str(exc),
            "unavailable": "Archidekt is not answering right now. Try again in a moment.",
        }.get(exc.kind, "The search could not be run: " + str(exc))
        return f"<p class='notice error'>{_esc(text)}</p>"

    @server.custom_route("/search", methods=["GET"], include_in_schema=False)
    async def search_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/search" + (f"?{request.url.query}" if request.url.query else ""))
        query = read_query(request.query_params)
        asked = bool(
            query["name"]
            or query["commander"]
            or query["owner"]
            or query["deck_format"] is not None
            or query["colors"]
        )
        body = search_form_html(query)
        if asked:
            note = ""
            try:
                found = await decks.search_decks(sub, **query)
                if query["commander"] and not found["decks"]:
                    names = await commander_names(state, query["commander"])
                    if not any(n.casefold() == query["commander"].casefold() for n in names):
                        if len(names) == 1:
                            query = {**query, "commander": names[0]}
                            found = await decks.search_decks(sub, **query)
                            note = f"<p class='notice'>Showing decks led by {_esc(names[0])}.</p>"
                        elif names:
                            links = ", ".join(
                                f"<a href='{_esc(search_link(query, commander=n, page=1))}'>{_esc(n)}</a>"
                                for n in names
                            )
                            note = (
                                "<p class='notice'>No commander has exactly that name. "
                                f"Did you mean: {links}?</p>"
                            )
            except DeckError as exc:
                if busy := busy_response(exc, request, page, sub=sub, sid=sid):
                    return busy
                body += problem(exc)
            else:
                body += note
                bits = [b for b in (query["name"], query["commander"], query["owner"]) if b]
                heading = "Decks" + (" matching " + ", ".join(bits) if bits else "")
                body += results_html(found, query, heading=heading)
        else:
            popular = "".join(
                f"<a href='{_esc(search_link(read_query({}), **q))}'>{_esc(label)}</a>"
                for label, q in (
                    ("Most viewed Commander decks", {"deck_format": 3, "order_by": "-viewCount"}),
                    ("Newest Commander decks", {"deck_format": 3, "order_by": "-createdAt"}),
                    ("Modern", {"deck_format": 2}),
                    ("Standard", {"deck_format": 1}),
                    ("Pauper", {"deck_format": 6}),
                    ("Pioneer", {"deck_format": 7}),
                    ("Oathbreaker", {"deck_format": 10}),
                )
            )
            body += (
                "<section class='panel'><h2>Browse public decks</h2>"
                "<p class='muted'>Search every public deck on "
                "Archidekt by name, commander, owner, format and "
                "colours, sorted the way the site sorts them. Open "
                "a deck to see it on the gateway, with the same "
                "views, stats and export as your own; clone it to your account from its page.</p>"
                f"<div class='popular'>{popular}"
                f"<a class='precons' href='/precons'>{icon('box')} Preconstructed decks by set</a></div>"
                "</section>"
            )
        return page("Search decks", body, sub=sub, sid=sid)

    @server.custom_route("/precons", methods=["GET"], include_in_schema=False)
    async def precons_page(request: Request) -> Response:
        """Archidekt's preconstructed decks, grouped by set as the site lists them (newest set
        first). A filter narrows by set or deck name; every deck opens on the gateway like any
        public deck and can be cloned from there."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/precons")
        q = (request.query_params.get("q") or "").strip()[:80]
        try:
            listing = await decks.precons(sub)
        except DeckError as exc:
            if busy := busy_response(exc, request, page, sub=sub, sid=sid):
                return busy
            return page(
                "Preconstructed decks", precons_form(q) + problem(exc), sub=sub, sid=sid, current="/search"
            )
        sections = []
        shown = 0
        for i, (set_name, raw_rows) in enumerate(listing.items()):
            rows = [list_row(d) for d in raw_rows]
            if q:
                ql = q.lower()
                if ql not in set_name.lower():
                    rows = [r for r in rows if ql in r["name"].lower()]
            if not rows:
                continue
            shown += len(rows)
            opened = " open" if q or i < 3 else ""
            sections.append(
                f"<details class='panel preconset'{opened}><summary><span class='setname'>{_esc(set_name)}"
                f"</span><span class='muted small'>{len(rows)} deck{'' if len(rows) == 1 else 's'}</span>"
                f"</summary>{deck_list_html(rows, covers=covers_for(rows), view='grid')}</details>"
            )
        if not sections:
            sections.append(
                "<section class='panel'><p class='muted'>No preconstructed deck matches that filter.</p>"
                "</section>"
            )
        total = sum(len(v) for v in listing.values())
        head = (
            "<div class='results-head'><h2>By set</h2>"
            f"<p class='muted'>{shown if q else total} of {total} decks in {len(listing)} sets, as Archidekt "
            "lists them. Open one to see it here, clone it to your account from its page, or compare it with "
            "your build.</p></div>"
        )
        return page(
            "Preconstructed decks",
            precons_form(q) + head + "".join(sections),
            sub=sub,
            sid=sid,
            current="/search",
        )

    def precons_form(q: str) -> str:
        return (
            "<section class='panel listbar'><form method='get' action='/precons' class='controls'>"
            "<div class='field grow'><label for='q'>Filter by set or deck name</label><span class='search'>"
            f"<input id='q' type='search' name='q' value='{_esc(q)}' placeholder='Example: Bloomburrow'>"
            f"<button type='submit' aria-label='Filter'>{icon('search')}</button></span></div>"
            f"<div class='field'><a class='btn' href='/search'>{icon('search')} Search all decks</a></div>"
            "</form></section>"
        )

    @server.custom_route("/users/{username}", methods=["GET"], include_in_schema=False)
    async def user_page(request: Request) -> Response:
        username = request.path_params["username"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/users/{username}")
        query = read_query({**request.query_params, "owner": username})
        query["order_by"] = (
            request.query_params.get("order")
            if request.query_params.get("order") in SEARCH_ORDERS
            else "-updatedAt"
        )
        try:
            found = await decks.search_decks(sub, **query)
        except DeckError as exc:
            if busy := busy_response(exc, request, page, sub=sub, sid=sid):
                return busy
            return page(
                username,
                problem(exc),
                sub=sub,
                sid=sid,
                current="/search",
                status=400 if exc.kind == "invalid" else 502,
            )
        rows = found["decks"]
        shown = rows[0]["owner"] if rows else username
        owner_id = rows[0].get("owner_id") if rows else None
        link = state.db.get_link(sub) or {}
        follow = (
            f"<button type='button' class='btn soc' data-social='follow' data-user='{_esc(owner_id)}' "
            f"data-name='{_esc(shown)}' data-state='unknown' title='Follow {_esc(shown)} on Archidekt'>"
            f"{icon('follow')}<span>Follow {_esc(shown)}</span></button>"
            if owner_id and str(link.get("archidekt_user_id") or "") != str(owner_id)
            else ""
        )
        order_opts = "".join(
            f"<option value='{_esc(k)}'{' selected' if k == query['order_by'] else ''}>{_esc(v)}</option>"
            for k, v in SEARCH_ORDERS.items()
        )
        head = (
            f"<section class='panel profile'>{avatar_html(shown, 'xl')}<div class='who'>"
            f"<span class='uname'>{_esc(shown)}</span>"
            f"<span class='sub'>{'Public decks on Archidekt' if rows else 'No public decks, or no such user'}"
            "</span></div>"
            f"<div class='social'>{follow}"
            f"<a class='btn ext' href='https://archidekt.com/u/{_esc(shown)}' "
            "target='_blank' rel='noreferrer noopener'>"
            f"{icon('external')} Archidekt profile</a></div></section>"
            f"<form method='get' class='panel listbar' id='listform'><div class='controls'>"
            f"<div class='field'><label for='f-order'>Sort by</label><span class='sel'>{icon('sort')}"
            f"<select id='f-order' name='order'>{order_opts}</select></span></div>"
            "<noscript><button type='submit' class='apply'>Apply</button></noscript></div></form>"
        )
        return page(shown, head + results_html(found, query, heading="Decks"), sub=sub, sid=sid)


def add_browse_tools(server: MCPServer, state: AppState) -> None:
    from mcp.server.auth.middleware.auth_context import get_access_token

    decks = state.decks

    def _sub() -> str:
        token = get_access_token()
        if token is None or not token.subject:
            raise RuntimeError("no authenticated user on this request")
        return token.subject

    async def _commander_names(text: str) -> list[str]:
        return await commander_names(state, text)

    @server.tool(
        name="search_decks",
        title="Search public Archidekt decks",
        description=(
            "Search Archidekt's public decks the way its deck search page does. Filters, all optional: "
            "`name` (part of the deck name), `commander` (a commander's card name; a partial name is "
            "looked up and, if several commanders match, returned as commander_suggestions), "
            "`owner` (Archidekt username), `format` (" + ", ".join(sorted(FORMAT_IDS)) + "), "
            "`colors` (letters from WUBRG: colour identity within those colours), "
            "`order_by` (-viewCount, -updatedAt, -createdAt, -size, edhBracket), `page` (60 decks a "
            "page) and `limit` (how many of the page to return, default 20). For one user's decks "
            "(their profile), give `owner` with order_by -updatedAt. Each result has an id and url; "
            "read one with get_deck. This is the one tool for public deck lists; list_my_decks lists "
            "the signed-in member's own decks, private ones included. Read-only."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def search_decks(
        name: Annotated[str | None, Field(description="Part of the deck name.")] = None,
        commander: Annotated[str | None, Field(description="A commander's card name.")] = None,
        owner: Annotated[str | None, Field(description="An Archidekt username.")] = None,
        format: Annotated[str | None, Field(description="commander, modern, standard, ...")] = None,  # noqa: A002
        colors: Annotated[
            str | None, Field(description="WUBRG letters: colour identity within them.")
        ] = None,
        order_by: Annotated[
            SearchOrder, Field(description="Sort order (default most viewed).")
        ] = "-viewCount",
        page: Annotated[int, Field(description="Page of 60 decks, from 1.")] = 1,
        limit: Annotated[int, Field(description="How many of the page to return, 1 to 60.")] = 20,
    ) -> dict[str, Any]:
        fmt = FORMAT_IDS.get((format or "").strip().lower()) if format else None
        if format and fmt is None and not str(format).isdigit():
            return {"ok": False, "error": "invalid", "message": f"unknown format '{format[:30]}'"}

        async def run(commander_name: str) -> dict[str, Any]:
            return await decks.search_decks(
                _sub(),
                name=(name or "").strip(),
                commander=commander_name,
                owner=(owner or "").strip(),
                deck_format=fmt
                if fmt is not None
                else (int(format) if format and str(format).isdigit() else None),
                colors=(colors or "").strip(),
                order_by=order_by if order_by in SEARCH_ORDERS else "-viewCount",
                page=max(1, min(int(page or 1), 17)),
            )

        commander = (commander or "").strip()
        matched: str | None = None
        try:
            found = await run(commander)
            if commander and not found["decks"]:
                # Archidekt matches a commander only by its full name, so "Krenko" finds nothing.
                # Look the name up among cards that can be commanders: one match is searched
                # again; several come back for the assistant to pick from.
                names = await _commander_names(commander)
                exact = [n for n in names if n.casefold() == commander.casefold()]
                if len(names) == 1 and not exact:
                    matched = names[0]
                    found = await run(matched)
                elif len(names) > 1 and not exact:
                    return {
                        "ok": True,
                        "decks": [],
                        "count": 0,
                        "commander_suggestions": names,
                        "message": f"no commander is named exactly '{commander[:60]}'; search again "
                        "with one of commander_suggestions",
                    }
        except DeckError as exc:
            return {"ok": False, "error": exc.kind, "message": str(exc)}
        if matched:
            found = {**found, "commander_matched": matched}
        shown = max(1, min(int(limit or 20), 60))
        if len(found["decks"]) > shown:
            found = {**found, "decks": found["decks"][:shown], "more_on_page": True}
        for d in found["decks"]:
            d["url"] = f"https://archidekt.com/decks/{d['id']}"
            d["gateway_url"] = f"{state.settings.public_url}/decks/{d['id']}"
        return {"ok": True, **found}
