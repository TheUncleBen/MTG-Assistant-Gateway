"""In-chat Approve: the proposal card an AI app renders in the conversation (MCP Apps) and the
one-time approval code that lets only that card apply or reject a proposal.

How it fits together:

* Every propose_* tool (and get_proposal) is bound to the ``ui://`` card below through the
  tool's ``_meta.ui.resourceUri`` (MCP Apps). A host that renders MCP Apps (Claude on web,
  desktop and phone; ChatGPT on the web) shows the card next to the tool result; a host that
  does not (Claude Code, older clients) ignores the metadata and shows the text, which still
  carries the review link.
* The card's buttons call ``confirm_proposal``, a tool whose ``_meta.ui.visibility`` is
  ``["app"]``: hosts offer it to the card, not to the model. It also requires the approval
  code, which travels in the tool result's ``_meta`` (delivered to the card, not part of
  the model's context) and is bound to the proposal, the member, the app that proposed and the
  proposal's creation time. The code is an HMAC of those with the gateway's session secret, so
  nothing is stored and nothing can be guessed; applying the proposal spends it, since a
  proposal is applied at most once.

What this guarantees, and what it does not: the gateway cannot tell a person's click from a
message the host sends on its own, so the protection rests on the host hiding the tool and the
``_meta`` from the model, as the MCP Apps specification says. In such a host, a prompt-injected
assistant cannot apply its own proposal: it never sees the code and the tool is not offered to
it. The browser review page (the member's signed-in session plus the click guard) remains the
path that needs no such trust, and is linked from every card.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.apps import Apps, ResourceCsp

CARD_URI = "ui://mtg-assistant-gateway/proposal-card"
CARD_PATH = Path(__file__).parent / "static" / "proposal-card.html"
APPROVAL_META_KEY = "mtg/approval"  # tool-result _meta key the card reads the code from
# Hosts that render the card load each change's small card image straight from Scryfall.
CARD_IMAGE_HOSTS = ["https://api.scryfall.com", "https://cards.scryfall.io"]
APPROVE_DECISIONS = ("approve", "reject")
# How long apply_proposal waits for the member to press Apply on the review page after a URL
# elicitation sent them there, and how often it looks.
BROWSER_WAIT_SECONDS = 45.0
BROWSER_POLL_SECONDS = 2.0

# _meta for the tools whose result the card renders. The flat ``ui/resourceUri`` key is the
# form older hosts read (the MCP Apps server helper writes both).
CARD_TOOL_META: dict[str, Any] = {"ui": {"resourceUri": CARD_URI}, "ui/resourceUri": CARD_URI}
# _meta for confirm_proposal: offered to the card only. The ``openai/*`` keys are ChatGPT's
# own spelling of the same two facts (not callable by the model, callable by the component).
CONFIRM_TOOL_META: dict[str, Any] = {
    "ui": {"visibility": ["app"]},
    "openai/visibility": "private",
    "openai/widgetAccessible": True,
}
# apply_proposal (the model's own apply, allowed by the member's approval mode): Claude Code
# shows its permission prompt on every call and offers no "always allow" for it.
APPLY_TOOL_META: dict[str, Any] = {"anthropic/requiresUserInteraction": True}


def proposal_tool_result(data: dict[str, Any], *, decks: Any, sub: str, in_chat: bool) -> Any:
    """A propose_* tool's result: the JSON as text and structured content, plus, for a pending
    proposal the member decides on, the in-chat card's one-time approval code in ``_meta`` (kept
    out of the text and structured content, so not in the model's context)."""
    import pydantic_core
    from mcp_types import CallToolResult, TextContent

    approval = None
    if (
        in_chat
        and data.get("ok")
        and data.get("state") == "pending"
        and data.get("proposal_id")
        and not data.get("assistant_may_apply")  # the assistant applies it: no buttons to press
    ):
        row = decks.db.get_proposal(str(data["proposal_id"]), sub)
        # R-142: an Archidekt account action is approved only on the review page, so its card gets
        # no code and shows "Approve on the review page" instead of Approve and Reject
        if row is not None and row.get("kind") != "action":
            approval = decks.approval_for(sub, row)
    meta = {APPROVAL_META_KEY: approval} if approval else None
    return CallToolResult(
        content=[TextContent(type="text", text=pydantic_core.to_json(data, fallback=str, indent=2).decode())],
        structured_content=data,
        _meta=meta,
    )


def approval_code(secret: str, *, proposal_id: str, sub: str, client_id: str, created_at: int) -> str:
    """The one-time code for one proposal: 192 bits of HMAC-SHA256 over the proposal, its owner,
    the app that made it and when, keyed with the gateway's session secret."""
    msg = "\x00".join(("mtg-approval", proposal_id, sub, client_id or "", str(int(created_at)))).encode()
    digest = hmac.new(secret.encode(), msg, hashlib.sha256).digest()[:24]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def approval_matches(expected: str, given: Any) -> bool:
    """Constant-time check of a code the card sent back."""
    if not isinstance(given, str) or not given or len(given) > 64:
        return False
    return hmac.compare_digest(expected.encode(), given.encode())


def card_html() -> str:
    return CARD_PATH.read_text(encoding="utf-8")


def apps_extension(public_url: str | None = None) -> Apps:
    """The MCP Apps extension carrying the proposal card resource and, with ``public_url``, the
    other in-chat cards (cards.py). Passing it to MCPServer advertises
    ``io.modelcontextprotocol/ui`` in the server's capabilities."""
    apps = Apps()
    apps.add_html_resource(
        CARD_URI,
        card_html(),
        name="proposal-card",
        title="Deck change proposal",
        description="Shows a proposal's exact change and lets the user approve or reject it.",
        csp=ResourceCsp(resource_domains=list(CARD_IMAGE_HOSTS)),
        prefers_border=True,
    )
    if public_url:
        from .cards import add_card_resources

        add_card_resources(apps, public_url)
    return apps


def client_elicits_urls(ctx: Any) -> bool:
    """Whether the connected client declared URL-mode elicitation (Claude Code does)."""
    caps = getattr(ctx, "client_capabilities", None)
    elicitation = getattr(caps, "elicitation", None) if caps is not None else None
    return elicitation is not None and getattr(elicitation, "url", None) is not None


async def apply_on_review_page(
    ctx: Any,
    describe: Callable[[], dict[str, Any]],
    *,
    wait_seconds: float = BROWSER_WAIT_SECONDS,
    poll_seconds: float = BROWSER_POLL_SECONDS,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> dict[str, Any] | None:
    """apply_proposal's path for a client with URL elicitation: ask the host to open the review
    page (the host shows the link and asks the person first), then wait a little for the
    proposal to leave ``pending`` through the member's own Apply or Reject there.

    Returns None when the client cannot do this (the caller answers ``browser_required`` as
    before), otherwise the tool result: the proposal as it stands, ``ok`` only once applied.
    """
    if not client_elicits_urls(ctx):
        return None
    p = describe()
    elicitation_id = secrets.token_urlsafe(9)
    try:
        answer = await ctx.elicit_url(
            "Open the gateway's review page to approve or reject this deck change. Nothing is sent "
            "to Archidekt until you press Apply there.",
            p["review_url"],
            elicitation_id,
        )
    except Exception:  # noqa: BLE001 - a host that refuses the request gets the plain text path
        return None
    if getattr(answer, "action", None) != "accept":
        return {
            "ok": False,
            "error": "browser_required",
            "message": "The user did not open the review page. Nothing was sent to Archidekt. They can "
            "open the review URL themselves and press Apply there.",
            "review_url": p["review_url"],
            "state": p["state"],
        }
    deadline = time.monotonic() + wait_seconds
    while p["state"] == "pending" and time.monotonic() < deadline:
        await sleep(poll_seconds)
        p = describe()
    session = getattr(ctx, "session", None)
    if session is not None and hasattr(session, "send_elicit_complete"):
        try:
            await session.send_elicit_complete(elicitation_id)
        except Exception:  # noqa: BLE001 - informational; the proposal's state is the truth
            pass
    if p["state"] == "pending":
        return {
            "ok": False,
            "error": "browser_pending",
            "message": "The review page was opened but the proposal is still pending. Ask the user "
            "whether they pressed Apply there, then call get_proposal; nothing was sent yet.",
            **p,
        }
    return {"ok": p["state"] == "applied", **p}
