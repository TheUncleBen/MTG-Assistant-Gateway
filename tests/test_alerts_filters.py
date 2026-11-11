"""Search inside the Alerts page (0.7.46 audit QG-25, QOL-16; one list since 0.7.47): a search
box, a Type filter (each type of alert) and a Show filter (All or Unread), which work without
scripts as a GET. The bell's own box stays whole."""

from __future__ import annotations

from mtg_gateway import alerts_page

from .conftest import Stack, stack_with
from .test_bell_gateway_alerts import _clear_sign_in_alerts, _sub
from .test_companion import COMPANION, NAV, linked_browser

stack_options = stack_with(COMPANION)


async def test_the_alerts_page_filters_by_type_and_by_words(stack: Stack) -> None:
    gw = stack.gw
    b = await linked_browser(stack)
    try:
        sub = _sub(gw)
        await _clear_sign_in_alerts(gw, sub)
        await gw.notifier.send(sub, "price", "Price drop · Sol Ring", "Sol Ring is $9.50.", "/watchlist")
        await gw.notifier.send(sub, "report", "Report ready · Atraxa", "Test finished.", "/history")
        await gw.notifier.send(sub, "summary", "Summary · This week", "Two decks changed.", "/")
        await gw.notifier.drain()
        page = (await b.http.get("/notifications", headers=NAV)).text
        assert "<form class='ui-tools n2' role='search' method='get' action='/notifications'>" in page
        assert "<select name='type' aria-label='Type'>" in page
        assert "<select name='show' aria-label='Show'>" in page
        for value, text in alerts_page.type_choices(admin=False):
            assert f"<option value='{value}'>{text}</option>" in page or (
                value == "" and f"<option value='' selected>{text}</option>" in page
            ), text
        assert "Gateway (admins)" not in page  # an admin's type, offered to admins only
        assert "Price drop · Sol Ring" in page and "Report ready · Atraxa" in page
        # one type
        page = (await b.http.get("/notifications?type=price", headers=NAV)).text
        assert "Price drop · Sol Ring" in page
        assert "Report ready · Atraxa" not in page and "Summary · This week" not in page
        assert "<option value='price' selected>Price alerts</option>" in page
        # words: a title or a line that holds them, any type
        page = (await b.http.get("/notifications?q=week", headers=NAV)).text
        assert "Summary · This week" in page and "Price drop · Sol Ring" not in page
        # the hint, and the shorter one for a box too narrow for it (ui.hint_attrs)
        hint = "placeholder='Search alerts' data-ph='Search'"
        assert f"<input type='search' name='q' value='week' {hint}>" in page
        page = (await b.http.get("/notifications?q=nothing+like+this", headers=NAV)).text
        assert alerts_page.NO_MATCH in page
        # the bell's box is never filtered (the page's script asks for it whole)
        d = (await b.http.get("/notifications?type=price", headers={"Accept": "application/json"})).json()
        assert "Report ready · Atraxa" in d["html"] and "Price drop · Sol Ring" in d["html"]
    finally:
        await b.aclose()
