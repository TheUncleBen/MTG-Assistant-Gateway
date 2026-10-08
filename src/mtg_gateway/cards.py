"""In-chat cards beyond Approve: the pictures and long lists an AI app can show next to a tool
result (MCP Apps), without any of it passing through the model.

Every card follows the same rules:

* The tool result's text and structured content stay what the assistant needs: a summary it can
  act on without the card. Pictures come from Scryfall into the card. A long list (a deck, the
  printings of a card) reaches the card through a **signed link** in the result's ``_meta``: a
  Fernet token naming the member, what to read and when it was issued. The card fetches it from
  ``/cards/data/<token>``; nothing about it is in the model's context. The link is read-only,
  bound to one member and one object, and expires after ``LINK_TTL`` seconds. Private decks are
  served only through a link issued to their owner.
* A card that picks something (a printing, which of the recognised cards to keep) writes
  nothing: the pick goes back to the assistant as text through ``ui/update-model-context``, and
  the assistant then proposes the change, which the member approves as always. The only card
  that changes anything is still the proposal card (approve.py), through its one-time code.
* Every card has a text fallback: the result's summary carries the same information, and a host
  that does not render MCP Apps (Claude Code, older clients) ignores the card metadata.

The cards: ``printings-card`` (card_printings: browse every printing's picture, tap to pick),
``picker-card`` (resolve_cards: confirm or drop the recognised cards, pick among ambiguous
names), ``deck-card`` (get_deck, get_snapshot: the deck by category with pictures
and rules text on tap) and ``account-card`` (account_status, whoami: one tap to the Account
page when Archidekt is not linked, writes are off or no approval mode is chosen).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import pydantic_core
from cryptography.fernet import Fernet, InvalidToken
from mcp.server.apps import Apps, ResourceCsp
from mcp_types import CallToolResult, TextContent
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .approve import CARD_IMAGE_HOSTS

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .archidekt import Deck

logger = logging.getLogger(__name__)

URI_BASE = "ui://mtg-assistant-gateway/"
PRINTINGS_CARD_URI = URI_BASE + "printings-card"
PICKER_CARD_URI = URI_BASE + "picker-card"
DECK_CARD_URI = URI_BASE + "deck-card"
ACCOUNT_CARD_URI = URI_BASE + "account-card"
CARD_META_KEY = "mtg/card"  # tool-result _meta key a card reads its link or data from
LINK_TTL = 600  # seconds a signed link stays valid
LINK_PATH = "/cards/data/"
SCRYFALL_API = "https://api.scryfall.com"  # the deck card reads rules text from Scryfall itself
STATIC = Path(__file__).parent / "static" / "cards"
LINK_KINDS = ("deck", "snapshot", "printings")


def tool_meta(uri: str) -> dict[str, Any]:
    """The ``_meta`` a tool carries to bind a card to its result (MCP Apps; the flat key is the
    spelling older hosts read)."""
    return {"ui": {"resourceUri": uri}, "ui/resourceUri": uri}


def card_html(name: str) -> str:
    """One card's HTML with the shared bridge (host protocol) and base styles inlined, so each
    ui:// resource is a single self-contained document."""
    html = (STATIC / f"{name}.html").read_text(encoding="utf-8")
    bridge = (STATIC / "_bridge.js").read_text(encoding="utf-8")
    base = (STATIC / "_base.css").read_text(encoding="utf-8")
    return html.replace("/*@BASE@*/", base).replace("/*@BRIDGE@*/", bridge)


def add_card_resources(apps: Apps, public_url: str) -> None:
    """Register the four cards on the MCP Apps extension. Each declares only the origins it
    needs: Scryfall for pictures, the gateway for its signed link, Scryfall's API for rules text."""
    gateway = _origin(public_url)
    apps.add_html_resource(
        PRINTINGS_CARD_URI,
        card_html("printings-card"),
        name="printings-card",
        title="Printings of a card",
        description="Every printing of one card with its picture; tap one to pick it.",
        csp=ResourceCsp(resource_domains=list(CARD_IMAGE_HOSTS), connect_domains=[gateway]),
        prefers_border=True,
    )
    apps.add_html_resource(
        PICKER_CARD_URI,
        card_html("picker-card"),
        name="picker-card",
        title="Recognised cards",
        description="The cards a list or photo resolved to; keep, drop or pick among the candidates.",
        csp=ResourceCsp(resource_domains=list(CARD_IMAGE_HOSTS)),
        prefers_border=True,
    )
    apps.add_html_resource(
        DECK_CARD_URI,
        card_html("deck-card"),
        name="deck-card",
        title="Deck",
        description="A deck by category with card pictures; tap a card for its rules text.",
        csp=ResourceCsp(resource_domains=list(CARD_IMAGE_HOSTS), connect_domains=[gateway, SCRYFALL_API]),
        prefers_border=True,
    )
    apps.add_html_resource(
        ACCOUNT_CARD_URI,
        card_html("account-card"),
        name="account-card",
        title="Account",
        description="What the gateway knows about the signed-in member, with a button to the Account page.",
        csp=ResourceCsp(),
        prefers_border=True,
    )


def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}"


class CardLinks:
    """Issues and opens the signed links cards fetch their data through."""

    def __init__(self, secret_key: str, public_url: str, *, ttl: int = LINK_TTL):
        self.fernet = Fernet(secret_key.encode())
        self.public_url = public_url.rstrip("/")
        self.ttl = ttl

    def issue(self, sub: str, kind: str, ref: str) -> str:
        """A URL only this member's card can use for ``ttl`` seconds to read one object."""
        if kind not in LINK_KINDS:
            raise ValueError(f"unknown link kind {kind!r}")
        payload = json.dumps(
            {"s": sub, "k": kind, "r": str(ref), "t": int(time.time())}, separators=(",", ":")
        )
        token = self.fernet.encrypt(payload.encode()).decode()
        return f"{self.public_url}{LINK_PATH}{token}"

    def open(self, token: str) -> dict[str, str] | None:
        """``{"sub", "kind", "ref"}`` for a valid, unexpired token; None otherwise."""
        if not token or len(token) > 1024:
            return None
        try:
            raw = self.fernet.decrypt(token.encode(), ttl=self.ttl)
            data = json.loads(raw)
        except (InvalidToken, ValueError, TypeError):
            return None
        if not isinstance(data, dict) or data.get("k") not in LINK_KINDS:
            return None
        sub, ref = data.get("s"), data.get("r")
        if not isinstance(sub, str) or not isinstance(ref, str) or not sub or not ref:
            return None
        return {"sub": sub, "kind": str(data["k"]), "ref": ref}


def with_card(data: dict[str, Any], card: dict[str, Any] | None) -> CallToolResult:
    """A tool result whose text and structured content are ``data`` (what the assistant reads)
    and whose ``_meta`` carries what the card needs, out of the model's context."""
    meta = {CARD_META_KEY: card} if card else None
    return CallToolResult(
        content=[TextContent(type="text", text=pydantic_core.to_json(data, fallback=str, indent=2).decode())],
        structured_content=data,
        _meta=meta,
    )


# -- the data a card fetches ---------------------------------------------------------------------


def deck_card_data(deck: Deck, *, public_url: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """The deck as the deck card draws it: rows grouped by category, with what a picture and a
    rules-text lookup need (the Scryfall id) and nothing else."""
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = [c["name"] for c in deck.categories if isinstance(c.get("name"), str)]
    for c in deck.cards:
        cat = c.categories[0] if c.categories else (c.default_category or "Uncategorised")
        groups.setdefault(cat, []).append(
            {
                "name": c.name,
                "qty": c.quantity,
                "set": c.set_code,
                "cn": c.collector_number,
                "finish": c.modifier if c.modifier and c.modifier.lower() != "normal" else "",
                "uid": c.scryfall_uid or "",
                "mana": c.mana_cost or "",
                "mv": c.cmc,
                "type": ", ".join(c.types) if c.types else "",
                "price": c.price,
                "in_deck": deck.in_deck(c),
                "commander": deck.is_commander(c) if hasattr(deck, "is_commander") else False,
            }
        )
    for name in groups:
        if name not in order:
            order.append(name)
    categories = [
        {
            "name": name,
            "count": sum(r["qty"] for r in groups[name]),
            "in_deck": any(r["in_deck"] for r in groups[name]),
            "cards": sorted(
                groups[name], key=lambda r: (r["mv"] if isinstance(r["mv"], (int, float)) else 99, r["name"])
            ),
        }
        for name in order
        if name in groups
    ]
    curve: dict[str, int] = {}
    for c in deck.main_cards:
        if "Land" in (c.types or []):
            continue
        mv = int(c.cmc) if isinstance(c.cmc, (int, float)) else 0
        key = "7+" if mv >= 7 else str(mv)
        curve[key] = curve.get(key, 0) + c.quantity
    out: dict[str, Any] = {
        "id": deck.id,
        "name": deck.name,
        "owner": deck.owner,
        "format": getattr(deck, "format", None),
        "url": f"https://archidekt.com/decks/{deck.id}",
        "gateway_url": f"{public_url}/decks/{deck.id}",
        "card_count": sum(c.quantity for c in deck.main_cards),
        "side_count": sum(c.quantity for c in deck.side_cards),
        "commanders": [c.name for c in deck.cards if "Commander" in c.categories],
        "colour_identity": sorted({col for c in deck.main_cards for col in (c.color_identity or [])}),
        "curve": curve,
        "categories": categories,
    }
    if snapshot:
        out["snapshot"] = snapshot
    return out


def add_card_routes(server: MCPServer, state: AppState, links: CardLinks) -> None:
    """``GET /cards/data/<token>``: the data behind a signed link, as JSON, for the card that holds
    the link. No cookie or bearer token is read; the token is the whole proof. Answered with an
    open CORS header because the card runs on the host's sandbox origin (``*.claudemcpcontent.com``,
    a WebView on the phone) and never with credentials."""
    s = state.settings
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Accept",
        "Access-Control-Max-Age": "600",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Vary": "Origin",
    }

    def fail(error: str, message: str, status: int) -> Response:
        return JSONResponse({"ok": False, "error": error, "message": message}, status, headers=headers)

    @server.custom_route(LINK_PATH + "{token}", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def card_data(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=headers)
        link = links.open(request.path_params.get("token", ""))
        if link is None:
            return fail("expired", "This card's link is no longer valid; ask for the data again.", 404)
        sub = link["sub"]
        user = state.db.get_user(sub)
        if user is None or user.get("disabled_at"):
            return fail("forbidden", "This member can no longer use the gateway.", 403)
        if state.membership is not None:
            from .membership import Membership

            if await state.membership.check(sub) is not Membership.ALLOWED:
                return fail("forbidden", "This member can no longer use the gateway.", 403)
        if state.metrics is not None:
            state.metrics.record("card", link["kind"], sub)
        from .decks import DeckError

        try:
            if link["kind"] == "deck":
                deck = await state.decks.get_any_deck(sub, link["ref"])
                data = deck_card_data(deck, public_url=s.public_url)
            elif link["kind"] == "snapshot":
                from .archidekt import parse_deck

                snap = state.decks.snapshot(sub, link["ref"])
                data = deck_card_data(
                    parse_deck(snap["deck"]),
                    public_url=s.public_url,
                    snapshot={"id": snap["id"], "taken_at": snap["taken_at"]},
                )
            else:  # printings
                scan = state.scan
                if scan is None:
                    return fail("unavailable", "Card lookups are not available on this gateway.", 503)
                try:
                    out = await scan.printings(link["ref"], owner=sub)
                except Exception as exc:  # ScanError: the kind is on the exception
                    kind = getattr(exc, "kind", "unavailable")
                    return fail(str(kind), str(exc), 503 if kind in ("unavailable", "rate_limited") else 404)
                data = {"oracle_id": link["ref"], **out}
        except DeckError as exc:
            return fail(exc.kind, str(exc), 404 if exc.kind in ("not_found", "forbidden") else 503)
        return JSONResponse({"ok": True, **data}, headers=headers)


# -- form questions (MCP elicitation) --------------------------------------------------------------


def client_elicits_forms(ctx: Any) -> bool:
    """Whether the connected client declared form-mode elicitation (Claude Code does)."""
    caps = getattr(ctx, "client_capabilities", None)
    elicitation = getattr(caps, "elicitation", None) if caps is not None else None
    if elicitation is None:
        return False
    # The capability object has a ``form`` member on the 2026 protocol; older clients that declare
    # elicitation at all declared the form kind implicitly.
    form = getattr(elicitation, "form", None)
    url = getattr(elicitation, "url", None)
    return form is not None or url is None


MAX_FORM_QUESTIONS = 8


async def ask_choices(ctx: Any, message: str, questions: list[dict[str, Any]]) -> dict[str, str] | None:
    """Ask the person up to ``MAX_FORM_QUESTIONS`` single-choice questions in one form, where the
    client supports it. ``questions`` items: ``{"key", "title", "options": [names]}``. Returns the
    answers by key (only the keys the person answered), or None when the client cannot ask, the
    person declined, or anything failed; callers then carry on as if nothing was asked."""
    if not questions or not client_elicits_forms(ctx):
        return None
    props: dict[str, Any] = {}
    for q in questions[:MAX_FORM_QUESTIONS]:
        options = [str(o)[:200] for o in q.get("options", []) if o][:20]
        if not options:
            continue
        props[str(q["key"])] = {
            "type": "string",
            "title": str(q.get("title", q["key"]))[:120],
            "enum": options,
        }
    if not props:
        return None
    schema = {"type": "object", "properties": props}
    try:
        answer = await ctx.session.elicit(message[:1000], schema, related_request_id=ctx.request_id)
    except Exception:  # noqa: BLE001 - a host that refuses or errors: the text path answers instead
        return None
    if getattr(answer, "action", None) != "accept":
        return None
    content = getattr(answer, "content", None) or {}
    if hasattr(content, "model_dump"):
        content = content.model_dump()
    if not isinstance(content, dict):
        return None
    out: dict[str, str] = {}
    for key, spec in props.items():
        value = content.get(key)
        if isinstance(value, str) and value in spec["enum"]:
            out[key] = value
    return out


__all__ = [
    "ACCOUNT_CARD_URI",
    "CARD_META_KEY",
    "DECK_CARD_URI",
    "LINK_PATH",
    "LINK_TTL",
    "PICKER_CARD_URI",
    "PRINTINGS_CARD_URI",
    "CardLinks",
    "add_card_resources",
    "add_card_routes",
    "ask_choices",
    "card_html",
    "client_elicits_forms",
    "deck_card_data",
    "tool_meta",
    "with_card",
]
