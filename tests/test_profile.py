"""The member's Archidekt profile and settings (parity release 2, REQUESTS.md sections 9 and 10):
one group a change, applied only by the member's own press, and the notification list."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import json

import pytest

from mtg_gateway import profile

from .conftest import Stack
from .test_account_actions import _browser_apply
from .test_decks_and_proxy import call, linked_user, structured


async def _propose(stack: Stack, token: str, **args: object) -> dict:
    return structured(await call(stack.h, token, "propose_profile_change", args))


@pytest.mark.parametrize(
    ("args", "check"),
    [
        (
            {"field": "bio", "bio": "Simic\nramp player\u202e"},
            lambda me: profile.bio_words(me["profile"]["bio"]) == "Simic\nramp player",
        ),
        (
            {"field": "collection_public", "on": True},
            lambda me: me["profile"]["is_collection_public"] is True,
        ),
        (
            {"field": "email", "switch": "newsletter", "on": True},
            lambda me: me["profile"]["newsletterable"] is True,
        ),
        (
            {"field": "email", "switch": "site_notifications", "on": False},
            lambda me: me["accountSettings"]["noArchidektNotifications"] is True,
        ),
        (
            {"field": "preference", "preference": "deck_view", "value": "Table"},
            lambda me: me["accountSettings"]["deckView"] == 4,
        ),
        (
            {"field": "preference", "preference": "use_default_categories", "value": "off"},
            lambda me: me["accountSettings"]["noUseDefaultCategories"] is True,
        ),
    ],
)
async def test_each_change_is_one_group_and_waits_for_the_members_press(
    stack: Stack, args: dict, check
) -> None:  # noqa: ANN001
    token = await linked_user(stack)
    p = await _propose(stack, token, **args)
    assert p["ok"] is True, p
    assert p["kind"] == "action"
    applied = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert applied["ok"] is False  # the assistant never applies it
    done = await _browser_apply(stack.h, p["proposal_id"])
    assert done["state"] == "applied" and done["result"]["verified"] is True, done
    assert check(stack.ark.account.profile("alice"))
    again = await _propose(stack, token, **args)
    assert again["ok"] is False and "already set that way" in again["message"]


async def test_a_new_username_is_kept_on_the_link(stack: Stack) -> None:
    token = await linked_user(stack)
    p = await _propose(stack, token, field="username", username="alice_two")
    assert "from alice to alice_two" in p["diff"], p
    done = await _browser_apply(stack.h, p["proposal_id"])
    assert done["result"]["verified"] is True, done
    link = stack.h.app.state.gateway.db._conn.execute(
        "SELECT archidekt_username FROM archidekt_links"
    ).fetchone()
    assert link[0] == "alice_two"
    taken = await _propose(stack, token, field="username", username="amy")
    assert taken["ok"] is True  # Archidekt answers that one: the apply says so
    failed = await _browser_apply(stack.h, taken["proposal_id"])
    assert failed.get("ok") is False or failed.get("state") != "applied", failed


@pytest.mark.parametrize(
    ("args", "says"),
    [
        ({"field": "bio", "bio": "x" * 1001}, "at most 1000"),
        ({"field": "username", "username": "a b"}, "1 to 150"),
        ({"field": "email", "switch": "spam", "on": True}, "switch must be one of"),
        ({"field": "email", "switch": "newsletter"}, "true or false"),
        ({"field": "preference", "preference": "deck_view", "value": "cards"}, "takes one of"),
    ],
)
async def test_bad_changes_are_refused(stack: Stack, args: dict, says: str) -> None:
    token = await linked_user(stack)
    out = await _propose(stack, token, **args)
    assert out["ok"] is False and says in out["message"], out


async def test_the_profile_and_notifications_read_in_the_gateways_words(stack: Stack) -> None:
    token = await linked_user(stack)
    stack.ark.account.profile("alice")["profile"]["bio"] = json.dumps({"ops": [{"insert": "Hi\n"}]})
    stack.ark.account.notes["alice"] = [
        {"notificationType": "DeckThread", "text": "amy commented", "linkId": 42, "seen": False},
        {"notificationType": "Odd", "text": "x", "seen": True},
    ]
    me = structured(await call(stack.h, token, "get_my_profile", {}))
    assert (
        me["bio"] == "Hi"
        and me["preferences"]["deck_view"] == "card"
        and me["email"]["site_notifications"] is True
    )
    bell = structured(await call(stack.h, token, "get_my_notifications", {}))
    assert bell["unseen"] == 1
    assert bell["notifications"][0]["url"] == "https://archidekt.com/decks/42"
    assert bell["notifications"][1]["url"] is None


def _picture(fmt: str, size: tuple[int, int] = (64, 64), **save: object) -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(out, format=fmt, **save)
    return out.getvalue()


def test_a_profile_picture_is_checked_and_saved_again_without_its_details() -> None:
    from PIL import Image

    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make: a detail that must not leave the gateway
    data, name = profile.clean_avatar(_picture("JPEG", exif=exif.tobytes()))
    assert name == "avatar.jpg" and b"PhoneMaker" not in data
    assert profile.clean_avatar(_picture("PNG"))[1] == "avatar.png"
    for bad, says in (
        (b"", "Choose"),
        (b"x" * 1_000_001, "1 MB"),
        (b"not a picture", "can read"),
        (_picture("GIF"), "JPEG or PNG"),
    ):
        with pytest.raises(Exception, match=says):
            profile.clean_avatar(bad)


async def test_the_profile_picture_upload_reads_the_profile_back(stack: Stack) -> None:
    await linked_user(stack)
    svc = stack.h.app.state.gateway.decks
    sub = next(iter(stack.h.app.state.gateway.db._conn.execute("SELECT sub FROM archidekt_links").fetchone()))
    out = await profile.set_avatar(svc, sub, _picture("PNG"))
    assert out == {"avatar": "https://storage.example.com/avatars/1.png", "verified": True}
    assert stack.ark.account.uploads[0][0] == "alice"
