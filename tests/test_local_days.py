"""Days are the member's days, not UTC's.

History's "Today" and "Yesterday", its When filter and the Reports "Today" filter were worked out
in UTC, so for a member in Chicago everything after 6 PM landed under the next day. The page now
tells the server how far its clock is from UTC (a cookie with minutes east) and the lists use it."""

from __future__ import annotations

import calendar

from mtg_gateway import history_view, localday, reports_page

# 2026-11-11 01:30 UTC is 2026-11-10 19:30 in Chicago (UTC-6)
NOW = calendar.timegm((2026, 11, 11, 1, 30, 0))
CHICAGO = "-360"


def _with(cookie: str | None):
    return localday.set_offset(localday.offset_from_cookie(cookie))


def test_the_cookie_is_minutes_east_and_anything_else_is_utc():
    assert localday.offset_from_cookie("-360") == -21600 and localday.offset_from_cookie("330") == 19800
    for bad in (None, "", "abc", "²", "-", "841", "-9999", "1.5", "+60", "3 0"):
        assert localday.offset_from_cookie(bad) == 0, bad


def test_an_evening_entry_is_today_for_a_member_west_of_utc():
    token = _with(CHICAGO)
    try:
        assert localday.day_of(NOW) == "2026-11-10"
        # "Today" starts at the member's midnight: 06:00 UTC on the 10th
        assert history_view.when_since("today", now=NOW) == calendar.timegm((2026, 11, 10, 6, 0, 0))
        assert reports_page._since("today", NOW) == calendar.timegm((2026, 11, 10, 6, 0, 0))
        assert history_view.when_since("7d", now=NOW) == calendar.timegm((2026, 11, 4, 6, 0, 0))
        assert history_view.when_since("year", now=NOW) == calendar.timegm((2026, 1, 1, 6, 0, 0))
    finally:
        localday.reset_offset(token)


def test_without_the_cookie_days_are_utc_days_as_before():
    assert localday.day_of(NOW) == "2026-11-11"
    assert history_view.when_since("today", now=NOW) == calendar.timegm((2026, 11, 11, 0, 0, 0))
    assert reports_page._since("today", NOW) == calendar.timegm((2026, 11, 11, 0, 0, 0))


def test_the_page_script_sends_the_offset_and_marks_the_day_lists():
    import pathlib

    src = pathlib.Path(history_view.__file__).parent
    js = (src / "static" / "feedback.js").read_text()
    assert "mtg_tzo=" in js and "getTimezoneOffset" in js and "data-local-days" in js
    assert "data-local-days" in (src / "history_view.py").read_text()
    assert "data-local-days" in (src / "reports_page.py").read_text()
