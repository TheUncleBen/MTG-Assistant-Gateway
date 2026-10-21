"""Browsing Archidekt the way its site does: deck search and public profiles.

``/search`` is the gateway's counterpart of archidekt.com/search/decks: filter public decks by
name, commander, owner, format, colour identity and sort order (the search panel the card search
and My decks share, searchpanel.py), load more of them a page at a time and open any of them on
the gateway's own deck page. The name box also takes an Archidekt deck link or deck ID, which
opens that deck. ``/search/api/decks`` is one page of results as JSON for the page's "Load more
decks" (static/search.js). ``/users/{username}`` is a public profile: that person's
public decks. Both read Archidekt anonymously (only public decks come back) through the member's
Archidekt budget, and both need the gateway sign-in like every other page. The same search is
offered as the ``search_decks`` MCP tool (``owner`` for one user's public decks) and under ``/api/v1``.

Deck art on the result cards is shown from Scryfall by the card's id, which Archidekt's listing
names in its ``featured`` URL; no Archidekt image is loaded.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlencode

from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import prefs
from . import searchpanel as sp
from .archidekt import FORMAT_IDS, FORMAT_NAMES, SEARCH_ORDERS, format_label, list_row
from .busy import busy_response
from .deckpage import (
    WRITES_OFF_FOLLOW,
    WRITES_OFF_TITLE,
    avatar_html,
    covers_for,
    deck_card_html,
    deck_list_html,
)
from .decks import DeckError, _clean_deck_id
from .pages import _csrf, browser_session, login_redirect
from .theme import display_name, icon, render, stylesheet_link
from .theme import esc as _esc

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
    "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "manifest-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
COLOURS = (("W", "White"), ("U", "Blue"), ("B", "Black"), ("R", "Red"), ("G", "Green"), ("C", "Colorless"))
# how the colours match (searchpanel.DECK_COLOUR_MODES, the three My decks offers): "At most
# these" is Archidekt's colorIdentity search (decks within the colours); "Exactly these" asks
# Archidekt the same and the gateway keeps only the decks whose colour identity is exactly those
# colours; "Including these" (the default) searches without Archidekt's colour filter and keeps
# the decks whose colours include every picked colour, from each page's colour pips (Archidekt has
# no search for either).
COLOUR_MODES = sp.DECK_COLOUR_MODES
# the sort direction: Automatic is Archidekt's own way for the order (newest first, the biggest
# first, the lowest bracket first); the other way sends the order without its leading "-" (the
# ordering parameter's standard form, so far not tried live with this gateway)
DIRECTIONS = sp.DIRECTIONS
MAX_PAGES = 17  # Archidekt caps a search at 1000 rows, 60 a page
LIMIT_NOTE = "Archidekt lists at most 1,000 decks for one search. Narrow the search to see other decks."
VIEWS = ("grid", "list")
FORMAT_OPTIONS = sorted(
    ((str(i), format_label(FORMAT_NAMES[i])) for i in sorted(FORMAT_NAMES)), key=lambda kv: kv[1].lower()
)
BROWSE_CSS = """
/* the deck search: the shared panel (theme.SEARCH_PANEL_CSS); its Commander and Owner boxes */
.spanel .fgrid > .fg.cmd{--gw:220px}
.spanel .fgrid > .fg.who{--gw:160px}
.results-head{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;
  margin:0 0 .75rem}
.results-head .muted{margin:0}
.results-head h2,.results-head .muted{min-width:0;overflow-wrap:anywhere}
.pager{display:flex;justify-content:center;align-items:center;gap:.5rem;margin:1rem 0}
.pager .btn{margin:0}
/* Avatar, name and the social row: the name block takes the room and wraps a long unbroken name;
   the social row keeps its own size (Follow ellipsises inside it) and drops under the name when the
   two no longer fit side by side, so neither squeezes the other (a 64-character name). */
.profile{display:flex;flex-wrap:wrap;align-items:center;gap:1rem;margin:0 0 1rem;min-width:0}
.profile .who .uname{overflow-wrap:anywhere}
.profile .social-off{flex-basis:100%;margin:0}
.profile .who{display:flex;flex-direction:column;flex:1 1 16rem;min-width:0}
.profile .who .uname{font-size:1.5rem;font-weight:900}
.profile .who .sub{color:var(--text-muted)}
.profile .social{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-left:auto;flex:0 0 auto;
  max-width:100%}
.profile .social .btn,.profile .social .soc{margin:0;display:inline-flex;align-items:center;gap:.4rem}
.profile .social .soc[hidden]{display:none}
.profile .social .soc.on{background:var(--orange);border-color:var(--orange);color:var(--on-orange)}
.profile .social .soc.on:hover{color:var(--on-orange)}
/* The follow question grows with its text: a long name wraps onto more lines and the Follow and
   No buttons stay inside the chip instead of dropping under the link. */
.profile .social .confirm{display:inline-flex;align-items:center;flex-wrap:wrap;gap:.4rem;
  background:var(--surface-2);border-radius:17px;padding:.25rem .35rem .25rem .85rem;min-height:34px;
  max-width:100%;box-sizing:border-box;font-size:.9rem;font-weight:700}
.profile .social .confirm button{margin:0;height:26px;padding:0 .7rem;border-radius:13px;font-size:.85rem}
/* focus moves to Follow by script after a tap: always ring it, as the deck page's confirms do */
.profile .social .confirm button:focus{outline:2px solid var(--focus);outline-offset:2px}
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
.listbar .controls .grow{flex:1 1 min(100%,16rem);min-width:0}
"""


def deck_ref(text: str) -> str | None:
    """The deck ID when ``text`` is an Archidekt deck link or a deck ID, else None."""
    if not text or not (text.isdigit() or "archidekt.com" in text.lower()):
        return None
    try:
        return _clean_deck_id(text)
    except DeckError:
        return None


def matching_colours(rows: list[dict[str, Any]], colors: str, mode: str) -> list[dict[str, Any]]:
    """The rows whose colour identity matches ``colors`` the way ``mode`` says (searchpanel.
    colour_match: exactly those, or including every one of them; C alone: colourless decks)."""
    return [r for r in rows if sp.colour_match(r.get("colors"), colors, mode)]


def archidekt_order(order_by: str, direction: str) -> str:
    """Archidekt's ``orderBy`` for the order and the direction: its own way unchanged
    (``auto``), else the order with ("desc") or without ("asc") its leading ``-``."""
    base = order_by.lstrip("-")
    if direction == "desc":
        return "-" + base
    if direction == "asc":
        return base
    return order_by


def read_query(qp: Any) -> dict[str, Any]:
    """The search form's fields, cleaned: unknown values fall back to the defaults."""
    fmt = (qp.get("format") or "").strip()
    picked = [v.upper() for v in (qp.getlist("c") if hasattr(qp, "getlist") else [])]
    colors = "".join(
        c
        for c, _n in COLOURS
        if c in picked or qp.get(f"c{c}") == "1" or c in (qp.get("colors") or "").upper()
    )
    try:
        page = max(1, min(int(qp.get("page") or 1), MAX_PAGES))
    except ValueError:
        page = 1
    return {
        "name": (qp.get("name") or qp.get("q") or "").strip()[:120],
        "commander": (qp.get("commander") or "").strip()[:120],
        "owner": (qp.get("owner") or "").strip()[:120],  # a profile's username comes through here too
        "deck_format": int(fmt) if fmt.isdigit() and int(fmt) in FORMAT_NAMES else None,
        "colors": colors,
        "colour_mode": qp.get("cm") if qp.get("cm") in dict(COLOUR_MODES) and colors else "incl",
        "order_by": qp.get("order") if qp.get("order") in SEARCH_ORDERS else "-viewCount",
        "direction": qp.get("dir") if qp.get("dir") in dict(DIRECTIONS) else "auto",
        "page": page,
    }


def archidekt_query(query: dict[str, Any]) -> dict[str, Any]:
    """The part of a cleaned query ArchidektClient.search_decks takes: "Including these" colours
    are not Archidekt's filter (the gateway keeps the matching decks itself), and the direction
    goes into the order."""
    out = {k: query[k] for k in ("name", "commander", "owner", "deck_format", "colors", "page")}
    if query.get("colour_mode") == "incl":
        out["colors"] = ""
    out["order_by"] = archidekt_order(query["order_by"], query.get("direction", "auto"))
    return out


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
    if q.get("colors"):
        params["c"] = list(q["colors"])
        if q.get("colour_mode", "incl") != "incl":
            params["cm"] = q["colour_mode"]
    if q.get("order_by") and q["order_by"] != "-viewCount":
        params["order"] = q["order_by"]
    if q.get("direction", "auto") != "auto":
        params["dir"] = q["direction"]
    if q.get("page", 1) > 1:
        params["page"] = q["page"]
    return "/search" + ("?" + urlencode(params, doseq=True) if params else "")


def count_text(found: dict[str, Any] | None, query: dict[str, Any], *, shown: int = 0) -> str:
    """The results bar's count: "About 1,000 decks" at Archidekt's cap, "N decks" otherwise; with
    the colour modes the gateway filters itself ("Exactly these", "Including these") the decks
    kept so far, since Archidekt counts the wider search."""
    if found is None:
        return "Decks"
    if query.get("colors") and query.get("colour_mode") != "atmost":
        return f"{shown} deck{'' if shown == 1 else 's'}"
    count = found.get("count")
    if not isinstance(count, int):
        return "Decks"
    return f"About {count:,} decks" if count >= 1000 else f"{count} deck{'' if count == 1 else 's'}"


def search_form_html(
    query: dict[str, Any], *, view: str = "grid", found: dict[str, Any] | None = None, shown: int = 0
) -> str:
    """The search panel (searchpanel.py): the name box (a deck name, or an Archidekt deck link or
    ID, which shows "Open this deck" under it), Colors with its match drop-down, Format, Commander
    and Owner, and the results bar: the count, Sort by with its direction, Grid or List, the card
    size and Clear filters."""
    # the card search for the same text: a commander's name is a card name, a deck name may be
    cards_q = query["commander"] or query["name"]
    cards_link = "/cards" + (f"?{urlencode({'q': cards_q})}" if cards_q else "")
    ref = deck_ref(query["name"])
    under = sp.open_hint_html(ref, f"/decks/open?{urlencode({'ref': ref})}") if ref else ""
    groups = sp.filter_grid(
        [
            sp.colours_group(
                label="Colors",
                name="c",
                chosen=query["colors"],
                mode_name="cm",
                mode_options=COLOUR_MODES,
                mode_value=query["colour_mode"],
            ),
            sp.select_group(
                label="Format",
                name="format",
                options=[("", "Any format"), *FORMAT_OPTIONS],
                value="" if query["deck_format"] is None else str(query["deck_format"]),
                input_id="s-format",
            ),
            sp.text_group(
                label="Commander",
                name="commander",
                value=query["commander"],
                input_id="s-cmd",
                placeholder="Card name, e.g. Atraxa, Praetors' Voice",
                attrs=" data-suggest='cards' maxlength='120'",
                cls="cmd",
            ),
            sp.text_group(
                label="Owner",
                name="owner",
                value=query["owner"],
                input_id="s-owner",
                placeholder="Archidekt username",
                attrs=" maxlength='120'",
                cls="who",
            ),
        ]
    )
    bar = sp.results_bar(
        count_html=sp.count_html(count_text(found, query, shown=shown), count_id="s-count"),
        sort_html=sp.sort_group(
            order_name="order",
            orders=SEARCH_ORDERS.items(),
            order=query["order_by"],
            order_id="s-order",
            dir_name="dir",
            direction=query["direction"],
        ),
        view_html=sp.view_group(view),
        clear_html="<a class='btn sp-clear clr' href='/search'>Clear filters</a>",
        cls="pub",
    )
    return (
        "<section class='panel spanel'><form method='get' action='/search' id='searchform' "
        "data-apply='submit'>"
        + sp.name_row(
            label="Deck name, or an Archidekt link or deck ID",
            name="name",
            value=query["name"],
            placeholder="Deck name, link or ID",
            input_id="s-name",
            search_label="Search decks",
            cross_html=sp.cross_link("Search cards instead", cards_link, "image"),
            under_html=under,
            attrs=" maxlength='120'",
        )
        + sp.filters_sheet(
            groups,
            show_html="<button type='submit' class='btn-primary sp-show'>Show results</button>",
            clear_html="<a class='btn sp-clear' href='/search'>Clear filters</a>",
        )
        + bar
        + "</form></section>"
    )


def deck_items_html(rows: list[dict[str, Any]]) -> str:
    """The tiles of one page of results, without the list around them (Load more appends them)."""
    covers = covers_for(rows)
    return "".join(
        deck_card_html(d, cover=covers.get(str(d["id"])), selected=False, show_owner=True) for d in rows
    )


def search_results_html(found: dict[str, Any], query: dict[str, Any], *, view: str) -> str:
    """The first page of a search, then "Load more decks" (static/search.js appends the next
    page), which says so when Archidekt's cap is reached."""
    rows = found["decks"]
    if not rows:
        return (
            "<section class='panel'><p class='muted'>No public decks match. Try fewer filters.</p></section>"
        )
    more = bool(found.get("has_more"))
    at_cap = more and query["page"] >= MAX_PAGES
    # the search these results are of, for Load more (the form may change without being sent)
    shown_qs = search_link(query, page=1).partition("?")[2]
    return (
        deck_list_html(rows, covers=covers_for(rows), view=view, show_owner=True)
        + f"<div class='loadmore' id='s-more' data-page='{query['page']}' data-max='{MAX_PAGES}' "
        f"data-qs='{_esc(shown_qs)}'"
        + ("" if more else " hidden")
        + f"><button type='button' class='btn' data-more{' hidden' if at_cap else ''}>Load more decks"
        "</button>"
        f"<p class='muted'{'' if at_cap else ' hidden'}>{_esc(LIMIT_NOTE)}</p></div>"
    )


def results_html(found: dict[str, Any], query: dict[str, Any], *, heading: str) -> str:
    """A profile's decks: the heading with the count, the tiles and the page links."""
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
    if found.get("has_more") and query["page"] < MAX_PAGES:
        pager += f"<a class='btn' href='{_esc(search_link(query, page=query['page'] + 1))}'>Next</a>"
    pager += "</nav>"
    return head + body + pager


def add_browse_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings
    decks = state.decks

    def page(
        title: str,
        body: str,
        *,
        sub: str,
        sid: str | None,
        current: str = "/search",
        status: int = 200,
        social: bool = False,
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
            user=display_name(user),
            wide=True,
            current=current,
            head_extra=f"{stylesheet_link('deck.css')}<style>{BROWSE_CSS}</style>"
            "<script src='/static/deck.js' defer></script>"
            # the search panel's drop-downs and phone sheet, then Load more and Open this deck
            "<script src='/static/searchpanel.js' defer></script>"
            "<script src='/static/search.js' defer></script>"
            # the user page's Follow button (static/social.js, the social actions' own file)
            + ("<script src='/static/social.js' defer></script>" if social else ""),
        )
        resp.headers["Content-Security-Policy"] = SEARCH_CSP
        return resp

    def problem(exc: DeckError) -> str:
        text = {
            "rate_limited": str(exc),
            "unavailable": "Archidekt is not answering right now. Try again later.",
        }.get(exc.kind, "The search could not be run: " + str(exc))
        return f"<p class='notice error'>{_esc(text)}</p>"

    async def run_search(sub: str, query: dict[str, Any]) -> dict[str, Any]:
        """One page from Archidekt; with "Exactly these" or "Including these" colours only the
        decks whose colours match (Archidekt's own filter is "At most these")."""
        found = await decks.search_decks(sub, **archidekt_query(query))
        if query["colors"] and query["colour_mode"] != "atmost":
            found = {
                **found,
                "decks": matching_colours(found["decks"], query["colors"], query["colour_mode"]),
            }
        return found

    def view_of(request: Request, sub: str) -> tuple[str, bool]:
        return prefs.view_choice(state.db, sub, request, "search", VIEWS, "grid")

    def remembered(resp: Response, sub: str, view: str, remember: bool) -> Response:
        if remember:
            return prefs.remember_view(
                state.db, sub, resp, "search", view, secure=s.public_url.startswith("https://")
            )
        return resp

    @server.custom_route("/search", methods=["GET"], include_in_schema=False)
    async def search_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/search" + (f"?{request.url.query}" if request.url.query else ""))
        query = read_query(request.query_params)
        view, remember = view_of(request, sub)
        asked = bool(
            query["name"]
            or query["commander"]
            or query["owner"]
            or query["deck_format"] is not None
            or query["colors"]
        )
        # a deck link in the name box opens that deck ("Open this deck" under the box): no search
        if deck_ref(query["name"]) and not query["name"].isdigit():
            asked = False
        body = ""
        if asked:
            note = ""
            try:
                found = await run_search(sub, query)
                if query["commander"] and not found["decks"]:
                    names = await commander_names(state, query["commander"])
                    if not any(n.casefold() == query["commander"].casefold() for n in names):
                        if len(names) == 1:
                            query = {**query, "commander": names[0]}
                            found = await run_search(sub, query)
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
                body += search_form_html(query, view=view) + problem(exc)
            else:
                body += (
                    search_form_html(query, view=view, found=found, shown=len(found["decks"]))
                    + note
                    + search_results_html(found, query, view=view)
                )
        else:
            body += search_form_html(query, view=view)
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
                "colors, sorted the way the site sorts them. Open "
                "a deck to see it on the gateway, with the same "
                "views, stats and export as your own; clone it to your account from its page.</p>"
                f"<div class='popular'>{popular}"
                f"<a class='precons' href='/precons'>{icon('box')} Preconstructed decks by set</a></div>"
                "</section>"
            )
        return remembered(page("Search decks", body, sub=sub, sid=sid), sub, view, remember)

    @server.custom_route("/search/api/decks", methods=["GET"], include_in_schema=False)
    async def search_api(request: Request) -> Response:
        """One page of the search in the address, for "Load more decks": ``{ok, html, count,
        page, has_more, at_cap}``; ``html`` is the tiles only, which the page appends."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "sign in first"}, status_code=401)
        if not prefs.from_this_site(request):  # it asks Archidekt on the member's own allowance
            return JSONResponse(
                {"ok": False, "error": "cross_site"}, status_code=403, headers={"Cache-Control": "no-store"}
            )
        query = read_query(request.query_params)
        try:
            found = await run_search(sub, query)
        except DeckError as exc:
            return JSONResponse(
                {"ok": False, "error": problem(exc)}, status_code=503 if exc.kind != "invalid" else 400
            )
        more = bool(found.get("has_more"))
        return JSONResponse(
            {
                "ok": True,
                "html": deck_items_html(found["decks"]),
                "count": found.get("count"),
                "shown": len(found["decks"]),
                "page": query["page"],
                "has_more": more and query["page"] < MAX_PAGES,
                "at_cap": more and query["page"] >= MAX_PAGES,
            },
            headers={"Cache-Control": "no-store"},
        )

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
            f"<button type='submit' aria-label='Filter' title='Filter'>{icon('search')}</button></span></div>"
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
            found = await decks.search_decks(sub, **archidekt_query(query))
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
        writes = bool(state.settings.writes_enabled)
        follow = (
            f"<button type='button' class='btn soc' data-social='follow' data-user='{_esc(owner_id)}' "
            f"data-name='{_esc(shown)}' data-state='unknown' "
            + (
                f"title='Follow {_esc(shown)} on Archidekt'>"
                if writes  # writes switched off: shown, disabled, and the reason is on the page
                else f"disabled title='{WRITES_OFF_TITLE}' aria-describedby='social-off'>"
            )
            + f"{icon('follow')}<span>Follow {_esc(shown)}</span></button>"
            if owner_id and str(link.get("archidekt_user_id") or "") != str(owner_id)
            else ""
        )
        follow_off = (
            f"<p class='social-off muted small' id='social-off'>{WRITES_OFF_FOLLOW}</p>"
            if follow and not writes
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
            f"{icon('external')} Archidekt profile</a></div>{follow_off}</section>"
            f"<form method='get' class='panel listbar' id='listform'><div class='controls'>"
            f"<div class='field'><label for='f-order'>Sort by</label><span class='sel'>{icon('sort')}"
            f"<select id='f-order' name='order'>{order_opts}</select></span></div>"
            "<noscript><button type='submit' class='apply'>Apply</button></noscript></div></form>"
        )
        return page(shown, head + results_html(found, query, heading="Decks"), sub=sub, sid=sid, social=True)


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
            "`colors` (letters from WUBRG: color identity within those colors), "
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
        colors: Annotated[str | None, Field(description="WUBRG letters: color identity within them.")] = None,
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
