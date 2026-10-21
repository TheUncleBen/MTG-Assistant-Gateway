"""The admin pages after the 2026-10-21 overhaul: the Overview's six widgets, the Users page with
its widgets, chips, filters, the member's side panel and its Disable and Delete data boxes, the
Metrics page in plain words (the raw name only in a row's details), the Card models page's search,
member filter and Remove box, the Settings page's order and notices, and the Activity page's row
menu. Every list names members and apps in words: a subject id or a tool name shows only in a row's
details."""

from __future__ import annotations

import html
import re
import time
from pathlib import Path

import pytest

from mtg_gateway import ui

from .conftest import FakeIdP, Harness, make_settings, running
from .test_admin import ADMIN, NAVIGATE, _with_admin, admin_browser

MODEL = '{"effects":[]}'
NOW = int(time.time())


def seed(h: Harness) -> None:
    db = h.db
    db.upsert_user(
        "user-2", email="bob@example.test", name="Bob", preferred_username="bob", groups=["mtg-users"]
    )
    db.upsert_user(
        "user-3",
        email="carol@example.test",
        name=None,
        preferred_username="carol_long_member_name",
        groups=["mtg-users"],
    )
    db.set_user_disabled("user-3", True)
    db.upsert_user("user-9", email=None, name=None, preferred_username=None, groups=["mtg-users"])
    db.save_link("user-2", username="bob_brews", user_id="7", secret_enc="enc")
    db.save_client("claude-app", {"client_name": "Claude"})
    for i, (sub, client) in enumerate([("user-2", "claude-app"), ("user-9", "dcr-4f9a")]):
        db.save_token(
            f"tok-{i}",
            kind="access",
            client_id=client,
            sub=sub,
            scopes=["mcp"],
            resource=None,
            family=f"f{i}",
            expires_at=NOW + 3600,
        )
    db.put_card_model("sol ring", "Sol Ring", MODEL, "user-9", 100)
    db.put_card_model("opt", "Opt", MODEL, "user-2", 200)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    db.metrics_increment(day, "user-2", "tool", "search_decks", 84)
    db.metrics_increment(day, "user-2", "tool", "made_up_tool", 2)
    db.metrics_increment(day, None, "error", "server_error", 3)
    db.metrics_increment(day, "user-2", "card", "printings", 1)


@pytest.fixture
async def gw(tmp_path: Path, idp: FakeIdP):
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
    async with running(_with_admin(Harness(settings, idp))) as h:
        seed(h)
        yield h


async def _get(b, path: str) -> str:
    r = await b.http.get(path, headers=NAVIGATE)
    assert r.status_code == 200, (path, r.status_code)
    return html.unescape(r.text)


def _widgets(text: str) -> list[str]:
    block = text.split("<section class='ui-widgets'", 1)[1].split("</section>", 1)[0]
    return re.findall(r"<span class='lab'>(?:<svg.*?</svg>)?([^<]+)</span>", block)


# -- Overview --------------------------------------------------------------------------------------


async def test_the_overview_is_six_widgets_then_system_updates_and_activity_by_day(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        text = await _get(b, "/admin")
        assert text.index("<h1>Admin: overview</h1>") < text.index(
            "Members, the last 30 days and the system."
        )
        assert text.index("the system.") < text.index("<nav class='adm-nav'")
        assert text.index("<nav class='adm-nav'") < text.index("<section class='ui-widgets'")
        assert _widgets(text) == [
            "Members",
            "Proposals, 30 days",
            "Tool calls, 30 days",
            "Errors, 30 days",
            "System",
            "Updates",
        ]
        assert "<span class='big'>4<small>signed in</small></span>" in text
        assert "1 disabled · 1 linked to Archidekt" in text
        assert "<span class='big'>86</span>" in text  # 84 + 2 tool calls
        assert "<span class='big'>3</span><span class='det'>3 today</span>" in text  # the errors
        assert "<div class='wdg alert'>" in text  # errors in the alert tone
        assert "class='spark' role='img' aria-label='Tool calls per day, oldest first:" in text
        assert "<section class='ui-card' id='system'" in text and "<details id='how-to-update'>" in text
        assert text.index("id='system'") < text.index("id='how-to-update'")
        assert text.index("id='how-to-update'") < text.index("<h2 id='byday-h'>Activity by day</h2>")
        assert "Per UTC day." in text
        byday = text.split("<h2 id='byday-h'>", 1)[1].split("</section>", 1)[0]
        for kind in ("Tool calls", "Errors"):
            assert f"<p class='lab'>{kind}" in byday, kind
        assert "<p class='lab'>tool" not in byday and "<p class='lab'>error" not in byday
        assert "search_decks" not in text and "made_up_tool" not in text
        assert "<main class='wrap ui-wide'" in text
    finally:
        await b.aclose()


# -- Users -----------------------------------------------------------------------------------------


async def test_users_list_reads_in_words_with_widgets_chips_and_filters(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        text = await _get(b, "/admin/users")
        assert "Everyone who has signed in. Disabling blocks this gateway only." in text
        assert _widgets(text) == [
            "Members",
            "Active in 7 days",
            "Disabled",
            "Linked to Archidekt",
            "Connected apps",
        ]
        assert "<span class='big'>4<small>signed in</small></span>" in text
        assert "<div class='wdg alert'>" in text  # one account is disabled
        assert "1 member uses Claude, 1 an unnamed app" in text
        assert "All members <span class='ui-sub'>4</span>" in text
        chips = text.split("<nav class='ui-chips' aria-label='Show'>", 1)[1].split("</nav>", 1)[0]
        assert "href='/admin/users' aria-current='true'>All <span class='n'>4</span>" in chips
        for word, n in [("Active", 3), ("Disabled", 1), ("Linked", 1), ("Not linked", 3), ("Admins", 1)]:
            assert f">{word} <span class='n'>{n}</span></a>" in chips, word
        # members in words, the apps as chips: never a subject id or a client id in the list
        rows = text.split("<div class='ui-table'", 1)[1].split("<nav class='ui-pager'", 1)[0]
        assert "<i class='nobody'>a member with no name</i>" in rows
        assert "<span class='nm'>Claude</span>" in rows and "<span class='nm'>Unnamed app</span>" in rows
        assert "claude-app" not in rows and "dcr-4f9a" not in rows and ">user-2<" not in rows
        assert "<span class='ui-lbl red'><svg" in rows and "Disabled</span>" in rows
        assert "aria-label='Actions for carol_long_member_name' aria-haspopup='menu'" in rows
        assert "data-box-disable='/admin/users?open=user-3&box=disable'" in rows
        assert "<script src='/static/admin.js?v=" in text
        assert "btn-warn" not in text and "class='btn'>" not in rows  # no orange on the admin pages
        # chips and the tool bar narrow the list
        only = await _get(b, "/admin/users?chip=disabled")
        assert only.count("class='tr mem") == 1 and "carol_long_member_name" in only
        assert "Nobody matches" not in only
        unnamed = await _get(b, "/admin/users?app=unnamed")
        assert unnamed.count("class='tr mem") == 1 and "a member with no name" in unnamed
        found = await _get(b, "/admin/users?q=bob")
        assert found.count("class='tr mem") == 1 and ">bob<" in found
        nobody = await _get(b, "/admin/users?q=zzz")
        assert "Nobody matches these filters. <a href='/admin/users'>Show everyone</a>" in nobody
    finally:
        await b.aclose()


async def test_a_members_panel_holds_the_ids_the_actions_and_the_two_boxes(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        text = await _get(b, "/admin/users?open=user-2")
        panel = text.split("<aside class='ui-drawer' aria-label='bob'>", 1)[1]
        assert "<a href='/admin/users'>← All members</a>" in panel
        assert "<code>user-2</code>" in panel and "<code>claude-app</code>" in panel
        assert "<span class='nm'>Claude</span>" in panel and "bob_brews" in panel
        assert "class='btn btn-outline-danger' href='/admin/users?open=user-2&box=disable'" in panel
        assert "data-box-open='disable'>Disable…</a>" in panel
        assert "value='revoke' class='btn-plain'>Revoke tokens and sessions</button>" in panel
        assert "value='unlink' class='btn-plain'>Unlink Archidekt</button>" in panel
        assert "href='/admin/activity?sub=user-2'>Activity</a>" in panel
        assert (
            "<div class='au-del'><a class='btn btn-outline-danger' href='/admin/users?open=user-2&box=delete'"
            in panel
        )
        assert (
            "id='disable' role='dialog'" in panel
            and "data-open" not in panel.split("id='disable'", 1)[1][:80]
        )
        assert "Disable “bob”?" in panel and "Delete “bob”'s data?" in panel
        # the old address with the box named opens the page with that box open
        disable = await _get(b, "/admin/users?open=user-2&box=disable")
        box = disable.split("id='disable'", 1)[1]
        assert box.startswith(" role='dialog'") and "data-open" in box[:200]
        assert "<button type='submit' class='danger' form='disf'>Disable account</button>" in box
        delete = await _get(b, "/admin/users?open=user-2&box=delete")
        box = delete.split("id='delete'", 1)[1]
        assert "data-open" in box[:200]
        assert "<input type='checkbox' name='confirm' value='yes' required>" in box
        assert "<button type='submit' class='danger' form='delf' data-needs-tick>Delete data</button>" in box
        assert "This can't be undone." in box
        # a disabled member gets Enable, no Disable; the admin's own row gets neither box
        carol = await _get(b, "/admin/users?open=user-3")
        assert "value='enable' class='btn-plain'>Enable</button>" in carol and "Disable…" not in carol
        me = await _get(b, f"/admin/users?open={ADMIN['sub']}")
        assert "This is you. You can't disable your own account here." in me
        assert "id='delete'" not in me and "id='disable'" not in me
        # the no-name member's panel heads with the same words as the list
        nobody = await _get(b, "/admin/users?open=user-9")
        assert "aria-label='a member with no name'" in nobody
        assert "<code>dcr-4f9a</code>" in nobody and "<span class='nm'>Unnamed app</span>" in nobody
    finally:
        await b.aclose()


async def test_the_panels_forms_still_act_and_say_so(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        csrf = await b.csrf("/admin/users")
        r = await b.http.post("/admin/users/user-3", data={"csrf": csrf, "action": "enable"})
        assert r.status_code == 303 and "/admin/users" in r.headers["location"]
        assert not gw.db.get_user("user-3")["disabled_at"]
        shown = await _get(b, r.headers["location"])
        assert "<div class='notice ok' role='status'>" in shown
        r = await b.http.post("/admin/users/user-2", data={"csrf": csrf, "action": "disable"})
        assert r.status_code == 303
        assert gw.db.get_user("user-2")["disabled_at"]
    finally:
        await b.aclose()


# -- Metrics ---------------------------------------------------------------------------------------


async def test_metrics_read_in_words_with_the_raw_name_only_in_a_rows_details(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        text = await _get(b, "/admin/metrics")
        assert "Usage and errors, counted per UTC day." in text
        assert _widgets(text) == ["Tool calls", "Errors", "API calls", "Admin actions"]
        assert "<span class='big'>86<small>in 30 days</small></span>" in text
        assert "Busiest: Search public decks, 84" in text
        assert "Everything counted <span class='ui-sub'>4</span>" in text
        chips = text.split("<nav class='ui-chips' aria-label='Show'>", 1)[1].split("</nav>", 1)[0]
        assert "aria-current='true'>All <span class='n'>4</span>" in chips
        assert ">Tool calls <span class='n'>2</span>" in chips and ">Errors <span class='n'>1</span>" in chips
        rows = text.split("<div class='ui-table'", 1)[1].split("<nav class='ui-pager'", 1)[0]
        titles = re.findall(r"aria-controls='m-\d+-more'>([^<]+)</a>", rows)
        assert titles == ["Search public decks", "Server error", "Unknown tool", "Card printings"]
        # the raw name only in the row's details, after "Recorded as"
        assert "<dt>Recorded as</dt><dd><code>search_decks</code></dd>" in rows
        assert "<dt>Recorded as</dt><dd><code>made_up_tool</code></dd>" in rows
        shown = re.sub(r"href='[^']*'", "", rows.split("<div class='more-body'", 1)[0])
        assert "made_up_tool" not in shown and "search_decks" not in shown
        assert "<dd>84 in 30 days</dd>" in rows
        assert "<div class='more-body' id='m-0-more' hidden>" in rows
        # the period and the kind narrow the list; a row's details open from an address too
        kind = await _get(b, "/admin/metrics?kind=error")
        assert kind.count("class='tr mrow") == 1 and "Server error" in kind
        week = await _get(b, "/admin/metrics?days=7")
        assert "<small>in 7 days</small>" in week and "<dd>84 in 7 days</dd>" in week
        assert "<option value='7' selected>Last 7 days</option>" in week
        opened = await _get(b, "/admin/metrics?open=tool%3Asearch_decks")
        assert "<div class='more-body' id='m-0-more'>" in opened and "aria-expanded='true'" in opened
        none = await _get(b, "/admin/metrics?q=zzz")
        assert "Nothing counted matches these filters. <a href='/admin/metrics'>Show everything</a>" in none
    finally:
        await b.aclose()


async def test_metrics_page_by_25(gw: Harness) -> None:
    day = time.strftime("%Y-%m-%d", time.gmtime())
    for i in range(30):
        gw.db.metrics_increment(day, None, "api", f"route_{i:02d}", 30 - i)
    b = await admin_browser(gw)
    try:
        text = await _get(b, "/admin/metrics?kind=api")
        assert text.count("class='tr mrow") == 25
        assert "<span class='info'>1–25 of 30</span>" in text
        assert "href='/admin/metrics?kind=api&offset=25' rel='next'>Older →</a>" in text
        more = await _get(b, "/admin/metrics?kind=api&offset=25")
        assert more.count("class='tr mrow") == 5 and "26–30 of 30" in more
    finally:
        await b.aclose()


# -- Card models -----------------------------------------------------------------------------------


async def test_card_models_search_member_filter_and_widgets(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        text = await _get(b, "/admin/models")
        assert _widgets(text) == ["Models kept", "Members with models", "Changed in 7 days"]
        assert "<span class='big'>2</span>" in text or "<span class='big'>2<small>" in text
        assert "<span class='big'>2<small>of 4</small></span>" in text
        assert "<input type='search' name='q'" in text and "placeholder='Search card name'" in text
        opts = text.split("<select name='member'", 1)[1].split("</select>", 1)[0]
        assert "<option value='' selected>Every member</option>" in opts
        assert ">bob</option>" in opts and ">a member with no name</option>" in opts
        rows = text.split("<div class='ui-table'", 1)[1].split("<nav class='ui-pager'", 1)[0]
        assert rows.count("class='tr model'") == 2
        assert "added by a member with no name · " in rows
        assert "data-who='a member with no name'" in rows
        assert "user-9" not in re.sub(r"(href|data-\w+)='[^']*'", "", rows)
        assert "href='/admin/models?remove=sol+ring&by=user-9' aria-label='Actions for Sol Ring'" in rows
        assert "Remove" not in rows  # Remove only in the row's menu and its box
        found = await _get(b, "/admin/models?q=opt")
        assert found.count("class='tr model'") == 1 and ">Opt<" in found
        bob = await _get(b, "/admin/models?member=user-2")
        assert bob.count("class='tr model'") == 1 and ">Opt<" in bob
        assert "<option value='user-2' selected>bob</option>" in bob
        none = await _get(b, "/admin/models?q=zzz")
        assert "No card models match. <a href='/admin/models'>Show all</a>" in none
        # a model that is gone can't be removed twice: the page says so instead of a box
        r = await b.http.get("/admin/models?remove=nope&by=user-9", headers=NAVIGATE)
        assert r.status_code == 200 and "id='remove'" not in r.text
    finally:
        await b.aclose()


# -- Settings --------------------------------------------------------------------------------------


async def test_settings_opens_with_the_heading_purpose_and_tab_bar_in_the_wide_column(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        text = await _get(b, "/admin/settings")
        h1 = text.index("<h1>Admin: settings</h1>")
        purpose = text.index("Settings for the whole gateway. A setting your stack sets is shown greyed out")
        nav = text.index("<nav class='adm-nav'")
        groups = text.index("<nav class='ui-chips set-nav'")
        assert h1 < purpose < nav < groups
        assert "<main class='wrap ui-wide'" in text
        assert ".set-page{max-width:52rem}" in text
        assert ".adm-nav{max-width:52rem}" not in text
        assert (
            "Changed here by" not in text
        )  # nothing saved yet: every row reads Default or Set in your stack
    finally:
        await b.aclose()


async def test_settings_saved_and_refused_show_as_tinted_strips_with_who_and_when(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        csrf = await b.csrf("/admin/settings")
        r = await b.http.post(
            "/admin/settings",
            data={"csrf": csrf, "group": "backups", "v_backup_hour_utc": "4"},
            headers=NAVIGATE,
        )
        assert r.status_code == 303
        shown = await _get(b, r.headers["location"].split("#")[0])
        assert "<div class='notice ok' role='status'><p class='b set-saved'>Saved.</p>" in shown
        row = shown.split("id='s-row-backup_hour_utc'", 1)[1].split("id='s-row-", 1)[0]
        assert re.search(r"Changed here by root, <time class='ui-when' datetime='[^']+' data-fmt='rel'", row)
        bad = await b.http.post(
            "/admin/settings",
            data={"csrf": csrf, "group": "backups", "v_backup_hour_utc": "99"},
            headers=NAVIGATE,
        )
        assert bad.status_code == 400
        assert "<div class='notice error' role='alert'>" in bad.text
        assert "btn-warn" not in bad.text
    finally:
        await b.aclose()


# -- Activity --------------------------------------------------------------------------------------


async def test_admin_activity_has_the_page_head_first_and_a_menu_on_every_row(gw: Harness) -> None:
    gw.db.audit(
        "tool_call", sub="user-2", client_id="claude-app", detail={"tool": "search_decks", "ok": True}
    )
    gw.db.audit("approval_refused", sub="user-2", client_id="claude-app", detail={"proposal_id": "p1"})
    b = await admin_browser(gw)
    try:
        text = await _get(b, "/admin/activity")
        assert text.index("<h1>Admin: activity</h1>") < text.index("<nav class='adm-nav'")
        assert text.index("<nav class='adm-nav'") < text.index("<section class='ui-widgets'")
        assert "<div class='wdg wide-sm'>" in text  # Last 14 days spans the phone row
        assert "<script src='/static/admin.js?v=" in text
        rows = text.split("act-table'", 1)[1]
        assert "No member" in rows and "unknown" not in rows.lower().split("</div>", 1)[0]
        kebabs = re.findall(
            r"<a class='kebab' href='([^']+)' aria-label='Actions for this event' aria-haspopup='menu'", rows
        )
        assert len(kebabs) >= 2 and all("open=" in k for k in kebabs)
        assert "<span class='sr-only'>Actions</span>" in text
        # the refused row's label carries the ban sign
        from mtg_gateway.activity_view import BAN_ICON

        refused = f"<span class='ui-lbl red'( title='[^']*')?>{re.escape(BAN_ICON)}Refused</span>"
        assert re.search(refused, rows)
        summaries = "".join(r.split("<div class='more-body'", 1)[0] for r in rows.split("<div class='tr ev"))
        assert "search_decks" not in summaries and "Search public decks" in summaries  # the tool in words
        assert "search_decks" in rows  # its raw name only in the row's details
    finally:
        await b.aclose()


def test_the_admin_script_is_served_and_binds_the_shared_menu() -> None:
    src = (Path(__file__).resolve().parents[1] / "src/mtg_gateway/static/admin.js").read_text()
    assert "window.MtgMenu" in src and "Menu.bind(document" in src
    assert 'button: ".kebab"' in src and "data-needs-tick" in src
    assert "danger: true" in src  # Disable…, Delete data… and Remove… are the red items
    assert ui.PER_PAGE == 25
