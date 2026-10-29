"""The Android app's notifications (notify.py): the stream route the app keeps open on the
gateway's own address, relaying the member's topic from the stack's ntfy server, its refusals, the
filtering of ntfy's events, kicks when a member switches off or disconnects their phones, the
gateway's stop, grouped messages per deck and the Account card inside the app.

The gateway is driven as a raw ASGI app so each streamed line can be read as it is sent (httpx's
ASGI transport waits for the whole body), and ntfy is a fake whose stream the test feeds."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from mtg_gateway import notify
from mtg_gateway.db import Database
from mtg_gateway.membership import Membership

from .conftest import Stack, StackOptions, stack_with
from .test_decks_and_proxy import Browser, call, mcp_token, structured

TOKEN = "tk_secret_ntfy_token_value"
APP_UA = "Mozilla/5.0 (Linux; Android 16; wv) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.7.30"


class FakeNtfy:
    """ntfy as the gateway sees it: publishes and deletes are recorded, and each subscription
    (``GET /{topic}/json``) streams the lines the test puts in its queue (None ends it)."""

    def __init__(self) -> None:
        self.published: list[dict] = []
        self.deleted: list[str] = []
        self.subscribed: list[dict[str, Any]] = []
        self.feeds: list[asyncio.Queue[str | None]] = []
        self.status = 200

    async def handler(self, req: httpx.Request) -> httpx.Response:
        if req.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(403)
        if self.status != 200:
            return httpx.Response(self.status)
        if req.method == "POST":
            self.published.append(json.loads(req.content))
            return httpx.Response(200, json={"id": "m1", "event": "message"})
        if req.method == "DELETE":
            self.deleted.append(req.url.path)
            return httpx.Response(200, json={"event": "message_delete"})
        feed: asyncio.Queue[str | None] = asyncio.Queue()
        self.feeds.append(feed)
        self.subscribed.append({"path": req.url.path, "params": dict(req.url.params)})

        async def lines() -> AsyncIterator[bytes]:
            while (line := await feed.get()) is not None:
                yield (line + "\n").encode()

        return httpx.Response(200, content=lines())

    def say(self, **event: Any) -> None:
        """One line on the newest subscription."""
        self.feeds[-1].put_nowait(json.dumps(event))


def _with_ntfy(st: Stack) -> None:
    st.ntfy = FakeNtfy()
    st.gw.notifier._http = httpx.AsyncClient(transport=httpx.MockTransport(st.ntfy.handler))


OPTIONS = StackOptions(
    settings={"writes_enabled": True, "ntfy_url": "https://ntfy.test", "ntfy_token": TOKEN},
    started=_with_ntfy,
)
stack_options = stack_with(OPTIONS)


class Stream:
    """One app connection to the stream route, driven as raw ASGI: ``status``, ``headers`` and
    each line as the gateway sends it."""

    def __init__(self, st: Stack, key: str | None, since: str = "") -> None:
        self.sent: asyncio.Queue[dict] = asyncio.Queue()
        self.gone = asyncio.Event()
        headers = [(b"host", b"mtg.test"), (b"user-agent", APP_UA.encode())]
        if key is not None:
            headers.append((b"authorization", f"Bearer {key}".encode()))
        self.scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": notify.STREAM_PATH,
            "raw_path": notify.STREAM_PATH.encode(),
            "query_string": f"since={since}".encode() if since else b"",
            "root_path": "",
            "headers": headers,
            "client": ("10.0.0.5", 40000),
            "server": ("mtg.test", 443),
        }
        self._asked = False
        self.task = asyncio.ensure_future(st.h.app(self.scope, self._receive, self.sent.put))
        self.status = 0
        self.headers: dict[str, str] = {}
        self._buf = b""

    async def _receive(self) -> dict:
        if not self._asked:
            self._asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self.gone.wait()
        return {"type": "http.disconnect"}

    async def start(self) -> Stream:
        msg = await asyncio.wait_for(self.sent.get(), 5)
        assert msg["type"] == "http.response.start"
        self.status = msg["status"]
        self.headers = {k.decode().lower(): v.decode() for k, v in msg["headers"]}
        return self

    async def body(self) -> bytes:
        """The whole body of a refusal."""
        out = b""
        while True:
            msg = await asyncio.wait_for(self.sent.get(), 5)
            out += msg.get("body", b"")
            if not msg.get("more_body"):
                return out

    async def line(self, timeout: float = 5) -> dict | None:
        """The next line, or None once the gateway ended the stream."""
        while b"\n" not in self._buf:
            msg = await asyncio.wait_for(self.sent.get(), timeout)
            self._buf += msg.get("body", b"")
            if not msg.get("more_body") and b"\n" not in self._buf:
                return None
        raw, self._buf = self._buf.split(b"\n", 1)
        return json.loads(raw)

    async def ended(self) -> bool:
        """True once the gateway closed the stream (lines still on the way are skipped)."""
        while await self.line() is not None:
            pass
        await asyncio.wait_for(self.task, 5)
        return True

    async def close(self) -> None:
        self.gone.set()
        await asyncio.wait_for(asyncio.gather(self.task, return_exceptions=True), 5)


async def _member(st: Stack, *, app: bool = False) -> tuple[Browser, str]:
    """A signed-in member with notifications on, and their app's key (their topic)."""
    b = Browser(st.h)
    await b.login("/account")  # in a browser: the app signs in through one too
    if app:
        b.http.headers["user-agent"] = APP_UA
    data = {"action": "notify_save", "ntfy_on": "1", "on_proposals": "1", "on_reports": "1"}
    r = await b.http.post("/account", data={"csrf": await b.csrf("/account/details"), **data})
    assert r.status_code == 303
    return b, notify.prefs(st.h.db, "user-1")["ntfy_topic"]


async def _post(b: Browser, data: dict) -> httpx.Response:
    return await b.http.post("/account", data={"csrf": await b.csrf("/account/details"), **data})


async def _refusal(st: Stack, key: str | None) -> tuple[int, dict]:
    s = await Stream(st, key).start()
    return s.status, json.loads(await s.body())


# -- refusals -------------------------------------------------------------------------------------


async def test_the_stream_refuses_keys_that_are_nobodys_and_members_who_are_off(stack: Stack) -> None:
    b, key = await _member(stack)
    try:
        assert notify.STRONG_TOPIC_RE.fullmatch(key)
        for bad in (None, "", "mtg-short-topic-1234", "mtg-" + "x" * 22, key + "x"):
            status, body = await _refusal(stack, bad)
            assert (status, body["error"]) == (401, "unknown_key")
        # a member who switched notifications off
        await _post(b, {"action": "notify_save"})
        status, body = await _refusal(stack, key)
        assert (status, body["error"]) == (403, "off")
        assert stack.ntfy.subscribed == []  # ntfy was never asked
    finally:
        await b.aclose()


async def test_the_stream_refuses_removed_members_too_many_connections_and_a_silent_ntfy(
    stack: Stack, monkeypatch
) -> None:
    b, key = await _member(stack)
    try:
        checks = iter([Membership.REVOKED, Membership.UNAVAILABLE, Membership.SIGN_IN])

        async def check(sub: str) -> Membership:
            return next(checks, Membership.ALLOWED)

        monkeypatch.setattr(stack.gw.membership, "check", check)
        assert (await _refusal(stack, key))[0] == 401
        assert (await _refusal(stack, key))[1]["error"] == "unavailable"
        # none of the member's sign-ins honoured: refused, but not a stop code (Push.kt), so the
        # phone keeps its key and tries again until they sign in again (C2)
        status, body = await _refusal(stack, key)
        assert (status, body["error"]) == (403, "sign_in_again")
        stack.gw.notifier._streams[key] = notify.STREAM_MAX_PER_KEY
        status, body = await _refusal(stack, key)
        assert (status, body["error"]) == (429, "too_many")
        # the whole gateway's cap: every other member's phones together
        stack.gw.notifier._streams = {f"mtg-other{i:04d}": 1 for i in range(notify.STREAM_MAX_TOTAL)}
        status, body = await _refusal(stack, key)
        assert (status, body["error"]) == (503, "busy")
        stack.gw.notifier._streams.clear()
        stack.ntfy.status = 500
        status, body = await _refusal(stack, key)
        assert (status, body["error"]) == (503, "unavailable")
        assert stack.gw.notifier.streams(key) == 0  # a refused connection is not counted
    finally:
        await b.aclose()


async def test_no_stream_without_a_notification_server(tmp_path, idp) -> None:
    from .conftest import open_stack

    async with open_stack(tmp_path, idp, StackOptions(settings={"writes_enabled": True})) as st:
        status, body = await _refusal(st, "mtg-" + "a" * 22)
        assert (status, body["error"]) == (404, "not_set_up")


# -- the relay ------------------------------------------------------------------------------------


async def test_the_app_gets_its_messages_filtered_once_each_with_keepalives(
    stack: Stack, monkeypatch
) -> None:
    monkeypatch.setattr(notify, "STREAM_KEEPALIVE", 0.2)
    b, key = await _member(stack)
    s = Stream(stack, key, since="AbCdEf123456")
    try:
        await s.start()
        assert s.status == 200
        assert s.headers["content-type"].startswith("application/x-ndjson")
        assert s.headers["x-accel-buffering"] == "no" and s.headers["cache-control"] == "no-store"
        sub = stack.ntfy.subscribed[-1]
        assert sub == {"path": f"/{key}/json", "params": {"since": "AbCdEf123456"}}
        stack.ntfy.say(id="o1", time=1, event="open", topic=key)
        assert (await s.line()) == {"id": "o1", "time": 1, "event": "open"}
        msg = {
            "id": "m1",
            "time": 2,
            "event": "message",
            "topic": key,
            "sequence_id": "deck-42",
            "title": "2 changes to Atraxa are waiting for you",
            "message": "Open the review page to look at them.",
            "click": "https://mtg.test/proposals",
            "tags": ["memo"],
            "priority": 3,
            "attachment": {"url": "https://elsewhere.test/x"},
        }
        stack.ntfy.say(**msg)
        stack.ntfy.say(**msg)  # ntfy can send one twice after since=
        stack.ntfy.say(id="p1", time=3, event="poll_request", topic=key)  # not for the app
        stack.ntfy.say(id="d1", time=4, event="message_delete", topic=key, sequence_id="deck-42")
        got = await s.line()
        assert got == {k: msg[k] for k in notify.STREAM_FIELDS}
        assert (await s.line())["event"] == "message_delete"
        # quiet ntfy: the gateway's own keepalive keeps proxies from closing the connection
        assert (await s.line())["event"] == "keepalive"
        assert stack.gw.notifier.streams(key) == 1
    finally:
        await s.close()
        await b.aclose()
    assert stack.gw.notifier.streams(key) == 0


async def test_a_bad_since_starts_from_now(stack: Stack) -> None:
    b, key = await _member(stack)
    s = Stream(stack, key, since="../../etc")
    try:
        await s.start()
        assert stack.ntfy.subscribed[-1]["params"] == {}
    finally:
        await s.close()
        await b.aclose()


async def test_switching_off_or_disconnecting_phones_ends_the_stream(stack: Stack) -> None:
    b, key = await _member(stack)
    try:
        s = await Stream(stack, key).start()
        await _post(b, {"action": "notify_save"})  # off
        assert await s.ended()
        assert (await _refusal(stack, key))[0] == 403
        await _post(b, {"action": "notify_save", "ntfy_on": "1"})
        assert notify.prefs(stack.h.db, "user-1")["ntfy_topic"] == key  # on again: same phones
        s = await Stream(stack, key).start()
        await _post(b, {"action": "notify_new_topic"})  # Disconnect my phones
        assert await s.ended()
        assert (await _refusal(stack, key))[0] == 401
        assert stack.gw.notifier.streams(key) == 0
    finally:
        await b.aclose()


async def test_a_change_made_elsewhere_ends_the_stream_at_the_next_check(stack: Stack, monkeypatch) -> None:
    monkeypatch.setattr(notify, "STREAM_KEEPALIVE", 0.05)
    monkeypatch.setattr(notify, "STREAM_RECHECK", 0.1)
    b, key = await _member(stack)
    try:
        s = await Stream(stack, key).start()
        notify.save_prefs(stack.h.db, "user-1", ntfy_on=False)  # another gateway process, a restore
        assert await s.ended()
    finally:
        await b.aclose()


async def test_a_member_removed_at_the_identity_provider_is_cut_off_at_the_next_check(
    stack: Stack, monkeypatch
) -> None:
    monkeypatch.setattr(notify, "STREAM_KEEPALIVE", 0.05)
    monkeypatch.setattr(notify, "STREAM_RECHECK", 0.1)
    b, key = await _member(stack)
    try:
        outcome = Membership.ALLOWED

        async def check(sub: str) -> Membership:
            return outcome

        monkeypatch.setattr(stack.gw.membership, "check", check)
        s = await Stream(stack, key).start()
        assert s.status == 200
        await asyncio.sleep(0.3)  # several checks while still allowed: still open
        assert stack.gw.notifier.streams(key) == 1
        outcome = Membership.UNAVAILABLE  # the identity provider not answering cuts nobody off
        await asyncio.sleep(0.3)
        assert stack.gw.notifier.streams(key) == 1
        outcome = Membership.SIGN_IN  # no sign-in of theirs honoured any more: it ends too (C2)
        assert await s.ended()
        assert stack.gw.notifier.streams(key) == 0
        outcome = Membership.ALLOWED
        s = await Stream(stack, key).start()
        assert s.status == 200
        outcome = Membership.REVOKED
        assert await s.ended()
        assert stack.gw.notifier.streams(key) == 0
    finally:
        await b.aclose()


async def test_connections_made_at_once_still_stop_at_the_cap(stack: Stack) -> None:
    b, key = await _member(stack)
    try:
        many = [Stream(stack, key) for _ in range(notify.STREAM_MAX_PER_KEY + 2)]
        statuses = sorted([(await s.start()).status for s in many])
        assert statuses == [200] * notify.STREAM_MAX_PER_KEY + [429, 429]
        assert stack.gw.notifier.streams(key) == notify.STREAM_MAX_PER_KEY
        for s in many:
            await s.close()
        assert stack.gw.notifier.streams(key) == 0
    finally:
        await b.aclose()


async def test_a_client_gone_before_the_stream_starts_frees_its_connection(stack: Stack) -> None:
    b, key = await _member(stack)
    try:
        refused = Stream(stack, None)  # only for its request; refused at once without a key
        await refused.close()
        scope = {
            **refused.scope,
            "headers": [*refused.scope["headers"], (b"authorization", f"Bearer {key}".encode())],
        }
        stuck = asyncio.Event()
        asked = False

        async def receive() -> dict:
            nonlocal asked
            if not asked:
                asked = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await stuck.wait()  # the reply's start is being written...
            return {"type": "http.disconnect"}  # ...when the phone goes away

        async def send(msg: dict) -> None:
            if msg["type"] == "http.response.start":
                stuck.set()
                await asyncio.sleep(3600)  # a socket that never takes the reply

        task = asyncio.ensure_future(stack.h.app(scope, receive, send))
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
        assert stack.gw.notifier.streams(key) == 0  # counted, then given back
    finally:
        await b.aclose()


async def test_a_silent_or_closed_ntfy_ends_the_stream(stack: Stack, monkeypatch) -> None:
    monkeypatch.setattr(notify, "STREAM_KEEPALIVE", 0.05)
    monkeypatch.setattr(notify, "STREAM_STALE", 0.2)
    b, key = await _member(stack)
    try:
        s = await Stream(stack, key).start()
        assert await s.ended()  # nothing from ntfy for STREAM_STALE seconds
        s = await Stream(stack, key).start()
        stack.ntfy.feeds[-1].put_nowait(None)  # ntfy closed it
        assert await s.ended()
    finally:
        await b.aclose()


async def test_the_gateway_stopping_ends_every_stream(stack: Stack) -> None:
    b, key = await _member(stack)
    try:
        s1 = await Stream(stack, key).start()
        s2 = await Stream(stack, key).start()
        assert stack.gw.notifier.streams(key) == 2
        stack.gw.notifier.close_streams()
        assert await s1.ended() and await s2.ended()
        assert stack.gw.notifier.streams(key) == 0
    finally:
        await b.aclose()


# -- grouped messages -----------------------------------------------------------------------------


async def test_a_group_replaces_its_message_and_withdraw_removes_it(stack: Stack) -> None:
    b, key = await _member(stack)
    try:
        n = stack.gw.notifier
        assert await n.send(
            "user-1", "proposals", "2 changes to Atraxa are waiting for you", "Look.", "/p", group="deck-42"
        )
        assert stack.ntfy.published[-1]["sequence_id"] == "deck-42"
        assert await n.send("user-1", "reports", "Deck report finished", "Done.", "/r", group="../x")
        assert "sequence_id" not in stack.ntfy.published[-1]  # not a group the gateway makes
        assert await n.withdraw("user-1", "deck-42")
        assert stack.ntfy.deleted == [f"/{key}/deck-42"]
        assert not await n.withdraw("user-1", "deck-x")
        # withdraw ignores the member's event choices: a message never outlives its cause
        await _post(b, {"action": "notify_save", "ntfy_on": "1"})  # proposals unticked
        assert not await n.send("user-1", "proposals", "t", "m", group="deck-42")
        assert await n.withdraw("user-1", "deck-42")
    finally:
        await b.aclose()


# -- the Account card in the app ------------------------------------------------------------------


async def test_the_card_in_the_app_connects_this_phone(stack: Stack) -> None:
    b, key = await _member(stack, app=True)
    try:
        r = await _post(b, {"action": "notify_save", "ntfy_on": "1", "on_proposals": "1"})
        assert r.headers["location"] == "/account/details?ok=notify_saved&connect=1#notifications"
        page = (await b.http.get("/account/details?ok=notify_saved&connect=1")).text
        assert f"data-native='startNotifications' data-native-arg='{key}' data-native-auto" in page
        assert f"data-native-state='notificationsState' data-native-arg='{key}'" in page
        assert "Get push notifications on this phone" in page and "Update the app" in page
        assert "/install?for=android" not in page
        # a topic a member chose before 0.7.30 is replaced: only gateway-made topics are keys
        notify.save_prefs(stack.h.db, "user-1", ntfy_topic="mtg-my-own-topic-1234")
        page = (await b.http.get("/account/details")).text
        new = notify.prefs(stack.h.db, "user-1")["ntfy_topic"]
        assert notify.STRONG_TOPIC_RE.fullmatch(new) and f"data-native-arg='{new}'" in page
        assert "mtg-my-own-topic-1234" not in page
        # off: no phone part and no Send test
        r = await _post(b, {"action": "notify_save"})
        assert r.headers["location"] == "/account/details?ok=notify_off#notifications"
        page = (await b.http.get("/account/details")).text
        assert "startNotifications" not in page and "Send test" not in page
    finally:
        await b.aclose()


async def test_the_card_outside_the_app_says_to_tick_and_save_in_the_app(stack: Stack) -> None:
    """Outside the app the card's hint names the steps in order: in the app, tick Send me
    notifications and press Save push notifications (saving there connects the phone). It used to say
    only "press Get push notifications on this phone", a button that shows once notifications are on.
    The summary's choice is labelled How often, not as if it were a button that sends it."""
    b, _key = await _member(stack, app=False)
    try:
        page = (await b.http.get("/account/details")).text
        assert (
            "open this page in the app, tick Send me push notifications and press Save push notifications."
        ) in page
        assert "and press Get push notifications on this phone" not in page
        assert "<label for='s-every'>How often</label>" in page and "Send my summary" not in page
    finally:
        await b.aclose()


def test_every_topic_the_gateway_didnt_make_is_replaced(tmp_path) -> None:
    """A topic a member typed in 0.7.26 can have the same form as one the gateway makes (mtg- and
    22 characters), so the form can't tell them apart: only the gateway's mark can. Each one stored
    before the upgrade is replaced, and a key made after it is kept."""
    db = Database(tmp_path / "gw.sqlite")
    try:
        for sub in ("typed", "short"):
            db.upsert_user(sub, email=None, name=None, preferred_username=None, groups=[])
        typed = "mtg-" + "MyOwnTopicIsTwentyTwoC"  # typed by the member, in the new keys' form
        assert notify.STRONG_TOPIC_RE.fullmatch(typed) and not notify.strong_topic(db, "typed", typed)
        notify.save_prefs(db, "typed", ntfy_on=True, ntfy_topic=typed)
        notify.save_prefs(db, "short", ntfy_topic="mtg-my-own-topic")
        assert notify.replace_old_topics(db) == 2
        after = {sub: notify.prefs(db, sub)["ntfy_topic"] for sub in ("typed", "short")}
        assert typed not in after.values() and "mtg-my-own-topic" not in after.values()
        assert all(notify.strong_topic(db, sub, t) for sub, t in after.items())
        assert not notify.strong_topic(db, "short", after["typed"])  # the mark is each member's own
        assert notify.prefs(db, "typed")["ntfy_on"] is True  # only the topic changes
        # the next start: a key made after the upgrade stays
        assert notify.replace_old_topics(db) == 0
        assert {sub: notify.prefs(db, sub)["ntfy_topic"] for sub in ("typed", "short")} == after
    finally:
        db.close()


def test_the_marks_key_is_read_once_and_kept_in_memory(tmp_path) -> None:
    """A phone connecting checks its topic's mark; after the first read that takes no write
    transaction on the database."""
    db = Database(tmp_path / "gw.sqlite")
    try:
        db.upsert_user("u", email=None, name=None, preferred_username=None, groups=[])
        topic = notify.new_topic(db, "u")
        opened: list[int] = []
        real_tx = db.tx

        def counting_tx(*a, **k):
            opened.append(1)
            return real_tx(*a, **k)

        db.tx = counting_tx  # type: ignore[method-assign]
        for _ in range(3):
            assert notify.strong_topic(db, "u", topic)
        assert opened == []
    finally:
        db.close()


async def test_a_topic_typed_while_an_older_version_ran_is_never_a_key(stack: Stack) -> None:
    """Rolled back to 0.7.29 after the update, a member can type a topic of the keys' form again.
    The stream refuses it at once, the Account page in the app doesn't hand it to the phone, and
    the next start replaces it."""
    b, key = await _member(stack, app=True)
    try:
        db = stack.h.db
        typed = "mtg-" + "a" * 22
        notify.save_prefs(db, "user-1", ntfy_on=True, ntfy_topic=typed)  # what 0.7.29's notify_save does
        status, body = await _refusal(stack, typed)
        assert (status, body["error"]) == (401, "unknown_key")
        assert notify.replace_old_topics(db) == 1
        new = notify.prefs(db, "user-1")["ntfy_topic"]
        assert new not in (typed, key) and notify.strong_topic(db, "user-1", new)
        page = (await b.http.get("/account/details")).text
        assert typed not in page and f"data-native-arg='{new}'" in page
    finally:
        await b.aclose()


async def test_a_key_made_after_the_upgrade_still_connects_after_a_restart(stack: Stack) -> None:
    b, key = await _member(stack, app=True)
    try:
        assert notify.replace_old_topics(stack.h.db) == 0  # what the next start runs
        assert notify.prefs(stack.h.db, "user-1")["ntfy_topic"] == key
        s = await Stream(stack, key).start()
        assert s.status == 200
        await s.close()
    finally:
        await b.aclose()


@pytest.mark.parametrize("app", [False, True])
async def test_the_token_is_never_on_the_page(stack: Stack, app: bool) -> None:
    b, _ = await _member(stack, app=app)
    try:
        page = (await b.http.get("/account/details")).text
        assert TOKEN not in page and "ntfy.test" not in page
    finally:
        await b.aclose()


async def test_one_message_per_deck_follows_its_waiting_count(stack: Stack) -> None:
    b, key = await _member(stack)
    try:
        n = stack.gw.notifier
        n.deck_waiting("user-1", "42", "Atraxa, Grand  Unifier", 1, "/decks/42")
        await n.drain()
        msg = stack.ntfy.published[-1]
        assert msg["title"] == "1 change to Atraxa, Grand Unifier is waiting for you"
        assert msg["sequence_id"] == "deck-42" and msg["click"] == "https://mtg.test/decks/42"
        assert msg["message"] == notify.WAITING_TEXT
        n.deck_waiting("user-1", "42", "Atraxa, Grand Unifier", 1, "/decks/42")  # no change: nothing sent
        n.deck_waiting("user-1", "42", "Atraxa, Grand Unifier", 2, "/decks/42")
        await n.drain()
        assert (
            len(stack.ntfy.published) == 2
            and stack.ntfy.published[-1]["title"] == "2 changes to Atraxa, Grand Unifier are waiting for you"
        )
        n.deck_waiting("user-1", "42", "Atraxa, Grand Unifier", 0, "/decks/42")
        await n.drain()
        assert stack.ntfy.deleted == [f"/{key}/deck-42"]
        n.deck_waiting("user-1", "x", "Odd", 3, "/decks/x")  # not a deck id: nothing
        await n.drain()
        assert len(stack.ntfy.published) == 2
        assert notify.waiting_title(3, "") == "3 changes to your deck are waiting for you"
    finally:
        await b.aclose()


async def test_each_decks_message_follows_its_proposals(stack: Stack) -> None:
    """Saved, rejected, a mode that lets the assistant apply, and a restart: the deck's one
    message follows what really waits for the member's press."""
    b, key = await _member(stack)
    n = stack.gw.notifier
    n.recount_delay = 0.01
    try:
        r = await b.link("alice", "pw-alice")
        assert r.status_code == 303, r.text
        token = await mcp_token(stack.h)

        async def propose(card: str) -> dict:
            p = structured(
                await call(
                    stack.h,
                    token,
                    "propose_deck_changes",
                    {"deck_id": "42", "changes": [{"action": "add", "card_name": card}], "separate": True},
                )
            )
            assert p["state"] == "pending" and p["assistant_may_apply"] is False  # manual mode
            await n.drain()
            return p

        p1 = await propose("Arcane Signet")
        msg = stack.ntfy.published[-1]
        assert msg["title"] == notify.waiting_title(1, p1["deck_name"])
        assert (
            msg["sequence_id"] == "deck-42"
            and msg["click"] == f"https://mtg.test/proposals/{p1['proposal_id']}"
        )
        assert "Arcane Signet" not in json.dumps(msg)
        p2 = await propose("Sol Ring")
        msg = stack.ntfy.published[-1]
        assert (
            msg["title"] == notify.waiting_title(2, p1["deck_name"])
            and msg["click"] == "https://mtg.test/decks/42"
        )
        sent = len(stack.ntfy.published)
        await call(stack.h, token, "reject_proposal", {"proposal_id": p2["proposal_id"]})
        await n.drain()
        assert len(stack.ntfy.published) == sent + 1
        assert stack.ntfy.published[-1]["title"] == notify.waiting_title(1, p1["deck_name"])
        # a mode that lets the assistant apply it: nothing waits for the member any more
        assert stack.h.db.set_approval_mode("user-1", "auto")
        await n.drain()
        assert stack.ntfy.deleted == [f"/{key}/deck-42"]
        assert stack.h.db.set_approval_mode("user-1", None)
        await n.drain()
        assert stack.ntfy.published[-1]["title"] == notify.waiting_title(1, p1["deck_name"])
        await call(stack.h, token, "reject_proposal", {"proposal_id": p1["proposal_id"]})
        await n.drain()
        assert stack.ntfy.deleted == [f"/{key}/deck-42"] * 2
        # after a restart the gateway no longer knows what it sent: a deck whose proposals change
        # is withdrawn at 0 all the same
        n.proposals_changed("user-1", "42")
        await n.drain()
        assert stack.ntfy.deleted == [f"/{key}/deck-42"] * 2  # withdrawn already: not again
        n._waiting.clear()
        n._cleared.clear()
        n.proposals_changed("user-1", "42")
        await n.drain()
        assert stack.ntfy.deleted == [f"/{key}/deck-42"] * 3
        assert n._waiting == {}
        assert stack.gw.decks.waiting_by_deck("user-1") == {}
    finally:
        await b.aclose()


async def test_an_admins_approval_setting_brings_each_decks_message_in_step(stack: Stack) -> None:
    """The admin's approval settings decide what waits for a member's press, and changing one
    writes no proposal: the deck's message follows it both ways, after a restart too."""
    b, key = await _member(stack)
    n = stack.gw.notifier
    n.recount_delay = 0.01
    live = stack.gw.decks.live
    try:
        r = await b.link("alice", "pw-alice")
        assert r.status_code == 303, r.text
        token = await mcp_token(stack.h)
        p = structured(
            await call(
                stack.h,
                token,
                "propose_deck_changes",
                {"deck_id": "42", "changes": [{"action": "add", "card_name": "Arcane Signet"}]},
            )
        )
        assert p["state"] == "pending" and p["assistant_may_apply"] is False  # the default: manual
        await n.drain()
        assert stack.ntfy.published[-1]["title"] == notify.waiting_title(1, p["deck_name"])
        # the default becomes auto: the assistant may apply it, so nothing waits for the member
        live.set("approval_mode_default", "auto", by="admin-1")
        await n.drain()
        assert stack.ntfy.deleted == [f"/{key}/deck-42"]
        assert stack.gw.decks.waiting_by_deck("user-1") == {}
        # the cap comes down to manual: it waits for the member's press again
        sent = len(stack.ntfy.published)
        live.set("approval_mode_max", "manual", by="admin-1")
        await n.drain()
        assert len(stack.ntfy.published) == sent + 1
        assert stack.ntfy.published[-1]["title"] == notify.waiting_title(1, p["deck_name"])
        # after a restart the gateway no longer knows what it sent: withdrawn all the same
        n._waiting.clear()
        n._cleared.clear()
        live.set("approval_mode_max", "auto", by="admin-1")
        await n.drain()
        assert stack.ntfy.deleted == [f"/{key}/deck-42"] * 2
        # a setting that decides nothing about waiting changes no message
        sent = len(stack.ntfy.published)
        live.set("snapshots_per_deck", 30, by="admin-1")
        await n.drain()
        assert len(stack.ntfy.published) == sent and len(stack.ntfy.deleted) == 2
    finally:
        await b.aclose()


async def test_drain_returns_when_a_send_finished_in_the_same_turn() -> None:
    """A finished task leaves the set only in its done callback, a loop turn later: drain called
    right then must still return (it spun for ever, at shutdown too)."""
    n = notify.Notifier(None, None, None, "https://mtg.test")  # type: ignore[arg-type]

    async def done_now() -> None:
        return None

    n._background(done_now())
    await asyncio.sleep(0)  # the task runs and finishes; its done callback is only scheduled
    assert n._tasks and all(t.done() for t in n._tasks)
    await asyncio.wait_for(n.drain(), 2)
    assert not n._tasks
