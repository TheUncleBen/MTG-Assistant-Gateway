"""Archidekt alerts on the member's phone (alerts_push.py, decision C10): for members whose push
notifications are on and whose phone is connected, the gateway asks Archidekt for the alert count
in the background, behind every member's own work and inside their Archidekt budget, and pushes
each new alert once, with a link that opens it in the gateway. Never a repeat, not even after a
restart; never what was there before; one switch on the Notifications card, on by default."""

from __future__ import annotations

import httpx
import pytest

from mtg_gateway import alerts_push, notify, profile_page
from mtg_gateway.alerts_push import AlertWatch

from .conftest import Stack, stack_with
from .test_decks_and_proxy import Browser, linked_user
from .test_notify_app import OPTIONS

stack_options = stack_with(OPTIONS)
SUB = "user-1"
N = "/account/details/notifications"
# the From Archidekt row of the Notifications page, set to Alerts and push (0.7.47)
ARK_PUSH = "<option value='push' selected>Alerts and push</option></select>"
WHO = "alice"


@pytest.fixture(autouse=True)
def _quick(monkeypatch) -> None:
    monkeypatch.setattr(alerts_push, "IDLE_WAIT", 0.3)
    monkeypatch.setattr(alerts_push, "IDLE_POLL", 0.01)
    for d in (profile_page._PROFILE, profile_page._COUNT, profile_page._LIST):
        d.clear()


async def _post(b: Browser, data: dict) -> httpx.Response:
    return await b.http.post("/account", data={"csrf": await b.csrf("/account/details"), **data})


async def _set_up(stack: Stack, *, phone: bool = True) -> tuple[Browser, AlertWatch]:
    """A linked member who turned push notifications on (the alerts box as the card shows it,
    ticked), with their phone connected unless ``phone`` is False."""
    await linked_user(stack)
    b = Browser(stack.h)
    await b.login("/account")
    page = (await b.http.get(N)).text
    assert ARK_PUSH in page  # on by default
    data = {"action": "notify_save", "ntfy_on": "1", "on_proposals": "1", "on_reports": "1", "on_alerts": "1"}
    assert (await _post(b, data)).status_code == 303
    topic = notify.prefs(stack.h.db, SUB)["ntfy_topic"]
    if phone:
        assert stack.gw.notifier.claim(topic)  # the app's connection, as the stream route counts it
    watch = stack.gw.alerts
    assert isinstance(watch, AlertWatch) and watch.interval == alerts_push.POLL_EVERY
    return b, watch


def _note(text: str, kind: str = "ForumThread", seen: bool = False, **ids) -> dict:
    return {"notificationType": kind, "text": text, "seen": seen, **ids}


def _arrive(stack: Stack, *notes: dict) -> None:
    """New alerts on Archidekt, newest first, above what was there."""
    stack.ark.account.notes[WHO] = [*notes, *stack.ark.account.notes.get(WHO, [])]


def _calls(stack: Stack, what: str) -> int:
    return sum(1 for _m, p in stack.ark.calls if p.endswith(f"/{what}/"))


def _pushed(stack: Stack) -> list[dict]:
    return [m for m in stack.ntfy.published if m.get("tags") == ["archidekt"]]


def _path(stack: Stack, i: int) -> str:
    """Where a pushed alert goes on once the Alerts page has marked it read."""
    aid = int(_pushed(stack)[i]["sequence_id"][2:])
    return stack.h.db.gateway_alert(SUB, aid)["path"]


async def _round(watch: AlertWatch) -> None:
    profile_page._COUNT.clear()  # time passes: the bell's kept count has aged out
    await watch.run_round()


async def test_a_new_alert_is_pushed_once_with_a_link_into_the_gateway(stack: Stack) -> None:
    _arrive(stack, _note("old reply", selfId=1), _note("amy followed you", kind="Profile", linkId="amy"))
    b, watch = await _set_up(stack)
    try:
        await _round(watch)  # the first look only notes what is there: nothing old is pushed
        assert _pushed(stack) == [] and _calls(stack, "notifications") == 0
        _arrive(stack, _note("bob replied to “Combo night”", selfId=7))
        await _round(watch)
        (msg,) = _pushed(stack)
        # 0.7.47: the type and what kind of alert, at a glance; Archidekt's own words in its line
        assert msg["title"] == "Archidekt · Forum reply"
        assert msg["message"] == "bob replied to “Combo night”"
        # it opens through the Alerts page (marked read there) and goes on to the thread here
        kept = stack.h.db.gateway_alert(SUB, int(msg["sequence_id"][2:]))
        assert (
            msg["click"].endswith(f"/notifications/open/{kept['id']}")
            and msg["sequence_id"] == f"a-{kept['id']}"
        )
        assert kept["path"] == "/forum/thread/7"
        await _round(watch)
        await _round(watch)
        assert len(_pushed(stack)) == 1  # never again
        _arrive(stack, _note("cat followed you", kind="Profile", linkId="cat"))
        await _round(watch)
        assert _path(stack, -1) == "/users/cat" and len(_pushed(stack)) == 2
        assert _pushed(stack)[-1]["title"] == "Archidekt · Profile"
        # News has no page here: it opens on Archidekt (through the Alerts page, marked read)
        _arrive(stack, _note("Spoiler season", kind="News", linkId=3))
        await _round(watch)
        assert (
            _path(stack, -1) == "https://archidekt.com/news/3"
            and _pushed(stack)[-1]["title"] == "Archidekt · News"
        )
    finally:
        await b.aclose()


async def test_nothing_is_pushed_again_after_a_restart(stack: Stack) -> None:
    b, watch = await _set_up(stack)
    try:
        await _round(watch)
        _arrive(stack, _note("bob replied", selfId=7))
        await _round(watch)
        assert len(_pushed(stack)) == 1
        # a new process: nothing in memory, the phone connects again, the count is still up
        fresh = AlertWatch(stack.gw.decks, stack.gw.notifier)
        await _round(fresh)
        assert len(_pushed(stack)) == 1
        _arrive(stack, _note("amy replied", selfId=8))
        await _round(fresh)
        assert [m["message"] for m in _pushed(stack)] == ["bob replied", "amy replied"]
    finally:
        await b.aclose()


async def test_the_same_words_twice_are_two_alerts(stack: Stack) -> None:
    b, watch = await _set_up(stack)
    try:
        await _round(watch)
        said = "amy commented on “Meren”"
        _arrive(stack, _note(said, kind="DeckThread", linkId=42))
        await _round(watch)
        for n in stack.ark.account.notes[WHO]:
            n["seen"] = True  # read on archidekt.com
        await _round(watch)
        _arrive(stack, _note(said, kind="DeckThread", linkId=42))  # amy comments again
        await _round(watch)
        assert [m["message"] for m in _pushed(stack)] == [said, said]
        assert _path(stack, -1) == "/decks/42"
        await _round(watch)
        assert len(_pushed(stack)) == 2
    finally:
        await b.aclose()


async def test_many_new_alerts_make_one_message(stack: Stack) -> None:
    b, watch = await _set_up(stack)
    try:
        await _round(watch)
        _arrive(stack, *(_note(f"reply {i}", selfId=i) for i in range(5)))
        await _round(watch)
        (msg,) = _pushed(stack)
        assert msg["title"] == "Archidekt · 5 new alerts"
        assert (
            stack.h.db.gateway_alert(SUB, int(msg["sequence_id"][2:]))["path"]
            == "/notifications?type=archidekt"
        )
        # each is on the Alerts page by itself too
        assert len(stack.h.db.gateway_alerts(SUB, kinds=("archidekt",))) == 6
    finally:
        await b.aclose()


async def test_what_the_member_read_in_the_bell_is_not_pushed(stack: Stack) -> None:
    b, watch = await _set_up(stack)
    try:
        await _round(watch)
        _arrive(stack, _note("bob replied", selfId=7))
        r = await b.http.get("/notifications", headers={"Accept": "application/json"})
        assert "bob replied" in r.json()["html"]
        await _round(watch)
        assert _pushed(stack) == []
    finally:
        await b.aclose()


async def test_the_bells_kept_count_saves_a_call(stack: Stack) -> None:
    b, watch = await _set_up(stack)
    try:
        assert (await b.http.get("/notifications/count")).json()["unseen"] == 0
        await watch.run_round()  # the bell's count is a few seconds old: used as it is
        assert _calls(stack, "notificationCount") == 1
        _arrive(stack, _note("bob replied", selfId=7))
        await _round(watch)
        assert _calls(stack, "notificationCount") == 2 and len(_pushed(stack)) == 1
        # and the poll's count fills the bell
        # (a "New assistant" alert from the sign-in counts too: only the Archidekt one is new here)
        others = sum(
            1 for a in stack.h.db.gateway_alerts(SUB) if a["kind"] != "archidekt" and not a["seen_at"]
        )
        assert (await b.http.get("/notifications/count")).json()["unseen"] == 1 + others
        assert _calls(stack, "notificationCount") == 2
    finally:
        await b.aclose()


async def test_no_phone_connected_or_switched_off_asks_archidekt_nothing(stack: Stack) -> None:
    b, watch = await _set_up(stack, phone=False)
    try:
        await _round(watch)
        assert _calls(stack, "notificationCount") == 0
        topic = notify.prefs(stack.h.db, SUB)["ntfy_topic"]
        stack.gw.notifier.claim(topic)
        stack.gw.notifier.release(topic)  # connected a moment ago: still counts
        await _round(watch)
        assert _calls(stack, "notificationCount") == 1
        data = {"action": "notify_save", "ntfy_on": "1", "deliver_archidekt": "list"}  # Alerts only
        await _post(b, data)
        assert notify.prefs(stack.h.db, SUB)["on_alerts"] is False
        page = (await b.http.get(N)).text
        assert "<option value='list' selected>Alerts only</option><option value='push'>" in page
        await _round(watch)
        assert _calls(stack, "notificationCount") == 1
        await _post(b, {"action": "notify_save"})  # push notifications off altogether
        await _round(watch)
        assert _calls(stack, "notificationCount") == 1
    finally:
        await b.aclose()


async def test_turning_alerts_back_on_starts_from_now(stack: Stack) -> None:
    b, watch = await _set_up(stack)
    try:
        await _round(watch)
        await _post(b, {"action": "notify_save", "ntfy_on": "1", "deliver_archidekt": "list"})  # no push
        _arrive(stack, _note("bob replied", selfId=7), _note("amy replied", selfId=8))
        await _post(b, {"action": "notify_save", "ntfy_on": "1", "deliver_archidekt": "push"})
        await _round(watch)
        assert _pushed(stack) == []  # what came while they were off is in the bell, not pushed
        _arrive(stack, _note("cat replied", selfId=9))
        await _round(watch)
        assert [m["message"] for m in _pushed(stack)] == ["cat replied"]
    finally:
        await b.aclose()


async def test_it_steps_aside_for_members_and_keeps_their_budget(stack: Stack, monkeypatch) -> None:
    b, watch = await _set_up(stack)
    try:
        decks = stack.gw.decks
        monkeypatch.setattr(decks, "archidekt_busy", lambda: True)  # a member's work is running
        await _round(watch)
        assert _calls(stack, "notificationCount") == 0
        monkeypatch.undo()
        # the member's budget: a quarter is always left for their own actions
        budget = decks.archidekt_budget
        keep = budget.per_window // 4
        while budget.take(SUB, keep=keep):
            pass
        await _round(watch)
        assert _calls(stack, "notificationCount") == 0
        assert budget.take(SUB)  # the member can still act
    finally:
        await b.aclose()


async def test_a_failing_archidekt_pushes_nothing_and_loses_nothing(stack: Stack) -> None:
    b, watch = await _set_up(stack)
    try:
        await _round(watch)
        _arrive(stack, _note("bob replied", selfId=7))
        orig = stack.ark.transport.handler
        stack.ark.transport.handler = lambda req: httpx.Response(500, json={"detail": "down"})
        await _round(watch)
        assert _pushed(stack) == []
        assert _calls(stack, "notificationCount") <= 2  # sent once, never retried
        stack.ark.transport.handler = orig
        await _round(watch)
        assert [m["message"] for m in _pushed(stack)] == ["bob replied"]
    finally:
        await b.aclose()


async def test_the_card_offers_the_choice_with_a_plain_hint(stack: Stack) -> None:
    b = Browser(stack.h)
    await b.login("/account")
    try:
        card = (await b.http.get(N)).text.split("id='notifications'", 1)[1]
        card = card.split("</section>", 1)[0]
        assert "<label for='nf-archidekt'>From Archidekt</label>" in card
        assert "A comment, a reply, a new follower or news in your Archidekt alerts." in card
    finally:
        await b.aclose()
