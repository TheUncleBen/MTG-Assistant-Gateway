"""What a test run keeps and shows (owner decision C7): a report keeps the exact list it played,
says when the deck has changed since and can put that list back through an ordinary proposal; it
compares itself with the deck's test before; a proposal is tested against the deck as it is, game
for game; the website starts every kind of test an assistant can; a few Forge games do not lead;
History is the decks' timeline and a test's one home is its report page; a read-only app's
comparison is kept; a Forge pair is shown head to head and stays out of the deck's trend."""

from __future__ import annotations

import json
import re

from mtg_gateway import report_view as rv
from mtg_gateway import reports as reports_module

from .conftest import Stack, stack_with
from .fake_mystic_forge import SIM_CALLS
from .test_companion import COMPANION, NAV, linked_browser
from .test_decks_and_proxy import call, linked_user, structured
from .test_history_page import store_report
from .test_reports_browser import FORGE_DONE

stack_options = stack_with(COMPANION)

LIST = "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n// Lands\n99 Island\n"

# A paired test's answer as the research service may give it: the text and a fenced json payload of
# deltas (deck A minus deck B) with 95% intervals.
PAIRED = {
    "tool": "goldfish_ab",
    "ok": True,
    "text": (
        "# Goldfish A/B\n200 paired games\n\n## Deltas (A − B)\n```json\n"
        + json.dumps(
            {
                "deltas": {
                    "commander_turn": {"mean": -0.6, "ci95": [-0.9, -0.3]},
                    "kill_turn": {"mean": 0.1, "ci95": [-0.2, 0.4]},
                }
            }
        )
        + "\n```"
    ),
}


def _sub(stack: Stack) -> str:
    return stack.h.app.state.gateway.db._one("SELECT sub FROM users", ())["sub"]


def _csrf(text: str) -> str:
    m = re.search(r"name='csrf' value='([0-9a-f]+)'", text)
    assert m, text[:2000]
    return m.group(1)


# -- (a) the tested list, "the deck has changed", put it back; (b) since your last test ------------
async def test_a_report_keeps_its_list_says_the_deck_changed_and_puts_it_back_through_a_proposal(
    stack: Stack,
) -> None:
    h = stack.h
    token = await linked_user(stack)
    first = structured(await call(h, token, "run_deck_report", {"deck_ref": "42", "games": 20}))
    assert first["ok"], first
    gw = h.app.state.gateway
    sub = _sub(stack)
    rows = gw.reports.tested(sub, first["report_id"])
    assert rows and any(x["n"] == "Acidic Slime" for x in rows)
    change = [{"action": "remove", "card_name": "Acidic Slime"}]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": change}))
    assert structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))["ok"]
    second = structured(await call(h, token, "run_deck_report", {"deck_ref": "42", "games": 20}))
    assert second["ok"] and second["report_id"] != first["report_id"], second
    b = await linked_browser(stack)
    try:
        page = (await b.http.get(f"/history/reports/{first['report_id']}", headers=NAV)).text
        main = page.split("<main", 1)[1]
        # the list it played, folded, with Copy
        assert "id='rv-tested'" in main and "Acidic Slime" in main.split("id='rv-tested'")[1]
        assert "data-copy='rep-list'" in main
        # the deck has changed since: what moved, and a way to put the list back (review first)
        assert "The deck has changed since this test:" in main and "−1 Acidic Slime" in main
        assert f"action='/history/reports/{first['report_id']}/restore'" in main
        assert "Put this list back (review first)" in main
        patches = list(stack.ark.patches)
        refused = await b.http.post(f"/history/reports/{first['report_id']}/restore", data={})
        assert refused.status_code == 403
        r = await b.http.post(f"/history/reports/{first['report_id']}/restore", data={"csrf": _csrf(page)})
        assert r.status_code == 303 and r.headers["location"].startswith("/proposals/"), r.text
        pid = r.headers["location"].rsplit("/", 1)[1]
        restore = gw.db.get_proposal(pid, sub)
        assert restore["state"] == "pending" and restore["deck_id"] == "42"
        assert [c["card_name"] for c in restore["changes"] if c["action"] == "add"] == ["Acidic Slime"]
        assert stack.ark.patches == patches  # nothing written: the member confirms it on its page
        # (b) the second test compares itself with the one before, with the cards that moved
        newer = (await b.http.get(f"/history/reports/{second['report_id']}", headers=NAV)).text
        since = newer.split("<section class='rv-since'", 1)[1].split("</section>", 1)[0]
        assert "Since your last test" in since and f"href='/history/reports/{first['report_id']}'" in since
        assert "−1 Acidic Slime" in since
        # the newest test of an unchanged deck says so, and offers nothing to put back
        assert "The deck is as this test played it." in newer and "Put this list back" not in newer
        # the exports keep the list
        md = (await b.http.get(f"/history/reports/{first['report_id']}/export.md")).text
        assert "## Tested list" in md and "Acidic Slime" in md.split("## Tested list")[1]
    finally:
        await b.aclose()


def test_the_restore_changes_put_back_counts_and_printings_and_refuse_nothing_to_do() -> None:
    from mtg_gateway.decklist import ListCard
    from mtg_gateway.decks import DeckError

    def deck(*cards: tuple[str, int, list[str]]):  # noqa: ANN202
        return reports_module.list_deck(
            [ListCard(quantity=q, name=n, categories=c) for n, q, c in cards], "D"
        )

    before = deck(("Opt", 2, ["Draw"]), ("Island", 30, []))
    after = deck(("Island", 31, []), ("Ponder", 1, []))
    rows = reports_module.tested_rows(before)
    assert reports_module.unpack_rows(reports_module.pack_rows(rows)) == rows
    changes = {(c["action"], c["card_name"]): c for c in reports_module.restore_changes(rows, after)}
    assert changes[("add", "Opt")]["quantity"] == 2 and changes[("add", "Opt")]["category"] == "Draw"
    assert ("remove", "Ponder") in changes and changes[("remove", "Island")]["quantity"] == 1
    try:
        reports_module.restore_changes(rows, before)
    except DeckError as exc:
        assert str(exc) == reports_module.RESTORE_SAME
    else:
        raise AssertionError("an unchanged deck has nothing to put back")


# -- (c) a proposal is tested against the deck as it is, game for game -----------------------------
async def test_a_proposal_is_tested_against_the_deck_now_and_its_page_shows_the_comparison(
    stack: Stack,
) -> None:
    h = stack.h
    token = await linked_user(stack)
    gw = h.app.state.gateway
    changes = [{"action": "add", "card_name": "Arcane Signet", "quantity": 1, "category": "Ramp"}]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    SIM_CALLS.clear()
    b = await linked_browser(stack)
    try:
        page = (await b.http.get(f"/proposals/{p['proposal_id']}", headers=NAV)).text
        assert f"action='/proposals/{p['proposal_id']}/test'" in page and "Test this change" in page
        r = await b.http.post(f"/proposals/{p['proposal_id']}/test", data={"csrf": _csrf(page)})
        assert r.status_code == 303 and r.headers["location"].startswith("/history/reports/rep_"), r.text
        await gw.reports.settle()
        # the paired test: deck A the deck after the change, deck B the deck as it is now
        ((_tool, sent),) = [c for c in SIM_CALLS if c[0] == "goldfish_ab"]
        assert "Arcane Signet" in str(sent["deck_a"]) and "Arcane Signet" not in str(sent["deck_b"])
        rid = r.headers["location"].rsplit("/", 1)[1]
        report = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "id='rv-paired'" in report and "Against the deck now: paired solo test" in report
        # with numbers to read, the proposal's page says what changed
        sub = _sub(stack)
        with gw.db.tx() as c:
            c.execute("UPDATE reports SET paired_json = ? WHERE id = ?", (json.dumps(PAIRED), rid))
        shown = (await b.http.get(f"/proposals/{p['proposal_id']}", headers=NAV)).text
        block = shown.split("<section class='pr-test'", 1)[1].split("</section>", 1)[0]
        assert "Against the deck now" in block and "Commander cast: 0.6 turns sooner." in block
        assert "Could be chance" in block and f"href='/history/reports/{rid}'" in block
        assert gw.reports.for_proposal(sub, p["proposal_id"])[0]["paired"]["ok"] is True
        assert stack.ark.patches == []
    finally:
        await b.aclose()


def test_paired_deltas_are_read_with_their_direction_and_whether_they_are_clear() -> None:
    rows = rv.ab_deltas(PAIRED)
    by = {r["key"]: r for r in rows}
    assert by["commander_turn"]["way"] == "better" and by["commander_turn"]["clear"] is True
    assert by["kill_turn"]["clear"] is False
    assert rv.ab_verdict(rows) == "Commander cast: 0.6 turns sooner."
    assert rv.ab_deltas({"ok": True, "text": "## Deltas (A − B)\n(none)"}) == []
    md = rv.report_markdown({"report_id": "x", "deck_id": "42", "stats": {}, "paired": PAIRED})
    assert "## Against the deck now: paired solo test" in md and "(clear difference)" in md


# -- (d) the website starts every kind of test ------------------------------------------------------
async def test_the_website_tests_a_pasted_list_a_snapshot_and_two_decks_ab(stack: Stack) -> None:
    h = stack.h
    gw = h.app.state.gateway
    b = await linked_browser(stack)
    try:
        reports_page = (await b.http.get("/reports", headers=NAV)).text
        assert "action='/reports/test'" in reports_page and "name='decklist'" in reports_page
        csrf = _csrf(reports_page)
        assert (await b.http.post("/reports/test", data={"decklist": LIST})).status_code == 403
        empty = await b.http.post("/reports/test", data={"csrf": csrf, "decklist": " "})
        assert empty.status_code == 409 and "Paste a decklist" in empty.text
        r = await b.http.post("/reports/test", data={"csrf": csrf, "decklist": LIST})
        assert r.status_code == 303, r.text
        await gw.reports.settle()
        listed = gw.reports.get(_sub(stack), r.headers["location"].rsplit("/", 1)[1])
        assert listed["source"]["kind"] == "list" and listed["deck_id"] == ""
        # a snapshot: tested as the deck stood then, filed under its deck
        sub = _sub(stack)
        deck = await gw.decks.get_any_deck(sub, "42")
        gw.db.save_snapshot(
            "snap_c7", owner_sub=sub, deck_id="42", proposal_id=None, fingerprint="f", deck=deck.raw
        )
        hist = (await b.http.get("/history?open=s-snap_c7", headers=NAV)).text
        assert "action='/history/snapshots/snap_c7/test'" in hist and "Test this version" in hist
        r = await b.http.post("/history/snapshots/snap_c7/test", data={"csrf": csrf})
        assert r.status_code == 303 and r.headers["location"].startswith("/history/reports/"), r.text
        await gw.reports.settle()
        snap_rep = gw.reports.get(sub, r.headers["location"].rsplit("/", 1)[1].split("?")[0])
        assert snap_rep["deck_id"] == "42" and snap_rep["settings"]["snapshot_id"] == "snap_c7"
        gone = await b.http.post("/history/snapshots/nope/test", data={"csrf": csrf})
        assert gone.status_code == 400 and "It may have been deleted." in gone.text
        # Compare: my decks and this deck's snapshots under the box (audit D-31), then the A/B
        stack.ark.private.discard(43)
        cmp = (await b.http.get("/decks/42/compare", headers=NAV)).text
        assert (
            "aria-label='Your decks and snapshots'" in cmp
            and "href='/decks/42/compare?with=snap_snap_c7'" in cmp
        )
        with_snap = (await b.http.get("/decks/42/compare?with=snap_snap_c7", headers=NAV)).text
        assert "(snapshot, " in with_snap and "/compare/forge" not in with_snap  # Forge pairs two decks
        assert "action='/decks/42/compare/ab'" in with_snap
        other = (await b.http.get("/decks/42/compare?with=43", headers=NAV)).text
        assert "Solo test both (A/B)" in other and "Compare with another of yours" in other
        SIM_CALLS.clear()
        r = await b.http.post("/decks/42/compare/ab", data={"csrf": csrf, "with": "43"})
        assert r.status_code == 303, r.text
        await gw.reports.settle()
        ab = gw.reports.get(sub, r.headers["location"].rsplit("/", 1)[1])
        assert ab["source"]["kind"] == "compare" and ab["source"]["b_deck_id"] == "43"
        assert [c[0] for c in SIM_CALLS] == ["goldfish_ab"]
        nothing = await b.http.post("/decks/42/compare/ab", data={"csrf": csrf, "with": ""})
        assert nothing.status_code == 409 and "Pick a deck or a snapshot" in nothing.text
    finally:
        await b.aclose()


# -- (e) a few Forge games do not headline the Reports page ----------------------------------------
async def test_a_few_forge_games_are_not_a_headline_win_rate_on_reports(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = _sub(stack)
        store_report(gw.db, sub, "rep_few")
        with gw.db.tx() as c:
            c.execute(
                "UPDATE reports SET forge_json = ?, forge_state = 'done' WHERE id = 'rep_few'",
                (json.dumps(FORGE_DONE),),
            )
        page = (await b.http.get("/reports", headers=NAV)).text
        table = page.split("class='ui-table rp-t'", 1)[1]
        row = table.split("/history/reports/rep_few'", 1)[1].split("<div class='tr'>", 1)[0]
        assert "10 games, too few" in row and "40%" not in row
        assert "won against precons" not in page  # the Latest report widget leads with the solo test
    finally:
        await b.aclose()


# -- (f) History is the decks' timeline -------------------------------------------------------------
async def test_history_lists_applied_changes_and_tests_open_their_report(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    change = [{"action": "remove", "card_name": "Acidic Slime"}]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": change}))
    waiting = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "remove", "card_name": "Forest"}]},
        )
    )
    structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    store_report(h.app.state.gateway.db, _sub(stack), "rep_t")
    b = await linked_browser(stack)
    try:
        body = (await b.http.get("/history", headers=NAV)).text.split("<main", 1)[1]
        assert ">Changes <span class='n'>1</span>" in body and ">Tests <span class='n'>1</span>" in body
        assert f"open=p-{p['proposal_id']}'" in body and ">Change</span>" in body
        assert f"/proposals/{waiting['proposal_id']}'" not in body  # waiting: on Proposals only
        assert "class='sm-only chev' href='/history/reports/rep_t'" in body and "open=r-rep_t" not in body
        r = await b.http.get("/history?open=r-rep_t", headers=NAV, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/history/reports/rep_t"
    finally:
        await b.aclose()


# -- (h) a Forge pair head to head, out of the deck's trend ----------------------------------------
async def test_a_forge_pair_is_shown_head_to_head_and_kept_out_of_the_trend(stack: Stack) -> None:
    from .test_forge import BRACKETED, FakeForge

    gw = stack.h.app.state.gateway
    gw.reports.forge = FakeForge(brackets=BRACKETED).client()
    stack.ark.private.discard(43)
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/decks/42/compare?with=43", headers=NAV)).text
        r = await b.http.post("/decks/42/compare/forge", data={"csrf": _csrf(page), "with": "43"})
        assert r.status_code == 303 and r.headers["location"].endswith("/head-to-head"), r.text
        both = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "<h1>Head to head</h1>" in both and "These games stay out of each deck" in both
        assert "data-pair-live" in both  # the page follows both runs while they play
        second = r.headers["location"].split("/")[3]
        sub = _sub(stack)
        first, other = gw.reports.pair_of(sub, second)
        assert {first, other} >= {second}
        assert gw.reports.get(sub, first)["source"]["kind"] == "pair"
        assert [x["report_id"] for x in gw.reports.series(sub, "42")] == []
        report = (await b.http.get(f"/history/reports/{second}", headers=NAV)).text
        assert f"href='/history/reports/{second}/head-to-head'" in report
    finally:
        await b.aclose()
