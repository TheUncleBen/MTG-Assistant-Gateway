"""The account-side screens of parity release 2 (profile_page.py): on Account the Archidekt profile
(username, plain-text bio, the picture box), the email and notification switches, the new-device
default settings and the forms that stay on Archidekt; the Collection page's Visibility line
(public asks once more); the bell's count and list routes, read after the page has loaded and
kept a short while; and the Home widget. Every change is the member's own press through a plain
form (works without scripts), the same proposal an assistant makes, which an assistant can never
apply; the picture never comes from an assistant at all."""

from __future__ import annotations

import html
import io
import re

import pytest

from mtg_gateway import profile, profile_page

from .conftest import Stack, aged_stamp, stack_with
from .test_approval_modes import SHIPPED
from .test_decks_and_proxy import Browser, call, linked_user, structured

stack_options = stack_with(SHIPPED)
WHO = "alice"


def _png(size: tuple[int, int] = (64, 64), fmt: str = "PNG") -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(out, format=fmt)
    return out.getvalue()


async def _member(stack: Stack) -> tuple[Browser, str]:
    """A linked member's browser, signed in, and an assistant's token for the same account."""
    token = await linked_user(stack)
    b = Browser(stack.h)
    await b.login("/account")
    return b, token


@pytest.fixture
async def member(stack: Stack):
    profile_page.forget_all("user-1")
    for d in (profile_page._PROFILE, profile_page._COUNT, profile_page._LIST):
        d.clear()
    b, token = await _member(stack)
    b.token = token  # type: ignore[attr-defined]
    try:
        yield b
    finally:
        await b.aclose()


def _stamp(b: Browser) -> str:
    """The page's render stamp, old enough: the press is refused without one (clickguard)."""
    return aged_stamp(b.h.settings, "press:user-1")


async def _post(b: Browser, path: str = "/account/profile", **data):
    data.setdefault("csrf", await b.csrf("/account"))
    data.setdefault("shown", _stamp(b))
    return await b.http.post(path, data=data)


def _said(text: str, words: str) -> bool:
    """The words as the page carries them: escaped (ui helpers) or as written (markup)."""
    return words in text or html.escape(words) in text


def _card(page: str, card_id: str) -> str:
    return page.split(f"id='{card_id}'", 1)[1].split("</div><div class='card'", 1)[0]


# -- the Account cards --------------------------------------------------------------------------------


async def test_the_four_cards_follow_the_mockup_while_linked(member: Browser) -> None:
    page = (await member.http.get("/account")).text
    for h in (
        "<h2>Archidekt profile</h2>",
        "<h2>Email and notifications on Archidekt</h2>",
        "<h2>New device default settings</h2>",
        "<h2>On Archidekt only</h2>",
    ):
        assert h in page, h
    prof = _card(page, "profile")
    assert "Your own JPEG or PNG up to 1 MB. Only you can change it, here." in prof
    assert "Shown on your decks and comments on Archidekt. Archidekt says if a name is taken." in prof
    assert "value='alice' maxlength='150'" in prof
    # the bio is plain text on Archidekt's side here: a textarea, no formatting toolbar, no presets
    assert "<textarea id='pr-bio'" in prof and "role='toolbar'" not in prof and "Preset" not in prof
    assert (
        "Save profile</button>" in prof
        and "A copy of your profile from just before is kept under History." in prof
    )
    assert "Upload a picture…" in prof and "data-box-open='picture'" in prof
    box = page.split("id='picture' role='dialog'", 1)[1]
    assert "Change your Archidekt profile picture" in page
    assert "Choose a JPEG or PNG up to 1 MB. The gateway checks the type and size" in box
    assert _said(box, "an assistant can't change your picture.")
    assert "accept='image/jpeg,image/png'" in box and "Use this picture" in box
    mail = _card(page, "email")
    for label, help_text in (
        ("Other members can contact me", "Lets members message you through Archidekt."),
        ("Archidekt's newsletter", "Occasional email about the site."),
        ("Email me when someone comments", "On your decks and your forum posts."),
        ("Turn off Archidekt notifications", "Turns off Archidekt's notifications for your account."),
    ):
        assert _said(mail, label) and _said(mail, help_text), label
    assert "Each switch saves to Archidekt when you press it." in mail
    # the switches show Archidekt's values: contactable on, newsletter off, notifications on (the
    # "turn off" switch off)
    assert "id='pr-contactable' type='checkbox' name='on' value='1' checked" in mail
    assert "id='pr-newsletter' type='checkbox' name='on' value='1'>" in mail
    assert "id='pr-site_notifications' type='checkbox' name='on' value='1'>" in mail
    defs = _card(page, "defaults")
    assert _said(
        defs,
        "What Archidekt starts with on a device you haven't set up yet. "
        "Each choice saves when you change it.",
    )
    for label in (
        "Deck view",
        "Search unique printings",
        "Use my default categories",
        "Search game",
        "Show custom uploads",
    ):
        assert label in defs, label
    for absent in ("Group by", "Sort by", "Price sources"):  # not in profile.PREFERENCES
        assert absent not in defs, absent
    assert (
        "<option value='card' selected>Card</option>" in defs
        and "<option value='paper' selected>Paper</option>" in defs
    )
    only = _card(page, "archidekt-only")
    assert (
        "Your password, e-mail, Patreon link and account deletion are changed on Archidekt's own site."
        in only
    )
    # one link, to an address Archidekt's saved scripts confirm: its settings pages' names are not known
    assert only.count("<a ") == 1 and f"href='{profile_page.ON_ARCHIDEKT_URL}'" in only
    assert profile_page.ON_ARCHIDEKT_URL == "https://archidekt.com/" and "Open Archidekt</a>" in only
    assert "target='_blank' rel='noopener noreferrer'" in only and "password, which" not in only
    assert (
        "/static/profile.js" in page
        and "blob:" in (await member.http.get("/account")).headers["content-security-policy"]
    )


async def test_the_cards_are_not_drawn_while_archidekt_is_not_linked(stack: Stack) -> None:
    b = Browser(stack.h)
    try:
        await b.login("/account")
        page = (await b.http.get("/account")).text
        assert (
            "Archidekt profile" not in page and "id='email'" not in page and "On Archidekt only" not in page
        )
        assert "data-bell" in page  # the bell is in the bar on every page
        assert (await b.http.get("/notifications/count")).json() == {"ok": True, "unseen": 0}
        page = (await b.http.get("/notifications")).text
        assert "Link your Archidekt account on the Account page to see its notifications here." in page
    finally:
        await b.aclose()


async def test_a_switch_and_a_drop_down_save_on_the_members_press(member: Browser, stack: Stack) -> None:
    me = stack.ark.account.profile(WHO)
    r = await _post(member, action="email", switch="newsletter", on="1")
    assert r.status_code == 303 and r.headers["location"] == "/account?ok=email#email", re.findall(
        r"role=.alert.>([^<]*)", r.text
    )
    assert me["profile"]["newsletterable"] is True
    page = (await member.http.get(r.headers["location"])).text
    assert "<div class='notice ok' role='status'>Saved on Archidekt.</div>" in _card(page, "email")
    assert "id='pr-newsletter' type='checkbox' name='on' value='1' checked" in page
    # "Turn off Archidekt notifications" is the inverted one: on means no notifications
    r = await _post(member, action="email", switch="site_notifications", on="1")
    assert r.status_code == 303 and me["accountSettings"]["noArchidektNotifications"] is True
    r = await _post(member, action="email", switch="site_notifications", on="0")
    assert me["accountSettings"]["noArchidektNotifications"] is False
    r = await _post(member, action="preference", preference="deck_view", value="grid")
    assert r.status_code == 303 and r.headers["location"] == "/account?ok=defaults#defaults"
    assert me["accountSettings"]["deckView"] == profile.DECK_VIEWS["grid"]
    r = await _post(member, action="preference", preference="use_default_categories", on="0")
    assert r.status_code == 303 and me["accountSettings"]["noUseDefaultCategories"] is True
    # with scripts the same post answers JSON, and the page's notice words
    r = await member.http.post(
        "/account/profile",
        data={
            "csrf": await member.csrf("/account"),
            "shown": _stamp(member),
            "action": "preference",
            "preference": "search_game",
            "value": "arena",
        },
        headers={"Accept": "application/json"},
    )
    assert r.json() == {"ok": True, "notice": "Saved on Archidekt.", "verified": True}
    assert me["accountSettings"]["searchGame"] == 3
    # each press was a proposal of the account action, applied by the member's own browser
    rows = stack.h.app.state.gateway.db._conn.execute(
        "SELECT state, changes_json, created_by_client FROM proposals WHERE kind='action'"
    ).fetchall()
    assert len(rows) == 6, rows
    assert all(r[0] == "applied" and '"action": "profile"' in r[1] and r[2] == "__browser__" for r in rows), (
        rows
    )
    applied = stack.h.app.state.gateway.db._conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE event='proposal_applied' AND detail_json LIKE '%review_page%'"
    ).fetchone()[0]
    assert applied == 6


async def test_bio_and_username_save_as_their_own_writes_with_the_notice(
    member: Browser, stack: Stack
) -> None:
    me = stack.ark.account.profile(WHO)
    r = await _post(
        member, action="profile", username="alice", bio="Commander player in Leeds.\nHappy to trade."
    )
    assert r.status_code == 303 and r.headers["location"] == "/account?ok=profile&saved=bio#profile"
    assert profile.bio_words(me["profile"]["bio"]) == "Commander player in Leeds.\nHappy to trade."
    page = (await member.http.get(r.headers["location"])).text
    assert "Profile saved on Archidekt: bio. Read back." in _card(page, "profile")
    assert "Commander player in Leeds.\nHappy to trade.</textarea>" in page
    r = await _post(
        member, action="profile", username="alice_two", bio="Commander player in Leeds.\nHappy to trade."
    )
    assert r.headers["location"] == "/account?ok=profile&saved=username#profile"
    assert me["username"] == "alice_two"
    page = (await member.http.get("/account")).text
    assert "value='alice_two' maxlength='150'" in page
    # nothing changed: the page says so, and nothing was sent
    sent = len([c for c in stack.ark.calls if c[0] == "PATCH"])
    r = await _post(
        member, action="profile", username="alice_two", bio="Commander player in Leeds.\nHappy to trade."
    )
    assert r.headers["location"] == "/account?ok=profile&saved=#profile"
    assert len([c for c in stack.ark.calls if c[0] == "PATCH"]) == sent
    page = (await member.http.get(r.headers["location"])).text
    assert profile_page.NOTHING_CHANGED in _card(page, "profile")


async def test_archidekts_refusal_shows_in_the_panel_with_the_typed_words(
    member: Browser, stack: Stack
) -> None:
    r = await _post(member, action="profile", username="amy", bio="")  # amy exists on the fake
    assert r.status_code == 400, r.headers
    prof = _card(r.text, "profile")
    assert "<div class='notice error' role='alert'>" in prof and "amy" in prof
    assert "value='amy' maxlength='150'" in prof  # kept in the field to fix
    assert stack.ark.account.profile(WHO)["username"] == "alice"
    # a bad switch name: the refusal in its own card
    r = await _post(member, action="email", switch="spam", on="1")
    assert r.status_code == 400 and "switch must be one of" in _card(r.text, "email")
    r = await member.http.post(
        "/account/profile",
        data={
            "csrf": await member.csrf("/account"),
            "shown": _stamp(member),
            "action": "email",
            "switch": "spam",
            "on": "1",
        },
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 400 and r.json()["ok"] is False and "switch must be one of" in r.json()["message"]
    # an expired form
    r = await member.http.post(
        "/account/profile", data={"csrf": "0" * 32, "action": "email", "switch": "newsletter"}
    )
    assert r.status_code == 403
    # a press faster than a person reads the page, or with no render stamp (clickguard): refused
    # with nothing sent, as the review page refuses it
    from mtg_gateway.clickguard import form_stamp

    before = len(stack.ark.calls)
    fresh = form_stamp(stack.h.settings.session_secret, "press:user-1")
    for shown in (fresh, "", "1.deadbeef"):
        r = await member.http.post(
            "/account/profile",
            data={
                "csrf": await member.csrf("/account"),
                "shown": shown,
                "action": "email",
                "switch": "newsletter",
                "on": "1",
            },
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 400 and "only just drawn" in r.json()["message"], shown
    assert stack.ark.account.profile(WHO)["profile"]["newsletterable"] is False
    assert not [c for c in stack.ark.calls[before:] if c[0] in ("PATCH", "PUT", "POST")]


async def test_an_assistant_never_applies_a_profile_change_or_uploads_a_picture(
    member: Browser, stack: Stack
) -> None:
    token = member.token  # type: ignore[attr-defined]
    p = structured(
        await call(
            stack.h, token, "propose_profile_change", {"field": "email", "switch": "newsletter", "on": True}
        )
    )
    assert p["ok"] and p["assistant_may_apply"] is False
    out = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False
    assert stack.ark.account.profile(WHO)["profile"]["newsletterable"] is False
    # the page's routes need the member's browser session: a bearer token is not one
    for path in ("/account/profile", "/account/picture"):
        r = await stack.h.http.post(
            path, data={"action": "email"}, headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code in (302, 401, 403) and not stack.ark.account.uploads, (path, r.status_code)
    tools = [t.name for t in (await stack.h.list_tools())] if hasattr(stack.h, "list_tools") else []
    assert not any("avatar" in t or "picture" in t for t in tools)


# -- the picture ------------------------------------------------------------------------------------------


async def test_the_picture_is_checked_and_sent_on_the_members_press(member: Browser, stack: Stack) -> None:
    csrf = await member.csrf("/account")
    r = await member.http.post(
        "/account/picture", data={"csrf": csrf}, files={"picture": ("me.png", _png(), "image/png")}
    )
    assert r.status_code == 303 and r.headers["location"] == "/account?ok=picture#profile", r.text
    assert stack.ark.account.uploads and stack.ark.account.uploads[-1][0] == WHO
    page = (await member.http.get(r.headers["location"])).text
    assert "Picture saved on Archidekt and read back." in _card(page, "profile")
    assert stack.ark.account.profile(WHO)["profile"]["avatar"].startswith(
        "https://storage.example.com/avatars/"
    )
    n = len(stack.ark.account.uploads)
    # a GIF, a file that is not a picture and a picture over 1 MB are refused in the box
    for name, data, kind, says in (
        ("me.gif", _png(fmt="GIF"), "image/gif", "Use a JPEG or PNG picture."),
        ("me.txt", b"not a picture", "text/plain", "not a picture the gateway can read"),
        ("big.png", b"\x89PNG" + b"x" * 1_000_100, "image/png", "larger than 1 MB"),
    ):
        r = await member.http.post(
            "/account/picture", data={"csrf": csrf}, files={"picture": (name, data, kind)}
        )
        assert r.status_code == 400 and says in r.text, (name, r.status_code)
        assert (
            "id='picture' role='dialog' aria-modal='true' aria-labelledby='picture-title' data-open" in r.text
        ), (name, r.text.split("id='picture'")[1][:160])
        assert len(stack.ark.account.uploads) == n
    r = await member.http.post("/account/picture", data={"csrf": csrf})
    assert r.status_code == 400 and "Choose a picture first." in r.text
    r = await member.http.post(
        "/account/picture", data={"csrf": "0" * 32}, files={"picture": ("me.png", _png(), "image/png")}
    )
    assert r.status_code == 403 and len(stack.ark.account.uploads) == n
    with_scripts = await member.http.post(
        "/account/picture",
        data={"csrf": csrf},
        files={"picture": ("me.jpg", _png(fmt="JPEG"), "image/jpeg")},
        headers={"Accept": "application/json"},
    )
    assert with_scripts.json() == {
        "ok": True,
        "notice": "Picture saved on Archidekt and read back.",
        "verified": True,
    }
    # the old address draws the box open
    page = (await member.http.get("/account?picture=1")).text
    assert "id='picture' role='dialog' aria-modal='true' aria-labelledby='picture-title' data-open" in page


# -- the Collection page's Visibility line -------------------------------------------------------------------


async def test_collection_visibility_public_asks_once_more(member: Browser, stack: Stack) -> None:
    me = stack.ark.account.profile(WHO)
    page = (await member.http.get("/collection")).text
    vis = page.split("id='visibility'", 1)[1].split("</div>", 1)[0]
    assert "Private</b> <span class='muted'>· only you can see it</span>" in vis
    assert "data-box-open='col-vis'" in vis and "Make it public…" in vis
    assert (
        "Make your collection public?" in page
        and "Keep it private" in page
        and "Make it public</button>" in page
    )
    assert (
        "Anyone can then see every card you own, with finish and condition, on your Archidekt profile."
        in page
    )
    assert "hidden>" in page.split("id='col-vis' role='dialog'", 1)[1][:200]
    assert (
        "data-open"
        in (await member.http.get("/collection?visibility=1")).text.split("id='col-vis' role='dialog'", 1)[1][
            :200
        ]
    )
    # a plain press without the box's confirmed field opens the box; the box's form makes it public
    r = await _post(member, action="collection_public", on="1", next="/collection")
    assert r.status_code == 303 and r.headers["location"] == "/collection?visibility=1"
    assert me["profile"]["is_collection_public"] is False
    r = await _post(member, action="collection_public", on="1", confirmed="1", next="/collection")
    assert r.status_code == 303 and r.headers["location"] == "/collection?ok=public#visibility"
    assert me["profile"]["is_collection_public"] is True
    page = (await member.http.get(r.headers["location"])).text
    vis = page.split("id='visibility'", 1)[1]
    assert "Public</b> <span class='muted'>· anyone can see it on your Archidekt profile</span>" in vis
    assert "Make it private</button>" in vis and "Your collection is public on Archidekt." in vis
    # private is one press
    r = await _post(member, action="collection_public", on="0", next="/collection")
    assert (
        r.headers["location"] == "/collection?ok=private#visibility"
        and me["profile"]["is_collection_public"] is False
    )
    assert "Your collection is private on Archidekt." in (await member.http.get(r.headers["location"])).text
    # it was the account action with field collection_public, pressed by the member
    rows = stack.h.app.state.gateway.db._conn.execute(
        "SELECT changes_json, created_by_client FROM proposals WHERE kind='action' ORDER BY rowid"
    ).fetchall()
    assert len(rows) == 2, rows
    assert all('"field": "collection_public"' in r[0] and r[1] == "__browser__" for r in rows), rows


# -- the bell and the Home widget --------------------------------------------------------------------------


async def test_the_bell_count_and_list_are_read_after_the_page_and_kept_a_short_while(
    member: Browser, stack: Stack
) -> None:
    stack.ark.account.notes[WHO] = [
        {"notificationType": "DeckThread", "text": "amy commented on “Meren”", "linkId": 42, "seen": False},
        {"notificationType": "ForumThread", "text": "bob replied", "selfId": 7, "seen": False},
        {"notificationType": "Odd", "text": "x", "seen": True},
    ]
    stack.ark.calls.clear()
    page = (await member.http.get("/")).text
    # the page render asked Archidekt nothing about notifications: the script asks afterwards
    assert not [c for c in stack.ark.calls if "notification" in c[1]]
    assert "<a class='icon-btn bell' href='/notifications' aria-label='Notifications'" in page
    assert "<span class='n' hidden aria-hidden='true'></span>" in page and "/static/bell.js" in page
    widget = page.split("data-bell-widget", 1)[1].split("</div>", 1)[0]
    assert "On Archidekt" in widget and "data-bell-big" in widget and "data-bell-open>Open</a>" in widget
    r = await member.http.get("/notifications/count")
    assert r.json() == {"ok": True, "unseen": 2} and r.headers["cache-control"] == "no-store"
    await member.http.get("/notifications/count")
    assert len([c for c in stack.ark.calls if "notificationCount" in c[1]]) == 1  # kept, not asked again
    r = await member.http.get("/notifications", headers={"Accept": "application/json"})
    d = r.json()
    assert d["ok"] and d["unseen"] == 2
    assert "<a class='bell-row new' href='/decks/42'>" in d["html"]  # a deck opens here, on the gateway
    assert "href='https://archidekt.com/forum/thread/7' target='_blank'" in d["html"]
    assert (
        "<div class='bell-row'><i aria-hidden='true'>A</i><span class='t'>x<span class='s'>Archidekt</span>"
        in d["html"]
    )
    # the count clears once the list was opened, as Archidekt's own bell does
    assert (await member.http.get("/notifications/count")).json()["unseen"] == 0
    await member.http.get("/notifications", headers={"Accept": "application/json"})
    assert len([c for c in stack.ark.calls if c[1].endswith("notifications/")]) == 1
    # the page without scripts
    page = (await member.http.get("/notifications")).text
    assert "<title>Notifications" in page and "amy commented on “Meren”" in page
    assert profile_page.BELL_NOTE in page and "href='/account#email'" in page
    assert "aria-current=page" in page.split("class='icon-btn bell'", 1)[1][:160]


async def test_the_bell_stays_quiet_when_archidekt_cannot_be_asked(member: Browser, stack: Stack) -> None:
    # a link without Archidekt's account number (profile.user_id refuses): the badge stays
    # empty, the list says why, and the pages stand
    stack.h.app.state.gateway.db._conn.execute("UPDATE archidekt_links SET archidekt_user_id=''")
    stack.h.app.state.gateway.db._conn.commit()
    r = await member.http.get("/notifications/count")
    assert r.status_code == 200 and r.json() == {"ok": True, "unseen": 0}
    r = await member.http.get("/notifications", headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json()["ok"] is False and "Link Archidekt again" in r.json()["message"]
    assert (await member.http.get("/")).status_code == 200
    page = (await member.http.get("/account")).text
    assert _said(page, "Archidekt did not answer, so your profile and settings can't be shown right now.")
    assert (await member.http.get("/collection")).status_code == 200


def test_the_profile_cards_without_a_profile_say_so() -> None:
    html = profile_page.account_cards(None, "c")
    assert "Archidekt did not answer" in html and "id='email'" not in html
    assert profile_page.collection_visibility(None, "c", "sub") == ""
    assert re.search(
        r"<i aria-hidden='true'>D</i>",
        profile_page.notifications_html(
            {"notifications": [{"kind": "Deck", "text": "t", "seen": True, "url": None}]}
        ),
    )


async def test_a_link_from_another_site_reads_no_notifications(member: Browser, stack: Stack) -> None:
    """Security review 0.7.44, S-09: reading the list clears the count, so a link on another site
    opens the page without reading it; one press on the page reads it."""
    stack.ark.calls.clear()
    page = (await member.http.get("/notifications", headers={"Sec-Fetch-Site": "cross-site"})).text
    assert not [c for c in stack.ark.calls if "notification" in c[1]]
    assert "href='/notifications'" in page and "Show my notifications" in page
    page = (await member.http.get("/notifications", headers={"Sec-Fetch-Site": "same-origin"})).text
    assert [c for c in stack.ark.calls if c[1].endswith("notifications/")]
    assert "Show my notifications" not in page


async def test_unlinking_forgets_the_profile_bell_and_list_counts(member: Browser, stack: Stack) -> None:
    """Security review 0.7.44, S-11: after an unlink (or Delete my data) nothing read from the
    old Archidekt account is shown again from memory."""
    from mtg_gateway import account_lists

    await member.http.get("/account")
    await member.http.get("/notifications/count")
    account_lists._COUNTS["user-1"] = (10**12, {"pins": 3})
    assert "user-1" in profile_page._PROFILE and "user-1" in profile_page._COUNT
    stack.h.app.state.gateway.decks.unlink("user-1")
    assert "user-1" not in profile_page._PROFILE and "user-1" not in profile_page._COUNT
    assert "user-1" not in account_lists._COUNTS
