"""The proposals listings ask the database a fixed number of times for the links between proposals
and for the connected apps' names, however many rows they show (Database.read_ahead): the
proposals page (proposals_view.gather), list_proposals and the waiting board (board_lines) used
to run proposal_links three times and the two client tables twice for every row."""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from typing import Any

import pytest

from mtg_gateway import proposals_view

from .test_history_page import seed_history

pytestmark = pytest.mark.anyio


@contextmanager
def _counted(db: Any):
    statements: list[str] = []
    db._conn.set_trace_callback(lambda s: statements.append(re.sub(r"\s+", " ", s)))
    try:
        yield statements
    finally:
        db._conn.set_trace_callback(None)


def _links(statements: list[str]) -> int:
    # reads of the links table (the board's hold sweep updates proposals with a link subquery; that
    # is a fixed few statements per request, not one per row)
    return sum(1 for s in statements if s.startswith("SELECT") and "FROM proposal_links" in s)


def _clients(statements: list[str]) -> int:
    return sum(1 for s in statements if "FROM oauth_clients" in s or "FROM cimd_clients" in s)


async def _signed_in(stack) -> str:
    db = stack.h.db
    db.upsert_user("user-1", email="a@example.test", name="Alice", preferred_username="alice", groups=[])
    db.save_link("user-1", username="alice", user_id="77", secret_enc="enc")
    db.save_client("app-1", {"client_name": "Claude"})
    seed_history(db, "user-1", now=int(time.time()))  # 60 proposals, 15 of them waiting
    return "user-1"


async def test_the_proposals_page_reads_links_and_app_names_once_for_any_number_of_rows(stack) -> None:
    sub = await _signed_in(stack)
    db, gw = stack.h.db, stack.gw
    with _counted(db) as all_rows:
        page = proposals_view.gather(gw, sub)
    assert len(page["items"]) == 60 and len(page["waiting_order"]) == 15
    assert _links(all_rows) == 1 and _clients(all_rows) == 2, (_links(all_rows), _clients(all_rows))
    # fewer rows: the same number of these queries (one for the links, one per client table)
    for pid in [it["id"] for it in page["items"]][:40]:
        with db.tx() as c:
            c.execute("DELETE FROM proposals WHERE id = ?", (pid,))
    with _counted(db) as fewer:
        page = proposals_view.gather(gw, sub)
    assert len(page["items"]) == 20
    assert _links(fewer) == _links(all_rows) and _clients(fewer) == _clients(all_rows)


async def test_list_proposals_and_the_board_read_links_and_names_once(stack) -> None:
    sub = await _signed_in(stack)
    db, decks = stack.h.db, stack.gw.decks
    with _counted(db) as many:
        rows = decks.list_proposals(sub, limit=60)
    assert len(rows) == 60 and all(r["created_by"] for r in rows)
    with _counted(db) as few:
        assert len(decks.list_proposals(sub, limit=5)) == 5
    assert _links(many) == _links(few) == 1 and _clients(many) == _clients(few) == 2
    with _counted(db) as board:
        lines = decks.board_lines(sub)
    assert len(lines) == 15
    assert _links(board) == 1 and _clients(board) == 2, (_links(board), _clients(board))


async def test_a_link_written_inside_a_read_ahead_is_seen_by_the_next_lookup(stack) -> None:
    """The read-ahead is dropped when a link is written inside its block, so a listing that
    links two proposals and asks again reads the link it just made."""
    sub = await _signed_in(stack)
    db = stack.h.db
    a, b = [r["id"] for r in db.proposal_index(sub)][:2]
    with db.read_ahead(sub, [a, b], ["app-1"]):
        assert db.proposal_links(sub, [a]) == []
        assert db.link_proposals(a, sub, "depends_on", b)
        kinds = [lk["kind"] for lk in db.proposal_links(sub, [a])]
    assert kinds == ["depends_on"]
    assert db.client_name("app-1") == "Claude"


async def test_a_read_ahead_of_more_than_500_proposals_still_answers_the_last_ones(stack) -> None:
    """proposal_links reads at most 500 ids at once: the read-ahead reads in slices of that size,
    so a long listing's 501st row still shows its links instead of "no links"."""
    sub = await _signed_in(stack)
    db = stack.h.db
    a, b = [r["id"] for r in db.proposal_index(sub)][:2]
    assert db.link_proposals(a, sub, "depends_on", b)
    many = [f"old-{i}" for i in range(500)] + [a, b]
    with db.read_ahead(sub, many):
        assert [lk["kind"] for lk in db.proposal_links(sub, [a])] == ["depends_on"]
        assert [lk["kind"] for lk in db.proposal_links(sub, [b])] == ["depends_on"]
