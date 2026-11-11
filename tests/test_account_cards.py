"""Account details › Push notifications (its own page, 0.7.46) and its Summary card: one orange
button per card, the summary in its own card with Send it now, each tick on its own row, the
notices inside the card they came from, and the tick-then-button pattern of Your view choices
(its own page) and Delete my data (the list's box)."""

from __future__ import annotations

import re

from mtg_gateway import notify, summary

from .conftest import Stack, stack_with
from .test_notify_app import APP_UA
from .test_watchlist import TOKEN, Clock, _browser, _opt_in, _options, _post

N = "/account/details/notifications"
stack_options = stack_with(_options(ntfy_url="https://ntfy.test", ntfy_token=TOKEN))


def _card(page: str, card_id: str) -> str:
    return page.split(f"id='{card_id}'", 1)[1].split("</section>", 1)[0]


def _orange(html: str) -> int:
    return len(re.findall(r"class='primary'|class='btn-primary'", html))


async def test_each_card_has_one_orange_button_and_every_tick_its_own_row(stack: Stack) -> None:
    b = await _browser(stack)
    try:
        page = (await b.http.get(N)).text
        notes, summ = _card(page, "notifications"), _card(page, "summary")
        # not on yet: no On label, no Your phones buttons; the list's row says Off
        assert "ui-lbl green" not in notes and "Send test" not in notes
        assert (
            "<span class='lbl'>Push notifications</span><span class='val'>Off</span>"
            in (await b.http.get("/account/details")).text
        )
        await _opt_in(b)
        page = (await b.http.get(N)).text
        notes, summ = _card(page, "notifications"), _card(page, "summary")
        # the page's title names the card: no heading repeated inside it, the On label alone
        assert "<h1>Push notifications</h1>" in page and "<h2 id='n-h'>" not in page
        assert "aria-label='Push notifications'><div class='gh'><span class='ui-lbl green'>" in notes
        assert "On</span>" in notes
        assert (
            "<span class='lbl'>Push notifications</span><span class='val'>On</span>"
            in (await b.http.get("/account/details")).text
        )
        # the summary is its own card after Notifications, not a part inside it
        assert "id='summary'" not in notes and page.index("id='notifications'") < page.index("id='summary'")
        assert _orange(notes) == 1 and "Save push notifications" in notes
        assert _orange(summ) == 1 and "Save summary" in summ
        # the master tick its own 44 px row (.ui-checks label); then one row a type of alert (0.7.47)
        assert "<div class='ui-checks master'><label><input type='checkbox' name='ntfy_on'" in notes
        assert "<fieldset class='nf-kinds'><legend>Each type of alert</legend>" in notes
        assert "class='check'" not in notes
        # Your phones: the hint, Send test and Disconnect my phones plain, the explanation visible
        assert "<h3>Your phones</h3>" in notes and "/install?for=android" in notes
        assert "<button class='btn-plain'>Send test</button>" in notes
        assert "<button class='btn-plain' aria-describedby='n-disc'>Disconnect my phones</button>" in notes
        assert (
            "Disconnect my phones: your phones stop getting push notifications until each one is "
            "connected again." in notes
        )
        assert "title='Your phones stop" not in page
    finally:
        await b.aclose()


async def test_in_the_app_get_notifications_on_this_phone_is_plain(stack: Stack) -> None:
    b = await _browser(stack)
    b.http.headers["user-agent"] = APP_UA
    try:
        await _opt_in(b)
        notes = _card((await b.http.get(N)).text, "notifications")
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
        assert "Send it now" not in _card((await b.http.get(N)).text, "summary")
        await _post(b, "/account", {"action": "summary_save", "summary_every": "daily"})
        summ = _card((await b.http.get(N)).text, "summary")
        assert "<h3>Today</h3><pre class='sum-text'>Summary for Oct 11 to Oct 12." in summ
        assert "value='summary_send'" in summ and "Send it now</button>" in summ
        assert "as your daily summary would. Like Send test, once a minute." in summ
        sent = len(stack.ntfy.messages)
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == f"{N}?ok=summary_sent#summary"
        assert len(stack.ntfy.messages) == sent + 1
        msg = stack.ntfy.messages[-1]
        assert msg["title"] == "Summary · Today" and msg["click"].startswith(
            "https://mtg.test/notifications/open/"
        )
        # it is kept under Alerts too, and opens the summary through it (marked read there)
        aid = int(msg["click"].rsplit("/", 1)[1])
        assert stack.gw.db.gateway_alert("user-1", aid)["path"] == f"{N}#summary"
        # the same short text the schedule sends
        assert msg["message"] == summary.short_text(stack.gw.summaries.build("user-1", "day"))
        # once a minute, on its own wait: Send test still works, and the test does not hold it back
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == f"{N}?err=summary_send_wait#summary"
        assert len(stack.ntfy.messages) == sent + 1
        r = await _post(b, "/account", {"action": "notify_test"})
        assert r.headers["location"] == f"{N}?ok=notify_test_sent#notifications"
        stack.gw.notifier._last_summary.clear()
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == f"{N}?ok=summary_sent#summary"
        # weekly: the week's summary, worded so
        stack.gw.notifier._last_summary.clear()
        await _post(b, "/account", {"action": "summary_save", "summary_every": "weekly"})
        summ = _card((await b.http.get(N)).text, "summary")
        assert "<h3>The last 7 days</h3>" in summ and "as your weekly summary would." in summ
        await _post(b, "/account", {"action": "summary_send"})
        assert stack.ntfy.messages[-1]["title"] == "Summary · This week"
        # a refusal from the server is said plainly
        stack.gw.notifier._last_summary.clear()
        stack.ntfy.status = 403
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == f"{N}?err=summary_send_failed#summary"
    finally:
        await b.aclose()


async def test_send_it_now_waits_for_notifications_and_a_choice(stack: Stack) -> None:
    b = await _browser(stack)
    try:
        await _opt_in(b)
        await _post(b, "/account", {"action": "summary_save", "summary_every": "daily"})
        await _post(b, "/account", {"action": "notify_save"})  # notifications off
        summ = _card((await b.http.get(N)).text, "summary")
        button = re.search(r"<button[^>]*>[^<]*<svg[^>]*>.*?</svg>Send it now", summ).group(0)
        assert " disabled" in button and "Push notifications are off" in summ
        sent = len(stack.ntfy.messages)
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == f"{N}?err=summary_send_failed#summary"
        await _opt_in(b)
        await _post(b, "/account", {"action": "summary_save", "summary_every": "off"})
        r = await _post(b, "/account", {"action": "summary_send"})
        assert r.headers["location"] == f"{N}?err=summary_send_failed#summary"
        assert len(stack.ntfy.messages) == sent
    finally:
        await b.aclose()


async def test_notices_show_inside_the_card_they_came_from(stack: Stack) -> None:
    b = await _browser(stack)
    try:
        await _opt_in(b)
        page = (await b.http.get(f"{N}?ok=summary_sent")).text
        top = page.split("id='notifications'", 1)[0]
        assert notify.ACCOUNT_OK["summary_sent"] not in top
        assert (
            "<div class='notice ok' role='status'>The summary is sent. Each phone you connected should show "
            "it.</div>" in _card(page, "summary")
        )
        page = (await b.http.get(f"{N}?err=notify_test_wait")).text
        assert notify.ACCOUNT_ERR["notify_test_wait"] in _card(page, "notifications")
        assert notify.ACCOUNT_ERR["notify_test_wait"] not in page.split("id='notifications'", 1)[0]
        page = (await b.http.get(f"{N}?err=summary_send_wait")).text
        assert (
            "A summary was just sent. The gateway sends one a minute, so wait before sending another."
            in _card(page, "summary")
        )
        # the list's old addresses open the page the notice belongs to, with it
        r = await b.http.get("/account/details?ok=summary_sent")
        assert r.status_code == 303 and r.headers["location"] == f"{N}?ok=summary_sent#notifications"
        r = await b.http.get("/account/details?ok=notify_saved&connect=1")
        assert r.headers["location"] == f"{N}?ok=notify_saved&connect=1#notifications"
        # other notices stay at the top of their page
        page = (await b.http.get("/account/details/choices?err=confirm_forget")).text
        assert page.index("Nothing was forgotten.") < page.index("id='choices'")
        r = await b.http.get("/account/details?err=confirm_forget")
        assert r.headers["location"] == "/account/details/choices?err=confirm_forget#choices"
    finally:
        await b.aclose()


def test_the_summary_dates_read_like_the_pages_dates() -> None:
    base = {"cards": [], "decks": [], "reports_finished": [], "proposals_waiting": 0, "proposals_applied": 0}
    s = {**base, "period": "day", "from_day": "2026-10-18", "to_day": "2026-10-19"}
    assert summary.text(s).startswith("Summary for Oct 18 to Oct 19.\n")
    s = {**base, "period": "week", "from_day": "2025-12-29", "to_day": "2026-01-05"}
    assert summary.text(s).startswith("Summary for Dec 29, 2025 to Jan 5.\n")


async def test_view_choices_asks_in_a_box_and_delete_my_data_puts_the_tick_above_the_button(
    stack: Stack,
) -> None:
    b = await _browser(stack)
    try:
        page = (await b.http.get("/account/details/choices")).text
        # Forget my view choices asks in the shared box (Keep them, then the solid Forget), as every
        # action that can't be undone does; the card's own button only opens it (audit D17)
        choices = page.split("id='choices'", 1)[1].split("</form>", 1)[0]
        assert (
            "data-box-open='forget-choices'" in choices
            and "name='confirm'" not in choices.split("<div class='ui-box", 1)[0]
        )
        box = page.split("id='forget-choices' role='dialog'", 1)[1].split("</div></div>", 1)[0]
        assert "<input type='hidden' name='confirm' value='yes'>" in box
        assert "<button type='button' class='btn-plain' data-cancel>Keep them</button>" in box
        assert "<button type='submit' class='danger' form='forgetf'>Forget my view choices</button>" in box
        # Delete my data: the list's red row opens the box that holds the tick and the solid red button
        page = (await b.http.get("/account/details")).text
        assert "<a class='yrow ydanger' href='/account/details?delete=1'" in page
        delete = page.split("id='delete' role='dialog'", 1)[1].split("</form>", 1)[0]
        assert "<input type='checkbox' id='delete-tick' name='confirm' value='yes' required>" in delete
        assert "<span>Yes, delete my data from this gateway</span></label>" in delete
        # Approval mode and Connected apps are one Assistants row (per-assistant modes), still #apps
        assert "<section class='ygrp' data-g='1' id='apps'><h2>Assistant</h2>" in page
        assert "<a class='yrow' href='/account/assistants'>" in page
    finally:
        await b.aclose()
