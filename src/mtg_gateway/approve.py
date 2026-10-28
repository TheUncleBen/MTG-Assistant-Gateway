"""In-chat Approve: the proposal card an AI app renders in the conversation (MCP Apps) and the
one-time approval code that lets only that card apply or reject a proposal.

How it fits together:

* Every propose_* tool (and amend, combine, apply) is bound to the ``ui://`` card below through the
  tool's ``_meta.ui.resourceUri`` (MCP Apps). A host that renders MCP Apps (Claude on web,
  desktop and phone; ChatGPT on the web and in its Android app) shows the card next to the tool
  result; a host that does not (Claude Code, older clients) ignores the metadata and shows the
  text, which still carries the review link.
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

from .archidekt import drops_formatting, words_only_note

CARD_URI = "ui://mtg-assistant-gateway/proposal-card"
CARD_PATH = Path(__file__).parent / "static" / "cards" / "proposal-card.html"
APPROVAL_META_KEY = "mtg/approval"  # tool-result _meta key the card reads the code from
APPROVAL_SET_META_KEY = "mtg/approval_set"  # "Apply all": {"ids": [...], "code": ...} (0.7.29)
APPROVALS_META_KEY = "mtg/approvals"  # the waiting-changes card: {proposal id: its code}
# Hosts that render the card load each change's small card image straight from Scryfall.
CARD_IMAGE_HOSTS = ["https://api.scryfall.com", "https://cards.scryfall.io"]
APPROVE_DECISIONS = ("approve", "reject")
# How long apply_proposal waits for the member to press Apply on the review page after a URL
# elicitation sent them there, and how often it looks.
BROWSER_WAIT_SECONDS = 45.0
BROWSER_POLL_SECONDS = 2.0
# A proposal result carries at most this many review rows (the card shows the first few and
# loads the rest on Show all), and its diff at most TEXT_DIFF_LINES lines, so a 100-card deck
# keeps the chat and the assistant's context light.
CARD_ROWS = 8
TEXT_DIFF_LINES = 40
MORE_TEXT = (
    "This result lists only the first changes. proposal_status with full true lists them all "
    "(no card); the user's card and review page show them all too."
)

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
# _meta for get_proposal, the card's own read of its proposal: the card's alone, like
# confirm_proposal (the model checks a proposal with proposal_status, which draws no card). Not
# bound to the card, so no host draws one for it.
CARD_READ_META: dict[str, Any] = dict(CONFIRM_TOOL_META)
# apply_proposal (the model's own apply, allowed by the member's approval mode): Claude Code
# shows its permission prompt on every call and offers no "always allow" for it.
APPLY_TOOL_META: dict[str, Any] = {"anthropic/requiresUserInteraction": True}


def card_approval(data: dict[str, Any], *, decks: Any, sub: str, in_chat: bool) -> tuple[str | None, bool]:
    """(code, other_app) for a waiting proposal's card: the one-time approval code when the
    member decides it in this chat, and whether it was made in another app (whose card alone can
    approve it, decks.check_approval; this card then sends the member to the review page). A
    deck deletion never has a code (its review page only); since 0.7.29 every other account
    action has one. A proposal the assistant may apply itself has no buttons to press."""
    from .decks import current_client

    if not (
        in_chat
        and data.get("state") == "pending"
        and data.get("proposal_id")
        and (data.get("ok") or data.get("error") == "browser_required")
        and not data.get("assistant_may_apply")
    ):
        return None, False
    row = decks.db.get_proposal(str(data["proposal_id"]), sub)
    if row is None or not decks.card_may_approve(row):
        return None, False
    creator = row.get("created_by_client")
    if not creator or creator != current_client.get():
        return None, True
    # the code of the version this result shows, so a card never carries a code for rows it does
    # not show (an amend between the read and here makes it one the gateway refuses, "changed")
    shown = data.get("version")
    return decks.approval_for(sub, row, version=int(shown) if shown else None), False


def proposal_tool_result(
    data: dict[str, Any],
    *,
    decks: Any,
    sub: str,
    in_chat: bool,
    drawn: bool = False,
    rows_limit: int | None = CARD_ROWS,
) -> Any:
    """A propose_* tool's result: the JSON as text and structured content, plus, for a pending
    proposal the member decides on, the in-chat card's one-time approval code in ``_meta`` (kept
    out of the text and structured content, so not in the model's context). The code is drawn
    only in the app that made the proposal (the only one whose press check_approval accepts); in
    another app the card says where to approve it instead (``card_other_app``).

    ``drawn``: the result draws a new card in the chat (a model's call of a card tool, not the
    card's own read), so the proposal's card number for this app goes up and the result carries
    it (``card_seq``); older cards that read a higher number step aside. ``rows_limit``: how many
    review rows the result carries (``rows_total`` says how many there are; the card loads the
    rest when asked); None for all of them."""
    from mcp_types import CallToolResult, TextContent

    if drawn and data.get("proposal_id") and (data.get("ok") or data.get("error") == "browser_required"):
        from .decks import current_client

        seq = decks.db.next_card_seq(str(data["proposal_id"]), sub, current_client.get())
        if seq:
            data = {**data, "card_seq": seq}
    approval, other_app = card_approval(data, decks=decks, sub=sub, in_chat=in_chat)
    if other_app:
        data = {**data, "card_other_app": True}
    meta: dict[str, Any] | None = {APPROVAL_META_KEY: approval} if approval else None
    if in_chat and data.get("state") == "pending":
        info, apply_all = deck_waiting(data, decks=decks, sub=sub)
        if info:
            data = {**data, "deck_waiting": info}
        if apply_all and approval:
            meta = {**(meta or {}), APPROVAL_SET_META_KEY: apply_all}
    data = bounded(with_words_note(data), rows_limit)
    return CallToolResult(
        content=[TextContent(type="text", text=proposal_text(data))],
        structured_content=data,
        _meta=meta,
    )


def rows_summary(rows: list[Any]) -> dict[str, int]:
    """What a proposal's rows add up to, for a card that shows only the first few: cards added,
    cards taken out, other changes, and rows in all."""
    adds = removes = others = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        qty = r.get("qty") if isinstance(r.get("qty"), int) else 0
        if r.get("kind") == "add":
            adds += qty
        elif r.get("kind") == "remove":
            removes += qty
        else:
            others += 1
    return {"adds": adds, "removes": removes, "others": others, "rows": len(rows)}


def bounded(data: dict[str, Any], rows_limit: int | None) -> dict[str, Any]:
    """A proposal result kept small however big the proposal is: at most ``rows_limit`` review
    rows (with ``rows_total`` and ``rows_summary``), TEXT_DIFF_LINES diff lines (with
    ``diff_lines_total``) and, for a new deck, as many cards of its stored list as rows
    (``cards_total``). proposal_status with full true, and the card's Show all, read the rest.
    None keeps everything (the card's own read of the whole list)."""
    if rows_limit is None:
        return data
    out = dict(data)
    rows = out.get("rows")
    if isinstance(rows, list) and len(rows) > rows_limit:
        out["rows"] = rows[:rows_limit]
        out["rows_total"] = len(rows)
        out["rows_summary"] = rows_summary(rows)
    diff = [ln for ln in str(out.get("diff") or "").splitlines()]
    if len(diff) > TEXT_DIFF_LINES:
        out["diff"] = "\n".join(diff[:TEXT_DIFF_LINES])
        out["diff_lines_total"] = len(diff)
    changes = out.get("changes")
    if isinstance(changes, list) and len(changes) > TEXT_DIFF_LINES:
        out["changes"] = changes[:TEXT_DIFF_LINES]
        out["changes_total"] = len(changes)
    elif isinstance(changes, dict) and isinstance(changes.get("cards"), list):
        cards = changes["cards"]
        if len(cards) > rows_limit:
            out["changes"] = {**changes, "cards": cards[:rows_limit], "cards_total": len(cards)}
    if any(k in out for k in ("rows_total", "diff_lines_total", "changes_total")) or (
        isinstance(out.get("changes"), dict) and "cards_total" in out["changes"]
    ):
        out["more"] = MORE_TEXT
    return out


# The comment actions whose text is written (actions.py).
_COMMENT_WRITES = ("comment_post", "comment_edit")


def stored_words_note(p: dict[str, Any]) -> str | None:
    """``words_only_note`` for a proposal whose STORED description or comment is a Quill document
    that its apply reduces to its words with formatting lost. Only a proposal stored before
    0.7.27 holds one (0.7.26 kept the text as given): since then the plain text is stored. None
    for a proposal that was rejected or expired, since nothing of it is saved."""
    if p.get("state") in ("rejected", "expired"):
        return None
    changes = p.get("changes") if isinstance(p.get("changes"), dict) else {}
    if p.get("kind") == "details" and drops_formatting(changes.get("description")):
        return words_only_note("description")
    if (
        p.get("kind") == "action"
        and changes.get("action") in _COMMENT_WRITES
        and drops_formatting(changes.get("text"))
    ):
        return words_only_note("comment")
    return None


def with_words_note(data: dict[str, Any], note: str | None = None) -> dict[str, Any]:
    """An assistant's proposal reply with ``note`` set when its description or comment lost
    links, formatting or embeds: ``note`` when the caller knows (the text it was given), else what
    the stored proposal shows (``stored_words_note``). A refusal (ok false) gets none."""
    note = note or stored_words_note(data)
    if note and data.get("ok") is not False:
        return {**data, "note": note}
    return data


# Left out of the text: the review rows and the stored edit, which the diff lines already say.
_TEXT_SKIP = ("rows", "changes", "diff", "card_seq")


def proposal_text(data: dict[str, Any]) -> str:
    """A proposal result as plain text, not JSON: what a chat app shows when it does not draw the
    card, and what an MCP Apps host gives the model (the structured content is for the card). A
    few sentences, the diff lines, then every other field as ``name: value`` on its own line,
    under the same names as the structured content, so the assistant reads all it did before."""
    import pydantic_core

    def one_line(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (dict, list)):
            return pydantic_core.to_json(v, fallback=str).decode()
        return " ".join(str(v).split())

    lines: list[str] = []
    skip = set(_TEXT_SKIP)
    if data.get("ok") is False:
        lines.append(
            "Not done: " + one_line(data.get("message") or data.get("error") or "the gateway refused.")
        )
        skip.add("message")
    elif data.get("proposal_id"):
        deck = one_line(data.get("deck_name") or data.get("deck_id"))
        head = f"Proposal {one_line(data['proposal_id'])} for {deck}: {one_line(data.get('state'))}"
        if data.get("risk"):
            head += f", {one_line(data['risk'])} risk"
            if data.get("risk_reason"):
                head += f" ({one_line(data['risk_reason'])})"
        if data.get("press_reason"):
            head += f"; it waits for the user's own press: it {one_line(data['press_reason'])}"
        lines.append(head + ".")
        if data.get("review_url"):
            lines.append("review_url: " + one_line(data["review_url"]))
            skip.add("review_url")
    diff = [ln for ln in str(data.get("diff") or "").splitlines() if ln.strip()]
    if diff:
        lines += ["", "Changes:", *("  " + ln for ln in diff)]
        total = data.get("diff_lines_total")
        if isinstance(total, int) and total > len(diff):
            lines.append(f"  … and {total - len(diff)} more (proposal_status with full true lists them all)")
    rest = [f"{k}: {one_line(v)}" for k, v in data.items() if k not in skip and v not in (None, "", [], {})]
    if rest:
        lines += ["", "Details:", *rest]
    return "\n".join(lines)


def approval_code(
    secret: str, *, proposal_id: str, sub: str, client_id: str, created_at: int, version: int = 1
) -> str:
    """The one-time code for one proposal: 192 bits of HMAC-SHA256 over the proposal, its owner,
    the app that made it, when, and (from its second version on) its version, keyed with
    the gateway's session secret. A card drawn for an earlier version no longer matches."""
    parts = ["mtg-approval", proposal_id, sub, client_id or "", str(int(created_at))]
    if int(version or 1) > 1:
        parts.append(f"v{int(version)}")
    msg = "\x00".join(parts).encode()
    digest = hmac.new(secret.encode(), msg, hashlib.sha256).digest()[:24]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def approval_set_code(secret: str, *, sub: str, client_id: str, items: list[tuple[str, int]]) -> str:
    """The one-time code for "Apply all" on a card (FLEX §6): 192 bits of HMAC-SHA256 over
    the member, the app and every proposal of the set with its version, so a card drawn before
    one of them was amended no longer matches. The set's proposals are each still applied (or
    not) on their own, as the member's one press (batch.py)."""
    parts = ["mtg-approval-set", sub, client_id or ""]
    parts += [f"{pid}:{int(v)}" for pid, v in sorted((str(p), int(v or 1)) for p, v in items)]
    msg = "\x00".join(parts).encode()
    digest = hmac.new(secret.encode(), msg, hashlib.sha256).digest()[:24]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def deck_waiting(data: dict[str, Any], *, decks: Any, sub: str) -> tuple[dict[str, Any] | None, Any]:
    """For a waiting proposal's card: the other changes waiting on
    its deck, as data the card and the assistant may both see ("2 earlier changes to this deck
    are waiting", "1 from ChatGPT also waits"), and the "Apply all" set for _meta: this app's
    waiting changes on the deck, this one included, when there are at least two."""
    from .decks import current_client

    pid, deck_id = data.get("proposal_id"), data.get("deck_id")
    if data.get("state") != "pending" or not pid or not str(deck_id or "").isdigit():
        return None, None
    client = current_client.get()
    rows = [r for r in decks.db.pending_board(sub, str(deck_id))]
    others = [r for r in rows if r["id"] != pid]
    if not others:
        return None, None
    mine = [r for r in rows if client and r.get("created_by_client") == client]
    apps: dict[str, int] = {}
    for r in others:
        if r.get("created_by_client") != client:
            label = decks._creator_label(r.get("created_by_client"))
            name = label[5:].rsplit(" (", 1)[0] if label.startswith("app: ") else "you"
            apps[name] = apps.get(name, 0) + 1
    info: dict[str, Any] = {
        "earlier": len(others),
        "same_app": len(mine),
        "other_apps": [{"app": a, "count": n} for a, n in sorted(apps.items())],
        "review_all_url": f"{decks.settings.public_url}/proposals#deck-{deck_id}",
        # the app asking, so the card names it ("2 more from Claude"): the count is per app
        **({"this_app": decks._creator_label(client)} if client else {}),
    }
    full = [decks.db.get_proposal(r["id"], sub) or {} for r in mine]
    # A change another app rewrote is approved on its own, never in "Apply all"
    items = [(r["id"], int(r.get("version") or 1)) for r in full if r and decks.in_apply_all(sub, r)]
    if len(items) < 2 or not client:
        return info, None
    # said only where an Apply all is offered, without the card's own change, by number
    # the card of such a change says so of itself
    out = [r for r in full if r and decks.card_may_approve(r) and not decks.in_apply_all(sub, r)]
    info["left_out"] = sum(1 for r in out if r["id"] != pid)
    info["left_out_nos"] = [r["board_no"] for r in out if r["id"] != pid and r.get("board_no")]
    if any(r["id"] == pid for r in out):
        info["this_left_out"] = True
    # what "Apply all" would apply, listed on its confirm before the press (a held
    # one says so; an amended one says who changed it and what)
    keys = (
        "proposal_id",
        "board_no",
        "summary",
        "status",
        "status_reason",
        "status_at",
        "version",
        "changed_by",
        "changed_by_apps",
        "what_changed",
    )
    in_set = {pid for pid, _v in items}
    info["set_lines"] = [
        {k: x[k] for k in keys if k in x}
        for x in decks.board_lines(sub, str(deck_id))
        if x["proposal_id"] in in_set
    ]
    code = approval_set_code(decks.settings.session_secret, sub=sub, client_id=client, items=items)
    return info, {"ids": [i for i, _v in items], "code": code}


def approval_matches(expected: str, given: Any) -> bool:
    """Constant-time check of a code the card sent back."""
    if not isinstance(given, str) or not given or len(given) > 64:
        return False
    return hmac.compare_digest(expected.encode(), given.encode())


def card_html() -> str:
    """The proposal card with the shared bridge and base styles inlined (0.7.29)."""
    from .cards import card_html as inline  # cards imports this module

    return inline("proposal-card")


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
            "whether they pressed Apply there, then call proposal_status; nothing was sent yet.",
            **p,
        }
    return {"ok": p["state"] == "applied", **p}
