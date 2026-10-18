"""Release 0.7.27: the Archidekt write calls checked against archidekt.com's own site code (its
public script files, read 2026-10-14). Each test pins one call to the shape the site sends: the
deck like's route, the vote taken back, rich text (descriptions and comments are Quill documents),
the collection's numeric condition and language, the tag position, the folder move item and the
cover URL's image version."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import json

import pytest

from mtg_gateway.archidekt import (
    ArchidektClient,
    ArchidektError,
    Pacer,
    QuillDoc,
    art_url,
    condition_code,
    language_code,
    plain_text,
    rich_doc,
    rich_text,
    same_rich_text,
)
from mtg_gateway.decks import next_tag_position

from .conftest import COLLECTION, Stack, stack_with
from .fake_archidekt import FakeArchidekt
from .test_browse_collection import NAV, api, linked
from .test_social_layout import post

stack_options = stack_with(COLLECTION)

UID = "be68e315-ffef-40a8-8a46-1c042b148c03"


def _client(ark: FakeArchidekt) -> ArchidektClient:
    return ArchidektClient("https://ark.test/api", "test", Pacer(0.0), http=ark.client())


def _amy_comment(cid: int, text: str, root: int = 300043) -> dict:
    return {
        "id": cid,
        "text": text,
        "owner": {"id": 78, "username": "amy", "avatar": None, "frame": None},
        "parent": root,
        "originalPost": root,
        "createdAt": "2026-10-10T12:00:00Z",
        "editedAt": None,
        "points": 0,
        "userInput": 0,
        "childrenCount": 0,
        "children": {"count": 0, "results": []},
        "archived": False,
        "locked": False,
        "type": 4,
    }


# -- rich text ------------------------------------------------------------------------------------
def test_rich_text_round_trip_matches_the_site_editor() -> None:
    doc = rich_doc("Ramp first.\nThen draw.")
    assert json.loads(doc) == {"ops": [{"insert": "Ramp first.\nThen draw.\n"}]}
    assert rich_text(doc) == "Ramp first.\nThen draw."
    assert rich_doc("") == ""  # no text stays no text
    formatted = json.dumps(
        {
            "ops": [
                {"insert": "Play "},
                {"insert": {"card-link": "Sol Ring"}},
                {"insert": " early", "attributes": {"bold": True}},
                {"attributes": {"list": "ordered"}, "insert": "\n"},
            ]
        }
    )
    # only a snapshot restore's document (a QuillDoc) keeps its formatting; any other text,
    # a document included, is written as the plain text it is
    assert rich_doc(QuillDoc(formatted)) == formatted
    assert json.loads(rich_doc(formatted)) == {"ops": [{"insert": formatted + "\n"}]}
    assert rich_doc(QuillDoc("older plain text")) == rich_doc("older plain text")
    assert rich_text(formatted) == "Play Sol Ring early"
    assert rich_text("older plain text") == "older plain text" and rich_text(None) == ""
    assert same_rich_text(formatted, "Play Sol Ring early\n") and not same_rich_text(formatted, "x")


async def test_description_and_comments_are_sent_as_quill_documents() -> None:
    ark = FakeArchidekt()
    client = _client(ark)
    token = ark.issue_access("alice")
    await client.update_deck(token, "42", {"description": "Lands matter."})
    assert ark.updates[-1] == {"deck_id": 42, "description": '{"ops":[{"insert":"Lands matter.\\n"}]}'}
    ark.comments[300043] = []
    ark.private.discard(43)
    made = await client.comment_create(token, 300043, "Nice ramp!")
    assert ark.comments[300043][0]["text"] == rich_doc("Nice ramp!")
    await client.comment_update(token, made["id"], "Edited.")
    assert ark.comments[300043][0]["text"] == rich_doc("Edited.")
    await client.aclose()


async def test_quill_description_shows_as_text_and_an_unchanged_text_is_no_change(stack: Stack) -> None:
    quill = json.dumps(
        {"ops": [{"insert": "Blink "}, {"insert": "value", "attributes": {"bold": True}}, {"insert": "\n"}]}
    )
    stack.ark.decks[42]["description"] = quill
    b = await linked(stack)
    try:
        page = await b.http.get("/decks/42", headers=NAV)
        assert "Blink value" in page.text and "{&quot;ops" not in page.text and '{"ops' not in page.text
        settings = await b.http.get("/decks/42/settings", headers=NAV)
        assert ">Blink value</textarea>" in settings.text
        # saving the settings with the text untouched leaves Archidekt's formatted document alone
        before = len(stack.ark.updates)
        csrf = await b.csrf("/decks")
        r = await b.http.post(
            "/decks/42/settings",
            data={
                "csrf": csrf,
                "name": stack.ark.decks[42]["name"],
                "description": "Blink value",
                "deck_format": "commander",
                "edh_bracket": str(stack.ark.decks[42].get("edhBracket") or ""),
                **({"private": "1"} if 42 in stack.ark.private else {}),
            },
        )
        assert r.status_code in (200, 303, 400), r.text[:300]
        mine = [u for u in stack.ark.updates[before:] if u["deck_id"] == 42]  # not the backup copy's
        assert all("description" not in u for u in mine), mine
        assert stack.ark.decks[42]["description"] == quill
    finally:
        await b.aclose()


async def test_comments_read_as_text_and_post_as_documents(stack: Stack) -> None:
    stack.ark.private.discard(43)
    stack.ark.comments[300043] = [_amy_comment(555001, rich_doc("Try Cultivate."))]
    b = await linked(stack)
    try:
        r = await b.http.get("/social/api/decks/43/comments")
        assert r.json()["comments"][0]["text"] == "Try Cultivate."
        r = await post(b, "/social/api/decks/43/comments", {"text": "Thanks!", "parent": 555001})
        assert r.status_code == 201 and r.json()["comment"]["text"] == "Thanks!"
        assert stack.ark.comments[300043][-1]["text"] == rich_doc("Thanks!")
    finally:
        await b.aclose()


# A Quill document with a hidden phishing link and an embed, as an assistant steered by text in
# another deck might send it.
PHISH = json.dumps(
    {
        "ops": [
            {"insert": "See my primer", "attributes": {"link": "https://evil.example/login"}},
            {"insert": {"image": "https://evil.example/pixel.png"}},
            {"insert": "\n"},
        ]
    }
)


def test_plain_text_keeps_only_the_words_of_a_document() -> None:
    """What an assistant or a form supplies is reduced to its words, never sent as a document;
    reducing it again changes nothing (a nested document is reduced all the way)."""
    assert plain_text(PHISH) == "See my primer"
    nested = json.dumps({"ops": [{"insert": PHISH + "\n"}]})
    assert plain_text(nested) == "See my primer" == plain_text(plain_text(nested))
    assert plain_text("Ramp {first}") == "Ramp {first}"
    assert "evil" not in rich_doc(plain_text(PHISH))


def test_drops_formatting_only_when_a_document_loses_more_than_its_words() -> None:
    """A Quill document (at any depth plain_text reduces) with an op other than a
    plain text insert loses something; plain text, Markdown and a words-only document do not."""
    from mtg_gateway.archidekt import drops_formatting, words_only_note

    def doc(*ops: dict) -> str:
        return json.dumps({"ops": list(ops)})

    assert drops_formatting(PHISH)  # a link and an image
    assert drops_formatting(doc({"insert": "Ramp", "attributes": {"bold": True}}, {"insert": "\n"}))
    assert drops_formatting(doc({"insert": "Primer"}, {"insert": "\n", "attributes": {"header": 2}}))
    assert drops_formatting(doc({"insert": "Key card: "}, {"insert": {"card-link": "Sol Ring"}}))
    assert drops_formatting(json.dumps({"ops": [{"insert": PHISH + "\n"}]}))  # nested
    assert not drops_formatting(doc({"insert": "Ramp first.\n"}, {"insert": "Then draw.\n"}))
    assert not drops_formatting(doc({"insert": "Ramp\n", "attributes": {}}))
    assert not drops_formatting("Ramp {first}")
    assert not drops_formatting("**Primer**: [guide](https://example.com/guide)")
    assert not drops_formatting('{"not": "a document"}') and not drops_formatting(None)
    assert words_only_note("comment") == (
        "Only the words of this comment are saved: the gateway writes new text as plain text, so the "
        "links, formatting and embeds in what you sent were removed (formatting already on Archidekt "
        "stays where its words did not change). Don't tell the user a link or formatting was added."
    )


async def test_a_comment_typed_as_a_document_is_posted_as_plain_text(stack: Stack) -> None:
    """A comment or comment edit sent as a Quill document with a hidden link (the comment form,
    or any caller of its API) is posted and edited as its words only."""
    stack.ark.private.discard(43)
    stack.ark.comments[300043] = []
    b = await linked(stack)
    try:
        r = await post(b, "/social/api/decks/43/comments", {"text": PHISH})
        assert r.status_code == 201, r.text
        assert r.json()["comment"]["text"] == "See my primer"
        made = stack.ark.comments[300043][-1]
        assert made["text"] == rich_doc("See my primer") and "evil" not in made["text"]
        r = await b.http.patch(
            f"/social/api/decks/43/comments/{made['id']}",
            content=json.dumps({"text": PHISH.replace("See my", "Read my")}),
            headers={"Content-Type": "application/json", "X-CSRF-Token": await b.csrf("/decks")},
        )
        assert r.status_code == 200, r.text
        assert stack.ark.comments[300043][-1]["text"] == rich_doc("Read my primer")
    finally:
        await b.aclose()


# -- likes and votes ----------------------------------------------------------------------------------
async def test_deck_like_goes_to_the_deck_vote_route(stack: Stack) -> None:
    stack.ark.private.discard(43)
    b = await linked(stack)
    try:
        r = await post(b, "/social/api/decks/43/vote", {"vote": "up"})
        assert r.status_code == 200 and r.json()["points"] == 1
        assert stack.ark.vote_calls[-1] == {"path": "/api/decks/43/vote/", "up": True, "remove": False}
        r = await post(b, "/social/api/decks/43/vote", {"vote": "down"})
        assert stack.ark.vote_calls[-1] == {"path": "/api/decks/43/vote/", "up": False, "remove": False}
        # taking a down vote back names the down vote, as the site's buttons do
        r = await post(b, "/social/api/decks/43/vote", {"vote": "none"})
        assert r.json()["points"] == 0 and stack.ark.votes == {}
        assert stack.ark.vote_calls[-1] == {"path": "/api/decks/43/vote/", "up": False, "remove": True}
        assert all(c["path"].startswith("/api/decks/") for c in stack.ark.vote_calls)
    finally:
        await b.aclose()


async def test_a_deck_like_already_so_sends_nothing(stack: Stack) -> None:
    """As for comment votes, liking a deck the member already likes (or taking back a vote
    they have not given) sends nothing, since Archidekt might toggle the vote off instead."""
    stack.ark.private.discard(43)
    b = await linked(stack)
    try:
        r = await post(b, "/social/api/decks/43/vote", {"vote": "up"})
        assert r.status_code == 200 and r.json()["points"] == 1
        sent = len(stack.ark.vote_calls)
        r = await post(b, "/social/api/decks/43/vote", {"vote": "up"})
        assert r.status_code == 200 and r.json() == {"ok": True, "vote": 1, "points": 1}, r.json()
        assert len(stack.ark.vote_calls) == sent and stack.ark.votes == {("alice", 300043): 1}
        r = await post(b, "/social/api/decks/43/vote", {"vote": "none"})
        assert r.json()["points"] == 0 and stack.ark.votes == {}
        sent = len(stack.ark.vote_calls)
        r = await post(b, "/social/api/decks/43/vote", {"vote": "none"})
        assert r.status_code == 200 and len(stack.ark.vote_calls) == sent
    finally:
        await b.aclose()


async def test_comment_vote_taken_back_names_the_vote(stack: Stack) -> None:
    stack.ark.private.discard(43)
    stack.ark.comments[300043] = [_amy_comment(555001, rich_doc("Try Cultivate."))]
    b = await linked(stack)
    try:
        path = "/social/api/decks/43/comments/555001/vote"
        r = await post(b, path, {"vote": "down"})
        assert r.status_code == 200, r.text
        assert stack.ark.vote_calls[-1] == {
            "path": "/api/comments/vote/555001/",
            "up": False,
            "remove": False,
        }
        r = await post(b, path, {"vote": "none"})
        assert r.status_code == 200, r.text
        assert stack.ark.vote_calls[-1] == {"path": "/api/comments/vote/555001/", "up": False, "remove": True}
    finally:
        await b.aclose()


# -- collection ---------------------------------------------------------------------------------------
def test_condition_and_language_numbers_are_archidekts_tables() -> None:
    assert [condition_code(i) for i in range(1, 6)] == ["NM", "LP", "MP", "HP", "DMG"]
    assert [language_code(i) for i in range(1, 12)] == [
        "EN", "ZHT", "DE", "FR", "IT", "JA", "KO", "PT", "RU", "ZHS", "ES",
    ]  # fmt: skip
    assert condition_code(None) == "" and language_code("en") == "EN"


async def test_collection_writes_send_numbers() -> None:
    ark = FakeArchidekt()
    client = _client(ark)
    token = ark.issue_access("alice")
    card_id = next(iter(ark._deck_printings()))["id"] if ark._deck_printings() else None
    assert card_id is not None
    made = await client.collection_add(token, card_id, 2, condition="LP", language="JA")
    assert (made["condition"], made["language"]) == (2, 6)
    out = await client.collection_set(token, made, condition="DMG", language="ES")
    assert (out["condition"], out["language"]) == (5, 11)
    out = await client.collection_set(token, out, condition=None)  # "not set" clears it
    assert out["condition"] is None and out["language"] == 11
    with pytest.raises(ArchidektError):
        await client.collection_add(token, card_id, 1, language="PH")  # Archidekt has no Phyrexian
    await client.aclose()


async def test_a_collection_edit_never_fails_on_a_stored_value_it_does_not_know() -> None:
    """Only the values being changed are converted (and refused when unknown). A stored
    number goes back as it came, a stored code as its number, and a stored string with no number
    ("" or an old "JAPANESE") is left out instead of failing a plain quantity change."""
    ark = FakeArchidekt()
    client = _client(ark)
    token = ark.issue_access("alice")
    card_id = next(iter(ark._deck_printings()))["id"]
    made = await client.collection_add(token, card_id, 2, condition="LP", language="JA")
    row = ark.collections["alice"][made["id"]]
    row.update(condition="", language="JAPANESE")
    out = await client.collection_set(token, dict(row), quantity=3)
    assert out["quantity"] == 3  # nothing raised; the unknown stored values were not sent back
    row.update(condition=3, language="fr")
    out = await client.collection_set(token, dict(row), quantity=4)
    assert (out["quantity"], out["condition"], out["language"]) == (4, 3, 4)
    with pytest.raises(ArchidektError):  # a value being changed must still be one Archidekt has
        await client.collection_set(token, dict(row), language="JAPANESE")
    await client.aclose()


async def test_collection_page_shows_codes_for_archidekts_numbers(stack: Stack) -> None:
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "add", "name": "Sol Ring", "quantity": "1"}
        )
        assert r.status_code == 303
        rid = next(iter(stack.ark.collections["alice"]))
        # nothing picked: English and Near Mint, as archidekt.com's own new record
        sent = stack.ark.collection_posts[-1]
        assert (sent.get("condition"), sent.get("language")) == (1, 1), sent
        stack.ark.collections["alice"][rid].update(condition=3, language=4)
        page = await b.http.get("/collection", headers=NAV)
        assert "class='pill cond'>MP<" in page.text
        assert "<option value='FR' selected>" in page.text and "value='PH'" not in page.text
        r = await api(b, "POST", f"/collection/api/rows/{rid}", {"language": "PH"})
        assert r.status_code == 400
    finally:
        await b.aclose()


# -- tags, folders, cover -----------------------------------------------------------------------------
def test_tag_position_follows_the_last_tag_like_the_site() -> None:
    assert next_tag_position([]) == "M-500000"
    assert next_tag_position(["M-500000"]) == "M-510000"
    assert next_tag_position(["M-530000", "M-505000"]) == "M-540000"  # after the highest one
    assert next_tag_position(["M-995000"]) == "N-0"
    assert next_tag_position(["9-995000"]) == "A-0"


def test_a_tag_after_the_last_letter_still_sorts_last() -> None:
    """Past "Z" there is no next letter; the new tag must not wrap to "Z-0" (which sorts first)."""
    for last in ("Z-990000", "Z-995000", "Z-999998", "Z-999999"):
        after = next_tag_position(["M-500000", last])
        assert after.startswith("Z-") and int(after[2:]) >= int(last[2:]), (last, after)
    assert next_tag_position(["Z-995000"]) == "Z-997500"
    assert next_tag_position(["Z-980000"]) == "Z-990000"  # room left: the usual step


async def test_added_tag_goes_after_a_reordered_tag(stack: Stack) -> None:
    stack.ark.deck_tags[42] = [{"id": 9001, "tag": 2, "deck": 42, "name": "tribal", "position": "M-720000"}]
    b = await linked(stack)
    try:
        csrf = await b.csrf("/decks")
        r = await b.http.post("/decks/42/tags", data={"csrf": csrf, "action": "add", "name": "budget"})
        assert r.status_code == 303, r.text[:300]
        assert stack.ark.deck_tags[42][-1]["position"] == "M-730000"
    finally:
        await b.aclose()


async def test_move_sends_the_site_move_item(stack: Stack) -> None:
    b = await linked(stack)
    try:
        csrf = await b.csrf("/decks")
        root = 1000 + 77
        folder = await b.http.post(
            "/folders", data={"csrf": csrf, "action": "create", "name": "Cube", "confirmed": "1"}
        )
        assert folder.status_code == 303
        fid = stack.ark.folders["alice"][-1]["id"]
        r = await b.http.post("/decks/42/move", data={"csrf": csrf, "folder_id": str(fid)})
        assert r.status_code == 303 and stack.ark.deck_folder[42] == fid
        assert stack.ark.mass_updates[-1] == {
            "items": [{"type": "deck", "id": 42, "patch": {"parentFolder": fid}}]
        }
        r = await b.http.post("/decks/42/move", data={"csrf": csrf, "folder_id": str(root)})
        assert stack.ark.deck_folder[42] == root
    finally:
        await b.aclose()


def test_cover_url_carries_the_image_version() -> None:
    base = f"https://card-images.archidekt.com/art/front/b/e/{UID}.webp"
    assert art_url(UID, "1783928704") == base + "?1783928704"
    assert art_url(UID) == base + "?11"  # the site's own default
    assert art_url(UID, "../x") == base + "?11"
