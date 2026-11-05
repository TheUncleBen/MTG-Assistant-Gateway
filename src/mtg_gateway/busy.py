"""One answer for every browser page when Archidekt cannot be used right now.

A page that reads from Archidekt catches a DeckError (or a CollectionError, which copies its
``kind``). Two kinds are not about the deck or the member at all: ``rate_limited`` (the member's
Archidekt budget, the Settings row ``archidekt_calls_per_10_min``, or the concurrency cap is used up,
Archidekt asked the gateway to slow down, or other work for the member is still running) and
``unavailable`` (Archidekt or Mystic Forge cannot be reached). Every page maps those two through
:func:`busy_response` to the same page, status and headers, so no page can call a used-up budget
"Deck not found" again. The other kinds stay with the page's own wording.
"""

from __future__ import annotations

import html
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# One sentence for each state of a service the gateway asks (Archidekt, Scryfall, Forge, the research
# service), the same in the tools' answers, the site's pages and notices, the home panel and the
# cards (0.7.46 sweep B-1). The first three say what went wrong with one request; "unavailable" and
# the two pauses say what holds right now. AGAIN is the one advice to add where a sentence ends an
# answer.
OUTAGE = {
    "timeout": "{s} didn't answer in time.",
    "unreachable": "{s} couldn't be reached.",
    "failing": "{s} had a problem answering.",
    "unavailable": "{s} can't be reached right now.",
    "slow_down": "{s} asked the gateway to slow down, so its requests are paused for now.",
    "paused": "{s} failed several times in a row, so its requests are paused for now.",
    # an apply that stopped before it changed anything (decks.TRY_AGAIN_KINDS), whatever the cause
    "stopped": "{s} didn't answer, so nothing was changed.",
}
AGAIN = "Try again later."


def outage(state: str, service: str = "Archidekt", *, again: bool = False) -> str:
    """The sentence for ``state`` (a key of OUTAGE) of ``service``, with AGAIN after it if asked."""
    words = OUTAGE[state].format(s=service)
    return f"{words} {AGAIN}" if again else words


FINE = "Nothing is wrong with your decks or your account."
# FINE with the advice joined on, for a text under a page or box that has its own Try again button
FINE_THEN = "Nothing is wrong with your decks or your account; try again "
BUSY_TITLE = "Archidekt is busy"
BUSY_TEXT = (
    "Other work for your account is still running, or one of your account's limits was reached, so "
    f"this couldn't be done right now. {FINE_THEN}a little later."
)
# The busy text when the refusal says why (DeckError.busy_cause, or Archidekt's own 429 behind it).
BUSY_TEXTS = {
    "member_slots": (
        "Your account already has several Archidekt actions running or waiting, for example from an "
        f"assistant, and there was no turn free for this one. {FINE_THEN}once some of them have "
        "finished."
    ),
    "budget": (
        "Your account has used up its Archidekt allowance for the last 10 minutes, for example "
        "because an assistant has been busy. It refills a little at a time, and this works again "
        f"once there is room. {FINE}"
    ),
    "archidekt": f"{outage('slow_down')} {FINE_THEN}later.",
}
# When the member's own allowance or turns ran out (not Archidekt's own pause), the title says so.
TOO_MANY_TITLE = "Too many requests right now"
MEMBER_CAUSES = frozenset({"member_slots", "budget"})
UNREACHABLE_TITLE = outage("unavailable").removesuffix(".")
UNREACHABLE_TEXT = (
    "The gateway couldn't get an answer from Archidekt (or the research service). This usually "
    "clears by itself."
)
# The wait to advise when the refusal does not say (a concurrency cap, Archidekt's own 429, an
# unreachable Archidekt): the pacer's breaker and Archidekt's typical Retry-After are about this long.
DEFAULT_RETRY_AFTER = 60
_STATUS = {"rate_limited": 429, "unavailable": 503}
_TITLE = {"rate_limited": BUSY_TITLE, "unavailable": UNREACHABLE_TITLE}
_TEXT = {"rate_limited": BUSY_TEXT, "unavailable": UNREACHABLE_TEXT}


@dataclass(frozen=True)
class BusyPlan:
    kind: str
    title: str
    text: str
    status: int
    retry_after: int
    detail: str  # the refusal's own words (safe to show: DeckError's contract)


def busy_cause(exc: BaseException) -> str | None:
    """Why a rate_limited refusal was made: a cause the refusal names (``busy_cause``), or
    "archidekt" when Archidekt's own 429 (or its pause) is behind it; None when it doesn't say."""
    seen: BaseException | None = exc
    for _ in range(5):  # a DeckError, a CollectionError made from one, and the error behind them
        if seen is None:
            break
        cause = getattr(seen, "busy_cause", None)
        if cause in BUSY_TEXTS:
            return str(cause)
        if type(seen).__name__ == "ArchidektError" and getattr(seen, "kind", None) == "rate_limited":
            return "archidekt"
        seen = seen.__cause__ or seen.__context__
    return None


def busy_plan(exc: BaseException) -> BusyPlan | None:
    """How to answer for ``exc``, or None when it is not an Archidekt-busy or -unreachable
    failure and the page should keep its own answer."""
    kind = getattr(exc, "kind", None)
    if kind not in _STATUS:
        return None
    retry = getattr(exc, "retry_after", None)
    if not isinstance(retry, int) or isinstance(retry, bool) or retry < 1:
        retry = DEFAULT_RETRY_AFTER
    text, title = _TEXT[kind], _TITLE[kind]
    if kind == "rate_limited":
        cause = busy_cause(exc) or ""
        text = BUSY_TEXTS.get(cause, text)
        if cause in MEMBER_CAUSES:
            title = TOO_MANY_TITLE
    return BusyPlan(kind, title, text, _STATUS[kind], min(retry, 3600), str(exc))


def busy_headers(plan: BusyPlan) -> dict[str, str]:
    return {"Retry-After": str(plan.retry_after), "Cache-Control": "no-store"}


_headers = busy_headers  # the older name (companion_tools.py)


def same_url(request: Request) -> str:
    """This request's path and query, for the Try again link."""
    return request.url.path + (f"?{request.url.query}" if request.url.query else "")


# The area a page belongs to, by its address: the noun the busy text names ("Nothing is wrong
# with your collection") and the second button. A page outside these names no area of its own.
AREAS = (
    ("/collection", "collection", "/collection", "Binder"),
    ("/watchlist", "watchlist", "/collection", "Binder"),
    ("/lists", "lists", "/collection/lists", "Lists"),
    ("/decks", "decks", "/decks", "My decks"),
    ("/folders", "decks", "/decks", "My decks"),
    ("/compare", "decks", "/decks", "My decks"),
)


def area_of(url: str) -> tuple[str | None, str, str]:
    """(noun, back address, back label) for a page's address."""
    path = url.split("?", 1)[0]
    for prefix, noun, href, label in AREAS:
        if path == prefix or path.startswith(prefix + "/"):
            return noun, href, label
    return None, "/", "Home"


def area_text(text: str, noun: str | None) -> str:
    """The busy text for an area: "your decks" names the page's own area, or none."""
    if noun == "decks":
        return text
    return text.replace(
        "your decks or your account", f"your {noun} or your account" if noun else "your account"
    )


def busy_body(plan: BusyPlan, url: str, what: str = "this page") -> str:
    """What a page shows in its own frame while Archidekt can't be asked and there is no held
    copy (0.7.46 re-audit D29): the strip the held pages show (busy.held_html), with Try again
    (a fresh read of ``url``), then why, and the area's own page. ``what`` names the page ("your
    binder"). Archidekt's own words (``plan.detail``: codes, seconds) are left out: the text says
    what happened and that nothing is wrong."""
    noun, back, label = area_of(url)
    second = (
        f" <a class='btn' href='{html.escape(back)}'>{html.escape(label)}</a>"
        if back != url.split("?", 1)[0]
        else ""
    )
    return (
        f"<p class='notice warn held' role='status'>{html.escape(plan.title.rstrip('.'))}, so "
        f"{html.escape(what)} can't be read yet. "
        f"<a class='btn' href='{html.escape(url)}'>Try again</a></p>"
        f"<p class='busy-why'>{html.escape(area_text(plan.text, noun))}{second}</p>"
    )


def busy_response(
    exc: BaseException,
    request: Request,
    page: Callable[..., Response],
    *,
    sub: str,
    sid: str | None,
    title: str | None = None,
    head: str = "",
    **page_kw: Any,
) -> Response | None:
    """The shared answer for a busy or unreachable Archidekt, rendered with the caller's own
    ``page(title, body, sub=, sid=, status=)``: the page keeps its own ``title`` (and ``head``,
    the page's heading block, with ``heading=False`` in ``page_kw``) over the busy strip; without
    a title the strip's words are the title. None when ``exc`` is some other failure."""
    plan = busy_plan(exc)
    if plan is None:
        return None
    body = head + busy_body(plan, same_url(request))
    resp = page(title or plan.title, body, sub=sub, sid=sid, status=plan.status, **page_kw)
    resp.headers.update(_headers(plan))
    return resp


def age_words(read_at: float, now: float | None = None) -> str:
    """How long ago ``read_at`` (wall time) was, in minutes or hours ("3 minutes", "1 hour")."""
    seconds = max(0, int((time.time() if now is None else now) - read_at))
    n, unit = (max(1, seconds // 60), "minute") if seconds < 3600 else (seconds // 3600, "hour")
    return f"{n} {unit}{'' if n == 1 else 's'}"


def held_text(
    exc: BaseException,
    read_at: float,
    what: str,
    *,
    now: float | None = None,
    amended: bool = False,
    added: bool = False,
) -> str:
    """What a page says over a copy shown as last read because Archidekt can't be asked
    (decks.ShortReads.get_held); ``what`` names it: "your binder", "this list". ``amended``: rows
    of it the member changed through the gateway since show what was saved (ShortReads.amend_last).
    ``added``: cards the member added through the gateway since are shown first (the Binder)."""
    plan = busy_plan(exc)
    why = plan.title if plan else "Archidekt can't be asked"
    text = f"{why.rstrip('.')}, so this is {what} as it was read {age_words(read_at, now)} ago."
    return (
        text
        + (" Cards you changed here since show what you saved." if amended else "")
        + (" Cards you added since are at the top." if added else "")
    )


def held_html(
    exc: BaseException, read_at: float, what: str, url: str, *, amended: bool = False, added: bool = False
) -> str:
    """The strip over a page shown as last read, with Try again (a fresh read of the page): the
    deck page's (deckpage.deck_held_html) for the Binder, Lists, profiles and search."""
    text = held_text(exc, read_at, what, amended=amended, added=added)
    return (
        f"<p class='notice warn held' role='status'>{html.escape(text)} "
        f"<a class='btn' href='{html.escape(url)}'>Try again</a></p>"
    )


def busy_text_response(exc: BaseException) -> Response | None:
    """The same answer for a download (export files): plain text, the same status and headers."""
    plan = busy_plan(exc)
    if plan is None:
        return None
    return Response(
        f"{plan.title}. {plan.text}\n",
        plan.status,
        media_type="text/plain; charset=utf-8",
        headers={**_headers(plan), "X-Content-Type-Options": "nosniff"},
    )


def busy_json_response(exc: BaseException) -> Response | None:
    """The same answer for a JSON download (export.json): the API's error shape, the same status
    and headers."""
    plan = busy_plan(exc)
    if plan is None:
        return None
    return JSONResponse(
        {
            "ok": False,
            "error": plan.kind,
            "message": f"{plan.title}. {plan.text}",
            "retry_after": plan.retry_after,
        },
        plan.status,
        headers={**_headers(plan), "X-Content-Type-Options": "nosniff"},
    )


__all__ = [
    "BUSY_TITLE",
    "DEFAULT_RETRY_AFTER",
    "UNREACHABLE_TITLE",
    "BusyPlan",
    "busy_body",
    "busy_headers",
    "busy_json_response",
    "busy_plan",
    "busy_response",
    "busy_text_response",
    "held_html",
    "held_text",
    "same_url",
]
