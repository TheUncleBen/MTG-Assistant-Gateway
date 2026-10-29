"""The Applied page and Undo after the UI overhaul: the three headings as labels (Applied, Failed
and a grey Not sent), the "Applying" notice as a tinted strip that updates in place (no page
reload with scripts, the meta refresh only for browsers without), the Undo buttons opening a box
(the "Undo this change?" page stays as the no-script answer) and that page saying how many rows
its list leaves out past 200."""

from __future__ import annotations

import re

from mtg_gateway import pages

from .conftest import Stack, aged_stamp
from .test_track_a_pages import _add, _propose, _signed_in


def _look(n: int) -> dict:
    rows = [{"kind": "add", "qty": 1, "name": f"Card {i}"} for i in range(n)]
    return {
        "deck_name": "Mono Green",
        "nothing": False,
        "warning": "",
        "rows": rows,
        "fingerprint": "f" * 64,
        "snapshot_id": "snap-1",
    }


def test_the_undo_list_says_how_many_rows_it_leaves_out() -> None:
    assert pages.UNDO_ROWS_SHOWN == 200
    body = pages._undo_body(_look(200), "c", "s", ["p1"], 0)
    assert "<div class='card' id='undo-confirm'><h2>Mono Green</h2>" in body
    assert body.count("<li>") == 200 and "not shown" not in body
    body = pages._undo_body(_look(201), "c", "s", ["p1"], 0)
    assert body.count("<li>") == 200 and "<p class='muted'>1 more row is not shown.</p>" in body
    body = pages._undo_body(_look(205), "c", "s", ["p1"], 0)
    assert body.count("<li>") == 200 and "<p class='muted'>5 more rows are not shown.</p>" in body
    assert "<button type='submit' name='action' value='undo' class='primary btn-primary'" in body


def _run(state: str) -> dict:
    row = {"proposal_id": "p1", "summary": "+1 Opt", "deck_name": "Mono Green", "board_no": 1}
    return {
        "state": state,
        "applied": [row],
        "failed": [{**row, "proposal_id": "p2", "why": "Archidekt said no"}],
        "not_sent": [{**row, "proposal_id": "p3", "why": "held"}],
        "undo": [
            {
                "deck_id": "42",
                "deck_name": "Mono Green",
                "snapshot_id": "snap-1",
                "applied": ["p1"],
                "after_at": 7,
            }
        ],
    }


def test_the_result_card_uses_labels_a_strip_and_updates_in_place() -> None:
    body = pages._result_body(_run("applying"), "csrf-1")
    assert body.startswith("<div class='card' id='applied-result' aria-live='polite' data-applied-live>")
    assert re.search(
        r"<div class='notice'><span class='ui-lbl blue'>"
        r"(<span class='ui-spin' aria-hidden='true'></span>)Applying</span> One",
        body,
    )
    assert "it updates by itself.</div>" in body and "refreshes" not in body
    for colour, word in (("green", "Applied"), ("red", "Failed"), ("grey", "Not sent")):
        assert re.search(
            rf"<h2 class='ui-section'><span class='ui-lbl {colour}'>(<svg.*?</svg>)?{word}</span></h2>", body
        ), word
    assert "<h2>Not sent</h2>" not in body
    assert "<form method='post' action='/proposals/undo' class='undo' data-undo-box>" in body
    done = pages._result_body(_run("done"), "csrf-1")
    assert done.startswith("<div class='card' id='applied-result' aria-live='polite'>")
    assert "class='notice'" not in done


async def test_the_pages_carry_the_scripts_and_the_reload_only_without_them(stack: Stack) -> None:
    h = stack.h
    token, b = await _signed_in(stack)
    try:
        pid = await _propose(h, token, _add("Opt"))
        url = f"/proposals/{pid}"
        form = await b.review_form(url)
        r = await b.http.post(url, data={**form, "action": "apply"})
        assert r.headers["location"] == f"{url}?ok=applied"
        page = (await b.http.get(url)).text
        assert "/static/undo-box.js" in page
        m = re.search(
            r"<form method='post' action='/proposals/undo' class='undo' data-undo-box>(.*?)</form>", page
        )
        assert m and "<button class='btn-plain'>Undo this change</button>" in m.group(1)
        fields = dict(re.findall(r"name='([a-z_]+)' value='([^']*)'", m.group(1)))
        # the preview (what the box shows): the card the script takes, with the guarded form
        r = await b.http.post("/proposals/undo", data=fields)
        assert r.status_code == 200 and "<div class='card' id='undo-confirm'><h2>" in r.text
        assert "name='action' value='undo'" in r.text and "<title>Undo this change?" in r.text
        # the Applied page of a batch: the card's id, no meta refresh outside <noscript>
        csrf = await b.csrf("/proposals")
        pid2 = await _propose(h, token, _add("Delver of Secrets"))
        r = await b.http.post(
            "/proposals/select",
            data={"csrf": csrf, "action": "apply", "pick": pid2, f"v_{pid2}": "1", "back": "/proposals"},
        )
        assert r.status_code == 200, r.text
        hidden = dict(re.findall(r"name='([a-zA-Z_0-9-]+)' value='([^']*)'", r.text))
        hidden["shown"] = aged_stamp(h.settings, "select:user-1")  # seen long enough for the guard
        r = await b.http.post("/proposals/apply", data=hidden)
        assert r.status_code == 303 and r.headers["location"].startswith("/proposals/applied/"), r.text
        page = (await b.http.get(r.headers["location"])).text
        assert "<div class='card' id='applied-result' aria-live='polite'>" in page
        assert "http-equiv='refresh'" not in page and "/static/undo-box.js" in page
        assert "data-undo-box" in page and "/static/applied-live.js" not in page
    finally:
        await b.aclose()
