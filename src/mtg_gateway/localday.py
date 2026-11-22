"""The member's own day, for the lists that group and filter by day.

Clock times are drawn in the viewer's time zone by the page (static/feedback.js), but "Today",
"Yesterday" and the "When" filters are worked out here, where only UTC was known: for a member
west of Greenwich an evening's entries landed under the next day. The page now tells the server
how far its clock is from UTC in a cookie (minutes east, no place name), and the day lists use it.
Without the cookie (the first page of a new browser, or no scripts) days are UTC days as before.
"""

from __future__ import annotations

import time
from contextvars import ContextVar

TZ_COOKIE = "mtg_tzo"
_MOST = 14 * 60  # the widest offsets in use are UTC-12:00 and UTC+14:00

_offset: ContextVar[int] = ContextVar("local_day_offset", default=0)


def offset_from_cookie(value: str | None) -> int:
    """Seconds east of UTC from the cookie's minutes ("-360"), 0 for anything else."""
    text = (value or "").strip()
    digits = text[1:] if text[:1] == "-" else text
    if not (digits.isascii() and digits.isdigit() and len(digits) <= 3) or int(digits) > _MOST:
        return 0
    return int(text) * 60


def set_offset(seconds: int):  # noqa: ANN201 - the ContextVar token
    return _offset.set(seconds)


def reset_offset(token) -> None:  # noqa: ANN001
    _offset.reset(token)


def offset() -> int:
    return _offset.get()


def day_of(ts: float) -> str:
    """The member's calendar day ("2026-11-11") a moment falls on."""
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts) + offset()))


def day_start(now: float, days_back: int = 0) -> int:
    """The moment (epoch seconds) the member's day began, ``days_back`` days before today's."""
    local = int(now) + offset()
    return (local // 86400 - days_back) * 86400 - offset()


def year_start(now: float) -> int:
    """The moment the member's year began."""
    import calendar

    year = time.gmtime(int(now) + offset()).tm_year
    return int(calendar.timegm((year, 1, 1, 0, 0, 0))) - offset()


def month_start(now: float) -> int:
    import calendar

    t = time.gmtime(int(now) + offset())
    return int(calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0))) - offset()
