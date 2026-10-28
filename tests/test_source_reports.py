"""Every simulation is a report (owner feedback F1, 0.7.46): a pasted list, a waiting proposal's
resulting deck and a simulated comparison are kept as the member's reports, labelled by what they
were run on, listed on Reports and History, linked from the deck or the proposal, and never counted
in a deck's own trend. Forge plays a pasted list by the same rules as a stored deck."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mtg_gateway.db import Database
from mtg_gateway.decks import DeckError
from mtg_gateway.reports import PROPOSAL_NOT_WAITING, ReportService

from .conftest import Stack, stack_with
from .fake_mystic_forge import SIM_CALLS
from .test_companion import COMPANION, NAV
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .test_forge import FakeForge, _Decks

stack_options = stack_with(COMPANION)

LIST = "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n// Lands\n99 Island\n"


async def _browser(stack: Stack) -> Browser:
    b = Browser(stack.h)
    await b.login("/account")
    return b


async def test_a_proposals_resulting_deck_is_simulated_and_kept_under_its_deck(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    changes = [
        {"action": "add", "card_name": "Arcane Signet", "quantity": 1, "category": "Ramp"},
        {"action": "remove", "card_name": "Acidic Slime"},
        {"action": "set_quantity", "card_name": "Forest", "quantity": 14},
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert p["ok"] and p["state"] == "pending", p
    own = structured(await call(h, token, "run_deck_report", {"deck_ref": "42", "games": 20}))
    assert own["ok"], own
    SIM_CALLS.clear()
    rep = structured(await call(h, token, "run_deck_report", {"proposal_id": p["proposal_id"], "games": 20}))
    assert rep["ok"] and rep["stored"] is True and rep["deck_id"] == "42", rep
    src = rep["source"]
    assert src["kind"] == "proposal" and src["proposal_id"] == p["proposal_id"], src
    assert src["label"] == f"Proposal #{p['board_no']} for “{p['deck_name']}”", src
    # the simulation played the deck as the proposal would leave it, nothing was applied
    ((tool, sent),) = [c for c in SIM_CALLS if c[0] == "goldfish_run"]
    deck = str(sent["deck"])
    assert "Arcane Signet" in deck and "Acidic Slime" not in deck and "14 Forest" in deck, deck
    assert stack.ark.patches == []
    # the deck's own trend and its next report never count it; its list of reports has it
    gw = h.app.state.gateway
    sub = gw.db._one("SELECT sub FROM users", ())["sub"]
    assert [x["report_id"] for x in gw.reports.series(sub, "42")] == [own["report_id"]]
    listed = structured(await call(h, token, "list_deck_reports", {"deck_id": "42"}))["reports"]
    assert {x["report_id"] for x in listed} == {own["report_id"], rep["report_id"]}
    # the proposal's page links its report; the Reports and History pages label it
    b = await _browser(stack)
    try:
        page = (await b.http.get(f"/proposals/{p['proposal_id']}", headers=NAV)).text
        assert f"href='/history/reports/{rep['report_id']}'" in page, page
        reports = (await b.http.get("/reports", headers=NAV)).text
        assert f"<span class='ui-lbl blue src'>Proposal #{p['board_no']} for" in reports
        history = (await b.http.get("/history", headers=NAV)).text
        assert f"/history/reports/{rep['report_id']}" in history and "Proposal #" in history
        report = (await b.http.get(f"/history/reports/{rep['report_id']}", headers=NAV)).text
        assert f"href='/proposals/{p['proposal_id']}'" in report and "Proposal #" in report
    finally:
        await b.aclose()
    # once it is no longer waiting, its resulting deck is not simulated
    structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    late = structured(await call(h, token, "run_deck_report", {"proposal_id": p["proposal_id"], "games": 20}))
    assert late["ok"] is False and late["message"] == PROPOSAL_NOT_WAITING, late
    nope = structured(await call(h, token, "run_deck_report", {"proposal_id": "nope", "games": 20}))
    assert nope["ok"] is False and nope["error"] == "not_found", nope
    both = structured(
        await call(h, token, "run_deck_report", {"deck_ref": "42", "proposal_id": "nope", "games": 20})
    )
    assert both["ok"] is False and "not both" in both["message"], both


async def test_a_pasted_list_is_listed_on_reports_and_history_with_no_deck(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": LIST, "games": 20}))
    assert rep["ok"] and rep["stored"] and rep["deck_id"] == "", rep
    b = await _browser(stack)
    try:
        reports = (await b.http.get("/reports", headers=NAV)).text
        table = reports.split("class='ui-table rp-t'", 1)[1]
        row = table.split(f"/history/reports/{rep['report_id']}'", 1)[1].split("<div class='tr'>", 1)[0]
        assert "<span class='ui-lbl blue src'>Pasted list</span>" in row, row
        assert "data-deck=" not in table  # no deck to open from its menu
        # the Latest report widget offers no deck trend for it, and Decks tested counts no deck
        assert "deck_id=&amp;type=reports" not in reports and "0<small>decks · 1 report" in reports
        report = (await b.http.get(f"/history/reports/{rep['report_id']}", headers=NAV)).text
        main = report.split("<main", 1)[1]
        assert "href='/decks/'" not in main and "href='/history?deck_id='" not in main, main
        assert "Pasted list" in main
        history = (await b.http.get("/history", headers=NAV)).text
        assert f"/history/reports/{rep['report_id']}" in history
    finally:
        await b.aclose()


async def test_a_simulated_comparison_is_kept_as_a_comparison_report(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    other = LIST.replace("99 Island", "98 Island\n1 Opt")
    out = structured(
        await call(h, token, "compare_decks", {"a": "42", "b_list": other, "simulate": True, "games": 20})
    )
    assert out["ok"] and out["goldfish_ab"]["ok"] and out["stored"] is True, out
    rep = structured(await call(h, token, "get_deck_report", {"report_id": out["report_id"]}))
    assert rep["ok"] and rep["deck_id"] == "42", rep
    assert rep["source"]["kind"] == "compare" and rep["source"]["label"] == "Comparison", rep["source"]
    assert rep["deck_name"].endswith(" vs Pasted list"), rep["deck_name"]
    assert rep["goldfish"] is None and "## Deltas" in rep["comparison"]["text"], rep
    # a comparison that is not simulated is a diff, not a run: nothing is kept
    before = len(structured(await call(h, token, "list_deck_reports", {}))["reports"])
    plain = structured(await call(h, token, "compare_decks", {"a": "42", "b_list": other}))
    assert plain["ok"] and "report_id" not in plain
    assert len(structured(await call(h, token, "list_deck_reports", {}))["reports"]) == before
    b = await _browser(stack)
    try:
        page = (await b.http.get(f"/history/reports/{out['report_id']}", headers=NAV)).text
        assert "id='rv-compare'" in page and "Comparison" in page, page
    finally:
        await b.aclose()


async def test_a_read_only_app_compares_but_keeps_no_report(stack: Stack) -> None:
    """compare_decks stays open to a read-only app (mtg.read); only a full-scope app stores the
    simulated comparison as a report."""
    from .test_round4_data import _app_token

    h = stack.h
    await linked_user(stack)
    token, _cid = await _app_token(h, "reader", scope="mtg.read")
    other = LIST.replace("99 Island", "98 Island\n1 Opt")
    out = structured(
        await call(h, token, "compare_decks", {"a": "42", "b_list": other, "simulate": True, "games": 20})
    )
    assert out["ok"] and out["goldfish_ab"]["ok"], out
    assert out["stored"] is False and "report_id" not in out, out
    sub = h.app.state.gateway.db._one("SELECT sub FROM users", ())["sub"]
    assert h.app.state.gateway.reports.list(sub, None) == []


async def test_a_read_only_app_runs_no_report_of_a_list_or_a_proposal(stack: Stack) -> None:
    """run_deck_report is one of the write tools: a read-only app (mtg.read) is refused it before
    it runs, so neither a pasted list's report nor a proposal's is kept for it."""
    from .test_round4_data import _app_token

    h = stack.h
    full = await linked_user(stack)
    p = structured(
        await call(
            h,
            full,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Opt"}]},
        )
    )
    token, _cid = await _app_token(h, "reader", scope="mtg.read")
    for args in ({"deck_ref": LIST, "games": 20}, {"proposal_id": p["proposal_id"], "games": 20}):
        out = await call(h, token, "run_deck_report", args)
        assert out.get("isError") or structured(out).get("ok") is False, out
    sub = h.app.state.gateway.db._one("SELECT sub FROM users", ())["sub"]
    assert h.app.state.gateway.reports.list(sub, None) == []


async def test_forge_plays_a_pasted_list_as_it_plays_a_deck(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    text = "1 Liesa, Forgotten Archangel *CMDR*\n1 Sol Ring\n98 Plains\n"
    out = await reports.run_text("alice", text, games=300)
    assert out["stored"] and out["source"]["label"] == "Pasted list"
    for _ in range(100):
        if fake.started:
            break
        await asyncio.sleep(0.02)
    assert len(fake.started) == 1, fake.started
    assert "Liesa, Forgotten Archangel" in json.dumps(fake.started[0])
    assert reports.get("alice", out["report_id"])["forge"]["state"] in ("running", "queued")
    # a member's report is theirs alone
    db.upsert_user("bob", email=None, name=None, preferred_username=None, groups=[])
    try:
        reports.get("bob", out["report_id"])
    except DeckError as exc:
        assert exc.kind == "not_found"
    else:
        raise AssertionError("another member read the report")


async def test_reports_of_lists_and_proposals_go_before_a_decks_own_reports(
    tmp_path: Path, monkeypatch
) -> None:
    """At the cap of reports kept per member, the oldest report of a pasted list, a proposal or a
    comparison goes first, so they never crowd out a deck's own reports (its trend)."""
    from mtg_gateway import reports as reports_mod

    monkeypatch.setattr(reports_mod, "MAX_REPORTS_PER_USER", 3)
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    reports = ReportService(db, _Decks(), None)  # type: ignore[arg-type]
    reports.min_interval = 0
    first = (await reports.run("alice", "42", simulate=False))["report_id"]
    lists = [(await reports.run_text("alice", LIST, simulate=False))["report_id"] for _ in range(2)]
    second = (await reports.run("alice", "42", simulate=False, options={"seed": 7}))["report_id"]
    kept = {r["report_id"] for r in reports.list("alice", None)}
    assert kept == {first, lists[1], second}, kept
    # a deck's own reports are pruned oldest first once no other report is left to go
    more = [
        (await reports.run("alice", "42", simulate=False, options={"seed": n}))["report_id"] for n in (8, 9)
    ]
    kept = {r["report_id"] for r in reports.list("alice", None)}
    assert kept == {second, *more}, kept
