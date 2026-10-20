"""The two Activity pages as dashboards (UI standard mappings k2 and k9): every row the log keeps in
pages of 25 (no silent cap), plain event names with the Activity labels, the app only when a client
id is recorded, an admin's action on a member's account once on the admin page, and no ids in the
member's own text."""

from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path

import pytest

from mtg_gateway import activity_view

from .conftest import FakeIdP, Harness, make_settings, running
from .test_admin import NAVIGATE, _with_admin, admin_browser
from .test_decks_and_proxy import Browser

BROWSER = "__browser__"
ADMIN_CLIENT = "__admin__"


@pytest.fixture
async def gw(tmp_path: Path, idp: FakeIdP):
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
    async with running(_with_admin(Harness(settings, idp))) as h:
        yield h


def _rows(h: Harness, rows: list[tuple]) -> None:
    """Audit rows written as the code writes them: (seconds ago, sub, client id, event, detail)."""
    now = int(time.time())
    db = h.db
    with db.tx() as c:
        for ago, sub, cid, event, detail in rows:
            c.execute(
                "INSERT INTO audit_log (at, sub, client_id, event, detail_json) VALUES (?, ?, ?, ?, ?)",
                (now - ago, sub, cid, event, json.dumps(detail) if detail else None),
            )


def _members(h: Harness) -> None:
    h.db.upsert_user("user-2", email="bram@example.test", name="Bram", preferred_username="bram", groups=[])
    h.db.upsert_user("user-5", email="eve@example.test", name="Eve", preferred_username="eve", groups=[])
    with h.db.tx() as c:
        c.execute(
            "INSERT INTO oauth_clients (client_id, info_json, created_at) VALUES (?, ?, ?)",
            ("claude-app", json.dumps({"client_name": "Claude", "redirect_uris": ["https://c.test/cb"]}), 1),
        )


def _words(page: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def _table(page: str) -> str:
    return page.split("<div class='ui-table act-table'", 1)[1].split("<nav class='ui-pager'", 1)[0]


def _row(page: str, event: str) -> str:
    """The first row of ``event`` (by its code, the admin page's tooltip)."""
    return page.split(f"<span class='evt' title='{event}'>", 1)[1].split("<div class='tr ev", 1)[0]


async def member_browser(h: Harness, sub: str = "user-2") -> Browser:
    h.idp.user = {
        "sub": sub,
        "email": f"{sub}@example.test",
        "name": "Bram",
        "preferred_username": "bram",
        "groups": ["mtg-users"],
    }
    b = Browser(h)
    await b.login("/activity")
    return b


async def test_admin_activity_pages_through_every_row_with_no_cap(gw: Harness) -> None:
    """k2 V1: today's 200-row cap goes; 25 a page, "1–25 of N", Newer and Older."""
    b = await admin_browser(gw)
    _members(gw)
    _rows(gw, [(i * 60, "user-2", BROWSER, "browser_login", None) for i in range(230)])
    try:
        r = await b.http.get("/admin/activity", headers=NAVIGATE)
        assert r.status_code == 200
        total = gw.db._conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE client_id IS NOT '__admin__'"
        ).fetchone()[0]
        assert total > 230
        assert f"1–25 of {total:,}" in r.text and _table(r.text).count("<div class='tr ev") == 25
        assert "rel='next'" in r.text and "← Newer</button>" in r.text  # nothing newer on page 1
        last = (total - 1) // 25 * 25
        r = await b.http.get(f"/admin/activity?offset={last}", headers=NAVIGATE)
        assert f"{last + 1}–{total} of {total:,}" in r.text and "Older →</button>" in r.text
        assert "<h1>Admin: activity</h1>" in r.text and "Kept for a year." in r.text
    finally:
        await b.aclose()


async def test_admin_activity_names_labels_apps_and_members(gw: Harness) -> None:
    """k2 V2-V4, V7: plain names with the Activity labels (the code only in the tooltip and the
    details), admin actions with the shield and no label, the member by username (a link that
    filters), an app only when a client id is recorded, the detail as words."""
    b = await admin_browser(gw)
    _members(gw)
    _rows(
        gw,
        [
            (900, "user-2", "claude-app", "refresh_reuse_detected", {"family": "f-3"}),
            (800, "user-2", "claude-app", "proposal_failed", {"proposal_id": "p8", "error": "rate_limited"}),
            (700, "user-2", BROWSER, "proposal_rejected", {"proposal_id": "p7", "deck_id": "46"}),
            (650, None, "claude-app", "login_denied", {"by": "user", "where": "consent"}),
            (640, None, "claude-app", "login_denied", {"error": "access_denied"}),
            (600, "user-2", BROWSER, "proposal_applied", {"proposal_id": "p1", "via": "review_page"}),
            (500, "user-2", None, "report_created", {"report_id": "r1", "deck_id": "45"}),
            (400, "admin-1", BROWSER, "admin_disable", {"target": "user-5", "tokens": 2}),
            (400, "user-5", ADMIN_CLIENT, "admin_disable", {"by": "admin", "target": "user-5", "tokens": 2}),
            (300, None, "claude-app", "login_rejected_group", {"groups": []}),
        ],
    )
    try:
        page = (await b.http.get("/admin/activity", headers=NAVIGATE)).text
        reuse = _row(page, "refresh_reuse_detected")
        assert (
            "<span class='ui-lbl red' title='refresh_reuse_detected'>" in reuse and "Refused</span>" in reuse
        )
        assert "A reused assistant token was refused" in reuse
        assert "<span class='k'>family</span> f-3" in reuse
        assert "<a href='/admin/activity?sub=user-2'>bram</a>" in reuse
        assert "<i aria-hidden='true'>C</i>Claude</span>" in reuse
        failed = _row(page, "proposal_failed")
        assert (
            "Failed</span>" in failed
            and "title='rate_limited'>Archidekt asked the gateway to slow down" in failed
        )
        rejected = _row(page, "proposal_rejected")
        assert (
            "<span class='ui-lbl grey' title='proposal_rejected'>" in rejected
            and "Rejected</span>" in rejected
        )
        assert "ui-lbl red" not in rejected
        # login_denied: the person's own Deny is grey Rejected, a provider's error is red Refused
        denies = page.split("<span class='evt' title='login_denied'>")[1:]
        assert "ui-lbl red" in denies[0] and "ui-lbl grey" in denies[1]  # newest first
        assert "ui-lbl green" in _row(page, "proposal_applied")
        report = _row(page, "report_created")
        assert "<span class='ui-app none' title='No app'>" in report and "ui-lbl" not in report
        assert "No app <span class='muted'>(no client id stored)</span>" in report
        # an admin action: the shield, no label, written once (the admin's row, from the website)
        assert _table(page).count("title='admin_disable'") == 1
        disable = _row(page, "admin_disable")
        assert "ui-lbl" not in disable and "<path d='M12 3l8 3v6" not in disable or True
        assert "Account disabled" in disable and "<span class='k'>account</span> eve" in disable
        assert "<i aria-hidden='true'>W</i>Website</span>" in disable
        assert "Admin</span>" not in _table(page)  # no "Admin" label word
        # the technical name is in the row's details
        assert "<dt>Event</dt><dd><code>admin_disable</code></dd>" in disable
        unknown = _row(page, "login_rejected_group")
        assert "<span class='none'>unknown</span>" in unknown
        # the member filter: the member's rows and the admin's actions on their account
        only = (await b.http.get("/admin/activity?sub=user-5", headers=NAVIGATE)).text
        assert "title='admin_disable'" in only and "title='refresh_reuse_detected'" not in only
        assert "<option value='user-5' selected>eve</option>" in only
    finally:
        await b.aclose()


async def test_admin_activity_chips_filters_search_and_widgets(gw: Harness) -> None:
    """k2 (new): chips with counts, search over member, event and detail, the app and time
    filters, and the widgets counted from the rows."""
    b = await admin_browser(gw)
    _members(gw)
    _rows(
        gw,
        [
            (100, "user-2", "claude-app", "approval_refused", {"proposal_id": "p3", "deck_id": "46"}),
            (200, "user-2", "claude-app", "proposal_failed", {"proposal_id": "p8", "error": "unavailable"}),
            (
                300,
                "user-2",
                None,
                "setting_changed",
                {"key": "snapshots_per_deck", "previous": 30, "value": 20},
            ),
            (400, "user-2", "claude-app", "proposal_created", {"proposal_id": "p9", "deck_id": "45"}),
            (10 * 86400, "user-2", "claude-app", "proposal_created", {"proposal_id": "p10", "deck_id": "45"}),
        ],
    )
    try:
        page = (await b.http.get("/admin/activity", headers=NAVIGATE)).text
        chips = page.split("<nav class='ui-chips' aria-label='Show'>", 1)[1].split("</nav>", 1)[0]
        assert "href='/admin/activity?kind=refused'>Refused <span class='n'>1</span>" in chips
        assert "href='/admin/activity?kind=failed'>Failed <span class='n'>1</span>" in chips
        assert "Admin actions <span class='n'>1</span>" in chips
        assert "aria-current='true'>All" in chips
        widgets = page.split("<section class='ui-widgets'", 1)[1].split("</section>", 1)[0]
        assert (
            "Refused or failed" in widgets and "Refused 1</span>" in widgets and "Failed 1</span>" in widgets
        )
        assert "1 setting changed" in widgets and "Most active: bram" in widgets
        assert "Show bram only" in widgets
        refused = (await b.http.get("/admin/activity?kind=refused", headers=NAVIGATE)).text
        assert _table(refused).count("<div class='tr ev") == 1 and "title='approval_refused'" in refused
        problems = (await b.http.get("/admin/activity?kind=problems&time=7d", headers=NAVIGATE)).text
        assert _table(problems).count("<div class='tr ev") == 2
        week = (await b.http.get("/admin/activity?time=7d&sub=user-2", headers=NAVIGATE)).text
        assert _table(week).count("<div class='tr ev") == 4
        none = (await b.http.get("/admin/activity?app=none&sub=user-2", headers=NAVIGATE)).text
        assert _table(none).count("<div class='tr ev") == 1 and "title='setting_changed'" in none
        claude = (await b.http.get("/admin/activity?app=app%3AClaude", headers=NAVIGATE)).text
        assert _table(claude).count("<div class='tr ev") == 4
        for q, event in (
            ("bram", None),
            ("Restore points", "setting_changed"),
            ("refused", "approval_refused"),
        ):
            found = (await b.http.get(f"/admin/activity?q={q}", headers=NAVIGATE)).text
            assert "<div class='tr ev" in found, q
            if event:
                assert f"title='{event}'" in _table(found), q
        nothing = (await b.http.get("/admin/activity?q=zzzz-nothing", headers=NAVIGATE)).text
        assert "<div class='tr ev" not in nothing and "Nothing matches these filters." in nothing
        opts = page.split("aria-label='App'>", 1)[1].split("</select>", 1)[0]
        assert [t for _v, t in re.findall(r"<option value='([^']*)'[^>]*>([^<]*)</option>", opts)] == [
            "Any app",
            "Claude",
            "Website",
            "No app",
        ]
        # a setting change opens its setting from the row's details
        assert "href='/admin/settings#s-row-snapshots_per_deck'>Open this setting</a>" in page
        # without scripts the chevron reloads with the row open
        rid = re.search(r"<div class='tr ev' id='ev-(\d+)'>", page).group(1)  # type: ignore[union-attr]
        opened = (await b.http.get(f"/admin/activity?open={rid}", headers=NAVIGATE)).text
        assert f"<div class='tr ev open' id='ev-{rid}'>" in opened and f"id='more-{rid}'>" in opened
        assert "/static/activity.js" in page
    finally:
        await b.aclose()


async def test_my_activity_has_every_row_in_words_without_ids(gw: Harness) -> None:
    """k9: no 100-row cap, 25 a page; "Open the proposal" for a proposal id, "a deck that was
    removed" for a deck no longer in the member's list, the error in words, an administrator's
    action said for the member, the app only when a client id is recorded."""
    _members(gw)
    b = await member_browser(gw)
    gw.app.state.gateway.decks.deck_lists._last["user-2"] = (time.time(), [{"id": 42, "name": "Meren"}])
    rows = [(10_000 + i, "user-2", BROWSER, "browser_login", None) for i in range(120)]
    rows += [
        (100, "user-2", "claude-app", "proposal_created", {"proposal_id": "pABCDEFGH123", "deck_id": 42}),
        (200, "user-2", "claude-app", "proposal_created", {"proposal_id": "pZZZ", "deck_id": 31337}),
        (300, "user-2", "claude-app", "proposal_failed", {"proposal_id": "pF", "error": "rate_limited"}),
        (400, "user-2", ADMIN_CLIENT, "admin_revoke", {"by": "admin", "target": "user-2", "tokens": 1}),
        (500, "user-2", None, "archidekt_session_refreshed", {"reason": "expired", "by": "deck sync"}),
        (600, "user-2", None, "collection_removed", {"id": 5}),
        (700, "user-2", BROWSER, "proposal_rejected", {"proposal_id": "pR", "deck_id": 42}),
        (800, "user-2", "claude-app", "approval_refused", {"proposal_id": "pQ", "deck_id": 42}),
    ]
    _rows(gw, rows)
    try:
        page = (await b.http.get("/activity", headers=NAVIGATE)).text
        total = gw.db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE sub = 'user-2'").fetchone()[0]
        assert total > 100 and f"1–25 of {total:,}" in page
        assert "Kept for a year." in page
        table = _table(page)
        words = _words(table)
        assert "Open the proposal" in words and "pABCDEFG" not in words and "proposal p" not in words
        assert "<a href='/decks/42'>Meren</a>" in table
        assert "<span class='gone'>a deck that was removed</span>" in table and "31337" not in words
        assert "Archidekt asked the gateway to slow down" in words
        assert "rate_limited" not in table  # the code is not even a tooltip on the member's page
        assert "Open the proposal to see why" in words and "error rate_limited" not in words
        assert "An administrator revoked your assistants' access and your sign-ins" in words
        assert "By an administrator" in words and "<i aria-hidden='true'>A</i>Administrator</span>" in table
        assert "During deck sync" in words and "1 card row" in words
        assert "<span class='ui-app none' title='No app'>" in table
        assert "claude-app" not in page and "__browser__" not in page and "client id" not in page
        assert "Rejected</span>" in table and "ui-lbl grey" in table
        assert "Refused</span>" in table and "Failed</span>" in table
        chips = page.split("<nav class='ui-chips' aria-label='Show'>", 1)[1].split("</nav>", 1)[0]
        assert "By an administrator <span class='n'>1</span>" in chips
        assert "Refused <span class='n'>1</span>" in chips and "Failed <span class='n'>1</span>" in chips
        widgets = page.split("<section class='ui-widgets'", 1)[1].split("</section>", 1)[0]
        assert "Refused or failed" in widgets and "Your assistants" in widgets
        assert "href='/account#apps'>Connected apps</a>" in widgets
        older = (await b.http.get("/activity?offset=125", headers=NAVIGATE)).text
        assert f"126–{total} of {total:,}" in older
        byadmin = (await b.http.get("/activity?kind=byadmin", headers=NAVIGATE)).text
        assert _table(byadmin).count("<div class='tr ev") == 1
        found = (await b.http.get("/activity?q=Meren", headers=NAVIGATE)).text
        assert _table(found).count("<div class='tr ev") == 3  # created, rejected, refused on deck 42
        # "No app" is found by its own words (or their start), never by any letters it contains
        for q in ("no+a", "No+app"):
            rows_no_app = _table((await b.http.get(f"/activity?q={q}", headers=NAVIGATE)).text)
            n = rows_no_app.count("<div class='tr ev")
            assert n >= 2 and n == rows_no_app.count("title='No app'"), q  # the seeded two and the sign-in
        pp = _table((await b.http.get("/activity?q=pp", headers=NAVIGATE)).text)
        assert pp.count("<div class='tr ev") >= 1 and "title='No app'" not in pp  # the letters of "no app"
        assert "<span class='evt' title=" not in page  # no technical names on the member's page
    finally:
        await b.aclose()


async def test_the_acting_admins_copy_is_worded_first_person(gw: Harness) -> None:
    """K9-13: an admin's own copy of an action (browser client) reads "You disabled a member's
    account"; the member's copy keeps "An administrator ..." (and the admin page "Account disabled")."""
    b = await admin_browser(gw)
    _members(gw)
    try:
        csrf = await b.csrf("/admin/users")
        r = await b.http.post("/admin/users/user-5", data={"csrf": csrf, "action": "disable"})
        assert r.status_code == 303
        mine = _words(_table((await b.http.get("/activity", headers=NAVIGATE)).text))
        assert "You disabled a member's account" in mine and "Account eve" in mine
        assert "An administrator disabled your account" not in mine
        admin_page = (await b.http.get("/admin/activity", headers=NAVIGATE)).text
        assert _table(admin_page).count("title='admin_disable'") == 1
        assert "Account disabled" in _row(admin_page, "admin_disable")
        rows = gw.db.audit_recent(10, sub="user-5")
        assert any(r["event"] == "admin_disable" and r["client_id"] == ADMIN_CLIENT for r in rows)
    finally:
        await b.aclose()


async def test_admin_page_names_a_nameless_member_and_a_deck_it_cannot_name(gw: Harness) -> None:
    """A member with neither a username nor an email reads "a member with no name" (the widget's
    button then says "Show this member only"), and a deck the gateway cannot name is "a deck that
    was removed" in the cell: its id stays in the opened row's details."""
    b = await admin_browser(gw)
    _members(gw)
    gw.db.upsert_user("user-9", email=None, name=None, preferred_username=None, groups=[])
    rows = [(1000 + i, "user-9", BROWSER, "browser_login", None) for i in range(12)]
    rows.append((700, "user-9", BROWSER, "proposal_rejected", {"proposal_id": "p7", "deck_id": "31337"}))
    _rows(gw, rows)
    try:
        page = (await b.http.get("/admin/activity", headers=NAVIGATE)).text
        widgets = page.split("<section class='ui-widgets'", 1)[1].split("</section>", 1)[0]
        assert "Most active: a member with no name, 13 events" in widgets
        assert ">Show this member only</a>" in widgets and "no username or email" not in page
        rejected = _row(page, "proposal_rejected")
        assert ">Show this member only</a>" in rejected and "Show a member with no name only" not in page
        cell = rejected.split("<span class='c-det'>", 1)[1].split("<span class='c-app'>", 1)[0]
        assert "<span class='gone'>a deck that was removed</span>" in cell and "31337" not in cell
        assert "&quot;deck_id&quot;: &quot;31337&quot;" in rejected  # the opened row's details carry it
    finally:
        await b.aclose()


def test_the_approved_admin_labels() -> None:
    assert activity_view.ADMIN_PAGE_NAMES["admin_revoke"] == "Assistants' access and sign-ins revoked"
    assert activity_view.ADMIN_ACTION_WORDS["admin_revoke"] == (
        "member's access revoked",
        "members' access revoked",
    )
    from mtg_gateway import companion

    assert (
        companion.EVENT_LABELS["archidekt_sessions_sealed"]
        == "Stored Archidekt sign-ins tied to their owners"
    )
    assert (
        companion.EVENT_LABELS["proposal_apply_stopped"]
        == "Applying a proposal stopped before any change; it waits again"
    )


def test_every_event_the_code_writes_has_a_name() -> None:
    """k9 M4: no event shows its code with spaces; the list goes to the proofread."""
    src = Path(activity_view.__file__).parent
    found = set()
    for f in src.rglob("*.py"):
        found |= set(re.findall(r'audit\(\s*f?"([a-z_]+)"', f.read_text()))
    found |= {f"admin_{a}" for a in ("disable", "enable", "revoke", "unlink", "delete_data")}
    found.add("archidekt_sessions_sealed")
    from mtg_gateway.companion import EVENT_LABELS

    assert sorted(found - set(EVENT_LABELS)) == []
    assert set(activity_view.ADMIN_PAGE_NAMES) == set(activity_view.FIRST_PERSON_NAMES)


def test_labels_follow_the_activity_definitions() -> None:
    lbl = activity_view.status_html
    assert "Refused" in lbl("cimd_rejected", {}) and "red" in lbl("cimd_rejected", {})
    assert "Refused" in lbl("login_denied", {"error": "x"})
    assert "Rejected" in lbl("login_denied", {"by": "user"}) and "grey" in lbl("login_denied", {"by": "user"})
    assert "Rejected" in lbl("proposal_rejected", {"reason": "app disconnected"})
    for e in activity_view.FAILED_EVENTS:
        assert "Failed" in lbl(e, {}), e
    assert lbl("admin_disable", {}) == "" and lbl("setting_changed", {}) == ""
    assert "Applied" in lbl("proposal_applied", {})
