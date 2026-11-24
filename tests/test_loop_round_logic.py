"""Six low findings of the 0.7.48 review (review-rest.md), each the scenario that went wrong:
a weekly summary sent off-Monday after a spell of Off, the Watchlist's last check shown before it
happened, past entries grouped by today's UTC offset across a clock change, and an unread alert
brought back read by Undo. The bell's box and feedback.js's reload are in
test_loop_round_browser.py."""

from __future__ import annotations

import calendar
from pathlib import Path

from mtg_gateway import localday, theme
from mtg_gateway.db import Database
from mtg_gateway.watchlist_page import Query, watchlist_page_html

from .conftest import Stack, stack_with
from .test_watchlist import TOKEN, _browser, _opt_in, _options, _post
from .test_watchlist_page import DATA

stack_options = stack_with(_options(ntfy_url="https://ntfy.test", ntfy_token=TOKEN))


# 1 -----------------------------------------------------------------------------------------------
async def test_a_weekly_summary_after_a_spell_of_off_waits_for_monday(stack: Stack) -> None:
    b = await _browser(stack)
    try:
        await _opt_in(b)
        due = stack.gw.summaries.send_due
        await _post(b, "/account", {"action": "summary_save", "summary_every": "daily"})
        assert await due("2026-09-01") == 1  # summary_sent_day is now an old day
        await _post(b, "/account", {"action": "summary_save", "summary_every": "off"})
        await _post(b, "/account", {"action": "summary_save", "summary_every": "weekly"})
        # Thursday: not a Monday, so the first weekly one waits
        assert await due("2026-09-17") == 0
        assert await due("2026-09-21") == 1  # Monday
        assert await due("2026-09-22") == 0
    finally:
        await b.aclose()


# 3 -----------------------------------------------------------------------------------------------
def test_the_last_check_is_never_a_moment_still_to_come() -> None:
    now = calendar.timegm((2026, 10, 19, 2, 0, 0))  # before today's 09:00 check
    data = {**DATA, "checked_daily_at_utc": "09:00"}
    page = watchlist_page_html(data, {}, "tok", Query(), now=now)
    status = page.split("<p class='w-status ui-dots'>")[1].split("</p>")[0]
    assert "datetime='2026-10-19T02:00:00Z'" in status, status
    assert "datetime='2026-10-19T09:00:00Z'" not in status


# 4 -----------------------------------------------------------------------------------------------
JUL_15_0030_EDT = calendar.timegm((2026, 7, 15, 4, 30, 0))


def test_the_zone_cookie_is_a_real_zone_name_or_nothing() -> None:
    assert localday.zone_from_cookie("America/New_York") == "America/New_York"
    assert localday.zone_from_cookie(" UTC ") == "UTC"
    long = "America/" + "A" * 80
    for bad in (None, "", "Nowhere/Land", "../etc/passwd", "/etc/localtime", "America/New York", long, "é"):
        assert localday.zone_from_cookie(bad) is None, bad


def test_a_past_entry_is_grouped_by_its_own_offset_when_the_zone_is_known() -> None:
    token = localday.set_offset(localday.offset_from_cookie("-300"))  # New York in November
    try:
        assert localday.day_of(JUL_15_0030_EDT) == "2026-07-14"  # the offset alone: a day early
        ztoken = localday.set_zone(localday.zone_from_cookie("America/New_York"))
        try:
            assert localday.day_of(JUL_15_0030_EDT) == "2026-07-15"
        finally:
            localday.reset_zone(ztoken)
        assert localday.day_of(JUL_15_0030_EDT) == "2026-07-14"
    finally:
        localday.reset_offset(token)


async def test_the_middleware_sets_the_zone_for_the_request_and_resets_it() -> None:
    seen: list[str] = []

    async def app(scope, receive, send) -> None:  # noqa: ANN001
        seen.append(localday.day_of(JUL_15_0030_EDT))

    mw = theme.ThemeMiddleware(app)
    cookie = b"mtg_tzo=-300; mtg_tz=America/New_York"
    await mw({"type": "http", "path": "/history", "headers": [(b"cookie", cookie)]}, None, None)
    await mw({"type": "http", "path": "/history", "headers": [(b"cookie", b"mtg_tzo=-300")]}, None, None)
    assert seen == ["2026-07-15", "2026-07-14"]
    assert localday.day_of(JUL_15_0030_EDT) == "2026-07-15"  # UTC outside a request


# 6 -----------------------------------------------------------------------------------------------
def test_undo_after_dismiss_brings_an_unread_alert_back_unread(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.db")
    try:
        db.upsert_user("user-1", email=None, name=None, preferred_username="u", groups=[])
        unread = db.add_gateway_alert("user-1", "price", "Price drop", "x", "/watchlist", kind="price")
        read = db.add_gateway_alert("user-1", "price", "Price rise", "y", "/watchlist", kind="price")
        db._conn.execute("UPDATE gateway_alerts SET read_at = 1000 WHERE id = ?", (read,))
        assert db.dismiss_gateway_alerts("user-1", [unread, read]) == [unread, read]
        assert db.dismiss_gateway_alerts("user-1", [unread, read], undo=True) == [unread, read]
        assert db.gateway_alert("user-1", unread)["read_at"] is None
        assert db.gateway_alert("user-1", read)["read_at"] == 1000  # read before: still read
    finally:
        db.close()
