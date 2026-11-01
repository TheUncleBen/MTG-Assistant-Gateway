"""The website's bell carries the gateway's own alerts too (0.7.46 audit QG-16, QOL-11): a price
alert that fired, a deck report that finished and the member's summary are kept for the bell
and the Alerts page beside Archidekt's, with the words the Android push uses, whether or not
push notifications are set up; the count clears once the list is opened. What waits for the
member's press (an assistant's changes) shows as a row of its own, read live. The kinds the
member switched off under Notifications stay off here too."""

from __future__ import annotations

from mtg_gateway import notify

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser

stack_options = stack_with(COMPANION)


def _sub(gw) -> str:  # noqa: ANN001
    return gw.db._one("SELECT sub FROM users", ())["sub"]


async def test_a_price_alert_and_a_finished_report_reach_the_bell(stack: Stack) -> None:
    gw = stack.gw
    b = await linked_browser(stack)
    try:
        sub = _sub(gw)
        assert not gw.notifier.enabled  # no push server in this stack: the bell still gets them
        r = await b.http.get("/notifications/count", headers={"Accept": "application/json"})
        assert r.json() == {"ok": True, "unseen": 0}
        await gw.notifier.send(
            sub, "prices", "Sol Ring fell below $10", "Sol Ring is $9.50 today.", "/watchlist?open=w_1"
        )
        gw.reports.on_finished(sub, "r1")
        await gw.notifier.drain()
        r = await b.http.get("/notifications/count", headers={"Accept": "application/json"})
        assert r.json() == {"ok": True, "unseen": 2}
        r = await b.http.get("/notifications", headers={"Accept": "application/json"})
        d = r.json()
        assert d["ok"] and d["unseen"] == 2
        html = d["html"]
        assert "From this gateway" in html and "From Archidekt" in html
        assert "Sol Ring fell below $10" in html and "href='/watchlist?open=w_1'" in html
        assert "Deck report finished" in html and "href='/history/reports/r1'" in html
        assert html.index("Sol Ring fell") < html.index("From Archidekt")  # the gateway's first
        # opened once: the count clears, the rows stay
        r = await b.http.get("/notifications/count", headers={"Accept": "application/json"})
        assert r.json() == {"ok": True, "unseen": 0}
        page = await b.http.get("/notifications", headers=NAV)
        assert "Sol Ring fell below $10" in page.text and "Your alerts from Archidekt." not in page.text
    finally:
        await b.aclose()


async def test_a_kind_switched_off_under_notifications_stays_out_of_the_bell(stack: Stack) -> None:
    gw = stack.gw
    b = await linked_browser(stack)
    try:
        sub = _sub(gw)
        notify.save_prefs(gw.db, sub, on_prices=False)
        await gw.notifier.send(sub, "prices", "Sol Ring fell below $10", "Sol Ring is $9.50.", "/watchlist")
        await gw.notifier.send(sub, "reports", "Deck report finished", "A report is ready.", "/history")
        r = await b.http.get("/notifications", headers={"Accept": "application/json"})
        d = r.json()
        assert "Sol Ring" not in d["html"] and "Deck report finished" in d["html"]
        assert d["unseen"] == 1
    finally:
        await b.aclose()


async def test_the_bell_keeps_the_newest_alerts_only(stack: Stack) -> None:
    gw = stack.gw
    await (await linked_browser(stack)).aclose()  # a member exists
    sub = _sub(gw)
    for i in range(notify.KEPT_ALERTS + 5):
        gw.db.add_gateway_alert(sub, "prices", f"Alert {i}", "text", "/watchlist")
    rows = gw.db.gateway_alerts(sub)
    assert len(rows) == notify.KEPT_ALERTS
    assert rows[0]["title"] == f"Alert {notify.KEPT_ALERTS + 4}"  # newest first
    assert gw.db.unseen_gateway_alerts(sub) == notify.KEPT_ALERTS
    gw.db.mark_gateway_alerts_seen(sub)
    assert gw.db.unseen_gateway_alerts(sub) == 0
