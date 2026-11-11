"""The Alerts page as the one place for every alert (0.7.47, the owner's notifications redesign).

Each push names its type and what it is about ("Price drop · Sol Ring"), carries the kind as its
tag (the phone's channel) and opens through the Alerts page, which marks it read. Read and
dismissed are kept on the server, so a reload, another tab or another device shows the same, and
the phone's message is withdrawn once the alert is read or dismissed anywhere (also from the
phone's own Mark read). Each type goes Off, to Alerts only, or to Alerts and push. Notices on
Home can be dismissed for good, and a page's one-time notice is gone after a reload."""

from __future__ import annotations

import httpx

from mtg_gateway import alerts_page, assistants, notify

from .conftest import Stack, stack_with
from .test_decks_and_proxy import Browser, call, linked_user, mcp_token, structured
from .test_notify_app import OPTIONS
from .test_notify_app import _member as _signed_in

stack_options = stack_with(OPTIONS)
SUB = "user-1"
JSON = {"Accept": "application/json"}


async def _member(st: Stack) -> tuple[Browser, str]:
    """A member with push notifications on and every type set to Alerts and push."""
    b, key = await _signed_in(st)
    notify.save_deliveries(st.h.db, SUB, {k: "push" for k in notify.KINDS})
    return b, key


async def _quiet(stack: Stack) -> None:
    """The sign-in's own "New assistant" alert, out of the way."""
    await stack.gw.notifier.drain()
    stack.h.db.dismiss_gateway_alerts(SUB, None)
    stack.h.db.mark_gateway_alerts_seen(SUB)


async def _price(stack: Stack, card: str = "Sol Ring", **kw) -> int:  # noqa: ANN003
    n = stack.gw.notifier
    await n.send(
        SUB,
        "price",
        notify.push_title("price", card, "Price drop"),
        "$12.00 to $9.50.",
        "/watchlist",
        subject=card,
        **kw,
    )
    return stack.h.db.gateway_alerts(SUB, kinds=("price",))[0]["id"]


async def _act(b: Browser, action: str, ids: list[int] | None = None) -> dict:
    csrf = await b.csrf("/account/details")
    r = await b.http.post(
        "/notifications/act", json={"action": action, "ids": ids or []}, headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_each_push_names_its_type_and_subject_and_opens_through_the_alerts_page(stack: Stack) -> None:
    b, key = await _member(stack)
    try:
        await _quiet(stack)
        aid = await _price(stack)
        msg = stack.ntfy.published[-1]
        assert msg["title"] == "Price drop · Sol Ring" and msg["tags"] == ["price"]
        assert msg["click"].endswith(f"/notifications/open/{aid}") and msg["sequence_id"] == f"a-{aid}"
        assert notify.push_title("report", "Atraxa") == "Report ready · Atraxa"
        assert notify.push_title("waiting", "") == "Review"
        assert notify.waiting_title(2, "") == "Review · Unnamed deck"
        # the same alert (one key) is kept and pushed once
        await _price(stack, ukey="price-w1-2026-11-10")
        await _price(stack, ukey="price-w1-2026-11-10")
        assert len(stack.h.db.gateway_alerts(SUB, kinds=("price",))) == 2
        assert len(stack.ntfy.published) == 2
        # opened from the phone: marked read, on to the alert's own page
        r = await b.http.get(f"/notifications/open/{aid}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/watchlist"
        assert stack.h.db.gateway_alert(SUB, aid)["read_at"] is not None
    finally:
        await b.aclose()


async def test_a_link_on_another_site_opens_the_alert_but_marks_nothing(stack: Stack) -> None:
    b, _key = await _member(stack)
    try:
        aid = await _price(stack)
        r = await b.http.get(
            f"/notifications/open/{aid}", headers={"Sec-Fetch-Site": "cross-site"}, follow_redirects=False
        )
        assert r.status_code == 303 and stack.h.db.gateway_alert(SUB, aid)["read_at"] is None
        # someone else's alert, or none: the Alerts page
        r = await b.http.get("/notifications/open/999999", follow_redirects=False)
        assert r.headers["location"] == "/notifications"
    finally:
        await b.aclose()


async def test_read_and_dismissed_follow_the_member_to_every_device_and_leave_the_phone(stack: Stack) -> None:
    b, key = await _member(stack)
    other = Browser(stack.h)
    await other.login("/account")  # a second device
    try:
        await _quiet(stack)
        a1 = await _price(stack, "Sol Ring")
        a2 = await _price(stack, "Mana Crypt")
        d = await _act(b, "read", [a1])
        assert d["changed"] == [a1] and d["message"] == "Marked read."
        await stack.gw.notifier.drain()
        assert stack.ntfy.deleted[-1] == f"/{key}/a-{a1}"
        page = (await other.http.get("/notifications", headers=JSON)).json()
        assert page["unread"] == 1  # the other device sees it read
        await _act(b, "unread", [a1])
        assert stack.h.db.gateway_alert(SUB, a1)["read_at"] is None
        # dismissed: off the list on every device, and Undo brings it back
        await _act(other, "dismiss", [a2])
        html = (await b.http.get("/notifications", headers=JSON)).json()["html"]
        assert "Mana Crypt" not in html and "Sol Ring" in html
        await _act(other, "undo", [a2])
        assert "Mana Crypt" in (await b.http.get("/notifications", headers=JSON)).json()["html"]
        # Mark all read, then Clear all
        d = await _act(b, "read_all")
        assert d["changed"] == [a1]  # a dismissed alert was read with it
        d = await _act(b, "clear_all")
        assert sorted(d["changed"]) == sorted([a1, a2])
        page = await b.http.get("/notifications")
        assert alerts_page.EMPTY.replace("'", "&#x27;") in page.text or "all caught up" in page.text
        # Undo of Clear all: back again
        await _act(b, "undo", [a1, a2])
        assert len(stack.h.db.gateway_alerts(SUB, kinds=("price",))) == 2
    finally:
        await b.aclose()
        await other.aclose()


async def test_the_page_works_without_scripts_and_a_reload_drops_the_notice(stack: Stack) -> None:
    b, _key = await _member(stack)
    try:
        aid = await _price(stack)
        page = await b.http.get("/notifications")
        assert "class='al-menu'" in page.text and "Mark all read" in page.text and "Clear all" in page.text
        csrf = await b.csrf("/account/details")
        r = await b.http.post(
            "/notifications/act",
            data={"csrf": csrf, "action": "read", "ids": str(aid)},
            follow_redirects=False,
        )
        assert r.status_code == 303 and r.headers["location"] == "/notifications?done=read"
        done = await b.http.get("/notifications?done=read")
        assert "Marked read." in done.text and "notice ok once" in done.text
        # the notice is shown once: static/feedback.js takes ?done= off the address
        js = (await b.http.get("/static/feedback.js")).text
        assert '"done"' in js and "location.replace" in js and "back_forward" in js
        # a bad token changes nothing
        r = await b.http.post("/notifications/act", data={"csrf": "x", "action": "dismiss", "ids": str(aid)})
        assert r.status_code == 403 and stack.h.db.gateway_alert(SUB, aid)["dismissed_at"] is None
        r = await b.http.post(
            "/notifications/act", json={"action": "dismiss", "ids": [aid]}, headers={"X-CSRF-Token": "x"}
        )
        assert r.status_code == 403
    finally:
        await b.aclose()


async def test_the_type_and_show_filters_and_search_narrow_the_list(stack: Stack) -> None:
    b, _key = await _member(stack)
    try:
        await _quiet(stack)
        await _price(stack, "Sol Ring")
        await stack.gw.notifier.send(SUB, "report", "Report ready · Atraxa", "Test finished.", "/history")
        page = (await b.http.get("/notifications?type=report")).text
        assert "Report ready · Atraxa" in page and "Sol Ring" not in page
        page = (await b.http.get("/notifications?q=sol")).text
        assert "Sol Ring" in page and "Atraxa" not in page
        await _act(b, "read_all")
        page = (await b.http.get("/notifications?show=unread")).text
        assert alerts_page.NO_MATCH in page
        # the admins' type is not offered to a member
        assert "Gateway (admins)" not in (await b.http.get("/notifications")).text
    finally:
        await b.aclose()


async def test_each_type_goes_off_to_alerts_only_or_to_alerts_and_push(stack: Stack) -> None:
    b, _key = await _member(stack)
    try:
        await _quiet(stack)
        page = (await b.http.get("/account/details/notifications")).text
        for k in notify.KINDS.values():
            if not k.admin:
                assert f"name='deliver_{k.key}'" in page, k.key
        csrf = await b.csrf("/account/details")
        data = {"csrf": csrf, "action": "notify_save", "ntfy_on": "1", "deliver_price": "list"}
        data["deliver_report"] = "off"
        assert (await b.http.post("/account", data=data)).status_code == 303
        await _price(stack)
        assert stack.ntfy.published == [] and len(stack.h.db.gateway_alerts(SUB, kinds=("price",))) == 1
        await stack.gw.notifier.send(SUB, "report", "Report ready · Atraxa", "Done.", "/history")
        assert stack.h.db.gateway_alerts(SUB, kinds=("report",)) == []
        d = notify.deliveries(stack.h.db, SUB)
        assert d["price"] == "list" and d["report"] == "off" and d["archidekt"] == "push"
    finally:
        await b.aclose()


async def test_an_old_unticked_switch_keeps_its_meaning(stack: Stack) -> None:
    b, _key = await _member(stack)
    try:
        notify.save_prefs(stack.h.db, SUB, on_prices=False, on_proposals=False)
        stack.h.db.set_pref(SUB, notify.DELIVER_PREF, None)
        d = notify.deliveries(stack.h.db, SUB, notify._row(stack.h.db, SUB))
        assert d["price"] == "off" and d["waiting"] == "list" and d["report"] == "push"
    finally:
        await b.aclose()


async def test_the_phones_mark_read_reads_the_alert_everywhere(stack: Stack) -> None:
    b, key = await _member(stack)
    try:
        aid = await _price(stack)

        async def post(k: str, body: dict) -> httpx.Response:
            return await stack.h.http.post(
                notify.READ_PATH, json=body, headers={"Authorization": f"Bearer {k}"}
            )

        assert (await post("mtg-nobody-xxxxxxxxxxxxxxxxxxxxxxxx", {"tag": f"a-{aid}"})).status_code == 401
        assert (await post(key, {"tag": "a-999999"})).status_code == 404
        assert (await post(key, {"tag": "deck-1"})).status_code == 404
        r = await post(key, {"tag": f"a-{aid}"})
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert stack.h.db.gateway_alert(SUB, aid)["read_at"] is not None
        await stack.gw.notifier.drain()
        assert f"/{key}/a-{aid}" in stack.ntfy.deleted  # off the member's other phones
    finally:
        await b.aclose()


async def test_admin_alerts_reach_admins_only(stack: Stack) -> None:
    b, _key = await _member(stack)
    try:
        n = stack.gw.notifier
        n.admin_check = lambda sub: False
        await n.send(
            SUB,
            "gateway",
            "Gateway · Update 0.7.48",
            "A newer release is out.",
            "/admin",
            ukey="update-0.7.48",
        )
        assert stack.h.db.gateway_alerts(SUB, kinds=("gateway",)) == []
        n.admin_check = lambda sub: True
        await n.send(
            SUB,
            "gateway",
            "Gateway · Update 0.7.48",
            "A newer release is out.",
            "/admin",
            ukey="update-0.7.48",
        )
        await n.send(
            SUB,
            "gateway",
            "Gateway · Update 0.7.48",
            "A newer release is out.",
            "/admin",
            ukey="update-0.7.48",
        )
        assert len(stack.h.db.gateway_alerts(SUB, kinds=("gateway",))) == 1  # once per release
    finally:
        await b.aclose()


async def test_a_new_assistant_is_one_alert_and_its_home_notice_dismisses_for_good(stack: Stack) -> None:
    b, _key = await _member(stack)
    try:
        await mcp_token(stack.h)  # an assistant connects
        await stack.gw.notifier.drain()
        alerts = stack.h.db.gateway_alerts(SUB, kinds=("account",))
        assert alerts and all(a["title"].startswith("New assistant · ") for a in alerts)
        before = len(alerts)
        await mcp_token(stack.h)  # another connection: its own alert, Home still shows one notice
        await stack.gw.notifier.drain()
        assert len(stack.h.db.gateway_alerts(SUB, kinds=("account",))) >= before
        home = (await b.http.get("/")).text
        assert "data-dismissable" in home and "action='/home/dismiss'" in home
        key = home.split("name='key' value='", 1)[1].split("'", 1)[0]
        csrf = await b.csrf("/account/details")
        r = await b.http.post(
            "/home/dismiss", data={"csrf": csrf, "what": "assistant", "key": key}, follow_redirects=False
        )
        assert r.status_code == 303
        prefix = assistants.alert_prefix(key)
        mine = [a for a in _account(stack)[0] if str(a["ukey"]).startswith(prefix)]
        assert mine and all(a["read_at"] is not None for a in mine)
        assert f"value='{key}'" not in (await b.http.get("/")).text  # gone after a reload too
    finally:
        await b.aclose()


async def test_home_counts_every_unread_alert_in_one_row(stack: Stack) -> None:
    b, _key = await _member(stack)
    try:
        home = (await b.http.get("/")).text
        assert "1 watched card past a price alert" not in home
        assert "data-bell-row" in home and "data-bell-n" in home and "Open alerts" in home
    finally:
        await b.aclose()


async def test_an_assistant_reads_the_gateways_alerts_too(stack: Stack) -> None:
    await linked_user(stack)
    token = await mcp_token(stack.h)
    await _quiet(stack)
    await stack.gw.notifier.send(SUB, "report", "Report ready · Atraxa", "Test finished.", "/history")
    out = structured(await call(stack.h, token, "get_my_notifications"))
    assert out["ok"]
    (mine,) = [a for a in out["gateway_alerts"] if a["type"] == "Deck reports"]
    assert (
        mine["type"] == "Deck reports" and mine["title"] == "Report ready · Atraxa" and mine["read"] is False
    )
    assert "id" not in mine and "path" not in mine


def _account(stack: Stack) -> tuple[list[dict], list[dict]]:
    alerts = stack.h.db.gateway_alerts(SUB, kinds=("account",))
    return alerts, [m for m in stack.ntfy.published if m.get("tags") == ["account"]]


async def test_a_token_refresh_is_never_a_new_assistant(stack: Stack) -> None:
    """The coordinator's 0.7.47 check: an assistant renews its access every hour. Its connection
    is a New assistant alert (and push); a refresh keeps and sends nothing."""
    b, _key = await _member(stack)
    try:
        h = stack.h
        client = await h.register()
        tokens = await h.tokens_for(client)
        await stack.gw.notifier.drain()
        first, pushed = _account(stack)
        assert len(first) >= 1 and len(pushed) == len(first)
        for _ in range(3):
            r = await h.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
            assert r.status_code == 200, r.text
            tokens = r.json()
        await stack.gw.notifier.drain()
        assert _account(stack) == (first, pushed)
    finally:
        await b.aclose()


async def test_a_reconnect_after_a_disconnect_is_a_new_assistant_again(stack: Stack) -> None:
    """0.7.47 audit (security): the alert follows the connection, not the assistant. An assistant
    disconnected and connected again (by the member, or by someone holding their sign-in) is a
    New assistant alert and push again, even after the first one was dismissed."""
    b, _key = await _member(stack)
    try:
        h = stack.h
        client = await h.register()
        await h.tokens_for(client)
        await stack.gw.notifier.drain()
        first, pushed = _account(stack)
        assert first and len(pushed) == len(first)
        h.db.dismiss_gateway_alerts(SUB, [a["id"] for a in first])
        assert h.db.revoke_client_for_user(SUB, client["client_id"]) > 0  # disconnected
        tokens = await h.tokens_for(client)  # connected again
        await stack.gw.notifier.drain()
        again, pushed_again = _account(stack)
        assert len(again) == 1 and again[0]["title"].startswith("New assistant · "), again
        assert again[0]["dismissed_at"] is None and again[0]["read_at"] is None
        assert len(pushed_again) == len(pushed) + 1
        # its refresh still sends nothing
        r = await h.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
        assert r.status_code == 200, r.text
        await stack.gw.notifier.drain()
        assert _account(stack) == (again, pushed_again)
    finally:
        await b.aclose()
