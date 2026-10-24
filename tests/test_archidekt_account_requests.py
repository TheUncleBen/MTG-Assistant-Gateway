"""The account-wide Archidekt requests of parity release 2 (archidekt_account.py): each sends the
route and body archidekt.com's own pages send (REQUESTS.md), refuses a bad value before anything
is sent, and reads the answer's shape. Against the in-memory Archidekt only."""

from __future__ import annotations

import json

import httpx
import pytest

from mtg_gateway.archidekt import ArchidektClient, ArchidektError, Pacer

from .fake_archidekt import FakeArchidekt


@pytest.fixture
def ark() -> FakeArchidekt:
    return FakeArchidekt()


@pytest.fixture
def client(ark: FakeArchidekt) -> ArchidektClient:
    return ArchidektClient("https://archidekt.com/api", "test", Pacer(0.0), http=ark.client())


def _sent(ark: FakeArchidekt) -> list[tuple[str, str]]:
    return [c for c in ark.calls if c[0] != "GET"]


async def test_templates_round_trip(ark: FakeArchidekt, client: ArchidektClient) -> None:
    tok = ark.issue_access("alice")
    made = await client.create_template(
        tok, {"name": "Ramp", "includedInDeck": True, "includedInPrice": True, "isPremier": False}
    )
    assert made["name"] == "Ramp"
    await client.update_template(
        tok,
        made["id"],
        {"name": "Ramp", "includedInDeck": True, "includedInPrice": False, "isPremier": False},
    )
    assert (await client.templates(tok))[0]["includedInPrice"] is False
    await client.delete_template(tok, made["id"])
    assert await client.templates(tok) == []
    with pytest.raises(ArchidektError):
        await client.create_template(tok, {"name": " ", "includedInDeck": True})
    assert ("POST", "/api/users/category/") in ark.calls


async def test_default_categories_need_template_names(ark: FakeArchidekt, client: ArchidektClient) -> None:
    tok = ark.issue_access("alice")
    for name in ("Ramp", "Draw"):
        await client.create_template(
            tok, {"name": name, "includedInDeck": True, "includedInPrice": True, "isPremier": False}
        )
    await client.set_default_categories(tok, 9003, ["Ramp", "Draw"])
    page = await client.default_categories(tok)
    assert page["cards"][0]["globalCategories"] == ["Ramp", "Draw"] and page["total"] == 1
    with pytest.raises(ArchidektError):
        await client.set_default_categories(tok, 9003, ["Nope"])  # Archidekt's 400


async def test_color_tags_check_name_and_color_before_sending(
    ark: FakeArchidekt, client: ArchidektClient
) -> None:
    tok = ark.issue_access("alice")
    before = len(ark.calls)
    for name, color in (("x" * 33, "#fa890d"), ("Cut", "red"), ("", "#000000")):
        with pytest.raises(ArchidektError):
            await client.create_color_tag(tok, name, color)
    assert len(ark.calls) == before  # nothing was sent
    tag = await client.create_color_tag(tok, "Cut", "#ff0000")
    await client.update_color_tag(tok, {**tag, "name": "Cuts", "color": "#00ff00"})
    assert (await client.color_tags(tok))[0]["name"] == "Cuts"
    await client.delete_color_tag(tok, tag["id"])
    assert await client.color_tags(tok) == []


async def test_pins(ark: FakeArchidekt, client: ArchidektClient) -> None:
    tok = ark.issue_access("alice")
    await client.pin_cards(tok, [{"cardId": 9003, "notes": "", "modifier": "Normal", "quantity": 1}])
    pin = (await client.pins(tok))[0]
    await client.update_pin(tok, pin["id"], card_id=9003, quantity=2, notes="for Meren", modifier="Foil")
    assert (await client.pins(tok))[0]["notes"] == "for Meren"
    with pytest.raises(ArchidektError):
        await client.update_pin(tok, pin["id"], card_id=9003, quantity=0, notes="", modifier="Normal")
    await client.delete_all_pins(tok)
    assert await client.pins(tok) == []
    assert ("DELETE", "/api/users/pins/deleteAll/") in ark.calls


async def test_custom_cards_and_the_picture_upload(ark: FakeArchidekt, client: ArchidektClient) -> None:
    tok = ark.issue_access("alice")
    with pytest.raises(ArchidektError):  # a type is required, as on the site
        await client.create_custom_card(tok, {"frontName": "Grizzled Mentor"}, 77)
    made = await client.create_custom_card(
        tok, {"frontName": "Grizzled Mentor", "frontTypes": "Creature", "backName": "dropped"}, 77
    )
    assert made["backName"] is None and made["hasBack"] is False and made["owner"] == 77
    url = await client.upload_image(tok, b"\x89PNG fake")
    assert url.startswith("https://")
    who, body, disposition = ark.account.uploads[-1]
    assert who == "alice" and body == b"\x89PNG fake" and disposition == 'attachment; filename="tmp.png"'
    put = [r for r in ark.calls if r == ("PUT", "/api/users/uploadImage/")]
    assert put
    await client.update_custom_card(tok, made["id"], {**made, "frontImageUrl": url}, 77)
    assert (await client.custom_cards(tok, "grizzled"))["results"][0]["frontImageUrl"] == url
    await client.delete_custom_card(tok, made["id"])
    assert (await client.custom_cards(tok))["results"] == []


async def test_help_report_pdf(ark: FakeArchidekt, client: ArchidektClient) -> None:
    tok = ark.issue_access("alice")
    h = await client.create_deck_help(tok, "42", "goals words", "issue words")
    assert json.loads(ark.account.help[42]["goals"])["ops"][0]["insert"].startswith("goals words")
    await client.update_deck_help(tok, h["id"], "goals words", "issue words", active=False)
    assert ark.account.help[42]["active"] is False
    with pytest.raises(ArchidektError):
        await client.report(tok, "https://evil.example/", "Deck", "spam")
    with pytest.raises(ArchidektError):
        await client.report(tok, "https://archidekt.com/decks/42", "Profile", "spam")
    await client.report(tok, "https://archidekt.com/decks/42", "Deck", "spam")
    assert ark.account.reports[-1]["type"] == "Deck"
    assert (await client.registration_pdf(tok, {"deckName": "x"})).endswith(".pdf")


async def test_private_profile_groups(ark: FakeArchidekt, client: ArchidektClient) -> None:
    tok = ark.issue_access("alice")
    await client.patch_user_private(tok, 77, profile={"is_collection_public": True})
    await client.patch_user_private(tok, 77, settings={"deckView": 2})
    me = await client.user_private(tok, 77)
    assert me["profile"]["is_collection_public"] is True and me["accountSettings"]["deckView"] == 2
    with pytest.raises(ArchidektError):  # the password and email routes are never reachable
        await client.patch_user_private(tok, 77, profile={"email": "x@example.com"})
    with pytest.raises(ArchidektError):
        await client.patch_user_private(tok, 77, settings={"password": "x"})
    with pytest.raises(ArchidektError):  # taken, Archidekt's 400
        await client.patch_user_private(tok, 77, username="amy")
    ark.account.notes["alice"] = [{"notificationType": "Deck", "text": "hi", "seen": False}]
    assert await client.notification_count(tok, 77) == 1
    assert (await client.notifications(tok, 77))[0]["text"] == "hi"


async def test_contest_entry(ark: FakeArchidekt, client: ArchidektClient) -> None:
    tok = ark.issue_access("alice")
    e = await client.contest_submit(tok, 77, 555, "42")
    assert (await client.contest_entries(tok, 555))[0]["deck"]["id"] == 42
    await client.contest_withdraw(tok, e["id"])
    assert await client.contest_entries(tok, 555) == []


async def test_an_upload_carries_no_json_content_type(ark: FakeArchidekt, client: ArchidektClient) -> None:
    seen: list[httpx.Request] = []
    real = ark.transport.handler

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return real(request)

    ark.transport.handler = spy
    tok = ark.issue_access("alice")
    await client.upload_avatar(tok, 77, b"\xff\xd8 jpeg", "me.jpg")
    req = seen[-1]
    assert req.method == "PUT" and req.content == b"\xff\xd8 jpeg"
    assert "json" not in req.headers.get("content-type", "")
    assert req.headers["Content-Disposition"] == 'attachment; filename="me.jpg"'
