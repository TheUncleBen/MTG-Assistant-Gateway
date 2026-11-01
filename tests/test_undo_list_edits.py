"""A list edit is reversible (0.7.46 audit QG-09, QOL-01): a template, color tag, default
category or custom card the member created, saved or deleted on the Lists pages can be undone
from the notice for a while; Undo sends the reverse change the same way (the member's own
press, applied at once and read back). A token is the member's own and is spent once; a pin's
quantity change answers the page's script with the token too."""

from __future__ import annotations

import re

import pytest

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED
from .test_lists_page import WHO, _member, _notice, _post, _seed

stack_options = stack_with(SHIPPED)


@pytest.fixture
async def member(stack: Stack):
    _seed(stack.ark)
    b = await _member(stack)
    try:
        yield b
    finally:
        await b.aclose()


def _undo_token(location: str) -> str:
    m = re.search(r"[?&]undo=([A-Za-z0-9_-]{16,})", location)
    assert m, location
    return m.group(1)


async def test_a_template_edit_and_a_tag_rename_are_undone_from_the_notice(member, stack: Stack) -> None:  # noqa: ANN001
    ark = stack.ark
    r = await _post(member, "templates", action="create", name="Draw", in_deck="1", in_price="1")
    assert r.status_code == 303
    token = _undo_token(r.headers["location"])
    page = (await member.http.get(r.headers["location"])).text
    assert _notice(page) == "Template created on Archidekt and read back."
    assert f"name='token' value='{token}'" in page and ">Undo</button>" in page
    # Undo of a create deletes it again, by the member's own press
    r = await _post(member, "templates", action="undo", token=token)
    assert r.status_code == 303 and r.headers["location"] == "/lists/templates?ok=list_undone&n=Draw", r.text
    assert [t["name"] for t in ark.account.templates[WHO]] == ["Ramp", "Commander"]
    assert _notice((await member.http.get(r.headers["location"])).text) == "Undone. “Draw” is as it was."
    # spent
    r = await _post(member, "templates", action="undo", token=token)
    assert r.status_code == 400 and "That Undo is no longer available." in r.text

    # an edit is undone with the values it had before (the name and its flags)
    r = await _post(member, "templates", action="edit", was_name="Ramp", name="Mana ramp", in_deck="1")
    assert r.status_code == 303
    t = next(t for t in ark.account.templates[WHO] if t["name"] == "Mana ramp")
    assert t["includedInPrice"] is False
    r = await _post(member, "templates", action="undo", token=_undo_token(r.headers["location"]))
    assert r.status_code == 303, r.text
    t = ark.account.templates[WHO][0]
    assert t["name"] == "Ramp" and t["includedInDeck"] is True and t["includedInPrice"] is True

    # a color tag's rename and recolor
    r = await _post(member, "tags", action="edit", was_name="Cut", name="Cut it", color="#aa0000")
    assert r.status_code == 303
    r = await _post(member, "tags", action="undo", token=_undo_token(r.headers["location"]))
    assert r.status_code == 303, r.text
    tag = next(t for t in ark.account.tags[WHO] if t["name"] == "Cut")
    assert tag["color"] != "#aa0000"


async def test_a_deleted_template_comes_back_with_undo(member, stack: Stack) -> None:  # noqa: ANN001
    ark = stack.ark
    before = dict(next(t for t in ark.account.templates[WHO] if t["name"] == "Commander"))
    r = await _post(member, "templates", action="delete", was_name="Commander", confirmed="1")
    assert r.status_code == 303 and "ok=template_deleted" in r.headers["location"], r.text
    assert "Commander" not in [t["name"] for t in ark.account.templates[WHO]]
    r = await _post(member, "templates", action="undo", token=_undo_token(r.headers["location"]))
    assert r.status_code == 303, r.text
    back = next(t for t in ark.account.templates[WHO] if t["name"] == "Commander")
    for k in ("includedInDeck", "includedInPrice", "isPremier"):
        assert back[k] == before[k], k


async def test_a_pin_quantity_change_answers_the_script_with_its_undo(member, stack: Stack) -> None:  # noqa: ANN001
    csrf = await member.csrf("/collection/wants")
    r = await member.http.post(
        "/lists/pins",
        data={"csrf": csrf, "action": "edit", "pin_id": "31", "quantity": "2"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and re.fullmatch(r"[A-Za-z0-9_-]{16,}", d["undo"]), d
    pin = next(p for p in stack.ark.account.pins[WHO] if p["id"] == 31)
    assert pin["quantity"] == 2
    r = await member.http.post(
        "/lists/pins",
        data={"csrf": csrf, "action": "undo", "token": d["undo"]},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200 and r.json()["ok"], r.text
    pin = next(p for p in stack.ark.account.pins[WHO] if p["id"] == 31)
    assert pin["quantity"] == 1
