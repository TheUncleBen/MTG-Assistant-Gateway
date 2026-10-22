"""The Account page's Notifications and Summary cards: one orange button per card, the
summary in its own card with Send it now, each tick on its own row, the notices inside the card
they came from, and the tick-then-button pattern of Your view choices and Delete my data."""

from __future__ import annotations

import re

from mtg_gateway import notify, summary

from .conftest import Stack, stack_with
from .test_notify_app import APP_UA
from .test_watchlist import TOKEN, Clock, _browser, _opt_in, _options, _post

stack_options = stack_with(_options(ntfy_url="https://ntfy.test", ntfy_token=TOKEN))


def _card(page: str, card_id: str) -> str:
    return page.split(f"id='{card_id}'", 1)[1].split("</section>", 1)[0]


def _orange(html: str) -> int:
    return len(re.findall(r"class='primary'|class='btn-primary'", html))


async def test_each_card_has_one_orange_button_and_every_tick_its_own_row(stack: Stack) -> None:
    b = await _browser(stack)
    try:
        page = (await b.http.get("/account")).text
        notes, summ = _card(page, "notifications"), _card(page, "summary")
        # not on yet: no On label, no Your phones buttons
        assert "ui-lbl green" not in notes and "Send test" not in notes
        await _opt_in(b)
        page = (await b.http.get("/account")).text
        notes, summ = _card(page, "notifications"), _card(page, "summary")
        assert "<h2 id='n-h'>Notifications</h2><span class='ui-lbl green'>" in notes and "On</span>" in notes
        # the summary is its own card after Notifications, not a part inside it
        assert "id='summary'" not in notes and page.index("id='notifications'") < page.index("id='summary'")
        assert _orange(notes) == 1 and "Save notifications" in notes
        assert _orange(summ) == 1 and "Save summary" in summ
        # each tick its own 44 px row (.ui-checks label); the When group has no panel border
        assert "<div class='ui-checks master'><label><input type='checkbox' name='ntfy_on'" in notes
        assert "<fieldset class='nf-when'><legend>When</legend><div class='ui-checks'>" in notes
        assert "class='check'" not in notes
        # Your phones: the hint, Send test and Disconnect my phones plain, the explanation visible
        assert "<h3>Your phones</h3>" in notes and "/install?for=android" in notes
        assert "<button class='btn-plain'>Send test</button>" in notes
        assert "<button class='btn-plain' aria-describedby='n-disc'>Disconnect my phones</button>" in notes
        assert (
            "Disconnect my phones: your phones stop getting notifications until each one is connected again."
            in notes
        )
        assert "title='Your phones stop" not in page
    finally:
        await b.aclose()


async def test_in_the_app_get_notifications_on_this_phone_is_plain(stack: Stack) -> None:
    b = await _browser(stack)
    b.http.headers["user-agent"] = APP_UA
    try:
        await _opt_in(b)
        notes = _card((await b.http.get("/account")).text, "notifications")
        button = re.search(r"<button[^>]*data-native='startNotifications'[^>]*>", notes).group(0)
        assert "class='btn-plain'" in button and "primary" not in button
        assert _orange(notes) == 1
        # the part is about this phone, under its own heading only
        assert "<h3>This phone</h3>" in notes and "<h3>Your phones</h3>" not in notes
    finally:
        await b.aclose()


async def test_send_it_now_sends_the_summary_the_card_shows(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)  # Monday 2026-10-12
    b = await _browser(stack)
    try:
        await _opt_in(b)
        # How often Off: nothing to send, so no Send it now
        assert "Send it now" not in _card((await b.http.get("/account")).text, "summary")
        await _post(b, "/account", {"action": "summary_save", "summary_every": "daily"})
        summ = _card((await b.http.get("/account")).text, "summary")
        assert "<h3>Today</h3><pre class='sum-text'>Summary for Oct 11 to Oct 12." in summ
        assert "value='summary_send'" in summ and "Send it now</button>" in summ
        assert "as your daily summary would. Like Send test, once a minute." in summ
        sent = len(stack.ntfy.messages)
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == "/account?ok=summary_sent#summary"
        assert len(stack.ntfy.messages) == sent + 1
        msg = stack.ntfy.messages[-1]
        assert msg["title"] == "Your daily summary" and msg["click"] == "https://mtg.test/account#summary"
        # the same short text the schedule sends
        assert msg["message"] == summary.short_text(stack.gw.summaries.build("user-1", "day"))
        # once a minute, on its own wait: Send test still works, and the test does not hold it back
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == "/account?err=summary_send_wait#summary"
        assert len(stack.ntfy.messages) == sent + 1
        r = await _post(b, "/account", {"action": "notify_test"})
        assert r.headers["location"] == "/account?ok=notify_test_sent#notifications"
        stack.gw.notifier._last_summary.clear()
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == "/account?ok=summary_sent#summary"
        # weekly: the week's summary, worded so
        stack.gw.notifier._last_summary.clear()
        await _post(b, "/account", {"action": "summary_save", "summary_every": "weekly"})
        summ = _card((await b.http.get("/account")).text, "summary")
        assert "<h3>The last 7 days</h3>" in summ and "as your weekly summary would." in summ
        await _post(b, "/account", {"action": "summary_send"})
        assert stack.ntfy.messages[-1]["title"] == "Your weekly summary"
        # a refusal from the server is said plainly
        stack.gw.notifier._last_summary.clear()
        stack.ntfy.status = 403
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == "/account?err=summary_send_failed#summary"
    finally:
        await b.aclose()


async def test_send_it_now_waits_for_notifications_and_a_choice(stack: Stack) -> None:
    b = await _browser(stack)
    try:
        await _opt_in(b)
        await _post(b, "/account", {"action": "summary_save", "summary_every": "daily"})
        await _post(b, "/account", {"action": "notify_save"})  # notifications off
        summ = _card((await b.http.get("/account")).text, "summary")
        button = re.search(r"<button[^>]*>[^<]*<svg[^>]*>.*?</svg>Send it now", summ).group(0)
        assert " disabled" in button and "Notifications are off" in summ
        sent = len(stack.ntfy.messages)
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == "/account?err=summary_send_failed#summary"
        await _opt_in(b)
        await _post(b, "/account", {"action": "summary_save", "summary_every": "off"})
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == "/account?err=summary_send_failed#summary"
        assert len(stack.ntfy.messages) == sent
    finally:
        await b.aclose()


async def test_notices_show_inside_the_card_they_came_from(stack: Stack) -> None:
    b = await _browser(stack)
    try:
        await _opt_in(b)
        page = (await b.http.get("/account?ok=summary_sent")).text
        top = page.split("id='notifications'", 1)[0]
        assert notify.ACCOUNT_OK["summary_sent"] not in top
        assert (
            "<div class='notice ok' role='status'>The summary is sent. Each phone you connected should show "
            "it.</div>" in _card(page, "summary")
        )
        page = (await b.http.get("/account?err=notify_test_wait")).text
        assert notify.ACCOUNT_ERR["notify_test_wait"] in _card(page, "notifications")
        assert notify.ACCOUNT_ERR["notify_test_wait"] not in page.split("id='notifications'", 1)[0]
        page = (await b.http.get("/account?err=summary_send_wait")).text
        assert (
            "A summary was just sent. The gateway sends one a minute, so wait before sending another."
            in _card(page, "summary")
        )
        # other notices stay at the top
        page = (await b.http.get("/account?err=confirm_forget")).text
        assert page.index("Nothing was forgotten.") < page.index("id='notifications'")
    finally:
        await b.aclose()


def test_the_summary_dates_read_like_the_pages_dates() -> None:
    base = {"cards": [], "decks": [], "reports_finished": [], "proposals_waiting": 0, "proposals_applied": 0}
    s = {**base, "period": "day", "from_day": "2026-10-18", "to_day": "2026-10-19"}
    assert summary.text(s).startswith("Summary for Oct 18 to Oct 19.\n")
    s = {**base, "period": "week", "from_day": "2025-12-29", "to_day": "2026-01-05"}
    assert summary.text(s).startswith("Summary for Dec 29, 2025 to Jan 5.\n")


async def test_view_choices_and_delete_my_data_put_the_tick_above_the_button(stack: Stack) -> None:
    b = await _browser(stack)
    try:
        page = (await b.http.get("/account")).text
        choices = page.split("id='choices'", 1)[1].split("</form>", 1)[0]
        assert (
            "<div class='ui-checks'><label><input type='checkbox' name='confirm' value='yes' required>"
            "<span>Yes, forget my view choices on every device</span></label></div>"
            "<div class='ui-actions'><button>Forget my view choices</button></div>" in choices
        )
        # Delete my data: a red outline opens the box that holds the tick and the solid red button
        delete = page.split("<h2>Delete my data</h2>", 1)[1].split("</form>", 1)[0]
        assert "<a class='btn btn-outline-danger' href='/account?delete=1' data-box-open='delete'>" in delete
        assert "<input type='checkbox' id='delete-tick' name='confirm' value='yes' required>" in delete
        assert "<span>Yes, delete my data from this gateway</span></label>" in delete
        # the Connected apps card is the target of My activity's "Connected apps" button
        assert "<div class='card' id='apps'><h2>Connected apps</h2>" in page
    finally:
        await b.aclose()
