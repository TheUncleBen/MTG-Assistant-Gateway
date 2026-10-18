"""Play more games: another round of Forge games for a report whose games have
ended, at the same table, added to its earlier games; refused with a reason when it can't start."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from mtg_gateway import reports as reports_module
from mtg_gateway.db import Database
from mtg_gateway.decks import DeckError
from mtg_gateway.report_view import forge_html, more_games_html
from mtg_gateway.reports import (
    MAX_REPORT_FORGE_GAMES,
    MORE_FULL,
    MORE_NO_FORGE,
    MORE_NOT_ENDED,
    MORE_NOT_THIS_ONE,
    MORE_PAIR_CANT,
    MORE_PAIR_QUEUE,
    MORE_PAIR_WAIT,
    MORE_QUEUE_FULL,
    ReportService,
    more_room,
)

from .test_forge import BRACKETED, FakeForge, _Decks, _PairDecks


def _service(tmp_path: Path, fake: FakeForge | None) -> tuple[Database, ReportService]:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    reports = ReportService(db, _Decks(), None, forge=fake.client() if fake else None, forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    return db, reports


async def _until(check: Any) -> None:
    for _ in range(300):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("never happened")


async def _finished_report(reports: ReportService, fake: FakeForge, winners: list[int], seed: int = 7) -> str:
    out = await reports.run("alice", "42", games=300, options={"seed": seed})
    fake.finish(out["forge"]["job_id"], winners)
    rid = out["report_id"]
    await _until(lambda: reports.get("alice", rid)["forge"]["state"] == "done")
    return rid


async def test_more_games_play_the_same_table_and_count_with_the_earlier_ones(tmp_path: Path) -> None:
    fake = FakeForge()
    db, reports = _service(tmp_path, fake)
    rid = await _finished_report(reports, fake, [1, 2, 1, 1])
    done = reports.get("alice", rid)["forge"]
    assert done["more_games"] == 4
    assert not {"deck_text", "played", "earlier", "previous"} & done.keys()  # kept inside
    first = fake.started[0]
    after = await reports.play_more("alice", rid)
    assert len(fake.started) == 2
    again = fake.started[1]
    assert again["games"] == 4 and again["decks"] == first["decks"]  # same deck, same opponents
    assert again["seed"] != 7  # a fresh seed, so these are new games
    # while they play, the report still shows the earlier games
    playing = after["forge"]
    assert playing["state"] == "running" and playing["result"]["games"] == 4
    assert playing["games_requested"] == 8 and playing["seats"] == done["seats"]
    fake.finish(playing["job_id"], [2, 2, 3, 1])
    await _until(lambda: reports.get("alice", rid)["forge"]["state"] == "done")
    final = reports.get("alice", rid)["forge"]
    result = final["result"]
    assert result["games"] == 8 and result["games_requested"] == 8
    assert [s["wins"] for s in result["seats"]] == [4, 3, 1, 0]
    assert result["seats"][0]["win_rate"] == 0.5
    assert [r["seed"] for r in final["earlier_runs"]] == [7, playing["seed"]]
    assert [r["games"] for r in final["earlier_runs"]] == [4, 4]
    stored = json.loads(db._one("SELECT forge_json FROM reports WHERE id = ?", (rid,))["forge_json"])
    assert [g["game"] for g in stored["played"]] == list(range(1, 9))  # numbered after the earlier ones
    assert final["more_games"] == 4
    audit = db._one("SELECT detail_json FROM audit_log WHERE event = 'report_more_games'", ())
    assert json.loads(audit["detail_json"]) == {"report_id": rid, "games": 4}


async def test_more_games_wait_behind_the_members_running_run(tmp_path: Path) -> None:
    fake = FakeForge()
    _, reports = _service(tmp_path, fake)
    rid = await _finished_report(reports, fake, [1, 1, 1, 1])
    other = await reports.run("alice", "43", games=300, options={"seed": 2})
    assert other["forge"]["state"] == "running" and len(fake.started) == 2
    waiting = (await reports.play_more("alice", rid))["forge"]
    assert waiting["state"] == "queued" and waiting["position"] == 1
    assert waiting["result"]["games"] == 4  # the earlier games stay on show
    assert len(fake.started) == 2
    fake.finish(other["forge"]["job_id"], [1])
    await _until(lambda: len(fake.started) == 3)
    assert fake.started[2]["decks"] == fake.started[0]["decks"] and fake.started[2]["games"] == 4
    assert reports.get("alice", rid)["forge"]["state"] == "running"
    reports._forge_task.cancel()


async def test_a_full_queue_starts_nothing_and_keeps_the_report(tmp_path: Path) -> None:
    fake = FakeForge()
    db, reports = _service(tmp_path, fake)
    rid = await _finished_report(reports, fake, [1, 2, 3, 4])
    before = db._one("SELECT forge_json FROM reports WHERE id = ?", (rid,))["forge_json"]
    await reports.run("alice", "43", games=300, options={"seed": 1})  # plays
    await reports.run("alice", "42", games=300, options={"seed": 2})  # waits
    await reports.run("alice", "42", games=300, options={"seed": 3})  # waits
    started = len(fake.started)
    with pytest.raises(DeckError) as refused:
        await reports.play_more("alice", rid)
    assert str(refused.value) == MORE_QUEUE_FULL and len(fake.started) == started
    assert db._one("SELECT forge_json FROM reports WHERE id = ?", (rid,))["forge_json"] == before
    reports._forge_task.cancel()


async def test_refusals_say_why(tmp_path: Path) -> None:
    fake = FakeForge()
    db, reports = _service(tmp_path, fake)
    running = await reports.run("alice", "42", games=300, options={"seed": 1})
    with pytest.raises(DeckError, match=MORE_NOT_ENDED):
        await reports.play_more("alice", running["report_id"])
    with pytest.raises(DeckError) as missing:
        await reports.play_more("alice", "nope")
    assert missing.value.kind == "not_found"
    with pytest.raises(DeckError) as theirs:  # someone else's report
        await reports.play_more("bob", running["report_id"])
    assert theirs.value.kind == "not_found"
    # a report from before the gateway kept the deck it played: nothing to add to
    old = {"state": "done", "seats": ["Liesa", "A", "B", "C"], "result": {"games": 4}}
    with db.tx() as c:
        c.execute("UPDATE reports SET forge_json = ? WHERE id = ?", (json.dumps(old), running["report_id"]))
    with pytest.raises(DeckError, match=MORE_NOT_THIS_ONE):
        await reports.play_more("alice", running["report_id"])
    assert reports.get("alice", running["report_id"])["forge"]["more_games"] == 0
    _, plain = _service(tmp_path / "plain", None)
    with pytest.raises(DeckError, match=MORE_NO_FORGE):
        await plain.play_more("alice", running["report_id"])
    reports._forge_task.cancel()


def test_more_room_caps_a_report_at_its_most_games() -> None:
    def section(n: int, state: str = "done") -> dict[str, Any]:
        played = [{"game": i + 1, "winner": 1} for i in range(n)]
        return {"state": state, "played": played, "deck_text": "1 X", "seats": ["X", "Y"]}

    assert more_room(section(4), 4) == (4, None)
    assert more_room(section(MAX_REPORT_FORGE_GAMES - 2), 4) == (2, None)
    assert more_room(section(MAX_REPORT_FORGE_GAMES), 4) == (0, MORE_FULL)
    assert more_room(section(4, "running"), 4) == (0, MORE_NOT_ENDED)
    assert more_room(section(4, "failed"), 4) == (4, None)  # the games it did play count
    assert more_room(section(0), 4) == (0, MORE_NOT_THIS_ONE)
    assert more_room(None, 4) == (0, MORE_NOT_THIS_ONE)
    assert more_room({}, 4) == (0, MORE_NOT_THIS_ONE)  # a report with no Forge games at all


async def test_opponents_gone_from_forge_keep_the_earlier_games(tmp_path: Path) -> None:
    fake = FakeForge()
    _, reports = _service(tmp_path, fake)
    rid = await _finished_report(reports, fake, [1, 1, 2, 2])
    fake.brackets = {"Another Precon": 3, "Precon 1": 3}  # Precon 2 and 3 left this Forge
    after = (await reports.play_more("alice", rid))["forge"]
    assert len(fake.started) == 1
    assert after["state"] == "done" and after["result"]["games"] == 4
    assert after["more_note"] == (
        "Some of this report's opponents are no longer in this gateway's Forge (Precon 2, Precon 3), so "
        "more games can't be played at the same table. Run the report (or its comparison) again."
    )
    html = forge_html(after)
    assert "The extra games did not start. Some of this report&#x27;s opponents" in html
    # pressing again once they are back clears the note
    fake.brackets = None
    again = (await reports.play_more("alice", rid))["forge"]
    assert again["state"] == "running" and "more_note" not in again
    reports._forge_task.cancel()


async def test_an_engine_failure_keeps_the_earlier_games(tmp_path: Path, monkeypatch: Any) -> None:
    fake = FakeForge()
    _, reports = _service(tmp_path, fake)
    rid = await _finished_report(reports, fake, [1, 1, 2, 2])

    async def failed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"state": "failed", "error": "Forge gave an unexpected answer."}

    monkeypatch.setattr(reports_module, "start_run", failed)
    after = (await reports.play_more("alice", rid))["forge"]
    assert after["state"] == "done" and after["result"]["games"] == 4
    assert after["more_note"] == "Forge gave an unexpected answer." and after["more_games"] == 4


def test_the_button_shows_only_when_more_games_can_be_added() -> None:
    assert (
        more_games_html({"state": "done", "more_games": 0}, "tok", "rep_1") == "<div id='forge-more'></div>"
    )
    assert more_games_html(None, "tok", "rep_1") == "<div id='forge-more'></div>"
    html = more_games_html({"state": "done", "more_games": 4}, "tok", "rep_1")
    assert "action='/history/reports/rep_1/forge/more'" in html and "value='tok'" in html
    assert "Play more games</button>" in html
    assert "Plays 4 more Forge games against the same opponents" in html
    one = more_games_html({"more_games": 1}, "tok", "rep_1")
    assert "Plays 1 more Forge game against the same opponents and adds it to the results above" in one


async def _finished_pair(reports: ReportService, fake: FakeForge) -> tuple[str, str]:
    """A Forge comparison's two reports (compare_with, then same_opponents_as), both finished."""
    first = await reports.run("alice", "42", simulate=False, forge=True, pair_with="43")
    second = await reports.run(
        "alice", "43", simulate=False, forge=True, same_opponents_as=first["report_id"]
    )
    fake.finish(first["forge"]["job_id"], [1, 2, 1, 1])
    await _until(lambda: len(fake.started) == 2)
    fake.finish(reports.get("alice", second["report_id"])["forge"]["job_id"], [2, 2, 1, 3])
    await _until(lambda: reports.get("alice", second["report_id"])["forge"]["state"] == "done")
    return first["report_id"], second["report_id"]


async def test_a_comparison_gets_more_games_on_both_decks(tmp_path: Path) -> None:
    """A safe default: more games for one deck of a comparison go to both, so
    the two stay even; pressed on either deck."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge(brackets=BRACKETED)
    reports = ReportService(db, _PairDecks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    a, b = await _finished_pair(reports, fake)
    view_a, view_b = reports.get("alice", a)["forge"], reports.get("alice", b)["forge"]
    assert (view_a["more_games"], view_a["more_with"]) == (4, "Deck 43")
    assert (view_b["more_games"], view_b["more_with"]) == (4, "Deck 42")
    html = more_games_html(view_b, "tok", b)
    assert (
        "Plays 4 more Forge games for this deck and 4 for Deck 42, the other deck of this comparison" in html
    )
    await reports.play_more("alice", b)  # pressed on the second deck: the first deck starts first
    assert len(fake.started) == 3  # one place in this Forge: the second deck's games wait
    assert reports.get("alice", a)["forge"]["state"] == "running"
    assert reports.get("alice", b)["forge"]["state"] == "queued"
    fake.finish(reports.get("alice", a)["forge"]["job_id"], [1, 1, 2, 2])
    await _until(lambda: len(fake.started) == 4)
    table = fake.started[0]["decks"][1:]
    assert fake.started[2]["decks"][0] == fake.started[0]["decks"][0]  # deck 42 first
    assert fake.started[3]["decks"][0] == fake.started[1]["decks"][0]  # then deck 43
    assert fake.started[2]["decks"][1:] == table and fake.started[3]["decks"][1:] == table
    assert fake.started[2]["games"] == fake.started[3]["games"] == 4
    assert reports.get("alice", a)["forge"]["result"]["games"] == 8
    audit = db._one("SELECT detail_json FROM audit_log WHERE event = 'report_more_games'", ())
    assert json.loads(audit["detail_json"]) == {"report_id": b, "games": 4, "with": a}
    reports._forge_task.cancel()


async def test_a_comparison_waits_for_both_and_needs_room_for_both(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge(brackets=BRACKETED)
    reports = ReportService(db, _PairDecks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    first = await reports.run("alice", "42", simulate=False, forge=True, pair_with="43")
    second = await reports.run(
        "alice", "43", simulate=False, forge=True, same_opponents_as=first["report_id"]
    )
    fake.finish(first["forge"]["job_id"], [1, 1, 1, 1])
    await _until(lambda: reports.get("alice", first["report_id"])["forge"]["state"] == "done")
    # the other deck's games are still playing
    assert reports.get("alice", first["report_id"])["forge"]["more_games"] == 0
    with pytest.raises(DeckError, match=MORE_PAIR_WAIT):
        await reports.play_more("alice", first["report_id"])
    await _until(lambda: len(fake.started) == 2)
    fake.finish(reports.get("alice", second["report_id"])["forge"]["job_id"], [1, 1, 1, 1])
    await _until(lambda: reports.get("alice", second["report_id"])["forge"]["state"] == "done")
    # one run playing and one waiting: no room for both decks' games, so neither starts
    await reports.run("alice", "44", simulate=False, forge=True)
    await reports.run("alice", "45", simulate=False, forge=True)
    started = len(fake.started)
    with pytest.raises(DeckError, match=MORE_PAIR_QUEUE):
        await reports.play_more("alice", second["report_id"])
    assert len(fake.started) == started
    assert reports.get("alice", second["report_id"])["forge"]["result"]["games"] == 4
    reports._forge_task.cancel()


async def test_a_comparison_whose_other_report_is_gone_or_full(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge(brackets=BRACKETED)
    reports = ReportService(db, _PairDecks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    a, b = await _finished_pair(reports, fake)
    row = json.loads(db._one("SELECT forge_json FROM reports WHERE id = ?", (a,))["forge_json"])
    full = {**row, "played": [{"game": i + 1, "winner": 1} for i in range(MAX_REPORT_FORGE_GAMES)]}
    with db.tx() as c:
        c.execute("UPDATE reports SET forge_json = ? WHERE id = ?", (json.dumps(full), a))
    with pytest.raises(DeckError, match=MORE_PAIR_CANT):
        await reports.play_more("alice", b)
    with db.tx() as c:  # the other deck's report deleted: just this one
        c.execute("DELETE FROM reports WHERE id = ?", (a,))
    view = (await reports.play_more("alice", b))["forge"]
    assert view["state"] in ("running", "queued") and len(fake.started) == 3
    reports._forge_task.cancel()


async def test_a_later_report_at_the_same_table_never_replaces_the_partner(tmp_path: Path) -> None:
    """The partner is recorded when the comparison is made, so a later report seating
    the first deck's opponents is a report of its own, not the new partner."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge(brackets=BRACKETED)
    reports = ReportService(db, _PairDecks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    a, b = await _finished_pair(reports, fake)
    later = await reports.run("alice", "44", simulate=False, forge=True, same_opponents_as=a)
    fake.finish(later["forge"]["job_id"], [1, 1, 1, 1])
    c = later["report_id"]
    await _until(lambda: reports.get("alice", c)["forge"]["state"] == "done")
    assert reports.get("alice", a)["forge"]["more_with"] == "Deck 43"
    assert reports.get("alice", b)["forge"]["more_with"] == "Deck 42"
    alone = reports.get("alice", c)["forge"]
    assert alone["more_games"] == 4 and "more_with" not in alone
    await reports.play_more("alice", c)  # only its own games
    assert len(fake.started) == 4  # one run: the comparison's two reports are left alone
    assert reports.get("alice", a)["forge"]["state"] == reports.get("alice", b)["forge"]["state"] == "done"
    reports._forge_task.cancel()


async def test_the_first_deck_is_taken_back_when_the_second_does_not_start(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The second deck's extra games failing to start (here when its turn comes, after
    the first deck's) takes the first deck's back too, so the two stay even."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge(brackets=BRACKETED)
    reports = ReportService(db, _PairDecks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    a, b = await _finished_pair(reports, fake)
    real = reports_module.start_run

    async def second_fails(client: Any, text: str, name: str, **kw: Any) -> dict[str, Any]:
        if name == "Deck 43":
            return {"state": "failed", "error": "Forge gave an unexpected answer."}
        return await real(client, text, name, **kw)

    monkeypatch.setattr(reports_module, "start_run", second_fails)
    await reports.play_more("alice", a)
    assert reports.get("alice", b)["forge"]["state"] == "queued"  # waits for the first deck's
    fake.finish(reports.get("alice", a)["forge"]["job_id"], [1, 1, 1, 1])
    await _until(lambda: reports.get("alice", b)["forge"].get("more_note"))
    after_a, after_b = reports.get("alice", a)["forge"], reports.get("alice", b)["forge"]
    assert after_a["result"]["games"] == after_b["result"]["games"] == 4
    assert after_a["more_note"] == after_b["more_note"] == "Forge gave an unexpected answer."
    assert after_a["state"] == after_b["state"] == "done"
    reports._forge_task.cancel()


async def test_a_retry_on_a_busy_forge_keeps_the_runs_place(tmp_path: Path) -> None:
    """Each retry against a busy Forge gave the waiting run a new queue time, so the
    member's waiting runs could swap places. It keeps the first one."""
    from .test_forge import _BusyForge

    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = _BusyForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    out = await reports.run("alice", "42", games=300, options={"seed": 9})
    rid = out["report_id"]
    stored = json.loads(db._one("SELECT forge_json FROM reports WHERE id = ?", (rid,))["forge_json"])
    with db.tx() as c:
        c.execute(
            "UPDATE reports SET forge_json = ? WHERE id = ?", (json.dumps({**stored, "queued_at": 1000}), rid)
        )
    before = fake.refused
    await _until(lambda: fake.refused >= before + 3)
    again = json.loads(db._one("SELECT forge_json FROM reports WHERE id = ?", (rid,))["forge_json"])
    assert again["engine_busy"] is True and again["queued_at"] == 1000
    reports._forge_task.cancel()


async def test_an_undo_while_the_refresher_waits_on_forge_is_not_overwritten(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The refresher reads a run, then waits on Forge; an undo saved meanwhile stays, and the
    refresher does not put the taken-back games' run over it."""
    fake = FakeForge()
    db, reports = _service(tmp_path, fake)
    rid = await _finished_report(reports, fake, [1, 2, 1, 1])
    before = reports._own_forge("alice", rid)[1]
    playing = (await reports.play_more("alice", rid))["forge"]
    real, undone = reports_module.refresh, []

    async def slow_refresh(forge: Any, section: dict[str, Any], **kw: Any) -> dict[str, Any]:
        fresh = await real(forge, section, **kw)
        if section.get("job_id") == playing["job_id"] and not undone:
            reports._undo_more("alice", rid, before, "taken back")  # lands while Forge answered
            undone.append(True)
        return {**fresh, "state": "running", "progress_marker": True}

    monkeypatch.setattr(reports_module, "refresh", slow_refresh)
    reports._ensure_forge_refresher()
    await _until(lambda: undone)
    await asyncio.sleep(0.2)  # a few more refresher rounds
    stored = reports._own_forge("alice", rid)[1]
    assert stored["more_note"] == "taken back" and stored["state"] == "done"
    assert "progress_marker" not in stored
    assert stored["job_id"] == before["job_id"]
