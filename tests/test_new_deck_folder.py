"""New deck and New package take an optional folder of the member's own (parity release 2): the
create request names it as archidekt.com's New deck form does, and when the new deck is not in it
on the read-back (Archidekt may ignore the field: not tried live yet) it is moved there with the
folder page's own Move request. A folder that is not the member's is refused before anything is
proposed."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import json

import pytest

from mtg_gateway.decks import DeckError

from .conftest import Stack
from .test_decks_and_proxy import linked_user

SUB = "user-1"  # the signed-in member of linked_user


async def _apply(stack: Stack, pid: str) -> dict:
    out = await stack.h.app.state.gateway.decks.apply(SUB, pid, via="browser")
    assert out.get("state") == "applied", out
    return out


@pytest.mark.parametrize("honoured", [True, False])
@pytest.mark.parametrize("package", [None, {"keywords": "fetchlands"}])
async def test_a_new_deck_or_package_lands_in_the_folder_picked(
    stack: Stack, honoured: bool, package: dict | None
) -> None:
    h, ark = stack.h, stack.ark
    ark.create_honours_folder = honoured
    await linked_user(stack)
    decks = h.app.state.gateway.decks
    folder = await decks.create_folder(SUB, "Brews")
    p = await decks.propose_new_deck(
        SUB, name="New one", deck_format="commander", cards=[], package=package, folder_id=int(folder["id"])
    )
    assert p["rows"][0]["folder"] == "Brews", p["rows"]
    assert "in folder “Brews”" in p["diff"].splitlines()[0], p["diff"]
    before = set(ark.decks)
    out = await _apply(stack, p["proposal_id"])
    (new_id,) = set(ark.decks) - before
    assert ark.deck_folder[new_id] == int(folder["id"])
    assert out["result"]["verified"] is True
    assert bool(ark.mass_updates) is not honoured  # moved only when the create left it at the top


async def test_a_folder_that_is_not_the_members_is_refused(stack: Stack) -> None:
    await linked_user(stack)
    decks = stack.h.app.state.gateway.decks
    with pytest.raises(DeckError, match="not in your Archidekt account"):
        await decks.propose_new_deck(SUB, name="X", deck_format="commander", cards=[], folder_id=999999)


async def test_the_create_request_names_the_folder(stack: Stack) -> None:
    ark = stack.ark
    await linked_user(stack)
    decks = stack.h.app.state.gateway.decks
    folder = await decks.create_folder(SUB, "Brews")
    p = await decks.propose_new_deck(
        SUB, name="Y", deck_format="commander", cards=[], folder_id=int(folder["id"])
    )
    await _apply(stack, p["proposal_id"])
    assert ark.last_create_body["parentFolder"] == int(folder["id"])
    assert json.dumps(ark.last_create_body)  # a plain JSON body


async def test_the_assistant_names_the_folder(stack: Stack) -> None:
    from .test_decks_and_proxy import call, structured

    token = await linked_user(stack)
    decks = stack.h.app.state.gateway.decks
    await decks.create_folder(SUB, "Brews")
    p = structured(
        await call(stack.h, token, "propose_new_deck", {"name": "Z", "cards": [], "folder": "brews"})
    )
    assert p["ok"] is True and "in folder “Brews”" in p["diff"], p
    bad = structured(
        await call(stack.h, token, "propose_new_deck", {"name": "Z", "cards": [], "folder": "Nope"})
    )
    assert bad["ok"] is False and "No folder is called 'Nope'" in bad["message"], bad


# -- the boxes on My decks: a Folder field ---------------------------------------------------------------


async def test_the_new_deck_and_new_package_boxes_take_a_folder(stack: Stack) -> None:
    from .test_decks_and_proxy import Browser

    ark = stack.ark
    await linked_user(stack)
    decks = stack.h.app.state.gateway.decks
    folder = await decks.create_folder(SUB, "Brews")
    fid = int(folder["id"])
    b = Browser(stack.h)
    await b.login("/decks")
    try:
        page = (await b.http.get("/decks")).text
        assert "<select id='nd-folder' name='folder_id'>" in page
        assert "<select id='np-folder' name='folder_id'>" in page
        assert page.count("<option value='' selected>Top level (no folder)</option>") == 2
        assert page.count(f"<option value='{fid}'>Brews</option>") == 2
        # opened from inside a folder, the box starts on that folder
        page = (await b.http.get(f"/decks?folder={fid}")).text
        assert page.count(f"<option value='{fid}' selected>Brews</option>") == 2
        csrf = await b.csrf("/decks")
        r = await b.http.post(
            "/decks/new",
            data={"csrf": csrf, "name": "In Brews", "format": "commander", "folder_id": str(fid)},
        )
        assert r.status_code == 303, r.text
        made = next(d for d in ark.decks.values() if d["name"] == "In Brews")
        assert ark.deck_folder[made["id"]] == fid
        r = await b.http.post(
            "/decks/new-package",
            data={"csrf": csrf, "name": "Brews package", "keywords": "x", "folder_id": str(fid)},
        )
        assert r.status_code == 303, r.text
        made = next(d for d in ark.decks.values() if d["name"] == "Brews package")
        assert ark.deck_folder[made["id"]] == fid
        # a folder that is not the member's comes back in the box, the typed values kept
        r = await b.http.post(
            "/decks/new", data={"csrf": csrf, "name": "Nope", "format": "commander", "folder_id": "999999"}
        )
        assert r.status_code == 400 and "not in your Archidekt account" in r.text
        assert "value='Nope'" in r.text
        r = await b.http.post(
            "/decks/new-package", data={"csrf": csrf, "name": "Nope", "keywords": "x", "folder_id": "999999"}
        )
        assert r.status_code == 400 and "not in your Archidekt account" in r.text
    finally:
        await b.aclose()
