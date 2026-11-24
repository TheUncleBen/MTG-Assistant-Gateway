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


# -- the member's zone: every start of a day, a month and a year by the offset that day had ------

NEW_YORK = "America/New_York"


def _zoned(name: str, offset_cookie: str):
    return localday.set_zone(localday.zone_from_cookie(name)), _with(offset_cookie)


def test_day_starts_follow_the_zone_across_a_clock_change():
    # 2026-11-03 17:00 UTC: noon in New York, two days after the clocks went back (EDT to EST on
    # 1 November 2026, 06:00 UTC). Today's offset is -5 h; the days before the change had -4 h
    now = calendar.timegm((2026, 11, 3, 17, 0, 0))
    zone, off = _zoned(NEW_YORK, "-300")
    try:
        assert localday.day_start(now) == calendar.timegm((2026, 11, 3, 5, 0, 0))
        assert localday.day_start(now, 1) == calendar.timegm((2026, 11, 2, 5, 0, 0))
        # 1 November began at midnight EDT (04:00 UTC) and was 25 hours long
        assert localday.day_start(now, 2) == calendar.timegm((2026, 11, 1, 4, 0, 0))
        assert localday.day_start(now, 3) == calendar.timegm((2026, 10, 31, 4, 0, 0))
        assert localday.month_start(now) == calendar.timegm((2026, 11, 1, 4, 0, 0))
        assert localday.year_start(now) == calendar.timegm((2026, 1, 1, 5, 0, 0))
        # a filter's first moment and the day a group is named by agree, on each side of it
        days = ("2026-11-03", "2026-11-02", "2026-11-01", "2026-10-31", "2026-10-30")
        for back, day in enumerate(days):
            start = localday.day_start(now, back)
            assert localday.day_of(start) == day and localday.day_of(start - 1) != day, back
        assert history_view.when_since("7d", now=now) == calendar.timegm((2026, 10, 28, 4, 0, 0))
    finally:
        localday.reset_offset(off)
        localday.reset_zone(zone)


def test_month_and_year_starts_follow_the_zone_in_summer_time():
    # 2026-03-20 16:00 UTC: noon EDT, after the clocks went forward on 8 March. March and the
    # year began in winter time (-5 h), today's offset is -4 h
    now = calendar.timegm((2026, 3, 20, 16, 0, 0))
    zone, off = _zoned(NEW_YORK, "-240")
    try:
        assert localday.day_start(now) == calendar.timegm((2026, 3, 20, 4, 0, 0))
        assert localday.day_start(now, 12) == calendar.timegm((2026, 3, 8, 5, 0, 0))  # a 23-hour day
        assert localday.day_start(now, 13) == calendar.timegm((2026, 3, 7, 5, 0, 0))
        assert localday.month_start(now) == calendar.timegm((2026, 3, 1, 5, 0, 0))
        assert localday.year_start(now) == calendar.timegm((2026, 1, 1, 5, 0, 0))
    finally:
        localday.reset_offset(off)
        localday.reset_zone(zone)


def test_without_a_zone_the_starts_fall_back_to_todays_offset():
    token = _with(CHICAGO)
    try:
        assert localday.day_start(NOW) == calendar.timegm((2026, 11, 10, 6, 0, 0))
        assert localday.month_start(NOW) == calendar.timegm((2026, 11, 1, 6, 0, 0))
        assert localday.year_start(NOW) == calendar.timegm((2026, 1, 1, 6, 0, 0))
    finally:
        localday.reset_offset(token)


def test_a_zone_name_that_is_no_place_is_not_taken():
    assert localday.zone_from_cookie("America/New_York") == "America/New_York"
    assert localday.zone_from_cookie("Europe/Berlin") == "Europe/Berlin"
    # "localtime" is this server's own zone and "Factory" is none at all
    for bad in ("localtime", "Factory", "", None, "Not/AZone", "../etc/passwd"):
        assert localday.zone_from_cookie(bad) is None, bad
