"""Browsing Archidekt the way its site does: deck search and public profiles.

``/search`` is the gateway's counterpart of archidekt.com/search/decks: filter public decks by
name, commander, owner, format, colour identity and sort order, page through them and open any
of them on the gateway's own deck page. ``/users/{username}`` is a public profile: that person's
public decks. Both read Archidekt anonymously (only public decks come back) through the member's
Archidekt budget, and both need the gateway sign-in like every other page. The same search is
offered as the ``search_decks`` and ``archidekt_user`` MCP tools and under ``/api/v1``.

Deck art on the result cards is shown from Scryfall by the card's id, which Archidekt's listing
names in its ``featured`` URL; no Archidekt image is loaded.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import Response

from .archidekt import FORMAT_NAMES, SEARCH_ORDERS
from .deckpage import DECK_CSS, avatar_html, deck_list_html
from .decks import DeckError
from .pages import _csrf, browser_session, login_redirect
from .theme import icon, render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

SEARCH_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
COLOURS = (("W", "White"), ("U", "Blue"), ("B", "Black"), ("R", "Red"), ("G", "Green"))
FORMAT_OPTIONS = [(str(i), FORMAT_NAMES[i].capitalize()) for i in sorted(FORMAT_NAMES)]
BROWSE_CSS = """
.searchbar .controls{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,2fr) minmax(0,
  1.5fr) minmax(8rem,1fr) minmax(8rem,1fr);
  gap:1rem;align-items:end}
.searchbar .colours{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem;min-height:var(--ctl)}
.searchbar .colours label{display:inline-flex;align-items:center;gap:.3rem;margin:0;cursor:pointer;
  font-weight:400}
.searchbar .colours input{width:auto;height:auto;margin:0}
.searchbar .go{display:flex;gap:.5rem;align-items:end}
.searchbar .go .btn,.searchbar .go button{margin:0}
.searchbar .field .lbl{display:block;font-size:.93rem;font-weight:700;margin-bottom:.3rem}
@media (max-width:1000px){ .searchbar .controls{grid-template-columns:1fr 1fr} }
@media (max-width:600px){ .searchbar .controls{grid-template-columns:1fr} }
.results-head{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;
  margin:0 0 .75rem}
.results-head .muted{margin:0}
.pager{display:flex;justify-content:center;align-items:center;gap:.5rem;margin:1rem 0}
.pager .btn{margin:0}
.profile{display:flex;align-items:center;gap:1rem;margin:0 0 1rem}
.profile .who{display:flex;flex-direction:column;min-width:0}
.profile .who .uname{font-size:1.5rem;font-weight:900}
.profile .who .sub{color:var(--text-muted)}
.profile .social{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-left:auto}
.profile .social .btn,.profile .social .soc{margin:0;display:inline-flex;align-items:center;gap:.4rem}
.profile .social .soc.on{background:var(--orange);border-color:var(--orange);color:#fff}
.profile .social .confirm{display:inline-flex;align-items:center;gap:.4rem;background:var(--surface-2);
  border-radius:17px;padding:0 .35rem 0 .85rem;height:34px;font-size:.9rem;font-weight:700}
.profile .social .confirm button{margin:0;height:26px;padding:0 .7rem;border-radius:13px;font-size:.85rem}
.profile .social .note{flex-basis:100%;color:var(--text-muted);font-size:.9rem;margin:0}
@media (max-width:600px){ .profile{flex-wrap:wrap} .profile .social{margin-left:0;flex-basis:100%} }
.popular{display:flex;flex-wrap:wrap;gap:.4rem;margin:.5rem 0 0}
.popular a{display:inline-flex;align-items:center;height:30px;padding:0 .7rem;border-radius:15px;
  background:var(--surface-2);color:var(--text);text-decoration:none;font-size:.9rem;
    border:1px solid var(--border)}
.popular a:hover{border-color:var(--orange);color:var(--orange)}
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
        "owner": (qp.get("owner") or "").strip()[:60],
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
    return (
        "<section class='panel searchbar'><form method='get' "
        "action='/search' class='controls' id='searchform'>"
        "<div class='field'><label for='s-name'>Deck name</label>"
        "<input id='s-name' type='search' name='name' "
        f"value='{_esc(query['name'])}' placeholder='Any part of the name'></div>"
        "<div class='field'><label for='s-cmd'>Commander</label>"
        f"<input id='s-cmd' type='text' name='commander' value='{_esc(query['commander'])}' "
        "placeholder='Card name, e.g. Atraxa, Praetors&#39; Voice' list='cardnames' autocomplete='off'>"
        "<datalist id='cardnames'></datalist></div>"
        "<div class='field'><label for='s-owner'>Owner</label>"
        "<input id='s-owner' type='text' name='owner' "
        f"value='{_esc(query['owner'])}' placeholder='Archidekt username'></div>"
        f"<div class='field'><label for='s-format'>Format</label><span class='sel'>{icon('decks')}"
        f"<select id='s-format' name='format'>{fmt_opts}</select></span></div>"
        f"<div class='field'><label for='s-order'>Sort by</label><span class='sel'>{icon('sort')}"
        f"<select id='s-order' name='order'>{order_opts}</select></span></div>"
        "<div class='field'><span class='lbl'>Colour "
        f"identity</span><div class='colours'>{colours}</div></div>"
        "<div class='field go'><button type='submit' "
        f"class='btn-primary'>{icon('search')} Search decks</button>"
        "<a class='btn' href='/search'>Clear</a></div>"
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
        about = f"About {count:,} decks" if count >= 1000 else f"{count} decks"
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
            try:
                found = await decks.search_decks(sub, **query)
            except DeckError as exc:
                body += problem(exc)
            else:
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
                f"<div class='popular'>{popular}</div></section>"
            )
        return page("Search decks", body, sub=sub, sid=sid)

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
            f"data-name='{_esc(shown)}' data-state='unknown'>{icon('follow')}"
            f"<span>Follow {_esc(shown)}</span></button>"
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

    @server.tool(
        name="search_decks",
        title="Search public Archidekt decks",
        description=(
            "Search Archidekt's public decks the way its deck search page does. Filters, all optional: "
            "`name` (part of the deck name), `commander` (a commander's card name), `owner` (Archidekt "
            "username), `format` (commander, modern, standard, pauper, pioneer, legacy, vintage, brawl, "
            "historic, oathbreaker), `colors` (letters from WUBRG: colour identity within those colours), "
            "`order_by` (-viewCount, -updatedAt, -createdAt, "
            "-size, edhBracket) and `page` (60 decks a page). "
            "Each result has an id and url; read one with get_deck. Read-only."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def search_decks(
        name: str | None = None,
        commander: str | None = None,
        owner: str | None = None,
        format: str | None = None,  # noqa: A002 - named for the user
        colors: str | None = None,
        order_by: str = "-viewCount",
        page: int = 1,
    ) -> dict[str, Any]:
        from .archidekt import FORMAT_IDS

        fmt = FORMAT_IDS.get((format or "").strip().lower()) if format else None
        if format and fmt is None and not str(format).isdigit():
            return {"ok": False, "error": "invalid", "message": f"unknown format '{format[:30]}'"}
        try:
            found = await decks.search_decks(
                _sub(),
                name=(name or "").strip(),
                commander=(commander or "").strip(),
                owner=(owner or "").strip(),
                deck_format=fmt
                if fmt is not None
                else (int(format) if format and str(format).isdigit() else None),
                colors=(colors or "").strip(),
                order_by=order_by if order_by in SEARCH_ORDERS else "-viewCount",
                page=max(1, min(int(page or 1), 17)),
            )
        except DeckError as exc:
            return {"ok": False, "error": exc.kind, "message": str(exc)}
        for d in found["decks"]:
            d["url"] = f"https://archidekt.com/decks/{d['id']}"
            d["gateway_url"] = f"{state.settings.public_url}/decks/{d['id']}"
        return {"ok": True, **found}

    @server.tool(
        name="archidekt_user",
        title="An Archidekt user's public decks",
        description=(
            "The public decks of one Archidekt user by `username` (newest first; `page` for more), like "
            "their profile page on the site. Read-only."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def archidekt_user(username: str, page: int = 1) -> dict[str, Any]:
        try:
            found = await decks.search_decks(
                _sub(),
                owner=(username or "").strip(),
                order_by="-updatedAt",
                page=max(1, min(int(page or 1), 17)),
            )
        except DeckError as exc:
            return {"ok": False, "error": exc.kind, "message": str(exc)}
        for d in found["decks"]:
            d["url"] = f"https://archidekt.com/decks/{d['id']}"
        return {"ok": True, "username": username, **found}
