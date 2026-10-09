"""One answer for every browser page when Archidekt cannot be used right now.

A page that reads from Archidekt catches a DeckError (or a CollectionError, which copies its
``kind``). Two kinds are not about the deck or the member at all: ``rate_limited`` (the member's
Archidekt budget, ``MTG_ARCHIDEKT_CALLS_PER_10_MIN``, or the concurrency cap is used up, or
Archidekt asked the gateway to slow down) and ``unavailable`` (Archidekt or Mystic Forge cannot
be reached). Every page maps those two through :func:`busy_response` to the same page, status and
headers, so no page can call a used-up budget "Deck not found" again. The other kinds stay with
the page's own wording.
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
    "Your account's Archidekt budget, or the gateway's, is used up for a few minutes. Nothing is "
    "wrong with this deck or your account; the page will work again shortly."
)
UNREACHABLE_TITLE = "Archidekt can't be reached right now"
UNREACHABLE_TEXT = (
    "The gateway could not get an answer from Archidekt (or the research service). This usually "
    "clears by itself within a minute or two."
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


def busy_plan(exc: BaseException) -> BusyPlan | None:
    """How to answer for ``exc``, or None when it is not an Archidekt-busy or -unreachable
    failure and the page should keep its own answer."""
    kind = getattr(exc, "kind", None)
    if kind not in _STATUS:
        return None
    retry = getattr(exc, "retry_after", None)
    if not isinstance(retry, int) or isinstance(retry, bool) or retry < 1:
        retry = DEFAULT_RETRY_AFTER
    return BusyPlan(kind, _TITLE[kind], _TEXT[kind], _STATUS[kind], min(retry, 3600), str(exc))


def _headers(plan: BusyPlan) -> dict[str, str]:
    return {"Retry-After": str(plan.retry_after), "Cache-Control": "no-store"}


def same_url(request: Request) -> str:
    """This request's path and query, for the Try again link."""
    return request.url.path + (f"?{request.url.query}" if request.url.query else "")


def busy_body(plan: BusyPlan, url: str) -> str:
    detail = f"<p class='muted small'>{html.escape(plan.detail)}</p>" if plan.detail else ""
    return (
        f"<div class='panel'><p>{html.escape(plan.text)}</p>{detail}"
        f"<div class='actions'><a class='btn btn-primary' href='{html.escape(url)}'>Try again</a> "
        "<a class='btn' href='/decks'>My decks</a></div></div>"
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
    "busy_json_response",
    "busy_plan",
    "busy_response",
    "busy_text_response",
    "same_url",
]
