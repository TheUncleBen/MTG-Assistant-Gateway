"""One answer for every browser page when Archidekt cannot be used right now.

A page that reads from Archidekt catches a DeckError (or a CollectionError, which copies its
``kind``). Two kinds are not about the deck or the member at all: ``rate_limited`` (the member's
Archidekt budget, ``MTG_ARCHIDEKT_CALLS_PER_10_MIN``, or the concurrency cap is used up,
Archidekt asked the gateway to slow down, or other work for the member is still running) and
``unavailable`` (Archidekt or Mystic Forge cannot be reached). Every page maps those two through
:func:`busy_response` to the same page, status and headers, so no page can call a used-up budget
"Deck not found" again. The other kinds stay with the page's own wording.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

BUSY_TITLE = "Archidekt is busy"
BUSY_TEXT = (
    "Other work for your account is still running, or one of your account's limits was reached, so "
    "this couldn't be done right now. Nothing is wrong with your decks or your account; try again "
    "a little later."
)
# The busy text when the refusal says why (DeckError.busy_cause, or Archidekt's own 429 behind it).
BUSY_TEXTS = {
    "member_slots": (
        "Your account already has several Archidekt actions running or waiting, for example from an "
        "assistant, and there was no turn free for this one. Nothing is wrong with your decks or "
        "your account; try again once some of them have finished."
    ),
    "budget": (
        "Your account has used up its Archidekt allowance for the last 10 minutes, for example "
        "because an assistant has been busy. It refills a little at a time, and this works again "
        "once there is room. Nothing is wrong with your decks or your account."
    ),
    "archidekt": (
        "Archidekt asked the gateway to slow down, so the gateway is pausing its Archidekt requests "
        "for now. Nothing is wrong with your decks or your account; try again later."
    ),
}
# When the member's own allowance or turns ran out (not Archidekt's own pause), the title says so.
TOO_MANY_TITLE = "Too many requests right now"
MEMBER_CAUSES = frozenset({"member_slots", "budget"})
UNREACHABLE_TITLE = "Archidekt can't be reached right now"
UNREACHABLE_TEXT = (
    "The gateway could not get an answer from Archidekt (or the research service). This usually "
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
    ("/lists", "lists", "/lists", "My lists"),
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


def busy_body(plan: BusyPlan, url: str) -> str:
    """The busy panel for the page at ``url``. Archidekt's own words (``plan.detail``: codes,
    seconds) are left out: the text says what happened and that nothing is wrong."""
    noun, back, label = area_of(url)
    second = (
        f" <a class='btn' href='{html.escape(back)}'>{html.escape(label)}</a>"
        if back != url.split("?", 1)[0]
        else ""
    )
    return (
        f"<div class='panel'><p>{html.escape(area_text(plan.text, noun))}</p>"
        f"<div class='actions'><a class='btn btn-primary' href='{html.escape(url)}'>Try again</a>"
        f"{second}</div></div>"
    )


def busy_response(
    exc: BaseException,
    request: Request,
    page: Callable[..., Response],
    *,
    sub: str,
    sid: str | None,
    **page_kw: Any,
) -> Response | None:
    """The shared page for a busy or unreachable Archidekt, rendered with the caller's own
    ``page(title, body, sub=, sid=, status=)``; None when ``exc`` is some other failure."""
    plan = busy_plan(exc)
    if plan is None:
        return None
    body = busy_body(plan, same_url(request))
    resp = page(plan.title, body, sub=sub, sid=sid, status=plan.status, **page_kw)
    resp.headers.update(_headers(plan))
    return resp


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
    "same_url",
]
