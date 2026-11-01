"""The words of a change on its review page, its chat card, History and the Undo page (review
F14): names in curly quotes, deck details by their names on the page with an arrow, a restore by
when the snapshot was taken (never its id), one spelling of "color", and the note about open
Archidekt tabs said on the change itself, not on every History and Proposals line."""

from __future__ import annotations

from mtg_gateway import modes, pages, tools_decks
from mtg_gateway.decks import BROWSER_CLIENT, OPEN_TABS_NOTE, current_client, row_line

from .conftest import Stack
from .test_approval_modes import _set_mode
from .test_deck_categories import SUB, _ready, _svc
from .test_decks_and_proxy import linked_user, structured
from .track_a_helpers import account_call


def test_a_new_deck_and_a_new_package_name_it_in_curly_quotes() -> None:
    deck = {"kind": "new_deck", "name": "TEST folder deck", "format": "commander", "cards": 0}
    assert row_line({**deck, "folder": "TEST folder", "private": True}) == (
        "New commander deck “TEST folder deck” in folder “TEST folder” (0 cards, private)"
    )
    package = {**deck, "name": "TEST package", "package": "for Commander", "private": True}
    assert row_line(package) == "New card package “TEST package” (0 cards, private; for Commander)"


async def test_a_new_folder_names_it_in_curly_quotes(stack: Stack) -> None:
    token = await linked_user(stack)
    await _set_mode(stack.h, "semi")
    p = structured(await account_call(stack.h, token, "propose_create_folder", {"name": "TEST folder"}))
    assert p["rows"][0]["text"] == "Create the folder “TEST folder” in your top level", p


def test_deck_details_read_as_deck_settings_names_them() -> None:
    def line(field: str, before: str, after: str, **more: object) -> str:
        return row_line({"kind": "detail", "field": field, "before": before, "after": after, **more})

    assert line("theorycrafted", "no", "yes") == "Theorycrafted: no → yes"
    assert line("game", "Any", "Paper") == "Game type: Any → Paper"
    assert line("format", "unknown", "commander") == "Format: unknown → commander"
    assert line("bracket", "none", "3") == "Commander bracket: none → 3"
    assert line("private", "no", "yes") == "Private: no → yes"
    assert line("unlisted", "no", "yes") == "Unlisted: no → yes"
    assert line("name", "“Old”", "“New”") == "Deck name: “Old” → “New”"
    assert line("folder", "Top level (no folder)", "Cube") == "Folder: Top level (no folder) → Cube"
    assert line("cover", "automatic", "Sol Ring") == "Cover image: automatic → Sol Ring"
    assert line("tags", "none", "budget", added=["budget"]) == "Deck tags: none → budget (adds budget)"
    assert row_line({"kind": "description", "after_len": 0}) == "Description: cleared"
    said = row_line({"kind": "description", "after_len": 5, "after_text": "Lands"})
    assert said == "Description: changed, 5 chars: “Lands”"
    for stale in ("->", "theorycrafted:", "game:", '"'):
        assert stale not in line("theorycrafted", "no", "yes") + line("game", "Any", "Paper") + said


def test_a_restore_is_named_by_when_its_snapshot_was_taken() -> None:
    r = {"kind": "restore", "snapshot_id": "L88v_4TZ90_2oZRv", "taken": "2026-10-31 21:30 UTC", "changes": 3}
    assert row_line(r) == "Put the deck back as it stood on 2026-10-31 21:30 UTC (3 changes)"
    body, _counts, _net = pages._change_rows([r], "")
    assert "L88v_4TZ90_2oZRv" not in body and "2026-10-31 21:30 UTC" in body and "3 changes" in body


def test_color_has_one_spelling_in_what_members_and_assistants_read() -> None:
    words = [
        modes.CATEGORY_COMMANDER_CONFIRM,
        *modes.CATEGORY_WORDS.values(),
        tools_decks.NO_COMMANDER_WARNING,
    ]
    assert not [w for w in words if "colour" in w]
    assert modes.category_words("move_commander", n=1).endswith("the deck's legality and its colors.")


async def test_the_open_tabs_note_is_said_on_the_change_not_on_every_listed_line(stack: Stack) -> None:
    await _ready(stack)
    svc = _svc(stack)
    for name in ("Removal", "Draw"):
        p = await svc.propose_categories(SUB, "42", [{"action": "create", "category": name}])
        assert p["diff"].endswith(OPEN_TABS_NOTE)  # the change itself says it, once
    current_client.set(BROWSER_CLIENT)
    listed = svc.list_proposals(SUB, full=True)
    assert len(listed) == 2
    for row in listed:
        assert OPEN_TABS_NOTE not in row["summary"] and OPEN_TABS_NOTE not in row["diff_text"], row
