"""The admin Settings page (0.7.29): every registered setting, a deployment value locked, a section
saved only when every field passes, risky changes confirmed, and saved values applied at start."""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from mtg_gateway import settings_page
from mtg_gateway.config import MANAGED_SETTINGS, LiveSettings, SettingError, with_stored
from mtg_gateway.db import Database

from .conftest import FakeIdP, Harness, make_settings, running
from .test_admin import ADMIN, NAVIGATE, _with_admin, admin_browser
from .test_decks_and_proxy import Browser


@pytest.fixture
async def gw(tmp_path: Path, idp: FakeIdP):
    # MTG_DECK_SYNC_MINUTES is set by "the deployment" here, so the page must show it locked.
    settings = make_settings(
        tmp_path,
        required_group="mtg-users",
        admin_group="mtg-admins",
        deck_sync_minutes=60,
        env_set=frozenset({"deck_sync_minutes"}),
    )
    async with running(_with_admin(Harness(settings, idp))) as h:
        yield h


def _live(h: Harness) -> LiveSettings:
    return h.app.state.gateway.live


async def _post(b: Browser, data: dict[str, str]):
    csrf = await b.csrf("/admin/settings")
    return await b.http.post("/admin/settings", data={"csrf": csrf, **data})


def _words(page: str) -> str:
    """The page's text without tags, as a person reads it."""
    return html.unescape(re.sub(r"<[^>]+>", "", page))


async def test_only_admins_see_the_settings_page(gw: Harness) -> None:
    gw.idp.user = {**ADMIN, "sub": "user-9", "groups": ["mtg-users"]}
    member = Browser(gw)
    await member.login("/account")
    try:
        assert (await member.http.get("/admin/settings", headers=NAVIGATE)).status_code == 404
        r = await member.http.post(
            "/admin/settings", data={"group": "backups", "v_archidekt_backup_copies": "2"}
        )
        assert r.status_code == 404
        assert _live(gw).get("archidekt_backup_copies") == 10
    finally:
        await member.aclose()


async def test_the_page_lists_every_setting_and_locks_the_deployments(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        r = await b.http.get("/admin/settings", headers=NAVIGATE)
        assert r.status_code == 200 and "<title>Admin: settings · " in r.text
        assert "href='/admin/settings'" in r.text
        page = html.unescape(r.text)
        for key in MANAGED_SETTINGS:
            assert f"id='s-row-{key}'" in r.text, key
            assert settings_page.label(key) in page, key
        sync = r.text.split("id='s-row-deck_sync_minutes'", 1)[1].split("id='s-row-", 1)[0]
        assert "Set in your stack" in sync and "<code>MTG_<wbr>DECK_<wbr>SYNC_<wbr>MINUTES</code>" in sync
        assert "disabled" in sync and "value='60'" in sync and "Back to default" not in sync
        # a setting with no env variable is never locked
        forge = r.text.split("id='s-row-forge_games'", 1)[1].split("id='s-row-", 1)[0]
        assert "Set in your stack" not in forge and "disabled" not in forge
        # the scan thresholds start folded away; the others are open
        assert (
            "<details><summary><span class='sum'><span class='h2'>Card scanning (advanced)</span>" in r.text
        )
        assert "Allowed: 0 (off), or 30 to 1,440" in page
    finally:
        await b.aclose()


async def test_the_stack_box_is_gone_and_each_locked_row_and_group_says_so(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No stack box. Each locked row has a grey "Set in your stack"
    label beside its name (its lock line keeps the variable), each group heading counts its locked
    rows, and the chip "Set in your stack {n}" (a link to ?locked=1 without scripts) shows only
    them, opening the folded group that holds one. No chip when the stack sets nothing."""
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
    async with running(_with_admin(Harness(settings, idp))) as h:
        b = await admin_browser(h)
        try:
            r = await b.http.get("/admin/settings", headers=NAVIGATE)
            assert r.status_code == 200 and "set-stack" not in r.text and "lockchip" not in r.text
            assert "set in your stack</span>" not in r.text
        finally:
            await b.aclose()
    locked = {"forge_games": 12, "scan_ambiguity_margin": MANAGED_SETTINGS["scan_ambiguity_margin"].default}
    settings = make_settings(
        tmp_path, required_group="mtg-users", admin_group="mtg-admins", env_set=frozenset(locked), **locked
    )
    async with running(_with_admin(Harness(settings, idp))) as h:
        b = await admin_browser(h)
        try:
            r = await b.http.get("/admin/settings", headers=NAVIGATE)
            assert "Your stack sets" not in r.text and "set-stack" not in r.text
            chips = r.text.split("<nav class='ui-chips set-nav' aria-label='Setting groups'>", 1)[1].split(
                "</nav>"
            )[0]
            assert _words(chips).endswith("Set in your stack 2")
            assert "href='?locked=1' role='button' aria-pressed='false'" in chips
            # the notice slot stays above the chips, under the intro line
            assert r.text.index("Settings for the whole gateway.") < r.text.index("class='ui-chips set-nav'")
            forge = r.text.split("id='s-row-forge_games'", 1)[1].split("id='s-row-", 1)[0]
            assert "<span class='ui-lbl grey set-badge'>" in forge and "Set in your stack</span>" in forge
            sim = r.text.split("id='g-simulation'", 1)[1].split("</section>", 1)[0]
            assert "1 set in your stack</span>" in sim.split("</div>", 1)[0]
            assert (
                "set in your stack</span>"
                not in r.text.split("id='g-approvals'", 1)[1].split("</section>")[0]
            )
            scan = r.text.split("id='g-scan'", 1)[1].split("</summary>", 1)[0]
            assert "<span class='ui-sub'>15 settings</span>" in scan and "1 set in your stack" in scan
            # a folded group of one setting reads "1 setting"
            keys_of = settings_page.group_keys
            monkeypatch.setattr(
                settings_page, "group_keys", lambda g: keys_of(g)[:1] if g == "scan" else keys_of(g)
            )
            one = (await b.http.get("/admin/settings", headers=NAVIGATE)).text
            assert "<span class='ui-sub'>1 setting</span>" in one.split("id='g-scan'", 1)[1]
            monkeypatch.setattr(settings_page, "group_keys", keys_of)
            assert "class='set-page'" in r.text and "only-locked" not in r.text.split("<main", 1)[1]
            # without scripts: the link shows the same, the folded group open, the chip pressed
            r = await b.http.get("/admin/settings?locked=1", headers=NAVIGATE)
            assert "class='set-page only-locked'" in r.text
            assert "<details open><summary><span class='sum'><span class='h2'>Card scanning" in r.text
            assert "href='/admin/settings' role='button' aria-pressed='true'" in r.text
            assert "class='ui-card set-group has-locked' id='g-simulation'" in r.text
            assert "class='ui-card set-group' id='g-approvals'" in r.text
            # the scan group starts folded, and opens for a link to one of its rows
            r = await b.http.get("/admin/settings?open=scan", headers=NAVIGATE)
            assert "<details open><summary><span class='sum'><span class='h2'>Card scanning" in r.text
            r = await b.http.get("/admin/settings?open=general", headers=NAVIGATE)
            assert r.status_code == 200 and "<details open>" not in r.text
        finally:
            await b.aclose()


async def test_confirm_ticks_carry_what_the_script_needs_and_show_after_a_refused_save(gw: Harness) -> None:
    """Each of the three ticks (CONFIRM) is in an orange box the
    script shows only once the value goes down or off (data-confirm, data-from); without scripts
    it shows as before. After a refused save the box says what was lowered, and saving still
    needs the tick."""
    b = await admin_browser(gw)
    try:
        r = await b.http.get("/admin/settings", headers=NAVIGATE)
        assert "/static/settings.js" in r.text
        for key, rule, start in (
            ("archidekt_backups", "off", "on"),
            ("archidekt_backup_copies", "lower", "10"),
            ("snapshots_per_deck", "lower", "25"),
        ):
            row = r.text.split(f"id='s-row-{key}'", 1)[1].split("id='s-row-", 1)[0]
            assert f"data-confirm='{rule}' data-from='{start}'" in row, key
            assert f"name='confirm_{key}'" in row and "<p class='lead' id='c-" in row and " hidden>" in row, (
                key
            )
        assert r.text.count("class='set-confirm'") == 3
        r = await _post(b, {"group": "backups", "v_archidekt_backup_copies": "5"})
        assert r.status_code == 400
        row = r.text.split("id='s-row-archidekt_backup_copies'", 1)[1].split("id='s-row-", 1)[0]
        assert "data-shown>" in row
        assert (
            _words(row).count("You lowered this from 10 to 5. Tick the box below to confirm lowering this.")
            == 1
        )
        assert _live(gw).get("archidekt_backup_copies") == 10
        r = await _post(b, {"group": "backups", "v_archidekt_backups": "off"})
        row = r.text.split("id='s-row-archidekt_backups'", 1)[1].split("id='s-row-", 1)[0]
        assert "You turned this off. Tick the box below to confirm turning this off." in _words(row)
    finally:
        await b.aclose()


async def test_a_number_keeps_its_unit_inside_the_box(gw: Harness) -> None:
    """K-04: the unit is inside the box (every control the same width), read with the box."""
    b = await admin_browser(gw)
    try:
        r = await b.http.get("/admin/settings", headers=NAVIGATE)
        row = r.text.split("id='s-row-proposal_expiry_days'", 1)[1].split("id='s-row-", 1)[0]
        assert "<span class='set-num has-u'><input type='number' id='f-proposal_expiry_days'" in row
        assert "u-proposal_expiry_days r-proposal_expiry_days'" in row
        assert "<span class='unit' id='u-proposal_expiry_days'>days</span></span>" in row
        assert "class='set-num'><input" in r.text.split("id='s-row-auto_apply_max_rows'", 1)[1][:400]
        assert "<span class='ui-hint unsaved' aria-live='polite'></span>" in r.text
    finally:
        await b.aclose()


async def test_saving_a_section_stores_and_reports_each_change(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        r = await _post(
            b,
            {
                "group": "backups",
                "v_archidekt_backups": "on",
                "v_archidekt_backup_copies": "7",
                "confirm_archidekt_backup_copies": "yes",
                "v_archidekt_backup_box_default": "off",
                "v_snapshots_per_deck": "25",
                "v_backup_hour_utc": "4",
                "v_backup_keep_days": "14",
            },
        )
        assert r.status_code == 303
        assert re.fullmatch(r"/admin/settings\?saved=backups&after=\d+#g-backups", r.headers["location"])
        live = _live(gw)
        assert (
            live.get("archidekt_backup_copies") == 7 and live.source("archidekt_backup_copies") == "dashboard"
        )
        assert live.get("backup_hour_utc") == 4
        assert live.source("snapshots_per_deck") == "default"  # unchanged fields aren't stored
        shown = await b.http.get(r.headers["location"].split("#")[0], headers=NAVIGATE)
        text = html.unescape(shown.text)
        assert "Saved." in text and "Backup copies kept per deck: 10 → 7" in _words(shown.text)
        assert "Nightly backup hour (UTC): 3 → 4 (after a restart)" in _words(shown.text)
        row = text.split("id='s-row-backup_hour_utc'", 1)[1].split("id='s-row-", 1)[0]
        assert "Changed here by root" in row and "The gateway uses 3 until it restarts." in row
        audit = [a for a in gw.db.audit_recent(20, sub="admin-1") if a["event"] == "setting_changed"]
        assert {a["detail"]["key"] for a in audit} == {"archidekt_backup_copies", "backup_hour_utc"}
    finally:
        await b.aclose()


async def test_one_refused_field_saves_nothing_in_its_section(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        r = await _post(
            b,
            {"group": "backups", "v_archidekt_backup_copies": "99", "v_snapshots_per_deck": "30"},
        )
        assert r.status_code == 400
        text = html.unescape(r.text)
        assert "Nothing was saved. See the message under “Backup copies kept per deck”." in text
        assert "Must be between 1 and 50." in text
        assert "value='99'" in r.text  # what was typed stays in the field
        assert "id='e-archidekt_backup_copies' role='alert'" in r.text
        assert _live(gw).get("snapshots_per_deck") == 25 and _live(gw).get("archidekt_backup_copies") == 10
    finally:
        await b.aclose()


async def test_turning_backup_copies_off_needs_the_tick(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        r = await _post(b, {"group": "backups", "v_archidekt_backups": "off"})
        assert r.status_code == 400 and "Tick the box below to confirm turning this off." in r.text
        assert _live(gw).get("archidekt_backups") is True
        r = await _post(
            b, {"group": "backups", "v_archidekt_backups": "off", "confirm_archidekt_backups": "yes"}
        )
        assert r.status_code == 303 and _live(gw).get("archidekt_backups") is False
        # Turning it back on needs no tick.
        r = await _post(b, {"group": "backups", "v_archidekt_backups": "on"})
        assert r.status_code == 303 and _live(gw).get("archidekt_backups") is True
    finally:
        await b.aclose()


async def test_lowering_copies_or_restore_points_needs_the_tick(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        r = await _post(b, {"group": "backups", "v_snapshots_per_deck": "10"})
        assert r.status_code == 400 and "Tick the box below to confirm lowering this." in r.text
        r = await _post(b, {"group": "backups", "v_archidekt_backup_copies": "4"})
        assert r.status_code == 400 and _live(gw).get("archidekt_backup_copies") == 10
        # Raising needs no tick.
        r = await _post(b, {"group": "backups", "v_snapshots_per_deck": "40"})
        assert r.status_code == 303 and _live(gw).get("snapshots_per_deck") == 40
        # Back to default (25) lowers it too.
        r = await _post(b, {"reset": "snapshots_per_deck"})
        assert r.status_code == 400 and _live(gw).get("snapshots_per_deck") == 40
        r = await _post(
            b, {"group": "backups", "v_snapshots_per_deck": "10", "confirm_snapshots_per_deck": "yes"}
        )
        assert r.status_code == 303 and _live(gw).get("snapshots_per_deck") == 10
    finally:
        await b.aclose()


async def test_back_to_default_and_a_save_with_no_change(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        await _post(b, {"group": "approvals", "v_proposal_expiry_days": "12"})
        assert _live(gw).get("proposal_expiry_days") == 12
        page = (await b.http.get("/admin/settings")).text
        assert "name='reset' value='proposal_expiry_days'" in page and "Back to default (7 days)" in page
        r = await _post(b, {"reset": "proposal_expiry_days"})
        assert r.status_code == 303 and r.headers["location"].endswith("#s-row-proposal_expiry_days")
        assert _live(gw).source("proposal_expiry_days") == "default"
        text = _words((await b.http.get(r.headers["location"].split("#")[0])).text)
        assert "Days a change waits for approval: 12 days → 7 days" in text
        r = await _post(b, {"group": "approvals", "v_proposal_expiry_days": "7"})
        nothing = html.unescape((await b.http.get(r.headers["location"].split("#")[0])).text)
        assert "Nothing to save: no setting was changed." in nothing
    finally:
        await b.aclose()


async def test_a_locked_or_unknown_setting_cannot_be_changed(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        r = await _post(b, {"group": "sync", "v_deck_sync_minutes": "90"})
        assert r.status_code == 303 and _live(gw).get("deck_sync_minutes") == 60
        r = await _post(b, {"group": "simulation", "v_forge_parallel_games": "2"})
        assert r.status_code == 303 and _live(gw).get("forge_parallel_games") == 2
        r = await _post(b, {"reset": "deck_sync_minutes"})
        assert r.status_code == 400
        assert (
            "Set in your stack (MTG_DECK_SYNC_MINUTES), so it can't be changed here. Change or remove it "
            "in your stack, then redeploy." in html.unescape(r.text)
        )
        r = await _post(b, {"reset": "writes_enabled"})
        assert r.status_code == 400 and "isn't one this page can change" in html.unescape(r.text)
        r = await _post(b, {"group": "general", "v_writes_enabled": "off"})
        assert r.status_code == 303 and gw.app.state.gateway.settings.writes_enabled is True
    finally:
        await b.aclose()


async def test_the_form_needs_its_csrf_token(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        r = await b.http.post("/admin/settings", data={"group": "backups", "v_archidekt_backup_copies": "3"})
        assert r.status_code == 403 and "This form expired" in r.text
        assert _live(gw).get("archidekt_backup_copies") == 10
    finally:
        await b.aclose()


def test_saved_values_apply_at_the_next_start(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    db.save_setting("backup_hour_utc", 5, by="admin-1")
    db.save_setting("deck_sync_minutes", 90, by="admin-1")
    db.save_setting("archidekt_backup_folder", "Old decks", by="admin-1")
    db.save_setting("archidekt_backup_copies", 500, by="admin-1")  # out of bounds: skipped
    db.save_setting("chatgpt_name", "X", by="admin-1")  # a row that no longer exists: skipped
    s = make_settings(tmp_path, deck_sync_minutes=60, env_set=frozenset({"deck_sync_minutes"}))
    started = with_stored(s, db)
    assert started.backup_hour_utc == 5
    assert started.deck_sync_minutes == 60  # the deployment wins
    assert started.archidekt_backup_folder == "Old decks"
    assert started.archidekt_backup_copies == 10
    assert with_stored(make_settings(tmp_path), Database(tmp_path / "empty.sqlite")).backup_hour_utc == 3
    db.close()


def test_one_second_and_one_day_are_singular() -> None:
    """Singular units: "1 second", "1 day" in Back to default, the save notice and the log."""
    shown, specs = settings_page.shown, MANAGED_SETTINGS
    assert shown(specs["archidekt_min_interval"], 1.0) == "1 second"
    assert shown(specs["archidekt_min_interval"], 2) == "2 seconds"
    assert shown(specs["archidekt_backoff_base"], 1.5) == "1.5 seconds"
    assert shown(specs["proposal_expiry_days"], 1) == "1 day"
    assert shown(specs["deck_sync_minutes"], 1440) == "1,440 minutes"
    line = settings_page.change_line({"key": "backup_keep_days", "previous": 14, "value": 1})
    assert line == "Days of nightly backups kept: 14 days → 1 day"


def test_every_setting_has_page_text() -> None:
    assert set(settings_page.TEXT) == set(MANAGED_SETTINGS)
    groups = {g for g, _t, _i in settings_page.GROUPS}
    assert {spec.group for spec in MANAGED_SETTINGS.values()} <= groups


def test_the_two_forge_game_rows_say_how_they_differ() -> None:
    """The total games of a report and how many of them play side by side are easy to mix
    up, so each row's help says what it is and names the other row."""
    total, total_help = settings_page.TEXT["forge_games"]
    at_once, at_once_help = settings_page.TEXT["forge_parallel_games"]
    assert "in all" in total_help and f"\u201c{at_once}\u201d" in total_help
    assert "side by side" in at_once_help and f"\u201c{total}\u201d" in at_once_help
    assert "doesn't change how many games a report plays" in at_once_help


async def test_member_cannot_reach_it_signed_out(gw: Harness) -> None:
    b = Browser(gw)
    try:
        r = await b.http.get("/admin/settings", headers=NAVIGATE)
        assert r.status_code in (302, 303) and "/login" in r.headers["location"]
    finally:
        await b.aclose()


async def test_the_activity_logs_say_a_setting_change_in_words(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        await _post(b, {"group": "approvals", "v_proposal_expiry_days": "14"})
        for path in ("/admin/activity", "/activity"):
            page = (await b.http.get(path, headers=NAVIGATE)).text
            text = _words(page)
            assert "Days a change waits for approval: 7 days → 14 days" in text, path
            # the stored JSON only in the admin's row details, never in the Detail column
            cells = "".join(re.findall(r"<span class='c-det'>(.*?)</span><span class='c-app'>", page))
            assert "Days a change waits" in _words(cells) and '"previous"' not in _words(cells), path
            if path == "/activity":
                assert '"previous"' not in text
        assert "Gateway setting changed" in _words((await b.http.get("/activity", headers=NAVIGATE)).text)
    finally:
        await b.aclose()


async def test_back_to_default_keeps_the_sections_other_typed_values(gw: Harness) -> None:
    """The other fields of the section are neither saved nor dropped."""
    b = await admin_browser(gw)
    try:
        await _post(b, {"group": "backups", "v_snapshots_per_deck": "40"})
        typed = {"v_backup_keep_days": "20", "v_snapshots_per_deck": "40", "v_backup_hour_utc": "3"}
        # refused for its missing tick: nothing saved, the typed value still in its box
        r = await _post(b, {"reset": "snapshots_per_deck", **typed})
        assert r.status_code == 400 and "value='20'" in r.text.split("id='s-row-backup_keep_days'")[1]
        assert _live(gw).get("backup_keep_days") == 14
        # done: the reset is saved, the other value is kept in the form and named
        r = await _post(b, {"reset": "snapshots_per_deck", "confirm_snapshots_per_deck": "yes", **typed})
        assert r.status_code == 200 and _live(gw).get("snapshots_per_deck") == 25
        assert _live(gw).get("backup_keep_days") == 14
        text = _words(r.text)
        assert "Restore points kept per deck: 40 → 25" in text
        assert "Not saved yet: “Days of nightly backups kept”." in text
        assert "value='20'" in r.text.split("id='s-row-backup_keep_days'")[1]
        # with nothing else changed it redirects as before
        r = await _post(b, {"reset": "snapshots_per_deck", "v_backup_keep_days": "14"})
        assert r.status_code == 303
    finally:
        await b.aclose()


async def test_a_stack_value_holds_for_every_kind_of_setting(tmp_path: Path, idp: FakeIdP) -> None:
    """A value the stack sets is set in stone until the stack changes. No form post,
    Back to default, stored value or restart changes it; every other write path goes through
    LiveSettings.set and reset, which refuse it."""
    locked = {
        "archidekt_backups": False,
        "approval_mode_max": "semi",
        "server_name": "Our Table",
        "log_level": "WARNING",
        "deck_sync_minutes": 60,
    }
    settings = make_settings(
        tmp_path, required_group="mtg-users", admin_group="mtg-admins", env_set=frozenset(locked), **locked
    )
    # a value stored on the page before the stack set it is ignored, at start and after
    db = Database(settings.db_path)
    db.save_setting("deck_sync_minutes", 30, by="admin-1")
    db.close()
    async with running(_with_admin(Harness(settings, idp))) as h:
        live = _live(h)
        b = await admin_browser(h)
        try:
            tries = {"archidekt_backups": "on", "approval_mode_max": "auto", "server_name": "Other"}
            tries |= {"log_level": "DEBUG", "deck_sync_minutes": "120"}
            for key, raw in tries.items():
                r = await _post(b, {"group": MANAGED_SETTINGS[key].group, f"v_{key}": raw})
                assert r.status_code == 303, key  # the locked field is ignored, nothing else sent
            for key in locked:
                r = await _post(b, {"reset": key})
                assert r.status_code == 400 and "Set in your stack" in r.text, key
                with pytest.raises(SettingError):
                    live.set(key, MANAGED_SETTINGS[key].default, by="admin-1")
                with pytest.raises(SettingError):
                    live.reset(key, by="admin-1")
                assert live.get(key) == locked[key] and live.source(key) == "deployment", key
            assert h.app.state.gateway.settings.deck_sync_minutes == 60
        finally:
            await b.aclose()
    db = Database(settings.db_path)
    try:
        assert with_stored(settings, db).deck_sync_minutes == 60
        # The page's value saved before the lock is kept, and applies again once
        # the stack no longer sets it
        assert db.stored_settings()["deck_sync_minutes"] == 30
        unlocked = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
        assert with_stored(unlocked, db).deck_sync_minutes == 30
    finally:
        db.close()
