"""Search inside the Alerts page (0.7.46 audit QG-25, QOL-16): a search box and a Type filter
(waiting for you, price alerts, deck reports, summaries, Archidekt's), which work without scripts
as a GET. The bell's own box stays whole."""

from __future__ import annotations

from mtg_gateway import notify

from .conftest import Stack, stack_with
from .test_bell_gateway_alerts import _sub
from .test_companion import COMPANION, NAV, linked_browser

stack_options = stack_with(COMPANION)


async def test_the_alerts_page_filters_by_type_and_by_words(stack: Stack) -> None:
    gw = stack.gw
    b = await linked_browser(stack)
    try:
        sub = _sub(gw)
        await gw.notifier.send(sub, "prices", "Sol Ring fell below $10", "Sol Ring is $9.50.", "/watchlist")
        await gw.notifier.send(sub, "reports", "Deck report finished", "A report is ready.", "/history")
        await gw.notifier.send(sub, "summary", "Your week on the gateway", "Two decks changed.", "/")
        await gw.notifier.drain()
        page = (await b.http.get("/notifications", headers=NAV)).text
        assert "<form class='ui-tools' role='search' method='get' action='/notifications'>" in page
        assert "<select name='type' aria-label='Type'>" in page
        for value, text in notify.ALERT_TYPES:
            assert f"<option value='{value}'>{text}</option>" in page or (
                value == "" and f"<option value='' selected>{text}</option>" in page
            ), text
        assert "Sol Ring fell below $10" in page and "Deck report finished" in page
        assert "<h3 class='bell-part'>From Archidekt</h3>" in page
        # one type: that part alone, its heading kept
        page = (await b.http.get("/notifications?type=prices", headers=NAV)).text
        assert "Sol Ring fell below $10" in page
        assert "Deck report finished" not in page and "Your week on the gateway" not in page
        assert "<h3 class='bell-part'>From Archidekt</h3>" not in page
        assert "<option value='prices' selected>Price alerts</option>" in page
        page = (await b.http.get("/notifications?type=archidekt", headers=NAV)).text
        assert "<h3 class='bell-part'>From Archidekt</h3>" in page and "Sol Ring fell below $10" not in page
        # words: a title or a line that holds them, any type
        page = (await b.http.get("/notifications?q=week", headers=NAV)).text
        assert "Your week on the gateway" in page and "Sol Ring fell below $10" not in page
        assert "<input type='search' name='q' value='week' placeholder='Search alerts'>" in page
        page = (await b.http.get("/notifications?q=nothing+like+this", headers=NAV)).text
        assert "No alerts match." in page
        # the bell's box is never filtered (the page's script asks for it whole)
        d = (await b.http.get("/notifications?type=prices", headers={"Accept": "application/json"})).json()
        assert (
            "Deck report finished" in d["html"] and "<h3 class='bell-part'>From Archidekt</h3>" in d["html"]
        )
    finally:
        await b.aclose()
