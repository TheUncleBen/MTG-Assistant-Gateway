"""The website's bell carries the gateway's own alerts too (0.7.46 audit QG-16, QOL-11; one list
since the 0.7.47 notifications redesign): a price alert that fired, a deck report that finished
and the member's summary are kept for the bell and the Alerts page beside Archidekt's, with the
words the push uses, whether or not push notifications are set up; the count clears once the
list is opened, the rows stay until they are dismissed. What waits for the member's press (an
assistant's changes) shows as a row of its own, read live. The kinds the member switched off
under Notifications stay off here too."""

from __future__ import annotations

from mtg_gateway import notify

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser

stack_options = stack_with(COMPANION)


def _sub(gw) -> str:  # noqa: ANN001
    return gw.db._one("SELECT sub FROM users", ())["sub"]


async def _clear_sign_in_alerts(gw, sub: str) -> None:  # noqa: ANN001
    """The browser's sign-in kept a "New assistant" alert (0.7.47): out of the way here."""
    await gw.notifier.drain()
    gw.db.dismiss_gateway_alerts(sub, None)
    gw.db.mark_gateway_alerts_seen(sub)


async def test_a_price_alert_and_a_finished_report_reach_the_bell(stack: Stack) -> None:
    gw = stack.gw
    b = await linked_browser(stack)
    try:
        sub = _sub(gw)
        await _clear_sign_in_alerts(gw, sub)
        assert not gw.notifier.enabled  # no push server in this stack: the bell still gets them
        r = await b.http.get("/notifications/count", headers={"Accept": "application/json"})
        assert r.json() == {"ok": True, "unseen": 0}
        await gw.notifier.send(
            sub, "prices", "Price drop · Sol Ring", "$12.00 to $9.50: below $10.", "/watchlist?open=w_1"
        )
        gw.reports.on_finished(sub, "r1")
        await gw.notifier.drain()
        r = await b.http.get("/notifications/count", headers={"Accept": "application/json"})
        assert r.json() == {"ok": True, "unseen": 2}
        r = await b.http.get("/notifications", headers={"Accept": "application/json"})
        d = r.json()
        assert d["ok"] and d["unseen"] == 2 and d["unread"] == 2
        html = d["html"]
        price = gw.db.gateway_alerts(sub, kinds=("price",))[0]
        report = gw.db.gateway_alerts(sub, kinds=("report",))[0]
        # each row names its type, and opens through the Alerts page (marked read there)
        assert "Price drop · Sol Ring" in html and f"href='/notifications/open/{price['id']}'" in html
        # the type word above, the card or deck as the row's title (no "·" to break on a phone)
        assert (
            "<span class='al-type'>Price drop</span>" in html and "<b class='al-title'>Sol Ring</b>" in html
        )
        # a title with no deck named (the report is gone): the type's name above it
        assert "<span class='al-type'>Deck reports</span>" in html
        assert report["title"].startswith("Report ready") and report["path"] == "/history/reports/r1"
        assert html.index("Report ready") < html.index("Price drop")  # newest first
        # opened once: the count clears, the rows stay (unread until opened or marked read)
        r = await b.http.get("/notifications/count", headers={"Accept": "application/json"})
        assert r.json() == {"ok": True, "unseen": 0}
        page = await b.http.get("/notifications", headers=NAV)
        assert "Price drop · Sol Ring" in page.text and "2 unread" in page.text
    finally:
        await b.aclose()


async def test_a_kind_switched_off_under_notifications_stays_out_of_the_bell(stack: Stack) -> None:
    gw = stack.gw
    b = await linked_browser(stack)
    try:
        sub = _sub(gw)
        await _clear_sign_in_alerts(gw, sub)
        notify.save_deliveries(gw.db, sub, {"price": "off"})
        await gw.notifier.send(sub, "prices", "Price drop · Sol Ring", "Sol Ring is $9.50.", "/watchlist")
        await gw.notifier.send(sub, "reports", "Report ready · Atraxa", "Test finished.", "/history")
        r = await b.http.get("/notifications", headers={"Accept": "application/json"})
        d = r.json()
        assert "Sol Ring" not in d["html"] and "Report ready · Atraxa" in d["html"]
        assert d["unseen"] == 1
    finally:
        await b.aclose()


async def test_the_bell_keeps_the_newest_alerts_only(stack: Stack) -> None:
    gw = stack.gw
    await (await linked_browser(stack)).aclose()  # a member exists
    sub = _sub(gw)
    await _clear_sign_in_alerts(gw, sub)
    for i in range(notify.KEPT_ALERTS + 5):
        gw.db.add_gateway_alert(sub, "prices", f"Alert {i}", "text", "/watchlist")
    rows = gw.db.gateway_alerts(sub)
    assert len(rows) == notify.KEPT_ALERTS
    assert rows[0]["title"] == f"Alert {notify.KEPT_ALERTS + 4}"  # newest first
    assert gw.db.unseen_gateway_alerts(sub) == notify.KEPT_ALERTS
    gw.db.mark_gateway_alerts_seen(sub)
    assert gw.db.unseen_gateway_alerts(sub) == 0


async def test_delete_my_data_takes_the_member_s_bell_alerts_too(stack: Stack) -> None:
    gw = stack.gw
    await (await linked_browser(stack)).aclose()  # a member exists
    sub = _sub(gw)
    before = gw.db._conn.execute("SELECT count(*) FROM gateway_alerts WHERE sub = ?", (sub,)).fetchone()[0]
    gw.db.add_gateway_alert(sub, "prices", "Price drop · Sol Ring", "Sol Ring is $9.50.", "/watchlist")
    assert gw.db.gateway_alerts(sub)
    out = gw.db.delete_member_data(sub)
    assert out["gateway_alerts"] == before + 1
    assert gw.db._conn.execute("SELECT count(*) FROM gateway_alerts").fetchone()[0] == 0
