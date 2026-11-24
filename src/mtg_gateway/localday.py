"""The member's own day, for the lists that group and filter by day.

Clock times are drawn in the viewer's time zone by the page (static/feedback.js), but "Today",
"Yesterday" and the "When" filters are worked out here, where only UTC was known: for a member
west of Greenwich an evening's entries landed under the next day. The page now tells the server
how far its clock is from UTC in a cookie (minutes east), and the day lists use it. That is
today's offset only, so the page also sends its time zone's name (``mtg_tz``, "America/New_York")
when it has one: a past entry is then grouped by the offset its own day had, on the other side of
a clock change too. A name the server does not know is ignored and the offset is used.
Without the cookies (the first page of a new browser, or no scripts) days are UTC days as before.
"""

from __future__ import annotations

import datetime as _dt
import functools
import re
import time
import zoneinfo
from contextvars import ContextVar

TZ_COOKIE = "mtg_tzo"
ZONE_COOKIE = "mtg_tz"
_MOST = 14 * 60  # the widest offsets in use are UTC-12:00 and UTC+14:00
_ZONE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}")
_ZONE_MAX = 64  # the longest IANA name is 32 characters
_NO_PLACE = frozenset({"localtime", "factory"})

_offset: ContextVar[int] = ContextVar("local_day_offset", default=0)
_zone: ContextVar[zoneinfo.ZoneInfo | None] = ContextVar("local_day_zone", default=None)


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


@functools.lru_cache(maxsize=1)
def _known_zones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def zone_from_cookie(value: str | None) -> str | None:
    """The IANA zone name in the cookie ("America/New_York"), ``None`` for anything else: only a
    name this server's time zone database has is taken."""
    text = (value or "").strip()
    if not text or len(text) > _ZONE_MAX or not _ZONE_RE.fullmatch(text) or text not in _known_zones():
        return None
    # names the database has that are no place: "localtime" is this server's own zone, "Factory"
    # is none at all. A browser never sends either
    if text.casefold() in _NO_PLACE:
        return None
    return text


def set_zone(name: str | None):  # noqa: ANN201 - the ContextVar token
    try:
        return _zone.set(zoneinfo.ZoneInfo(name) if name else None)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return _zone.set(None)


def reset_zone(token) -> None:  # noqa: ANN001
    _zone.reset(token)


def day_of(ts: float) -> str:
    """The member's calendar day ("2026-11-11") a moment falls on: by the member's zone when the
    page sent its name, so by the offset that day had, else by today's offset."""
    zone = _zone.get()
    if zone is not None:
        try:
            return _dt.datetime.fromtimestamp(int(ts), zone).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            pass
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts) + offset()))


def _zone_date(now: float) -> tuple[zoneinfo.ZoneInfo, _dt.date] | None:
    """The member's zone and the calendar day ``now`` falls on there; ``None`` without a zone."""
    zone = _zone.get()
    if zone is None:
        return None
    try:
        return zone, _dt.datetime.fromtimestamp(int(now), zone).date()
    except (OverflowError, OSError, ValueError):
        return None


def _midnight(zone: zoneinfo.ZoneInfo, day: _dt.date) -> int:
    """The moment a calendar day began in a zone, by the offset that day had (a day whose clock
    change is at midnight begins at the first moment that carries its date)."""
    return int(_dt.datetime(day.year, day.month, day.day, tzinfo=zone).timestamp())


def day_start(now: float, days_back: int = 0) -> int:
    """The moment (epoch seconds) the member's day began, ``days_back`` days before today's: by
    the member's zone when the page sent its name (as ``day_of``, so a filter and the groups it
    shows agree across a clock change), else by today's offset."""
    at = _zone_date(now)
    if at is not None:
        try:
            return _midnight(at[0], at[1] - _dt.timedelta(days=days_back))
        except (OverflowError, OSError, ValueError):
            pass
    local = int(now) + offset()
    return (local // 86400 - days_back) * 86400 - offset()


def year_start(now: float) -> int:
    """The moment the member's year began (by the zone, else by today's offset)."""
    import calendar

    at = _zone_date(now)
    if at is not None:
        try:
            return _midnight(at[0], at[1].replace(month=1, day=1))
        except (OverflowError, OSError, ValueError):
            pass
    year = time.gmtime(int(now) + offset()).tm_year
    return int(calendar.timegm((year, 1, 1, 0, 0, 0))) - offset()


def month_start(now: float) -> int:
    """The moment the member's month began (by the zone, else by today's offset)."""
    import calendar

    at = _zone_date(now)
    if at is not None:
        try:
            return _midnight(at[0], at[1].replace(day=1))
        except (OverflowError, OSError, ValueError):
            pass
    t = time.gmtime(int(now) + offset())
    return int(calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0))) - offset()
